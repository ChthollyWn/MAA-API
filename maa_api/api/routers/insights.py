"""能力效果看板的指标、评测运行与运行反馈端点（M99 规格 §4.3 的 1–9 号）。

所有端点都先确认 ``app.state.insights_service`` 已装配（进程启动中、或测试只
装了路由时返回 503 ``SERVICE_UNAVAILABLE``）；指标端点直接从
:class:`~maa_api.services.insights.InsightsService` 取数。评测运行的两个端点另用
请求作用域会话（``get_session``）构造 ``EvalRunRepository`` /
``EvalCaseRepository``：列表分页与详情用例都走仓储，指标率在路由层按这次运行
自身的全部 case 现算，不复用服务的窗口口径（spec §4.5.3 末段）。

反馈的两个端点（7、8 号）同样用请求作用域会话，但只读 ``agent_run`` 与
``run_feedback``：先确认运行存在（否则 404 ``RUN_NOT_FOUND``），``PUT`` 再按
``run_id`` upsert，``GET`` 在没有评价时返回 ``{"item": null}``。``PUT`` 的
``updated_at`` 必须是**本次请求时刻**（仓储原样写入；spec 交接口径，见
``db/repositories/feedback.py`` 的模块说明），``created_at`` 由仓储保留；不校验
运行状态，运行中断后也可以评价（spec §4.5.4）。``comment`` 去空白后为空串按
``None`` 存。

参数边界交给 Pydantic：``window_days`` 1–365、反馈摘要的 ``limit`` 1–20、
未知 ``metric`` 由 ``MetricId`` 校验，越界都是 422；反馈的 ``rating`` 1–5、
``reasons`` 去重后最多 5 个、``comment`` ≤ 500 字。评测列表的 ``page`` / ``size``
在函数里校验，非法时是 400 ``INVALID_PAGINATION``（spec §4.5.5）。指标是否支持
趋势 / 分组由目录判定：不支持分组时返回 400 ``INVALID_PARAMETER``，
``details.supported`` 给可分组指标的 id 列表。时间字段按 ``runs.py`` 的
``_utc_z`` 渲染成带 ``Z`` 的 ISO 8601。
"""

from __future__ import annotations

from collections.abc import Sequence

from fastapi import APIRouter, Depends, Path as PathParam, Query, Request
from sqlalchemy.ext.asyncio import AsyncSession

from maa_api.api.deps import get_session, require_auth
from maa_api.api.errors import error_responses
from maa_api.api.schemas.insights import (
    BreakdownOut,
    EvalCaseOut,
    EvalMetricRateOut,
    EvalRunDetailOut,
    EvalRunPage,
    FeedbackDigestOut,
    FeedbackIn,
    FeedbackOut,
    MetricCatalogOut,
    OverviewOut,
    RunFeedbackOut,
    TrendOut,
    breakdown_out,
    eval_case_out,
    eval_run_detail_out,
    eval_run_page_out,
    feedback_digest_out,
    feedback_out,
    metric_definition_out,
    overview_out,
    trend_out,
)
from maa_api.db.models import AgentRun, EvalCaseResult, RunFeedback, utcnow
from maa_api.db.repositories.evals import EvalCaseRepository, EvalRunRepository
from maa_api.db.repositories.feedback import RunFeedbackRepository
from maa_api.domain.enums import EvalRunMode, MetricId, MetricScope
from maa_api.domain.errors import AppError, ErrorCode
from maa_api.services.insights import METRICS, InsightsService, MetricDefinition
from maa_api.services.insights.stats import rate, wilson_interval

__all__ = ["MAX_EVAL_CASES", "MAX_EVAL_PAGE_SIZE", "router"]

#: 评测列表 ``size`` 的上限（spec §4.3）。
MAX_EVAL_PAGE_SIZE = 50
#: 评测详情一次最多返回的 case 数；超出时 ``cases_truncated=true``（spec §4.5.5）。
MAX_EVAL_CASES = 500
#: 指标率只输出目录里 scope 为离线的指标，顺序与目录一致。
_OFFLINE_METRICS = tuple(
    definition.id for definition in METRICS if definition.scope is MetricScope.OFFLINE
)
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


