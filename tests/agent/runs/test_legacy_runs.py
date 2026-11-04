"""v2 门面接入持久化运行的用例（M66，docs/v3/specs/m66-durable-runs §4.2.4、§6）。

一个用例一个库：内存库 + ``StaticPool`` 覆盖绝大多数路径，需要真实并发（同一会话
第二轮被拒）的用例改走文件库。v2 的 ``tests/agent/test_runtime.py`` 与
``test_incidents.py`` 一个断言都不改；这里从外部观察运行行、消息的 ``run_id`` 与终态。
"""

from __future__ import annotations

import asyncio
import json
from collections.abc import Iterable
from dataclasses import dataclass
from datetime import timedelta
from typing import Any

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncEngine, AsyncSession, async_sessionmaker
from sqlalchemy.pool import StaticPool
from sqlmodel import SQLModel

import maa_api.db.models  # noqa: F401 - 建表需要全部模型进 metadata
from maa_api.agent.confirmation import ConfirmationService
from maa_api.agent.harness.builtin_middleware import build_model_context
from maa_api.agent.policy import PolicyEngine
from maa_api.agent.registry import ToolDefinition, ToolRegistry, ToolRisk
from maa_api.agent.runs.recovery import RECOVERY_FINISH_REASON
from maa_api.agent.runtime import AgentRuntime
from maa_api.agent.specs.registry import builtin_spec
from maa_api.agent.tools.raw import ClickParams
from maa_api.db import session as db_session
from maa_api.db.models import (
    AgentMessage,
    AgentRun,
    AgentSession,
    new_uuid,
    utcnow,
)
from maa_api.db.repositories.agent import (
    AgentMessageRepository,
    AgentRunRepository,
    AgentSessionRepository,
)
from maa_api.domain.enums import AgentRunKind, AgentRunStatus, AgentSessionKind
from maa_api.domain.errors import AppError, ErrorCode
from maa_api.settings import LLMSettings, Settings
from tests.fakes.fake_llm import (
    FakeLLM,
    parse_events,
    text_response,
    tool_call,
    tool_response,
)
from tests.fakes.legacy_turn import stream_legacy_turn

MODEL = "legacy-model"
BASE_URL = "https://llm.test/v1"
SESSION_ID = "legacy-session"

CAPABILITIES = {
    "model": MODEL,
    "tool_calling": True,
    "vision": True,
    "ready": True,
    "status": "ready",
    "degraded_reason": None,
}


@dataclass
class _Harness:
    runtime: AgentRuntime
    factory: async_sessionmaker[AsyncSession]
    confirmations: ConfirmationService
    llm: FakeLLM
    session_id: str
    engine: AsyncEngine
    settings: Settings

    async def close(self) -> None:
        await self.engine.dispose()


async def _harness(
    responses: Iterable[Any] = (),
    *,
    registry: ToolRegistry | None = None,
    database_url: str | None = None,
    **runtime_options: Any,
) -> _Harness:
    engine = db_session.make_engine(
        database_url or "sqlite+aiosqlite:///:memory:",
        **({} if database_url else {"poolclass": StaticPool}),
    )
    factory = async_sessionmaker(engine, expire_on_commit=False, class_=AsyncSession)
    async with engine.begin() as connection:
        await connection.run_sync(SQLModel.metadata.create_all)
    tool_registry = registry or ToolRegistry()
    confirmations = ConfirmationService(factory, tool_registry, policy=PolicyEngine())
    settings = Settings(
        llm=LLMSettings(base_url=BASE_URL, api_key="test-key", model=MODEL)
    )
    llm = FakeLLM(responses)
    runtime = AgentRuntime(
        factory,
        confirmations,
        settings_provider=lambda _request: settings,
        client_factory=lambda _settings: llm,
        confirmation_poll_seconds=0.01,
        **runtime_options,
    )
    runtime._capability_cache[(MODEL, BASE_URL)] = dict(CAPABILITIES)
    async with factory() as session:
        await AgentSessionRepository(session).create(
            AgentSession(id=SESSION_ID, model=MODEL, base_url=BASE_URL)
        )
        await session.commit()
    return _Harness(
        runtime=runtime,
        factory=factory,
        confirmations=confirmations,
        llm=llm,
        session_id=SESSION_ID,
        engine=engine,
        settings=settings,
    )


