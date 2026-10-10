"""M31 收尾：养成计划的生命周期（预览、剩余场次重算、删除定时任务同步取消）。

规格 docs/superpowers/specs/2026-10-09-m25-prts-knowledge.md §7.7 的第 1、3、4 条：
一次确认的预览卡片、每次流水线结束后重算剩余场次并更新同一条定时任务、用户在任务页
删除任务时计划同步取消。异步用例沿用仓库既有的 ``async_test`` + ``asyncio.run`` 包装
（本仓没有 pytest-asyncio）。
"""

from __future__ import annotations

import asyncio
import functools
from types import SimpleNamespace
from typing import Any, Mapping, Sequence

from sqlalchemy import event
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker
from sqlalchemy.pool import StaticPool
from sqlmodel import SQLModel

import maa_api.db.models  # noqa: F401
from maa_api.db.session import make_engine
from maa_api.services.account.plans import (
    EXPIRING_MEDICINE,
    MAX_FIGHT_RUNS,
    UpgradePlanService,
    build_daily_template,
    build_today_tasks,
    dropped_stage_codes,
    rebuild_template_fights,
    split_fight_runs,
)
from maa_api.services.schedule_service import ScheduleWrite

STAGE = {"stage_id": "main_01-07", "stage_code": "1-7", "runs": 3, "sanity_per_run": 6, "sanity": 18}
STAGE_CE = {"stage_id": "main_05-07", "stage_code": "CE-5", "runs": 2, "sanity_per_run": 30, "sanity": 60}


def async_test(func):
    @functools.wraps(func)
    def wrapper(*args, **kwargs):
        return asyncio.run(func(*args, **kwargs))

    return wrapper


# --------------------------------------------------------------------------- #
# 替身
# --------------------------------------------------------------------------- #
class _Result:
    """planner 返回值替身：调用方只用 ``as_dict()``。"""

    def __init__(self, payload: Mapping[str, Any]) -> None:
        self.payload = dict(payload)

    def as_dict(self) -> dict[str, Any]:
        return dict(self.payload)


def plan_payload(
    *,
    stages: Sequence[Mapping[str, Any]] | None = None,
    requirements: Mapping[str, Any] | None = None,
    total_sanity: int | None = None,
    days_estimated: float | None = None,
    unobtainable: Sequence[Mapping[str, Any]] | None = None,
) -> dict[str, Any]:
    rows = [dict(row) for row in (stages if stages is not None else [STAGE])]
    sanity = (
        int(total_sanity)
        if total_sanity is not None
        else sum(int(row.get("sanity") or 0) for row in rows)
    )
    return {
        "requirements": dict(requirements if requirements is not None else {"固源岩组": 4}),
        "stages": rows,
        "total_sanity": sanity,
        "days_estimated": round(sanity / 240, 2) if days_estimated is None else days_estimated,
        "unobtainable": [dict(row) for row in (unobtainable or ())],
        "notes": ["规划说明"],
        "feasible": not unobtainable,
    }


class FakePlanner:
    """``plan``（启动/预览）与 ``plan_needs``（重算）各给一个预设结果。"""

    def __init__(
        self,
        *,
        plan: Mapping[str, Any] | None = None,
        needs: Mapping[str, Any] | None = None,
    ) -> None:
        self.plan_payload = dict(plan) if plan is not None else plan_payload()
        self.needs_payload = dict(needs) if needs is not None else plan_payload()
        self.plan_error: Exception | None = None
        self.needs_error: Exception | None = None
        self.needs_calls: list[dict[str, Any]] = []

    async def plan(self, targets: Any, *, use_depot: bool = True, box: Any = None) -> _Result:
        if self.plan_error is not None:
            raise self.plan_error
        return _Result(self.plan_payload)

    async def plan_needs(
        self, needs: Mapping[str, int], *, server: str | None = None, display: Any = None
    ) -> _Result:
        self.needs_calls.append(dict(needs))
        if self.needs_error is not None:
            raise self.needs_error
        return _Result(self.needs_payload)


class FakePipelineService:
    def __init__(self) -> None:
        self.submissions: list[dict[str, Any]] = []

    async def submit(self, payload: Mapping[str, Any]) -> dict[str, Any]:
        self.submissions.append(dict(payload))
        return {"id": f"pipeline-{len(self.submissions)}"}


