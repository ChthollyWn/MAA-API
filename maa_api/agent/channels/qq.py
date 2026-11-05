"""QQ 机器人适配器（spec §4.2、§4.5「适配器」）。

官方文档（查阅日期 2026-11-05）：

- 获取 access_token（``POST https://api.bot.qq.com/app/getAppAccessToken``）：
  https://bot.q.qq.com/wiki/develop/api-v2/dev-prepare/access-token.html
- 消息收发（单聊 ``/v2/users/{openid}/messages``、群聊
  ``/v2/groups/{group_openid}/messages``、频道
  ``/channels/{channel_id}/messages``）：
  https://bot.q.qq.com/wiki/develop/api-v2/server-inter/message/overview.html
- WebSocket 网关与 ``qq-botpy`` SDK：
  https://bot.q.qq.com/wiki/develop/api/
- 签名：Ed25519。Bot Secret 反复翻倍到 32 字节作为 seed；签名体是
  ``timestamp + body``；请求头 ``X-Signature-Ed25519`` /
  ``X-Signature-Timestamp``：
  https://bot.q.qq.com/wiki/develop/api-v2/dev-prepare/interface-framework/sign.html

实现要点：

- 接收用 ``qq-botpy`` 的 WebSocket 网关（SDK 缺包时 ``start`` 只记「未安装」）。
- 发送直接走 HTTP：先换 access_token（缓存到过期前 60 秒），再按 ``chat_id``
  前缀选单聊/群聊/频道接口。入站转换会把场景编进 ``external_chat_id``：
  ``user:<openid>`` / ``group:<group_openid>`` / ``channel:<channel_id>``，
  发件箱把 ``external_chat_id`` 原样传回 ``send``，因此两边一致。
- sandbox：``channels.qq_sandbox=true`` 时用
  ``https://sandbox.api.sgroup.qq.com``。
"""

from __future__ import annotations

import asyncio
import inspect
import logging
import threading
import time
from collections.abc import Mapping
from typing import Any
from urllib.parse import quote

import httpx
from cryptography.exceptions import InvalidSignature
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey
from cryptography.hazmat.primitives.serialization import Encoding, PublicFormat

from maa_api.agent.channels.adapter import (
    BaseAdapter,
    Inbound,
    InboundHandler,
    config_bool,
    field_value,
    import_sdk,
    request_json,
)
from maa_api.agent.channels.errors import OutboxFatal, OutboxRetry
from maa_api.domain.enums import ChannelPlatform

__all__ = [
    "QQ_API_BASE",
    "QQ_RATE_LIMIT_CODE",
    "QQ_SANDBOX_API_BASE",
    "QQ_SIGNATURE_HEADER",
    "QQ_TIMESTAMP_HEADER",
    "QQ_TOKEN_URL",
    "QQAdapter",
    "inbound_from_qq_message",
    "qq_ed25519_public_key",
    "verify_qq_signature",
]

logger = logging.getLogger(__name__)

#: 当前官方 token 地址（2026-09 文档更新后为 api.bot.qq.com）。
#: 文档：https://bot.q.qq.com/wiki/develop/api-v2/dev-prepare/access-token.html
#: （查阅日期 2026-11-05）。
QQ_TOKEN_URL = "https://api.bot.qq.com/app/getAppAccessToken"
#: 正式/沙箱 API 根；文档见模块 docstring 的消息收发链接（查阅日期 2026-11-05）。
QQ_API_BASE = "https://api.sgroup.qq.com"
QQ_SANDBOX_API_BASE = "https://sandbox.api.sgroup.qq.com"

#: Ed25519 签名头（spec §4.5）。
#: 文档：https://bot.q.qq.com/wiki/develop/api-v2/dev-prepare/interface-framework/sign.html
#: （查阅日期 2026-11-05）。
QQ_SIGNATURE_HEADER = "X-Signature-Ed25519"
QQ_TIMESTAMP_HEADER = "X-Signature-Timestamp"

#: token 提前 60 秒刷新；100001 是 QQ 的限流业务码。
QQ_TOKEN_MARGIN_SECONDS = 60
QQ_RATE_LIMIT_CODE = 100001


