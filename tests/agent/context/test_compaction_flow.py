"""M74 T6：压缩在运行里的行为（spec §4.5.2、§6 的前五条用例）。

自动压缩用真临时库 + 假摘要模型，钉住「运行内发一次 SSE compaction、之后的请求
从摘要消息开始、WS 广播 compaction_created」；降级用例钉住「摘要失败写机械摘要
检查点，运行继续」；第三条钉住 token 预算替换字符预算；第四条钉住 legacy 的布局
逐字节不变；第五条用真实总控服务跑 200 条用户消息与 20 条汇报的长会话，钉住
16 000 窗口下每次请求都在溢出线下、摘要保留全部工单 ID、工具声明不丢。
"""

from __future__ import annotations

import asyncio
import json
from types import SimpleNamespace

from maa_api.agent.confirmation import ConfirmationService
from maa_api.agent.context.compaction import CompactionMiddleware, Compactor
from maa_api.agent.context.layout import ContextLayout, parts_of
from maa_api.agent.context.tokens import TokenEstimator, budget_for
from maa_api.agent.harness.builtin_middleware import (
    ContextLayoutMiddleware,
    build_model_context,
)
from maa_api.agent.harness.context import (
    ModelRequest,
    ModelResponse,
    RunContext,
    Usage,
)
from maa_api.agent.harness.loop import AgentLoop, MemoryTranscript
from maa_api.agent.harness.middleware import MiddlewareChain
from maa_api.agent.llm.profiles import ResolvedProfile
from maa_api.agent.llm.schemas.compaction_summary import CompactionSummary, Ref
from maa_api.agent.orchestrator.service import OrchestratorService
from maa_api.agent.registry import ToolRegistry
from maa_api.agent.runtime import AgentRuntime
from maa_api.agent.runs.service import RunService
from maa_api.agent.specs.registry import AgentSpecRegistry, builtin_spec
from maa_api.agent.tickets.inbox import InboxService
from maa_api.agent.tools import build_registry
from maa_api.agent.workers.pump import TurnHandle
from maa_api.db.repositories.agent import AgentMessageRepository
from maa_api.db.repositories.compaction import CompactionCheckpointRepository
from maa_api.domain.enums import InboxKind
from maa_api.domain.errors import AppError, ErrorCode
from tests.fakes.fake_llm import FakeLLM, text_response
from tests.fakes.v3_db import make_session_factory, seed_session

_WORKER_PROFILE = ResolvedProfile(
    name="worker",
    kind="chat",
    provider="openai_compatible",
    base_url="https://llm.test/v1",
    api_key="sk-test",
    model="worker-model",
    context_window=8_000,
    max_output_tokens=None,
    supports_vision=None,
    supports_tools=True,
    supports_json_schema=None,
    dynamic_context_position="tail_system",
    price_input=None,
    price_cached_input=None,
    price_output=None,
    currency="CNY",
    embedding_dim=None,
    local_model_path=None,
    readonly=False,
)
_ORCHESTRATOR_PROFILE = ResolvedProfile(
    name="orchestrator",
    kind="chat",
    provider="openai_compatible",
    base_url="https://llm.test/v1",
    api_key="sk-test",
    model="orchestrator-model",
    context_window=16_000,
    max_output_tokens=None,
    supports_vision=True,
    supports_tools=True,
    supports_json_schema=True,
    dynamic_context_position="tail_system",
    price_input=None,
    price_cached_input=None,
    price_output=None,
    currency="CNY",
    embedding_dim=None,
    local_model_path=None,
    readonly=False,
)
_SUMMARIZER_PROFILE = ResolvedProfile(
    name="summarizer",
    kind="chat",
    provider="openai_compatible",
    base_url="https://llm.test/v1",
    api_key="sk-test",
    model="summarizer-model",
    context_window=100_000,
    max_output_tokens=None,
    supports_vision=None,
    supports_tools=False,
    supports_json_schema=None,
    dynamic_context_position="tail_system",
    price_input=None,
    price_cached_input=None,
    price_output=None,
    currency="CNY",
    embedding_dim=None,
    local_model_path=None,
    readonly=False,
)


