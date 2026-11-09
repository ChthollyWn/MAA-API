"""M87 T4：作业授权的轮次上限与新一轮（spec §4.3、§4.5.5、§6）。

策略只在「没有有效授权」时按 ``copilot_loop.max_iterations`` 夹住预填次数并在
确认载荷里写 ``max_attempts``；工单 Worker 会话在次数用完时把旧授权落 ``exhausted``
并弹新卡，v2 聊天会话仍抛 ``COPILOT_GRANT_EXHAUSTED``。临时库与 ``ToolContext``
照 ``tests/agent/test_confirmation_guard.py`` 的写法。
"""

from __future__ import annotations

import asyncio
from types import SimpleNamespace
from typing import Any

import pytest
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker
from sqlalchemy.pool import StaticPool
from sqlmodel import SQLModel

import maa_api.db.models  # noqa: F401
from maa_api.agent.confirmation import ConfirmationService
from maa_api.agent.policy import PolicyEngine
from maa_api.agent.registry import ToolContext
from maa_api.agent.tools import build_registry
from maa_api.db import session as db_session
from maa_api.db.models import AgentSession, CopilotGrant, ResourceAsset, Ticket
from maa_api.domain.enums import AgentSessionStatus, CallerType, TicketStatus
from maa_api.domain.errors import AppError, ErrorCode
from maa_api.services.copilot.grant import CopilotGrantService
from maa_api.settings import CopilotLoopSettings, Settings, set_settings

TICKET_ID = "ticket-m87"
TICKET_SESSION_ID = "session-m87-ticket"
CHAT_SESSION_ID = "session-m87-chat"
STAGE_ID = "main_01-07"
STAGE_CODE = "1-7"


def _database():
    engine = db_session.make_engine(
        "sqlite+aiosqlite:///:memory:", poolclass=StaticPool
    )
    factory = async_sessionmaker(engine, expire_on_commit=False, class_=AsyncSession)
    return engine, factory


def _request():
    return SimpleNamespace(app=SimpleNamespace(state=SimpleNamespace()))


def _internal_context(
    session: Any = None, *, session_id: str | None = None
) -> ToolContext:
    return ToolContext(
        CallerType.INTERNAL,
        session_id,
        "request-m87",
        _request(),
        session,
    )


async def _seed_ticket_session(
    factory,
    *,
    session_id: str = TICKET_SESSION_ID,
    ticket_id: str = TICKET_ID,
) -> None:
    """工单 + 它的 Worker 会话；``_ticket_worker_session`` 需要 ``ticket_id`` 非空。"""
    async with factory() as db:
        db.add(
            Ticket(
                id=ticket_id,
                title="日常",
                kind="copilot",
                status=TicketStatus.OPEN.value,
                origin="user",
            )
        )
        await db.flush()
        db.add(
            AgentSession(
                id=session_id,
                model="model-test",
                kind="worker",
                status=AgentSessionStatus.ACTIVE,
                ticket_id=ticket_id,
            )
        )
        await db.commit()


async def _seed_chat_session(factory, *, session_id: str = CHAT_SESSION_ID) -> None:
    """v2 聊天会话：有会话行，但没有 ``ticket_id``。"""
    async with factory() as db:
        db.add(
            AgentSession(
                id=session_id,
                model="model-test",
                kind="chat",
                status=AgentSessionStatus.ACTIVE,
            )
        )
        await db.commit()


async def _invoke(
    service: ConfirmationService,
    factory,
    *,
    session_id: str,
    arguments: dict[str, Any],
) -> dict[str, Any]:
    async with factory() as policy_session:
        return await service.invoke(
            "run_copilot_attempt",
            arguments,
            _internal_context(policy_session, session_id=session_id),
            mode="async",
        )


async def _reject(service: ConfirmationService, accepted: dict[str, Any]) -> None:
    confirmation_id = accepted["confirmation_id"]
    worker = service._workers.get(confirmation_id)
    await service.resolve(confirmation_id, approved=False, resolved_by="test")
    if worker is not None:
        await worker


async def _seed_copilot(factory) -> None:
    """``_copilot_stage`` 从作业资源读关卡；批准授权时关卡不能为空。"""
    async with factory() as db:
        db.add(
            ResourceAsset(
                id="cp-m87",
                kind="copilot",
                name="1-7 三星作业",
                content={"actions": []},
                meta={"stage_id": STAGE_ID, "stage_code": STAGE_CODE},
            )
        )
        await db.commit()


async def _grant_row(factory, grant_id: str) -> CopilotGrant | None:
    async with factory() as db:
        return await db.get(CopilotGrant, grant_id)


async def _exhausted_grant(
    factory,
    *,
    session_id: str,
    attempts: int = 1,
) -> str:
    """批准一份授权并消耗完；返回 grant id（旧行仍 ``active``、``remaining==0``）。"""
    service = CopilotGrantService(factory)
    grant = await service.approve(
        session_id,
        stage_id=STAGE_ID,
        stage_code=STAGE_CODE,
        attempts=attempts,
    )
    consumed = await service.consume(session_id, STAGE_ID, stage_code=STAGE_CODE)
    assert consumed["remaining"] == 0
    return str(grant["id"])


def test_ticket_session_prefill_is_clamped() -> None:
    async def scenario() -> None:
        engine, factory = _database()
        async with engine.begin() as connection:
            await connection.run_sync(SQLModel.metadata.create_all)
        try:
            await _seed_ticket_session(factory)
            service = ConfirmationService(
                factory, build_registry(), policy=PolicyEngine()
            )
            accepted = await _invoke(
                service,
                factory,
                session_id=TICKET_SESSION_ID,
                arguments={"copilot_id": "cp-m87", "grant_attempts": 5},
            )

            assert accepted["status"] == "awaiting_confirmation"
            detail = await service.get(accepted["confirmation_id"])
            assert detail["payload"]["attempts"] == 3
            assert detail["payload"]["max_attempts"] == 3
            await _reject(service, accepted)
        finally:
            await engine.dispose()

    asyncio.run(scenario())


