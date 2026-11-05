"""钉钉适配器（spec §4.2、§4.5「适配器」）。

官方文档（查阅日期 2026-11-05）：

- Stream 模式总览：
  https://opensource.dingtalk.com/developerpedia/docs/learn/stream/overview
- Stream 协议与回调字段（``/v1.0/im/bot/messages/get``）：
  https://open-dingtalk.github.io/developerpedia/docs/learn/stream/protocol/
- 机器人发送单聊消息（``oToMessages/batchSend``）与群聊消息
  （``groupMessages/send``）：
  https://open.dingtalk.com/document/orgapp/the-robot-sends-a-group-message
- 企业内部应用 access_token：
  https://open.dingtalk.com/document/orgapp/obtain-orgapp-token

实现要点：

- 接收用 ``dingtalk-stream`` 的 WebSocket 长连接（无需公网回调），SDK 缺包时
  ``start`` 只记「未安装」并返回。
- 发送先用 client_id/client_secret 换 access_token（缓存到过期前 60 秒），再按
  ``chat_id`` 选单聊或群聊接口。入站转换把场景编进 ``external_chat_id``：
  ``group:<conversationId>`` / ``user:<senderStaffId>``；兼容裸 ``cid`` 视为群。
- 签名：钉钉回调通用 ``timestamp + "\\n" + client_secret`` 的 HMAC-SHA256、
  Base64 编码，头为 ``timestamp`` / ``sign``；Stream 长连接本身由
  client_id/client_secret 建立，回调验签函数供 HTTP 回调与测试固定样例使用。
- 失败分类遵循 §4.5：网络错误/429/5xx → OutboxRetry，其余 4xx → OutboxFatal。
"""

from __future__ import annotations

import asyncio
import base64
import hashlib
import hmac
import json
import logging
import threading
import time
from collections.abc import Mapping
from typing import Any

import httpx

from maa_api.agent.channels.adapter import (
    BaseAdapter,
    Inbound,
    InboundHandler,
    field_value,
    import_sdk,
    request_json,
)
from maa_api.agent.channels.errors import OutboxFatal
from maa_api.domain.enums import ChannelPlatform

__all__ = [
    "DINGTALK_ACCESS_TOKEN_HEADER",
    "DINGTALK_ACCESS_TOKEN_PATH",
    "DINGTALK_API_BASE",
    "DINGTALK_MSG_KEY",
    "DINGTALK_SEND_GROUP_PATH",
    "DINGTALK_SEND_USER_PATH",
    "DingTalkAdapter",
    "inbound_from_dingtalk_message",
    "verify_dingtalk_signature",
]

logger = logging.getLogger(__name__)

#: 发送与 token 接口都走 api.dingtalk.com。
#: 文档：https://open.dingtalk.com/document/orgapp/the-robot-sends-a-group-message
#: （查阅日期 2026-11-05）。
DINGTALK_API_BASE = "https://api.dingtalk.com"
DINGTALK_ACCESS_TOKEN_PATH = "/v1.0/oauth2/accessToken"
DINGTALK_SEND_USER_PATH = "/v1.0/robot/oToMessages/batchSend"
DINGTALK_SEND_GROUP_PATH = "/v1.0/robot/groupMessages/send"
#: access_token 放这个请求头（官方 v1.0 机器人接口）。
DINGTALK_ACCESS_TOKEN_HEADER = "x-acs-dingtalk-access-token"
#: 机器人文本消息的 msgKey / msgParam 固定形状。
DINGTALK_MSG_KEY = "sampleText"
#: token 提前 60 秒刷新，避免边界上过期（官方有效期 7200 秒）。
DINGTALK_TOKEN_MARGIN_SECONDS = 60


def verify_dingtalk_signature(
    timestamp: str, signature: str, client_secret: str
) -> bool:
    """校验钉钉回调的 ``timestamp`` / ``sign``（HMAC-SHA256 + Base64）。"""
    if not timestamp or not signature or not client_secret:
        return False
    string_to_sign = f"{timestamp}\n{client_secret}".encode("utf-8")
    expected = base64.b64encode(
        hmac.new(client_secret.encode("utf-8"), string_to_sign, hashlib.sha256).digest()
    ).decode("ascii")
    return hmac.compare_digest(expected, signature)


def inbound_from_dingtalk_message(data: Any) -> Inbound | None:
    """把 Stream 回调里的 ``data``（JSON 字符串或 dict）转成 :class:`Inbound`。"""
    payload = data
    if isinstance(payload, str):
        try:
            payload = json.loads(payload)
        except ValueError:
            return None
    if not isinstance(payload, Mapping):
        return None
    text_field = field_value(payload, "text")
    text = field_value(text_field, "content")
    if not isinstance(text, str) or not text.strip():
        return None
    conversation_id = field_value(payload, "conversationId")
    conversation_type = str(field_value(payload, "conversationType", "") or "")
    staff_id = field_value(payload, "senderStaffId") or field_value(
        payload, "senderId"
    )
    # 群聊用 conversationId，单聊用 senderStaffId；把场景编进 external_chat_id，
    # 发件箱原样传回 send 时才能选对 oToMessages / groupMessages 接口。
    if conversation_type == "2" and conversation_id:
        chat_id = f"group:{conversation_id}"
    elif staff_id:
        chat_id = f"user:{staff_id}"
    elif conversation_id:
        chat_id = str(conversation_id)
    else:
        return None
    user_id = staff_id or conversation_id
    if not user_id:
        return None
    return Inbound(
        platform=ChannelPlatform.DINGTALK,
        external_user_id=str(user_id),
        external_chat_id=chat_id,
        text=text.strip(),
        message_id=str(field_value(payload, "msgId", "")),
    )


