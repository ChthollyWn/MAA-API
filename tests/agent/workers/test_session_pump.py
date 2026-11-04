"""M70 T8：SessionPump 与并发闸（规格 §4.2.2、§4.5.1、§6）。

五条用例：一轮期间的多次唤醒合并成下一轮、批处理窗口内的唤醒都归第一轮、
WorkerSlots 的容量 / 优先级 / 容量热生效 / 取消移出、中断的调用顺序与宽限期、
未启动一轮的退避与 close。全部直接驱动，不经过 lifespan，也不碰数据库。
"""

from __future__ import annotations

import asyncio
import contextlib
import time

from maa_api.agent.workers import pump as pump_module
from maa_api.agent.workers.pump import (
    SessionPump,
    TurnHandle,
    WorkerSlots,
)


async def _wait_until(predicate, *, timeout: float = 1.0) -> None:
    """等一个同步谓词为真；超时直接失败，避免用例挂住。"""
    loop = asyncio.get_running_loop()
    deadline = loop.time() + timeout
    while not predicate():
        if loop.time() > deadline:
            raise AssertionError("等待条件超时")
        await asyncio.sleep(0.005)


def test_wakes_during_a_turn_merge_into_one_next_turn() -> None:
    async def scenario() -> None:
        pending = 1
        turns: list[TurnHandle] = []
        first_started = asyncio.Event()
        release_first = asyncio.Event()

        async def run_turn(handle: TurnHandle) -> bool:
            nonlocal pending
            turns.append(handle)
            # 一轮开始就把当时的工作全部领走；这之后到达的唤醒留给下一轮。
            pending = 0
            if len(turns) == 1:
                first_started.set()
                await release_first.wait()
            return True

        async def has_work() -> bool:
            return pending > 0

        pump = SessionPump(
            "session-merge",
            run_turn=run_turn,
            has_work=has_work,
            window_ms=lambda: 0,
        )
        pump.wake()
        await asyncio.wait_for(first_started.wait(), timeout=1)

        # 第一轮还在跑时来 5 次唤醒：只应合并成下一轮，而不是 5 轮。
        for _ in range(5):
            pending += 1
            pump.wake()
        release_first.set()

        await _wait_until(lambda: len(turns) == 2 and not pump.busy)
        await asyncio.sleep(0.05)
        assert len(turns) == 2
        assert pump.handle is None
        await pump.close()

    asyncio.run(scenario())


def test_batch_window_delays_the_first_turn() -> None:
    async def scenario() -> None:
        loop = asyncio.get_running_loop()
        pending = 3
        claimed: list[int] = []
        started_at: list[float] = []

        async def run_turn(_handle: TurnHandle) -> bool:
            nonlocal pending
            started_at.append(loop.time())
            claimed.append(pending)
            pending = 0
            return True

        async def has_work() -> bool:
            return pending > 0

        pump = SessionPump(
            "session-window",
            run_turn=run_turn,
            has_work=has_work,
            window_ms=lambda: 100,
        )
        first_wake = loop.time()
        pump.wake()
        await asyncio.sleep(0.02)
        pump.wake()
        await asyncio.sleep(0.02)
        pump.wake()
        # 窗口内一次都还没开始。
        assert started_at == []

        await _wait_until(lambda: len(started_at) == 1)
        assert claimed == [3]
        assert started_at[0] - first_wake >= 0.09
        await asyncio.sleep(0.03)
        assert len(started_at) == 1
        await pump.close()

    asyncio.run(scenario())


