"""关卡解析：内核优先，回退到本机 ``overview.json`` 关卡索引（M24 §4.4）。

回退索引在 M24 之前是只有常驻关的 ``stages.json``（608 条）；现在读含活动的
``Arknights-Tile-Pos/overview.json``（4255 条），并且代号跨期次重名时返回**全部**
候选，而不是静默取字典序第一条。
"""

from __future__ import annotations

import asyncio
import importlib
import importlib.util
import json
from pathlib import Path


def _resolver(overview_path, core_registry=None, **kwargs):
    module_name = "maa_api.services.stage_resolver"
    assert importlib.util.find_spec(module_name) is not None, (
        "stage resolver service must be implemented"
    )
    module = importlib.import_module(module_name)
    return module.StageResolver(core_registry, overview_path=overview_path, **kwargs)


def _overview(tmp_path: Path, entries: list[dict]) -> Path:
    """写一份 overview.json（``{键: {...}}``）到 ``Arknights-Tile-Pos/`` 下。"""
    directory = tmp_path / "Arknights-Tile-Pos"
    directory.mkdir(parents=True, exist_ok=True)
    path = directory / "overview.json"
    payload = {
        f"{item['stageId']}-{str(item.get('levelId', '')).replace('/', '-')}": item
        for item in entries
    }
    path.write_text(json.dumps(payload, ensure_ascii=False), encoding="utf-8")
    return path


def test_resource_fallback_resolves_stage_code_stage_id_and_name(tmp_path) -> None:
    path = _overview(
        tmp_path,
        [
            {
                "code": "1-7",
                "stageId": "main_01-07",
                "levelId": "obt/main/level_main_01-07",
                "name": "暴君",
            },
            {
                "code": "CE-6",
                "stageId": "wk_melee_6",
                "levelId": "obt/weekly/level_weekly_melee_6",
                "name": "实验原件押运",
            },
        ],
    )
    resolver = _resolver(path)

    by_code = asyncio.run(resolver.resolve("1-7"))
    by_stage_id = asyncio.run(resolver.resolve("WK_MELEE_6"))
    by_name = asyncio.run(resolver.resolve("实验原件押运"))

    expected = {
        "code": "CE-6",
        "stage_id": "wk_melee_6",
        "level_id": "obt/weekly/level_weekly_melee_6",
        "name": "实验原件押运",
    }
    assert by_code["source"] == "resource"
    assert by_code["resolved"]["stage_id"] == "main_01-07"
    assert by_code["resolved"]["name"] == "暴君"
    assert by_stage_id["resolved"] == expected
    assert by_name["resolved"] == expected
    # 三个查询各自命中一条候选。
    assert by_stage_id["candidates_total"] == 1
    assert by_stage_id["candidates"] == [expected]


def test_repeated_codes_across_periods_return_every_candidate(tmp_path) -> None:
    """同名代号跨期次：全部候选都返回，第一条仍是既有的 ``resolved``。"""

    path = _overview(
        tmp_path,
        [
            {
                "code": "XX-08",
                "stageId": "act1demo_08",
                "levelId": "activities/act1demo/level_act1demo_08",
                "name": "一期八号",
            },
            {
                "code": "XX-08",
                "stageId": "act3demo_08",
                "levelId": "activities/act3demo/level_act3demo_08",
                "name": "三期八号",
            },
            {
                "code": "XX-08",
                "stageId": "act2demo_08",
                "levelId": "activities/act2demo/level_act2demo_08",
                "name": "二期八号",
            },
        ],
    )
    result = asyncio.run(_resolver(path).resolve("XX-08"))

    assert [item["stage_id"] for item in result["candidates"]] == [
        "act1demo_08",
        "act2demo_08",
        "act3demo_08",
    ]
    assert result["candidates_total"] == 3
    assert result["resolved"]["stage_id"] == "act1demo_08"  # 兼容：仍是排序后的第一条
    assert [item["name"] for item in result["candidates"]] == ["一期八号", "二期八号", "三期八号"]


def test_matching_order_is_stage_id_then_level_id_then_code_then_name(tmp_path) -> None:
    """查询词同时能当标识与名字用时，按 stageId → levelId → code → 中文名的顺序落地。"""

    path = _overview(
        tmp_path,
        [
            {
                "code": "DEMO",
                "stageId": "act1demo_01",
                "levelId": "activities/act1demo/level_act1demo_01",
                "name": "DEMO",
            },
            {
                "code": "OTHER",
                "stageId": "act2demo_01",
                "levelId": "activities/act2demo/level_act2demo_01",
                "name": "别的",
            },
        ],
    )
    resolver = _resolver(path)

    # "DEMO" 既是 code 又是中文名：code 级先命中，只有那一条。
    by_code = asyncio.run(resolver.resolve("DEMO"))
    assert [item["stage_id"] for item in by_code["candidates"]] == ["act1demo_01"]

    # levelId 级：给全路径时按 levelId 命中。
    by_level = asyncio.run(resolver.resolve("activities/act2demo/level_act2demo_01"))
    assert [item["stage_id"] for item in by_level["candidates"]] == ["act2demo_01"]


