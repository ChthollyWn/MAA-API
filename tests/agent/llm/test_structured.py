"""M67 T6：结构化输出（规格 §4.2.7、§4.5.3、§6）。

用例形态与 ``tests/agent/llm`` 下其他模块一致：同步测试 + ``asyncio.run``（本仓
没有 pytest-asyncio）。模型用 :class:`~tests.fakes.fake_llm.FakeLLM` 记录请求与
请求选项；路由与账本用轻量替身，``SCHEMAS`` 里的业务 schema 用 ``monkeypatch``
临时注册，不污染其他用例。
"""

from __future__ import annotations

import asyncio
import json
from typing import Any

import pytest
from pydantic import BaseModel

from maa_api.agent.llm.profiles import ResolvedProfile
from maa_api.agent.llm.structured import (
    SCHEMAS,
    STRUCTURED_PROMPT,
    STRUCTURED_RETRY_PROMPT,
    StructuredResult,
    complete_structured,
    json_schema_for,
)
from maa_api.domain.errors import AppError, ErrorCode
from tests.fakes.fake_llm import FakeLLM, text_response

MODEL = "structured-model"
BASE_URL = "https://llm.test/v1"
SCHEMA_NAME = "probe_demo"
AGENT_NAME = "structured-test"


class DemoModel(BaseModel):
    """测试用的临时 schema：``count`` 缺失时 pydantic 会给出字段路径。"""

    name: str
    count: int


def _profile(*, supports_json_schema: bool | None) -> ResolvedProfile:
    return ResolvedProfile(
        name="default",
        kind="chat",
        provider="openai_compatible",
        base_url=BASE_URL,
        api_key="sk-test",
        model=MODEL,
        context_window=None,
        max_output_tokens=None,
        supports_vision=False,
        supports_tools=True,
        supports_json_schema=supports_json_schema,
        dynamic_context_position="tail_system",
        price_input=None,
        price_cached_input=None,
        price_output=None,
        currency="CNY",
        embedding_dim=None,
        local_model_path=None,
        readonly=True,
    )


class _FakeHandle:
    """路由交出的客户端句柄；只记录是否被关闭。"""

    def __init__(self, client: FakeLLM) -> None:
        self.client = client
        self.closed = False

    async def aclose(self) -> None:
        self.closed = True


class _FakeRouter:
    """固定返回同一份档案；``client_for`` 每次交出一个新句柄。"""

    def __init__(self, profile: ResolvedProfile, client: FakeLLM) -> None:
        self.profile = profile
        self.client = client
        self.roles: list[str] = []
        self.handles: list[_FakeHandle] = []

    async def resolve(self, role: str) -> ResolvedProfile:
        self.roles.append(role)
        return self.profile

    def client_for(self, profile: ResolvedProfile) -> _FakeHandle:
        assert profile is self.profile
        handle = _FakeHandle(self.client)
        self.handles.append(handle)
        return handle


class _FakeLedger:
    """只记录 ``record`` 的入参；预算查询一律返回 0。"""

    def __init__(self) -> None:
        self.records: list[dict[str, Any]] = []

    async def record(self, **kwargs: Any) -> int:
        self.records.append(kwargs)
        return 0

    async def run_spent_micros(self, run_id: str) -> int:
        return 0

    async def spent_today_micros(self, *, now: Any = None) -> int:
        return 0

    def warn(self, **_kwargs: Any) -> None:
        pass


