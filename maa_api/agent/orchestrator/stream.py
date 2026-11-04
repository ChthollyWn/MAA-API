"""SSE v3 帧与 WS ``agent_run`` 广播（规格 §4.3.2、§4.3.3、§4.5.8）。

``RunEventStream`` 是两条 SSE 端点的帧生成器：构造时先订阅总线（免得漏事件），
登记消息到达时已在跑的那一轮，之后把那一轮剩下的事件与「消费本条消息的那一轮」
按顺序转成帧。``frames()`` 在收件箱项被丢弃或消费它的运行已结束时用数据库兜底，
不依赖运行一定发出 ``run_end``。

``AgentRunBroadcaster`` 是同一总线的 WS 出口，把运行事件映射成 ``agent_run``
频道上的 ``run_started`` / ``run_finished`` / ``message_created``；T13 在 lifespan
里用 ``run_event_bus`` 与 ``ws_manager.broadcast`` 装配它。
"""

from __future__ import annotations

import asyncio
import logging
from collections.abc import AsyncIterator, Callable, Mapping
from typing import Any

from maa_api.agent.harness.events import SSE_V3_EVENTS, RunEvent, sse_event
from maa_api.db.models import AgentInbox, AgentSession
from maa_api.db.repositories.agent import TERMINAL_RUN_STATUSES
from maa_api.domain.enums import AgentSessionKind, InboxStatus
from maa_api.domain.errors import ErrorCode

__all__ = [
    "SSE_KEEPALIVE_SECONDS",
    "SSE_V3_EVENTS",
    "AgentRunBroadcaster",
    "RunEventStream",
]

logger = logging.getLogger(__name__)

#: keep-alive 注释帧的间隔；也决定数据库兜底的检查节奏（规格 §4.5.8）。
SSE_KEEPALIVE_SECONDS = 10.0

#: 收件箱项被丢弃时的错误帧：总控流用 ``RUN_NOT_FOUND``，工单流用 ``TICKET_ENDED``。
_DROPPED_ERRORS: Mapping[str, tuple[str, str]] = {
    str(AgentSessionKind.ORCHESTRATOR): (
        str(ErrorCode.RUN_NOT_FOUND),
        "这条消息没有被处理",
    ),
}
_DROPPED_ERROR_FALLBACK = (
    str(ErrorCode.TICKET_ENDED),
    "工单已结束，这条消息没有被处理",
)

#: WS ``run_finished`` 的终态推导：``run_end`` 只带 ``finish_reason``。
_FAILED_FINISH_REASONS = frozenset({"error", "budget_exceeded", "stuck_loop"})


def _event_data(event: RunEvent) -> Mapping[str, Any]:
    return event.data if isinstance(event.data, Mapping) else {}


def _event_run_id(event: RunEvent) -> str | None:
    """事件属于哪条运行：优先总线给的 ``run_id``，测试与兜底再读 data。"""
    run_id = event.run_id or _event_data(event).get("run_id")
    return str(run_id) if run_id else None


