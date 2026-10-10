"""访问层测试（规格 §3.2–§3.3、§4.9）：白名单、重试、限流、熔断、缓存与状态。

全部用 `httpx.MockTransport`，不打网。
"""

from __future__ import annotations

import asyncio
import json
import time
from typing import Any

import httpx
import pytest

from maa_api.domain.errors import AppError, ErrorCode
from maa_api.services.external.cache import CacheStore, cache_key
from maa_api.services.external.http import ExternalClient, USER_AGENT
from maa_api.services.external.sources import build_url, get_spec, resolve_source
from maa_api.services.external.status import SourceStatusRegistry
from maa_api.settings import Settings, SourcesSettings

PRTS_ASK = {"action": "ask", "query": "[[分类:有活动信息的页面]]", "format": "json", "formatversion": "2"}


def make_settings(**overrides: Any) -> Settings:
    """测试用设置：默认关掉代理、限流放宽，个别用例再覆盖。"""
    sources = {"use_proxy": False, "prts_rate_per_minute": 120}
    sources.update(overrides)
    return Settings(sources=SourcesSettings(**sources))


class FakeClock:
    """可控时钟：单调与墙上时间都由测试推进。"""

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
    handler: Any,
    *,
    clock: FakeClock | None = None,
    settings: Settings | None = None,
    cache: CacheStore | None = None,
    status: SourceStatusRegistry | None = None,
) -> tuple[ExternalClient, FakeClock]:
    clock = clock or FakeClock()
    transport = (
        handler
        if isinstance(handler, httpx.AsyncBaseTransport)
        else httpx.MockTransport(handler)
    )
    client = ExternalClient(
        transport=transport,
        settings_provider=lambda: settings or make_settings(),
        clock=clock.monotonic,
        wall_clock=clock.wall,
        sleep=clock.sleep,
        jitter=lambda: 0.0,
        cache=cache or CacheStore(clock=clock.wall),
        status=status or SourceStatusRegistry(clock=clock.wall),
    )
    return client, clock


def json_handler(payload: Any = None, *, status: int = 200, headers: dict[str, str] | None = None):
    body = json.dumps(payload if payload is not None else {"query": {"ok": True}}).encode()

    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(status, content=body, headers=headers or {})

    return handler


def async_test(func):
    """本仓库不装 pytest-asyncio：异步用例统一用 `asyncio.run` 跑（同 tests/services 的惯例）。"""

    def wrapper(*args, **kwargs):
        return asyncio.run(func(*args, **kwargs))

    wrapper.__name__ = func.__name__
    wrapper.__doc__ = func.__doc__
    return wrapper


# ---------------------------------------------------------------------------
# 白名单
# ---------------------------------------------------------------------------
def test_url_is_built_from_spec() -> None:
    url = build_url("prts", "api", PRTS_ASK)
    assert url.startswith("https://prts.wiki/api.php?")
    assert "formatversion=2" in url


def test_unknown_param_is_rejected() -> None:
    with pytest.raises(AppError) as info:
        build_url("prts", "api", {"url": "https://evil.example/x"})
    assert info.value.code is ErrorCode.EXTERNAL_SOURCE_UNAVAILABLE


def test_unknown_endpoint_is_rejected() -> None:
    with pytest.raises(AppError) as info:
        build_url("prts", "raw", {})
    assert info.value.code is ErrorCode.EXTERNAL_RESPONSE_INVALID


def test_control_characters_are_rejected() -> None:
    with pytest.raises(AppError):
        build_url("prts", "api", {"titles": "a\r\nHost: evil"})


def test_overlong_param_is_rejected() -> None:
    with pytest.raises(AppError):
        build_url("prts", "api", {"query": "x" * 2001})


def test_spec_hosts_and_limits() -> None:
    spec = get_spec("prts")
    assert spec.hosts == ("prts.wiki",)
    assert spec.max_bytes == 2 * 1024 * 1024


# ---------------------------------------------------------------------------
# 请求形状
# ---------------------------------------------------------------------------
@async_test
async def test_request_uses_get_with_user_agent() -> None:
    seen: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        seen.append(request)
        return httpx.Response(200, json={"query": {}})

    client, _ = make_client(handler)
    try:
        await client.get_json("prts", "api", PRTS_ASK)
    finally:
        await client.aclose()
    assert len(seen) == 1
    assert seen[0].method == "GET"
    assert seen[0].headers["User-Agent"] == USER_AGENT
    assert seen[0].url.host == "prts.wiki"
    assert dict(seen[0].url.params) == {
        "action": "ask",
        "query": "[[分类:有活动信息的页面]]",
        "format": "json",
        "formatversion": "2",
    }


