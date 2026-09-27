"""Web Push 投递：VAPID 签名 + RFC 8291 加密 + 订阅失效处理（M14 Task 3）。

**推送是「即时层」，不是可靠层。** 它只在用户没开着页面时用于触达人工确认
（docs/09 §13.1 的三级递进），因此这里的失败一律不向上冒泡：确认本身照常落库、
照常等待、照常超时，推送发不出去只是少了一条通知，绝不能让确认流程本身失败。
调用方用 :meth:`WebPushService.notify_confirmation` 得到一份结果摘要，用来记日志。

失效处理遵循 Web Push 的既有约定：推送服务对已注销的订阅返回 ``404`` 或 ``410``，
此时把该行标记为失效（保留行，见 :mod:`maa_api.db.repositories.push`），后续发送
直接跳过。其它错误码只记录，不停用订阅 —— 一次 500 或超时是服务端抖动，停用会让
用户彻底收不到通知。
"""

from __future__ import annotations

import json
import logging
from dataclasses import dataclass, field
from typing import Any

import httpx

from maa_api.domain.errors import AppError, ErrorCode
from maa_api.services import webpush_crypto as crypto
from maa_api.services import webpush_vapid as vapid
from maa_api.services.push_subscription_service import PushSubscriptionService

__all__ = ["PushDeliveryResult", "WebPushService", "build_confirmation_payload"]

logger = logging.getLogger(__name__)

#: 推送服务对已注销订阅返回这两个状态码（RFC 8030 §7.3）。
_GONE_STATUS = frozenset({404, 410})

#: 明文上限的保守留白：通知体是短文本，截断而不是让它加密失败。
_TITLE_LIMIT = 120
_BODY_LIMIT = 400

#: 单次投递的超时。确认有时效（消耗类 10 分钟），卡在一次投递上没有意义。
_DEFAULT_TIMEOUT = 10.0


@dataclass
class PushDeliveryResult:
    """一次广播投递的摘要，用于日志与 smoke 断言。"""

    attempted: int = 0
    delivered: int = 0
    disabled: list[str] = field(default_factory=list)
    failed: int = 0
    skipped_reason: str | None = None

    @property
    def ok(self) -> bool:
        return self.failed == 0 and not self.skipped_reason


def _truncate(value: str, limit: int) -> str:
    text = " ".join((value or "").split())
    if len(text) <= limit:
        return text
    return text[: limit - 1] + "…"


def build_confirmation_payload(
    *,
    confirmation_id: str,
    title: str,
    summary: str,
    url: str,
    actions: bool = True,
) -> dict[str, Any]:
    """构造确认通知的载荷。

    按「Apple 卡片优先」设计（docs/09 §13.2）：完整信息写进 ``title`` 与 ``body``，
    ``tag`` 做同类合并，``data`` 承载深链与处置所需的 id。``actions`` 只是 Chromium
    上的渐进增强 —— WebKit 会静默忽略它，所以流程正确性不能以按钮为前提。

    ``title`` / ``body`` 只放操作名与原因，**不放** token、API Key、绝对路径等
    敏感内容：推送要经过第三方推送服务。
    """
    return {
        "title": _truncate(f"需要确认：{title}", _TITLE_LIMIT),
        "body": _truncate(summary or "Agent 请求执行一个需要人工确认的操作。", _BODY_LIMIT),
        "tag": f"confirm:{confirmation_id}",
        "data": {"confirmationId": confirmation_id, "url": url},
        "actions": (
            [
                {"action": "approve", "title": "准许"},
                {"action": "reject", "title": "拒绝"},
            ]
            if actions
            else []
        ),
    }


