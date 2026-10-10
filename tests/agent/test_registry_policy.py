"""Task registry validation and Agent tool authorization policy contracts."""

from __future__ import annotations

import asyncio
import importlib
from datetime import timedelta
from types import SimpleNamespace
from typing import Any

import pytest
from pydantic import BaseModel, ConfigDict, Field
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker
from sqlalchemy.pool import NullPool
from sqlmodel import SQLModel

from maa_api.agent.policy import PolicyEngine, ToolRisk
from maa_api.agent.registry import ToolContext, ToolDefinition, ToolRegistry
from maa_api.db.models import AgentSession, Confirmation, Pipeline, Task, utcnow
from maa_api.db.repositories.agent import AgentSessionRepository
from maa_api.db.session import make_engine
from maa_api.domain.enums import (
    AgentSessionStatus,
    CallerType,
    ConfirmationStatus,
    PipelineSource,
    PipelineStatus,
    Priority,
    RiskLevel,
)
from maa_api.domain.errors import AppError, ErrorCode
from maa_api.domain.task import PipelineCreate
from maa_api.services.schedule_service import ScheduleWrite


class ExampleParams(BaseModel):
    model_config = ConfigDict(extra="forbid")

    count: int = Field(gt=0, description="执行次数")


async def _handler(params: ExampleParams, context: ToolContext) -> dict[str, Any]:
    return {"count": params.count, "request_id": context.request_id}


def _definition(
    name: str,
    risk: ToolRisk = ToolRisk.DANGEROUS,
    *,
    group: str = "raw",
) -> ToolDefinition:
    return ToolDefinition(
        name=name,
        group=group,
        risk=risk,
        description=f"{name} action",
        params_model=ExampleParams,
        handler=_handler,
    )


def _context(
    caller: CallerType = CallerType.INTERNAL,
    *,
    session_id: str | None = None,
    db_session: Any = None,
) -> ToolContext:
    return ToolContext(
        caller=caller,
        session_id=session_id,
        request_id="request-1",
        request=None,
        db_session=db_session,
    )


def test_registry_exports_pydantic_schema_and_metadata() -> None:
    registry = ToolRegistry()
    definition = _definition("count_runs")
    registry.register(definition)

    exported = registry.export_schema()

    assert exported == [
        {
            "name": "count_runs",
            "group": "raw",
            "risk": "DANGEROUS",
            "description": "count_runs action",
            "inputSchema": {
                "additionalProperties": False,
                "properties": {
                    "count": {
                        "description": "执行次数",
                        "exclusiveMinimum": 0,
                        "title": "Count",
                        "type": "integer",
                    }
                },
                "required": ["count"],
                "title": "ExampleParams",
                "type": "object",
            },
        }
    ]
    assert registry.list() == [definition]
    assert registry.get("count_runs") is definition


def test_registry_starts_empty_without_implicit_unimplemented_tools() -> None:
    registry = ToolRegistry()

    assert registry.list() == []
    assert registry.export_schema() == []


def test_registry_rejects_duplicate_names_and_unknown_tools() -> None:
    registry = ToolRegistry()
    registry.register(_definition("count_runs"))

    with pytest.raises(ValueError, match="already registered"):
        registry.register(_definition("count_runs"))
    with pytest.raises(AppError) as exc_info:
        registry.get("missing")
    assert exc_info.value.code == ErrorCode.TOOL_NOT_FOUND


def test_registry_validates_arguments_and_dispatches_model_to_handler() -> None:
    registry = ToolRegistry()
    registry.register(_definition("count_runs"))
    context = _context()

    valid = registry.validate("count_runs", {"count": 3})
    assert valid == ExampleParams(count=3)
    assert asyncio.run(registry.execute("count_runs", {"count": 3}, context)) == {
        "count": 3,
        "request_id": "request-1",
    }

    with pytest.raises(AppError) as exc_info:
        registry.validate("count_runs", {"count": 0, "unexpected": True})
    assert exc_info.value.code == ErrorCode.TOOL_ARGS_INVALID


