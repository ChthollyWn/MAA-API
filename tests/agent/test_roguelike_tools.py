"""`get_roguelike_info` 工具（规格 §8.4）：参数模型、注册元信息与门面装配。

服务用替身注入（`app.state.roguelike_service`），不打网（规格 §4.9）。异步用例用
`asyncio.run` 跑（本仓库不装 pytest-asyncio）。
"""

from __future__ import annotations

import asyncio
from types import SimpleNamespace
from typing import Any

import pytest
from pydantic import ValidationError

from maa_api.agent.registry import ToolContext, ToolRegistry, ToolRisk
from maa_api.agent.tools.roguelike import (
    GetRoguelikeInfoParams,
    get_roguelike_info,
    register_tools,
)
from maa_api.domain.enums import CallerType
from maa_api.services.game.activities import FOREIGN_REGION_NOTE
from maa_api.settings import Settings


class FakeService:
    """`RoguelikeService` 替身：只记调用参数，返回固定结果。"""

    def __init__(self, payload: dict[str, Any] | None = None) -> None:
        self.payload = payload or {
            "theme": "BlackFlow",
            "theme_label": "沉沦者的黑流树海",
            "topic": "squads",
            "fields": {"type": "squads", "squads": []},
            "text": "",
            "truncated": None,
            "notes": ["上游策略尚不完整"],
            "sources": [],
            "untrusted": True,
            "unavailable": [],
            "region_note": None,
        }
        self.calls: list[dict[str, Any]] = []

    async def info(self, *, theme: str | None = None, topic: str = "overview", name: str | None = None):
        self.calls.append({"theme": theme, "topic": topic, "name": name})
        return dict(self.payload)


def context(**state: Any) -> ToolContext:
    return ToolContext(
        CallerType.INTERNAL,
        None,
        None,
        SimpleNamespace(app=SimpleNamespace(state=SimpleNamespace(**state))),
        None,
    )


def call(arguments: dict[str, Any], ctx: ToolContext) -> Any:
    async def scenario():
        return await get_roguelike_info(GetRoguelikeInfoParams.model_validate(arguments), ctx)

    return asyncio.run(scenario())


def test_register_tools_exposes_a_safe_game_tool() -> None:
    registry = ToolRegistry()
    register_tools(registry)

    definition = registry.get("get_roguelike_info")
    assert definition.group == "game"
    assert definition.risk is ToolRisk.SAFE
    assert definition.read_only is True
    assert "BlackFlow" in definition.description
    assert "不完整" in definition.description or "前置" in definition.description
    assert definition.params_model is GetRoguelikeInfoParams


def test_params_model_forbids_unknown_fields_and_bounds_name() -> None:
    with pytest.raises(ValidationError):
        GetRoguelikeInfoParams(unknown=1)
    with pytest.raises(ValidationError):
        GetRoguelikeInfoParams(topic="nope")
    with pytest.raises(ValidationError):
        GetRoguelikeInfoParams(name="")
    assert GetRoguelikeInfoParams().topic == "overview"
    assert GetRoguelikeInfoParams().theme is None
    assert GetRoguelikeInfoParams().name is None


def test_handler_passes_the_arguments_through() -> None:
    service = FakeService()
    result = call({"theme": "JieGarden", "topic": "relic", "name": "热水壶"}, context(roguelike_service=service))

    assert service.calls == [{"theme": "JieGarden", "topic": "relic", "name": "热水壶"}]
    assert result["theme"] == "BlackFlow"
    assert result["notes"] == ["上游策略尚不完整"]


def test_handler_uses_defaults() -> None:
    service = FakeService()
    call({}, context(roguelike_service=service))

    assert service.calls == [{"theme": None, "topic": "overview", "name": None}]


def test_foreign_channel_gets_a_region_note() -> None:
    """外服下肉鸽数据照常查 PRTS，但结果要注明是国服数据（规格 §4.3）。"""

    service = FakeService()
    result = call(
        {},
        context(
            roguelike_service=service,
            settings_provider=lambda: Settings(channel={"client_type": "YoStarEN"}),
        ),
    )

    assert result["region_note"] == FOREIGN_REGION_NOTE


def test_cn_channel_keeps_the_region_note_empty() -> None:
    service = FakeService()
    result = call({}, context(roguelike_service=service))

    assert result["region_note"] is None
