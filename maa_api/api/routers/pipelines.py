"""Pipeline submission, history, cancellation and associated resources (M5)."""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Any

from fastapi import APIRouter, Depends, Header, Query
from pydantic import BaseModel, ConfigDict, Field, TypeAdapter, ValidationError
from sqlalchemy.ext.asyncio import AsyncSession

from maa_api.api.deps import (
    get_pipeline_runner,
    get_queue_service,
    get_session,
    require_auth,
)
from maa_api.api.errors import error_responses
from maa_api.db.models import Pipeline, Screenshot, Task
from maa_api.db.repositories.log import LogRepository, ScreenshotRepository
from maa_api.db.repositories.pipeline import PipelineRepository, TaskRepository
from maa_api.domain.enums import (
    LogLevel,
    LogSource,
    PipelineSource,
    PipelineStatus,
    TaskStatus,
)
from maa_api.domain.errors import AppError, ErrorCode
from maa_api.domain.task import PipelineCreate, TaskInput
from maa_api.services.log_hub import SOURCE_DB_TO_WIRE, SOURCE_WIRE_TO_DB

__all__ = ["pipeline_wire", "router", "task_wire"]

router = APIRouter(prefix="/api/pipelines", tags=["pipelines"])
_TASK_ADAPTER = TypeAdapter(TaskInput)


def _iso(value: datetime | None) -> str | None:
    if value is None:
        return None
    if value.tzinfo is None:
        value = value.replace(tzinfo=timezone.utc)
    return value.astimezone(timezone.utc).isoformat().replace("+00:00", "Z")


def task_wire(task: Task) -> dict[str, Any]:
    now = datetime.now(timezone.utc).replace(tzinfo=None)
    started = task.started_at
    ended = task.finished_at or now
    duration = None
    if started is not None:
        duration = max((ended - started).total_seconds(), 0.0)
    error = None
    if task.error_code or task.error_message:
        error = {"code": task.error_code, "message": task.error_message}
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
        "error": error,
        "created_at": _iso(task.created_at),
        "started_at": _iso(task.started_at),
        "finished_at": _iso(task.finished_at),
        "duration_seconds": duration,
    }


def pipeline_wire(pipeline: Pipeline) -> dict[str, Any]:
    tasks = getattr(pipeline, "tasks", None)
    error = None
    if pipeline.error_code or pipeline.error_message:
        error = {"code": pipeline.error_code, "message": pipeline.error_message}
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
        "error": error,
        "created_at": _iso(pipeline.created_at),
        "started_at": _iso(pipeline.started_at),
        "finished_at": _iso(pipeline.finished_at),
    }
    if tasks is not None:
        task_items = list(tasks)
        completed = sum(TaskStatus(task.status) is TaskStatus.COMPLETED for task in task_items)
        failed = sum(
            TaskStatus(task.status) in (TaskStatus.FAILED, TaskStatus.SKIPPED)
            for task in task_items
        )
        result["progress"] = {
            "total": pipeline.task_count,
            "completed": completed,
            "failed": failed,
        }
        result["tasks"] = [task_wire(task) for task in task_items]
    return result


def _pipeline_not_found(pipeline_id: str) -> AppError:
    return AppError(
        ErrorCode.PIPELINE_NOT_FOUND,
        "流水线不存在",
        {"pipeline_id": pipeline_id},
    )


def _invalid_pagination(message: str) -> AppError:
    return AppError(ErrorCode.INVALID_PAGINATION, message)


class RetryRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    priority: int | None = Field(default=None, ge=0, le=2)