def _settings():
    return SimpleNamespace(
        context=SimpleNamespace(
            fallback_context_tokens=128_000,
            compaction_ratio=0.75,
            keep_recent_turns=2,
            clear_tool_results_after=3,
        )
    )


class _Registry:
    def __init__(self, refetchable: set[str]) -> None:
        self._refetchable = set(refetchable)

    def get(self, name: str) -> SimpleNamespace:
        return SimpleNamespace(refetchable=name in self._refetchable)


class _Router:
    async def resolve(self, role: str) -> ResolvedProfile:
        assert role == "summarizer"
        return _SUMMARIZER_PROFILE

    async def resolve_for_spec(self, spec: object, **_: object) -> ResolvedProfile:
        return _WORKER_PROFILE


class _Recorder:
    """记下最终请求，并在收尾时确认运行正常结束。"""

    def __init__(self, requests: list[ModelRequest]) -> None:
        self.requests = requests

    async def __call__(self, ctx: RunContext, req: ModelRequest) -> ModelResponse:
        self.requests.append(req)
        return ModelResponse(
            text="完成",
            reasoning="",
            usage=Usage(),
            upstream_finish_reason="stop",
            latency_ms=1,
        )


def _app_state(factory, compactor, router=None, registry=None):
    return SimpleNamespace(
        session_factory=factory,
        context_layout=ContextLayout(TokenEstimator()),
        model_router=router or _Router(),
        cost_ledger=None,
        run_service=None,
        agent_spec_registry=SimpleNamespace(get=builtin_spec),
        confirmation_service=SimpleNamespace(
            registry=registry or _Registry({"get_logs"})
        ),
        compactor=compactor,
    )


async def _seed_worker_messages(factory, session_id: str) -> None:
    """一条简报加 10 个 8 000 字的 get_logs 往返：清理后仍越过 8 000 窗口的阈值。"""
    rows = [{"seq": 0, "role": "user", "content": "简报：刷 1-7 到 50 个固源岩"}]
    seq = 1
    for index in range(10):
        call_id = f"call-{index}"
        rows.append(
            {
                "seq": seq,
                "role": "assistant",
                "content": None,
                "tool_calls": [
                    {
                        "id": call_id,
                        "type": "function",
                        "function": {"name": "get_logs", "arguments": '{"limit": 20}'},
                    }
                ],
            }
        )
        seq += 1
        rows.append(
            {
                "seq": seq,
                "role": "tool",
                "content": "x" * 8_000,
                "tool_call_id": call_id,
                "tool_name": "get_logs",
            }
        )
        seq += 1
    async with factory() as session:
        from maa_api.db.models import AgentMessage

        for row in rows:
            session.add(AgentMessage(session_id=session_id, **row))
        await session.commit()


