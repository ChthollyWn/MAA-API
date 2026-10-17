"""Agent operations tools backed by the shared update and maintenance services."""

from __future__ import annotations

import asyncio
import time
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field

from maa_api.agent.registry import ToolContext, ToolDefinition, ToolRegistry, ToolRisk
from maa_api.domain.enums import ResourceChannel, UpdateTarget
from maa_api.domain.errors import AppError, ErrorCode

__all__ = ["register_tools"]

#: ``wait_update`` 的轮询间隔（秒）。更新进度以秒级变化，1 秒足够细。
WAIT_UPDATE_POLL_SECONDS = 1.0
#: 更新终态：``success`` / ``failed`` / ``cancelled`` / ``skipped``。
UPDATE_TERMINAL_STATUSES = frozenset({"success", "failed", "cancelled", "skipped"})


class CheckUpdatesParams(BaseModel):
    model_config = ConfigDict(extra="forbid")

    refresh: bool = False


class UpdateCoreParams(BaseModel):
    model_config = ConfigDict(extra="forbid")

    channel: Literal["stable"] = "stable"
    force: bool = False
    version: str | None = Field(default=None, min_length=1, max_length=64)


class UpdateResourceParams(BaseModel):
    model_config = ConfigDict(extra="forbid")

    channel: Literal["ota", "repo", "all"] = "all"
    force: bool = False


class UpdateGameParams(BaseModel):
    model_config = ConfigDict(extra="forbid")

    channel: Literal["Official", "Bilibili"] | None = None
    force: bool = False


class WaitUpdateParams(BaseModel):
    model_config = ConfigDict(extra="forbid")

    update_id: str | None = Field(
        default=None,
        description="update_* 工具返回的 update_id；不填就等当前正在跑的更新。",
    )
    timeout_seconds: int = Field(default=180, ge=1, le=900)


class EmptyParams(BaseModel):
    model_config = ConfigDict(extra="forbid")


def register_tools(registry: ToolRegistry) -> None:
    """Register status and destructive update/restart operations."""
    definitions = (
        ToolDefinition(
            name="check_updates",
            group="ops",
            risk=ToolRisk.SAFE,
            description="查内核、活动资源和游戏本体有没有新版本。用户问要不要更新时先查一次。",
            params_model=CheckUpdatesParams,
            handler=check_updates,
            read_only=True,
        ),
        ToolDefinition(
            name="update_core",
            group="ops",
            risk=ToolRisk.DANGEROUS,
            description=(
                "更新 MaaCore，过程会重启内核，正在跑的流水线会被中断。"
                "更新是异步的，调用后用 wait_update 等它结束。"
            ),
            params_model=UpdateCoreParams,
            handler=update_core,
        ),
        ToolDefinition(
            name="update_resource",
            group="ops",
            risk=ToolRisk.DANGEROUS,
            description=(
                "更新活动资源（活动关卡与图片等本地资源）。用在：用户明确要求；"
                "或内核因为不认识这个关卡而拒绝执行（作业/Fight 都会报 Not support stage："
                "Copilot 任务会读作业里的 stage_name 并在内核资源里校验）；"
                "或 Fight 需要新关卡的地块数据。注意：搜索作业站本身不需要更新资源。"
                "更新是异步的、可能要几分钟，调用后用 wait_update 等它结束，不要反复 get_versions。"
            ),
            params_model=UpdateResourceParams,
            handler=update_resource,
        ),
        ToolDefinition(
            name="update_game",
            group="ops",
            risk=ToolRisk.DANGEROUS,
            description="下载并安装游戏本体 APK。用户要升级游戏客户端时用。",
            params_model=UpdateGameParams,
            handler=update_game,
        ),
        ToolDefinition(
            name="wait_update",
            group="ops",
            risk=ToolRisk.SAFE,
            description=(
                "等一次更新（update_resource/update_core/update_game 返回的 update_id）结束或超时，"
                "返回终态与进度。更新是异步的：提交后用它等结果，不要反复 get_versions。"
                "超时返回 finished=false，更新仍在跑，可以再调一次。"
            ),
            params_model=WaitUpdateParams,
            handler=wait_update,
            read_only=True,
        ),
        ToolDefinition(
            name="restart_core",
            group="ops",
            risk=ToolRisk.DANGEROUS,
            description="重启 MaaCore，会先停掉队列与运行中的流水线。",
            params_model=EmptyParams,
            handler=restart_core,
        ),
    )
    for definition in definitions:
        registry.register(definition)


