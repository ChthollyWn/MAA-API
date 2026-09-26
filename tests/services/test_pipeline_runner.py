"""PipelineRunner resolves persisted Copilot asset ids only at MaaCore append."""

from __future__ import annotations

import asyncio
import hashlib
import json
from pathlib import Path

import pytest
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker
from sqlalchemy.pool import NullPool
from sqlmodel import SQLModel

import maa_api.db.models  # noqa: F401
from maa_api.agent.incidents import AgentIncidentService
from maa_api.core.registry import CoreRegistry, DEFAULT_CORE_ID
from maa_api.db.models import AgentSession, Pipeline, ResourceAsset, Task
from maa_api.db.repositories.agent import AgentMessageRepository, AgentSessionRepository
from maa_api.db.repositories.pipeline import PipelineRepository
from maa_api.db.session import make_engine
from maa_api.domain.enums import (
    PipelineSource,
    PipelineStatus,
    Priority,
    ResourceAssetKind,
    TaskStatus,
)
from maa_api.domain.errors import AppError, ErrorCode
from maa_api.services.pipeline_runner import PipelineRunner
from maa_api.services.resource_service import ResourceService
from maa_api.settings import Settings
from maa_api.services import resource_service as resource_service_module
from tests.fakes.fake_asst import FakeAsst, FakeScript, STUCK_SCRIPT, SUCCESS_SCRIPT

ASSET_ID = "a" * 32


def _copilot() -> dict:
    return {
        "minimum_required": "v4.0.0",
        "stage_name": "1-7",
        "doc": {"title": "test operation", "details": "validated sample"},
        "opers": [{"name": "Amiya", "skill": 1}],
        "actions": [
            {"type": "Deploy", "name": "Amiya", "location": [3, 4], "direction": "Left"}
        ],
    }


class FakeCoreClient:
    """Async CoreClient adapter around the project's callback-driven FakeAsst."""

    def __init__(self, asst: FakeAsst) -> None:
        self.asst = asst
        self.handlers = {}
        self.loop = asyncio.get_running_loop()
        self.snapshot_path: Path | None = None
        self.snapshot_content: dict | None = None
        self.snapshot_existed_at_append = False
        self.append_event = asyncio.Event()
        self.started_event = asyncio.Event()
        asst.set_callback(self._on_callback)

    def on(self, event_type, handler) -> None:
        self.handlers[event_type] = handler

    def _on_callback(self, message: int, details: bytes, _arg) -> None:
        handler = self.handlers.get("CALLBACK")
        if handler is not None:
            payload = {"msg": message, "details": json.loads(details)}
            self.loop.call_soon_threadsafe(handler, payload)

    async def append_task(self, type_name: str, params: dict) -> int:
        if type_name == "Copilot":
            self.snapshot_path = Path(params["filename"])
            self.snapshot_existed_at_append = self.snapshot_path.is_file()
            self.snapshot_content = json.loads(
                self.snapshot_path.read_text(encoding="utf-8")
            )
            self.append_event.set()
        task_id = self.asst.append_task(type_name, params)
        if not task_id:
            raise AppError(
                ErrorCode.CORE_COMMAND_FAILED,
                f"MaaCore 拒绝任务 {type_name!r}",
                {"cmd_id": "fake-command", "error": {"code": "CORE_COMMAND_FAILED"}},
            )
        return task_id

    async def start(self) -> bool:
        started = self.asst.start()
        self.started_event.set()
        return started

    async def stop(self) -> bool:
        return self.asst.stop()


class SimpleSupervisor:
    state = "ready"
    generation = 1


class FakeIncidentRuntime:
    def __init__(self):
        self.calls = []

    async def run_incident(self, session_id, request, allowed_tools):
        self.calls.append((session_id, request, frozenset(allowed_tools)))
        return {"status": "completed"}


async def _create_schema(engine) -> None:
    async with engine.begin() as connection:
        await connection.run_sync(SQLModel.metadata.create_all)


async def _seed_copilot(
    factory,
    resource_root: Path,
    *,
    deleted: bool = False,
    external: bool = False,
):
    pipeline = Pipeline(
        id="pipeline-copilot",
        source=PipelineSource.AGENT,
        priority=Priority.AGENT,
        status=PipelineStatus.RUNNING,
        task_count=1,
    )
    task = Task(
        id="task-copilot",
        pipeline_id=pipeline.id,
        order_index=0,
        type_name="Copilot",
        task_name="执行作业",
        params={"asset_id": ASSET_ID, "formation": False},
        status=TaskStatus.PENDING,
    )
    asset_content = _copilot()
    asset = ResourceAsset(
        id=ASSET_ID,
        kind=ResourceAssetKind.COPILOT,
        name="test operation",
        content=None if external else asset_content,
        path=f"agent-assets/copilot/{ASSET_ID}.json" if external else None,
        checksum=hashlib.sha256(
            json.dumps(
                asset_content,
                ensure_ascii=False,
                sort_keys=True,
                separators=(",", ":"),
            ).encode("utf-8")
        ).hexdigest(),
    )
    async with factory() as session:
        session.add(pipeline)
        session.add(task)
        session.add(asset)
        await session.commit()
    if external:
        external_path = resource_root / "agent-assets" / "copilot" / f"{ASSET_ID}.json"
        external_path.parent.mkdir(parents=True, exist_ok=True)
        external_path.write_text(json.dumps(asset_content), encoding="utf-8")
    if deleted:
        async with factory() as session:
            current = await session.get(ResourceAsset, asset.id)
            await session.delete(current)
            await session.commit()
    return pipeline, task


