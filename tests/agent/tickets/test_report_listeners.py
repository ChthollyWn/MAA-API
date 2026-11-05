"""汇报监听器（M82 规格 §4.2、§6）。

覆盖 §6 的一条：监听器抛错时汇报照常落库并转发。监听器在提交之后各自起后台
任务，异常只记 warning，不回滚、不打断转发。
"""

from __future__ import annotations

import asyncio
import logging

from sqlalchemy import select

from maa_api.agent.tickets.inbox import InboxService
from maa_api.agent.tickets.reports import parse_report
from maa_api.agent.tickets.service import TicketService
from maa_api.agent.tickets.threads import ThreadRouter
from maa_api.db.models import AgentInbox, Ticket, TicketReport
from maa_api.db.repositories.ticket import TicketRepository
from maa_api.domain.enums import (
    EventSource,
    InboxKind,
    ReportStatus,
    TicketOrigin,
    TicketStatus,
)
from tests.fakes.v3_db import seed_session


class _Harness:
    """临时库上的收件箱、路由与真实工单服务。"""

    def __init__(self, factory, registry) -> None:
        self.factory = factory
        self.inbox = InboxService(factory)
        self.router = ThreadRouter(factory, self.inbox)
        self.events: list[tuple[str, dict]] = []
        self.service = TicketService(
            factory,
            inbox_service=self.inbox,
            thread_router=self.router,
            spec_registry=registry,
            broadcast=lambda kind, data: self.events.append((kind, data)),
        )


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


async def _inbox(factory, session_id: str, kind: InboxKind) -> list[AgentInbox]:
    async with factory() as db:
        result = await db.execute(
            select(AgentInbox).where(
                AgentInbox.session_id == session_id,
                AgentInbox.kind == str(kind),
            )
        )
        return list(result.scalars().all())


def test_listener_failure_does_not_break_report(
    v3_factory, spec_registry, caplog
) -> None:
    async def scenario() -> None:
        caplog.set_level(logging.WARNING)
        harness = _Harness(v3_factory, spec_registry)
        orchestrator = await seed_session(v3_factory, kind="orchestrator")
        ticket, _ = await harness.service.create(
            kind="daily_ops",
            title="日常",
            scope="把日常做了",
            origin=TicketOrigin.USER,
            source=EventSource.WEB,
            sender="web",
            orchestrator_session_id=orchestrator,
        )
        seen: list[tuple[str, str]] = []

        async def broken(ticket_obj, report_obj) -> None:
            seen.append((ticket_obj.id, report_obj.status.value))
            raise RuntimeError("监听器炸了")

        harness.service.add_report_listener(broken)
        outcome = await harness.service.record_report(
            ticket.id, parse_report({"status": "ready", "summary": "做完了"})
        )

        # 后台任务自己捕获异常；等它们跑完再断言。
        pending = [
            task for task in harness.service._memory_tasks if not task.done()
        ]
        if pending:
            await asyncio.gather(*pending)

        assert seen == [(ticket.id, ReportStatus.READY.value)]
        assert "汇报监听器失败" in caplog.text
        # 汇报照常落库、转发，与监听器成败无关。
        assert outcome.forwarded_inbox_id is not None
        stored = await _ticket(v3_factory, ticket.id)
        assert stored is not None
        assert stored.status == TicketStatus.DONE.value
        reports = await _reports(v3_factory, ticket.id)
        assert len(reports) == 1
        assert reports[0].status == ReportStatus.READY.value
        assert reports[0].forwarded is True
        items = await _inbox(v3_factory, orchestrator, InboxKind.REPORT)
        assert len(items) == 1
        assert items[0].ref_id == str(reports[0].id)

    asyncio.run(scenario())
