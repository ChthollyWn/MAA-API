"""Agent pipeline tools preserve queue policy and reuse pipeline services."""

from __future__ import annotations

import asyncio
import importlib
import importlib.util
import time
from datetime import UTC, datetime
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import pytest
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker
from sqlalchemy.pool import NullPool
from sqlmodel import SQLModel

from maa_api.agent.confirmation import ConfirmationService
from maa_api.agent.policy import PolicyEngine
from maa_api.agent.preconditions import WAIT_TIMEOUT_HINT
from maa_api.agent.registry import ToolContext, ToolRegistry, ToolRisk
from maa_api.core.registry import DEFAULT_CORE_ID, CoreRegistry
from maa_api.db.repositories.pipeline import PipelineRepository, TaskRepository
from maa_api.db.session import make_engine
from maa_api.domain.enums import (
    CallerType,
    PipelineSource,
    PipelineStatus,
    Priority,
    TaskStatus,
)
from maa_api.db.models import Pipeline, Task, utcnow
from maa_api.db.repositories.base import Page
from maa_api.domain.errors import AppError, ErrorCode
from maa_api.domain.task import PipelineCreate, StartUpInput
from maa_api.services.pipeline_runner import PipelineRunner


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
        "wait_pipeline",
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
            "wait_pipeline",
            "list_pipelines",
            "get_queue",
            "stop_pipeline",
            "cancel_queued",
        }
    )


def test_list_task_types_defaults_to_a_compact_catalog_for_the_agent() -> None:
    """M57：默认 compact，避免一次调用把 34K 字 schema 塞进每一轮上下文。"""
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
        "Depot",
        "Custom",
        "Copilot",
    ]
    assert result["detail"] == "compact"
    assert "stage" in result["items"][2]["param_names"]
    assert "schema" not in result["items"][2]
    assert "runtime_immutable" in result["items"][2]

    fig = asyncio.run(
        registry.execute(
            "list_task_types", {"name": "Fight", "detail": "full"}, _context()
        )
    )
    assert [item["name"] for item in fig["items"]] == ["Fight"]
    assert (
        fig["items"][0]["schema"]["properties"]["stage"]["anyOf"][0]["type"]
        == "string"
    )

    rest_context = ToolContext(
        caller=CallerType.REST,
        session_id=None,
        request_id="pipeline-rest",
        request=SimpleNamespace(app=SimpleNamespace(state=SimpleNamespace())),
        db_session=None,
    )
    rest = asyncio.run(registry.execute("list_task_types", {}, rest_context))
    assert rest["detail"] == "full"
    assert "schema" in rest["items"][0]


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


# ---------------------------------------------------------------------------
# wait_pipeline（规格 §4.2）
# ---------------------------------------------------------------------------


async def _invoke_wait(
    runner: object, arguments: dict[str, Any], *, db_session: object | None = None
) -> dict[str, Any]:
    return await _register_pipeline().execute(
        "wait_pipeline",
        arguments,
        _context(
            state=SimpleNamespace(pipeline_runner=runner), db_session=db_session
        ),
    )


class _ExplodingSession:
    """属性一碰就炸的会话替身：用来证明 handler 没有用调用方会话等待。"""

    def __getattr__(self, name: str) -> Any:
        raise AssertionError(f"wait_pipeline 不应访问 ctx.db_session（.{name}）")


class _WaitCore:
    """最小 CoreClient 替身：PipelineRunner 构造时只订阅事件。"""

    def __init__(self) -> None:
        self.handlers: dict[str, Any] = {}

    def on(self, event_type: str, handler: Any) -> None:
        self.handlers[event_type] = handler


class _WaitSupervisor:
    state = "ready"
    generation = 1


async def _create_wait_schema(engine: Any) -> None:
    async with engine.begin() as connection:
        await connection.run_sync(SQLModel.metadata.create_all)


