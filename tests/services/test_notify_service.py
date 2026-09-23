"""Offline persistence and delivery tests for notification channels."""

from __future__ import annotations

import asyncio
from typing import Any

import pytest
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker
from sqlalchemy.pool import NullPool
from sqlmodel import SQLModel

import maa_api.db.models  # noqa: F401
from maa_api.db import session as db_session
from maa_api.domain.enums import NotifyChannelType, NotifyEvent
from maa_api.domain.errors import AppError, ErrorCode
from maa_api.services.notify_service import NotifyService, _render_template, channel_wire
from maa_api.db.session import make_engine


@pytest.fixture
def notify_session_factory(tmp_path):
    engine = make_engine(
        f"sqlite+aiosqlite:///{tmp_path / 'notify.db'}", poolclass=NullPool
    )

    async def create_schema():
        async with engine.begin() as connection:
            await connection.run_sync(SQLModel.metadata.create_all)

    asyncio.run(create_schema())
    factory = async_sessionmaker(engine, expire_on_commit=False, class_=AsyncSession)
    yield factory
    engine.sync_engine.dispose()


class _HttpSender:
    def __init__(self, status_code: int = 200):
        self.status_code = status_code
        self.calls: list[tuple[str, str, dict[str, Any]]] = []

    async def request(self, method: str, url: str, **kwargs):
        self.calls.append((method, url, kwargs))
        return {"status_code": self.status_code, "text": "upstream"}


def test_validates_by_channel_type_and_masks_config(notify_session_factory):
    service = NotifyService(notify_session_factory)

    async def scenario():
        with pytest.raises(AppError) as caught:
            await service.create_channel(
                type=NotifyChannelType.BARK,
                name="bad",
                config={"server": "file:///tmp", "device_key": "secret"},
                events=[NotifyEvent.UPDATE_AVAILABLE],
            )
        assert caught.value.code == ErrorCode.NOTIFY_CONFIG_INVALID

        channel = await service.create_channel(
            type=NotifyChannelType.WEBHOOK,
            name="deploy",
            config={
                "url": "https://example.test/hook?token=secret",
                "headers": {"Authorization": "Bearer abc", "X-Label": "ok"},
                "body_template": "{title}: {targets}",
            },
            events=[NotifyEvent.UPDATE_AVAILABLE],
        )
        wire = channel_wire(channel)
        assert wire["config"]["headers"]["Authorization"] == "***"
        assert "secret" not in wire["config"]["url"]
        assert "***" in wire["config"]["url"]

        updated = await service.update_channel(
            channel.id,
            type=NotifyChannelType.WEBHOOK,
            name="deploy",
            config={
                "url": "https://example.test/hook?token=***",
                "headers": {"Authorization": "***", "X-Label": "changed"},
                "body_template": "{title}: {targets}",
            },
            events=[NotifyEvent.UPDATE_AVAILABLE],
            enabled=True,
        )
        assert updated.config["headers"]["Authorization"] == "Bearer abc"
        assert updated.config["url"] == "https://example.test/hook?token=secret"

        with pytest.raises(AppError) as conflict:
            await service.create_channel(
                type=NotifyChannelType.WEBHOOK,
                name="deploy",
                config={"url": "https://example.test"},
                events=[],
            )
        assert conflict.value.code == ErrorCode.NOTIFY_CHANNEL_CONFLICT

        dingtalk = await service.create_channel(
            type=NotifyChannelType.DINGTALK,
            name="ding",
            config={"webhook": "https://example.test/robot/send?access_token=private", "secret": "signing-secret"},
            events=[],
        )
        ding_wire = channel_wire(dingtalk)
        assert ding_wire["config"]["webhook"] == "https://example.test/***"
        assert ding_wire["config"]["secret"] == "***"
        ding_updated = await service.update_channel(
            dingtalk.id,
            type=NotifyChannelType.DINGTALK,
            name="ding",
            config={"webhook": ding_wire["config"]["webhook"], "secret": "***"},
            events=[],
            enabled=True,
        )
        assert ding_updated.config["webhook"].endswith("access_token=private")
        assert ding_updated.config["secret"] == "signing-secret"

    asyncio.run(scenario())


