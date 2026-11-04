"""工单领域五个仓储与 ``get_active_orchestrator`` 的行为（M69 规格 §4.2、§6）。

用例形态：同步测试函数 + ``asyncio.run(scenario())``（本仓无 pytest-asyncio）；
临时库由 ``tests/fakes/v3_db.py`` 的 ``make_session_factory`` 建（与
``tests/db/conftest.py`` 同口径的 NullPool + ``create_all``）。

覆盖的硬约束：

- ``MandateRepository`` 是只追加的：没有 update / delete / set 方法，同一工单
  第二条委托被 ``uq_mandate_ticket_id`` 拒绝。
- ``TicketRepository.update_if_status`` 是条件更新：状态不在 ``allowed`` 里返回
  ``False`` 且不改行；仓储不 commit。
- ``list_unforwarded``：progress 与 forwarded=true 不返回，dropped 只在工单
  ``origin=user`` 时返回。
- 订阅停用后再 ``upsert_active`` 复用同一行并恢复 active、清空 ``ended_at``。
- ``AgentInboxRepository.mark_consumed`` 只算从 pending 改到的行，``purge`` 只删
  终态且 ``consumed_at`` 早于截止时间的项。
"""

import asyncio
from datetime import datetime, timedelta

import pytest
from sqlalchemy import func, select, update
from sqlalchemy.exc import IntegrityError

from maa_api.db.models import (
    AgentInbox,
    AgentRun,
    AgentSession,
    Mandate,
    Ticket,
    TicketReport,
    utcnow,
)
from maa_api.db.repositories.agent import AgentSessionRepository
from maa_api.db.repositories.ticket import (
    AgentInboxRepository,
    MandateRepository,
    ThreadSubscriptionRepository,
    TicketReportRepository,
    TicketRepository,
)
from tests.fakes.v3_db import make_session_factory, seed_run, seed_session


@pytest.fixture
def v3_factory(db_path):
    """临时库的会话工厂；用例结束 dispose 同步引擎（不依赖事件循环）。"""
    engine, factory = asyncio.run(make_session_factory(db_path))
    yield factory
    engine.sync_engine.dispose()


def _ticket(
    ticket_id: str,
    *,
    title: str = "工单",
    status: str = "open",
    kind: str = "daily_ops",
    origin: str = "user",
    updated_at: datetime | None = None,
) -> Ticket:
    return Ticket(
        id=ticket_id,
        title=title,
        status=status,
        kind=kind,
        origin=origin,
        updated_at=updated_at or utcnow(),
    )


def _mandate(ticket_id: str) -> Mandate:
    return Mandate(
        ticket_id=ticket_id,
        scope="跑日常",
        strength="explicit",
        source="web",
        risk_ceiling="consume",
    )


def _report(
    ticket_id: str,
    status: str = "ready",
    *,
    forwarded: bool = False,
    summary: str = "汇报",
    created_at: datetime | None = None,
) -> TicketReport:
    return TicketReport(
        ticket_id=ticket_id,
        status=status,
        summary=summary,
        forwarded=forwarded,
        created_at=created_at or utcnow(),
    )


def _inbox(
    session_id: str,
    *,
    status: str = "pending",
    created_at: datetime | None = None,
    consumed_at: datetime | None = None,
) -> AgentInbox:
    return AgentInbox(
        session_id=session_id,
        kind="user_message",
        payload={},
        status=status,
        created_at=created_at or utcnow(),
        consumed_at=consumed_at,
    )


# ---------------------------------------------------------------------------
# Mandate：只追加
# ---------------------------------------------------------------------------


def test_mandate_repository_is_append_only(v3_factory):
    method_names = {
        name
        for name in dir(MandateRepository)
        if not name.startswith("_") and callable(getattr(MandateRepository, name))
    }
    assert {"create", "get_by_ticket"} <= method_names
    assert [
        name
        for name in sorted(method_names)
        if name.startswith(("update", "delete", "set"))
    ] == []

    async def scenario():
        async with v3_factory() as db:
            await TicketRepository(db).create(_ticket("t-1"))
            await db.commit()

        async with v3_factory() as db:
            mandate = await MandateRepository(db).create(_mandate("t-1"))
            await db.commit()
            mandate_id = mandate.id

        async with v3_factory() as db:
            repo = MandateRepository(db)
            stored = await repo.get_by_ticket("t-1")
            assert stored is not None and stored.id == mandate_id
            assert await repo.get_by_ticket("t-missing") is None

        # 同一工单的委托唯一：第二次写入在 flush 时撞 uq_mandate_ticket_id。
        with pytest.raises(IntegrityError):
            async with v3_factory() as db:
                await MandateRepository(db).create(_mandate("t-1"))
                await db.commit()

    asyncio.run(scenario())