@async_test
async def test_proxy_is_used_when_enabled() -> None:
    settings = Settings(
        proxy="http://127.0.0.1:7890",
        sources=SourcesSettings(use_proxy=True, prts_rate_per_minute=120),
    )
    client, _ = make_client(json_handler(), settings=settings)
    try:
        source = resolve_source("prts", settings)
        assert source.proxy == "http://127.0.0.1:7890"
    finally:
        await client.aclose()


@async_test
async def test_source_disabled_is_reported() -> None:
    settings = Settings(sources=SourcesSettings(prts_enabled=False))
    client, _ = make_client(json_handler(), settings=settings)
    try:
        with pytest.raises(AppError) as info:
            await client.get_json("prts", "api", PRTS_ASK)
    finally:
        await client.aclose()
    assert info.value.code is ErrorCode.EXTERNAL_SOURCE_DISABLED


@async_test
async def test_global_switch_disables_everything() -> None:
    settings = Settings(sources=SourcesSettings(enabled=False))
    client, _ = make_client(json_handler(), settings=settings)
    try:
        with pytest.raises(AppError) as info:
            await client.get_json("prts", "api", PRTS_ASK)
    finally:
        await client.aclose()
    assert info.value.code is ErrorCode.EXTERNAL_SOURCE_DISABLED


# ---------------------------------------------------------------------------
# 响应上限与结构
# ---------------------------------------------------------------------------
@async_test
async def test_response_over_limit_is_rejected() -> None:
    big = json.dumps({"query": {"x": "y" * (3 * 1024 * 1024)}}).encode()
    client, _ = make_client(
        lambda request: httpx.Response(200, content=big)
    )
    try:
        with pytest.raises(AppError) as info:
            await client.get_json("prts", "api", PRTS_ASK)
    finally:
        await client.aclose()
    assert info.value.code is ErrorCode.EXTERNAL_RESPONSE_INVALID


@async_test
async def test_non_json_response_is_rejected() -> None:
    client, _ = make_client(lambda request: httpx.Response(200, content=b"<html>nope"))
    try:
        with pytest.raises(AppError) as info:
            await client.get_json("prts", "api", PRTS_ASK)
    finally:
        await client.aclose()
    assert info.value.code is ErrorCode.EXTERNAL_RESPONSE_INVALID


@async_test
async def test_mediawiki_error_body_is_rejected() -> None:
    fixture = json.loads(
        (__import__("pathlib").Path("tests/fixtures/prts/ask_error_too_large.json")).read_text(
            encoding="utf-8"
        )
    )
    client, _ = make_client(json_handler(fixture))
    try:
        with pytest.raises(AppError) as info:
            await client.get_json("prts", "api", PRTS_ASK)
    finally:
        await client.aclose()
    assert info.value.code is ErrorCode.EXTERNAL_RESPONSE_INVALID


# ---------------------------------------------------------------------------
# 重试
# ---------------------------------------------------------------------------
@async_test
async def test_connection_error_retries_once() -> None:
    calls = {"n": 0}

    def handler(request: httpx.Request) -> httpx.Response:
        calls["n"] += 1
        if calls["n"] == 1:
            raise httpx.ConnectError("boom", request=request)
        return httpx.Response(200, json={"query": {}})

    client, clock = make_client(handler)
    try:
        result = await client.get_json("prts", "api", PRTS_ASK)
    finally:
        await client.aclose()
    assert calls["n"] == 2
    assert result.cached is False
    assert clock.slept == [1.0]


@async_test
async def test_404_does_not_retry() -> None:
    calls = {"n": 0}

    def handler(request: httpx.Request) -> httpx.Response:
        calls["n"] += 1
        return httpx.Response(404, json={})

    client, _ = make_client(handler)
    try:
        with pytest.raises(AppError) as info:
            await client.get_json("prts", "api", PRTS_ASK)
    finally:
        await client.aclose()
    assert calls["n"] == 1
    assert info.value.code is ErrorCode.EXTERNAL_SOURCE_UNAVAILABLE


