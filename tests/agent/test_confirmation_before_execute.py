"""M71 T5：确认的执行前回调（规格 §4.2 第 1–3 条、§6 的 ``test_confirmation_before_execute.py``）。

异步用例一律「同步测试函数 + ``asyncio.run(scenario())``」。租约用真实的
``DeviceLeaseService`` 与临时库；确认服务用真实实现与一个 DANGEROUS 的假工具。
"""

from __future__ import annotations

import asyncio
from types import SimpleNamespace
from typing import Any

import pytest
from pydantic import BaseModel, ConfigDict
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from maa_api.agent.confirmation import ConfirmationService
from maa_api.agent.lease import DeviceLeaseService, LeaseHolder
from maa_api.agent.policy import PolicyEngine
from maa_api.agent.registry import (
    ToolContext,
    ToolDefinition,
    ToolOrigin,
    ToolRegistry,
    ToolRisk,
)
from maa_api.db.models import AgentRun, AgentSession, Ticket
from maa_api.db.repositories.audit import AuditRepository
from maa_api.domain.enums import AuditStatus, CallerType
from maa_api.domain.errors import AppError, ErrorCode
from maa_api.settings import LeaseSettings
from tests.fakes.v3_db import make_session_factory

TICKET_A = "ticket-a"
TICKET_B = "ticket-b"
WORKER_A = "session-a"
WORKER_B = "session-b"
RUN_A = "run-a"
RUN_B = "run-b"
BOOT_ID = "boot-test"


class _Params(BaseModel):
    model_config = ConfigDict(extra="forbid")


def _request():
    return SimpleNamespace(app=SimpleNamespace(state=SimpleNamespace()))


class _HandlerLog:
    """工具 handler 的调用记录：参数、执行时看到的租约与执行上下文。"""

    def __init__(self) -> None:
        self.contexts: list[ToolContext] = []
        self.lease_ticket: list[str | None] = []
        self.calls = 0


def _registry(
    handler_log: _HandlerLog,
    lease: DeviceLeaseService | None = None,
    *,
    risk: ToolRisk = ToolRisk.DANGEROUS,
    device: str = "exclusive",
) -> ToolRegistry:
    async def handler(_params, ctx: ToolContext) -> dict[str, Any]:
        handler_log.calls += 1
        handler_log.contexts.append(ctx)
        if lease is not None:
            row = await lease.current()
            handler_log.lease_ticket.append(row.ticket_id if row is not None else None)
        return {"ok": True}

    registry = ToolRegistry()
    registry.register(
        ToolDefinition(
            name="fake_write" if device == "exclusive" else "fake_read",
            group="test",
            risk=risk,
            description="测试工具",
            params_model=_Params,
            handler=handler,
            device=device,  # type: ignore[arg-type]
            rest_visible=False,
        )
    )
    return registry


async def _seed_ticket(
    factory: async_sessionmaker[AsyncSession],
    *,
    ticket_id: str,
    session_id: str,
    run_id: str,
) -> None:
    """工单 → Worker 会话 → 回填 worker_session_id → 根运行（循环外键分步插入）。"""
    async with factory() as db:
        db.add(Ticket(id=ticket_id, title=f"工单 {ticket_id}", kind="general", origin="user"))
        await db.commit()
        db.add(
            AgentSession(
                id=session_id,
                model="test-model",
                kind="worker",
                status="active",
                ticket_id=ticket_id,
            )
        )
        await db.commit()
        ticket = await db.get(Ticket, ticket_id)
        assert ticket is not None
        ticket.worker_session_id = session_id
        await db.commit()
        db.add(
            AgentRun(
                id=run_id,
                root_run_id=run_id,
                session_id=session_id,
                ticket_id=ticket_id,
                agent_name="general",
                kind="worker_turn",
                status="running",
                boot_id=BOOT_ID,
            )
        )
        await db.commit()


def _lease(factory: async_sessionmaker[AsyncSession]) -> DeviceLeaseService:
    return DeviceLeaseService(
        factory,
        boot_id=BOOT_ID,
        settings_provider=lambda: SimpleNamespace(
            lease=LeaseSettings(ttl_seconds=60, wait_seconds=0)
        ),
        poll_seconds=0.02,
    )


def _tool_context(*, run_id: str, ticket_id: str, session_id: str) -> ToolContext:
    return ToolContext(
        caller=CallerType.INTERNAL,
        session_id=session_id,
        request_id=None,
        request=_request(),
        db_session=None,
        origin=ToolOrigin(run_id=run_id, ticket_id=ticket_id, agent="general"),
    )


