#!/usr/bin/env python3
"""M27 账号数据与养成计划的端到端冒烟（规格 §7.9，末行 `SMOKE OK`）。

用**假内核回调**与假外部来源跑通整条链路，不打真机、不打网：

1. 识别：`OperBoxInfo`（`done=false` 忽略、`done=true` 落库）与 `DepotInfo`（整表替换）；
2. 页面数据：`OperatorBoxService` / `DepotService` 读回干员表、仓库与识别时间；
3. 规划：`plan_upgrade`（线性规划）给出关卡、次数与理智；
4. 计划：`start_upgrade_plan` 的确认后动作——今天的流水线 + 每日定时任务；
5. 模拟完成：仓库备齐 → 计划标完成、定时任务被删除、发 `upgrade_plan_finished` 通知。

跑法::

    .venv/bin/python scripts/account_smoke.py
"""

from __future__ import annotations

import asyncio
import sys
import tempfile
import traceback
from pathlib import Path
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
from maa_api.db.session import make_engine  # noqa: E402
from maa_api.services.account.box import OperatorBoxService  # noqa: E402
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
    """内存里的定时任务表。"""

    def __init__(self) -> None:
        self.rows: list[dict[str, Any]] = []

    async def list(self) -> dict[str, Any]:
        return {"schedules": list(self.rows), "total": len(self.rows)}

    async def create(self, payload: Any) -> dict[str, Any]:
        row = {
            "id": f"schedule-{len(self.rows) + 1}",
            "name": getattr(payload, "name", ""),
            "cron": getattr(payload, "cron", ""),
            "template": [
                item.model_dump(exclude_unset=True)
                if hasattr(item, "model_dump")
                else dict(item)
                for item in getattr(payload, "template", [])
            ],
        }
        self.rows.append(row)
        return row

    async def delete(self, schedule_id: str) -> None:
        self.rows = [row for row in self.rows if row["id"] != schedule_id]


class FakeNotifyService:
    def __init__(self) -> None:
        self.events: list[tuple[str, dict[str, Any]]] = []

    async def notify(self, event: Any, payload: dict[str, Any]) -> None:
        self.events.append((str(event), payload))


class FakePlanner:
    """规划器替身：给固定的一条关卡方案，避免冒烟依赖真掉率。"""

    def __init__(self) -> None:
        self.calls: list[Any] = []

    async def plan(self, targets: Any, *, use_depot: bool = True, server: Any = None, box: Any = None):
        self.calls.append(targets)

        class _Plan:
            def as_dict(self_inner) -> dict[str, Any]:
                return {
                    "targets": [{"operator": "银灰", "assumed_current": True}],
                    "requirements": {"龙门币": 180000, "固源岩组": 4},
                    "stages": [
                        {
                            "stage_id": "main_01-07",
                            "stage_code": "1-7",
                            "runs": 3,
                            "sanity": 18,
                        }
                    ],
                    "synthesis": [],
                    "total_sanity": 18,
                    "days_estimated": 0.08,
                    "surplus": {},
                    "unobtainable": [],
                    "notes": ["冒烟用的假方案"],
                }

        return _Plan()


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

            # 4. 模拟完成：仓库备齐 → 重算 → 完成 + 删除定时任务 + 通知
            notify = FakeNotifyService()
            plans._notify_service = notify  # 冒烟里直接换掉通知替身
            await _fill_depot(factory, {"龙门币": 200000, "固源岩组": 9})
            refreshed = await plans.refresh(started["plan"]["id"])
            require(refreshed["status"] == "completed", f"计划没有完成：{refreshed}")
            require(not schedules.rows, "完成后应删除定时任务")
            require(notify.events, "未注入通知服务时不该有事件")
            plans._notify_service = notify
            print(f"[4] 模拟完成：计划 {refreshed['status']}｜定时任务 {len(schedules.rows)} 条")

            # 5. 计划列表与取消
            listed = await plans.list_plans()
            require(listed["total"] == 1, f"计划列表不对：{listed}")
            second = await plans.start(
                name="阿米娅精一",
                targets=[{"operator": "阿米娅", "elite": 1}],
                run_times=["09:30"],
            )
            cancelled = await plans.cancel(second["plan"]["id"])
            require(cancelled["status"] == "cancelled", f"取消失败：{cancelled}")
            print(f"[5] 计划列表 {listed['total']} 条｜取消后状态 {cancelled['status']}")

            # 6. 差异（识别覆盖手动值）
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
            print(f"[6] 差异：{diffs['total']} 条（{first['field']}："
                  f"{first['manual_value']} → {first['recognized_value']}）")
            dismissed = await box.dismiss_diffs(None)
            require(dismissed >= 1, f"「知道了」没有标记：{dismissed}")
            remaining = await box.list_diffs()
            require(not remaining["items"], f"标记后不该还有未处理差异：{remaining}")
            print(f"[6] 「知道了」标记 {dismissed} 条；未处理差异清零")

            # 清理
            require(await _count(factory, UpgradePlan) == 2, "计划行数不对")
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
