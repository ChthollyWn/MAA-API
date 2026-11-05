"""话题键的格式、解析、来源前缀与话题路由（M69 规格 §4.2、§4.5.4、v3 03 §15）。

前四个公开名（``ThreadKey`` / ``parse_thread_key`` / 两张映射表 / ``RouteResult``）
是不碰数据库的纯函数与数据形状；``ThreadRouter`` 负责把一条话题消息投递到订阅
工单的 Worker 收件箱，没有落点时降级给总控。
"""

from __future__ import annotations

import re
from collections.abc import Callable, Mapping
from dataclasses import dataclass
from datetime import datetime
from typing import TYPE_CHECKING, Any, Literal

from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from maa_api.db.models import ThreadSubscription, Ticket, utcnow
from maa_api.db.repositories.agent import AgentSessionRepository
from maa_api.db.repositories.ticket import (
    ThreadSubscriptionRepository,
    TicketRepository,
)
from maa_api.domain.enums import (
    AgentSessionKind,
    AgentSessionStatus,
    EventSource,
    InboxKind,
    TicketStatus,
)
from maa_api.domain.errors import AppError, ErrorCode

if TYPE_CHECKING:
    from maa_api.agent.tickets.inbox import InboxService

__all__ = [
    "MAX_THREAD_KEY_CHARS",
    "SOURCE_PREFIXES",
    "THREAD_KEY_PATTERNS",
    "RouteResult",
    "ThreadKey",
    "ThreadRouter",
    "parse_thread_key",
]

MAX_THREAD_KEY_CHARS = 160
_UUID = r"[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}"

#: 十种话题键前缀的完整格式（03 §15）。
THREAD_KEY_PATTERNS: Mapping[str, re.Pattern[str]] = {
    "chat": re.compile(r"^chat:orchestrator$"),
    "ticket": re.compile(rf"^ticket:{_UUID}$"),
    "pipeline": re.compile(rf"^pipeline:{_UUID}$"),
    "core": re.compile(r"^core:[a-z0-9][a-z0-9_-]{0,31}$"),
    "device": re.compile(r"^device:[a-z0-9][a-z0-9_-]{0,31}$"),
    "schedule": re.compile(r"^schedule:[a-z0-9][a-z0-9_-]{0,47}$"),
    "system": re.compile(r"^system:[a-z][a-z0-9_]{0,63}$"),
    "im": re.compile(
        r"^im:(telegram|feishu|dingtalk|qq):[A-Za-z0-9_@.:=+/-]{1,128}$"
    ),
    "goal": re.compile(rf"^goal:{_UUID}$"),
    "watchdog": re.compile(r"^watchdog:[a-z][a-z0-9_]{0,47}$"),
}
#: 03 §15 的来源与前缀对应，M81 用它校验事件。
SOURCE_PREFIXES: Mapping[EventSource, frozenset[str]] = {
    EventSource.WEB: frozenset({"chat", "ticket"}),
    EventSource.IM_TELEGRAM: frozenset({"im"}),
    EventSource.IM_FEISHU: frozenset({"im"}),
    EventSource.IM_DINGTALK: frozenset({"im"}),
    EventSource.IM_QQ: frozenset({"im"}),
    EventSource.PIPELINE: frozenset({"pipeline"}),
    EventSource.CORE: frozenset({"core"}),
    EventSource.DEVICE: frozenset({"device"}),
    EventSource.SCHEDULE: frozenset({"schedule"}),
    EventSource.SYSTEM: frozenset({"system"}),
    EventSource.WATCHDOG: frozenset({"watchdog"}),
    EventSource.GOAL: frozenset({"goal"}),
}


@dataclass(frozen=True, slots=True)
class ThreadKey:
    """解析后的话题键；``value`` 是第一个冒号之后的部分。"""

    raw: str
    prefix: str
    value: str


@dataclass(frozen=True, slots=True)
class RouteResult:
    """一次 ``ThreadRouter.publish`` 的落点（T5 使用）。"""

    routed_to: Literal["ticket", "orchestrator", "none"]
    inbox_ids: tuple[int, ...]
    ticket_ids: tuple[str, ...]


def parse_thread_key(key: str) -> ThreadKey:
    """长度超过 160、前缀未知或格式不符 → ``INVALID_PARAMETER``。"""
    if not isinstance(key, str) or len(key) > MAX_THREAD_KEY_CHARS:
        raise AppError(
            ErrorCode.INVALID_PARAMETER,
            "话题键格式不对",
            {"thread_key": key},
        )
    prefix, separator, value = key.partition(":")
    pattern = THREAD_KEY_PATTERNS.get(prefix)
    if not separator or pattern is None or pattern.fullmatch(key) is None:
        raise AppError(
            ErrorCode.INVALID_PARAMETER,
            "话题键格式不对",
            {"thread_key": key},
        )
    return ThreadKey(raw=key, prefix=prefix, value=value)