@pytest.fixture
def wait_runner(tmp_path: Path):
    """真实 PipelineRunner + SQLite：等待语义必须落在短会话轮询上。"""

    engine = make_engine(
        f"sqlite+aiosqlite:///{tmp_path / 'wait-pipeline.db'}", poolclass=NullPool
    )
    asyncio.run(_create_wait_schema(engine))
    factory = async_sessionmaker(engine, expire_on_commit=False, class_=AsyncSession)
    registry = CoreRegistry()
    registry.register(DEFAULT_CORE_ID, _WaitCore())
    yield PipelineRunner(registry, _WaitSupervisor(), factory), factory
    engine.sync_engine.dispose()


async def _seed_wait_pipeline(
    factory: async_sessionmaker[AsyncSession],
    *,
    status: PipelineStatus,
    task_status: TaskStatus,
) -> None:
    async with factory() as session:
        session.add(
            Pipeline(
                id="pipeline-wait",
                source=PipelineSource.AGENT,
                priority=Priority.AGENT,
                status=status,
                task_count=1,
                title="等待测试",
            )
        )
        session.add(
            Task(
                id="task-wait",
                pipeline_id="pipeline-wait",
                order_index=0,
                type_name="StartUp",
                task_name="开始唤醒",
                params={"start_game_enabled": True},
                status=task_status,
            )
        )
        await session.commit()


def test_wait_pipeline_is_a_bounded_safe_read_only_tool() -> None:
    registry = _register_pipeline()
    definition = registry.get("wait_pipeline")

    assert definition.group == "pipeline"
    assert definition.risk is ToolRisk.SAFE
    assert definition.read_only is True
    # M58：默认等待从 300 秒降到 120 秒，超时结果带「先看画面」的固定 hint。
    assert definition.description == (
        "等一条流水线结束（完成、失败或取消）或超时再拿结果。"
        "提交流水线后需要等结果时用，不要反复调用 get_pipeline。"
        "等待期间不会产生新的输出，预计要等超过一分钟时先在回复里告诉用户在等什么。"
        "超时返回 finished=false 与 hint：先看现场截图确认画面，再决定续等、停车或换方案。"
        "失败时先看现场截图判断是不是游戏没启动：画面是设备桌面或登录页就先补 StartUp。"
    )

    assert registry.validate("wait_pipeline", {"pipeline_id": "pipeline-1"}).timeout_seconds == 120
    for timeout in (4, 601):
        with pytest.raises(AppError) as excinfo:
            registry.validate(
                "wait_pipeline",
                {"pipeline_id": "pipeline-1", "timeout_seconds": timeout},
            )
        assert excinfo.value.code is ErrorCode.TOOL_ARGS_INVALID
    with pytest.raises(AppError) as excinfo:
        registry.validate("wait_pipeline", {"pipeline_id": "x" * 37})
    assert excinfo.value.code is ErrorCode.TOOL_ARGS_INVALID


def test_wait_pipeline_returns_the_terminal_pipeline_with_its_tasks() -> None:
    row = _pipeline(status=PipelineStatus.COMPLETED)
    task = Task(
        id="task-1",
        pipeline_id=row.id,
        order_index=0,
        type_name="StartUp",
        task_name="开始唤醒",
        status="completed",
    )
    object.__setattr__(row, "tasks", [task])
    seen: dict[str, Any] = {}

    class Runner:
        async def wait_terminal(self, pipeline_id: str, timeout_seconds: float, **_kwargs):
            seen["pipeline_id"] = pipeline_id
            seen["timeout_seconds"] = timeout_seconds
            return row, True

    result = asyncio.run(
        _invoke_wait(
            Runner(), {"pipeline_id": "pipeline-1", "timeout_seconds": 30}
        )
    )

    assert seen == {"pipeline_id": "pipeline-1", "timeout_seconds": 30}
    assert result["finished"] is True
    assert result["waited_seconds"] >= 0
    assert result["pipeline"]["id"] == "pipeline-1"
    assert result["pipeline"]["status"] == "completed"
    assert [item["id"] for item in result["pipeline"]["tasks"]] == ["task-1"]