def _evaluate(
    definition: ToolDefinition,
    arguments: dict[str, Any],
    context: ToolContext,
):
    return asyncio.run(PolicyEngine().evaluate(definition, arguments, context))


@pytest.mark.parametrize("name", ["trigger_screencap", "back_to_home", "stop_pipeline"])
def test_safe_allowlist_never_requires_confirmation(name: str) -> None:
    decision = _evaluate(_definition(name), {}, _context(CallerType.REST))

    assert decision.requires_confirmation is False
    assert decision.risk == RiskLevel.NONE


def test_ordinary_dangerous_tool_requires_confirmation() -> None:
    decision = _evaluate(_definition("update_core"), {}, _context())

    assert decision.requires_confirmation is True
    assert decision.confirmation_action == "update_core"
    assert decision.risk == RiskLevel.DESTRUCTIVE


def test_copilot_upload_is_exempt_from_confirmation() -> None:
    upload = _evaluate(_definition("upload_copilot", ToolRisk.SAFE), {}, _context())

    assert upload.requires_confirmation is False
    assert upload.risk == RiskLevel.NONE


@pytest.mark.parametrize(
    ("task", "requires_confirmation"),
    [
        ({"name": "Fight", "times": 5}, False),
        ({"name": "Fight", "stone": 2}, True),
        ({"name": "Fight", "medicine": 1}, True),
        ({"name": "Fight", "expiring_medicine": 1}, True),
        ({"name": "Recruit", "expedite": True}, True),
        ({"name": "Mall", "shopping": True}, True),
        ({"name": "Roguelike", "investment_enabled": True}, True),
        ({"name": "Fight", "stone": 0, "medicine": 0}, False),
    ],
)
def test_submit_pipeline_checks_consumption_parameters(task, requires_confirmation) -> None:
    decision = _evaluate(
        _definition("submit_pipeline", ToolRisk.CONDITIONAL, group="pipeline"),
        {"tasks": [task]},
        _context(CallerType.REST),
    )

    assert decision.requires_confirmation is requires_confirmation
    if requires_confirmation:
        assert decision.risk == RiskLevel.CONSUME
        assert decision.reasons


def test_consumption_reasons_include_each_triggered_risk_field() -> None:
    decision = _evaluate(
        _definition("submit_pipeline", ToolRisk.CONDITIONAL, group="pipeline"),
        {"tasks": [{"name": "Fight", "stone": 2, "medicine": 1}]},
        _context(CallerType.REST),
    )

    assert decision.reasons == ("Fight.stone=2", "Fight.medicine=1")


def test_set_task_params_checks_nested_task_parameters() -> None:
    decision = _evaluate(
        _definition("set_task_params", ToolRisk.CONDITIONAL, group="pipeline"),
        {
            "task": {
                "name": "Recruit",
                "params": {"name": "Recruit", "expedite": True},
            }
        },
        _context(CallerType.REST),
    )

    assert decision.requires_confirmation is True
    assert decision.risk == RiskLevel.CONSUME
    assert decision.reasons == ("Recruit.expedite=True",)


def test_set_task_params_checks_risk_fields_without_task_type_name() -> None:
    decision = _evaluate(
        _definition("set_task_params", ToolRisk.CONDITIONAL, group="pipeline"),
        {"task_id": 7, "params": {"stone": 2}},
        _context(CallerType.REST),
    )

    assert decision.requires_confirmation is True
    assert decision.risk == RiskLevel.CONSUME


@pytest.mark.parametrize(
    ("name", "arguments", "requires_confirmation"),
    [
        ("create_schedule", {"template": [{"name": "Fight", "times": 5}]}, False),
        ("create_schedule", {"template": [{"name": "Fight", "stone": 1}]}, True),
        ("update_schedule", {"template": [{"name": "Mall", "shopping": True}]}, True),
        ("update_schedule", {"enabled": False}, False),
        ("delete_schedule", {"schedule_id": "schedule-1"}, False),
    ],
)
def test_schedule_confirmation_depends_on_template_consumption(
    name, arguments, requires_confirmation
) -> None:
    decision = _evaluate(
        _definition(name, ToolRisk.CONDITIONAL, group="schedule"),
        arguments,
        _context(CallerType.REST),
    )

    assert decision.requires_confirmation is requires_confirmation


