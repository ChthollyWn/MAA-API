"""能力效果看板的指标端点（M99 规格 §4.3 的 1–4 与 9 号）。

五个只读端点都从 ``app.state.insights_service`` 取启动时装配好的
:class:`~maa_api.services.insights.InsightsService`；服务尚未装配（进程启动中、
或测试只装了路由）返回 503 ``SERVICE_UNAVAILABLE``。

参数边界交给 Pydantic：``window_days`` 1–365、反馈摘要的 ``limit`` 1–20、
未知 ``metric`` 由 ``MetricId`` 校验，越界都是 422。指标是否支持趋势 / 分组由
目录判定：不支持分组时返回 400 ``INVALID_PARAMETER``，``details.supported`` 给
可分组指标的 id 列表。时间字段按 ``runs.py`` 的 ``_utc_z`` 渲染成带 ``Z`` 的
ISO 8601。

本任务只加 1–4 与 9 号端点；评测运行查询与反馈写入端点由后续任务补在同一个
``APIRouter`` 上。
"""

from __future__ import annotations

from fastapi import APIRouter, Depends, Query, Request

from maa_api.api.deps import require_auth
from maa_api.api.errors import error_responses
from maa_api.api.schemas.insights import (
    BreakdownOut,
    FeedbackDigestOut,
    MetricCatalogOut,
    OverviewOut,
    TrendOut,
    breakdown_out,
    feedback_digest_out,
    metric_definition_out,
    overview_out,
    trend_out,
)
from maa_api.domain.enums import MetricId
from maa_api.domain.errors import AppError, ErrorCode
from maa_api.services.insights import METRICS, InsightsService, MetricDefinition

__all__ = ["router"]

#: 指标目录是纯数据；路由用它判断趋势 / 分组能力与 ``details.supported``。
_METRICS_BY_ID = {definition.id: definition for definition in METRICS}
#: 支持按 ``ticket_kind`` 分组的指标 id（规格 §4.5.1：只有四个在线指标）。
_BREAKDOWN_METRICS = [
    definition.id.value for definition in METRICS if definition.supports_breakdown
]


def _definition(metric: MetricId) -> MetricDefinition:
    """按 id 取目录定义；``MetricId`` 已由查询参数校验，缺项是编码缺陷。"""
    return _METRICS_BY_ID[metric]


def _insights_service(request: Request) -> InsightsService:
    """取看板服务；装配层还没设置时返回 503。"""
    service = getattr(request.app.state, "insights_service", None)
    if service is None:
        raise AppError(
            ErrorCode.SERVICE_UNAVAILABLE, "能力效果看板还在启动中，请稍后重试"
        )
    return service


router = APIRouter(prefix="/api/agent", tags=["agent"])


@router.get(
    "/insights/catalog",
    response_model=MetricCatalogOut,
    summary="读取能力指标目录",
    responses=error_responses("SERVICE_UNAVAILABLE", "UNAUTHORIZED"),
    dependencies=[Depends(require_auth)],
)
async def get_insights_catalog(request: Request) -> MetricCatalogOut:
    """七个指标的口径、证据等级与限制；顺序与规格 §4.5.1 相同。"""
    definitions = await _insights_service(request).catalog()
    return MetricCatalogOut(
        items=[
            metric_definition_out(definition) for definition in definitions
        ]
    )


@router.get(
    "/insights/overview",
    response_model=OverviewOut,
    summary="读取能力指标总览",
    responses=error_responses(
        "VALIDATION_ERROR", "SERVICE_UNAVAILABLE", "UNAUTHORIZED"
    ),
    dependencies=[Depends(require_auth)],
)
async def get_insights_overview(
    request: Request,
    window_days: int = Query(30, ge=1, le=365, description="统计窗口天数"),
) -> OverviewOut:
    """七个指标的本窗口读数、上一窗口 delta、secondary 与 warnings。"""
    data = await _insights_service(request).overview(window_days)
    return overview_out(data)


@router.get(
    "/insights/trend",
    response_model=TrendOut,
    summary="读取单个指标的趋势",
    responses=error_responses(
        "INVALID_PARAMETER",
        "VALIDATION_ERROR",
        "SERVICE_UNAVAILABLE",
        "UNAUTHORIZED",
    ),
    dependencies=[Depends(require_auth)],
)
async def get_insights_trend(
    request: Request,
    metric: MetricId = Query(..., description="指标 id"),
    window_days: int = Query(30, ge=1, le=365, description="统计窗口天数"),
) -> TrendOut:
    """按 UTC 日分桶的趋势；没有数据时返回空点集。"""
    service = _insights_service(request)
    definition = _definition(metric)
    if not definition.supports_trend:
        raise AppError(
            ErrorCode.INVALID_PARAMETER, f"指标 {metric.value} 不支持趋势"
        )
    data = await service.trend(metric, window_days)
    return trend_out(data)


@router.get(
    "/insights/breakdown",
    response_model=BreakdownOut,
    summary="读取单个指标的分组",
    responses=error_responses(
        "INVALID_PARAMETER",
        "VALIDATION_ERROR",
        "SERVICE_UNAVAILABLE",
        "UNAUTHORIZED",
    ),
    dependencies=[Depends(require_auth)],
)
async def get_insights_breakdown(
    request: Request,
    metric: MetricId = Query(..., description="指标 id"),
    window_days: int = Query(30, ge=1, le=365, description="统计窗口天数"),
) -> BreakdownOut:
    """按工单种类（``ticket_kind``）分组的读数；离线指标返回 400。"""
    service = _insights_service(request)
    definition = _definition(metric)
    if not definition.supports_breakdown:
        raise AppError(
            ErrorCode.INVALID_PARAMETER,
            f"指标 {metric.value} 不支持分组",
            details={"supported": _BREAKDOWN_METRICS},
        )
    data = await service.breakdown(metric, window_days)
    return breakdown_out(data)


@router.get(
    "/insights/feedback",
    response_model=FeedbackDigestOut,
    summary="读取反馈摘要",
    responses=error_responses(
        "VALIDATION_ERROR", "SERVICE_UNAVAILABLE", "UNAUTHORIZED"
    ),
    dependencies=[Depends(require_auth)],
)
async def get_insights_feedback(
    request: Request,
    window_days: int = Query(30, ge=1, le=365, description="统计窗口天数"),
    limit: int = Query(10, ge=1, le=20, description="最近评价条数，最多 20"),
) -> FeedbackDigestOut:
    """窗口内的正 / 负 / 中性评价、原因分布与最近 ``limit`` 条评价。"""
    data = await _insights_service(request).feedback_digest(window_days, limit)
    return feedback_digest_out(data)
