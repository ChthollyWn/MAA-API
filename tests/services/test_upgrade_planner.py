"""养成规划（规格 §7.6）：小型合成数据上的最优解、候选筛选、降级与假设。

全部用假的 operators / drops / items（PRTS）+ 假的 ``box``，不打网；本机常驻关表
用 ``monkeypatch`` 换成固定集合（``planner.permanent_stage_ids`` 是留给测试的接缝，
生产路径读 ``<资源层>/stages.json``）。
"""

from __future__ import annotations

import asyncio
import functools
import json
from dataclasses import dataclass, field
from types import SimpleNamespace
from typing import Any, Mapping, Sequence

import pytest

from maa_api.domain.errors import AppError, ErrorCode
from maa_api.services.account import planner
from maa_api.services.account.planner import UpgradePlanner, UpgradeTarget
from maa_api.services.external.prts import WikiPage
from maa_api.services.game.levelup import LevelUpTable
from maa_api.settings import Settings

# --------------------------------------------------------------------------- #
# 工具
# --------------------------------------------------------------------------- #


def async_test(func):
    """本仓库不装 pytest-asyncio：异步用例统一用 `asyncio.run` 跑。

    ``functools.wraps`` 让 pytest 顺着 ``__wrapped__`` 看到原函数的参数，
    需要 ``monkeypatch`` 等 fixture 的异步用例才能拿到注入。
    """

    @functools.wraps(func)
    def wrapper(*args, **kwargs):
        return asyncio.run(func(*args, **kwargs))

    return wrapper


def _settings() -> Settings:
    return Settings()


# --------------------------------------------------------------------------- #
# 假服务
# --------------------------------------------------------------------------- #


class FakeOperators:
    """``OperatorService`` 的最小替身：只回答 ``get(name, sections=[...])``。"""

    def __init__(self, payloads: Mapping[str, Mapping[str, Any]]) -> None:
        self.payloads = dict(payloads)
        self.calls: list[dict[str, Any]] = []

    async def get(
        self,
        name: str,
        *,
        sections: Sequence[str] | None = None,
        skill_detail: str = "max",
    ) -> dict[str, Any]:
        self.calls.append({"name": name, "sections": tuple(sections or ())})
        payload = self.payloads.get(name)
        if payload is None:
            raise AppError(
                ErrorCode.GAME_DATA_NOT_FOUND, f"找不到干员：{name}", {"name": name}
            )
        return {"query": name, "operator": {"name": name, "char_id": payload.get("char_id", f"char-{name}")},
                "candidates": [], "candidates_total": 1, **payload}


class FakeDrops:
    """``DropService.item_rates`` 的最小替身。``default=True`` 时任何材料都有一关。"""

    def __init__(self, rates: Mapping[str, Sequence[Mapping[str, Any]]] | None = None, *,
                 default: bool = False, default_ap_cost: int = 6) -> None:
        self.rates = {name: [dict(row) for row in rows] for name, rows in (rates or {}).items()}
        self.default = default
        self.default_ap_cost = default_ap_cost
        self.calls: list[dict[str, Any]] = []

    async def item_rates(
        self,
        item: str,
        *,
        limit: int = 8,
        include_closed: bool = False,
        server: str | None = None,
    ) -> dict[str, Any]:
        self.calls.append(
            {"item": item, "limit": limit, "include_closed": include_closed, "server": server}
        )
        if item in self.rates:
            rows = self.rates[item]
        elif self.default:
            rows = [
                stage_row(
                    "main_01-07", "1-7", item, times=1000, quantity=1000,
                    ap_cost=self.default_ap_cost,
                )
            ]
        else:
            raise AppError(
                ErrorCode.GAME_DATA_NOT_FOUND, f"企鹅物流里没有「{item}」", {"item": item}
            )
        return {
            "query": item,
            "item": {"item_id": f"id-{item}", "name": item},
            "rows": rows[:limit],
            "rows_total": len(rows),
            "include_closed": include_closed,
            "server": server or "CN",
        }


class _Batch:
    def __init__(self, pages: Sequence[WikiPage]) -> None:
        self.pages = tuple(pages)


class FakePrts:
    """``PrtsClient.wikitext`` 的最小替身（一次最多 20 个标题的批量取页面）。"""

    def __init__(self, pages: Mapping[str, str] | None = None) -> None:
        self.pages = dict(pages or {})
        self.calls: list[tuple[str, ...]] = []

    async def wikitext(self, titles: Sequence[str]) -> _Batch:
        requested = [str(title) for title in titles]
        self.calls.append(tuple(requested))
        pages = []
        for title in requested:
            content = self.pages.get(title)
            pages.append(
                WikiPage(
                    page_id=1 if content is not None else None,
                    title=title,
                    content=content or "",
                    revision=1 if content is not None else None,
                    timestamp=None,
                    missing=content is None,
                )
            )
        return _Batch(pages)


class FakeItems:
    """``ItemService`` 的最小替身：planner 只用它的 ``prts`` 属性。"""

    def __init__(self, prts: FakePrts) -> None:
        self.prts = prts


class FakeIndex:
    """``ActivityIndex`` 的最小替身：``find_stage`` + ``snapshot``。"""

    def __init__(self, matches: Mapping[str, Sequence[Mapping[str, Any]]] | None = None,
                 snapshot: Any = None, *, fail: bool = False) -> None:
        self.matches = {key: [dict(row) for row in rows] for key, rows in (matches or {}).items()}
        self._snapshot = snapshot
        self.fail = fail
        self.calls: list[str] = []

    def find_stage(self, query: str) -> dict[str, Any]:
        self.calls.append(query)
        if self.fail:
            raise AppError(ErrorCode.RESOURCE_LOAD_FAILED, "活动资源不可用")
        rows = self.matches.get(query, [])
        return {"query": query, "matches": rows, "matches_total": len(rows)}

    def snapshot(self) -> Any:
        if self._snapshot is None:
            raise AppError(ErrorCode.RESOURCE_LOAD_FAILED, "活动资源不可用")
        return self._snapshot


