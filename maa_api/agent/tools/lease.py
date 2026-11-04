"""``hold_device`` / ``release_device``：跨运行的显式设备租约（M71 规格 §4.2）。

两个工具只服务工单里的代理：``ctx.origin`` 为空或没有工单时拒绝。租约归工单所有
（同一工单的子代理重入），服务从 ``app.state.device_lease_service`` 取；服务尚未
装配时按 fail closed 报 ``DEVICE_LEASE_BUSY``。两者都不进 REST 目录，``device``
固定 ``none``——它们自己管理租约，不能再被 ``LeaseMiddleware`` 包一层。
"""

from __future__ import annotations

from typing import Any

from pydantic import BaseModel, ConfigDict, Field

from maa_api.agent.lease import LeaseHolder, _wire_time
from maa_api.agent.registry import (
    ToolContext,
    ToolDefinition,
    ToolOrigin,
    ToolRegistry,
    ToolRisk,
)
from maa_api.domain.errors import AppError, ErrorCode

__all__ = [
    "HoldDeviceParams",
    "ReleaseDeviceParams",
    "hold_device",
    "lease_tools",
    "release_device",
]

HOLD_DEVICE_DESCRIPTION = (
    "需要连续占用设备做多步操作（先导航再提交、连续几次点击）时先调用，写明用途。"
    "设备在 release_device、工单结束、超时或被用户接管时释放。"
)
RELEASE_DEVICE_DESCRIPTION = (
    "连续的设备操作做完，或者要长时间等用户时调用，把设备让出来。"
)


class HoldDeviceParams(BaseModel):
    """``hold_device`` 的参数（规格 §4.2）。"""

    model_config = ConfigDict(extra="forbid")

    purpose: str = Field(min_length=1, max_length=120)


class ReleaseDeviceParams(BaseModel):
    """``release_device`` 没有参数（规格 §4.2）。"""

    model_config = ConfigDict(extra="forbid")


def lease_tools() -> list[ToolDefinition]:
    """两个设备租约工具；调用方负责注册（``build_registry()`` 接在 M70 之后）。"""
    return [
        ToolDefinition(
            name="hold_device",
            group="lease",
            risk=ToolRisk.SAFE,
            description=HOLD_DEVICE_DESCRIPTION,
            params_model=HoldDeviceParams,
            handler=hold_device,
            read_only=False,
            rest_visible=False,
            device="none",
        ),
        ToolDefinition(
            name="release_device",
            group="lease",
            risk=ToolRisk.SAFE,
            description=RELEASE_DEVICE_DESCRIPTION,
            params_model=ReleaseDeviceParams,
            handler=release_device,
            read_only=False,
            rest_visible=False,
            device="none",
        ),
    ]


def _ticket_origin(ctx: ToolContext) -> ToolOrigin:
    """只有工单里的代理能持有设备；总控与 REST 调用没有 origin。"""
    origin = ctx.origin
    if origin is None or not origin.ticket_id:
        raise AppError(ErrorCode.INVALID_PARAMETER, "只有工单里的代理能占用设备")
    return origin


def _lease_service(ctx: ToolContext) -> Any:
    """取 ``app.state`` 上的租约服务；服务缺失时 fail closed。"""
    state = getattr(getattr(ctx.request, "app", None), "state", None)
    service = getattr(state, "device_lease_service", None)
    if service is None:
        raise AppError(
            ErrorCode.DEVICE_LEASE_BUSY,
            "设备租约服务尚未启动",
            {"reason": "lease_service_unavailable"},
        )
    return service


def _holder(origin: ToolOrigin) -> LeaseHolder:
    return LeaseHolder(
        ticket_id=origin.ticket_id, run_id=origin.run_id, agent=origin.agent
    )


async def hold_device(
    params: HoldDeviceParams, ctx: ToolContext
) -> dict[str, Any]:
    """显式取设备租约；同一工单重入即续期。"""
    origin = _ticket_origin(ctx)
    service = _lease_service(ctx)
    row = await service.acquire(_holder(origin), purpose=params.purpose, explicit=True)
    return {
        "held": True,
        "lease_id": row.lease_id,
        "expires_at": _wire_time(row.expires_at),
        "explicit": True,
    }


async def release_device(
    _params: ReleaseDeviceParams, ctx: ToolContext
) -> dict[str, Any]:
    """释放本工单持有的租约；当前没有持有时报 ``DEVICE_LEASE_NOT_HELD``。"""
    origin = _ticket_origin(ctx)
    service = _lease_service(ctx)
    if not await service.release(_holder(origin)):
        raise AppError(ErrorCode.DEVICE_LEASE_NOT_HELD, "当前没有占用设备")
    return {"released": True}
