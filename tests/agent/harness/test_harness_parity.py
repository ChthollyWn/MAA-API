"""M65 T1：v2 会话、诊断与侦察的行为对齐基线。

16 个场景把改动前的 SSE 帧、模型请求、落库消息、会话计数、审计与返回值录进
``golden/legacy_parity.json``。golden 提交后即冻结：任何任务都只能用代码适配它，
不许设 ``MAA_REGENERATE_PARITY=1``，也不许手改。测试不能导入
``maa_api.agent.harness`` 与 ``maa_api.agent.specs``（搬家前它们还不存在），门面与
recon 只使用公开名字：``AgentRuntime``、``INCIDENT_ALLOWED_TOOLS``、``ReconDeps``、
``ReconStep``、``budget_from_settings``、``run_recon``。
"""

from __future__ import annotations

import asyncio
import hashlib
import json
import os
import re
from collections.abc import Callable, Iterable, Mapping, Sequence
from pathlib import Path
from typing import Any

import pytest
from pydantic import BaseModel, ConfigDict
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker
from sqlmodel import SQLModel

from maa_api.agent.confirmation import ConfirmationService
from maa_api.agent.policy import PolicyEngine
from maa_api.agent.questions import QuestionService
from maa_api.agent.recon import explore
from maa_api.agent.recon.runner import (
    ReconDeps,
    ReconStep,
    budget_from_settings,
    run_recon,
)
from maa_api.agent.registry import ToolDefinition, ToolRegistry, ToolRisk
from maa_api.agent.runtime import INCIDENT_ALLOWED_TOOLS, AgentRuntime
from maa_api.agent.tools import build_registry
from maa_api.db import session as db_session
from maa_api.db.models import AgentAudit, AgentMessage, AgentSession
from maa_api.db.repositories.agent import AgentMessageRepository, AgentSessionRepository
from maa_api.domain.errors import AppError, ErrorCode
from maa_api.settings import AgentSettings, LLMSettings, Settings, WorkspaceSettings
from tests.fakes.fake_llm import FakeLLM, parse_events, text_response, tool_response

GOLDEN_PATH = Path(__file__).parent / "golden" / "legacy_parity.json"
REGENERATE_ENV = "MAA_REGENERATE_PARITY"
SESSION_ID, MODEL, BASE_URL = "parity", "parity-model", "https://llm.test/v1"
#: 每次运行都会变的 UUID（确认 / 提问的 id）。
_UUID = re.compile(r"[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}")
#: 状态摘要里的「现在」；它在 system 正文里，按 JSON 文本替换。
_NOW = re.compile(r'"now": "[^"]*"')
#: 固定的知识库段：真实知识库读的是工作区文件，这里只要一段稳定文本。
_KNOWLEDGE = "知识库：这是行为对齐基线的固定指引。"
_DEFAULT_WORKSPACE_FILE = "app.py"
_DEFAULT_WORKSPACE_BODY = "print('hi')\n"


def _json_copy(value: Any) -> Any:
    """深拷贝到纯 JSON（与测试文件里其余记录口径一致）。"""

    return json.loads(json.dumps(value, ensure_ascii=False, default=str))


class _Normalizer:
    """每次运行都会变的值换成稳定占位；同一场景里同一个值总是同一个占位。"""

    def __init__(self, literals: dict[str, str] | None = None) -> None:
        self._literals = sorted(
            (literals or {}).items(), key=lambda item: -len(item[0])
        )  # 长的先换
        self._uuids: dict[str, str] = {}
        self._audits: dict[int, str] = {}

    def text(self, value: str) -> str:
        for literal, placeholder in self._literals:
            if literal:
                value = value.replace(literal, placeholder)
        value = _NOW.sub('"now": "<now>"', value)
        return _UUID.sub(
            lambda match: self._uuids.setdefault(
                match.group(0), f"<uuid:{len(self._uuids) + 1}>"
            ),
            value,
        )

    def audit(self, value: Any) -> Any:
        if isinstance(value, int) and not isinstance(value, bool):
            return self._audits.setdefault(
                value, f"<audit:{len(self._audits) + 1}>"
            )
        return value

    def value(self, data: Any) -> Any:
        if isinstance(data, Mapping):
            normalized: dict[str, Any] = {}
            for key, item in data.items():
                if key == "expires_at" and item:
                    normalized[key] = "<ts>"
                elif key == "audit_id":
                    normalized[key] = self.audit(item)
                else:
                    normalized[key] = self.value(item)
            return normalized
        if isinstance(data, (list, tuple)):
            return [self.value(item) for item in data]
        if isinstance(data, str):
            return self.text(data)
        return data


