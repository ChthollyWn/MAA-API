"""M42 §4.5：``OperatorBoxService.apply_skland_sync`` 的写库语义（真实 SQLite）。

与 ``test_skland_sync.py`` 的分工：那边测编排（护栏、冷却、凭据），这边测这一层的
三件事——写哪些列、``None`` 不动列、来源标记。

**M47 起这是账号干员数据的唯一写入者**（docs/13 §8.70）：内核 `OperBox` 识别、手动
编辑与 Agent 补录三条通道一起下线，所以这里不再有「覆盖手动值时记差异」这类用例；
反过来钉住的是「同步不再产生 ``account_operator_diff`` 新行、旧行原样留作历史」。
"""

from __future__ import annotations

import asyncio
from datetime import datetime
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
LATER = datetime(2026, 10, 13, 4, 0, 0)


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


def _seed(factory, char_id: str, **fields: Any) -> None:
    """直接写一行「历史状态」——M47 之前可能由识别 / 手动 / Agent 留下的形状。"""

    async def scenario() -> None:
        defaults: dict[str, Any] = dict(name="测试干员", rarity=6, own=True, recognized_at=NOW)
        defaults.update(fields)
        async with factory() as session:
            session.add(AccountOperator(char_id=char_id, **defaults))
            await session.commit()

    asyncio.run(scenario())


async def _diff_rows(factory) -> list[AccountOperatorDiff]:
    async with factory() as session:
        return list((await session.execute(select(AccountOperatorDiff))).scalars().all())


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
            "ownership_cleared": 0,
        }
        # M47：同步不再产生差异行，摘要里也没有 diffs 键。
        assert "diffs" not in summary

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
            assert row.recognized_at == NOW

        assert await _diff_rows(session_factory) == []

    asyncio.run(scenario())


def test_skland_sync_leaves_none_fields_untouched(session_factory):
    """``None`` 是「上游没给这一项」：不写列、也不改来源标记。"""
    # 老库里的行：历史来源是 recognition（M47 前内核识别的口径），M47 后读侧仍认它。
    _seed(
        session_factory,
        "char_002_amiya",
        elite=2,
        level=90,
        potential=6,
        field_sources={"elite": "recognition", "level": "recognition"},
    )

    async def scenario():
        box = _box(session_factory)
        await box.apply_skland_sync(
            [_update("char_002_amiya", level=None, elite=None, favor_percent=120)],
            synced_at=NOW,
        )

        async with session_factory() as session:
            row = await session.get(AccountOperator, "char_002_amiya")
            assert row.level == 90  # 上游没给等级，旧值原样留着
            assert row.elite == 2
            assert row.favor_percent == 120
            assert row.field_sources["level"] == "recognition"
            assert row.field_sources["favor_percent"] == "skland"

    asyncio.run(scenario())


def test_skland_sync_overwrites_a_legacy_manual_value_without_a_new_diff(
    session_factory,
):
    """M47：同步能覆盖历史手动值，但**不再**写 ``account_operator_diff`` 新行。

    旧行是历史，既不改也不删（迁移 ``0022`` 只清 ``account_operator``）。
    """
    _seed(
        session_factory,
        "char_002_amiya",
        level=55,
        field_sources={"level": "manual"},
    )

    async def scenario():
        box = _box(session_factory)
        await box.apply_skland_sync([_update("char_002_amiya", level=90)], synced_at=NOW)

        async with session_factory() as session:
            row = await session.get(AccountOperator, "char_002_amiya")
            assert row.level == 90
            assert row.field_sources["level"] == "skland"

        assert await _diff_rows(session_factory) == []

    asyncio.run(scenario())


def test_skland_sync_keeps_historical_diff_rows(session_factory):
    """已经躺在表里的差异行是历史：同步既不新增、也不改动它们。"""

    _seed(session_factory, "char_002_amiya", level=55)

    async def scenario():
        async with session_factory() as session:
            session.add(
                AccountOperatorDiff(
                    char_id="char_002_amiya",
                    field="level",
                    manual_value=55,
                    recognized_value=90,
                    created_at=NOW,
                )
            )
            await session.commit()

        box = _box(session_factory)
        await box.apply_skland_sync([_update("char_002_amiya", level=90)], synced_at=LATER)

        rows = await _diff_rows(session_factory)
        assert [(row.char_id, row.field, row.manual_value, row.recognized_value) for row in rows] == [
            ("char_002_amiya", "level", 55, 90)
        ]
        assert rows[0].dismissed_at is None
        assert rows[0].created_at == NOW  # 旧行的时间戳没被同步改写

    asyncio.run(scenario())


def test_skland_sync_counts_unchanged_rows_and_refreshes_the_stamp(session_factory):
    async def scenario():
        box = _box(session_factory)
        await box.apply_skland_sync([_update("char_002_amiya", level=90)], synced_at=NOW)
        summary = await box.apply_skland_sync(
            [_update("char_002_amiya", level=90)], synced_at=LATER
        )
        assert summary["unchanged"] == 1 and summary["updated"] == 0

        async with session_factory() as session:
            row = await session.get(AccountOperator, "char_002_amiya")
            assert row.recognized_at == LATER

    asyncio.run(scenario())


def test_skland_sync_never_clears_ownership_without_permission(session_factory):
    """默认 ``clear_missing=False``：即使库里有一名不在本轮 ``chars`` 里的干员，也不动它。"""
    _seed(session_factory, "char_9999", level=90, own=True)

    async def scenario():
        box = _box(session_factory)
        summary = await box.apply_skland_sync(
            [_update("char_0001", level=90)], synced_at=NOW
        )
        assert summary["ownership_cleared"] == 0

        async with session_factory() as session:
            assert (await session.get(AccountOperator, "char_9999")).own is True

    asyncio.run(scenario())


def test_skland_sync_can_clear_ownership_when_asked(session_factory):
    _seed(session_factory, "char_9999", level=90, own=True)

    async def scenario():
        box = _box(session_factory)
        summary = await box.apply_skland_sync(
            [_update("char_0001", level=90)], clear_missing=True, synced_at=NOW
        )
        assert summary["ownership_cleared"] == 1

        async with session_factory() as session:
            row = await session.get(AccountOperator, "char_9999")
            assert row.own is False
            assert row.field_sources["own"] == "skland"

    asyncio.run(scenario())
