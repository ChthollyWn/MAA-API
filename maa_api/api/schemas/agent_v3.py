"""v3 Agent 运行查询的响应模型与转换函数（M66 规格 §4.3.1）。

``agent_run`` 的列名与对外字段几乎一一对应，只有两处刻意不同：主键对外叫
``run_id``（不叫 ``id``），``ticket_id`` 在工单功能上线前恒为空（表里还没有这一列）。
时间字段一律按 UTC 的 ``Z`` 后缀输出：库里存的是 naive UTC，Pydantic 默认会带
``+00:00`` 或干脆不带时区，前端与 OpenAPI 都按 ISO 8601 UTC 解析，这里统一在
序列化时补上时区。
"""

from __future__ import annotations

from datetime import UTC, datetime
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field, field_serializer

from maa_api.agent.runs import RunNode
from maa_api.db.models import AgentMessage, AgentRun
from maa_api.domain.enums import AgentRunKind, AgentRunStatus

__all__ = [
    "AgentMessageCreate",
    "AgentMessageItem",
    "AgentMessagePage",
    "Mention",
    "OrchestratorInterruptResult",
    "OrchestratorStateModel",
    "RunDetail",
    "RunPage",
    "RunSummary",
    "TicketCancelRequest",
    "TicketInterruptResult",
    "agent_message_item",
    "run_detail",
    "run_summary",
]


def _utc_z(value: datetime | None) -> str | None:
    """把库里的 naive UTC 时间渲染成 ``...Z``；``None`` 原样返回。"""
    if value is None:
        return None
    if value.tzinfo is None:
        value = value.replace(tzinfo=UTC)
    return value.astimezone(UTC).isoformat().replace("+00:00", "Z")


class RunSummary(BaseModel):
    """一次代理调用的摘要。"""

    run_id: str
    session_id: str | None
    ticket_id: str | None = Field(default=None, description="所属工单；工单功能上线前恒为空")
    parent_run_id: str | None
    root_run_id: str
    agent_name: str
    spec_version: str | None
    kind: AgentRunKind
    status: AgentRunStatus
    trigger_seq: int | None
    model: str | None
    model_profile: str | None
    prompt_tokens: int
    completion_tokens: int
    cached_tokens: int
    reasoning_tokens: int
    cost_micros: int
    currency: str | None
    steps: int
    finish_reason: str | None
    error_code: str | None
    heartbeat_at: datetime | None = Field(json_schema_extra={"format": "date-time"})
    started_at: datetime = Field(json_schema_extra={"format": "date-time"})
    finished_at: datetime | None = Field(json_schema_extra={"format": "date-time"})

    @field_serializer("heartbeat_at", "started_at", "finished_at")
    def _serialize_time(self, value: datetime | None) -> str | None:
        return _utc_z(value)


class RunDetail(RunSummary):
    """运行详情与子运行树（深度不超过 3）。"""

    error_message: str | None
    boot_id: str
    meta: dict[str, Any] | None
    children: list["RunDetail"] = Field(default_factory=list)


class RunPage(BaseModel):
    items: list[RunSummary]
    total: int
    page: int
    size: int


RunDetail.model_rebuild()


def run_summary(run: AgentRun) -> RunSummary:
    """逐字段搬运；``run_id`` 取 ``run.id``，``ticket_id`` 填 ``None``。"""
    return RunSummary(
        run_id=run.id,
        session_id=run.session_id,
        ticket_id=None,
        parent_run_id=run.parent_run_id,
        root_run_id=run.root_run_id,
        agent_name=run.agent_name,
        spec_version=run.spec_version,
        kind=AgentRunKind(run.kind),
        status=AgentRunStatus(run.status),
        trigger_seq=run.trigger_seq,
        model=run.model,
        model_profile=run.model_profile,
        prompt_tokens=run.prompt_tokens,
        completion_tokens=run.completion_tokens,
        cached_tokens=run.cached_tokens,
        reasoning_tokens=run.reasoning_tokens,
        cost_micros=run.cost_micros,
        currency=run.currency,
        steps=run.steps,
        finish_reason=run.finish_reason,
        error_code=run.error_code,
        heartbeat_at=run.heartbeat_at,
        started_at=run.started_at,
        finished_at=run.finished_at,
    )


