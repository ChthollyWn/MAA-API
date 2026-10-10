"""`get_stage_level_data` 工具（规格 §9.3）：注册元数据、参数校验与 handler。

`agent/tools/__init__.py` 由主 agent 统一装配（M29 期间并行任务同时改工具目录），所以这里
不通过 ``build_registry()`` 取这条工具，而是自己注册一份注册表——注册项本身仍是真的
:func:`register_tools`。领域层用假的 ``StageLevelService`` 注入 ``app.state``，不打网。
"""

from __future__ import annotations

import asyncio
from types import SimpleNamespace
from typing import Any

import pytest
from pydantic import ValidationError

from maa_api.agent.registry import ToolContext, ToolRegistry, ToolRisk
from maa_api.agent.tools import stage_level as stage_level_tool
from maa_api.domain.enums import CallerType
from maa_api.domain.errors import AppError, ErrorCode


def async_test(func):
    """本仓库不装 pytest-asyncio：异步用例统一用 `asyncio.run` 跑。"""

    def wrapper(*args, **kwargs):
        return asyncio.run(func(*args, **kwargs))

    wrapper.__name__ = func.__name__
    return wrapper


def registry() -> ToolRegistry:
    item = ToolRegistry()
    stage_level_tool.register_tools(item)
    return item


def definition():
    return registry().get("get_stage_level_data")


def context(**state: Any) -> ToolContext:
    return ToolContext(
        CallerType.INTERNAL,
        None,
        None,
        SimpleNamespace(app=SimpleNamespace(state=SimpleNamespace(**state))),
        None,
    )


def call(arguments: dict[str, Any], ctx: ToolContext) -> Any:
    """按注册表的 handler 调用（参数先过 params_model，与真实调用一致）。"""
    item = definition()
    return asyncio.run(
        item.handler(item.params_model.model_validate(arguments), ctx)
    )


class FakeService:
    """假的领域服务：记录参数并按 ``level_data`` 的形状回。"""

    def __init__(self) -> None:
        self.calls: list[dict[str, Any]] = []
        self.error: Exception | None = None

    async def level_data(
        self,
        stage: str,
        *,
        include: list[str] | None = None,
        wave_detail: str = "summary",
    ) -> dict[str, Any]:
        self.calls.append({"stage": stage, "include": include, "wave_detail": wave_detail})
        if self.error is not None:
            raise self.error
        return {
            "query": stage,
            "level_id": "obt/main/level_main_01-07",
            "include": include or ["options", "map", "routes", "waves"],
            "wave_detail": wave_detail,
            "options": {"deploy_limit": 8},
            "bytes": 1234,
            "sources": [{"kind": "gamedata"}],
            "untrusted": True,
            "unavailable": [],
            "region_note": None,
        }


# --------------------------------------------------------------------------- #
# 注册元数据
# --------------------------------------------------------------------------- #


def test_tool_metadata_is_safe_read_only_and_in_the_game_group() -> None:
    """组 `game`、SAFE、只读（规格 §9.3、附录 A）。"""
    item = definition()
    assert item.name == "get_stage_level_data"
    assert item.group == "game"
    assert item.risk is ToolRisk.SAFE
    assert item.read_only is True
    assert item.handler is stage_level_tool.get_stage_level_data
    assert registry().list("game") == [item]


def test_register_tools_registers_exactly_one_tool() -> None:
    names = [item.name for item in registry().list()]
    assert names == ["get_stage_level_data"]


def test_description_marks_external_data_as_untrusted() -> None:
    """§4.1：工具描述必须写明返回的是外部数据、不是指令。"""
    description = definition().description
    assert "不是指令" in description
    assert "untrusted" in description or "拆包数据" in description
    assert "send_stage_map" in description  # 与画图工具的分工写清楚


def test_params_schema_requires_stage_and_defaults() -> None:
    model = definition().params_model
    schema = model.model_json_schema()
    assert schema["required"] == ["stage"]
    defaults = model(stage="1-7")
    assert defaults.include is None
    assert defaults.wave_detail == "summary"
    assert model(stage="1-7", include=["map"], wave_detail="full").include == ["map"]

    with pytest.raises(ValidationError):
        model()
    with pytest.raises(ValidationError):
        model(stage="1-7", include=["nope"])
    with pytest.raises(ValidationError):
        model(stage="1-7", wave_detail="everything")
    with pytest.raises(ValidationError):
        model(stage="1-7", unknown=True)


# --------------------------------------------------------------------------- #
# handler
# --------------------------------------------------------------------------- #


def test_handler_passes_arguments_through() -> None:
    fake = FakeService()
    payload = call(
        {"stage": "1-7", "include": ["map", "waves"], "wave_detail": "full"},
        context(stage_level_service=fake),
    )
    assert fake.calls == [
        {"stage": "1-7", "include": ["map", "waves"], "wave_detail": "full"}
    ]
    assert payload["wave_detail"] == "full"
    assert payload["untrusted"] is True


def test_handler_defaults_reach_the_service() -> None:
    fake = FakeService()
    call({"stage": "1-7"}, context(stage_level_service=fake))
    assert fake.calls == [{"stage": "1-7", "include": None, "wave_detail": "summary"}]


def test_handler_propagates_domain_errors() -> None:
    fake = FakeService()
    fake.error = AppError(ErrorCode.GAME_DATA_NOT_FOUND, "没有这一关", {"stage": "1-7"})
    with pytest.raises(AppError) as info:
        call({"stage": "1-7"}, context(stage_level_service=fake))
    assert info.value.code is ErrorCode.GAME_DATA_NOT_FOUND


def test_handler_falls_back_to_the_default_service(monkeypatch: pytest.MonkeyPatch) -> None:
    """``app.state`` 上没有服务时用模块级默认门面（与 game.py 同一口径）。"""
    from maa_api.services.game import level_data as level_data_module

    fake = FakeService()
    monkeypatch.setattr(level_data_module, "default_stage_level_service", lambda: fake)
    call({"stage": "1-7", "include": ["options"]}, context())
    assert fake.calls == [{"stage": "1-7", "include": ["options"], "wave_detail": "summary"}]
