"""Agent pipeline tools preserve queue policy and reuse pipeline services."""

from __future__ import annotations

import asyncio
import importlib
import importlib.util
from datetime import UTC, datetime
from types import SimpleNamespace

from maa_api.agent.registry import ToolContext, ToolRegistry, ToolRisk
from maa_api.domain.enums import CallerType, PipelineSource, PipelineStatus, Priority
from maa_api.db.models import Pipeline, Task, utcnow
from maa_api.db.repositories.base import Page
from maa_api.domain.task import PipelineCreate, StartUpInput


def _register_pipeline() -> ToolRegistry:
    module_name = "maa_api.agent.tools.pipeline"
    assert importlib.util.find_spec(module_name) is not None, (
        "pipeline tool module must be implemented"
    )
    module = importlib.import_module(module_name)
    registry = ToolRegistry()
    module.register_tools(registry)
    return registry


def _context(*, state: object | None = None, db_session: object | None = None, session_id=None):
    return ToolContext(
        caller=CallerType.INTERNAL,
        session_id=session_id,
        request_id="pipeline-test",
        request=SimpleNamespace(app=SimpleNamespace(state=state or SimpleNamespace())),
        db_session=db_session,
    )


def _pipeline(**overrides) -> Pipeline:
    values = {
        "id": "pipeline-1",
        "source": PipelineSource.AGENT,
        "priority": Priority.AGENT,
        "status": PipelineStatus.PENDING,
        "task_count": 1,
        "title": "Agent task",
        "created_at": utcnow(),
    }
    values.update(overrides)
    return Pipeline(**values)


def test_pipeline_group_registers_the_documented_status_and_control_tools() -> None:
    registry = _register_pipeline()

    definitions = registry.list("pipeline")
    assert [definition.name for definition in definitions] == [
        "list_task_types",
        "get_pipeline",
        "list_pipelines",
        "get_queue",
        "submit_pipeline",
        "stop_pipeline",
        "set_task_params",
        "cancel_queued",
    ]
    risks = {definition.name: definition.risk for definition in definitions}
    assert risks["submit_pipeline"] is ToolRisk.CONDITIONAL
    assert risks["set_task_params"] is ToolRisk.CONDITIONAL
    assert all(
        risks[name] is ToolRisk.SAFE
        for name in {
            "list_task_types",
            "get_pipeline",
            "list_pipelines",
            "get_queue",
            "stop_pipeline",
            "cancel_queued",
        }
    )


def test_list_task_types_exports_all_nine_real_task_schemas() -> None:
    registry = _register_pipeline()

    result = asyncio.run(
        registry.execute("list_task_types", {}, _context())
    )

    assert [item["name"] for item in result["items"]] == [
        "StartUp",
        "CloseDown",
        "Fight",
        "Recruit",
        "Infrast",
        "Mall",
        "Award",
        "Roguelike",
        "Reclamation",
    ]
    assert result["items"][2]["schema"]["properties"]["stage"]["anyOf"][0]["type"] == "string"
    assert "runtime_immutable" in result["items"][2]


def test_get_pipeline_returns_the_stored_pipeline_and_task_details(monkeypatch) -> None:
    from maa_api.db.repositories.pipeline import PipelineRepository

    row = _pipeline()
    task = Task(
        id="task-1",
        pipeline_id=row.id,
        order_index=0,
        type_name="StartUp",
        task_name="开始唤醒",
        params={"start_game_enabled": True},
    )

    async def get(self, pipeline_id: str, *, with_tasks: bool = False):
        assert pipeline_id == row.id and with_tasks is True
        object.__setattr__(row, "tasks", [task])
        return row

    monkeypatch.setattr(PipelineRepository, "get", get)
    registry = _register_pipeline()

    result = asyncio.run(
        registry.execute(
            "get_pipeline", {"pipeline_id": row.id}, _context(db_session=object())
        )
    )

    assert result["id"] == "pipeline-1"
    assert result["tasks"][0]["id"] == "task-1"
    assert result["progress"] == {"total": 1, "completed": 0, "failed": 0}


def test_list_pipelines_uses_repository_filters_and_returns_page(monkeypatch) -> None:
    from maa_api.db.repositories.pipeline import PipelineRepository

    seen: dict[str, object] = {}

    async def list_rows(self, **kwargs):
        seen.update(kwargs)
        return Page(items=[_pipeline()], total=1, page=2, size=5)

    monkeypatch.setattr(PipelineRepository, "list", list_rows)
    registry = _register_pipeline()

    result = asyncio.run(
        registry.execute(
            "list_pipelines",
            {
                "status": "pending",
                "source": "agent",
                "since": "2026-09-01T00:00:00Z",
                "page": 2,
                "size": 5,
            },
            _context(db_session=object()),
        )
    )

    assert seen["status"] is PipelineStatus.PENDING
    assert seen["source"] is PipelineSource.AGENT
    assert seen["since"] == datetime(2026, 9, 1)
    assert (seen["page"], seen["size"]) == (2, 5)
    assert result["items"][0]["id"] == "pipeline-1"
    assert result["total"] == 1