# ---------------------------------------------------------------------------
# Ticket：过滤、排序、条件更新、批量标题、活动运行
# ---------------------------------------------------------------------------


def test_ticket_list_filters_and_orders_by_updated_at(v3_factory):
    base = utcnow()
    tickets = [
        _ticket("t-1", status="open", kind="daily_ops", origin="user", updated_at=base),
        _ticket(
            "t-2",
            status="waiting",
            kind="daily_ops",
            origin="trigger",
            updated_at=base + timedelta(seconds=2),
        ),
        _ticket(
            "t-3",
            status="done",
            kind="researcher",
            origin="system",
            updated_at=base + timedelta(seconds=1),
        ),
        _ticket("t-4", status="blocked", kind="daily_ops", origin="user", updated_at=base),
    ]

    async def scenario():
        async with v3_factory() as db:
            repo = TicketRepository(db)
            for ticket in tickets:
                await repo.create(ticket)
            await db.commit()

        async with v3_factory() as db:
            repo = TicketRepository(db)

            page = await repo.list()
            assert [ticket.id for ticket in page.items] == ["t-2", "t-3", "t-4", "t-1"]
            assert (page.total, page.page, page.size) == (4, 1, 20)

            assert [
                ticket.id for ticket in (await repo.list(statuses={"open"})).items
            ] == ["t-1"]
            assert [
                ticket.id
                for ticket in (await repo.list(statuses={"open", "waiting"})).items
            ] == ["t-2", "t-1"]
            assert [
                ticket.id for ticket in (await repo.list(kind="daily_ops")).items
            ] == ["t-2", "t-4", "t-1"]
            assert [
                ticket.id for ticket in (await repo.list(origin="user")).items
            ] == ["t-4", "t-1"]
            assert [
                ticket.id
                for ticket in (
                    await repo.list(
                        statuses={"open", "blocked"}, kind="daily_ops", origin="user"
                    )
                ).items
            ] == ["t-4", "t-1"]

            second = await repo.list(page=2, size=2)
            assert [ticket.id for ticket in second.items] == ["t-4", "t-1"]
            assert (second.total, second.page, second.size) == (4, 2, 2)

    asyncio.run(scenario())


def test_update_if_status_is_conditional(v3_factory):
    now = utcnow()

    async def scenario():
        async with v3_factory() as db:
            await TicketRepository(db).create(_ticket("t-1"))
            await db.commit()

        # 仓储不 commit：条件更新成功但回滚后，另一个会话仍看到 open。
        async with v3_factory() as db:
            repo = TicketRepository(db)
            assert (
                await repo.update_if_status(
                    "t-1",
                    allowed={"open"},
                    values={"status": "waiting", "summary": "等人工", "updated_at": now},
                )
                is True
            )
            await db.rollback()

        async with v3_factory() as db:
            repo = TicketRepository(db)
            assert (await repo.get("t-1")).status == "open"
            assert (
                await repo.update_if_status(
                    "t-1",
                    allowed={"open"},
                    values={"status": "waiting", "summary": "等人工", "updated_at": now},
                )
                is True
            )
            await db.commit()

        async with v3_factory() as db:
            repo = TicketRepository(db)
            # 状态已不在 allowed：返回 False 且不覆盖 waiting。
            assert (
                await repo.update_if_status(
                    "t-1", allowed={"open"}, values={"status": "done"}
                )
                is False
            )
            assert (
                await repo.update_if_status(
                    "t-missing", allowed={"waiting"}, values={"status": "done"}
                )
                is False
            )
            assert (
                await repo.update_if_status(
                    "t-1", allowed=(), values={"status": "done"}
                )
                is False
            )
            ticket = await repo.get("t-1")
            assert ticket.status == "waiting"
            assert ticket.summary == "等人工"

    asyncio.run(scenario())


