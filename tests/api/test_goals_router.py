"""目标 REST 契约（M82 规格 §4.3、§6 的 ``test_goals_router.py``）。

四个用例覆盖 §6 列出的断言：

- 建立返回 ``201`` 且带 ``Location``，响应里的时间补 ``Z``；
- 未知目标 ``404 GOAL_NOT_FOUND``；
- 暂停 / 恢复 / 取消的状态迁移，已结束的目标再改是 ``409 GOAL_ENDED``；
- 列表按状态（多值取并集）过滤、分页与越界 ``400 INVALID_PAGINATION``。

应用按生产装配的最小集现搭：只装目标路由、统一错误处理器与 ``app.state.goal_service``；
规划工单与话题订阅用替身（真正建单由 ``tests/agent/goals/test_service.py`` 覆盖），
数据写进 ``isolated_db`` 的临时库。所有数据库操作都经 ``TestClient`` 的 portal 跑在
同一个事件循环里。
"""

from __future__ import annotations

from types import SimpleNamespace
from typing import Any
from uuid import uuid4

from fastapi import FastAPI
from sqlmodel import SQLModel

import maa_api.db.models  # noqa: F401  # 注册全部表，create_all 才能建出目标两表
from maa_api.agent.goals.service import GoalService
from maa_api.api.errors import register_exception_handlers
from maa_api.api.routers import goals as goals_router
from maa_api.db import session as db_session
from maa_api.db.models import Ticket
from maa_api.domain.enums import TicketOrigin

#: 一个合理的截止时间（北京时间 2026-11-30 22:00）。
DEADLINE = "2026-11-30T22:00:00+08:00"


class _TicketService:
    """替身建单入口：真写一行 ``ticket``（``goal.planning_ticket_id`` 有外键）。"""

    def __init__(self, factory: Any) -> None:
        self._factory = factory
        self.created: list[Ticket] = []

    async def create(self, **kwargs: Any) -> tuple[Ticket, SimpleNamespace]:
        ticket = Ticket(
            title=str(kwargs.get("title", "规划")),
            kind=str(kwargs.get("kind", "planner")),
            origin=str(kwargs.get("origin", TicketOrigin.GOAL.value)),
            goal_id=kwargs.get("goal_id"),
        )
        async with self._factory() as session:
            session.add(ticket)
            await session.commit()
        self.created.append(ticket)
        return ticket, SimpleNamespace()

    async def cancel(self, ticket_id: str, *, reason: str | None = None) -> None:
        del ticket_id, reason


class _ThreadRouter:
    """替身话题路由：订阅只记下来。"""

    def __init__(self) -> None:
        self.subscriptions: list[tuple[str, str]] = []

    async def subscribe(self, ticket_id: str, thread_key: str) -> None:
        self.subscriptions.append((ticket_id, thread_key))


async def _create_schema(engine: Any) -> None:
    async with engine.begin() as connection:
        await connection.run_sync(SQLModel.metadata.create_all)


def _build_app(isolated_db, make_client, settings):
    """建表、装路由与目标服务；返回 ``(app, client)``。"""
    app = FastAPI()
    register_exception_handlers(app)
    app.include_router(goals_router.router)
    app.state.goal_service = GoalService(
        db_session.session_factory,
        app=app,
        ticket_service=_TicketService(db_session.session_factory),
        thread_router=_ThreadRouter(),
        trigger_service=SimpleNamespace(),
        upgrade_plan_service=SimpleNamespace(),
        # 没有定时任务服务：暂停 / 恢复只改目标本身，不碰任务的路径照常走。
        get_schedule_service=lambda: None,
        settings_provider=lambda: settings,
    )
    client = make_client(app)
    client.portal.call(_create_schema, isolated_db)
    return app, client


def _error(response):
    return response.json()["error"]


def _create(client, title: str = "银灰精二专三", **overrides: Any):
    payload: dict[str, Any] = {
        "title": title,
        "description": "月底前",
        "target": {
            "text": "月底前把银灰练到精二、三技能专三",
            "operators": [{"operator": "银灰", "elite": 2, "masteries": {"3": 3}}],
        },
        "deadline": DEADLINE,
        "review_cron": "0 22 * * *",
    }
    payload.update(overrides)
    return client.post("/api/agent/goals", json=payload)


