"""确定性 durable 状态（v3 06 §4.4、M74 规格 §4.2.2）。

压缩会把较早的消息交给模型概括，摘要可能漏掉关键事实；这些事实系统本来就能
直接读出来，于是每次运行开始时收集成 :class:`DurableState`，压缩时原样写进检查点
并渲染进摘要消息，不经过模型。

渲染是纯函数：同一份状态得到同样的文本（ID 按固定长度截短）。空的部分整行不写。
"""

from __future__ import annotations

import logging
from collections.abc import Mapping, Sequence
from datetime import datetime
from typing import Any

from pydantic import BaseModel, Field
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from maa_api.agent.context.layout import (
    TAIL_SEGMENT_ORDER,
    ContextLayout,
    Segment,
    parts_of,
    put_segment,
)
from maa_api.agent.context.tokens import TokenEstimator
from maa_api.agent.harness.context import ModelRequest, RunContext
from maa_api.agent.harness.middleware import Middleware
from maa_api.agent.specs.schema import LEGACY_SPEC_NAMES
from maa_api.db.models import AgentQuestion, Confirmation, Mandate, Ticket
from maa_api.db.repositories.ticket import MandateRepository, TicketRepository
from maa_api.domain.enums import ConfirmationStatus, QuestionStatus, TicketStatus

__all__ = [
    "DurableContextMiddleware",
    "DurableState",
    "collect_durable_state",
    "render_durable",
    "render_open_tickets",
]

logger = logging.getLogger(__name__)

#: 进行中的工单、待处理事项各自的上限（v3 06 §4.4）。
DURABLE_ITEM_LIMIT = 20
#: 工单、确认、提问正文的截断长度。
DURABLE_TEXT_CHARS = 100
#: 渲染时 ID 截短后的字符数：工单类 4 字、产物类 8 字（06 §5.3 的示例口径）。
TICKET_ID_HEAD = 4
ARTIFACT_ID_HEAD = 8

_OPEN_TICKET_STATUSES = (
    TicketStatus.OPEN.value,
    TicketStatus.WAITING.value,
    TicketStatus.BLOCKED.value,
)


class DurableState(BaseModel):
    """压缩时不经过模型的确定性状态（v3 06 §4.4）。

    ``plan`` / ``notes`` / ``skills`` 是 M75 / M78 的预留字段，M74 只填 ``skills``
    （从 ``ctx.state["skills"]["loaded"]`` 取），其余保持空。
    """

    #: {id, title, status, last_report_status}；Worker 才有。
    ticket: dict[str, Any] | None = None
    #: {scope, strength, risk_ceiling}；Worker 才有。
    mandate: dict[str, Any] | None = None
    #: [{id, title, status, human_action}]，总控才有，≤ 20 条。
    open_tickets: list[dict[str, Any]] = Field(default_factory=list)
    #: M75 填写。
    plan: dict[str, Any] | None = None
    #: M75 填写。
    notes: list[dict[str, Any]] = Field(default_factory=list)
    #: M78 填写：已加载技能的 id。
    skills: list[str] = Field(default_factory=list)
    #: [{kind: confirmation|question, id, tool?, risk?, text}]，≤ 20 条。
    pending: list[dict[str, Any]] = Field(default_factory=list)
    #: 收集时间：本地时间 ISO 8601，带时区偏移。
    captured_at: str


def _short_id(value: Any, head: int) -> str:
    """ID 截短成「前 ``head`` 字 + …」；短 id 原样返回，空值返回空串。"""
    text = "" if value is None else str(value)
    if len(text) <= head:
        return text
    return f"{text[:head]}…"


def _short_artifact_id(value: Any) -> str:
    """产物 id 的截短：UUID 保留到第一个连字符（``6f1c2a9e-…``）。"""
    text = "" if value is None else str(value)
    head = ARTIFACT_ID_HEAD
    if len(text) > head and text[head : head + 1] == "-":
        head += 1
    return _short_id(text, head)


def _human_prompt(human_action: Any) -> str | None:
    """``human_action`` 里给用户看的问句；没有或形状不对时返回 ``None``。"""
    if not isinstance(human_action, Mapping):
        return None
    prompt = human_action.get("prompt")
    if isinstance(prompt, str) and prompt:
        return prompt
    return None


def render_open_tickets(items: Sequence[Mapping[str, Any]]) -> str:
    """「进行中的工单」块（总控尾部 ``tickets`` 段的正文）；没有时返回 ``""``。"""
    if not items:
        return ""
    lines = ["进行中的工单："]
    for item in items:
        line = (
            f"- 「{item.get('title', '')}」{item.get('status', '')}"
            f"（{_short_id(item.get('id'), TICKET_ID_HEAD)}）"
        )
        prompt = _human_prompt(item.get("human_action"))
        if prompt:
            line += f"，需要你：{prompt}"
        lines.append(line)
    return "\n".join(lines)