def test_auto_compaction_in_a_worker_run(tmp_path, monkeypatch) -> None:
    engine, factory = asyncio.run(make_session_factory(tmp_path / "auto.db"))

    async def scenario() -> None:
        session_id = await seed_session(factory, kind="worker")
        await _seed_worker_messages(factory, session_id)
        settings = _settings()

        calls: list[dict] = []

        async def fake_complete_structured(**kwargs):
            calls.append(kwargs)
            return SimpleNamespace(
                value=CompactionSummary(summary="较早的消息已经压缩成这一段。")
            )

        monkeypatch.setattr(
            "maa_api.agent.context.compaction.get_settings", lambda: settings
        )
        monkeypatch.setattr(
            "maa_api.agent.context.compaction.complete_structured",
            fake_complete_structured,
        )

        broadcasts: list[tuple[str, object]] = []
        state = _app_state(factory, None)
        compactor = Compactor(
            state,
            factory,
            broadcast=lambda message_type, data: broadcasts.append(
                (message_type, data)
            ),
        )
        state.compactor = compactor
        app = SimpleNamespace(state=state)
        events: list[tuple[str, object]] = []

        async def emit(name: str, data: object) -> None:
            events.append((name, data))

        ctx = RunContext(
            run_id="run-auto",
            root_run_id="run-auto",
            parent_run_id=None,
            session_id=session_id,
            ticket_id=None,
            spec=builtin_spec("general"),
            app=app,
            emit=emit,
        )
        requests: list[ModelRequest] = []
        loop = AgentLoop(
            chain=MiddlewareChain(
                [
                    ContextLayoutMiddleware(),
                    CompactionMiddleware(settings_provider=lambda: settings),
                ]
            ),
            transcript=MemoryTranscript([]),
            call_model=_Recorder(requests),
            tool_schemas=lambda _ctx: [],
            registry=ToolRegistry(),
        )
        outcome = await loop.run(ctx)
        assert outcome.status == "succeeded"
        assert len(calls) == 1

        # 运行内发一次 SSE compaction。
        compaction_events = [data for name, data in events if name == "compaction"]
        assert len(compaction_events) == 1
        event = compaction_events[0]
        assert event["checkpoint_id"] is not None
        assert event["tokens_before"] > event["tokens_after"] > 0

        # WS 广播 compaction_created。
        assert [item[0] for item in broadcasts] == ["agent_event"]
        payload = broadcasts[0][1]
        assert payload["session_id"] == session_id
        assert payload["event"] == "compaction_created"
        assert payload["payload"]["trigger"] == "auto"
        assert payload["payload"]["checkpoint_id"] == event["checkpoint_id"]

        async with factory() as session:
            checkpoint = await CompactionCheckpointRepository(
                session
            ).active_for_session(session_id)
            assert checkpoint is not None
            assert checkpoint.id == event["checkpoint_id"]
            assert checkpoint.summary_json["degraded"] is False
            assert checkpoint.model == "summarizer-model"
            assert checkpoint.summary.startswith("【压缩摘要】")
            remaining = await AgentMessageRepository(session).list_after_seq(
                session_id, checkpoint.upto_seq
            )
            assert remaining
            assert all(message.seq > checkpoint.upto_seq for message in remaining)

        # 之后的请求从摘要消息开始、只带 seq > upto_seq 的消息。
        request = requests[0]
        history = parts_of(request).history
        assert history[0]["role"] == "user"
        assert history[0]["content"].startswith("【压缩摘要】")
        # 剩余消息按 prepare 后的形状接在摘要后面（tail 是最后一条 system）。
        from maa_api.agent.harness.builtin_middleware import (
            prepare_history_messages,
        )

        prepared = prepare_history_messages(remaining, vision_enabled=True)
        assert history[1:] == prepared

    try:
        asyncio.run(scenario())
    finally:
        engine.sync_engine.dispose()


def test_summarizer_failure_writes_a_degraded_checkpoint(
    tmp_path, monkeypatch
) -> None:
    engine, factory = asyncio.run(make_session_factory(tmp_path / "degraded.db"))

    async def scenario() -> None:
        session_id = await seed_session(factory, kind="worker")
        await _seed_worker_messages(factory, session_id)
        settings = _settings()

        async def failing_complete_structured(**kwargs):
            del kwargs
            raise AppError(ErrorCode.STRUCTURED_OUTPUT_INVALID, "模型两次都不合法")

        monkeypatch.setattr(
            "maa_api.agent.context.compaction.get_settings", lambda: settings
        )
        monkeypatch.setattr(
            "maa_api.agent.context.compaction.complete_structured",
            failing_complete_structured,
        )

        events: list[tuple[str, object]] = []

        async def emit(name: str, data: object) -> None:
            events.append((name, data))

        state = _app_state(factory, None)
        compactor = Compactor(state, factory)
        state.compactor = compactor
        app = SimpleNamespace(state=state)
        ctx = RunContext(
            run_id="run-degraded",
            root_run_id="run-degraded",
            parent_run_id=None,
            session_id=session_id,
            ticket_id=None,
            spec=builtin_spec("general"),
            app=app,
            emit=emit,
        )
        loop = AgentLoop(
            chain=MiddlewareChain(
                [
                    ContextLayoutMiddleware(),
                    CompactionMiddleware(settings_provider=lambda: settings),
                ]
            ),
            transcript=MemoryTranscript([]),
            call_model=_Recorder([]),
            tool_schemas=lambda _ctx: [],
            registry=ToolRegistry(),
        )
        outcome = await loop.run(ctx)
        # 摘要模型不可用不影响这一轮继续跑。
        assert outcome.status == "succeeded"
        assert [name for name, _ in events if name == "compaction"] == ["compaction"]

        async with factory() as session:
            checkpoint = await CompactionCheckpointRepository(
                session
            ).active_for_session(session_id)
        assert checkpoint is not None
        assert checkpoint.model is None
        assert checkpoint.summary_json["degraded"] is True
        assert checkpoint.summary.startswith("【压缩摘要】")
        assert "较早的消息已按规则压缩，细节可以按 ID 重新查询" in checkpoint.summary
        assert "简报：刷 1-7 到 50 个固源岩" in checkpoint.summary

    try:
        asyncio.run(scenario())
    finally:
        engine.sync_engine.dispose()


