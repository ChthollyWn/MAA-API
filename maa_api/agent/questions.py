"""Agent 主动提问：带选项的卡片、挂起等待与用户裁决（规格 §3.3）。

与 :class:`~maa_api.agent.confirmation.ConfirmationService` 同构但**独立**：两者
都是「落库先行 + 进程内 :class:`asyncio.Event` 只做唤醒」，但这条链路处理的是
「在若干选项里取一个答案」，不是「授权一次有风险的操作」。因此它没有策略引擎、
没有风险等级、不推送通知，也**不占「待确认」计数**（规格 §2.2）。

三条实现纪律：

- **落库先行**：提问先落 ``agent_question``（``pending``），内存里的 Event 只是让
  等待方**尽快**醒来的加速器。等待方每轮都会重新读一次库，因此就算事件在别的路径
  上丢了（跨进程、REST 直接回答、会话中断），:meth:`wait` 仍会收敛到终态。
- **不抛异常表示「用户没答」**：跳过与取消都是正常的终态，由调用方（runtime）读
  wire 里的 ``status`` 决定怎么收尾；只有「找不到 / 已经处理过 / 选择不合法」这类
  调用方用错了的路径才抛 :class:`~maa_api.domain.errors.AppError`。
- **广播失败不影响主流程**：``question_request`` / ``question_resolved`` 只是给前端
  失效查询用的信号，WebSocket 挂了不能把一次已经落库的提问变成失败。

**提问没有时间失效**（维护性补丁 `questions-no-expiry`）：pending 提问一直保留到被
回答、跳过，或被生命周期收尾取消（会话中断 / 工单结束 / 总控重新开始）。服务重启
不再清理 pending；若提问的来源运行已经不在，:meth:`answer` 会把答案交给装配层注入
的投递回调（工单 → Worker、总控 → 总控会话），由新的一轮接着办。
"""

from __future__ import annotations

import asyncio
import logging
from collections.abc import Awaitable, Callable, Mapping, Sequence
from datetime import UTC, datetime
from typing import Any

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from maa_api.agent.origin import origin_wire, ticket_titles
from maa_api.agent.registry import ToolOrigin
from maa_api.db.models import AgentAudit, AgentQuestion, AgentRun, utcnow
from maa_api.db.repositories.audit import AuditRepository
from maa_api.db.repositories.question import AgentQuestionRepository
from maa_api.domain.enums import (
    AgentRunStatus,
    AuditStatus,
    CallerType,
    QuestionStatus,
    RiskLevel,
)
from maa_api.domain.errors import AppError, ErrorCode

logger = logging.getLogger(__name__)

#: 等待方重新读库的节奏；事件先到就立刻返回，用不到这个间隔。
DEFAULT_QUESTION_POLL_SECONDS = 0.25
#: ``ask_user`` 在审计里的工具名：提问也是工具调用，审计页要能按名字筛出来。
ASK_USER_TOOL_NAME = "ask_user"
#: 两条「没答成」的收尾文案；过期只存在于历史数据，不再产生新的。
SKIPPED_SUMMARY = "用户跳过了提问"
#: 本轮被用户中断时撤回挂起提问的收尾文案（规格 §3.3）。
CANCELLED_SUMMARY = "本轮已被中断，提问已取消"
#: 工单结束时撤回该工单挂起提问的收尾文案（维护性补丁 §4.3）。
TICKET_ENDED_SUMMARY = "工单已结束，提问已取消"
#: 运行处于这些状态时，等待方仍在进程内等答案，不需要投递。
TERMINAL_RUN_STATUSES = frozenset(
    {
        AgentRunStatus.SUCCEEDED,
        AgentRunStatus.FAILED,
        AgentRunStatus.INTERRUPTED,
        AgentRunStatus.CANCELLED,
    }
)

#: 答案投递回调：输入是终态提问 wire（含 selection 与 origin）。
AnswerDelivery = Callable[[dict[str, Any]], Awaitable[None]]


