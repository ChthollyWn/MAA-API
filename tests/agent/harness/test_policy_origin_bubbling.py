"""M71 T7：``PolicyMiddleware`` 的 origin、冒泡与子代理策略身份（规格 §4.5.6–§4.5.7、§6）。

两条用例：

- Worker 发起需确认的调用：SSE ``confirmation_required`` 带完整的 origin（工单标题、
  代理名、运行 id），确认冒泡把工单改成 ``waiting`` 并给总控写一条 ``human_request``，
  批准后工单回到 ``open``。
- 子代理调用：``on_run_start`` 读父 Worker 会话，审计的 ``session_id`` 是 Worker 会话、
  ``agent_name`` 是子代理自己；Worker 会话已有原子操作授权时 ``click`` 不再弹授权。

异步用例一律「同步测试函数 + ``asyncio.run(scenario())``」。
"""

from __future__ import annotations

import asyncio
from datetime import timedelta
from types import SimpleNamespace
from typing import Any

from pydantic import BaseModel, ConfigDict
from sqlalchemy import select

from maa_api.agent.confirmation import ConfirmationService
from maa_api.agent.harness.builtin_middleware import PolicyMiddleware
from maa_api.agent.harness.context import RunContext, ToolCall, ToolOutcome
from maa_api.agent.policy import PolicyEngine
from maa_api.agent.registry import ToolDefinition, ToolRegistry, ToolRisk
from maa_api.agent.specs.registry import builtin_spec
from maa_api.agent.tickets.inbox import InboxService
from maa_api.agent.tickets.service import TicketService
from maa_api.agent.tickets.threads import ThreadRouter
from maa_api.db.models import (
    AgentAudit,
    AgentInbox,
    AgentSession,
    Confirmation,
    Ticket,
    utcnow,
)
from maa_api.domain.enums import (
    CallerType,
    ConfirmationStatus,
    InboxKind,
    RiskLevel,
    TicketStatus,
)
from tests.fakes.v3_db import make_session_factory, seed_run, seed_session


class _ValueParams(BaseModel):
    model_config = ConfigDict(extra="forbid")

    value: int


class _ClickParams(BaseModel):
    model_config = ConfigDict(extra="forbid")

    x: int
    y: int


def _registry(*, atomic_click: bool = False) -> ToolRegistry:
    registry = ToolRegistry()

    async def dangerous_handler(params, _ctx) -> dict[str, Any]:
        return {"accepted": params.value}

    async def click_handler(params, _ctx) -> dict[str, Any]:
        return {"clicked": [params.x, params.y]}

    registry.register(
        ToolDefinition(
            name="dangerous_action",
            group="ops",
            risk=ToolRisk.DANGEROUS,
            description="需要确认的测试工具",
            params_model=_ValueParams,
            handler=dangerous_handler,
        )
    )
    if atomic_click:
        registry.register(
            ToolDefinition(
                name="click",
                group="raw",
                risk=ToolRisk.DANGEROUS,
                description="原子操作测试工具",
                params_model=_ClickParams,
                handler=click_handler,
            )
        )
    return registry


async def _seed_ticket(factory) -> tuple[str, str, str]:
    """总控会话、工单、Worker 会话与根运行；返回 ``(ticket, worker, run)``。"""
    orchestrator_id = await seed_session(factory, kind="orchestrator")
    worker_session_id = await seed_session(factory, kind="worker")
    async with factory() as db:
        ticket = Ticket(
            title="日常",
            kind="general",
            origin="user",
            orchestrator_session_id=orchestrator_id,
            worker_session_id=worker_session_id,
        )
        db.add(ticket)
        await db.commit()
        ticket_id = str(ticket.id)
    run_id = await seed_run(
        factory, session_id=worker_session_id, ticket_id=ticket_id
    )
    return ticket_id, worker_session_id, run_id


def _ticket_service(factory) -> TicketService:
    inbox = InboxService(factory)
    return TicketService(
        factory,
        inbox_service=inbox,
        thread_router=ThreadRouter(factory, inbox),
        spec_registry=SimpleNamespace(
            get=lambda _name: SimpleNamespace(kind="worker", risk_ceiling="destructive")
        ),
    )


async def _inbox(factory, session_id: str) -> list[AgentInbox]:
    async with factory() as db:
        result = await db.execute(
            select(AgentInbox)
            .where(AgentInbox.session_id == session_id)
            .order_by(AgentInbox.id)
        )
        return list(result.scalars().all())


