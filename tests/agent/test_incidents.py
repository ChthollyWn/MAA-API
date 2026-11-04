"""Failure incidents open durable diagnose tickets; same key never diagnoses twice."""

from __future__ import annotations

import asyncio
import json
from collections.abc import Iterable
from types import SimpleNamespace
from typing import Any
from uuid import UUID

from pydantic import BaseModel, ConfigDict
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker
from sqlalchemy.pool import NullPool, StaticPool
from sqlmodel import SQLModel

import maa_api.db.models  # noqa: F401
from maa_api.agent.confirmation import ConfirmationService
from maa_api.agent.harness.events import RunEventBus
from maa_api.agent.incidents import AgentIncidentService, INCIDENT_ALLOWED_TOOLS
from maa_api.agent.policy import PolicyEngine
from maa_api.agent.registry import ToolDefinition, ToolRegistry, ToolRisk
from maa_api.agent.runtime import AgentRuntime
from maa_api.agent.runs.service import RunService
from maa_api.agent.specs.registry import AgentSpecRegistry
from maa_api.agent.tickets.inbox import InboxService
from maa_api.agent.tickets.service import TicketService
from maa_api.agent.tickets.threads import ThreadRouter
from maa_api.agent.tools import build_registry
from maa_api.agent.workers.service import WorkerService
from maa_api.db import session as db_session
from maa_api.db.models import (
    AgentAudit,
    AgentInbox,
    AgentMessage,
    AgentSession,
    Confirmation,
    LogEntry,
    Mandate,
    Pipeline,
    Screenshot,
    Task,
    Ticket,
)
from maa_api.db.repositories.agent import AgentMessageRepository, AgentSessionRepository
from maa_api.db.repositories.ticket import MandateRepository, TicketRepository
from maa_api.domain.enums import TicketStatus
from maa_api.domain.errors import AppError, ErrorCode
from maa_api.settings import LLMSettings, Settings
from tests.fakes.legacy_turn import stream_legacy_turn


class ActiveRunner:
    def __init__(self, active_pipeline_id=None):
        self.active_pipeline_id = active_pipeline_id
        self.active_pipeline_epoch = None
        self.crashed_pipeline_ids: dict[int, set[str]] = {}

    def core_crashed_pipeline_ids(self, generation):
        return tuple(self.crashed_pipeline_ids.get(generation, ()))


class FakeLLM:
    def __init__(self, responses: Iterable[list[dict[str, Any]]]):
        self.responses = list(responses)
        self.models: list[str] = []
        self.requests: list[tuple[list[dict[str, Any]], list[dict[str, Any]]]] = []

    async def stream_chat_completion(self, model, messages, tools):
        self.models.append(model)
        self.requests.append((list(messages), list(tools)))
        for chunk in self.responses.pop(0):
            yield chunk


class EmptyParams(BaseModel):
    model_config = ConfigDict(extra="forbid")


async def _unused_tool(_params, _context):
    raise AssertionError("incident test tool should not perform an external effect")


def _tool_response(name: str):
    return [
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
                                "function": {"name": name, "arguments": "{}"},
                            }
                        ]
                    },
                    "finish_reason": "tool_calls",
                }
            ]
        }
    ]


def _tool_calls_response(*names: str):
    return [
        {
            "choices": [
                {
                    "index": 0,
                    "delta": {
                        "tool_calls": [
                            {
                                "index": index,
                                "id": f"call-{index}",
                                "type": "function",
                                "function": {"name": name, "arguments": "{}"},
                            }
                            for index, name in enumerate(names)
                        ]
                    },
                    "finish_reason": "tool_calls",
                }
            ]
        }
    ]


def _text_response(text: str):
    return [{"choices": [{"index": 0, "delta": {"content": text}, "finish_reason": "stop"}]}]


def _make_pipeline(pipeline_id: str, *, core_epoch=None, error_code="CORE_CRASHED"):
    return Pipeline(
        id=pipeline_id,
        core_id="default",
        core_epoch=core_epoch,
        source="manual",
        priority=0,
        status="failed",
        title="failed pipeline",
        task_count=1,
        error_code=error_code,
        error_message="MaaCore stopped unexpectedly",
    )


def _tool_calls_with_arguments(*calls: tuple[str, dict[str, Any]]):
    """一批带参数的模型工具调用（一个 chunk 里给出全部 tool_calls）。"""
    return [
        {
            "choices": [
                {
                    "index": 0,
                    "delta": {
                        "tool_calls": [
                            {
                                "index": index,
                                "id": f"call-{index}",
                                "type": "function",
                                "function": {
                                    "name": name,
                                    "arguments": json.dumps(args, ensure_ascii=False),
                                },
                            }
                            for index, (name, args) in enumerate(calls)
                        ]
                    },
                    "finish_reason": "tool_calls",
                }
            ]
        }
    ]


def _diagnose_turn(pipeline_id: str, *, status: str = "ready", summary: str = "已定位。"):
    """诊断 Worker 的一轮脚本：先看流水线，再给出结论。"""
    return _tool_calls_with_arguments(
        ("get_pipeline", {"pipeline_id": pipeline_id}),
        ("report", {"status": status, "summary": summary}),
    )


class _Titler:
    async def title_for(self, _text: str) -> str:
        return "自动标题"


