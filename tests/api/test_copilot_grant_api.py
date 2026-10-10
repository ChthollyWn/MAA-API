"""作业授权的 HTTP 面（M29，规格 §9.6、§9.10）：撤销、会话 wire、确认接口的 overrides。

三件事各一组用例：

1. ``DELETE /api/agent/sessions/{id}/copilot-grant`` → 204，授权落 ``revoked``，并广播
   与 ``atomic_grant`` 撤销一致形状的事件；
2. ``GET /api/agent/sessions/{id}`` 的 ``copilot_grant``（与 ``atomic_grant`` 并列的
   形状，前端 ``normalizeCopilotGrant`` 按它渲染授权条）；
3. ``POST /api/confirmations/{id}`` 带 ``overrides`` 批准 ``grant_copilot_attempts`` 时，
   三项覆盖写进授权记录；非法 overrides 是 422 且不落授权。
"""

from __future__ import annotations

import asyncio
from collections.abc import Iterator
from datetime import timedelta
from typing import Any

import pytest
from fastapi import FastAPI
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker
from sqlalchemy.pool import StaticPool
from sqlmodel import SQLModel

import maa_api.db.models  # noqa: F401  # 注册建表用的元数据
from maa_api.agent.confirmation import ConfirmationService
from maa_api.agent.policy import PolicyEngine
from maa_api.agent.registry import ToolRegistry
from maa_api.api.deps import get_session
from maa_api.api.errors import register_exception_handlers
from maa_api.db import session as db_session
from maa_api.db.models import AgentSession, Confirmation, utcnow
from maa_api.db.repositories.audit import ConfirmationRepository
from maa_api.db.repositories.copilot import CopilotGrantRepository
from maa_api.services.copilot.grant import CopilotGrantService
from maa_api.settings import Settings

REVOKE_PATH = "/api/agent/sessions/{session_id}/copilot-grant"


@pytest.fixture
def grant_app(
    tmp_settings: Settings,
) -> Iterator[tuple[FastAPI, async_sessionmaker[AsyncSession], list[tuple[str, Any]]]]:
    """路由 app + 临时库 + 抓取广播事件的列表。"""
    engine = db_session.make_engine(
        "sqlite+aiosqlite:///:memory:", poolclass=StaticPool
    )
    factory = async_sessionmaker(engine, expire_on_commit=False, class_=AsyncSession)
    events: list[tuple[str, Any]] = []

    async def prepare() -> None:
        async with engine.begin() as connection:
            await connection.run_sync(SQLModel.metadata.create_all)

    asyncio.run(prepare())

    confirmation_service = ConfirmationService(
        factory,
        ToolRegistry(),
        policy=PolicyEngine(),
        broadcast=lambda message_type, data: events.append((message_type, data)),
    )
    from maa_api.main import request_trace_headers
    from maa_api.api.routers import agent, confirmations

    application = FastAPI()
    register_exception_handlers(application)
    application.middleware("http")(request_trace_headers)
    application.include_router(agent.router)
    application.include_router(confirmations.router)
    application.state.confirmation_service = confirmation_service
    application.state.session_factory = factory
    application.state.copilot_grant_service = CopilotGrantService(factory)

    async def get_test_session():
        async with factory() as session:
            yield session

    application.dependency_overrides[get_session] = get_test_session
    try:
        yield application, factory, events
    finally:
        engine.sync_engine.dispose()


@pytest.fixture
def client(grant_app, make_client) -> Any:
    return make_client(grant_app[0])


def _seed_session(factory: async_sessionmaker[AsyncSession], title: str = "作业研究") -> str:
    async def scenario() -> str:
        async with factory() as session:
            created = await session.merge(
                AgentSession(title=title, model="deepseek-flash")
            )
            await session.commit()
            return str(created.id)

    return asyncio.run(scenario())


