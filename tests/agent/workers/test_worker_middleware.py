"""M70 T7：三个 Worker 中间件（spec §4.5.5、§4.5.6、§4.5.11、§6）。

异步用例沿用 M69 的写法：同步测试函数 + ``asyncio.run(scenario())``；临时库用
``tests/fakes/v3_db.make_session_factory``。链顺序用例钉住 05 §5.2 的 14/16/18
位置，并确认总控链里没有这三个。
"""

from __future__ import annotations

import asyncio
import json
from types import SimpleNamespace

from sqlalchemy import func, select

from maa_api.agent.confirmation import ConfirmationService
from maa_api.agent.harness.context import (
    ModelResponse,
    RunContext,
    RunOutcome,
    ToolCall,
    ToolOutcome,
    Usage,
)
from maa_api.agent.harness.loop import AgentLoop, MemoryTranscript
from maa_api.agent.harness.middleware import Middleware, MiddlewareChain
from maa_api.agent.policy import PolicyDecision
from maa_api.agent.registry import ToolContext
from maa_api.agent.runtime import AgentRuntime
from maa_api.agent.specs.registry import builtin_spec
from maa_api.agent.tickets.inbox import InboxService
from maa_api.agent.tickets.service import TicketService
from maa_api.agent.tickets.threads import ThreadRouter
from maa_api.agent.tools import build_registry
from maa_api.agent.workers.middleware import (
    MISSING_MANDATE_MESSAGE,
    REPORT_NOT_LAST_MESSAGE,
    MandateMiddleware,
    ReportMiddleware,
    ensure_report,
)
from maa_api.db.models import (
    AgentAudit,
    AgentInbox,
    Confirmation,
    Ticket,
    TicketReport,
    new_uuid,
)
from maa_api.domain.enums import CallerType, RiskLevel, TicketStatus
from tests.fakes.v3_db import make_session_factory, seed_run, seed_session


def _context(
    name: str = "general",
    *,
    ticket_id: str | None = "ticket-1",
    state: dict | None = None,
    app: object = None,
) -> RunContext:
    return RunContext(
        run_id="run-t7",
        root_run_id="run-t7",
        parent_run_id=None,
        session_id="session-1",
        ticket_id=ticket_id,
        spec=builtin_spec(name),
        app=app,
        state=dict(state or {}),
    )


def _call(
    call_id: str, name: str, arguments: dict | None = None
) -> ToolCall:
    return ToolCall(
        id=call_id,
        name=name,
        raw_arguments=json.dumps(arguments or {}, ensure_ascii=False),
        arguments=arguments or {},
    )


def _response(tool_calls: list[ToolCall]) -> ModelResponse:
    return ModelResponse(
        text="",
        reasoning="",
        usage=Usage(),
        upstream_finish_reason="tool_calls",
        latency_ms=1,
        tool_calls=list(tool_calls),
    )


async def _counts(factory) -> tuple[int, int]:
    async with factory() as db:
        audits = await db.scalar(select(func.count()).select_from(AgentAudit))
        confirmations = await db.scalar(select(func.count()).select_from(Confirmation))
    return int(audits or 0), int(confirmations or 0)


def test_mandate_blocks_consumption_under_none_ceiling(tmp_path) -> None:
    engine, factory = asyncio.run(make_session_factory(tmp_path / "mandate.db"))
    try:
        registry = build_registry()
        confirmations = ConfirmationService(factory, registry)
        middleware = MandateMiddleware(
            registry=registry,
            confirmation_service=confirmations,
            session_factory=factory,
        )
        ctx = _context(state={"mandate.ceiling": "none"})
        invoked: list[str] = []

        async def inner(call: ToolCall) -> ToolOutcome:
            invoked.append(call.name)
            return ToolOutcome(status="ok", result={})

        call = _call(
            "call-1", "submit_pipeline", {"tasks": [{"name": "Fight", "medicine": 1}]}
        )
        outcome = asyncio.run(middleware.wrap_tool(ctx, call, inner))

        assert outcome.status == "error"
        assert outcome.error["code"] == "MANDATE_SCOPE_EXCEEDED"
        assert "超出工单的风险上限" in outcome.error["message"]
        assert "消耗类确认" in outcome.error["message"]
        assert outcome.error["details"] == {
            "tool": "submit_pipeline",
            "risk": "consume",
            "ceiling": "none",
            "ticket_id": "ticket-1",
        }
        # 被拦下的调用不进确认服务：没有确认行，也没有审计行。
        assert invoked == []
        assert asyncio.run(_counts(factory)) == (0, 0)
    finally:
        engine.sync_engine.dispose()


