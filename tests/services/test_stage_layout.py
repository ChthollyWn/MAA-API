"""M23 §3.4：关卡布局数据链（资源层、平局规则、归一化、图例）。

真实资源用仓库自带的 ``resource/lib/maa/<平台>/resource``（不依赖 ``settings``，
避免同进程里别的用例改过全局设置）；层行为另用 ``tmp_path`` 造三层目录验证。
"""

from __future__ import annotations

import json
import platform
from dataclasses import dataclass
from pathlib import Path

import pytest

from maa_api.domain.errors import AppError, ErrorCode
from maa_api.services import stage_layout
from maa_api.services.stage_layout import (
    TERRAIN_LABELS,
    TERRAIN_ORDER,
    TILE_KEY_TERRAIN,
    load_stage_layout,
)

REPO_ROOT = Path(__file__).resolve().parents[2]
TILE_POS_DIRECTORY = "Arknights-Tile-Pos"


def _local_resource_root() -> Path:
    folder = {"Darwin": "Darwin", "Linux": "Linux", "Windows": "Win32"}.get(
        platform.system(), platform.system()
    )
    return REPO_ROOT / "resource" / "lib" / "maa" / folder / "resource"


LOCAL_RESOURCE = _local_resource_root()
LOCAL_TILE_POS = LOCAL_RESOURCE / TILE_POS_DIRECTORY


@dataclass(frozen=True)
class _Layers:
    base: Path
    cache: Path
    custom: Path


