"""Resolve stage aliases through the optional native API and local resource index.

两级实现（M24 §4.4）：

1. **内核优先**：``AsstGetMapLevelKey`` 能把代号 / ``stageId`` / ``levelId`` 互查。
   它只在 macOS 构建里导出，其他平台回退。
2. **本地回退**：读 ``Arknights-Tile-Pos/overview.json``（含活动，4255 条），按
   ``stageId`` → ``levelId`` → ``code`` → 中文名精确匹配，全部大小写不敏感。

回退的索引在 M24 之前是 ``stages.json``（608 条，**只有常驻关**），活动关根本查不到；
而且代号跨期次重名时（``XX-08`` 可能属于好几期）旧实现静默取 ``stageId`` 字典序第一条，
会给出**别的期**的关卡。现在把全部候选按 ``stageId`` 排好一并返回：``resolved`` 仍是
第一条（兼容既有调用方），新增 ``candidates`` 与 ``candidates_total`` 供上层消歧。
"""

from __future__ import annotations

import json
import platform
from pathlib import Path
from typing import Any

from maa_api.core.registry import DEFAULT_CORE_ID
from maa_api.settings import REPO_ROOT, get_settings
from maa_api.services.activity_index import OVERVIEW_DIRECTORY, OVERVIEW_FILENAME

__all__ = ["StageResolver"]


class StageResolver:
    """Use MaaCore's experimental resolver, falling back to ``overview.json``."""

    def __init__(
        self,
        core_registry: Any = None,
        *,
        overview_path: str | Path | None = None,
        stages_path: str | Path | None = None,
    ):
        # ``stages_path`` 是 M23 及之前装配与测试用的名字，语义已换成「本地关卡索引」，
        # 与 ``overview_path`` 指向同一处；两个都传时以 ``overview_path`` 为准。
        self.core_registry = core_registry
        chosen = overview_path if overview_path is not None else stages_path
        self.overview_path = Path(chosen) if chosen is not None else None
        #: 旧名字保留，读的人不用改（内部只用 :attr:`overview_path`）。
        self.stages_path = self.overview_path

    async def resolve(self, key: str) -> dict[str, Any]:
        query = key.strip()
        if not query:
            return _empty(query, "resource")
        core_result = await self._resolve_with_core(query)
        if core_result is not None:
            # 内核一次只给一条：候选就是它自己，形状与回退路径保持一致。
            return {
                "query": query,
                "source": "core",
                "resolved": core_result,
                "candidates": [core_result],
                "candidates_total": 1,
            }
        candidates = self._resolve_from_overview(query)
        return {
            "query": query,
            "source": "resource",
            "resolved": candidates[0] if candidates else None,
            "candidates": candidates,
            "candidates_total": len(candidates),
        }

    async def _resolve_with_core(self, key: str) -> dict[str, Any] | None:
        if self.core_registry is None:
            return None
        try:
            client = self.core_registry.get(DEFAULT_CORE_ID)
            resolver = getattr(client, "resolve_stage", None)
            if not callable(resolver):
                return None
            result = await resolver(key)
        except Exception:
            return None
        if not isinstance(result, dict) or not any(result.values()):
            return None
        return {
            "code": result.get("code"),
            "stage_id": result.get("stage_id"),
            "level_id": result.get("level_id"),
            "name": result.get("name"),
        }

    def _resolve_from_overview(self, key: str) -> list[dict[str, Any]]:
        """本地关卡索引全部命中，按 ``stageId`` 排序。

        匹配顺序是「标识越精确越先」：``stageId`` → ``levelId`` → ``code`` → 中文名。
        同一级内大小写不敏感；某一级有命中就不再往下一级找——否则用中文名查到的关会被
        同名的代号命中淹没。
        """
        stages = self._load_overview()
        if stages is None:
            return []
        needle = key.casefold()
        # stageId 是唯一标识，用户给的就是它时**原样返回**（``main_01-07#f#`` 要求的就是
        # 带 ``#f#`` 的那条），不做同图合并。
        exact = [
            _candidate(stage)
            for stage in stages
            if str(stage.get("stageId", "")).casefold() == needle
        ]
        if exact:
            return sorted(exact, key=lambda item: str(item["stage_id"]))
        # 注意：overview.json 的值是驼峰（``stageId`` / ``levelId``），返回给上层才转蛇形。
        for field in ("levelId", "code", "name"):
            matches = [
                _candidate(stage)
                for stage in stages
                if isinstance(stage.get(field), str)
                and str(stage[field]).casefold() == needle
            ]
            if matches:
                return _deduplicated(matches)
        return []

    def _load_overview(self) -> list[dict[str, Any]] | None:
        try:
            payload = json.loads(self._overview_file().read_text(encoding="utf-8"))
        except (OSError, ValueError):
            return None
        if not isinstance(payload, dict):
            return None
        # overview.json 是 ``{f"{stageId}-{levelId}": {...}}``；值里带齐四个字段。
        return [item for item in payload.values() if isinstance(item, dict)]

    def _overview_file(self) -> Path:
        """把注入的路径折算成 ``overview.json``。

        装配与测试可以传目录、传 ``overview.json`` 本身，也可以沿用旧名字传同级的
        ``stages.json`` —— 后两种都折算到同一处，避免「参数叫 stages 却读 overview」
        这种只有读代码才知道的事。
        """
        if self.overview_path is None:
            return _core_resource_root() / OVERVIEW_DIRECTORY / OVERVIEW_FILENAME
        path = self.overview_path
        if path.is_dir():
            return path / OVERVIEW_DIRECTORY / OVERVIEW_FILENAME
        if path.name in {OVERVIEW_FILENAME, "stages.json"} and path.parent.name != OVERVIEW_DIRECTORY:
            return path.parent / OVERVIEW_DIRECTORY / OVERVIEW_FILENAME
        return path


