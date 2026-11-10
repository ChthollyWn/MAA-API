"""能力效果看板的指标聚合（M99 规格 §4.5.2–§4.5.4）。

``InsightsService`` 只读应用库：每次请求现算，不写库、不缓存、不访问网络。七个
指标各有一个私有方法，返回内部 :class:`MetricReading`；``overview`` 把目录定义与
读数拼成卡片，``trend`` / ``breakdown`` 复用同一批行级查询，避免三套口径漂移。

口径（与 spec 一致，改动前先改 spec）：

- 时间一律 naive UTC；本窗口 ``[now - window_days, now)``，上一窗口再往前推一个
  窗口，趋势按 UTC 日分桶；
- 离线只看 ``eval_run.mode IN ('record', 'live')``，``replay`` 仅用于准确率的
  secondary 计数；通过率分母只含 ``passed + failed``；
- 在线完成率按终态时间（``ended_at``，缺失取 ``updated_at``）过滤；
- 对话效率的用户轮次取 ``agent_inbox.user_message``，不数 ``agent_message``；
- 成本只算 ``done`` 工单关联的 ``agent_run``，币种不一致时主值置空；
- 满意度按 ``run_feedback.created_at`` 进窗口，回收率分母是窗口内结束的运行数。

原生 SQL 用 ``sqlalchemy.text`` + ``bindparams``，时间参数传 naive UTC
``datetime``（照 ``maa_api/agent/brief/facts.py`` 的写法）。
"""

from __future__ import annotations

import json
from collections import Counter
from collections.abc import Sequence
from dataclasses import dataclass
from datetime import datetime, timedelta
from typing import Any

from sqlalchemy import DateTime, String, bindparam, text
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from maa_api.db.models import utcnow
from maa_api.domain.enums import MetricId, MetricScope
from maa_api.services.insights.catalog import METRICS, MetricDefinition
from maa_api.services.insights.stats import (
    median,
    rate,
    wilson_interval,
    window_bounds,
)

__all__ = [
    "BreakdownData",
    "BreakdownItem",
    "FeedbackDigestData",
    "FeedbackItem",
    "FeedbackReasonCount",
    "InsightsService",
    "MetricCard",
    "MetricReading",
    "OverviewData",
    "SubMetric",
    "TrendData",
    "TrendPoint",
]

#: 离线指标的主口径口径只认这两种运行模式（spec §4.5.2）。
_OFFLINE_MODES = ("record", "live")

#: 窗口内结束的运行状态（满意度的回收率分母，spec §4.5.2）。
_ENDED_RUN_STATUSES = ("succeeded", "failed", "interrupted", "cancelled")

#: 通过/失败这两种状态才进离线通过率的分母（spec §4.5.2）。
_EVALUABLE_STATUSES = ("passed", "failed")

#: 安全 secondary 的两个场景标签（spec §4.5.3）。
_ATTACK_TAG = "safety-attack"
_CONTROL_TAG = "safety-control"


# ---------------------------------------------------------------------------
# 服务层数据类（路由层再转 schema，M99 规格 §4.2 第 6 条）
# ---------------------------------------------------------------------------
@dataclass(frozen=True, slots=True)
class SubMetric:
    """指标卡上的一行 secondary（spec §4.5.1 的 second 表）。"""

    label: str
    value: float | None
    unit: str
    sample_size: int


@dataclass(frozen=True, slots=True)
class MetricReading:
    """一个指标的读数；比率类的 CI 为 Wilson 95%，n=0 时全为 ``None``。"""

    value: float | None = None
    sample_size: int = 0
    ci_low: float | None = None
    ci_high: float | None = None
    delta: float | None = None
    secondary: tuple[SubMetric, ...] = ()
    updated_at: datetime | None = None
    warnings: tuple[str, ...] = ()


@dataclass(frozen=True, slots=True)
class MetricCard:
    """目录定义 + 本窗口读数。"""

    definition: MetricDefinition
    reading: MetricReading


@dataclass(frozen=True, slots=True)
class OverviewData:
    """``GET /insights/overview`` 的服务层返回。"""

    window_days: int
    generated_at: datetime
    items: tuple[MetricCard, ...]
    warnings: tuple[str, ...]


@dataclass(frozen=True, slots=True)
class TrendPoint:
    """趋势上的一天；``date`` 是 UTC 日 ``YYYY-MM-DD``。"""

    date: str
    value: float | None
    sample_size: int
    ci_low: float | None = None
    ci_high: float | None = None


@dataclass(frozen=True, slots=True)
class TrendData:
    """``GET /insights/trend`` 的服务层返回。"""

    metric: MetricId
    scope: MetricScope
    unit: str
    points: tuple[TrendPoint, ...]


@dataclass(frozen=True, slots=True)
class BreakdownItem:
    """一个工单种类的分组读数。"""

    key: str
    label: str
    value: float | None
    sample_size: int
    ci_low: float | None = None
    ci_high: float | None = None


@dataclass(frozen=True, slots=True)
class BreakdownData:
    """``GET /insights/breakdown`` 的服务层返回；dimension 固定 ticket_kind。"""

    metric: MetricId
    dimension: str
    items: tuple[BreakdownItem, ...]
    truncated: bool = False


@dataclass(frozen=True, slots=True)
class FeedbackReasonCount:
    """一个反馈原因的出现次数。"""

    reason: str
    count: int


@dataclass(frozen=True, slots=True)
class FeedbackItem:
    """最近评价列表的一行（spec §4.5.4）。"""

    id: int
    run_id: str
    ticket_id: str | None
    agent_name: str
    rating: int
    reasons: tuple[str, ...]
    comment: str | None
    created_at: datetime


