"""TraceMiddleware 的 span 树、成本一致与「不记正文」（M68 规格 §4.2.2、§6）。

钩子用例不起 lifespan：临时库直接构造 ``SpanRecorder`` 与最小的 ``RunContext``，
逐个调钩子后 ``flush()`` 读库断言。最后一条用 ``FakeLLM`` 驱动一次真实的
``legacy_chat`` 会话，确认 v2 门面经 ``AgentLoop`` 确实产出同一棵树。
"""

from __future__ import annotations

import asyncio
import json
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from datetime import date, datetime, timedelta
from pathlib import Path
from types import SimpleNamespace
from typing import Any

from pydantic import BaseModel, ConfigDict
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

import maa_api.agent.llm.cost as cost_module
import maa_api.settings as settings_module
from maa_api.agent.harness import builtin_middleware
from maa_api.agent.harness.builtin_middleware import TraceMiddleware
from maa_api.agent.harness.context import (
    ModelRequest,
    ModelResponse,
    RunContext,
    RunOutcome,
    ToolCall,
    ToolOutcome,
    Usage,
)
from maa_api.agent.llm.cost import (
    CostLedger,
    compute_cost_micros,
    compute_cost_micros_at,
)
from maa_api.agent.llm.profiles import ResolvedProfile
from maa_api.agent.registry import ToolDefinition, ToolRegistry, ToolRisk
from maa_api.agent.runtime import AgentRuntime
from maa_api.agent.specs.registry import builtin_spec
from maa_api.agent.trace import (
    SpanHandle,
    SpanRecorder,
    current_span,
    reset_span,
    use_span,
)
from maa_api.db.models import (
    AgentAudit,
    AgentRun,
    AgentSession,
    Confirmation,
    CostLedgerEntry,
    TraceSpan,
    new_uuid,
    utcnow,
)
from maa_api.db.session import make_engine
from maa_api.domain.enums import AgentRunKind, SpanKind
from maa_api.settings import LLMSettings, Settings, TraceSettings
from tests.fakes.fake_llm import FakeLLM, tool_call, tool_response
from tests.fakes.legacy_turn import stream_legacy_turn


# --------------------------------------------------------------------------- #
# 夹具
# --------------------------------------------------------------------------- #
def _settings(*, enabled: bool = True) -> Settings:
    return Settings(
        trace=TraceSettings(enabled=enabled),
        llm=LLMSettings(
            base_url="https://llm.test/v1",
            api_key="test-key",
            model="test-model",
        ),
    )


def _profile(
    *,
    name: str = "worker-a",
    model: str = "worker-model",
    max_output_tokens: int | None = 2048,
    price_input_offpeak: float | None = None,
    price_cached_input_offpeak: float | None = None,
    price_output_offpeak: float | None = None,
) -> ResolvedProfile:
    return ResolvedProfile(
        name=name,
        kind="chat",
        provider="openai_compatible",
        base_url="https://llm.test/v1",
        api_key="sk-test",
        model=model,
        context_window=None,
        max_output_tokens=max_output_tokens,
        supports_vision=None,
        supports_tools=True,
        supports_json_schema=None,
        dynamic_context_position="tail_system",
        price_input=2.0,
        price_cached_input=1.0,
        price_output=4.0,
        price_input_offpeak=price_input_offpeak,
        price_cached_input_offpeak=price_cached_input_offpeak,
        price_output_offpeak=price_output_offpeak,
        currency="CNY",
        embedding_dim=None,
        local_model_path=None,
        readonly=False,
    )


@asynccontextmanager
async def _harness(
    tmp_path: Path, *, enabled: bool = True
) -> AsyncIterator[tuple[SpanRecorder, async_sessionmaker[AsyncSession], Any]]:
    """临时库 + recorder + 只带追踪所需属性的 ``app.state``。"""
    from sqlmodel import SQLModel

    engine = make_engine(f"sqlite+aiosqlite:///{tmp_path / 'trace.db'}")
    async with engine.begin() as connection:
        await connection.run_sync(SQLModel.metadata.create_all)
    factory = async_sessionmaker(engine, expire_on_commit=False, class_=AsyncSession)
    recorder = SpanRecorder(
        factory, settings_provider=lambda: _settings(enabled=enabled)
    )
    app = SimpleNamespace(
        state=SimpleNamespace(span_recorder=recorder, session_factory=factory)
    )
    try:
        yield recorder, factory, app
    finally:
        await recorder.close()
        await engine.dispose()


