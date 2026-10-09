"""Agent 知识库端点（规格 §4.1）。

四个操作对应服务层的四个方法，路由里不做合并或上限判断；属于 REST 层的判断只有字段
格式与长度，约束写在请求模型里，违反时走 FastAPI 原生的 ``422 VALIDATION_ERROR``
（``details.fields`` 逐字段列表）。条目数与全文段字数不设上限，因此没有对应的错误码。

写操作各记一条服务日志（条目 id、动作）。知识库不是 Agent 工具调用，不进 Agent 审计
（规格 §4.1）。
"""

from __future__ import annotations

import logging
from typing import Literal

from fastapi import APIRouter, Depends, Path, Request, Response, status
from pydantic import BaseModel, ConfigDict, Field

from maa_api.agent.knowledge import (
    ENTRY_ID_PATTERN,
    MAX_BODY_CHARS,
    MAX_TITLE_CHARS,
    MAX_WHEN_TO_USE_CHARS,
    KnowledgeDetail,
    KnowledgeService,
    KnowledgeSummary,
    KnowledgeWrite,
)
from maa_api.api.deps import require_auth
from maa_api.api.errors import error_responses
from maa_api.domain.errors import AppError, ErrorCode

__all__ = ["KnowledgePage", "KnowledgeWriteRequest", "router"]

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/agent/knowledge", tags=["agent"])

#: ``kind`` 的取值域，与 :data:`~maa_api.agent.knowledge.KNOWLEDGE_KINDS` 一致
#: （``tests/api/test_agent_knowledge_router.py`` 钉住两者不漂移）。
KnowledgeKind = Literal["playbook", "screen", "rule", "glossary", "profile"]


class KnowledgeWriteRequest(KnowledgeWrite):
    """``PUT`` 的入参：完整的生效内容。

    继承服务层的 :class:`~maa_api.agent.knowledge.KnowledgeWrite`（字段名一致），
    在这里补上 REST 层的格式与长度约束；服务层的同名校验保留作兜底。
    """

    model_config = ConfigDict(extra="forbid")

    kind: KnowledgeKind
    title: str = Field(min_length=1, max_length=MAX_TITLE_CHARS)
    when_to_use: str = Field(min_length=1, max_length=MAX_WHEN_TO_USE_CHARS)
    body: str = Field(min_length=1, max_length=MAX_BODY_CHARS)


class KnowledgePage(BaseModel):
    """列表响应：列表项不含正文，只给字数。"""

    items: list[KnowledgeSummary]
    total: int


def _service(request: Request) -> KnowledgeService:
    service = getattr(request.app.state, "knowledge_service", None)
    if service is None:
        raise AppError(ErrorCode.SERVICE_UNAVAILABLE, "知识库功能还在启动中，请稍后重试")
    return service


@router.get(
    "",
    response_model=KnowledgePage,
    summary="读取 Agent 知识库条目",
    responses=error_responses("UNAUTHORIZED"),
    dependencies=[Depends(require_auth)],
)
async def list_knowledge(request: Request) -> KnowledgePage:
    items = await _service(request).list_entries()
    return KnowledgePage(items=items, total=len(items))


@router.get(
    "/{entry_id}",
    response_model=KnowledgeDetail,
    summary="读取 Agent 知识条目详情",
    responses=error_responses(
        "KNOWLEDGE_NOT_FOUND", "VALIDATION_ERROR", "UNAUTHORIZED"
    ),
    dependencies=[Depends(require_auth)],
)
async def get_knowledge_entry(
    request: Request,
    entry_id: str = Path(
        description="知识条目 id", pattern=ENTRY_ID_PATTERN.pattern
    ),
) -> KnowledgeDetail:
    return await _service(request).get_entry(entry_id)


@router.put(
    "/{entry_id}",
    response_model=KnowledgeDetail,
    summary="写入 Agent 知识条目",
    responses=error_responses(
        "KNOWLEDGE_BUILTIN_READONLY",
        "VALIDATION_ERROR",
        "UNAUTHORIZED",
    ),
    dependencies=[Depends(require_auth)],
)
async def put_knowledge_entry(
    payload: KnowledgeWriteRequest,
    request: Request,
    entry_id: str = Path(
        description="知识条目 id", pattern=ENTRY_ID_PATTERN.pattern
    ),
) -> KnowledgeDetail:
    """写入完整的生效内容：新建自定义条目，或修改、停用内置条目。

    内置条目的 ``kind`` 不可修改，改了返回 400 ``KNOWLEDGE_BUILTIN_READONLY``。
    """
    detail = await _service(request).put_entry(entry_id, payload)
    logger.info(
        "知识库条目写入：id=%s source=%s modified=%s enabled=%s",
        detail.id,
        detail.source,
        detail.modified,
        detail.enabled,
    )
    return detail


@router.delete(
    "/{entry_id}",
    status_code=status.HTTP_204_NO_CONTENT,
    response_class=Response,
    summary="删除 Agent 知识条目",
    responses=error_responses(
        "KNOWLEDGE_NOT_FOUND",
        "KNOWLEDGE_BUILTIN_READONLY",
        "VALIDATION_ERROR",
        "UNAUTHORIZED",
    ),
    dependencies=[Depends(require_auth)],
)
async def delete_knowledge_entry(
    request: Request,
    entry_id: str = Path(
        description="知识条目 id", pattern=ENTRY_ID_PATTERN.pattern
    ),
) -> Response:
    """删除自定义条目，或撤销对内置条目的修改（恢复默认）。

    内置条目未被修改过时返回 400 ``KNOWLEDGE_BUILTIN_READONLY``；要停用它请写入
    ``enabled=false``。
    """
    await _service(request).delete_entry(entry_id)
    logger.info("知识库条目删除：id=%s", entry_id)
    return Response(status_code=status.HTTP_204_NO_CONTENT)
