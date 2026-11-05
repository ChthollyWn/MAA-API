"""IM 通道与发件箱 REST 契约（M85 规格 §4.3、§6）。

覆盖 §6 的 ``test_secret_roundtrip_masked``（通道密钥走与 ``llm.api_key`` 同一条
Fernet 路径、接口回显 ``***``），并补六个端点、错误码、分页、401 与服务未装配
503。应用按最小生产集现搭：真实 ``ChannelService`` / ``OutboxWorker`` /
``SettingService``，只有平台适配器是不联网的 ``FakeAdapter``。
"""

from __future__ import annotations

from datetime import datetime, timedelta
from types import SimpleNamespace
from typing import Any

import pytest
from cryptography.fernet import Fernet
from fastapi import FastAPI
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker
from sqlmodel import SQLModel

import maa_api.db.models  # noqa: F401  # 注册全部表
import maa_api.settings as settings_module
from maa_api.agent.channels.fake import FakeAdapter
from maa_api.agent.channels.outbox import OutboxWorker
from maa_api.agent.channels.service import ChannelService
from maa_api.api import deps
from maa_api.api.errors import register_exception_handlers
from maa_api.api.routers import channels as channels_router
from maa_api.api.routers import settings as settings_router
from maa_api.db import session as db_session
from maa_api.db.repositories.channels import ChannelBindingRepository
from maa_api.domain.enums import (
    ChannelPlatform,
    OutboxChannel,
    OutboxKind,
)
from maa_api.services.setting_service import SettingService

NOW = datetime(2026, 11, 5, 12, 0, 0)
USER = "im-user-1"
CHAT = "chat-1"


class _Trigger:
    async def publish(self, **_kwargs: Any) -> None:
        return None


def _service(
    factory: async_sessionmaker[AsyncSession],
    settings: Any,
    adapters: dict[ChannelPlatform, FakeAdapter],
) -> ChannelService:
    def adapter_for(channel: str) -> FakeAdapter | None:
        if not str(channel).startswith("im_"):
            return None
        try:
            return adapters.get(ChannelPlatform(str(channel)[3:]))
        except ValueError:
            return None

    worker = OutboxWorker(factory, adapter_for, clock=lambda: NOW)
    return ChannelService(
        factory,
        outbox_worker=worker,
        trigger_service=_Trigger(),
        thread_router=SimpleNamespace(),
        inbox_service=SimpleNamespace(),
        settings_provider=lambda: settings,
        adapters=adapters,
        clock=lambda: NOW,
    )


async def _create_schema(engine: Any) -> None:
    async with engine.begin() as connection:
        await connection.run_sync(SQLModel.metadata.create_all)


def _build_app(
    isolated_db: Any,
    make_client: Any,
    settings: Any,
    *,
    with_service: bool = True,
    encryption_key: str | None = None,
):
    app = FastAPI()
    register_exception_handlers(app)
    app.include_router(channels_router.router)
    app.include_router(settings_router.router)
    app.state.setting_service = SettingService(
        db_session.session_factory,
        settings_path=settings_module.DEFAULT_CONFIG_PATH,
        env=(
            {"MAA_LLM_ENCRYPTION_KEY": encryption_key}
            if encryption_key is not None
            else {}
        ),
    )
    adapters = {
        ChannelPlatform.TELEGRAM: FakeAdapter(ChannelPlatform.TELEGRAM)
    }
    if with_service:
        app.state.channel_service = _service(
            db_session.session_factory, settings, adapters
        )
    client = make_client(app)
    client.portal.call(_create_schema, isolated_db)
    return app, client, adapters


async def _seed_binding(
    factory: async_sessionmaker[AsyncSession],
    *,
    chat: str = CHAT,
    user: str = USER,
) -> str:
    async with factory() as session:
        row = await ChannelBindingRepository(session).create(
            platform=ChannelPlatform.TELEGRAM.value,
            external_user_id=user,
            external_chat_id=chat,
            now=NOW,
        )
        await session.commit()
        return str(row.id)