def test_waiting_for_confirmation_does_not_hold_lease(tmp_path) -> None:
    async def scenario() -> None:
        engine, factory = await make_session_factory(tmp_path / "before-lease.db")
        try:
            await _seed_ticket(
                factory, ticket_id=TICKET_A, session_id=WORKER_A, run_id=RUN_A
            )
            await _seed_ticket(
                factory, ticket_id=TICKET_B, session_id=WORKER_B, run_id=RUN_B
            )
            lease = _lease(factory)
            handler_log = _HandlerLog()
            service = ConfirmationService(
                factory, _registry(handler_log, lease), policy=PolicyEngine()
            )
            holder_a = LeaseHolder(TICKET_A, RUN_A, "general")
            holder_b = LeaseHolder(TICKET_B, RUN_B, "general")
            context = _tool_context(
                run_id=RUN_A, ticket_id=TICKET_A, session_id=WORKER_A
            )

            async def take_lease(_audit_id: int) -> None:
                await lease.acquire(holder_a, purpose="fake_write")

            accepted = await service.invoke(
                "fake_write",
                {},
                context,
                mode="async",
                on_before_execute=take_lease,
            )
            assert accepted["status"] == "awaiting_confirmation"
            # 等确认期间不占设备：另一个工单能取到并释放。
            assert await lease.current() is None
            other = await lease.acquire(holder_b, purpose="click")
            assert other.ticket_id == TICKET_B
            await lease.release(holder_b)
            assert await lease.current() is None

            await service.resolve(
                accepted["confirmation_id"],
                approved=True,
                resolved_by="web",
                request=_request(),
            )
            await service._workers[accepted["confirmation_id"]]

            assert handler_log.calls == 1
            # 批准后、执行前才取：handler 执行时租约属于本工单。
            assert handler_log.lease_ticket == [TICKET_A]
            held = await lease.current()
            assert held is not None
            assert held.ticket_id == TICKET_A
            assert held.run_id == RUN_A
            assert held.explicit is False

            await lease.release_run(RUN_A)
            assert await lease.current() is None
        finally:
            await engine.dispose()

    asyncio.run(scenario())


def test_before_execute_error_fails_audit_without_running_handler(tmp_path) -> None:
    async def scenario() -> None:
        engine, factory = await make_session_factory(tmp_path / "before-error.db")
        try:
            handler_log = _HandlerLog()
            service = ConfirmationService(
                factory, _registry(handler_log, risk=ToolRisk.SAFE, device="none")
            )
            context = ToolContext(
                CallerType.REST, None, "request-1", _request(), None
            )

            async def busy(_audit_id: int) -> None:
                raise AppError(
                    ErrorCode.DEVICE_LEASE_BUSY, "设备正被其他工单使用，稍后再试"
                )

            with pytest.raises(AppError) as error:
                await service.invoke(
                    "fake_read",
                    {},
                    context,
                    mode="sync",
                    on_before_execute=busy,
                )
            assert error.value.code == ErrorCode.DEVICE_LEASE_BUSY
            assert handler_log.calls == 0

            audit_id = int((error.value.details or {})["audit_id"])
            async with factory() as db:
                audit = await AuditRepository(db).get(audit_id)
            assert audit is not None
            assert audit.status == AuditStatus.FAILED
            assert audit.error_code == ErrorCode.DEVICE_LEASE_BUSY.value
        finally:
            await engine.dispose()

    asyncio.run(scenario())


def test_before_execute_runs_for_unconfirmed_calls_too(tmp_path) -> None:
    async def scenario() -> None:
        engine, factory = await make_session_factory(tmp_path / "before-sync.db")
        try:
            order: list[str] = []
            handler_log = _HandlerLog()
            service = ConfirmationService(
                factory, _registry(handler_log, risk=ToolRisk.SAFE, device="none")
            )
            context = ToolContext(CallerType.REST, None, "request-1", _request(), None)
            seen_audits: list[int] = []

            async def hook(audit_id: int) -> None:
                order.append("hook")
                seen_audits.append(audit_id)

            result = await service.invoke(
                "fake_read",
                {},
                context,
                mode="sync",
                on_before_execute=hook,
            )

            assert result["status"] == "success"
            assert handler_log.calls == 1
            assert order == ["hook"]
            assert seen_audits == [result["audit_id"]]
        finally:
            await engine.dispose()

    asyncio.run(scenario())


def test_execution_context_keeps_origin(tmp_path) -> None:
    async def scenario() -> None:
        engine, factory = await make_session_factory(tmp_path / "before-origin.db")
        try:
            await _seed_ticket(
                factory, ticket_id=TICKET_A, session_id=WORKER_A, run_id=RUN_A
            )
            handler_log = _HandlerLog()
            service = ConfirmationService(
                factory, _registry(handler_log, risk=ToolRisk.SAFE, device="none")
            )
            origin = ToolOrigin(run_id=RUN_A, ticket_id=TICKET_A, agent="general")
            context = ToolContext(
                caller=CallerType.INTERNAL,
                session_id=WORKER_A,
                request_id="request-1",
                request=_request(),
                db_session=None,
                origin=origin,
            )

            async def hook(_audit_id: int) -> None:
                return None

            await service.invoke(
                "fake_read", {}, context, mode="sync", on_before_execute=hook
            )

            assert len(handler_log.contexts) == 1
            execution_context = handler_log.contexts[0]
            # 执行期重建的上下文只换 db_session，origin 原样保留（DATACLASSES.replace）。
            assert execution_context.origin == origin
            assert execution_context.db_session is not None
        finally:
            await engine.dispose()

    asyncio.run(scenario())
