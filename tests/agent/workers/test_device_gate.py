"""M70 T7：进程内设备门（spec §4.5.6、§6 最后一行）。

同工单可重入；别的工单要等到释放；等待可取消且不改变持有者。
"""

from __future__ import annotations

import asyncio
import contextlib

from maa_api.agent.workers.device_gate import DeviceGate


def test_gate_reentrant_waits_and_cancellable() -> None:
    async def scenario() -> None:
        gate = DeviceGate()
        assert gate.owner is None

        # 同工单可重入：第二次 acquire 立即成功，不会把自己挡住。
        await gate.acquire("ticket-a")
        await asyncio.wait_for(gate.acquire("ticket-a"), timeout=1)
        assert gate.owner == "ticket-a"

        # 别的工单一直等到释放。
        waiter = asyncio.create_task(gate.acquire("ticket-b"))
        await asyncio.sleep(0)
        await asyncio.sleep(0)
        assert not waiter.done()
        assert gate.owner == "ticket-a"

        # 非持有者 release 无操作，等待方仍然等。
        await gate.release("ticket-b")
        assert gate.owner == "ticket-a"
        assert not waiter.done()

        # 持有者释放后等待方拿到门。
        await gate.release("ticket-a")
        await asyncio.wait_for(waiter, timeout=1)
        assert gate.owner == "ticket-b"

        # 等待可取消：取消不改持有者，门随后仍能正常交接。
        cancelled = asyncio.create_task(gate.acquire("ticket-c"))
        await asyncio.sleep(0)
        await asyncio.sleep(0)
        assert not cancelled.done()
        cancelled.cancel()
        with contextlib.suppress(asyncio.CancelledError):
            await cancelled
        assert gate.owner == "ticket-b"

        await gate.release("ticket-b")
        assert gate.owner is None
        await asyncio.wait_for(gate.acquire("ticket-c"), timeout=1)
        assert gate.owner == "ticket-c"

    asyncio.run(scenario())
