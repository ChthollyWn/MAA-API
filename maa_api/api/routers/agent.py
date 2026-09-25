"""Agent tool, session and audit REST endpoints (docs/05 §6.15)."""

from __future__ import annotations

import asyncio
import hashlib
import json
from contextlib import asynccontextmanager
from datetime import UTC, datetime, timedelta
from typing import Any

from fastapi import APIRouter, Depends, Header, Query, Request, Response, status
from pydantic import BaseModel, ConfigDict, Field
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from maa_api.agent.registry import ToolContext
from maa_api.api.deps import get_session, require_auth
from maa_api.api.errors import error_responses
from maa_api.db.models import AgentAudit, AgentIdempotency, AgentMessage, AgentSession, utcnow
from maa_api.db.repositories.agent import (
    AgentIdempotencyRepository,
    AgentMessageRepository,
    AgentSessionRepository,
)
from maa_api.db.repositories.audit import AuditRepository
from maa_api.domain.enums import AuditStatus, CallerType, RiskLevel
from maa_api.domain.errors import AppError, ErrorCode
from maa_api.settings import get_settings
from maa_api.services.log_hub import current_request_id

router = APIRouter(prefix="/api/agent", tags=["agent"])
_IDEMPOTENCY_LOCKS: dict[tuple[int, str], tuple[asyncio.Lock, int]] = {}
IDEMPOTENCY_WAIT_POLLS = 200
IDEMPOTENCY_POLL_SECONDS = 0.05


class InvokeRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    arguments: dict[str, Any]
    mode: str = Field(default="sync", pattern="^(sync|async)$")


class SessionCreateRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    title: str | None = Field(default=None, max_length=64)


@asynccontextmanager
async def _idempotency_lock(key: str):
    loop_key = (id(asyncio.get_running_loop()), key)
    lock, users = _IDEMPOTENCY_LOCKS.get(loop_key, (asyncio.Lock(), 0))
    _IDEMPOTENCY_LOCKS[loop_key] = (lock, users + 1)
    try:
        async with lock:
            yield
    finally:
        lock, users = _IDEMPOTENCY_LOCKS[loop_key]
        if users <= 1:
            _IDEMPOTENCY_LOCKS.pop(loop_key, None)
        else:
            _IDEMPOTENCY_LOCKS[loop_key] = (lock, users - 1)


def _invoke_hash(name: str, payload: InvokeRequest) -> str:
    canonical = json.dumps(
        {
            "name": name,
            "arguments": payload.arguments,
            "mode": payload.mode,
        },
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
        default=str,
    )
    return hashlib.sha256(canonical.encode("utf8")).hexdigest()


def _idempotency_error(error: dict[str, Any]) -> AppError:
    return AppError(
        ErrorCode(error["code"]),
        error.get("message"),
        error.get("details"),
    )


async def _replay_or_in_progress(
    repository_factory: Any,
    *,
    key: str,
    request_hash: str,
    response: Response,
) -> dict[str, Any]:
    audit_id = None
    for _ in range(IDEMPOTENCY_WAIT_POLLS):
        async with repository_factory() as db:
            record = await AgentIdempotencyRepository(db).get(CallerType.REST, key)
            if record is None:
                raise AppError(ErrorCode.SERVICE_UNAVAILABLE, "幂等请求预约已失效，请重试")
            if record.request_hash != request_hash:
                raise AppError(
                    ErrorCode.IDEMPOTENCY_KEY_CONFLICT,
                    "相同 Idempotency-Key 已用于不同的工具请求",
                )
            audit_id = record.audit_id
            body = record.response_body
            response_status = record.response_status
            await db.commit()
        if body is not None:
            if response_status is not None:
                response.status_code = response_status
            if "__idempotency_error__" in body:
                raise _idempotency_error(body["__idempotency_error__"])
            return body
        await asyncio.sleep(IDEMPOTENCY_POLL_SECONDS)
    response.status_code = status.HTTP_202_ACCEPTED
    return {
        "status": "in_progress",
        "audit_id": audit_id,
        "idempotency_key": key,
        "retry_after_seconds": 1,
    }


