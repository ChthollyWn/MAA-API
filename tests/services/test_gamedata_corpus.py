"""拆包语料大表（M77、规格 §4.2）：白名单、只走镜像、不进缓存、既有行为不变。

全部用 `httpx.MockTransport`，不打网。异步用例用 `asyncio.run` 跑。
"""

from __future__ import annotations

import asyncio
from typing import Any

import httpx
import pytest

from maa_api.domain.errors import AppError, ErrorCode
from maa_api.services.external.gamedata import (
    GAMEDATA_CORPUS_KIND,
    GamedataClient,
    validate_corpus_table,
)
from maa_api.services.external.http import ExternalClient
from maa_api.services.external.sources import (
    CACHE_LEVEL,
    GAMEDATA_CORPUS_FILES,
    SOURCES,
    resolve_source,
)
from maa_api.settings import Settings, SourcesSettings

FASTLY_HOST = "fastly.jsdelivr.net"
CDN_HOST = "cdn.jsdelivr.net"
FASTLY_PREFIX = "/gh/Kengxxiao/ArknightsGameData@master"
CORPUS_PATH = f"{FASTLY_PREFIX}/zh_CN/gamedata/excel/character_table.json"
LEVEL_PATH = f"{FASTLY_PREFIX}/zh_CN/gamedata/levels/obt/main/level_main_01-07.json"


def async_test(func):
    """本仓库不装 pytest-asyncio：异步用例统一用 `asyncio.run` 跑。"""

    def wrapper(*args, **kwargs):
        return asyncio.run(func(*args, **kwargs))

    wrapper.__name__ = func.__name__
    return wrapper


def make_settings(**overrides: Any) -> Settings:
    sources: dict[str, Any] = {
        "use_proxy": False,
        "gamedata_rate_per_minute": 60,
    }
    sources.update(overrides)
    return Settings(sources=SourcesSettings(**sources))


class Routes:
    """按主机回响应，并记录每一次请求。"""

    def __init__(self, **hosts: Any) -> None:
        self.hosts = hosts
        self.requests: list[httpx.Request] = []

    def __call__(self, request: httpx.Request) -> httpx.Response:
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


def make_client(
    routes: Routes, *, settings: Settings | None = None
) -> tuple[GamedataClient, ExternalClient]:
    settings = settings or make_settings()
    external = ExternalClient(
        transport=httpx.MockTransport(routes),
        settings_provider=lambda: settings,
    )
    return (
        GamedataClient(external, settings_provider=lambda: settings),
        external,
    )


@async_test
async def test_corpus_table_whitelist() -> None:
    """三个文件名之外报 INVALID_PARAMETER，不发请求。"""
    routes = Routes()
    client, external = make_client(routes)
    try:
        for name in sorted(GAMEDATA_CORPUS_FILES):
            assert validate_corpus_table(name) == name
        for bad in (
            "",
            "gamedata_const.json",
            "level_main_01-07.json",
            "../item_table.json",
            "ITEM_TABLE.JSON",
        ):
            with pytest.raises(AppError) as info:
                await client.corpus_table(bad)
            assert info.value.code is ErrorCode.INVALID_PARAMETER, bad
    finally:
        await external.aclose()
    assert routes.requests == []


@async_test
async def test_corpus_table_uses_corpus_kind_without_cache_or_github() -> None:
    """请求走 gamedata_corpus；缓存不写；镜像都失败也不去 api.github.com。"""
    routes = Routes(
        **{
            FASTLY_HOST: lambda request: httpx.Response(503, text="busy"),
            CDN_HOST: lambda request: httpx.Response(503, text="busy"),
        }
    )
    # 默认允许 GitHub 兜底；语料大表必须绕过它。
    client, external = make_client(routes)
    try:
        with pytest.raises(AppError) as info:
            await client.corpus_table("character_table.json")
    finally:
        await external.aclose()

    assert info.value.code is ErrorCode.EXTERNAL_SOURCE_UNAVAILABLE
    assert "api.github.com" not in routes.hosts_seen
    # 503 会被访问层重试一次，所以每个镜像两次。
    assert routes.hosts_seen == [
        FASTLY_HOST,
        FASTLY_HOST,
        CDN_HOST,
        CDN_HOST,
    ]
    assert routes.requests[0].url.path == CORPUS_PATH
    assert external.cache.stats()["entries"] == 0
    assert external.cache.stats(GAMEDATA_CORPUS_KIND)["entries"] == 0
    # 状态记在 gamedata_corpus 名下，不是既有的 gamedata。
    assert external.status.status(GAMEDATA_CORPUS_KIND).requests == 2
    assert external.status.status("gamedata").requests == 0


def test_corpus_spec_limits() -> None:
    """24 MiB 上限；受 sources.gamedata_enabled 管，限流取 gamedata 的键。"""
    spec = SOURCES["gamedata_corpus"]
    assert spec.max_bytes == 24 * 1024 * 1024
    assert spec.hosts == (FASTLY_HOST, CDN_HOST)
    assert spec.cache_level == CACHE_LEVEL
    assert spec.rate_per_minute == 4

    disabled = Settings(sources=SourcesSettings(gamedata_enabled=False))
    with pytest.raises(AppError) as info:
        resolve_source("gamedata_corpus", disabled)
    assert info.value.code is ErrorCode.EXTERNAL_SOURCE_DISABLED

    configured = resolve_source(
        "gamedata_corpus",
        Settings(sources=SourcesSettings(gamedata_rate_per_minute=7)),
    )
    assert configured.rate_per_minute == 7


@async_test
async def test_existing_excel_and_level_unchanged() -> None:
    """excel()、level() 仍走 gamedata、镜像前缀，并写进 gamedata 的缓存。"""
    routes = Routes(
        **{
            FASTLY_HOST: b'{"ok": true}',
            CDN_HOST: b'{"ok": true}',
            "api.github.com": b'{"ok": true}',
        }
    )
    client, external = make_client(routes)
    try:
        excel_first = await client.excel("gamedata_const.json")
        excel_second = await client.excel("gamedata_const.json")
        level_first = await client.level("obt/main/level_main_01-07")
        level_second = await client.level("obt/main/level_main_01-07")
    finally:
        await external.aclose()

    assert excel_first.cached is False and excel_first.stale is False
    assert excel_second.cached is True
    assert level_first.cached is False and level_first.stale is False
    assert level_second.cached is True
    assert len(routes.requests) == 2
    assert routes.hosts_seen == [FASTLY_HOST, FASTLY_HOST]
    assert [request.url.path for request in routes.requests] == [
        f"{FASTLY_PREFIX}/zh_CN/gamedata/excel/gamedata_const.json",
        LEVEL_PATH,
    ]
    assert external.cache.stats("gamedata")["entries"] == 2
    assert external.cache.stats(GAMEDATA_CORPUS_KIND)["entries"] == 0