def _normalizer_for(tmp: Path) -> _Normalizer:
    """recon 的 list_files 结果里有绝对路径，两种写法都换成 ``<ws>``。"""

    return _Normalizer(
        {
            str(tmp.resolve()): "<ws>",
            str(tmp): "<ws>",
        }
    )


def _request(
    model: str,
    messages: Sequence[Mapping[str, Any]],
    tools: Sequence[Mapping[str, Any]],
    norm: _Normalizer,
) -> dict[str, Any]:
    """一次模型请求：system 正文只留哈希、长度与尾巴（含 ``now``，必须先规范化）。"""

    normalized = [norm.value(dict(message)) for message in messages]
    if (
        normalized
        and normalized[0].get("role") == "system"
        and isinstance(normalized[0].get("content"), str)
    ):
        content = normalized[0]["content"]
        normalized[0] = {
            "role": "system",
            "sha256": hashlib.sha256(content.encode("utf-8")).hexdigest(),
            "length": len(content),
            "tail": content[-300:],
        }
    return {"model": model, "messages": normalized, "tools": norm.value(list(tools))}


def _call(
    call_id: str, name: str, arguments: Mapping[str, Any] | None = None, *, index: int = 0
) -> dict[str, Any]:
    """一条 OpenAI ``tool_calls`` 增量。"""

    return {
        "index": index,
        "id": call_id,
        "type": "function",
        "function": {
            "name": name,
            "arguments": json.dumps(arguments or {}, ensure_ascii=False),
        },
    }


class _EmptyParams(BaseModel):
    model_config = ConfigDict(extra="forbid")


class _LookupParams(BaseModel):
    model_config = ConfigDict(extra="forbid")

    key: str


class _WriteParams(BaseModel):
    model_config = ConfigDict(extra="forbid")

    value: str


class _ClickParams(BaseModel):
    model_config = ConfigDict(extra="forbid")

    x: int
    y: int


def _tool(
    name: str,
    *,
    risk: ToolRisk = ToolRisk.SAFE,
    read_only: bool = False,
    params: type[BaseModel] | None = None,
    group: str = "parity",
    model_visible: bool = True,
) -> ToolDefinition:
    """假工具：handler 回一个只有工具名与参数的结果，绝不碰外部服务。"""

    def handler(values: BaseModel, _context: Any) -> dict[str, Any]:
        return {"tool": name, "arguments": values.model_dump()}

    return ToolDefinition(
        name=name,
        group=group,
        risk=risk,
        description=f"行为对齐基线里的假工具 {name}",
        params_model=params or _EmptyParams,
        handler=handler,
        read_only=read_only,
        model_visible=model_visible,
    )


def _interaction_registry() -> ToolRegistry:
    """只有真 ``ask_user`` 定义（handler 会被 runtime 的提问链路拦下，不会执行）。"""

    real = build_registry()
    registry = ToolRegistry()
    registry.register(real.get("ask_user"))
    return registry


class _FixedKnowledgeService:
    async def render_context(self) -> str:
        return _KNOWLEDGE