class FakeDepot:
    """``DepotService.list_items`` 的最小替身（``page_cap`` 用来逼出分页）。"""

    def __init__(self, counts: Mapping[str, int] | None = None, *, page_cap: int | None = None) -> None:
        self.counts = dict(counts or {})
        self.page_cap = page_cap
        self.calls: list[dict[str, Any]] = []

    async def list_items(
        self, *, category: str | None = None, query: str | None = None,
        limit: int = 100, offset: int = 0,
    ) -> dict[str, Any]:
        self.calls.append({"limit": limit, "offset": offset})
        rows = [
            {"item_id": f"i{index}", "name": name, "count": count, "category": "材料"}
            for index, (name, count) in enumerate(self.counts.items())
        ]
        size = limit if self.page_cap is None else min(limit, self.page_cap)
        return {
            "items": rows[offset : offset + size],
            "total": len(rows),
            "limit": limit,
            "offset": offset,
        }


def box_row(name: str, **overrides: Any) -> dict[str, Any]:
    """照 ``account/rules.box_row_wire`` 的行形状。"""
    row = {
        "char_id": f"char-{name}",
        "name": name,
        "rarity": 6,
        "profession": "近卫",
        "own": True,
        "elite": 0,
        "level": 1,
        "potential": 0,
        "skill_level": 1,
        "masteries": {"1": 0, "2": 0, "3": 0},
        "modules": {},
        "field_sources": {},
        "recognized_at": None,
        "updated_at": None,
    }
    row.update(overrides)
    return row


class FakeBox:
    """``OperatorBoxService`` 的最小替身（``row_key`` 用来同时覆盖 items/operators 两种键）。"""

    def __init__(self, rows: Sequence[Mapping[str, Any]] = (), *, depot: Any = None,
                 row_key: str = "items") -> None:
        self.rows = [dict(row) for row in rows]
        self.depot = depot
        self.row_key = row_key
        self.calls: list[dict[str, Any]] = []

    async def list_operators(self, **filters: Any) -> dict[str, Any]:
        self.calls.append(filters)
        query = str(filters.get("query") or "")
        matched = [row for row in self.rows if query and query in str(row.get("name") or "")]
        limit = int(filters.get("limit") or 50)
        return {self.row_key: matched[:limit], "total": len(matched)}

    async def get_operator(self, char_id: str) -> dict[str, Any]:
        for row in self.rows:
            if row.get("char_id") == char_id:
                return dict(row)
        raise AppError(ErrorCode.ACCOUNT_DATA_NOT_FOUND, "没有记录", {"char_id": char_id})


# --------------------------------------------------------------------------- #
# 合成数据（材料成本 / 掉率 / PRTS 页面）
# --------------------------------------------------------------------------- #

TARGET = "目标料"
FINE = "精密料"
BLOCK = "模组数据块"
BASE_COSTS: dict[str, Any] = {
    "elite": {
        "精1": [{"name": "龙门币", "amount": 30000}, {"name": TARGET, "amount": 10}],
        "精2": [{"name": "龙门币", "amount": 180000}, {"name": FINE, "amount": 4}],
    },
    "skill_levels": {
        "2": [{"name": "技巧概要·卷1", "amount": 5}],
        "3": [{"name": "技巧概要·卷1", "amount": 5}, {"name": "基础料", "amount": 6}],
        "4": [{"name": "技巧概要·卷2", "amount": 8}],
        "5": [{"name": "技巧概要·卷2", "amount": 8}, {"name": "基础料", "amount": 5}],
        "6": [{"name": "技巧概要·卷2", "amount": 8}, {"name": "基础料", "amount": 7}],
        "7": [{"name": "技巧概要·卷3", "amount": 8}, {"name": "基础料", "amount": 4}],
        "一8": [{"name": "专精料", "amount": 6}],
        "一9": [{"name": "专精料", "amount": 12}],
        "一10": [{"name": "专精料", "amount": 15}],
    },
    "modules": [
        {
            "name": "甲模组",
            "levels": [
                {"level": 1, "materials": [{"name": BLOCK, "amount": 4}, {"name": "龙门币", "amount": 80000}]},
                {"level": 2, "materials": [{"name": BLOCK, "amount": 4}, {"name": "龙门币", "amount": 100000}]},
                {"level": 3, "materials": [{"name": BLOCK, "amount": 4}, {"name": "龙门币", "amount": 120000}]},
            ],
        }
    ],
    "note": "测试用",
}

MODULES_SECTION: list[dict[str, Any]] = [
    {"name": "甲模组", "type_code": "LOR-X", "levels": BASE_COSTS["modules"][0]["levels"]}
]

BASE_PAYLOAD: dict[str, Any] = {
    "costs": BASE_COSTS,
    "modules": MODULES_SECTION,
    "unavailable": [],
}


def stage_row(
    stage_id: str,
    code: str,
    item: str,
    *,
    times: int,
    quantity: int,
    ap_cost: int,
    open_: bool = True,
    start: str = "2024-01-01",
    end: str | None = None,
) -> dict[str, Any]:
    """照 ``DropService`` 的行形状（``rate = quantity / times``）。"""
    rate = quantity / times
    return {
        "stage_id": stage_id,
        "stage_code": code,
        "item_id": f"id-{item}",
        "item_name": item,
        "times": times,
        "quantity": quantity,
        "rate": rate,
        "ap_cost": ap_cost,
        "sanity_per_item": (ap_cost / rate) if rate > 0 else None,
        "small_sample": times < 100,
        "started_at": start,
        "ended_at": end,
        "open": open_,
    }


def item_page(name: str, *, recipe: str | None = None, obtain: str | None = None) -> str:
    lines = ["{{道具信息", f"|名称={name}", "|itemId=30001", "|稀有度=3"]
    if obtain:
        lines.append(f"|获得方式={obtain}")
    text = "\n".join(lines) + "\n}}\n"
    if recipe:
        text += recipe + "\n"
    return text


def craft(product: str, materials: Sequence[tuple[str, int]], *, count: int = 1,
          lmd: int | None = None) -> str:
    lines = ["{{道具配方/加工站", f"|产物={product}", f"|产物数量={count}"]
    for index, (name, quantity) in enumerate(materials, start=1):
        lines.append(f"|原料{index}={name}")
        lines.append(f"|原料{index}数量={quantity}")
    if lmd is not None:
        lines.append(f"|龙门币消耗={lmd}")
    lines.append("}}")
    return "\n".join(lines)


