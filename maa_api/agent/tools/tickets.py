"""五个工单工具：总控建单、补充、查询与取消（M70 规格 §4.5.11）。

工具壳只做参数与调用方检查，业务在 :class:`WorkerService`（建单、补充、取消）与
:class:`TicketService`（列表、详情）里；T9/T11 落地之前测试通过 ``app.state`` 上的
替身注入。五个工具都不进 REST 目录（§4.5.12）。
"""

from __future__ import annotations

from collections.abc import Mapping
from datetime import UTC, datetime
from typing import Any

from pydantic import BaseModel, ConfigDict, Field

from maa_api.agent.harness.context import RunContext, require_run
from maa_api.agent.registry import ToolContext, ToolDefinition, ToolRegistry, ToolRisk
from maa_api.agent.specs.registry import builtin_specs
from maa_api.agent.specs.schema import SPEC_NAME_PATTERN
from maa_api.agent.tools._services import app_state, v3_service
from maa_api.domain.enums import TicketStatus
from maa_api.domain.errors import AppError, ErrorCode

__all__ = [
    "CancelTicketParams",
    "CreateTicketParams",
    "GetTicketParams",
    "ListTicketsParams",
    "SendToTicketParams",
    "register_tools",
]

CREATE_TICKET_DESCRIPTION = "要动设备、要多步骤、要查资料或分析时用：建一个工单交给 Worker 去做。"
SEND_TO_TICKET_DESCRIPTION = "用户对进行中的工单补充或修改要求时用：把补充说明转给那个 Worker。"
LIST_TICKETS_DESCRIPTION = "用户问有哪些事在办、办到哪了时用。"
GET_TICKET_DESCRIPTION = "要看某个工单的委托、最近汇报与是否在运行时用。"
CANCEL_TICKET_DESCRIPTION = "用户要放弃某个工单时用。只停 Worker，不停已经提交的流水线。"


class CreateTicketParams(BaseModel):
    """``create_ticket`` 的参数（规格 §4.5.11）。"""

    model_config = ConfigDict(extra="forbid")

    kind: str = Field(min_length=2, max_length=32, pattern=SPEC_NAME_PATTERN)
    title: str = Field(min_length=1, max_length=64)
    brief: str = Field(min_length=1, max_length=4000)
    scope: str = Field(min_length=1, max_length=2000)


class SendToTicketParams(BaseModel):
    """``send_to_ticket`` 的参数（规格 §4.5.11）。"""

    model_config = ConfigDict(extra="forbid")

    ticket_id: str = Field(min_length=1, max_length=36)
    content: str = Field(min_length=1, max_length=4000)


class ListTicketsParams(BaseModel):
    """``list_tickets`` 的参数（规格 §4.5.11）。"""

    model_config = ConfigDict(extra="forbid")

    status: list[TicketStatus] | None = Field(default=None, max_length=6)
    limit: int = Field(default=10, ge=1, le=20)


class GetTicketParams(BaseModel):
    """``get_ticket`` 的参数（规格 §4.5.11）。"""

    model_config = ConfigDict(extra="forbid")

    ticket_id: str = Field(min_length=1, max_length=36)


class CancelTicketParams(BaseModel):
    """``cancel_ticket`` 的参数（规格 §4.5.11）。"""

    model_config = ConfigDict(extra="forbid")

    ticket_id: str = Field(min_length=1, max_length=36)
    reason: str = Field(min_length=1, max_length=200)


def register_tools(registry: ToolRegistry) -> None:
    """注册 ``tickets`` 组的五个工具；全部不进 REST 目录。"""

    for name, description, params_model, handler, read_only in (
        ("create_ticket", CREATE_TICKET_DESCRIPTION, CreateTicketParams, create_ticket, False),
        ("send_to_ticket", SEND_TO_TICKET_DESCRIPTION, SendToTicketParams, send_to_ticket, False),
        ("list_tickets", LIST_TICKETS_DESCRIPTION, ListTicketsParams, list_tickets, True),
        ("get_ticket", GET_TICKET_DESCRIPTION, GetTicketParams, get_ticket, True),
        ("cancel_ticket", CANCEL_TICKET_DESCRIPTION, CancelTicketParams, cancel_ticket, False),
    ):
        registry.register(
            ToolDefinition(
                name=name,
                group="tickets",
                risk=ToolRisk.SAFE,
                description=description,
                params_model=params_model,
                handler=handler,
                read_only=read_only,
                rest_visible=False,
            )
        )


