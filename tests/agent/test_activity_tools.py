"""``get_activity_stages`` 工具（M25 规格 §5.6）。

用假活动服务（返回现造的 :class:`ActivityReport`）与假客户端把参数形态、返回信封与降级路径
写死；本机资源的推断规则由 ``tests/services/test_activity_index.py`` 覆盖，这里不重复。
**所有用例都不打网**：需要「PRTS 取数」的地方注入现造报告，需要降级的地方注入一个直接抛
``EXTERNAL_SOURCE_UNAVAILABLE`` 的假客户端。异步用例用 `asyncio.run` 跑。
"""

from __future__ import annotations

import asyncio
import json
from datetime import datetime
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import pytest
from pydantic import ValidationError

from maa_api.agent.registry import ToolContext
from maa_api.agent.tools import build_registry
from maa_api.domain.enums import CallerType
from maa_api.domain.errors import AppError, ErrorCode
from maa_api.services.activity_index import ActivityIndex
from maa_api.services.external.envelope import SourceRef
from maa_api.services.game.activities import (
    ActivityReport,
    ActivityService,
    CurrentActivity,
    StageEntry,
)
from maa_api.settings import Settings

REPO_ROOT = Path(__file__).resolve().parents[2]
REAL_LAYERS = (
    REPO_ROOT / "resource" / "maa-layers" / "repo" / "resource",
    REPO_ROOT / "resource" / "lib" / "maa" / "Darwin" / "resource",
)

#: 现造活动的时间窗（北京时间），覆盖「在开」的边界之外——工具只负责原样透出。
START_AT = "2026-09-29T16:00:00+08:00"
END_AT = "2026-10-20T03:59:00+08:00"


# --------------------------------------------------------------------------- #
# 夹具、假服务、假客户端
# --------------------------------------------------------------------------- #
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


class FakeActivityService:
    """假活动服务：只实现工具用到的 `current`，并记下调用参数。"""

    def __init__(self, report: ActivityReport) -> None:
        self.report = report
        self.calls: list[dict[str, Any]] = []

    async def current(
        self,
        *,
        detail: str = "summary",
        client_type: str | None = None,
        budget_seconds: float | None = None,
    ) -> ActivityReport:
        self.calls.append({"detail": detail, "client_type": client_type})
        return self.report


class UnavailablePrts:
    """假客户端：任何取数都抛 `EXTERNAL_SOURCE_UNAVAILABLE`（规格 §3.5 的降级入口）。"""

    def _fail(self) -> None:
        raise AppError(ErrorCode.EXTERNAL_SOURCE_UNAVAILABLE, "PRTS 不可用（假客户端）", {"kind": "prts"})

    async def ask(self, *args: Any, **kwargs: Any) -> Any:
        self._fail()

    async def wikitext(self, titles: Any) -> Any:
        self._fail()

    def smw_pages(self, result: Any) -> list[Any]:
        return []


def _stage(code: str, name: str, stage_id: str) -> StageEntry:
    prefix = stage_id.split("_", 1)[0]
    return StageEntry(code=code, name=name, stage_id=stage_id, level_id=f"activities/{prefix}/level_{stage_id}")


def _activity(
    name: str,
    page: str,
    prefix: str | None,
    stages: list[StageEntry],
    *,
    source: str = "prts",
    **extra: Any,
) -> CurrentActivity:
    return CurrentActivity(
        name=name,
        page=page,
        start_at=datetime.fromisoformat(START_AT),
        end_at=datetime.fromisoformat(END_AT),
        stage_id_prefix=prefix,
        stages=tuple(stages),
        navigation="missing",
        matched_by="stage_page_id",
        source=source,
        **extra,
    )


def _report(*activities: CurrentActivity, **kwargs: Any) -> ActivityReport:
    return ActivityReport(activities=tuple(activities), **kwargs)


def _source(title: str) -> SourceRef:
    return SourceRef(
        kind="prts",
        label="PRTS",
        title=title,
        url=f"https://prts.wiki/w/{title.replace(' ', '_')}",
        revision=123456,
        fetched_at=datetime(2026, 10, 9, 20, 15, 3),
        cached=True,
        stale=False,
    )


def _context(index: ActivityIndex | None = None, service: Any = None) -> ToolContext:
    state = SimpleNamespace()
    if index is not None:
        state.activity_index = index
    if service is not None:
        state.activity_service = service
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