# --------------------------------------------------------------------------- #
# 夹具
# --------------------------------------------------------------------------- #


@dataclass
class Harness:
    planner: UpgradePlanner
    operators: FakeOperators
    drops: FakeDrops
    prts: FakePrts
    index: FakeIndex
    box: Any = None


def harness(
    *,
    payloads: Mapping[str, Mapping[str, Any]] | None = None,
    rates: Mapping[str, Sequence[Mapping[str, Any]]] | None = None,
    pages: Mapping[str, str] | None = None,
    default_rates: bool = False,
    box: Any = None,
    depot: Any = None,
    index: Any = None,
    row_key: str = "items",
) -> Harness:
    operators = FakeOperators(payloads if payloads is not None else {"甲": BASE_PAYLOAD})
    drops = FakeDrops(rates, default=default_rates)
    prts = FakePrts(pages)
    box = box if box is not None else FakeBox(row_key=row_key)
    index = index if index is not None else FakeIndex()
    planner_instance = UpgradePlanner(
        operators=operators,
        drops=drops,
        items=FakeItems(prts),
        index=index,
        depot=depot,
        box=box,
        # M29：升级经验 / 龙门币用内置的官方数值表。显式注入而不是让规划器惰性加载，是为了
        # 让这组用例既不打网、也不受真实拆包数据的影响（惰性加载只用于生产装配）。
        levelup=LevelUpTable.builtin(),
        settings_provider=_settings,
    )
    return Harness(planner=planner_instance, operators=operators, drops=drops,
                   prts=prts, index=index, box=box)


@pytest.fixture(autouse=True)
def permanent_stages(monkeypatch: pytest.MonkeyPatch) -> None:
    """本机 stages.json 换成固定集合：测试不读真实资源目录。"""
    monkeypatch.setattr(
        planner, "permanent_stage_ids", lambda: frozenset({"main_01-07", "main_04-04"})
    )


# --------------------------------------------------------------------------- #
# 线性规划
# --------------------------------------------------------------------------- #


@async_test
async def test_lp_picks_cheapest_stage_and_reports_basis() -> None:
    """两个候选关：1-7 单件 12 理智、4-4 单件 18 理智 → 选 1-7。"""
    h = harness(
        rates={
            TARGET: [
                stage_row("main_01-07", "1-7", TARGET, times=10000, quantity=5000, ap_cost=6),
                stage_row("main_04-04", "4-4", TARGET, times=10000, quantity=10000, ap_cost=18),
            ]
        }
    )

    plan = await h.planner.plan([UpgradeTarget("甲", elite=1)], box=h.box)

    assert plan.feasible is True
    assert plan.requirements == {TARGET: 10, "龙门币": 30000}
    assert [stage["stage_id"] for stage in plan.stages] == ["main_01-07"]
    stage = plan.stages[0]
    assert stage["stage_code"] == "1-7"
    assert stage["runs"] == 20
    assert stage["sanity_per_run"] == 6
    assert stage["sanity"] == 120
    basis = stage["items"][0]
    assert basis["item_name"] == TARGET
    assert basis["rate"] == pytest.approx(0.5)
    assert basis["sanity_per_item"] == pytest.approx(12.0)
    assert basis["times"] == 10000
    assert plan.total_sanity == 120
    assert plan.total_runs == 20
    assert plan.days_estimated == pytest.approx(0.5)
    assert plan.synthesis == ()
    assert plan.surplus == {}
    assert plan.unobtainable == ()
    assert any("未计入升级所需的经验与龙门币" in note for note in plan.notes)
    assert any("样本" in note for note in plan.notes)
    # 龙门币只报需求、不进刷关规划（规格 §7.3）
    assert "龙门币" not in {row["name"] for row in plan.unobtainable}
    assert all(row["item"] != "龙门币" for row in h.drops.calls)


@async_test
async def test_synthesis_beats_farming_and_lmd_is_listed_separately() -> None:
    """中级材料只能合成：初级材料 ×3 + 100 龙门币 → 2 次合成、6 次 1-7。"""
    payload = {
        "costs": {"elite": {"精1": [{"name": "中级材料", "amount": 2}]}, "skill_levels": {},
                  "modules": []},
        "modules": [],
        "unavailable": [],
    }
    h = harness(
        payloads={"甲": payload},
        rates={"初级材料": [stage_row("main_01-07", "1-7", "初级材料", times=1000,
                                      quantity=1000, ap_cost=6)]},
        pages={"中级材料": item_page("中级材料", recipe=craft(
            "中级材料", [("初级材料", 3)], lmd=100))},
    )

    plan = await h.planner.plan([UpgradeTarget("甲", elite=1)], box=h.box)

    assert plan.requirements == {"中级材料": 2}
    assert [stage["stage_id"] for stage in plan.stages] == ["main_01-07"]
    assert plan.stages[0]["runs"] == 6
    assert plan.total_sanity == 36
    assert len(plan.synthesis) == 1
    synthesis = plan.synthesis[0]
    assert synthesis["product"] == "中级材料"
    assert synthesis["count"] == 2
    assert synthesis["materials"] == [{"name": "初级材料", "amount": 3}]
    assert synthesis["total_materials"] == [{"name": "初级材料", "amount": 6}]
    assert synthesis["lmd_cost"] == 100
    assert synthesis["lmd_total"] == 200
    assert plan.synthesis_lmd == 200
    assert plan.feasible is True
    assert plan.surplus == {}


@async_test
async def test_small_sample_stage_is_not_a_candidate() -> None:
    """样本 99 的关卡不进候选（阈值 100）；样本 100 的进。"""
    h = harness(
        rates={
            TARGET: [
                stage_row("main_01-07", "1-7", TARGET, times=99, quantity=50, ap_cost=6),
                stage_row("main_04-04", "4-4", TARGET, times=100, quantity=100, ap_cost=18),
            ]
        }
    )

    plan = await h.planner.plan([UpgradeTarget("甲", elite=1)], box=h.box)

    assert [stage["stage_id"] for stage in plan.stages] == ["main_04-04"]
    assert plan.total_sanity == 180
    assert h.drops.calls[0]["include_closed"] is False
    assert h.drops.calls[0]["limit"] == 8


