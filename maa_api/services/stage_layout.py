"""关卡布局：从本地 ``Arknights-Tile-Pos`` 解析并归一化成网格码（M23 §3.4）。

数据链与规则（与规格 §3.4 一一对应）：

1. 资源层按 :func:`iter_resource_roots` 的**反向**顺序找 ``Arknights-Tile-Pos/
   overview.json``（custom 覆盖 cache 覆盖基础层），命中层里的关卡文件才算数；
2. ``overview.json`` 是 ``{f"{stageId}-{levelId.replace('/','-')}": {...}}`` 的 dict，
   按文件签名缓存；
3. 定位顺序：``stageId``/``levelId`` 精确匹配（大小写不敏感）→ ``code`` 精确匹配；
   多命中按 ``stageId`` 字典序取第一条并带出 ``candidates``；都没命中**不**在这里回落
   ``StageResolver``（它是 async 且要内核），由工具层决定兜底顺序；
4. 读文件先按 overview 的 ``filename``；不在磁盘上时用同一目录的文件名清单按
   ``f"{stageId}-"`` 前缀匹配（**绝不**把含 ``/`` 的 ``levelId`` 拼进 glob）；
5. 单格归一成 3 字符 ``<地形><可部署><高度>``；起点/终点来自 ``isStart``/``isEnd``
   布尔而不是 ``tileKey``，单独出坐标数组，行序保持文件顺序。
"""

from __future__ import annotations

import json
import threading
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from maa_api.domain.errors import AppError, ErrorCode
from maa_api.services.resource_index import iter_resource_roots

__all__ = [
    "TILE_KEY_TERRAIN",
    "TERRAIN_LABELS",
    "TERRAIN_ORDER",
    "HEIGHT_LABELS",
    "load_stage_layout",
]

#: tile 数据目录（相对每一个资源层）。
TILE_POS_DIRECTORY = "Arknights-Tile-Pos"
OVERVIEW_FILENAME = "overview.json"

#: 五类地形的中性标签（规格 §3.4）。顺序也是图例顺序，前端只渲染后端给的项。
TERRAIN_LABELS: dict[str, str] = {
    "#": "阻挡",
    "~": "坑洞",
    "w": "水域",
    "!": "特殊地形",
    ".": "普通地面",
}
TERRAIN_ORDER = ("#", "~", "w", "!", ".")

#: ``heightType`` 的中性标签。
HEIGHT_LABELS = {"0": "地面", "1": "高台"}

#: 表外新键的兜底地形（规格 §3.4：``.`` 普通地面，其余未特别标记者）。
DEFAULT_TERRAIN = "."

