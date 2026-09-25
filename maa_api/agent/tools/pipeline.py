"""Agent pipeline inspection and queue-control tools."""

from __future__ import annotations

from datetime import UTC, datetime
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field
from sqlalchemy.ext.asyncio import AsyncSession

from maa_api.agent.registry import ToolContext, ToolDefinition, ToolRegistry, ToolRisk
from maa_api.db.models import Pipeline, Task
from maa_api.db.repositories.pipeline import PipelineRepository
from maa_api.domain.enums import (
    PipelineSource,
    PipelineStatus,
    TaskStatus,
)
from maa_api.domain.errors import AppError, ErrorCode
from maa_api.domain.task import (
    PipelineCreate,
    RUNTIME_IMMUTABLE,
    TASK_MODELS,
    TASK_LABELS,
)

__all__ = ["SetTaskParams", "register_tools"]


class EmptyParams(BaseModel):
    model_config = ConfigDict(extra="forbid")


class GetPipelineParams(BaseModel):
    model_config = ConfigDict(extra="forbid")

    pipeline_id: str = Field(min_length=1, max_length=36)


class ListPipelinesParams(BaseModel):
    model_config = ConfigDict(extra="forbid")

    status: Literal["pending", "running", "completed", "failed", "cancelled"] | None = None
    source: Literal["manual", "agent", "scheduled"] | None = None
    since: datetime | None = None
    page: int = Field(default=1, ge=1)
    size: int = Field(default=20, ge=1, le=200)


class SetTaskParams(BaseModel):
    model_config = ConfigDict(extra="forbid")

    task_id: str = Field(min_length=1, max_length=36)
    params: dict[str, Any] = Field(min_length=1)


class CancelQueuedParams(BaseModel):
    model_config = ConfigDict(extra="forbid")

    source: Literal["manual", "agent", "scheduled"] | None = None


def register_tools(registry: ToolRegistry) -> None:
    """Register the SAFE, CONDITIONAL and queue-control pipeline tools."""
    definitions = (
        ToolDefinition(
            name="list_task_types",
            group="pipeline",
            risk=ToolRisk.SAFE,
            description="列出 MAA 支持的任务类型、参数 schema 和运行中不可修改字段。",
            params_model=EmptyParams,
            handler=list_task_types,
        ),
        ToolDefinition(
            name="get_pipeline",
            group="pipeline",
            risk=ToolRisk.SAFE,
            description="读取流水线详情与任务级状态。",
            params_model=GetPipelineParams,
            handler=get_pipeline,
        ),
        ToolDefinition(
            name="list_pipelines",
            group="pipeline",
            risk=ToolRisk.SAFE,
            description="分页查询流水线执行历史。",
            params_model=ListPipelinesParams,
            handler=list_pipelines,
        ),
        ToolDefinition(
            name="get_queue",
            group="pipeline",
            risk=ToolRisk.SAFE,
            description="读取当前流水线和待执行队列，包含实际优先级。",
            params_model=EmptyParams,
            handler=get_queue,
        ),
        ToolDefinition(
            name="submit_pipeline",
            group="pipeline",
            risk=ToolRisk.CONDITIONAL,
            description="提交 MAA 任务流水线并以 agent 队列优先级入队。",
            params_model=PipelineCreate,
            handler=submit_pipeline,
        ),
        ToolDefinition(
            name="stop_pipeline",
            group="pipeline",
            risk=ToolRisk.SAFE,
            description="停止当前运行中的流水线；这是无需确认的救援动作。",
            params_model=EmptyParams,
            handler=stop_pipeline,
        ),
        ToolDefinition(
            name="set_task_params",
            group="pipeline",
            risk=ToolRisk.CONDITIONAL,
            description="修改运行中任务支持热更新的参数；消耗类参数仍需确认。",
            params_model=SetTaskParams,
            handler=set_task_params,
        ),
        ToolDefinition(
            name="cancel_queued",
            group="pipeline",
            risk=ToolRisk.SAFE,
            description="取消指定来源的所有排队中流水线，不影响正在执行的任务。",
            params_model=CancelQueuedParams,
            handler=cancel_queued,
        ),
    )
    for definition in definitions:
        registry.register(definition)


async def list_task_types(_params: EmptyParams, _ctx: ToolContext) -> dict[str, Any]:
    items = []
    for name, model in TASK_MODELS.items():
        schema = model.model_json_schema(ref_template="#/$defs/{model}")
        items.append(
            {
                "name": name,
                "label": TASK_LABELS[name],
                "description": (model.__doc__ or "").strip(),
                "schema": schema,
                "runtime_immutable": list(RUNTIME_IMMUTABLE[name]),
            }
        )
    return {"items": items}


async def get_pipeline(params: GetPipelineParams, ctx: ToolContext) -> dict[str, Any]:
    pipeline = await PipelineRepository(_required_session(ctx)).get(
        params.pipeline_id, with_tasks=True
    )
    if pipeline is None:
        raise AppError(
            ErrorCode.PIPELINE_NOT_FOUND,
            "流水线不存在",
            {"pipeline_id": params.pipeline_id},
        )
    return _pipeline_wire(pipeline)


async def list_pipelines(
    params: ListPipelinesParams, ctx: ToolContext
) -> dict[str, Any]:
    since = _naive_utc(params.since)
    page = await PipelineRepository(_required_session(ctx)).list(
        status=PipelineStatus(params.status) if params.status else None,
        source=PipelineSource(params.source) if params.source else None,
        since=since,
        page=params.page,
        size=params.size,
    )
    return {
        "items": [_pipeline_wire(item) for item in page.items],
        "total": page.total,
        "page": page.page,
        "size": page.size,
    }


