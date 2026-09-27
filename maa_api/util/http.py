"""反向代理感知的请求辅助函数（M15，docs/09 §12.4）。

首版 HTTPS 入口是 ``tailscale serve``：它是反向代理，浏览器到它是 HTTPS，而它到
uvicorn 是到 ``127.0.0.1`` 的明文 HTTP。因此「这次请求对用户而言是不是 HTTPS」
只能看代理头，不能看连接本身。

信任边界由 uvicorn 的 ``--forwarded-allow-ips`` 控制（默认只信任回环，见
``maa_api.main.resolve_forwarded_allow_ips``）：只有来自可信代理的请求才会带上
``X-Forwarded-Proto``，直接连上来的客户端伪造这个头不会经过任何代理、也就不会被
uvicorn 当作可信代理头处理。

反过来，**无条件**给 cookie 加 ``Secure`` 同样不行：局域网 HTTP 直连（
``http://192.168.x.x:8002``）下浏览器会拒收带 ``Secure`` 的 cookie，WebSocket 握手
就拿不到凭据。所以判定必须是「按实际协议」，而不是「按部署形态猜」。
"""

from __future__ import annotations

from fastapi import Request

__all__ = ["forwarded_proto", "request_is_https"]

#: 代理链上可能出现的多个协议值，用逗号分隔；取最左边（最靠近客户端）的那个。
_FORWARDED_PROTO_HEADER = "x-forwarded-proto"


def forwarded_proto(request: Request) -> str | None:
    """返回 ``X-Forwarded-Proto`` 的第一个值（小写），没有则 ``None``。"""
    raw = request.headers.get(_FORWARDED_PROTO_HEADER)
    if not raw:
        return None
    first = raw.split(",", 1)[0].strip().lower()
    return first or None


def request_is_https(request: Request) -> bool:
    """用户侧的这次请求是否走 HTTPS。

    优先看代理头；没有代理头时回落到连接本身的 scheme（同机直连、或本地 HTTPS）。
    """
    proto = forwarded_proto(request)
    if proto is not None:
        return proto == "https"
    return request.url.scheme == "https"
