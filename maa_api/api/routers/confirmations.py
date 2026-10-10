"""Human approval list, detail and resolution endpoints (docs/05 §6.16)."""

from __future__ import annotations

from typing import Any, Literal

from fastapi import APIRouter, Depends, Query, Request
from pydantic import BaseModel, ConfigDict, Field, field_validator

from maa_api.api.deps import require_auth
from maa_api.api.errors import error_responses
from maa_api.domain.enums import ConfirmationStatus
from maa_api.domain.errors import AppError, ErrorCode

router = APIRouter(prefix="/api/confirmations", tags=["confirmations"])


class OverridesRequest(BaseModel):
    """``grant_copilot_attempts`` 卡片上可以改的三项（规格 §9.6）。

    尝试次数、成功标准与是否允许助战都能在卡片上改；有效期只展示，**不在**这里。
    键名与取值域与 ``services/copilot/grant.py`` 的常量一一对应（1–10 /
    ``three_star`` / ``clear`` / ``fill_one`` / ``none``）。``extra="forbid"``：
    拼错的键是 422，不静默忽略 —— 用户以为改了次数而实际没改是最坏的结果。
    """

    model_config = ConfigDict(extra="forbid")

    attempts: int | None = Field(default=None, ge=1, le=10)
    success: Literal["three_star", "clear"] | None = None
    support: Literal["fill_one", "none"] | None = None

    @field_validator("attempts", mode="before")
    @classmethod
    def _attempts_is_an_int(cls, value: Any) -> Any:
        """次数不接受 ``"3"`` 这类字符串（pydantic 默认会把它转成整数）。"""
        if value is None or isinstance(value, bool) or isinstance(value, int):
            return value
        raise ValueError("attempts 必须是 1–10 之间的整数")



class ResolveRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    approved: bool
    reason: str | None = Field(default=None, max_length=4096)
    #: 只对 ``grant_copilot_attempts`` 有意义；其余确认动作原样忽略。
    overrides: OverridesRequest | None = None


def _service(request: Request):
    service = getattr(request.app.state, "confirmation_service", None)
    if service is None:
        raise AppError(ErrorCode.AGENT_DISABLED, "确认功能还在启动中，请稍后重试")
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
        raise AppError(ErrorCode.INVALID_PAGINATION, "page 不能小于 1，size 需在 1–200 之间")
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
        "SERVICE_UNAVAILABLE", "UNAUTHORIZED",
    ),
    dependencies=[Depends(require_auth)],
)
async def resolve_confirmation(
    confirmation_id: str,
    payload: ResolveRequest,
    request: Request,
) -> dict:
    """批准或拒绝一条确认。

    ``overrides`` 只有 ``grant_copilot_attempts`` 用得上（规格 §9.6）：用户在作业授权
    卡片上改的尝试次数 / 成功标准 / 是否允许助战，批准时写进授权记录。
    """
    return await _service(request).resolve(
        confirmation_id,
        approved=payload.approved,
        resolved_by="web",
        reason=payload.reason,
        request=request,
        overrides=(
            payload.overrides.model_dump(exclude_none=True)
            if payload.overrides is not None
            else None
        ),
    )