def _ctx(
    app: Any,
    *,
    run_id: str | None = None,
    root_run_id: str | None = None,
    session_id: str | None = None,
    ticket_id: str | None = None,
    spec_name: str = "legacy_chat",
) -> RunContext:
    own_run = run_id or new_uuid()
    return RunContext(
        run_id=own_run,
        root_run_id=root_run_id or own_run,
        parent_run_id=None,
        session_id=session_id,
        ticket_id=ticket_id,
        spec=builtin_spec(spec_name),
        app=app,
    )


def _request(
    *, response_format: dict[str, Any] | None = None
) -> ModelRequest:
    return ModelRequest(
        messages=[{"role": "user", "content": "hi"}],
        tools=[],
        response_format=response_format,
    )


def _response(
    *,
    text: str = "",
    reasoning: str = "",
    usage: Usage | None = None,
    finish_reason: str | None = "stop",
    latency_ms: int = 10,
    model: str | None = None,
    response_id: str | None = None,
) -> ModelResponse:
    return ModelResponse(
        text=text,
        reasoning=reasoning,
        usage=usage or Usage(),
        upstream_finish_reason=finish_reason,
        latency_ms=latency_ms,
        model=model,
        response_id=response_id,
    )


async def _spans(factory: async_sessionmaker[AsyncSession]) -> list[TraceSpan]:
    async with factory() as session:
        result = await session.execute(
            select(TraceSpan).order_by(TraceSpan.started_at, TraceSpan.id)
        )
        return list(result.scalars().all())


async def _create_run(
    factory: async_sessionmaker[AsyncSession],
    run_id: str,
    *,
    root_run_id: str | None = None,
) -> None:
    """``trace_span.run_id`` 有外键：先建运行行，否则写库会因外键失败被丢弃。"""
    async with factory() as session:
        session.add(
            AgentRun(
                id=run_id,
                root_run_id=root_run_id or run_id,
                agent_name="legacy_chat",
                kind=AgentRunKind.LEGACY_TURN,
                boot_id="boot-test",
            )
        )
        await session.commit()


def _by_name(spans: list[TraceSpan], name: str) -> list[TraceSpan]:
    return [span for span in spans if span.name == name]


def _attributes_text(span: TraceSpan) -> str:
    return json.dumps(span.attributes, ensure_ascii=False, default=str, sort_keys=True)


class _LookupParams(BaseModel):
    model_config = ConfigDict(extra="forbid")

    key: str


class _Router:
    """固定档案的替身（ContextLayoutMiddleware 用）。"""

    def __init__(self, profile: ResolvedProfile) -> None:
        self.profile = profile

    async def resolve_for_spec(
        self,
        _spec: Any,
        *,
        session_model: str | None = None,
        session_base_url: str | None = None,
    ) -> ResolvedProfile:
        return self.profile


# --------------------------------------------------------------------------- #
# 1. 树形
# --------------------------------------------------------------------------- #
def test_run_llm_and_tool_spans_form_one_tree(tmp_path: Path) -> None:
    async def scenario() -> None:
        async with _harness(tmp_path) as (recorder, factory, app):
            ctx = _ctx(app)
            await _create_run(factory, ctx.run_id)
            ctx.state["model"] = {"profile": _profile()}
            middleware = TraceMiddleware()
            await middleware.on_run_start(ctx)
            assert isinstance(ctx.state["trace.run_span"], SpanHandle)

            for _ in range(2):
                await middleware.before_model(ctx, _request())
                await middleware.after_model(ctx, _response())

            call = ToolCall(
                id="call-1",
                name="get_system_status",
                raw_arguments="{}",
                arguments={},
            )

            async def call_next(_call: ToolCall) -> ToolOutcome:
                return ToolOutcome(status="ok", result={"ok": True})

            await middleware.wrap_tool(ctx, call, call_next)
            await middleware.on_run_end(
                ctx,
                RunOutcome(
                    status="succeeded",
                    finish_reason="stop",
                    usage=Usage(prompt_tokens=20, completion_tokens=4),
                ),
            )
            await recorder.flush()

            spans = await _spans(factory)
            assert [span.name for span in spans].count("agent.run") == 1
            assert [span.name for span in spans].count("llm.chat") == 2
            assert [span.name for span in spans].count("tool.execute") == 1
            root = _by_name(spans, "agent.run")[0]
            assert root.parent_span_id is None
            assert root.trace_id == ctx.root_run_id
            assert root.run_id == ctx.run_id
            span_ids = {span.span_id for span in spans}
            for span in spans:
                if span is root:
                    continue
                assert span.parent_span_id == root.span_id
                assert span.span_id in span_ids
                assert span.trace_id == root.trace_id
            assert root.attributes["maa.run.status"] == "succeeded"
            assert root.attributes["maa.run.finish_reason"] == "stop"
            assert root.attributes["maa.run.steps"] == 0

    asyncio.run(scenario())


