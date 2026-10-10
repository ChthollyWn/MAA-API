"""UpgradePlanService 的既有行为（启动、列表顺带重算、收尾钩子、取消失败路径）。

M31 新增的生命周期（预览、剩余场次重算、删除钩子）在
``tests/services/test_upgrade_plan_lifecycle.py``；这里只钉住原有语义不被回归。
替身与包装函数直接从那个文件复用，避免两份假实现漂移。
"""

from __future__ import annotations

import pytest

from maa_api.domain.errors import AppError, ErrorCode
from maa_api.services.account.plans import _cron_from_times
from tests.services.test_upgrade_plan_lifecycle import (
    FakeDepot,
    FakePipelineService,
    FakePlanner,
    FakeScheduleService,
    async_test,
    fights,
    service,
)


@async_test
async def test_start_without_run_times_is_rejected_before_any_write():
    pipeline = FakePipelineService()
    schedule = FakeScheduleService()
    _factory, handle = await service(pipeline=pipeline, schedule=schedule)

    with pytest.raises(AppError) as invalid:
        await handle.start(name="甲", targets=[{"operator": "甲"}], run_times=["  "])

    assert invalid.value.code == ErrorCode.INVALID_PARAMETER
    assert pipeline.submissions == [] and schedule.rows == []


@async_test
async def test_start_persists_the_plan_and_both_delivery_shapes():
    pipeline = FakePipelineService()
    schedule = FakeScheduleService()
    _factory, handle = await service(pipeline=pipeline, schedule=schedule)

    started = await handle.start(
        name="甲", targets=[{"operator": "甲"}], run_times=["08:00"]
    )

    plan = started["plan"]
    assert plan["status"] == "active"
    assert plan["targets"] == [{"operator": "甲"}]
    assert plan["requirements"] == {"固源岩组": 4}
    assert plan["progress"]["total_sanity"] == 18
    assert plan["schedule_id"] == "schedule-1"
    assert started["run_times"] == ["08:00"]
    assert started["medicine"] == {"today": 0, "daily_expiring_medicine": 999, "stone": 0}
    assert pipeline.submissions[0]["title"] == "养成计划：甲（今天）"
    assert fights(pipeline.submissions[0]["tasks"]) == [
        {"stage": "1-7", "times": 3, "medicine": 0, "expiring_medicine": 0}
    ]
    assert schedule.row("schedule-1")["name"] == "养成计划：甲"
    assert schedule.row("schedule-1")["cron"] == "0 8 * * *"

    listed = await handle.list_plans()
    assert listed["total"] == 1 and listed["plans"][0]["id"] == plan["id"]


@async_test
async def test_list_plans_refreshes_active_plans_and_completes_them():
    schedule = FakeScheduleService()
    _factory, handle = await service(schedule=schedule, depot=FakeDepot({"固源岩组": 9}))
    started = await handle.start(name="甲", targets=[{"operator": "甲"}], run_times=["08:00"])

    listed = await handle.list_plans()

    assert listed["plans"][0]["status"] == "completed"
    assert schedule.rows == []
    assert (await handle._get_plan(started["plan"]["id"]))["status"] == "completed"


@async_test
async def test_pipeline_finished_hook_refreshes_and_swallows_errors():
    _factory, handle = await service(depot=FakeDepot())
    started = await handle.start(name="甲", targets=[{"operator": "甲"}], run_times=["08:00"])

    async def boom(plan_id: str):
        raise RuntimeError("重算炸了")

    handle.refresh = boom  # type: ignore[method-assign] - 钩子必须自己吞掉异常
    await handle.on_pipeline_finished("pipeline-1")

    async def unreadable():
        raise RuntimeError("库读不到")

    reading = handle._load_plans
    handle._load_plans = unreadable  # type: ignore[method-assign]
    await handle.on_pipeline_finished("pipeline-2")
    handle._load_plans = reading  # type: ignore[method-assign]
    assert (await handle._get_plan(started["plan"]["id"]))["status"] == "active"


@async_test
async def test_cancel_unknown_plan_raises_not_found():
    _factory, handle = await service()

    with pytest.raises(AppError) as missing:
        await handle.cancel("no-such-plan")

    assert missing.value.code == ErrorCode.UPGRADE_PLAN_NOT_FOUND


def test_cron_from_times_maps_and_rejects():
    assert _cron_from_times(["08:00", "20:00"]) == "0 8,20 * * *"
    with pytest.raises(AppError) as invalid:
        _cron_from_times(["25:00"])
    assert invalid.value.code == ErrorCode.INVALID_PARAMETER


@async_test
async def test_refresh_without_requirements_is_a_noop():
    planner = FakePlanner(plan={"requirements": {}, "stages": [], "total_sanity": 0})
    _factory, handle = await service(planner=planner)
    started = await handle.start(name="甲", targets=[{"operator": "甲"}], run_times=["08:00"])

    refreshed = await handle.refresh(started["plan"]["id"])

    assert refreshed["status"] == "active"
    assert planner.needs_calls == []
