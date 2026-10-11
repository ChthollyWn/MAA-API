"""M36：练度扫描两个 Agent 工具（规格 §4.7）。

- 元信息与参数边界：`scan_operator_training` 是 CONDITIONAL、`read_only=False`（它写账号
  表还占设备），`cancel_training_scan` 只是叫停，按 SAFE 处理；
- 确认策略：提交**每次**都要人工确认（挂 `ACCOUNT_WRITE_TOOLS`，理由是人数与占用设备），
  取消不弹卡片（挂 `SAFE_TOOL_NAMES`，与 `stop_pipeline` 同款）；
- 派发：两个 handler 都只认装配名 `training_scan_service`——扫描服务要设备、流水线与资源
  层，不是一个会话工厂能现造的，所以没装配时必须报装配错误而不是 TypeError。

不打网、不碰数据库：服务是假件，塞进 `app.state`。
"""

from __future__ import annotations

import asyncio
from types import SimpleNamespace
from typing import Any

import pytest
from pydantic import ValidationError

from maa_api.agent.policy import PolicyEngine
from maa_api.agent.registry import ToolContext, ToolRisk
from maa_api.agent.tools import build_registry
from maa_api.domain.enums import CallerType, RiskLevel
from maa_api.domain.errors import AppError, ErrorCode


def async_test(func):
    def wrapper(*args, **kwargs):
        return asyncio.run(func(*args, **kwargs))

    wrapper.__name__ = func.__name__
    return wrapper


class FakeScanService:
    """记账用的假服务：不碰设备，只核对参数。"""

    def __init__(self) -> None:
        self.calls: list[tuple[str, dict[str, Any]]] = []

    async def submit(self, **kwargs: Any) -> dict[str, Any]:
        self.calls.append(("submit", kwargs))
        return {
            "pipeline_id": "pipe-1",
            "total": 2,
            "targets": [
                {"char_id": "char_002_amiya", "name": "阿米娅"},
                {"char_id": "char_103_angel", "name": "能天使"},
            ],
            "deferred": ["陈"],
            "unresolved": [],
        }

    async def cancel(self) -> dict[str, Any]:
        self.calls.append(("cancel", {}))
        return {"status": "cancelled", "updated": 1, "total": 2}


def context(**state: Any) -> ToolContext:
    return ToolContext(
        CallerType.INTERNAL,
        "session-1",
        "request-1",
        SimpleNamespace(app=SimpleNamespace(state=SimpleNamespace(**state))),
        None,
    )


async def call(name: str, arguments: dict[str, Any], ctx: ToolContext) -> Any:
    definition = build_registry().get(name)
    params = definition.params_model.model_validate(arguments)
    return await definition.handler(params, ctx)


def _definition(name: str):
    return build_registry().get(name)


# ---------------------------------------------------------------------------
# 元信息与参数边界
# ---------------------------------------------------------------------------


def test_scan_tool_is_conditional_and_writes() -> None:
    definition = _definition("scan_operator_training")

    assert definition.group == "account"
    assert definition.risk is ToolRisk.CONDITIONAL
    assert definition.read_only is False


def test_cancel_tool_is_safe_and_only_stops() -> None:
    definition = _definition("cancel_training_scan")

    assert definition.group == "account"
    assert definition.risk is ToolRisk.SAFE
    assert definition.read_only is False


def test_scan_params_have_defaults_and_bounds() -> None:
    model = _definition("scan_operator_training").params_model

    params = model.model_validate({})
    assert params.limit == 40
    assert params.include_plan_targets is True
    assert params.char_ids is None

    assert model.model_validate({"limit": 1}).limit == 1
    assert model.model_validate({"limit": 200}).limit == 200
    with pytest.raises(ValidationError):
        model.model_validate({"limit": 0})
    with pytest.raises(ValidationError):
        model.model_validate({"limit": 201})
    with pytest.raises(ValidationError):
        model.model_validate({"unknown_field": 1})


