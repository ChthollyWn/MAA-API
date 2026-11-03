"""M65 T6：上下文组装中间件与 ``build_model_context`` 等价（规格 §4.2.9）。"""

from __future__ import annotations

import asyncio
import json
from typing import Any

from maa_api.agent.harness.builtin_middleware import (
    ContextLayoutMiddleware,
    build_model_context,
)
from maa_api.agent.harness.context import ModelRequest, RunContext
from maa_api.agent.specs.registry import builtin_spec

_KNOWLEDGE = "知识库：这是上下文组装用例的固定指引。"
_STATUS = {"now": "2026-01-01T00:00:00+00:00", "sessions": 2, "pending": []}
_TOOLS: list[dict[str, Any]] = [
    {
        "type": "function",
        "function": {
            "name": "lookup",
            "description": "查一下",
            "parameters": {"type": "object", "properties": {}},
        },
    }
]


def _history() -> list[dict[str, Any]]:
    return [
        {"role": "user", "content": "帮我看一下这个任务"},
        {
            "role": "assistant",
            "content": "我先查一下",
            "reasoning_content": "先看日志再决定",
            "tool_calls": [
                {
                    "id": "call-1",
                    "type": "function",
                    "function": {"name": "lookup", "arguments": '{"q": "日志"}'},
                }
            ],
        },
        {
            "role": "tool",
            "tool_call_id": "call-1",
            "tool_name": "lookup",
            "content": '{"result": {"lines": ["a", "b"]}}',
        },
    ]


def _context(*, max_context_chars: int | None) -> RunContext:
    ctx = RunContext(
        run_id="run-t6",
        root_run_id="run-t6",
        parent_run_id=None,
        session_id="session-t6",
        ticket_id=None,
        spec=builtin_spec("legacy_chat"),
        vision_enabled=True,
    )
    # 门面在 T8 每轮写一次；三个值就是 v2 ``_run_turn_body`` 直接传的那三个。
    ctx.state[ContextLayoutMiddleware.name] = {
        "knowledge_context": _KNOWLEDGE,
        "status_summary": _STATUS,
        "max_context_chars": max_context_chars,
    }
    return ctx


def test_context_layout_middleware_equals_build_model_context():
    ctx = _context(max_context_chars=60_000)
    history = _history()
    request = ModelRequest(messages=list(history), tools=list(_TOOLS))

    updated = asyncio.run(ContextLayoutMiddleware().before_model(ctx, request))

    expected = build_model_context(
        history,
        system_prompt=ctx.spec.body,
        knowledge_context=_KNOWLEDGE,
        status_summary=_STATUS,
        max_context_chars=60_000,
        envelope_chars=len(json.dumps(_TOOLS, ensure_ascii=False)),
        vision_enabled=ctx.vision_enabled,
    )
    # 中间件就地换掉 req.messages，内容与直接调用 ``build_model_context`` 完全一致。
    assert updated is request
    assert updated.messages == expected
    assert [message["role"] for message in updated.messages] == [
        "system",
        "user",
        "assistant",
        "tool",
    ]


def test_context_layout_uses_the_spec_body_as_system_prompt():
    ctx = _context(max_context_chars=None)
    request = ModelRequest(messages=_history(), tools=list(_TOOLS))

    updated = asyncio.run(ContextLayoutMiddleware().before_model(ctx, request))

    system = updated.messages[0]
    assert system["role"] == "system"
    # v2 的系统提示词就是 legacy_chat 定义的正文；知识库段与状态摘要接在它后面。
    assert system["content"].startswith(builtin_spec("legacy_chat").body)
    assert _KNOWLEDGE in system["content"]
