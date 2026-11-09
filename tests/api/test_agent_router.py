"""REST contract for M11 tool invocations, audit lookup and approvals.

v2 会话的四个只读端点（列表、详情、删除、消息分页）已随旧会话查看器删除，
本文件不再覆盖它们；保留的授权撤销端点与工具/审计契约照旧。
"""

from __future__ import annotations

import asyncio
from concurrent.futures import ThreadPoolExecutor

from fastapi import FastAPI
from fastapi.testclient import TestClient
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker
from sqlalchemy.pool import StaticPool
from sqlmodel import SQLModel

import maa_api.db.models  # noqa: F401
from maa_api.db.models import (
    AgentAudit,
    AgentIdempotency,
    AgentRun,
    AgentSession,
    Ticket,
)
from maa_api.agent.confirmation import ConfirmationService
from maa_api.agent.policy import PolicyEngine
from maa_api.agent.registry import ToolDefinition, ToolRegistry, ToolRisk
from maa_api.api.errors import register_exception_handlers
from maa_api.api.deps import get_session
from maa_api.db import session as db_session
from maa_api.db.repositories.agent import (
    AgentIdempotencyRepository,
    AgentSessionRepository,
)
from maa_api.db.repositories.audit import AuditRepository
from maa_api.domain.enums import CallerType
from maa_api.settings import LLMSettings, Settings, set_settings


class Args(SQLModel):
    value: int


def test_agent_rest_contract_includes_tools_confirmations_and_atomic_grant(monkeypatch):
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

        client = TestClient(app, raise_server_exceptions=False)
        client.__enter__()
        try:
            listed = client.get("/api/agent/tools").json()
            assert listed["total"] == 2
            assert listed["items"][0]["name"] == "dangerous_action"
            assert client.get("/api/agent/tools?risk_level=destructive").json()["total"] == 1
            assert client.get("/api/agent/tools?risk_level=none").json()["total"] == 1

            async with factory() as db:
                stored = await AgentSessionRepository(db).create(
                    AgentSession(title="test", model="m", base_url="http://x")
                )
                await db.commit()
            session_id = stored.id

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

            # 授权撤销与删除会话脱钩：撤销原子授权仍在，会话本身不再有删除端点。
            assert client.delete(f"/api/agent/sessions/{session_id}/atomic-grant").status_code == 204

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


def test_removed_v2_session_routes_and_read_only_viewer():
    """M72 + 维护性补丁：四个 v2 只读端点与旧写端点都不再注册。

    只读端点（列表、详情、删除、消息分页）随旧会话查看器一起删除；旧的 v2 写端点
    （消息、中断、能力自检）此前已删。保留的原子/作业授权撤销仍在 OpenAPI 里。
    """

    app = FastAPI()
    register_exception_handlers(app)
    from maa_api.api.routers import agent

    app.include_router(agent.router)
    client = TestClient(app, raise_server_exceptions=False)
    client.__enter__()
    try:
        # 删干净之后没有同路径的其它 method：POST 列表路径直接 404，而不是 405。
        created = client.post("/api/agent/sessions", json={"title": "test"})
        assert created.status_code == 404
        assert created.json()["error"]["code"] == "NOT_FOUND"
        for method, path in (
            ("get", "/api/agent/sessions"),
            ("get", "/api/agent/sessions/x"),
            ("delete", "/api/agent/sessions/x"),
            ("get", "/api/agent/sessions/x/messages"),
        ):
            response = getattr(client, method)(path)
            assert response.status_code == 404, (method, path, response.text)
            assert response.json()["error"]["code"] == "NOT_FOUND"

        interrupted = client.post("/api/agent/sessions/x/interrupt")
        assert interrupted.status_code == 404
        assert interrupted.json()["error"]["code"] == "NOT_FOUND"
        capabilities = client.post("/api/agent/capabilities/check")
        assert capabilities.status_code == 404
        assert capabilities.json()["error"]["code"] == "NOT_FOUND"

        paths = set(app.openapi()["paths"])
        # 维护性补丁删掉的三个只读路径（删除会话复用 {session_id}，不再单列）。
        for path in (
            "/api/agent/sessions",
            "/api/agent/sessions/{session_id}",
            "/api/agent/sessions/{session_id}/messages",
        ):
            assert path not in paths, path
        assert "/api/agent/sessions/{session_id}/interrupt" not in paths
        assert "/api/agent/capabilities/check" not in paths
        # 保留的授权撤销与 v3 会话端点仍在。
        assert {
            "/api/agent/sessions/{session_id}/atomic-grant",
            "/api/agent/sessions/{session_id}/copilot-grant",
        } <= paths
    finally:
        client.__exit__(None, None, None)


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


