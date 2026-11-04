"""工单端点（M69 规格 §4.3 的只读三条 + M70 §4.3.1 的对话四条）。

七条端点都从 ``app.state.ticket_service`` / ``app.state.worker_service`` 取启动时
装配好的服务；服务尚未装配（进程启动中、或测试只装了路由）返回 503
``AGENT_DISABLED``（spec §4.3 的口径）。分页参数与分页范围在路由层判断
（400 ``INVALID_PAGINATION``），工单是否存在交给服务层抛 404
``TICKET_NOT_FOUND``（详情缺委托是装配错误，照旧抛 ``RuntimeError``）。

响应模型都写在本文件（规格 §4.3 的八个模型），时间字段是 ``_iso`` 输出的
``...Z`` 字符串；枚举字段直接用领域枚举，这样进 OpenAPI 的是取值列表。工单列表
按服务的口径（``updated_at DESC, id DESC``）分页，``status`` 可重复表示并集。

M70 加的对话端点：``POST .../messages`` 返回 SSE（先订阅 ``RunEventStream`` 再写
消息，避免漏事件，见规格 §4.5.8）；消息列表按 ``agent_message.id`` 升序，可带
``parent_session_id`` 的子代理会话，``after_seq`` 指 Worker 会话的 ``seq``。
"""

from __future__ import annotations

from datetime import UTC, datetime
from typing import Any, Literal

from fastapi import APIRouter, Depends, Path, Query, Request
from pydantic import BaseModel
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession
from starlette.responses import StreamingResponse

from maa_api.agent.mentions import MentionContext, render_reference_block
from maa_api.agent.orchestrator.stream import RunEventStream
from maa_api.agent.tickets.service import TicketDetailData, TicketService
from maa_api.api.deps import get_session, require_auth
from maa_api.api.errors import error_responses
from maa_api.api.routers.device_lease import DeviceLeaseOut
from maa_api.api.schemas.agent_v3 import (
    AgentMessageCreate,
    AgentMessagePage,
    TicketCancelRequest,
    TicketInterruptResult,
    agent_message_item,
)
from maa_api.db.models import (
    AgentMessage,
    AgentSession,
    Mandate,
    ThreadSubscription,
    Ticket,
    TicketReport,
)
from maa_api.db.repositories.agent import (
    AgentMessageRepository,
    AgentSessionRepository,
    load_inbox_meta,
)
from maa_api.db.repositories.ticket import TicketRepository
from maa_api.domain.enums import (
    EventSource,
    MandateStrength,
    ReportStatus,
    RiskLevel,
    TicketOrigin,
    TicketStatus,
)
from maa_api.domain.errors import AppError, ErrorCode

__all__ = [
    "ForkedFrom",
    "HumanActionOut",
    "MandateOut",
    "SSE_HEADERS",
    "SubscriptionOut",
    "TicketDetail",
    "TicketPage",
    "TicketReportOut",
    "TicketReportPage",
    "TicketSummary",
    "forked_from_of",
    "router",
    "ticket_detail_out",
]

#: 两条 SSE 端点共用的响应头（与 v2 的 ``stream_session_message`` 一致）。
SSE_HEADERS = {"Cache-Control": "no-cache", "X-Accel-Buffering": "no"}
#: OpenAPI 里声明 SSE 响应体类型；不写 response_model。
SSE_RESPONSES: dict[int, dict[str, Any]] = {
    200: {"content": {"text/event-stream": {}}}
}


class HumanActionOut(BaseModel):
    """待人工处理的动作；``type`` 决定前端弹哪一种卡片。"""

    type: Literal["confirmation", "question", "decision"]
    prompt: str
    ref_id: str | None = None


class TicketSummary(BaseModel):
    """工单列表项与详情里的工单摘要。"""

    id: str
    title: str
    kind: str
    status: TicketStatus
    origin: TicketOrigin
    priority: int
    summary: str | None
    last_report_status: ReportStatus | None
    human_action: HumanActionOut | None
    created_at: str
    updated_at: str
    ended_at: str | None


