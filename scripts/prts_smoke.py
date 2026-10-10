#!/usr/bin/env python3
"""M25 的 PRTS 真网络冒烟（规格 §5.15 第 4 条，属补充证据，不是硬门槛）。

跑法::

    .venv/bin/python scripts/prts_smoke.py

覆盖：

1. `get_activity_stages` 不带参数：列出当时 PRTS 上全部在开的活动，每条带结束时间与
   `source=prts`；
2. 活动页给出的关卡代号区间能关联到本期 stageId（写方案时的例子是 `VEC-01…VEC-12` →
   `act3break_*`）；
3. `search_prts("爱国者")` 命中 `7-18 爱国者之死`；`get_stage("7-18")` 返回推荐等级
   `精英2 LV.30`、部署上限 9、敌人数量 45；
4. 关掉 `sources.prts_enabled` 后 `get_activity_stages` 返回本机结果（`source=resource`），
   注入段标题变成降级口径。

加 `--m26` 时再跑 M26 的四项（规格 §6.8）：干员、敌人、材料与掉落率。

成功时末行是 `SMOKE OK`。
"""

from __future__ import annotations

import argparse
import asyncio
import sys
import traceback
from pathlib import Path
from typing import Any

REPO_ROOT = Path(__file__).resolve().parents[1]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from maa_api.agent.knowledge import (  # noqa: E402
    ACTIVITY_HEADER_FALLBACK,
    ACTIVITY_HEADER_PRTS,
    render_activity_section,
)
from maa_api.services.external.http import ExternalClient  # noqa: E402
from maa_api.services.external.prts import PrtsClient  # noqa: E402
from maa_api.services.external.penguin import PenguinClient  # noqa: E402
from maa_api.services.game.activities import ActivityService  # noqa: E402
from maa_api.services.game.drops import DropService  # noqa: E402
from maa_api.services.game.enemies import EnemyService  # noqa: E402
from maa_api.services.game.items import ItemService  # noqa: E402
from maa_api.services.game.operators import OperatorService  # noqa: E402
from maa_api.services.game.pages import PageService  # noqa: E402
from maa_api.services.game.stages import StageService  # noqa: E402
from maa_api.settings import Settings, SourcesSettings  # noqa: E402


class SmokeFailure(RuntimeError):
    pass


def require(condition: Any, message: str) -> None:
    if not condition:
        raise SmokeFailure(message)


def settings_with_prts(enabled: bool) -> Settings:
    return Settings(
        sources=SourcesSettings(
            enabled=True, use_proxy=False, prts_enabled=enabled, prts_rate_per_minute=30
        )
    )