def _render_pending(item: Mapping[str, Any]) -> str:
    """一条待处理事项；确认带工具与风险，提问只有问句。"""
    short_id = _short_id(item.get("id"), TICKET_ID_HEAD)
    text = str(item.get("text") or "")
    if item.get("kind") == "confirmation":
        risk = item.get("risk")
        head = f"- 确认 {item.get('tool', '')}（{short_id}"
        if risk:
            head += f"，{risk}"
        return f"{head}）：{text}"
    return f"- 提问（{short_id}）：{text}"


def render_durable(state: DurableState) -> str:
    """把一份 durable 状态渲染成摘要里的「确定性状态」（空的部分整行不写）。"""
    lines: list[str] = []
    if state.ticket:
        lines.append(
            f"工单：「{state.ticket.get('title', '')}」"
            f"（{_short_id(state.ticket.get('id'), TICKET_ID_HEAD)}，"
            f"{state.ticket.get('status', '')}）"
        )
    if state.mandate:
        lines.append(
            f"委托：{state.mandate.get('scope', '')}"
            f"（{state.mandate.get('strength', '')}，"
            f"风险上限 {state.mandate.get('risk_ceiling', '')}）"
        )
    open_block = render_open_tickets(state.open_tickets)
    if open_block:
        lines.append(open_block)
    if state.pending:
        lines.append("待处理：")
        lines.extend(_render_pending(item) for item in state.pending)
    return "\n".join(lines)


async def _open_tickets(session: AsyncSession) -> list[dict[str, Any]]:
    """进行中的工单：open / waiting / blocked，priority 升序、updated_at 降序，20 条。"""
    result = await session.execute(
        select(Ticket)
        .where(Ticket.status.in_(list(_OPEN_TICKET_STATUSES)))
        .order_by(
            Ticket.priority.asc(),
            Ticket.updated_at.desc(),
            Ticket.id.desc(),
        )
        .limit(DURABLE_ITEM_LIMIT)
    )
    return [
        {
            "id": ticket.id,
            "title": ticket.title,
            "status": str(ticket.status),
            "human_action": ticket.human_action,
        }
        for ticket in result.scalars().all()
    ]


async def _pending_confirmations(
    session: AsyncSession, *, ticket_id: str | None
) -> list[tuple[datetime, dict[str, Any]]]:
    """待确认；``ticket_id`` 非空时只取该工单发起的（Worker 口径）。"""
    stmt = select(Confirmation).where(
        Confirmation.status == ConfirmationStatus.PENDING
    )
    if ticket_id is not None:
        stmt = stmt.where(Confirmation.origin_ticket_id == ticket_id)
    stmt = stmt.order_by(Confirmation.created_at.asc(), Confirmation.id.asc()).limit(
        DURABLE_ITEM_LIMIT
    )
    result = await session.execute(stmt)
    return [
        (
            row.created_at,
            {
                "kind": "confirmation",
                "id": row.id,
                "tool": row.action,
                "risk": str(row.risk_level),
                "text": (row.reason or "")[:DURABLE_TEXT_CHARS],
            },
        )
        for row in result.scalars().all()
    ]


async def _pending_questions(
    session: AsyncSession, *, session_id: str | None
) -> list[tuple[datetime, dict[str, Any]]]:
    """待回答提问；``session_id`` 非空时只取该会话的（Worker 口径）。"""
    stmt = select(AgentQuestion).where(
        AgentQuestion.status == QuestionStatus.PENDING
    )
    if session_id is not None:
        stmt = stmt.where(AgentQuestion.session_id == session_id)
    stmt = stmt.order_by(AgentQuestion.created_at.asc(), AgentQuestion.id.asc()).limit(
        DURABLE_ITEM_LIMIT
    )
    result = await session.execute(stmt)
    return [
        (
            row.created_at,
            {
                "kind": "question",
                "id": row.id,
                "text": (row.question or "")[:DURABLE_TEXT_CHARS],
            },
        )
        for row in result.scalars().all()
    ]


def _merge_pending(
    confirmations: Sequence[tuple[datetime, dict[str, Any]]],
    questions: Sequence[tuple[datetime, dict[str, Any]]],
) -> list[dict[str, Any]]:
    """确认与提问按 created_at 升序合并（同刻按 kind、id 兜稳定次序），≤ 20 条。"""
    merged = sorted(
        [*confirmations, *questions],
        key=lambda pair: (pair[0], str(pair[1].get("kind")), str(pair[1].get("id"))),
    )
    return [item for _, item in merged[:DURABLE_ITEM_LIMIT]]