@pytest.fixture
def layered(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> _Layers:
    """三层资源根（基础层 / cache / custom），把 ``iter_resource_roots`` 指过去。"""
    layers = _Layers(
        base=tmp_path / "base" / "resource",
        cache=tmp_path / "maa-layers" / "cache" / "resource",
        custom=tmp_path / "maa-layers" / "custom" / "resource",
    )
    monkeypatch.setattr(
        stage_layout,
        "iter_resource_roots",
        lambda: [layers.base, layers.cache, layers.custom],
    )
    return layers


@pytest.fixture
def local_tiles(monkeypatch: pytest.MonkeyPatch) -> None:
    """只用仓库自带的真实资源层（层目录按不存在处理）。"""
    if not LOCAL_TILE_POS.is_dir():
        pytest.skip("仓库未自带 Arknights-Tile-Pos 资源")
    monkeypatch.setattr(stage_layout, "iter_resource_roots", lambda: [LOCAL_RESOURCE])


def _write_stage(
    root: Path,
    *,
    stage_id: str,
    level_id: str,
    code: str,
    name: str = "测试关",
    tiles: list[list[dict[str, object]]],
) -> None:
    """把一张关卡写进 ``root/Arknights-Tile-Pos``，并同步更新 overview.json。"""
    directory = root / TILE_POS_DIRECTORY
    directory.mkdir(parents=True, exist_ok=True)
    filename = f"{stage_id}-{level_id.replace('/', '-')}.json"
    (directory / filename).write_text(
        json.dumps(
            {
                "code": code,
                "stageId": stage_id,
                "levelId": level_id,
                "name": name,
                "width": len(tiles[0]),
                "height": len(tiles),
                "tiles": tiles,
            }
        ),
        encoding="utf-8",
    )
    overview_path = directory / "overview.json"
    overview = (
        json.loads(overview_path.read_text(encoding="utf-8"))
        if overview_path.is_file()
        else {}
    )
    key = f"{stage_id}-{level_id.replace('/', '-')}"
    overview[key] = {
        "code": code,
        "stageId": stage_id,
        "levelId": level_id,
        "filename": filename,
        "name": name,
        "width": len(tiles[0]),
        "height": len(tiles),
    }
    overview_path.write_text(json.dumps(overview), encoding="utf-8")


def _tile(
    tile_key: str,
    *,
    buildable: int = 0,
    height: int = 0,
    start: bool = False,
    end: bool = False,
) -> dict[str, object]:
    return {
        "tileKey": tile_key,
        "buildableType": buildable,
        "heightType": height,
        "isStart": start,
        "isEnd": end,
    }


# ----------------------------------------------------------------------
# 真实资源：1-7 与起点/终点口径
# ----------------------------------------------------------------------


def test_1_7_grid_size_codes_and_candidates(local_tiles: None) -> None:
    stage = load_stage_layout("1-7")

    assert stage["code"] == "1-7"
    assert stage["name"] == "暴君"
    assert stage["stage_id"] == "main_01-07"
    # `1-7` 在 overview 里有两条（含 `main_01-07#f#`），按 stageId 字典序取第一条。
    assert stage["candidates"] == 2
    assert (stage["width"], stage["height"]) == (11, 7)
    assert len(stage["tiles"]) == 7
    assert all(len(row) == 11 for row in stage["tiles"])

    # 单格 3 字符：<地形><可部署><高度>。1-7 的第 0 行整行是禁入格（buildableType 0）。
    assert stage["tiles"][0] == ["#01"] * 11
    assert stage["tiles"][1][0] == "#01"
    # 地面通道：road 是可部署近战位（buildableType 1），start 格不可部署。
    assert stage["tiles"][1][4] == ".10"
    assert stage["tiles"][1][9] == ".00"
    # 起点/终点来自 isStart/isEnd 布尔，按文件行序（tiles[0] 就是第 0 行）。
    assert stage["start"] == [[10, 1], [10, 3], [10, 5]]
    assert stage["end"] == [[0, 3]]


def test_start_and_end_come_from_booleans_not_tile_keys(local_tiles: None) -> None:
    """GT-1：isStart 有 6 格、tile_start 只有 3 格；isEnd 还落在一格 tile_start 上。

    如果起点/终点按 tileKey 推导，这关会得到完全不同的答案（规格 §3.4 第 5 条）。
    """
    stage = load_stage_layout("a001_01")

    assert stage["code"] == "GT-1"
    assert stage["start"] == [[9, 1], [7, 3], [8, 3], [9, 3], [8, 4], [9, 5]]
    assert stage["end"] == [[0, 2], [9, 3], [0, 5]]
    # (9, 3) 同时是起点与终点，且它的 tileKey 是 tile_start —— 只按 tileKey 推不出它。
    assert [9, 3] in stage["start"] and [9, 3] in stage["end"]
    assert stage["tiles"][3][9] == ".00"


def test_ambiguous_code_picks_the_first_stage_id_and_counts_candidates(
    local_tiles: None,
) -> None:
    stage = load_stage_layout("ISW-NO")

    # 554 个候选按 stageId 字典序取第一条。
    assert stage["candidates"] == 554
    assert stage["stage_id"] == "ro1_e_1_1"
    assert stage["tiles"], "胜出关卡必须真的读到 tile 文件"


def test_stage_id_query_is_case_insensitive_and_wins_over_code(
    local_tiles: None,
) -> None:
    stage = load_stage_layout("MAIN_01-07")

    assert stage["stage_id"] == "main_01-07"
    # 精确命中 stageId 时候选数就是命中数（1），不再按 code 摊开。
    assert stage["candidates"] == 1


def test_legend_only_lists_codes_present_in_this_stage(local_tiles: None) -> None:
    stage = load_stage_layout("1-7")
    legend = stage["legend"]

    terrain_codes = [item["code"] for item in legend if len(item["code"]) == 1]
    assert terrain_codes == ["#", "."]
    assert all(item["label"] == TERRAIN_LABELS[item["code"]] for item in legend if len(item["code"]) == 1)

    combos = {item["code"] for item in legend if len(item["code"]) == 2}
    assert combos == {"00", "01", "10", "21"}
    assert {item["label"] for item in legend if item["code"] == "21"} == {
        "可部署类型 2 · 高台"
    }
    # 图例里的地形顺序固定，前端只按后端给的顺序渲染。
    assert terrain_codes == [code for code in TERRAIN_ORDER if code in {"#", "."}]


def test_unknown_stage_raises_resource_asset_not_found(local_tiles: None) -> None:
    with pytest.raises(AppError) as error:
        load_stage_layout("绝对不是关卡-999")

    assert error.value.code is ErrorCode.RESOURCE_ASSET_NOT_FOUND


def test_empty_query_raises_resource_asset_not_found(local_tiles: None) -> None:
    with pytest.raises(AppError) as error:
        load_stage_layout("   ")

    assert error.value.code is ErrorCode.RESOURCE_ASSET_NOT_FOUND


# ----------------------------------------------------------------------
# 资源层：反向查找、目录缺失回落、索引缺失/损坏
# ----------------------------------------------------------------------


def test_layers_missing_falls_back_to_the_base_layer(
    layered: _Layers, monkeypatch: pytest.MonkeyPatch
) -> None:
    _write_stage(
        layered.base,
        stage_id="main_01-01",
        level_id="obt/main/level_main_01-01",
        code="1-1",
        tiles=[[_tile("tile_road", buildable=1), _tile("tile_start", start=True)]],
    )

    stage = load_stage_layout("1-1")

    assert (stage["width"], stage["height"]) == (2, 1)
    assert stage["start"] == [[1, 0]]
    # 增量层目录不存在是常态，不该报错。
    assert not layered.cache.exists() and not layered.custom.exists()


def test_custom_layer_overrides_cache_and_base(layered: _Layers) -> None:
    for root, width in (
        (layered.base, 1),
        (layered.cache, 2),
        (layered.custom, 3),
    ):
        _write_stage(
            root,
            stage_id="main_09-09",
            level_id="obt/main/level_main_09-09",
            code="9-9",
            tiles=[[_tile("tile_road", buildable=1) for _ in range(width)]],
        )

    stage = load_stage_layout("9-9")

    # custom 覆盖 cache 覆盖基础层：宽度 3 的那一份胜出。
    assert stage["width"] == 3


def test_reads_from_the_winning_layer_when_a_lower_layer_has_no_index(
    layered: _Layers,
) -> None:
    _write_stage(
        layered.custom,
        stage_id="main_08-08",
        level_id="obt/main/level_main_08-08",
        code="8-8",
        tiles=[[_tile("tile_wall", buildable=2, height=1)]],
    )

    stage = load_stage_layout("8-8")

    assert stage["tiles"] == [["#21"]]


def test_missing_overview_raises_resource_load_failed(layered: _Layers) -> None:
    with pytest.raises(AppError) as error:
        load_stage_layout("1-7")

    assert error.value.code is ErrorCode.RESOURCE_LOAD_FAILED
    assert error.value.details == {"resource": f"{TILE_POS_DIRECTORY}/overview.json"}


@pytest.mark.parametrize("payload", ["{ not json", "[]", "{}", '{"k": 1}'])
def test_corrupt_overview_raises_resource_load_failed(
    layered: _Layers, payload: str
) -> None:
    directory = layered.base / TILE_POS_DIRECTORY
    directory.mkdir(parents=True)
    (directory / "overview.json").write_text(payload, encoding="utf-8")

    with pytest.raises(AppError) as error:
        load_stage_layout("1-7")

    assert error.value.code is ErrorCode.RESOURCE_LOAD_FAILED


def test_corrupt_stage_file_raises_resource_load_failed(layered: _Layers) -> None:
    _write_stage(
        layered.base,
        stage_id="main_07-07",
        level_id="obt/main/level_main_07-07",
        code="7-7",
        tiles=[[_tile("tile_road", buildable=1)]],
    )
    directory = layered.base / TILE_POS_DIRECTORY
    (directory / "main_07-07-obt-main-level_main_07-07.json").write_text(
        '{"tiles": [[{"noTileKey": true}]]}', encoding="utf-8"
    )

    with pytest.raises(AppError) as error:
        load_stage_layout("7-7")

    assert error.value.code is ErrorCode.RESOURCE_LOAD_FAILED
    assert error.value.details == {
        "resource": f"{TILE_POS_DIRECTORY}/main_07-07-obt-main-level_main_07-07.json"
    }


def test_missing_stage_file_raises_resource_asset_not_found(layered: _Layers) -> None:
    directory = layered.base / TILE_POS_DIRECTORY
    directory.mkdir(parents=True)
    (directory / "overview.json").write_text(
        json.dumps(
            {
                "main_06-06-obt/main/level_main_06-06": {
                    "code": "6-6",
                    "stageId": "main_06-06",
                    "levelId": "obt/main/level_main_06-06",
                    "filename": "main_06-06-obt-main-level_main_06-06.json",
                    "name": "缺失",
                    "width": 1,
                    "height": 1,
                }
            }
        ),
        encoding="utf-8",
    )

    with pytest.raises(AppError) as error:
        load_stage_layout("6-6")

    assert error.value.code is ErrorCode.RESOURCE_ASSET_NOT_FOUND


# ----------------------------------------------------------------------
# 文件定位：filename 不在磁盘上时按 `f"{stageId}-"` 前缀找（levelId 带 `/`）
# ----------------------------------------------------------------------


def test_missing_filename_falls_back_to_prefix_listing(layered: _Layers) -> None:
    """levelId 里的 `/` 拼不出文件名，前缀匹配必须用它替换后的形态。"""
    directory = layered.base / TILE_POS_DIRECTORY
    directory.mkdir(parents=True)
    filename = "main_05-05-obt-main-level_main_05-05.json"
    (directory / filename).write_text(
        json.dumps(
            {
                "code": "5-5",
                "stageId": "main_05-05",
                "levelId": "obt/main/level_main_05-05",
                "name": "前缀",
                "width": 2,
                "height": 1,
                "tiles": [[_tile("tile_road", buildable=1), _tile("tile_hole")]],
            }
        ),
        encoding="utf-8",
    )
    (directory / "overview.json").write_text(
        json.dumps(
            {
                "main_05-05-obt/main/level_main_05-05": {
                    "code": "5-5",
                    "stageId": "main_05-05",
                    "levelId": "obt/main/level_main_05-05",
                    "filename": "已经不存在的文件名.json",
                    "name": "前缀",
                    "width": 2,
                    "height": 1,
                }
            }
        ),
        encoding="utf-8",
    )

    stage = load_stage_layout("5-5")

    assert stage["tiles"] == [[".10", "~00"]]
    assert stage["stage_id"] == "main_05-05"


def test_filename_escaping_the_layer_is_rejected(layered: _Layers, tmp_path: Path) -> None:
    directory = layered.base / TILE_POS_DIRECTORY
    directory.mkdir(parents=True)
    outside = tmp_path / "outside.json"
    outside.write_text(
        json.dumps({"tiles": [[_tile("tile_road", buildable=1)]]}), encoding="utf-8"
    )
    (directory / "overview.json").write_text(
        json.dumps(
            {
                "main_04-04-obt/main/level_main_04-04": {
                    "code": "4-4",
                    "stageId": "main_04-04",
                    "levelId": "obt/main/level_main_04-04",
                    "filename": "../../outside.json",
                    "name": "越界",
                    "width": 1,
                    "height": 1,
                }
            }
        ),
        encoding="utf-8",
    )

    with pytest.raises(AppError) as error:
        load_stage_layout("4-4")

    assert error.value.code is ErrorCode.RESOURCE_ASSET_NOT_FOUND


# ----------------------------------------------------------------------
# 归一化与映射表覆盖率
# ----------------------------------------------------------------------


def test_unknown_tile_key_falls_back_to_plain_ground(layered: _Layers) -> None:
    _write_stage(
        layered.base,
        stage_id="main_03-03",
        level_id="obt/main/level_main_03-03",
        code="3-3",
        tiles=[
            [
                _tile("tile_全新地形"),
                _tile("tile_road", buildable=7),
                _tile("tile_road", height=9),
            ]
        ],
    )

    stage = load_stage_layout("3-3")

    # 表外新键 → 普通地面；buildableType / heightType 收敛到 0–3 / 0–1。
    assert stage["tiles"][0] == [".00", ".30", ".01"]


def test_tile_key_table_covers_every_local_key() -> None:
    """夹具是本地 107 个 ``tileKey`` 的快照；资源更新冒出新 key 时这条测试变红。"""
    fixture = json.loads(
        (REPO_ROOT / "tests" / "fixtures" / "tile_keys.json").read_text(encoding="utf-8")
    )
    keys = fixture["keys"]

    assert keys, "夹具不能是空的"
    assert len(keys) == len(set(keys)), "夹具里的 tileKey 必须唯一"
    missing = sorted(set(keys) - set(TILE_KEY_TERRAIN))
    assert missing == [], f"映射表缺这些 tileKey：{missing}"
    assert {TILE_KEY_TERRAIN[key] for key in keys} <= set(TERRAIN_LABELS)
