"""活动索引：从资源层读当期活动并推出关卡代号（M24 规格 §4.1）。

两类用例：

- **夹具层**：临时目录里放最小 ``version.json`` + ``overview.json`` + ``tasks/Stages/``，
  把推断规则的每个边界写死，不依赖真数据。
- **真资源包**：本机 ``resource/`` 下确有活动资源时，断言当期活动与关卡代号；资源缺失
  时跳过（干净检出里没有 ``maa-layers/repo`` 是正常的）。
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from maa_api.domain.errors import AppError, ErrorCode
from maa_api.services.activity_index import (
    ACTIVITY_LEVEL_NAMESPACE,
    MAX_STAGES,
    ActivityIndex,
    ActivityStage,
    infer_activity,
    main_stage_codes,
)

REPO_ROOT = Path(__file__).resolve().parents[2]
REAL_LAYERS = (
    REPO_ROOT / "resource" / "maa-layers" / "repo" / "resource",
    REPO_ROOT / "resource" / "lib" / "maa" / "Darwin" / "resource",
)


def _real_layer() -> Path:
    for root in REAL_LAYERS:
        if (root / "version.json").is_file() and (
            root / "Arknights-Tile-Pos" / "overview.json"
        ).is_file():
            return root
    pytest.skip("本机没有活动资源层")


def _stage(stage_id: str, code: str, name: str, *, namespace: str = ACTIVITY_LEVEL_NAMESPACE) -> dict:
    prefix = stage_id.split("_", 1)[0]
    return {
        "stageId": stage_id,
        "code": code,
        "name": name,
        "levelId": f"{namespace}{prefix}/level_{stage_id}",
        "filename": f"{stage_id}.json",
        "width": 9,
        "height": 7,
    }


def _activity(
    prefix: str,
    entries: list[tuple[str, str, str]],
    *,
    namespace: str = ACTIVITY_LEVEL_NAMESPACE,
) -> dict[str, dict]:
    """``entries`` 是 ``(stageId 后缀, code, 中文名)``，stageId 为 ``<prefix>_<后缀>``。"""
    return {
        f"{prefix}_{suffix}-x": _stage(f"{prefix}_{suffix}", code, name, namespace=namespace)
        for suffix, code, name in entries
    }


#: 四期同名代号的裁切夹具：act1break / act1vecb / act2break / act3break。
VEC_OVERVIEW: dict[str, dict] = {
    **_activity("act1break", [("01", "VEC-01", "横冲直进"), ("08", "VEC-08", "结构化思维")]),
    **_activity("act1vecb", [("08", "VEC-08", "思绪之隙")]),
    **_activity("act2break", [("08", "VEC-08", "脉冲隔绝术")]),
    **_activity(
        "act3break",
        [
            ("01", "VEC-01", "寂静螺旋"),
            ("02", "VEC-02", "波形扭曲"),
            ("03", "VEC-03", "极速赛道"),
            ("08", "VEC-08", "磷脂层破坏"),
            ("12", "VEC-12", "十二号"),
            ("ex01", "VEC-EX-1", "突袭一号"),
        ],
    ),
    **_activity("act54side", [("01", "SR-1", "月出")]),
    **_activity("ro3", [("01", "ISW-DF", "与虫为伴")], namespace="obt/roguelike/"),
    **_activity("camp_r_02", [("01", "RA-1", "荒野一号")], namespace="obt/campaign/"),
}


def _layer(
    root: Path,
    *,
    name: str,
    time: int | str | None = 1790668800,
    overview: dict[str, dict] | None = None,
    navigation: tuple[str, ...] = (),
    updated_at: str | None = "2026-10-08 21:02:19.000",
) -> Path:
    """写一个最小资源层；返回它的 ``resource`` 根目录。"""
    resource = root / "resource"
    (resource / "Arknights-Tile-Pos").mkdir(parents=True, exist_ok=True)
    (resource / "tasks" / "Stages").mkdir(parents=True, exist_ok=True)
    version: dict[str, object] = {"activity": {"name": name, "time": time}}
    if updated_at is not None:
        version["last_updated"] = updated_at
    (resource / "version.json").write_text(json.dumps(version, ensure_ascii=False), encoding="utf-8")
    (resource / "Arknights-Tile-Pos" / "overview.json").write_text(
        json.dumps(overview if overview is not None else VEC_OVERVIEW, ensure_ascii=False),
        encoding="utf-8",
    )
    for prefix in navigation:
        (resource / "tasks" / "Stages" / f"{prefix}.json").write_text("{}", encoding="utf-8")
    return resource


# ---------------------------------------------------------------------------
# 夹具层：推断规则的每个边界
# ---------------------------------------------------------------------------
def test_snapshot_picks_the_current_series_and_lists_its_stages(tmp_path) -> None:
    root = _layer(tmp_path, name="矢量突破#3 拟生态")
    snapshot = ActivityIndex(root).snapshot()

    assert snapshot.stage_id_prefix == "act3break"
    assert snapshot.series == 3
    assert snapshot.matched_by == "series-number"
    # 夹具里同期只剩 act3break 一个有字母模式后缀、且全在 activities/ 的前缀。
    assert snapshot.confidence == "high"
    assert snapshot.alternatives == ()
    assert [stage.code for stage in snapshot.stages][:3] == ["VEC-01", "VEC-02", "VEC-03"]
    assert [stage.code for stage in snapshot.stages][-2:] == ["VEC-12", "VEC-EX-1"]
    assert snapshot.code_range == ("VEC-01", "VEC-12")
    assert "act1break" in snapshot.related and "act2break" in snapshot.related
    assert snapshot.resource_updated_at == "2026-10-08 21:02:19.000"
    assert str(root) == snapshot.source


def test_a_lone_candidate_is_high_confidence(tmp_path) -> None:
    """同期只剩一个候选时才是 high：把非活动命名空间的候选排除掉。"""

    overview = {
        key: value
        for key, value in VEC_OVERVIEW.items()
        if not key.startswith(("ro3", "camp_r_02"))
    }
    root = _layer(tmp_path, name="矢量突破#3 拟生态", overview=overview)
    snapshot = ActivityIndex(root).snapshot()

    assert snapshot.confidence == "high"
    assert snapshot.stage_id_prefix == "act3break"
    assert snapshot.alternatives == ()


def test_non_activity_namespaces_and_suffixless_prefixes_are_not_candidates(tmp_path) -> None:
    """三条硬门槛：期数对得上、前缀有字母模式后缀、全部关卡在 ``activities/`` 命名空间。

    真数据里同期撞车的 ``ro3``（肉鸽，无后缀）、``a003`` / ``act18d3``（尾部数字，无后缀）、
    ``camp_r_02``（剿灭，尾部数字）都被挡在候选之外，9 个候选因此收敛到 3 个完整活动。
    """

    overview = {
        **VEC_OVERVIEW,
        **_activity("a003", [("01", "OF-1", "共舞")]),
        **_activity("act18d3", [("01", "SV-1", "源石尘行动")]),
    }
    index = ActivityIndex(_layer(tmp_path, name="矢量突破#3 拟生态", overview=overview))
    snapshot = index.snapshot()

    candidates = {snapshot.stage_id_prefix, *(item.prefix for item in snapshot.alternatives)}
    assert candidates == {"act3break"}
    # 被挡在候选外不等于查不到：这几关仍然能按代号/中文名查到。
    assert [item["stage_id"] for item in index.find_stage("ISW-DF")["matches"]] == ["ro3_01"]
    assert [item["stage_id"] for item in index.find_stage("OF-1")["matches"]] == ["a003_01"]


def test_alternatives_are_ranked_and_reported(tmp_path) -> None:
    """同期完整活动的候选按「主关数 → 总关数 → 前缀」排序，其余进 alternatives。"""

    overview = {
        **VEC_OVERVIEW,
        **_activity("act3bossrush", [("01", "TN-1", "一号"), ("02", "TN-2", "二号")]),
        **_activity("act3enemyduel", [("01", "VS-1", "对决")]),
    }
    snapshot = ActivityIndex(_layer(tmp_path, name="矢量突破#3 拟生态", overview=overview)).snapshot()

    assert snapshot.stage_id_prefix == "act3break"
    assert snapshot.confidence == "low"
    assert [(item.prefix, item.main_stages) for item in snapshot.alternatives][:2] == [
        ("act3bossrush", 2),
        ("act3enemyduel", 1),
    ]
    assert snapshot.alternatives[0].code_range == ("TN-1", "TN-2")


def test_snapshot_reports_navigation_by_the_stages_file(tmp_path) -> None:
    """导航文件决定 ``navigation``：真资源的这一期没有 ``act3break.json``。"""

    assert ActivityIndex(_layer(tmp_path / "a", name="矢量突破#3 拟生态")).snapshot().navigation == "missing"
    available = _layer(tmp_path / "b", name="矢量突破#3 拟生态", navigation=("act3break",))
    assert ActivityIndex(available).snapshot().navigation == "available"


def test_snapshot_without_a_series_number_says_none_and_gives_no_codes(tmp_path) -> None:
    """活动名不带期数（例如只有副标题）→ 明确说不知道，且不给任何代号。"""

    snapshot = ActivityIndex(_layer(tmp_path, name="月行水上")).snapshot()

    assert snapshot.confidence == "none"
    assert snapshot.series is None
    assert snapshot.stage_id_prefix is None
    assert snapshot.stages == ()
    assert snapshot.alternatives == ()
    assert snapshot.navigation == "unknown"
    assert snapshot.code_range is None


def test_snapshot_without_any_matching_prefix_stays_none(tmp_path) -> None:
    snapshot = ActivityIndex(_layer(tmp_path, name="矢量突破#9 拟生态")).snapshot()

    assert snapshot.confidence == "none"
    assert snapshot.series == 9
    assert snapshot.stages == ()


def test_infer_activity_accepts_chinese_series_ordinals() -> None:
    """``第三期`` 这种写法也认；中文序号与 ``#3`` 是同一套规则。"""

    stages = {"act3break": [ActivityStage("VEC-01", "寂静螺旋", "act3break_01", "activities/act3break/level_x")]}
    prefix, series, confidence, matched_by, _related, alternatives, stages_out = infer_activity(
        "矢量突破第三期 拟生态", stages
    )

    assert (prefix, series, confidence, matched_by) == ("act3break", 3, "high", "series-number")
    assert alternatives == ()
    assert [stage.code for stage in stages_out] == ["VEC-01"]