async def get_queue(_params: EmptyParams, ctx: ToolContext) -> dict[str, Any]:
    return await _required_service(_app_state(ctx), "queue_service").snapshot()


async def submit_pipeline(
    params: PipelineCreate, ctx: ToolContext
) -> dict[str, Any]:
    state = _app_state(ctx)
    queue_service = _required_service(state, "queue_service")
    runner = _required_service(state, "pipeline_runner")
    pipeline, reused = await queue_service.submit(
        params,
        source=PipelineSource.AGENT,
        agent_session_id=ctx.session_id,
    )
    if not reused:
        await runner.publish_pipeline(pipeline.id)
        await runner.publish_queue_changed()
    return {
        "pipeline_id": pipeline.id,
        "status": str(pipeline.status),
        "created_at": _iso(pipeline.created_at),
        "reused": reused,
    }


async def stop_pipeline(_params: EmptyParams, ctx: ToolContext) -> dict[str, Any]:
    session = _required_session(ctx)
    pipeline = await PipelineRepository(session).current()
    if pipeline is None:
        return {"pipeline_id": None, "status": "idle"}
    await _required_service(_app_state(ctx), "pipeline_runner").request_cancel(
        pipeline.id
    )
    return {"pipeline_id": pipeline.id, "status": "cancellation_requested"}


async def set_task_params(params: SetTaskParams, ctx: ToolContext) -> dict[str, Any]:
    task = await _required_service(_app_state(ctx), "pipeline_runner").set_task_params(
        params.task_id, params.params
    )
    return {"task_id": task.id, "params": task.params, "status": str(task.status)}


async def cancel_queued(
    params: CancelQueuedParams, ctx: ToolContext
) -> dict[str, Any]:
    count = await _required_service(_app_state(ctx), "pipeline_runner").cancel_pending(
        source=params.source
    )
    return {"cancelled": count, "source": params.source}


def _app_state(ctx: ToolContext) -> Any:
    app = getattr(ctx.request, "app", None)
    state = getattr(app, "state", None)
    if state is None:
        raise AppError(ErrorCode.INTERNAL_ERROR, "工具上下文未提供应用运行时状态")
    return state


def _required_service(state: Any, name: str) -> Any:
    service = getattr(state, name, None)
    if service is None:
        raise AppError(ErrorCode.INTERNAL_ERROR, f"应用未装配 {name}")
    return service


def _required_session(ctx: ToolContext) -> AsyncSession:
    if ctx.db_session is None:
        raise AppError(ErrorCode.INTERNAL_ERROR, "工具上下文未提供数据库会话")
    return ctx.db_session


def _pipeline_wire(pipeline: Pipeline) -> dict[str, Any]:
    tasks = getattr(pipeline, "tasks", None)
    result: dict[str, Any] = {
        "id": pipeline.id,
        "core_id": pipeline.core_id,
        "source": str(pipeline.source),
        "priority": int(pipeline.priority),
        "status": str(pipeline.status),
        "title": pipeline.title,
        "task_count": pipeline.task_count,
        "notify_on_finish": pipeline.notify_on_finish,
        "schedule_id": pipeline.schedule_id,
        "agent_session_id": pipeline.agent_session_id,
        "retry_of_id": pipeline.retry_of_id,
        "error": _error(pipeline.error_code, pipeline.error_message),
        "created_at": _iso(pipeline.created_at),
        "started_at": _iso(pipeline.started_at),
        "finished_at": _iso(pipeline.finished_at),
    }
    if tasks is not None:
        items = list(tasks)
        result["progress"] = {
            "total": pipeline.task_count,
            "completed": sum(TaskStatus(task.status) is TaskStatus.COMPLETED for task in items),
            "failed": sum(
                TaskStatus(task.status) in (TaskStatus.FAILED, TaskStatus.SKIPPED)
                for task in items
            ),
        }
        result["tasks"] = [_task_wire(task) for task in items]
    return result


def _task_wire(task: Task) -> dict[str, Any]:
    ended = task.finished_at or datetime.now(UTC).replace(tzinfo=None)
    duration = None
    if task.started_at is not None:
        duration = max((ended - task.started_at).total_seconds(), 0.0)
    return {
        "id": task.id,
        "pipeline_id": task.pipeline_id,
        "order_index": task.order_index,
        "type_name": task.type_name,
        "task_name": task.task_name,
        "params": task.params,
        "raw_params": task.raw_params,
        "status": str(task.status),
        "retry_count": task.retry_count,
        "max_retries": task.max_retries,
        "retry_delay": task.retry_delay,
        "maa_task_id": task.maa_task_id,
        "error": _error(task.error_code, task.error_message),
        "created_at": _iso(task.created_at),
        "started_at": _iso(task.started_at),
        "finished_at": _iso(task.finished_at),
        "duration_seconds": duration,
    }


def _error(code: str | None, message: str | None) -> dict[str, str | None] | None:
    if code is None and message is None:
        return None
    return {"code": code, "message": message}


def _iso(value: datetime | None) -> str | None:
    if value is None:
        return None
    if value.tzinfo is None:
        value = value.replace(tzinfo=UTC)
    return value.astimezone(UTC).isoformat().replace("+00:00", "Z")


def _naive_utc(value: datetime | None) -> datetime | None:
    if value is None or value.tzinfo is None:
        return value
    return value.astimezone(UTC).replace(tzinfo=None)
