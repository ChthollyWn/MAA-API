"""能力效果看板指标端点的 REST 契约（M99 规格 §4.3、§4.5.1、§4.5.2）。

本任务只覆盖 1–4 与 9 号端点（catalog / overview / trend / breakdown /
feedback digest）。应用按生产装配的最小集现搭：只装 insights 路由、统一错误
处理器与 ``app.state.insights_service``；表建在 ``isolated_db`` 的临时库里，
不碰真实 ``resource/maa_api.db``。

覆盖：目录的七条与固定顺序、总览空数据七张卡全空、窗口边界 422、未知
``MetricId`` 422、不支持分组的指标 400 且 ``details.supported`` 给四个在线指标、
服务未装配 503、鉴权、时间字段带 ``Z`` 与反馈摘要的空数据。
"""

from __future__ import annotations

from datetime import datetime
from typing import Any

import pytest
from fastapi import FastAPI
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker
from sqlmodel import SQLModel

import maa_api.db.models  # noqa: F401  # 注册全部表，create_all 才能建出看板读取的七张表
from maa_api.api import deps
from maa_api.api.errors import register_exception_handlers
from maa_api.api.routers import insights as insights_router
from maa_api.db.models import AgentRun, RunFeedback, Ticket, utcnow
from maa_api.domain.enums import AgentRunKind, AgentRunStatus
from maa_api.services.insights import InsightsService

#: 目录顺序逐字照规格 §4.5.1（不是 ``MetricId`` 的枚举定义顺序）。
METRIC_IDS = [
    "accuracy",
    "hallucination_rate",
    "safety_rate",
    "task_completion",
    "conversation_efficiency",
    "cost",
    "user_satisfaction",
]
#: ``supports_breakdown`` 只对四个在线指标为真。
BREAKDOWN_IDS = [
    "task_completion",
    "conversation_efficiency",
    "cost",
    "user_satisfaction",
]
#: ``MetricDefinitionOut`` 的 13 个字段（规格 §4.3）。
DEFINITION_FIELDS = {
    "id",
    "label",
    "scope",
    "unit",
    "definition",
    "formula",
    "source",
    "evaluation",
    "evidence",
    "min_sample",
    "limitations",
    "supports_trend",
    "supports_breakdown",
}
#: 五个端点与各自的最小合法查询串；鉴权与 503 用例共用。
ENDPOINTS = [
    ("/api/agent/insights/catalog", None),
    ("/api/agent/insights/overview", {"window_days": 30}),
    ("/api/agent/insights/trend", {"metric": "accuracy"}),
    ("/api/agent/insights/breakdown", {"metric": "task_completion"}),
    ("/api/agent/insights/feedback", {"window_days": 30, "limit": 10}),
]

#: 反馈摘要用例里唯一运行与评价的 id（36 位 UUID 形状）。
FEEDBACK_RUN = "aaaaaaaa-aaaa-aaaa-aaaa-aaaaaaaaaaaa"
#: 单币种成本用例的工单与运行 id。
COST_TICKET = "ticket-cost"
COST_RUN = "run-cost"


def _build_app(
    isolated_db,
    make_client,
    *,
    seed: Any = None,
):
    """建表、装配 ``InsightsService``，返回已进入上下文的客户端。"""
    factory = async_sessionmaker(
        isolated_db, expire_on_commit=False, class_=AsyncSession
    )
    app = FastAPI()
    register_exception_handlers(app)
    app.include_router(insights_router.router)
    app.state.insights_service = InsightsService(factory)
    client = make_client(app)

    async def create_schema() -> None:
        async with isolated_db.begin() as connection:
            await connection.run_sync(SQLModel.metadata.create_all)

    client.portal.call(create_schema)
    if seed is not None:
        client.portal.call(seed, factory)
    return client


async def _seed_feedback(factory: async_sessionmaker[AsyncSession]) -> None:
    """一条成功运行 + 一条五星评价；时间都落在本窗口内。"""
    finished_at = utcnow()
    async with factory() as session:
        session.add(
            AgentRun(
                id=FEEDBACK_RUN,
                root_run_id=FEEDBACK_RUN,
                agent_name="legacy_chat",
                kind=AgentRunKind.LEGACY_TURN,
                status=AgentRunStatus.SUCCEEDED,
                boot_id="test-boot",
                started_at=finished_at,
                finished_at=finished_at,
            )
        )
        await session.flush()
        session.add(
            RunFeedback(
                run_id=FEEDBACK_RUN,
                rating=5,
                reasons=["slow"],
                comment="再快一点",
            )
        )
        await session.commit()


