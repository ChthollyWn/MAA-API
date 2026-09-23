"""Queue inspection and controls (docs/05 §6.6)."""

from __future__ import annotations

from fastapi import APIRouter, Depends, Query, Response
from pydantic import BaseModel, ConfigDict, Field

from maa_api.api.deps import get_pipeline_runner, get_queue_service, require_auth
from maa_api.api.errors import error_responses
from maa_api.domain.enums import Priority

__all__ = ["router"]

router = APIRouter(prefix="/api/queue", tags=["queue"])


class PriorityRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    priority: int = Field(ge=0, le=2)


@router.get(
    "",
    summary="获取队列快照",
    responses=error_responses("UNAUTHORIZED"),
    dependencies=[Depends(require_auth)],
)
async def get_queue(queue_service=Depends(get_queue_service)):
    return await queue_service.snapshot()


@router.delete(
    "",
    status_code=204,
    response_class=Response,
    summary="取消所有待执行流水线",
    responses=error_responses("INVALID_PARAMETER", "UNAUTHORIZED"),
    dependencies=[Depends(require_auth)],
)
async def clear_queue(
    source: str | None = Query(default=None),
    runner=Depends(get_pipeline_runner),
) -> None:
    await runner.cancel_pending(source=source)
    return None


@router.patch(
    "/{pipeline_id}",
    summary="调整待执行流水线优先级",
    responses=error_responses(
        "PIPELINE_NOT_FOUND", "QUEUE_ITEM_NOT_PENDING", "UNAUTHORIZED"
    ),
    dependencies=[Depends(require_auth)],
)
async def reprioritize_pipeline(
    pipeline_id: str,
    payload: PriorityRequest,
    queue_service=Depends(get_queue_service),
    runner=Depends(get_pipeline_runner),
):
    pipeline = await queue_service.reprioritize(
        pipeline_id, Priority(payload.priority)
    )
    await runner.publish_queue_changed()
    return {
        "pipeline_id": pipeline.id,
        "priority": pipeline.priority,
        "status": str(pipeline.status),
    }


@router.post(
    "/pause",
    summary="暂停队列消费",
    responses=error_responses("UNAUTHORIZED"),
    dependencies=[Depends(require_auth)],
)
async def pause_queue(
    queue_service=Depends(get_queue_service),
    runner=Depends(get_pipeline_runner),
):
    await queue_service.pause()
    await runner.publish_queue_changed()
    return {"paused": True}


@router.post(
    "/resume",
    summary="恢复队列消费",
    responses=error_responses("UNAUTHORIZED"),
    dependencies=[Depends(require_auth)],
)
async def resume_queue(
    queue_service=Depends(get_queue_service),
    runner=Depends(get_pipeline_runner),
):
    await queue_service.resume()
    await runner.publish_queue_changed()
    return {"paused": False}
