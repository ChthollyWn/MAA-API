"""M88 T2：活动商店（``EventShopList``）与关卡表代币掉落的纯解析。

夹具是 PRTS 活动页的真实裁剪快照（来源与抓取日期见 ``tests/fixtures/prts/README.md``）：

- ``activity_zuori_shop.wikitext``：附录 A 的《昨日海》片段（活动信息七字段、YW-1–YW-8 与
  YW-EX-1 关卡表、``==劳工配给所==`` 的 ``{{#invoke:EventShopList|list}}`` 整段）；
- ``activity_zuori.wikitext``：既有快照，含 ``YW-ST-1``（纯剧情，无理智消耗）与常规代币掉落；
- ``activity_vec03.wikitext``：既有快照，没有商店小节。

测试只读固定夹具、不联网、不读盘（除夹具本身）。
"""

from __future__ import annotations

from datetime import datetime
from pathlib import Path

import pytest

from maa_api.services.prts_parse.activity import CN_TIMEZONE, parse_activity_page
from maa_api.services.prts_parse.event_shop import (
    ParsedShop,
    TokenDrop,
    parse_event_shop,
    parse_token_drops,
)

FIXTURES = Path(__file__).resolve().parents[1] / "fixtures" / "prts"

TOKEN = "“未来瞻望镜”"


def wikitext(name: str) -> str:
    return (FIXTURES / name).read_text(encoding="utf-8")


@pytest.fixture(scope="module")
def shop_content() -> str:
    """附录 A 的《昨日海》片段（商店 + 关卡表 + 活动信息）。"""
    return wikitext("activity_zuori_shop.wikitext")


@pytest.fixture(scope="module")
def zuori_content() -> str:
    """既有《昨日海》快照（含 YW-ST-1 与常规代币掉落，没有 EventShopList）。"""
    return wikitext("activity_zuori.wikitext")


@pytest.fixture(scope="module")
def vec03_content() -> str:
    """既有《矢量突破/03》快照（没有商店小节）。"""
    return wikitext("activity_vec03.wikitext")


def test_shop_token_and_sections(shop_content: str) -> None:
    shop = parse_event_shop(shop_content)

    assert shop.token == TOKEN
    sections = {good.section for good in shop.goods}
    assert {"金色阶段", "紫色阶段", "灰色阶段", "无限库存区"} <= sections
    assert "往期复刻" not in sections


def test_shop_items_stock_price_bundle(shop_content: str) -> None:
    shop = parse_event_shop(shop_content)

    assert len(shop.goods) == 31
    by_item_section = {(good.item, good.section): good for good in shop.goods}

    iron = by_item_section[("异铁块", "金色阶段")]
    assert (iron.bundle, iron.stock, iron.price) == (1, 10, 40)
    module_block = by_item_section[("模组数据块", "金色阶段")]
    assert (module_block.bundle, module_block.stock, module_block.price) == (1, 2, 75)
    ester = by_item_section[("聚酸酯组", "紫色阶段")]
    assert (ester.bundle, ester.stock, ester.price) == (1, 15, 8)
    lmd = by_item_section[("龙门币", "紫色阶段")]
    assert (lmd.bundle, lmd.stock, lmd.price) == (5000, 100, 7)
    exp_record = by_item_section[("高级作战记录", "紫色阶段")]
    assert (exp_record.bundle, exp_record.stock, exp_record.price) == (2, 25, 5)
    furniture_part = by_item_section[("家具零件", "灰色阶段")]
    assert (furniture_part.bundle, furniture_part.stock, furniture_part.price) == (10, 200, 2)
    rma = by_item_section[("RMA70-12", "无限库存区")]
    assert (rma.bundle, rma.stock, rma.price) == (1, None, 40)
    lmd_infinite = by_item_section[("龙门币", "无限库存区")]
    assert (lmd_infinite.bundle, lmd_infinite.stock, lmd_infinite.price) == (20, None, 1)


def test_shop_skips_furniture_and_skins(shop_content: str) -> None:
    names = {good.item for good in parse_event_shop(shop_content).goods}

    assert "全域广播扬声器" not in names
    assert "流星专属时装" not in names
    assert "家具零件" in names


def test_token_drops_regular_only(shop_content: str, zuori_content: str) -> None:
    drops = parse_token_drops(shop_content, TOKEN)

    assert drops["YW-1"] == TokenDrop("YW-1", 9, 9)
    assert drops["YW-3"] == TokenDrop("YW-3", 12, 12)
    assert drops["YW-6"] == TokenDrop("YW-6", 21, 21)
    assert drops["YW-8"] == TokenDrop("YW-8", 21, 21)
    assert len(drops) == 8
    # 首次掉落的 n=80 不算；EX 关没有常规代币掉落。
    assert "YW-EX-1" not in drops
    # 纯剧情关没有 {{道具图标|理智|…}}。
    assert "YW-ST-1" not in parse_token_drops(zuori_content, TOKEN)


def test_page_without_shop(vec03_content: str) -> None:
    assert parse_event_shop(vec03_content) == ParsedShop(token=None, goods=())


def test_activity_page_deadline(shop_content: str) -> None:
    parsed = parse_activity_page(shop_content, page_title="昨日海")

    assert parsed.end_at == datetime(2026, 10, 23, 3, 59, tzinfo=CN_TIMEZONE)
    assert parsed.exchange_end_at == datetime(2026, 10, 30, 3, 59, tzinfo=CN_TIMEZONE)
