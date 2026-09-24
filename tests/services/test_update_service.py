"""Offline contract tests for shared update orchestration (M7-06)."""

from __future__ import annotations

import asyncio
import json
import os
import time
from datetime import datetime
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import pytest
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker
from sqlalchemy.pool import NullPool
from sqlmodel import SQLModel

from maa_api.db.models import ResourceAsset, Setting, UpdateRecord
from maa_api.db.repositories.setting import SettingRepository
from maa_api.db.repositories.update import UpdateRepository
from maa_api.db.session import make_engine
from maa_api.domain.enums import ResourceChannel, UpdatePhase, UpdateStatus, UpdateTarget
from maa_api.domain.errors import AppError, ErrorCode
from maa_api.services.log_hub import current_pipeline_id, current_request_id
from maa_api.services.update_service import UpdateService


@pytest.fixture
def update_db_factory(tmp_path: Path):
    engine = make_engine(
        f"sqlite+aiosqlite:///{tmp_path / 'updates.db'}", poolclass=NullPool
    )

    async def create_tables():
        async with engine.begin() as connection:
            await connection.run_sync(SQLModel.metadata.create_all)

    asyncio.run(create_tables())
    factory = async_sessionmaker(engine, expire_on_commit=False, class_=AsyncSession)
    yield factory
    engine.sync_engine.dispose()


class _Core:
    def __init__(self, *, available: bool = False, gate: asyncio.Event | None = None):
        self.available = available
        self.gate = gate
        self.check_calls = 0
        self.update_calls = 0

    async def check(self):
        self.check_calls += 1
        return {"current": "v1", "latest": "v2", "available": self.available}

    async def update(self, *, channel: str, force: bool = False):
        self.update_calls += 1
        await self.progress({"phase": "downloading", "downloaded": 20, "total": 100})
        if self.gate is not None:
            self.gate.set()
            await self.release.wait()
        await self.progress({"phase": "restarting"})
        return SimpleNamespace(version="v2")


class _Resources:
    def __init__(self, *, changed: bool = False):
        self.layers_root = Path("resource") / "maa-layers"
        self.temp_root = Path("resource") / "temp" / "resource-updates"
        self.check_calls = 0
        self.changed = changed

    async def check(self, channel, *, ota_metadata=None, repo_version=None):
        self.check_calls += 1
        return {
            ResourceChannel.OTA: SimpleNamespace(
                channel=ResourceChannel.OTA,
                changed=self.changed,
                checksum="a" * 64,
                etag='"etag"',
                last_modified="today",
                body=b'{"tasks": []}' if self.changed else None,
                error=None,
            ),
            ResourceChannel.REPO: SimpleNamespace(
                channel=ResourceChannel.REPO,
                changed=False,
                version="2026-09-01",
                error=None,
            ),
        }


class _Stage:
    def __init__(self, channel, path: Path, version=None):
        self.channel = channel
        self.path = path
        self.version = version
        self.checksum = "b" * 64
        self.etag = '"etag"'
        self.last_modified = "today"
        self.cleanup_path = None