def _confirmation_service(request: Request):
    service = getattr(request.app.state, "confirmation_service", None)
    if service is None:
        raise AppError(ErrorCode.AGENT_DISABLED, "Agent 服务尚未启动")
    return service


def _page(page: int, size: int) -> None:
    if page < 1 or size < 1 or size > 200:
        raise AppError(ErrorCode.INVALID_PAGINATION, "page 必须大于 0，size 必须为 1–200")


def _iso(value: datetime | None) -> str | None:
    if value is None:
        return None
    if value.tzinfo is None:
        value = value.replace(tzinfo=UTC)
    return value.astimezone(UTC).isoformat().replace("+00:00", "Z")


def session_wire(session: AgentSession) -> dict[str, Any]:
    grant_active = bool(
        session.atomic_grant_id
        and session.atomic_grant_expires_at
        and session.atomic_grant_expires_at > datetime.now(UTC).replace(tzinfo=None)
    )
    return {
        "session_id": session.id,
        "title": session.title,
        "status": str(session.status),
        "model": session.model,
        "base_url": session.base_url,
        "message_count": session.message_count,
        "prompt_tokens": session.prompt_tokens,
        "completion_tokens": session.completion_tokens,
        "total_tokens": session.prompt_tokens + session.completion_tokens,
        "atomic_grant": (
            {
                "granted": grant_active,
                "expires_at": _iso(session.atomic_grant_expires_at),
                "grant_id": session.atomic_grant_id,
            }
            if session.atomic_grant_id is not None
            else None
        ),
        "created_at": _iso(session.created_at),
        "updated_at": _iso(session.updated_at),
        "last_message_at": _iso(session.last_message_at),
    }


def message_wire(message: AgentMessage) -> dict[str, Any]:
    return {
        "id": message.id,
        "session_id": message.session_id,
        "seq": message.seq,
        "role": str(message.role),
        "content": message.content,
        "tool_calls": message.tool_calls,
        "tool_call_id": message.tool_call_id,
        "tool_name": message.tool_name,
        "audit_id": message.audit_id,
        "prompt_tokens": message.prompt_tokens,
        "completion_tokens": message.completion_tokens,
        "finish_reason": message.finish_reason,
        "latency_ms": message.latency_ms,
        "created_at": _iso(message.created_at),
    }


def audit_wire(audit: AgentAudit) -> dict[str, Any]:
    return {
        "audit_id": audit.id,
        "caller": str(audit.caller),
        "caller_detail": audit.caller_detail,
        "request_id": audit.request_id,
        "session_id": audit.session_id,
        "tool_name": audit.tool_name,
        "arguments": audit.arguments,
        "result_summary": audit.result_summary,
        "result_ref": audit.result_ref,
        "status": str(audit.status),
        "error_code": audit.error_code,
        "risk_level": str(audit.risk_level),
        "forced": audit.forced,
        "confirmation_id": audit.confirmation_id,
        "authorized_by_id": audit.authorized_by_id,
        "duration_ms": audit.duration_ms,
        "created_at": _iso(audit.created_at),
    }


@router.get(
    "/tools",
    summary="读取 Agent 工具清单",
    responses=error_responses("AGENT_DISABLED", "UNAUTHORIZED"),
    dependencies=[Depends(require_auth)],
)
async def list_tools(
    request: Request,
    risk_level: str | None = Query(default=None),
) -> dict[str, Any]:
    service = _confirmation_service(request)
    definitions = service.registry.list()
    items = [
        {
            "name": definition.name,
            "group": definition.group,
            "risk_level": {
                "SAFE": RiskLevel.NONE.value,
                "CONDITIONAL": RiskLevel.CONSUME.value,
                "DANGEROUS": RiskLevel.DESTRUCTIVE.value,
            }[definition.risk.value],
            "risk": definition.risk.value.lower(),
            "description": definition.description,
            "input_schema": definition.params_model.model_json_schema(
                by_alias=True, mode="validation"
            ),
        }
        for definition in definitions
    ]
    if risk_level is not None:
        aliases = {
            "safe": RiskLevel.NONE.value,
            "conditional": RiskLevel.CONSUME.value,
            "dangerous": RiskLevel.DESTRUCTIVE.value,
        }
        normalized_risk = aliases.get(risk_level.lower(), risk_level.lower())
        allowed = {risk.value for risk in RiskLevel}
        if normalized_risk not in allowed:
            raise AppError(ErrorCode.INVALID_PARAMETER, "risk_level 取值无效")
        items = [item for item in items if item["risk_level"] == normalized_risk]
    return {"items": items, "total": len(items)}