class MandateOut(BaseModel):
    """工单的委托（建单时写入，此后不可变）。"""

    id: str
    scope: str
    strength: MandateStrength
    source: EventSource
    sender: str | None
    rule_id: str | None
    risk_ceiling: RiskLevel
    policy_version: str | None
    created_at: str


class SubscriptionOut(BaseModel):
    """工单对某个话题键的订阅；停用后保留行。"""

    thread_key: str
    active: bool
    created_at: str
    ended_at: str | None


class TicketReportOut(BaseModel):
    """一次 Worker 汇报的对外形状。"""

    id: int
    ticket_id: str
    run_id: str | None
    status: ReportStatus
    summary: str
    human_action: HumanActionOut | None
    details: dict[str, Any] | None
    forwarded: bool
    created_at: str


class ForkedFrom(BaseModel):
    """分叉来源：源工单与分叉处的消息 seq（由 Worker 会话的两列反查）。"""

    ticket_id: str
    title: str
    at_seq: int


class TicketDetail(BaseModel):
    """工单详情：摘要、结果、委托、订阅、最近 5 条汇报与 Worker 是否在跑。"""

    ticket: TicketSummary
    result: dict[str, Any] | None
    mandate: MandateOut
    subscriptions: list[SubscriptionOut]
    recent_reports: list[TicketReportOut]
    worker_session_id: str | None
    turn_running: bool
    #: 当前设备租约属于本工单时填，否则为 ``null``（M71 §4.3）。
    lease: DeviceLeaseOut | None
    #: 由哪个工单的哪条消息分叉出来（M80 §4.3）；不是分叉出来的为 ``null``。
    forked_from: ForkedFrom | None = None


class TicketPage(BaseModel):
    """工单列表的分页信封。"""

    items: list[TicketSummary]
    total: int
    page: int
    size: int


class TicketReportPage(BaseModel):
    """工单汇报的分页信封。"""

    items: list[TicketReportOut]
    total: int
    page: int
    size: int


router = APIRouter(prefix="/api/agent/tickets", tags=["agent"])


def _service(request: Request) -> TicketService:
    """取工单服务；装配层还没设置时返回 503。"""
    service = getattr(request.app.state, "ticket_service", None)
    if service is None:
        raise AppError(ErrorCode.AGENT_DISABLED, "工单服务还在启动中，请稍后重试")
    return service


def _worker_service(request: Request) -> Any:
    """取 Worker 服务；装配层还没设置时返回 503。"""
    service = getattr(request.app.state, "worker_service", None)
    if service is None:
        raise AppError(ErrorCode.AGENT_DISABLED, "工单服务还在启动中，请稍后重试")
    return service


def _page(page: int, size: int) -> None:
    if page < 1 or size < 1 or size > 200:
        raise AppError(
            ErrorCode.INVALID_PAGINATION, "page 不能小于 1，size 需在 1–200 之间"
        )


def _iso(value: datetime | None) -> str | None:
    """把库里的 naive UTC 时间渲染成 ``...Z``（与 ``routers/agent.py`` 同口径）。"""
    if value is None:
        return None
    if value.tzinfo is None:
        value = value.replace(tzinfo=UTC)
    return value.astimezone(UTC).isoformat().replace("+00:00", "Z")


def _human_action(value: dict[str, Any] | None) -> HumanActionOut | None:
    return None if value is None else HumanActionOut.model_validate(value)


def _ticket_summary(ticket: Ticket) -> TicketSummary:
    return TicketSummary(
        id=ticket.id,
        title=ticket.title,
        kind=ticket.kind,
        status=TicketStatus(ticket.status),
        origin=TicketOrigin(ticket.origin),
        priority=ticket.priority,
        summary=ticket.summary,
        last_report_status=(
            None
            if ticket.last_report_status is None
            else ReportStatus(ticket.last_report_status)
        ),
        human_action=_human_action(ticket.human_action),
        created_at=_iso(ticket.created_at),
        updated_at=_iso(ticket.updated_at),
        ended_at=_iso(ticket.ended_at),
    )


