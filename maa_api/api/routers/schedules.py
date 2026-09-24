"""Schedule CRUD and queue-backed execution API."""

from __future__ import annotations

from typing import Any

from fastapi import APIRouter, Depends, Query, Request, Response, status
from pydantic import BaseModel

from maa_api.api.deps import require_auth
from maa_api.api.errors import error_responses
from maa_api.domain.errors import AppError, ErrorCode
from maa_api.services.schedule_service import (
    SchedulePage,
    SchedulePatch,
    ScheduleRunRequest,
    ScheduleService,
    ScheduleView,
    ScheduleWrite,
)

__all__ = ["router"]

router = APIRouter(prefix="/api/schedules", tags=["schedules"])


class ScheduleRunAccepted(BaseModel):
    schedule_id: str
    pipeline_id: str
    status: str
    priority: int


def _service(request: Request) -> ScheduleService:
    service = getattr(request.app.state, "schedule_service", None)
    if service is None:
        raise AppError(ErrorCode.SERVICE_UNAVAILABLE, "定时任务服务尚未启动")
    return service


@router.get(
    "",
    response_model=SchedulePage,
    summary="读取定时任务",
    responses=error_responses("UNAUTHORIZED"),
    dependencies=[Depends(require_auth)],
)
async def list_schedules(
    request: Request,
    enabled: bool | None = Query(default=None),
) -> SchedulePage:
    return await _service(request).list(enabled=enabled)


@router.post(
    "",
    response_model=ScheduleView,
    status_code=status.HTTP_201_CREATED,
    summary="新建定时任务",
    responses=error_responses(
        "SCHEDULE_CRON_INVALID",
        "SCHEDULE_NAME_CONFLICT",
        "TASK_PARAM_INVALID",
        "UNKNOWN_TASK_TYPE",
        "TASK_PARAM_DEPRECATED",
        "VALIDATION_ERROR",
        "UNAUTHORIZED",
    ),
    dependencies=[Depends(require_auth)],
)
async def create_schedule(
    payload: ScheduleWrite,
    request: Request,
    response: Response,
) -> ScheduleView:
    schedule = await _service(request).create(payload)
    response.headers["Location"] = "/api/schedules/" + schedule.id
    return schedule


@router.get(
    "/{schedule_id}",
    response_model=ScheduleView,
    summary="读取定时任务与近期执行结果",
    responses=error_responses("SCHEDULE_NOT_FOUND", "UNAUTHORIZED"),
    dependencies=[Depends(require_auth)],
)
async def get_schedule(schedule_id: str, request: Request) -> ScheduleView:
    return await _service(request).get(schedule_id)


@router.put(
    "/{schedule_id}",
    response_model=ScheduleView,
    summary="全量替换定时任务",
    responses=error_responses(
        "SCHEDULE_NOT_FOUND",
        "SCHEDULE_CRON_INVALID",
        "SCHEDULE_NAME_CONFLICT",
        "TASK_PARAM_INVALID",
        "UNKNOWN_TASK_TYPE",
        "TASK_PARAM_DEPRECATED",
        "VALIDATION_ERROR",
        "UNAUTHORIZED",
    ),
    dependencies=[Depends(require_auth)],
)
async def update_schedule(
    schedule_id: str,
    payload: ScheduleWrite,
    request: Request,
) -> ScheduleView:
    return await _service(request).update(schedule_id, payload)


@router.patch(
    "/{schedule_id}",
    response_model=ScheduleView,
    summary="局部更新定时任务",
    responses=error_responses(
        "SCHEDULE_NOT_FOUND",
        "SCHEDULE_CRON_INVALID",
        "VALIDATION_ERROR",
        "UNAUTHORIZED",
    ),
    dependencies=[Depends(require_auth)],
)
async def patch_schedule(
    schedule_id: str,
    payload: SchedulePatch,
    request: Request,
) -> ScheduleView:
    changes: dict[str, Any] = payload.model_dump(exclude_unset=True)
    # Nullable fields mean "not supplied" for this PATCH contract.
    return await _service(request).patch(
        schedule_id,
        {key: value for key, value in changes.items() if value is not None},
    )


@router.delete(
    "/{schedule_id}",
    status_code=status.HTTP_204_NO_CONTENT,
    response_class=Response,
    summary="删除定时任务",
    responses=error_responses("SCHEDULE_NOT_FOUND", "UNAUTHORIZED"),
    dependencies=[Depends(require_auth)],
)
async def delete_schedule(schedule_id: str, request: Request) -> None:
    await _service(request).delete(schedule_id)
    return None


@router.post(
    "/{schedule_id}/run",
    response_model=ScheduleRunAccepted,
    status_code=status.HTTP_202_ACCEPTED,
    summary="立即运行定时任务",
    responses=error_responses(
        "SCHEDULE_NOT_FOUND",
        "QUEUE_FULL",
        "QUEUE_PAUSED",
        "PIPELINE_EMPTY",
        "PIPELINE_ALREADY_RUNNING",
        "TASK_PARAM_INVALID",
        "UNKNOWN_TASK_TYPE",
        "UNAUTHORIZED",
    ),
    dependencies=[Depends(require_auth)],
)
async def run_schedule(
    schedule_id: str,
    payload: ScheduleRunRequest,
    request: Request,
    response: Response,
) -> ScheduleRunAccepted:
    pipeline = await _service(request).run_now(
        schedule_id, priority=payload.priority
    )
    response.headers["Location"] = "/api/pipelines/" + pipeline.id
    return ScheduleRunAccepted(
        schedule_id=schedule_id,
        pipeline_id=pipeline.id,
        status=str(pipeline.status),
        priority=int(pipeline.priority),
    )
