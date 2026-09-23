"""Offline API contract tests for the M7 update router."""

from __future__ import annotations

import asyncio
from datetime import datetime, timezone
from types import SimpleNamespace
from typing import Any

import pytest
from fastapi import FastAPI
from sqlmodel import SQLModel

from maa_api.api.errors import register_exception_handlers
from maa_api.api.routers import updates
from maa_api.db import session as db_session
from maa_api.db.models import Pipeline, UpdateRecord
from maa_api.domain.enums import PipelineStatus, PipelineSource, UpdateStatus, UpdateTarget
from maa_api.domain.errors import AppError, ErrorCode
from maa_api.main import _wait_for_pipeline_idle, confirm_manual_interrupt, create_app
from maa_api.services.update_service import UpdateService


class UpdateServiceStub:
    def __init__(self) -> None:
        self.record = UpdateRecord(
            id="update-1",
            target=UpdateTarget.CORE,
            channel="stable",
            status=UpdateStatus.RUNNING,
            phase="checking",
            progress=0,
            triggered_by="manual",
            created_at=datetime(2026, 9, 24, tzinfo=timezone.utc),
            started_at=datetime(2026, 9, 24, tzinfo=timezone.utc),
        )
        self.calls: list[tuple[str, Any]] = []

    async def status(self, *, refresh: bool = False) -> dict[str, Any]:
        self.calls.append(("status", refresh))
        return {"updates": {}, "checked_at": "2026-09-24T00:00:00+00:00", "cached": not refresh}

    async def start(self, target: Any, options: Any, caller: str) -> UpdateRecord:
        self.calls.append(("start", (target, dict(options), caller)))
        return self.record

    async def rollback_core(self, *, force_interrupt: bool = False, caller: str = "manual") -> UpdateRecord:
        self.calls.append(("rollback_core", (force_interrupt, caller)))
        return self.record

    async def list(self, *, target: Any = None, status: Any = None, page: int, size: int) -> Any:
        self.calls.append(("list", (target, status, page, size)))
        return SimpleNamespace(items=[self.record], total=1, page=page, size=size)

    async def get(self, update_id: str) -> UpdateRecord:
        self.calls.append(("get", update_id))
        return self.record

    async def retry(self, update_id: str, caller: str) -> UpdateRecord:
        self.calls.append(("retry", (update_id, caller)))
        return self.record

    async def cancel(self, update_id: str) -> UpdateRecord:
        self.calls.append(("cancel", update_id))
        return self.record


def _app(service: Any = None) -> FastAPI:
    app = FastAPI(
        generate_unique_id_function=lambda route: f"{route.tags[0]}_{route.name}"
    )
    register_exception_handlers(app)
    app.include_router(updates.router)
    if service is not None:
        app.state.update_service = service
    return app


@pytest.fixture
def update_app(tmp_settings: Any, isolated_db: Any) -> tuple[FastAPI, UpdateServiceStub]:
    del tmp_settings, isolated_db  # Keep test client configuration and DB isolated.
    service = UpdateServiceStub()
    return _app(service), service


def test_update_service_missing_is_503(make_client: Any, tmp_settings: Any) -> None:
    del tmp_settings
    client = make_client(_app())
    response = client.get("/api/updates/status")
    assert response.status_code == 503
    assert response.json()["error"]["code"] == "SERVICE_UNAVAILABLE"


def test_status_refresh_and_openapi_contract(update_app: Any, make_client: Any) -> None:
    app, service = update_app
    client = make_client(app)
    response = client.get("/api/updates/status?refresh=true")
    assert response.status_code == 200
    assert response.json()["cached"] is False
    assert service.calls[-1] == ("status", True)

    document = client.get("/openapi.json").json()
    operation = document["paths"]["/api/updates/status"]["get"]
    assert operation["tags"] == ["updates"]
    assert operation["operationId"] == "updates_update_status"
    assert "503" in operation["responses"]


def test_main_openapi_includes_updates_and_notifications() -> None:
    paths = create_app().openapi()["paths"]
    assert "/api/updates/status" in paths
    assert "/api/notifications/channels" in paths


