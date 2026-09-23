"""Device preflight and durable scheduled-pipeline deferral (M6-04)."""

from __future__ import annotations

import asyncio
from datetime import timedelta
from typing import Any

from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine
from sqlalchemy.pool import NullPool
from sqlmodel import SQLModel

import maa_api.db.models  # noqa: F401
from maa_api.core.registry import CoreRegistry
from maa_api.db.models import Pipeline, Task, utcnow
from maa_api.db.repositories.pipeline import PipelineRepository, TaskRepository
from maa_api.domain.enums import (
    PipelineSource,
    PipelineStatus,
    Priority,
    TaskStatus,
)
from maa_api.domain.errors import ErrorCode
from maa_api.services.pipeline_runner import PipelineRunner


class _Client:
    def __init__(self) -> None:
        self.handlers: dict[str, list[Any]] = {}
        self.connected_calls = 0

    def on(self, event_type: str, handler: Any) -> None:
        self.handlers.setdefault(event_type, []).append(handler)

    async def connected(self) -> bool:
        self.connected_calls += 1
        return False


class _Supervisor:
    state = "ready"
    generation = 3


class _UnavailableDevice:
    def __init__(self) -> None:
        self.preflight_calls: list[float] = []

    async def ensure_available(self, timeout: float) -> bool:
        self.preflight_calls.append(timeout)
        return False

    def snapshot(self) -> dict[str, Any]:
        return {"core_id": "default", "state": "unavailable", "address": "offline:5555"}


def _pipeline(source: PipelineSource, *, defer_count: int = 0) -> Pipeline:
    priority = {
        PipelineSource.MANUAL: Priority.MANUAL,
        PipelineSource.AGENT: Priority.AGENT,
        PipelineSource.SCHEDULED: Priority.SCHEDULED,
    }[source]
    return Pipeline(
        source=source,
        priority=priority,
        task_count=1,
        title=f"{source.value} test",
        defer_count=defer_count,
    )


async def _create_schema(engine) -> None:
    async with engine.begin() as connection:
        await connection.run_sync(SQLModel.metadata.create_all)


def _harness(db_path, source: PipelineSource):
    async def setup():
        engine = create_async_engine(
            f"sqlite+aiosqlite:///{db_path}", poolclass=NullPool
        )
        await _create_schema(engine)
        factory = async_sessionmaker(engine, expire_on_commit=False, class_=AsyncSession)
        async with factory() as session:
            pipeline = _pipeline(source)
            task = Task(
                pipeline_id=pipeline.id,
                order_index=0,
                type_name="Award",
                task_name="领取奖励",
                params={},
            )
            await PipelineRepository(session).create(pipeline, [task])
            await session.commit()
            pipeline_id, task_id = pipeline.id, task.id

        client = _Client()
        registry = CoreRegistry()
        registry.register("default", client)
        device = _UnavailableDevice()
        broadcasts: list[tuple[str, dict[str, Any]]] = []
        runner = PipelineRunner(
            registry,
            _Supervisor(),
            factory,
            device_manager=device,
            broadcast=lambda kind, payload: broadcasts.append((kind, payload)),
        )
        runner._running = True
        return engine, factory, runner, client, device, broadcasts, pipeline_id, task_id

    return asyncio.run(setup())


def test_core_ready_claim_gate_does_not_probe_client_connection(tmp_path) -> None:
    async def scenario() -> None:
        engine = create_async_engine(
            f"sqlite+aiosqlite:///{tmp_path / 'gate.db'}", poolclass=NullPool
        )
        factory = async_sessionmaker(engine, expire_on_commit=False, class_=AsyncSession)
        client = _Client()
        registry = CoreRegistry()
        registry.register("default", client)
        runner = PipelineRunner(registry, _Supervisor(), factory)

        assert await runner._core_can_run_work() is True
        assert client.connected_calls == 0
        await engine.dispose()

    asyncio.run(scenario())


