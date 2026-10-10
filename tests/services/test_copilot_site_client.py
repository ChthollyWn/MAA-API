"""作业站客户端（规格 §9.4、附录 D）：白名单、请求形状、404 口径、缓存与降级。

全部用 `tests/fixtures/copilot/` 的真实响应快照与 `httpx.MockTransport`，不打网
（来源与抓取日期见同目录 README.md）。异步用例用 `asyncio.run` 跑（本仓库不装
pytest-asyncio）。
"""

from __future__ import annotations

import asyncio
import json
from functools import wraps
from pathlib import Path
from typing import Any, Callable

import httpx
import pytest

from maa_api.domain.errors import AppError, ErrorCode
from maa_api.services.external.copilot_site import (
    COPILOT_SITE,
    DEFAULT_LIMIT,
    MAX_LIMIT,
    ORDERS,
    CopilotSiteClient,
    copilot_link,
    parse_content,
)
from maa_api.services.external.http import USER_AGENT, ExternalClient
from maa_api.services.external.sources import build_url, get_spec
from maa_api.settings import Settings, SourcesSettings

FIXTURES = Path(__file__).resolve().parents[1] / "fixtures" / "copilot"

QUERY_PATH = "/copilot/query"
GET_PATH = "/copilot/get/22311"


def async_test(func):
    """本仓库不装 pytest-asyncio：异步用例统一用 `asyncio.run` 跑。"""

    def wrapper(*args, **kwargs):
        return asyncio.run(func(*args, **kwargs))

    return wraps(func)(wrapper)


def fixture(name: str) -> Any:
    return json.loads((FIXTURES / name).read_text(encoding="utf-8"))


def _settings() -> Settings:
    return Settings(sources=SourcesSettings(use_proxy=False))


class SiteHandler:
    """按路径回夹具，并记录每一次请求。"""

    def __init__(self, routes: dict[str, Any]) -> None:
        self.routes = routes
        self.requests: list[httpx.Request] = []

    def __call__(self, request: httpx.Request) -> httpx.Response:
        self.requests.append(request)
        route = self.routes.get(request.url.path)
        if route is None:
            return httpx.Response(404, json={"detail": "not found"})
        if callable(route):
            route = route(request)
        return httpx.Response(
            200, content=json.dumps(route, ensure_ascii=False).encode("utf-8")
        )


def make_client(routes: dict[str, Any]) -> tuple[CopilotSiteClient, SiteHandler]:
    handler = SiteHandler(routes)
    external = ExternalClient(
        transport=httpx.MockTransport(handler), settings_provider=_settings
    )
    return CopilotSiteClient(external), handler


# ---------------------------------------------------------------------------
# 白名单与 URL 形状
# ---------------------------------------------------------------------------
def test_source_is_registered_in_the_outbound_whitelist() -> None:
    spec = get_spec("copilot_site")
    assert spec.hosts == ("prts.maa.plus",)
    assert spec.paths["query"] == "/copilot/query"
    assert spec.paths["get"] == "/copilot/get/{site_id}"
    assert spec.cache_level == "live"
    assert spec.rate_per_minute == 20


def test_query_url_is_built_from_the_registry() -> None:
    url = build_url(
        "copilot_site",
        "query",
        {"level_keyword": "main_01-07", "order_by": "hot", "page": "1", "limit": "5"},
    )
    assert url == (
        "https://prts.maa.plus/copilot/query"
        "?level_keyword=main_01-07&order_by=hot&page=1&limit=5"
    )


def test_get_url_replaces_the_path_placeholder() -> None:
    assert build_url("copilot_site", "get", {"site_id": "22311"}) == (
        "https://prts.maa.plus/copilot/get/22311"
    )