def _ticket_is_terminal(ticket: Ticket) -> bool:
    return TicketStatus(ticket.status).is_terminal


class ThreadRouter:
    """话题订阅与投递（M69 规格 §4.5.4）；落点永远是收件箱，不直接起运行。"""

    def __init__(
        self,
        session_factory: async_sessionmaker[AsyncSession],
        inbox_service: InboxService,
        *,
        clock: Callable[[], datetime] = utcnow,
    ) -> None:
        self._session_factory = session_factory
        self._inbox_service = inbox_service
        self._clock = clock

    async def subscribe(self, ticket_id: str, thread_key: str) -> ThreadSubscription:
        """订阅话题；重复订阅幂等。非法键、已结束与不存在的工单分别报错。"""
        key = parse_thread_key(thread_key)
        if key.prefix in ("ticket", "chat"):
            raise AppError(
                ErrorCode.INVALID_PARAMETER,
                "这个话题不需要订阅",
                {"thread_key": thread_key},
            )
        async with self._session_factory() as session:
            ticket = await TicketRepository(session).get(ticket_id)
            if ticket is None:
                raise AppError(
                    ErrorCode.TICKET_NOT_FOUND,
                    "没有这个工单",
                    {"ticket_id": ticket_id},
                )
            if _ticket_is_terminal(ticket):
                raise AppError(
                    ErrorCode.TICKET_ENDED,
                    "工单已结束",
                    {"ticket_id": ticket_id},
                )
            subscription = await ThreadSubscriptionRepository(session).upsert_active(
                ticket_id, thread_key, now=self._clock()
            )
            await session.commit()
            return subscription

    async def unsubscribe(self, ticket_id: str, thread_key: str) -> bool:
        """停用一条活动订阅；本来就不活动或不存在时返回 ``False``。"""
        async with self._session_factory() as session:
            deactivated = await ThreadSubscriptionRepository(session).deactivate(
                ticket_id, thread_key, now=self._clock()
            )
            await session.commit()
            return deactivated

    async def deliver(
        self,
        thread_key: str,
        *,
        kind: InboxKind = InboxKind.THREAD_EVENT,
        payload: Mapping[str, Any] | None = None,
        ref_id: str | None = None,
    ) -> list[str]:
        """只投给 active 订阅的工单，返回投到的会话 id；**不回落总控**（M70 §3.3）。

        与 :meth:`publish` 的差别只有一条：没有 active 订阅（订阅已随工单结束置为
        inactive、工单已结束或还没有 Worker 会话）时返回空列表，不写总控收件箱。
        「没订阅时交总控」留给需要它的调用方（``publish`` / ``to_orchestrator``）。
        """
        parse_thread_key(thread_key)
        delivered: list[str] = []
        for _ticket_id, session_id in await self._subscribed_targets(thread_key):
            await self._inbox_service.put(
                session_id,
                kind,
                thread_key=thread_key,
                ref_id=ref_id,
                payload=payload,
            )
            delivered.append(session_id)
        return delivered

    async def deliver_to_subscribers(
        self,
        thread_key: str,
        *,
        inbox_kind: InboxKind,
        ref_id: str,
        payload: Mapping[str, Any],
    ) -> list[str]:
        """把事件写进所有活跃订阅工单的 Worker 收件箱，返回投到的**工单 id**。

        M81 §4.5.2 第 1 步：只投订阅者，没有订阅时返回空列表、不交总控（与
        :meth:`deliver` 同口径）。内部复用同一套订阅者解析，只是把返回值从会话 id
        换成工单 id，供事件表记 ``ticket_id`` 与 ``routed_to=ticket``。
        """
        parse_thread_key(thread_key)
        delivered: list[str] = []
        for ticket_id, session_id in await self._subscribed_targets(thread_key):
            await self._inbox_service.put(
                session_id,
                inbox_kind,
                thread_key=thread_key,
                ref_id=ref_id,
                payload=payload,
            )
            delivered.append(ticket_id)
        return list(dict.fromkeys(delivered))

    async def _subscribed_targets(self, thread_key: str) -> list[tuple[str, str]]:
        """active 订阅里「工单未结束且已有 Worker 会话」的 ``(工单 id, 会话 id)``。"""
        async with self._session_factory() as session:
            subscribed = await ThreadSubscriptionRepository(
                session
            ).active_ticket_ids(thread_key)
            ticket_repo = TicketRepository(session)
            targets: list[tuple[str, str]] = []
            for ticket_id in subscribed:
                ticket = await ticket_repo.get(ticket_id)
                if (
                    ticket is not None
                    and not _ticket_is_terminal(ticket)
                    and ticket.worker_session_id
                ):
                    targets.append((ticket.id, ticket.worker_session_id))
        return targets

    async def publish(
        self,
        thread_key: str,
        *,
        kind: InboxKind = InboxKind.THREAD_EVENT,
        ref_id: str | None = None,
        payload: Mapping[str, Any] | None = None,
    ) -> RouteResult:
        """把一条话题消息投递到订阅工单的 Worker 收件箱；没有落点就交总控。"""
        key = parse_thread_key(thread_key)
        targets: list[str] = []
        ticket_ids: list[str] = []
        via_orchestrator = False

        if key.prefix == "chat":
            # chat:orchestrator 直接进总控。
            orchestrator_id = await self.orchestrator_session_id()
            if orchestrator_id is not None:
                targets.append(orchestrator_id)
            via_orchestrator = True
        elif key.prefix == "ticket":
            # 工单自己的话题：隐式订阅，直接投给它的 Worker；没有 Worker 就交总控。
            async with self._session_factory() as session:
                ticket = await TicketRepository(session).get(key.value)
            if (
                ticket is not None
                and not _ticket_is_terminal(ticket)
                and ticket.worker_session_id
            ):
                targets.append(ticket.worker_session_id)
                ticket_ids.append(ticket.id)
            else:
                orchestrator_id = await self.orchestrator_session_id()
                if orchestrator_id is not None:
                    targets.append(orchestrator_id)
                via_orchestrator = True
        else:
            # 订阅工单按订阅 id 升序；已结束或还没有 Worker 会话的跳过。
            async with self._session_factory() as session:
                subscribed = await ThreadSubscriptionRepository(
                    session
                ).active_ticket_ids(thread_key)
                ticket_repo = TicketRepository(session)
                for ticket_id in subscribed:
                    ticket = await ticket_repo.get(ticket_id)
                    if (
                        ticket is not None
                        and not _ticket_is_terminal(ticket)
                        and ticket.worker_session_id
                    ):
                        targets.append(ticket.worker_session_id)
                        ticket_ids.append(ticket.id)
            if not targets:
                orchestrator_id = await self.orchestrator_session_id()
                if orchestrator_id is not None:
                    targets.append(orchestrator_id)
                via_orchestrator = True

        unique_targets = list(dict.fromkeys(targets))
        if not unique_targets:
            return RouteResult(routed_to="none", inbox_ids=(), ticket_ids=())
        inbox_ids = []
        for session_id in unique_targets:
            item = await self._inbox_service.put(
                session_id,
                kind,
                thread_key=thread_key,
                ref_id=ref_id,
                payload=payload,
            )
            inbox_ids.append(int(item.id))
        return RouteResult(
            routed_to="orchestrator" if via_orchestrator else "ticket",
            inbox_ids=tuple(inbox_ids),
            ticket_ids=tuple(ticket_ids),
        )

    async def to_orchestrator(
        self,
        *,
        kind: InboxKind,
        thread_key: str | None,
        ref_id: str | None,
        payload: Mapping[str, Any],
        preferred_session_id: str | None = None,
    ) -> int | None:
        """写进总控收件箱并返回收件箱项 id；没有活动的总控会话返回 ``None``。"""
        session_id = await self.orchestrator_session_id(preferred_session_id)
        if session_id is None:
            return None
        item = await self._inbox_service.put(
            session_id,
            kind,
            thread_key=thread_key,
            ref_id=ref_id,
            payload=payload,
        )
        return int(item.id)

    async def orchestrator_session_id(self, preferred: str | None = None) -> str | None:
        """优先用 ``preferred``（活动、未归档的总控会话），否则取当前活动的总控。"""
        async with self._session_factory() as session:
            repo = AgentSessionRepository(session)
            if preferred:
                candidate = await repo.get(preferred)
                if (
                    candidate is not None
                    and candidate.kind == AgentSessionKind.ORCHESTRATOR
                    and candidate.status == AgentSessionStatus.ACTIVE
                    and candidate.archived_at is None
                ):
                    return str(candidate.id)
            active = await repo.get_active_orchestrator()
            return str(active.id) if active is not None else None