@pytest.mark.parametrize(
    ("path", "payload", "expected_target"),
    [
        ("/api/updates/core", {"channel": "stable", "version": "v7.0.0"}, UpdateTarget.CORE),
        ("/api/updates/resource", {"channel": "repo", "reload_mode": "defer"}, UpdateTarget.RESOURCE),
        ("/api/updates/game", {}, UpdateTarget.GAME),
    ],
)
def test_update_requests_are_accepted_and_pollable(
    update_app: Any,
    make_client: Any,
    path: str,
    payload: dict[str, Any],
    expected_target: UpdateTarget,
) -> None:
    app, service = update_app
    client = make_client(app)
    response = client.post(path, json=payload)
    assert response.status_code == 202
    assert response.headers["location"] == "/api/updates/update-1"
    assert response.json()["id"] == "update-1"
    _, (target, options, caller) = service.calls[-1]
    assert target is expected_target
    assert caller == "manual"
    if expected_target is UpdateTarget.GAME:
        assert options["channel"] == "Bilibili"  # configured default in tmp_settings


def test_core_rollback_retry_cancel_and_history_filters(
    update_app: Any, make_client: Any
) -> None:
    app, service = update_app
    client = make_client(app)

    rollback = client.post("/api/updates/core/rollback", json={"force_interrupt": True})
    assert rollback.status_code == 202
    assert service.calls[-1] == ("rollback_core", (True, "manual"))

    retry = client.post("/api/updates/update-1/retry")
    assert retry.status_code == 202
    assert service.calls[-1] == ("retry", ("update-1", "manual"))

    cancelled = client.delete("/api/updates/update-1")
    assert cancelled.status_code == 202
    assert service.calls[-1] == ("cancel", "update-1")

    history = client.get("/api/updates?target=core&status=running&page=2&size=10")
    assert history.status_code == 200
    assert history.json()["total"] == 1
    assert service.calls[-1] == ("list", ("core", "running", 2, 10))

    detail = client.get("/api/updates/update-1")
    assert detail.status_code == 200
    assert "log" in detail.json()
    assert service.calls[-1] == ("get", "update-1")


def test_invalid_channels_and_history_filters_are_domain_errors(
    update_app: Any, make_client: Any
) -> None:
    app, _ = update_app
    client = make_client(app)
    response = client.post("/api/updates/core", json={"channel": "beta"})
    assert response.status_code == 400
    assert response.json()["error"]["code"] == "INVALID_PARAMETER"

    response = client.get("/api/updates?status=unknown")
    assert response.status_code == 400
    assert response.json()["error"]["code"] == "INVALID_PARAMETER"

    response = client.get("/api/updates?page=0")
    assert response.status_code == 400
    assert response.json()["error"]["code"] == "INVALID_PAGINATION"


def test_history_filter_totals_use_isolated_database(
    make_client: Any, tmp_settings: Any, isolated_db: Any
) -> None:
    del tmp_settings

    async def seed() -> None:
        async with isolated_db.begin() as connection:
            await connection.run_sync(SQLModel.metadata.create_all)
        async with db_session.session_factory() as session:
            session.add_all(
                [
                    UpdateRecord(
                        id="core-failed",
                        target=UpdateTarget.CORE,
                        status=UpdateStatus.FAILED,
                        triggered_by="manual",
                    ),
                    UpdateRecord(
                        id="core-success",
                        target=UpdateTarget.CORE,
                        status=UpdateStatus.SUCCESS,
                        triggered_by="manual",
                    ),
                    UpdateRecord(
                        id="resource-failed",
                        target=UpdateTarget.RESOURCE,
                        status=UpdateStatus.FAILED,
                        triggered_by="manual",
                    ),
                ]
            )
            await session.commit()

    asyncio.run(seed())
    client = make_client(_app(UpdateService(db_session.session_factory)))
    filtered = client.get("/api/updates?target=core&status=failed")
    assert filtered.status_code == 200
    assert filtered.json()["total"] == 1
    assert [item["id"] for item in filtered.json()["items"]] == ["core-failed"]

    by_status = client.get("/api/updates?status=failed")
    assert by_status.status_code == 200
    assert by_status.json()["total"] == 2


