"""工具结果清理：把较早的可重取结果换成占位（v3 06 §5、M74 规格 §4.2.4）。

清理只改发给模型的副本，库里的消息不动；结果只取决于历史本身，与时间、调用次数
无关。边界每 :data:`CLEARING_BATCH_ROUNDS` 个工具往返才前进一次，两次推进之间
前缀不变（v3 06 §5.2）。

本模块同时承担溢出判断：清理之后估算仍超过 ``window − output_reserve`` 时抛
``LLM_CONTEXT_OVERFLOW``，由 ``AgentLoop`` 映射成 ``finish_reason=context_overflow``。
"""

from __future__ import annotations

import inspect
import json
from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass
from typing import Any

from maa_api.agent.context.layout import ContextLayout
from maa_api.agent.context.tokens import (
    ContextBudget,
    TokenEstimator,
    budget_for,
    estimate_text,
)
from maa_api.agent.harness.context import ModelRequest, RunContext
from maa_api.agent.harness.middleware import Middleware
from maa_api.agent.specs.schema import LEGACY_SPEC_NAMES
from maa_api.domain.errors import AppError, ErrorCode
from maa_api.settings import get_settings

__all__ = [
    "CLEARING_BATCH_ROUNDS",
    "CLEARING_MIN_RESULT_CHARS",
    "CLEARING_MIN_TOKENS",
    "ClearingPlan",
    "ToolResultClearingMiddleware",
    "apply_clearing",
    "clearing_placeholder",
    "plan_clearing",
    "tool_rounds",
]


#: 清理边界成批推进的步长：每 6 个工具往返前进一次（06 §5.2）。
CLEARING_BATCH_ROUNDS = 6
#: content 文本超过这个字符数才是候选。
CLEARING_MIN_RESULT_CHARS = 200
#: 候选合计不到这个 token 数时不清理（收益太小，不值得断开缓存）。
CLEARING_MIN_TOKENS = 4000

#: 占位文案里产物 id 的截短长度（06 §5.3 的示例口径）。
_ARTIFACT_ID_HEAD = 8
#: 溢出的固定文案（06 §5.2、M74 规格 §4.2.4，逐字）。
CONTEXT_OVERFLOW_MESSAGE = (
    "上下文超出了模型窗口，压缩后仍然装不下。把任务拆小，或在工单里用 /compact 后重试"
)


@dataclass(frozen=True, slots=True)
class ClearingPlan:
    """一次清理计划：边界之前的哪些 tool 消息换成占位。"""

    #: 清理前 boundary 个工具往返。
    boundary: int
    #: 要换成占位的 tool 消息在 history 里的下标（升序）。
    message_indexes: tuple[int, ...]


def _content_text(content: Any) -> str:
    """参与字数与估算的正文：字符串原样；数组取 text 部分之和；其余为空。"""
    if isinstance(content, str):
        return content
    if isinstance(content, (list, tuple)):
        return "".join(
            str(part.get("text") or "")
            for part in content
            if isinstance(part, Mapping)
        )
    return ""


def _parse_json(content: Any) -> Any:
    """content 能解析成 JSON 时返回解析结果，否则 ``None``。"""
    text = _content_text(content)
    if not text:
        return None
    try:
        return json.loads(text)
    except (TypeError, ValueError):
        return None


def _payload_field(value: Any, key: str) -> Any:
    """从结果的顶层或它的 ``result`` 字段取一个键。"""
    if not isinstance(value, Mapping):
        return None
    if value.get(key):
        return value[key]
    result = value.get("result")
    if isinstance(result, Mapping) and result.get(key):
        return result[key]
    return None


def _is_error_result(content: Any) -> bool:
    """content 解析成 JSON 后顶层有 ``error`` 且没有 ``result``。"""
    value = _parse_json(content)
    return isinstance(value, Mapping) and "error" in value and "result" not in value


def _short_artifact_id(value: Any) -> str:
    """产物 id 截短：UUID 保留到第一个连字符（``6f1c2a9e-…``）。"""
    text = "" if value is None else str(value)
    head = _ARTIFACT_ID_HEAD
    if len(text) > head and text[head : head + 1] == "-":
        head += 1
    if len(text) <= head:
        return text
    return f"{text[:head]}…"


