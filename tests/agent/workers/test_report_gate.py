"""M87 T3：``ready`` 汇报的完成闸门接入（spec §4.3、§4.5.3、§6）。

用例沿用 M70 的写法：同步测试函数 + ``asyncio.run(scenario())``，临时库用
``tests/fakes/v3_db``。``report`` 经真实的 ``ConfirmationService`` 调用，工具
handler 从 ``CURRENT_RUN`` 取运行、再从 ``app.state.worker_service`` 取真实的
``WorkerService``，这样 ``agent_audit`` 的 ``success`` 行与「不结束运行」都是
生产路径上的行为。

与 ``test_worker_service.py`` 的私有 helper 不同：这里的工单是 ``daily_ops``，
``submit_pipeline`` 的审计与 ``agent_run`` 由用例自己写，否则闸门按 §4.5.1
的归属条件过滤后只能得到 ``not_required``。
"""

from __future__ import annotations

import asyncio
import json
from datetime import timedelta
from types import SimpleNamespace

from sqlalchemy import select

from maa_api.agent.confirmation import ConfirmationService
from maa_api.agent.harness.context import (
    CURRENT_RUN,
    ModelResponse,
    RunContext,
    ToolCall,
    ToolOutcome,
    Usage,
)
from maa_api.agent.harness.events import RunEventBus
from maa_api.agent.harness.loop import AgentLoop, MemoryTranscript
from maa_api.agent.harness.middleware import Middleware, MiddlewareChain
from maa_api.agent.registry import ToolContext, ToolOrigin
from maa_api.agent.tickets.inbox import InboxService
from maa_api.agent.tickets.mandates import build_mandate
from maa_api.agent.tickets.service import TicketService
from maa_api.agent.tickets.threads import ThreadRouter
from maa_api.agent.tools import build_registry
from maa_api.agent.tools.report import ReportParams
from maa_api.agent.workers.middleware import SUBMITTED_KEY, ReportMiddleware
from maa_api.agent.workers.service import WorkerService
from maa_api.db.models import (
    AgentAudit,
    AgentRun,
    AgentSession,
    Pipeline,
    Ticket,
    TicketReport,
    new_uuid,
    utcnow,
)
from maa_api.domain.enums import (
    AgentRunStatus,
    CallerType,
    PipelineStatus,
    ReportStatus,
    TicketStatus,
)
from maa_api.domain.errors import AppError, ErrorCode
from tests.fakes.v3_db import make_session_factory, seed_session

_SPEC = SimpleNamespace(
    name="daily_ops",
    kind="worker",
    title="日常执行",
    description="跑日常",
    risk_ceiling="consume",
    model_role="worker",
    spec_version="abcdef123456",
    body="你是 MAA-API 的日常 Worker",
    tools=(),
    delegates=(),
    verify_before_ready=True,
    max_steps=10,
)


class _Registry:
    """按名字返回 ``daily_ops``；``planner`` 只用于「没有闸门标志」的用例。"""

    def __init__(self) -> None:
        self._specs = {
            "daily_ops": _SPEC,
            "planner": SimpleNamespace(
                name="planner",
                kind="worker",
                title="计划员",
                description="写计划",
                risk_ceiling="none",
                model_role="worker",
                spec_version="abcdef123456",
                body="你是计划员",
                tools=(),
                delegates=(),
                max_steps=10,
            ),
        }

    def get(self, name: str) -> SimpleNamespace:
        if name in self._specs:
            return self._specs[name]
        raise AppError(
            ErrorCode.AGENT_SPEC_NOT_FOUND, f"没有名为 {name} 的代理定义"
        )

    def list(self) -> list[SimpleNamespace]:
        return list(self._specs.values())


class _ProfileRouter:
    def __init__(self) -> None:
        self.profile = SimpleNamespace(
            model="test-model",
            base_url="https://llm.test/v1",
            api_key="sk-test",
        )

    async def resolve(self, role: str) -> SimpleNamespace:
        return self.profile


class _Titler:
    async def title_for(self, text: str) -> str:
        return "自动标题"


