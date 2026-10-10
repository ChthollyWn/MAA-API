"""材料与道具（规格 §6.5）：PRTS 道具页 + 加工站配方 + 企鹅物流掉率前 5。

掉率来自企鹅物流；取不到时基础字段照常返回，掉率与配方的缺失进 ``unavailable``。
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any, Callable, Mapping

from maa_api.domain.errors import AppError, ErrorCode
from maa_api.services.external.envelope import SourceRef, build_envelope, merge_envelopes
from maa_api.services.external.prts import PrtsClient
from maa_api.services.prts_parse.item import ItemPage, parse_item_page
from maa_api.services.resource_index import iter_resource_roots
from maa_api.settings import Settings, get_settings

__all__ = ["ItemService", "default_item_service"]

#: 本机兜底用的两个资源文件（与 `resource_index` 的层序一致）。
_ITEM_INDEX = Path("item_index.json")
_STAGES = Path("stages.json")

#: 可以降级的外部错误：只影响掉率字段组（§3.5）。
_DEGRADABLE = frozenset(
    {
        ErrorCode.EXTERNAL_SOURCE_DISABLED,
        ErrorCode.EXTERNAL_SOURCE_UNAVAILABLE,
        ErrorCode.EXTERNAL_SOURCE_RATE_LIMITED,
        # 页面取回来但结构不符时同样退回本机 item_index（规格 §3.5 把「解析失败」
        # 与断网、限流并列）。
        ErrorCode.EXTERNAL_RESPONSE_INVALID,
    }
)


class ItemService:
    """材料情报的领域服务；``drops`` 由掉率服务注入（M26）。"""

    def __init__(
        self,
        *,
        prts: PrtsClient | None = None,
        drops: Any = None,
        settings_provider: Callable[[], Settings] = get_settings,
    ) -> None:
        self._prts = prts
        self._drops = drops
        self._settings_provider = settings_provider
        #: 本机索引与常驻关表的惰性缓存（兜底路径用）。
        self._item_index: Mapping[str, Mapping[str, Any]] | None = None
        self._stages: tuple[Mapping[str, Any], ...] | None = None

    @property
    def prts(self) -> PrtsClient:
        return self._prts or PrtsClient()

    @property
    def drops(self) -> Any:
        if self._drops is not None:
            return self._drops
        from maa_api.services.game.drops import default_drop_service

        return default_drop_service()

    async def get(self, name: str, *, rate_limit: int = 5, server: str | None = None) -> dict[str, Any]:
        """取一个材料的字段：基础、加工站配方、掉率前 N。

        PRTS 不可用时退回本机 ``item_index.json`` 与常驻关的 ``dropInfos``（规格 §3.5）：
        给名字、itemId、稀有度、描述、用途、配方原料与「哪些常驻关掉」，没有掉率。
        """
        try:
            page, sources = await self._load(name)
        except AppError as exc:
            if exc.code in _DEGRADABLE:
                return self._local_fallback(name, reason=str(exc.code))
            raise
        payload: dict[str, Any] = {
            "query": name,
            "item": {
                "title": page.title,
                "name": page.name,
                "item_id": page.item_id,
                "icon_id": page.icon_id,
                "rarity": page.rarity,
                "category": page.category,
                "description": page.description,
                "usage": page.usage,
                "obtain": page.obtain,
            },
            "recipe": _recipe(page),
            "drop_rates": [],
        }
        unavailable: list[str] = []
        source_refs: list[SourceRef] = list(sources)

        try:
            rows = await self.drops.top_rates(
                page.name or page.item_id or name, limit=rate_limit, server=server
            )
            payload["drop_rates"] = rows
            # `top_rates` 只回列表，来源信封由这里按许可说明补一条。
            source_refs.append(_penguin_ref(self.drops))
        except AppError as exc:
            if exc.code in _DEGRADABLE:
                unavailable.append("drop_rates")
            else:
                raise

        payload.update(build_envelope(source_refs, unavailable=unavailable))
        return payload

    async def _load(self, name: str) -> tuple[ItemPage, list[SourceRef]]:
        """定位道具页：直查/重定向 → 分类确认 → 搜索。"""
        batch = await self.prts.wikitext([name])
        page = batch.pages[0] if batch.pages else None
        if page is not None and not page.missing and await self._is_item(page.title):
            return parse_item_page(page.content, title=page.title), list(batch.source_refs())

        found = await self.prts.search(name, limit=5)
        for item in found.query.get("search") or []:
            if not isinstance(item, dict) or not isinstance(item.get("title"), str):
                continue
            title = str(item["title"])
            if not await self._is_item(title):
                continue
            candidate = await self.prts.wikitext([title])
            if candidate.pages and not candidate.pages[0].missing:
                return (
                    parse_item_page(candidate.pages[0].content, title=candidate.pages[0].title),
                    list(candidate.source_refs()),
                )
        raise AppError(
            ErrorCode.GAME_DATA_NOT_FOUND,
            f"找不到材料：{name}。可以试名称或 itemId",
            {"name": name},
        )

    async def _is_item(self, title: str) -> bool:
        try:
            mapping = await self.prts.categories([title])
        except AppError:
            return False
        categories = " ".join(mapping.get(title) or [])
        return any(marker in categories for marker in ("道具", "材料"))


    # -- 本机兜底（规格 §3.5）--------------------------------------------
    def _local_fallback(self, name: str, *, reason: str) -> dict[str, Any]:
        """本机 ``item_index.json`` + ``stages.json`` 的 ``dropInfos``。"""
        index = self._load_item_index()
        entry = _find_local_item(index, name)
        if entry is None:
            raise AppError(
                ErrorCode.GAME_DATA_NOT_FOUND,
                f"找不到材料：{name}。PRTS 也不可用（{reason}），本机索引里没有它",
                {"name": name, "reason": reason},
            )
        item_id, payload = entry
        formula = payload.get("formula")
        materials = [
            {
                "name": (index.get(str(material)) or {}).get("name") or str(material),
                "amount": int(count),
            }
            for material, count in (formula or {}).items()
            if isinstance(count, (int, float))
        ]
        return {
            "query": name,
            "item": {
                "title": None,
                "name": payload.get("name"),
                "item_id": item_id,
                "icon_id": _icon_id(payload.get("icon")),
                "rarity": payload.get("rarity") if isinstance(payload.get("rarity"), int) else None,
                "category": payload.get("classifyType"),
                "description": payload.get("description"),
                "usage": payload.get("usage"),
                "obtain": None,
                "source": "resource",
            },
            "recipe": (
                {
                    "product": payload.get("name"),
                    "product_count": 1,
                    "lmd_cost": None,
                    "mood_cost": None,
                    "station_level": None,
                    "byproduct_total": None,
                    "materials": materials,
                    "byproducts": [],
                    "source": "resource",
                    "note": "本机 item_index 的 formula 只有原料，龙门币与副产物要等 PRTS 恢复",
                }
                if materials
                else None
            ),
            "drop_rates": [],
            "local_drop_stages": self._local_drop_stages(item_id),
            "fallback_reason": reason,
            **build_envelope(
                [],
                unavailable=["recipe", "drop_rates"],
                region_note=None,
            ),
        }

    def _load_item_index(self) -> Mapping[str, Mapping[str, Any]]:
        """本机 `item_index.json`（itemId → 条目）；读不到返回空表。"""
        if self._item_index is not None:
            return self._item_index
        for root in iter_resource_roots():
            path = Path(root) / _ITEM_INDEX
            if not path.is_file():
                continue
            try:
                payload = json.loads(path.read_text(encoding="utf-8"))
            except (OSError, ValueError):
                continue
            if isinstance(payload, dict):
                self._item_index = {
                    str(key): value
                    for key, value in payload.items()
                    if isinstance(value, dict)
                }
                return self._item_index
        self._item_index = {}
        return self._item_index

    def _load_local_stages(self) -> tuple[Mapping[str, Any], ...]:
        """本机常驻关表的 ``dropInfos``（哪些关掉这个材料，没有掉率）。"""
        if self._stages is not None:
            return self._stages
        for root in iter_resource_roots():
            path = Path(root) / _STAGES
            if not path.is_file():
                continue
            try:
                payload = json.loads(path.read_text(encoding="utf-8"))
            except (OSError, ValueError):
                continue
            if isinstance(payload, list):
                self._stages = tuple(item for item in payload if isinstance(item, dict))
                return self._stages
        self._stages = ()
        return self._stages

    def _local_drop_stages(self, item_id: str) -> list[dict[str, Any]]:
        stages: list[dict[str, Any]] = []
        for stage in self._load_local_stages():
            drops = stage.get("dropInfos")
            if not isinstance(drops, list):
                continue
            if not any(
                isinstance(drop, dict) and str(drop.get("itemId")) == item_id
                for drop in drops
            ):
                continue
            stages.append(
                {
                    "stage_id": stage.get("stageId"),
                    "code": stage.get("code"),
                    "ap_cost": stage.get("apCost"),
                    "drop_type": next(
                        (
                            str(drop.get("dropType"))
                            for drop in drops
                            if isinstance(drop, dict)
                            and str(drop.get("itemId")) == item_id
                            and drop.get("dropType")
                        ),
                        None,
                    ),
                }
            )
        stages.sort(key=lambda item: (str(item.get("code") or "")))
        return stages[:20]


def _find_local_item(
    index: Mapping[str, Mapping[str, Any]], query: str
) -> tuple[str, Mapping[str, Any]] | None:
    """本机索引里按 itemId 或名字精确匹配（名字不区分大小写）。"""
    needle = query.strip()
    if needle in index:
        return needle, index[needle]
    lowered = needle.casefold()
    for item_id, payload in index.items():
        name = payload.get("name")
        if isinstance(name, str) and name.casefold() == lowered:
            return item_id, payload
    return None


def _icon_id(icon: Any) -> str | None:
    """``30013.png`` → ``30013``。"""
    if not isinstance(icon, str) or not icon:
        return None
    return icon.rsplit(".", 1)[0] if "." in icon else icon


def _recipe(page: ItemPage) -> dict[str, Any] | None:
    recipe = page.recipe
    if recipe is None:
        return None
    return {
        "product": recipe.product,
        "product_count": recipe.product_count,
        "lmd_cost": recipe.lmd_cost,
        "mood_cost": recipe.mood_cost,
        "station_level": recipe.station_level,
        "byproduct_total": recipe.byproduct_total,
        "materials": [
            {"name": name, "amount": amount} for name, amount in recipe.materials
        ],
        "byproducts": [
            {"name": name, "rate": rate} for name, rate in recipe.byproducts
        ],
    }


def _penguin_ref(drops: Any) -> SourceRef:
    """企鹅物流的来源出处；许可说明写进 title（规格 §6.5 要求注明 CC BY-NC 4.0）。"""
    return SourceRef(
        kind="penguin",
        label="企鹅物流",
        title="企鹅物流（数据许可 CC BY-NC 4.0）",
        url="https://penguin-stats.io/",
    )


#: 进程内共享的默认门面。
_DEFAULT: ItemService | None = None


def default_item_service() -> ItemService:
    global _DEFAULT
    if _DEFAULT is None:
        _DEFAULT = ItemService()
    return _DEFAULT


#: 供后续里程碑合并多来源信封用。
_MERGE = merge_envelopes
