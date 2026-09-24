"""ScheduleService cron semantics, persistence, and queue integration."""

from __future__ import annotations

import asyncio
from datetime import datetime, timezone
from typing import Any

import pytest
from apscheduler.jobstores.base import JobLookupError
from apscheduler.triggers.cron import CronTrigger
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker
from sqlalchemy.pool import StaticPool
from sqlmodel import SQLModel

import maa_api.db.models  # noqa: F401
from maa_api.db.models import Schedule
from maa_api.db.repositories.schedule import ScheduleRepository
from maa_api.domain.errors import AppError, ErrorCode
from maa_api.services.queue_service import QueueService
from maa_api.services.schedule_service import ScheduleService, ScheduleWrite, cron_trigger
from maa_api.db.session import make_engine


class FakeScheduler:
    def __init__(self):
        self.jobs: dict[str, Any] = {}

    def add_job(self, func, *, trigger, kwargs, id, **options):
        next_run_time = trigger.get_next_fire_time(
            None, datetime.now(timezone.utc)
        )
        job = type(
            "Job",
            (),
            {
                "id": id,
                "func": func,
                "trigger": trigger,
                "kwargs": kwargs,
                "options": options,
                "next_run_time": next_run_time,
            },
        )()
        self.jobs[id] = job
        return job

    def get_job(self, job_id: str):
        return self.jobs.get(job_id)

    def remove_job(self, job_id: str):
        if job_id not in self.jobs:
            raise JobLookupError(job_id)
        del self.jobs[job_id]


class FakeRunner:
    def __init__(self):
        self.published: list[str] = []
        self.queue_changed = 0

    async def publish_pipeline(self, pipeline_id: str):
        self.published.append(pipeline_id)

    async def publish_queue_changed(self):
        self.queue_changed += 1


async def _database():
    engine = make_engine(
        "sqlite+aiosqlite:///:memory:", poolclass=StaticPool
    )
    async with engine.begin() as connection:
        await connection.run_sync(SQLModel.metadata.create_all)
    factory = async_sessionmaker(
        engine, expire_on_commit=False, class_=AsyncSession
    )
    return engine, factory


def _write(
    *,
    name: str = "morning",
    cron: str = "0 7 * * 1",
    timezone_name: str = "Asia/Shanghai",
    enabled: bool = True,
    template: list[dict[str, Any]] | None = None,
) -> ScheduleWrite:
    return ScheduleWrite(
        name=name,
        cron=cron,
        timezone=timezone_name,
        enabled=enabled,
        priority=2,
        template=template if template is not None else [{"name": "StartUp"}],
    )


def test_cron_uses_posix_weekday_numbers_for_sunday_saturday_and_timezone():
    sunday = cron_trigger("0 7 * * 0", "Asia/Shanghai")
    saturday = cron_trigger("30 19 * * 6", "UTC")
    sunday_next = sunday.get_next_fire_time(
        None, datetime(2026, 9, 20, 0, tzinfo=timezone.utc)
    )
    saturday_next = saturday.get_next_fire_time(
        None, datetime(2026, 9, 19, 0, tzinfo=timezone.utc)
    )
    assert sunday_next is not None
    assert sunday_next.weekday() == 6
    assert sunday_next.hour == 7
    assert str(sunday_next.tzinfo) == "Asia/Shanghai"
    assert saturday_next is not None
    assert saturday_next.weekday() == 5
    assert saturday_next.hour == 19
    assert str(saturday_next.tzinfo) == "UTC"


def test_cron_weekday_ranges_keep_posix_sunday_alias_semantics():
    sunday_alias = cron_trigger("0 7 * * 7-7", "UTC")
    sunday_zero = cron_trigger("0 7 * * 0-0", "UTC")
    friday_through_sunday = cron_trigger("0 7 * * 5-7", "UTC")
    friday_step = cron_trigger("0 7 * * 5/2", "UTC")
    sunday_midnight = datetime(2026, 9, 20, 0, tzinfo=timezone.utc)
    assert sunday_alias.get_next_fire_time(None, sunday_midnight).weekday() == 6
    assert sunday_zero.get_next_fire_time(None, sunday_midnight).weekday() == 6
    assert friday_through_sunday.get_next_fire_time(
        None, datetime(2026, 9, 18, 8, tzinfo=timezone.utc)
    ).weekday() == 5
    assert friday_step.get_next_fire_time(
        None, datetime(2026, 9, 18, 8, tzinfo=timezone.utc)
    ).weekday() == 6


