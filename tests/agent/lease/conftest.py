"""``tests/agent/lease`` 的共享 fixture（M71 规格 §4.2、§4.5.1–§4.5.3、§6）。

``lease_env`` 返回 ``(factory, service, events, published)``：临时 SQLite 库的
会话工厂、注入了可控时钟与 ``poll_seconds=0.02`` 的 ``DeviceLeaseService``、记录
WS 广播的 ``events``（``[(message_type, data)]``）与假 ``ThreadRouter`` 记下的
``published``。库里预置两个工单，各带 Worker 会话与根运行，工单 A 另带一个子代理
会话（供重入与领取闸门用例）。

``lease_world`` 提供时钟、持有人与各类 id；用例要推时间就改 ``clock``，不 sleep
真实的租约时长。异步用例一律「同步测试函数 + ``asyncio.run(scenario())``」。
"""

from __future__ import annotations

import asyncio
from dataclasses import dataclass
from datetime import datetime, timedelta
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import pytest
from sqlalchemy.ext.asyncio import AsyncEngine, AsyncSession, async_sessionmaker

from maa_api.agent.lease import DeviceLeaseService, LeaseHolder
from maa_api.db.models import AgentRun, AgentSession, Ticket
from maa_api.settings import LeaseSettings
from tests.fakes.v3_db import make_session_factory

TICKET_A = "ticket-a"
TICKET_B = "ticket-b"
WORKER_A = "session-a"
WORKER_B = "session-b"
SUBAGENT_A = "session-a-sub"
RUN_A = "run-a"
RUN_B = "run-b"

BOOT_ID = "boot-test"
#: 测试把 TTL 压到最短合法值 60 秒，过期靠推时钟制造。
TTL_SECONDS = 60
START = datetime(2026, 1, 1, 12, 0, 0)


class MutableClock:
    """可控时钟：``advance`` 推时间，测试不付出真实等待。"""

    def __init__(self, now: datetime) -> None:
        self.now = now

    def __call__(self) -> datetime:
        return self.now

    def advance(self, seconds: float) -> datetime:
        self.now = self.now + timedelta(seconds=seconds)
        return self.now


class RecordingThreadRouter:
    """只记下 ``publish`` 的参数；抢占的收件箱语义由断言检查，不落库。"""

    def __init__(self, published: list[dict[str, Any]]) -> None:
        self.published = published

    async def publish(
        self,
        thread_key: str,
        *,
        kind: Any,
        ref_id: str | None = None,
        payload: dict[str, Any] | None = None,
    ) -> None:
        self.published.append(
            {
                "thread_key": thread_key,
                "kind": kind,
                "ref_id": ref_id,
                "payload": dict(payload or {}),
            }
        )


@dataclass(frozen=True)
class LeaseWorld:
    """用例共用的时钟、持有人与 id；与 ``lease_env`` 分开以保留四元组形状。"""

    clock: MutableClock
    settings: LeaseSettings
    holders: dict[str, LeaseHolder]
    wake_calls: list[str]
    ticket_ids: dict[str, str]
    worker_sessions: dict[str, str]
    subagent_sessions: dict[str, str]
    run_ids: dict[str, str]


@pytest.fixture
def lease_world() -> LeaseWorld:
    """两个工单的持有人（A、B）与工单 A 的子代理持有人。"""
    return LeaseWorld(
        clock=MutableClock(START),
        settings=LeaseSettings(ttl_seconds=TTL_SECONDS, wait_seconds=5),
        holders={
            "a": LeaseHolder(ticket_id=TICKET_A, run_id=RUN_A, agent="daily_ops"),
            "b": LeaseHolder(ticket_id=TICKET_B, run_id=RUN_B, agent="daily_ops"),
            "subagent_a": LeaseHolder(ticket_id=TICKET_A, run_id=RUN_A, agent="researcher"),
        },
        wake_calls=[],
        ticket_ids={"a": TICKET_A, "b": TICKET_B},
        worker_sessions={"a": WORKER_A, "b": WORKER_B},
        subagent_sessions={"a": SUBAGENT_A},
        run_ids={"a": RUN_A, "b": RUN_B},
    )


async def _seed(factory: async_sessionmaker[AsyncSession]) -> None:
    """两个工单、三个会话、两个根运行；外键顺序受 ``ticket`` ↔ ``session`` 约束。"""
    async with factory() as db:
        db.add(Ticket(id=TICKET_A, title="工单甲", kind="daily_ops", origin="user"))
        db.add(Ticket(id=TICKET_B, title="工单乙", kind="daily_ops", origin="user"))
        await db.commit()
        db.add_all(
            [
                AgentSession(
                    id=WORKER_A,
                    model="test-model",
                    kind="worker",
                    status="active",
                    ticket_id=TICKET_A,
                ),
                AgentSession(
                    id=WORKER_B,
                    model="test-model",
                    kind="worker",
                    status="active",
                    ticket_id=TICKET_B,
                ),
                AgentSession(
                    id=SUBAGENT_A,
                    model="test-model",
                    kind="subagent",
                    status="active",
                    ticket_id=TICKET_A,
                    parent_session_id=WORKER_A,
                ),
            ]
        )
        await db.commit()
        for ticket_id, session_id in ((TICKET_A, WORKER_A), (TICKET_B, WORKER_B)):
            ticket = await db.get(Ticket, ticket_id)
            assert ticket is not None
            ticket.worker_session_id = session_id
        await db.commit()
        db.add_all(
            [
                AgentRun(
                    id=RUN_A,
                    root_run_id=RUN_A,
                    session_id=WORKER_A,
                    ticket_id=TICKET_A,
                    agent_name="daily_ops",
                    kind="worker_turn",
                    status="running",
                    boot_id=BOOT_ID,
                ),
                AgentRun(
                    id=RUN_B,
                    root_run_id=RUN_B,
                    session_id=WORKER_B,
                    ticket_id=TICKET_B,
                    agent_name="daily_ops",
                    kind="worker_turn",
                    status="running",
                    boot_id=BOOT_ID,
                ),
            ]
        )
        await db.commit()


@pytest.fixture
def lease_env(
    tmp_path: Path, lease_world: LeaseWorld
) -> Any:
    """``(factory, service, events, published)``；用例结束销毁同步引擎。"""
    events: list[tuple[str, dict[str, Any]]] = []
    published: list[dict[str, Any]] = []
    engines: list[AsyncEngine] = []

    async def setup() -> async_sessionmaker[AsyncSession]:
        engine, factory = await make_session_factory(tmp_path / "lease.db")
        engines.append(engine)
        await _seed(factory)
        return factory

    factory = asyncio.run(setup())
    service = DeviceLeaseService(
        factory,
        boot_id=BOOT_ID,
        thread_router=RecordingThreadRouter(published),
        broadcast=lambda message_type, data: events.append((message_type, data)),
        settings_provider=lambda: SimpleNamespace(lease=lease_world.settings),
        on_released=lambda: lease_world.wake_calls.append("released"),
        clock=lease_world.clock,
        poll_seconds=0.02,
    )
    yield factory, service, events, published
    engines[0].sync_engine.dispose()