class _Harness:
    """一条真实的 v2 runtime 装配：文件库、确认服务、假模型与会话。"""

    def __init__(self, tmp: Path) -> None:
        #: 必须用文件库：内存库 + StaticPool 时，「拒绝确认」的场景会挂死。
        self.url = f"sqlite+aiosqlite:///{tmp / 'parity.sqlite3'}"
        self.tmp = tmp
        self.engine: Any = None
        self.factory: Any = None
        self.registry: ToolRegistry | None = None
        self.confirmations: ConfirmationService | None = None
        self.questions: QuestionService | None = None
        self.runtime: AgentRuntime | None = None
        self.llm: Any = None
        self.settings: Settings | None = None

    async def setup(
        self,
        responses: Iterable[Any] | None = None,
        *,
        registry: ToolRegistry | None = None,
        questions: bool = False,
        vision: bool = True,
        llm: Any = None,
        **runtime_options: Any,
    ) -> None:
        self.engine = db_session.make_engine(self.url)
        self.factory = async_sessionmaker(
            self.engine, expire_on_commit=False, class_=AsyncSession
        )
        async with self.engine.begin() as connection:
            await connection.run_sync(SQLModel.metadata.create_all)
        self.registry = registry or ToolRegistry()
        self.confirmations = ConfirmationService(
            self.factory, self.registry, policy=PolicyEngine()
        )
        self.questions = (
            QuestionService(self.factory, timeout_seconds=60) if questions else None
        )
        self.llm = llm if llm is not None else FakeLLM(responses or [])
        self.settings = Settings(
            llm=LLMSettings(base_url=BASE_URL, api_key="parity-key", model=MODEL),
            workspace=WorkspaceSettings(root=str(self.tmp)),
        )
        self.runtime = AgentRuntime(
            self.factory,
            self.confirmations,
            settings_provider=lambda _request: self.settings,
            client_factory=lambda _settings: self.llm,
            context_provider=lambda _request: {"device": "connected"},
            knowledge_service=_FixedKnowledgeService(),
            confirmation_poll_seconds=0.01,
            question_service=self.questions,
            **runtime_options,
        )
        # 预置能力缓存，跳过真实能力探查（探查会额外发一次没有 system 的请求）。
        self.runtime._capability_cache[(MODEL, BASE_URL)] = {
            "model": MODEL,
            "tool_calling": True,
            "vision": vision,
            "ready": True,
            "status": "ready",
            "degraded_reason": None,
        }
        async with self.factory() as session:
            await AgentSessionRepository(session).create(
                AgentSession(id=SESSION_ID, model=MODEL, base_url=BASE_URL)
            )
            await session.commit()

    async def stream(
        self, content: str, on_event: Callable[[str, dict[str, Any]], Any] | None = None
    ) -> list[str]:
        runtime = self.runtime
        assert runtime is not None
        assert await runtime.acquire_session(SESSION_ID)
        frames: list[str] = []
        async for frame in runtime.stream_message(SESSION_ID, content, None):
            frames.append(frame)
            if frame.startswith(":"):
                continue
            if on_event is not None:
                for name, data in parse_events([frame]):
                    result = on_event(name, data)
                    if result is not None and asyncio.iscoroutine(result):
                        await result
        return frames

    async def capture(
        self,
        frames: Sequence[str],
        norm: _Normalizer,
        *,
        result: Any = None,
    ) -> dict[str, Any]:
        assert self.factory is not None
        async with self.factory() as session:
            messages = await AgentMessageRepository(session).list_by_session(SESSION_ID)
            stored_session = await AgentSessionRepository(session).get(SESSION_ID)
            audits = list(
                (
                    await session.execute(
                        select(AgentAudit)
                        .where(AgentAudit.session_id == SESSION_ID)
                        .order_by(AgentAudit.id)
                    )
                )
                .scalars()
                .all()
            )
        assert stored_session is not None

        requests = getattr(self.llm, "turns", None)
        if requests is None:
            requests = getattr(self.llm, "requests", [])
        captured_requests = [
            _request(model, request_messages, request_tools, norm)
            for model, request_messages, request_tools in requests
        ]
        captured_messages = [
            {
                "id": message.id,
                "seq": message.seq,
                "role": message.role,
                "content": norm.value(message.content),
                "reasoning_content": norm.value(message.reasoning_content),
                "tool_calls": norm.value(message.tool_calls),
                "tool_call_id": message.tool_call_id,
                "tool_name": message.tool_name,
                "audit_id": norm.audit(message.audit_id),
                "prompt_tokens": message.prompt_tokens,
                "completion_tokens": message.completion_tokens,
                "finish_reason": message.finish_reason,
            }
            for message in messages
        ]
        captured_audits = sorted(
            (
                norm.value(
                    {
                        "tool_name": audit.tool_name,
                        "status": str(audit.status),
                        "error_code": audit.error_code,
                        "risk_level": str(audit.risk_level),
                        "caller": str(audit.caller),
                        "result_summary": audit.result_summary,
                    }
                )
                for audit in audits
            ),
            key=lambda item: json.dumps(item, ensure_ascii=False, sort_keys=True),
        )
        captured: dict[str, Any] = {
            "sse": [[name, norm.value(data)] for name, data in parse_events(frames)],
            "requests": captured_requests,
            "messages": captured_messages,
            "session": {
                "title": stored_session.title,
                "message_count": stored_session.message_count,
                "prompt_tokens": stored_session.prompt_tokens,
                "completion_tokens": stored_session.completion_tokens,
            },
            "audits": captured_audits,
        }
        if result is not None:
            captured["result"] = norm.value(result)
        return captured

    async def close(self) -> None:
        if self.engine is not None:
            await self.engine.dispose()


