"""OpenAI-compatible streaming client and API-key encryption contracts."""

from __future__ import annotations

import asyncio
import importlib
import json
import logging
from collections.abc import AsyncIterator
from typing import Any

import httpx
import pytest
from cryptography.fernet import Fernet

from maa_api.domain.errors import AppError, ErrorCode
from maa_api.settings import Settings


def _api_key_primitives():
    try:
        module = importlib.import_module("maa_api.agent.secrets")
        return (
            module.LLMEncryptionKeyInvalid,
            module.decrypt_api_key,
            module.encrypt_api_key,
        )
    except (ImportError, AttributeError):
        pytest.fail("LLM API-key encryption behavior is not implemented", pytrace=False)


def _client_type():
    try:
        return importlib.import_module("maa_api.agent.llm").OpenAICompatibleClient
    except (ImportError, AttributeError):
        pytest.fail("OpenAICompatibleClient is not implemented", pytrace=False)


def test_api_key_ciphertext_round_trips_and_rejects_wrong_key_and_tampering() -> None:
    LLMEncryptionKeyInvalid, decrypt_api_key, encrypt_api_key = _api_key_primitives()
    key = Fernet.generate_key().decode("ascii")
    other_key = Fernet.generate_key().decode("ascii")
    ciphertext = encrypt_api_key("sk-test-value", key)

    assert ciphertext.startswith("enc:v1:")
    assert "sk-test-value" not in ciphertext
    assert decrypt_api_key(ciphertext, key) == "sk-test-value"
    with pytest.raises(LLMEncryptionKeyInvalid):
        decrypt_api_key(ciphertext, other_key)
    with pytest.raises(LLMEncryptionKeyInvalid):
        decrypt_api_key(ciphertext[:-2] + "xx", key)


def test_api_key_encryption_rejects_missing_or_invalid_fernet_key() -> None:
    LLMEncryptionKeyInvalid, decrypt_api_key, encrypt_api_key = _api_key_primitives()
    with pytest.raises(LLMEncryptionKeyInvalid):
        encrypt_api_key("sk-test-value", "")
    with pytest.raises(LLMEncryptionKeyInvalid):
        decrypt_api_key("enc:v1:not-a-fernet-token", "not-a-fernet-key")


def test_llm_settings_default_to_deepseek_flash() -> None:
    settings = Settings()

    assert settings.llm.base_url == "https://api.deepseek.com"
    assert settings.llm.api_key == ""
    assert settings.llm.model == "deepseek-flash"


def _chunk(data: dict[str, Any]) -> bytes:
    return f"data: {json.dumps(data, separators=(',', ':'))}\n\n".encode()


async def _collect(chunks: AsyncIterator[dict[str, Any]]) -> list[dict[str, Any]]:
    return [chunk async for chunk in chunks]


def test_stream_yields_openai_sse_chunks_in_order_and_preserves_usage_and_image_request() -> None:
    chunks = [
        {
            "id": "chatcmpl-1",
            "object": "chat.completion.chunk",
            "choices": [
                {
                    "index": 0,
                    "delta": {
                        "content": "I can help.",
                        "tool_calls": [
                            {
                                "index": 0,
                                "id": "call_1",
                                "type": "function",
                                "function": {
                                    "name": "submit_pipeline",
                                    "arguments": '{"tasks":[',
                                },
                            }
                        ],
                    },
                    "finish_reason": None,
                }
            ],
        },
        {
            "id": "chatcmpl-1",
            "object": "chat.completion.chunk",
            "choices": [
                {
                    "index": 0,
                    "delta": {
                        "content": "What would you like to run?",
                        "tool_calls": [
                            {
                                "index": 0,
                                "function": {"arguments": '{"name":"Fight"}]}'},
                            }
                        ],
                    },
                    "finish_reason": None,
                }
            ],
        },
        {
            "id": "chatcmpl-1",
            "object": "chat.completion.chunk",
            "choices": [
                {"index": 0, "delta": {}, "finish_reason": "tool_calls"}
            ],
            "usage": {
                "prompt_tokens": 37,
                "completion_tokens": 12,
                "total_tokens": 49,
                "prompt_tokens_details": {"cached_tokens": 5},
            },
        },
    ]
    image_data_url = "data:image/png;base64,aGVsbG8="
    messages = [
        {
            "role": "user",
            "content": [
                {"type": "text", "text": "Describe this screenshot."},
                {
                    "type": "image_url",
                    "image_url": {"url": image_data_url},
                },
            ],
        }
    ]
    request_json: dict[str, Any] = {}
    auth_headers: list[str] = []
    request_urls: list[str] = []

    def handler(request: httpx.Request) -> httpx.Response:
        nonlocal request_json
        request_json = json.loads(request.content)
        auth_headers.append(request.headers["authorization"])
        request_urls.append(str(request.url))
        body = b"".join(_chunk(chunk) for chunk in chunks) + b"data: [DONE]\n\n"
        return httpx.Response(
            200, headers={"content-type": "text/event-stream"}, content=body
        )

    async def scenario() -> list[dict[str, Any]]:
        async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as http:
            client = _client_type()(
                base_url="https://llm.example/v1/",
                api_key="sk-never-log-this",
                http_client=http,
            )
            return await _collect(
                client.stream_chat_completion(
                    "vision-model",
                    messages,
                    [{"type": "function", "function": {"name": "submit_pipeline"}}],
                )
            )

    observed = asyncio.run(scenario())

    assert observed == chunks
    assert observed[2]["usage"]["total_tokens"] == 49
    assert observed[2]["usage"]["prompt_tokens_details"] == {"cached_tokens": 5}
    assert request_json["model"] == "vision-model"
    assert request_json["messages"] == messages
    assert request_json["messages"][0]["content"][1]["image_url"]["url"] == image_data_url
    assert request_json["stream"] is True
    assert request_json["stream_options"] == {"include_usage": True}
    assert auth_headers == ["Bearer sk-never-log-this"]
    assert request_urls == ["https://llm.example/v1/chat/completions"]