async def _seed_outbox(
    factory: async_sessionmaker[AsyncSession],
    *,
    worker: OutboxWorker,
    count: int,
) -> None:
    async with factory() as session:
        for index in range(count):
            await worker.enqueue(
                session,
                channel=OutboxChannel.IM_TELEGRAM,
                target=CHAT,
                kind=OutboxKind.REPLY,
                payload={"text": f"回复 {index}", "platform": "telegram"},
                dedupe_key=f"reply:seed-{index}",
                now=NOW,
            )
        await session.commit()


def _error(response: Any) -> dict[str, Any]:
    return response.json()["error"]


# ---------------------------------------------------------------------------
# §6 的密钥回显
# ---------------------------------------------------------------------------
def test_secret_roundtrip_masked(
    isolated_db, make_client, tmp_settings
) -> None:
    """PUT 通道密钥后 GET 只回 ``***``；库内是 Fernet 密文，明文不进响应。"""
    key = Fernet.generate_key().decode("ascii")
    _app, client, _adapters = _build_app(
        isolated_db, make_client, tmp_settings, encryption_key=key
    )

    response = client.put(
        "/api/settings",
        json={
            "items": {
                "channels.telegram_bot_token": "tg-secret",
                "channels.feishu_app_secret": "fs-secret",
                "channels.feishu_app_id": "cli_public",
            }
        },
    )
    assert response.status_code == 200, response.text
    assert "tg-secret" not in response.text
    assert "fs-secret" not in response.text

    current = client.get("/api/settings")
    assert current.status_code == 200
    items = {item["key"]: item for item in current.json()["items"]}
    for secret_key in (
        "channels.telegram_bot_token",
        "channels.feishu_app_secret",
    ):
        assert items[secret_key]["value"] == "***"
        assert items[secret_key]["source"] == "db"
    assert items["channels.feishu_app_id"]["value"] == "cli_public"
    assert "tg-secret" not in current.text
    assert "fs-secret" not in current.text

    async def read_stored() -> dict[str, str]:
        from maa_api.db.repositories.setting import SettingRepository

        async with db_session.session_factory() as session:
            return {
                setting_key: await SettingRepository(session).get(setting_key)
                for setting_key in (
                    "channels.telegram_bot_token",
                    "channels.feishu_app_secret",
                )
            }

    stored = client.portal.call(read_stored)
    for plaintext in ("tg-secret", "fs-secret"):
        assert plaintext not in str(stored)
    assert all(value.startswith("enc:v1:") for value in stored.values())


# ---------------------------------------------------------------------------
# 六个端点
# ---------------------------------------------------------------------------
def test_channels_and_bind_codes(isolated_db, make_client, tmp_settings) -> None:
    _app, client, _adapters = _build_app(isolated_db, make_client, tmp_settings)

    response = client.get("/api/agent/channels")
    assert response.status_code == 200
    rows = {item["platform"]: item for item in response.json()}
    assert set(rows) == {"telegram", "feishu", "dingtalk", "qq"}
    assert rows["telegram"]["enabled"] is False
    assert rows["telegram"]["configured"] is False
    assert rows["telegram"]["connected"] is False
    assert "token" not in response.text.lower()

    created = client.post("/api/agent/channels/telegram/bind-codes")
    assert created.status_code == 201, created.text
    body = created.json()
    assert len(body["code"]) == 8
    assert body["expires_at"].endswith("Z")
    expires = datetime.fromisoformat(body["expires_at"].replace("Z", "+00:00"))
    assert expires == (NOW + timedelta(minutes=10)).replace(
        tzinfo=expires.tzinfo
    )

    invalid = client.post("/api/agent/channels/telegramx/bind-codes")
    assert invalid.status_code == 400
    assert _error(invalid)["code"] == "BIND_CODE_INVALID"


def test_bindings_list_and_revoke(isolated_db, make_client, tmp_settings) -> None:
    _app, client, _adapters = _build_app(isolated_db, make_client, tmp_settings)
    binding_id = client.portal.call(_seed_binding, db_session.session_factory)

    listed = client.get("/api/agent/channels/bindings")
    assert listed.status_code == 200
    items = listed.json()["items"]
    assert len(items) == 1
    assert items[0]["id"] == binding_id
    assert items[0]["platform"] == "telegram"
    assert items[0]["external_chat_id"] == CHAT
    assert items[0]["bound_at"].endswith("Z")
    assert "secret" not in listed.text.lower()
    assert "token" not in listed.text.lower()

    revoked = client.delete(f"/api/agent/channels/bindings/{binding_id}")
    assert revoked.status_code == 204
    assert client.get("/api/agent/channels/bindings").json()["items"] == []

    missing = client.delete(f"/api/agent/channels/bindings/{binding_id}")
    assert missing.status_code == 404
    assert _error(missing)["code"] == "CHANNEL_BINDING_NOT_FOUND"


