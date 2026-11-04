"""会话回溯与工单分叉（M80 规格 §4.2、§4.5.7、§4.5.8）。

回溯把一个会话的历史**截回到**某条消息：``seq > to_seq`` 且还没被取代的消息
打上 ``superseded_at``，它们从此不进上下文（``AgentMessageRepository`` 默认
排除）；之后的压缩检查点作废（``invalidated``），没有 active 时把更早的一条改回
``active``；目标之后建的计划改 ``abandoned``。副作用不回滚，但逐条列出来写进一条
``system`` 提示，避免模型重复执行。

并发纪律（§4.5.7 第 1 步）：写事务的**第一条语句**是那条 ``UPDATE agent_message``。
SQLite 一个连接拿到写锁之后别的连接不能再提交，随后再查一次活动运行看到的就是
此刻的全部已提交运行；之后才开始的运行会读到回溯后的历史。

分叉按「工单 + 委托 + 到 ``at_seq`` 为止的历史 + 最后一个有效检查点」在一个事务
里建新工单。委托逐字段照抄（推断委托仍是 ``inferred``、风险上限仍是 ``none``），
不写 Worker 收件箱（历史已经整段复制过来），也不复制话题订阅、设备租约、计划与
产物。这里直接用仓储写行而不是 ``TicketService.create``：建单入口会按新工单的
来源重算委托强度与风险上限，推断委托会被翻成显式委托（§4.5.8 明确要求保持原样）。
"""

from __future__ import annotations

import inspect
import json
import logging
from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass
from datetime import datetime
from typing import Any

from sqlalchemy import func, select, update
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from maa_api.agent.registry import ToolRegistry
from maa_api.agent.tickets.service import ticket_status_payload
from maa_api.db.models import (
    AgentInbox,
    AgentMessage,
    AgentQuestion,
    AgentSession,
    CompactionCheckpoint,
    Confirmation,
    Mandate,
    RunPlan,
    Ticket,
    new_uuid,
    utcnow,
)
from maa_api.db.repositories.agent import (
    AgentMessageRepository,
    AgentRunRepository,
    AgentSessionRepository,
)
from maa_api.db.repositories.compaction import CompactionCheckpointRepository
from maa_api.db.repositories.ticket import MandateRepository, TicketRepository
from maa_api.domain.enums import (
    AgentSessionKind,
    AgentSessionStatus,
    CheckpointStatus,
    ConfirmationStatus,
    InboxKind,
    PlanStatus,
    QuestionStatus,
    TicketOrigin,
    TicketStatus,
)
from maa_api.domain.errors import AppError, ErrorCode

__all__ = [
    "FORK_TITLE_SUFFIX",
    "REWIND_IGNORED_TOOLS",
    "RewindResult",
    "RewindService",
    "SideEffect",
]

logger = logging.getLogger(__name__)

#: 回溯时不算副作用的写工具（§4.2）：只影响本轮自身、不碰设备、不对外。
REWIND_IGNORED_TOOLS = frozenset(
    {
        "ask_user",
        "check_confirmation",
        "list_tool_groups",
        "enable_tool_group",
        "load_skill",
        "read_skill_file",
    }
)
#: 能回溯的会话种类（v2 会话、子代理与后台运行不支持）。
REWINDABLE_KINDS = frozenset(
    {AgentSessionKind.ORCHESTRATOR, AgentSessionKind.WORKER}
)
#: 工单已结束的状态：Worker 会话不能再回溯，前端改用分叉。
ENDED_TICKET_STATUSES = frozenset(
    {TicketStatus.DONE, TicketStatus.FAILED, TicketStatus.CANCELLED}
)
#: 未结束的计划状态（同 ``PlanService.ACTIVE_PLAN_STATUSES``）。
LIVE_PLAN_STATUSES = frozenset(
    {
        PlanStatus.DRAFT.value,
        PlanStatus.AWAITING_APPROVAL.value,
        PlanStatus.ACTIVE.value,
    }
)
#: 分叉工单的标题后缀。
FORK_TITLE_SUFFIX = "（分叉）"
#: 工单标题列宽。
_MAX_TITLE_CHARS = 64
#: 副作用 ``detail`` 里 id 的截断长度。
_SHORT_ID_CHARS = 8