def test_token_budget_replaces_the_char_budget() -> None:
    profile = ResolvedProfile(
        name="huge-window",
        kind="chat",
        provider="openai_compatible",
        base_url="https://llm.test/v1",
        api_key="sk-test",
        model="huge-model",
        context_window=1_000_000,
        max_output_tokens=None,
        supports_vision=None,
        supports_tools=True,
        supports_json_schema=None,
        dynamic_context_position="tail_system",
        price_input=None,
        price_cached_input=None,
        price_output=None,
        currency="CNY",
        embedding_dim=None,
        local_model_path=None,
        readonly=False,
    )
    history = [
        {"role": "user", "content": f"{index}" + "a" * 90_000}
        for index in range(5)
    ]
    assert sum(len(message["content"]) for message in history) > 400_000
    # M67 的字符预算会把这段历史裁短。
    old = build_model_context(history, max_context_chars=400_000, vision_enabled=False)
    assert len(old) - 1 < len(history)

    layout = ContextLayout(TokenEstimator())
    app = SimpleNamespace(
        state=SimpleNamespace(context_layout=layout)
    )
    ctx = RunContext(
        run_id="run-budget",
        root_run_id="run-budget",
        parent_run_id=None,
        session_id="session-budget",
        ticket_id=None,
        spec=builtin_spec("general"),
        app=app,
        vision_enabled=False,
    )
    ctx.state["model"] = {"profile": profile}
    ctx.state[ContextLayoutMiddleware.name] = {
        "stable": "",
        "dynamic": "",
        "status": {},
    }
    request = ModelRequest(messages=list(history), tools=[], profile=profile)
    updated = asyncio.run(ContextLayoutMiddleware().before_model(ctx, request))

    budget = budget_for(profile, _settings())
    assert parts_of(updated).history == history
    assert updated.estimated_tokens < budget.trigger_tokens
    assert updated.estimated_tokens < budget.overflow_tokens