def _is_group_target(chat_id: str) -> bool:
    target = chat_id.strip()
    return target.startswith("cid") or target.startswith("group:")


def _target_id(chat_id: str) -> str:
    target = chat_id.strip()
    for prefix in ("group:", "user:"):
        if target.startswith(prefix):
            return target[len(prefix) :]
    return target


class DingTalkAdapter(BaseAdapter):
    """Stream 长连接接收 + access_token 调用机器人发送接口。"""

    platform = ChannelPlatform.DINGTALK

    def __init__(
        self,
        config: Mapping[str, Any],
        *,
        http_client: httpx.AsyncClient | None = None,
        on_inbound: InboundHandler | None = None,
    ) -> None:
        super().__init__(config, http_client=http_client, on_inbound=on_inbound)
        self._token: str | None = None
        self._token_expires_at = 0.0
        self._thread: threading.Thread | None = None
        self._loop: asyncio.AbstractEventLoop | None = None

    async def start(self) -> None:
        if self._running:
            return
        if not self._start_allowed(
            required=("dingtalk_client_id", "dingtalk_client_secret")
        ):
            return
        sdk = import_sdk(self.platform, "dingtalk_stream")
        if sdk is None:
            return
        self._loop = asyncio.get_running_loop()
        adapter = self

        class _Handler(sdk.ChatbotHandler):
            async def process(self, callback: Any) -> tuple[str, str]:
                adapter._submit_message(field_value(callback, "data"))
                return sdk.AckMessage.STATUS_OK, "OK"

        try:
            credential = sdk.Credential(
                self._credential("dingtalk_client_id"),
                self._credential("dingtalk_client_secret"),
            )
            stream_client = sdk.DingTalkStreamClient(credential)
            stream_client.register_callback_handler(
                sdk.chatbot.ChatbotMessage.TOPIC, _Handler()
            )
        except Exception:  # noqa: BLE001 - SDK 初始化失败不影响其他平台
            logger.exception("钉钉 Stream 初始化失败，不启动")
            return
        self._running = True
        self._thread = threading.Thread(
            target=self._run_stream,
            args=(stream_client,),
            name="dingtalk-stream",
            daemon=True,
        )
        self._thread.start()
        logger.info("钉钉 Stream 长连接已启动")

    def _submit_message(self, data: Any) -> None:
        inbound = inbound_from_dingtalk_message(data)
        if inbound is None or self._loop is None:
            return
        asyncio.run_coroutine_threadsafe(self._notify(inbound), self._loop)

    def _run_stream(self, stream_client: Any) -> None:
        try:
            stream_client.start_forever()
        except Exception:  # noqa: BLE001 - 后台线程不能把异常抛给主进程
            if self._running:
                logger.exception("钉钉 Stream 长连接异常退出")
        finally:
            self._running = False

    async def stop(self) -> None:
        # dingtalk-stream 没有公开的 stop；线程是 daemon，进程退出时结束。
        if self._running:
            logger.info("钉钉 Stream 标记停止（SDK 无公开 stop 接口，线程随进程退出）")
        await super().stop()

    async def send(self, chat_id: str, text: str) -> dict[str, Any]:
        token = await self._access_token()
        target = _target_id(chat_id)
        robot_code = (
            self._credential("dingtalk_robot_code")
            or self._credential("dingtalk_client_id")
        )
        if not target or not robot_code:
            raise OutboxFatal("钉钉未配置 robot_code 或目标 ID")
        msg_param = json.dumps({"content": text}, ensure_ascii=False)
        if _is_group_target(chat_id):
            url = f"{DINGTALK_API_BASE}{DINGTALK_SEND_GROUP_PATH}"
            body: dict[str, Any] = {
                "robotCode": robot_code,
                "openConversationId": target,
                "msgKey": DINGTALK_MSG_KEY,
                "msgParam": msg_param,
            }
        else:
            url = f"{DINGTALK_API_BASE}{DINGTALK_SEND_USER_PATH}"
            body = {
                "robotCode": robot_code,
                "userIds": [target],
                "msgKey": DINGTALK_MSG_KEY,
                "msgParam": msg_param,
            }
        _, payload = await request_json(
            self._client,
            "POST",
            url,
            platform=str(self.platform),
            headers={DINGTALK_ACCESS_TOKEN_HEADER: token},
            json=body,
        )
        return {
            "platform": str(self.platform),
            "process_query_key": payload.get("processQueryKey"),
            "chat_id": target,
        }

    async def _access_token(self) -> str:
        if self._token and time.monotonic() < self._token_expires_at:
            return self._token
        client_id = self._credential("dingtalk_client_id")
        client_secret = self._credential("dingtalk_client_secret")
        if not client_id or not client_secret:
            raise OutboxFatal("钉钉未配置 client_id / client_secret")
        _, body = await request_json(
            self._client,
            "POST",
            f"{DINGTALK_API_BASE}{DINGTALK_ACCESS_TOKEN_PATH}",
            platform=str(self.platform),
            json={"appKey": client_id, "appSecret": client_secret},
        )
        token = body.get("accessToken") or body.get("access_token")
        if not isinstance(token, str) or not token:
            raise OutboxFatal(
                f"钉钉取 access_token 失败：code={body.get('code')} "
                f"message={body.get('message', '')}"
            )
        try:
            expires_in = int(body.get("expireIn") or body.get("expire_in") or 7200)
        except (TypeError, ValueError):
            expires_in = 7200
        self._token = token
        self._token_expires_at = time.monotonic() + max(
            expires_in - DINGTALK_TOKEN_MARGIN_SECONDS, 0
        )
        return token
