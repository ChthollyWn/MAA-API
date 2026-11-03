"""M65 T7：工具批调度与三道执行期闸门（规格 §4.2.8）。"""

from __future__ import annotations

import asyncio
import json
from typing import Any

import pytest
from pydantic import BaseModel, ConfigDict

from maa_api.agent.harness.builtin_middleware import (
    QUESTION_SKIPPED_TEXT,
    ClarificationMiddleware,
)
from maa_api.agent.harness.context import RunContext, ToolCall, ToolOutcome
from maa_api.agent.harness.middleware import Middleware, MiddlewareChain
from maa_api.agent.harness.toolexec import (
    CONFIRMATION_TERMINAL_TEXT,
    HIDDEN_TOOL_MESSAGE,
    SKIPPED_AFTER_REJECTION_MESSAGE,
    TOOL_NOT_ALLOWED_MESSAGE,
    execute_batch,
    vision_disabled_message,
)
from maa_api.agent.registry import ToolDefinition, ToolRegistry, ToolRisk
from maa_api.agent.specs.registry import builtin_spec
from maa_api.agent.tools import build_registry
from maa_api.domain.errors import AppError, ErrorCode


class _EmptyParams(BaseModel):
    model_config = ConfigDict(extra="forbid")


async def _noop(_params: BaseModel, _context: Any) -> dict[str, Any]:
    return {}


def _tool(
    name: str, *, read_only: bool = False, model_visible: bool = True
) -> ToolDefinition:
    return ToolDefinition(
        name=name,
        group="test",
        risk=ToolRisk.SAFE,
        description=name,
        params_model=_EmptyParams,
        handler=_noop,
        read_only=read_only,
        model_visible=model_visible,
    )


def _registry(*definitions: ToolDefinition) -> ToolRegistry:
    registry = ToolRegistry()
    for definition in definitions:
        registry.register(definition)
    return registry


def _context(**options: Any) -> tuple[RunContext, list[tuple[str, Any]]]:
    emitted: list[tuple[str, Any]] = []

    async def emit(name: str, data: Any) -> None:
        emitted.append((name, data))

    ctx = RunContext(
        run_id="run-1",
        root_run_id="run-1",
        parent_run_id=None,
        session_id="session-1",
        ticket_id=None,
        spec=builtin_spec("legacy_chat"),
        emit=emit,
        **options,
    )
    return ctx, emitted


class _Executor(Middleware):
    """链上的执行器：记录并发峰值与调用顺序，默认成功。"""

    name = "executor"

    def __init__(self, *, delay: float = 0.0, outcome: ToolOutcome | None = None) -> None:
        self.delay = delay
        self.outcome = outcome
        self.calls: list[ToolCall] = []
        self.active = 0
        self.peak = 0

    def reset(self) -> None:
        self.calls = []
        self.active = 0
        self.peak = 0

    async def wrap_tool(
        self, ctx: RunContext, call: ToolCall, call_next: Any
    ) -> ToolOutcome:
        self.calls.append(call)
        self.active += 1
        self.peak = max(self.peak, self.active)
        try:
            await asyncio.sleep(self.delay)
            if self.outcome is not None:
                return self.outcome
            return ToolOutcome(
                status="ok",
                result={"tool": call.name, "arguments": call.arguments},
            )
        finally:
            self.active -= 1


def test_read_only_batches_run_concurrently_and_others_serially():
    async def scenario():
        registry = _registry(_tool("lookup", read_only=True), _tool("write_value"))
        executor = _Executor(delay=0.01)
        chain = MiddlewareChain([executor])

        ctx, _emitted = _context()
        outcomes = await execute_batch(
            ctx,
            [
                ToolCall("call-1", "lookup", '{"key": "a"}'),
                ToolCall("call-2", "lookup", '{"key": "b"}'),
            ],
            chain=chain,
            registry=registry,
        )
        assert [outcome.status for outcome in outcomes] == ["ok", "ok"]
        assert executor.peak == 2

        # 批里只要有一个非只读工具，整批就串行。
        executor.reset()
        ctx, _emitted = _context()
        outcomes = await execute_batch(
            ctx,
            [
                ToolCall("call-3", "write_value", '{"value": "x"}'),
                ToolCall("call-4", "write_value", '{"value": "y"}'),
            ],
            chain=chain,
            registry=registry,
        )
        assert [outcome.status for outcome in outcomes] == ["ok", "ok"]
        assert executor.peak == 1

    asyncio.run(scenario())


