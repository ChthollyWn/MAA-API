"""流式 chunk 解析与一次模型调用的完整收流（规格 §4.2.7）。

``merge_tool_call_deltas``、``chunk_tool_calls`` 是 v2 ``runtime`` 的原样搬运；
``collect_completion`` 是 v2 ``AgentRuntime._complete`` 的函数版：逐块转发
``text_delta``，把文本与思考直接攒进 ``draft``（中断就发生在这个循环里），返回
:class:`ModelResponse`。
"""

from __future__ import annotations

import time
from collections.abc import Mapping, Sequence
from typing import Any

from maa_api.agent.harness.context import EmitFn, ModelResponse, TurnDraft, Usage
from maa_api.domain.errors import AppError, ErrorCode

__all__ = ["chunk_tool_calls", "collect_completion", "merge_tool_call_deltas"]


def merge_tool_call_deltas(deltas: Sequence[Mapping[str, Any]]) -> list[dict[str, Any]]:
    """Join OpenAI tool-call name, id and argument fragments by tool-call index."""
    merged: dict[int, dict[str, Any]] = {}
    for fragment in deltas:
        try:
            index = int(fragment.get("index", 0))
        except (TypeError, ValueError):
            index = 0
        target = merged.setdefault(
            index,
            {"id": "", "type": "function", "function": {"name": "", "arguments": ""}},
        )
        call_id = fragment.get("id")
        if isinstance(call_id, str):
            target["id"] += call_id
        if isinstance(fragment.get("type"), str):
            target["type"] = fragment["type"]
        function = fragment.get("function")
        if isinstance(function, Mapping):
            name = function.get("name")
            arguments = function.get("arguments")
            if isinstance(name, str):
                target["function"]["name"] += name
            if isinstance(arguments, str):
                target["function"]["arguments"] += arguments
    return [merged[index] for index in sorted(merged)]


def chunk_tool_calls(chunk: Mapping[str, Any]) -> list[dict[str, Any]]:
    found: list[dict[str, Any]] = []
    for choice in chunk.get("choices") or []:
        if not isinstance(choice, Mapping):
            continue
        delta = choice.get("delta") or {}
        if isinstance(delta, Mapping):
            calls = delta.get("tool_calls") or []
            if isinstance(calls, list):
                found.extend(call for call in calls if isinstance(call, Mapping))
    return found


async def collect_completion(
    client: Any,
    model: str,
    messages: Sequence[dict[str, Any]],
    tools: Sequence[dict[str, Any]],
    *,
    emit: EmitFn,
    draft: TurnDraft | None = None,
) -> ModelResponse:
    """读完整条流式应答，返回合并后的 :class:`ModelResponse`。

    ``draft`` 在流式循环里被原地追加：中断收尾拿到的就是「已经发给前端、但还没
    落库」的那部分文本与思考，所以不能等本函数返回再回填。
    """
    fragments: list[dict[str, Any]] = []
    # 文本与思考内容直接攒在 draft 上（同一批 list）：中断就发生在这个 async for
    # 里面，等本函数返回再取文本已经来不及了（规格 §3.1）。
    text_parts: list[str] = draft.text if draft is not None else []
    reasoning_parts: list[str] = draft.reasoning if draft is not None else []
    usage: dict[str, int] = {}
    finish_reason: str | None = None
    started = time.monotonic()
    async for chunk in client.stream_chat_completion(model, messages, tools):
        if not isinstance(chunk, Mapping):
            raise AppError(ErrorCode.LLM_REQUEST_FAILED, "LLM 返回了无效的响应块")
        fragments.extend(chunk_tool_calls(chunk))
        for choice in chunk.get("choices") or []:
            if not isinstance(choice, Mapping):
                continue
            delta = choice.get("delta") or {}
            if isinstance(delta, Mapping):
                value = delta.get("content")
                if isinstance(value, str) and value:
                    text_parts.append(value)
                    await emit("text_delta", {"text": value})
                # 思考模式的思考内容：不展示给用户，但必须累积下来随消息持久化，
                # 否则下一轮回传缺失、上游以 400 拒绝（见 migration 0013）。
                thinking = delta.get("reasoning_content")
                if isinstance(thinking, str) and thinking:
                    reasoning_parts.append(thinking)
            reason = choice.get("finish_reason")
            if isinstance(reason, str):
                finish_reason = reason
        chunk_usage = chunk.get("usage")
        if isinstance(chunk_usage, Mapping):
            for key in ("prompt_tokens", "completion_tokens"):
                value = chunk_usage.get(key)
                if isinstance(value, int) and value >= 0:
                    usage[key] = value
    return ModelResponse(
        text="".join(text_parts),
        reasoning="".join(reasoning_parts),
        usage=Usage(
            prompt_tokens=usage.get("prompt_tokens"),
            completion_tokens=usage.get("completion_tokens"),
        ),
        upstream_finish_reason=finish_reason,
        latency_ms=int((time.monotonic() - started) * 1000),
        raw_tool_calls=merge_tool_call_deltas(fragments),
    )
