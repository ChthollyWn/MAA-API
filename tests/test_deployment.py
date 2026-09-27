"""M15 部署配套：代理头信任、cookie 的 Secure 判定与浏览器来源白名单。

这三项是 `tailscale serve` 落地的必要条件（docs/09 §12.4）：它把浏览器侧的 HTTPS
终结在代理上，而到 uvicorn 的连接是明文回环 HTTP。任一项缺失都会得到「页面能开但
某一处悄悄坏掉」的状态——cookie 没有 Secure、或 REST 能用而 WebSocket 被拒。
"""

from __future__ import annotations

import pytest
from fastapi import FastAPI, Request, Response
from fastapi.testclient import TestClient

from maa_api.api.ws import _trusted_cookie_origin
from maa_api.main import (
    CORS_ORIGINS,
    effective_cors_origin_regex,
    effective_cors_origins,
    resolve_forwarded_allow_ips,
)
from maa_api.util.http import forwarded_proto, request_is_https


def _probe_app() -> FastAPI:
    app = FastAPI()

    @app.get("/probe")
    def probe(request: Request) -> dict[str, object]:
        return {"https": request_is_https(request), "proto": forwarded_proto(request)}

    @app.get("/cookie")
    def cookie(request: Request, response: Response) -> dict[str, str]:
        from maa_api.api.routers.system import COOKIE_NAME

        response.set_cookie(
            COOKIE_NAME, "secret", path="/", httponly=True, samesite="lax",
            secure=request_is_https(request),
        )
        return {"ok": "1"}

    return app


@pytest.mark.parametrize(
    ("proto", "expected_https"),
    [
        ("https", True),
        ("HTTPS", True),
        ("http", False),
        ("https, http", True),  # 取最靠近客户端的第一个值
        ("http, https", False),
    ],
)
def test_forwarded_proto_drives_https_detection(proto: str, expected_https: bool) -> None:
    client = TestClient(_probe_app())
    body = client.get("/probe", headers={"X-Forwarded-Proto": proto}).json()
    assert body["https"] is expected_https


def test_without_proxy_header_connection_scheme_is_used() -> None:
    client = TestClient(_probe_app())
    body = client.get("/probe").json()
    assert body["proto"] is None
    # TestClient 用 http:// 基址，所以没有代理头时判定为非 HTTPS
    assert body["https"] is False


def test_cookie_gets_secure_behind_tls_terminating_proxy() -> None:
    """代理终结 TLS 时必须带 Secure，否则 cookie 在 HTTPS 站点上失去保护。"""
    client = TestClient(_probe_app())
    response = client.get("/cookie", headers={"X-Forwarded-Proto": "https"})
    assert "secure" in response.headers["set-cookie"].lower()


def test_cookie_has_no_secure_over_plain_lan_http() -> None:
    """局域网 HTTP 直连时不能带 Secure，否则浏览器拒收、WebSocket 握手拿不到凭据。"""
    client = TestClient(_probe_app())
    response = client.get("/cookie")
    assert "secure" not in response.headers["set-cookie"].lower()


@pytest.mark.parametrize(
    ("value", "expected"),
    [
        (None, "127.0.0.1"),
        ("", "127.0.0.1"),
        ("   ", "127.0.0.1"),
        ("127.0.0.1", "127.0.0.1"),
        ("127.0.0.1,10.0.0.1", "127.0.0.1,10.0.0.1"),
    ],
)
def test_forwarded_allow_ips_defaults_to_loopback(value: str | None, expected: str) -> None:
    """默认必须只信任回环：``*`` 允许任何客户端伪造来源地址与协议。"""
    assert resolve_forwarded_allow_ips(value) == expected


def test_cors_origins_append_without_replacing_defaults() -> None:
    merged = effective_cors_origins(["https://host.tailnet.ts.net"])
    assert merged[: len(CORS_ORIGINS)] == CORS_ORIGINS
    assert "https://host.tailnet.ts.net" in merged


def test_cors_origins_are_deduplicated_and_normalized() -> None:
    merged = effective_cors_origins(
        [
            "https://host.tailnet.ts.net/",  # 结尾斜杠要归一
            "https://host.tailnet.ts.net",  # 重复项要折叠
            "  ",  # 空白要忽略
            CORS_ORIGINS[0],  # 与默认项重复要折叠
        ]
    )
    assert merged.count("https://host.tailnet.ts.net") == 1
    assert merged.count(CORS_ORIGINS[0]) == 1
    assert len(merged) == len(CORS_ORIGINS) + 1


def test_cors_regex_keeps_lan_default_when_unset() -> None:
    assert effective_cors_origin_regex("") == effective_cors_origin_regex("   ")
    # 默认正则仍覆盖 RFC1918，保证升级后局域网访问不回退
    assert "192\\.168\\." in effective_cors_origin_regex("")


def test_cors_regex_unions_configured_pattern() -> None:
    combined = effective_cors_origin_regex(r"https://[a-z0-9-]+\.ts\.net")
    assert "192\\.168\\." in combined
    assert "ts\\.net" in combined