def test_parallel_tools_false_forces_serial_execution():
    async def scenario():
        registry = _registry(_tool("lookup", read_only=True))
        executor = _Executor(delay=0.01)
        ctx, _emitted = _context(parallel_tools=False)

        outcomes = await execute_batch(
            ctx,
            [
                ToolCall("call-1", "lookup", '{"key": "a"}'),
                ToolCall("call-2", "lookup", '{"key": "b"}'),
            ],
            chain=MiddlewareChain([executor]),
            registry=registry,
        )

        assert [outcome.status for outcome in outcomes] == ["ok", "ok"]
        # recon 的 parallel_tools=False：只读批也一个接一个。
        assert executor.peak == 1

    asyncio.run(scenario())


def test_tool_start_is_emitted_before_the_gates():
    async def scenario():
        registry = _registry(_tool("trigger_screencap", model_visible=False))
        executor = _Executor()
        ctx, emitted = _context()

        outcomes = await execute_batch(
            ctx,
            [ToolCall("call-1", "trigger_screencap", "{}")],
            chain=MiddlewareChain([executor]),
            registry=registry,
        )

        # 被闸门拦下的调用也先有 tool_start，前端才能挂上工具条。
        assert emitted == [
            (
                "tool_start",
                {"tool_call_id": "call-1", "name": "trigger_screencap", "arguments": {}},
            )
        ]
        assert outcomes[0].status == "error"
        assert outcomes[0].audit_id is None
        assert outcomes[0].error == {
            "code": ErrorCode.TOOL_NOT_FOUND.value,
            "message": HIDDEN_TOOL_MESSAGE,
        }
        # 被拦下的调用没进执行器（确认服务与审计都不碰）。
        assert executor.calls == []

    asyncio.run(scenario())


def test_gates_order_hidden_then_allowed_then_vision():
    async def scenario():
        registry = _registry(
            _tool("trigger_screencap", model_visible=False),
            _tool("annotate_screenshot"),
            _tool("get_screenshot"),
        )
        executor = _Executor()
        ctx, emitted = _context(
            allowed_tools=frozenset({"get_screenshot"}), vision_enabled=False
        )

        outcomes = await execute_batch(
            ctx,
            [
                ToolCall("call-1", "trigger_screencap", "{}"),
                ToolCall("call-2", "annotate_screenshot", '{"zoom": 2}'),
                ToolCall("call-3", "get_screenshot", "{}"),
            ],
            chain=MiddlewareChain([executor]),
            registry=registry,
        )

        assert [data["name"] for name, data in emitted if name == "tool_start"] == [
            "trigger_screencap",
            "annotate_screenshot",
            "get_screenshot",
        ]
        # call-1 同时命中三闸门：报第一道（隐藏）；call-2 命中白名单后排第二；
        # call-3 过两道后在视觉闸门被拦。
        assert outcomes[0].error == {
            "code": ErrorCode.TOOL_NOT_FOUND.value,
            "message": HIDDEN_TOOL_MESSAGE,
        }
        assert outcomes[1].error == {
            "code": ErrorCode.TOOL_NOT_FOUND.value,
            "message": TOOL_NOT_ALLOWED_MESSAGE,
        }
        assert outcomes[2].error == {
            "code": ErrorCode.LLM_REQUEST_FAILED.value,
            "message": vision_disabled_message("get_screenshot"),
        }
        assert executor.calls == []
        # 圈选不是原子屏幕操作，文案单独一份。
        assert vision_disabled_message("annotate_screenshot") == (
            "当前模型不支持视觉输入；圈选截图已禁用"
        )

    asyncio.run(scenario())


def test_rejected_confirmation_skips_the_rest_with_placeholders():
    async def scenario():
        registry = _registry(_tool("write_value"), _tool("drop_value"))
        rejected = ToolOutcome(
            status="rejected",
            audit_id=7,
            error={
                "code": ErrorCode.CONFIRMATION_REJECTED.value,
                "message": "不要",
            },
        )
        executor = _Executor(outcome=rejected)
        ctx, emitted = _context()

        outcomes = await execute_batch(
            ctx,
            [
                ToolCall("call-1", "write_value", '{"value": "x"}'),
                ToolCall("call-2", "drop_value", "{}"),
            ],
            chain=MiddlewareChain([executor]),
            registry=registry,
        )

        assert [outcome.status for outcome in outcomes] == ["rejected", "skipped"]
        skipped = outcomes[1]
        assert skipped.audit_id is None
        assert skipped.error == {
            "code": ErrorCode.CONFIRMATION_REJECTED.value,
            "message": SKIPPED_AFTER_REJECTION_MESSAGE,
        }
        # 余下的调用不再执行，也不发 tool_start（v2 在 run() 之前就跳出了循环）。
        assert [call.name for call in executor.calls] == ["write_value"]
        assert [data["name"] for name, data in emitted if name == "tool_start"] == [
            "write_value"
        ]

    asyncio.run(scenario())