def test_mandate_blocks_atomic_grant_under_none_ceiling(tmp_path) -> None:
    engine, factory = asyncio.run(make_session_factory(tmp_path / "atomic.db"))
    try:

        async def scenario() -> None:
            session_id = await seed_session(factory, kind="worker")
            registry = build_registry()
            confirmations = ConfirmationService(factory, registry)
            middleware = MandateMiddleware(
                registry=registry,
                confirmation_service=confirmations,
                session_factory=factory,
            )
            ctx = _context(state={"mandate.ceiling": "none"})
            ctx.session_id = session_id
            invoked: list[str] = []

            async def inner(call: ToolCall) -> ToolOutcome:
                invoked.append(call.name)
                return ToolOutcome(status="ok", result={})

            outcome = await middleware.wrap_tool(
                ctx, _call("click-1", "click", {"x": 10, "y": 20}), inner
            )
            # M71 §0 #10：原子授权的风险虽记 none，但要按 consume 参与上限比较；
            # 上限 none 的工单不能借「弹出授权」绕过上限。
            assert outcome.status == "error"
            assert outcome.error["code"] == "MANDATE_SCOPE_EXCEEDED"
            assert "消耗类确认" in outcome.error["message"]
            assert outcome.error["details"]["risk"] == "consume"
            assert invoked == []
            assert await _counts(factory) == (0, 0)

            definition = registry.get("click")
            params = registry.validate("click", {"x": 10, "y": 20})
            async with factory() as db:
                decision = await confirmations.policy.evaluate(
                    definition,
                    params,
                    ToolContext(
                        CallerType.INTERNAL,
                        session_id,
                        "run:run-t7",
                        None,
                        db,
                    ),
                )
            # 策略层本身仍给出 none + grant_atomic_ops；差别在上限比较的口径。
            assert decision.requires_confirmation is True
            assert decision.risk == RiskLevel.NONE
            assert decision.confirmation_action == "grant_atomic_ops"

        asyncio.run(scenario())
    finally:
        engine.sync_engine.dispose()


class _RecordingPolicy:
    def __init__(self) -> None:
        self.calls: list[str] = []

    async def evaluate(
        self, definition: object, params: object, context: object
    ) -> PolicyDecision:
        self.calls.append(definition.name)  # type: ignore[attr-defined]
        return PolicyDecision(True, RiskLevel.DESTRUCTIVE)


def test_mandate_skips_policy_under_destructive_and_fails_closed_without_ceiling() -> None:
    policy = _RecordingPolicy()
    middleware = MandateMiddleware(
        registry=build_registry(),
        confirmation_service=SimpleNamespace(policy=policy),
        session_factory=None,
    )

    async def scenario() -> None:
        invoked: list[str] = []

        async def inner(call: ToolCall) -> ToolOutcome:
            invoked.append(call.name)
            return ToolOutcome(status="ok", result={})

        # 上限是 destructive：不调策略，直接放行。
        destructive = _context(state={"mandate.ceiling": "destructive"})
        outcome = await middleware.wrap_tool(
            destructive,
            _call(
                "call-1",
                "submit_pipeline",
                {"tasks": [{"name": "Fight", "medicine": 1}]},
            ),
            inner,
        )
        assert outcome.status == "ok"
        assert invoked == ["submit_pipeline"]
        assert policy.calls == []

        # 没有委托上限：fail closed，也不调策略。
        missing = _context(state={})
        blocked = await middleware.wrap_tool(
            missing, _call("call-2", "get_screenshot", {}), inner
        )
        assert blocked.error["code"] == "MANDATE_SCOPE_EXCEEDED"
        assert blocked.error["message"] == MISSING_MANDATE_MESSAGE
        assert invoked == ["submit_pipeline"]
        assert policy.calls == []

    asyncio.run(scenario())


