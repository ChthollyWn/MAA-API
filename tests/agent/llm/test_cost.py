"""M67 T5：成本账本（规格 §4.2.5、§6）。

用例形态与 ``tests/db`` 一致：同步测试函数 + ``asyncio.run(scenario())``（本仓没有
pytest-asyncio），每个场景自建内存库。价格是「每百万 token」的币种单位，账本列
是百万分之一币种，所以 token × 价格就是 micros。
"""

from __future__ import annotations

import asyncio
from datetime import UTC, date, datetime, time, timedelta
from typing import Any

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker
from sqlalchemy.pool import StaticPool
from sqlmodel import SQLModel

import maa_api.db.models  # noqa: F401  # 把表注册进 SQLModel.metadata
from maa_api.agent.llm.cost import (
    BUDGET_CURRENCY,
    CostLedger,
    compute_cost_micros,
)
from maa_api.agent.llm.profiles import ResolvedProfile
from maa_api.agent.llm.usage import Usage
from maa_api.db import session as db_session
from maa_api.db.models import AgentRun, CostLedgerEntry
from maa_api.db.repositories.cost import CostLedgerRepository
from maa_api.domain.enums import (
    AgentRunKind,
    DynamicContextPosition,
    ModelProfileKind,
    ModelProvider,
)

#: 当前进程的服务器本地时区；``day`` 分组按它计算。
LOCAL_TZ = datetime.now().astimezone().tzinfo


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
    name: str = "worker-a",
    model: str = "worker-model",
    currency: str = "CNY",
    price_input: float | None = None,
    price_cached_input: float | None = None,
    price_output: float | None = None,
) -> ResolvedProfile:
    return ResolvedProfile(
        name=name,
        kind=str(ModelProfileKind.CHAT),
        provider=str(ModelProvider.OPENAI_COMPATIBLE),
        base_url="https://profile.test/v1",
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
        readonly=False,
    )


async def _create_run(factory: async_sessionmaker[AsyncSession], run_id: str) -> None:
    async with factory() as db:
        db.add(
            AgentRun(
                id=run_id,
                root_run_id=run_id,
                agent_name="legacy_chat",
                kind=AgentRunKind.LEGACY_TURN,
                boot_id="boot-test",
            )
        )
        await db.commit()


async def _add_rows(
    factory: async_sessionmaker[AsyncSession], rows: list[CostLedgerEntry]
) -> None:
    async with factory() as db:
        repo = CostLedgerRepository(db)
        for row in rows:
            await repo.add(row)
        await db.commit()


def _utc_naive(local_moment: datetime) -> datetime:
    return local_moment.astimezone(UTC).replace(tzinfo=None)


def _local_midnight(day: date) -> datetime:
    """本地某天 0 点对应的 naive UTC（summarize 的区间端点）。"""
    return _utc_naive(datetime.combine(day, time.min, tzinfo=LOCAL_TZ))


# ---------------------------------------------------------------------------
# 成本公式
# ---------------------------------------------------------------------------
def test_cost_formula_splits_cached_input() -> None:
    """prompt 1000（缓存 400）、completion 200，价格 2/0.5/8 → 3000 micros。"""
    usage = Usage(prompt_tokens=1000, cached_tokens=400, completion_tokens=200)
    profile = _profile(
        price_input=2, price_cached_input=0.5, price_output=8
    )

    # 600 × 2 + 400 × 0.5 + 200 × 8 = 3000。
    assert compute_cost_micros(usage, profile) == 3000

    # 缓存价格没配时退回输入价；.5 micros 按 ROUND_HALF_UP 进一位。
    fallback = _profile(price_input=2, price_output=8)
    assert compute_cost_micros(usage, fallback) == 1000 * 2 + 200 * 8


def test_missing_prices_cost_zero_but_rows_are_written() -> None:
    """价格全空时成本是 0，账本行与运行累计照写（§4.5.6）。"""
    async def scenario() -> None:
        factory, engine = await _make_factory()
        try:
            await _create_run(factory, "run-free")
            ledger = CostLedger(factory)

            cost = await ledger.record(
                run_id="run-free",
                session_id=None,
                agent_name="legacy_chat",
                role="worker",
                profile=_profile(),
                usage=Usage(prompt_tokens=100, completion_tokens=20),
            )
            assert cost == 0

            async with factory() as db:
                rows = (
                    await db.execute(select(CostLedgerEntry))
                ).scalars().all()
                run = (
                    await db.execute(
                        select(AgentRun).where(AgentRun.id == "run-free")
                    )
                ).scalar_one()
            assert len(rows) == 1
            assert rows[0].cost_micros == 0
            assert rows[0].currency == BUDGET_CURRENCY
            assert run.cost_micros == 0
            assert run.prompt_tokens == 100
            assert run.completion_tokens == 20
        finally:
            await engine.dispose()

    asyncio.run(scenario())


