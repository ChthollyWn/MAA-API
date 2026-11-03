"""``RunRecovery`` 的用例（M66，docs/v3/specs/m66-durable-runs §4.2.3、§6）。

内存库 + ``StaticPool``：一个用例一个库，同步函数里 ``asyncio.run``。恢复直接建库、
写上一进程留下的行，不经过 ``RunService``——这条路径测的是「启动时把别人留下的活动
运行收尾」，不是运行生命周期本身。
"""

from __future__ import annotations

import asyncio
import json
from datetime import datetime, timedelta
from typing import Any

from sqlalchemy.ext.asyncio import AsyncEngine, AsyncSession, async_sessionmaker
from sqlalchemy.pool import StaticPool
from sqlmodel import SQLModel

import maa_api.db.models  # noqa: F401 - 建表需要全部模型进 metadata
from maa_api.agent.confirmation import ConfirmationService
from maa_api.agent.policy import PolicyEngine
from maa_api.agent.registry import ToolRegistry
from maa_api.agent.runs import RunRecovery
from maa_api.agent.runs.recovery import (
    RECOVERY_FINISH_REASON,
    RECOVERY_NOTICE,
    RECOVERY_TOOL_STUB,
)
from maa_api.agent.runtime import AgentRuntime
from maa_api.db.models import AgentMessage, AgentRun, AgentSession, new_uuid, utcnow
from maa_api.db.repositories.agent import (
    AgentMessageRepository,
    AgentRunRepository,
)
from maa_api.db.session import make_engine
from maa_api.domain.enums import AgentRunKind, AgentRunStatus
from maa_api.settings import AgentSettings, LLMSettings, Settings
from tests.fakes.fake_llm import FakeLLM, text_response

CURRENT_BOOT = "boot-current"
PREVIOUS_BOOT = "boot-previous"
MODEL = "recovery-model"
BASE_URL = "https://llm.test/v1"


class FakeClock:
    """可拨的时钟；``RunRecovery`` 的 ``finished_at`` 与种子数据共用同一个。"""

    def __init__(self, now: datetime | None = None) -> None:
        self._now = now or datetime(2026, 11, 3, 12, 0, 0)

    def __call__(self) -> datetime:
        return self._now

    def advance(self, seconds: float) -> None:
        self._now += timedelta(seconds=seconds)


async def _prepare() -> tuple[AsyncEngine, async_sessionmaker[AsyncSession]]:
    engine = make_engine("sqlite+aiosqlite:///:memory:", poolclass=StaticPool)
    factory = async_sessionmaker(engine, expire_on_commit=False, class_=AsyncSession)
    async with engine.begin() as connection:
        await connection.run_sync(SQLModel.metadata.create_all)
    return engine, factory


def _recovery(
    factory: async_sessionmaker[AsyncSession],
    clock: FakeClock,
    *,
    boot_id: str = CURRENT_BOOT,
) -> RunRecovery:
    return RunRecovery(factory, boot_id=boot_id, clock=clock)


async def _seed_session(
    factory: async_sessionmaker[AsyncSession],
    session_id: str,
    *,
    message_count: int = 0,
) -> None:
    async with factory() as session:
        session.add(
            AgentSession(
                id=session_id,
                model=MODEL,
                base_url=BASE_URL,
                message_count=message_count,
            )
        )
        await session.commit()


async def _add_message(
    factory: async_sessionmaker[AsyncSession],
    session_id: str,
    *,
    seq: int,
    role: str,
    content: str | None = None,
    tool_calls: list[dict[str, Any]] | None = None,
    tool_call_id: str | None = None,
    tool_name: str | None = None,
) -> None:
    async with factory() as session:
        await AgentMessageRepository(session).create(
            AgentMessage(
                session_id=session_id,
                seq=seq,
                role=role,
                content=content,
                tool_calls=tool_calls,
                tool_call_id=tool_call_id,
                tool_name=tool_name,
            )
        )
        await session.commit()


def _call(call_id: str, name: str = "get_device_status") -> dict[str, Any]:
    return {
        "id": call_id,
        "type": "function",
        "function": {"name": name, "arguments": "{}"},
    }


async def _insert_run(
    factory: async_sessionmaker[AsyncSession],
    *,
    boot_id: str,
    session_id: str | None = None,
    status: str = AgentRunStatus.RUNNING,
    run_id: str | None = None,
    started_at: datetime | None = None,
) -> AgentRun:
    """直接插一条运行（模拟别的进程留下的行），不经过 ``RunService``。"""
    run_id = run_id or new_uuid()
    stamp = started_at or utcnow()
    async with factory() as session:
        await AgentRunRepository(session).create(
            AgentRun(
                id=run_id,
                session_id=session_id,
                root_run_id=run_id,
                agent_name="legacy_chat",
                kind=AgentRunKind.LEGACY_TURN,
                status=status,
                boot_id=boot_id,
                started_at=stamp,
                heartbeat_at=stamp,
            )
        )
        await session.commit()
    return await _load_run(factory, run_id)


