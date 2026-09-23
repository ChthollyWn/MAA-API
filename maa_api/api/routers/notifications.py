"""Notification channel configuration and synchronous test-send endpoints."""

from __future__ import annotations

from typing import Any

from fastapi import APIRouter, Depends, Query, Request, Response, status
from pydantic import BaseModel, ConfigDict, Field

from maa_api.api.deps import require_auth
from maa_api.api.errors import error_responses
from maa_api.domain.enums import NotifyChannelType, NotifyEvent
from maa_api.domain.errors import AppError, ErrorCode
from maa_api.services.notify_service import NotifyService, channel_wire

__all__ = ["router"]

router = APIRouter(prefix="/api", tags=["notifications"])


class NotifyChannelRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    type: NotifyChannelType
    name: str = Field(min_length=1, max_length=64)
    config: dict[str, Any]
    events: list[NotifyEvent]
    enabled: bool = True


class NotifyTestRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    event: NotifyEvent = NotifyEvent.PIPELINE_COMPLETED


def _service(request: Request) -> NotifyService:
    service = getattr(request.app.state, "notify_service", None)
    if service is None:
        raise AppError(ErrorCode.SERVICE_UNAVAILABLE, "通知服务尚未启动")
    return service


@router.get(
    "/notifications/channels",
    summary="读取通知通道列表",
    dependencies=[Depends(require_auth)],
)
async def list_notification_channels(
    request: Request,
    type: NotifyChannelType | None = Query(default=None),
    enabled: bool | None = Query(default=None),
) -> dict[str, Any]:
    channels = await _service(request).list_channels(type=type, enabled=enabled)
    return {"items": [channel_wire(item) for item in channels], "total": len(channels)}


@router.post(
    "/notifications/channels",
    status_code=status.HTTP_201_CREATED,
    summary="创建通知通道",
    responses=error_responses("NOTIFY_CONFIG_INVALID", "NOTIFY_CHANNEL_CONFLICT"),
    dependencies=[Depends(require_auth)],
)
async def create_notification_channel(
    payload: NotifyChannelRequest,
    request: Request,
    response: Response,
) -> dict[str, Any]:
    channel = await _service(request).create_channel(**payload.model_dump())
    response.headers["Location"] = f"/api/notifications/channels/{channel.id}"
    return channel_wire(channel)


@router.put(
    "/notifications/channels/{channel_id}",
    summary="更新通知通道",
    responses=error_responses("NOTIFY_CHANNEL_NOT_FOUND", "NOTIFY_CONFIG_INVALID", "NOTIFY_CHANNEL_CONFLICT"),
    dependencies=[Depends(require_auth)],
)
async def update_notification_channel(
    channel_id: str,
    payload: NotifyChannelRequest,
    request: Request,
) -> dict[str, Any]:
    channel = await _service(request).update_channel(channel_id, **payload.model_dump())
    return channel_wire(channel)


@router.delete(
    "/notifications/channels/{channel_id}",
    status_code=status.HTTP_204_NO_CONTENT,
    summary="删除通知通道",
    responses=error_responses("NOTIFY_CHANNEL_NOT_FOUND"),
    dependencies=[Depends(require_auth)],
)
async def delete_notification_channel(channel_id: str, request: Request) -> Response:
    await _service(request).delete_channel(channel_id)
    return Response(status_code=status.HTTP_204_NO_CONTENT)


@router.post(
    "/notifications/channels/{channel_id}/test",
    summary="发送通知测试消息",
    responses=error_responses("NOTIFY_SEND_FAILED", "NOTIFY_CHANNEL_NOT_FOUND"),
    dependencies=[Depends(require_auth)],
)
async def test_notification_channel(
    channel_id: str,
    request: Request,
    payload: NotifyTestRequest | None = None,
) -> dict[str, Any]:
    event = payload.event if payload is not None else NotifyEvent.PIPELINE_COMPLETED
    return await _service(request).test_channel(channel_id, event=event)
