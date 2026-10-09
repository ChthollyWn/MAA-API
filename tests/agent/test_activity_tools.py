"""``get_activity_stages`` 工具（M24 规格 §4.2）。

用假索引把三种参数形态与失败路径写死；真资源的形状由 ``tests/services/test_activity_index.py``
覆盖，这里不重复。
"""

from __future__ import annotations

import asyncio
import json
from pathlib import Path
from types import SimpleNamespace

import pytest
from pydantic import ValidationError

from maa_api.agent.registry import ToolContext
from maa_api.agent.tools import build_registry
from maa_api.domain.enums import CallerType
from maa_api.domain.errors import AppError, ErrorCode
from maa_api.services.activity_index import ActivityIndex

REPO_ROOT = Path(__file__).resolve().parents[2]
REAL_LAYERS = (
    REPO_ROOT / "resource" / "maa-layers" / "repo" / "resource",
    REPO_ROOT / "resource" / "lib" / "maa" / "Darwin" / "resource",
)


def _layer(tmp_path: Path) -> Path:
    """一个最小资源层：当期「矢量突破#3 拟生态」，act3break 两关 + 一个同期候选。"""

    root = tmp_path / "resource"
    (root / "Arknights-Tile-Pos").mkdir(parents=True)
    (root / "tasks" / "Stages").mkdir(parents=True)
    (root / "version.json").write_text(
        json.dumps(
            {
                "activity": {"name": "矢量突破#3 拟生态", "time": 1790668800},
                "last_updated": "2026-10-08 21:02:19.000",
            },
            ensure_ascii=False,
        ),
        encoding="utf-8",
    )
    overview = {}
    for prefix, entries in (
        ("act1break", [("08", "VEC-08", "结构化思维")]),
        ("act3break", [("01", "VEC-01", "寂静螺旋"), ("08", "VEC-08", "磷脂层破坏")]),
        ("act3bossrush", [("01", "TN-1", "一号对抗")]),
    ):
        for suffix, code, name in entries:
            overview[f"{prefix}_{suffix}-x"] = {
                "stageId": f"{prefix}_{suffix}",
                "code": code,
                "name": name,
                "levelId": f"activities/{prefix}/level_{prefix}_{suffix}",
            }
    (root / "Arknights-Tile-Pos" / "overview.json").write_text(
        json.dumps(overview, ensure_ascii=False), encoding="utf-8"
    )
    return root


def _context(index: ActivityIndex | None = None) -> ToolContext:
    state = SimpleNamespace(activity_index=index) if index is not None else SimpleNamespace()
    return ToolContext(
        CallerType.INTERNAL,
        None,
        None,
        SimpleNamespace(app=SimpleNamespace(state=state)),
        None,
    )


def _definition():
    return build_registry().get("get_activity_stages")


def _params(**kwargs):
    return _definition().params_model.model_validate(kwargs)


def test_registered_as_a_safe_read_only_activity_tool() -> None:
    definition = _definition()

    assert definition.group == "activity"
    assert definition.risk.value == "SAFE"
    assert definition.read_only is True
    assert definition.model_visible is True
    assert "活动" in definition.description


def test_params_reject_an_overlong_or_malformed_input() -> None:
    model = _definition().params_model

    with pytest.raises(ValidationError):
        model.model_validate({"stage": "x" * 65})
    with pytest.raises(ValidationError):
        model.model_validate({"prefix": "Has-Caps"})
    with pytest.raises(ValidationError):
        model.model_validate({"stage": "VEC-08", "unknown": 1})


def test_without_arguments_returns_the_current_activity_snapshot(tmp_path) -> None:
    index = ActivityIndex(_layer(tmp_path))

    async def scenario():
        return await _definition().handler(_params(), _context(index))

    result = asyncio.run(scenario())
    assert result["activity"]["name"] == "矢量突破#3 拟生态"
    assert result["activity"]["started_at"] == "2026-09-29"
    assert result["stage_id_prefix"] == "act3break"
    assert result["confidence"] == "low"
    assert result["code_range"] == ["VEC-01", "VEC-08"]
    assert [item["code"] for item in result["stages"]] == ["VEC-01", "VEC-08"]
    assert result["stages_total"] == 2
    assert result["navigation"] == "missing"
    assert result["resource_updated_at"] == "2026-10-08 21:02:19.000"
    assert [item["stage_id_prefix"] for item in result["alternatives"]] == ["act3bossrush"]
    assert result["related"] == ["act1break"]