def test_get_queue_returns_the_queue_services_priority_ordered_snapshot() -> None:
    class Queue:
        async def snapshot(self):
            return {
                "running": {"pipeline_id": "running-1", "priority": 1},
                "pending": [
                    {"pipeline_id": "agent-1", "priority": 1},
                    {"pipeline_id": "scheduled-1", "priority": 2},
                ],
                "counts": {"pending": 2, "running": 1},
                "paused": False,
            }

    registry = _register_pipeline()
    result = asyncio.run(
        registry.execute(
            "get_queue",
            {},
            _context(state=SimpleNamespace(queue_service=Queue())),
        )
    )

    assert [item["priority"] for item in result["pending"]] == [1, 2]
    assert result["counts"] == {"pending": 2, "running": 1}


def test_submit_pipeline_is_admitted_as_agent_work_and_published_once() -> None:
    row = _pipeline()

    class Queue:
        async def submit(self, payload, **kwargs):
            assert isinstance(payload, PipelineCreate)
            assert payload.tasks[0].name == "StartUp"
            assert kwargs == {
                "source": PipelineSource.AGENT,
                "agent_session_id": "session-1",
            }
            return row, False

    class Runner:
        def __init__(self):
            self.published: list[str] = []
            self.queue_changed = 0

        async def publish_pipeline(self, pipeline_id: str):
            self.published.append(pipeline_id)

        async def publish_queue_changed(self):
            self.queue_changed += 1

    runner = Runner()
    registry = _register_pipeline()
    result = asyncio.run(
        registry.execute(
            "submit_pipeline",
            {"tasks": [{"name": "StartUp", "start_game_enabled": True}]},
            _context(
                state=SimpleNamespace(queue_service=Queue(), pipeline_runner=runner),
                session_id="session-1",
            ),
        )
    )

    assert result["pipeline_id"] == "pipeline-1"
    assert result["status"] == "pending"
    assert runner.published == ["pipeline-1"]
    assert runner.queue_changed == 1


def test_stop_pipeline_targets_the_current_running_pipeline(monkeypatch) -> None:
    from maa_api.db.repositories.pipeline import PipelineRepository

    row = _pipeline(status=PipelineStatus.RUNNING)

    async def current(self, core_id: str = "default"):
        assert core_id == "default"
        return row

    monkeypatch.setattr(PipelineRepository, "current", current)

    class Runner:
        def __init__(self):
            self.cancelled: list[str] = []

        async def request_cancel(self, pipeline_id: str):
            self.cancelled.append(pipeline_id)

    runner = Runner()
    registry = _register_pipeline()
    result = asyncio.run(
        registry.execute(
            "stop_pipeline",
            {},
            _context(
                state=SimpleNamespace(pipeline_runner=runner), db_session=object()
            ),
        )
    )

    assert runner.cancelled == ["pipeline-1"]
    assert result == {"pipeline_id": "pipeline-1", "status": "cancellation_requested"}


def test_set_task_params_delegates_to_the_runner_and_returns_updated_params() -> None:
    class Runner:
        async def set_task_params(self, task_id: str, params: dict):
            assert task_id == "task-1"
            assert params == {"times": 7}
            return Task(
                id=task_id,
                pipeline_id="pipeline-1",
                order_index=0,
                type_name="Fight",
                task_name="刷理智",
                params={"stage": "1-7", "times": 7},
                status="running",
                maa_task_id=42,
            )

    registry = _register_pipeline()
    result = asyncio.run(
        registry.execute(
            "set_task_params",
            {"task_id": "task-1", "params": {"times": 7}},
            _context(state=SimpleNamespace(pipeline_runner=Runner())),
        )
    )

    assert result["task_id"] == "task-1"
    assert result["params"] == {"stage": "1-7", "times": 7}


def test_cancel_queued_cancels_pending_work_only() -> None:
    class Runner:
        async def cancel_pending(self, *, source: str | None = None):
            assert source == "agent"
            return 2

    registry = _register_pipeline()
    result = asyncio.run(
        registry.execute(
            "cancel_queued",
            {"source": "agent"},
            _context(state=SimpleNamespace(pipeline_runner=Runner())),
        )
    )

    assert result == {"cancelled": 2, "source": "agent"}
