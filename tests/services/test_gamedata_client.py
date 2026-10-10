"""拆包关卡文件客户端与坐标换算探针（规格 §9.3、§9.2 T1 第①条、附录 D）。

全部用 `tests/fixtures/gamedata/` 夹具与 `httpx.MockTransport`，不打网。

前两组用例是 T1 探针的**可执行结论**：

1. :func:`test_copilot_deployments_land_on_deployable_tiles` —— 作业站 22311（1-7，稀音单人）
   的**全部部署点**都落在可部署格子上，且 ``buildableType`` 与地面/高台一致；
2. :func:`test_route_endpoints_land_on_start_and_end_tiles` —— 路线的起点/终点落在红门/蓝门。
"""

from __future__ import annotations

import asyncio
import json
from pathlib import Path
from typing import Any

import httpx
import pytest

from maa_api.domain.errors import AppError, ErrorCode
from maa_api.services.external.gamedata import (
    GamedataClient,
    validate_excel_name,
    validate_level_id,
)
from maa_api.services.external.http import ExternalClient
from maa_api.services.external.sources import build_url, gamedata_mirror_origins
from maa_api.services.prts_parse.gamedata import (
    is_buildable,
    pack_level_data,
    parse_level_file,
    tile_index,
)
from maa_api.settings import Settings, SourcesSettings

FIXTURES = Path("tests/fixtures/gamedata")

FASTLY = "https://fastly.jsdelivr.net/gh/Kengxxiao/ArknightsGameData@master"
CDN = "https://cdn.jsdelivr.net/gh/Kengxxiao/ArknightsGameData@master"

LEVEL_PATH = "/gh/Kengxxiao/ArknightsGameData@master"
GITHUB_PATH = (
    "/repos/Kengxxiao/ArknightsGameData/contents/"
    "zh_CN/gamedata/levels/obt/main/level_main_01-07.json"
)

LEVEL_ID = "obt/main/level_main_01-07"


def async_test(func):
    """本仓库不装 pytest-asyncio：异步用例统一用 `asyncio.run` 跑。"""

    def wrapper(*args, **kwargs):
        return asyncio.run(func(*args, **kwargs))

    wrapper.__name__ = func.__name__
    return wrapper


def fixture(name: str) -> Any:
    return json.loads((FIXTURES / name).read_text(encoding="utf-8"))


def fixture_bytes(name: str) -> bytes:
    return (FIXTURES / name).read_bytes()


class Routes:
    """按主机（必要时按路径）回夹具或错误响应，并记录每一次请求。"""

    def __init__(self, **hosts: Any) -> None:
        self.hosts = hosts
        self.requests: list[httpx.Request] = []

    def handler(self, request: httpx.Request) -> httpx.Response:
        self.requests.append(request)
        payload = self.hosts.get(request.url.host)
        if callable(payload):
            return payload(request)
        if isinstance(payload, httpx.Response):
            return payload
        if payload is None:
            return httpx.Response(404, json={"message": "Not Found"})
        return httpx.Response(200, content=payload)

    @property
    def hosts_seen(self) -> list[str]:
        return [request.url.host for request in self.requests]


def make_settings(**overrides: Any) -> Settings:
    sources: dict[str, Any] = {
        "use_proxy": False,
        "gamedata_rate_per_minute": 60,
    }
    sources.update(overrides)
    return Settings(sources=SourcesSettings(**sources))


def make_client(
    routes: Routes, *, settings: Settings | None = None
) -> tuple[GamedataClient, Settings]:
    settings = settings or make_settings()
    return (
        GamedataClient(
            ExternalClient(
                transport=httpx.MockTransport(routes.handler),
                settings_provider=lambda: settings,
            ),
            settings_provider=lambda: settings,
        ),
        settings,
    )


def ok_routes() -> Routes:
    return Routes(**{host: fixture_bytes("level_main_01-07.json") for host in (
        "fastly.jsdelivr.net",
        "cdn.jsdelivr.net",
        "api.github.com",
    )})


# --------------------------------------------------------------------------- #
# T1 探针：坐标换算
# --------------------------------------------------------------------------- #


def _copilot_job() -> dict[str, Any]:
    return json.loads(fixture("copilot_22311.json")["data"]["content"])


