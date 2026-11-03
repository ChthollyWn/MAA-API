"""模型调用的成本账本与汇总（M67 规格 §4.2.5、docs/v3/03 §4.2）。

价格列是**每百万 token 的币种单位**，成本列 ``cost_micros`` 是币种的百万分之一；
两者相差正好一百万，所以「token × 价格」直接就是 micros，不需要再乘除 10⁶：

    micros = (prompt − cached) × price_input
           + cached × (price_cached_input 或 price_input)
           + completion × price_output

思考 token 已包含在 ``completion_tokens`` 里（:mod:`maa_api.agent.llm.usage`），
计费不重复加。价格用 ``Decimal(str(x))`` 计算再 ``ROUND_HALF_UP`` 取整，避免
二进制浮点在 .5 附近把成本算少 1 micros。

:class:`CostLedger` 是成本写入的唯一入口：每次模型调用记一行 ``cost_ledger``，
同时把四个 token 列、币种与成本累计进 ``agent_run``。仓储不提交事务，本类负责
事务边界：一次 ``record`` 是一个事务，写锁竞争经 ``run_with_busy_retry`` 重试。

预算判定不在这里，见 :mod:`maa_api.agent.llm.budget`；本模块只提供
:meth:`CostLedger.run_spent_micros` / :meth:`CostLedger.spent_today_micros`
两个查询。两者都只统计 :data:`BUDGET_CURRENCY` 的行。
"""

from __future__ import annotations

import logging
from collections import Counter
from collections.abc import Callable, Sequence
from datetime import UTC, date, datetime
from decimal import ROUND_HALF_UP, Decimal
from typing import Any

from pydantic import BaseModel
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from maa_api.agent.llm.profiles import ResolvedProfile
from maa_api.agent.llm.usage import Usage
from maa_api.db.models import AgentRun, CostLedgerEntry, utcnow
from maa_api.db.repositories.cost import CostLedgerRepository
from maa_api.db.session import run_with_busy_retry

__all__ = [
    "BUDGET_CURRENCY",
    "CostGroupView",
    "CostLedger",
    "CostSummaryView",
    "compute_cost_micros",
    "local_day_start",
]

logger = logging.getLogger(__name__)

#: 预算只统计这一币种的账本行；``default`` 档案是 CNY，``budget.*`` 也按人民币理解。
BUDGET_CURRENCY = "CNY"
#: 分组汇总支持的四种口径；``day`` 按服务器本地日期，其余按账本列。
GROUP_BY_CHOICES = ("day", "agent", "model", "role")


def _decimal(value: float | None) -> Decimal:
    """价格按十进制字面值参与计算；``None`` 视为 0（没配价格的项不计费）。"""
    return Decimal(str(value)) if value is not None else Decimal(0)


def compute_cost_micros(usage: Usage, profile: ResolvedProfile) -> int:
    """一次模型调用的成本（micros）；价格全空时为 0。

    缓存命中部分优先用 ``price_cached_input``，没配则退回 ``price_input``；
    ``cached_tokens`` 理论上恒 ≤ ``prompt_tokens``，这里仍取 ``max(..., 0)``，
    负的未命中数量不会变成负数成本。
    """
    cached_price = (
        profile.price_cached_input
        if profile.price_cached_input is not None
        else profile.price_input
    )
    uncached_prompt = max(usage.prompt_tokens - usage.cached_tokens, 0)
    total = (
        Decimal(uncached_prompt) * _decimal(profile.price_input)
        + Decimal(usage.cached_tokens) * _decimal(cached_price)
        + Decimal(usage.completion_tokens) * _decimal(profile.price_output)
    )
    return int(total.quantize(Decimal("1"), rounding=ROUND_HALF_UP))


def _utc_naive(moment: datetime) -> datetime:
    """带时区的时间换成 naive UTC；naive 时间按约定已经是 UTC，原样返回。"""
    if moment.tzinfo is None:
        return moment
    return moment.astimezone(UTC).replace(tzinfo=None)


def local_day_start(now: datetime | None = None) -> datetime:
    """服务器本地时区「今天 0 点」对应的 naive UTC（与 ``created_at`` 同口径）。"""
    moment = _utc_naive(now or utcnow()).replace(tzinfo=UTC)
    local_midnight = moment.astimezone().replace(
        hour=0, minute=0, second=0, microsecond=0
    )
    return local_midnight.astimezone(UTC).replace(tzinfo=None)


def _local_date(moment: datetime) -> str:
    """账本时间（naive UTC）在服务器本地时区的 ``YYYY-MM-DD``。"""
    return _utc_naive(moment).replace(tzinfo=UTC).astimezone().date().isoformat()


