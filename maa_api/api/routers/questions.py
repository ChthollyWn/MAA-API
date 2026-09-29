"""Agent 主动提问端点（M21，规格 §3.4 / docs/05 §6.18）。

四个操作与 :class:`~maa_api.agent.questions.QuestionService` 一一对应，路由里不
做状态机判断 —— 唯一属于 REST 层的判断是分页范围（``page`` / ``size``）：它和其它
列表接口同一口径，违反时是 ``400 INVALID_PAGINATION``。

沿用 ``agent`` tag（与 M18 的知识库端点同一取舍）：这几个端点是 Agent 会话交互的
一部分，``operationId`` 因此仍是 ``agent_*``，前端生成客户端不必多一套命名空间。
"""

from __future__ import annotations

from fastapi import APIRouter, Depends, Query, Request
from pydantic import BaseModel, ConfigDict

from maa_api.api.deps import require_auth
from maa_api.api.errors import error_responses
from maa_api.domain.enums import QuestionStatus
from maa_api.domain.errors import AppError, ErrorCode

__all__ = ["AnswerRequest", "router"]

router = APIRouter(prefix="/api/agent/questions", tags=["agent"])


class AnswerRequest(BaseModel):
    """``POST /api/agent/questions/{id}`` 的入参。

    ``selected`` 用**必填的字符串数组**而不是 ``min_length=1``：空数组是「没有选择」
    这个业务错误（``400 QUESTION_SELECTION_INVALID``，规格 §3.4），不是字段级校验
    失败；把约束写进模型只会让同一种错误在两个状态码之间漂移。
    """

    model_config = ConfigDict(extra="forbid")

    selected: list[str]


def _service(request: Request):
    service = getattr(request.app.state, "question_service", None)
    if service is None:
        raise AppError(ErrorCode.AGENT_DISABLED, "提问服务尚未启动")
    return service


@router.get(
    "",
    summary="分页读取 Agent 提问",
    responses=error_responses("INVALID_PAGINATION", "UNAUTHORIZED"),
    dependencies=[Depends(require_auth)],
)
async def list_questions(
    request: Request,
    session_id: str | None = Query(default=None),
    status: QuestionStatus | None = Query(default=QuestionStatus.PENDING),
    page: int = Query(default=1),
    size: int = Query(default=20),
) -> dict:
    if page < 1 or size < 1 or size > 200:
        raise AppError(ErrorCode.INVALID_PAGINATION, "page 必须大于 0，size 必须为 1–200")
    return await _service(request).list(
        session_id=session_id, status=status, page=page, size=size
    )


@router.get(
    "/{question_id}",
    summary="读取单条 Agent 提问",
    responses=error_responses("QUESTION_NOT_FOUND", "UNAUTHORIZED"),
    dependencies=[Depends(require_auth)],
)
async def get_question(question_id: str, request: Request) -> dict:
    return await _service(request).get(question_id)


@router.post(
    "/{question_id}",
    summary="提交 Agent 提问的选择",
    responses=error_responses(
        "QUESTION_NOT_FOUND",
        "QUESTION_ALREADY_ANSWERED",
        "QUESTION_EXPIRED",
        "QUESTION_SELECTION_INVALID",
        "UNAUTHORIZED",
    ),
    dependencies=[Depends(require_auth)],
)
async def answer_question(
    question_id: str,
    payload: AnswerRequest,
    request: Request,
) -> dict:
    return await _service(request).answer(
        question_id, selected=payload.selected, answered_by="web"
    )


@router.post(
    "/{question_id}/skip",
    summary="跳过 Agent 提问",
    responses=error_responses(
        "QUESTION_NOT_FOUND",
        "QUESTION_ALREADY_ANSWERED",
        "QUESTION_EXPIRED",
        "UNAUTHORIZED",
    ),
    dependencies=[Depends(require_auth)],
)
async def skip_question(question_id: str, request: Request) -> dict:
    return await _service(request).skip(question_id, answered_by="web")
