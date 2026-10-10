#!/usr/bin/env python3
"""M27 账号数据与养成计划的端到端冒烟（M31 补齐进度重算与删除钩子，M33 补齐 CALLBACK
接线，末行 `SMOKE OK`）。

用**假内核回调**与假外部来源跑通整条链路，不打真机、不打网：

1. 识别：`OperBoxInfo`（`done=false` 忽略、`done=true` 落库）与 `DepotInfo`（整表替换）；
2. 页面数据：`OperatorBoxService` / `DepotService` 读回干员表、仓库与识别时间；
3. 规划：`plan_upgrade`（线性规划）给出关卡、次数与理智，随后 `start_upgrade_plan`
   的确认后动作——今天的流水线 + 每日定时任务；
4. 进度重算（规格 §7.7）：材料不齐时按剩余需求重跑规划，更新**同一条**定时任务的 Fight 次数；
5. 模拟完成：仓库备齐 → 计划标完成、定时任务被删除、发 `upgrade_plan_finished` 通知；
6. 计划列表与取消；
7. 删除定时任务（规格 §7.7）：用户在任务页删掉每日任务 → 计划同步标为已取消；
8. 识别覆盖手动值的差异；
9. 接线（M33 §4.1）：注册好的 CALLBACK 处理器收到**真实事件**后异步落库
   （不是直接调 `handle_callback`），识别记录带上流水线上下文与渠道。

跑法::

    .venv/bin/python scripts/account_smoke.py
"""

from __future__ import annotations

import asyncio
import sys
import tempfile
import traceback
from pathlib import Path
from types import SimpleNamespace
from typing import Any

REPO_ROOT = Path(__file__).resolve().parents[1]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

import maa_api.db.models  # noqa: F401,E402 - 建表前先导入模型
from maa_api.db.models import (  # noqa: E402
    AccountDepotItem,
    AccountOperator,
    AccountOperatorDiff,
    AccountRecognition,
    UpgradePlan,
)
from maa_api.core.enums import Message  # noqa: E402
from maa_api.db.repositories.account import (  # noqa: E402
    RECOGNITION_KIND_OPERBOX,
    AccountRecognitionRepository,
)
from maa_api.db.session import make_engine  # noqa: E402
from maa_api.services.account.box import OperatorBoxService  # noqa: E402
from maa_api.services.account.callback_wiring import install_account_capture  # noqa: E402
from maa_api.services.account.capture import AccountCaptureService  # noqa: E402
from maa_api.services.account.depot import DepotService  # noqa: E402
from maa_api.services.account.plans import UpgradePlanService  # noqa: E402
from sqlalchemy.ext.asyncio import async_sessionmaker  # noqa: E402
from sqlalchemy.pool import NullPool  # noqa: E402
from sqlalchemy import event  # noqa: E402
from sqlmodel import SQLModel  # noqa: E402


def _disable_foreign_keys(connection: Any, _record: Any) -> None:
    """临时库关掉外键（假流水线/假定时任务的 id 不在库里）。"""
    cursor = connection.cursor()
    cursor.execute("PRAGMA foreign_keys=OFF")
    cursor.close()


class SmokeFailure(RuntimeError):
    pass


def require(condition: Any, message: str) -> None:
    if not condition:
        raise SmokeFailure(message)


# ---------------------------------------------------------------------------
# 假内核回调
# ---------------------------------------------------------------------------
def operbox_callback(*, done: bool, own: list[dict[str, Any]]) -> dict[str, Any]:
    """`SubTaskExtraInfo` 的 OperBoxInfo 形状（字段取自上游 `OperBoxRecognitionTask`）。"""
    return {
        "msg": int(Message.SubTaskExtraInfo),
        "what": "OperBoxInfo",
        "details": {
            "done": done,
            "all_opers": [
                {"id": "char_172_svrash", "name": "银灰", "rarity": 6, "own": True},
                {"id": "char_002_amiya", "name": "阿米娅", "rarity": 5, "own": True},
                {"id": "char_124_kroos", "name": "克洛丝", "rarity": 3, "own": False},
            ],
            "own_opers": own,
        },
    }


