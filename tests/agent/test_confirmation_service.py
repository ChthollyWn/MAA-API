"""Persistence-backed M11 confirmation and tool execution lifecycle tests."""

from __future__ import annotations

import asyncio
import json
from datetime import UTC, datetime, timedelta
from types import SimpleNamespace

from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker
from sqlalchemy.pool import StaticPool
from sqlmodel import SQLModel

import maa_api.db.models  # noqa: F401
from maa_api.agent.policy import PolicyEngine
from maa_api.agent.registry import ToolContext, ToolDefinition, ToolRegistry, ToolRisk
from maa_api.db import session as db_session
from maa_api.db.repositories.agent import AgentIdempotencyRepository, AgentSessionRepository
from maa_api.db.repositories.audit import AuditRepository, ConfirmationRepository
from maa_api.db.models import AgentAudit, AgentIdempotency, AgentSession, Confirmation, utcnow
from maa_api.domain.enums import (
    AgentSessionStatus,
    AuditStatus,
    CallerType,
    ConfirmationStatus,
    RiskLevel,
)
from maa_api.agent.confirmation import ConfirmationService


class Params(SQLModel):
    value: int = 2


def _database():
    engine = db_session.make_engine(
        "sqlite+aiosqlite:///:memory:", poolclass=StaticPool
    )
    factory = async_sessionmaker(engine, expire_on_commit=False, class_=AsyncSession)
    return engine, factory


def _request():
    return SimpleNamespace(app=SimpleNamespace(state=SimpleNamespace()))


def test_approved_confirmation_executes_once_and_persists_result_and_audit():
    async def scenario():
        engine, factory = _database()
        async with engine.begin() as connection:
            await connection.run_sync(SQLModel.metadata.create_all)
        events = []
        calls = []

        async def handler(params, _context):
            calls.append(params.value)
            return {"accepted": params.value}

        registry = ToolRegistry()
        registry.register(
            ToolDefinition(
                name="dangerous_action",
                group="ops",
                risk=ToolRisk.DANGEROUS,
                description="test action",
                params_model=Params,
                handler=handler,
            )
        )
        service = ConfirmationService(
            factory,
            registry,
            policy=PolicyEngine(),
            broadcast=lambda kind, data: events.append((kind, data)),
        )
        context = ToolContext(CallerType.REST, None, "request-1", _request(), None)

        accepted = await service.invoke("dangerous_action", {"value": 7}, context, mode="async")
        assert accepted["status"] == "awaiting_confirmation"
        assert (await service.get(accepted["confirmation_id"]))["action"] == "dangerous_action"
        assert [kind for kind, _data in events] == ["confirm_request"]

        await service.resolve(
            accepted["confirmation_id"], approved=True, resolved_by="web", request=_request()
        )
        await service._workers[accepted["confirmation_id"]]
        assert calls == [7]
        from maa_api.domain.errors import AppError, ErrorCode

        with __import__("pytest").raises(AppError) as error:
            await service.resolve(
                accepted["confirmation_id"], approved=True, resolved_by="web", request=_request()
            )
        assert error.value.code == ErrorCode.CONFIRMATION_ALREADY_RESOLVED

        async with factory() as session:
            confirmation = await ConfirmationRepository(session).get(
                accepted["confirmation_id"]
            )
            audit = await AuditRepository(session).get(accepted["audit_id"])
            assert confirmation.status == ConfirmationStatus.APPROVED
            assert confirmation.audit_id == audit.id
            assert audit.status == AuditStatus.SUCCESS
            assert "accepted" in audit.result_summary
        assert events[-1][0] == "confirm_resolved"
        await engine.dispose()

    asyncio.run(scenario())


def test_rejecting_confirmation_never_executes_the_pending_tool():
    async def scenario():
        engine, factory = _database()
        async with engine.begin() as connection:
            await connection.run_sync(SQLModel.metadata.create_all)
        calls = []
        registry = ToolRegistry()
        registry.register(
            ToolDefinition(
                name="dangerous_action",
                group="ops",
                risk=ToolRisk.DANGEROUS,
                description="test action",
                params_model=Params,
                handler=lambda params, _context: calls.append(params.value),
            )
        )
        service = ConfirmationService(factory, registry, policy=PolicyEngine())
        context = ToolContext(CallerType.REST, None, None, _request(), None)
        accepted = await service.invoke("dangerous_action", {"value": 3}, context, mode="async")
        await service.resolve(
            accepted["confirmation_id"], approved=False, resolved_by="web", request=_request()
        )
        await service._workers[accepted["confirmation_id"]]

        assert calls == []
        async with factory() as session:
            audit = await AuditRepository(session).get(accepted["audit_id"])
            assert audit.status == AuditStatus.REJECTED
        await engine.dispose()

    asyncio.run(scenario())