def test_infer_activity_requires_the_activity_namespace_and_a_suffix() -> None:
    """两条硬门槛：命名空间必须是 ``activities/``，前缀必须有字母模式后缀。"""

    stages = {
        "act3break": [ActivityStage("VEC-01", "寂静螺旋", "act3break_01", "activities/x")],
        "ro3": [ActivityStage("ISW-DF", "与虫为伴", "ro3_01", "obt/roguelike/x")],
        "act18d3": [ActivityStage("SV-1", "源石尘行动", "act18d3_01", "activities/x")],
        "a003": [ActivityStage("OF-1", "共舞", "a003_01", "activities/x")],
    }
    prefix, _series, confidence, _matched_by, _related, alternatives, stages_out = infer_activity(
        "矢量突破#3 拟生态", stages
    )

    assert (prefix, confidence, alternatives) == ("act3break", "high", ())
    assert [stage.code for stage in stages_out] == ["VEC-01"]


def test_snapshot_survives_a_garbage_activity_time(tmp_path) -> None:
    assert ActivityIndex(_layer(tmp_path, name="矢量突破#3 拟生态", time="不是数字")).snapshot().started_at is None


def test_main_stage_codes_skips_special_stages() -> None:
    stages = [
        ActivityStage("VEC-01", "a", "act3break_01", None),
        ActivityStage("VEC-EX-1", "b", "act3break_ex01", None),
        ActivityStage("VEC-TR-1", "c", "act3break_tr01", None),
        ActivityStage("VEC-12", "d", "act3break_12", None),
    ]
    assert main_stage_codes(stages) == ["VEC-01", "VEC-12"]


