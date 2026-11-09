"""M88 T4：刷图方案服务与默认装配（规格 §4.5.1、§4.5.4–§4.5.6）。

``FarmingOptimizer.optimize`` 是工具层唯一入口：读仓库 → 取规划输入 → 取活动 → 取用药池
→ 合并模型 → 求解 → 装配结果（含报告产物）。所有外部依赖从构造参数注入，测试不联网。
"""

from __future__ import annotations

import math
from datetime import datetime, timezone
from typing import Any, Callable

from maa_api.services.account import planner
from maa_api.services.economy.data import load_events, medicine_pool, merge, read_depot
from maa_api.services.economy.model import FarmingModel, FarmingRequest, MedicinePool
from maa_api.services.economy.report import render_report, report_title
from maa_api.services.economy.solver import SHORTFALL_TOL, FarmingSolution, solve_farming

__all__ = ["FarmingOptimizer", "default_farming_optimizer"]


def _now() -> datetime:
    """当前时间（带本地时区）；测试注入固定时间。"""
    return datetime.now(timezone.utc).astimezone()


def _days(sanity: int, current: int) -> float:
    """``ceil_1(max(sanity − current, 0) / 240)``：天数不计任何药。"""
    return math.ceil(max(sanity - current, 0) / planner.SANITY_PER_DAY * 10) / 10


def _format_days(value: float) -> str:
    return f"{value:g}"


