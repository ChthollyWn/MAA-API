"""关卡文件解析（规格 §9.3、§9.2 T1 第①条）：纯函数，拆包关卡 JSON 进、结构出。

不联网、不读盘；取数在 ``maa_api/services/external/gamedata.py``，wire 形状在
``maa_api/services/game/level_data.py``。

## 坐标换算（T1 探针结论，2026-10-10 实测）

数据来源：`tests/fixtures/gamedata/level_main_01-07.json`（拆包镜像
``fastly.jsdelivr.net/gh/Kengxxiao/ArknightsGameData@master/zh_CN/gamedata/levels/obt/main/level_main_01-07.json``，
HTTP 200、87 KB，与作业站那一关的 ``levelId`` 相同）与
`tests/fixtures/gamedata/copilot_22311.json`（作业站 ``GET /copilot/get/22311``，
标题「1-7 稀音 单人 61s 速刷 信赖 无专」，`stage_name=main_01-07`）。

**结论一：MAA 作业的 ``location`` 就是 ``[col, row]``，行自上而下数，原点左上，与文件里
的 Tile-Pos 坐标同一套，不需要翻转或换轴。**

``mapData.map`` 是 ``height`` 行 × ``width`` 列的地块**下标**矩阵，行序自上而下：

```
map[0] = [66, 67, ..., 76]   # 最上面一行
map[6] = [ 0,  1, ..., 10]   # 最下面一行
```

``mapData.tiles`` 是一维数组，坐标与该矩阵的关系是

```
tiles[row * width + col] == tiles[map[row][col]]
```

即一维下标按**自下而上**的行号递增，而 ``map`` 矩阵的 ``row`` 是**自上而下**的行号
（``row == height - 1 - flat_row``）。因此要从 ``[col, row]`` 取地块，直接用
``tiles[map[row][col]]``，见 :func:`tile_index`——不要自己算一维下标。

三条互相独立的证据：

1. ``routes[*].startPosition/endPosition`` 写的是 ``{row, col}`` 对象：1-7 的 26 条路线里
   起点有 25 条落在 ``tile_start``、终点全部落在 ``tile_end``（另一条是 ``preview`` 用的
   占位路线，两端都在 ``tile_forbidden``）。把 ``{row: r, col: c}`` 直接读成
   ``location=[c, r]`` 时 51/52 个端点落在语义正确的地块上；换轴或翻行都对不上。
2. 作业 22311 的 5 个部署动作：``[8,2]`` 落在 ``tile_wall``（``buildableType=RANGED``、
   ``HIGHLAND``），另外 4 个 ``[8,5] [8,3] [8,1] [7,3]`` 全部落在 ``tile_road``
   （``buildableType=MELEE``、``LOWLAND``）——**5/5 落在可部署格**，且
   ``buildableType`` 与干员的地面/高台属性一致（近战占 LOWLAND、远程占 HIGHLAND）。
3. 一维下标若按 ``col * height + row`` 解释，``[8,2]`` 会落到 ``tile_forbidden``，与作业
   实际能三星通关矛盾。

**结论二：可部署格判定的两个字段。** ``buildableType`` 取值 ``NONE`` / ``MELEE`` /
``RANGED``（未观察到 ``ALL``，代码里按 ``3`` 兜底保留），``heightType`` 取值 ``LOWLAND`` /
``HIGHLAND``。近战（地面）干员只能放 ``MELEE``/``LOWLAND``，远程（高台）只能放
``RANGED``/``HIGHLAND``；``NONE`` 是任何干员都放不下的格子。1-7 的 77 格里 MELEE 23 格、
RANGED 16 格、NONE 38 格。

**结论三：地图编码复用 ``send_stage_map`` 的同一套。** 每格 3 字符
``<地形><可部署类型><高度>``，地形字符来自
:data:`~maa_api.services.stage_layout.TILE_KEY_TERRAIN`（``#`` 阻挡、``~`` 坑洞、``w`` 水域、
``!`` 特殊地形、``.`` 普通地面），可部署类型 ``0`` 不可部署 / ``1`` 近战 / ``2`` 远程 /
``3`` 两者，高度 ``0`` 地面 / ``1`` 高台。本模块直接复用那张表，**不另立一套编码**，
所以 ``get_stage_level_data`` 返回的网格与 ``send_stage_map`` 的 ``tiles`` 逐字符相同。

## 时间线口径

``waves[*].fragments[*]`` 是同一波里的片段，**按顺序累加** ``preDelay``；每个动作的绝对时刻
（秒）是 ``wave.preDelay + Σ(前面片段与当前片段的 preDelay) + action.preDelay``。实测 1-7
的第一个出怪在 6.0 秒（片段 3.0 + 动作 3.0），与该关 61 秒速刷作业的时间线一致。
"""

