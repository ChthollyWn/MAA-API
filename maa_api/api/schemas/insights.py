"""能力效果看板的请求/响应模型（M99 规格 §4.3）。

目录、总览、趋势、分组与反馈摘要的形状在规格里是逐字段定死的：这里只做
字段搬运，不重新计算口径。时间字段对外一律是带 ``Z`` 的 ISO 8601 字符串
（库里存 naive UTC，照 ``runs.py`` 的 ``_utc_z`` 补齐时区）。

服务层数据类（:mod:`maa_api.services.insights`）与对外模型刻意分开：服务层
``dataclass`` 用于聚合，路由层经本模块的 ``*_out`` 函数转换，避免 ORM / 数据类
字段直接漏进 OpenAPI。
"""

from __future__ import annotations

from datetime import UTC, datetime
from typing import TYPE_CHECKING, Any

from pydantic import BaseModel, ConfigDict

from maa_api.domain.enums import (
    EvalCaseStatus,
    EvalRunMode,
    EvidenceGrade,
    FeedbackReason,
    MetricId,
    MetricScope,
)
from maa_api.services.insights import (
    BreakdownData,
    FeedbackDigestData,
    MetricCard,
    MetricDefinition,
    OverviewData,
    SubMetric,
    TrendData,
)

if TYPE_CHECKING:
    from maa_api.db.models import EvalCaseResult, EvalRun
    from maa_api.db.repositories.base import Page

__all__ = [
    "BreakdownItemOut",
    "BreakdownOut",
    "EvalCaseOut",
    "EvalMetricRateOut",
    "EvalRunDetailOut",
    "EvalRunPage",
    "EvalRunSummaryOut",
    "FeedbackDigestOut",
    "InsightFeedbackItemOut",
    "MetricCardOut",
    "MetricCatalogOut",
    "MetricDefinitionOut",
    "OverviewOut",
    "SubMetricOut",
    "TrendOut",
    "TrendPointOut",
    "breakdown_out",
    "eval_case_out",
    "eval_run_detail_out",
    "eval_run_page_out",
    "eval_run_summary_out",
    "feedback_digest_out",
    "metric_card_out",
    "metric_definition_out",
    "overview_out",
    "trend_out",
]


def _utc_z(value: datetime) -> str:
    """把库里的 naive UTC 时间渲染成 ``...Z``（照 ``runs.py`` 的 ``_utc_z``）。"""
    if value.tzinfo is None:
        value = value.replace(tzinfo=UTC)
    return value.astimezone(UTC).isoformat().replace("+00:00", "Z")


# ----------------------------------------------------------------------
# 目录
# ----------------------------------------------------------------------
class MetricDefinitionOut(BaseModel):
    """一个指标的口径与展示文案（spec §4.3 / §4.5.1）。"""

    model_config = ConfigDict(extra="forbid")

    id: MetricId
    label: str
    scope: MetricScope
    unit: str
    definition: str
    formula: str
    source: str
    evaluation: str
    evidence: EvidenceGrade
    min_sample: int
    limitations: list[str]
    supports_trend: bool
    supports_breakdown: bool


class MetricCatalogOut(BaseModel):
    """``GET /insights/catalog`` 的响应：顺序与 §4.5.1 相同。"""

    model_config = ConfigDict(extra="forbid")

    items: list[MetricDefinitionOut]


def metric_definition_out(definition: MetricDefinition) -> MetricDefinitionOut:
    """目录项逐字段搬运；``limitations`` 从 tuple 转 list。"""
    return MetricDefinitionOut(
        id=definition.id,
        label=definition.label,
        scope=definition.scope,
        unit=definition.unit,
        definition=definition.definition,
        formula=definition.formula,
        source=definition.source,
        evaluation=definition.evaluation,
        evidence=definition.evidence,
        min_sample=definition.min_sample,
        limitations=list(definition.limitations),
        supports_trend=definition.supports_trend,
        supports_breakdown=definition.supports_breakdown,
    )


# ----------------------------------------------------------------------
# 总览
# ----------------------------------------------------------------------
class SubMetricOut(BaseModel):
    """指标卡上的一行 secondary（spec §4.5.1 的 secondary 表）。"""

    model_config = ConfigDict(extra="forbid")

    label: str
    value: float | None
    unit: str
    sample_size: int


class MetricCardOut(BaseModel):
    """一个指标的本窗口读数；无数据时 ``value``/CI/``delta`` 为 ``None``。"""

    model_config = ConfigDict(extra="forbid")

    id: MetricId
    label: str
    scope: MetricScope
    unit: str
    value: float | None
    sample_size: int
    ci_low: float | None
    ci_high: float | None
    delta: float | None
    evidence: EvidenceGrade
    updated_at: str | None
    secondary: list[SubMetricOut]


class OverviewOut(BaseModel):
    """``GET /insights/overview`` 的响应。"""

    model_config = ConfigDict(extra="forbid")

    window_days: int
    generated_at: str
    items: list[MetricCardOut]
    warnings: list[str]