def test_titles_and_worker_turn_running(v3_factory):
    async def scenario():
        async with v3_factory() as db:
            repo = TicketRepository(db)
            await repo.create(_ticket("t-1", title="日常"))
            await repo.create(_ticket("t-2", title="剿灭"))
            await db.commit()

        running_session = await seed_session(v3_factory)
        await seed_run(v3_factory, session_id=running_session, status="running")
        waiting_session = await seed_session(v3_factory)
        await seed_run(v3_factory, session_id=waiting_session, status="waiting_human")
        finished_session = await seed_session(v3_factory)
        await seed_run(v3_factory, session_id=finished_session, status="succeeded")

        async with v3_factory() as db:
            repo = TicketRepository(db)
            assert await repo.titles(["t-1", "t-2", "t-missing"]) == {
                "t-1": "日常",
                "t-2": "剿灭",
            }
            assert await repo.titles([]) == {}
            assert await repo.worker_turn_running(None) is False
            assert await repo.worker_turn_running("session-missing") is False
            assert await repo.worker_turn_running(running_session) is True
            assert await repo.worker_turn_running(waiting_session) is True
            assert await repo.worker_turn_running(finished_session) is False

    asyncio.run(scenario())


# ---------------------------------------------------------------------------
# ThreadSubscription：幂等 upsert、停用与按话题反查
# ---------------------------------------------------------------------------


def test_subscription_upsert_reactivates(v3_factory):
    now = utcnow()

    async def scenario():
        async with v3_factory() as db:
            await TicketRepository(db).create(_ticket("t-1"))
            await db.commit()

        async with v3_factory() as db:
            subscription = await ThreadSubscriptionRepository(db).upsert_active(
                "t-1", "device:emu", now=now
            )
            await db.commit()
            subscription_id = subscription.id

        async with v3_factory() as db:
            repo = ThreadSubscriptionRepository(db)
            assert (
                await repo.deactivate(
                    "t-1", "device:emu", now=now + timedelta(seconds=1)
                )
                is True
            )
            # 已经停用：再次 deactivate 不改行。
            assert (
                await repo.deactivate(
                    "t-1", "device:emu", now=now + timedelta(seconds=2)
                )
                is False
            )
            await db.commit()
            rows = await repo.list_for_ticket("t-1")
            assert len(rows) == 1
            assert rows[0].active is False
            assert rows[0].ended_at is not None

        async with v3_factory() as db:
            repo = ThreadSubscriptionRepository(db)
            revived = await repo.upsert_active(
                "t-1", "device:emu", now=now + timedelta(seconds=3)
            )
            await db.commit()
            assert revived.id == subscription_id
            assert revived.active is True
            assert revived.ended_at is None
            # 复用同一行，不新增订阅。
            assert len(await repo.list_for_ticket("t-1")) == 1

    asyncio.run(scenario())


def test_subscription_active_ticket_ids_and_deactivate_for_ticket(v3_factory):
    now = utcnow()

    async def scenario():
        async with v3_factory() as db:
            repo = TicketRepository(db)
            for ticket_id in ("t-1", "t-2", "t-3"):
                await repo.create(_ticket(ticket_id))
            await db.commit()

        # 订阅顺序 t-3、t-1、t-2：active_ticket_ids 按订阅 id 升序返回。
        async with v3_factory() as db:
            repo = ThreadSubscriptionRepository(db)
            for ticket_id in ("t-3", "t-1", "t-2"):
                await repo.upsert_active(ticket_id, "device:emu", now=now)
            await repo.upsert_active("t-2", "core:maa", now=now)
            await db.commit()

        async with v3_factory() as db:
            repo = ThreadSubscriptionRepository(db)
            assert await repo.active_ticket_ids("device:emu") == ["t-3", "t-1", "t-2"]
            assert await repo.active_ticket_ids("core:maa") == ["t-2"]
            assert await repo.active_ticket_ids("schedule:missing") == []
            assert (
                await repo.deactivate(
                    "t-1", "device:emu", now=now + timedelta(seconds=1)
                )
                is True
            )
            await db.commit()
            assert await repo.active_ticket_ids("device:emu") == ["t-3", "t-2"]

        async with v3_factory() as db:
            repo = ThreadSubscriptionRepository(db)
            assert (
                await repo.deactivate_for_ticket(
                    "t-3", now=now + timedelta(seconds=2)
                )
                == 1
            )
            assert await repo.deactivate_for_ticket("t-3", now=now) == 0
            await db.commit()
            assert await repo.active_ticket_ids("device:emu") == ["t-2"]

    asyncio.run(scenario())


