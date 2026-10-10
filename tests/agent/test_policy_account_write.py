"""养成计划确认卡片（M31 补口，规格 §7.7）：预演进 payload、冲突进 reasons。

M27 的确认卡片只有 ``summary`` / ``targets`` / ``run_times``，用户按下确认之前看不到
「会和现有定时任务争抢理智」这件事（docs/13 决策记录 :1207）。这一轮把
:class:`~maa_api.services.account.plans.UpgradePlanService.preview` 的结果接进确认
payload：拿得到就放 ``preview`` 并把冲突写进 ``reasons``，拿不到就放 ``preview_error``
——无论如何确认照弹、风险仍是 NONE。

本文件只驱动策略引擎，不碰数据库：``start_upgrade_plan`` 的分支不读会话。
"""

from __future__ import annotations

import asyncio
from typing import Any

import pytest

from maa_api.agent.policy import PLAN_PREVIEW_ERROR_LIMIT, PolicyEngine
from maa_api.agent.registry import ToolContext
from maa_api.agent.tools import build_registry
from maa_api.domain.enums import CallerType, RiskLevel

ARGS = {
    "name": "银灰精二",
    "targets": [{"operator": "银灰", "elite": 2}],
    "run_times": ["08:00", "20:00"],
}
SUMMARY = "启动养成计划「银灰精二」：1 个目标，每日 08:00、20:00"


def async_test(func):
    def wrapper(*args, **kwargs):
        return asyncio.run(func(*args, **kwargs))

    wrapper.__name__ = func.__name__
    return wrapper


def _context() -> ToolContext:
    return ToolContext(
        caller=CallerType.INTERNAL,
        session_id="session-1",
        request_id="request-1",
        request=None,
        db_session=None,
    )


async def _decide(
    arguments: dict[str, Any], *, engine: PolicyEngine | None = None
) -> Any:
    """走真实注册表 + 参数模型（生产路径），只把引擎换成注入了预演的。"""
    definition = build_registry().get("start_upgrade_plan")
    params = definition.params_model.model_validate(arguments)
    policy = engine if engine is not None else PolicyEngine()
    return await policy.evaluate(definition, params, _context())


async def _decide_raw(
    arguments: dict[str, Any], engine: PolicyEngine
) -> Any:
    """跳过参数模型的裸字典路径：用来构造「参数不齐」的调用。"""
    definition = build_registry().get("start_upgrade_plan")
    return await engine.evaluate(definition, arguments, _context())


class RecordingPreview:
    """记录调用参数、返回预设结果的预演提供者。"""

    def __init__(self, result: Any = None, *, exc: BaseException | None = None) -> None:
        self.result = result if result is not None else {"conflicts": []}
        self.exc = exc
        self.calls: list[dict[str, Any]] = []

    async def __call__(self, **kwargs: Any) -> Any:
        self.calls.append(kwargs)
        if self.exc is not None:
            raise self.exc
        return self.result


# ---------------------------------------------------------------------------
# 预演进 payload
# ---------------------------------------------------------------------------


@async_test
async def test_preview_lands_in_the_confirmation_payload() -> None:
    preview = {"today_tasks": [], "days_estimated": 3, "conflicts": [], "notes": []}
    provider = RecordingPreview(preview)
    context = _context()

    definition = build_registry().get("start_upgrade_plan")
    params = definition.params_model.model_validate(ARGS)
    decision = await PolicyEngine(plan_preview=provider).evaluate(
        definition, params, context
    )

    assert provider.calls[0]["name"] == "银灰精二"
    assert provider.calls[0]["targets"][0]["operator"] == "银灰"
    assert provider.calls[0]["targets"][0]["elite"] == 2
    assert provider.calls[0]["run_times"] == ["08:00", "20:00"]
    # 预演看到的上下文就是这次工具调用的上下文（装配时可直接注入 service.preview）。
    assert provider.calls[0]["context"] is context
    assert decision.extra_payload is not None
    assert decision.extra_payload["preview"] == preview
    assert "preview_error" not in decision.extra_payload
    # 老的三个字段一个不少。
    assert decision.extra_payload["summary"] == SUMMARY
    assert decision.extra_payload["targets"][0]["operator"] == "银灰"
    assert decision.extra_payload["run_times"] == ["08:00", "20:00"]
    assert decision.reasons == (SUMMARY,)
    assert decision.requires_confirmation is True
    assert decision.risk is RiskLevel.NONE


# ---------------------------------------------------------------------------
# 冲突进 reasons
# ---------------------------------------------------------------------------