def depot_callback(*, done: bool, data: str | None = None) -> dict[str, Any]:
    """`SubTaskExtraInfo` 的 DepotInfo 形状（`data` 是 JSON 字符串）。"""
    return {
        "msg": int(Message.SubTaskExtraInfo),
        "what": "DepotInfo",
        "details": {
            "done": done,
            "data": data if data is not None else '{"30012": 42, "30013": 7, "龙门币": 120000}',
        },
    }


# ---------------------------------------------------------------------------
# 假外部依赖
# ---------------------------------------------------------------------------
class FakePipelineService:
    """记录提交的流水线，返回一个假 id。"""

    def __init__(self) -> None:
        self.submissions: list[dict[str, Any]] = []

    async def submit(self, payload: dict[str, Any]) -> dict[str, Any]:
        self.submissions.append(payload)
        return {"id": f"pipeline-{len(self.submissions)}"}


class FakeScheduleService:
    """内存里的定时任务表（形状贴近真实 `ScheduleService`，含删除钩子）。"""

    def __init__(self) -> None:
        self.rows: list[dict[str, Any]] = []
        self.updated: list[str] = []
        self.deleted: list[str] = []
        self._hook: Any = None

    def bind_delete_hook(self, hook: Any) -> None:
        self._hook = hook

    def row(self, schedule_id: str) -> dict[str, Any]:
        for row in self.rows:
            if row["id"] == schedule_id:
                return row
        raise SmokeFailure(f"定时任务不存在：{schedule_id}")

    async def list(self) -> Any:
        return SimpleNamespace(
            items=[SimpleNamespace(**row) for row in self.rows], total=len(self.rows)
        )

    async def get(self, schedule_id: str) -> Any:
        return SimpleNamespace(**self.row(schedule_id))

    async def create(self, payload: Any) -> Any:
        row = {
            "id": f"schedule-{len(self.rows) + 1}",
            "name": getattr(payload, "name", ""),
            "cron": getattr(payload, "cron", ""),
            "timezone": getattr(payload, "timezone", "Asia/Shanghai"),
            "template": [
                item.model_dump(exclude_unset=True)
                if hasattr(item, "model_dump")
                else dict(item)
                for item in getattr(payload, "template", [])
            ],
            "priority": getattr(payload, "priority", 2),
            "enabled": getattr(payload, "enabled", True),
            "skip_if_running": getattr(payload, "skip_if_running", True),
        }
        self.rows.append(row)
        return SimpleNamespace(**row)

    async def update(self, schedule_id: str, payload: Any) -> Any:
        row = self.row(schedule_id)
        row.update(payload.model_dump(exclude_unset=True))
        self.updated.append(schedule_id)
        return SimpleNamespace(**row)

    async def delete(self, schedule_id: str) -> None:
        row = self.row(schedule_id)
        self.rows = [item for item in self.rows if item["id"] != schedule_id]
        self.deleted.append(schedule_id)
        if self._hook is not None:
            await self._hook(schedule_id)


class FakeNotifyService:
    def __init__(self) -> None:
        self.events: list[tuple[str, dict[str, Any]]] = []

    async def notify(self, event: Any, payload: dict[str, Any]) -> None:
        self.events.append((str(event), payload))


class FakeCoreClient:
    """`MaaCoreClient` 的事件注册面：只留 `on()` 与一个手动 `emit()`。"""

    def __init__(self) -> None:
        self.handlers: dict[str, list[Any]] = {}
        self.consumed: list[dict[str, Any]] = []

    def on(self, event_type: str, handler: Any) -> None:
        self.handlers.setdefault(event_type, []).append(handler)

    def emit(self, payload: dict[str, Any]) -> None:
        self.consumed.append(payload)
        for handler in list(self.handlers.get("CALLBACK", ())):
            handler(payload)