def test_schedule_write_model_consumption_detection_reads_task_input_models() -> None:
    schedule = ScheduleWrite.model_validate(
        {
            "name": "daily",
            "cron": "0 4 * * *",
            "template": [{"name": "Fight", "stone": 1}],
        }
    )
    decision = _evaluate(
        _definition("create_schedule", ToolRisk.CONDITIONAL, group="schedule"),
        schedule,
        _context(CallerType.REST),
    )

    assert decision.requires_confirmation is True
    assert decision.reasons == ("Fight.stone=1",)


def test_rest_atomic_calls_always_require_per_call_confirmation() -> None:
    decision = _evaluate(_definition("click"), {"x": 10}, _context(CallerType.REST))

    assert decision.requires_confirmation is True
    assert decision.confirmation_action == "click"


def test_internal_atomic_call_without_a_session_fails_closed() -> None:
    with pytest.raises(AppError) as exc_info:
        _evaluate(_definition("click"), {"x": 10}, _context())

    assert exc_info.value.code == ErrorCode.AGENT_SESSION_NOT_FOUND


def test_internal_atomic_call_without_database_context_fails_closed() -> None:
    context = _context(CallerType.INTERNAL, session_id="existing-session")

    with pytest.raises(AppError) as exc_info:
        _evaluate(_definition("click"), {"x": 10}, context)

    assert exc_info.value.code == ErrorCode.AGENT_SESSION_NOT_FOUND


def test_unknown_caller_cannot_fall_through_to_per_call_confirmation() -> None:
    context = _context()
    invalid_context = ToolContext(
        caller="unknown",  # type: ignore[arg-type]
        session_id=None,
        request_id=context.request_id,
        request=context.request,
        db_session=context.db_session,
    )

    with pytest.raises(AppError) as exc_info:
        _evaluate(_definition("click"), {"x": 10}, invalid_context)

    assert exc_info.value.code == ErrorCode.FORBIDDEN


def test_internal_atomic_authorization_uses_default_and_configurable_window(tmp_path) -> None:
    engine = make_engine(
        f"sqlite+aiosqlite:///{tmp_path / 'agent-policy-window.db'}",
        poolclass=NullPool,
    )

    async def scenario():
        async with engine.begin() as connection:
            await connection.run_sync(SQLModel.metadata.create_all)
        factory = async_sessionmaker(engine, expire_on_commit=False, class_=AsyncSession)
        async with factory() as session:
            agent_session = await AgentSessionRepository(session).create(
                AgentSession(id="session-window", model="model-test")
            )
            await session.commit()
            arguments = {"x": 10}
            default = await PolicyEngine().evaluate(
                _definition("click"),
                arguments,
                _context(CallerType.INTERNAL, session_id=agent_session.id, db_session=session),
            )
            configured = await PolicyEngine(atomic_grant_minutes=60).evaluate(
                _definition("click"),
                arguments,
                _context(CallerType.INTERNAL, session_id=agent_session.id, db_session=session),
            )
        assert default.confirmation_action == "grant_atomic_ops"
        assert default.window_seconds == 900
        assert configured.window_seconds == 3600

    try:
        asyncio.run(scenario())
    finally:
        engine.sync_engine.dispose()



@pytest.mark.parametrize("minutes", [0, 61, True, 1.5])
def test_internal_atomic_grant_window_rejects_invalid_configuration(minutes) -> None:
    with pytest.raises((TypeError, ValueError)):
        PolicyEngine(atomic_grant_minutes=minutes)


