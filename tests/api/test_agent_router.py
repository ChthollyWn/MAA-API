"""REST contract for M11 tool invocations, audit lookup, sessions and approvals."""

from __future__ import annotations

import asyncio
from concurrent.futures import ThreadPoolExecutor

from fastapi import FastAPI, Request
from fastapi.testclient import TestClient
import pytest
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker
from sqlalchemy.pool import StaticPool
from sqlmodel import SQLModel

import maa_api.db.models  # noqa: F401
from maa_api.db.models import AgentAudit, AgentIdempotency, AgentSession
from maa_api.agent.confirmation import ConfirmationService
from maa_api.agent.policy import PolicyEngine
from maa_api.agent.registry import ToolDefinition, ToolRegistry, ToolRisk
from maa_api.api.errors import register_exception_handlers
from maa_api.api.deps import get_session
from maa_api.db import session as db_session
from maa_api.db.repositories.agent import AgentIdempotencyRepository
from maa_api.db.repositories.audit import AuditRepository
from maa_api.domain.enums import CallerType
from maa_api.settings import LLMSettings, Settings, set_settings


class Args(SQLModel):
    value: int


def test_agent_rest_contract_includes_sessions_confirmations_and_read_only_messages(monkeypatch):
    async def scenario():
        engine = db_session.make_engine(
            "sqlite+aiosqlite:///:memory:", poolclass=StaticPool
        )
        factory = async_sessionmaker(engine, expire_on_commit=False, class_=AsyncSession)
        async with engine.begin() as connection:
            await connection.run_sync(SQLModel.metadata.create_all)

        invoked = []
        sync_invoked = []

        async def handler(params, _context):
            invoked.append(params.value)
            return {"value": params.value}

        async def safe_handler(params, _context):
            sync_invoked.append(params.value)
            if params.value == 12:
                from maa_api.domain.errors import AppError, ErrorCode

                raise AppError(
                    ErrorCode.CORE_COMMAND_FAILED,
                    "test synchronous tool failure",
                    {"attempt": 1},
                )
            return {"value": params.value}

        registry = ToolRegistry()
        registry.register(
            ToolDefinition(
                name="dangerous_action",
                group="ops",
                risk=ToolRisk.DANGEROUS,
                description="dangerous test action",
                params_model=Args,
                handler=handler,
            )
        )
        registry.register(
            ToolDefinition(
                name="safe_action",
                group="status",
                risk=ToolRisk.SAFE,
                description="safe test action",
                params_model=Args,
                handler=safe_handler,
            )
        )
        service = ConfirmationService(factory, registry, policy=PolicyEngine())
        app = FastAPI()
        register_exception_handlers(app)
        from maa_api.main import request_trace_headers
        from maa_api.api.routers import agent, confirmations

        app.middleware("http")(request_trace_headers)
        app.include_router(agent.router)
        app.include_router(confirmations.router)
        app.state.confirmation_service = service
        app.state.session_factory = factory
        async def get_test_session():
            async with factory() as session:
                yield session

        app.dependency_overrides[get_session] = get_test_session
        set_settings(Settings(llm=LLMSettings(base_url="https://llm.test/v1", api_key="k", model="m")))

        class LockedRuntime:
            async def acquire_session(self, _session_id):
                return False

            def release_session(self, _session_id):
                raise AssertionError("busy turns must not release a lock they did not acquire")

            async def check_capabilities(self, _request):
                return {
                    "model": "m",
                    "tool_calling": True,
                    "vision": False,
                    "ready": True,
                    "status": "vision_degraded",
                    "degraded_reason": "当前模型不支持图片输入；截图和原子屏幕操作已禁用",
                }

        app.state.agent_runtime = LockedRuntime()
        client = TestClient(app, raise_server_exceptions=False)
        client.__enter__()
        try:
            listed = client.get("/api/agent/tools").json()
            assert listed["total"] == 2
            assert listed["items"][0]["name"] == "dangerous_action"
            assert client.get("/api/agent/tools?risk_level=destructive").json()["total"] == 1
            assert client.get("/api/agent/tools?risk_level=none").json()["total"] == 1

            created = client.post("/api/agent/sessions", json={"title": "test"})
            assert created.status_code == 201
            session_id = created.json()["session_id"]
            assert client.get("/api/agent/sessions").json()["total"] == 1
            assert client.get(f"/api/agent/sessions/{session_id}").status_code == 200
            messages = client.get(f"/api/agent/sessions/{session_id}/messages")
            assert messages.status_code == 200 and messages.json()["items"] == []
            second_turn = client.post(
                f"/api/agent/sessions/{session_id}/messages", json={"content": "hi"}
            )
            assert second_turn.status_code == 409
            assert second_turn.json()["error"]["code"] == "AGENT_SESSION_BUSY"
            capabilities = client.post("/api/agent/capabilities/check")
            assert capabilities.status_code == 200
            assert capabilities.json() == {
                "model": "m",
                "tool_calling": True,
                "vision": False,
                "ready": True,
                "status": "vision_degraded",
                "degraded_reason": "当前模型不支持图片输入；截图和原子屏幕操作已禁用",
            }

            accepted = client.post(
                "/api/agent/tools/dangerous_action/invoke",
                json={"arguments": {"value": 8}, "mode": "async"},
                headers={"Idempotency-Key": "invoke-key-1", "X-Request-Id": "trace-123"},
            )
            assert accepted.status_code == 202
            confirmation_id = accepted.json()["confirmation_id"]
            audit_id = accepted.json()["audit_id"]
            async with factory() as db:
                key_record = await AgentIdempotencyRepository(db).get(
                    CallerType.REST, "invoke-key-1"
                )
                assert key_record.audit_id == audit_id
                assert key_record.request_mode == "async"
                assert key_record.response_status == 202
                assert key_record.response_body == accepted.json()
            replayed = client.post(
                "/api/agent/tools/dangerous_action/invoke",
                json={"arguments": {"value": 8}, "mode": "async"},
                headers={"Idempotency-Key": "invoke-key-1"},
            )
            assert replayed.status_code == 202
            assert replayed.json() == accepted.json()
            conflict = client.post(
                "/api/agent/tools/dangerous_action/invoke",
                json={"arguments": {"value": 80}, "mode": "async"},
                headers={"Idempotency-Key": "invoke-key-1"},
            )
            assert conflict.status_code == 409
            assert conflict.json()["error"]["code"] == "IDEMPOTENCY_KEY_CONFLICT"
            whitespace_key = client.post(
                "/api/agent/tools/dangerous_action/invoke",
                json={"arguments": {"value": 10}, "mode": "async"},
                headers={"Idempotency-Key": "   "},
            )
            assert whitespace_key.status_code == 400
            assert whitespace_key.json()["error"]["code"] == "INVALID_PARAMETER"
            concurrent_request = {
                "arguments": {"value": 6},
                "mode": "async",
            }
            def post_same_key():
                return client.post(
                    "/api/agent/tools/dangerous_action/invoke",
                    json=concurrent_request,
                    headers={"Idempotency-Key": "parallel-key"},
                )

            loop = asyncio.get_running_loop()
            with ThreadPoolExecutor(max_workers=2) as executor:
                first, second = await asyncio.gather(
                    loop.run_in_executor(executor, post_same_key),
                    loop.run_in_executor(executor, post_same_key),
                )
            assert first.status_code == second.status_code == 202
            assert first.json()["audit_id"] == second.json()["audit_id"]
            assert first.json() == second.json()
            audit_before_approval = client.get(f"/api/agent/audits/{audit_id}").json()
            assert audit_before_approval["request_id"] == "trace-123"
            detail = client.get(f"/api/confirmations/{confirmation_id}")
            assert detail.status_code == 200
            assert detail.json()["audit_id"] == audit_id

            approved = client.post(
                f"/api/confirmations/{confirmation_id}",
                json={"approved": True},
            )
            assert approved.status_code == 200
            assert approved.json()["status"] == "approved"
            for _ in range(20):
                audit = client.get(f"/api/agent/audits/{audit_id}").json()
                if audit.get("status") == "success":
                    break
                await asyncio.sleep(0.005)
            assert audit["status"] == "success"
            final_confirmation = client.get(f"/api/confirmations/{confirmation_id}").json()
            assert final_confirmation["execution"]["status"] == "success"
            assert final_confirmation["execution"]["audit_id"] == audit_id
            assert '"value": 8' in final_confirmation["execution"]["result"]
            assert invoked == [8]
            assert client.get("/api/agent/audits?caller=rest").json()["total"] == 2

            assert client.delete(f"/api/agent/sessions/{session_id}/atomic-grant").status_code == 204
            assert client.delete(f"/api/agent/sessions/{session_id}").status_code == 204
            assert client.get(f"/api/agent/sessions/{session_id}").status_code == 404

            sync_first = client.post(
                "/api/agent/tools/safe_action/invoke",
                json={"arguments": {"value": 11}, "mode": "sync"},
                headers={"Idempotency-Key": "sync-crash-recovery"},
            )
            assert sync_first.status_code == 200
            assert sync_first.json()["result"] == {"value": 11}
            async with factory() as db:
                sync_record = await AgentIdempotencyRepository(db).get(
                    CallerType.REST, "sync-crash-recovery"
                )
                sync_audit_id = sync_record.audit_id
                sync_record.response_body = None
                await db.commit()
            await service.reset_after_restart()
            sync_retry = client.post(
                "/api/agent/tools/safe_action/invoke",
                json={"arguments": {"value": 11}, "mode": "sync"},
                headers={"Idempotency-Key": "sync-crash-recovery"},
            )
            assert sync_retry.status_code == 200
            assert sync_retry.json() == sync_first.json()
            assert sync_invoked == [11]
            sync_audit = client.get(f"/api/agent/audits/{sync_audit_id}").json()
            assert sync_audit["status"] == "success"

            failed_first = client.post(
                "/api/agent/tools/safe_action/invoke",
                json={"arguments": {"value": 12}, "mode": "sync"},
                headers={"Idempotency-Key": "sync-error-replay"},
            )
            assert failed_first.status_code == 502
            failed_replay = client.post(
                "/api/agent/tools/safe_action/invoke",
                json={"arguments": {"value": 12}, "mode": "sync"},
                headers={"Idempotency-Key": "sync-error-replay"},
            )
            assert failed_replay.status_code == failed_first.status_code
            assert failed_replay.json() == failed_first.json()
            assert sync_invoked == [11, 12]

            original_idempotency_get = AgentIdempotencyRepository.get
            snapshot_write_reads = 0

            async def fail_after_terminal_snapshot(repository, caller, key):
                nonlocal snapshot_write_reads
                if key == "sync-final-snapshot-write-fails":
                    snapshot_write_reads += 1
                    if snapshot_write_reads == 3:
                        raise RuntimeError("injected redundant response write failure")
                return await original_idempotency_get(repository, caller, key)

            monkeypatch.setattr(AgentIdempotencyRepository, "get", fail_after_terminal_snapshot)
            interrupted_response = client.post(
                "/api/agent/tools/safe_action/invoke",
                json={"arguments": {"value": 13}, "mode": "sync"},
                headers={"Idempotency-Key": "sync-final-snapshot-write-fails"},
            )
            assert interrupted_response.status_code == 500
            assert sync_invoked == [11, 12, 13]
            replay_after_error = client.post(
                "/api/agent/tools/safe_action/invoke",
                json={"arguments": {"value": 13}, "mode": "sync"},
                headers={"Idempotency-Key": "sync-final-snapshot-write-fails"},
            )
            assert replay_after_error.status_code == 200
            assert replay_after_error.json()["result"] == {"value": 13}
            assert sync_invoked == [11, 12, 13]
        finally:
            client.__exit__(None, None, None)
            set_settings(None)
            await engine.dispose()

    asyncio.run(scenario())


