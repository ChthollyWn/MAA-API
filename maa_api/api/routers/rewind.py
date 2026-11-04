"""会话回溯与工单分叉端点（M80 规格 §4.3）。

两条端点都从 ``app.state.rewind_service`` 取启动时装配好的
:class:`~maa_api.agent.context.rewind.RewindService`；服务尚未装配（进程启动中、
或测试只装了路由）返回 503 ``AGENT_DISABLED``，与工单路由同口径。

回溯把会话截回到某条消息，成功或 ``dry_run`` 都是 200；目标不合法、会话不可回溯
由服务层抛 400 / 404 / 409。分叉建出新工单后返回 201 的 ``TicketDetail``，响应头
``Location`` 指向新工单。
"""

from __future__ import annotations

from typing import Any

from fastapi import APIRouter, Depends, Path, Request, Response
from sqlalchemy.ext.asyncio import AsyncSession

from maa_api.agent.context.rewind import RewindResult, RewindService
from maa_api.api.deps import get_session, require_auth
from maa_api.api.errors import error_responses
from maa_api.api.routers.tickets import (
    TicketDetail,
    forked_from_of,
    ticket_detail_out,
)
from maa_api.api.schemas.agent_v3 import (
    ForkRequest,
    RewindRequest,
    RewindResultOut,
    RewindSideEffectOut,
)
from maa_api.domain.errors import AppError, ErrorCode

__all__ = ["router"]

router = APIRouter(prefix="/api/agent", tags=["agent"])


def _service(request: Request) -> RewindService:
    """取回溯服务；装配层还没设置时返回 503。"""
    service = getattr(request.app.state, "rewind_service", None)
    if service is None:
        raise AppError(ErrorCode.AGENT_DISABLED, "回溯服务还在启动中，请稍后重试")
    return service


def _result_out(result: RewindResult) -> RewindResultOut:
    return RewindResultOut(
        session_id=result.session_id,
        to_seq=result.to_seq,
        dry_run=result.dry_run,
        superseded=result.superseded,
        superseded_events=result.superseded_events,
        checkpoints_invalidated=result.checkpoints_invalidated,
        checkpoint_reactivated=result.checkpoint_reactivated,
        plan_abandoned=result.plan_abandoned,
        side_effects=[
            RewindSideEffectOut(
                seq=effect.seq,
                tool_name=effect.tool_name,
                audit_id=effect.audit_id,
                detail=effect.detail,
            )
            for effect in result.side_effects
        ],
        notice_message_id=result.notice_message_id,
    )


@router.post(
    "/sessions/{session_id}/rewind",
    response_model=RewindResultOut,
    summary="回到指定消息",
    responses=error_responses(
        "REWIND_TARGET_INVALID",
        "AGENT_SESSION_NOT_FOUND",
        "TICKET_ENDED",
        "RUN_ALREADY_ACTIVE",
        "AGENT_DISABLED",
        "UNAUTHORIZED",
    ),
    dependencies=[Depends(require_auth)],
)
async def rewind_session(
    payload: RewindRequest,
    request: Request,
    session_id: str = Path(min_length=1, max_length=36, description="会话 id"),
) -> RewindResultOut:
    """取代目标之后的消息、作废之后的检查点与计划；副作用逐条列出。"""
    result = await _service(request).rewind(
        session_id, payload.to_seq, dry_run=payload.dry_run
    )
    return _result_out(result)


@router.post(
    "/tickets/{ticket_id}/fork",
    status_code=201,
    response_model=TicketDetail,
    summary="从这条消息分叉工单",
    responses={
        **error_responses(
            "TICKET_NOT_FOUND",
            "REWIND_TARGET_INVALID",
            "AGENT_DISABLED",
            "UNAUTHORIZED",
        ),
        201: {
            "description": "Created",
            "headers": {
                "Location": {
                    "description": "新工单的 URI",
                    "schema": {"type": "string"},
                }
            },
        },
    },
    dependencies=[Depends(require_auth)],
)
async def fork_ticket(
    payload: ForkRequest,
    request: Request,
    response: Response,
    ticket_id: str = Path(min_length=1, max_length=36, description="源工单 id"),
    session: AsyncSession = Depends(get_session),
) -> TicketDetail:
    """复制到 ``at_seq`` 为止的历史与同一份委托，新工单从 ``open`` 开始。"""
    new_ticket_id = await _service(request).fork_ticket(ticket_id, payload.at_seq)
    response.headers["Location"] = f"/api/agent/tickets/{new_ticket_id}"
    ticket_service: Any = getattr(request.app.state, "ticket_service", None)
    if ticket_service is None:
        raise AppError(ErrorCode.AGENT_DISABLED, "工单服务还在启动中，请稍后重试")
    data = await ticket_service.detail(new_ticket_id)
    return ticket_detail_out(
        data,
        forked_from=await forked_from_of(session, data.ticket.worker_session_id),
    )
