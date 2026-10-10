"""PRTS 客户端的请求形状与校验（规格 §5.4、§3.6）。全夹具，不打网。"""

from __future__ import annotations

import asyncio
import json
from pathlib import Path
from typing import Any

import httpx
import pytest

from maa_api.domain.errors import AppError, ErrorCode
from maa_api.services.external.http import ExternalClient
from maa_api.services.external.prts import (
    MAX_TITLES_PER_REQUEST,
    PrtsClient,
    id_condition,
    page_condition,
    timestamp_seconds,
    validate_title,
)
from maa_api.settings import Settings, SourcesSettings

FIXTURES = Path("tests/fixtures/prts")


def async_test(func):
    """本仓库不装 pytest-asyncio：异步用例统一用 `asyncio.run` 跑。"""

    def wrapper(*args, **kwargs):
        return asyncio.run(func(*args, **kwargs))

    wrapper.__name__ = func.__name__
    return wrapper


def fixture(name: str) -> dict[str, Any]:
    return json.loads((FIXTURES / name).read_text(encoding="utf-8"))


def make_prts(handler, *, calls: list[httpx.Request] | None = None) -> PrtsClient:
    def wrapped(request: httpx.Request) -> httpx.Response:
        if calls is not None:
            calls.append(request)
        return handler(request)

    external = ExternalClient(
        transport=httpx.MockTransport(wrapped),
        settings_provider=lambda: Settings(
            sources=SourcesSettings(use_proxy=False, prts_rate_per_minute=120)
        ),
    )
    return PrtsClient(external)


def respond(payload: dict[str, Any]):
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, content=json.dumps(payload).encode("utf-8"))

    return handler


# ---------------------------------------------------------------------------
# 标题与查询校验（纯函数）
# ---------------------------------------------------------------------------
@pytest.mark.parametrize(
    "title",
    ["用户:张三", "文件:头像.png", "特殊:搜索", "模板:关卡信息", "分类:干员", "User:foo"],
)
def test_namespace_titles_are_rejected(title: str) -> None:
    with pytest.raises(AppError) as info:
        validate_title(title)
    assert info.value.code is ErrorCode.INVALID_PARAMETER


@pytest.mark.parametrize("title", ["a|b", "a[[b]]", "a{{b}}", "a#b", "a<b>", "a\nb", "", "x" * 101])
def test_dangerous_titles_are_rejected(title: str) -> None:
    with pytest.raises(AppError):
        validate_title(title)


def test_main_namespace_titles_pass() -> None:
    assert validate_title(" 7-18 爱国者之死 ") == "7-18 爱国者之死"
    assert validate_title("矢量突破/03") == "矢量突破/03"
    assert validate_title("爱国者/spine") == "爱国者/spine"


def test_condition_helpers_validate_ids() -> None:
    assert id_condition("关卡id", "act3break_01") == "[[关卡id::act3break_01]]"
    assert page_condition("7-18 爱国者之死") == "[[7-18 爱国者之死]]"
    with pytest.raises(AppError):
        id_condition("关卡id", "act3break_01]] OR [[x")
    with pytest.raises(AppError):
        page_condition("用户:张三")


def test_timestamp_seconds_parses_smw_shape() -> None:
    assert timestamp_seconds({"timestamp": "1791518400", "raw": "1/2026/10/9/4/0/0/0"}) == 1791518400
    assert timestamp_seconds("1791518400") == 1791518400
    assert timestamp_seconds(None) is None
    assert timestamp_seconds({"raw": "x"}) is None


# ---------------------------------------------------------------------------
# 请求形状
# ---------------------------------------------------------------------------
@async_test
async def test_ask_builds_smw_query() -> None:
    calls: list[httpx.Request] = []
    payload = fixture("ask_activities.json")
    prts = make_prts(respond(payload), calls=calls)
    result = await prts.ask(
        ["[[分类:有活动信息的页面]]", "[[!分类:集成战略]]"],
        printouts=["名称缩短", "活动开始时间", "活动结束时间"],
        sort="活动开始时间",
        order="desc",
        limit=20,
    )
    params = dict(calls[0].url.params)
    assert params["action"] == "ask"
    assert params["format"] == "json"
    assert params["formatversion"] == "2"
    # 多个条件是 AND（空格），不是 OR：OR 会把「不在集成战略分类」变成「在任意一个条件里」。
    assert params["query"] == (
        "[[分类:有活动信息的页面]] [[!分类:集成战略]]"
        "|?名称缩短|?活动开始时间|?活动结束时间|sort=活动开始时间|order=desc|limit=20"
    )
    pages = prts.smw_pages(result)
    assert [page.title for page in pages][:2] == ["昨日海", "矢量突破/03"]
    assert pages[0].first("名称缩短") == "昨日海"
    assert pages[0].fullurl.startswith("https://prts.wiki/w/")


