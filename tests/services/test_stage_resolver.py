"""Stage resolution prefers the native map and falls back to local stage data."""

from __future__ import annotations

import asyncio
import importlib
import importlib.util
import json


def _resolver(stages_path, core_registry=None):
    module_name = "maa_api.services.stage_resolver"
    assert importlib.util.find_spec(module_name) is not None, (
        "stage resolver service must be implemented"
    )
    module = importlib.import_module(module_name)
    return module.StageResolver(core_registry, stages_path=stages_path)


def test_resource_fallback_resolves_stage_code_and_stage_id(tmp_path) -> None:
    stages_path = tmp_path / "stages.json"
    stages_path.write_text(
        json.dumps(
            [
                {"code": "1-7", "stageId": "main_01-07"},
                {"code": "CE-6", "stageId": "wk_melee_6"},
            ]
        ),
        encoding="utf-8",
    )
    resolver = _resolver(stages_path)

    first = asyncio.run(resolver.resolve("1-7"))
    second = asyncio.run(resolver.resolve("WK_MELEE_6"))

    assert first == {
        "query": "1-7",
        "source": "resource",
        "resolved": {"code": "1-7", "stage_id": "main_01-07", "level_id": None, "name": None},
    }
    assert second["resolved"]["code"] == "CE-6"
    assert second["resolved"]["stage_id"] == "wk_melee_6"


def test_native_stage_mapping_takes_precedence_over_local_resource(tmp_path) -> None:
    stages_path = tmp_path / "stages.json"
    stages_path.write_text(
        json.dumps([{"code": "1-7", "stageId": "main_01-07"}]),
        encoding="utf-8",
    )

    class Core:
        async def resolve_stage(self, key: str):
            assert key == "Alias-1-7"
            return {
                "code": "1-7",
                "stage_id": "main_01-07",
                "level_id": "level-main-01-07",
                "name": "切城",
            }

    class Registry:
        def get(self, core_id: str):
            assert core_id == "default"
            return Core()

    resolver = _resolver(stages_path, Registry())
    result = asyncio.run(resolver.resolve("Alias-1-7"))

    assert result == {
        "query": "Alias-1-7",
        "source": "core",
        "resolved": {
            "code": "1-7",
            "stage_id": "main_01-07",
            "level_id": "level-main-01-07",
            "name": "切城",
        },
    }


def test_unknown_stage_is_reported_without_inventing_an_alias(tmp_path) -> None:
    stages_path = tmp_path / "stages.json"
    stages_path.write_text("[]", encoding="utf-8")
    resolver = _resolver(stages_path)

    result = asyncio.run(resolver.resolve("not-a-stage"))

    assert result == {"query": "not-a-stage", "source": "resource", "resolved": None}