def run_detail(node: RunNode) -> RunDetail:
    """``run_summary`` 的字段 + ``error_message``、``boot_id``、``meta``；``children`` 递归。"""
    run = node.run
    return RunDetail(
        **run_summary(run).model_dump(),
        error_message=run.error_message,
        boot_id=run.boot_id,
        meta=run.meta,
        children=[run_detail(child) for child in node.children],
    )


# ----------------------------------------------------------------------
# M70 总控 / 工单消息端点（规格 §4.3.1）
# ----------------------------------------------------------------------
class Mention(BaseModel):
    """一条 @ 引用；``type`` 决定前端去哪类对象里跳转。"""

    type: Literal["knowledge", "operator", "stage", "pipeline", "ticket"]
    id: str = Field(min_length=1, max_length=128)
    label: str = Field(min_length=1, max_length=64)


class AgentMessageCreate(BaseModel):
    """两条 SSE 端点共用的消息请求体。"""

    model_config = ConfigDict(extra="forbid")

    content: str = Field(min_length=1, max_length=32_000)
    mentions: list[Mention] | None = Field(default=None, max_length=20)


class TicketCancelRequest(BaseModel):
    """取消工单的请求体；不写原因时服务层用「用户取消」。"""

    model_config = ConfigDict(extra="forbid")

    reason: str | None = Field(default=None, max_length=200)


class OrchestratorStateModel(BaseModel):
    """总控会话的对外状态（``GET /api/agent/orchestrator``）。"""

    session_id: str
    status: str
    turn_running: bool
    pending_inbox: int
    archived_count: int


class OrchestratorInterruptResult(BaseModel):
    """中断总控当前一轮的结果；没有活动轮次时 ``interrupted=false``。"""

    session_id: str
    interrupted: bool
    turn_running: bool


class TicketInterruptResult(BaseModel):
    """中断工单当前一轮的结果；幂等。"""

    ticket_id: str
    interrupted: bool
    turn_running: bool


class AgentMessageItem(BaseModel):
    """v2 ``message_wire`` 的全部字段 + M70 的运行 / 收件箱归属。"""

    id: int
    session_id: str
    seq: int
    role: str
    content: str | None
    reasoning_content: str | None
    tool_calls: list[dict[str, Any]] | None
    tool_call_id: str | None
    tool_name: str | None
    audit_id: int | None
    prompt_tokens: int | None
    completion_tokens: int | None
    finish_reason: str | None
    latency_ms: int | None
    created_at: str
    run_id: str | None
    inbox_id: int | None


class AgentMessagePage(BaseModel):
    """消息分页信封；按 ``seq``（总控）或 ``id``（工单含子代理）升序。"""

    items: list[AgentMessageItem]
    total: int
    page: int
    size: int


def agent_message_item(message: AgentMessage) -> AgentMessageItem:
    """``agent_message`` 行 → 对外模型；时间统一成 ``...Z``。"""
    return AgentMessageItem(
        id=int(message.id or 0),
        session_id=message.session_id,
        seq=int(message.seq or 0),
        role=str(message.role),
        content=message.content,
        reasoning_content=message.reasoning_content,
        tool_calls=message.tool_calls,
        tool_call_id=message.tool_call_id,
        tool_name=message.tool_name,
        audit_id=message.audit_id,
        prompt_tokens=message.prompt_tokens,
        completion_tokens=message.completion_tokens,
        finish_reason=message.finish_reason,
        latency_ms=message.latency_ms,
        created_at=_utc_z(message.created_at) or "",
        run_id=message.run_id,
        inbox_id=message.inbox_id,
    )