# ---------------------------------------------------------------------------
# 单关查询
# ---------------------------------------------------------------------------
def test_find_stage_returns_every_period_that_shares_a_code(tmp_path) -> None:
    index = ActivityIndex(_layer(tmp_path, name="矢量突破#3 拟生态"))
    result = index.find_stage("VEC-08")

    assert [item["stage_id"] for item in result["matches"]] == [
        "act1break_08",
        "act1vecb_08",
        "act2break_08",
        "act3break_08",
    ]
    assert [item["period"] for item in result["matches"]] == [1, 1, 2, 3]
    by_activity = {item["activity"]: item for item in result["matches"]}
    assert by_activity["act3break"]["name"] == "磷脂层破坏"
    assert by_activity["act3break"]["is_current"] is True
    assert by_activity["act1break"]["is_current"] is False
    assert by_activity["act1break"]["mode"] == "break"
    assert by_activity["act1vecb"]["mode"] == "vecb"
    assert result["current_activity"] == "矢量突破#3 拟生态"


def test_find_stage_matches_stage_id_level_id_and_chinese_name(tmp_path) -> None:
    index = ActivityIndex(_layer(tmp_path, name="矢量突破#3 拟生态"))

    for query, stage_id in (
        ("act3break_08", "act3break_08"),
        ("activities/act3break/level_act3break_08", "act3break_08"),
        ("磷脂层破坏", "act3break_08"),
    ):
        matches = index.find_stage(query)["matches"]
        assert [item["stage_id"] for item in matches] == [stage_id], query