def test_copilot_deployments_land_on_deployable_tiles() -> None:
    """1-7 作业的全部部署点都落在可部署格子上（T1 第①条的硬断言）。

    判定用关卡文件自己的 ``buildableType``：``NONE`` 谁都不能放，``MELEE`` 是地面位、
    ``RANGED`` 是高台位。作业 22311 是稀音（远程）＋移动摄影器（近战），5 个部署点
    5/5 命中，且地块高度与干员属性一致。
    """
    payload = fixture("level_main_01-07.json")
    job = _copilot_job()
    assert job["stage_name"] == "main_01-07"

    deployments = [row for row in job["actions"] if row["type"] == "Deploy"]
    assert len(deployments) == 5

    names = {row["name"] for row in deployments}
    assert names == {"稀音", "移动摄影器"}  # 远程 + 近战各一路，两种地块都要覆盖到

    tiles = payload["mapData"]["tiles"]
    for row in deployments:
        col, row_index = row["location"]
        index = tile_index(payload, col, row_index)
        assert index is not None, row
        tile = tiles[index]
        assert is_buildable(tile), f"{row['name']} 的 {row['location']} 落在不可部署格"
        if row["name"] == "移动摄影器":
            # 近战（地面）位
            assert is_buildable(tile, ranged=False), row
        else:
            # 远程（高台）位
            assert is_buildable(tile, ranged=True), row


def test_route_endpoints_land_on_start_and_end_tiles() -> None:
    """路线端点换算：起点落红门、终点落蓝门（1-7 的 27 条里 26 条出怪路线）。

    第 0 条路线是 ``preview`` 用的占位（两端都是 ``tile_forbidden``），不算在内。
    """
    payload = fixture("level_main_01-07.json")
    tiles = payload["mapData"]["tiles"]
    checked = 0
    for route in payload["routes"]:
        start = route["startPosition"]
        end = route["endPosition"]
        start_tile = tiles[tile_index(payload, start["col"], start["row"])]
        end_tile = tiles[tile_index(payload, end["col"], end["row"])]
        if start_tile["tileKey"] == "tile_forbidden":
            assert end_tile["tileKey"] == "tile_forbidden"
            continue
        assert start_tile["tileKey"].startswith("tile_start"), route
        assert end_tile["tileKey"].startswith("tile_end"), route
        checked += 1
    assert checked == 26  # 1-7 的 routes 里 26 条是真正的出怪路线


def test_tile_index_uses_top_down_map_rows() -> None:
    """一维下标的口径：``tiles[map[row][col]]``，``map`` 的行序与 ``location`` 的 y 一致。

    1-7 的 ``map`` 是 7×11、``tiles`` 是 77；最上面一行是 66..76（自下而上的行号），
    最下面一行是 0..10。若按 ``col * height + row`` 解释，``[8, 2]`` 会落到
    ``tile_forbidden``，而作业里那一格是高台远程位。
    """
    payload = fixture("level_main_01-07.json")
    rows = payload["mapData"]["map"]
    assert len(rows) == 7 and len(rows[0]) == 11
    assert rows[0] == list(range(66, 77))
    assert rows[6] == list(range(0, 11))
    assert tile_index(payload, 8, 2) == rows[2][8]
    assert payload["mapData"]["tiles"][tile_index(payload, 8, 2)]["tileKey"] == "tile_wall"
    assert tile_index(payload, 0, 0) == rows[0][0]
    # 越界返回 None，不抛异常。
    assert tile_index(payload, 11, 0) is None
    assert tile_index(payload, 0, 7) is None


def test_parser_map_matches_send_stage_map_encoding() -> None:
    """解析出的网格与 ``send_stage_map`` 同一套编码（3 字符码，逐字符可对）。"""
    from maa_api.services.prts_parse.gamedata import ENCODING

    level = parse_level_file(fixture("level_main_01-07.json"))
    assert level.map is not None
    assert (level.map.width, level.map.height) == (11, 7)
    flat = [code for row in level.map.grid for code in row]
    assert len(flat) == 77
    assert all(len(code) == 3 for code in flat)
    # 地形字符取自 stage_layout 的同一张表；可部署/高度字符来自 buildableType/heightType。
    assert set(flat) <= {
        "#00", "#01", ".00", ".01", "!00", "!01", "~00", "~01", "w00", "w01",
        "#10", "#11", ".10", ".11", "!10", "!11", "~10", "~11", "w10", "w11",
        "#20", "#21", ".20", ".21", "!20", "!21", "~20", "~21", "w20", "w21",
        "#30", "#31", ".30", ".31", "!30", "!31", "~30", "~31", "w30", "w31",
    }
    assert "与 send_stage_map 的 tiles 同一套编码" in ENCODING
    assert level.map.grid[0][0] == "#01"  # 左上角是阻挡高台
    assert level.map.grid[1][10] == ".10" or level.map.grid[1][10].startswith(".")
    assert len(level.map.buildable) == 39
    assert level.map.start == ((10, 1), (10, 3), (10, 5))
    assert level.map.end == ((0, 3),)
    assert level.options.deploy_limit == 8
    assert level.options.life_points == 10
    assert level.options.initial_cost == 10
    assert level.options.max_cost == 99
    assert level.options.cost_recovery == pytest.approx(1.0)


