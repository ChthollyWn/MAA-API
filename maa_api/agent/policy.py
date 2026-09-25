"""Parameter-aware confirmation and session authorization policy for agent tools."""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Any

from pydantic import BaseModel

from maa_api.agent.registry import ToolContext, ToolDefinition, ToolRisk
from maa_api.db.repositories.agent import AgentSessionRepository
from maa_api.db.repositories.audit import ConfirmationRepository
from maa_api.domain.enums import AgentSessionStatus, CallerType, RiskLevel
from maa_api.domain.task import RISK_FIELDS

DEFAULT_ATOMIC_GRANT_MINUTES = 15
MAX_ATOMIC_GRANT_MINUTES = 60

ATOMIC_TOOL_NAMES = frozenset(
    {"click", "swipe", "long_press", "input_text", "key_event"}
)
SAFE_TOOL_NAMES = frozenset(
    {
        "stop_pipeline",
        "trigger_screencap",
        "back_to_home",
        "upload_copilot",
        "delete_schedule",
    }
)

_CONSUMPTION_FIELD_ORDER: dict[str, tuple[str, ...]] = {
    "Fight": ("stone", "medicine", "expiring_medicine"),
    "Recruit": ("expedite",),
    "Mall": ("shopping",),
    "Roguelike": ("investment_enabled",),
}
_CONSUMPTION_COUNT_FIELDS = frozenset(
    {"stone", "medicine", "expiring_medicine"}
)
_CONSUMPTION_FIELD_NAMES = frozenset(
    field for fields in RISK_FIELDS.values() for field in fields
)


@dataclass(frozen=True, slots=True)
class PolicyDecision:
    """Resolved risk, confirmation route, and any existing grant attribution."""

    requires_confirmation: bool
    risk: RiskLevel
    reasons: tuple[str, ...] = ()
    confirmation_action: str | None = None
    authorized_by: str | None = None
    window_seconds: int | None = None


class PolicyEngine:
    """Evaluate tool risk against arguments and persisted internal-session grants."""

    def __init__(self, *, atomic_grant_minutes: int = DEFAULT_ATOMIC_GRANT_MINUTES):
        if isinstance(atomic_grant_minutes, bool) or not isinstance(
            atomic_grant_minutes, int
        ):
            raise TypeError("atomic_grant_minutes must be an integer")
        if not 1 <= atomic_grant_minutes <= MAX_ATOMIC_GRANT_MINUTES:
            raise ValueError(
                f"atomic_grant_minutes must be between 1 and {MAX_ATOMIC_GRANT_MINUTES}"
            )
        self.atomic_grant_minutes = atomic_grant_minutes

    async def evaluate(
        self,
        definition: ToolDefinition,
        arguments: Any,
        context: ToolContext,
    ) -> PolicyDecision:
        """Return whether a tool call needs confirmation and why.

        Only an INTERNAL caller can reuse the time-bounded grant attached to its
        persisted agent session. External REST/MCP requests are always evaluated
        per call and do not load or inherit internal session authorization.
        """
        name = definition.name
        declared_risk = _tool_risk(definition.risk)

        if name in SAFE_TOOL_NAMES:
            return PolicyDecision(False, RiskLevel.NONE)

        if name in ATOMIC_TOOL_NAMES:
            return await self._evaluate_atomic(definition, context)

        if name in {
            "submit_pipeline",
            "set_task_params",
            "create_schedule",
            "update_schedule",
        }:
            reasons = _consumption_reasons(arguments, tool_name=name)
            if reasons:
                return PolicyDecision(
                    True,
                    RiskLevel.CONSUME,
                    reasons=reasons,
                    confirmation_action=name,
                )
            return PolicyDecision(False, RiskLevel.NONE)

        if declared_risk is ToolRisk.SAFE:
            return PolicyDecision(False, RiskLevel.NONE)

        if declared_risk is ToolRisk.DANGEROUS:
            return PolicyDecision(
                True,
                RiskLevel.DESTRUCTIVE,
                reasons=(f"{name} 属于需确认的高风险操作",),
                confirmation_action=name,
            )

        # A CONDITIONAL tool without a registered argument rule must fail closed.
        return PolicyDecision(
            True,
            RiskLevel.DESTRUCTIVE,
            reasons=(f"{name} 尚未配置参数风险规则",),
            confirmation_action=name,
        )

    async def _evaluate_atomic(
        self, definition: ToolDefinition, context: ToolContext
    ) -> PolicyDecision:
        if _caller(context.caller) is not CallerType.INTERNAL or not context.session_id:
            return _atomic_confirmation(definition.name)

        session = None
        if context.db_session is not None:
            session = await AgentSessionRepository(context.db_session).get(
                context.session_id
            )
        grant_id = getattr(session, "atomic_grant_id", None)
        expires_at = getattr(session, "atomic_grant_expires_at", None)
        session_active = (
            str(getattr(session, "status", "")) == AgentSessionStatus.ACTIVE.value
        )
        if (
            session_active
            and grant_id
            and expires_at is not None
            and _is_future(expires_at)
            and await self._is_approved_grant(grant_id, context)
        ):
            return PolicyDecision(
                False,
                RiskLevel.NONE,
                authorized_by=grant_id,
            )

        if session_active or context.db_session is None:
            return PolicyDecision(
                True,
                RiskLevel.NONE,
                reasons=("当前会话尚未获得有效的原子操作授权",),
                confirmation_action="grant_atomic_ops",
                window_seconds=self.atomic_grant_minutes * 60,
            )
        return _atomic_confirmation(definition.name)

    async def _is_approved_grant(
        self, grant_id: str, context: ToolContext
    ) -> bool:
        if context.db_session is None:
            return False
        confirmation = await ConfirmationRepository(context.db_session).get(
            grant_id
        )
        return bool(
            confirmation is not None
            and str(confirmation.status) == "approved"
            and confirmation.action == "grant_atomic_ops"
            and str(confirmation.requested_by) == CallerType.INTERNAL.value
            and confirmation.payload.get("session_id") == context.session_id
        )