def test_legacy_specs_keep_the_v2_layout() -> None:
    history = [
        {"role": "user", "content": "旧问题"},
        {
            "role": "assistant",
            "content": "旧回答",
            "reasoning_content": "思考",
            "tool_calls": [
                {
                    "id": "call-old",
                    "type": "function",
                    "function": {"name": "lookup", "arguments": "{}"},
                }
            ],
        },
        {
            "role": "tool",
            "tool_call_id": "call-old",
            "tool_name": "lookup",
            "content": '{"result": {"ok": true}}',
        },
    ]
    ctx = RunContext(
        run_id="run-legacy",
        root_run_id="run-legacy",
        parent_run_id=None,
        session_id="session-legacy",
        ticket_id=None,
        spec=builtin_spec("legacy_chat"),
    )
    ctx.state[ContextLayoutMiddleware.name] = {
        "knowledge_context": "知识库段",
        "status_summary": {"now": "2026-11-04T12:00:00+08:00"},
        "max_context_chars": 60_000,
    }
    tools = [
        {
            "type": "function",
            "function": {
                "name": "lookup",
                "description": "查一下",
                "parameters": {"type": "object", "properties": {}},
            },
        }
    ]
    request = ModelRequest(messages=list(history), tools=list(tools))
    updated = asyncio.run(ContextLayoutMiddleware().before_model(ctx, request))

    expected = build_model_context(
        history,
        system_prompt=ctx.spec.body,
        knowledge_context="知识库段",
        status_summary={"now": "2026-11-04T12:00:00+08:00"},
        max_context_chars=60_000,
        envelope_chars=len(json.dumps(tools, ensure_ascii=False)),
        vision_enabled=ctx.vision_enabled,
    )
    assert updated.messages == expected
    assert "【压缩摘要】" not in json.dumps(updated.messages, ensure_ascii=False)