async def _load_run(
    factory: async_sessionmaker[AsyncSession], run_id: str
) -> AgentRun:
    async with factory() as session:
        stored = await session.get(AgentRun, run_id, populate_existing=True)
    assert stored is not None
    return stored


async def _load_session(
    factory: async_sessionmaker[AsyncSession], session_id: str
) -> AgentSession:
    async with factory() as session:
        stored = await session.get(AgentSession, session_id, populate_existing=True)
    assert stored is not None
    return stored


async def _messages(
    factory: async_sessionmaker[AsyncSession], session_id: str
) -> list[AgentMessage]:
    async with factory() as session:
        return await AgentMessageRepository(session).list_by_session(session_id)


def test_recover_marks_previous_boot_runs_interrupted() -> None:
    async def scenario() -> None:
        engine, factory = await _prepare()
        clock = FakeClock()
        await _seed_session(factory, "session-running")
        await _seed_session(factory, "session-waiting")
        first = await _insert_run(
            factory,
            boot_id=PREVIOUS_BOOT,
            session_id="session-running",
            status=AgentRunStatus.RUNNING,
            started_at=clock(),
        )
        clock.advance(60)
        second = await _insert_run(
            factory,
            boot_id=PREVIOUS_BOOT,
            session_id="session-waiting",
            status=AgentRunStatus.WAITING_HUMAN,
            started_at=clock(),
        )
        try:
            recovered = await _recovery(factory, clock).recover()
            assert recovered == [first.id, second.id]
            for run_id in (first.id, second.id):
                stored = await _load_run(factory, run_id)
                assert stored.status == AgentRunStatus.INTERRUPTED
                assert stored.finish_reason == RECOVERY_FINISH_REASON
                assert stored.finished_at == clock()
        finally:
            await engine.dispose()

    asyncio.run(scenario())


def test_recover_appends_tool_stubs_then_system_notice() -> None:
    async def scenario() -> None:
        engine, factory = await _prepare()
        clock = FakeClock()
        await _seed_session(factory, "session-1", message_count=2)
        await _add_message(
            factory,
            "session-1",
            seq=0,
            role="assistant",
            tool_calls=[_call("call-1"), _call("call-2")],
        )
        await _add_message(
            factory,
            "session-1",
            seq=1,
            role="tool",
            content='{"ok": true}',
            tool_call_id="call-1",
            tool_name="get_device_status",
        )
        run = await _insert_run(
            factory, boot_id=PREVIOUS_BOOT, session_id="session-1"
        )
        try:
            recovered = await _recovery(factory, clock).recover()
            assert recovered == [run.id]
            messages = await _messages(factory, "session-1")
            assert [message.role for message in messages] == [
                "assistant",
                "tool",
                "tool",
                "system",
            ]
            stub = messages[2]
            assert stub.tool_call_id == "call-2"
            assert stub.tool_name == "get_device_status"
            assert stub.run_id == run.id
            assert json.loads(stub.content or "") == RECOVERY_TOOL_STUB
            notice = messages[3]
            assert notice.content == RECOVERY_NOTICE
            assert notice.run_id == run.id
            assert notice.finish_reason == RECOVERY_FINISH_REASON
            assert (await _load_session(factory, "session-1")).message_count == 4
        finally:
            await engine.dispose()

    asyncio.run(scenario())


def test_recover_skips_stubs_when_batch_is_complete() -> None:
    async def scenario() -> None:
        engine, factory = await _prepare()
        clock = FakeClock()
        await _seed_session(factory, "session-complete", message_count=2)
        await _add_message(
            factory,
            "session-complete",
            seq=0,
            role="assistant",
            tool_calls=[_call("call-1")],
        )
        await _add_message(
            factory,
            "session-complete",
            seq=1,
            role="tool",
            content='{"ok": true}',
            tool_call_id="call-1",
            tool_name="get_device_status",
        )
        complete_run = await _insert_run(
            factory, boot_id=PREVIOUS_BOOT, session_id="session-complete"
        )
        await _seed_session(factory, "session-text", message_count=2)
        await _add_message(
            factory, "session-text", seq=0, role="user", content="看看设备"
        )
        await _add_message(
            factory, "session-text", seq=1, role="assistant", content="看过了"
        )
        text_run = await _insert_run(
            factory, boot_id=PREVIOUS_BOOT, session_id="session-text"
        )
        try:
            recovered = await _recovery(factory, clock).recover()
            assert set(recovered) == {complete_run.id, text_run.id}
            complete_messages = await _messages(factory, "session-complete")
            assert [message.role for message in complete_messages] == [
                "assistant",
                "tool",
                "system",
            ]
            assert complete_messages[-1].content == RECOVERY_NOTICE
            assert (await _load_session(factory, "session-complete")).message_count == 3
            text_messages = await _messages(factory, "session-text")
            assert [message.role for message in text_messages] == [
                "user",
                "assistant",
                "system",
            ]
            assert text_messages[-1].content == RECOVERY_NOTICE
            assert (await _load_session(factory, "session-text")).message_count == 3
        finally:
            await engine.dispose()

    asyncio.run(scenario())