@async_test
async def test_closed_stage_is_dropped() -> None:
    """便宜但已关闭的关卡剔除，改用开放的那关。"""
    h = harness(
        rates={
            TARGET: [
                stage_row("main_01-07", "1-7", TARGET, times=1000, quantity=1000,
                          ap_cost=6, open_=False),
                stage_row("main_04-04", "4-4", TARGET, times=1000, quantity=1000, ap_cost=18),
            ]
        }
    )

    plan = await h.planner.plan([UpgradeTarget("甲", elite=1)], box=h.box)

    assert [stage["stage_id"] for stage in plan.stages] == ["main_04-04"]
    assert plan.total_sanity == 180


@async_test
async def test_unnavigable_stage_is_dropped() -> None:
    """当期活动里有导航文件的关可用；索引里没有的关剔除。"""
    index = FakeIndex(
        matches={
            "act99side_01": [
                {"stage_id": "act99side_01", "code": "XX-1", "activity": "act99side",
                 "is_current": True, "level_id": "activities/act99side/level_act99side_01"}
            ]
        },
        snapshot=SimpleNamespace(stage_id_prefix="act99side", navigation="available"),
    )
    h = harness(
        rates={
            TARGET: [
                stage_row("act99side_01", "XX-1", TARGET, times=500, quantity=500, ap_cost=12),
                stage_row("act99side_09", "XX-9", TARGET, times=500, quantity=500, ap_cost=6),
            ]
        },
        index=index,
    )

    plan = await h.planner.plan([UpgradeTarget("甲", elite=1)], box=h.box)

    assert [stage["stage_id"] for stage in plan.stages] == ["act99side_01"]
    assert plan.total_sanity == 120
    assert plan.feasible is True