def test_stage_argument_returns_every_period_and_marks_the_current_one(tmp_path) -> None:
    index = ActivityIndex(_layer(tmp_path))

    async def scenario():
        return await _definition().handler(_params(stage="VEC-08"), _context(index))

    result = asyncio.run(scenario())
    assert result["matches_total"] == 2
    assert [(item["activity"], item["period"], item["is_current"]) for item in result["matches"]] == [
        ("act1break", 1, False),
        ("act3break", 3, True),
    ]
    assert result["current_activity"] == "矢量突破#3 拟生态"
    assert "hint" not in result


def test_stage_argument_that_matches_nothing_gets_a_hint_not_an_error(tmp_path) -> None:
    index = ActivityIndex(_layer(tmp_path))

    async def scenario():
        return await _definition().handler(_params(stage="NOPE-1"), _context(index))

    result = asyncio.run(scenario())
    assert result["matches"] == [] and result["matches_total"] == 0
    assert "stageId" in result["hint"]


def test_prefix_argument_returns_that_family_and_flags_candidates(tmp_path) -> None:
    index = ActivityIndex(_layer(tmp_path))

    async def scenario(prefix):
        return await _definition().handler(_params(prefix=prefix), _context(index))

    current = asyncio.run(scenario("act3break"))
    assert current["is_current"] is True
    assert [item["stage_id"] for item in current["stages"]] == ["act3break_01", "act3break_08"]

    alternative = asyncio.run(scenario("act3bossrush"))
    assert alternative["is_current"] is False
    assert [item["code"] for item in alternative["stages"]] == ["TN-1"]


def test_unknown_prefix_is_an_invalid_parameter_with_the_known_prefixes(tmp_path) -> None:
    index = ActivityIndex(_layer(tmp_path))

    async def scenario():
        return await _definition().handler(_params(prefix="act99side"), _context(index))

    with pytest.raises(AppError) as error:
        asyncio.run(scenario())
    assert error.value.code == ErrorCode.INVALID_PARAMETER
    assert set(error.value.details["known"]) == {"act3break", "act3bossrush"}


def test_missing_resource_is_a_service_level_error(tmp_path) -> None:
    """没有装配索引、默认门面也读不到资源时，报的是资源不可用而不是空结果。"""

    async def scenario():
        definition = _definition()
        return await definition.handler(_params(stage="VEC-08"), _context(ActivityIndex(tmp_path / "nowhere")))

    with pytest.raises(AppError) as error:
        asyncio.run(scenario())
    assert error.value.code == ErrorCode.RESOURCE_LOAD_FAILED


def _real_layer() -> Path:
    for root in REAL_LAYERS:
        if (root / "version.json").is_file():
            return root
    pytest.skip("本机没有活动资源层")


def test_real_resource_pack_end_to_end() -> None:
    """真资源：不带参数拿到当期活动与代号区间，再按代号查回同一条。"""

    index = ActivityIndex(_real_layer())

    async def scenario():
        definition = _definition()
        listed = await definition.handler(_params(), _context(index))
        first = listed["stages"][0]
        found = await definition.handler(_params(stage=first["code"]), _context(index))
        return listed, first, found

    listed, first, found = asyncio.run(scenario())
    assert listed["confidence"] in {"high", "low"}
    assert listed["activity"]["name"]
    assert first["code"] and first["stage_id"].startswith(f"{listed['stage_id_prefix']}_")
    current = [item for item in found["matches"] if item["is_current"]]
    assert first["stage_id"] in [item["stage_id"] for item in current]