def test_grant_confirmation_persists_bounded_session_authorization_and_runs_first_action():
    async def scenario():
        engine, factory = _database()
        async with engine.begin() as connection:
            await connection.run_sync(SQLModel.metadata.create_all)
        calls = []
        async with factory() as session:
            row = await AgentSessionRepository(session).create(
                AgentSession(model="", status=AgentSessionStatus.ACTIVE)
            )
            await session.commit()
            session_id = row.id

        registry = ToolRegistry()
        registry.register(
            ToolDefinition(
                name="click",
                group="raw",
                risk=ToolRisk.DANGEROUS,
                description="test click",
                params_model=Params,
                handler=lambda params, _context: calls.append(params.value),
            )
        )
        service = ConfirmationService(factory, registry, policy=PolicyEngine())
        async with factory() as policy_session:
            context = ToolContext(
                CallerType.INTERNAL, session_id, None, _request(), policy_session
            )
            accepted = await service.invoke("click", {"value": 4}, context, mode="async")
        assert (await service.get(accepted["confirmation_id"]))["action"] == "grant_atomic_ops"
        await service.resolve(
            accepted["confirmation_id"], approved=True, resolved_by="web", request=_request()
        )
        await service._workers[accepted["confirmation_id"]]

        assert calls == [4]
        async with factory() as session:
            persisted = await AgentSessionRepository(session).get(session_id)
            grant = await ConfirmationRepository(session).get(persisted.atomic_grant_id)
            assert grant.status == ConfirmationStatus.APPROVED
            assert persisted.atomic_grant_expires_at > datetime.now(UTC).replace(tzinfo=None)
            assert (persisted.atomic_grant_expires_at - grant.created_at) <= timedelta(hours=1)
        await engine.dispose()

    asyncio.run(scenario())


def test_existing_session_grant_is_written_to_authorized_by_audit_column():
    async def scenario():
        engine, factory = _database()
        async with engine.begin() as connection:
            await connection.run_sync(SQLModel.metadata.create_all)
        async with factory() as session:
            confirmation = await ConfirmationRepository(session).create(
                Confirmation(
                    id="approved-grant",
                    action="grant_atomic_ops",
                    risk_level=RiskLevel.NONE,
                    reason="批准原子操作",
                    payload={"session_id": "session-authorized", "window_seconds": 900},
                    status=ConfirmationStatus.APPROVED,
                    requested_by=CallerType.INTERNAL,
                    expires_at=utcnow() + timedelta(hours=1),
                )
            )
            await session.commit()
            agent_session = await AgentSessionRepository(session).create(
                AgentSession(
                    id="session-authorized",
                    model="model-test",
                    atomic_grant_id=confirmation.id,
                    atomic_grant_expires_at=utcnow() + timedelta(minutes=15),
                )
            )
            await session.commit()
            session_id = agent_session.id

        calls = []
        registry = ToolRegistry()
        registry.register(
            ToolDefinition(
                name="click",
                group="raw",
                risk=ToolRisk.DANGEROUS,
                description="test click",
                params_model=Params,
                handler=lambda params, _context: calls.append(params.value),
            )
        )
        service = ConfirmationService(factory, registry, policy=PolicyEngine())
        async with factory() as caller_session:
            result = await service.invoke(
                "click",
                {"value": 9},
                ToolContext(
                    CallerType.INTERNAL, session_id, None, _request(), caller_session
                ),
                mode="async",
            )

        assert result["status"] == "accepted"
        await service._workers[str(result["audit_id"])]
        assert calls == [9]
        async with factory() as session:
            audit = await AuditRepository(session).get(result["audit_id"])
            assert audit.authorized_by_id == "approved-grant"
            assert audit.confirmation_id is None
        await engine.dispose()

    asyncio.run(scenario())