async def _seed_cost(factory: async_sessionmaker[AsyncSession]) -> None:
    """一张窗口内 done 的工单 + 一条 CNY 运行，成本卡应带币种。"""
    finished_at = utcnow()
    async with factory() as session:
        session.add(
            Ticket(
                id=COST_TICKET,
                title="成本工单",
                kind="daily_ops",
                status="done",
                origin="user",
                created_at=finished_at,
                updated_at=finished_at,
                ended_at=finished_at,
            )
        )
        await session.flush()
        session.add(
            AgentRun(
                id=COST_RUN,
                root_run_id=COST_RUN,
                ticket_id=COST_TICKET,
                agent_name="daily_ops",
                kind=AgentRunKind.WORKER_TURN,
                status=AgentRunStatus.SUCCEEDED,
                boot_id="test-boot",
                started_at=finished_at,
                finished_at=finished_at,
                cost_micros=500_000,
                currency="CNY",
            )
        )
        await session.commit()


def _error(response):
    return response.json()["error"]


def _card(body: dict[str, Any], metric_id: str) -> dict[str, Any]:
    """从 overview 里取指定指标的卡片。"""
    return next(item for item in body["items"] if item["id"] == metric_id)


# ---------------------------------------------------------------------------
# 目录
# ---------------------------------------------------------------------------
def test_catalog_returns_seven_metrics_in_spec_order(
    isolated_db, make_client, tmp_settings
):
    """目录逐条返回 §4.5.1 的七条，顺序固定、字段完整、分组能力按 scope。"""
    client = _build_app(isolated_db, make_client)

    response = client.get("/api/agent/insights/catalog")
    assert response.status_code == 200, response.text
    payload = response.json()
    assert [item["id"] for item in payload["items"]] == METRIC_IDS
    assert [item["min_sample"] for item in payload["items"]] == [
        10,
        5,
        6,
        20,
        20,
        20,
        20,
    ]

    for item in payload["items"]:
        assert set(item) == DEFINITION_FIELDS
        assert item["supports_trend"] is True
        assert isinstance(item["limitations"], list) and item["limitations"]

    by_id = {item["id"]: item for item in payload["items"]}
    assert by_id["accuracy"]["scope"] == "offline"
    assert by_id["accuracy"]["unit"] == "ratio"
    assert by_id["accuracy"]["evidence"] == "measured"
    assert by_id["accuracy"]["supports_breakdown"] is False
    assert by_id["task_completion"]["scope"] == "online"
    assert by_id["task_completion"]["evidence"] == "self_reported"
    assert by_id["task_completion"]["supports_breakdown"] is True
    assert by_id["cost"]["unit"] == "micros"
    assert by_id["conversation_efficiency"]["unit"] == "turns"
    assert [item["id"] for item in payload["items"] if item["supports_breakdown"]] == (
        BREAKDOWN_IDS
    )


# ---------------------------------------------------------------------------
# 总览
# ---------------------------------------------------------------------------
def test_overview_empty_data_returns_seven_null_cards(
    isolated_db, make_client, tmp_settings
):
    """空库：七张卡 value/CI/delta 全 None、sample_size 0，warnings 说明口径。"""
    client = _build_app(isolated_db, make_client)

    response = client.get("/api/agent/insights/overview")
    assert response.status_code == 200, response.text
    body = response.json()
    assert body["window_days"] == 30
    assert body["generated_at"].endswith("Z")
    assert [item["id"] for item in body["items"]] == METRIC_IDS
    assert all(item["value"] is None for item in body["items"])
    assert all(item["sample_size"] == 0 for item in body["items"])
    assert all(item["ci_low"] is None for item in body["items"])
    assert all(item["ci_high"] is None for item in body["items"])
    assert all(item["delta"] is None for item in body["items"])
    assert all(item["updated_at"] is None for item in body["items"])
    assert all(item["currency"] is None for item in body["items"])
    assert all(isinstance(item["secondary"], list) for item in body["items"])
    # 证据等级仍取目录里的等级，不是空值。
    assert _card(body, "accuracy")["evidence"] == "measured"
    assert _card(body, "task_completion")["evidence"] == "self_reported"
    assert any("record/live" in warning for warning in body["warnings"])