def _require_orchestrator() -> RunContext:
    return require_run(kinds={"orchestrator"})


def _field(value: Any, name: str, default: Any = None) -> Any:
    """同时支持 ORM 行、dataclass 与映射（测试替身）。"""
    if isinstance(value, Mapping):
        return value.get(name, default)
    return getattr(value, name, default)


def _text(value: Any) -> str | None:
    return None if value is None else str(value)


def _iso(value: Any) -> str | None:
    if value is None:
        return None
    if isinstance(value, datetime):
        if value.tzinfo is None:
            value = value.replace(tzinfo=UTC)
        return value.astimezone(UTC).isoformat().replace("+00:00", "Z")
    return str(value)


def _worker_spec(kind: str, run: RunContext, ctx: ToolContext) -> Any:
    """建单前的形状检查：必须是已加载、非 legacy 的 ``worker`` 定义。"""
    registry = getattr(app_state(run, ctx), "agent_spec_registry", None)
    spec = None
    if registry is not None:
        try:
            spec = registry.get(kind)
        except (AppError, KeyError):
            spec = None
    if spec is None:
        spec = builtin_specs().get(kind)
    if spec is None or spec.kind != "worker" or spec.name.startswith("legacy_"):
        raise AppError(
            ErrorCode.INVALID_PARAMETER, f"没有叫 {kind} 的 Worker", {"kind": kind}
        )
    return spec


def _delegation_source(run: RunContext) -> tuple[str, str, str, str | None]:
    """按本轮领取到的项判断委托强度（规格 §4.5.11）。"""
    for item in reversed(list(run.inbox_items or ())):
        if str(_field(item, "kind")) != "user_message":
            continue
        payload = _field(item, "payload") or {}
        if not isinstance(payload, Mapping):
            payload = {}
        return (
            "explicit",
            "user",
            str(payload.get("source") or "web"),
            _text(payload.get("sender") or "web"),
        )
    return ("inferred", "system", "system", "orchestrator")


async def create_ticket(
    params: CreateTicketParams, ctx: ToolContext
) -> dict[str, Any]:
    """建一张工单，返回工单与 Worker 会话 id，并向总控流发 ``ticket_created``。"""
    run = _require_orchestrator()
    _worker_spec(params.kind, run, ctx)
    worker_service = v3_service(run, ctx, "worker_service", "Worker 服务")
    strength, origin, source, sender = _delegation_source(run)
    opened = await worker_service.open_ticket(
        kind=params.kind,
        title=params.title,
        brief=params.brief,
        scope=params.scope,
        origin=origin,
        strength=strength,
        source=source,
        sender=sender,
        orchestrator_session_id=run.session_id,
    )
    ticket_id = _text(_field(opened, "ticket_id"))
    title = _field(opened, "title")
    kind = _field(opened, "kind")
    status = _text(_field(opened, "status"))
    await run.emit(
        "ticket_created",
        {"ticket_id": ticket_id, "title": title, "kind": kind, "status": status},
    )
    return {
        "ticket_id": ticket_id,
        "title": title,
        "kind": kind,
        "status": status,
        "worker_session_id": _text(_field(opened, "worker_session_id")),
        "strength": strength,
    }


async def send_to_ticket(
    params: SendToTicketParams, ctx: ToolContext
) -> dict[str, Any]:
    """把总控的补充说明转给工单的 Worker。"""
    run = _require_orchestrator()
    worker_service = v3_service(run, ctx, "worker_service", "Worker 服务")
    inbox_id = await worker_service.send_from_orchestrator(
        params.ticket_id, params.content
    )
    return {"ticket_id": params.ticket_id, "inbox_id": inbox_id}


def _ticket_summary(ticket: Any) -> dict[str, Any]:
    """工单摘要：列表与详情共用同一形状（与 REST ``TicketSummary`` 对齐）。"""
    summary = _field(ticket, "summary")
    return {
        "id": _text(_field(ticket, "id")),
        "title": _field(ticket, "title"),
        "kind": _field(ticket, "kind"),
        "status": _text(_field(ticket, "status")),
        "origin": _text(_field(ticket, "origin")),
        "priority": _field(ticket, "priority"),
        "summary": summary,
        "last_report_status": _text(_field(ticket, "last_report_status")),
        "human_action": _field(ticket, "human_action"),
        "created_at": _iso(_field(ticket, "created_at")),
        "updated_at": _iso(_field(ticket, "updated_at")),
        "ended_at": _iso(_field(ticket, "ended_at")),
    }