def test_idempotency_loser_returns_explicit_in_progress_with_linked_audit(monkeypatch):
    async def scenario():
        from fastapi import Response
        from maa_api.api.routers import agent
        engine = db_session.make_engine(
            "sqlite+aiosqlite:///:memory:", poolclass=StaticPool
        )
        factory = async_sessionmaker(engine, expire_on_commit=False, class_=AsyncSession)
        async with engine.begin() as connection:
            await connection.run_sync(SQLModel.metadata.create_all)
        async with factory() as session:
            audit = await AuditRepository(session).create(
                AgentAudit(
                    caller=CallerType.REST,
                    tool_name="dangerous_action",
                    arguments={"value": 4},
                    status="pending",
                    risk_level="destructive",
                )
            )
            await AgentIdempotencyRepository(session).create(
                AgentIdempotency(
                    caller=CallerType.REST,
                    key="in-progress",
                    request_hash="a" * 64,
                    audit_id=audit.id,
                    request_mode="async",
                )
            )
            await session.commit()

        monkeypatch.setattr(agent, "IDEMPOTENCY_WAIT_POLLS", 2)
        monkeypatch.setattr(agent, "IDEMPOTENCY_POLL_SECONDS", 0.001)
        response = Response()
        body = await agent._replay_or_in_progress(
            factory,
            key="in-progress",
            request_hash="a" * 64,
            response=response,
        )
        assert response.status_code == 202
        assert body == {
            "status": "in_progress",
            "audit_id": audit.id,
            "idempotency_key": "in-progress",
            "retry_after_seconds": 1,
        }
        await engine.dispose()

    asyncio.run(scenario())


