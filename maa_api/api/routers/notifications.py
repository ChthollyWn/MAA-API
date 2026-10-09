"""通知通道配置、同步测试发送与 Web Push 订阅。"""

from __future__ import annotations

from typing import Any

from fastapi import APIRouter, Depends, Query, Request, Response, status
from pydantic import BaseModel, ConfigDict, Field

from maa_api.api.deps import require_auth
from maa_api.api.errors import error_responses
from maa_api.domain.enums import NotifyChannelType, NotifyEvent
from maa_api.domain.errors import AppError, ErrorCode
from maa_api.services.notify_service import NotifyService, channel_wire
from maa_api.services.push_subscription_service import (
    PushSubscriptionService,
    subscription_wire,
)

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
        raise AppError(ErrorCode.SERVICE_UNAVAILABLE, "通知功能还在启动中，请稍后重试")
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


# --------------------------------------------------------------------------
# Web Push（docs/09 §13.1）
# --------------------------------------------------------------------------


class PushSubscribeRequest(BaseModel):
    """浏览器 ``PushSubscription.toJSON()`` 的子集，``expirationTime`` 忽略。"""

    model_config = ConfigDict(extra="ignore")

    endpoint: str = Field(min_length=1, max_length=2048)
    keys: dict[str, str]


class PushUnsubscribeRequest(BaseModel):
    """按 ``endpoint`` 注销订阅。"""

    model_config = ConfigDict(extra="ignore")

    endpoint: str = Field(min_length=1, max_length=2048)


def _push_service(request: Request) -> PushSubscriptionService:
    service = getattr(request.app.state, "push_subscription_service", None)
    if service is None:
        raise AppError(ErrorCode.SERVICE_UNAVAILABLE, "推送订阅功能还在启动中，请稍后重试")
    return service


@router.get(
    "/notifications/vapid-public-key",
    summary="读取 Web Push 的 VAPID 公钥",
    description=(
        "返回 VAPID 公钥，用于浏览器端 `pushManager.subscribe` 的 "
        "`applicationServerKey`。\n\n"
        "- 未配置私钥或私钥不可用时返回 503 `PUSH_NOT_CONFIGURED`"
    ),
    responses=error_responses("PUSH_NOT_CONFIGURED"),
    dependencies=[Depends(require_auth)],
)
async def get_vapid_public_key(request: Request) -> dict[str, Any]:
    return await _push_service(request).public_key()


@router.get(
    "/notifications/push-subscriptions",
    summary="列出 Web Push 订阅",
    description=(
        "为保护凭据，响应不含 `endpoint` 全文与 `p256dh` / `auth`，"
        "只返回设备来源、登记时间与失效状态。\n\n"
        "- `include_disabled=true` 时同时返回已失效的订阅"
    ),
    dependencies=[Depends(require_auth)],
)
async def list_push_subscriptions(
    request: Request,
    include_disabled: bool = Query(default=False),
) -> dict[str, Any]:
    subscriptions = await _push_service(request).list_subscriptions(
        include_disabled=include_disabled
    )
    return {
        "items": [subscription_wire(item) for item in subscriptions],
        "total": len(subscriptions),
    }


@router.post(
    "/notifications/push-subscriptions",
    status_code=status.HTTP_201_CREATED,
    summary="登记 Web Push 订阅",
    description=(
        "登记浏览器推送订阅，接受 `PushSubscription.toJSON()` 的形状。\n\n"
        "- 同一 `endpoint` 重复登记是幂等更新：返回 200 与既有资源，不新增行\n"
        "- `endpoint` 必须是 https 绝对 URL；`keys.p256dh` 必须是 65 octet 的未压缩 "
        "P-256 点；`keys.auth` 必须是 16 octet"
    ),
    # 幂等重放固定返回 200；契约要显式声明这一档，否则前端生成的类型缺它。
    responses={
        200: {"description": "该 endpoint 已登记，本次为幂等更新"},
        **error_responses("PUSH_SUBSCRIPTION_INVALID"),
    },
    dependencies=[Depends(require_auth)],
)
async def create_push_subscription(
    payload: PushSubscribeRequest,
    request: Request,
    response: Response,
) -> Any:
    stored, created = await _push_service(request).subscribe(
        endpoint=payload.endpoint,
        p256dh=payload.keys.get("p256dh", ""),
        auth=payload.keys.get("auth", ""),
        user_agent=request.headers.get("user-agent"),
    )
    response.headers["Location"] = f"/api/notifications/push-subscriptions/{stored.id}"
    if not created:
        response.status_code = status.HTTP_200_OK
    return subscription_wire(stored)


@router.delete(
    "/notifications/push-subscriptions/{subscription_id}",
    status_code=status.HTTP_204_NO_CONTENT,
    summary="删除 Web Push 订阅",
    responses=error_responses("PUSH_SUBSCRIPTION_NOT_FOUND"),
    dependencies=[Depends(require_auth)],
)
async def delete_push_subscription(subscription_id: str, request: Request) -> Response:
    await _push_service(request).delete(subscription_id)
    return Response(status_code=status.HTTP_204_NO_CONTENT)


@router.post(
    "/notifications/push-subscriptions/delete",
    status_code=status.HTTP_204_NO_CONTENT,
    summary="按 endpoint 注销当前浏览器的订阅",
    description=(
        "按 `endpoint` 精确注销当前浏览器的订阅。\n\n"
        "- `endpoint` 放在请求体，避免它进入访问日志与浏览器历史\n"
        "- 幂等：找不到对应订阅同样返回 204"
    ),
    dependencies=[Depends(require_auth)],
)
async def delete_push_subscription_by_endpoint(
    payload: PushUnsubscribeRequest, request: Request
) -> Response:
    await _push_service(request).delete_by_endpoint(payload.endpoint)
    return Response(status_code=status.HTTP_204_NO_CONTENT)
