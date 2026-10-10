"""养成计划（规格 §7.7）：今天的流水线 + 每日定时任务 + 进度重算与自动停止。

一次确认覆盖两步：

1. **今天**：提交一条流水线 ``[Fight × k, Depot]``，三档用药全为 0，只用现有理智（Q39）；
2. **每日**：创建定时任务，模板 ``[StartUp, Fight × k, Depot]``，``expiring_medicine=999``
   （自然恢复 + 48 小时内过期的理智药，Q46），不加 ``CloseDown``（不替用户决定关游戏）。

确认卡片先看 :meth:`UpgradePlanService.preview`：它把同一套任务拼装（``build_today_tasks``
/ ``build_daily_template``）跑一遍但不落库、不提交流水线、不建定时任务，所以卡片里
看到的就是将来真跑的。

进度在「计划流水线结束」与「读取计划」时重算：仓库数量满足全部需求就把计划标完成、
停用并删除定时任务、发 ``upgrade_plan_finished`` 通知（S15）；没齐就用剩余需求重跑规划
（``UpgradePlanner.plan_needs``），把新的关卡与场次写回**同一条**定时任务（用药口径不变、
不重复确认），关卡关闭后被剔除的代号记进 ``progress["closed_dropped"]``。

用户在定时任务页删掉任务时，:meth:`UpgradePlanService.on_schedule_deleted` 把对应计划
标为 ``cancelled``；该钩子经 :meth:`ScheduleService.bind_delete_hook` 注入。计划自己删
定时任务的两处（完成、取消）会在删除期间置抑制标志，避免钩子把刚完成的计划改回取消。
"""

from __future__ import annotations

import asyncio
import logging
from contextlib import contextmanager
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Any, Callable, Iterator, Mapping, Sequence

from maa_api.domain.errors import AppError, ErrorCode
from maa_api.settings import Settings, get_settings

__all__ = [
    "EXPIRING_MEDICINE",
    "MAX_FIGHT_RUNS",
    "RESTORE_SANITY_PER_DAY",
    "UpgradePlanService",
    "build_daily_template",
    "build_fight_tasks",
    "build_today_tasks",
    "default_upgrade_plan_service",
    "dropped_stage_codes",
    "rebuild_template_fights",
    "split_fight_runs",
]

logger = logging.getLogger(__name__)

#: `upgrade_plan` 里的 JSON 列（其余列按原类型写入）。
_JSON_FIELDS = frozenset({"targets", "requirements", "progress"})

#: 每天自然恢复的理智（规格 §7.6 的估算口径）。
RESTORE_SANITY_PER_DAY = 240

#: 计划流水线里 Fight 的默认上限（超过就拆成多条任务，避免一条任务刷几十次）。
MAX_FIGHT_RUNS = 20

#: 每日模板的用药口径：自然恢复 + 48 小时内过期的理智药（规格 §7.7 Q46）。
EXPIRING_MEDICINE = 999

#: 只报需求、不参与「刷关是否齐了」判定的材料（仓库识别不含这两项，规格 §7.3）。
_REPORT_ONLY_NAMES = frozenset({"龙门币"})

#: 重建 Fight 时被替换掉的字段（其余字段从原任务带过来）。
_TASK_SHAPE = frozenset({"name", "stage", "times"})


def split_fight_runs(runs: int, *, limit: int = MAX_FIGHT_RUNS) -> list[int]:
    """一条 Fight 的场次按上限切分：``30 → [20, 10]``；``0`` 或负数 → ``[]``。

    ``MAX_FIGHT_RUNS`` 不是死常量：单条 ``Fight`` 超过上限就拆成多条任务
    （规格 §7.7 的刷关清单口径），今天的流水线与每日模板用同一套切分。
    """
    try:
        total = int(runs or 0)
    except (TypeError, ValueError):
        return []
    if total <= 0:
        return []
    size = max(int(limit or 0), 1)
    return [min(size, total - start) for start in range(0, total, size)]


def _stage_code(stage: Mapping[str, Any]) -> str | None:
    """关卡代号优先，其次内部 id（与 ``FightInput.stage`` 的取值口径一致）。"""
    if not isinstance(stage, Mapping):
        return None
    code = stage.get("stage_code") or stage.get("stage_id")
    return str(code) if code else None


