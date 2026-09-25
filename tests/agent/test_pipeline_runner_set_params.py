"""Runtime task parameter updates validate, call MaaCore, then persist the patch."""

from __future__ import annotations

import asyncio
from pathlib import Path

import pytest
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker
from sqlalchemy.pool import NullPool
from sqlmodel import SQLModel

import maa_api.db.models  # noqa: F401
from maa_api.core.registry import CoreRegistry, DEFAULT_CORE_ID
from maa_api.db.models import Pipeline, Task
from maa_api.db.repositories.pipeline import TaskRepository
from maa_api.db.session import make_engine
from maa_api.domain.enums import PipelineSource, PipelineStatus, Priority, TaskStatus
from maa_api.domain.errors import AppError, ErrorCode
from maa_api.services.pipeline_runner import PipelineRunner, _ActivePipeline, _Attempt


async def _create_schema(engine) -> None:
    async with engine.begin() as connection:
        await connection.run_sync(SQLModel.metadata.create_all)


@pytest.fixture
def runner_env(tmp_path: Path):
    engine = make_engine(
        f"sqlite+aiosqlite:///{tmp_path / 'agent-pipeline.db'}", poolclass=NullPool
    )
    asyncio.run(_create_schema(engine))
    factory = async_sessionmaker(
        engine, expire_on_commit=False, class_=AsyncSession
    )

    class Core:
        def __init__(self):
            self.handlers = {}
            self.calls: list[tuple[int, dict]] = []
            self.accept = True

        def on(self, event_type, handler):
            self.handlers[event_type] = handler

        async def set_task_params(self, task_id: int, params: dict) -> bool:
            self.calls.append((task_id, params))
            return self.accept

    core = Core()
    registry = CoreRegistry()
    registry.register(DEFAULT_CORE_ID, core)
    runner = PipelineRunner(registry, SimpleSupervisor(), factory)
    yield runner, factory, core
    engine.sync_engine.dispose()


class SimpleSupervisor:
    state = "ready"
    generation = 1


async def _seed_task(factory, *, status: TaskStatus = TaskStatus.RUNNING):
    async with factory() as session:
        pipeline = Pipeline(
            id="pipeline-live",
            source=PipelineSource.AGENT,
            priority=Priority.AGENT,
            status=PipelineStatus.RUNNING,
            task_count=1,
        )
        task = Task(
            id="task-live",
            pipeline_id=pipeline.id,
            order_index=0,
            type_name="Fight",
            task_name="刷理智",
            params={"stage": "1-7", "times": 5},
            status=status,
            maa_task_id=42,
        )
        session.add(pipeline)
        session.add(task)
        await session.commit()
        return pipeline.id, task.id


def _activate_current_task(runner: PipelineRunner, pipeline_id: str, task_id: str) -> None:
    loop = asyncio.get_running_loop()
    runner._active = _ActivePipeline(pipeline_id=pipeline_id)
    runner._active.attempt = _Attempt(
        task_id=task_id,
        type_name="Fight",
        maa_task_id=42,
        future=loop.create_future(),
    )


def test_runtime_update_uses_task_schema_calls_core_and_persists_accepted_patch(runner_env) -> None:
    runner, factory, core = runner_env

    async def scenario() -> None:
        pipeline_id, task_id = await _seed_task(factory)
        _activate_current_task(runner, pipeline_id, task_id)

        assert callable(getattr(runner, "set_task_params", None)), (
            "PipelineRunner must expose validated runtime parameter updates"
        )
        updated = await runner.set_task_params(task_id, {"times": 7})

        assert updated.params == {"stage": "1-7", "times": 7}
        assert core.calls == [(42, {"times": 7})]
        async with factory() as session:
            stored = await session.get(Task, task_id)
            assert stored.params == {"stage": "1-7", "times": 7}

    asyncio.run(scenario())