class _Orchestrator:
    async def current_session_id(self) -> str | None:
        return None

    async def notify(self, text: str, *, passive: bool, **_kwargs) -> int:
        return 0


class _RunService:
    """测试不允许真的起一轮。"""

    async def get(self, run_id: str) -> SimpleNamespace | None:
        return None

    async def start(self, **kwargs) -> SimpleNamespace:
        raise AssertionError("这个用例不应该真的起一轮")

    async def finish(self, run_id: str, *, status, finish_reason: str) -> None:
        return None


class _Harness:
    def __init__(self, factory) -> None:
        self.factory = factory
        self.inbox = InboxService(factory)
        self.router = ThreadRouter(factory, self.inbox)
        self.registry = _Registry()
        self.events: list[tuple[str, dict]] = []
        self.ticket_service = TicketService(
            factory,
            inbox_service=self.inbox,
            thread_router=self.router,
            spec_registry=self.registry,
            broadcast=self._record,
        )
        self.state = SimpleNamespace(
            model_router=_ProfileRouter(),
            setting_service=None,
            question_service=None,
            confirmation_service=None,
            ticket_service=self.ticket_service,
        )
        self.app = SimpleNamespace(state=self.state)
        self.service = WorkerService(
            app=self.app,
            session_factory=factory,
            spec_registry=self.registry,
            run_service=_RunService(),
            inbox_service=self.inbox,
            ticket_service=self.ticket_service,
            thread_router=self.router,
            delegation=SimpleNamespace(),
            titler=_Titler(),
            event_bus=RunEventBus(run_id=None),
        )
        self.service.bind_orchestrator(_Orchestrator())
        # ``report`` 的 handler 从 ``CURRENT_RUN.app.state.worker_service`` 取服务。
        self.state.worker_service = self.service

    def _record(self, message_type: str, data: dict) -> None:
        self.events.append((message_type, data))

    async def close(self) -> None:
        await self.service.close()


async def _make_ticket(factory) -> tuple[str, str]:
    """建一张 ``daily_ops`` 工单并挂 Worker 会话；返回 ``(ticket_id, session_id)``。"""
    ticket_id = new_uuid()
    async with factory() as db:
        db.add(
            Ticket(
                id=ticket_id,
                title="日常",
                kind="daily_ops",
                status=TicketStatus.OPEN.value,
                origin="user",
                priority=1,
            )
        )
        await db.flush()
        db.add(
            build_mandate(
                ticket_id=ticket_id,
                scope="测试范围",
                origin="user",
                source="web",
                sender="web",
                rule_id=None,
                spec_ceiling=_SPEC.risk_ceiling,
            )
        )
        await db.commit()
    worker_session_id = await seed_session(factory, kind="worker", ticket_id=ticket_id)
    async with factory() as db:
        stored = await db.get(Ticket, ticket_id)
        assert stored is not None
        stored.worker_session_id = worker_session_id
        await db.commit()
    return ticket_id, worker_session_id


async def _seed_effect(
    factory,
    *,
    ticket_id: str,
    worker_session_id: str,
    tool_name: str = "submit_pipeline",
    created_at=None,
) -> str:
    """写一条本工单 Worker 根运行的写设备审计与对应 ``agent_run``。"""
    run_id = new_uuid()
    moment = created_at or utcnow()
    async with factory() as db:
        db.add(
            AgentRun(
                id=run_id,
                root_run_id=run_id,
                session_id=worker_session_id,
                ticket_id=ticket_id,
                agent_name="daily_ops",
                kind="worker_turn",
                status=AgentRunStatus.RUNNING.value,
                boot_id="boot-test",
                started_at=moment,
            )
        )
        await db.commit()
    async with factory() as db:
        db.add(
            AgentAudit(
                caller=CallerType.INTERNAL.value,
                ticket_id=ticket_id,
                session_id=worker_session_id,
                agent_name="daily_ops",
                tool_name=tool_name,
                status="success",
                run_id=run_id,
                created_at=moment,
            )
        )
        await db.commit()
    return run_id