def test_wave_times_accumulate_fragment_pre_delay() -> None:
    """波次时刻 = wave.preDelay + Σ片段 preDelay + action.preDelay（秒）。"""
    level = parse_level_file(fixture("level_main_01-07.json"))
    assert len(level.waves) == 24  # 27 个动作里有 2 个 STORY 与 1 个 PREVIEW_CURSOR
    first = level.waves[0]
    assert (first.time, first.enemy_key, first.count, first.route) == (
        6.0,
        "enemy_1007_slime_2",
        1,
        1,
    )
    assert all(wave.time >= 0 for wave in level.waves)
    assert [wave.time for wave in level.waves] == sorted(wave.time for wave in level.waves)
    assert level.enemy_names == {}  # 关卡文件只带 key，不带名字


def test_summary_waves_merge_and_keep_totals() -> None:
    """``summary`` 合并同波、同敌人、同路线、同数量的相邻动作，总数不丢。"""
    payload = fixture("level_main_01-07.json")
    level = parse_level_file(payload)
    summary = pack_level_data(level, wave_detail="summary")
    full = pack_level_data(level, wave_detail="full")
    assert summary["waves_detail"] == "summary"
    assert full["waves_detail"] == "full"
    assert sum(row["count"] for row in summary["waves"]) == sum(
        row["count"] for row in full["waves"]
    )
    assert len(summary["waves"]) <= len(full["waves"])
    # 合成一份可合并的输入：同波同敌人同路线同数量的两条相邻动作应合成一条。
    wave = level.waves[0]
    merged = pack_level_data(
        type(level)(
            options=level.options,
            map=level.map,
            routes=level.routes,
            waves=(wave, type(wave)(
                wave=wave.wave,
                fragment=wave.fragment,
                time=wave.time + 1,
                enemy_key=wave.enemy_key,
                enemy_name=wave.enemy_name,
                count=wave.count,
                interval=wave.interval,
                route=wave.route,
            )),
        ),
        wave_detail="summary",
    )
    assert len(merged["waves"]) == 1
    assert merged["waves"][0]["count"] == wave.count * 2
    assert merged["waves"][0]["time"] == wave.time


def test_pack_truncates_routes_and_reports_it() -> None:
    """体积：先合并波次、再截断路线，截断写进 ``truncated``（规格 §9.3）。"""
    level = parse_level_file(fixture("level_main_01-07.json"))
    packed = pack_level_data(level)
    assert packed["bytes"] <= 24 * 1024
    assert len(packed["waves"]) == 24
    assert len(packed["routes"]) == 12  # 24 条被用到的路线只留前 12 条
    assert packed["truncated"]["routes_extra"] == 12
    assert packed["routes"][0]["index"] == 1
    first = packed["routes"][0]
    assert first["start"] == [10, 5]
    assert first["end"] == [0, 3]
    assert first["waypoints"], first
    assert packed["map"]["start"] == [[10, 1], [10, 3], [10, 5]]


