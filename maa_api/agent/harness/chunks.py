"""流式 chunk 解析与一次模型调用的完整收流（规格 §4.2.7）。

``merge_tool_call_deltas``、``chunk_tool_calls`` 是 v2 ``runtime`` 的原样搬运；
``parse_tool_call`` / ``parse_arguments`` 校验模型给的调用与参数原文（v2
``_parse_tool_call`` / ``_parse_arguments``）；``collect_completion`` 是 v2
``AgentRuntime._complete`` 的函数版：逐块转发 ``text_delta``，把文本与思考直接攒进
``draft``（中断就发生在这个循环里），返回 :class:`ModelResponse`。
"""

from __future__ import annotations

import json
import time
from collections.abc import Mapping, Sequence
from typing import Any

from maa_api.agent.harness.context import EmitFn, ModelResponse, ToolCall, TurnDraft
from maa_api.agent.llm.usage import normalize_usage
from maa_api.domain.errors import AppError, ErrorCode

__all__ = [
    "chunk_tool_calls",
    "collect_completion",
    "merge_tool_call_deltas",
    "parse_arguments",
    "parse_tool_call",
]

#: usage 里直接取用的顶层 token 计数。
_USAGE_SCALAR_FIELDS = (
    "prompt_tokens",
    "completion_tokens",
    "cached_tokens",
    "prompt_cache_hit_tokens",
    "prompt_cache_miss_tokens",
)
#: usage 的嵌套 details：``{外层键: 内层字段}``。
_USAGE_DETAIL_FIELDS = (
    ("prompt_tokens_details", "cached_tokens"),
    ("completion_tokens_details", "reasoning_tokens"),
)


def _is_token_count(value: Any) -> bool:
    """非负 ``int``（排除 ``bool``）才算一个可收的 token 计数。"""
    return isinstance(value, int) and not isinstance(value, bool) and value >= 0


def _merge_usage(target: dict[str, Any], source: Mapping[str, Any]) -> None:
    """把一块 usage 的合法计数并进 ``target``，保留每个字段最后一个合法值。

    v2 的收流口径是「后一块给了非法值时不覆盖前一块的合法值」；整块取最后一个带
    usage 的映射做不到这一点（M65 ``test_harness_chunks`` 钉着），所以按字段合并，
    再交给 :func:`normalize_usage` 统一归一。
    """
    for key in _USAGE_SCALAR_FIELDS:
        value = source.get(key)
        if _is_token_count(value):
            target[key] = value
    for key, field in _USAGE_DETAIL_FIELDS:
        details = source.get(key)
        if not isinstance(details, Mapping):
            continue
        value = details.get(field)
        if _is_token_count(value):
            target.setdefault(key, {})[field] = value


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


def parse_tool_call(call: Mapping[str, Any]) -> ToolCall:
    """把一条 OpenAI 形态的 ``tool_calls`` 条目校验成 :class:`ToolCall`。

    校验与报错与 v2 ``_parse_tool_call`` 一字不改：模型给的参数原文先原样保留，
    ``execute_batch`` 派发前才解析（v2 先落 assistant 消息再解析参数）。
    """
    function = call.get("function")
    if not isinstance(function, Mapping) or not isinstance(function.get("name"), str):
        raise AppError(ErrorCode.LLM_REQUEST_FAILED, "LLM 返回了无效的工具调用")
    call_id = call.get("id")
    if not isinstance(call_id, str) or not call_id:
        raise AppError(ErrorCode.LLM_REQUEST_FAILED, "LLM 返回的工具调用缺少 id")
    return ToolCall(
        id=call_id,
        name=function["name"],
        raw_arguments=function.get("arguments", "{}"),
    )


def parse_arguments(value: Any) -> dict[str, Any]:
    """把模型给的参数原文解析成对象；坏 JSON 或非对象抛 ``LLM_REQUEST_FAILED``。"""
    try:
        arguments = json.loads(value) if isinstance(value, str) else value
    except (TypeError, ValueError):
        raise AppError(ErrorCode.LLM_REQUEST_FAILED, "LLM 返回了无效的工具参数 JSON") from None
    if not isinstance(arguments, dict):
        raise AppError(ErrorCode.LLM_REQUEST_FAILED, "LLM 工具参数必须是 JSON 对象")
    return arguments


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
    raw_usage: dict[str, Any] = {}
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
            _merge_usage(raw_usage, chunk_usage)
    return ModelResponse(
        text="".join(text_parts),
        reasoning="".join(reasoning_parts),
        usage=normalize_usage(raw_usage),
        upstream_finish_reason=finish_reason,
        latency_ms=int((time.monotonic() - started) * 1000),
        raw_tool_calls=merge_tool_call_deltas(fragments),
    )