def _call(arguments: dict[str, Any], context: ToolContext) -> dict[str, Any]:
    async def scenario():
        return await _definition().handler(_params(**arguments), context)

    return asyncio.run(scenario())


# --------------------------------------------------------------------------- #
# 注册与参数
# --------------------------------------------------------------------------- #
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
        model.model_validate({"stage": ""})
    with pytest.raises(ValidationError):
        model.model_validate({"prefix": "Has-Caps"})
    with pytest.raises(ValidationError):
        model.model_validate({"stage": "VEC-08", "unknown": 1})


# --------------------------------------------------------------------------- #
# 不带参数：当期活动报告（规格 §5.6）
# --------------------------------------------------------------------------- #
def test_without_arguments_returns_two_open_activities_with_the_source_envelope(tmp_path) -> None:
    index = ActivityIndex(_layer(tmp_path))
    service = FakeActivityService(
        _report(
            _activity(
                "矢量突破 #3",
                "矢量突破/03",
                "act3break",
                [_stage(f"VEC-{n:02d}", f"关卡{n}", f"act3break_{n:02d}") for n in range(1, 13)],
            ),
            _activity(
                "昨日海",
                "昨日海",
                "act52side",
                [_stage(f"YW-{n}", f"昨日海{n}", f"act52side_{n:02d}") for n in range(1, 9)],
            ),
            sources=(_source("矢量突破/03"), _source("昨日海")),
        )
    )

    result = _call({}, _context(index, service=service))

    assert len(result["activities"]) == 2
    first, second = result["activities"]
    assert first["name"] == "矢量突破 #3"
    assert first["page"] == "矢量突破/03"
    assert first["end_at"] == END_AT
    assert first["code_range"] == {"first": "VEC-01", "last": "VEC-12", "count": 12}
    assert first["stages_total"] == 12
    assert first["source"] == "prts"
    assert second["name"] == "昨日海"
    assert second["code_range"] == {"first": "YW-1", "last": "YW-8", "count": 8}
    assert second["source"] == "prts"

    assert result["fallback_reason"] is None
    assert result["region_note"] is None
    assert [item["kind"] for item in result["sources"]] == ["prts", "prts"]
    assert result["sources"][0]["title"] == "矢量突破/03"
    assert result["untrusted"] is True
    assert result["unavailable"] == []
    # 工具要的是完整报告（含关卡表），注入段才用 summary。
    assert service.calls == [{"detail": "full", "client_type": None}]


def test_no_open_activity_marks_the_activities_group_unavailable(tmp_path) -> None:
    index = ActivityIndex(_layer(tmp_path))
    service = FakeActivityService(_report())

    result = _call({}, _context(index, service=service))

    assert result["activities"] == []
    assert result["unavailable"] == ["activities"]
    assert result["sources"] == []
    assert result["untrusted"] is True


# --------------------------------------------------------------------------- #
# stage 参数：本机索引 + 当期活动标注
# --------------------------------------------------------------------------- #
def test_stage_argument_returns_every_period_and_marks_the_current_one(tmp_path) -> None:
    index = ActivityIndex(_layer(tmp_path))
    service = FakeActivityService(
        _report(_activity("矢量突破 #3", "矢量突破/03", "act3break", []), sources=(_source("矢量突破/03"),))
    )

    result = _call({"stage": "VEC-08"}, _context(index, service=service))

    assert result["matches_total"] == 2
    assert [(item["activity"], item["is_current"], item["activity_name"]) for item in result["matches"]] == [
        ("act1break", False, None),
        ("act3break", True, "矢量突破 #3"),
    ]
    assert result["current_activities"] == ["矢量突破 #3"]
    assert "hint" not in result
    assert result["untrusted"] is True


def test_stage_argument_that_matches_nothing_gets_a_hint_not_an_error(tmp_path) -> None:
    index = ActivityIndex(_layer(tmp_path))
    service = FakeActivityService(_report())

    result = _call({"stage": "NOPE-1"}, _context(index, service=service))

    assert result["matches"] == [] and result["matches_total"] == 0
    assert "stageId" in result["hint"]
    assert result["current_activities"] == []
    assert result["sources"] == []
    assert result["untrusted"] is True


