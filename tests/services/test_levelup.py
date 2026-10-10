"""升级经验与龙门币（规格 §7.6、§9.1 第 10 条）。

全部用 `tests/fixtures/gamedata/` 的常量表夹具，不打网。

三组断言：

1. **内置兜底 = 拆包数据**：内置表的 sha256 摘要必须与夹具里的 ``gamedata_const.json``
   逐字节一致（改表或抄错会立刻变红）；
2. **官方数值**：6★ 精二 1→90 级正好 750,000 经验 / 744,955 龙门币（社区通用的官方值），
   精一 1→80 级是 652,460 经验 / 557,325 龙门币 + 30,000 精一龙门币；
3. **边界**：目标低于当前、精英越界、缺表、作战记录折算。
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from maa_api.domain.errors import AppError, ErrorCode
from maa_api.services.game.levelup import (
    BUILTIN_EXP_ITEMS,
    MAX_LEVELS,
    SOURCE_BUILTIN,
    SOURCE_PACKED,
    LevelUpTable,
    levelup_cost,
    max_level_for,
)

FIXTURES = Path("tests/fixtures/gamedata")


def fixture(name: str):
    return json.loads((FIXTURES / name).read_text(encoding="utf-8"))


@pytest.fixture(scope="module")
def packed() -> LevelUpTable:
    return LevelUpTable.from_payloads(
        fixture("gamedata_const.json"), fixture("item_table.json")
    )


@pytest.fixture(scope="module")
def builtin() -> LevelUpTable:
    return LevelUpTable.builtin()


# --------------------------------------------------------------------------- #
# 内置表与拆包数据一致
# --------------------------------------------------------------------------- #


def test_builtin_tables_match_the_packed_data(packed: LevelUpTable, builtin: LevelUpTable) -> None:
    """内置的官方数值表与夹具里的拆包表逐字节一致。"""
    assert packed.table_digest() == builtin.table_digest()
    assert packed.exp_map == builtin.exp_map
    assert packed.upgrade_lmd_map == builtin.upgrade_lmd_map
    assert packed.elite_lmd == builtin.elite_lmd
    assert dict(packed.max_levels) == dict(builtin.max_levels)


def test_table_shapes_match_the_pack(packed: LevelUpTable) -> None:
    """结构：三条经验曲线各 90 项、龙门币曲线各 89 项、精英化 6 行、满级 6 行。"""
    assert [len(row) for row in packed.exp_map] == [90, 90, 90]
    assert [len(row) for row in packed.upgrade_lmd_map] == [89, 89, 89]
    assert [len(row) for row in packed.elite_lmd] == [2, 2, 2, 2, 2, 2]
    assert len(packed.max_levels) == 6
    assert packed.source == SOURCE_PACKED
    assert packed.available is True


def test_max_level_table_matches_the_character_table(packed: LevelUpTable) -> None:
    """各稀有度、各精英阶段的满级（1★/2★ 30、3★ 40/55、4★ 45/60/70、5★ 50/70/80、6★ 50/80/90）。"""
    assert {key: tuple(value) for key, value in packed.max_levels.items()} == {
        1: (30,),
        2: (30,),
        3: (40, 55),
        4: (45, 60, 70),
        5: (50, 70, 80),
        6: (50, 80, 90),
    }
    assert max_level_for(6, 2) == 90
    assert max_level_for(6, 3) is None
    assert max_level_for(9, 0) is None
    assert dict(MAX_LEVELS) == dict(packed.max_levels)


def test_exp_items_come_from_the_item_table(packed: LevelUpTable) -> None:
    """作战记录的折算值来自 ``item_table.json`` 的 ``expItems``。"""
    assert dict(packed.exp_items) == {"2001": 200, "2002": 400, "2003": 1000, "2004": 2000}
    assert dict(BUILTIN_EXP_ITEMS) == dict(packed.exp_items)


# --------------------------------------------------------------------------- #
# 官方数值
# --------------------------------------------------------------------------- #


def test_six_star_two_elite_level_90_is_the_official_total(packed: LevelUpTable) -> None:
    """6★ 精二 1→90：750,000 经验、744,955 龙门币（官方值，社区通用口径）。"""
    cost = packed.cost(rarity=6, from_elite=2, from_level=1, to_elite=2, to_level=90)
    assert cost["exp"] == 750_000
    assert cost["lmd"] == 744_955
    assert cost["elite_lmd"] == 0
    assert cost["source"] == SOURCE_PACKED
    assert cost["note"] is None


def test_six_star_full_path_includes_promotions(packed: LevelUpTable) -> None:
    """6★ 精零 1 级 → 精二 90 级：精零 1→50 + 精一 1→80 + 精二 1→90，精英化龙门币单列。"""
    cost = packed.cost(rarity=6, from_elite=0, from_level=1, to_elite=2, to_level=90)
    assert cost["exp"] == 1_395_761
    assert cost["lmd"] == 1_296_646
    assert cost["elite_lmd"] == 30_000 + 180_000
    # 与逐段算的结果一致（口径可交叉验算）：精零 1→50 + 精一 1→80 + 精二 1→90。
    # （精一那段本身就是 1→50 与 50→80 两段之和，所以这里不再单独加一次。）
    legs = [
        packed.cost(rarity=6, from_elite=0, from_level=1, to_elite=0, to_level=50),
        packed.cost(rarity=6, from_elite=1, from_level=1, to_elite=1, to_level=80),
        packed.cost(rarity=6, from_elite=2, from_level=1, to_elite=2, to_level=90),
    ]
    assert cost["exp"] == sum(leg["exp"] for leg in legs)
    assert cost["lmd"] == sum(leg["lmd"] for leg in legs)


def test_five_star_two_elite_level_80(packed: LevelUpTable) -> None:
    """5★ 精二 1→80：495,000 经验（5★/6★ 共用第三条曲线）。"""
    cost = packed.cost(rarity=5, from_elite=2, from_level=1, to_elite=2, to_level=80)
    assert cost["exp"] == 495_000
    assert cost["lmd"] == 447_378


def test_three_star_promotion_cost(packed: LevelUpTable) -> None:
    """3★ 精零 1 级 → 精一 55 级：走第三条曲线（与 5★/6★ 同一条），精一龙门币 10,000。"""
    cost = packed.cost(rarity=3, from_elite=0, from_level=1, to_elite=1, to_level=55)
    assert cost["exp"] == 279_022  # 精零 1→40（97,805）+ 精一 1→55（181,217）
    assert cost["elite_lmd"] == 10_000
    assert cost["lmd"] == 193_112


def test_one_star_has_only_one_elite_phase(packed: LevelUpTable) -> None:
    """1★/2★ 不能精一：``evolveGoldCost`` 是 -1，精英化龙门币按 0 算。"""
    two = packed.cost(rarity=2, from_elite=0, from_level=1, to_elite=0, to_level=30)
    assert two["exp"] == 24_592  # 2★ 走第 1 条曲线（1★ 是 9,800，两条不同）
    assert two["lmd"] == 14_784
    assert packed.promotion_lmd(1, 1) == 0
    assert packed.promotion_lmd(2, 2) == 0
    assert packed.promotion_lmd(6, 2) == 180_000
    cost = packed.cost(rarity=1, from_elite=0, from_level=1, to_elite=0, to_level=30)
    assert cost["exp"] == 9_800
    assert cost["lmd"] == 6_043


def test_rarity_curve_mapping_is_documented(packed: LevelUpTable) -> None:
    """三条曲线按 1★ / 2★ / 3★–6★ 取用（模块说明里的实现口径与不确定处）。"""
    first = packed.cost(rarity=1, from_elite=0, from_level=1, to_elite=0, to_level=2)
    assert first["exp"] == 100  # 第 0 条曲线的第 1 项
    two = packed.cost(rarity=2, from_elite=0, from_level=1, to_elite=0, to_level=2)
    assert two["exp"] == 120  # 第 1 条曲线
    for rarity in (3, 4, 5, 6):
        row = packed.cost(rarity=rarity, from_elite=0, from_level=1, to_elite=0, to_level=2)
        assert row["exp"] == 191, rarity  # 3★–6★ 共用第 2 条曲线
    assert packed.as_dict()["rarity_curve"] == {1: 0, 2: 1, 3: 2, 4: 2, 5: 2, 6: 2}
    # 三条曲线的有效区间与满级档位相容（能直接从数据里读出来的那部分）：
    # 第 0 条到 49 级、第 1 条到 79 级、第 2 条到 89 级。
    assert [max(index + 1 for index, value in enumerate(row) if value > 0)
            for row in packed.exp_map] == [49, 79, 89]


# --------------------------------------------------------------------------- #
# 边界与降级
# --------------------------------------------------------------------------- #


def test_backwards_and_out_of_range_are_not_guessed(packed: LevelUpTable) -> None:
    """目标低于当前、精英越界：返回全 0 并写 note，不猜。"""
    backwards = packed.cost(rarity=6, from_elite=2, from_level=90, to_elite=2, to_level=50)
    assert backwards["exp"] == 0 and backwards["lmd"] == 0
    assert "低于当前" in backwards["note"]

    out_of_range = packed.cost(rarity=6, from_elite=0, from_level=1, to_elite=3, to_level=90)
    assert out_of_range["exp"] == 0
    assert out_of_range["note"]


def test_unavailable_table_degrades(  ) -> None:
    """没有表时返回 ``unavailable`` 与 0，不抛异常。"""
    empty = LevelUpTable()
    cost = empty.cost(rarity=6, from_elite=0, from_level=1, to_elite=2, to_level=90)
    assert cost["exp"] == 0 and cost["lmd"] == 0
    assert cost["source"] == "unavailable"
    assert cost["note"]


def test_broken_payload_is_rejected() -> None:
    """结构不符抛 ``EXTERNAL_RESPONSE_INVALID``（调用方据此换内置表）。"""
    for payload in ([], {}, {"characterExpMap": []}, {"characterExpMap": [[1]], "characterUpgradeCostMap": []}):
        with pytest.raises(AppError) as info:
            LevelUpTable.from_payloads(payload)
        assert info.value.code is ErrorCode.EXTERNAL_RESPONSE_INVALID


def test_levelup_cost_module_entry_uses_builtin_by_default() -> None:
    """``levelup_cost`` 的缺省表是内置官方数值表（纯函数，不打网）。"""
    cost = levelup_cost(rarity=6, from_elite=2, from_level=1, to_elite=2, to_level=90)
    assert cost["exp"] == 750_000
    assert cost["lmd"] == 744_955
    assert cost["source"] == SOURCE_BUILTIN


def test_exp_cards_conversion(packed: LevelUpTable, builtin: LevelUpTable) -> None:
    """经验 → 作战记录张数：从高级到低级贪心，末位补一张最小的。"""
    assert packed.exp_cards(0) == []
    assert packed.exp_cards(2000) == [
        {"item_id": "2004", "name": "高级作战记录", "count": 1, "exp_each": 2000}
    ]
    rows = packed.exp_cards(750_000)
    assert sum(row["count"] * row["exp_each"] for row in rows) >= 750_000
    assert rows[0]["item_id"] == "2004"
    assert [row["exp_each"] for row in rows] == sorted(
        (row["exp_each"] for row in rows), reverse=True
    )
    # 没有 expItems 时给空表（不影响经验与龙门币数值）。
    assert LevelUpTable(exp_map=((1,),), upgrade_lmd_map=((1,),)).exp_cards(100) == []


def test_as_dict_exposes_source_and_digest(packed: LevelUpTable, builtin: LevelUpTable) -> None:
    """``as_dict`` 给规划器拼 notes 用：来源、可用性、曲线映射与摘要。"""
    payload = packed.as_dict()
    assert payload["source"] == SOURCE_PACKED
    assert payload["available"] is True
    assert set(payload["digest"]) == {"exp", "upgrade_lmd", "elite_lmd"}
    assert len(payload["digest"]["exp"]) == 12
    assert builtin.as_dict()["source"] == SOURCE_BUILTIN
    assert builtin.note