def test_startup_expires_pending_fails_approved_orphans_and_clears_session_grants():
    async def scenario():
        engine, factory = _database()
        async with engine.begin() as connection:
            await connection.run_sync(SQLModel.metadata.create_all)
        registry = ToolRegistry()
        service = ConfirmationService(factory, registry, policy=PolicyEngine())
        async with factory() as session:
            grant = await ConfirmationRepository(session).create(
                Confirmation(
                    action="grant_atomic_ops",
                    risk_level=RiskLevel.NONE,
                    reason="grant",
                    payload={"session_id": "", "window_seconds": 900},
                    requested_by=CallerType.INTERNAL,
                    expires_at=datetime.now(UTC).replace(tzinfo=None) + timedelta(minutes=1),
                )
            )
            await session.commit()
            session_row = await AgentSessionRepository(session).create(
                AgentSession(
                    model="",
                    atomic_grant_id=grant.id,
                    atomic_grant_expires_at=datetime.now(UTC).replace(tzinfo=None) + timedelta(minutes=10),
                )
            )
            await session.commit()
            session_id = session_row.id
            await AgentIdempotencyRepository(session).create(
                AgentIdempotency(
                    caller=CallerType.REST,
                    key="unlinked-after-crash",
                    request_hash="e" * 64,
                    audit_id=None,
                )
            )
            await session.commit()
            approved_orphan = await ConfirmationRepository(session).create(
                Confirmation(
                    id="approved-orphan",
                    action="dangerous_action",
                    risk_level=RiskLevel.DESTRUCTIVE,
                    reason="approved but tool not durably complete",
                    payload={"tool_name": "dangerous_action", "arguments": {"value": 6}},
                    status=ConfirmationStatus.APPROVED,
                    requested_by=CallerType.REST,
                    expires_at=datetime.now(UTC).replace(tzinfo=None) + timedelta(minutes=5),
                )
            )
            orphan_audit = await AuditRepository(session).create(
                AgentAudit(
                    caller=CallerType.REST,
                    tool_name="dangerous_action",
                    arguments={"value": 6},
                    status=AuditStatus.PENDING,
                    risk_level=RiskLevel.DESTRUCTIVE,
                    confirmation_id=approved_orphan.id,
                )
            )
            approved_orphan.audit_id = orphan_audit.id
            await session.commit()
            approved_grant = await ConfirmationRepository(session).create(
                Confirmation(
                    id="approved-grant-orphan",
                    action="grant_atomic_ops",
                    risk_level=RiskLevel.NONE,
                    reason="grant was approved before restart",
                    payload={
                        "tool_name": "click",
                        "arguments": {"value": 7},
                        "caller": "internal",
                        "session_id": "grant-orphan-session",
                        "window_seconds": 900,
                    },
                    status=ConfirmationStatus.APPROVED,
                    requested_by=CallerType.INTERNAL,
                    expires_at=datetime.now(UTC).replace(tzinfo=None) + timedelta(minutes=5),
                )
            )
            grant_orphan_audit = await AuditRepository(session).create(
                AgentAudit(
                    caller=CallerType.INTERNAL,
                    tool_name="click",
                    arguments={"value": 7},
                    status=AuditStatus.PENDING,
                    risk_level=RiskLevel.NONE,
                    confirmation_id=approved_grant.id,
                )
            )
            approved_grant.audit_id = grant_orphan_audit.id
            await session.commit()
            await AgentSessionRepository(session).create(
                AgentSession(
                    id="grant-orphan-session",
                    model="model-test",
                    atomic_grant_id=approved_grant.id,
                    atomic_grant_expires_at=datetime.now(UTC).replace(tzinfo=None)
                    + timedelta(minutes=10),
                )
            )
            await session.commit()

        await service.start(scan_interval_seconds=0.05)
        assert service._expiry_task is not None
        await service.close()
        assert service._expiry_task is None
        async with factory() as session:
            pending = await ConfirmationRepository(session).get(grant.id)
            stored_session = await AgentSessionRepository(session).get(session_id)
            stored_orphan = await ConfirmationRepository(session).get(approved_orphan.id)
            stored_orphan_audit = await AuditRepository(session).get(orphan_audit.id)
            stored_grant_orphan = await ConfirmationRepository(session).get(approved_grant.id)
            stored_grant_audit = await AuditRepository(session).get(grant_orphan_audit.id)
            grant_orphan_session = await AgentSessionRepository(session).get(
                "grant-orphan-session"
            )
            assert pending.status == ConfirmationStatus.EXPIRED
            assert stored_session.atomic_grant_id is None
            assert stored_session.atomic_grant_expires_at is None
            assert stored_orphan.status == ConfirmationStatus.APPROVED
            assert stored_orphan_audit.status == AuditStatus.FAILED
            assert stored_orphan_audit.error_code == "SERVICE_UNAVAILABLE"
            assert "不确定" in stored_orphan_audit.result_summary
            assert stored_grant_orphan.status == ConfirmationStatus.APPROVED
            assert stored_grant_audit.status == AuditStatus.FAILED
            assert stored_grant_audit.error_code == "SERVICE_UNAVAILABLE"
            assert grant_orphan_session.atomic_grant_id is None
            assert grant_orphan_session.atomic_grant_expires_at is None
            assert await AgentIdempotencyRepository(session).get(
                CallerType.REST, "unlinked-after-crash"
            ) is None
        await engine.dispose()

    asyncio.run(scenario())


def test_expiry_scan_marks_audit_terminal_and_broadcasts_agent_grant_event():
    async def scenario():
        engine, factory = _database()
        async with engine.begin() as connection:
            await connection.run_sync(SQLModel.metadata.create_all)
        now = datetime.now(UTC).replace(tzinfo=None)
        events = []
        async with factory() as session:
            grant = await ConfirmationRepository(session).create(
                Confirmation(
                    action="grant_atomic_ops",
                    risk_level=RiskLevel.NONE,
                    reason="approved grant",
                    payload={"session_id": "expiring-session", "window_seconds": 900},
                    requested_by=CallerType.INTERNAL,
                    status=ConfirmationStatus.APPROVED,
                    expires_at=now + timedelta(hours=1),
                )
            )
            await session.flush()
            await AgentSessionRepository(session).create(
                AgentSession(
                    id="expiring-session",
                    model="model-test",
                    atomic_grant_id=grant.id,
                    atomic_grant_expires_at=now - timedelta(seconds=1),
                )
            )
            confirmation = await ConfirmationRepository(session).create(
                Confirmation(
                    action="dangerous_action",
                    risk_level=RiskLevel.DESTRUCTIVE,
                    reason="needs approval",
                    payload={"tool_name": "dangerous_action", "arguments": {}},
                    requested_by=CallerType.REST,
                    expires_at=now - timedelta(seconds=1),
                )
            )
            audit = await AuditRepository(session).create(
                AgentAudit(
                    caller=CallerType.REST,
                    tool_name="dangerous_action",
                    arguments={},
                    status=AuditStatus.PENDING,
                    risk_level=RiskLevel.DESTRUCTIVE,
                    confirmation_id=confirmation.id,
                )
            )
            confirmation.audit_id = audit.id
            await session.commit()

        service = ConfirmationService(
            factory,
            ToolRegistry(),
            broadcast=lambda kind, data: events.append((kind, data)),
            clock=lambda: now,
        )
        await service.expire_due()
        async with factory() as session:
            stored_confirmation = await ConfirmationRepository(session).get(confirmation.id)
            stored_audit = await AuditRepository(session).get(audit.id)
            stored_session = await AgentSessionRepository(session).get("expiring-session")
            assert stored_confirmation.status == ConfirmationStatus.EXPIRED
            assert stored_audit.status == AuditStatus.EXPIRED
            assert stored_audit.error_code == "CONFIRMATION_EXPIRED"
            assert stored_session.atomic_grant_id is None

        assert ("confirm_resolved", {
            "confirmation_id": confirmation.id, "resolution": "expired"
        }) in events
        assert ("agent_event", {
            "session_id": "expiring-session",
            "event": "atomic_grant_changed",
            "payload": {"granted": False, "expires_at": None, "grant_id": None},
        }) in events
        await engine.dispose()

    asyncio.run(scenario())