def test_overview_cost_card_carries_single_currency(
    isolated_db, make_client, tmp_settings
):
    """单币种种子数据下成本卡的 ``currency`` 是该币种，其余卡片为空。"""
    client = _build_app(isolated_db, make_client, seed=_seed_cost)

    response = client.get("/api/agent/insights/overview")
    assert response.status_code == 200, response.text
    body = response.json()
    cost = _card(body, "cost")

    assert cost["value"] == pytest.approx(500_000)
    assert cost["sample_size"] == 1
    assert cost["currency"] == "CNY"
    secondary = {item["label"]: item for item in cost["secondary"]}
    assert secondary["窗口总成本"]["value"] == pytest.approx(500_000)
    # 币种只属于成本卡；其他指标不该带币种。
    assert all(
        item["currency"] is None
        for item in body["items"]
        if item["id"] != "cost"
    )


@pytest.mark.parametrize("window_days", [0, 366, "abc"])
def test_overview_window_validation(
    isolated_db, make_client, tmp_settings, window_days
):
    """``window_days`` 越界或不是整数 → 422 ``VALIDATION_ERROR``。"""
    client = _build_app(isolated_db, make_client)

    response = client.get(
        "/api/agent/insights/overview", params={"window_days": window_days}
    )
    assert response.status_code == 422, response.text
    assert _error(response)["code"] == "VALIDATION_ERROR"


@pytest.mark.parametrize("window_days", [1, 365])
def test_overview_window_boundaries_are_valid(
    isolated_db, make_client, tmp_settings, window_days
):
    """1 与 365 是合法边界，窗口原样回显。"""
    client = _build_app(isolated_db, make_client)

    response = client.get(
        "/api/agent/insights/overview", params={"window_days": window_days}
    )
    assert response.status_code == 200, response.text
    assert response.json()["window_days"] == window_days


@pytest.mark.parametrize(
    ("path", "params"),
    [
        ("/api/agent/insights/trend", {"metric": "accuracy"}),
        ("/api/agent/insights/breakdown", {"metric": "task_completion"}),
        ("/api/agent/insights/feedback", None),
    ],
)
def test_window_validation_on_all_endpoints(
    isolated_db, make_client, tmp_settings, path, params
):
    """除总览外的端点同样把 ``window_days`` 限制在 1–365。"""
    client = _build_app(isolated_db, make_client)

    response = client.get(path, params={**(params or {}), "window_days": 0})
    assert response.status_code == 422, response.text
    assert _error(response)["code"] == "VALIDATION_ERROR"


# ---------------------------------------------------------------------------
# 趋势与分组
# ---------------------------------------------------------------------------
def test_trend_empty_data_returns_empty_points(
    isolated_db, make_client, tmp_settings
):
    """空库的趋势是空点集；metric/scope/unit 仍按目录给出。"""
    client = _build_app(isolated_db, make_client)

    response = client.get(
        "/api/agent/insights/trend", params={"metric": "accuracy"}
    )
    assert response.status_code == 200, response.text
    assert response.json() == {
        "metric": "accuracy",
        "scope": "offline",
        "unit": "ratio",
        "points": [],
    }


@pytest.mark.parametrize(
    "path",
    ["/api/agent/insights/trend", "/api/agent/insights/breakdown"],
)
def test_unknown_metric_is_422(isolated_db, make_client, tmp_settings, path):
    """未知 ``metric`` 由 ``MetricId`` 查询参数校验成 422。"""
    client = _build_app(isolated_db, make_client)

    response = client.get(path, params={"metric": "not_a_metric"})
    assert response.status_code == 422, response.text
    assert _error(response)["code"] == "VALIDATION_ERROR"


def test_breakdown_unsupported_metric_400(
    isolated_db, make_client, tmp_settings
):
    """离线指标不支持分组：400 ``INVALID_PARAMETER`` + ``details.supported``。"""
    client = _build_app(isolated_db, make_client)

    response = client.get(
        "/api/agent/insights/breakdown", params={"metric": "accuracy"}
    )
    assert response.status_code == 400, response.text
    error = _error(response)
    assert error["code"] == "INVALID_PARAMETER"
    assert error["details"]["supported"] == BREAKDOWN_IDS