class _ModelHandle:
    def __init__(self, llm: FakeLLM) -> None:
        self.client = llm

    async def aclose(self) -> None:
        return None


class _ModelRouter:
    """把诊断 Worker 的模型调用交给 FakeLLM；档案只用于会话行。"""

    profile = SimpleNamespace(
        name="default",
        kind="chat",
        provider="openai_compatible",
        model="test-model",
        base_url="https://llm.test/v1",
        api_key="sk-test",
        context_window=None,
        max_output_tokens=None,
        supports_vision=True,
        supports_tools=True,
        supports_json_schema=True,
        dynamic_context_position="tail_system",
        price_input=None,
        price_cached_input=None,
        price_output=None,
        currency="USD",
        embedding_dim=None,
        local_model_path=None,
        readonly=False,
        key=("default", "test-model"),
    )

    def __init__(self, llm: FakeLLM) -> None:
        self.llm = llm
        self.roles: list[str] = []

    async def resolve(self, role: str) -> Any:
        self.roles.append(str(role))
        return self.profile

    async def resolve_for_spec(self, _spec: Any, **_kwargs: Any) -> Any:
        return self.profile

    def client_for(self, _profile: Any) -> _ModelHandle:
        return _ModelHandle(self.llm)


class _Harness:
    """诊断链路的临时装配：真实工单 / Worker 服务 + FakeLLM。"""

    def __init__(self, *, engine, factory, service, llm, router, worker_service):
        self.engine = engine
        self.factory = factory
        self.service = service
        self.llm = llm
        self.router = router
        self.worker_service = worker_service
        self.events: list[tuple[str, dict[str, Any]]] = []

    async def close(self) -> None:
        await self.worker_service.close()
        await self.engine.dispose()


async def _incident_harness(
    tmp_path,
    *,
    enabled: bool = True,
    runner=None,
    llm_responses: Iterable[list[dict[str, Any]]] = (),
    boot_id: str | None = None,
    core_id: str = "default",
) -> _Harness:
    engine = db_session.make_engine(
        f"sqlite+aiosqlite:///{tmp_path / 'incidents.db'}", poolclass=NullPool
    )
    factory = async_sessionmaker(engine, expire_on_commit=False, class_=AsyncSession)
    async with engine.begin() as connection:
        await connection.run_sync(SQLModel.metadata.create_all)

    registry = AgentSpecRegistry(build_registry())
    registry.load()
    inbox_service = InboxService(factory)
    thread_router = ThreadRouter(factory, inbox_service)
    ticket_service = TicketService(
        factory,
        inbox_service=inbox_service,
        thread_router=thread_router,
        spec_registry=registry,
    )
    confirmations = ConfirmationService(factory, build_registry())
    runtime = AgentRuntime(factory, confirmations, question_service=None)
    llm = FakeLLM(llm_responses)
    model_router = _ModelRouter(llm)
    run_service = RunService(factory, boot_id="m73-t2-boot")
    event_bus = RunEventBus()
    state = SimpleNamespace(
        agent_runtime=runtime,
        model_router=model_router,
        run_event_bus=event_bus,
        run_service=run_service,
        ticket_service=ticket_service,
        inbox_service=inbox_service,
        agent_spec_registry=registry,
    )
    app = SimpleNamespace(state=state)
    worker_service = WorkerService(
        app=app,
        session_factory=factory,
        spec_registry=registry,
        run_service=run_service,
        inbox_service=inbox_service,
        ticket_service=ticket_service,
        thread_router=thread_router,
        delegation=SimpleNamespace(),
        titler=_Titler(),
        event_bus=event_bus,
    )
    state.worker_service = worker_service

    def settings_provider():
        agent = Settings().agent.model_copy(
            update={"auto_diagnose_failures": enabled}
        )
        return Settings().model_copy(update={"agent": agent})

    service = AgentIncidentService(
        factory,
        runtime,
        settings_provider=settings_provider,
        pipeline_runner=runner or ActiveRunner(),
        request=SimpleNamespace(app=app),
        core_id=core_id,
        boot_id=boot_id,
    )
    harness = _Harness(
        engine=engine,
        factory=factory,
        service=service,
        llm=llm,
        router=model_router,
        worker_service=worker_service,
    )
    service.broadcast = lambda kind, payload: harness.events.append((kind, payload))
    return harness


def _disable_worker_wake(harness: _Harness) -> None:
    """多数用只验证建单/收件箱，不让诊断 Worker 真跑一轮。"""
    harness.worker_service.wake = lambda _session_id: None  # type: ignore[method-assign]


async def _seed_failed_pipeline(
    factory,
    pipeline_id: str = "pipeline-1",
    *,
    core_epoch=None,
    error_code: str = "TASK_FAILED",
):
    async with factory() as session:
        session.add(_make_pipeline(pipeline_id, core_epoch=core_epoch, error_code=error_code))
        await session.flush()
        session.add(
            Task(
                id=f"task-{pipeline_id}",
                pipeline_id=pipeline_id,
                order_index=0,
                type_name="Fight",
                task_name="Fight",
                params={},
                status="failed",
                error_code="TASK_FAILED",
                error_message="task ended in error",
            )
        )
        await session.flush()
        for source in ("maa_task", "server", "maacore_debug"):
            session.add(
                LogEntry(
                    source=source,
                    level="error",
                    content=f"{source} diagnostic detail",
                    pipeline_id=pipeline_id,
                )
            )
        session.add(
            Screenshot(
                id=f"crash-shot-{pipeline_id}",
                pipeline_id=pipeline_id,
                task_id=f"task-{pipeline_id}",
                trigger="crash",
                backend="core",
                path="image/screenshot/ab/archive.jpg",
                format="jpg",
                width=16,
                height=16,
                size_bytes=32,
            )
        )
        await session.commit()


