"""v3 Agent 运行查询的响应模型与转换函数（M66 规格 §4.3.1）。

``agent_run`` 的列名与对外字段几乎一一对应，只有两处刻意不同：主键对外叫
``run_id``（不叫 ``id``），``ticket_id`` 在工单功能上线前恒为空（表里还没有这一列）。
时间字段一律按 UTC 的 ``Z`` 后缀输出：库里存的是 naive UTC，Pydantic 默认会带
``+00:00`` 或干脆不带时区，前端与 OpenAPI 都按 ISO 8601 UTC 解析，这里统一在
序列化时补上时区。
"""

from __future__ import annotations

from collections.abc import Mapping
from datetime import UTC, datetime
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field, field_serializer

from maa_api.agent.runs import RunNode
from maa_api.db.models import AgentMessage, AgentRun, Artifact, MemoryItem, RunPlan
from maa_api.domain.enums import (
    AgentRunKind,
    AgentRunStatus,
    ArtifactKind,
    ArtifactStorage,
    InboxKind,
    MemoryKind,
    MemoryStatus,
    PlanItemStatus,
    PlanStatus,
)

__all__ = [
    "AgentMessageCreate",
    "AgentMessageItem",
    "AgentMessagePage",
    "ArtifactDetailOut",
    "ArtifactFormSubmit",
    "ArtifactFormSubmitResult",
    "ArtifactOut",
    "ArtifactPage",
    "ArtifactPinRequest",
    "ForkRequest",
    "MemoryOut",
    "MemoryPage",
    "MemoryReviewRequest",
    "MemoryUpdateRequest",
    "Mention",
    "NavEdgeOut",
    "NavEdgePut",
    "NavExemplarOut",
    "NavGraphOut",
    "NavNodeOut",
    "NavNodePut",
    "NavRecognizerModel",
    "OrchestratorInterruptResult",
    "OrchestratorStateModel",
    "PlanApprovalRequest",
    "PlanItemOut",
    "PlanOut",
    "RewindRequest",
    "RewindResultOut",
    "RewindSideEffectOut",
    "RunDetail",
    "RunPage",
    "RunSummary",
    "TicketCancelRequest",
    "TicketInterruptResult",
    "agent_message_item",
    "artifact_detail_out",
    "artifact_out",
    "memory_out",
    "plan_out",
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
    """v2 ``message_wire`` 的全部字段 + 运行 / 收件箱归属（M70、M72）。"""

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
    inbox_kind: InboxKind | None = None
    inbox_thread_key: str | None = None
    #: 被回溯取代的时刻（M80）；非空的消息不进上下文，前端默认折叠。
    superseded_at: str | None = None


class AgentMessagePage(BaseModel):
    """消息分页信封；按 ``seq``（总控）或 ``id``（工单含子代理）升序。"""

    items: list[AgentMessageItem]
    total: int
    page: int
    size: int


def agent_message_item(
    message: AgentMessage,
    inbox_meta: Mapping[int, tuple[str, str | None]] | None = None,
) -> AgentMessageItem:
    """``agent_message`` 行 → 对外模型；时间统一成 ``...Z``。

    ``inbox_meta`` 是调用方用 :func:`load_inbox_meta` 一次性批量取回的
    ``inbox_id → (kind, thread_key)``；没有收件箱归属时两个字段为 ``None``。
    """
    inbox = (
        inbox_meta.get(int(message.inbox_id))
        if inbox_meta is not None and message.inbox_id is not None
        else None
    )
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
        inbox_kind=InboxKind(inbox[0]) if inbox is not None else None,
        inbox_thread_key=inbox[1] if inbox is not None else None,
        superseded_at=_utc_z(message.superseded_at),
    )


# ----------------------------------------------------------------------
# M80 回溯与分叉（规格 §4.3）：RewindRequest / RewindResultOut / ForkRequest
# ----------------------------------------------------------------------
class RewindRequest(BaseModel):
    """回溯请求：保留到 ``to_seq``（含）。"""

    model_config = ConfigDict(extra="forbid")

    to_seq: int = Field(ge=-1, description="保留到这条消息（含）；-1 只用于总控会话")
    dry_run: bool = Field(default=False, description="只计算影响，不写库")


class RewindSideEffectOut(BaseModel):
    """回溯范围内已经发生、不会被撤销的一次工具调用。"""

    seq: int
    tool_name: str
    audit_id: int | None
    #: 「流水线 1f3e9a2c」「工单「日常」」；取不到为 ``null``。
    detail: str | None


class RewindResultOut(BaseModel):
    """一次回溯的结果（``dry_run`` 时只报告不写库）。"""

    session_id: str
    to_seq: int
    dry_run: bool
    superseded: int
    superseded_events: int
    checkpoints_invalidated: int
    checkpoint_reactivated: str | None
    plan_abandoned: bool
    side_effects: list[RewindSideEffectOut]
    notice_message_id: int | None


class ForkRequest(BaseModel):
    """分叉请求：在 ``at_seq`` 这条消息（含）处分叉出新工单。"""

    model_config = ConfigDict(extra="forbid")

    at_seq: int = Field(ge=0, description="分叉处的消息 seq（含）")


