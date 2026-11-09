"""M88 T3：刷图线性规划（规格 §4.5.3）。

纯函数：``build_matrix`` 组矩阵，``solve_farming`` 两阶段求解（先最大化覆盖率，再最小化
理智），最后把合成/兑换向上取整、按预算与代币做取整后修复。所有 ``linprog`` 调用都经
``maa_api.services.account.planner._linprog_call``，测试可以替换这个接缝。
"""

from __future__ import annotations

import math
from dataclasses import dataclass
from datetime import datetime
from typing import Iterable

from maa_api.services.account import planner
from maa_api.services.economy.model import FarmingModel, ShopGood

__all__ = [
    "COVERAGE_TOL",
    "EPSILON",
    "FarmingSolution",
    "LpMatrix",
    "SHORTFALL_TOL",
    "build_matrix",
    "solve_farming",
]

#: 第二阶段的覆盖率不允许比第一阶段最优值差超过这个量。
COVERAGE_TOL = 1e-6
#: 合成次数与兑换份数的打破平局系数。
EPSILON = 1e-3
#: 短缺量超过半件才判为 unobtainable。
SHORTFALL_TOL = 0.5

#: 浮点数比较的容差：HiGHS 的整数解可能带回 ``0.9999999999`` 这类值。
_CEIL_TOL = 1e-9
#: 约束复查的容差。
_ROW_TOL = 1e-9


@dataclass(slots=True)
class FarmingSolution:
    """一次刷图求解的结果（坐标与 ``FarmingModel`` 的列表顺序一致）。"""

    runs: tuple[int, ...]
    synth: tuple[int, ...]
    buys: tuple[int, ...]
    shortfall: dict[str, float]        # _key 归一后的材料键 → 缺口（单位：件）
    total_sanity: int
    status: str | None                 # 非空表示求解失败的原因
    notes: tuple[str, ...]


@dataclass(slots=True)
class LpMatrix:
    """``build_matrix`` 的产物：变量顺序 x → y → z → u，行顺序材料 → 代币 → 截止 → 预算。"""

    c: list[float]
    A_ub: list[list[float]]
    b_ub: list[float]
    bounds: list[tuple[float, float | None]]
    x_slice: slice
    y_slice: slice
    z_slice: slice
    u_slice: slice
    x_index: dict[str, int]            # stage_id → 变量下标
    y_index: dict[str, int]            # 配方产物键 → 变量下标
    u_index: dict[str, int]            # 材料键 → 变量下标
    z_order: list[ShopGood]            # z 段按 model.shop 顺序
    row_names: list[str]               # "material:<key>" / "token:<name>" / "deadline:<iso>" / "budget"


# --------------------------------------------------------------------------- #
# 组矩阵
# --------------------------------------------------------------------------- #


def _demand(model: FarmingModel) -> dict[str, int]:
    """``d_i = max(need_i − depot_i, 0)``：只为大于 0 的需求材料建 ``u``。"""
    return {
        key: max(int(amount) - int(model.depot.get(key, 0)), 0)
        for key, amount in model.needs.items()
    }