#: 目标不合法的原因 → 给前端看的中文说明（``details.reason`` 是机器可读值）。
_TARGET_REASONS: Mapping[str, str] = {
    "session_kind": "只有总控会话与工单会话可以回溯",
    "archived": "会话已归档，不能再回溯",
    "not_found": "目标消息不存在",
    "superseded": "目标消息已经被回溯取代",
    "splits_tool_batch": "不能停在工具调用批次中间",
    "session_start": "工单会话要从一条具体的消息开始回溯",
    "no_worker_session": "这个工单还没有 Worker 会话",
}


@dataclass(frozen=True, slots=True)
class SideEffect:
    """回溯范围内已经发生、不会被撤销的一次工具调用（§4.2）。"""

    seq: int
    tool_name: str
    audit_id: int | None
    #: 「流水线 1f3e9a2c」「工单「日常」」；取不到为 ``None``。
    detail: str | None


@dataclass(frozen=True, slots=True)
class RewindResult:
    """一次回溯的结果；字段与接口的 ``RewindResultOut`` 一一对应（§4.3）。"""

    session_id: str
    to_seq: int
    dry_run: bool
    superseded: int
    superseded_events: int
    checkpoints_invalidated: int
    checkpoint_reactivated: str | None
    plan_abandoned: bool
    side_effects: tuple[SideEffect, ...]
    notice_message_id: int | None


@dataclass(frozen=True, slots=True)
class _RewindPlan:
    """写入之前的计算结果：只读事务里算好，写事务只落库。"""

    session_id: str
    to_seq: int
    superseded: int
    side_effects: tuple[SideEffect, ...]
    superseded_events: int
    checkpoints_invalidated: tuple[str, ...]
    checkpoint_reactivated: str | None
    plan_ids: tuple[str, ...]


def _target_invalid(reason: str, **details: Any) -> AppError:
    """``REWIND_TARGET_INVALID``：``details.reason`` 是 §4.5.7 目标表里的行。"""
    return AppError(
        ErrorCode.REWIND_TARGET_INVALID,
        _TARGET_REASONS.get(reason, "回溯目标不合法"),
        {"reason": reason, **details},
    )


def _session_not_found(session_id: str) -> AppError:
    return AppError(
        ErrorCode.AGENT_SESSION_NOT_FOUND,
        "没有这个会话",
        {"session_id": session_id},
    )


def _run_active(session_id: str, **details: Any) -> AppError:
    return AppError(
        ErrorCode.RUN_ALREADY_ACTIVE,
        "会话里还有运行或待处理的事项，等它结束再回溯",
        {"session_id": session_id, **details},
    )


def _ended_ticket(ticket_id: str) -> AppError:
    return AppError(
        ErrorCode.TICKET_ENDED,
        "工单已结束，改用分叉",
        {"ticket_id": ticket_id},
    )


def _json_body(content: str | None) -> dict[str, Any] | None:
    """解析 tool 消息正文；不是 JSON 对象时返回 ``None``。"""
    if not content:
        return None
    try:
        parsed = json.loads(content)
    except (TypeError, ValueError):
        return None
    return parsed if isinstance(parsed, dict) else None


def _short(value: Any) -> str:
    return str(value)[:_SHORT_ID_CHARS]


def _tool_call_ids(tool_calls: Any) -> set[str]:
    if not isinstance(tool_calls, Sequence) or isinstance(tool_calls, (str, bytes)):
        return set()
    return {
        str(call["id"])
        for call in tool_calls
        if isinstance(call, Mapping) and call.get("id") is not None
    }