def test_offline_device_does_not_run_tasks_and_fails_manual_and_agent(
    tmp_path, caplog
) -> None:
    for source in (PipelineSource.MANUAL, PipelineSource.AGENT):
        db_path = tmp_path / f"{source.value}.db"
        (
            engine,
            factory,
            runner,
            client,
            device,
            broadcasts,
            pipeline_id,
            task_id,
        ) = _harness(db_path, source)

        async def scenario() -> None:
            async with factory() as session:
                repo = PipelineRepository(session)
                pipeline = await repo.claim_next(core_epoch=3)
                assert pipeline is not None and pipeline.id == pipeline_id
                tasks = await TaskRepository(session).list_by_pipeline(pipeline_id)
                await session.commit()
            await runner._execute_pipeline(pipeline, tasks)

            async with factory() as session:
                current = await PipelineRepository(session).get(pipeline_id)
                task = await TaskRepository(session).get(task_id)
                assert current.status == PipelineStatus.FAILED
                assert current.error_code == ErrorCode.DEVICE_NOT_CONNECTED
                assert task.status == TaskStatus.FAILED
                assert task.error_code == ErrorCode.DEVICE_NOT_CONNECTED
                assert task.retry_count == 0

            assert device.preflight_calls == [10.0]
            assert client.connected_calls == 0
            assert not any(kind.startswith("CALL_") for kind, _ in broadcasts)
            assert any(kind == "task_status" for kind, _ in broadcasts)
            assert any(kind == "pipeline_status" for kind, _ in broadcasts)
            assert any(kind == "device_status" for kind, _ in broadcasts)
            assert "设备预检失败" in caplog.text
            assert ErrorCode.DEVICE_NOT_CONNECTED in caplog.text
            await engine.dispose()

        asyncio.run(scenario())


def test_scheduled_device_preflight_defers_six_times_then_fails_seventh(tmp_path) -> None:
    (
        engine,
        factory,
        runner,
        client,
        device,
        broadcasts,
        pipeline_id,
        task_id,
    ) = _harness(tmp_path / "scheduled.db", PipelineSource.SCHEDULED)

    async def scenario() -> None:
        for defer_count in range(6):
            async with factory() as session:
                pipeline = await PipelineRepository(session).claim_next(core_epoch=3)
                assert pipeline is not None and pipeline.id == pipeline_id
                tasks = await TaskRepository(session).list_by_pipeline(pipeline_id)
                await session.commit()
            await runner._execute_pipeline(pipeline, tasks)

            async with factory() as session:
                deferred = await PipelineRepository(session).get(pipeline_id)
                assert deferred.status == PipelineStatus.PENDING
                assert deferred.defer_count == defer_count + 1
                assert deferred.error_code is None
                assert deferred.deferred_until is not None
                assert deferred.deferred_until >= utcnow() + timedelta(minutes=4, seconds=59)
                task = await TaskRepository(session).get(task_id)
                assert task.status == TaskStatus.PENDING
                # Advance the persisted delay in the isolated DB to exercise the
                # next scheduled attempt without sleeping for five minutes.
                deferred.deferred_until = utcnow() - timedelta(seconds=1)
                await session.commit()

        async with factory() as session:
            pipeline = await PipelineRepository(session).claim_next(core_epoch=3)
            assert pipeline is not None and pipeline.defer_count == 6
            tasks = await TaskRepository(session).list_by_pipeline(pipeline_id)
            await session.commit()
        await runner._execute_pipeline(pipeline, tasks)

        async with factory() as session:
            terminal = await PipelineRepository(session).get(pipeline_id)
            task = await TaskRepository(session).get(task_id)
            assert terminal.status == PipelineStatus.FAILED
            assert terminal.defer_count == 6
            assert terminal.error_code == ErrorCode.DEVICE_NOT_CONNECTED
            assert task.status == TaskStatus.FAILED
            assert task.error_code == ErrorCode.DEVICE_NOT_CONNECTED

        assert len(device.preflight_calls) == 7
        assert client.connected_calls == 0
        assert sum(kind == "device_status" for kind, _ in broadcasts) == 1
        await engine.dispose()

    asyncio.run(scenario())
