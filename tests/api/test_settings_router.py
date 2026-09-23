"""Settings HTTP API integration tests using an isolated app and SQLite DB."""

from __future__ import annotations

import asyncio
from typing import Any

import pytest
from fastapi import FastAPI
from sqlalchemy.ext.asyncio import AsyncEngine
from sqlmodel import SQLModel

import maa_api.db.models  # noqa: F401
import maa_api.settings as settings_module
from maa_api.api.errors import register_exception_handlers
from maa_api.api.routers import settings as settings_router
from maa_api.db import session as db_session
from maa_api.domain.errors import ErrorCode
from maa_api.services.setting_service import SettingService


class _DeviceManager:
    def __init__(self, result: bool = True):
        self.result = result
        self.calls: list[dict[str, Any]] = []

    async def reconfigure(self, **kwargs):
        self.calls.append(kwargs)
        return self.result


@pytest.fixture
def setting_client(isolated_db: AsyncEngine, tmp_settings, make_client):
    async def create_schema():
        async with isolated_db.begin() as connection:
            await connection.run_sync(SQLModel.metadata.create_all)

    asyncio.run(create_schema())
    isolated_db.sync_engine.dispose()
    manager = _DeviceManager()
    app = FastAPI()
    register_exception_handlers(app)
    app.include_router(settings_router.router)
    app.state.setting_service = SettingService(
        db_session.session_factory,
        device_manager=manager,
        settings_path=settings_module.DEFAULT_CONFIG_PATH,
        env={},
    )
    return make_client(app), manager


def test_get_settings_and_schema(setting_client):
    client, _manager = setting_client
    response = client.get("/api/settings")
    assert response.status_code == 200
    body = response.json()
    items = {item["key"]: item for item in body["items"]}
    assert items["app.access_token"]["value"] == "***"
    assert items["adb.address"]["source"] == "yaml"
    assert items["adb.common_ports"]["value"] == [5555, 5556, 7555, 16384, 21503, 62001]

    schema = client.get("/api/settings/schema")
    assert schema.status_code == 200
    fields = {item["key"]: item for item in schema.json()["items"]}
    assert fields["app.access_token"]["readonly"] is True
    assert fields["adb.path"]["readonly"] is True
    assert "BlueStacks" in fields["adb.connection_config"]["enum"]


def test_put_reset_and_readonly(setting_client):
    client, manager = setting_client
    response = client.put(
        "/api/settings",
        json={"items": {"adb.address": "192.0.2.4:5555", "adb.common_ports": [7000]}},
    )
    assert response.status_code == 200
    assert manager.calls == [{"address": "192.0.2.4:5555"}]
    values = {item["key"]: item for item in response.json()["items"]}
    assert values["adb.address"]["value"] == "192.0.2.4:5555"
    assert values["adb.address"]["source"] == "db"

    readonly = client.put("/api/settings", json={"items": {"adb.path": "/tmp/adb"}})
    assert readonly.status_code == 403
    assert readonly.json()["error"]["code"] == ErrorCode.SETTING_READONLY

    reset = client.post("/api/settings/reset", json={"keys": ["adb.address"]})
    assert reset.status_code == 200
    values = {item["key"]: item for item in reset.json()["items"]}
    assert values["adb.address"]["value"] == "127.0.0.1:5555"
    assert values["adb.address"]["source"] == "yaml"


def test_failed_hot_apply_keeps_saved_setting(setting_client):
    client, manager = setting_client
    manager.result = False
    response = client.put("/api/settings", json={"items": {"adb.address": "offline:5555"}})
    assert response.status_code == 500
    assert response.json()["error"]["code"] == ErrorCode.SETTING_APPLY_FAILED
    assert response.json()["error"]["details"]["applied"] is True
    assert manager.calls == [{"address": "offline:5555"}]

    current = client.get("/api/settings").json()
    address = next(item for item in current["items"] if item["key"] == "adb.address")
    assert address["value"] == "offline:5555"
    assert address["source"] == "db"