async def _ticket(factory, ticket_id: str) -> Ticket | None:
    async with factory() as session:
        return await TicketRepository(session).get(ticket_id)


async def _mandate(factory, ticket_id: str) -> Mandate | None:
    async with factory() as session:
        return await MandateRepository(session).get_by_ticket(ticket_id)


async def _ticket_count(factory) -> int:
    async with factory() as session:
        return int(
            (await session.execute(select(func.count()).select_from(Ticket))).scalar_one()
        )


async def _tickets(factory) -> list[Ticket]:
    async with factory() as session:
        result = await session.execute(select(Ticket).order_by(Ticket.created_at.asc()))
        return list(result.scalars().all())


async def _inbox_items(factory, session_id: str) -> list[AgentInbox]:
    async with factory() as session:
        result = await session.execute(
            select(AgentInbox)
            .where(AgentInbox.session_id == session_id)
            .order_by(AgentInbox.id.asc())
        )
        return list(result.scalars().all())


async def _system_texts(factory, session_id: str) -> list[str]:
    return [
        str((item.payload or {}).get("text") or "")
        for item in await _inbox_items(factory, session_id)
        if str(item.kind) == "system" and (item.payload or {}).get("text")
    ]


def _pending_items(items: list[AgentInbox]) -> list[AgentInbox]:
    return [item for item in items if str(item.status) == "pending"]


async def _messages(factory, session_id: str) -> list[AgentMessage]:
    async with factory() as session:
        return await AgentMessageRepository(session).list_by_session(session_id)


async def _wait_until(predicate, *, timeout: float = 10.0, message: str = "条件未成立"):
    loop = asyncio.get_running_loop()
    deadline = loop.time() + timeout
    while loop.time() < deadline:
        if await predicate():
            return
        await asyncio.sleep(0.01)
    raise AssertionError(message)


async def _wait_for_ticket(
    factory, ticket_id: str, status: TicketStatus, *, timeout: float = 10.0
) -> Ticket:
    loop = asyncio.get_running_loop()
    deadline = loop.time() + timeout
    while loop.time() < deadline:
        ticket = await _ticket(factory, ticket_id)
        if ticket is not None and str(ticket.status) == status.value:
            return ticket
        await asyncio.sleep(0.01)
    ticket = await _ticket(factory, ticket_id)
    raise AssertionError(
        f"工单 {ticket_id} 没有在期限内变成 {status.value}；当前={getattr(ticket, 'status', None)}"
    )


def test_pipeline_failure_opens_one_diagnose_ticket(tmp_path):
    """一次失败只开一张诊断工单；同一键重放只追加收件箱，不重复诊断。"""

    async def scenario():
        harness = await _incident_harness(
            tmp_path, llm_responses=[_diagnose_turn("pipeline-1")]
        )
        try:
            await _seed_failed_pipeline(harness.factory)

            first, second = await asyncio.gather(
                harness.service.pipeline_failed("pipeline-1"),
                harness.service.pipeline_failed("pipeline-1"),
            )

            assert first.session_id == second.session_id
            assert first.incident_key == "pipeline:pipeline-1"
            assert {first.created, second.created} == {True, False}
            # 建单阶段不调用模型；失败先落成一张可见的工单。
            assert harness.llm.requests == []
            assert await _ticket_count(harness.factory) == 1

            ticket = await _ticket(harness.factory, first.session_id)
            assert ticket is not None
            assert ticket.kind == "diagnose"
            assert ticket.origin == "system"
            assert ticket.result is not None
            assert ticket.result["incident_key"] == "pipeline:pipeline-1"
            mandate = await _mandate(harness.factory, ticket.id)
            assert mandate is not None
            assert str(mandate.strength) == "inferred"
            assert str(mandate.risk_ceiling) == "none"
            assert mandate.scope == (
                "只诊断这一次故障并给出下一步。不提交消耗类任务，不改设置，不重装内核。"
            )

            system_texts = await _system_texts(harness.factory, ticket.worker_session_id)
            assert len(system_texts) == 2
            assert system_texts[0].startswith("自动诊断：")
            assert "pipeline-1" in system_texts[0]
            assert "终态: failed" in system_texts[0]
            assert "错误码: TASK_FAILED" in system_texts[0]
            assert "crash-shot-pipeline-1" in system_texts[0]
            assert "pipeline-1" in system_texts[1]

            # 诊断 Worker 真跑一轮：get_pipeline 后 report(ready)。
            harness.worker_service.wake(ticket.worker_session_id)
            done = await _wait_for_ticket(
                harness.factory, ticket.id, TicketStatus.DONE
            )
            assert len(harness.llm.requests) == 1
            session_id = ticket.worker_session_id

            async def tools_persisted() -> bool:
                messages = await _messages(harness.factory, session_id)
                return len(
                    [message for message in messages if message.role == "tool"]
                ) >= 2

            await _wait_until(tools_persisted, message="诊断工具结果没有落库")
            messages = await _messages(harness.factory, session_id)
            tool_messages = [message for message in messages if message.role == "tool"]
            assert [message.tool_name for message in tool_messages] == [
                "get_pipeline",
                "report",
            ]
            payload = json.loads(tool_messages[0].content)
            assert payload["result"]["id"] == "pipeline-1"
            # 终态汇报不得删掉建单时写入的 incident_key（规格 §4.1）。
            assert done.result is not None
            assert done.result["incident_key"] == "pipeline:pipeline-1"
            report_events = [
                data for kind, data in harness.events if kind == "agent_event"
            ]
            assert [event["event"] for event in report_events] == ["incident_created"]
        finally:
            await harness.close()

    asyncio.run(scenario())


