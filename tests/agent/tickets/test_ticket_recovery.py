"""重启恢复把被中断运行所属的工单改成 ``blocked``（M69 规格 §4.5.6、§6）。

恢复发生在 ``ticket_service`` 装配之前，所以这里只断言库里的结果：工单状态、补写的
``forwarded=false`` 汇报，以及终态工单不受影响。异步用例一律「同步测试函数 +
``asyncio.run(scenario())``」，临时库用 ``tests/agent/tickets/conftest.py`` 的
``v3_factory``。
"""

from __future__ import annotations

import asyncio

from sqlalchemy import select

from maa_api.agent.runs import RunRecovery
from maa_api.agent.tickets.service import (
    BLOCKED_AFTER_RESTART_PROMPT,
    BLOCKED_AFTER_RESTART_SUMMARY,
    block_tickets_after_restart,
)
from maa_api.db.models import AgentRun, Ticket, TicketReport, new_uuid, utcnow
from maa_api.db.repositories.ticket import TicketRepository
from maa_api.domain.enums import AgentRunStatus, TicketOrigin, TicketStatus
from tests.fakes.v3_db import seed_run, seed_session

PREVIOUS_BOOT = "boot-previous"
CURRENT_BOOT = "boot-current"
_BLOCKED_HUMAN_ACTION = {
    "type": "decision",
    "prompt": BLOCKED_AFTER_RESTART_PROMPT,
    "ref_id": None,
}


async def _seed_ticket(
    factory, *, status: TicketStatus = TicketStatus.OPEN
) -> str:
    """写一条工单并提交，返回 id。"""
    now = utcnow()
    ticket = Ticket(
        id=new_uuid(),
        title="重启恢复用例",
        kind="daily_ops",
        status=status.value,
        origin=TicketOrigin.USER.value,
        priority=1,
        created_at=now,
        updated_at=now,
    )
    async with factory() as db:
        db.add(ticket)
        await db.commit()
    return str(ticket.id)


async def _ticket(factory, ticket_id: str) -> Ticket | None:
    async with factory() as db:
        return await TicketRepository(db).get(ticket_id)


async def _reports(factory, ticket_id: str) -> list[TicketReport]:
    async with factory() as db:
        result = await db.execute(
            select(TicketReport)
            .where(TicketReport.ticket_id == ticket_id)
            .order_by(TicketReport.id.asc())
        )
        return list(result.scalars().all())


def _assert_blocked_report(report: TicketReport) -> None:
    assert report.status == "blocked"
    assert report.summary == BLOCKED_AFTER_RESTART_SUMMARY
    assert report.human_action == _BLOCKED_HUMAN_ACTION
    assert report.forwarded is False
    assert report.run_id is None


def test_block_tickets_after_restart(v3_factory) -> None:
    """open、waiting 变 blocked 并各插一条汇报；done 不变、不插汇报。"""

    async def scenario() -> None:
        open_id = await _seed_ticket(v3_factory)
        waiting_id = await _seed_ticket(v3_factory, status=TicketStatus.WAITING)
        done_id = await _seed_ticket(v3_factory, status=TicketStatus.DONE)
        now = utcnow()

        async with v3_factory() as db:
            blocked = await block_tickets_after_restart(
                db, [open_id, waiting_id, done_id], now=now
            )
            await db.commit()
        assert blocked == [open_id, waiting_id]

        for ticket_id in blocked:
            ticket = await _ticket(v3_factory, ticket_id)
            assert ticket is not None
            assert ticket.status == TicketStatus.BLOCKED.value
            assert ticket.last_report_status == "blocked"
            assert ticket.summary == BLOCKED_AFTER_RESTART_SUMMARY
            assert ticket.human_action == _BLOCKED_HUMAN_ACTION
            reports = await _reports(v3_factory, ticket_id)
            assert len(reports) == 1
            _assert_blocked_report(reports[0])

        done_ticket = await _ticket(v3_factory, done_id)
        assert done_ticket is not None
        assert done_ticket.status == TicketStatus.DONE.value
        assert await _reports(v3_factory, done_id) == []

    asyncio.run(scenario())


def test_run_recovery_blocks_tickets_of_interrupted_runs(v3_factory) -> None:
    """旧 boot 的运行带 ticket_id：``recover()`` 后该工单 blocked，终态工单不变。"""

    async def scenario() -> None:
        open_id = await _seed_ticket(v3_factory)
        done_id = await _seed_ticket(v3_factory, status=TicketStatus.DONE)
        open_session = await seed_session(v3_factory, kind="worker")
        done_session = await seed_session(v3_factory, kind="worker")
        open_run = await seed_run(
            v3_factory,
            session_id=open_session,
            ticket_id=open_id,
            status=AgentRunStatus.RUNNING,
            boot_id=PREVIOUS_BOOT,
        )
        done_run = await seed_run(
            v3_factory,
            session_id=done_session,
            ticket_id=done_id,
            status=AgentRunStatus.WAITING_HUMAN,
            boot_id=PREVIOUS_BOOT,
        )

        recovered = await RunRecovery(
            v3_factory, boot_id=CURRENT_BOOT
        ).recover()
        assert set(recovered) == {open_run, done_run}

        async with v3_factory() as db:
            stored_open = await db.get(AgentRun, open_run, populate_existing=True)
            stored_done = await db.get(AgentRun, done_run, populate_existing=True)
        assert stored_open is not None
        assert stored_done is not None
        assert stored_open.status == AgentRunStatus.INTERRUPTED.value
        assert stored_done.status == AgentRunStatus.INTERRUPTED.value

        ticket = await _ticket(v3_factory, open_id)
        assert ticket is not None
        assert ticket.status == TicketStatus.BLOCKED.value
        assert ticket.summary == BLOCKED_AFTER_RESTART_SUMMARY
        reports = await _reports(v3_factory, open_id)
        assert len(reports) == 1
        _assert_blocked_report(reports[0])

        done_ticket = await _ticket(v3_factory, done_id)
        assert done_ticket is not None
        assert done_ticket.status == TicketStatus.DONE.value
        assert await _reports(v3_factory, done_id) == []

    asyncio.run(scenario())
