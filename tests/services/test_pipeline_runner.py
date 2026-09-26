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
from maa_api.core.registry import CoreRegistry, DEFAULT_CORE_ID
from maa_api.db.models import Pipeline, ResourceAsset, Task
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
from maa_api.services import resource_service as resource_service_module
from tests.fakes.fake_asst import FakeAsst, FakeScript, SUCCESS_SCRIPT

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
        asst.set_callback(self._on_callback)

    def on(self, event_type, handler) -> None:
        self.handlers[event_type] = handler

    def _on_callback(self, message: int, details: bytes, _arg) -> None:
        handler = self.handlers.get("CALLBACK")
        if handler is not None:
            payload = {"msg": message, "details": json.loads(details)}
            self.loop.call_soon_threadsafe(handler, payload)

    async def append_task(self, type_name: str, params: dict) -> int:
        task_id = self.asst.append_task(type_name, params)
        if not task_id:
            raise AppError(
                ErrorCode.CORE_COMMAND_FAILED,
                f"MaaCore 拒绝任务 {type_name!r}",
                {"cmd_id": "fake-command", "error": {"code": "CORE_COMMAND_FAILED"}},
            )
        return task_id

    async def start(self) -> bool:
        return self.asst.start()

    async def stop(self) -> bool:
        return self.asst.stop()


class SimpleSupervisor:
    state = "ready"
    generation = 1


async def _create_schema(engine) -> None:
    async with engine.begin() as connection:
        await connection.run_sync(SQLModel.metadata.create_all)


async def _seed_copilot(factory, resource_root: Path, *, deleted: bool = False):
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
        content=asset_content,
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

    def make(script: FakeScript = SUCCESS_SCRIPT):
        asst = FakeAsst(script=script, delay_scale=0)
        core = FakeCoreClient(asst)
        registry = CoreRegistry()
        registry.register(DEFAULT_CORE_ID, core)
        resource_service = ResourceService(factory, resource_root=resource_root)
        runner = PipelineRunner(
            registry,
            SimpleSupervisor(),
            factory,
            resource_service=resource_service,
        )
        runner._running = True
        return runner, asst

    yield factory, resource_root, make
    engine.sync_engine.dispose()


def test_runner_resolves_copilot_path_only_for_core_append(runner_env) -> None:
    factory, resource_root, make = runner_env

    async def scenario() -> None:
        pipeline, task = await _seed_copilot(factory, resource_root)
        runner, asst = make()
        await runner._execute_pipeline(pipeline, [task])

        expected_path = (
            resource_root / "agent-assets" / "copilot" / f"{ASSET_ID}.json"
        ).resolve()
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
        assert (resource_root / "agent-assets/copilot" / f"{ASSET_ID}.json").is_file()
        await runner.stop()
        asst.stop()

    asyncio.run(scenario())


def test_runner_fails_pipeline_when_copilot_asset_was_removed(runner_env) -> None:
    factory, resource_root, make = runner_env

    async def scenario() -> None:
        pipeline, task = await _seed_copilot(
            factory, resource_root, deleted=True
        )
        runner, asst = make()
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


def test_runner_does_not_append_when_inline_copilot_cannot_be_written(
    runner_env, monkeypatch
) -> None:
    factory, resource_root, make = runner_env

    def fail_write(_target, _content):
        raise OSError("simulated disk write failure")

    monkeypatch.setattr(resource_service_module, "_atomic_write_json", fail_write)

    async def scenario() -> None:
        pipeline, task = await _seed_copilot(factory, resource_root)
        runner, asst = make()
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
        runner, asst = make(rejected)
        await runner._execute_pipeline(pipeline, [task])

        assert asst.calls_of("append_task")[0][1][0] == "Copilot"
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