def test_revoking_session_rejects_pending_grant_and_clears_persisted_authorization():
    async def scenario():
        engine, factory = _database()
        async with engine.begin() as connection:
            await connection.run_sync(SQLModel.metadata.create_all)
        async with factory() as session:
            stored = await AgentSessionRepository(session).create(
                AgentSession(model="model-test")
            )
            await session.commit()
            session_id = stored.id
        events = []
        registry = ToolRegistry()
        registry.register(
            ToolDefinition(
                name="click",
                group="raw",
                risk=ToolRisk.DANGEROUS,
                description="test click",
                params_model=Params,
                handler=lambda params, _context: params.value,
            )
        )
        service = ConfirmationService(
            factory,
            registry,
            policy=PolicyEngine(),
            broadcast=lambda kind, data: events.append((kind, data)),
        )
        async with factory() as policy_session:
            accepted = await service.invoke(
                "click",
                {"value": 1},
                ToolContext(
                    CallerType.INTERNAL, session_id, None, _request(), policy_session
                ),
                mode="async",
            )
        await service.revoke_session_grant(session_id)
        await service._workers[accepted["confirmation_id"]]

        detail = await service.get(accepted["confirmation_id"])
        assert detail["status"] == ConfirmationStatus.REJECTED.value
        async with factory() as session:
            audit = await AuditRepository(session).get(accepted["audit_id"])
            stored = await AgentSessionRepository(session).get(session_id)
            assert audit.status == AuditStatus.REJECTED
            assert stored.atomic_grant_id is None
            assert stored.atomic_grant_expires_at is None
        assert ("agent_event", {
            "session_id": session_id,
            "event": "atomic_grant_changed",
            "payload": {"granted": False, "expires_at": None, "grant_id": None},
        }) in events
        await engine.dispose()

    asyncio.run(scenario())


def test_rest_sync_invocation_waits_for_resolution_and_broadcasts_approval_once(monkeypatch):
    async def scenario():
        import maa_api.agent.confirmation as confirmation_module

        monkeypatch.setattr(confirmation_module, "MCP_CONFIRMATION_WAIT_SECONDS", 0.01)
        engine, factory = _database()
        async with engine.begin() as connection:
            await connection.run_sync(SQLModel.metadata.create_all)
        events = []
        calls = []

        async def handler(params, _context):
            calls.append(params.value)
            return {"done": params.value}

        registry = ToolRegistry()
        registry.register(
            ToolDefinition(
                name="dangerous_action",
                group="ops",
                risk=ToolRisk.DANGEROUS,
                description="test action",
                params_model=Params,
                handler=handler,
            )
        )
        service = ConfirmationService(
            factory,
            registry,
            broadcast=lambda kind, data: events.append((kind, data)),
            clock=lambda: datetime(2026, 9, 25, 0, 0, 0),
        )
        context = ToolContext(CallerType.REST, None, "sync-request", _request(), None)
        invoke_task = asyncio.create_task(
            service.invoke("dangerous_action", {"value": 9}, context, mode="sync")
        )

        for _ in range(20):
            async with factory() as session:
                pending = await ConfirmationRepository(session).list_pending()
            if pending:
                break
            await asyncio.sleep(0.001)
        assert pending
        await asyncio.sleep(0.03)
        assert not invoke_task.done(), "REST sync must wait for confirmation past MCP's short wait"

        await service.resolve(pending[0].id, approved=True, resolved_by="web")
        result = await invoke_task
        assert result["status"] == "success"
        assert result["result"] == {"done": 9}
        assert calls == [9]
        resolved = [event for event in events if event[0] == "confirm_resolved"]
        assert len(resolved) == 1
        await engine.dispose()

    asyncio.run(scenario())


def test_mcp_confirmation_helper_returns_pending_after_short_wait(monkeypatch):
    async def scenario():
        import maa_api.agent.confirmation as confirmation_module

        monkeypatch.setattr(confirmation_module, "MCP_CONFIRMATION_WAIT_SECONDS", 0.01)
        engine, factory = _database()
        async with engine.begin() as connection:
            await connection.run_sync(SQLModel.metadata.create_all)
        registry = ToolRegistry()
        registry.register(
            ToolDefinition(
                name="dangerous_action",
                group="ops",
                risk=ToolRisk.DANGEROUS,
                description="test action",
                params_model=Params,
                handler=lambda _params, _context: None,
            )
        )
        service = ConfirmationService(
            factory,
            registry,
            clock=lambda: datetime(2026, 9, 25, 0, 0, 0),
        )
        context = ToolContext(CallerType.MCP, None, None, _request(), None)
        result = await service.invoke_mcp(
            "dangerous_action", {"value": 1}, context
        )
        assert result["status"] == "awaiting_confirmation"
        assert result["hint"]
        assert (await service.get(result["confirmation_id"]))["status"] == "pending"
        await engine.dispose()

    asyncio.run(scenario())


