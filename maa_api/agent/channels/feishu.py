"""飞书（Lark）适配器（spec §4.2、§4.5「适配器」）。

官方文档（查阅日期 2026-11-05）：

- Python 服务端 SDK（PyPI ``lark-oapi``，锁定 1.7.5）：
  https://open.feishu.cn/document/uAjLw4CM/ukTMukTMukTM/server-side-sdk/python--sdk
- 长连接 ``lark.ws.Client``：无需公网 HTTP 回调即可接收 ``im.message.receive_v1``。
- 发送：``lark.Client`` 的 ``im.v1.message.create``，``receive_id_type="chat_id"``。
- 事件加密：EncryptKey AES-256-CBC；密钥是 ``sha256(encrypt_key)`` 的 32 字节，
  IV 取密文前 16 字节，明文 PKCS#7 填充。
- 事件签名：``sha256(timestamp + nonce + encrypt_key + body)``；请求头
  ``X-Lark-Signature`` / ``X-Lark-Request-Timestamp`` /
  ``X-Lark-Request-Nonce``。
- VerificationToken 比对：事件体 ``header.token``（v2）或顶层 ``token``（v1）。

签名/解密函数独立于适配器，既给 HTTP 回调（若未来新增）用，也让测试能用固定
样例覆盖，不需要真实飞书应用。日志与文档里不出现真实密钥。
"""

from __future__ import annotations

import asyncio
import base64
import hashlib
import hmac
import json
import logging
import threading
from collections.abc import Mapping
from typing import Any

import httpx
from cryptography.hazmat.primitives.ciphers import Cipher, algorithms, modes

from maa_api.agent.channels.adapter import (
    BaseAdapter,
    Inbound,
    InboundHandler,
    error_for_status,
    field_value,
    import_sdk,
)
from maa_api.agent.channels.errors import OutboxFatal, OutboxRetry
from maa_api.domain.enums import ChannelPlatform

__all__ = [
    "FEISHU_DEFAULT_DOMAIN",
    "FEISHU_NONCE_HEADER",
    "FEISHU_RETRY_CODES",
    "FEISHU_SIGNATURE_HEADER",
    "FEISHU_TIMESTAMP_HEADER",
    "FeishuAdapter",
    "FeishuDecryptError",
    "decrypt_feishu_event",
    "inbound_from_feishu_event",
    "verify_feishu_signature",
    "verify_feishu_token",
]

logger = logging.getLogger(__name__)

#: 默认开放平台域名；``channels.feishu_domain`` 可覆盖（自建/私有化）。
#: 文档：https://open.feishu.cn/document/uAjLw4CM/ukTMukTMukTM/server-side-sdk/python--sdk
#: （查阅日期 2026-11-05，SDK 版本 1.7.5）。
FEISHU_DEFAULT_DOMAIN = "https://open.feishu.cn"

#: HTTP 回调签名头（spec §4.5；长连接事件由 SDK 走可信内部通道，不带这三个头）。
#: 文档：飞书「事件订阅」EncryptKey 与签名一节（查阅日期 2026-11-05）。
FEISHU_SIGNATURE_HEADER = "X-Lark-Signature"
FEISHU_TIMESTAMP_HEADER = "X-Lark-Request-Timestamp"
FEISHU_NONCE_HEADER = "X-Lark-Request-Nonce"

#: 飞书限流错误码；命中时按可重试处理，其余业务错误按致命处理。
FEISHU_RETRY_CODES = frozenset({99991400})


class FeishuDecryptError(ValueError):
    """EncryptKey 解密失败：密钥不对、密文损坏或填充不合法。"""


def verify_feishu_signature(
    *,
    timestamp: str,
    nonce: str,
    body: str | bytes,
    encrypt_key: str,
    signature: str,
) -> bool:
    """校验 ``X-Lark-Signature``（常量时间比较）。"""
    if not timestamp or not nonce or not encrypt_key or not signature:
        return False
    raw = body.encode("utf-8") if isinstance(body, str) else bytes(body)
    expected = hashlib.sha256(
        timestamp.encode("utf-8")
        + nonce.encode("utf-8")
        + encrypt_key.encode("utf-8")
        + raw
    ).hexdigest()
    return hmac.compare_digest(expected, signature)


