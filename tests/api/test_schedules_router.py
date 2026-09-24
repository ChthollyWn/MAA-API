"""Schedule HTTP contract and uniform error handling."""

from __future__ import annotations

from contextlib import asynccontextmanager
from datetime import datetime, timezone

import pytest
from apscheduler.jobstores.base import JobLookupError
from fastapi import FastAPI
from fastapi.testclient import TestClient
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker
from sqlalchemy.pool import StaticPool
from sqlmodel import SQLModel

import maa_api.db.models  # noqa: F401
from maa_api.api import deps
from maa_api.api.errors import register_exception_handlers
from maa_api.api.routers import schedules
from maa_api.db.session import make_engine
from maa_api.domain.errors import AppError, ErrorCode
from maa_api.services.queue_service import QueueService
from maa_api.services.schedule_service import ScheduleService


class FakeScheduler:
    def __init__(self):
        self.jobs = {}

    def add_job(self, func, *, trigger, kwargs, id, **options):
        next_run_time = trigger.get_next_fire_time(
            None, datetime.now(timezone.utc)
        )
        job = type("Job", (), {
            "id": id,
            "func": func,
            "trigger": trigger,
            "kwargs": kwargs,
            "options": options,
            "next_run_time": next_run_time,
        })()
        self.jobs[id] = job
        return job

    def get_job(self, job_id):
        return self.jobs.get(job_id)

    def remove_job(self, job_id):
        if job_id not in self.jobs:
            raise JobLookupError(job_id)
        del self.jobs[job_id]


class FakeRunner:
    async def publish_pipeline(self, pipeline_id: str):
        return None

    async def publish_queue_changed(self):
        return None


@pytest.mark.parametrize("tmp_settings", ["s3cret"], indirect=True)
def test_schedule_routes_cover_crud_run_auth_and_error_codes(tmp_settings):
    deps.reset_rate_limiter()
    engine = make_engine("sqlite+aiosqlite:///:memory:", poolclass=StaticPool)
    factory = async_sessionmaker(
        engine, expire_on_commit=False, class_=AsyncSession
    )
    scheduler = FakeScheduler()
    queue = QueueService(factory)
    service = ScheduleService(factory, scheduler, queue, FakeRunner())

    @asynccontextmanager
    async def lifespan(_app):
        async with engine.begin() as connection:
            await connection.run_sync(SQLModel.metadata.create_all)
        yield
        await service.close()
        await engine.dispose()

    app = FastAPI(lifespan=lifespan)
    register_exception_handlers(app)
    app.state.schedule_service = service
    app.include_router(schedules.router)

    with TestClient(app, raise_server_exceptions=False) as client:
        unauthorized = client.get("/api/schedules")
        assert unauthorized.status_code == 401
        assert unauthorized.json()["error"]["code"] == "UNAUTHORIZED"

        headers = {"X-Token": "s3cret"}
        empty = client.get("/api/schedules", headers=headers)
        assert empty.status_code == 200
        assert empty.json() == {"items": [], "total": 0}
        assert client.get("/api/schedules?page=2", headers=headers).status_code == 200

        body = {
            "name": "日常",
            "cron": "0 7 * * 1",
            "timezone": "Asia/Shanghai",
            "template": [{"name": "StartUp"}],
        }
        created = client.post("/api/schedules", json=body, headers=headers)
        assert created.status_code == 201
        schedule_id = created.json()["id"]
        assert created.headers["location"] == "/api/schedules/" + schedule_id
        assert created.json()["priority"] == 2
        assert created.json()["template"] == [{"name": "StartUp"}]
        assert created.json()["next_run_at"] is not None
        assert created.json()["next_run_at"].endswith("Z")

        duplicate = client.post("/api/schedules", json=body, headers=headers)
        assert duplicate.status_code == 409
        assert duplicate.json()["error"]["code"] == "SCHEDULE_NAME_CONFLICT"

        invalid_cron = client.post(
            "/api/schedules", json={**body, "name": "invalid", "cron": "daily"}, headers=headers
        )
        assert invalid_cron.status_code == 400
        assert invalid_cron.json()["error"]["code"] == "SCHEDULE_CRON_INVALID"

        listed = client.get("/api/schedules?enabled=true", headers=headers)
        assert listed.json()["total"] == 1
        assert listed.json()["items"][0]["name"] == "日常"
        detail = client.get("/api/schedules/" + schedule_id, headers=headers)
        assert detail.status_code == 200
        assert detail.json()["recent_runs"] == []

        patched = client.patch(
            "/api/schedules/" + schedule_id,
            json={"enabled": False},
            headers=headers,
        )
        assert patched.status_code == 200
        assert patched.json()["enabled"] is False
        assert not scheduler.jobs

        replaced = client.put(
            "/api/schedules/" + schedule_id,
            json={**body, "enabled": True, "cron": "30 8 * * 6"},
            headers=headers,
        )
        assert replaced.status_code == 200
        assert replaced.json()["cron"] == "30 8 * * 6"
        assert len(scheduler.jobs) == 1

        accepted = client.post(
            "/api/schedules/" + schedule_id + "/run",
            json={},
            headers=headers,
        )
        assert accepted.status_code == 202
        assert accepted.headers["location"] == "/api/pipelines/" + accepted.json()["pipeline_id"]
        assert accepted.json()["schedule_id"] == schedule_id

        duplicate_run = client.post(
            "/api/schedules/" + schedule_id + "/run",
            json={},
            headers=headers,
        )
        assert duplicate_run.status_code == 409
        assert duplicate_run.json()["error"]["code"] == "PIPELINE_ALREADY_RUNNING"

        no_skip_body = {**body, "enabled": True, "cron": "30 8 * * 6", "skip_if_running": False}
        no_skip = client.put(
            "/api/schedules/" + schedule_id,
            json=no_skip_body,
            headers=headers,
        )
        assert no_skip.status_code == 200

        async def queue_full(*_args, **_kwargs):
            raise AppError(ErrorCode.QUEUE_FULL, "queue full")

        queue.submit = queue_full
        full = client.post(
            "/api/schedules/" + schedule_id + "/run",
            json={},
            headers=headers,
        )
        assert full.status_code == 429
        assert full.json()["error"]["code"] == "QUEUE_FULL"

        deleted = client.delete("/api/schedules/" + schedule_id, headers=headers)
        assert deleted.status_code == 204
        assert deleted.content == b""
        assert not scheduler.jobs
        missing = client.get("/api/schedules/" + schedule_id, headers=headers)
        assert missing.status_code == 404
        assert missing.json()["error"]["code"] == "SCHEDULE_NOT_FOUND"

    deps.reset_rate_limiter()
