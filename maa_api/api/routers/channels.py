"""IM 通道与发件箱端点（M85 规格 §4.3）。

六个操作与 04 §2.4 的 M85 行一一对应。路由层只做平台参数、分页范围与
业务错误码的出口：绑定码、绑定列表、测试消息与发件箱都经
:class:`~maa_api.agent.channels.service.ChannelService`；服务未装配
（lifespan 还在启动）时统一回 503 ``SERVICE_UNAVAILABLE``。

DTO 写在本文件里（04 §1 允许），因为这些都是本里程碑新增的读模型。
"""

from __future__ import annotations

from datetime import UTC, datetime
from typing import Any

from fastapi import APIRouter, Depends, Path, Query, Request, Response, status
from pydantic import BaseModel

from maa_api.agent.channels.service import ChannelService
from maa_api.api.deps import require_auth
from maa_api.api.errors import error_responses
from maa_api.domain.enums import ChannelPlatform, OutboxChannel, OutboxKind, OutboxStatus
from maa_api.domain.errors import AppError, ErrorCode

__all__ = [
    "BindCodeOut",
    "BindingListOut",
    "BindingOut",
    "ChannelOut",
    "OutboxOut",
    "OutboxPage",
    "TestChannelOut",
    "router",
]

router = APIRouter(prefix="/api/agent", tags=["agent"])

#: 发件箱分页上限；与其它 agent 列表同口径。
MAX_PAGE_SIZE = 200


class ChannelOut(BaseModel):
    """一个平台的开关、配置与连接状态；不含密钥、不含绑定列表。"""

    platform: ChannelPlatform
    enabled: bool
    configured: bool
    connected: bool | None = None
    last_error: str | None = None


class BindCodeOut(BaseModel):
    """一次性绑定码；``expires_at`` 是 UTC 时间。"""

    code: str
    expires_at: datetime


class BindingOut(BaseModel):
    """一条通道绑定；外部身份与会话 id 是数据，不是凭据。"""

    id: str
    platform: ChannelPlatform
    external_user_id: str
    external_chat_id: str
    display_name: str | None = None
    status: str
    bound_at: datetime
    last_seen_at: datetime | None = None
    revoked_at: datetime | None = None


class BindingListOut(BaseModel):
    """绑定列表（不分页；数量由真实绑定数决定）。"""

    items: list[BindingOut]


class TestChannelOut(BaseModel):
    """测试消息同步发送的结果：发给了几条 active 绑定。"""

    sent: int


class OutboxOut(BaseModel):
    """发件箱的一行；``payload`` 原样返回，``receipt`` 为平台回执。"""

    id: int
    channel: OutboxChannel
    target: str
    kind: OutboxKind
    payload: dict[str, Any]
    dedupe_key: str
    status: OutboxStatus
    attempts: int
    next_attempt_at: datetime
    last_error: str | None = None
    receipt: dict[str, Any] | None = None
    ref_run_id: str | None = None
    ref_ticket_id: str | None = None
    created_at: datetime
    sent_at: datetime | None = None


class OutboxPage(BaseModel):
    """发件箱分页信封（§4.3 / 04 §1）。"""

    items: list[OutboxOut]
    total: int
    page: int
    size: int


def _service(request: Request) -> ChannelService:
    service = getattr(request.app.state, "channel_service", None)
    if service is None:
        raise AppError(
            ErrorCode.SERVICE_UNAVAILABLE, "IM 通道服务还在启动中，请稍后重试"
        )
    return service


def _utc(value: datetime | None) -> datetime | None:
    """库里的 naive UTC → 带时区的 UTC，Pydantic 会渲染成 ``...Z``。"""
    if value is None:
        return None
    if value.tzinfo is None:
        return value.replace(tzinfo=UTC)
    return value.astimezone(UTC)


def _validate_pagination(page: int, size: int) -> None:
    if page < 1 or size < 1 or size > MAX_PAGE_SIZE:
        raise AppError(
            ErrorCode.INVALID_PAGINATION,
            f"page 不能小于 1，size 需在 1–{MAX_PAGE_SIZE} 之间",
        )


def _binding_out(row: Any) -> BindingOut:
    return BindingOut(
        id=str(row.id),
        platform=ChannelPlatform(row.platform),
        external_user_id=row.external_user_id,
        external_chat_id=row.external_chat_id,
        display_name=row.display_name,
        status=row.status,
        bound_at=_utc(row.bound_at),
        last_seen_at=_utc(row.last_seen_at),
        revoked_at=_utc(row.revoked_at),
    )