class FakeDepot:
    def __init__(self, counts: Mapping[str, int] | None = None) -> None:
        self.counts = dict(counts or {})

    async def list_items(self, *, limit: int = 1000, offset: int = 0) -> dict[str, Any]:
        items = [
            {"item_id": f"id-{name}", "name": name, "count": count}
            for name, count in self.counts.items()
        ]
        return {"items": items, "total": len(items), "limit": limit, "offset": offset}


class FakeScheduleService:
    """内存版 ScheduleService：``list`` 走 ``SchedulePage`` 形状，``delete`` 内联 await 钩子。"""

    def __init__(self) -> None:
        self.rows: list[dict[str, Any]] = []
        self.updated: list[tuple[str, ScheduleWrite]] = []
        self.deleted: list[str] = []
        self._hook: Any = None
        self._next = 0

    def bind_delete_hook(self, hook: Any) -> None:
        self._hook = hook

    def seed(
        self,
        *,
        name: str = "已有任务",
        cron: str = "0 8 * * *",
        template: Sequence[Mapping[str, Any]] | None = None,
        **fields: Any,
    ) -> str:
        self._next += 1
        row: dict[str, Any] = {
            "id": f"schedule-{self._next}",
            "name": name,
            "cron": cron,
            "timezone": "Asia/Shanghai",
            "template": [dict(task) for task in (template or ({"name": "StartUp"},))],
            "priority": 3,
            "enabled": True,
            "skip_if_running": True,
        }
        row.update(fields)
        self.rows.append(row)
        return str(row["id"])

    def row(self, schedule_id: str) -> dict[str, Any]:
        for row in self.rows:
            if row["id"] == schedule_id:
                return row
        raise AssertionError(f"定时任务不存在：{schedule_id}")

    async def list(self, *, enabled: bool | None = None) -> SimpleNamespace:
        rows = [row for row in self.rows if enabled is None or row["enabled"] is enabled]
        return SimpleNamespace(items=[SimpleNamespace(**row) for row in rows], total=len(rows))

    async def get(self, schedule_id: str) -> SimpleNamespace:
        return SimpleNamespace(**self.row(schedule_id))

    async def create(self, payload: ScheduleWrite) -> SimpleNamespace:
        schedule_id = self.seed(
            name=payload.name,
            cron=payload.cron,
            timezone=payload.timezone,
            template=payload.model_dump(exclude_unset=True)["template"],
            priority=payload.priority,
            enabled=payload.enabled,
            skip_if_running=payload.skip_if_running,
        )
        return await self.get(schedule_id)

    async def update(self, schedule_id: str, payload: ScheduleWrite) -> SimpleNamespace:
        row = self.row(schedule_id)
        row.update(payload.model_dump(exclude_unset=True))
        self.updated.append((schedule_id, payload))
        return await self.get(schedule_id)

    async def delete(self, schedule_id: str) -> None:
        row = self.row(schedule_id)
        self.rows.remove(row)
        self.deleted.append(schedule_id)
        if self._hook is not None:
            await self._hook(schedule_id)


def _disable_foreign_keys(dbapi_connection: Any, _record: Any) -> None:
    """计划行会带上替身定时任务的 schedule_id，关掉外键（与 account_smoke 同口径）。"""
    cursor = dbapi_connection.cursor()
    cursor.execute("PRAGMA foreign_keys=OFF")
    cursor.close()


async def database() -> tuple[Any, async_sessionmaker[AsyncSession]]:
    engine = make_engine("sqlite+aiosqlite:///:memory:", poolclass=StaticPool)
    event.listen(engine.sync_engine, "connect", _disable_foreign_keys)
    async with engine.begin() as connection:
        await connection.run_sync(SQLModel.metadata.create_all)
    factory = async_sessionmaker(engine, expire_on_commit=False, class_=AsyncSession)
    return engine, factory


async def service(
    *,
    planner: FakePlanner | None = None,
    pipeline: FakePipelineService | None = None,
    schedule: FakeScheduleService | None = None,
    depot: FakeDepot | None = None,
    notify: Any = None,
) -> tuple[async_sessionmaker[AsyncSession], UpgradePlanService]:
    _engine, factory = await database()
    handle = UpgradePlanService(
        factory,
        planner=planner or FakePlanner(),
        pipeline_service=pipeline,
        schedule_service=schedule,
        notify_service=notify,
        depot=depot or FakeDepot(),
    )
    return factory, handle


