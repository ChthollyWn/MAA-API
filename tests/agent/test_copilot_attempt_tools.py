"""作业尝试两条工具（规格 §9.7、docs/11 §3.2）：注册元信息、参数边界与派发。

`agent/tools/__init__.py` 的接入与 `agent/policy.py` 的授权分支由主 agent 做（本任务的
写范围不含它们），所以这里自己建一个空注册表调 `register_tools`，再断言目录、参数模型与
handler 的派发；另加两条「主 agent 需要的行为」：缺授权时 `COPILOT_GRANT_*` /
`CONFIRMATION_REQUIRED` 原样抛给上层，以及参数模型不接受多余字段。
"""

from __future__ import annotations

import asyncio
from types import SimpleNamespace
from typing import Any

import pytest
from pydantic import ValidationError

from maa_api.agent.registry import ToolContext, ToolDefinition, ToolRegistry, ToolRisk
from maa_api.agent.tools.copilot_attempt import (
    RecordCopilotResultParams,
    RunCopilotAttemptParams,
    record_copilot_result,
    register_tools,
    run_copilot_attempt,
)
from maa_api.domain.enums import CallerType
from maa_api.domain.errors import AppError, ErrorCode


def registry() -> ToolRegistry:
    item = ToolRegistry()
    register_tools(item)
    return item


def context(**state: Any) -> ToolContext:
    return ToolContext(
        CallerType.INTERNAL,
        "session-1",
        None,
        SimpleNamespace(app=SimpleNamespace(state=SimpleNamespace(**state))),
        None,
    )


def definition(name: str) -> ToolDefinition:
    return registry().get(name)


def call(name: str, arguments: dict[str, Any], ctx: ToolContext) -> Any:
    item = definition(name)

    async def scenario():
        return await item.handler(item.params_model.model_validate(arguments), ctx)

    return asyncio.run(scenario())


class FakeAttemptService:
    def __init__(self, *, outcome: str = "three_star") -> None:
        self.outcome = outcome
        self.runs: list[dict[str, Any]] = []
        self.records: list[dict[str, Any]] = []
        self.error: AppError | None = None

    async def run(self, **kwargs):
        if self.error is not None:
            raise self.error
        self.runs.append(kwargs)
        return {"attempt_id": "attempt-1", "outcome": self.outcome, "counted": True}

    async def record(self, attempt_id, outcome, evidence=""):
        if self.error is not None:
            raise self.error
        self.records.append(
            {"attempt_id": attempt_id, "outcome": outcome, "evidence": evidence}
        )
        return {"attempt": {"id": attempt_id, "outcome": outcome}, "counted": True}


# ---------------------------------------------------------------------------
# 注册元信息
# ---------------------------------------------------------------------------
def test_registers_two_tools_in_the_resource_group():
    items = {item.name: item for item in registry().list()}
    assert set(items) == {"run_copilot_attempt", "record_copilot_result"}
    for item in items.values():
        # docs/11 §3.2 的组别约定：作业资源与草稿工具同在 `resource` 组。
        assert item.group == "resource"
        assert item.risk is ToolRisk.SAFE
        assert item.model_visible is True
    assert items["run_copilot_attempt"].read_only is False
    assert items["record_copilot_result"].read_only is False


def test_run_tool_returns_a_typed_result_model():
    assert RunCopilotAttemptParams.model_fields["copilot_id"].is_required()
    params = RunCopilotAttemptParams(copilot_id="a")
    assert params.grant_attempts == 3
    assert params.success == "three_star"
    assert params.support is None


def test_record_tool_limits_outcome_and_evidence():
    assert RecordCopilotResultParams.model_fields["attempt_id"].is_required()
    assert (
        RecordCopilotResultParams(attempt_id="a", outcome="clear").evidence == ""
    )
    for outcome in ("three_star", "two_star", "clear", "failed"):
        assert RecordCopilotResultParams(attempt_id="a", outcome=outcome).outcome == outcome
    for bad in ("unknown", "sanity_insufficient", "error", "not_started"):
        with pytest.raises(ValidationError):
            RecordCopilotResultParams(attempt_id="a", outcome=bad)
    with pytest.raises(ValidationError):
        RecordCopilotResultParams(attempt_id="a", outcome="three_star", evidence="x" * 201)
    with pytest.raises(ValidationError):
        RecordCopilotResultParams(attempt_id="a", outcome="three_star", extra=1)


