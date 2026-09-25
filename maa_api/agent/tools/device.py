"""Agent tools for device status, discovery, and reconnection."""

from __future__ import annotations

from typing import Any

from pydantic import BaseModel, ConfigDict

from maa_api.agent.registry import ToolContext, ToolDefinition, ToolRegistry, ToolRisk
from maa_api.domain.errors import AppError, ErrorCode


class EmptyParams(BaseModel):
    model_config = ConfigDict(extra="forbid")


class ListDevicesParams(BaseModel):
    model_config = ConfigDict(extra="forbid")

    include_common_ports: bool = False


def _device_manager(context: ToolContext) -> Any:
    request = context.request
    manager = getattr(
        getattr(getattr(request, "app", None), "state", None),
        "device_manager",
        None,
    )
    if manager is None:
        raise AppError(ErrorCode.SERVICE_UNAVAILABLE, "设备管理服务尚未启动")
    return manager


async def _get_device_status(
    _params: EmptyParams, context: ToolContext
) -> dict[str, Any]:
    return _device_manager(context).snapshot()


async def _list_devices(
    params: ListDevicesParams, context: ToolContext
) -> dict[str, Any]:
    manager = _device_manager(context)
    try:
        devices = await manager.list_devices(
            include_common_ports=params.include_common_ports
        )
    except AppError as exc:
        if (
            exc.code == ErrorCode.DEVICE_SCAN_FAILED
            and isinstance(exc.details, dict)
            and exc.details.get("stage") == "adb_binary"
        ):
            raise AppError(ErrorCode.ADB_NOT_FOUND, exc.message, exc.details) from exc
        raise
    return {
        "current": manager.address,
        "devices": devices,
        "common_ports": list(manager.common_ports),
    }


def _reconnect_error(manager: Any) -> AppError:
    snapshot = manager.snapshot()
    state = str(
        getattr(getattr(manager, "state", None), "value", snapshot.get("state", ""))
    )
    if state in {"connecting", "reconnecting"}:
        return AppError(
            ErrorCode.PIPELINE_ALREADY_RUNNING,
            "设备正在自动重连，请稍后再试",
            {
                "operation": "reconnect",
                "state": snapshot.get("state"),
                "retry": snapshot.get("retry") or {},
                "message": "设备正在自动重连，请稍后再试",
            },
        )

    details = dict(snapshot.get("last_error") or {})
    stage = str(details.get("stage") or "adb_connect")
    address = str(snapshot.get("address") or "")
    details.setdefault("stage", stage)
    details.setdefault("command", f"adb connect {address}".strip())
    details.setdefault("output", "设备连接失败")
    hints = {
        "adb_binary": "请在设置页检查 ADB 路径，或将 adb 加入 PATH",
        "adb_connect": "请确认模拟器已启动，且 ADB 调试端口正确",
        "adb_shell": "设备已连接但无响应，请尝试重启模拟器",
        "core_connect": "内核无法连接设备，可能是分辨率不受支持或触控方案不兼容",
    }
    details.setdefault("hint", hints.get(stage, hints["core_connect"]))
    details.setdefault("address", address)
    details.setdefault("attempts", (snapshot.get("retry") or {}).get("attempt", 0))
    code = (
        ErrorCode.ADB_NOT_FOUND
        if stage == "adb_binary"
        else ErrorCode.ADB_CONNECT_FAILED
    )
    message = (
        "找不到 ADB 可执行文件"
        if code is ErrorCode.ADB_NOT_FOUND
        else f"无法连接到 {address}" if address else "设备连接失败"
    )
    return AppError(code, message, details)


async def _reconnect_device(
    _params: EmptyParams, context: ToolContext
) -> dict[str, Any]:
    manager = _device_manager(context)
    state = str(
        getattr(
            getattr(manager, "state", None),
            "value",
            manager.snapshot().get("state", ""),
        )
    )
    if state in {"connecting", "reconnecting"}:
        raise _reconnect_error(manager)
    connected = await manager.connect(reason="reconnect")
    if not connected:
        raise _reconnect_error(manager)
    return manager.snapshot()


def register_tools(registry: ToolRegistry) -> None:
    """Register device status, scan, and reconnect tools."""
    registry.register(
        ToolDefinition(
            name="get_device_status",
            group="device",
            risk=ToolRisk.SAFE,
            description="读取当前设备连接状态、地址、分辨率与 UUID。",
            params_model=EmptyParams,
            handler=_get_device_status,
        )
    )
    registry.register(
        ToolDefinition(
            name="list_devices",
            group="device",
            risk=ToolRisk.SAFE,
            description="扫描 ADB 可见设备；可选探测常见模拟器端口。",
            params_model=ListDevicesParams,
            handler=_list_devices,
        )
    )
    registry.register(
        ToolDefinition(
            name="reconnect_device",
            group="device",
            risk=ToolRisk.SAFE,
            description="通过设备管理器手动尝试一次重连。",
            params_model=EmptyParams,
            handler=_reconnect_device,
        )
    )
