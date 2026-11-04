"""M75 T3：计划状态机、批准前工具过滤与待批准汇报（spec §4.5、§6，06 §7.1/§7.2）。

用例分三层：``PlanService`` 的状态表（4 步待批准、3 步直接进行中、只改状态不重审、
改标题重审、退回草稿、工单结束放弃）、``PlanMiddleware`` 的批准前工具过滤与尾部
``plan`` 段、``update_plan`` 工具与 ``ensure_report`` 的 waiting 汇报。临时库沿用
``tests/fakes/v3_db`` 的 ``make_session_factory``，不碰真实 ``resource/maa_api.db``。
"""

from __future__ import annotations

import asyncio
import re
from types import SimpleNamespace

import pytest
from sqlalchemy import select

from maa_api.agent.context.layout import ContextParts
from maa_api.agent.harness.context import (
    CURRENT_RUN,
    ModelRequest,
    RunContext,
    RunOutcome,
    ToolCall,
    ToolOutcome,
)
from maa_api.agent.harness.plan import (
    PENDING_HUMAN_ACTION_KEY,
    PLAN_APPROVAL_REQUIRED,
    PlanMiddleware,
)
from maa_api.agent.plans.service import (
    PLAN_INVALID,
    PlanInvalid,
    PlanService,
    plan_human_action,
)
from maa_api.agent.registry import ToolContext
from maa_api.agent.specs.registry import builtin_spec
from maa_api.agent.tickets.inbox import InboxService
from maa_api.agent.tickets.service import TicketEnded, TicketService
from maa_api.agent.tickets.threads import ThreadRouter
from maa_api.agent.tools import build_registry
from maa_api.agent.tools.workspace import UpdatePlanParams, update_plan
from maa_api.agent.workers.middleware import WAITING_PLAN_SUMMARY, ensure_report
from maa_api.db.models import Ticket, TicketReport
from maa_api.domain.enums import CallerType, TicketStatus
from maa_api.domain.errors import AppError
from tests.fakes.v3_db import make_session_factory, seed_run, seed_session

ITEM_ID = re.compile(r"^[0-9a-f]{8}$")
TITLE = "刷 1-7 到 50 个固源岩"


def _service(factory) -> PlanService:
    """固定成 06 §7.1 的默认策略，用例不依赖真实 ``config.yaml``。"""
    return PlanService(factory, approval_mode="complex", complex_min_steps=4)


def _items(count: int, status: str = "pending") -> list[dict]:
    return [{"title": f"步骤 {index}", "status": status} for index in range(1, count + 1)]


def _spec(*tools: str):
    base = builtin_spec("general")
    return base.model_copy(update={"tools": (*base.tools, *tools)})


def _run(*, session_id: str, ticket_id: str | None, app: object) -> RunContext:
    return RunContext(
        run_id="run-plan",
        root_run_id="run-plan",
        parent_run_id=None,
        session_id=session_id,
        ticket_id=ticket_id,
        spec=_spec("update_plan"),
        app=app,
    )


class _current_run:
    def __init__(self, run: RunContext) -> None:
        self._run = run
        self._token = None

    def __enter__(self) -> None:
        self._token = CURRENT_RUN.set(self._run)

    def __exit__(self, *exc_info: object) -> None:
        CURRENT_RUN.reset(self._token)


def _tool_context(run: RunContext) -> ToolContext:
    return ToolContext(CallerType.INTERNAL, run.session_id, None, None, None)


async def _seed_ticket(factory, title: str = "计划用例") -> str:
    ticket = Ticket(
        title=title, kind="general", status=TicketStatus.OPEN.value, origin="user"
    )
    async with factory() as db:
        db.add(ticket)
        await db.commit()
    return str(ticket.id)