def _atomic_confirmation(name: str) -> PolicyDecision:
    return PolicyDecision(
        True,
        RiskLevel.NONE,
        reasons=(f"{name} 是绕过 MaaCore 识别与容错的原子操作",),
        confirmation_action=name,
    )


def _tool_risk(value: ToolRisk | str) -> ToolRisk:
    if isinstance(value, ToolRisk):
        return value
    return ToolRisk(str(value).upper())


def _caller(value: CallerType | str) -> CallerType | None:
    try:
        return CallerType(value)
    except ValueError:
        return None


def _is_future(value: datetime) -> bool:
    """Compare database UTC-naive and API timezone-aware timestamp values safely."""
    if value.tzinfo is None:
        value = value.replace(tzinfo=UTC)
    return value > datetime.now(UTC)


def _as_json_value(value: Any) -> Any:
    if isinstance(value, BaseModel):
        return value.model_dump(mode="python", by_alias=True, exclude_unset=True)
    return value


def _consumption_reasons(arguments: Any, *, tool_name: str) -> tuple[str, ...]:
    reasons: list[str] = []
    value = _as_json_value(arguments)
    if not isinstance(value, Mapping):
        return ()

    if tool_name in {"submit_pipeline", "create_schedule", "update_schedule"}:
        tasks = value.get("tasks", value.get("template", ()))
        task_values = tasks if isinstance(tasks, (list, tuple)) else (tasks,)
    else:
        task = value.get("task")
        params = (
            task
            if isinstance(task, Mapping)
            else value.get("params", value.get("task_params", value))
        )
        task_values = (params,)

    def inspect_task(task: Any) -> None:
        task = _as_json_value(task)
        if not isinstance(task, Mapping):
            return
        task_name = next(
            (
                task[key]
                for key in ("name", "task_name", "type", "type_name")
                if isinstance(task.get(key), str)
                and task[key] in RISK_FIELDS
            ),
            None,
        )
        params = task.get("params", task.get("raw_params", task))
        params = _as_json_value(params)
        if not isinstance(params, Mapping):
            return
        field_names = (
            _CONSUMPTION_FIELD_ORDER[task_name]
            if task_name in _CONSUMPTION_FIELD_ORDER
            else tuple(sorted(_CONSUMPTION_FIELD_NAMES))
        )
        if task_name is not None:
            field_names = tuple(
                field for field in field_names if field in RISK_FIELDS[task_name]
            )
        for field in field_names:
            if field not in params:
                continue
            candidate = params[field]
            if field in _CONSUMPTION_COUNT_FIELDS:
                triggered = (
                    isinstance(candidate, (int, float))
                    and not isinstance(candidate, bool)
                    and candidate > 0
                )
            else:
                triggered = candidate is True
            if triggered:
                label = f"{task_name}.{field}" if task_name else field
                reasons.append(f"{label}={candidate}")

    for task in task_values:
        inspect_task(task)
    return tuple(dict.fromkeys(reasons))