def _qq_seed(bot_secret: str) -> bytes:
    """官方规则：secret 反复翻倍到 32 字节，取前 32 字节作 Ed25519 seed。"""
    if not bot_secret:
        raise ValueError("QQ bot secret 不能为空")
    seed = bot_secret.encode("utf-8")
    while len(seed) < 32:
        seed = (seed * 2)[:32]
    return seed[:32]


def qq_ed25519_public_key(bot_secret: str) -> bytes:
    """由 Bot Secret 推导 Ed25519 公钥（32 字节 raw），用于人工核对。"""
    private = Ed25519PrivateKey.from_private_bytes(_qq_seed(bot_secret))
    return private.public_key().public_bytes(Encoding.Raw, PublicFormat.Raw)


def verify_qq_signature(
    signature: str, timestamp: str, body: str | bytes, bot_secret: str
) -> bool:
    """校验 ``X-Signature-Ed25519``：Ed25519(signature, timestamp + body)。"""
    if not signature or not timestamp or not bot_secret:
        return False
    if len(signature) != 128:
        return False
    try:
        raw_signature = bytes.fromhex(signature)
    except ValueError:
        return False
    # 与官方 Go 示例一致：拒绝非规范的高位（sig[63] & 224）。
    if len(raw_signature) != 64 or raw_signature[63] & 224:
        return False
    raw_body = body.encode("utf-8") if isinstance(body, str) else bytes(body)
    public_key = Ed25519PrivateKey.from_private_bytes(_qq_seed(bot_secret)).public_key()
    try:
        public_key.verify(raw_signature, timestamp.encode("utf-8") + raw_body)
    except InvalidSignature:
        return False
    return True


def inbound_from_qq_message(message: Any) -> Inbound | None:
    """把 botpy 的 ``Message`` 对象（或同形状 dict）转成 :class:`Inbound`。"""
    content = field_value(message, "content")
    if not isinstance(content, str) or not content.strip():
        return None
    author = field_value(message, "author") or {}
    message_id = field_value(message, "id")
    channel_id = field_value(message, "channel_id")
    group_openid = field_value(message, "group_openid")
    user_openid = field_value(author, "user_openid") or field_value(
        author, "member_openid"
    )
    user_id = field_value(author, "id") or user_openid
    if channel_id:
        chat_id = f"channel:{channel_id}"
    elif group_openid:
        chat_id = f"group:{group_openid}"
    elif user_openid or user_id:
        chat_id = f"user:{user_openid or user_id}"
    else:
        return None
    if not user_id:
        return None
    return Inbound(
        platform=ChannelPlatform.QQ,
        external_user_id=str(user_id),
        external_chat_id=chat_id,
        text=content.strip(),
        message_id=str(message_id or ""),
    )


