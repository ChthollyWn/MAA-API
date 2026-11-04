"""计划端点 REST 契约（M75 规格 §4.3、§6）。

只装 ``plans`` 路由：会话、工单、计划与收件箱写进 ``tests/api/conftest.py`` 的
``isolated_db`` 临时库。覆盖 404（会话不存在 / 没有未结束计划）、409（不在待批准）、
批准写 Worker ``system`` 收件箱并唤醒，以及退回草稿的消息文本。
"""

from __future__ import annotations

import asyncio
from types import SimpleNamespace

import pytest
from fastapi import FastAPI
from sqlalchemy import select
from sqlmodel import SQLModel

from maa_api.agent.plans.service import PlanService
from maa_api.agent.tickets.inbox import InboxService
from maa_api.api.errors import register_exception_handlers
from maa_api.api.routers import plans
from maa_api.db import session as db_session
from maa_api.db.models import AgentInbox, AgentSession, Ticket, new_uuid
from maa_api.domain.enums import TicketStatus

TITLE = "刷 1-7 到 50 个固源岩"


class _WorkerService:
    """唤醒替身：记下被唤醒的会话 id。"""

    def __init__(self) -> None:
        self.woken: list[str] = []

    def wake(self, session_id: str) -> None:
        self.woken.append(session_id)


@pytest.fixture
def plans_env(isolated_db, tmp_settings):
    """临时库 + 真 ``PlanService`` / ``InboxService`` 的 app；只装计划路由。"""

    async def prepare() -> None:
        async with isolated_db.begin() as connection:
            await connection.run_sync(SQLModel.metadata.create_all)

    asyncio.run(prepare())
    # 种子连接属于 asyncio.run 的事件循环，交给 TestClient 的循环之前先回收。
    isolated_db.sync_engine.dispose()
    factory = db_session.session_factory
    app = FastAPI()
    register_exception_handlers(app)
    app.include_router(plans.router)
    app.state.plan_service = PlanService(
        factory, approval_mode="complex", complex_min_steps=4
    )
    app.state.inbox_service = InboxService(factory)
    app.state.worker_service = _WorkerService()
    return SimpleNamespace(
        app=app,
        factory=factory,
        worker=app.state.worker_service,
        plan_service=app.state.plan_service,
    )


async def _seed_session(factory, *, with_ticket: bool = False) -> tuple[str, str | None]:
    """写一条 Worker 会话（可选一张工单）并提交，返回 id。"""
    ticket_id: str | None = None
    async with factory() as db:
        if with_ticket:
            ticket = Ticket(
                title=TITLE,
                kind="general",
                status=TicketStatus.OPEN.value,
                origin="user",
            )
            db.add(ticket)
            await db.flush()
            ticket_id = str(ticket.id)
        session = AgentSession(
            id=new_uuid(),
            model="test-model",
            kind="worker",
            ticket_id=ticket_id,
        )
        db.add(session)
        await db.commit()
        session_id = str(session.id)
    return session_id, ticket_id


def _items(count: int) -> list[dict]:
    return [{"title": f"步骤 {index}", "status": "pending"} for index in range(1, count + 1)]


async def _create_plan(env, session_id: str, ticket_id: str | None, count: int) -> str:
    plan = await env.plan_service.create_or_update(
        session_id=session_id,
        ticket_id=ticket_id,
        title=TITLE,
        items=_items(count),
    )
    return str(plan.id)


async def _inbox(factory, session_id: str) -> list[AgentInbox]:
    async with factory() as db:
        result = await db.execute(
            select(AgentInbox)
            .where(AgentInbox.session_id == session_id)
            .order_by(AgentInbox.id.asc())
        )
        return list(result.scalars().all())