from __future__ import annotations

import json
import math
from dataclasses import dataclass, field
from typing import Any, Mapping, Sequence

from maa_api.domain.errors import AppError, ErrorCode
from maa_api.services.stage_layout import (
    DEFAULT_TERRAIN,
    HEIGHT_LABELS,
    TERRAIN_LABELS,
    TERRAIN_ORDER,
    TILE_KEY_TERRAIN,
)

__all__ = [
    "BUILDABLE_LABELS",
    "ENCODING",
    "MAX_LEVEL_BYTES",
    "LevelFile",
    "LevelMap",
    "LevelOptions",
    "LevelRoute",
    "LevelWave",
    "is_buildable",
    "pack_level_data",
    "parse_level_file",
    "tile_index",
]

#: 单个工具结果序列化后的上限（规格 §4.4：24 KB）。
MAX_LEVEL_BYTES = 24 * 1024

#: 网格编码说明（进返回体，模型据此读网格；与 ``send_stage_map`` 一致）。
ENCODING = (
    "每格 3 字符：<地形><可部署类型><高度>。地形 # 阻挡、~ 坑洞、w 水域、! 特殊地形、"
    ". 普通地面；可部署类型 0 不可部署、1 近战（地面）、2 远程（高台）、3 两者；"
    "高度 0 地面、1 高台。与 send_stage_map 的 tiles 同一套编码。"
)

#: 可部署类型的中性标签。
BUILDABLE_LABELS: dict[str, str] = {
    "0": "不可部署",
    "1": "近战（地面）",
    "2": "远程（高台）",
    "3": "近战或远程",
}

#: ``buildableType`` → 编码里的可部署类型字符。
_BUILDABLE_CODES: dict[str, str] = {"MELEE": "1", "RANGED": "2", "ALL": "3"}

#: ``heightType`` → 编码里的高度字符。
_HEIGHT_CODES: dict[str, str] = {"LOWLAND": "0", "HIGHLAND": "1"}

#: 出怪动作的类型（其余类型是演出、预览光标等，不进波次）。
_SPAWN_TYPES = frozenset({"SPAWN"})

#: 打包时的分级上限：波次最多 80 条、路线最多 12 条、每条路线的途经点最多 8 个。
MAX_PACKED_WAVES = 80
MAX_PACKED_SUMMARY_WAVES = 40
MAX_PACKED_ROUTES = 12
MAX_WAYPOINTS = 8
#: 仍超预算时逐级收紧途经点。
_WAYPOINT_STEPS = (8, 4, 2)


@dataclass(frozen=True, slots=True)
class LevelOptions:
    """``options`` 段（规格 §9.3 的五个字段 + 训练关标记）。"""

    deploy_limit: int | None = None
    life_points: int | None = None
    initial_cost: int | None = None
    max_cost: int | None = None
    cost_recovery: float | None = None

    def as_dict(self) -> dict[str, Any]:
        return {
            "deploy_limit": self.deploy_limit,
            "life_points": self.life_points,
            "initial_cost": self.initial_cost,
            "max_cost": self.max_cost,
            "cost_recovery": self.cost_recovery,
        }