class QuestionService:
    """Agent 提问的创建、等待、裁决与生命周期收尾（规格 §3.3）。"""

    def __init__(
        self,
        session_factory: async_sessionmaker[AsyncSession],
        *,
        broadcast: Callable[[str, dict[str, Any]], None] | None = None,
        clock: Callable[[], datetime] = utcnow,
    ) -> None:
        self.session_factory = session_factory
        self.broadcast = broadcast or (lambda _kind, _data: None)
        self.clock = clock
        self._resolution_events: dict[str, asyncio.Event] = {}
        self._answer_delivery: AnswerDelivery | None = None

    def set_answer_delivery(self, delivery: AnswerDelivery | None) -> None:
        """注入「来源运行已经结束」时的答案投递回调（装配层实现路由）。"""
        self._answer_delivery = delivery

    async def create(
        self,
        *,
        session_id: str,
        tool_call_id: str,
        question: str,
        options: Sequence[Any],
        multiple: bool,
        request_id: str | None = None,
        origin: ToolOrigin | None = None,
    ) -> tuple[dict[str, Any], int]:
        """落一条提问与它对应的审计行，返回 ``(wire, audit_id)``。

        审计行在同一事务里创建，``status=pending``、``risk_level=none``：提问不授权
        任何东西（没有风险等级），但「问过什么、用户答了什么」必须和普通工具调用一样
        可审计。答案落定后由 :meth:`answer` / :meth:`skip` / :meth:`cancel_pending_for_*`
        把这一行推到终态，因此不存在「提问已 answered 而审计还 pending」的中间态。
        ``origin`` 是发起提问的代理运行（M71 §4.2）；REST 直接提问时为空。
        """
        now = self.clock()
        normalized = _normalize_options(options)
        origin_run_id = origin.run_id if origin is not None else None
        origin_ticket_id = origin.ticket_id if origin is not None else None
        origin_agent = origin.agent if origin is not None else None
        async with self.session_factory() as session:
            row = await AgentQuestionRepository(session).create(
                AgentQuestion(
                    session_id=session_id,
                    tool_call_id=tool_call_id,
                    question=question,
                    options=normalized,
                    multiple=bool(multiple),
                    status=QuestionStatus.PENDING,
                    created_at=now,
                    origin_run_id=origin_run_id,
                    origin_ticket_id=origin_ticket_id,
                    origin_agent=origin_agent,
                )
            )
            audit = await AuditRepository(session).create(
                AgentAudit(
                    caller=CallerType.INTERNAL,
                    tool_name=ASK_USER_TOOL_NAME,
                    request_id=request_id,
                    arguments={
                        "question": question,
                        "options": normalized,
                        "multiple": bool(multiple),
                    },
                    status=AuditStatus.PENDING,
                    risk_level=RiskLevel.NONE,
                    session_id=session_id,
                    run_id=origin_run_id,
                    ticket_id=origin_ticket_id,
                    agent_name=origin_agent,
                    created_at=now,
                )
            )
            row.audit_id = audit.id
            await session.flush()
            titles = await ticket_titles(session, [origin_ticket_id])
            wire = self._wire(row, titles)
            audit_id = int(audit.id)
            await session.commit()
        self._broadcast("question_request", dict(wire))
        return wire, audit_id

    async def get(self, question_id: str) -> dict[str, Any]:
        """按 id 取一条提问的 wire；不存在抛 ``QUESTION_NOT_FOUND``。"""
        async with self.session_factory() as session:
            row = await AgentQuestionRepository(session).get(question_id)
            if row is None:
                raise AppError(ErrorCode.QUESTION_NOT_FOUND, "提问不存在")
            titles = await ticket_titles(session, [row.origin_ticket_id])
            return self._wire(row, titles)

    async def list(
        self,
        *,
        session_id: str | None = None,
        status: QuestionStatus | str | None = QuestionStatus.PENDING,
        page: int = 1,
        size: int = 20,
    ) -> dict[str, Any]:
        """提问分页列表，形状与其它列表接口一致（``items`` / ``total`` / ``page`` / ``size``）。"""
        async with self.session_factory() as session:
            result = await AgentQuestionRepository(session).list(
                session_id=session_id, status=status, page=page, size=size
            )
            titles = await ticket_titles(
                session, [row.origin_ticket_id for row in result.items]
            )
            return {
                "items": [self._wire(row, titles) for row in result.items],
                "total": result.total,
                "page": result.page,
                "size": result.size,
            }

    async def wait(
        self, question_id: str, *, poll_seconds: float = DEFAULT_QUESTION_POLL_SECONDS
    ) -> dict[str, Any]:
        """等这条提问进入终态并返回终态 wire；没有时间上限，**不抛异常**。

        等待事件与轮询两路并行（规格 §3.3）：事件负责「尽快」，轮询负责「不漏」：
        由 REST 直接回答、或由收尾路径取消的提问不会经过本进程的事件字典。提问没有
        超时（维护性补丁 `questions-no-expiry`），所以只有等待方被取消（中断）时才退出。
        """
        row = await self._load(question_id)
        if row.status != QuestionStatus.PENDING:
            return await self.get(question_id)
        event = self._resolution_events.get(question_id)
        if event is None:
            event = asyncio.Event()
            self._resolution_events[question_id] = event
        try:
            while True:
                try:
                    await asyncio.wait_for(
                        event.wait(), timeout=max(poll_seconds, 0.001)
                    )
                except TimeoutError:
                    pass
                if event.is_set():
                    break
                current = await self._load(question_id)
                if current.status != QuestionStatus.PENDING:
                    return await self.get(question_id)
            return await self.get(question_id)
        finally:
            self._resolution_events.pop(question_id, None)

    async def answer(
        self,
        question_id: str,
        *,
        selected: Sequence[str],
        answered_by: str = "web",
    ) -> dict[str, Any]:
        """提交用户的选择：CAS ``pending → answered``，冲突时按当前状态报 409。

        校验顺序是刻意的：先 404、再终态（已答 / 已跳过 / 已取消；历史 ``expired``
        行报 ``QUESTION_EXPIRED``），最后才校验选择内容。对一个已经处理过的提问回
        400，会把「你来晚了」说成「你选错了」。
        """
        row = await self._load(question_id)
        if row.status != QuestionStatus.PENDING:
            raise _terminal_error(row)
        labels = _validate_selection(row, selected)
        async with self.session_factory() as session:
            repo = AgentQuestionRepository(session)
            changed = await repo.resolve(
                row.id,
                QuestionStatus.ANSWERED,
                selection=labels,
                answered_by=answered_by,
                answered_at=self.clock(),
            )
            if not changed:
                # CAS 失败只可能是并发抢答/抢收尾：读回真值按状态报错。
                await session.rollback()
                current = await repo.get(question_id)
                if current is None:
                    raise AppError(ErrorCode.QUESTION_NOT_FOUND, "提问不存在")
                raise _terminal_error(current)
            await self._finish_audit(
                session,
                row.audit_id,
                AuditStatus.SUCCESS,
                result_summary="用户选择：" + "、".join(labels),
            )
            await session.commit()
        wire = await self.get(question_id)
        self._broadcast_resolved(wire, "answered")
        await self._deliver_if_orphaned(wire)
        return wire

    async def skip(
        self, question_id: str, *, answered_by: str = "web"
    ) -> dict[str, Any]:
        """用户点了「跳过」：CAS ``pending → skipped``，审计置 ``rejected``。"""
        row = await self._load(question_id)
        if row.status != QuestionStatus.PENDING:
            raise _terminal_error(row)
        async with self.session_factory() as session:
            repo = AgentQuestionRepository(session)
            changed = await repo.resolve(
                row.id,
                QuestionStatus.SKIPPED,
                answered_by=answered_by,
                answered_at=self.clock(),
            )
            if not changed:
                await session.rollback()
                current = await repo.get(question_id)
                if current is None:
                    raise AppError(ErrorCode.QUESTION_NOT_FOUND, "提问不存在")
                raise _terminal_error(current)
            await self._finish_audit(
                session,
                row.audit_id,
                AuditStatus.REJECTED,
                result_summary=SKIPPED_SUMMARY,
            )
            await session.commit()
        wire = await self.get(question_id)
        self._broadcast_resolved(wire, "skipped")
        return wire

    async def cancel_pending_for_session(self, session_id: str) -> list[str]:
        """撤回该会话仍挂起的提问：CAS ``pending → cancelled`` 并唤醒等待方（§3.3）。

        中断必须让卡片进入终态而不是留着 pending：等待那一轮的协程马上会被取消，
        留下的 pending 卡片会一直挂着，前端刷新后还会把它当成可提交的提问。
        审计置 ``rejected`` 与「跳过」同口径：提问没有得到答案，但也不是超时。
        返回被撤回的提问 id 列表；没有挂起提问时返回空列表（幂等）。
        """
        now = self.clock()
        async with self.session_factory() as session:
            repo = AgentQuestionRepository(session)
            ids = await repo.cancel_pending_for_session(session_id, now)
            rows = await _rows_by_ids(session, ids)
            for row in rows:
                await self._finish_audit(
                    session,
                    row.audit_id,
                    AuditStatus.REJECTED,
                    result_summary=CANCELLED_SUMMARY,
                )
            titles = await ticket_titles(
                session, [row.origin_ticket_id for row in rows]
            )
            await session.commit()
            wires = [self._wire(row, titles) for row in rows]
        # 广播与唤醒放在事务提交之后：等待方被唤醒后会立刻重读库，此时终态必须已经可见。
        for wire in wires:
            self._broadcast_resolved(wire, "cancelled")
        return [str(wire["question_id"]) for wire in wires]

    async def cancel_pending_for_ticket(self, ticket_id: str) -> list[str]:
        """撤回某个工单仍挂起的提问（工单结束时由结束监听器调用，维护性补丁 §4.3）。"""
        now = self.clock()
        async with self.session_factory() as session:
            repo = AgentQuestionRepository(session)
            ids = await repo.cancel_pending_for_ticket(ticket_id, now)
            rows = await _rows_by_ids(session, ids)
            for row in rows:
                await self._finish_audit(
                    session,
                    row.audit_id,
                    AuditStatus.REJECTED,
                    result_summary=TICKET_ENDED_SUMMARY,
                )
            titles = await ticket_titles(
                session, [row.origin_ticket_id for row in rows]
            )
            await session.commit()
            wires = [self._wire(row, titles) for row in rows]
        for wire in wires:
            self._broadcast_resolved(wire, "cancelled")
        return [str(wire["question_id"]) for wire in wires]

    async def _deliver_if_orphaned(self, wire: Mapping[str, Any]) -> None:
        """来源运行已结束、且本进程没有等待者时，把答案投递给来源会话（§4.2）。

        判定分两步：
        1. 本进程有等待者（``_resolution_events``）→ 等待轮会自己醒来，不投递。
        2. 来源运行仍是 running / waiting_human → 运行会读到已答状态，不投递；
           这覆盖「用户抢在 ``wait`` 注册事件之前作答」的竞态。
        只有来源运行已终态 / 不存在 / 没有 run_id 时才回调装配层，把答案交回会话。
        """
        delivery = self._answer_delivery
        if delivery is None:
            return
        question_id = str(wire.get("question_id") or "")
        if not question_id or question_id in self._resolution_events:
            return
        origin = wire.get("origin")
        run_id = origin.get("run_id") if isinstance(origin, Mapping) else None
        if isinstance(run_id, str) and run_id and await self._run_active(run_id):
            return
        try:
            await delivery(dict(wire))
        except Exception:  # noqa: BLE001 - 投递失败不能让已经落库的回答变成错误
            logger.exception("提问答案投递失败：question=%s", question_id)

    async def _run_active(self, run_id: str) -> bool:
        """来源运行是否还在（running / waiting_human）；查不到按已结束处理。"""
        async with self.session_factory() as session:
            row = await session.get(AgentRun, run_id)
        if row is None:
            return False
        try:
            status = AgentRunStatus(row.status)
        except ValueError:  # 未知状态按已结束处理，宁可投递也不要丢答案
            return False
        return status not in TERMINAL_RUN_STATUSES

    async def _load(self, question_id: str) -> AgentQuestion:
        async with self.session_factory() as session:
            row = await AgentQuestionRepository(session).get(question_id)
            if row is None:
                raise AppError(ErrorCode.QUESTION_NOT_FOUND, "提问不存在")
            return row

    async def _finish_audit(
        self,
        session: AsyncSession,
        audit_id: int | None,
        status: AuditStatus,
        *,
        error_code: str | None = None,
        result_summary: str | None = None,
    ) -> None:
        """把 ``ask_user`` 的审计行推到终态；``audit_id`` 为空（审计被清理）时跳过。"""
        if audit_id is None:
            return
        await AuditRepository(session).set_terminal(
            audit_id,
            status,
            error_code=error_code,
            result_summary=result_summary,
        )

    def _broadcast(self, message_type: str, data: dict[str, Any]) -> None:
        try:
            self.broadcast(message_type, data)
        except Exception:  # noqa: BLE001 - 广播只是前端失效查询的信号
            logger.exception("广播 %s 失败：%s", message_type, data.get("question_id"))

    def _broadcast_resolved(self, wire: Mapping[str, Any], reason: str) -> None:
        """广播终态：payload 就是 wire 加一个 ``reason``（规格 §3.3）。"""
        payload = dict(wire)
        payload["reason"] = reason
        self._broadcast("question_resolved", payload)
        event = self._resolution_events.get(str(wire.get("question_id")))
        if event is not None:
            event.set()

    @staticmethod
    def _wire(
        row: AgentQuestion, titles: Mapping[str, str] | None = None
    ) -> dict[str, Any]:
        """REST 与 SSE 共用的提问形状（规格 §3.3；origin 自 M71 §4.2）。

        维护性补丁 `questions-no-expiry` 起不再有 ``expires_at``：提问没有有效期。
        """
        return {
            "question_id": row.id,
            "session_id": row.session_id,
            "tool_call_id": row.tool_call_id,
            "question": row.question,
            "options": [
                {
                    "label": str(option.get("label", "")),
                    "description": option.get("description"),
                }
                for option in (row.options or [])
                if isinstance(option, Mapping)
            ],
            "multiple": bool(row.multiple),
            "status": row.status,
            "selection": row.selection,
            "created_at": _wire_time(row.created_at),
            "answered_at": _wire_time(row.answered_at),
            "answered_by": row.answered_by,
            "origin": origin_wire(
                run_id=row.origin_run_id,
                ticket_id=row.origin_ticket_id,
                agent=row.origin_agent,
                titles=titles or {},
            ),
        }