# --------------------------------------------------------------------------- #
# 2. 上游耗时与用量
# --------------------------------------------------------------------------- #
def test_llm_span_uses_upstream_latency_and_usage(tmp_path: Path) -> None:
    async def scenario() -> None:
        async with _harness(tmp_path) as (recorder, factory, app):
            ctx = _ctx(app)
            await _create_run(factory, ctx.run_id)
            ctx.state["model"] = {"profile": _profile(max_output_tokens=1024)}
            middleware = TraceMiddleware()
            await middleware.on_run_start(ctx)
            await middleware.before_model(ctx, _request())
            await middleware.after_model(
                ctx,
                _response(
                    usage=Usage(
                        prompt_tokens=100,
                        completion_tokens=20,
                        cached_tokens=40,
                        reasoning_tokens=5,
                    ),
                    latency_ms=250,
                    model="upstream-model",
                    response_id="chatcmpl-1",
                ),
            )
            await middleware.on_run_end(
                ctx,
                RunOutcome(
                    status="succeeded",
                    finish_reason="stop",
                    usage=Usage(
                        prompt_tokens=100,
                        completion_tokens=20,
                        cached_tokens=40,
                        reasoning_tokens=5,
                    ),
                ),
            )
            await recorder.flush()

            llm = _by_name(await _spans(factory), "llm.chat")[0]
            assert llm.duration_ms == 250
            assert llm.ended_at - llm.started_at == timedelta(milliseconds=250)
            assert llm.attributes["gen_ai.usage.input_tokens"] == 100
            assert llm.attributes["gen_ai.usage.output_tokens"] == 20
            assert llm.attributes["maa.usage.cached_tokens"] == 40
            assert llm.attributes["maa.usage.reasoning_tokens"] == 5
            assert llm.attributes["maa.llm.latency_ms"] == 250
            assert llm.attributes["gen_ai.response.model"] == "upstream-model"
            assert llm.attributes["gen_ai.response.id"] == "chatcmpl-1"
            assert llm.attributes["gen_ai.response.finish_reasons"] == ["stop"]
            assert llm.attributes["gen_ai.request.model"] == "worker-model"
            assert llm.attributes["gen_ai.request.max_tokens"] == 1024
            assert llm.attributes["maa.model.profile"] == "worker-a"
            # (100 - 40) × 2 + 40 × 1 + 20 × 4 = 240 micros。
            assert llm.attributes["maa.cost.micros"] == 240
            assert llm.attributes["maa.cost.currency"] == "CNY"

    asyncio.run(scenario())


# --------------------------------------------------------------------------- #
# 3. 成本一致：Σ llm.chat = agent_run.cost_micros = Σ cost_ledger
# --------------------------------------------------------------------------- #
def test_llm_span_costs_add_up_to_the_run_total(tmp_path: Path) -> None:
    async def scenario() -> None:
        async with _harness(tmp_path) as (recorder, factory, app):
            ctx = _ctx(app)
            profile = _profile()
            ctx.state["model"] = {"profile": profile}
            await _create_run(factory, ctx.run_id)

            ledger = CostLedger(factory)
            app.state.cost_ledger = ledger
            middleware = TraceMiddleware()
            await middleware.on_run_start(ctx)
            usages = [
                Usage(prompt_tokens=100, completion_tokens=20, cached_tokens=40),
                Usage(prompt_tokens=50, completion_tokens=10),
            ]
            for usage in usages:
                await middleware.before_model(ctx, _request())
                await middleware.after_model(ctx, _response(usage=usage))
                await ledger.record(
                    run_id=ctx.run_id,
                    session_id=ctx.session_id,
                    agent_name=ctx.spec.name,
                    role=ctx.spec.model_role,
                    profile=profile,
                    usage=usage,
                )
            await middleware.on_run_end(
                ctx,
                RunOutcome(
                    status="succeeded",
                    finish_reason="stop",
                    usage=usages[0] + usages[1],
                ),
            )
            await recorder.flush()

            spans = await _spans(factory)
            span_costs = sum(
                int(span.attributes["maa.cost.micros"])
                for span in _by_name(spans, "llm.chat")
            )
            root = _by_name(spans, "agent.run")[0]
            async with factory() as session:
                run = (
                    await session.execute(select(AgentRun).where(AgentRun.id == ctx.run_id))
                ).scalar_one()
                ledger_total = (
                    await session.execute(
                        select(func.coalesce(func.sum(CostLedgerEntry.cost_micros), 0))
                        .where(CostLedgerEntry.run_id == ctx.run_id)
                    )
                ).scalar_one()

            assert span_costs > 0
            assert span_costs == run.cost_micros == ledger_total
            assert root.attributes["maa.cost.micros"] == run.cost_micros
            assert root.attributes["gen_ai.usage.input_tokens"] == 150
            assert root.attributes["gen_ai.usage.output_tokens"] == 30

    asyncio.run(scenario())