class WebPushService:
    """把通知投递到全部未失效的订阅。"""

    def __init__(
        self,
        subscriptions: PushSubscriptionService,
        *,
        http_client: Any = None,
        timeout: float = _DEFAULT_TIMEOUT,
        settings_provider=None,
    ) -> None:
        self._subscriptions = subscriptions
        self._http = http_client
        self._timeout = timeout
        self._settings_provider = settings_provider

    # -- 配置 ---------------------------------------------------------------

    def _settings(self):
        if self._settings_provider is not None:
            return self._settings_provider()
        from maa_api.settings import get_settings

        return get_settings()

    def _private_key(self):
        """读取并解析 VAPID 私钥；未配置或非法都返回 ``None``（不抛）。"""
        raw = (self._settings().notify.vapid_private_key or "").strip()
        if not raw:
            return None
        try:
            return vapid.load_vapid_private_key(raw)
        except ValueError:
            logger.warning("VAPID 私钥不可解析，Web Push 本轮跳过")
            return None

    def _subject(self) -> str:
        return self._settings().notify.vapid_subject

    # -- 发送 ---------------------------------------------------------------

    async def _request(self, method: str, url: str, **kwargs) -> Any:
        if self._http is not None:
            return await self._http.request(method, url, **kwargs)
        async with httpx.AsyncClient(timeout=self._timeout) as client:
            return await client.request(method, url, **kwargs)

    async def send_to_subscription(
        self, subscription: Any, payload: dict[str, Any]
    ) -> bool:
        """向单个订阅投递，返回是否成功。

        ``404`` / ``410`` 视为订阅已失效并标记；其它异常只记日志。
        """
        private_key = self._private_key()
        if private_key is None:
            raise AppError(
                ErrorCode.PUSH_NOT_CONFIGURED, "未配置可用的 VAPID 私钥"
            )

        body = json.dumps(payload, ensure_ascii=False, separators=(",", ":")).encode()
        if len(body) > crypto.MAX_PLAINTEXT_BYTES:
            # 通知体本就很短；真超了就截断 body 字段重试一次，而不是让加密失败。
            trimmed = dict(payload)
            trimmed["body"] = _truncate(str(payload.get("body", "")), 128)
            body = json.dumps(trimmed, ensure_ascii=False, separators=(",", ":")).encode()
            if len(body) > crypto.MAX_PLAINTEXT_BYTES:
                logger.warning("推送载荷超过 RFC 8291 上限，放弃投递")
                return False

        try:
            encrypted = crypto.encrypt_push_message(
                body,
                ua_public=crypto.b64url_decode(subscription.p256dh),
                auth_secret=crypto.b64url_decode(subscription.auth),
            )
        except crypto.PushEncryptionError as exc:
            # 订阅里存的是坏密钥（例如客户端 bug）：停用而不是每次重试都失败
            logger.warning("订阅 %s 的密钥无法用于加密：%s", subscription.id, exc)
            await self._subscriptions.mark_disabled(subscription.id)
            return False

        headers = {
            "Content-Encoding": "aes128gcm",
            "Content-Type": "application/octet-stream",
            "TTL": "600",
            "Authorization": vapid.build_vapid_header(
                private_key,
                endpoint=subscription.endpoint,
                subject=self._subject(),
            ),
        }
        response = await self._request(
            "POST",
            subscription.endpoint,
            content=encrypted.body,
            headers=headers,
        )
        status_code = int(getattr(response, "status_code", 0) or 0)
        if 200 <= status_code < 300:
            await self._subscriptions.mark_sent(subscription.id)
            return True
        if status_code in _GONE_STATUS:
            logger.info(
                "订阅 %s 已被推送服务注销（HTTP %s），标记失效",
                subscription.id,
                status_code,
            )
            await self._subscriptions.mark_disabled(subscription.id)
            return False
        logger.warning(
            "推送投递失败 HTTP %s（订阅 %s）：%s",
            status_code,
            subscription.id,
            str(getattr(response, "text", ""))[:200],
        )
        return False

    async def broadcast(self, payload: dict[str, Any]) -> PushDeliveryResult:
        """向全部未失效订阅投递，返回结果摘要。任何异常都不向上抛。"""
        result = PushDeliveryResult()
        if self._private_key() is None:
            result.skipped_reason = "vapid_not_configured"
            return result

        subscriptions = await self._subscriptions.active_subscriptions()
        if not subscriptions:
            result.skipped_reason = "no_subscription"
            return result

        for subscription in subscriptions:
            result.attempted += 1
            try:
                if await self.send_to_subscription(subscription, payload):
                    result.delivered += 1
                else:
                    # 失败原因已在 send_to_subscription 里记录；这里只区分
                    # 「被停用」与「可重试的失败」，供调用方判断是否需要告警。
                    refreshed = await self._subscriptions.get(subscription.id)
                    if refreshed.disabled_at is not None:
                        result.disabled.append(subscription.id)
                    else:
                        result.failed += 1
            except Exception:
                result.failed += 1
                logger.exception("推送投递异常（订阅 %s）", subscription.id)
        return result

    async def notify_confirmation(
        self,
        *,
        confirmation_id: str,
        title: str,
        summary: str,
        url: str,
        has_active_client: bool,
    ) -> PushDeliveryResult:
        """确认请求的推送入口。

        只有在**没有活跃前端连接**时才推送：用户正看着页面时不缺这条通知，
        重复触达只会造成干扰（docs/09 §13.1 的第 2 步）。
        """
        if has_active_client:
            return PushDeliveryResult(skipped_reason="client_connected")
        payload = build_confirmation_payload(
            confirmation_id=confirmation_id,
            title=title,
            summary=summary,
            url=url,
        )
        return await self.broadcast(payload)
