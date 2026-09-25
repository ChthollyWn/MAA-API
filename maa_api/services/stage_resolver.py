"""Resolve stage aliases through the optional native API and local resource index."""

from __future__ import annotations

import json
import platform
from pathlib import Path
from typing import Any

from maa_api.core.registry import DEFAULT_CORE_ID
from maa_api.settings import REPO_ROOT, get_settings

__all__ = ["StageResolver"]


class StageResolver:
    """Use MaaCore's experimental resolver, falling back to ``stages.json``."""

    def __init__(self, core_registry: Any = None, *, stages_path: str | Path | None = None):
        self.core_registry = core_registry
        self.stages_path = Path(stages_path) if stages_path is not None else None

    async def resolve(self, key: str) -> dict[str, Any]:
        query = key.strip()
        if not query:
            return {"query": query, "source": "resource", "resolved": None}
        core_result = await self._resolve_with_core(query)
        if core_result is not None:
            return {"query": query, "source": "core", "resolved": core_result}
        return {
            "query": query,
            "source": "resource",
            "resolved": self._resolve_from_resource(query),
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

    def _resolve_from_resource(self, key: str) -> dict[str, str | None] | None:
        try:
            stages = json.loads(self._stages_path().read_text(encoding="utf-8"))
        except (OSError, ValueError):
            return None
        if not isinstance(stages, list):
            return None
        query = key.casefold()
        for stage in stages:
            if not isinstance(stage, dict):
                continue
            code = stage.get("code")
            stage_id = stage.get("stageId")
            aliases = {
                str(value).casefold()
                for value in (code, stage_id)
                if value is not None
            }
            if query not in aliases:
                continue
            return {
                "code": str(code) if code is not None else None,
                "stage_id": str(stage_id) if stage_id is not None else None,
                "level_id": None,
                "name": None,
            }
        return None

    def _stages_path(self) -> Path:
        if self.stages_path is not None:
            return self.stages_path
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
        return core_root / "resource" / "stages.json"
