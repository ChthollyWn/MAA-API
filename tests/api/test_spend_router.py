"""消耗与简报 REST 契约（M84 规格 §4.3、§6 的 ``test_spend_router.py``）。

§6 列出的三条是：默认区间是含今天 7 天且 ``today`` 固定三项；``since`` 晚于今天或
跨度超过 90 天回 400 ``INVALID_PARAMETER``；``POST /api/agent/brief/run`` 回 202 并
带 ``ticket_id`` / ``artifact_id``。这里再补未装配 503 与两个端点 401。

应用按生产装配的最小集现搭：只装消耗路由、统一错误处理器与 ``app.state`` 上的
``spend_guard`` / ``brief_service`` / ``ticket_service``；真实 ``SpendGuard`` 写进
``isolated_db`` 的临时库。时钟固定成北京时间 2026-11-05 20:00，分日与 ``since``
校验都不随跑测时刻漂移。
"""

from __future__ import annotations

from datetime import date, datetime, timedelta
from types import SimpleNamespace
from typing import Any

import pytest
from fastapi import FastAPI
from sqlmodel import SQLModel

import maa_api.db.models  # noqa: F401  # 注册全部表，create_all 才能建出消耗账本
from maa_api.agent import spend as spend_module
from maa_api.agent.spend import SpendGuard
from maa_api.api import deps
from maa_api.api.errors import register_exception_handlers
from maa_api.api.routers import spend as spend_router
from maa_api.db import session as db_session
from maa_api.db.models import SpendLedger
from maa_api.settings import Settings, SpendSettings

#: 北京 2026-11-05 20:00（naive UTC），消耗日 2026-11-05。
NOW = datetime(2026, 11, 5, 12, 0)
TODAY = date(2026, 11, 5)


async def _create_schema(engine: Any) -> None:
    async with engine.begin() as connection:
        await connection.run_sync(SQLModel.metadata.create_all)


def _guard(*, medicine: int = -1, stone: int = -1, expedite: int = -1) -> SpendGuard:
    """真实守卫 + 固定时钟 + 可控上限（每次判定现读的设置替身）。"""
    return SpendGuard(
        db_session.session_factory,
        settings_provider=lambda: Settings(
            spend=SpendSettings(
                daily_medicine_limit=medicine,
                daily_stone_limit=stone,
                daily_expedite_limit=expedite,
            )
        ),
        clock=lambda: NOW,
    )


class _BriefService:
    """简报服务替身：``run_now`` 返回两个固定 id，记录调用次数。"""

    def __init__(self) -> None:
        self.calls = 0

    async def run_now(self) -> SimpleNamespace:
        self.calls += 1
        return SimpleNamespace(ticket_id="ticket-brief", artifact_id="artifact-brief")


def _build_app(
    isolated_db,
    make_client,
    *,
    guard: Any = None,
    brief_service: Any = None,
    ticket_service: Any = None,
):
    app = FastAPI()
    register_exception_handlers(app)
    app.include_router(spend_router.router)
    if guard is not None:
        app.state.spend_guard = guard
    if brief_service is not None:
        app.state.brief_service = brief_service
    if ticket_service is not None:
        app.state.ticket_service = ticket_service
    client = make_client(app)
    client.portal.call(_create_schema, isolated_db)
    return app, client


def _seed_ledger(
    client, *, kind: str, planned: int, created_at: datetime
) -> None:
    async def seed() -> None:
        async with db_session.session_factory() as db:
            db.add(
                SpendLedger(
                    kind=kind,
                    planned=planned,
                    source="manual",
                    created_at=created_at,
                )
            )
            await db.commit()

    client.portal.call(seed)


def _error(response):
    return response.json()["error"]


@pytest.fixture
def frozen_spend_clock(monkeypatch: pytest.MonkeyPatch) -> None:
    """路由的 ``since`` 校验与守卫用同一个固定时钟。"""
    monkeypatch.setattr(spend_module, "utcnow", lambda: NOW)