def test_policy_middleware_emits_origin_and_bubbles(tmp_path) -> None:
    async def scenario() -> None:
        engine, factory = await make_session_factory(tmp_path / "policy-origin.db")
        try:
            ticket_id, worker_session_id, run_id = await _seed_ticket(factory)
            registry = _registry()
            confirmations = ConfirmationService(factory, registry, policy=PolicyEngine())
            ticket_service = _ticket_service(factory)
            app = SimpleNamespace(
                state=SimpleNamespace(
                    ticket_service=ticket_service,
                    thread_router=ticket_service._thread_router,
                )
            )
            emitted: list[tuple[str, dict[str, Any]]] = []

            async def emit(name: str, data: dict[str, Any]) -> None:
                emitted.append((name, data))
                if name == "confirmation_required":
                    await confirmations.resolve(
                        data["confirmation_id"],
                        approved=True,
                        resolved_by="web",
                    )

            ctx = RunContext(
                run_id=run_id,
                root_run_id=run_id,
                parent_run_id=None,
                session_id=worker_session_id,
                ticket_id=ticket_id,
                spec=builtin_spec("general"),
                app=app,
                run=SimpleNamespace(id=run_id, kind="worker_turn"),
                emit=emit,
            )
            middleware = PolicyMiddleware(
                confirmation_service=confirmations,
                session_factory=factory,
                poll_seconds=0.01,
            )
            await middleware.on_run_start(ctx)

            async def unused(_call: ToolCall) -> ToolOutcome:  # pragma: no cover
                raise AssertionError("PolicyMiddleware 是链上的执行器，不应调 call_next")

            outcome = await middleware.wrap_tool(
                ctx,
                ToolCall(
                    id="call-1",
                    name="dangerous_action",
                    raw_arguments='{"value": 2}',
                    arguments={"value": 2},
                ),
                unused,
            )

            assert outcome.status == "ok"
            assert outcome.result == {"accepted": 2}

            assert [name for name, _data in emitted] == ["confirmation_required"]
            confirmation_event = emitted[0][1]
            assert confirmation_event["origin"] == {
                "ticket_id": ticket_id,
                "ticket_title": "日常",
                "agent": "general",
                "run_id": run_id,
            }

            async with factory() as db:
                ticket = await db.get(Ticket, ticket_id)
            assert ticket is not None
            # 批准之后 settle 把工单从 waiting 改回 open。
            assert ticket.status == TicketStatus.OPEN.value
            assert ticket.human_action is None

            async with factory() as db:
                orchestrator_id = await db.scalar(
                    select(AgentSession.id).where(AgentSession.kind == "orchestrator")
                )
            items = await _inbox(factory, str(orchestrator_id))
            assert len(items) == 1
            assert items[0].kind == InboxKind.SYSTEM.value
            assert items[0].ref_id == confirmation_event["confirmation_id"]
            assert items[0].payload["event"] == "human_request"
            assert items[0].payload["type"] == "confirmation"
            assert items[0].payload["ticket_id"] == ticket_id
            assert items[0].payload["agent"] == "general"
            detail = await confirmations.get(confirmation_event["confirmation_id"])
            assert items[0].payload["prompt"] == detail["reason"][:200]
            assert "dangerous_action" in items[0].payload["prompt"]
        finally:
            engine.sync_engine.dispose()

    asyncio.run(scenario())


def test_subagent_calls_use_worker_session_identity(tmp_path) -> None:
    async def scenario() -> None:
        engine, factory = await make_session_factory(tmp_path / "subagent-policy.db")
        try:
            ticket_id, worker_session_id, worker_run_id = await _seed_ticket(factory)
            async with factory() as db:
                subagent = AgentSession(
                    model="test-model",
                    kind="subagent",
                    status="active",
                    ticket_id=ticket_id,
                    parent_session_id=worker_session_id,
                )
                db.add(subagent)
                await db.commit()
                subagent_session_id = str(subagent.id)
            subagent_run_id = await seed_run(
                factory, session_id=subagent_session_id, ticket_id=ticket_id
            )

            # Worker 会话已获批的原子操作授权；子代理复用它，不再弹确认。
            async with factory() as db:
                grant = Confirmation(
                    id="grant-1",
                    action="grant_atomic_ops",
                    risk_level=RiskLevel.NONE,
                    reason="批准原子操作",
                    payload={"session_id": worker_session_id, "window_seconds": 900},
                    status=ConfirmationStatus.APPROVED,
                    requested_by=CallerType.INTERNAL,
                    expires_at=utcnow() + timedelta(hours=1),
                )
                db.add(grant)
                await db.commit()
                worker_row = await db.get(AgentSession, worker_session_id)
                assert worker_row is not None
                worker_row.atomic_grant_id = grant.id
                worker_row.atomic_grant_expires_at = utcnow() + timedelta(minutes=15)
                await db.commit()

            registry = _registry(atomic_click=True)
            confirmations = ConfirmationService(factory, registry, policy=PolicyEngine())
            emitted: list[tuple[str, dict[str, Any]]] = []

            async def emit(name: str, data: dict[str, Any]) -> None:
                emitted.append((name, data))

            ctx = RunContext(
                run_id=subagent_run_id,
                root_run_id=worker_run_id,
                parent_run_id=worker_run_id,
                session_id=subagent_session_id,
                ticket_id=ticket_id,
                spec=builtin_spec("researcher"),
                app=SimpleNamespace(state=SimpleNamespace()),
                run=SimpleNamespace(id=subagent_run_id, kind="subagent_turn"),
                emit=emit,
            )
            middleware = PolicyMiddleware(
                confirmation_service=confirmations,
                session_factory=factory,
                poll_seconds=0.01,
            )
            await middleware.on_run_start(ctx)
            assert ctx.state["policy.session_id"] == worker_session_id

            async def unused(_call: ToolCall) -> ToolOutcome:  # pragma: no cover
                raise AssertionError("PolicyMiddleware 是链上的执行器，不应调 call_next")

            outcome = await middleware.wrap_tool(
                ctx,
                ToolCall(
                    id="call-1",
                    name="click",
                    raw_arguments='{"x": 1, "y": 2}',
                    arguments={"x": 1, "y": 2},
                ),
                unused,
            )
            assert outcome.status == "ok"
            assert outcome.result == {"clicked": [1, 2]}
            assert emitted == []

            async with factory() as db:
                audits = list(
                    (
                        await db.execute(
                            select(AgentAudit).where(AgentAudit.tool_name == "click")
                        )
                    )
                    .scalars()
                    .all()
                )
                click_confirmations = list(
                    (
                        await db.execute(
                            select(Confirmation).where(Confirmation.action == "click")
                        )
                    )
                    .scalars()
                    .all()
                )
            assert len(audits) == 1
            audit = audits[0]
            assert audit.session_id == worker_session_id
            assert audit.agent_name == "researcher"
            assert audit.run_id == subagent_run_id
            assert audit.ticket_id == ticket_id
            assert click_confirmations == []
        finally:
            engine.sync_engine.dispose()

    asyncio.run(scenario())
