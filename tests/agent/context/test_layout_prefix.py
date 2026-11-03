"""M67 T7：连续请求的字节级前缀（规格 §4.5.5、§6）。

照 ``test_harness_loop.py`` 的写法自建夹具：``MemoryTranscript`` + 假模型 + 一个
只读的假档案路由/知识库/状态摘要，不建共享 conftest。这里钉的是布局本身的性质：
同一运行里相邻两步、以及同一会话的两次运行之间，除动态尾部外前缀逐字节相同。
"""

from __future__ import annotations

import asyncio
import json
from collections.abc import Sequence
from types import SimpleNamespace
from typing import Any

from maa_api.agent.context.layout import TAIL_HEADER, ContextLayout
from maa_api.agent.context.tokens import TokenEstimator
from maa_api.agent.harness.builtin_middleware import ContextLayoutMiddleware
from maa_api.agent.harness.context import (
    ModelRequest,
    ModelResponse,
    RunContext,
    ToolOutcome,
    Usage,
)
from maa_api.agent.harness.loop import AgentLoop, MemoryTranscript
from maa_api.agent.harness.middleware import CallNext, Middleware, MiddlewareChain
from maa_api.agent.llm.profiles import ResolvedProfile
from maa_api.agent.registry import ToolRegistry
from maa_api.agent.specs.registry import builtin_spec

_TOOL_SCHEMA = {
    "type": "function",
    "function": {
        "name": "lookup",
        "description": "查一下",
        "parameters": {"type": "object", "properties": {}},
    },
}


def _initial_history() -> list[dict[str, Any]]:
    """两轮对话的起点；让前缀比较覆盖不止一条历史消息。"""
    return [
        {"role": "user", "content": "旧问题"},
        {"role": "assistant", "content": "旧回答"},
        {"role": "user", "content": "开始"},
    ]


def _response(
    *,
    text: str = "",
    finish_reason: str | None = None,
    calls: Sequence[dict[str, Any]] = (),
) -> ModelResponse:
    return ModelResponse(
        text=text,
        reasoning="",
        usage=Usage(),
        upstream_finish_reason=finish_reason,
        latency_ms=1,
        raw_tool_calls=[dict(call) for call in calls],
    )


def _call(call_id: str, name: str, arguments: Any = None) -> dict[str, Any]:
    return {
        "index": 0,
        "id": call_id,
        "type": "function",
        "function": {"name": name, "arguments": json.dumps(arguments or {})},
    }


class _FakeModel:
    """按队列作答，并保留每次请求（``before_model`` 之后的最终形态）。"""

    def __init__(self, responses: Sequence[ModelResponse]) -> None:
        self.responses = list(responses)
        self.requests: list[ModelRequest] = []

    async def __call__(self, ctx: RunContext, req: ModelRequest) -> ModelResponse:
        self.requests.append(req)
        return self.responses.pop(0)


class _Dispatch(Middleware):
    """链上的执行器：工具调用直接成功。"""

    name = "dispatch"

    async def wrap_tool(
        self, ctx: RunContext, call: Any, call_next: CallNext
    ) -> ToolOutcome:
        return ToolOutcome(status="ok", result={"ok": True})


class _Knowledge:
    """固定的知识库替身：稳定段与动态段分开返回。"""

    async def render_stable_context(self) -> str:
        return "知识库稳定段：索引、术语表、档案"

    async def render_dynamic_context(self) -> str:
        return "知识库动态段：当期活动、卡池、账号"


class _Router:
    """固定档案的替身；不看 session 快照（非 legacy 定义本来就不用）。"""

    def __init__(self, profile: ResolvedProfile) -> None:
        self.profile = profile

    async def resolve_for_spec(
        self,
        spec: Any,
        *,
        session_model: str | None = None,
        session_base_url: str | None = None,
    ) -> ResolvedProfile:
        return self.profile


class _Runtime:
    async def _status_summary(self, request: Any) -> dict[str, Any]:
        return {"now": "2026-11-03T12:00:00+08:00", "core": "running"}


def _profile(position: str) -> ResolvedProfile:
    return ResolvedProfile(
        name="layout",
        kind="chat",
        provider="openai_compatible",
        base_url="https://llm.test/v1",
        api_key="sk-test",
        model="layout-model",
        context_window=None,
        max_output_tokens=None,
        supports_vision=None,
        supports_tools=True,
        supports_json_schema=None,
        dynamic_context_position=position,
        price_input=None,
        price_cached_input=None,
        price_output=None,
        currency="CNY",
        embedding_dim=None,
        local_model_path=None,
        readonly=False,
    )


