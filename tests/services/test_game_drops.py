"""掉率领域层（规格 §6.5、§6.6）：筛选、排序、样本阈值、变体对回与降级。

全部用 `tests/fixtures/penguin/` 夹具与 `httpx.MockTransport`，不打网。
"""

from __future__ import annotations

import asyncio
import json
from datetime import datetime
from pathlib import Path
from typing import Any

import httpx
import pytest

from maa_api.domain.errors import AppError, ErrorCode
from maa_api.services.external.http import ExternalClient
from maa_api.services.external.penguin import PenguinClient
from maa_api.services.game.drops import DropRow, DropService
from maa_api.settings import ChannelSettings, Settings, SourcesSettings

FIXTURES = Path("tests/fixtures/penguin")

#: 与登记表一致：四个端点都在 `/PenguinStats/api/v2` 前缀下（2026-10-10 实测）。
MATRIX_PATH = "/PenguinStats/api/v2/result/matrix"
STAGES_PATH = "/PenguinStats/api/v2/stages"
ITEMS_PATH = "/PenguinStats/api/v2/items"

ROW_KEYS = {
    "stage_id",
    "stage_code",
    "item_id",
    "item_name",
    "times",
    "quantity",
    "rate",
    "ap_cost",
    "sanity_per_item",
    "small_sample",
    "started_at",
    "ended_at",
    "open",
}

# 夹具里的两行，用来断言统计区间与开放状态。
SMALL_SAMPLE_ROW = ("act13d0_05", 30012, 92, 1, 1600934400000, 1601496000000, "FA-5", 12)


def async_test(func):
    """本仓库不装 pytest-asyncio：异步用例统一用 `asyncio.run` 跑。"""

    def wrapper(*args, **kwargs):
        return asyncio.run(func(*args, **kwargs))

    wrapper.__name__ = func.__name__
    return wrapper


def fixture(name: str) -> Any:
    return json.loads((FIXTURES / name).read_text(encoding="utf-8"))


def local_date(milliseconds: int) -> str:
    """按本机时区把企鹅物流的毫秒时间戳转成 `YYYY-MM-DD`。"""
    return datetime.fromtimestamp(milliseconds / 1000).astimezone().strftime("%Y-%m-%d")


async def _no_sleep(seconds: float) -> None:
    return None


def make_settings(*, server: str = "CN", **overrides: Any) -> Settings:
    sources: dict[str, Any] = {"use_proxy": False, "penguin_rate_per_minute": 60}
    sources.update(overrides)
    return Settings(sources=SourcesSettings(**sources), channel=ChannelSettings(server=server))


class Routes:
    """按路径回夹具或错误响应，并记录每一次请求。"""

    def __init__(self, **routes: Any) -> None:
        self.routes = routes
        self.requests: list[httpx.Request] = []

    def handler(self, request: httpx.Request) -> httpx.Response:
        self.requests.append(request)
        payload = self.routes.get(request.url.path)
        if callable(payload):
            return payload(request)
        if isinstance(payload, httpx.Response):
            return payload
        if payload is None:
            return httpx.Response(404, content=b'{"message": "not found"}')
        return httpx.Response(200, content=json.dumps(payload).encode("utf-8"))

    def paths(self) -> list[str]:
        return [request.url.path for request in self.requests]


def response(payload: Any) -> httpx.Response:
    return httpx.Response(200, content=json.dumps(payload).encode("utf-8"))


def matrix_route(request: httpx.Request) -> httpx.Response:
    """默认 matrix 路由：`show_closed_zones=true` 时回含已关闭关卡的夹具。"""
    closed = request.url.params.get("show_closed_zones") == "true"
    return response(
        fixture("matrix_closed_subset.json" if closed else "matrix_subset.json")
    )


def routes(
    *,
    matrix: Any = None,
    stages: Any = None,
    items: Any = None,
) -> Routes:
    return Routes(
        **{
            MATRIX_PATH: matrix_route if matrix is None else matrix,
            STAGES_PATH: fixture("stages_subset.json") if stages is None else stages,
            ITEMS_PATH: fixture("items_subset.json") if items is None else items,
        }
    )


