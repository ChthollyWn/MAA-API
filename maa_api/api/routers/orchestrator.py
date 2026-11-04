"""总控端点（M70 规格 §4.3.1）。

五条端点都从 ``app.state.orchestrator_service`` 取启动时装配好的
:class:`~maa_api.agent.orchestrator.service.OrchestratorService`；服务尚未装配
（进程启动中、或测试只装了路由）返回 503 ``AGENT_DISABLED``，与工单路由同口径。

两条读端点是普通 JSON：状态与消息分页。两条写端点里 ``POST /messages`` 返回
SSE（先订阅 ``RunEventStream`` 再写消息，避免漏掉事件，见规格 §4.5.8）；
``POST /interrupt`` 幂等，``POST /reset`` 在运行中返回 409。
"""

from __future__ import annotations

from typing import Any

from fastapi import APIRouter, Depends, Query, Request
from sqlalchemy.ext.asyncio import AsyncSession
from starlette.responses import StreamingResponse

from maa_api.agent.orchestrator.stream import RunEventStream
from maa_api.api.deps import get_session, require_auth
from maa_api.api.errors import error_responses
from maa_api.api.schemas.agent_v3 import (
    AgentMessageCreate,
    AgentMessagePage,
    OrchestratorInterruptResult,
    OrchestratorStateModel,
    agent_message_item,
)
from maa_api.db.repositories.agent import AgentMessageRepository
from maa_api.domain.errors import AppError, ErrorCode

__all__ = ["router"]

router = APIRouter(prefix="/api/agent", tags=["agent"])

#: 两条 SSE 端点共用的响应头（与 v2 的 ``stream_session_message`` 一致）。
SSE_HEADERS = {"Cache-Control": "no-cache", "X-Accel-Buffering": "no"}
#: OpenAPI 里声明 SSE 响应体类型；不写 response_model。
SSE_RESPONSES: dict[int, dict[str, Any]] = {
    200: {"content": {"text/event-stream": {}}}
}


def _service(request: Request) -> Any:
    """取总控服务；装配层还没设置时返回 503。"""
    service = getattr(request.app.state, "orchestrator_service", None)
    if service is None:
        raise AppError(
            ErrorCode.AGENT_DISABLED, "总控服务还在启动中，请稍后重试"
        )
    return service


def _page(page: int, size: int) -> None:
    if page < 1 or size < 1 or size > 200:
        raise AppError(
            ErrorCode.INVALID_PAGINATION, "page 不能小于 1，size 需在 1–200 之间"
        )


def _state_model(state: Any) -> OrchestratorStateModel:
    return OrchestratorStateModel(
        session_id=state.session_id,
        status=state.status,
        turn_running=state.turn_running,
        pending_inbox=state.pending_inbox,
        archived_count=state.archived_count,
    )


@router.get(
    "/orchestrator",
    response_model=OrchestratorStateModel,
    summary="读取总控状态",
    responses=error_responses("AGENT_DISABLED", "UNAUTHORIZED"),
    dependencies=[Depends(require_auth)],
)
async def get_orchestrator(request: Request) -> OrchestratorStateModel:
    """当前总控会话的状态；没有活动会话时先建一个（规格 §4.3.1）。"""
    return _state_model(await _service(request).state())


@router.post(
    "/orchestrator/messages",
    response_class=StreamingResponse,
    summary="向总控发送消息并以 SSE 接收事件",
    responses={**SSE_RESPONSES, **error_responses(
        "LLM_NOT_CONFIGURED",
        "LLM_ENCRYPTION_KEY_INVALID",
        "VALIDATION_ERROR",
        "AGENT_DISABLED",
        "UNAUTHORIZED",
    )},
    dependencies=[Depends(require_auth)],
)
async def send_orchestrator_message(
    payload: AgentMessageCreate, request: Request
) -> StreamingResponse:
    """校验请求 → 订阅事件 → 写用户消息 → 返回这条消息的 SSE 流。"""
    service = _service(request)
    await service.ensure_ready()
    session = await service.ensure_session()
    stream = RunEventStream(request.app, session_id=str(session.id))
    try:
        posted = await service.post_user_message(
            payload.content,
            [mention.model_dump() for mention in payload.mentions or []],
        )
    except BaseException:
        stream.close()
        raise
    stream.track(posted.inbox_id)
    return StreamingResponse(
        stream.frames(), media_type="text/event-stream", headers=SSE_HEADERS
    )


@router.get(
    "/orchestrator/messages",
    response_model=AgentMessagePage,
    summary="分页读取当前总控会话的消息",
    responses=error_responses(
        "INVALID_PAGINATION", "AGENT_DISABLED", "UNAUTHORIZED"
    ),
    dependencies=[Depends(require_auth)],
)
async def list_orchestrator_messages(
    request: Request,
    after_seq: int | None = Query(default=None, ge=-1, description="只取 seq 更大的消息"),
    page: int = Query(default=1, description="页码，从 1 开始"),
    size: int = Query(default=20, description="每页条数，1–200"),
    session: AsyncSession = Depends(get_session),
) -> AgentMessagePage:
    _page(page, size)
    current = await _service(request).ensure_session()
    result = await AgentMessageRepository(session).list_page(
        str(current.id), after_seq=after_seq, page=page, size=size
    )
    return AgentMessagePage(
        items=[agent_message_item(item) for item in result.items],
        total=result.total,
        page=result.page,
        size=result.size,
    )


@router.post(
    "/orchestrator/interrupt",
    response_model=OrchestratorInterruptResult,
    summary="中断总控正在执行的一轮",
    responses=error_responses("AGENT_DISABLED", "UNAUTHORIZED"),
    dependencies=[Depends(require_auth)],
)
async def interrupt_orchestrator(request: Request) -> OrchestratorInterruptResult:
    """幂等：没有活动轮次时返回 200 且 ``interrupted=false``。"""
    result = await _service(request).interrupt()
    return OrchestratorInterruptResult(**result)


@router.post(
    "/orchestrator/reset",
    response_model=OrchestratorStateModel,
    summary="重新开始总控会话",
    responses=error_responses(
        "RUN_ALREADY_ACTIVE", "AGENT_DISABLED", "UNAUTHORIZED"
    ),
    dependencies=[Depends(require_auth)],
)
async def reset_orchestrator(request: Request) -> OrchestratorStateModel:
    """换一个新会话；运行中或还有未处理消息时 409。"""
    return _state_model(await _service(request).reset())
