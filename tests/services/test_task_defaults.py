"""Global task channel defaults retain env > DB > YAML > code priority."""

from __future__ import annotations

import asyncio
from pathlib import Path

import pytest
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker
from sqlalchemy.pool import NullPool
from sqlmodel import SQLModel

import maa_api.db.models  # noqa: F401
import maa_api.settings as settings_module
from maa_api.db.repositories.setting import SettingRepository
from maa_api.db.session import make_engine
from maa_api.domain.task import ChannelDefaults
from maa_api.services.task_defaults import load_channel_defaults
from maa_api.settings import ENV_OVERRIDES


@pytest.fixture
def db_session_factory(tmp_path: Path):
    engine = make_engine(
        f"sqlite+aiosqlite:///{tmp_path / 'defaults.db'}", poolclass=NullPool
    )

    async def create_tables():
        async with engine.begin() as connection:
            await connection.run_sync(SQLModel.metadata.create_all)

    asyncio.run(create_tables())
    yield async_sessionmaker(engine, expire_on_commit=False, class_=AsyncSession)
    engine.sync_engine.dispose()


def test_channel_defaults_merge_yaml_db_and_environment(
    db_session_factory, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
):
    config = tmp_path / "config.yaml"
    config.write_text(
        "channel:\n  client_type: Official\n  server: US\n", encoding="utf8"
    )
    monkeypatch.setattr(settings_module, "DEFAULT_CONFIG_PATH", config)
    for variable in ENV_OVERRIDES.values():
        monkeypatch.delenv(variable, raising=False)
    monkeypatch.setenv("MAA_CHANNEL_CLIENT_TYPE", "YoStarJP")

    async def scenario():
        async with db_session_factory() as session:
            repo = SettingRepository(session)
            await repo.set("channel.client_type", "Bilibili")
            await repo.set("channel.server", "JP")
            await session.commit()
            defaults = await load_channel_defaults(session)
            assert defaults == ChannelDefaults(client_type="YoStarJP", server="JP")

    asyncio.run(scenario())


def test_channel_defaults_use_db_then_yaml_then_code(
    db_session_factory, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
):
    config = tmp_path / "config.yaml"
    config.write_text(
        "channel:\n  client_type: Official\n  server: US\n", encoding="utf8"
    )
    monkeypatch.setattr(settings_module, "DEFAULT_CONFIG_PATH", config)
    for variable in ENV_OVERRIDES.values():
        monkeypatch.delenv(variable, raising=False)

    async def scenario():
        async with db_session_factory() as session:
            repo = SettingRepository(session)
            await repo.set("channel.client_type", "Bilibili")
            await session.commit()
            defaults = await load_channel_defaults(session)
            assert defaults == ChannelDefaults(client_type="Bilibili", server="US")

    asyncio.run(scenario())


def test_invalid_channel_db_value_only_falls_back_that_dimension(
    db_session_factory, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
):
    config = tmp_path / "config.yaml"
    config.write_text("channel:\n  client_type: Official\n  server: US\n", encoding="utf8")
    monkeypatch.setattr(settings_module, "DEFAULT_CONFIG_PATH", config)
    for variable in ENV_OVERRIDES.values():
        monkeypatch.delenv(variable, raising=False)

    async def scenario():
        async with db_session_factory() as session:
            repo = SettingRepository(session)
            await repo.set("channel.client_type", "Unknown")
            await repo.set("channel.server", "JP")
            await session.commit()
            defaults = await load_channel_defaults(session)
            assert defaults == ChannelDefaults(client_type="Official", server="JP")

    asyncio.run(scenario())
