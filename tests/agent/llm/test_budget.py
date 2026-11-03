"""M67 T5：预算检查（规格 §4.2.5、§6）。

用例形态与 ``tests/agent`` 一致：同步测试函数 + ``asyncio.run(scenario())``（本仓
没有 pytest-asyncio）。第一个用例走完整的 v2 门面（运行建立、预算超限、SSE 与
``agent_run`` 终态）；其余两个直接驱动 :class:`BudgetMiddleware`，不碰网络。
"""

from __future__ import annotations

import asyncio
from types import SimpleNamespace
from typing import Any

from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker
from sqlalchemy.pool import StaticPool
from sqlmodel import SQLModel

import maa_api.db.models  # noqa: F401  # 把表注册进 SQLModel.metadata
from maa_api.agent.confirmation import ConfirmationService
from maa_api.agent.harness.context import ModelRequest, RunContext
from maa_api.agent.llm.budget import BudgetMiddleware
from maa_api.agent.llm.cost import BUDGET_CURRENCY, CostLedger
from maa_api.agent.llm.profiles import ResolvedProfile
from maa_api.agent.llm.usage import Usage
from maa_api.agent.policy import PolicyEngine
from maa_api.agent.registry import ToolRegistry
from maa_api.agent.runtime import AgentRuntime
from maa_api.agent.specs.registry import builtin_spec
from maa_api.db import session as db_session
from maa_api.db.models import AgentRun, AgentSession, CostLedgerEntry
from maa_api.db.repositories.agent import AgentSessionRepository
from maa_api.db.repositories.cost import CostLedgerRepository
from maa_api.domain.enums import (
    AgentRunKind,
    AgentRunStatus,
    DynamicContextPosition,
    ModelProfileKind,
    ModelProvider,
)
from maa_api.domain.errors import AppError, ErrorCode
from maa_api.settings import BudgetSettings, LLMSettings, Settings
from tests.fakes.fake_llm import FakeLLM, parse_events

MODEL = "budget-model"
BASE_URL = "https://llm.test/v1"


async def _make_factory() -> tuple[async_sessionmaker[AsyncSession], Any]:
    engine = db_session.make_engine(
        "sqlite+aiosqlite:///:memory:", poolclass=StaticPool
    )
    async with engine.begin() as connection:
        await connection.run_sync(SQLModel.metadata.create_all)
    factory = async_sessionmaker(engine, expire_on_commit=False, class_=AsyncSession)
    return factory, engine


def _profile(
    *,
    name: str = "default",
    model: str = MODEL,
    currency: str = "CNY",
    price_input: float | None = None,
    price_cached_input: float | None = None,
    price_output: float | None = None,
) -> ResolvedProfile:
    return ResolvedProfile(
        name=name,
        kind=str(ModelProfileKind.CHAT),
        provider=str(ModelProvider.OPENAI_COMPATIBLE),
        base_url=BASE_URL,
        api_key="sk-test",
        model=model,
        context_window=None,
        max_output_tokens=None,
        supports_vision=None,
        supports_tools=True,
        supports_json_schema=None,
        dynamic_context_position=str(DynamicContextPosition.TAIL_SYSTEM),
        price_input=price_input,
        price_cached_input=price_cached_input,
        price_output=price_output,
        currency=currency,
        embedding_dim=None,
        local_model_path=None,
        readonly=True,
    )


def _settings(*, per_run: float = 0.0, daily: float = 0.0) -> Settings:
    return Settings(
        llm=LLMSettings(base_url=BASE_URL, api_key="sk-test", model=MODEL),
        budget=BudgetSettings(
            per_run_cost_limit=per_run, daily_cost_limit=daily
        ),
    )


def _ctx(run_id: str = "run-budget") -> RunContext:
    return RunContext(
        run_id=run_id,
        root_run_id=run_id,
        parent_run_id=None,
        session_id=None,
        ticket_id=None,
        spec=builtin_spec("legacy_chat"),
    )


def _entry(
    *,
    run_id: str | None = None,
    cost: int,
    currency: str = BUDGET_CURRENCY,
) -> CostLedgerEntry:
    return CostLedgerEntry(
        run_id=run_id,
        agent_name="legacy_chat",
        role="worker",
        model_profile="default",
        model=MODEL,
        cost_micros=cost,
        currency=currency,
    )


async def _add_rows(
    factory: async_sessionmaker[AsyncSession], rows: list[CostLedgerEntry]
) -> None:
    async with factory() as db:
        repo = CostLedgerRepository(db)
        for row in rows:
            await repo.add(row)
        await db.commit()


