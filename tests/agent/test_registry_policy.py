"""Task registry validation and Agent tool authorization policy contracts."""

from __future__ import annotations

import asyncio
from datetime import timedelta
from typing import Any

import pytest
from pydantic import BaseModel, ConfigDict, Field
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker
from sqlalchemy.pool import NullPool
from sqlmodel import SQLModel

from maa_api.agent.policy import PolicyEngine, ToolRisk
from maa_api.agent.registry import ToolContext, ToolDefinition, ToolRegistry
from maa_api.db.models import AgentSession, Confirmation, utcnow
from maa_api.db.repositories.agent import AgentSessionRepository
from maa_api.db.session import make_engine
from maa_api.domain.enums import (
    AgentSessionStatus,
    CallerType,
    ConfirmationStatus,
    RiskLevel,
)
from maa_api.domain.errors import AppError, ErrorCode
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
    decision = _evaluate(_definition(name), {}, _context(CallerType.MCP))

    assert decision.requires_confirmation is False
    assert decision.risk == RiskLevel.NONE


def test_ordinary_dangerous_tool_requires_confirmation() -> None:
    decision = _evaluate(_definition("update_core"), {}, _context())

    assert decision.requires_confirmation is True
    assert decision.confirmation_action == "update_core"
    assert decision.risk == RiskLevel.DESTRUCTIVE


def test_copilot_upload_is_exempt_from_confirmation() -> None:
    upload = _evaluate(_definition("upload_copilot", ToolRisk.CONDITIONAL), {}, _context())

    assert upload.requires_confirmation is False


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
        _context(CallerType.MCP),
    )

    assert decision.requires_confirmation is requires_confirmation
    if requires_confirmation:
        assert decision.risk == RiskLevel.CONSUME
        assert decision.reasons


def test_consumption_reasons_include_each_triggered_risk_field() -> None:
    decision = _evaluate(
        _definition("submit_pipeline", ToolRisk.CONDITIONAL, group="pipeline"),
        {"tasks": [{"name": "Fight", "stone": 2, "medicine": 1}]},
        _context(CallerType.MCP),
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
        _context(CallerType.MCP),
    )

    assert decision.requires_confirmation is True
    assert decision.risk == RiskLevel.CONSUME
    assert decision.reasons == ("Recruit.expedite=True",)


def test_set_task_params_checks_risk_fields_without_task_type_name() -> None:
    decision = _evaluate(
        _definition("set_task_params", ToolRisk.CONDITIONAL, group="pipeline"),
        {"task_id": 7, "params": {"stone": 2}},
        _context(CallerType.MCP),
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
        _context(CallerType.MCP),
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
        _context(CallerType.MCP),
    )

    assert decision.requires_confirmation is True
    assert decision.reasons == ("Fight.stone=1",)


@pytest.mark.parametrize("caller", [CallerType.REST, CallerType.MCP])
def test_external_atomic_calls_always_require_per_call_confirmation(caller) -> None:
    decision = _evaluate(_definition("click"), {"x": 10}, _context(caller))

    assert decision.requires_confirmation is True
    assert decision.confirmation_action == "click"


def test_internal_atomic_call_without_a_session_fails_closed() -> None:
    with pytest.raises(AppError) as exc_info:
        _evaluate(_definition("click"), {"x": 10}, _context())

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
            external_mcp_decision = await policy.evaluate(
                _definition("swipe"),
                {"x1": 1, "y1": 1, "x2": 4, "y2": 4},
                _context(CallerType.MCP, session_id=active.id, db_session=session),
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
        assert external_mcp_decision.requires_confirmation is True
        assert external_mcp_decision.confirmation_action == "swipe"

    try:
        asyncio.run(scenario())
    finally:
        engine.sync_engine.dispose()


@pytest.mark.parametrize("caller", [CallerType.REST, CallerType.MCP])
def test_external_callers_cannot_reuse_internal_session_grants(caller) -> None:
    decision = _evaluate(
        _definition("click"),
        {"x": 10},
        _context(caller, session_id="session-valid"),
    )

    assert decision.requires_confirmation is True
    assert decision.confirmation_action == "click"
