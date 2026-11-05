"""IM 通道服务：绑定、入站与出站（M85 规格 §4.2、§4.5「绑定」「入站消息」）。

职责边界：

- **绑定**：网页生成一次性码（8 位，大写字母与数字，去掉 ``0O1I``，10 分钟
  有效）；IM 里 ``/bind <CODE>`` 消费码、建或复用 active 绑定，并把「已绑定」
  写进发件箱。码不存在、已用、过期或平台不符时回「绑定码无效或已过期」，不写
  收件箱。
- **入站**：已绑定身份发来的普通文本发布为 ``im.message`` 事件（来源是对应
  ``EventSource.IM_*``），由事件总线写总控收件箱；``thread_key`` 是
  ``im:<platform>:<chat>``，``message_id`` 做去重键。未绑定身份直接丢弃并记
  debug 日志。
- **出站**：服务**从不直接调用适配器**。所有回复都经
  :meth:`OutboxWorker.enqueue` 进发件箱（与业务状态同一事务），由 worker 经
  平台适配器发出。绑定回执的 dedupe 键按入站 ``message_id`` 生成，重启重放不会
  重复回复。
- **给 G4 的数据方法**：``list_channels`` / ``list_bindings`` /
  ``revoke_binding`` / ``test_channel`` 与 ``enqueue_for_bindings``。路由层只做
  参数校验与错误码映射，不再自己拼事件或发件箱行。
"""

from __future__ import annotations

import inspect
import logging
import re
import secrets
from collections.abc import Callable, Mapping
from datetime import datetime, timedelta
from typing import Any

from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from maa_api.agent.channels.adapter import Inbound
from maa_api.agent.channels.outbox import OutboxWorker
from maa_api.agent.tickets.inbox import InboxService
from maa_api.agent.tickets.threads import ThreadRouter
from maa_api.db.models import ChannelBindCode, ChannelBinding, Outbox, utcnow
from maa_api.db.repositories.channels import (
    BINDING_ACTIVE,
    ChannelBindCodeRepository,
    ChannelBindingRepository,
)
from maa_api.domain.enums import (
    ChannelPlatform,
    EventSource,
    OutboxChannel,
    OutboxKind,
)
from maa_api.domain.errors import AppError, ErrorCode
from maa_api.settings import Settings, get_settings

__all__ = [
    "BIND_CODE_ALPHABET",
    "BIND_CODE_LENGTH",
    "BIND_CODE_TTL",
    "BIND_INVALID_TEXT",
    "BIND_OK_TEXT",
    "TEST_TEXT",
    "TEXT_ONLY_TEXT",
    "ChannelService",
]

logger = logging.getLogger(__name__)

#: 绑定码长度与有效期（spec §4.3、§4.5「绑定」）。
BIND_CODE_LENGTH = 8
BIND_CODE_TTL = timedelta(minutes=10)
#: 去掉容易混淆的 ``0``、``O``、``1``、``I`` 后的大写字母与数字。
BIND_CODE_ALPHABET = "ABCDEFGHJKLMNPQRSTUVWXYZ23456789"
#: 入站绑定命令；只认大写，由 08 §8 的流程决定。
BIND_PATTERN = re.compile(r"^/bind\s+([A-Z0-9]{8})$")
#: 入站回执文案（spec §4.5：不为这些句子新造错误码）。
BIND_OK_TEXT = "已绑定"
BIND_INVALID_TEXT = "绑定码无效或已过期"
TEXT_ONLY_TEXT = "这个通道只接收普通文本"
TEST_TEXT = "这是一条来自 MAA-API 的测试消息。"
#: 生成绑定码撞主键时的重试次数（32^8 的空间，实际几乎不会撞）。
_BIND_CODE_ATTEMPTS = 5

#: 平台 → 判定「已配置」所需的设置字段（密钥或 id 缺失即未配置）。
_REQUIRED_FIELDS: dict[ChannelPlatform, tuple[str, ...]] = {
    ChannelPlatform.TELEGRAM: ("telegram_bot_token",),
    ChannelPlatform.FEISHU: ("feishu_app_id", "feishu_app_secret"),
    ChannelPlatform.DINGTALK: ("dingtalk_client_id", "dingtalk_client_secret"),
    ChannelPlatform.QQ: ("qq_app_id", "qq_app_secret"),
}