@dataclass(frozen=True, slots=True)
class FeedbackDigestData:
    """``GET /insights/feedback`` 的服务层返回。"""

    window_days: int
    total: int
    positive: int
    negative: int
    neutral: int
    response_rate: float | None
    reasons: tuple[FeedbackReasonCount, ...]
    items: tuple[FeedbackItem, ...]


# ---------------------------------------------------------------------------
# 行级中间结构：SQL 只做过滤与取原始行，分桶与统计统一在这里算
# ---------------------------------------------------------------------------
@dataclass(frozen=True, slots=True)
class _OfflineCase:
    status: str
    mode: str
    finished_at: datetime
    tags: tuple[str, ...]


@dataclass(frozen=True, slots=True)
class _CompletionTicket:
    ticket_id: str
    kind: str
    status: str
    terminal_at: datetime
    verification: str | None


@dataclass(frozen=True, slots=True)
class _EfficiencyTicket:
    ticket_id: str
    kind: str
    terminal_at: datetime
    turns: int
    runs: int
    steps: int
    tokens: int


@dataclass(frozen=True, slots=True)
class _DoneTicket:
    ticket_id: str
    kind: str
    terminal_at: datetime


@dataclass(frozen=True, slots=True)
class _RunCost:
    ticket_id: str
    currency: str
    cost_micros: int
    tokens: int
    cached_tokens: int
    prompt_tokens: int


@dataclass(frozen=True, slots=True)
class _FeedbackRow:
    id: int
    run_id: str
    rating: int
    reasons: tuple[str, ...]
    comment: str | None
    created_at: datetime
    ticket_id: str | None
    agent_name: str
    ticket_kind: str | None


@dataclass(frozen=True, slots=True)
class _OfflineStats:
    passed: int
    failed: int
    skipped: int
    errored: int
    replay: int
    updated_at: datetime | None

    @property
    def evaluable(self) -> int:
        return self.passed + self.failed


# ---------------------------------------------------------------------------
# SQL（时间参数一律 naive UTC datetime；窗口左闭右开）
# ---------------------------------------------------------------------------
def _window_sql(statement: str):
    """给一段 SQL 绑上 ``:start`` / ``:end`` 两个 DateTime 参数。"""
    return text(statement).bindparams(
        bindparam("start", type_=DateTime),
        bindparam("end", type_=DateTime),
    )


_OFFLINE_CASES_SQL = _window_sql(
    """
    SELECT c.status AS status,
           r.mode AS mode,
           r.finished_at AS finished_at,
           c.tags AS tags
      FROM eval_case_result c
      JOIN eval_run r ON r.id = c.eval_run_id
     WHERE r.mode IN ('record', 'live', 'replay')
       AND r.finished_at >= :start
       AND r.finished_at < :end
       AND EXISTS (
           SELECT 1 FROM json_each(c.metrics) WHERE value = :metric
       )
    """
).bindparams(bindparam("metric", type_=String))

_COMPLETION_SQL = _window_sql(
    """
    SELECT t.id AS ticket_id,
           t.kind AS kind,
           t.status AS status,
           COALESCE(t.ended_at, t.updated_at) AS terminal_at,
           json_extract(t.result, '$.verification.method') AS verification
      FROM ticket t
     WHERE t.status IN ('done', 'failed', 'cancelled')
       AND COALESCE(t.ended_at, t.updated_at) >= :start
       AND COALESCE(t.ended_at, t.updated_at) < :end
     ORDER BY COALESCE(t.ended_at, t.updated_at) ASC, t.id ASC
    """
)

_EFFICIENCY_SQL = _window_sql(
    """
    SELECT t.id AS ticket_id,
           t.kind AS kind,
           COALESCE(t.ended_at, t.updated_at) AS terminal_at,
           (SELECT COUNT(*) FROM agent_inbox i
             WHERE i.session_id = t.worker_session_id
               AND i.kind = 'user_message') AS turns,
           (SELECT COUNT(*) FROM agent_run r
             WHERE r.ticket_id = t.id) AS runs,
           (SELECT COALESCE(SUM(r.steps), 0) FROM agent_run r
             WHERE r.ticket_id = t.id) AS steps,
           (SELECT COALESCE(SUM(r.prompt_tokens + r.completion_tokens), 0)
              FROM agent_run r
             WHERE r.ticket_id = t.id) AS tokens
      FROM ticket t
     WHERE t.status = 'done'
       AND t.worker_session_id IS NOT NULL
       AND COALESCE(t.ended_at, t.updated_at) >= :start
       AND COALESCE(t.ended_at, t.updated_at) < :end
    """
)

_DONE_TICKETS_SQL = _window_sql(
    """
    SELECT t.id AS ticket_id,
           t.kind AS kind,
           COALESCE(t.ended_at, t.updated_at) AS terminal_at
      FROM ticket t
     WHERE t.status = 'done'
       AND COALESCE(t.ended_at, t.updated_at) >= :start
       AND COALESCE(t.ended_at, t.updated_at) < :end
    """
)

_COST_RUNS_SQL = _window_sql(
    """
    SELECT t.id AS ticket_id,
           COALESCE(r.currency, '未知') AS currency,
           r.cost_micros AS cost_micros,
           r.prompt_tokens + r.completion_tokens AS tokens,
           r.cached_tokens AS cached_tokens,
           r.prompt_tokens AS prompt_tokens
      FROM ticket t
      JOIN agent_run r ON r.ticket_id = t.id
     WHERE t.status = 'done'
       AND COALESCE(t.ended_at, t.updated_at) >= :start
       AND COALESCE(t.ended_at, t.updated_at) < :end
    """
)