async def _rows_by_ids(session: AsyncSession, ids: Sequence[str]) -> list[AgentQuestion]:
    """按 id 批量取回行（批量 UPDATE 之后读真值，不能用身份映射里的旧实例）。"""
    if not ids:
        return []
    result = await session.execute(
        select(AgentQuestion)
        .where(AgentQuestion.id.in_(list(ids)))
        .execution_options(populate_existing=True)
    )
    return list(result.scalars().all())


def _normalize_options(options: Sequence[Any]) -> list[dict[str, Any]]:
    """把选项归一成 ``[{"label": ..., "description": ...}]`` 并落库/进审计。

    接受 Pydantic 模型（runtime 校验后的 ``AskUserOption``）与普通映射两种形态：
    服务不该依赖工具层的参数模型，但它必须保证落库形状只有一种。
    """
    normalized: list[dict[str, Any]] = []
    for option in options:
        if isinstance(option, Mapping):
            label = option.get("label")
            description = option.get("description")
        else:
            label = getattr(option, "label", None)
            description = getattr(option, "description", None)
        if not isinstance(label, str) or not label:
            raise AppError(
                ErrorCode.INVALID_PARAMETER,
                "提问选项缺少 label",
                {"options": [str(item) for item in options]},
            )
        normalized.append(
            {
                "label": label,
                "description": description if isinstance(description, str) else None,
            }
        )
    if not normalized:
        raise AppError(ErrorCode.INVALID_PARAMETER, "提问至少需要一个选项")
    return normalized


