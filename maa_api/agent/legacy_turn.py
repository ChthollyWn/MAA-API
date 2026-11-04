"""v2 会话轮的 SSE 驱动：从 :class:`AgentRuntime` 抽出，供评测与测试复用。

M72 删掉了 v2 写端点与运行时的旧 SSE 方法；但评测运行器
（``agent.evals.runner``）仍要驱动 v2 场景，``tests/fakes/legacy_turn`` 也靠它
保住 v2 行为覆盖。实现与删除前的方法体逐字一致：锁检查、生产 task 登记、
keep-alive 与收尾都由这里负责，轮次本身仍走 ``runtime._produce_turn``。生产
HTTP 路径不再调用本模块。
"""

from __future__ import annotations

import asyncio
from collections.abc import AsyncIterator
from typing import Any

from maa_api.agent import runtime as runtime_module
from maa_api.agent.harness.events import sse_event
from maa_api.domain.errors import ErrorCode

__all__ = ["stream_legacy_turn"]


async def stream_legacy_turn(
    runtime: Any, session_id: str, content: str, request: Any
) -> AsyncIterator[str]:
    """Produce named SSE events. Caller acquires the session slot first."""
    if session_id not in runtime._locked_sessions:
        yield sse_event(
            "error",
            {
                "code": str(ErrorCode.AGENT_SESSION_BUSY),
                "message": "会话没有持有活动轮次锁",
            },
        )
        return
    queue: asyncio.Queue[Any] = asyncio.Queue()
    producer = asyncio.create_task(
        runtime._produce_turn(queue, session_id, content, request),
        name=f"agent-turn-{session_id}",
    )
    runtime._turn_tasks.add(producer)
    producer.add_done_callback(runtime._forget_turn_task)
    while True:
        waiter = asyncio.create_task(queue.get())
        runtime._queue_waiters.add(waiter)
        waiter.add_done_callback(runtime._forget_queue_waiter)
        while not waiter.done():
            await asyncio.wait(
                (waiter,), timeout=runtime_module.SSE_KEEPALIVE_SECONDS
            )
            if not waiter.done():
                yield ": keep-alive\n\n"
        item = waiter.result()
        if item is runtime_module._SSE_END:
            await asyncio.gather(producer, return_exceptions=True)
            return
        yield item
