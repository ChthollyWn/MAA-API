"""养成计划 REST 端点（M31 补口，规格 §7.7）：列表、单条、取消。

用临时 SQLite 起真实服务（``isolated_db`` + ``create_all``），app 是现搭的（``tests/api``
的纪律：不 import ``maa_api.main``）。计划行直接写库、``requirements`` 留空，这样
``list_plans()`` 的刷新没有可算的东西，用例只盯路由的契约；定时任务用假服务记录
``delete`` 调用，验证「取消 = 删定时任务 + status=cancelled」这一条。

``preview``（确认卡片里的预演）不在这里测：它只走 Agent 确认链路，
见 ``tests/agent/test_policy_account_write.py``。
"""

from __future__ import annotations

import asyncio
from datetime import datetime
from typing import Any

import pytest
from fastapi import FastAPI
from sqlalchemy.ext.asyncio import AsyncEngine
from sqlmodel import SQLModel

import maa_api.db.models  # noqa: F401  # 注册全部表
from maa_api.api.errors import register_exception_handlers
from maa_api.api.routers import account as account_router
from maa_api.db import session as db_session
from maa_api.db.models import Schedule, UpgradePlan
from maa_api.services.account.plans import UpgradePlanService

#: 三条端点（方法, 路径），鉴权与 503 用例逐个打一遍。
ENDPOINTS = (
    ("GET", "/api/account/upgrade-plans"),
    ("GET", "/api/account/upgrade-plans/plan-active"),
    ("DELETE", "/api/account/upgrade-plans/plan-active"),
)

#: 列表 `items` / 单条返回的键（服务层 ``_plan_wire`` 的形状）。
WIRE_KEYS = {
    "id",
    "name",
    "status",
    "targets",
    "requirements",
    "progress",
    "schedule_id",
    "session_id",
    "created_at",
    "updated_at",
    "completed_at",
}

AUTH = {"Authorization": "Bearer s3cret"}


class FakeScheduleService:
    """只记 ``delete`` 调用：取消计划时该删的任务有没有删。"""

    def __init__(self) -> None:
        self.deleted: list[str] = []

    async def delete(self, schedule_id: str) -> dict[str, Any]:
        self.deleted.append(schedule_id)
        return {"id": schedule_id, "deleted": True}


async def _create_schema(engine: AsyncEngine) -> None:
    async with engine.begin() as connection:
        await connection.run_sync(SQLModel.metadata.create_all)


def _build_app(
    isolated_db: AsyncEngine, *, schedule_service: Any = None
) -> FastAPI:
    """现搭 app：注册处理器 + 账号路由 + 计划服务（拿临时库的会话工厂）。"""
    asyncio.run(_create_schema(isolated_db))
    app = FastAPI()
    register_exception_handlers(app)
    app.include_router(account_router.router)
    # 生产装配同款：`app.state.upgrade_plan_service` 是 main.py 的键名。
    app.state.upgrade_plan_service = UpgradePlanService(
        db_session.session_factory, schedule_service=schedule_service
    )
    return app


def _client(isolated_db: AsyncEngine, make_client, *, schedule_service: Any = None):
    """``(app, client)``：用例两边都要用（app 拿服务，client 发请求）。"""
    app = _build_app(isolated_db, schedule_service=schedule_service)
    return app, make_client(app)


def _plan(
    plan_id: str,
    name: str,
    status: str,
    *,
    day: int,
    schedule_id: str | None = None,
) -> dict[str, Any]:
    """一条计划行；``created_at`` 递增，用来钉住列表顺序。"""
    return {
        "id": plan_id,
        "name": name,
        "status": status,
        "targets": [{"operator": name, "elite": 2, "level": 80}],
        "requirements": {},
        "progress": {"missing": {}},
        "schedule_id": schedule_id,
        "created_at": datetime(2026, 1, day, 12, 0, 0),
        "updated_at": datetime(2026, 1, day, 12, 0, 0),
    }