def test_session_message_endpoint_streams_named_sse_events():
    async def scenario():
        engine = db_session.make_engine(
            "sqlite+aiosqlite:///:memory:", poolclass=StaticPool
        )
        factory = async_sessionmaker(engine, expire_on_commit=False, class_=AsyncSession)
        async with engine.begin() as connection:
            await connection.run_sync(SQLModel.metadata.create_all)
        async with factory() as session:
            stored = AgentSession(id="stream-session", model="m", base_url="https://llm.test/v1")
            session.add(stored)
            await session.commit()

        class StreamingRuntime:
            session_factory = factory

            def __init__(self):
                self.locked = False
                self.received = None

            async def acquire_session(self, _session_id):
                if self.locked:
                    return False
                self.locked = True
                return True

            def release_session(self, _session_id):
                self.locked = False

            async def ensure_configured(self, _request, _session_id):
                return None

            async def stream_message(self, session_id, content, _request):
                self.received = (session_id, content)
                yield 'event: message_start\ndata: {"session_id":"stream-session"}\n\n'
                yield 'event: text_delta\ndata: {"text":"hello"}\n\n'
                yield 'event: message_end\ndata: {"finish_reason":"stop"}\n\n'

        runtime = StreamingRuntime()
        app = FastAPI()
        register_exception_handlers(app)
        from maa_api.api.routers import agent

        app.include_router(agent.router)
        app.state.agent_runtime = runtime
        app.state.session_factory = factory
        set_settings(Settings())
        client = TestClient(app, raise_server_exceptions=False)
        client.__enter__()
        try:
            response = client.post(
                "/api/agent/sessions/stream-session/messages",
                json={"content": "hello"},
            )
            assert response.status_code == 200
            assert response.headers["content-type"].startswith("text/event-stream")
            assert "event: message_start" in response.text
            assert "event: text_delta" in response.text
            assert "event: message_end" in response.text
            assert runtime.received == ("stream-session", "hello")
        finally:
            client.__exit__(None, None, None)
            set_settings(None)
            await engine.dispose()

    asyncio.run(scenario())


