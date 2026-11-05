"""M81 三个事件仓储的行为（spec §4.1、§6）。

用例形态：同步测试函数 + ``asyncio.run(scenario())``（本仓无 pytest-asyncio）；
临时库由 ``tests/fakes/v3_db.py`` 的 ``make_session_factory`` 建（与
``tests/db/conftest.py`` 同口径的 NullPool + ``create_all``）。

覆盖的硬约束：

- ``TriggerRepository.insert`` 只 ``flush``；同一 ``dedupe_key`` 第二次插入抛
  ``IntegrityError``（部分唯一索引），不带键的事件可以重复入表。
- 分页按 ``id DESC``、过滤条件与 ``total`` 一致；``recent_on_thread`` 的
  ``before_id`` 是严格小于；``last_routed`` 只认 ticket / observation。
- ``purge`` 保留挂了工单的、以及仍有较新观察记录引用的旧事件。
- 规则仓储整行 upsert（``created_at`` 保留、NULL 可写回）与删除返回值。
- 观察仓储的分页过滤、90 天清理与 ``stats_since`` 的四元组（bad 只数最近 5 条）。
"""

import asyncio
from datetime import datetime, timedelta

import pytest
from sqlalchemy.exc import IntegrityError

from maa_api.db.models import (
    AgentTrigger,
    Ticket,
    TriggerObservation,
    TriggerRule,
    utcnow,
)
from maa_api.db.repositories.triggers import (
    TriggerObservationRepository,
    TriggerRepository,
    TriggerRuleRepository,
)
from tests.fakes.v3_db import make_session_factory, seed_run, seed_session


@pytest.fixture
def v3_factory(db_path):
    """临时库的会话工厂；用例结束 dispose 同步引擎（不依赖事件循环）。"""
    engine, factory = asyncio.run(make_session_factory(db_path))
    yield factory
    engine.sync_engine.dispose()


def _trigger(
    *,
    source: str = "pipeline",
    kind: str = "pipeline.failed",
    thread_key: str = "pipeline:1",
    dedupe_key: str | None = None,
    rule_id: str | None = None,
    routed_to: str | None = None,
    eval_run_id: str | None = None,
    ticket_id: str | None = None,
    processed_at: datetime | None = None,
    created_at: datetime | None = None,
) -> AgentTrigger:
    return AgentTrigger(
        source=source,
        kind=kind,
        thread_key=thread_key,
        dedupe_key=dedupe_key,
        rule_id=rule_id,
        routed_to=routed_to,
        eval_run_id=eval_run_id,
        ticket_id=ticket_id,
        processed_at=processed_at,
        created_at=created_at or utcnow(),
    )


def _rule(rule_id: str, **kwargs) -> TriggerRule:
    kwargs.setdefault("origin", "override")
    kwargs.setdefault("enabled", True)
    return TriggerRule(id=rule_id, **kwargs)


def _observation(
    trigger_id: int,
    *,
    rule_id: str = "sanity-full",
    verdict: str | None = None,
    reviewed_at: datetime | None = None,
    created_at: datetime | None = None,
) -> TriggerObservation:
    return TriggerObservation(
        trigger_id=trigger_id,
        rule_id=rule_id,
        decision={"decision": "accept", "reason": "测试"},
        verdict=verdict,
        reviewed_at=reviewed_at,
        created_at=created_at or utcnow(),
    )


async def _seed_ticket(factory, ticket_id: str = "ticket-1") -> str:
    async with factory() as session:
        session.add(
            Ticket(
                id=ticket_id,
                title="事件工单",
                kind="daily_ops",
                origin="trigger",
            )
        )
        await session.commit()
    return ticket_id