def test_llm_span_cost_uses_offpeak_prices_in_offpeak_window(
    tmp_path: Path, monkeypatch
) -> None:
    """``llm.chat`` span 按结束时刻取峰谷价，节假日来自当前 settings。"""
    fixed_end = datetime(2026, 11, 11, 4, 0)  # 北京时间周三 12:00，空闲时段
    monkeypatch.setattr(builtin_middleware, "utcnow", lambda: fixed_end)
    # 中间件漏传 ``at`` 时 helper 会退回自己的 utcnow；钉在高峰时刻让漏传必失败。
    monkeypatch.setattr(
        cost_module, "utcnow", lambda: datetime(2026, 11, 11, 2, 0)
    )
    # helper 的默认 settings 入口读进程缓存；注入空节假日，避免本机 config.yaml 干扰。
    monkeypatch.setattr(settings_module, "_CACHE", _settings(enabled=True))

    async def scenario() -> None:
        async with _harness(tmp_path) as (recorder, factory, app):
            ctx = _ctx(app)
            await _create_run(factory, ctx.run_id)
            profile = _profile(
                price_input_offpeak=1.0,
                price_cached_input_offpeak=0.5,
                price_output_offpeak=2.0,
            )
            ctx.state["model"] = {"profile": profile}
            middleware = TraceMiddleware()
            await middleware.on_run_start(ctx)
            await middleware.before_model(ctx, _request())
            usage = Usage(
                prompt_tokens=1000, cached_tokens=400, completion_tokens=200
            )
            await middleware.after_model(ctx, _response(usage=usage))
            await middleware.on_run_end(
                ctx,
                RunOutcome(status="succeeded", finish_reason="stop", usage=usage),
            )
            await recorder.flush()

            llm = _by_name(await _spans(factory), "llm.chat")[0]
            expected = compute_cost_micros_at(
                usage,
                profile,
                at=fixed_end,
                settings_provider=lambda: _settings(enabled=True),
            )
            peak = compute_cost_micros(usage, profile)
            # 谷价 600×1 + 400×0.5 + 200×2 = 1200；峰价同用量 600×2 + 400×1 +
            # 200×4 = 2400，两者必须不同。
            assert (expected, peak) == (1200, 2400)
            assert llm.attributes["maa.cost.micros"] == expected

    asyncio.run(scenario())