async def _capture_turn(
    tmp_path: Path,
    responses: Iterable[Any],
    content: str,
    *,
    registry: ToolRegistry | None = None,
    questions: bool = False,
    vision: bool = True,
    on_event: Callable[[str, dict[str, Any]], Any] | None = None,
    **runtime_options: Any,
) -> dict[str, Any]:
    harness = _Harness(tmp_path)
    await harness.setup(
        responses,
        registry=registry,
        questions=questions,
        vision=vision,
        **runtime_options,
    )
    try:
        frames = await harness.stream(content, on_event=on_event)
        return await harness.capture(frames, _normalizer_for(tmp_path))
    finally:
        await harness.close()


# --------------------------------------------------------------------------- #
# 16 个场景
# --------------------------------------------------------------------------- #
async def _scenario_text_turn(tmp_path: Path) -> dict[str, Any]:
    return await _capture_turn(
        tmp_path,
        [text_response("你好", prompt_tokens=19, completion_tokens=3)],
        "早上好",
    )


async def _scenario_parallel_read_only_batch(tmp_path: Path) -> dict[str, Any]:
    registry = ToolRegistry()
    registry.register(_tool("lookup", read_only=True, params=_LookupParams))
    return await _capture_turn(
        tmp_path,
        [
            tool_response(
                _call("call-1", "lookup", {"key": "a"}, index=0),
                _call("call-2", "lookup", {"key": "b"}, index=1),
            ),
            text_response("查到了"),
        ],
        "查一下 a 和 b",
        registry=registry,
    )


async def _scenario_serial_write_with_reasoning(tmp_path: Path) -> dict[str, Any]:
    registry = ToolRegistry()
    registry.register(
        _tool("write_value", params=_WriteParams, group="ops")
    )
    responses = [
        [
            {
                "choices": [
                    {
                        "index": 0,
                        "delta": {
                            "content": "先写一下",
                            "reasoning_content": "要先写值",
                            "tool_calls": [
                                _call("call-1", "write_value", {"value": "x"})
                            ],
                        },
                        "finish_reason": None,
                    }
                ]
            },
            {
                "choices": [
                    {"index": 0, "delta": {}, "finish_reason": "tool_calls"}
                ]
            },
        ],
        text_response("写好了"),
    ]
    return await _capture_turn(
        tmp_path, responses, "把值写成 x", registry=registry
    )


