"""M65 T4：运行事件总线与 v2 SSE 帧（规格 §4.2.6）。"""

from __future__ import annotations

import asyncio

from maa_api.agent.harness.context import RunContext
from maa_api.agent.harness.events import RunEvent, RunEventBus, sse_event
from maa_api.agent.specs.registry import builtin_spec
from maa_api.domain.enums import CallerType


def _context() -> RunContext:
    return RunContext(
        run_id="run-1",
        root_run_id="run-1",
        parent_run_id=None,
        session_id="session-1",
        ticket_id=None,
        spec=builtin_spec("legacy_chat"),
    )


def test_bus_forwards_events_to_every_sink_in_order():
    order: list[str] = []
    first: list[RunEvent] = []
    second: list[RunEvent] = []
    payload = {"tool_call_id": "call-1", "name": "lookup", "result": {"ok": True}}

    async def sink_a(event: RunEvent) -> None:
        order.append("a")
        first.append(event)

    async def sink_b(event: RunEvent) -> None:
        order.append("b")
        second.append(event)

    bus = RunEventBus(run_id="run-1")
    bus.subscribe(sink_a)
    bus.subscribe(sink_b)
    asyncio.run(bus.emit("tool_result", payload))

    # 两个 sink 顺序收到同一个 RunEvent。
    assert order == ["a", "b"]
    assert len(first) == len(second) == 1
    assert first[0] is second[0]
    assert first[0] == RunEvent(name="tool_result", data=payload, run_id="run-1")
    assert first[0].data is payload

    asyncio.run(bus.emit("message_end", {"finish_reason": "stop"}))
    assert [event.name for event in first] == ["tool_result", "message_end"]
    assert [event.name for event in second] == ["tool_result", "message_end"]


def test_sse_event_frame_is_byte_identical_to_v2():
    assert (
        sse_event("tool_result", {"api_key": "x", "n": 1, "text": "中文"})
        == 'event: tool_result\ndata: {"api_key":"[REDACTED]","n":1,"text":"中文"}\n\n'
    )


def test_run_context_defaults_match_the_contract():
    ctx = _context()

    assert ctx.caller is CallerType.INTERNAL
    assert ctx.steps == 0
    assert ctx.vision_enabled is True
    assert ctx.parallel_tools is True
    assert ctx.interrupted_now() is False
    assert ctx.allowed_tools is None
    assert ctx.pending_stop is None
    assert ctx.cancel_event.is_set() is False

    # 默认 emit 是丢弃：不抛、不产生副作用。
    assert asyncio.run(ctx.emit("text_delta", {"text": "x"})) is None

    # 草稿默认空，且可以原地清空。
    assert (ctx.draft.text, ctx.draft.reasoning, ctx.draft.pending_tool_calls) == ([], [], [])
    ctx.draft.text.append("半句")
    ctx.draft.reset()
    assert ctx.draft.text == []

    ctx.stop_after_batch("max_steps", "步数用尽")
    assert ctx.pending_stop == ("max_steps", "步数用尽")

    ctx.stop_after_batch("question_unanswered")
    assert ctx.pending_stop == ("question_unanswered", None)