@router.post(
    "",
    status_code=202,
    summary="提交流水线",
    responses=error_responses(
        "PIPELINE_EMPTY",
        "PIPELINE_TOO_MANY_TASKS",
        "UNKNOWN_TASK_TYPE",
        "TASK_PARAM_INVALID",
        "QUEUE_FULL",
        "QUEUE_PAUSED",
        "IDEMPOTENCY_KEY_CONFLICT",
        "UNAUTHORIZED",
    ),
    dependencies=[Depends(require_auth)],
)
async def submit_pipeline(
    payload: PipelineCreate,
    queue_service=Depends(get_queue_service),
    runner=Depends(get_pipeline_runner),
    idempotency_key: str | None = Header(default=None, alias="Idempotency-Key"),
) -> dict[str, Any]:
    pipeline, reused = await queue_service.submit(
        payload,
        source=PipelineSource.MANUAL,
        idempotency_key=idempotency_key,
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


@router.get(
    "/current",
    summary="获取当前流水线",
    responses=error_responses("UNAUTHORIZED"),
    dependencies=[Depends(require_auth)],
)
async def current_pipeline(
    session: AsyncSession = Depends(get_session),
) -> dict[str, Any]:
    repo = PipelineRepository(session)
    pipeline = await repo.current()
    if pipeline is None:
        return {"pipeline": None}
    pipeline = await repo.get(pipeline.id, with_tasks=True)
    return {"pipeline": pipeline_wire(pipeline) if pipeline is not None else None}


@router.get(
    "",
    summary="查询流水线历史",
    responses=error_responses("INVALID_PARAMETER", "INVALID_PAGINATION", "UNAUTHORIZED"),
    dependencies=[Depends(require_auth)],
)
async def list_pipelines(
    status: str | None = None,
    source: str | None = None,
    schedule_id: str | None = None,
    since: datetime | None = None,
    page: int = 1,
    size: int = 20,
    session: AsyncSession = Depends(get_session),
) -> dict[str, Any]:
    if page < 1 or size < 1 or size > 200:
        raise _invalid_pagination("page 必须大于等于 1，size 必须在 1 到 200 之间")
    try:
        parsed_status = PipelineStatus(status) if status is not None else None
        parsed_source = PipelineSource(source) if source is not None else None
    except ValueError:
        raise AppError(ErrorCode.INVALID_PARAMETER, "status 或 source 取值无效") from None
    result = await PipelineRepository(session).list(
        status=parsed_status,
        source=parsed_source,
        schedule_id=schedule_id,
        since=(
            since.astimezone(timezone.utc).replace(tzinfo=None)
            if since is not None and since.tzinfo is not None
            else since
        ),
        page=page,
        size=size,
    )
    return {
        "items": [pipeline_wire(item) for item in result.items],
        "total": result.total,
        "page": result.page,
        "size": result.size,
    }


@router.get(
    "/{pipeline_id}",
    summary="获取流水线详情",
    responses=error_responses("PIPELINE_NOT_FOUND", "UNAUTHORIZED"),
    dependencies=[Depends(require_auth)],
)
async def get_pipeline(
    pipeline_id: str,
    session: AsyncSession = Depends(get_session),
) -> dict[str, Any]:
    pipeline = await PipelineRepository(session).get(pipeline_id, with_tasks=True)
    if pipeline is None:
        raise _pipeline_not_found(pipeline_id)
    return pipeline_wire(pipeline)


@router.delete(
    "/{pipeline_id}",
    status_code=202,
    summary="取消流水线",
    responses=error_responses(
        "PIPELINE_NOT_FOUND", "PIPELINE_NOT_CANCELLABLE", "UNAUTHORIZED"
    ),
    dependencies=[Depends(require_auth)],
)
async def cancel_pipeline(
    pipeline_id: str,
    runner=Depends(get_pipeline_runner),
) -> dict[str, Any]:
    await runner.request_cancel(pipeline_id)
    return {"pipeline_id": pipeline_id, "status": "cancellation_requested"}


@router.post(
    "/{pipeline_id}/retry",
    status_code=202,
    summary="重放流水线",
    responses=error_responses(
        "PIPELINE_NOT_FOUND",
        "QUEUE_FULL",
        "QUEUE_PAUSED",
        "IDEMPOTENCY_KEY_CONFLICT",
        "INVALID_PARAMETER",
        "UNAUTHORIZED",
    ),
    dependencies=[Depends(require_auth)],
)
async def retry_pipeline(
    pipeline_id: str,
    payload: RetryRequest | None = None,
    queue_service=Depends(get_queue_service),
    runner=Depends(get_pipeline_runner),
    session: AsyncSession = Depends(get_session),
    idempotency_key: str | None = Header(default=None, alias="Idempotency-Key"),
) -> dict[str, Any]:
    previous = await PipelineRepository(session).get(pipeline_id, with_tasks=True)
    if previous is None:
        raise _pipeline_not_found(pipeline_id)
    raw_tasks: list[dict[str, Any]] = []
    for task in getattr(previous, "tasks", []):
        raw_tasks.append({"name": task.type_name, **(task.raw_params or {})})
    try:
        parsed_tasks = [_TASK_ADAPTER.validate_python(item) for item in raw_tasks]
    except ValidationError as exc:
        raise AppError(
            ErrorCode.TASK_PARAM_INVALID,
            "历史任务参数无法重放",
            {"pipeline_id": pipeline_id, "errors": exc.errors()},
        ) from exc
    request_body = PipelineCreate(
        tasks=parsed_tasks,
        title=previous.title,
        priority=payload.priority if payload is not None else previous.priority,
        notify_on_finish=previous.notify_on_finish,
    )
    pipeline, reused = await queue_service.submit(
        request_body,
        source=PipelineSource.MANUAL,
        idempotency_key=idempotency_key,
        retry_of_id=previous.id,
    )
    if not reused:
        await runner.publish_pipeline(pipeline.id)
        await runner.publish_queue_changed()
    return {
        "pipeline_id": pipeline.id,
        "status": str(pipeline.status),
        "retry_of_id": pipeline.retry_of_id,
        "created_at": _iso(pipeline.created_at),
        "reused": reused,
    }


@router.get(
    "/{pipeline_id}/tasks",
    summary="获取流水线任务列表",
    responses=error_responses("PIPELINE_NOT_FOUND", "UNAUTHORIZED"),
    dependencies=[Depends(require_auth)],
)
async def list_pipeline_tasks(
    pipeline_id: str,
    session: AsyncSession = Depends(get_session),
) -> dict[str, Any]:
    repo = PipelineRepository(session)
    if await repo.get(pipeline_id) is None:
        raise _pipeline_not_found(pipeline_id)
    tasks = await TaskRepository(session).list_by_pipeline(pipeline_id)
    return {"items": [task_wire(task) for task in tasks], "total": len(tasks)}


@router.get(
    "/{pipeline_id}/logs",
    summary="查询流水线日志",
    responses=error_responses(
        "PIPELINE_NOT_FOUND", "INVALID_PARAMETER", "INVALID_PAGINATION", "UNAUTHORIZED"
    ),
    dependencies=[Depends(require_auth)],
)
async def list_pipeline_logs(
    pipeline_id: str,
    source: list[str] | None = Query(default=None, alias="source[]"),
    level: list[str] | None = Query(default=None, alias="level[]"),
    after_id: int | None = None,
    order: str = "asc",
    page: int | None = None,
    size: int = 100,
    session: AsyncSession = Depends(get_session),
) -> dict[str, Any]:
    if await PipelineRepository(session).get(pipeline_id) is None:
        raise _pipeline_not_found(pipeline_id)
    if order not in {"asc", "desc"}:
        raise AppError(ErrorCode.INVALID_PARAMETER, "order 必须是 asc 或 desc")
    if size < 1 or size > 1000 or (page is not None and page < 1):
        raise _invalid_pagination("page 必须大于等于 1，size 必须在 1 到 1000 之间")
    if after_id is not None and page is not None:
        raise _invalid_pagination("after_id 与 page 不能同时使用")
    try:
        parsed_sources = (
            [SOURCE_WIRE_TO_DB[item] for item in source]
            if source is not None
            else None
        )
        normalized_levels = [
            "WARNING" if item.upper() == "WARN" else item.upper()
            for item in level or ()
        ]
        parsed_levels = (
            [LogLevel(item.lower()) for item in normalized_levels]
            if level is not None
            else None
        )
    except (KeyError, ValueError):
        raise AppError(ErrorCode.INVALID_PARAMETER, "日志来源或级别取值无效") from None
    repo = LogRepository(session)
    result = await repo.query(
        sources=parsed_sources,
        levels=parsed_levels,
        pipeline_id=pipeline_id,
        after_id=after_id,
        page=page or 1,
        size=size,
        order=order,
    )
    return {
        "items": [_log_wire(item) for item in result.items],
        "total": result.total,
        "page": result.page,
        "size": result.size,
    }


@router.get(
    "/{pipeline_id}/screenshots",
    summary="查询流水线截图",
    responses=error_responses("PIPELINE_NOT_FOUND", "UNAUTHORIZED"),
    dependencies=[Depends(require_auth)],
)
async def list_pipeline_screenshots(
    pipeline_id: str,
    session: AsyncSession = Depends(get_session),
) -> dict[str, Any]:
    if await PipelineRepository(session).get(pipeline_id) is None:
        raise _pipeline_not_found(pipeline_id)
    screenshots = await ScreenshotRepository(session).list_by_pipeline(pipeline_id)
    return {
        "items": [_screenshot_wire(item) for item in screenshots],
        "total": len(screenshots),
    }


def _log_wire(entry) -> dict[str, Any]:
    created_at = entry.created_at
    if created_at.tzinfo is None:
        created_at = created_at.replace(tzinfo=timezone.utc)
    meta = entry.meta or {}
    return {
        "id": entry.id,
        "source": SOURCE_DB_TO_WIRE[LogSource(entry.source)],
        "level": str(entry.level).upper(),
        "content": entry.content,
        "ts": created_at.timestamp(),
        "pipeline_id": entry.pipeline_id,
        "task_id": entry.task_id,
        "logger": meta.get("logger"),
        "attachment": meta.get("attachment"),
    }


def _screenshot_wire(item: Screenshot) -> dict[str, Any]:
    return {
        "id": item.id,
        "pipeline_id": item.pipeline_id,
        "task_id": item.task_id,
        "trigger": str(item.trigger),
        "backend": str(item.backend),
        "path": item.path,
        "format": item.format,
        "width": item.width,
        "height": item.height,
        "size_bytes": item.size_bytes,
        "deleted_at": _iso(item.deleted_at),
        "created_at": _iso(item.created_at),
    }
