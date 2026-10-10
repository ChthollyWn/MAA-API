"""M28：当期卡池注入段与 `update_user_profile` 的确认差异（规格 §4.5、§8.2、§8.3）。"""

from __future__ import annotations

import asyncio
from typing import Any

from maa_api.agent.knowledge import (
    GACHA_HEADER,
    MAX_GACHA_LINES,
    merge_profile_section,
    render_gacha_lines,
    split_profile_sections,
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


def test_gacha_lines_have_name_up_and_end() -> None:
    """规格 §8.2 的注入行：名称、UP 干员、结束时间。"""
    lines = render_gacha_lines(
        {
            "pools": [
                {
                    "name": "海渊巡游",
                    "end_at": "2026-10-22T03:59:00+08:00",
                    "up_six": [{"name": "史尔特尔"}],
                    "up_five": [{"name": "极境"}],
                },
                {"name": "中坚寻访", "end_at": None, "up_six": [], "up_five": []},
            ]
        }
    )
    assert lines[0] == "- 海渊巡游｜UP：史尔特尔、极境｜至 10-22 03:59"
    assert lines[1] == "- 中坚寻访｜至 时间未知"
    assert all(len(line) <= 120 for line in lines)


def test_gacha_lines_are_capped_and_tolerant() -> None:
    assert len(render_gacha_lines({"pools": [{"name": f"卡池{i}"} for i in range(6)]})) == MAX_GACHA_LINES
    assert render_gacha_lines(None) == []
    assert render_gacha_lines({}) == []
    assert GACHA_HEADER.startswith("当期卡池")


def test_profile_sections_round_trip() -> None:
    body = "## 基建布局\n243\n\n## 班次\n早班 08:00"
    assert split_profile_sections(body) == {"基建布局": "243", "班次": "早班 08:00"}
    appended = merge_profile_section(body, "班次", "晚班 20:00", mode="append")
    assert split_profile_sections(appended)["班次"] == "早班 08:00\n晚班 20:00"
    replaced = merge_profile_section(body, "基建布局", "252", mode="replace")
    assert split_profile_sections(replaced)["基建布局"] == "252"
    created = merge_profile_section(body, "碎石方针", "不碎", mode="append")
    assert split_profile_sections(created)["碎石方针"] == "不碎"
    assert list(split_profile_sections(created)) == ["基建布局", "班次", "碎石方针"]


@async_test
async def test_update_user_profile_requires_confirmation_and_reports_the_diff() -> None:
    registry = build_registry()
    definition = registry.get("update_user_profile")
    params = definition.params_model.model_validate(
        {"section": "基建布局", "content": "243", "mode": "replace"}
    )
    decision = await PolicyEngine().evaluate(definition, params, _context())
    assert decision.requires_confirmation is True
    assert decision.risk is RiskLevel.NONE
    assert decision.confirmation_action == "update_user_profile"
    assert decision.reasons == ("更新用户档案「基建布局」：替换 3 字",)
    assert decision.extra_payload is not None
    assert decision.extra_payload["section"] == "基建布局"
    # 读不到现有值时 `from` 为 None：确认卡片照常显示「将要写入什么」。
    assert decision.extra_payload["diff"] == [
        {"section": "基建布局", "from": None, "to": "243", "mode": "replace"}
    ]


@async_test
async def test_update_user_profile_append_mode_is_labelled() -> None:
    registry = build_registry()
    definition = registry.get("update_user_profile")
    params = definition.params_model.model_validate(
        {"section": "班次", "content": "晚班 20:00", "mode": "append"}
    )
    decision = await PolicyEngine().evaluate(definition, params, _context())
    assert decision.reasons == ("更新用户档案「班次」：追加 8 字",)
