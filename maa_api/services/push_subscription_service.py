"""Web Push 订阅的登记、列表与失效标记（M14，docs/09 §13.1）。

**校验必须在写入前完成。** ``p256dh`` 是浏览器的 ECDH 公钥，只有在 P-256 曲线上
且不是无穷远点时才能参与密钥协商（RFC 8291 §7 把这条列为必需的安全检查：
不校验公钥可能导致私钥被提取）。所以这里不只查长度，还实际解析一次点。

**列表接口不回显密钥材料。** ``endpoint`` 全文、``p256dh``、``auth`` 与凭据同级：
``endpoint`` 本身是推送服务的持有者凭据（拿到它就能向该订阅投递），两个密钥字段
更是解密推送所必需。设置页只需要「哪台设备、是否失效、最近是否送达」，所以对外
形状固定为 :func:`subscription_wire`。

VAPID 密钥的管理规则见 :mod:`maa_api.services.webpush_vapid`：私钥来自
``config.yaml`` / 环境变量，**不在启动时自动生成**。
"""

from __future__ import annotations

from collections.abc import Sequence
from urllib.parse import urlsplit

import sqlalchemy.exc
from cryptography.hazmat.primitives.asymmetric import ec
from sqlalchemy.ext.asyncio import AsyncSession

from maa_api.db import session as db_session
from maa_api.db.models import PushSubscription
from maa_api.db.repositories.push import PushSubscriptionRepository
from maa_api.domain.errors import AppError, ErrorCode
from maa_api.services import webpush_crypto as _crypto
from maa_api.services import webpush_vapid as vapid

__all__ = ["PushSubscriptionService", "subscription_wire"]

#: User-Agent 头部可能很长，DB 列宽 255；超长截断而不是拒绝（它只是备注）。
_MAX_USER_AGENT = 255


def _invalid(message: str, details: dict[str, object] | None = None) -> AppError:
    return AppError(ErrorCode.PUSH_SUBSCRIPTION_INVALID, message, details)


def _validate_endpoint(endpoint: str) -> str:
    """订阅 endpoint 必须是 ``https`` 绝对 URL。

    推送服务全部要求 HTTPS；接受 HTTP 会得到一个永远投递失败、却每次都被
    重试的订阅。
    """
    if not endpoint or not isinstance(endpoint, str):
        raise _invalid("endpoint 不能为空")
    if len(endpoint) > 2048:
        raise _invalid("endpoint 过长", {"length": len(endpoint)})
    parts = urlsplit(endpoint)
    if parts.scheme != "https" or not parts.netloc:
        raise _invalid(
            "endpoint 必须是 https 绝对 URL", {"scheme": parts.scheme or None}
        )
    return parts.netloc


def _validate_p256dh(value: str) -> None:
    """``p256dh`` 必须是 65 octet 未压缩 P-256 点。"""
    if not value or not isinstance(value, str):
        raise _invalid("keys.p256dh 不能为空")
    try:
        raw = _crypto.b64url_decode(value)
    except _crypto.PushEncryptionError as exc:
        raise _invalid(f"keys.p256dh 不是合法的 base64url: {exc}") from exc
    if len(raw) != 65 or raw[0] != 0x04:
        raise _invalid(
            "keys.p256dh 必须是 65 octet 未压缩 P-256 点",
            {"length": len(raw)},
        )
    try:
        ec.EllipticCurvePublicKey.from_encoded_point(ec.SECP256R1(), raw)
    except ValueError as exc:
        # 长度对但不在曲线上 / 无穷远点：这一步是 RFC 8291 §7 要求的检查
        raise _invalid(f"keys.p256dh 不在 P-256 曲线上: {exc}") from exc


def _validate_auth(value: str) -> None:
    """``auth`` 必须是 16 octet 认证密钥（RFC 8291 §3.2）。"""
    if not value or not isinstance(value, str):
        raise _invalid("keys.auth 不能为空")
    try:
        raw = _crypto.b64url_decode(value)
    except _crypto.PushEncryptionError as exc:
        raise _invalid(f"keys.auth 不是合法的 base64url: {exc}") from exc
    if len(raw) != 16:
        raise _invalid("keys.auth 必须是 16 octet", {"length": len(raw)})


def subscription_wire(subscription: PushSubscription) -> dict[str, object]:
    """订阅的对外形状：**不含** ``endpoint`` 全文与两个密钥字段。"""
    return {
        "id": subscription.id,
        "endpoint_host": subscription.endpoint_host,
        "user_agent": subscription.user_agent,
        "created_at": subscription.created_at,
        "last_success_at": subscription.last_success_at,
        "disabled": subscription.disabled_at is not None,
        "disabled_at": subscription.disabled_at,
    }