def test_worker_slots_cap_and_release_by_priority() -> None:
    async def scenario() -> None:
        capacity = 1
        slots = WorkerSlots(lambda: capacity)
        assert (slots.in_use, slots.waiting) == (0, 0)

        # 先占住唯一一个闸位。
        await slots.acquire(5)
        assert slots.in_use == 1

        acquired: list[str] = []
        release_a = asyncio.Event()
        release_b = asyncio.Event()

        async def wait_for_slot(name: str, priority: int, release: asyncio.Event) -> None:
            await slots.acquire(priority)
            acquired.append(name)
            await release.wait()
            slots.release()

        low = asyncio.create_task(wait_for_slot("b", 1, release_b))
        await _wait_until(lambda: slots.waiting == 1)
        high = asyncio.create_task(wait_for_slot("a", 0, release_a))
        await _wait_until(lambda: slots.waiting == 2)
        assert acquired == []

        # 容量热生效：没有 release 也能放行排队者，且 priority 0 先走。
        capacity = 2
        await _wait_until(lambda: len(acquired) == 1)
        assert acquired == ["a"]
        assert (slots.in_use, slots.waiting) == (2, 1)

        # 释放手头这个闸位后，priority 1 的等待者才拿到。
        slots.release()
        await _wait_until(lambda: len(acquired) == 2)
        assert acquired == ["a", "b"]
        assert (slots.in_use, slots.waiting) == (2, 0)

        release_a.set()
        release_b.set()
        await asyncio.gather(high, low)
        await _wait_until(lambda: slots.in_use == 0)

        # 取消等待者：从等待堆里移除自己，不占闸、不改容量。
        capacity = 1
        await slots.acquire(0)
        cancelled = asyncio.create_task(slots.acquire(0))
        await _wait_until(lambda: slots.waiting == 1)
        cancelled.cancel()
        with contextlib.suppress(asyncio.CancelledError):
            await cancelled
        assert (slots.in_use, slots.waiting) == (1, 0)
        slots.release()
        assert (slots.in_use, slots.waiting) == (0, 0)

    asyncio.run(scenario())


def test_interrupt_sets_reason_aborts_then_cancels_after_grace(monkeypatch) -> None:
    monkeypatch.setattr(pump_module, "INTERRUPT_GRACE_SECONDS", 0.1)

    async def scenario() -> None:
        loop = asyncio.get_running_loop()
        pending = 1
        events: list[tuple[str, float]] = []
        handles: list[TurnHandle] = []
        started = asyncio.Event()

        async def run_turn(handle: TurnHandle) -> bool:
            nonlocal pending
            pending = 0
            handles.append(handle)
            events.append(("turn-start", loop.time()))
            started.set()
            try:
                # 模拟卡在工具里、不主动看 cancel_event 的一轮。
                await asyncio.sleep(10)
            except asyncio.CancelledError:
                events.append(("turn-cancelled", loop.time()))
                raise
            return True

        async def abort() -> None:
            events.append(("abort", loop.time()))

        async def has_work() -> bool:
            return pending > 0

        pump = SessionPump(
            "session-interrupt",
            run_turn=run_turn,
            has_work=has_work,
            window_ms=lambda: 0,
        )
        pump.wake()
        await asyncio.wait_for(started.wait(), timeout=1)
        handle = pump.handle
        assert handle is not None

        began = time.monotonic()
        interrupted = await pump.interrupt("cancelled", abort=abort)
        elapsed = time.monotonic() - began

        assert interrupted is True
        assert handle.cancel_reason == "cancelled"
        assert handle.cancel_event.is_set()
        # 顺序：置原因 → 第一次撤挂起 → 宽限到期 → 取消本轮 → 再撤一次。
        assert [name for name, _ in events] == [
            "turn-start",
            "abort",
            "turn-cancelled",
            "abort",
        ]
        assert events[2][1] - events[1][1] >= 0.09
        assert elapsed >= 0.09

        await _wait_until(lambda: pump.handle is None and not pump.busy)
        # 没有在跑的一轮：撤一次挂起，返回 False。
        assert await pump.interrupt("interrupted", abort=abort) is False
        assert [name for name, _ in events][-1] == "abort"
        assert len(handles) == 1
        await pump.close()

    asyncio.run(scenario())


def test_not_started_turn_backs_off_and_close_stops_loop(monkeypatch) -> None:
    monkeypatch.setattr(pump_module, "RETRY_DELAY_SECONDS", 0.05)

    async def scenario() -> None:
        loop = asyncio.get_running_loop()
        calls: list[float] = []

        async def run_turn(_handle: TurnHandle) -> bool:
            calls.append(loop.time())
            return False

        async def has_work() -> bool:
            return True

        pump = SessionPump(
            "session-retry",
            run_turn=run_turn,
            has_work=has_work,
            window_ms=lambda: 0,
        )
        pump.wake()
        await _wait_until(lambda: len(calls) >= 3)
        # 没启动的一轮之间隔了退避时间。
        assert calls[1] - calls[0] >= 0.04
        assert calls[2] - calls[1] >= 0.04

        await pump.close()
        stopped_at = len(calls)
        assert pump.busy is False
        assert pump.handle is None
        await asyncio.sleep(0.15)
        assert len(calls) == stopped_at

    asyncio.run(scenario())