def test_internal_atomic_grant_is_validated_from_persisted_session(tmp_path) -> None:
    engine = make_engine(
        f"sqlite+aiosqlite:///{tmp_path / 'agent-policy.db'}", poolclass=NullPool
    )

    async def scenario() -> None:
        async with engine.begin() as connection:
            await connection.run_sync(SQLModel.metadata.create_all)
        db_session_factory = async_sessionmaker(
            engine, expire_on_commit=False, class_=AsyncSession
        )
        now = utcnow()
        grant = Confirmation(
            id="grant-confirmation",
            action="grant_atomic_ops",
            risk_level=RiskLevel.NONE,
            reason="允许本次会话直接操作游戏界面",
            payload={"session_id": "session-valid", "window_seconds": 900},
            status=ConfirmationStatus.APPROVED,
            requested_by=CallerType.INTERNAL,
            expires_at=now + timedelta(minutes=2),
        )
        unapproved_grant = Confirmation(
            id="unapproved-grant",
            action="grant_atomic_ops",
            risk_level=RiskLevel.NONE,
            reason="待批准的授权请求",
            payload={"session_id": "session-pending", "window_seconds": 900},
            status=ConfirmationStatus.PENDING,
            requested_by=CallerType.INTERNAL,
            expires_at=now + timedelta(minutes=2),
        )
        active = AgentSession(
            id="session-valid",
            model="model-test",
            atomic_grant_id=grant.id,
            atomic_grant_expires_at=now + timedelta(minutes=15),
        )
        expired = AgentSession(
            id="session-expired",
            model="model-test",
            atomic_grant_id=grant.id,
            atomic_grant_expires_at=now - timedelta(seconds=1),
        )
        revoked = AgentSession(
            id="session-revoked",
            model="model-test",
            atomic_grant_id=grant.id,
            atomic_grant_expires_at=now + timedelta(minutes=15),
        )
        finished = AgentSession(
            id="session-finished",
            model="model-test",
            status=AgentSessionStatus.FINISHED,
            atomic_grant_id=grant.id,
            atomic_grant_expires_at=now + timedelta(minutes=15),
        )
        pending = AgentSession(
            id="session-pending",
            model="model-test",
            atomic_grant_id=unapproved_grant.id,
            atomic_grant_expires_at=now + timedelta(minutes=15),
        )

        async with db_session_factory() as session:
            session.add(grant)
            session.add(unapproved_grant)
            await session.commit()
            session.add_all([active, expired, revoked, finished, pending])
            await session.commit()
            await AgentSessionRepository(session).clear_grant(revoked.id)
            await session.commit()

        async with db_session_factory() as session:
            policy = PolicyEngine()
            active_decision = await policy.evaluate(
                _definition("swipe"),
                {"x1": 1, "y1": 1, "x2": 4, "y2": 4},
                _context(CallerType.INTERNAL, session_id=active.id, db_session=session),
            )
            expired_decision = await policy.evaluate(
                _definition("swipe"),
                {"x1": 1, "y1": 1, "x2": 4, "y2": 4},
                _context(CallerType.INTERNAL, session_id=expired.id, db_session=session),
            )
            revoked_decision = await policy.evaluate(
                _definition("swipe"),
                {"x1": 1, "y1": 1, "x2": 4, "y2": 4},
                _context(CallerType.INTERNAL, session_id=revoked.id, db_session=session),
            )
            with pytest.raises(AppError) as missing_error:
                await policy.evaluate(
                    _definition("swipe"),
                    {"x1": 1, "y1": 1, "x2": 4, "y2": 4},
                    _context(
                        CallerType.INTERNAL,
                        session_id="session-missing",
                        db_session=session,
                    ),
                )
            with pytest.raises(AppError) as finished_error:
                await policy.evaluate(
                    _definition("swipe"),
                    {"x1": 1, "y1": 1, "x2": 4, "y2": 4},
                    _context(CallerType.INTERNAL, session_id=finished.id, db_session=session),
                )
            pending_decision = await policy.evaluate(
                _definition("swipe"),
                {"x1": 1, "y1": 1, "x2": 4, "y2": 4},
                _context(CallerType.INTERNAL, session_id=pending.id, db_session=session),
            )
            external_rest_decision = await policy.evaluate(
                _definition("swipe"),
                {"x1": 1, "y1": 1, "x2": 4, "y2": 4},
                _context(CallerType.REST, session_id=active.id, db_session=session),
            )
            external_rest_decision = await policy.evaluate(
                _definition("swipe"),
                {"x1": 1, "y1": 1, "x2": 4, "y2": 4},
                _context(CallerType.REST, session_id=active.id, db_session=session),
            )

        assert active_decision.requires_confirmation is False
        assert active_decision.authorized_by == "grant-confirmation"
        assert expired_decision.requires_confirmation is True
        assert expired_decision.confirmation_action == "grant_atomic_ops"
        assert revoked_decision.requires_confirmation is True
        assert revoked_decision.confirmation_action == "grant_atomic_ops"
        assert missing_error.value.code == ErrorCode.AGENT_SESSION_NOT_FOUND
        assert finished_error.value.code == ErrorCode.AGENT_SESSION_NOT_FOUND
        assert pending_decision.requires_confirmation is True
        assert pending_decision.confirmation_action == "grant_atomic_ops"
        assert external_rest_decision.requires_confirmation is True
        assert external_rest_decision.confirmation_action == "swipe"
        assert external_rest_decision.requires_confirmation is True
        assert external_rest_decision.confirmation_action == "swipe"

    try:
        asyncio.run(scenario())
    finally:
        engine.sync_engine.dispose()


