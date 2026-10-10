"""M25 Task 5：关卡页解析（规格 §5.7）的字段白名单与页面边界。

纯函数测试：不打网、不读仓库以外的文件。真实夹具是 ``tests/fixtures/prts/page_*.json``
（PRTS ``action=query&prop=revisions&rvslots=main`` 的裁剪快照，来源与抓取日期见同目录
README.md），正文在 ``query.pages[0].revisions[0].slots.main.content``：

- ``page_7-18.json``：``titles=7-18``，``redirects=1`` 之后是 ``7-18 爱国者之死``；
- ``page_vec08.json``：``VEC-08 磷脂层破坏``，活动关（``作战消耗=0``、无突袭段）。

合成用例里的 wikitext 形状都来自实测页面（``7-18`` 的突袭段与两个 ``{{关卡装置}}``、
``VEC-08`` 的 ``{{color}}`` + ``<br/>`` 描述、``敌人N地位级别``/``敌人N生命值`` 等）。
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import pytest

from maa_api.services.prts_parse.stage import (
    StageDevice,
    StageEnemy,
    StagePage,
    parse_stage_page,
)

FIXTURES = Path(__file__).resolve().parents[1] / "fixtures" / "prts"


def fixture(name: str) -> dict[str, Any]:
    return json.loads((FIXTURES / name).read_text(encoding="utf-8"))


def wikitext(name: str) -> str:
    """夹具里 ``query.pages[0]`` 的正文（``redirects=1`` 之后的页面）。"""
    return fixture(name)["query"]["pages"][0]["revisions"][0]["slots"]["main"]["content"]


def fixture_title(name: str) -> str:
    """夹具里 ``query.pages[0]`` 的标题（可能已被 ``redirects=1`` 改写）。"""
    return fixture(name)["query"]["pages"][0]["title"]


@pytest.fixture(scope="module")
def page_7_18() -> str:
    """7-18 爱国者之死（主线难关，带突袭段）正文。"""
    return wikitext("page_7-18.json")


@pytest.fixture(scope="module")
def vec08() -> str:
    """VEC-08 磷脂层破坏（活动关，无突袭段）正文。"""
    return wikitext("page_vec08.json")


def parse(content: str, *, title: str = "测试关") -> StagePage:
    return parse_stage_page(content, title=title)


# --------------------------------------------------------------------------- #
# 真实夹具：7-18 爱国者之死
# --------------------------------------------------------------------------- #


def test_fixture_7_18_uses_redirected_title(page_7_18: str) -> None:
    # 夹具是 titles=7-18 + redirects=1 的响应：请求的是代号，拿到的是重定向后的页面
    assert fixture_title("page_7-18.json") == "7-18 爱国者之死"
    page = parse(page_7_18, title=fixture_title("page_7-18.json"))
    assert page.title == "7-18 爱国者之死"
    assert page.code == "7-18"
    assert page.name == "爱国者之死"


def test_fixture_7_18_basic_fields(page_7_18: str) -> None:
    page = parse(page_7_18, title=fixture_title("page_7-18.json"))
    assert page.stage_id == "main_07-16"
    assert page.stage_type == "主线"
    assert page.subtype == "难关"
    assert page.difficulty == "NORMAL"
    assert page.unlock == "2星通关7-17 感染者之盾-2"
    assert page.recommend_level == "精英2 LV.30"
    assert page.deploy_limit == 9
    assert page.initial_cost == 10
    assert page.max_cost == 99
    assert page.life_points == 3
    assert page.enemy_count == 45
    assert page.map_size == "12×8"
    assert page.sanity == 21
    assert page.practice_sanity == 1
    assert page.area == "第七章 苦难摇篮"
    assert page.shortest_time == "3分47秒"


def test_fixture_7_18_enemies(page_7_18: str) -> None:
    page = parse(page_7_18)
    assert len(page.enemies) == 8
    assert page.enemies[0] == StageEnemy(name="游击队猎犬", count=4, level=0)
    assert [(enemy.name, enemy.count) for enemy in page.enemies] == [
        ("游击队猎犬", 4),
        ("游击队战士", 16),
        ("游击队狙击手", 4),
        ("游击队迫击炮兵", 2),
        ("游击队突袭战士", 11),
        ("游击队突袭战士组长", 2),
        ("游击队萨卡兹战士", 5),
        ("爱国者", 1),
    ]
    # 这页只有 敌人N / 敌人N数量 / 敌人N级别，后两项没有
    assert all(enemy.level == 0 for enemy in page.enemies)
    assert all(enemy.rank is None and enemy.hp is None for enemy in page.enemies)


def test_fixture_7_18_devices(page_7_18: str) -> None:
    page = parse(page_7_18)
    assert page.devices == (
        StageDevice(
            name="干扰地雷",
            deployable=15,
            skill="触爆",
            note="施加增伤效果后造成伤害",
        ),
        StageDevice(name="源石祭坛", deployable=None, skill="脉冲波", note=None),
    )


def test_fixture_7_18_challenge(page_7_18: str) -> None:
    page = parse(page_7_18)
    assert page.has_challenge is True
    assert page.challenge_condition is not None
    assert page.challenge_condition.startswith("附加条件：")
    assert "战场中的6个位置无法部署我方单位" in page.challenge_condition
    assert page.challenge_practice_sanity == 3
    # 突袭段自带 推荐等级=-，普通段的 精英2 LV.30 优先
    assert page.recommend_level == "精英2 LV.30"


def test_fixture_7_18_drops(page_7_18: str) -> None:
    page = parse(page_7_18)
    assert page.first_drops == ("至纯源石:三星获得", "阿米娅:首次掉落")
    assert page.normal_drops == ("异铁组:小概率", "酮凝集:概率掉落")


def test_fixture_7_18_description_is_cleaned(page_7_18: str) -> None:
    page = parse(page_7_18)
    assert page.description is not None
    assert "{{" not in page.description and "}}" not in page.description
    assert "<br" not in page.description
    # 两个 <br/> 归一成换行，<源石祭坛>/<干扰地雷> 这种装饰尖括号原样保留
    assert page.description.count("\n") == 2
    assert page.description.startswith("感染者游击队“盾”的指挥官。")
    assert "<源石祭坛>周期性向四周释放脉冲波，对我军与敌军造成伤害" in page.description
    assert "<干扰地雷>爆炸时可使四周的所有敌人造成大量伤害" in page.description


# --------------------------------------------------------------------------- #
# 真实夹具：VEC-08 磷脂层破坏（活动关）
# --------------------------------------------------------------------------- #


def test_fixture_vec08_basic_shape(vec08: str) -> None:
    page = parse(vec08, title=fixture_title("page_vec08.json"))
    assert page.title == "VEC-08 磷脂层破坏"
    assert page.code == "VEC-08"
    assert page.name == "磷脂层破坏"
    assert page.stage_id == "act3break_08"
    assert page.stage_type == "活动"
    assert page.difficulty == "NORMAL"
    assert page.subtype is None  # 活动关没有 子类型
    assert page.recommend_level == "-"
    assert page.unlock == "2星通关VEC-07 萌芽之时"
    assert page.deploy_limit == 8
    assert page.initial_cost == 10
    assert page.life_points == 1
    assert page.enemy_count == 16
    assert page.map_size == "11×8"
    assert page.area == "核心突破"
    assert page.sanity == 0  # 活动关不耗理智
    assert page.practice_sanity == -1


def test_fixture_vec08_has_no_challenge_section(vec08: str) -> None:
    page = parse(vec08)
    assert page.has_challenge is False
    assert page.challenge_condition is None
    assert page.challenge_practice_sanity is None
    # 也没有 首次掉落/常规掉落 与 {{关卡装置}}
    assert page.first_drops == ()
    assert page.normal_drops == ()
    assert page.devices == ()


def test_fixture_vec08_enemies(vec08: str) -> None:
    page = parse(vec08)
    assert [
        (enemy.name, enemy.count, enemy.level, enemy.rank, enemy.hp) for enemy in page.enemies
    ] == [
        ("萨卡兹穿刺手组长", 1, 0, None, "30000"),
        ("弧光锋卫长", 12, 0, None, "20000"),
        ("“肇事者”试做型", 2, 0, None, None),
        ("“再灌注”", 0, 0, None, None),
        ("“过灌注”", 0, 0, None, None),
        ("“Touch”，再生之流", 1, 0, None, None),
        ("“过灌注”", 0, 0, "领袖", None),  # 敌人7地位级别=领袖
    ]
    # 数量 0（场上生成、初始不出现）是合法值，不能被当成缺字段丢掉
    assert page.enemies[3].count == 0


def test_fixture_vec08_description_is_cleaned(vec08: str) -> None:
    page = parse(vec08)
    assert page.description is not None
    assert "{{" not in page.description and "}}" not in page.description
    assert "<br" not in page.description
    assert page.description.count("\n") == 3
    assert page.description.startswith("“由内而生的凋亡，与从外部而来的刀剑一样，")
    assert "<活性躁乱>部署在其上的我方单位每秒受到狂躁损伤" in page.description
    assert page.description.endswith("敌人的基础属性提升")


# --------------------------------------------------------------------------- #
# 边界：没有关卡信息
# --------------------------------------------------------------------------- #


@pytest.mark.parametrize(
    "content",
    [
        "",
        "{{pathnav2|关卡一览}}\n[[分类:关卡]]",
        "#REDIRECT [[7-18 爱国者之死]]\n[[分类:重定向]]",
        "{{关卡材料掉落}}\n{{关卡导航}}",
    ],
)
def test_page_without_stage_fields_yields_none(content: str) -> None:
    page = parse(content)
    assert page.code is None
    assert page.name is None
    assert page.stage_id is None
    assert page.stage_type is None
    assert page.difficulty is None
    assert page.recommend_level is None
    assert page.deploy_limit is None
    assert page.initial_cost is None
    assert page.life_points is None
    assert page.enemy_count is None
    assert page.map_size is None
    assert page.sanity is None
    assert page.description is None
    assert page.first_drops == ()
    assert page.normal_drops == ()
    assert page.challenge_condition is None
    assert page.challenge_practice_sanity is None
    assert page.has_challenge is False
    assert page.enemies == ()
    assert page.devices == ()


def test_normal_template_with_missing_fields_yields_none() -> None:
    page = parse(
        "{{普通关卡信息\n"
        "|关卡代号=1-7\n"
        "|关卡名=暴君\n"
        "|部署上限=abc\n"  # 不是数
        "|作战消耗=-\n"  # 不是数
        "|演习消耗=-1\n"  # 负数照收
        "|地图大小=   \n"  # 只有空白
        "|敌人数量=四十五\n"  # 中文数字不是数
        "}}"
    )
    assert (page.code, page.name) == ("1-7", "暴君")
    assert page.deploy_limit is None
    assert page.sanity is None
    assert page.practice_sanity == -1
    assert page.map_size is None
    assert page.enemy_count is None
    assert page.stage_id is None


def test_numeric_fields_tolerate_markup() -> None:
    page = parse("{{普通关卡信息|部署上限={{color|#fff|9}}|作战消耗={{color|#fff|21}}}}")
    assert page.deploy_limit == 9
    assert page.sanity == 21


# --------------------------------------------------------------------------- #
# 边界：{{敌方情报}}
# --------------------------------------------------------------------------- #


def test_enemies_sparse_indices_and_missing_names() -> None:
    page = parse(
        "{{敌方情报\n"
        "|敌人1=甲\n"
        "|敌人1数量=2\n"
        "|敌人1级别=x\n"  # 非法级别：只有这一项为 None
        "|敌人3=丙\n"
        "|敌人3地位级别=领袖\n"
        "|敌人3生命值=1000\n"
        "|敌人4数量=9\n"  # 只有数量、没有名字：整条丢掉
        "|说明=无视\n"  # 不是 敌人N 键
        "}}"
    )
    assert page.enemies == (
        StageEnemy(name="甲", count=2, level=None),
        StageEnemy(name="丙", rank="领袖", hp="1000"),
    )


def test_enemies_missing_template_is_empty() -> None:
    assert parse("{{普通关卡信息|关卡代号=1-7}}").enemies == ()


# --------------------------------------------------------------------------- #
# 边界：{{关卡装置}} 出现多次
# --------------------------------------------------------------------------- #


def test_devices_come_from_every_template_in_order() -> None:
    page = parse(
        "{{关卡装置|装置名称=干扰地雷|装置可部署数量=15|装置技能=触爆|备注=施加增伤}}\n"
        "==地图装置==\n"
        "{{关卡装置|装置技能=没有名字的整条丢掉}}\n"
        "{{关卡装置|装置名称=源石祭坛|装置技能=脉冲波}}\n"
        "{{关卡装置|装置名称=留声机}}\n"
    )
    assert page.devices == (
        StageDevice(name="干扰地雷", deployable=15, skill="触爆", note="施加增伤"),
        StageDevice(name="源石祭坛", skill="脉冲波"),
        StageDevice(name="留声机"),
    )


# --------------------------------------------------------------------------- #
# 边界：{{突袭关卡信息}} 与掉落切分
# --------------------------------------------------------------------------- #


def test_challenge_only_page_keeps_challenge_fields() -> None:
    page = parse(
        "{{突袭关卡信息\n"
        "|关卡代号=7-18\n"
        "|关卡难度=FOUR_STAR\n"
        "|推荐等级=精英1 LV.1\n"
        "|关卡描述={{color|#FF0000|附加条件：}}<br/>战场中的6个位置无法部署我方单位\n"
        "|演习消耗=3\n"
        "}}"
    )
    assert page.has_challenge is True
    assert page.challenge_practice_sanity == 3
    assert page.challenge_condition is not None
    assert page.challenge_condition.startswith("附加条件：")
    assert "战场中的6个位置无法部署我方单位" in page.challenge_condition
    # 普通段整个缺席：没有从突袭段借字段，只借了 推荐等级
    assert page.code is None
    assert page.stage_id is None
    assert page.sanity is None
    assert page.recommend_level == "精英1 LV.1"


def test_drops_split_on_both_commas_and_drop_empty_items() -> None:
    page = parse(
        "{{普通关卡信息"
        "|首次掉落=至纯源石:三星获得,阿米娅:首次掉落，龙门币:固定掉落,,  "
        "|常规掉落=, ,"
        "}}"
    )
    assert page.first_drops == ("至纯源石:三星获得", "阿米娅:首次掉落", "龙门币:固定掉落")
    assert page.normal_drops == ()


def test_description_cleans_terms_and_br() -> None:
    page = parse("{{普通关卡信息|关卡描述=第一行{{术语|ba.dt.rampage|狂躁损伤}}<br/>第二行<br>第三行}}")
    assert page.description == "第一行狂躁损伤\n第二行\n第三行"