# --------------------------------------------------------------------------- #
# prefix 参数：一组关卡 + 未知前缀
# --------------------------------------------------------------------------- #
def test_prefix_argument_returns_that_family_and_flags_candidates(tmp_path) -> None:
    index = ActivityIndex(_layer(tmp_path))
    service = FakeActivityService(
        _report(_activity("矢量突破 #3", "矢量突破/03", "act3break", []), sources=(_source("矢量突破/03"),))
    )

    context = _context(index, service=service)
    current = _call({"prefix": "act3break"}, context)
    assert current["stage_id_prefix"] == "act3break"
    assert current["is_current"] is True
    assert current["activity_name"] == "矢量突破 #3"
    assert [item["stage_id"] for item in current["stages"]] == ["act3break_01", "act3break_08"]
    assert current["stages_total"] == 2
    assert current["untrusted"] is True

    alternative = _call({"prefix": "act3bossrush"}, context)
    assert alternative["is_current"] is False
    assert alternative["activity_name"] is None
    assert [item["code"] for item in alternative["stages"]] == ["TN-1"]


def test_unknown_prefix_is_an_invalid_parameter_with_the_known_prefixes(tmp_path) -> None:
    index = ActivityIndex(_layer(tmp_path))
    service = FakeActivityService(
        _report(_activity("矢量突破 #3", "矢量突破/03", "act3break", []), sources=(_source("矢量突破/03"),))
    )

    with pytest.raises(AppError) as error:
        _call({"prefix": "act99side"}, _context(index, service=service))
    assert error.value.code is ErrorCode.INVALID_PARAMETER
    assert set(error.value.details["known"]) == {"act3break", "act3bossrush"}


# --------------------------------------------------------------------------- #
# 降级：PRTS 不可用 / 外服（规格 §3.5、§5.6）
# --------------------------------------------------------------------------- #
def test_local_fallback_keeps_confidence_and_alternatives(tmp_path) -> None:
    """规格 §5.6：本地降级时 activities 只有一条，带 M24 的 confidence 等字段。

    当前 ``tools/activity.py::_report`` 用的是 ``report.as_dict()``，而
    ``CurrentActivity.fallback_dict()``（唯一会带上这四个字段的方法）在仓库里没有任何
    调用点：这条用例因此是红的，直到工具层改用 ``fallback_dict()``。
    """

    index = ActivityIndex(_layer(tmp_path))
    service = ActivityService(prts=UnavailablePrts(), index=index, settings_provider=lambda: Settings())

    result = _call({}, _context(index, service=service))

    assert result["fallback_reason"]
    assert len(result["activities"]) == 1
    activity = result["activities"][0]
    assert activity["source"] == "resource"
    assert activity["confidence"] == "low"
    assert activity["alternatives"]
    assert activity["related"] == ["act1break"]
    assert activity["resource_updated_at"] == "2026-10-08 21:02:19.000"
    assert result["sources"] == []
    assert result["untrusted"] is True


def test_missing_resource_is_a_service_level_error(tmp_path) -> None:
    """没有装配索引、默认门面也读不到资源时，报的是资源不可用而不是空结果。"""

    with pytest.raises(AppError) as error:
        _call({"stage": "VEC-08"}, _context(ActivityIndex(tmp_path / "nowhere")))
    assert error.value.code is ErrorCode.RESOURCE_LOAD_FAILED


# --------------------------------------------------------------------------- #
# 真资源（本机没有资源层时跳过）
# --------------------------------------------------------------------------- #
def _real_layer() -> Path:
    for root in REAL_LAYERS:
        if (root / "version.json").is_file():
            return root
    pytest.skip("本机没有活动资源层")


def test_real_resource_pack_end_to_end() -> None:
    """真资源 + 本地兜底：不带参数拿到当期活动与代号区间，再按代号查回同一条。"""

    index = ActivityIndex(_real_layer())
    # PRTS 假客户端直接不可用：这条用例只走本机资源，不打网。
    service = ActivityService(prts=UnavailablePrts(), index=index, settings_provider=lambda: Settings())
    context = _context(index, service=service)

    listed = _call({}, context)
    assert listed["fallback_reason"]
    activity = listed["activities"][0]
    assert activity["source"] == "resource"
    assert activity["name"]
    if not activity["stages"]:
        # 本机当期活动是「昨日海」这种没写期数的名字：M24 规则不给代号（confidence=none），
        # 没有代号可回查；这一情形由 tests/services/test_activity_index.py 覆盖。
        pytest.skip("本机活动资源推断不出代号区间，无法做代号回查")

    first = activity["stages"][0]
    assert first["code"]
    found = _call({"stage": first["code"]}, context)
    current = [item for item in found["matches"] if item["is_current"]]
    assert first["stage_id"] in [item["stage_id"] for item in current]
