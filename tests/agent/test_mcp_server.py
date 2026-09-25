"""M12 MCP HTTP behavior: request scoped discovery and call isolation."""

from __future__ import annotations

import pytest
from anyio import run as anyio_run
from fastapi import FastAPI
from httpx import ASGITransport, AsyncClient
from pydantic import BaseModel
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker
from sqlalchemy.pool import StaticPool
from sqlmodel import SQLModel

from mcp import ClientSession
from mcp.client.streamable_http import streamablehttp_client

from maa_api.agent.mcp_scopes import (
    ALL_MCP_SCOPES,
    DEFAULT_MCP_SCOPES,
    is_tool_visible,
    normalize_mcp_scopes,
)
from maa_api.agent.mcp_server import (
    MCPHostASGIApp,
    create_mcp_session_manager,
    mount_mcp_http,
    transport_security_settings,
)
from maa_api.agent.registry import ToolContext, ToolDefinition, ToolRegistry, ToolRisk
from maa_api.agent.confirmation import ConfirmationService
from maa_api.agent.tools import build_registry
from maa_api.db.models import AgentAudit
from maa_api.db.repositories.audit import AuditRepository
from maa_api.db import session as db_session
from maa_api.domain.enums import CallerType
from maa_api.domain.errors import AppError, ErrorCode
from maa_api.settings import Settings, get_settings, set_settings


@pytest.fixture(autouse=True)
def _restore_settings():
    previous = get_settings()
    yield
    set_settings(previous)


def test_mcp_scope_defaults_include_confirmation_and_keep_opt_in_groups_hidden() -> None:
    assert DEFAULT_MCP_SCOPES == (
        "status",
        "pipeline",
        "device",
        "schedule",
        "confirmation",
    )
    assert ALL_MCP_SCOPES == DEFAULT_MCP_SCOPES + ("raw", "resource", "ops")
    assert normalize_mcp_scopes(None) == DEFAULT_MCP_SCOPES
    assert is_tool_visible("confirmation", DEFAULT_MCP_SCOPES)
    assert not any(is_tool_visible(group, DEFAULT_MCP_SCOPES) for group in ("raw", "resource", "ops"))


def test_mcp_scopes_select_groups_and_canonicalize_duplicate_entries() -> None:
    assert normalize_mcp_scopes("ops, status,ops,raw") == (
        "status",
        "raw",
        "ops",
    )
    assert normalize_mcp_scopes("resource,pipeline") == ("pipeline", "resource")


@pytest.mark.parametrize("value", ["", "   ", ", ,"])
def test_mcp_scopes_reject_explicit_empty_value(value: str) -> None:
    with pytest.raises(ValueError, match="scope"):
        normalize_mcp_scopes(value)


def test_mcp_scopes_reject_unknown_group() -> None:
    with pytest.raises(ValueError, match="unknown|未知|scope"):
        normalize_mcp_scopes("status,admin")


def test_tool_visibility_requires_selected_group() -> None:
    scopes = normalize_mcp_scopes("raw")

    assert is_tool_visible("raw", scopes)
    assert not is_tool_visible("ops", scopes)


class _Params(BaseModel):
    value: int = 1


class _TrackingSession:
    def __init__(self) -> None:
        self.closed = False

    async def __aenter__(self):
        return self

    async def __aexit__(self, *_exc_info):
        self.closed = True