def test_confirmation_service_persists_mcp_scopes_for_immediate_and_pending_audits():
    async def scenario():
        engine, factory = _database()
        async with engine.begin() as connection:
            await connection.run_sync(SQLModel.metadata.create_all)

        registry = ToolRegistry()
        registry.register(
            ToolDefinition(
                name="safe_action",
                group="status",
                risk=ToolRisk.SAFE,
                description="safe test action",
                params_model=Params,
                handler=lambda params, _context: {"value": params.value},
            )
        )
        registry.register(
            ToolDefinition(
                name="dangerous_action",
                group="ops",
                risk=ToolRisk.DANGEROUS,
                description="dangerous test action",
                params_model=Params,
                handler=lambda params, _context: {"value": params.value},
            )
        )
        service = ConfirmationService(factory, registry, policy=PolicyEngine())
        mcp_context = ToolContext(
            CallerType.MCP,
            None,
            "mcp-request",
            _request(),
            None,
            scopes=("status", "ops"),
        )

        immediate = await service.invoke(
            "safe_action", {"value": 1}, mcp_context
        )
        rest = await service.invoke(
            "safe_action",
            {"value": 2},
            ToolContext(CallerType.REST, None, None, _request(), None),
        )
        internal = await service.invoke(
            "safe_action",
            {"value": 3},
            ToolContext(CallerType.INTERNAL, None, None, _request(), None),
        )
        pending = await service.invoke(
            "dangerous_action", {"value": 4}, mcp_context, mode="async"
        )

        async with factory() as session:
            repo = AuditRepository(session)
            immediate_audit = await repo.get(immediate["audit_id"])
            rest_audit = await repo.get(rest["audit_id"])
            internal_audit = await repo.get(internal["audit_id"])
            pending_audit = await repo.get(pending["audit_id"])
            assert immediate_audit.scopes == ["status", "ops"]
            assert pending_audit.scopes == ["status", "ops"]
            assert rest_audit.scopes is None
            assert internal_audit.scopes is None

        await service.close()
        await engine.dispose()

    asyncio.run(scenario())


def test_approved_confirmation_retains_execution_failure_and_resolved_event():
    async def scenario():
        from maa_api.domain.errors import AppError, ErrorCode

        engine, factory = _database()
        async with engine.begin() as connection:
            await connection.run_sync(SQLModel.metadata.create_all)
        events = []
        registry = ToolRegistry()

        async def fail_handler(_params, _context):
            raise AppError(ErrorCode.ADB_COMMAND_FAILED, "点击执行失败")

        registry.register(
            ToolDefinition(
                name="dangerous_action",
                group="ops",
                risk=ToolRisk.DANGEROUS,
                description="test action",
                params_model=Params,
                handler=fail_handler,
            )
        )
        service = ConfirmationService(
            factory,
            registry,
            broadcast=lambda kind, data: events.append((kind, data)),
            clock=lambda: datetime(2026, 9, 25, 0, 0, 0),
        )
        accepted = await service.invoke(
            "dangerous_action",
            {"value": 2},
            ToolContext(CallerType.REST, None, None, _request(), None),
            mode="async",
        )
        await service.resolve(
            accepted["confirmation_id"], approved=True, resolved_by="web"
        )
        await service._workers[accepted["confirmation_id"]]
        detail = await service.get(accepted["confirmation_id"])
        assert detail["execution"] == {
            "status": "failed",
            "result": "点击执行失败",
            "result_ref": None,
            "error_code": "ADB_COMMAND_FAILED",
            "audit_id": accepted["audit_id"],
        }
        resolved = [data for kind, data in events if kind == "confirm_resolved"]
        assert len(resolved) == 1 and resolved[0]["resolution"] == "approved"
        await engine.dispose()

    asyncio.run(scenario())


def test_check_confirmation_never_reexecutes_approved_pending_audit():
    async def scenario():
        engine, factory = _database()
        async with engine.begin() as connection:
            await connection.run_sync(SQLModel.metadata.create_all)
        calls = []
        registry = ToolRegistry()
        registry.register(
            ToolDefinition(
                name="dangerous_action",
                group="ops",
                risk=ToolRisk.DANGEROUS,
                description="test action",
                params_model=Params,
                handler=lambda params, _context: calls.append(params.value) or {"ran": params.value},
            )
        )
        async with factory() as session:
            confirmation = await ConfirmationRepository(session).create(
                Confirmation(
                    id="approved-recovery",
                    action="dangerous_action",
                    risk_level=RiskLevel.DESTRUCTIVE,
                    reason="approved before restart",
                    payload={
                        "tool_name": "dangerous_action",
                        "arguments": {"value": 12},
                        "caller": "mcp",
                        "request_id": "mcp-request-1",
                    },
                    status=ConfirmationStatus.APPROVED,
                    requested_by=CallerType.MCP,
                    resolved_by="web",
                    expires_at=datetime.now(UTC).replace(tzinfo=None) + timedelta(minutes=5),
                )
            )
            audit = await AuditRepository(session).create(
                AgentAudit(
                    caller=CallerType.MCP,
                    tool_name="dangerous_action",
                    arguments={"value": 12},
                    status=AuditStatus.PENDING,
                    risk_level=RiskLevel.DESTRUCTIVE,
                    confirmation_id=confirmation.id,
                )
            )
            confirmation.audit_id = audit.id
            await session.commit()

        service = ConfirmationService(
            factory,
            registry,
            clock=lambda: datetime(2026, 9, 25, 0, 0, 0),
        )
        await service.start(scan_interval_seconds=0.05)
        result = await service.check_confirmation(
            "approved-recovery",
            context=ToolContext(CallerType.MCP, None, "mcp-request-1", _request(), None),
        )
        assert result["execution"]["status"] == "failed"
        assert result["execution"]["error_code"] == "SERVICE_UNAVAILABLE"
        assert calls == []
        await service.close()
        await engine.dispose()

    asyncio.run(scenario())


