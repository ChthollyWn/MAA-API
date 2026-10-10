"""Agent tool, session and audit REST endpoints (docs/05 §6.15)."""

from __future__ import annotations

import asyncio
import hashlib
import json
import logging
from collections.abc import Mapping
from contextlib import asynccontextmanager
from datetime import UTC, datetime, timedelta
from typing import Any

from fastapi import APIRouter, Depends, Header, Query, Request, Response, status
from pydantic import BaseModel, ConfigDict, Field
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession
from starlette.responses import StreamingResponse

from maa_api.agent.registry import ToolContext
from maa_api.api.deps import get_session, require_auth
from maa_api.api.errors import error_responses
from maa_api.db.models import (
    AgentAudit,
    AgentIdempotency,
    AgentMessage,
    AgentSession,
    utcnow,
)
from maa_api.db.repositories.agent import (
    AgentIdempotencyRepository,
    AgentMessageRepository,
    AgentSessionRepository,
)
from maa_api.db.repositories.audit import AuditRepository, ConfirmationRepository
from maa_api.domain.enums import AuditStatus, CallerType, RiskLevel
from maa_api.domain.errors import AppError, ErrorCode
from maa_api.settings import get_settings
from maa_api.services.log_hub import current_request_id

logger = logging.getLogger(__name__)

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


class SessionMessageRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    content: str = Field(min_length=1, max_length=32_000)


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
                raise AppError(ErrorCode.SERVICE_UNAVAILABLE, "幂等请求的受理记录已过期，请重试")
            if record.request_hash != request_hash:
                raise AppError(
                    ErrorCode.IDEMPOTENCY_KEY_CONFLICT,
                    "相同 Idempotency-Key 已用于不同的工具请求，请换一个 key 重试",
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
        raise AppError(ErrorCode.AGENT_DISABLED, "Agent 功能还在启动中，请稍后重试")
    return service


def _broadcast_grant_change(request: Request, session_id: str, event: str) -> None:
    """授权撤销后广播一条 ``agent_event``，与 ``ConfirmationService`` 里同形状。

    前端（``web/src/realtime/query-bridge.ts``）按 ``agent_event`` 刷新会话详情；
    作业授权与原子授权各用各的 ``event`` 名，互不误触。
    """
    service = getattr(request.app.state, "confirmation_service", None)
    broadcast = getattr(service, "broadcast", None)
    if not callable(broadcast):
        return
    broadcast(
        "agent_event",
        {
            "session_id": session_id,
            "event": event,
            "payload": {
                "granted": False,
                "grant_id": None,
                "expires_at": None,
                "status": "revoked",
            },
        },
    )


def _agent_runtime(request: Request):
    state = request.app.state
    runtime = getattr(state, "agent_runtime", None)
    if runtime is not None:
        return runtime
    service = _confirmation_service(request)
    # 应用没装配 runtime 时的兜底（测试直接拿 FastAPI() 拼路由会走到这里）：提问服务
    # 可能没装，取不到就传 None，runtime 会据此对 ask_user 报 AGENT_DISABLED。
    question_service = getattr(state, "question_service", None)
    factory = getattr(state, "agent_runtime_factory", None)
    if factory is not None:
        from maa_api.agent.runtime import call_runtime_factory

        runtime = call_runtime_factory(factory, service, question_service)
    else:
        from maa_api.agent.runtime import AgentRuntime

        runtime = AgentRuntime(
            service.session_factory, service, question_service=question_service
        )
    state.agent_runtime = runtime
    return runtime


async def _effective_llm_settings(request: Request):
    service = getattr(request.app.state, "setting_service", None)
    if service is not None:
        settings = await service.refresh()
        return settings, getattr(service, "llm_encryption_error", None)
    return get_settings(), None


class _LockedSessionStreamingResponse(StreamingResponse):
    """Transfer lock ownership to the turn producer when SSE body iteration starts."""

    def __init__(self, content: Any, *, runtime: Any, session_id: str, **kwargs: Any):
        self._runtime = runtime
        self._session_id = session_id
        self._producer_owns_lock = False

        async def transfer_lock_when_iteration_starts():
            # AgentRuntime starts its detached producer synchronously when its
            # async generator is first advanced. From then on that producer
            # releases the lock after the whole turn, even if this response is
            # cancelled while sending a later chunk.
            self._producer_owns_lock = True
            async for chunk in content:
                yield chunk

        super().__init__(transfer_lock_when_iteration_starts(), **kwargs)

    async def __call__(self, scope: Any, receive: Any, send: Any) -> None:
        try:
            await super().__call__(scope, receive, send)
        finally:
            if not self._producer_owns_lock:
                self._runtime.release_session(self._session_id)


def _page(page: int, size: int) -> None:
    if page < 1 or size < 1 or size > 200:
        raise AppError(ErrorCode.INVALID_PAGINATION, "page 不能小于 1，size 需在 1–200 之间")


def _iso(value: datetime | None) -> str | None:
    if value is None:
        return None
    if value.tzinfo is None:
        value = value.replace(tzinfo=UTC)
    return value.astimezone(UTC).isoformat().replace("+00:00", "Z")


def _grant_service(request: Request) -> Any:
    """作业授权服务：优先用装配好的单例，缺省按会话工厂现建。"""
    service = getattr(request.app.state, "copilot_grant_service", None)
    if service is not None:
        return service
    factory = getattr(request.app.state, "session_factory", None)
    if factory is None:
        raise AppError(ErrorCode.SERVICE_UNAVAILABLE, "作业授权功能还在启动中，请稍后重试")
    from maa_api.services.copilot.grant import CopilotGrantService

    return CopilotGrantService(factory)


def copilot_grant_wire(grant: Mapping[str, Any]) -> dict[str, Any]:
    """作业授权 wire → 会话详情里的 ``copilot_grant``（规格 §9.6，与 ``atomic_grant`` 并列）。

    键名沿用 ``services/copilot/grant.grant_wire`` 的 ``attempts`` / ``used``，前端
    ``normalizeCopilotGrant`` 按 ``attempts_total`` / ``attempts_used`` 读（两种都认）；
    这里额外给出规格点名的两个名字，免得前端只认一种。
    """
    attempts = int(grant.get("attempts") or 0)
    used = int(grant.get("used") or 0)
    expires_at = grant.get("expires_at")
    if isinstance(expires_at, str):
        try:
            expires_at = datetime.fromisoformat(expires_at.replace("Z", "+00:00"))
        except ValueError:
            expires_at = None
    return {
        "grant_id": grant.get("id"),
        "stage_id": grant.get("stage_id"),
        "stage_code": grant.get("stage_code"),
        "attempts_total": attempts,
        "attempts_used": used,
        "remaining_attempts": max(attempts - used, 0),
        "success": grant.get("success"),
        "support": grant.get("support"),
        "status": grant.get("status"),
        # 与 ``atomic_grant.expires_at`` 一样统一成 ``...Z``：前端 Date.parse 两种都认，
        # 但同一个响应里两种写法看着像两套约定。
        "expires_at": _iso(expires_at),
    }


async def active_copilot_grant_wire(request: Request, session_id: str) -> dict[str, Any] | None:
    """该会话当前生效的作业授权；没有 active（或已过期）时为 ``None``。"""
    try:
        grant = await _grant_service(request).get_active(session_id)
    except AppError:
        raise
    except Exception:  # noqa: BLE001 - 授权读不到不该让会话详情整体 500
        logger.warning("读取会话的作业授权失败：%s", session_id, exc_info=True)
        return None
    return copilot_grant_wire(grant) if grant else None


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
        "incident_key": session.incident_key,
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
        # 作业授权（M29，规格 §9.6）：与 atomic_grant 并列；列表接口不带这个字段
        # （每行都去查一次授权表不值当），前端在会话详情里补。
        "copilot_grant": None,
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
        # 思考模式模型的思考内容。不展示给用户，但必须持久化并在下一轮回传，
        # 否则上游以 400 拒绝；这里一并读出，便于排查与审计。
        "reasoning_content": message.reasoning_content,
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
            "read_only": definition.read_only,
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
            raise AppError(
                ErrorCode.INVALID_PARAMETER,
                "risk_level 不是支持的取值，"
                "可选：safe、conditional、dangerous 或 none、consume、destructive",
            )
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
        raise AppError(
            ErrorCode.INVALID_PARAMETER, "Idempotency-Key 不能为空；不需要幂等时请去掉该请求头"
        )
    if len(key) > 64:
        raise AppError(
            ErrorCode.INVALID_PARAMETER, "Idempotency-Key 最长为 64 个字符，请换一个更短的 key"
        )

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
                        "相同 Idempotency-Key 已用于不同的工具请求，请换一个 key 重试",
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
                    request_mode=payload.mode,
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
                    raise AppError(ErrorCode.SERVICE_UNAVAILABLE, "幂等请求的受理记录已过期，请重试")
                if record.request_hash != request_hash:
                    raise AppError(
                        ErrorCode.IDEMPOTENCY_KEY_CONFLICT,
                        "相同 Idempotency-Key 已用于不同的工具请求，请换一个 key 重试",
                    )
                record.audit_id = audit_id
                record.request_mode = payload.mode
                if payload.mode == "async":
                    audit = await AuditRepository(db).get(audit_id)
                    if audit is not None and audit.confirmation_id:
                        confirmation = await ConfirmationRepository(db).get(
                            audit.confirmation_id
                        )
                        if confirmation is None:
                            raise AppError(
                                ErrorCode.SERVICE_UNAVAILABLE,
                                "确认记录已失效，请重新发起该操作",
                                {"audit_id": audit_id},
                            )
                        record.response_body = {
                            "code": str(ErrorCode.CONFIRMATION_REQUIRED),
                            "status": "awaiting_confirmation",
                            "confirmation_id": confirmation.id,
                            "audit_id": audit_id,
                            "expires_at": _iso(confirmation.expires_at),
                        }
                    else:
                        record.response_body = {"status": "accepted", "audit_id": audit_id}
                    record.response_status = status.HTTP_202_ACCEPTED
                await db.commit()

        async def persist_audit_terminal(
            db: AsyncSession,
            audit_id: int,
            response_status: int,
            response_body: dict[str, Any],
        ) -> None:
            record = await AgentIdempotencyRepository(db).get_by_audit_id(audit_id)
            if (
                record is None
                or record.request_mode != "sync"
                or record.response_body is not None
            ):
                return
            record.response_status = response_status
            record.response_body = json.loads(
                json.dumps(response_body, ensure_ascii=False, default=str)
            )

        try:
            result = await service.invoke(
                name,
                payload.arguments,
                context,
                mode=payload.mode,
                on_audit_created=persist_audit_link,
                on_audit_terminal=persist_audit_terminal,
            )
        except AppError as exc:
            audit_id = (exc.details or {}).get("audit_id")
            async with repository_factory() as db:
                repository = AgentIdempotencyRepository(db)
                record = await repository.get(CallerType.REST, key)
                if audit_id is None and record is not None:
                    audit_id = record.audit_id
                if record is not None and audit_id is not None:
                    record.audit_id = int(audit_id)
                    if record.response_body is None:
                        record.response_status = exc.http_status
                        record.response_body = {
                            "__idempotency_error__": {
                                "code": str(exc.code),
                                "message": exc.message,
                                "details": {
                                    **(exc.details or {}),
                                    "audit_id": int(audit_id),
                                },
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
                repository = AgentIdempotencyRepository(db)
                record = await repository.get(CallerType.REST, key)
                if record is not None and record.audit_id is None:
                    await repository.delete(CallerType.REST, key)
                elif record is not None and record.response_body is None:
                    error = AppError(
                        ErrorCode.SERVICE_UNAVAILABLE,
                        "工具调用中断，执行结果不确定；请先按 audit_id 查询审计记录，确认未执行后再重试",
                        {"audit_id": record.audit_id},
                    )
                    record.response_status = error.http_status
                    record.response_body = {
                        "__idempotency_error__": {
                            "code": str(error.code),
                            "message": error.message,
                            "details": error.details,
                        }
                    }
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
            # The handler/audit transaction may already have committed a durable
            # response snapshot. Never delete a linked reservation here: a retry
            # could otherwise execute the side effect again with the same key.
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
    responses=error_responses(
        "LLM_NOT_CONFIGURED", "LLM_ENCRYPTION_KEY_INVALID", "UNAUTHORIZED"
    ),
    dependencies=[Depends(require_auth)],
)
async def create_session(
    payload: SessionCreateRequest,
    response: Response,
    request: Request,
    session: AsyncSession = Depends(get_session),
) -> dict[str, Any]:
    resolved, encryption_error = await _effective_llm_settings(request)
    settings = resolved.llm
    if not settings.api_key and encryption_error is not None:
        raise AppError(
            ErrorCode.LLM_ENCRYPTION_KEY_INVALID,
            "已保存的 LLM API Key 无法解密，请到设置页重新填写 API Key",
        )
    if not settings.base_url or not settings.api_key or not settings.model:
        raise AppError(
            ErrorCode.LLM_NOT_CONFIGURED,
            "还没有配置 LLM，请先在设置页填写 base_url、api_key 与 model",
        )
    stored = await AgentSessionRepository(session).create(
        AgentSession(title=payload.title, model=settings.model, base_url=settings.base_url)
    )
    await session.commit()
    response.headers["Location"] = f"/api/agent/sessions/{stored.id}"
    return session_wire(stored)


@router.post(
    "/capabilities/check",
    summary="检查 Agent 模型能力",
    responses=error_responses(
        "AGENT_DISABLED", "LLM_NOT_CONFIGURED", "LLM_ENCRYPTION_KEY_INVALID", "UNAUTHORIZED"
    ),
    dependencies=[Depends(require_auth)],
)
async def check_capabilities(request: Request) -> dict[str, Any]:
    return await _agent_runtime(request).check_capabilities(request)


@router.post(
    "/sessions/{session_id}/messages",
    summary="发送 Agent 消息并接收 SSE 事件",
    responses=error_responses(
        "AGENT_DISABLED",
        "AGENT_SESSION_NOT_FOUND",
        "AGENT_SESSION_BUSY",
        "LLM_NOT_CONFIGURED",
        "LLM_ENCRYPTION_KEY_INVALID",
        "UNAUTHORIZED",
    ),
    dependencies=[Depends(require_auth)],
)
async def stream_session_message(
    session_id: str,
    payload: SessionMessageRequest,
    request: Request,
) -> StreamingResponse:
    runtime = _agent_runtime(request)
    factory = getattr(runtime, "session_factory", None) or getattr(
        request.app.state, "session_factory", None
    )
    if factory is None:
        factory = _confirmation_service(request).session_factory
    async with factory() as db_session:
        exists = await AgentSessionRepository(db_session).get(session_id)
    if exists is None:
        raise AppError(ErrorCode.AGENT_SESSION_NOT_FOUND, "Agent 会话不存在，请刷新列表后重试")
    if not await runtime.acquire_session(session_id):
        raise AppError(
            ErrorCode.AGENT_SESSION_BUSY, "该会话还有一轮消息未结束，请等它结束后再发送"
        )
    try:
        await runtime.ensure_configured(request, session_id)
    except BaseException:
        runtime.release_session(session_id)
        raise

    return _LockedSessionStreamingResponse(
        runtime.stream_message(session_id, payload.content, request),
        runtime=runtime,
        session_id=session_id,
        media_type="text/event-stream",
        headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"},
    )


@router.get(
    "/sessions/{session_id}",
    summary="读取 Agent 会话详情",
    responses=error_responses(
        "AGENT_SESSION_NOT_FOUND", "SERVICE_UNAVAILABLE", "UNAUTHORIZED"
    ),
    dependencies=[Depends(require_auth)],
)
async def get_session_detail(
    session_id: str,
    request: Request,
    session: AsyncSession = Depends(get_session),
) -> dict[str, Any]:
    stored = await AgentSessionRepository(session).get(session_id)
    if stored is None:
        raise AppError(ErrorCode.AGENT_SESSION_NOT_FOUND, "Agent 会话不存在，请刷新列表后重试")
    wire = session_wire(stored)
    # 作业授权（M29，规格 §9.6）：与 atomic_grant 并列，前端授权条按它渲染。
    wire["copilot_grant"] = await active_copilot_grant_wire(request, session_id)
    # ``turn_running`` 是「刷新后仍能停」的真相源（规格 §3.4）。刻意直接读
    # ``app.state.agent_runtime``：为了一个只读字段去现场构造 runtime 会在没装配
    # runtime 的部署里凭空造出一个空运行时（那不是这个端点该做的事）。列表接口不带
    # 这个字段，省得为每一行都去问一次进程内状态。
    runtime = getattr(request.app.state, "agent_runtime", None)
    wire["turn_running"] = bool(runtime is not None and runtime.turn_running(session_id))
    return wire



@router.post(
    "/sessions/{session_id}/interrupt",
    summary="中断该会话正在执行的一轮",
    responses=error_responses(
        "AGENT_DISABLED", "AGENT_SESSION_NOT_FOUND", "UNAUTHORIZED"
    ),
    dependencies=[Depends(require_auth)],
)
async def interrupt_session_turn(
    session_id: str,
    request: Request,
    session: AsyncSession = Depends(get_session),
) -> dict[str, Any]:
    """中断该会话正在执行的一轮。

    幂等：没有活动轮次时返回 200 且 ``interrupted=false``。只中断这一轮，
    不影响流水线与队列。
    """
    runtime = _agent_runtime(request)
    if await AgentSessionRepository(session).get(session_id) is None:
        raise AppError(ErrorCode.AGENT_SESSION_NOT_FOUND, "Agent 会话不存在，请刷新列表后重试")
    interrupted = await runtime.interrupt_turn(session_id)
    return {
        "session_id": session_id,
        "interrupted": interrupted,
        "turn_running": runtime.turn_running(session_id),
    }


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
        raise AppError(ErrorCode.AGENT_SESSION_NOT_FOUND, "Agent 会话不存在，请刷新列表后重试")
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
        raise AppError(ErrorCode.AGENT_SESSION_NOT_FOUND, "Agent 会话不存在，请刷新列表后重试")
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
    _broadcast_grant_change(request, session_id, "atomic_grant_changed")
    return Response(status_code=status.HTTP_204_NO_CONTENT)


@router.delete(
    "/sessions/{session_id}/copilot-grant",
    status_code=status.HTTP_204_NO_CONTENT,
    summary="撤销 Agent 会话的作业授权",
    description=(
        "把该会话生效中的作业授权标 `revoked`（规格 §9.6），前端授权条的「撤销」入口。\n\n"
        "幂等：没有生效中的授权同样返回 204。"
    ),
    responses=error_responses(
        "AGENT_SESSION_NOT_FOUND", "SERVICE_UNAVAILABLE", "UNAUTHORIZED"
    ),
    dependencies=[Depends(require_auth)],
)
async def revoke_copilot_grant(session_id: str, request: Request) -> Response:
    async with _confirmation_service(request).session_factory() as session:
        exists = await AgentSessionRepository(session).get(session_id)
    if exists is None:
        raise AppError(ErrorCode.AGENT_SESSION_NOT_FOUND, "Agent 会话不存在，请刷新列表后重试")
    await _grant_service(request).revoke(session_id)
    _broadcast_grant_change(request, session_id, "copilot_grant_changed")
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
        raise AppError(ErrorCode.NOT_FOUND, "Agent 审计记录不存在，请刷新列表后重试")
    return audit_wire(audit)
