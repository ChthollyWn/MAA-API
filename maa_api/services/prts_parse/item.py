"""材料页解析（规格 §6.5，M26 Task 4）：`{{道具信息}}` + `{{道具配方/加工站}}`。

只做纯函数：wikitext 进，结构出，不联网、不读盘。

实测（2026-10-10，固源岩组 / 聚酸酯组）：

- ``{{道具信息}}``：``名称``、``itemId=30013``、``iconId``、``描述``、``用途``、``获得方式``、
  ``稀有度=2``、``分类=材料``（另有 ``id`` 是页面内部编号，不进模型）。
- ``{{道具配方/加工站}}``：``产物``、``产物数量``、``龙门币消耗``、``心情消耗``、
  ``加工站等级需求``、``副产物总概率=10``、若干 ``原料N`` / ``原料N数量`` 与
  ``副产物N`` / ``副产物N掉率``。

实现裁决（规格没写死、由本模块定）：

1. 加工站配方的原料是**独立键** ``原料N`` / ``原料N数量``，与干员页的 ``{{材料消耗}}``
   无关，所以本模块不依赖 ``prts_parse/costs.py``（``costs.py`` 还在并行开发）。
2. ``原料N数量`` 缺失或非数字时按 ``1`` 计（页面上写 1 份时会省略数量键）；``原料N``
   为空串的槽整个跳过。
3. 掉率与 ``副产物总概率`` 是**百分比数值**（``26.3`` 就是 26.3%，不是小数），按 ``float``
   读取；``稀有度``、``产物数量``、``龙门币消耗``、``心情消耗``、``加工站等级需求``、
   原料数量按 ``int`` 读取；解析不了一律 ``None``（不抛异常）。
4. 原料与副产物按 ``N`` 的数字升序（``原料10`` 排在 ``原料2`` 之后），不做字符串排序。
5. ``recipe`` 只在页面里出现 ``{{道具配方/加工站}}`` 时才不是 ``None``；模板在但字段缺时
   返回字段为 ``None`` / 空元组的 :class:`CraftRecipe`。
6. ``描述`` / ``用途`` / ``获得方式`` 与敌人页一样走 ``clean_text``（去链接与展示模板）。
"""

from __future__ import annotations

import re
from dataclasses import dataclass

from maa_api.services.prts_parse.activity import (
    clean_text,
    template_param,
)
from maa_api.services.prts_parse.wikitext import (
    Template,
    find_templates,
)

__all__ = [
    "INFO_TEMPLATE",
    "RECIPE_TEMPLATE",
    "CraftRecipe",
    "ItemPage",
    "parse_item_page",
]

#: 基础信息模板（实测）。
INFO_TEMPLATE = "道具信息"

#: 加工站配方模板（实测）。
RECIPE_TEMPLATE = "道具配方/加工站"

#: ``原料1`` / ``原料1数量`` / ``副产物1`` / ``副产物1掉率``。
_MATERIAL_KEY = re.compile(r"^原料(?P<index>\d+)(?P<field>数量)?$")
_BYPRODUCT_KEY = re.compile(r"^副产物(?P<index>\d+)(?P<field>掉率)?$")

#: 数字字段：整串必须是数字（``3`` / ``26.3``；``3w`` 这种留给材料消耗解析器）。
_NUMBER = re.compile(r"^[+-]?\d+(?:\.\d+)?$")


@dataclass(frozen=True, slots=True)
class CraftRecipe:
    """加工站配方：原料按 ``原料N`` 升序、副产物按 ``副产物N`` 升序。"""

    product: str | None = None
    product_count: int | None = None
    lmd_cost: int | None = None
    mood_cost: int | None = None
    station_level: int | None = None
    byproduct_total: float | None = None
    materials: tuple[tuple[str, int], ...] = ()
    byproducts: tuple[tuple[str, float | None], ...] = ()


@dataclass(frozen=True, slots=True)
class ItemPage:
    """材料页解析结果：``{{道具信息}}`` 的基础字段 + 可选的加工站配方。"""

    title: str
    name: str | None = None
    item_id: str | None = None
    icon_id: str | None = None
    rarity: int | None = None
    category: str | None = None
    description: str | None = None
    usage: str | None = None
    obtain: str | None = None
    recipe: CraftRecipe | None = None


