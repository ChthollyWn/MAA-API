"""M26 Task 2：干员页解析（规格 §6.3）与共享材料消耗解析。

纯函数测试：不打网、不读仓库以外的文件。夹具是 ``tests/fixtures/prts/page_*.json``
（PRTS ``action=query&prop=revisions&rvslots=main`` 的裁剪快照，来源与抓取日期见同目录
README.md），正文在 ``query.pages[0].revisions[0].slots.main.content``：

- ``page_silverash.json``：银灰（6★、三个技能都带专精、两个模组）；
- ``page_surtr.json``：史尔特尔（6★、天赋列表写 ``潜能增强_3天赋效果`` 这种异形键）；
- ``page_kroos.json``：克洛丝（3★、单技能、无模组、``{{精英化材料}}`` 只有精1）。

合成用例的形状同样来自实测页面（``{{pathnavis}}``、只有 ``{{CharinfoV2}}`` 的残页）。
字段名与取值以计划里的 T1 实测表为准。
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import pytest

from maa_api.services.prts_parse.costs import (
    MaterialCost,
    parse_material_costs,
    parse_wikitext,
)
from maa_api.services.prts_parse.operator import (
    OperatorModule,
    OperatorPage,
    OperatorSkill,
    OperatorSkillLevel,
    OperatorTalent,
    parse_operator_page,
)

FIXTURES = Path(__file__).resolve().parents[1] / "fixtures" / "prts"


def fixture(name: str) -> dict[str, Any]:
    return json.loads((FIXTURES / name).read_text(encoding="utf-8"))


def wikitext(name: str) -> str:
    """夹具里 ``query.pages[0]`` 的正文。"""
    return fixture(name)["query"]["pages"][0]["revisions"][0]["slots"]["main"]["content"]


@pytest.fixture(scope="module")
def silverash() -> OperatorPage:
    return parse_operator_page(wikitext("page_silverash.json"), title="银灰")


@pytest.fixture(scope="module")
def surtr() -> OperatorPage:
    return parse_operator_page(wikitext("page_surtr.json"), title="史尔特尔")


@pytest.fixture(scope="module")
def kroos() -> OperatorPage:
    return parse_operator_page(wikitext("page_kroos.json"), title="克洛丝")


def costs_of(pairs: tuple[tuple[str, int], ...]) -> dict[str, int]:
    return dict(pairs)


def module_by_name(page: OperatorPage, name: str) -> OperatorModule:
    for module in page.modules:
        if module.name == name:
            return module
    raise AssertionError(f"没有模组 {name!r}：{[m.name for m in page.modules]}")


def skill_level(skill: OperatorSkill, level: str) -> OperatorSkillLevel:
    for item in skill.levels:
        if item.level == level:
            return item
    raise AssertionError(f"技能 {skill.name!r} 没有等级 {level!r}：{[i.level for i in skill.levels]}")


# --------------------------------------------------------------------------- #
# 材料消耗解析（costs.py，敌人/材料解析共用）
# --------------------------------------------------------------------------- #


def test_material_costs_templates_numbers_and_spaces() -> None:
    text = "{{材料消耗|固源岩组|4}} {{材料消耗|龙门币|3w}}  {{材料消耗|装置|3}}"
    assert parse_material_costs(text) == (
        MaterialCost(name="固源岩组", amount=4),
        MaterialCost(name="龙门币", amount=30000),
        MaterialCost(name="装置", amount=3),
    )


def test_material_costs_w_suffix_is_ten_thousand() -> None:
    assert parse_material_costs("{{材料消耗|龙门币|18w}}") == (
        MaterialCost(name="龙门币", amount=180000),
    )
    assert parse_material_costs("{{材料消耗|龙门币|1w}}") == (
        MaterialCost(name="龙门币", amount=10000),
    )


def test_material_costs_chinese_wan_suffix() -> None:
    """实测：模组材料消耗写 ``8万``（精英化写 ``8w``），两种都要当万。"""
    assert parse_material_costs("{{材料消耗|龙门币|8万}}") == (
        MaterialCost(name="龙门币", amount=80000),
    )
    assert parse_material_costs("{{材料消耗|龙门币|12万}}") == (
        MaterialCost(name="龙门币", amount=120000),
    )


def test_material_costs_named_parameters() -> None:
    """``{{材料消耗|名称=…|数量=…}}`` 这种写法按命名参数读（实测里没有，防御性支持）。"""
    assert parse_material_costs("{{材料消耗|名称=聚酸酯组|数量=4}}") == (
        MaterialCost(name="聚酸酯组", amount=4),
    )


def test_material_costs_merges_same_name() -> None:
    text = "{{材料消耗|龙门币|3w}} {{材料消耗|技巧概要·卷3|8}} {{材料消耗|龙门币|5w}}"
    assert parse_material_costs(text) == (
        MaterialCost(name="龙门币", amount=80000),
        MaterialCost(name="技巧概要·卷3", amount=8),
    )


def test_material_costs_merge_within_one_key() -> None:
    """同一个参数值里同名材料出现两次也要相加。"""
    assert parse_material_costs("{{材料消耗|源岩|2}}{{材料消耗|源岩|3}}") == (
        MaterialCost(name="源岩", amount=5),
    )


def test_material_costs_ignores_other_templates_and_noise() -> None:
    text = (
        "{{材料消耗|装置|3}}\n"
        "{{道具图标|初级作战记录|25|50px}}"
        "{{color|#0098DC|正文}}"
        "{{材料消耗}}"
        "{{材料消耗|没有数量}}"
        "{{材料消耗|空数量|}}"
        "{{材料消耗||5}}"
    )
    assert parse_material_costs(text) == (MaterialCost(name="装置", amount=3),)


def test_material_costs_empty_text() -> None:
    assert parse_material_costs("") == ()
    assert parse_material_costs("没有任何模板") == ()


def test_material_cost_is_frozen() -> None:
    cost = MaterialCost(name="装置", amount=3)
    with pytest.raises(Exception):
        cost.amount = 4  # type: ignore[misc]


# --------------------------------------------------------------------------- #
# 共享的 ``键=值`` 表（costs.parse_wikitext，敌人/材料解析共用）
# --------------------------------------------------------------------------- #


def test_parse_wikitext_keeps_values_with_pipes_and_nested_equals() -> None:
    text = "|再部署=70s|部署费用=18→20|所属势力=谢拉格,喀兰贸易|备注=a[[b|c]]d{{e|f=g}}"
    assert dict(parse_wikitext(text)) == {
        "再部署": "70s",
        "部署费用": "18→20",
        "所属势力": "谢拉格,喀兰贸易",
        "备注": "a[[b|c]]d{{e|f=g}}",
    }


def test_parse_wikitext_skips_blank_lines_and_positional_args() -> None:
    # ``{{属性}}`` 用空行分段；位置参数（没有 ``=``）不当参数
    assert dict(parse_wikitext("|a=1|\n|b=2|没有等号")) == {"a": "1", "b": "2"}


def test_parse_wikitext_last_wins_and_empty_text() -> None:
    assert dict(parse_wikitext("|a=1|a=2")) == {"a": "2"}
    assert dict(parse_wikitext("")) == {}


# --------------------------------------------------------------------------- #
# 银灰：基础字段
# --------------------------------------------------------------------------- #


def test_silverash_title_and_basic_fields(silverash: OperatorPage) -> None:
    assert silverash.title == "银灰"
    assert silverash.name == "银灰"
    assert silverash.char_id == "char_172_svrash"
    assert silverash.rarity == 6  # 稀有度=5 从 0 数
    assert silverash.profession == "近卫"
    assert silverash.branch == "领主"
    assert silverash.position == "近战位"
    assert silverash.info_number == "JC01"
    assert silverash.tags == ("输出", "支援")


def test_silverash_trait_is_cleaned(silverash: OperatorPage) -> None:
    # 原始值：可以进行{{color|#00B0FF|远程攻击}}，但此时攻击力降低至80%
    assert silverash.trait == "可以进行远程攻击，但此时攻击力降低至80%"
    assert "{{" not in (silverash.trait or "")


def test_silverash_obtain_and_release_time(silverash: OperatorPage) -> None:
    assert silverash.obtain == "公开招募 中坚寻访"
    assert silverash.release_time == "2019-04-30 10:00"


def test_kroos_rarity_is_three_star(kroos: OperatorPage) -> None:
    assert kroos.rarity == 3  # 稀有度=2
    assert kroos.char_id == "char_124_kroos"
    assert kroos.profession == "狙击"
    assert kroos.branch == "速射手"
    assert kroos.position == "远程位"
    assert kroos.info_number == "PA12"
    assert kroos.tags == ("输出",)
    assert kroos.trait == "优先攻击空中单位"
    assert kroos.obtain == "公开招募 标准寻访 中坚寻访 主线剧情"
    assert kroos.release_time == "2019-04-30 10:00"


def test_surtr_basic_fields(surtr: OperatorPage) -> None:
    assert surtr.name == "史尔特尔"
    assert surtr.rarity == 6
    assert surtr.profession == "近卫"
    assert surtr.branch == "术战者"
    assert surtr.release_time == "2020-09-24 16:00"


# --------------------------------------------------------------------------- #
# 银灰：属性与攻击范围
# --------------------------------------------------------------------------- #


def test_silverash_stats_keep_raw_keys(silverash: OperatorPage) -> None:
    stats = silverash.stats
    # 规格要求 ``{{属性}}`` 的原始键值原样带出（领域层再换算）
    assert stats["部署费用"] == "18→20"
    assert stats["再部署"] == "70s"
    assert stats["阻挡数"] == "2"
    assert stats["攻击速度"] == "1.3s"
    assert stats["精英0_1级_生命上限"] == "1075"
    assert stats["精英0_1级_攻击"] == "297"
    assert stats["精英0_满级"] == "50"
    assert stats["精英0_满级_生命上限"] == "1536"
    assert stats["精英1_满级_生命上限"] == "2022"
    assert stats["精英2_满级_生命上限"] == "2560"
    assert stats["精英2_满级_攻击"] == "713"
    assert stats["精英2_满级_防御"] == "397"
    assert stats["精英2_满级_法术抗性"] == "10"
    assert stats["信赖加成_生命上限"] == "0"
    assert stats["信赖加成_攻击"] == "50"
    assert stats["信赖加成_防御"] == "50"
    assert stats["潜能"] == "-1,-4,26,,-1"
    assert stats["潜能类型"] == "cost,re_deploy,atk,,cost"


def test_silverash_stats_keeps_every_key(silverash: OperatorPage) -> None:
    """白名单是「``{{属性}}`` 全键」，不筛子集、数值也不换算。"""
    assert "所属势力" in silverash.stats
    assert "初始模组名" in silverash.stats
    assert silverash.stats["所属势力"] == "谢拉格,喀兰贸易"
    assert silverash.stats["模组1数据"] == "max_hp:330;atk:70"
    assert len(silverash.stats) == 32  # 夹具里 ``{{属性}}`` 的参数个数


def test_silverash_range_codes(silverash: OperatorPage) -> None:
    assert dict(silverash.range_codes) == {
        "精英0范围": "2-3",
        "精英1范围": "3-12",
        "精英2范围": "3-12",
    }


def test_kroos_range_codes_have_two_stages(kroos: OperatorPage) -> None:
    # 3★ 只有精英0/精英1
    assert dict(kroos.range_codes) == {"精英0范围": "3-1", "精英1范围": "3-3"}
    assert "精英2范围" not in kroos.range_codes


# --------------------------------------------------------------------------- #
# 银灰：材料消耗
# --------------------------------------------------------------------------- #


def test_silverash_elite_costs(silverash: OperatorPage) -> None:
    assert costs_of(((c.name, c.amount) for c in silverash.elite_costs["精1"])) == {
        "龙门币": 30000,  # 3w
        "近卫芯片": 5,
        "聚酸酯": 8,
        "装置": 3,
    }
    assert costs_of(((c.name, c.amount) for c in silverash.elite_costs["精2"])) == {
        "龙门币": 180000,  # 18w
        "近卫双芯片": 4,
        "D32钢": 4,
        "白马醇": 6,
    }


def test_kroos_elite_costs_only_elite_one(kroos: OperatorPage) -> None:
    # 3★ 没有精2
    assert list(kroos.elite_costs) == ["精1"]
    assert costs_of(((c.name, c.amount) for c in kroos.elite_costs["精1"])) == {"龙门币": 10000}


def test_kroos_skill_costs_are_level_2_to_7(kroos: OperatorPage) -> None:
    assert list(kroos.skill_costs) == ["2", "3", "4", "5", "6", "7"]
    assert costs_of(((c.name, c.amount) for c in kroos.skill_costs["2"])) == {
        "技巧概要·卷1": 1
    }
    assert costs_of(((c.name, c.amount) for c in kroos.skill_costs["3"])) == {
        "技巧概要·卷1": 2,
        "破损装置": 1,
    }
    assert costs_of(((c.name, c.amount) for c in kroos.skill_costs["7"])) == {
        "技巧概要·卷3": 2,
        "RMA70-12": 1,
    }


def test_silverash_skill_costs_include_three_mastery_groups(silverash: OperatorPage) -> None:
    assert list(silverash.skill_costs) == [
        "2", "3", "4", "5", "6", "7",
        "一8", "一9", "一10",
        "二8", "二9", "二10",
        "三8", "三9", "三10",
    ]
    assert costs_of(((c.name, c.amount) for c in silverash.skill_costs["一8"])) == {
        "技巧概要·卷3": 6,
        "异铁块": 4,
        "糖组": 4,
    }
    assert costs_of(((c.name, c.amount) for c in silverash.skill_costs["二8"])) == {
        "技巧概要·卷3": 6,
        "酮阵列": 4,
        "聚酸酯组": 6,
    }
    assert costs_of(((c.name, c.amount) for c in silverash.skill_costs["三8"])) == {
        "技巧概要·卷3": 6,
        "改量装置": 3,
        "异铁组": 4,
    }
    assert costs_of(((c.name, c.amount) for c in silverash.skill_costs["三10"])) == {
        "技巧概要·卷3": 15,
        "双极纳米片": 6,
        "糖聚块": 5,
    }


# --------------------------------------------------------------------------- #
# 银灰 / 克洛丝：技能
# --------------------------------------------------------------------------- #


def test_silverash_has_three_skills_in_page_order(silverash: OperatorPage) -> None:
    assert [skill.name for skill in silverash.skills] == ["强力击·γ型", "雪境生存法则", "真银斩"]


def test_silverash_skill_types(silverash: OperatorPage) -> None:
    strong_strike, survival, truesilver = silverash.skills
    assert strong_strike.charge_type == "攻击回复"
    assert strong_strike.trigger_type == "自动触发"
    assert survival.charge_type == "自动回复"
    assert survival.trigger_type == "手动触发"
    assert truesilver.charge_type == "自动回复"
    assert truesilver.trigger_type == "手动触发"


def test_silverash_skill_levels_are_7_plus_mastery(silverash: OperatorPage) -> None:
    strong_strike = silverash.skills[0]
    assert [level.level for level in strong_strike.levels] == [
        "1", "2", "3", "4", "5", "6", "7", "专精1", "专精2", "专精3"
    ]
    level7 = skill_level(strong_strike, "7")
    assert level7.sp_initial == "0"
    assert level7.sp_cost == "3"
    assert level7.duration is None  # 实测 ``技能7持续=`` 是空串
    assert level7.description == "下次攻击的攻击力提高至225%"
    mastery3 = skill_level(strong_strike, "专精3")
    assert mastery3.sp_cost == "2"
    assert mastery3.description == "下次攻击的攻击力提高至290%"


def test_silverash_skill_multiline_description_keeps_breaks(silverash: OperatorPage) -> None:
    """``描述`` 里的 ``<br/>`` 归一成换行；短文本里的 ``<br>`` 同理。"""
    survival = silverash.skills[1]
    level1 = skill_level(survival, "1")
    assert level1.description == (
        "可以在下列状态和初始状态间切换：\n"
        "攻击范围缩小，防御力+35%，每秒恢复最大生命的3.0%"
    )
    truesilver = skill_level(silverash.skills[2], "专精3")
    assert truesilver.sp_initial == "75"
    assert truesilver.sp_cost == "90"
    assert truesilver.duration == "30"
    assert truesilver.description == (
        "防御力-70%，攻击力+200%，攻击范围扩大，同时攻击至多6个目标（视为近距离攻击）"
    )


def test_silverash_skill_note(silverash: OperatorPage) -> None:
    """``备注`` 保留换行；没写 ``备注`` 的技能是 ``None``（真银斩那个 ``{{技能2}}`` 没写）。"""
    note = silverash.skills[0].note
    assert note is not None
    assert note.startswith("※技能生效时，不会应用特性的“攻击降低”")
    assert "\n" in note  # 备注里的 ``<br>`` 归一成换行
    assert "{{" not in note
    assert silverash.skills[2].note is None


def test_kroos_skill_description_keys(silverash: OperatorPage, kroos: OperatorPage) -> None:
    """克洛丝只有一个技能：``技能N描述`` 1–7，没有专精键。"""
    assert [skill.name for skill in kroos.skills] == ["二连射·自动"]
    skill = kroos.skills[0]
    assert skill.charge_type == "攻击回复"
    assert skill.trigger_type == "自动触发"
    assert skill.note is None  # 页面没写 ``备注``
    assert [level.level for level in skill.levels] == ["1", "2", "3", "4", "5", "6", "7"]
    assert skill_level(skill, "1").description == (
        "下次攻击时连续射击2次，每次射击造成相当于攻击力100%的物理伤害"
    )
    assert skill_level(skill, "7").description == (
        "下次攻击时连续射击2次，每次射击造成相当于攻击力140%的物理伤害"
    )
    assert skill_level(skill, "7").sp_cost == "4"
    assert skill_level(skill, "7").duration is None


def test_surtr_each_skill_has_mastery_descriptions(surtr: OperatorPage) -> None:
    assert [skill.name for skill in surtr.skills] == ["烈焰魔剑", "熔核巨影", "黄昏"]
    for skill in surtr.skills:
        for level in ("专精1", "专精2", "专精3"):
            described = skill_level(skill, level)
            assert described.description, f"{skill.name} {level} 缺描述"
            assert "{{" not in described.description


# --------------------------------------------------------------------------- #
# 银灰 / 史尔特尔 / 克洛丝：天赋与潜能
# --------------------------------------------------------------------------- #


def test_silverash_talents_are_base_talents_only(silverash: OperatorPage) -> None:
    """``天赋列表3`` 里 ``天赋N条件`` 带模组等级的条目归模组，基础天赋只留非模组。"""
    assert [(t.name, t.condition) for t in silverash.talents] == [
        ("领袖", "精英1"),
        ("领袖", "精英2"),
        ("鹰眼视觉", "精英2"),
    ]
    effect = silverash.talents[0].effect
    assert effect is not None
    assert effect.startswith("攻击力+5%")
    assert "{{" not in effect
    # ``{{修正|可部署单位|原文=…}}`` 取展示文字
    assert "可部署单位" in effect
    assert "原文=" not in effect


def test_kroos_talents(silverash: OperatorPage, kroos: OperatorPage) -> None:
    assert [(t.name, t.condition) for t in kroos.talents] == [
        ("要害瞄准·初级", "精英1"),
        ("要害瞄准·初级", "精英1 55级"),
    ]
    assert kroos.talents[1].effect == "攻击时，20%几率当次攻击的攻击力提升至150%"


def test_surtr_talents_from_two_lists(surtr: OperatorPage) -> None:
    assert [(t.name, t.condition) for t in surtr.talents] == [
        ("熔火", "精英1"),
        ("熔火", "精英2"),
        ("余烬", "精英1"),
        ("余烬", "精英2"),
    ]


def test_silverash_potential_talents(silverash: OperatorPage) -> None:
    """``潜能增强_天赋N*`` 里非模组的 4 条；模组那 4 条进 ``modules[...]``。"""
    assert [(t.name, t.condition) for t in silverash.potential_talents] == [
        ("领袖", "精英1"),
        ("领袖", "精英2"),
    ]
    effect = silverash.potential_talents[0].effect
    assert effect is not None
    assert effect.startswith("攻击力+7%（+2%）")
    assert "{{" not in effect


def test_surtr_potential_talents_use_separate_key_shape(surtr: OperatorPage) -> None:
    """实测史尔特尔写 ``潜能增强_3天赋效果``（数字在前、没有第二个下划线）。"""
    assert [(t.name, t.condition) for t in surtr.potential_talents] == [
        ("熔火", "精英1"),
        ("熔火", "精英2"),
        ("余烬", "精英1"),
        ("余烬", "精英2"),
    ]
    first = surtr.potential_talents[0]
    assert first.name == "熔火"  # 键里没有名字，从 ``天赋1`` 借
    assert first.effect == "无视攻击目标14（+2）法术抗性"
    third = surtr.potential_talents[2]
    assert third.name == "余烬"  # 键是 ``潜能增强_3天赋效果``，名字从 ``天赋3`` 借
    assert third.condition == "精英1"  # 条件从 ``天赋3条件`` 借
    assert third.effect == "受到致命伤害时持续使生命值不低于1，5（+1）秒后强制退出战场"


def test_kroos_potential_talents(kroos: OperatorPage) -> None:
    assert [(t.name, t.condition) for t in kroos.potential_talents] == [
        ("要害瞄准·初级", "精英1"),
        ("要害瞄准·初级", "精英1 55级"),
    ]
    assert kroos.potential_talents[1].effect == (
        "攻击时，20%几率当次攻击的攻击力提升至160%（+10%）"
    )


def test_silverash_potentials(silverash: OperatorPage) -> None:
    assert dict(silverash.potentials) == {
        "潜能2": "部署费用-1",
        "潜能3": "再部署时间-4秒",
        "潜能4": "攻击力+26",
        "潜能5": "第一天赋效果增强",
        "潜能6": "部署费用-1",
    }


def test_kroos_potentials(kroos: OperatorPage) -> None:
    assert dict(kroos.potentials) == {
        "潜能2": "部署费用-1",
        "潜能3": "再部署时间-4秒",
        "潜能4": "攻击力+21",
        "潜能5": "天赋效果增强",
        "潜能6": "部署费用-1",
    }


# --------------------------------------------------------------------------- #
# 银灰 / 史尔特尔：模组
# --------------------------------------------------------------------------- #


def test_silverash_module_names_in_page_order(silverash: OperatorPage) -> None:
    assert [module.name for module in silverash.modules] == ["银灰证章", "雪境羽兽护理套组"]


def test_silverash_base_module_has_no_type_or_stats(silverash: OperatorPage) -> None:
    """基础证章（``基础证章=yes``）没有 ``类型``/属性/材料，全是空值。"""
    badge = module_by_name(silverash, "银灰证章")
    assert badge.type_code is None
    assert badge.trait_added is False
    assert badge.trait is None
    assert badge.level_stats == ()
    assert badge.talents == ()
    assert badge.potential_talents == ()
    assert badge.costs == ()


def test_silverash_exclusive_module_fields(silverash: OperatorPage) -> None:
    module = module_by_name(silverash, "雪境羽兽护理套组")
    assert module.type_code == "LOR-X"
    assert module.trait_added is True
    assert module.trait is not None
    assert module.trait.startswith("攻击附带10%攻击力的法术伤害")
    assert "{{" not in module.trait


def test_silverash_module_level_stats(silverash: OperatorPage) -> None:
    module = module_by_name(silverash, "雪境羽兽护理套组")
    assert [(level, dict(stats)) for level, stats in module.level_stats] == [
        (1, {"生命": "190", "攻击": "45"}),
        (2, {"生命": "270", "攻击": "60"}),
        (3, {"生命": "330", "攻击": "70"}),
    ]


def test_silverash_module_talents(silverash: OperatorPage) -> None:
    module = module_by_name(silverash, "雪境羽兽护理套组")
    assert [t.name for t in module.talents] == ["领袖", "领袖"]
    assert [t.condition for t in module.talents] == ["精英2 X模组2级", "精英2 X模组3级"]
    assert module.talents[0].effect is not None
    assert module.talents[0].effect.startswith("天赋【领袖】更新\n攻击力+20%")
    assert "变动数值" not in module.talents[0].effect
    assert module.talents[1].effect is not None
    assert "攻击精英与领袖敌人时攻击力提升至115%" in module.talents[1].effect
    assert "\n" in module.talents[1].effect  # 末尾的 ※ 说明另起一行


def test_silverash_module_potential_talents(silverash: OperatorPage) -> None:
    module = module_by_name(silverash, "雪境羽兽护理套组")
    assert [t.name for t in module.potential_talents] == ["领袖", "领袖"]
    assert module.potential_talents[0].condition == "精英2 X模组2级"
    assert module.potential_talents[0].effect is not None
    assert "（+2%）" in module.potential_talents[0].effect


def test_silverash_module_costs_with_wan_suffix(silverash: OperatorPage) -> None:
    """实测模组材料写 ``{{材料消耗|龙门币|8万}}``（不是 ``w``）。"""
    module = module_by_name(silverash, "雪境羽兽护理套组")
    assert [(level, costs_of(((c.name, c.amount) for c in costs))) for level, costs in module.costs] == [
        (1, {"模组数据块": 4, "聚合剂": 2, "龙门币": 80000}),
        (2, {"模组数据块": 4, "数据增补条": 60, "晶体电子单元": 3, "龙门币": 100000}),
        (3, {"模组数据块": 4, "数据增补仪": 20, "烧结核凝晶": 4, "龙门币": 120000}),
    ]


def test_surtr_modules(surtr: OperatorPage) -> None:
    assert [module.name for module in surtr.modules] == [
        "史尔特尔证章",
        "萨米的不灭心脏碎片",
        "旅游必需品",
    ]
    heart = module_by_name(surtr, "萨米的不灭心脏碎片")
    assert heart.type_code == "AFT-X"
    assert heart.trait_added is True
    assert [(level, dict(stats)) for level, stats in heart.level_stats] == [
        (1, {"攻击": "30", "法术抗性": "5"}),
        (2, {"攻击": "48", "法术抗性": "5"}),
        (3, {"攻击": "60", "法术抗性": "5"}),
    ]
    assert [t.name for t in heart.talents] == ["熔火", "熔火"]
    assert heart.talents[1].effect == "天赋【熔火】更新\n无视攻击目标26法术抗性"
    assert heart.potential_talents == ()  # 这页没写潜能天赋
    assert len(heart.costs) == 3

    travel = module_by_name(surtr, "旅游必需品")
    assert travel.type_code == "AFT-Y"
    assert [(level, dict(stats)) for level, stats in travel.level_stats] == [
        (1, {"攻击": "45", "防御": "42"}),
        (2, {"攻击": "55", "防御": "56"}),
        (3, {"攻击": "60", "防御": "65"}),
    ]
    assert travel.trait is not None
    assert travel.trait == "自身阻挡的敌人受到10%的法术脆弱效果"


# --------------------------------------------------------------------------- #
# 合成用例：模组条件按等级下标挑
# --------------------------------------------------------------------------- #


def test_module_talent_conditions_pick_by_level_index() -> None:
    """同名天赋在多个模组等级里复用时，``天赋N`` 的 N 决定挑哪条条件。"""
    content = (
        "{{天赋列表3|天赋=第一天赋|天赋1=领袖|天赋1条件=精英2"
        "|天赋1效果=基础效果|天赋2=领袖|天赋2条件=精英2 X模组2级|天赋2效果=模组2级效果"
        "|天赋3=领袖|天赋3条件=精英2 X模组3级|天赋3效果=模组3级效果}}\n"
        "{{模组|名称=X模组|类型=LOR-X|生命=190|生命2=270|生命3=330"
        "|天赋2=天赋【领袖】更新<br/>2级数值|天赋3=天赋【领袖】更新<br/>3级数值}}"
    )
    page = parse_operator_page(content, title="合成")
    assert [(t.name, t.condition) for t in page.talents] == [("领袖", "精英2")]
    module = page.modules[0]
    assert [(t.condition, t.effect) for t in module.talents] == [
        ("精英2 X模组2级", "天赋【领袖】更新\n2级数值"),
        ("精英2 X模组3级", "天赋【领袖】更新\n3级数值"),
    ]
    assert [(level, dict(stats)) for level, stats in module.level_stats] == [
        (1, {"生命": "190"}),
        (2, {"生命": "270"}),
        (3, {"生命": "330"}),
    ]


# --------------------------------------------------------------------------- #
# 边界：缺字段、空页面
# --------------------------------------------------------------------------- #


def test_missing_fields_do_not_raise() -> None:
    page = parse_operator_page("{{CharinfoV2|干员名=无名}}", title="无名")
    assert page.title == "无名"
    assert page.name == "无名"
    assert page.char_id is None
    assert page.rarity is None
    assert page.profession is None
    assert page.branch is None
    assert page.trait is None
    assert page.tags == ()
    assert page.position is None
    assert page.info_number is None
    assert page.obtain is None
    assert page.release_time is None
    assert dict(page.stats) == {}
    assert dict(page.range_codes) == {}
    assert dict(page.elite_costs) == {}
    assert dict(page.skill_costs) == {}
    assert page.skills == ()
    assert page.talents == ()
    assert page.potential_talents == ()
    assert dict(page.potentials) == {}
    assert page.modules == ()


def test_empty_page() -> None:
    page = parse_operator_page("", title="空页")
    assert page.title == "空页"
    assert page.name is None
    assert page.rarity is None
    assert page.skills == ()
    assert page.modules == ()
    assert dict(page.stats) == {}


def test_pathnavis_only_page() -> None:
    page = parse_operator_page("{{pathnavis|干员一览}}", title="导航")
    assert page.title == "导航"
    assert page.name is None
    assert page.char_id is None
    assert page.talents == ()
    assert page.skills == ()


def test_rarity_is_clamped_into_one_to_six() -> None:
    """``稀有度`` 是从 0 数的；越界值不抛异常，夹到 1–6。"""
    assert parse_operator_page("{{CharinfoV2|稀有度=0}}", title="x").rarity == 1
    assert parse_operator_page("{{CharinfoV2|稀有度=5}}", title="x").rarity == 6
    assert parse_operator_page("{{CharinfoV2|稀有度=9}}", title="x").rarity == 6
    assert parse_operator_page("{{CharinfoV2|稀有度=稀有}}", title="x").rarity is None


def test_skill_without_levels_keeps_empty_tuple() -> None:
    page = parse_operator_page("{{技能|技能名=空技能}}", title="x")
    assert [skill.name for skill in page.skills] == ["空技能"]
    assert page.skills[0].levels == ()
    assert page.skills[0].charge_type is None
    assert page.skills[0].trigger_type is None
    assert page.skills[0].note is None


def test_module_with_name_only_does_not_raise() -> None:
    page = parse_operator_page("{{模组|名称=残页模组}}", title="x")
    assert [module.name for module in page.modules] == ["残页模组"]
    module = page.modules[0]
    assert module.type_code is None
    assert module.trait_added is False
    assert module.level_stats == ()
    assert module.costs == ()


def test_material_cost_amount_without_number_is_zero() -> None:
    """数量写不出数字时按 0 记，不抛异常（页面残页的自保）。"""
    assert parse_material_costs("{{材料消耗|龙门币|很多}}") == (
        MaterialCost(name="龙门币", amount=0),
    )
