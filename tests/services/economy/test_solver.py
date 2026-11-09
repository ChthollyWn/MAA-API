"""M88 T3：刷图线性规划求解器（规格 §4.5.3 / §6 的求解器用例）。

全部用手造的 ``FarmingModel``：不联网、不读盘；同一模型重复求解结果确定。
"""

from __future__ import annotations

import asyncio
import functools
from datetime import datetime, timedelta, timezone

import pytest

from maa_api.services.account import planner
from maa_api.services.economy.model import FarmingModel, ShopGood
from maa_api.services.economy.solver import (
    COVERAGE_TOL,
    EPSILON,
    SHORTFALL_TOL,
    build_matrix,
    solve_farming,
)

NOW = datetime(2026, 10, 20, 12, 0, tzinfo=timezone(timedelta(hours=8)))
WORD = "异铁块"
TOKEN = "“未来瞻望镜”"


def _model(*, needs, options, recipes=(), shop=(), owned=(), deadlines=(), now=NOW):
    return FarmingModel(needs=dict(needs), depot={}, display={k: k for k in needs},
                        options=list(options), event_of={}, deadline_of={}, navigable_of={},
                        tokens_of={}, recipes=list(recipes), shop=list(shop),
                        owned_tokens=dict(owned), deadline_budgets=list(deadlines), now=now)


def _permanent(stage_id="main_01-07", key=WORD, rate=1.0, ap=30):
    return planner.StageOption(stage_id=stage_id, stage_code="1-7", ap_cost=ap, rates={key: rate},
                               details=[{"item_name": key, "sanity_per_item": ap / rate}])


def _event(stage_id="act52side_06", tokens=42, ap=21, key=WORD, rate=0.0):
    return planner.StageOption(stage_id=stage_id, stage_code="YW-6", ap_cost=ap, rates={key: rate})


def _good(*, item=WORD, key=None, price=40, stock=10, bundle=1, unit_sanity=None, token=TOKEN):
    return ShopGood(activity="昨日海", token=token, item=item, key=key or planner._key(item),
                    bundle=bundle, price=price, stock=stock, section="金色阶段",
                    unit_sanity=unit_sanity)


def test_matrix_row_order_matches_spec() -> None:
    model = _model(needs={WORD: 10}, options=[_permanent()], shop=[_good()],
                   owned={TOKEN: 100}, deadlines=[(NOW + timedelta(days=1), 210.0)])

    matrix = build_matrix(model, budget=240)

    assert matrix.row_names[0] == f"material:{WORD}"
    assert matrix.row_names[1] == f"token:{TOKEN}"
    assert matrix.row_names[2].startswith("deadline:")
    assert matrix.row_names[3] == "budget"
    assert matrix.x_index == {"main_01-07": 0}
    assert len(matrix.bounds) == len(matrix.c)


def test_permanent_only_matches_v2_total() -> None:
    model = _model(needs={WORD: 10}, options=[_permanent()])

    rows, recipes, _ = planner._supply_analysis(
        needs=model.needs, depot={}, has_stage={WORD: True}, recipes=[]
    )
    v2 = planner._solve_allocation(
        rows=rows, stages=model.options, recipes=recipes, needs=model.needs, depot={}
    )
    solution = solve_farming(model)

    expected = sum(runs * stage.ap_cost for runs, stage in zip(v2.runs, model.options))
    assert solution.total_sanity == expected == 300


def test_shop_beats_farming_when_cheaper() -> None:
    model = _model(needs={WORD: 10}, options=[_event(), _permanent()],
                   shop=[_good(price=40, stock=10)])
    model.tokens_of["act52side_06"] = (TOKEN, 42)

    solution = solve_farming(model)

    runs = dict(zip([stage.stage_id for stage in model.options], solution.runs))
    assert solution.buys == (10,)
    assert runs["act52side_06"] == 10
    assert runs["main_01-07"] == 0
    assert solution.total_sanity == 210


