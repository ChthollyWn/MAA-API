"""Agent operations tools backed by the shared update and maintenance services."""

from __future__ import annotations

from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field

from maa_api.agent.registry import ToolContext, ToolDefinition, ToolRegistry, ToolRisk
from maa_api.domain.enums import ResourceChannel, UpdateTarget
from maa_api.domain.errors import AppError, ErrorCode

__all__ = ["register_tools"]


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


class EmptyParams(BaseModel):
    model_config = ConfigDict(extra="forbid")


def register_tools(registry: ToolRegistry) -> None:
    """Register status and destructive update/restart operations."""
    definitions = (
        ToolDefinition(
            name="check_updates",
            group="ops",
            risk=ToolRisk.SAFE,
            description="检查 MaaCore、活动资源与游戏本体是否有可用更新。",
            params_model=CheckUpdatesParams,
            handler=check_updates,
        ),
        ToolDefinition(
            name="update_core",
            group="ops",
            risk=ToolRisk.DANGEROUS,
            description="通过统一更新服务更新 MaaCore；会在安全维护窗口内重启内核。",
            params_model=UpdateCoreParams,
            handler=update_core,
        ),
        ToolDefinition(
            name="update_resource",
            group="ops",
            risk=ToolRisk.DANGEROUS,
            description="通过统一更新服务更新指定活动资源通道并按需重载。",
            params_model=UpdateResourceParams,
            handler=update_resource,
        ),
        ToolDefinition(
            name="update_game",
            group="ops",
            risk=ToolRisk.DANGEROUS,
            description="通过统一更新服务下载并安装游戏本体 APK。",
            params_model=UpdateGameParams,
            handler=update_game,
        ),
        ToolDefinition(
            name="restart_core",
            group="ops",
            risk=ToolRisk.DANGEROUS,
            description="安全排空流水线、暂停队列并在 Supervisor 维护窗口内重启 MaaCore。",
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
    }


def _service(context: ToolContext, name: str) -> Any:
    state = getattr(getattr(getattr(context.request, "app", None), "state", None), name, None)
    if state is None:
        raise AppError(ErrorCode.SERVICE_UNAVAILABLE, f"{name} 尚未启动")
    return state
