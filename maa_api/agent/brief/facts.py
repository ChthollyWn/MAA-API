"""每日简报的事实收集（M84 规格 §4.5.10）。

``collect_facts(app, now)`` 一次读齐渲染与 analyst 工单要用的数据。每一块单独
``try``：某一块读失败只记 ``debug``、该块留空，整份简报照常生成（规格 §4.5.13
「活动、卡池、成本读取失败 → 对应小节或行省略，其他照常」）。

口径：

- 「今日」与「昨日」都按消耗日（北京时间 04:00 分日，:func:`spend_day_window`），
  与消耗账本同一天界。
- 跨里程碑的表（``ticket`` / ``goal`` / ``goal_milestone`` / ``cost_ledger`` /
  ``watchdog_alert`` / ``memory_item`` / ``trigger_observation``）只用
  ``sqlalchemy.text()`` 读 03 定死的表名列名，不 import 它们的模型类。
- 养成计划直接读 ``upgrade_plan`` 行，不调 ``list_plans()``——那会重算并写库。
"""

from __future__ import annotations

import logging
from collections.abc import Awaitable, Callable, Mapping
from dataclasses import dataclass
from datetime import UTC, date, datetime, timedelta
from typing import Any
from zoneinfo import ZoneInfo

from sqlalchemy import DateTime, String, bindparam, func, select, text
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from maa_api.agent.knowledge import render_activity_section, render_gacha_lines
from maa_api.agent.spend import SPEND_TIMEZONE, spend_day_window
from maa_api.db.models import (
    Pipeline,
    SanityObservation,
    Schedule,
    StageDrop,
    UpgradePlan,
)
from maa_api.domain.enums import PipelineStatus
from maa_api.services.game.activities import report_from_snapshot

__all__ = ["BriefFacts", "collect_facts"]

logger = logging.getLogger(__name__)

WEEKDAYS: tuple[str, ...] = ("周一", "周二", "周三", "周四", "周五", "周六", "周日")

#: 「昨日」窗口里各类状态的固定顺序与中文说明。
_PIPELINE_STATUSES: tuple[str, ...] = (
    PipelineStatus.COMPLETED.value,
    PipelineStatus.FAILED.value,
    PipelineStatus.CANCELLED.value,
)
_TICKET_STATUSES: tuple[str, ...] = ("done", "failed", "cancelled")

#: 没装配活动索引 / 卡池服务时的事实（测试与最小应用）。
_EMPTY_PENDING: dict[str, Any] = {
    "confirmations": 0,
    "questions": 0,
    "tickets": [],
    "tickets_total": 0,
    "alerts": 0,
    "memories": 0,
    "observations": 0,
}

_TICKET_COUNT_SQL = text(
    "SELECT status, COUNT(*) AS total FROM ticket "
    "WHERE ended_at >= :start AND ended_at < :end GROUP BY status"
).bindparams(bindparam("start", type_=DateTime), bindparam("end", type_=DateTime))

_TICKET_TITLE_SQL = text(
    "SELECT title FROM ticket "
    "WHERE ended_at >= :start AND ended_at < :end "
    "ORDER BY ended_at DESC LIMIT 3"
).bindparams(bindparam("start", type_=DateTime), bindparam("end", type_=DateTime))

_COST_SQL = text(
    "SELECT currency, SUM(cost_micros) AS total FROM cost_ledger "
    "WHERE created_at >= :start AND created_at < :end GROUP BY currency"
).bindparams(bindparam("start", type_=DateTime), bindparam("end", type_=DateTime))

_GOAL_SQL = text(
    "SELECT id, title, deadline FROM goal "
    "WHERE status = 'active' ORDER BY updated_at DESC LIMIT 5"
)

_GOAL_MILESTONE_SQL = text(
    "SELECT goal_id, COUNT(*) AS total, "
    "SUM(CASE WHEN status = 'done' THEN 1 ELSE 0 END) AS done "
    "FROM goal_milestone WHERE goal_id IN :ids GROUP BY goal_id"
).bindparams(bindparam("ids", expanding=True, type_=String))

