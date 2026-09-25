"""Persistence-backed risk confirmations and shared tool invocation lifecycle."""

from __future__ import annotations

import asyncio
import json
import logging
import time
from collections.abc import Callable, Mapping
from datetime import UTC, datetime, timedelta
from typing import Any

from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from maa_api.agent.policy import PolicyDecision, PolicyEngine
from maa_api.agent.registry import ToolContext, ToolRegistry
from maa_api.db.models import AgentAudit, AgentSession, Confirmation, utcnow
from maa_api.db.repositories.agent import (
    AgentIdempotencyRepository,
    AgentSessionRepository,
)
from maa_api.db.repositories.audit import AuditRepository, ConfirmationRepository
from maa_api.domain.enums import (
    AgentSessionStatus,
    AuditStatus,
    CallerType,
    ConfirmationStatus,
    RiskLevel,
)
from maa_api.domain.errors import AppError, ErrorCode

logger = logging.getLogger(__name__)

DEFAULT_CONFIRMATION_TIMEOUT_SECONDS = 10 * 60
DEFAULT_GRANT_CONFIRMATION_TIMEOUT_SECONDS = 120
MCP_CONFIRMATION_WAIT_SECONDS = 25


class ConfirmationService:
    """Own confirmation state transitions and durable tool audit lifecycle.

    Pending calls are represented in SQLite before the service starts waiting.
    The in-process tasks only bridge an active request to that durable state;
    :meth:`reset_after_restart` expires abandoned confirmations and clears all
    session grants before accepting traffic after a process restart.
    """

    def __init__(
        self,
        session_factory: async_sessionmaker[AsyncSession],
        registry: ToolRegistry,
        *,
        policy: PolicyEngine | None = None,
        broadcast: Callable[[str, dict[str, Any]], None] | None = None,
        notify: Any = None,
        confirmation_timeout_seconds: int = DEFAULT_CONFIRMATION_TIMEOUT_SECONDS,
        grant_confirmation_timeout_seconds: int = DEFAULT_GRANT_CONFIRMATION_TIMEOUT_SECONDS,
        clock: Callable[[], datetime] = utcnow,
    ) -> None:
        if confirmation_timeout_seconds < 1 or grant_confirmation_timeout_seconds < 1:
            raise ValueError("confirmation timeouts must be positive")
        self.session_factory = session_factory
        self.registry = registry
        self.policy = policy or PolicyEngine()
        self.broadcast = broadcast or (lambda _kind, _data: None)
        self.notify = notify
        self.confirmation_timeout_seconds = int(confirmation_timeout_seconds)
        self.grant_confirmation_timeout_seconds = int(grant_confirmation_timeout_seconds)
        self.clock = clock
        self._resolution_events: dict[str, asyncio.Event] = {}
        self._workers: dict[str, asyncio.Task[None]] = {}
        self._expiry_task: asyncio.Task[None] | None = None
        self._grant_locks: dict[str, asyncio.Lock] = {}
        self._grant_revocation_epochs: dict[str, int] = {}

    async def invoke(
        self,
        name: str,
        arguments: Mapping[str, Any],
        context: ToolContext,
        *,
        mode: str = "sync",
        on_audit_created: Callable[[int], Any] | None = None,
    ) -> dict[str, Any]:
        return await self._invoke(
            name,
            arguments,
            context,
            mode=mode,
            on_audit_created=on_audit_created,
        )

    async def invoke_mcp(
        self,
        name: str,
        arguments: Mapping[str, Any],
        context: ToolContext,
    ) -> dict[str, Any]:
        """Invoke from MCP, returning pending after its short synchronous wait."""
        return await self._invoke(
            name, arguments, context, mode="sync", mcp_short_wait=True
        )

    async def _invoke(
        self,
        name: str,
        arguments: Mapping[str, Any],
        context: ToolContext,
        *,
        mode: str,
        mcp_short_wait: bool = False,
        on_audit_created: Callable[[int], Any] | None = None,
    ) -> dict[str, Any]:
        if mode not in {"sync", "async"}:
            raise AppError(ErrorCode.INVALID_PARAMETER, "mode 必须为 sync 或 async")
        definition = self.registry.get(name)
        params = self.registry.validate(name, arguments)
        decision = await self.policy.evaluate(definition, params, context)
        if decision.requires_confirmation:
            confirmation_id, audit_id = await self._create_confirmation(
                definition.name, arguments, context, decision
            )
            await _notify_audit_created(on_audit_created, audit_id)
            event = asyncio.Event()
            self._resolution_events[confirmation_id] = event
            worker = asyncio.create_task(
                self._wait_and_execute(
                    confirmation_id,
                    audit_id,
                    definition.name,
                    dict(arguments),
                    context,
                    decision,
                    event,
                    self._grant_revocation_epochs.get(context.session_id or "", 0),
                ),
                name=f"agent-confirm-{confirmation_id}",
            )
            self._workers[confirmation_id] = worker
            worker.add_done_callback(
                lambda task, key=confirmation_id: self._worker_done(key, task)
            )
            if mode == "async":
                return {
                    "code": str(ErrorCode.CONFIRMATION_REQUIRED),
                    "status": "awaiting_confirmation",
                    "confirmation_id": confirmation_id,
                    "audit_id": audit_id,
                    "expires_at": self._confirmation_expiry(
                        decision.confirmation_action
                    ).replace(tzinfo=UTC).isoformat().replace("+00:00", "Z"),
                }
            if mcp_short_wait:
                try:
                    await asyncio.wait_for(
                        asyncio.shield(worker), timeout=MCP_CONFIRMATION_WAIT_SECONDS
                    )
                except TimeoutError:
                    return {
                        "code": str(ErrorCode.CONFIRMATION_REQUIRED),
                        "status": "awaiting_confirmation",
                        "confirmation_id": confirmation_id,
                        "audit_id": audit_id,
                        "expires_at": self._confirmation_expiry(
                            decision.confirmation_action
                        ).replace(tzinfo=UTC).isoformat().replace("+00:00", "Z"),
                        "hint": "用户尚未确认。请用 check_confirmation 查询结果，或稍后重试。",
                    }
            else:
                await asyncio.shield(worker)
            detail = await self.get(confirmation_id)
            status = detail["status"]
            if status == ConfirmationStatus.REJECTED.value:
                raise AppError(
                    ErrorCode.CONFIRMATION_REJECTED,
                    "确认请求已被拒绝",
                    {"audit_id": audit_id, "confirmation_id": confirmation_id},
                )
            if status == ConfirmationStatus.EXPIRED.value:
                raise AppError(
                    ErrorCode.CONFIRMATION_EXPIRED,
                    "确认请求已过期",
                    {"audit_id": audit_id, "confirmation_id": confirmation_id},
                )
            execution = detail.get("execution") or {}
            if execution.get("status") != AuditStatus.SUCCESS.value:
                try:
                    failure_code = ErrorCode(
                        execution.get("error_code") or ErrorCode.TOOL_EXECUTION_FAILED
                    )
                except ValueError:
                    failure_code = ErrorCode.TOOL_EXECUTION_FAILED
                raise AppError(
                    failure_code,
                    execution.get("result") or "工具执行失败",
                    {"audit_id": audit_id, "confirmation_id": confirmation_id},
                )
            raw_result = execution.get("result")
            try:
                result = json.loads(raw_result) if raw_result is not None else None
            except (TypeError, ValueError):
                result = raw_result
            return {
                "status": "success",
                "confirmation_id": confirmation_id,
                "audit_id": audit_id,
                "result": result,
            }

        audit_id = await self._create_audit(name, arguments, context, decision)
        await _notify_audit_created(on_audit_created, audit_id)
        if mode == "async":
            worker = asyncio.create_task(
                self._execute_and_record(
                    audit_id,
                    name,
                    dict(arguments),
                    context,
                    authorized_by=decision.authorized_by,
                ),
                name=f"agent-tool-{audit_id}",
            )
            self._workers[str(audit_id)] = worker
            worker.add_done_callback(
                lambda task, key=str(audit_id): self._worker_done(key, task)
            )
            return {"status": "accepted", "audit_id": audit_id}

        try:
            result = await self._execute_and_record(
                audit_id,
                name,
                dict(arguments),
                context,
                authorized_by=decision.authorized_by,
            )
        except AppError as exc:
            exc.details = {**(exc.details or {}), "audit_id": audit_id}
            raise
        return {"status": "success", "audit_id": audit_id, "result": result}

    async def resolve(
        self,
        confirmation_id: str,
        *,
        approved: bool,
        resolved_by: str,
        reason: str | None = None,
        request: Any = None,
    ) -> dict[str, Any]:
        async with self.session_factory() as session:
            repo = ConfirmationRepository(session)
            row = await repo.get(confirmation_id)
            if row is None:
                raise AppError(ErrorCode.CONFIRMATION_NOT_FOUND, "确认请求不存在")
            if row.status != ConfirmationStatus.PENDING:
                code = (
                    ErrorCode.CONFIRMATION_EXPIRED
                    if row.status == ConfirmationStatus.EXPIRED
                    else ErrorCode.CONFIRMATION_ALREADY_RESOLVED
                )
                raise AppError(code, "确认请求已经处理")
            if _as_utc(row.expires_at) <= _as_utc(self.clock()):
                changed = await repo.resolve(
                    row.id,
                    ConfirmationStatus.EXPIRED,
                    resolved_by="system",
                    reason="确认超时",
                    resolved_at=self.clock(),
                )
                if changed:
                    if row.audit_id is not None:
                        await AuditRepository(session).set_terminal(
                            row.audit_id,
                            AuditStatus.EXPIRED,
                            error_code=str(ErrorCode.CONFIRMATION_EXPIRED),
                            result_summary="确认超时，已自动拒绝",
                        )
                    await session.commit()
                    expired_row = await repo.get(confirmation_id)
                else:
                    await session.rollback()
                    async with self.session_factory() as reread_session:
                        expired_row = await ConfirmationRepository(reread_session).get(
                            confirmation_id
                        )
                if expired_row is None:
                    raise AppError(ErrorCode.CONFIRMATION_NOT_FOUND, "确认请求不存在")
                if changed:
                    self.broadcast(
                        "confirm_resolved",
                        self._resolved_event(expired_row, "expired"),
                    )
                    event = self._resolution_events.get(confirmation_id)
                    if event is not None:
                        event.set()
                    raise AppError(
                        ErrorCode.CONFIRMATION_EXPIRED,
                        "确认请求已过期",
                        {"audit_id": row.audit_id, "confirmation_id": confirmation_id},
                    )
                code = (
                    ErrorCode.CONFIRMATION_EXPIRED
                    if expired_row.status == ConfirmationStatus.EXPIRED
                    else ErrorCode.CONFIRMATION_ALREADY_RESOLVED
                )
                raise AppError(code, "确认请求已经处理")

            target = ConfirmationStatus.APPROVED if approved else ConfirmationStatus.REJECTED
            changed = await repo.resolve(
                row.id, target, resolved_by=resolved_by, reason=reason
            )
            if not changed:
                raise AppError(ErrorCode.CONFIRMATION_ALREADY_RESOLVED, "确认请求已经处理")
            if row.audit_id is not None and not approved:
                await AuditRepository(session).set_terminal(
                    row.audit_id,
                    AuditStatus.REJECTED,
                    error_code=str(ErrorCode.CONFIRMATION_REJECTED),
                    result_summary=reason or "用户拒绝操作",
                )
            await session.commit()

        if not approved:
            self.broadcast("confirm_resolved", self._resolved_event(row, "rejected"))
        event = self._resolution_events.get(confirmation_id)
        if event is not None:
            event.set()
        result = await self.get(confirmation_id)
        return result

    async def get(self, confirmation_id: str) -> dict[str, Any]:
        async with self.session_factory() as session:
            row = await ConfirmationRepository(session).get(confirmation_id)
            if row is None:
                raise AppError(ErrorCode.CONFIRMATION_NOT_FOUND, "确认请求不存在")
            audit = await AuditRepository(session).get(row.audit_id) if row.audit_id else None
            return self._confirmation_wire(row, audit)

    async def list(
        self,
        *,
        status: ConfirmationStatus | str | None = ConfirmationStatus.PENDING,
        page: int = 1,
        size: int = 20,
    ) -> dict[str, Any]:
        async with self.session_factory() as session:
            result = await ConfirmationRepository(session).list(
                status=status, page=page, size=size
            )
            return {
                "items": [self._confirmation_wire(row) for row in result.items],
                "total": result.total,
                "page": result.page,
                "size": result.size,
            }

    async def reset_after_restart(self) -> None:
        """Fail closed on every nonterminal invocation before accepting traffic."""
        from sqlalchemy import select, update

        from maa_api.db.models import AgentSession

        now = self.clock()
        async with self.session_factory() as session:
            repo = ConfirmationRepository(session)
            result = await repo.expire_all_pending(now)
            expired_rows = (
                await session.execute(
                    select(Confirmation).where(Confirmation.id.in_(result))
                )
            ).scalars().all() if result else []
            expired_audit_ids = {row.audit_id for row in expired_rows if row.audit_id is not None}
            audits = AuditRepository(session)
            for row in expired_rows:
                if row.audit_id is not None:
                    await audits.set_terminal(
                        row.audit_id,
                        AuditStatus.EXPIRED,
                        error_code=str(ErrorCode.CONFIRMATION_EXPIRED),
                        result_summary="服务重启，待确认操作已失效，未执行",
                    )

            pending_audits = (
                await session.execute(
                    select(AgentAudit).where(AgentAudit.status == AuditStatus.PENDING)
                )
            ).scalars().all()
            for audit in pending_audits:
                if audit.id not in expired_audit_ids:
                    await audits.set_terminal(
                        audit.id,
                        AuditStatus.FAILED,
                        error_code=str(ErrorCode.SERVICE_UNAVAILABLE),
                        result_summary=(
                            "服务重启时调用仍处于 pending，执行结果不确定；"
                            "为避免重复副作用，不会自动重试"
                        ),
                    )

            grant_sessions = list(
                (
                    await session.execute(
                        select(AgentSession.id).where(
                            AgentSession.atomic_grant_id.is_not(None)
                            | AgentSession.atomic_grant_expires_at.is_not(None)
                        )
                    )
                ).scalars().all()
            )
            await session.execute(
                update(AgentSession)
                .where(
                    AgentSession.atomic_grant_id.is_not(None)
                    | AgentSession.atomic_grant_expires_at.is_not(None)
                )
                .values(atomic_grant_id=None, atomic_grant_expires_at=None, updated_at=now)
            )
            await AgentIdempotencyRepository(session).delete_unlinked()
            await session.commit()
        for confirmation_id in result:
            self.broadcast(
                "confirm_resolved",
                {"confirmation_id": confirmation_id, "resolution": "expired"},
            )
            event = self._resolution_events.get(confirmation_id)
            if event is not None:
                event.set()
        for session_id in grant_sessions:
            self.broadcast(
                "agent_event",
                {
                    "session_id": session_id,
                    "event": "atomic_grant_changed",
                    "payload": {"granted": False, "expires_at": None, "grant_id": None},
                },
            )

    async def start(self, *, scan_interval_seconds: float = 10.0) -> None:
        """Reset volatile confirmation state and start the expiry safety scan."""
        if self._expiry_task is not None and not self._expiry_task.done():
            return
        await self.reset_after_restart()
        self._expiry_task = asyncio.create_task(
            self._expiry_loop(max(float(scan_interval_seconds), 0.05)),
            name="agent-confirmation-expiry-scan",
        )

    async def close(self) -> None:
        """Stop expiry work and cancel process-local waiters during shutdown."""
        from sqlalchemy import select

        if self._expiry_task is not None:
            self._expiry_task.cancel()
            await asyncio.gather(self._expiry_task, return_exceptions=True)
            self._expiry_task = None
        workers = list(self._workers.values())
        for worker in workers:
            worker.cancel()
        if workers:
            await asyncio.gather(*workers, return_exceptions=True)
        now = self.clock()
        async with self.session_factory() as session:
            repo = ConfirmationRepository(session)
            expired_ids = await repo.expire_all_pending(now)
            expired_rows = (
                await session.execute(
                    select(Confirmation).where(Confirmation.id.in_(expired_ids))
                )
            ).scalars().all() if expired_ids else []
            expired_audit_ids = {
                row.audit_id for row in expired_rows if row.audit_id is not None
            }
            audit_repo = AuditRepository(session)
            for row in expired_rows:
                if row.audit_id is not None:
                    await audit_repo.set_terminal(
                        row.audit_id,
                        AuditStatus.EXPIRED,
                        error_code=str(ErrorCode.CONFIRMATION_EXPIRED),
                        result_summary="服务关闭，待确认操作已失效，未执行",
                    )
            pending_audits = (
                await session.execute(
                    select(AgentAudit).where(AgentAudit.status == AuditStatus.PENDING)
                )
            ).scalars().all()
            for audit in pending_audits:
                if audit.id not in expired_audit_ids:
                    await audit_repo.set_terminal(
                        audit.id,
                        AuditStatus.FAILED,
                        error_code=str(ErrorCode.SERVICE_UNAVAILABLE),
                        result_summary=(
                            "服务关闭时调用仍处于 pending，执行结果不确定；"
                            "为避免重复副作用，不会自动重试"
                        ),
                    )
            await session.commit()
        for confirmation_id in expired_ids:
            self.broadcast(
                "confirm_resolved",
                {"confirmation_id": confirmation_id, "resolution": "expired"},
            )
            event = self._resolution_events.get(confirmation_id)
            if event is not None:
                event.set()

    async def _expiry_loop(self, interval: float) -> None:
        while True:
            await asyncio.sleep(interval)
            try:
                await self.expire_due()
            except asyncio.CancelledError:
                raise
            except Exception:
                logger.exception("Agent 确认过期扫描失败")

    async def expire_due(self) -> None:
        """Expire overdue confirmations and session grants in one database unit."""
        from sqlalchemy import select

        now = self.clock()
        async with self.session_factory() as session:
            repo = ConfirmationRepository(session)
            ids = await repo.expire_overdue(now)
            if ids:
                rows = (
                    await session.execute(
                        select(Confirmation).where(Confirmation.id.in_(ids))
                    )
                ).scalars().all()
                for row in rows:
                    if row.audit_id is not None:
                        await AuditRepository(session).set_terminal(
                            row.audit_id,
                            AuditStatus.EXPIRED,
                            error_code=str(ErrorCode.CONFIRMATION_EXPIRED),
                            result_summary="确认超时，已自动拒绝",
                        )
            expired_grants = await AgentSessionRepository(session).clear_expired_grants(now)
            await session.commit()
        for confirmation_id in ids:
            self.broadcast(
                "confirm_resolved",
                {"confirmation_id": confirmation_id, "resolution": "expired"},
            )
            event = self._resolution_events.get(confirmation_id)
            if event is not None:
                event.set()
        for session_id, _grant_id in expired_grants:
            self.broadcast(
                "agent_event",
                {
                    "session_id": session_id,
                    "event": "atomic_grant_changed",
                    "payload": {"granted": False, "expires_at": None, "grant_id": None},
                },
            )

    async def revoke_session_grant(self, session_id: str, *, resolved_by: str = "web") -> None:
        """Clear grant fields and reject outstanding grant confirmations."""
        from maa_api.db.models import AgentSession

        lock = self._grant_locks.setdefault(session_id, asyncio.Lock())
        async with lock:
            self._grant_revocation_epochs[session_id] = (
                self._grant_revocation_epochs.get(session_id, 0) + 1
            )
            now = self.clock()
            async with self.session_factory() as session:
                repo = AgentSessionRepository(session)
                if await repo.get(session_id) is None:
                    raise AppError(ErrorCode.AGENT_SESSION_NOT_FOUND, "Agent 会话不存在")
                await repo.clear_grant(session_id)
                pending = await ConfirmationRepository(session).reject_pending_grants_for_session(
                    session_id,
                    resolved_by=resolved_by,
                    reason="用户撤销了会话授权",
                    now=now,
                )
                for confirmation in pending:
                    if confirmation.audit_id is not None:
                        await AuditRepository(session).set_terminal(
                            confirmation.audit_id,
                            AuditStatus.REJECTED,
                            error_code=str(ErrorCode.CONFIRMATION_REJECTED),
                            result_summary="用户撤销了会话授权",
                        )
                await session.commit()
        for confirmation in pending:
            self.broadcast(
                "confirm_resolved",
                self._resolved_event(confirmation, "rejected"),
            )
            event = self._resolution_events.get(confirmation.id)
            if event is not None:
                event.set()
        self.broadcast(
            "agent_event",
            {
                "session_id": session_id,
                "event": "atomic_grant_changed",
                "payload": {"granted": False, "expires_at": None, "grant_id": None},
            },
        )

    async def check_confirmation(
        self, confirmation_id: str, *, context: ToolContext | None = None
    ) -> dict[str, Any]:
        """Return persisted status/result without executing any stored action."""
        del context
        return await self.get(confirmation_id)

    async def _create_confirmation(
        self,
        name: str,
        arguments: Mapping[str, Any],
        context: ToolContext,
        decision: PolicyDecision,
    ) -> tuple[str, int]:
        now = self.clock()
        timeout = (
            self.grant_confirmation_timeout_seconds
            if decision.confirmation_action == "grant_atomic_ops"
            else self.confirmation_timeout_seconds
        )
        expires_at = now + timedelta(seconds=timeout)
        audit = AgentAudit(
            caller=_caller(context.caller),
            caller_detail=_caller_detail(context),
            tool_name=name,
            request_id=context.request_id,
            arguments=dict(arguments),
            status=AuditStatus.PENDING,
            risk_level=decision.risk,
            authorized_by_id=decision.authorized_by,
            session_id=context.session_id,
            created_at=now,
        )
        async with self.session_factory() as session:
            confirmation = Confirmation(
                action=decision.confirmation_action or name,
                risk_level=decision.risk,
                reason="；".join(decision.reasons) or "该操作需要人工确认",
                payload={
                    "tool_name": name,
                    "arguments": dict(arguments),
                    "caller": _caller(context.caller),
                    "caller_detail": _caller_detail(context),
                    "session_id": context.session_id,
                    "request_id": context.request_id,
                    "grant": decision.confirmation_action == "grant_atomic_ops",
                    "window_seconds": decision.window_seconds,
                },
                requested_by=_caller(context.caller),
                audit_id=None,
                expires_at=expires_at,
            )
            confirmation = await ConfirmationRepository(session).create(confirmation)
            audit.confirmation_id = confirmation.id
            audit = await AuditRepository(session).create(audit)
            audit.confirmation_id = confirmation.id
            confirmation.audit_id = audit.id
            await session.flush()
            await session.commit()

        event = {
            "confirmation_id": confirmation.id,
            "category": decision.risk.value,
            "title": name,
            "summary": confirmation.reason,
            "caller": {
                "kind": _caller(context.caller),
                "name": _caller_detail(context),
                "session_id": context.session_id,
            },
            "tool": name,
            "params": dict(arguments),
            "expires_at": expires_at.replace(tzinfo=UTC).timestamp(),
        }
        self.broadcast("confirm_request", event)
        if self.notify is not None:
            try:
                await self.notify.send_event(
                    "confirmation_required",
                    {
                        "title": f"需要确认：{name}",
                        "message": confirmation.reason,
                        "confirmation_id": confirmation.id,
                    },
                )
            except Exception:
                logger.exception("发送人工确认通知失败：%s", confirmation.id)
        return confirmation.id, audit.id

    async def _wait_and_execute(
        self,
        confirmation_id: str,
        audit_id: int,
        name: str,
        arguments: dict[str, Any],
        context: ToolContext,
        decision: PolicyDecision,
        event: asyncio.Event,
        revocation_epoch: int = 0,
    ) -> None:
        expires_at = self._confirmation_expiry(decision.confirmation_action)
        remaining = max((expires_at - self.clock()).total_seconds(), 0)
        try:
            await asyncio.wait_for(event.wait(), timeout=remaining)
        except TimeoutError:
            await self._expire(confirmation_id, audit_id)
            return
        confirmation = await self.get(confirmation_id)
        if confirmation["status"] != ConfirmationStatus.APPROVED.value:
            return
        grant_id = None
        if decision.confirmation_action == "grant_atomic_ops":
            grant_id = confirmation_id
            session_id = context.session_id or ""
            lock = self._grant_locks.setdefault(session_id, asyncio.Lock())
            async with lock:
                if self._grant_revocation_epochs.get(session_id, 0) != revocation_epoch:
                    await self._complete_audit(
                        audit_id,
                        AuditStatus.REJECTED,
                        error_code=str(ErrorCode.CONFIRMATION_REJECTED),
                        result_summary="用户撤销了会话授权",
                    )
                    return
                await self._grant_session(
                    context.session_id,
                    confirmation_id,
                    int(decision.window_seconds or 900),
                )
        try:
            await self._execute_and_record(
                audit_id,
                name,
                arguments,
                context,
                confirmation_id=confirmation_id,
                authorized_by=grant_id,
            )
        except AppError:
            # Detached confirmation workers persist failures on the audit row;
            # they must not leave unobserved task exceptions on the event loop.
            pass
        self.broadcast(
            "confirm_resolved",
            {
                "confirmation_id": confirmation_id,
                "resolution": "approved",
                "audit_id": audit_id,
            },
        )

    async def _execute_and_record(
        self,
        audit_id: int,
        name: str,
        arguments: dict[str, Any],
        context: ToolContext,
        *,
        confirmation_id: str | None = None,
        authorized_by: str | None = None,
    ) -> Any:
        started = time.monotonic()
        definition = self.registry.get(name)
        try:
            async with self.session_factory() as db_session:
                execution_context = ToolContext(
                    context.caller,
                    context.session_id,
                    context.request_id,
                    context.request,
                    db_session,
                )
                result = await self.registry.execute(name, arguments, execution_context)
            await self._complete_audit(
                audit_id,
                AuditStatus.SUCCESS,
                result=result,
                duration_ms=int((time.monotonic() - started) * 1000),
                confirmation_id=(None if authorized_by else confirmation_id),
                authorized_by=authorized_by,
            )
            return result
        except AppError as exc:
            await self._complete_audit(
                audit_id,
                AuditStatus.FAILED,
                error_code=str(exc.code),
                result_summary=exc.message,
                duration_ms=int((time.monotonic() - started) * 1000),
                confirmation_id=(None if authorized_by else confirmation_id),
                authorized_by=authorized_by,
            )
            raise
        except Exception as exc:
            logger.exception("Agent 工具执行失败：%s", name)
            await self._complete_audit(
                audit_id,
                AuditStatus.FAILED,
                error_code=str(ErrorCode.TOOL_EXECUTION_FAILED),
                result_summary=type(exc).__name__,
                duration_ms=int((time.monotonic() - started) * 1000),
                confirmation_id=(None if authorized_by else confirmation_id),
                authorized_by=authorized_by,
            )
            raise AppError(ErrorCode.TOOL_EXECUTION_FAILED, "工具执行失败") from exc

    async def _create_audit(
        self,
        name: str,
        arguments: Mapping[str, Any],
        context: ToolContext,
        decision: PolicyDecision,
    ) -> int:
        async with self.session_factory() as session:
            row = await AuditRepository(session).create(
                AgentAudit(
                    caller=_caller(context.caller),
                    caller_detail=_caller_detail(context),
                    tool_name=name,
                    request_id=context.request_id,
                    arguments=dict(arguments),
                    status=AuditStatus.PENDING,
                    risk_level=decision.risk,
                    session_id=context.session_id,
                    created_at=self.clock(),
                )
            )
            await session.commit()
            return int(row.id)

    async def _complete_audit(
        self,
        audit_id: int,
        status: AuditStatus,
        *,
        result: Any = None,
        error_code: str | None = None,
        result_summary: str | None = None,
        duration_ms: int = 0,
        confirmation_id: str | None = None,
        authorized_by: str | None = None,
    ) -> None:
        if result is not None:
            result_summary = json.dumps(result, ensure_ascii=False, default=str)
        result_ref = _result_refs(result)
        async with self.session_factory() as session:
            await AuditRepository(session).set_terminal(
                audit_id,
                status,
                result_summary=result_summary,
                result_ref=result_ref,
                error_code=error_code,
                duration_ms=duration_ms,
                confirmation_id=confirmation_id,
                authorized_by_id=authorized_by,
            )
            await session.commit()

    async def _grant_session(
        self, session_id: str | None, confirmation_id: str, window_seconds: int
    ) -> None:
        if not session_id:
            raise AppError(ErrorCode.AGENT_SESSION_NOT_FOUND, "原子操作授权缺少会话")
        async with self.session_factory() as session:
            updated = await AgentSessionRepository(session).update_grant(
                session_id,
                confirmation_id=confirmation_id,
                expires_at=self.clock() + timedelta(seconds=window_seconds),
            )
            if not updated:
                raise AppError(ErrorCode.AGENT_SESSION_NOT_FOUND, "Agent 会话不存在")
            await session.commit()
        self.broadcast(
            "agent_event",
            {
                "session_id": session_id,
                "event": "atomic_grant_changed",
                "payload": {
                    "grant_id": confirmation_id,
                    "expires_at": (self.clock() + timedelta(seconds=window_seconds))
                    .replace(tzinfo=UTC)
                    .timestamp(),
                    "granted": True,
                },
            },
        )

    async def _expire(self, confirmation_id: str, audit_id: int) -> None:
        async with self.session_factory() as session:
            changed = await ConfirmationRepository(session).resolve(
                confirmation_id,
                ConfirmationStatus.EXPIRED,
                resolved_by="system",
                reason="确认超时",
            )
            if changed:
                await AuditRepository(session).set_terminal(
                    audit_id,
                    AuditStatus.EXPIRED,
                    error_code=str(ErrorCode.CONFIRMATION_EXPIRED),
                    result_summary="确认超时，已自动拒绝",
                )
            await session.commit()
        if changed:
            self.broadcast(
                "confirm_resolved",
                {"confirmation_id": confirmation_id, "resolution": "expired", "audit_id": audit_id},
            )
            event = self._resolution_events.get(confirmation_id)
            if event is not None:
                event.set()

    def _confirmation_expiry(self, action: str | None) -> datetime:
        timeout = (
            self.grant_confirmation_timeout_seconds
            if action == "grant_atomic_ops"
            else self.confirmation_timeout_seconds
        )
        return self.clock() + timedelta(seconds=timeout)

    def _forget_worker(self, key: str) -> None:
        self._workers.pop(key, None)
        self._resolution_events.pop(key, None)

    def _worker_done(self, key: str, task: asyncio.Task[Any]) -> None:
        if not task.cancelled():
            error = task.exception()
            if error is not None:
                logger.error(
                    "Agent 调用 worker 异常结束 key=%s",
                    key,
                    exc_info=(type(error), error, error.__traceback__),
                )
        self._forget_worker(key)

    @staticmethod
    def _resolved_event(row: Confirmation, resolution: str) -> dict[str, Any]:
        return {
            "confirmation_id": row.id,
            "resolution": resolution,
            "resolved_by": row.resolved_by,
            "reason": row.resolved_reason,
            "audit_id": row.audit_id,
        }

    @classmethod
    def _confirmation_wire(
        cls, row: Confirmation, audit: AgentAudit | None = None
    ) -> dict[str, Any]:
        return {
            "confirmation_id": row.id,
            "action": row.action,
            "risk_level": str(row.risk_level),
            "reason": row.reason,
            "payload": row.payload,
            "status": str(row.status),
            "requested_by": str(row.requested_by),
            "audit_id": row.audit_id,
            "resolved_by": row.resolved_by,
            "resolved_reason": row.resolved_reason,
            "expires_at": _wire_time(row.expires_at),
            "created_at": _wire_time(row.created_at),
            "resolved_at": _wire_time(row.resolved_at),
            "execution": (
                {
                    "status": str(audit.status),
                    "result": audit.result_summary,
                    "result_ref": audit.result_ref,
                    "error_code": audit.error_code,
                    "audit_id": audit.id,
                }
                if audit is not None
                else None
            ),
        }


def _caller(value: CallerType | str) -> CallerType:
    return CallerType(value)


async def _notify_audit_created(
    callback: Callable[[int], Any] | None, audit_id: int
) -> None:
    if callback is None:
        return
    result = callback(audit_id)
    if asyncio.iscoroutine(result) or isinstance(result, asyncio.Future):
        await result


def _caller_detail(context: ToolContext) -> str | None:
    if context.request is None:
        return None
    request_state = getattr(context.request, "state", None)
    return getattr(request_state, "caller_detail", None)


def _as_utc(value: datetime) -> datetime:
    return value.replace(tzinfo=UTC) if value.tzinfo is None else value.astimezone(UTC)


def _wire_time(value: datetime | None) -> str | None:
    if value is None:
        return None
    return _as_utc(value).isoformat().replace("+00:00", "Z")


def _result_refs(result: Any) -> dict[str, Any] | None:
    if not isinstance(result, Mapping):
        return None
    keys = {key: result[key] for key in ("pipeline_id", "screenshot_id", "update_id") if key in result}
    return keys or None