@dataclass(frozen=True, slots=True)
class LevelMap:
    """``map`` 段：自上而下逐行的 3 字符码网格 + 图例 + 红蓝门坐标。"""

    width: int
    height: int
    grid: tuple[tuple[str, ...], ...]
    start: tuple[tuple[int, int], ...] = ()
    end: tuple[tuple[int, int], ...] = ()
    buildable: tuple[tuple[int, int], ...] = ()

    def as_dict(self) -> dict[str, Any]:
        return {
            "width": self.width,
            "height": self.height,
            "encoding": ENCODING,
            "grid": [list(row) for row in self.grid],
            "legend": self.legend(),
            "start": [list(point) for point in self.start],
            "end": [list(point) for point in self.end],
            "buildable_tiles": len(self.buildable),
            "note": (
                "坐标 [x, y] 与 MAA 作业的 location 一致（原点左上，x 向右、y 向下）；"
                "start 是红门（敌人出生点），end 是蓝门（目标点）。"
            ),
        }

    def legend(self) -> list[dict[str, str]]:
        """只列本关出现过的地形与「可部署类型 + 高度」组合，标签保持中性。"""
        terrain_present: set[str] = set()
        combos_present: set[str] = set()
        for row in self.grid:
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


@dataclass(frozen=True, slots=True)
class LevelRoute:
    """``routes`` 段的一条路线（坐标已换算成 ``location``）。"""

    index: int
    start: tuple[int, int]
    end: tuple[int, int]
    waypoints: tuple[dict[str, Any], ...] = ()
    wait_seconds: float = 0.0

    def as_dict(self) -> dict[str, Any]:
        return {
            "index": self.index,
            "start": list(self.start),
            "end": list(self.end),
            "waypoints": [dict(point) for point in self.waypoints],
            "wait_seconds": self.wait_seconds,
        }


@dataclass(frozen=True, slots=True)
class LevelWave:
    """``waves`` 段的一条出怪动作（时刻是绝对秒）。"""

    wave: int
    fragment: int
    time: float
    enemy_key: str
    enemy_name: str | None
    count: int
    interval: float
    route: int

    def as_dict(self) -> dict[str, Any]:
        return {
            "wave": self.wave,
            "fragment": self.fragment,
            "time": self.time,
            "enemy_key": self.enemy_key,
            "enemy_name": self.enemy_name,
            "count": self.count,
            "interval": self.interval,
            "route": self.route,
        }


@dataclass(frozen=True, slots=True)
class LevelFile:
    """一份关卡文件的解析结果（未打包，字段齐全）。"""

    options: LevelOptions = field(default_factory=LevelOptions)
    map: LevelMap | None = None
    routes: tuple[LevelRoute, ...] = ()
    waves: tuple[LevelWave, ...] = ()
    enemy_names: Mapping[str, str] = field(default_factory=dict)

    def routes_used(self) -> tuple[LevelRoute, ...]:
        """被出怪动作用到的路线（按路线编号升序）。"""
        used = {wave.route for wave in self.waves}
        return tuple(route for route in self.routes if route.index in used)


# --------------------------------------------------------------------------- #
# 纯工具
# --------------------------------------------------------------------------- #


def tile_index(payload: Mapping[str, Any], col: int, row: int) -> int | None:
    """``[col, row]`` → ``mapData.tiles`` 的下标（探针结论一）。

    ``mapData.map`` 的行序与 ``location`` 的 y 一致（都自上而下），所以直接查表；越界或
    形状不对返回 ``None``。**不要**自己按 ``col * height + row`` 算一维下标——那是另一个
    行序，会整体错位。
    """
    map_data = payload.get("mapData") if isinstance(payload, Mapping) else None
    rows = map_data.get("map") if isinstance(map_data, Mapping) else None
    if not isinstance(rows, Sequence) or isinstance(rows, (str, bytes)):
        return None
    if not 0 <= row < len(rows):
        return None
    line = rows[row]
    if not isinstance(line, Sequence) or isinstance(line, (str, bytes)):
        return None
    if not 0 <= col < len(line):
        return None
    value = line[col]
    return value if isinstance(value, int) and not isinstance(value, bool) else None


def _tile_at(payload: Mapping[str, Any], col: int, row: int) -> Mapping[str, Any] | None:
    index = tile_index(payload, col, row)
    if index is None:
        return None
    tiles = (payload.get("mapData") or {}).get("tiles")
    if not isinstance(tiles, Sequence) or isinstance(tiles, (str, bytes)):
        return None
    if not 0 <= index < len(tiles):
        return None
    tile = tiles[index]
    return tile if isinstance(tile, Mapping) else None