def fights(tasks: Sequence[Mapping[str, Any]]) -> list[dict[str, Any]]:
    """任务数组里的 Fight 关键字段（dict 与落库模板都能读）。"""
    return [
        {
            "stage": task.get("stage"),
            "times": int(task.get("times") or 0),
            "medicine": int(task.get("medicine") or 0),
            "expiring_medicine": int(task.get("expiring_medicine") or 0),
        }
        for task in tasks
        if task.get("name") == "Fight"
    ]


# --------------------------------------------------------------------------- #
# 切分纯函数
# --------------------------------------------------------------------------- #
def test_split_fight_runs_caps_every_task():
    assert MAX_FIGHT_RUNS == 20
    assert split_fight_runs(30) == [20, 10]
    assert split_fight_runs(20) == [20]
    assert split_fight_runs(21) == [20, 1]
    assert split_fight_runs(45) == [20, 20, 5]
    assert split_fight_runs(0) == []
    assert split_fight_runs(-3) == []
    assert split_fight_runs(12, limit=5) == [5, 5, 2]


def test_builders_wrap_long_fights_and_keep_daily_shape():
    stages = [{**STAGE, "runs": 30}]
    today = build_today_tasks(stages)
    daily = build_daily_template(stages)

    assert fights(today) == [
        {"stage": "1-7", "times": 20, "medicine": 0, "expiring_medicine": 0},
        {"stage": "1-7", "times": 10, "medicine": 0, "expiring_medicine": 0},
    ]
    assert today[-1] == {"name": "Depot"}
    assert daily[0] == {"name": "StartUp"}
    assert daily[-1] == {"name": "Depot"}
    assert fights(daily) == [
        {"stage": "1-7", "times": 20, "medicine": 0, "expiring_medicine": EXPIRING_MEDICINE},
        {"stage": "1-7", "times": 10, "medicine": 0, "expiring_medicine": EXPIRING_MEDICINE},
    ]
    assert all(task.get("name") != "CloseDown" for task in today + daily)


def test_rebuild_template_fights_keeps_other_tasks_and_medicine():
    template = [
        {"name": "StartUp"},
        {"name": "Fight", "stage": "1-7", "times": 3, "medicine": 1, "stone": 2,
         "expiring_medicine": 999, "series": 2},
        {"name": "Depot"},
        {"name": "Recruit"},
    ]

    rebuilt = rebuild_template_fights(template, [{**STAGE, "runs": 25}])

    assert [task["name"] for task in rebuilt] == ["StartUp", "Fight", "Fight", "Depot", "Recruit"]
    assert fights(rebuilt) == [
        {"stage": "1-7", "times": 20, "medicine": 1, "expiring_medicine": 999},
        {"stage": "1-7", "times": 5, "medicine": 1, "expiring_medicine": 999},
    ]
    # 其余字段（stone/series）从原任务带过来，没有的新增字段不出现。
    assert rebuilt[1]["stone"] == 2
    assert rebuilt[1]["series"] == 2
    # 原模板不被就地修改，空场次时原样返回。
    assert fights(template) == [
        {"stage": "1-7", "times": 3, "medicine": 1, "expiring_medicine": 999}
    ]
    assert rebuild_template_fights(template, []) == [dict(task) for task in template]


def test_rebuild_template_fights_inserts_before_closing_depot():
    rebuilt = rebuild_template_fights(
        [{"name": "StartUp"}, {"name": "Depot"}], [{**STAGE, "runs": 4}]
    )
    assert [task["name"] for task in rebuilt] == ["StartUp", "Fight", "Depot"]


def test_dropped_stage_codes_is_a_set_difference():
    assert dropped_stage_codes([STAGE, STAGE_CE], [STAGE]) == ["CE-5"]
    assert dropped_stage_codes([STAGE], [STAGE]) == []
    assert dropped_stage_codes([], [STAGE]) == []