def test_confirmation_terminal_outcomes_request_a_stop():
    async def scenario():
        registry = _registry(_tool("write_value"), _tool("drop_value"))
        expired = ToolOutcome(
            status="expired",
            audit_id=9,
            error={
                "code": ErrorCode.CONFIRMATION_EXPIRED.value,
                "message": "确认请求已过期",
                "details": {"audit_id": 9},
            },
        )
        ctx, _emitted = _context()

        outcomes = await execute_batch(
            ctx,
            [
                ToolCall("call-1", "write_value", '{"value": "x"}'),
                ToolCall("call-2", "drop_value", "{}"),
            ],
            chain=MiddlewareChain([_Executor(outcome=expired)]),
            registry=registry,
        )

        assert [outcome.status for outcome in outcomes] == ["expired", "skipped"]
        # 命中确认终态的批以 confirmation_resolved 收尾（skipped 占位也带上）。
        for outcome in outcomes:
            assert outcome.stop_run is True
            assert outcome.stop_reason == "confirmation_resolved"
            assert outcome.stop_text == CONFIRMATION_TERMINAL_TEXT

    asyncio.run(scenario())


class _FakeQuestions:
    """只回 ``skipped`` 的提问服务替身，记录创建参数。"""

    def __init__(self) -> None:
        self.created: list[dict[str, Any]] = []

    async def create(
        self,
        *,
        session_id: str,
        tool_call_id: str,
        question: str,
        options: list[dict[str, Any]],
        multiple: bool,
        request_id: str | None = None,
    ) -> tuple[dict[str, Any], int]:
        self.created.append(
            {
                "session_id": session_id,
                "tool_call_id": tool_call_id,
                "question": question,
                "multiple": multiple,
                "request_id": request_id,
            }
        )
        return (
            {
                "question_id": "question-1",
                "question": question,
                "options": options,
                "multiple": multiple,
                "expires_at": "2026-01-01T00:00:00Z",
            },
            11,
        )

    async def wait(self, question_id: str) -> dict[str, Any]:
        return {"status": "skipped", "question_id": question_id}


def test_unanswered_question_does_not_skip_later_calls():
    async def scenario():
        real = build_registry()
        registry = _registry(real.get("ask_user"), _tool("write_value"))
        questions = _FakeQuestions()
        executor = _Executor()
        chain = MiddlewareChain(
            [
                ClarificationMiddleware(question_service=questions, registry=registry),
                executor,
            ]
        )
        ctx, emitted = _context()
        arguments = {
            "question": "走哪个客户端渠道？",
            "options": [{"label": "官服"}, {"label": "B 服"}],
            "multiple": False,
        }

        outcomes = await execute_batch(
            ctx,
            [
                ToolCall(
                    "call-1", "ask_user", json.dumps(arguments, ensure_ascii=False)
                ),
                ToolCall("call-2", "write_value", '{"value": "x"}'),
            ],
            chain=chain,
            registry=registry,
        )

        assert [outcome.status for outcome in outcomes] == ["ok", "ok"]
        assert outcomes[0].audit_id == 11
        assert outcomes[0].result == {
            "status": "skipped",
            "question_id": "question-1",
        }
        assert outcomes[0].stop_run is True
        assert outcomes[0].stop_reason == "question_unanswered"
        assert outcomes[0].stop_text == QUESTION_SKIPPED_TEXT
        # v2 已知怪行为（§4.5.3 第 2 条，本里程碑不修）：提问没答成时，
        # 同一批后面的调用照常执行。
        assert [call.name for call in executor.calls] == ["write_value"]
        assert outcomes[1].status == "ok"
        assert [data["name"] for name, data in emitted if name == "tool_start"] == [
            "ask_user",
            "write_value",
        ]
        assert [name for name, _data in emitted].count("question_required") == 1
        assert questions.created == [
            {
                "session_id": "session-1",
                "tool_call_id": "call-1",
                "question": "走哪个客户端渠道？",
                "multiple": False,
                "request_id": None,
            }
        ]

    asyncio.run(scenario())


def test_bad_arguments_raise_before_any_dispatch():
    async def scenario():
        registry = _registry(_tool("lookup", read_only=True))
        executor = _Executor()
        ctx, emitted = _context()

        with pytest.raises(AppError) as caught:
            await execute_batch(
                ctx,
                [ToolCall("call-1", "lookup", '{"broken":')],
                chain=MiddlewareChain([executor]),
                registry=registry,
            )

        assert caught.value.code is ErrorCode.LLM_REQUEST_FAILED
        assert caught.value.message == "LLM 返回了无效的工具参数 JSON"
        # 坏参数在 tool_start 与派发之前整批抛出。
        assert executor.calls == []
        assert emitted == []

    asyncio.run(scenario())
