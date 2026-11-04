"""M65 T5：流式 chunk 解析与一次模型调用的完整收流（规格 §4.2.7）。"""

from __future__ import annotations

import asyncio
from typing import Any

import pytest

from maa_api.agent.harness.chunks import (
    collect_completion,
    parse_arguments,
    parse_tool_call,
)
from maa_api.agent.harness.context import ToolCall, TurnDraft, Usage
from maa_api.domain.errors import AppError, ErrorCode


class _StreamingClient:
    """只实现 ``stream_chat_completion`` 的最小假客户端。"""

    def __init__(self, *chunks: Any) -> None:
        self.chunks = list(chunks)
        self.requests: list[tuple[str, list[dict[str, Any]], list[dict[str, Any]]]] = []

    async def stream_chat_completion(self, model, messages, tools):
        self.requests.append((model, list(messages), list(tools)))
        for chunk in self.chunks:
            yield chunk


def test_collect_completion_streams_text_and_fills_the_draft():
    emitted: list[tuple[str, dict[str, Any]]] = []

    async def emit(name, data):
        emitted.append((name, data))

    client = _StreamingClient(
        {"choices": [{"index": 0, "delta": {"content": "你"}, "finish_reason": None}]},
        {
            "choices": [
                {
                    "index": 0,
                    "delta": {"content": "好", "reasoning_content": "想一下"},
                    "finish_reason": None,
                }
            ]
        },
        {
            "choices": [{"index": 0, "delta": {}, "finish_reason": "stop"}],
            "usage": {"prompt_tokens": 7, "completion_tokens": 2},
        },
    )
    draft = TurnDraft()
    messages = [{"role": "user", "content": "在吗"}]
    tools = [{"type": "function", "function": {"name": "ping"}}]

    resp = asyncio.run(
        collect_completion(client, "m", messages, tools, emit=emit, draft=draft)
    )

    # 请求原样转给客户端。
    assert client.requests == [("m", messages, tools)]
    # 每段文本一条 text_delta，顺序与分片一致。
    assert emitted == [
        ("text_delta", {"text": "你"}),
        ("text_delta", {"text": "好"}),
    ]
    # 文本与思考直接攒在 draft 上，返回值与它是同一份内容。
    assert draft.text == ["你", "好"]
    assert draft.reasoning == ["想一下"]
    assert resp.text == "".join(draft.text) == "你好"
    assert resp.reasoning == "".join(draft.reasoning) == "想一下"
    assert resp.usage == Usage(prompt_tokens=7, completion_tokens=2)
    assert resp.upstream_finish_reason == "stop"
    assert isinstance(resp.latency_ms, int)
    assert resp.latency_ms >= 0
    assert resp.raw_tool_calls == []


def test_collect_completion_keeps_only_non_negative_usage():
    async def emit(_name, _data):
        return None

    client = _StreamingClient(
        {
            "choices": [{"index": 0, "delta": {"content": "a"}, "finish_reason": None}],
            "usage": {"prompt_tokens": 5, "completion_tokens": 4},
        },
        {
            "choices": [{"index": 0, "delta": {}, "finish_reason": None}],
            "usage": {"prompt_tokens": -1, "completion_tokens": "3"},
        },
        {
            "choices": [{"index": 0, "delta": {}, "finish_reason": "stop"}],
            "usage": {"prompt_tokens": 2.5, "completion_tokens": -7},
        },
    )

    resp = asyncio.run(collect_completion(client, "m", [], [], emit=emit))

    # 后两块都是负数或非整数：一个都不收，前一块的合法值保持不变。
    assert resp.usage == Usage(prompt_tokens=5, completion_tokens=4)


def test_collect_completion_rejects_a_non_mapping_chunk():
    async def emit(_name, _data):
        return None

    client = _StreamingClient(
        {"choices": [{"index": 0, "delta": {"content": "ok"}, "finish_reason": None}]},
        "not-a-chunk",
    )
    draft = TurnDraft()

    with pytest.raises(AppError) as caught:
        asyncio.run(
            collect_completion(client, "m", [], [], emit=emit, draft=draft)
        )

    assert caught.value.code is ErrorCode.LLM_REQUEST_FAILED
    assert caught.value.message == "LLM 返回了无效的响应块"
    # 报错前已经流出去的文本仍在 draft 上：收尾要靠它补历史。
    assert draft.text == ["ok"]


def test_parse_tool_call_requires_a_name_and_an_id():
    with pytest.raises(AppError) as caught:
        parse_tool_call({"id": "call-1", "function": {"arguments": "{}"}})
    assert caught.value.code is ErrorCode.LLM_REQUEST_FAILED
    assert caught.value.message == "LLM 返回了无效的工具调用"

    with pytest.raises(AppError) as caught:
        parse_tool_call({"id": "", "function": {"name": "lookup"}})
    assert caught.value.code is ErrorCode.LLM_REQUEST_FAILED
    assert caught.value.message == "LLM 返回的工具调用缺少 id"

    # 合法调用解析成 ToolCall：参数原文原样保留，缺省参数按 v2 是 "{}"。
    assert parse_tool_call(
        {"id": "call-2", "function": {"name": "lookup", "arguments": '{"key": "a"}'}}
    ) == ToolCall(id="call-2", name="lookup", raw_arguments='{"key": "a"}')
    assert parse_tool_call({"id": "call-3", "function": {"name": "lookup"}}) == ToolCall(
        id="call-3", name="lookup", raw_arguments="{}"
    )


def test_parse_arguments_rejects_invalid_json_and_non_objects():
    with pytest.raises(AppError) as caught:
        parse_arguments('{"broken":')
    assert caught.value.code is ErrorCode.LLM_REQUEST_FAILED
    assert caught.value.message == "LLM 返回了无效的工具参数 JSON"

    with pytest.raises(AppError) as caught:
        parse_arguments('["not", "an", "object"]')
    assert caught.value.code is ErrorCode.LLM_REQUEST_FAILED
    assert caught.value.message == "LLM 工具参数必须是 JSON 对象"

    # 字符串与已经是对象的值都接受（v2 原样）。
    assert parse_arguments('{"key": "a"}') == {"key": "a"}
    assert parse_arguments({"key": "a"}) == {"key": "a"}


def test_v3_text_delta_carries_run_id_but_v2_stays_unchanged():
    """M72 §4.5.1：v3 的每个 SSE 事件都带 run_id（04 §3）；v2 不传时保持原形状。"""

    def collect(*, run_id: str | None) -> list[tuple[str, dict[str, Any]]]:
        emitted: list[tuple[str, dict[str, Any]]] = []

        async def emit(name, data):
            emitted.append((name, data))

        client = _StreamingClient(
            {"choices": [{"index": 0, "delta": {"content": "好"}, "finish_reason": "stop"}]}
        )

        async def scenario() -> None:
            await collect_completion(
                client,
                "m",
                [{"role": "user", "content": "x"}],
                [],
                emit=emit,
                run_id=run_id,
            )

        asyncio.run(scenario())
        return emitted

    assert collect(run_id="r-1") == [("text_delta", {"text": "好", "run_id": "r-1"})]
    assert collect(run_id=None) == [("text_delta", {"text": "好"})]