# --------------------------------------------------------------------------- #
# preview
# --------------------------------------------------------------------------- #
@async_test
async def test_preview_does_not_write_and_matches_start_shapes():
    pipeline = FakePipelineService()
    schedule = FakeScheduleService()
    planner = FakePlanner()
    _factory, handle = await service(planner=planner, pipeline=pipeline, schedule=schedule)

    preview = await handle.preview(name="甲", targets=[{"operator": "甲"}], run_times=["08:00"])

    assert pipeline.submissions == []
    assert schedule.rows == []
    assert set(preview) == {
        "today_tasks",
        "daily_template",
        "medicine",
        "days_estimated",
        "total_sanity",
        "stages",
        "conflicts",
        "unobtainable",
        "notes",
    }
    assert preview["medicine"] == {"today": 0, "stone": 0, "expiring_medicine": EXPIRING_MEDICINE}
    assert preview["total_sanity"] == 18
    assert preview["days_estimated"] == round(18 / 240, 2)
    assert [row["stage_code"] for row in preview["stages"]] == ["1-7"]

    started = await handle.start(name="甲", targets=[{"operator": "甲"}], run_times=["08:00"])

    # 卡片里看到的就是真跑的任务（同一套拼装函数）。
    assert preview["today_tasks"] == started["today"]["tasks"]
    assert preview["daily_template"] == schedule.row(str(started["schedule_id"]))["template"]


@async_test
async def test_preview_degrades_on_planning_failure_and_bad_run_times():
    planner = FakePlanner()
    planner.plan_error = RuntimeError("掉率服务不可用")
    _factory, handle = await service(planner=planner)

    preview = await handle.preview(name="甲", targets=[{"operator": "甲"}], run_times=["25:00"])

    assert preview["today_tasks"] == [{"name": "Depot"}]
    assert preview["daily_template"] == [{"name": "StartUp"}, {"name": "Depot"}]
    assert preview["total_sanity"] == 0
    assert any("规划失败" in note and "掉率服务不可用" in note for note in preview["notes"])
    assert any("运行时刻不可用" in note for note in preview["notes"])
    assert any("没有需要刷的关卡" in note for note in preview["notes"])

    no_times = await handle.preview(name="甲", targets=[{"operator": "甲"}], run_times=[])
    assert any("run_times 为空" in note for note in no_times["notes"])


@async_test
async def test_preview_reports_conflicts_unobtainable_and_splits_fights():
    schedule = FakeScheduleService()
    schedule.seed(name="日常刷材料", template=[{"name": "StartUp"}, {"name": "Fight", "times": 6}])
    schedule.seed(name="只用基建", template=[{"name": "StartUp"}])
    planner = FakePlanner(
        plan=plan_payload(
            stages=[{**STAGE, "runs": 30}],
            unobtainable=[
                {"name": "扭转醇", "amount": 5, "have": 0, "missing": 5, "reason": "关卡已关闭"}
            ],
        )
    )
    _factory, handle = await service(planner=planner, schedule=schedule)

    preview = await handle.preview(name="甲", targets=[{"operator": "甲"}], run_times=["08:00"])

    assert preview["conflicts"] == [{"schedule_id": "schedule-1", "name": "日常刷材料"}]
    assert [row["name"] for row in preview["unobtainable"]] == ["扭转醇"]
    assert [task["times"] for task in preview["today_tasks"] if task["name"] == "Fight"] == [20, 10]
    assert "规划说明" in preview["notes"]


# --------------------------------------------------------------------------- #
# start 的切分
# --------------------------------------------------------------------------- #
@async_test
async def test_start_splits_long_fights_in_pipeline_and_template():
    pipeline = FakePipelineService()
    schedule = FakeScheduleService()
    planner = FakePlanner(plan=plan_payload(stages=[{**STAGE, "runs": 30}]))
    _factory, handle = await service(planner=planner, pipeline=pipeline, schedule=schedule)

    started = await handle.start(name="甲", targets=[{"operator": "甲"}], run_times=["08:00", "20:00"])

    submitted = pipeline.submissions[0]["tasks"]
    assert [task["times"] for task in submitted if task["name"] == "Fight"] == [20, 10]
    template = schedule.row(str(started["schedule_id"]))["template"]
    assert [task["times"] for task in template if task["name"] == "Fight"] == [20, 10]
    assert template[0]["name"] == "StartUp"
    assert fights(template)[0]["expiring_medicine"] == EXPIRING_MEDICINE


