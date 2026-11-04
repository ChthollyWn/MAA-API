"""记忆端点（M79 规格 §4.3）：列表、审核、修改与删除。

四条操作对应 :class:`~maa_api.agent.memory.service.MemoryService` 的四个方法；
状态机（只有 ``proposed`` 能审核、只有 ``active`` 能修改、批准时落实取代关系）都在
服务层，路由只做请求体形状、分页与 404 / 409 的出口。错误码沿用 G1 已预置的
``MEMORY_NOT_FOUND``（404）与 ``MEMORY_NOT_PROPOSED``（409）。

``GET`` 不传 ``status`` 时返回全部状态（规格 §4.3）：前端一次读全，客户端按
「待审核 / 生效中 / 已归档」分组。库里的时间是不带时区的 UTC，出参在
:func:`~maa_api.api.schemas.agent_v3.memory_out` 的序列化器里补成 ``...Z``。
"""

from __future__ import annotations

import logging
from typing import Any

from fastapi import APIRouter, Depends, Path, Query, Request, Response, status

from maa_api.agent.memory.service import MemoryService
from maa_api.api.deps import require_auth
from maa_api.api.errors import error_responses
from maa_api.api.schemas.agent_v3 import (
    MemoryOut,
    MemoryPage,
    MemoryReviewRequest,
    MemoryUpdateRequest,
    memory_out,
)
from maa_api.domain.errors import AppError, ErrorCode

__all__ = ["router"]

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/agent/memories", tags=["agent"])

#: 列表分页上限；与 ``GET /api/agent/runs`` 同口径。
MAX_PAGE_SIZE = 200


def _service(request: Request) -> MemoryService:
    service = getattr(request.app.state, "memory_service", None)
    if service is None:
        raise AppError(
            ErrorCode.SERVICE_UNAVAILABLE, "记忆服务还在启动中，请稍后重试"
        )
    return service


@router.get(
    "",
    response_model=MemoryPage,
    summary="列出长期记忆",
    responses=error_responses(
        "INVALID_PAGINATION", "SERVICE_UNAVAILABLE", "UNAUTHORIZED"
    ),
    dependencies=[Depends(require_auth)],
)
async def list_memories(
    request: Request,
    status_filter: str | None = Query(
        None,
        alias="status",
        description="按状态过滤（proposed/active/rejected/superseded/expired）；不传返回全部状态",
    ),
    kind: str | None = Query(None, description="按种类过滤（semantic/episodic/procedural）"),
    q: str | None = Query(None, description="在正文与分类键里做子串匹配"),
    page: int = Query(1, description="页码，从 1 开始"),
    size: int = Query(20, description="每页条数，1–200"),
) -> MemoryPage:
    if page < 1 or size < 1 or size > MAX_PAGE_SIZE:
        raise AppError(
            ErrorCode.INVALID_PAGINATION,
            f"page 不能小于 1，size 需在 1–{MAX_PAGE_SIZE} 之间",
        )
    result = await _service(request).list_memories(
        status=status_filter, kind=kind, q=q, page=page, size=size
    )
    return MemoryPage(
        items=[memory_out(memory) for memory in result.items],
        total=result.total,
        page=result.page,
        size=result.size,
    )


@router.post(
    "/{memory_id}/review",
    response_model=MemoryOut,
    summary="审核一条待审记忆",
    responses=error_responses(
        "MEMORY_NOT_FOUND", "MEMORY_NOT_PROPOSED", "UNAUTHORIZED"
    ),
    dependencies=[Depends(require_auth)],
)
async def review_memory(
    payload: MemoryReviewRequest,
    request: Request,
    memory_id: str = Path(description="记忆 id"),
) -> MemoryOut:
    """批准或拒绝一条 ``proposed`` 记忆。

    ``approved=true`` 改为 ``active``，带 ``content`` 时顺手改写正文；``false``
    改为 ``rejected``。已经不在 ``proposed`` 返回 409 ``MEMORY_NOT_PROPOSED``。
    取代关系不由请求体传入：批准时若 ``supersedes_id`` 指向一条生效中的记忆，
    服务层把那条改为 ``superseded``。
    """
    memory = await _service(request).review(
        memory_id, payload.approved, payload.content
    )
    logger.info(
        "记忆审核：id=%s approved=%s status=%s", memory.id, payload.approved, memory.status
    )
    return memory_out(memory)


@router.put(
    "/{memory_id}",
    response_model=MemoryOut,
    summary="修改一条生效中的记忆",
    responses=error_responses(
        "MEMORY_NOT_FOUND", "INVALID_PARAMETER", "UNAUTHORIZED"
    ),
    dependencies=[Depends(require_auth)],
)
async def update_memory(
    payload: MemoryUpdateRequest,
    request: Request,
    memory_id: str = Path(description="记忆 id"),
) -> MemoryOut:
    """改一条 ``active`` 记忆的 ``content``、``subject`` 或 ``expires_at``。

    不存在的记忆返回 404 ``MEMORY_NOT_FOUND``；不是生效中或一个字段都没传时由
    服务层报 400 ``INVALID_PARAMETER``。``expires_at`` 传 ``null`` 表示清空。
    """
    fields: dict[str, Any] = {}
    for name in ("content", "subject", "expires_at"):
        if name in payload.model_fields_set:
            fields[name] = getattr(payload, name)
    memory = await _service(request).update_memory(memory_id, **fields)
    return memory_out(memory)


@router.delete(
    "/{memory_id}",
    status_code=status.HTTP_204_NO_CONTENT,
    response_class=Response,
    summary="删除一条记忆",
    responses=error_responses("MEMORY_NOT_FOUND", "UNAUTHORIZED"),
    dependencies=[Depends(require_auth)],
)
async def delete_memory(
    request: Request,
    memory_id: str = Path(description="记忆 id"),
) -> Response:
    """物理删除一条记忆（03 §14 不自动删，用户在这里删）；不存在报 404。"""
    await _service(request).delete_memory(memory_id)
    logger.info("记忆删除：id=%s", memory_id)
    return Response(status_code=status.HTTP_204_NO_CONTENT)
