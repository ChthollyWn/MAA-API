"""值守 REST 契约（M83 规格 §4.3、§6 的 ``test_watchdog.py``）。

§6 列出的一条是「解除不存在的 id 返回 404 与 ``WATCHDOG_ALERT_NOT_FOUND``」；这里
再补概况、列表过滤 / 分页、解除成功与幂等、未装配 503 与四个路由 401：

- 概况的 ``mode`` 读设置、``last_run_at`` 读服务属性、``open_count`` 现查未解除数，
  时间为 ``...Z``；
- 告警列表默认只看未解除，``severity`` 过滤，``resolved=true`` 只看已解除，
  分页越界是 400 ``INVALID_PAGINATION``，``created_at`` 倒序；
- 解除写入 ``resolved_at``，重复解除保留首次时间；
- 服务未装配回 503 ``SERVICE_UNAVAILABLE``。

应用按生产装配的最小集现搭：只装值守路由、统一错误处理器、``app.state`` 的
``watchdog_service``（只用它的 ``last_run_at``）与会话工厂；数据写进
``isolated_db`` 的临时库。所有数据库操作都经 ``TestClient`` 的 portal 跑在同一个
事件循环里。
"""

from __future__ import annotations

from datetime import datetime
from types import SimpleNamespace
from typing import Any

import pytest
from fastapi import FastAPI
from sqlmodel import SQLModel

import maa_api.db.models  # noqa: F401  # 注册全部表，create_all 才能建出 watchdog_alert
from maa_api.api import deps
from maa_api.api.errors import register_exception_handlers
from maa_api.api.routers import watchdog as watchdog_router
from maa_api.db import session as db_session
from maa_api.db.models import WatchdogAlert


async def _create_schema(engine: Any) -> None:
    async with engine.begin() as connection:
        await connection.run_sync(SQLModel.metadata.create_all)


def _build_app(isolated_db, make_client, *, service: bool = True):
    app = FastAPI()
    register_exception_handlers(app)
    app.include_router(watchdog_router.router)
    app.state.session_factory = db_session.session_factory
    if service:
        app.state.watchdog_service = SimpleNamespace(last_run_at=None)
    client = make_client(app)
    client.portal.call(_create_schema, isolated_db)
    return app, client


def _error(response):
    return response.json()["error"]


def _alert(
    *,
    check: str = "run_heartbeat_stale",
    severity: str = "warn",
    target_kind: str = "run",
    target_id: str | None = "run-1",
    message: str = "运行心跳超时",
    created_at: datetime | None = None,
    resolved_at: datetime | None = None,
) -> WatchdogAlert:
    return WatchdogAlert(
        check=check,
        severity=severity,
        target_kind=target_kind,
        target_id=target_id,
        message=message,
        evidence={"sample": check},
        action=None,
        action_status="recorded",
        created_at=created_at or datetime(2026, 11, 5, 8, 0),
        resolved_at=resolved_at,
    )


def _seed(client, *alerts: WatchdogAlert) -> list[int]:
    async def run() -> None:
        async with db_session.session_factory() as session:
            session.add_all(list(alerts))
            await session.commit()
            for alert in alerts:
                await session.refresh(alert)

    client.portal.call(run)
    return [int(alert.id) for alert in alerts]


# ---------------------------------------------------------------------------
# 概况
# ---------------------------------------------------------------------------
def test_status_reports_mode_last_run_and_open_count(
    isolated_db, make_client, tmp_settings
):
    """mode 取设置、last_run_at 取服务属性、open_count 只数未解除，时间带 Z。"""
    app, client = _build_app(isolated_db, make_client)
    _seed(
        client,
        _alert(created_at=datetime(2026, 11, 5, 8, 0)),
        _alert(severity="critical", created_at=datetime(2026, 11, 5, 9, 0)),
        _alert(
            created_at=datetime(2026, 11, 5, 10, 0),
            resolved_at=datetime(2026, 11, 5, 10, 30),
        ),
    )
    app.state.watchdog_service.last_run_at = datetime(2026, 11, 5, 8, 30)

    response = client.get("/api/agent/watchdog")
    assert response.status_code == 200
    body = response.json()
    assert body == {
        "mode": "record",
        "last_run_at": "2026-11-05T08:30:00Z",
        "open_count": 2,
    }

    tmp_settings.watchdog.mode = "auto"
    changed = client.get("/api/agent/watchdog")
    assert changed.status_code == 200
    assert changed.json()["mode"] == "auto"

    app.state.watchdog_service.last_run_at = None
    empty = client.get("/api/agent/watchdog")
    assert empty.status_code == 200
    assert empty.json()["last_run_at"] is None