def test_non_success_response_maps_to_stable_llm_request_error() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(
            500,
            json={"error": {"code": "server_error", "message": "private body"}},
        )

    async def scenario() -> None:
        async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as http:
            client = _client_type()(
                base_url="https://llm.example/v1", api_key="sk-hidden", http_client=http
            )
            with pytest.raises(AppError) as exc_info:
                await _collect(client.stream_chat_completion("m", [], []))
        assert exc_info.value.code == ErrorCode.LLM_REQUEST_FAILED
        assert exc_info.value.details == {
            "upstream_status": 500,
            "upstream_code": "server_error",
            "upstream_message": "private body",
        }
        assert "private body" not in str(exc_info.value)
        assert "sk-hidden" not in repr(exc_info.value)

    asyncio.run(scenario())


@pytest.mark.parametrize(
    ("payload", "expected_code"),
    [
        (
            {
                "code": "context_length_exceeded",
                "message": (
                    "This model's maximum context length is 65536 tokens, "
                    "however you requested 70000 tokens"
                ),
            },
            "context_length_exceeded",
        ),
        (
            {
                "code": "context_window_exceeded",
                "message": "the request exceeds the model's context window",
            },
            "context_window_exceeded",
        ),
        (
            {
                "code": "string_above_max_length",
                "message": "the input string is above the maximum length",
            },
            "string_above_max_length",
        ),
        # 上游只给通用码时按消息措辞兜底（本地不再预裁剪，这条是唯一能看出窗口装不下的地方）。
        (
            {
                "code": "invalid_request_error",
                "message": "This model's maximum context length is 65536 tokens",
            },
            "invalid_request_error",
        ),
        ({"message": "prompt is too long for this model"}, None),
    ],
    ids=[
        "context_length_exceeded",
        "context_window_exceeded",
        "string_above_max_length",
        "generic-code-with-context-message",
        "no-code-too-long-message",
    ],
)
def test_upstream_context_overflow_maps_to_llm_context_overflow(
    payload: dict[str, Any], expected_code: str | None
) -> None:
    """上游因上下文过长拒绝时给可读的 400 LLM_CONTEXT_OVERFLOW，details 保留原话。"""

    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(400, json={"error": payload})

    async def scenario() -> AppError:
        async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as http:
            client = _client_type()(
                base_url="https://llm.example/v1", api_key="sk-hidden", http_client=http
            )
            with pytest.raises(AppError) as exc_info:
                await _collect(client.stream_chat_completion("m", [], []))
        return exc_info.value

    error = asyncio.run(scenario())
    assert error.code == ErrorCode.LLM_CONTEXT_OVERFLOW
    assert error.http_status == 400
    assert "超出模型上下文长度" in (error.message or "")
    assert error.details["upstream_status"] == 400
    assert error.details["upstream_message"] == payload["message"]
    assert error.details.get("upstream_code") == expected_code