async def _scenario_confirmation_approved(tmp_path: Path) -> dict[str, Any]:
    registry = ToolRegistry()
    registry.register(
        _tool("click", risk=ToolRisk.DANGEROUS, params=_ClickParams, group="raw")
    )
    harness = _Harness(tmp_path)
    await harness.setup(
        [
            tool_response(_call("call-1", "click", {"x": 31, "y": 42})),
            text_response("点过了"),
        ],
        registry=registry,
    )

    async def on_event(name: str, data: dict[str, Any]) -> None:
        if name == "confirmation_required":
            assert harness.confirmations is not None
            await harness.confirmations.resolve(
                data["confirmation_id"], approved=True, resolved_by="user"
            )

    try:
        frames = await harness.stream("点一下这个位置", on_event=on_event)
        return await harness.capture(frames, _normalizer_for(tmp_path))
    finally:
        await harness.close()


async def _scenario_confirmation_rejected_skips_rest(
    tmp_path: Path,
) -> dict[str, Any]:
    registry = ToolRegistry()
    registry.register(
        _tool("drop_value", risk=ToolRisk.DANGEROUS, group="ops")
    )
    harness = _Harness(tmp_path)
    await harness.setup(
        [
            tool_response(
                _call("call-1", "drop_value", {}, index=0),
                _call("call-2", "drop_value", {}, index=1),
            )
        ],
        registry=registry,
    )

    async def on_event(name: str, data: dict[str, Any]) -> None:
        if name == "confirmation_required":
            assert harness.confirmations is not None
            await harness.confirmations.resolve(
                data["confirmation_id"],
                approved=False,
                resolved_by="user",
                reason="不要",
            )

    try:
        frames = await harness.stream("把这两个值都删掉", on_event=on_event)
        return await harness.capture(frames, _normalizer_for(tmp_path))
    finally:
        await harness.close()


def _ask_user_response() -> list[dict[str, Any]]:
    return tool_response(
        _call(
            "call-1",
            "ask_user",
            {
                "question": "你是哪个服的？",
                "options": [
                    {"label": "官服（Official）"},
                    {"label": "B 服（Bilibili）"},
                ],
                "multiple": False,
            },
        )
    )


async def _scenario_ask_user_answered(tmp_path: Path) -> dict[str, Any]:
    harness = _Harness(tmp_path)
    await harness.setup(
        [_ask_user_response(), text_response("好的")],
        registry=_interaction_registry(),
        questions=True,
    )

    async def on_event(name: str, data: dict[str, Any]) -> None:
        if name == "question_required":
            assert harness.questions is not None
            await harness.questions.answer(
                data["question_id"], selected=["官服（Official）"]
            )

    try:
        frames = await harness.stream("帮我选一个客户端", on_event=on_event)
        return await harness.capture(frames, _normalizer_for(tmp_path))
    finally:
        await harness.close()


async def _scenario_ask_user_skipped(tmp_path: Path) -> dict[str, Any]:
    harness = _Harness(tmp_path)
    await harness.setup(
        [_ask_user_response()],
        registry=_interaction_registry(),
        questions=True,
    )

    async def on_event(name: str, data: dict[str, Any]) -> None:
        if name == "question_required":
            assert harness.questions is not None
            await harness.questions.skip(data["question_id"])

    try:
        frames = await harness.stream("帮我选一个客户端", on_event=on_event)
        return await harness.capture(frames, _normalizer_for(tmp_path))
    finally:
        await harness.close()


async def _scenario_stuck_loop(tmp_path: Path) -> dict[str, Any]:
    registry = ToolRegistry()
    registry.register(_tool("lookup", read_only=True, params=_LookupParams))
    responses = [
        tool_response(_call(f"call-{index}", "lookup", {"key": "same"}))
        for index in range(1, 6)
    ]
    return await _capture_turn(
        tmp_path, responses, "一直查同一个键", registry=registry
    )


