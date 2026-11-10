"""运行反馈端点的 REST 契约（M99 规格 §4.3 端点 7、8，§4.5.4、§4.5.5）。

两个端点都走请求作用域会话：``PUT`` 先确认 ``agent_run`` 存在，再按 ``run_id``
upsert（覆盖时不换行、``created_at`` 保留），``GET`` 在没有评价时返回
``{"item": null}``。服务未装配时仍走 ``_insights_service`` 的 503 出口。app 按生产
装配的最小集现搭：只装 insights 路由、统一错误处理器与 ``app.state.insights_service``；
表建在 ``isolated_db`` 的临时库里，不碰真实 ``resource/maa_api.db``。

覆盖：新建（字段与 ``Z`` 时间）、覆盖（同一行、``created_at`` 保留、``updated_at``
变化、``reasons`` 去重后最多 5 个、空白评论按 ``None`` 存）、``GET`` 无评价返回
``{"item": null}`` / 有评价回填、未知运行 404 ``RUN_NOT_FOUND``、评分 0/6 与未知原因
422、服务未装配 503、鉴权。
"""

from __future__ import annotations

from collections.abc import Awaitable, Callable
from datetime import UTC, datetime, timedelta
from typing import Any

import pytest
from fastapi import FastAPI
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker
from sqlmodel import SQLModel

import maa_api.db.models  # noqa: F401  # 注册全部表，create_all 才能建出 agent_run / run_feedback
from maa_api.api import deps
from maa_api.api.errors import register_exception_handlers
from maa_api.api.routers import insights as insights_router
from maa_api.api.routers import runs as runs_router
from maa_api.db import session as db_session
from maa_api.db.models import AgentRun, RunFeedback
from maa_api.domain.enums import AgentRunKind, AgentRunStatus
from maa_api.services.insights import InsightsService

#: 一个 36 位运行 id（``agent_run.id`` 的宽度）。
RUN_ID = "11111111-1111-1111-1111-111111111111"
#: 固定的 naive UTC 时刻（与库里的口径一致），避免用例依赖当前时间。
T1 = datetime(2026, 11, 10, 8, 0, 0)
#: 覆盖用例的旧评价时刻；只要早于「现在」即可，取一个明显更早的固定值。
OLD = datetime(2026, 1, 1, 8, 0, 0)
#: ``FeedbackOut`` 的 7 个字段（规格 §4.3）。
FEEDBACK_FIELDS = {
    "id",
    "run_id",
    "rating",
    "reasons",
    "comment",
    "created_at",
    "updated_at",
}

Seed = Callable[[async_sessionmaker[AsyncSession]], Awaitable[None]]


def _run(
    run_id: str = RUN_ID, *, status: AgentRunStatus = AgentRunStatus.SUCCEEDED
) -> AgentRun:
    """一条最小可用的运行；``run_feedback.run_id`` 有 CASCADE 外键。"""
    finished_at = (
        None if status is AgentRunStatus.INTERRUPTED else T1 + timedelta(minutes=1)
    )
    return AgentRun(
        id=run_id,
        root_run_id=run_id,
        agent_name="legacy_chat",
        kind=AgentRunKind.LEGACY_TURN,
        status=status,
        boot_id="test-boot",
        started_at=T1,
        finished_at=finished_at,
    )


def _build_app(
    isolated_db,
    make_client,
    *,
    seed: Seed | None = None,
    with_service: bool = True,
    with_runs_router: bool = False,
):
    """建路由与（可选）服务装配，返回已进入上下文的客户端。

    ``with_runs_router`` 用于复现生产注册顺序：既有 ``runs.router`` 先装、
    insights 路由后装，确认 ``/{run_id}/feedback`` 不会被 ``/{run_id}`` 吞掉。
    """
    app = FastAPI()
    register_exception_handlers(app)
    if with_runs_router:
        app.include_router(runs_router.router)
    app.include_router(insights_router.router)
    if with_service:
        app.state.insights_service = InsightsService(db_session.session_factory)
    client = make_client(app)

    async def create_schema() -> None:
        async with isolated_db.begin() as connection:
            await connection.run_sync(SQLModel.metadata.create_all)

    client.portal.call(create_schema)
    if seed is not None:
        client.portal.call(seed, db_session.session_factory)
    return client