# ---------------------------------------------------------------------------
# 列表
# ---------------------------------------------------------------------------
def test_list_filters_severity_resolved_and_paginates(
    isolated_db, make_client, tmp_settings
):
    """默认只看未解除、按 created_at 倒序；severity / resolved / 分页都可组合。"""
    _app, client = _build_app(isolated_db, make_client)
    first = _alert(
        check="run_heartbeat_stale",
        severity="warn",
        created_at=datetime(2026, 11, 5, 8, 0),
    )
    second = _alert(
        check="pipeline_no_callback",
        severity="critical",
        target_kind="device",
        target_id="default",
        created_at=datetime(2026, 11, 5, 9, 0),
    )
    third = _alert(
        check="ticket_stale",
        severity="warn",
        target_kind="ticket",
        target_id="ticket-1",
        created_at=datetime(2026, 11, 5, 10, 0),
    )
    resolved = _alert(
        check="core_not_ready",
        severity="critical",
        target_kind="core",
        target_id="default",
        created_at=datetime(2026, 11, 5, 11, 0),
        resolved_at=datetime(2026, 11, 5, 11, 30),
    )
    first_id, second_id, third_id, resolved_id = _seed(
        client, first, second, third, resolved
    )

    default = client.get("/api/agent/watchdog/alerts")
    assert default.status_code == 200
    body = default.json()
    assert (body["total"], body["page"], body["size"]) == (3, 1, 20)
    assert [item["id"] for item in body["items"]] == [third_id, second_id, first_id]
    wire = body["items"][0]
    assert wire["check"] == "ticket_stale"
    assert wire["severity"] == "warn"
    assert wire["target_kind"] == "ticket"
    assert wire["target_id"] == "ticket-1"
    assert wire["message"]
    assert wire["evidence"] == {"sample": "ticket_stale"}
    assert wire["action_status"] == "recorded"
    assert wire["action"] is None
    assert wire["created_at"] == "2026-11-05T10:00:00Z"
    assert wire["resolved_at"] is None

    warn = client.get("/api/agent/watchdog/alerts", params={"severity": "warn"})
    assert warn.status_code == 200
    assert warn.json()["total"] == 2
    assert [item["id"] for item in warn.json()["items"]] == [third_id, first_id]

    only_resolved = client.get(
        "/api/agent/watchdog/alerts", params={"resolved": "true"}
    )
    assert only_resolved.status_code == 200
    assert only_resolved.json()["total"] == 1
    assert only_resolved.json()["items"][0]["id"] == resolved_id
    assert only_resolved.json()["items"][0]["resolved_at"] == "2026-11-05T11:30:00Z"

    second_page = client.get(
        "/api/agent/watchdog/alerts", params={"page": 2, "size": 2}
    )
    assert second_page.status_code == 200
    assert (second_page.json()["total"], second_page.json()["page"]) == (3, 2)
    assert [item["id"] for item in second_page.json()["items"]] == [first_id]

    for params in ({"page": 0}, {"size": 0}, {"size": 201}):
        invalid = client.get("/api/agent/watchdog/alerts", params=params)
        assert invalid.status_code == 400, params
        assert _error(invalid)["code"] == "INVALID_PAGINATION"


# ---------------------------------------------------------------------------
# 解除
# ---------------------------------------------------------------------------
def test_resolve_missing(isolated_db, make_client, tmp_settings):
    """§6：解除不存在的 id 返回 404 ``WATCHDOG_ALERT_NOT_FOUND``。"""
    _app, client = _build_app(isolated_db, make_client)

    response = client.post("/api/agent/watchdog/alerts/999/resolve")
    assert response.status_code == 404
    error = _error(response)
    assert error["code"] == "WATCHDOG_ALERT_NOT_FOUND"
    assert error["message"] == "没有这条值守告警"
    assert error["details"] == {"alert_id": 999}


def test_resolve_writes_resolved_at_and_is_idempotent(
    isolated_db, make_client, tmp_settings
):
    """解除写入 ``resolved_at``；第二次解除返回同一时间，列表随之移动。"""
    _app, client = _build_app(isolated_db, make_client)
    (alert_id,) = _seed(client, _alert())

    resolved = client.post(f"/api/agent/watchdog/alerts/{alert_id}/resolve")
    assert resolved.status_code == 200
    wired = resolved.json()
    assert wired["id"] == alert_id
    assert wired["resolved_at"].endswith("Z")

    assert client.get("/api/agent/watchdog/alerts").json()["total"] == 0
    history = client.get("/api/agent/watchdog/alerts", params={"resolved": "true"})
    assert history.status_code == 200
    assert history.json()["total"] == 1
    assert history.json()["items"][0]["resolved_at"] == wired["resolved_at"]

    again = client.post(f"/api/agent/watchdog/alerts/{alert_id}/resolve")
    assert again.status_code == 200
    assert again.json()["resolved_at"] == wired["resolved_at"]


# ---------------------------------------------------------------------------
# 装配与鉴权
# ---------------------------------------------------------------------------
def test_unwired_service_is_503(isolated_db, make_client, tmp_settings):
    """服务未装配时三个端点都回 503 ``SERVICE_UNAVAILABLE``。"""
    _app, client = _build_app(isolated_db, make_client, service=False)

    requests = (
        ("get", "/api/agent/watchdog", {}),
        ("get", "/api/agent/watchdog/alerts", {}),
        ("post", "/api/agent/watchdog/alerts/1/resolve", {}),
    )
    for method, path, kwargs in requests:
        response = getattr(client, method)(path, **kwargs)
        assert response.status_code == 503, (method, path, response.text)
        assert _error(response)["code"] == "SERVICE_UNAVAILABLE"


@pytest.mark.parametrize("tmp_settings", ["s3cret"], indirect=True)
def test_routes_require_auth(isolated_db, make_client, tmp_settings):
    """三个端点都挂 ``require_auth``。"""
    deps.reset_rate_limiter()
    _app, client = _build_app(isolated_db, make_client)

    requests = (
        ("get", "/api/agent/watchdog", {}),
        ("get", "/api/agent/watchdog/alerts", {}),
        ("post", "/api/agent/watchdog/alerts/1/resolve", {}),
    )
    for method, path, kwargs in requests:
        response = getattr(client, method)(path, **kwargs)
        assert response.status_code == 401, (method, path, response.text)
        assert _error(response)["code"] == "UNAUTHORIZED"
    deps.reset_rate_limiter()
