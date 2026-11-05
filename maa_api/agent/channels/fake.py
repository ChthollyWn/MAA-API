"""FakeAdapter：测试与冒烟用的通道替身（spec §4.2、§5 T2）。

G1 的 ``tests/agent/test_outbox.py`` 里有一份私有 FakeAdapter；这个模块把它
固化成可复用实现，G2 之后的测试、G3 的 ChannelService 测试和 G5 的冒烟都从这里
导入：

- ``sent`` 记录每次成功的 ``(chat_id, text)``；
- ``retry_times`` / ``fatal`` 注入失败，交给 ``OutboxWorker`` 走重试或死信；
- ``inject(inbound)`` 把一条入站消息交给 ``on_inbound`` 回调，模拟平台推送。
"""

from __future__ import annotations

from collections.abc import Mapping
from typing import Any

from maa_api.agent.channels.adapter import Inbound, InboundHandler
from maa_api.agent.channels.errors import OutboxFatal, OutboxRetry
from maa_api.domain.enums import ChannelPlatform

__all__ = ["FakeAdapter"]


class FakeAdapter:
    """可编排的适配器：不联网，记录发送，按剧本抛发件箱异常。"""

    def __init__(
        self,
        platform: ChannelPlatform | str = ChannelPlatform.TELEGRAM,
        *,
        config: Mapping[str, Any] | None = None,
        on_inbound: InboundHandler | None = None,
        retry_times: int = 0,
        fatal: bool = False,
        receipt: Mapping[str, Any] | None = None,
    ) -> None:
        self.platform = ChannelPlatform(str(platform))
        self.config = dict(config or {})
        self.sent: list[tuple[str, str]] = []
        self.inbound: list[Inbound] = []
        self.retry_times = retry_times
        self.fatal = fatal
        self.receipt: dict[str, Any] = dict(receipt or {})
        self.started = 0
        self.stopped = 0
        self._running = False
        self._on_inbound = on_inbound

    @property
    def running(self) -> bool:
        return self._running

    async def start(self) -> None:
        self.started += 1
        self._running = True

    async def stop(self) -> None:
        self.stopped += 1
        self._running = False

    async def send(self, chat_id: str, text: str) -> dict[str, Any]:
        if self.retry_times > 0:
            self.retry_times -= 1
            raise OutboxRetry(f"FakeAdapter 注入的可重试失败（剩余 {self.retry_times} 次）")
        if self.fatal:
            raise OutboxFatal("FakeAdapter 注入的致命失败")
        self.sent.append((chat_id, text))
        return {"platform": str(self.platform), **self.receipt}

    async def inject(self, inbound: Inbound) -> None:
        """模拟平台推送一条入站消息；没注册回调时只记录。"""
        self.inbound.append(inbound)
        if self._on_inbound is not None:
            await self._on_inbound(inbound)
