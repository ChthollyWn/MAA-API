"""M85 T2：通道绑定、入站与发件箱回复（spec §4.5「绑定」「入站消息」、§6）。

覆盖 spec §6 的三条（``test_bind_code_expires`` /
``test_bind_then_message_reaches_orchestrator`` /
``test_duplicate_platform_message_ignored``），另补：绑定码已用、平台不符、
重复绑定复用同一行、非绑定斜杠命令、未绑定身份丢弃、发件箱去重与
``enqueue_for_bindings`` / 数据方法。

测试纪律与 ``tests/agent/events/test_bus.py`` 一致：临时库用
``tests/fakes/v3_db.py`` 的真实 SQLite；收件箱、话题路由、事件总线与发件箱
都是真实实现，只有平台适配器是 :class:`FakeAdapter`（不联网），不碰
``resource/maa_api.db``。
"""

from __future__ import annotations

import asyncio
from datetime import datetime, timedelta
from types import SimpleNamespace
from typing import Any

from sqlalchemy import func, select

from maa_api.agent.channels.adapter import Inbound
from maa_api.agent.channels.fake import FakeAdapter
from maa_api.agent.channels.outbox import OutboxWorker
from maa_api.agent.channels.service import (
    BIND_CODE_ALPHABET,
    BIND_CODE_LENGTH,
    BIND_CODE_TTL,
    BIND_INVALID_TEXT,
    BIND_OK_TEXT,
    TEST_TEXT,
    TEXT_ONLY_TEXT,
    ChannelService,
)
from maa_api.agent.events.bus import TriggerService
from maa_api.agent.events.rules import TriggerRuleService
from maa_api.agent.tickets.inbox import InboxService
from maa_api.agent.tickets.threads import ThreadRouter
from maa_api.db.models import (
    AgentInbox,
    AgentTrigger,
    ChannelBinding,
    Outbox,
)
from maa_api.db.repositories.channels import (
    ChannelBindCodeRepository,
    ChannelBindingRepository,
    OutboxRepository,
)
from maa_api.domain.enums import (
    ChannelPlatform,
    InboxKind,
    OutboxChannel,
    OutboxKind,
    OutboxStatus,
    TriggerRoute,
)
from maa_api.domain.errors import AppError, ErrorCode
from maa_api.settings import Settings
from tests.fakes.v3_db import make_session_factory, seed_session

#: 固定时钟：绑定码的有效期与会话时间不随真实时间漂移。
NOW = datetime(2026, 11, 5, 12, 0, 0)
USER = "im-user-1"
CHAT = "chat-1"


class _SpecRegistry:
    """最小代理定义注册表；im.message 不该命中任何规则，因此不会被调用。"""

    def get(self, name: str) -> SimpleNamespace:
        return SimpleNamespace(kind="worker", risk_ceiling="none")


class _Evaluator:
    """替身研判：im.message 不命中规则时不应进入研判。"""

    async def evaluate(self, trigger: Any, rule: Any) -> Any:  # pragma: no cover
        raise AssertionError("im.message 不应触发研判")


class _Harness:
    """临时库上的真实事件总线、收件箱、路由、发件箱与通道服务。"""

    def __init__(self, factory: Any) -> None:
        self.factory = factory
        self.settings = Settings()
        self.now = NOW
        self.inbox = InboxService(factory, clock=lambda: self.now)
        self.router = ThreadRouter(factory, self.inbox, clock=lambda: self.now)
        self.rules = TriggerRuleService(
            factory,
            spec_registry=_SpecRegistry(),
            settings_provider=lambda: self.settings,
        )
        self.trigger = TriggerService(
            factory,
            app=SimpleNamespace(state=SimpleNamespace(worker_service=None)),
            rules=self.rules,
            evaluator=_Evaluator(),
            ticket_service=SimpleNamespace(),
            thread_router=self.router,
            orchestrator_service=SimpleNamespace(),
            broadcast=None,
            settings_provider=lambda: self.settings,
            clock=lambda: self.now,
        )
        self.adapter = FakeAdapter(ChannelPlatform.TELEGRAM)
        self.worker = OutboxWorker(
            factory,
            lambda _channel: self.adapter,
            clock=lambda: self.now,
        )
        self.service = ChannelService(
            factory,
            outbox_worker=self.worker,
            trigger_service=self.trigger,
            thread_router=self.router,
            inbox_service=self.inbox,
            settings_provider=lambda: self.settings,
            clock=lambda: self.now,
        )

    def advance(self, delta: timedelta) -> None:
        self.now += delta


async def _factory(path: Any) -> Any:
    return await make_session_factory(path)


async def _count(factory: Any, model: Any, *conditions: Any) -> int:
    async with factory() as session:
        statement = select(func.count()).select_from(model).where(*conditions)
        return int(await session.scalar(statement) or 0)