async def _add_pipeline(
    factory,
    *,
    status: PipelineStatus = PipelineStatus.COMPLETED,
    agent_session_id: str | None = None,
    title: str = "日常",
    created_at=None,
    finished_at=None,
) -> str:
    pipeline_id = new_uuid()
    now = utcnow()
    async with factory() as db:
        db.add(
            Pipeline(
                id=pipeline_id,
                source="agent",
                priority=1,
                task_count=1,
                status=status.value,
                title=title,
                agent_session_id=agent_session_id,
                created_at=created_at or now,
                started_at=(created_at or now) - timedelta(minutes=3),
                finished_at=finished_at or now,
            )
        )
        await db.commit()
    return pipeline_id


async def _add_verifier_verdict(
    factory,
    *,
    ticket_id: str,
    worker_session_id: str,
    finished_at,
    passed: bool = True,
    confidence: float = 0.9,
) -> str:
    """写一条本工单的验证员运行，``meta`` 里带结论。"""
    run_id = new_uuid()
    async with factory() as db:
        db.add(
            AgentRun(
                id=run_id,
                root_run_id=run_id,
                session_id=worker_session_id,
                parent_run_id=run_id,
                ticket_id=ticket_id,
                agent_name="verifier",
                kind="subagent",
                status=AgentRunStatus.SUCCEEDED.value,
                boot_id="boot-test",
                started_at=finished_at - timedelta(minutes=2),
                finished_at=finished_at,
                meta={
                    "verdict": {
                        "passed": passed,
                        "confidence": confidence,
                        "evidence": ["截图显示 1-7 三星", "流水线 completed"],
                        "notes": "",
                        "degraded": False,
                        "recorded_at": "2026-11-09T20:00:00Z",
                    }
                },
            )
        )
        await db.commit()
    return run_id


async def _reports(factory, ticket_id: str) -> list[TicketReport]:
    async with factory() as db:
        result = await db.execute(
            select(TicketReport)
            .where(TicketReport.ticket_id == ticket_id)
            .order_by(TicketReport.id.asc())
        )
        return list(result.scalars().all())


async def _ticket(factory, ticket_id: str) -> Ticket | None:
    async with factory() as db:
        return await db.get(Ticket, ticket_id)


async def _report_audits(factory, run_id: str) -> list[AgentAudit]:
    async with factory() as db:
        result = await db.execute(
            select(AgentAudit).where(
                AgentAudit.tool_name == "report", AgentAudit.run_id == run_id
            )
        )
        return list(result.scalars().all())


def _context(
    harness: _Harness,
    *,
    run_id: str,
    ticket_id: str,
    session_id: str,
    spec: SimpleNamespace = _SPEC,
    emitted: list[tuple[str, dict]] | None = None,
) -> RunContext:
    async def emit(name: str, data: dict) -> None:
        if emitted is not None:
            emitted.append((name, data))

    return RunContext(
        run_id=run_id,
        root_run_id=run_id,
        parent_run_id=None,
        session_id=session_id,
        ticket_id=ticket_id,
        spec=spec,
        app=harness.app,
        emit=emit,
    )


async def _invoke(
    harness: _Harness,
    ctx: RunContext,
    arguments: dict,
) -> dict:
    """经真实 ``ConfirmationService`` 调 ``report``；``CURRENT_RUN`` 指向同一运行。"""
    service = ConfirmationService(harness.factory, build_registry())
    tool_context = ToolContext(
        CallerType.INTERNAL,
        ctx.session_id,
        "request-report",
        SimpleNamespace(app=harness.app),
        None,
        ToolOrigin(
            run_id=ctx.run_id,
            ticket_id=ctx.ticket_id,
            agent=str(ctx.spec.name),
        ),
    )
    token = CURRENT_RUN.set(ctx)
    try:
        return await service.invoke("report", arguments, tool_context)
    finally:
        CURRENT_RUN.reset(token)


def _details_of_size(size: int) -> dict:
    """构造序列化后恰好 ``size`` 字符的 details。"""
    payload: dict = {"note": ""}
    filler = size - len(json.dumps(payload, ensure_ascii=False))
    assert filler > 0
    payload["note"] = "x" * filler
    assert len(json.dumps(payload, ensure_ascii=False)) == size
    return payload