def test_insert_dedupe_conflict_and_get(v3_factory):
    async def scenario():
        async with v3_factory() as session:
            repo = TriggerRepository(session)
            stored = await repo.insert(
                _trigger(dedupe_key="dup", rule_id="sanity-full")
            )
            await session.commit()
            assert stored.id == 1

        async with v3_factory() as session:
            repo = TriggerRepository(session)
            with pytest.raises(IntegrityError):
                await repo.insert(_trigger(dedupe_key="dup"))
            await session.rollback()

        async with v3_factory() as session:
            repo = TriggerRepository(session)
            # 不带 dedupe_key 的事件可以重复入表（部分索引只约束非 NULL）。
            await repo.insert(_trigger())
            await repo.insert(_trigger())
            await session.commit()

            fetched = await repo.get(1)
            assert fetched is not None and fetched.dedupe_key == "dup"
            assert await repo.get(999) is None
            by_key = await repo.get_by_dedupe_key("dup")
            assert by_key is not None and by_key.id == 1
            assert await repo.get_by_dedupe_key("missing") is None
            assert (await repo.page()).total == 3

    asyncio.run(scenario())


def test_page_filters_and_orders_by_id_desc(v3_factory):
    async def scenario():
        async with v3_factory() as session:
            repo = TriggerRepository(session)
            for index in range(4):
                await repo.insert(
                    _trigger(
                        source="pipeline" if index < 3 else "core",
                        kind="pipeline.failed" if index < 2 else "core.crashed",
                        rule_id="sanity-full" if index == 2 else None,
                    )
                )
            await session.commit()

            first = await repo.page(source="pipeline", page=1, size=1)
            assert first.total == 3
            assert [row.id for row in first.items] == [3]
            second = await repo.page(source="pipeline", page=2, size=1)
            assert [row.id for row in second.items] == [2]

            kinds = await repo.page(kind="core.crashed", page=1, size=20)
            assert kinds.total == 2
            assert [row.id for row in kinds.items] == [4, 3]

            ruled = await repo.page(rule_id="sanity-full", page=1, size=20)
            assert ruled.total == 1
            assert ruled.items[0].id == 3

            empty = await repo.page(source="watchdog", page=1, size=20)
            assert empty.total == 0 and empty.items == []

    asyncio.run(scenario())


def test_recent_on_thread_is_strictly_before_and_limited(v3_factory):
    async def scenario():
        async with v3_factory() as session:
            repo = TriggerRepository(session)
            for _ in range(5):
                await repo.insert(_trigger(thread_key="pipeline:1"))
            await repo.insert(_trigger(thread_key="pipeline:2"))
            await session.commit()

            recent = await repo.recent_on_thread("pipeline:1", limit=3, before_id=5)
            assert [row.id for row in recent] == [4, 3, 2]
            assert [
                row.id
                for row in await repo.recent_on_thread(
                    "pipeline:1", limit=1, before_id=5
                )
            ] == [4]
            # before_id 是严格小于：当前这条（5）不在结果里；更早的话题没有事件。
            assert (
                await repo.recent_on_thread("pipeline:2", limit=5, before_id=6)
            ) == []
            assert [
                row.id
                for row in await repo.recent_on_thread(
                    "pipeline:1", limit=10, before_id=99
                )
            ] == [5, 4, 3, 2, 1]

    asyncio.run(scenario())


def test_last_routed_only_counts_ticket_and_observation(v3_factory):
    async def scenario():
        now = utcnow()
        async with v3_factory() as session:
            repo = TriggerRepository(session)
            await session.commit()

        async with v3_factory() as session:
            repo = TriggerRepository(session)
            await repo.insert(
                _trigger(
                    rule_id="r1",
                    thread_key="t1",
                    routed_to="ticket",
                    created_at=now - timedelta(days=2),
                )
            )
            await repo.insert(
                _trigger(
                    rule_id="r1",
                    thread_key="t1",
                    routed_to="observation",
                    created_at=now - timedelta(minutes=30),
                )
            )
            await repo.insert(
                _trigger(
                    rule_id="r1",
                    thread_key="t1",
                    routed_to="none",
                    created_at=now - timedelta(minutes=10),
                )
            )
            await repo.insert(
                _trigger(
                    rule_id="r1",
                    thread_key="t1",
                    routed_to="orchestrator",
                    created_at=now - timedelta(minutes=5),
                )
            )
            await repo.insert(
                _trigger(
                    rule_id="r2",
                    thread_key="t1",
                    routed_to="ticket",
                    created_at=now - timedelta(minutes=3),
                )
            )
            await repo.insert(
                _trigger(
                    rule_id="r1",
                    thread_key="t2",
                    routed_to="ticket",
                    created_at=now - timedelta(minutes=2),
                )
            )
            await session.commit()

            found = await repo.last_routed(
                rule_id="r1", thread_key="t1", since=now - timedelta(hours=1)
            )
            assert found is not None and found.routed_to == "observation"
            assert (
                await repo.last_routed(
                    rule_id="r1", thread_key="t1", since=now - timedelta(minutes=20)
                )
            ) is None

    asyncio.run(scenario())


