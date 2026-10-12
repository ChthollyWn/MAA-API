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


def test_masteries_take_their_slots_from_the_response_not_from_rarity():
    """槽位数＝``len(skills)``：两技能干员只写两槽，第三个技能真的在就照写。

    这条刻意**不看稀有度**：W0 尚未确认 ``charInfoMap`` 的字段名，稀有度读不出来时按
    稀有度截槽会把 6★ 的第 3 个技能专精写成 0（＝「确认没有专精」）。
    """
    two_skills = [_char(charId="char_5", skills=[
        {"id": "a", "specializeLevel": 3},
        {"id": "b", "specializeLevel": 0},
    ])]
    updates, dropped = to_operator_updates(two_skills, catalog={"char_5": Meta(rarity=6)})
    (update,) = updates
    assert update.masteries == {"1": 3, "2": 0, "3": 0}
    assert dropped == 0

    three_skills = [_char(charId="char_6", skills=[
        {"id": "a", "specializeLevel": 3},
        {"id": "b", "specializeLevel": 0},
        {"id": "c", "specializeLevel": 2},
    ])]
    # 稀有度完全读不出来（没有 catalog、没有 charInfoMap）也要保住第 3 槽。
    updates, _ = to_operator_updates(three_skills)
    assert updates[0].masteries == {"1": 3, "2": 0, "3": 2}


def test_masteries_ignore_slots_beyond_three_and_count_them():
    updates, dropped = to_operator_updates(
        [_char(skills=[{"id": f"sk{i}", "specializeLevel": 1} for i in range(4)])]
    )
    assert updates[0].masteries == {"1": 1, "2": 1, "3": 1}
    # 第 4 个槽位在 masteries 与 skill_ids 两处各计一次（同一个越界条目被两条产线看到）。
    assert dropped == 2
    assert updates[0].skill_ids == ["sk0", "sk1", "sk2"]


# ---------------------------------------------------------------------------
# M43：技能 id 与模组明细（typeIcon → 类型字母 / 分支码 / 标签）
# ---------------------------------------------------------------------------
def test_skill_ids_follow_the_slot_order_and_truncate_at_three():
    updates, dropped = to_operator_updates(
        [_char(skills=[{"id": "skchr_amiya_2", "specializeLevel": 3}, {"id": "skchr_amiya_3", "specializeLevel": 0}])]
    )
    assert updates[0].skill_ids == ["skchr_amiya_2", "skchr_amiya_3"]
    assert dropped == 0

    # `skills` 不是列表 ⇒ 不知道（`None`），不是空列表。
    updates, _ = to_operator_updates([_char(skills="nope")])
    assert updates[0].skill_ids is None

    # 条目缺 id ⇒ 计一次 dropped，但已解析出来的槽位照留（缺 `specializeLevel` 的那条
    # 在 masteries 那条产线上也会各计一次，所以总数是 2）。
    updates, dropped = to_operator_updates([_char(skills=[{"specializeLevel": 1}, {"id": "sk1"}] )])
    assert updates[0].skill_ids == ["sk1"]
    assert dropped == 2


def test_module_details_carry_branch_label_and_type_icon():
    """真机实测的 `typeIcon`：`ccr-y` ⇒ 类型 Y、分支 ccr、标签 CCR-Y。"""
    chars = [
        _char(
            equip=[
                {"id": "uniequip_001_amiya", "level": 1, "locked": False},
                {"id": "uniequip_002_amiya", "level": 3, "locked": False},
                {"id": "uniequip_003_amiya", "level": 1, "locked": True},
            ]
        )
    ]
    map_ = {
        "uniequip_001_amiya": {"typeIcon": "original"},
        "uniequip_002_amiya": {"typeIcon": "ccr-y"},
        "uniequip_003_amiya": {"typeIcon": "ccr-x"},
    }
    updates, dropped = to_operator_updates(chars, equipment_info_map=map_)
    (update,) = updates
    # 证章（original）不进 modules 也不算 dropped；locked 的那条当没装。
    assert update.modules == {"Y": 3}
    assert update.module_details == [
        {
            "type": "Y",
            "level": 3,
            "equip_id": "uniequip_002_amiya",
            "branch": "ccr",
            "label": "CCR-Y",
            "type_icon": "ccr-y",
        }
    ]
    assert dropped == 0