class CostGroupView(BaseModel):
    """一种分组口径下的一行：key 是日期 / 代理名 / 模型名 / 角色名。"""

    key: str
    calls: int = 0
    prompt_tokens: int = 0
    cached_tokens: int = 0
    completion_tokens: int = 0
    cost_micros: int = 0


class CostSummaryView(BaseModel):
    """成本汇总（``GET /api/agent/costs`` 的响应体）。"""

    currency: str
    total_micros: int
    groups: list[CostGroupView]


class CostLedger:
    """成本账本的写入、汇总与告警出口。

    ``session_factory`` 由装配层注入（生产是 ``db.session.session_factory``）；
    ``broadcast`` 用于预算告警，签名与 ``ws_manager.broadcast`` 一致，缺省不广播。
    """

    def __init__(
        self,
        session_factory: async_sessionmaker[AsyncSession],
        *,
        broadcast: Callable[[str, Any], Any] | None = None,
    ) -> None:
        self._session_factory = session_factory
        self._broadcast = broadcast
        # 同一运行 / 同一天只告警一次（规格 §4.2.5）。运行集合只在真正告警时增长，
        # 进程生命周期内最多一条运行一个 id；日告警只保留最近一天。
        self._warned_runs: set[str] = set()
        self._warned_day: date | None = None

    # ------------------------------------------------------------------
    # 写入
    # ------------------------------------------------------------------
    async def record(
        self,
        *,
        run_id: str | None,
        session_id: str | None,
        agent_name: str,
        role: str,
        profile: ResolvedProfile,
        usage: Usage,
    ) -> int:
        """记一行账本，并把用量累计进 ``agent_run``；返回本次 ``cost_micros``。

        事务纪律：整个操作经 ``run_with_busy_retry`` 在 SAVEPOINT 里执行一次，
        成功后由本方法 ``commit``。``run_id`` 为空或行已不存在时只写账本行。
        """
        cost_micros = compute_cost_micros(usage, profile)

        async def operation() -> int:
            await self._write(
                session=session,
                run_id=run_id,
                session_id=session_id,
                agent_name=agent_name,
                role=role,
                profile=profile,
                usage=usage,
                cost_micros=cost_micros,
            )
            return cost_micros

        async with self._session_factory() as session:
            result = await run_with_busy_retry(session, operation)
            await session.commit()
        return result

    async def _write(
        self,
        *,
        session: AsyncSession,
        run_id: str | None,
        session_id: str | None,
        agent_name: str,
        role: str,
        profile: ResolvedProfile,
        usage: Usage,
        cost_micros: int,
    ) -> None:
        """往 ``cost_ledger`` 插一行并更新运行累计；调用方负责事务。"""
        await CostLedgerRepository(session).add(
            CostLedgerEntry(
                run_id=run_id,
                session_id=session_id,
                agent_name=agent_name,
                role=role,
                model_profile=profile.name,
                model=profile.model,
                prompt_tokens=usage.prompt_tokens,
                cached_tokens=usage.cached_tokens,
                completion_tokens=usage.completion_tokens,
                reasoning_tokens=usage.reasoning_tokens,
                cost_micros=cost_micros,
                currency=profile.currency,
            )
        )
        if run_id is None:
            return
        run = (
            await session.execute(select(AgentRun).where(AgentRun.id == run_id))
        ).scalar_one_or_none()
        if run is None:
            # 账本行可能先于运行被清理（run_id 是 SET NULL），这里不制造孤儿。
            logger.debug("成本账本：运行 %s 已不存在，只写账本行", run_id)
            return

        # token 与币种无关，任何币种都累加（规格 §4.5.6）。
        run.prompt_tokens = (run.prompt_tokens or 0) + usage.prompt_tokens
        run.cached_tokens = (run.cached_tokens or 0) + usage.cached_tokens
        run.completion_tokens = (run.completion_tokens or 0) + usage.completion_tokens
        run.reasoning_tokens = (run.reasoning_tokens or 0) + usage.reasoning_tokens
        if not run.model:
            run.model = profile.model
        if not run.model_profile:
            run.model_profile = profile.name

        run_currency = (run.currency or "").strip()
        if not run_currency:
            # 第一次记账决定这次运行的币种。
            run.currency = profile.currency
            run.cost_micros = (run.cost_micros or 0) + cost_micros
        elif run_currency == profile.currency:
            run.cost_micros = (run.cost_micros or 0) + cost_micros
        else:
            logger.warning(
                "成本币种与运行不一致，cost_micros 不累加：run=%s run_currency=%s "
                "profile=%s profile_currency=%s",
                run_id,
                run_currency,
                profile.name,
                profile.currency,
            )

    # ------------------------------------------------------------------
    # 查询
    # ------------------------------------------------------------------
    async def run_spent_micros(self, run_id: str) -> int:
        """这次运行在 :data:`BUDGET_CURRENCY` 下已花的 micros。"""
        async with self._session_factory() as session:
            result = await session.execute(
                select(
                    func.coalesce(func.sum(CostLedgerEntry.cost_micros), 0)
                ).where(
                    CostLedgerEntry.run_id == run_id,
                    CostLedgerEntry.currency == BUDGET_CURRENCY,
                )
            )
            return int(result.scalar_one() or 0)

    async def spent_today_micros(self, *, now: datetime | None = None) -> int:
        """服务器本地「今天」在 :data:`BUDGET_CURRENCY` 下已花的 micros。"""
        since = local_day_start(now)
        async with self._session_factory() as session:
            return await CostLedgerRepository(session).sum_cost_since(
                since, BUDGET_CURRENCY
            )

    async def summarize(
        self,
        *,
        since: datetime,
        until: datetime,
        group_by: str,
        currency: str | None = None,
    ) -> CostSummaryView:
        """``since <= created_at < until`` 的账本按 ``group_by`` 汇总。

        ``currency`` 为 ``None`` 时取区间内行数最多的币种（并列按币种名升序），
        没有数据时为 :data:`BUDGET_CURRENCY`。``day`` 按服务器本地日期升序；
        ``agent`` / ``model`` / ``role`` 按 ``cost_micros`` 降序、再按 key 升序。
        """
        if group_by not in GROUP_BY_CHOICES:
            raise ValueError(f"不支持的分组口径: {group_by!r}")
        async with self._session_factory() as session:
            rows = await CostLedgerRepository(session).rows_between(
                _utc_naive(since), _utc_naive(until)
            )
        chosen = currency or _busiest_currency(rows)
        selected = [row for row in rows if row.currency == chosen]
        groups = _group_rows(selected, group_by)
        return CostSummaryView(
            currency=chosen,
            total_micros=sum(group.cost_micros for group in groups),
            groups=groups,
        )

    # ------------------------------------------------------------------
    # 告警
    # ------------------------------------------------------------------
    def warn(
        self,
        *,
        scope: str,
        limit: float,
        spent_micros: int,
        run_id: str | None,
        session_id: str | None = None,
    ) -> None:
        """成本越过告警线时记一条 WARNING 并广播 ``budget_warning``。

        ``scope="run"`` 同一次运行只告警一次，``scope="day"`` 同一天只告警一次；
        重复调用直接返回。广播失败只记日志：告警是观测信息，不能打断运行。
        """
        if scope == "day":
            today = _local_date(utcnow())
            if self._warned_day == today:
                return
            self._warned_day = today
        elif run_id is not None:
            if run_id in self._warned_runs:
                return
            self._warned_runs.add(run_id)

        logger.warning(
            "模型成本达到告警线：scope=%s limit=%s spent_micros=%s currency=%s run=%s",
            scope,
            limit,
            spent_micros,
            BUDGET_CURRENCY,
            run_id,
        )
        if self._broadcast is None:
            return
        try:
            self._broadcast(
                "agent_event",
                {
                    "session_id": session_id,
                    "event": "budget_warning",
                    "payload": {
                        "scope": scope,
                        "limit": limit,
                        "spent_micros": spent_micros,
                        "currency": BUDGET_CURRENCY,
                        "run_id": run_id,
                    },
                },
            )
        except Exception:  # noqa: BLE001 - 见 docstring
            logger.warning("预算告警广播失败：run=%s", run_id, exc_info=True)