@router.post(
    "/tools/{name}/invoke",
    summary="调用 Agent 工具",
    responses=error_responses(
        "TOOL_NOT_FOUND", "TOOL_ARGS_INVALID", "CONFIRMATION_REJECTED",
        "CONFIRMATION_EXPIRED", "IDEMPOTENCY_KEY_CONFLICT", "UNAUTHORIZED",
    ),
    dependencies=[Depends(require_auth)],
)
async def invoke_tool(
    name: str,
    payload: InvokeRequest,
    request: Request,
    response: Response,
    session: AsyncSession = Depends(get_session),
    idempotency_key: str | None = Header(default=None, alias="Idempotency-Key"),
) -> dict[str, Any]:
    service = _confirmation_service(request)
    context = ToolContext(CallerType.REST, None, current_request_id.get(), request, session)
    if idempotency_key is None:
        result = await service.invoke(name, payload.arguments, context, mode=payload.mode)
        if payload.mode == "async" or result.get("status") == "awaiting_confirmation":
            response.status_code = status.HTTP_202_ACCEPTED
        return result
    key = idempotency_key.strip()
    if not key:
        raise AppError(ErrorCode.INVALID_PARAMETER, "Idempotency-Key 不能为空")
    if len(key) > 64:
        raise AppError(ErrorCode.INVALID_PARAMETER, "Idempotency-Key 最长为 64 个字符")

    request_hash = _invoke_hash(name, payload)
    async with _idempotency_lock(f"rest:{key}"):
        repository_factory = service.session_factory
        replay_pending = False
        async with repository_factory() as db:
            repository = AgentIdempotencyRepository(db)
            await repository.delete_expired(utcnow() - timedelta(hours=24))
            existing = await repository.get(CallerType.REST, key)
            if existing is not None:
                if existing.request_hash != request_hash:
                    raise AppError(
                        ErrorCode.IDEMPOTENCY_KEY_CONFLICT,
                        "相同 Idempotency-Key 已用于不同的工具请求",
                    )
                await db.commit()
                if existing.response_body is not None:
                    if existing.response_status is not None:
                        response.status_code = existing.response_status
                    if "__idempotency_error__" in existing.response_body:
                        raise _idempotency_error(
                            existing.response_body["__idempotency_error__"]
                        )
                    return existing.response_body
                replay_pending = True

            if not replay_pending:
                reservation = AgentIdempotency(
                    caller=CallerType.REST,
                    key=key,
                    request_hash=request_hash,
                    created_at=utcnow(),
                )
                try:
                    await repository.create(reservation)
                    await db.commit()
                except IntegrityError:
                    await db.rollback()
                    replay_pending = True

        if replay_pending:
            return await _replay_or_in_progress(
                repository_factory,
                key=key,
                request_hash=request_hash,
                response=response,
            )

        async def persist_audit_link(audit_id: int) -> None:
            async with repository_factory() as db:
                record = await AgentIdempotencyRepository(db).get(CallerType.REST, key)
                if record is None:
                    raise AppError(ErrorCode.SERVICE_UNAVAILABLE, "幂等请求预约已失效")
                if record.request_hash != request_hash:
                    raise AppError(
                        ErrorCode.IDEMPOTENCY_KEY_CONFLICT,
                        "相同 Idempotency-Key 已用于不同的工具请求",
                    )
                record.audit_id = audit_id
                await db.commit()

        try:
            result = await service.invoke(
                name,
                payload.arguments,
                context,
                mode=payload.mode,
                on_audit_created=persist_audit_link,
            )
        except AppError as exc:
            audit_id = (exc.details or {}).get("audit_id")
            async with repository_factory() as db:
                repository = AgentIdempotencyRepository(db)
                record = await repository.get(CallerType.REST, key)
                if record is not None and audit_id is not None:
                    record.audit_id = int(audit_id)
                    record.response_status = exc.http_status
                    record.response_body = {
                        "__idempotency_error__": {
                            "code": str(exc.code),
                            "message": exc.message,
                            "details": exc.details,
                        }
                    }
                elif record is not None:
                    await repository.delete(CallerType.REST, key)
                await db.commit()
            raise AppError(
                exc.code,
                exc.message,
                {**(exc.details or {}), **({"audit_id": audit_id} if audit_id else {})},
                exc.headers,
            ) from exc
        except Exception:
            async with repository_factory() as db:
                await AgentIdempotencyRepository(db).delete(CallerType.REST, key)
                await db.commit()
            raise

        if payload.mode == "async" or result.get("status") == "awaiting_confirmation":
            response.status_code = status.HTTP_202_ACCEPTED
        try:
            stored_body = json.loads(json.dumps(result, ensure_ascii=False, default=str))
        except (TypeError, ValueError):
            stored_body = {"status": "accepted", "audit_id": result.get("audit_id")}
        try:
            async with repository_factory() as db:
                record = await AgentIdempotencyRepository(db).get(CallerType.REST, key)
                if record is not None:
                    record.audit_id = result.get("audit_id")
                    record.response_status = response.status_code
                    record.response_body = stored_body
                await db.commit()
        except Exception:
            async with repository_factory() as db:
                await AgentIdempotencyRepository(db).delete(CallerType.REST, key)
                await db.commit()
            raise
        return result