class PushSubscriptionService:
    """订阅的登记与生命周期管理。"""

    def __init__(self, session_factory=None) -> None:
        self._session_factory = session_factory or db_session.session_factory

    def _configured_key(self) -> ec.EllipticCurvePrivateKey:
        """读取并解析 VAPID 私钥，未配置或非法时抛 ``PUSH_NOT_CONFIGURED``。"""
        settings = self._settings()
        raw = (settings.notify.vapid_private_key or "").strip()
        if not raw:
            raise AppError(
                ErrorCode.PUSH_NOT_CONFIGURED,
                "未配置 VAPID 私钥，Web Push 不可用；"
                "请运行 scripts/generate_vapid_keys.py 并把结果写入 config.yaml",
            )
        try:
            return vapid.load_vapid_private_key(raw)
        except ValueError as exc:
            # 配置了但不可用同样是「没配好」，用同一个码而不是 500
            raise AppError(
                ErrorCode.PUSH_NOT_CONFIGURED,
                f"VAPID 私钥不可用: {exc}",
            ) from exc

    def _settings(self):
        from maa_api.settings import get_settings

        return get_settings()

    def subject(self) -> str:
        return self._settings().notify.vapid_subject

    async def public_key(self) -> dict[str, str]:
        """``GET /api/notifications/vapid-public-key`` 的响应体。

        解析一次私钥再导出公钥（而不是单独存一份公钥）：两份配置会漂移，
        而漂移的表现是「订阅成功但推送全部失败」，很难排查。
        """
        private_key = self._configured_key()
        return {
            "public_key": vapid.public_key_from_private(private_key),
            "subject": self.subject(),
        }

    async def list_subscriptions(
        self, *, include_disabled: bool = False
    ) -> list[PushSubscription]:
        async with self._session_factory() as session:
            rows = await PushSubscriptionRepository(session).list_all(
                include_disabled=include_disabled
            )
            return list(rows)

    async def subscribe(
        self,
        *,
        endpoint: str,
        p256dh: str,
        auth: str,
        user_agent: str | None = None,
    ) -> tuple[PushSubscription, bool]:
        """登记订阅，返回 ``(订阅, 是否新建)``。

        幂等：同一 endpoint 重复登记返回既有行并刷新密钥，不让浏览器重装
        PWA 之后留下永远投递失败的陈旧密钥。
        """
        host = _validate_endpoint(endpoint)
        _validate_p256dh(p256dh)
        _validate_auth(auth)
        trimmed_agent = (user_agent or "").strip()[:_MAX_USER_AGENT] or None

        async with self._session_factory() as session:
            try:
                stored, created = await PushSubscriptionRepository(session).upsert(
                    endpoint=endpoint,
                    endpoint_host=host,
                    p256dh=p256dh,
                    auth=auth,
                    user_agent=trimmed_agent,
                )
                await session.commit()
            except sqlalchemy.exc.IntegrityError as exc:
                # 并发重放同一 endpoint 时唯一约束兜底：改走更新路径
                await session.rollback()
                existing = await PushSubscriptionRepository(session).get_by_endpoint(
                    endpoint
                )
                if existing is None:
                    raise _invalid("订阅写入冲突，请重试") from exc
                stored, created = existing, False
        return stored, created

    async def get(self, subscription_id: str) -> PushSubscription:
        async with self._session_factory() as session:
            subscription = await PushSubscriptionRepository(session).get(
                subscription_id
            )
        if subscription is None:
            raise AppError(
                ErrorCode.PUSH_SUBSCRIPTION_NOT_FOUND,
                "订阅不存在",
                {"subscription_id": subscription_id},
            )
        return subscription

    async def delete(self, subscription_id: str) -> None:
        async with self._session_factory() as session:
            deleted = await PushSubscriptionRepository(session).delete(subscription_id)
            if not deleted:
                raise AppError(
                    ErrorCode.PUSH_SUBSCRIPTION_NOT_FOUND,
                    "订阅不存在",
                    {"subscription_id": subscription_id},
                )
            await session.commit()

    async def mark_sent(self, subscription_id: str) -> None:
        async with self._session_factory() as session:
            await PushSubscriptionRepository(session).mark_sent(subscription_id)
            await session.commit()

    async def mark_disabled(self, subscription_id: str) -> None:
        async with self._session_factory() as session:
            await PushSubscriptionRepository(session).mark_disabled(subscription_id)
            await session.commit()

    async def active_subscriptions(self) -> Sequence[PushSubscription]:
        """未失效的订阅，供发送路径使用。"""
        async with self._session_factory() as session:
            return await PushSubscriptionRepository(session).list_all()
