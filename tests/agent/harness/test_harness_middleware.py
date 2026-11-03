"""M65 T4：中间件协议、链顺序与洋葱语义（规格 §4.2.5）。"""

from __future__ import annotations

import asyncio

import pytest

from maa_api.agent.harness.context import (
    ModelRequest,
    ModelResponse,
    RunContext,
    RunOutcome,
    ToolCall,
    ToolOutcome,
    Usage,
)
from maa_api.agent.harness.middleware import ALL_AGENT_KINDS, Middleware, MiddlewareChain
from maa_api.agent.specs.registry import builtin_spec


def _context() -> RunContext:
    return RunContext(
        run_id="run-1",
        root_run_id="run-1",
        parent_run_id=None,
        session_id=None,
        ticket_id=None,
        spec=builtin_spec("legacy_chat"),
    )


def _request() -> ModelRequest:
    return ModelRequest(messages=[{"role": "user", "content": "hi"}], tools=[])


def _response() -> ModelResponse:
    return ModelResponse(
        text="ok",
        reasoning="",
        usage=Usage(),
        upstream_finish_reason="stop",
        latency_ms=5,
    )


def _outcome() -> RunOutcome:
    return RunOutcome(status="succeeded", finish_reason="stop")


class _Recorder(Middleware):
    """按阶段往共享日志里记一笔，用来钉住链的调用顺序。"""

    def __init__(self, name: str, log: list[str]) -> None:
        self.name = name
        self._log = log

    async def on_run_start(self, ctx: RunContext) -> None:
        self._log.append(f"{self.name}:start")

    async def before_model(self, ctx: RunContext, req: ModelRequest) -> ModelRequest:
        self._log.append(f"{self.name}:before")
        req.meta.setdefault("order", []).append(self.name)
        return req

    async def after_model(self, ctx: RunContext, resp: ModelResponse) -> ModelResponse:
        self._log.append(f"{self.name}:after")
        return resp

    async def on_run_end(self, ctx: RunContext, outcome: RunOutcome) -> None:
        self._log.append(f"{self.name}:end")


class _ToolRecorder(Middleware):
    def __init__(self, name: str, log: list[str]) -> None:
        self.name = name
        self._log = log

    async def wrap_tool(self, ctx: RunContext, call: ToolCall, call_next) -> ToolOutcome:
        self._log.append(f"{self.name}:before_tool")
        outcome = await call_next(call)
        self._log.append(f"{self.name}:after_tool")
        return outcome


def test_chain_runs_before_model_in_order_and_after_model_in_reverse():
    log: list[str] = []
    chain = MiddlewareChain([_Recorder("a", log), _Recorder("b", log)])
    ctx = _context()

    asyncio.run(chain.on_run_start(ctx))
    assert log == ["a:start", "b:start"]

    req = asyncio.run(chain.before_model(ctx, _request()))
    assert log == ["a:start", "b:start", "a:before", "b:before"]
    # 每个中间件拿到的是上一个中间件返回的请求（逐个替换）。
    assert req.meta["order"] == ["a", "b"]

    asyncio.run(chain.after_model(ctx, _response()))
    assert log == ["a:start", "b:start", "a:before", "b:before", "b:after", "a:after"]


def test_on_run_end_runs_in_reverse():
    log: list[str] = []
    chain = MiddlewareChain([_Recorder("a", log), _Recorder("b", log)])

    asyncio.run(chain.on_run_end(_context(), _outcome()))

    assert log == ["b:end", "a:end"]


def test_wrap_tool_is_an_onion_with_the_first_middleware_outermost():
    log: list[str] = []
    chain = MiddlewareChain([_ToolRecorder("a", log), _ToolRecorder("b", log)])
    call = ToolCall(id="call-1", name="lookup", raw_arguments='{"q": 1}')

    async def executor(tool_call: ToolCall) -> ToolOutcome:
        log.append(f"executor:{tool_call.name}:{tool_call.id}")
        return ToolOutcome(status="ok", result={"ok": True})

    outcome = asyncio.run(chain.wrap_tool(_context(), call, executor))

    assert log == [
        "a:before_tool",
        "b:before_tool",
        "executor:lookup:call-1",
        "b:after_tool",
        "a:after_tool",
    ]
    assert outcome == ToolOutcome(status="ok", result={"ok": True})
    assert outcome.body() == {"result": {"ok": True}}


def test_wrap_tool_without_an_executor_raises():
    """链里没有执行器时，``toolexec`` 传入的 innermost 抛 RuntimeError，链原样上抛。"""

    log: list[str] = []
    chain = MiddlewareChain([_ToolRecorder("a", log), _ToolRecorder("b", log)])
    call = ToolCall(id="call-1", name="lookup", raw_arguments="{}")

    async def no_executor(tool_call: ToolCall) -> ToolOutcome:
        log.append(f"executor:{tool_call.name}")
        raise RuntimeError(f"中间件链里没有执行器：{tool_call.name}")

    with pytest.raises(RuntimeError, match="中间件链里没有执行器：lookup"):
        asyncio.run(chain.wrap_tool(_context(), call, no_executor))

    # 异常穿透洋葱，所有中间件的 after 钩子都不执行。
    assert log == ["a:before_tool", "b:before_tool", "executor:lookup"]


class _Everywhere(Middleware):
    name = "everywhere"


class _Clarification(Middleware):
    """T7 的 ClarificationMiddleware 只进 orchestrator / worker 链。"""

    name = "clarification"
    applies_to = frozenset({"orchestrator", "worker"})


class _SubagentOnly(Middleware):
    name = "subagent_only"
    applies_to = frozenset({"subagent"})


def test_for_kind_keeps_only_middlewares_that_apply():
    middlewares = [_Everywhere(), _Clarification(), _SubagentOnly()]

    assert ALL_AGENT_KINDS == frozenset({"orchestrator", "worker", "subagent", "background"})
    assert Middleware.applies_to is ALL_AGENT_KINDS

    assert MiddlewareChain.for_kind("worker", middlewares).names == ("everywhere", "clarification")
    assert MiddlewareChain.for_kind("orchestrator", middlewares).names == (
        "everywhere",
        "clarification",
    )
    # ClarificationMiddleware 不进 subagent 链。
    assert MiddlewareChain.for_kind("subagent", middlewares).names == (
        "everywhere",
        "subagent_only",
    )
    assert MiddlewareChain.for_kind("background", middlewares).names == ("everywhere",)
    assert MiddlewareChain.for_kind("nope", middlewares).names == ()

    # 构造器不过滤，保持顺序；调用方自己决定是否 for_kind。
    assert MiddlewareChain(middlewares).names == ("everywhere", "clarification", "subagent_only")