def test_runtime_update_rejects_stage_before_calling_core(runner_env) -> None:
    runner, factory, core = runner_env

    async def scenario() -> None:
        pipeline_id, task_id = await _seed_task(factory)
        _activate_current_task(runner, pipeline_id, task_id)

        assert callable(getattr(runner, "set_task_params", None)), (
            "PipelineRunner must expose validated runtime parameter updates"
        )
        with pytest.raises(AppError) as exc_info:
            await runner.set_task_params(task_id, {"stage": "CE-6"})

        assert exc_info.value.code is ErrorCode.TASK_NOT_RUNTIME_MUTABLE
        assert core.calls == []
        async with factory() as session:
            stored = await session.get(Task, task_id)
            assert stored.params == {"stage": "1-7", "times": 5}

    asyncio.run(scenario())


def test_runtime_update_rejects_invalid_task_values_before_core(runner_env) -> None:
    runner, factory, core = runner_env

    async def scenario() -> None:
        pipeline_id, task_id = await _seed_task(factory)
        _activate_current_task(runner, pipeline_id, task_id)

        assert callable(getattr(runner, "set_task_params", None)), (
            "PipelineRunner must expose validated runtime parameter updates"
        )
        with pytest.raises(AppError) as exc_info:
            await runner.set_task_params(task_id, {"series": 10})

        assert exc_info.value.code is ErrorCode.TASK_PARAM_INVALID
        assert core.calls == []

    asyncio.run(scenario())


def test_runtime_update_does_not_persist_when_core_rejects_the_patch(runner_env) -> None:
    runner, factory, core = runner_env
    core.accept = False

    async def scenario() -> None:
        pipeline_id, task_id = await _seed_task(factory)
        _activate_current_task(runner, pipeline_id, task_id)

        assert callable(getattr(runner, "set_task_params", None)), (
            "PipelineRunner must expose validated runtime parameter updates"
        )
        with pytest.raises(AppError) as exc_info:
            await runner.set_task_params(task_id, {"times": 7})

        assert exc_info.value.code is ErrorCode.CORE_COMMAND_FAILED
        async with factory() as session:
            stored = await session.get(Task, task_id)
            assert stored.params == {"stage": "1-7", "times": 5}

    asyncio.run(scenario())


def test_runtime_update_normalizes_field_aliases_without_preserving_stale_value(runner_env) -> None:
    runner, factory, core = runner_env

    async def scenario() -> None:
        pipeline_id, task_id = await _seed_task(factory)
        async with factory() as session:
            task = await session.get(Task, task_id)
            task.params = {"stage": "1-7", "DrGrandet": False}
            await session.commit()
        _activate_current_task(runner, pipeline_id, task_id)

        assert callable(getattr(runner, "set_task_params", None)), (
            "PipelineRunner must expose validated runtime parameter updates"
        )
        updated = await runner.set_task_params(task_id, {"dr_grandet": True})

        assert core.calls == [(42, {"DrGrandet": True})]
        assert updated.params == {"stage": "1-7", "DrGrandet": True}

    asyncio.run(scenario())


def test_runtime_update_rejects_null_patch_values_before_core(runner_env) -> None:
    runner, factory, core = runner_env

    async def scenario() -> None:
        pipeline_id, task_id = await _seed_task(factory)
        _activate_current_task(runner, pipeline_id, task_id)

        assert callable(getattr(runner, "set_task_params", None)), (
            "PipelineRunner must expose validated runtime parameter updates"
        )
        with pytest.raises(AppError) as exc_info:
            await runner.set_task_params(task_id, {"times": None})

        assert exc_info.value.code is ErrorCode.TASK_PARAM_INVALID
        assert core.calls == []
        async with factory() as session:
            stored = await session.get(Task, task_id)
            assert stored.params == {"stage": "1-7", "times": 5}

    asyncio.run(scenario())


def test_task_repository_does_not_change_completed_task_params(runner_env) -> None:
    _runner, factory, _core = runner_env

    async def scenario() -> None:
        _pipeline_id, task_id = await _seed_task(factory, status=TaskStatus.COMPLETED)
        async with factory() as session:
            changed = await TaskRepository(session).update_params(
                task_id, params={"stage": "CE-6"}
            )
            await session.commit()
            stored = await session.get(Task, task_id)

        assert changed is False
        assert stored.params == {"stage": "1-7", "times": 5}

    asyncio.run(scenario())
