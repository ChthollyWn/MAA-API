"""REST integration tests for notification channels using offline transports."""

from __future__ import annotations

import asyncio
from typing import Any

import maa_api.db.models  # noqa: F401
from fastapi import FastAPI
import pytest
from sqlalchemy.ext.asyncio import AsyncEngine
from sqlmodel import SQLModel

from maa_api.api.errors import register_exception_handlers
from maa_api.api.routers import notifications as notifications_router
from maa_api.db import session as db_session
from maa_api.domain.errors import ErrorCode
from maa_api.services.notify_service import NotifyService


class _HttpSender:
    def __init__(self, status_code: int = 200):
        self.status_code = status_code
        self.calls: list[tuple[str, str, dict[str, Any]]] = []

    async def request(self, method: str, url: str, **kwargs):
        self.calls.append((method, url, kwargs))
        return {"status_code": self.status_code, "text": "offline test response"}


def _notification_client(isolated_db: AsyncEngine, make_client, status_code: int = 200):
    async def create_schema():
        async with isolated_db.begin() as connection:
            await connection.run_sync(SQLModel.metadata.create_all)

    asyncio.run(create_schema())
    isolated_db.sync_engine.dispose()
    transport = _HttpSender(status_code)
    app = FastAPI()
    register_exception_handlers(app)
    app.include_router(notifications_router.router)
    app.state.notify_service = NotifyService(
        db_session.session_factory,
        http_sender=transport,
        smtp_sender=lambda *_args, **_kwargs: None,
    )
    return make_client(app), transport


def test_notification_channel_crud_and_test_send(isolated_db, make_client, tmp_settings):
    client, transport = _notification_client(isolated_db, make_client)
    payload = {
        "type": "webhook",
        "name": "更新消息",
        "config": {
            "url": "https://example.test/hook?token=private",
            "headers": {"Authorization": "Bearer private"},
            "body_template": "{title}: {targets}",
        },
        "events": ["update_available"],
        "enabled": True,
    }

    created = client.post("/api/notifications/channels", json=payload)
    assert created.status_code == 201
    assert created.headers["location"].endswith(created.json()["id"])
    channel_id = created.json()["id"]
    wire = created.json()
    assert wire["config"]["headers"]["Authorization"] == "***"
    assert "private" not in wire["config"]["url"]
    assert wire["events"] == ["update_available"]

    listed = client.get("/api/notifications/channels", params={"enabled": True})
    assert listed.status_code == 200
    assert listed.json()["total"] == 1
    assert listed.json()["items"][0]["id"] == channel_id

    tested = client.post(
        f"/api/notifications/channels/{channel_id}/test",
        json={"event": "update_available"},
    )
    assert tested.status_code == 200
    assert tested.json() == {"sent": True, "event": "update_available"}
    assert len(transport.calls) == 1
    assert transport.calls[0][2]["timeout"] == 10.0

    updated = {**payload, "name": "更新消息二", "enabled": False}
    result = client.put(f"/api/notifications/channels/{channel_id}", json=updated)
    assert result.status_code == 200
    assert result.json()["name"] == "更新消息二"
    assert result.json()["enabled"] is False

    deleted = client.delete(f"/api/notifications/channels/{channel_id}")
    assert deleted.status_code == 204
    missing = client.delete(f"/api/notifications/channels/{channel_id}")
    assert missing.status_code == 404
    assert missing.json()["error"]["code"] == ErrorCode.NOTIFY_CHANNEL_NOT_FOUND


def test_invalid_config_send_failure_and_auth_shape(isolated_db, make_client, tmp_settings):
    client, _transport = _notification_client(isolated_db, make_client, status_code=503)
    invalid = client.post(
        "/api/notifications/channels",
        json={
            "type": "bark",
            "name": "invalid",
            "config": {"server": "file:///tmp", "device_key": "secret"},
            "events": ["update_available"],
        },
    )
    assert invalid.status_code == 422
    assert invalid.json()["error"]["code"] == ErrorCode.NOTIFY_CONFIG_INVALID

    created = client.post(
        "/api/notifications/channels",
        json={
            "type": "webhook",
            "name": "bad upstream",
            "config": {"url": "https://example.test"},
            "events": ["pipeline_completed"],
        },
    )
    channel_id = created.json()["id"]
    failed = client.post(f"/api/notifications/channels/{channel_id}/test")
    assert failed.status_code == 502
    assert failed.json()["error"]["code"] == ErrorCode.NOTIFY_SEND_FAILED

    missing = client.post("/api/notifications/channels/not-found/test")
    assert missing.status_code == 404
    assert missing.json()["error"]["code"] == ErrorCode.NOTIFY_CHANNEL_NOT_FOUND


@pytest.mark.parametrize("tmp_settings", ["notify-secret"], indirect=True)
def test_routes_require_auth_when_configured(isolated_db, make_client, tmp_settings):
    client, _transport = _notification_client(isolated_db, make_client)
    assert client.get("/api/notifications/channels").status_code == 401
    allowed = client.get(
        "/api/notifications/channels",
        headers={"Authorization": "Bearer notify-secret"},
    )
    assert allowed.status_code == 200