def test_complex_plan_waits_for_approval(tmp_path) -> None:
    """4 步达到 ``complex_min_steps``，第一次提交进入 awaiting_approval。"""

    async def scenario() -> None:
        engine, factory = await make_session_factory(tmp_path / "complex.db")
        try:
            session_id = await seed_session(factory, kind="worker")
            ticket_id = await _seed_ticket(factory)
            service = _service(factory)
            plan = await service.create_or_update(
                session_id=session_id,
                ticket_id=ticket_id,
                title=TITLE,
                items=_items(4),
            )
            assert plan.status == "awaiting_approval"
            assert plan.approved_by is None and plan.approved_at is None
            assert [item["status"] for item in plan.items] == ["pending"] * 4
            assert len({item["id"] for item in plan.items}) == 4
            assert all(ITEM_ID.match(item["id"]) for item in plan.items)

            # 同一时刻最多一步 in_progress：不写库，原计划保持原样。
            with pytest.raises(PlanInvalid):
                await service.create_or_update(
                    session_id=session_id,
                    ticket_id=ticket_id,
                    title=TITLE,
                    items=[
                        {"title": "步骤 1", "status": "in_progress"},
                        {"title": "步骤 2", "status": "in_progress"},
                    ],
                )
            stored = await service.active_for_session(session_id)
            assert stored is not None
            assert stored.status == "awaiting_approval"
            assert [item["title"] for item in stored.items] == [
                "步骤 1",
                "步骤 2",
                "步骤 3",
                "步骤 4",
            ]
        finally:
            engine.sync_engine.dispose()

    asyncio.run(scenario())


def test_short_plan_activates(tmp_path) -> None:
    """3 步不需要批准：直接 active，并把第一项改成 in_progress。"""

    async def scenario() -> None:
        engine, factory = await make_session_factory(tmp_path / "short.db")
        try:
            session_id = await seed_session(factory, kind="worker")
            service = _service(factory)
            plan = await service.create_or_update(
                session_id=session_id,
                ticket_id=None,
                title=TITLE,
                items=_items(3),
            )
            assert plan.status == "active"
            assert [item["status"] for item in plan.items] == [
                "in_progress",
                "pending",
                "pending",
            ]
            assert plan.approved_at is None
        finally:
            engine.sync_engine.dispose()

    asyncio.run(scenario())


def test_blocked_tool_before_approval(tmp_path) -> None:
    """待批准时 ``submit_pipeline`` 在策略引擎之前被拦，只读工具照常放行。"""

    async def scenario() -> None:
        engine, factory = await make_session_factory(tmp_path / "blocked.db")
        try:
            session_id = await seed_session(factory, kind="worker")
            ticket_id = await _seed_ticket(factory)
            service = _service(factory)
            await service.create_or_update(
                session_id=session_id,
                ticket_id=ticket_id,
                title=TITLE,
                items=_items(4),
            )
            app = SimpleNamespace(state=SimpleNamespace(plan_service=service))
            middleware = PlanMiddleware(registry=build_registry())
            ctx = _run(session_id=session_id, ticket_id=ticket_id, app=app)

            async def forbidden(_call: ToolCall) -> ToolOutcome:  # pragma: no cover
                raise AssertionError("待批准的工具不应进入策略引擎")

            blocked = await middleware.wrap_tool(
                ctx,
                ToolCall(
                    id="call-submit",
                    name="submit_pipeline",
                    raw_arguments="{}",
                    arguments={},
                ),
                forbidden,
            )
            assert blocked.status == "error"
            assert blocked.error is not None
            assert blocked.error["code"] == PLAN_APPROVAL_REQUIRED
            assert blocked.error["details"]["plan_id"]

            async def allowed(_call: ToolCall) -> ToolOutcome:
                return ToolOutcome(status="ok", result={"ok": True})

            readonly = await middleware.wrap_tool(
                ctx,
                ToolCall(
                    id="call-read",
                    name="get_system_status",
                    raw_arguments="{}",
                    arguments={},
                ),
                allowed,
            )
            assert readonly.status == "ok"
            # 计划自身与汇报仍在白名单里（read_only=False 但必须放行）。
            for name in ("update_plan", "report", "ask_user"):
                outcome = await middleware.wrap_tool(
                    ctx,
                    ToolCall(id=f"call-{name}", name=name, raw_arguments="{}", arguments={}),
                    allowed,
                )
                assert outcome.status == "ok"
        finally:
            engine.sync_engine.dispose()

    asyncio.run(scenario())


