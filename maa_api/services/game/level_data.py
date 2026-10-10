"""关卡文件的领域层（规格 §9.3）：按代号 / stageId 定位关卡，取拆包文件并打包。

数据链：

1. 定位关卡：``StageService`` 的本机索引（``resolve`` 规则：代号、stageId、levelId、中文名
   都行），多命中时只要 ``level_id`` 一致就取 ``stageId`` 不带 ``#`` 的那条（与
   ``get_stage`` 同款口径）；
2. 取文件：``GamedataClient.level(level_id)``（镜像顺序 + GitHub 兜底 + ``level`` 档缓存）；
3. 解析：``prts_parse.gamedata.parse_level_file``（纯函数）；
4. 打包：``pack_level_data``（≤ 24 KB，先合并波次再截断路线，截断写进 ``truncated``）。

外部来源不可用（关掉、限流、网络故障）时**不降级成空图**：抛 ``AppError``，让工具层把它
变成 ``unavailable`` 或错误（§3.5：宁可报「取不到」，也不假装）。找不到关卡报
``GAME_DATA_NOT_FOUND``；来源上确实没有这份文件也报 ``GAME_DATA_NOT_FOUND``（客户端区分）。
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Callable, Mapping, Sequence

from maa_api.domain.errors import AppError, ErrorCode
from maa_api.services.activity_index import ActivityIndex, default_activity_index
from maa_api.services.external.envelope import build_envelope
from maa_api.services.external.gamedata import GamedataClient, validate_level_id
from maa_api.services.game.stages import StageService
from maa_api.services.prts_parse.gamedata import (
    MAX_LEVEL_BYTES,
    pack_level_data,
    parse_level_file,
)
from maa_api.settings import Settings, get_settings

__all__ = [
    "ALL_GROUPS",
    "WAVE_DETAILS",
    "StageLevelService",
    "default_stage_level_service",
]

#: `include` 的取值域（规格 §9.3）。
ALL_GROUPS = ("options", "map", "routes", "waves")

#: `wave_detail` 的取值域。
WAVE_DETAILS = ("summary", "full")


@dataclass(frozen=True, slots=True)
class StageLevelService:
    """关卡文件（拆包数据）的领域服务；依赖可注入。"""

    gamedata: GamedataClient | None = None
    stages: StageService | None = None
    index: ActivityIndex | None = None
    settings_provider: Callable[[], Settings] = get_settings

    @property
    def client(self) -> GamedataClient:
        if self.gamedata is not None:
            return self.gamedata
        from maa_api.services.external.gamedata import default_gamedata_client

        return default_gamedata_client()

    @property
    def stage_service(self) -> StageService:
        if self.stages is not None:
            return self.stages
        from maa_api.services.game.stages import default_stage_service

        return default_stage_service()

    @property
    def local_index(self) -> ActivityIndex:
        return self.index or default_activity_index()

    async def level_data(
        self,
        stage: str,
        *,
        include: Sequence[str] | None = None,
        wave_detail: str = "summary",
    ) -> dict[str, Any]:
        """取一关的地图、路线与波次（规格 §9.3 的返回形状）。

        :param stage: 关卡代号（``1-7``）、stageId（``main_01-07``）、levelId 或中文名。
        :param include: ``options`` / ``map`` / ``routes`` / ``waves`` 的子集，默认全部。
        :param wave_detail: ``summary``（默认，按片段合并）或 ``full``。
        :raises AppError: ``GAME_DATA_NOT_FOUND``（找不到关卡或来源上没有文件）、
            ``INVALID_PARAMETER``（include / wave_detail 不合法）、
            ``EXTERNAL_*``（来源被关掉、限流、取不到）。
        """
        groups = _normalize_groups(include)
        detail = _normalize_detail(wave_detail)
        target = self._locate(stage)
        level_id = validate_level_id(str(target.get("level_id") or ""))
        data = await self.client.level(level_id)
        level = parse_level_file(data.data)
        packed = pack_level_data(level, include=groups, wave_detail=detail)
        payload: dict[str, Any] = {
            "query": stage,
            "stage": {
                "code": target.get("code"),
                "name": target.get("name"),
                "stage_id": target.get("stage_id"),
                "level_id": level_id,
            },
            "level_id": level_id,
            "include": list(groups),
            "wave_detail": detail,
            "max_bytes": MAX_LEVEL_BYTES,
            "enemy_names_note": (
                "关卡文件只带敌人 key（enemy_xxx），不带名字；"
                "要名字用 get_enemy(key) 查 PRTS。"
            ),
        }
        payload.update(packed)
        payload["source_ref"] = data.source_ref(title=level_id).as_dict()
        payload.update(
            build_envelope(
                [data.source_ref(title=level_id)],
                region_note=_region_note(self.settings_provider()),
            )
        )
        return payload

    # -- 定位 -------------------------------------------------------------
    def _locate(self, stage: str) -> Mapping[str, Any]:
        """按代号 / stageId / levelId / 中文名定位一条索引记录。"""
        query = str(stage or "").strip()
        if not query:
            raise AppError(ErrorCode.INVALID_PARAMETER, "stage 不能为空", {"stage": stage})
        found = self.local_index.find_stage(query)
        matches = [
            match
            for match in (found.get("matches") or [])
            if isinstance(match, Mapping) and match.get("level_id")
        ]
        if not matches:
            raise AppError(
                ErrorCode.GAME_DATA_NOT_FOUND,
                f"找不到关卡：{stage}。可以用代号（1-7）、stageId（main_01-07）、"
                "levelId 或中文关卡名再试",
                {"stage": query},
            )
        # 同一个 levelId 的 `#f#` 变体不是另一关：取 stageId 不带 `#` 的那条（与 get_stage 同款）。
        best = matches[0]
        for match in matches:
            if "#" in str(best.get("stage_id")) and "#" not in str(match.get("stage_id")):
                best = match
        if len(matches) > 1 and not _same_level(matches):
            codes = "、".join(str(item.get("code")) for item in matches[:5])
            raise AppError(
                ErrorCode.INVALID_PARAMETER,
                f"「{stage}」有多个不同关卡的候选（{codes}），请用 stageId 再查一次",
                {"stage": query, "candidates": [dict(item) for item in matches[:10]]},
            )
        return best


def _same_level(matches: Sequence[Mapping[str, Any]]) -> bool:
    """候选是不是同一关（levelId 去重后只剩一个）。"""
    return len({str(item.get("level_id")) for item in matches}) == 1


def _normalize_groups(include: Sequence[str] | None) -> tuple[str, ...]:
    if include is None:
        return ALL_GROUPS
    cleaned: list[str] = []
    for group in include:
        if group not in ALL_GROUPS:
            raise AppError(
                ErrorCode.INVALID_PARAMETER,
                f"include 只能是 {'、'.join(ALL_GROUPS)} 的子集，收到 {group}",
                {"include": list(include)},
            )
        if group not in cleaned:
            cleaned.append(group)
    return tuple(cleaned) or ALL_GROUPS


def _normalize_detail(wave_detail: str) -> str:
    detail = str(wave_detail or "").strip().lower()
    if detail not in WAVE_DETAILS:
        raise AppError(
            ErrorCode.INVALID_PARAMETER,
            f"wave_detail 只能是 {'、'.join(WAVE_DETAILS)}，收到 {wave_detail}",
            {"wave_detail": str(wave_detail)[:20]},
        )
    return detail


def _region_note(settings: Settings) -> str | None:
    from maa_api.services.game.activities import FOREIGN_REGION_NOTE, is_cn_client

    client_type = getattr(getattr(settings, "channel", None), "client_type", None)
    return None if is_cn_client(client_type) else FOREIGN_REGION_NOTE


#: 进程内共享的默认门面。
_DEFAULT: StageLevelService | None = None


def default_stage_level_service() -> StageLevelService:
    global _DEFAULT
    if _DEFAULT is None:
        _DEFAULT = StageLevelService()
    return _DEFAULT