class FarmingOptimizer:
    """刷图方案的领域服务；依赖全部可注入（测试与多来源装配）。"""

    def __init__(
        self,
        *,
        planner: Any,
        depot: Any,
        plans: Any,
        activities: Any,
        prts: Any,
        gamedata: Any,
        artifacts: Any | None = None,
        now: Callable[[], datetime] = _now,
    ) -> None:
        self._planner = planner
        self._depot = depot
        self._plans = plans
        self._activities = activities
        self._prts = prts
        self._gamedata = gamedata
        self._artifacts = artifacts
        self._now = now

    async def optimize(
        self,
        request: FarmingRequest,
        *,
        write_report: bool,
        session_id: str | None,
        ticket_id: str | None,
    ) -> dict[str, Any]:
        """取数 → 合并 → 求解 → 装配结果；外部来源失败只进 ``notes``，不抛错。"""
        now = self._now()
        depot_counts, depot_notes = await read_depot(self._depot)
        notes: list[str] = list(depot_notes)

        if request.plan_id:
            needs = await self._plans.missing_needs(request.plan_id)
            depot: dict[str, int] = {}
        else:
            needs = dict(request.materials or {})
            depot = dict(depot_counts) if request.use_depot else {}
            if not request.use_depot:
                notes.append("use_depot=False：没有用仓库库存抵扣")

        inputs = await self._planner.farming_inputs(needs, server=request.server)

        events: list[Any] = []
        sources: list[dict[str, Any]] = []
        event_notes: list[str] = []
        if request.include_events:
            events, sources, event_notes = await load_events(
                activities=self._activities, prts=self._prts, now=now
            )
        notes.extend(event_notes)

        medicine = await medicine_pool(
            gamedata=self._gamedata,
            depot_counts=depot_counts,
            use_medicine=request.use_medicine,
            sources=sources,
        )
        if medicine.note.startswith("没有读到理智药"):
            notes.append(medicine.note)

        model = merge(
            inputs,
            depot=depot,
            events=events,
            request=request,
            medicine=medicine,
            now=now,
        )
        notes.extend(inputs.notes)

        full = solve_farming(model)
        solution = full
        days_full = _days(full.total_sanity, request.current_sanity)
        days = days_full
        if request.window_days is not None and days_full > request.window_days:
            budget = (
                float(request.current_sanity)
                + float(request.window_days) * planner.SANITY_PER_DAY
            )
            solution = solve_farming(model, budget=budget)
            days = _days(solution.total_sanity, request.current_sanity)
        notes.extend(full.notes)
        if solution is not full:
            notes.extend(solution.notes)

        requirements = {
            inputs.display.get(key, key): amount for key, amount in inputs.needs.items()
        }
        fits_window = (
            None if request.window_days is None else days_full <= request.window_days
        )
        coverage = _coverage(model, solution, inputs.display)
        unobtainable = _unobtainable(model, full, inputs)
        status = _status(full, unobtainable, fits_window)

        stages = _stage_rows(model, solution)
        synthesis = _synthesis_rows(model, solution)
        shop_rows = _shop_rows(model, solution)
        token_rows = _token_rows(model, solution)
        result: dict[str, Any] = {
            "status": status,
            "fits_window": fits_window,
            "requirements": requirements,
            "stages": stages,
            "synthesis": synthesis,
            "shop": shop_rows,
            "tokens": token_rows,
            "sanity": {
                "needed": solution.total_sanity,
                "current": request.current_sanity,
                "medicine": 0,
                "natural_per_day": planner.SANITY_PER_DAY,
                "window_days": request.window_days,
            },
            "days_estimated": days,
            "days_estimated_full": days_full,
            "medicine": _medicine_row(medicine, request, full),
            "coverage": coverage,
            "unobtainable": unobtainable,
            "notes": notes,
            "explanation": _explanation(
                full=full,
                days_full=days_full,
                status=status,
                model=model,
                inputs=inputs,
                request=request,
                stages=stages,
                shop=shop_rows,
                coverage=coverage,
                unobtainable=unobtainable,
            ),
            "sources": [{"kind": "penguin", "label": "企鹅物流"}, *sources],
            "report_artifact_id": None,
        }
        notes.insert(0, "按每天自然恢复 240 理智估算，不含月卡与活动赠送")

        await self._write_report(
            result, write_report=write_report, session_id=session_id, ticket_id=ticket_id, now=now
        )
        return result

    async def _write_report(
        self,
        result: dict[str, Any],
        *,
        write_report: bool,
        session_id: str | None,
        ticket_id: str | None,
        now: datetime,
    ) -> None:
        if not write_report or self._artifacts is None or session_id is None:
            return
        try:
            artifact = await self._artifacts.put(
                kind="report",
                title=report_title(result),
                body=render_report(result, generated_at=now).encode("utf-8"),
                mime="text/markdown",
                session_id=session_id,
                ticket_id=ticket_id,
                meta={"farming": True},
            )
        except Exception:  # noqa: BLE001 - 报告写失败不影响工具结果
            result["notes"].append("报告没有保存成功")
            return
        result["report_artifact_id"] = getattr(artifact, "id", None)


def _status(
    full: FarmingSolution,
    unobtainable: list[dict[str, Any]],
    fits_window: bool | None,
) -> str:
    if full.status is not None or unobtainable:
        return "unobtainable"
    if fits_window is False:
        return "partial"
    return "complete"


def _coverage(
    model: FarmingModel,
    solution: FarmingSolution,
    display: dict[str, str],
) -> dict[str, float]:
    coverage: dict[str, float] = {}
    for key, need in model.needs.items():
        demand = max(int(need) - int(model.depot.get(key, 0)), 0)
        shortfall = float(solution.shortfall.get(key, 0.0))
        value = 1.0 if demand <= 0 else max(0.0, 1.0 - shortfall / demand)
        coverage[display.get(key, key)] = round(value, 2)
    return coverage


def _unobtainable(
    model: FarmingModel,
    full: FarmingSolution,
    inputs: Any,
) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    for key, need in model.needs.items():
        missing = float(full.shortfall.get(key, 0.0))
        if missing <= SHORTFALL_TOL:
            continue
        rows.append(
            {
                "name": inputs.display.get(key, key),
                "amount": int(need),
                "have": int(model.depot.get(key, 0)),
                "missing": max(int(need) - int(model.depot.get(key, 0)), 0),
                "reason": inputs.rate_status.get(key)
                or "当前可用的关卡、配方与商店凑不齐",
            }
        )
    rows.sort(key=lambda row: row["name"])
    return rows