def test_status_only_edit_does_not_reapprove(tmp_path) -> None:
    """已批准计划只改状态/备注：保持 active，批准信息不变。"""

    async def scenario() -> None:
        engine, factory = await make_session_factory(tmp_path / "status.db")
        try:
            session_id = await seed_session(factory, kind="worker")
            service = _service(factory)
            created = await service.create_or_update(
                session_id=session_id,
                ticket_id=None,
                title=TITLE,
                items=_items(4),
            )
            approved = await service.approve(created.id)
            assert approved.status == "active"
            edit = [dict(item) for item in approved.items]
            edit[0]["status"] = "in_progress"
            edit[1]["status"] = "done"
            edit[2]["note"] = "掉落表已核对"
            edit[3]["status"] = "cancelled"
            updated = await service.create_or_update(
                session_id=session_id,
                ticket_id=None,
                title=TITLE,
                items=edit,
            )
            assert updated.status == "active"
            assert updated.approved_at == approved.approved_at
            assert updated.approved_by == "user"
            assert [item["status"] for item in updated.items] == [
                "in_progress",
                "done",
                "pending",
                "cancelled",
            ]
            # id 在只改状态时被复用，不重新生成。
            assert [item["id"] for item in updated.items] == [
                item["id"] for item in created.items
            ]
        finally:
            engine.sync_engine.dispose()

    asyncio.run(scenario())


def test_rewrite_title_reapproves(tmp_path) -> None:
    """改写某步标题或新增步骤：重新待批准，上一版批准作废。"""

    async def scenario() -> None:
        engine, factory = await make_session_factory(tmp_path / "rewrite.db")
        try:
            session_id = await seed_session(factory, kind="worker")
            service = _service(factory)
            created = await service.create_or_update(
                session_id=session_id,
                ticket_id=None,
                title=TITLE,
                items=_items(4),
            )
            await service.approve(created.id)
            renamed = [dict(item) for item in created.items]
            renamed[1]["title"] = "提交 1-7 刷图流水线"
            rewritten = await service.create_or_update(
                session_id=session_id,
                ticket_id=None,
                title=TITLE,
                items=renamed,
            )
            assert rewritten.status == "awaiting_approval"
            assert rewritten.approved_by is None and rewritten.approved_at is None

            # 重新批准后再新增一步：仍然重新待批准。
            await service.approve(rewritten.id)
            extended = [dict(item) for item in rewritten.items] + [
                {"title": "结束后核对掉落", "status": "pending"}
            ]
            again = await service.create_or_update(
                session_id=session_id,
                ticket_id=None,
                title=TITLE,
                items=extended,
            )
            assert again.status == "awaiting_approval"
            assert len(again.items) == 5
        finally:
            engine.sync_engine.dispose()

    asyncio.run(scenario())


def test_reject_returns_to_draft(tmp_path) -> None:
    """``approved=false`` 退回 draft；再次提交仍按是否需要批准判定。"""

    async def scenario() -> None:
        engine, factory = await make_session_factory(tmp_path / "reject.db")
        try:
            session_id = await seed_session(factory, kind="worker")
            service = _service(factory)
            created = await service.create_or_update(
                session_id=session_id,
                ticket_id=None,
                title=TITLE,
                items=_items(4),
            )
            rejected = await service.reject(created.id, reason="步骤太粗")
            assert rejected.status == "draft"
            assert rejected.approved_at is None
            # draft 不能直接批准，只能改完再提交。
            with pytest.raises(AppError):
                await service.approve(created.id)
            resubmitted = await service.create_or_update(
                session_id=session_id,
                ticket_id=None,
                title=TITLE,
                items=_items(4),
            )
            assert resubmitted.status == "awaiting_approval"
        finally:
            engine.sync_engine.dispose()

    asyncio.run(scenario())