def _validate_eval_pagination(page: int, size: int) -> None:
    """``page`` ≥ 1、``size`` 1–50；非法是 400 ``INVALID_PAGINATION``。"""
    if page < 1 or size < 1 or size > MAX_EVAL_PAGE_SIZE:
        raise AppError(
            ErrorCode.INVALID_PAGINATION,
            f"page 不能小于 1，size 需在 1–{MAX_EVAL_PAGE_SIZE} 之间",
        )


async def _require_run(session: AsyncSession, run_id: str) -> None:
    """运行不存在时 404 ``RUN_NOT_FOUND``；不校验运行状态（spec §4.5.4）。"""
    if await session.get(AgentRun, run_id) is None:
        raise AppError(ErrorCode.RUN_NOT_FOUND, "没有这个运行", {"run_id": run_id})


def _normalized_comment(comment: str | None) -> str | None:
    """去首尾空白；空串或纯空白按 ``None`` 存（spec §4.5.4）。"""
    if comment is None:
        return None
    stripped = comment.strip()
    return stripped or None


def _metric_rates(cases: Sequence[EvalCaseResult]) -> list[EvalMetricRateOut]:
    """按这次运行自身的全部 case 现算指标率（spec §4.5.3 末段）。

    分母只含 ``passed + failed``；``skipped`` / ``error`` 不计数但 case 仍算
    「参与了」该指标（可出现 ``total=0`` 的一行）。``metrics=[]`` 的 case 不进
    任何指标。``hallucination_rate`` 取失败占比（``1 - passed/total``），其余离线
    指标取通过率；Wilson 区间与取同一侧的计数保持一致。
    """
    counts: dict[MetricId, list[int]] = {}
    for case in cases:
        for raw_metric in case.metrics:
            metric = MetricId(raw_metric)
            bucket = counts.setdefault(metric, [0, 0])
            if case.status == "passed":
                bucket[0] += 1
            elif case.status == "failed":
                bucket[1] += 1

    rates: list[EvalMetricRateOut] = []
    for metric in _OFFLINE_METRICS:
        if metric not in counts:
            continue
        metric_passed, metric_failed = counts[metric]
        total = metric_passed + metric_failed
        successes = (
            metric_failed if metric is MetricId.HALLUCINATION_RATE else metric_passed
        )
        interval = wilson_interval(successes, total)
        rates.append(
            EvalMetricRateOut(
                metric=metric,
                passed=metric_passed,
                total=total,
                rate=rate(successes, total),
                ci_low=None if interval is None else interval[0],
                ci_high=None if interval is None else interval[1],
            )
        )
    return rates


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
    "/evals/runs",
    response_model=EvalRunPage,
    summary="读取评测运行列表",
    responses=error_responses(
        "INVALID_PAGINATION",
        "VALIDATION_ERROR",
        "SERVICE_UNAVAILABLE",
        "UNAUTHORIZED",
    ),
    dependencies=[Depends(require_auth)],
)
async def list_eval_runs(
    request: Request,
    page: int = Query(1, description="页码，从 1 开始"),
    size: int = Query(20, description=f"每页条数，1–{MAX_EVAL_PAGE_SIZE}"),
    suite: str | None = Query(None, description="按套件过滤：regression / capability"),
    mode: EvalRunMode | None = Query(None, description="按运行模式过滤"),
    session: AsyncSession = Depends(get_session),
) -> EvalRunPage:
    """按 ``finished_at DESC, id DESC`` 分页；``suite`` / ``mode`` 可选过滤。"""
    _insights_service(request)
    _validate_eval_pagination(page, size)
    result = await EvalRunRepository(session).page_runs(
        page=page,
        size=size,
        suite=suite,
        mode=None if mode is None else mode.value,
    )
    return eval_run_page_out(result)