async def check_updates(
    params: CheckUpdatesParams, context: ToolContext
) -> dict[str, Any]:
    return await _service(context, "update_service").status(refresh=params.refresh)


async def update_core(params: UpdateCoreParams, context: ToolContext) -> dict[str, Any]:
    return await _start_update(
        context,
        UpdateTarget.CORE,
        {"channel": params.channel, "force": params.force, "version": params.version},
    )


async def update_resource(
    params: UpdateResourceParams, context: ToolContext
) -> dict[str, Any]:
    return await _start_update(
        context,
        UpdateTarget.RESOURCE,
        {"channel": ResourceChannel(params.channel), "force": params.force},
    )


async def update_game(params: UpdateGameParams, context: ToolContext) -> dict[str, Any]:
    options: dict[str, Any] = {"force": params.force}
    if params.channel is not None:
        options["channel"] = params.channel
    return await _start_update(context, UpdateTarget.GAME, options)


async def wait_update(
    params: WaitUpdateParams, context: ToolContext
) -> dict[str, Any]:
    """等一次更新到终态（M57，docs/13 §8.80）。

    更新是异步后台任务：``update_*`` 只返回 update_id，模型以前只能反复
    ``get_versions`` 轮询，拿不到变化时会被「原地打转」检测硬停（2026-10-17 实机会话
    seq99）。``wait_update`` 阻塞到终态或超时，返回进度与终态。
    """
    service = _service(context, "update_service")
    started = time.monotonic()
    while True:
        record = await _find_update(service, params.update_id)
        if record is None:
            return {
                "finished": True,
                "update": None,
                "waited_seconds": round(time.monotonic() - started, 1),
                "note": "当前没有进行中的更新",
            }
        summary = _update_summary(record)
        status = str(getattr(record.status, "value", record.status))
        waited = round(time.monotonic() - started, 1)
        if status in UPDATE_TERMINAL_STATUSES:
            return {"finished": True, "update": summary, "waited_seconds": waited}
        if time.monotonic() - started >= params.timeout_seconds:
            return {
                "finished": False,
                "update": summary,
                "waited_seconds": waited,
                "note": "还在跑；可以再调用一次 wait_update 继续等",
            }
        await asyncio.sleep(WAIT_UPDATE_POLL_SECONDS)


async def _find_update(service: Any, update_id: str | None) -> Any:
    """按 id 找更新记录；没给 id 就取当前正在跑的那条。"""
    if update_id:
        return await service.get(update_id)
    status = await service.status()
    running = status.get("running") if isinstance(status, dict) else None
    running_id = running.get("id") if isinstance(running, dict) else None
    if not running_id:
        return None
    return await service.get(str(running_id))


def _update_summary(record: Any) -> dict[str, Any]:
    finished_at = getattr(record, "finished_at", None)
    return {
        "update_id": str(getattr(record, "id", "")),
        "target": str(getattr(record, "target", "")),
        "channel": getattr(record, "channel", None),
        "status": str(getattr(record, "status", "")),
        "phase": getattr(record, "phase", None),
        "progress": getattr(record, "progress", None),
        "bytes_total": getattr(record, "bytes_total", None),
        "bytes_done": getattr(record, "bytes_done", None),
        "from_version": getattr(record, "from_version", None),
        "to_version": getattr(record, "to_version", None),
        "error_code": getattr(record, "error_code", None),
        "error_message": getattr(record, "error_message", None),
        "finished_at": finished_at.isoformat() if finished_at is not None else None,
    }


async def restart_core(_params: EmptyParams, context: ToolContext) -> dict[str, Any]:
    return await _service(context, "agent_ops_service").restart_core(caller="agent")


async def _start_update(
    context: ToolContext, target: UpdateTarget, options: dict[str, Any]
) -> dict[str, Any]:
    record = await _service(context, "update_service").start(
        target,
        options,
        caller="agent",
    )
    return {
        "update_id": str(record.id),
        "target": str(getattr(record.target, "value", record.target)),
        "status": str(getattr(record.status, "value", record.status)),
        "note": "更新在后台异步执行：用 wait_update(update_id=…) 等它结束，不要反复 get_versions。",
    }


def _service(context: ToolContext, name: str) -> Any:
    state = getattr(getattr(getattr(context.request, "app", None), "state", None), name, None)
    if state is None:
        raise AppError(ErrorCode.SERVICE_UNAVAILABLE, f"{name} 尚未启动")
    return state