def build_matrix(model: FarmingModel, *, budget: float | None) -> LpMatrix:
    """按规格 §4.5.3 的行/列顺序组装 ``A_ub · v ≤ b_ub``；``c`` 是第一阶段的系数。"""
    options = list(model.options)
    recipes = list(model.recipes)
    shop = list(model.shop)
    x_count = len(options)
    y_count = len(recipes)
    z_count = len(shop)

    demand = _demand(model)
    u_keys = sorted((key for key, amount in demand.items() if amount > 0), key=planner._key)
    size = x_count + y_count + z_count + len(u_keys)

    x_slice = slice(0, x_count)
    y_slice = slice(x_count, x_count + y_count)
    z_slice = slice(x_count + y_count, x_count + y_count + z_count)
    u_slice = slice(x_count + y_count + z_count, size)

    u_index = {key: u_slice.start + offset for offset, key in enumerate(u_keys)}
    x_index = {stage.stage_id: index for index, stage in enumerate(options)}
    y_index = {recipe.product: y_slice.start + index for index, recipe in enumerate(recipes)}

    c = [0.0] * size
    for key, index in u_index.items():
        c[index] = 1.0 / max(demand[key], 1)

    matrix: list[list[float]] = []
    bounds_ub: list[float] = []
    row_names: list[str] = []

    material_keys = set(model.needs)
    for recipe in recipes:
        material_keys.add(recipe.product)
        material_keys.update(key for key, _ in recipe.materials)

    for key in sorted(material_keys, key=planner._key):
        row = [0.0] * size
        for index, stage in enumerate(options):
            row[index] = -float(stage.rates.get(key, 0.0))
        for index, recipe in enumerate(recipes):
            produced = recipe.output if recipe.product == key else 0
            consumed = sum(amount for name, amount in recipe.materials if name == key)
            row[y_slice.start + index] = -float(produced - consumed)
        for index, good in enumerate(shop):
            if good.key == key:
                row[z_slice.start + index] = -float(good.bundle)
        if key in u_index:
            row[u_index[key]] = -1.0
        matrix.append(row)
        bounds_ub.append(float(model.depot.get(key, 0) - model.needs.get(key, 0)))
        row_names.append(f"material:{key}")

    for token in sorted({good.token for good in shop}):
        row = [0.0] * size
        for index, good in enumerate(shop):
            if good.token == token:
                row[z_slice.start + index] = float(good.price)
        for index, stage in enumerate(options):
            tokens = model.tokens_of.get(stage.stage_id)
            if tokens is not None and tokens[0] == token:
                row[index] = -float(tokens[1])
        matrix.append(row)
        bounds_ub.append(float(model.owned_tokens.get(token, 0)))
        row_names.append(f"token:{token}")

    for moment, amount in sorted(model.deadline_budgets, key=lambda item: item[0]):
        row = [0.0] * size
        for index, stage in enumerate(options):
            deadline = model.deadline_of.get(stage.stage_id)
            if deadline is not None and deadline <= moment:
                row[index] = float(stage.ap_cost)
        matrix.append(row)
        bounds_ub.append(float(amount))
        row_names.append(f"deadline:{_iso(moment)}")

    if budget is not None:
        row = [0.0] * size
        for index, stage in enumerate(options):
            row[index] = float(stage.ap_cost)
        matrix.append(row)
        bounds_ub.append(float(budget))
        row_names.append("budget")

    bounds: list[tuple[float, float | None]] = [(0.0, None)] * x_count
    bounds.extend([(0.0, None)] * y_count)
    for good in shop:
        upper = None if good.stock is None else float(max(good.stock, 0))
        bounds.append((0.0, upper))
    for key in u_keys:
        bounds.append((0.0, float(demand[key])))

    return LpMatrix(
        c=c,
        A_ub=matrix,
        b_ub=bounds_ub,
        bounds=bounds,
        x_slice=x_slice,
        y_slice=y_slice,
        z_slice=z_slice,
        u_slice=u_slice,
        x_index=x_index,
        y_index=y_index,
        u_index=u_index,
        z_order=shop,
        row_names=row_names,
    )


def _iso(moment: datetime) -> str:
    return moment.isoformat()


# --------------------------------------------------------------------------- #
# 求解
# --------------------------------------------------------------------------- #