class _ResourceWorkflow(_Resources):
    def __init__(self, tmp_path: Path, *, ota_error=None, repo_error=None, repo_changed=False):
        super().__init__(changed=True)
        self.temp_root = tmp_path / "resource-temp"
        self.ota_error = ota_error
        self.repo_error = repo_error
        self.repo_changed = repo_changed
        self.installed = []
        self.reload_modes = []
        self.stage_calls = []
        self.ota_path = tmp_path / "staged-tasks.json"
        self.ota_path.write_text('{"tasks": []}', encoding="utf-8")

    async def check(self, channel, *, ota_metadata=None, repo_version=None):
        self.check_calls += 1
        return {
            ResourceChannel.OTA: SimpleNamespace(
                channel=ResourceChannel.OTA,
                changed=self.changed,
                checksum="a" * 64,
                etag='"etag"',
                last_modified="today",
                body=b'{"tasks": []}' if self.changed else None,
                error=self.ota_error,
            ),
            ResourceChannel.REPO: SimpleNamespace(
                channel=ResourceChannel.REPO,
                changed=self.repo_changed,
                version="2026-09-02",
                error=self.repo_error,
            ),
        }

    async def stage(self, check):
        channel = check.channel
        self.stage_calls.append(channel)
        if channel is ResourceChannel.REPO and self.repo_error == "stage-failed":
            raise RuntimeError("repo stage failed")
        if channel is ResourceChannel.OTA:
            return _Stage(channel, self.ota_path)
        return _Stage(channel, self.temp_root / "repo", version=check.version)

    async def install_all(self, staged, *, reload_mode="wait"):
        self.installed.extend(staged)
        self.reload_modes.append(reload_mode)


class _Game:
    def __init__(self):
        self.calls = 0

    async def inspect(self, channel: str):
        self.calls += 1
        return {
            "channel": channel,
            "installed": {"version_name": "1.0"},
            "latest_version": "1.0",
            "update_may_be_available": False,
        }


def _count(factory, model):
    async def read():
        async with factory() as session:
            return await session.scalar(select(func.count()).select_from(model))

    return asyncio.run(read())


def test_status_uses_five_minute_cache_and_never_creates_update_records(update_db_factory):
    core, resources, game = _Core(), _Resources(), _Game()
    service = UpdateService(
        update_db_factory,
        core_workflow=core,
        resource_workflow=resources,
        game_workflow=game,
    )

    async def scenario():
        first = await service.status(refresh=True)
        second = await service.status()
        assert first["cached"] is False
        assert second["cached"] is True
        assert second["checked_at"] == first["checked_at"]
        assert core.check_calls == resources.check_calls == 1
        assert game.calls == 2
        assert first["updates"]["core"]["available"] is False
        assert first["updates"]["resource"]["available"] is False
        async with update_db_factory() as session:
            assert await session.scalar(select(func.count()).select_from(UpdateRecord)) == 0

    asyncio.run(scenario())


def test_shared_lock_records_progress_and_completes_workflow(update_db_factory):
    gate, release = asyncio.Event(), asyncio.Event()
    core = _Core(available=True, gate=gate)
    core.release = release
    events: list[tuple[str, dict[str, Any]]] = []
    service = UpdateService(
        update_db_factory,
        core_workflow=core,
        broadcast=lambda kind, data: events.append((kind, data)),
    )

    async def scenario():
        record = await service.start(UpdateTarget.CORE, {"channel": "stable"})
        await asyncio.wait_for(gate.wait(), timeout=2)
        with pytest.raises(AppError) as error:
            await service.start(UpdateTarget.RESOURCE, {"channel": "ota"})
        assert error.value.code == ErrorCode.UPDATE_ALREADY_RUNNING
        task = service._tasks[record.id]
        release.set()
        await asyncio.wait_for(task, timeout=2)
        stored = await service.get(record.id)
        assert stored.status == UpdateStatus.SUCCESS
        assert stored.phase == UpdatePhase.DONE
        assert any(kind == "update_progress" and data["phase"] == "downloading" for kind, data in events)
        assert any(kind == "update_progress" and data["phase"] == "restarting" for kind, data in events)

    asyncio.run(scenario())


