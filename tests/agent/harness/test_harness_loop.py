"""M65 T8：AgentLoop 的步进、收尾与取消路径（规格 §4.2.10、§4.5.1、§4.5.2）。

全部用 :class:`MemoryTranscript` 与假模型：这里钉的是循环自己的语义（落库顺序、
``tool_result`` 字段、收尾优先级、中断补桩、失败路径），不碰数据库。
"""

from __future__ import annotations

import asyncio
import json
from collections.abc import Sequence
from typing import Any

import pytest
from pydantic import BaseModel, ConfigDict

from maa_api.agent.harness.builtin_middleware import (
    QUESTION_EXPIRED_TEXT,
    QUESTION_SKIPPED_TEXT,
    STUCK_LOOP_TEXT,
    LoopGuardMiddleware,
)
from maa_api.agent.harness.context import (
    ModelRequest,
    ModelResponse,
    RunContext,
    RunOutcome,
    StopRun,
    ToolCall,
    ToolOutcome,
    Usage,
)
from maa_api.agent.harness.loop import (
    INTERRUPTED_FINISH_REASON,
    INTERRUPTED_NOTICE,
    INTERRUPTED_TOOL_STUB,
    INVALID_TOOL_CALL_TEXT,
    AgentLoop,
    MemoryTranscript,
    select_stop,
)
from maa_api.agent.harness.middleware import CallNext, Middleware, MiddlewareChain
from maa_api.agent.harness.toolexec import CONFIRMATION_TERMINAL_TEXT
from maa_api.agent.registry import ToolDefinition, ToolRegistry, ToolRisk
from maa_api.agent.specs.registry import builtin_spec
from maa_api.domain.errors import AppError, ErrorCode


class _EmptyParams(BaseModel):
    model_config = ConfigDict(extra="forbid")


async def _unused_handler(_params: BaseModel, _context: Any) -> dict[str, Any]:
    return {}


def _tool(name: str, *, read_only: bool = False) -> ToolDefinition:
    return ToolDefinition(
        name=name,
        group="test",
        risk=ToolRisk.SAFE,
        description=name,
        params_model=_EmptyParams,
        handler=_unused_handler,
        read_only=read_only,
    )


def _registry(*definitions: ToolDefinition) -> ToolRegistry:
    registry = ToolRegistry()
    for definition in definitions:
        registry.register(definition)
    return registry


def _call(
    call_id: str,
    name: str,
    arguments: Any = None,
    *,
    index: int = 0,
) -> dict[str, Any]:
    """一条 OpenAI 形态的 ``tool_calls`` 条目；``arguments`` 可传坏 JSON 原文。"""
    raw = arguments if isinstance(arguments, str) else json.dumps(arguments or {})
    return {
        "index": index,
        "id": call_id,
        "type": "function",
        "function": {"name": name, "arguments": raw},
    }


def _response(
    *,
    text: str = "",
    reasoning: str = "",
    prompt_tokens: int | None = None,
    completion_tokens: int | None = None,
    finish_reason: str | None = None,
    latency_ms: int = 1,
    calls: Sequence[dict[str, Any]] = (),
) -> ModelResponse:
    return ModelResponse(
        text=text,
        reasoning=reasoning,
        usage=Usage(prompt_tokens=prompt_tokens, completion_tokens=completion_tokens),
        upstream_finish_reason=finish_reason,
        latency_ms=latency_ms,
        raw_tool_calls=[dict(call) for call in calls],
    )


class _FakeModel:
    """按队列作答，并把文本像 ``collect_completion`` 那样直接记进当前草稿。"""

    def __init__(self, responses: Sequence[Any]) -> None:
        self.responses = list(responses)
        self.requests: list[ModelRequest] = []

    async def __call__(self, ctx: RunContext, req: ModelRequest) -> ModelResponse:
        self.requests.append(req)
        response = self.responses.pop(0)
        if isinstance(response, BaseException):
            raise response
        if callable(response):
            response = response(ctx)
        if response.text:
            ctx.draft.text.append(response.text)
        if response.reasoning:
            ctx.draft.reasoning.append(response.reasoning)
        return response


