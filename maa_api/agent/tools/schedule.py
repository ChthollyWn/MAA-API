"""Agent schedule CRUD backed by the normal validator and APScheduler service."""

from __future__ import annotations

from typing import Any

from pydantic import BaseModel, ConfigDict, Field

from maa_api.agent.registry import ToolContext, ToolDefinition, ToolRegistry, ToolRisk
from maa_api.agent.tools.task_schema import schedule_model_schema
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
            description="列出定时任务和最近执行情况。用户问「定时任务都跑了吗」时用。",
            params_model=ListSchedulesParams,
            handler=list_schedules,
            read_only=True,
        ),
        ToolDefinition(
            name="create_schedule",
            group="schedule",
            risk=ToolRisk.CONDITIONAL,
            description="新建并启用一个定时任务。建之前先和用户对齐时间与任务内容；模板里有消耗类参数时会弹人工确认。",
            params_model=ScheduleWrite,
            model_schema=schedule_model_schema(),
            handler=create_schedule,
        ),
        ToolDefinition(
            name="update_schedule",
            group="schedule",
            risk=ToolRisk.CONDITIONAL,
            description="整条改掉一个定时任务（全量覆盖，没传的字段会按默认值走）。模板含消耗类参数时需要确认。",
            params_model=UpdateScheduleParams,
            model_schema=schedule_model_schema(update=True),
            handler=update_schedule,
        ),
        ToolDefinition(
            name="delete_schedule",
            group="schedule",
            risk=ToolRisk.SAFE,
            description="删掉一个定时任务。按已确认策略不需要确认；删之前说清删的是哪一个。",
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
