"""v3 Agent 运行查询的响应模型与转换函数（M66 规格 §4.3.1）。

``agent_run`` 的列名与对外字段几乎一一对应，只有两处刻意不同：主键对外叫
``run_id``（不叫 ``id``），``ticket_id`` 在工单功能上线前恒为空（表里还没有这一列）。
时间字段一律按 UTC 的 ``Z`` 后缀输出：库里存的是 naive UTC，Pydantic 默认会带
``+00:00`` 或干脆不带时区，前端与 OpenAPI 都按 ISO 8601 UTC 解析，这里统一在
序列化时补上时区。
"""

from __future__ import annotations

from datetime import UTC, datetime
from typing import Any

from pydantic import BaseModel, Field, field_serializer

from maa_api.agent.runs import RunNode
from maa_api.db.models import AgentRun
from maa_api.domain.enums import AgentRunKind, AgentRunStatus

__all__ = ["RunDetail", "RunPage", "RunSummary", "run_detail", "run_summary"]


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