def test_rest_hidden_tools_are_absent_from_the_catalog_and_not_invocable():
    """M65 §4.2.16：rest_visible=False 的工具不出现在 REST 目录，调用报 TOOL_NOT_FOUND。"""

    async def scenario():
        engine = db_session.make_engine(
            "sqlite+aiosqlite:///:memory:", poolclass=StaticPool
        )
        factory = async_sessionmaker(engine, expire_on_commit=False, class_=AsyncSession)
        async with engine.begin() as connection:
            await connection.run_sync(SQLModel.metadata.create_all)

        invoked = []

        async def hidden_handler(_params, _context):
            invoked.append(True)
            return {"ok": True}

        registry = ToolRegistry()
        registry.register(
            ToolDefinition(
                name="hidden_action",
                group="ops",
                risk=ToolRisk.SAFE,
                description="hidden test action",
                params_model=Args,
                handler=hidden_handler,
                rest_visible=False,
            )
        )
        service = ConfirmationService(factory, registry, policy=PolicyEngine())
        app = FastAPI()
        register_exception_handlers(app)
        from maa_api.main import request_trace_headers
        from maa_api.api.routers import agent

        app.middleware("http")(request_trace_headers)
        app.include_router(agent.router)
        app.state.confirmation_service = service
        app.state.session_factory = factory

        async def get_test_session():
            async with factory() as session:
                yield session

        app.dependency_overrides[get_session] = get_test_session
        set_settings(
            Settings(
                llm=LLMSettings(
                    base_url="https://llm.test/v1", api_key="k", model="m"
                )
            )
        )
        client = TestClient(app, raise_server_exceptions=False)
        client.__enter__()
        try:
            listed = client.get("/api/agent/tools").json()
            assert listed == {"items": [], "total": 0}

            response = client.post(
                "/api/agent/tools/hidden_action/invoke",
                json={"arguments": {"value": 1}, "mode": "sync"},
            )
            assert response.status_code == 404
            assert response.json()["error"]["code"] == "TOOL_NOT_FOUND"
            assert invoked == []
        finally:
            client.__exit__(None, None, None)
            set_settings(None)
            await engine.dispose()

    asyncio.run(scenario())


def test_list_audits_filters_by_ticket_and_returns_origin_columns():
    """M71 §4.3：`GET /api/agent/audits` 按 ticket_id 过滤，并返回归属三列。"""

    async def scenario():
        engine = db_session.make_engine(
            "sqlite+aiosqlite:///:memory:", poolclass=StaticPool
        )
        factory = async_sessionmaker(engine, expire_on_commit=False, class_=AsyncSession)
        async with engine.begin() as connection:
            await connection.run_sync(SQLModel.metadata.create_all)

        session_id, run_id = "audit-session", "audit-run"
        first_ticket, second_ticket = "audit-ticket-1", "audit-ticket-2"
        async with factory() as db:
            db.add(AgentSession(id=session_id, model="test-model"))
            db.add(Ticket(id=first_ticket, title="日常", kind="daily_ops", origin="user"))
            db.add(Ticket(id=second_ticket, title="刷本", kind="daily_ops", origin="user"))
            await db.commit()
            db.add(
                AgentRun(
                    id=run_id,
                    root_run_id=run_id,
                    session_id=session_id,
                    agent_name="daily_ops",
                    kind="worker_turn",
                    boot_id="boot-test",
                )
            )
            await db.commit()
            audits = AuditRepository(db)
            await audits.create(
                AgentAudit(
                    caller=CallerType.INTERNAL,
                    tool_name="click",
                    status="success",
                    run_id=run_id,
                    ticket_id=first_ticket,
                    agent_name="daily_ops",
                )
            )
            await audits.create(
                AgentAudit(
                    caller=CallerType.REST,
                    tool_name="get_status",
                    status="success",
                    ticket_id=second_ticket,
                    agent_name="daily_ops",
                )
            )
            await audits.create(
                AgentAudit(
                    caller=CallerType.REST, tool_name="get_version", status="success"
                )
            )
            await db.commit()

        app = FastAPI()
        register_exception_handlers(app)
        from maa_api.api.routers import agent

        app.include_router(agent.router)

        async def get_test_session():
            async with factory() as session:
                yield session

        app.dependency_overrides[get_session] = get_test_session
        set_settings(Settings())
        client = TestClient(app, raise_server_exceptions=False)
        client.__enter__()
        try:
            filtered = client.get(
                "/api/agent/audits", params={"ticket_id": first_ticket}
            )
            assert filtered.status_code == 200, filtered.text
            payload = filtered.json()
            assert payload["total"] == 1
            item = payload["items"][0]
            assert (item["run_id"], item["ticket_id"], item["agent_name"]) == (
                run_id,
                first_ticket,
                "daily_ops",
            )

            second = client.get(
                "/api/agent/audits", params={"ticket_id": second_ticket}
            ).json()
            assert second["total"] == 1
            assert second["items"][0]["ticket_id"] == second_ticket
            assert second["items"][0]["run_id"] is None

            everything = client.get("/api/agent/audits").json()
            assert everything["total"] == 3
            for audit in everything["items"]:
                assert {"run_id", "ticket_id", "agent_name"} <= set(audit)
            nulls = [
                audit for audit in everything["items"] if audit["ticket_id"] is None
            ]
            assert len(nulls) == 1
            assert (nulls[0]["run_id"], nulls[0]["agent_name"]) == (None, None)
        finally:
            client.__exit__(None, None, None)
            set_settings(None)
            await engine.dispose()

    asyncio.run(scenario())