def verify_feishu_token(token: str | None, verification_token: str | None) -> bool:
    """比对事件里的 VerificationToken；未配置 token 时返回 ``False``。"""
    if not verification_token or not token:
        return False
    return hmac.compare_digest(str(token), str(verification_token))


def decrypt_feishu_event(encrypt: str, encrypt_key: str) -> dict[str, Any]:
    """按飞书 EncryptKey 规则解密事件，返回 JSON 对象。

    AES 密钥是 ``sha256(encrypt_key)``；密文 base64 解码后前 16 字节是 IV，
    其余是 PKCS#7 填充的密文。失败抛 :class:`FeishuDecryptError`。
    """
    if not encrypt or not encrypt_key:
        raise FeishuDecryptError("飞书事件缺少 encrypt 或 EncryptKey")
    try:
        data = base64.b64decode(encrypt, validate=True)
        if len(data) <= 16:
            raise ValueError("ciphertext too short")
        key = hashlib.sha256(encrypt_key.encode("utf-8")).digest()
        decryptor = Cipher(algorithms.AES(key), modes.CBC(data[:16])).decryptor()
        padded = decryptor.update(data[16:]) + decryptor.finalize()
        pad = padded[-1]
        if pad < 1 or pad > 16 or padded[-pad:] != bytes([pad]) * pad:
            raise ValueError("invalid padding")
        payload = json.loads(padded[:-pad].decode("utf-8"))
    except (ValueError, UnicodeDecodeError) as exc:
        raise FeishuDecryptError(f"飞书事件解密失败：{type(exc).__name__}") from exc
    if not isinstance(payload, dict):
        raise FeishuDecryptError("飞书事件解密结果不是 JSON 对象")
    return payload


def _message_text(content: Any) -> str | None:
    """``message.content`` 是 JSON 字符串（或已被 SDK 解析成 dict）。"""
    if isinstance(content, Mapping):
        data = content
    elif isinstance(content, str):
        try:
            parsed = json.loads(content)
        except ValueError:
            return content.strip() or None
        data = parsed if isinstance(parsed, Mapping) else {}
    else:
        return None
    text = data.get("text")
    return text.strip() if isinstance(text, str) and text.strip() else None


def inbound_from_feishu_event(payload: Any) -> Inbound | None:
    """把 ``im.message.receive_v1`` 事件转成 :class:`Inbound`。"""
    event = field_value(payload, "event") or payload
    message = field_value(event, "message")
    sender = field_value(event, "sender")
    if message is None or sender is None:
        return None
    message = message or {}
    sender = sender or {}
    text = _message_text(field_value(message, "content"))
    if text is None:
        return None
    sender_id = field_value(sender, "sender_id") or {}
    open_id = field_value(sender_id, "open_id") or field_value(sender, "open_id")
    chat_id = field_value(message, "chat_id")
    if not open_id or not chat_id:
        return None
    return Inbound(
        platform=ChannelPlatform.FEISHU,
        external_user_id=str(open_id),
        external_chat_id=str(chat_id),
        text=text,
        message_id=str(field_value(message, "message_id", "")),
    )


def _feishu_error(platform: str, response: Any) -> Exception:
    """把 lark SDK 的失败响应分类成发件箱异常。"""
    raw = field_value(response, "raw")
    status = field_value(raw, "status_code")
    code = field_value(response, "code")
    message = str(field_value(response, "msg", "") or "")
    if isinstance(status, int) and status >= 400:
        return error_for_status(platform, status, message)
    text = f"飞书发送失败：code={code} {message}".strip()
    if code in FEISHU_RETRY_CODES:
        return OutboxRetry(text)
    return OutboxFatal(text)