class RewindService:
    """会话回溯与工单分叉的唯一入口（规格 §4.5.7、§4.5.8）。"""

    def __init__(
        self,
        session_factory: async_sessionmaker[AsyncSession],
        *,
        registry: ToolRegistry,
        ticket_service: Any,
        clock: Callable[[], datetime] = utcnow,
        broadcast: Callable[[str, dict[str, Any]], Any] | None = None,
    ) -> None:
        self._session_factory = session_factory
        self._registry = registry
        self._ticket_service = ticket_service
        self._clock = clock
        self._broadcast = broadcast

    # ------------------------------------------------------------------
    # 回溯
    # ------------------------------------------------------------------
    async def rewind(
        self, session_id: str, to_seq: int, *, dry_run: bool = False
    ) -> RewindResult:
        """把会话截回到 ``to_seq``（含）；``dry_run`` 只算影响不写库。"""
        plan = await self._prepare_rewind(session_id, to_seq)
        if plan.superseded == 0 or dry_run:
            # 目标就是最后一条消息：与空操作完全一致，不写库也不广播。
            return self._result(plan, dry_run=dry_run, notice_message_id=None)
        return await self._apply_rewind(plan)

    @staticmethod
    def _result(
        plan: _RewindPlan,
        *,
        dry_run: bool,
        notice_message_id: int | None,
        superseded: int | None = None,
    ) -> RewindResult:
        return RewindResult(
            session_id=plan.session_id,
            to_seq=plan.to_seq,
            dry_run=dry_run,
            superseded=plan.superseded if superseded is None else superseded,
            superseded_events=plan.superseded_events,
            checkpoints_invalidated=len(plan.checkpoints_invalidated),
            checkpoint_reactivated=plan.checkpoint_reactivated,
            plan_abandoned=bool(plan.plan_ids),
            side_effects=plan.side_effects,
            notice_message_id=notice_message_id,
        )

    async def _prepare_rewind(self, session_id: str, to_seq: int) -> _RewindPlan:
        """只读地做完 §4.5.7 的全部校验与计算；写库留给写事务。"""
        async with self._session_factory() as session:
            stored = await AgentSessionRepository(session).get(session_id)
            if stored is None:
                raise _session_not_found(session_id)
            kind = str(stored.kind)
            if kind not in REWINDABLE_KINDS:
                raise _target_invalid("session_kind", session_id=session_id)
            if stored.archived_at is not None:
                raise _target_invalid("archived", session_id=session_id)
            if kind == AgentSessionKind.WORKER and stored.ticket_id:
                ticket = await TicketRepository(session).get(stored.ticket_id)
                if (
                    ticket is not None
                    and TicketStatus(ticket.status) in ENDED_TICKET_STATUSES
                ):
                    raise _ended_ticket(str(stored.ticket_id))
            active = await AgentRunRepository(session).get_active_for_session(
                session_id
            )
            if active is not None:
                raise _run_active(session_id, run_id=str(active.id))
            pending = await self._pending(session, session_id)
            if pending:
                raise _run_active(session_id, pending=pending)

            messages = await AgentMessageRepository(session).list_by_session(
                session_id, include_superseded=True
            )
            target = self._validate_target(messages, to_seq, kind)
            affected = [
                message
                for message in messages
                if message.seq > to_seq and message.superseded_at is None
            ]
            if not affected:
                return _RewindPlan(
                    session_id=session_id,
                    to_seq=to_seq,
                    superseded=0,
                    side_effects=(),
                    superseded_events=0,
                    checkpoints_invalidated=(),
                    checkpoint_reactivated=None,
                    plan_ids=(),
                )

            checkpoints = await CompactionCheckpointRepository(
                session
            ).list_for_session(session_id)
            invalidated = _invalidate_ids(checkpoints, to_seq)
            return _RewindPlan(
                session_id=session_id,
                to_seq=to_seq,
                superseded=len(affected),
                side_effects=await self._side_effects(session, affected),
                superseded_events=await self._superseded_events(session, affected),
                checkpoints_invalidated=invalidated,
                checkpoint_reactivated=(
                    _reactivate_id(checkpoints, to_seq) if invalidated else None
                ),
                plan_ids=await self._plan_ids(session, session_id, target),
            )

    async def _pending(
        self, session: AsyncSession, session_id: str
    ) -> dict[str, int] | None:
        """本会话待处理的确认与提问；有就返回计数（§4.3 的 ``details.pending``）。"""
        confirmations = (
            await session.execute(
                select(Confirmation.payload).where(
                    Confirmation.status == ConfirmationStatus.PENDING
                )
            )
        ).scalars()
        confirmation_count = sum(
            1
            for payload in confirmations
            if isinstance(payload, Mapping) and payload.get("session_id") == session_id
        )
        question_count = int(
            await session.scalar(
                select(func.count())
                .select_from(AgentQuestion)
                .where(
                    AgentQuestion.session_id == session_id,
                    AgentQuestion.status == QuestionStatus.PENDING,
                )
            )
            or 0
        )
        if confirmation_count == 0 and question_count == 0:
            return None
        return {"confirmations": confirmation_count, "questions": question_count}

    def _validate_target(
        self, messages: Sequence[AgentMessage], to_seq: int, kind: str
    ) -> AgentMessage | None:
        """按 §4.5.7 的目标表校验；不合法抛 ``REWIND_TARGET_INVALID``。"""
        if to_seq == -1:
            if kind == AgentSessionKind.ORCHESTRATOR:
                return None
            raise _target_invalid("session_start")
        target = next((row for row in messages if row.seq == to_seq), None)
        if target is None:
            raise _target_invalid("not_found", to_seq=to_seq)
        if target.superseded_at is not None:
            raise _target_invalid("superseded", to_seq=to_seq)
        role = str(target.role)
        if role in ("user", "system"):
            return target
        if role == "assistant" and not target.tool_calls:
            return target
        if role == "tool" and _tool_batch_complete(messages, target):
            return target
        raise _target_invalid("splits_tool_batch", to_seq=to_seq)

    async def _side_effects(
        self, session: AsyncSession, affected: Sequence[AgentMessage]
    ) -> tuple[SideEffect, ...]:
        """受影响消息里的写工具调用（§4.5.7「副作用 detail 取法」）。"""
        candidates: list[tuple[AgentMessage, Mapping[str, Any]]] = []
        ticket_ids: set[str] = set()
        for message in affected:
            if str(message.role) != "tool":
                continue
            name = message.tool_name
            if not name or name in REWIND_IGNORED_TOOLS:
                continue
            try:
                definition = self._registry.get(name)
            except AppError:
                continue
            if definition.read_only:
                continue
            body = _json_body(message.content)
            if body is None or "result" not in body:
                continue
            guard = body.get("guard")
            if isinstance(guard, Mapping) and guard.get("action") == "blocked":
                continue
            result = body["result"]
            if isinstance(result, Mapping) and result.get("ticket_id"):
                ticket_ids.add(str(result["ticket_id"]))
            candidates.append((message, result if isinstance(result, Mapping) else {}))

        titles = await TicketRepository(session).titles(ticket_ids)
        return tuple(
            SideEffect(
                seq=int(message.seq),
                tool_name=str(message.tool_name),
                audit_id=message.audit_id,
                detail=_effect_detail(result, titles),
            )
            for message, result in candidates
        )

    async def _superseded_events(
        self, session: AsyncSession, affected: Sequence[AgentMessage]
    ) -> int:
        """作废部分里不会再送达的事件数（收件箱项不是用户消息）。"""
        inbox_ids = {
            int(message.inbox_id)
            for message in affected
            if message.inbox_id is not None
        }
        if not inbox_ids:
            return 0
        rows = await session.execute(
            select(AgentInbox.id, AgentInbox.kind).where(AgentInbox.id.in_(inbox_ids))
        )
        kinds = {int(item_id): str(kind) for item_id, kind in rows.all()}
        return sum(
            1
            for message in affected
            if message.inbox_id is not None
            and kinds.get(int(message.inbox_id)) != InboxKind.USER_MESSAGE.value
        )

    async def _plan_ids(
        self,
        session: AsyncSession,
        session_id: str,
        target: AgentMessage | None,
    ) -> tuple[str, ...]:
        """目标之后建的计划（目标为空时全部未结束计划）。"""
        rows = (
            await session.execute(
                select(RunPlan).where(
                    RunPlan.session_id == session_id,
                    RunPlan.status.in_(LIVE_PLAN_STATUSES),
                )
            )
        ).scalars()
        cutoff = target.created_at if target is not None else None
        return tuple(
            str(plan.id)
            for plan in rows
            if cutoff is None or plan.created_at > cutoff
        )

    async def _apply_rewind(self, plan: _RewindPlan) -> RewindResult:
        """§4.5.7 的写事务：第一条语句必须是那条 ``UPDATE agent_message``。"""
        session_id = plan.session_id
        now = self._clock()
        async with self._session_factory() as session:
            result = await session.execute(
                update(AgentMessage)
                .where(
                    AgentMessage.session_id == session_id,
                    AgentMessage.seq > plan.to_seq,
                    AgentMessage.superseded_at.is_(None),
                )
                .values(superseded_at=now),
                execution_options={"synchronize_session": False},
            )
            superseded = max(int(result.rowcount or 0), 0)
            if superseded == 0:
                # 另一半并发回溯先到：本事务什么都没改，回滚后按空操作返回。
                await session.rollback()
                return self._result(plan, dry_run=False, notice_message_id=None, superseded=0)
            active = await AgentRunRepository(session).get_active_for_session(
                session_id
            )
            if active is not None:
                await session.rollback()
                raise _run_active(session_id, run_id=str(active.id))

            if plan.checkpoints_invalidated:
                await session.execute(
                    update(CompactionCheckpoint)
                    .where(
                        CompactionCheckpoint.id.in_(
                            list(plan.checkpoints_invalidated)
                        )
                    )
                    .values(status=CheckpointStatus.INVALIDATED),
                    execution_options={"synchronize_session": False},
                )
            if plan.checkpoint_reactivated is not None:
                await session.execute(
                    update(CompactionCheckpoint)
                    .where(
                        CompactionCheckpoint.id == plan.checkpoint_reactivated
                    )
                    .values(status=CheckpointStatus.ACTIVE),
                    execution_options={"synchronize_session": False},
                )
            if plan.plan_ids:
                await session.execute(
                    update(RunPlan)
                    .where(RunPlan.id.in_(list(plan.plan_ids)))
                    .values(status=PlanStatus.ABANDONED, updated_at=now),
                    execution_options={"synchronize_session": False},
                )

            notice_message_id: int | None = None
            if plan.side_effects or plan.superseded_events:
                messages = AgentMessageRepository(session)
                stored = await messages.create(
                    AgentMessage(
                        session_id=session_id,
                        seq=await messages.next_seq(session_id),
                        role="system",
                        content=_notice_text(
                            superseded, plan.side_effects, plan.superseded_events
                        ),
                        created_at=now,
                    )
                )
                notice_message_id = int(stored.id) if stored.id else None

            await session.commit()

        await self._broadcast_rewind(
            session_id, plan.to_seq, superseded, notice_message_id
        )
        return self._result(
            plan,
            dry_run=False,
            notice_message_id=notice_message_id,
            superseded=superseded,
        )

    async def _broadcast_rewind(
        self,
        session_id: str,
        to_seq: int,
        superseded: int,
        notice_message_id: int | None,
    ) -> None:
        """广播 ``agent_event`` / ``session_rewound``；失败只记日志。"""
        if self._broadcast is None:
            return
        payload = {
            "session_id": session_id,
            "event": "session_rewound",
            "payload": {
                "session_id": session_id,
                "to_seq": to_seq,
                "superseded": superseded,
                "notice_message_id": notice_message_id,
            },
        }
        try:
            result = self._broadcast("agent_event", payload)
            if inspect.isawaitable(result):
                await result
        except Exception:  # noqa: BLE001 - 已提交，广播失败不能回滚
            logger.warning(
                "会话回溯事件广播失败：session=%s", session_id, exc_info=True
            )

    # ------------------------------------------------------------------
    # 分叉
    # ------------------------------------------------------------------
    async def fork_ticket(self, ticket_id: str, at_seq: int) -> str:
        """从 ``at_seq`` 处分叉工单，返回新工单 id（§4.5.8）。"""
        source_ticket = await self._ticket_service.get(ticket_id)
        source_session_id = source_ticket.worker_session_id
        if not source_session_id:
            raise _target_invalid("no_worker_session", ticket_id=ticket_id)

        now = self._clock()
        async with self._session_factory() as session:
            source_session = await AgentSessionRepository(session).get(
                source_session_id
            )
            if source_session is None:
                raise _target_invalid("no_worker_session", ticket_id=ticket_id)
            messages = await AgentMessageRepository(session).list_by_session(
                source_session_id, include_superseded=True
            )
            self._validate_target(messages, at_seq, AgentSessionKind.WORKER)
            source_mandate = await MandateRepository(session).get_by_ticket(ticket_id)
            if source_mandate is None:
                raise RuntimeError(f"工单 {ticket_id} 缺少委托记录")
            orchestrator = await AgentSessionRepository(
                session
            ).get_active_orchestrator()
            stored_ticket = await self._write_fork(
                session,
                source_ticket=source_ticket,
                source_session=source_session,
                source_mandate=source_mandate,
                messages=messages,
                at_seq=at_seq,
                orchestrator_session_id=(
                    str(orchestrator.id) if orchestrator is not None else None
                ),
                now=now,
            )
            await session.commit()

        await self._publish_ticket_created(stored_ticket)
        return str(stored_ticket.id)

    async def _write_fork(
        self,
        session: AsyncSession,
        *,
        source_ticket: Ticket,
        source_session: AgentSession,
        source_mandate: Mandate,
        messages: Sequence[AgentMessage],
        at_seq: int,
        orchestrator_session_id: str | None,
        now: datetime,
    ) -> Ticket:
        """在同一事务里写新工单、委托、Worker 会话、历史、检查点与提示。"""
        ticket = Ticket(
            id=new_uuid(),
            title=_fork_title(source_ticket.title),
            kind=source_ticket.kind,
            status=TicketStatus.OPEN.value,
            origin=TicketOrigin.USER.value,
            priority=source_ticket.priority,
            orchestrator_session_id=orchestrator_session_id,
            created_at=now,
            updated_at=now,
        )
        await TicketRepository(session).create(ticket)
        await MandateRepository(session).create(
            Mandate(
                ticket_id=str(ticket.id),
                scope=source_mandate.scope,
                strength=source_mandate.strength,
                source=source_mandate.source,
                sender=source_mandate.sender,
                rule_id=source_mandate.rule_id,
                risk_ceiling=source_mandate.risk_ceiling,
                policy_version=source_mandate.policy_version,
                created_at=now,
            )
        )

        copied = [
            message
            for message in messages
            if message.seq <= at_seq and message.superseded_at is None
        ]
        new_session = AgentSession(
            id=new_uuid(),
            title=source_session.title,
            model=source_session.model,
            base_url=source_session.base_url,
            kind=AgentSessionKind.WORKER,
            status=AgentSessionStatus.ACTIVE,
            ticket_id=str(ticket.id),
            forked_from_session_id=str(source_session.id),
            forked_from_seq=at_seq,
            message_count=len(copied) + 1,
            created_at=now,
            updated_at=now,
            last_message_at=now,
        )
        await AgentSessionRepository(session).create(new_session)
        # 工单与 Worker 会话的关联列在工单行上：新工单必须指到新会话，
        # ``TicketDetail.worker_session_id`` 与分叉来源反查都靠它。
        ticket.worker_session_id = str(new_session.id)
        await TicketRepository(session).update_if_status(
            str(ticket.id),
            allowed=(TicketStatus.OPEN.value,),
            values={"worker_session_id": str(new_session.id), "updated_at": now},
        )

        message_repo = AgentMessageRepository(session)
        for message in copied:
            await message_repo.create(
                AgentMessage(
                    session_id=str(new_session.id),
                    seq=int(message.seq),
                    role=message.role,
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
                    created_at=message.created_at,
                    run_id=None,
                    inbox_id=message.inbox_id,
                    superseded_at=None,
                )
            )

        checkpoint = await self._latest_valid_checkpoint(
            session, str(source_session.id), at_seq
        )
        if checkpoint is not None:
            await CompactionCheckpointRepository(session).add(
                CompactionCheckpoint(
                    session_id=str(new_session.id),
                    run_id=None,
                    upto_seq=int(checkpoint.upto_seq),
                    summary=checkpoint.summary,
                    summary_json=checkpoint.summary_json,
                    durable=checkpoint.durable,
                    tokens_before=checkpoint.tokens_before,
                    tokens_after=checkpoint.tokens_after,
                    model=checkpoint.model,
                    trigger=checkpoint.trigger,
                    status=CheckpointStatus.ACTIVE,
                    created_at=checkpoint.created_at,
                )
            )

        await message_repo.create(
            AgentMessage(
                session_id=str(new_session.id),
                seq=await message_repo.next_seq(str(new_session.id)),
                role="system",
                content=_fork_notice(source_ticket.title, at_seq),
                created_at=now,
            )
        )
        return ticket

    async def _latest_valid_checkpoint(
        self, session: AsyncSession, session_id: str, at_seq: int
    ) -> CompactionCheckpoint | None:
        """源会话 ``upto_seq ≤ at_seq``、未作废、``created_at`` 最新的检查点。"""
        rows = await CompactionCheckpointRepository(session).list_for_session(
            session_id
        )
        candidates = [
            row
            for row in rows
            if str(row.status) != CheckpointStatus.INVALIDATED
            and int(row.upto_seq) <= at_seq
        ]
        if not candidates:
            return None
        return max(candidates, key=lambda row: (row.created_at, str(row.id)))

    async def _publish_ticket_created(self, ticket: Ticket) -> None:
        """广播 ``ticket_status`` / ``ticket_created``；失败只记日志。"""
        if self._broadcast is None:
            return
        try:
            result = self._broadcast(
                "ticket_status", ticket_status_payload("ticket_created", ticket)
            )
            if inspect.isawaitable(result):
                await result
        except Exception:  # noqa: BLE001 - 工单已落库，广播失败不能回滚
            logger.exception("分叉工单创建广播失败：ticket=%s", ticket.id)