def test_path_parameter_rejects_url_shaped_values() -> None:
    """登记表这一层不接受查询串 / 片段 / 空格。

    纯数字这一关由客户端把（`_site_id`）：比登记的路径段规则更严，两条一起挡住把任意
    东西拼进路径的用法。
    """
    for bad in ("22311?x=1", "22311#f", "22 311", "22\n311"):
        with pytest.raises(AppError) as info:
            build_url("copilot_site", "get", {"site_id": bad})
        assert info.value.code is ErrorCode.EXTERNAL_SOURCE_UNAVAILABLE


def test_unknown_parameter_is_rejected() -> None:
    with pytest.raises(AppError):
        build_url("copilot_site", "query", {"url": "https://evil.example/x"})


def test_link_format_is_frozen() -> None:
    assert copilot_link(22311) == "https://prts.maa.plus/copilot/22311"
    assert copilot_link("abc") is None
    assert COPILOT_SITE == "https://prts.maa.plus/"


# ---------------------------------------------------------------------------
# 请求形状与解析
# ---------------------------------------------------------------------------
@async_test
async def test_query_sends_get_with_user_agent_and_no_extra_params() -> None:
    client, handler = make_client({QUERY_PATH: fixture("query_main_01-07.json")})
    data = await client.query(stage="main_01-07", order="hot", page=1, limit=5)

    (request,) = handler.requests
    assert request.method == "GET"
    assert request.headers["User-Agent"] == USER_AGENT
    assert request.url.host == "prts.maa.plus"
    assert request.url.path == QUERY_PATH
    assert dict(request.url.params) == {
        "level_keyword": "main_01-07",
        "order_by": "hot",
        "page": "1",
        "limit": "5",
    }
    assert data.total == 298
    assert data.page == 1
    assert data.limit == 50  # 站点回显它自己的分页字段
    assert data.has_next is True
    assert len(data.rows) == 5
    assert data.source_ref().kind == "copilot_site"
    assert data.source_ref().label == "作业站"


@async_test
async def test_query_row_fields_are_parsed() -> None:
    client, _handler = make_client({QUERY_PATH: fixture("query_main_01-07.json")})
    data = await client.query(stage="main_01-07", limit=5)
    row = next(item for item in data.rows if item.site_id == 22311)
    assert row.uploader == "Genicur"
    assert row.views == 833
    assert row.rating_ratio == 0.9
    assert row.rating_level == 9
    assert row.not_enough_rating is False
    assert row.available is True
    assert row.hot_score == pytest.approx(13.154089847363426)
    assert row.as_dict()["link"] == "https://prts.maa.plus/copilot/22311"
    # 列表里的 content 已经是「不含 actions」的字符串（站点自己省的）。
    content = row.parse_content()
    assert content is not None
    assert "actions" not in content
    assert content["doc"]["title"].startswith("1-7 稀音")


@async_test
async def test_get_returns_the_full_copilot_with_actions() -> None:
    client, handler = make_client({GET_PATH: fixture("get_22311.json")})
    record = await client.get(22311)

    (request,) = handler.requests
    assert request.url.path == GET_PATH
    assert dict(request.url.params) == {}
    assert len(record.rows) == 1
    row = record.rows[0]
    assert row.site_id == 22311
    content = row.parse_content()
    assert content is not None
    assert content["version"] == 3
    assert content["difficulty"] == 1
    assert [action["type"] for action in content["actions"]][:2] == ["SpeedUp", "Deploy"]
    assert content["actions"][1]["location"] == [8, 2]


@async_test
async def test_get_accepts_a_string_id() -> None:
    client, handler = make_client({GET_PATH: fixture("get_22311.json")})
    record = await client.get("22311")
    assert record.rows[0].site_id == 22311
    assert handler.requests[0].url.path == GET_PATH


@async_test
async def test_repeated_calls_hit_the_cache() -> None:
    client, handler = make_client({QUERY_PATH: fixture("query_main_01-07.json")})
    await client.query(stage="main_01-07", limit=5)
    second = await client.query(stage="main_01-07", limit=5)
    assert len(handler.requests) == 1
    assert second.cached is True
    assert second.stale is False