def test_ticket_end_abandons_plan(tmp_path) -> None:
    """工单进终态：名下未结束计划全部 abandoned，之后读不到当前计划。"""

    async def scenario() -> None:
        engine, factory = await make_session_factory(tmp_path / "ended.db")
        try:
            session_id = await seed_session(factory, kind="worker")
            ticket_id = await _seed_ticket(factory)
            service = _service(factory)
            plan = await service.create_or_update(
                session_id=session_id,
                ticket_id=ticket_id,
                title=TITLE,
                items=_items(4),
            )
            ended = TicketEnded(
                ticket_id=ticket_id,
                status=TicketStatus.CANCELLED,
                worker_session_id=session_id,
            )
            assert await service.on_ticket_ended(ended) == 1
            stored = await service.get(plan.id)
            assert stored is not None and stored.status == "abandoned"
            assert await service.active_for_session(session_id) is None
        finally:
            engine.sync_engine.dispose()

    asyncio.run(scenario())


def test_plan_segment_and_human_action_in_before_model(tmp_path) -> None:
    """``before_model`` 写尾部 plan 段与待批准 ``human_action``；无计划不插段。"""

    async def scenario() -> None:
        engine, factory = await make_session_factory(tmp_path / "tail.db")
        try:
            session_id = await seed_session(factory, kind="worker")
            service = _service(factory)
            plan = await service.create_or_update(
                session_id=session_id,
                ticket_id=None,
                title=TITLE,
                items=[
                    {"title": "查当前库存与理智", "status": "done"},
                    {"title": "提交 1-7 刷图流水线", "status": "in_progress"},
                    {"title": "结束后核对掉落", "status": "pending"},
                ],
            )
            app = SimpleNamespace(state=SimpleNamespace(plan_service=service))
            middleware = PlanMiddleware(registry=build_registry())
            ctx = _run(session_id=session_id, ticket_id=None, app=app)

            # 3 步不需要批准：没有 human_action，但尾部 plan 段带「进行中」。
            req = ModelRequest(
                messages=[],
                tools=[],
                profile=SimpleNamespace(key="test"),
                meta={"layout_parts": ContextParts()},
            )
            await middleware.before_model(ctx, req)
            assert PENDING_HUMAN_ACTION_KEY not in ctx.state
            assert req.messages[-1]["role"] == "system"
            assert f"当前计划「{TITLE}」（进行中）：" in req.messages[-1]["content"]
            assert "1. [完成] 查当前库存与理智" in req.messages[-1]["content"]
            assert "2. [进行中] 提交 1-7 刷图流水线" in req.messages[-1]["content"]

            # 改成 4 步：待批准，human_action 进入 ctx.state，供 ensure_report 代交。
            await service.create_or_update(
                session_id=session_id,
                ticket_id=None,
                title=TITLE,
                items=_items(4),
            )
            pending_req = ModelRequest(
                messages=[],
                tools=[],
                profile=SimpleNamespace(key="test"),
                meta={"layout_parts": ContextParts()},
            )
            await middleware.before_model(ctx, pending_req)
            assert ctx.state[PENDING_HUMAN_ACTION_KEY] == plan_human_action(
                await service.active_for_session(session_id)
            )
            assert "（等待批准）" in pending_req.messages[-1]["content"]

            # 没有计划的会话：不插 plan 段，也不留待批准状态。
            other_session = await seed_session(factory, kind="worker")
            other = _run(session_id=other_session, ticket_id=None, app=app)
            empty_req = ModelRequest(
                messages=[],
                tools=[],
                profile=SimpleNamespace(key="test"),
                meta={"layout_parts": ContextParts()},
            )
            await middleware.before_model(other, empty_req)
            assert empty_req.messages == []
            assert PENDING_HUMAN_ACTION_KEY not in other.state
        finally:
            engine.sync_engine.dispose()

    asyncio.run(scenario())