def test_report_not_last_in_batch_is_rejected_and_run_continues() -> None:
    async def scenario() -> None:
        middleware = ReportMiddleware()
        ctx = _context()
        await middleware.on_run_start(ctx)
        report = _call(
            "report-1", "report", {"status": "progress", "summary": "做完了"}
        )
        click = _call("click-1", "click", {"x": 1, "y": 2})
        await middleware.after_model(ctx, _response([report, click]))
        assert ctx.state["report.invalid_ids"] == {"report-1"}

        invoked: list[str] = []

        async def inner(call: ToolCall) -> ToolOutcome:
            invoked.append(call.name)
            return ToolOutcome(status="ok", result={"ok": True})

        rejected = await middleware.wrap_tool(ctx, report, inner)
        assert rejected.status == "error"
        assert rejected.error["code"] == "INVALID_PARAMETER"
        assert rejected.error["message"] == REPORT_NOT_LAST_MESSAGE
        assert rejected.stop_run is False
        assert ctx.pending_stop is None
        # 同一批里其余调用照常执行，本轮不结束。
        passed = await middleware.wrap_tool(ctx, click, inner)
        assert passed.status == "ok"
        assert invoked == ["click"]

        # 一批两个 report：两个都不合法。
        batch = _context()
        await middleware.on_run_start(batch)
        first = _call("report-2", "report", {"status": "progress", "summary": "a"})
        second = _call("report-3", "report", {"status": "progress", "summary": "b"})
        await middleware.after_model(batch, _response([first, second]))
        assert batch.state["report.invalid_ids"] == {"report-2", "report-3"}

    asyncio.run(scenario())


class _ReportExecutor(Middleware):
    """测试里的执行器：``report`` 一律成功，不碰真实的 WorkerService。"""

    name = "executor"

    async def wrap_tool(
        self, ctx: RunContext, call: ToolCall, call_next
    ) -> ToolOutcome:
        return ToolOutcome(status="ok", result={"report_id": 7})


def test_report_stops_the_run_with_report_submitted() -> None:
    async def scenario() -> None:
        middleware = ReportMiddleware()
        registry = build_registry()
        raw_tool_calls = [
            {
                "id": "call-1",
                "type": "function",
                "function": {
                    "name": "report",
                    "arguments": json.dumps(
                        {"status": "progress", "summary": "流水线已提交"},
                        ensure_ascii=False,
                    ),
                },
            }
        ]

        async def call_model(ctx: RunContext, request: object) -> ModelResponse:
            return ModelResponse(
                text="",
                reasoning="",
                usage=Usage(),
                upstream_finish_reason="tool_calls",
                latency_ms=1,
                raw_tool_calls=raw_tool_calls,
            )

        loop = AgentLoop(
            chain=MiddlewareChain([middleware, _ReportExecutor()]),
            transcript=MemoryTranscript([{"role": "user", "content": "开始"}]),
            call_model=call_model,
            tool_schemas=lambda ctx: [],
            registry=registry,
        )
        ctx = _context()
        outcome = await loop.run(ctx)

        assert outcome.finish_reason == "report_submitted"
        assert ctx.state["report.submitted"] == {"report_id": 7}

    asyncio.run(scenario())


async def _seed_ticket(factory, orchestrator_id: str) -> tuple[str, str]:
    """建工单、它的 Worker 会话与一条运行；返回 ``(ticket_id, run_id)``。"""
    ticket_id = new_uuid()
    async with factory() as db:
        db.add(
            Ticket(
                id=ticket_id,
                title="补汇报测试工单",
                kind="general",
                status=TicketStatus.OPEN.value,
                origin="user",
                orchestrator_session_id=orchestrator_id,
            )
        )
        await db.commit()
    worker_session_id = await seed_session(factory, kind="worker", ticket_id=ticket_id)
    run_id = await seed_run(
        factory, session_id=worker_session_id, ticket_id=ticket_id
    )
    return ticket_id, run_id


async def _reports(factory, ticket_id: str) -> list[TicketReport]:
    async with factory() as db:
        result = await db.execute(
            select(TicketReport)
            .where(TicketReport.ticket_id == ticket_id)
            .order_by(TicketReport.id.asc())
        )
        return list(result.scalars().all())


async def _inbox(factory, session_id: str) -> list[AgentInbox]:
    async with factory() as db:
        result = await db.execute(
            select(AgentInbox)
            .where(AgentInbox.session_id == session_id)
            .order_by(AgentInbox.id.asc())
        )
        return list(result.scalars().all())


