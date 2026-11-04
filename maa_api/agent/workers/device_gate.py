"""进程内的设备门（M70 规格 §4.5.6）。

M70 还没有设备租约：写设备的工具（``device == "exclusive"``）在调用前先取这道门，
保证同一时刻只有一个工单在动设备。同一工单可重入（Worker 与其子代理共用工单 id），
别的工单一直等；不设超时、不落库。M71 用 ``DeviceLeaseService`` 与
``LeaseMiddleware`` 替换并删除本模块。
"""

from __future__ import annotations

import asyncio

__all__ = ["DeviceGate"]


class DeviceGate:
    """同一时刻只允许一个工单调用写设备工具的进程内门。

    ``acquire`` 在门被别的工单持有时一直等（``Condition.wait_for`` 可被取消：等待方
    被取消时不会改变持有者）；``release`` 只有持有者调用才生效，非持有者无操作。
    """

    def __init__(self) -> None:
        self._condition = asyncio.Condition()
        self._owner: str | None = None

    @property
    def owner(self) -> str | None:
        """当前持有门的工单 id；空闲时为 ``None``。"""
        return self._owner

    async def acquire(self, ticket_id: str) -> None:
        """取门；同一工单可重入，被别的工单持有时一直等。"""
        async with self._condition:
            await self._condition.wait_for(
                lambda: self._owner is None or self._owner == ticket_id
            )
            self._owner = ticket_id

    async def release(self, ticket_id: str) -> None:
        """释放门；不是持有者时什么都不做（工单结束时再释放一次是幂等的）。"""
        async with self._condition:
            if self._owner != ticket_id:
                return
            self._owner = None
            self._condition.notify_all()