# ----------------------------------------------------------------------
# M75 产物端点（规格 §4.3）：ArtifactOut / ArtifactDetailOut / ArtifactPage
# ----------------------------------------------------------------------
class ArtifactOut(BaseModel):
    """产物元数据；列表不返回正文。"""

    id: str
    ticket_id: str | None
    session_id: str | None
    kind: ArtifactKind
    title: str
    mime: str
    size_bytes: int
    storage: ArtifactStorage
    pinned: bool
    created_at: str


class ArtifactDetailOut(ArtifactOut):
    """产物详情：正文最多 64 KiB，超出时 ``truncated=true``；``meta`` 原样返回整列。"""

    content: str
    truncated: bool
    meta: dict[str, Any]


class ArtifactPage(BaseModel):
    """产物分页信封，与 04 §1 的统一分页形状一致。"""

    items: list[ArtifactOut]
    total: int
    page: int
    size: int


class ArtifactPinRequest(BaseModel):
    """置顶或取消置顶（规格 §4.3）。"""

    model_config = ConfigDict(extra="forbid")

    pinned: bool


class ArtifactFormSubmit(BaseModel):
    """提交生成式表单的答案（M90 规格 §4.3.3）。"""

    model_config = ConfigDict(extra="forbid")

    values: dict[str, Any]


class ArtifactFormSubmitResult(BaseModel):
    """一次成功提交的受理结果（M90 规格 §4.3.3）。"""

    artifact_id: str
    ticket_id: str
    submitted_at: str


def artifact_out(artifact: Artifact) -> ArtifactOut:
    """``artifact`` 行 → 列表/置顶端点的对外模型。"""
    return ArtifactOut(
        id=str(artifact.id),
        ticket_id=artifact.ticket_id,
        session_id=artifact.session_id,
        kind=ArtifactKind(str(artifact.kind)),
        title=artifact.title,
        mime=artifact.mime,
        size_bytes=artifact.size_bytes,
        storage=ArtifactStorage(str(artifact.storage)),
        pinned=bool(artifact.pinned),
        created_at=_utc_z(artifact.created_at) or "",
    )


def artifact_detail_out(
    artifact: Artifact, *, content: str, truncated: bool
) -> ArtifactDetailOut:
    """详情端点：元数据 + 截断后的正文 + 整列 ``meta``。"""
    return ArtifactDetailOut(
        **artifact_out(artifact).model_dump(),
        content=content,
        truncated=truncated,
        meta=dict(artifact.meta or {}),
    )


# ----------------------------------------------------------------------
# M75 计划端点（规格 §4.3）：PlanOut 与批准请求
# ----------------------------------------------------------------------
class PlanItemOut(BaseModel):
    """计划里的一步；``id`` 是 8 位十六进制、同计划唯一。"""

    id: str
    title: str
    status: PlanItemStatus
    note: str | None = None


class PlanOut(BaseModel):
    """``GET .../plan`` 与批准端点的响应（spec §4.3）。"""

    id: str
    session_id: str
    ticket_id: str | None
    title: str
    items: list[PlanItemOut]
    status: PlanStatus
    approved_by: str | None
    approved_at: str | None


class PlanApprovalRequest(BaseModel):
    """批准或退回一份待批准计划；退回可带原因。"""

    model_config = ConfigDict(extra="forbid")

    approved: bool
    reason: str | None = Field(default=None, max_length=200)


# ----------------------------------------------------------------------
# M79 记忆端点（规格 §4.3、03 §10.2）：MemoryOut / MemoryPage / 两个请求体
# ----------------------------------------------------------------------
class MemoryOut(BaseModel):
    """一条长期记忆的全部列（03 §10.2）；时间统一渲染成 ``...Z``。

    没有 ``pinned`` 列，不要加（规格 §4.3）。
    """

    id: str
    kind: MemoryKind
    subject: str | None
    content: str
    status: MemoryStatus
    confidence: float | None
    source_ticket_id: str | None
    source_session_id: str | None
    source_run_id: str | None
    supersedes_id: str | None
    expires_at: datetime | None = Field(json_schema_extra={"format": "date-time"})
    last_used_at: datetime | None = Field(json_schema_extra={"format": "date-time"})
    use_count: int
    reviewed_by: str | None
    reviewed_at: datetime | None = Field(json_schema_extra={"format": "date-time"})
    created_at: datetime = Field(json_schema_extra={"format": "date-time"})
    updated_at: datetime = Field(json_schema_extra={"format": "date-time"})

    @field_serializer(
        "expires_at", "last_used_at", "reviewed_at", "created_at", "updated_at"
    )
    def _serialize_time(self, value: datetime | None) -> str | None:
        return _utc_z(value)


class MemoryPage(BaseModel):
    """记忆分页信封，与 04 §1 的统一分页形状一致。"""

    items: list[MemoryOut]
    total: int
    page: int
    size: int


class MemoryReviewRequest(BaseModel):
    """``POST .../review`` 的入参：批准可顺手改写正文，拒绝只改状态。"""

    model_config = ConfigDict(extra="forbid")

    approved: bool
    content: str | None = None


