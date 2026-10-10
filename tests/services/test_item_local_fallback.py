"""材料本机兜底（规格 §3.5）：PRTS 不可用时用 ``item_index.json`` 与常驻关 ``dropInfos``。"""

from __future__ import annotations

import asyncio

from maa_api.domain.errors import AppError, ErrorCode
from maa_api.services.game.items import ItemService


class DownPrts:
    """所有取数都失败的 PRTS 客户端。"""

    def __init__(self, code: ErrorCode = ErrorCode.EXTERNAL_SOURCE_UNAVAILABLE) -> None:
        self.code = code

    async def wikitext(self, titles):
        raise AppError(self.code, "PRTS 不可用")

    async def categories(self, titles):
        raise AppError(self.code, "PRTS 不可用")

    async def search(self, term, limit=5):
        raise AppError(self.code, "PRTS 不可用")


def async_test(func):
    def wrapper(*args, **kwargs):
        return asyncio.run(func(*args, **kwargs))

    wrapper.__name__ = func.__name__
    return wrapper


@async_test
async def test_item_falls_back_to_the_local_index() -> None:
    """PRTS 挂了也要给名字、itemId、稀有度、配方原料与哪些常驻关掉。"""
    result = await ItemService(prts=DownPrts()).get("固源岩组")

    assert result["item"]["item_id"] == "30013"
    assert result["item"]["name"] == "固源岩组"
    assert result["item"]["rarity"] == 2
    assert result["item"]["source"] == "resource"
    assert result["item"]["description"]
    assert result["recipe"]["source"] == "resource"
    assert result["recipe"]["materials"] == [{"name": "固源岩", "amount": 5}]
    # 本机索引没有龙门币与副产物，如实留空而不是编。
    assert result["recipe"]["lmd_cost"] is None
    assert result["recipe"]["byproducts"] == []
    assert result["drop_rates"] == []
    assert result["local_drop_stages"]
    assert {"stage_id", "code", "ap_cost", "drop_type"} <= set(result["local_drop_stages"][0])
    assert result["unavailable"] == ["recipe", "drop_rates"]
    assert result["fallback_reason"] == "EXTERNAL_SOURCE_UNAVAILABLE"
    assert result["untrusted"] is True


@async_test
async def test_item_fallback_accepts_item_id_and_reports_unknown_names() -> None:
    """按 itemId 也能兜底；本机也没有的名字要报错而不是给空壳。"""
    service = ItemService(prts=DownPrts())
    by_id = await service.get("30013")
    assert by_id["item"]["name"] == "固源岩组"

    try:
        await ItemService(prts=DownPrts()).get("不存在的材料xyz")
    except AppError as exc:
        assert exc.code is ErrorCode.GAME_DATA_NOT_FOUND
    else:  # pragma: no cover - 兜底必须报错
        raise AssertionError("未知材料在 PRTS 不可用时应报 GAME_DATA_NOT_FOUND")


@async_test
async def test_item_fallback_also_applies_to_invalid_responses() -> None:
    """「解析失败」与断网并列（§3.5 的不可用一栏），同样走本机兜底。"""
    result = await ItemService(
        prts=DownPrts(ErrorCode.EXTERNAL_RESPONSE_INVALID)
    ).get("固源岩组")
    assert result["item"]["item_id"] == "30013"
    assert result["fallback_reason"] == "EXTERNAL_RESPONSE_INVALID"
