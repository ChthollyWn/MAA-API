#!/usr/bin/env python3
"""M29 阵容研究的端到端冒烟（规格 §9.12，末行 `SMOKE OK`）。

用**假作业站**与**假内核回调**跑通整条链路，不打真机、不打网：

1. 搜索：`search_copilots` 按账号数据把候选分成 `ok` / `missing_one` / `infeasible`；
2. 导入：`save_copilot_draft(site_id=…)` 存成 COPILOT 资源并做静态校验；
3. 授权：没有授权时策略返回 `grant_copilot_attempts`，批准后写 `copilot_grant`；
4. 两次尝试：第一次「行动失败」，第二次「三星」——结果由识别回调判定；
5. 通知：授权进终态后 `copilot_research_finished` 的 payload 成型；
6. 清理：`cleanup_copilots` 保留成功的作业、删掉过期的 AI 草稿。

跑法::

    .venv/bin/python scripts/copilot_research_smoke.py
"""

from __future__ import annotations

import asyncio
import json
import sys
import tempfile
import traceback
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any

REPO_ROOT = Path(__file__).resolve().parents[1]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

import maa_api.db.models  # noqa: F401,E402 - 建表前先导入模型
from maa_api.agent.policy import PolicyEngine  # noqa: E402
from maa_api.agent.registry import ToolContext  # noqa: E402
from maa_api.agent.tools import build_registry  # noqa: E402
from maa_api.db.session import make_engine  # noqa: E402
from maa_api.domain.enums import CallerType  # noqa: E402
from maa_api.services.copilot.draft import CopilotDraftService  # noqa: E402
from maa_api.services.copilot.grant import CopilotGrantService  # noqa: E402
from maa_api.services.copilot.notify import (  # noqa: E402
    build_copilot_research_notification,
)
from maa_api.services.copilot.result import (  # noqa: E402
    CopilotResultService,
    judge_callbacks,
)
from maa_api.services.external.copilot_site import (  # noqa: E402
    CopilotRow,
    CopilotSiteData,
)
from maa_api.services.game.copilots import CopilotService  # noqa: E402
from maa_api.services.retention_service import cleanup_copilots  # noqa: E402
from sqlalchemy import event  # noqa: E402
from sqlalchemy.ext.asyncio import async_sessionmaker  # noqa: E402
from sqlalchemy.pool import NullPool  # noqa: E402
from sqlmodel import SQLModel  # noqa: E402


def keep_only_ok(payload: dict[str, Any]) -> list[dict[str, Any]]:
    """`only_feasible=true` 的返回：没有 `tier` 字段的就按整体结果取。"""
    rows = payload.get("results") or []
    filtered = [row for row in rows if row.get("tier") in (None, "ok")]
    return filtered or rows


class SmokeFailure(RuntimeError):
    pass


def require(condition: Any, message: str) -> None:
    if not condition:
        raise SmokeFailure(message)


def _disable_foreign_keys(connection: Any, _record: Any) -> None:
    """临时库关掉外键：授权行引用的是内存里的假会话 id。"""
    cursor = connection.cursor()
    cursor.execute("PRAGMA foreign_keys=OFF")
    cursor.close()


# ---------------------------------------------------------------------------
# 假作业站：一份可行作业、一份缺一名、一份不可行
# ---------------------------------------------------------------------------
def _copilot(stage: str, opers: list[tuple[str, int, int, int]]) -> str:
    return json.dumps(
        {
            "stage_name": stage,
            "minimum_required": "v4.0",
            "doc": {"title": f"{stage} 三星作业"},
            "opers": [
                {
                    "name": name,
                    "skill": 1,
                    "skill_usage": 1,
                    "requirements": {"elite": elite, "level": level, "skill_level": skill},
                }
                for name, elite, level, skill in opers
            ],
            "actions": [
                {"type": "Deploy", "name": name, "location": [1, 2], "direction": "Right"}
                for name, *_ in opers
            ],
        },
        ensure_ascii=False,
    )


class FakeSiteClient:
    """作业站替身：`query` 返回三份候选，`get` 返回完整作业。"""

    def __init__(self) -> None:
        self.rows = [
            CopilotRow(
                site_id=101,
                uploader="冒烟",
                views=1000,
                rating_ratio=0.98,
                hot_score=99.0,
                content=_copilot("1-7", [("银灰", 2, 90, 7), ("克洛丝", 1, 60, 7)]),
            ),
            CopilotRow(
                site_id=102,
                uploader="冒烟",
                views=500,
                rating_ratio=0.90,
                hot_score=80.0,
                content=_copilot("1-7", [("银灰", 2, 90, 7), ("无名干员", 2, 90, 7)]),
            ),
            CopilotRow(
                site_id=103,
                uploader="冒烟",
                views=10,
                rating_ratio=0.50,
                hot_score=1.0,
                content=_copilot("1-7", [("甲", 2, 90, 7), ("乙", 2, 90, 7)]),
            ),
        ]

    async def query(self, *, stage: str, order: str = "hot", page: int = 1, limit: int = 5):
        return CopilotSiteData(
            endpoint="query",
            url="https://prts.maa.plus/copilot/query",
            fetched_at=datetime.now(timezone.utc),
            cached=False,
            stale=False,
            rows=tuple(self.rows[:limit]),
            page=page,
            limit=limit,
            total=len(self.rows),
            has_next=False,
            status_code=200,
        )

    async def get(self, site_id: int | str):
        for row in self.rows:
            if str(row.site_id) == str(site_id):
                return CopilotSiteData(
                    endpoint="get",
                    url=f"https://prts.maa.plus/copilot/get/{site_id}",
                    fetched_at=datetime.now(timezone.utc),
                    cached=False,
                    stale=False,
                    rows=(row,),
                    page=1,
                    limit=1,
                    total=1,
                    has_next=False,
                    status_code=200,
                )
        raise AssertionError(f"冒烟里没有作业站作业 {site_id}")


