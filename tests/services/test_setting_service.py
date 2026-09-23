"""SettingService persistence, precedence, validation and hot-apply behavior."""

from __future__ import annotations

import asyncio
from pathlib import Path
from typing import Any

import pytest
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker
from sqlalchemy.pool import NullPool
from sqlmodel import SQLModel

import maa_api.db.models  # noqa: F401
from maa_api.db.session import make_engine
from maa_api.db.repositories.setting import SettingRepository
from maa_api.domain.errors import AppError, ErrorCode
from maa_api.services.setting_service import SettingService
from maa_api.settings import get_settings, set_settings


@pytest.fixture
def db_session_factory(tmp_path: Path):
    engine = make_engine(
        f"sqlite+aiosqlite:///{tmp_path / 'settings.db'}", poolclass=NullPool
    )

    async def create_tables():
        async with engine.begin() as connection:
            await connection.run_sync(SQLModel.metadata.create_all)

    asyncio.run(create_tables())
    factory = async_sessionmaker(engine, expire_on_commit=False, class_=AsyncSession)
    yield factory
    engine.sync_engine.dispose()


@pytest.fixture(autouse=True)
def _clear_settings_cache():
    set_settings(None)
    yield
    set_settings(None)


class _DeviceManager:
    def __init__(self, result: bool = True, error: Exception | None = None):
        self.result = result
        self.error = error
        self.calls: list[dict[str, Any]] = []

    async def reconfigure(self, **kwargs):
        self.calls.append(kwargs)
        if self.error:
            raise self.error
        return self.result


def test_refresh_resolves_yaml_db_environment_and_reports_sources(
    db_session_factory, tmp_path: Path
):
    path = tmp_path / "config.yaml"
    path.write_text(
        "app:\n  proxy: yaml-proxy\nadb:\n  address: yaml:5555\n  common_ports: [6000]\n",
        encoding="utf8",
    )

    async def scenario():
        async with db_session_factory() as session:
            repo = SettingRepository(session)
            await repo.set("app.proxy", "db-proxy")
            await repo.set("adb.address", "db:5555")
            await repo.set("adb.common_ports", [6001])
            await session.commit()
        service = SettingService(
            db_session_factory,
            settings_path=path,
            env={"MAA_APP_PROXY": "env-proxy"},
        )
        settings = await service.refresh()
        assert settings.proxy == "env-proxy"
        assert settings.adb.address == "db:5555"
        assert settings.adb.common_ports == [6001]
        view = await service.get()
        items = {item["key"]: item for item in view["items"]}
        assert items["app.proxy"]["source"] == "env"
        assert items["adb.address"]["source"] == "db"
        assert items["adb.common_ports"]["source"] == "db"
        assert items["adb.connection_config"]["source"] == "default"
        assert get_settings() == settings
        # Resolving defaults never writes them into the setting table.
        async with db_session_factory() as session:
            assert set((await SettingRepository(session).all())) == {
                "app.proxy", "adb.address", "adb.common_ports"
            }

    asyncio.run(scenario())


def test_update_validates_persists_and_reconfigures_address(db_session_factory, tmp_path):
    path = tmp_path / "config.yaml"
    path.write_text("adb:\n  address: old:5555\n", encoding="utf8")
    manager = _DeviceManager()

    async def scenario():
        service = SettingService(
            db_session_factory, device_manager=manager, settings_path=path, env={}
        )
        result = await service.update(
            {"adb.address": "new:5555", "adb.common_ports": [7000, 7001]}
        )
        assert manager.calls == [{"address": "new:5555"}]
        rows = {item["key"]: item for item in result["items"]}
        assert rows["adb.address"]["value"] == "new:5555"
        assert rows["adb.address"]["source"] == "db"
        assert rows["adb.common_ports"]["value"] == [7000, 7001]
        async with db_session_factory() as session:
            assert await SettingRepository(session).all() == {
                "adb.address": "new:5555", "adb.common_ports": [7000, 7001]
            }

    asyncio.run(scenario())


