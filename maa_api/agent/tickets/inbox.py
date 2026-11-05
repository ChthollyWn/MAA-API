"""代理收件箱服务：写入、批量领取与监听者通知（M69 规格 §4.2、§4.5.5）。

同一项不能被两个运行领到，由两层机制共同保证：

- **进程内**：``claim`` 按会话 id 持一把 :class:`asyncio.Lock`，同一进程里的两个
  运行不会交错执行「读 pending → 标记 consumed」。
- **数据库**：``AgentInboxRepository.mark_consumed`` 的 ``AND status='pending'``
  是条件更新，SQLite 只允许一个写者；后到的事务改不到已被领走的行，
  ``list_consumed_by`` 再按 ``consumed_run_id`` 过滤，所以输家只会拿到空集或剩下
  的项。两者冲突时以数据库为准（02 §3 第 6 条）。

``put`` 会先做参数校验再碰数据库：校验失败不落库、也不通知监听者。
"""

from __future__ import annotations

import asyncio
import json
import logging
from collections.abc import Callable, Mapping
from datetime import datetime
from typing import Any

from sqlalchemy import and_, func, select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from maa_api.agent.tickets.threads import parse_thread_key
from maa_api.db.models import AgentInbox, AgentMessage, utcnow
from maa_api.db.repositories.agent import (
    AgentMessageRepository,
    AgentSessionRepository,
)
from maa_api.db.repositories.ticket import AgentInboxRepository
from maa_api.domain.enums import InboxKind, InboxStatus
from maa_api.domain.errors import AppError, ErrorCode

logger = logging.getLogger(__name__)

#: 一条收件箱项 ``payload`` 序列化成 JSON（UTF-8）后的上限（M69 规格 §4.2）。
MAX_PAYLOAD_BYTES = 16384
#: ``ref_id`` 的最大长度。
MAX_REF_ID_CHARS = 64
#: 领取条数的合法区间；越界是调用方用错，抛 ``ValueError`` 而不是 ``AppError``。
MIN_CLAIM_LIMIT = 1
MAX_CLAIM_LIMIT = 200

#: 有新项时的同步通知；参数是收件会话 id。M70 的 OrchestratorService /
#: WorkerService 用它起运行。
InboxListener = Callable[[str], None]

__all__ = [
    "MAX_CLAIM_LIMIT",
    "MAX_PAYLOAD_BYTES",
    "MAX_REF_ID_CHARS",
    "MIN_CLAIM_LIMIT",
    "InboxListener",
    "InboxService",
]


def _invalid(message: str, details: dict[str, Any] | None = None) -> AppError:
    return AppError(ErrorCode.INVALID_PARAMETER, message, details)


def _validate_ref_id(ref_id: str | None) -> None:
    if ref_id is None:
        return
    if not isinstance(ref_id, str) or len(ref_id) > MAX_REF_ID_CHARS:
        raise _invalid(
            f"收件箱项的 ref_id 必须是 {MAX_REF_ID_CHARS} 字以内的字符串",
            {"ref_id": ref_id},
        )


def _normalize_payload(payload: Mapping[str, Any] | None) -> dict[str, Any]:
    """返回可 JSON 序列化、且编码后不超过 16 KiB 的普通 dict。"""
    if payload is None:
        return {}
    if not isinstance(payload, Mapping):
        raise _invalid("收件箱载荷必须是 JSON 对象", {"payload_type": type(payload).__name__})
    normalized = dict(payload)
    try:
        encoded = json.dumps(normalized, ensure_ascii=False).encode("utf-8")
    except (TypeError, ValueError) as exc:
        raise _invalid("收件箱载荷无法序列化成 JSON", {"reason": str(exc)}) from exc
    if len(encoded) > MAX_PAYLOAD_BYTES:
        raise _invalid(
            f"收件箱载荷超过 {MAX_PAYLOAD_BYTES} 字节",
            {"size": len(encoded), "limit": MAX_PAYLOAD_BYTES},
        )
    return normalized