def test_llm_span_cost_uses_holiday_offpeak_prices_on_peak_weekday(
    tmp_path: Path, monkeypatch
) -> None:
    """工作日高峰时刻若在 ``llm.peak_holidays`` 里，span 仍按空闲价计费。

    ``compute_cost_micros_at`` 的 ``settings_provider`` 缺省在调用时解析，所以这里
    monkeypatch ``maa_api.agent.llm.cost.get_settings`` 就能注入节假日；若默认参数
    仍是导入时绑定的函数对象，这个用例会读到进程缓存、断言失败。
    """
    fixed_end = datetime(2026, 11, 11, 2, 0)  # 北京时间周三 10:00，工作日高峰
    monkeypatch.setattr(builtin_middleware, "utcnow", lambda: fixed_end)

    def _holiday_settings() -> Settings:
        return Settings(
            trace=TraceSettings(enabled=True),
            llm=LLMSettings(
                base_url="https://llm.test/v1",
                api_key="test-key",
                model="test-model",
                price_input=2.0,
                price_cached_input=1.0,
                price_output=4.0,
                price_input_offpeak=1.0,
                price_cached_input_offpeak=0.5,
                price_output_offpeak=2.0,
                peak_holidays="2026-11-11",
            ),
        )

    monkeypatch.setattr(cost_module, "get_settings", _holiday_settings)

    async def scenario() -> None:
        async with _harness(tmp_path) as (recorder, factory, app):
            ctx = _ctx(app)
            await _create_run(factory, ctx.run_id)
            profile = _profile(
                price_input_offpeak=1.0,
                price_cached_input_offpeak=0.5,
                price_output_offpeak=2.0,
            )
            ctx.state["model"] = {"profile": profile}
            middleware = TraceMiddleware()
            await middleware.on_run_start(ctx)
            await middleware.before_model(ctx, _request())
            usage = Usage(
                prompt_tokens=1000, cached_tokens=400, completion_tokens=200
            )
            await middleware.after_model(ctx, _response(usage=usage))
            await middleware.on_run_end(
                ctx,
                RunOutcome(status="succeeded", finish_reason="stop", usage=usage),
            )
            await recorder.flush()

            llm = _by_name(await _spans(factory), "llm.chat")[0]
            peak = compute_cost_micros(usage, profile, at=fixed_end)
            offpeak = compute_cost_micros(
                usage, profile, at=fixed_end, holidays={date(2026, 11, 11)}
            )
            # 同一用量与时刻：不放假是峰价 600×2 + 400×1 + 200×4 = 2400，
            # 列入节假日后按谷价 600×1 + 400×0.5 + 200×2 = 1200。
            assert (peak, offpeak) == (2400, 1200)
            assert llm.attributes["maa.cost.micros"] == offpeak

    asyncio.run(scenario())


# --------------------------------------------------------------------------- #
# 4. 委派 span
# --------------------------------------------------------------------------- #
def test_delegate_tool_gets_delegate_span(tmp_path: Path) -> None:
    async def scenario() -> None:
        async with _harness(tmp_path) as (recorder, factory, app):
            ctx = _ctx(app)
            await _create_run(factory, ctx.run_id)
            middleware = TraceMiddleware()
            await middleware.on_run_start(ctx)
            call = ToolCall(
                id="call-delegate",
                name="delegate",
                raw_arguments="{}",
                arguments={"agent": "oracle"},
            )

            async def call_next(_call: ToolCall) -> ToolOutcome:
                return ToolOutcome(status="ok", result={"run_id": "child-run"})

            await middleware.wrap_tool(ctx, call, call_next)
            await middleware.on_run_end(
                ctx, RunOutcome(status="succeeded", finish_reason="stop")
            )
            await recorder.flush()

            delegate = _by_name(await _spans(factory), "delegate.subagent")
            assert len(delegate) == 1
            span = delegate[0]
            assert span.kind == str(SpanKind.DELEGATE)
            assert span.attributes["gen_ai.operation.name"] == "execute_tool"
            assert span.attributes["gen_ai.tool.name"] == "delegate"
            assert span.attributes["maa.tool.status"] == "ok"
            assert span.attributes["maa.subagent.name"] == "oracle"
            assert span.attributes["maa.subagent.run_id"] == "child-run"

    asyncio.run(scenario())


# --------------------------------------------------------------------------- #
# 5. 子运行的父是委派 span
# --------------------------------------------------------------------------- #
def test_child_run_parent_is_the_tool_span(tmp_path: Path) -> None:
    async def scenario() -> None:
        async with _harness(tmp_path) as (recorder, factory, app):
            ctx = _ctx(app)
            await _create_run(factory, ctx.run_id)
            middleware = TraceMiddleware()
            await middleware.on_run_start(ctx)
            parent_span = ctx.state["trace.run_span"]
            call = ToolCall(
                id="call-delegate",
                name="delegate_recon",
                raw_arguments="{}",
                arguments={"agent": "oracle"},
            )
            child = _ctx(
                app,
                run_id=new_uuid(),
                root_run_id=ctx.root_run_id,
                session_id=None,
            )
            await _create_run(factory, child.run_id, root_run_id=ctx.root_run_id)

            async def call_next(_call: ToolCall) -> ToolOutcome:
                await middleware.on_run_start(child)
                await middleware.on_run_end(
                    child, RunOutcome(status="succeeded", finish_reason="stop")
                )
                return ToolOutcome(status="ok", result={"run_id": child.run_id})

            await middleware.wrap_tool(ctx, call, call_next)
            await middleware.on_run_end(
                ctx, RunOutcome(status="succeeded", finish_reason="stop")
            )
            await recorder.flush()

            spans = await _spans(factory)
            child_span = next(span for span in spans if span.run_id == child.run_id)
            delegate_span = next(
                span for span in spans if span.name == "delegate.subagent"
            )
            assert child_span.name == "agent.run"
            assert child_span.parent_span_id == delegate_span.span_id
            assert child_span.trace_id == ctx.root_run_id
            assert delegate_span.parent_span_id == parent_span.span_id

    asyncio.run(scenario())