def test_count_evaluated_since_and_unprocessed(v3_factory):
    async def scenario():
        session_id = await seed_session(v3_factory)
        run_id = await seed_run(v3_factory, session_id=session_id)
        now = utcnow()

        async with v3_factory() as session:
            repo = TriggerRepository(session)
            await repo.insert(
                _trigger(eval_run_id=run_id, created_at=now - timedelta(hours=1))
            )
            await repo.insert(
                _trigger(eval_run_id=run_id, created_at=now - timedelta(days=2))
            )
            await repo.insert(
                _trigger(created_at=now - timedelta(minutes=5))
            )
            await repo.insert(
                _trigger(
                    created_at=now - timedelta(minutes=4),
                    processed_at=now - timedelta(minutes=3),
                )
            )
            await session.commit()

            assert (
                await repo.count_evaluated_since(now - timedelta(days=1))
            ) == 1
            unprocessed = await repo.unprocessed()
            assert [row.id for row in unprocessed] == [1, 2, 3]

    asyncio.run(scenario())


def test_purge_keeps_ticketed_and_recently_observed(v3_factory):
    async def scenario():
        now = utcnow()
        ticket_id = await _seed_ticket(v3_factory)

        async with v3_factory() as session:
            repo = TriggerRepository(session)
            observations = TriggerObservationRepository(session)
            old = now - timedelta(days=40)
            ticketed = await repo.insert(
                _trigger(
                    ticket_id=ticket_id,
                    created_at=old,
                    processed_at=old,
                )
            )
            observed = await repo.insert(
                _trigger(created_at=old, processed_at=old)
            )
            await repo.insert(_trigger(created_at=old, processed_at=old))
            await repo.insert(
                _trigger(created_at=now - timedelta(days=1), processed_at=now)
            )
            await repo.insert(
                _trigger(created_at=now - timedelta(days=200), processed_at=old)
            )
            await observations.create(
                _observation(
                    observed.id,
                    created_at=now - timedelta(days=10),
                )
            )
            await observations.create(
                _observation(
                    3,
                    created_at=now - timedelta(days=100),
                )
            )
            await session.commit()

            removed = await repo.purge(
                before=now - timedelta(days=30),
                keep_observed_since=now - timedelta(days=90),
            )
            assert removed == 2
            assert (await repo.get(ticketed.id)) is not None
            remaining = await repo.page(size=10)
            assert remaining.total == 3
            assert [row.id for row in remaining.items] == [4, 2, 1]

    asyncio.run(scenario())


def test_rule_upsert_and_delete(v3_factory):
    async def scenario():
        async with v3_factory() as session:
            repo = TriggerRuleRepository(session)
            assert await repo.all() == []
            assert await repo.get("sanity-full") is None

            original = _rule(
                "sanity-full",
                title="理智满了",
                mode="observe",
                worker="daily_ops",
                cooldown_seconds=600,
            )
            await repo.upsert(original)
            await session.commit()

        async with v3_factory() as session:
            repo = TriggerRuleRepository(session)
            stored = await repo.get("sanity-full")
            assert stored is not None
            assert stored.title == "理智满了"
            created_at = stored.created_at

        async with v3_factory() as session:
            repo = TriggerRuleRepository(session)
            # 整行覆盖：没写的列回到 NULL（覆盖层语义），created_at 保留。
            await repo.upsert(
                _rule("sanity-full", mode="auto", updated_at=utcnow())
            )
            await repo.upsert(_rule("aaa-user", origin="user", mode="off"))
            await session.commit()

        async with v3_factory() as session:
            repo = TriggerRuleRepository(session)
            assert [row.id for row in await repo.all()] == [
                "aaa-user",
                "sanity-full",
            ]
            stored = await repo.get("sanity-full")
            assert stored.title is None
            assert stored.worker is None
            assert stored.cooldown_seconds is None
            assert stored.mode == "auto"
            assert stored.created_at == created_at
            assert await repo.delete("sanity-full") is True
            assert await repo.delete("sanity-full") is False
            await session.commit()
            assert [row.id for row in await repo.all()] == ["aaa-user"]

    asyncio.run(scenario())