def _register_demo(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setitem(SCHEMAS, SCHEMA_NAME, DemoModel)


def _formatted_prompt() -> str:
    """按规格拼出的提示词正文；提示词必须逐字进模型可见文本。"""
    return STRUCTURED_PROMPT.format(
        schema=json.dumps(json_schema_for(SCHEMA_NAME), ensure_ascii=False)
    )


def _call(router: _FakeRouter, ledger: _FakeLedger, **overrides: Any) -> Any:
    values: dict[str, Any] = {
        "router": router,
        "ledger": ledger,
        "role": "worker",
        "schema_name": SCHEMA_NAME,
        "messages": [{"role": "user", "content": "生成一个演示对象"}],
        "agent_name": AGENT_NAME,
    }
    values.update(overrides)
    return complete_structured(**values)


# ---------------------------------------------------------------------------
# 两种模式的请求形状
# ---------------------------------------------------------------------------
def test_two_modes_send_the_right_request(monkeypatch: pytest.MonkeyPatch) -> None:
    """支持 JSON Schema 时带 ``response_format``；否则把提示词并入最后一条 user。"""
    _register_demo(monkeypatch)

    async def scenario() -> None:
        messages = [{"role": "user", "content": "生成一个演示对象"}]

        # 模式一：json_schema——请求选项里有 response_format，消息不加提示词。
        schema_llm = FakeLLM([text_response('{"name": "demo", "count": 3}')])
        schema_ledger = _FakeLedger()
        schema_router = _FakeRouter(_profile(supports_json_schema=True), schema_llm)
        schema_result = await _call(
            schema_router, schema_ledger, messages=messages, max_tokens=128
        )

        assert isinstance(schema_result, StructuredResult)
        assert schema_result.mode == "json_schema"
        assert schema_result.attempts == 1
        assert schema_result.value == DemoModel(name="demo", count=3)
        assert schema_result.usage.prompt_tokens == 7
        assert schema_result.usage.completion_tokens == 2
        assert schema_llm.requests[0][2] == []
        assert schema_llm.request_options == [
            {
                "response_format": {
                    "type": "json_schema",
                    "json_schema": {
                        "name": SCHEMA_NAME,
                        "schema": json_schema_for(SCHEMA_NAME),
                        "strict": False,
                    },
                },
                "max_tokens": 128,
            }
        ]
        # 入参没有被就地修改。
        assert schema_llm.requests[0][1] == messages
        assert schema_router.handles[0].closed is True
        assert schema_ledger.records[0]["role"] == "worker"
        assert schema_ledger.records[0]["agent_name"] == AGENT_NAME
        assert schema_ledger.records[0]["usage"].prompt_tokens == 7

        # 模式二：prompt——没有 response_format，提示词空一行后追加；正文带
        # ``` 围栏也能被 extract_json_object 剥掉。
        prompt_llm = FakeLLM(
            [text_response('```json\n{"name": "demo", "count": 1}\n```')]
        )
        prompt_ledger = _FakeLedger()
        prompt_router = _FakeRouter(_profile(supports_json_schema=False), prompt_llm)
        prompt_result = await _call(
            prompt_router, prompt_ledger, messages=messages
        )

        assert prompt_result.mode == "prompt"
        assert prompt_result.attempts == 1
        assert prompt_result.value == DemoModel(name="demo", count=1)
        assert "response_format" not in prompt_llm.request_options[0]
        sent = prompt_llm.requests[0][1]
        assert sent[-1]["role"] == "user"
        assert sent[-1]["content"].startswith("生成一个演示对象\n\n")
        assert sent[-1]["content"].endswith(_formatted_prompt())
        assert messages == [{"role": "user", "content": "生成一个演示对象"}]

    asyncio.run(scenario())


# ---------------------------------------------------------------------------
# 校验失败：带上原文与错误重试一次，仍失败则 502
# ---------------------------------------------------------------------------
def test_invalid_output_is_retried_then_fails(monkeypatch: pytest.MonkeyPatch) -> None:
    """第一次缺字段时第二次请求含 assistant 原文与重试提示；两次都不合法则报错。"""
    _register_demo(monkeypatch)

    async def scenario() -> None:
        invalid = '{"name": "demo"}'
        llm = FakeLLM(
            [text_response(invalid, prompt_tokens=5), text_response(invalid, prompt_tokens=6)]
        )
        ledger = _FakeLedger()
        router = _FakeRouter(_profile(supports_json_schema=True), llm)

        with pytest.raises(AppError) as excinfo:
            await _call(router, ledger)

        error = excinfo.value
        assert error.code == ErrorCode.STRUCTURED_OUTPUT_INVALID
        assert error.http_status == 502
        assert error.message == "模型两次都没有给出符合要求的结构化结果"
        assert error.details is not None
        assert error.details["schema"] == SCHEMA_NAME
        errors = error.details["errors"]
        assert errors and "count" in errors[0]
        assert len(errors) <= 5

        assert len(llm.requests) == 2
        retried = llm.requests[1][1]
        assert retried[-2] == {"role": "assistant", "content": invalid}
        assert retried[-1] == {
            "role": "user",
            "content": STRUCTURED_RETRY_PROMPT.format(errors="\n".join(errors)),
        }
        assert retried[-1]["content"].startswith("上一次的输出不符合要求：\n")
        assert "count" in retried[-1]["content"]

        # 两次请求都记了账；句柄都关闭；失败时不返回结果。
        assert [record["usage"].prompt_tokens for record in ledger.records] == [5, 6]
        assert [handle.closed for handle in router.handles] == [True, True]

    asyncio.run(scenario())


# ---------------------------------------------------------------------------
# response_format 被拒：回落提示词模式
# ---------------------------------------------------------------------------
def test_rejected_response_format_falls_back_to_prompt_mode(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """第一次 400，第二次没有 ``response_format``，结果 ``mode="prompt"``。"""
    _register_demo(monkeypatch)

    async def scenario() -> None:
        rejected = AppError(
            ErrorCode.LLM_REQUEST_FAILED,
            "LLM 上游请求失败",
            {"upstream_status": 400},
        )
        llm = FakeLLM([rejected, text_response('{"name": "demo", "count": 2}')])
        ledger = _FakeLedger()
        router = _FakeRouter(_profile(supports_json_schema=True), llm)

        result = await _call(router, ledger)

        assert result.mode == "prompt"
        assert result.attempts == 2
        assert result.value == DemoModel(name="demo", count=2)
        assert "response_format" in llm.request_options[0]
        assert "response_format" not in llm.request_options[1]
        assert llm.request_options[1] == {}
        # 第一次走 json_schema 模式（消息只有一条，没有提示词）；回落后的第二次
        # 带上了提示词。FakeLLM 记录的是消息列表的浅拷贝，回落时就地改写的仍是
        # 同一批消息 dict，所以这里只按「第二次请求里的提示词恰好出现一次」断言。
        assert len(llm.requests[0][1]) == 1
        retried_prompt = llm.requests[1][1][-1]["content"]
        assert retried_prompt.endswith(_formatted_prompt())
        assert retried_prompt.count(_formatted_prompt()) == 1
        # 400 的那次没有成功收流，不记账；只有成功的一次进账本与合计。
        assert len(ledger.records) == 1
        assert result.usage.prompt_tokens == 7
        assert result.usage.completion_tokens == 2
        assert [handle.closed for handle in router.handles] == [True, True]

    asyncio.run(scenario())