@pytest.fixture
def runner_env(tmp_path: Path):
    engine = make_engine(
        f"sqlite+aiosqlite:///{tmp_path / 'copilot-runner.db'}", poolclass=NullPool
    )
    asyncio.run(_create_schema(engine))
    factory = async_sessionmaker(engine, expire_on_commit=False, class_=AsyncSession)
    resource_root = tmp_path / "resource"
    resource_root.mkdir()

    def make(
        script: FakeScript = SUCCESS_SCRIPT,
        *,
        session_factory_override=None,
    ):
        asst = FakeAsst(script=script, delay_scale=0)
        core = FakeCoreClient(asst)
        registry = CoreRegistry()
        registry.register(DEFAULT_CORE_ID, core)
        resource_service = ResourceService(factory, resource_root=resource_root)
        runner = PipelineRunner(
            registry,
            SimpleSupervisor(),
            session_factory_override or factory,
            resource_service=resource_service,
        )
        runner._running = True
        return runner, asst, core

    yield factory, resource_root, make
    engine.sync_engine.dispose()


def test_runner_resolves_copilot_path_only_for_core_append(runner_env) -> None:
    factory, resource_root, make = runner_env

    async def scenario() -> None:
        pipeline, task = await _seed_copilot(factory, resource_root)
        runner, asst, core = make()
        await runner._execute_pipeline(pipeline, [task])

        assert core.snapshot_path is not None
        expected_path = core.snapshot_path
        assert expected_path.relative_to(resource_root).parts[:3] == (
            "agent-assets",
            "copilot",
            "runs",
        )
        assert core.snapshot_existed_at_append is True
        assert core.snapshot_content == _copilot()
        assert asst.calls_of("append_task")[0][1] == (
            "Copilot",
            {"filename": str(expected_path), "formation": False},
        )
        async with factory() as session:
            stored_task = await session.get(Task, task.id)
            stored_pipeline = await session.get(Pipeline, pipeline.id)
            assert stored_task.params == {"asset_id": ASSET_ID, "formation": False}
            assert stored_task.status == TaskStatus.COMPLETED
            assert stored_pipeline.status == PipelineStatus.COMPLETED
        assert not expected_path.exists()
        assert not expected_path.parent.exists()
        await runner.stop()
        asst.stop()

    asyncio.run(scenario())


def test_runner_fails_pipeline_when_copilot_asset_was_removed(runner_env) -> None:
    factory, resource_root, make = runner_env

    async def scenario() -> None:
        pipeline, task = await _seed_copilot(
            factory, resource_root, deleted=True
        )
        runner, asst, _core = make()
        await runner._execute_pipeline(pipeline, [task])

        assert asst.calls_of("append_task") == []
        async with factory() as session:
            stored_task = await session.get(Task, task.id)
            stored_pipeline = await session.get(Pipeline, pipeline.id)
            assert stored_task.params["asset_id"] == ASSET_ID
            assert stored_task.status == TaskStatus.FAILED
            assert stored_task.error_code == ErrorCode.RESOURCE_ASSET_NOT_FOUND
            assert stored_pipeline.status == PipelineStatus.FAILED
            assert stored_pipeline.error_code == ErrorCode.RESOURCE_ASSET_NOT_FOUND
        await runner.stop()
        asst.stop()

    asyncio.run(scenario())