def test_tool_definition_defaults_to_model_visible_and_rejects_non_bool_metadata() -> None:
    """`model_visible` 是工具元数据的一部分，类型校验与 `read_only` 同一风格（规格 §4.4）。"""

    definition = _definition("count_runs")

    assert definition.model_visible is True
    with pytest.raises(TypeError, match="model_visible must be a bool"):
        ToolDefinition(
            name="hidden_action",
            group="raw",
            risk=ToolRisk.SAFE,
            description="hidden action",
            params_model=ExampleParams,
            handler=_handler,
            model_visible="no",  # type: ignore[arg-type]
        )


def test_rest_caller_cannot_reuse_internal_session_grants() -> None:
    decision = _evaluate(
        _definition("click"),
        {"x": 10},
        _context(CallerType.REST, session_id="session-valid"),
    )

    assert decision.requires_confirmation is True
    assert decision.confirmation_action == "click"
# ---------------------------------------------------------------------------
# D1：策略按「内核实际收到的值」（含默认值）判断（规格 §5.3）
# ---------------------------------------------------------------------------


def _validated(name: str, arguments: dict[str, Any]) -> BaseModel:
    """走生产路径的同一套校验：``ConfirmationService`` 先把参数 validate 成模型。"""
    module_name = {
        "set_task_params": "maa_api.agent.tools.pipeline",
        "create_schedule": "maa_api.agent.tools.schedule",
        "update_schedule": "maa_api.agent.tools.schedule",
    }[name]
    registry = ToolRegistry()
    importlib.import_module(module_name).register_tools(registry)
    return registry.validate(name, arguments)


def _submit_definition() -> ToolDefinition:
    return _definition("submit_pipeline", ToolRisk.CONDITIONAL, group="pipeline")


def _set_params_definition() -> ToolDefinition:
    return _definition("set_task_params", ToolRisk.CONDITIONAL, group="pipeline")


def _schedule_definition(name: str = "create_schedule") -> ToolDefinition:
    return _definition(name, ToolRisk.CONDITIONAL, group="schedule")


class _RecordingSession:
    """真会话 + 记下查过哪些 task id，避免替身会话与真 ``TaskRepository`` 行为分叉。"""

    def __init__(self, session: Any) -> None:
        self._session = session
        self.lookups: list[str] = []

    def __getattr__(self, name: str) -> Any:
        return getattr(self._session, name)

    async def get(self, entity: Any, ident: Any, **kwargs: Any) -> Any:
        if getattr(entity, "__name__", "") == "Task":
            self.lookups.append(ident)
        return await self._session.get(entity, ident, **kwargs)


class _FailingSession(_RecordingSession):
    """读取现有参数时报错（例如被别处回滚过的会话）。"""

    async def get(self, entity: Any, ident: Any, **kwargs: Any) -> Any:
        raise AppError(ErrorCode.TASK_NOT_FOUND, "任务不存在")


async def _create_schema(engine) -> None:
    async with engine.begin() as connection:
        await connection.run_sync(SQLModel.metadata.create_all)