def is_buildable(tile: Mapping[str, Any] | None, *, ranged: bool | None = None) -> bool:
    """这一格能不能放干员；``ranged`` 给定时再判地面/高台是否匹配（探针结论二）。"""
    if not isinstance(tile, Mapping):
        return False
    code = _BUILDABLE_CODES.get(str(tile.get("buildableType") or "").upper(), "0")
    if code == "0":
        return False
    if ranged is None:
        return True
    height = _HEIGHT_CODES.get(str(tile.get("heightType") or "").upper(), "0")
    return (code == "2" and height == "1") if ranged else (code == "1" and height == "0")


def _code_of(tile: Mapping[str, Any]) -> str:
    """单格 → 3 字符码（与 ``send_stage_map`` 同一套，探针结论三）。"""
    terrain = TILE_KEY_TERRAIN.get(str(tile.get("tileKey") or ""), DEFAULT_TERRAIN)
    buildable = _BUILDABLE_CODES.get(str(tile.get("buildableType") or "").upper(), "0")
    height = _HEIGHT_CODES.get(str(tile.get("heightType") or "").upper(), "0")
    return f"{terrain}{buildable}{height}"


def _as_float(value: Any, default: float = 0.0) -> float:
    if isinstance(value, bool) or value is None:
        return default
    try:
        number = float(value)
    except (TypeError, ValueError):
        return default
    return number if math.isfinite(number) else default


def _as_int(value: Any) -> int | None:
    if isinstance(value, bool) or value is None:
        return None
    if isinstance(value, int):
        return value
    if isinstance(value, float) and value.is_integer():
        return int(value)
    return None


def _point(location: Mapping[str, Any] | None) -> tuple[int, int] | None:
    """``{row, col}`` → ``[col, row]``（探针结论一）。"""
    if not isinstance(location, Mapping):
        return None
    row = _as_int(location.get("row"))
    col = _as_int(location.get("col"))
    if row is None or col is None:
        return None
    return (col, row)


# --------------------------------------------------------------------------- #
# 解析
# --------------------------------------------------------------------------- #


def parse_level_file(payload: Any) -> LevelFile:
    """拆包关卡 JSON → :class:`LevelFile`；形状不对抛 ``EXTERNAL_RESPONSE_INVALID``。

    缺 ``mapData`` 或 ``mapData.tiles`` 视为结构不符（规格 §3.5：宁可报「取不到」，也不
    假装解析出一张空图）；波次与路线缺失只当空。
    """
    if not isinstance(payload, Mapping):
        raise _invalid("顶层不是对象")
    map_data = payload.get("mapData")
    if not isinstance(map_data, Mapping):
        raise _invalid("缺少 mapData")
    level_map = _parse_map(map_data)
    return LevelFile(
        options=_parse_options(payload.get("options")),
        map=level_map,
        routes=tuple(_parse_routes(payload.get("routes"))),
        waves=tuple(_parse_waves(payload)),
        enemy_names=_parse_enemy_names(payload.get("enemyDbRefs")),
    )


def _invalid(message: str) -> AppError:
    return AppError(
        ErrorCode.EXTERNAL_RESPONSE_INVALID,
        f"拆包关卡文件结构不符：{message}",
        {"kind": "gamedata"},
    )


def _parse_options(raw: Any) -> LevelOptions:
    options = raw if isinstance(raw, Mapping) else {}
    recovery = options.get("costIncreaseTime")
    return LevelOptions(
        deploy_limit=_as_int(options.get("characterLimit")),
        life_points=_as_int(options.get("maxLifePoint")),
        initial_cost=_as_int(options.get("initialCost")),
        max_cost=_as_int(options.get("maxCost")),
        cost_recovery=_as_float(recovery, default=0.0) if recovery is not None else None,
    )