class FakeBox:
    """账号数据替身：只有银灰与克洛丝。"""

    async def list_operators(self, *, query=None, owned_only=True, limit=50, **kwargs):
        rows = [
            {"char_id": "char_172_svrash", "name": "银灰", "rarity": 6, "own": True, "elite": 2, "level": 90, "skill_level": 7, "masteries": {"1": 3}, "modules": {}},
            {"char_id": "char_124_kroos", "name": "克洛丝", "rarity": 3, "own": True, "elite": 1, "level": 60, "skill_level": 7, "masteries": {}, "modules": {}},
        ]
        if query:
            rows = [row for row in rows if query in row["name"]]
        return {"items": rows, "total": len(rows), "limit": limit, "offset": 0}

    async def get_operator(self, char_id: str):
        for row in (await self.list_operators())["items"]:
            if row["char_id"] == char_id:
                return row
        raise AssertionError(char_id)


# ---------------------------------------------------------------------------
# 主流程
# ---------------------------------------------------------------------------
async def run() -> None:
    with tempfile.TemporaryDirectory() as tmp:
        engine = make_engine(
            f"sqlite+aiosqlite:///{Path(tmp) / 'smoke.db'}", poolclass=NullPool
        )
        event.listen(engine.sync_engine, "connect", _disable_foreign_keys)
        try:
            async with engine.begin() as connection:
                await connection.run_sync(SQLModel.metadata.create_all)
            factory = async_sessionmaker(engine, expire_on_commit=False)
            box = FakeBox()
            site = FakeSiteClient()

            # 1. 搜索与三档判定
            copilots = CopilotService(box=box, site_client=site)
            all_rows = await copilots.search(stage="1-7", limit=5, only_feasible=False)
            tiers = dict(all_rows.get("tiers") or {})
            require(tiers.get("ok"), f"没有可行的作业：{all_rows}")
            require(tiers.get("missing_one"), f"没有「缺一名」的作业：{all_rows}")
            found = await copilots.search(stage="1-7", limit=5, only_feasible=True)
            kept = keep_only_ok(found)
            require(len(kept) < len(all_rows["results"]), f"筛选没有起作用：{found}")
            first = kept[0]
            print(
                f"[1] search_copilots('1-7') → 候选 {len(all_rows['results'])} 份"
                f"｜三档 {tiers}｜可执行 {len(kept)} 份"
                f"｜首位 {first.get('title') or ('作业站 #' + str(first.get('site_id')))}"
            )

            # 2. 导入成草稿（静态校验 + 存资源）
            # 冒烟不读真地块与干员基础数据（那两样走网络/资源包，由单测用真夹具覆盖）：
            # `layout_provider` / `operators_provider` 返回 None 时静态检查记 skipped，
            # 协议校验照常执行。
            from maa_api.services.resource_service import ResourceService

            resource_root = Path(tmp) / "resource"
            resource_root.mkdir(parents=True, exist_ok=True)
            resources = ResourceService(factory, resource_root=resource_root)
            draft_service = CopilotDraftService(
                factory,
                resource_service=resources,
                box=box,
                site_client=site,
                layout_provider=lambda _stage: None,
                operators_provider=lambda: None,
            )
            saved = await draft_service.save(site_id=101, note="冒烟用")
            asset_id = saved["id"]
            require(saved["source"] == "site", f"草稿来源不对：{saved}")
            require(saved["site_id"] in (101, "101"), f"草稿没有记站点 id：{saved}")
            require(saved["checks"]["ok"] is True, f"草稿静态校验没过：{saved['checks']}")
            print(
                f"[2] save_copilot_draft(site_id=101) → 资源 {asset_id[:8]}…"
                f"｜来源 {saved['source']}｜关卡 {saved['stage_id']}"
                f"｜校验 跳过项 {len(saved['checks']['skipped'])} 条"
            )

            # 3. 没有授权 → 弹授权卡片
            grants = CopilotGrantService(factory)
            registry = build_registry()
            definition = registry.get("run_copilot_attempt")
            params = definition.params_model.model_validate(
                {"copilot_id": asset_id, "grant_attempts": 2, "success": "three_star"}
            )
            context = ToolContext(
                caller=CallerType.INTERNAL,
                session_id="sess-smoke",
                request_id="req-1",
                request=None,
                db_session=None,
            )
            decision = await PolicyEngine().evaluate(definition, params, context)
            require(decision.requires_confirmation, f"没有授权应当弹卡片：{decision}")
            require(
                decision.confirmation_action == "grant_copilot_attempts",
                f"确认动作不对：{decision.confirmation_action}",
            )
            require(decision.extra_payload is not None, "确认 payload 缺失")
            print(
                f"[3] 无授权 → 确认动作 {decision.confirmation_action}"
                f"｜预填 {decision.extra_payload['attempts']} 次 / "
                f"{decision.extra_payload['success']}"
            )

            # 4. 批准授权
            grant = await grants.approve(
                "sess-smoke",
                stage_id="main_01-07",
                stage_code="1-7",
                attempts=2,
                success="three_star",
                support="fill_one",
                grant_id="confirm-smoke",
            )
            wire = await grants.session_wire("sess-smoke")
            require(wire and wire["status"] == "active", f"授权没生效：{wire}")
            require(wire["attempts_total"] == 2, f"授权次数不对：{wire}")
            print(
                f"[4] 批准授权 → {wire['status']}｜{wire['attempts_used']}/{wire['attempts_total']} 次"
                f"｜成功标准 {wire['success']}｜助战 {wire['support']}"
            )

            # 5. 两次尝试：第一次失败、第二次三星
            first = await grants.consume("sess-smoke", "main_01-07", stage_code="1-7")
            require(
                first["remaining"] == 1, f"第一次尝试后应剩 1 次：{first}"
            )
            failed = judge_callbacks(
                [{"what": "SubTaskExtraInfo", "details": {"task": "MaaApi@CopilotResult@Failed"}}],
                support="fill_one",
            )
            require(failed == "failed", f"第一次应判定失败：{failed}")
            second = await grants.consume("sess-smoke", "main_01-07", stage_code="1-7")
            require(second["remaining"] == 0, f"第二次尝试后应剩 0 次：{second}")
            three_star = judge_callbacks(
                [{"what": "SubTaskExtraInfo", "details": {"task": "MaaApi@CopilotResult@ThreeStar"}}],
                support="fill_one",
            )
            require(three_star == "three_star", f"第二次应判定三星：{three_star}")
            print(
                f"[5] 两次尝试 → 第 1 次 {failed}（剩 {first['remaining']} 次）、"
                f"第 2 次 {three_star}（剩 {second['remaining']} 次）"
            )

            # 6. 终态通知
            await grants.finish("sess-smoke", "succeeded")
            notify_event, payload = build_copilot_research_notification(
                "success",
                stage_code="1-7",
                attempts_used=2,
                attempts_total=2,
                copilot_id=asset_id,
                copilot_title="1-7 三星速通",
                session_id="sess-smoke",
            )
            require(
                notify_event == "copilot_research_finished",
                f"通知事件不对：{notify_event}",
            )
            require(payload.get("attempts_used") == 2, f"通知 payload 缺次数：{payload}")
            print(f"[6] 通知 {notify_event} →「{payload['title']}」")

            # 7. 清理：成功的作业保留，过期的 AI 草稿删掉
            stale = await draft_service.save(
                content=json.loads(_copilot("1-7", [("银灰", 2, 90, 7)])),
                note="过期草稿",
                # `source="ai"` 才会被自动清理（规格 §9.11：只清 AI 与作业站副本）。
                source="ai",
            )
            await _age_asset(factory, stale["id"], days=30)
            cleaned = await cleanup_copilots(session_factory=factory)
            require(
                stale["id"] in cleaned["deleted"],
                f"过期草稿应被删掉：{cleaned}",
            )
            require(
                asset_id not in cleaned["deleted"],
                f"新导入的作业不该被删：{cleaned}",
            )
            require(
                any(item["id"] == asset_id for item in cleaned["skipped"]),
                f"新导入的作业应记一条保留原因：{cleaned}",
            )
            print(
                f"[7] cleanup_copilots → 删除 {len(cleaned['deleted'])} 份"
                f"｜保留 {len(cleaned['skipped'])} 份（含成功作业）"
            )

            await engine.dispose()
        finally:
            await engine.dispose()


async def _age_asset(factory: Any, asset_id: str, *, days: int) -> None:
    """把资源创建时间往前拨，模拟「创建超过保留天数」。"""
    from maa_api.db.models import ResourceAsset, utcnow

    async with factory() as session:
        row = await session.get(ResourceAsset, asset_id)
        require(row is not None, f"资源不存在：{asset_id}")
        row.created_at = utcnow() - timedelta(days=days)
        session.add(row)
        await session.commit()


def main() -> int:
    try:
        asyncio.run(run())
    except Exception as exc:  # noqa: BLE001 - 冒烟脚本自己报告失败
        print(f"[copilot_research_smoke] [FAIL] {type(exc).__name__}: {exc}", flush=True)
        traceback.print_exc()
        return 1
    print("SMOKE OK", flush=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