def test_expiry_cas_loser_does_not_overwrite_or_broadcast_over_concurrent_approval(monkeypatch):
    async def scenario():
        engine, factory = _database()
        async with engine.begin() as connection:
            await connection.run_sync(SQLModel.metadata.create_all)
        async with factory() as session:
            confirmation = await ConfirmationRepository(session).create(
                Confirmation(
                    id="cas-confirmation",
                    action="dangerous_action",
                    risk_level=RiskLevel.DESTRUCTIVE,
                    reason="needs confirmation",
                    payload={"tool_name": "dangerous_action", "arguments": {}},
                    requested_by=CallerType.REST,
                    expires_at=datetime(2026, 9, 25, 0, 0, 0),
                )
            )
            audit = await AuditRepository(session).create(
                AgentAudit(
                    caller=CallerType.REST,
                    tool_name="dangerous_action",
                    arguments={},
                    status=AuditStatus.PENDING,
                    risk_level=RiskLevel.DESTRUCTIVE,
                    confirmation_id=confirmation.id,
                )
            )
            confirmation.audit_id = audit.id
            await session.commit()

        original_resolve = ConfirmationRepository.resolve

        async def race_with_approval(self, confirmation_id, target, **kwargs):
            if target is ConfirmationStatus.EXPIRED:
                changed = await original_resolve(
                    self,
                    confirmation_id,
                    ConfirmationStatus.APPROVED,
                    resolved_by="web",
                )
                await self.session.commit()
                assert changed
                return False
            return await original_resolve(self, confirmation_id, target, **kwargs)

        monkeypatch.setattr(ConfirmationRepository, "resolve", race_with_approval)
        events = []
        service = ConfirmationService(
            factory,
            ToolRegistry(),
            broadcast=lambda kind, data: events.append((kind, data)),
            clock=lambda: datetime(2026, 9, 25, 0, 0, 1),
        )
        from maa_api.domain.errors import AppError, ErrorCode

        try:
            await service.resolve(
                confirmation.id,
                approved=True,
                resolved_by="web",
            )
        except AppError as exc:
            assert exc.code == ErrorCode.CONFIRMATION_ALREADY_RESOLVED
        else:
            raise AssertionError("CAS loser must report the winning terminal state")

        async with factory() as session:
            stored = await ConfirmationRepository(session).get(confirmation.id)
            stored_audit = await AuditRepository(session).get(audit.id)
        assert stored.status == ConfirmationStatus.APPROVED
        assert stored_audit.status == AuditStatus.PENDING
        assert events == []
        await engine.dispose()

    asyncio.run(scenario())


def test_close_terminalizes_interrupted_direct_and_pending_confirmation_audits():
    async def scenario():
        engine, factory = _database()
        async with engine.begin() as connection:
            await connection.run_sync(SQLModel.metadata.create_all)
        started = asyncio.Event()

        async def slow_handler(_params, _context):
            started.set()
            await asyncio.Event().wait()

        registry = ToolRegistry()
        registry.register(
            ToolDefinition(
                name="slow_safe",
                group="ops",
                risk=ToolRisk.SAFE,
                description="waits until service shutdown",
                params_model=Params,
                handler=slow_handler,
            )
        )
        registry.register(
            ToolDefinition(
                name="dangerous_action",
                group="ops",
                risk=ToolRisk.DANGEROUS,
                description="requires confirmation",
                params_model=Params,
                handler=lambda params, _context: params.value,
            )
        )
        service = ConfirmationService(factory, registry)
        direct = await service.invoke(
            "slow_safe",
            {"value": 1},
            ToolContext(CallerType.REST, None, None, _request(), None),
            mode="async",
        )
        pending = await service.invoke(
            "dangerous_action",
            {"value": 2},
            ToolContext(CallerType.REST, None, None, _request(), None),
            mode="async",
        )
        await started.wait()
        await service.close()

        async with factory() as session:
            direct_audit = await AuditRepository(session).get(direct["audit_id"])
        confirmation = await service.get(pending["confirmation_id"])
        assert direct_audit.status == AuditStatus.FAILED
        assert direct_audit.error_code == "SERVICE_UNAVAILABLE"
        assert confirmation["status"] == ConfirmationStatus.EXPIRED.value
        assert confirmation["execution"]["status"] == AuditStatus.EXPIRED.value
        await engine.dispose()

    asyncio.run(scenario())


