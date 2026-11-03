"""M65 T8：runtime 门面的再导出、链顺序与代理定义接线（规格 §4.2.2、§4.2.11）。

门面只做 v2 前置步骤与收尾，一轮里的循环交给 ``AgentLoop``；这里钉住三件事：
搬走的名字仍从 runtime 拿得到且是同一个对象、harness 不反向依赖门面、v2 会话与自动
诊断各自带着自己的代理定义跑。
"""

from __future__ import annotations

import asyncio
from pathlib import Path
from typing import Any

from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker
from sqlmodel import SQLModel

from maa_api.agent import runtime as runtime_module
from maa_api.agent.confirmation import ConfirmationService
from maa_api.agent.harness import builtin_middleware, chunks, events, loop, toolexec
from maa_api.agent.harness.context import RunOutcome
from maa_api.agent.harness.loop import AgentLoop
from maa_api.agent.policy import PolicyEngine
from maa_api.agent.registry import ToolRegistry
from maa_api.agent.runtime import AgentRuntime
from maa_api.agent.tools import build_registry
from maa_api.db import session as db_session
from maa_api.db.models import AgentMessage, AgentSession
from maa_api.db.repositories.agent import AgentMessageRepository, AgentSessionRepository
from maa_api.settings import LLMSettings, Settings, WorkspaceSettings
from tests.fakes.fake_llm import FakeLLM

#: §4.2.2「再导出」列：名字 → 新位置。
MOVED_NAMES: dict[str, Any] = {
    "DEFAULT_MAX_CONTEXT_CHARS": builtin_middleware,
    "IMAGE_CONTEXT_CHARS": builtin_middleware,
    "TOOL_RESULT_PRUNE_THRESHOLD": builtin_middleware,
    "TOOL_RESULT_PRUNE_HEAD": builtin_middleware,
    "TOOL_RESULT_PRUNE_TAIL": builtin_middleware,
    "HISTORICAL_REASONING_CHARS": builtin_middleware,
    "SCREENSHOT_RESULT_TOOLS": builtin_middleware,
    "AGENT_SCREENSHOT_GRID": builtin_middleware,
    "QUESTION_EXPIRED_TEXT": builtin_middleware,
    "QUESTION_SKIPPED_TEXT": builtin_middleware,
    "STUCK_REPEAT_LIMIT": builtin_middleware,
    "STUCK_EXEMPT_TOOLS": builtin_middleware,
    "INTERRUPTED_FINISH_REASON": loop,
    "INTERRUPTED_NOTICE": loop,
    "INTERRUPTED_TOOL_STUB": loop,
    "SCREEN_TOOL_NAMES": toolexec,
    "merge_tool_call_deltas": chunks,
    "build_model_context": builtin_middleware,
    "sse_event": events,
    "_context_size": builtin_middleware,
}

BASE_URL = "https://llm.test/v1"
MODEL = "facade-model"


def test_runtime_reexports_moved_names_from_their_new_home():
    for name, module in MOVED_NAMES.items():
        assert getattr(runtime_module, name) is getattr(module, name), name


def test_harness_never_imports_the_runtime_facade():
    harness_dir = Path(builtin_middleware.__file__).parent
    for path in sorted(harness_dir.glob("*.py")):
        text = path.read_text(encoding="utf-8")
        assert "maa_api.agent.runtime" not in text, path.name


def test_legacy_chain_order_matches_the_contract():
    # 链只读门面上的服务属性，不会在构造期碰数据库或注册表。
    confirmations = ConfirmationService(None, build_registry())
    runtime = AgentRuntime(None, confirmations)

    assert runtime._legacy_chain().names == (
        "trace",
        "context_layout",
        "loop_guard",
        "clarification",
        "policy",
    )


def test_chat_and_incident_turns_run_with_their_definitions(tmp_path, monkeypatch):
    async def scenario() -> None:
        engine = db_session.make_engine(f"sqlite+aiosqlite:///{tmp_path / 'facade.sqlite3'}")
        factory = async_sessionmaker(engine, expire_on_commit=False, class_=AsyncSession)
        async with engine.begin() as connection:
            await connection.run_sync(SQLModel.metadata.create_all)
        registry = ToolRegistry()
        confirmations = ConfirmationService(factory, registry, policy=PolicyEngine())
        llm = FakeLLM([])
        settings = Settings(
            llm=LLMSettings(base_url=BASE_URL, api_key="facade-key", model=MODEL),
            workspace=WorkspaceSettings(root=str(tmp_path)),
        )
        runtime = AgentRuntime(
            factory,
            confirmations,
            settings_provider=lambda _request: settings,
            client_factory=lambda _settings: llm,
            confirmation_poll_seconds=0.01,
        )
        runtime._capability_cache[(MODEL, BASE_URL)] = {
            "model": MODEL,
            "tool_calling": True,
            "vision": True,
            "ready": True,
            "status": "ready",
            "degraded_reason": None,
        }
        async with factory() as session:
            await AgentSessionRepository(session).create(
                AgentSession(id="chat", model=MODEL, base_url=BASE_URL)
            )
            await AgentSessionRepository(session).create(
                AgentSession(
                    id="incident",
                    model=MODEL,
                    base_url=BASE_URL,
                    incident_key="incident-1",
                )
            )
            await AgentMessageRepository(session).create(
                AgentMessage(session_id="incident", seq=0, role="user", content="诊断故障")
            )
            await session.commit()

        seen: list[str] = []

        async def recording_run(loop_self: AgentLoop, ctx: Any) -> RunOutcome:
            seen.append(ctx.spec.name)
            return RunOutcome("succeeded", "stop")

        monkeypatch.setattr(AgentLoop, "run", recording_run)
        try:
            assert await runtime.acquire_session("chat") is True
            _frames = [
                frame async for frame in runtime.stream_message("chat", "你好", None)
            ]
            result = await runtime.run_incident("incident", None, {"get_logs"})
            assert result == {"status": "completed"}
        finally:
            await engine.dispose()

        # stream_message 是普通会话，run_incident 是自动诊断：各自带自己的定义。
        assert seen == ["legacy_chat", "legacy_incident"]

    asyncio.run(scenario())