# ---------------------------------------------------------------------------
# 运行累计
# ---------------------------------------------------------------------------
def test_record_accumulates_into_agent_run() -> None:
    """两次调用后四个 token 列、成本与 model_profile 都是两次之和/首次写入。"""
    async def scenario() -> None:
        factory, engine = await _make_factory()
        try:
            await _create_run(factory, "run-acc")
            ledger = CostLedger(factory)
            profile = _profile(
                name="worker-a",
                model="worker-model",
                price_input=2,
                price_cached_input=0.5,
                price_output=8,
            )

            first = await ledger.record(
                run_id="run-acc",
                session_id=None,
                agent_name="legacy_chat",
                role="worker",
                profile=profile,
                usage=Usage(
                    prompt_tokens=100,
                    cached_tokens=20,
                    completion_tokens=10,
                    reasoning_tokens=4,
                ),
            )
            second = await ledger.record(
                run_id="run-acc",
                session_id=None,
                agent_name="legacy_chat",
                role="worker",
                profile=profile,
                usage=Usage(
                    prompt_tokens=50,
                    cached_tokens=5,
                    completion_tokens=7,
                    reasoning_tokens=3,
                ),
            )
            # 80×2 + 20×0.5 + 10×8 = 250；45×2 + 5×0.5 + 7×8 = 148.5 → 149。
            assert (first, second) == (250, 149)

            async with factory() as db:
                rows = (
                    await db.execute(select(CostLedgerEntry))
                ).scalars().all()
                run = (
                    await db.execute(
                        select(AgentRun).where(AgentRun.id == "run-acc")
                    )
                ).scalar_one()
            assert len(rows) == 2
            assert run.prompt_tokens == 150
            assert run.cached_tokens == 25
            assert run.completion_tokens == 17
            assert run.reasoning_tokens == 7
            assert run.cost_micros == 399
            assert run.currency == BUDGET_CURRENCY
            assert run.model == "worker-model"
            assert run.model_profile == "worker-a"
        finally:
            await engine.dispose()

    asyncio.run(scenario())


# ---------------------------------------------------------------------------
# 汇总
# ---------------------------------------------------------------------------
def test_summarize_groups_by_day_agent_model_role() -> None:
    """四种分组的 key、计数、合计正确；区间外的行不计。"""
    async def scenario() -> None:
        factory, engine = await _make_factory()
        try:
            today = datetime.now().astimezone().date()
            yesterday = today - timedelta(days=1)
            d1 = _local_midnight(yesterday)
            d2 = _local_midnight(today)
            until = _local_midnight(today + timedelta(days=1))

            def row(
                created_at: datetime,
                *,
                agent: str,
                model: str,
                role: str,
                prompt: int,
                cached: int,
                completion: int,
                cost: int,
            ) -> CostLedgerEntry:
                return CostLedgerEntry(
                    agent_name=agent,
                    role=role,
                    model_profile="worker-a",
                    model=model,
                    prompt_tokens=prompt,
                    cached_tokens=cached,
                    completion_tokens=completion,
                    cost_micros=cost,
                    currency=BUDGET_CURRENCY,
                    created_at=created_at,
                )

            await _add_rows(
                factory,
                [
                    row(
                        d1 + timedelta(hours=10),  # 本地 D1 的 10 点
                        agent="legacy_chat",
                        model="m1",
                        role="worker",
                        prompt=100,
                        cached=10,
                        completion=20,
                        cost=100,
                    ),
                    row(
                        d1 + timedelta(hours=23),
                        agent="legacy_chat",
                        model="m2",
                        role="worker",
                        prompt=200,
                        cached=20,
                        completion=30,
                        cost=200,
                    ),
                    row(
                        d2 + timedelta(hours=10),
                        agent="incident",
                        model="m1",
                        role="summarizer",
                        prompt=300,
                        cached=30,
                        completion=40,
                        cost=500,
                    ),
                    # 区间外：早于 since、晚于 until，都不参与汇总。
                    row(
                        d1 - timedelta(days=10),
                        agent="outside",
                        model="m1",
                        role="worker",
                        prompt=1,
                        cached=1,
                        completion=1,
                        cost=9999,
                    ),
                    row(
                        until + timedelta(days=10),
                        agent="outside",
                        model="m1",
                        role="worker",
                        prompt=1,
                        cached=1,
                        completion=1,
                        cost=9999,
                    ),
                ],
            )
            ledger = CostLedger(factory)

            async def grouped(group_by: str):
                return await ledger.summarize(
                    since=d1, until=until, group_by=group_by, currency=None
                )

            day = await grouped("day")
            assert day.currency == BUDGET_CURRENCY
            assert day.total_micros == 800
            assert [(group.key, group.calls, group.cost_micros) for group in day.groups] == [
                (yesterday.isoformat(), 2, 300),
                (today.isoformat(), 1, 500),
            ]
            assert day.groups[0].prompt_tokens == 300
            assert day.groups[0].cached_tokens == 30
            assert day.groups[0].completion_tokens == 50

            agent = await grouped("agent")
            assert [(group.key, group.calls, group.cost_micros) for group in agent.groups] == [
                ("incident", 1, 500),
                ("legacy_chat", 2, 300),
            ]

            model = await grouped("model")
            assert [(group.key, group.calls, group.cost_micros) for group in model.groups] == [
                ("m1", 2, 600),
                ("m2", 1, 200),
            ]

            role = await grouped("role")
            assert [(group.key, group.calls, group.cost_micros) for group in role.groups] == [
                ("summarizer", 1, 500),
                ("worker", 2, 300),
            ]
        finally:
            await engine.dispose()

    asyncio.run(scenario())
