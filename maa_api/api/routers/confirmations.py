"""Human approval list, detail and resolution endpoints (docs/05 §6.16)."""

from __future__ import annotations

from fastapi import APIRouter, Depends, Query, Request
from pydantic import BaseModel, ConfigDict, Field

from maa_api.api.deps import require_auth
from maa_api.api.errors import error_responses
from maa_api.domain.enums import ConfirmationStatus
from maa_api.domain.errors import AppError, ErrorCode

router = APIRouter(prefix="/api/confirmations", tags=["confirmations"])


class ResolveRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    approved: bool
    reason: str | None = Field(default=None, max_length=4096)


def _service(request: Request):
    service = getattr(request.app.state, "confirmation_service", None)
    if service is None:
        raise AppError(ErrorCode.AGENT_DISABLED, "确认服务尚未启动")
    return service


@router.get(
    "",
    summary="分页读取人工确认请求",
    responses=error_responses("INVALID_PAGINATION", "UNAUTHORIZED"),
    dependencies=[Depends(require_auth)],
)
async def list_confirmations(
    request: Request,
    status: ConfirmationStatus | None = Query(default=ConfirmationStatus.PENDING),
    page: int = Query(default=1),
    size: int = Query(default=20),
) -> dict:
    if page < 1 or size < 1 or size > 200:
        raise AppError(ErrorCode.INVALID_PAGINATION, "page 必须大于 0，size 必须为 1–200")
    return await _service(request).list(status=status, page=page, size=size)


@router.get(
    "/{confirmation_id}",
    summary="读取人工确认详情与执行结果",
    responses=error_responses("CONFIRMATION_NOT_FOUND", "UNAUTHORIZED"),
    dependencies=[Depends(require_auth)],
)
async def get_confirmation(confirmation_id: str, request: Request) -> dict:
    return await _service(request).get(confirmation_id)


@router.post(
    "/{confirmation_id}",
    summary="批准或拒绝人工确认",
    responses=error_responses(
        "CONFIRMATION_NOT_FOUND", "CONFIRMATION_ALREADY_RESOLVED", "CONFIRMATION_EXPIRED",
        "UNAUTHORIZED",
    ),
    dependencies=[Depends(require_auth)],
)
async def resolve_confirmation(
    confirmation_id: str,
    payload: ResolveRequest,
    request: Request,
) -> dict:
    return await _service(request).resolve(
        confirmation_id,
        approved=payload.approved,
        resolved_by="web",
        reason=payload.reason,
        request=request,
    )