def test_terminal_and_startup_recovered_failures_notify_incidents_once(runner_env) -> None:
    factory, _resource_root, make = runner_env

    async def scenario() -> None:
        async with factory() as session:
            session.add(
                Pipeline(
                    id="pipeline-recovered-failure",
                    source=PipelineSource.MANUAL,
                    priority=Priority.MANUAL,
                    status=PipelineStatus.RUNNING,
                    task_count=0,
                )
            )
            session.add(
                Pipeline(
                    id="pipeline-old-core-epoch",
                    source=PipelineSource.MANUAL,
                    priority=Priority.MANUAL,
                    status=PipelineStatus.FAILED,
                    task_count=0,
                    core_epoch=1,
                    error_code=ErrorCode.CORE_CRASHED,
                    error_message="old service generation 1 crashed",
                )
            )
            await session.commit()
        runner, asst, _core = make()

        # Startup recovery happens before the incident service is bound.
        await runner._recover_interrupted()
        assert runner.recovered_pipeline_ids == ("pipeline-recovered-failure",)
        assert runner.core_crashed_pipeline_ids(1) == ()

        calls = []

        class FakeIncidents:
            async def pipeline_failed(self, pipeline_id):
                calls.append(pipeline_id)

        runner.bind_incident_service(FakeIncidents())
        for pipeline_id in runner.recovered_pipeline_ids:
            runner.schedule_pipeline_incident(pipeline_id)

        # A normally completed failure is reported after its commit only once.
        async with factory() as session:
            session.add(
                Pipeline(
                    id="pipeline-terminal-failure",
                    source=PipelineSource.MANUAL,
                    priority=Priority.MANUAL,
                    status=PipelineStatus.RUNNING,
                    task_count=0,
                )
            )
            await session.commit()
        await runner._finish_pipeline(
            "pipeline-terminal-failure",
            PipelineStatus.FAILED,
            error_code=ErrorCode.INTERNAL_ERROR,
            error_message="worker failed",
        )
        await runner._finish_pipeline(
            "pipeline-terminal-failure",
            PipelineStatus.FAILED,
            error_code=ErrorCode.INTERNAL_ERROR,
            error_message="duplicate terminal attempt",
        )
        await asyncio.gather(*tuple(runner._incident_tasks))

        assert sorted(calls) == [
            "pipeline-recovered-failure",
            "pipeline-terminal-failure",
        ]
        async with factory() as session:
            recovered = await session.get(Pipeline, "pipeline-recovered-failure")
        assert recovered.status == PipelineStatus.FAILED
        assert recovered.error_code == ErrorCode.CORE_CRASHED
        await runner.stop()
        asst.stop()

    asyncio.run(scenario())


def test_core_crash_after_claim_before_active_is_coalesced_into_claimed_pipeline(
    runner_env, monkeypatch
) -> None:
    factory, _resource_root, make = runner_env

    async def scenario() -> None:
        async with factory() as session:
            pipeline = Pipeline(
                id="pipeline-claimed-crash",
                source=PipelineSource.MANUAL,
                priority=Priority.MANUAL,
                status=PipelineStatus.PENDING,
                task_count=1,
            )
            session.add(pipeline)
            await session.flush()
            session.add(
                Task(
                    id="task-claimed-crash",
                    pipeline_id=pipeline.id,
                    order_index=0,
                    type_name="Fight",
                    task_name="Fight",
                    params={},
                    status=TaskStatus.PENDING,
                )
            )
            await session.commit()

        event_state = {"commit_armed": False, "claim_armed": False}

        class CommitInterleaveSession(AsyncSession):
            async def commit(self):
                await super().commit()
                if not event_state["commit_armed"]:
                    return
                event_state["commit_armed"] = False
                event_state["runner"].notify_core_crash(
                    {"generation": 1, "reason": "process_exit"}
                )
                event_state["core_result"] = await event_state[
                    "incidents"
                ].core_crashed(1, {"generation": 1, "reason": "process_exit"})

        original_claim_next = PipelineRepository.claim_next

        async def claim_next_with_interleaved_crash(repo, *args, **kwargs):
            result = await original_claim_next(repo, *args, **kwargs)
            if event_state["claim_armed"]:
                event_state["claim_armed"] = False
                event_state["runner"].notify_core_crash(
                    {"generation": 1, "reason": "process_exit"}
                )
                event_state["claim_result"] = await event_state[
                    "incidents"
                ].core_crashed(1, {"generation": 1, "reason": "process_exit"})
            return result

        monkeypatch.setattr(
            PipelineRepository,
            "claim_next",
            claim_next_with_interleaved_crash,
        )

        interleave_factory = async_sessionmaker(
            bind=factory.kw["bind"],
            class_=CommitInterleaveSession,
            expire_on_commit=False,
        )
        runner, asst, core = make(session_factory_override=interleave_factory)
        runner._loop = asyncio.get_running_loop()
        incident_runtime = FakeIncidentRuntime()
        incidents = AgentIncidentService(
            factory,
            incident_runtime,
            settings_provider=lambda: Settings(),
            pipeline_runner=runner,
            boot_id="11111111-1111-4111-8111-111111111111",
        )
        runner.bind_incident_service(incidents)
        event_state.update(
            runner=runner,
            incidents=incidents,
            claim_armed=True,
            commit_armed=True,
        )
        claimed, tasks = await runner._claim_next()
        assert claimed is not None and claimed.id == "pipeline-claimed-crash"
        assert runner.active_pipeline_id == claimed.id
        assert event_state["claim_result"].session_id is None
        assert event_state["core_result"].session_id is None
        await runner._execute_pipeline(claimed, tasks)
        await asyncio.gather(*tuple(runner._incident_tasks))

        assert asst.calls_of("append_task") == []
        assert runner.core_crashed_pipeline_ids(1) == (claimed.id,)
        assert len(incident_runtime.calls) == 1
        async with factory() as session:
            stored_pipeline = await session.get(Pipeline, claimed.id)
            stored_task = await session.get(Task, "task-claimed-crash")
            sessions = await AgentSessionRepository(session).list_recent()
        assert stored_pipeline.status == PipelineStatus.FAILED
        assert stored_pipeline.error_code == ErrorCode.CORE_CRASHED
        assert stored_task.status == TaskStatus.FAILED
        assert len(sessions) == 1
        assert sessions[0].incident_key == "pipeline:pipeline-claimed-crash"
        await runner.stop()
        asst.stop()

    asyncio.run(scenario())