_TICKET_PENDING_SQL = text(
    "SELECT title FROM ticket WHERE status IN ('waiting', 'blocked') "
    "ORDER BY updated_at DESC LIMIT 3"
)
_TICKET_PENDING_TOTAL_SQL = text(
    "SELECT COUNT(*) FROM ticket WHERE status IN ('waiting', 'blocked')"
)
_CONFIRMATION_PENDING_SQL = text(
    "SELECT COUNT(*) FROM confirmation WHERE status = 'pending'"
)
_QUESTION_PENDING_SQL = text(
    "SELECT COUNT(*) FROM agent_question WHERE status = 'pending'"
)
_ALERT_PENDING_SQL = text(
    "SELECT COUNT(*) FROM watchdog_alert "
    "WHERE resolved_at IS NULL AND severity = 'critical'"
)
_MEMORY_PENDING_SQL = text(
    "SELECT COUNT(*) FROM memory_item WHERE status = 'proposed'"
)
_OBSERVATION_PENDING_SQL = text(
    "SELECT COUNT(*) FROM trigger_observation WHERE verdict IS NULL"
)


@dataclass(frozen=True, slots=True)
class BriefFacts:
    """渲染一份简报所需的全部事实（规格 §4.5.10）。"""

    date: date
    weekday: str
    sanity: dict[str, Any] | None
    schedules_today: list[dict[str, Any]]
    activity_lines: list[str]
    gacha_lines: list[str]
    plans: list[dict[str, Any]]
    goals: list[dict[str, Any]]
    pipelines_yesterday: dict[str, Any]
    drops_yesterday: list[dict[str, Any]]
    tickets_yesterday: dict[str, Any]
    cost_yesterday: dict[str, int]
    spend_today: list[dict[str, Any]]
    spend_yesterday: dict[str, int]
    pending: dict[str, Any]


# ---------------------------------------------------------------------------
# 小工具
# ---------------------------------------------------------------------------
def _naive_utc(moment: datetime) -> datetime:
    """统一成库里用的 naive UTC。"""
    if moment.tzinfo is None:
        return moment
    return moment.astimezone(UTC).replace(tzinfo=None)


def _to_local(moment: datetime) -> datetime:
    """naive UTC → 北京时间（简报的「local」按国服口径）。"""
    return _naive_utc(moment).replace(tzinfo=UTC).astimezone(
        ZoneInfo(SPEND_TIMEZONE)
    )


def _state(app: Any) -> Any:
    return getattr(app, "state", None)


def _factory(app: Any) -> async_sessionmaker[AsyncSession] | None:
    return getattr(_state(app), "session_factory", None)


def _zone(name: Any) -> ZoneInfo:
    try:
        return ZoneInfo(str(name or SPEND_TIMEZONE))
    except Exception:  # noqa: BLE001 - 非法时区退化到国服口径，不拖垮整块
        return ZoneInfo(SPEND_TIMEZONE)


def _clip(value: Any, limit: int) -> str:
    return str(value or "").strip()[:limit]


def _month_day(value: date) -> str:
    return f"{value.month} 月 {value.day} 日"


def _parse_datetime(value: Any) -> datetime | None:
    """``text()`` 读出来的 DATETIME 是字符串，按 SQLite 的落库格式解析。"""
    if value is None:
        return None
    if isinstance(value, datetime):
        return value
    try:
        return datetime.fromisoformat(str(value))
    except (TypeError, ValueError):
        return None


def _number(value: Any) -> int:
    try:
        return int(value)
    except (TypeError, ValueError):
        return 0


async def _block(
    label: str,
    loader: Callable[..., Awaitable[Any]],
    *args: Any,
    default: Any,
) -> Any:
    """跑一块事实；失败记 ``debug`` 并返回空值（规格 §4.5.10）。"""
    try:
        return await loader(*args)
    except Exception:  # noqa: BLE001 - 某一块读失败不能让整份简报失败
        logger.debug("简报事实读取失败：%s", label, exc_info=True)
        return default() if callable(default) else default