def test_breakdown_supported_metric_empty_data(
    isolated_db, make_client, tmp_settings
):
    """在线指标的空分组：dimension 固定 ``ticket_kind``，items 为空、不截断。"""
    client = _build_app(isolated_db, make_client)

    response = client.get(
        "/api/agent/insights/breakdown", params={"metric": "task_completion"}
    )
    assert response.status_code == 200, response.text
    assert response.json() == {
        "metric": "task_completion",
        "dimension": "ticket_kind",
        "items": [],
        "truncated": False,
    }


# ---------------------------------------------------------------------------
# 反馈摘要
# ---------------------------------------------------------------------------
def test_feedback_digest_empty_data(isolated_db, make_client, tmp_settings):
    """空库：计数全 0、回收率 None、原因与评价列表为空。"""
    client = _build_app(isolated_db, make_client)

    response = client.get("/api/agent/insights/feedback")
    assert response.status_code == 200, response.text
    assert response.json() == {
        "window_days": 30,
        "total": 0,
        "positive": 0,
        "negative": 0,
        "neutral": 0,
        "response_rate": None,
        "reasons": [],
        "items": [],
    }


@pytest.mark.parametrize("limit", [0, 21])
def test_feedback_limit_validation(isolated_db, make_client, tmp_settings, limit):
    """``limit`` 限制在 1–20（默认 10）。"""
    client = _build_app(isolated_db, make_client)

    response = client.get(
        "/api/agent/insights/feedback", params={"limit": limit}
    )
    assert response.status_code == 422, response.text
    assert _error(response)["code"] == "VALIDATION_ERROR"


def test_feedback_digest_and_updated_at_are_z_times(
    isolated_db, make_client, tmp_settings
):
    """有数据时：评价 ``created_at`` 与卡片 ``updated_at`` 都是带 ``Z`` 的串。"""
    client = _build_app(isolated_db, make_client, seed=_seed_feedback)

    response = client.get(
        "/api/agent/insights/feedback", params={"window_days": 7, "limit": 5}
    )
    assert response.status_code == 200, response.text
    body = response.json()
    assert body["window_days"] == 7
    assert body["total"] == 1
    assert body["positive"] == 1
    assert body["negative"] == 0
    assert body["neutral"] == 0
    assert body["reasons"] == [{"reason": "slow", "count": 1}]
    item = body["items"][0]
    assert item["run_id"] == FEEDBACK_RUN
    assert item["agent_name"] == "legacy_chat"
    assert item["rating"] == 5
    assert item["reasons"] == ["slow"]
    assert item["comment"] == "再快一点"
    assert item["created_at"].endswith("Z")
    assert datetime.fromisoformat(item["created_at"].replace("Z", "+00:00"))

    overview = client.get(
        "/api/agent/insights/overview", params={"window_days": 7}
    )
    assert overview.status_code == 200, overview.text
    card = _card(overview.json(), "user_satisfaction")
    assert card["value"] == 1.0
    assert card["sample_size"] == 1
    assert card["updated_at"] is not None
    assert card["updated_at"].endswith("Z")


# ---------------------------------------------------------------------------
# 鉴权与服务装配
# ---------------------------------------------------------------------------
@pytest.mark.parametrize("tmp_settings", ["insights-secret"], indirect=True)
def test_requires_auth(isolated_db, make_client, tmp_settings):
    """五个端点未带 token 都是 401 ``UNAUTHORIZED``；带上就 200。"""
    client = _build_app(isolated_db, make_client)
    deps.reset_rate_limiter()
    try:
        for path, params in ENDPOINTS:
            response = client.get(path, params=params)
            assert response.status_code == 401, path
            assert _error(response)["code"] == "UNAUTHORIZED"

            response = client.get(
                path, params=params, headers={"X-Token": "insights-secret"}
            )
            assert response.status_code == 200, f"{path}: {response.text}"
    finally:
        deps.reset_rate_limiter()


def test_service_unavailable_503(make_client, tmp_settings):
    """服务尚未装配（只装路由）时五个端点统一 503 ``SERVICE_UNAVAILABLE``。"""
    app = FastAPI()
    register_exception_handlers(app)
    app.include_router(insights_router.router)
    client = make_client(app)

    for path, params in ENDPOINTS:
        response = client.get(path, params=params)
        assert response.status_code == 503, path
        assert _error(response)["code"] == "SERVICE_UNAVAILABLE"