async def _scenario_malformed_arguments(tmp_path: Path) -> dict[str, Any]:
    registry = ToolRegistry()
    registry.register(_tool("lookup", read_only=True, params=_LookupParams))
    responses = [
        [
            {
                "choices": [
                    {
                        "index": 0,
                        "delta": {
                            "tool_calls": [
                                {
                                    "index": 0,
                                    "id": "call-1",
                                    "type": "function",
                                    "function": {
                                        "name": "lookup",
                                        "arguments": '{"broken":',
                                    },
                                }
                            ]
                        },
                        "finish_reason": None,
                    }
                ]
            },
            {
                "choices": [
                    {"index": 0, "delta": {}, "finish_reason": "tool_calls"}
                ]
            },
        ]
    ]
    return await _capture_turn(
        tmp_path, responses, "查一个坏参数", registry=registry
    )


async def _scenario_vision_and_hidden_gates(tmp_path: Path) -> dict[str, Any]:
    registry = ToolRegistry()
    registry.register(_tool("get_screenshot"))
    registry.register(_tool("annotate_screenshot"))
    registry.register(_tool("trigger_screencap", model_visible=False))
    registry.register(_tool("lookup", read_only=True, params=_LookupParams))
    return await _capture_turn(
        tmp_path,
        [
            tool_response(
                _call("call-1", "trigger_screencap", {}, index=0),
                _call("call-2", "annotate_screenshot", {}, index=1),
                _call("call-3", "click", {"x": 1, "y": 2}, index=2),
            ),
            text_response("结束"),
        ],
        "截个图圈一下",
        registry=registry,
        vision=False,
    )


async def _scenario_llm_timeout_notice(tmp_path: Path) -> dict[str, Any]:
    return await _capture_turn(
        tmp_path,
        [AppError(ErrorCode.LLM_TIMEOUT, "LLM 上游请求超时")],
        "在吗",
    )


async def _scenario_context_overflow_notice(tmp_path: Path) -> dict[str, Any]:
    return await _capture_turn(
        tmp_path,
        [text_response("这一轮不该发出请求")],
        "超长上下文",
        max_context_chars=10,
    )


class _HangingClient:
    """先吐一段文字，然后挂住等用户中断的假客户端。"""

    def __init__(self) -> None:
        self.requests: list[tuple[str, list[dict[str, Any]], list[dict[str, Any]]]] = []

    async def stream_chat_completion(
        self,
        model: str,
        messages: list[dict[str, Any]],
        tools: list[dict[str, Any]],
    ):
        self.requests.append(
            (
                model,
                _json_copy(messages),
                _json_copy(tools),
            )
        )
        yield {
            "choices": [
                {
                    "index": 0,
                    "delta": {"content": "正在想"},
                    "finish_reason": None,
                }
            ]
        }
        await asyncio.Event().wait()


async def _scenario_interrupt_mid_stream(tmp_path: Path) -> dict[str, Any]:
    harness = _Harness(tmp_path)
    await harness.setup([], llm=_HangingClient())
    interrupted = False

    async def on_event(name: str, _data: dict[str, Any]) -> None:
        nonlocal interrupted
        if name == "text_delta" and not interrupted:
            interrupted = True
            assert harness.runtime is not None
            assert await harness.runtime.interrupt_turn(SESSION_ID) is True

    try:
        frames = await harness.stream("慢慢想", on_event=on_event)
        return await harness.capture(frames, _normalizer_for(tmp_path))
    finally:
        await harness.close()


async def _scenario_incident_run(tmp_path: Path) -> dict[str, Any]:
    registry = ToolRegistry()
    registry.register(_tool("get_logs", read_only=True))
    registry.register(_tool("submit_pipeline", risk=ToolRisk.DANGEROUS))
    harness = _Harness(tmp_path)
    await harness.setup(
        [
            tool_response(
                _call("call-1", "get_logs", {}, index=0),
                _call("call-2", "submit_pipeline", {}, index=1),
            ),
            text_response("看过了，是内核没启动"),
        ],
        registry=registry,
    )
    try:
        async with harness.factory() as session:
            await AgentMessageRepository(session).create(
                AgentMessage(
                    session_id=SESSION_ID,
                    seq=1,
                    role="user",
                    content="游戏卡住了，帮我看看",
                )
            )
            await session.commit()
        assert harness.runtime is not None
        result = await harness.runtime.run_incident(
            SESSION_ID,
            None,
            INCIDENT_ALLOWED_TOOLS | {"submit_pipeline"},
        )
        return await harness.capture(
            [], _normalizer_for(tmp_path), result=result
        )
    finally:
        await harness.close()