def make_service(routes_: Routes, *, settings: Settings | None = None) -> DropService:
    settings = settings if settings is not None else make_settings()
    external = ExternalClient(
        transport=httpx.MockTransport(routes_.handler),
        settings_provider=lambda: settings,
        sleep=_no_sleep,
    )
    penguin = PenguinClient(external, settings_provider=lambda: settings)
    return DropService(penguin=penguin, settings_provider=lambda: settings)


def base_stage(stage_id: str = "a001_01", *, code: str = "GT-1", ap_cost: int = 9) -> dict[str, Any]:
    """构造一条基础 id 的 `/stages` 记录（夹具里只留了变体）。"""
    return {
        "stageId": stage_id,
        "zoneId": "permanent_sidestory_1_zone1",
        "stageType": "ACTIVITY",
        "code": code,
        "apCost": ap_cost,
        "existence": {"CN": {"exist": True}},
    }


def matrix_row(
    stage_id: str,
    item_id: str,
    times: int,
    quantity: int,
    *,
    start: int = 1604217600000,
    end: int | None = None,
) -> dict[str, Any]:
    return {
        "stageId": stage_id,
        "itemId": item_id,
        "times": times,
        "quantity": quantity,
        "stdDev": 0.1,
        "start": start,
        "end": end,
    }


# ---------------------------------------------------------------------------
# 返回形状与来源信封
# ---------------------------------------------------------------------------
@async_test
async def test_item_rates_by_name_returns_rows_and_envelope() -> None:
    service = make_service(routes())

    result = await service.item_rates("固源岩组")

    assert result["query"] == "固源岩组"
    assert result["item"] == {
        "item_id": "30013",
        "name": "固源岩组",
        "rarity": 2,
        "item_type": "MATERIAL",
    }
    assert result["rows_total"] == 40
    assert len(result["rows"]) == 8  # 默认 limit
    assert result["include_closed"] is False
    assert result["server"] == "CN"
    assert result["untrusted"] is True
    assert result["unavailable"] == []
    assert result["region_note"] is None
    assert set(result["rows"][0]) == ROW_KEYS

    sanity = [row["sanity_per_item"] for row in result["rows"]]
    assert sanity == sorted(sanity)
    assert all(not row["small_sample"] for row in result["rows"])
    assert result["rows"][0]["stage_code"] == "10-6"
    assert result["rows"][0]["stage_id"] in {"main_10-05", "tough_10-05"}


@async_test
async def test_envelope_carries_the_penguin_license() -> None:
    service = make_service(routes())

    result = await service.item_rates("固源岩组")

    assert len(result["sources"]) == 1
    source = result["sources"][0]
    assert source["kind"] == "penguin"
    assert source["label"] == "企鹅物流"
    assert "CC BY-NC 4.0" in source["title"]
    assert source["url"] == "https://penguin-stats.io/"
    assert source["cached"] is False
    assert source["stale"] is False
    assert source["fetched_at"]


@async_test
async def test_item_rates_by_item_id_and_limit() -> None:
    service = make_service(routes())

    result = await service.item_rates("30013", limit=3)

    assert result["item"]["item_id"] == "30013"
    assert result["rows_total"] == 40
    assert len(result["rows"]) == 3


@async_test
async def test_item_rates_ignores_whitespace_in_names() -> None:
    service = make_service(routes())

    result = await service.item_rates(" 固源岩 组 ")

    assert result["item"]["item_id"] == "30013"


@async_test
async def test_item_rates_for_an_item_without_open_rows_is_empty() -> None:
    service = make_service(routes())

    result = await service.item_rates("30012")

    assert result["item"]["item_id"] == "30012"
    assert result["rows"] == []
    assert result["rows_total"] == 0