def test_variants_of_the_same_level_collapse_into_one_candidate(tmp_path) -> None:
    """同一张图的 ``#f#`` 变体合并成一条，且保留没有 ``#f#`` 的写法。

    真实数据里 ``main_01-07`` 与 ``main_01-07#f#`` 的 ``levelId`` 与中文名完全相同，
    重复计入候选会让「候选数」虚高、把上层引向不存在的「两关”。
    """

    path = _overview(
        tmp_path,
        [
            {
                "code": "1-7",
                "stageId": "main_01-07",
                "levelId": "obt/main/level_main_01-07",
                "name": "暴君",
            },
            {
                "code": "1-7",
                "stageId": "main_01-07#f#",
                "levelId": "obt/main/level_main_01-07",
                "name": "暴君",
            },
        ],
    )
    resolver = _resolver(path)

    by_code = asyncio.run(resolver.resolve("1-7"))
    assert by_code["candidates_total"] == 1
    assert by_code["resolved"]["stage_id"] == "main_01-07"

    # 但按带 #f# 的 stageId 精确查询时，要原样返回那一条：用户给的就是它。
    by_variant = asyncio.run(resolver.resolve("main_01-07#f#"))
    assert by_variant["candidates_total"] == 1
    assert by_variant["resolved"]["stage_id"] == "main_01-07#f#"


def test_distinct_levels_sharing_a_code_are_not_merged(tmp_path) -> None:
    """合并的判据是「同一张图」：``levelId`` 不同就不许合。"""

    path = _overview(
        tmp_path,
        [
            {"code": "XX-01", "stageId": "act1demo_01", "levelId": "activities/act1demo/level_act1demo_01", "name": "一期一号"},
            {"code": "XX-01", "stageId": "act2demo_01", "levelId": "activities/act2demo/level_act2demo_01", "name": "二期一号"},
        ],
    )
    result = asyncio.run(_resolver(path).resolve("XX-01"))

    assert result["candidates_total"] == 2
    assert [item["stage_id"] for item in result["candidates"]] == ["act1demo_01", "act2demo_01"]


def test_native_stage_mapping_takes_precedence_over_local_resource(tmp_path) -> None:
    path = _overview(
        tmp_path,
        [
            {
                "code": "1-7",
                "stageId": "main_01-07",
                "levelId": "obt/main/level_main_01-07",
                "name": "暴君",
            }
        ],
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

    resolver = _resolver(path, Registry())
    result = asyncio.run(resolver.resolve("Alias-1-7"))

    assert result["source"] == "core"
    assert result["resolved"] == {
        "code": "1-7",
        "stage_id": "main_01-07",
        "level_id": "level-main-01-07",
        "name": "切城",
    }
    # 内核路径的候选就是它自己，形状与回退路径一致。
    assert result["candidates"] == [result["resolved"]]
    assert result["candidates_total"] == 1


def test_unknown_stage_is_reported_without_inventing_an_alias(tmp_path) -> None:
    resolver = _resolver(_overview(tmp_path, []))

    result = asyncio.run(resolver.resolve("not-a-stage"))

    assert result == {
        "query": "not-a-stage",
        "source": "resource",
        "resolved": None,
        "candidates": [],
        "candidates_total": 0,
    }


def test_empty_query_returns_an_empty_result(tmp_path) -> None:
    resolver = _resolver(_overview(tmp_path, []))

    assert asyncio.run(resolver.resolve("   ")) == {
        "query": "",
        "source": "resource",
        "resolved": None,
        "candidates": [],
        "candidates_total": 0,
    }


def test_legacy_stages_path_argument_still_points_at_the_overview(tmp_path) -> None:
    """旧的 ``stages_path`` 装配名保留：传同级的 stages.json 也折算到 overview.json。"""

    _overview(
        tmp_path,
        [
            {
                "code": "1-7",
                "stageId": "main_01-07",
                "levelId": "obt/main/level_main_01-07",
                "name": "暴君",
            }
        ],
    )
    legacy = tmp_path / "stages.json"
    legacy.write_text("[]", encoding="utf-8")

    module = importlib.import_module("maa_api.services.stage_resolver")
    resolver = module.StageResolver(None, stages_path=legacy)

    assert resolver.overview_path == legacy  # 属性保留旧名，语义已换成 overview
    assert asyncio.run(resolver.resolve("1-7"))["resolved"]["stage_id"] == "main_01-07"


def test_missing_index_degrades_to_no_candidates(tmp_path) -> None:
    resolver = _resolver(tmp_path / "Arknights-Tile-Pos" / "overview.json")

    result = asyncio.run(resolver.resolve("1-7"))

    assert result["resolved"] is None
    assert result["candidates_total"] == 0
