"""Immediate MaaCore actions guarded against pipeline interference (M5)."""

from __future__ import annotations

import logging

from fastapi import APIRouter, Depends
from pydantic import BaseModel, ConfigDict, Field
from sqlalchemy.ext.asyncio import AsyncSession

from maa_api.api.deps import (
    get_core_registry,
    get_pipeline_runner,
    get_session,
    require_auth,
)
from maa_api.api.errors import error_responses
from maa_api.db.repositories.pipeline import PipelineRepository
from maa_api.domain.errors import AppError, ErrorCode

__all__ = ["router"]

logger = logging.getLogger(__name__)
router = APIRouter()


class ClickRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    x: int = Field(ge=0)
    y: int = Field(ge=0)
    force: bool = False


class ForceRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    force: bool = False


async def _ensure_atomic_allowed(
    *,
    operation: str,
    force: bool,
    session: AsyncSession,
) -> None:
    current = await PipelineRepository(session).current()
    if current is not None and not force:
        raise AppError(
            ErrorCode.PIPELINE_ALREADY_RUNNING,
            "流水线运行期间不能执行原子操作；如确需介入，请显式设置 force=true",
            {
                "pipeline_id": current.id,
                "operation": operation,
            },
        )
    if current is not None and force:
        logger.warning(
            "强制原子操作介入运行中的流水线：pipeline_id=%s operation=%s",
            current.id,
            operation,
        )


@router.post(
    "/api/device/click",
    summary="执行单点点击",
    responses=error_responses(
        "CORE_NOT_READY", "PIPELINE_ALREADY_RUNNING", "UNAUTHORIZED"
    ),
    dependencies=[Depends(require_auth)],
    tags=["device"],
)
async def click_device(
    payload: ClickRequest,
    runner=Depends(get_pipeline_runner),
    registry=Depends(get_core_registry),
    session: AsyncSession = Depends(get_session),
):
    async with runner.operation_lock:
        await _ensure_atomic_allowed(
            operation="click", force=payload.force, session=session
        )
        await registry.get().click(payload.x, payload.y, block=True)
    return {"ok": True, "backend": "core", "x": payload.x, "y": payload.y}


@router.post(
    "/api/core/back_to_home",
    summary="返回游戏主界面",
    responses=error_responses(
        "CORE_NOT_READY", "PIPELINE_ALREADY_RUNNING", "UNAUTHORIZED"
    ),
    dependencies=[Depends(require_auth)],
    tags=["core"],
)
async def back_to_home(
    payload: ForceRequest | None = None,
    runner=Depends(get_pipeline_runner),
    registry=Depends(get_core_registry),
    session: AsyncSession = Depends(get_session),
):
    force = payload.force if payload is not None else False
    async with runner.operation_lock:
        await _ensure_atomic_allowed(
            operation="back_to_home", force=force, session=session
        )
        ok = await registry.get().back_to_home()
    return {"ok": bool(ok), "backend": "core"}
