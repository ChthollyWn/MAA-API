"""计划端点（M75 规格 §4.3、§4.5）。

两个端点都围绕 Worker 会话当前那份未结束计划：

- ``GET /api/agent/sessions/{session_id}/plan``：当前计划，没有则为 ``null``；
- ``POST /api/agent/sessions/{session_id}/plan/approval``：批准（→ ``active``）或
  退回（→ ``draft``）。批准/退回后往 Worker 收件箱写一条 ``system`` 并唤醒该会话，
  Worker 醒来后在下一轮里按新的计划状态继续。

「不在待批准」由服务层判定并抛 409 ``PLAN_NOT_AWAITING_APPROVAL``；会话不存在是
404 ``AGENT_SESSION_NOT_FOUND``，会话存在但没有未结束计划是 404
``PLAN_NOT_FOUND``。路由只做请求形状与收件箱通知，不复制状态机。
"""

from __future__ import annotations

import logging
from typing import Any

from fastapi import APIRouter, Depends, Path, Request
from sqlalchemy.ext.asyncio import AsyncSession

from maa_api.api.deps import get_session, require_auth
from maa_api.api.errors import error_responses
from maa_api.api.schemas.agent_v3 import PlanApprovalRequest, PlanOut, plan_out
from maa_api.db.repositories.agent import AgentSessionRepository
from maa_api.domain.enums import InboxKind
from maa_api.domain.errors import AppError, ErrorCode

__all__ = ["router"]

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/agent", tags=["agent"])

#: 退回但没写原因时给 Worker 的注释。
DEFAULT_REJECT_COMMENT = "未说明原因"


async def _require_session(session: AsyncSession, session_id: str) -> None:
    """会话不存在（或已删除）一律 404 ``AGENT_SESSION_NOT_FOUND``。"""
    stored = await AgentSessionRepository(session).get(session_id)
    if stored is None:
        raise AppError(ErrorCode.AGENT_SESSION_NOT_FOUND, "Agent 会话不存在")


def _plan_service(request: Request) -> Any:
    service = getattr(request.app.state, "plan_service", None)
    if service is None:
        raise AppError(ErrorCode.SERVICE_UNAVAILABLE, "计划服务尚未启动")
    return service


async def _require_plan(request: Request, session_id: str) -> Any:
    plan = await _plan_service(request).active_for_session(session_id)
    if plan is None:
        raise AppError(ErrorCode.PLAN_NOT_FOUND, "这个会话没有进行中的计划")
    return plan


async def _notify_worker(request: Request, plan: Any, text: str) -> None:
    """写一条 Worker ``system`` 收件箱并唤醒它；通知失败不撤销已落库的批准。"""
    inbox = getattr(request.app.state, "inbox_service", None)
    if inbox is not None:
        try:
            await inbox.put(
                str(plan.session_id),
                InboxKind.SYSTEM,
                thread_key=f"ticket:{plan.ticket_id}" if plan.ticket_id else None,
                ref_id=f"plan:{plan.id}",
                payload={
                    "text": text,
                    "passive": False,
                    "origin": "user",
                    "ticket_id": plan.ticket_id,
                },
            )
        except Exception:  # noqa: BLE001 - 批准已经落库，通知失败只记日志
            logger.warning(
                "计划通知写入 Worker 收件箱失败：plan=%s session=%s",
                plan.id,
                plan.session_id,
                exc_info=True,
            )
    worker = getattr(request.app.state, "worker_service", None)
    if worker is not None:
        try:
            worker.wake(str(plan.session_id))
        except Exception:  # noqa: BLE001 - 唤醒是尽力而为
            logger.warning("唤醒 Worker 失败：session=%s", plan.session_id, exc_info=True)


@router.get(
    "/sessions/{session_id}/plan",
    response_model=PlanOut | None,
    summary="读 Worker 会话当前未结束的计划",
    responses=error_responses("AGENT_SESSION_NOT_FOUND", "SERVICE_UNAVAILABLE", "UNAUTHORIZED"),
    dependencies=[Depends(require_auth)],
)
async def get_plan(
    request: Request,
    session_id: str = Path(..., description="Worker 会话 id"),
    session: AsyncSession = Depends(get_session),
) -> PlanOut | None:
    """当前计划；没有未结束计划时返回 ``null``。"""
    await _require_session(session, session_id)
    plan = await _plan_service(request).active_for_session(session_id)
    return None if plan is None else plan_out(plan)


@router.post(
    "/sessions/{session_id}/plan/approval",
    response_model=PlanOut,
    summary="批准或退回待批准的计划",
    responses=error_responses(
        "AGENT_SESSION_NOT_FOUND",
        "PLAN_NOT_FOUND",
        "PLAN_NOT_AWAITING_APPROVAL",
        "SERVICE_UNAVAILABLE",
        "UNAUTHORIZED",
    ),
    dependencies=[Depends(require_auth)],
)
async def approve_plan(
    request: Request,
    payload: PlanApprovalRequest,
    session_id: str = Path(..., description="Worker 会话 id"),
    session: AsyncSession = Depends(get_session),
) -> PlanOut:
    """``approved=true`` 转 ``active``，``false`` 退回 ``draft``；都写收件箱并唤醒。"""
    await _require_session(session, session_id)
    service = _plan_service(request)
    plan = await _require_plan(request, session_id)
    if payload.approved:
        updated = await service.approve(plan.id)
        text = f"用户已批准计划 {plan.id}"
    else:
        comment = (payload.reason or "").strip() or DEFAULT_REJECT_COMMENT
        updated = await service.reject(plan.id, reason=comment)
        text = f"用户退回了计划 {plan.id}：{comment}"
    await _notify_worker(request, updated, text)
    return plan_out(updated)