# ---------------------------------------------------------------------------
# 404 口径（HTTP 200 + body status_code=404）
# ---------------------------------------------------------------------------
@async_test
async def test_get_maps_body_404_to_game_data_not_found() -> None:
    client, _handler = make_client({"/copilot/get/99999999": fixture("get_404.json")})
    with pytest.raises(AppError) as info:
        await client.get(99999999)
    assert info.value.code is ErrorCode.GAME_DATA_NOT_FOUND
    assert info.value.details["site_status"] == 404
    assert "99999999" in (info.value.message or "")


@async_test
async def test_query_body_404_is_a_source_error() -> None:
    client, _handler = make_client(
        {QUERY_PATH: {"status_code": 404, "message": "作业不存在"}}
    )
    with pytest.raises(AppError) as info:
        await client.query(stage="main_01-07", limit=5)
    # 需求方按 §3.5 记 unavailable：这里给的是可降级的那一类错误码。
    assert info.value.code is ErrorCode.EXTERNAL_SOURCE_UNAVAILABLE
    assert info.value.details["site_status"] == 404


@async_test
async def test_http_404_is_a_source_error() -> None:
    client, _handler = make_client({})
    with pytest.raises(AppError) as info:
        await client.get(22311)
    assert info.value.code is ErrorCode.EXTERNAL_SOURCE_UNAVAILABLE


@async_test
async def test_response_without_status_code_is_invalid() -> None:
    client, _handler = make_client({QUERY_PATH: {"data": []}})
    with pytest.raises(AppError) as info:
        await client.query(stage="main_01-07", limit=5)
    assert info.value.code is ErrorCode.EXTERNAL_RESPONSE_INVALID


# ---------------------------------------------------------------------------
# 参数校验
# ---------------------------------------------------------------------------
@async_test
async def test_invalid_order_is_rejected_before_the_request() -> None:
    client, handler = make_client({QUERY_PATH: fixture("query_main_01-07.json")})
    for order in ORDERS:
        await client.query(stage="main_01-07", order=order, limit=1)
    with pytest.raises(AppError) as info:
        await client.query(stage="main_01-07", order="bogus")
    assert info.value.code is ErrorCode.INVALID_PARAMETER
    assert len(handler.requests) == len(ORDERS)


@async_test
async def test_limit_range_is_enforced() -> None:
    client, handler = make_client({QUERY_PATH: fixture("query_main_01-07.json")})
    for bad in (0, -1, MAX_LIMIT + 1):
        with pytest.raises(AppError) as info:
            await client.query(stage="main_01-07", limit=bad)
        assert info.value.code is ErrorCode.INVALID_PARAMETER
    assert handler.requests == []


@async_test
async def test_stage_keyword_rejects_url_shaped_input() -> None:
    client, handler = make_client({QUERY_PATH: fixture("query_main_01-07.json")})
    for bad in ("https://evil.example/x", "1-7&order_by=id", "中文关卡名"):
        with pytest.raises(AppError) as info:
            await client.query(stage=bad, limit=1)
        assert info.value.code is ErrorCode.INVALID_PARAMETER
    assert handler.requests == []


@async_test
async def test_site_id_must_be_numeric() -> None:
    client, handler = make_client({GET_PATH: fixture("get_22311.json")})
    with pytest.raises(AppError) as info:
        await client.get("22a11")
    assert info.value.code is ErrorCode.INVALID_PARAMETER
    assert handler.requests == []


def test_default_limit_matches_the_tool_spec() -> None:
    assert DEFAULT_LIMIT == 5
    assert MAX_LIMIT == 10 * 5  # 客户端上限 50；工具层的 1–10 由 params_model 夹


# ---------------------------------------------------------------------------
# parse_content
# ---------------------------------------------------------------------------
def test_parse_content_accepts_objects_and_strings() -> None:
    assert parse_content({"a": 1}) == {"a": 1}
    assert parse_content('{"a": 1}') == {"a": 1}
    assert parse_content("not json") is None
    assert parse_content("[]") is None
    assert parse_content(None) is None