def test_pack_stays_under_budget_for_a_much_larger_level() -> None:
    """构造 400 条出怪、30 条路线、每条 120 个途经点的关卡：仍然 ≤ 24 KB 且截断如实。

    三档退让都会走到：波次截到 40（summary）/ 80（full）、途经点收到 8、路线留前 12。
    """
    from maa_api.services.prts_parse.gamedata import (
        LevelFile,
        LevelMap,
        LevelOptions,
        LevelRoute,
        LevelWave,
    )

    routes = tuple(
        LevelRoute(
            index=index,
            start=(23, 7),
            end=(0, 7),
            waypoints=tuple(
                {"x": step % 24, "y": step % 14, "type": "move"} for step in range(120)
            ),
        )
        for index in range(30)
    )
    waves = tuple(
        LevelWave(
            wave=index // 20,
            fragment=index % 20,
            time=float(index),
            enemy_key=f"enemy_{index % 7}",
            enemy_name=None,
            count=2,
            interval=1.0,
            route=index % 30,
        )
        for index in range(400)
    )
    level = LevelFile(
        options=LevelOptions(8, 10, 10, 99, 1.0),
        map=LevelMap(
            width=24,
            height=14,
            grid=tuple(tuple(".10" for _ in range(24)) for _ in range(14)),
            start=((23, 7),),
            end=((0, 7),),
            buildable=(),
        ),
        routes=routes,
        waves=waves,
    )
    for detail, limit in (("summary", 40), ("full", 80)):
        packed = pack_level_data(level, wave_detail=detail)
        assert packed["bytes"] <= 24 * 1024, detail
        assert len(packed["waves"]) == limit
        assert packed["truncated"]["waves"]["total"] == 400
        assert len(packed["routes"]) == 12
        assert packed["truncated"]["routes_extra"] == 18
        assert packed["truncated"]["waypoints"]["kept_per_route"] == 8
        assert all(len(row["waypoints"]) <= 8 for row in packed["routes"])


def test_pack_trims_waypoints_when_over_budget() -> None:
    """超 24 KB 时按 8 → 4 → 2 收紧途经点，并记进 ``truncated.waypoints``。"""
    level = parse_level_file(fixture("level_main_01-07.json"))
    routes = tuple(
        type(route)(
            index=route.index,
            start=route.start,
            end=route.end,
            waypoints=tuple(
                {"x": index, "y": index % 7, "type": "move"} for index in range(120)
            ),
            wait_seconds=0.0,
        )
        for route in level.routes
    )
    heavy = type(level)(options=level.options, map=level.map, routes=routes, waves=level.waves)
    packed = pack_level_data(heavy)
    assert packed["bytes"] <= 24 * 1024
    assert packed["truncated"]["waypoints"]["kept_per_route"] == 8
    assert all(len(row["waypoints"]) <= 8 for row in packed["routes"])
    # 途经点够用，波次一条不丢（退让顺序：先截断路线，不动时间线）。
    assert len(packed["waves"]) == len(level.waves)


def test_pack_include_subset() -> None:
    """``include`` 只给要的字段组：只给 waves 时不动地图与路线。"""
    level = parse_level_file(fixture("level_main_01-07.json"))
    assert set(pack_level_data(level, include=["waves"])) >= {"waves"}
    only_waves = pack_level_data(level, include=["waves"])
    assert "map" not in only_waves and "routes" not in only_waves and "options" not in only_waves
    only_map = pack_level_data(level, include=["map"])
    assert "map" in only_map and "waves" not in only_map
    assert only_map["bytes"] < only_waves["bytes"] + 20_000


def test_parse_rejects_broken_payload() -> None:
    """结构不符抛 ``EXTERNAL_RESPONSE_INVALID``，不假装解析出空图。"""
    for payload in (
        [],
        {},
        {"mapData": {}},
        {"mapData": {"map": [[0]], "tiles": "nope"}},
        {"mapData": {"map": [[5]], "tiles": [{"tileKey": "tile_road"}]}},
    ):
        with pytest.raises(AppError) as info:
            parse_level_file(payload)
        assert info.value.code is ErrorCode.EXTERNAL_RESPONSE_INVALID


# --------------------------------------------------------------------------- #
# 客户端：请求形状、镜像顺序、兜底、缓存
# --------------------------------------------------------------------------- #


def test_default_mirrors_and_github_params_are_registered() -> None:
    """附录 D：镜像前缀两个，GitHub 兜底带 ``ref=master`` 与 raw 的 Accept。"""
    assert gamedata_mirror_origins(SourcesSettings()) == (FASTLY, CDN)
    assert build_url("gamedata_github", "contents", {"path": "a/b.json", "ref": "master"}) == (
        "https://api.github.com/repos/Kengxxiao/ArknightsGameData/contents/a/b.json?ref=master"
    )