def _busiest_currency(rows: Sequence[CostLedgerEntry]) -> str:
    """区间内账本行最多的币种；空区间按 :data:`BUDGET_CURRENCY`。"""
    if not rows:
        return BUDGET_CURRENCY
    counts = Counter(row.currency for row in rows)
    return min(counts, key=lambda item: (-counts[item], item))


def _group_rows(
    rows: Sequence[CostLedgerEntry], group_by: str
) -> list[CostGroupView]:
    """把账本行聚成 :class:`CostGroupView`，按规格排序。"""
    groups: dict[str, CostGroupView] = {}
    for row in rows:
        if group_by == "day":
            key = _local_date(row.created_at)
        elif group_by == "agent":
            key = row.agent_name
        elif group_by == "model":
            key = row.model
        else:
            key = row.role
        group = groups.get(key)
        if group is None:
            group = groups[key] = CostGroupView(key=key)
        group.calls += 1
        group.prompt_tokens += row.prompt_tokens
        group.cached_tokens += row.cached_tokens
        group.completion_tokens += row.completion_tokens
        group.cost_micros += row.cost_micros

    if group_by == "day":
        return sorted(groups.values(), key=lambda group: group.key)
    return sorted(groups.values(), key=lambda group: (-group.cost_micros, group.key))
