"""Agent tools for validated user-owned Copilot and resource assets."""

from __future__ import annotations

from typing import Any

from pydantic import BaseModel, ConfigDict, Field

from maa_api.agent.registry import ToolContext, ToolDefinition, ToolRegistry, ToolRisk
from maa_api.domain.errors import AppError, ErrorCode

__all__ = ["register_tools"]


class EmptyParams(BaseModel):
    model_config = ConfigDict(extra="forbid")


class ListAssetsParams(BaseModel):
    model_config = ConfigDict(extra="forbid")

    page: int = Field(default=1, ge=1)
    size: int = Field(default=20, ge=1, le=200)
    q: str | None = Field(default=None, max_length=128)


class UploadAssetParams(BaseModel):
    model_config = ConfigDict(extra="forbid")

    name: str = Field(min_length=1, max_length=64)
    description: str | None = Field(default=None, max_length=4096)
    content: dict[str, Any]


class RegisterCustomTaskParams(UploadAssetParams):
    pass


class RemoveCustomTaskParams(BaseModel):
    model_config = ConfigDict(extra="forbid")

    asset_id: str = Field(min_length=1, max_length=36)


def register_tools(registry: ToolRegistry) -> None:
    """Register validated resource tools; there is intentionally no Copilot runner."""
    definitions = (
        ToolDefinition(
            name="list_copilots",
            group="resource",
            risk=ToolRisk.SAFE,
            description="翻已保存的 Copilot 作业，看有哪些、各叫什么。列表不带作业内容，回答「存了哪些作业」时用。",
            params_model=ListAssetsParams,
            handler=list_copilots,
            read_only=True,
        ),
        ToolDefinition(
            name="upload_copilot",
            group="resource",
            risk=ToolRisk.SAFE,
            description="把 Copilot 自动战斗作业存下来备用。只保存，不执行；要跑就用 submit_pipeline 提交 Copilot 任务。",
            params_model=UploadAssetParams,
            handler=upload_copilot,
        ),
        ToolDefinition(
            name="set_infrast_plan",
            group="resource",
            risk=ToolRisk.DANGEROUS,
            description="保存或覆盖一份自定义基建排班方案，供 Infrast 任务引用。高风险，会弹人工确认；调用前先讲清要写入什么、会不会覆盖同名方案。",
            params_model=UploadAssetParams,
            handler=set_infrast_plan,
        ),
        ToolDefinition(
            name="list_custom_tasks",
            group="resource",
            risk=ToolRisk.SAFE,
            description="列出已注入的自定义任务，确认某个 Custom_ 任务在不在。",
            params_model=ListAssetsParams,
            handler=list_custom_tasks,
            read_only=True,
        ),
        ToolDefinition(
            name="register_custom_task",
            group="resource",
            risk=ToolRisk.DANGEROUS,
            description="注册一个自定义任务，供 submit_pipeline 使用。任务名必须以 Custom_ 开头。",
            params_model=RegisterCustomTaskParams,
            handler=register_custom_task,
        ),
        ToolDefinition(
            name="remove_custom_task",
            group="resource",
            risk=ToolRisk.DANGEROUS,
            description="删掉一个已注册的自定义任务，并重载内核增量资源。",
            params_model=RemoveCustomTaskParams,
            handler=remove_custom_task,
        ),
    )
    for definition in definitions:
        registry.register(definition)


async def list_copilots(
    params: ListAssetsParams, context: ToolContext
) -> dict[str, Any]:
    return await _service(context).list_copilots(
        page=params.page, size=params.size, q=params.q
    )


async def upload_copilot(
    params: UploadAssetParams, context: ToolContext
) -> dict[str, Any]:
    return await _service(context).upload_copilot(
        name=params.name,
        description=params.description,
        content=params.content,
    )


async def set_infrast_plan(
    params: UploadAssetParams, context: ToolContext
) -> dict[str, Any]:
    return await _service(context).set_infrast_plan(
        name=params.name,
        description=params.description,
        content=params.content,
    )


async def list_custom_tasks(
    params: ListAssetsParams, context: ToolContext
) -> dict[str, Any]:
    return await _service(context).list_custom_tasks(
        page=params.page, size=params.size, q=params.q
    )


async def register_custom_task(
    params: RegisterCustomTaskParams, context: ToolContext
) -> dict[str, Any]:
    return await _service(context).register_custom_task(
        name=params.name,
        description=params.description,
        content=params.content,
    )


async def remove_custom_task(
    params: RemoveCustomTaskParams, context: ToolContext
) -> dict[str, Any]:
    return await _service(context).remove_custom_task(params.asset_id)


def _service(context: ToolContext) -> Any:
    state = getattr(getattr(getattr(context.request, "app", None), "state", None), "resource_service", None)
    if state is None:
        raise AppError(ErrorCode.SERVICE_UNAVAILABLE, "资源服务尚未启动")
    return state
