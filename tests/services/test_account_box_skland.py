"""M42 §4.5：``OperatorBoxService.apply_skland_sync`` 的写库语义（真实 SQLite）。

与 ``test_skland_sync.py`` 的分工：那边测编排（护栏、冷却、凭据），这边测这一层的
四件事——写哪些列、``None`` 不动列、来源标记、以及**覆盖手动值时记差异**。
"""

from __future__ import annotations

import asyncio
from datetime import UTC, datetime
from typing import Any

import pytest
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker
from sqlalchemy.pool import NullPool
from sqlmodel import SQLModel

from maa_api.db.models import AccountOperator, AccountOperatorDiff
from maa_api.db.session import make_engine
from maa_api.services.account.box import OperatorBoxService
from maa_api.services.skland.mapping import OperatorTrainingUpdate
from maa_api.settings import Settings

NOW = datetime(2026, 10, 12, 4, 30, 0)


@pytest.fixture
def session_factory(tmp_path):
    engine = make_engine(
        f"sqlite+aiosqlite:///{tmp_path / 'box_skland.db'}", poolclass=NullPool
    )

    async def create_schema():
        async with engine.begin() as connection:
            await connection.run_sync(SQLModel.metadata.create_all)

    asyncio.run(create_schema())
    factory = async_sessionmaker(engine, expire_on_commit=False, class_=AsyncSession)
    yield factory
    engine.sync_engine.dispose()


def _box(factory) -> OperatorBoxService:
    return OperatorBoxService(factory, settings_provider=lambda: Settings())


def _update(char_id: str, **fields: Any) -> OperatorTrainingUpdate:
    return OperatorTrainingUpdate(char_id=char_id, **fields)


def test_skland_sync_writes_every_field_and_marks_the_source(session_factory):
    async def scenario():
        box = _box(session_factory)
        summary = await box.apply_skland_sync(
            [
                _update(
                    "char_002_amiya",
                    name="阿米娅",
                    rarity=5,
                    profession="术师",
                    elite=2,
                    level=80,
                    potential=6,
                    skill_level=7,
                    masteries={"1": 3, "2": 0, "3": 0},
                    modules={"X": 2},
                    favor_percent=200,
                )
            ],
            synced_at=NOW,
        )
        assert summary == {
            "total": 1,
            "created": 1,
            "updated": 1,
            "unchanged": 0,
            "diffs": 0,
            "ownership_cleared": 0,
        }

        async with session_factory() as session:
            row = await session.get(AccountOperator, "char_002_amiya")
            assert (row.name, row.rarity, row.profession) == ("阿米娅", 5, "术师")
            assert (row.own, row.elite, row.level, row.potential) == (True, 2, 80, 6)
            assert (row.skill_level, row.masteries, row.modules) == (
                7,
                {"1": 3, "2": 0, "3": 0},
                {"X": 2},
            )
            assert row.favor_percent == 200
            assert set(row.field_sources.values()) == {"skland"}

    asyncio.run(scenario())


def test_skland_sync_leaves_none_fields_untouched(session_factory):
    """``None`` 是「上游没给这一项」：不写列、也不改来源标记。"""

    async def scenario():
        box = _box(session_factory)
        await box.apply_recognition(
            {
                "char_002_amiya": {
                    "name": "阿米娅",
                    "rarity": 5,
                    "own": True,
                    "elite": 2,
                    "level": 90,
                    "potential": 6,
                }
            }
        )
        await box.apply_skland_sync(
            [_update("char_002_amiya", level=None, elite=None, favor_percent=120)],
            synced_at=NOW,
        )

        async with session_factory() as session:
            row = await session.get(AccountOperator, "char_002_amiya")
            assert row.level == 90  # 上游没给等级，识别值原样留着
            assert row.elite == 2
            assert row.favor_percent == 120
            assert row.field_sources["level"] == "recognition"
            assert row.field_sources["favor_percent"] == "skland"

    asyncio.run(scenario())


def test_skland_sync_records_a_diff_when_it_overrides_a_manual_edit(session_factory):
    """手动改过的字段被森空岛纠正：记一条差异，让页面上能看见。"""

    async def scenario():
        box = _box(session_factory)
        await box.apply_recognition(
            {"char_002_amiya": {"own": True, "elite": 2, "level": 90, "potential": 6}}
        )
        await box.patch_operator("char_002_amiya", {"level": 55}, source="manual")

        await box.apply_skland_sync([_update("char_002_amiya", level=90)], synced_at=NOW)

        async with session_factory() as session:
            row = await session.get(AccountOperator, "char_002_amiya")
            assert row.level == 90
            assert row.field_sources["level"] == "skland"
            diffs = (
                await session.execute(select(AccountOperatorDiff))
            ).scalars().all()
            assert [(d.char_id, d.field, d.manual_value, d.recognized_value) for d in diffs] == [
                ("char_002_amiya", "level", 55, 90)
            ]

    asyncio.run(scenario())


def test_skland_sync_does_not_record_a_diff_for_recognition_overwrites(session_factory):
    """识别值被同步覆盖不算差异（差异只在「人手改过」时产生）。"""

    async def scenario():
        box = _box(session_factory)
        await box.apply_recognition(
            {"char_002_amiya": {"own": True, "elite": 2, "level": 80, "potential": 6}}
        )
        await box.apply_skland_sync([_update("char_002_amiya", level=90)], synced_at=NOW)

        async with session_factory() as session:
            diffs = (
                await session.execute(select(AccountOperatorDiff))
            ).scalars().all()
            assert diffs == []

    asyncio.run(scenario())


def test_skland_sync_counts_unchanged_rows_and_refreshes_the_stamp(session_factory):
    async def scenario():
        box = _box(session_factory)
        await box.apply_skland_sync([_update("char_002_amiya", level=90)], synced_at=NOW)
        later = datetime(2026, 10, 13, 4, 0, 0)
        summary = await box.apply_skland_sync(
            [_update("char_002_amiya", level=90)], synced_at=later
        )
        assert summary["unchanged"] == 1 and summary["updated"] == 0

        async with session_factory() as session:
            row = await session.get(AccountOperator, "char_002_amiya")
            assert row.recognized_at == later

    asyncio.run(scenario())


def test_skland_sync_never_clears_ownership_without_permission(session_factory):
    """默认 ``clear_missing=False``：即使库里有一名不在本轮 ``chars`` 里的干员，也不动它。"""

    async def scenario():
        box = _box(session_factory)
        await box.apply_recognition({"char_9999": {"own": True, "level": 90}})
        summary = await box.apply_skland_sync(
            [_update("char_0001", level=90)], synced_at=NOW
        )
        assert summary["ownership_cleared"] == 0

        async with session_factory() as session:
            assert (await session.get(AccountOperator, "char_9999")).own is True

    asyncio.run(scenario())


def test_skland_sync_can_clear_ownership_when_asked(session_factory):
    async def scenario():
        box = _box(session_factory)
        await box.apply_recognition({"char_9999": {"own": True, "level": 90}})
        summary = await box.apply_skland_sync(
            [_update("char_0001", level=90)], clear_missing=True, synced_at=NOW
        )
        assert summary["ownership_cleared"] == 1

        async with session_factory() as session:
            row = await session.get(AccountOperator, "char_9999")
            assert row.own is False
            assert row.field_sources["own"] == "skland"

    asyncio.run(scenario())