class _FakePlan:
    """与 `UpgradePlan.as_dict()` 同形的最小方案。"""

    def __init__(self, *, runs: int, requirements: dict[str, int]) -> None:
        self._runs = runs
        self._requirements = requirements

    def as_dict(self) -> dict[str, Any]:
        sanity = self._runs * 6
        return {
            "targets": [{"operator": "银灰", "assumed_current": True}],
            "requirements": dict(self._requirements),
            "stages": [
                {
                    "stage_id": "main_01-07",
                    "stage_code": "1-7",
                    "runs": self._runs,
                    "sanity_per_run": 6,
                    "sanity": sanity,
                }
            ],
            "synthesis": [],
            "total_sanity": sanity,
            "days_estimated": round(sanity / 240, 2),
            "surplus": {},
            "unobtainable": [],
            "notes": ["冒烟用的假方案"],
        }


class FakePlanner:
    """规划器替身：给固定方案，避免冒烟依赖真掉率。

    `plan()` 是启动/预览用的一次性方案（1-7 刷 3 次）；`plan_needs()` 是流水线结束
    后的剩余需求重算，故意给不同场次（7 次），好让冒烟看到每日任务被真的改掉。
    """

    def __init__(self) -> None:
        self.calls: list[Any] = []
        self.needs_calls: list[dict[str, int]] = []

    async def plan(self, targets: Any, *, use_depot: bool = True, server: Any = None, box: Any = None):
        self.calls.append(targets)
        # 有意留一个仓库里没有的材料，让 refresh 走「不齐 → 重算」这条分支。
        return _FakePlan(runs=3, requirements={"龙门币": 180000, "固源岩组": 4, "扭转醇": 5})

    async def plan_needs(self, needs: Any, *, server: Any = None, display: Any = None) -> _FakePlan:
        self.needs_calls.append(dict(needs))
        return _FakePlan(runs=7, requirements={})


