"""M84 T4：定时任务被消耗上限拦下（spec §4.5.7、§6）。

到点触发（``propagate_error=False``）记 ``last_result="skipped"`` 且不建流水线；
「立即执行」（``propagate_error=True``）把 403 ``SPEND_CAP_EXCEEDED`` 原样抛出。
拦截记录的 ``ref`` 是 ``schedule:<id>``。
"""

from __future__ import annotations

import asyncio
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import pytest
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from maa_api.agent.spend import SpendGuard
from maa_api.db.models import Pipeline
from maa_api.domain.errors import AppError, ErrorCode
from maa_api.services.queue_service import QueueService
from maa_api.services.schedule_service import ScheduleService, ScheduleWrite
from maa_api.settings import Settings, SpendSettings
from tests.fakes.v3_db import make_session_factory

#: 北京 2026-11-05 20:00（naive UTC），消耗日 2026-11-05。
NOW = datetime(2026, 11, 5, 12, 0)


class _Limits:
    def __init__(self, *, medicine: int = -1) -> None:
        self.medicine = medicine

    def __call__(self) -> Settings:
        return Settings(
            spend=SpendSettings(daily_medicine_limit=self.medicine)
        )


class FakeScheduler:
    def __init__(self) -> None:
        self.jobs: dict[str, Any] = {}

    def add_job(self, func, *, trigger, kwargs, id, **options):
        self.jobs[id] = type(
            "Job",
            (),
            {
                "id": id,
                "func": func,
                "trigger": trigger,
                "kwargs": kwargs,
                "options": options,
                "next_run_time": trigger.get_next_fire_time(
                    None, datetime.now(timezone.utc)
                ),
            },
        )()
        return self.jobs[id]

    def get_job(self, job_id: str):
        return self.jobs.get(job_id)

    def remove_job(self, job_id: str):
        job = self.jobs.pop(job_id, None)
        if job is None:
            from apscheduler.jobstores.base import JobLookupError

            raise JobLookupError(job_id)
        return job


class FakeRunner:
    def __init__(self) -> None:
        self.published: list[str] = []

    async def publish_pipeline(self, pipeline_id: str) -> None:
        self.published.append(pipeline_id)

    async def publish_queue_changed(self) -> None:
        pass


@pytest.fixture
def isolated_config(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    import maa_api.settings as settings_module

    monkeypatch.setattr(
        settings_module, "DEFAULT_CONFIG_PATH", tmp_path / "missing-config.yaml"
    )
    for env_name in settings_module.ENV_OVERRIDES.values():
        monkeypatch.delenv(env_name, raising=False)


def _write() -> ScheduleWrite:
    return ScheduleWrite(
        name="spend-cap",
        cron="0 7 * * *",
        priority=2,
        template=[{"name": "Fight", "stage": "1-7", "medicine": 2}],
    )


async def _pipeline_count(factory: async_sessionmaker[AsyncSession]) -> int:
    async with factory() as session:
        return int(
            (
                await session.execute(select(func.count()).select_from(Pipeline))
            ).scalar_one()
        )


def test_scheduled_fire_over_cap_records_skipped(
    tmp_path: Path, isolated_config: None
) -> None:
    async def scenario() -> None:
        engine, factory = await make_session_factory(tmp_path / "schedule-cap.db")
        queue = QueueService(factory)
        guard = SpendGuard(
            factory, settings_provider=_Limits(medicine=1), clock=lambda: NOW
        )
        queue.bind_spend_guard(guard)
        service = ScheduleService(factory, FakeScheduler(), queue, FakeRunner())
        try:
            created = await service.create(_write())
            await service._scheduled_trigger(created.id)

            detail = await service.get(created.id)
            assert detail.last_run_result is not None
            assert detail.last_run_result.status == "skipped"
            assert detail.last_run_result.pipeline_id is None
            assert await _pipeline_count(factory) == 0
            blocked = guard.blocked_today()
            assert len(blocked) == 1
            assert blocked[0]["ref"] == f"schedule:{created.id}"
            assert blocked[0]["source"] == "scheduled"
        finally:
            engine.sync_engine.dispose()

    asyncio.run(scenario())


def test_run_now_over_cap_raises(
    tmp_path: Path, isolated_config: None
) -> None:
    async def scenario() -> None:
        engine, factory = await make_session_factory(tmp_path / "schedule-run-now.db")
        queue = QueueService(factory)
        guard = SpendGuard(
            factory, settings_provider=_Limits(medicine=0), clock=lambda: NOW
        )
        queue.bind_spend_guard(guard)
        service = ScheduleService(factory, FakeScheduler(), queue, FakeRunner())
        try:
            created = await service.create(_write())
            with pytest.raises(AppError) as caught:
                await service.run_now(created.id)
            assert caught.value.code is ErrorCode.SPEND_CAP_EXCEEDED
            assert caught.value.http_status == 403
            assert await _pipeline_count(factory) == 0
            assert guard.blocked_today()[0]["ref"] == f"schedule:{created.id}"
        finally:
            engine.sync_engine.dispose()

    asyncio.run(scenario())