def _mandate_out(mandate: Mandate) -> MandateOut:
    return MandateOut(
        id=mandate.id,
        scope=mandate.scope,
        strength=MandateStrength(mandate.strength),
        source=EventSource(mandate.source),
        sender=mandate.sender,
        rule_id=mandate.rule_id,
        risk_ceiling=RiskLevel(mandate.risk_ceiling),
        policy_version=mandate.policy_version,
        created_at=_iso(mandate.created_at),
    )


def _subscription_out(subscription: ThreadSubscription) -> SubscriptionOut:
    return SubscriptionOut(
        thread_key=subscription.thread_key,
        active=subscription.active,
        created_at=_iso(subscription.created_at),
        ended_at=_iso(subscription.ended_at),
    )


def _report_out(report: TicketReport) -> TicketReportOut:
    return TicketReportOut(
        id=report.id,
        ticket_id=report.ticket_id,
        run_id=report.run_id,
        status=ReportStatus(report.status),
        summary=report.summary,
        human_action=_human_action(report.human_action),
        details=report.details,
        forwarded=report.forwarded,
        created_at=_iso(report.created_at),
    )


async def forked_from_of(
    session: AsyncSession, worker_session_id: str | None
) -> ForkedFrom | None:
    """由 Worker 会话的 ``forked_from_session_id`` / ``forked_from_seq`` 反查来源。"""
    if not worker_session_id:
        return None
    worker = await AgentSessionRepository(session).get(worker_session_id)
    if (
        worker is None
        or not worker.forked_from_session_id
        or worker.forked_from_seq is None
    ):
        return None
    source = await AgentSessionRepository(session).get(worker.forked_from_session_id)
    if source is None or not source.ticket_id:
        return None
    ticket = await TicketRepository(session).get(source.ticket_id)
    if ticket is None:
        return None
    return ForkedFrom(
        ticket_id=str(ticket.id),
        title=ticket.title,
        at_seq=int(worker.forked_from_seq),
    )


def ticket_detail_out(
    data: TicketDetailData,
    *,
    lease: DeviceLeaseOut | None = None,
    forked_from: ForkedFrom | None = None,
) -> TicketDetail:
    return TicketDetail(
        ticket=_ticket_summary(data.ticket),
        result=data.ticket.result,
        mandate=_mandate_out(data.mandate),
        subscriptions=[_subscription_out(item) for item in data.subscriptions],
        recent_reports=[_report_out(item) for item in data.recent_reports],
        worker_session_id=data.ticket.worker_session_id,
        turn_running=data.turn_running,
        lease=lease,
        forked_from=forked_from,
    )


@router.get(
    "",
    response_model=TicketPage,
    summary="分页读取工单",
    responses=error_responses(
        "INVALID_PAGINATION", "AGENT_DISABLED", "UNAUTHORIZED"
    ),
    dependencies=[Depends(require_auth)],
)
async def list_tickets(
    request: Request,
    statuses: list[TicketStatus] | None = Query(
        None, alias="status", description="只看这些状态，可重复表示并集"
    ),
    kind: str | None = Query(None, description="只看这个代理定义的工单"),
    origin: TicketOrigin | None = Query(None, description="只看这个来源的工单"),
    page: int = Query(1, description="页码，从 1 开始"),
    size: int = Query(20, description="每页条数，1–200"),
) -> TicketPage:
    _page(page, size)
    result = await _service(request).list(
        statuses=statuses,
        kind=kind,
        origin=origin,
        page=page,
        size=size,
    )
    return TicketPage(
        items=[_ticket_summary(item) for item in result.items],
        total=result.total,
        page=result.page,
        size=result.size,
    )