def test_test_channel_errors_and_success(
    isolated_db, make_client, tmp_settings
) -> None:
    app, client, adapters = _build_app(
        isolated_db, make_client, tmp_settings
    )
    client.portal.call(_seed_binding, db_session.session_factory)
    adapter = adapters[ChannelPlatform.TELEGRAM]

    # 未启用/未配置 → 503。
    not_configured = client.post("/api/agent/channels/telegram/test")
    assert not_configured.status_code == 503
    assert _error(not_configured)["code"] == "CHANNEL_NOT_CONFIGURED"

    tmp_settings.channels.telegram_enabled = True
    tmp_settings.channels.telegram_bot_token = "tg-token"

    adapter.fatal = True
    failed = client.post("/api/agent/channels/telegram/test")
    assert failed.status_code == 502
    assert _error(failed)["code"] == "CHANNEL_SEND_FAILED"

    adapter.fatal = False
    ok = client.post("/api/agent/channels/telegram/test")
    assert ok.status_code == 200
    assert ok.json() == {"sent": 1}
    assert adapter.sent[-1] == (CHAT, "这是一条来自 MAA-API 的测试消息。")

    invalid = client.post("/api/agent/channels/telegramx/test")
    assert invalid.status_code == 400
    assert _error(invalid)["code"] == "BIND_CODE_INVALID"


def test_outbox_filters_paginates_and_limits(
    isolated_db, make_client, tmp_settings
) -> None:
    _app, client, _adapters = _build_app(isolated_db, make_client, tmp_settings)
    service = client.app.state.channel_service

    async def seed() -> None:
        await _seed_outbox(
            db_session.session_factory,
            worker=service._outbox,
            count=3,
        )

    client.portal.call(seed)

    page = client.get("/api/agent/outbox", params={"page": 1, "size": 2})
    assert page.status_code == 200
    body = page.json()
    assert body["total"] == 3
    assert (body["page"], body["size"]) == (1, 2)
    assert len(body["items"]) == 2
    assert body["items"][0]["channel"] == "im_telegram"
    assert body["items"][0]["status"] == "pending"
    assert body["items"][0]["created_at"].endswith("Z")

    filtered = client.get(
        "/api/agent/outbox",
        params={"channel": "im_telegram", "status": "sent"},
    )
    assert filtered.status_code == 200
    assert filtered.json()["total"] == 0

    for params in ({"page": 0}, {"size": 0}, {"size": 201}):
        invalid = client.get("/api/agent/outbox", params=params)
        assert invalid.status_code == 400, params
        assert _error(invalid)["code"] == "INVALID_PAGINATION"


def test_missing_service_returns_503(isolated_db, make_client, tmp_settings) -> None:
    _app, client, _adapters = _build_app(
        isolated_db, make_client, tmp_settings, with_service=False
    )
    response = client.get("/api/agent/channels")
    assert response.status_code == 503
    assert _error(response)["code"] == "SERVICE_UNAVAILABLE"


@pytest.mark.parametrize(
    "method,path",
    [
        ("get", "/api/agent/channels"),
        ("post", "/api/agent/channels/telegram/bind-codes"),
        ("get", "/api/agent/channels/bindings"),
        ("delete", "/api/agent/channels/bindings/x"),
        ("post", "/api/agent/channels/telegram/test"),
        ("get", "/api/agent/outbox"),
    ],
)
def test_requires_auth(
    isolated_db, make_client, tmp_settings, method: str, path: str
) -> None:
    _app, client, _adapters = _build_app(isolated_db, make_client, tmp_settings)
    tmp_settings.access_token = "secret-token"
    deps.reset_rate_limiter()
    try:
        response = getattr(client, method)(path)
        assert response.status_code == 401
        assert _error(response)["code"] == "UNAUTHORIZED"
    finally:
        deps.reset_rate_limiter()