@pytest.fixture
def policy_db(tmp_path):
    engine = make_engine(
        f"sqlite+aiosqlite:///{tmp_path / 'policy-d1.db'}", poolclass=NullPool
    )
    asyncio.run(_create_schema(engine))
    factory = async_sessionmaker(engine, expire_on_commit=False, class_=AsyncSession)
    yield factory
    engine.sync_engine.dispose()


def _run_with_task(factory, task: Task | None, evaluate):
    """在一条真会话里放好 pipeline/task 行，用 ``_RecordingSession`` 评估并回收结果。"""

    async def scenario():
        async with factory() as session:
            if task is not None:
                session.add(_pipeline_row(task.pipeline_id))
                session.add(task)
                await session.commit()
            context = _context(CallerType.REST, db_session=_RecordingSession(session))
            decision = await evaluate(context)
            return decision, context.db_session.lookups

    return asyncio.run(scenario())


def _pipeline_row(pipeline_id: str) -> Pipeline:
    return Pipeline(
        id=pipeline_id,
        source=PipelineSource.AGENT,
        priority=Priority.AGENT,
        status=PipelineStatus.RUNNING,
        task_count=1,
    )


def _seed_task(**overrides) -> Task:
    values = {
        "id": "task-mall",
        "pipeline_id": "pipeline-1",
        "order_index": 0,
        "type_name": "Mall",
        "task_name": "获取信用及购物",
        "params": {"shopping": True},
        "status": "running",
    }
    values.update(overrides)
    return Task(**values)


@pytest.mark.parametrize(
    ("task", "requires_confirmation"),
    [
        ({"name": "Mall"}, True),
        ({"name": "Mall", "shopping": False}, False),
        ({"name": "Roguelike"}, True),
        ({"name": "Roguelike", "investment_enabled": False}, False),
        ({"name": "Roguelike", "theme": "Sami", "mode": 5}, True),
        ({"name": "Fight", "stage": "1-7", "series": 0}, False),
        ({"name": "Fight", "stone": 0, "medicine": 0, "expiring_medicine": 0}, False),
        ({"name": "Recruit", "times": 4}, False),
        ({"name": "Award"}, False),
    ],
)
def test_submit_pipeline_uses_kernel_defaults_for_omitted_risk_fields(
    task, requires_confirmation
) -> None:
    """省略 `shopping` / `investment_enabled` 时内核收到默认 ``true``，必须确认（D1）。"""
    create = PipelineCreate.model_validate({"tasks": [task]})
    decision = _evaluate(_submit_definition(), create, _context(CallerType.REST))

    assert decision.requires_confirmation is requires_confirmation
    if requires_confirmation:
        assert decision.risk == RiskLevel.CONSUME


def test_submit_pipeline_reports_omitted_default_risk_values_as_reasons() -> None:
    create = PipelineCreate.model_validate(
        {"tasks": [{"name": "Mall"}, {"name": "Roguelike"}]}
    )
    decision = _evaluate(_submit_definition(), create, _context(CallerType.REST))

    assert decision.reasons == (
        "Mall.shopping=True",
        "Roguelike.investment_enabled=True",
    )


def test_submit_pipeline_still_ignores_default_zero_count_fields() -> None:
    """Fight 的三个计数字段默认 0，省略或显式 0 都不该弹确认（规格 §5.3 正反例）。"""
    create = PipelineCreate.model_validate({"tasks": [{"name": "Fight", "stage": "1-7"}]})
    decision = _evaluate(_submit_definition(), create, _context(CallerType.REST))

    assert decision.requires_confirmation is False
    assert decision.reasons == ()


def test_submit_pipeline_keeps_non_risk_defaults_out_of_the_decision() -> None:
    """``PipelineCreate`` 自身的默认值（``notify_on_finish=True``）不是 risk 字段。"""
    create = PipelineCreate.model_validate({"tasks": [{"name": "StartUp"}]})
    decision = _evaluate(_submit_definition(), create, _context(CallerType.REST))

    assert decision.requires_confirmation is False