def test_orchestrator_long_session_stays_within_budget(tmp_path, monkeypatch) -> None:
    """总控窗口 16 000 的长会话：200 条消息与 20 条汇报压缩后仍在预算内。

    用真实总控服务与真实中间件链跑若干轮；直接调 ``_run_turn`` 而不经过泵的批
    处理窗口，用例不等真实时间。收件箱一次最多领 50 条：第一条领走 20 条汇报与
    30 条用户消息，之后每股 50 条，最后 20 条收尾。
    """
    engine, factory = asyncio.run(
        make_session_factory(tmp_path / "long-session.db")
    )

    async def scenario() -> None:
        tool_registry = build_registry()
        spec_registry = AgentSpecRegistry(tool_registry)
        spec_registry.load()
        confirmations = ConfirmationService(factory, tool_registry)
        runtime = AgentRuntime(
            factory,
            confirmations,
            settings_provider=lambda _request: SimpleNamespace(channel=None),
        )
        run_service = RunService(factory, boot_id="m74-t9a")
        inbox = InboxService(factory)
        layout = ContextLayout(TokenEstimator())
        llm = FakeLLM([text_response("收到，继续。") for _ in range(10)])
        orchestrator_profile = _ORCHESTRATOR_PROFILE

        class _LongSessionRouter:
            async def resolve(self, role: object) -> ResolvedProfile:
                if str(role) == "summarizer":
                    return _SUMMARIZER_PROFILE
                return orchestrator_profile

            async def resolve_for_spec(
                self, spec: object, **_: object
            ) -> ResolvedProfile:
                return orchestrator_profile

            def client_for(self, _profile: ResolvedProfile) -> object:
                class _Handle:
                    def __init__(self, client: FakeLLM) -> None:
                        self.client = client

                    async def aclose(self) -> None:
                        return None

                return _Handle(llm)

        router = _LongSessionRouter()
        state = SimpleNamespace(
            agent_runtime=runtime,
            model_router=router,
            run_event_bus=None,
            run_service=run_service,
            session_factory=factory,
            context_layout=layout,
            agent_spec_registry=spec_registry,
            confirmation_service=confirmations,
            knowledge_service=None,
            ticket_service=SimpleNamespace(),
        )
        app = SimpleNamespace(state=state)
        compactor = Compactor(state, factory)
        state.compactor = compactor
        orchestrator = OrchestratorService(
            app=app,
            session_factory=factory,
            spec_registry=spec_registry,
            run_service=run_service,
            inbox_service=inbox,
            ticket_service=SimpleNamespace(),
            question_service=None,
            confirmation_service=confirmations,
            model_router=router,
            event_bus=None,
        )
        try:
            session = await orchestrator.ensure_session()
            session_id = str(session.id)

            ticket_ids = [f"ticket-{index:02d}" for index in range(20)]
            for index, ticket_id in enumerate(ticket_ids):
                await inbox.put(
                    session_id,
                    InboxKind.REPORT,
                    payload={
                        "ticket_id": ticket_id,
                        "ticket_title": f"工单 {index}",
                        "ticket_kind": "general",
                        "status": "succeeded",
                        "summary": (
                            f"第 {index} 个工单完成，结果可以按 ID 查询。"
                            + "细节" * 20
                        ),
                    },
                )

            async def post_user_messages(start: int, count: int) -> None:
                for index in range(start, start + count):
                    await orchestrator.post_user_message(
                        f"第 {index} 条用户消息："
                        + "请记录这条长会话信息。" * 10,
                        [],
                    )

            settings = _settings()
            monkeypatch.setattr(
                "maa_api.agent.context.compaction.get_settings",
                lambda: settings,
            )
            monkeypatch.setattr(
                "maa_api.agent.workers.pump.get_settings",
                lambda: settings,
            )

            summary_calls: list[dict[str, object]] = []

            async def fake_complete_structured(**kwargs: object) -> object:
                summary_calls.append(kwargs)
                # 只引用摘要输入里真的出现过的工单 ID：20 条汇报必须都在
                # 被压缩的区间里，最后一条检查点才可能列全。
                prompt = "\n".join(
                    str(message.get("content") or "")
                    for message in kwargs["messages"]
                )
                found = [ticket_id for ticket_id in ticket_ids if ticket_id in prompt]
                return SimpleNamespace(
                    value=CompactionSummary(
                        summary="较早的消息已压缩，工单结果可按 ID 重新查询。",
                        refs=[
                            Ref(
                                kind="ticket",
                                id=ticket_id,
                                label="create_ticket",
                            )
                            for ticket_id in found
                        ],
                    )
                )

            monkeypatch.setattr(
                "maa_api.agent.context.compaction.complete_structured",
                fake_complete_structured,
            )

            observed: list[tuple[int, set[str], bool]] = []
            original_init = AgentLoop.__init__

            def capture_init(self: AgentLoop, *, call_model, **kwargs):
                """在中间件处理完之后、真正调模型之前记下最终请求。"""

                def wrapped(ctx: RunContext, request: ModelRequest):
                    tool_names = {
                        str(tool.get("function", {}).get("name"))
                        for tool in request.tools
                        if isinstance(tool, dict)
                    }
                    has_summary = any(
                        isinstance(message.get("content"), str)
                        and message["content"].startswith("【压缩摘要】")
                        for message in request.messages
                    )
                    observed.append(
                        (request.estimated_tokens, tool_names, has_summary)
                    )
                    return call_model(ctx, request)

                original_init(self, call_model=wrapped, **kwargs)

            monkeypatch.setattr(AgentLoop, "__init__", capture_init)

            async def run_turn() -> None:
                handle = TurnHandle()
                assert await orchestrator._run_turn(handle)
                assert handle.run_id is not None

            await post_user_messages(0, 30)
            await run_turn()
            for start in (30, 80, 130):
                await post_user_messages(start, 50)
                await run_turn()
            await post_user_messages(180, 20)
            await run_turn()

            assert await inbox.pending_count(session_id) == 0
            budget = budget_for(orchestrator_profile, settings)
            assert budget.window == 16_000
            assert observed
            assert all(
                estimated < budget.overflow_tokens
                for estimated, _tools, _summary in observed
            )
            assert all(
                "get_ticket" in tools for _estimated, tools, _summary in observed
            )
            assert any(has_summary for _estimated, _tools, has_summary in observed)

            async with factory() as db:
                repo = CompactionCheckpointRepository(db)
                checkpoints = await repo.list_for_session(session_id)
                active = await repo.active_for_session(session_id)
            assert len(checkpoints) >= 1
            assert active is not None
            assert [tid for tid in ticket_ids if tid not in active.summary] == []
            assert summary_calls
            assert all(
                call["schema_name"] == "compaction_summary"
                for call in summary_calls
            )
        finally:
            await orchestrator.close()
            await run_service.close()

    try:
        asyncio.run(scenario())
    finally:
        engine.sync_engine.dispose()