def _validate_selection(row: AgentQuestion, selected: Sequence[str]) -> list[str]:
    """校验并归一用户的选择，返回去重后的 label 列表。

    四条规则（规格 §3.3）：非空、去重、每一项都在 ``options.label`` 里、
    ``multiple=False`` 时只能一项。任何一条不满足都抛 ``QUESTION_SELECTION_INVALID``：这是 400 而不是 409：请求本身（选了什么）不成立，与提问当前状态无关。
    """
    if isinstance(selected, str):
        # 防御：REST body 是字符串数组，但内部调用方可能直接传单个 label。
        selected = [selected]
    labels = [option["label"] for option in _normalize_options(row.options or [])]
    cleaned: list[str] = []
    for item in selected:
        if not isinstance(item, str) or not item:
            raise _selection_error(selected, labels, row)
        if item in cleaned:
            continue
        if item not in labels:
            raise _selection_error(selected, labels, row)
        cleaned.append(item)
    if not cleaned:
        raise _selection_error(selected, labels, row)
    if not row.multiple and len(cleaned) > 1:
        raise _selection_error(selected, labels, row)
    return cleaned


def _selection_error(
    selected: Sequence[Any], labels: Sequence[str], row: AgentQuestion
) -> AppError:
    return AppError(
        ErrorCode.QUESTION_SELECTION_INVALID,
        "请在给定选项中选择",
        {
            "selected": [item for item in selected if isinstance(item, str)],
            "options": list(labels),
            "multiple": bool(row.multiple),
        },
    )


def _terminal_error(row: AgentQuestion) -> AppError:
    """按终态给出 409：历史过期是 ``QUESTION_EXPIRED``，其余是「已经处理」。"""
    if row.status == QuestionStatus.EXPIRED:
        return AppError(ErrorCode.QUESTION_EXPIRED, "提问已过期")
    return AppError(ErrorCode.QUESTION_ALREADY_ANSWERED, "提问已经处理")


def _as_utc(value: datetime) -> datetime:
    return value.replace(tzinfo=UTC) if value.tzinfo is None else value.astimezone(UTC)


def _wire_time(value: datetime | None) -> str | None:
    if value is None:
        return None
    return _as_utc(value).isoformat().replace("+00:00", "Z")