#: ``tileKey`` → 五类地形的**单一真相源**。本地 4186 张关卡图共 107 个不同
#: ``tileKey``，全部显式列在这里；``tests/fixtures/tile_keys.json`` 是这份清单的
#: 夹具，测试断言零遗漏 —— 资源更新冒出新键时那条测试变红，提醒补表。
#: 分类口径：名字含 forbidden / wall / fence 的归阻挡；hole / empty / rabbithole 归
#: 坑洞；sea / water / puddle 归水域；带机制语义的（毒雾、火山、传送、草丛、冰面、
#: 治疗格、目标点、菌毯、流沙、活动与生息演算专用地形等）归特殊地形；road / floor /
#: 起终点等普通地面归普通地面。
TILE_KEY_TERRAIN: dict[str, str] = {
    # 阻挡：禁入格、墙、栅栏，以及同形的固定障碍。
    "tile_forbidden": "#",
    "tile_wall": "#",
    "tile_fence": "#",
    "tile_fence_bound": "#",
    "tile_passable_wall": "#",
    "tile_passable_wall_forbidden": "#",
    "tile_wooden_wall": "#",
    "tile_toxicwall": "#",
    "tile_sleep_wall": "#",
    "tile_pollution_wall": "#",
    "tile_yinyang_wall": "#",
    "tile_ftplant_wall": "#",
    "tile_replace_wall": "#",
    "tile_ristar_road_forbidden": "#",
    "tile_gazebo": "#",
    "tile_rarope": "#",
    "tile_icetur_rb": "#",
    "tile_icetur_lt": "#",
    "tile_icetur_lb": "#",
    "tile_icetur_rt": "#",
    # 坑洞：洞、空格与兔子洞。
    "tile_hole": "~",
    "tile_empty": "~",
    "tile_rabbithole_in_1": "~",
    "tile_rabbithole_out_1": "~",
    "tile_rabbithole_out_2": "~",
    # 水域：各类海、深水与水洼。
    "tile_codpsea": "w",
    "tile_deepsea": "w",
    "tile_deepwater": "w",
    "tile_xbdpsea": "w",
    "tile_puddle": "w",
    # 特殊机制：毒雾、火山、传送、草丛、冰面、治疗格、目标点、菌毯、流沙、活动专用地形等。
    "tile_telin": "!",
    "tile_telout": "!",
    "tile_creep": "!",
    "tile_creepf": "!",
    "tile_toxic": "!",
    "tile_toxicroad": "!",
    "tile_toxichill": "!",
    "tile_icestr": "!",
    "tile_football": "!",
    "tile_volcano": "!",
    "tile_volcano_emp": "!",
    "tile_volspread": "!",
    "tile_grass": "!",
    "tile_infection": "!",
    "tile_infection_dqq": "!",
    "tile_quicksand": "!",
    "tile_mire": "!",
    "tile_healing": "!",
    "tile_enemygoal": "!",
    "tile_allygoal": "!",
    "tile_dygmny_1": "!",
    "tile_dygmny_2": "!",
    "tile_dygmny_3": "!",
    "tile_yinyang_road": "!",
    "tile_yinyang_switch": "!",
    "tile_corrosion": "!",
    "tile_corrosion_2": "!",
    "tile_smog": "!",
    "tile_pollution_road": "!",
    "tile_pollution_roadf": "!",
    "tile_grvtybtn": "!",
    "tile_steam": "!",
    "tile_defbreak": "!",
    "tile_defup": "!",
    "tile_balloon": "!",
    "tile_balloon_fbd": "!",
    "tile_bigforce": "!",
    "tile_cnvfuse": "!",
    "tile_cnvfuse_fbd": "!",
    "tile_achand": "!",
    "tile_merope": "!",
    "tile_ristar_road": "!",
    "tile_sleep_road": "!",
    "tile_act27side": "!",
    "tile_act48side": "!",
    "tile_acshop": "!",
    "tile_sandrope": "!",
    "tile_sandrope_2": "!",
    "tile_rcm_crate": "!",
    "tile_rcm_operator": "!",
    "tile_mpprts_enemy_born": "!",
    "tile_flower": "!",
    "tile_flowerf": "!",
    "tile_ftplant_road": "!",
    "tile_ftplant_roadf": "!",
    "tile_magic_circle": "!",
    "tile_magic_circle_h": "!",
    "tile_reed": "!",
    "tile_reedw": "!",
    "tile_reedf": "!",
    "tile_stairs": "!",
    "tile_character": "!",
    "tile_characterf": "!",
    "tile_green": "!",
    # 普通地面：路、地板与起终点。
    "tile_road": ".",
    "tile_floor": ".",
    "tile_start": ".",
    "tile_end": ".",
    "tile_start_cooperate": ".",
    "tile_end_cooperate": ".",
    "tile_start_dqq": ".",
    "tile_end_dqq": ".",
    "tile_flystart": ".",
    "tile_ftstart": ".",
    "tile_bnspck_road": ".",
    "tile_replace_road": ".",
    "tile_woodrd": ".",
}


@dataclass(frozen=True, slots=True)
class _Entry:
    code: str | None
    stage_id: str
    level_id: str | None
    filename: str
    name: str | None
    width: int | None
    height: int | None


@dataclass(frozen=True, slots=True)
class _OverviewCache:
    path: Path
    signature: tuple[int, int, int, int]
    directory: Path
    entries: tuple[_Entry, ...]


_CACHE_LOCK = threading.Lock()
_OVERVIEW_CACHE: _OverviewCache | None = None


def load_stage_layout(query: str) -> dict[str, Any]:
    """按关卡名定位并归一化一张关卡布局（规格 §3.4）。

    返回值是**快照**：资源更新后不会变，重发一次即可。未知关卡抛
    ``RESOURCE_ASSET_NOT_FOUND``；索引或关卡文件缺失、损坏抛 ``RESOURCE_LOAD_FAILED``。
    """
    entries, directory = _load_overview()
    matches = _match(entries, query)
    if not matches:
        raise AppError(ErrorCode.RESOURCE_ASSET_NOT_FOUND, f"关卡不存在：{query}")
    winner = matches[0]
    payload, resource = _read_stage_file(directory, winner)

    tiles, start, end = _normalize_tiles(payload, resource)
    height = len(tiles)
    width = len(tiles[0])
    return {
        "code": winner.code or "",
        "name": winner.name or "",
        "stage_id": winner.stage_id,
        "width": width,
        "height": height,
        "candidates": len(matches),
        "tiles": tiles,
        "start": start,
        "end": end,
        "legend": _legend(tiles),
    }