# ---------------------------------------------------------------------------
# AgentInbox：领取、丢弃与保留期清理
# ---------------------------------------------------------------------------


def test_inbox_mark_consumed_skips_non_pending(v3_factory):
    now = utcnow()

    async def scenario():
        session_id = await seed_session(v3_factory)
        run_id = await seed_run(v3_factory, session_id=session_id)
        second_run_id = await seed_run(
            v3_factory, session_id=session_id, status="succeeded"
        )

        async with v3_factory() as db:
            repo = AgentInboxRepository(db)
            items = [await repo.create(_inbox(session_id)) for _ in range(4)]
            await db.commit()
            item_ids = [int(item.id) for item in items]

        async with v3_factory() as db:
            repo = AgentInboxRepository(db)
            assert await repo.pending_ids(session_id, limit=2) == item_ids[:2]
            assert await repo.count_pending(session_id) == 4

            assert (
                await repo.mark_consumed(item_ids[:2], run_id=run_id, now=now) == 2
            )
            # 两个 id 已经 consumed，不会被再次标记。
            assert (
                await repo.mark_consumed(item_ids[:2], run_id=run_id, now=now) == 0
            )
            # 混入已消费的 id 时只算真正从 pending 改到的 ids[2]（换一个运行）。
            assert (
                await repo.mark_consumed(
                    [item_ids[0], item_ids[2]], run_id=second_run_id, now=now
                )
                == 1
            )
            await db.commit()

            first_claim = await repo.list_consumed_by(item_ids, run_id=run_id)
            assert [int(item.id) for item in first_claim] == [item_ids[0], item_ids[1]]
            second_claim = await repo.list_consumed_by(item_ids, run_id=second_run_id)
            assert [int(item.id) for item in second_claim] == [item_ids[2]]
            assert all(item.status == "consumed" for item in first_claim + second_claim)
            assert all(item.consumed_run_id == run_id for item in first_claim)
            assert all(item.consumed_at is not None for item in first_claim + second_claim)

        async with v3_factory() as db:
            repo = AgentInboxRepository(db)
            assert await repo.count_pending(session_id) == 1
            assert await repo.drop_pending(session_id, now=now) == 1
            assert await repo.drop_pending(session_id, now=now) == 0
            assert await repo.count_pending(session_id) == 0
            await db.commit()

    asyncio.run(scenario())


def test_inbox_purge_finished_before(v3_factory):
    now = utcnow()
    cutoff = now - timedelta(days=30)
    old = now - timedelta(days=31)
    fresh = now - timedelta(days=29)

    async def scenario():
        session_id = await seed_session(v3_factory)
        async with v3_factory() as db:
            repo = AgentInboxRepository(db)
            for item in (
                _inbox(session_id, status="consumed", created_at=old, consumed_at=old),
                _inbox(session_id, status="dropped", created_at=old, consumed_at=old),
                _inbox(session_id, status="consumed", created_at=fresh, consumed_at=fresh),
                _inbox(session_id, status="pending", created_at=old),
            ):
                await repo.create(item)
            await db.commit()

        async with v3_factory() as db:
            assert await AgentInboxRepository(db).purge_finished_before(cutoff) == 2
            await db.commit()

        async with v3_factory() as db:
            remaining = (
                await db.execute(select(func.count()).select_from(AgentInbox))
            ).scalar_one()
            # 29 天前消费的与 31 天前仍 pending 的都保留。
            assert int(remaining) == 2
            assert await AgentInboxRepository(db).count_pending(session_id) == 1

    asyncio.run(scenario())


# ---------------------------------------------------------------------------
# TicketReport：分页、最近汇报与补转规则
# ---------------------------------------------------------------------------