async def run(*, m26: bool = False) -> None:
    settings = settings_with_prts(True)
    client = ExternalClient(settings_provider=lambda: settings)
    prts = PrtsClient(client)
    activities = ActivityService(
        prts=prts, settings_provider=lambda: settings
    )
    stages = StageService(prts=prts, settings_provider=lambda: settings)
    pages = PageService(prts=prts)

    try:
        # 1. 当期活动
        report = await activities.current(detail="full", client_type="Official")
        require(report.fallback_reason is None, f"PRTS 不可用：{report.fallback_reason}")
        require(report.activities, "没有在开的活动")
        print(f"[1] 在开活动 {len(report.activities)} 个：")
        for activity in report.activities:
            require(activity.source == "prts", f"{activity.name} 的来源不是 prts")
            require(activity.end_at is not None, f"{activity.name} 没有结束时间")
            print(
                f"    - {activity.name}｜至 {activity.end_at:%m-%d %H:%M}｜"
                f"{activity.code_range}｜{len(activity.stages)} 关｜matched_by={activity.matched_by}"
            )
            require(
                activity.matched_by in {"stage_page_id", "local_code_name", "none"},
                f"未知的 matched_by：{activity.matched_by}",
            )

        # 2. 代号区间 → 本期 stageId
        checked = 0
        for activity in report.activities:
            range_info = activity.code_range
            if not range_info or not activity.stage_id_prefix:
                continue
            prefix = activity.stage_id_prefix
            resolved = {
                stage.code: stage.stage_id
                for stage in activity.stages
                if stage.stage_id and stage.stage_id.startswith(f"{prefix}_")
            }
            require(
                range_info["first"] in resolved and range_info["last"] in resolved,
                f"{activity.name} 的代号区间没能全部关联到 {prefix}_*：{range_info}",
            )
            print(
                f"[2] {activity.name}：{range_info['first']}…{range_info['last']}"
                f"（{range_info['count']} 关）→ {prefix}_*，示例 "
                f"{range_info['first']}={resolved[range_info['first']]}"
            )
            checked += 1
        require(checked > 0, "没有任何活动能关联出代号区间与 stageId")

        # 3. 搜索与单关
        found = await pages.search("爱国者", limit=5)
        titles = [item["title"] for item in found["results"]]
        require(
            any("爱国者之死" in title for title in titles),
            f"search_prts('爱国者') 没命中 7-18 爱国者之死：{titles}",
        )
        print(f"[3] search_prts('爱国者') → {titles[:3]}")

        detail = await stages.get("7-18")
        basic = detail.get("basic") or {}
        require(basic.get("recommend_level") == "精英2 LV.30", f"推荐等级不对：{basic}")
        require(basic.get("deploy_limit") == 9, f"部署上限不对：{basic}")
        require(basic.get("enemy_count") == 45, f"敌人数量不对：{basic}")
        require(detail.get("enemies"), "敌方情报为空")
        print(
            f"[3] get_stage('7-18') → 推荐等级 {basic['recommend_level']}｜部署上限 "
            f"{basic['deploy_limit']}｜敌人数量 {basic['enemy_count']}｜"
            f"敌方情报 {len(detail['enemies'])} 条｜装置 {len(detail.get('devices') or [])} 条"
        )

        # 4. 注入段（PRTS 与降级两种口径）
        summary = await activities.current(detail="summary", client_type="Official")
        section = render_activity_section(summary)
        require(section, "PRTS 可用时注入段为空")
        require(
            section[0].startswith("当期活动（PRTS"),
            f"注入标题不是 PRTS 口径：{section[0]}",
        )
        require(
            all(len(line) <= 120 for line in section[1:]),
            f"注入行超 120 字：{section[1:]}",
        )
        print(f"[4] 注入段（PRTS）：{section}")

        offline = settings_with_prts(False)
        offline_client = ExternalClient(settings_provider=lambda: offline)
        offline_activities = ActivityService(
            prts=PrtsClient(offline_client), settings_provider=lambda: offline
        )
        degraded = await offline_activities.current(detail="summary", client_type="Official")
        offline_section = render_activity_section(degraded)
        require(
            offline_section and offline_section[0] == ACTIVITY_HEADER_FALLBACK,
            f"关掉 PRTS 后没有走降级口径：{offline_section}",
        )
        require(
            degraded.activities and degraded.activities[0].source == "resource",
            "降级结果没有标 source=resource",
        )
        print(f"[4] 注入段（降级）：{offline_section}")
        require(
            ACTIVITY_HEADER_PRTS.split("{")[0] in section[0],
            "PRTS 标题与常量不一致",
        )
        if m26:
            await run_m26(client)
        await offline_client.aclose()
    finally:
        await client.aclose()


