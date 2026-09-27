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


def test_llm_settings_default_to_deepseek_v41_flash() -> None:
    settings = Settings()

    assert settings.llm.base_url == "https://api.deepseek.com"
    assert settings.llm.api_key == ""
    assert settings.llm.model == "deepseek-v4.1-flash"


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
        }
        assert "private body" not in str(exc_info.value)
        assert "sk-hidden" not in repr(exc_info.value)

    asyncio.run(scenario())


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
        assert exc_info.value.details == {"upstream_status": 401}
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
