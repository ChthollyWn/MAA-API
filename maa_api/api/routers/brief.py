"""每日简报端点（M84 规格 §4.3、§4.5）。

``POST /api/agent/brief/run`` 立即生成一份每日简报。响应模型写在本文件里
（04 §1 允许，形状会进 OpenAPI 与前端生成类型）；消费类端点（消耗汇总）已随
消耗护栏一并删除。服务未装配（lifespan 还在启动）时统一回 503
``SERVICE_UNAVAILABLE``，与其它路由同款。
"""

from __future__ import annotations

from typing import Any

from fastapi import APIRouter, Depends, Request, status
from pydantic import BaseModel

from maa_api.api.deps import require_auth
from maa_api.api.errors import error_responses
from maa_api.domain.errors import AppError, ErrorCode

__all__ = [
    "BriefRunResult",
    "router",
]

router = APIRouter(prefix="/api/agent", tags=["agent"])


class BriefRunResult(BaseModel):
    """``POST /api/agent/brief/run`` 的响应体（§4.3、§4.5.9）。"""

    ticket_id: str
    artifact_id: str


def _brief_service(request: Request) -> Any:
    service = getattr(request.app.state, "brief_service", None)
    if service is None:
        raise AppError(
            ErrorCode.SERVICE_UNAVAILABLE, "简报服务还在启动中，请稍后重试"
        )
    return service


def _require_ticket_service(request: Request) -> None:
    """``run_now`` 要建 analyst 工单；缺工单服务按服务未装配回 503。"""

    if getattr(request.app.state, "ticket_service", None) is None:
        raise AppError(
            ErrorCode.SERVICE_UNAVAILABLE, "工单服务还在启动中，请稍后重试"
        )


@router.post(
    "/brief/run",
    response_model=BriefRunResult,
    status_code=status.HTTP_202_ACCEPTED,
    summary="立即生成每日简报",
    responses=error_responses("SERVICE_UNAVAILABLE", "UNAUTHORIZED"),
    dependencies=[Depends(require_auth)],
)
async def run_brief(request: Request) -> BriefRunResult:
    """建一份简报产物与 analyst 工单并返回两个 id（202，不受一天一张限制）。"""

    service = _brief_service(request)
    _require_ticket_service(request)
    result = await service.run_now()
    return BriefRunResult(
        ticket_id=str(result.ticket_id), artifact_id=str(result.artifact_id)
    )