def test_same_key_appends_inbox(tmp_path):
    """同一 pipeline 再失败一次：工单数不变，Worker 收件箱多一条 system。"""

    async def scenario():
        harness = await _incident_harness(tmp_path)
        _disable_worker_wake(harness)
        try:
            await _seed_failed_pipeline(harness.factory)

            first = await harness.service.pipeline_failed("pipeline-1")
            first_ticket = await _ticket(harness.factory, first.session_id)
            first_items = _pending_items(
                await _inbox_items(harness.factory, first_ticket.worker_session_id)
            )
            assert len(first_items) == 1

            replay = await harness.service.pipeline_failed("pipeline-1")

            assert replay.created is False
            assert replay.session_id == first.session_id
            assert await _ticket_count(harness.factory) == 1
            ticket = await _ticket(harness.factory, first.session_id)
            items = _pending_items(
                await _inbox_items(harness.factory, ticket.worker_session_id)
            )
            assert len(items) == len(first_items) + 1
            assert str(items[-1].kind) == "system"
            assert "pipeline-1" in items[-1].payload["text"]
            assert "错误码: TASK_FAILED" in items[-1].payload["text"]
            async with harness.factory() as session:
                sessions = await AgentSessionRepository(session).list_recent()
            assert len([item for item in sessions if str(item.kind) == "worker"]) == 1
        finally:
            await harness.close()

    asyncio.run(scenario())


def test_late_core_crash_updates_the_existing_pipeline_ticket(tmp_path):
    """迟到的 CORE_CRASHED 补进同一张诊断工单的收件箱，不再开新单。"""

    async def scenario():
        harness = await _incident_harness(tmp_path)
        _disable_worker_wake(harness)
        try:
            async with harness.factory() as session:
                session.add(
                    _make_pipeline(
                        "pipeline-late-core",
                        core_epoch=4,
                        error_code=ErrorCode.INTERNAL_ERROR.value,
                    )
                )
                await session.commit()

            first = await harness.service.pipeline_failed("pipeline-late-core")
            async with harness.factory() as session:
                pipeline = await session.get(Pipeline, "pipeline-late-core")
                pipeline.error_code = ErrorCode.CORE_CRASHED.value
                pipeline.error_message = "MaaCore 子进程在 claim 清理期间崩溃"
                await session.commit()

            replay = await harness.service.pipeline_failed("pipeline-late-core")
            third = await harness.service.pipeline_failed("pipeline-late-core")

            assert replay.session_id == first.session_id
            assert replay.created is False
            assert third.session_id == first.session_id
            assert await _ticket_count(harness.factory) == 1
            ticket = await _ticket(harness.factory, first.session_id)
            items = _pending_items(
                await _inbox_items(harness.factory, ticket.worker_session_id)
            )
            update_key = "pipeline:pipeline-late-core:core:4"
            updates = [item for item in items if item.ref_id == update_key]
            assert len(updates) == 1
            update = updates[0].payload["text"]
            assert "pipeline-late-core" in update
            assert "CORE_CRASHED" in update
            assert update_key in update
            # 重放本身也追加一条「这条流水线又失败了」的说明。
            generic = [
                item
                for item in items
                if item.ref_id == f"incident:{ticket.id}"
            ]
            assert len(items) == 4
            assert len(generic) == 2
            events = [
                data["event"] for _kind, data in harness.events
                if _kind == "agent_event"
            ]
            assert events.count("incident_created") == 1
            assert events.count("incident_context_updated") == 1
        finally:
            await harness.close()

    asyncio.run(scenario())


def test_idle_core_generation_is_deduplicated_and_active_pipeline_is_coalesced(tmp_path):
    async def scenario():
        harness = await _incident_harness(tmp_path)
        _disable_worker_wake(harness)
        try:
            first = await harness.service.core_crashed(17, {"reason": "process_exit"})
            second = await harness.service.core_crashed(17, {"reason": "process_exit"})
            assert first.session_id == second.session_id
            assert first.session_id is not None
            assert first.created is True and second.created is False
            assert await _ticket_count(harness.factory) == 1
            ticket = await _ticket(harness.factory, first.session_id)
            assert ticket.kind == "diagnose"
            assert ticket.result["incident_key"] == (
                f"core:default:{harness.service.boot_id}:17"
            )

            harness.service.pipeline_runner.active_pipeline_id = "pipeline-active"
            harness.service.pipeline_runner.active_pipeline_epoch = 18
            coalesced = await harness.service.core_crashed(18, {"reason": "process_exit"})
            assert coalesced.session_id is None
            assert coalesced.status == "coalesced_with_active_pipeline"
            assert await _ticket_count(harness.factory) == 1
        finally:
            await harness.close()

    asyncio.run(scenario())