@async_test
async def test_503_retries_then_succeeds() -> None:
    calls = {"n": 0}

    def handler(request: httpx.Request) -> httpx.Response:
        calls["n"] += 1
        if calls["n"] == 1:
            return httpx.Response(503, json={})
        return httpx.Response(200, json={"query": {}})

    client, _ = make_client(handler)
    try:
        await client.get_json("prts", "api", PRTS_ASK)
    finally:
        await client.aclose()
    assert calls["n"] == 2


# ---------------------------------------------------------------------------
# 429 与熔断
# ---------------------------------------------------------------------------
@async_test
async def test_429_sets_cooldown_and_blocks_next_call() -> None:
    calls = {"n": 0}

    def handler(request: httpx.Request) -> httpx.Response:
        calls["n"] += 1
        return httpx.Response(429, headers={"Retry-After": "30"}, json={})

    client, clock = make_client(handler)
    try:
        with pytest.raises(AppError) as first:
            await client.get_json("prts", "api", PRTS_ASK)
        assert first.value.code is ErrorCode.EXTERNAL_SOURCE_RATE_LIMITED
        assert first.value.headers == {"Retry-After": "30"}
        # 冷却期内直接拒绝，不再打站点。
        with pytest.raises(AppError) as second:
            await client.get_json("prts", "api", PRTS_ASK)
        assert second.value.code is ErrorCode.EXTERNAL_SOURCE_RATE_LIMITED
        assert calls["n"] == 1
        clock.now += 31
        with pytest.raises(AppError):
            await client.get_json("prts", "api", PRTS_ASK)
        assert calls["n"] == 2
    finally:
        await client.aclose()


@async_test
async def test_circuit_breaker_after_three_failures() -> None:
    calls = {"n": 0}

    def handler(request: httpx.Request) -> httpx.Response:
        calls["n"] += 1
        return httpx.Response(500, json={})

    client, clock = make_client(handler)
    try:
        for _ in range(3):
            with pytest.raises(AppError):
                await client.get_json("prts", "api", PRTS_ASK)
        assert calls["n"] == 3  # 500 不在重试名单里（只重试连接错误与 502/503/504）
        with pytest.raises(AppError) as info:
            await client.get_json("prts", "api", PRTS_ASK)
        assert info.value.code is ErrorCode.EXTERNAL_SOURCE_RATE_LIMITED
        assert calls["n"] == 3
        snapshot = client.status.snapshot(client.cache.stats)
        assert snapshot[0]["kind"] == "prts"
        assert snapshot[0]["cooldown_seconds"] == 60
    finally:
        await client.aclose()


@async_test
async def test_token_bucket_limits_requests() -> None:
    calls = {"n": 0}

    def handler(request: httpx.Request) -> httpx.Response:
        calls["n"] += 1
        return httpx.Response(200, json={"query": {"n": calls["n"]}})

    client, _ = make_client(handler, settings=make_settings(prts_rate_per_minute=1))
    try:
        await client.get_json("prts", "api", PRTS_ASK)
        # 每分钟一个令牌，第二个键要等 60 秒，超过 5 秒上限 → 限流。
        with pytest.raises(AppError) as info:
            await client.get_json("prts", "api", {**PRTS_ASK, "query": "别的"})
        assert info.value.code is ErrorCode.EXTERNAL_SOURCE_RATE_LIMITED
        assert calls["n"] == 1
    finally:
        await client.aclose()


# ---------------------------------------------------------------------------
# 缓存
# ---------------------------------------------------------------------------
@async_test
async def test_cache_hit_skips_request() -> None:
    calls = {"n": 0}

    def handler(request: httpx.Request) -> httpx.Response:
        calls["n"] += 1
        return httpx.Response(200, json={"query": {"n": calls["n"]}})

    client, clock = make_client(handler)
    try:
        first = await client.get_json("prts", "api", PRTS_ASK, cache_level="live")
        second = await client.get_json("prts", "api", PRTS_ASK, cache_level="live")
        assert calls["n"] == 1
        assert first.cached is False
        assert second.cached is True
        assert second.data == first.data
        clock.now += 301  # live 档 5 分钟过期
        third = await client.get_json("prts", "api", PRTS_ASK, cache_level="live")
        assert calls["n"] == 2
        assert third.cached is False
    finally:
        await client.aclose()