@router.get(
    "/{ticket_id}",
    response_model=TicketDetail,
    summary="读取工单详情",
    responses=error_responses(
        "TICKET_NOT_FOUND", "AGENT_DISABLED", "UNAUTHORIZED"
    ),
    dependencies=[Depends(require_auth)],
)
async def get_ticket(
    request: Request,
    ticket_id: str = Path(min_length=1, max_length=36, description="工单 id"),
    session: AsyncSession = Depends(get_session),
) -> TicketDetail:
    data = await _service(request).detail(ticket_id)
    lease: DeviceLeaseOut | None = None
    lease_service = getattr(request.app.state, "device_lease_service", None)
    if lease_service is not None:
        row = await lease_service.current()
        if row is not None and row.ticket_id == ticket_id:
            lease = DeviceLeaseOut.model_validate(await lease_service.wire(row))
    return ticket_detail_out(
        data,
        lease=lease,
        forked_from=await forked_from_of(session, data.ticket.worker_session_id),
    )


@router.get(
    "/{ticket_id}/reports",
    response_model=TicketReportPage,
    summary="分页读取工单汇报",
    responses=error_responses(
        "TICKET_NOT_FOUND", "INVALID_PAGINATION", "AGENT_DISABLED", "UNAUTHORIZED"
    ),
    dependencies=[Depends(require_auth)],
)
async def list_ticket_reports(
    request: Request,
    ticket_id: str = Path(min_length=1, max_length=36, description="工单 id"),
    page: int = Query(1, description="页码，从 1 开始"),
    size: int = Query(20, description="每页条数，1–200"),
) -> TicketReportPage:
    _page(page, size)
    result = await _service(request).list_reports(ticket_id, page=page, size=size)
    return TicketReportPage(
        items=[_report_out(item) for item in result.items],
        total=result.total,
        page=result.page,
        size=result.size,
    )


# ----------------------------------------------------------------------
# M70：工单里的对话与中断（规格 §4.3.1）
# ----------------------------------------------------------------------
@router.post(
    "/{ticket_id}/messages",
    response_class=StreamingResponse,
    summary="向工单发送消息并以 SSE 接收事件",
    responses={**SSE_RESPONSES, **error_responses(
        "TICKET_NOT_FOUND",
        "TICKET_ENDED",
        "LLM_NOT_CONFIGURED",
        "VALIDATION_ERROR",
        "AGENT_DISABLED",
        "UNAUTHORIZED",
    )},
    dependencies=[Depends(require_auth)],
)
async def send_ticket_message(
    payload: AgentMessageCreate,
    request: Request,
    ticket_id: str = Path(min_length=1, max_length=36, description="工单 id"),
) -> StreamingResponse:
    """校验工单与 LLM 配置 → 订阅事件 → 写用户消息 → 返回这条消息的 SSE 流。"""
    service = _worker_service(request)
    ticket = await service.require_open_ticket(ticket_id)
    await service.ensure_ready(ticket)
    session = await service.ensure_worker_session(ticket)
    stream = RunEventStream(request.app, session_id=str(session.id))
    try:
        block = await render_reference_block(
            payload.mentions,
            MentionContext(request.app.state, request.app.state.session_factory),
        )
        posted = await service.post_user_message(
            ticket_id,
            payload.content + block,
            [mention.model_dump() for mention in payload.mentions or []],
            notify_content=payload.content,
        )
    except BaseException:
        stream.close()
        raise
    stream.track(posted.inbox_id)
    return StreamingResponse(
        stream.frames(), media_type="text/event-stream", headers=SSE_HEADERS
    )