def _now() -> str:
    """收集时间：本地时间 ISO 8601 带时区偏移。"""
    return datetime.now().astimezone().isoformat()


async def collect_durable_state(
    app_state: Any,
    *,
    session_id: str,
    session_kind: str,
    ticket_id: str | None,
) -> DurableState:
    """从库里收集这一轮的确定性状态（v3 06 §4.4）。

    Worker 取本工单与委托、本工单的待确认、本会话的待回答提问；总控取进行中的
    工单与全部待确认、提问。读库失败只记日志并留下空状态：durable 是压缩时的
    补充信息，不能让一轮运行因此失败。
    """
    state = DurableState(captured_at=_now())
    session_factory = getattr(app_state, "session_factory", None)
    if session_factory is None:
        logger.debug("没有 session_factory，durable 状态只带收集时间")
        return state
    try:
        async with session_factory() as session:
            if session_kind == "worker":
                if ticket_id:
                    ticket = await TicketRepository(session).get(ticket_id)
                    if ticket is not None:
                        state.ticket = {
                            "id": ticket.id,
                            "title": ticket.title,
                            "status": str(ticket.status),
                            "last_report_status": ticket.last_report_status,
                        }
                    mandate = await MandateRepository(session).get_by_ticket(ticket_id)
                    if mandate is not None:
                        state.mandate = {
                            "scope": mandate.scope,
                            "strength": mandate.strength,
                            "risk_ceiling": mandate.risk_ceiling,
                        }
                confirmations = (
                    await _pending_confirmations(session, ticket_id=ticket_id)
                    if ticket_id
                    else []
                )
                questions = await _pending_questions(
                    session, session_id=session_id
                )
            else:
                state.open_tickets = await _open_tickets(session)
                confirmations = await _pending_confirmations(
                    session, ticket_id=None
                )
                questions = await _pending_questions(session, session_id=None)
            state.pending = _merge_pending(confirmations, questions)
    except Exception:  # noqa: BLE001 - durable 是补充信息，读失败不能让运行失败
        logger.warning("收集 durable 状态失败，本轮不带确定性状态", exc_info=True)
    return state


def _layout_service(ctx: RunContext) -> ContextLayout:
    """优先用进程内的 ``app.state.context_layout``，测试里退回本轮本地实例。"""
    service = getattr(getattr(ctx.app, "state", None), "context_layout", None)
    if isinstance(service, ContextLayout):
        return service
    local = ctx.state.get("durable.context_layout")
    if isinstance(local, ContextLayout):
        return local
    local = ContextLayout(TokenEstimator())
    ctx.state["durable.context_layout"] = local
    return local


class DurableContextMiddleware(Middleware):
    """每轮开始收集 durable 状态；总控的动态尾部带「进行中的工单」段（M74 §4.2.2）。

    legacy 定义直接返回：v2 的布局与请求字节不变。``before_model`` 只给总控加段，
    Worker 的 durable 状态只供压缩使用。
    """

    name = "durable_context"
    applies_to = frozenset({"orchestrator", "worker"})

    async def on_run_start(self, ctx: RunContext) -> None:
        if ctx.spec.name in LEGACY_SPEC_NAMES:
            return
        state = await collect_durable_state(
            getattr(ctx.app, "state", None),
            session_id=ctx.session_id or "",
            session_kind=ctx.spec.kind,
            ticket_id=ctx.ticket_id,
        )
        # M78 会把已加载技能写进 ctx.state["skills"]；这里只做转发，不改技能逻辑。
        skills = ctx.state.get("skills")
        if isinstance(skills, Mapping):
            loaded = skills.get("loaded")
            if isinstance(loaded, (list, tuple)):
                state.skills = [str(item) for item in loaded]
        ctx.state["durable"] = state

    async def before_model(self, ctx: RunContext, req: ModelRequest) -> ModelRequest:
        if ctx.spec.name in LEGACY_SPEC_NAMES:
            return req
        if ctx.spec.kind != "orchestrator":
            return req
        durable = ctx.state.get("durable")
        if not isinstance(durable, DurableState):
            return req
        text = render_open_tickets(durable.open_tickets)
        if not text:
            return req
        parts = req.meta.get("layout_parts")
        if parts is None:
            return req
        profile = req.profile
        if profile is None:
            model_state = ctx.state.get("model")
            if isinstance(model_state, Mapping):
                profile = model_state.get("profile")
        if profile is None:
            return req
        put_segment(parts.tail, Segment("tickets", text), TAIL_SEGMENT_ORDER)
        return _layout_service(ctx).apply(ctx, req, parts, profile=profile)