# --------------------------------------------------------------------------- #
# 6. 跨 trace 的父忽略
# --------------------------------------------------------------------------- #
def test_foreign_trace_parent_is_ignored(tmp_path: Path) -> None:
    async def scenario() -> None:
        async with _harness(tmp_path) as (recorder, factory, app):
            ctx = _ctx(app)
            await _create_run(factory, ctx.run_id)
            middleware = TraceMiddleware()
            foreign = recorder.start_span(
                "agent.run",
                SpanKind.OTHER,
                trace_id=new_uuid(),
                run_id=None,
            )
            token = use_span(foreign)
            try:
                assert current_span() is foreign
                await middleware.on_run_start(ctx)
            finally:
                reset_span(token)
            own_span = ctx.state["trace.run_span"]
            assert own_span.parent_span_id is None
            await middleware.on_run_end(
                ctx, RunOutcome(status="succeeded", finish_reason="stop")
            )
            await recorder.flush()

            root = _by_name(await _spans(factory), "agent.run")[0]
            assert root.run_id == ctx.run_id
            assert root.parent_span_id is None

    asyncio.run(scenario())


# --------------------------------------------------------------------------- #
# 7. 从审计派生确认等待
# --------------------------------------------------------------------------- #
def test_confirmation_wait_span_is_derived_from_audit(tmp_path: Path) -> None:
    async def scenario() -> None:
        async with _harness(tmp_path) as (recorder, factory, app):
            created_at = utcnow() - timedelta(seconds=5)
            async with factory() as session:
                confirmation = Confirmation(
                    id=new_uuid(),
                    action="submit_pipeline",
                    risk_level="consume",
                    reason="需要确认",
                    payload={},
                    status="approved",
                    requested_by="agent",
                    created_at=created_at,
                    resolved_at=created_at + timedelta(seconds=3),
                    expires_at=created_at + timedelta(minutes=10),
                )
                session.add(confirmation)
                await session.flush()
                audit = AgentAudit(
                    caller="internal",
                    tool_name="submit_pipeline",
                    status="ok",
                    confirmation_id=confirmation.id,
                )
                session.add(audit)
                await session.commit()
                audit_id = audit.id

            ctx = _ctx(app)
            await _create_run(factory, ctx.run_id)
            middleware = TraceMiddleware()
            await middleware.on_run_start(ctx)
            call = ToolCall(
                id="call-2",
                name="submit_pipeline",
                raw_arguments="{}",
                arguments={},
            )

            async def call_next(_call: ToolCall) -> ToolOutcome:
                return ToolOutcome(status="ok", audit_id=audit_id, result={})

            await middleware.wrap_tool(ctx, call, call_next)
            # 派生是 record.spawn 的后台小任务：等它入队（工具 span 也在队列里）。
            for _ in range(200):
                if recorder.stats()["pending"] >= 2:
                    break
                await asyncio.sleep(0.01)
            else:  # pragma: no cover - 派生任务没跑起来
                raise AssertionError("confirmation span 没有被派生")
            await middleware.on_run_end(
                ctx, RunOutcome(status="succeeded", finish_reason="stop")
            )
            await recorder.flush()

            spans = await _spans(factory)
            confirmation_spans = _by_name(spans, "confirmation.wait")
            assert len(confirmation_spans) == 1
            span = confirmation_spans[0]
            tool_span = _by_name(spans, "tool.execute")[0]
            assert span.parent_span_id == tool_span.span_id
            assert span.trace_id == ctx.root_run_id
            assert span.started_at == created_at
            assert span.ended_at == created_at + timedelta(seconds=3)
            assert span.duration_ms == 3000
            assert span.attributes["maa.confirmation.status"] == "approved"
            assert span.attributes["maa.audit.id"] == audit_id
            assert span.attributes["maa.confirmation.id"] == confirmation.id

    asyncio.run(scenario())


