"""Telegram Bot API 适配器（spec §4.2、§4.5「适配器」）。

官方文档（查阅日期 2026-11-05）：

- Bot API 总览与 ``getUpdates`` / ``sendMessage``：
  https://core.telegram.org/bots/api
- 长轮询 ``offset`` / ``timeout`` 语义见 ``getUpdates`` 一节。

实现要点：

- 接收走 ``getUpdates`` 长轮询，不要求公网回调；``offset`` 取上一条
  ``update_id + 1``，``timeout`` 是服务端挂起秒数。
- 发送走 ``sendMessage``，回执取 ``result.message_id``；失败按 §4.5 分类。
- 代理：``channels.telegram_proxy`` 为空时回落到 ``app.proxy``（spec §4.4）。
- ``httpx`` 已在基础依赖里，不需要额外 extra。
"""

from __future__ import annotations

import asyncio
import logging
from collections.abc import Mapping
from contextlib import suppress
from typing import Any

import httpx

from maa_api.agent.channels.adapter import (
    BaseAdapter,
    Inbound,
    InboundHandler,
    request_json,
)
from maa_api.agent.channels.errors import OutboxFatal
from maa_api.domain.enums import ChannelPlatform

__all__ = [
    "TELEGRAM_API_BASE",
    "TELEGRAM_DEFAULT_POLL_TIMEOUT",
    "TELEGRAM_GET_UPDATES_METHOD",
    "TELEGRAM_POLL_RETRY_SECONDS",
    "TELEGRAM_SEND_MESSAGE_METHOD",
    "TelegramAdapter",
    "inbound_from_update",
    "telegram_api_url",
]

logger = logging.getLogger(__name__)

#: Bot API 根地址；实际请求路径是 ``/bot<token>/<method>``。
#: 文档：https://core.telegram.org/bots/api（查阅日期 2026-11-05）。
TELEGRAM_API_BASE = "https://api.telegram.org"
TELEGRAM_GET_UPDATES_METHOD = "getUpdates"
TELEGRAM_SEND_MESSAGE_METHOD = "sendMessage"

#: getUpdates 的默认挂起秒数；轮询失败后的退避秒数。
TELEGRAM_DEFAULT_POLL_TIMEOUT = 25
TELEGRAM_POLL_RETRY_SECONDS = 3.0


def telegram_api_url(token: str, method: str) -> str:
    """拼 Telegram Bot API 方法 URL（token 只出现在 URL 里，不写日志）。"""
    return f"{TELEGRAM_API_BASE}/bot{token}/{method}"


def inbound_from_update(update: Mapping[str, Any]) -> Inbound | None:
    """把一条 Telegram update 转成 :class:`Inbound`；非文本消息返回 ``None``。"""
    message = update.get("message") or update.get("edited_message")
    if not isinstance(message, Mapping):
        return None
    text = message.get("text")
    chat = message.get("chat")
    sender = message.get("from")
    if not isinstance(text, str) or not text.strip():
        return None
    if not isinstance(chat, Mapping) or not isinstance(sender, Mapping):
        return None
    chat_id = chat.get("id")
    user_id = sender.get("id")
    if chat_id is None or user_id is None:
        return None
    return Inbound(
        platform=ChannelPlatform.TELEGRAM,
        external_user_id=str(user_id),
        external_chat_id=str(chat_id),
        text=text.strip(),
        message_id=str(message.get("message_id", "")),
    )


class TelegramAdapter(BaseAdapter):
    """长轮询接收 + ``sendMessage`` 发送。"""

    platform = ChannelPlatform.TELEGRAM

    def __init__(
        self,
        config: Mapping[str, Any],
        *,
        http_client: httpx.AsyncClient | None = None,
        on_inbound: InboundHandler | None = None,
    ) -> None:
        super().__init__(config, http_client=http_client, on_inbound=on_inbound)
        proxy = (
            str(self.setting("telegram_proxy", "") or "").strip()
            or str(self.setting("app.proxy", "") or "").strip()
            or str(self.setting("proxy", "") or "").strip()
        )
        self._proxy = proxy or None
        try:
            self._poll_timeout = max(
                0, int(self.setting("telegram_poll_timeout", TELEGRAM_DEFAULT_POLL_TIMEOUT))
            )
        except (TypeError, ValueError):
            self._poll_timeout = TELEGRAM_DEFAULT_POLL_TIMEOUT
        self._poll_task: asyncio.Task[None] | None = None

    async def start(self) -> None:
        if self._running:
            return
        if not self._start_allowed(required=("telegram_bot_token",)):
            return
        self._running = True
        self._poll_task = asyncio.create_task(
            self._poll_loop(), name="telegram-get-updates"
        )
        logger.info("Telegram 长轮询已启动")

    async def stop(self) -> None:
        task = self._poll_task
        self._poll_task = None
        if task is not None and not task.done():
            task.cancel()
            with suppress(asyncio.CancelledError):
                await task
        await super().stop()

    async def send(self, chat_id: str, text: str) -> dict[str, Any]:
        token = self._credential("telegram_bot_token")
        if not token:
            raise OutboxFatal("Telegram 未配置 bot token")
        _, body = await request_json(
            self._client,
            "POST",
            telegram_api_url(token, TELEGRAM_SEND_MESSAGE_METHOD),
            platform=str(self.platform),
            json={"chat_id": chat_id, "text": text},
        )
        if not body.get("ok"):
            raise OutboxFatal(
                f"Telegram sendMessage 返回 ok=false：{body.get('description', '')}"
            )
        result = body.get("result")
        result = result if isinstance(result, Mapping) else {}
        return {
            "platform": str(self.platform),
            "message_id": result.get("message_id"),
            "date": result.get("date"),
            "chat_id": chat_id,
        }

    async def poll_once(self, offset: int = 0) -> tuple[list[dict[str, Any]], int]:
        """拉取一批 update 并分发，返回 ``(updates, 下一 offset)``；测试可直接调用。"""
        token = self._credential("telegram_bot_token")
        if not token:
            raise OutboxFatal("Telegram 未配置 bot token")
        _, body = await request_json(
            self._client,
            "GET",
            telegram_api_url(token, TELEGRAM_GET_UPDATES_METHOD),
            platform=str(self.platform),
            params={"offset": offset, "timeout": self._poll_timeout},
            timeout=self._poll_timeout + 10,
        )
        if not body.get("ok"):
            raise OutboxFatal(
                f"Telegram getUpdates 返回 ok=false：{body.get('description', '')}"
            )
        updates = body.get("result")
        if not isinstance(updates, list):
            raise OutboxFatal("Telegram getUpdates result 不是数组")
        next_offset = offset
        for update in updates:
            if not isinstance(update, Mapping):
                continue
            update_id = update.get("update_id")
            if isinstance(update_id, int):
                next_offset = max(next_offset, update_id + 1)
            inbound = inbound_from_update(update)
            if inbound is not None:
                await self._notify(inbound)
        return list(updates), next_offset

    async def _poll_loop(self) -> None:
        offset = 0
        while self._running:
            try:
                _, offset = await self.poll_once(offset)
            except asyncio.CancelledError:
                raise
            except Exception as exc:  # noqa: BLE001 - 长轮询必须保活
                logger.warning(
                    "Telegram 长轮询失败，%s 秒后重试：%s",
                    TELEGRAM_POLL_RETRY_SECONDS,
                    exc,
                )
                await asyncio.sleep(TELEGRAM_POLL_RETRY_SECONDS)
