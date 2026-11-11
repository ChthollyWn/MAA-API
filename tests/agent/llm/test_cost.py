"""M67 T5：成本账本（规格 §4.2.5、§6）。

用例形态与 ``tests/db`` 一致：同步测试函数 + ``asyncio.run(scenario())``（本仓没有
pytest-asyncio），每个场景自建内存库。价格是「每百万 token」的币种单位，账本列
是百万分之一币种，所以 token × 价格就是 micros。
"""

from __future__ import annotations

import asyncio
import logging
from datetime import UTC, date, datetime, time, timedelta
from typing import Any

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker
from sqlalchemy.pool import StaticPool
from sqlmodel import SQLModel

import maa_api.db.models  # noqa: F401  # 把表注册进 SQLModel.metadata
from maa_api.agent.llm.cost import (
    BUDGET_CURRENCY,
    PEAK_TIMEZONE,
    CostLedger,
    compute_cost_micros,
    compute_cost_micros_at,
    is_peak_time,
    parse_peak_holidays,
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
from maa_api.settings import LLMSettings, Settings

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
    price_input_offpeak: float | None = None,
    price_cached_input_offpeak: float | None = None,
    price_output_offpeak: float | None = None,
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
        price_input_offpeak=price_input_offpeak,
        price_cached_input_offpeak=price_cached_input_offpeak,
        price_output_offpeak=price_output_offpeak,
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


# ---------------------------------------------------------------------------
# 峰谷计价（offpeak 规格 §4.5）
# ---------------------------------------------------------------------------
def _beijing(
    year: int, month: int, day: int, hour: int, minute: int, second: int = 0
) -> datetime:
    """北京时间的 naive 时刻 → naive UTC（项目统一的 UTC 口径）。"""
    return datetime(year, month, day, hour, minute, second) - timedelta(hours=8)


def test_compute_cost_micros_at_uses_injected_settings() -> None:
    """``compute_cost_micros_at`` 从注入 settings 的 ``peak_holidays`` 取节假日。"""
    usage = Usage(prompt_tokens=1000, cached_tokens=400, completion_tokens=200)
    profile = _profile(
        price_input=2,
        price_cached_input=0.5,
        price_output=8,
        price_input_offpeak=1,
        price_cached_input_offpeak=0.25,
        price_output_offpeak=4,
    )
    # 2026-11-11 01:00 UTC = 北京时间周三 09:00，平常落在高峰窗口。
    at = datetime(2026, 11, 11, 1, 0)
    holiday = Settings(llm=LLMSettings(peak_holidays="2026-11-11"))
    plain = Settings(llm=LLMSettings(peak_holidays=""))

    # 注入的节假日列表生效 → 按空闲价 1500，而不是高峰价 3000。
    assert compute_cost_micros_at(
        usage, profile, at=at, settings_provider=lambda: holiday
    ) == 1500
    # 同一时刻、空节假日列表仍是高峰价，证明结果由 settings 里的列表决定。
    assert compute_cost_micros_at(
        usage, profile, at=at, settings_provider=lambda: plain
    ) == 3000


def test_peak_windows_boundaries() -> None:
    """工作日 [09:00,12:00)、[14:00,18:00) 为高峰，两端左闭右开。"""
    assert not is_peak_time(_beijing(2026, 11, 11, 8, 59, 59))
    assert is_peak_time(_beijing(2026, 11, 11, 9, 0))
    assert not is_peak_time(_beijing(2026, 11, 11, 12, 0))
    assert not is_peak_time(_beijing(2026, 11, 11, 13, 59, 59))
    assert is_peak_time(_beijing(2026, 11, 11, 14, 0))
    assert not is_peak_time(_beijing(2026, 11, 11, 18, 0))

    # 带 +08:00 的时间按同一本地时刻判断；naive 按 UTC 解释（同一用例已覆盖）。
    assert is_peak_time(datetime(2026, 11, 11, 9, 0, tzinfo=PEAK_TIMEZONE))
    assert not is_peak_time(
        datetime(2026, 11, 11, 8, 59, 59, tzinfo=PEAK_TIMEZONE)
    )


def test_weekend_is_offpeak() -> None:
    """周六/周日任意时刻都是空闲时段。"""
    assert not is_peak_time(_beijing(2026, 11, 14, 10, 0))  # 周六
    assert not is_peak_time(_beijing(2026, 11, 15, 10, 0))  # 周日


def test_holiday_is_offpeak() -> None:
    """节假日列表里的日期全天按空闲时段。"""
    holidays = frozenset({date(2026, 11, 11)})
    assert not is_peak_time(_beijing(2026, 11, 11, 10, 0), holidays=holidays)
    # 不在列表里的前一个工作日同一时刻仍是高峰。
    assert is_peak_time(_beijing(2026, 11, 10, 10, 0), holidays=holidays)


def test_offpeak_prices_fallback_per_component() -> None:
    """空闲价逐项回退：缺哪项用对应高峰价；峰价全空、谷价有值时高峰成本 0。"""
    usage = Usage(prompt_tokens=1000, cached_tokens=400, completion_tokens=200)
    peak_at = _beijing(2026, 11, 11, 9, 0)
    offpeak_at = _beijing(2026, 11, 11, 12, 0)

    # 只配输出谷价：输入/缓存回退峰价 → 600×2 + 400×0.5 + 200×4 = 2200。
    only_output_offpeak = _profile(
        price_input=2,
        price_cached_input=0.5,
        price_output=8,
        price_output_offpeak=4,
    )
    assert compute_cost_micros(usage, only_output_offpeak, at=offpeak_at) == 2200
    # 同一档案在高峰用峰价 → 600×2 + 400×0.5 + 200×8 = 3000。
    assert compute_cost_micros(usage, only_output_offpeak, at=peak_at) == 3000

    # 谷价全为 None 时与只配峰价完全一致（空闲时段也不例外）。
    peak_only = _profile(price_input=2, price_cached_input=0.5, price_output=8)
    assert compute_cost_micros(usage, peak_only, at=offpeak_at) == 3000
    assert compute_cost_micros(usage, peak_only, at=offpeak_at) == (
        compute_cost_micros(usage, peak_only)
    )

    # 三项谷价都配置时按谷价 → 600×1 + 400×0.25 + 200×4 = 1500。
    full_offpeak = _profile(
        price_input=2,
        price_cached_input=0.5,
        price_output=8,
        price_input_offpeak=1,
        price_cached_input_offpeak=0.25,
        price_output_offpeak=4,
    )
    assert compute_cost_micros(usage, full_offpeak, at=offpeak_at) == 1500
    assert compute_cost_micros(usage, full_offpeak, at=peak_at) == 3000

    # 高峰价全 None、谷价有值：高峰时段成本 0，空闲时段按谷价。
    offpeak_only = _profile(
        price_input_offpeak=1,
        price_cached_input_offpeak=0.25,
        price_output_offpeak=4,
    )
    assert compute_cost_micros(usage, offpeak_only, at=peak_at) == 0
    assert compute_cost_micros(usage, offpeak_only, at=offpeak_at) == 1500


def test_compute_without_at_keeps_peak_semantics() -> None:
    """不传 ``at`` 时保持旧公式：全部按高峰价，结果与旧版完全一致。"""
    usage = Usage(prompt_tokens=1000, cached_tokens=400, completion_tokens=200)
    profile = _profile(
        price_input=2,
        price_cached_input=0.5,
        price_output=8,
        price_input_offpeak=1,
        price_cached_input_offpeak=0.25,
        price_output_offpeak=4,
    )

    # M67 公式：600×2 + 400×0.5 + 200×8 = 3000。
    assert compute_cost_micros(usage, profile) == 3000
    assert compute_cost_micros(usage, profile) == compute_cost_micros(
        usage, profile, at=_beijing(2026, 11, 11, 9, 0)
    )


def test_parse_peak_holidays_skips_invalid(caplog) -> None:
    """逗号/空白分隔；非法项 warning 后跳过，重复项忽略，不抛异常。"""
    raw = "2026-11-11, 2026-11-12\n 2027-01-01, not-a-date, 2026-13-01,,2026-11-11"

    with caplog.at_level(logging.WARNING, logger="maa_api.agent.llm.cost"):
        holidays = parse_peak_holidays(raw)

    assert holidays == frozenset(
        {date(2026, 11, 11), date(2026, 11, 12), date(2027, 1, 1)}
    )
    assert len(caplog.records) == 2
    assert parse_peak_holidays("") == frozenset()
    assert parse_peak_holidays("  \n\t ") == frozenset()


def test_ledger_records_offpeak_cost() -> None:
    """账本按记账时刻取价：北京时间 09:00 高峰、12:00 空闲各记一行。"""
    async def scenario() -> None:
        factory, engine = await _make_factory()
        try:
            await _create_run(factory, "run-offpeak")
            # 注入空节假日，避免本机 config.yaml 把 2026-11-11 配成节假日。
            ledger = CostLedger(
                factory,
                settings_provider=lambda: Settings(
                    llm=LLMSettings(peak_holidays="")
                ),
            )
            profile = _profile(
                price_input=2,
                price_cached_input=0.5,
                price_output=8,
                price_input_offpeak=1,
                price_cached_input_offpeak=0.25,
                price_output_offpeak=4,
            )
            usage = Usage(
                prompt_tokens=1000, cached_tokens=400, completion_tokens=200
            )

            async def run_cost() -> int:
                async with factory() as db:
                    run = (
                        await db.execute(
                            select(AgentRun).where(AgentRun.id == "run-offpeak")
                        )
                    ).scalar_one()
                return run.cost_micros

            # 2026-11-11 01:00 UTC = 北京时间周三 09:00（高峰）→ 3000。
            peak_cost = await ledger.record(
                run_id="run-offpeak",
                session_id=None,
                agent_name="legacy_chat",
                role="worker",
                profile=profile,
                usage=usage,
                at=datetime(2026, 11, 11, 1, 0),
            )
            assert peak_cost == 3000
            assert await run_cost() == peak_cost

            # 2026-11-11 04:00 UTC = 北京时间周三 12:00（空闲）→ 1500。
            offpeak_cost = await ledger.record(
                run_id="run-offpeak",
                session_id=None,
                agent_name="legacy_chat",
                role="worker",
                profile=profile,
                usage=usage,
                at=datetime(2026, 11, 11, 4, 0),
            )
            assert offpeak_cost == 1500
            assert await run_cost() == peak_cost + offpeak_cost

            async with factory() as db:
                rows = (
                    await db.execute(
                        select(CostLedgerEntry).order_by(CostLedgerEntry.id)
                    )
                ).scalars().all()
            assert [row.cost_micros for row in rows] == [peak_cost, offpeak_cost]
        finally:
            await engine.dispose()

    asyncio.run(scenario())