# --------------------------------------------------------------------------- #
# refresh：剩余需求重算 + 更新同一条定时任务
# --------------------------------------------------------------------------- #
@async_test
async def test_refresh_replans_missing_needs_and_updates_the_same_schedule():
    pipeline = FakePipelineService()
    schedule = FakeScheduleService()
    planner = FakePlanner(
        plan=plan_payload(requirements={"固源岩组": 4, "龙门币": 180000}),
        needs=plan_payload(
            stages=[{**STAGE, "runs": 8, "sanity": 48}],
            requirements={"固源岩组": 4},
            total_sanity=48,
        ),
    )
    _factory, handle = await service(
        planner=planner, pipeline=pipeline, schedule=schedule, depot=FakeDepot({"固源岩组": 1})
    )
    started = await handle.start(name="甲", targets=[{"operator": "甲"}], run_times=["08:00"])
    schedule_id = str(started["schedule_id"])
    before = dict(schedule.row(schedule_id))

    refreshed = await handle.refresh(started["plan"]["id"])

    # 剩余需求 = 需求 − 仓库（龙门币只报需求，不参与判定），直接喂给 plan_needs。
    assert planner.needs_calls == [{"固源岩组": 3}]
    progress = refreshed["progress"]
    assert progress["missing"] == {"固源岩组": 3}
    assert [row["stage_code"] for row in progress["stages"]] == ["1-7"]
    assert progress["total_sanity"] == 48
    assert progress["days_estimated"] == 0.2
    assert progress["replanned_at"]
    assert progress["closed_dropped"] == []
    assert "replan_error" not in progress
    assert refreshed["status"] == "active"

    after = schedule.row(schedule_id)
    assert len(schedule.updated) == 1 and schedule.updated[0][0] == schedule_id
    assert after["id"] == before["id"]
    assert (after["name"], after["cron"], after["timezone"]) == (
        before["name"],
        before["cron"],
        before["timezone"],
    )
    # priority/enabled/skip_if_running 原样带回（默认优先级 2 = Priority.SCHEDULED）。
    assert (after["priority"], after["enabled"], after["skip_if_running"]) == (2, True, True)
    assert [task["times"] for task in after["template"] if task["name"] == "Fight"] == [8]
    assert after["template"][0]["name"] == "StartUp"
    assert fights(after["template"])[0]["expiring_medicine"] == EXPIRING_MEDICINE
    # 没有重复确认：用药口径与任务名保持不变。
    assert after["name"] == before["name"]


@async_test
async def test_refresh_records_closed_stages_dropped_from_the_template():
    schedule = FakeScheduleService()
    planner = FakePlanner(
        plan=plan_payload(stages=[STAGE, STAGE_CE], requirements={"固源岩组": 4, "源岩": 2}),
        needs=plan_payload(stages=[STAGE], requirements={"固源岩组": 3}),
    )
    _factory, handle = await service(planner=planner, schedule=schedule, depot=FakeDepot())
    started = await handle.start(name="甲", targets=[{"operator": "甲"}], run_times=["08:00"])

    refreshed = await handle.refresh(started["plan"]["id"])

    assert refreshed["progress"]["closed_dropped"] == ["CE-5"]
    template = schedule.row(str(started["schedule_id"]))["template"]
    assert [task["stage"] for task in template if task["name"] == "Fight"] == ["1-7"]


@async_test
async def test_refresh_keeps_old_stages_when_replan_fails():
    schedule = FakeScheduleService()
    planner = FakePlanner(plan=plan_payload(requirements={"固源岩组": 4}))
    planner.needs_error = RuntimeError("网络不可用")
    _factory, handle = await service(planner=planner, schedule=schedule, depot=FakeDepot())
    started = await handle.start(name="甲", targets=[{"operator": "甲"}], run_times=["08:00"])
    before = schedule.row(str(started["schedule_id"]))

    refreshed = await handle.refresh(started["plan"]["id"])

    progress = refreshed["progress"]
    assert "网络不可用" in progress["replan_error"]
    assert [row["stage_code"] for row in progress["stages"]] == ["1-7"]
    assert progress["total_sanity"] == 18
    assert schedule.updated == []
    assert [task["times"] for task in before["template"] if task["name"] == "Fight"] == [3]
    assert refreshed["status"] == "active"


@async_test
async def test_refresh_notes_when_the_schedule_is_already_gone():
    schedule = FakeScheduleService()
    planner = FakePlanner(plan=plan_payload(requirements={"固源岩组": 4}))
    _factory, handle = await service(planner=planner, schedule=schedule, depot=FakeDepot())
    started = await handle.start(name="甲", targets=[{"operator": "甲"}], run_times=["08:00"])
    await schedule.delete(str(started["schedule_id"]))  # 钩子没绑：只是任务没了

    refreshed = await handle.refresh(started["plan"]["id"])

    assert "跳过每日任务的场次更新" in refreshed["progress"]["schedule_note"]
    assert refreshed["status"] == "active"
    assert schedule.updated == []