class _Dispatch(Middleware):
    """链上的执行器：记录调用顺序与派发瞬间的历史，默认成功。"""

    name = "dispatch"

    def __init__(
        self,
        transcript: MemoryTranscript | None = None,
        *,
        audit_ids: dict[str, int] | None = None,
        cancel_on: str | None = None,
    ) -> None:
        self.transcript = transcript
        self.audit_ids = audit_ids or {}
        self.cancel_on = cancel_on
        self.calls: list[ToolCall] = []
        self.history_at_dispatch: list[list[dict[str, Any]]] = []

    async def wrap_tool(
        self, ctx: RunContext, call: ToolCall, call_next: CallNext
    ) -> ToolOutcome:
        if self.transcript is not None:
            self.history_at_dispatch.append(
                [dict(message) for message in self.transcript.messages]
            )
        self.calls.append(call)
        if call.name == self.cancel_on:
            raise asyncio.CancelledError()
        return ToolOutcome(
            status="ok",
            audit_id=self.audit_ids.get(call.id),
            result={"tool": call.name, "arguments": call.arguments},
        )


class _Stopper(Middleware):
    """在 ``after_model`` 里直接结束本轮。"""

    name = "stopper"

    def __init__(self, reason: str, *, text: str | None = None) -> None:
        self.reason = reason
        self.text = text

    async def after_model(self, ctx: RunContext, resp: ModelResponse) -> ModelResponse:
        raise StopRun(self.reason, text=self.text)


class _Recorder(Middleware):
    name = "recorder"

    def __init__(self) -> None:
        self.outcomes: list[RunOutcome] = []

    async def on_run_end(self, ctx: RunContext, outcome: RunOutcome) -> None:
        self.outcomes.append(outcome)


def _context(
    *,
    spec: Any = None,
    interrupted_now: Any = None,
    cancel: bool = False,
) -> tuple[RunContext, list[tuple[str, Any]]]:
    emitted: list[tuple[str, Any]] = []

    async def emit(name: str, data: Any) -> None:
        emitted.append((name, data))

    ctx = RunContext(
        run_id="run-1",
        root_run_id="run-1",
        parent_run_id=None,
        session_id="session-1",
        ticket_id=None,
        spec=spec or builtin_spec("legacy_chat"),
        emit=emit,
        interrupted_now=interrupted_now or (lambda: False),
    )
    if cancel:
        ctx.cancel_event.set()
    return ctx, emitted


def _run(
    *,
    responses: Sequence[Any],
    transcript: MemoryTranscript | None = None,
    registry: ToolRegistry | None = None,
    middlewares: Sequence[Middleware] = (),
    spec: Any = None,
    interrupted_now: Any = None,
    cancel: bool = False,
) -> tuple[RunOutcome, MemoryTranscript, _FakeModel, list[tuple[str, Any]], RunContext]:
    store = transcript or MemoryTranscript()
    model = _FakeModel(responses)
    ctx, emitted = _context(spec=spec, interrupted_now=interrupted_now, cancel=cancel)
    loop = AgentLoop(
        chain=MiddlewareChain(middlewares),
        transcript=store,
        call_model=model,
        tool_schemas=lambda run: [],
        registry=registry or ToolRegistry(),
    )
    outcome = asyncio.run(loop.run(ctx))
    return outcome, store, model, emitted, ctx


def test_text_only_run_persists_the_answer_and_emits_message_end():
    outcome, transcript, model, emitted, _ctx = _run(
        responses=[
            _response(
                text="你好",
                reasoning="想想",
                prompt_tokens=19,
                completion_tokens=3,
                finish_reason=None,
                latency_ms=5,
            )
        ]
    )

    assert transcript.messages == [{"role": "assistant", "content": "你好"}]
    assert emitted == [
        ("message_end", {"session_id": "session-1", "finish_reason": "stop"})
    ]
    assert (outcome.status, outcome.finish_reason, outcome.final_text) == (
        "succeeded",
        "stop",
        "你好",
    )
    assert (outcome.usage.prompt_tokens, outcome.usage.completion_tokens) == (19, 3)
    assert model.requests[0].messages == []
    assert model.requests[0].tools == []


