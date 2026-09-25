"""MCP Streamable HTTP transport for the host FastAPI application."""

from __future__ import annotations

import ipaddress
import json
import logging
import socket
from collections.abc import Mapping
from typing import Any

from fastapi import FastAPI
from fastapi.responses import JSONResponse, Response
from mcp import types
from mcp.server import Server
from mcp.server.fastmcp.server import StreamableHTTPASGIApp
from mcp.server.streamable_http_manager import StreamableHTTPSessionManager
from mcp.server.transport_security import TransportSecuritySettings
from mcp.shared.exceptions import McpError
from starlette.requests import Request
from starlette.routing import Match, Mount, get_route_path
from starlette.types import Receive, Scope, Send

from maa_api.agent.mcp_scopes import is_tool_visible, normalize_mcp_scopes
from maa_api.agent.registry import ToolContext
from maa_api.api.deps import require_mcp_auth
from maa_api.domain.enums import CallerType
from maa_api.domain.errors import AppError, ErrorCode
from maa_api.settings import get_settings

logger = logging.getLogger(__name__)

DEFAULT_ALLOWED_HOSTS = ("localhost", "127.0.0.1")
_RFC1918_NETWORKS = (
    ipaddress.ip_network("10.0.0.0/8"),
    ipaddress.ip_network("172.16.0.0/12"),
    ipaddress.ip_network("192.168.0.0/16"),
)


def _discover_local_ipv4_hosts() -> list[str]:
    """Discover RFC1918 interface addresses without introducing a dependency."""
    try:
        records = socket.getaddrinfo(
            socket.gethostname(), None, socket.AF_INET, socket.SOCK_STREAM
        )
    except OSError:
        logger.warning("无法发现本机局域网 IPv4 地址；MCP 仍允许 loopback 和显式配置")
        return []

    addresses: set[str] = set()
    for record in records:
        try:
            address = ipaddress.ip_address(record[4][0])
        except (IndexError, ValueError, TypeError):
            continue
        if isinstance(address, ipaddress.IPv4Address) and any(
            address in network for network in _RFC1918_NETWORKS
        ):
            addresses.add(str(address))
    return sorted(addresses)


def transport_security_settings() -> TransportSecuritySettings:
    """Create SDK DNS-rebinding rules from configured and local LAN hosts."""
    configured_hosts = get_settings().mcp.allowed_hosts
    hosts = list(dict.fromkeys((*DEFAULT_ALLOWED_HOSTS, *configured_hosts)))
    hosts.extend(host for host in _discover_local_ipv4_hosts() if host not in hosts)

    allowed_hosts = [f"{host}:*" for host in hosts]
    allowed_origins = [
        origin
        for host in hosts
        for origin in (
            f"http://{host}:*",
            f"https://{host}:*",
            f"http://{host}",
            f"https://{host}",
        )
    ]
    return TransportSecuritySettings(
        enable_dns_rebinding_protection=True,
        allowed_hosts=allowed_hosts,
        allowed_origins=allowed_origins,
    )


def _request_for_current_mcp_message(server: Server[Any, Any]) -> Request:
    request = server.request_context.request
    if not isinstance(request, Request):
        raise AppError(ErrorCode.SERVICE_UNAVAILABLE, "MCP HTTP 请求上下文不可用")
    return request


def _scopes_for_request(request: Request) -> tuple[str, ...]:
    try:
        return normalize_mcp_scopes(request.query_params.get("scopes"))
    except ValueError as exc:
        raise AppError(ErrorCode.INVALID_PARAMETER, str(exc)) from exc


def _tools_for_request(registry: Any, scopes: tuple[str, ...]) -> list[types.Tool]:
    return [
        types.Tool(
            name=definition.name,
            description=definition.description,
            inputSchema=definition.params_model.model_json_schema(
                by_alias=True, mode="validation"
            ),
        )
        for definition in registry.list()
        if is_tool_visible(definition.group, scopes)
    ]


def _call_tool_result(
    structured_content: dict[str, Any], *, is_error: bool = False
) -> types.ServerResult:
    return types.ServerResult(
        types.CallToolResult(
            content=[
                types.TextContent(
                    type="text",
                    text=json.dumps(structured_content, ensure_ascii=False, default=str),
                )
            ],
            structuredContent=structured_content,
            isError=is_error,
        )
    )


def _app_error_result(error: AppError) -> types.ServerResult:
    return _call_tool_result(
        {
            "code": str(error.code),
            "message": error.message,
            "details": error.details,
        },
        is_error=True,
    )