async def _messages(harness: _Harness) -> list[AgentMessage]:
    async with harness.factory() as session:
        return await AgentMessageRepository(session).list_by_session(harness.session_id)


async def _runs(harness: _Harness) -> list[AgentRun]:
    async with harness.factory() as session:
        result = await session.execute(
            select(AgentRun)
            .where(AgentRun.session_id == harness.session_id)
            .order_by(AgentRun.started_at.asc(), AgentRun.id.asc())
        )
        return list(result.scalars().all())


async def _reload_run(harness: _Harness, run_id: str) -> AgentRun:
    async with harness.factory() as session:
        run = await AgentRunRepository(session).get(run_id)
    assert run is not None
    return run


async def _single_run(harness: _Harness) -> AgentRun:
    runs = await _runs(harness)
    assert len(runs) == 1, runs
    return runs[0]


async def _stored_session(harness: _Harness) -> AgentSession:
    async with harness.factory() as session:
        stored = await AgentSessionRepository(session).get(harness.session_id)
    assert stored is not None
    return stored


async def _drain(stream: Any) -> list[str]:
    return [chunk async for chunk in stream]


async def _next_event(stream: Any, name: str) -> dict[str, Any]:
    """读到指定 SSE 事件为止（keep-alive 不计），返回该事件的 data。"""
    while True:
        chunk = await asyncio.wait_for(stream.__anext__(), timeout=5)
        for event, data in parse_events([chunk]):
            if event == name:
                return data


def _register_counter(registry: ToolRegistry, name: str = "query") -> list[int]:
    """注册一个只读安全工具，返回它每次被调用时看到的 ``round`` 参数。"""
    from pydantic import BaseModel, ConfigDict

    class RoundParams(BaseModel):
        model_config = ConfigDict(extra="forbid")

        round: int

    invoked: list[int] = []

    async def handler(params, _context):
        invoked.append(params.round)
        return {"ok": True}

    registry.register(
        ToolDefinition(
            name=name,
            group="status",
            risk=ToolRisk.SAFE,
            description="Return one test result.",
            params_model=RoundParams,
            handler=handler,
            read_only=True,
        )
    )
    return invoked


def _round_call(index: int, *, round_number: int = 1) -> dict[str, Any]:
    return {
        "index": 0,
        "id": f"call-{index}",
        "type": "function",
        "function": {
            "name": "query",
            "arguments": json.dumps({"round": round_number}),
        },
    }


# ---------------------------------------------------------------------------
# 12 条用例
# ---------------------------------------------------------------------------


def test_chat_turn_records_one_succeeded_legacy_run() -> None:
    """一轮文本对话后恰好一条运行，终态/触发点/token/步数都对得上。"""

    async def scenario():
        harness = await _harness([text_response("你好，我在。")])
        try:
            assert await harness.runtime.acquire_session(harness.session_id) is True
            await _drain(
                stream_legacy_turn(harness.runtime, harness.session_id, "早上好", None)
            )
            run = await _single_run(harness)
            assert run.kind == AgentRunKind.LEGACY_TURN
            assert run.agent_name == "legacy_chat"
            assert run.status == AgentRunStatus.SUCCEEDED
            assert run.finish_reason == "stop"
            assert run.spec_version == builtin_spec("legacy_chat").spec_version
            messages = await _messages(harness)
            user = next(message for message in messages if message.role == "user")
            assert run.trigger_seq == user.seq
            assert user.run_id is None
            stored = await _stored_session(harness)
            assert run.prompt_tokens == stored.prompt_tokens == 7
            assert run.completion_tokens == stored.completion_tokens == 2
            assert run.steps == 1
            assert run.finished_at is not None
            assert run.boot_id == harness.runtime.run_service.boot_id
        finally:
            await harness.close()

    asyncio.run(scenario())


