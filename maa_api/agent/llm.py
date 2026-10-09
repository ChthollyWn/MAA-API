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
        "context_window_exceeded",
        "insufficient_quota",
        "invalid_api_key",
        "invalid_request_error",
        "model_not_found",
        "rate_limit_exceeded",
        "server_error",
        "string_above_max_length",
    }
)

#: 上游「上下文/输入过长」的已知错误码。本地不再预先按字符数裁剪会话历史，
#: 所以这条映射是用户唯一能看出「窗口装不下」的地方，而不是笼统的请求失败。
_CONTEXT_OVERFLOW_CODES = frozenset(
    {
        "context_length_exceeded",
        "context_window_exceeded",
        "string_above_max_length",
    }
)

#: 上游没给已知错误码时，按消息措辞兜底判定「上下文过长」。只匹配明确的措辞，
#: 避免把「模型名太长」这类无关 400 误判成上下文溢出。
_CONTEXT_OVERFLOW_MESSAGE_PATTERNS = (
    "context length",
    "context window",
    "context_length",
    "maximum context",
    "max context",
    "too many tokens",
    "token limit",
    "too long",
    "上下文长度",
    "上下文窗口",
    "超出上下文",
)

#: ``details.upstream_message`` 的长度上限：够定位「模型名不存在」「图片格式不支持」
#: 这类 400，又不至于让一段上游正文塞满错误响应。``details.upstream_error``
#: （连接层失败）沿用同一个上限。
MAX_UPSTREAM_MESSAGE_CHARS = 300

#: 连接层失败的已知成因：``str(exc)`` 命中关键词就给一句中文结论，用户不必自己
#: 从 ``[SSL: CERTIFICATE_VERIFY_FAILED]`` 这种原文里认。顺序即优先级。
_TRANSPORT_CAUSE_LABELS: tuple[tuple[tuple[str, ...], str], ...] = (
    (
        (
            "certificate_verify_failed",
            "certificate verify failed",
            "sslcertverificationerror",
            "self-signed certificate",
            "self signed certificate",
            "unable to get local issuer",
        ),
        "TLS 证书校验失败",
    ),
    (
        (
            "nodename nor servname",
            "name or service not known",
            "temporary failure in name resolution",
            "getaddrinfo",
            "name resolution",
        ),
        "域名解析失败",
    ),
    (("connection refused",), "连接被拒绝"),
    (
        (
            "connection reset",
            "connection aborted",
            "broken pipe",
            "server disconnected",
            "incomplete chunked read",
            "connection closed",
        ),
        "与上游的连接中断",
    ),
    (("proxy",), "代理连接失败"),
    (("timed out", "timeout"), "连接超时"),
)


def _describe_transport_error(exc: httpx.HTTPError) -> str:
    """把连接层异常压成一句可读原因（先脱敏，再截断）。

    连接层失败（TLS 校验、域名解析、连接被拒、流中断）在 ``str(exc)`` 里已经有
    可读原文，但没有上游响应，所以 ``details`` 里不会有 ``upstream_status``。
    命中已知措辞时补一句中文结论；都不命中就退回异常类名加原文，至少还能看出
    是 ``ConnectError`` 还是 ``RemoteProtocolError``。原文可能回显 API Key 或
    ``Authorization`` 头（``details`` 会原样出接口），所以与 ``upstream_message``
    走同一套 ``redact_remote_data`` 脱敏。
    """
    raw = str(redact_remote_data(str(exc).strip()))
    haystack = raw.casefold()
    label = next(
        (
            candidate
            for patterns, candidate in _TRANSPORT_CAUSE_LABELS
            if any(pattern in haystack for pattern in patterns)
        ),
        None,
    )
    if label and raw:
        described = f"{label}（{raw}）"
    elif label:
        described = label
    elif raw:
        described = f"{type(exc).__name__}：{raw}"
    else:
        described = type(exc).__name__
    return described[:MAX_UPSTREAM_MESSAGE_CHARS]


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
                    if self._is_context_overflow(error_code, upstream_message):
                        # 本地不再按字符数预裁剪会话历史，上下文装不下时只能靠这里
                        # 把上游的拒绝翻译成可读的 400，而不是笼统的「请求失败」。
                        raise AppError(
                            ErrorCode.LLM_CONTEXT_OVERFLOW,
                            "会话历史或知识库内容超出模型上下文长度，请开启新会话或缩短内容",
                            details,
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
        except httpx.HTTPError as exc:
            # 连接层失败没有上游响应，``details`` 里不会有 ``upstream_status``；此前
            # ``from None`` 把唯一的原因（TLS 校验、DNS、拒连、流中断）整个丢掉，
            # 界面只剩「LLM 上游请求失败」，与「模型不支持工具调用」无法区分。
            raise AppError(
                ErrorCode.LLM_REQUEST_FAILED,
                "LLM 上游请求失败",
                {"upstream_error": _describe_transport_error(exc)},
            ) from None

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
    def _is_context_overflow(
        error_code: str | None, upstream_message: str | None
    ) -> bool:
        """上游是否因上下文/输入过长而拒绝，需要映射成 ``LLM_CONTEXT_OVERFLOW``。

        先认已知错误码；上游没给（或给的是 ``invalid_request_error`` 这类通用码）时，
        再按消息措辞兜底。``upstream_message`` 已经过脱敏与截断。
        """
        if error_code in _CONTEXT_OVERFLOW_CODES:
            return True
        if not upstream_message:
            return False
        haystack = upstream_message.casefold()
        return any(pattern in haystack for pattern in _CONTEXT_OVERFLOW_MESSAGE_PATTERNS)

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
