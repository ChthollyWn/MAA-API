"""REST contract for M11 tool invocations, audit lookup, sessions and approvals."""

from __future__ import annotations

import asyncio
from concurrent.futures import ThreadPoolExecutor

from fastapi import FastAPI
from fastapi.testclient import TestClient
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker
from sqlalchemy.pool import StaticPool
from sqlmodel import SQLModel

import maa_api.db.models  # noqa: F401
from maa_api.db.models import AgentAudit, AgentIdempotency
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

        async def handler(params, _context):
            invoked.append(params.value)
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
        client = TestClient(app, raise_server_exceptions=False)
        client.__enter__()
        try:
            listed = client.get("/api/agent/tools").json()
            assert listed["total"] == 1
            assert listed["items"][0]["name"] == "dangerous_action"
            assert client.get("/api/agent/tools?risk_level=destructive").json()["total"] == 1
            assert client.get("/api/agent/tools?risk_level=none").json()["total"] == 0

            created = client.post("/api/agent/sessions", json={"title": "test"})
            assert created.status_code == 201
            session_id = created.json()["session_id"]
            assert client.get("/api/agent/sessions").json()["total"] == 1
            assert client.get(f"/api/agent/sessions/{session_id}").status_code == 200
            messages = client.get(f"/api/agent/sessions/{session_id}/messages")
            assert messages.status_code == 200 and messages.json()["items"] == []
            assert client.post(
                f"/api/agent/sessions/{session_id}/messages", json={"content": "hi"}
            ).status_code == 405

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