def test_assistant_is_persisted_before_dispatch_and_tool_messages_follow_call_order():
    first = _call("call-1", "first_tool", {"x": 1}, index=0)
    second = _call("call-2", "second_tool", {"y": 2}, index=1)
    registry = _registry(_tool("first_tool"), _tool("second_tool"))
    transcript = MemoryTranscript()
    dispatch = _Dispatch(transcript, audit_ids={"call-1": 11, "call-2": 22})

    outcome, _store, model, emitted, _ctx = _run(
        responses=[
            _response(
                text="先跑两个", finish_reason="tool_calls", calls=[first, second]
            ),
            _response(text="都跑完了"),
        ],
        transcript=transcript,
        registry=registry,
        middlewares=[dispatch],
    )

    # 派发每一个调用时，assistant（带 tool_calls）已经先落库。
    assert dispatch.history_at_dispatch[0] == [
        {"role": "assistant", "content": "先跑两个", "tool_calls": [first, second]}
    ]
    assert [call.id for call in dispatch.calls] == ["call-1", "call-2"]
    # tool 消息按调用顺序排列，正文是 ``{"result": ...}``。
    assert [message["role"] for message in transcript.messages] == [
        "assistant",
        "tool",
        "tool",
        "assistant",
    ]
    assert [message["tool_call_id"] for message in transcript.messages[1:3]] == [
        "call-1",
        "call-2",
    ]
    assert json.loads(transcript.messages[1]["content"]) == {
        "result": {"tool": "first_tool", "arguments": {"x": 1}}
    }
    assert transcript.messages[-1] == {"role": "assistant", "content": "都跑完了"}

    assert [name for name, _data in emitted] == [
        "tool_start",
        "tool_start",
        "tool_result",
        "tool_result",
        "message_end",
    ]
    assert list(emitted[2][1]) == [
        "tool_call_id",
        "name",
        "message_id",
        "audit_id",
        "result",
    ]
    assert emitted[2][1] == {
        "tool_call_id": "call-1",
        "name": "first_tool",
        "message_id": None,
        "audit_id": 11,
        "result": {"tool": "first_tool", "arguments": {"x": 1}},
    }
    assert emitted[4] == (
        "message_end",
        {"session_id": "session-1", "finish_reason": "stop"},
    )
    # 第二轮请求带上了刚落的 assistant 与两条 tool 消息。
    assert [message["role"] for message in model.requests[1].messages] == [
        "assistant",
        "tool",
        "tool",
    ]
    assert (outcome.status, outcome.finish_reason) == ("succeeded", "stop")


def _unanswered(text: str) -> ToolOutcome:
    return ToolOutcome(
        status="ok",
        result={"status": "skipped"},
        stop_run=True,
        stop_reason="question_unanswered",
        stop_text=text,
    )


def _confirmation(text: str = CONFIRMATION_TERMINAL_TEXT) -> ToolOutcome:
    return ToolOutcome(
        status="rejected",
        error={"code": ErrorCode.CONFIRMATION_REJECTED.value, "message": "拒绝"},
        stop_run=True,
        stop_reason="confirmation_resolved",
        stop_text=text,
    )


def _other(reason: str, text: str) -> ToolOutcome:
    return ToolOutcome(
        status="ok", result={}, stop_run=True, stop_reason=reason, stop_text=text
    )


@pytest.mark.parametrize(
    ("outcomes", "pending", "expected"),
    [
        (
            [_unanswered(QUESTION_SKIPPED_TEXT), _confirmation(), _other("x", "x"), _unanswered(QUESTION_EXPIRED_TEXT)],
            None,
            ("question_unanswered", QUESTION_EXPIRED_TEXT),
        ),
        (
            [_confirmation("first"), _other("x", "x"), _confirmation("second")],
            None,
            ("confirmation_resolved", "first"),
        ),
        (
            [_other("first_stop", "甲"), _other("second_stop", "乙")],
            None,
            ("first_stop", "甲"),
        ),
        ([], ("stuck_loop", STUCK_LOOP_TEXT), ("stuck_loop", STUCK_LOOP_TEXT)),
        ([_confirmation()], ("stuck_loop", STUCK_LOOP_TEXT), ("confirmation_resolved", CONFIRMATION_TERMINAL_TEXT)),
        ([], None, None),
    ],
)
def test_stop_precedence(
    outcomes: list[ToolOutcome],
    pending: tuple[str, str | None] | None,
    expected: tuple[str, str | None] | None,
):
    # 最后一个没答成的提问 → 第一个确认终态 → 第一个其他 stop_run → pending_stop。
    assert select_stop(outcomes, pending) == expected


def test_loop_guard_stops_after_the_third_identical_batch_is_executed():
    registry = _registry(_tool("lookup", read_only=True))
    dispatch = _Dispatch()
    responses = [
        _response(
            finish_reason="tool_calls",
            calls=[_call(f"call-{index}", "lookup", {"key": "same"})],
        )
        for index in range(1, 6)
    ]

    outcome, transcript, model, emitted, _ctx = _run(
        responses=responses,
        registry=registry,
        middlewares=[LoopGuardMiddleware(), dispatch],
    )

    assert (outcome.status, outcome.finish_reason, outcome.final_text) == (
        "succeeded",
        "stuck_loop",
        STUCK_LOOP_TEXT,
    )
    # 第三批照常执行、落库，然后才收尾：模型只被请求三次。
    assert [call.id for call in dispatch.calls] == ["call-1", "call-2", "call-3"]
    assert len(model.requests) == 3
    assert transcript.messages[-1] == {
        "role": "assistant",
        "content": STUCK_LOOP_TEXT,
    }
    assert emitted[-2:] == [
        ("text_delta", {"text": STUCK_LOOP_TEXT}),
        ("message_end", {"session_id": "session-1", "finish_reason": "stuck_loop"}),
    ]