def test_rejected_ready_keeps_ticket_and_run_going(tmp_path) -> None:
    engine, factory = asyncio.run(make_session_factory(tmp_path / "rejected.db"))
    try:

        async def scenario() -> None:
            ticket_id, worker_session_id = await _make_ticket(factory)
            run_id = await _seed_effect(
                factory,
                ticket_id=ticket_id,
                worker_session_id=worker_session_id,
            )
            await _add_pipeline(
                factory,
                status=PipelineStatus.FAILED,
                agent_session_id=worker_session_id,
            )
            harness = _Harness(factory)
            emitted: list[tuple[str, dict]] = []
            ctx = _context(
                harness,
                run_id=run_id,
                ticket_id=ticket_id,
                session_id=worker_session_id,
                emitted=emitted,
            )

            payload = await _invoke(
                harness,
                ctx,
                {"status": "ready", "summary": "日常做完了"},
            )

            result = payload["result"]
            assert result["accepted"] is False
            assert result["reason"] == "verification_required"
            assert result["delegate"]["agent"] == "verifier"
            assert payload["status"] == "success"

            # 被拒的这次调用不写 ticket_report，工单状态不变，也不发 report 事件。
            assert await _reports(factory, ticket_id) == []
            stored = await _ticket(factory, ticket_id)
            assert stored is not None
            assert str(stored.status) == TicketStatus.OPEN.value
            assert emitted == []
            audits = await _report_audits(factory, run_id)
            assert [(row.tool_name, row.status) for row in audits] == [
                ("report", "success")
            ]
            await harness.close()

        asyncio.run(scenario())
    finally:
        engine.sync_engine.dispose()


def test_accepted_ready_writes_verification_details(tmp_path) -> None:
    engine, factory = asyncio.run(make_session_factory(tmp_path / "accepted.db"))
    try:

        async def scenario() -> None:
            ticket_id, worker_session_id = await _make_ticket(factory)
            run_id = await _seed_effect(
                factory,
                ticket_id=ticket_id,
                worker_session_id=worker_session_id,
            )
            await _add_pipeline(
                factory,
                status=PipelineStatus.COMPLETED,
                agent_session_id=worker_session_id,
            )
            harness = _Harness(factory)
            ctx = _context(
                harness,
                run_id=run_id,
                ticket_id=ticket_id,
                session_id=worker_session_id,
            )

            payload = await _invoke(
                harness,
                ctx,
                {
                    "status": "ready",
                    "summary": "日常做完了",
                    "details": {"note": "两条流水线"},
                },
            )

            assert payload["status"] == "success"
            reports = await _reports(factory, ticket_id)
            assert len(reports) == 1
            details = reports[0].details or {}
            assert details["verification"]["method"] == "machine"
            assert details["note"] == "两条流水线"
            stored = await _ticket(factory, ticket_id)
            assert stored is not None
            assert str(stored.status) == TicketStatus.DONE.value
            await harness.close()

        asyncio.run(scenario())
    finally:
        engine.sync_engine.dispose()


def test_non_ready_reports_skip_gate(tmp_path) -> None:
    engine, factory = asyncio.run(make_session_factory(tmp_path / "failed.db"))
    try:

        async def scenario() -> None:
            ticket_id, worker_session_id = await _make_ticket(factory)
            run_id = await _seed_effect(
                factory,
                ticket_id=ticket_id,
                worker_session_id=worker_session_id,
            )
            await _add_pipeline(
                factory,
                status=PipelineStatus.FAILED,
                agent_session_id=worker_session_id,
            )
            harness = _Harness(factory)
            ctx = _context(
                harness,
                run_id=run_id,
                ticket_id=ticket_id,
                session_id=worker_session_id,
            )

            payload = await _invoke(
                harness,
                ctx,
                {"status": "failed", "summary": "没跑成"},
            )

            assert payload["status"] == "success"
            reports = await _reports(factory, ticket_id)
            assert len(reports) == 1
            assert reports[0].status == ReportStatus.FAILED.value
            assert (reports[0].details or {}).get("verification") is None
            await harness.close()

        asyncio.run(scenario())
    finally:
        engine.sync_engine.dispose()