def clearing_placeholder(message: Mapping[str, Any], tool_name: str) -> str:
    """一条被清理 tool 消息的占位文案（v3 06 §5.3 的三种格式）。

    字数取原 content 的字符数（数组取 text 部分之和）；``artifact_id`` 优先于
    ``captured_at``。文本只由消息本身算出，同一份消息每次得到同样的结果。
    """
    content = message.get("content")
    text = _content_text(content)
    chars = len(text)
    value = _parse_json(content)
    artifact_id = _payload_field(value, "artifact_id")
    captured_at = _payload_field(value, "captured_at")
    if artifact_id:
        return (
            f"[旧结果已清理：{tool_name}，原 {chars} 字，"
            f"全文在产物 {_short_artifact_id(artifact_id)}；用 read_artifact 读取]"
        )
    if captured_at:
        return (
            f"[旧结果已清理：{tool_name}，原 {chars} 字，"
            f"截图时间 {captured_at}；需要时重新调用]"
        )
    return f"[旧结果已清理：{tool_name}，原 {chars} 字；需要时重新调用]"


def _call_names(message: Mapping[str, Any]) -> dict[str, str]:
    """一条 assistant 消息里 ``tool_call_id → 工具名`` 的映射。"""
    names: dict[str, str] = {}
    calls = message.get("tool_calls")
    if not isinstance(calls, (list, tuple)):
        return names
    for call in calls:
        if not isinstance(call, Mapping):
            continue
        call_id = call.get("id")
        if not call_id:
            continue
        function = call.get("function")
        name = function.get("name") if isinstance(function, Mapping) else None
        if not name:
            name = call.get("name")
        if name:
            names[str(call_id)] = str(name)
    return names


def tool_rounds(history: Sequence[Mapping[str, Any]]) -> list[tuple[int, int]]:
    """切出完整工具往返的 ``[start, end)``：带 ``tool_calls`` 的 assistant + 紧随的 tool。

    只有 tool 回复齐全才算一个往返；不完整的（模型输出被截断、消息还在路上）不参与
    清理，避免删掉还在等待的配对。
    """
    rounds: list[tuple[int, int]] = []
    index = 0
    while index < len(history):
        message = history[index]
        if message.get("role") != "assistant":
            index += 1
            continue
        calls = message.get("tool_calls")
        if not isinstance(calls, (list, tuple)) or not calls:
            index += 1
            continue
        required = {
            str(call.get("id"))
            for call in calls
            if isinstance(call, Mapping) and call.get("id")
        }
        if not required:
            index += 1
            continue
        end = index + 1
        replies: set[str] = set()
        while end < len(history) and history[end].get("role") == "tool":
            reply_id = history[end].get("tool_call_id")
            if reply_id is not None:
                replies.add(str(reply_id))
            end += 1
        if required.issubset(replies):
            rounds.append((index, end))
        index = end
    return rounds


def plan_clearing(
    history: Sequence[Mapping[str, Any]],
    *,
    keep_rounds: int,
    is_refetchable: Callable[[str], bool],
    estimator: TokenEstimator,
) -> ClearingPlan:
    """按 06 §5.2 算清理计划：边界成批推进，候选还要过大小与合计收益两关。

    ``estimator`` 留给调用方保持签名稳定；候选合计按 :func:`estimate_text` 计算，
    这样同一份历史在任何进程、任何校准比例下都得到同一个计划。
    """
    del estimator  # 计划只依赖历史本身（06 §5.2 第 5 条），不依赖校准比例
    rounds = tool_rounds(history)
    total = len(rounds)
    if total <= keep_rounds:
        boundary = 0
    else:
        boundary = ((total - keep_rounds) // CLEARING_BATCH_ROUNDS) * (
            CLEARING_BATCH_ROUNDS
        )
    if boundary <= 0:
        return ClearingPlan(0, ())

    candidates: list[tuple[int, str]] = []
    for start, end in rounds[:boundary]:
        names = _call_names(history[start])
        for index in range(start + 1, end):
            message = history[index]
            if message.get("role") != "tool":
                continue
            tool_name = names.get(str(message.get("tool_call_id")))
            if not tool_name:
                raw_name = message.get("name")
                tool_name = str(raw_name) if raw_name else ""
            if not tool_name or not is_refetchable(tool_name):
                continue
            text = _content_text(message.get("content"))
            if len(text) <= CLEARING_MIN_RESULT_CHARS:
                continue
            if _is_error_result(message.get("content")):
                continue
            candidates.append((index, text))

    estimated = sum(estimate_text(text) for _, text in candidates)
    if estimated < CLEARING_MIN_TOKENS:
        return ClearingPlan(boundary, ())
    return ClearingPlan(boundary, tuple(index for index, _ in candidates))


