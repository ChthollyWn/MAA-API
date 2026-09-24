"""Schedule repository persistence and query semantics."""

from __future__ import annotations

import asyncio
from datetime import timedelta

from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker
from sqlalchemy.pool import StaticPool
from sqlmodel import SQLModel

import maa_api.db.models  # noqa: F401
from maa_api.db.models import Schedule, utcnow
from maa_api.db.repositories.schedule import ScheduleRepository
from maa_api.db.session import make_engine


async def _memory_session_factory():
    engine = make_engine("sqlite+aiosqlite:///:memory:", poolclass=StaticPool)
    async with engine.begin() as connection:
        await connection.run_sync(SQLModel.metadata.create_all)
    return engine, async_sessionmaker(
        engine, expire_on_commit=False, class_=AsyncSession
    )


def test_list_filters_enabled_and_keeps_stable_order():
    async def scenario():
        engine, session_factory = await _memory_session_factory()
        try:
            async with session_factory() as session:
                repo = ScheduleRepository(session)
                await repo.create(Schedule(name="enabled", cron="0 7 * * *", enabled=True))
                await repo.create(Schedule(name="disabled", cron="0 7 * * *", enabled=False))
                await session.commit()

            async with session_factory() as session:
                repo = ScheduleRepository(session)
                all_rows = await repo.list()
                enabled_rows = await repo.list(enabled=True)
                disabled_rows = await repo.list(enabled=False)
                assert len(all_rows) == 2
                assert [row.name for row in enabled_rows] == ["enabled"]
                assert [row.name for row in disabled_rows] == ["disabled"]
        finally:
            await engine.dispose()

    asyncio.run(scenario())


def test_record_run_persists_skipped_result_without_pipeline():
    async def scenario():
        engine, session_factory = await _memory_session_factory()
        try:
            async with session_factory() as session:
                schedule = await ScheduleRepository(session).create(
                    Schedule(name="weekly", cron="0 7 * * 0")
                )
                await session.commit()
                schedule_id = schedule.id

            now = utcnow()
            next_run = now + timedelta(days=7)
            async with session_factory() as session:
                repo = ScheduleRepository(session)
                assert await repo.record_run(
                    schedule_id,
                    last_run_at=now,
                    next_run_at=next_run,
                    pipeline_id=None,
                    result="skipped",
                )
                await session.commit()

            async with session_factory() as session:
                saved = await ScheduleRepository(session).get(schedule_id)
                assert saved is not None
                assert saved.last_run_at == now
                assert saved.next_run_at == next_run
                assert saved.last_pipeline_id is None
                assert saved.last_result == "skipped"
        finally:
            await engine.dispose()

    asyncio.run(scenario())


def test_update_and_delete_keep_pipeline_foreign_key_safe():
    async def scenario():
        engine, session_factory = await _memory_session_factory()
        try:
            async with session_factory() as session:
                repo = ScheduleRepository(session)
                row = await repo.create(Schedule(name="old", cron="0 7 * * *"))
                row.name = "renamed"
                await repo.update(row)
                await session.commit()
                row_id = row.id

            async with session_factory() as session:
                repo = ScheduleRepository(session)
                saved = await repo.get(row_id)
                assert saved is not None
                assert saved.name == "renamed"
                assert await repo.delete(row_id)
                await session.commit()
                assert await repo.get(row_id) is None
        finally:
            await engine.dispose()

    asyncio.run(scenario())