@async_test
async def test_include_closed_is_forwarded_and_reported() -> None:
    routes_ = routes()
    service = make_service(routes_)

    result = await service.item_rates("30012", include_closed=True)

    assert result["include_closed"] is True
    assert result["rows_total"] == 7

    default = await service.item_rates("30012")
    assert default["rows_total"] == 0

    matrix_requests = [
        request for request in routes_.requests if request.url.path == MATRIX_PATH
    ]
    assert dict(matrix_requests[0].url.params) == {
        "server": "CN",
        "show_closed_zones": "true",
    }
    assert dict(matrix_requests[1].url.params) == {"server": "CN"}


@async_test
async def test_server_follows_the_channel_setting_and_can_be_overridden() -> None:
    routes_ = routes()
    service = make_service(routes_, settings=make_settings(server="JP"))

    result = await service.item_rates("30013")
    assert result["server"] == "JP"
    overridden = await service.item_rates("30013", server="KR")
    assert overridden["server"] == "KR"
    matrix_requests = [
        request for request in routes_.requests if request.url.path == MATRIX_PATH
    ]
    assert dict(matrix_requests[0].url.params) == {"server": "JP"}
    assert dict(matrix_requests[1].url.params) == {"server": "KR"}


@async_test
async def test_invalid_limit_is_rejected() -> None:
    service = make_service(routes())

    for limit in (0, 21):
        with pytest.raises(AppError) as info:
            await service.item_rates("30013", limit=limit)
        assert info.value.code is ErrorCode.INVALID_PARAMETER


# ---------------------------------------------------------------------------
# 行的计算（样本阈值、除零保护、统计区间、开放状态）
# ---------------------------------------------------------------------------
@async_test
async def test_small_sample_row_fields_and_ordering() -> None:
    service = make_service(routes())

    result = await service.item_rates("30012", include_closed=True)

    assert result["rows_total"] == 7
    assert [row["small_sample"] for row in result["rows"]] == [False] * 6 + [True]
    row = result["rows"][-1]
    stage_id, item_id, times, quantity, start, end, code, ap_cost = SMALL_SAMPLE_ROW
    assert row["stage_id"] == stage_id
    assert row["stage_code"] == code
    assert row["item_id"] == str(item_id)
    assert row["item_name"] == "固源岩"
    assert row["times"] == times
    assert row["quantity"] == quantity
    assert row["rate"] == pytest.approx(quantity / times)
    assert row["ap_cost"] == ap_cost
    assert row["sanity_per_item"] == pytest.approx(ap_cost / (quantity / times))
    assert row["small_sample"] is True
    assert row["open"] is False
    assert row["started_at"] == local_date(start)
    assert row["ended_at"] == local_date(end)

    sufficient = result["rows"][:-1]
    sanity = [item["sanity_per_item"] for item in sufficient]
    assert sanity == sorted(sanity)


@async_test
async def test_open_rows_have_no_end_date_and_are_reported_open() -> None:
    matrix = {"matrix": [matrix_row("main_08-13", "30013", 4042328, 107229)]}
    service = make_service(routes(matrix=matrix))

    result = await service.item_rates("30013")

    row = result["rows"][0]
    assert row["stage_id"] == "main_08-13"
    assert row["ended_at"] is None
    assert row["open"] is True
    assert row["started_at"] == local_date(1604217600000)


@async_test
async def test_zero_times_rows_are_dropped() -> None:
    payload = fixture("matrix_subset.json")
    payload["matrix"] = [
        *payload["matrix"],
        matrix_row("main_08-13", "30013", 0, 0),
        matrix_row("main_10-05", "30013", 0, 7),
    ]
    service = make_service(routes(matrix=payload))

    result = await service.item_rates("30013")

    assert result["rows_total"] == 40
    assert all(row["times"] > 0 for row in result["rows"])