def _outbox_out(row: Any) -> OutboxOut:
    return OutboxOut(
        id=int(row.id),
        channel=OutboxChannel(row.channel),
        target=row.target,
        kind=OutboxKind(row.kind),
        payload=dict(row.payload or {}),
        dedupe_key=row.dedupe_key,
        status=OutboxStatus(row.status),
        attempts=int(row.attempts or 0),
        next_attempt_at=_utc(row.next_attempt_at),
        last_error=row.last_error,
        receipt=dict(row.receipt) if row.receipt is not None else None,
        ref_run_id=row.ref_run_id,
        ref_ticket_id=row.ref_ticket_id,
        created_at=_utc(row.created_at),
        sent_at=_utc(row.sent_at),
    )


@router.get(
    "/channels",
    response_model=list[ChannelOut],
    summary="读取 IM 通道状态",
    responses=error_responses("SERVICE_UNAVAILABLE", "UNAUTHORIZED"),
    dependencies=[Depends(require_auth)],
)
async def list_channels(request: Request) -> list[ChannelOut]:
    """四个平台的启用、配置与连接状态；不含密钥与绑定。"""
    rows = await _service(request).list_channels()
    return [ChannelOut(**row) for row in rows]


@router.post(
    "/channels/{platform}/bind-codes",
    response_model=BindCodeOut,
    status_code=status.HTTP_201_CREATED,
    summary="生成一次性绑定码",
    responses=error_responses(
        "BIND_CODE_INVALID", "SERVICE_UNAVAILABLE", "UNAUTHORIZED"
    ),
    dependencies=[Depends(require_auth)],
)
async def create_bind_code(
    request: Request,
    platform: str = Path(description="telegram / feishu / dingtalk / qq"),
) -> BindCodeOut:
    """写一条 10 分钟有效的绑定码；非法平台回 400 ``BIND_CODE_INVALID``。"""
    row = await _service(request).create_bind_code(platform)
    return BindCodeOut(code=row.code, expires_at=_utc(row.expires_at))


@router.get(
    "/channels/bindings",
    response_model=BindingListOut,
    summary="读取通道绑定",
    responses=error_responses("SERVICE_UNAVAILABLE", "UNAUTHORIZED"),
    dependencies=[Depends(require_auth)],
)
async def list_channel_bindings(request: Request) -> BindingListOut:
    """active 绑定列表；不含密钥。"""
    rows = await _service(request).list_bindings()
    return BindingListOut(items=[_binding_out(row) for row in rows])


@router.delete(
    "/channels/bindings/{binding_id}",
    status_code=status.HTTP_204_NO_CONTENT,
    summary="解除通道绑定",
    responses=error_responses(
        "CHANNEL_BINDING_NOT_FOUND", "SERVICE_UNAVAILABLE", "UNAUTHORIZED"
    ),
    dependencies=[Depends(require_auth)],
)
async def revoke_channel_binding(
    request: Request,
    binding_id: str = Path(min_length=1, max_length=36, description="绑定 id"),
) -> Response:
    """置为 revoked；不存在或已撤销回 404 ``CHANNEL_BINDING_NOT_FOUND``。"""
    await _service(request).revoke_binding(binding_id)
    return Response(status_code=status.HTTP_204_NO_CONTENT)


@router.post(
    "/channels/{platform}/test",
    response_model=TestChannelOut,
    summary="发送通道测试消息",
    responses=error_responses(
        "CHANNEL_NOT_CONFIGURED",
        "CHANNEL_SEND_FAILED",
        "BIND_CODE_INVALID",
        "SERVICE_UNAVAILABLE",
        "UNAUTHORIZED",
    ),
    dependencies=[Depends(require_auth)],
)
async def test_channel(
    request: Request,
    platform: str = Path(description="telegram / feishu / dingtalk / qq"),
) -> TestChannelOut:
    """同步发给该平台全部 active 绑定；未配置 503、发送失败 502。"""
    sent = await _service(request).test_channel(platform)
    return TestChannelOut(sent=sent)


@router.get(
    "/outbox",
    response_model=OutboxPage,
    summary="分页读取发件箱",
    responses=error_responses(
        "INVALID_PAGINATION", "SERVICE_UNAVAILABLE", "UNAUTHORIZED"
    ),
    dependencies=[Depends(require_auth)],
)
async def list_outbox(
    request: Request,
    status_filter: OutboxStatus | None = Query(
        default=None, alias="status", description="按发送状态过滤"
    ),
    channel: OutboxChannel | None = Query(
        default=None, description="按出口过滤（im_* 或 webpush）"
    ),
    page: int = Query(default=1, description="页码，从 1 开始"),
    size: int = Query(default=20, description="每页条数，1–200"),
) -> OutboxPage:
    """``created_at`` 倒序分页；``status`` / ``channel`` 都可选。"""
    _validate_pagination(page, size)
    result = await _service(request).list_outbox(
        status=status_filter.value if status_filter is not None else None,
        channel=channel.value if channel is not None else None,
        page=page,
        size=size,
    )
    return OutboxPage(
        items=[_outbox_out(row) for row in result.items],
        total=result.total,
        page=result.page,
        size=result.size,
    )