def solve_farming(model: FarmingModel, *, budget: float | None = None) -> FarmingSolution:
    """两阶段求解 + 整数化 + 取整后修复；失败写 ``status``，不抛错。"""
    matrix = build_matrix(model, budget=budget)
    if not matrix.c:
        return _zero_solution(model)

    first = _call_linprog(
        c=matrix.c,
        A_ub=matrix.A_ub or None,
        b_ub=matrix.b_ub or None,
        bounds=matrix.bounds,
    )
    if isinstance(first, str):
        return _failure(model, first)
    coverage = 0.0
    if getattr(first, "fun", None) is not None:
        coverage = float(first.fun)

    second_c = (
        [float(stage.ap_cost) for stage in model.options]
        + [EPSILON] * (len(model.recipes) + len(model.shop))
        + [0.0] * len(matrix.u_index)
    )
    second_rows = [list(row) for row in matrix.A_ub]
    second_bounds_ub = list(matrix.b_ub)
    coverage_row = [0.0] * len(matrix.c)
    demand = _demand(model)
    for key, index in matrix.u_index.items():
        coverage_row[index] = 1.0 / max(demand[key], 1)
    second_rows.append(coverage_row)
    second_bounds_ub.append(coverage + COVERAGE_TOL)

    second = _call_linprog(
        c=second_c,
        A_ub=second_rows or None,
        b_ub=second_bounds_ub or None,
        bounds=matrix.bounds,
    )
    if isinstance(second, str):
        return _failure(model, second)

    notes: list[str] = []
    synth = tuple(
        max(int(math.ceil(float(value) - _CEIL_TOL)), 0)
        for value in second.x[matrix.y_slice]
    )
    buys = tuple(
        _cap_buys(good, value) for good, value in zip(matrix.z_order, second.x[matrix.z_slice])
    )

    fixed_bounds = list(matrix.bounds)
    for offset, value in enumerate(synth):
        fixed_bounds[matrix.y_slice.start + offset] = (float(value), float(value))
    for offset, value in enumerate(buys):
        fixed_bounds[matrix.z_slice.start + offset] = (float(value), float(value))

    third = _call_linprog(
        c=second_c,
        A_ub=second_rows or None,
        b_ub=second_bounds_ub or None,
        bounds=fixed_bounds,
    )
    if isinstance(third, str) or getattr(third, "success", False) is not True:
        notes.append("合成与兑换取整后没有求出配套的出击次数，出击次数沿用取整前的解")
        x_raw = [float(value) for value in second.x[matrix.x_slice]]
    else:
        x_raw = [float(value) for value in third.x[matrix.x_slice]]
    runs = tuple(max(int(math.ceil(value - _CEIL_TOL)), 0) for value in x_raw)

    runs, buys, repair_notes = _repair(model, runs=runs, buys=buys, budget=budget)
    notes.extend(repair_notes)

    shortfall = _shortfall(model, runs=runs, synth=synth, buys=buys)
    total_sanity = sum(stage.ap_cost * taken for stage, taken in zip(model.options, runs))
    return FarmingSolution(
        runs=runs,
        synth=synth,
        buys=buys,
        shortfall=shortfall,
        total_sanity=int(total_sanity),
        status=None,
        notes=tuple(notes),
    )


def _call_linprog(**kwargs: object) -> object | str:
    """调 ``planner._linprog_call``；抛异常或无解都折成一句可读的原因。"""
    try:
        result = planner._linprog_call(**kwargs)
    except Exception as exc:  # scipy 缺失、数值异常……都不该让整次规划失败
        return f"线性规划不可用（{type(exc).__name__}: {exc}）"
    if result is None or not getattr(result, "success", False):
        message = getattr(result, "message", None) or "求解器没有给出解"
        return f"线性规划不可用（{message}）"
    return result


def _cap_buys(good: ShopGood, value: float) -> int:
    count = max(int(math.ceil(float(value) - _CEIL_TOL)), 0)
    if good.stock is not None:
        count = min(count, max(int(good.stock), 0))
    return count


# --------------------------------------------------------------------------- #
# 取整后修复
# --------------------------------------------------------------------------- #