def test_update_workflow_does_not_inherit_request_id(update_db_factory):
    class ContextWorkflow:
        def __init__(self):
            self.context = None

        async def check(self):
            return {"current": "v1", "latest": "v2", "available": True}

        async def update(self, *, channel: str, force: bool = False):
            self.context = (current_request_id.get(), current_pipeline_id.get())
            return SimpleNamespace(version="v2")

    workflow = ContextWorkflow()
    service = UpdateService(update_db_factory, core_workflow=workflow)

    async def scenario():
        request_token = current_request_id.set("request-42")
        pipeline_token = current_pipeline_id.set("pipeline-7")
        try:
            record = await service.start(UpdateTarget.CORE, {"channel": "stable"})
            assert current_request_id.get() == "request-42"
            task = service._tasks[record.id]
            await asyncio.wait_for(task, timeout=2)
            assert workflow.context == (None, "pipeline-7")
            assert current_request_id.get() == "request-42"
        finally:
            current_pipeline_id.reset(pipeline_token)
            current_request_id.reset(request_token)

    asyncio.run(scenario())


def test_core_rollback_uses_global_lock_and_update_record_without_network(
    update_db_factory,
):
    class RollbackCore:
        def __init__(self):
            self.started = asyncio.Event()
            self.release = asyncio.Event()

        def current_version(self, _client=None):
            return "v1.0.0"

        core_client = None

        async def rollback(self):
            self.started.set()
            await self.release.wait()
            return "v0.9.0"

    workflow = RollbackCore()
    service = UpdateService(update_db_factory, core_workflow=workflow)

    async def scenario():
        record = await service.rollback_core(force_interrupt=False)
        await asyncio.wait_for(workflow.started.wait(), timeout=2)
        with pytest.raises(AppError) as error:
            await service.start(UpdateTarget.GAME, {"channel": "Official"})
        assert error.value.code == ErrorCode.UPDATE_ALREADY_RUNNING
        workflow.release.set()
        await asyncio.wait_for(service._tasks[record.id], timeout=2)
        stored = await service.get(record.id)
        assert stored.target == UpdateTarget.CORE
        assert stored.channel == "stable"
        assert stored.status == UpdateStatus.SUCCESS
        assert stored.from_version == "v1.0.0"
        assert stored.to_version == "v0.9.0"
        assert await service.list(target=UpdateTarget.CORE, status=UpdateStatus.SUCCESS)

    asyncio.run(scenario())


def test_core_version_override_cannot_select_nonstable_version(update_db_factory):
    service = UpdateService(update_db_factory, core_workflow=_Core(available=True))

    async def scenario():
        record = await service.start(
            UpdateTarget.CORE,
            {"channel": "stable", "version": "v-beta"},
        )
        await asyncio.wait_for(service._tasks[record.id], timeout=2)
        stored = await service.get(record.id)
        assert stored.status == UpdateStatus.FAILED
        assert stored.error_code == ErrorCode.INVALID_PARAMETER.value
        assert service.core_workflow.update_calls == 0

    asyncio.run(scenario())


def test_daily_check_deduplicates_available_version_across_service_instances(update_db_factory):
    core, resources, game = _Core(available=True), _Resources(), _Game()
    emitted: list[tuple[str, dict[str, Any]]] = []
    first = UpdateService(
        update_db_factory,
        core_workflow=core,
        resource_workflow=resources,
        game_workflow=game,
        broadcast=lambda kind, data: emitted.append((kind, data)),
    )
    second = UpdateService(
        update_db_factory,
        core_workflow=core,
        resource_workflow=resources,
        game_workflow=game,
        broadcast=lambda kind, data: emitted.append((kind, data)),
    )

    async def scenario():
        await first.daily_check()
        await second.daily_check()
        assert [kind for kind, _ in emitted].count("update_available") == 1
        async with update_db_factory() as session:
            assert await session.scalar(select(func.count()).select_from(UpdateRecord)) == 0
            saved = await SettingRepository(session).get("updates.notified_versions")
            assert saved["core:"] == "v2"

    asyncio.run(scenario())