def test_pending_core_crash_takes_precedence_over_cancelled_claim(runner_env) -> None:
    factory, resource_root, make = runner_env

    async def scenario() -> None:
        pipeline, task = await _seed_copilot(factory, resource_root)
        runner, asst, _core = make()
        runner._loop = asyncio.get_running_loop()
        runner._claimed_pipeline = pipeline
        runner._claimed_pipeline_id = pipeline.id
        runner._claimed_pipeline_epoch = pipeline.core_epoch
        runner._cancel_requested_ids.add(pipeline.id)
        runner.notify_core_crash({"generation": 1, "reason": "process_exit"})

        await runner._execute_pipeline(pipeline, [task])

        async with factory() as session:
            stored_pipeline = await session.get(Pipeline, pipeline.id)
            stored_task = await session.get(Task, task.id)
        assert stored_pipeline.status == PipelineStatus.FAILED
        assert stored_pipeline.error_code == ErrorCode.CORE_CRASHED
        assert stored_task.status == TaskStatus.FAILED
        assert stored_task.error_code == ErrorCode.CORE_CRASHED
        assert asst.calls_of("append_task") == []
        await runner.stop()
        asst.stop()

    asyncio.run(scenario())


def test_core_crash_staged_during_empty_claim_becomes_idle_incident(
    runner_env, monkeypatch
) -> None:
    factory, _resource_root, make = runner_env

    async def scenario() -> None:
        runner, asst, _core = make()
        runner._loop = asyncio.get_running_loop()
        incident_runtime = FakeIncidentRuntime()
        incidents = AgentIncidentService(
            factory,
            incident_runtime,
            settings_provider=lambda: Settings(),
            pipeline_runner=runner,
            boot_id="11111111-1111-4111-8111-111111111111",
        )
        runner.bind_incident_service(incidents)
        result_state = {}

        original_claim_next = PipelineRepository.claim_next

        async def intercepted_empty_claim(repo, *args, **kwargs):
            claimed = await original_claim_next(repo, *args, **kwargs)
            runner.notify_core_crash({"generation": 1, "reason": "process_exit"})
            result_state["coalesced"] = await incidents.core_crashed(
                1, {"generation": 1, "reason": "process_exit"}
            )
            return claimed

        monkeypatch.setattr(PipelineRepository, "claim_next", intercepted_empty_claim)
        assert await runner._claim_next() == (None, [])
        assert result_state["coalesced"].session_id is None
        await asyncio.gather(*tuple(runner._incident_tasks))

        async with factory() as session:
            sessions = await AgentSessionRepository(session).list_recent()
        assert len(sessions) == 1
        assert sessions[0].incident_key == (
            "core:default:11111111-1111-4111-8111-111111111111:1"
        )
        assert len(incident_runtime.calls) == 1
        await runner.stop()
        asst.stop()

    asyncio.run(scenario())