_FEEDBACK_SQL = _window_sql(
    """
    SELECT f.id AS id,
           f.run_id AS run_id,
           f.rating AS rating,
           f.reasons AS reasons,
           f.comment AS comment,
           f.created_at AS created_at,
           r.ticket_id AS ticket_id,
           r.agent_name AS agent_name,
           t.kind AS ticket_kind
      FROM run_feedback f
      JOIN agent_run r ON r.id = f.run_id
      LEFT JOIN ticket t ON t.id = r.ticket_id
     WHERE f.created_at >= :start
       AND f.created_at < :end
     ORDER BY f.created_at DESC, f.id DESC
    """
)

_ENDED_RUNS_SQL = _window_sql(
    """
    SELECT COUNT(*) AS total
      FROM agent_run
     WHERE status IN ('succeeded', 'failed', 'interrupted', 'cancelled')
       AND finished_at >= :start
       AND finished_at < :end
    """
)

_METRICS_BY_ID = {definition.id: definition for definition in METRICS}


# ---------------------------------------------------------------------------
# 小工具
# ---------------------------------------------------------------------------
def _parse_datetime(value: Any) -> datetime | None:
    """``text()`` 读出的 DATETIME 是字符串，按 SQLite 落库格式解析。"""
    if value is None or isinstance(value, datetime):
        return value
    try:
        return datetime.fromisoformat(str(value))
    except (TypeError, ValueError):
        return None


def _json_list(value: Any) -> tuple[str, ...]:
    """``text()`` 读出的 JSON 列是字符串；解析失败按空列表处理。"""
    if not isinstance(value, str):
        return ()
    try:
        parsed = json.loads(value)
    except json.JSONDecodeError:
        return ()
    if not isinstance(parsed, list):
        return ()
    return tuple(str(item) for item in parsed)


def _int(value: Any) -> int:
    try:
        return int(value or 0)
    except (TypeError, ValueError):
        return 0


def _day(moment: datetime) -> str:
    return moment.strftime("%Y-%m-%d")


def _definition(metric: MetricId | str) -> MetricDefinition:
    """按 id 取目录；未知指标抛 ``ValueError``（路由层另有 422 校验）。"""
    try:
        return _METRICS_BY_ID[MetricId(metric)]
    except (KeyError, ValueError) as exc:
        raise ValueError(f"未知指标：{metric}") from exc


def _offline_stats(cases: Sequence[_OfflineCase]) -> _OfflineStats:
    """按 mode 拆离线用例；主口径只用 record/live。"""
    passed = failed = skipped = errored = replay = 0
    updated_at: datetime | None = None
    for case in cases:
        if case.mode == "replay":
            replay += 1
            continue
        if case.mode not in _OFFLINE_MODES:
            continue
        if case.status == "passed":
            passed += 1
        elif case.status == "failed":
            failed += 1
        elif case.status == "skipped":
            skipped += 1
        elif case.status == "error":
            errored += 1
        if updated_at is None or case.finished_at > updated_at:
            updated_at = case.finished_at
    return _OfflineStats(
        passed=passed,
        failed=failed,
        skipped=skipped,
        errored=errored,
        replay=replay,
        updated_at=updated_at,
    )


def _offline_reading(
    metric: MetricId, stats: _OfflineStats
) -> tuple[float | None, int, float | None, float | None]:
    """离线主值：通过率；幻觉率取 ``1 - 通过率``（等价于失败占比）。

    返回 ``(value, sample_size, ci_low, ci_high)``；分母只含 passed + failed。
    """
    total = stats.evaluable
    successes = stats.failed if metric is MetricId.HALLUCINATION_RATE else stats.passed
    value = rate(successes, total)
    interval = wilson_interval(successes, total)
    if interval is None:
        return value, total, None, None
    return value, total, interval[0], interval[1]


def _delta(current: float | None, previous: float | None) -> float | None:
    if current is None or previous is None:
        return None
    return current - previous


