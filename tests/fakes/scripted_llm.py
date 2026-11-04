"""按调用方分队列作答的脚本化 LLM（M70 规格 T14，§6 的七个端到端用例共用）。

与 :class:`tests.fakes.fake_llm.FakeLLM` 的区别是**先认调用方再取脚本**：总控、
Worker 与四个子代理共用同一个 ``app.state.agent_runtime.client_factory``，端到端
测试靠系统提示正文第一句里的身份标记（:data:`MARKERS`，与
``maa_api/agent/specs/builtin/*.md`` 的实际正文保持一致）区分调用方，各自排一条
脚本队列。多个工单同时跑时传自定义 ``route``，按工单卡里的「工单：「X」」区分。

:class:`Hold` 用来把某一次模型调用卡住：``started`` 在调用真正到达模型时置位，
测试线程等它出现后再做别的动作；``gate`` 由测试线程放开后，调用才返回
``response``。两个事件都是 :class:`threading.Event`，应用线程与测试线程都能用。

M67 的自动能力探查（工具调用 / 视觉 / JSON Schema / 尾部位置）没有系统提示词，
不属于任何调用方；:class:`ScriptedLLM` 按探针请求的固定文本直接作答，不消耗脚本
队列、也不记进 ``requests``。端到端的第一个 v3 运行会先触发这一次探查。
"""

from __future__ import annotations

import asyncio
import threading
from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field
from typing import Any

from maa_api.domain.errors import AppError
from tests.fakes.fake_llm import (
    FakeResponse,
    content_text,
    text_response,
    tool_call,
    tool_response,
)

__all__ = [
    "MARKERS",
    "Hold",
    "ScriptedLLM",
    "install_llm",
    "route_by_marker",
]

#: 调用方 → 系统提示正文第一句里的身份标记（spec §4.7；改正文时保留这句）。
MARKERS: dict[str, str] = {
    "orchestrator": "你是 MAA-API 的总控",
    "general": "你是 MAA-API 的通用执行 Worker",
    "researcher": "你是情报员子代理",
    "navigator": "你是导航员子代理",
    "verifier": "你是验证员子代理",
    "titler": "你给工单起标题",
}

#: M67 能力探查的固定请求文本（``maa_api/agent/llm/profiles.py`` 的私有常量）。
_PROBE_TOOL_NAME = "capability_probe"
_VISION_PROBE_PROMPT = "What color is this image? Answer with one word."
_VISION_PROBE_ANSWER = "blue"
_JSON_SCHEMA_PROBE_PROMPT = "Return a JSON object with ok set to true."
_JSON_SCHEMA_PROBE_ANSWER = '{"ok": true}'
_TAIL_PROBE_SYSTEM = "You are a probe. Reply with OK."

#: ``Hold`` 等 gate 的最长时间；超时说明测试自己忘了放开，直接让这一轮失败。
_HOLD_TIMEOUT_SECONDS = 10.0


@dataclass
class Hold:
    """先等 ``gate`` 再作答的一次模型调用；两个事件供测试线程与应用线程共用。"""

    response: FakeResponse
    gate: threading.Event = field(default_factory=threading.Event)
    started: threading.Event = field(default_factory=threading.Event)


def _message_text(message: Mapping[str, Any]) -> str:
    """一条消息的文本内容（多模态数组取 text 部分）。"""
    return content_text(message.get("content"))


def route_by_marker(messages: Sequence[Mapping[str, Any]]) -> str:
    """按第一条 ``system`` 消息里的身份标记返回调用方键；都不匹配抛断言。"""
    system_text = next(
        (
            _message_text(message)
            for message in messages
            if str(message.get("role")) == "system"
        ),
        "",
    )
    for key, marker in MARKERS.items():
        if marker in system_text:
            return key
    raise AssertionError(
        "无法从系统提示里识别调用方；"
        f"已配置的标记={sorted(MARKERS)}，第一条 system={system_text[:160]!r}"
    )