def test_wait_pipeline_timeout_is_a_normal_result_not_an_error() -> None:
    row = _pipeline(status=PipelineStatus.RUNNING)
    object.__setattr__(row, "tasks", [])

    class Runner:
        async def wait_terminal(self, pipeline_id: str, timeout_seconds: float, **_kwargs):
            return row, False

    result = asyncio.run(
        _invoke_wait(Runner(), {"pipeline_id": "pipeline-1", "timeout_seconds": 5})
    )

    assert result["finished"] is False
    assert result["pipeline"]["status"] == "running"
    assert "error" not in result
    # M58：超时不再只是「安静地返回运行中快照」，而是带一条先看画面的处置提示。
    assert result["hint"] == WAIT_TIMEOUT_HINT


def test_wait_pipeline_never_touches_the_tool_database_session() -> None:
    row = _pipeline(status=PipelineStatus.COMPLETED)
    object.__setattr__(row, "tasks", [])

    class Runner:
        async def wait_terminal(self, pipeline_id: str, timeout_seconds: float, **_kwargs):
            return row, True

    # 拆台替身：handler 若把 ctx.db_session 传给仓储就会在这里炸。
    alive = asyncio.run(
        _invoke_wait(
            Runner(), {"pipeline_id": "pipeline-1"}, db_session=_ExplodingSession()
        )
    )
    none_session = asyncio.run(
        _invoke_wait(Runner(), {"pipeline_id": "pipeline-1"}, db_session=None)
    )

    assert alive["finished"] is True
    assert none_session["finished"] is True


def test_wait_pipeline_returns_immediately_when_the_pipeline_is_already_terminal(
    wait_runner,
) -> None:
    runner, factory = wait_runner

    async def scenario() -> None:
        await _seed_wait_pipeline(
            factory, status=PipelineStatus.COMPLETED, task_status=TaskStatus.COMPLETED
        )
        started = time.monotonic()
        result = await _invoke_wait(
            runner, {"pipeline_id": "pipeline-wait", "timeout_seconds": 300}
        )
        elapsed = time.monotonic() - started

        assert result["finished"] is True
        assert result["pipeline"]["status"] == "completed"
        assert result["pipeline"]["progress"] == {"total": 1, "completed": 1, "failed": 0}
        assert [item["status"] for item in result["pipeline"]["tasks"]] == ["completed"]
        assert elapsed < 0.5
        assert result["waited_seconds"] < 0.5

    asyncio.run(scenario())


def test_wait_terminal_returns_once_the_pipeline_finishes_in_the_background(
    wait_runner,
) -> None:
    runner, factory = wait_runner

    async def scenario() -> None:
        await _seed_wait_pipeline(
            factory, status=PipelineStatus.RUNNING, task_status=TaskStatus.RUNNING
        )

        async def finish_later() -> None:
            await asyncio.sleep(0.05)
            async with factory() as session:
                await PipelineRepository(session).mark_terminal(
                    "pipeline-wait",
                    PipelineStatus.FAILED,
                    error_code=ErrorCode.CORE_CRASHED,
                    error_message="内核中断",
                )
                await TaskRepository(session).update_status(
                    "task-wait",
                    TaskStatus.FAILED,
                    error_code=ErrorCode.CORE_CRASHED,
                    error_message="内核中断",
                )
                await session.commit()

        finisher = asyncio.create_task(finish_later())
        try:
            pipeline, finished = await runner.wait_terminal(
                "pipeline-wait", 5.0, poll_seconds=0.02
            )
        finally:
            await finisher

        assert finished is True
        assert PipelineStatus(pipeline.status) is PipelineStatus.FAILED
        assert pipeline.error_code == ErrorCode.CORE_CRASHED
        assert [task.status for task in pipeline.tasks] == ["failed"]

    asyncio.run(scenario())