# ---------------------------------------------------------------------------
# 各块
# ---------------------------------------------------------------------------
async def _sanity_block(app: Any, now: datetime) -> dict[str, Any] | None:
    """最近一条 7 天内的采样 → 估算与回满时刻（规格 §4.5.10）。"""
    factory = _factory(app)
    if factory is None:
        return None
    async with factory() as session:
        row = (
            (
                await session.execute(
                    select(SanityObservation)
                    .where(
                        SanityObservation.created_at
                        >= now - timedelta(days=7)
                    )
                    .order_by(
                        SanityObservation.created_at.desc(),
                        SanityObservation.id.desc(),
                    )
                    .limit(1)
                )
            )
            .scalars()
            .first()
        )
    if row is None:
        return None
    observed = _naive_utc(row.created_at)
    maximum = _number(row.max_sanity)
    current = _number(row.current_sanity)
    elapsed = max(0, int((now - observed).total_seconds() // 60))
    estimated = min(maximum, current + elapsed // 6)
    full_at = observed + timedelta(
        minutes=6 * max(maximum - current, 0)
    )
    return {
        "estimated": estimated,
        "max": maximum,
        "full_at_local": _to_local(full_at).strftime("%H:%M"),
        "sampled_at_local": _to_local(observed).strftime("%m-%d %H:%M"),
    }


async def _schedules_block(
    app: Any, now: datetime, today_end: datetime
) -> list[dict[str, Any]]:
    factory = _factory(app)
    if factory is None:
        return []
    async with factory() as session:
        rows = (
            (
                await session.execute(
                    select(Schedule)
                    .where(
                        Schedule.enabled.is_(True),
                        Schedule.next_run_at.is_not(None),
                        Schedule.next_run_at >= now,
                        Schedule.next_run_at < today_end,
                    )
                    .order_by(Schedule.next_run_at.asc())
                )
            )
            .scalars()
            .all()
        )
    out: list[dict[str, Any]] = []
    for row in rows:
        at = _naive_utc(row.next_run_at).replace(tzinfo=UTC).astimezone(
            _zone(row.timezone)
        )
        template = row.template or []
        fight = any(
            isinstance(item, Mapping) and item.get("name") == "Fight"
            for item in template
        )
        out.append(
            {
                "name": row.name,
                "at_local": at.strftime("%H:%M"),
                "fight": fight,
            }
        )
    return out


async def _activity_block(app: Any) -> list[str]:
    index = getattr(_state(app), "activity_index", None)
    if index is None:
        return []
    report = report_from_snapshot(index.snapshot())
    return render_activity_section(report)[:12]


async def _gacha_block(app: Any) -> list[str]:
    gacha = getattr(_state(app), "gacha_service", None)
    if gacha is None:
        return []
    summary = await gacha.pools()
    return render_gacha_lines(summary)[:6]


async def _plans_block(app: Any) -> list[dict[str, Any]]:
    factory = _factory(app)
    if factory is None:
        return []
    async with factory() as session:
        rows = (
            (
                await session.execute(
                    select(UpgradePlan)
                    .where(UpgradePlan.status == "active")
                    .order_by(UpgradePlan.created_at.asc())
                )
            )
            .scalars()
            .all()
        )
    out: list[dict[str, Any]] = []
    for row in rows:
        progress = row.progress or {}
        missing = progress.get("missing") if isinstance(progress, Mapping) else None
        counts = {
            str(name): _number(quantity)
            for name, quantity in (missing or {}).items()
        }
        counts = {name: count for name, count in counts.items() if count > 0}
        days = progress.get("days_estimated") if isinstance(progress, Mapping) else None
        item: dict[str, Any] = {
            "name": row.name,
            "missing_kinds": len(counts),
            "missing_total": sum(counts.values()),
        }
        if isinstance(days, (int, float)) and days > 0:
            item["days_estimated"] = float(days)
        out.append(item)
    return out


async def _goals_block(app: Any) -> list[dict[str, Any]]:
    factory = _factory(app)
    if factory is None:
        return []
    async with factory() as session:
        rows = (await session.execute(_GOAL_SQL)).mappings().all()
        ids = [str(row["id"]) for row in rows]
        milestones: dict[str, tuple[int, int]] = {}
        if ids:
            for row in (
                await session.execute(_GOAL_MILESTONE_SQL, {"ids": ids})
            ).mappings().all():
                milestones[str(row["goal_id"])] = (
                    _number(row["done"]),
                    _number(row["total"]),
                )
    out: list[dict[str, Any]] = []
    for row in rows:
        done, total = milestones.get(str(row["id"]), (0, 0))
        deadline = _parse_datetime(row["deadline"])
        out.append(
            {
                "title": row["title"],
                "deadline_local": (
                    _month_day(_to_local(deadline).date())
                    if deadline is not None
                    else None
                ),
                "done": done,
                "total": total,
            }
        )
    return out


async def _pipelines_block(
    app: Any, start: datetime, end: datetime
) -> dict[str, Any]:
    factory = _factory(app)
    result: dict[str, Any] = {
        "completed": 0,
        "failed": 0,
        "cancelled": 0,
        "failed_items": [],
    }
    if factory is None:
        return result
    async with factory() as session:
        rows = (
            await session.execute(
                select(Pipeline.status, func.count())
                .where(Pipeline.created_at >= start, Pipeline.created_at < end)
                .group_by(Pipeline.status)
            )
        ).all()
        for status, total in rows:
            if status in _PIPELINE_STATUSES:
                result[str(status)] = _number(total)
        failed = (
            await session.execute(
                select(Pipeline.title, Pipeline.error_message)
                .where(
                    Pipeline.status == PipelineStatus.FAILED.value,
                    Pipeline.created_at >= start,
                    Pipeline.created_at < end,
                )
                .order_by(Pipeline.created_at.desc())
                .limit(3)
            )
        ).all()
    result["failed_items"] = [
        {"title": title, "reason": _clip(error_message, 40)}
        for title, error_message in failed
    ]
    return result


async def _drops_block(
    app: Any, start: datetime, end: datetime
) -> list[dict[str, Any]]:
    factory = _factory(app)
    if factory is None:
        return []
    total = func.sum(StageDrop.add_quantity)
    async with factory() as session:
        rows = (
            await session.execute(
                select(StageDrop.item_name, total)
                .where(
                    StageDrop.created_at >= start,
                    StageDrop.created_at < end,
                    StageDrop.item_name.is_not(None),
                )
                .group_by(StageDrop.item_name)
                .order_by(total.desc())
                .limit(5)
            )
        ).all()
    return [
        {"item_name": name, "quantity": _number(quantity)}
        for name, quantity in rows
    ]


async def _tickets_block(
    app: Any, start: datetime, end: datetime
) -> dict[str, Any]:
    factory = _factory(app)
    result: dict[str, Any] = {"done": 0, "failed": 0, "cancelled": 0, "titles": []}
    if factory is None:
        return result
    params = {"start": start, "end": end}
    async with factory() as session:
        rows = (await session.execute(_TICKET_COUNT_SQL, params)).mappings().all()
        titles = (
            await session.execute(_TICKET_TITLE_SQL, params)
        ).mappings().all()
    for row in rows:
        status = str(row["status"])
        if status in _TICKET_STATUSES:
            result[status] = _number(row["total"])
    result["titles"] = [str(row["title"]) for row in titles]
    return result


async def _cost_block(
    app: Any, start: datetime, end: datetime
) -> dict[str, int]:
    factory = _factory(app)
    if factory is None:
        return {}
    async with factory() as session:
        rows = (
            await session.execute(_COST_SQL, {"start": start, "end": end})
        ).mappings().all()
    return {
        str(row["currency"]): _number(row["total"])
        for row in rows
    }


async def _spend_block(
    app: Any, now: datetime
) -> tuple[list[dict[str, Any]], dict[str, int]]:
    """今日 ``summary()["today"]`` 与昨日 ``usage()``（规格 §4.5.10）。"""
    guard = getattr(_state(app), "spend_guard", None)
    if guard is None:
        return [], {}
    summary = await guard.summary()
    today = list(summary.get("today") or [])
    yesterday: dict[str, int] = {}
    factory = _factory(app)
    if factory is not None:
        start, _, _ = spend_day_window(now)
        async with factory() as session:
            used = await guard.usage(
                session,
                start=start - timedelta(days=1),
                end=start,
            )
        yesterday = {kind.value: _number(value) for kind, value in used.items()}
    return today, yesterday


async def _pending_block(app: Any) -> dict[str, Any]:
    """待处理六类；每一类单独 try，某一类失败只丢该类。"""
    factory = _factory(app)
    if factory is None:
        return dict(_EMPTY_PENDING)
    pending = {
        "confirmations": 0,
        "questions": 0,
        "tickets": [],
        "tickets_total": 0,
        "alerts": 0,
        "memories": 0,
        "observations": 0,
    }
    counts: tuple[tuple[str, Any], ...] = (
        ("confirmations", _CONFIRMATION_PENDING_SQL),
        ("questions", _QUESTION_PENDING_SQL),
        ("alerts", _ALERT_PENDING_SQL),
        ("memories", _MEMORY_PENDING_SQL),
        ("observations", _OBSERVATION_PENDING_SQL),
    )
    async with factory() as session:
        for key, statement in counts:
            try:
                pending[key] = _number(
                    (await session.execute(statement)).scalar_one()
                )
            except Exception:  # noqa: BLE001 - 一类读失败只丢这一类
                logger.debug("简报待处理读取失败：%s", key, exc_info=True)
        try:
            rows = (
                await session.execute(_TICKET_PENDING_SQL)
            ).mappings().all()
            pending["tickets"] = [str(row["title"]) for row in rows]
            pending["tickets_total"] = _number(
                (await session.execute(_TICKET_PENDING_TOTAL_SQL)).scalar_one()
            )
        except Exception:  # noqa: BLE001
            logger.debug("简报待处理读取失败：tickets", exc_info=True)
    return pending


# ---------------------------------------------------------------------------
# 入口
# ---------------------------------------------------------------------------
async def collect_facts(app: Any, now: datetime) -> BriefFacts:
    """收集一份简报的全部事实；每块单独降级（规格 §4.5.10）。"""
    moment = _naive_utc(now)
    today_start, today_end, day = spend_day_window(moment)
    yesterday_start = today_start - timedelta(days=1)
    yesterday_end = today_start

    sanity = await _block("理智", _sanity_block, app, moment, default=None)
    schedules = await _block(
        "今天的定时任务",
        _schedules_block,
        app,
        moment,
        today_end,
        default=list,
    )
    activity = await _block("当期活动", _activity_block, app, default=list)
    gacha = await _block("卡池", _gacha_block, app, default=list)
    plans = await _block("养成计划", _plans_block, app, default=list)
    goals = await _block("目标", _goals_block, app, default=list)
    pipelines = await _block(
        "昨日流水线",
        _pipelines_block,
        app,
        yesterday_start,
        yesterday_end,
        default=dict,
    )
    drops = await _block(
        "昨日掉落",
        _drops_block,
        app,
        yesterday_start,
        yesterday_end,
        default=list,
    )
    tickets = await _block(
        "昨日工单",
        _tickets_block,
        app,
        yesterday_start,
        yesterday_end,
        default=dict,
    )
    cost = await _block(
        "昨日成本",
        _cost_block,
        app,
        yesterday_start,
        yesterday_end,
        default=dict,
    )
    spend_today, spend_yesterday = await _block(
        "消耗",
        _spend_block,
        app,
        moment,
        default=lambda: ([], {}),
    )
    pending = await _block(
        "待处理", _pending_block, app, default=dict
    )

    return BriefFacts(
        date=day,
        weekday=WEEKDAYS[day.weekday()],
        sanity=sanity,
        schedules_today=schedules,
        activity_lines=activity,
        gacha_lines=gacha,
        plans=plans,
        goals=goals,
        pipelines_yesterday=pipelines,
        drops_yesterday=drops,
        tickets_yesterday=tickets,
        cost_yesterday=cost,
        spend_today=spend_today,
        spend_yesterday=spend_yesterday,
        pending=pending,
    )
