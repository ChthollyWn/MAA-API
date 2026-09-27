"""HTTP streaming client for OpenAI-compatible Chat Completions APIs."""

from __future__ import annotations

import json
from collections.abc import AsyncIterator, Sequence
from typing import Any

import httpx

from maa_api.agent.redaction import redact_remote_data
from maa_api.domain.errors import AppError, ErrorCode

__all__ = ["OpenAICompatibleClient"]

_KNOWN_UPSTREAM_ERROR_CODES = frozenset(
    {
        "context_length_exceeded",
        "insufficient_quota",
        "invalid_api_key",
        "invalid_request_error",
        "model_not_found",
        "rate_limit_exceeded",
        "server_error",
    }
)

#: ``details.upstream_message`` 的长度上限：够定位「模型名不存在」「图片格式不支持」
#: 这类 400，又不至于让一段上游正文塞满错误响应。
MAX_UPSTREAM_MESSAGE_CHARS = 300


class OpenAICompatibleClient:
    """Yield raw OpenAI SSE chunk dictionaries without executing tool calls."""

    def __init__(
        self,
        *,
        base_url: str,
        api_key: str,
        http_client: httpx.AsyncClient | None = None,
        timeout_seconds: float = 120.0,
    ) -> None:
        self.base_url = base_url.rstrip("/")
        self.api_key = api_key
        self.timeout_seconds = timeout_seconds
        self._http_client = (
            http_client
            if http_client is not None
            else httpx.AsyncClient(timeout=timeout_seconds)
        )
        self._owns_http_client = http_client is None

    @property
    def _completion_url(self) -> str:
        if self.base_url.endswith("/chat/completions"):
            return self.base_url
        return f"{self.base_url}/chat/completions"

    async def aclose(self) -> None:
        """Close the internally-created HTTP client, if this instance owns it."""
        if self._owns_http_client:
            await self._http_client.aclose()

    async def __aenter__(self) -> OpenAICompatibleClient:
        return self

    async def __aexit__(self, *_: object) -> None:
        await self.aclose()

    async def stream_chat_completion(
        self,
        model: str,
        messages: Sequence[dict[str, Any]],
        tools: Sequence[dict[str, Any]],
    ) -> AsyncIterator[dict[str, Any]]:
        """Yield each parsed OpenAI SSE chunk in wire order, including usage chunks."""
        if not self.base_url or not self.api_key:
            raise AppError(ErrorCode.LLM_NOT_CONFIGURED, "LLM 配置不完整")

        body = {
            "model": model,
            "messages": messages,
            "tools": tools,
            "stream": True,
            "stream_options": {"include_usage": True},
        }
        headers = {
            "Authorization": f"Bearer {self.api_key}",
            "Accept": "text/event-stream",
        }
        try:
            async with self._http_client.stream(
                "POST",
                self._completion_url,
                json=body,
                headers=headers,
                timeout=self.timeout_seconds,
            ) as response:
                if not 200 <= response.status_code < 300:
                    await response.aread()
                    error_code = self._upstream_error_code(response)
                    details: dict[str, Any] = {"upstream_status": response.status_code}
                    if error_code is not None:
                        details["upstream_code"] = error_code
                    upstream_message = self._upstream_error_message(response)
                    if upstream_message is not None:
                        details["upstream_message"] = upstream_message
                    if response.status_code == 429:
                        retry_after = response.headers.get("Retry-After")
                        raise AppError(
                            ErrorCode.LLM_RATE_LIMITED,
                            "LLM 上游请求频率受限",
                            details,
                            {"Retry-After": retry_after} if retry_after else None,
                        )
                    raise AppError(
                        ErrorCode.LLM_REQUEST_FAILED,
                        "LLM 上游请求失败",
                        details,
                    )

                data_lines: list[str] = []
                done = False
                async for line in response.aiter_lines():
                    if not line:
                        if data_lines:
                            data = "\n".join(data_lines)
                            data_lines.clear()
                            if data == "[DONE]":
                                done = True
                                break
                            yield self._parse_chunk(data)
                        continue
                    if line.startswith(":") or line.startswith(
                        ("event:", "id:", "retry:")
                    ):
                        continue
                    if line == "data":
                        data_lines.append("")
                        continue
                    if line.startswith("data:"):
                        data_lines.append(line[5:].lstrip())
                        continue
                    raise AppError(
                        ErrorCode.LLM_REQUEST_FAILED,
                        "LLM 上游返回了无效的流式数据",
                    )
                if data_lines and not done:
                    data = "\n".join(data_lines)
                    if data == "[DONE]":
                        done = True
                    else:
                        yield self._parse_chunk(data)
                if not done:
                    raise AppError(
                        ErrorCode.LLM_REQUEST_FAILED,
                        "LLM 上游返回了未完成的流式数据",
                    )
        except httpx.TimeoutException:
            raise AppError(ErrorCode.LLM_TIMEOUT, "LLM 上游请求超时") from None
        except httpx.HTTPError:
            raise AppError(ErrorCode.LLM_REQUEST_FAILED, "LLM 上游请求失败") from None

    @staticmethod
    def _upstream_error_code(response: httpx.Response) -> str | None:
        try:
            payload = response.json()
        except (ValueError, UnicodeDecodeError):
            return None
        if not isinstance(payload, dict):
            return None
        error = payload.get("error")
        if not isinstance(error, dict):
            return None
        code = error.get("code")
        if isinstance(code, str) and code in _KNOWN_UPSTREAM_ERROR_CODES:
            return code
        return None

    @staticmethod
    def _upstream_error_message(response: httpx.Response) -> str | None:
        """取上游 ``error.message`` 作为可读原因，已脱敏且截断。

        ``AppError.details`` 会经 ``app_error`` 原样出接口，所以这里必须沿用与
        发往模型的上下文相同的脱敏规则。上游正文可以任意长，也可能为空。
        """
        try:
            payload = response.json()
        except (ValueError, UnicodeDecodeError):
            return None
        if not isinstance(payload, dict):
            return None
        error = payload.get("error")
        if not isinstance(error, dict):
            return None
        message = error.get("message")
        if not isinstance(message, str) or not message.strip():
            return None
        return str(redact_remote_data(message.strip()))[:MAX_UPSTREAM_MESSAGE_CHARS]

    @staticmethod
    def _parse_chunk(data: str) -> dict[str, Any]:
        try:
            chunk = json.loads(data)
        except json.JSONDecodeError:
            raise AppError(
                ErrorCode.LLM_REQUEST_FAILED,
                "LLM 上游返回了无效的流式数据",
            ) from None
        if not isinstance(chunk, dict):
            raise AppError(
                ErrorCode.LLM_REQUEST_FAILED,
                "LLM 上游返回了无效的流式数据",
            )
        return chunk