@async_test
async def test_stale_fallback_on_failure() -> None:
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
        fallback = await client.get_json("prts", "api", PRTS_ASK, cache_level="live")
        assert fallback.stale is True
        assert fallback.cached is True
        assert fallback.data == {"query": {"n": 1}}
    finally:
        await client.aclose()


@async_test
async def test_no_stale_fallback_beyond_24_hours() -> None:
    calls = {"n": 0}

    def handler(request: httpx.Request) -> httpx.Response:
        calls["n"] += 1
        if calls["n"] == 1:
            return httpx.Response(200, json={"query": {"n": 1}})
        raise httpx.ConnectError("down", request=request)

    client, clock = make_client(handler)
    try:
        await client.get_json("prts", "api", PRTS_ASK, cache_level="live")
        clock.now += 24 * 3600 + 400
        with pytest.raises(AppError):
            await client.get_json("prts", "api", PRTS_ASK, cache_level="live")
    finally:
        await client.aclose()


@async_test
async def test_single_flight_merges_concurrent_requests() -> None:
    calls = {"n": 0}
    started = asyncio.Event()

    def handler(request: httpx.Request) -> httpx.Response:
        calls["n"] += 1
        return httpx.Response(200, json={"query": {"n": calls["n"]}})

    class SlowTransport(httpx.AsyncBaseTransport):
        async def handle_async_request(self, request: httpx.Request) -> httpx.Response:
            started.set()
            await asyncio.sleep(0.01)
            return handler(request)

    client, _ = make_client(SlowTransport())
    try:
        results = await asyncio.gather(
            *(client.get_json("prts", "api", PRTS_ASK) for _ in range(5))
        )
    finally:
        await client.aclose()
    assert calls["n"] == 1
    assert {result.data["query"]["n"] for result in results} == {1}


def test_cache_clear_by_kind() -> None:
    clock = FakeClock()
    store = CacheStore(clock=clock.wall)
    store.put(cache_key("prts", "api", {"a": "1"}), "prts", {"v": 1}, ttl=60)
    store.put(cache_key("prts", "api", {"a": "2"}), "prts", {"v": 2}, ttl=60)
    assert store.stats("prts")["entries"] == 2
    assert store.clear("prts") == 2
    assert store.stats("prts")["entries"] == 0


def test_cache_lru_eviction_respects_byte_limit() -> None:
    clock = FakeClock()
    store = CacheStore(max_bytes=200, clock=clock.wall)
    store.put("k1", "prts", {"x": "a" * 100}, ttl=60)
    store.put("k2", "prts", {"x": "b" * 100}, ttl=60)
    store.put("k3", "prts", {"x": "c" * 100}, ttl=60)
    assert store.stats()["entries"] == 1
    assert store.stats()["evictions"] >= 2


def test_status_snapshot_shape() -> None:
    clock = FakeClock()
    registry = SourceStatusRegistry(clock=clock.wall)
    registry.record_success("prts", bytes_read=100)
    registry.record_failure("prts", code="EXTERNAL_SOURCE_UNAVAILABLE", message="boom")
    registry.record_failure("prts", code="EXTERNAL_SOURCE_UNAVAILABLE", message="boom")
    registry.record_failure("prts", code="EXTERNAL_SOURCE_UNAVAILABLE", message="boom")
    snapshot = registry.snapshot(lambda kind: {"entries": 0})
    # M26 起登记表里多了企鹅物流、M29 起多了作业站与拆包数据；快照按 SOURCES 的顺序列出。
    assert [item["kind"] for item in snapshot] == [
        "prts",
        "penguin",
        "copilot_site",
        "gamedata",
        "gamedata_github",
    ]
    assert snapshot[0]["consecutive_failures"] == 3
    assert snapshot[0]["cooldown_seconds"] == 60
    assert snapshot[0]["last_error_code"] == "EXTERNAL_SOURCE_UNAVAILABLE"


def test_default_cache_limit_follows_settings() -> None:
    settings = Settings(sources=SourcesSettings(cache_max_mb=8))
    client = ExternalClient(
        settings_provider=lambda: settings,
        transport=json_handler(),
        clock=time.monotonic,
    )
    assert client.cache.max_bytes == 8 * 1024 * 1024
