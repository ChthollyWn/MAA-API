"""M85 T4：IM 确认答复的边界（spec §4.5「确认」、§6 的两条）。

覆盖 §6 的 ``test_destructive_not_approved_from_im`` 与
``test_exact_phrase_approves_non_destructive``，另补：拒绝短语、单独「同意」不
算批准、未知前缀不写收件箱、放开 ``channels.allow_destructive_approval`` 后可以
在 IM 里批准。

测试用真实 SQLite 与真实 ``ConfirmationService.resolve``；只有事件总线与平台
适配器是替身，不联网、不碰真实库。
"""

from __future__ import annotations

import asyncio
from datetime import datetime, timedelta
from types import SimpleNamespace
from typing import Any

from sqlalchemy import select

from maa_api.agent.channels.adapter import Inbound
from maa_api.agent.channels.fake import FakeAdapter
from maa_api.agent.channels.outbox import OutboxWorker
from maa_api.agent.channels.service import ChannelService
from maa_api.agent.confirmation import ConfirmationService
from maa_api.agent.registry import ToolRegistry
from maa_api.db.models import Confirmation, Outbox
from maa_api.db.repositories.channels import ChannelBindingRepository
from maa_api.domain.enums import (
    ChannelPlatform,
    ConfirmationStatus,
    OutboxKind,
    RiskLevel,
)
from maa_api.settings import ChannelsSettings, Settings
from tests.fakes.v3_db import make_session_factory

NOW = datetime(2026, 11, 5, 12, 0, 0)
USER = "im-user-1"
CHAT = "chat-1"
CONSUME_ID = "abcd1234-1111-4000-8000-000000000001"
DESTRUCTIVE_ID = "beef5678-2222-4000-8000-000000000002"


class _Trigger:
    """事件总线替身：确认分支不应调用它，普通文本应发布 im.message。"""

    def __init__(self) -> None:
        self.published: list[dict[str, Any]] = []

    async def publish(self, **kwargs: Any) -> None:
        self.published.append(kwargs)


class _Harness:
    def __init__(self, factory: Any, *, allow_destructive: bool = False) -> None:
        self.factory = factory
        self.settings = Settings(
            channels=ChannelsSettings(
                allow_destructive_approval=allow_destructive
            )
        )
        self.trigger = _Trigger()
        self.adapter = FakeAdapter(ChannelPlatform.TELEGRAM)
        self.worker = OutboxWorker(
            factory, lambda _channel: self.adapter, clock=lambda: NOW
        )
        self.confirmations = ConfirmationService(
            factory, ToolRegistry(), clock=lambda: NOW
        )
        self.service = ChannelService(
            factory,
            outbox_worker=self.worker,
            trigger_service=self.trigger,
            thread_router=SimpleNamespace(),
            inbox_service=SimpleNamespace(),
            settings_provider=lambda: self.settings,
            adapters={ChannelPlatform.TELEGRAM: self.adapter},
            confirmation_service=self.confirmations,
            clock=lambda: NOW,
        )


def _inbound(text: str, message_id: str) -> Inbound:
    return Inbound(
        platform=ChannelPlatform.TELEGRAM,
        external_user_id=USER,
        external_chat_id=CHAT,
        text=text,
        message_id=message_id,
    )


async def _seed_binding(factory: Any) -> str:
    async with factory() as session:
        row = await ChannelBindingRepository(session).create(
            platform=ChannelPlatform.TELEGRAM.value,
            external_user_id=USER,
            external_chat_id=CHAT,
            now=NOW,
        )
        await session.commit()
        return str(row.id)


async def _seed_confirmation(
    factory: Any,
    confirmation_id: str,
    *,
    risk: RiskLevel,
    expires_at: datetime | None = None,
) -> None:
    async with factory() as session:
        session.add(
            Confirmation(
                id=confirmation_id,
                action="click",
                risk_level=risk.value,
                reason="需要确认后才能继续",
                payload={"tool_name": "click"},
                status=ConfirmationStatus.PENDING,
                requested_by="agent",
                expires_at=expires_at or NOW + timedelta(minutes=5),
                created_at=NOW,
            )
        )
        await session.commit()


async def _confirmation(factory: Any, confirmation_id: str) -> Confirmation:
    async with factory() as session:
        row = await session.get(Confirmation, confirmation_id)
        assert row is not None
        return row


async def _outbox(factory: Any) -> list[Outbox]:
    async with factory() as session:
        result = await session.execute(select(Outbox).order_by(Outbox.id.asc()))
        return list(result.scalars().all())


# ---------------------------------------------------------------------------
# spec §6 两条
# ---------------------------------------------------------------------------
def test_destructive_not_approved_from_im(tmp_path) -> None:
    """破坏类在默认配置下不因 IM 文本通过，只回网页链接，确认保持 pending。"""

    async def scenario() -> None:
        harness = None
        engine, factory = await make_session_factory(tmp_path / "destructive.db")
        try:
            harness = _Harness(factory)
            await _seed_binding(factory)
            await _seed_confirmation(factory, DESTRUCTIVE_ID, risk=RiskLevel.DESTRUCTIVE)

            accepted = await harness.service.handle_inbound(
                _inbound(f"同意 {DESTRUCTIVE_ID[:8]}", "m-destructive")
            )

            assert accepted is True
            row = await _confirmation(factory, DESTRUCTIVE_ID)
            assert row.status == ConfirmationStatus.PENDING
            assert harness.trigger.published == []
            replies = await _outbox(factory)
            assert [item.payload["text"] for item in replies] == [
                f"这类确认只能在网页上处理：/?confirm={DESTRUCTIVE_ID}"
            ]
            assert replies[0].kind == OutboxKind.REPLY.value
        finally:
            if harness is not None:
                await harness.confirmations.close()
            await engine.dispose()

    asyncio.run(scenario())