# ---------------------------------------------------------------------------
# 主流程
# ---------------------------------------------------------------------------
async def run() -> None:
    with tempfile.TemporaryDirectory() as tmp:
        engine = make_engine(
            f"sqlite+aiosqlite:///{Path(tmp) / 'smoke.db'}", poolclass=NullPool
        )
        # 冒烟的定时任务与流水线是内存替身（id 不在库里），外键约束会把计划写入挡下来；
        # 这里只关掉这一个临时库的外键检查，不影响生产库的 PRAGMA。
        event.listen(engine.sync_engine, "connect", _disable_foreign_keys)
        try:
            async with engine.begin() as connection:
                await connection.run_sync(SQLModel.metadata.create_all)
            factory = async_sessionmaker(engine, expire_on_commit=False)

            box = OperatorBoxService(factory)
            depot = DepotService(factory)
            capture = AccountCaptureService(
                factory, box_service=box, depot_service=depot
            )

            # 1. 识别：done=false 忽略，done=true 落库
            progress = await capture.handle_callback(
                operbox_callback(done=False, own=[]), message=Message.SubTaskExtraInfo
            )
            require(progress.kind == "operbox", f"未识别为 operbox：{progress}")
            require(
                await _count(factory, AccountOperator) == 0,
                "done=false 的识别不该落库",
            )
            finished = await capture.handle_callback(
                operbox_callback(
                    done=True,
                    own=[
                        {
                            "id": "char_172_svrash",
                            "name": "银灰",
                            "own": True,
                            "elite": 1,
                            "level": 60,
                            "potential": 2,
                            "rarity": 6,
                        }
                    ],
                ),
                message=Message.SubTaskExtraInfo,
            )
            require(finished.written, f"done=true 没有落库：{finished}")
            require(await _count(factory, AccountOperator) == 3, "all_opers 应落三行")
            saved = await box.get_operator("char_172_svrash")
            require(saved["elite"] == 1 and saved["level"] == 60, f"识别值不对：{saved}")
            require(saved["profession"] == "近卫", f"职业没补上：{saved}")
            print(f"[1] 识别落库：干员 {await _count(factory, AccountOperator)} 行，"
                  f"银灰 精一 {saved['level']} 级、潜能 {saved['potential']}、职业 {saved['profession']}")

            depot_result = await capture.handle_callback(
                depot_callback(done=True), message=Message.SubTaskExtraInfo
            )
            require(depot_result.item_count >= 3, f"仓库没落库：{depot_result}")
            print(f"[1] 仓库整表替换：{depot_result.item_count} 种物品")

            # 2. 页面数据
            summary = await box.summary()
            depot_summary = await depot.summary()
            require(summary["total"] >= 1, f"干员概要不对：{summary}")
            require(summary["recognized_at"], f"缺识别时间：{summary}")
            require(depot_summary["total"] >= 3, f"仓库概要不对：{depot_summary}")
            listing = await box.list_operators(query="银灰")
            require(len(listing["items"]) == 1, f"列表返回不对：{listing}")
            print(f"[2] 页面数据：干员 {summary['total']} 名（精二 {summary['elite_two']}）"
                  f"｜仓库 {depot_summary['total']} 种｜识别时间 {summary['recognized_at']}")

            # 3. 规划
            planner = FakePlanner()
            from maa_api.services.account.planner import UpgradeTarget

            plan_service = UpgradePlanService(
                factory, planner=planner, box=box, depot=depot
            )
            pipeline = FakePipelineService()
            schedules = FakeScheduleService()
            plans = UpgradePlanService(
                factory,
                planner=planner,
                box=box,
                depot=depot,
                pipeline_service=pipeline,
                schedule_service=schedules,
            )
            started = await plans.start(
                name="银灰精二",
                targets=[{"operator": "银灰", "elite": 2}],
                run_times=["08:00", "20:00"],
            )
            require(started["plan"]["status"] == "active", f"计划状态不对：{started}")
            require(pipeline.submissions, "没有提交今天的流水线")
            tasks = pipeline.submissions[0]["tasks"]
            fight = [task for task in tasks if task["name"] == "Fight"]
            require(fight and fight[0]["medicine"] == 0 and fight[0]["stone"] == 0,
                    f"今天的流水线不该用药碎石：{tasks}")
            require(tasks[-1]["name"] == "Depot", f"今天应带 Depot 收尾：{tasks}")
            require(schedules.rows, "没有创建每日定时任务")
            daily = schedules.rows[0]["template"]
            require(daily[0]["name"] == "StartUp", f"每日模板应以 StartUp 开头：{daily}")
            require(
                any(task["name"] == "Fight" and task.get("expiring_medicine") == 999 for task in daily),
                f"每日模板应吃 48 小时内过期的理智药：{daily}",
            )
            require(
                not any(task["name"] == "CloseDown" for task in daily),
                "每日模板不该替用户关游戏",
            )
            print(f"[3] 养成计划启动：今天的流水线 {[task['name'] for task in tasks]}"
                  f"｜每日 {schedules.rows[0]['cron']}｜总理智 {started['total_sanity']}")

            # 4. 进度重算：不齐 → 用剩余需求重跑规划 → 更新同一条定时任务的 Fight 次数
            notify = FakeNotifyService()
            plans._notify_service = notify  # 冒烟里直接换掉通知替身
            replanned = await plans.refresh(started["plan"]["id"])
            require(replanned["status"] == "active", f"材料没齐不该完成：{replanned}")
            require(
                planner.needs_calls == [{"扭转醇": 5}],
                f"剩余需求应跳过已备齐的材料与龙门币：{planner.needs_calls}",
            )
            require(schedules.updated == ["schedule-1"], f"没有更新同一条定时任务：{schedules.updated}")
            require(
                [task["times"] for task in schedules.rows[0]["template"] if task["name"] == "Fight"]
                == [7],
                f"每日任务的 Fight 次数没按剩余需求重算：{schedules.rows[0]['template']}",
            )
            require(
                schedules.rows[0]["name"] == "养成计划：银灰精二"
                and schedules.rows[0]["cron"] == "0 8,20 * * *",
                f"更新不该改任务名与时刻：{schedules.rows[0]}",
            )
            require(
                replanned["progress"]["replanned_at"]
                and replanned["progress"]["missing"] == {"扭转醇": 5}
                and replanned["progress"]["closed_dropped"] == [],
                f"进度字段不对：{replanned['progress']}",
            )
            print(f"[4] 进度重算：剩余 {replanned['progress']['missing']}｜每日任务 Fight "
                  f"{[task['times'] for task in schedules.rows[0]['template'] if task['name'] == 'Fight']} 次"
                  f"｜重算时间 {replanned['progress']['replanned_at']}")

            # 5. 模拟完成：仓库备齐 → 重算 → 完成 + 删除定时任务 + 通知
            await _fill_depot(factory, {"龙门币": 200000, "固源岩组": 9, "扭转醇": 5})
            refreshed = await plans.refresh(started["plan"]["id"])
            require(refreshed["status"] == "completed", f"计划没有完成：{refreshed}")
            require(not schedules.rows, "完成后应删除定时任务")
            require(len(notify.events) == 1, f"完成应发一条通知：{notify.events}")
            require(
                notify.events[0][1]["plan_id"] == started["plan"]["id"],
                f"通知应带计划 id：{notify.events}",
            )
            print(f"[5] 模拟完成：计划 {refreshed['status']}｜定时任务 {len(schedules.rows)} 条"
                  f"｜通知 {notify.events[0][0]}")

            # 6. 计划列表与取消
            listed = await plans.list_plans()
            require(listed["total"] == 1, f"计划列表不对：{listed}")
            second = await plans.start(
                name="阿米娅精一",
                targets=[{"operator": "阿米娅", "elite": 1}],
                run_times=["09:30"],
            )
            cancelled = await plans.cancel(second["plan"]["id"])
            require(cancelled["status"] == "cancelled", f"取消失败：{cancelled}")
            print(f"[6] 计划列表 {listed['total']} 条｜取消后状态 {cancelled['status']}")

            # 7. 删除定时任务（用户在任务页删除）→ 计划同步取消
            schedules.bind_delete_hook(plans.on_schedule_deleted)
            third = await plans.start(
                name="凯尔希精二",
                targets=[{"operator": "凯尔希", "elite": 2}],
                run_times=["07:15"],
            )
            require(third["schedule_id"], f"第三个计划应有定时任务：{third}")
            await schedules.delete(str(third["schedule_id"]))
            after_delete = await plans._get_plan(third["plan"]["id"])
            require(
                after_delete["status"] == "cancelled",
                f"删除定时任务后计划应取消：{after_delete}",
            )
            require(
                (await plans._get_plan(started["plan"]["id"]))["status"] == "completed",
                "删除别的任务不该改动已完成计划",
            )
            print(f"[7] 删除定时任务：{third['schedule_id']} → 计划 {after_delete['status']}"
                  f"｜已完成计划仍是 completed")

            # 8. 差异（识别覆盖手动值）
            await box.patch_operator("char_172_svrash", {"level": 80}, source="manual")
            await capture.handle_callback(
                operbox_callback(
                    done=True,
                    own=[
                        {
                            "id": "char_172_svrash",
                            "name": "银灰",
                            "own": True,
                            "elite": 2,
                            "level": 85,
                            "potential": 3,
                            "rarity": 6,
                        }
                    ],
                ),
                message=Message.SubTaskExtraInfo,
            )
            diffs = await box.list_diffs()
            require(diffs["total"] >= 1, f"识别覆盖手动值应记差异：{diffs}")
            first = diffs["items"][0]
            print(f"[8] 差异：{diffs['total']} 条（{first['field']}："
                  f"{first['manual_value']} → {first['recognized_value']}）")
            dismissed = await box.dismiss_diffs(None)
            require(dismissed >= 1, f"「知道了」没有标记：{dismissed}")
            remaining = await box.list_diffs()
            require(not remaining["items"], f"标记后不该还有未处理差异：{remaining}")
            print(f"[8] 「知道了」标记 {dismissed} 条；未处理差异清零")

            # 9. M33 接线：注册好的 CALLBACK 处理器收到真实事件后异步落库。
            #    M27 的教训：直接调 `handle_callback` 的冒烟盯不住「谁在生产侧调用它」，
            #    这里必须走 `on("CALLBACK", ...)` 注册出来的那条路。
            client = FakeCoreClient()
            install_account_capture(
                client,
                capture,
                context_provider=lambda _msg, _details: ("pipeline-smoke", "task-smoke"),
                client_type_provider=lambda: "Bilibili",
            )
            require("CALLBACK" in client.handlers, "接线没有注册 CALLBACK 处理器")
            client.emit(
                {
                    "msg": int(Message.SubTaskExtraInfo),
                    "details": {
                        "class": "asst::OperBoxRecognitionTask",
                        "what": "OperBoxInfo",
                        "details": {
                            "done": True,
                            "all_opers": [
                                {
                                    "id": "char_010_chen",
                                    "name": "陈",
                                    "rarity": 6,
                                    "own": True,
                                },
                                {
                                    "id": "char_124_kroos",
                                    "name": "克洛丝",
                                    "rarity": 3,
                                    "own": False,
                                },
                            ],
                            "own_opers": [],
                        },
                    },
                }
            )
            # 处理器只创建任务（不阻塞消费线程），所以这里让出事件循环等它落库。
            wired = None
            for _ in range(50):
                wired = await _latest_operbox_recognition(factory)
                if wired is not None and wired.pipeline_id == "pipeline-smoke":
                    break
                await asyncio.sleep(0.01)
            require(wired is not None, "CALLBACK 事件没有落库")
            require(
                (wired.pipeline_id, wired.task_id, wired.client_type)
                == ("pipeline-smoke", "task-smoke", "Bilibili"),
                f"识别记录应带流水线上下文与渠道：{wired}",
            )
            chen = await box.get_operator("char_010_chen")
            require(chen["own"] is True, f"接线后陈应是已拥有：{chen}")
            print(f"[9] CALLBACK 接线：识别记录 {wired.kind} 带 pipeline={wired.pipeline_id}"
                  f"｜task={wired.task_id}｜渠道={wired.client_type}｜陈 own={chen['own']}")

            # 清理
            require(await _count(factory, UpgradePlan) == 3, "计划行数不对")
            require(await _count(factory, AccountRecognition) >= 2, "识别记录行数不对")
            await plan_service.list_plans()
            await engine.dispose()
        finally:
            await engine.dispose()
            for model in (
                UpgradePlan,
                AccountOperatorDiff,
                AccountRecognition,
                AccountDepotItem,
                AccountOperator,
            ):
                require(model.__tablename__, "模型表名缺失")


