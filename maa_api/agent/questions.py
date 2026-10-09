"""Agent 主动提问：带选项的卡片、挂起等待与用户裁决（规格 §3.3）。

与 :class:`~maa_api.agent.confirmation.ConfirmationService` 同构但**独立**：两者
都是「落库先行 + 进程内 :class:`asyncio.Event` 只做唤醒 + 重启清理」，但这条链路
处理的是「在若干选项里取一个答案」，不是「授权一次有风险的操作」。因此它没有策略
引擎、没有风险等级、不推送通知，也**不占「待确认」计数**（规格 §2.2）。

三条实现纪律：

- **落库先行**：提问先落 ``agent_question``（``pending`` + ``expires_at``），内存里
  的 Event 只是让等待方**尽快**醒来的加速器。等待方每轮都会重新读一次库，因此就算
  事件在别的路径上丢了（跨进程、REST 直接回答、扫描任务置过期），
  :meth:`wait` 仍会按 ``expires_at`` 收敛到终态而不是永久挂起。
- **不抛异常表示「用户没答」**：超时与跳过都是正常的终态，由调用方（runtime）读
  wire 里的 ``status`` 决定怎么收尾；只有「找不到 / 已经处理过 / 选择不合法」这类
  调用方用错了的路径才抛 :class:`~maa_api.domain.errors.AppError`。
- **广播失败不影响主流程**：``question_request`` / ``question_resolved`` 只是给前端
  失效查询用的信号，WebSocket 挂了不能把一次已经落库的提问变成失败。
"""

from __future__ import annotations

import asyncio
import logging
from collections.abc import Callable, Mapping, Sequence
from datetime import UTC, datetime, timedelta
from typing import Any

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from maa_api.db.models import AgentAudit, AgentQuestion, utcnow
from maa_api.db.repositories.audit import AuditRepository
from maa_api.db.repositories.question import AgentQuestionRepository
from maa_api.domain.enums import (
    AuditStatus,
    CallerType,
    QuestionStatus,
    RiskLevel,
)
from maa_api.domain.errors import AppError, ErrorCode

logger = logging.getLogger(__name__)

#: 默认等待时长，与 ``agent.question_timeout_seconds`` 的默认值一致（规格 §1.3）。
DEFAULT_QUESTION_TIMEOUT_SECONDS = 10 * 60
#: 等待方重新读库的节奏；事件先到就立刻返回，用不到这个间隔。
DEFAULT_QUESTION_POLL_SECONDS = 0.25
#: ``ask_user`` 在审计里的工具名：提问也是工具调用，审计页要能按名字筛出来。
ASK_USER_TOOL_NAME = "ask_user"
#: 三条「没答成」的收尾文案；过期同时落审计的 result_summary。
EXPIRED_SUMMARY = "提问超时，本轮已结束"
RESTART_SUMMARY = "服务重启，未回答的提问已失效"
SHUTDOWN_SUMMARY = "服务关闭，未回答的提问已失效"
SKIPPED_SUMMARY = "用户跳过了提问"
#: 本轮被用户中断时撤回挂起提问的收尾文案（规格 §3.3）。
CANCELLED_SUMMARY = "本轮已被中断，提问已取消"