def test_core_crash_during_failed_claim_is_scheduled_after_claim_marker_clears(
    runner_env,
) -> None:
    factory, resource_root, make = runner_env

    async def scenario() -> None:
        pipeline, task = await _seed_copilot(factory, resource_root)
        async with factory() as session:
            stored = await session.get(Pipeline, pipeline.id)
            stored.status = PipelineStatus.PENDING
            stored.started_at = None
            await session.commit()

        state = {"armed": False}

        class FailingCommitSession(AsyncSession):
            async def commit(self):
                if state["armed"]:
                    state["armed"] = False
                    await self.rollback()
                    state["runner"].notify_core_crash(
                        {"generation": 1, "reason": "process_exit"}
                    )
                    state["core_result"] = await state["incidents"].core_crashed(
                        1, {"generation": 1, "reason": "process_exit"}
                    )
                    raise RuntimeError("claim commit failed")
                await super().commit()

        session_factory = async_sessionmaker(
            bind=factory.kw["bind"],
            class_=FailingCommitSession,
            expire_on_commit=False,
        )
        runner, asst, _core = make(session_factory_override=session_factory)
        runner._loop = asyncio.get_running_loop()
        incident_runtime = FakeIncidentRuntime()
        incidents = AgentIncidentService(
            factory,
            incident_runtime,
            settings_provider=lambda: Settings(),
            pipeline_runner=runner,
            boot_id="11111111-1111-4111-8111-111111111111",
        )
        runner.bind_incident_service(incidents)
        state.update(runner=runner, incidents=incidents, armed=True)

        with pytest.raises(RuntimeError, match="claim commit failed"):
            await runner._claim_next()
        await asyncio.gather(*tuple(runner._incident_tasks))

        assert runner.is_claiming_core_generation(1) is False
        assert state["core_result"].session_id is None
        async with factory() as session:
            stored = await session.get(Pipeline, pipeline.id)
            sessions = await AgentSessionRepository(session).list_recent()
        assert stored.status == PipelineStatus.PENDING
        assert len(sessions) == 1
        assert sessions[0].incident_key == (
            "core:default:11111111-1111-4111-8111-111111111111:1"
        )
        assert len(incident_runtime.calls) == 1
        await runner.stop()
        asst.stop()

    asyncio.run(scenario())


def test_claim_commit_applied_before_error_uses_pipeline_incident_if_release_fails(
    runner_env, monkeypatch
) -> None:
    factory, resource_root, make = runner_env

    async def scenario() -> None:
        pipeline, _task = await _seed_copilot(factory, resource_root)
        async with factory() as session:
            stored = await session.get(Pipeline, pipeline.id)
            stored.status = PipelineStatus.PENDING
            stored.started_at = None
            await session.commit()

        state = {"armed": False}

        class CommitThenRaiseSession(AsyncSession):
            async def commit(self):
                if state["armed"]:
                    state["armed"] = False
                    await super().commit()
                    raise RuntimeError("commit response lost")
                await super().commit()

        session_factory = async_sessionmaker(
            bind=factory.kw["bind"],
            class_=CommitThenRaiseSession,
            expire_on_commit=False,
        )
        runner, asst, _core = make(session_factory_override=session_factory)
        runner._loop = asyncio.get_running_loop()
        class ContextRuntime(FakeIncidentRuntime):
            async def run_incident(self, session_id, request, allowed_tools):
                async with factory() as session:
                    messages = await AgentMessageRepository(session).list_by_session(
                        session_id
                    )
                self.initial_context = json.loads(messages[0].content or "{}")
                return await super().run_incident(session_id, request, allowed_tools)

        incidents_runtime = ContextRuntime()
        incidents = AgentIncidentService(
            factory,
            incidents_runtime,
            settings_provider=lambda: Settings(),
            pipeline_runner=runner,
            boot_id="11111111-1111-4111-8111-111111111111",
        )
        runner.bind_incident_service(incidents)
        state.update(runner=runner, incidents=incidents, armed=True)

        async def fail_release(*_args, **_kwargs):
            state["runner"].notify_core_crash(
                {"generation": 1, "reason": "process_exit"}
            )
            state["coalesced"] = await state["incidents"].core_crashed(
                1, {"generation": 1, "reason": "process_exit"}
            )
            raise OSError("queue release unavailable")

        monkeypatch.setattr(PipelineRepository, "release_failed_claim", fail_release)

        with pytest.raises(RuntimeError, match="commit response lost"):
            await runner._claim_next()
        await asyncio.gather(*tuple(runner._incident_tasks))
        late_core = await incidents.core_crashed(
            1, {"generation": 1, "reason": "process_exit"}
        )
        async with factory() as session:
            stored = await session.get(Pipeline, pipeline.id)
            sessions = await AgentSessionRepository(session).list_recent()
        assert stored.status == PipelineStatus.FAILED
        assert stored.error_code == ErrorCode.CORE_CRASHED
        assert incidents_runtime.initial_context["failure_object"]["error_code"] == (
            ErrorCode.CORE_CRASHED.value
        )
        assert state["coalesced"].session_id is None
        assert late_core.session_id is None
        assert late_core.status == "coalesced_with_failed_pipeline_claim"
        assert len(sessions) == 1
        assert sessions[0].incident_key == f"pipeline:{pipeline.id}"
        assert len(incidents_runtime.calls) == 1
        await runner.stop()
        asst.stop()

    asyncio.run(scenario())