def test_rate_limit_and_unrelated_upstream_errors_keep_their_mapping() -> None:
    """429 与「非上下文」的 4xx/5xx 行为不变：只有上下文过长才变成 400。"""

    def rate_limited(request: httpx.Request) -> httpx.Response:
        return httpx.Response(
            429,
            headers={"Retry-After": "7"},
            json={
                "error": {
                    "code": "rate_limit_exceeded",
                    "message": "Too many requests; retry later.",
                }
            },
        )

    def unrelated(request: httpx.Request) -> httpx.Response:
        return httpx.Response(
            400,
            json={
                "error": {
                    "code": "invalid_request_error",
                    "message": "You have uploaded an unsupported image",
                }
            },
        )

    async def scenario(handler) -> AppError:
        async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as http:
            client = _client_type()(
                base_url="https://llm.example/v1", api_key="sk-hidden", http_client=http
            )
            with pytest.raises(AppError) as exc_info:
                await _collect(client.stream_chat_completion("m", [], []))
        return exc_info.value

    limited = asyncio.run(scenario(rate_limited))
    assert limited.code == ErrorCode.LLM_RATE_LIMITED
    assert limited.http_status == 429
    assert limited.headers == {"Retry-After": "7"}

    unrelated_error = asyncio.run(scenario(unrelated))
    assert unrelated_error.code == ErrorCode.LLM_REQUEST_FAILED
    assert unrelated_error.http_status == 502


def test_invalid_model_name_surfaces_upstream_message_without_credentials() -> None:
    """模型名写错时要能在 details 里看到上游原话，且凭据不得回显。"""

    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(
            400,
            json={
                "error": {
                    "code": "invalid_request_error",
                    "message": (
                        "The supported API model names are deepseek-flash, deepseek-v4-pro, "
                        "but you passed deepseek-v4.1-flash. Authorization: Bearer sk-live-secret"
                    ),
                }
            },
        )

    async def scenario() -> None:
        async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as http:
            client = _client_type()(
                base_url="https://llm.example/v1",
                api_key="sk-live-secret",
                http_client=http,
            )
            with pytest.raises(AppError) as exc_info:
                await _collect(client.stream_chat_completion("deepseek-v4.1-flash", [], []))
        assert exc_info.value.details == {
            "upstream_status": 400,
            "upstream_code": "invalid_request_error",
            "upstream_message": (
                "The supported API model names are deepseek-flash, deepseek-v4-pro, "
                "but you passed deepseek-v4.1-flash. Authorization: Bearer [REDACTED]"
            ),
        }
        assert "sk-live-secret" not in repr(exc_info.value)

    asyncio.run(scenario())


def test_upstream_message_is_truncated_and_absent_when_empty() -> None:
    long_message = "x" * 1000

    def long_handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(
            400, json={"error": {"code": "invalid_request_error", "message": long_message}}
        )

    def empty_handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(400, json={"error": {"code": "invalid_request_error", "message": "  "}})

    async def scenario(handler) -> dict[str, Any]:
        async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as http:
            client = _client_type()(
                base_url="https://llm.example/v1", api_key="sk-hidden", http_client=http
            )
            with pytest.raises(AppError) as exc_info:
                await _collect(client.stream_chat_completion("m", [], []))
        return exc_info.value.details

    details = asyncio.run(scenario(long_handler))
    assert len(details["upstream_message"]) == 300
    assert asyncio.run(scenario(empty_handler)) == {
        "upstream_status": 400,
        "upstream_code": "invalid_request_error",
    }


@pytest.mark.parametrize(
    "upstream_code",
    ["sk-upstream-secret", "invalid-sk-upstream-secret"],
)
def test_unknown_upstream_error_code_cannot_echo_api_key(upstream_code: str) -> None:
    api_key = "sk-upstream-secret"

    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(
            401,
            json={"error": {"code": upstream_code, "message": "unauthorized"}},
        )

    async def scenario() -> None:
        async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as http:
            client = _client_type()(
                base_url="https://llm.example/v1", api_key=api_key, http_client=http
            )
            with pytest.raises(AppError) as exc_info:
                await _collect(client.stream_chat_completion("m", [], []))
        assert exc_info.value.code == ErrorCode.LLM_REQUEST_FAILED
        assert exc_info.value.details == {
            "upstream_status": 401,
            "upstream_message": "unauthorized",
        }
        assert api_key not in repr(exc_info.value)

    asyncio.run(scenario())


@pytest.mark.parametrize("bad_sse", [b"data: {\"broken\":\n\n", b"data: not-json\n\n"])
def test_malformed_sse_payload_maps_to_stable_llm_request_error(bad_sse: bytes) -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(
            200, headers={"content-type": "text/event-stream"}, content=bad_sse
        )

    async def scenario() -> None:
        async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as http:
            client = _client_type()(
                base_url="https://llm.example/v1", api_key="sk-hidden", http_client=http
            )
            with pytest.raises(AppError) as exc_info:
                await _collect(client.stream_chat_completion("m", [], []))
        assert exc_info.value.code == ErrorCode.LLM_REQUEST_FAILED
        assert "sk-hidden" not in repr(exc_info.value)

    asyncio.run(scenario())


