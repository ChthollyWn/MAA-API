"""四个目标工具的注册与确认策略（M82 规格 §4.2、§4.5.5、§6）。

工具壳的 handler 与 :class:`GoalService` 的接缝由 T7 装配后覆盖；本文件钉住
``goals`` 组的注册形状，以及 ``create_goal``/``update_goal`` 在 ``PolicyEngine``
里的确认口径。
"""

from __future__ import annotations

import asyncio
from types import SimpleNamespace
from typing import Any

from maa_api.agent.policy import PolicyEngine
from maa_api.agent.registry import ToolContext, ToolRisk
from maa_api.agent.tools import build_registry
from maa_api.domain.enums import CallerType, RiskLevel

GOAL_TOOLS = {"create_goal", "list_goals", "get_goal", "update_goal"}


def _context(goal_service: Any = None) -> ToolContext:
    state = SimpleNamespace(goal_service=goal_service)
    return ToolContext(
        caller=CallerType.INTERNAL,
        session_id=None,
        request_id=None,
        request=SimpleNamespace(app=SimpleNamespace(state=state)),
        db_session=None,
    )


class _ResumeService:
    """只有策略层用到的 ``resume_consumption_reasons`` 的替身。"""

    def __init__(self, reasons: list[str]) -> None:
        self.reasons = reasons
        self.calls: list[tuple[str, ToolContext]] = []

    async def resume_consumption_reasons(
        self, goal_id: str, *, context: ToolContext
    ) -> list[str]:
        self.calls.append((goal_id, context))
        return list(self.reasons)


def test_goal_tools_registered_hidden_from_rest() -> None:
    definitions = {item.name: item for item in build_registry().list("goals")}

    assert set(definitions) == GOAL_TOOLS
    for definition in definitions.values():
        assert definition.group == "goals"
        assert definition.rest_visible is False
        assert definition.model_visible is True
    assert definitions["create_goal"].risk is ToolRisk.CONDITIONAL
    assert definitions["update_goal"].risk is ToolRisk.CONDITIONAL
    assert definitions["list_goals"].risk is ToolRisk.SAFE
    assert definitions["get_goal"].risk is ToolRisk.SAFE
    assert definitions["list_goals"].read_only is True
    assert definitions["get_goal"].read_only is True
    assert definitions["create_goal"].read_only is False
    assert definitions["update_goal"].read_only is False


def test_update_goal_resume_with_medicine_schedule_requires_confirmation() -> None:
    service = _ResumeService(["每天刷 1-7：medicine"])
    definition = build_registry().get("update_goal")

    decision = asyncio.run(
        PolicyEngine().evaluate(
            definition,
            {"goal_id": "goal-1", "status": "active"},
            _context(service),
        )
    )

    assert decision.requires_confirmation is True
    assert decision.risk == RiskLevel.CONSUME
    assert decision.confirmation_action == "update_goal"
    assert any("每天刷 1-7" in reason for reason in decision.reasons)
    assert [call[0] for call in service.calls] == ["goal-1"]


def test_create_goal_needs_no_confirmation() -> None:
    definition = build_registry().get("create_goal")

    decision = asyncio.run(
        PolicyEngine().evaluate(
            definition,
            {
                "title": "月底前练银灰",
                "target": {"text": "月底前把银灰练到精二"},
            },
            _context(),
        )
    )

    assert decision.requires_confirmation is False
    assert decision.risk == RiskLevel.NONE