def test_observation_create_get_page_and_purge(v3_factory):
    async def scenario():
        now = utcnow()
        async with v3_factory() as session:
            triggers = TriggerRepository(session)
            observations = TriggerObservationRepository(session)
            trigger = await triggers.insert(_trigger())
            other = await triggers.insert(_trigger(thread_key="pipeline:2"))
            await session.flush()

            unreviewed = await observations.create(
                _observation(trigger.id, rule_id="sanity-full", created_at=now)
            )
            reviewed = await observations.create(
                _observation(
                    trigger.id,
                    rule_id="device-unavailable",
                    verdict="good",
                    reviewed_at=now - timedelta(hours=1),
                    created_at=now - timedelta(days=2),
                )
            )
            await observations.create(
                _observation(
                    other.id,
                    rule_id="sanity-full",
                    verdict="bad",
                    reviewed_at=now - timedelta(hours=2),
                    created_at=now - timedelta(days=200),
                )
            )
            await session.commit()

            assert (await observations.get(unreviewed.id)).rule_id == "sanity-full"
            assert (await observations.get(999)) is None
            assert (
                await observations.get_by_trigger(trigger.id)
            ).id == unreviewed.id
            assert await observations.get_by_trigger(999) is None

            all_page = await observations.page(size=10)
            assert all_page.total == 3
            assert [row.id for row in all_page.items] == [
                unreviewed.id,
                reviewed.id,
                3,
            ]
            assert (
                await observations.page(rule_id="sanity-full", size=10)
            ).total == 2
            assert (await observations.page(reviewed=True, size=10)).total == 2
            pending = await observations.page(reviewed=False, size=10)
            assert [row.id for row in pending.items] == [unreviewed.id]

            assert await observations.purge(now - timedelta(days=90)) == 1
            assert (await observations.page(size=10)).total == 2

    asyncio.run(scenario())


def test_observation_stats_since(v3_factory):
    async def scenario():
        now = utcnow()
        since = now - timedelta(days=7)
        async with v3_factory() as session:
            triggers = TriggerRepository(session)
            observations = TriggerObservationRepository(session)
            trigger = await triggers.insert(_trigger())
            await session.flush()

            # 规则 a：6 条已审阅 + 2 条未审阅；bad 在最旧的一条，不计入最近 5 条。
            for minute in range(1, 7):
                reviewed_at = now - timedelta(minutes=10 - minute)
                await observations.create(
                    _observation(
                        trigger.id,
                        rule_id="a",
                        verdict="bad" if minute == 1 else "good",
                        reviewed_at=reviewed_at,
                        created_at=since + timedelta(hours=1),
                    )
                )
            for minute in range(2):
                await observations.create(
                    _observation(
                        trigger.id,
                        rule_id="a",
                        created_at=since + timedelta(hours=2, minutes=minute),
                    )
                )
            # 规则 b：最近 5 条里 2 条 bad。
            for index in range(5):
                await observations.create(
                    _observation(
                        trigger.id,
                        rule_id="b",
                        verdict="bad" if index >= 3 else "good",
                        reviewed_at=now - timedelta(minutes=5 - index),
                        created_at=since + timedelta(hours=3, minutes=index),
                    )
                )
            # since 之前的老记录不参与统计。
            await observations.create(
                _observation(
                    trigger.id,
                    rule_id="c",
                    verdict="good",
                    reviewed_at=now - timedelta(minutes=1),
                    created_at=since - timedelta(days=1),
                )
            )
            await session.commit()

            stats = await observations.stats_since(since)
            assert stats == {
                "a": (8, 6, 5, 0),
                "b": (5, 5, 3, 2),
            }
            assert await observations.stats_since(now + timedelta(minutes=1)) == {}

    asyncio.run(scenario())
