"""工具批调度与三道执行期闸门（规格 §4.2.8）。

``execute_batch`` 是 v2 ``AgentRuntime._execute_tool_batch`` 的调度部分：先整批解析
参数（坏 JSON 不派发任何调用），再按「只读并发、其余串行」执行。三道闸门在中间件链
外面、``tool_start`` **之后**，顺序与 v2 一致：隐藏工具 → 执行期白名单 → 视觉降级。
被闸门拦下的调用不进确认服务、不落审计。

策略、确认与等待在 ``PolicyMiddleware`` 里（``builtin_middleware``）——它是链上那个
不调 ``call_next`` 的执行器。
"""

from __future__ import annotations

import asyncio
import json
from collections.abc import Mapping, Sequence
from dataclasses import replace
from typing import Any

from maa_api.agent.harness.chunks import parse_arguments
from maa_api.agent.harness.context import (
    CURRENT_RUN,
    RunContext,
    ToolCall,
    ToolOutcome,
)
from maa_api.agent.harness.middleware import MiddlewareChain
from maa_api.agent.registry import ToolRegistry
from maa_api.domain.errors import AppError, ErrorCode

__all__ = [
    "CONFIRMATION_TERMINAL_CODES",
    "CONFIRMATION_TERMINAL_TEXT",
    "HIDDEN_TOOL_MESSAGE",
    "SCREEN_TOOL_NAMES",
    "SKIPPED_AFTER_REJECTION_MESSAGE",
    "TOOL_NOT_ALLOWED_MESSAGE",
    "app_error_wire",
    "execute_batch",
    "json_text",
    "vision_disabled_message",
]

SCREEN_TOOL_NAMES = frozenset(
    {
        "get_screenshot",
        # 圈选截图也要在视觉降级时隐藏并拦截：模型看不见画面就不可能圈对位置；
        # 其余四个媒体工具服务的是用户的眼睛，降级时仍应可用（规格 §3.2）。
        "annotate_screenshot",
        "click",
        "swipe",
        "long_press",
        "input_text",
        "key_event",
    }
)
#: 视觉降级时的拦截文案：按工具名生成，不把圈选说成「原子屏幕操作」（规格 §3.2）。
_VISION_DISABLED_MESSAGES = {
    "annotate_screenshot": "当前模型不支持视觉输入；圈选截图已禁用",
}
_DEFAULT_VISION_DISABLED_MESSAGE = "当前模型不支持视觉输入；截图和原子屏幕操作已禁用"
#: 模型硬造一个模型不可见的工具时的拒绝文案（不进确认服务、不落审计）。
HIDDEN_TOOL_MESSAGE = "内置 Agent 不提供该工具，请用 get_screenshot"
#: 自动诊断会话的调用超出执行期白名单时的拒绝文案。
TOOL_NOT_ALLOWED_MESSAGE = "此自动诊断会话不允许调用该工具"
#: 串行批里前一项确认被拒/过期后，余下调用补的占位结果文案。
SKIPPED_AFTER_REJECTION_MESSAGE = "前一项操作未获确认，本项未执行。"
#: 确认进入终态（被拒或过期）的错误码；命中它的批以 ``confirmation_resolved`` 收尾。
CONFIRMATION_TERMINAL_CODES = frozenset(
    {ErrorCode.CONFIRMATION_REJECTED.value, ErrorCode.CONFIRMATION_EXPIRED.value}
)
#: 确认被拒或过期时结束本轮写给用户的那句话。
CONFIRMATION_TERMINAL_TEXT = "确认被拒绝或过期了，我不会重试。要换个方案吗？"


def vision_disabled_message(tool_name: str) -> str:
    """视觉降级时的拦截文案：按工具名生成（规格 §3.2）。

    ``annotate_screenshot`` 不能沿用「截图和原子屏幕操作已禁用」：圈选不是原子操作。
    """
    return _VISION_DISABLED_MESSAGES.get(tool_name, _DEFAULT_VISION_DISABLED_MESSAGE)


def json_text(value: Any) -> str:
    """工具结果落库 / 进 SSE 的紧凑 JSON（v2 原样）。"""
    return json.dumps(value, ensure_ascii=False, separators=(",", ":"), default=str)


def app_error_wire(error: AppError) -> dict[str, Any]:
    """``AppError`` 的线上形状：``code`` / ``message``，有 details 才带上。"""
    return {
        "code": str(error.code),
        "message": error.message,
        **({"details": error.details} if error.details is not None else {}),
    }


