"""共用的 Fake LLM 与 SSE 事件解析（M18 场景测试）。

`tests/agent/test_runtime.py` 与 `tests/agent/test_incidents.py` 各自还留着一份私有
实现；本次只把场景测试需要的这一小撮抽成真包路径（`from tests.fakes.fake_llm import
...`），**没有**回头改写那两个文件——跨文件导入兄弟测试模块会依赖 pytest 的
rootdir 导入模式与私有名字，真包路径稳定得多。

`tests/agent/` 没有 `__init__.py`，那里的模块按顶层模块名导入；`tests/fakes/` 是
真包，所以这条导入路径与 pytest 的导入模式无关。
"""

from __future__ import annotations

import json
from collections.abc import Callable, Iterable, Sequence
from typing import Any

from maa_api.domain.errors import AppError

__all__ = [
    "FakeLLM",
    "FakeResponse",
    "content_text",
    "parse_events",
    "text_response",
    "tool_call",
    "tool_response",
    "turn_events",
]

#: 一次模型应答：分片序列、注入的错误，或按当前消息动态生成分片的工厂。
#:
#: 工厂形式是给「模型要先读上一条工具结果才能发下一个调用」的场景用的，例如
#: `wait_pipeline` 的 `pipeline_id` 只能从 `submit_pipeline` 的结果里读出来。
FakeResponse = (
    Sequence[dict[str, Any]]
    | AppError
    | Callable[[list[dict[str, Any]]], Sequence[dict[str, Any]]]
)


class FakeLLM:
    """按队列作答的 OpenAI 兼容流，并记录每次请求的 ``(model, messages, tools)``。"""

    def __init__(self, responses: Iterable[FakeResponse]) -> None:
        self.responses = list(responses)
        self.requests: list[tuple[str, list[dict[str, Any]], list[dict[str, Any]]]] = []

    async def stream_chat_completion(self, model, messages, tools):
        self.requests.append((model, list(messages), list(tools)))
        if not self.responses:
            # 队列空掉时按会话标题请求作答：标题是首轮收流前的一次额外调用（无工具、
            # 无系统提示词）。绝大多数用例只关心对话那条链路，不必为每条脚本多排一份。
            assert tools == [], "unexpected extra LLM call outside title generation"
            yield text_response("标题")[0]
            return
        response = self.responses.pop(0)
        if callable(response):
            response = response(list(messages))
        if isinstance(response, AppError):
            raise response
        for chunk in response:
            yield chunk

    @property
    def turns(self) -> list[tuple[str, list[dict[str, Any]], list[dict[str, Any]]]]:
        """只保留对话链路上的请求。

        能力探测与标题生成都是运行时自己发的一次性调用，共同特征是**没有系统提示词**
        （`build_model_context` 的第一条永远是 system）。不能按「有没有工具」区分：
        能力探测带着探针工具，而会话轮次的工具表在测试里也可能是空的。
        """
        return [
            request
            for request in self.requests
            if request[1] and request[1][0].get("role") == "system"
        ]


def text_response(
    text: str, *, prompt_tokens: int = 7, completion_tokens: int = 2
) -> list[dict[str, Any]]:
    """一次纯文本应答（含 usage）。"""
    return [
        {"choices": [{"index": 0, "delta": {"content": text}, "finish_reason": None}]},
        {
            "choices": [{"index": 0, "delta": {}, "finish_reason": "stop"}],
            "usage": {
                "prompt_tokens": prompt_tokens,
                "completion_tokens": completion_tokens,
                "total_tokens": prompt_tokens + completion_tokens,
            },
        },
    ]


def tool_call(
    call_id: str,
    name: str,
    arguments: dict[str, Any] | None = None,
    *,
    index: int = 0,
) -> dict[str, Any]:
    """一条 OpenAI ``tool_calls`` 增量；``arguments`` 省略时序列化为 ``{}``。"""
    return {
        "index": index,
        "id": call_id,
        "type": "function",
        "function": {
            "name": name,
            "arguments": json.dumps(arguments or {}, ensure_ascii=False),
        },
    }


def tool_response(*calls: dict[str, Any]) -> list[dict[str, Any]]:
    """一轮只发工具调用的应答（`finish_reason: tool_calls`）。"""
    return [
        {
            "choices": [
                {"index": 0, "delta": {"tool_calls": list(calls)}, "finish_reason": None}
            ]
        },
        {"choices": [{"index": 0, "delta": {}, "finish_reason": "tool_calls"}]},
    ]


def content_text(content: object) -> str:
    """把消息 content 归一成文本：多模态数组取其中的 text 部分拼起来。"""
    if isinstance(content, str):
        return content
    if isinstance(content, list):
        return "".join(
            part.get("text", "")
            for part in content
            if isinstance(part, dict) and part.get("type") == "text"
        )
    return ""


def parse_events(chunks: Sequence[str]) -> list[tuple[str, dict[str, Any]]]:
    """把 SSE 分片解析成 ``(事件名, 数据)`` 序列。"""
    payload = "".join(chunks)
    result: list[tuple[str, dict[str, Any]]] = []
    for block in payload.split("\n\n"):
        if not block.strip() or block.startswith(":"):
            continue
        lines = dict(line.split(": ", 1) for line in block.splitlines())
        result.append((lines["event"], json.loads(lines["data"])))
    return result


def turn_events(chunks: Sequence[str]) -> list[tuple[str, dict[str, Any]]]:
    """一条流上的对话事件；`session_title` 是收尾的额外一次调用，这里排除。"""
    return [event for event in parse_events(chunks) if event[0] != "session_title"]