async def run_m26(client: Any) -> None:
    """M26 的四项：干员、敌人、材料、掉落率（规格 §6.8）。"""
    prts = PrtsClient(client)
    penguin = PenguinClient(client)
    operators = OperatorService(prts=prts)
    enemies = EnemyService(prts=prts)
    drops = DropService(penguin=penguin)
    items = ItemService(prts=prts, drops=drops)

    # 1. 干员：6★、三个技能都带专精描述、带模组
    silver = await operators.get("银灰", sections=["basic", "skills", "modules", "costs"])
    identity = silver["operator"]
    require(identity["rarity"] == 6, f"银灰的稀有度不是 6★：{identity}")
    require(silver["operator"]["char_id"] == "char_172_svrash", f"char_id 不对：{identity}")
    skills = silver.get("skills") or []
    require(len(skills) == 3, f"银灰应有三个技能，实际 {len(skills)}")
    for skill in skills:
        levels = {level["level"] for level in skill["levels"]}
        require(
            {"7", "专精1", "专精2", "专精3"} <= levels,
            f"{skill['name']} 缺专精描述：{sorted(levels)}",
        )
    modules = silver.get("modules") or []
    require(modules, "银灰没有模组")
    print(
        f"[M26-1] get_operator('银灰') → {identity['rarity']}★｜{identity['profession']}"
        f"｜技能 {len(skills)} 个（都带专精）｜模组 {len(modules)} 个"
        f"｜精一材料 {len((silver.get('costs') or {}).get('elite', {}).get('精1', []))} 项"
    )

    # 2. 敌人：源石虫有多级数值（实测两级）
    slug = await enemies.get("源石虫")
    levels = slug.get("levels") or []
    require(len(levels) >= 2, f"源石虫的级数不足：{levels}")
    require(levels[0].get("hp") is not None, f"源石虫缺少生命值：{levels[0]}")
    require(
        levels[-1].get("def") is not None,
        f"高级别没有用级别 0 补齐：{levels[-1]}",
    )
    print(
        f"[M26-2] get_enemy('源石虫') → {slug['enemy']['index']}｜{slug['enemy']['rank']}"
        f"｜{len(levels)} 级｜级别 0 生命 {levels[0]['hp']}、级别 {levels[-1]['index']} 生命 "
        f"{levels[-1]['hp']}｜抗性 {len(levels[-1].get('resistances') or {})} 项"
    )

    # 3. 材料：配方与掉率前 5
    item = await items.get("固源岩组")
    require(item["item"]["item_id"] == "30013", f"itemId 不对：{item['item']}")
    require(item["recipe"] and item["recipe"]["materials"], "固源岩组没有配方")
    rates = item.get("drop_rates") or []
    require(rates, "固源岩组的掉率前 5 为空")
    require(len(rates) <= 5, f"掉率条数超过 5：{len(rates)}")
    print(
        f"[M26-3] get_item('固源岩组') → itemId {item['item']['item_id']}｜配方原料 "
        f"{item['recipe']['materials']}｜副产物 {len(item['recipe']['byproducts'])} 项"
        f"｜掉率前 {len(rates)}：{[row['stage_code'] for row in rates]}"
    )

    # 4. 掉落率：按材料查，首位样本数 ≥ 100
    table = await drops.item_rates("固源岩组", limit=8)
    rows = table.get("rows") or []
    require(rows, "固源岩组的掉落率表为空")
    top = rows[0]
    require(
        not top["small_sample"] and top["times"] >= 100,
        f"首位的样本数不足：{top}",
    )
    print(
        f"[M26-4] get_drop_rates(item='固源岩组') → 共 {table['rows_total']} 行，首位 "
        f"{top['stage_code']}（{top['stage_id']}）掉率 {top['rate']:.4f}、样本 {top['times']}、"
        f"单件期望理智 {top['sanity_per_item']:.2f}"
    )


class Smoke:
    def __init__(self, *, m26: bool = False) -> None:
        self.m26 = m26

    def run(self) -> int:
        try:
            asyncio.run(run(m26=self.m26))
        except Exception as exc:  # noqa: BLE001 - 冒烟脚本自己报告失败
            print(f"[prts_smoke] [FAIL] {type(exc).__name__}: {exc}", flush=True)
            traceback.print_exc()
            return 1
        print("SMOKE OK", flush=True)
        return 0


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--m26", action="store_true", help="额外跑 M26 的干员、敌人、材料与掉落率四项"
    )
    args = parser.parse_args()
    return Smoke(m26=args.m26).run()


if __name__ == "__main__":
    raise SystemExit(main())