def test_pipeline_idle_wait_drains_pending_before_pausing(
    tmp_settings: Any, isolated_db: Any
) -> None:
    del tmp_settings

    class QueueStub:
        paused = False

        async def pause(self) -> bool:
            async with db_session.session_factory() as session:
                row = await session.get(Pipeline, "queued-1")
            assert row is None or row.status != PipelineStatus.PENDING
            self.paused = True
            return True

        async def resume(self) -> bool:
            self.paused = False
            return True

    class RunnerStub:
        def __init__(self) -> None:
            import asyncio

            self.operation_lock = asyncio.Lock()
            self._active = None
            self.cancelled: list[str] = []

        async def request_cancel(self, pipeline_id: str) -> None:
            self.cancelled.append(pipeline_id)

    async def scenario() -> None:
        async with isolated_db.begin() as connection:
            await connection.run_sync(SQLModel.metadata.create_all)
        async with db_session.session_factory() as session:
            session.add(
                Pipeline(
                    id="queued-1",
                    source=PipelineSource.MANUAL,
                    priority=0,
                    status=PipelineStatus.PENDING,
                    task_count=1,
                )
            )
            await session.commit()

        queue, runner = QueueStub(), RunnerStub()

        async def runner_drains_queue() -> None:
            import asyncio

            await asyncio.sleep(0.05)
            async with db_session.session_factory() as session:
                row = await session.get(Pipeline, "queued-1")
                assert row is not None
                row.status = PipelineStatus.COMPLETED
                await session.commit()

        drain_task = asyncio.create_task(runner_drains_queue())
        changed = await _wait_for_pipeline_idle(
            queue,
            runner,
            db_session.session_factory,
            "default",
            timeout_seconds=1,
            poll_interval=0.01,
        )
        await drain_task
        assert changed is True
        assert queue.paused is True
        assert runner.cancelled == []
        await queue.resume()

    asyncio.run(scenario())


def test_pipeline_idle_wait_restores_state_when_confirmation_is_denied(
    tmp_settings: Any, isolated_db: Any
) -> None:
    del tmp_settings, isolated_db

    class QueueStub:
        paused = False

        async def pause(self) -> bool:
            self.paused = True
            return True

        async def resume(self) -> bool:
            self.paused = False
            return True

    class RunnerStub:
        def __init__(self) -> None:
            self.operation_lock = asyncio.Lock()
            self._active = None
            self.cancelled: list[str] = []

        async def request_cancel(self, pipeline_id: str) -> None:
            self.cancelled.append(pipeline_id)

    async def scenario() -> None:
        queue, runner = QueueStub(), RunnerStub()

        async def deny(_target: Any, _options: Any) -> bool:
            return False

        with pytest.raises(AppError) as error:
            await _wait_for_pipeline_idle(
                queue,
                runner,
                db_session.session_factory,
                "default",
                options={"force_interrupt": True},
                confirmation_policy=deny,
                timeout_seconds=0.1,
            )
        assert error.value.code is ErrorCode.FORBIDDEN
        assert queue.paused is False
        assert runner.cancelled == []

    asyncio.run(scenario())


def test_pipeline_idle_wait_fails_closed_without_confirmation_policy(
    tmp_settings: Any, isolated_db: Any
) -> None:
    del tmp_settings, isolated_db

    class QueueStub:
        paused = False

        async def pause(self) -> bool:
            self.paused = True
            return True

        async def resume(self) -> bool:
            self.paused = False
            return True

    class RunnerStub:
        def __init__(self) -> None:
            self.operation_lock = asyncio.Lock()
            self._active = None
            self.cancelled: list[str] = []

        async def request_cancel(self, pipeline_id: str) -> None:
            self.cancelled.append(pipeline_id)

    async def scenario() -> None:
        queue, runner = QueueStub(), RunnerStub()
        with pytest.raises(AppError) as error:
            await _wait_for_pipeline_idle(
                queue,
                runner,
                db_session.session_factory,
                "default",
                options={"force_interrupt": True},
                confirmation_policy=None,
                timeout_seconds=0.1,
            )
        assert error.value.code is ErrorCode.FORBIDDEN
        assert queue.paused is False
        assert runner.cancelled == []

    asyncio.run(scenario())


def test_force_interrupt_policy_accepts_explicit_manual_consent_but_denies_agent() -> None:
    assert confirm_manual_interrupt("core", {"_caller": "manual"}) is True
    assert confirm_manual_interrupt("core", {"_caller": "agent"}) is False
    assert confirm_manual_interrupt("core", {}) is False