def test_turn_messages_carry_run_id_but_user_message_does_not() -> None:
    """带工具调用的一轮：助手与 tool 消息带本轮 ``run_id``，用户消息为空。"""

    async def scenario():
        registry = ToolRegistry()
        _register_counter(registry)
        harness = await _harness(
            [tool_response(_round_call(0)), text_response("查完了。")],
            registry=registry,
        )
        try:
            assert await harness.runtime.acquire_session(harness.session_id) is True
            await _drain(
                stream_legacy_turn(harness.runtime, harness.session_id, "查一下状态", None)
            )
            run = await _single_run(harness)
            messages = await _messages(harness)
            assert [message.role for message in messages] == [
                "user",
                "assistant",
                "tool",
                "assistant",
            ]
            assert messages[0].run_id is None
            assert all(message.run_id == run.id for message in messages[1:])
        finally:
            await harness.close()

    asyncio.run(scenario())


def test_incident_turn_records_legacy_incident_run() -> None:
    """``run_incident`` 一条 ``legacy_incident`` 运行；诊断会话的 kind 是 incident。"""

    async def scenario():
        harness = await _harness([text_response("看过了，是内核没启动。")])
        try:
            async with harness.factory() as session:
                stored_session = await session.get(AgentSession, SESSION_ID)
                stored_session.kind = AgentSessionKind.INCIDENT
                stored_session.title = "自动诊断"
                stored_session.incident_key = "pipeline:pipeline-1"
                await session.flush()
                await AgentMessageRepository(session).create(
                    AgentMessage(
                        session_id=SESSION_ID,
                        seq=0,
                        role="user",
                        content='{"incident_type":"pipeline_failure"}',
                    )
                )
                await session.commit()
            result = await harness.runtime.run_incident(
                SESSION_ID, None, {"get_pipeline"}
            )
            assert result == {"status": "completed"}

            async with harness.factory() as session:
                run = (
                    await session.execute(
                        select(AgentRun).where(AgentRun.session_id == SESSION_ID)
                    )
                ).scalars().one()
                messages = await AgentMessageRepository(session).list_by_session(
                    SESSION_ID
                )
                stored = await AgentSessionRepository(session).get(SESSION_ID)
            trigger = next(message for message in messages if message.role == "user")
            assert run.kind == AgentRunKind.LEGACY_TURN
            assert run.agent_name == "legacy_incident"
            assert run.status == AgentRunStatus.SUCCEEDED
            assert run.finish_reason == "stop"
            assert run.spec_version == builtin_spec("legacy_incident").spec_version
            assert run.trigger_seq == trigger.seq
            assert stored is not None
            assert stored.kind == AgentSessionKind.INCIDENT
        finally:
            await harness.close()

    asyncio.run(scenario())


def test_turn_failure_records_failed_run_with_error_code() -> None:
    """模型抛 ``AppError(LLM_TIMEOUT)``：运行 failed / turn_error，失败说明带 run_id。"""

    async def scenario():
        harness = await _harness([AppError(ErrorCode.LLM_TIMEOUT, "上游超时")])
        try:
            assert await harness.runtime.acquire_session(harness.session_id) is True
            await _drain(
                stream_legacy_turn(harness.runtime, harness.session_id, "你好", None)
            )
            run = await _single_run(harness)
            assert run.status == AgentRunStatus.FAILED
            assert run.finish_reason == "turn_error"
            assert run.error_code == str(ErrorCode.LLM_TIMEOUT)
            messages = await _messages(harness)
            assert [message.role for message in messages] == ["user", "assistant"]
            notice = messages[-1]
            assert notice.run_id == run.id
            assert "上游超时" in (notice.content or "")
        finally:
            await harness.close()

    asyncio.run(scenario())


def test_stuck_loop_records_failed_run() -> None:
    """连续相同的工具调用：运行 failed / stuck_loop。"""

    async def scenario():
        registry = ToolRegistry()
        _register_counter(registry)
        responses = [tool_response(_round_call(index)) for index in range(10)]
        harness = await _harness(responses, registry=registry)
        try:
            assert await harness.runtime.acquire_session(harness.session_id) is True
            chunks = await _drain(
                stream_legacy_turn(harness.runtime, harness.session_id, "继续", None)
            )
            events = parse_events(chunks)
            assert events[-1][0] == "message_end"
            assert events[-1][1]["finish_reason"] == "stuck_loop"
            run = await _single_run(harness)
            assert run.status == AgentRunStatus.FAILED
            assert run.finish_reason == "stuck_loop"
        finally:
            await harness.close()

    asyncio.run(scenario())