def test_run_tool_rejects_out_of_range_attempts_and_unknown_support():
    with pytest.raises(ValidationError):
        RunCopilotAttemptParams(copilot_id="a", grant_attempts=0)
    with pytest.raises(ValidationError):
        RunCopilotAttemptParams(copilot_id="a", grant_attempts=11)
    with pytest.raises(ValidationError):
        RunCopilotAttemptParams(copilot_id="a", support="maybe")
    with pytest.raises(ValidationError):
        RunCopilotAttemptParams(copilot_id="a", success="two_star")


# ---------------------------------------------------------------------------
# 派发
# ---------------------------------------------------------------------------
def test_run_dispatches_to_the_attempt_service_with_the_session():
    fake = FakeAttemptService(outcome="failed")
    ctx = context(copilot_attempt_service=fake)
    result = call(
        "run_copilot_attempt",
        {"copilot_id": "a" * 32, "support": "none", "grant_attempts": 5, "success": "clear"},
        ctx,
    )
    assert result["attempt_id"] == "attempt-1"
    assert fake.runs == [
        {
            "copilot_id": "a" * 32,
            "session_id": "session-1",
            "support": "none",
            "grant_attempts": 5,
            "success": "clear",
        }
    ]


def test_record_dispatches_to_the_attempt_service():
    fake = FakeAttemptService()
    ctx = context(copilot_attempt_service=fake)
    result = call(
        "record_copilot_result",
        {"attempt_id": "attempt-1", "outcome": "two_star", "evidence": "两星通关"},
        ctx,
    )
    assert result["attempt"]["outcome"] == "two_star"
    assert fake.records == [
        {"attempt_id": "attempt-1", "outcome": "two_star", "evidence": "两星通关"}
    ]


def test_missing_grant_errors_reach_the_caller():
    """主 agent 的 policy 分支需要认这两个错误码（以及缺授权时的确认动作）。"""
    for code in (
        ErrorCode.COPILOT_GRANT_STAGE_MISMATCH,
        ErrorCode.COPILOT_GRANT_EXHAUSTED,
        ErrorCode.CONFIRMATION_REQUIRED,
    ):
        fake = FakeAttemptService()
        fake.error = AppError(code, "需要授权", {"confirmation_action": "grant_copilot_attempts"})
        ctx = context(copilot_attempt_service=fake)
        with pytest.raises(AppError) as info:
            call("run_copilot_attempt", {"copilot_id": "a" * 32}, ctx)
        assert info.value.code == code
        # 两个授权错误是 409；缺授权的 CONFIRMATION_REQUIRED 沿用既有映射（202，
        # 表示「弹卡片等用户批准」而不是失败）。
        expected = 202 if code is ErrorCode.CONFIRMATION_REQUIRED else 409
        assert info.value.http_status == expected


def test_handlers_are_importable_directly():
    """注册表按 handler 引用调用；函数本身也要能直接 await（REST 侧同一入口）。"""
    fake = FakeAttemptService()
    ctx = context(copilot_attempt_service=fake)
    params = RunCopilotAttemptParams(copilot_id="a" * 32)
    result = asyncio.run(run_copilot_attempt(params, ctx))
    assert result["outcome"] == "three_star"
    record = RecordCopilotResultParams(attempt_id="attempt-1", outcome="clear")
    assert asyncio.run(record_copilot_result(record, ctx))["counted"] is True


def test_assembled_service_gets_the_resource_service_bound():
    """main.py 早于资源服务构造 attempt 服务时，工具层补一次后置绑定（否则识别写不出文件）。"""
    from maa_api.services.copilot.attempt import CopilotAttemptService
    from maa_api.services.copilot.result import CopilotResultService

    result_service = CopilotResultService()
    attempt_service = CopilotAttemptService(
        None,
        pipeline_service=SimpleNamespace(),
        grant_service=SimpleNamespace(),
        result_service=result_service,
    )
    fake_resource = SimpleNamespace(resource_root="/tmp/nowhere")
    ctx = context(
        copilot_attempt_service=attempt_service, resource_service=fake_resource
    )
    params = RunCopilotAttemptParams(copilot_id="a" * 32)

    async def scenario():
        with pytest.raises(Exception):
            # pipeline_service 是空壳，跑到提交流水线就炸——这里只验绑定这一步。
            await run_copilot_attempt(params, ctx)

    asyncio.run(scenario())
    assert result_service.resource_service is fake_resource


def test_missing_service_assembly_is_reported():
    ctx = context()
    with pytest.raises(AppError) as info:
        call("record_copilot_result", {"attempt_id": "a", "outcome": "clear"}, ctx)
    assert info.value.code == ErrorCode.SERVICE_UNAVAILABLE