def test_unreleased_failed_claim_without_core_crash_is_terminalized(
    runner_env, monkeypatch
) -> None:
    factory, resource_root, make = runner_env

    async def scenario() -> None:
        pipeline, _task = await _seed_copilot(factory, resource_root)
        async with factory() as session:
            stored = await session.get(Pipeline, pipeline.id)
            stored.status = PipelineStatus.PENDING
            stored.started_at = None
            await session.commit()

        state = {"armed": False}

        class CommitThenRaiseSession(AsyncSession):
            async def commit(self):
                if state["armed"]:
                    state["armed"] = False
                    await super().commit()
                    raise RuntimeError("claim commit response lost")
                await super().commit()

        session_factory = async_sessionmaker(
            bind=factory.kw["bind"],
            class_=CommitThenRaiseSession,
            expire_on_commit=False,
        )
        runner, asst, _core = make(session_factory_override=session_factory)
        runner._loop = asyncio.get_running_loop()
        state["armed"] = True

        async def fail_release(*_args, **_kwargs):
            raise OSError("queue release unavailable")

        monkeypatch.setattr(PipelineRepository, "release_failed_claim", fail_release)
        with pytest.raises(RuntimeError, match="claim commit response lost"):
            await runner._claim_next()

        async with factory() as session:
            stored = await session.get(Pipeline, pipeline.id)
        assert stored.status == PipelineStatus.FAILED
        assert stored.error_code == ErrorCode.INTERNAL_ERROR
        await runner.stop()
        asst.stop()

    asyncio.run(scenario())


def test_late_failed_claim_crash_updates_its_pipeline_during_newer_active_generation(
    runner_env,
) -> None:
    factory, resource_root, make = runner_env

    async def scenario() -> None:
        pipeline, _task = await _seed_copilot(factory, resource_root)
        async with factory() as session:
            stored = await session.get(Pipeline, pipeline.id)
            stored.status = PipelineStatus.FAILED
            stored.error_code = ErrorCode.INTERNAL_ERROR
            stored.error_message = "claim 释放失败"
            await session.commit()

        runner, asst, _core = make()
        runner._loop = asyncio.get_running_loop()
        runner._active = type(
            "NewGenerationPipeline",
            (),
            {"pipeline_id": "new-pipeline", "core_epoch": 2, "core_crashed": False, "task": None},
        )()
        runner._claim_failure_pipeline_by_generation[1] = pipeline.id
        incident_runtime = FakeIncidentRuntime()
        incidents = AgentIncidentService(
            factory,
            incident_runtime,
            settings_provider=lambda: Settings(),
            pipeline_runner=runner,
            boot_id="11111111-1111-4111-8111-111111111111",
        )
        runner.bind_incident_service(incidents)

        runner.notify_core_crash({"generation": 1, "reason": "process_exit"})
        await asyncio.gather(*tuple(runner._incident_tasks))
        await incidents.pipeline_failed(pipeline.id)
        coalesced = await incidents.core_crashed(
            1, {"generation": 1, "reason": "process_exit"}
        )

        async with factory() as session:
            stored = await session.get(Pipeline, pipeline.id)
            sessions = await AgentSessionRepository(session).list_recent()
        assert stored.status == PipelineStatus.FAILED
        assert stored.error_code == ErrorCode.CORE_CRASHED
        assert runner._active.core_crashed is False
        assert coalesced.session_id is None
        assert len(sessions) == 1
        assert sessions[0].incident_key == f"pipeline:{pipeline.id}"
        assert len(incident_runtime.calls) == 1
        runner._active = None
        await runner.stop()
        asst.stop()

    asyncio.run(scenario())


def test_core_crash_during_cancel_commit_promotes_cancelled_pipeline(
    runner_env, monkeypatch
) -> None:
    factory, resource_root, make = runner_env

    async def scenario() -> None:
        pipeline, task = await _seed_copilot(factory, resource_root)
        runner, asst, _core = make()
        runner._loop = asyncio.get_running_loop()
        runner._claimed_pipeline = pipeline
        runner._cancel_requested_ids.add(pipeline.id)
        cancel_rows = runner._cancel_pipeline_rows

        async def cancel_then_crash(pipeline_id: str) -> None:
            await cancel_rows(pipeline_id)
            runner.notify_core_crash({"generation": 1, "reason": "process_exit"})

        monkeypatch.setattr(runner, "_cancel_pipeline_rows", cancel_then_crash)
        await runner._execute_pipeline(pipeline, [task])

        async with factory() as session:
            stored_pipeline = await session.get(Pipeline, pipeline.id)
            stored_task = await session.get(Task, task.id)
        assert stored_pipeline.status == PipelineStatus.FAILED
        assert stored_pipeline.error_code == ErrorCode.CORE_CRASHED
        assert stored_task.status == TaskStatus.FAILED
        assert stored_task.error_code == ErrorCode.CORE_CRASHED
        assert runner.core_crashed_pipeline_ids(1) == (pipeline.id,)
        await runner.stop()
        asst.stop()

    asyncio.run(scenario())


