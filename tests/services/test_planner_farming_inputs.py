"""M88 T1：规划器公共取数接缝（``farming_inputs``）与计划缺口（``missing_needs``）。

- ``plan_needs`` 的 ``as_dict()`` 在重构前后逐字段不变：``GOLDEN`` 是重构**前**用同一组
  固定假数据跑出来的原样输出（规格 §5 T1 步骤 3）。
- ``FarmingInputs.needs`` 已去掉龙门币与作战记录；``rate_status`` 与 ``plan_needs`` 的
  ``unobtainable.reason`` 同源。
- ``missing_needs`` 保留作战记录（v2 ``refresh`` 既有口径：只排除龙门币）。

全部用固定假数据与 ``FakePrts``/``FakeDrops``，不联网；常驻关表用 ``monkeypatch`` 换成
固定集合（与 ``test_upgrade_planner`` 同一接缝）。
"""

from __future__ import annotations

import asyncio
import functools

import pytest

from maa_api.domain.errors import AppError, ErrorCode
from maa_api.services.account import planner
from tests.services.test_upgrade_plan_lifecycle import (
    FakeDepot,
    FakePipelineService,
    FakePlanner,
    FakeScheduleService,
    plan_payload,
    service,
)
from tests.services.test_upgrade_planner import TARGET, harness, stage_row

#: 重构前（HEAD 2a7abe2）跑出来的 ``plan_needs(...).as_dict()`` 原样输出。
GOLDEN = {
    "days_estimated": 0.25,
    "feasible": True,
    "notes": [
        "候选关卡：企鹅物流在 CN 开放、理智消耗 > 0、样本数 ≥ 100，且 MAA 能导航"
        "（stages.json 常驻关 + 有导航文件的活动关）",
        "掉率来自企鹅物流（CN）：2024-01-01 起，至今，参与规划的样本数 1000–1000",
    ],
    "requirements": {"目标料": 10},
    "sanity_per_day": 240,
    "server": "CN",
    "stages": [
        {
            "items": [
                {
                    "ended_at": None,
                    "item_id": "id-目标料",
                    "item_name": "目标料",
                    "rate": 1.0,
                    "sanity_per_item": 6.0,
                    "started_at": "2024-01-01",
                    "times": 1000,
                }
            ],
            "runs": 10,
            "sanity": 60,
            "sanity_per_run": 6,
            "stage_code": "1-7",
            "stage_id": "main_01-07",
        }
    ],
    "surplus": {},
    "synthesis": [],
    "synthesis_lmd": 0,
    "targets": [],
    "total_runs": 10,
    "total_sanity": 60,
    "unknowns": [],
    "unobtainable": [],
}


def async_test(func):
    """本仓库不装 pytest-asyncio：异步用例统一用 ``asyncio.run`` 跑。"""

    @functools.wraps(func)
    def wrapper(*args, **kwargs):
        return asyncio.run(func(*args, **kwargs))

    return wrapper


@pytest.fixture(autouse=True)
def permanent_stages(monkeypatch: pytest.MonkeyPatch) -> None:
    """本机 stages.json 换成固定集合：测试不读真实资源目录。"""
    monkeypatch.setattr(
        planner, "permanent_stage_ids", lambda: frozenset({"main_01-07", "main_04-04"})
    )


# --------------------------------------------------------------------------- #
# 重构不回归
# --------------------------------------------------------------------------- #
@async_test
async def test_plan_needs_unchanged_after_refactor() -> None:
    h = harness(
        rates={
            TARGET: [
                stage_row("main_01-07", "1-7", TARGET, times=1000, quantity=1000, ap_cost=6)
            ]
        }
    )

    plan = await h.planner.plan_needs({TARGET: 10})

    assert plan.as_dict() == GOLDEN


# --------------------------------------------------------------------------- #
# farming_inputs
# --------------------------------------------------------------------------- #
@async_test
async def test_farming_inputs_drops_report_only_items() -> None:
    h = harness(default_rates=True)

    inputs = await h.planner.farming_inputs({"龙门币": 30000, "作战记录": 5, TARGET: 10})

    assert set(inputs.needs) == {planner._key(TARGET)}
    assert inputs.needs[planner._key(TARGET)] == 10
    assert any("只报需求" in note for note in inputs.notes)
    assert inputs.server == "CN"


@async_test
async def test_farming_inputs_empty_after_report_only() -> None:
    h = harness(default_rates=True)

    inputs = await h.planner.farming_inputs({"龙门币": 1, "作战记录": 1})

    assert inputs.needs == {}
    assert inputs.options == []
    assert inputs.recipes == []
    assert any("没有剩余需求" in note for note in inputs.notes)


@async_test
async def test_farming_inputs_rate_status_matches_plan_needs() -> None:
    h = harness()  # 不给 TARGET 的掉率：两边都应记「企鹅物流没有取到」

    inputs = await h.planner.farming_inputs({TARGET: 10})
    plan = await h.planner.plan_needs({TARGET: 10})

    reason = plan.as_dict()["unobtainable"][0]["reason"]
    assert inputs.rate_status[planner._key(TARGET)] == reason
    assert "企鹅物流" in reason


# --------------------------------------------------------------------------- #
# missing_needs
# --------------------------------------------------------------------------- #
@async_test
async def test_missing_needs_subtracts_depot() -> None:
    _factory, handle = await service(
        planner=FakePlanner(plan=plan_payload(requirements={"固源岩组": 30})),
        depot=FakeDepot({"固源岩组": 12}),
        schedule=FakeScheduleService(),
        pipeline=FakePipelineService(),
    )
    started = await handle.start(name="甲", targets=[{"operator": "甲"}], run_times=["08:00"])

    assert await handle.missing_needs(started["plan"]["id"]) == {"固源岩组": 18}


@async_test
async def test_missing_needs_keeps_battle_records() -> None:
    _factory, handle = await service(
        planner=FakePlanner(plan=plan_payload(requirements={"作战记录": 5})),
        depot=FakeDepot({}),
        schedule=FakeScheduleService(),
        pipeline=FakePipelineService(),
    )
    started = await handle.start(name="甲", targets=[{"operator": "甲"}], run_times=["08:00"])

    assert await handle.missing_needs(started["plan"]["id"]) == {"作战记录": 5}


@async_test
async def test_missing_needs_plan_not_found() -> None:
    _factory, handle = await service()

    with pytest.raises(AppError) as exc:
        await handle.missing_needs("没有这个计划")

    assert exc.value.code is ErrorCode.UPGRADE_PLAN_NOT_FOUND
