"""M70 T3：运行时接缝（``CURRENT_RUN``、``run_start``、取消原因、``message_persisted``）。

全部用内存 transcript 与假模型：这里钉的是总控 / Worker 运行依赖的四个接缝，
不碰数据库，也不改 M65 的 v2 事件名。
"""

from __future__ import annotations

import asyncio
from collections.abc import Sequence
from types import SimpleNamespace
from typing import Any

import pytest
from pydantic import BaseModel, ConfigDict

from maa_api.agent.harness.context import (
    CURRENT_RUN,
    ModelResponse,
    RunContext,
    RunOutcome,
    ToolCall,
    ToolOutcome,
    Usage,
    require_run,
)
from maa_api.agent.harness.events import (
    SSE_V3_EVENTS,
    RunEvent,
    RunEventBus,
)
from maa_api.agent.harness.loop import CANCELLED_FINISH_REASON, AgentLoop, MemoryTranscript
from maa_api.agent.harness.middleware import CallNext, Middleware, MiddlewareChain
from maa_api.agent.harness.toolexec import execute_batch
from maa_api.agent.registry import ToolDefinition, ToolRegistry, ToolRisk
from maa_api.agent.specs.registry import builtin_spec
from maa_api.domain.errors import AppError, ErrorCode


class _EmptyParams(BaseModel):
    model_config = ConfigDict(extra="forbid")


async def _unused_handler(_params: BaseModel, _context: Any) -> dict[str, Any]:
    return {}


def _tool(name: str, *, read_only: bool = True) -> ToolDefinition:
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


def _response(*, text: str = "", finish_reason: str | None = None) -> ModelResponse:
    return ModelResponse(
        text=text,
        reasoning="",
        usage=Usage(prompt_tokens=11, completion_tokens=6),
        upstream_finish_reason=finish_reason,
        latency_ms=1,
    )


class _FakeModel:
    def __init__(self, responses: Sequence[ModelResponse]) -> None:
        self.responses = list(responses)
        self.requests = 0

    async def __call__(self, _ctx: RunContext, _req: Any) -> ModelResponse:
        self.requests += 1
        response = self.responses.pop(0)
        if response.text:
            _ctx.draft.text.append(response.text)
        return response


def _run(
    *,
    spec_name: str = "legacy_chat",
    run_kind: str | None = "worker_turn",
    inbox_items: Sequence[Any] = (),
    cancel: bool = False,
    cancel_reason: str = "interrupted",
) -> tuple[RunOutcome, MemoryTranscript, _FakeModel, list[RunEvent], RunContext]:
    events: list[RunEvent] = []
    bus = RunEventBus(run_id="run-1")

    async def sink(event: RunEvent) -> None:
        events.append(event)

    bus.subscribe(sink)
    ctx = RunContext(
        run_id="run-1",
        root_run_id="run-1",
        parent_run_id=None,
        session_id="session-1",
        ticket_id="ticket-1",
        spec=builtin_spec(spec_name),
        emit=bus.emit,
        run=None if run_kind is None else SimpleNamespace(kind=run_kind),
        inbox_items=list(inbox_items),
        cancel_reason=lambda: cancel_reason,
    )
    if cancel:
        ctx.cancel_event.set()
    model = _FakeModel([_response(text="回答")])
    transcript = MemoryTranscript()
    loop = AgentLoop(
        chain=MiddlewareChain([]),
        transcript=transcript,
        call_model=model,
        tool_schemas=lambda _ctx: [],
        registry=ToolRegistry(),
    )
    outcome = asyncio.run(loop.run(ctx))
    return outcome, transcript, model, events, ctx




