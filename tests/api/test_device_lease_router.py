"""M71 设备租约端点契约（规格 §4.3、§6 的 ``test_device_lease_router.py``）。

用例按生产装配的最小集搭 app：只装 device_lease 路由、统一错误处理器与真实的
``DeviceLeaseService``（临时 SQLite 库，不 mock 仓储）。``GET`` 无租约时是
``{held: false, lease: null, waiters: 0}``，有租约时 ``DeviceLeaseOut`` 的 11 个
字段齐全且时间带 ``Z``；``DELETE`` 抢占后返回 204，持有者下一次取租约得到
``DEVICE_LEASE_PREEMPTED``，没有租约时也是 204。服务缺失时两条都返回 503
``AGENT_DISABLED``（T9 只装路由、服务装配在 T11）。
"""

from __future__ import annotations

import asyncio
from functools import partial
from types import SimpleNamespace

import pytest
from fastapi import FastAPI

from maa_api.agent.lease import DeviceLeaseService, LeaseHolder
from maa_api.api.errors import register_exception_handlers
from maa_api.api.routers import device_lease as device_lease_router
from maa_api.db.models import AgentRun, AgentSession, Ticket
from maa_api.domain.errors import AppError, ErrorCode
from maa_api.settings import LeaseSettings
from tests.fakes.v3_db import make_session_factory

TICKET = "ticket-a"
SESSION = "session-a"
RUN = "run-a"

#: ``DeviceLeaseOut`` 的 11 个字段（规格 §4.3）。
LEASE_FIELDS = {
    "device_id",
    "lease_id",
    "ticket_id",
    "ticket_title",
    "run_id",
    "holder_agent",
    "purpose",
    "explicit",
    "acquired_at",
    "expires_at",
    "heartbeat_at",
}


def _holder() -> LeaseHolder:
    return LeaseHolder(ticket_id=TICKET, run_id=RUN, agent="daily_ops")


@pytest.fixture
def lease_env(tmp_path):
    """真实租约服务 + 一个持租约所需的工单 / Worker 会话 / 根运行。"""
    engine, factory = asyncio.run(make_session_factory(tmp_path / "lease-router.db"))

    async def seed() -> None:
        async with factory() as db:
            db.add(Ticket(id=TICKET, title="日常", kind="daily_ops", origin="user"))
            await db.commit()
            db.add(
                AgentSession(
                    id=SESSION,
                    model="test-model",
                    kind="worker",
                    status="active",
                    ticket_id=TICKET,
                )
            )
            await db.commit()
            ticket = await db.get(Ticket, TICKET)
            assert ticket is not None
            ticket.worker_session_id = SESSION
            await db.commit()
            db.add(
                AgentRun(
                    id=RUN,
                    root_run_id=RUN,
                    session_id=SESSION,
                    ticket_id=TICKET,
                    agent_name="daily_ops",
                    kind="worker_turn",
                    status="running",
                    boot_id="boot-test",
                )
            )
            await db.commit()

    asyncio.run(seed())
    service = DeviceLeaseService(
        factory,
        boot_id="boot-test",
        settings_provider=lambda: SimpleNamespace(
            lease=LeaseSettings(ttl_seconds=60, wait_seconds=0)
        ),
    )
    app = FastAPI()
    register_exception_handlers(app)
    app.include_router(device_lease_router.router)
    app.state.device_lease_service = service
    yield app, service
    engine.sync_engine.dispose()


def test_get_device_lease_shape(lease_env, make_client, tmp_settings):
    app, service = lease_env
    client = make_client(app)

    empty = client.get("/api/agent/device-lease")
    assert empty.status_code == 200, empty.text
    assert empty.json() == {"held": False, "lease": None, "waiters": 0}

    client.portal.call(partial(service.acquire, _holder(), purpose="click"))
    held = client.get("/api/agent/device-lease")
    assert held.status_code == 200, held.text
    body = held.json()
    assert body["held"] is True
    assert body["waiters"] == 0
    assert set(body["lease"]) == LEASE_FIELDS
    assert body["lease"]["device_id"] == "default"
    assert body["lease"]["ticket_id"] == TICKET
    assert body["lease"]["ticket_title"] == "日常"
    assert body["lease"]["run_id"] == RUN
    assert body["lease"]["holder_agent"] == "daily_ops"
    assert body["lease"]["purpose"] == "click"
    assert body["lease"]["explicit"] is False
    for key in ("acquired_at", "expires_at", "heartbeat_at"):
        assert body["lease"][key].endswith("Z"), body["lease"][key]

    # 服务尚未装配（T9 只装路由）：两条端点都是 503 AGENT_DISABLED。
    bare = FastAPI()
    register_exception_handlers(bare)
    bare.include_router(device_lease_router.router)
    bare_client = make_client(bare)
    missing_get = bare_client.get("/api/agent/device-lease")
    assert missing_get.status_code == 503
    assert missing_get.json()["error"]["code"] == "AGENT_DISABLED"
    assert bare_client.delete("/api/agent/device-lease").status_code == 503


def test_delete_device_lease_preempts_and_returns_204(
    lease_env, make_client, tmp_settings
):
    app, service = lease_env
    client = make_client(app)
    client.portal.call(partial(service.acquire, _holder(), purpose="click"))

    released = client.delete("/api/agent/device-lease")
    assert released.status_code == 204
    assert released.content == b""

    after = client.get("/api/agent/device-lease")
    assert after.status_code == 200, after.text
    assert after.json() == {"held": False, "lease": None, "waiters": 0}

    with pytest.raises(AppError) as excinfo:
        client.portal.call(partial(service.acquire, _holder(), purpose="click"))
    assert excinfo.value.code is ErrorCode.DEVICE_LEASE_PREEMPTED
    assert excinfo.value.details == {"reason": "manual_release"}

    # 没有租约时 DELETE 同样 204（与 v2 的人工操作语义一致）。
    assert client.delete("/api/agent/device-lease").status_code == 204