def test_spec_without_flag_skips_gate(tmp_path) -> None:
    engine, factory = asyncio.run(make_session_factory(tmp_path / "no-flag.db"))
    try:

        async def scenario() -> None:
            ticket_id, worker_session_id = await _make_ticket(factory)
            run_id = await _seed_effect(
                factory,
                ticket_id=ticket_id,
                worker_session_id=worker_session_id,
            )
            await _add_pipeline(
                factory,
                status=PipelineStatus.FAILED,
                agent_session_id=worker_session_id,
            )
            harness = _Harness(factory)
            planner = harness.registry.get("planner")
            ctx = _context(
                harness,
                run_id=run_id,
                ticket_id=ticket_id,
                session_id=worker_session_id,
                spec=planner,
            )

            payload = await _invoke(
                harness,
                ctx,
                {
                    "status": "ready",
                    "summary": "计划写完了",
                    "details": {"note": "没有闸门"},
                },
            )

            assert payload["status"] == "success"
            reports = await _reports(factory, ticket_id)
            assert len(reports) == 1
            assert reports[0].details == {"note": "没有闸门"}
            await harness.close()

        asyncio.run(scenario())
    finally:
        engine.sync_engine.dispose()


def test_merged_details_trim_evidence(tmp_path) -> None:
    engine, factory = asyncio.run(make_session_factory(tmp_path / "trim.db"))
    try:

        async def scenario() -> None:
            ticket_id, worker_session_id = await _make_ticket(factory)
            run_id = await _seed_effect(
                factory,
                ticket_id=ticket_id,
                worker_session_id=worker_session_id,
            )
            await _add_pipeline(
                factory,
                status=PipelineStatus.COMPLETED,
                agent_session_id=worker_session_id,
            )
            harness = _Harness(factory)
            ctx = _context(
                harness,
                run_id=run_id,
                ticket_id=ticket_id,
                session_id=worker_session_id,
            )
            original = _details_of_size(3900)

            payload = await _invoke(
                harness,
                ctx,
                {
                    "status": "ready",
                    "summary": "做完了",
                    "details": original,
                },
            )

            assert payload["status"] == "success"
            reports = await _reports(factory, ticket_id)
            assert len(reports) == 1
            details = reports[0].details or {}
            assert details["verification"]["method"] == "machine"
            assert details["verification"]["evidence"] == []
            assert len(json.dumps(details, ensure_ascii=False)) <= 4000
            await harness.close()

        asyncio.run(scenario())
    finally:
        engine.sync_engine.dispose()


def test_verification_metadata_survives_full_details(tmp_path) -> None:
    engine, factory = asyncio.run(make_session_factory(tmp_path / "full.db"))
    try:

        async def scenario() -> None:
            ticket_id, worker_session_id = await _make_ticket(factory)
            now = utcnow()
            run_id = await _seed_effect(
                factory,
                ticket_id=ticket_id,
                worker_session_id=worker_session_id,
                tool_name="click",
                created_at=now - timedelta(minutes=3),
            )
            await _add_pipeline(
                factory,
                status=PipelineStatus.COMPLETED,
                agent_session_id=worker_session_id,
                created_at=now - timedelta(minutes=2),
                finished_at=now - timedelta(minutes=2),
            )
            verdict_run_id = await _add_verifier_verdict(
                factory,
                ticket_id=ticket_id,
                worker_session_id=worker_session_id,
                finished_at=now,
            )
            harness = _Harness(factory)
            ctx = _context(
                harness,
                run_id=run_id,
                ticket_id=ticket_id,
                session_id=worker_session_id,
            )
            original = _details_of_size(4000)

            payload = await _invoke(
                harness,
                ctx,
                {
                    "status": "ready",
                    "summary": "做完了",
                    "details": original,
                },
            )

            assert payload["status"] == "success"
            reports = await _reports(factory, ticket_id)
            assert len(reports) == 1
            details = reports[0].details or {}
            verification = details["verification"]
            assert verification["method"] == "verifier"
            assert verification["verdict_run_id"] == verdict_run_id
            assert "evidence" not in verification
            assert len(json.dumps(details, ensure_ascii=False)) <= 4120
            await harness.close()

        asyncio.run(scenario())
    finally:
        engine.sync_engine.dispose()