def test_find_stage_is_case_insensitive_and_empty_for_unknown_queries(tmp_path) -> None:
    index = ActivityIndex(_layer(tmp_path, name="矢量突破#3 拟生态"))

    assert index.find_stage("vec-08")["matches_total"] == 4
    unknown = index.find_stage("不存在-99")
    assert unknown["matches"] == [] and unknown["matches_total"] == 0


# ---------------------------------------------------------------------------
# 资源缺失 / 损坏 / 缓存
# ---------------------------------------------------------------------------
def test_missing_resource_layer_raises_resource_load_failed(tmp_path) -> None:
    with pytest.raises(AppError) as error:
        ActivityIndex(tmp_path / "nowhere").snapshot()
    assert error.value.code == ErrorCode.RESOURCE_LOAD_FAILED


def test_corrupt_overview_raises_resource_load_failed(tmp_path) -> None:
    root = _layer(tmp_path, name="矢量突破#3 拟生态")
    (root / "Arknights-Tile-Pos" / "overview.json").write_text("{ not json", encoding="utf-8")

    with pytest.raises(AppError) as error:
        ActivityIndex(root).snapshot()
    assert error.value.code == ErrorCode.RESOURCE_LOAD_FAILED


def test_version_without_an_activity_block_raises_resource_load_failed(tmp_path) -> None:
    root = _layer(tmp_path, name="矢量突破#3 拟生态")
    (root / "version.json").write_text(json.dumps({"last_updated": "x"}), encoding="utf-8")

    with pytest.raises(AppError) as error:
        ActivityIndex(root).snapshot()
    assert error.value.code == ErrorCode.RESOURCE_LOAD_FAILED


def test_adding_a_navigation_file_invalidates_the_cache(tmp_path) -> None:
    """签名包含 ``tasks/Stages/``：新增导航文件后同一进程里 ``navigation`` 要跟着变。"""

    root = _layer(tmp_path, name="矢量突破#3 拟生态")
    index = ActivityIndex(root)
    assert index.snapshot().navigation == "missing"

    (root / "tasks" / "Stages" / "act3break.json").write_text("{}", encoding="utf-8")
    assert index.snapshot().navigation == "available"


def test_snapshot_reflects_a_resource_update_without_restarting(tmp_path) -> None:
    """签名缓存：换掉 version.json 之后下一次调用就要看到新活动（§8.33/§8.34 的链路）。"""

    root = _layer(tmp_path, name="矢量突破#2 拟生态")
    index = ActivityIndex(root)
    assert index.snapshot().stage_id_prefix == "act2break"

    _layer(tmp_path, name="矢量突破#3 拟生态")
    second = index.snapshot()
    assert second.stage_id_prefix == "act3break"
    assert second.name == "矢量突破#3 拟生态"


# ---------------------------------------------------------------------------
# 真资源包
# ---------------------------------------------------------------------------
def test_real_resource_pack_reports_the_current_activity_and_its_stages() -> None:
    """真资源的形状自洽：有活动名与开始时间、有 stageId 前缀、代号不重复且都属于该前缀。"""

    snapshot = ActivityIndex(_real_layer()).snapshot()

    assert snapshot.name
    assert snapshot.started_at is not None
    assert snapshot.resource_updated_at
    assert snapshot.confidence in {"high", "low"}
    assert snapshot.stage_id_prefix
    # 上限只对 wire 形状成立：`as_dict()` 会截断，`snapshot.stages` 是完整表。
    # 拿完整表去比 60 会在「某期活动超过 60 关」时误报。
    wire = snapshot.as_dict()
    assert len(snapshot.stages) >= 1
    assert len(wire["stages"]) == min(len(snapshot.stages), MAX_STAGES)
    assert wire["stages_total"] == len(snapshot.stages)
    codes = [stage.code for stage in snapshot.stages]
    assert len(set(codes)) == len(codes)
    assert all(stage.stage_id.startswith(f"{snapshot.stage_id_prefix}_") for stage in snapshot.stages)


def test_real_resource_pack_disambiguates_a_repeated_code() -> None:
    """真资源里同名代号跨期次：查一条就必须能看出它属于哪一期。"""

    index = ActivityIndex(_real_layer())
    snapshot = index.snapshot()
    assert snapshot.stages, "真资源里当期活动应当有关卡表"

    sample = snapshot.stages[len(snapshot.stages) // 2]
    result = index.find_stage(sample.code)
    current = [item for item in result["matches"] if item["is_current"]]
    assert [item["stage_id"] for item in current] == [sample.stage_id]
    if result["matches_total"] > 1:
        periods = {item["period"] for item in result["matches"] if item["period"] is not None}
        assert len(periods) > 1, "同名代号应当出现在多个期次，否则这条用例失去意义"