@async_test
async def test_missing_local_stage_index_skips_navigability_filter(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """本机 stages.json 与活动索引都读不到时不过滤，并在 notes 里说明。"""
    monkeypatch.setattr(planner, "permanent_stage_ids", lambda: frozenset())
    h = harness(
        rates={TARGET: [stage_row("long_01-01", "LG-1", TARGET, times=500,
                                  quantity=500, ap_cost=6)]},
        index=FakeIndex(fail=True),
    )

    plan = await h.planner.plan([UpgradeTarget("甲", elite=1)], box=h.box)

    assert [stage["stage_id"] for stage in plan.stages] == ["long_01-01"]
    assert plan.feasible is True
    assert any("导航" in note and "未做" in note for note in plan.notes)


# --------------------------------------------------------------------------- #
# 配方闭包
# --------------------------------------------------------------------------- #


@async_test
async def test_recipe_closure_is_recursive() -> None:
    """终极 ← 高级 ×2 ← 中级 ×2 ← D32钢 ×2，D32钢 可刷：一次链式展开。"""
    payload = {
        "costs": {"elite": {"精1": [{"name": "终极材料", "amount": 1}]}, "skill_levels": {},
                  "modules": []},
        "modules": [],
        "unavailable": [],
    }
    h = harness(
        payloads={"甲": payload},
        rates={"D32钢": [stage_row("main_01-07", "1-7", "D32钢", times=1000,
                                   quantity=1000, ap_cost=6)]},
        pages={
            "终极材料": item_page("终极材料", recipe=craft("终极材料", [("高级材料", 2)])),
            "高级材料": item_page("高级材料", recipe=craft("高级材料", [("中级材料", 2)])),
            "中级材料": item_page("中级材料", recipe=craft("中级材料", [("D32钢", 2)])),
            "D32钢": item_page("D32钢"),
        },
    )

    plan = await h.planner.plan([UpgradeTarget("甲", elite=1)], box=h.box)

    requested = {title for call in h.prts.calls for title in call}
    assert requested == {"终极材料", "高级材料", "中级材料", "D32钢"}
    assert all(len(call) <= 20 for call in h.prts.calls)
    counts = {row["product"]: row["count"] for row in plan.synthesis}
    assert counts == {"终极材料": 1, "高级材料": 2, "中级材料": 4}
    middle = next(row for row in plan.synthesis if row["product"] == "中级材料")
    # 闭包里的材料名按 PRTS 原样留一份，不出现归一化后的小写名
    assert middle["materials"] == [{"name": "D32钢", "amount": 2}]
    assert [stage["stage_id"] for stage in plan.stages] == ["main_01-07"]
    assert plan.stages[0]["runs"] == 8
    assert plan.stages[0]["items"][0]["item_name"] == "D32钢"
    assert plan.total_sanity == 48
    assert plan.feasible is True


@async_test
async def test_recipe_cycle_terminates_and_is_unobtainable() -> None:
    """互相依赖的配方（甲 ← 乙 ← 甲）不会死循环，也不会凭空产出。"""
    payload = {
        "costs": {"elite": {"精1": [{"name": "循环甲", "amount": 1}]}, "skill_levels": {},
                  "modules": []},
        "modules": [],
        "unavailable": [],
    }
    h = harness(
        payloads={"甲": payload},
        rates={},
        pages={
            "循环甲": item_page("循环甲", recipe=craft("循环甲", [("循环乙", 1)])),
            "循环乙": item_page("循环乙", recipe=craft("循环乙", [("循环甲", 1)])),
        },
    )

    plan = await h.planner.plan([UpgradeTarget("甲", elite=1)], box=h.box)

    assert plan.feasible is False
    assert plan.synthesis == ()
    assert plan.stages == ()
    assert [row["name"] for row in plan.unobtainable] == ["循环甲"]
    assert len(h.prts.calls) <= 3  # 每个材料最多取一次


@async_test
async def test_recipe_with_mismatched_product_is_ignored() -> None:
    """页面写的产物与页面名不符时保守丢弃该配方（防方向取反）。"""
    payload = {
        "costs": {"elite": {"精1": [{"name": "甲材料", "amount": 1}]}, "skill_levels": {},
                  "modules": []},
        "modules": [],
        "unavailable": [],
    }
    h = harness(
        payloads={"甲": payload},
        rates={},
        pages={"甲材料": item_page("甲材料", recipe=craft("乙材料", [("丙材料", 1)]))},
    )

    plan = await h.planner.plan([UpgradeTarget("甲", elite=1)], box=h.box)

    assert plan.synthesis == ()
    assert [row["name"] for row in plan.unobtainable] == ["甲材料"]


# --------------------------------------------------------------------------- #
# 不可获得的材料与降级
# --------------------------------------------------------------------------- #


@async_test
async def test_unobtainable_material_is_reported_with_hint() -> None:
    """模组数据块刷不到：进 unobtainable 并附获取提示，其余材料照常规划。"""
    payload = {
        "costs": {
            "elite": {"精1": [{"name": TARGET, "amount": 10}, {"name": BLOCK, "amount": 4}]},
            "skill_levels": {},
            "modules": [],
        },
        "modules": [],
        "unavailable": [],
    }
    h = harness(
        payloads={"甲": payload},
        rates={TARGET: [stage_row("main_01-07", "1-7", TARGET, times=1000,
                                  quantity=1000, ap_cost=6)]},
        pages={BLOCK: item_page(BLOCK, obtain="每周任务与限时活动商店")},
    )

    plan = await h.planner.plan([UpgradeTarget("甲", elite=1)], box=h.box)

    assert plan.feasible is False
    assert plan.requirements == {TARGET: 10, BLOCK: 4}
    assert [stage["stage_id"] for stage in plan.stages] == ["main_01-07"]
    assert plan.total_sanity == 60
    assert len(plan.unobtainable) == 1
    missing = plan.unobtainable[0]
    assert missing["name"] == BLOCK
    assert missing["amount"] == 4
    assert missing["missing"] == 4
    assert missing["have"] == 0
    assert "掉率" in missing["reason"]
    assert missing["hint"] == "每周任务与限时活动商店"
    assert any("无法" in note or "缺口" in note for note in plan.notes)


@async_test
async def test_material_without_any_candidate_stage_gets_static_hint() -> None:
    """掉率表里全是样本不足/关闭的关：算「关卡打不到」，用内置提示兜底。"""
    payload = {
        "costs": {"elite": {"精1": [{"name": BLOCK, "amount": 2}]}, "skill_levels": {},
                  "modules": []},
        "modules": [],
        "unavailable": [],
    }
    h = harness(
        payloads={"甲": payload},
        rates={BLOCK: [stage_row("main_01-07", "1-7", BLOCK, times=10, quantity=5, ap_cost=6)]},
    )

    plan = await h.planner.plan([UpgradeTarget("甲", elite=1)], box=h.box)

    assert plan.feasible is False
    assert plan.unobtainable[0]["hint"]
    assert "候选" in plan.unobtainable[0]["reason"] or "样本" in plan.unobtainable[0]["reason"]


@async_test
async def test_solver_failure_degrades_without_raising(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """scipy 不可用（或求解失败）时不抛异常：给需求 + 说明。"""
    def boom(**kwargs: Any) -> Any:
        raise ImportError("没有 scipy")

    monkeypatch.setattr(planner, "_linprog_call", boom)
    h = harness(
        rates={TARGET: [stage_row("main_01-07", "1-7", TARGET, times=1000,
                                  quantity=1000, ap_cost=6)]}
    )

    plan = await h.planner.plan([UpgradeTarget("甲", elite=1)], box=h.box)

    assert plan.feasible is False
    assert plan.stages == ()
    assert plan.total_sanity == 0
    assert plan.requirements == {TARGET: 10, "龙门币": 30000}
    assert any("线性规划" in note for note in plan.notes)


@async_test
async def test_costs_unavailable_degrades_for_that_target() -> None:
    """一个目标的材料表取不到：只有它降级，另一个照常算。"""
    h = harness(
        payloads={"甲": BASE_PAYLOAD},
        rates={TARGET: [stage_row("main_01-07", "1-7", TARGET, times=1000,
                                  quantity=1000, ap_cost=6)]},
    )

    plan = await h.planner.plan(
        [UpgradeTarget("甲", elite=1), UpgradeTarget("乙", elite=1)], box=h.box
    )

    assert plan.requirements == {TARGET: 10, "龙门币": 30000}
    assert plan.targets[1]["operator"] == "乙"
    assert plan.targets[1]["warnings"]
    assert plan.targets[0]["warnings"] == []
    assert any("乙" in note for note in plan.notes)


# --------------------------------------------------------------------------- #
# 当前状态与库存
# --------------------------------------------------------------------------- #


@async_test
async def test_no_box_record_assumes_fresh_operator() -> None:
    """没有干员记录时按「刚获得」算，并标 assumed_current。"""
    h = harness(
        rates={TARGET: [stage_row("main_01-07", "1-7", TARGET, times=1000,
                                  quantity=1000, ap_cost=6)]}
    )

    plan = await h.planner.plan([UpgradeTarget("甲", elite=1)], box=h.box)

    row = plan.targets[0]
    assert row["assumed_current"] is True
    assert row["current"]["elite"] == 0
    assert row["current"]["level"] == 1
    assert row["current"]["skill_level"] == 1
    assert row["requirements"] == {TARGET: 10, "龙门币": 30000}
    assert any("刚获得" in note for note in plan.notes)


@async_test
async def test_box_record_without_ownership_is_assumed_fresh() -> None:
    """记录说没拥有：按刚获得算，并说明原因。"""
    box = FakeBox([box_row("甲", own=False)])
    h = harness(
        rates={TARGET: [stage_row("main_01-07", "1-7", TARGET, times=1000,
                                  quantity=1000, ap_cost=6)]},
        box=box,
    )

    plan = await h.planner.plan([UpgradeTarget("甲", elite=1)], box=box)

    assert plan.targets[0]["assumed_current"] is True
    assert any("拥有" in warning for warning in plan.targets[0]["warnings"])
    assert plan.requirements == {TARGET: 10, "龙门币": 30000}


@async_test
async def test_box_record_operators_key_is_accepted() -> None:
    """账号数据行用 ``operators`` 键返回时同样认。"""
    box = FakeBox([box_row("甲", elite=1)], row_key="operators")
    h = harness(
        rates={TARGET: [stage_row("main_01-07", "1-7", TARGET, times=1000,
                                  quantity=1000, ap_cost=6)]},
        box=box,
    )

    plan = await h.planner.plan([UpgradeTarget("甲", elite=2)], box=box)

    assert plan.targets[0]["assumed_current"] is False
    # M29：精一 1 级 → 精二满级 90 的升级经验与龙门币也进需求
    # （6★：1,245,000 经验 / 1,192,333 升级龙门币 + 精二 180,000 龙门币）。
    assert plan.requirements == {
        FINE: 4,
        "作战记录": 1_245_000,
        "龙门币": 180_000 + 1_192_333,
    }


@async_test
async def test_box_record_makes_targets_incremental() -> None:
    """精一 60 级、技能 7、专精一 1、X 模组 1 → 只补剩下的部分。"""
    box = FakeBox([
        box_row("甲", elite=1, level=60, potential=2, skill_level=7,
                masteries={"1": 1, "2": 0, "3": 0}, modules={"X": 1})
    ])
    h = harness(default_rates=True, box=box)

    plan = await h.planner.plan(
        [UpgradeTarget("甲", elite=2, masteries={1: 3}, modules=(("X", 3),))], box=box
    )

    assert plan.targets[0]["assumed_current"] is False
    assert plan.requirements == {
        FINE: 4,
        "专精料": 27,  # 一9 + 一10
        BLOCK: 8,  # 模组 2、3 级
        # 精一 60 级 → 精二满级 90 的升级经验与龙门币（M29）
        "作战记录": 1_018_114,
        "龙门币": 180000 + 100000 + 120000 + 1_020_765,
    }


@async_test
async def test_masteries_without_prerequisites_add_them_implicitly() -> None:
    """只写「专精一」：按刚获得算，隐含补上精英二与技能 7 级，并标 implied。"""
    payload = {
        "costs": {
            "elite": BASE_COSTS["elite"],
            "skill_levels": BASE_COSTS["skill_levels"],
            "modules": [],
        },
        "modules": [],
        "unavailable": [],
    }
    h = harness(payloads={"甲": payload}, default_rates=True)

    plan = await h.planner.plan([UpgradeTarget("甲", masteries={1: 1})], box=h.box)

    row = plan.targets[0]
    assert row["assumed_current"] is True
    assert set(row["implied"]) == {"elite", "skill_level"}
    assert row["target"]["elite"] == 2
    assert row["target"]["skill_level"] == 7
    assert plan.requirements == {
        TARGET: 10,
        FINE: 4,
        "技巧概要·卷1": 10,
        "技巧概要·卷2": 24,
        "技巧概要·卷3": 8,
        "基础料": 22,
        "专精料": 6,
        "龙门币": 210000,
    }


@async_test
async def test_explicit_target_below_prerequisites_does_not_exceed_it() -> None:
    """显式写精英一 + 专精：不越过用户给的精英化，只给冲突说明。"""
    payload = {
        "costs": {"elite": BASE_COSTS["elite"], "skill_levels": BASE_COSTS["skill_levels"],
                  "modules": []},
        "modules": [],
        "unavailable": [],
    }
    h = harness(payloads={"甲": payload}, default_rates=True)

    plan = await h.planner.plan(
        [UpgradeTarget("甲", elite=1, masteries={1: 1})], box=h.box
    )

    row = plan.targets[0]
    assert row["implied"] == []
    assert plan.requirements == {TARGET: 10, "龙门币": 30000}
    assert any("专精" in warning for warning in row["warnings"])


@async_test
async def test_use_depot_deducts_inventory() -> None:
    """use_depot=True 时库存抵扣；False 时按全量算。"""
    rates = {TARGET: [stage_row("main_01-07", "1-7", TARGET, times=1000,
                                quantity=500, ap_cost=6)]}  # rate 0.5
    depot = FakeDepot({TARGET: 4})
    box = FakeBox(depot=depot)
    h = harness(rates=rates, box=box)

    with_depot = await h.planner.plan([UpgradeTarget("甲", elite=1)], box=box)
    without_depot = await h.planner.plan(
        [UpgradeTarget("甲", elite=1)], use_depot=False, box=box
    )

    assert with_depot.stages[0]["runs"] == 12
    assert with_depot.total_sanity == 72
    assert with_depot.requirements == {TARGET: 10, "龙门币": 30000}
    assert without_depot.stages[0]["runs"] == 20
    assert without_depot.total_sanity == 120
    assert depot.calls  # 库存被读过


@async_test
async def test_depot_is_paged() -> None:
    """仓库一页装不下时分页读完（假服务每页最多 2 条）。"""
    depot = FakeDepot({TARGET: 4, FINE: 1, "别的料": 9}, page_cap=2)
    box = FakeBox(depot=depot)
    h = harness(
        rates={TARGET: [stage_row("main_01-07", "1-7", TARGET, times=1000,
                                  quantity=500, ap_cost=6)]},
        box=box,
    )

    plan = await h.planner.plan([UpgradeTarget("甲", elite=1)], box=box)

    assert len(depot.calls) >= 2
    assert plan.stages[0]["runs"] == 12
    assert plan.surplus == {}
    assert "别的料" not in plan.surplus


@async_test
async def test_depot_covered_material_is_not_unobtainable() -> None:
    """刷不到但库存够的材料不算缺口。"""
    payload = {
        "costs": {"elite": {"精1": [{"name": BLOCK, "amount": 3}]}, "skill_levels": {},
                  "modules": []},
        "modules": [],
        "unavailable": [],
    }
    box = FakeBox(depot=FakeDepot({BLOCK: 5}))
    h = harness(payloads={"甲": payload}, box=box)

    plan = await h.planner.plan([UpgradeTarget("甲", elite=1)], box=box)

    assert plan.feasible is True
    assert plan.unobtainable == ()
    assert plan.stages == ()
    assert plan.total_sanity == 0
    assert plan.surplus[BLOCK] == 2


@async_test
async def test_depot_service_can_be_injected_directly() -> None:
    """``depot`` 构造参数直接注入 ``DepotService``（工具层更方便的路径）。"""
    depot = FakeDepot({TARGET: 4})
    h = harness(
        rates={TARGET: [stage_row("main_01-07", "1-7", TARGET, times=1000,
                                  quantity=500, ap_cost=6)]},
        depot=depot,
    )

    plan = await h.planner.plan([UpgradeTarget("甲", elite=1)], box=h.box)

    assert plan.stages[0]["runs"] == 12
    assert depot.calls


@async_test
async def test_missing_depot_data_is_noted() -> None:
    """use_depot=True 但没有仓库数据：库存按 0 算并在 notes 说明。"""
    h = harness(
        rates={TARGET: [stage_row("main_01-07", "1-7", TARGET, times=1000,
                                  quantity=500, ap_cost=6)]}
    )

    plan = await h.planner.plan([UpgradeTarget("甲", elite=1)], box=h.box)

    assert plan.stages[0]["runs"] == 20
    assert any("库存" in note for note in plan.notes)


# --------------------------------------------------------------------------- #
# 多目标与 wire 形状
# --------------------------------------------------------------------------- #


@async_test
async def test_requirements_are_summed_across_targets() -> None:
    h = harness(
        rates={TARGET: [stage_row("main_01-07", "1-7", TARGET, times=1000,
                                  quantity=1000, ap_cost=6)]}
    )

    plan = await h.planner.plan(
        [UpgradeTarget("甲", elite=1), UpgradeTarget("甲", elite=1)], box=h.box
    )

    assert plan.requirements[TARGET] == 20
    assert len(plan.targets) == 2


@async_test
async def test_as_dict_is_json_serializable() -> None:
    h = harness(
        rates={TARGET: [stage_row("main_01-07", "1-7", TARGET, times=1000,
                                  quantity=1000, ap_cost=6)]}
    )

    plan = await h.planner.plan([UpgradeTarget("甲", elite=1)], box=h.box)
    payload = plan.as_dict()

    assert set(payload) >= {
        "targets", "requirements", "stages", "synthesis", "total_sanity",
        "days_estimated", "surplus", "unobtainable", "notes",
    }
    assert payload["server"] == "CN"
    assert payload["sanity_per_day"] == 240
    assert isinstance(payload["notes"], list)
    assert json.loads(json.dumps(payload, ensure_ascii=False))["total_sanity"] == 60


@async_test
async def test_target_from_dict_normalizes_tool_arguments() -> None:
    """工具参数 → UpgradeTarget：字符串键、列表形状都接受，越界值夹住。"""
    target = UpgradeTarget.from_dict(
        {
            "operator": "甲",
            "elite": 2,
            "skill_level": 7,
            "masteries": {"1": 3, "2": 0},
            "modules": [{"type": "x", "level": 3}, {"type": "Y", "level": 9}],
        }
    )

    assert target.operator == "甲"
    assert dict(target.masteries or {}) == {1: 3, 2: 0}
    assert target.modules == (("X", 3), ("Y", 3))


@async_test
async def test_server_is_passed_through_to_drop_service() -> None:
    h = harness(
        rates={TARGET: [stage_row("main_01-07", "1-7", TARGET, times=1000,
                                  quantity=1000, ap_cost=6)]}
    )

    plan = await h.planner.plan([UpgradeTarget("甲", elite=1)], box=h.box, server="US")

    assert h.drops.calls[0]["server"] == "US"
    assert plan.server == "US"


@async_test
async def test_unspecified_target_fields_mean_no_change() -> None:
    """没写的字段表示「不改」：精一的干员只写 operator 时不产生需求。"""
    box = FakeBox([box_row("甲", elite=1)])
    h = harness(default_rates=True, box=box)

    plan = await h.planner.plan([UpgradeTarget("甲")], box=box)

    # 没写精英/等级 = 不改精英阶段：材料需求为空（`plan.stages` 也为空）。M29 起升级经验与
    # 龙门币按「当前精英阶段的满级」算，所以精一 1 级仍有可升的级（精一满级 80）。
    assert plan.requirements == {"作战记录": 495_000, "龙门币": 447_378}
    assert plan.stages == ()
    assert plan.feasible is True


# --------------------------------------------------------------------------- #
# 工具层适配（Agent 工具与养成计划调的入口）
# --------------------------------------------------------------------------- #


@async_test
async def test_as_dict_plan_uses_injected_account_box() -> None:
    """``as_dict_plan`` 不给 box 时用装配时注入的账号数据句柄（工具层的调用形状）。"""
    box = FakeBox([box_row("甲", elite=1)])
    h = harness(
        rates={FINE: [stage_row("main_01-07", "1-7", FINE, times=1000,
                                quantity=1000, ap_cost=6)]},
        box=box,
    )

    payload = await h.planner.as_dict_plan(
        [{"operator": "甲", "elite": 2}], use_depot=True
    )

    assert payload["targets"][0]["assumed_current"] is False
    assert payload["requirements"][FINE] == 4
    assert payload["stages"][0]["runs"] == 4
    assert json.loads(json.dumps(payload, ensure_ascii=False))["total_sanity"] == 24


@async_test
async def test_default_upgrade_planner_is_a_shared_planner() -> None:
    """``default_upgrade_planner()`` 是进程内单例，且带上了账号数据句柄。"""
    first = planner.default_upgrade_planner()
    second = planner.default_upgrade_planner()

    assert isinstance(first, UpgradePlanner)
    assert first is second
    assert first._account_box is not None  # noqa: SLF001 - 只验证装配到位
    assert first._depot is not None  # noqa: SLF001


# --------------------------------------------------------------------------- #
# M29：升级经验与龙门币（规格 §7.6 第 10 条、§9.1 第 10 条）
#
# 既有用例不注入 ``levelup``，规划器于是惰性加载「当前可用的表」；为了让本组用例不依赖
# 真实拆包数据，这里显式注入 `LevelUpTable.builtin()`（内置的官方数值表）。
# --------------------------------------------------------------------------- #


def levelup_planner(*args: Any, **kwargs: Any) -> UpgradePlanner:
    """照 :func:`harness` 装配，但注入内置的升级经验/龙门币表。"""
    from maa_api.services.game.levelup import LevelUpTable

    return UpgradePlanner(*args, levelup=LevelUpTable.builtin(), **kwargs)


@async_test
async def test_levelup_costs_are_added_to_requirements() -> None:
    """升级经验折算成「作战记录」、升级龙门币进 requirements；M27 的「未计入」说明去掉。

    6★ 从精零 1 级练到精一满级 80：内置的官方数值表给出 645,761 经验 / 551,691 龙门币
    （含精零 1→50 与精一 1→80 两段），加上材料表里的精一龙门币 30,000 与 10 个目标料。
    """
    box = FakeBox([box_row("甲", rarity=6, elite=0, level=1)])
    h = harness(rates={TARGET: [stage_row("main_01-07", "1-7", TARGET, times=1000,
                                          quantity=1000, ap_cost=6)]}, box=box)
    h.planner = levelup_planner(
        operators=h.operators, drops=h.drops, items=FakeItems(h.prts),
        index=h.index, box=box, settings_provider=_settings,
    )

    plan = await h.planner.plan([UpgradeTarget("甲", elite=1)], box=box)

    assert plan.requirements[TARGET] == 10
    assert plan.requirements["作战记录"] == 645_761
    assert plan.requirements["龙门币"] == 30_000 + 551_691
    # 只报需求、不参与刷关规划（仓库识别不含这两项，规格 §7.3）。
    assert "作战记录" not in {row["item"] for row in h.drops.calls}
    assert not any("未计入升级所需的经验与龙门币" in note for note in plan.notes)
    assert any("升级经验与龙门币已计入需求" in note for note in plan.notes)
    row = plan.targets[0]
    assert row["warnings"] == []
    assert row["target"]["elite"] == 1


@async_test
async def test_levelup_costs_use_the_target_elite_cap() -> None:
    """目标等级按**目标精英阶段**的满级算（精一是 80，不是当前阶段的 50）。"""
    box = FakeBox([box_row("甲", rarity=6, elite=0, level=50)])
    h = harness(rates={}, box=box)
    h.planner = levelup_planner(
        operators=h.operators, drops=h.drops, items=FakeItems(h.prts),
        index=h.index, box=box, settings_provider=_settings,
    )

    plan = await h.planner.plan([UpgradeTarget("甲", elite=1)], box=box)

    # 精零已经满级：经验全部来自精一 1→80（495,000），龙门币 447,378 + 精一 30,000。
    assert plan.requirements["作战记录"] == 495_000
    assert plan.requirements["龙门币"] == 30_000 + 447_378


@async_test
async def test_levelup_notes_the_builtin_fallback_source() -> None:
    """用内置表兜底时 notes 写出来源（§3.5：禁止冒充）。"""
    box = FakeBox([box_row("甲", rarity=6, elite=0, level=1)])
    h = harness(rates={TARGET: [stage_row("main_01-07", "1-7", TARGET, times=1000,
                                          quantity=1000, ap_cost=6)]}, box=box)
    h.planner = levelup_planner(
        operators=h.operators, drops=h.drops, items=FakeItems(h.prts),
        index=h.index, box=box, settings_provider=_settings,
    )

    plan = await h.planner.plan([UpgradeTarget("甲", elite=1)], box=box)

    assert any("内置的官方数值表" in note for note in plan.notes)


@async_test
async def test_unavailable_levelup_table_degrades_per_target() -> None:
    """表不可用时那一段不计，并在 notes 与 warnings 里说明，其余照常。"""
    from maa_api.services.game.levelup import LevelUpTable

    box = FakeBox([box_row("甲", rarity=6, elite=0, level=1)])
    h = harness(rates={TARGET: [stage_row("main_01-07", "1-7", TARGET, times=1000,
                                          quantity=1000, ap_cost=6)]}, box=box)
    h.planner = levelup_planner(
        operators=h.operators, drops=h.drops, items=FakeItems(h.prts),
        index=h.index, box=box, settings_provider=_settings,
    )
    h.planner._levelup = LevelUpTable()  # noqa: SLF001 - 空表 = 不可用
    h.planner._levelup_loaded = False  # noqa: SLF001

    plan = await h.planner.plan([UpgradeTarget("甲", elite=1)], box=box)

    assert plan.requirements[TARGET] == 10
    assert "作战记录" not in plan.requirements
    assert any("未计入升级所需的经验与龙门币" in note for note in plan.notes)
    assert any("升级经验与龙门币表不可用" in note for note in plan.targets[0]["warnings"])


@async_test
async def test_levelup_is_not_counted_when_nothing_changes() -> None:
    """目标与当前一致（精零 50 级、不精一）时不产生升级需求。"""
    box = FakeBox([box_row("甲", rarity=6, elite=0, level=50)])
    h = harness(default_rates=True, box=box)
    h.planner = levelup_planner(
        operators=h.operators, drops=h.drops, items=FakeItems(h.prts),
        index=h.index, box=box, settings_provider=_settings,
    )

    plan = await h.planner.plan([UpgradeTarget("甲")], box=box)

    assert plan.requirements == {}
    assert not any("升级经验与龙门币已计入需求" in note for note in plan.notes)
    assert any("未计入升级所需的经验与龙门币" in note for note in plan.notes)


@async_test
async def test_levelup_uses_the_account_rarity_curve() -> None:
    """星级来自账号数据：5★ 精一满级是 70（6★ 是 80），与 6★ 共用一条曲线。"""
    five = FakeBox([box_row("甲", rarity=5, elite=0, level=1)])
    h = harness(default_rates=True, box=five)
    h.planner = levelup_planner(
        operators=h.operators, drops=h.drops, items=FakeItems(h.prts),
        index=h.index, box=five, settings_provider=_settings,
    )

    plan = await h.planner.plan([UpgradeTarget("甲", elite=1)], box=five)

    assert plan.requirements["作战记录"] == 484_561
    assert plan.requirements["龙门币"] == 20_000 + 390_075


@async_test
async def test_levelup_without_account_record_uses_the_lowest_rarity() -> None:
    """账号里没有记录（按刚获得算）时星级未知：按 1★ 兜底，宁少不多，并标明来源。"""
    h = harness(default_rates=True)
    h.planner = levelup_planner(
        operators=h.operators, drops=h.drops, items=FakeItems(h.prts),
        index=h.index, box=None, settings_provider=_settings,
    )

    plan = await h.planner.plan([UpgradeTarget("甲")], box=None)

    # 1★ 不知道星级时按 1★ 兜底：精零 1→30 的 9,800 经验 / 6,043 龙门币（不能精一）。
    assert plan.requirements["作战记录"] == 9_800
    assert plan.requirements["龙门币"] == 6_043
    assert any("内置的官方数值表" in note for note in plan.notes)