# ---------------------------------------------------------------------------
# §6 的三条
# ---------------------------------------------------------------------------
def test_summarize_spend_default_range(
    isolated_db, make_client, tmp_settings, frozen_spend_clock
):
    """缺省 ``since`` 是含今天 7 天；``today`` 固定三项且不限时 ``remaining`` 为 null。"""
    _app, client = _build_app(
        isolated_db, make_client, guard=_guard(medicine=5, stone=0)
    )
    _seed_ledger(
        client, kind="medicine", planned=2, created_at=NOW - timedelta(minutes=5)
    )

    response = client.get("/api/agent/spend")
    assert response.status_code == 200, response.text
    body = response.json()

    assert body["date"] == TODAY.isoformat()
    assert body["day_start"] == "2026-11-04T20:00:00Z"
    assert body["day_end"] == "2026-11-05T20:00:00Z"
    assert [(row["kind"], row["label"]) for row in body["today"]] == [
        ("medicine", "理智药"),
        ("stone", "源石"),
        ("expedite", "加急许可"),
    ]
    assert [row["used"] for row in body["today"]] == [2, 0, 0]
    assert [row["limit"] for row in body["today"]] == [5, 0, -1]
    assert [row["remaining"] for row in body["today"]] == [3, 0, None]

    expected_days = [
        (TODAY - timedelta(days=offset)).isoformat() for offset in range(6, -1, -1)
    ]
    assert [row["date"] for row in body["days"]] == expected_days
    assert body["days"][-1] == {
        "date": TODAY.isoformat(),
        "medicine": 2,
        "stone": 0,
        "expedite": 0,
    }
    assert body["blocked_today"] == []
    assert body["capped_sources"] == ["agent", "scheduled"]


def test_summarize_spend_rejects_bad_since(
    isolated_db, make_client, tmp_settings, frozen_spend_clock
):
    """未来日期与超过 90 天（含首尾）的 ``since`` 都是 400 ``INVALID_PARAMETER``。"""
    _app, client = _build_app(isolated_db, make_client, guard=_guard())

    future = client.get(
        "/api/agent/spend",
        params={"since": (TODAY + timedelta(days=1)).isoformat()},
    )
    assert future.status_code == 400, future.text
    assert _error(future)["code"] == "INVALID_PARAMETER"

    too_long = client.get(
        "/api/agent/spend",
        params={"since": (TODAY - timedelta(days=90)).isoformat()},
    )
    assert too_long.status_code == 400, too_long.text
    assert _error(too_long)["code"] == "INVALID_PARAMETER"

    # 含首尾正好 90 天是合法上限，区间长度与请求一致。
    longest = client.get(
        "/api/agent/spend",
        params={"since": (TODAY - timedelta(days=89)).isoformat()},
    )
    assert longest.status_code == 200, longest.text
    assert len(longest.json()["days"]) == 90


def test_run_brief_returns_202(
    isolated_db, make_client, tmp_settings
):
    """手动简报回 202，带工单与产物 id；无请求体也要能调用。"""
    service = _BriefService()
    _app, client = _build_app(
        isolated_db,
        make_client,
        brief_service=service,
        ticket_service=SimpleNamespace(),
    )

    response = client.post("/api/agent/brief/run")
    assert response.status_code == 202, response.text
    assert response.json() == {
        "ticket_id": "ticket-brief",
        "artifact_id": "artifact-brief",
    }
    assert service.calls == 1


# ---------------------------------------------------------------------------
# 装配与鉴权
# ---------------------------------------------------------------------------
def test_services_unwired_are_503(isolated_db, make_client, tmp_settings):
    """``spend_guard`` 与 ``brief_service`` 未装配时两条端点都回 503。"""
    _app, client = _build_app(isolated_db, make_client)

    summary = client.get("/api/agent/spend")
    assert summary.status_code == 503, summary.text
    assert _error(summary)["code"] == "SERVICE_UNAVAILABLE"

    run = client.post("/api/agent/brief/run")
    assert run.status_code == 503, run.text
    assert _error(run)["code"] == "SERVICE_UNAVAILABLE"


def test_run_brief_requires_ticket_service(
    isolated_db, make_client, tmp_settings
):
    """简报服务在但工单服务缺失时同样 503，不半路抛 RuntimeError。"""
    _app, client = _build_app(
        isolated_db, make_client, brief_service=_BriefService()
    )

    response = client.post("/api/agent/brief/run")
    assert response.status_code == 503, response.text
    assert _error(response)["code"] == "SERVICE_UNAVAILABLE"


@pytest.mark.parametrize("tmp_settings", ["s3cret"], indirect=True)
def test_routes_require_auth(isolated_db, make_client, tmp_settings):
    """两条端点都挂 ``require_auth``。"""
    deps.reset_rate_limiter()
    _app, client = _build_app(isolated_db, make_client, guard=_guard())

    for method, path in (
        ("get", "/api/agent/spend"),
        ("post", "/api/agent/brief/run"),
    ):
        response = getattr(client, method)(path)
        assert response.status_code == 401, (method, path, response.text)
        assert _error(response)["code"] == "UNAUTHORIZED"
    deps.reset_rate_limiter()