# --------------------------------------------------------------------------- #
# 8. 模型调用抛错仍补 llm span
# --------------------------------------------------------------------------- #
def test_model_error_still_records_llm_span(tmp_path: Path) -> None:
    async def scenario() -> None:
        async with _harness(tmp_path) as (recorder, factory, app):
            ctx = _ctx(app)
            await _create_run(factory, ctx.run_id)
            ctx.state["model"] = {"profile": _profile()}
            middleware = TraceMiddleware()
            await middleware.on_run_start(ctx)
            await middleware.before_model(ctx, _request())
            # 模拟 call_model 抛错：after_model 不会被调用。
            await middleware.on_run_end(
                ctx,
                RunOutcome(status="failed", finish_reason="LLM_REQUEST_FAILED"),
            )
            await recorder.flush()

            spans = await _spans(factory)
            llm = _by_name(spans, "llm.chat")[0]
            root = _by_name(spans, "agent.run")[0]
            assert llm.status == "error"
            assert llm.attributes["error.type"] == "LLM_REQUEST_FAILED"
            assert llm.parent_span_id == root.span_id
            assert root.status == "error"
            assert root.attributes["error.type"] == "LLM_REQUEST_FAILED"
            assert root.attributes["maa.run.status"] == "failed"

    asyncio.run(scenario())


# --------------------------------------------------------------------------- #
# 9. 不记正文、推理、工具参数与结果
# --------------------------------------------------------------------------- #
def test_no_content_attributes_are_recorded(tmp_path: Path) -> None:
    async def scenario() -> None:
        async with _harness(tmp_path) as (recorder, factory, app):
            ctx = _ctx(app)
            await _create_run(factory, ctx.run_id)
            ctx.state["model"] = {"profile": _profile()}
            middleware = TraceMiddleware()
            await middleware.on_run_start(ctx)
            await middleware.before_model(ctx, _request())
            await middleware.after_model(
                ctx,
                _response(
                    text="SECRET-ASSISTANT-TEXT",
                    reasoning="SECRET-REASONING-TEXT",
                ),
            )
            call = ToolCall(
                id="call-secret",
                name="read_file",
                raw_arguments="{}",
                arguments={"path": "SECRET-TOOL-ARGUMENT"},
            )

            async def call_next(_call: ToolCall) -> ToolOutcome:
                return ToolOutcome(
                    status="ok", result={"content": "SECRET-TOOL-RESULT"}
                )

            await middleware.wrap_tool(ctx, call, call_next)
            await middleware.on_run_end(
                ctx, RunOutcome(status="succeeded", finish_reason="stop")
            )
            await recorder.flush()

            dump = "\n".join(
                _attributes_text(span) for span in await _spans(factory)
            )
            for secret in (
                "SECRET-ASSISTANT-TEXT",
                "SECRET-REASONING-TEXT",
                "SECRET-TOOL-ARGUMENT",
                "SECRET-TOOL-RESULT",
            ):
                assert secret not in dump
            for span in await _spans(factory):
                for key in span.attributes:
                    assert key not in {
                        "content",
                        "messages",
                        "arguments",
                        "result",
                        "reasoning",
                    }

    asyncio.run(scenario())