def _parse_map(map_data: Mapping[str, Any]) -> LevelMap:
    rows = map_data.get("map")
    tiles = map_data.get("tiles")
    if not isinstance(rows, Sequence) or isinstance(rows, (str, bytes)) or not rows:
        raise _invalid("mapData.map 不是非空数组")
    if not isinstance(tiles, Sequence) or isinstance(tiles, (str, bytes)) or not tiles:
        raise _invalid("mapData.tiles 不是非空数组")

    grid: list[list[str]] = []
    start: list[tuple[int, int]] = []
    end: list[tuple[int, int]] = []
    buildable: list[tuple[int, int]] = []
    width: int | None = None
    for row, line in enumerate(rows):
        if not isinstance(line, Sequence) or isinstance(line, (str, bytes)) or not line:
            raise _invalid("mapData.map 里有空行")
        if width is None:
            width = len(line)
        elif len(line) != width:
            raise _invalid("mapData.map 每行长度不一致")
        codes: list[str] = []
        for col, value in enumerate(line):
            if not isinstance(value, int) or isinstance(value, bool) or not 0 <= value < len(tiles):
                raise _invalid("mapData.map 里有越界的地块下标")
            tile = tiles[value]
            if not isinstance(tile, Mapping):
                raise _invalid("mapData.tiles 里有非对象项")
            codes.append(_code_of(tile))
            # 红蓝门两种写法都认：关卡文件里用的是 `tile_start` / `tile_end` 地块，
            # 布尔字段是本地 Tile-Pos（`stage_layout`）的写法，留着兼容同一形状的输入。
            tile_key = str(tile.get("tileKey") or "")
            if tile.get("isStart") is True or tile_key.startswith("tile_start"):
                start.append((col, row))
            if tile.get("isEnd") is True or tile_key.startswith("tile_end"):
                end.append((col, row))
            if codes[-1][1] != "0":
                buildable.append((col, row))
        grid.append(codes)
    return LevelMap(
        width=width or 0,
        height=len(grid),
        grid=tuple(tuple(row) for row in grid),
        start=tuple(start),
        end=tuple(end),
        buildable=tuple(buildable),
    )


def _parse_enemy_names(raw: Any) -> dict[str, str]:
    """``enemyDbRefs`` → ``{key: 名字}``；没有 ``name`` 时不猜（规格 §3.5）。"""
    names: dict[str, str] = {}
    if not isinstance(raw, Sequence) or isinstance(raw, (str, bytes)):
        return names
    for entry in raw:
        if not isinstance(entry, Mapping):
            continue
        key = entry.get("id")
        name = entry.get("name")
        if isinstance(key, str) and isinstance(name, str) and name.strip():
            names.setdefault(key, name.strip())
    return names


def _parse_routes(raw: Any) -> list[LevelRoute]:
    if not isinstance(raw, Sequence) or isinstance(raw, (str, bytes)):
        return []
    routes: list[LevelRoute] = []
    for index, item in enumerate(raw):
        if not isinstance(item, Mapping):
            continue
        start = _point(item.get("startPosition"))
        end = _point(item.get("endPosition"))
        if start is None or end is None:
            continue
        waypoints: list[dict[str, Any]] = []
        wait_seconds = 0.0
        checkpoints = item.get("checkpoints")
        if isinstance(checkpoints, Sequence) and not isinstance(checkpoints, (str, bytes)):
            for checkpoint in checkpoints:
                if not isinstance(checkpoint, Mapping):
                    continue
                point = _point(checkpoint.get("position"))
                if point is None:
                    continue
                kind = str(checkpoint.get("type") or "").upper()
                delay = _as_float(checkpoint.get("time"))
                if kind == "WAIT":
                    wait_seconds += delay
                    waypoints.append({"x": point[0], "y": point[1], "type": "wait", "seconds": delay})
                elif kind == "MOVE":
                    waypoints.append({"x": point[0], "y": point[1], "type": "move"})
                # 其余类型（DISAPPEAR 等）没有走位语义，不计入途经点。
        routes.append(
            LevelRoute(
                index=index,
                start=start,
                end=end,
                waypoints=tuple(waypoints),
                wait_seconds=round(wait_seconds, 3),
            )
        )
    return routes


