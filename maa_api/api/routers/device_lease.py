"""设备租约端点与人工抢占 helper（M71 规格 §4.3、§4.5.3）。

两条端点都从 ``app.state.device_lease_service`` 取启动时装配好的服务；服务尚未
装配（进程启动中、或测试只装了路由）返回 503 ``AGENT_DISABLED``。时间字段由服务
的 ``wire`` 输出 ``...Z`` 字符串，这里只做响应模型校验。

:func:`preempt_device` 是五类人工操作（手动提交/重放、REST 原子操作、停止运行中
流水线、REST 调用独占设备工具、DELETE 本端点）共用的抢占入口：服务缺失时静默
跳过（T9 之前/装配完成前的人工操作行为与 v2 一致），``current`` / ``preempt``
抛异常只记日志，绝不让抢占失败影响人工操作本身。
"""

from __future__ import annotations

import logging
from typing import Any

from fastapi import APIRouter, Depends, Request, Response
from pydantic import BaseModel

from maa_api.agent.lease import PreemptReason
from maa_api.api.deps import require_auth
from maa_api.api.errors import error_responses
from maa_api.domain.errors import AppError, ErrorCode

__all__ = [
    "DeviceLeaseOut",
    "DeviceLeaseState",
    "preempt_device",
    "router",
]

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/agent/device-lease", tags=["agent"])


class DeviceLeaseOut(BaseModel):
    """当前设备租约的对外形状；与 ``DeviceLeaseService.wire`` 一一对应。"""

    device_id: str
    lease_id: str
    ticket_id: str | None
    ticket_title: str | None
    run_id: str | None
    holder_agent: str
    purpose: str
    explicit: bool
    acquired_at: str
    expires_at: str
    heartbeat_at: str


class DeviceLeaseState(BaseModel):
    """``GET`` 的响应：是否持有、租约详情与排队人数。"""

    held: bool
    lease: DeviceLeaseOut | None
    waiters: int


def _service(request: Request) -> Any:
    """取租约服务；装配层还没设置时返回 503。"""
    service = getattr(request.app.state, "device_lease_service", None)
    if service is None:
        raise AppError(
            ErrorCode.AGENT_DISABLED, "设备租约服务还在启动中，请稍后重试"
        )
    return service


async def preempt_device(
    request: Request,
    reason: PreemptReason,
    detail: str | None = None,
) -> None:
    """人工操作前抢占设备租约；服务缺失或抢占失败都不影响人工操作。"""
    service = getattr(request.app.state, "device_lease_service", None)
    if service is None:
        return
    try:
        if await service.current() is None:
            return
        await service.preempt(reason, detail=detail)
    except Exception:  # noqa: BLE001 - 抢占失败只记日志，人工操作照常进行
        logger.exception("人工操作抢占设备租约失败：reason=%s", reason)


@router.get(
    "",
    response_model=DeviceLeaseState,
    summary="读取设备租约",
    responses=error_responses("AGENT_DISABLED", "UNAUTHORIZED"),
    dependencies=[Depends(require_auth)],
)
async def get_device_lease(request: Request) -> DeviceLeaseState:
    """当前租约状态；没有租约时 ``lease`` 为 ``null``。"""
    service = _service(request)
    lease = await service.current()
    return DeviceLeaseState(
        held=lease is not None,
        lease=await service.wire(lease),
        waiters=service.waiters,
    )


@router.delete(
    "",
    status_code=204,
    response_class=Response,
    summary="人工释放设备租约",
    description=(
        "用户接管设备：删掉当前租约并给持有者留一次性抢占记号，"
        "没有租约时同样返回 204。"
    ),
    responses=error_responses("AGENT_DISABLED", "UNAUTHORIZED"),
    dependencies=[Depends(require_auth)],
)
async def release_device_lease(request: Request) -> None:
    """释放当前租约；没有租约也是 204（与 v2 的人工操作语义一致）。"""
    _service(request)
    await preempt_device(request, "manual_release")