def test_type_icon_variants_are_parsed_or_dropped_never_guessed():
    """六类写法：正常 / 大写 / 两位分支码 / 未知字母 / 证章 / 缺失。"""
    map_ = {
        "u_normal": {"typeIcon": "ccr-y"},
        "u_upper": {"typeIcon": "dec-X"},
        "u_short": {"typeIcon": "ra-a"},
        "u_unknown": {"typeIcon": "so-b"},
        "u_original": {"typeIcon": "original"},
        "u_missing": {},
    }
    chars = [
        _char(charId=f"char_{name}", equip=[{"id": name, "level": 2, "locked": False}])
        for name in map_
    ]
    updates, dropped = to_operator_updates(chars, equipment_info_map=map_)
    by_id = {update.char_id: update for update in updates}
    assert by_id["char_u_normal"].modules == {"Y": 2}
    assert by_id["char_u_normal"].module_details[0]["label"] == "CCR-Y"
    assert by_id["char_u_upper"].modules == {"X": 2}
    assert by_id["char_u_upper"].module_details[0]["label"] == "DEC-X"
    assert by_id["char_u_short"].modules == {"A": 2}
    assert by_id["char_u_short"].module_details[0]["branch"] == "ra"
    # 未知字母（B）与证章：不写类型；证章是正常状态不计 dropped，未知字母计一次。
    assert by_id["char_u_unknown"].modules is None
    assert by_id["char_u_original"].modules is None
    assert dropped == 2  # u_unknown + u_missing


def test_module_details_stay_unknown_when_nothing_resolves():
    """非空 `equip` 一个都解析不出 ⇒ 明细也是 `None`（不知道），不是空列表。"""
    updates, dropped = to_operator_updates(
        [_char(equip=[{"id": "uniequip_999_x", "level": 2, "locked": False}])],
        equipment_info_map={},
    )
    assert updates[0].modules is None
    assert updates[0].module_details is None
    assert dropped == 1

    # 空 `equip` 才是正面读数：`{}` + 空明细。
    updates, dropped = to_operator_updates([_char(equip=[])], equipment_info_map={})
    assert updates[0].modules == {}
    assert updates[0].module_details == []
    assert dropped == 0


def test_masteries_none_when_skills_is_not_a_list_but_empty_list_means_zero():
    updates, _ = to_operator_updates([_char(skills=None)])
    assert updates[0].masteries is None
    updates, _ = to_operator_updates([_char(skills=[])])
    assert updates[0].masteries == {"1": 0, "2": 0, "3": 0}


def test_unresolvable_modules_stay_unknown_instead_of_none_confirmed():
    """非空 ``equip`` 却一个类型都认不出 ⇒ ``None``（不知道），不是 ``{}``（确认没有）。"""
    chars = [_char(equip=[{"id": "uniequip_999_x", "level": 2, "locked": False}])]
    updates, dropped = to_operator_updates(chars, equipment_info_map={})
    (update,) = updates
    assert update.modules is None
    assert dropped == 1

    # 反面对照：空列表是正面读数，仍然写 ``{}``。
    updates, dropped = to_operator_updates([_char(equip=[])], equipment_info_map={})
    assert updates[0].modules == {}
    assert dropped == 0


def test_partially_resolved_modules_keep_what_is_known():
    chars = [
        _char(
            equip=[
                {"id": "uniequip_002_amiya", "level": 2, "locked": False},
                {"id": "uniequip_999_x", "level": 1, "locked": False},
            ]
        )
    ]
    updates, dropped = to_operator_updates(
        chars, equipment_info_map={"uniequip_002_amiya": {"type": "MOD_X"}}
    )
    assert updates[0].modules == {"X": 2}
    assert dropped == 1


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
    """兜底用 ``charInfoMap``，但它的 ``rarity`` 是 **0 基**：4 ⇒ 本机口径的 5。

    真机实测（2026-10-12）：阿米娅在 ``charInfoMap`` 里是 ``rarity: 4``，而本机
    ``battle_data.json`` 与库里存的是 5。兜底路径不加这一笔就会整体小 1 星。
    """
    updates, _ = to_operator_updates(
        [_char(charId="char_999_x")],
        char_info_map={"char_999_x": {"name": "新人", "rarity": 4, "profession": "狙击"}},
    )
    (update,) = updates
    assert (update.name, update.rarity, update.profession) == ("新人", 5, "狙击")

    # 0 基的 5 ⇒ 本机 6；越界的 6 在 0 基里不存在，直接当读不出（留 None）。
    updates, _ = to_operator_updates(
        [_char(charId="char_998_x")], char_info_map={"char_998_x": {"rarity": 5}}
    )
    assert updates[0].rarity == 6
    updates, _ = to_operator_updates(
        [_char(charId="char_997_x")], char_info_map={"char_997_x": {"rarity": 6}}
    )
    assert updates[0].rarity is None


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