#: 汇报状态与人工待办类型的中文（M70 §4.5.9）。
_REPORT_STATUS_LABELS: Mapping[str, str] = {
    "progress": "进行中",
    "waiting": "等待用户",
    "ready": "已完成",
    "blocked": "受阻",
    "failed": "失败",
    "dropped": "已放弃",
}
_HUMAN_ACTION_LABELS: Mapping[str, str] = {
    "confirmation": "确认",
    "question": "回答",
    "decision": "决定",
}


class InboxService:
    """``agent_inbox`` 的写入与领取；事务边界由本服务自己掌握。"""

    def __init__(
        self,
        session_factory: async_sessionmaker[AsyncSession],
        *,
        clock: Callable[[], datetime] = utcnow,
    ) -> None:
        self._session_factory = session_factory
        self._clock = clock
        self._locks: dict[str, asyncio.Lock] = {}
        self._listeners: list[InboxListener] = []

    def add_listener(self, listener: InboxListener) -> None:
        """注册新项监听者；按注册顺序在提交之后调用。"""
        self._listeners.append(listener)

    async def put(
        self,
        session_id: str,
        kind: InboxKind,
        *,
        thread_key: str | None = None,
        ref_id: str | None = None,
        payload: Mapping[str, Any] | None = None,
    ) -> AgentInbox:
        """校验后写一条 pending 项并提交；提交后同步通知监听者，异常只记日志。"""
        if thread_key:
            parse_thread_key(thread_key)
        _validate_ref_id(ref_id)
        normalized = _normalize_payload(payload)

        now = self._clock()
        async with self._session_factory() as session:
            item = await AgentInboxRepository(session).create(
                AgentInbox(
                    session_id=session_id,
                    kind=str(kind),
                    thread_key=thread_key,
                    ref_id=ref_id,
                    payload=normalized,
                    status=InboxStatus.PENDING,
                    created_at=now,
                )
            )
            await session.commit()

        # 提交之后才通知：监听者在回调里开新会话必须能读到该项。
        self._notify(session_id)
        return item

    def _notify(self, session_id: str) -> None:
        """提交之后按注册顺序通知；单个监听者失败只记日志（M69 §4.5.5）。"""
        for listener in tuple(self._listeners):
            try:
                listener(session_id)
            except Exception:
                logger.exception("收件箱监听者失败（会话 %s）", session_id)

    async def put_user_message(
        self,
        session_id: str,
        *,
        content: str,
        thread_key: str | None = None,
        payload: Mapping[str, Any] | None = None,
    ) -> tuple[AgentInbox, AgentMessage]:
        """同一事务写用户消息与 ``user_message`` 收件箱项（M70 §4.5.2）。

        用户消息的 ``run_id`` 与 ``inbox_id`` 为空；收件箱项的 ``ref_id`` 是消息
        ``seq``，``payload.message_id`` 指向刚落库的消息。提交后通知监听者。
        """
        if thread_key:
            parse_thread_key(thread_key)
        normalized = _normalize_payload(payload)
        normalized.setdefault("text", content)

        now = self._clock()
        async with self._session_factory() as session:
            stored_session = await AgentSessionRepository(session).get(session_id)
            if stored_session is None:
                raise AppError(
                    ErrorCode.AGENT_SESSION_NOT_FOUND, "Agent 会话不存在"
                )
            messages = AgentMessageRepository(session)
            seq = await messages.next_seq(session_id)
            message = await messages.create(
                AgentMessage(
                    session_id=session_id,
                    seq=seq,
                    role="user",
                    content=content,
                    run_id=None,
                    inbox_id=None,
                    created_at=now,
                )
            )
            normalized["message_id"] = int(message.id)
            item = await AgentInboxRepository(session).create(
                AgentInbox(
                    session_id=session_id,
                    kind=str(InboxKind.USER_MESSAGE),
                    thread_key=thread_key,
                    ref_id=str(seq),
                    payload=normalized,
                    status=InboxStatus.PENDING,
                    created_at=now,
                )
            )
            stored_session.message_count += 1
            stored_session.updated_at = now
            stored_session.last_message_at = now
            await session.commit()

        self._notify(session_id)
        return item, message

    async def claim(
        self, session_id: str, *, run_id: str, limit: int = 50
    ) -> list[AgentInbox]:
        """批量领取该会话最早的 ``limit`` 条 pending 项并置 consumed（§4.5.5）。"""
        limit = int(limit)
        if not MIN_CLAIM_LIMIT <= limit <= MAX_CLAIM_LIMIT:
            raise ValueError(
                f"claim 的 limit 必须在 {MIN_CLAIM_LIMIT}–{MAX_CLAIM_LIMIT} 之间"
            )

        lock = self._locks.setdefault(session_id, asyncio.Lock())
        async with lock:
            async with self._session_factory() as session:
                repo = AgentInboxRepository(session)
                ids = await repo.pending_ids(session_id, limit=limit)
                if not ids:
                    return []
                now = self._clock()
                await repo.mark_consumed(ids, run_id=run_id, now=now)
                rows = await repo.list_consumed_by(ids, run_id=run_id)
                await session.commit()
                return rows

    async def drop_pending(self, session_id: str) -> int:
        """把该会话全部 pending 项标成 dropped，返回改到的条数。"""
        now = self._clock()
        async with self._session_factory() as session:
            dropped = await AgentInboxRepository(session).drop_pending(
                session_id, now=now
            )
            await session.commit()
            return dropped

    async def pending_count(self, session_id: str) -> int:
        """该会话 pending 项条数。"""
        async with self._session_factory() as session:
            return await AgentInboxRepository(session).count_pending(session_id)

    async def count_pending(self, session_id: str) -> int:
        """``pending_count`` 的 M70 名字（§3.3 表里的 ``count_pending``）。"""
        return await self.pending_count(session_id)

    async def has_active_pending(self, session_id: str) -> bool:
        """是否存在会单独唤醒一轮的 pending 项（M70 §4.5.9）。

        ``system`` 项且 ``payload.passive`` 为真时不算活跃工作：它不单独起一轮，
        但会被下一次 ``claim`` 一起领走。判定在 SQLite 里用 ``json_extract`` 做，
        避免把整页项读进内存。
        """
        passive = func.coalesce(func.json_extract(AgentInbox.payload, "$.passive"), 0)
        statement = (
            select(func.count())
            .select_from(AgentInbox)
            .where(
                AgentInbox.session_id == session_id,
                AgentInbox.status == InboxStatus.PENDING,
                ~and_(AgentInbox.kind == InboxKind.SYSTEM, passive == 1),
            )
        )
        async with self._session_factory() as session:
            result = await session.execute(statement)
            return int(result.scalar_one()) > 0

    def render(self, item: AgentInbox) -> str | None:
        """把一条收件箱项转成消息正文（M70 §4.5.9）；``user_message`` 返回 ``None``。"""
        kind = str(item.kind)
        payload = item.payload or {}
        if kind == InboxKind.USER_MESSAGE:
            return None
        if kind == InboxKind.REPORT:
            title = payload.get("ticket_title") or payload.get("title") or ""
            ticket_kind = payload.get("ticket_kind") or payload.get("kind") or ""
            status = _REPORT_STATUS_LABELS.get(
                str(payload.get("status")), str(payload.get("status") or "")
            )
            text = (
                f"【工单汇报】工单「{title}」（{ticket_kind}，"
                f"ticket_id={payload.get('ticket_id')}）{status}："
                f"{payload.get('summary') or ''}"
            )
            action = payload.get("human_action")
            if isinstance(action, Mapping):
                action_type = _HUMAN_ACTION_LABELS.get(
                    str(action.get("type")), str(action.get("type") or "")
                )
                text += f"\n需要用户处理（{action_type}）：{action.get('prompt') or ''}"
            return text
        if kind == InboxKind.THREAD_EVENT:
            return (
                f"【事件】{item.thread_key or ''}：{payload.get('summary') or ''}"
            )
        if kind == InboxKind.TRIGGER:
            # M81 §4.2：一条事件三行；研判理由只在规则真的给了理由时出现。
            lines = [f"【事件】{payload.get('summary') or ''}"]
            lines.append(
                f"话题 {item.thread_key or ''}，事件 #{payload.get('trigger_id')}"
            )
            reason = payload.get("reason")
            if reason:
                lines.append(f"研判：{reason}")
            return "\n".join(lines)
        if kind == InboxKind.SYSTEM:
            return f"【系统】{payload.get('text') or ''}"
        return None
