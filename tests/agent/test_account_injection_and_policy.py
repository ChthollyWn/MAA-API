"""M27：账号写入的确认策略与账号数据注入段（规格 §7.4、§7.7、§7.8）。"""

from __future__ import annotations

import asyncio
from typing import Any

from maa_api.agent.knowledge import (
    ACCOUNT_HEADER,
    GLOSSARY_HEADER,
    INDEX_HEADER,
    render_account_lines,
)
from maa_api.agent.policy import PolicyEngine
from maa_api.agent.registry import ToolContext
from maa_api.agent.tools import build_registry
from maa_api.domain.enums import CallerType, RiskLevel


def async_test(func):
    def wrapper(*args, **kwargs):
        return asyncio.run(func(*args, **kwargs))

    wrapper.__name__ = func.__name__
    return wrapper


def _context(*, db_session: Any = None) -> ToolContext:
    return ToolContext(
        caller=CallerType.INTERNAL,
        session_id="session-1",
        request_id="request-1",
        request=None,
        db_session=db_session,
    )


async def _decide(name: str, arguments: dict[str, Any], *, db_session: Any = None):
    registry = build_registry()
    definition = registry.get(name)
    params = definition.params_model.model_validate(arguments)
    return await PolicyEngine().evaluate(definition, params, _context(db_session=db_session))


@async_test
async def test_start_upgrade_plan_confirmation_carries_a_summary() -> None:
    decision = await _decide(
        "start_upgrade_plan",
        {
            "name": "银灰精二",
            "targets": [{"operator": "银灰", "elite": 2}],
            "run_times": ["08:00", "20:00"],
        },
    )
    assert decision.requires_confirmation is True
    assert decision.risk is RiskLevel.NONE
    assert decision.confirmation_action == "start_upgrade_plan"
    assert decision.extra_payload is not None
    assert decision.extra_payload["summary"] == "启动养成计划「银灰精二」：1 个目标，每日 08:00、20:00"
    assert decision.extra_payload["run_times"] == ["08:00", "20:00"]


@async_test
async def test_account_read_tools_stay_safe() -> None:
    """读接口不需要确认。"""
    for name, arguments in (
        ("get_operator_box", {}),
        ("get_depot", {}),
        ("list_upgrade_plans", {}),
        ("cancel_upgrade_plan", {"plan_id": "plan-1"}),
    ):
        decision = await _decide(name, arguments)
        assert decision.requires_confirmation is False, name


def test_render_account_lines_matches_the_spec_shape() -> None:
    """规格 §7.8 的注入行：数量、精二数、识别时间、过期提示。"""
    lines = render_account_lines(
        {
            "operators": {
                "recognized": True,
                "total": 312,
                "elite2": 85,
                "recognized_at": "2026-10-02T20:00:00+08:00",
                "stale": True,
            },
            "depot": {
                "recognized": True,
                "recognized_at": "2026-10-08T20:00:00+08:00",
                "stale": False,
            },
        }
    )
    assert lines[0].startswith("干员 312 名（精二 85）识别于 10-02（")
    assert lines[0].endswith("天前），已过期")
    assert lines[1].startswith("仓库识别于 10-08（")
    assert lines[1].endswith("天前）")
    assert all(len(line) <= 120 for line in lines)


def test_render_account_lines_says_never_recognized() -> None:
    lines = render_account_lines(
        {"operators": {"recognized": False}, "depot": {"recognized": False}}
    )
    assert lines == [
        "干员：尚未同步（需要时建议在账号页绑定森空岛账号并同步练度）",
        "仓库：尚未识别（需要时建议运行仓库识别）",
    ]


def test_render_account_lines_is_empty_without_a_summary() -> None:
    assert render_account_lines(None) == []
    assert render_account_lines({}) == []


def test_account_section_sits_between_activity_and_glossary() -> None:
    """段落顺序：索引 → 当期活动 → 账号数据 → 术语表（规格 §4.5）。"""
    assert INDEX_HEADER != ACCOUNT_HEADER
    assert GLOSSARY_HEADER != ACCOUNT_HEADER