def _seed_confirmation(
    factory: async_sessionmaker[AsyncSession],
    *,
    session_id: str,
    payload: dict[str, Any] | None = None,
    action: str = "grant_copilot_attempts",
) -> str:
    """写一条 ``pending`` 的作业授权确认（payload 按规格 §9.6 的预填形状）。"""
    body = {
        "session_id": session_id,
        "stage_id": "main_01-07",
        "stage_code": "1-7",
        "attempts": 3,
        "success": "three_star",
        "support": "fill_one",
        "window_minutes": 60,
        "tool_name": "run_copilot_attempt",
        "target_kind": "copilot",
    }
    body.update(payload or {})

    async def scenario() -> str:
        async with factory() as session:
            created = await ConfirmationRepository(session).create(
                Confirmation(
                    action=action,
                    risk_level="consume",
                    reason="执行作业尝试需要授权",
                    payload=body,
                    requested_by="internal",
                    expires_at=utcnow() + timedelta(minutes=10),
                )
            )
            await session.commit()
            return str(created.id)

    return asyncio.run(scenario())


def _active_grant(factory: async_sessionmaker[AsyncSession], session_id: str) -> dict[str, Any] | None:
    async def scenario() -> dict[str, Any] | None:
        async with factory() as session:
            row = await CopilotGrantRepository(session).active_for_session(session_id)
            if row is None:
                return None
            return {
                "id": row.id,
                "stage_id": row.stage_id,
                "stage_code": row.stage_code,
                "status": row.status,
                "attempts": row.attempts,
                "used": row.used,
                "success": row.success,
                "support": row.support,
            }

    return asyncio.run(scenario())


def _grant_status(factory: async_sessionmaker[AsyncSession], grant_id: str) -> str | None:
    async def scenario() -> str | None:
        async with factory() as session:
            row = await CopilotGrantRepository(session).get(grant_id)
            return None if row is None else str(row.status)

    return asyncio.run(scenario())


# ---------------------------------------------------------------------------
# 1. DELETE /api/agent/sessions/{session_id}/copilot-grant
# ---------------------------------------------------------------------------


def test_revoke_copilot_grant_returns_204_and_broadcasts(grant_app, client) -> None:
    factory = grant_app[1]
    events = grant_app[2]
    session_id = _seed_session(factory)
    confirmation_id = _seed_confirmation(factory, session_id=session_id)
    approved = client.post(
        f"/api/confirmations/{confirmation_id}", json={"approved": True}
    )
    assert approved.status_code == 200, approved.text
    assert _active_grant(factory, session_id) is not None
    before = len(events)

    response = client.delete(REVOKE_PATH.format(session_id=session_id))

    assert response.status_code == 204
    assert response.content == b""
    assert _active_grant(factory, session_id) is None
    assert _grant_status(factory, confirmation_id) == "revoked"
    broadcast = [
        data
        for kind, data in events[before:]
        if kind == "agent_event" and data.get("event") == "copilot_grant_changed"
    ]
    assert broadcast, events[before:]
    assert broadcast[0]["session_id"] == session_id
    assert broadcast[0]["payload"] == {
        "granted": False,
        "grant_id": None,
        "expires_at": None,
        "status": "revoked",
    }
    # 撤销授权不等于删除会话。
    assert client.get(f"/api/agent/sessions/{session_id}").status_code == 200


def test_revoke_is_idempotent_without_active_grant(grant_app, client) -> None:
    factory = grant_app[1]
    session_id = _seed_session(factory)

    assert client.delete(REVOKE_PATH.format(session_id=session_id)).status_code == 204
    assert client.delete(REVOKE_PATH.format(session_id=session_id)).status_code == 204


def test_revoke_unknown_session_is_404(grant_app, client) -> None:
    response = client.delete(REVOKE_PATH.format(session_id="0" * 32))

    assert response.status_code == 404
    assert response.json()["error"]["code"] == "AGENT_SESSION_NOT_FOUND"


# ---------------------------------------------------------------------------
# 2. 会话 wire 的 copilot_grant
# ---------------------------------------------------------------------------