def test_upstream_timeout_maps_to_llm_timeout() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        raise httpx.ReadTimeout("private transport detail", request=request)

    async def scenario() -> None:
        async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as http:
            client = _client_type()(
                base_url="https://llm.example/v1", api_key="sk-hidden", http_client=http
            )
            with pytest.raises(AppError) as exc_info:
                await _collect(client.stream_chat_completion("m", [], []))
        assert exc_info.value.code == ErrorCode.LLM_TIMEOUT
        assert "sk-hidden" not in repr(exc_info.value)

    asyncio.run(scenario())


def _transport_failure(error: httpx.HTTPError) -> AppError:
    """把 mock transport 抛出的连接层异常跑成 ``AppError``，供下面几条用例共用。"""

    def handler(request: httpx.Request) -> httpx.Response:
        raise error

    async def scenario() -> AppError:
        async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as http:
            client = _client_type()(
                base_url="https://llm.example/v1", api_key="sk-hidden", http_client=http
            )
            with pytest.raises(AppError) as exc_info:
                await _collect(client.stream_chat_completion("m", [], []))
        return exc_info.value

    return asyncio.run(scenario())


@pytest.mark.parametrize(
    ("error", "expected_label"),
    [
        (
            httpx.ConnectError(
                "[SSL: CERTIFICATE_VERIFY_FAILED] certificate verify failed: "
                "self-signed certificate in certificate chain (_ssl.c:1028)"
            ),
            "TLS 证书校验失败",
        ),
        (
            httpx.ConnectError(
                "[Errno 8] nodename nor servname provided, or not known"
            ),
            "域名解析失败",
        ),
        (httpx.ConnectError("[Errno 61] Connection refused"), "连接被拒绝"),
        (
            httpx.RemoteProtocolError("Server disconnected without sending a response."),
            "与上游的连接中断",
        ),
    ],
    ids=["tls-verify", "name-resolution", "connection-refused", "stream-aborted"],
)
def test_transport_failure_keeps_the_real_cause_in_details(
    error: httpx.HTTPError, expected_label: str
) -> None:
    """连接层失败没有上游响应，真实原因必须落在 ``details.upstream_error`` 里。

    此前 ``except httpx.HTTPError`` 用 ``from None`` 硬编码文案，TLS 证书校验失败、
    域名解析失败、连接被拒全都显示成一句「LLM 上游请求失败」，无法与「模型能力不足」
    区分（``.venv`` 被重建丢掉 certifi CA 包时就是这个症状）。
    """
    mapped = _transport_failure(error)

    assert mapped.code == ErrorCode.LLM_REQUEST_FAILED
    assert mapped.message == "LLM 上游请求失败"
    assert set(mapped.details) == {"upstream_error"}
    described = mapped.details["upstream_error"]
    assert described.startswith(f"{expected_label}（")
    assert str(error) in described
    assert "sk-hidden" not in repr(mapped)


def test_transport_cause_falls_back_to_the_exception_name_and_is_redacted_and_truncated() -> None:
    """陌生措辞退回「异常类名：原文」，原文同样要脱敏、截断。"""
    assert _transport_failure(httpx.ReadError("")).details == {
        "upstream_error": "ReadError"
    }

    leaked = _transport_failure(
        httpx.ConnectError("handshake failed; Authorization: Bearer sk-live-secret")
    )
    assert "sk-live-secret" not in repr(leaked)
    assert leaked.details["upstream_error"].startswith("ConnectError：")
    assert "[REDACTED]" in leaked.details["upstream_error"]

    long_message = _transport_failure(httpx.ReadError("y" * 1000))
    assert len(long_message.details["upstream_error"]) == 300
    assert long_message.details["upstream_error"].startswith("ReadError：")


def test_client_never_logs_api_key_or_authorization_header(caplog: pytest.LogCaptureFixture) -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(
            401,
            json={"error": {"code": "invalid_api_key", "message": "invalid"}},
        )

    async def scenario() -> None:
        async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as http:
            client = _client_type()(
                base_url="https://llm.example/v1",
                api_key="sk-do-not-log",
                http_client=http,
            )
            with pytest.raises(AppError):
                await _collect(client.stream_chat_completion("m", [], []))

    caplog.set_level(logging.DEBUG)
    asyncio.run(scenario())
    assert "sk-do-not-log" not in caplog.text
    assert "Authorization" not in caplog.text