def _stage_rows(model: FarmingModel, solution: FarmingSolution) -> list[dict[str, Any]]:
    allowed = set(model.needs)
    for recipe in model.recipes:
        allowed.add(recipe.product)
        allowed.update(key for key, _ in recipe.materials)

    rows: list[dict[str, Any]] = []
    for stage, runs in zip(model.options, solution.runs):
        if runs <= 0:
            continue
        tokens = model.tokens_of.get(stage.stage_id)
        details = [
            dict(detail)
            for detail in stage.details
            if detail.get("item_name") and planner._key(detail["item_name"]) in allowed
        ]
        rows.append(
            {
                "stage_id": stage.stage_id,
                "stage_code": stage.stage_code,
                "runs": int(runs),
                "sanity_per_run": stage.ap_cost,
                "sanity": int(runs) * stage.ap_cost,
                "event": stage.stage_id in model.event_of,
                "activity": model.event_of.get(stage.stage_id),
                "deadline": _deadline(model, stage.stage_id),
                "navigable": model.navigable_of.get(stage.stage_id, True),
                "tokens_per_run": tokens[1] if tokens is not None else None,
                "items": details,
            }
        )
    rows.sort(key=lambda row: (-row["sanity"], row["stage_id"]))
    return rows


def _deadline(model: FarmingModel, stage_id: str) -> str | None:
    moment = model.deadline_of.get(stage_id)
    return moment.isoformat() if moment is not None else None


def _shop_rows(model: FarmingModel, solution: FarmingSolution) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    for good, count in zip(model.shop, solution.buys):
        if count <= 0:
            continue
        quantity = int(count) * good.bundle
        value = None if good.unit_sanity is None else round(quantity * good.unit_sanity, 2)
        rows.append(
            {
                "activity": good.activity,
                "item": good.item,
                "quantity": quantity,
                "bundle": good.bundle,
                "price": good.price,
                "tokens": int(count) * good.price,
                "stock": good.stock,
                "section": good.section,
                "value_sanity": value,
            }
        )
    rows.sort(
        key=lambda row: (
            row["value_sanity"] is None,
            -(row["value_sanity"] or 0.0),
            row["item"],
        )
    )
    return rows


def _token_rows(model: FarmingModel, solution: FarmingSolution) -> list[dict[str, Any]]:
    tokens = sorted(
        {drops[0] for drops in model.tokens_of.values()}
        | {good.token for good in model.shop if good.token}
    )
    rows: list[dict[str, Any]] = []
    for token in tokens:
        owned = int(model.owned_tokens.get(token, 0))
        earned = 0
        activity = None
        for stage, runs in zip(model.options, solution.runs):
            drops = model.tokens_of.get(stage.stage_id)
            if drops is None or drops[0] != token:
                continue
            earned += drops[1] * runs
            if activity is None:
                activity = model.event_of.get(stage.stage_id)
        spent = sum(
            good.price * int(count)
            for good, count in zip(model.shop, solution.buys)
            if good.token == token
        )
        if activity is None:
            activity = next(
                (good.activity for good in model.shop if good.token == token), None
            )
        rows.append(
            {
                "activity": activity,
                "token": token,
                "owned": owned,
                "earned": earned,
                "spent": spent,
                "left": max(owned + earned - spent, 0),
            }
        )
    return rows


def _synthesis_rows(model: FarmingModel, solution: FarmingSolution) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    for recipe, count in zip(model.recipes, solution.synth):
        if count <= 0:
            continue
        materials = [
            {"name": model.display.get(key, key), "amount": amount}
            for key, amount in recipe.materials
        ]
        rows.append(
            {
                "product": model.display.get(recipe.product, recipe.product),
                "count": int(count),
                "materials": materials,
                "total_materials": [
                    {"name": row["name"], "amount": row["amount"] * int(count)}
                    for row in materials
                ],
                "lmd_cost": recipe.lmd_cost,
                "lmd_total": recipe.lmd_cost * int(count),
            }
        )
    rows.sort(key=lambda row: row["product"])
    return rows