def test_session_message_response_releases_lock_if_response_start_send_fails():
    async def scenario():
        engine = db_session.make_engine(
            "sqlite+aiosqlite:///:memory:", poolclass=StaticPool
        )
        factory = async_sessionmaker(engine, expire_on_commit=False, class_=AsyncSession)
        async with engine.begin() as connection:
            await connection.run_sync(SQLModel.metadata.create_all)
        async with factory() as session:
            session.add(
                AgentSession(id="send-fails", model="m", base_url="https://llm.test/v1")
            )
            await session.commit()

        class LockedRuntime:
            session_factory = factory

            def __init__(self):
                self.locked = False
                self.release_count = 0
                self.body_started = False

            async def acquire_session(self, _session_id):
                if self.locked:
                    return False
                self.locked = True
                return True

            def release_session(self, _session_id):
                self.release_count += 1
                self.locked = False

            async def ensure_configured(self, _request, _session_id):
                return None

            async def stream_message(self, _session_id, _content, _request):
                self.body_started = True
                yield 'event: message_start\ndata: {}\n\n'

        runtime = LockedRuntime()
        app = FastAPI()
        app.state.agent_runtime = runtime
        app.state.session_factory = factory
        scope = {
            "type": "http",
            "asgi": {"version": "3.0", "spec_version": "2.4"},
            "http_version": "1.1",
            "method": "POST",
            "scheme": "http",
            "path": "/api/agent/sessions/send-fails/messages",
            "raw_path": b"/api/agent/sessions/send-fails/messages",
            "query_string": b"",
            "headers": [],
            "server": ("testserver", 80),
            "client": ("testclient", 12345),
            "app": app,
        }
        request = Request(scope)
        from maa_api.api.routers.agent import SessionMessageRequest, stream_session_message

        response = await stream_session_message(
            "send-fails", SessionMessageRequest(content="hello"), request
        )

        async def receive():
            await asyncio.Future()

        async def send(message):
            if message["type"] == "http.response.start":
                raise RuntimeError("client disconnected before response body")

        try:
            try:
                await response(scope, receive, send)
            except RuntimeError as exc:
                assert "client disconnected" in str(exc)
            else:
                raise AssertionError("ASGI send failure should escape the response")
            assert runtime.body_started is False
            assert runtime.locked is False
            assert runtime.release_count == 1
        finally:
            await engine.dispose()

    asyncio.run(scenario())


