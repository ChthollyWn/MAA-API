"""关卡情报（规格 §5.7）：本机索引定位 + PRTS 关卡页字段白名单。

先用本机 ``overview.json`` 的 ``resolve`` 规则定位关卡（代号、stageId、levelId、中文名都行），
再用 SMW 的 ``[[关卡id::…]]`` 反查 PRTS 页面，取 ``{{普通关卡信息}}`` / ``{{突袭关卡信息}}`` /
``{{敌方情报}}`` / ``{{关卡装置}}``。PRTS 不可用时只返回本机能给的字段，并把这些字段组写进
``unavailable``（§3.5：禁止冒充）。
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Callable, Mapping, Sequence

from maa_api.domain.errors import AppError, ErrorCode
from maa_api.services.activity_index import ActivityIndex, default_activity_index
from maa_api.services.external.envelope import SourceRef, build_envelope
from maa_api.services.external.prts import PrtsClient, id_condition, page_condition
from maa_api.services.prts_parse.stage import StagePage, parse_stage_page
from maa_api.settings import Settings, get_settings

__all__ = ["ALL_GROUPS", "StageService", "default_stage_service"]

#: `include` 的取值域（规格 §5.7）。
ALL_GROUPS = ("basic", "enemies", "devices", "drops", "map")

#: 结果里各字段组的字段白名单（`map` 由本机地块给，见 `_map_group`）。
_BASIC_FIELDS = (
    "code",
    "name",
    "stage_id",
    "level_id",
    "stage_type",
    "subtype",
    "difficulty",
    "recommend_level",
    "deploy_limit",
    "initial_cost",
    "max_cost",
    "life_points",
    "enemy_count",
    "map_size",
    "sanity",
    "description",
    "unlock",
    "area",
    "shortest_time",
    "challenge_condition",
    "challenge_practice_sanity",
    "has_challenge",
)


@dataclass(frozen=True, slots=True)
class StageService:
    """关卡情报的领域服务。"""

    prts: PrtsClient | None = None
    index: ActivityIndex | None = None
    drops: Any = None
    settings_provider: Callable[[], Settings] = get_settings

    @property
    def client(self) -> PrtsClient:
        return self.prts or PrtsClient()

    @property
    def local_index(self) -> ActivityIndex:
        return self.index or default_activity_index()

    @property
    def drop_service(self) -> Any:
        """掉率服务（M26 接入）；缺省用模块级默认门面。"""
        if self.drops is not None:
            return self.drops
        from maa_api.services.game.drops import default_drop_service

        return default_drop_service()

    async def get(
        self,
        stage: str,
        *,
        include: Sequence[str] | None = None,
        client_type: str | None = None,
    ) -> dict[str, Any]:
        """取一关的情报；多个候选时只返回候选列表，不替模型选。"""
        groups = _normalize_groups(include)
        result = self.local_index.find_stage(stage)
        matches = _dedupe_candidates(result.get("matches") or [])
        if not matches:
            raise AppError(
                ErrorCode.GAME_DATA_NOT_FOUND,
                f"找不到关卡：{stage}。可以用代号（7-18）、stageId（main_07-16）、"
                "levelId 或中文关卡名再试",
                {"stage": stage},
            )
        if len(matches) > 1:
            # 同名代号跨期撞车时让模型自己挑（规格 §5.7）。
            return {
                "query": stage,
                "stage": None,
                "matched_by": result.get("matched_field"),
                "candidates": matches,
                "candidates_total": len(matches),
                "hint": "有多个同名候选，用 stageId 再查一次以确定是哪一关。",
                **build_envelope(),
            }

        target = matches[0]
        payload: dict[str, Any] = {
            "query": stage,
            "stage": {
                "code": target.get("code"),
                "name": target.get("name"),
                "stage_id": target.get("stage_id"),
                "level_id": target.get("level_id"),
                "activity": target.get("activity"),
            },
            "matched_by": result.get("matched_field"),
            "candidates": matches,
            "candidates_total": 1,
        }
        unavailable: list[str] = []
        sources: list[SourceRef] = []

        page: StagePage | None = None
        try:
            located = await self._locate_page(target)
            if located is not None:
                title, batch = located
                sources.extend(batch.source_refs())
                page = parse_stage_page(batch.pages[0].content, title=title)
        except AppError as exc:
            if exc.code in {
                ErrorCode.EXTERNAL_SOURCE_DISABLED,
                ErrorCode.EXTERNAL_SOURCE_UNAVAILABLE,
                ErrorCode.EXTERNAL_SOURCE_RATE_LIMITED,
            }:
                # 降级：别把整个工具调用变成错误，只是这些字段组取不到（§3.5）。
                unavailable.extend(group for group in groups if group not in {"map"})
            else:
                raise

        if page is not None:
            if "basic" in groups:
                payload["basic"] = _basic_group(page, target)
            if "enemies" in groups:
                payload["enemies"] = [ _enemy(enemy) for enemy in page.enemies ]
            if "devices" in groups:
                payload["devices"] = [ _device(device) for device in page.devices ]
            if "drops" in groups:
                payload["drops"] = await self._drops_group(page, target)
        else:
            if "basic" in groups:
                payload["basic"] = _basic_group(None, target)
                unavailable.append("basic")
            for group in ("enemies", "devices", "drops"):
                if group in groups:
                    unavailable.append(group)

        if "map" in groups:
            payload["map"] = _map_group(target)

        payload.update(
            build_envelope(
                sources,
                unavailable=_dedupe(unavailable),
                region_note=_region_note(client_type, self.settings_provider()),
            )
        )
        return payload

    async def _drops_group(
        self, page: StagePage, target: Mapping[str, Any]
    ) -> dict[str, Any]:
        """掉落：PRTS 的定性描述 + 企鹅物流的掉率（M26，规格 §6.1 第 3 条）。

        掉率取不到时只降级这一段：定性描述照常返回，`rates_unavailable` 说明原因。
        """
        payload: dict[str, Any] = {
            "first": list(page.first_drops),
            "normal": list(page.normal_drops),
            "source": "prts",
            "rates": [],
        }
        stage_id = target.get("stage_id")
        if not stage_id:
            payload["rates_unavailable"] = "没有 stageId，无法查企鹅物流的掉率"
            return payload
        try:
            payload["rates"] = await self.drop_service.stage_drop_rates(str(stage_id))
        except AppError as exc:
            if exc.code in {
                ErrorCode.EXTERNAL_SOURCE_DISABLED,
                ErrorCode.EXTERNAL_SOURCE_UNAVAILABLE,
                ErrorCode.EXTERNAL_SOURCE_RATE_LIMITED,
                ErrorCode.EXTERNAL_RESPONSE_INVALID,
                ErrorCode.GAME_DATA_NOT_FOUND,
            }:
                payload["rates_unavailable"] = f"企鹅物流取不到掉率（{exc.code}）"
            else:
                raise
        return payload

    async def _locate_page(
        self, target: Mapping[str, Any]
    ) -> tuple[str, Any] | None:
        """定位 PRTS 关卡页：SMW 反查 → `<代号> <中文名>` 标题 → 搜索。"""
        stage_id = target.get("stage_id")
        if stage_id:
            result = await self.client.ask(
                [id_condition("关卡id", str(stage_id))],
                printouts=["关卡id", "关卡名称"],
                limit=20,
                combine="or",
            )
            pages = self.client.smw_pages(result)
            if pages:
                title = pages[0].title
                batch = await self.client.wikitext([title])
                if batch.pages and not batch.pages[0].missing:
                    return batch.pages[0].title, batch

        code = target.get("code")
        name = target.get("name")
        if code and name:
            title = f"{code} {name}"
            batch = await self.client.wikitext([title])
            if batch.pages and not batch.pages[0].missing:
                return batch.pages[0].title, batch

        if code:
            found = await self.client.search(str(code), limit=5)
            for entry in found.query.get("search") or []:
                if not isinstance(entry, dict) or not entry.get("title"):
                    continue
                batch = await self.client.wikitext([str(entry["title"])])
                if batch.pages and not batch.pages[0].missing:
                    return batch.pages[0].title, batch
        return None


def _dedupe_candidates(matches: Sequence[Mapping[str, Any]]) -> list[dict[str, Any]]:
    """同一个 levelId 的多个 stageId 视为一关（实测 ``7-18`` 有 ``main_07-16#f#`` 变体）。

    保留 stageId 里不带 ``#`` 的那条——那是内核与作业用的正式 id。
    """
    best: dict[str, Mapping[str, Any]] = {}
    order: list[str] = []
    for match in matches:
        key = str(match.get("level_id") or match.get("stage_id") or "")
        current = best.get(key)
        if current is None:
            best[key] = match
            order.append(key)
            continue
        if "#" in str(current.get("stage_id")) and "#" not in str(match.get("stage_id")):
            best[key] = match
    return [dict(best[key]) for key in order]


def _normalize_groups(include: Sequence[str] | None) -> tuple[str, ...]:
    if include is None:
        return ALL_GROUPS
    cleaned: list[str] = []
    for group in include:
        if group not in ALL_GROUPS:
            raise AppError(
                ErrorCode.INVALID_PARAMETER,
                f"include 只能是 { '、'.join(ALL_GROUPS) } 的子集，收到 {group}",
                {"include": list(include)},
            )
        if group not in cleaned:
            cleaned.append(group)
    return tuple(cleaned) or ALL_GROUPS


def _basic_group(page: StagePage | None, target: Mapping[str, Any]) -> dict[str, Any]:
    """`basic` 组：PRTS 有就用 PRTS，缺的用本机索引补。"""
    payload: dict[str, Any] = {
        "code": target.get("code"),
        "name": target.get("name"),
        "stage_id": target.get("stage_id"),
        "level_id": target.get("level_id"),
        "source": "prts",
    }
    if page is None:
        payload["source"] = "resource"
        return payload
    for field in _BASIC_FIELDS:
        value = getattr(page, field, None)
        if value is not None and value != "" and value != ():
            payload[field] = value
    # PRTS 页面的标题就是关卡页名，模型据此说出处。
    payload.setdefault("code", target.get("code"))
    payload.setdefault("name", target.get("name"))
    payload["source"] = "prts"
    return payload


def _enemy(enemy: Any) -> dict[str, Any]:
    return {
        "name": enemy.name,
        "count": enemy.count,
        "level": enemy.level,
        "rank": enemy.rank,
    }


def _device(device: Any) -> dict[str, Any]:
    return {
        "name": device.name,
        "deployable": device.deployable,
        "skill": device.skill,
        "note": device.note,
    }


def _map_group(target: Mapping[str, Any]) -> dict[str, Any] | None:
    """本机地块摘要：尺寸、可部署格子数、红门与蓝门位置（规格 §5.7）。

    图仍然走 `send_stage_map`；这里只给模型能推理的摘要。
    """
    from maa_api.services.stage_layout import load_stage_layout

    query = target.get("stage_id") or target.get("code")
    if not query:
        return None
    try:
        layout = load_stage_layout(str(query))
    except AppError:
        return None
    tiles = layout.get("tiles") or []
    buildable = sum(
        1
        for row in tiles
        for tile in row
        if isinstance(tile, str) and len(tile) >= 2 and tile[1] in {"1", "2", "3"}
    )
    return {
        "width": layout.get("width"),
        "height": layout.get("height"),
        "buildable_tiles": buildable,
        "red_gates": [[int(x), int(y)] for x, y in (layout.get("start") or [])],
        "blue_gates": [[int(x), int(y)] for x, y in (layout.get("end") or [])],
        "source": "resource",
        "note": "坐标以左上角为原点、单位是格子；完整布局用 send_stage_map。",
    }


def _dedupe(values: Sequence[str]) -> list[str]:
    seen: dict[str, None] = {}
    for value in values:
        seen.setdefault(value, None)
    return list(seen)


def _region_note(client_type: str | None, settings: Settings) -> str | None:
    from maa_api.services.game.activities import FOREIGN_REGION_NOTE, is_cn_client

    resolved = client_type if client_type is not None else settings.channel.client_type
    return None if is_cn_client(resolved) else FOREIGN_REGION_NOTE


#: 进程内共享的默认门面。
_DEFAULT: StageService | None = None


def default_stage_service() -> StageService:
    global _DEFAULT
    if _DEFAULT is None:
        _DEFAULT = StageService()
    return _DEFAULT


#: 供工具层引用：`page_condition` 与 `id_condition` 都在客户端里校验。
_PAGE_CONDITION = page_condition