def test_update_plan_tool_returns_human_action_and_rejects_invalid(tmp_path) -> None:
    """工具结果带 plan_id/status/items 与待批准 human_action；非法步骤报 plan_invalid。"""

    async def scenario() -> None:
        engine, factory = await make_session_factory(tmp_path / "tool.db")
        try:
            session_id = await seed_session(factory, kind="worker")
            ticket_id = await _seed_ticket(factory)
            service = _service(factory)
            app = SimpleNamespace(state=SimpleNamespace(plan_service=service))
            ctx = _run(session_id=session_id, ticket_id=ticket_id, app=app)
            params = UpdatePlanParams(
                title=TITLE, items=[{"title": item["title"]} for item in _items(4)]
            )
            with _current_run(ctx):
                result = await update_plan(params, _tool_context(ctx))
            assert result["status"] == "awaiting_approval"
            assert len(result["items"]) == 4
            assert result["human_action"]["ref_id"] == result["plan_id"]
            assert result["human_action"]["prompt"] == f"批准计划「{TITLE}」？"
            assert ctx.state[PENDING_HUMAN_ACTION_KEY] == result["human_action"]

            invalid = UpdatePlanParams(
                title=TITLE,
                items=[
                    {"title": "步骤 1", "status": "in_progress"},
                    {"title": "步骤 2", "status": "in_progress"},
                ],
            )
            with _current_run(ctx):
                rejected = await update_plan(invalid, _tool_context(ctx))
            assert rejected["error"]["code"] == PLAN_INVALID
            stored = await service.active_for_session(session_id)
            assert stored is not None and stored.status == "awaiting_approval"
            assert len(stored.items) == 4
        finally:
            engine.sync_engine.dispose()

    asyncio.run(scenario())


def test_waiting_report_when_plan_pending(tmp_path) -> None:
    """一轮结束仍待批准：补一条 waiting 汇报；工单已指向同一计划时不重复。"""

    async def scenario() -> None:
        engine, factory = await make_session_factory(tmp_path / "waiting.db")
        try:
            session_id = await seed_session(factory, kind="worker")
            ticket_id = await _seed_ticket(factory)
            service = _service(factory)
            plan = await service.create_or_update(
                session_id=session_id,
                ticket_id=ticket_id,
                title=TITLE,
                items=_items(4),
            )
            inbox = InboxService(factory)
            tickets = TicketService(
                factory,
                inbox_service=inbox,
                thread_router=ThreadRouter(factory, inbox),
                spec_registry=None,
            )
            app = SimpleNamespace(
                state=SimpleNamespace(plan_service=service, ticket_service=tickets)
            )
            run_id = await seed_run(
                factory, session_id=session_id, ticket_id=ticket_id
            )
            ctx = _run(session_id=session_id, ticket_id=ticket_id, app=app)
            ctx.run_id = run_id
            ctx.state[PENDING_HUMAN_ACTION_KEY] = plan_human_action(plan)
            await ensure_report(ctx, RunOutcome("succeeded", "stop"))

            async with factory() as db:
                stored = list(
                    (
                        await db.execute(
                            select(TicketReport).where(
                                TicketReport.ticket_id == ticket_id
                            )
                        )
                    ).scalars()
                )
            assert len(stored) == 1
            assert stored[0].status == "waiting"
            assert stored[0].summary == WAITING_PLAN_SUMMARY
            assert stored[0].human_action["ref_id"] == str(plan.id)

            # 新一轮（新的 ctx）仍是待批准，但工单已经指向这份计划：不再重复。
            fresh = _run(session_id=session_id, ticket_id=ticket_id, app=app)
            fresh.run_id = run_id
            fresh.state[PENDING_HUMAN_ACTION_KEY] = plan_human_action(plan)
            await ensure_report(fresh, RunOutcome("succeeded", "stop"))
            async with factory() as db:
                count = len(
                    list(
                        (
                            await db.execute(
                                select(TicketReport).where(
                                    TicketReport.ticket_id == ticket_id
                                )
                            )
                        ).scalars()
                    )
                )
            assert count == 1
        finally:
            engine.sync_engine.dispose()

    asyncio.run(scenario())