@router.get(
    "/sessions",
    summary="分页读取 Agent 会话",
    dependencies=[Depends(require_auth)],
)
async def list_sessions(
    page: int = Query(default=1),
    size: int = Query(default=20),
    session: AsyncSession = Depends(get_session),
) -> dict[str, Any]:
    _page(page, size)
    result = await AgentSessionRepository(session).list_page(page=page, size=size)
    return {
        "items": [session_wire(item) for item in result.items],
        "total": result.total,
        "page": result.page,
        "size": result.size,
    }


@router.post(
    "/sessions",
    status_code=status.HTTP_201_CREATED,
    summary="创建 Agent 会话",
    responses=error_responses("LLM_NOT_CONFIGURED", "UNAUTHORIZED"),
    dependencies=[Depends(require_auth)],
)
async def create_session(
    payload: SessionCreateRequest,
    response: Response,
    session: AsyncSession = Depends(get_session),
) -> dict[str, Any]:
    settings = get_settings().llm
    if not settings.base_url or not settings.api_key or not settings.model:
        raise AppError(ErrorCode.LLM_NOT_CONFIGURED, "请先配置 LLM base_url、api_key 和 model")
    stored = await AgentSessionRepository(session).create(
        AgentSession(title=payload.title, model=settings.model, base_url=settings.base_url)
    )
    await session.commit()
    response.headers["Location"] = f"/api/agent/sessions/{stored.id}"
    return session_wire(stored)


@router.get(
    "/sessions/{session_id}",
    summary="读取 Agent 会话详情",
    responses=error_responses("AGENT_SESSION_NOT_FOUND", "UNAUTHORIZED"),
    dependencies=[Depends(require_auth)],
)
async def get_session_detail(
    session_id: str,
    session: AsyncSession = Depends(get_session),
) -> dict[str, Any]:
    stored = await AgentSessionRepository(session).get(session_id)
    if stored is None:
        raise AppError(ErrorCode.AGENT_SESSION_NOT_FOUND, "Agent 会话不存在")
    return session_wire(stored)