@async_test
async def test_zero_quantity_row_has_no_sanity_estimate_and_sorts_last() -> None:
    matrix = {
        "matrix": [
            matrix_row("main_08-13", "30013", 1000, 300),
            matrix_row("main_10-05", "30013", 500, 0),
        ]
    }
    service = make_service(routes(matrix=matrix))

    result = await service.item_rates("30013")

    assert result["rows_total"] == 2
    row = next(item for item in result["rows"] if item["times"] == 500)
    assert row["rate"] == 0.0
    assert row["sanity_per_item"] is None
    assert row["small_sample"] is False
    # 有理智估计的行都排在它前面。
    assert result["rows"].index(row) == len(result["rows"]) - 1


def test_drop_row_as_dict_rounds_nothing() -> None:
    row = DropRow(
        stage_id="main_08-13",
        stage_code="R8-11",
        item_id="30013",
        item_name="固源岩组",
        times=4,
        quantity=1,
        rate=0.25,
        ap_cost=21,
        sanity_per_item=84.0,
        small_sample=True,
        started_at="2026-01-01",
        ended_at=None,
        open=True,
    )

    payload = row.as_dict()

    assert set(payload) == ROW_KEYS
    assert payload["rate"] == 0.25
    assert payload["sanity_per_item"] == 84.0
    assert payload["ended_at"] is None
    assert payload["open"] is True


# ---------------------------------------------------------------------------
# 名字解析（itemId → 名称 → 唯一子串）
# ---------------------------------------------------------------------------
@async_test
async def test_unknown_item_is_not_found() -> None:
    service = make_service(routes())

    with pytest.raises(AppError) as info:
        await service.item_rates("不存在的东西")

    assert info.value.code is ErrorCode.GAME_DATA_NOT_FOUND
    assert info.value.details["query"] == "不存在的东西"


@async_test
async def test_ambiguous_item_name_lists_the_candidates() -> None:
    service = make_service(routes())

    # 「作战记录」不是完整名称，同时是 基础作战记录 与 初级作战记录 的子串。
    with pytest.raises(AppError) as info:
        await service.item_rates("作战记录")

    assert info.value.code is ErrorCode.INVALID_PARAMETER
    candidates = info.value.details["candidates"]
    assert {candidate["item_id"] for candidate in candidates} == {"2001", "2002"}
    assert {candidate["name"] for candidate in candidates} == {"基础作战记录", "初级作战记录"}


@async_test
async def test_stage_rates_resolves_code_and_reports_the_stage() -> None:
    service = make_service(routes())

    result = await service.stage_rates("R8-11")

    assert result["query"] == "R8-11"
    assert result["stage"] == {
        "stage_id": "main_08-13",
        "code": "R8-11",
        "ap_cost": 21,
        "open": True,
        "zone_id": "main_8",
        "stage_type": "MAIN",
    }
    assert result["rows_total"] == 1
    row = result["rows"][0]
    assert row["item_id"] == "30013"
    assert row["item_name"] == "固源岩组"
    assert row["rate"] == pytest.approx(107229 / 4042328)
    assert row["sanity_per_item"] == pytest.approx(21 / (107229 / 4042328))

    by_id = await service.stage_rates("main_08-13")
    assert by_id["stage"]["stage_id"] == "main_08-13"
    assert by_id["rows"] == result["rows"]


@async_test
async def test_unknown_stage_is_not_found() -> None:
    service = make_service(routes())

    with pytest.raises(AppError) as info:
        await service.stage_rates("ZZ-99")

    assert info.value.code is ErrorCode.GAME_DATA_NOT_FOUND
    assert info.value.details["query"] == "ZZ-99"


@async_test
async def test_ambiguous_stage_code_lists_the_candidates() -> None:
    service = make_service(routes())

    with pytest.raises(AppError) as info:
        await service.stage_rates("10-6")

    assert info.value.code is ErrorCode.INVALID_PARAMETER
    candidates = info.value.details["candidates"]
    assert {candidate["stage_id"] for candidate in candidates} == {
        "main_10-05",
        "tough_10-05",
    }


@async_test
async def test_ambiguous_stage_substring_lists_the_candidates() -> None:
    service = make_service(routes())

    with pytest.raises(AppError) as info:
        await service.stage_rates("1-1")

    assert info.value.code is ErrorCode.INVALID_PARAMETER
    assert len(info.value.details["candidates"]) > 1