async def _seed_run(factory: async_sessionmaker[AsyncSession]) -> None:
    """只有运行、没有评价。"""
    async with factory() as session:
        session.add(_run())
        await session.commit()


async def _seed_run_with_feedback(factory: async_sessionmaker[AsyncSession]) -> None:
    """一条旧评价：``created_at`` / ``updated_at`` 都是 ``OLD``。"""
    async with factory() as session:
        session.add(_run())
        await session.flush()
        session.add(
            RunFeedback(
                run_id=RUN_ID,
                rating=2,
                reasons=["slow"],
                comment="旧评论",
                created_at=OLD,
                updated_at=OLD,
            )
        )
        await session.commit()


def _stored_feedback(client) -> list[dict[str, Any]]:
    """直接读库，返回全部评价行的可比较快照（在 TestClient 的事件循环里跑）。"""

    async def read() -> list[dict[str, Any]]:
        async with db_session.session_factory() as session:
            rows = (await session.execute(select(RunFeedback))).scalars().all()
            return [
                {
                    "id": row.id,
                    "run_id": row.run_id,
                    "rating": row.rating,
                    "reasons": list(row.reasons),
                    "comment": row.comment,
                    "created_at": row.created_at,
                    "updated_at": row.updated_at,
                }
                for row in rows
            ]

    return client.portal.call(read)


def _error(response):
    return response.json()["error"]


def _path(run_id: str = RUN_ID) -> str:
    return f"/api/agent/runs/{run_id}/feedback"


# ---------------------------------------------------------------------------
# 新建与覆盖
# ---------------------------------------------------------------------------
def test_put_creates_feedback(isolated_db, make_client, tmp_settings):
    """首次提交插入一行；回包字段、评分、原因与 ``Z`` 时间都符合 spec。"""
    client = _build_app(isolated_db, make_client, seed=_seed_run)

    response = client.put(
        _path(),
        json={
            "rating": 5,
            "reasons": ["slow", "incomplete"],
            "comment": "再多给点细节",
        },
    )
    assert response.status_code == 200, response.text
    body = response.json()
    assert set(body) == FEEDBACK_FIELDS
    assert isinstance(body["id"], int)
    assert body["run_id"] == RUN_ID
    assert body["rating"] == 5
    assert body["reasons"] == ["slow", "incomplete"]
    assert body["comment"] == "再多给点细节"
    assert body["created_at"].endswith("Z")
    assert body["updated_at"].endswith("Z")

    stored = _stored_feedback(client)
    assert len(stored) == 1
    assert stored[0]["id"] == body["id"]
    assert stored[0]["rating"] == 5
    assert stored[0]["reasons"] == ["slow", "incomplete"]
    assert stored[0]["comment"] == "再多给点细节"


def test_put_defaults_reasons_and_comment(isolated_db, make_client, tmp_settings):
    """只给评分时 ``reasons=[]``、``comment=None``。"""
    client = _build_app(isolated_db, make_client, seed=_seed_run)

    response = client.put(_path(), json={"rating": 3})
    assert response.status_code == 200, response.text
    body = response.json()
    assert body["reasons"] == []
    assert body["comment"] is None


def test_put_overwrites_same_row_keeping_created_at(
    isolated_db, make_client, tmp_settings
):
    """重复提交只改行：``created_at`` 保留、``updated_at`` 前进、原因去重、空白评论为 ``None``。"""
    client = _build_app(
        isolated_db, make_client, seed=_seed_run_with_feedback
    )

    response = client.put(
        _path(),
        json={
            "rating": 4,
            "reasons": ["unsafe", "slow", "unsafe", "incomplete", "unsafe"],
            "comment": "   ",
        },
    )
    assert response.status_code == 200, response.text
    body = response.json()
    # 覆盖不换行：id 与 created_at 都是种子里那一行的。
    assert body["id"] == 1
    assert body["created_at"] == "2026-01-01T08:00:00Z"
    assert body["rating"] == 4
    assert body["reasons"] == ["unsafe", "slow", "incomplete"]
    assert body["comment"] is None
    updated_at = datetime.fromisoformat(body["updated_at"].replace("Z", "+00:00"))
    assert updated_at > OLD.replace(tzinfo=UTC)

    stored = _stored_feedback(client)
    assert len(stored) == 1
    assert stored[0]["id"] == body["id"]
    assert stored[0]["created_at"] == OLD
    assert stored[0]["updated_at"] == updated_at.replace(tzinfo=None)