# --------------------------------------------------------------------------- #
# 10. FakeLLM 驱动的 legacy_chat 会话
# --------------------------------------------------------------------------- #
def test_legacy_chat_turn_writes_spans(tmp_path: Path) -> None:
    async def scenario() -> None:
        from sqlmodel import SQLModel

        engine = make_engine(f"sqlite+aiosqlite:///{tmp_path / 'legacy.db'}")
        async with engine.begin() as connection:
            await connection.run_sync(SQLModel.metadata.create_all)
        factory = async_sessionmaker(
            engine, expire_on_commit=False, class_=AsyncSession
        )
        recorder = SpanRecorder(
            factory, settings_provider=lambda: _settings(enabled=True)
        )
        profile = _profile()
        app_state = SimpleNamespace(
            span_recorder=recorder,
            session_factory=factory,
            cost_ledger=CostLedger(factory),
            model_router=_Router(profile),
        )
        app = SimpleNamespace(state=app_state)
        request = SimpleNamespace(app=app)
        registry = ToolRegistry()
        invoked: list[str] = []

        async def lookup(params: _LookupParams, _context: Any) -> dict[str, Any]:
            invoked.append(params.key)
            return {"value": f"found:{params.key}"}

        registry.register(
            ToolDefinition(
                name="lookup_status",
                group="status",
                risk=ToolRisk.SAFE,
                description="Read one status value.",
                params_model=_LookupParams,
                handler=lookup,
                read_only=True,
            )
        )
        from maa_api.agent.confirmation import ConfirmationService
        from maa_api.agent.policy import PolicyEngine

        confirmations = ConfirmationService(
            factory, registry, policy=PolicyEngine()
        )
        llm = FakeLLM(
            [
                tool_response(
                    tool_call("call-1", "lookup_status", {"key": "stage"})
                ),
                # 带 id / model 的原始分片：顺带覆盖 M68 §4.2.3 的
                # ``ModelResponse.model`` 与 ``response_id`` 填入。
                [
                    {
                        "id": "chatcmpl-eval-1",
                        "model": "upstream-model",
                        "choices": [
                            {
                                "index": 0,
                                "delta": {"content": "已读取关卡状态"},
                                "finish_reason": None,
                            }
                        ],
                    },
                    {
                        "id": "chatcmpl-eval-2",
                        "model": "upstream-model-2",
                        "choices": [
                            {
                                "index": 0,
                                "delta": {},
                                "finish_reason": "stop",
                            }
                        ],
                        "usage": {
                            "prompt_tokens": 11,
                            "completion_tokens": 3,
                            "total_tokens": 14,
                        },
                    },
                ],
            ]
        )
        runtime = AgentRuntime(
            factory,
            confirmations,
            settings_provider=lambda _request: _settings(enabled=True),
            client_factory=lambda _settings: llm,
        )
        app_state.agent_runtime = runtime
        runtime._capability_cache[("test-model", "https://llm.test/v1")] = {
            "model": "test-model",
            "tool_calling": True,
            "vision": True,
            "ready": True,
            "status": "ready",
            "degraded_reason": None,
        }
        async with factory() as session:
            session.add(
                AgentSession(
                    id="session-1",
                    model="test-model",
                    base_url="https://llm.test/v1",
                )
            )
            await session.commit()

        try:
            assert await runtime.acquire_session("session-1") is True
            chunks = [
                chunk
                async for chunk in stream_legacy_turn(
                    runtime, "session-1", "当前关卡？", request
                )
            ]
            assert any("message_end" in chunk for chunk in chunks)
            runtime.release_session("session-1")
            assert invoked == ["stage"]

            await recorder.flush()
            spans = await _spans(factory)
            names = [span.name for span in spans]
            assert names.count("agent.run") == 1
            assert names.count("llm.chat") == 2
            assert names.count("tool.execute") == 1

            root = _by_name(spans, "agent.run")[0]
            assert root.parent_span_id is None
            for span in spans:
                if span is root:
                    continue
                assert span.parent_span_id == root.span_id
                assert span.trace_id == root.trace_id

            async with factory() as session:
                run = (
                    await session.execute(
                        select(AgentRun).where(AgentRun.session_id == "session-1")
                    )
                ).scalar_one()
                ledger_total = (
                    await session.execute(
                        select(func.coalesce(func.sum(CostLedgerEntry.cost_micros), 0))
                        .where(CostLedgerEntry.run_id == run.id)
                    )
                ).scalar_one()
            llm_spans = _by_name(spans, "llm.chat")
            span_costs = sum(
                int(span.attributes["maa.cost.micros"]) for span in llm_spans
            )
            # 第一个带字符串 model / id 的分片决定响应字段（后续分片不覆盖）。
            assert llm_spans[-1].attributes["gen_ai.response.model"] == "upstream-model"
            assert llm_spans[-1].attributes["gen_ai.response.id"] == "chatcmpl-eval-1"
            assert span_costs > 0
            assert span_costs == run.cost_micros == ledger_total
            assert root.attributes["maa.cost.micros"] == run.cost_micros

            dump = "\n".join(_attributes_text(span) for span in spans)
            for content in (
                "当前关卡？",
                "已读取关卡状态",
                "found:stage",
                '"stage"',
            ):
                assert content not in dump
        finally:
            runtime.release_session("session-1")
            await recorder.close()
            await engine.dispose()

    asyncio.run(scenario())
