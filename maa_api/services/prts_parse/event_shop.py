"""活动商店与关卡表代币掉落的解析（M88 T2；口径见规格 §4.5.7）。

纯函数：wikitext 进，结构出；不联网、不读盘、不导入 ``maa_api.services.economy``。

- ``parse_event_shop``：取 ``{{#invoke:EventShopList|list}}`` 的 ``token_alias`` 与 ``data``，
  按 ``;;`` 切字段解析商品；页面没有商店小节时返回 ``ParsedShop(token=None, goods=())``。
- ``parse_token_drops``：按 ``|-`` 切关卡表的行，同一行同时有 ``{{关卡链接|<代号>}}`` 与
  ``{{道具图标|理智|<数字>|`` 才算一关；常规代币掉落是第二个位置参数为空的
  ``{{关卡报酬|<代币>||…|n=<数字>}}``（``三星获得`` 是首次掉落，不算）。
- ``regular_token``：页面没有商店时，从关卡表里第一个「第二位置参数为空」的
  ``{{关卡报酬|…}}`` 取代币名（供 ``load_events`` 兜底）。

模板一律用 :mod:`maa_api.services.prts_parse.wikitext` 的切分设施解析，不手写花括号配对。
"""

from __future__ import annotations

import re
from dataclasses import dataclass

from maa_api.services.prts_parse.wikitext import (
    Template,
    find_templates,
    parse_templates,
    template_param,
)

__all__ = [
    "EVENT_SHOP_TEMPLATE",
    "ParsedShop",
    "ParsedShopGood",
    "TokenDrop",
    "parse_event_shop",
    "parse_token_drops",
    "regular_token",
]

#: 商店模板名（实测：``{{#invoke:EventShopList|list …}}``）。
EVENT_SHOP_TEMPLATE = "#invoke:EventShopList"

#: 商品行的分区标题标记（``data`` 行的第一个字段）。
_SECTION_MARKER = "X"

#: 每份数量：``<small>'''×5000'''</small>``。
_BUNDLE_RE = re.compile(r"<small>\s*'''\s*×\s*(\d+)\s*'''\s*</small>")

#: 物品模板：位置参数 0 是物品名。
_ITEM_TEMPLATES = frozenset({"道具图标", "家具"})

#: 不是材料、整行跳过的模板（家具与皮肤）。
_SKIP_TEMPLATES = ("{{家具|", "{{皮肤头像|")


@dataclass(frozen=True, slots=True)
class TokenDrop:
    """一关的理智消耗与每次三星通关的常规代币掉落。"""

    stage_code: str
    ap_cost: int
    tokens: int


@dataclass(frozen=True, slots=True)
class ParsedShopGood:
    """商店里的一项商品；``stock is None`` 表示无限库存。"""

    item: str
    bundle: int
    stock: int | None
    price: int
    section: str


@dataclass(frozen=True, slots=True)
class ParsedShop:
    """商店解析结果；页面没有商店小节时 ``token is None``、``goods == ()``。"""

    token: str | None
    goods: tuple[ParsedShopGood, ...]


def parse_event_shop(content: str) -> ParsedShop:
    """解析 ``{{#invoke:EventShopList|list}}`` 里的代币名与商品；没有商店返回空结果。"""
    templates = find_templates(content, EVENT_SHOP_TEMPLATE, include_nested=False)
    if not templates:
        return ParsedShop(token=None, goods=())

    template = templates[0]
    token = (template_param(template, "token_alias") or "").removeprefix("带框_") or None
    data = template_param(template, "data") or ""

    goods: list[ParsedShopGood] = []
    section = ""
    for raw_line in data.splitlines():
        line = raw_line.strip()
        if not line:
            continue
        fields = [field.strip() for field in line.split(";;")]
        if fields[0] == _SECTION_MARKER:
            section = _section_name(fields[1] if len(fields) > 1 else "")
            continue
        good = _parse_good(fields, section=section)
        if good is not None:
            goods.append(good)
    return ParsedShop(token=token, goods=tuple(goods))


