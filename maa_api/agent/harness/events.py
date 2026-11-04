"""运行事件与 SSE 帧（规格 §4.2.6）。

``RunEventBus`` 是中间件与循环向外发数据的唯一出口；M65 只有门面一个订阅者，诊断
与侦察的总线没有订阅者，事件直接丢弃。``sse_event`` 是 v2 的原样实现：先
``redact_remote_data`` 再紧凑 JSON，帧字节与 v2 完全一致。
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
    """按订阅顺序把同一个 :class:`RunEvent` 交给每个 sink。"""

    def __init__(self, *, run_id: str | None = None) -> None:
        self.run_id = run_id
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

    async def emit(self, name: str, data: Any) -> None:
        event = RunEvent(name=name, data=data, run_id=self.run_id)
        # 快照：sink 在 emit 期间订阅或退订不影响这一次的分发顺序。
        for sink in tuple(self._sinks):
            await sink(event)


def sse_event(event: str, data: Any) -> str:
    """把一条事件编成 v2 的 SSE 帧（先脱敏，再紧凑 JSON）。"""
    safe = redact_remote_data(data)
    return f"event: {event}\ndata: {json.dumps(safe, ensure_ascii=False, separators=(',', ':'), default=str)}\n\n"