class FeishuAdapter(BaseAdapter):
    """``lark-oapi`` 长连接接收 + ``im.v1.message.create`` 发送。"""

    platform = ChannelPlatform.FEISHU

    def __init__(
        self,
        config: Mapping[str, Any],
        *,
        http_client: httpx.AsyncClient | None = None,
        on_inbound: InboundHandler | None = None,
        sdk_client: Any | None = None,
    ) -> None:
        super().__init__(config, http_client=http_client, on_inbound=on_inbound)
        #: 测试注入假 SDK 客户端；生产为 ``None``，发送时按需构造 ``lark.Client``。
        self._sdk_client = sdk_client
        self._thread: threading.Thread | None = None
        self._loop: asyncio.AbstractEventLoop | None = None

    async def start(self) -> None:
        if self._running:
            return
        if not self._start_allowed(
            required=("feishu_app_id", "feishu_app_secret")
        ):
            return
        lark = import_sdk(self.platform, "lark_oapi")
        if lark is None:
            return
        self._loop = asyncio.get_running_loop()
        try:
            dispatcher = (
                lark.EventDispatcherHandler.builder("", "")
                .register_p2_im_message_receive_v1(self._on_sdk_event)
                .build()
            )
            ws_client = lark.ws.Client(
                self._credential("feishu_app_id"),
                self._credential("feishu_app_secret"),
                event_handler=dispatcher,
                log_level=lark.LogLevel.INFO,
            )
        except Exception:  # noqa: BLE001 - SDK 初始化失败不影响其他平台
            logger.exception("飞书长连接初始化失败，不启动")
            return
        self._running = True
        self._thread = threading.Thread(
            target=self._run_ws, args=(ws_client,), name="feishu-ws", daemon=True
        )
        self._thread.start()
        logger.info("飞书长连接已启动")

    def _on_sdk_event(self, data: Any) -> None:
        """SDK 线程回调：把事件送回主事件循环，避免跨 loop 使用会话。"""
        inbound = inbound_from_feishu_event(data)
        if inbound is None or self._loop is None:
            return
        asyncio.run_coroutine_threadsafe(self._notify(inbound), self._loop)

    def _run_ws(self, ws_client: Any) -> None:
        try:
            ws_client.start()
        except Exception:  # noqa: BLE001 - 后台线程不能把异常抛给主进程
            if self._running:
                logger.exception("飞书长连接异常退出")
        finally:
            self._running = False

    async def stop(self) -> None:
        # lark-oapi 的 ws.Client 没有公开 stop；线程是 daemon，进程退出时结束。
        if self._running:
            logger.info("飞书长连接标记停止（SDK 无公开 stop 接口，线程随进程退出）")
        await super().stop()

    async def send(self, chat_id: str, text: str) -> dict[str, Any]:
        app_id = self._credential("feishu_app_id")
        app_secret = self._credential("feishu_app_secret")
        if not app_id or not app_secret:
            raise OutboxFatal("飞书未配置 app_id / app_secret")

        sdk_client = self._sdk_client
        if sdk_client is None:
            lark = import_sdk(self.platform, "lark_oapi")
            if lark is None:
                raise OutboxFatal("飞书 SDK 未安装（im-feishu extra）")
            sdk_client = self._build_api_client(lark, app_id, app_secret)

        api = import_sdk(self.platform, "lark_oapi.api.im.v1")
        if api is None:
            raise OutboxFatal("飞书 SDK 未安装（im-feishu extra）")
        request = (
            api.CreateMessageRequest.builder()
            .receive_id_type("chat_id")
            .request_body(
                api.CreateMessageRequestBody.builder()
                .receive_id(chat_id)
                .msg_type("text")
                .content(json.dumps({"text": text}, ensure_ascii=False))
                .build()
            )
            .build()
        )
        try:
            response = await asyncio.to_thread(
                sdk_client.im.v1.message.create, request
            )
        except Exception as exc:  # noqa: BLE001 - SDK 网络异常类型不稳定
            raise OutboxRetry(f"飞书发送网络错误：{type(exc).__name__}") from exc

        if not self._succeeded(response):
            raise _feishu_error(str(self.platform), response)
        data = field_value(response, "data") or {}
        return {
            "platform": str(self.platform),
            "message_id": field_value(data, "message_id"),
            "chat_id": chat_id,
        }

    def _build_api_client(self, lark: Any, app_id: str, app_secret: str) -> Any:
        builder = lark.Client.builder().app_id(app_id).app_secret(app_secret)
        domain = str(self.setting("feishu_domain", FEISHU_DEFAULT_DOMAIN) or "").strip()
        if domain and hasattr(builder, "domain"):
            builder = builder.domain(domain)
        return builder.build()

    @staticmethod
    def _succeeded(response: Any) -> bool:
        success = field_value(response, "success")
        if callable(success):
            return bool(success())
        if success is not None:
            return bool(success)
        return field_value(response, "code", 0) == 0