def parse_token_drops(content: str, token: str) -> dict[str, TokenDrop]:
    """关卡表里每关的 ``(理智, 常规代币掉落)``；没有常规代币掉落的关不收。"""
    drops: dict[str, TokenDrop] = {}
    for row in content.split("|-"):
        stage_code = _stage_code(row)
        if stage_code is None:
            continue
        ap_cost = _sanity_cost(row)
        if ap_cost is None:
            continue
        tokens = _regular_token_amount(row, token)
        if tokens is None:
            continue
        drops[stage_code] = TokenDrop(stage_code=stage_code, ap_cost=ap_cost, tokens=tokens)
    return drops


def regular_token(content: str) -> str | None:
    """关卡表里第一个「第二位置参数为空」的 ``{{关卡报酬|…}}`` 的代币名。"""
    for template in parse_templates(content, include_nested=False):
        if template.name != "关卡报酬":
            continue
        if (template.arg(1) or "").strip():
            continue
        name = (template.arg(0) or "").strip()
        if name:
            return name
    return None


# -- 商店 -------------------------------------------------------------------
def _section_name(field: str) -> str:
    """分区名取第二个字段里 ``{{`` 之前的文本（``往期复刻 {{popup|…}}`` → ``往期复刻``）。"""
    return field.split("{{", 1)[0].strip()


def _parse_good(fields: list[str], *, section: str) -> ParsedShopGood | None:
    """``<标记>;;<物品>;;<库存>;;<单价>;;<颜色>`` 的一行；形状不认识时返回 ``None``。"""
    if len(fields) < 4:
        return None
    item_field = fields[1]
    # 行首是 0/1 兑换标记，跳过判据必须看物品字段（整行 startswith 会漏掉家具与皮肤）。
    if item_field.startswith(_SKIP_TEMPLATES):
        return None
    item = _item_name(item_field)
    if not item:
        return None
    stock_text = fields[2].strip()
    if stock_text == "∞":
        stock: int | None = None
    else:
        stock = _int(stock_text)
        if stock is None:
            return None
    price = _int(fields[3])
    if price is None:
        return None
    bundle_match = _BUNDLE_RE.search(item_field)
    bundle = int(bundle_match.group(1)) if bundle_match is not None else 1
    return ParsedShopGood(
        item=item, bundle=bundle, stock=stock, price=price, section=section
    )


def _item_name(field: str) -> str | None:
    """物品字段里 ``{{道具图标|…}}`` / ``{{家具|…}}`` 的位置参数 0。"""
    for template in parse_templates(field, include_nested=False):
        if template.name in _ITEM_TEMPLATES:
            name = (template.arg(0) or "").strip()
            if name:
                return name
    return None


def _int(value: str) -> int | None:
    try:
        return int(value.strip())
    except (AttributeError, ValueError):
        return None


# -- 关卡表 -----------------------------------------------------------------
def _stage_code(row: str) -> str | None:
    """行里的 ``{{关卡链接|<代号>}}`` 代号。"""
    for template in parse_templates(row, include_nested=False):
        if template.name != "关卡链接":
            continue
        code = (template.arg(0) or "").strip()
        if code:
            return code
    return None


def _sanity_cost(row: str) -> int | None:
    """行里的 ``{{道具图标|理智|<数字>|…}}`` 理智消耗。"""
    for template in parse_templates(row, include_nested=False):
        if template.name != "道具图标" or (template.arg(0) or "").strip() != "理智":
            continue
        value = _int(template.arg(1) or "")
        if value is not None:
            return value
    return None


def _regular_token_amount(row: str, token: str) -> int | None:
    """行里第二位置参数为空的 ``{{关卡报酬|<token>||…|n=<数字>}}`` 的 ``n``。"""
    for template in parse_templates(row, include_nested=False):
        if template.name != "关卡报酬":
            continue
        if (template.arg(0) or "").strip() != token.strip():
            continue
        if (template.arg(1) or "").strip():
            continue
        value = _int(template.param("n") or "")
        if value is not None:
            return value
    return None
