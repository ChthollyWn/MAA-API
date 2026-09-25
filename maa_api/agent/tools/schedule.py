"""Agent schedule CRUD backed by the normal validator and APScheduler service."""

from __future__ import annotations

from typing import Any

from pydantic import BaseModel, ConfigDict, Field

from maa_api.agent.registry import ToolContext, ToolDefinition, ToolRegistry, ToolRisk
from maa_api.domain.errors import AppError, ErrorCode
from maa_api.services.schedule_service import ScheduleWrite

__all__ = ["register_tools"]


class ListSchedulesParams(BaseModel):
    model_config = ConfigDict(extra="forbid")

    enabled: bool | None = None


class UpdateScheduleParams(ScheduleWrite):
    schedule_id: str = Field(min_length=1, max_length=36)


def register_tools(registry: ToolRegistry) -> None:
    definitions = (
        ToolDefinition(
            name="list_schedules",
            group="schedule",
            risk=ToolRisk.SAFE,
            description="列出定时任务与最近执行情况。",
            params_model=ListSchedulesParams,
            handler=list_schedules,
        ),
        ToolDefinition(
            name="create_schedule",
            group="schedule",
            risk=ToolRisk.CONDITIONAL,
            description="创建并启用定时任务；模板含消耗类参数时需要确认。",
            params_model=ScheduleWrite,
            handler=create_schedule,
        ),
        ToolDefinition(
            name="update_schedule",
            group="schedule",
            risk=ToolRisk.CONDITIONAL,
            description="全量修改定时任务；模板含消耗类参数时需要确认。",
            params_model=UpdateScheduleParams,
            handler=update_schedule,
        ),
        ToolDefinition(
            name="delete_schedule",
            group="schedule",
            risk=ToolRisk.SAFE,
            description="删除指定定时任务；按已确认策略无需确认。",
            params_model=DeleteScheduleParams,
            handler=delete_schedule,
        ),
    )
    for definition in definitions:
        registry.register(definition)


class DeleteScheduleParams(BaseModel):
    model_config = ConfigDict(extra="forbid")

    schedule_id: str = Field(min_length=1, max_length=36)


async def list_schedules(
    params: ListSchedulesParams, context: ToolContext
) -> dict[str, Any]:
    result = await _service(context).list(enabled=params.enabled)
    return _json_value(result)


async def create_schedule(
    params: ScheduleWrite, context: ToolContext
) -> dict[str, Any]:
    result = await _service(context).create(params)
    return _json_value(result)


async def update_schedule(
    params: UpdateScheduleParams, context: ToolContext
) -> dict[str, Any]:
    result = await _service(context).update(
        params.schedule_id,
        ScheduleWrite.model_validate(params.model_dump(exclude={"schedule_id"})),
    )
    return _json_value(result)


async def delete_schedule(
    params: DeleteScheduleParams, context: ToolContext
) -> dict[str, Any]:
    await _service(context).delete(params.schedule_id)
    return {"schedule_id": params.schedule_id, "deleted": True}


def _service(context: ToolContext) -> Any:
    service = getattr(getattr(getattr(context.request, "app", None), "state", None), "schedule_service", None)
    if service is None:
        raise AppError(ErrorCode.SERVICE_UNAVAILABLE, "定时任务服务尚未启动")
    return service


def _json_value(value: Any) -> Any:
    dump = getattr(value, "model_dump", None)
    if callable(dump):
        return dump(mode="json", by_alias=True)
    return value