def test_ticket_report_recent_and_list_pages(v3_factory):
    base = utcnow()

    async def scenario():
        async with v3_factory() as db:
            tickets = TicketRepository(db)
            await tickets.create(_ticket("t-1"))
            await tickets.create(_ticket("t-2"))
            reports = TicketReportRepository(db)
            first = await reports.create(
                _report("t-1", created_at=base + timedelta(seconds=1))
            )
            second = await reports.create(
                _report("t-1", created_at=base + timedelta(seconds=2))
            )
            third = await reports.create(_report("t-1", created_at=base))
            await reports.create(_report("t-2", created_at=base + timedelta(seconds=3)))
            await db.commit()
            report_ids = (int(first.id), int(second.id), int(third.id))

        async with v3_factory() as db:
            repo = TicketReportRepository(db)
            # recent 按 id DESC；list 按 created_at DESC, id DESC。
            assert [
                int(report.id) for report in await repo.recent("t-1", limit=2)
            ] == [report_ids[2], report_ids[1]]
            first_page = await repo.list("t-1", page=1, size=2)
            assert [int(report.id) for report in first_page.items] == [
                report_ids[1],
                report_ids[0],
            ]
            assert (first_page.total, first_page.page, first_page.size) == (3, 1, 2)
            second_page = await repo.list("t-1", page=2, size=2)
            assert [int(report.id) for report in second_page.items] == [report_ids[2]]

    asyncio.run(scenario())


def test_unforwarded_reports_follow_forwarding_rules(v3_factory):
    async def scenario():
        async with v3_factory() as db:
            tickets = TicketRepository(db)
            for ticket_id, origin in (
                ("t-user", "user"),
                ("t-trigger", "trigger"),
                ("t-sys", "system"),
            ):
                await tickets.create(_ticket(ticket_id, origin=origin))
            reports = TicketReportRepository(db)
            await reports.create(_report("t-user", "progress"))
            waiting = await reports.create(_report("t-trigger", "waiting"))
            dropped_user = await reports.create(_report("t-user", "dropped"))
            await reports.create(_report("t-trigger", "dropped"))
            await reports.create(_report("t-user", "ready", forwarded=True))
            blocked = await reports.create(_report("t-sys", "blocked"))
            await db.commit()
            expected_ids = [
                int(waiting.id),
                int(dropped_user.id),
                int(blocked.id),
            ]

        async with v3_factory() as db:
            repo = TicketReportRepository(db)
            assert [int(report.id) for report in await repo.list_unforwarded()] == (
                expected_ids
            )
            assert await repo.mark_forwarded(expected_ids[0]) is True
            assert await repo.mark_forwarded(999_999) is False
            await db.commit()
            assert [
                int(report.id) for report in await repo.list_unforwarded(limit=1)
            ] == [expected_ids[1]]
            assert [
                int(report.id) for report in await repo.list_unforwarded(limit=10)
            ] == expected_ids[1:]

    asyncio.run(scenario())


# ---------------------------------------------------------------------------
# AgentSession.get_active_orchestrator 与 v3_db 建库工具
# ---------------------------------------------------------------------------


def test_get_active_orchestrator_ignores_archived(v3_factory):
    async def scenario():
        active_id = await seed_session(v3_factory, kind="orchestrator")
        await seed_session(
            v3_factory, kind="orchestrator", status="active", archived=True
        )
        await seed_session(v3_factory, kind="orchestrator", status="finished")
        await seed_session(v3_factory, kind="worker")
        older_active_id = await seed_session(v3_factory, kind="orchestrator")

        async with v3_factory() as db:
            await db.execute(
                update(AgentSession)
                .where(AgentSession.id == older_active_id)
                .values(created_at=utcnow() - timedelta(days=1))
            )
            await db.commit()

        async with v3_factory() as db:
            found = await AgentSessionRepository(db).get_active_orchestrator()
            assert found is not None
            assert found.id == active_id

    asyncio.run(scenario())


def test_v3_db_seed_helpers(tmp_path):
    async def scenario():
        engine, factory = await make_session_factory(tmp_path / "seed.db")
        try:
            session_id = await seed_session(factory, kind="orchestrator")
            archived_id = await seed_session(factory, archived=True)
            run_id = await seed_run(factory, session_id=session_id)

            async with factory() as db:
                session = await db.get(AgentSession, session_id)
                archived = await db.get(AgentSession, archived_id)
                run = await db.get(AgentRun, run_id)
                assert session is not None
                assert session.kind == "orchestrator"
                assert session.status == "active"
                assert session.archived_at is None
                assert archived is not None and archived.archived_at is not None
                assert run is not None
                assert run.root_run_id == run_id
                assert run.agent_name == "test"
                assert run.kind == "worker_turn"
                assert run.status == "running"
                assert run.boot_id == "boot-test"
                assert run.started_at is not None
        finally:
            engine.sync_engine.dispose()

    asyncio.run(scenario())