# ---------------------------------------------------------------------------
# `_perm` / `_rep` 对回基础 id（实测：变体与基础 id 同时在 /stages 里，code 相同）
# ---------------------------------------------------------------------------
@async_test
async def test_perm_variant_maps_back_to_the_base_stage_id() -> None:
    stages = [*fixture("stages_subset.json"), base_stage()]
    matrix = {
        "matrix": [
            *fixture("matrix_closed_subset.json")["matrix"],
            # 基础 id 自己也有一行；同一材料只留样本更多的那次统计。
            matrix_row("a001_01", "30012", 1000, 500),
        ]
    }
    service = make_service(routes(matrix=matrix, stages=stages))

    result = await service.stage_rates("GT-1", include_closed=True, limit=10)

    assert result["stage"]["stage_id"] == "a001_01"
    assert result["stage"]["code"] == "GT-1"
    assert result["stage"]["ap_cost"] == 9
    rows = [row for row in result["rows"] if row["item_id"] == "30012"]
    assert len(rows) == 1
    assert rows[0]["stage_id"] == "a001_01"
    assert rows[0]["times"] == 2823  # a001_01_perm 的样本更多
    assert rows[0]["stage_code"] == "GT-1"
    assert rows[0]["ap_cost"] == 9


@async_test
async def test_perm_variant_keeps_its_raw_id_when_the_base_is_absent() -> None:
    service = make_service(routes(matrix=fixture("matrix_closed_subset.json")))

    result = await service.stage_rates("a001_01_perm", include_closed=True, limit=10)

    assert result["stage"]["stage_id"] == "a001_01_perm"
    assert result["stage"]["code"] == "GT-1"
    assert result["rows"][0]["stage_id"] == "a001_01_perm"


@async_test
async def test_shared_code_prefers_the_perm_variant_over_the_rep() -> None:
    service = make_service(routes(matrix=fixture("matrix_closed_subset.json")))

    result = await service.stage_rates("GT-1", include_closed=True, limit=10)

    # 基础 id 不在夹具的 /stages 里，退到常驻化的 _perm。
    assert result["stage"]["stage_id"] == "a001_01_perm"
    assert result["rows_total"] == 1
    assert result["rows"][0]["stage_id"] == "a001_01_perm"


# ---------------------------------------------------------------------------
# 供 get_item / get_stage 用的两个便捷方法
# ---------------------------------------------------------------------------
@async_test
async def test_top_rates_excludes_small_samples_and_sorts_by_sanity() -> None:
    # 这份夹具当默认变体用，材料 30012 有 6 条足够样本 + 1 条小样本。
    matrix = fixture("matrix_closed_subset.json")
    service = make_service(routes(matrix=matrix))

    rows = await service.top_rates("固源岩", limit=10)

    assert len(rows) == 6
    assert all(not row["small_sample"] for row in rows)
    assert "act13d0_05" not in {row["stage_id"] for row in rows}
    sanity = [row["sanity_per_item"] for row in rows]
    assert sanity == sorted(sanity)
    assert rows[0]["stage_id"] == "a001_01_perm"


@async_test
async def test_top_rates_defaults_to_five_rows() -> None:
    service = make_service(routes())

    rows = await service.top_rates("固源岩组")

    assert len(rows) == 5
    assert all(not row["small_sample"] for row in rows)


@async_test
async def test_top_rates_returns_empty_for_an_item_penguin_does_not_track() -> None:
    """`get_item` 已经用 PRTS 定位了材料，企鹅物流没有它时不该让整个工具失败。"""
    service = make_service(routes())

    assert await service.top_rates("不存在的材料") == []


@async_test
async def test_stage_drop_rates_returns_rows_for_one_stage() -> None:
    service = make_service(routes())

    rows = await service.stage_drop_rates("main_08-13")

    assert len(rows) == 1
    assert rows[0]["stage_id"] == "main_08-13"
    assert rows[0]["item_id"] == "30013"
    assert rows[0]["item_name"] == "固源岩组"