def _seed(
    plans: list[dict[str, Any]], *, schedules: list[dict[str, Any]] = ()
) -> None:
    """直接写计划 / 定时任务行：本文件只验路由，不走 planner 与流水线。"""

    async def scenario() -> None:
        async with db_session.session_factory() as session:
            for values in schedules:
                session.add(Schedule(**values))
            for values in plans:
                session.add(UpgradePlan(**values))
            await session.commit()

    asyncio.run(scenario())


def _three_plans() -> list[dict[str, Any]]:
    return [
        _plan("plan-active", "银灰精二", "active", day=1),
        _plan("plan-done", "艾雅法拉专三", "completed", day=2),
        _plan("plan-cancelled", "令精二", "cancelled", day=3),
    ]


def _error(response):
    return response.json()["error"]


# ---------------------------------------------------------------------------
# 鉴权与 503
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("tmp_settings", ["s3cret"], indirect=True)
@pytest.mark.parametrize(("method", "path"), ENDPOINTS)
def test_upgrade_plan_endpoints_require_auth(
    isolated_db, make_client, tmp_settings, method, path
):
    _app, client = _client(isolated_db, make_client)

    response = client.request(method, path, json={})
    assert response.status_code == 401
    assert _error(response)["code"] == "UNAUTHORIZED"

    ok = client.request(method, path, json={}, headers=AUTH)
    assert ok.status_code != 401


@pytest.mark.parametrize(("method", "path"), ENDPOINTS)
def test_upgrade_plan_service_unavailable_before_lifespan(
    isolated_db, make_client, tmp_settings, method, path
):
    """服务还没装配（lifespan 未到位）时统一 503，不是 500。"""
    app = FastAPI()
    register_exception_handlers(app)
    app.include_router(account_router.router)
    client = make_client(app)

    response = client.request(method, path, json={})
    assert response.status_code == 503
    assert _error(response)["code"] == "SERVICE_UNAVAILABLE"


# ---------------------------------------------------------------------------
# GET /api/account/upgrade-plans
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("tmp_settings", ["s3cret"], indirect=True)
def test_list_upgrade_plans_returns_wire_shape(
    isolated_db, make_client, tmp_settings
):
    _app, client = _client(isolated_db, make_client)
    _seed(_three_plans())

    response = client.get("/api/account/upgrade-plans", headers=AUTH)

    assert response.status_code == 200
    body = response.json()
    assert set(body) == {"items", "total"}
    assert body["total"] == 3
    # 服务层按 created_at 排序，`plans` 这个中间键不外泄。
    assert [item["id"] for item in body["items"]] == [
        "plan-active",
        "plan-done",
        "plan-cancelled",
    ]
    for item in body["items"]:
        assert set(item) == WIRE_KEYS
    assert body["items"][0]["targets"] == [
        {"operator": "银灰精二", "elite": 2, "level": 80}
    ]


@pytest.mark.parametrize("tmp_settings", ["s3cret"], indirect=True)
@pytest.mark.parametrize(
    ("status", "expected"),
    [
        ("active", ["plan-active"]),
        ("completed", ["plan-done"]),
        ("cancelled", ["plan-cancelled"]),
    ],
)
def test_list_upgrade_plans_filters_by_status(
    isolated_db, make_client, tmp_settings, status, expected
):
    _app, client = _client(isolated_db, make_client)
    _seed(_three_plans())

    response = client.get(
        "/api/account/upgrade-plans", params={"status": status}, headers=AUTH
    )

    assert response.status_code == 200
    body = response.json()
    assert [item["id"] for item in body["items"]] == expected
    assert body["total"] == len(expected)


@pytest.mark.parametrize("tmp_settings", ["s3cret"], indirect=True)
def test_list_upgrade_plans_rejects_unknown_status(
    isolated_db, make_client, tmp_settings
):
    """非三态的 status 走 FastAPI 的 422，错误码是全局的 VALIDATION_ERROR。"""
    _app, client = _client(isolated_db, make_client)
    _seed(_three_plans())

    response = client.get(
        "/api/account/upgrade-plans", params={"status": "running"}, headers=AUTH
    )

    assert response.status_code == 422
    assert _error(response)["code"] == "VALIDATION_ERROR"