def test_current_run_is_isolated_per_concurrent_handler() -> None:
    seen: list[tuple[str, str]] = []
    barrier = asyncio.Barrier(2)

    class _Probe(Middleware):
        name = "probe"

        async def wrap_tool(
            self, ctx: RunContext, call: ToolCall, call_next: CallNext
        ) -> ToolOutcome:
            await barrier.wait()
            run = require_run(kinds={"worker", "subagent"})
            seen.append((ctx.run_id, run.run_id))
            return ToolOutcome(status="ok", result={"run_id": run.run_id})

    async def scenario() -> None:
        chain = MiddlewareChain([_Probe()])
        registry = _registry(_tool("lookup"))
        call = ToolCall(id="call-1", name="lookup", raw_arguments="{}")
        first = RunContext(
            run_id="run-a",
            root_run_id="run-a",
            parent_run_id=None,
            session_id="session-a",
            ticket_id=None,
            spec=builtin_spec("legacy_chat"),
        )
        second = RunContext(
            run_id="run-b",
            root_run_id="run-b",
            parent_run_id=None,
            session_id="session-b",
            ticket_id=None,
            spec=builtin_spec("recon"),
        )
        outcomes = await asyncio.gather(
            execute_batch(first, [call], chain=chain, registry=registry),
            execute_batch(second, [call], chain=chain, registry=registry),
        )
        assert outcomes[0][0].result == {"run_id": "run-a"}
        assert outcomes[1][0].result == {"run_id": "run-b"}

    asyncio.run(scenario())
    assert sorted(seen) == [("run-a", "run-a"), ("run-b", "run-b")]
    # 批结束之后 ContextVar 复位，不会泄漏到下一个工具。
    assert CURRENT_RUN.get() is None


def test_require_run_rejects_wrong_kind_and_missing_run() -> None:
    ctx = RunContext(
        run_id="run-1",
        root_run_id="run-1",
        parent_run_id=None,
        session_id="session-1",
        ticket_id=None,
        spec=builtin_spec("recon"),
    )
    with pytest.raises(AppError) as exc_info:
        require_run(kinds={"orchestrator"})
    assert exc_info.value.code is ErrorCode.INVALID_PARAMETER

    token = CURRENT_RUN.set(ctx)
    try:
        assert require_run(kinds={"subagent"}).run_id == "run-1"
        with pytest.raises(AppError) as exc_info:
            require_run(kinds={"worker"})
        assert exc_info.value.code is ErrorCode.INVALID_PARAMETER
    finally:
        CURRENT_RUN.reset(token)


def test_run_start_carries_consumed_inbox_ids() -> None:
    outcome, _transcript, _model, events, _ctx = _run(
        inbox_items=[SimpleNamespace(id=12), SimpleNamespace(id=13)]
    )

    assert outcome.finish_reason == "stop"
    assert events[0].name == "run_start"
    assert events[0].data == {
        "run_id": "run-1",
        "parent_run_id": None,
        "session_id": "session-1",
        "ticket_id": "ticket-1",
        "agent": "legacy_chat",
        "kind": "worker_turn",
        "consumed_inbox_ids": [12, 13],
    }


def test_cancel_reason_cancelled_finishes_run_as_cancelled() -> None:
    outcome, transcript, model, events, _ctx = _run(
        cancel=True, cancel_reason=CANCELLED_FINISH_REASON
    )

    assert (outcome.status, outcome.finish_reason) == ("cancelled", "cancelled")
    assert model.requests == 0
    assert transcript.messages == []
    # v3 运行照常发 run_end，流据此关闭。
    assert events[-1].name == "run_end"
    assert events[-1].data["finish_reason"] == "cancelled"


def test_message_persisted_is_on_bus_but_not_sse() -> None:
    _outcome, _transcript, _model, events, _ctx = _run()

    names = [event.name for event in events]
    assert "message_persisted" in names
    persisted = next(event for event in events if event.name == "message_persisted")
    assert persisted.data["session_id"] == "session-1"
    assert persisted.data["ticket_id"] == "ticket-1"
    assert persisted.data["agent"] == "legacy_chat"
    assert persisted.data["kind"] == "worker_turn"
    assert persisted.data["message"]["role"] == "assistant"
    assert persisted.data["message"]["preview"] == "回答"
    assert "message_persisted" not in SSE_V3_EVENTS
    assert "run_start" in SSE_V3_EVENTS
    assert "run_end" in SSE_V3_EVENTS
    # v2 运行（没有运行行）事件名与 M65 一样，不出现 v3 的内部事件。
    _outcome, _transcript, _model, v2_events, _ctx = _run(run_kind=None)
    assert [event.name for event in v2_events] == ["message_end"]