def test_idle_core_crash_uses_current_boot_same_generation_pipeline_incident(tmp_path):
    async def scenario():
        harness = await _incident_harness(tmp_path)
        _disable_worker_wake(harness)
        try:
            await _seed_failed_pipeline(
                harness.factory,
                "pipeline-crashed",
                core_epoch=23,
                error_code=ErrorCode.CORE_CRASHED.value,
            )
            harness.service.pipeline_runner.crashed_pipeline_ids[23] = {
                "pipeline-crashed"
            }

            result = await harness.service.core_crashed(23, {"reason": "process_exit"})
            assert result.session_id is not None
            assert await _ticket_count(harness.factory) == 1
            ticket = await _ticket(harness.factory, result.session_id)
            assert ticket.kind == "diagnose"
            assert ticket.result["incident_key"] == "pipeline:pipeline-crashed"
        finally:
            await harness.close()

    asyncio.run(scenario())


def test_core_incident_key_separates_service_boots_but_replays_within_one_boot(tmp_path):
    async def scenario():
        harness = await _incident_harness(tmp_path)
        _disable_worker_wake(harness)
        other_boot = "22222222-2222-4222-8222-222222222222"
        service_two = AgentIncidentService(
            harness.factory,
            harness.service.runtime,
            settings_provider=harness.service.settings_provider,
            pipeline_runner=ActiveRunner(),
            request=harness.service.request,
            boot_id=other_boot,
        )
        try:
            assert UUID(harness.service.boot_id) != UUID(service_two.boot_id)
            first = await harness.service.core_crashed(17, {"reason": "process_exit"})
            replay = await harness.service.core_crashed(17, {"reason": "process_exit"})
            after_restart = await service_two.core_crashed(17, {"reason": "process_exit"})

            assert first.session_id == replay.session_id
            assert after_restart.session_id != first.session_id
            assert first.incident_key == (
                f"core:default:{harness.service.boot_id}:17"
            )
            assert after_restart.incident_key == f"core:default:{other_boot}:17"
            assert await _ticket_count(harness.factory) == 2
            first_ticket = await _ticket(harness.factory, first.session_id)
            second_ticket = await _ticket(harness.factory, after_restart.session_id)
            assert first_ticket.result["incident_key"] == first.incident_key
            assert second_ticket.result["incident_key"] == after_restart.incident_key
        finally:
            await harness.close()

    asyncio.run(scenario())


def test_new_boot_does_not_coalesce_old_core_epoch_pipeline(tmp_path):
    async def scenario():
        harness = await _incident_harness(tmp_path)
        _disable_worker_wake(harness)
        try:
            await _seed_failed_pipeline(
                harness.factory, "pipeline-old-boot", core_epoch=17
            )

            result = await harness.service.core_crashed(17, {"reason": "process_exit"})

            assert result.session_id is not None
            assert result.incident_key == f"core:default:{harness.service.boot_id}:17"
            ticket = await _ticket(harness.factory, result.session_id)
            assert ticket.result["incident_key"] == result.incident_key
            assert ticket.result["incident_key"] != "pipeline:pipeline-old-boot"
        finally:
            await harness.close()

    asyncio.run(scenario())


def test_auto_diagnose_off_opens_nothing(tmp_path):
    async def scenario():
        harness = await _incident_harness(tmp_path, enabled=False)
        try:
            await _seed_failed_pipeline(harness.factory)
            pipeline_result = await harness.service.pipeline_failed("pipeline-1")
            core_result = await harness.service.core_crashed(3, {"reason": "process_exit"})

            assert pipeline_result.session_id is None
            assert pipeline_result.status == "disabled"
            assert core_result.session_id is None
            assert core_result.status == "disabled"
            assert await _ticket_count(harness.factory) == 0
            assert harness.llm.requests == []
            async with harness.factory() as session:
                assert await AgentSessionRepository(session).list_recent() == []
            async with harness.factory() as session:
                inbox_count = int(
                    (
                        await session.execute(
                            select(func.count()).select_from(AgentInbox)
                        )
                    ).scalar_one()
                )
            assert inbox_count == 0
        finally:
            await harness.close()

    asyncio.run(scenario())