def test_stop_run_from_after_model_finishes_without_persisting():
    outcome, transcript, model, emitted, _ctx = _run(
        responses=[_response(text="不会落库")],
        middlewares=[_Stopper("context_overflow")],
    )

    assert transcript.messages == []
    assert len(model.requests) == 1
    assert (outcome.status, outcome.finish_reason, outcome.final_text) == (
        "succeeded",
        "context_overflow",
        None,
    )
    assert emitted == [
        ("message_end", {"session_id": "session-1", "finish_reason": "context_overflow"})
    ]


def test_max_steps_finishes_after_the_batch():
    spec = builtin_spec("legacy_chat").model_copy(update={"max_steps": 1})
    registry = _registry(_tool("lookup"))
    dispatch = _Dispatch()
    outcome, transcript, model, emitted, ctx = _run(
        responses=[
            _response(
                finish_reason="tool_calls",
                calls=[_call("call-1", "lookup", {"key": "a"})],
            ),
            _response(text="不应该再问一次"),
        ],
        registry=registry,
        middlewares=[dispatch],
        spec=spec,
    )

    assert (outcome.status, outcome.finish_reason, outcome.final_text) == (
        "succeeded",
        "max_steps",
        None,
    )
    assert len(model.requests) == 1
    assert ctx.steps == 1
    assert [message["role"] for message in transcript.messages] == [
        "assistant",
        "tool",
    ]
    # 步数用尽只落最后一批，不再补 message_end。
    assert [name for name, _data in emitted] == ["tool_start", "tool_result"]


def test_cancel_event_finishes_as_interrupted():
    outcome, transcript, model, emitted, _ctx = _run(
        responses=[_response(text="不会调用")],
        cancel=True,
    )

    assert model.requests == []
    assert (outcome.status, outcome.finish_reason) == ("interrupted", "interrupted")
    assert transcript.messages == [
        {"role": "assistant", "content": INTERRUPTED_NOTICE}
    ]
    assert emitted == [
        (
            "interrupted",
            {"session_id": "session-1", "finish_reason": INTERRUPTED_FINISH_REASON},
        ),
        (
            "message_end",
            {"session_id": "session-1", "finish_reason": INTERRUPTED_FINISH_REASON},
        ),
    ]


def test_interrupt_writes_stubs_then_the_interrupted_message():
    registry = _registry(_tool("lookup"), _tool("lookup_again"))
    transcript = MemoryTranscript()
    dispatch = _Dispatch(cancel_on="lookup")
    calls = [
        _call("call-1", "lookup", {"key": "a"}, index=0),
        _call("call-2", "lookup_again", {"key": "b"}, index=1),
    ]
    ctx, emitted = _context(interrupted_now=lambda: True)
    loop = AgentLoop(
        chain=MiddlewareChain([dispatch]),
        transcript=transcript,
        call_model=_FakeModel([_response(text="先看一下", finish_reason="tool_calls", calls=calls)]),
        tool_schemas=lambda run: [],
        registry=registry,
    )

    with pytest.raises(asyncio.CancelledError):
        asyncio.run(loop.run(ctx))

    assert [message["role"] for message in transcript.messages] == [
        "assistant",
        "tool",
        "tool",
        "assistant",
    ]
    assert [message.get("tool_call_id") for message in transcript.messages[1:3]] == [
        "call-1",
        "call-2",
    ]
    for message in transcript.messages[1:3]:
        assert json.loads(message["content"]) == INTERRUPTED_TOOL_STUB
    assert transcript.messages[3] == {
        "role": "assistant",
        "content": f"先看一下\n\n{INTERRUPTED_NOTICE}",
    }
    # 补桩、落中断消息之后才发事件，然后重新抛出取消。
    assert [name for name, _data in emitted][-2:] == ["interrupted", "message_end"]
    assert emitted[-1] == (
        "message_end",
        {"session_id": "session-1", "finish_reason": INTERRUPTED_FINISH_REASON},
    )