def build_fight_tasks(
    stages: Sequence[Mapping[str, Any]] | None,
    *,
    expiring_medicine: int,
    medicine: int = 0,
    stone: int = 0,
) -> list[dict[str, Any]]:
    """按关卡顺序拼 Fight 任务，每条不超过 ``MAX_FIGHT_RUNS`` 次。"""
    tasks: list[dict[str, Any]] = []
    for stage in stages or ():
        code = _stage_code(stage)
        try:
            runs = int(stage.get("runs") or 0) if isinstance(stage, Mapping) else 0
        except (TypeError, ValueError):
            runs = 0
        for count in split_fight_runs(runs):
            tasks.append(
                {
                    "name": "Fight",
                    "stage": code,
                    "medicine": medicine,
                    "stone": stone,
                    "expiring_medicine": expiring_medicine,
                    "times": count,
                }
            )
    return tasks


def build_today_tasks(stages: Sequence[Mapping[str, Any]] | None) -> list[dict[str, Any]]:
    """今天的流水线：``[Fight × k, Depot]``，三档用药全 0（只用现有理智）。"""
    return [*build_fight_tasks(stages, expiring_medicine=0), {"name": "Depot"}]


def build_daily_template(stages: Sequence[Mapping[str, Any]] | None) -> list[dict[str, Any]]:
    """每日定时任务的模板：``[StartUp, Fight × k, Depot]``，``expiring_medicine=999``。"""
    return [
        {"name": "StartUp"},
        *build_fight_tasks(stages, expiring_medicine=EXPIRING_MEDICINE),
        {"name": "Depot"},
    ]


def rebuild_template_fights(
    template: Sequence[Mapping[str, Any]] | None,
    stages: Sequence[Mapping[str, Any]] | None,
) -> list[dict[str, Any]]:
    """只换掉模板里的 Fight 任务，其余任务（StartUp/Depot/用户加的）原样保留。

    新场次沿用模板里第一条 Fight 的用药字段（``medicine``/``stone``/
    ``expiring_medicine`` 与 ``series`` 等），并同样按 ``MAX_FIGHT_RUNS`` 切分
    ——规格 §7.7：更新场次时用药口径不变、不再重复确认。没有新场次时原样返回。
    """
    rows = [dict(task) for task in (template or ()) if isinstance(task, Mapping)]
    fights = build_fight_tasks(stages, expiring_medicine=0)
    if not fights:
        return rows
    indexes = [index for index, task in enumerate(rows) if task.get("name") == "Fight"]
    base: dict[str, Any] = {}
    if indexes:
        base = {key: value for key, value in rows[indexes[0]].items() if key not in _TASK_SHAPE}
        replaced = [
            {**base, "name": "Fight", "stage": task["stage"], "times": task["times"]}
            for task in fights
        ]
        return [*rows[: indexes[0]], *replaced, *rows[indexes[-1] + 1 :]]
    # 模板里没有 Fight（用户改过）：插在收尾的 Depot 之前，没有就追加。
    insert_at = len(rows) - 1 if rows and rows[-1].get("name") == "Depot" else len(rows)
    return [
        *rows[:insert_at],
        *[{**base, "name": "Fight", "stage": task["stage"], "times": task["times"]} for task in fights],
        *rows[insert_at:],
    ]


def dropped_stage_codes(
    previous: Sequence[Mapping[str, Any]] | None,
    stages: Sequence[Mapping[str, Any]] | None,
) -> list[str]:
    """上一次规划里有、这次没有了的关卡代号（关卡关闭后被剔除，规格 §7.7）。"""
    return sorted(
        {code for code in (_stage_code(row) for row in previous or ()) if code}
        - {code for code in (_stage_code(row) for row in stages or ()) if code}
    )


@dataclass(frozen=True, slots=True)
class _PlanContext:
    """一次计划启动需要的运行时对象（缺失时按降级处理）。"""

    pipeline_service: Any = None
    schedule_service: Any = None
    notify_service: Any = None
    box: Any = None
    depot: Any = None