def test_audit_created_callback_finishes_before_direct_tool_handler_runs():
    async def scenario():
        engine, factory = _database()
        async with engine.begin() as connection:
            await connection.run_sync(SQLModel.metadata.create_all)
        callback_audit_ids = []
        handler_saw_link = []

        async def handler(params, _context):
            handler_saw_link.append(bool(callback_audit_ids))
            return params.value

        registry = ToolRegistry()
        registry.register(
            ToolDefinition(
                name="safe_action",
                group="status",
                risk=ToolRisk.SAFE,
                description="safe test action",
                params_model=Params,
                handler=handler,
            )
        )
        service = ConfirmationService(factory, registry)
        accepted = await service.invoke(
            "safe_action",
            {"value": 3},
            ToolContext(CallerType.REST, None, None, _request(), None),
            mode="async",
            on_audit_created=lambda audit_id: callback_audit_ids.append(audit_id),
        )
        await service._workers[str(accepted["audit_id"])]
        assert callback_audit_ids == [accepted["audit_id"]]
        assert handler_saw_link == [True]
        await engine.dispose()

    asyncio.run(scenario())


def test_startup_recovers_complete_successful_sync_idempotency_result():
    async def scenario():
        engine, factory = _database()
        async with engine.begin() as connection:
            await connection.run_sync(SQLModel.metadata.create_all)
        async with factory() as session:
            audit = await AuditRepository(session).create(
                AgentAudit(
                    caller=CallerType.REST,
                    tool_name="get_status",
                    arguments={},
                    status=AuditStatus.SUCCESS,
                    risk_level=RiskLevel.NONE,
                    result_summary='{"ok": true}',
                )
            )
            record = await AgentIdempotencyRepository(session).create(
                AgentIdempotency(
                    caller=CallerType.REST,
                    key="crash-after-sync-success",
                    request_hash="a" * 64,
                    audit_id=audit.id,
                    response_status=200,
                    request_mode="sync",
                )
            )
            await session.commit()
            record_id = record.id
            audit_id = audit.id

        await ConfirmationService(factory, ToolRegistry()).reset_after_restart()
        async with factory() as session:
            recovered = await session.get(AgentIdempotency, record_id)
            assert recovered.response_status == 200
            assert recovered.response_body == {
                "status": "success",
                "audit_id": audit_id,
                "result": {"ok": True},
            }
        await engine.dispose()

    asyncio.run(scenario())


def test_startup_does_not_replay_unanswered_truncated_sync_success():
    async def scenario():
        from maa_api.domain.errors import AppError, ErrorCode

        engine, factory = _database()
        async with engine.begin() as connection:
            await connection.run_sync(SQLModel.metadata.create_all)
        async with factory() as session:
            audit = await AuditRepository(session).create(
                AgentAudit(
                    caller=CallerType.REST,
                    tool_name="get_screenshot",
                    arguments={},
                    status=AuditStatus.SUCCESS,
                    risk_level=RiskLevel.NONE,
                    result_summary=json.dumps({"image": "x" * 3000}),
                )
            )
            record = await AgentIdempotencyRepository(session).create(
                AgentIdempotency(
                    caller=CallerType.REST,
                    key="crash-after-truncated-result",
                    request_hash="e" * 64,
                    audit_id=audit.id,
                    request_mode="sync",
                )
            )
            await session.commit()
            record_id, audit_id = record.id, audit.id

        await ConfirmationService(factory, ToolRegistry()).reset_after_restart()
        async with factory() as session:
            recovered = await session.get(AgentIdempotency, record_id)
            assert recovered.response_status == AppError(ErrorCode.SERVICE_UNAVAILABLE).http_status
            error = recovered.response_body["__idempotency_error__"]
            assert error["code"] == ErrorCode.SERVICE_UNAVAILABLE.value
            assert error["details"]["audit_id"] == audit_id
            assert "完整响应快照不可用" in error["message"]
        await engine.dispose()

    asyncio.run(scenario())


def test_startup_fails_closed_for_legacy_idempotency_without_request_mode():
    async def scenario():
        from maa_api.domain.errors import AppError, ErrorCode

        engine, factory = _database()
        async with engine.begin() as connection:
            await connection.run_sync(SQLModel.metadata.create_all)
        async with factory() as session:
            audit = await AuditRepository(session).create(
                AgentAudit(
                    caller=CallerType.REST,
                    tool_name="safe_tool",
                    arguments={},
                    status=AuditStatus.SUCCESS,
                    risk_level=RiskLevel.NONE,
                    result_summary='{"status": "success", "result": 1}',
                )
            )
            record = await AgentIdempotencyRepository(session).create(
                AgentIdempotency(
                    caller=CallerType.REST,
                    key="legacy-no-mode",
                    request_hash="f" * 64,
                    audit_id=audit.id,
                    request_mode=None,
                )
            )
            await session.commit()
            record_id, audit_id = record.id, audit.id

        await ConfirmationService(factory, ToolRegistry()).reset_after_restart()
        async with factory() as session:
            recovered = await session.get(AgentIdempotency, record_id)
            assert recovered.response_status == AppError(ErrorCode.SERVICE_UNAVAILABLE).http_status
            error = recovered.response_body["__idempotency_error__"]
            assert error["code"] == ErrorCode.SERVICE_UNAVAILABLE.value
            assert error["details"]["audit_id"] == audit_id
            assert "缺少原调用模式" in error["message"]
        await engine.dispose()

    asyncio.run(scenario())