def _sub_metric_out(metric: SubMetric) -> SubMetricOut:
    return SubMetricOut(
        label=metric.label,
        value=metric.value,
        unit=metric.unit,
        sample_size=metric.sample_size,
    )


def metric_card_out(card: MetricCard) -> MetricCardOut:
    """目录定义 + 读数拼成一张卡；``updated_at`` 缺失时是 ``None``。"""
    definition = card.definition
    reading = card.reading
    return MetricCardOut(
        id=definition.id,
        label=definition.label,
        scope=definition.scope,
        unit=definition.unit,
        value=reading.value,
        sample_size=reading.sample_size,
        ci_low=reading.ci_low,
        ci_high=reading.ci_high,
        delta=reading.delta,
        evidence=definition.evidence,
        updated_at=(
            None if reading.updated_at is None else _utc_z(reading.updated_at)
        ),
        secondary=[_sub_metric_out(item) for item in reading.secondary],
    )


def overview_out(data: OverviewData) -> OverviewOut:
    return OverviewOut(
        window_days=data.window_days,
        generated_at=_utc_z(data.generated_at),
        items=[metric_card_out(card) for card in data.items],
        warnings=list(data.warnings),
    )


# ----------------------------------------------------------------------
# 趋势与分组
# ----------------------------------------------------------------------
class TrendPointOut(BaseModel):
    """趋势上的一天；``date`` 是 UTC 日 ``YYYY-MM-DD``。"""

    model_config = ConfigDict(extra="forbid")

    date: str
    value: float | None
    sample_size: int
    ci_low: float | None
    ci_high: float | None


class TrendOut(BaseModel):
    """``GET /insights/trend`` 的响应；没有数据时 ``points`` 为空。"""

    model_config = ConfigDict(extra="forbid")

    metric: MetricId
    scope: MetricScope
    unit: str
    points: list[TrendPointOut]


class BreakdownItemOut(BaseModel):
    """一个工单种类的分组读数。"""

    model_config = ConfigDict(extra="forbid")

    key: str
    label: str
    value: float | None
    sample_size: int
    ci_low: float | None
    ci_high: float | None


class BreakdownOut(BaseModel):
    """``GET /insights/breakdown`` 的响应；``dimension`` 固定 ``ticket_kind``。"""

    model_config = ConfigDict(extra="forbid")

    metric: MetricId
    dimension: str
    items: list[BreakdownItemOut]
    truncated: bool


def trend_out(data: TrendData) -> TrendOut:
    return TrendOut(
        metric=data.metric,
        scope=data.scope,
        unit=data.unit,
        points=[
            TrendPointOut(
                date=point.date,
                value=point.value,
                sample_size=point.sample_size,
                ci_low=point.ci_low,
                ci_high=point.ci_high,
            )
            for point in data.points
        ],
    )


def breakdown_out(data: BreakdownData) -> BreakdownOut:
    return BreakdownOut(
        metric=data.metric,
        dimension=data.dimension,
        items=[
            BreakdownItemOut(
                key=item.key,
                label=item.label,
                value=item.value,
                sample_size=item.sample_size,
                ci_low=item.ci_low,
                ci_high=item.ci_high,
            )
            for item in data.items
        ],
        truncated=data.truncated,
    )


# ----------------------------------------------------------------------
# 评测运行
# ----------------------------------------------------------------------
#: grader ``details`` 的快照上限（规格 §4.5.3）：发布时已截一次，读侧再兜一层，
#: 防止旧数据或外部写入把超长明细带进响应。
_MAX_GRADER_DETAILS = 5
_MAX_GRADER_DETAIL_CHARS = 256


class EvalRunSummaryOut(BaseModel):
    """一次评测运行的汇总（spec §4.3）；时间字段是带 ``Z`` 的 ISO 8601。"""

    model_config = ConfigDict(extra="forbid")

    id: str
    suite: str
    mode: EvalRunMode
    started_at: str
    finished_at: str
    duration_ms: int
    total: int
    passed: int
    failed: int
    error: int
    skipped: int
    pass_rate: float
    model_calls: int
    cost_micros: int
    currency: str | None


class EvalRunPage(BaseModel):
    """``GET /evals/runs`` 的分页信封；``total`` 是过滤后的总数。"""

    model_config = ConfigDict(extra="forbid")

    items: list[EvalRunSummaryOut]
    total: int
    page: int
    size: int


class EvalMetricRateOut(BaseModel):
    """这次运行自身某指标的可判定通过计数与比率（spec §4.5.3）。"""

    model_config = ConfigDict(extra="forbid")

    metric: MetricId
    passed: int
    total: int
    rate: float | None
    ci_low: float | None
    ci_high: float | None


class EvalCaseOut(BaseModel):
    """一个评测用例的判分快照（spec §4.3）。"""

    model_config = ConfigDict(extra="forbid")

    scenario: str
    title: str
    suite: str
    agent: str
    metrics: list[MetricId]
    tags: list[str]
    status: EvalCaseStatus
    score: float | None
    attempt: int
    duration_ms: int
    model_calls: int
    cost_micros: int
    currency: str | None
    graders: list[dict[str, Any]]
    error: str | None


