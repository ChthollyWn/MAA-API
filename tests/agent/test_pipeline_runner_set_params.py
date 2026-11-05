"""Runtime task parameter updates validate, call MaaCore, then persist the patch."""

from __future__ import annotations

import asyncio
from datetime import datetime
from pathlib import Path

import pytest
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker
from sqlalchemy.pool import NullPool
from sqlmodel import SQLModel

import maa_api.db.models  # noqa: F401
from maa_api.agent.spend import SpendGuard
from maa_api.core.registry import CoreRegistry, DEFAULT_CORE_ID
from maa_api.db.models import Pipeline, SpendLedger, Task
from maa_api.db.repositories.pipeline import TaskRepository
from maa_api.db.session import make_engine
from maa_api.domain.enums import PipelineSource, PipelineStatus, Priority, TaskStatus
from maa_api.domain.errors import AppError, ErrorCode
from maa_api.services.pipeline_runner import PipelineRunner, _ActivePipeline, _Attempt
from maa_api.settings import Settings, SpendSettings


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


def test_runtime_roguelike_patch_validates_cross_fields_against_persisted_state(runner_env) -> None:
    runner, factory, core = runner_env

    async def scenario() -> None:
        async with factory() as session:
            pipeline = Pipeline(
                id="pipeline-roguelike",
                source=PipelineSource.AGENT,
                priority=Priority.AGENT,
                status=PipelineStatus.RUNNING,
                task_count=1,
            )
            task = Task(
                id="task-roguelike",
                pipeline_id=pipeline.id,
                order_index=0,
                type_name="Roguelike",
                task_name="自动肉鸽",
                params={"theme": "Sami", "mode": 1},
                status=TaskStatus.RUNNING,
                maa_task_id=42,
            )
            session.add(pipeline)
            session.add(task)
            await session.commit()

        loop = asyncio.get_running_loop()
        runner._active = _ActivePipeline(pipeline_id="pipeline-roguelike")
        runner._active.attempt = _Attempt(
            task_id="task-roguelike",
            type_name="Roguelike",
            maa_task_id=42,
            future=loop.create_future(),
        )

        updated = await runner.set_task_params("task-roguelike", {"mode": 5})

        assert core.calls == [(42, {"mode": 5})]
        assert updated.params == {"theme": "Sami", "mode": 5}
        async with factory() as session:
            stored = await session.get(Task, "task-roguelike")
            assert stored.params == {"theme": "Sami", "mode": 5}

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


# ---------------------------------------------------------------------------
# M84 §4.5.6：运行中改参的消耗预留
# ---------------------------------------------------------------------------
#: 北京 2026-11-05 20:00（naive UTC），消耗日 2026-11-05。
NOW = datetime(2026, 11, 5, 12, 0)


class _SpendLimits:
    def __init__(self, *, medicine: int = -1) -> None:
        self.medicine = medicine

    def __call__(self) -> Settings:
        return Settings(
            spend=SpendSettings(daily_medicine_limit=self.medicine)
        )


def _bind_spend_guard(
    runner: PipelineRunner,
    factory: async_sessionmaker[AsyncSession],
    *,
    medicine: int,
) -> SpendGuard:
    guard = SpendGuard(
        factory,
        settings_provider=_SpendLimits(medicine=medicine),
        clock=lambda: NOW,
    )
    runner.bind_spend_guard(guard)
    return guard


async def _seed_medicine_ledger(
    factory: async_sessionmaker[AsyncSession],
    *,
    pipeline_id: str,
    task_id: str | None,
    planned: int,
    source: PipelineSource = PipelineSource.AGENT,
) -> None:
    async with factory() as session:
        session.add(
            SpendLedger(
                pipeline_id=pipeline_id,
                task_id=task_id,
                kind="medicine",
                planned=planned,
                source=source.value,
                created_at=NOW,
            )
        )
        await session.commit()