class MemoryUpdateRequest(BaseModel):
    """``PUT`` 的入参：三项都可选，但至少要改一项（服务层报参数错误）。

    ``subject`` 与 ``expires_at`` 显式传 ``null`` 表示清空；路由用
    ``model_fields_set`` 区分「没传」与「传了 null」。
    """

    model_config = ConfigDict(extra="forbid")

    content: str | None = None
    subject: str | None = None
    expires_at: datetime | None = None


def memory_out(memory: MemoryItem) -> MemoryOut:
    """``memory_item`` 行 → 对外模型；时间由字段序列化器补 ``Z``。"""
    return MemoryOut(
        id=str(memory.id),
        kind=MemoryKind(str(memory.kind)),
        subject=memory.subject,
        content=memory.content,
        status=MemoryStatus(str(memory.status)),
        confidence=memory.confidence,
        source_ticket_id=memory.source_ticket_id,
        source_session_id=memory.source_session_id,
        source_run_id=memory.source_run_id,
        supersedes_id=memory.supersedes_id,
        expires_at=memory.expires_at,
        last_used_at=memory.last_used_at,
        use_count=int(memory.use_count or 0),
        reviewed_by=memory.reviewed_by,
        reviewed_at=memory.reviewed_at,
        created_at=memory.created_at,
        updated_at=memory.updated_at,
    )


def plan_out(plan: RunPlan) -> PlanOut:
    """``run_plan`` 行 → 对外模型；步骤按库里的顺序输出。"""
    items: list[PlanItemOut] = []
    for raw in plan.items or ():
        if not isinstance(raw, Mapping):
            continue
        items.append(
            PlanItemOut(
                id=str(raw.get("id") or ""),
                title=str(raw.get("title") or ""),
                status=PlanItemStatus(str(raw.get("status") or "pending")),
                note=raw.get("note"),
            )
        )
    return PlanOut(
        id=str(plan.id),
        session_id=str(plan.session_id),
        ticket_id=plan.ticket_id,
        title=plan.title,
        items=items,
        status=PlanStatus(str(plan.status)),
        approved_by=plan.approved_by,
        approved_at=_utc_z(plan.approved_at),
    )


# ----------------------------------------------------------------------
# M86 界面导航图端点（规格 §4.3.2）：七个请求与响应模型
# ----------------------------------------------------------------------
class NavExemplarOut(BaseModel):
    """一个识别样本；``thumb_url`` 在截图已被清理或查不到时为 ``None``。"""

    phash: str
    screenshot_id: str | None
    source: str
    added_at: str | None
    thumb_url: str | None


class NavRecognizerModel(BaseModel):
    """节点的识别配置（§4.2）；请求里 ``extra="forbid"``。"""

    model_config = ConfigDict(extra="forbid")

    rois: list[list[float]] = Field(
        default_factory=lambda: [[0, 0, 1, 1]], min_length=1, max_length=4
    )
    max_distance: int = Field(default=10, ge=1, le=32)
    description: str = Field(default="", max_length=200)
    generic: bool = False


class NavNodeOut(BaseModel):
    """一个界面节点；时间统一渲染成 ``...Z``。"""

    id: str
    title: str
    kind: str
    origin: str
    recognizer: NavRecognizerModel
    exemplars: list[NavExemplarOut]
    created_at: str
    updated_at: str


class NavEdgeOut(BaseModel):
    """一条跳转；``action`` 是原始动作 JSON，``action_hash`` 是规范哈希。"""

    id: str
    from_node: str
    to_node: str
    action: dict[str, Any]
    action_hash: str
    cost_ms: int | None
    success_count: int
    fail_count: int
    last_used_at: str | None
    origin: str
    enabled: bool
    created_at: str
    updated_at: str


class NavGraphOut(BaseModel):
    """整张导航图与三个运行时开关（``vision_available`` 现解析视觉档案）。"""

    nodes: list[NavNodeOut]
    edges: list[NavEdgeOut]
    enabled: bool
    learn: bool
    vision_available: bool


class NavNodePut(BaseModel):
    """``PUT /api/agent/nav/nodes/{node_id}`` 的入参（§4.3.2）。"""

    model_config = ConfigDict(extra="forbid")

    title: str = Field(min_length=1, max_length=64)
    kind: Literal["screen", "stage", "menu", "dialog"]
    recognizer: NavRecognizerModel | None = None
    add_exemplar: Literal["current"] | None = None
    add_exemplar_screenshot_id: str | None = Field(default=None, max_length=36)
    remove_exemplars: list[str] = Field(default_factory=list, max_length=8)


class NavEdgePut(BaseModel):
    """``PUT /api/agent/nav/edges/{edge_id}`` 的入参（§4.3.2）。

    不存在时 ``from_node``/``to_node``/``action`` 必须给全；存在时只有
    ``enabled``/``action``/``cost_ms`` 生效，端点与现值不同是 400。
    """

    model_config = ConfigDict(extra="forbid")

    from_node: str | None = None
    to_node: str | None = None
    action: dict[str, Any] | None = None
    enabled: bool | None = None
    cost_ms: int | None = Field(default=None, ge=0, le=60000)
