"""模型成本预算（M67 规格 §4.2.5、docs/v3/03 §4.2）。

两个上限都按人民币理解、单位元，默认 0 表示不限：

- ``budget.per_run_cost_limit``（可被代理定义里的 ``budget.max_cost_per_run`` 覆盖）
  与 ``budget.daily_cost_limit``；
- 花费达到上限的 ``budget.warn_ratio`` 时告警一次：运行级记在
  ``ctx.state["budget"]["run_warned"]``，日级由 :class:`~maa_api.agent.llm.cost.CostLedger`
  记住已告警的日期。

:class:`BudgetMiddleware` 只统计 :data:`~maa_api.agent.llm.cost.BUDGET_CURRENCY`
的账本行（USD 等其它币种不计入预算），挂在中间件链第 2 位（trace 之后、
context_layout 之前）。没有账本（例如测试直接构造的 v2 门面）或两个上限都是 0
时它不查询、不告警、不抛错，行为与 v2 完全一致。

:func:`check_budget` 是同一套判断的函数形式，供 ``complete_structured`` 这类不经过
``AgentLoop`` 的调用使用；它只读 ``get_settings().budget``，没有代理定义级覆盖。
"""

from __future__ import annotations

import inspect
from collections.abc import Callable
from typing import Any

from maa_api.agent.harness.context import ModelRequest, RunContext
from maa_api.agent.harness.middleware import Middleware
from maa_api.agent.llm.cost import BUDGET_CURRENCY
from maa_api.domain.errors import AppError, ErrorCode
from maa_api.settings import get_settings

__all__ = ["BudgetMiddleware", "check_budget"]

MICROS_PER_UNIT = 1_000_000


def _details(scope: str, limit: float, spent_micros: int) -> dict[str, Any]:
    """``BUDGET_EXCEEDED`` 的固定三键加币种（规格 §4.2.5）。"""
    return {
        "scope": scope,
        "limit": limit,
        "spent_micros": spent_micros,
        "currency": BUDGET_CURRENCY,
    }


def _limit_micros(limit: float) -> int:
    """元 → micros；四舍五入与成本公式同一口径。"""
    return round(limit * MICROS_PER_UNIT)


def _over_limit(scope: str, limit: float, spent_micros: int) -> AppError:
    message = (
        "这次运行的模型成本已达到上限"
        if scope == "run"
        else "今天的模型成本已达到上限"
    )
    return AppError(
        ErrorCode.BUDGET_EXCEEDED, message, _details(scope, limit, spent_micros)
    )


def _reached_warn_line(
    spent_micros: int, *, limit: float, warn_ratio: float
) -> bool:
    """是否达到告警线（含等于）。上限为 0 的调用方不会走到这里。"""
    return spent_micros >= warn_ratio * limit * MICROS_PER_UNIT


async def check_budget(ledger: Any, *, run_id: str | None = None) -> None:
    """按当前设置检查运行级与每日预算；超限抛 ``BUDGET_EXCEEDED``。

    ``run_id`` 为 ``None`` 时跳过运行级检查（没有运行可查），每日上限照常检查。
    达到告警线时通过 ``ledger.warn`` 记日志并广播，同一运行 / 同一天只告警一次。
    """
    budget = get_settings().budget
    run_limit = float(budget.per_run_cost_limit)
    if run_limit > 0 and run_id is not None:
        spent = await ledger.run_spent_micros(run_id)
        if spent >= _limit_micros(run_limit):
            raise _over_limit("run", run_limit, spent)
        if _reached_warn_line(spent, limit=run_limit, warn_ratio=budget.warn_ratio):
            ledger.warn(
                scope="run",
                limit=run_limit,
                spent_micros=spent,
                run_id=run_id,
            )

    day_limit = float(budget.daily_cost_limit)
    if day_limit > 0:
        spent = await ledger.spent_today_micros()
        if spent >= _limit_micros(day_limit):
            raise _over_limit("day", day_limit, spent)
        if _reached_warn_line(spent, limit=day_limit, warn_ratio=budget.warn_ratio):
            ledger.warn(
                scope="day",
                limit=day_limit,
                spent_micros=spent,
                run_id=run_id,
            )


class BudgetMiddleware(Middleware):
    """调用模型前检查单次运行与每日预算（规格 §4.2.5）。

    ``ledger`` 缺省时从 ``ctx.app.state.cost_ledger`` 取：装配层在 lifespan 里挂上
    之后，v2 会话与非 legacy 链共用同一个账本；测试或未装配的应用取不到账本，
    中间件就什么都不做。
    """

    name = "budget"

    def __init__(
        self,
        *,
        ledger: Any = None,
        settings_provider: Callable[[], Any] | None = None,
    ) -> None:
        self._ledger = ledger
        self._settings_provider = settings_provider or get_settings

    async def before_model(self, ctx: RunContext, req: ModelRequest) -> ModelRequest:
        ledger = self._resolve_ledger(ctx)
        if ledger is None:
            return req
        settings = await self._resolve_settings()
        budget = getattr(settings, "budget", None)
        if budget is None:
            return req

        spec_budget = getattr(getattr(ctx.spec, "budget", None), "max_cost_per_run", 0)
        run_limit = float(spec_budget or 0) or float(budget.per_run_cost_limit)
        if run_limit > 0:
            spent = await ledger.run_spent_micros(ctx.run_id)
            if spent >= _limit_micros(run_limit):
                raise _over_limit("run", run_limit, spent)
            if _reached_warn_line(
                spent, limit=run_limit, warn_ratio=budget.warn_ratio
            ):
                state = ctx.state.setdefault(self.name, {})
                if not state.get("run_warned"):
                    ledger.warn(
                        scope="run",
                        limit=run_limit,
                        spent_micros=spent,
                        run_id=ctx.run_id,
                        session_id=ctx.session_id,
                    )
                    state["run_warned"] = True

        day_limit = float(budget.daily_cost_limit)
        if day_limit > 0:
            spent = await ledger.spent_today_micros()
            if spent >= _limit_micros(day_limit):
                raise _over_limit("day", day_limit, spent)
            if _reached_warn_line(
                spent, limit=day_limit, warn_ratio=budget.warn_ratio
            ):
                # 日级去重由账本记住的日期负责；重复调用不会重复广播。
                ledger.warn(
                    scope="day",
                    limit=day_limit,
                    spent_micros=spent,
                    run_id=ctx.run_id,
                    session_id=ctx.session_id,
                )
        return req

    def _resolve_ledger(self, ctx: RunContext) -> Any:
        if self._ledger is not None:
            return self._ledger
        state = getattr(ctx.app, "state", None)
        return getattr(state, "cost_ledger", None)

    async def _resolve_settings(self) -> Any:
        settings = self._settings_provider()
        if inspect.isawaitable(settings):
            settings = await settings
        return settings