class _FakeConfirmationService:
    def __init__(self) -> None:
        self.registry = ToolRegistry()
        self.calls: list[tuple[str, ToolContext]] = []
        self.last_session: _TrackingSession | None = None

        async def safe_handler(params: _Params, context: ToolContext):
            return {
                "value": params.value,
                "host_marker": context.request.app.state.host_marker,
                "request_path": context.request.scope["path"],
            }

        async def check_confirmation(_params, context: ToolContext):
            return await context.request.app.state.confirmation_service.check_confirmation(
                "confirmation-1", context=context
            )

        self.registry.register(
            ToolDefinition(
                name="safe_status",
                group="status",
                risk=ToolRisk.SAFE,
                description="safe test tool",
                params_model=_Params,
                handler=safe_handler,
            )
        )
        self.registry.register(
            ToolDefinition(
                name="raw_action",
                group="raw",
                risk=ToolRisk.SAFE,
                description="opt-in test tool",
                params_model=_Params,
                handler=safe_handler,
            )
        )
        self.registry.register(
            ToolDefinition(
                name="ops_action",
                group="ops",
                risk=ToolRisk.SAFE,
                description="opt-in test tool",
                params_model=_Params,
                handler=safe_handler,
            )
        )
        self.registry.register(
            ToolDefinition(
                name="check_confirmation",
                group="confirmation",
                risk=ToolRisk.SAFE,
                description="query confirmation",
                params_model=_Params,
                handler=check_confirmation,
            )
        )
        self.registry.register(
            ToolDefinition(
                name="business_error",
                group="status",
                risk=ToolRisk.SAFE,
                description="produce a business error",
                params_model=_Params,
                handler=safe_handler,
            )
        )

    async def invoke_mcp(self, name: str, arguments: dict, context: ToolContext):
        self.calls.append((name, context))
        self.last_session = context.db_session
        if name == "business_error":
            raise AppError(ErrorCode.INVALID_PARAMETER, "参数不成立", {"field": "value"})
        return await self.registry.execute(name, arguments, context)

    async def check_confirmation(self, _confirmation_id: str, *, context=None):
        assert context is not None
        return {"status": "pending", "id": "confirmation-1"}


def _host_app(service: _FakeConfirmationService) -> FastAPI:
    app = FastAPI()
    app.state.host_marker = "host-fastapi-state"
    app.state.confirmation_service = service
    return app


def _mount_mcp(app: FastAPI) -> tuple[MCPHostASGIApp, object]:
    manager = create_mcp_session_manager(app)
    mount = MCPHostASGIApp(app, manager)
    mount_mcp_http(app, mount)
    return mount, manager


def _httpx_factory(app: FastAPI):
    def factory(*args, **kwargs):
        kwargs["transport"] = ASGITransport(app=app)
        return AsyncClient(*args, **kwargs)

    return factory


async def _client_call(
    app: FastAPI,
    *,
    scopes: str | None = None,
    headers: dict[str, str] | None = None,
    action,
):
    query = "" if scopes is None else f"?scopes={scopes.replace(' ', '%20')}"
    async with streamablehttp_client(
        f"http://localhost:8000/mcp{query}",
        headers=headers,
        httpx_client_factory=_httpx_factory(app),
    ) as (read_stream, write_stream, _session_id):
        async with ClientSession(read_stream, write_stream) as session:
            await session.initialize()
            return await action(session)


def _install_test_settings(monkeypatch, *, access_token: str = "", allowed_hosts=None):
    set_settings(
        Settings(
            access_token=access_token,
            mcp=({"allowed_hosts": allowed_hosts} if allowed_hosts is not None else {}),
        )
    )


def test_sdk_client_gets_request_scoped_tool_lists_and_rejects_hidden_calls(
    monkeypatch,
) -> None:
    async def scenario():
        _install_test_settings(monkeypatch)
        service = _FakeConfirmationService()
        app = _host_app(service)
        _, manager = _mount_mcp(app)
        sessions: list[_TrackingSession] = []

        def session_factory():
            session = _TrackingSession()
            sessions.append(session)
            return session

        monkeypatch.setattr(db_session, "session_factory", session_factory)
        async with manager.run():
            default = await _client_call(
                app,
                headers={"X-Request-Id": "mcp-request-1"},
                action=lambda session: session.list_tools(),
            )
            opted_in = await _client_call(
                app,
                scopes="ops, raw,ops",
                action=lambda session: session.list_tools(),
            )
            hidden = await _client_call(
                app,
                action=lambda session: session.call_tool("raw_action", {"value": 2}),
            )
            success = await _client_call(
                app,
                scopes="status",
                headers={"X-Request-Id": "mcp-request-2"},
                action=lambda session: session.call_tool("safe_status", {"value": 7}),
            )
            business_error = await _client_call(
                app,
                action=lambda session: session.call_tool("business_error", {"value": 0}),
            )
            invalid_scopes = await _client_call(
                app,
                scopes=", ,",
                action=lambda session: session.call_tool("safe_status", {"value": 1}),
            )

        default_names = {tool.name for tool in default.tools}
        assert "check_confirmation" in default_names
        assert "raw_action" not in default_names
        assert "ops_action" not in default_names
        assert {tool.name for tool in opted_in.tools} == {"raw_action", "ops_action"}
        assert hidden.isError is True
        assert hidden.structuredContent["code"] == "TOOL_NOT_FOUND"
        assert success.isError is False
        assert success.structuredContent == {
            "value": 7,
            "host_marker": "host-fastapi-state",
            "request_path": "/",
        }
        assert business_error.isError is True
        assert business_error.structuredContent == {
            "code": "INVALID_PARAMETER",
            "message": "参数不成立",
            "details": {"field": "value"},
        }
        assert invalid_scopes.isError is True
        assert invalid_scopes.structuredContent["code"] == "INVALID_PARAMETER"
        assert [name for name, _context in service.calls] == [
            "safe_status",
            "business_error",
        ]
        name, context = service.calls[0]
        assert name == "safe_status"
        assert context.caller is CallerType.MCP
        assert context.session_id is None
        assert context.request_id == "mcp-request-2"
        assert context.scopes == ("status",)
        assert context.request.app is app
        assert sessions and all(session.closed for session in sessions)
        assert service.last_session.closed is True

    anyio_run(scenario)


