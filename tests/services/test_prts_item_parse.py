"""M26 Task 4：材料页解析（规格 §6.5）的基础字段与加工站配方。

纯函数测试：不打网、不读仓库以外的文件。真实夹具是 ``tests/fixtures/prts/page_*.json``
（PRTS ``action=query&prop=revisions&rvslots=main`` 的裁剪快照，正文在
``query.pages[0].revisions[0].slots.main.content``）：

- ``page_item_30013.json``（固源岩组）：``{{道具信息}}`` + ``{{道具配方/加工站}}``
  （1 条原料、6 条副产物、``副产物总概率=10``、``龙门币消耗=200``）；
- ``page_item_30033.json``（聚酸酯组）：同上，原料是``聚酸酯``。

配方原料用的是独立的 ``原料N`` / ``原料N数量`` 键（不是 ``{{材料消耗}}``），所以这里
不依赖 ``prts_parse/costs.py``。
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import pytest

from maa_api.services.prts_parse.item import (
    CraftRecipe,
    ItemPage,
    parse_item_page,
)

FIXTURES = Path(__file__).resolve().parents[1] / "fixtures" / "prts"

#: 夹具里加工站配方的六条副产物（掉率是百分比数值，不是小数）。
EXPECTED_BYPRODUCTS = (
    ("固源岩", 26.3),
    ("糖", 17.5),
    ("聚酸酯", 17.5),
    ("异铁", 14.0),
    ("酮凝集", 14.0),
    ("装置", 10.5),
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
def solid_rock() -> ItemPage:
    """固源岩组。"""
    name = "page_item_30013.json"
    return parse_item_page(wikitext(name), title=fixture_title(name))


@pytest.fixture(scope="module")
def polyester() -> ItemPage:
    """聚酸酯组。"""
    name = "page_item_30033.json"
    return parse_item_page(wikitext(name), title=fixture_title(name))


# -- 基础字段 -------------------------------------------------------------------


def test_solid_rock_basic_fields(solid_rock: ItemPage) -> None:
    assert solid_rock.title == "固源岩组"
    assert solid_rock.name == "固源岩组"
    assert solid_rock.item_id == "30013"
    assert solid_rock.icon_id == "MTL_SL_G3"
    assert solid_rock.rarity == 2
    assert isinstance(solid_rock.rarity, int)
    assert solid_rock.category == "材料"
    assert solid_rock.obtain == "加工站"
    assert solid_rock.description is not None
    assert "固源岩的压缩定型产物" in solid_rock.description
    assert solid_rock.usage is not None
    assert "制造站合成项目的原料" in solid_rock.usage


def test_polyester_basic_fields(polyester: ItemPage) -> None:
    assert polyester.title == "聚酸酯组"
    assert polyester.item_id == "30033"
    assert polyester.icon_id == "MTL_SL_RUSH3"
    assert polyester.rarity == 2
    assert polyester.category == "材料"
    assert polyester.obtain == "加工站"
    assert polyester.description is not None
    assert "进一步加工后的材料" in polyester.description


def test_description_has_no_markup(solid_rock: ItemPage) -> None:
    assert solid_rock.description is not None
    assert "{{" not in solid_rock.description
    assert "[[" not in solid_rock.description
    assert solid_rock.description.startswith("固源岩的压缩定型产物")


# -- 加工站配方 -----------------------------------------------------------------


def test_solid_rock_recipe_scalars(solid_rock: ItemPage) -> None:
    recipe = solid_rock.recipe
    assert isinstance(recipe, CraftRecipe)
    assert recipe.product == "固源岩组"
    assert recipe.product_count == 1
    assert recipe.lmd_cost == 200
    assert recipe.mood_cost == 2
    assert recipe.station_level == 2
    assert recipe.byproduct_total == 10.0


def test_solid_rock_recipe_materials(solid_rock: ItemPage) -> None:
    assert solid_rock.recipe is not None
    assert solid_rock.recipe.materials == (("固源岩", 5),)
    assert all(isinstance(name, str) and isinstance(count, int)
               for name, count in solid_rock.recipe.materials)


def test_solid_rock_recipe_byproducts(solid_rock: ItemPage) -> None:
    assert solid_rock.recipe is not None
    assert solid_rock.recipe.byproducts == EXPECTED_BYPRODUCTS
    assert all(isinstance(name, str) and isinstance(rate, float)
               for name, rate in solid_rock.recipe.byproducts)


def test_polyester_recipe(polyester: ItemPage) -> None:
    recipe = polyester.recipe
    assert recipe is not None
    assert recipe.product == "聚酸酯组"
    assert recipe.product_count == 1
    assert recipe.lmd_cost == 200
    assert recipe.mood_cost == 2
    assert recipe.station_level == 2
    assert recipe.byproduct_total == 10.0
    assert recipe.materials == (("聚酸酯", 4),)
    assert recipe.byproducts == EXPECTED_BYPRODUCTS


def test_material_quantities_are_ints(solid_rock: ItemPage) -> None:
    """数量是 int（不是 ``"5"``）；掉率是 float（不是 ``"26.3"``）。"""
    recipe = solid_rock.recipe
    assert recipe is not None
    name, count = recipe.materials[0]
    assert (name, count) == ("固源岩", 5)
    assert recipe.materials != (("固源岩", "5"),)


def test_recipe_is_none_without_template(solid_rock: ItemPage) -> None:
    """只有 ``{{道具信息}}``、没有 ``{{道具配方/加工站}}`` 的页面没有配方。"""
    page = parse_item_page(
        "{{道具信息\n|名称=测试道具\n|itemId=99999\n|稀有度=3\n|分类=材料\n}}\n",
        title="测试道具",
    )
    assert page.recipe is None
    assert page.item_id == "99999"
    assert page.rarity == 3


def test_recipe_with_only_product_still_returned() -> None:
    """模板在但字段缺 → 返回 ``CraftRecipe``，缺的字段是 ``None`` / 空元组。"""
    page = parse_item_page("{{道具配方/加工站\n|产物=测试道具\n}}", title="测试道具")
    recipe = page.recipe
    assert isinstance(recipe, CraftRecipe)
    assert recipe.product == "测试道具"
    assert recipe.product_count is None
    assert recipe.lmd_cost is None
    assert recipe.mood_cost is None
    assert recipe.station_level is None
    assert recipe.byproduct_total is None
    assert recipe.materials == ()
    assert recipe.byproducts == ()


def test_material_count_defaults_to_one() -> None:
    """``原料N数量`` 缺失时按 1 计（PRTS 上默认 1 份）。"""
    page = parse_item_page("{{道具配方/加工站\n|原料1=固源岩\n|原料2=糖\n|原料2数量=3\n}}", title="t")
    assert page.recipe is not None
    assert page.recipe.materials == (("固源岩", 1), ("糖", 3))


def test_materials_sorted_numerically() -> None:
    """``原料10`` 排在 ``原料2`` 之后（数字序，不是字符串序）。"""
    page = parse_item_page(
        "{{道具配方/加工站\n|原料10=十\n|原料10数量=10\n|原料2=二\n|原料2数量=2\n}}",
        title="t",
    )
    assert page.recipe is not None
    assert page.recipe.materials == (("二", 2), ("十", 10))


def test_byproducts_sorted_numerically_and_rate_missing() -> None:
    page = parse_item_page(
        "{{道具配方/加工站\n|副产物10=十\n|副产物10掉率=1.5\n|副产物2=二\n}}",
        title="t",
    )
    assert page.recipe is not None
    assert page.recipe.byproducts == (("二", None), ("十", 1.5))


def test_quantity_suffix_and_whitespace_tolerated() -> None:
    """数字字段容忍两侧空白与 ``+`` 号这类写法，解析不了就是 ``None``。"""
    page = parse_item_page(
        "{{道具信息\n|名称=x\n|稀有度= 3 \n}}\n"
        "{{道具配方/加工站\n|龙门币消耗=一百\n|心情消耗=0\n}}",
        title="x",
    )
    assert page.rarity == 3
    assert page.recipe is not None
    assert page.recipe.lmd_cost is None
    assert page.recipe.mood_cost == 0


def test_rarity_non_numeric_is_none() -> None:
    page = parse_item_page("{{道具信息\n|名称=x\n|稀有度=\n}}", title="x")
    assert page.rarity is None


# -- 页面边界 -------------------------------------------------------------------


def test_empty_page_does_not_raise() -> None:
    page = parse_item_page("", title="不存在")
    assert page.title == "不存在"
    assert page.name is None
    assert page.item_id is None
    assert page.icon_id is None
    assert page.rarity is None
    assert page.category is None
    assert page.description is None
    assert page.usage is None
    assert page.obtain is None
    assert page.recipe is None


def test_navigation_only_page_does_not_raise() -> None:
    page = parse_item_page("{{Navigator|道具一览}}\n{{道具导航}}\n[[分类:材料]]\n", title="导航页")
    assert page.name is None
    assert page.item_id is None
    assert page.recipe is None


def test_partial_item_template_does_not_raise() -> None:
    page = parse_item_page("{{道具信息\n|itemId=1\n}}", title="半页")
    assert page.item_id == "1"
    assert page.name is None
    assert page.rarity is None
    assert page.recipe is None


def test_parse_does_not_read_files(monkeypatch: pytest.MonkeyPatch) -> None:
    """纯函数：解析入口不碰网络与文件系统。"""

    def boom(*args: Any, **kwargs: Any) -> Any:  # pragma: no cover - 只在不纯时触发
        raise AssertionError("解析器不该访问外部资源")

    monkeypatch.setattr("builtins.open", boom)
    page = parse_item_page("{{道具信息\n|itemId=2\n}}", title="t")
    assert page.item_id == "2"