class UpgradePlanService:
    """养成计划的领域服务；外部依赖全部可注入（测试与装配）。"""

    def __init__(
        self,
        session_factory: Callable[[], Any],
        *,
        planner: Any = None,
        pipeline_service: Any = None,
        schedule_service: Any = None,
        notify_service: Any = None,
        box: Any = None,
        depot: Any = None,
        settings_provider: Callable[[], Settings] = get_settings,
    ) -> None:
        self._session_factory = session_factory
        self._planner = planner
        self._pipeline_service = pipeline_service
        self._schedule_service = schedule_service
        self._notify_service = notify_service
        self._box = box
        self._depot = depot
        self._settings_provider = settings_provider
        #: >0 时忽略定时任务删除钩子（计划自己删任务期间，见 `_suppress_schedule_delete`）。
        self._suppress_delete = 0

    # -- 依赖 -------------------------------------------------------------
    @property
    def planner(self) -> Any:
        if self._planner is not None:
            return self._planner
        from maa_api.services.account.planner import default_upgrade_planner

        return default_upgrade_planner()

    @property
    def box(self) -> Any:
        if self._box is not None:
            return self._box
        from maa_api.services.account.box import OperatorBoxService

        return OperatorBoxService(self._session_factory)

    @property
    def depot(self) -> Any:
        if self._depot is not None:
            return self._depot
        from maa_api.services.account.depot import DepotService

        return DepotService(self._session_factory)

    def bind_schedule_service(self, schedule_service: Any) -> None:
        """后置注入定时任务服务（照 ``PipelineRunner.bind_finish_hook`` 的口径）。

        装配顺序是「计划服务 → 确认服务 → 定时任务服务」：``main.py`` 里
        :class:`~maa_api.services.schedule_service.ScheduleService` 是在计划服务
        **之后**才构造的，构造时传进去的 ``schedule_service`` 恒为 ``None``
        （M27 起就存在的装配缺陷：真实启动时每日定时任务不会建、冲突检查恒空）。
        所以在定时任务服务建好之后用它补一次绑定，而不是把整个账号服务块往前挪。

        与 ``ScheduleService.bind_delete_hook`` 配对使用：删除钩子由装配方接成
        :meth:`on_schedule_deleted`。
        """
        self._schedule_service = schedule_service

    # -- 启动 -------------------------------------------------------------
    async def start(
        self,
        *,
        name: str,
        targets: Sequence[Mapping[str, Any]],
        run_times: Sequence[str],
        context: Any = None,
    ) -> dict[str, Any]:
        """一次确认后的两步动作；返回计划、今天的流水线与定时任务。"""
        times = [str(item).strip() for item in run_times if str(item).strip()]
        if not times:
            raise AppError(ErrorCode.INVALID_PARAMETER, "run_times 不能为空", {"run_times": list(run_times)})
        plan = await self.planner.plan(targets, use_depot=True, box=self.box)
        plan_dict = plan.as_dict()
        stages = [row for row in plan_dict.get("stages") or [] if int(row.get("runs") or 0) > 0]

        conflicts = await self._conflicting_schedules()
        schedule_id = None
        if stages and self._schedule_service is not None:
            schedule_id = await self._create_schedule(name=name, stages=stages, run_times=times)

        today = None
        if stages and self._pipeline_service is not None:
            today = await self._submit_today(name=name, stages=stages)

        row = await self._create_plan_row(
            name=name,
            targets=list(targets),
            requirements=plan_dict.get("requirements") or {},
            progress={"stages": stages, "total_sanity": plan_dict.get("total_sanity")},
            schedule_id=schedule_id,
            session_id=getattr(context, "session_id", None) if context is not None else None,
        )
        return {
            "plan": row,
            "today": today,
            "schedule_id": schedule_id,
            "run_times": times,
            "total_sanity": plan_dict.get("total_sanity"),
            "days_estimated": plan_dict.get("days_estimated"),
            "conflicts": conflicts,
            "notes": list(plan_dict.get("notes") or []),
            "medicine": {"today": 0, "daily_expiring_medicine": 999, "stone": 0},
        }

    # -- 预览（确认卡片）--------------------------------------------------
    async def preview(
        self,
        *,
        name: str,
        targets: Sequence[Mapping[str, Any]],
        run_times: Sequence[str],
        context: Any = None,
    ) -> dict[str, Any]:
        """确认卡片用的一次性预览：只算，不落库、不提交流水线、不建定时任务。

        ``today_tasks`` / ``daily_template`` 与 :meth:`start` 走同一套拼装纯函数
        （``build_today_tasks`` / ``build_daily_template``），所以卡片里看到的就是
        将来真跑的（单条 Fight 同样按 ``MAX_FIGHT_RUNS`` 切分）。规划失败或不可解也
        照常返回，原因写进 ``notes`` / ``unobtainable``，不把异常抛给卡片。
        ``context`` 只为与 :meth:`start` 同形（预览不需要会话）。
        """
        times = [str(item).strip() for item in run_times if str(item).strip()]
        notes: list[str] = ["预览：没有提交流水线，也没有创建定时任务"]
        if not times:
            notes.append("run_times 为空：start 会拒绝这次启动（先让用户确认运行时刻）")
        else:
            try:
                _cron_from_times(times)
            except AppError as exc:
                notes.append(f"运行时刻不可用（{exc}）：start 会拒绝这次启动")

        plan_dict: dict[str, Any] = {}
        try:
            plan = await self.planner.plan(targets, use_depot=True, box=self.box)
            plan_dict = plan.as_dict()
        except Exception as exc:  # noqa: BLE001 - 预览失败只降级成说明
            logger.warning("养成计划预览的规划失败：%s", exc)
            notes.append(f"规划失败（{type(exc).__name__}）：{exc}")

        stages = [
            row for row in plan_dict.get("stages") or [] if int(row.get("runs") or 0) > 0
        ]
        notes.extend(str(item) for item in plan_dict.get("notes") or ())
        if not stages:
            notes.append("这次没有需要刷的关卡：材料可能已备齐，或掉率数据里没有可用关卡")

        return {
            "today_tasks": build_today_tasks(stages),
            "daily_template": build_daily_template(stages),
            "medicine": {"today": 0, "stone": 0, "expiring_medicine": EXPIRING_MEDICINE},
            "days_estimated": float(plan_dict.get("days_estimated") or 0.0),
            "total_sanity": int(plan_dict.get("total_sanity") or 0),
            "stages": stages,
            "conflicts": await self._conflicting_schedules(),
            "unobtainable": list(plan_dict.get("unobtainable") or []),
            "notes": list(dict.fromkeys(notes)),
        }

    async def _conflicting_schedules(self) -> list[dict[str, Any]]:
        """已有的用理智定时任务（创建前提示「与现有任务争抢理智」）。"""
        if self._schedule_service is None:
            return []
        try:
            listing = await self._schedule_service.list()
        except Exception:  # noqa: BLE001 - 冲突检查只是提示
            return []
        conflicts: list[dict[str, Any]] = []
        for row in _page_items(listing):
            template = _row_field(row, "template")
            for task in template or []:
                if isinstance(task, Mapping) and task.get("name") == "Fight":
                    conflicts.append(
                        {
                            "schedule_id": _row_field(row, "id"),
                            "name": _row_field(row, "name"),
                        }
                    )
                    break
        return conflicts

    async def _submit_today(
        self, *, name: str, stages: Sequence[Mapping[str, Any]]
    ) -> dict[str, Any] | None:
        """今天的流水线：只用现有理智，不吃药不碎石（单条 Fight 按上限切分）。"""
        tasks = build_today_tasks(stages)
        try:
            created = await self._pipeline_service.submit(
                {"tasks": tasks, "title": f"养成计划：{name}（今天）"}
            )
        except Exception as exc:  # noqa: BLE001 - 提交失败不该吞掉计划本身
            logger.warning("养成计划今天的流水线提交失败：%s", exc)
            return {"error": str(exc), "tasks": tasks}
        return {"pipeline_id": created.get("id") if isinstance(created, Mapping) else None, "tasks": tasks}

    async def _create_schedule(
        self, *, name: str, stages: Sequence[Mapping[str, Any]], run_times: Sequence[str]
    ) -> str | None:
        """每日定时任务：``StartUp + Fight × k + Depot``（按上限切分，不加 CloseDown）。"""
        from maa_api.services.schedule_service import ScheduleWrite

        tasks = build_daily_template(stages)
        cron = _cron_from_times(run_times)
        payload = ScheduleWrite(
            name=f"养成计划：{name}",
            cron=cron,
            template=tasks,  # type: ignore[arg-type] - TaskInput 判别联合按 name 收
        )
        created = await self._schedule_service.create(payload)
        return str(getattr(created, "id", None) or (created.get("id") if isinstance(created, Mapping) else ""))

    # -- 进度 -------------------------------------------------------------
    async def list_plans(self) -> dict[str, Any]:
        """列出全部计划，并顺带重算每个进行中计划的进度。"""
        plans = await self._load_plans()
        refreshed: list[dict[str, Any]] = []
        for plan in plans:
            row = plan
            if plan.get("status") == "active":
                row = await self.refresh(plan["id"])
            refreshed.append(row)
        return {"plans": refreshed, "total": len(refreshed)}

    async def refresh(self, plan_id: str) -> dict[str, Any]:
        """重算一个计划的进度；材料齐了就标完成、停用并删除定时任务、发通知。

        没齐时用剩余需求重跑规划（``UpgradePlanner.plan_needs``），把新的关卡与场次
        写回**同一条**定时任务（规格 §7.7）：用药口径不变，不再重复确认。
        """
        plan = await self._get_plan(plan_id)
        requirements = plan.get("requirements") or {}
        if not requirements:
            return plan
        depot = await self._depot_counts()
        missing = {
            name: max(int(need) - int(depot.get(str(name), 0)), 0)
            for name, need in requirements.items()
            if str(name) not in _REPORT_ONLY_NAMES
        }
        missing = {name: count for name, count in missing.items() if count > 0}
        progress = {**plan.get("progress", {}), "missing": missing}
        if missing:
            progress = await self._replan(plan, missing=missing, progress=progress)
            return await self._update_plan(plan_id, {"progress": progress})

        await self._finish(plan)
        return await self._update_plan(
            plan_id,
            {"status": "completed", "progress": progress, "completed_at": _now()},
        )

    async def _replan(
        self,
        plan: Mapping[str, Any],
        *,
        missing: Mapping[str, int],
        progress: dict[str, Any],
    ) -> dict[str, Any]:
        """用剩余需求重跑规划，把新场次写回同一条定时任务，并记录被剔除的关卡。

        重算失败（网络/数据/规划报错）不能让流水线收尾出错：记
        ``progress["replan_error"]`` 并保留旧 ``stages``。
        """
        previous = list(progress.get("stages") or [])
        try:
            plan_dict = (await self.planner.plan_needs(missing, display={})).as_dict()
        except Exception as exc:  # noqa: BLE001 - 钩子路径不能被重算拖垮
            logger.warning("养成计划 %s 的剩余需求重算失败", plan.get("id"), exc_info=True)
            progress["replan_error"] = f"{type(exc).__name__}：{exc}"
            return progress

        stages = [
            row for row in plan_dict.get("stages") or [] if int(row.get("runs") or 0) > 0
        ]
        progress.update(
            {
                "stages": stages,
                "total_sanity": int(plan_dict.get("total_sanity") or 0),
                "days_estimated": float(plan_dict.get("days_estimated") or 0.0),
                "replanned_at": _iso(_now()),
                # 与上一次 progress["stages"] 比，消失的关卡就是被关闭剔除的。
                "closed_dropped": dropped_stage_codes(previous, stages),
            }
        )
        progress.pop("replan_error", None)
        await self._update_schedule(plan, stages=stages, progress=progress)
        return progress

    async def _update_schedule(
        self,
        plan: Mapping[str, Any],
        *,
        stages: Sequence[Mapping[str, Any]],
        progress: dict[str, Any],
    ) -> None:
        """更新计划的定时任务：只换 Fight 的次数，其余字段与任务原样带回。

        计划没有 ``schedule_id``（当初没有可刷关卡）或任务已被删时跳过更新并记 note。
        ``ScheduleView`` 不回显 ``misfire_grace_seconds`` / ``catch_up``，``update``
        会把这两项落回 ``ScheduleWrite`` 的默认值——这是接口层的现存限制。
        """
        schedule_id = plan.get("schedule_id")
        if not schedule_id or self._schedule_service is None:
            progress["schedule_note"] = "计划没有定时任务：跳过每日任务的场次更新"
            return
        from maa_api.services.schedule_service import ScheduleWrite

        try:
            current = await self._schedule_service.get(str(schedule_id))
        except Exception as exc:  # noqa: BLE001 - 任务可能已被用户删掉
            logger.debug("养成计划的定时任务读不到：%s", exc, exc_info=True)
            progress["schedule_note"] = (
                f"定时任务已不存在或读不到（{type(exc).__name__}）：跳过每日任务的场次更新"
            )
            return

        template = list(_row_field(current, "template") or [])
        rebuilt = rebuild_template_fights(template, stages)
        if not rebuilt or rebuilt == template:
            return
        try:
            payload = ScheduleWrite(
                name=str(_row_field(current, "name") or f"养成计划：{plan.get('name')}"),
                cron=str(_row_field(current, "cron") or ""),
                timezone=str(_row_field(current, "timezone") or "Asia/Shanghai"),
                template=rebuilt,  # type: ignore[arg-type] - TaskInput 判别联合按 name 收
                priority=_int_or(_row_field(current, "priority"), 3),
                enabled=_bool_or(_row_field(current, "enabled"), True),
                skip_if_running=_bool_or(_row_field(current, "skip_if_running"), True),
            )
            await self._schedule_service.update(str(schedule_id), payload)
        except Exception as exc:  # noqa: BLE001 - 更新失败不能拖垮收尾
            logger.warning("养成计划的定时任务更新失败：%s", exc)
            progress["schedule_error"] = f"定时任务更新失败（{type(exc).__name__}）：{exc}"

    async def on_pipeline_finished(self, pipeline_id: str, status: Any = None) -> None:
        """计划流水线结束后的钩子：把在跑的计划重算一遍（规格 §7.7 的进度更新）。"""
        try:
            plans = await self._load_plans()
        except Exception:  # noqa: BLE001 - 钩子不能影响流水线收尾
            return
        for plan in plans:
            if plan.get("status") != "active":
                continue
            try:
                await self.refresh(plan["id"])
            except Exception:  # noqa: BLE001
                logger.warning("养成计划 %s 的进度重算失败", plan.get("id"), exc_info=True)

    async def on_schedule_deleted(self, schedule_id: str) -> None:
        """定时任务被删（用户在任务页删除）时，同步把对应计划标为已取消（规格 §7.7）。

        经 ``ScheduleService.bind_delete_hook`` 注入；计划自己删任务的两处（完成、
        取消）走 ``_suppress_schedule_delete``，此时这个钩子直接返回。
        """
        if getattr(self, "_suppress_delete", 0) > 0:
            return
        try:
            plans = await self._load_plans()
        except Exception:  # noqa: BLE001 - 钩子不能影响删除本身
            logger.warning("定时任务删除后读取养成计划失败", exc_info=True)
            return
        for plan in plans:
            if plan.get("status") != "active":
                continue
            if str(plan.get("schedule_id") or "") != str(schedule_id):
                continue
            try:
                await self._update_plan(plan["id"], {"status": "cancelled"})
            except Exception:  # noqa: BLE001
                logger.warning("养成计划 %s 随定时任务删除取消失败", plan.get("id"), exc_info=True)
            return

    @contextmanager
    def _suppress_schedule_delete(self) -> Iterator[None]:
        """计划自己删定时任务期间抑制删除钩子，避免把刚完成的计划改回 ``cancelled``。

        ``ScheduleService.delete`` 是**内联** await 钩子的，所以计数在整个删除过程中
        一直有效；钩子里如果自己再起后台任务，这个抑制就不保证了（见 ``bind_delete_hook``）。
        """
        self._suppress_delete += 1
        try:
            yield
        finally:
            self._suppress_delete -= 1

    async def _delete_schedule(self, plan: Mapping[str, Any]) -> None:
        """删除计划的定时任务；期间抑制删除钩子（完成与取消共用）。"""
        schedule_id = plan.get("schedule_id")
        if not schedule_id or self._schedule_service is None:
            return
        with self._suppress_schedule_delete():
            try:
                await self._schedule_service.delete(str(schedule_id))
            except Exception:  # noqa: BLE001 - 任务可能已被用户删掉
                logger.debug("养成计划的定时任务删除失败（可能已不存在）", exc_info=True)

    async def _finish(self, plan: Mapping[str, Any]) -> None:
        """完成：停用并删除定时任务，发 `upgrade_plan_finished` 通知。"""
        await self._delete_schedule(plan)
        if self._notify_service is not None:
            try:
                from maa_api.domain.enums import NotifyEvent

                await self._notify_service.notify(
                    NotifyEvent.UPGRADE_PLAN_FINISHED,
                    {
                        "title": f"养成计划完成：{plan.get('name')}",
                        "message": "材料已备齐，定时任务已停止；升级操作请在游戏里完成。",
                        "plan_id": plan.get("id"),
                    },
                )
            except Exception:  # noqa: BLE001 - 通知失败不影响计划状态
                logger.warning("养成计划完成通知发送失败", exc_info=True)

    # -- 取消 -------------------------------------------------------------
    async def cancel(self, plan_id: str) -> dict[str, Any]:
        """停止计划并删除对应的定时任务（删除期间抑制钩子，状态由这里写）。"""
        plan = await self._get_plan(plan_id)
        await self._delete_schedule(plan)
        return await self._update_plan(plan_id, {"status": "cancelled"})

    # -- 持久化（薄封装，便于测试替换）-------------------------------------
    async def _create_plan_row(
        self,
        *,
        name: str,
        targets: list[Mapping[str, Any]],
        requirements: Mapping[str, Any],
        progress: Mapping[str, Any],
        schedule_id: str | None,
        session_id: str | None,
    ) -> dict[str, Any]:
        from maa_api.db.models import UpgradePlan, utcnow

        async with self._session_factory() as session:
            row = UpgradePlan(
                name=name,
                status="active",
                targets=[_jsonable(item) for item in targets],
                requirements=dict(requirements),
                progress=dict(progress),
                schedule_id=schedule_id,
                session_id=session_id,
                created_at=utcnow(),
                updated_at=utcnow(),
            )
            session.add(row)
            await session.commit()
            await session.refresh(row)
            return _plan_wire(row)

    async def _load_plans(self) -> list[dict[str, Any]]:
        from sqlmodel import select

        from maa_api.db.models import UpgradePlan

        async with self._session_factory() as session:
            rows = (await session.execute(select(UpgradePlan).order_by(UpgradePlan.created_at))).scalars().all()
        return [_plan_wire(row) for row in rows]

    async def _get_plan(self, plan_id: str) -> dict[str, Any]:
        for plan in await self._load_plans():
            if plan["id"] == plan_id:
                return plan
        raise AppError(
            ErrorCode.UPGRADE_PLAN_NOT_FOUND,
            f"养成计划不存在：{plan_id}",
            {"plan_id": plan_id},
        )

    async def _update_plan(self, plan_id: str, changes: Mapping[str, Any]) -> dict[str, Any]:
        from maa_api.db.models import UpgradePlan, utcnow

        async with self._session_factory() as session:
            row = await session.get(UpgradePlan, plan_id)
            if row is None:
                raise AppError(
                    ErrorCode.UPGRADE_PLAN_NOT_FOUND,
                    f"养成计划不存在：{plan_id}",
                    {"plan_id": plan_id},
                )
            for key, value in changes.items():
                # 只有 JSON 列需要转成可序列化结构；时间列要保持 datetime，否则
                # SQLite 的 DateTime 绑定会报 StatementError。
                setattr(row, key, _jsonable(value) if key in _JSON_FIELDS else value)
            row.updated_at = utcnow()
            session.add(row)
            await session.commit()
            await session.refresh(row)
            return _plan_wire(row)

    async def _depot_counts(self) -> dict[str, int]:
        """仓库数量：``item_id`` 与名字都建索引，需求里写哪个都能对上。"""
        counts: dict[str, int] = {}
        try:
            listing = await self.depot.list_items(limit=1000, offset=0)
        except Exception:  # noqa: BLE001 - 读不到仓库就按「都没备齐」算
            return counts
        for item in listing.get("items") or []:
            if not isinstance(item, Mapping):
                continue
            count = int(item.get("count") or 0)
            if item.get("item_id"):
                counts[str(item["item_id"])] = count
            if item.get("name"):
                counts[str(item["name"])] = count
        return counts