def test_core_crash_during_schedule_defer_commit_promotes_failed_pipeline(
    runner_env, monkeypatch
) -> None:
    factory, resource_root, make = runner_env

    async def scenario() -> None:
        pipeline, task = await _seed_copilot(factory, resource_root)
        async with factory() as session:
            stored = await session.get(Pipeline, pipeline.id)
            stored.source = PipelineSource.SCHEDULED
            await session.commit()
        pipeline.source = PipelineSource.SCHEDULED

        class UnavailableDevice:
            async def ensure_available(self, *, timeout: float):
                return False

            def snapshot(self):
                return {"state": "unavailable"}

        runner, asst, _core = make()
        runner._loop = asyncio.get_running_loop()
        runner.device_manager = UnavailableDevice()
        runner._claimed_pipeline = pipeline
        original_defer = PipelineRepository.defer_running

        async def defer_then_crash(repo, pipeline_id, deferred_until):
            deferred = await original_defer(repo, pipeline_id, deferred_until)
            runner.notify_core_crash({"generation": 1, "reason": "process_exit"})
            return deferred

        monkeypatch.setattr(PipelineRepository, "defer_running", defer_then_crash)
        await runner._execute_pipeline(pipeline, [task])

        async with factory() as session:
            stored_pipeline = await session.get(Pipeline, pipeline.id)
            stored_task = await session.get(Task, task.id)
        assert stored_pipeline.status == PipelineStatus.FAILED
        assert stored_pipeline.error_code == ErrorCode.CORE_CRASHED
        assert stored_task.status == TaskStatus.FAILED
        assert stored_task.error_code == ErrorCode.CORE_CRASHED
        assert runner.core_crashed_pipeline_ids(1) == (pipeline.id,)
        await runner.stop()
        asst.stop()

    asyncio.run(scenario())


def test_existing_core_crashed_row_still_schedules_pipeline_incident(runner_env, monkeypatch) -> None:
    factory, resource_root, make = runner_env

    async def scenario() -> None:
        pipeline, task = await _seed_copilot(factory, resource_root)
        async with factory() as session:
            stored = await session.get(Pipeline, pipeline.id)
            stored.status = PipelineStatus.FAILED
            stored.error_code = ErrorCode.CORE_CRASHED
            stored.error_message = "MaaCore 子进程中断，流水线已停止"
            await session.commit()
        runner, asst, _core = make()
        runner._active = type(
            "ActiveCrash",
            (),
            {"pipeline_id": pipeline.id, "core_epoch": 1, "core_crashed": True, "task": None},
        )()
        calls = []
        monkeypatch.setattr(runner, "schedule_pipeline_incident", calls.append)

        await runner._record_core_crash_terminal(pipeline.id)

        assert calls == [pipeline.id]
        async with factory() as session:
            stored_pipeline = await session.get(Pipeline, pipeline.id)
            stored_task = await session.get(Task, task.id)
        assert stored_pipeline.status == PipelineStatus.FAILED
        assert stored_pipeline.error_code == ErrorCode.CORE_CRASHED
        assert stored_task.status == TaskStatus.FAILED
        assert stored_task.error_code == ErrorCode.CORE_CRASHED
        await runner.stop()
        asst.stop()

    asyncio.run(scenario())


def test_runner_does_not_append_when_inline_copilot_cannot_be_written(
    runner_env, monkeypatch
) -> None:
    factory, resource_root, make = runner_env

    def fail_write(_target, _content):
        raise OSError("simulated disk write failure")

    monkeypatch.setattr(resource_service_module, "_atomic_write_json", fail_write)

    async def scenario() -> None:
        pipeline, task = await _seed_copilot(factory, resource_root)
        runner, asst, _core = make()
        await runner._execute_pipeline(pipeline, [task])

        assert asst.calls_of("append_task") == []
        async with factory() as session:
            stored_task = await session.get(Task, task.id)
            stored_pipeline = await session.get(Pipeline, pipeline.id)
            assert stored_task.status == TaskStatus.FAILED
            assert stored_task.error_code == ErrorCode.RESOURCE_LOAD_FAILED
            assert stored_pipeline.status == PipelineStatus.FAILED
            assert stored_pipeline.error_code == ErrorCode.RESOURCE_LOAD_FAILED
        await runner.stop()
        asst.stop()

    asyncio.run(scenario())


