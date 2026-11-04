"""页面服务监听与正文清洗（M77 规格 §4.2、§6 T3）。

全夹具 + `httpx.MockTransport`，不打网；异步用例用 `asyncio.run` 跑（本仓库不装
pytest-asyncio，沿用 `tests/services/test_game_pages.py` 的写法）。
"""

from __future__ import annotations

import asyncio
import json
import logging
from datetime import datetime
from typing import Any

import httpx

from maa_api.services.external.http import ExternalClient
from maa_api.services.external.prts import PrtsClient, WikiPage
from maa_api.services.game.pages import (
    PageService,
    _clean_text,
    clean_page_text,
)
from maa_api.settings import Settings, SourcesSettings

PAGE_TITLE = "7-18 爱国者之死"
PAGE_CONTENT = "==剧情==\n{{剧情跳转|7-18}}\n'''爱国者'''之死。\n\n==敌方情报==\n游击队猎犬\n"


def async_test(func):
    """本仓库不装 pytest-asyncio：异步用例统一用 `asyncio.run` 跑。"""

    def wrapper(*args, **kwargs):
        return asyncio.run(func(*args, **kwargs))

    wrapper.__name__ = func.__name__
    return wrapper


def wiki(payload: dict[str, Any]) -> httpx.Response:
    return httpx.Response(200, content=json.dumps(payload).encode("utf-8"))


def page_routes() -> dict[str, Any]:
    """wikitext + categories + 章节表三条路由；没安排的端点回 404，方便发现多发请求。"""
    revisions = {
        "query": {
            "pages": [
                {
                    "pageid": 1,
                    "ns": 0,
                    "title": PAGE_TITLE,
                    "revisions": [
                        {
                            "revid": 361910,
                            "timestamp": "2026-10-10T00:00:00Z",
                            "slots": {"main": {"content": PAGE_CONTENT}},
                        }
                    ],
                }
            ]
        }
    }
    categories = {
        "query": {
            "pages": [
                {
                    "ns": 0,
                    "title": PAGE_TITLE,
                    "categories": [
                        {"ns": 14, "title": "分类:主线关卡"},
                        {"ns": 14, "title": "分类:普通难度关卡"},
                    ],
                }
            ]
        }
    }
    sections = {"parse": {"sections": [{"line": "<span>剧情</span>"}]}}

    def handler(request: httpx.Request) -> httpx.Response:
        params = dict(request.url.params)
        key = params.get("action", "")
        if key == "query":
            key = f"query-{params.get('list') or params.get('prop', '')}"
        elif key == "parse":
            key = f"parse-{params.get('prop', '')}"
        routes = {
            "query-revisions": revisions,
            "query-categories": categories,
            "parse-sections": sections,
        }
        route = routes.get(key)
        if route is None:
            return httpx.Response(404, content=b"{}")
        return wiki(route)

    return httpx.MockTransport(handler)


def make_service() -> PageService:
    external = ExternalClient(
        transport=page_routes(),
        settings_provider=lambda: Settings(
            sources=SourcesSettings(use_proxy=False, prts_rate_per_minute=120)
        ),
    )
    return PageService(prts=PrtsClient(external))


@async_test
async def test_listener_receives_loaded_page() -> None:
    service = make_service()
    received: list[tuple[WikiPage, datetime]] = []

    async def listener(page: WikiPage, fetched_at: datetime) -> None:
        received.append((page, fetched_at))

    service.add_listener(listener)
    result = await service.page(PAGE_TITLE)

    assert len(received) == 1
    page, fetched_at = received[0]
    assert isinstance(page, WikiPage)
    assert page.title == PAGE_TITLE
    assert page.revision == 361910
    assert "爱国者" in page.content
    assert isinstance(fetched_at, datetime)
    assert fetched_at.isoformat() == result["fetched_at"]


def test_listener_error_does_not_break_page(caplog) -> None:
    service = make_service()
    called: list[str] = []

    async def broken_listener(page: WikiPage, fetched_at: datetime) -> None:
        called.append(page.title)
        raise RuntimeError("监听器炸了")

    service.add_listener(broken_listener)
    caplog.set_level(logging.DEBUG, logger="maa_api.services.game.pages")

    result = asyncio.run(service.page(PAGE_TITLE))

    assert called == [PAGE_TITLE]
    assert result["title"] == PAGE_TITLE
    assert "爱国者之死" in result["text"]
    assert any(
        record.levelno == logging.DEBUG and "页面监听器" in record.getMessage()
        for record in caplog.records
    )


def test_clean_page_text_matches_private_cleaner() -> None:
    samples = [
        PAGE_CONTENT,
        "==注释与链接==\n[[首页|返回]]\n",
        "没有标题的一行正文。\n",
        "== 标题 ==\n{{#widget:style|style=x}}正文&amp;实体\n",
    ]
    for content in samples:
        assert clean_page_text(content) == _clean_text(content)