class _StreamingLLM:
    """第一轮流到一半挂住；后续调用（下一轮、标题）按剧本作答。"""

    def __init__(self, first_text: str) -> None:
        self.first_text = first_text
        self.blocked = asyncio.Event()
        self.calls = 0

    async def stream_chat_completion(self, model, messages, tools):
        self.calls += 1
        if self.calls == 1:
            yield {
                "choices": [
                    {
                        "index": 0,
                        "delta": {"content": self.first_text},
                        "finish_reason": None,
                    }
                ]
            }
            self.blocked.set()
            await asyncio.Event().wait()  # 只有被取消才会离开
            return
        if self.calls == 2:
            for chunk in text_response("好的，继续。"):
                yield chunk
            return
        yield {
            "choices": [
                {"index": 0, "delta": {"content": "标题"}, "finish_reason": None}
            ]
        }


def test_interrupt_records_interrupted_run_and_next_turn_starts(tmp_path) -> None:
    """中断后运行 interrupted / interrupted；返回后立刻能发下一条，新的一轮是新运行。"""

    async def scenario():
        harness = await _harness(
            [], database_url=f"sqlite+aiosqlite:///{tmp_path / 'interrupt.sqlite3'}"
        )
        llm = _StreamingLLM("正在读取日志……")
        harness.runtime.client_factory = lambda _settings: llm
        try:
            assert await harness.runtime.acquire_session(harness.session_id) is True
            stream = stream_legacy_turn(
                harness.runtime, harness.session_id, "帮我看看为什么失败", None
            )
            await _next_event(stream, "text_delta")
            await asyncio.wait_for(llm.blocked.wait(), timeout=5)
            assert harness.runtime.turn_running(harness.session_id) is True

            assert await harness.runtime.interrupt_turn(harness.session_id) is True
            await asyncio.wait_for(_drain(stream), timeout=5)

            run = await _single_run(harness)
            assert run.status == AgentRunStatus.INTERRUPTED
            assert run.finish_reason == "interrupted"
            # 终态先落库、再放进程内占位：紧接着的下一条消息不会被误报 409。
            assert await harness.runtime.acquire_session(harness.session_id) is True
            await asyncio.wait_for(
                _drain(
                    stream_legacy_turn(
                        harness.runtime, harness.session_id, "继续", None
                    )
                ),
                timeout=5,
            )
            runs = await _runs(harness)
            assert len(runs) == 2
            assert runs[-1].status == AgentRunStatus.SUCCEEDED
            assert runs[-1].id != runs[0].id
        finally:
            await harness.close()

    asyncio.run(scenario())


class _ConfirmLLM:
    """第一轮发一个危险工具调用；等批准、工具执行完之后在第二次模型调用上挂住。"""

    def __init__(self) -> None:
        self.calls = 0
        self.blocked = asyncio.Event()
        self.release = asyncio.Event()

    async def stream_chat_completion(self, model, messages, tools):
        self.calls += 1
        if self.calls == 1:
            for chunk in tool_response(
                tool_call("confirm-call", "click", {"x": 1, "y": 2})
            ):
                yield chunk
            return
        if self.calls == 2:
            self.blocked.set()
            await self.release.wait()
            for chunk in text_response("已经点过了。"):
                yield chunk
            return
        yield {
            "choices": [
                {"index": 0, "delta": {"content": "标题"}, "finish_reason": None}
            ]
        }