def test_missing_report_adds_progress_and_failed_run_adds_blocked(tmp_path) -> None:
    engine, factory = asyncio.run(make_session_factory(tmp_path / "report.db"))
    try:

        async def scenario() -> None:
            orchestrator_id = await seed_session(factory, kind="orchestrator")
            inbox = InboxService(factory)
            service = TicketService(
                factory,
                inbox_service=inbox,
                thread_router=ThreadRouter(factory, inbox),
                spec_registry=SimpleNamespace(
                    get=lambda name: SimpleNamespace(
                        kind="worker", risk_ceiling="consume"
                    )
                ),
            )
            app = SimpleNamespace(
                state=SimpleNamespace(ticket_service=service)
            )

            # 正常结束但没有 report：补一条 progress，不转发给总控。
            ticket_id, run_id = await _seed_ticket(factory, orchestrator_id)
            ctx = _context(app=app, ticket_id=ticket_id)
            ctx.run_id = run_id
            await ensure_report(
                ctx, RunOutcome("succeeded", "stop", "流水线已提交")
            )
            stored = await _reports(factory, ticket_id)
            assert [(row.status, row.summary) for row in stored] == [
                ("progress", "流水线已提交")
            ]
            assert stored[0].details == {"auto": True, "finish_reason": "stop"}
            assert await _inbox(factory, orchestrator_id) == []
            # 幂等：重复调用不再写一条。
            await ensure_report(
                ctx, RunOutcome("succeeded", "stop", "流水线已提交")
            )
            assert len(await _reports(factory, ticket_id)) == 1

            # 失败的一轮：补 blocked，并转发给总控。
            failed_ticket, failed_run_id = await _seed_ticket(
                factory, orchestrator_id
            )
            failed_ctx = _context(app=app, ticket_id=failed_ticket)
            failed_ctx.run_id = failed_run_id
            await ensure_report(
                failed_ctx, RunOutcome("failed", "stuck_loop")
            )
            blocked = await _reports(factory, failed_ticket)
            assert len(blocked) == 1
            assert blocked[0].status == "blocked"
            assert (
                blocked[0].summary
                == "这一轮运行没有完成：连续重复同样的调用，已停下。"
            )
            assert blocked[0].human_action == {
                "type": "decision",
                "prompt": "要让 Worker 再试一次，还是取消这个工单？",
                "ref_id": None,
            }
            forwarded = await _inbox(factory, orchestrator_id)
            assert len(forwarded) == 1
            assert str(forwarded[0].kind) == "report"
            assert forwarded[0].payload["status"] == "blocked"
            assert forwarded[0].payload["summary"] == blocked[0].summary

            # 工单已经结束：再调也不写。
            ended_ctx = _context(app=app, ticket_id=failed_ticket)
            await ensure_report(ended_ctx, RunOutcome("failed", "error"))
            assert len(await _reports(factory, failed_ticket)) == 1

            # 取消的一轮不补汇报。
            cancelled_ticket, cancelled_run_id = await _seed_ticket(
                factory, orchestrator_id
            )
            cancelled_ctx = _context(app=app, ticket_id=cancelled_ticket)
            cancelled_ctx.run_id = cancelled_run_id
            await ensure_report(
                cancelled_ctx,
                RunOutcome("cancelled", "cancelled"),
            )
            assert await _reports(factory, cancelled_ticket) == []

        asyncio.run(scenario())
    finally:
        engine.sync_engine.dispose()


def test_v3_chain_puts_worker_middlewares_at_14_16_18_and_not_in_orchestrator() -> None:
    confirmations = ConfirmationService(None, build_registry())
    runtime = AgentRuntime(None, confirmations)

    assert runtime.build_chain("worker").names == (
        "trace",
        "context_layout",
        "durable_context",
        "compaction",
        "tool_result_clearing",
        # M75：外置紧接清理之后（G3 的 plan 会插在两者之间）。
        "spill",
        "loop_guard",
        "mandate",
        "lease",
        "clarification",
        "report",
        "policy",
    )
    # 子代理没有 durable 与压缩（只给总控与 Worker）与 report（18 号只给 Worker），
    # 但清理（8 号）与 14/16 号都在；spill 挂上但因定义里没有 read_artifact 而不外置。
    assert runtime.build_chain("subagent").names == (
        "trace",
        "context_layout",
        "tool_result_clearing",
        "spill",
        "loop_guard",
        "mandate",
        "lease",
        "policy",
    )
    # 总控链里没有 Worker 的三个（14/16/18），但 6/7/8 号都在。
    assert runtime.build_chain("orchestrator").names == (
        "trace",
        "context_layout",
        "durable_context",
        "compaction",
        "tool_result_clearing",
        "spill",
        "loop_guard",
        "clarification",
        "policy",
    )