def test_startup_recovers_async_confirmation_as_original_202_snapshot():
    async def scenario():
        engine, factory = _database()
        async with engine.begin() as connection:
            await connection.run_sync(SQLModel.metadata.create_all)
        now = datetime(2026, 9, 25, 0, 0, 0)
        async with factory() as session:
            confirmation = await ConfirmationRepository(session).create(
                Confirmation(
                    id="awaiting-before-crash",
                    action="dangerous_action",
                    risk_level=RiskLevel.DESTRUCTIVE,
                    reason="需用户确认",
                    payload={"tool_name": "dangerous_action", "arguments": {"value": 5}},
                    requested_by=CallerType.REST,
                    expires_at=now + timedelta(minutes=10),
                )
            )
            audit = await AuditRepository(session).create(
                AgentAudit(
                    caller=CallerType.REST,
                    tool_name="dangerous_action",
                    arguments={"value": 5},
                    status=AuditStatus.PENDING,
                    risk_level=RiskLevel.DESTRUCTIVE,
                    confirmation_id=confirmation.id,
                )
            )
            confirmation.audit_id = audit.id
            idem = await AgentIdempotencyRepository(session).create(
                AgentIdempotency(
                    caller=CallerType.REST,
                    key="crash-after-async-accept",
                    request_hash="b" * 64,
                    audit_id=audit.id,
                    response_status=202,
                    request_mode="async",
                )
            )
            await session.commit()
            record_id = idem.id

        service = ConfirmationService(
            factory,
            ToolRegistry(),
            clock=lambda: now,
        )
        await service.reset_after_restart()
        async with factory() as session:
            recovered = await session.get(AgentIdempotency, record_id)
            assert recovered.response_status == 202
            assert recovered.response_body == {
                "code": "CONFIRMATION_REQUIRED",
                "status": "awaiting_confirmation",
                "confirmation_id": confirmation.id,
                "audit_id": audit.id,
                "expires_at": confirmation.expires_at.replace(tzinfo=UTC)
                .isoformat()
                .replace("+00:00", "Z"),
            }
            final_confirmation = await ConfirmationRepository(session).get(confirmation.id)
            assert final_confirmation.status == ConfirmationStatus.EXPIRED
        await engine.dispose()

    asyncio.run(scenario())


def test_startup_recovers_unanswered_sync_rejection_and_indeterminate_error_statuses():
    async def scenario():
        from maa_api.domain.errors import AppError, ErrorCode

        engine, factory = _database()
        async with engine.begin() as connection:
            await connection.run_sync(SQLModel.metadata.create_all)
        async with factory() as session:
            rejected_confirmation = await ConfirmationRepository(session).create(
                Confirmation(
                    id="rejected-before-crash",
                    action="dangerous_action",
                    risk_level=RiskLevel.DESTRUCTIVE,
                    reason="拒绝",
                    payload={"tool_name": "dangerous_action", "arguments": {}},
                    status=ConfirmationStatus.REJECTED,
                    requested_by=CallerType.REST,
                    resolved_by="web",
                    expires_at=utcnow() + timedelta(minutes=1),
                )
            )
            rejected_audit = await AuditRepository(session).create(
                AgentAudit(
                    caller=CallerType.REST,
                    tool_name="dangerous_action",
                    arguments={},
                    status=AuditStatus.REJECTED,
                    risk_level=RiskLevel.DESTRUCTIVE,
                    confirmation_id=rejected_confirmation.id,
                    error_code=ErrorCode.CONFIRMATION_REJECTED,
                )
            )
            rejected_confirmation.audit_id = rejected_audit.id
            rejected_idem = await AgentIdempotencyRepository(session).create(
                AgentIdempotency(
                    caller=CallerType.REST,
                    key="crash-after-reject",
                    request_hash="c" * 64,
                    audit_id=rejected_audit.id,
                    response_status=200,
                    request_mode="sync",
                )
            )
            indeterminate_audit = await AuditRepository(session).create(
                AgentAudit(
                    caller=CallerType.REST,
                    tool_name="click",
                    arguments={"x": 1},
                    status=AuditStatus.FAILED,
                    risk_level=RiskLevel.NONE,
                    error_code=ErrorCode.SERVICE_UNAVAILABLE,
                    result_summary="结果不确定",
                )
            )
            indeterminate_idem = await AgentIdempotencyRepository(session).create(
                AgentIdempotency(
                    caller=CallerType.REST,
                    key="crash-after-click",
                    request_hash="d" * 64,
                    audit_id=indeterminate_audit.id,
                    response_status=200,
                    request_mode="sync",
                )
            )
            await session.commit()
            rejected_id, indeterminate_id = rejected_idem.id, indeterminate_idem.id

        await ConfirmationService(factory, ToolRegistry()).reset_after_restart()
        async with factory() as session:
            rejected = await session.get(AgentIdempotency, rejected_id)
            indeterminate = await session.get(AgentIdempotency, indeterminate_id)
            rejected_error = rejected.response_body["__idempotency_error__"]
            indeterminate_error = indeterminate.response_body["__idempotency_error__"]
            assert rejected.response_status == AppError(ErrorCode.CONFIRMATION_REJECTED).http_status
            assert rejected_error["code"] == "CONFIRMATION_REJECTED"
            assert rejected_error["details"]["confirmation_id"] == rejected_confirmation.id
            assert indeterminate.response_status == AppError(ErrorCode.SERVICE_UNAVAILABLE).http_status
            assert indeterminate_error["code"] == "SERVICE_UNAVAILABLE"
            assert "不确定" in indeterminate_error["message"]
        await engine.dispose()

    asyncio.run(scenario())