def test_default_check_confirmation_tool_is_callable_over_sdk(monkeypatch) -> None:
    async def scenario():
        _install_test_settings(monkeypatch)
        service = _FakeConfirmationService()
        app = _host_app(service)
        _, manager = _mount_mcp(app)
        monkeypatch.setattr(db_session, "session_factory", _TrackingSession)
        async with manager.run():
            result = await _client_call(
                app,
                action=lambda session: session.call_tool("check_confirmation", {}),
            )
        assert result.isError is False
        assert result.structuredContent == {"status": "pending", "id": "confirmation-1"}

    anyio_run(scenario)


def test_sdk_invocation_persists_scope_audit_and_pending_result_remains_queryable(
    monkeypatch,
) -> None:
    async def scenario():
        import maa_api.agent.confirmation as confirmation_module

        _install_test_settings(monkeypatch)
        monkeypatch.setattr(confirmation_module, "MCP_CONFIRMATION_WAIT_SECONDS", 0.01)
        engine = db_session.make_engine(
            "sqlite+aiosqlite:///:memory:", poolclass=StaticPool
        )
        factory = async_sessionmaker(
            engine, expire_on_commit=False, class_=AsyncSession
        )
        async with engine.begin() as connection:
            await connection.run_sync(SQLModel.metadata.create_all)

        registry = build_registry()

        async def operation(_params: _Params, _context: ToolContext):
            return {"ok": True}

        registry.register(
            ToolDefinition(
                name="safe_mcp_audit",
                group="status",
                risk=ToolRisk.SAFE,
                description="MCP audit integration",
                params_model=_Params,
                handler=operation,
            )
        )
        registry.register(
            ToolDefinition(
                name="pending_mcp_action",
                group="ops",
                risk=ToolRisk.DANGEROUS,
                description="pending MCP integration",
                params_model=_Params,
                handler=operation,
            )
        )
        service = ConfirmationService(factory, registry)
        app = _host_app(service)
        _, manager = _mount_mcp(app)
        monkeypatch.setattr(db_session, "session_factory", factory)
        try:
            async with manager.run():
                safe_result = await _client_call(
                    app,
                    scopes="status, status",
                    action=lambda session: session.call_tool("safe_mcp_audit", {"value": 4}),
                )
                pending = await _client_call(
                    app,
                    scopes="ops",
                    action=lambda session: session.call_tool("pending_mcp_action", {"value": 8}),
                )
                queried = await _client_call(
                    app,
                    action=lambda session: session.call_tool(
                        "check_confirmation",
                        {"confirmation_id": pending.structuredContent["confirmation_id"]},
                    ),
                )
            assert safe_result.isError is False
            assert pending.structuredContent["status"] == "awaiting_confirmation"
            assert pending.structuredContent["hint"]
            assert queried.structuredContent["result"]["status"] == "pending"
            async with factory() as session:
                audit_repository = AuditRepository(session)
                safe_audit = await audit_repository.get(
                    safe_result.structuredContent["audit_id"]
                )
                pending_audit = await audit_repository.get(
                    pending.structuredContent["audit_id"]
                )
            assert isinstance(safe_audit, AgentAudit)
            assert safe_audit.scopes == ["status"]
            assert pending_audit.scopes == ["ops"]
        finally:
            await service.close()
            await engine.dispose()

    anyio_run(scenario)