def create_mcp_server(host_app: FastAPI) -> Server[Any, Any]:
    """Create low-level handlers that resolve services from each host request."""
    server: Server[Any, Any] = Server("maa-api", version="2")

    @server.list_tools()
    async def list_tools() -> list[types.Tool]:
        request = _request_for_current_mcp_message(server)
        try:
            scopes = _scopes_for_request(request)
        except AppError as exc:
            raise McpError(
                types.ErrorData(code=types.INVALID_PARAMS, message=exc.message)
            ) from exc
        service = getattr(request.app.state, "confirmation_service", None)
        if service is None:
            raise McpError(
                types.ErrorData(
                    code=types.INTERNAL_ERROR,
                    message="MCP 工具服务尚未启动",
                )
            )
        return _tools_for_request(service.registry, scopes)

    async def call_tool(request_message: types.CallToolRequest) -> types.ServerResult:
        request = _request_for_current_mcp_message(server)
        import maa_api.db.session as db_session
        from maa_api.services.log_hub import current_request_id

        try:
            async with db_session.session_factory() as db:
                scopes = _scopes_for_request(request)
                service = getattr(request.app.state, "confirmation_service", None)
                if service is None:
                    raise AppError(
                        ErrorCode.SERVICE_UNAVAILABLE,
                        "MCP 工具服务尚未启动",
                    )
                registry = service.registry
                name = request_message.params.name
                arguments = request_message.params.arguments or {}
                definition = registry.get(name)
                if not is_tool_visible(definition.group, scopes):
                    raise AppError(ErrorCode.TOOL_NOT_FOUND, f"未知工具：{name}")
                registry.validate(name, arguments)
                context = ToolContext(
                    caller=CallerType.MCP,
                    session_id=None,
                    request_id=(
                        request.scope.get("maa_request_id")
                        or request.headers.get("x-request-id", "").strip()
                        or current_request_id.get()
                    ),
                    request=request,
                    db_session=db,
                    scopes=scopes,
                )
                result = await service.invoke_mcp(name, arguments, context)
            structured = (
                dict(result)
                if isinstance(result, Mapping)
                else {"result": result}
            )
            return _call_tool_result(structured)
        except AppError as exc:
            return _app_error_result(exc)

    # The SDK decorator's shared tool cache is intentionally bypassed for calls:
    # visibility must use the scopes on this request, not a previous tools/list.
    server.request_handlers[types.CallToolRequest] = call_tool
    return server


def create_mcp_session_manager(host_app: FastAPI) -> StreamableHTTPSessionManager:
    """Build a stateful Streamable HTTP manager with Host and Origin checks."""
    return StreamableHTTPSessionManager(
        create_mcp_server(host_app),
        security_settings=transport_security_settings(),
    )


class MCPHostASGIApp:
    """Bridge the mounted MCP endpoint to its FastAPI host state and auth rules."""

    def __init__(
        self,
        host_app: FastAPI,
        session_manager: StreamableHTTPSessionManager | None = None,
    ) -> None:
        self.host_app = host_app
        self.session_manager = session_manager

    def bind_session_manager(
        self, session_manager: StreamableHTTPSessionManager | None
    ) -> None:
        self.session_manager = session_manager

    async def _auth_error_response(
        self, request: Request, error: AppError, scope: Scope, receive: Receive, send: Send
    ) -> None:
        handler = self.host_app.exception_handlers.get(AppError)
        if handler is not None:
            response = await handler(request, error)
        else:
            detail: dict[str, Any] = {
                "code": str(error.code),
                "message": error.message,
            }
            if error.details is not None:
                detail["details"] = error.details
            response = JSONResponse(
                status_code=error.http_status,
                content={"error": detail},
                headers=(
                    {"WWW-Authenticate": "Bearer"}
                    if error.http_status == 401
                    else None
                ),
            )
        await response(scope, receive, send)

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] != "http":
            await Response(status_code=404)(scope, receive, send)
            return

        request = Request(scope, receive)
        if not scope.get("maa_request_id"):
            scope["maa_request_id"] = (
                request.headers.get("x-request-id", "").strip() or None
            )
        try:
            await require_mcp_auth(request)
        except AppError as exc:
            await self._auth_error_response(request, exc, scope, receive, send)
            return

        manager = self.session_manager
        if manager is None:
            await Response("MCP server is not ready", status_code=503)(
                scope, receive, send
            )
            return

        # Starlette's Mount normally assigns scope['app'] to this adapter. Tool
        # handlers must instead see FastAPI's shared state and service objects.
        scope["app"] = self.host_app
        await StreamableHTTPASGIApp(manager)(scope, receive, send)


class _MCPHostMount(Mount):
    """Mount that maps the bare `/mcp` endpoint to the child's `/` path."""

    def matches(self, scope: Scope):
        if scope["type"] in ("http", "websocket") and get_route_path(scope) == self.path:
            root_path = scope.get("root_path", "")
            return Match.FULL, {
                "path_params": dict(scope.get("path_params", {})),
                "app_root_path": scope.get("app_root_path", root_path),
                "root_path": root_path + self.path,
                "endpoint": self.app,
                "path": "/",
                "raw_path": b"/",
            }
        return super().matches(scope)


def mount_mcp_http(host_app: FastAPI, mcp_app: MCPHostASGIApp) -> None:
    """Mount at `/mcp` while ensuring the MCP transport receives path `/`."""
    host_app.router.routes.append(_MCPHostMount("/mcp", app=mcp_app))