def _invalidate_ids(
    checkpoints: Sequence[CompactionCheckpoint], to_seq: int
) -> tuple[str, ...]:
    """``status`` 为 active / superseded 且 ``upto_seq > to_seq`` 的检查点。"""
    return tuple(
        str(row.id)
        for row in checkpoints
        if str(row.status)
        in (CheckpointStatus.ACTIVE, CheckpointStatus.SUPERSEDED)
        and int(row.upto_seq) > to_seq
    )


def _reactivate_id(
    checkpoints: Sequence[CompactionCheckpoint], to_seq: int
) -> str | None:
    """作废后没有 active 时，``upto_seq ≤ to_seq``、未作废、最新的一条。"""
    if any(
        str(row.status) == CheckpointStatus.ACTIVE and int(row.upto_seq) <= to_seq
        for row in checkpoints
    ):
        return None
    candidates = [
        row
        for row in checkpoints
        if str(row.status) != CheckpointStatus.INVALIDATED
        and int(row.upto_seq) <= to_seq
    ]
    if not candidates:
        return None
    latest = max(candidates, key=lambda row: (row.created_at, str(row.id)))
    return str(latest.id)


def _tool_batch_complete(
    messages: Sequence[AgentMessage], target: AgentMessage
) -> bool:
    """目标 tool 消息所属批次的结果是否都落在目标之内（§4.5.7 目标表）。"""
    if not target.tool_call_id:
        return False
    assistant = None
    for message in reversed(messages):
        if message.seq >= target.seq:
            continue
        if str(message.role) != "assistant" or not message.tool_calls:
            continue
        if target.tool_call_id in _tool_call_ids(message.tool_calls):
            assistant = message
            break
    if assistant is None:
        return False
    ids = _tool_call_ids(assistant.tool_calls)
    return not any(
        message.seq > target.seq
        and str(message.role) == "tool"
        and message.tool_call_id in ids
        for message in messages
    )


