"""system 组路由：健康检查与 auth cookie 的换取／清除（M3-07）。

覆盖 docs/05 §6.1 system 组、§5.2 免鉴权模式、§5.3 豁免清单、§5.4 cookie 换 WS 凭据、
docs/13 §4 登出端点。被测 app 一律在本文件里现搭（``FastAPI()`` + M3-04 的
:func:`~maa_api.api.errors.register_exception_handlers` + 本卡 router），
**不 import ``maa_api.main``**（M3-09 之前它还是旧装配）。

配置走 ``tests/api`` 夹具的 ``tmp_settings``（默认空 token ＝ 免鉴权模式，间接
参数化传 token 即启用鉴权），客户端走 ``make_client``（已进 lifespan 上下文）。
"""

from __future__ import annotations

from collections.abc import Iterator
from datetime import datetime, timezone
from http.cookies import SimpleCookie
from importlib.metadata import PackageNotFoundError

import pytest
from fastapi import FastAPI

import maa_api.api.routers.system as system_module
from maa_api.api import deps
from maa_api.api.deps import COOKIE_NAME
from maa_api.api.errors import register_exception_handlers

TOKEN = "s3cret-token"

HEALTH = "/api/system/health"
COOKIE = "/api/system/auth/cookie"


@pytest.fixture(autouse=True)
def _clean_rate_limiter() -> Iterator[None]:
    """每个用例前后清空进程内鉴权失败计数，避免 401 用例互相污染（M3-06 限流）。"""
    deps.reset_rate_limiter()
    yield
    deps.reset_rate_limiter()


def build_app() -> FastAPI:
    """最小 app：M3-04 的统一错误体处理器 + 本卡的 system router。"""
    app = FastAPI(docs_url=None, redoc_url=None, openapi_url=None)
    register_exception_handlers(app)
    app.include_router(system_module.router)
    return app


def _set_cookie_header(resp) -> str:
    """响应里的 ``Set-Cookie`` 原始串；没有该头时断言失败并带上响应体。"""
    header = resp.headers.get("set-cookie")
    assert header is not None, resp.text
    return header


def _morsel(resp, name: str = COOKIE_NAME) -> SimpleCookie:
    """把 ``Set-Cookie`` 解析成 morsel，便于按属性断言。"""
    jar = SimpleCookie()
    jar.load(_set_cookie_header(resp))
    assert name in jar, resp.headers.get("set-cookie")
    return jar


# ----------------------------------------------------------------------
# 路由声明：前缀写在模块里 + OpenAPI 路径完整
# ----------------------------------------------------------------------


def test_router_declares_prefix_and_tags() -> None:
    """前缀与 tag 都写在模块里（M3-09 只 include_router，不再拼前缀）。"""
    assert system_module.router.prefix == "/api/system"
    assert system_module.router.tags == ["system"]


def test_openapi_paths_are_complete_after_plain_include() -> None:
    """无前缀 include 本模块也要得到完整路径。

    不按 ``{r.path for r in app.routes}`` 断言：fastapi 0.141 的 ``include_router``
    是惰性的，``app.routes`` 里只有没有 ``.path`` 的 ``_IncludedRouter`` 包装对象
    （实测见 docs/ENVIRONMENT.md），有效路径要经 ``app.openapi()`` 取。
    """
    app = FastAPI()
    app.include_router(system_module.router)
    paths = app.openapi()["paths"]
    assert HEALTH in paths
    assert COOKIE in paths
    assert set(paths[COOKIE]) >= {"post", "delete"}


def test_endpoint_metadata_follows_doc_style() -> None:
    """docs/05 §11.2：summary 是 ≤12 字的动宾短语，description 覆盖用途/副作用/错误码。"""
    routes = {(route.path, method): route
              for route in system_module.router.routes
              for method in route.methods}
    assert set(routes) == {(HEALTH, "GET"), (COOKIE, "POST"), (COOKIE, "DELETE")}
    for route in routes.values():
        assert route.summary, route.path
        assert len(route.summary) <= 12, route.summary
        assert route.description

    health = routes[(HEALTH, "GET")]
    assert "免鉴权" in health.description
    assert "auth_enabled" in health.description
    assert "core" in health.description
    assert "device" in health.description
    assert "queue" in health.description

    exchange = routes[(COOKIE, "POST")]
    assert "HttpOnly" in exchange.description
    assert "Set-Cookie" in exchange.description
    assert set(exchange.responses) >= {401, 403}

    clear = routes[(COOKIE, "DELETE")]
    assert "Max-Age=0" in clear.description
    assert set(clear.responses) >= {401, 403}


# ----------------------------------------------------------------------
# GET /api/system/health
# ----------------------------------------------------------------------


@pytest.mark.parametrize("tmp_settings", [TOKEN], indirect=True)
def test_health_is_exempt_and_reports_auth_enabled(tmp_settings, make_client) -> None:
    """配置 token 后不带凭据也应 200，且 auth_enabled 为 true（docs/05 §5.2/§5.3）。"""
    client = make_client(build_app())
    resp = client.get(HEALTH)
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["status"] == "ok"
    assert body["auth_enabled"] is True
    # 免鉴权：带错误 token 也照样 200，不因凭据错误被拒。
    wrong = client.get(HEALTH, headers={"Authorization": "Bearer wrong"})
    assert wrong.status_code == 200
    assert wrong.json()["auth_enabled"] is True