@async_test
async def test_stage_drop_rates_keeps_small_samples_flagged() -> None:
    matrix = {
        "matrix": [
            row
            for row in fixture("matrix_closed_subset.json")["matrix"]
            if row["stageId"] == "act13d0_05"
        ]
    }
    service = make_service(routes(matrix=matrix))

    rows = await service.stage_drop_rates("act13d0_05")

    assert len(rows) == 1
    assert rows[0]["small_sample"] is True
    assert rows[0]["open"] is False


@async_test
async def test_stage_drop_rates_for_an_unknown_stage_is_not_found() -> None:
    service = make_service(routes())

    with pytest.raises(AppError) as info:
        await service.stage_drop_rates("nope_01")

    assert info.value.code is ErrorCode.GAME_DATA_NOT_FOUND


# ---------------------------------------------------------------------------
# 降级（规格 §3.5：掉率无兜底，字段组缺失进 unavailable）
# ---------------------------------------------------------------------------
@async_test
async def test_stages_failure_degrades_stage_fields() -> None:
    routes_ = routes(stages=httpx.Response(500, content=b"{}"))
    service = make_service(routes_)

    result = await service.item_rates("30013", limit=3)

    assert result["unavailable"] == ["stage_code", "ap_cost", "open"]
    assert result["rows_total"] == 40
    for row in result["rows"]:
        assert row["stage_code"] is None
        assert row["ap_cost"] is None
        assert row["sanity_per_item"] is None
        assert row["open"] is None
        assert row["item_name"] == "固源岩组"


@async_test
async def test_items_failure_degrades_names_but_keeps_id_queries() -> None:
    routes_ = routes(items=httpx.Response(500, content=b"{}"))
    service = make_service(routes_)

    result = await service.item_rates("30013", limit=3)

    assert result["unavailable"] == ["item_name"]
    assert result["item"] == {
        "item_id": "30013",
        "name": None,
        "rarity": None,
        "item_type": None,
    }
    assert all(row["item_name"] is None for row in result["rows"])
    assert all(row["stage_code"] is not None for row in result["rows"])

    with pytest.raises(AppError) as info:
        await service.item_rates("固源岩组")
    assert info.value.code is ErrorCode.EXTERNAL_SOURCE_UNAVAILABLE


@async_test
async def test_stage_rates_need_the_stage_table() -> None:
    routes_ = routes(stages=httpx.Response(500, content=b"{}"))
    service = make_service(routes_)

    with pytest.raises(AppError) as info:
        await service.stage_rates("R8-11")

    assert info.value.code is ErrorCode.EXTERNAL_SOURCE_UNAVAILABLE


@async_test
async def test_matrix_failure_is_not_degraded() -> None:
    routes_ = routes(matrix=httpx.Response(500, content=b"{}"))
    service = make_service(routes_)

    with pytest.raises(AppError) as info:
        await service.item_rates("30013")

    assert info.value.code is ErrorCode.EXTERNAL_SOURCE_UNAVAILABLE


# ---------------------------------------------------------------------------
# 取数纪律：整表只请求一次
# ---------------------------------------------------------------------------
@async_test
async def test_matrix_stages_and_items_are_fetched_once() -> None:
    routes_ = routes()
    service = make_service(routes_)

    await service.item_rates("30013")
    await service.item_rates("30033")
    await service.stage_rates("R8-11")
    await service.top_rates("固源岩组")
    await service.stage_drop_rates("main_08-13")

    paths = routes_.paths()
    assert paths.count(MATRIX_PATH) == 1
    assert paths.count(STAGES_PATH) == 1
    assert paths.count(ITEMS_PATH) == 1


@async_test
async def test_default_service_is_a_singleton() -> None:
    from maa_api.services.game.drops import default_drop_service

    assert default_drop_service() is default_drop_service()
    assert isinstance(default_drop_service(), DropService)