def _notice_text(
    superseded: int, side_effects: Sequence[SideEffect], events: int
) -> str:
    """回溯提示消息的正文（§4.5.7，逐字）。"""
    parts = [f"用户回到了这里，之后的 {superseded} 条消息已作废。"]
    if side_effects:
        listed = "、".join(
            f"{effect.tool_name}（{effect.detail}）"
            if effect.detail
            else effect.tool_name
            for effect in side_effects
        )
        parts.append(f"作废部分里已经执行的操作不会撤销：{listed}。")
    if events:
        parts.append(
            f"作废部分里的 {events} 条事件不会重新送达，"
            "相关工单与流水线的当前状态以查询为准。"
        )
    parts.append("继续之前先查看现场，不要重复这些操作。")
    return "".join(parts)


def _effect_detail(result: Mapping[str, Any], titles: Mapping[str, str]) -> str | None:
    """按 §4.5.7 的取法给出副作用的可读说明；都没有时 ``None``。"""
    pipeline_id = result.get("pipeline_id")
    if pipeline_id:
        return f"流水线 {_short(pipeline_id)}"
    ticket_id = result.get("ticket_id")
    if ticket_id:
        title = titles.get(str(ticket_id))
        return f"工单「{title}」" if title else None
    schedule_id = result.get("schedule_id")
    if schedule_id:
        return f"定时任务 {_short(schedule_id)}"
    artifact_id = result.get("artifact_id")
    if artifact_id:
        return f"产物 {_short(artifact_id)}"
    return None


def _fork_title(source_title: str) -> str:
    """源标题 + 「（分叉）」；超过 64 字时截源标题。"""
    room = _MAX_TITLE_CHARS - len(FORK_TITLE_SUFFIX)
    return f"{source_title[:room]}{FORK_TITLE_SUFFIX}"


def _fork_notice(source_title: str, at_seq: int) -> str:
    """分叉后追加的 system 提示（§4.5.8，逐字）。"""
    return (
        f"这个工单从工单「{source_title}」的第 {at_seq} 条消息处分叉。"
        "原工单在这之后做过的事不在这段记录里；"
        "设备、流水线与账号的当前状态以现场为准。"
    )