@async_test
async def test_refresh_without_schedule_notes_and_skips():
    planner = FakePlanner(
        plan=plan_payload(stages=[], requirements={"固源岩组": 4}, total_sanity=0),
        needs=plan_payload(stages=[STAGE], requirements={"固源岩组": 3}),
    )
    _factory, handle = await service(planner=planner, depot=FakeDepot())

    started = await handle.start(name="甲", targets=[{"operator": "甲"}], run_times=["08:00"])
    refreshed = await handle.refresh(started["plan"]["id"])

    assert started["schedule_id"] is None
    assert "计划没有定时任务" in refreshed["progress"]["schedule_note"]


# --------------------------------------------------------------------------- #
# 删除钩子：删任务 → 计划取消；计划自己删任务 → 不误伤完成态
# --------------------------------------------------------------------------- #
@async_test
async def test_deleting_the_schedule_cancels_the_matching_active_plan():
    schedule = FakeScheduleService()
    planner = FakePlanner()
    _factory, handle = await service(planner=planner, schedule=schedule, depot=FakeDepot())
    schedule.bind_delete_hook(handle.on_schedule_deleted)
    first = await handle.start(name="甲", targets=[{"operator": "甲"}], run_times=["08:00"])
    second = await handle.start(name="乙", targets=[{"operator": "乙"}], run_times=["08:00"])

    await schedule.delete(str(first["schedule_id"]))

    plans = {plan["id"]: plan for plan in (await handle.list_plans())["plans"]}
    assert plans[first["plan"]["id"]]["status"] == "cancelled"
    assert plans[second["plan"]["id"]]["status"] == "active"

    # 不存在的任务号静默跳过，已经取消的计划也不再动。
    await handle.on_schedule_deleted("schedule-does-not-exist")
    assert (await handle._get_plan(first["plan"]["id"]))["status"] == "cancelled"


@async_test
async def test_completing_a_plan_is_not_flipped_to_cancelled_by_the_delete_hook():
    schedule = FakeScheduleService()
    planner = FakePlanner(plan=plan_payload(requirements={"固源岩组": 4}))
    _factory, handle = await service(
        planner=planner, schedule=schedule, depot=FakeDepot({"固源岩组": 9})
    )
    schedule.bind_delete_hook(handle.on_schedule_deleted)
    started = await handle.start(name="甲", targets=[{"operator": "甲"}], run_times=["08:00"])

    refreshed = await handle.refresh(started["plan"]["id"])

    assert refreshed["status"] == "completed"
    assert schedule.deleted == [str(started["schedule_id"])]
    assert schedule.rows == []
    assert (await handle._get_plan(started["plan"]["id"]))["status"] == "completed"


@async_test
async def test_cancel_is_not_overwritten_by_the_delete_hook():
    schedule = FakeScheduleService()
    _factory, handle = await service(planner=FakePlanner(), schedule=schedule, depot=FakeDepot())
    schedule.bind_delete_hook(handle.on_schedule_deleted)
    started = await handle.start(name="甲", targets=[{"operator": "甲"}], run_times=["08:00"])

    cancelled = await handle.cancel(started["plan"]["id"])

    assert cancelled["status"] == "cancelled"
    assert schedule.deleted == [str(started["schedule_id"])]
    assert (await handle._get_plan(started["plan"]["id"]))["status"] == "cancelled"


@async_test
async def test_delete_hook_failure_does_not_break_cancellation():
    schedule = FakeScheduleService()

    async def boom(schedule_id: str) -> None:
        raise RuntimeError("钩子炸了")

    schedule.bind_delete_hook(boom)
    _factory, handle = await service(planner=FakePlanner(), schedule=schedule, depot=FakeDepot())
    started = await handle.start(name="甲", targets=[{"operator": "甲"}], run_times=["08:00"])

    # 钩子抛异常（真实 ScheduleService 会吞掉，这里的替身直接抛）也不能让取消失败。
    cancelled = await handle.cancel(started["plan"]["id"])

    assert cancelled["status"] == "cancelled"
    assert schedule.rows == []