class QQAdapter(BaseAdapter):
    """``qq-botpy`` 网关接收 + HTTP 发送。"""

    platform = ChannelPlatform.QQ

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

    @property
    def api_base(self) -> str:
        sandbox = config_bool(self.setting("qq_sandbox", False))
        return QQ_SANDBOX_API_BASE if sandbox else QQ_API_BASE

    async def start(self) -> None:
        if self._running:
            return
        if not self._start_allowed(required=("qq_app_id", "qq_app_secret")):
            return
        botpy = import_sdk(self.platform, "botpy")
        if botpy is None:
            return
        self._loop = asyncio.get_running_loop()
        adapter = self

        try:
            intents = botpy.Intents.none()
        except AttributeError:  # pragma: no cover - 兼容旧版 botpy
            intents = botpy.Intents()
        for name in (
            "public_messages",
            "direct_message",
            "public_guild_messages",
            "group_at_messages",
        ):
            if hasattr(intents, name):
                setattr(intents, name, True)

        class _Client(botpy.Client):
            async def on_ready(self) -> None:  # pragma: no cover - 需真实网关
                logger.info("QQ 机器人网关已连接")

            async def on_at_message_create(self, message: Any) -> None:
                adapter._submit_message(message)

            async def on_message_create(self, message: Any) -> None:
                adapter._submit_message(message)

            async def on_group_at_message_create(self, message: Any) -> None:
                adapter._submit_message(message)

            async def on_c2c_message_create(self, message: Any) -> None:
                adapter._submit_message(message)

        try:
            client = _Client(intents=intents)
        except Exception:  # noqa: BLE001 - SDK 初始化失败不影响其他平台
            logger.exception("QQ 机器人客户端初始化失败，不启动")
            return
        self._running = True
        self._thread = threading.Thread(
            target=self._run_bot, args=(client,), name="qq-botpy", daemon=True
        )
        self._thread.start()
        logger.info("QQ 机器人网关已启动")

    def _submit_message(self, message: Any) -> None:
        inbound = inbound_from_qq_message(message)
        if inbound is None or self._loop is None:
            return
        asyncio.run_coroutine_threadsafe(self._notify(inbound), self._loop)

    def _run_bot(self, client: Any) -> None:
        app_id = self._credential("qq_app_id")
        app_secret = self._credential("qq_app_secret")
        try:
            params = inspect.signature(client.run).parameters
            if "secret" in params:
                client.run(appid=app_id, secret=app_secret)
            elif "token" in params:
                client.run(appid=app_id, token=app_secret)
            else:  # pragma: no cover - 位置参数兜底
                client.run(app_id, app_secret)
        except Exception:  # noqa: BLE001 - 后台线程不能把异常抛给主进程
            if self._running:
                logger.exception("QQ 机器人网关异常退出")
        finally:
            self._running = False

    async def stop(self) -> None:
        # qq-botpy 没有公开的 stop；线程是 daemon，进程退出时结束。
        if self._running:
            logger.info("QQ 机器人标记停止（SDK 无公开 stop 接口，线程随进程退出）")
        await super().stop()

    async def send(self, chat_id: str, text: str) -> dict[str, Any]:
        token = await self._access_token()
        url, body = self._message_request(chat_id, text)
        _, payload = await request_json(
            self._client,
            "POST",
            url,
            platform=str(self.platform),
            headers={"Authorization": f"QQBot {token}"},
            json=body,
        )
        code = payload.get("code")
        if isinstance(code, int) and code != 0 and not payload.get("id"):
            message = f"QQ 发送失败：code={code} message={payload.get('message', '')}"
            if code == QQ_RATE_LIMIT_CODE:
                raise OutboxRetry(message)
            raise OutboxFatal(message)
        return {
            "platform": str(self.platform),
            "message_id": payload.get("id"),
            "timestamp": payload.get("timestamp"),
            "chat_id": chat_id,
        }

    def _message_request(
        self, chat_id: str, text: str
    ) -> tuple[str, dict[str, Any]]:
        target = chat_id.strip()
        if target.startswith("channel:"):
            channel_id = quote(target[len("channel:") :], safe="")
            return f"{self.api_base}/channels/{channel_id}/messages", {"content": text}
        if target.startswith("group:"):
            group_openid = quote(target[len("group:") :], safe="")
            return (
                f"{self.api_base}/v2/groups/{group_openid}/messages",
                {"content": text, "msg_type": 0},
            )
        if target.startswith("user:"):
            target = target[len("user:") :]
        openid = quote(target, safe="")
        return (
            f"{self.api_base}/v2/users/{openid}/messages",
            {"content": text, "msg_type": 0},
        )

    async def _access_token(self) -> str:
        if self._token and time.monotonic() < self._token_expires_at:
            return self._token
        app_id = self._credential("qq_app_id")
        app_secret = self._credential("qq_app_secret")
        if not app_id or not app_secret:
            raise OutboxFatal("QQ 未配置 app_id / app_secret")
        _, body = await request_json(
            self._client,
            "POST",
            QQ_TOKEN_URL,
            platform=str(self.platform),
            json={"appId": app_id, "clientSecret": app_secret},
        )
        token = body.get("access_token")
        if not isinstance(token, str) or not token:
            # 官方文档：token 接口失败时 HTTP 仍可能是 200，业务错误看 code。
            code = body.get("code")
            message = f"QQ 取 access_token 失败：code={code} message={body.get('message', '')}"
            if code == QQ_RATE_LIMIT_CODE:
                raise OutboxRetry(message)
            raise OutboxFatal(message)
        try:
            expires_in = int(body.get("expires_in") or 7200)
        except (TypeError, ValueError):
            expires_in = 7200
        self._token = token
        self._token_expires_at = time.monotonic() + max(
            expires_in - QQ_TOKEN_MARGIN_SECONDS, 0
        )
        return token