def _app(position: str) -> SimpleNamespace:
    return SimpleNamespace(
        state=SimpleNamespace(
            context_layout=ContextLayout(TokenEstimator()),
            knowledge_service=_Knowledge(),
            model_router=_Router(_profile(position)),
            agent_runtime=_Runtime(),
        )
    )


def _run_turn(
    position: str,
    responses: Sequence[ModelResponse],
    *,
    transcript: MemoryTranscript | None = None,
) -> tuple[_FakeModel, MemoryTranscript, Any]:
    app = _app(position)
    store = transcript if transcript is not None else MemoryTranscript(_initial_history())
    model = _FakeModel(responses)
    ctx = RunContext(
        run_id="run-layout",
        root_run_id="run-layout",
        parent_run_id=None,
        session_id="session-layout",
        ticket_id=None,
        spec=builtin_spec("recon"),
        app=app,
        request=SimpleNamespace(app=app),
        vision_enabled=True,
    )
    loop = AgentLoop(
        chain=MiddlewareChain([ContextLayoutMiddleware(), _Dispatch()]),
        transcript=store,
        call_model=model,
        tool_schemas=lambda _run: [dict(_TOOL_SCHEMA)],
        registry=ToolRegistry(),
    )
    outcome = asyncio.run(loop.run(ctx))
    return model, store, outcome


def _tool_then_text() -> list[ModelResponse]:
    return [
        _response(
            finish_reason="tool_calls",
            calls=[_call("call-1", "lookup", {"key": "a"})],
        ),
        _response(text="做好了"),
    ]


def test_consecutive_steps_share_a_byte_identical_prefix() -> None:
    model, _store, outcome = _run_turn("tail_system", _tool_then_text())

    assert outcome.status == "succeeded"
    assert len(model.requests) == 2
    first, second = model.requests
    # 第一应答是工具调用、第二应答是文本；tail_system 下第二次请求的开头与第一次
    # 请求的「除尾部那条 system 之外」逐字节相同。
    assert json.dumps(
        first.messages[:-1], ensure_ascii=False
    ) == json.dumps(
        second.messages[: len(first.messages) - 1], ensure_ascii=False
    )
    assert first.tools == second.tools
    assert first.meta["layout"]["break_reason"] is None
    assert second.meta["layout"]["break_reason"] is None


def test_next_run_extends_the_previous_prefix() -> None:
    transcript = MemoryTranscript(_initial_history())
    model, transcript, _outcome = _run_turn(
        "tail_system", _tool_then_text(), transcript=transcript
    )
    previous = model.requests[-1]

    model, _transcript, _outcome = _run_turn(
        "tail_system", [_response(text="第二轮")], transcript=transcript
    )
    first = model.requests[0]

    # 第二次运行第一次请求的前 N-1 条（N = 上一运行最后一次请求的条数）与它相同，
    # 随后接上上一轮新落库的历史；tail_system 的尾部是最后一条，不参与比较。
    cut = len(previous.messages) - 1
    assert json.dumps(first.messages[:cut], ensure_ascii=False) == json.dumps(
        previous.messages[:cut], ensure_ascii=False
    )
    assert len(first.messages) > len(previous.messages)


def test_tail_user_prefix_excludes_only_the_merged_message() -> None:
    model, _store, _outcome = _run_turn("tail_user", _tool_then_text())
    first, second = model.requests

    # 第一次请求的最后一条 user 被并入了尾部；第二次请求里同一条是未改动的历史。
    assert first.messages[-1]["role"] == "user"
    assert TAIL_HEADER in first.messages[-1]["content"]
    assert any(
        message.get("role") == "user" and message.get("content") == "开始"
        for message in second.messages
    )
    # tail_user 下比较到倒数第二条：被并入尾部的那条消息本身就是唯一的差异。
    assert json.dumps(
        first.messages[:-1], ensure_ascii=False
    ) == json.dumps(
        second.messages[: len(first.messages) - 1], ensure_ascii=False
    )
    # 第二次请求的最后一条 tool 同样并入了尾部。
    assert second.messages[-1]["role"] == "tool"
    assert TAIL_HEADER in second.messages[-1]["content"]


def test_status_summary_is_not_in_the_system_message() -> None:
    model, _store, _outcome = _run_turn(
        "tail_system", [_response(text="好了")]
    )
    request = model.requests[0]
    system = request.messages[0]["content"]

    assert "状态摘要：" not in system
    assert "知识库稳定段" in system
    tail = request.messages[-1]
    assert tail["role"] == "system"
    assert tail["content"].startswith(TAIL_HEADER)
    assert "状态摘要：" in tail["content"]
    assert "知识库动态段" in tail["content"]