class _ServiceExecutor(Middleware):
    """把 ``report`` 交给真实的 ``WorkerService``，其余工具一律成功。"""

    name = "executor"

    def __init__(self, harness: _Harness, seen: list[dict]) -> None:
        self.harness = harness
        self.seen = seen

    async def wrap_tool(
        self, ctx: RunContext, call: ToolCall, call_next
    ) -> ToolOutcome:
        if call.name == "report":
            result = await self.harness.service.submit_report(
                ctx, ReportParams.model_validate(call.arguments or {})
            )
            self.seen.append({"name": call.name, "result": result})
            return ToolOutcome(status="ok", result=result)
        self.seen.append({"name": call.name, "result": None})
        return ToolOutcome(status="ok", result={"ok": True})


def test_rejected_report_continues_to_next_batch(tmp_path) -> None:
    """§5 T3 第 4 步的测试落点：被拒的 ``report`` 不是这一轮的收尾。"""
    engine, factory = asyncio.run(make_session_factory(tmp_path / "next-batch.db"))
    try:

        async def scenario() -> None:
            ticket_id, worker_session_id = await _make_ticket(factory)
            run_id = await _seed_effect(
                factory,
                ticket_id=ticket_id,
                worker_session_id=worker_session_id,
            )
            await _add_pipeline(
                factory,
                status=PipelineStatus.FAILED,
                agent_session_id=worker_session_id,
            )
            harness = _Harness(factory)
            ctx = _context(
                harness,
                run_id=run_id,
                ticket_id=ticket_id,
                session_id=worker_session_id,
            )
            responses = [
                ModelResponse(
                    text="",
                    reasoning="",
                    usage=Usage(),
                    upstream_finish_reason="tool_calls",
                    latency_ms=1,
                    raw_tool_calls=[
                        {
                            "id": "call-report",
                            "type": "function",
                            "function": {
                                "name": "report",
                                "arguments": json.dumps(
                                    {"status": "ready", "summary": "做完了"},
                                    ensure_ascii=False,
                                ),
                            },
                        }
                    ],
                ),
                ModelResponse(
                    text="",
                    reasoning="",
                    usage=Usage(),
                    upstream_finish_reason="tool_calls",
                    latency_ms=1,
                    raw_tool_calls=[
                        {
                            "id": "call-shot",
                            "type": "function",
                            "function": {
                                "name": "get_screenshot",
                                "arguments": "{}",
                            },
                        }
                    ],
                ),
                ModelResponse(
                    text="看过了",
                    reasoning="",
                    usage=Usage(),
                    upstream_finish_reason="stop",
                    latency_ms=1,
                ),
            ]

            async def call_model(_ctx: RunContext, _request: object) -> ModelResponse:
                return responses.pop(0)

            seen: list[dict] = []
            loop = AgentLoop(
                chain=MiddlewareChain([ReportMiddleware(), _ServiceExecutor(harness, seen)]),
                transcript=MemoryTranscript([{"role": "user", "content": "开始"}]),
                call_model=call_model,
                tool_schemas=lambda _ctx: [],
                registry=build_registry(),
            )

            outcome = await loop.run(ctx)

            assert [item["name"] for item in seen] == ["report", "get_screenshot"]
            assert seen[0]["result"]["accepted"] is False
            assert outcome.finish_reason != "report_submitted"
            assert outcome.finish_reason == "stop"
            assert ctx.state[SUBMITTED_KEY] is None
            # 轮末兜底补的 progress 带 details.auto；被拒的 ready 没有落行。
            reports = await _reports(factory, ticket_id)
            assert all(row.status != "ready" for row in reports)
            assert all((row.details or {}).get("auto") is True for row in reports)
            await harness.close()

        asyncio.run(scenario())
    finally:
        engine.sync_engine.dispose()