def test_confirmation_wait_sets_waiting_human_then_running() -> None:
    """危险工具等确认时运行是 waiting_human；批准后回到 running，最终 succeeded。"""

    async def scenario():
        executed: list[bool] = []

        async def click(params, _context):
            executed.append(True)
            return {"x": params.x, "y": params.y}

        registry = ToolRegistry()
        registry.register(
            ToolDefinition(
                name="click",
                group="ops",
                risk=ToolRisk.DANGEROUS,
                description="Requires human approval.",
                params_model=ClickParams,
                handler=click,
            )
        )
        harness = await _harness([], registry=registry)
        llm = _ConfirmLLM()
        harness.runtime.client_factory = lambda _settings: llm
        try:
            assert await harness.runtime.acquire_session(harness.session_id) is True
            stream = stream_legacy_turn(
                harness.runtime, harness.session_id, "帮我点一下", None
            )
            required = await _next_event(stream, "confirmation_required")
            run = await _single_run(harness)
            assert run.status == AgentRunStatus.WAITING_HUMAN

            await harness.confirmations.resolve(
                required["confirmation_id"],
                approved=True,
                resolved_by="test",
            )
            await asyncio.wait_for(llm.blocked.wait(), timeout=5)
            run = await _reload_run(harness, run.id)
            assert run.status == AgentRunStatus.RUNNING
            assert executed == [True]

            llm.release.set()
            await asyncio.wait_for(_drain(stream), timeout=5)
            run = await _reload_run(harness, run.id)
            assert run.status == AgentRunStatus.SUCCEEDED
            assert run.finish_reason == "stop"
        finally:
            await harness.close()

    asyncio.run(scenario())


class _BusyLLM:
    """首轮吐出一段文本后挂住；放行后正常收尾，标题调用单独作答。"""

    def __init__(self) -> None:
        self.calls = 0
        self.blocked = asyncio.Event()
        self.release = asyncio.Event()

    async def stream_chat_completion(self, model, messages, tools):
        self.calls += 1
        if self.calls == 1:
            yield {
                "choices": [
                    {
                        "index": 0,
                        "delta": {"content": "正在处理"},
                        "finish_reason": None,
                    }
                ]
            }
            self.blocked.set()
            await self.release.wait()
            for chunk in text_response("处理完了。"):
                yield chunk
            return
        yield {
            "choices": [
                {"index": 0, "delta": {"content": "标题"}, "finish_reason": None}
            ]
        }


def test_second_message_while_turn_running_is_rejected(tmp_path) -> None:
    """同一会话并发两条消息：第二条拿到 AGENT_SESSION_BUSY，库里只有一条运行。"""

    async def scenario():
        harness = await _harness(
            [], database_url=f"sqlite+aiosqlite:///{tmp_path / 'busy.sqlite3'}"
        )
        llm = _BusyLLM()
        harness.runtime.client_factory = lambda _settings: llm
        try:
            assert await harness.runtime.acquire_session(harness.session_id) is True

            async def first_turn() -> list[str]:
                return await _drain(
                    stream_legacy_turn(
                        harness.runtime, harness.session_id, "第一条", None
                    )
                )

            task = asyncio.create_task(first_turn())
            await asyncio.wait_for(llm.blocked.wait(), timeout=5)
            while not harness.runtime.turn_running(harness.session_id):
                await asyncio.sleep(0.01)

            second = await _drain(
                stream_legacy_turn(
                    harness.runtime, harness.session_id, "第二条", None
                )
            )
            assert len(second) == 1
            assert "event: error" in second[0]
            assert "AGENT_SESSION_BUSY" in second[0]

            llm.release.set()
            body = "".join(await asyncio.wait_for(task, timeout=10))
            assert "event: message_end" in body
            assert len(await _runs(harness)) == 1
        finally:
            await harness.close()

    asyncio.run(scenario())


def test_acquire_rejects_when_database_has_active_run_from_live_process() -> None:
    """别的进程、心跳新鲜的 running 运行：占位为假且不留下进程内残留。"""

    async def scenario():
        harness = await _harness([])
        try:
            run_id = new_uuid()
            async with harness.factory() as session:
                await AgentRunRepository(session).create(
                    AgentRun(
                        id=run_id,
                        session_id=harness.session_id,
                        root_run_id=run_id,
                        agent_name="legacy_chat",
                        kind=AgentRunKind.LEGACY_TURN,
                        status=AgentRunStatus.RUNNING,
                        boot_id="other-boot",
                        heartbeat_at=utcnow(),
                        started_at=utcnow(),
                    )
                )
                await session.commit()

            assert await harness.runtime.acquire_session(harness.session_id) is False
            assert harness.session_id not in harness.runtime._locked_sessions
            run = await _reload_run(harness, run_id)
            assert run.status == AgentRunStatus.RUNNING
        finally:
            await harness.close()

    asyncio.run(scenario())