def _load_overview() -> tuple[tuple[_Entry, ...], Path]:
    """按反向层序找第一个存在的 ``overview.json``，按签名缓存。"""
    global _OVERVIEW_CACHE

    for root in reversed(iter_resource_roots()):
        directory = root / TILE_POS_DIRECTORY
        path = directory / OVERVIEW_FILENAME
        try:
            resolved_directory = directory.resolve()
            resolved_path = path.resolve()
            resolved_path.relative_to(resolved_directory)
            signature = _file_signature(resolved_path)
        except (OSError, RuntimeError, ValueError):
            continue
        return _cached_overview(resolved_path, resolved_directory, signature)

    raise AppError(
        ErrorCode.RESOURCE_LOAD_FAILED,
        "无法读取本地关卡索引",
        {"resource": f"{TILE_POS_DIRECTORY}/{OVERVIEW_FILENAME}"},
    )


def _cached_overview(
    path: Path, directory: Path, signature: tuple[int, int, int, int]
) -> tuple[tuple[_Entry, ...], Path]:
    global _OVERVIEW_CACHE

    with _CACHE_LOCK:
        cached = _OVERVIEW_CACHE
        if (
            cached is not None
            and cached.path == path
            and cached.signature == signature
        ):
            return cached.entries, cached.directory

        payload = _read_json(path, f"{TILE_POS_DIRECTORY}/{OVERVIEW_FILENAME}")
        try:
            # 读取途中被替换的文件不能让签名与数据错配（与 resources.py 同款处理）。
            after_read = _file_signature(path)
            if after_read != signature:
                signature = after_read
                payload = _read_json(path, f"{TILE_POS_DIRECTORY}/{OVERVIEW_FILENAME}")
                signature = _file_signature(path)
        except OSError as exc:
            raise _load_error(f"{TILE_POS_DIRECTORY}/{OVERVIEW_FILENAME}") from exc
        entries = _parse_overview(payload)
        _OVERVIEW_CACHE = _OverviewCache(path, signature, directory, entries)
        return entries, directory


def _parse_overview(payload: Any) -> tuple[_Entry, ...]:
    if not isinstance(payload, dict) or not payload:
        raise _load_error(f"{TILE_POS_DIRECTORY}/{OVERVIEW_FILENAME}")

    entries: list[_Entry] = []
    for key, record in payload.items():
        if not isinstance(key, str) or not isinstance(record, dict):
            raise _load_error(f"{TILE_POS_DIRECTORY}/{OVERVIEW_FILENAME}")
        stage_id = record.get("stageId")
        filename = record.get("filename")
        if not _non_empty_str(stage_id) or not _non_empty_str(filename):
            raise _load_error(f"{TILE_POS_DIRECTORY}/{OVERVIEW_FILENAME}")
        entries.append(
            _Entry(
                code=_optional_str(record.get("code")),
                stage_id=stage_id,
                level_id=_optional_str(record.get("levelId")),
                filename=filename,
                name=_optional_str(record.get("name")),
                width=_optional_int(record.get("width")),
                height=_optional_int(record.get("height")),
            )
        )
    return tuple(entries)


def _match(entries: tuple[_Entry, ...], query: str) -> list[_Entry]:
    """精确定位关卡：stageId/levelId 优先，其次 code；多命中按 stageId 字典序。"""
    folded = query.strip().casefold()
    if not folded:
        return []
    exact = [
        entry
        for entry in entries
        if entry.stage_id.casefold() == folded
        or (entry.level_id is not None and entry.level_id.casefold() == folded)
    ]
    matched = exact or [
        entry
        for entry in entries
        if entry.code is not None and entry.code.casefold() == folded
    ]
    return sorted(matched, key=lambda entry: (entry.stage_id, entry.level_id or ""))


def _read_stage_file(directory: Path, entry: _Entry) -> tuple[Any, str]:
    """先按 overview 的 filename 读；不在磁盘上时按 ``f"{stageId}-"`` 前缀找。"""
    filename = _confined_file(directory, entry.filename)
    if filename is None:
        filename = _prefix_match(directory, entry)
    if filename is None:
        raise AppError(
            ErrorCode.RESOURCE_ASSET_NOT_FOUND,
            f"关卡数据文件缺失：{entry.stage_id}",
            {"stage_id": entry.stage_id, "filename": entry.filename},
        )
    resource = f"{TILE_POS_DIRECTORY}/{filename.name}"
    return _read_json(filename, resource), resource


