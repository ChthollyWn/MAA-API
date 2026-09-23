"""M6-05 repository behavior for persistent scheduled-pipeline deferrals."""

import asyncio
from datetime import timedelta

from sqlalchemy import update

from maa_api.db.models import Pipeline, utcnow
from maa_api.db.repositories.pipeline import PipelineRepository
from maa_api.domain.enums import PipelineSource, PipelineStatus, Priority


def _pipeline(title: str, *, source=PipelineSource.SCHEDULED, **overrides) -> Pipeline:
    fields = {
        "source": source,
        "priority": Priority.SCHEDULED if source == PipelineSource.SCHEDULED else Priority.MANUAL,
        "task_count": 1,
        "title": title,
    }
    fields.update(overrides)
    return Pipeline(**fields)


def test_claim_skips_future_deferral_without_changing_other_fifo(db_session_factory):
    async def scenario():
        async with db_session_factory() as session:
            repo = PipelineRepository(session)
            now = utcnow()
            future = _pipeline(
                "future",
                priority=Priority.MANUAL,
                created_at=now - timedelta(minutes=2),
                deferred_until=now + timedelta(minutes=5),
                defer_count=2,
            )
            first = _pipeline(
                "first",
                source=PipelineSource.MANUAL,
                priority=Priority.MANUAL,
                created_at=now - timedelta(minutes=1),
            )
            second = _pipeline(
                "second",
                source=PipelineSource.MANUAL,
                priority=Priority.MANUAL,
                created_at=now,
            )
            for pipeline in (future, first, second):
                await repo.create(pipeline, [])
            await session.commit()

            claimed = await repo.claim_next()
            assert claimed is not None and claimed.id == first.id
            assert (await repo.claim_next()).id == second.id
            assert await repo.claim_next() is None

            # Once due, the row becomes eligible in its original priority/FIFO position.
            await session.execute(
                update(Pipeline)
                .where(Pipeline.id == future.id)
                .values(deferred_until=utcnow() - timedelta(seconds=1))
            )
            await session.commit()
            claimed = await repo.claim_next()
            assert claimed is not None and claimed.id == future.id
            assert claimed.deferred_until is None
            assert claimed.defer_count == 2

    asyncio.run(scenario())


def test_defer_running_is_conditional_persistent_and_scheduled_only(db_session_factory):
    async def scenario():
        async with db_session_factory() as session:
            repo = PipelineRepository(session)
            scheduled = _pipeline("scheduled")
            manual = _pipeline(
                "manual",
                source=PipelineSource.MANUAL,
                priority=Priority.MANUAL,
            )
            await repo.create(scheduled, [])
            await repo.create(manual, [])
            await session.commit()

            claimed = await repo.claim_next()  # manual priority is higher than scheduled
            assert claimed is not None and claimed.id == manual.id
            assert await repo.defer_running(manual.id, utcnow() + timedelta(minutes=5)) is False
            claimed = await repo.claim_next()
            assert claimed is not None and claimed.id == scheduled.id
            running = await repo.get(scheduled.id)
            assert running is not None and running.status == PipelineStatus.RUNNING
            due_at = utcnow() + timedelta(minutes=5)
            assert await repo.defer_running(scheduled.id, due_at) is True
            await session.commit()

            deferred = await repo.get(scheduled.id)
            assert deferred is not None
            assert deferred.status == PipelineStatus.PENDING
            assert deferred.started_at is None
            assert deferred.core_epoch is None
            assert deferred.deferred_until == due_at
            assert deferred.defer_count == 1
            assert await repo.claim_next() is None  # manual already running; scheduled is deferred

    asyncio.run(scenario())