def test_runner_persists_copilot_core_rejection_as_failed(runner_env) -> None:
    factory, resource_root, make = runner_env
    rejected = FakeScript(
        name="append-rejected",
        events=SUCCESS_SCRIPT.events,
        append_task_fails=True,
    )

    async def scenario() -> None:
        pipeline, task = await _seed_copilot(factory, resource_root)
        runner, asst, core = make(rejected)
        await runner._execute_pipeline(pipeline, [task])

        assert asst.calls_of("append_task")[0][1][0] == "Copilot"
        assert core.snapshot_existed_at_append is True
        assert core.snapshot_path is not None
        assert not core.snapshot_path.exists()
        async with factory() as session:
            stored_task = await session.get(Task, task.id)
            stored_pipeline = await session.get(Pipeline, pipeline.id)
            assert stored_task.status == TaskStatus.FAILED
            assert stored_task.error_code == ErrorCode.CORE_COMMAND_FAILED
            assert stored_pipeline.status == PipelineStatus.FAILED
            assert stored_pipeline.error_code == ErrorCode.CORE_COMMAND_FAILED
        await runner.stop()
        asst.stop()

    asyncio.run(scenario())


@pytest.mark.parametrize("source_change", ["mutate", "remove"])
def test_runner_uses_snapshot_if_external_source_changes_after_resolution(
    runner_env, monkeypatch, source_change
) -> None:
    factory, resource_root, make = runner_env

    async def scenario() -> None:
        pipeline, task = await _seed_copilot(
            factory, resource_root, external=True
        )
        runner, asst, core = make()
        source = resource_root / "agent-assets" / "copilot" / f"{ASSET_ID}.json"
        original_resolve = runner.resource_service.resolve_copilot_path

        async def resolve_then_change(asset_id: str) -> Path:
            snapshot = await original_resolve(asset_id)
            if source_change == "remove":
                source.unlink()
            else:
                changed = _copilot() | {"stage_name": "changed-after-resolution"}
                source.write_text(json.dumps(changed), encoding="utf-8")
            return snapshot

        monkeypatch.setattr(
            runner.resource_service, "resolve_copilot_path", resolve_then_change
        )
        await runner._execute_pipeline(pipeline, [task])

        assert core.snapshot_existed_at_append is True
        assert core.snapshot_content == _copilot()
        assert core.snapshot_path is not None
        assert not core.snapshot_path.exists()
        if source_change == "remove":
            assert not source.exists()
        else:
            assert json.loads(source.read_text(encoding="utf-8"))["stage_name"] == (
                "changed-after-resolution"
            )
        async with factory() as session:
            stored_pipeline = await session.get(Pipeline, pipeline.id)
            assert stored_pipeline.status == PipelineStatus.COMPLETED
        await runner.stop()
        asst.stop()

    asyncio.run(scenario())


def test_runner_release_failure_does_not_rewrite_terminal_result(
    runner_env, monkeypatch, caplog
) -> None:
    factory, resource_root, make = runner_env

    async def scenario() -> None:
        pipeline, task = await _seed_copilot(factory, resource_root)
        runner, asst, core = make()
        calls = []

        async def fail_release(path: Path) -> None:
            calls.append(path)
            raise OSError("simulated cleanup failure")

        monkeypatch.setattr(
            runner.resource_service,
            "release_copilot_path",
            fail_release,
            raising=False,
        )
        await runner._execute_pipeline(pipeline, [task])

        assert len(calls) == 1
        assert core.snapshot_path == calls[0]
        assert core.snapshot_path.exists()
        async with factory() as session:
            stored_pipeline = await session.get(Pipeline, pipeline.id)
            assert stored_pipeline.status == PipelineStatus.COMPLETED
        assert "清理 Copilot snapshot 失败" in caplog.text
        core.snapshot_path.unlink(missing_ok=True)
        core.snapshot_path.parent.rmdir()
        await runner.stop()
        asst.stop()

    asyncio.run(scenario())


@pytest.mark.parametrize("terminal", ["shutdown", "crash"])
def test_runner_releases_snapshot_on_shutdown_and_core_crash(runner_env, terminal) -> None:
    factory, resource_root, make = runner_env

    async def scenario() -> None:
        pipeline, task = await _seed_copilot(factory, resource_root)
        runner, asst, core = make(STUCK_SCRIPT)
        job = asyncio.create_task(runner._execute_pipeline(pipeline, [task]))
        await asyncio.wait_for(core.started_event.wait(), timeout=2)
        assert core.snapshot_path is not None
        assert core.snapshot_path.is_file()

        if terminal == "shutdown":
            await runner.stop()
        else:
            runner._apply_core_crash({"reason": "test crash"})
        await asyncio.wait_for(job, timeout=2)

        assert not core.snapshot_path.exists()
        assert not core.snapshot_path.parent.exists()
        async with factory() as session:
            stored_pipeline = await session.get(Pipeline, pipeline.id)
            expected = (
                PipelineStatus.CANCELLED
                if terminal == "shutdown"
                else PipelineStatus.FAILED
            )
            assert stored_pipeline.status == expected
        if terminal == "crash":
            await runner.stop()
        asst.stop()

    asyncio.run(scenario())