def test_acquire_takes_over_stale_run_from_previous_process() -> None:
    """同样的运行但心跳过期：占位成功，旧运行已经按重启恢复收尾。"""

    async def scenario():
        harness = await _harness([])
        try:
            run_id = new_uuid()
            stale = utcnow() - timedelta(hours=1)
            async with harness.factory() as session:
                await AgentRunRepository(session).create(
                    AgentRun(
                        id=run_id,
                        session_id=harness.session_id,
                        root_run_id=run_id,
                        agent_name="legacy_chat",
                        kind=AgentRunKind.LEGACY_TURN,
                        status=AgentRunStatus.RUNNING,
                        boot_id="other-boot",
                        heartbeat_at=stale,
                        started_at=stale,
                    )
                )
                await session.commit()

            assert await harness.runtime.acquire_session(harness.session_id) is True
            harness.runtime.release_session(harness.session_id)
            run = await _reload_run(harness, run_id)
            assert run.status == AgentRunStatus.INTERRUPTED
            assert run.finish_reason == RECOVERY_FINISH_REASON
            assert run.finished_at is not None
        finally:
            await harness.close()

    asyncio.run(scenario())


def test_legacy_sse_events_are_unchanged() -> None:
    """v2 的事件名、顺序与字段集合不变；所有事件都没有 ``run_id`` 键。"""

    async def scenario():
        harness = await _harness([text_response("你好")])
        try:
            assert await harness.runtime.acquire_session(harness.session_id) is True
            chunks = await _drain(
                stream_legacy_turn(harness.runtime, harness.session_id, "早上好", None)
            )
            events = parse_events(chunks)
            assert [name for name, _data in events] == [
                "message_start",
                "text_delta",
                "message_end",
                "session_title",
            ]
            assert set(events[0][1]) == {
                "session_id",
                "message_id",
                "seq",
                "capability_status",
                "degraded_reason",
            }
            assert set(events[1][1]) == {"text"}
            assert set(events[2][1]) == {"session_id", "finish_reason"}
            assert set(events[3][1]) == {"session_id", "title"}
            assert all("run_id" not in data for _name, data in events)
        finally:
            await harness.close()

    async def tool_scenario():
        registry = ToolRegistry()
        _register_counter(registry)
        harness = await _harness(
            [tool_response(_round_call(0)), text_response("查完了。")],
            registry=registry,
        )
        try:
            assert await harness.runtime.acquire_session(harness.session_id) is True
            chunks = await _drain(
                stream_legacy_turn(harness.runtime, harness.session_id, "查一下", None)
            )
            events = parse_events(chunks)
            starts = [data for name, data in events if name == "tool_start"]
            assert len(starts) == 1
            assert set(starts[0]) == {"tool_call_id", "name", "arguments"}
            assert all("run_id" not in data for _name, data in events)
        finally:
            await harness.close()

    asyncio.run(scenario())
    asyncio.run(tool_scenario())


def test_build_model_context_maps_system_rows_to_prefixed_user() -> None:
    """历史里的 ``role=system`` 行变成带【系统】前缀的 user；首条系统提示不受影响。"""

    built = build_model_context(
        [
            {"role": "user", "content": "上一问"},
            {"role": "system", "content": "服务重启时这一轮被中断。"},
        ],
        system_prompt="SYS",
    )
    # 首条系统提示仍是 system 消息（后面照旧拼上状态摘要段）。
    assert built[0]["role"] == "system"
    assert built[0]["content"].startswith("SYS")
    assert {"role": "user", "content": "【系统】服务重启时这一轮被中断。"} in built
    assert [message["role"] for message in built] == ["system", "user", "user"]