def _platform(value: ChannelPlatform | str) -> ChannelPlatform:
    """把入参收敛成平台枚举；非法值按 spec 的 ``BIND_CODE_INVALID`` 处理。"""
    try:
        return value if isinstance(value, ChannelPlatform) else ChannelPlatform(str(value))
    except ValueError as exc:
        raise AppError(
            ErrorCode.BIND_CODE_INVALID,
            "不存在的通道平台",
            {"platform": str(value)},
        ) from exc


def _outbox_channel(platform: ChannelPlatform) -> OutboxChannel:
    """平台 → 发件箱出口：``telegram`` → ``im_telegram``。"""
    return OutboxChannel(f"im_{platform.value}")


def _event_source(platform: ChannelPlatform) -> EventSource:
    """平台 → 事件来源：``im_telegram`` 等四个。"""
    return EventSource(f"im_{platform.value}")


class ChannelService:
    """绑定、入站与出站编排；依赖全部由装配层注入。"""

    def __init__(
        self,
        session_factory: async_sessionmaker[AsyncSession],
        *,
        outbox_worker: OutboxWorker,
        trigger_service: Any,
        thread_router: ThreadRouter,
        inbox_service: InboxService,
        settings_provider: Callable[[], Any] = get_settings,
        clock: Callable[[], datetime] = utcnow,
    ) -> None:
        self._session_factory = session_factory
        self._outbox = outbox_worker
        self._trigger_service = trigger_service
        # 事件总线内部已用 thread_router 写收件箱；这里保留注入，供 G4 的确认
        # 分支与后续「回复某条工单机器人消息」使用，不在本任务里另开写入口。
        self._thread_router = thread_router
        self._inbox_service = inbox_service
        self._settings_provider = settings_provider
        self._clock = clock

    # ------------------------------------------------------------------
    # 绑定
    # ------------------------------------------------------------------
    async def create_bind_code(
        self, platform: ChannelPlatform | str
    ) -> ChannelBindCode:
        """生成一条 10 分钟有效的一次性绑定码并落库。"""
        target = _platform(platform)
        now = self._clock()
        expires_at = now + BIND_CODE_TTL
        for _ in range(_BIND_CODE_ATTEMPTS):
            code = "".join(
                secrets.choice(BIND_CODE_ALPHABET) for _ in range(BIND_CODE_LENGTH)
            )
            async with self._session_factory() as session:
                try:
                    row = await ChannelBindCodeRepository(session).create(
                        code=code,
                        platform=target.value,
                        expires_at=expires_at,
                        now=now,
                    )
                    await session.commit()
                except IntegrityError:
                    await session.rollback()
                    continue
            logger.info("已生成 %s 绑定码，%s 过期", target.value, expires_at)
            return row
        raise AppError(
            ErrorCode.BIND_CODE_INVALID, "生成绑定码失败，请重试"
        )

    async def handle_inbound(self, inbound: Inbound) -> bool:
        """处理一条平台入站消息，返回是否消费（绑定成功或发布了用户消息）。"""
        platform = _platform(inbound.platform)
        text = str(inbound.text or "").strip()
        if not text:
            logger.debug("丢弃来自 %s 的空消息", platform.value)
            return False

        match = BIND_PATTERN.fullmatch(text)
        if match is not None:
            return await self._bind(platform, inbound, match.group(1))
        if text.startswith("/"):
            # 非绑定命令：只回一句，不写收件箱（spec §4.5「入站消息」）。
            await self._reply(
                platform,
                inbound,
                TEXT_ONLY_TEXT,
                dedupe_key=self._bind_reply_key(platform, inbound, prefix="notice"),
            )
            return False

        binding = await self._active_binding(platform, inbound)
        if binding is None:
            logger.debug(
                "丢弃未绑定身份的消息：platform=%s chat=%s",
                platform.value,
                inbound.external_chat_id,
            )
            return False

        await self._trigger_service.publish(
            source=_event_source(platform),
            kind="im.message",
            thread_key=f"im:{platform.value}:{inbound.external_chat_id}",
            payload={
                "binding_id": binding.id,
                "message_id": inbound.message_id,
                "text": text,
                "reply_to": getattr(inbound, "reply_to", None),
                "platform": platform.value,
                "external_chat_id": inbound.external_chat_id,
            },
            sender=f"im:{platform.value}:{inbound.external_user_id}",
            dedupe_key=f"im:{platform.value}:{inbound.message_id}",
        )
        async with self._session_factory() as session:
            await ChannelBindingRepository(session).mark_seen(
                binding.id, now=self._clock()
            )
            await session.commit()
        return True

    async def _bind(
        self, platform: ChannelPlatform, inbound: Inbound, code: str
    ) -> bool:
        """消费绑定码；成功建/复用绑定，失败回「绑定码无效或已过期」。"""
        now = self._clock()
        bound = False
        async with self._session_factory() as session:
            consumed = await ChannelBindCodeRepository(session).consume(
                code, platform.value, now=now
            )
            if consumed is not None:
                bindings = ChannelBindingRepository(session)
                binding = await bindings.get_by_identity(
                    platform.value,
                    inbound.external_user_id,
                    inbound.external_chat_id,
                )
                if binding is None:
                    await bindings.create(
                        platform=platform.value,
                        external_user_id=inbound.external_user_id,
                        external_chat_id=inbound.external_chat_id,
                        now=now,
                    )
                else:
                    # 唯一索引把「旧 active」限定在同一行：重绑复用这一行并刷新
                    # bound_at，等价于先 revoke 再 create（后者会撞唯一约束）。
                    await bindings.reactivate(binding, now=now)
                bound = True
            await self._outbox.enqueue(
                session,
                channel=_outbox_channel(platform),
                target=inbound.external_chat_id,
                kind=OutboxKind.REPLY,
                payload={
                    "text": BIND_OK_TEXT if bound else BIND_INVALID_TEXT,
                    "platform": platform.value,
                },
                dedupe_key=self._bind_reply_key(platform, inbound),
                now=now,
            )
            await session.commit()
        return bound

    # ------------------------------------------------------------------
    # 出站
    # ------------------------------------------------------------------
    async def enqueue_reply(
        self,
        target: str,
        platform: ChannelPlatform | str,
        text: str,
        *,
        dedupe_key: str | None = None,
        ref_run_id: str | None = None,
    ) -> Outbox:
        """把一条 IM 回复写入发件箱（kind=reply），提交后返回该行。"""
        target_platform = _platform(platform)
        key = dedupe_key or f"reply:{secrets.token_hex(8)}"
        async with self._session_factory() as session:
            row = await self._outbox.enqueue(
                session,
                channel=_outbox_channel(target_platform),
                target=target,
                kind=OutboxKind.REPLY,
                payload={"text": text, "platform": target_platform.value},
                dedupe_key=key,
                ref_run_id=ref_run_id,
                now=self._clock(),
            )
            await session.commit()
        return row

    async def enqueue_for_bindings(
        self,
        kind: OutboxKind | str,
        source_id: str,
        text: str | Callable[[ChannelBinding], str],
        payload: Mapping[str, Any] | None = None,
        *,
        ref_run_id: str | None = None,
    ) -> list[Outbox]:
        """给每个 active 绑定各写一条消息（汇报 / 确认 / 简报 / 告警，G4 用）。

        ``dedupe_key`` 固定为 ``f"{kind}:{source_id}:{binding_id}"``，同一事件
        重复调用不重复写行。没有绑定时返回空列表、不写行。
        """
        try:
            outbox_kind = (
                kind if isinstance(kind, OutboxKind) else OutboxKind(str(kind))
            )
        except ValueError as exc:
            raise AppError(
                ErrorCode.INVALID_PARAMETER,
                "不存在的发件箱种类",
                {"kind": str(kind)},
            ) from exc
        bindings = await self.list_bindings()
        if not bindings:
            return []
        rows: list[Outbox] = []
        async with self._session_factory() as session:
            for binding in bindings:
                message = text(binding) if callable(text) else text
                row = await self._outbox.enqueue(
                    session,
                    channel=_outbox_channel(_platform(binding.platform)),
                    target=binding.external_chat_id,
                    kind=outbox_kind,
                    payload={**dict(payload or {}), "text": message},
                    dedupe_key=(
                        f"{outbox_kind.value}:{source_id}:{binding.id}"
                    ),
                    ref_run_id=ref_run_id,
                    now=self._clock(),
                )
                rows.append(row)
            await session.commit()
        return rows

    # ------------------------------------------------------------------
    # G4 路由使用的数据方法
    # ------------------------------------------------------------------
    async def list_channels(self) -> list[dict[str, Any]]:
        """四个平台的启用、配置与连接状态；不含密钥与绑定列表。

        ``connected`` 由 G4 的适配器装配补充（本任务只返回数据方法能回答的
        ``enabled`` / ``configured``）；``last_error`` 留空。
        """
        settings = await self._settings()
        result: list[dict[str, Any]] = []
        for platform in ChannelPlatform:
            enabled = bool(
                getattr(settings.channels, f"{platform.value}_enabled", False)
            )
            result.append(
                {
                    "platform": platform.value,
                    "enabled": enabled,
                    "configured": self._configured(settings.channels, platform),
                    "connected": None,
                    "last_error": None,
                }
            )
        return result

    async def list_bindings(
        self, *, platform: ChannelPlatform | str | None = None, status: str | None = BINDING_ACTIVE
    ) -> list[ChannelBinding]:
        """绑定列表；默认只列 active，``status=None`` 时列全部。"""
        target = _platform(platform).value if platform is not None else None
        async with self._session_factory() as session:
            return await ChannelBindingRepository(session).list_all(
                platform=target, status=status
            )

    async def revoke_binding(self, binding_id: str) -> None:
        """解除一条绑定；不存在或已撤销时抛 ``CHANNEL_BINDING_NOT_FOUND``。"""
        async with self._session_factory() as session:
            revoked = await ChannelBindingRepository(session).revoke(
                binding_id, now=self._clock()
            )
            await session.commit()
        if not revoked:
            raise AppError(
                ErrorCode.CHANNEL_BINDING_NOT_FOUND,
                "没有这条通道绑定",
                {"binding_id": binding_id},
            )

    async def test_channel(self, platform: ChannelPlatform | str) -> int:
        """给该平台全部 active 绑定各发一条测试消息，返回写入的行数。

        未启用或缺少凭证时抛 ``CHANNEL_NOT_CONFIGURED``（503）；真正的发送由
        发件箱异步完成。
        """
        target = _platform(platform)
        settings = await self._settings()
        enabled = bool(
            getattr(settings.channels, f"{target.value}_enabled", False)
        )
        if not enabled or not self._configured(settings.channels, target):
            raise AppError(
                ErrorCode.CHANNEL_NOT_CONFIGURED,
                "该 IM 通道未启用或缺少凭证",
                {"platform": target.value},
            )
        bindings = await self.list_bindings(platform=target)
        if not bindings:
            return 0
        now = self._clock()
        async with self._session_factory() as session:
            for binding in bindings:
                await self._outbox.enqueue(
                    session,
                    channel=_outbox_channel(target),
                    target=binding.external_chat_id,
                    kind=OutboxKind.ALERT,
                    payload={"text": TEST_TEXT, "platform": target.value},
                    dedupe_key=(
                        f"test:{target.value}:{binding.id}:{now.isoformat()}"
                    ),
                    now=now,
                )
            await session.commit()
        return len(bindings)

    # ------------------------------------------------------------------
    # 内部
    # ------------------------------------------------------------------
    async def _active_binding(
        self, platform: ChannelPlatform, inbound: Inbound
    ) -> ChannelBinding | None:
        async with self._session_factory() as session:
            return await ChannelBindingRepository(session).get_active(
                platform.value,
                inbound.external_user_id,
                inbound.external_chat_id,
            )

    async def _reply(
        self,
        platform: ChannelPlatform,
        inbound: Inbound,
        text: str,
        *,
        dedupe_key: str,
    ) -> None:
        async with self._session_factory() as session:
            await self._outbox.enqueue(
                session,
                channel=_outbox_channel(platform),
                target=inbound.external_chat_id,
                kind=OutboxKind.REPLY,
                payload={"text": text, "platform": platform.value},
                dedupe_key=dedupe_key,
                now=self._clock(),
            )
            await session.commit()

    @staticmethod
    def _bind_reply_key(
        platform: ChannelPlatform, inbound: Inbound, *, prefix: str = "bind"
    ) -> str:
        """回执去重键：同一入站消息重放只回一次；没有 message_id 时用随机值。"""
        message_id = str(inbound.message_id or "").strip() or secrets.token_hex(8)
        return f"{prefix}:{platform.value}:{message_id}"[:160]

    @staticmethod
    def _configured(channels: Any, platform: ChannelPlatform) -> bool:
        """平台凭证是否齐全；空串等于未配置。"""
        return all(
            str(getattr(channels, name, "") or "").strip()
            for name in _REQUIRED_FIELDS[platform]
        )

    async def _settings(self) -> Settings:
        """设置提供者可以是同步或异步（与 TriggerService 同口径）。"""
        value = self._settings_provider()
        if inspect.isawaitable(value):
            value = await value
        return value