def test_recover_interrupted_marks_running_rows_failed(update_db_factory):
    async def scenario():
        async with update_db_factory() as session:
            session.add(
                UpdateRecord(
                    target=UpdateTarget.GAME,
                    triggered_by="manual",
                    status=UpdateStatus.RUNNING,
                    phase=UpdatePhase.DOWNLOADING,
                )
            )
            await session.commit()
        service = UpdateService(update_db_factory)
        assert await service.recover_interrupted() == 1
        async with update_db_factory() as session:
            row = await session.scalar(select(UpdateRecord))
            assert row.status == UpdateStatus.FAILED
            assert row.error_code == "UPDATE_INTERRUPTED"

    asyncio.run(scenario())


def test_available_query_failure_is_unknown_not_current(update_db_factory):
    class OfflineCore:
        async def check(self):
            raise RuntimeError("network unavailable")

    service = UpdateService(update_db_factory, core_workflow=OfflineCore())

    async def scenario():
        state = await service._inspect_core()
        assert state["available"] is None
        assert state["current"] is None

    asyncio.run(scenario())


def test_resource_all_keeps_ota_when_optional_repo_stage_fails(update_db_factory, tmp_path):
    workflow = _ResourceWorkflow(
        tmp_path, repo_changed=True, repo_error="stage-failed"
    )
    service = UpdateService(update_db_factory, resource_workflow=workflow)

    async def scenario():
        record = await service.start(
            UpdateTarget.RESOURCE, {"channel": "all"}
        )
        await asyncio.wait_for(service._tasks[record.id], timeout=2)
        stored = await service.get(record.id)
        assert stored.status == UpdateStatus.SUCCESS
        assert [item.channel for item in workflow.installed] == [ResourceChannel.OTA]
        assert workflow.reload_modes == ["wait"]
        assert '"optional_errors"' in (stored.log or "")

    asyncio.run(scenario())


def test_resource_all_ota_current_optional_repo_check_failure_is_skipped(
    update_db_factory, tmp_path
):
    class CurrentOTAOptionalRepoFailure:
        layers_root = tmp_path / "resource" / "maa-layers"

        async def check(self, _channel, **_kwargs):
            return {
                ResourceChannel.OTA: SimpleNamespace(
                    channel=ResourceChannel.OTA,
                    changed=False,
                    checksum="same-checksum",
                    error=None,
                ),
                ResourceChannel.REPO: SimpleNamespace(
                    channel=ResourceChannel.REPO,
                    changed=False,
                    error="UPDATE_MANIFEST_UNAVAILABLE: offline",
                ),
            }

    async def scenario():
        service = UpdateService(
            update_db_factory,
            resource_workflow=CurrentOTAOptionalRepoFailure(),
        )
        record = await service.start(
            UpdateTarget.RESOURCE,
            {"channel": ResourceChannel.ALL.value},
        )
        await asyncio.wait_for(service._tasks[record.id], timeout=2)
        stored = await service.get(record.id)
        assert stored.status == UpdateStatus.SKIPPED
        assert stored.error_code is None
        log = json.loads(stored.log)
        assert log["optional_errors"][0]["channel"] == ResourceChannel.REPO.value
        assert "offline" in log["optional_errors"][0]["error"]

    asyncio.run(scenario())


def test_resource_all_ota_update_succeeds_and_records_optional_repo_check_error(
    update_db_factory, tmp_path
):
    class ChangedOTAOptionalRepoFailure:
        layers_root = tmp_path / "resource" / "maa-layers"

        async def check(self, _channel, **_kwargs):
            return {
                ResourceChannel.OTA: SimpleNamespace(
                    channel=ResourceChannel.OTA,
                    changed=True,
                    checksum="sha256-new",
                    etag='"new"',
                    last_modified="today",
                    body=b'{"tasks":[]}',
                    error=None,
                ),
                ResourceChannel.REPO: SimpleNamespace(
                    channel=ResourceChannel.REPO,
                    changed=False,
                    error="UPDATE_MANIFEST_UNAVAILABLE: offline",
                ),
            }

        async def stage(self, check):
            from maa_api.services.resource_update import StagedResource

            path = tmp_path / "staged-ota.json"
            path.write_bytes(check.body)
            return StagedResource(check.channel, path, checksum=check.checksum)

        async def install_all(self, staged, *, reload_mode="wait"):
            assert staged

    async def scenario():
        service = UpdateService(
            update_db_factory,
            resource_workflow=ChangedOTAOptionalRepoFailure(),
        )
        record = await service.start(
            UpdateTarget.RESOURCE,
            {"channel": ResourceChannel.ALL.value},
        )
        await asyncio.wait_for(service._tasks[record.id], timeout=2)
        stored = await service.get(record.id)
        assert stored.status == UpdateStatus.SUCCESS
        log = json.loads(stored.log)
        assert log["optional_errors"][0]["channel"] == ResourceChannel.REPO.value

    asyncio.run(scenario())