def test_mirror_origins_skip_unknown_hosts_and_fall_back() -> None:
    """镜像只接受登记过的主机；全被跳过时回落到默认两个（配置写错≠数据不可用）。"""
    configured = SourcesSettings(
        gamedata_mirrors=[
            "https://evil.example.com/gh/x@master",
            "https://fastly.jsdelivr.net/gh/Other/Repo@main/",
            "not-a-url",
        ]
    )
    assert gamedata_mirror_origins(configured) == (
        "https://fastly.jsdelivr.net/gh/Other/Repo@main",
    )
    assert gamedata_mirror_origins(SourcesSettings(gamedata_mirrors=[])) == (FASTLY, CDN)


@async_test
async def test_level_request_shape_and_cache_hit() -> None:
    """请求形状：镜像主机 + 固定路径前缀，``level`` 档缓存命中后不再发请求。"""
    routes = ok_routes()
    client, _ = make_client(routes)
    first = await client.level(LEVEL_ID)
    assert first.via == FASTLY
    assert first.cached is False
    request = routes.requests[0]
    assert request.url.host == "fastly.jsdelivr.net"
    # 路径只能走路径：实测 `…@master?path=…` 被 jsDelivr 拒（HTTP 400），所以镜像请求
    # 不带查询串，关卡路径拼在前缀后面。
    assert request.url.path == (
        f"{LEVEL_PATH}/zh_CN/gamedata/levels/obt/main/level_main_01-07.json"
    )
    assert dict(request.url.params) == {}
    assert request.headers["user-agent"].startswith("MAA-API/")
    assert len(routes.requests) == 1

    second = await client.level(LEVEL_ID)
    assert second.cached is True
    assert len(routes.requests) == 1  # 缓存命中，没有第二次请求


@async_test
async def test_level_falls_back_to_second_mirror() -> None:
    """第一个镜像 5xx 时改试第二个；两者内容一致，取到就用。"""
    payload = fixture_bytes("level_main_01-07.json")
    routes = Routes(**{
        # 5xx 是访问层会重试一次的状态码：第一条镜像的两次尝试都要失败才会改试第二条。
        "fastly.jsdelivr.net": lambda request: httpx.Response(503, text="busy"),
        "cdn.jsdelivr.net": payload,
    })
    client, _ = make_client(routes)
    data = await client.level(LEVEL_ID)
    assert data.via == CDN
    assert routes.hosts_seen == [
        "fastly.jsdelivr.net",
        "fastly.jsdelivr.net",
        "cdn.jsdelivr.net",
    ]


@async_test
async def test_level_uses_github_fallback_with_raw_accept() -> None:
    """两个镜像都失败且开关为真时走 GitHub API（``Accept: application/vnd.github.raw+json``）。"""
    routes = Routes(**{
        # 唯一配的镜像 404（仓库路径写错），GitHub 兜底给内容。
        "fastly.jsdelivr.net": lambda request: httpx.Response(404, json={"message": "no"}),
        "api.github.com": fixture_bytes("level_main_01-07.json"),
    })
    client, _ = make_client(
        routes,
        settings=make_settings(
            gamedata_mirrors=["https://fastly.jsdelivr.net/gh/nope@master"]
        ),
    )
    data = await client.level(LEVEL_ID)
    assert data.via == "github"
    assert routes.hosts_seen == ["fastly.jsdelivr.net", "api.github.com"]
    assert routes.requests[0].url.path.startswith("/gh/nope@master/")
    github = routes.requests[-1]
    assert github.url.host == "api.github.com"
    assert github.url.path == GITHUB_PATH
    assert dict(github.url.params) == {"ref": "master"}


@async_test
async def test_github_fallback_can_be_disabled() -> None:
    """兜底开关关掉时，镜像失败就直接报错，不去打 GitHub。"""
    routes = Routes(**{"fastly.jsdelivr.net": lambda request: httpx.Response(503, text="busy")})
    client, _ = make_client(
        routes, settings=make_settings(gamedata_github_api_fallback=False)
    )
    with pytest.raises(AppError) as info:
        await client.level(LEVEL_ID)
    assert info.value.code is ErrorCode.EXTERNAL_SOURCE_UNAVAILABLE
    assert "api.github.com" not in routes.hosts_seen


@async_test
async def test_all_candidates_404_is_game_data_not_found() -> None:
    """所有候选都 404 才算「来源上没有这一关」。"""
    routes = Routes()  # 默认全 404
    client, _ = make_client(routes)
    with pytest.raises(AppError) as info:
        await client.level("obt/main/level_nope")
    assert info.value.code is ErrorCode.GAME_DATA_NOT_FOUND
    # 两个镜像 + GitHub 兜底各试一次（404 不在访问层的重试状态码里）。
    assert routes.hosts_seen == [
        "fastly.jsdelivr.net",
        "cdn.jsdelivr.net",
        "api.github.com",
    ]