class RunEventStream:
    """一条 SSE 流的生命周期（规格 §4.5.8）。

    构造即订阅；``frames()`` 第一次推进时再等「消息到达时已在跑的那一轮」的登记
    完成，避免端点返回 ``StreamingResponse`` 之前漏掉它。
    """

    def __init__(self, app: Any, *, session_id: str) -> None:
        self.app = app
        self.session_id = session_id
        self._queue: asyncio.Queue[RunEvent] = asyncio.Queue()
        #: 本流关心其事件的运行：消息到达时已在跑的，加上之后消费本条消息的。
        self._runs: set[str] = set()
        #: 目标运行（消费本条消息的那一轮）：它的 ``run_end`` 之后关流。
        self._target_run: str | None = None
        #: 流里已经见过的 ``run_end``，数据库兜底不重复合成。
        self._ended_runs: set[str] = set()
        self._inbox_id: int | None = None
        self._closed = False

        state = getattr(app, "state", None)
        self._run_service = getattr(state, "run_service", None)
        self._event_bus = getattr(state, "run_event_bus", None)
        self._unsubscribe: Callable[[], None] | None = None
        if self._event_bus is not None:
            self._unsubscribe = self._event_bus.subscribe(self._on_event)

        self._registration: asyncio.Task[None] | None = None
        if self._run_service is not None:
            try:
                self._registration = asyncio.create_task(
                    self._register_active_run(), name="maa-api-sse-active-run"
                )
            except RuntimeError:  # 没有事件循环（同步构造）：frames() 里再登记
                self._registration = None

    async def _register_active_run(self) -> None:
        """消息到达时已经在跑的那一轮：它的剩余事件也要转发（§4.5.8）。"""
        service = self._run_service
        if service is None:
            return
        try:
            active = await service.active_run(self.session_id)
        except Exception:  # noqa: BLE001 - 登记失败不能把 SSE 请求变成 500
            logger.warning("SSE 流登记活动运行失败：session=%s", self.session_id, exc_info=True)
            return
        if active is not None:
            self._runs.add(str(active.id))

    async def _wait_ready(self) -> None:
        task = self._registration
        if task is not None:
            await task

    async def _on_event(self, event: RunEvent) -> None:
        data = _event_data(event)
        run_id = _event_run_id(event)
        if event.name == "run_start" and data.get("session_id") == self.session_id:
            if run_id:
                self._runs.add(run_id)
        elif (
            event.name in ("run_start", "subagent_start")
            and data.get("parent_run_id") in self._runs
        ):
            if run_id:
                self._runs.add(run_id)
        if run_id not in self._runs or event.name not in SSE_V3_EVENTS:
            return
        if event.name == "run_end":
            self._ended_runs.add(run_id)
        self._queue.put_nowait(event)

    def track(self, inbox_id: int) -> None:
        """登记这条请求写入的收件箱项；消费它的那一轮就是目标运行。"""
        self._inbox_id = int(inbox_id)

    async def frames(self) -> AsyncIterator[str]:
        """按顺序产出 SSE 帧；目标运行结束、项被丢弃或客户端断开时收尾。"""
        try:
            await self._wait_ready()
            while not self._closed:
                try:
                    event = await asyncio.wait_for(
                        self._queue.get(), timeout=SSE_KEEPALIVE_SECONDS
                    )
                except TimeoutError:
                    yield ": keep-alive\n\n"
                    tail = await self._check_by_db()
                    if tail is not None:
                        for frame in tail:
                            yield frame
                        return
                    continue
                yield sse_event(event.name, event.data)
                data = _event_data(event)
                if (
                    event.name == "run_start"
                    and data.get("session_id") == self.session_id
                    and self._inbox_id is not None
                    and self._inbox_id in (data.get("consumed_inbox_ids") or [])
                ):
                    self._target_run = _event_run_id(event)
                if event.name == "run_end" and _event_run_id(event) == self._target_run:
                    return
        finally:
            self.close()

    def close(self) -> None:
        """退订并停止登记任务；幂等（客户端断开时由 ``finally`` 调用）。"""
        if self._closed:
            return
        self._closed = True
        if self._unsubscribe is not None:
            self._unsubscribe()
            self._unsubscribe = None
        task = self._registration
        if task is not None and not task.done():
            task.cancel()
        self._registration = None

    async def _check_by_db(self) -> list[str] | None:
        """keep-alive 超时后的兜底：项被丢弃、或目标运行已结束却没见到 ``run_end``。"""
        inbox_id = self._inbox_id
        service = self._run_service
        factory = getattr(service, "session_factory", None)
        if inbox_id is None or factory is None:
            return None
        async with factory() as db:
            item = await db.get(AgentInbox, inbox_id)
            if item is None:
                return None
            item_session_id = str(item.session_id)
            if item_session_id != self.session_id:
                # 总控重置时把项搬到了新会话：改跟新会话，别把旧会话的流耗死。
                self.session_id = item_session_id
                await self._register_active_run()
                return None
            item_status = str(item.status)
            if item_status == InboxStatus.DROPPED:
                return [await self._dropped_frame(db)]
            if item_status != InboxStatus.CONSUMED:
                return None
            run_id = item.consumed_run_id
        if not run_id or str(run_id) in self._ended_runs:
            return None
        run = await service.get(str(run_id))
        if run is None or str(run.status) not in TERMINAL_RUN_STATUSES:
            return None
        self._ended_runs.add(str(run_id))
        return [sse_event("run_end", _synthesized_run_end(run))]

    async def _dropped_frame(self, db: Any) -> str:
        session = await db.get(AgentSession, self.session_id)
        kind = str(session.kind) if session is not None else ""
        code, message = _DROPPED_ERRORS.get(kind, _DROPPED_ERROR_FALLBACK)
        return sse_event("error", {"code": code, "message": message})


def _synthesized_run_end(run: Any) -> dict[str, Any]:
    """用运行行合成 ``run_end``，字段与 AgentLoop 发出的一致。"""
    return {
        "run_id": str(run.id),
        "finish_reason": run.finish_reason or str(run.status),
        "usage": {
            "prompt_tokens": int(getattr(run, "prompt_tokens", 0) or 0),
            "completion_tokens": int(getattr(run, "completion_tokens", 0) or 0),
            "cached_tokens": int(getattr(run, "cached_tokens", 0) or 0),
            "cost_micros": int(getattr(run, "cost_micros", 0) or 0),
            "currency": getattr(run, "currency", None),
        },
    }