def _candidate(stage: dict[str, Any]) -> dict[str, Any]:
    """overview 的一条 → 统一形状；缺字段一律 ``None``，不抛。"""
    code = stage.get("code")
    stage_id = stage.get("stageId")
    level_id = stage.get("levelId")
    name = stage.get("name")
    return {
        "code": str(code) if code is not None else None,
        "stage_id": str(stage_id) if stage_id is not None else None,
        "level_id": str(level_id) if level_id is not None else None,
        "name": str(name) if name is not None else None,
    }


def _deduplicated(matches: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """同一张图的多个 ``stageId`` 变体合成一条候选，按 ``stageId`` 字典序返回。

    真实数据里 ``main_01-07`` 与 ``main_01-07#f#`` 的 ``levelId`` 完全相同（``#f#``
    是同一张图的另一套判定，关卡图与中文名也一样），重复计入候选会把「候选数」虚高、
    让上层以为有两关。分组键取 ``levelId``（缺失时退回 ``code`` + ``stageId``）；组内
    保留 ``stageId`` 字典序最小的那条，因此无 ``#f#`` 的写法排在前面（``#`` 的字典序
    小于数字与字母）。
    """
    grouped: dict[Any, list[dict[str, Any]]] = {}
    for item in matches:
        key = item["level_id"] or (item["code"], item["stage_id"])
        grouped.setdefault(key, []).append(item)
    winners = [
        sorted(group, key=lambda item: str(item["stage_id"]))[0] for group in grouped.values()
    ]
    winners.sort(key=lambda item: str(item["stage_id"]))
    return winners


def _empty(query: str, source: str) -> dict[str, Any]:
    return {
        "query": query,
        "source": source,
        "resolved": None,
        "candidates": [],
        "candidates_total": 0,
    }


def _core_resource_root() -> Path:
    """MaaCore 的资源目录（``<maa_path>/resource``），不加载内核。"""
    settings = get_settings()
    if settings.maa_core_path:
        core_root = Path(settings.maa_core_path).expanduser()
        if not core_root.is_absolute():
            core_root = REPO_ROOT / core_root
    else:
        folder = {"Darwin": "Darwin", "Linux": "Linux", "Windows": "Win32"}.get(
            platform.system(), platform.system()
        )
        core_root = REPO_ROOT / "resource" / "lib" / "maa" / folder
    return core_root / "resource"