def test_health_reports_auth_disabled_without_token(tmp_settings, make_client) -> None:
    """未配置 token（空串）＝ 免鉴权模式，auth_enabled 为 false（前端据此跳过登录页）。"""
    assert tmp_settings.access_token == ""
    client = make_client(build_app())
    resp = client.get(HEALTH)
    assert resp.status_code == 200
    assert resp.json()["auth_enabled"] is False


def test_health_runtime_fields_are_null_before_lifespan(tmp_settings, make_client) -> None:
    """未进入 lifespan 时仍健康可探测，且未装配的运行态字段显式为 null。"""
    resp = make_client(build_app()).get(HEALTH)
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["core"] is None
    assert body["device"] is None
    assert body["queue"] is None


def test_health_reports_injected_runtime_snapshots_without_queue_entries(
    tmp_settings, make_client
) -> None:
    """读取 app.state 的运行态快照，队列仅暴露计数与暂停状态。"""

    class FakeCoreSupervisor:
        state = "ready"
        pid = 4321
        generation = 3

    class FakeDeviceManager:
        def snapshot(self) -> dict:
            return {
                "core_id": "default",
                "state": "connected",
                "address": "127.0.0.1:5555",
                "uuid": "emulator-5554",
                "resolution": {"width": 1280, "height": 720},
                "last_connected_at": 1_758_000_000.0,
                "retry": {"attempt": 1, "max": 5, "next_at": None},
                "last_error": None,
            }

    class FakeQueueService:
        async def snapshot(self) -> dict:
            return {
                "running": {"id": "private-running-item"},
                "pending": [{"id": "private-pending-item"}],
                "counts": {"pending": 4, "running": 1},
                "paused": True,
            }

    app = build_app()
    app.state.core_supervisor = FakeCoreSupervisor()
    app.state.device_manager = FakeDeviceManager()
    app.state.queue_service = FakeQueueService()

    resp = make_client(app).get(HEALTH)
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["core"] == {"state": "ready", "pid": 4321, "generation": 3}
    assert body["device"] == {
        "core_id": "default",
        "state": "connected",
        "address": "127.0.0.1:5555",
        "uuid": "emulator-5554",
        "resolution": {"width": 1280, "height": 720},
        "last_connected_at": 1_758_000_000.0,
        "retry": {"attempt": 1, "max": 5, "next_at": None},
        "last_error": None,
    }
    assert body["queue"] == {"pending": 4, "running": 1, "paused": True}
    assert "private-running-item" not in resp.text
    assert "private-pending-item" not in resp.text


def test_health_version_is_non_empty(tmp_settings, make_client) -> None:
    """version 始终是非空字符串（读分发元数据，失败回退 0.1.0）。"""
    body = make_client(build_app()).get(HEALTH).json()
    assert isinstance(body["version"], str)
    assert body["version"]


def test_health_version_falls_back_when_metadata_missing(
    tmp_settings, make_client, monkeypatch
) -> None:
    """分发元数据读不到时回退常量，健康检查不能因此 500。"""

    def _raise(_name: str) -> str:
        raise PackageNotFoundError("maa-api")

    monkeypatch.setattr(system_module, "version", _raise)
    resp = make_client(build_app()).get(HEALTH)
    assert resp.status_code == 200, resp.text
    assert resp.json()["version"] == "0.1.0"


def test_health_started_at_null_then_echo(tmp_settings, make_client) -> None:
    """started_at 缺失时为 null（不省略键），app.state 写入后原样回显。"""
    app = build_app()
    client = make_client(app)
    first = client.get(HEALTH).json()
    assert "started_at" in first
    assert first["started_at"] is None

    app.state.started_at = "2025-09-16T12:00:00+00:00"
    assert client.get(HEALTH).json()["started_at"] == "2025-09-16T12:00:00+00:00"

    # M3-09 的 lifespan 若写 datetime 也不能让监控端点 500（字段契约仍是 str | None，
    # datetime 归一成 ISO 8601）。
    app.state.started_at = datetime(2025, 9, 16, 12, 0, tzinfo=timezone.utc)
    echoed = client.get(HEALTH)
    assert echoed.status_code == 200, echoed.text
    assert echoed.json()["started_at"] == "2025-09-16T12:00:00+00:00"


# ----------------------------------------------------------------------
# POST /api/system/auth/cookie：换取
# ----------------------------------------------------------------------