def test_missing_session_and_missing_plan_are_404(plans_env, make_client) -> None:
    """未知会话 404 AGENT_SESSION_NOT_FOUND；没有计划 404 PLAN_NOT_FOUND。"""
    with make_client(plans_env.app) as client:
        missing = client.get("/api/agent/sessions/nope/plan")
        assert missing.status_code == 404
        assert missing.json()["error"]["code"] == "AGENT_SESSION_NOT_FOUND"
        missing_post = client.post(
            "/api/agent/sessions/nope/plan/approval", json={"approved": True}
        )
        assert missing_post.status_code == 404
        assert missing_post.json()["error"]["code"] == "AGENT_SESSION_NOT_FOUND"

        session_id, _ticket_id = asyncio.run(_seed_session(plans_env.factory))
        empty = client.get(f"/api/agent/sessions/{session_id}/plan")
        assert empty.status_code == 200
        assert empty.json() is None

        no_plan = client.post(
            f"/api/agent/sessions/{session_id}/plan/approval",
            json={"approved": True},
        )
        assert no_plan.status_code == 404
        assert no_plan.json()["error"]["code"] == "PLAN_NOT_FOUND"


def test_approval_on_active_plan_is_409(plans_env, make_client) -> None:
    """3 步计划直接 active，不允许再批准：409 PLAN_NOT_AWAITING_APPROVAL。"""
    session_id, ticket_id = asyncio.run(
        _seed_session(plans_env.factory, with_ticket=True)
    )
    asyncio.run(_create_plan(plans_env, session_id, ticket_id, 3))
    with make_client(plans_env.app) as client:
        listed = client.get(f"/api/agent/sessions/{session_id}/plan")
        assert listed.status_code == 200
        body = listed.json()
        assert body["status"] == "active"
        assert body["ticket_id"] == ticket_id
        assert [item["status"] for item in body["items"]] == [
            "in_progress",
            "pending",
            "pending",
        ]
        conflict = client.post(
            f"/api/agent/sessions/{session_id}/plan/approval",
            json={"approved": True},
        )
        assert conflict.status_code == 409
        assert conflict.json()["error"]["code"] == "PLAN_NOT_AWAITING_APPROVAL"


def test_approve_writes_worker_inbox_and_wakes(plans_env, make_client) -> None:
    """批准后 200 active + approved_by/at，并写一条 system 收件箱、唤醒 Worker。"""
    session_id, ticket_id = asyncio.run(
        _seed_session(plans_env.factory, with_ticket=True)
    )
    plan_id = asyncio.run(_create_plan(plans_env, session_id, ticket_id, 4))
    with make_client(plans_env.app) as client:
        response = client.post(
            f"/api/agent/sessions/{session_id}/plan/approval",
            json={"approved": True},
        )
        assert response.status_code == 200
        body = response.json()
        assert body["id"] == plan_id
        assert body["status"] == "active"
        assert body["approved_by"] == "user"
        assert body["approved_at"].endswith("Z")
        # 已批准的计划不再待批准：第二次请求 409。
        again = client.post(
            f"/api/agent/sessions/{session_id}/plan/approval",
            json={"approved": True},
        )
        assert again.status_code == 409

    items = asyncio.run(_inbox(plans_env.factory, session_id))
    assert len(items) == 1
    assert str(items[0].kind) == "system"
    assert items[0].thread_key == f"ticket:{ticket_id}"
    assert items[0].ref_id == f"plan:{plan_id}"
    assert items[0].payload["text"] == f"用户已批准计划 {plan_id}"
    assert plans_env.worker.woken == [session_id]


def test_reject_returns_to_draft_and_notifies(plans_env, make_client) -> None:
    """退回：200 draft，收件箱带上原因。"""
    session_id, ticket_id = asyncio.run(
        _seed_session(plans_env.factory, with_ticket=True)
    )
    plan_id = asyncio.run(_create_plan(plans_env, session_id, ticket_id, 4))
    with make_client(plans_env.app) as client:
        response = client.post(
            f"/api/agent/sessions/{session_id}/plan/approval",
            json={"approved": False, "reason": "步骤太粗"},
        )
        assert response.status_code == 200
        body = response.json()
        assert body["status"] == "draft"
        assert body["approved_by"] is None
        assert body["approved_at"] is None

    items = asyncio.run(_inbox(plans_env.factory, session_id))
    assert len(items) == 1
    assert items[0].payload["text"] == f"用户退回了计划 {plan_id}：步骤太粗"
    assert plans_env.worker.woken == [session_id]
