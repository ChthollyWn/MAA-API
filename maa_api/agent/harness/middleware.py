"""中间件协议与链（规格 §4.2.5）。

顺序约定：``on_run_start``、``before_model`` 正序，``after_model``、``on_run_end``
逆序，``wrap_tool`` 是洋葱——第一个中间件在最外层。``wrap_tool`` 的 ``innermost``
由 ``toolexec`` 提供，链里必须有一个不调 ``call_next`` 的执行器（v2 会话是
``PolicyMiddleware``，recon 是 ``ReconDispatchMiddleware``）。
"""

from __future__ import annotations

from collections.abc import Awaitable, Callable, Sequence
from typing import ClassVar

from maa_api.agent.harness.context import (
    ModelRequest,
    ModelResponse,
    RunContext,
    RunOutcome,
    ToolCall,
    ToolOutcome,
)

__all__ = ["ALL_AGENT_KINDS", "CallNext", "Middleware", "MiddlewareChain"]

ALL_AGENT_KINDS: frozenset[str] = frozenset({"orchestrator", "worker", "subagent", "background"})
CallNext = Callable[[ToolCall], Awaitable[ToolOutcome]]


class Middleware:
    """一轮运行里的一个横切关注点；默认全部直通。"""

    name: ClassVar[str] = "middleware"
    applies_to: ClassVar[frozenset[str]] = ALL_AGENT_KINDS

    async def on_run_start(self, ctx: RunContext) -> None:
        return None

    async def before_model(self, ctx: RunContext, req: ModelRequest) -> ModelRequest:
        return req

    async def after_model(self, ctx: RunContext, resp: ModelResponse) -> ModelResponse:
        return resp

    async def wrap_tool(
        self, ctx: RunContext, call: ToolCall, call_next: CallNext
    ) -> ToolOutcome:
        return await call_next(call)

    async def on_run_end(self, ctx: RunContext, outcome: RunOutcome) -> None:
        return None


class MiddlewareChain:
    """按固定顺序驱动一组中间件；构造时不过滤，``for_kind`` 才按种类筛选。"""

    def __init__(self, middlewares: Sequence[Middleware]) -> None:
        self._middlewares: tuple[Middleware, ...] = tuple(middlewares)

    @classmethod
    def for_kind(cls, kind: str, middlewares: Sequence[Middleware]) -> MiddlewareChain:
        """只留 ``applies_to`` 含 ``kind`` 的中间件，保持原顺序。"""
        return cls([middleware for middleware in middlewares if kind in middleware.applies_to])

    @property
    def names(self) -> tuple[str, ...]:
        return tuple(middleware.name for middleware in self._middlewares)

    async def on_run_start(self, ctx: RunContext) -> None:
        for middleware in self._middlewares:
            await middleware.on_run_start(ctx)

    async def before_model(self, ctx: RunContext, req: ModelRequest) -> ModelRequest:
        for middleware in self._middlewares:
            req = await middleware.before_model(ctx, req)
        return req

    async def after_model(self, ctx: RunContext, resp: ModelResponse) -> ModelResponse:
        for middleware in reversed(self._middlewares):
            resp = await middleware.after_model(ctx, resp)
        return resp

    async def wrap_tool(
        self, ctx: RunContext, call: ToolCall, innermost: CallNext
    ) -> ToolOutcome:
        call_next = innermost
        for middleware in reversed(self._middlewares):
            call_next = _bind_wrap_tool(middleware, ctx, call_next)
        return await call_next(call)

    async def on_run_end(self, ctx: RunContext, outcome: RunOutcome) -> None:
        for middleware in reversed(self._middlewares):
            await middleware.on_run_end(ctx, outcome)


def _bind_wrap_tool(middleware: Middleware, ctx: RunContext, call_next: CallNext) -> CallNext:
    """把下一个中间件（或执行器）包在 ``middleware`` 里，得到一层洋葱。"""

    async def wrapped(call: ToolCall) -> ToolOutcome:
        return await middleware.wrap_tool(ctx, call, call_next)

    return wrapped