# ---------------------------------------------------------------------------
# 运行级上限：完整门面
# ---------------------------------------------------------------------------
def test_run_limit_stops_before_the_next_call() -> None:
    """单次上限 0.001 元；已花 2000 micros 后下一次 before_model 直接拒绝。

    用完整的 v2 门面跑一轮：SSE 必须发 ``BUDGET_EXCEEDED`` 的 error，``agent_run``
    以 ``finish_reason=budget_exceeded`` / ``status=failed`` / ``error_code`` 收尾。
    """
    async def scenario() -> None:
        factory, engine = await _make_factory()
        try:
            settings = _settings(per_run=0.001)
            confirmations = ConfirmationService(
                factory, ToolRegistry(), policy=PolicyEngine()
            )
            llm = FakeLLM([])
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
            async with factory() as db:
                await AgentSessionRepository(db).create(
                    AgentSession(id="session-budget", model=MODEL, base_url=BASE_URL)
                )
                await db.commit()

            ledger = CostLedger(factory)
            run_ids: list[str] = []
            original_begin = runtime._begin_legacy_run

            async def begin_with_one_paid_call(session_id: str, **kwargs: Any) -> AgentRun:
                run = await original_begin(session_id, **kwargs)
                run_ids.append(run.id)
                # 模拟「第一次调用已经花掉 2000 micros」：1000 prompt × 2 元/百万。
                await ledger.record(
                    run_id=run.id,
                    session_id=session_id,
                    agent_name="legacy_chat",
                    role="worker",
                    profile=_profile(price_input=2),
                    usage=Usage(prompt_tokens=1000),
                )
                return run

            runtime._begin_legacy_run = begin_with_one_paid_call  # type: ignore[method-assign]
            request = SimpleNamespace(
                app=SimpleNamespace(
                    state=SimpleNamespace(cost_ledger=ledger)
                )
            )

            assert await runtime.acquire_session("session-budget") is True
            chunks = [
                chunk
                async for chunk in runtime.stream_message(
                    "session-budget", "早上好", request
                )
            ]
            runtime.release_session("session-budget")

            events = parse_events(chunks)
            errors = [data for name, data in events if name == "error"]
            assert errors == [
                {
                    "code": str(ErrorCode.BUDGET_EXCEEDED),
                    "message": "这次运行的模型成本已达到上限",
                    "details": {
                        "scope": "run",
                        "limit": 0.001,
                        "spent_micros": 2000,
                        "currency": BUDGET_CURRENCY,
                    },
                }
            ], events
            # 预算在模型调用前就拒绝了，模型一次都没有被请求。
            assert llm.requests == []

            run = await runtime.run_service.get(run_ids[0])
            assert run is not None
            assert str(run.status) == AgentRunStatus.FAILED
            assert run.finish_reason == "budget_exceeded"
            assert run.error_code == str(ErrorCode.BUDGET_EXCEEDED)
            assert run.cost_micros == 2000
        finally:
            await engine.dispose()

    asyncio.run(scenario())


# ---------------------------------------------------------------------------
# 每日上限：只统计 CNY
# ---------------------------------------------------------------------------
def test_daily_limit_counts_only_cny_rows() -> None:
    """每日上限 0.001 元：USD 行不计入，CNY 行到线才拒绝。"""
    async def scenario() -> None:
        factory, engine = await _make_factory()
        try:
            await _add_rows(
                factory,
                [
                    _entry(cost=10_000, currency="USD"),
                    _entry(cost=500, currency=BUDGET_CURRENCY),
                ],
            )
            ledger = CostLedger(factory)
            middleware = BudgetMiddleware(
                ledger=ledger, settings_provider=lambda: _settings(daily=0.001)
            )
            ctx = _ctx()
            request = ModelRequest(messages=[], tools=[])

            # 10000 的 USD 行不计入每日花费，CNY 只花了 500 < 1000。
            assert await middleware.before_model(ctx, request) is request

            await _add_rows(factory, [_entry(cost=1500, currency=BUDGET_CURRENCY)])
            with_app_error = None
            try:
                await middleware.before_model(ctx, request)
            except AppError as exc:
                with_app_error = exc
            assert with_app_error is not None
            assert with_app_error.code == ErrorCode.BUDGET_EXCEEDED
            assert with_app_error.details == {
                "scope": "day",
                "limit": 0.001,
                "spent_micros": 2000,
                "currency": BUDGET_CURRENCY,
            }
        finally:
            await engine.dispose()

    asyncio.run(scenario())


# ---------------------------------------------------------------------------
# 告警：每次运行只广播一次
# ---------------------------------------------------------------------------
def test_warning_is_broadcast_once_per_run() -> None:
    """越过告警线后连续两次调用只广播一次 ``budget_warning``。"""
    async def scenario() -> None:
        factory, engine = await _make_factory()
        try:
            broadcasts: list[tuple[str, Any]] = []
            async with factory() as db:
                db.add(
                    AgentRun(
                        id="run-warn",
                        root_run_id="run-warn",
                        agent_name="legacy_chat",
                        kind=AgentRunKind.LEGACY_TURN,
                        boot_id="boot-test",
                    )
                )
                await db.commit()
            await _add_rows(factory, [_entry(run_id="run-warn", cost=9000)])
            ledger = CostLedger(
                factory, broadcast=lambda name, data: broadcasts.append((name, data))
            )
            middleware = BudgetMiddleware(
                ledger=ledger, settings_provider=lambda: _settings(per_run=0.01)
            )
            ctx = _ctx("run-warn")
            request = ModelRequest(messages=[], tools=[])

            # 9000 / 10000 越过默认告警线 0.8；还没到上限，两次都不抛。
            await middleware.before_model(ctx, request)
            await middleware.before_model(ctx, request)

            assert broadcasts == [
                (
                    "agent_event",
                    {
                        "session_id": None,
                        "event": "budget_warning",
                        "payload": {
                            "scope": "run",
                            "limit": 0.01,
                            "spent_micros": 9000,
                            "currency": BUDGET_CURRENCY,
                            "run_id": "run-warn",
                        },
                    },
                )
            ], broadcasts
            assert ctx.state["budget"]["run_warned"] is True
        finally:
            await engine.dispose()

    asyncio.run(scenario())