class _StepScript:
    """注入 ``run_recon`` 的假步进函数：按剧本返回步骤并记录每次看到的 messages。"""

    def __init__(self, steps: list[ReconStep]) -> None:
        self.steps = list(steps)
        self.seen: list[list[dict[str, Any]]] = []

    async def __call__(
        self,
        _deps: ReconDeps,
        messages: list[dict[str, Any]],
        _schemas: list[dict[str, Any]],
    ) -> ReconStep:
        self.seen.append(_json_copy(messages))
        if not self.steps:
            return ReconStep(text="没有更多步骤")
        return self.steps.pop(0)


def _recon_settings(tmp_path: Path, **agent: Any) -> Settings:
    return Settings(
        llm=LLMSettings(base_url=BASE_URL, api_key="parity-key", model=MODEL),
        workspace=WorkspaceSettings(root=str(tmp_path)),
        agent=AgentSettings(**agent),
    )


def _recording_recon_registry(calls: list[dict[str, Any]]) -> ToolRegistry:
    """三个同名工具的表：handler 记录调用并回一个短结果，不用真工作区。"""

    registry = ToolRegistry()
    for name, model in (
        ("list_files", explore.ListFilesParams),
        ("read_file", explore.ReadFileParams),
        ("search_code", explore.SearchCodeParams),
    ):
        registry.register(
            ToolDefinition(
                name=name,
                group="recon",
                risk=ToolRisk.SAFE,
                description=f"假的 {name}",
                params_model=model,
                handler=_recon_recorder(name, calls),
                read_only=True,
            )
        )
    return registry


def _recon_recorder(name: str, calls: list[dict[str, Any]]):
    async def handler(params: Any, _context: Any) -> dict[str, Any]:
        calls.append({"name": name, "arguments": params.model_dump()})
        return {"ok": True, "tool": name}

    return handler


async def _scenario_recon_step_script(tmp_path: Path) -> dict[str, Any]:
    settings = _recon_settings(tmp_path)
    calls: list[dict[str, Any]] = []
    registry = _recording_recon_registry(calls)
    script = _StepScript(
        [
            ReconStep(
                text="先看看目录",
                calls=[
                    {
                        "id": "call-1",
                        "name": "list_files",
                        "arguments": json.dumps({"path": "."}),
                    },
                    # 不在 recon 白名单里：run_recon 回灌前就会把它过滤掉。
                    {"id": "call-2", "name": "delegate_recon", "arguments": "{}"},
                ],
            ),
            ReconStep(
                text=json.dumps(
                    {
                        "summary": "入口在 app.py",
                        "findings": [
                            {
                                "title": "入口文件",
                                "detail": "第一行打印 hi",
                                "evidence": ["app.py:1"],
                            }
                        ],
                        "uncertainties": [],
                    },
                    ensure_ascii=False,
                )
            ),
        ]
    )
    budget = budget_from_settings(settings)
    report = await run_recon(
        ReconDeps(
            settings=settings,
            client=None,
            model=MODEL,
            max_steps=budget.max_steps,
            max_chars=budget.max_chars,
            output_chars=budget.output_chars,
        ),
        question="入口在哪",
        hints=["先看根目录"],
        registry=registry,
        step_runner=script,
    )
    norm = _normalizer_for(tmp_path)
    return {
        "steps": norm.value(_json_copy(script.seen)),
        "tool_calls": norm.value(_json_copy(calls)),
        "report": norm.value(_json_copy(report)),
    }