def parse_item_page(content: str, *, title: str) -> ItemPage:
    """解析材料页：``{{道具信息}}`` 基础字段 + ``{{道具配方/加工站}}`` 配方。

    页面缺模板、缺字段、只有导航模板时一律返回带 ``None`` 的对象，不抛异常。
    """
    info = _first_template(content, INFO_TEMPLATE)
    recipe = _first_template(content, RECIPE_TEMPLATE)
    return ItemPage(
        title=str(title),
        name=_first_text(info, "名称"),
        item_id=_first_text(info, "itemId"),
        icon_id=_first_text(info, "iconId"),
        rarity=_to_int(template_param(info, "稀有度")),
        category=_first_text(info, "分类"),
        description=clean_text(template_param(info, "描述")),
        usage=clean_text(template_param(info, "用途")),
        obtain=clean_text(template_param(info, "获得方式")),
        recipe=_parse_recipe(recipe) if recipe is not None else None,
    )


# -- 内部 -----------------------------------------------------------------------


def _parse_recipe(template: Template) -> CraftRecipe:
    return CraftRecipe(
        product=_first_text(template, "产物"),
        product_count=_to_int(template_param(template, "产物数量")),
        lmd_cost=_to_int(template_param(template, "龙门币消耗")),
        mood_cost=_to_int(template_param(template, "心情消耗")),
        station_level=_to_int(template_param(template, "加工站等级需求")),
        byproduct_total=_to_float(template_param(template, "副产物总概率")),
        materials=_materials(template),
        byproducts=_byproducts(template),
    )


def _materials(template: Template) -> tuple[tuple[str, int], ...]:
    """``原料N`` / ``原料N数量`` 按数量键的数字升序；缺数量键按 1 计。"""
    groups = _numbered(template, _MATERIAL_KEY)
    rows = []
    for index in sorted(groups):
        values = groups[index]
        name = (values.get("name") or "").strip()
        if not name:
            continue
        count = _to_int(values.get("数量"))
        rows.append((name, 1 if count is None else count))
    return tuple(rows)


def _byproducts(template: Template) -> tuple[tuple[str, float | None], ...]:
    """``副产物N`` / ``副产物N掉率`` 按数字升序；缺掉率键是 ``None``。"""
    groups = _numbered(template, _BYPRODUCT_KEY)
    rows = []
    for index in sorted(groups):
        values = groups[index]
        name = (values.get("name") or "").strip()
        if not name:
            continue
        rows.append((name, _to_float(values.get("掉率"))))
    return tuple(rows)


def _numbered(template: Template, pattern: re.Pattern[str]) -> dict[int, dict[str, str]]:
    """把 ``键N<后缀>`` 形式的参数按 ``N`` 聚成 ``{N: {后缀: 值}}``（空后缀记为 ``name``）。"""
    groups: dict[int, dict[str, str]] = {}
    for arg in template.args:
        if not arg.key:
            continue
        match = pattern.match(arg.key)
        if match is None or not arg.value:
            continue
        field = match.group("field") or "name"
        groups.setdefault(int(match.group("index")), {})[field] = arg.value.strip()
    return groups


def _first_template(content: str, name: str) -> Template | None:
    templates = find_templates(content, name, include_nested=False)
    return templates[0] if templates else None


def _first_text(template: Template | None, key: str) -> str | None:
    raw = template_param(template, key)
    if raw is None:
        return None
    return raw.strip() or None


def _number(value: str | None) -> str | None:
    """数字字段的读取前置：去空白 + 形状检查，不合法返回 ``None``。"""
    if value is None:
        return None
    text = value.strip()
    return text if _NUMBER.match(text) else None


def _to_int(value: str | None) -> int | None:
    text = _number(value)
    if text is None:
        return None
    try:
        return int(float(text))
    except (TypeError, ValueError):  # pragma: no cover - 形状检查已挡住
        return None


def _to_float(value: str | None) -> float | None:
    text = _number(value)
    if text is None:
        return None
    try:
        return float(text)
    except (TypeError, ValueError):  # pragma: no cover - 形状检查已挡住
        return None