@router.get(
    "/evals/runs/{run_id}",
    response_model=EvalRunDetailOut,
    summary="读取评测运行详情",
    responses=error_responses(
        "EVAL_RUN_NOT_FOUND", "SERVICE_UNAVAILABLE", "UNAUTHORIZED"
    ),
    dependencies=[Depends(require_auth)],
)
async def get_eval_run(
    request: Request,
    run_id: str = PathParam(description="评测运行 id（等于套件结果的 id）"),
    session: AsyncSession = Depends(get_session),
) -> EvalRunDetailOut:
    """概要、按该运行自身全部 case 现算的指标率与 ``(attempt, scenario)`` 升序的用例。"""
    _insights_service(request)
    run = await EvalRunRepository(session).get_run(run_id)
    if run is None:
        raise AppError(
            ErrorCode.EVAL_RUN_NOT_FOUND,
            "没有这次评测运行",
            {"run_id": run_id},
        )
    cases = await EvalCaseRepository(session).list_by_run(run_id)
    # 仓储按自增 id（发布顺序）返回；详情固定按 (attempt, scenario) 升序后再截断，
    # 保证截断丢的是排序最后的那些行（spec §4.5.5）。
    ordered = sorted(cases, key=lambda case: (case.attempt, case.scenario))
    shown: list[EvalCaseOut] = [
        eval_case_out(case) for case in ordered[:MAX_EVAL_CASES]
    ]
    return eval_run_detail_out(
        run,
        shown,
        _metric_rates(ordered),
        cases_truncated=len(ordered) > MAX_EVAL_CASES,
    )


@router.put(
    "/runs/{run_id}/feedback",
    response_model=FeedbackOut,
    summary="提交或覆盖一次运行的评价",
    responses=error_responses(
        "RUN_NOT_FOUND",
        "VALIDATION_ERROR",
        "SERVICE_UNAVAILABLE",
        "UNAUTHORIZED",
    ),
    dependencies=[Depends(require_auth)],
)
async def put_run_feedback(
    payload: FeedbackIn,
    request: Request,
    run_id: str = PathParam(min_length=1, max_length=36, description="运行 id"),
    session: AsyncSession = Depends(get_session),
) -> FeedbackOut:
    """按 ``run_id`` upsert：没有则插入，已有则覆盖且 ``created_at`` 不变。

    ``updated_at`` 由这里取请求时刻：仓储照入参原样写入，不自己生成时间
    （``db/repositories/feedback.py`` 的模块说明）。``reasons`` 已在 ``FeedbackIn``
    里去重（去重后最多 5 个）；``comment`` 去空白后为空按 ``None``。运行不存在是
    404 ``RUN_NOT_FOUND``；不校验运行状态。
    """
    _insights_service(request)
    await _require_run(session, run_id)
    feedback = await RunFeedbackRepository(session).upsert(
        RunFeedback(
            run_id=run_id,
            rating=payload.rating,
            reasons=[reason.value for reason in payload.reasons],
            comment=_normalized_comment(payload.comment),
            updated_at=utcnow(),
        )
    )
    await session.commit()
    return feedback_out(feedback)


@router.get(
    "/runs/{run_id}/feedback",
    response_model=RunFeedbackOut,
    summary="读取一次运行的评价",
    responses=error_responses(
        "RUN_NOT_FOUND", "SERVICE_UNAVAILABLE", "UNAUTHORIZED"
    ),
    dependencies=[Depends(require_auth)],
)
async def get_run_feedback(
    request: Request,
    run_id: str = PathParam(min_length=1, max_length=36, description="运行 id"),
    session: AsyncSession = Depends(get_session),
) -> RunFeedbackOut:
    """运行存在但没有评价时返回 ``{"item": null}``；运行不存在才 404。"""
    _insights_service(request)
    await _require_run(session, run_id)
    feedback = await RunFeedbackRepository(session).get_by_run(run_id)
    return RunFeedbackOut(
        item=None if feedback is None else feedback_out(feedback)
    )


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