async def _count(factory: Any, model: Any) -> int:
    from sqlmodel import func, select

    async with factory() as session:
        result = await session.execute(select(func.count()).select_from(model))
        return int(result.scalar() or 0)


async def _latest_operbox_recognition(factory: Any) -> Any:
    """最新一条干员识别记录（接线那步靠它确认事件真的落库了）。"""

    async with factory() as session:
        return await AccountRecognitionRepository(session).latest(RECOGNITION_KIND_OPERBOX)


async def _fill_depot(factory: Any, items: dict[str, int]) -> None:
    """模拟一次仓库识别把材料备齐。"""
    import json

    payload = json.dumps({_item_id(name): count for name, count in items.items()})
    capture = AccountCaptureService(factory)
    await capture.handle_callback(depot_callback(done=True, data=payload), message=Message.SubTaskExtraInfo)


def _item_id(name: str) -> str:
    return {"固源岩组": "30013", "龙门币": "4001"}.get(name, name)


class Smoke:
    def run(self) -> int:
        try:
            asyncio.run(run())
        except Exception as exc:  # noqa: BLE001 - 冒烟脚本自己报告失败
            print(f"[account_smoke] [FAIL] {type(exc).__name__}: {exc}", flush=True)
            traceback.print_exc()
            return 1
        print("SMOKE OK", flush=True)
        return 0


def main() -> int:
    return Smoke().run()


if __name__ == "__main__":
    raise SystemExit(main())
