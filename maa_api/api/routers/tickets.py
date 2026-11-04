"""工单只读端点（M69 规格 §4.3）。

三条端点都从 ``app.state.ticket_service`` 取启动时装配好的
:class:`~maa_api.agent.tickets.service.TicketService`；服务尚未装配（进程启动中、
或测试只装了路由）返回 503 ``SERVICE_UNAVAILABLE``，与 ``routers/runs.py`` 同口径。
分页参数与分页范围在路由层判断（400 ``INVALID_PAGINATION``），工单是否存在交给
服务层抛 404 ``TICKET_NOT_FOUND``（详情缺委托是装配错误，照旧抛 ``RuntimeError``）。

响应模型都写在本文件（规格 §4.3 的八个模型），时间字段是 ``_iso`` 输出的
``...Z`` 字符串；枚举字段直接用领域枚举，这样进 OpenAPI 的是取值列表。工单列表
按服务的口径（``updated_at DESC, id DESC``）分页，``status`` 可重复表示并集。
"""

from __future__ import annotations

from datetime import UTC, datetime
from typing import Any, Literal

from fastapi import APIRouter, Depends, Path, Query, Request
from pydantic import BaseModel

from maa_api.agent.tickets.service import TicketDetailData, TicketService
from maa_api.api.deps import require_auth
from maa_api.api.errors import error_responses
from maa_api.db.models import Mandate, ThreadSubscription, Ticket, TicketReport
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
    "HumanActionOut",
    "MandateOut",
    "SubscriptionOut",
    "TicketDetail",
    "TicketPage",
    "TicketReportOut",
    "TicketReportPage",
    "TicketSummary",
    "router",
]


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


class TicketDetail(BaseModel):
    """工单详情：摘要、结果、委托、订阅、最近 5 条汇报与 Worker 是否在跑。"""

    ticket: TicketSummary
    result: dict[str, Any] | None
    mandate: MandateOut
    subscriptions: list[SubscriptionOut]
    recent_reports: list[TicketReportOut]
    worker_session_id: str | None
    turn_running: bool


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
        raise AppError(ErrorCode.SERVICE_UNAVAILABLE, "工单服务还在启动中，请稍后重试")
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


def _ticket_detail(data: TicketDetailData) -> TicketDetail:
    return TicketDetail(
        ticket=_ticket_summary(data.ticket),
        result=data.ticket.result,
        mandate=_mandate_out(data.mandate),
        subscriptions=[_subscription_out(item) for item in data.subscriptions],
        recent_reports=[_report_out(item) for item in data.recent_reports],
        worker_session_id=data.ticket.worker_session_id,
        turn_running=data.turn_running,
    )


@router.get(
    "",
    response_model=TicketPage,
    summary="分页读取工单",
    responses=error_responses(
        "INVALID_PAGINATION", "SERVICE_UNAVAILABLE", "UNAUTHORIZED"
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
        "TICKET_NOT_FOUND", "SERVICE_UNAVAILABLE", "UNAUTHORIZED"
    ),
    dependencies=[Depends(require_auth)],
)
async def get_ticket(
    request: Request,
    ticket_id: str = Path(min_length=1, max_length=36, description="工单 id"),
) -> TicketDetail:
    return _ticket_detail(await _service(request).detail(ticket_id))


@router.get(
    "/{ticket_id}/reports",
    response_model=TicketReportPage,
    summary="分页读取工单汇报",
    responses=error_responses(
        "TICKET_NOT_FOUND", "INVALID_PAGINATION", "SERVICE_UNAVAILABLE", "UNAUTHORIZED"
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