def test_session_message_keeps_lock_until_detached_turn_finishes_after_midstream_disconnect():
    async def scenario():
        engine = db_session.make_engine(
            "sqlite+aiosqlite:///:memory:", poolclass=StaticPool
        )
        factory = async_sessionmaker(engine, expire_on_commit=False, class_=AsyncSession)
        async with engine.begin() as connection:
            await connection.run_sync(SQLModel.metadata.create_all)
        async with factory() as session:
            session.add(
                AgentSession(id="body-disconnect", model="m", base_url="https://llm.test/v1")
            )
            await session.commit()

        class DetachedTurnRuntime:
            session_factory = factory

            def __init__(self):
                self.locked = False
                self.body_started = asyncio.Event()
                self.tool_finished = asyncio.Event()
                self.producer = None
                self.release_count = 0

            async def acquire_session(self, _session_id):
                if self.locked:
                    return False
                self.locked = True
                return True

            def release_session(self, _session_id):
                self.release_count += 1
                self.locked = False

            async def ensure_configured(self, _request, _session_id):
                return None

            async def _complete_dispatched_turn(self):
                await self.body_started.wait()
                await self.tool_finished.wait()
                self.release_session("body-disconnect")

            async def stream_message(self, _session_id, _content, _request):
                self.producer = asyncio.create_task(self._complete_dispatched_turn())
                self.body_started.set()
                yield "event: tool_start\ndata: {}\n\n"
                await self.producer

        runtime = DetachedTurnRuntime()
        app = FastAPI()
        app.state.agent_runtime = runtime
        app.state.session_factory = factory
        scope = {
            "type": "http",
            "asgi": {"version": "3.0", "spec_version": "2.4"},
            "http_version": "1.1",
            "method": "POST",
            "scheme": "http",
            "path": "/api/agent/sessions/body-disconnect/messages",
            "raw_path": b"/api/agent/sessions/body-disconnect/messages",
            "query_string": b"",
            "headers": [],
            "server": ("testserver", 80),
            "client": ("testclient", 12345),
            "app": app,
        }
        request = Request(scope)
        from maa_api.api.routers.agent import SessionMessageRequest, stream_session_message

        response = await stream_session_message(
            "body-disconnect", SessionMessageRequest(content="write"), request
        )

        async def receive():
            await asyncio.Future()

        async def send(message):
            if message["type"] == "http.response.body" and message.get("body"):
                raise RuntimeError("client disconnected after stream started")

        try:
            with pytest.raises(RuntimeError, match="client disconnected"):
                await response(scope, receive, send)
            assert runtime.body_started.is_set()
            assert runtime.locked is True
            assert runtime.release_count == 0
            assert await runtime.acquire_session("body-disconnect") is False
            runtime.tool_finished.set()
            await runtime.producer
            assert runtime.locked is False
            assert runtime.release_count == 1
            assert await runtime.acquire_session("body-disconnect") is True
        finally:
            if runtime.producer is not None and not runtime.producer.done():
                runtime.tool_finished.set()
                await runtime.producer
            await engine.dispose()

    asyncio.run(scenario())


def test_session_message_route_releases_lock_when_configuration_check_is_cancelled():
    async def scenario():
        engine = db_session.make_engine(
            "sqlite+aiosqlite:///:memory:", poolclass=StaticPool
        )
        factory = async_sessionmaker(engine, expire_on_commit=False, class_=AsyncSession)
        async with engine.begin() as connection:
            await connection.run_sync(SQLModel.metadata.create_all)
        async with factory() as session:
            session.add(
                AgentSession(id="cancelled-config", model="m", base_url="https://llm.test/v1")
            )
            await session.commit()

        class LockedRuntime:
            session_factory = factory

            def __init__(self):
                self.locked = False
                self.release_count = 0

            async def acquire_session(self, _session_id):
                self.locked = True
                return True

            def release_session(self, _session_id):
                self.release_count += 1
                self.locked = False

            async def ensure_configured(self, _request, _session_id):
                raise asyncio.CancelledError

        runtime = LockedRuntime()
        app = FastAPI()
        app.state.agent_runtime = runtime
        app.state.session_factory = factory
        scope = {
            "type": "http",
            "asgi": {"version": "3.0"},
            "http_version": "1.1",
            "method": "POST",
            "scheme": "http",
            "path": "/api/agent/sessions/cancelled-config/messages",
            "raw_path": b"/api/agent/sessions/cancelled-config/messages",
            "query_string": b"",
            "headers": [],
            "server": ("testserver", 80),
            "client": ("testclient", 12345),
            "app": app,
        }
        request = Request(scope)
        from maa_api.api.routers.agent import SessionMessageRequest, stream_session_message

        try:
            with pytest.raises(asyncio.CancelledError):
                await stream_session_message(
                    "cancelled-config", SessionMessageRequest(content="hello"), request
                )
            assert runtime.locked is False
            assert runtime.release_count == 1
        finally:
            await engine.dispose()

    asyncio.run(scenario())