@pytest.mark.parametrize("tmp_settings", [TOKEN], indirect=True)
@pytest.mark.parametrize(
    "auth_kwargs",
    [
        {"headers": {"Authorization": f"Bearer {TOKEN}"}},
        {"headers": {"X-Token": TOKEN}},
        {"params": {"token": TOKEN}},
    ],
)
def test_post_cookie_sets_httponly_cookie(auth_kwargs, tmp_settings, make_client) -> None:
    """鉴权通过 → 204 无体 + Set-Cookie 带 HttpOnly / SameSite=lax / Path=/。"""
    client = make_client(build_app())
    resp = client.post(COOKIE, **auth_kwargs)
    assert resp.status_code == 204, resp.text
    assert resp.content == b""
    assert resp.headers.get("content-type") is None

    raw = _set_cookie_header(resp)
    assert f"{COOKIE_NAME}={TOKEN}" in raw
    assert "HttpOnly" in raw
    assert "SameSite=lax" in raw
    assert "Path=/" in raw
    assert "Secure" not in raw  # HTTPS 判定归 M15

    morsel = _morsel(resp)[COOKIE_NAME]
    assert morsel.value == TOKEN
    assert morsel["httponly"] is True
    assert morsel["path"] == "/"
    assert morsel["samesite"].lower() == "lax"
    assert morsel["secure"] == ""  # 未带 Secure
    assert morsel["max-age"] == ""  # 会话 cookie，不设过期


@pytest.mark.parametrize("tmp_settings", [TOKEN], indirect=True)
def test_post_cookie_with_wrong_token_is_401(tmp_settings, make_client) -> None:
    """token 不匹配 → 401 UNAUTHORIZED 统一错误体，且不下发 cookie。"""
    client = make_client(build_app())
    resp = client.post(COOKIE, headers={"Authorization": "Bearer wrong"})
    assert resp.status_code == 401
    body = resp.json()
    assert body["error"]["code"] == "UNAUTHORIZED"
    assert body["error"]["details"]["channel"] == "bearer"
    assert "set-cookie" not in resp.headers


@pytest.mark.parametrize("tmp_settings", [TOKEN], indirect=True)
def test_post_cookie_without_token_is_401(tmp_settings, make_client) -> None:
    """写方法没有凭据 → 401（require_auth 直接抛，不经路由体）。"""
    resp = make_client(build_app()).post(COOKIE)
    assert resp.status_code == 401
    assert resp.json()["error"]["code"] == "UNAUTHORIZED"
    assert "set-cookie" not in resp.headers


@pytest.mark.parametrize("tmp_settings", [TOKEN], indirect=True)
def test_post_cookie_with_cookie_only_is_403(tmp_settings, make_client) -> None:
    """仅凭 cookie 的写方法 → 403 FORBIDDEN（cookie 渠道收窄，docs/05 §5.1）。"""
    client = make_client(build_app())
    resp = client.post(COOKIE, cookies={COOKIE_NAME: TOKEN})
    assert resp.status_code == 403
    body = resp.json()
    assert body["error"]["code"] == "FORBIDDEN"
    assert body["error"]["details"] == {"channel": "cookie", "method": "POST"}
    assert "set-cookie" not in resp.headers


def test_post_cookie_is_noop_without_auth_enabled(tmp_settings, make_client) -> None:
    """免鉴权模式：无需凭据即 204，但不下发 cookie（没有秘密可发）。"""
    assert tmp_settings.access_token == ""
    client = make_client(build_app())
    resp = client.post(COOKIE)
    assert resp.status_code == 204, resp.text
    assert resp.content == b""
    assert "set-cookie" not in resp.headers


# ----------------------------------------------------------------------
# DELETE /api/system/auth/cookie：清除（docs/13 §4）
# ----------------------------------------------------------------------


@pytest.mark.parametrize("tmp_settings", [TOKEN], indirect=True)
def test_delete_cookie_clears_it(tmp_settings, make_client) -> None:
    """带头部 token → 204 + 清 cookie。

    实测（docs/ENVIRONMENT.md）：``delete_cookie`` 的 ``expires`` 被
    ``http.cookies`` 渲染成**当前时刻**（不是 1970），因此删除语义只认
    ``Max-Age=0``，不要断言 expires 是过去时间。
    """
    client = make_client(build_app())
    resp = client.delete(COOKIE, headers={"Authorization": f"Bearer {TOKEN}"})
    assert resp.status_code == 204, resp.text
    assert resp.content == b""
    assert resp.headers.get("content-type") is None

    raw = _set_cookie_header(resp)
    assert "Max-Age=0" in raw
    assert "Path=/" in raw
    morsel = _morsel(resp)[COOKIE_NAME]
    assert morsel.value == ""
    assert morsel["max-age"] == "0"
    assert morsel["path"] == "/"


@pytest.mark.parametrize("tmp_settings", [TOKEN], indirect=True)
def test_delete_cookie_requires_header_token(tmp_settings, make_client) -> None:
    """写方法：缺凭据 401；仅凭 cookie 403。"""
    client = make_client(build_app())

    missing = client.delete(COOKIE)
    assert missing.status_code == 401
    assert missing.json()["error"]["code"] == "UNAUTHORIZED"
    assert "set-cookie" not in missing.headers

    cookie_only = client.delete(COOKIE, cookies={COOKIE_NAME: TOKEN})
    assert cookie_only.status_code == 403
    assert cookie_only.json()["error"]["code"] == "FORBIDDEN"
    assert "set-cookie" not in cookie_only.headers
