"""M26 Task 3：敌人页解析（规格 §6.4）的字段、级别差异与边界。

纯函数测试：不打网、不读仓库以外的文件。真实夹具是 ``tests/fixtures/prts/page_*.json``
（PRTS ``action=query&prop=revisions&rvslots=main`` 的裁剪快照，正文在
``query.pages[0].revisions[0].slots.main.content``）：

- ``page_originium_slug.json``（源石虫）：``{{敌人信息/common2}}`` + 两级 ``levelcontent``，
  级别 1 只有 ``最大生命值`` / ``攻击力`` 两个键；
- ``page_patriot.json``（爱国者）：级别 0 带 ``技能0..2``、``天赋`` 与十二种 ``XX抗性``，
  级别 1 只覆盖 ``技能2效果``。

规格口径（Task 3 实测，见 ``docs/superpowers/plans/2026-10-10-m26-game-entities.md``
「实测确认」表）：``merge_levels`` 的返回是**每级一个 dict**，键是 ``merge_levels``
里带 ``index``（int）、``技能``（list[dict]）与 ``天赋``（str）的中文键，级别 1 以上先用
级别 0 补齐（PRTS 只写差异）。
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import pytest

from maa_api.services.prts_parse.enemy import (
    EnemyLevel,
    EnemyPage,
    EnemySkill,
    merge_levels,
    parse_enemy_page,
)

FIXTURES = Path(__file__).resolve().parents[1] / "fixtures" / "prts"

#: 规格 §6.4 点名的十二种异常抗性。
RESISTANCE_KEYS = (
    "眩晕抗性",
    "沉默抗性",
    "沉睡抗性",
    "冻结抗性",
    "浮空抗性",
    "战栗抗性",
    "恐惧抗性",
    "麻痹抗性",
    "诱导抗性",
    "传送抗性",
    "缚地抗性",
)

#: 级别 0 的完整数值键（源石虫与爱国者都有）。
BASE_STAT_KEYS = (
    "最大生命值",
    "攻击力",
    "防御力",
    "法术抗性",
    "移动速度",
    "攻击速度",
    "攻击间隔",
    "生命恢复速度",
    "sp恢复速度",
    "重量等级",
    "损伤抵抗",
    "元素抗性",
    "基础嘲讽等级",
)


def fixture(name: str) -> dict[str, Any]:
    return json.loads((FIXTURES / name).read_text(encoding="utf-8"))


def wikitext(name: str) -> str:
    """夹具里 ``query.pages[0]`` 的正文。"""
    return fixture(name)["query"]["pages"][0]["revisions"][0]["slots"]["main"]["content"]


def fixture_title(name: str) -> str:
    """夹具里 ``query.pages[0]`` 的标题。"""
    return fixture(name)["query"]["pages"][0]["title"]


@pytest.fixture(scope="module")
def slug() -> EnemyPage:
    """源石虫（两级、字段少）。"""
    name = "page_originium_slug.json"
    return parse_enemy_page(wikitext(name), title=fixture_title(name))


@pytest.fixture(scope="module")
def patriot() -> EnemyPage:
    """爱国者（两级、技能与天赋多）。"""
    name = "page_patriot.json"
    return parse_enemy_page(wikitext(name), title=fixture_title(name))


def merged_by_index(page: EnemyPage) -> dict[int, dict[str, Any]]:
    return {level["index"]: level for level in merge_levels(page)}


def skills_by_slot(level: dict[str, Any]) -> dict[int, dict[str, Any]]:
    return {skill["slot"]: skill for skill in level["技能"]}


# -- 基础字段 -------------------------------------------------------------------


def test_originium_slug_basic_fields(slug: EnemyPage) -> None:
    assert slug.title == "源石虫"
    assert slug.enemy_id == "1"
    assert slug.name == "源石虫"
    assert slug.index == "B1"
    assert slug.rank == "普通"
    assert slug.category == "感染生物"
    assert slug.damage_type == "物理"
    assert slug.attack_type == "近战"
    assert slug.move_type == "地面"
    assert slug.debut == "初始"
    assert slug.description is not None and "野生的被感染生物" in slug.description
    assert slug.abilities is None  # 源石虫没有 `能力` 键


def test_patriot_basic_fields(patriot: EnemyPage) -> None:
    assert patriot.title == "爱国者"
    assert patriot.enemy_id == "1139"
    assert patriot.name == "爱国者"
    assert patriot.index == "PT"
    assert patriot.rank == "领袖"
    assert patriot.category == "萨卡兹"
    assert patriot.damage_type == "物理"
    assert patriot.attack_type == "近战"
    assert patriot.move_type == "地面"
    assert patriot.debut == "主线第七章"
    assert patriot.description is not None and "温迪戈" not in patriot.description
    assert patriot.description is not None and "年久失修的铠甲" in patriot.description


def test_clean_description_drops_display_templates(slug: EnemyPage, patriot: EnemyPage) -> None:
    """`描述` 取 `{{敌人信息/common2}}` 的那条并清洗（级别里的 `描述` 留在 fields 里）。"""
    assert patriot.description is not None
    assert "{{" not in patriot.description
    assert patriot.description.startswith("年久失修的铠甲多处破损")
    # 级别 0 的 `描述` 带 `{{color}}`，原样留在 fields 里（未清洗）
    assert "{{color" in patriot.levels[0].fields["描述"]
    # 源石虫的 common2 描述与级别 0 的短描述不同，取的是 common2 的那份
    assert slug.description is not None
    assert "集群攻击" in slug.description


def test_abilities_keeps_line_breaks(patriot: EnemyPage) -> None:
    """`能力` 逐条列出：`<br>` 与 `{{color}}` 都清掉，换行保留。"""
    assert patriot.abilities is not None
    assert "{{" not in patriot.abilities
    assert "<br>" not in patriot.abilities
    lines = patriot.abilities.split("\n")
    assert lines[0] == "·大幅强化敌军攻击与防御力"
    assert lines[1] == "行军姿态"
    assert lines[-1] == "·免疫眩晕，免疫冻结，免疫浮空"
    assert all(line.strip() for line in lines)


# -- 级别与差异 -----------------------------------------------------------------


def test_originium_slug_has_two_levels(slug: EnemyPage) -> None:
    """实测：源石虫只有级别 0 与级别 1 两级（规格 §6.8 的「三级」按多级理解）。"""
    assert [level.index for level in slug.levels] == [0, 1]
    assert all(isinstance(level, EnemyLevel) for level in slug.levels)
    assert isinstance(slug.levels, tuple)


def test_originium_slug_level_1_only_has_two_keys(slug: EnemyPage) -> None:
    """级别 1 只写与级别 0 不同的字段：`index` 之外只有生命值与攻击力。"""
    level1 = slug.levels[1]
    assert dict(level1.fields) == {"最大生命值": "2050", "攻击力": "300"}
    assert "index" not in level1.fields


def test_originium_slug_level_0_full_stats(slug: EnemyPage) -> None:
    level0 = slug.levels[0]
    for key in BASE_STAT_KEYS:
        assert key in level0.fields, key
    assert level0.fields["最大生命值"] == "550"
    assert level0.fields["攻击力"] == "130"
    assert level0.fields["防御力"] == "0"
    assert level0.fields["法术抗性"] == "0"
    assert level0.fields["移动速度"] == "1.0"
    assert level0.fields["攻击间隔"] == "1.7"
    assert level0.fields["重量等级"] == "0"
    assert level0.fields["攻击范围半径"] == ""  # 空值原样保留（未清洗）


def test_originium_slug_resistances(slug: EnemyPage) -> None:
    level0 = slug.levels[0]
    for key in RESISTANCE_KEYS:
        assert level0.fields[key] == "无", key
    assert len(RESISTANCE_KEYS) == 11


def test_slug_defaults_when_fields_absent(slug: EnemyPage) -> None:
    """源石虫没有技能也没有天赋。"""
    assert slug.levels[0].skills == ()
    assert slug.levels[0].talent is None
    assert slug.levels[1].skills == ()
    assert slug.levels[1].talent is None


def test_patriot_levels_and_resistances(patriot: EnemyPage) -> None:
    assert [level.index for level in patriot.levels] == [0, 1]
    level0 = patriot.levels[0]
    assert level0.fields["最大生命值"] == "45000"
    assert level0.fields["防御力"] == "500"
    assert level0.fields["法术抗性"] == "45"
    assert level0.fields["重量等级"] == "7"
    for key in RESISTANCE_KEYS:
        assert key in level0.fields, key
    assert level0.fields["沉默抗性"] == "有"
    assert level0.fields["沉睡抗性"] == "有"
    assert level0.fields["冻结抗性"] == "无"
    assert level0.fields["损伤抵抗"] == "0"
    assert level0.fields["元素抗性"] == "0"
    assert level0.fields["基础嘲讽等级"] == "0"


def test_patriot_level_1_raw_fields(patriot: EnemyPage) -> None:
    """级别 1 只覆盖三个键；`技能2效果` 保留 `{{变动数值}}` 原文（未清洗）。"""
    level1 = patriot.levels[1]
    assert set(level1.fields) == {"最大生命值", "攻击力", "技能2效果"}
    assert level1.fields["最大生命值"] == "60000"
    assert "{{变动数值|5%|-}}" in level1.fields["技能2效果"]


def test_patriot_level_1_skills_only_override_slot_2(patriot: EnemyPage) -> None:
    """级别 1 只写了 `技能2效果`：产出一条技能槽，名字等留给合并层从级别 0 补。"""
    level1 = patriot.levels[1]
    assert [skill.slot for skill in level1.skills] == [2]
    assert level1.skills[0].name is None
    assert (
        level1.skills[0].effect
        == "对自身半径1.75范围内的我方单位造成相当于攻击力{{变动数值|5%|-}}的真实溅射伤害\n※不可对空"
    )


# -- 技能 -----------------------------------------------------------------------


def test_patriot_level_0_skills(patriot: EnemyPage) -> None:
    level0 = patriot.levels[0]
    assert [skill.slot for skill in level0.skills] == [0, 1, 2]
    assert all(isinstance(skill, EnemySkill) for skill in level0.skills)

    first = level0.skills[0]
    assert first.slot == 0
    assert first.name == "投枪\n[毁灭姿态]"
    assert first.effect is not None and "相当于攻击力135%的物理普通伤害" in first.effect
    assert first.effect is not None and first.effect.endswith("※不可对空")
    assert first.effect is not None and "<br>" not in first.effect
    assert first.sp_initial == "15"
    assert first.sp_cost == "20"

    second = level0.skills[1]
    assert second.slot == 1
    assert second.name == "生命献祭"
    assert second.sp_initial == "9999"
    assert second.sp_cost == "9999"


def test_skill_effect_keeps_raw_template_in_level_1(patriot: EnemyPage) -> None:
    """技能原文不清洗：级别 1 的覆盖率写在 `fields` 里，`{{变动数值}}` 原样保留。"""
    assert "{{变动数值|5%|-}}" in patriot.levels[1].fields["技能2效果"]


def test_patriot_talent(patriot: EnemyPage) -> None:
    """`天赋` 清洗后保留换行与 `<战术命令>` 这类装饰尖括号。"""
    talent = patriot.levels[0].talent
    assert talent is not None
    assert "{{" not in talent
    assert "<br>" not in talent
    assert talent.split("\n")[0] == "免疫脉冲波"
    assert "<战术命令>" in talent
    assert talent.rstrip().endswith("每间隔0.8s触发一次【献祭】")


# -- merge_levels 的补齐语义 -----------------------------------------------------


def test_merge_levels_returns_dicts_with_int_index(slug: EnemyPage) -> None:
    merged = merge_levels(slug)
    assert isinstance(merged, tuple)
    assert [level["index"] for level in merged] == [0, 1]
    assert all(isinstance(level["index"], int) for level in merged)
    assert all(isinstance(level["技能"], list) for level in merged)


def test_merge_levels_slug_level_1_inherits_level_0(slug: EnemyPage) -> None:
    """级别 1 的生命值来自自己，防御力 / 法术抗性 / 重量等级 来自级别 0。"""
    merged = merged_by_index(slug)
    level0, level1 = merged[0], merged[1]

    assert level1["最大生命值"] == "2050"  # 自己的
    assert level1["攻击力"] == "300"  # 自己的
    assert level1["防御力"] == "0"  # 级别 0 的
    assert level1["法术抗性"] == "0"  # 级别 0 的
    assert level1["重量等级"] == "0"  # 级别 0 的
    assert level1["眩晕抗性"] == "无"  # 级别 0 的
    assert level1["移动速度"] == level0["移动速度"]
    assert level1["攻击范围半径"] == level0["攻击范围半径"]


def test_merge_levels_level_0_is_its_own_values(slug: EnemyPage) -> None:
    merged = merged_by_index(slug)
    assert dict(slug.levels[0].fields) == {
        key: value for key, value in merged[0].items() if key not in ("index", "技能", "天赋")
    }


def test_merge_levels_patriot_skill_inheritance(patriot: EnemyPage) -> None:
    """爱国者级别 1 只覆盖 `技能2效果` → 合并后技能 0/1 完整、技能 2 用级别 1 的效果。"""
    merged = merged_by_index(patriot)
    skills = skills_by_slot(merged[1])
    assert sorted(skills) == [0, 1, 2]
    assert skills[0]["name"] == "投枪\n[毁灭姿态]"
    assert skills[0]["sp_initial"] == "15"
    assert skills[0]["sp_cost"] == "20"
    assert skills[0]["effect"] == merged[0]["技能"][0]["effect"]
    assert skills[1]["name"] == "生命献祭"
    # 槽 2：名字/初始/消耗沿用级别 0，效果用级别 1 覆盖的那份
    assert skills[2]["name"] == "献祭"
    assert skills[2]["sp_initial"] == "9999"
    assert "{{变动数值|5%|-}}" in skills[2]["effect"]
    assert skills[2]["effect"] != merged[0]["技能"][2]["effect"]


def test_merge_levels_patriot_talent_inherited(patriot: EnemyPage) -> None:
    """级别 1 没写 `天赋` → 沿用级别 0；技能是 list[dict]，天赋是 str。"""
    merged = merged_by_index(patriot)
    assert merged[1]["天赋"] == merged[0]["天赋"]
    assert isinstance(merged[1]["天赋"], str)
    assert isinstance(merged[1]["技能"], list)


def test_merge_levels_keeps_raw_chinese_keys(slug: EnemyPage) -> None:
    """级别级用原始中文键名（领域层映射的输入），技能 dict 用 EnemySkill 的英文字段名。"""
    merged = merged_by_index(slug)
    assert "最大生命值" in merged[1]
    assert "hp" not in merged[1]
    assert merged[1]["技能"] == []
    patriot_skills = merged_by_index(
        parse_enemy_page(wikitext("page_patriot.json"), title="爱国者")
    )[0]["技能"]
    assert set(patriot_skills[0]) == {"slot", "name", "effect", "sp_initial", "sp_cost"}
    assert set(merged[0]) == {
        "index",
        "技能",
        "天赋",
        *slug.levels[0].fields,
    }


def test_merge_levels_empty_page() -> None:
    page = parse_enemy_page("", title="空")
    assert merge_levels(page) == ()


# -- 页面边界 -------------------------------------------------------------------


def test_empty_page_does_not_raise() -> None:
    page = parse_enemy_page("", title="不存在")
    assert page.title == "不存在"
    assert page.enemy_id is None
    assert page.name is None
    assert page.index is None
    assert page.rank is None
    assert page.category is None
    assert page.damage_type is None
    assert page.attack_type is None
    assert page.move_type is None
    assert page.description is None
    assert page.abilities is None
    assert page.debut is None
    assert page.levels == ()


def test_navigation_only_page_does_not_raise() -> None:
    content = "{{Navigator|敌人一览}}\n{{敌人导航}}\n[[分类:敌人]]\n"
    page = parse_enemy_page(content, title="导航页")
    assert page.levels == ()
    assert page.enemy_id is None
    assert merge_levels(page) == ()


def test_missing_fields_do_not_raise() -> None:
    """只有 `{{敌人信息/common2}}`、没有 levelcontent 的页面。"""
    content = "{{敌人信息/common2\n|id=9\n|名称=测试\n}}\n"
    page = parse_enemy_page(content, title="测试")
    assert page.enemy_id == "9"
    assert page.name == "测试"
    assert page.index is None
    assert page.levels == ()


def test_level_without_index_uses_order() -> None:
    """`index` 缺失时按出现顺序补 0/1（不抛异常）。"""
    content = (
        "{{敌人信息/common2\n|id=5\n|名称=测试\n}}\n"
        "==级别0==\n{{敌人信息/levelcontent\n|最大生命值=100\n}}\n"
        "==级别1==\n{{敌人信息/levelcontent\n|最大生命值=200\n}}\n"
    )
    page = parse_enemy_page(content, title="测试")
    assert [level.index for level in page.levels] == [0, 1]
    merged = merged_by_index(page)
    assert merged[1]["最大生命值"] == "200"
    # 级别 0 没有别的键，级别 1 也就没有可补的
    assert set(merged[1]) == {"index", "最大生命值", "技能", "天赋"}


def test_levels_sorted_by_index_even_out_of_order() -> None:
    """正文里 section 顺序反了也按 `index` 升序返回。"""
    content = (
        "{{敌人信息/common2\n|id=6\n|名称=测试\n}}\n"
        "{{敌人信息/levelcontent\n|index=1\n|最大生命值=200\n}}\n"
        "{{敌人信息/levelcontent\n|index=0\n|最大生命值=100\n|防御力=5\n}}\n"
    )
    page = parse_enemy_page(content, title="测试")
    assert [level.index for level in page.levels] == [0, 1]
    merged = merged_by_index(page)
    assert merged[1]["最大生命值"] == "200"
    assert merged[1]["防御力"] == "5"


def test_skill_slot_needs_some_field() -> None:
    """槽有了 `技能N效果` 就算一条技能（名字可能写在上一个级别里）；只有名字也算。"""
    content = (
        "{{敌人信息/levelcontent\n|index=0\n|技能0=\n|技能0效果=空名字\n|技能1=有名字\n}}"
    )
    page = parse_enemy_page(content, title="测试")
    assert [(skill.slot, skill.name) for skill in page.levels[0].skills] == [
        (0, None),
        (1, "有名字"),
    ]


def test_skill_slot_after_9_is_numeric_sorted() -> None:
    """`技能10` 这类两位槽位：按数字排序，不做字符串排序。"""
    content = (
        "{{敌人信息/levelcontent\n|index=0\n|技能10=十\n|技能2=二\n|技能2初始=1\n|技能10初始=9\n}}"
    )
    page = parse_enemy_page(content, title="测试")
    assert [skill.slot for skill in page.levels[0].skills] == [2, 10]
    merged = merged_by_index(page)
    assert [skill["slot"] for skill in merged[0]["技能"]] == [2, 10]


def test_parse_does_not_read_files(monkeypatch: pytest.MonkeyPatch) -> None:
    """纯函数：解析入口不碰网络与文件系统。"""

    def boom(*args: Any, **kwargs: Any) -> Any:  # pragma: no cover - 只在不纯时触发
        raise AssertionError("解析器不该访问外部资源")

    monkeypatch.setattr("builtins.open", boom)
    page = parse_enemy_page("{{敌人信息/common2\n|id=1\n}}", title="t")
    assert page.enemy_id == "1"
