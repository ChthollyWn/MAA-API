"""企鹅物流客户端的请求形状、主机回退、缓存与结构校验（规格 §6.6、附录 B、附录 D）。

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
from pydantic import ValidationError

from maa_api.domain.errors import AppError, ErrorCode
from maa_api.services.external.http import ExternalClient
from maa_api.services.external.penguin import PenguinClient, PenguinData
from maa_api.services.external.sources import resolve_source
from maa_api.settings import ENV_OVERRIDES, Settings, SourcesSettings

FIXTURES = Path("tests/fixtures/penguin")

#: 四个端点都在 `/PenguinStats/api/v2` 前缀下（2026-10-10 实测：不带前缀的
#: `/stages`、`/items`、`/period` 会拿到前端 SPA 的 HTML，状态码仍是 200）。
MATRIX_PATH = "/PenguinStats/api/v2/result/matrix"
STAGES_PATH = "/PenguinStats/api/v2/stages"
ITEMS_PATH = "/PenguinStats/api/v2/items"
PERIOD_PATH = "/PenguinStats/api/v2/period"

IO_HOST = "penguin-stats.io"
CN_HOST = "penguin-stats.cn"


def async_test(func):
    """本仓库不装 pytest-asyncio：异步用例统一用 `asyncio.run` 跑。"""

    def wrapper(*args, **kwargs):
        return asyncio.run(func(*args, **kwargs))

    wrapper.__name__ = func.__name__
    return wrapper


def fixture(name: str) -> Any:
    return json.loads((FIXTURES / name).read_text(encoding="utf-8"))


async def _no_sleep(seconds: float) -> None:
    """跳过访问层的重试间隔，单测不等真实秒数。"""
    return None


def make_settings(*, host: str = IO_HOST, **overrides: Any) -> Settings:
    sources: dict[str, Any] = {
        "use_proxy": False,
        "penguin_rate_per_minute": 60,
        "penguin_host": host,
    }
    sources.update(overrides)
    return Settings(sources=SourcesSettings(**sources))


class Routes:
    """按路径（必要时按主机）回夹具或错误响应，并记录每一次请求。"""

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

    def hosts(self) -> list[str]:
        return [request.url.host for request in self.requests]


def make_external(routes: Routes, *, settings: Settings | None = None) -> ExternalClient:
    settings = settings if settings is not None else make_settings()
    return ExternalClient(
        transport=httpx.MockTransport(routes.handler),
        settings_provider=lambda: settings,
        sleep=_no_sleep,
    )


def make_penguin(routes: Routes, *, settings: Settings | None = None) -> PenguinClient:
    settings = settings if settings is not None else make_settings()
    external = make_external(routes, settings=settings)
    return PenguinClient(external, settings_provider=lambda: settings)


def ok_json(payload: Any) -> httpx.Response:
    return httpx.Response(200, content=json.dumps(payload).encode("utf-8"))


# ---------------------------------------------------------------------------
# 配置（附录 B：penguin_enabled / penguin_host / penguin_rate_per_minute）
# ---------------------------------------------------------------------------
def test_penguin_settings_defaults_and_env_names() -> None:
    sources = SourcesSettings()

    assert sources.penguin_enabled is True
    assert sources.penguin_host == IO_HOST
    assert sources.penguin_rate_per_minute == 10
    assert ENV_OVERRIDES["sources.penguin_enabled"] == "MAA_SOURCES_PENGUIN_ENABLED"
    assert ENV_OVERRIDES["sources.penguin_host"] == "MAA_SOURCES_PENGUIN_HOST"
    assert ENV_OVERRIDES["sources.penguin_rate_per_minute"] == "MAA_SOURCES_PENGUIN_RATE_PER_MINUTE"


@pytest.mark.parametrize("host", ["penguin-stats.org", "example.com", ""])
def test_penguin_host_only_accepts_the_two_registered_hosts(host: str) -> None:
    with pytest.raises(ValidationError):
        SourcesSettings(penguin_host=host)


@pytest.mark.parametrize("rate", [0, 61])
def test_penguin_rate_is_bounded(rate: int) -> None:
    with pytest.raises(ValidationError):
        SourcesSettings(penguin_rate_per_minute=rate)


def test_resolve_source_follows_penguin_settings() -> None:
    resolved = resolve_source("penguin", make_settings(penguin_rate_per_minute=7))

    assert resolved.kind == "penguin"
    assert resolved.label == "企鹅物流"
    assert resolved.rate_per_minute == 7
    assert resolved.spec.hosts == (IO_HOST, CN_HOST)
    assert dict(resolved.spec.paths) == {
        "matrix": MATRIX_PATH,
        "stages": STAGES_PATH,
        "items": ITEMS_PATH,
        "period": PERIOD_PATH,
    }
    assert resolved.spec.max_bytes == 4 * 1024 * 1024
    assert resolved.spec.read_timeout == 20.0
    assert resolved.spec.concurrency == 2
    assert "CC BY-NC 4.0" in resolved.spec.license


def test_resolve_source_honours_the_penguin_switch() -> None:
    with pytest.raises(AppError) as info:
        resolve_source("penguin", make_settings(penguin_enabled=False))

    assert info.value.code is ErrorCode.EXTERNAL_SOURCE_DISABLED


# ---------------------------------------------------------------------------
# 请求形状
# ---------------------------------------------------------------------------
@async_test
async def test_matrix_request_shape_and_metadata() -> None:
    routes = Routes(**{MATRIX_PATH: fixture("matrix_subset.json")})
    client = make_penguin(routes)

    result = await client.matrix()

    assert isinstance(result, PenguinData)
    assert result.data == fixture("matrix_subset.json")
    request = routes.requests[0]
    assert request.method == "GET"
    assert request.url.host == IO_HOST
    assert request.url.path == MATRIX_PATH
    assert dict(request.url.params) == {"server": "CN"}
    assert result.cached is False
    assert result.stale is False
    assert isinstance(result.fetched_at, datetime)
    assert result.url == f"https://{IO_HOST}{MATRIX_PATH}?server=CN"

    ref = result.source_ref()
    assert ref.kind == "penguin"
    assert ref.label == "企鹅物流"
    assert "CC BY-NC 4.0" in (ref.title or "")
    assert ref.url == "https://penguin-stats.io/"
    assert ref.fetched_at == result.fetched_at
    assert ref.cached is False and ref.stale is False


@async_test
async def test_matrix_include_closed_adds_show_closed_zones() -> None:
    routes = Routes(**{MATRIX_PATH: fixture("matrix_closed_subset.json")})
    client = make_penguin(routes)

    result = await client.matrix(server="JP", include_closed=True)

    assert dict(routes.requests[0].url.params) == {
        "server": "JP",
        "show_closed_zones": "true",
    }
    assert result.data == fixture("matrix_closed_subset.json")


@async_test
async def test_configured_host_is_used_for_every_endpoint() -> None:
    routes = Routes(
        **{
            MATRIX_PATH: fixture("matrix_subset.json"),
            STAGES_PATH: fixture("stages_subset.json"),
            ITEMS_PATH: fixture("items_subset.json"),
            PERIOD_PATH: fixture("period_subset.json"),
        }
    )
    client = make_penguin(routes, settings=make_settings(host=CN_HOST))

    await client.matrix()
    await client.stages()
    await client.items()
    await client.period()

    assert set(routes.hosts()) == {CN_HOST}


@async_test
async def test_stages_items_and_period_paths_and_params() -> None:
    routes = Routes(
        **{
            STAGES_PATH: fixture("stages_subset.json"),
            ITEMS_PATH: fixture("items_subset.json"),
            PERIOD_PATH: fixture("period_subset.json"),
        }
    )
    client = make_penguin(routes)

    stages = await client.stages(server="KR")
    items = await client.items()
    period = await client.period()

    assert stages.data[0]["stageId"] == "main_04-04"
    assert items.data[0]["itemId"] == "2001"
    assert period.data == fixture("period_subset.json")
    assert [(request.url.path, dict(request.url.params)) for request in routes.requests] == [
        (STAGES_PATH, {"server": "KR"}),
        (ITEMS_PATH, {}),
        (PERIOD_PATH, {}),
    ]


@async_test
async def test_unknown_server_is_rejected_before_any_request() -> None:
    routes = Routes(**{MATRIX_PATH: fixture("matrix_subset.json")})
    client = make_penguin(routes)

    with pytest.raises(AppError) as info:
        await client.matrix(server="EN")

    assert info.value.code is ErrorCode.INVALID_PARAMETER
    assert routes.requests == []


@async_test
async def test_whole_matrix_is_fetched_once() -> None:
    routes = Routes(**{MATRIX_PATH: fixture("matrix_subset.json")})
    client = make_penguin(routes)

    first = await client.matrix()
    second = await client.matrix(include_closed=False)

    assert len(routes.requests) == 1
    assert first.cached is False
    assert second.cached is True
    assert second.data == first.data


# ---------------------------------------------------------------------------
# 主机回退（规格 §6.6：失败一次后自动改试另一个）
# ---------------------------------------------------------------------------
@async_test
async def test_host_failure_falls_back_from_io_to_cn() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.host == CN_HOST:
            return ok_json(fixture("matrix_subset.json"))
        return httpx.Response(500, content=b'{"message": "boom"}')

    routes = Routes(**{MATRIX_PATH: handler})
    client = make_penguin(routes)

    result = await client.matrix()

    assert routes.hosts() == [IO_HOST, CN_HOST]
    assert result.url.startswith(f"https://{CN_HOST}/")
    assert result.data == fixture("matrix_subset.json")


@async_test
async def test_host_failure_falls_back_from_cn_to_io() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.host == IO_HOST:
            return ok_json(fixture("stages_subset.json"))
        return httpx.Response(500, content=b'{"message": "boom"}')

    routes = Routes(**{STAGES_PATH: handler})
    client = make_penguin(routes, settings=make_settings(host=CN_HOST))

    result = await client.stages()

    assert routes.hosts() == [CN_HOST, IO_HOST]
    assert result.url.startswith(f"https://{IO_HOST}/")


@async_test
async def test_both_hosts_failing_reports_the_last_error() -> None:
    routes = Routes(**{MATRIX_PATH: httpx.Response(500, content=b"{}")})
    client = make_penguin(routes)

    with pytest.raises(AppError) as info:
        await client.matrix()

    assert info.value.code is ErrorCode.EXTERNAL_SOURCE_UNAVAILABLE
    assert routes.hosts() == [IO_HOST, CN_HOST]


@async_test
async def test_disabled_source_does_not_try_the_second_host() -> None:
    routes = Routes(**{MATRIX_PATH: fixture("matrix_subset.json")})
    client = make_penguin(routes, settings=make_settings(penguin_enabled=False))

    with pytest.raises(AppError) as info:
        await client.matrix()

    assert info.value.code is ErrorCode.EXTERNAL_SOURCE_DISABLED
    assert routes.requests == []


@async_test
async def test_rate_limited_is_not_retried_on_the_other_host() -> None:
    routes = Routes(
        **{MATRIX_PATH: httpx.Response(429, headers={"Retry-After": "30"}, content=b"{}")}
    )
    client = make_penguin(routes)

    with pytest.raises(AppError) as info:
        await client.matrix()

    assert info.value.code is ErrorCode.EXTERNAL_SOURCE_RATE_LIMITED
    assert routes.hosts() == [IO_HOST]


# ---------------------------------------------------------------------------
# 结构校验（EXTERNAL_RESPONSE_INVALID）
# ---------------------------------------------------------------------------
@async_test
async def test_matrix_payload_without_matrix_list_is_invalid() -> None:
    routes = Routes(**{MATRIX_PATH: []})
    client = make_penguin(routes)

    with pytest.raises(AppError) as info:
        await client.matrix()

    assert info.value.code is ErrorCode.EXTERNAL_RESPONSE_INVALID


@async_test
async def test_unparsable_body_falls_back_to_the_other_host() -> None:
    """读不出 JSON 的响应不进缓存，所以值得改试另一个主机。"""

    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.host == CN_HOST:
            return ok_json(fixture("matrix_subset.json"))
        return httpx.Response(200, content=b"<html>not json</html>")

    routes = Routes(**{MATRIX_PATH: handler})
    client = make_penguin(routes)

    result = await client.matrix()

    assert routes.hosts() == [IO_HOST, CN_HOST]
    assert result.data == fixture("matrix_subset.json")


@async_test
async def test_matrix_key_must_be_a_list() -> None:
    routes = Routes(**{MATRIX_PATH: {"matrix": {"stageId": "main_08-13"}}})
    client = make_penguin(routes)

    with pytest.raises(AppError) as info:
        await client.matrix()

    assert info.value.code is ErrorCode.EXTERNAL_RESPONSE_INVALID


@async_test
async def test_list_endpoints_reject_non_list_payloads() -> None:
    for endpoint, path in (
        ("stages", STAGES_PATH),
        ("items", ITEMS_PATH),
        ("period", PERIOD_PATH),
    ):
        routes = Routes(**{path: {"data": []}})
        client = make_penguin(routes)

        with pytest.raises(AppError) as info:
            await getattr(client, endpoint)()

        assert info.value.code is ErrorCode.EXTERNAL_RESPONSE_INVALID


# ---------------------------------------------------------------------------
# ExternalClient.get_json 的 host 参数（唯一的 M25 改动）
# ---------------------------------------------------------------------------
@async_test
async def test_get_json_rejects_a_host_outside_the_source_registry() -> None:
    routes = Routes(**{ITEMS_PATH: fixture("items_subset.json")})
    external = make_external(routes)

    with pytest.raises(AppError) as info:
        await external.get_json("penguin", "items", {}, host="evil.example.com")
    assert info.value.code is ErrorCode.INVALID_PARAMETER
    assert routes.requests == []

    # 主机必须属于该来源：PRTS 的来源表里没有企鹅物流的主机。
    with pytest.raises(AppError) as info:
        await external.get_json("prts", "api", {"action": "query"}, host=IO_HOST)
    assert info.value.code is ErrorCode.INVALID_PARAMETER
    assert routes.requests == []

    await external.aclose()


@async_test
async def test_get_json_with_an_explicit_host_uses_it() -> None:
    routes = Routes(**{ITEMS_PATH: fixture("items_subset.json")})
    external = make_external(routes)

    result = await external.get_json("penguin", "items", {}, host=CN_HOST)

    assert routes.hosts() == [CN_HOST]
    assert result.url.startswith(f"https://{CN_HOST}{ITEMS_PATH}")

    await external.aclose()


@async_test
async def test_get_json_without_host_uses_the_registry_default() -> None:
    routes = Routes(**{ITEMS_PATH: fixture("items_subset.json")})
    external = make_external(routes)

    result = await external.get_json("penguin", "items", {})

    assert routes.hosts() == [IO_HOST]
    assert result.url.startswith(f"https://{IO_HOST}{ITEMS_PATH}")

    await external.aclose()