async def _outbox(factory: Any) -> list[Outbox]:
    async with factory() as session:
        result = await session.execute(select(Outbox).order_by(Outbox.id.asc()))
        return list(result.scalars().all())


async def _inbox(factory: Any, session_id: str) -> list[AgentInbox]:
    async with factory() as session:
        result = await session.execute(
            select(AgentInbox)
            .where(AgentInbox.session_id == session_id)
            .order_by(AgentInbox.id.asc())
        )
        return list(result.scalars().all())


def _inbound(
    text: str,
    *,
    message_id: str,
    platform: ChannelPlatform = ChannelPlatform.TELEGRAM,
    user: str = USER,
    chat: str = CHAT,
) -> Inbound:
    return Inbound(
        platform=platform,
        external_user_id=user,
        external_chat_id=chat,
        text=text,
        message_id=message_id,
    )


async def _bind(
    harness: _Harness,
    *,
    platform: ChannelPlatform = ChannelPlatform.TELEGRAM,
    user: str = USER,
    chat: str = CHAT,
    message_id: str = "bind-1",
) -> tuple[str, bool]:
    code_row = await harness.service.create_bind_code(platform)
    ok = await harness.service.handle_inbound(
        _inbound(
            f"/bind {code_row.code}",
            message_id=message_id,
            platform=platform,
            user=user,
            chat=chat,
        )
    )
    return code_row.code, ok


# ---------------------------------------------------------------------------
# spec §6 三条
# ---------------------------------------------------------------------------
def test_bind_code_expires(tmp_path) -> None:
    """过期码返回 BIND_CODE_INVALID 口径的回执，不建绑定、不写收件箱。"""

    async def scenario() -> None:
        engine, factory = await _factory(tmp_path / "bind.db")
        try:
            harness = _Harness(factory)
            code = (await harness.service.create_bind_code("telegram")).code
            assert len(code) == BIND_CODE_LENGTH
            assert set(code) <= set(BIND_CODE_ALPHABET)
            assert not set(code) & set("0O1I")

            harness.advance(BIND_CODE_TTL + timedelta(seconds=1))
            accepted = await harness.service.handle_inbound(
                _inbound(f"/bind {code}", message_id="m-expired")
            )

            assert accepted is False
            assert await _count(factory, ChannelBinding) == 0
            assert await _count(factory, AgentInbox) == 0
            rows = await _outbox(factory)
            assert [row.payload["text"] for row in rows] == [BIND_INVALID_TEXT]
            assert rows[0].channel == OutboxChannel.IM_TELEGRAM.value
            assert rows[0].target == CHAT

            # 回执由 OutboxWorker 经 FakeAdapter 发出，服务不直接调适配器。
            assert harness.adapter.sent == []
            assert await harness.worker.run_once(now=harness.now) == 1
            assert harness.adapter.sent == [(CHAT, BIND_INVALID_TEXT)]

            async with factory() as session:
                stored = await ChannelBindCodeRepository(session).get(code)
                assert stored is not None and stored.used_at is None
        finally:
            await engine.dispose()

    asyncio.run(scenario())


def test_bind_then_message_reaches_orchestrator(tmp_path) -> None:
    """绑定成功后，普通文本写成 user_message 收件箱项，payload 含平台。"""

    async def scenario() -> None:
        engine, factory = await _factory(tmp_path / "bind.db")
        try:
            harness = _Harness(factory)
            orchestrator_id = await seed_session(
                factory, kind="orchestrator", status="active"
            )
            code, accepted = await _bind(harness)

            assert accepted is True
            assert len(code) == BIND_CODE_LENGTH
            async with factory() as session:
                binding = await ChannelBindingRepository(session).get_active(
                    "telegram", USER, CHAT
                )
                assert binding is not None
                assert binding.status == "active"
                assert binding.last_seen_at == NOW
            replies = await _outbox(factory)
            assert [row.payload["text"] for row in replies] == [BIND_OK_TEXT]
            assert await harness.worker.run_once(now=harness.now) == 1
            assert harness.adapter.sent == [(CHAT, BIND_OK_TEXT)]

            accepted = await harness.service.handle_inbound(
                _inbound("帮我刷个理智", message_id="m-1")
            )
            assert accepted is True

            items = await _inbox(factory, orchestrator_id)
            assert len(items) == 1
            item = items[0]
            assert item.kind == InboxKind.USER_MESSAGE.value
            assert item.payload["source"] == "im_telegram"
            assert "帮我刷个理智" in item.payload["payload"]
            assert '"platform": "telegram"' in item.payload["payload"]

            async with factory() as session:
                trigger = (
                    await session.execute(
                        select(AgentTrigger).where(
                            AgentTrigger.dedupe_key == "im:telegram:m-1"
                        )
                    )
                ).scalar_one()
                assert trigger.kind == "im.message"
                assert trigger.source == "im_telegram"
                assert trigger.thread_key == f"im:telegram:{CHAT}"
                assert trigger.routed_to == TriggerRoute.ORCHESTRATOR.value
                binding = await ChannelBindingRepository(session).get_active(
                    "telegram", USER, CHAT
                )
                assert binding is not None
                assert binding.last_seen_at == NOW
        finally:
            await engine.dispose()

    asyncio.run(scenario())