def test_session_detail_exposes_copilot_grant_beside_atomic_grant(grant_app, client) -> None:
    factory = grant_app[1]
    session_id = _seed_session(factory)

    empty = client.get(f"/api/agent/sessions/{session_id}")
    assert empty.status_code == 200, empty.text
    assert empty.json()["copilot_grant"] is None
    assert empty.json()["atomic_grant"] is None

    confirmation_id = _seed_confirmation(factory, session_id=session_id)
    assert (
        client.post(
            f"/api/confirmations/{confirmation_id}", json={"approved": True}
        ).status_code
        == 200
    )

    detail = client.get(f"/api/agent/sessions/{session_id}")
    assert detail.status_code == 200, detail.text
    grant = detail.json()["copilot_grant"]
    assert grant == {
        "grant_id": confirmation_id,
        "stage_id": "main_01-07",
        "stage_code": "1-7",
        "attempts_total": 3,
        "attempts_used": 0,
        "remaining_attempts": 3,
        "success": "three_star",
        "support": "fill_one",
        "status": "active",
        "expires_at": grant["expires_at"],
    }
    assert grant["expires_at"] and grant["expires_at"].endswith("Z")
    # 与 atomic_grant 并列，互不影响。
    assert detail.json()["atomic_grant"] is None


def test_session_detail_drops_copilot_grant_after_revoke(grant_app, client) -> None:
    factory = grant_app[1]
    session_id = _seed_session(factory)
    confirmation_id = _seed_confirmation(factory, session_id=session_id)
    client.post(f"/api/confirmations/{confirmation_id}", json={"approved": True})
    assert client.get(f"/api/agent/sessions/{session_id}").json()["copilot_grant"] is not None

    assert client.delete(REVOKE_PATH.format(session_id=session_id)).status_code == 204

    assert client.get(f"/api/agent/sessions/{session_id}").json()["copilot_grant"] is None


# ---------------------------------------------------------------------------
# 3. 确认接口的 overrides
# ---------------------------------------------------------------------------


def test_approval_with_overrides_writes_them_into_the_grant(grant_app, client) -> None:
    factory = grant_app[1]
    session_id = _seed_session(factory)
    confirmation_id = _seed_confirmation(
        factory, session_id=session_id, payload={"attempts": 8, "success": "clear"}
    )

    response = client.post(
        f"/api/confirmations/{confirmation_id}",
        json={
            "approved": True,
            "overrides": {"attempts": 5, "success": "clear", "support": "none"},
        },
    )

    assert response.status_code == 200, response.text
    assert response.json()["status"] == "approved"
    grant = _active_grant(factory, session_id)
    assert grant is not None
    assert grant["id"] == confirmation_id  # 附录 E：授权 id = 确认 id
    assert (grant["attempts"], grant["success"], grant["support"]) == (
        5,
        "clear",
        "none",
    )
    assert grant["used"] == 0
    assert grant["stage_id"] == "main_01-07"
    assert grant["stage_code"] == "1-7"


def test_approval_without_overrides_keeps_the_prefilled_values(grant_app, client) -> None:
    factory = grant_app[1]
    session_id = _seed_session(factory)
    confirmation_id = _seed_confirmation(
        factory,
        session_id=session_id,
        payload={"attempts": 7, "success": "clear", "support": "none"},
    )

    response = client.post(
        f"/api/confirmations/{confirmation_id}", json={"approved": True}
    )

    assert response.status_code == 200, response.text
    grant = _active_grant(factory, session_id)
    assert grant is not None
    assert (grant["attempts"], grant["success"], grant["support"]) == (
        7,
        "clear",
        "none",
    )


def test_partial_overrides_keep_the_rest(grant_app, client) -> None:
    factory = grant_app[1]
    session_id = _seed_session(factory)
    confirmation_id = _seed_confirmation(factory, session_id=session_id)

    response = client.post(
        f"/api/confirmations/{confirmation_id}",
        json={"approved": True, "overrides": {"attempts": 10}},
    )

    assert response.status_code == 200, response.text
    grant = _active_grant(factory, session_id)
    assert grant is not None
    assert (grant["attempts"], grant["success"], grant["support"]) == (
        10,
        "three_star",
        "fill_one",
    )