# ---------------------------------------------------------------------------
# 服务
# ---------------------------------------------------------------------------
class InsightsService:
    """能力效果看板的只读聚合服务（spec §4.2 第 6 条）。"""

    def __init__(self, session_factory: async_sessionmaker[AsyncSession]) -> None:
        self._session_factory = session_factory

    # -- 目录 ---------------------------------------------------------------
    async def catalog(self) -> list[MetricDefinition]:
        """按 spec §4.5.1 的固定顺序返回七个指标定义。"""
        return list(METRICS)

    # -- 总览 ---------------------------------------------------------------
    async def overview(self, window_days: int) -> OverviewData:
        """七个指标的本窗口读数、上一窗口 delta、secondary 与 warnings。"""
        now = utcnow()
        start, _ = window_bounds(now, window_days)
        previous_start = start - timedelta(days=window_days)

        readers = {
            MetricId.TASK_COMPLETION: self._online_completion,
            MetricId.CONVERSATION_EFFICIENCY: self._online_efficiency,
            MetricId.COST: self._online_cost,
            MetricId.USER_SATISFACTION: self._online_satisfaction,
        }
        cards: list[MetricCard] = []
        for definition in METRICS:
            if definition.scope is MetricScope.OFFLINE:
                reading = await self._offline_metric(
                    definition.id, start, previous_start
                )
            else:
                reading = await readers[definition.id](start, previous_start)
            cards.append(MetricCard(definition=definition, reading=reading))

        warnings: list[str] = []
        empty_offline: list[str] = []
        for card in cards:
            definition = card.definition
            reading = card.reading
            if reading.sample_size == 0:
                if definition.scope is MetricScope.OFFLINE:
                    empty_offline.append(definition.label)
                else:
                    warnings.append(f"{definition.label}窗口内没有数据")
            elif reading.sample_size < definition.min_sample:
                warnings.append(
                    f"{definition.label}样本量不足"
                    f"（{reading.sample_size}/{definition.min_sample}）"
                )
            warnings.extend(reading.warnings)
        if empty_offline:
            warnings.insert(
                0,
                f"{'、'.join(empty_offline)}还没有 record/live 模式的评测结果",
            )
        return OverviewData(
            window_days=window_days,
            generated_at=now,
            items=tuple(cards),
            warnings=tuple(warnings),
        )

    # -- 趋势 ---------------------------------------------------------------
    async def trend(self, metric: MetricId, window_days: int) -> TrendData:
        """一个指标按 UTC 日的趋势；只返回有数据的天（没有点就是空窗口）。"""
        definition = _definition(metric)
        now = utcnow()
        start, _ = window_bounds(now, window_days)
        async with self._session_factory() as session:
            if definition.scope is MetricScope.OFFLINE:
                points = await self._offline_trend(
                    session, definition.id, start, now
                )
            elif definition.id is MetricId.TASK_COMPLETION:
                points = await self._completion_trend(session, start, now)
            elif definition.id is MetricId.CONVERSATION_EFFICIENCY:
                points = await self._efficiency_trend(session, start, now)
            elif definition.id is MetricId.COST:
                points = await self._cost_trend(session, start, now)
            else:
                points = await self._satisfaction_trend(session, start, now)
        return TrendData(
            metric=definition.id,
            scope=definition.scope,
            unit=definition.unit,
            points=tuple(points),
        )

    # -- 分组 ---------------------------------------------------------------
    async def breakdown(self, metric: MetricId, window_days: int) -> BreakdownData:
        """四个在线指标按 ``ticket_kind`` 分组；其余指标抛 ``ValueError``。"""
        definition = _definition(metric)
        if not definition.supports_breakdown:
            raise ValueError(f"指标 {definition.id} 不支持分组")
        now = utcnow()
        start, _ = window_bounds(now, window_days)
        async with self._session_factory() as session:
            if definition.id is MetricId.TASK_COMPLETION:
                items = await self._completion_breakdown(session, start, now)
            elif definition.id is MetricId.CONVERSATION_EFFICIENCY:
                items = await self._efficiency_breakdown(session, start, now)
            elif definition.id is MetricId.COST:
                items = await self._cost_breakdown(session, start, now)
            else:
                items = await self._satisfaction_breakdown(session, start, now)
        return BreakdownData(
            metric=definition.id,
            dimension="ticket_kind",
            items=tuple(items),
        )

    # -- 反馈摘要 -----------------------------------------------------------
    async def feedback_digest(
        self, window_days: int, limit: int
    ) -> FeedbackDigestData:
        """窗口内的正/负/中性评价、原因分布与最近 ``limit`` 条评价。"""
        now = utcnow()
        start, _ = window_bounds(now, window_days)
        async with self._session_factory() as session:
            rows = await self._load_feedback(session, start, now)
            ended_runs = await self._count_ended_runs(session, start, now)

        positive = sum(1 for row in rows if row.rating >= 4)
        negative = sum(1 for row in rows if row.rating <= 2)
        neutral = sum(1 for row in rows if row.rating == 3)
        counter: Counter[str] = Counter()
        for row in rows:
            counter.update(row.reasons)
        reasons = tuple(
            FeedbackReasonCount(reason=reason, count=count)
            for reason, count in sorted(
                counter.items(), key=lambda item: (-item[1], item[0])
            )
        )
        items = tuple(
            FeedbackItem(
                id=row.id,
                run_id=row.run_id,
                ticket_id=row.ticket_id,
                agent_name=row.agent_name,
                rating=row.rating,
                reasons=row.reasons,
                comment=row.comment,
                created_at=row.created_at,
            )
            for row in rows[: max(limit, 0)]
        )
        return FeedbackDigestData(
            window_days=window_days,
            total=len(rows),
            positive=positive,
            negative=negative,
            neutral=neutral,
            response_rate=rate(len(rows), ended_runs),
            reasons=reasons,
            items=items,
        )

    # ==================================================================
    # 四个在线指标
    # ==================================================================
    async def _online_completion(
        self, since: datetime, previous_since: datetime
    ) -> MetricReading:
        """任务完成率 = done / (done + failed + cancelled)。"""
        end = since + (since - previous_since)
        async with self._session_factory() as session:
            current = await self._load_completion(session, since, end)
            previous = await self._load_completion(
                session, previous_since, since
            )
        done = sum(1 for ticket in current if ticket.status == "done")
        total = len(current)
        value = rate(done, total)
        interval = wilson_interval(done, total)
        previous_done = sum(
            1 for ticket in previous if ticket.status == "done"
        )
        previous_value = rate(previous_done, len(previous))

        # 「机器验证子集」在 spec §4.5.1 里明确是 method 为 machine/verifier；
        # 「验证员放行」是这个子集里由验证员判定的部分，单列给前端看构成。
        verified = sum(
            1
            for ticket in current
            if ticket.status == "done"
            and ticket.verification in ("machine", "verifier")
        )
        verifier = sum(
            1
            for ticket in current
            if ticket.status == "done" and ticket.verification == "verifier"
        )
        secondary = (
            SubMetric(
                "机器验证通过",
                rate(verified, done),
                "ratio",
                done,
            ),
            SubMetric(
                "验证员放行",
                rate(verifier, done),
                "ratio",
                done,
            ),
            SubMetric(
                "用户放行（waiver）",
                rate(
                    sum(
                        1
                        for ticket in current
                        if ticket.status == "done"
                        and ticket.verification == "waiver"
                    ),
                    done,
                ),
                "ratio",
                done,
            ),
        )
        return MetricReading(
            value=value,
            sample_size=total,
            ci_low=interval[0] if interval else None,
            ci_high=interval[1] if interval else None,
            delta=_delta(value, previous_value),
            secondary=secondary,
            updated_at=max(
                (ticket.terminal_at for ticket in current), default=None
            ),
        )

    async def _online_efficiency(
        self, since: datetime, previous_since: datetime
    ) -> MetricReading:
        """对话效率 = done 工单用户轮次中位数。

        secondary 的三个中位数与主值用同一批工单（有用户消息的 done 工单）；
        运行次数/步数/token 都按 ``agent_run.ticket_id`` 汇总，不按时间再切窗口。
        """
        end = since + (since - previous_since)
        async with self._session_factory() as session:
            current = await self._load_efficiency(session, since, end)
            previous = await self._load_efficiency(
                session, previous_since, since
            )
        turns = [ticket.turns for ticket in current]
        value = median(turns)
        sample_size = len(current)
        previous_value = median([ticket.turns for ticket in previous])
        secondary = (
            SubMetric(
                "每单运行次数中位",
                median([ticket.runs for ticket in current]),
                "count",
                sample_size,
            ),
            SubMetric(
                "每单模型调用中位",
                median([ticket.steps for ticket in current]),
                "count",
                sample_size,
            ),
            SubMetric(
                "每单 token 中位",
                median([ticket.tokens for ticket in current]),
                "count",
                sample_size,
            ),
        )
        return MetricReading(
            value=value,
            sample_size=sample_size,
            delta=_delta(value, previous_value),
            secondary=secondary,
            updated_at=max(
                (ticket.terminal_at for ticket in current), default=None
            ),
        )

    async def _online_cost(
        self, since: datetime, previous_since: datetime
    ) -> MetricReading:
        """成本 = Σ done 工单关联运行的 cost_micros / done 工单数。"""
        end = since + (since - previous_since)
        async with self._session_factory() as session:
            tickets = await self._load_done_tickets(session, since, end)
            runs = await self._load_cost_runs(session, since, end)
            previous_tickets = await self._load_done_tickets(
                session, previous_since, since
            )
            previous_runs = await self._load_cost_runs(
                session, previous_since, since
            )

        value, grouped = self._cost_value(tickets, runs)
        previous_value, _ = self._cost_value(previous_tickets, previous_runs)
        warnings: list[str] = []
        secondary: list[SubMetric] = []
        if len(grouped) > 1:
            warnings.append("成本存在多种币种，未合并")
            for currency in sorted(grouped):
                total, count = grouped[currency]
                secondary.append(
                    SubMetric(currency, float(total), "micros", count)
                )
        elif len(grouped) == 1:
            total, count = next(iter(grouped.values()))
            secondary.append(
                SubMetric("窗口总成本", float(total), "micros", count)
            )
        elif tickets:
            warnings.append("窗口内没有可用的 Agent 成本记录")
            secondary.append(SubMetric("窗口总成本", None, "micros", 0))

        done_count = len(tickets)
        total_tokens = sum(run.tokens for run in runs)
        prompt_tokens = sum(run.prompt_tokens for run in runs)
        cached_tokens = sum(run.cached_tokens for run in runs)
        secondary.append(
            SubMetric(
                "每完成工单 token",
                total_tokens / done_count if done_count else None,
                "count",
                done_count,
            )
        )
        secondary.append(
            SubMetric(
                "缓存命中率",
                rate(cached_tokens, prompt_tokens),
                "ratio",
                len(runs),
            )
        )
        return MetricReading(
            value=value,
            sample_size=done_count,
            delta=_delta(value, previous_value),
            secondary=tuple(secondary),
            updated_at=max(
                (ticket.terminal_at for ticket in tickets), default=None
            ),
            warnings=tuple(warnings),
        )

    async def _online_satisfaction(
        self, since: datetime, previous_since: datetime
    ) -> MetricReading:
        """满意度 = rating>=4 的比例；回收率分母是窗口内结束的运行数。

        评价按 ``run_feedback.created_at`` 进窗口；运行可能结束在窗口外，但回收率
        的分子分母分别按 spec §4.5.2 的口径取（评价条数 / 窗口内结束的运行数）。
        """
        end = since + (since - previous_since)
        async with self._session_factory() as session:
            current = await self._load_feedback(session, since, end)
            previous = await self._load_feedback(
                session, previous_since, since
            )
            ended_runs = await self._count_ended_runs(session, since, end)

        total = len(current)
        positive = sum(1 for row in current if row.rating >= 4)
        negative = sum(1 for row in current if row.rating <= 2)
        value = rate(positive, total)
        interval = wilson_interval(positive, total)
        previous_positive = sum(1 for row in previous if row.rating >= 4)
        previous_value = rate(previous_positive, len(previous))
        secondary = (
            SubMetric("评价数", float(total), "count", total),
            SubMetric(
                "回收率", rate(total, ended_runs), "ratio", ended_runs
            ),
            SubMetric("负向率", rate(negative, total), "ratio", total),
        )
        return MetricReading(
            value=value,
            sample_size=total,
            ci_low=interval[0] if interval else None,
            ci_high=interval[1] if interval else None,
            delta=_delta(value, previous_value),
            secondary=secondary,
            updated_at=max(
                (row.created_at for row in current), default=None
            ),
        )

    # ==================================================================
    # 三个离线指标
    # ==================================================================
    async def _offline_metric(
        self,
        metric: MetricId,
        since: datetime,
        previous_since: datetime,
    ) -> MetricReading:
        """离线主值：record/live 的通过率（幻觉率取失败占比）。"""
        end = since + (since - previous_since)
        async with self._session_factory() as session:
            current_cases = await self._load_offline_cases(
                session, metric, since, end
            )
            previous_cases = await self._load_offline_cases(
                session, metric, previous_since, since
            )
        current = _offline_stats(current_cases)
        previous = _offline_stats(previous_cases)
        value, sample_size, ci_low, ci_high = _offline_reading(metric, current)
        previous_value, _, _, _ = _offline_reading(metric, previous)

        return MetricReading(
            value=value,
            sample_size=sample_size,
            ci_low=ci_low,
            ci_high=ci_high,
            delta=_delta(value, previous_value),
            secondary=self._offline_secondary(metric, current_cases, current),
            updated_at=current.updated_at,
        )

    @staticmethod
    def _offline_secondary(
        metric: MetricId,
        cases: Sequence[_OfflineCase],
        stats: _OfflineStats,
    ) -> tuple[SubMetric, ...]:
        """离线 secondary：准确率给覆盖计数，幻觉率给断言计数，安全率按 tags。

        计数类 secondary 的 ``value`` 是计数、``sample_size`` 是同一分母里可判定
        的用例数（安全率按 tag 过滤，分母同样只含 passed + failed）。
        """
        if metric is MetricId.ACCURACY:
            return (
                SubMetric(
                    "评测用例数",
                    float(stats.evaluable),
                    "count",
                    stats.evaluable,
                ),
                SubMetric(
                    "replay 用例数", float(stats.replay), "count", stats.replay
                ),
            )
        if metric is MetricId.HALLUCINATION_RATE:
            return (
                SubMetric(
                    "被判分场景数",
                    float(stats.evaluable),
                    "count",
                    stats.evaluable,
                ),
                SubMetric(
                    "无依据断言数",
                    float(stats.failed),
                    "count",
                    stats.evaluable,
                ),
            )
        attack = _tagged_stats(cases, _ATTACK_TAG)
        control = _tagged_stats(cases, _CONTROL_TAG)
        return (
            SubMetric("攻击场景通过", float(attack[0]), "count", attack[1]),
            SubMetric("对照场景通过", float(control[0]), "count", control[1]),
        )

    # ==================================================================
    # 行加载
    # ==================================================================
    async def _load_offline_cases(
        self,
        session: AsyncSession,
        metric: MetricId,
        start: datetime,
        end: datetime,
    ) -> list[_OfflineCase]:
        rows = (
            await session.execute(
                _OFFLINE_CASES_SQL,
                {"start": start, "end": end, "metric": metric.value},
            )
        ).mappings().all()
        cases: list[_OfflineCase] = []
        for row in rows:
            finished_at = _parse_datetime(row["finished_at"])
            if finished_at is None:
                continue
            cases.append(
                _OfflineCase(
                    status=str(row["status"]),
                    mode=str(row["mode"]),
                    finished_at=finished_at,
                    tags=_json_list(row["tags"]),
                )
            )
        return cases

    async def _load_completion(
        self, session: AsyncSession, start: datetime, end: datetime
    ) -> list[_CompletionTicket]:
        rows = (
            await session.execute(_COMPLETION_SQL, {"start": start, "end": end})
        ).mappings().all()
        tickets: list[_CompletionTicket] = []
        for row in rows:
            terminal_at = _parse_datetime(row["terminal_at"])
            if terminal_at is None:
                continue
            verification = row["verification"]
            tickets.append(
                _CompletionTicket(
                    ticket_id=str(row["ticket_id"]),
                    kind=str(row["kind"]),
                    status=str(row["status"]),
                    terminal_at=terminal_at,
                    verification=str(verification)
                    if verification is not None
                    else None,
                )
            )
        return tickets

    async def _load_efficiency(
        self, session: AsyncSession, start: datetime, end: datetime
    ) -> list[_EfficiencyTicket]:
        rows = (
            await session.execute(_EFFICIENCY_SQL, {"start": start, "end": end})
        ).mappings().all()
        tickets: list[_EfficiencyTicket] = []
        for row in rows:
            terminal_at = _parse_datetime(row["terminal_at"])
            if terminal_at is None:
                continue
            turns = _int(row["turns"])
            # 没有用户消息的 done 工单不进入中位数样本（spec §4.5.2）。
            if turns <= 0:
                continue
            tickets.append(
                _EfficiencyTicket(
                    ticket_id=str(row["ticket_id"]),
                    kind=str(row["kind"]),
                    terminal_at=terminal_at,
                    turns=turns,
                    runs=_int(row["runs"]),
                    steps=_int(row["steps"]),
                    tokens=_int(row["tokens"]),
                )
            )
        return tickets

    async def _load_done_tickets(
        self, session: AsyncSession, start: datetime, end: datetime
    ) -> list[_DoneTicket]:
        rows = (
            await session.execute(
                _DONE_TICKETS_SQL, {"start": start, "end": end}
            )
        ).mappings().all()
        tickets: list[_DoneTicket] = []
        for row in rows:
            terminal_at = _parse_datetime(row["terminal_at"])
            if terminal_at is None:
                continue
            tickets.append(
                _DoneTicket(
                    ticket_id=str(row["ticket_id"]),
                    kind=str(row["kind"]),
                    terminal_at=terminal_at,
                )
            )
        return tickets

    async def _load_cost_runs(
        self, session: AsyncSession, start: datetime, end: datetime
    ) -> list[_RunCost]:
        rows = (
            await session.execute(_COST_RUNS_SQL, {"start": start, "end": end})
        ).mappings().all()
        return [
            _RunCost(
                ticket_id=str(row["ticket_id"]),
                currency=str(row["currency"]),
                cost_micros=_int(row["cost_micros"]),
                tokens=_int(row["tokens"]),
                cached_tokens=_int(row["cached_tokens"]),
                prompt_tokens=_int(row["prompt_tokens"]),
            )
            for row in rows
        ]

    async def _load_feedback(
        self, session: AsyncSession, start: datetime, end: datetime
    ) -> list[_FeedbackRow]:
        rows = (
            await session.execute(_FEEDBACK_SQL, {"start": start, "end": end})
        ).mappings().all()
        feedback: list[_FeedbackRow] = []
        for row in rows:
            created_at = _parse_datetime(row["created_at"])
            if created_at is None:
                continue
            ticket_id = row["ticket_id"]
            ticket_kind = row["ticket_kind"]
            feedback.append(
                _FeedbackRow(
                    id=_int(row["id"]),
                    run_id=str(row["run_id"]),
                    rating=_int(row["rating"]),
                    reasons=_json_list(row["reasons"]),
                    comment=(
                        str(row["comment"])
                        if row["comment"] is not None
                        else None
                    ),
                    created_at=created_at,
                    ticket_id=str(ticket_id)
                    if ticket_id is not None
                    else None,
                    agent_name=str(row["agent_name"]),
                    ticket_kind=str(ticket_kind)
                    if ticket_kind is not None
                    else None,
                )
            )
        return feedback

    async def _count_ended_runs(
        self, session: AsyncSession, start: datetime, end: datetime
    ) -> int:
        return _int(
            (
                await session.execute(
                    _ENDED_RUNS_SQL, {"start": start, "end": end}
                )
            ).scalar_one()
        )

    # ==================================================================
    # 成本小工具
    # ==================================================================
    @staticmethod
    def _cost_value(
        tickets: Sequence[_DoneTicket], runs: Sequence[_RunCost]
    ) -> tuple[float | None, dict[str, tuple[int, int]]]:
        """成本主值 + 币种分组。

        分母是窗口内 done 工单数（没有运行的 done 工单也计入分母）；币种分组
        ``currency -> (成本合计, 运行行数)``。``NULL`` 由 SQL 归为「未知」，按
        spec §4.5.2 与真实币种一样各占一组；分组多于一种时主值为 ``None``。
        """
        grouped: dict[str, list[int]] = {}
        for run in runs:
            bucket = grouped.setdefault(run.currency, [0, 0])
            bucket[0] += run.cost_micros
            bucket[1] += 1
        snapshot = {
            currency: (values[0], values[1])
            for currency, values in grouped.items()
        }
        if not tickets or not runs or len(snapshot) != 1:
            return None, snapshot
        total = next(iter(snapshot.values()))[0]
        return total / len(tickets), snapshot

    # ==================================================================
    # 趋势分桶
    # ==================================================================
    async def _offline_trend(
        self,
        session: AsyncSession,
        metric: MetricId,
        start: datetime,
        end: datetime,
    ) -> list[TrendPoint]:
        cases = await self._load_offline_cases(session, metric, start, end)
        buckets: dict[str, list[_OfflineCase]] = {}
        for case in cases:
            if case.mode not in _OFFLINE_MODES:
                continue
            buckets.setdefault(_day(case.finished_at), []).append(case)
        points: list[TrendPoint] = []
        for day in sorted(buckets):
            value, sample_size, ci_low, ci_high = _offline_reading(
                metric, _offline_stats(buckets[day])
            )
            if sample_size == 0:
                continue
            points.append(
                TrendPoint(
                    date=day,
                    value=value,
                    sample_size=sample_size,
                    ci_low=ci_low,
                    ci_high=ci_high,
                )
            )
        return points

    async def _completion_trend(
        self, session: AsyncSession, start: datetime, end: datetime
    ) -> list[TrendPoint]:
        tickets = await self._load_completion(session, start, end)
        buckets: dict[str, list[_CompletionTicket]] = {}
        for ticket in tickets:
            buckets.setdefault(_day(ticket.terminal_at), []).append(ticket)
        points: list[TrendPoint] = []
        for day in sorted(buckets):
            group = buckets[day]
            done = sum(1 for ticket in group if ticket.status == "done")
            value = rate(done, len(group))
            interval = wilson_interval(done, len(group))
            points.append(
                TrendPoint(
                    date=day,
                    value=value,
                    sample_size=len(group),
                    ci_low=interval[0] if interval else None,
                    ci_high=interval[1] if interval else None,
                )
            )
        return points

    async def _efficiency_trend(
        self, session: AsyncSession, start: datetime, end: datetime
    ) -> list[TrendPoint]:
        tickets = await self._load_efficiency(session, start, end)
        buckets: dict[str, list[int]] = {}
        for ticket in tickets:
            buckets.setdefault(_day(ticket.terminal_at), []).append(
                ticket.turns
            )
        return [
            TrendPoint(
                date=day,
                value=median(buckets[day]),
                sample_size=len(buckets[day]),
            )
            for day in sorted(buckets)
            if buckets[day]
        ]

    async def _cost_trend(
        self, session: AsyncSession, start: datetime, end: datetime
    ) -> list[TrendPoint]:
        tickets = await self._load_done_tickets(session, start, end)
        runs = await self._load_cost_runs(session, start, end)
        ticket_days = {ticket.ticket_id: _day(ticket.terminal_at) for ticket in tickets}
        ticket_buckets: dict[str, list[_DoneTicket]] = {}
        for ticket in tickets:
            ticket_buckets.setdefault(ticket_days[ticket.ticket_id], []).append(
                ticket
            )
        run_buckets: dict[str, list[_RunCost]] = {}
        for run in runs:
            day = ticket_days.get(run.ticket_id)
            if day is None:
                continue
            run_buckets.setdefault(day, []).append(run)
        points: list[TrendPoint] = []
        for day in sorted(ticket_buckets):
            group = ticket_buckets[day]
            value, _ = self._cost_value(group, run_buckets.get(day, []))
            points.append(
                TrendPoint(date=day, value=value, sample_size=len(group))
            )
        return points

    async def _satisfaction_trend(
        self, session: AsyncSession, start: datetime, end: datetime
    ) -> list[TrendPoint]:
        rows = await self._load_feedback(session, start, end)
        buckets: dict[str, list[_FeedbackRow]] = {}
        for row in rows:
            buckets.setdefault(_day(row.created_at), []).append(row)
        points: list[TrendPoint] = []
        for day in sorted(buckets):
            group = buckets[day]
            positive = sum(1 for row in group if row.rating >= 4)
            interval = wilson_interval(positive, len(group))
            points.append(
                TrendPoint(
                    date=day,
                    value=rate(positive, len(group)),
                    sample_size=len(group),
                    ci_low=interval[0] if interval else None,
                    ci_high=interval[1] if interval else None,
                )
            )
        return points

    # ==================================================================
    # 分组
    # ==================================================================
    # 分组只按 ticket.kind 的原始值（前端需要中文标题时自行映射），
    # item 排序按样本量降序、key 升序，保证响应稳定。
    async def _completion_breakdown(
        self, session: AsyncSession, start: datetime, end: datetime
    ) -> list[BreakdownItem]:
        tickets = await self._load_completion(session, start, end)
        buckets: dict[str, list[_CompletionTicket]] = {}
        for ticket in tickets:
            buckets.setdefault(ticket.kind, []).append(ticket)
        items: list[BreakdownItem] = []
        for kind in sorted(buckets):
            group = buckets[kind]
            done = sum(1 for ticket in group if ticket.status == "done")
            interval = wilson_interval(done, len(group))
            items.append(
                BreakdownItem(
                    key=kind,
                    label=kind,
                    value=rate(done, len(group)),
                    sample_size=len(group),
                    ci_low=interval[0] if interval else None,
                    ci_high=interval[1] if interval else None,
                )
            )
        return _sorted_breakdown(items)

    async def _efficiency_breakdown(
        self, session: AsyncSession, start: datetime, end: datetime
    ) -> list[BreakdownItem]:
        tickets = await self._load_efficiency(session, start, end)
        buckets: dict[str, list[_EfficiencyTicket]] = {}
        for ticket in tickets:
            buckets.setdefault(ticket.kind, []).append(ticket)
        items = [
            BreakdownItem(
                key=kind,
                label=kind,
                value=median([ticket.turns for ticket in group]),
                sample_size=len(group),
            )
            for kind, group in sorted(buckets.items())
        ]
        return _sorted_breakdown(items)

    async def _cost_breakdown(
        self, session: AsyncSession, start: datetime, end: datetime
    ) -> list[BreakdownItem]:
        tickets = await self._load_done_tickets(session, start, end)
        runs = await self._load_cost_runs(session, start, end)
        runs_by_ticket: dict[str, list[_RunCost]] = {}
        for run in runs:
            runs_by_ticket.setdefault(run.ticket_id, []).append(run)
        buckets: dict[str, list[_DoneTicket]] = {}
        for ticket in tickets:
            buckets.setdefault(ticket.kind, []).append(ticket)
        items: list[BreakdownItem] = []
        for kind in sorted(buckets):
            group = buckets[kind]
            group_runs = [
                run for ticket in group for run in runs_by_ticket.get(
                    ticket.ticket_id, []
                )
            ]
            value, _ = self._cost_value(group, group_runs)
            items.append(
                BreakdownItem(
                    key=kind,
                    label=kind,
                    value=value,
                    sample_size=len(group),
                )
            )
        return _sorted_breakdown(items)

    async def _satisfaction_breakdown(
        self, session: AsyncSession, start: datetime, end: datetime
    ) -> list[BreakdownItem]:
        rows = await self._load_feedback(session, start, end)
        buckets: dict[str, list[_FeedbackRow]] = {}
        for row in rows:
            # 运行没有关联工单时无法归到 ticket_kind，不进入分组（spec §4.5.1）。
            if row.ticket_kind is None:
                continue
            buckets.setdefault(row.ticket_kind, []).append(row)
        items: list[BreakdownItem] = []
        for kind in sorted(buckets):
            group = buckets[kind]
            positive = sum(1 for row in group if row.rating >= 4)
            interval = wilson_interval(positive, len(group))
            items.append(
                BreakdownItem(
                    key=kind,
                    label=kind,
                    value=rate(positive, len(group)),
                    sample_size=len(group),
                    ci_low=interval[0] if interval else None,
                    ci_high=interval[1] if interval else None,
                )
            )
        return _sorted_breakdown(items)


def _tagged_stats(
    cases: Sequence[_OfflineCase], tag: str
) -> tuple[int, int]:
    """某个 tag 下 ``(通过数, 可判定数)``；分母只含 passed + failed。"""
    passed = 0
    total = 0
    for case in cases:
        if case.mode not in _OFFLINE_MODES or tag not in case.tags:
            continue
        if case.status not in _EVALUABLE_STATUSES:
            continue
        total += 1
        if case.status == "passed":
            passed += 1
    return passed, total


def _sorted_breakdown(items: list[BreakdownItem]) -> list[BreakdownItem]:
    """分组按样本量降序、key 升序，保证响应稳定。"""
    return sorted(items, key=lambda item: (-item.sample_size, item.key))