def test_duplicate_platform_message_ignored(tmp_path) -> None:
    """同一 message_id 只发布一条事件、只写一条收件箱项。"""

    async def scenario() -> None:
        engine, factory = await _factory(tmp_path / "bind.db")
        try:
            harness = _Harness(factory)
            orchestrator_id = await seed_session(
                factory, kind="orchestrator", status="active"
            )
            await _bind(harness)

            first = await harness.service.handle_inbound(
                _inbound("同一句话", message_id="m-dup")
            )
            second = await harness.service.handle_inbound(
                _inbound("同一句话", message_id="m-dup")
            )

            assert first is True
            assert second is True
            assert await _count(factory, AgentTrigger) == 1
            assert len(await _inbox(factory, orchestrator_id)) == 1
        finally:
            await engine.dispose()

    asyncio.run(scenario())


# ---------------------------------------------------------------------------
# 绑定边界
# ---------------------------------------------------------------------------
def test_used_bind_code_is_rejected(tmp_path) -> None:
    async def scenario() -> None:
        engine, factory = await _factory(tmp_path / "bind.db")
        try:
            harness = _Harness(factory)
            code, accepted = await _bind(harness)
            assert accepted is True

            accepted = await harness.service.handle_inbound(
                _inbound(f"/bind {code}", message_id="m-reuse")
            )

            assert accepted is False
            assert await _count(factory, ChannelBinding) == 1
            replies = [row.payload["text"] for row in await _outbox(factory)]
            assert replies == [BIND_OK_TEXT, BIND_INVALID_TEXT]
        finally:
            await engine.dispose()

    asyncio.run(scenario())


def test_bind_code_from_another_platform_is_rejected(tmp_path) -> None:
    async def scenario() -> None:
        engine, factory = await _factory(tmp_path / "bind.db")
        try:
            harness = _Harness(factory)
            code_row = await harness.service.create_bind_code(
                ChannelPlatform.TELEGRAM
            )

            accepted = await harness.service.handle_inbound(
                _inbound(
                    f"/bind {code_row.code}",
                    message_id="m-platform",
                    platform=ChannelPlatform.FEISHU,
                    chat="feishu-chat",
                )
            )

            assert accepted is False
            assert await _count(factory, ChannelBinding) == 0
            assert [row.payload["text"] for row in await _outbox(factory)] == [
                BIND_INVALID_TEXT
            ]
            async with factory() as session:
                stored = await ChannelBindCodeRepository(session).get(code_row.code)
                assert stored is not None and stored.used_at is None
        finally:
            await engine.dispose()

    asyncio.run(scenario())


def test_rebinding_reuses_the_single_active_binding(tmp_path) -> None:
    async def scenario() -> None:
        engine, factory = await _factory(tmp_path / "bind.db")
        try:
            harness = _Harness(factory)
            _, first = await _bind(harness, message_id="bind-a")
            assert first is True
            harness.advance(timedelta(hours=1))
            _, second = await _bind(harness, message_id="bind-b")
            assert second is True

            # 唯一索引不允许同一 (platform, user, chat) 出现第二行；重绑复用该行。
            assert await _count(factory, ChannelBinding) == 1
            async with factory() as session:
                binding = await ChannelBindingRepository(session).get_active(
                    "telegram", USER, CHAT
                )
                assert binding is not None
                assert binding.bound_at == harness.now
                assert binding.revoked_at is None
            replies = [row.payload["text"] for row in await _outbox(factory)]
            assert replies == [BIND_OK_TEXT, BIND_OK_TEXT]
        finally:
            await engine.dispose()

    asyncio.run(scenario())


# ---------------------------------------------------------------------------
# 入站边界
# ---------------------------------------------------------------------------
def test_slash_command_replies_without_writing_inbox(tmp_path) -> None:
    async def scenario() -> None:
        engine, factory = await _factory(tmp_path / "bind.db")
        try:
            harness = _Harness(factory)
            await _bind(harness)

            accepted = await harness.service.handle_inbound(
                _inbound("/help", message_id="m-help")
            )

            assert accepted is False
            assert await _count(factory, AgentInbox) == 0
            assert [row.payload["text"] for row in await _outbox(factory)] == [
                BIND_OK_TEXT,
                TEXT_ONLY_TEXT,
            ]
        finally:
            await engine.dispose()

    asyncio.run(scenario())