class EvalRunDetailOut(EvalRunSummaryOut):
    """``GET /evals/runs/{run_id}`` 的响应：summary + 指标率 + 用例。"""

    model_config = ConfigDict(extra="forbid")

    metric_rates: list[EvalMetricRateOut]
    cases: list[EvalCaseOut]
    #: ``cases`` 超过 500 条被截断时为 true（spec §4.5.5）。
    cases_truncated: bool


def _eval_run_summary_fields(run: EvalRun) -> dict[str, Any]:
    """summary 的 15 个字段；detail 复用同一份，避免两处字段漂移。"""
    return {
        "id": run.id,
        "suite": run.suite,
        "mode": EvalRunMode(run.mode),
        "started_at": _utc_z(run.started_at),
        "finished_at": _utc_z(run.finished_at),
        "duration_ms": run.duration_ms,
        "total": run.total,
        "passed": run.passed,
        "failed": run.failed,
        "error": run.error,
        "skipped": run.skipped,
        "pass_rate": run.pass_rate,
        "model_calls": run.model_calls,
        "cost_micros": run.cost_micros,
        "currency": run.currency,
    }


def eval_run_summary_out(run: EvalRun) -> EvalRunSummaryOut:
    return EvalRunSummaryOut(**_eval_run_summary_fields(run))


def eval_run_page_out(page: Page[EvalRun]) -> EvalRunPage:
    """分页信封逐字段搬运；``items`` 里每行转成 summary。"""
    return EvalRunPage(
        items=[eval_run_summary_out(run) for run in page.items],
        total=page.total,
        page=page.page,
        size=page.size,
    )


def _grader_out(grader: dict[str, Any]) -> dict[str, Any]:
    """单个判分器快照：``details`` 最多 5 条、每条截 256 字。"""
    payload = dict(grader)
    details = payload.get("details")
    if isinstance(details, list):
        payload["details"] = [
            str(detail)[:_MAX_GRADER_DETAIL_CHARS]
            for detail in details[:_MAX_GRADER_DETAILS]
        ]
    return payload


def eval_case_out(case: EvalCaseResult) -> EvalCaseOut:
    """用例快照；``metrics`` / ``status`` 从库里的字符串还原成枚举。"""
    return EvalCaseOut(
        scenario=case.scenario,
        title=case.title,
        suite=case.suite,
        agent=case.agent,
        metrics=[MetricId(metric) for metric in case.metrics],
        tags=list(case.tags),
        status=EvalCaseStatus(case.status),
        score=case.score,
        attempt=case.attempt,
        duration_ms=case.duration_ms,
        model_calls=case.model_calls,
        cost_micros=case.cost_micros,
        currency=case.currency,
        graders=[_grader_out(grader) for grader in case.graders],
        error=case.error,
    )


def eval_run_detail_out(
    run: EvalRun,
    cases: list[EvalCaseOut],
    metric_rates: list[EvalMetricRateOut],
    cases_truncated: bool,
) -> EvalRunDetailOut:
    """详情 = summary + 调用方算好的指标率 + 已截断的用例列表。"""
    return EvalRunDetailOut(
        **_eval_run_summary_fields(run),
        metric_rates=list(metric_rates),
        cases=list(cases),
        cases_truncated=cases_truncated,
    )


# ----------------------------------------------------------------------
# 反馈摘要
# ----------------------------------------------------------------------
class InsightFeedbackItemOut(BaseModel):
    """最近评价列表的一行（spec §4.5.4）。"""

    model_config = ConfigDict(extra="forbid")

    id: int
    run_id: str
    ticket_id: str | None
    agent_name: str
    rating: int
    reasons: list[FeedbackReason]
    comment: str | None
    created_at: str


class FeedbackDigestOut(BaseModel):
    """``GET /insights/feedback`` 的响应。"""

    model_config = ConfigDict(extra="forbid")

    window_days: int
    total: int
    positive: int
    negative: int
    neutral: int
    response_rate: float | None
    reasons: list[dict[str, Any]]
    items: list[InsightFeedbackItemOut]


def feedback_digest_out(data: FeedbackDigestData) -> FeedbackDigestOut:
    return FeedbackDigestOut(
        window_days=data.window_days,
        total=data.total,
        positive=data.positive,
        negative=data.negative,
        neutral=data.neutral,
        response_rate=data.response_rate,
        reasons=[
            {"reason": reason.reason, "count": reason.count}
            for reason in data.reasons
        ],
        items=[
            InsightFeedbackItemOut(
                id=item.id,
                run_id=item.run_id,
                ticket_id=item.ticket_id,
                agent_name=item.agent_name,
                rating=item.rating,
                reasons=list(item.reasons),
                comment=item.comment,
                created_at=_utc_z(item.created_at),
            )
            for item in data.items
        ],
    )