def _confined_file(directory: Path, filename: str) -> Path | None:
    """文件名必须是这一层目录里的普通文件，且解析后仍在目录内。"""
    try:
        resolved_directory = directory.resolve()
        candidate = (directory / filename).resolve()
        candidate.relative_to(resolved_directory)
        if not candidate.is_file():
            return None
    except (OSError, RuntimeError, ValueError):
        return None
    return candidate


def _prefix_match(directory: Path, entry: _Entry) -> Path | None:
    """按 ``f"{stageId}-"`` 前缀在目录清单里找关卡文件。

    **不**把 ``levelId`` 拼进 glob：``levelId`` 里带 ``/``（``obt/main/level_main_01-07``），
    拼出来的模式永远匹配不到。前缀命中多个时优先用 ``stageId-levelId`` 推出的确定文件名，
    再退到字典序第一条。
    """
    prefix = f"{entry.stage_id}-"
    try:
        names = sorted(
            item.name
            for item in directory.iterdir()
            if item.is_file() and item.name.startswith(prefix) and item.suffix == ".json"
        )
    except OSError:
        return None
    if not names:
        return None
    if entry.level_id:
        expected = f"{prefix}{entry.level_id.replace('/', '-')}.json"
        if expected in names:
            names = [expected]
    return _confined_file(directory, names[0])


def _normalize_tiles(
    payload: Any, resource: str
) -> tuple[list[list[str]], list[list[int]], list[list[int]]]:
    """把 ``tiles`` 归一成 3 字符码网格，并从布尔取出起点/终点坐标。"""
    if not isinstance(payload, dict):
        raise _load_error(resource)
    raw_tiles = payload.get("tiles")
    if not isinstance(raw_tiles, list) or not raw_tiles:
        raise _load_error(resource)

    tiles: list[list[str]] = []
    start: list[list[int]] = []
    end: list[list[int]] = []
    width: int | None = None
    for row_index, raw_row in enumerate(raw_tiles):
        if not isinstance(raw_row, list) or not raw_row:
            raise _load_error(resource)
        if width is None:
            width = len(raw_row)
        elif len(raw_row) != width:
            raise _load_error(resource)

        row: list[str] = []
        for column, raw_tile in enumerate(raw_row):
            if not isinstance(raw_tile, dict):
                raise _load_error(resource)
            tile_key = raw_tile.get("tileKey")
            if not _non_empty_str(tile_key):
                raise _load_error(resource)
            terrain = TILE_KEY_TERRAIN.get(tile_key, DEFAULT_TERRAIN)
            buildable = _clamped_int(raw_tile.get("buildableType"), 0, 3, 0)
            height = _clamped_int(raw_tile.get("heightType"), 0, 1, 0)
            row.append(f"{terrain}{buildable}{height}")
            if raw_tile.get("isStart") is True:
                start.append([column, row_index])
            if raw_tile.get("isEnd") is True:
                end.append([column, row_index])
        tiles.append(row)
    return tiles, start, end


def _legend(tiles: list[list[str]]) -> list[dict[str, str]]:
    """只列本关出现过的地形与可部署/高度组合，标签保持中性（规格 §3.4）。"""
    terrain_present: set[str] = set()
    combos_present: set[str] = set()
    for row in tiles:
        for code in row:
            terrain_present.add(code[0])
            combos_present.add(code[1:])
    legend = [
        {"code": terrain, "label": TERRAIN_LABELS[terrain]}
        for terrain in TERRAIN_ORDER
        if terrain in terrain_present
    ]
    legend.extend(
        {
            "code": combo,
            "label": f"可部署类型 {combo[0]} · {HEIGHT_LABELS[combo[1]]}",
        }
        for combo in sorted(combos_present)
    )
    return legend


def _read_json(path: Path, resource: str) -> Any:
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise _load_error(resource) from exc


def _file_signature(path: Path) -> tuple[int, int, int, int]:
    stat = path.stat()
    if not path.is_file():
        raise OSError("overview is not a file")
    return stat.st_dev, stat.st_ino, stat.st_size, stat.st_mtime_ns


def _load_error(resource: str) -> AppError:
    return AppError(
        ErrorCode.RESOURCE_LOAD_FAILED,
        "无法读取本地关卡数据",
        {"resource": resource},
    )


def _non_empty_str(value: Any) -> bool:
    return isinstance(value, str) and bool(value.strip())


def _optional_str(value: Any) -> str | None:
    return value if isinstance(value, str) and value else None


def _optional_int(value: Any) -> int | None:
    if isinstance(value, bool) or not isinstance(value, int):
        return None
    return value


def _clamped_int(value: Any, minimum: int, maximum: int, default: int) -> int:
    if isinstance(value, bool) or not isinstance(value, int):
        return default
    return min(max(value, minimum), maximum)