@router.get(
    "/{ticket_id}/messages",
    response_model=AgentMessagePage,
    summary="分页读取工单的消息",
    responses=error_responses(
        "TICKET_NOT_FOUND", "INVALID_PAGINATION", "AGENT_DISABLED", "UNAUTHORIZED"
    ),
    dependencies=[Depends(require_auth)],
)
async def list_ticket_messages(
    ticket_id: str = Path(min_length=1, max_length=36, description="工单 id"),
    after_seq: int | None = Query(
        default=None, ge=-1, description="只取 Worker 会话里 seq 更大的消息"
    ),
    page: int = Query(default=1, description="页码，从 1 开始"),
    size: int = Query(default=20, description="每页条数，1–200"),
    include_subagents: bool = Query(
        default=False, description="是否带上该 Worker 会话的子代理消息"
    ),
    session: AsyncSession = Depends(get_session),
) -> AgentMessagePage:
    """Worker 会话的消息；``include_subagents`` 时加上子代理会话，按 id 升序。"""
    _page(page, size)
    ticket = await TicketRepository(session).get(ticket_id)
    if ticket is None:
        raise AppError(
            ErrorCode.TICKET_NOT_FOUND, "没有这个工单", {"ticket_id": ticket_id}
        )
    worker_session_id = ticket.worker_session_id
    if not worker_session_id:
        return AgentMessagePage(items=[], total=0, page=page, size=size)

    session_ids = [worker_session_id]
    if include_subagents:
        rows = await session.execute(
            select(AgentSession.id).where(
                AgentSession.parent_session_id == worker_session_id
            )
        )
        session_ids.extend(str(item) for item in rows.scalars())

    conditions = [AgentMessage.session_id.in_(session_ids)]
    if after_seq is not None and after_seq >= 0:
        # ``after_seq`` 是 Worker 会话的 seq；换算成那条消息（或它之前最后一条）
        # 的 id 后取 id 更大的行，子代理消息才能按同一时间线一起过滤。
        boundary = await session.scalar(
            select(func.max(AgentMessage.id)).where(
                AgentMessage.session_id == worker_session_id,
                AgentMessage.seq <= after_seq,
            )
        )
        if boundary is not None:
            conditions.append(AgentMessage.id > int(boundary))
    stmt = (
        select(AgentMessage)
        .where(*conditions)
        .order_by(AgentMessage.id.asc())
    )
    count_stmt = (
        select(func.count())
        .select_from(AgentMessage)
        .where(*conditions)
    )
    result = await AgentMessageRepository(session).paginate(
        stmt, count_stmt, page=page, size=size
    )
    inbox_meta = await load_inbox_meta(
        session,
        {item.inbox_id for item in result.items if item.inbox_id is not None},
    )
    return AgentMessagePage(
        items=[agent_message_item(item, inbox_meta) for item in result.items],
        total=result.total,
        page=result.page,
        size=result.size,
    )


@router.post(
    "/{ticket_id}/interrupt",
    response_model=TicketInterruptResult,
    summary="中断工单正在执行的一轮",
    responses=error_responses(
        "TICKET_NOT_FOUND", "AGENT_DISABLED", "UNAUTHORIZED"
    ),
    dependencies=[Depends(require_auth)],
)
async def interrupt_ticket(
    request: Request,
    ticket_id: str = Path(min_length=1, max_length=36, description="工单 id"),
) -> TicketInterruptResult:
    """幂等：没有活动轮次时返回 200 且 ``interrupted=false``。"""
    result = await _worker_service(request).interrupt(ticket_id)
    return TicketInterruptResult(**result)


@router.post(
    "/{ticket_id}/cancel",
    response_model=TicketDetail,
    summary="取消工单",
    responses=error_responses(
        "TICKET_NOT_FOUND", "TICKET_ENDED", "AGENT_DISABLED", "UNAUTHORIZED"
    ),
    dependencies=[Depends(require_auth)],
)
async def cancel_ticket(
    payload: TicketCancelRequest,
    request: Request,
    ticket_id: str = Path(min_length=1, max_length=36, description="工单 id"),
    session: AsyncSession = Depends(get_session),
) -> TicketDetail:
    """停掉 Worker 与子代理（已提交的流水线不停），返回取消后的工单详情。"""
    data = await _worker_service(request).cancel(
        ticket_id, reason=payload.reason, actor="user"
    )
    return ticket_detail_out(
        data,
        forked_from=await forked_from_of(session, data.ticket.worker_session_id),
    )