def test_dispatches_each_subscribed_event_once_and_records_failures(notify_session_factory):
    transport = _HttpSender(status_code=503)
    service = NotifyService(notify_session_factory, http_sender=transport)

    async def scenario():
        subscribed = await service.create_channel(
            type=NotifyChannelType.WEBHOOK,
            name="updates",
            config={"url": "https://example.test/hook", "body_template": "{title} {targets}"},
            events=[NotifyEvent.UPDATE_AVAILABLE],
        )
        await service.create_channel(
            type=NotifyChannelType.WEBHOOK,
            name="pipelines",
            config={"url": "https://example.test/pipeline"},
            events=[NotifyEvent.PIPELINE_COMPLETED],
        )
        result = await service.send_event(
            NotifyEvent.UPDATE_AVAILABLE,
            {"targets": [{"target": "core"}, {"target": "resource"}]},
        )
        assert result == {"matched": 1, "sent": 0, "failed": 1}
        assert len(transport.calls) == 1
        assert "core" in transport.calls[0][2]["content"].decode()
        assert "resource" in transport.calls[0][2]["content"].decode()
        assert transport.calls[0][2]["timeout"] == 10.0

        stored = await service.get_channel(subscribed.id)
        assert stored.enabled is True
        assert stored.last_status == "failed"
        assert stored.last_error.startswith("HTTP 503")

    asyncio.run(scenario())


def test_supports_all_five_transports_with_injected_clients(notify_session_factory):
    calls: list[tuple[dict[str, Any], str, str, float]] = []

    async def smtp(config, subject, body, *, timeout):
        calls.append((dict(config), subject, body, timeout))

    http = _HttpSender()
    service = NotifyService(notify_session_factory, http_sender=http, smtp_sender=smtp)

    async def scenario():
        configs = [
            (NotifyChannelType.EMAIL, {"server": "mail.test", "port": 465, "to": ["x@test"], "username": "u", "password": "p"}),
            (NotifyChannelType.WEBHOOK, {"url": "https://example.test/hook"}),
            (NotifyChannelType.BARK, {"device_key": "device"}),
            (NotifyChannelType.DINGTALK, {"webhook": "https://example.test/ding", "secret": "secret"}),
            (NotifyChannelType.WECOM, {"webhook": "https://example.test/wecom"}),
        ]
        channels = []
        for channel_type, config in configs:
            channels.append(await service.create_channel(
                type=channel_type,
                name=channel_type.value,
                config=config,
                events=[NotifyEvent.PIPELINE_COMPLETED],
            ))
        for channel in channels:
            assert (await service.test_channel(channel.id))["sent"] is True

        assert len(calls) == 1
        assert calls[0][3] == 10.0
        assert len(http.calls) == 4
        methods = {call[0] for call in http.calls}
        assert methods == {"POST"}

    asyncio.run(scenario())


def test_test_send_writes_failure_and_rejects_template_execution(notify_session_factory):
    sender = _HttpSender(status_code=500)
    service = NotifyService(notify_session_factory, http_sender=sender)

    async def scenario():
        channel = await service.create_channel(
            type=NotifyChannelType.WEBHOOK,
            name="broken",
            config={"url": "https://example.test", "body_template": "{{7*7}}"},
            events=[NotifyEvent.PIPELINE_COMPLETED],
        )
        with pytest.raises(ValueError):
            _render_template("{{7*7}}", {"title": "x"})
        with pytest.raises(AppError) as caught:
            await service.test_channel(channel.id)
        assert caught.value.code == ErrorCode.NOTIFY_SEND_FAILED
        stored = await service.get_channel(channel.id)
        assert stored.enabled is True
        assert stored.last_status == "failed"

    asyncio.run(scenario())
