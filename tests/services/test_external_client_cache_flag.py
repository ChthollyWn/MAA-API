"""访问层的 `get_json(cache=False)`（M77、规格 §4.2）。

不读缓存、不写缓存、失败不回退旧值；同键并发合并照旧。全部用 `httpx.MockTransport`。
"""

from __future__ import annotations

import asyncio
from typing import Callable

import httpx
import pytest

from maa_api.domain.errors import AppError, ErrorCode
from maa_api.services.external.cache import CacheStore, cache_key
from maa_api.services.external.http import ExternalClient
from maa_api.services.external.status import SourceStatusRegistry
from maa_api.settings import Settings, SourcesSettings

PRTS_ASK = {
    "action": "ask",
    "query": "[[分类:有活动信息的页面]]",
    "format": "json",
    "formatversion": "2",
}


def async_test(func):
    """本仓库不装 pytest-asyncio：异步用例统一用 `asyncio.run` 跑。"""

    def wrapper(*args, **kwargs):
        return asyncio.run(func(*args, **kwargs))

    wrapper.__name__ = func.__name__
    return wrapper


class FakeClock:
    """可控时钟：缓存 TTL 与重试等待都由测试推进。"""

    def __init__(self, start: float = 1_700_000_000.0) -> None:
        self.now = start
        self.slept: list[float] = []

    def monotonic(self) -> float:
        return self.now

    def wall(self) -> float:
        return self.now

    async def sleep(self, seconds: float) -> None:
        self.slept.append(seconds)
        self.now += seconds


def make_client(
    handler: Callable[[httpx.Request], httpx.Response] | httpx.AsyncBaseTransport,
) -> tuple[ExternalClient, FakeClock]:
    clock = FakeClock()
    transport = (
        handler
        if isinstance(handler, httpx.AsyncBaseTransport)
        else httpx.MockTransport(handler)
    )
    settings = Settings(
        sources=SourcesSettings(use_proxy=False, prts_rate_per_minute=120)
    )
    client = ExternalClient(
        transport=transport,
        settings_provider=lambda: settings,
        clock=clock.monotonic,
        wall_clock=clock.wall,
        sleep=clock.sleep,
        jitter=lambda: 0.0,
        cache=CacheStore(clock=clock.wall),
        status=SourceStatusRegistry(clock=clock.wall),
    )
    return client, clock


def counting_handler() -> tuple[Callable[[httpx.Request], httpx.Response], dict[str, int]]:
    calls = {"n": 0}

    def handler(request: httpx.Request) -> httpx.Response:
        calls["n"] += 1
        return httpx.Response(200, json={"query": {"n": calls["n"]}})

    return handler, calls


@async_test
async def test_cache_false_skips_read_and_write() -> None:
    """同一请求发两次网络，缓存里不留条目。"""
    handler, calls = counting_handler()
    client, _ = make_client(handler)
    try:
        first = await client.get_json("prts", "api", PRTS_ASK, cache=False)
        second = await client.get_json("prts", "api", PRTS_ASK, cache=False)
        assert calls["n"] == 2
        assert first.cached is False and second.cached is False
        assert second.data == {"query": {"n": 2}}
        assert client.cache.stats("prts")["entries"] == 0
        assert client.cache.peek(cache_key("prts", "api", PRTS_ASK)) is None
    finally:
        await client.aclose()


@async_test
async def test_cache_false_ignores_fresh_entry() -> None:
    """已有新鲜缓存时也不读、不覆盖：下一次默认调用仍拿到旧值。"""
    handler, calls = counting_handler()
    client, _ = make_client(handler)
    key = cache_key("prts", "api", PRTS_ASK)
    try:
        first = await client.get_json(
            "prts", "api", PRTS_ASK, cache_level="live"
        )
        direct = await client.get_json(
            "prts", "api", PRTS_ASK, cache_level="live", cache=False
        )
        assert direct.cached is False
        assert direct.data == {"query": {"n": 2}}
        assert calls["n"] == 2
        entry = client.cache.peek(key)
        assert entry is not None and entry.value == first.data
        cached = await client.get_json(
            "prts", "api", PRTS_ASK, cache_level="live"
        )
        assert cached.cached is True
        assert cached.data == first.data
        assert calls["n"] == 2
    finally:
        await client.aclose()


@async_test
async def test_cache_false_does_not_fall_back_to_stale() -> None:
    """失败时不回退旧值；同样的旧值在默认 cache=True 下仍会回退。"""
    calls = {"n": 0}

    def handler(request: httpx.Request) -> httpx.Response:
        calls["n"] += 1
        if calls["n"] == 1:
            return httpx.Response(200, json={"query": {"n": 1}})
        raise httpx.ConnectError("down", request=request)

    client, clock = make_client(handler)
    try:
        await client.get_json("prts", "api", PRTS_ASK, cache_level="live")
        clock.now += 400  # 过期，但还在 24 小时回退窗口内
        with pytest.raises(AppError) as info:
            await client.get_json(
                "prts", "api", PRTS_ASK, cache_level="live", cache=False
            )
        assert info.value.code is ErrorCode.EXTERNAL_SOURCE_UNAVAILABLE
        # 连接错误重试一次，所以失败路径是两次请求。
        assert calls["n"] == 3

        # 旧值还在：默认路径同样失败时会回退，证明 cache=False 只是没去读它。
        fallback = await client.get_json(
            "prts", "api", PRTS_ASK, cache_level="live"
        )
        assert fallback.stale is True
        assert fallback.cached is True
        assert fallback.data == {"query": {"n": 1}}
        assert calls["n"] == 5
    finally:
        await client.aclose()


@async_test
async def test_cache_false_still_merges_concurrent_requests() -> None:
    """`cache=False` 只关缓存，同键并发仍合并成一次请求。"""
    calls = {"n": 0}

    class SlowTransport(httpx.AsyncBaseTransport):
        async def handle_async_request(self, request: httpx.Request) -> httpx.Response:
            calls["n"] += 1
            await asyncio.sleep(0.01)
            return httpx.Response(200, json={"query": {"n": calls["n"]}})

    client, _ = make_client(SlowTransport())
    try:
        results = await asyncio.gather(
            *(
                client.get_json("prts", "api", PRTS_ASK, cache=False)
                for _ in range(5)
            )
        )
    finally:
        await client.aclose()
    assert calls["n"] == 1
    assert {result.data["query"]["n"] for result in results} == {1}
    assert client.cache.stats("prts")["entries"] == 0