def test_inferred_mandate_blocks_consumption(tmp_path):
    """诊断工单是推断委托、上限 none：带理智药的提交被拦且不建确认。"""

    async def scenario():
        harness = await _incident_harness(
            tmp_path,
            llm_responses=[
                _tool_calls_with_arguments(
                    (
                        "submit_pipeline",
                        {
                            "title": "试一下",
                            "tasks": [
                                {
                                    "name": "Fight",
                                    "stage": "1-7",
                                    "times": 1,
                                    "medicine": 1,
                                }
                            ],
                        },
                    ),
                    (
                        "report",
                        {
                            "status": "blocked",
                            "summary": "超出委托范围，不能用药。",
                            "human_action": {
                                "type": "decision",
                                "prompt": "要另开工单允许用药吗？",
                            },
                        },
                    ),
                )
            ],
        )
        try:
            await _seed_failed_pipeline(harness.factory)
            result = await harness.service.pipeline_failed("pipeline-1")
            ticket = await _ticket(harness.factory, result.session_id)
            harness.worker_service.wake(ticket.worker_session_id)

            blocked = await _wait_for_ticket(
                harness.factory, ticket.id, TicketStatus.BLOCKED
            )
            assert blocked.last_report_status == "blocked"
            mandate = await _mandate(harness.factory, ticket.id)
            assert str(mandate.strength) == "inferred"
            assert str(mandate.risk_ceiling) == "none"

            async def submit_persisted() -> bool:
                messages = await _messages(
                    harness.factory, ticket.worker_session_id
                )
                return any(
                    message.role == "tool"
                    and message.tool_name == "submit_pipeline"
                    for message in messages
                )

            await _wait_until(submit_persisted, message="submit_pipeline 的工具结果没有落库")
            messages = await _messages(harness.factory, ticket.worker_session_id)
            submits = [
                message
                for message in messages
                if message.role == "tool" and message.tool_name == "submit_pipeline"
            ]
            assert len(submits) == 1
            assert json.loads(submits[0].content)["error"]["code"] == (
                "MANDATE_SCOPE_EXCEEDED"
            )
            async with harness.factory() as session:
                confirmations = int(
                    (
                        await session.execute(
                            select(func.count()).select_from(Confirmation)
                        )
                    ).scalar_one()
                )
                audits = int(
                    (
                        await session.execute(
                            select(func.count())
                            .select_from(AgentAudit)
                            .where(
                                AgentAudit.session_id == ticket.worker_session_id,
                                AgentAudit.tool_name == "submit_pipeline",
                            )
                        )
                    ).scalar_one()
                )
            assert confirmations == 0
            assert audits == 0
        finally:
            await harness.close()

    asyncio.run(scenario())


def test_incident_configuration_failure_is_saved_as_assistant_explanation(tmp_path):
    """v2 诊断会话的直接回放入口仍保底：LLM 未配置时先落一条可见说明。"""

    async def scenario():
        settings_state = {
            "value": Settings(
                llm=LLMSettings(
                    base_url="https://llm.initial/v1",
                    api_key="",
                    model="initial-model",
                )
            )
        }
        engine = db_session.make_engine(
            f"sqlite+aiosqlite:///{tmp_path / 'runtime-failure.db'}", poolclass=StaticPool
        )
        factory = async_sessionmaker(engine, expire_on_commit=False, class_=AsyncSession)
        async with engine.begin() as connection:
            await connection.run_sync(SQLModel.metadata.create_all)
        confirmations = ConfirmationService(factory, ToolRegistry(), policy=PolicyEngine())
        runtime = AgentRuntime(
            factory,
            confirmations,
            settings_provider=lambda _request: settings_state["value"],
        )
        try:
            async with factory() as session:
                await AgentSessionRepository(session).create(
                    AgentSession(
                        id="incident-config",
                        title="自动诊断",
                        kind="incident",
                        model="unconfigured",
                        base_url=None,
                        incident_key="pipeline:pipeline-config",
                    )
                )
                await AgentMessageRepository(session).create(
                    AgentMessage(
                        session_id="incident-config",
                        seq=0,
                        role="user",
                        content='{"incident_type":"pipeline_failure","failure_object":{}}',
                    )
                )
                await session.commit()

            result = await runtime.run_incident(
                "incident-config", None, INCIDENT_ALLOWED_TOOLS
            )
            assert result["status"] == "failed"
            async with factory() as session:
                messages = await AgentMessageRepository(session).list_by_session(
                    "incident-config"
                )
            assert [message.role for message in messages] == ["user", "assistant"]
            assert "LLM" in messages[1].content
            assert "api_key" in messages[1].content

            settings_state["value"] = Settings(
                llm=LLMSettings(
                    base_url="https://llm.test/v1",
                    api_key="repaired-secret",
                    model="test-model",
                )
            )
            llm = FakeLLM([_text_response("可以继续检查。")])
            runtime.client_factory = lambda _settings: llm
            runtime._capability_cache[("test-model", "https://llm.test/v1")] = {
                "tool_calling": True,
                "vision": True,
                "status": "ready",
                "degraded_reason": None,
            }
            assert await runtime.acquire_session("incident-config")
            chunks = [
                chunk
                async for chunk in stream_legacy_turn(
                    runtime, "incident-config", "继续诊断", None
                )
            ]
            assert llm.models == ["test-model"]
            assert "event: message_end" in "".join(chunks)
            async with factory() as session:
                stored = await AgentSessionRepository(session).get("incident-config")
            assert stored.model == "test-model"
            assert stored.base_url == "https://llm.test/v1"
        finally:
            await engine.dispose()

    asyncio.run(scenario())


def test_incident_schema_exposes_every_registered_recovery_tool():
    runtime = object.__new__(AgentRuntime)
    schemas = runtime._tool_schemas(
        build_registry(), vision_enabled=True, allowed_tools=INCIDENT_ALLOWED_TOOLS
    )
    assert {item["function"]["name"] for item in schemas} == INCIDENT_ALLOWED_TOOLS


def test_recovery_allowlist_excludes_trigger_screencap_and_its_schema():
    """整帧 PNG 会把上下文撑爆并让会话失效，诊断白名单不再包含它（规格 §4.4）。"""

    assert "trigger_screencap" not in INCIDENT_ALLOWED_TOOLS

    runtime = object.__new__(AgentRuntime)
    schemas = runtime._tool_schemas(
        build_registry(), vision_enabled=True, allowed_tools=INCIDENT_ALLOWED_TOOLS
    )
    names = {item["function"]["name"] for item in schemas}
    assert names == INCIDENT_ALLOWED_TOOLS
    assert "trigger_screencap" not in names