@pytest.mark.parametrize(
    ("task", "requires_confirmation", "reasons"),
    [
        ({"name": "Mall"}, True, ("Mall.shopping=True",)),
        ({"name": "Mall", "shopping": False}, False, ()),
        ({"name": "Roguelike"}, True, ("Roguelike.investment_enabled=True",)),
        ({"name": "Fight", "stone": 0}, False, ()),
        ({"name": "Fight", "stone": 2}, True, ("Fight.stone=2",)),
    ],
)
def test_create_schedule_uses_kernel_defaults_for_omitted_risk_fields(
    task, requires_confirmation, reasons
) -> None:
    params = _validated(
        "create_schedule",
        {"name": "daily", "cron": "0 4 * * *", "template": [task]},
    )
    decision = _evaluate(_schedule_definition(), params, _context(CallerType.REST))

    assert decision.requires_confirmation is requires_confirmation
    assert decision.reasons == reasons


def test_update_schedule_uses_kernel_defaults_for_omitted_risk_fields() -> None:
    params = _validated(
        "update_schedule",
        {
            "schedule_id": "schedule-1",
            "name": "daily",
            "cron": "0 4 * * *",
            "template": [{"name": "Roguelike", "theme": "Sami"}],
        },
    )
    decision = _evaluate(
        _schedule_definition("update_schedule"), params, _context(CallerType.REST)
    )

    assert decision.requires_confirmation is True
    assert decision.risk == RiskLevel.CONSUME
    assert decision.reasons == ("Roguelike.investment_enabled=True",)


def test_set_task_params_without_database_context_fails_closed() -> None:
    """拿不到现有参数时按含默认值的完整参数判断，宁可多确认（fail closed）。"""
    params = _validated("set_task_params", {"task_id": "task-1", "params": {"times": 4}})
    decision = _evaluate(_set_params_definition(), params, _context(CallerType.REST))

    assert decision.requires_confirmation is True
    assert decision.risk == RiskLevel.CONSUME


def test_set_task_params_without_a_database_session_fails_closed() -> None:
    params = _validated("set_task_params", {"task_id": "task-1", "params": {"times": 4}})
    decision = _evaluate(
        _set_params_definition(),
        params,
        _context(CallerType.INTERNAL, session_id="session-1"),
    )

    assert decision.requires_confirmation is True


def test_set_task_params_reads_the_persisted_task_and_merges_the_patch(policy_db) -> None:
    """patch 只有 ``times`` 时，现有参数里的 ``shopping=true`` 仍要弹确认（D1）。"""
    params = _validated("set_task_params", {"task_id": "task-mall", "params": {"times": 4}})

    decision, lookups = _run_with_task(
        policy_db,
        _seed_task(),
        lambda context: PolicyEngine().evaluate(_set_params_definition(), params, context),
    )

    assert lookups == ["task-mall"]
    assert decision.requires_confirmation is True
    assert decision.risk == RiskLevel.CONSUME
    assert decision.reasons == ("Mall.shopping=True",)


def test_set_task_params_patch_overrides_the_persisted_value(policy_db) -> None:
    """现有 ``shopping=true`` + 本次显式 ``false``：内核最终收到 false，不确认。"""
    params = _validated(
        "set_task_params", {"task_id": "task-mall", "params": {"shopping": False}}
    )

    decision, _lookups = _run_with_task(
        policy_db,
        _seed_task(),
        lambda context: PolicyEngine().evaluate(_set_params_definition(), params, context),
    )

    assert decision.requires_confirmation is False
    assert decision.reasons == ()


def test_set_task_params_requires_confirmation_for_a_risk_field_in_the_patch(policy_db) -> None:
    params = _validated("set_task_params", {"task_id": "task-fight", "params": {"stone": 2}})

    decision, _lookups = _run_with_task(
        policy_db,
        _seed_task(
            id="task-fight",
            type_name="Fight",
            task_name="刷理智",
            params={"stage": "1-7", "stone": 0},
        ),
        lambda context: PolicyEngine().evaluate(_set_params_definition(), params, context),
    )

    assert decision.requires_confirmation is True
    assert decision.reasons == ("Fight.stone=2",)