def test_exact_phrase_approves_non_destructive(tmp_path) -> None:
    """只有精确「同意 <前 8 位>」才批准消耗类确认；不写收件箱。"""

    async def scenario() -> None:
        harness = None
        engine, factory = await make_session_factory(tmp_path / "consume.db")
        try:
            harness = _Harness(factory)
            await _seed_binding(factory)
            await _seed_confirmation(factory, CONSUME_ID, risk=RiskLevel.CONSUME)

            accepted = await harness.service.handle_inbound(
                _inbound(f"同意 {CONSUME_ID[:8]}", "m-consume")
            )

            assert accepted is True
            row = await _confirmation(factory, CONSUME_ID)
            assert row.status == ConfirmationStatus.APPROVED
            assert row.resolved_by == "im"
            assert harness.trigger.published == []
            replies = await _outbox(factory)
            assert [item.payload["text"] for item in replies] == ["已同意"]
        finally:
            if harness is not None:
                await harness.confirmations.close()
            await engine.dispose()

    asyncio.run(scenario())


# ---------------------------------------------------------------------------
# 边界补充
# ---------------------------------------------------------------------------
def test_plain_agreement_is_not_an_approval(tmp_path) -> None:
    """「同意」单独一条是普通文本，进总控收件箱，确认仍 pending。"""

    async def scenario() -> None:
        harness = None
        engine, factory = await make_session_factory(tmp_path / "plain.db")
        try:
            harness = _Harness(factory)
            await _seed_binding(factory)
            await _seed_confirmation(factory, CONSUME_ID, risk=RiskLevel.CONSUME)

            accepted = await harness.service.handle_inbound(
                _inbound("同意", "m-plain")
            )

            assert accepted is True
            row = await _confirmation(factory, CONSUME_ID)
            assert row.status == ConfirmationStatus.PENDING
            assert len(harness.trigger.published) == 1
            assert harness.trigger.published[0]["kind"] == "im.message"
            assert await _outbox(factory) == []
        finally:
            if harness is not None:
                await harness.confirmations.close()
            await engine.dispose()

    asyncio.run(scenario())


def test_reject_phrase_rejects_without_inbox(tmp_path) -> None:
    async def scenario() -> None:
        harness = None
        engine, factory = await make_session_factory(tmp_path / "reject.db")
        try:
            harness = _Harness(factory)
            await _seed_binding(factory)
            await _seed_confirmation(factory, CONSUME_ID, risk=RiskLevel.CONSUME)

            await harness.service.handle_inbound(
                _inbound(f"拒绝 {CONSUME_ID[:8]}", "m-reject")
            )

            row = await _confirmation(factory, CONSUME_ID)
            assert row.status == ConfirmationStatus.REJECTED
            assert harness.trigger.published == []
            replies = await _outbox(factory)
            assert [item.payload["text"] for item in replies] == ["已拒绝"]
        finally:
            if harness is not None:
                await harness.confirmations.close()
            await engine.dispose()

    asyncio.run(scenario())


def test_unknown_prefix_does_not_write_inbox(tmp_path) -> None:
    async def scenario() -> None:
        harness = None
        engine, factory = await make_session_factory(tmp_path / "unknown.db")
        try:
            harness = _Harness(factory)
            await _seed_binding(factory)

            await harness.service.handle_inbound(
                _inbound("同意 deadbeef", "m-unknown")
            )

            assert harness.trigger.published == []
            replies = await _outbox(factory)
            assert replies[0].payload["text"].startswith("没有找到这条待确认请求")
        finally:
            if harness is not None:
                await harness.confirmations.close()
            await engine.dispose()

    asyncio.run(scenario())


def test_destructive_allowed_when_setting_enabled(tmp_path) -> None:
    """显式放开后破坏类也能在 IM 批准（V3-D25 的开关语义）。"""

    async def scenario() -> None:
        harness = None
        engine, factory = await make_session_factory(tmp_path / "allowed.db")
        try:
            harness = _Harness(factory, allow_destructive=True)
            await _seed_binding(factory)
            await _seed_confirmation(factory, DESTRUCTIVE_ID, risk=RiskLevel.DESTRUCTIVE)

            await harness.service.handle_inbound(
                _inbound(f"同意 {DESTRUCTIVE_ID[:8]}", "m-allowed")
            )

            row = await _confirmation(factory, DESTRUCTIVE_ID)
            assert row.status == ConfirmationStatus.APPROVED
            replies = await _outbox(factory)
            assert [item.payload["text"] for item in replies] == ["已同意"]
        finally:
            if harness is not None:
                await harness.confirmations.close()
            await engine.dispose()

    asyncio.run(scenario())
