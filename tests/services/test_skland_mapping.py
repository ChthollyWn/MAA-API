"""M42 §2.3：森空岛 ``chars[]`` → 练度更新的换算（纯函数，无网络无数据库）。

覆盖六件事：字段对位、``potentialRank`` 的 0 基换算、越界值丢弃计数、
``skills[]`` → 槽位（按稀有度截断）、``equip[]`` → 模组类型（``equipmentInfoMap``
解析 + 别名归一 + locked 跳过）、名称来源优先级（本机目录 > ``charInfoMap``）。
"""

from dataclasses import dataclass

import pytest

from maa_api.services.skland.mapping import (
    POTENTIAL_RANK_OFFSET,
    module_type_of,
    to_operator_updates,
)


@dataclass(frozen=True)
class Meta:
    name: str | None = None
    rarity: int | None = None
    profession: str | None = None
    name_en: str | None = None


def _char(**overrides):
    base = {
        "charId": "char_002_amiya",
        "level": 80,
        "evolvePhase": 2,
        "potentialRank": 5,
        "mainSkillLvl": 7,
        "favorPercent": 200,
        "skills": [{"id": "sk1", "specializeLevel": 3}],
        "equip": [],
    }
    base.update(overrides)
    return base


def test_amiya_maps_to_the_documented_wire_shape():
    updates, dropped = to_operator_updates(
        [_char()],
        catalog={"char_002_amiya": Meta(name="阿米娅", rarity=5, profession="术师")},
    )
    assert dropped == 0
    (update,) = updates
    assert update.char_id == "char_002_amiya"
    assert update.own is True
    assert (update.name, update.rarity, update.profession) == ("阿米娅", 5, "术师")
    assert (update.elite, update.level) == (2, 80)
    assert update.skill_level == 7
    assert update.favor_percent == 200


def test_potential_rank_is_zero_based():
    """``potentialRank`` 0–5 → ``potential`` 1–6（真机库 184 行是 6 ＝ 满潜）。"""
    assert POTENTIAL_RANK_OFFSET == 1
    updates, dropped = to_operator_updates(
        [_char(potentialRank=rank) for rank in (0, 3, 5)],
    )
    assert dropped == 0
    assert [u.potential for u in updates] == [1, 4, 6]


@pytest.mark.parametrize(
    "overrides",
    [
        {"level": 91},
        {"evolvePhase": 3},
        {"potentialRank": 6},
        {"mainSkillLvl": 0},
        {"favorPercent": 201},
    ],
)
def test_out_of_range_values_are_dropped_and_counted(overrides):
    """越界的单项留 ``None`` 并计一次 dropped——不夹取、不猜。"""
    updates, dropped = to_operator_updates([_char(**overrides)])
    (update,) = updates
    assert dropped == 1
    field = next(iter(overrides))
    mapping = {
        "level": update.level,
        "evolvePhase": update.elite,
        "potentialRank": update.potential,
        "mainSkillLvl": update.skill_level,
        "favorPercent": update.favor_percent,
    }
    assert mapping[field] is None


def test_masteries_fill_three_slots_and_truncate_by_rarity():
    """4–5★ 只有两个技能：第三槽归零（与 ``normalize_masteries`` 同口径）。"""
    chars = [
        _char(
            charId="char_5",
            skills=[
                {"id": "a", "specializeLevel": 3},
                {"id": "b", "specializeLevel": 0},
                {"id": "c", "specializeLevel": 2},
            ],
        )
    ]
    updates, dropped = to_operator_updates(chars, catalog={"char_5": Meta(rarity=5)})
    (update,) = updates
    assert update.masteries == {"1": 3, "2": 0, "3": 0}
    assert dropped == 0

    updates, _ = to_operator_updates(chars, catalog={"char_5": Meta(rarity=6)})
    assert updates[0].masteries == {"1": 3, "2": 0, "3": 2}


def test_masteries_none_when_skills_is_not_a_list_but_empty_list_means_zero():
    updates, _ = to_operator_updates([_char(skills=None)])
    assert updates[0].masteries is None
    updates, _ = to_operator_updates([_char(skills=[])])
    assert updates[0].masteries == {"1": 0, "2": 0, "3": 0}


def test_modules_come_from_equipment_info_map_and_skip_locked():
    equipment = {
        "uniequip_002_amiya": {"type": "MOD_X"},
        "uniequip_003_amiya": {"moduleType": "mod_y"},
    }
    chars = [
        _char(
            equip=[
                {"id": "uniequip_002_amiya", "level": 2, "locked": False},
                {"id": "uniequip_003_amiya", "level": 1, "locked": True},
                {"id": "uniequip_004_amiya", "level": 3, "locked": False},
            ]
        )
    ]
    updates, dropped = to_operator_updates(chars, equipment_info_map=equipment)
    (update,) = updates
    # 003 被 locked 跳过、004 类型未知计入 dropped；X=2 留下。
    assert update.modules == {"X": 2}
    assert dropped == 1


def test_empty_equip_list_means_confirmed_none():
    updates, dropped = to_operator_updates([_char(equip=[])])
    assert updates[0].modules == {}
    assert dropped == 0


@pytest.mark.parametrize(
    ("raw", "expected"),
    [
        ("MOD_X", "X"),
        ("mod_y", "Y"),
        ("UNIEQUIP_D", "D"),
        ("A", "A"),
        ("MOD_Z", None),
        (None, None),
    ],
)
def test_module_type_normalization(raw, expected):
    assert module_type_of("id", {"id": {"type": raw}}) == expected


def test_char_info_map_is_the_fallback_for_names_only():
    updates, _ = to_operator_updates(
        [_char(charId="char_999_x")],
        char_info_map={"char_999_x": {"name": "新人", "rarity": 6, "profession": "狙击"}},
    )
    (update,) = updates
    assert (update.name, update.rarity, update.profession) == ("新人", 6, "狙击")


def test_local_catalog_wins_over_char_info_map():
    updates, _ = to_operator_updates(
        [_char()],
        catalog={"char_002_amiya": Meta(name="阿米娅", rarity=5, profession="术师")},
        char_info_map={"char_002_amiya": {"name": "Amiya", "rarity": 6, "profession": "SNIPER"}},
    )
    (update,) = updates
    assert (update.name, update.rarity, update.profession) == ("阿米娅", 5, "术师")


def test_entries_without_char_id_are_skipped():
    updates, dropped = to_operator_updates([{"level": 1}, _char()])
    assert len(updates) == 1
    assert dropped == 1