@router.delete(
    "/sessions/{session_id}",
    status_code=status.HTTP_204_NO_CONTENT,
    summary="删除 Agent 会话",
    responses=error_responses("AGENT_SESSION_NOT_FOUND", "UNAUTHORIZED"),
    dependencies=[Depends(require_auth)],
)
async def delete_session(session_id: str, request: Request) -> Response:
    service = _confirmation_service(request)
    async with service.session_factory() as session:
        exists = await AgentSessionRepository(session).get(session_id)
    if exists is None:
        raise AppError(ErrorCode.AGENT_SESSION_NOT_FOUND, "Agent 会话不存在")
    await service.revoke_session_grant(session_id)
    async with service.session_factory() as session:
        await AgentSessionRepository(session).delete(session_id)
        await session.commit()
    return Response(status_code=status.HTTP_204_NO_CONTENT)


@router.get(
    "/sessions/{session_id}/messages",
    summary="按序读取 Agent 会话消息",
    responses=error_responses("AGENT_SESSION_NOT_FOUND", "UNAUTHORIZED"),
    dependencies=[Depends(require_auth)],
)
async def list_session_messages(
    session_id: str,
    after_seq: int | None = Query(default=None, ge=-1),
    page: int = Query(default=1),
    size: int = Query(default=20),
    session: AsyncSession = Depends(get_session),
) -> dict[str, Any]:
    _page(page, size)
    if await AgentSessionRepository(session).get(session_id) is None:
        raise AppError(ErrorCode.AGENT_SESSION_NOT_FOUND, "Agent 会话不存在")
    result = await AgentMessageRepository(session).list_page(
        session_id, after_seq=after_seq, page=page, size=size
    )
    return {
        "items": [message_wire(item) for item in result.items],
        "total": result.total,
        "page": result.page,
        "size": result.size,
    }


@router.delete(
    "/sessions/{session_id}/atomic-grant",
    status_code=status.HTTP_204_NO_CONTENT,
    summary="撤销 Agent 会话的原子操作授权",
    responses=error_responses("AGENT_SESSION_NOT_FOUND", "UNAUTHORIZED"),
    dependencies=[Depends(require_auth)],
)
async def revoke_atomic_grant(session_id: str, request: Request) -> Response:
    await _confirmation_service(request).revoke_session_grant(session_id)
    return Response(status_code=status.HTTP_204_NO_CONTENT)


@router.get(
    "/audits",
    summary="分页读取 Agent 工具审计",
    responses=error_responses("INVALID_PAGINATION", "UNAUTHORIZED"),
    dependencies=[Depends(require_auth)],
)
async def list_audits(
    caller: CallerType | None = Query(default=None),
    tool_name: str | None = Query(default=None),
    audit_status: AuditStatus | None = Query(default=None, alias="status"),
    risk_level: RiskLevel | None = Query(default=None),
    since: datetime | None = Query(default=None),
    page: int = Query(default=1),
    size: int = Query(default=20),
    session: AsyncSession = Depends(get_session),
) -> dict[str, Any]:
    _page(page, size)
    result = await AuditRepository(session).list(
        caller=caller,
        tool_name=tool_name,
        status=audit_status,
        risk_level=risk_level,
        since=since,
        page=page,
        size=size,
    )
    return {
        "items": [audit_wire(item) for item in result.items],
        "total": result.total,
        "page": result.page,
        "size": result.size,
    }


@router.get(
    "/audits/{audit_id}",
    summary="读取单条 Agent 工具审计",
    responses=error_responses("NOT_FOUND", "UNAUTHORIZED"),
    dependencies=[Depends(require_auth)],
)
async def get_audit(audit_id: int, session: AsyncSession = Depends(get_session)) -> dict[str, Any]:
    audit = await AuditRepository(session).get(audit_id)
    if audit is None:
        raise AppError(ErrorCode.NOT_FOUND, "Agent 审计记录不存在")
    return audit_wire(audit)