@pytest.mark.parametrize(
    ("cron", "timezone_name"),
    [("* * * * * *", "UTC"), ("0 7 * * 0", "Not/A_Timezone"), ("0 7 * * 9", "UTC")],
)
def test_invalid_cron_or_timezone_is_rejected(cron, timezone_name):
    with pytest.raises(ValueError):
        cron_trigger(cron, timezone_name)


def test_create_validates_template_registers_enabled_job_and_returns_contract():
    async def scenario():
        engine, factory = await _database()
        scheduler = FakeScheduler()
        runner = FakeRunner()
        queue = QueueService(factory)
        service = ScheduleService(factory, scheduler, queue, runner)
        try:
            created = await service.create(_write())
            assert created.name == "morning"
            assert created.enabled is True
            assert created.priority == 2
            assert created.next_run_at is not None
            assert created.last_run_result is None
            assert created.recent_runs == []
            assert created.template == [{"name": "StartUp"}]
            assert len(scheduler.jobs) == 1
        finally:
            await engine.dispose()

    asyncio.run(scenario())


def test_invalid_cron_and_duplicate_name_use_schedule_error_codes():
    async def scenario():
        engine, factory = await _database()
        service = ScheduleService(factory, FakeScheduler(), QueueService(factory), FakeRunner())
        try:
            with pytest.raises(AppError) as invalid:
                await service.create(_write(cron="every Sunday"))
            assert invalid.value.code == ErrorCode.SCHEDULE_CRON_INVALID
            await service.create(_write())
            with pytest.raises(AppError) as duplicate:
                await service.create(_write())
            assert duplicate.value.code == ErrorCode.SCHEDULE_NAME_CONFLICT
        finally:
            await engine.dispose()

    asyncio.run(scenario())


def test_run_now_uses_queue_source_schedule_link_and_publishes_events():
    async def scenario():
        engine, factory = await _database()
        scheduler = FakeScheduler()
        runner = FakeRunner()
        queue = QueueService(factory)
        service = ScheduleService(factory, scheduler, queue, runner)
        try:
            created = await service.create(_write())
            pipeline = await service.run_now(created.id)
            assert pipeline.source == "scheduled"
            assert pipeline.schedule_id == created.id
            assert pipeline.priority == 2
            assert runner.published == [pipeline.id]
            assert runner.queue_changed == 1
            detail = await service.get(created.id)
            assert detail.last_run_result is not None
            assert detail.last_run_result.pipeline_id == pipeline.id
            assert detail.last_run_result.status == "pending"
            assert len(detail.recent_runs) == 1
        finally:
            await engine.dispose()

    asyncio.run(scenario())


def test_scheduled_trigger_skips_only_when_own_schedule_has_unfinished_pipeline():
    async def scenario():
        engine, factory = await _database()
        scheduler = FakeScheduler()
        runner = FakeRunner()
        queue = QueueService(factory)
        service = ScheduleService(factory, scheduler, queue, runner)
        try:
            created = await service.create(_write())
            await service.run_now(created.id)
            await service._scheduled_trigger(created.id)
            detail = await service.get(created.id)
            assert detail.last_run_result is not None
            assert detail.last_run_result.status == "skipped"
            assert detail.last_run_result.pipeline_id is None
            assert len(detail.recent_runs) == 2
            assert runner.queue_changed == 1
        finally:
            await engine.dispose()

    asyncio.run(scenario())


def test_patch_syncs_scheduler_registration_and_delete_removes_only_owned_job():
    async def scenario():
        engine, factory = await _database()
        scheduler = FakeScheduler()
        service = ScheduleService(factory, scheduler, QueueService(factory), FakeRunner())
        try:
            created = await service.create(_write())
            assert len(scheduler.jobs) == 1
            disabled = await service.patch(created.id, {"enabled": False})
            assert disabled.enabled is False
            assert disabled.next_run_at is None
            assert scheduler.jobs == {}
            enabled = await service.patch(created.id, {"enabled": True, "cron": "30 8 * * 6"})
            assert enabled.enabled is True
            assert len(scheduler.jobs) == 1
            assert scheduler.jobs["maa-schedule-" + created.id].trigger.fields[4].__str__() == "5"
            await service.delete(created.id)
            assert scheduler.jobs == {}
            with pytest.raises(AppError) as missing:
                await service.get(created.id)
            assert missing.value.code == ErrorCode.SCHEDULE_NOT_FOUND
        finally:
            await engine.dispose()

    asyncio.run(scenario())