async def _medicine_ledger_rows(
    factory: async_sessionmaker[AsyncSession],
) -> list[SpendLedger]:
    async with factory() as session:
        return list(
            (
                await session.execute(
                    select(SpendLedger).order_by(SpendLedger.id.asc())
                )
            )
            .scalars()
            .all()
        )


def test_agent_patch_over_cap_is_rejected_before_core(runner_env) -> None:
    runner, factory, core = runner_env

    async def scenario() -> None:
        pipeline_id, task_id = await _seed_task(factory)
        _activate_current_task(runner, pipeline_id, task_id)
        _bind_spend_guard(runner, factory, medicine=5)
        # 本任务原先计划 2 瓶；别处（manual）今天已用 3 瓶。2 → 5 的差额 3，
        # 已计 5 + 3 > 上限 5，拦下。
        await _seed_medicine_ledger(
            factory, pipeline_id=pipeline_id, task_id=task_id, planned=2
        )
        await _seed_medicine_ledger(
            factory,
            pipeline_id=pipeline_id,
            task_id=None,
            planned=3,
            source=PipelineSource.MANUAL,
        )

        with pytest.raises(AppError) as exc_info:
            await runner.set_task_params(
                task_id, {"medicine": 5}, spend_source=PipelineSource.AGENT
            )

        assert exc_info.value.code is ErrorCode.SPEND_CAP_EXCEEDED
        assert exc_info.value.details["requested"] == 3
        assert core.calls == []
        rows = await _medicine_ledger_rows(factory)
        assert [(row.task_id, row.planned) for row in rows] == [
            (task_id, 2),
            (None, 3),
        ]
        async with factory() as session:
            stored = await session.get(Task, task_id)
            assert stored.params == {"stage": "1-7", "times": 5}

    asyncio.run(scenario())


def test_core_rejection_rolls_back_the_reserved_patch(runner_env) -> None:
    """守卫已按差额改账本并 flush；内核拒绝时随会话回滚（spec §4.2）。"""

    runner, factory, core = runner_env
    core.accept = False

    async def scenario() -> None:
        pipeline_id, task_id = await _seed_task(factory)
        _activate_current_task(runner, pipeline_id, task_id)
        _bind_spend_guard(runner, factory, medicine=999)
        await _seed_medicine_ledger(
            factory, pipeline_id=pipeline_id, task_id=task_id, planned=2
        )

        with pytest.raises(AppError) as exc_info:
            await runner.set_task_params(
                task_id, {"medicine": 5}, spend_source=PipelineSource.AGENT
            )

        assert exc_info.value.code is ErrorCode.CORE_COMMAND_FAILED
        assert core.calls == [(42, {"medicine": 5})]
        rows = await _medicine_ledger_rows(factory)
        assert [(row.task_id, row.planned) for row in rows] == [(task_id, 2)]

    asyncio.run(scenario())


def test_manual_patch_over_cap_is_allowed_and_updates_ledger(runner_env) -> None:
    """R1：manual 来源不受限，但预留照常把账本改到新的计划量。"""

    runner, factory, core = runner_env

    async def scenario() -> None:
        pipeline_id, task_id = await _seed_task(factory)
        _activate_current_task(runner, pipeline_id, task_id)
        _bind_spend_guard(runner, factory, medicine=0)
        await _seed_medicine_ledger(
            factory,
            pipeline_id=pipeline_id,
            task_id=task_id,
            planned=2,
            source=PipelineSource.MANUAL,
        )

        updated = await runner.set_task_params(
            task_id, {"medicine": 5}, spend_source=PipelineSource.MANUAL
        )

        assert core.calls == [(42, {"medicine": 5})]
        assert updated.params["medicine"] == 5
        rows = await _medicine_ledger_rows(factory)
        assert [(row.task_id, row.planned, row.source) for row in rows] == [
            (task_id, 5, PipelineSource.MANUAL.value)
        ]

    asyncio.run(scenario())