def test_incident_schema_and_dispatch_both_enforce_the_recovery_allowlist(tmp_path):
    async def scenario():
        registry = ToolRegistry()
        registry.register(
            ToolDefinition(
                name="get_logs", group="status", risk=ToolRisk.SAFE,
                description="read logs", params_model=EmptyParams, handler=_unused_tool,
                read_only=True,
            )
        )
        forbidden = (
            "submit_pipeline", "update_core", "update_resource", "update_schedule",
            "restart_core", "click", "swipe", "upload_copilot", "delete_resource",
        )
        for name in forbidden:
            registry.register(
                ToolDefinition(
                    name=name, group="mutation", risk=ToolRisk.DANGEROUS,
                    description="not available to automatic recovery",
                    params_model=EmptyParams, handler=_unused_tool,
                )
            )
        engine = db_session.make_engine(
            f"sqlite+aiosqlite:///{tmp_path / 'allowlist.db'}", poolclass=StaticPool
        )
        factory = async_sessionmaker(engine, expire_on_commit=False, class_=AsyncSession)
        async with engine.begin() as connection:
            await connection.run_sync(SQLModel.metadata.create_all)
        confirmations = ConfirmationService(factory, registry, policy=PolicyEngine())
        llm = FakeLLM([_tool_calls_response(*forbidden), _text_response("已检查。")])
        settings = Settings(
            llm=LLMSettings(base_url="https://llm.test/v1", api_key="secret", model="test")
        )
        runtime = AgentRuntime(
            factory, confirmations,
            settings_provider=lambda _request: settings,
            client_factory=lambda _settings: llm,
        )
        runtime._capability_cache[("test", "https://llm.test/v1")] = {
            "tool_calling": True, "vision": True, "status": "ready",
            "degraded_reason": None,
        }
        async with factory() as session:
            stored = await AgentSessionRepository(session).create(
                AgentSession(id="restricted", model="test", base_url="https://llm.test/v1")
            )
            await AgentMessageRepository(session).create(
                AgentMessage(session_id=stored.id, seq=0, role="user", content="诊断故障")
            )
            await session.commit()

        original_invoke = confirmations.invoke
        invoke_calls = []

        async def tracked_invoke(*args, **kwargs):
            invoke_calls.append(args[0])
            return await original_invoke(*args, **kwargs)

        confirmations.invoke = tracked_invoke
        try:
            await runtime.run_incident(
                "restricted", None, INCIDENT_ALLOWED_TOOLS | {"submit_pipeline"}
            )
            sent_messages, sent_tools = llm.requests[0]
            names = {item["function"]["name"] for item in sent_tools}
            assert names == {"get_logs"}
            async with factory() as session:
                messages = await AgentMessageRepository(session).list_by_session("restricted")
            tool_replies = [message for message in messages if message.role == "tool"]
            assert len(tool_replies) == len(forbidden)
            assert all(
                json.loads(message.content)["error"]["code"] == ErrorCode.TOOL_NOT_FOUND
                for message in tool_replies
            )
            assert invoke_calls == []
            # The initial user prompt reaches the model as persisted conversation history.
            assert any(item.get("content") == "诊断故障" for item in sent_messages)
        finally:
            await engine.dispose()

    asyncio.run(scenario())


def test_incident_allowed_action_still_uses_confirmation_service(tmp_path):
    async def scenario():
        registry = ToolRegistry()
        registry.register(
            ToolDefinition(
                name="get_device_status", group="device", risk=ToolRisk.DANGEROUS,
                description="status read that requires confirmation by policy",
                params_model=EmptyParams, handler=_unused_tool,
            )
        )
        engine = db_session.make_engine(
            f"sqlite+aiosqlite:///{tmp_path / 'confirmation.db'}", poolclass=StaticPool
        )
        factory = async_sessionmaker(engine, expire_on_commit=False, class_=AsyncSession)
        async with engine.begin() as connection:
            await connection.run_sync(SQLModel.metadata.create_all)
        confirmations = ConfirmationService(factory, registry, policy=PolicyEngine())
        llm = FakeLLM([_tool_response("get_device_status")])
        settings = Settings(
            llm=LLMSettings(base_url="https://llm.test/v1", api_key="secret", model="test")
        )
        runtime = AgentRuntime(
            factory, confirmations, settings_provider=lambda _request: settings,
            client_factory=lambda _settings: llm,
        )
        runtime._capability_cache[("test", "https://llm.test/v1")] = {
            "tool_calling": True, "vision": True, "status": "ready",
            "degraded_reason": None,
        }
        async with factory() as session:
            stored = await AgentSessionRepository(session).create(
                AgentSession(id="confirmed", model="test", base_url="https://llm.test/v1")
            )
            await AgentMessageRepository(session).create(
                AgentMessage(
                    session_id=stored.id,
                    seq=0,
                    role="user",
                    content="停止失败的任务",
                )
            )
            await session.commit()
        events = []
        confirmations.broadcast = lambda kind, data: events.append((kind, data))
        try:
            run = asyncio.create_task(
                runtime.run_incident("confirmed", None, {"get_device_status"})
            )
            for _ in range(100):
                request_event = next(
                    (data for kind, data in events if kind == "confirm_request"), None
                )
                if request_event is not None:
                    break
                await asyncio.sleep(0.01)
            assert request_event is not None
            assert request_event["tool"] == "get_device_status"
            await confirmations.resolve(
                request_event["confirmation_id"],
                approved=False,
                resolved_by="test",
                reason="test confirms the standard policy gate is active",
            )
            # 已知 flake（M68 记录，docs/v3/13 §8.26；M71 §8.44 再加固）：整包满载下
            # 调度停顿可能超过 15 秒，单跑与整文件重跑均通过；放宽到 30 秒，断言不变。
            await asyncio.wait_for(run, timeout=60)
        finally:
            if "run" in locals() and not run.done():
                run.cancel()
                await asyncio.gather(run, return_exceptions=True)
            await engine.dispose()

    asyncio.run(scenario())