def test_set_task_params_accepts_a_clean_patch_on_a_fight_task(policy_db) -> None:
    """Fight 的计数字段默认 0：只改次数的运行中更新不该被误报。"""
    params = _validated("set_task_params", {"task_id": "task-fight", "params": {"times": 7}})

    decision, _lookups = _run_with_task(
        policy_db,
        _seed_task(
            id="task-fight",
            type_name="Fight",
            task_name="刷理智",
            params={"stage": "1-7", "stone": 0, "medicine": 0},
        ),
        lambda context: PolicyEngine().evaluate(_set_params_definition(), params, context),
    )

    assert decision.requires_confirmation is False
    assert decision.reasons == ()


def test_set_task_params_fails_closed_when_the_task_row_is_missing(policy_db) -> None:
    params = _validated(
        "set_task_params", {"task_id": "task-missing", "params": {"times": 4}}
    )

    decision, _lookups = _run_with_task(
        policy_db,
        None,
        lambda context: PolicyEngine().evaluate(_set_params_definition(), params, context),
    )

    assert decision.requires_confirmation is True


def test_set_task_params_fails_closed_when_the_row_read_fails(policy_db) -> None:
    """读取现有参数报错时不能跳过判定，按保守策略要求确认。"""
    params = _validated(
        "set_task_params", {"task_id": "task-mall", "params": {"times": 4}}
    )

    async def evaluate(factory):
        async with factory() as session:
            context = _context(CallerType.REST, db_session=_FailingSession(session))
            return await PolicyEngine().evaluate(_set_params_definition(), params, context)

    decision = asyncio.run(evaluate(policy_db))

    assert decision.requires_confirmation is True
    assert decision.risk == RiskLevel.CONSUME


def test_set_task_params_fails_closed_for_an_unmapped_task_type(policy_db) -> None:
    """任务类型不在 schema 里（例如已下线类型）时，只能按 patch 保守判断。"""
    params = _validated(
        "set_task_params", {"task_id": "task-legacy", "params": {"times": 4}}
    )

    decision, _lookups = _run_with_task(
        policy_db,
        _seed_task(id="task-legacy", type_name="Legacy", task_name="旧任务"),
        lambda context: PolicyEngine().evaluate(_set_params_definition(), params, context),
    )

    assert decision.requires_confirmation is True
    assert decision.risk == RiskLevel.CONSUME


def test_set_task_params_merges_an_omitted_mutable_flag_for_roguelike(policy_db) -> None:
    """Roguelike 的 ``investment_enabled`` 默认 true：patch 不碰它时也要确认（D1）。"""
    params = _validated("set_task_params", {"task_id": "task-rogue", "params": {"mode": 4}})

    decision, _lookups = _run_with_task(
        policy_db,
        _seed_task(
            id="task-rogue",
            type_name="Roguelike",
            task_name="自动肉鸽",
            params={"theme": "Sami", "mode": 1},
        ),
        lambda context: PolicyEngine().evaluate(_set_params_definition(), params, context),
    )

    assert decision.requires_confirmation is True
    assert decision.reasons == ("Roguelike.investment_enabled=True",)


def test_set_task_params_does_not_confirm_when_the_patch_disables_the_flag(policy_db) -> None:
    params = _validated(
        "set_task_params",
        {"task_id": "task-rogue", "params": {"investment_enabled": False}},
    )

    decision, _lookups = _run_with_task(
        policy_db,
        _seed_task(
            id="task-rogue",
            type_name="Roguelike",
            task_name="自动肉鸽",
            params={"theme": "Sami", "mode": 1},
        ),
        lambda context: PolicyEngine().evaluate(_set_params_definition(), params, context),
    )

    assert decision.requires_confirmation is False
    assert decision.reasons == ()


def test_internal_caller_gets_the_same_consumption_answer_as_rest() -> None:
    """内部会话的确认判定与 REST 一致：只有原子操作才谈「会话授权」。"""
    create = PipelineCreate.model_validate({"tasks": [{"name": "Mall"}]})
    internal = _evaluate(
        _submit_definition(),
        create,
        _context(CallerType.INTERNAL, session_id="session-1", db_session=object()),
    )

    assert internal.requires_confirmation is True
    assert internal.risk == RiskLevel.CONSUME
    assert internal.authorized_by is None