def _cron_from_times(run_times: Sequence[str]) -> str:
    """`["08:00", "20:00"]` → `0 8,20 * * *`；只认 `HH:MM`。"""
    hours: list[int] = []
    minutes: set[int] = set()
    for item in run_times:
        text = str(item).strip()
        parts = text.split(":")
        if len(parts) != 2 or not all(part.isdigit() for part in parts):
            raise AppError(
                ErrorCode.INVALID_PARAMETER,
                f"运行时刻必须是 HH:MM，收到 {text}",
                {"run_times": list(run_times)},
            )
        hour, minute = int(parts[0]), int(parts[1])
        if not 0 <= hour <= 23 or not 0 <= minute <= 59:
            raise AppError(
                ErrorCode.INVALID_PARAMETER,
                f"运行时刻超出范围：{text}",
                {"run_times": list(run_times)},
            )
        hours.append(hour)
        minutes.add(minute)
    minutes_text = ",".join(str(value) for value in sorted(minutes))
    hours_text = ",".join(str(value) for value in sorted(set(hours)))
    return f"{minutes_text} {hours_text} * * *"


def _page_items(listing: Any) -> list[Any]:
    """列表页的行：兼容 ``SchedulePage``（pydantic）与测试替身的 dict 形状。"""
    if isinstance(listing, Mapping):
        rows = listing.get("schedules")
        if rows is None:
            rows = listing.get("items")
        return [row for row in rows or []]
    return [row for row in getattr(listing, "items", None) or []]