def _probe_chunks(
    messages: Sequence[Mapping[str, Any]], tools: Sequence[Mapping[str, Any]]
) -> list[dict[str, Any]] | None:
    """M67 自动探查的固定应答；不是探针请求时返回 ``None``。"""
    payloads = [
        tool.get("function", {})
        for tool in tools
        if isinstance(tool, Mapping)
    ]
    if any(
        isinstance(function, Mapping)
        and function.get("name") == _PROBE_TOOL_NAME
        for function in payloads
    ):
        return tool_response(tool_call("probe-tool-call", _PROBE_TOOL_NAME, {}))
    text = "\n".join(_message_text(message) for message in messages)
    if _VISION_PROBE_PROMPT in text:
        return text_response(_VISION_PROBE_ANSWER)
    if _JSON_SCHEMA_PROBE_PROMPT in text:
        return text_response(_JSON_SCHEMA_PROBE_ANSWER)
    if _TAIL_PROBE_SYSTEM in text:
        return text_response("OK")
    return None


class ScriptedLLM:
    """按调用方分队列作答的 OpenAI 兼容流，并记录每次请求。

    ``scripts`` 是 ``{调用方键: 应答队列}``；队列项可以是 ``FakeResponse``（分片
    序列、``AppError`` 或按消息动态生成分片的工厂）或 :class:`Hold`。队列用完后
    再调用会抛 ``AssertionError``，让测试在「脚本没排够」处显式失败。

    ``requests`` 每项是 ``(调用方键, messages, tools)``，只记真正的调用；能力探查
    不记。``remaining()`` 返回每个调用方剩下的脚本条数。
    """

    def __init__(
        self,
        scripts: Mapping[str, Sequence[FakeResponse | Hold]],
        *,
        route: Any = route_by_marker,
    ) -> None:
        self._scripts: dict[str, list[FakeResponse | Hold]] = {
            key: list(items) for key, items in scripts.items()
        }
        self._route = route
        self.requests: list[tuple[str, list[dict[str, Any]], list[dict[str, Any]]]] = []

    def remaining(self) -> dict[str, int]:
        """每个调用方脚本队列里还剩几条（包含已经排空、值为 0 的键）。"""
        return {key: len(items) for key, items in self._scripts.items()}

    def _take(self, key: str) -> FakeResponse | Hold:
        items = self._scripts.get(key)
        if not items:
            raise AssertionError(
                f"调用方 {key!r} 没有剩余脚本；剩余={self.remaining()}"
            )
        return items.pop(0)

    async def stream_chat_completion(
        self,
        model: str,
        messages: Sequence[dict[str, Any]],
        tools: Sequence[dict[str, Any]],
        **options: Any,
    ):
        """按调用方取一条脚本并产出分片；``options`` 收下 M67 的额外关键字。"""
        del model, options
        probe = _probe_chunks(messages, tools)
        if probe is not None:
            for chunk in probe:
                yield chunk
            return

        key = self._route(messages)
        self.requests.append((key, list(messages), list(tools)))
        item = self._take(key)
        if isinstance(item, Hold):
            item.started.set()
            released = await asyncio.to_thread(
                item.gate.wait, _HOLD_TIMEOUT_SECONDS
            )
            if not released:
                raise AssertionError(
                    f"调用方 {key!r} 的 Hold 未在 {_HOLD_TIMEOUT_SECONDS:.0f}s 内放开"
                )
            response: FakeResponse = item.response
        else:
            response = item
        if callable(response):
            response = response(list(messages))
        if isinstance(response, AppError):
            raise response
        for chunk in response:
            yield chunk


def install_llm(app: Any, llm: Any) -> None:
    """把 ``llm`` 设成 M67 的测试替换点（``AgentRuntime.client_factory``）。

    必须在应用 lifespan 启动之后调用：``agent_runtime`` 由 lifespan 创建。总控、
    Worker 与子代理解析档案时都经 ``ModelRouter.client_for`` 读这个工厂。
    """
    runtime = getattr(getattr(app, "state", None), "agent_runtime", None)
    if runtime is None:
        raise AssertionError("应用尚未启动：app.state.agent_runtime 不存在")
    runtime.client_factory = lambda _settings: llm