@pytest.mark.parametrize(
    "overrides",
    [
        {"attempts": 0},
        {"attempts": 11},
        {"attempts": "3"},
        {"success": "two_star"},
        {"support": "fill_two"},
        {"retain": True},
    ],
)
def test_invalid_overrides_are_422_and_write_no_grant(grant_app, client, overrides) -> None:
    factory = grant_app[1]
    session_id = _seed_session(factory)
    confirmation_id = _seed_confirmation(factory, session_id=session_id)

    response = client.post(
        f"/api/confirmations/{confirmation_id}",
        json={"approved": True, "overrides": overrides},
    )

    assert response.status_code == 422, response.text
    assert response.json()["error"]["code"] == "VALIDATION_ERROR"
    assert _active_grant(factory, session_id) is None
    # 请求没被接受，确认仍是待处理，用户可以改完再批。
    assert client.get(f"/api/confirmations/{confirmation_id}").json()["status"] == "pending"


def test_approval_reads_stage_from_tool_arguments_when_payload_omits_it(grant_app, client) -> None:
    """预填缺 ``stage_id`` / ``stage_code`` 时退回工具参数（REST 直接调工具的形态）。"""
    factory = grant_app[1]
    session_id = _seed_session(factory)
    confirmation_id = _seed_confirmation(
        factory,
        session_id=session_id,
        payload={"stage_id": None, "stage_code": None},
    )
    # payload 里显式写 null 就等于「没给」，arguments 兜底。
    async def attach_arguments() -> None:
        async with factory() as session:
            row = await ConfirmationRepository(session).get(confirmation_id)
            assert row is not None
            row.payload = {
                **row.payload,
                "arguments": {
                    "session_id": session_id,
                    "stage_id": "main_01-07",
                    "stage_code": "1-7",
                },
            }
            await session.merge(row)
            await session.commit()

    asyncio.run(attach_arguments())

    response = client.post(
        f"/api/confirmations/{confirmation_id}",
        json={"approved": True, "overrides": {"attempts": 2}},
    )

    assert response.status_code == 200, response.text
    grant = _active_grant(factory, session_id)
    assert grant is not None
    assert (grant["stage_id"], grant["stage_code"], grant["attempts"]) == (
        "main_01-07",
        "1-7",
        2,
    )


def test_approval_without_any_stage_is_400_and_creates_no_grant(grant_app, client) -> None:
    """关卡缺失时不能悄悄批准：报 400，不写出没有关卡的授权。"""
    factory = grant_app[1]
    session_id = _seed_session(factory)
    confirmation_id = _seed_confirmation(
        factory, session_id=session_id, payload={"stage_id": None, "stage_code": None}
    )

    response = client.post(
        f"/api/confirmations/{confirmation_id}", json={"approved": True}
    )

    assert response.status_code == 400, response.text
    assert response.json()["error"]["code"] == "INVALID_PARAMETER"
    assert _active_grant(factory, session_id) is None


def test_rejection_creates_no_grant(grant_app, client) -> None:
    factory = grant_app[1]
    session_id = _seed_session(factory)
    confirmation_id = _seed_confirmation(factory, session_id=session_id)

    response = client.post(
        f"/api/confirmations/{confirmation_id}",
        json={"approved": False, "reason": "不打了"},
    )

    assert response.status_code == 200, response.text
    assert response.json()["status"] == "rejected"
    assert _active_grant(factory, session_id) is None


def test_other_actions_ignore_overrides(grant_app, client) -> None:
    """``overrides`` 只对 ``grant_copilot_attempts`` 生效，别的确认动作照旧。"""
    factory = grant_app[1]
    session_id = _seed_session(factory)
    confirmation_id = _seed_confirmation(
        factory, session_id=session_id, action="set_task_params"
    )

    response = client.post(
        f"/api/confirmations/{confirmation_id}",
        json={"approved": True, "overrides": {"attempts": 4}},
    )

    assert response.status_code == 200, response.text
    assert response.json()["status"] == "approved"
    assert _active_grant(factory, session_id) is None