def test_chat_session_keeps_v2_prefill() -> None:
    async def scenario() -> None:
        engine, factory = _database()
        async with engine.begin() as connection:
            await connection.run_sync(SQLModel.metadata.create_all)
        try:
            await _seed_chat_session(factory)
            service = ConfirmationService(
                factory, build_registry(), policy=PolicyEngine()
            )
            accepted = await _invoke(
                service,
                factory,
                session_id=CHAT_SESSION_ID,
                arguments={"copilot_id": "cp-m87", "grant_attempts": 5},
            )

            assert accepted["status"] == "awaiting_confirmation"
            detail = await service.get(accepted["confirmation_id"])
            assert detail["payload"]["attempts"] == 5
            assert "max_attempts" not in detail["payload"]
            await _reject(service, accepted)
        finally:
            await engine.dispose()

    asyncio.run(scenario())


def test_approval_clamps_override_above_max() -> None:
    async def scenario() -> None:
        engine, factory = _database()
        async with engine.begin() as connection:
            await connection.run_sync(SQLModel.metadata.create_all)
        try:
            await _seed_ticket_session(factory)
            await _seed_copilot(factory)
            service = ConfirmationService(
                factory, build_registry(), policy=PolicyEngine()
            )
            accepted = await _invoke(
                service,
                factory,
                session_id=TICKET_SESSION_ID,
                arguments={"copilot_id": "cp-m87", "grant_attempts": 5},
            )
            await service.resolve(
                accepted["confirmation_id"],
                approved=True,
                resolved_by="web",
                request=_request(),
                overrides={"attempts": 8},
            )
            worker = service._workers.get(accepted["confirmation_id"])
            if worker is not None:
                await worker

            row = await _grant_row(factory, accepted["confirmation_id"])
            assert row is not None
            assert row.attempts == 3
        finally:
            await engine.dispose()

    asyncio.run(scenario())


def test_max_iterations_setting_is_hot() -> None:
    async def scenario() -> None:
        engine, factory = _database()
        async with engine.begin() as connection:
            await connection.run_sync(SQLModel.metadata.create_all)
        try:
            await _seed_ticket_session(
                factory, session_id="session-hot-a", ticket_id="ticket-hot-a"
            )
            await _seed_ticket_session(
                factory, session_id="session-hot-b", ticket_id="ticket-hot-b"
            )
            service = ConfirmationService(
                factory, build_registry(), policy=PolicyEngine()
            )
            set_settings(
                Settings(copilot_loop=CopilotLoopSettings(max_iterations=2))
            )
            first = await _invoke(
                service,
                factory,
                session_id="session-hot-a",
                arguments={"copilot_id": "cp-m87"},
            )
            first_detail = await service.get(first["confirmation_id"])
            assert first_detail["payload"]["max_attempts"] == 2
            assert first_detail["payload"]["attempts"] == 2
            await _reject(service, first)

            set_settings(
                Settings(copilot_loop=CopilotLoopSettings(max_iterations=5))
            )
            second = await _invoke(
                service,
                factory,
                session_id="session-hot-b",
                arguments={"copilot_id": "cp-m87"},
            )
            second_detail = await service.get(second["confirmation_id"])
            assert second_detail["payload"]["max_attempts"] == 5
            assert second_detail["payload"]["attempts"] == 3
            await _reject(service, second)
        finally:
            set_settings(None)
            await engine.dispose()

    asyncio.run(scenario())


def test_ticket_session_exhausted_grant_opens_new_card() -> None:
    async def scenario() -> None:
        engine, factory = _database()
        async with engine.begin() as connection:
            await connection.run_sync(SQLModel.metadata.create_all)
        try:
            await _seed_ticket_session(factory)
            grant_id = await _exhausted_grant(factory, session_id=TICKET_SESSION_ID)
            service = ConfirmationService(
                factory, build_registry(), policy=PolicyEngine()
            )

            accepted = await _invoke(
                service,
                factory,
                session_id=TICKET_SESSION_ID,
                arguments={"copilot_id": "cp-m87"},
            )

            assert accepted["status"] == "awaiting_confirmation"
            old = await _grant_row(factory, grant_id)
            assert old is not None
            assert str(old.status) == "exhausted"
            detail = await service.get(accepted["confirmation_id"])
            assert detail["payload"]["max_attempts"] == 3
            assert detail["action"] == "grant_copilot_attempts"
            await _reject(service, accepted)
        finally:
            await engine.dispose()

    asyncio.run(scenario())


def test_chat_session_exhausted_grant_still_raises() -> None:
    async def scenario() -> None:
        engine, factory = _database()
        async with engine.begin() as connection:
            await connection.run_sync(SQLModel.metadata.create_all)
        try:
            await _seed_chat_session(factory)
            grant_id = await _exhausted_grant(factory, session_id=CHAT_SESSION_ID)
            service = ConfirmationService(
                factory, build_registry(), policy=PolicyEngine()
            )

            with pytest.raises(AppError) as info:
                await _invoke(
                    service,
                    factory,
                    session_id=CHAT_SESSION_ID,
                    arguments={"copilot_id": "cp-m87"},
                )
            assert info.value.code == ErrorCode.COPILOT_GRANT_EXHAUSTED

            old = await _grant_row(factory, grant_id)
            assert old is not None
            assert str(old.status) == "active"
        finally:
            await engine.dispose()

    asyncio.run(scenario())