def test_stock_cap_is_respected() -> None:
    model = _model(needs={WORD: 15}, options=[_permanent()],
                   shop=[_good(price=40, stock=10)], owned={TOKEN: 400})

    solution = solve_farming(model)

    assert solution.buys == (10,)
    assert solution.runs == (5,)
    assert solution.total_sanity == 150


def test_token_row_binds() -> None:
    model = _model(needs={WORD: 10}, options=[_permanent()],
                   shop=[_good(price=40, stock=None)], owned={TOKEN: 80})

    solution = solve_farming(model)

    assert solution.buys == (2,)


def test_event_deadline_caps_event_runs() -> None:
    model = _model(needs={WORD: 10}, options=[_event()],
                   shop=[_good(price=40, stock=10)],
                   deadlines=[(NOW + timedelta(days=1), 210.0)])
    model.tokens_of["act52side_06"] = (TOKEN, 42)

    solution = solve_farming(model)

    assert solution.runs[0] <= 10
    assert solution.total_sanity <= 210


def test_cumulative_event_budget_caps_overlapping_events() -> None:
    first = _event(stage_id="act51side_01", tokens=30, ap=30)
    second = _event(stage_id="act52side_01", tokens=30, ap=30)
    model = _model(needs={WORD: 10}, options=[first, second], shop=[_good(price=30, stock=10)])
    deadline = NOW + timedelta(days=1)
    model.deadline_of["act51side_01"] = deadline
    model.deadline_of["act52side_01"] = deadline
    model.tokens_of["act51side_01"] = (TOKEN, 30)
    model.tokens_of["act52side_01"] = (TOKEN, 30)
    model.deadline_budgets = [(deadline, 240.0)]

    solution = solve_farming(model)

    assert solution.runs[0] + solution.runs[1] <= 8
    assert solution.total_sanity <= 240


def test_budget_mode_spreads_coverage() -> None:
    both = planner.StageOption(
        stage_id="main_01-07", stage_code="1-7", ap_cost=60,
        rates={WORD: 1.0, "聚酸酯组": 1.0},
        details=[{"item_name": WORD, "sanity_per_item": 60.0},
                 {"item_name": "聚酸酯组", "sanity_per_item": 60.0}],
    )
    model = _model(needs={WORD: 10, "聚酸酯组": 10}, options=[both])

    solution = solve_farming(model, budget=300)

    assert solution.status is None
    for key in model.needs:
        coverage = 1 - solution.shortfall[key] / model.needs[key]
        assert 0.4 <= coverage <= 0.6


def test_unobtainable_shows_as_shortfall() -> None:
    model = _model(needs={WORD: 10}, options=[])

    solution = solve_farming(model)

    assert solution.shortfall == {WORD: 10}
    assert solution.runs == ()
    assert solution.status is None


def test_integerization_respects_stock() -> None:
    model = _model(needs={WORD: 5}, options=[_permanent()],
                   shop=[_good(price=40, stock=2)], owned={TOKEN: 200})

    solution = solve_farming(model)

    assert solution.buys[0] <= 2
    assert all(isinstance(runs, int) for runs in solution.runs)


def test_integerization_respects_token_budget() -> None:
    model = _model(needs={WORD: 10}, options=[_permanent()],
                   shop=[_good(price=40, stock=None)], owned={TOKEN: 50})

    solution = solve_farming(model)

    spent = sum(good.price * count for good, count in zip(model.shop, solution.buys))
    earned = 0
    assert spent <= 50 + earned
    assert any("按代币削减" in note for note in solution.notes)


def test_integerization_respects_window_budget() -> None:
    model = _model(needs={WORD: 10}, options=[_permanent(ap=21)])

    solution = solve_farming(model, budget=205)

    assert sum(runs * 21 for runs in solution.runs) <= 205


def test_linprog_failure_is_reported(monkeypatch: pytest.MonkeyPatch) -> None:
    def boom(**kwargs):
        raise RuntimeError("scipy 不在")

    monkeypatch.setattr(planner, "_linprog_call", boom)
    model = _model(needs={WORD: 10}, options=[_permanent()])

    solution = solve_farming(model)

    assert solution.status is not None
    assert "线性规划不可用" in solution.status