# ---------------------------------------------------------------------------
# GET /api/account/upgrade-plans/{plan_id}
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("tmp_settings", ["s3cret"], indirect=True)
def test_get_upgrade_plan_returns_single(isolated_db, make_client, tmp_settings):
    _app, client = _client(isolated_db, make_client)
    _seed(_three_plans())

    response = client.get("/api/account/upgrade-plans/plan-done", headers=AUTH)

    assert response.status_code == 200
    body = response.json()
    assert body["id"] == "plan-done"
    assert body["name"] == "艾雅法拉专三"
    assert body["status"] == "completed"
    assert set(body) == WIRE_KEYS


@pytest.mark.parametrize("tmp_settings", ["s3cret"], indirect=True)
def test_get_upgrade_plan_missing_is_404(isolated_db, make_client, tmp_settings):
    _app, client = _client(isolated_db, make_client)
    _seed(_three_plans())

    response = client.get("/api/account/upgrade-plans/plan-nope", headers=AUTH)

    assert response.status_code == 404
    error = _error(response)
    assert error["code"] == "UPGRADE_PLAN_NOT_FOUND"
    assert error["details"]["plan_id"] == "plan-nope"


# ---------------------------------------------------------------------------
# DELETE /api/account/upgrade-plans/{plan_id}
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("tmp_settings", ["s3cret"], indirect=True)
def test_delete_upgrade_plan_cancels_and_removes_schedule(
    isolated_db, make_client, tmp_settings
):
    """取消 = 删定时任务 + status=cancelled，返回取消后的计划。"""
    schedules = FakeScheduleService()
    _app, client = _client(isolated_db, make_client, schedule_service=schedules)
    _seed(
        [_plan("plan-active", "银灰精二", "active", day=1, schedule_id="sched-1")],
        schedules=[
            {
                "id": "sched-1",
                "name": "银灰精二 每日",
                "cron": "0 8 * * *",
                "template": [],
            }
        ],
    )

    response = client.delete("/api/account/upgrade-plans/plan-active", headers=AUTH)

    assert response.status_code == 200
    body = response.json()
    assert body["id"] == "plan-active"
    assert body["status"] == "cancelled"
    assert body["schedule_id"] == "sched-1"
    assert set(body) == WIRE_KEYS
    assert schedules.deleted == ["sched-1"]

    # 落库了：再读一次还是 cancelled（复读走 list_plans 的刷新路径）。
    again = client.get("/api/account/upgrade-plans/plan-active", headers=AUTH)
    assert again.status_code == 200
    assert again.json()["status"] == "cancelled"


@pytest.mark.parametrize("tmp_settings", ["s3cret"], indirect=True)
def test_delete_upgrade_plan_twice_is_still_200(
    isolated_db, make_client, tmp_settings
):
    schedules = FakeScheduleService()
    _app, client = _client(isolated_db, make_client, schedule_service=schedules)
    _seed([_plan("plan-active", "银灰精二", "active", day=1)])

    first = client.delete("/api/account/upgrade-plans/plan-active", headers=AUTH)
    second = client.delete("/api/account/upgrade-plans/plan-active", headers=AUTH)

    assert (first.status_code, second.status_code) == (200, 200)
    assert second.json()["status"] == "cancelled"


@pytest.mark.parametrize("tmp_settings", ["s3cret"], indirect=True)
def test_delete_upgrade_plan_missing_is_404(isolated_db, make_client, tmp_settings):
    _app, client = _client(isolated_db, make_client)
    _seed(_three_plans())

    response = client.delete("/api/account/upgrade-plans/plan-nope", headers=AUTH)

    assert response.status_code == 404
    assert _error(response)["code"] == "UPGRADE_PLAN_NOT_FOUND"
