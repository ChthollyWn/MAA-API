"""M67 T3：``response_format`` / ``max_tokens`` 两个请求体选项（规格 §4.2.1）。

断言口径是整份请求体：两个参数都不传时与 v2 逐字节相同（键集合、值都不变），
传了才多出同名字段。
"""

from __future__ import annotations

import asyncio
import json
from typing import Any

import httpx

from maa_api.agent.llm import OpenAICompatibleClient


def _chunk(data: dict[str, Any]) -> bytes:
    return f"data: {json.dumps(data, separators=(',', ':'))}\n\n".encode()


def _handler(bodies: list[dict[str, Any]]) -> httpx.MockTransport:
    """记录每次请求的 JSON 体，回一条能正常收流的 SSE。"""

    def handle(request: httpx.Request) -> httpx.Response:
        bodies.append(json.loads(request.content))
        payload = _chunk(
            {
                "choices": [
                    {"index": 0, "delta": {"content": "ok"}, "finish_reason": "stop"}
                ]
            }
        ) + b"data: [DONE]\n\n"
        return httpx.Response(
            200, headers={"content-type": "text/event-stream"}, content=payload
        )

    return httpx.MockTransport(handle)


def test_request_body_options() -> None:
    messages = [{"role": "user", "content": "在吗"}]
    tools = [{"type": "function", "function": {"name": "ping"}}]
    bodies: list[dict[str, Any]] = []

    async def scenario() -> None:
        # 短超时与只尝试一次：这条用例只关心请求体，不该被退避拖慢。
        async with httpx.AsyncClient(transport=_handler(bodies)) as http:
            client = OpenAICompatibleClient(
                base_url="https://llm.example/v1",
                api_key="sk-test",
                http_client=http,
                max_attempts=1,
            )
            async for _ in client.stream_chat_completion("m", messages, tools):
                pass
            async for _ in client.stream_chat_completion(
                "m",
                messages,
                tools,
                response_format={"type": "json_object"},
                max_tokens=64,
            ):
                pass

    asyncio.run(scenario())

    # 不传新参数：请求体与 v2 完全一致，没有多出任何键。
    assert bodies[0] == {
        "model": "m",
        "messages": messages,
        "tools": tools,
        "stream": True,
        "stream_options": {"include_usage": True},
    }
    # 传了就原样写进请求体。
    assert bodies[1] == {
        "model": "m",
        "messages": messages,
        "tools": tools,
        "stream": True,
        "stream_options": {"include_usage": True},
        "response_format": {"type": "json_object"},
        "max_tokens": 64,
    }
