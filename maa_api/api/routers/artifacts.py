"""产物端点（M75 规格 §4.3、§4.5）。

四个端点都从 ``app.state.artifact_service`` 取服务，服务未装配时返回 503
``SERVICE_UNAVAILABLE``：

- ``GET /api/agent/artifacts``：按工单 / 会话 / 种类分页列元数据，**不含正文**；
- ``GET /api/agent/artifacts/{artifact_id}``：元数据 + 正文，最多
  :data:`ARTIFACT_DETAIL_MAX_CHARS` 字，超出时 ``truncated=true``；
- ``GET /api/agent/artifacts/{artifact_id}/content``：原始字节，``Content-Type``
  取产物的 ``mime``；
- ``PUT /api/agent/artifacts/{artifact_id}/pin``：置顶或取消置顶。

列表的分页边界在路由层判断（400 ``INVALID_PAGINATION``），产物不存在统一由服务层
抛 404 ``ARTIFACT_NOT_FOUND``，路由不重复判断。控制台登录用户可以读任意产物：
Worker 工具 ``read_artifact`` 的工单范围只约束模型，不约束 HTTP（spec §4.3）。
"""

from __future__ import annotations

from typing import Any

from fastapi import APIRouter, Depends, Path, Query, Request, Response

from maa_api.api.deps import require_auth
from maa_api.api.errors import error_responses
from maa_api.api.schemas.agent_v3 import (
    ArtifactDetailOut,
    ArtifactOut,
    ArtifactPage,
    ArtifactPinRequest,
    artifact_detail_out,
    artifact_out,
)
from maa_api.domain.enums import ArtifactKind
from maa_api.domain.errors import AppError, ErrorCode

__all__ = ["ARTIFACT_DETAIL_MAX_CHARS", "router"]

#: 详情端点返回正文的上限（spec §4.3：64 KiB）；超出截断并置 ``truncated``。
ARTIFACT_DETAIL_MAX_CHARS = 64 * 1024

router = APIRouter(prefix="/api/agent/artifacts", tags=["agent"])


def _artifact_service(request: Request) -> Any:
    service = getattr(request.app.state, "artifact_service", None)
    if service is None:
        raise AppError(ErrorCode.SERVICE_UNAVAILABLE, "产物服务尚未启动")
    return service


@router.get(
    "",
    response_model=ArtifactPage,
    summary="列出产物元数据",
    responses=error_responses(
        "INVALID_PAGINATION", "SERVICE_UNAVAILABLE", "UNAUTHORIZED"
    ),
    dependencies=[Depends(require_auth)],
)
async def list_artifacts(
    request: Request,
    ticket_id: str | None = Query(None, description="只看这个工单的产物"),
    session_id: str | None = Query(None, description="只看这个会话的产物"),
    kind: ArtifactKind | None = Query(None, description="只看这种产物"),
    page: int = Query(1, description="页码，从 1 开始"),
    size: int = Query(20, description="每页条数，1–200"),
) -> ArtifactPage:
    """按创建时间倒序分页；列表只返回元数据，不带正文。"""
    if page < 1 or size < 1 or size > 200:
        raise AppError(
            ErrorCode.INVALID_PAGINATION, "page 不能小于 1，size 需在 1–200 之间"
        )
    result = await _artifact_service(request).list(
        ticket_id=ticket_id,
        session_id=session_id,
        kind=kind,
        page=page,
        size=size,
    )
    return ArtifactPage(
        items=[artifact_out(item) for item in result.items],
        total=result.total,
        page=result.page,
        size=result.size,
    )


@router.get(
    "/{artifact_id}",
    response_model=ArtifactDetailOut,
    summary="读取产物元数据与正文",
    responses=error_responses("ARTIFACT_NOT_FOUND", "SERVICE_UNAVAILABLE", "UNAUTHORIZED"),
    dependencies=[Depends(require_auth)],
)
async def get_artifact(
    request: Request,
    artifact_id: str = Path(min_length=1, max_length=36, description="产物 id"),
) -> ArtifactDetailOut:
    """正文最多 64 KiB；超出时只返回开头并置 ``truncated=true``。"""
    detail = await _artifact_service(request).get(
        artifact_id, max_chars=ARTIFACT_DETAIL_MAX_CHARS
    )
    return artifact_detail_out(
        detail.artifact, content=detail.content, truncated=detail.truncated
    )


@router.get(
    "/{artifact_id}/content",
    response_class=Response,
    summary="下载产物原始字节",
    responses=error_responses("ARTIFACT_NOT_FOUND", "SERVICE_UNAVAILABLE", "UNAUTHORIZED"),
    dependencies=[Depends(require_auth)],
)
async def download_artifact(
    request: Request,
    artifact_id: str = Path(min_length=1, max_length=36, description="产物 id"),
) -> Response:
    """原样返回字节；``Content-Type`` 取产物的 ``mime``。"""
    data, mime = await _artifact_service(request).download(artifact_id)
    return Response(content=data, media_type=mime)


@router.put(
    "/{artifact_id}/pin",
    response_model=ArtifactOut,
    summary="置顶或取消置顶产物",
    responses=error_responses("ARTIFACT_NOT_FOUND", "SERVICE_UNAVAILABLE", "UNAUTHORIZED"),
    dependencies=[Depends(require_auth)],
)
async def pin_artifact(
    request: Request,
    payload: ArtifactPinRequest,
    artifact_id: str = Path(min_length=1, max_length=36, description="产物 id"),
) -> ArtifactOut:
    """置顶的 ``spill`` 不会被每天 04:00 的保留任务删除。"""
    artifact = await _artifact_service(request).pin(
        artifact_id, pinned=payload.pinned
    )
    return artifact_out(artifact)