def test_put_dedupes_reasons_before_length_check(
    isolated_db, make_client, tmp_settings
):
    """去重发生在长度校验之前：原始数组超过 5 项、去重后不超过 5 项时可接受。"""
    client = _build_app(isolated_db, make_client, seed=_seed_run)

    response = client.put(
        _path(),
        json={
            "rating": 2,
            "reasons": [
                "slow",
                "slow",
                "unsafe",
                "unsafe",
                "incomplete",
                "other",
                "inaccurate",
                "slow",
            ],
        },
    )
    assert response.status_code == 200, response.text
    assert response.json()["reasons"] == [
        "slow",
        "unsafe",
        "incomplete",
        "other",
        "inaccurate",
    ]


def test_put_accepts_non_terminal_run(isolated_db, make_client, tmp_settings):
    """不校验运行状态：运行失败 / 中断后也可以评价（spec §4.5.4）。"""
    async def seed_interrupted(factory: async_sessionmaker[AsyncSession]) -> None:
        async with factory() as session:
            session.add(_run(status=AgentRunStatus.INTERRUPTED))
            await session.commit()

    client = _build_app(isolated_db, make_client, seed=seed_interrupted)

    response = client.put(_path(), json={"rating": 1, "reasons": ["other"]})
    assert response.status_code == 200, response.text
    assert response.json()["rating"] == 1


@pytest.mark.parametrize("comment", ["", "   ", "\n\t"])
def test_comment_blank_stored_as_none(
    isolated_db, make_client, tmp_settings, comment
):
    """空串与纯空白评论都按 ``None`` 存（spec §4.5.4），回包与库里一致。"""
    client = _build_app(isolated_db, make_client, seed=_seed_run)

    response = client.put(_path(), json={"rating": 3, "comment": comment})
    assert response.status_code == 200, response.text
    assert response.json()["comment"] is None
    assert _stored_feedback(client)[0]["comment"] is None


# ---------------------------------------------------------------------------
# 读取
# ---------------------------------------------------------------------------
def test_get_returns_null_when_absent(isolated_db, make_client, tmp_settings):
    """运行存在但没有评价：200 且恰好是 ``{"item": null}``，不是 404。"""
    client = _build_app(isolated_db, make_client, seed=_seed_run)

    response = client.get(_path())
    assert response.status_code == 200, response.text
    assert response.json() == {"item": None}


def test_get_returns_existing_feedback(isolated_db, make_client, tmp_settings):
    """有评价时回填全部 7 个字段，时间为 ``Z`` 结尾。"""
    client = _build_app(
        isolated_db, make_client, seed=_seed_run_with_feedback
    )

    response = client.get(_path())
    assert response.status_code == 200, response.text
    body = response.json()
    assert set(body) == {"item"}
    item = body["item"]
    assert set(item) == FEEDBACK_FIELDS
    assert item["id"] == 1
    assert item["run_id"] == RUN_ID
    assert item["rating"] == 2
    assert item["reasons"] == ["slow"]
    assert item["comment"] == "旧评论"
    assert item["created_at"] == "2026-01-01T08:00:00Z"
    assert item["updated_at"] == "2026-01-01T08:00:00Z"


def test_put_then_get_round_trip(isolated_db, make_client, tmp_settings):
    """PUT 之后 GET 读到刚写入的值。"""
    client = _build_app(isolated_db, make_client, seed=_seed_run)

    put = client.put(
        _path(), json={"rating": 5, "reasons": ["incomplete"], "comment": "不错"}
    )
    assert put.status_code == 200, put.text

    got = client.get(_path())
    assert got.status_code == 200, got.text
    assert got.json()["item"] == put.json()