async def execute_batch(
    ctx: RunContext,
    calls: Sequence[ToolCall],
    *,
    chain: MiddlewareChain,
    registry: ToolRegistry,
) -> list[ToolOutcome]:
    """执行一批工具调用，返回与 ``calls`` 一一对应的结果（追加的 skipped 除外）。

    参数在派发前整批解析：任何一条坏 JSON 都让整批在 ``tool_start`` 之前抛
    ``AppError``，不会留下执行了一半的批。只读批并发执行；其余按顺序串行，某个结果
    是确认终态时，余下调用补 ``skipped`` 占位、不再执行。最后给所有确认终态结果打上
    ``stop_run``，让本轮以 ``confirmation_resolved`` 收尾（规格 §4.2.8）。
    """
    # Parse all arguments before dispatch so malformed JSON never leaves a
    # partially executed batch.
    parsed = [replace(call, arguments=parse_arguments(call.raw_arguments)) for call in calls]

    parallel = bool(ctx.parallel_tools and parsed)
    if parallel:
        for call in parsed:
            try:
                if not registry.get(call.name).read_only:
                    parallel = False
                    break
            except AppError:
                parallel = False
                break

    async def innermost(call: ToolCall) -> ToolOutcome:
        # 链里必须有一个不调 call_next 的执行器（v2 会话是 PolicyMiddleware）。
        raise RuntimeError(f"中间件链里没有执行器：{call.name}")

    async def run(call: ToolCall) -> ToolOutcome:
        event: dict[str, Any] = {
            "tool_call_id": call.id,
            "name": call.name,
            "arguments": call.arguments,
        }
        if ctx.emits_v3_events:
            event["run_id"] = ctx.run_id
        await ctx.emit("tool_start", event)
        hidden_tool = False
        try:
            hidden_tool = not registry.get(call.name).model_visible
        except AppError:
            # 未注册的工具仍走原有路径，保持「未知工具」的报错文案。
            pass
        if hidden_tool:
            # 模型看不见的工具只留给 REST；拒绝时不进确认服务，也不落审计。
            return ToolOutcome(
                status="error",
                error={"code": ErrorCode.TOOL_NOT_FOUND.value, "message": HIDDEN_TOOL_MESSAGE},
            )
        if ctx.allowed_tools is not None and call.name not in ctx.allowed_tools:
            return ToolOutcome(
                status="error",
                error={"code": ErrorCode.TOOL_NOT_FOUND.value, "message": TOOL_NOT_ALLOWED_MESSAGE},
            )
        if not ctx.vision_enabled and call.name in SCREEN_TOOL_NAMES:
            return ToolOutcome(
                status="error",
                error={
                    "code": ErrorCode.LLM_REQUEST_FAILED.value,
                    "message": vision_disabled_message(call.name),
                },
            )
        # 工具 handler 经 ``require_run`` 取当前运行（M70 §4.2.2）。在调链之前 set、
        # 之后 reset：只读批的每个 ``run`` 都是独立任务，各自的 ContextVar 互不可见。
        token = CURRENT_RUN.set(ctx)
        try:
            return await chain.wrap_tool(ctx, call, innermost)
        finally:
            CURRENT_RUN.reset(token)

    if parallel:
        outcomes = list(await asyncio.gather(*(run(call) for call in parsed)))
    else:
        outcomes = []
        for index, call in enumerate(parsed):
            outcome = await run(call)
            outcomes.append(outcome)
            error = outcome.error
            if (
                not isinstance(error, Mapping)
                or error.get("code") not in CONFIRMATION_TERMINAL_CODES
            ):
                continue
            for _skipped in parsed[index + 1 :]:
                outcomes.append(
                    ToolOutcome(
                        status="skipped",
                        error={
                            "code": error["code"],
                            "message": SKIPPED_AFTER_REJECTION_MESSAGE,
                        },
                    )
                )
            break

    for outcome in outcomes:
        error = outcome.error
        if (
            not outcome.stop_run
            and isinstance(error, Mapping)
            and error.get("code") in CONFIRMATION_TERMINAL_CODES
        ):
            outcome.stop_run = True
            outcome.stop_reason = "confirmation_resolved"
            outcome.stop_text = CONFIRMATION_TERMINAL_TEXT
    return outcomes