def test_connection_config_update_saves_then_reconfigures_without_address_override(
    db_session_factory,
):
    manager = _DeviceManager()

    async def scenario():
        service = SettingService(
            db_session_factory, device_manager=manager, env={}
        )
        await service.update({"adb.connection_config": "BlueStacks"})
        assert manager.calls == [{}]
        async with db_session_factory() as session:
            assert await SettingRepository(session).get("adb.connection_config") == "BlueStacks"

    asyncio.run(scenario())


@pytest.mark.parametrize(
    ("items", "code"),
    [
        ({"bad.key": 1}, ErrorCode.SETTING_KEY_UNKNOWN),
        ({"adb.path": "/custom/adb"}, ErrorCode.SETTING_READONLY),
        ({"adb.screenshot_quality": 0}, ErrorCode.SETTING_VALUE_INVALID),
        ({"adb.common_ports": [5555, 5555]}, ErrorCode.SETTING_VALUE_INVALID),
    ],
)
def test_update_rejects_invalid_and_readonly_values(db_session_factory, items, code):
    async def scenario():
        service = SettingService(db_session_factory, env={})
        with pytest.raises(AppError) as exc:
            await service.update(items)
        assert exc.value.code == code

    asyncio.run(scenario())


def test_readonly_access_token_is_not_exposed_or_writable(db_session_factory):
    async def scenario():
        service = SettingService(db_session_factory, env={})
        view = await service.get()
        token = next(item for item in view["items"] if item["key"] == "app.access_token")
        assert token["value"] == "***"
        await service.update({"app.access_token": "***"})
        await service.update({"app.access_token": ""})
        with pytest.raises(AppError) as exc:
            await service.update({"app.access_token": "secret"})
        assert exc.value.code == ErrorCode.SETTING_READONLY

    asyncio.run(scenario())


def test_sensitive_llm_key_masks_and_empty_or_masked_input_is_unchanged(db_session_factory):
    async def scenario():
        service = SettingService(db_session_factory, env={})
        await service.update({"llm.api_key": "my-secret"})
        before = await service.get()
        key = next(item for item in before["items"] if item["key"] == "llm.api_key")
        assert key["value"] == "***"
        await service.update({"llm.api_key": "***"})
        await service.update({"llm.api_key": ""})
        async with db_session_factory() as session:
            assert await SettingRepository(session).get("llm.api_key") == "my-secret"

    asyncio.run(scenario())


@pytest.mark.parametrize("failure", [False, True])
def test_apply_failure_reports_saved_value_and_applied_true(
    db_session_factory, failure: bool
):
    manager = _DeviceManager(result=False) if not failure else _DeviceManager(error=RuntimeError("offline"))

    async def scenario():
        service = SettingService(db_session_factory, device_manager=manager, env={})
        with pytest.raises(AppError) as exc:
            await service.update({"adb.address": "new:5555"})
        assert exc.value.code == ErrorCode.SETTING_APPLY_FAILED
        assert exc.value.details["applied"] is True
        async with db_session_factory() as session:
            assert await SettingRepository(session).get("adb.address") == "new:5555"
        assert manager.calls == [{"address": "new:5555"}]

    asyncio.run(scenario())


def test_reset_removes_override_and_falls_back_to_yaml(db_session_factory, tmp_path):
    path = tmp_path / "config.yaml"
    path.write_text("adb:\n  address: yaml:5555\n", encoding="utf8")

    async def scenario():
        manager = _DeviceManager()
        service = SettingService(
            db_session_factory,
            settings_path=path,
            env={},
            device_manager=manager,
        )
        await service.update({"adb.address": "db:5555"})
        view = await service.reset(["adb.address"])
        address = next(item for item in view["items"] if item["key"] == "adb.address")
        assert address["value"] == "yaml:5555"
        assert address["source"] == "yaml"
        assert manager.calls == [{"address": "db:5555"}, {"address": "yaml:5555"}]

    asyncio.run(scenario())