def test_recover_ignores_current_boot_and_finished_runs() -> None:
    async def scenario() -> None:
        engine, factory = await _prepare()
        clock = FakeClock()
        await _seed_session(factory, "session-current")
        await _seed_session(factory, "session-done")
        current = await _insert_run(
            factory,
            boot_id=CURRENT_BOOT,
            session_id="session-current",
            status=AgentRunStatus.RUNNING,
        )
        finished = await _insert_run(
            factory,
            boot_id=PREVIOUS_BOOT,
            session_id="session-done",
            status=AgentRunStatus.SUCCEEDED,
        )
        try:
            recovered = await _recovery(factory, clock).recover()
            assert recovered == []
            kept_current = await _load_run(factory, current.id)
            assert kept_current.status == AgentRunStatus.RUNNING
            assert kept_current.finished_at is None
            kept_finished = await _load_run(factory, finished.id)
            assert kept_finished.status == AgentRunStatus.SUCCEEDED
            assert kept_finished.finished_at is None
            assert await _messages(factory, "session-current") == []
            assert await _messages(factory, "session-done") == []
        finally:
            await engine.dispose()

    asyncio.run(scenario())


def test_recover_is_idempotent() -> None:
    async def scenario() -> None:
        engine, factory = await _prepare()
        clock = FakeClock()
        await _seed_session(factory, "session-1", message_count=2)
        await _add_message(
            factory,
            "session-1",
            seq=0,
            role="assistant",
            tool_calls=[_call("call-1"), _call("call-2")],
        )
        await _add_message(
            factory,
            "session-1",
            seq=1,
            role="tool",
            content='{"ok": true}',
            tool_call_id="call-1",
            tool_name="get_device_status",
        )
        run = await _insert_run(
            factory, boot_id=PREVIOUS_BOOT, session_id="session-1"
        )
        recovery = _recovery(factory, clock)
        try:
            assert await recovery.recover() == [run.id]
            messages_after_first = await _messages(factory, "session-1")
            assert await recovery.recover() == []
            assert await recovery.recover_run(run.id) is False
            assert await _messages(factory, "session-1") == messages_after_first
            assert (await _load_session(factory, "session-1")).message_count == 4
        finally:
            await engine.dispose()

    asyncio.run(scenario())


def test_recovered_session_next_turn_sends_valid_history() -> None:
    async def scenario() -> None:
        engine, factory = await _prepare()
        clock = FakeClock()
        session_id = "session-recovered"
        await _seed_session(factory, session_id, message_count=3)
        await _add_message(
            factory, session_id, seq=0, role="user", content="查一下设备状态"
        )
        await _add_message(
            factory,
            session_id,
            seq=1,
            role="assistant",
            tool_calls=[_call("call-1"), _call("call-2")],
        )
        await _add_message(
            factory,
            session_id,
            seq=2,
            role="tool",
            content='{"ok": true}',
            tool_call_id="call-1",
            tool_name="get_device_status",
        )
        run = await _insert_run(
            factory, boot_id=PREVIOUS_BOOT, session_id=session_id
        )
        try:
            assert await _recovery(factory, clock).recover() == [run.id]

            registry = ToolRegistry()
            confirmations = ConfirmationService(
                factory, registry, policy=PolicyEngine()
            )
            llm = FakeLLM([text_response("继续检查。")])
            settings = Settings(
                llm=LLMSettings(
                    base_url=BASE_URL, api_key="recovery-key", model=MODEL
                ),
                agent=AgentSettings(run_heartbeat_seconds=5, run_stale_seconds=30),
            )
            runtime = AgentRuntime(
                factory,
                confirmations,
                settings_provider=lambda _request: settings,
                client_factory=lambda _settings: llm,
            )
            runtime._capability_cache[(MODEL, BASE_URL)] = {
                "model": MODEL,
                "tool_calling": True,
                "vision": True,
                "ready": True,
                "status": "ready",
                "degraded_reason": None,
            }
            assert await runtime.acquire_session(session_id) is True
            chunks = [
                chunk
                async for chunk in runtime.stream_message(session_id, "继续", None)
            ]
            assert any("event: message_end" in chunk for chunk in chunks)
            assert len(llm.turns) == 1
            messages = llm.turns[0][1]
            assert all(message["role"] != "system" for message in messages[1:])
            call_index = next(
                index
                for index, message in enumerate(messages)
                if message.get("tool_calls")
            )
            assert messages[call_index]["tool_calls"][0]["id"] == "call-1"
            assert messages[call_index + 1]["role"] == "tool"
            assert messages[call_index + 1]["tool_call_id"] == "call-1"
            stub = messages[call_index + 2]
            assert stub["role"] == "tool"
            assert stub["tool_call_id"] == "call-2"
            assert json.loads(stub["content"]) == RECOVERY_TOOL_STUB
            notice = messages[call_index + 3]
            assert notice == {
                "role": "user",
                "content": f"【系统】{RECOVERY_NOTICE}",
            }
            assert messages[call_index + 4] == {"role": "user", "content": "继续"}
        finally:
            await engine.dispose()

    asyncio.run(scenario())