def test_unbound_message_is_dropped(tmp_path) -> None:
    async def scenario() -> None:
        engine, factory = await _factory(tmp_path / "bind.db")
        try:
            harness = _Harness(factory)

            accepted = await harness.service.handle_inbound(
                _inbound("我是陌生人", message_id="m-stranger")
            )

            assert accepted is False
            assert await _count(factory, AgentTrigger) == 0
            assert await _count(factory, AgentInbox) == 0
            assert await _outbox(factory) == []
        finally:
            await engine.dispose()

    asyncio.run(scenario())


def test_invalid_platform_is_rejected(tmp_path) -> None:
    async def scenario() -> None:
        engine, factory = await _factory(tmp_path / "bind.db")
        try:
            harness = _Harness(factory)
            try:
                await harness.service.create_bind_code("slack")
            except AppError as exc:
                assert exc.code == ErrorCode.BIND_CODE_INVALID
            else:  # pragma: no cover - 失败时给明确断言
                raise AssertionError("不存在的平台应报 BIND_CODE_INVALID")
        finally:
            await engine.dispose()

    asyncio.run(scenario())


# ---------------------------------------------------------------------------
# 发件箱与数据方法
# ---------------------------------------------------------------------------
def test_enqueue_reply_and_enqueue_for_bindings_dedupe(tmp_path) -> None:
    async def scenario() -> None:
        engine, factory = await _factory(tmp_path / "bind.db")
        try:
            harness = _Harness(factory)
            await _bind(harness)
            harness.advance(timedelta(minutes=1))

            row = await harness.service.enqueue_reply(
                CHAT, "telegram", "一条回复", dedupe_key="reply:run-1"
            )
            again = await harness.service.enqueue_reply(
                CHAT, "telegram", "一条回复", dedupe_key="reply:run-1"
            )
            assert row.id == again.id

            async with factory() as session:
                repo = OutboxRepository(session)
                page = await repo.page()
                assert page.total == 2  # 绑定回执 + 这条回复
                assert (await repo.counts())[OutboxStatus.PENDING.value] == 2
                assert len(await repo.due(harness.now)) == 2

            # 给所有 active 绑定各写一条；同一 source 重复调用不再加行。
            rows = await harness.service.enqueue_for_bindings(
                OutboxKind.REPORT, "report-1", "日报"
            )
            assert len(rows) == 1
            assert rows[0].payload["text"] == "日报"
            rows = await harness.service.enqueue_for_bindings(
                OutboxKind.REPORT, "report-1", "日报"
            )
            assert len(rows) == 1
            async with factory() as session:
                total = await session.scalar(
                    select(func.count()).select_from(Outbox)
                )
                assert int(total or 0) == 3
        finally:
            await engine.dispose()

    asyncio.run(scenario())


def test_channel_data_methods(tmp_path) -> None:
    async def scenario() -> None:
        engine, factory = await _factory(tmp_path / "bind.db")
        try:
            harness = _Harness(factory)
            channels = {item["platform"]: item for item in await harness.service.list_channels()}
            assert set(channels) == {"telegram", "feishu", "dingtalk", "qq"}
            assert all(item["enabled"] is False for item in channels.values())
            assert all(item["configured"] is False for item in channels.values())

            # 未启用/未配置：503 口径。
            try:
                await harness.service.test_channel("telegram")
            except AppError as exc:
                assert exc.code == ErrorCode.CHANNEL_NOT_CONFIGURED
            else:  # pragma: no cover - 失败时给明确断言
                raise AssertionError("未配置的通道不应能发测试消息")

            await _bind(harness)
            harness.settings.channels.telegram_enabled = True
            harness.settings.channels.telegram_bot_token = "tg-token"
            channels = {item["platform"]: item for item in await harness.service.list_channels()}
            assert channels["telegram"]["enabled"] is True
            assert channels["telegram"]["configured"] is True

            assert await harness.service.test_channel("telegram") == 1
            async with factory() as session:
                due = await OutboxRepository(session).due(harness.now)
            assert any(row.payload["text"] == TEST_TEXT for row in due)

            bindings = await harness.service.list_bindings()
            assert [binding.external_chat_id for binding in bindings] == [CHAT]
            await harness.service.revoke_binding(bindings[0].id)
            assert await harness.service.list_bindings() == []
            assert len(await harness.service.list_bindings(status=None)) == 1
            try:
                await harness.service.revoke_binding(bindings[0].id)
            except AppError as exc:
                assert exc.code == ErrorCode.CHANNEL_BINDING_NOT_FOUND
            else:  # pragma: no cover
                raise AssertionError("重复撤销应报 404 口径")
        finally:
            await engine.dispose()

    asyncio.run(scenario())
