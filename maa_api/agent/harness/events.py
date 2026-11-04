"""运行事件与 SSE 帧（规格 §4.2.6）。

``RunEventBus`` 是中间件与循环向外发数据的唯一出口；M65 只有门面一个订阅者，诊断
与侦察的总线没有订阅者，事件直接丢弃。

M70 起有两层总线：每轮运行一条带 ``run_id`` 的 per-run 总线，装配层再用一条
``run_id=None`` 的 hub 订阅全部运行（``app.state.run_event_bus``）。per-run 总线
通过构造参数 ``hub`` 或 :meth:`RunEventBus.forward_to` 把**同一个** ``RunEvent``
原样转给 hub，hub 只负责分发给 SSE / WS / 话题服务的订阅者，不重造事件——这样
``ticket_created`` 这类 data 里不带 ``run_id`` 的事件也不会失去归属。

``sse_event`` 是 v2 的原样实现：先 ``redact_remote_data`` 再紧凑 JSON，帧字节与
v2 完全一致。
"""

from __future__ import annotations

import json
from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from typing import Any

from maa_api.agent.redaction import redact_remote_data

__all__ = [
    "SSE_V3_EVENTS",
    "RunEvent",
    "RunEventBus",
    "RunEventSink",
    "sse_event",
]

#: SSE v3 转发的事件名（M70 §4.3.2）。总线内部事件 ``message_persisted`` 不在其中：
#: 它只给 WS 广播器用，T12 的 ``RunEventStream`` 按这个集合过滤。
SSE_V3_EVENTS = frozenset(
    {
        "run_start",
        "text_delta",
        "tool_start",
        "tool_result",
        "confirmation_required",
        "question_required",
        "subagent_start",
        "subagent_end",
        "report",
        "ticket_created",
        "interrupted",
        "run_end",
        "error",
    }
)


@dataclass(frozen=True, slots=True)
class RunEvent:
    #: v2 的 SSE 事件名，见规格 §4.3.3。
    name: str
    #: v2 的 data，原样。
    data: Any
    run_id: str | None = None


RunEventSink = Callable[[RunEvent], Awaitable[None]]


class RunEventBus:
    """按订阅顺序把同一个 :class:`RunEvent` 交给每个 sink。

    ``hub`` 是可选的上级总线：本总线每次 ``emit`` 之后把同一个事件对象转给
    hub 的订阅者（M70 §4.5.8）。一轮运行用 ``RunEventBus(run_id=..., hub=...)``
    保证事件带运行 id，装配层的 hub 用 ``run_id=None`` 只做分发。
    """

    def __init__(
        self, *, run_id: str | None = None, hub: RunEventBus | None = None
    ) -> None:
        if hub is self:
            raise ValueError("事件总线不能转发给自己")
        self.run_id = run_id
        self._hub = hub
        self._sinks: list[RunEventSink] = []

    def subscribe(self, sink: RunEventSink) -> Callable[[], None]:
        """登记一个 sink，返回退订函数（M70 §3.3：SSE 流断开时退订）。"""
        self._sinks.append(sink)

        def unsubscribe() -> None:
            try:
                self._sinks.remove(sink)
            except ValueError:  # 已经退订过：幂等
                pass

        return unsubscribe

    @property
    def hub(self) -> RunEventBus | None:
        """当前转发的上级总线（没有为 ``None``）。"""
        return self._hub

    def forward_to(self, hub: RunEventBus | None) -> None:
        """把本总线的事件转发到 hub；``None`` 表示停止转发。"""
        if hub is self:
            raise ValueError("事件总线不能转发给自己")
        self._hub = hub

    async def emit(self, name: str, data: Any) -> None:
        event = RunEvent(name=name, data=data, run_id=self.run_id)
        await self.dispatch(event)
        hub = self._hub
        if hub is not None:
            # 同一个对象原样转交：run_id、name、data 都不重造。
            await hub.dispatch(event)

    async def dispatch(self, event: RunEvent) -> None:
        """把已有事件交给订阅者；hub 转发走的也是这里。"""
        # 快照：sink 在 emit 期间订阅或退订不影响这一次的分发顺序。
        for sink in tuple(self._sinks):
            await sink(event)


def sse_event(event: str, data: Any) -> str:
    """把一条事件编成 v2 的 SSE 帧（先脱敏，再紧凑 JSON）。"""
    safe = redact_remote_data(data)
    return f"event: {event}\ndata: {json.dumps(safe, ensure_ascii=False, separators=(',', ':'), default=str)}\n\n"