def test_wait_terminal_returns_the_current_state_on_timeout(wait_runner) -> None:
    runner, factory = wait_runner

    async def scenario() -> None:
        await _seed_wait_pipeline(
            factory, status=PipelineStatus.RUNNING, task_status=TaskStatus.RUNNING
        )
        started = time.monotonic()
        pipeline, finished = await runner.wait_terminal(
            "pipeline-wait", 0.2, poll_seconds=0.05
        )
        elapsed = time.monotonic() - started

        assert finished is False
        assert PipelineStatus(pipeline.status) is PipelineStatus.RUNNING
        assert [task.status for task in pipeline.tasks] == ["running"]
        assert 0.15 <= elapsed < 2.0

    asyncio.run(scenario())


def test_wait_pipeline_rejects_an_unknown_pipeline_id_without_waiting(
    wait_runner,
) -> None:
    runner, _factory = wait_runner

    async def scenario() -> None:
        started = time.monotonic()
        with pytest.raises(AppError) as excinfo:
            await _invoke_wait(runner, {"pipeline_id": "missing-pipeline"})
        elapsed = time.monotonic() - started

        assert excinfo.value.code is ErrorCode.PIPELINE_NOT_FOUND
        assert excinfo.value.details == {"pipeline_id": "missing-pipeline"}
        # 默认 timeout 是 120 秒：未知 id 必须立刻失败，不能等满超时。
        assert elapsed < 0.5

    asyncio.run(scenario())


def test_wait_pipeline_propagates_cancellation_instead_of_swallowing_it(
    wait_runner,
) -> None:
    runner, factory = wait_runner

    async def scenario() -> None:
        await _seed_wait_pipeline(
            factory, status=PipelineStatus.RUNNING, task_status=TaskStatus.RUNNING
        )
        task = asyncio.create_task(
            _invoke_wait(
                runner, {"pipeline_id": "pipeline-wait", "timeout_seconds": 300}
            )
        )
        await asyncio.sleep(0.05)
        task.cancel()
        with pytest.raises(asyncio.CancelledError):
            await task
        assert task.cancelled() is True

    asyncio.run(scenario())


def test_wait_pipeline_is_cancelled_with_its_confirmation_worker(wait_runner) -> None:
    """服务关闭时 handler 随 ConfirmationService 的 worker 一起被取消（规格 §4.2）。"""

    runner, factory = wait_runner

    async def scenario() -> None:
        await _seed_wait_pipeline(
            factory, status=PipelineStatus.RUNNING, task_status=TaskStatus.RUNNING
        )
        service = ConfirmationService(
            factory, _register_pipeline(), policy=PolicyEngine()
        )
        context = ToolContext(
            caller=CallerType.INTERNAL,
            session_id=None,
            request_id="wait-shutdown-test",
            request=SimpleNamespace(
                app=SimpleNamespace(
                    state=SimpleNamespace(pipeline_runner=runner)
                )
            ),
            db_session=None,
        )

        accepted = await service.invoke(
            "wait_pipeline",
            {"pipeline_id": "pipeline-wait", "timeout_seconds": 300},
            context,
            mode="async",
        )
        assert accepted["status"] == "accepted", accepted
        await asyncio.sleep(0.05)
        assert [
            task for task in asyncio.all_tasks() if task.get_name().startswith("agent-tool-")
        ], "等待应当在 ConfirmationService 的 worker 里跑"

        started = time.monotonic()
        await asyncio.wait_for(service.close(), timeout=5.0)
        elapsed = time.monotonic() - started

        # 取消真生效时 close() 立刻返回；被吞掉的话这里会等满 300 秒超时。
        assert elapsed < 1.0
        assert [
            task for task in asyncio.all_tasks() if task.get_name().startswith("agent-tool-")
        ] == []

    asyncio.run(scenario())