@async_test
async def test_mixed_failure_is_not_reported_as_not_found() -> None:
    """镜像 5xx + 一个 404：不能把「取不到」说成「不存在」（§3.5）。"""
    payload = fixture_bytes("level_main_01-07.json")
    routes = Routes(**{
        "fastly.jsdelivr.net": lambda request: httpx.Response(500, text="boom"),
        "cdn.jsdelivr.net": lambda request: httpx.Response(404, json={"message": "no"}),
        "api.github.com": lambda request: httpx.Response(500, text="boom"),
    })
    client, _ = make_client(routes)
    with pytest.raises(AppError) as info:
        await client.level(LEVEL_ID)
    assert info.value.code is ErrorCode.EXTERNAL_SOURCE_UNAVAILABLE
    assert payload  # 夹具确实存在（这条用例只用错误响应）


@async_test
async def test_source_disabled_does_not_degrade_to_not_found() -> None:
    """``sources.gamedata_enabled=false`` 报 ``EXTERNAL_SOURCE_DISABLED``，且不发请求。"""
    routes = ok_routes()
    client, _ = make_client(routes, settings=make_settings(gamedata_enabled=False))
    with pytest.raises(AppError) as info:
        await client.level(LEVEL_ID)
    assert info.value.code is ErrorCode.EXTERNAL_SOURCE_DISABLED
    assert routes.requests == []


@async_test
async def test_excel_reads_whitelisted_tables_only() -> None:
    """``excel(name)`` 只取白名单里的两张常量表。"""
    const = fixture_bytes("gamedata_const.json")
    routes = Routes(**{host: const for host in ("fastly.jsdelivr.net", "cdn.jsdelivr.net")})
    client, _ = make_client(routes)
    data = await client.excel("gamedata_const.json")
    assert data.via == FASTLY
    assert "characterExpMap" in data.data
    assert routes.requests[0].url.path.endswith("/zh_CN/gamedata/excel/gamedata_const.json")
    with pytest.raises(AppError) as info:
        await client.excel("character_table.json")
    assert info.value.code is ErrorCode.INVALID_PARAMETER


def test_level_id_whitelist_rejects_before_building_url() -> None:
    """``levelId`` 形状不对报 ``INVALID_PARAMETER``，且不拼 URL（不发请求）。"""
    assert validate_level_id("OBT/Main/Level_Main_01-07") == LEVEL_ID
    assert validate_level_id("activities/act3break/level_act3break_01") == (
        "activities/act3break/level_act3break_01"
    )
    for bad in (
        "",
        "main_01-07",
        "obt",
        "obt/main/../../etc/passwd",
        "obt//main/x",
        "obt/main/x/",
        "obt/main/x?y",
        "obt/main/x y",
        "obt/main/x\\y",
        "obt/main/x#f",
        "http://evil.example.com/x",
    ):
        with pytest.raises(AppError) as info:
            validate_level_id(bad)
        assert info.value.code is ErrorCode.INVALID_PARAMETER, bad


@async_test
async def test_invalid_level_id_sends_no_request() -> None:
    routes = ok_routes()
    client, _ = make_client(routes)
    with pytest.raises(AppError) as info:
        await client.level("obt/main/../../etc/passwd")
    assert info.value.code is ErrorCode.INVALID_PARAMETER
    assert routes.requests == []


def test_excel_name_whitelist() -> None:
    assert validate_excel_name("item_table.json") == "item_table.json"
    for bad in ("", "character_table.json", "../gamedata_const.json", "GAMEDATA_CONST.JSON"):
        with pytest.raises(AppError) as info:
            validate_excel_name(bad)
        assert info.value.code is ErrorCode.INVALID_PARAMETER, bad


@async_test
async def test_source_ref_points_at_github_repo() -> None:
    """来源信封：链接只指向白名单主机（``github.com``）。"""
    routes = ok_routes()
    client, _ = make_client(routes)
    data = await client.level(LEVEL_ID)
    ref = data.source_ref(title=LEVEL_ID)
    assert ref.kind == "gamedata"
    assert ref.title == LEVEL_ID
    assert ref.url == "https://github.com/Kengxxiao/ArknightsGameData"
    assert ref.fetched_at is not None