def _repair(
    model: FarmingModel,
    *,
    runs: tuple[int, ...],
    buys: tuple[int, ...],
    budget: float | None,
) -> tuple[tuple[int, ...], tuple[int, ...], list[str]]:
    """按整数解复查预算/截止行与代币行；超了就削减，材料缺口留给 ``shortfall``。"""
    x = list(runs)
    z = list(buys)
    cuts = 0
    token_cuts = 0

    budget_rows: list[tuple[list[int], float]] = []
    for moment, amount in sorted(model.deadline_budgets, key=lambda item: item[0]):
        indices = [
            index
            for index, stage in enumerate(model.options)
            if (deadline := model.deadline_of.get(stage.stage_id)) is not None
            and deadline <= moment
        ]
        budget_rows.append((indices, float(amount)))
    if budget is not None:
        budget_rows.append((list(range(len(model.options))), float(budget)))

    while True:
        reduced = False
        for indices, limit in budget_rows:
            if not indices:
                continue
            spent = sum(model.options[index].ap_cost * x[index] for index in indices)
            if spent <= limit + _ROW_TOL:
                continue
            candidates = [index for index in indices if x[index] > 0]
            if not candidates:
                continue
            chosen = max(
                candidates,
                key=lambda index: (model.options[index].ap_cost, model.options[index].stage_id),
            )
            x[chosen] -= 1
            cuts += 1
            reduced = True
            break
        if not reduced:
            break

    while True:
        reduced = False
        for token in sorted({good.token for good in model.shop}):
            earned = sum(
                tokens[1] * x[index]
                for index, stage in enumerate(model.options)
                if (tokens := model.tokens_of.get(stage.stage_id)) is not None
                and tokens[0] == token
            )
            spent_tokens = sum(
                good.price * z[index]
                for index, good in enumerate(model.shop)
                if good.token == token
            )
            if spent_tokens <= model.owned_tokens.get(token, 0) + earned + _ROW_TOL:
                continue
            candidates = [
                index
                for index, good in enumerate(model.shop)
                if good.token == token and z[index] > 0
            ]
            if not candidates:
                continue
            z[_pick_shop_cut(model.shop, candidates)] -= 1
            token_cuts += 1
            reduced = True
            break
        if not reduced:
            break

    notes: list[str] = []
    if cuts or token_cuts:
        notes.append(f"取整后按预算削减了 {cuts} 次出击、按代币削减了 {token_cuts} 份兑换")
    return tuple(x), tuple(z), notes


def _pick_shop_cut(shop: list[ShopGood], candidates: list[int]) -> int:
    """``unit_sanity`` 最低的优先削减；null 视为最低，同值取 ``_key(item)`` 字典序大的。"""
    best_key = min(candidates, key=lambda index: _unit_rank(shop[index]))
    rank = _unit_rank(shop[best_key])
    tied = [index for index in candidates if _unit_rank(shop[index]) == rank]
    return max(tied, key=lambda index: planner._key(shop[index].item))


def _unit_rank(good: ShopGood) -> tuple[int, float]:
    if good.unit_sanity is None:
        return (0, 0.0)
    return (1, float(good.unit_sanity))


# --------------------------------------------------------------------------- #
# 结果重算
# --------------------------------------------------------------------------- #


def _shortfall(
    model: FarmingModel,
    *,
    runs: Iterable[int],
    synth: Iterable[int],
    buys: Iterable[int],
) -> dict[str, float]:
    supply: dict[str, float] = {}
    for stage, taken in zip(model.options, runs):
        for key, rate in stage.rates.items():
            supply[key] = supply.get(key, 0.0) + float(rate) * taken
    for recipe, taken in zip(model.recipes, synth):
        supply[recipe.product] = supply.get(recipe.product, 0.0) + float(recipe.output) * taken
        for key, amount in recipe.materials:
            supply[key] = supply.get(key, 0.0) - float(amount) * taken
    for good, taken in zip(model.shop, buys):
        supply[good.key] = supply.get(good.key, 0.0) + float(good.bundle) * taken

    shortfall: dict[str, float] = {}
    for key in sorted(model.needs, key=planner._key):
        missing = float(model.needs[key] - model.depot.get(key, 0)) - supply.get(key, 0.0)
        shortfall[key] = 0.0 if missing <= 1e-9 else round(missing, 6)
    return shortfall


def _zero_solution(model: FarmingModel) -> FarmingSolution:
    return FarmingSolution(
        runs=(0,) * len(model.options),
        synth=(0,) * len(model.recipes),
        buys=(0,) * len(model.shop),
        shortfall={key: 0.0 for key in sorted(model.needs, key=planner._key)},
        total_sanity=0,
        status=None,
        notes=(),
    )


def _failure(model: FarmingModel, message: str) -> FarmingSolution:
    return FarmingSolution(
        runs=(0,) * len(model.options),
        synth=(0,) * len(model.recipes),
        buys=(0,) * len(model.shop),
        shortfall={
            key: float(max(int(model.needs[key]) - int(model.depot.get(key, 0)), 0))
            for key in sorted(model.needs, key=planner._key)
        },
        total_sanity=0,
        status=message,
        notes=(message,),
    )