class AgentRunBroadcaster:
    """把总线上的三种事件映射成 WS ``agent_run``（规格 §4.3.3）。

    ``run_end`` 只带 ``run_id`` / ``finish_reason``，``message_persisted`` 不带
    ``run_id``；两者要的会话与运行字段由 ``run_start`` 记下的上下文补齐。路由写的
    用户消息 ``kind`` 为 null，按「没有运行的消息」输出 null 的运行字段。
    """

    def __init__(
        self,
        event_bus: Any,
        broadcast: Callable[[str, dict[str, Any]], None],
    ) -> None:
        self._event_bus = event_bus
        self._broadcast = broadcast
        self._unsubscribe: Callable[[], None] | None = None
        self._runs: dict[str, dict[str, Any]] = {}
        self._session_runs: dict[str, str] = {}

    def start(self) -> None:
        """开始订阅；重复调用无副作用。"""
        if self._unsubscribe is not None or self._event_bus is None:
            return
        self._unsubscribe = self._event_bus.subscribe(self._on_event)

    def stop(self) -> None:
        """退订并清空运行上下文；重复调用无副作用。"""
        if self._unsubscribe is not None:
            self._unsubscribe()
            self._unsubscribe = None
        self._runs.clear()
        self._session_runs.clear()

    async def _on_event(self, event: RunEvent) -> None:
        if event.name == "run_start":
            self._emit_started(event)
        elif event.name == "run_end":
            self._emit_finished(event)
        elif event.name == "message_persisted":
            self._emit_message(event)

    def _emit_started(self, event: RunEvent) -> None:
        data = _event_data(event)
        run_id = _event_run_id(event)
        if not run_id:
            return
        context = {
            "run_id": run_id,
            "session_id": data.get("session_id"),
            "ticket_id": data.get("ticket_id"),
            "agent": data.get("agent"),
            "kind": data.get("kind"),
        }
        self._runs[run_id] = context
        session_id = context["session_id"]
        if session_id:
            self._session_runs[str(session_id)] = run_id
        self._broadcast("agent_run", {"event": "run_started", **context, "status": "running"})

    def _emit_finished(self, event: RunEvent) -> None:
        data = _event_data(event)
        run_id = _event_run_id(event)
        context = self._runs.pop(run_id, {}) if run_id else {}
        if run_id:
            session_id = context.get("session_id") or data.get("session_id")
            if session_id and self._session_runs.get(str(session_id)) == run_id:
                self._session_runs.pop(str(session_id), None)
        self._broadcast(
            "agent_run",
            {
                "event": "run_finished",
                "run_id": run_id,
                "session_id": _pick(data, context, "session_id"),
                "ticket_id": _pick(data, context, "ticket_id"),
                "agent": _pick(data, context, "agent"),
                "kind": _pick(data, context, "kind"),
                "status": _finished_status(data),
                "finish_reason": data.get("finish_reason"),
            },
        )

    def _emit_message(self, event: RunEvent) -> None:
        data = _event_data(event)
        session_id = data.get("session_id")
        # 路由写的用户消息 kind 为 null：它不属于任何运行，运行字段全为 null。
        has_run = bool(data.get("kind"))
        run_id = data.get("run_id")
        if not run_id and has_run and session_id:
            run_id = self._session_runs.get(str(session_id))
        context = self._runs.get(str(run_id), {}) if run_id else {}
        raw_message = data.get("message")
        message = raw_message if isinstance(raw_message, Mapping) else {}
        self._broadcast(
            "agent_run",
            {
                "event": "message_created",
                "run_id": run_id,
                "session_id": session_id,
                "ticket_id": _pick(data, context, "ticket_id"),
                "agent": _pick(data, context, "agent"),
                "kind": _pick(data, context, "kind"),
                "status": "running" if run_id else None,
                "message": {
                    key: message.get(key)
                    for key in ("id", "seq", "role", "inbox_id", "preview")
                },
            },
        )


def _pick(data: Mapping[str, Any], context: Mapping[str, Any], key: str) -> Any:
    value = data.get(key)
    return value if value is not None else context.get(key)


def _finished_status(data: Mapping[str, Any]) -> str:
    """``run_end`` 的终态：``run_end`` 目前只带 ``finish_reason``。"""
    status = data.get("status")
    if status:
        return str(status)
    reason = str(data.get("finish_reason") or "")
    if reason == "interrupted":
        return "interrupted"
    if reason == "cancelled":
        return "cancelled"
    if reason in _FAILED_FINISH_REASONS:
        return "failed"
    return "succeeded"