def test_cancel_params_take_nothing() -> None:
    model = _definition("cancel_training_scan").params_model

    assert model.model_validate({}) is not None
    with pytest.raises(ValidationError):
        model.model_validate({"pipeline_id": "pipe-1"})


# ---------------------------------------------------------------------------
# 确认策略
# ---------------------------------------------------------------------------


@async_test
async def test_scan_always_requires_confirmation_with_the_headcount() -> None:
    definition = _definition("scan_operator_training")
    params = definition.params_model.model_validate({"limit": 12})

    decision = await PolicyEngine().evaluate(definition, params, context())

    assert decision.requires_confirmation is True
    assert decision.risk is RiskLevel.NONE
    assert decision.confirmation_action == "scan_operator_training"
    assert decision.reasons == (
        "扫描干员练度：最多 12 位，含养成计划里的目标",
        "扫描期间设备被占用，读到的技能等级、专精与模组会写进账号表",
    )
    assert decision.extra_payload is not None
    assert decision.extra_payload["limit"] == 12
    assert decision.extra_payload["include_plan_targets"] is True
    assert decision.extra_payload["char_ids"] == []


@async_test
async def test_scan_confirmation_names_the_given_roster() -> None:
    definition = _definition("scan_operator_training")
    params = definition.params_model.model_validate(
        {"char_ids": ["char_002_amiya", "银灰"], "include_plan_targets": False}
    )

    decision = await PolicyEngine().evaluate(definition, params, context())

    assert decision.reasons is not None
    assert decision.reasons[0] == "扫描干员练度：最多 40 位，名单已指定 2 位"
    assert decision.extra_payload is not None
    assert decision.extra_payload["include_plan_targets"] is False
    assert decision.extra_payload["char_ids"] == ["char_002_amiya", "银灰"]


@async_test
async def test_cancel_needs_no_confirmation() -> None:
    definition = _definition("cancel_training_scan")
    params = definition.params_model.model_validate({})

    decision = await PolicyEngine().evaluate(definition, params, context())

    assert decision.requires_confirmation is False
    assert decision.risk is RiskLevel.NONE
    assert decision.confirmation_action is None


# ---------------------------------------------------------------------------
# 派发
# ---------------------------------------------------------------------------


def test_scan_dispatches_to_the_assembled_service() -> None:
    service = FakeScanService()

    result = asyncio.run(
        call(
            "scan_operator_training",
            {"limit": 5, "char_ids": ["char_002_amiya"], "include_plan_targets": False},
            context(training_scan_service=service),
        )
    )

    assert service.calls == [
        (
            "submit",
            {"limit": 5, "char_ids": ["char_002_amiya"], "include_plan_targets": False},
        )
    ]
    assert result["pipeline_id"] == "pipe-1"
    assert result["total"] == 2


def test_scan_uses_the_defaults_when_nothing_is_given() -> None:
    service = FakeScanService()

    asyncio.run(call("scan_operator_training", {}, context(training_scan_service=service)))

    assert service.calls == [
        ("submit", {"limit": 40, "char_ids": None, "include_plan_targets": True})
    ]


def test_cancel_dispatches_to_the_assembled_service() -> None:
    service = FakeScanService()

    result = asyncio.run(call("cancel_training_scan", {}, context(training_scan_service=service)))

    assert service.calls == [("cancel", {})]
    assert result["status"] == "cancelled"


def test_scan_without_assembly_reports_a_wiring_error() -> None:
    """没装配时报 INTERNAL_ERROR 说清缺什么，而不是一头撞进 TypeError。"""
    with pytest.raises(AppError) as caught:
        asyncio.run(call("scan_operator_training", {}, context()))

    assert caught.value.code is ErrorCode.INTERNAL_ERROR
    assert "training_scan_service" in str(caught.value.message)


def test_cancel_without_assembly_reports_a_wiring_error() -> None:
    with pytest.raises(AppError) as caught:
        asyncio.run(call("cancel_training_scan", {}, context()))

    assert caught.value.code is ErrorCode.INTERNAL_ERROR
    assert "training_scan_service" in str(caught.value.message)