# ---------------------------------------------------------------------------
# M18：诊断会话同样注入知识库段，且可以调用 read_knowledge（规格 §4.1）
# ---------------------------------------------------------------------------
def _read_knowledge_response(entry_id: str):
    return [
        {
            "choices": [
                {
                    "index": 0,
                    "delta": {
                        "tool_calls": [
                            {
                                "index": 0,
                                "id": "call-knowledge",
                                "type": "function",
                                "function": {
                                    "name": "read_knowledge",
                                    "arguments": json.dumps({"entry_id": entry_id}),
                                },
                            }
                        ]
                    },
                    "finish_reason": "tool_calls",
                }
            ]
        }
    ]


def test_recovery_allowlist_includes_read_knowledge_and_exposes_its_schema():
    from maa_api.agent.knowledge import ENTRY_ID_PATTERN

    assert "read_knowledge" in INCIDENT_ALLOWED_TOOLS

    runtime = object.__new__(AgentRuntime)
    schemas = runtime._tool_schemas(
        build_registry(), vision_enabled=True, allowed_tools=INCIDENT_ALLOWED_TOOLS
    )
    by_name = {item["function"]["name"]: item for item in schemas}
    assert "read_knowledge" in by_name
    assert (
        by_name["read_knowledge"]["function"]["parameters"]["properties"]["entry_id"][
            "pattern"
        ]
        == ENTRY_ID_PATTERN.pattern
    )


def test_diagnosis_turn_injects_the_knowledge_index_and_can_read_an_entry(tmp_path):
    from types import SimpleNamespace

    from maa_api.agent.knowledge import KnowledgeService

    async def scenario():
        engine = db_session.make_engine(
            f"sqlite+aiosqlite:///{tmp_path / 'incident-knowledge.db'}",
            poolclass=StaticPool,
        )
        factory = async_sessionmaker(engine, expire_on_commit=False, class_=AsyncSession)
        async with engine.begin() as connection:
            await connection.run_sync(SQLModel.metadata.create_all)

        knowledge = KnowledgeService(factory)
        confirmations = ConfirmationService(factory, build_registry(), policy=PolicyEngine())
        llm = FakeLLM(
            [_read_knowledge_response("screen-home"), _text_response("已按剧本检查。")]
        )
        settings = Settings(
            llm=LLMSettings(base_url="https://llm.test/v1", api_key="secret", model="test")
        )
        runtime = AgentRuntime(
            factory,
            confirmations,
            settings_provider=lambda _request: settings,
            client_factory=lambda _settings: llm,
        )
        runtime._capability_cache[("test", "https://llm.test/v1")] = {
            "tool_calling": True,
            "vision": True,
            "status": "ready",
            "degraded_reason": None,
        }
        # 生产装配里知识库挂在 app.state 上；诊断会话的请求对象由事件服务提供。
        request = SimpleNamespace(
            app=SimpleNamespace(state=SimpleNamespace(knowledge_service=knowledge))
        )
        async with factory() as session:
            await AgentSessionRepository(session).create(
                AgentSession(id="diagnosed", model="test", base_url="https://llm.test/v1")
            )
            await AgentMessageRepository(session).create(
                AgentMessage(session_id="diagnosed", seq=0, role="user", content="核心崩溃了")
            )
            await session.commit()

        try:
            result = await runtime.run_incident("diagnosed", request, INCIDENT_ALLOWED_TOOLS)
            assert result == {"status": "completed"}

            system = llm.requests[0][0][0]["content"]
            assert llm.requests[0][0][0]["role"] == "system"
            assert "知识库索引（开发者与用户维护" in system
            assert "- screen-home｜首页布局" in system
            assert system.index("你是 MAA-API 内置 Agent") < system.index("知识库索引（")
            assert system.index("知识库索引（") < system.index("当前服务状态摘要")

            async with factory() as session:
                messages = await AgentMessageRepository(session).list_by_session("diagnosed")
            tool_messages = [message for message in messages if message.role == "tool"]
            assert len(tool_messages) == 1
            payload = json.loads(tool_messages[0].content)["result"]
            assert payload["id"] == "screen-home"
            assert payload["kind"] == "screen"
            assert payload["source"] == "builtin"
            assert payload["body"].startswith("位置用占截图宽、高的比例表示")
            assert payload["updated_at"] is None
        finally:
            await engine.dispose()

    asyncio.run(scenario())