def _parse_waves(payload: Mapping[str, Any]) -> list[LevelWave]:
    raw_waves = payload.get("waves")
    names = _parse_enemy_names(payload.get("enemyDbRefs"))
    if not isinstance(raw_waves, Sequence) or isinstance(raw_waves, (str, bytes)):
        return []
    waves: list[LevelWave] = []
    for wave_index, wave in enumerate(raw_waves):
        if not isinstance(wave, Mapping):
            continue
        wave_delay = _as_float(wave.get("preDelay"))
        fragments = wave.get("fragments")
        if not isinstance(fragments, Sequence) or isinstance(fragments, (str, bytes)):
            continue
        fragment_delay = 0.0
        for fragment_index, fragment in enumerate(fragments):
            if not isinstance(fragment, Mapping):
                continue
            fragment_delay += _as_float(fragment.get("preDelay"))
            actions = fragment.get("actions")
            if not isinstance(actions, Sequence) or isinstance(actions, (str, bytes)):
                continue
            for action in actions:
                if not isinstance(action, Mapping):
                    continue
                if str(action.get("actionType") or "").upper() not in _SPAWN_TYPES:
                    continue
                key = action.get("key")
                route = _as_int(action.get("routeIndex"))
                if not isinstance(key, str) or not key or route is None:
                    continue
                waves.append(
                    LevelWave(
                        wave=wave_index,
                        fragment=fragment_index,
                        time=round(
                            wave_delay + fragment_delay + _as_float(action.get("preDelay")), 3
                        ),
                        enemy_key=key,
                        enemy_name=names.get(key),
                        count=max(_as_int(action.get("count")) or 1, 1),
                        interval=round(_as_float(action.get("interval"), default=1.0), 3),
                        route=route,
                    )
                )
    return sorted(waves, key=lambda item: (item.time, item.route))


# --------------------------------------------------------------------------- #
# 打包（体积 ≤ 24 KB）
# --------------------------------------------------------------------------- #