def test_resource_all_fails_when_required_ota_check_is_unavailable(update_db_factory, tmp_path):
    workflow = _ResourceWorkflow(
        tmp_path, ota_error="OTA unavailable", repo_changed=True
    )
    service = UpdateService(update_db_factory, resource_workflow=workflow)

    async def scenario():
        record = await service.start(UpdateTarget.RESOURCE, {"channel": "all"})
        await asyncio.wait_for(service._tasks[record.id], timeout=2)
        stored = await service.get(record.id)
        assert stored.status == UpdateStatus.FAILED
        assert workflow.stage_calls == []

    asyncio.run(scenario())


def test_resource_install_failure_retry_reuses_staged_dict_check(
    update_db_factory, tmp_path
):
    class RetryResources:
        def __init__(self):
            self.layers_root = tmp_path / "resource" / "maa-layers"
            self.staging = tmp_path / "staged-ota.json"
            self.staging.write_text('{"tasks":[]}', encoding="utf-8")
            self.install_calls = 0
            self.stage_calls = 0

        async def check(self, channel, **kwargs):
            return {
                ResourceChannel.OTA: SimpleNamespace(
                    channel=ResourceChannel.OTA,
                    changed=True,
                    checksum="sha256-new",
                    etag='"new"',
                    last_modified="today",
                    error=None,
                )
            }

        async def stage(self, check):
            from maa_api.services.resource_update import StagedResource

            self.stage_calls += 1
            return StagedResource(
                ResourceChannel.OTA,
                self.staging,
                checksum=check.checksum,
                etag=check.etag,
                last_modified=check.last_modified,
            )

        async def install_all(self, staged, *, reload_mode="wait"):
            self.install_calls += 1
            if self.install_calls == 1:
                raise RuntimeError("install failed once")

    async def scenario():
        workflow = RetryResources()
        service = UpdateService(update_db_factory, resource_workflow=workflow)
        first = await service.start(UpdateTarget.RESOURCE, {"channel": "ota"})
        await asyncio.wait_for(service._tasks[first.id], timeout=2)
        failed = await service.get(first.id)
        assert failed.status == UpdateStatus.FAILED
        second = await service.retry(first.id)
        await asyncio.wait_for(service._tasks[second.id], timeout=2)
        retried = await service.get(second.id)
        assert retried.status == UpdateStatus.SUCCESS
        assert workflow.stage_calls == 1
        assert workflow.install_calls == 2

    asyncio.run(scenario())