def _cleared_message(message: Mapping[str, Any], placeholder: str) -> dict[str, Any]:
    """被替换的消息：保留 role、tool_call_id 与原有的 name 键，其余键去掉。"""
    cleared: dict[str, Any] = {
        "role": message.get("role", "tool"),
        "tool_call_id": message.get("tool_call_id"),
    }
    if "name" in message:
        cleared["name"] = message["name"]
    cleared["content"] = placeholder
    return cleared


def apply_clearing(
    history: list[dict[str, Any]], plan: ClearingPlan
) -> list[dict[str, Any]]:
    """返回换过占位的新列表；不改入参里的任何消息 dict。"""
    names: dict[str, str] = {}
    for message in history:
        if message.get("role") == "assistant":
            for call_id, name in _call_names(message).items():
                names.setdefault(call_id, name)
    cleared = list(history)
    for index in plan.message_indexes:
        if index < 0 or index >= len(cleared):
            continue
        message = cleared[index]
        tool_name = names.get(str(message.get("tool_call_id")))
        if not tool_name:
            raw_name = message.get("name")
            tool_name = str(raw_name) if raw_name else ""
        cleared[index] = _cleared_message(
            message, clearing_placeholder(message, tool_name)
        )
    return cleared


def _layout_service(ctx: RunContext) -> ContextLayout:
    """优先用进程内的 ``app.state.context_layout``，测试里退回本轮本地实例。"""
    service = getattr(getattr(ctx.app, "state", None), "context_layout", None)
    if isinstance(service, ContextLayout):
        return service
    local = ctx.state.get("clearing.context_layout")
    if isinstance(local, ContextLayout):
        return local
    local = ContextLayout(TokenEstimator())
    ctx.state["clearing.context_layout"] = local
    return local


def _registry_of(ctx: RunContext, registry: Any) -> Any:
    """注入的 registry 优先，否则取 ``app.state.confirmation_service.registry``。"""
    if registry is not None:
        return registry
    service = getattr(getattr(ctx.app, "state", None), "confirmation_service", None)
    return getattr(service, "registry", None)


def _refetchable_check(registry: Any) -> Callable[[str], bool]:
    """``name → registry.get(name).refetchable``；未知工具名按 False。"""

    def is_refetchable(name: str) -> bool:
        if registry is None:
            return False
        try:
            return bool(registry.get(name).refetchable)
        except Exception:  # noqa: BLE001 - 未知工具按不可重取处理
            return False

    return is_refetchable


class ToolResultClearingMiddleware(Middleware):
    """把较早的可重取工具结果换成占位，并在清理后做溢出判断（M74 §4.2.4）。

    四个代理种类都执行；legacy 定义直接返回（v2 布局逐字节不变）。清理后重新
    ``context_layout.apply``，然后比较 ``estimated_tokens`` 与溢出线。
    """

    name = "tool_result_clearing"
    applies_to = frozenset({"orchestrator", "worker", "subagent", "background"})

    def __init__(
        self,
        *,
        registry: Any = None,
        settings_provider: Callable[[], Any] | None = None,
    ) -> None:
        self._registry = registry
        self._settings_provider = settings_provider or get_settings

    async def _resolve_settings(self) -> Any:
        settings = self._settings_provider()
        if inspect.isawaitable(settings):
            settings = await settings
        return settings

    async def before_model(self, ctx: RunContext, req: ModelRequest) -> ModelRequest:
        if ctx.spec.name in LEGACY_SPEC_NAMES:
            return req
        parts = req.meta.get("layout_parts")
        if parts is None:
            return req
        profile = req.profile
        if profile is None:
            model_state = ctx.state.get("model")
            if isinstance(model_state, Mapping):
                profile = model_state.get("profile")
        if profile is None:
            return req

        settings = await self._resolve_settings()
        budget: ContextBudget = budget_for(profile, settings)
        layout = _layout_service(ctx)
        plan = plan_clearing(
            parts.history,
            keep_rounds=int(settings.context.clear_tool_results_after),
            is_refetchable=_refetchable_check(_registry_of(ctx, self._registry)),
            estimator=layout.estimator,
        )
        if plan.message_indexes:
            parts.history = apply_clearing(parts.history, plan)
            req = layout.apply(ctx, req, parts, profile=profile)

        # 先 apply 再记边界：G5 的断点判断按「边界比上一次大 → clearing」比较旧值。
        ctx.state.setdefault("context_layout", {})["clear_boundary"] = plan.boundary

        if req.estimated_tokens > budget.overflow_tokens:
            raise AppError(
                ErrorCode.LLM_CONTEXT_OVERFLOW,
                CONTEXT_OVERFLOW_MESSAGE,
                {
                    "estimated_tokens": req.estimated_tokens,
                    "overflow_tokens": budget.overflow_tokens,
                },
            )
        return req