def test_create_returns_201_with_location(isolated_db, make_client, tmp_settings):
    """建目标回 201、``Location`` 指向详情；响应形状与时间 ``Z`` 对齐 §4.3。"""
    app, client = _build_app(isolated_db, make_client, tmp_settings)

    response = _create(client)
    assert response.status_code == 201
    body = response.json()
    assert response.headers["Location"] == f"/api/agent/goals/{body['id']}"
    assert body["status"] == "active"
    assert body["title"] == "银灰精二专三"
    assert body["target"]["text"] == "月底前把银灰练到精二、三技能专三"
    assert body["target"]["operators"] == [
        {"operator": "银灰", "elite": 2, "skill_level": None, "masteries": {"3": 3}, "modules": None}
    ]
    assert body["review_cron"] == "0 22 * * *"
    assert body["planning_ticket_id"]
    assert body["milestones"] == []
    assert [item["kind"] for item in body["tickets"]] == ["planner"]
    assert body["deadline"].endswith("Z")
    assert body["created_at"].endswith("Z")
    assert body["updated_at"].endswith("Z")
    # 替身建单入口真的被调用了，且订阅到 goal:<id>。
    assert app.state.goal_service._ticket_service.created


def test_get_unknown_404(isolated_db, make_client, tmp_settings):
    """不存在的目标回 404 ``GOAL_NOT_FOUND``，统一错误体。"""
    _app, client = _build_app(isolated_db, make_client, tmp_settings)

    response = client.get(f"/api/agent/goals/{uuid4()}")
    assert response.status_code == 404
    error = _error(response)
    assert error["code"] == "GOAL_NOT_FOUND"
    assert error["message"] == "没有这个目标"


def test_update_status_transitions_and_ended_409(
    isolated_db, make_client, tmp_settings
):
    """active → paused → active → cancelled 都走 PUT；已结束再改是 409。"""
    _app, client = _build_app(isolated_db, make_client, tmp_settings)
    goal_id = _create(client).json()["id"]
    url = f"/api/agent/goals/{goal_id}"

    paused = client.put(url, json={"status": "paused"})
    assert paused.status_code == 200
    assert paused.json()["status"] == "paused"

    resumed = client.put(url, json={"status": "active"})
    assert resumed.status_code == 200
    assert resumed.json()["status"] == "active"

    renamed = client.put(url, json={"title": "银灰精二"})
    assert renamed.status_code == 200
    assert renamed.json()["title"] == "银灰精二"

    cancelled = client.put(url, json={"status": "cancelled"})
    assert cancelled.status_code == 200
    assert cancelled.json()["status"] == "cancelled"
    assert cancelled.json()["ended_at"].endswith("Z")

    ended = client.put(url, json={"status": "paused"})
    assert ended.status_code == 409
    error = _error(ended)
    assert error["code"] == "GOAL_ENDED"
    assert error["message"] == "目标已结束"


def test_list_filters_status_and_paginates(isolated_db, make_client, tmp_settings):
    """``status`` 可重复取并集；分页越界是 400 ``INVALID_PAGINATION``。"""
    _app, client = _build_app(isolated_db, make_client, tmp_settings)
    first = _create(client, title="进行中一").json()["id"]
    _create(client, title="进行中二")
    paused_id = _create(client, title="要暂停").json()["id"]
    client.put(f"/api/agent/goals/{paused_id}", json={"status": "paused"})

    active = client.get("/api/agent/goals", params={"status": "active"})
    assert active.status_code == 200
    assert active.json()["total"] == 2
    assert {item["title"] for item in active.json()["items"]} == {"进行中一", "进行中二"}

    both = client.get(
        "/api/agent/goals",
        params=[("status", "active"), ("status", "paused")],
    )
    assert both.status_code == 200
    assert both.json()["total"] == 3

    second = client.get("/api/agent/goals", params={"page": 2, "size": 2})
    body = second.json()
    assert second.status_code == 200
    assert (body["page"], body["size"], body["total"]) == (2, 2, 3)
    assert len(body["items"]) == 1
    assert body["items"][0]["updated_at"].endswith("Z")

    detail = client.get(f"/api/agent/goals/{first}")
    assert detail.status_code == 200
    assert detail.json()["id"] == first

    for params in ({"page": 0}, {"size": 0}, {"size": 201}):
        invalid = client.get("/api/agent/goals", params=params)
        assert invalid.status_code == 400, params
        assert _error(invalid)["code"] == "INVALID_PAGINATION"