@async_test
async def test_conflicts_add_a_reason_and_keep_the_risk() -> None:
    provider = RecordingPreview(
        {
            "conflicts": [
                {"schedule_id": "sched-1", "name": "每日刷材料"},
                {"schedule_id": "sched-2", "name": "银灰精二 每日"},
            ]
        }
    )

    decision = await _decide(ARGS, engine=PolicyEngine(plan_preview=provider))

    assert decision.reasons == (
        SUMMARY,
        "与现有定时任务争抢理智：每日刷材料（sched-1）、银灰精二 每日（sched-2）",
    )
    # 冲突只是提示：风险与「每次都要确认」都不变。
    assert decision.risk is RiskLevel.NONE
    assert decision.requires_confirmation is True
    assert decision.confirmation_action == "start_upgrade_plan"


@async_test
async def test_conflicts_with_missing_fields_degrade_or_skip() -> None:
    """条目的字段缺一个就用另一边，全缺就跳过——不能让确认卡片多一行空话。"""
    provider = RecordingPreview(
        {
            "conflicts": [
                {"schedule_id": "sched-1"},
                {"name": "每日刷材料"},
                {"schedule_id": "", "name": "  "},
                "sched-2",
            ]
        }
    )

    decision = await _decide(ARGS, engine=PolicyEngine(plan_preview=provider))

    assert decision.reasons == (
        SUMMARY,
        "与现有定时任务争抢理智：sched-1、每日刷材料、sched-2",
    )


@async_test
async def test_empty_conflicts_add_no_reason() -> None:
    provider = RecordingPreview({"conflicts": []})

    decision = await _decide(ARGS, engine=PolicyEngine(plan_preview=provider))

    assert decision.reasons == (SUMMARY,)


# ---------------------------------------------------------------------------
# 预演失败只降级
# ---------------------------------------------------------------------------


@async_test
async def test_preview_failure_still_requires_confirmation() -> None:
    provider = RecordingPreview(exc=RuntimeError("planner 还没就绪"))

    decision = await _decide(ARGS, engine=PolicyEngine(plan_preview=provider))

    assert decision.extra_payload is not None
    assert decision.extra_payload["preview_error"] == "planner 还没就绪"
    assert "preview" not in decision.extra_payload
    assert decision.extra_payload["summary"] == SUMMARY
    assert decision.extra_payload["targets"][0]["operator"] == "银灰"
    assert decision.extra_payload["run_times"] == ["08:00", "20:00"]
    assert decision.requires_confirmation is True
    assert decision.risk is RiskLevel.NONE
    assert decision.confirmation_action == "start_upgrade_plan"
    assert decision.reasons == (SUMMARY,)


@async_test
async def test_preview_error_is_a_short_string() -> None:
    provider = RecordingPreview(exc=RuntimeError("预演炸了" * 200))

    decision = await _decide(ARGS, engine=PolicyEngine(plan_preview=provider))

    assert decision.extra_payload is not None
    error = decision.extra_payload["preview_error"]
    assert isinstance(error, str)
    assert len(error) == PLAN_PREVIEW_ERROR_LIMIT


@async_test
async def test_preview_error_falls_back_to_the_exception_name() -> None:
    provider = RecordingPreview(exc=RuntimeError())

    decision = await _decide(ARGS, engine=PolicyEngine(plan_preview=provider))

    assert decision.extra_payload is not None
    assert decision.extra_payload["preview_error"] == "RuntimeError"


@async_test
async def test_non_mapping_preview_is_reported_as_error() -> None:
    provider = RecordingPreview(["不是字典"])

    decision = await _decide(ARGS, engine=PolicyEngine(plan_preview=provider))

    assert decision.extra_payload is not None
    assert decision.extra_payload["preview_error"] == "计划预演返回的不是映射：list"
    assert "preview" not in decision.extra_payload
    assert decision.requires_confirmation is True


# ---------------------------------------------------------------------------
# 不注入提供者时行为不变
# ---------------------------------------------------------------------------


@async_test
async def test_without_a_provider_the_payload_stays_m27() -> None:
    decision = await _decide(ARGS)

    assert decision.extra_payload is not None
    assert set(decision.extra_payload) == {"summary", "targets", "run_times"}
    assert decision.reasons == (SUMMARY,)
    assert decision.requires_confirmation is True
    assert decision.risk is RiskLevel.NONE


@async_test
async def test_incomplete_arguments_do_not_call_the_provider() -> None:
    provider = RecordingPreview()
    engine = PolicyEngine(plan_preview=provider)

    for arguments in (
        {"name": "", "targets": [{"operator": "银灰"}], "run_times": ["08:00"]},
        {"name": "银灰精二", "targets": [], "run_times": ["08:00"]},
        {"name": "银灰精二", "targets": [{"operator": "银灰"}], "run_times": []},
    ):
        decision = await _decide_raw(arguments, engine)
        assert decision.extra_payload is not None
        assert "preview" not in decision.extra_payload
        assert "preview_error" not in decision.extra_payload
        assert decision.requires_confirmation is True

    assert provider.calls == []


def test_plan_preview_must_be_callable() -> None:
    with pytest.raises(TypeError):
        PolicyEngine(plan_preview=object())  # type: ignore[arg-type]