# ---------------------------------------------------------------------------
# WebSocket 的 origin 白名单必须与 HTTP CORS 用同一份配置
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("origin", "trusted"),
    [
        (None, False),
        ("", False),
        ("http://localhost:8002", True),
        ("http://192.168.1.50:8002", True),
        ("http://10.1.2.3:8002", True),
        ("https://evil.example.com", False),
        # 带路径/凭据/查询的 origin 一律拒绝：它们不是合法 origin
        ("http://192.168.1.1:8002/path", False),
        ("http://user:pass@192.168.1.1:8002", False),
        ("not a url", False),
    ],
)
def test_ws_origin_defaults(origin: str | None, trusted: bool) -> None:
    assert _trusted_cookie_origin(origin) is trusted


def test_ws_origin_accepts_configured_tailnet_domain(monkeypatch) -> None:
    """tailscale serve 的来源里没有 IP 字面量，只能靠配置放行。"""
    import maa_api.api.ws as ws_module
    from types import SimpleNamespace

    settings = SimpleNamespace(
        deployment=SimpleNamespace(
            cors_origins=["https://host.tailnet.ts.net"],
            cors_origin_regex="",
        )
    )
    monkeypatch.setattr("maa_api.settings.get_settings", lambda: settings)
    assert _trusted_cookie_origin("https://host.tailnet.ts.net") is True
    # 大小写与结尾斜杠的差异不应绕过或误判
    assert _trusted_cookie_origin("https://host.tailnet.ts.net/") is True
    assert _trusted_cookie_origin("https://other.tailnet.ts.net") is False


def test_ws_origin_accepts_configured_regex(monkeypatch) -> None:
    import re
    from types import SimpleNamespace

    settings = SimpleNamespace(
        deployment=SimpleNamespace(
            cors_origins=[],
            cors_origin_regex=r"https://[a-z0-9-]+\.tailnet\.ts\.net",
        )
    )
    monkeypatch.setattr("maa_api.settings.get_settings", lambda: settings)
    assert _trusted_cookie_origin("https://maa-api.tailnet.ts.net") is True
    assert _trusted_cookie_origin("https://maa-api.tailnet.ts.net.evil.com") is False
    assert re.fullmatch(
        settings.deployment.cors_origin_regex, "https://maa-api.tailnet.ts.net"
    )


# ---------------------------------------------------------------------------
# 「有连接」不等于「用户在看」：人工确认的推送按页面可见性判断
# ---------------------------------------------------------------------------


def test_has_visible_client_ignores_backgrounded_sessions() -> None:
    """后台标签页的 WebSocket 往往还活着，但它不该阻止推送。

    只按「有没有连接」判断会导致：用户切走页面 → 确认到达 → 服务端认为用户在
    看而不推送 → 用户什么都收不到 → 确认等到超时自动拒绝。
    """
    from maa_api.api.ws import ClientSession, ConnectionManager

    manager = ConnectionManager()
    hidden = ClientSession(ws=None)  # type: ignore[arg-type]
    hidden.visible = False
    manager._sessions[hidden.id] = hidden
    assert manager.sessions, "会话确实存在"
    assert manager.has_visible_client() is False

    manager._sessions[hidden.id].visible = True
    assert manager.has_visible_client() is True


def test_has_visible_client_is_false_without_sessions() -> None:
    from maa_api.api.ws import ConnectionManager

    assert ConnectionManager().has_visible_client() is False


def test_closed_session_does_not_count_as_visible() -> None:
    from maa_api.api.ws import ClientSession, ConnectionManager

    manager = ConnectionManager()
    session = ClientSession(ws=None)  # type: ignore[arg-type]
    session.visible = True
    session.closed = True
    manager._sessions[session.id] = session
    assert manager.has_visible_client() is False


def test_visibility_message_updates_session_state() -> None:
    """客户端上报 visibilitychange 后，服务端的判断必须跟着变。"""
    import asyncio

    from maa_api.api.ws import ClientSession, _handle_message

    session = ClientSession(ws=None)  # type: ignore[arg-type]
    first_subscribe = [False]

    async def scenario() -> None:
        await _handle_message(
            session, {"type": "visibility", "data": {"visible": False}}, None,
            first_subscribe=first_subscribe,
        )
        assert session.visible is False
        await _handle_message(
            session, {"type": "visibility", "data": {"visible": True}}, None,
            first_subscribe=first_subscribe,
        )
        assert session.visible is True
        # 缺省视为可见：老客户端不发这条消息时行为与之前一致（有连接就不推送）
        await _handle_message(
            session, {"type": "visibility", "data": {}}, None,
            first_subscribe=first_subscribe,
        )
        assert session.visible is True

    asyncio.run(scenario())


def test_confirmation_push_decision_uses_visible_client() -> None:
    """确认流程读的是可见性谓词，而不是「有没有连接」。"""
    import inspect

    from maa_api.agent.confirmation import ConfirmationService

    source = inspect.getsource(ConfirmationService._push_confirmation)
    assert "has_active_client" in source
    # main.py 注入的必须是可见性谓词
    import maa_api.main as main_module

    assert "has_visible_client" in inspect.getsource(main_module)