def test_non_interrupt_cancellation_propagates_without_writes():
    transcript = MemoryTranscript()
    ctx, emitted = _context()
    loop = AgentLoop(
        chain=MiddlewareChain([]),
        transcript=transcript,
        call_model=_FakeModel([asyncio.CancelledError()]),
        tool_schemas=lambda run: [],
        registry=ToolRegistry(),
    )

    with pytest.raises(asyncio.CancelledError):
        asyncio.run(loop.run(ctx))

    assert transcript.messages == []
    assert emitted == []


def test_invalid_tool_call_persists_plain_assistant_then_raises():
    invalid = [
        {
            "index": 0,
            "id": "",
            "type": "function",
            "function": {"name": "lookup", "arguments": "{}"},
        }
    ]

    def run_once(text: str) -> tuple[MemoryTranscript, AppError]:
        transcript = MemoryTranscript()
        ctx, _emitted = _context()
        loop = AgentLoop(
            chain=MiddlewareChain([]),
            transcript=transcript,
            call_model=_FakeModel(
                [
                    _response(
                        text=text,
                        reasoning="想一下",
                        prompt_tokens=5,
                        completion_tokens=6,
                        finish_reason="tool_calls",
                        calls=invalid,
                    )
                ]
            ),
            tool_schemas=lambda run: [],
            registry=ToolRegistry(),
        )
        with pytest.raises(AppError) as caught:
            asyncio.run(loop.run(ctx))
        return transcript, caught.value

    transcript, error = run_once("我先查一下")
    assert error.code == ErrorCode.LLM_REQUEST_FAILED
    assert transcript.messages == [{"role": "assistant", "content": "我先查一下"}]
    assert "tool_calls" not in transcript.messages[0]

    # 模型没有给文本时，assistant 正文用固定的提示语。
    transcript, _error = run_once("")
    assert transcript.messages == [
        {"role": "assistant", "content": INVALID_TOOL_CALL_TEXT}
    ]


def test_bad_arguments_write_an_error_reply_for_every_call_then_raise():
    registry = _registry(_tool("lookup"), _tool("lookup_again"))
    dispatch = _Dispatch()
    calls = [
        _call("call-1", "lookup", "{not json", index=0),
        _call("call-2", "lookup_again", "[1, 2]", index=1),
    ]
    transcript = MemoryTranscript()
    ctx, emitted = _context()
    loop = AgentLoop(
        chain=MiddlewareChain([dispatch]),
        transcript=transcript,
        call_model=_FakeModel(
            [
                _response(
                    text="我先查", finish_reason="tool_calls", calls=calls
                )
            ]
        ),
        tool_schemas=lambda run: [],
        registry=registry,
    )

    with pytest.raises(AppError) as caught:
        asyncio.run(loop.run(ctx))

    assert caught.value.code == ErrorCode.LLM_REQUEST_FAILED
    # 整批参数先解析：坏参数不派发任何调用。
    assert dispatch.calls == []
    assert [message["role"] for message in transcript.messages] == [
        "assistant",
        "tool",
        "tool",
    ]
    assert [message["tool_call_id"] for message in transcript.messages[1:]] == [
        "call-1",
        "call-2",
    ]
    for message in transcript.messages[1:]:
        assert json.loads(message["content"])["error"]["code"] == (
            ErrorCode.LLM_REQUEST_FAILED.value
        )
    # 错误路径的 tool_result 没有 audit_id 键。
    assert [name for name, _data in emitted] == ["tool_result", "tool_result"]
    for _name, data in emitted:
        assert list(data) == ["tool_call_id", "name", "message_id", "error"]


def test_on_run_end_runs_on_success_and_failure():
    recorder = _Recorder()

    outcome, _transcript, _model, _emitted, _ctx = _run(
        responses=[_response(text="好了")],
        middlewares=[recorder],
    )
    assert (outcome.status, outcome.finish_reason) == ("succeeded", "stop")

    ctx, _emitted = _context()
    loop = AgentLoop(
        chain=MiddlewareChain([recorder]),
        transcript=MemoryTranscript(),
        call_model=_FakeModel([AppError(ErrorCode.LLM_REQUEST_FAILED, "上游炸了")]),
        tool_schemas=lambda run: [],
        registry=ToolRegistry(),
    )
    with pytest.raises(AppError):
        asyncio.run(loop.run(ctx))

    assert [(item.status, item.finish_reason) for item in recorder.outcomes] == [
        ("succeeded", "stop"),
        ("failed", "error"),
    ]