def _medicine_row(
    medicine: MedicinePool, request: FarmingRequest, full: FarmingSolution
) -> dict[str, Any]:
    needed = max(full.total_sanity - request.current_sanity, 0)
    max_days_saved = round(
        min(medicine.owned_sanity, needed) / planner.SANITY_PER_DAY, 1
    )
    return {
        "use": request.use_medicine,
        "scope": medicine.scope,
        "items": [
            {"name": item.name, "ap": item.ap, "count": item.count}
            for item in medicine.items
        ],
        "owned_sanity": medicine.owned_sanity,
        "counted_sanity": medicine.counted_sanity,
        "expiring_medicine": medicine.expiring_medicine,
        "max_days_saved": max_days_saved,
        "note": medicine.note,
    }


def _explanation(
    *,
    full: FarmingSolution,
    days_full: float,
    status: str,
    model: FarmingModel,
    inputs: Any,
    request: FarmingRequest,
    stages: list[dict[str, Any]],
    shop: list[dict[str, Any]],
    coverage: dict[str, float],
    unobtainable: list[dict[str, Any]],
) -> str:
    if not model.needs:
        return "没有剩余需求：不需要刷关"

    parts = [
        f"凑齐需要 {full.total_sanity} 理智，按当前理智 {request.current_sanity} "
        f"与每天 {planner.SANITY_PER_DAY} 的自然恢复约 {_format_days(days_full)} 天。"
    ]
    top = stages[:2]
    if top:
        sentence = "主要刷 " + "、".join(
            f"{row['stage_code']} ×{row['runs']}" for row in top
        )
        event_row = next(
            (row for row in top if row["event"] and row["tokens_per_run"]), None
        )
        if event_row is not None:
            sentence += (
                f"；{event_row['activity']} 的 {event_row['stage_code']} "
                f"每次还给 {event_row['tokens_per_run']} 个代币"
            )
        parts.append(sentence + "。")
    if shop:
        first_two = shop[:2]
        listed = "、".join(f"{row['item']} ×{row['quantity']}" for row in first_two)
        value = int(round(sum(row["value_sanity"] or 0.0 for row in first_two)))
        parts.append(f"代币先换 {listed}，按掉率折合约 {value} 理智。")
    if status == "partial":
        entries = [
            f"{inputs.display.get(key, key)} {int(round(coverage.get(inputs.display.get(key, key), 0.0) * 100))}%"
            for key in list(model.needs)[:3]
        ]
        parts.append(
            f"{_format_days(float(request.window_days))} 天内能做到：{'、'.join(entries)}。"
        )
    elif unobtainable:
        for row in unobtainable:
            parts.append(f"{row['name']} 目前凑不齐：{row['reason']}。")
    return "".join(parts)


def default_farming_optimizer(app_state: Any | None = None) -> FarmingOptimizer:
    """按接缝裁决的装配表现建；``app_state`` 为 None 时全部用默认装配。"""
    from maa_api.db import session as db_session
    from maa_api.services.account.depot import DepotService
    from maa_api.services.account.planner import default_upgrade_planner
    from maa_api.services.account.plans import default_upgrade_plan_service
    from maa_api.services.external.gamedata import GamedataClient
    from maa_api.services.game.activities import default_activity_service

    activities = default_activity_service()
    session_factory = getattr(app_state, "session_factory", None) or db_session.session_factory
    return FarmingOptimizer(
        planner=getattr(app_state, "planner_service", None) or default_upgrade_planner(),
        depot=getattr(app_state, "depot_service", None) or DepotService(session_factory),
        plans=getattr(app_state, "upgrade_plan_service", None)
        or default_upgrade_plan_service(),
        activities=activities,
        prts=activities.prts,
        gamedata=GamedataClient(),
        artifacts=getattr(app_state, "artifact_service", None),
    )
