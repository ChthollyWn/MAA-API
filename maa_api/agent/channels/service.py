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

from maa_api.agent.channels.adapter import ChannelAdapter, Inbound
from maa_api.agent.channels.outbox import OutboxWorker
from maa_api.agent.tickets.inbox import InboxService
from maa_api.agent.tickets.threads import ThreadRouter
from maa_api.db.models import (
    ChannelBindCode,
    ChannelBinding,
    Confirmation,
    Outbox,
    utcnow,
)
from maa_api.db.repositories.audit import ConfirmationRepository
from maa_api.db.repositories.channels import (
    BINDING_ACTIVE,
    ChannelBindCodeRepository,
    ChannelBindingRepository,
    OutboxRepository,
)
from maa_api.domain.enums import (
    ChannelPlatform,
    EventSource,
    OutboxChannel,
    OutboxKind,
    RiskLevel,
)
from maa_api.domain.errors import AppError, ErrorCode
from maa_api.settings import Settings, get_settings

__all__ = [
    "BIND_CODE_ALPHABET",
    "BIND_CODE_LENGTH",
    "BIND_CODE_TTL",
    "BIND_INVALID_TEXT",
    "BIND_OK_TEXT",
    "CONFIRMATION_DENIED_TEXT",
    "CONFIRMATION_NOT_ALLOWED_TEXT",
    "CONFIRMATION_NOT_FOUND_TEXT",
    "CONFIRMATION_OK_TEXT",
    "CONFIRMATION_REPLY_PATTERN",
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
#: 确认答复：只认「同意/拒绝 + 确认 id 前 8 位」，不加「可以」这类模糊说法。
CONFIRMATION_REPLY_PATTERN = re.compile(r"^(同意|拒绝)\s+([0-9a-fA-F]{8})$")
#: 入站回执文案（spec §4.5：不为这些句子新造错误码）。
BIND_OK_TEXT = "已绑定"
BIND_INVALID_TEXT = "绑定码无效或已过期"
TEXT_ONLY_TEXT = "这个通道只接收普通文本"
TEST_TEXT = "这是一条来自 MAA-API 的测试消息。"
CONFIRMATION_OK_TEXT = "已同意"
CONFIRMATION_DENIED_TEXT = "已拒绝"
CONFIRMATION_NOT_FOUND_TEXT = "没有找到这条待确认请求，可能已经处理或过期"
CONFIRMATION_NOT_ALLOWED_TEXT = "这类确认只能在网页上处理"
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
        adapters: Mapping[ChannelPlatform | str, ChannelAdapter] | None = None,
        confirmation_service: Any = None,
        clock: Callable[[], datetime] = utcnow,
    ) -> None:
        self._session_factory = session_factory
        self._outbox = outbox_worker
        self._trigger_service = trigger_service
        #: 平台 → 适配器。``list_channels`` 读 running；``test_channel`` 同步发测试消息。
        self._adapters = {
            str(key): adapter for key, adapter in (adapters or {}).items()
        }
        #: 确认服务在 G4 才参与：入站批准/拒绝与「创建确认后写 IM 发件箱」。
        self._confirmation_service = confirmation_service
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

        # 确认答复：只认精确短语，不把「可以」「同意」单独当成批准（spec §4.5）。
        if await self._handle_confirmation_reply(platform, inbound, text):
            return True

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
    # 确认答复
    # ------------------------------------------------------------------
    async def _handle_confirmation_reply(
        self,
        platform: ChannelPlatform,
        inbound: Inbound,
        text: str,
    ) -> bool:
        """处理「同意/拒绝 <前 8 位>」；返回是否已消费这条入站消息。

        - 短语必须精确匹配，单独「同意」「可以」不算（spec §4.5「确认」）；
        - 破坏类默认不在 IM 里批准：回复网页链接并记
          ``CHANNEL_APPROVAL_NOT_ALLOWED``，确认保持 pending；
        - 成功回「已同意 / 已拒绝」，绝不写收件箱。
        """
        match = CONFIRMATION_REPLY_PATTERN.fullmatch(text)
        if match is None:
            return False
        service = self._confirmation_service
        if service is None:
            await self._reply(
                platform,
                inbound,
                CONFIRMATION_NOT_FOUND_TEXT,
                dedupe_key=self._bind_reply_key(platform, inbound, prefix="confirm"),
            )
            return True
        approved = match.group(1) == "同意"
        prefix = match.group(2).lower()
        confirmation = await self._pending_confirmation(prefix)
        if confirmation is None:
            await self._reply(
                platform,
                inbound,
                CONFIRMATION_NOT_FOUND_TEXT,
                dedupe_key=self._bind_reply_key(platform, inbound, prefix="confirm"),
            )
            return True
        settings = await self._settings()
        if (
            str(confirmation.risk_level) == RiskLevel.DESTRUCTIVE.value
            and not bool(settings.channels.allow_destructive_approval)
        ):
            logger.warning(
                "%s：破坏类确认 %s 不能在 IM 中处理，platform=%s",
                ErrorCode.CHANNEL_APPROVAL_NOT_ALLOWED,
                confirmation.id,
                platform.value,
            )
            await self._reply(
                platform,
                inbound,
                f"{CONFIRMATION_NOT_ALLOWED_TEXT}：/?confirm={confirmation.id}",
                dedupe_key=self._bind_reply_key(platform, inbound, prefix="confirm"),
            )
            return True
        try:
            await service.resolve(
                confirmation.id,
                approved=approved,
                resolved_by="im",
                reason=f"IM {platform.value} {'批准' if approved else '拒绝'}",
            )
        except AppError as exc:
            logger.info(
                "IM 处理确认 %s 失败：%s（%s）",
                confirmation.id,
                exc.code,
                platform.value,
            )
            await self._reply(
                platform,
                inbound,
                CONFIRMATION_NOT_FOUND_TEXT,
                dedupe_key=self._bind_reply_key(platform, inbound, prefix="confirm"),
            )
            return True
        await self._reply(
            platform,
            inbound,
            CONFIRMATION_OK_TEXT if approved else CONFIRMATION_DENIED_TEXT,
            dedupe_key=self._bind_reply_key(platform, inbound, prefix="confirm"),
        )
        return True

    async def _pending_confirmation(self, prefix: str) -> Confirmation | None:
        """按 8 位前缀找**唯一**一条 pending 确认；歧义或没有都返回 ``None``。"""
        async with self._session_factory() as session:
            rows = await ConfirmationRepository(session).list_pending_by_prefix(
                prefix, limit=2
            )
        return rows[0] if len(rows) == 1 else None

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

    async def enqueue_reply_for_run(
        self,
        run_id: str,
        target: str,
        platform: ChannelPlatform | str,
        text: str,
        *,
        ref_run_id: str | None = None,
    ) -> Outbox:
        """总控 IM 来源的一轮回复：``dedupe_key=reply:<run_id>``（spec §4.5）。"""
        return await self.enqueue_reply(
            target,
            platform,
            text,
            dedupe_key=f"reply:{run_id}",
            ref_run_id=ref_run_id or run_id,
        )

    async def enqueue_for_bindings(
        self,
        kind: OutboxKind | str,
        source_id: str,
        text: str | Callable[[ChannelBinding], str],
        payload: Mapping[str, Any] | None = None,
        *,
        ref_run_id: str | None = None,
        ref_ticket_id: str | None = None,
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
                    ref_ticket_id=ref_ticket_id,
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
            adapter = self._adapters.get(platform.value)
            result.append(
                {
                    "platform": platform.value,
                    "enabled": enabled,
                    "configured": self._configured(settings.channels, platform),
                    "connected": (
                        bool(getattr(adapter, "running", False))
                        if adapter is not None
                        else None
                    ),
                    "last_error": getattr(adapter, "last_error", None),
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
        """同步给该平台全部 active 绑定发测试消息，返回发送条数。

        测试端点是「同步发送路径」：未启用 / 缺凭证回 503
        ``CHANNEL_NOT_CONFIGURED``；平台拒绝或不可达回 502
        ``CHANNEL_SEND_FAILED``。发件箱内部的重试与死信不抛这个码（04 §2.4）。
        """
        target = _platform(platform)
        settings = await self._settings()
        enabled = bool(
            getattr(settings.channels, f"{target.value}_enabled", False)
        )
        adapter = self._adapters.get(target.value)
        if (
            not enabled
            or not self._configured(settings.channels, target)
            or adapter is None
        ):
            raise AppError(
                ErrorCode.CHANNEL_NOT_CONFIGURED,
                "该 IM 通道未启用或缺少凭证",
                {"platform": target.value},
            )
        bindings = await self.list_bindings(platform=target)
        sent = 0
        for binding in bindings:
            try:
                await adapter.send(binding.external_chat_id, TEST_TEXT)
            except Exception as exc:  # noqa: BLE001 - 测试发送失败统一映射 502
                logger.warning(
                    "通道测试消息发送失败：platform=%s binding=%s",
                    target.value,
                    binding.id,
                    exc_info=True,
                )
                raise AppError(
                    ErrorCode.CHANNEL_SEND_FAILED,
                    "测试消息发送失败",
                    {
                        "platform": target.value,
                        "binding_id": binding.id,
                        "reason": str(exc),
                    },
                ) from exc
            sent += 1
        return sent

    async def list_outbox(
        self,
        *,
        status: str | None = None,
        channel: str | None = None,
        page: int = 1,
        size: int = 20,
    ):
        """发件箱分页；路由层只做参数校验与 DTO 映射。"""
        async with self._session_factory() as session:
            return await OutboxRepository(session).page(
                status=status,
                channel=channel,
                page=page,
                size=size,
            )

    async def on_ticket_report(self, ticket: Any, report: Any) -> None:
        """工单汇报落库后给 active 绑定写 ``kind=report``（M85 §4.5「出站」）。

        只转发 08 §1.5 允许打扰的四种状态；progress 与 dropped 不进 IM。
        ``source_id`` 用 ``<ticket_id>:<status>``（监听器拿到的是 ``ReportInput``，
        没有落库后的 report id），去重键仍是 ``{kind}:{source_id}:{binding_id}``。
        """
        status = getattr(getattr(report, "status", None), "value", None) or str(
            getattr(report, "status", "")
        )
        if status not in {"waiting", "ready", "blocked", "failed"}:
            return
        title = str(getattr(ticket, "title", "") or "")
        summary = str(getattr(report, "summary", "") or "")
        text = f"{title}：{summary}" if title else summary
        await self.enqueue_for_bindings(
            OutboxKind.REPORT,
            f"{getattr(ticket, 'id', '')}:{status}",
            text,
            {
                "ticket_id": getattr(ticket, "id", None),
                "ticket_title": title,
                "status": status,
                "summary": summary,
            },
            ref_ticket_id=getattr(ticket, "id", None),
        )

    async def on_confirmation_created(
        self, confirmation: Any, tool_name: str
    ) -> None:
        """确认进入 pending 后给 active 绑定写 ``kind=confirmation``。

        由 :class:`ConfirmationService` 调用；发件箱失败只记日志，不能反过来让
        确认流程失败。破坏类且未放开时只给网页链接。
        """
        confirmation_id = str(getattr(confirmation, "id", ""))
        if not confirmation_id:
            return
        risk = str(getattr(confirmation, "risk_level", ""))
        reason = str(getattr(confirmation, "reason", "") or "")
        prefix = confirmation_id[:8]
        settings = await self._settings()
        if (
            risk == RiskLevel.DESTRUCTIVE.value
            and not bool(settings.channels.allow_destructive_approval)
        ):
            text = (
                f"{CONFIRMATION_NOT_ALLOWED_TEXT}：{tool_name}\n"
                f"{reason}\n/?confirm={confirmation_id}"
            )
        else:
            text = (
                f"需要确认：{tool_name}\n{reason}\n"
                f"回复「同意 {prefix}」或「拒绝 {prefix}」"
            )
        try:
            await self.enqueue_for_bindings(
                OutboxKind.CONFIRMATION,
                confirmation_id,
                text,
                {
                    "confirmation_id": confirmation_id,
                    "tool_name": tool_name,
                    "risk_level": risk,
                    "url": f"/?confirm={confirmation_id}",
                },
            )
        except Exception:  # noqa: BLE001 - 出站失败不能影响确认本身
            logger.warning(
                "确认 %s 写 IM 发件箱失败", confirmation_id, exc_info=True
            )

    def bind_confirmation_service(self, service: Any) -> None:
        """装配晚于构造时补注入确认服务。"""
        self._confirmation_service = service

    def register_adapter(
        self, platform: ChannelPlatform | str, adapter: ChannelAdapter
    ) -> None:
        """替换某个平台的适配器（测试与冒烟的注入点，不改路由契约）。

        发件箱的 ``adapter_for`` 由装配层闭包持有同一个适配器字典，调用方在
        替换本服务的映射时也要替换 ``app.state.channel_adapters``。
        """
        self._adapters[_platform(platform).value] = adapter

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