@async_test
async def test_ask_rejects_bad_condition() -> None:
    prts = make_prts(respond({"query": {}}))
    with pytest.raises(AppError) as info:
        await prts.ask(["[[x]] OR 1=1"], printouts=[])
    assert info.value.code is ErrorCode.INVALID_PARAMETER


@async_test
async def test_ask_rejects_bad_sort() -> None:
    prts = make_prts(respond({"query": {}}))
    with pytest.raises(AppError):
        await prts.ask(["[[关卡id::act3break_01]]"], sort="a|b")


@async_test
async def test_wikitext_resolves_redirect_and_reads_metadata() -> None:
    calls: list[httpx.Request] = []
    payload = fixture("page_7-18.json")
    prts = make_prts(respond(payload), calls=calls)
    batch = await prts.wikitext(["7-18"])
    params = dict(calls[0].url.params)
    assert params["prop"] == "revisions"
    assert params["redirects"] == "1"
    assert params["rvslots"] == "main"
    assert params["rvprop"] == "content|ids|timestamp"
    page = batch.by_title("7-18 爱国者之死")
    assert page is not None
    assert page.redirect_from == "7-18"
    assert page.revision == 361910
    assert "{{普通关卡信息" in page.content
    refs = batch.source_refs()
    assert refs[0].title == "7-18 爱国者之死"
    assert refs[0].revision == 361910
    assert refs[0].url == "https://prts.wiki/w/7-18_爱国者之死"


@async_test
async def test_wikitext_rejects_too_many_titles() -> None:
    prts = make_prts(respond({"query": {"pages": []}}))
    with pytest.raises(AppError) as info:
        await prts.wikitext([f"关卡{i}" for i in range(MAX_TITLES_PER_REQUEST + 1)])
    assert info.value.code is ErrorCode.INVALID_PARAMETER


@async_test
async def test_categories_maps_titles() -> None:
    payload = fixture("categories.json")
    prts = make_prts(respond(payload))
    mapping = await prts.categories(["7-18 爱国者之死", "爱国者", "矢量突破/03"])
    assert "分类:敌人" in mapping["爱国者"]
    assert "分类:有活动信息的页面" in mapping["矢量突破/03"]


@async_test
async def test_search_request_shape_and_limit() -> None:
    calls: list[httpx.Request] = []
    payload = fixture("search_ajz.json")
    prts = make_prts(respond(payload), calls=calls)
    result = await prts.search("爱国者", limit=5)
    params = dict(calls[0].url.params)
    assert params["list"] == "search"
    assert params["srnamespace"] == "0"
    assert params["srlimit"] == "5"
    assert params["srprop"] == "snippet|titlesnippet|redirecttitle"
    results = result.query["search"]
    assert results[0]["title"] == "7-18 爱国者之死"
    with pytest.raises(AppError):
        await prts.search("爱国者", limit=11)


@async_test
async def test_search_rejects_dangerous_query() -> None:
    prts = make_prts(respond({"query": {"search": []}}))
    with pytest.raises(AppError):
        await prts.search("a|b")


@async_test
async def test_sections_only_takes_section_table() -> None:
    calls: list[httpx.Request] = []
    payload = fixture("sections.json")
    prts = make_prts(respond(payload), calls=calls)
    result = await prts.sections("矢量突破/03")
    params = dict(calls[0].url.params)
    assert params["action"] == "parse"
    assert params["prop"] == "sections"
    assert "rvprop" not in params
    sections = result.data["parse"]["sections"]
    assert [item["line"] for item in sections][:2] == ["关卡信息", "<span>矢量突破 #3「拟生态」作战环境&amp;敌人一览表</span>"]


@async_test
async def test_cargo_validates_identifiers_and_where() -> None:
    calls: list[httpx.Request] = []
    prts = make_prts(respond({"cargoquery": []}), calls=calls)
    await prts.cargo(
        tables=["building_skill2"],
        fields=["name", "room", "description"],
        where="room='Control'",
        limit=500,
    )
    params = dict(calls[0].url.params)
    assert params["tables"] == "building_skill2"
    assert params["fields"] == "name,room,description"
    assert params["where"] == "room='Control'"
    with pytest.raises(AppError):
        await prts.cargo(tables=["a;DROP TABLE"], fields=["x"])
    with pytest.raises(AppError):
        await prts.cargo(tables=["a"], fields=["x"], where="1=1; --")
    with pytest.raises(AppError):
        await prts.cargo(tables=["a"], fields=["x"], limit=501)


@async_test
async def test_error_response_from_mediawiki_is_mapped() -> None:
    prts = make_prts(respond(fixture("ask_error_too_large.json")))
    with pytest.raises(AppError) as info:
        await prts.ask([f"[[关卡id::act3break_{i:02d}]]" for i in range(1, 13)])
    assert info.value.code is ErrorCode.EXTERNAL_RESPONSE_INVALID
