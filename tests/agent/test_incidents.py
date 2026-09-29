"""Failure incidents are durable, deduplicated, and run with a restricted tool set."""

from __future__ import annotations

import asyncio
import json
from collections.abc import Iterable
from typing import Any
from uuid import UUID

from pydantic import BaseModel, ConfigDict
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker
from sqlalchemy.pool import NullPool, StaticPool
from sqlmodel import SQLModel

import maa_api.db.models  # noqa: F401
from maa_api.agent.confirmation import ConfirmationService
from maa_api.agent.incidents import AgentIncidentService, INCIDENT_ALLOWED_TOOLS
from maa_api.agent.policy import PolicyEngine
from maa_api.agent.registry import ToolDefinition, ToolRegistry, ToolRisk
from maa_api.agent.runtime import AgentRuntime
from maa_api.agent.tools import build_registry
from maa_api.db import session as db_session
from maa_api.db.models import AgentMessage, AgentSession, LogEntry, Pipeline, Screenshot, Task
from maa_api.db.repositories.agent import AgentMessageRepository, AgentSessionRepository
from maa_api.domain.errors import AppError, ErrorCode
from maa_api.settings import LLMSettings, Settings


class FakeRuntime:
    def __init__(self, session_factory):
        self.session_factory = session_factory
        self.calls: list[tuple[str, Any, frozenset[str]]] = []

    async def run_incident(self, session_id, request, allowed_tools):
        self.calls.append((session_id, request, frozenset(allowed_tools)))
        async with self.session_factory() as session:
            repo = AgentMessageRepository(session)
            await repo.create(
                AgentMessage(
                    session_id=session_id,
                    seq=await repo.next_seq(session_id),
                    role="assistant",
                    content="检查完成。",
                )
            )
            await session.commit()


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


async def _incident_harness(tmp_path, *, enabled=True, runner=None, runtime=None):
    engine = db_session.make_engine(
        f"sqlite+aiosqlite:///{tmp_path / 'incidents.db'}", poolclass=NullPool
    )
    factory = async_sessionmaker(engine, expire_on_commit=False, class_=AsyncSession)
    async def create_schema():
        async with engine.begin() as connection:
            await connection.run_sync(SQLModel.metadata.create_all)

    await create_schema()
    actual_runtime = runtime or FakeRuntime(factory)

    def settings_provider():
        agent = Settings().agent.model_copy(
            update={"auto_diagnose_failures": enabled}
        )
        return Settings().model_copy(update={"agent": agent})

    service = AgentIncidentService(
        factory,
        actual_runtime,
        settings_provider=settings_provider,
        pipeline_runner=runner or ActiveRunner(),
    )
    return service, factory, actual_runtime, engine