def pack_level_data(
    level: LevelFile,
    *,
    include: Sequence[str] = ("options", "map", "routes", "waves"),
    wave_detail: str = "summary",
) -> dict[str, Any]:
    """按 ``include`` 与 ``wave_detail`` 打包；结果保证 ≤ 24 KB（规格 §9.3、§4.4）。

    退让顺序（截断了什么一律写进 ``truncated``）：

    1. 合并波次：``summary`` 把同一波里连续的同敌人同路线同数量的动作合成一条；
    2. 有条数上限的字段先截到上限（波次 40/80、路线 12、每条路线的途经点 8）；
    3. 还超 24 KB：途经点按 8 → 4 → 2 收紧，再把波次砍一半（只砍一次，不把时间线砍空），
       最后才整条丢路线（从尾往前丢，前面的路线通常先用上）。

    :param wave_detail: ``summary``（默认）或 ``full``（每个动作一条）。两者都保留 ``time``
        （第一次出现的绝对秒）与 ``count``（合并后的总数）。
    :returns: ``{options?, map?, routes?, waves?, waves_detail?, truncated?, bytes}``。
    """
    payload: dict[str, Any] = {}
    if "options" in include:
        payload["options"] = level.options.as_dict()
    if "map" in include and level.map is not None:
        payload["map"] = level.map.as_dict()
    routes = level.routes_used()
    if "routes" in include:
        payload["routes"] = [route.as_dict() for route in routes]
    if "waves" in include:
        waves = _summary_waves(level.waves) if wave_detail == "summary" else list(level.waves)
        payload["waves"] = [wave.as_dict() for wave in waves]
        payload["waves_detail"] = wave_detail

    limit = MAX_PACKED_SUMMARY_WAVES if wave_detail == "summary" else MAX_PACKED_WAVES
    truncated: dict[str, Any] = {}
    # 1) 条数上限：每一条都是「这一类最多给多少」，与总体积无关。
    if "waves" in include and len(payload["waves"]) > limit:
        total_waves = len(payload["waves"])
        payload["waves"] = payload["waves"][:limit]
        truncated["waves"] = {
            "kept": limit,
            "total": total_waves,
            "reason": f"按 {wave_detail} 模式最多保留 {limit} 条出怪动作",
        }
    dropped_routes = 0
    if "routes" in include and len(payload["routes"]) > MAX_PACKED_ROUTES:
        dropped_routes = len(payload["routes"]) - MAX_PACKED_ROUTES
        payload["routes"] = payload["routes"][:MAX_PACKED_ROUTES]
    if "routes" in include and MAX_WAYPOINTS:
        trimmed = _trim_waypoints(payload["routes"], MAX_WAYPOINTS)
        if trimmed:
            payload["routes"] = trimmed
            truncated["waypoints"] = {
                "kept_per_route": MAX_WAYPOINTS,
                "reason": f"每条路线最多保留 {MAX_WAYPOINTS} 个途经点（首尾必留）",
            }

    # 2) 还超 24 KB：途经点再收紧，然后砍波次，最后丢路线。
    over_budget = _size_of(payload, truncated) > MAX_LEVEL_BYTES
    if over_budget and "routes" in include:
        for step in _WAYPOINT_STEPS:
            if step >= MAX_WAYPOINTS:
                continue
            trimmed = _trim_waypoints(payload["routes"], step)
            if not trimmed:
                continue
            payload["routes"] = trimmed
            truncated["waypoints"] = {
                "kept_per_route": step,
                "reason": "结果超过 24 KB，途经点收紧到 " + str(step) + " 个",
            }
            if _size_of(payload, truncated) <= MAX_LEVEL_BYTES:
                break
    if (
        "waves" in include
        and len(payload["waves"]) > 1
        and _size_of(payload, truncated) > MAX_LEVEL_BYTES
    ):
        kept = max(len(payload["waves"]) // 2, 1)
        payload["waves"] = payload["waves"][:kept]
        truncated["waves"] = {
            "kept": kept,
            "total": len(level.waves),
            "reason": "结果超过 24 KB，波次先砍一半",
        }
    kept_routes = len(payload["routes"]) if "routes" in include else 0
    while (
        "routes" in include
        and kept_routes > 0
        and _size_of(payload, truncated) > MAX_LEVEL_BYTES
    ):
        kept_routes -= 1
        dropped_routes += 1
        payload["routes"] = payload["routes"][:kept_routes]
        truncated["routes"] = {
            "kept": kept_routes,
            "total": len(routes),
            "reason": "结果超过 24 KB，路线按编号保留前若干条",
        }
    if "routes" not in truncated and dropped_routes:
        truncated["routes_extra"] = dropped_routes
    if truncated:
        payload["truncated"] = truncated
    payload["bytes"] = _size_of(payload, None)
    return payload


def _size_of(payload: Mapping[str, Any], truncated: Mapping[str, Any] | None) -> int:
    """序列化后的字节数（``ensure_ascii=False``，与工具结果同一口径）。"""
    body = dict(payload)
    if truncated is None:
        body.pop("truncated", None)
        body.pop("bytes", None)
    else:
        body["truncated"] = dict(truncated)
    return len(json.dumps(body, ensure_ascii=False, separators=(",", ":")).encode("utf-8"))


def _summary_waves(waves: Sequence[LevelWave]) -> list[LevelWave]:
    """``summary``：同波、同敌人、同路线、同数量的动作合并成一条（保留首次时刻）。

    只合并**相邻**动作：中间插了别的敌人就分成两条，时间线不会被抹平。合并后的
    ``count`` 是各条之和，``interval`` 取第一段的值。
    """
    merged: list[LevelWave] = []
    for wave in waves:
        previous = merged[-1] if merged else None
        if (
            previous is not None
            and previous.wave == wave.wave
            and previous.enemy_key == wave.enemy_key
            and previous.route == wave.route
            and previous.count == wave.count
        ):
            merged[-1] = LevelWave(
                wave=previous.wave,
                fragment=previous.fragment,
                time=previous.time,
                enemy_key=previous.enemy_key,
                enemy_name=previous.enemy_name,
                count=previous.count + wave.count,
                interval=previous.interval,
                route=previous.route,
            )
            continue
        merged.append(wave)
    return merged


def _trim_waypoints(routes: Sequence[Mapping[str, Any]], keep: int) -> list[dict[str, Any]]:
    """每条路线最多留 ``keep`` 个途经点：首尾必留，中间按间隔抽稀。"""
    trimmed: list[dict[str, Any]] = []
    changed = False
    for route in routes:
        points = list(route.get("waypoints") or ())
        if len(points) <= keep:
            trimmed.append(dict(route))
            continue
        changed = True
        if keep <= 1:
            kept = [points[-1]]
        else:
            step = (len(points) - 1) / (keep - 1)
            indexes = sorted({round(index * step) for index in range(keep)})
            kept = [points[index] for index in indexes]
        trimmed.append({**route, "waypoints": kept})
    return trimmed if changed else []