def test_transport_security_uses_host_wildcards_and_origin_patterns(monkeypatch) -> None:
    _install_test_settings(monkeypatch, allowed_hosts=["192.168.10.4"])
    monkeypatch.setattr(
        "maa_api.agent.mcp_server.socket.getaddrinfo",
        lambda *_args, **_kwargs: [
            (2, 1, 6, "", ("192.168.10.9", 0)),
            (2, 1, 6, "", ("8.8.8.8", 0)),
        ],
    )

    security = transport_security_settings()

    assert "localhost:*" in security.allowed_hosts
    assert "127.0.0.1:*" in security.allowed_hosts
    assert "192.168.10.4:*" in security.allowed_hosts
    assert "192.168.10.9:*" in security.allowed_hosts
    assert "8.8.8.8:*" not in security.allowed_hosts
    assert "http://192.168.10.4:*" in security.allowed_origins
    assert "https://192.168.10.4:*" in security.allowed_origins


def test_mcp_auth_requires_bearer_and_never_treats_session_id_as_auth(monkeypatch) -> None:
    async def scenario():
        _install_test_settings(monkeypatch, access_token="secret")
        service = _FakeConfirmationService()
        app = _host_app(service)
        _, manager = _mount_mcp(app)
        async with manager.run():
            async with AsyncClient(
                transport=ASGITransport(app=app), base_url="http://localhost:8000"
            ) as client:
                responses = []
                for headers, path in (
                    ({"Mcp-Session-Id": "session-is-not-auth"}, "/mcp"),
                    ({"X-Token": "secret"}, "/mcp"),
                    ({"Authorization": "Basic secret"}, "/mcp"),
                    ({}, "/mcp?token=secret"),
                ):
                    responses.append(
                        await client.post(
                            path,
                            headers={"content-type": "application/json", **headers},
                            json={"jsonrpc": "2.0", "id": 1, "method": "initialize", "params": {}},
                        )
                    )
                authorized = await client.post(
                    "/mcp",
                    headers={
                        "Authorization": "Bearer secret",
                        "content-type": "application/json",
                        "accept": "application/json, text/event-stream",
                    },
                    json={
                        "jsonrpc": "2.0",
                        "id": 2,
                        "method": "initialize",
                        "params": {
                            "protocolVersion": "2025-06-18",
                            "capabilities": {},
                            "clientInfo": {"name": "mcp-test", "version": "1"},
                        },
                    },
                )
        assert [response.status_code for response in responses] == [401] * 4
        assert all(response.headers.get("www-authenticate") == "Bearer" for response in responses)
        assert authorized.status_code == 200

    anyio_run(scenario)


def test_mcp_host_and_origin_allowlist_accepts_configured_lan_and_rejects_other_hosts(
    monkeypatch,
) -> None:
    async def scenario():
        _install_test_settings(monkeypatch, allowed_hosts=["192.168.10.8"])
        service = _FakeConfirmationService()
        app = _host_app(service)
        _, manager = _mount_mcp(app)
        async with manager.run():
            async with AsyncClient(transport=ASGITransport(app=app)) as client:
                payload = {
                    "jsonrpc": "2.0",
                    "id": 1,
                    "method": "initialize",
                    "params": {
                        "protocolVersion": "2025-06-18",
                        "capabilities": {},
                        "clientInfo": {"name": "mcp-test", "version": "1"},
                    },
                }
                good = await client.post(
                    "http://192.168.10.8:8100/mcp",
                    headers={
                        "Origin": "https://192.168.10.8:4443",
                        "content-type": "application/json",
                        "accept": "application/json, text/event-stream",
                    },
                    json=payload,
                )
                bad_origin = await client.post(
                    "http://192.168.10.8:8100/mcp",
                    headers={
                        "Origin": "https://evil.example.com",
                        "content-type": "application/json",
                        "accept": "application/json, text/event-stream",
                    },
                    json=payload,
                )
                bad_host = await client.post(
                    "http://evil.example.com:8100/mcp",
                    headers={
                        "content-type": "application/json",
                        "accept": "application/json, text/event-stream",
                    },
                    json=payload,
                )
        assert good.status_code == 200
        assert bad_origin.status_code == 400
        assert bad_host.status_code == 421

    anyio_run(scenario)
