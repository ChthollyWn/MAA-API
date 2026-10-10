"""通用检索领域层（规格 §5.8）：`search_prts` 与 `get_prts_page` 的回答形状。

全夹具 + `httpx.MockTransport`，不打网（规格 §4.9）。异步用例用 `asyncio.run` 跑
（本仓库不装 pytest-asyncio，沿用 `tests/services/test_prts_client.py` 的写法）。
"""

from __future__ import annotations

import asyncio
import json
from pathlib import Path
from typing import Any
from urllib.parse import unquote, urlsplit

import httpx
import pytest

from maa_api.domain.errors import AppError, ErrorCode
from maa_api.services.external.http import ExternalClient
from maa_api.services.external.prts import PrtsClient
from maa_api.services.game.pages import (
    KIND_ACTIVITY,
    KIND_ENEMY,
    KIND_GACHA,
    KIND_ITEM,
    KIND_OPERATOR,
    KIND_OTHER,
    KIND_ROGUELIKE,
    KIND_STAGE,
    KIND_STORY,
    MAX_ACTIVITY_STAGE_REFS,
    MAX_SNIPPET_CHARS,
    PageService,
    classify_kind,
    clean_section_title,
    default_page_service,
    make_page_url,
    make_search_url,
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


def category_titles(page: dict[str, Any]) -> list[str]:
    """`categories.json` 的 `分类:xxx` 标题（`prop=categories` 的真实返回就带前缀）。"""
    return [entry["title"] for entry in page.get("categories", [])]


def wiki(payload: dict[str, Any]) -> httpx.Response:
    return httpx.Response(200, content=json.dumps(payload).encode("utf-8"))


def dispatch_transport(routes: dict[str, Any], *, calls: list[httpx.Request] | None = None):
    """按 `action` + `list` / `prop` 分派夹具；键形如 ``query-search``、``query-revisions``。

    值可以是夹具字典，也可以是 ``(params) -> 夹具字典`` 的 callable。没安排的端点回 404，
    测试里能立刻看出领域层多发了请求。
    """

    def handler(request: httpx.Request) -> httpx.Response:
        if calls is not None:
            calls.append(request)
        params = dict(request.url.params)
        action = params.get("action", "")
        key = action
        if action == "query":
            if params.get("list"):
                key = f"query-{params['list']}"
            elif params.get("prop"):
                key = f"query-{params['prop']}"
        elif action == "parse":
            key = f"parse-{params.get('prop', '')}"
        route = routes.get(key)
        if route is None:
            return httpx.Response(404, content=b"{}")
        return wiki(route(params) if callable(route) else route)

    return httpx.MockTransport(handler)


def make_service(
    routes: dict[str, Any], *, calls: list[httpx.Request] | None = None
) -> PageService:
    external = ExternalClient(
        transport=dispatch_transport(routes, calls=calls),
        settings_provider=lambda: Settings(
            sources=SourcesSettings(use_proxy=False, prts_rate_per_minute=120)
        ),
    )
    return PageService(prts=PrtsClient(external))


def page_routes(
    payload: dict[str, Any],
    *,
    sections: dict[str, Any] | None = None,
    categories: list[str] | None = None,
):
    """`page_*.json` → 取数路由：wikitext + categories（+ 章节表）。

    `page_*.json` 里没有分类（夹具只留了 wikitext），分类用 `categories.json` 的真实返回
    显式传进来；不传就当作「分类取不到」。
    """
    page = payload["query"]["pages"][0]
    title = page["title"]
    redirects = payload["query"].get("redirects", [])
    page_categories = [{"ns": 14, "title": name} for name in (categories or [])]

    def revisions(params: dict[str, str]) -> dict[str, Any]:
        requested = params.get("titles", "")
        matched = [item for item in redirects if item["from"] == requested]
        return {"query": {"pages": [page], "redirects": matched}}

    def category_response(params: dict[str, str]) -> dict[str, Any]:
        resolvable = {title: page_categories}
        for item in redirects:
            resolvable[item["from"]] = page_categories
        return {
            "query": {
                "pages": [
                    {"ns": 0, "title": name, "categories": resolvable[name]}
                    for name in params.get("titles", "").split("|")
                    if name in resolvable
                ]
            }
        }

    routes: dict[str, Any] = {
        "query-revisions": revisions,
        "query-categories": category_response,
        "parse-sections": sections if sections is not None else {"parse": {"sections": []}},
    }
    return title, routes


def categories_fixture(*titles: str) -> list[str]:
    """从 `categories.json` 取指定标题的分类（真实 `prop=categories` 的返回形状）。"""
    pages = fixture("categories.json")["query"]["pages"]
    wanted = set(titles)
    return [name for page in pages if page["title"] in wanted for name in category_titles(page)]


def search_routes(
    payload: dict[str, Any],
    categories: dict[str, Any] | None = None,
    *,
    limit: int = 10,
) -> dict[str, Any]:
    """`search_ajz.json` → 检索路由；`limit` 按真实 API 的行为裁掉多余命中。"""
    sliced = json.loads(json.dumps(payload))
    if "query" in sliced:
        sliced["query"]["search"] = sliced["query"].get("search", [])[:limit]
    return {
        "query-search": sliced,
        "query-categories": categories if categories is not None else {"query": {"pages": []}},
    }


# ---------------------------------------------------------------------------
# 纯函数：kind 推断、章节标题清洗、url 形状
# ---------------------------------------------------------------------------
@pytest.mark.parametrize(
    ("categories", "expected"),
    [
        (["分类:有活动信息的页面", "分类:矢量突破"], KIND_ACTIVITY),
        (["分类:集成战略", "分类:主题"], KIND_ROGUELIKE),
        (["分类:主线关卡", "分类:含有内置剧情的关卡"], KIND_STAGE),
        (["分类:普通难度关卡"], KIND_STAGE),
        (["分类:干员", "分类:六星干员"], KIND_OPERATOR),
        (["分类:敌人", "分类:领袖级别的敌人"], KIND_ENEMY),
        (["分类:道具", "分类:材料"], KIND_ITEM),
        (["分类:材料"], KIND_ITEM),
        (["分类:寻访", "分类:限定寻访"], KIND_GACHA),
        (["分类:剧情", "分类:主线剧情"], KIND_STORY),
        # 优先级：活动 > 关卡 > 集成战略 > 干员 > 敌人 > 道具 > 卡池 > 剧情 > 其他
        (["分类:干员", "分类:有活动信息的页面"], KIND_ACTIVITY),
        (["分类:含有内置剧情的关卡"], KIND_STAGE),
        (["分类:集成战略", "分类:干员"], KIND_ROGUELIKE),
        (["分类:道具", "分类:敌人"], KIND_ENEMY),
        ([], KIND_OTHER),
        (["分类:未归类的东西"], KIND_OTHER),
    ],
)
def test_classify_kind_table(categories: list[str], expected: str) -> None:
    assert classify_kind(categories) == expected


def test_clean_section_title_decodes_html() -> None:
    cleaned = clean_section_title("<span>矢量突破 #3「拟生态」作战环境&amp;敌人一览表</span>")
    assert cleaned == "矢量突破 #3「拟生态」作战环境&敌人一览表"
    assert clean_section_title("关卡信息") == "关卡信息"


def test_make_page_url_keeps_raw_title_and_replaces_spaces() -> None:
    assert make_page_url("7-18 爱国者之死") == "https://prts.wiki/w/7-18_爱国者之死"
    assert make_page_url("矢量突破/03") == "https://prts.wiki/w/矢量突破/03"


def test_make_search_url_encodes_the_query() -> None:
    assert make_search_url("爱国者") == "https://prts.wiki/w/Special:搜索?search=%E7%88%B1%E5%9B%BD%E8%80%85"
    assert make_search_url("7-18 爱国者") == "https://prts.wiki/w/Special:搜索?search=7-18+%E7%88%B1%E5%9B%BD%E8%80%85"


def test_default_page_service_is_a_singleton() -> None:
    """工具层（`tools/game.py`）用这个入口取进程内共享的门面。"""
    assert default_page_service() is default_page_service()
    assert isinstance(default_page_service(), PageService)


# ---------------------------------------------------------------------------
# search_prts
# ---------------------------------------------------------------------------
@async_test
async def test_search_returns_results_with_kind_and_cleaned_snippet() -> None:
    calls: list[httpx.Request] = []
    payload = fixture("search_ajz.json")
    pages = fixture("categories.json")["query"]["pages"]
    categories = {
        "query": {
            "pages": [
                {
                    "ns": 0,
                    "title": page["title"],
                    "categories": [{"ns": 14, "title": c} for c in category_titles(page)],
                }
                for page in pages
            ]
        }
    }
    service = make_service(search_routes(payload, categories, limit=5), calls=calls)

    result = await service.search("爱国者", limit=5)

    search_params = dict(calls[0].url.params)
    assert search_params["list"] == "search"
    assert search_params["srsearch"] == "爱国者"
    assert search_params["srnamespace"] == "0"
    assert search_params["srlimit"] == "5"
    # 分类只查一次，且把全部命中标题一起带上（一次批量分类查询）。
    category_calls = [call for call in calls if dict(call.url.params).get("prop") == "categories"]
    assert len(category_calls) == 1
    assert dict(category_calls[0].url.params)["titles"].split("|") == [
        "7-18 爱国者之死",
        "爱国者/spine",
        "爱国者的源石祭坛/spine",
        "爱国者的源石祭坛",
        "爱国者",
    ]

    assert result["query"] == "爱国者"
    assert result["results_total"] == 5
    assert result["untrusted"] is True
    assert result["unavailable"] == []
    assert result["region_note"] is None
    assert [item["title"] for item in result["results"]] == [
        "7-18 爱国者之死",
        "爱国者/spine",
        "爱国者的源石祭坛/spine",
        "爱国者的源石祭坛",
        "爱国者",
    ]
    first = result["results"][0]
    assert "searchmatch" not in first["snippet"]
    assert "<span" not in first["snippet"]
    assert "\n" not in first["snippet"]
    assert "爱国者之死" in first["snippet"]
    assert first["kind"] == KIND_STAGE
    assert first["url"] == "https://prts.wiki/w/7-18_爱国者之死"
    assert result["results"][4]["kind"] == KIND_ENEMY
    # 分类里没有的页面（/spine 这类子页面）落回「其他」。
    assert result["results"][1]["kind"] == KIND_OTHER
    assert len(result["sources"]) == 1
    assert result["sources"][0]["kind"] == "prts"
    assert result["sources"][0]["label"] == "PRTS"
    # 出处给人类可读的搜索页，而不是底层的 api.php JSON 链接。
    assert result["sources"][0]["url"] == make_search_url("爱国者")
    assert result["sources"][0]["url"].startswith("https://prts.wiki/w/")


@async_test
async def test_search_snippet_is_capped() -> None:
    payload = {
        "query": {
            "searchinfo": {"totalhits": 1},
            "search": [{"ns": 0, "title": "爱国者", "snippet": "爱" * 400}],
        }
    }
    service = make_service(search_routes(payload))
    result = await service.search("爱国者")
    snippet = result["results"][0]["snippet"]
    assert len(snippet) == MAX_SNIPPET_CHARS
    assert snippet.endswith("…")


@async_test
async def test_search_gives_kind_other_when_categories_fail() -> None:
    """分类请求失败时 kind 一律给「其他」，不为此抛错（规格 §5.8）。"""
    payload = fixture("search_ajz.json")
    service = make_service(
        search_routes(
            payload, {"error": {"code": "internal_api_error", "info": "boom"}}, limit=3
        )
    )
    result = await service.search("爱国者", limit=3)
    assert result["results_total"] == 3
    assert {item["kind"] for item in result["results"]} == {KIND_OTHER}
    assert result["sources"][0]["kind"] == "prts"


@async_test
async def test_search_keeps_kind_when_one_title_has_no_categories() -> None:
    service = make_service(
        search_routes(
            fixture("search_ajz.json"),
            {"query": {"pages": [{"ns": 0, "title": "爱国者", "categories": [
                {"ns": 14, "title": "分类:敌人"}
            ]}]}},
            limit=5,
        )
    )
    result = await service.search("爱国者", limit=5)
    kinds = {item["title"]: item["kind"] for item in result["results"]}
    assert kinds["爱国者"] == KIND_ENEMY
    # 分类表里没有的标题落回「其他」，不借用别人的分类。
    assert kinds["爱国者/spine"] == KIND_OTHER


@async_test
async def test_search_propagates_app_error_when_search_itself_fails() -> None:
    service = make_service(
        search_routes({"error": {"code": "readapidenied", "info": "nope"}})
    )
    with pytest.raises(AppError) as info:
        await service.search("爱国者")
    assert info.value.code is ErrorCode.EXTERNAL_RESPONSE_INVALID


# ---------------------------------------------------------------------------
# get_prts_page
# ---------------------------------------------------------------------------
@async_test
async def test_page_redirect_carries_revision_categories_and_stage_fields() -> None:
    calls: list[httpx.Request] = []
    _title, routes = page_routes(
        fixture("page_7-18.json"), categories=categories_fixture("7-18 爱国者之死")
    )
    service = make_service(routes, calls=calls)

    result = await service.page("7-18")

    revisions_params = dict(calls[0].url.params)
    assert revisions_params["prop"] == "revisions"
    assert revisions_params["redirects"] == "1"
    assert revisions_params["titles"] == "7-18"

    assert result["title"] == "7-18 爱国者之死"
    assert result["redirected_from"] == "7-18"
    assert result["revision"] == 361910
    assert result["kind"] == KIND_STAGE
    assert result["categories"] == [
        "分类:主线关卡",
        "分类:含有内置剧情的关卡",
        "分类:普通难度关卡",
        "分类:突袭难度关卡",
    ]
    assert result["fields"]["type"] == "stage"
    stage = result["fields"]["stage"]
    assert stage["code"] == "7-18"
    assert stage["name"] == "爱国者之死"
    assert stage["stage_id"] == "main_07-16"
    assert stage["stage_type"] == "主线"
    assert stage["recommend_level"] == "精英2 LV.30"
    assert stage["deploy_limit"] == 9
    assert stage["initial_cost"] == 10
    assert stage["life_points"] == 3
    assert stage["enemy_count"] == 45
    assert stage["map_size"] == "12×8"
    assert stage["first_drops"] == ["至纯源石:三星获得", "阿米娅:首次掉落"]
    assert stage["has_challenge"] is True
    assert any(enemy["name"] == "游击队猎犬" for enemy in stage["enemies"])
    assert set(stage["enemies"][0]) == {"name", "count", "level", "rank", "hp"}
    assert any(device["name"] == "干扰地雷" for device in stage["devices"])
    assert result["sections"] == [
        "剧情",
        "普通",
        "突袭",
        "敌方情报",
        "关卡内剧情",
        "我方装置",
        "地图装置",
        "材料掉落",
        "注释与链接",
    ]
    assert "爱国者之死" in result["text"]
    assert result["truncated"] is None
    assert result["untrusted"] is True
    assert result["unavailable"] == []
    assert result["region_note"] is None
    assert len(result["sources"]) == 1
    source = result["sources"][0]
    assert source["kind"] == "prts"
    assert source["label"] == "PRTS"
    assert source["revision"] == 361910
    assert source["title"] == "7-18 爱国者之死"
    assert unquote(urlsplit(source["url"]).path) == "/w/7-18_爱国者之死"
    assert source["url"].startswith("https://prts.wiki/w/")


@async_test
async def test_page_activity_fields() -> None:
    # `昨日海` 不在 categories.json 里：活动页的分类按页面上的 `{{活动信息}}` 断言。
    _title, routes = page_routes(
        fixture("page_zuori.json"), categories=["分类:有活动信息的页面"]
    )
    service = make_service(routes)
    result = await service.page("昨日海")
    assert result["kind"] == KIND_ACTIVITY
    assert result["fields"]["type"] == "activity"
    activity = result["fields"]["activity"]
    assert activity["name"] == "昨日海"
    assert activity["type"] == "支线故事"
    assert activity["start_at"].startswith("2026-10-09T12:00")
    assert activity["end_at"].startswith("2026-10-30T03:59")
    assert activity["codes"]
    assert activity["stage_count"] == len(activity["stages"])


@async_test
async def test_page_activity_stage_refs_are_capped() -> None:
    """关卡引用按 MAX_ACTIVITY_STAGE_REFS 截断，总量留在 `stage_count`（§4.4）。"""
    links = "".join(f"* {{{{关卡链接|YW-{index}}}}}\n" for index in range(1, 90))
    content = (
        "{{活动信息\n|名称缩短=昨日海\n|类型=支线故事\n"
        "|活动开始时间=2026-10-09 12:00\n|活动结束时间=2026-10-30 03:59\n}}\n" + links
    )
    page = {
        "pageid": 1,
        "ns": 0,
        "title": "昨日海",
        "revisions": [
            {
                "revid": 2,
                "timestamp": "2026-10-10T00:00:00Z",
                "slots": {"main": {"content": content}},
            }
        ],
    }
    _title, routes = page_routes({"query": {"pages": [page]}})
    service = make_service(routes)
    activity = (await service.page("昨日海"))["fields"]["activity"]
    assert len(activity["stages"]) == MAX_ACTIVITY_STAGE_REFS
    assert len(activity["codes"]) == MAX_ACTIVITY_STAGE_REFS
    assert activity["stage_count"] == 89


@async_test
async def test_page_sections_are_cleaned_and_strip_html() -> None:
    _title, routes = page_routes(fixture("page_vec03.json"), sections=fixture("sections.json"))
    service = make_service(routes)
    result = await service.page("矢量突破/03")
    assert result["sections"] == [
        "关卡信息",
        "矢量突破 #3「拟生态」作战环境&敌人一览表",
        "突破里程碑",
        "蚀刻章套组",
        "音乐鉴赏",
    ]
    assert all("<span>" not in title and "&amp;" not in title for title in result["sections"])


@async_test
async def test_page_sections_failure_falls_back_to_wikitext_headings() -> None:
    """章节表取不到时不抛错（规格 §5.8）：退回 wikitext 里能切出来的标题。"""
    title, routes = page_routes(fixture("page_7-18.json"))
    routes.pop("parse-sections")
    service = make_service(routes)
    result = await service.page(title)
    assert result["sections"][:3] == ["剧情", "普通", "突袭"]
    assert result["sections"]


@async_test
async def test_page_without_headings_has_empty_sections() -> None:
    """页面真没有章节时给 `[]`（章节表与 wikitext 都没有标题）。"""
    plain_page = {
        "pageid": 1,
        "ns": 0,
        "title": "矢量突破/03",
        "revisions": [
            {
                "revid": 1,
                "timestamp": "2026-10-10T00:00:00Z",
                "slots": {"main": {"content": "只有一行正文。\n"}},
            }
        ],
    }
    _title, routes = page_routes({"query": {"pages": [plain_page]}})
    service = make_service(routes)
    result = await service.page("矢量突破/03")
    assert result["sections"] == []
    assert result["text"] == "只有一行正文。"


@async_test
async def test_page_section_body_and_cleaning() -> None:
    title, routes = page_routes(fixture("page_7-18.json"))
    service = make_service(routes)
    result = await service.page(title, section="剧情")
    assert "爱国者之死" in result["text"]
    assert "{{剧情跳转" not in result["text"]
    assert "==普通==" not in result["text"]


@async_test
async def test_page_unknown_section_lists_available_sections() -> None:
    title, routes = page_routes(fixture("page_7-18.json"))
    service = make_service(routes)
    with pytest.raises(AppError) as info:
        await service.page(title, section="不存在的章节")
    assert info.value.code is ErrorCode.GAME_DATA_NOT_FOUND
    assert "剧情" in info.value.message
    assert "不存在的章节" in info.value.message


@async_test
async def test_page_missing_is_not_found() -> None:
    missing = {"pageid": None, "ns": 0, "title": "7-18 爱国者之死", "missing": True}
    service = make_service(
        {
            "query-revisions": {"query": {"pages": [missing]}},
            "query-categories": {"query": {"pages": []}},
            "parse-sections": {"parse": {"sections": []}},
        }
    )
    with pytest.raises(AppError) as info:
        await service.page("7-18 爱国者之死")
    assert info.value.code is ErrorCode.GAME_DATA_NOT_FOUND


@pytest.mark.parametrize("max_chars", [0, 499, 8001, -1, True])
def test_page_rejects_out_of_range_max_chars(max_chars: Any) -> None:
    """参数校验在任何取数之前发生，所以这里可以直接 `asyncio.run`（便于 parametrize）。"""
    title, routes = page_routes(fixture("page_7-18.json"))
    service = make_service(routes)
    with pytest.raises(AppError) as info:
        asyncio.run(service.page(title, max_chars=max_chars))
    assert info.value.code is ErrorCode.INVALID_PARAMETER


@async_test
async def test_page_rejects_negative_offset() -> None:
    title, routes = page_routes(fixture("page_7-18.json"))
    service = make_service(routes)
    with pytest.raises(AppError) as info:
        await service.page(title, offset=-1)
    assert info.value.code is ErrorCode.INVALID_PARAMETER


@async_test
async def test_page_rejects_other_namespaces() -> None:
    """命名空间校验交给 `prts.wikitext`，领域层不重复实现。"""
    title, routes = page_routes(fixture("page_7-18.json"))
    service = make_service(routes)
    with pytest.raises(AppError) as info:
        await service.page("用户:张三")
    assert info.value.code is ErrorCode.INVALID_PARAMETER


@async_test
async def test_page_truncates_and_reports_next_offset() -> None:
    title, routes = page_routes(fixture("page_vec03.json"))
    service = make_service(routes)

    first = await service.page(title, max_chars=5000)
    total = first["truncated"]["total_chars"]
    assert len(first["text"]) == 5000
    assert total > 5000
    assert first["truncated"] == {"field": "text", "total_chars": total, "next_offset": 5000}

    second = await service.page(title, offset=5000, max_chars=8000)
    assert len(second["text"]) == total - 5000
    assert second["truncated"] is None

    last = await service.page(title, offset=total - 10, max_chars=8000)
    assert len(last["text"]) == 10
    assert last["text"] == second["text"][-10:]
    assert last["truncated"] is None

    full = await service.page(title, offset=0, max_chars=8000)
    assert full["truncated"]["next_offset"] == 8000
    assert full["text"] == (first["text"] + second["text"])[:8000]


@async_test
async def test_page_offset_beyond_end_returns_empty_text() -> None:
    title, routes = page_routes(fixture("page_7-18.json"))
    service = make_service(routes)
    result = await service.page(title, offset=10_000)
    assert result["text"] == ""
    assert result["truncated"] is None


@async_test
async def test_page_without_known_template_has_no_fields() -> None:
    """没有已知模板时 `fields` 为 null（本里程碑只支持活动与关卡）。"""
    rewritten = {
        "query": {
            "pages": [
                {
                    "pageid": 96813,
                    "ns": 0,
                    "title": "矢量突破/03",
                    "revisions": [
                        {
                            "revid": 1,
                            "timestamp": "2026-10-10T00:00:00Z",
                            "slots": {
                                "main": {
                                    "content": "{{#widget:style|style=x}}\n==注释与链接==\n[[首页|返回]]\n"
                                }
                            },
                        }
                    ],
                }
            ]
        }
    }
    _title, routes = page_routes(rewritten)
    service = make_service(routes)
    result = await service.page("矢量突破/03")
    assert result["fields"] is None
    assert result["text"] == "== 注释与链接 ==\n返回"