class _ReconClient:
    """每次都回一个 ``list_files`` 调用的假客户端，记录每次请求。"""

    def __init__(self) -> None:
        self.requests: list[tuple[str, list[dict[str, Any]], list[dict[str, Any]]]] = []

    async def stream_chat_completion(
        self,
        model: str,
        messages: list[dict[str, Any]],
        tools: list[dict[str, Any]],
    ):
        self.requests.append(
            (
                model,
                _json_copy(messages),
                _json_copy(tools),
            )
        )
        index = len(self.requests)
        for chunk in tool_response(
            _call(f"call-{index}", "list_files", {"path": "."})
        ):
            yield chunk


async def _scenario_recon_runtime_budget(tmp_path: Path) -> dict[str, Any]:
    settings = _recon_settings(tmp_path, recon_max_steps=2)
    client = _ReconClient()
    runtime = AgentRuntime(
        None,  # type: ignore[arg-type] - recon 不碰会话库
        None,  # type: ignore[arg-type] - recon 不碰确认服务
        settings_provider=lambda _request=None: settings,
        client_factory=lambda _settings: client,
    )
    # 真实 explore 工具的结果会嵌入工作区绝对路径，``chars_used`` 随 pytest tmp 目录
    # 名长度漂移（M71 全量跑暴露）。这里换成固定结果的记录注册表，预算数字与目录名
    # 无关；golden 只重录本场景一次（13 §8.44）。
    from unittest.mock import patch

    from maa_api.agent.recon import runner as recon_runner

    calls: list[dict[str, Any]] = []
    recorded = _recording_recon_registry(calls)
    with patch.object(recon_runner, "_recon_registry", lambda: recorded):
        report = await runtime.recon("列一下文件")
    norm = _normalizer_for(tmp_path)
    normalized = norm.value(_json_copy(report))
    return {
        "requests": [
            _request(model, messages, tools, norm)
            for model, messages, tools in client.requests
        ],
        "report": normalized,
    }


SCENARIOS: dict[str, Callable[[Path], Any]] = {
    "text_turn": _scenario_text_turn,
    "parallel_read_only_batch": _scenario_parallel_read_only_batch,
    "serial_write_with_reasoning": _scenario_serial_write_with_reasoning,
    "confirmation_approved": _scenario_confirmation_approved,
    "confirmation_rejected_skips_rest": _scenario_confirmation_rejected_skips_rest,
    "ask_user_answered": _scenario_ask_user_answered,
    "ask_user_skipped": _scenario_ask_user_skipped,
    "stuck_loop": _scenario_stuck_loop,
    "malformed_arguments": _scenario_malformed_arguments,
    "vision_and_hidden_gates": _scenario_vision_and_hidden_gates,
    "llm_timeout_notice": _scenario_llm_timeout_notice,
    "context_overflow_notice": _scenario_context_overflow_notice,
    "interrupt_mid_stream": _scenario_interrupt_mid_stream,
    "incident_run": _scenario_incident_run,
    "recon_step_script": _scenario_recon_step_script,
    "recon_runtime_budget": _scenario_recon_runtime_budget,
}


@pytest.mark.parametrize("name", sorted(SCENARIOS))
def test_legacy_behaviour_matches_the_recorded_golden(
    name: str, tmp_path: Path
) -> None:
    captured = json.loads(
        json.dumps(
            asyncio.run(SCENARIOS[name](tmp_path)),
            ensure_ascii=False,
            sort_keys=True,
            default=str,
        )
    )
    if os.environ.get(REGENERATE_ENV) == "1":
        golden: dict[str, Any] = {}
        if GOLDEN_PATH.exists():
            golden = json.loads(GOLDEN_PATH.read_text(encoding="utf-8"))
        golden[name] = captured
        GOLDEN_PATH.parent.mkdir(parents=True, exist_ok=True)
        GOLDEN_PATH.write_text(
            json.dumps(golden, indent=1, sort_keys=True, ensure_ascii=False) + "\n",
            encoding="utf-8",
        )
        return
    golden = json.loads(GOLDEN_PATH.read_text(encoding="utf-8"))
    assert captured == golden[name]


def test_golden_covers_exactly_the_scenarios() -> None:
    golden = json.loads(GOLDEN_PATH.read_text(encoding="utf-8"))
    assert set(golden) == set(SCENARIOS)