def _ticket_item(ticket: Any) -> dict[str, Any]:
    """``list_tickets`` 的列表项：只保留模型需要的字段，摘要截到 200 字。"""
    summary = _field(ticket, "summary")
    return {
        "ticket_id": _text(_field(ticket, "id")),
        "title": _field(ticket, "title"),
        "kind": _field(ticket, "kind"),
        "status": _text(_field(ticket, "status")),
        "last_report_status": _text(_field(ticket, "last_report_status")),
        "summary": None if summary is None else str(summary)[:200],
        "human_action": _field(ticket, "human_action"),
        "updated_at": _iso(_field(ticket, "updated_at")),
    }


async def list_tickets(
    params: ListTicketsParams, ctx: ToolContext
) -> dict[str, Any]:
    """列出工单，返回模型需要的摘要字段。"""
    run = _require_orchestrator()
    ticket_service = v3_service(run, ctx, "ticket_service", "工单服务")
    page = await ticket_service.list(
        statuses=params.status, page=1, size=params.limit
    )
    return {
        "items": [_ticket_item(ticket) for ticket in page.items],
        "total": page.total,
    }


def _ticket_detail_payload(data: Any) -> dict[str, Any]:
    """工单详情：pydantic 模型直接 dump，M69 的 dataclass 按同一形状组装。"""
    dump = getattr(data, "model_dump", None)
    if callable(dump):
        return dump(mode="json")
    ticket = _field(data, "ticket")
    mandate = _field(data, "mandate")
    return {
        "ticket": _ticket_summary(ticket),
        "result": _field(ticket, "result"),
        "mandate": {
            "id": _text(_field(mandate, "id")),
            "scope": _field(mandate, "scope"),
            "strength": _text(_field(mandate, "strength")),
            "source": _text(_field(mandate, "source")),
            "sender": _field(mandate, "sender"),
            "rule_id": _field(mandate, "rule_id"),
            "risk_ceiling": _text(_field(mandate, "risk_ceiling")),
            "policy_version": _field(mandate, "policy_version"),
            "created_at": _iso(_field(mandate, "created_at")),
        },
        "subscriptions": [
            {
                "thread_key": _field(item, "thread_key"),
                "active": _field(item, "active"),
                "created_at": _iso(_field(item, "created_at")),
                "ended_at": _iso(_field(item, "ended_at")),
            }
            for item in (_field(data, "subscriptions") or ())
        ],
        "recent_reports": [
            {
                "id": _field(item, "id"),
                "ticket_id": _field(item, "ticket_id"),
                "run_id": _field(item, "run_id"),
                "status": _text(_field(item, "status")),
                "summary": _field(item, "summary"),
                "human_action": _field(item, "human_action"),
                "details": _field(item, "details"),
                "forwarded": _field(item, "forwarded"),
                "created_at": _iso(_field(item, "created_at")),
            }
            for item in (_field(data, "recent_reports") or ())
        ],
        "worker_session_id": _text(_field(ticket, "worker_session_id")),
        "turn_running": bool(_field(data, "turn_running", False)),
    }


async def get_ticket(params: GetTicketParams, ctx: ToolContext) -> dict[str, Any]:
    """读取工单详情（委托、最近汇报与是否在运行）。"""
    run = _require_orchestrator()
    ticket_service = v3_service(run, ctx, "ticket_service", "工单服务")
    return _ticket_detail_payload(await ticket_service.detail(params.ticket_id))


async def cancel_ticket(
    params: CancelTicketParams, ctx: ToolContext
) -> dict[str, Any]:
    """取消工单；只停 Worker，不停已经提交的流水线。"""
    run = _require_orchestrator()
    worker_service = v3_service(run, ctx, "worker_service", "Worker 服务")
    detail = await worker_service.cancel(
        params.ticket_id, reason=params.reason, actor="orchestrator"
    )
    ticket = _field(detail, "ticket", detail)
    return {
        "ticket_id": params.ticket_id,
        "status": _text(_field(ticket, "status")),
    }