def test_repo_asset_persists_version_from_downloaded_archive_not_check_response(
    update_db_factory, tmp_path
):
    class RepoWorkflow:
        layers_root = tmp_path / "resource" / "maa-layers"

        async def check(self, _channel, **_kwargs):
            return {
                ResourceChannel.OTA: SimpleNamespace(
                    channel=ResourceChannel.OTA, changed=False, error=None
                ),
                ResourceChannel.REPO: SimpleNamespace(
                    channel=ResourceChannel.REPO,
                    changed=True,
                    version="queried-version",
                    error=None,
                ),
            }

        async def stage(self, check):
            from maa_api.services.resource_update import StagedResource

            path = tmp_path / "repo-staged"
            (path / "resource").mkdir(parents=True, exist_ok=True)
            return StagedResource(
                ResourceChannel.REPO,
                path,
                version="actual-archive-version",
            )

        async def install_all(self, staged, *, reload_mode="wait"):
            return None

    async def scenario():
        service = UpdateService(update_db_factory, resource_workflow=RepoWorkflow())
        record = await service.start(
            UpdateTarget.RESOURCE,
            {"channel": ResourceChannel.REPO.value},
        )
        await asyncio.wait_for(service._tasks[record.id], timeout=2)
        assert (await service.get(record.id)).status == UpdateStatus.SUCCESS
        async with update_db_factory() as session:
            from maa_api.db.repositories.resource import ResourceAssetRepository
            from maa_api.domain.enums import ResourceAssetKind

            row = await ResourceAssetRepository(session).get_by_kind_name(
                ResourceAssetKind.REPO_RESOURCE, "MaaResource"
            )
            assert row is not None
            assert row.remote_version == "actual-archive-version"

    asyncio.run(scenario())


def test_resource_defer_skips_queue_preflight_and_persists_pending_reload(update_db_factory, tmp_path):
    workflow = _ResourceWorkflow(tmp_path)
    prepared = []
    service = UpdateService(
        update_db_factory,
        resource_workflow=workflow,
        prepare_update=lambda target, options: prepared.append((target, options)),
    )

    async def scenario():
        record = await service.start(
            UpdateTarget.RESOURCE,
            {"channel": "ota", "reload_mode": "defer"},
        )
        await asyncio.wait_for(service._tasks[record.id], timeout=2)
        stored = await service.get(record.id)
        assert stored.status == UpdateStatus.SUCCESS
        assert prepared == []
        assert workflow.reload_modes == ["defer"]
        status = await service.status(refresh=True)
        assert status["updates"]["resource"]["reload_pending"] is True

    asyncio.run(scenario())


def test_deferred_reload_stays_pending_in_same_process_and_clears_after_restart(
    update_db_factory,
):
    async def scenario():
        async with update_db_factory() as session:
            await SettingRepository(session).set(
                "updates.resource_reload_pending", True, updated_by="system"
            )
            await SettingRepository(session).set(
                "updates.resource_reload_pending_process", "prior-process", updated_by="system"
            )
            await session.commit()

        # This simulates a later lifespan. READY means the new child loaded the
        # staged layers, so the durable marker is now safe to clear.
        token = ["process-2:generation-1"]
        service = UpdateService(
            update_db_factory,
            resource_layers_loaded=lambda: token[0],
        )
        assert await service._get_reload_pending() is False
        async with update_db_factory() as session:
            assert await SettingRepository(session).get("updates.resource_reload_pending") is False
            assert await SettingRepository(session).get("updates.resource_reload_pending_process") == ""

        # Same process as the deferred install must never clear the marker just
        # because its unchanged child happens to be READY.
        await service._store_reload_pending(True)
        assert await service._get_reload_pending() is True
        token[0] = "process-2:generation-2"
        assert await service._get_reload_pending() is False

    asyncio.run(scenario())


def test_resource_force_reload_uses_force_interrupt_queue_gate(update_db_factory, tmp_path):
    workflow = _ResourceWorkflow(tmp_path)
    prepared: list[dict[str, Any]] = []
    service = UpdateService(
        update_db_factory,
        resource_workflow=workflow,
        prepare_update=lambda _target, options: prepared.append(dict(options)),
    )

    async def scenario():
        record = await service.start(
            UpdateTarget.RESOURCE,
            {"channel": "ota", "reload_mode": "force"},
        )
        await asyncio.wait_for(service._tasks[record.id], timeout=2)
        assert prepared and prepared[0]["force_interrupt"] is True

    asyncio.run(scenario())