def _row_field(row: Any, key: str, default: Any = None) -> Any:
    """行字段：dict 与 pydantic 视图（``ScheduleView``）都能读。"""
    if isinstance(row, Mapping):
        return row.get(key, default)
    return getattr(row, key, default)


def _int_or(value: Any, default: int) -> int:
    try:
        return int(value)
    except (TypeError, ValueError):
        return default


def _bool_or(value: Any, default: bool) -> bool:
    if value is None:
        return default
    return bool(value)


def _plan_wire(row: Any) -> dict[str, Any]:
    """计划行 → 工具返回的形状。"""
    return {
        "id": str(getattr(row, "id", "")),
        "name": getattr(row, "name", None),
        "status": str(getattr(row, "status", "")),
        "targets": list(getattr(row, "targets", None) or []),
        "requirements": dict(getattr(row, "requirements", None) or {}),
        "progress": dict(getattr(row, "progress", None) or {}),
        "schedule_id": getattr(row, "schedule_id", None),
        "session_id": getattr(row, "session_id", None),
        "created_at": _iso(getattr(row, "created_at", None)),
        "updated_at": _iso(getattr(row, "updated_at", None)),
        "completed_at": _iso(getattr(row, "completed_at", None)),
    }


def _jsonable(value: Any) -> Any:
    if isinstance(value, Mapping):
        return {str(key): _jsonable(item) for key, item in value.items()}
    if isinstance(value, (list, tuple)):
        return [_jsonable(item) for item in value]
    if isinstance(value, datetime):
        return value.isoformat()
    return value


def _iso(value: Any) -> str | None:
    if value is None:
        return None
    if isinstance(value, datetime):
        moment = value if value.tzinfo else value.replace(tzinfo=timezone.utc)
        return moment.isoformat()
    return str(value)


def _now() -> datetime:
    return datetime.now(timezone.utc).replace(tzinfo=None)


#: 进程内共享的默认门面（装配时用 `app.state` 上的实例覆盖）。
_DEFAULT: UpgradePlanService | None = None


def default_upgrade_plan_service() -> UpgradePlanService:
    global _DEFAULT
    if _DEFAULT is None:
        from maa_api.db import session as db_session

        _DEFAULT = UpgradePlanService(db_session.session_factory)
    return _DEFAULT


#: 供钩子复用：把协程安全地丢到后台（流水线收尾路径不该被计划的耗时拖住）。
def schedule_background(coro: Any) -> None:
    try:
        loop = asyncio.get_running_loop()
    except RuntimeError:  # pragma: no cover - 没有事件循环时只能放弃
        return
    loop.create_task(coro)
