"""养成计划（规格 §7.7）：今天的流水线 + 每日定时任务 + 进度重算与自动停止。

一次确认覆盖两步：

1. **今天**：提交一条流水线 ``[Fight × k, Depot]``，三档用药全为 0，只用现有理智（Q39）；
2. **每日**：创建定时任务，模板 ``[StartUp, Fight × k, Depot]``，``expiring_medicine=999``
   （自然恢复 + 48 小时内过期的理智药，Q46），不加 ``CloseDown``（不替用户决定关游戏）。

进度在「计划流水线结束」与「读取计划」时重算：仓库数量满足全部需求就把计划标完成、
停用并删除定时任务、发 ``upgrade_plan_finished`` 通知（S15）。
"""

from __future__ import annotations

import asyncio
import logging
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Any, Callable, Mapping, Sequence

from maa_api.domain.errors import AppError, ErrorCode
from maa_api.settings import Settings, get_settings

__all__ = ["RESTORE_SANITY_PER_DAY", "UpgradePlanService", "default_upgrade_plan_service"]

logger = logging.getLogger(__name__)

#: `upgrade_plan` 里的 JSON 列（其余列按原类型写入）。
_JSON_FIELDS = frozenset({"targets", "requirements", "progress"})

#: 每天自然恢复的理智（规格 §7.6 的估算口径）。
RESTORE_SANITY_PER_DAY = 240

#: 计划流水线里 Fight 的默认上限（超过就拆成多条任务，避免一条任务刷几十次）。
MAX_FIGHT_RUNS = 20


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

    async def _conflicting_schedules(self) -> list[dict[str, Any]]:
        """已有的用理智定时任务（创建前提示「与现有任务争抢理智」）。"""
        if self._schedule_service is None:
            return []
        try:
            listing = await self._schedule_service.list()
        except Exception:  # noqa: BLE001 - 冲突检查只是提示
            return []
        rows = listing.get("schedules") if isinstance(listing, Mapping) else None
        conflicts: list[dict[str, Any]] = []
        for row in rows or []:
            template = row.get("template") if isinstance(row, Mapping) else None
            for task in template or []:
                if isinstance(task, Mapping) and task.get("name") == "Fight":
                    conflicts.append(
                        {"schedule_id": row.get("id"), "name": row.get("name")}
                    )
                    break
        return conflicts

    async def _submit_today(
        self, *, name: str, stages: Sequence[Mapping[str, Any]]
    ) -> dict[str, Any] | None:
        """今天的流水线：只用现有理智，不吃药不碎石。"""
        tasks: list[dict[str, Any]] = []
        for stage in stages:
            tasks.append(
                {
                    "name": "Fight",
                    "stage": stage.get("stage_code") or stage.get("stage_id"),
                    "medicine": 0,
                    "stone": 0,
                    "expiring_medicine": 0,
                    "times": int(stage.get("runs") or 0),
                }
            )
        tasks.append({"name": "Depot"})
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
        """每日定时任务：StartUp + Fight × k + Depot。"""
        from maa_api.services.schedule_service import ScheduleWrite

        tasks: list[dict[str, Any]] = [{"name": "StartUp"}]
        for stage in stages:
            tasks.append(
                {
                    "name": "Fight",
                    "stage": stage.get("stage_code") or stage.get("stage_id"),
                    "medicine": 0,
                    "stone": 0,
                    "expiring_medicine": 999,
                    "times": int(stage.get("runs") or 0),
                }
            )
        tasks.append({"name": "Depot"})
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
        """重算一个计划的进度；材料齐了就标完成、停用并删除定时任务、发通知。"""
        plan = await self._get_plan(plan_id)
        requirements = plan.get("requirements") or {}
        if not requirements:
            return plan
        depot = await self._depot_counts()
        missing = {
            name: max(int(need) - int(depot.get(str(name), 0)), 0)
            for name, need in requirements.items()
            if str(name) not in {"龙门币"}
        }
        missing = {name: count for name, count in missing.items() if count > 0}
        if missing:
            return await self._update_plan(plan_id, {"progress": {**plan.get("progress", {}), "missing": missing}})

        await self._finish(plan)
        return await self._update_plan(
            plan_id,
            {
                "status": "completed",
                "progress": {**plan.get("progress", {}), "missing": {}},
                "completed_at": _now(),
            },
        )

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

    async def _finish(self, plan: Mapping[str, Any]) -> None:
        """完成：停用并删除定时任务，发 `upgrade_plan_finished` 通知。"""
        schedule_id = plan.get("schedule_id")
        if schedule_id and self._schedule_service is not None:
            try:
                await self._schedule_service.delete(str(schedule_id))
            except Exception:  # noqa: BLE001 - 任务可能已被用户删掉
                logger.debug("养成计划的定时任务删除失败（可能已不存在）", exc_info=True)
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
        """停止计划并删除对应的定时任务。"""
        plan = await self._get_plan(plan_id)
        schedule_id = plan.get("schedule_id")
        if schedule_id and self._schedule_service is not None:
            try:
                await self._schedule_service.delete(str(schedule_id))
            except Exception:  # noqa: BLE001
                logger.debug("取消计划时删除定时任务失败", exc_info=True)
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