class QuestionService:
    """Agent 提问的创建、等待、裁决与过期收敛（规格 §3.3）。"""

    def __init__(
        self,
        session_factory: async_sessionmaker[AsyncSession],
        *,
        broadcast: Callable[[str, dict[str, Any]], None] | None = None,
        timeout_seconds: int = DEFAULT_QUESTION_TIMEOUT_SECONDS,
        clock: Callable[[], datetime] = utcnow,
    ) -> None:
        if timeout_seconds < 1:
            raise ValueError("question timeout must be positive")
        self.session_factory = session_factory
        self.broadcast = broadcast or (lambda _kind, _data: None)
        self.timeout_seconds = int(timeout_seconds)
        self.clock = clock
        self._resolution_events: dict[str, asyncio.Event] = {}
        self._expiry_task: asyncio.Task[None] | None = None

    async def create(
        self,
        *,
        session_id: str,
        tool_call_id: str,
        question: str,
        options: Sequence[Any],
        multiple: bool,
        request_id: str | None = None,
    ) -> tuple[dict[str, Any], int]:
        """落一条提问与它对应的审计行，返回 ``(wire, audit_id)``。

        审计行在同一事务里创建，``status=pending``、``risk_level=none``：提问不授权
        任何东西（没有风险等级），但「问过什么、用户答了什么」必须和普通工具调用一样
        可审计。答案落定后由 :meth:`answer` / :meth:`skip` / :meth:`expire_due` 把这一
        行推到终态，因此不存在「提问已 answered 而审计还 pending」的中间态。
        """
        now = self.clock()
        expires_at = now + timedelta(seconds=self.timeout_seconds)
        normalized = _normalize_options(options)
        async with self.session_factory() as session:
            row = await AgentQuestionRepository(session).create(
                AgentQuestion(
                    session_id=session_id,
                    tool_call_id=tool_call_id,
                    question=question,
                    options=normalized,
                    multiple=bool(multiple),
                    status=QuestionStatus.PENDING,
                    expires_at=expires_at,
                    created_at=now,
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
                    created_at=now,
                )
            )
            row.audit_id = audit.id
            await session.flush()
            wire = self._wire(row)
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
            return self._wire(row)

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
            return {
                "items": [self._wire(row) for row in result.items],
                "total": result.total,
                "page": result.page,
                "size": result.size,
            }

    async def wait(
        self, question_id: str, *, poll_seconds: float = DEFAULT_QUESTION_POLL_SECONDS
    ) -> dict[str, Any]:
        """等这条提问进入终态并返回终态 wire；超时自己收敛，**不抛异常**。

        等待事件与轮询两路并行（规格 §3.3）：事件负责「尽快」，轮询负责「不漏」：由 REST 直接回答、或由后台扫描任务置过期的提问不会经过本进程的事件字典。
        到 ``expires_at`` 仍未答就自己走 :meth:`_expire_now` 兜底（扫描间隔可能配置得
        比超时还长），然后返回 ``expired`` 的 wire。
        """
        row = await self._load(question_id)
        if row.status != QuestionStatus.PENDING:
            return self._wire(row)
        deadline = _as_utc(row.expires_at)
        event = self._resolution_events.get(question_id)
        if event is None:
            event = asyncio.Event()
            self._resolution_events[question_id] = event
        try:
            while True:
                remaining = (deadline - _as_utc(self.clock())).total_seconds()
                if remaining <= 0:
                    break
                try:
                    await asyncio.wait_for(
                        event.wait(), timeout=min(max(poll_seconds, 0.001), remaining)
                    )
                except TimeoutError:
                    pass
                if event.is_set():
                    break
                current = await self._load(question_id)
                if current.status != QuestionStatus.PENDING:
                    return self._wire(current)
            # 事件没等到、时间也用完了：置过期；CAS 失败说明别人抢先处理，读回真值即可。
            expired = await self._expire_now(question_id)
            if expired is not None:
                return expired
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

        校验顺序是刻意的：先 404、再终态（已答 / 已跳过 / 已过期），最后才校验选择
        内容。对一个已经处理过的提问回 400，会把「你来晚了」说成「你选错了」。
        """
        row = await self._load(question_id)
        if row.status != QuestionStatus.PENDING:
            raise _terminal_error(row)
        if _is_overdue(row, self.clock()):
            await self._expire_now(question_id)
            raise AppError(ErrorCode.QUESTION_EXPIRED, "提问已过期")
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
                # CAS 失败只可能是并发抢答/抢过期：读回真值按状态报错。
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
        return wire

    async def skip(
        self, question_id: str, *, answered_by: str = "web"
    ) -> dict[str, Any]:
        """用户点了「跳过」：CAS ``pending → skipped``，审计置 ``rejected``。"""
        row = await self._load(question_id)
        if row.status != QuestionStatus.PENDING:
            raise _terminal_error(row)
        if _is_overdue(row, self.clock()):
            await self._expire_now(question_id)
            raise AppError(ErrorCode.QUESTION_EXPIRED, "提问已过期")
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
        留下的 pending 卡片会一直挂着直到超时，前端刷新后还会把它当成可提交的提问。
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
            await session.commit()
            wires = [self._wire(row) for row in rows]
        # 广播与唤醒放在事务提交之后：等待方被唤醒后会立刻重读库，此时终态必须已经可见。
        for wire in wires:
            self._broadcast_resolved(wire, "cancelled")
        return [str(wire["question_id"]) for wire in wires]

    async def reset_after_restart(self) -> None:
        """把重启前遗留的 pending 提问置 ``expired``。

        进程重启后等待那一轮的协程已经不存在了，没超时的提问也不可能再被回答
        （规格 §3.3）：留着 pending 只会让前端刷新后看到一张永远提交不了的卡片。
        """
        rows = await self._expire_all(RESTART_SUMMARY)
        self._broadcast_expired(rows)

    async def expire_due(self) -> list[str]:
        """把 ``expires_at`` 已过的待答提问置 ``expired``，返回其 id 列表。

        后台扫描与 :meth:`wait` 的超时兜底共用同一条落库路径：终态、审计与广播三件
        事必须完全一致，不能有两条「过期」的实现。
        """
        now = self.clock()
        async with self.session_factory() as session:
            repo = AgentQuestionRepository(session)
            ids = await repo.expire_overdue(now)
            rows = await _rows_by_ids(session, ids)
            for row in rows:
                await self._finish_audit(
                    session,
                    row.audit_id,
                    AuditStatus.EXPIRED,
                    error_code=str(ErrorCode.QUESTION_EXPIRED),
                    result_summary=EXPIRED_SUMMARY,
                )
            await session.commit()
            wires = [self._wire(row) for row in rows]
        self._broadcast_expired(wires)
        return ids

    async def start(self, *, scan_interval_seconds: float = 10.0) -> None:
        """清理重启遗留的 pending，并启动超时扫描（重复调用是空操作）。"""
        if self._expiry_task is not None and not self._expiry_task.done():
            return
        await self.reset_after_restart()
        self._expiry_task = asyncio.create_task(
            self._expiry_loop(max(float(scan_interval_seconds), 0.05)),
            name="agent-question-expiry-scan",
        )

    async def close(self) -> None:
        """停扫描，并把还挂着的 pending 置 ``expired``（它们的等待方已经没了）。"""
        if self._expiry_task is not None:
            self._expiry_task.cancel()
            await asyncio.gather(self._expiry_task, return_exceptions=True)
            self._expiry_task = None
        rows = await self._expire_all(SHUTDOWN_SUMMARY)
        self._broadcast_expired(rows)

    async def _expiry_loop(self, interval: float) -> None:
        while True:
            await asyncio.sleep(interval)
            try:
                await self.expire_due()
            except asyncio.CancelledError:
                raise
            except Exception:  # noqa: BLE001 - 扫描失败不能打死后台任务
                logger.exception("Agent 提问过期扫描失败")

    async def _load(self, question_id: str) -> AgentQuestion:
        async with self.session_factory() as session:
            row = await AgentQuestionRepository(session).get(question_id)
            if row is None:
                raise AppError(ErrorCode.QUESTION_NOT_FOUND, "提问不存在")
            return row

    async def _expire_now(self, question_id: str) -> dict[str, Any] | None:
        """把单条超时提问置 ``expired`` 并广播，返回终态 wire。

        ``None`` 表示 CAS 没改到行（别人已经答了或已经置过期）：调用方读回真值即可。
        """
        now = self.clock()
        async with self.session_factory() as session:
            repo = AgentQuestionRepository(session)
            row = await repo.get(question_id)
            if row is None or row.status != QuestionStatus.PENDING:
                return None
            changed = await repo.resolve(
                row.id,
                QuestionStatus.EXPIRED,
                answered_by="system",
                answered_at=now,
            )
            if not changed:
                await session.rollback()
                return None
            await self._finish_audit(
                session,
                row.audit_id,
                AuditStatus.EXPIRED,
                error_code=str(ErrorCode.QUESTION_EXPIRED),
                result_summary=EXPIRED_SUMMARY,
            )
            await session.commit()
        wire = await self.get(question_id)
        self._broadcast_resolved(wire, "expired")
        return wire

    async def _expire_all(self, summary: str) -> list[dict[str, Any]]:
        """把所有 pending 提问置终态（重启清理与服务关闭共用），返回终态 wire 列表。"""
        now = self.clock()
        async with self.session_factory() as session:
            repo = AgentQuestionRepository(session)
            ids = await repo.expire_all_pending(now)
            rows = await _rows_by_ids(session, ids)
            for row in rows:
                await self._finish_audit(
                    session,
                    row.audit_id,
                    AuditStatus.EXPIRED,
                    error_code=str(ErrorCode.QUESTION_EXPIRED),
                    result_summary=summary,
                )
            await session.commit()
            return [self._wire(row) for row in rows]

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

    def _broadcast_expired(self, wires: Sequence[Mapping[str, Any]]) -> None:
        for wire in wires:
            self._broadcast_resolved(wire, "expired")

    @staticmethod
    def _wire(row: AgentQuestion) -> dict[str, Any]:
        """REST 与 SSE 共用的提问形状（规格 §3.3）。"""
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
            "expires_at": _wire_time(row.expires_at),
            "answered_at": _wire_time(row.answered_at),
            "answered_by": row.answered_by,
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
    """按终态给出 409：已过期是 ``QUESTION_EXPIRED``，已答/已跳过是 ``QUESTION_ALREADY_ANSWERED``。"""
    if row.status == QuestionStatus.EXPIRED:
        return AppError(ErrorCode.QUESTION_EXPIRED, "提问已过期")
    return AppError(ErrorCode.QUESTION_ALREADY_ANSWERED, "提问已经处理")


def _is_overdue(row: AgentQuestion, now: datetime) -> bool:
    return _as_utc(row.expires_at) <= _as_utc(now)


def _as_utc(value: datetime) -> datetime:
    return value.replace(tzinfo=UTC) if value.tzinfo is None else value.astimezone(UTC)


def _wire_time(value: datetime | None) -> str | None:
    if value is None:
        return None
    return _as_utc(value).isoformat().replace("+00:00", "Z")