# ---------------------------------------------------------------------------
# 404 与 422
# ---------------------------------------------------------------------------
@pytest.mark.parametrize("method", ["get", "put"])
def test_unknown_run_is_404(isolated_db, make_client, tmp_settings, method):
    """运行 id 不存在：两个端点都是 404 ``RUN_NOT_FOUND``，``details`` 带原 id。"""
    client = _build_app(isolated_db, make_client)
    request = getattr(client, method)
    kwargs = {"json": {"rating": 5}} if method == "put" else {}

    response = request(_path("nope"), **kwargs)
    assert response.status_code == 404, response.text
    error = _error(response)
    assert error["code"] == "RUN_NOT_FOUND"
    assert error["details"]["run_id"] == "nope"
    # 404 不落任何评价行（外键也拦得住，但这里先由存在性判断挡住）。
    assert _stored_feedback(client) == []


@pytest.mark.parametrize("rating", [0, 6])
def test_invalid_rating_is_422(isolated_db, make_client, tmp_settings, rating):
    """评分必须落在 1–5；0 与 6 都是 422 ``VALIDATION_ERROR``。"""
    client = _build_app(isolated_db, make_client, seed=_seed_run)

    response = client.put(_path(), json={"rating": rating})
    assert response.status_code == 422, response.text
    assert _error(response)["code"] == "VALIDATION_ERROR"
    assert _stored_feedback(client) == []


def test_unknown_reason_is_422(isolated_db, make_client, tmp_settings):
    """未知原因由 Pydantic 拒绝（422），不是静默丢弃。"""
    client = _build_app(isolated_db, make_client, seed=_seed_run)

    response = client.put(
        _path(), json={"rating": 3, "reasons": ["slow", "not-a-reason"]}
    )
    assert response.status_code == 422, response.text
    assert _error(response)["code"] == "VALIDATION_ERROR"
    assert _stored_feedback(client) == []


def test_comment_longer_than_500_is_422(isolated_db, make_client, tmp_settings):
    """``comment`` 上限 500 字（spec §4.3）。"""
    client = _build_app(isolated_db, make_client, seed=_seed_run)

    response = client.put(_path(), json={"rating": 3, "comment": "x" * 501})
    assert response.status_code == 422, response.text
    assert _error(response)["code"] == "VALIDATION_ERROR"


# ---------------------------------------------------------------------------
# 鉴权与服务装配
# ---------------------------------------------------------------------------
def test_feedback_route_is_not_shadowed_by_runs_run_id_route(
    isolated_db, make_client, tmp_settings
):
    """生产注册顺序（runs 先、insights 后）下反馈路由仍命中，不被 ``/{run_id}`` 吞掉。"""
    client = _build_app(
        isolated_db, make_client, seed=_seed_run, with_runs_router=True
    )

    get = client.get(_path())
    assert get.status_code == 200, get.text
    assert get.json() == {"item": None}

    put = client.put(_path(), json={"rating": 5})
    assert put.status_code == 200, put.text
    assert put.json()["run_id"] == RUN_ID


@pytest.mark.parametrize("tmp_settings", ["run-feedback-secret"], indirect=True)
def test_requires_auth(isolated_db, make_client, tmp_settings):
    """未带 token 是 401 ``UNAUTHORIZED``；带上后两个端点都放行。"""
    client = _build_app(isolated_db, make_client, seed=_seed_run)
    deps.reset_rate_limiter()
    try:
        assert client.get(_path()).status_code == 401
        put = client.put(_path(), json={"rating": 5})
        assert put.status_code == 401, put.text
        assert _error(put)["code"] == "UNAUTHORIZED"
        assert _stored_feedback(client) == []

        headers = {"X-Token": "run-feedback-secret"}
        assert client.get(_path(), headers=headers).status_code == 200
        assert (
            client.put(
                _path(), json={"rating": 5}, headers=headers
            ).status_code
            == 200
        )
    finally:
        deps.reset_rate_limiter()


def test_requires_insights_service(isolated_db, make_client, tmp_settings):
    """服务未装配时两个端点都 503 ``SERVICE_UNAVAILABLE``（与同模块其他端点一致）。"""
    client = _build_app(
        isolated_db, make_client, seed=_seed_run, with_service=False
    )

    get = client.get(_path())
    assert get.status_code == 503, get.text
    assert _error(get)["code"] == "SERVICE_UNAVAILABLE"

    put = client.put(_path(), json={"rating": 5})
    assert put.status_code == 503, put.text
    assert _error(put)["code"] == "SERVICE_UNAVAILABLE"
