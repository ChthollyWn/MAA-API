"""Agent core restart serializes queue work with update maintenance."""

from __future__ import annotations

import asyncio
import importlib
import importlib.util
from contextlib import asynccontextmanager
from types import SimpleNamespace

import pytest

from maa_api.domain.errors import AppError


def _service_type():
    module_name = "maa_api.services.agent_ops_service"
    assert importlib.util.find_spec(module_name) is not None, (
        "AgentOpsService must be implemented"
    )
    module = importlib.import_module(module_name)
    assert hasattr(module, "AgentOpsService"), "AgentOpsService must be implemented"
    return module.AgentOpsService


def test_restart_pauses_queue_uses_supervisor_maintenance_and_resumes(monkeypatch):
    Service = _service_type()
    from maa_api.db.repositories.pipeline import PipelineRepository

    async def no_running(self, core_id="default"):
        assert core_id == "default"
        return None

    async def no_pending(self, core_id="default"):
        return 0

    monkeypatch.setattr(PipelineRepository, "current", no_running)
    monkeypatch.setattr(PipelineRepository, "count_pending", no_pending)

    async def scenario():
        events = []

        class Updates:
            def __init__(self):
                self._lock = asyncio.Lock()

            async def _running_record(self):
                return None

        class Queue:
            paused = False

            async def pause(self):
                events.append("pause")
                self.paused = True
                return True

            async def resume(self):
                events.append("resume")
                self.paused = False
                return True

        class Runner:
            operation_lock = asyncio.Lock()
            _active = None
            core_id = "default"

        class Session:
            async def __aenter__(self):
                return self

            async def __aexit__(self, *_args):
                return None

        class Supervisor:
            state = SimpleNamespace(value="ready")
            generation = 8

            @asynccontextmanager
            async def acquire_maintenance(self):
                events.append("maintenance-enter")
                try:
                    yield self
                finally:
                    events.append("maintenance-exit")

            async def restart(self):
                events.append("restart")

        service = Service(
            core_supervisor=Supervisor(),
            update_service=Updates(),
            queue_service=Queue(),
            pipeline_runner=Runner(),
            session_factory=Session,
        )
        result = await service.restart_core(caller="agent")
        return service, events, result

    service, events, result = asyncio.run(scenario())
    assert events == ["pause", "maintenance-enter", "restart", "maintenance-exit", "resume"]
    assert result == {"status": "ready", "generation": 8}
    assert not service.update_service._lock.locked()


def test_restart_refuses_running_pipeline_and_restores_queue_state(monkeypatch):
    Service = _service_type()
    from maa_api.db.repositories.pipeline import PipelineRepository

    active = SimpleNamespace(id="pipeline-8")

    async def running(self, core_id="default"):
        return active

    monkeypatch.setattr(PipelineRepository, "current", running)

    async def has_pending(self, core_id="default"):
        return 0

    monkeypatch.setattr(PipelineRepository, "count_pending", has_pending)

    async def scenario():
        events = []

        class Updates:
            def __init__(self):
                self._lock = asyncio.Lock()

            async def _running_record(self):
                return None

        class Queue:
            paused = False

            async def pause(self):
                self.paused = True
                events.append("pause")
                return True

            async def resume(self):
                self.paused = False
                events.append("resume")
                return True

        class Runner:
            operation_lock = asyncio.Lock()
            _active = active
            core_id = "default"

        class Session:
            async def __aenter__(self):
                return self

            async def __aexit__(self, *_args):
                return None

        class Supervisor:
            @asynccontextmanager
            async def acquire_maintenance(self):
                events.append("maintenance")
                yield self

            async def restart(self):
                events.append("restart")

        service = Service(
            core_supervisor=Supervisor(),
            update_service=Updates(),
            queue_service=Queue(),
            pipeline_runner=Runner(),
            session_factory=Session,
            idle_timeout_seconds=0,
        )
        with pytest.raises(AppError) as exc_info:
            await service.restart_core(caller="agent")
        return service, events, exc_info.value

    service, events, error = asyncio.run(scenario())
    assert error.code == "UPDATE_BLOCKED_BY_PIPELINE"
    assert events == []
    assert not service.update_service._lock.locked()


def test_restart_refuses_to_overlap_an_update():
    Service = _service_type()

    async def scenario():
        events = []

        class Updates:
            def __init__(self):
                self._lock = asyncio.Lock()

            async def _running_record(self):
                return None

        updates = Updates()
        await updates._lock.acquire()

        class Queue:
            paused = False

            async def pause(self):
                events.append("pause")
                return True

            async def resume(self):
                events.append("resume")

        class Runner:
            operation_lock = asyncio.Lock()
            _active = None
            core_id = "default"

        class Supervisor:
            @asynccontextmanager
            async def acquire_maintenance(self):
                events.append("maintenance")
                yield self

            async def restart(self):
                events.append("restart")

        service = Service(
            core_supervisor=Supervisor(),
            update_service=updates,
            queue_service=Queue(),
            pipeline_runner=Runner(),
            session_factory=object(),
            idle_timeout_seconds=0,
        )
        with pytest.raises(AppError) as exc_info:
            await service.restart_core(caller="agent")
        return events, exc_info.value

    events, error = asyncio.run(scenario())
    assert error.code == "UPDATE_ALREADY_RUNNING"
    assert events == []


@pytest.mark.parametrize("cancel_after_pause", [False, True])
def test_restart_resumes_owned_pause_when_pause_raises_or_task_is_cancelled(
    monkeypatch, cancel_after_pause
):
    Service = _service_type()
    from maa_api.db.repositories.pipeline import PipelineRepository

    async def no_running(self, core_id="default"):
        return None

    async def no_pending(self, core_id="default"):
        return 0

    monkeypatch.setattr(PipelineRepository, "current", no_running)
    monkeypatch.setattr(PipelineRepository, "count_pending", no_pending)

    async def scenario():
        events = []
        paused = False
        pause_requested = asyncio.Event()
        release_pause = asyncio.Event()

        class Updates:
            def __init__(self):
                self._lock = asyncio.Lock()

            async def _running_record(self):
                return None

        class Queue:
            @property
            def paused(self):
                return paused

            async def pause(self):
                nonlocal paused
                paused = True
                events.append("pause")
                if cancel_after_pause:
                    pause_requested.set()
                    await release_pause.wait()
                raise RuntimeError("pause failed after taking effect")

            async def resume(self):
                nonlocal paused
                events.append("resume")
                paused = False
                return True

        class Runner:
            operation_lock = asyncio.Lock()
            _active = None
            core_id = "default"

        class Session:
            async def __aenter__(self):
                return self

            async def __aexit__(self, *_args):
                return None

        class Supervisor:
            @asynccontextmanager
            async def acquire_maintenance(self):
                events.append("maintenance")
                yield self

            async def restart(self):
                events.append("restart")

        service = Service(
            core_supervisor=Supervisor(),
            update_service=Updates(),
            queue_service=Queue(),
            pipeline_runner=Runner(),
            session_factory=Session,
        )
        task = asyncio.create_task(service.restart_core())
        if cancel_after_pause:
            await pause_requested.wait()
            task.cancel()
            release_pause.set()
        result = await asyncio.gather(task, return_exceptions=True)
        return result[0], events, paused

    outcome, events, paused = asyncio.run(scenario())
    if cancel_after_pause:
        assert isinstance(outcome, asyncio.CancelledError)
    else:
        assert isinstance(outcome, RuntimeError)
    assert events == ["pause", "resume"]
    assert paused is False