def test_check_only_resource_metadata_does_not_refresh_updated_at(update_db_factory):
    old = datetime(2000, 1, 1)

    async def scenario():
        async with update_db_factory() as session:
            session.add_all(
                [
                    ResourceAsset(
                        kind="repo_resource",
                        name="MaaResource",
                        path="maa-layers/repo",
                        updated_at=old,
                    ),
                    ResourceAsset(
                        kind="ota_resource",
                        name="resource/tasks.json",
                        path="maa-layers/cache/tasks.json",
                        checksum="a" * 64,
                        updated_at=old,
                    ),
                ]
            )
            await session.commit()

        service = UpdateService(update_db_factory, resource_workflow=_Resources())
        await service._persist_resource_checks(
            {
                ResourceChannel.OTA: SimpleNamespace(error=None),
                ResourceChannel.REPO: SimpleNamespace(error=None),
            }
        )
        async with update_db_factory() as session:
            rows = (await session.scalars(select(ResourceAsset))).all()
            assert len(rows) == 2
            assert all(row.updated_at == old for row in rows)
            assert all(row.last_checked_at is not None for row in rows)

    asyncio.run(scenario())


def test_expired_core_retry_directory_is_pruned_but_stage_directory_is_kept(
    update_db_factory, tmp_path
):
    class CoreCache:
        def __init__(self):
            self.temp_root = tmp_path / "core-temp"
            self.temp_root.mkdir()

    workflow = CoreCache()
    expired = workflow.temp_root / "v1.0"
    expired.mkdir()
    active = workflow.temp_root / "v2.0"
    active.mkdir()
    (active / ".stage-active").mkdir()
    old_time = time.time() - 8 * 24 * 60 * 60
    os.utime(expired, (old_time, old_time))
    os.utime(active, (old_time, old_time))
    service = UpdateService(update_db_factory, core_workflow=workflow)

    async def scenario():
        await service._prune_expired_caches(UpdateTarget.CORE)
        assert not expired.exists()
        assert active.exists()

    asyncio.run(scenario())


def test_progress_broadcast_throttle_and_db_write_interval(update_db_factory):
    now = [100.0]
    events: list[dict[str, Any]] = []
    service = UpdateService(
        update_db_factory,
        clock=lambda: now[0],
        broadcast=lambda kind, data: events.append(data) if kind == "update_progress" else None,
    )

    async def scenario():
        async with update_db_factory() as session:
            record = await UpdateRepository(session).create(
                UpdateRecord(
                    target=UpdateTarget.CORE,
                    triggered_by="manual",
                    status=UpdateStatus.RUNNING,
                )
            )
            await session.commit()
        await service._emit_progress(
            record.id, UpdateTarget.CORE,
            {"phase": "downloading", "percent": 0, "downloaded": 0, "total": 100},
        )
        now[0] = 100.1
        await service._emit_progress(
            record.id, UpdateTarget.CORE,
            {"phase": "downloading", "percent": 0.5, "downloaded": 0, "total": 100},
        )
        now[0] = 100.2
        await service._emit_progress(
            record.id, UpdateTarget.CORE,
            {"phase": "downloading", "percent": 1, "downloaded": 1, "total": 100},
        )
        now[0] = 100.8
        await service._emit_progress(
            record.id, UpdateTarget.CORE,
            {"phase": "downloading", "percent": 1.2, "downloaded": 1, "total": 100},
        )
        assert [event["percent"] for event in events] == [0.0, 1.0, 1.2]
        async with update_db_factory() as session:
            stored = await session.get(UpdateRecord, record.id)
            assert stored.progress == 0
        now[0] = 105.1
        await service._emit_progress(
            record.id, UpdateTarget.CORE,
            {"phase": "downloading", "percent": 2, "downloaded": 2, "total": 100},
        )
        async with update_db_factory() as session:
            stored = await session.get(UpdateRecord, record.id)
            assert stored.progress == 2 and stored.bytes_done == 2

    asyncio.run(scenario())