def test_pipeline_failure_replay_reuses_persisted_incident_and_does_not_recall_llm(tmp_path):
    async def scenario():
        service, factory, runtime, engine = await _incident_harness(tmp_path)
        events = []
        service.broadcast = lambda kind, payload: events.append((kind, payload))
        try:
            async with factory() as session:
                session.add(_make_pipeline("pipeline-1", error_code="TASK_FAILED"))
                await session.flush()
                session.add(
                    Task(
                        id="task-1",
                        pipeline_id="pipeline-1",
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
                            pipeline_id="pipeline-1",
                        )
                    )
                session.add(
                    Screenshot(
                        id="crash-shot-1",
                        pipeline_id="pipeline-1",
                        task_id="task-1",
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

            first, second = await asyncio.gather(
                service.pipeline_failed("pipeline-1"),
                service.pipeline_failed("pipeline-1"),
            )

            assert first.session_id == second.session_id
            assert first.created is True
            assert second.created is False
            assert len(runtime.calls) == 1
            assert [event[1]["event"] for event in events] == [
                "incident_created", "incident_finished"
            ]
            assert runtime.calls[0][2] == INCIDENT_ALLOWED_TOOLS
            async with factory() as session:
                messages = await AgentMessageRepository(session).list_by_session(first.session_id)
                stored = await AgentSessionRepository(session).get(first.session_id)
            assert stored.incident_key == "pipeline:pipeline-1"
            assert stored.title.startswith("[自动诊断]")
            assert [message.role for message in messages] == [
                "user", "assistant", "tool", "assistant"
            ]
            assert "TASK_FAILED" in messages[0].content
            assert "status" in messages[0].content
            context = json.loads(messages[0].content)
            assert set(context["error_log_index"]) == {"task", "service", "core"}
            snapshot_result = json.loads(messages[2].content)["result"]
            assert snapshot_result["screenshot_id"] == "crash-shot-1"
            assert snapshot_result["snapshot_source"] == "existing_crash_archive"
            assert "base64" not in "".join(message.content or "" for message in messages)
        finally:
            await engine.dispose()

    asyncio.run(scenario())


def test_late_core_crash_updates_the_existing_pipeline_incident_context(tmp_path):
    async def scenario():
        service, factory, default_runtime, engine = await _incident_harness(tmp_path)
        runtime = default_runtime
        runtime.acquire_attempts = 0
        runtime.releases = 0
        async def acquire_session(_session_id):
            runtime.acquire_attempts += 1
            return runtime.acquire_attempts >= 3
        runtime.acquire_session = acquire_session
        runtime.release_session = lambda _session_id: setattr(
            runtime, "releases", runtime.releases + 1
        )
        events = []
        service.broadcast = lambda kind, payload: events.append((kind, payload))
        try:
            async with factory() as session:
                session.add(
                    _make_pipeline(
                        "pipeline-late-core",
                        core_epoch=4,
                        error_code=ErrorCode.INTERNAL_ERROR.value,
                    )
                )
                await session.commit()

            first = await service.pipeline_failed("pipeline-late-core")
            async with factory() as session:
                pipeline = await session.get(Pipeline, "pipeline-late-core")
                pipeline.error_code = ErrorCode.CORE_CRASHED.value
                pipeline.error_message = "MaaCore 子进程在 claim 清理期间崩溃"
                await session.commit()

            replay = await service.pipeline_failed("pipeline-late-core")
            async with factory() as session:
                messages = await AgentMessageRepository(session).list_by_session(
                    first.session_id
                )
                sessions = await AgentSessionRepository(session).list_recent()

            assert replay.session_id == first.session_id
            assert replay.created is False
            assert [message.role for message in messages] == [
                "user", "assistant", "user", "assistant"
            ]
            update = json.loads(messages[2].content)
            assert update["incident_type"] == "pipeline_core_crash_update"
            assert update["failure_object"]["error_code"] == ErrorCode.CORE_CRASHED.value
            assert update["incident_update_key"] == (
                "pipeline:pipeline-late-core:core:4"
            )
            assert len(sessions) == 1
            assert len(runtime.calls) == 1
            assert runtime.acquire_attempts == 3
            assert runtime.releases == 1
            assert [event[1]["event"] for event in events] == [
                "incident_created",
                "incident_finished",
                "incident_context_updated",
            ]
        finally:
            await engine.dispose()

    asyncio.run(scenario())


def test_idle_core_generation_is_deduplicated_and_active_pipeline_is_coalesced(tmp_path):
    async def scenario():
        service, factory, runtime, engine = await _incident_harness(tmp_path)
        try:
            first = await service.core_crashed(17, {"reason": "process_exit"})
            second = await service.core_crashed(17, {"reason": "process_exit"})
            assert first.session_id == second.session_id
            assert first.session_id is not None
            assert first.created is True and second.created is False
            assert len(runtime.calls) == 1
            async with factory() as session:
                stored = await AgentSessionRepository(session).get(first.session_id)
            assert stored.incident_key == f"core:default:{service.boot_id}:17"

            service.pipeline_runner.active_pipeline_id = "pipeline-active"
            service.pipeline_runner.active_pipeline_epoch = 18
            coalesced = await service.core_crashed(18, {"reason": "process_exit"})
            assert coalesced.session_id is None
            assert len(runtime.calls) == 1
        finally:
            await engine.dispose()

    asyncio.run(scenario())


def test_unique_key_resolves_cross_service_creation_race_before_second_llm_call(tmp_path):
    async def scenario():
        service_one, factory, runtime_one, engine = await _incident_harness(tmp_path)
        runtime_two = FakeRuntime(factory)
        service_two = AgentIncidentService(
            factory,
            runtime_two,
            settings_provider=lambda: Settings(),
            pipeline_runner=ActiveRunner(),
        )
        async with factory() as session:
            session.add(_make_pipeline("pipeline-race", error_code="TASK_FAILED"))
            await session.commit()

        both_checked = asyncio.Event()
        check_lock = asyncio.Lock()
        checked = 0
        for service in (service_one, service_two):
            original = service._existing

            async def synchronized_existing(key, original=original):
                nonlocal checked
                result = await original(key)
                async with check_lock:
                    checked += 1
                    if checked == 2:
                        both_checked.set()
                await both_checked.wait()
                return result

            service._existing = synchronized_existing

        try:
            first, second = await asyncio.wait_for(
                asyncio.gather(
                    service_one.pipeline_failed("pipeline-race"),
                    service_two.pipeline_failed("pipeline-race"),
                ),
                timeout=5,
            )
            assert first.session_id == second.session_id
            assert sum(len(runtime.calls) for runtime in (runtime_one, runtime_two)) == 1
            assert first.created != second.created
        finally:
            await engine.dispose()

    asyncio.run(scenario())


def test_idle_core_crash_uses_current_boot_same_generation_pipeline_incident(tmp_path):
    async def scenario():
        service, factory, runtime, engine = await _incident_harness(tmp_path)
        try:
            async with factory() as session:
                session.add(_make_pipeline("pipeline-crashed", core_epoch=23))
                session.add(
                    Task(
                        id="task-crashed",
                        pipeline_id="pipeline-crashed",
                        order_index=0,
                        type_name="Fight",
                        task_name="Fight",
                        params={},
                        status="failed",
                        error_code="CORE_CRASHED",
                    )
                )
                await session.commit()
            service.pipeline_runner.crashed_pipeline_ids[23] = {"pipeline-crashed"}

            result = await service.core_crashed(23, {"reason": "process_exit"})
            assert result.session_id is not None
            assert len(runtime.calls) == 1
            async with factory() as session:
                stored = await AgentSessionRepository(session).get(result.session_id)
            assert stored.incident_key == "pipeline:pipeline-crashed"
        finally:
            await engine.dispose()

    asyncio.run(scenario())


def test_core_incident_key_separates_service_boots_but_replays_within_one_boot(tmp_path):
    async def scenario():
        _unused_service, factory, _unused_runtime, engine = await _incident_harness(tmp_path)
        runtime_one = FakeRuntime(factory)
        runtime_two = FakeRuntime(factory)
        service_one = AgentIncidentService(
            factory,
            runtime_one,
            settings_provider=lambda: Settings(),
            pipeline_runner=ActiveRunner(),
        )
        service_two = AgentIncidentService(
            factory,
            runtime_two,
            settings_provider=lambda: Settings(),
            pipeline_runner=ActiveRunner(),
        )
        try:
            assert UUID(service_one.boot_id) != UUID(service_two.boot_id)
            first = await service_one.core_crashed(17, {"reason": "process_exit"})
            replay = await service_one.core_crashed(17, {"reason": "process_exit"})
            after_restart = await service_two.core_crashed(17, {"reason": "process_exit"})

            assert first.session_id == replay.session_id
            assert after_restart.session_id != first.session_id
            assert first.incident_key == (
                f"core:default:{service_one.boot_id}:17"
            )
            assert after_restart.incident_key == (
                f"core:default:{service_two.boot_id}:17"
            )
            assert len(runtime_one.calls) == 1
            assert len(runtime_two.calls) == 1
        finally:
            await engine.dispose()

    asyncio.run(scenario())


def test_new_boot_does_not_coalesce_old_core_epoch_pipeline(tmp_path):
    async def scenario():
        service, factory, runtime, engine = await _incident_harness(tmp_path)
        try:
            async with factory() as session:
                session.add(_make_pipeline("pipeline-old-boot", core_epoch=17))
                session.add(
                    Task(
                        id="task-old-boot",
                        pipeline_id="pipeline-old-boot",
                        order_index=0,
                        type_name="Fight",
                        task_name="Fight",
                        params={},
                        status="failed",
                        error_code="CORE_CRASHED",
                    )
                )
                await session.commit()

            result = await service.core_crashed(17, {"reason": "process_exit"})

            assert result.session_id is not None
            assert result.incident_key == f"core:default:{service.boot_id}:17"
            assert len(runtime.calls) == 1
            async with factory() as session:
                stored = await AgentSessionRepository(session).get(result.session_id)
            assert stored.incident_key != "pipeline:pipeline-old-boot"
        finally:
            await engine.dispose()

    asyncio.run(scenario())


def test_disabled_auto_diagnosis_does_not_create_session_or_call_runtime(tmp_path):
    async def scenario():
        service, factory, runtime, engine = await _incident_harness(tmp_path, enabled=False)
        try:
            result = await service.core_crashed(3, {"reason": "process_exit"})
            assert result.session_id is None
            assert runtime.calls == []
            async with factory() as session:
                assert await AgentSessionRepository(session).list_recent() == []
        finally:
            await engine.dispose()

    asyncio.run(scenario())


def test_incident_configuration_failure_is_saved_as_assistant_explanation(tmp_path):
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
        service = AgentIncidentService(
            factory,
            runtime,
            settings_provider=lambda: settings_state["value"],
        )
        try:
            async with factory() as session:
                session.add(_make_pipeline("pipeline-config", error_code="TASK_FAILED"))
                await session.commit()
            result = await service.pipeline_failed("pipeline-config")
            async with factory() as session:
                messages = await AgentMessageRepository(session).list_by_session(result.session_id)
            assert [message.role for message in messages] == ["user", "assistant"]
            assert "LLM" in messages[1].content
            assert "api_key" in messages[1].content
            async with factory() as session:
                stored = await AgentSessionRepository(session).get(result.session_id)
            assert stored.model == "unconfigured"
            assert stored.base_url is None

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
            assert await runtime.acquire_session(result.session_id)
            chunks = [
                chunk
                async for chunk in runtime.stream_message(
                    result.session_id, "继续诊断", None
                )
            ]
            assert llm.models == ["test-model"]
            assert "event: message_end" in "".join(chunks)
            async with factory() as session:
                stored = await AgentSessionRepository(session).get(result.session_id)
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
            await asyncio.wait_for(run, timeout=2)
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
            [_read_knowledge_response("account-resources"), _text_response("已按剧本检查。")]
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
            assert "- account-resources｜查询账号资源" in system
            assert system.index("你是 MAA-API 内置 Agent") < system.index("知识库索引（")
            assert system.index("知识库索引（") < system.index("当前服务状态摘要")

            async with factory() as session:
                messages = await AgentMessageRepository(session).list_by_session("diagnosed")
            tool_messages = [message for message in messages if message.role == "tool"]
            assert len(tool_messages) == 1
            payload = json.loads(tool_messages[0].content)["result"]
            assert payload["id"] == "account-resources"
            assert payload["kind"] == "playbook"
            assert payload["source"] == "builtin"
            assert payload["body"].startswith("## 前置判断")
            assert payload["updated_at"] is None
        finally:
            await engine.dispose()

    asyncio.run(scenario())
