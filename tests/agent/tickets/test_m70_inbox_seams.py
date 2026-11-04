"""M70 T3：收件箱与话题接缝（监听、passive、deliver、汇报转发解析函数）。

异步用例沿用 M69 的写法：同步测试函数 + ``asyncio.run(scenario())``，临时库用
``tests/agent/tickets/conftest.py`` 的 ``v3_factory`` 与 ``spec_registry``。
"""

from __future__ import annotations

import asyncio

from sqlalchemy import select

from maa_api.agent.tickets.inbox import InboxService
from maa_api.agent.tickets.reports import parse_report
from maa_api.agent.tickets.service import TicketService
from maa_api.agent.tickets.threads import ThreadRouter
from maa_api.db.models import AgentInbox, AgentMessage, Ticket, TicketReport, new_uuid
from maa_api.domain.enums import EventSource, InboxKind, TicketOrigin
from tests.fakes.v3_db import seed_run, seed_session

_PIPELINE = "pipeline:0f8fad5b-d9cb-469f-a165-70867728950e"


async def _inbox(factory, session_id: str) -> list[AgentInbox]:
    async with factory() as db:
        result = await db.execute(
            select(AgentInbox)
            .where(AgentInbox.session_id == session_id)
            .order_by(AgentInbox.id.asc())
        )
        return list(result.scalars().all())


async def _messages(factory, session_id: str) -> list[AgentMessage]:
    async with factory() as db:
        result = await db.execute(
            select(AgentMessage)
            .where(AgentMessage.session_id == session_id)
            .order_by(AgentMessage.seq.asc())
        )
        return list(result.scalars().all())


async def _ticket(
    factory, *, status: str = "open", worker_session_id: str | None = None
) -> str:
    ticket_id = new_uuid()
    async with factory() as db:
        db.add(
            Ticket(
                id=ticket_id,
                title="接缝测试工单",
                kind="daily_ops",
                status=status,
                origin="user",
                worker_session_id=worker_session_id,
            )
        )
        await db.commit()
    return ticket_id


def _router(factory) -> ThreadRouter:
    return ThreadRouter(factory, InboxService(factory))


def test_listener_is_called_after_commit(v3_factory) -> None:
    async def scenario() -> None:
        session_id = await seed_session(v3_factory, kind="orchestrator")
        service = InboxService(v3_factory)
        observed: list[tuple[str, int, int]] = []
        tasks: list[asyncio.Task[None]] = []

        async def read_back(got_session_id: str) -> None:
            observed.append(
                (
                    got_session_id,
                    len(await _messages(v3_factory, got_session_id)),
                    len(await _inbox(v3_factory, got_session_id)),
                )
            )

        def listener(got_session_id: str) -> None:
            tasks.append(
                asyncio.get_running_loop().create_task(read_back(got_session_id))
            )

        service.add_listener(listener)
        item, message = await service.put_user_message(
            session_id,
            content="你好",
            thread_key="chat:orchestrator",
            payload={"text": "你好", "mentions": [], "source": "web", "sender": "web"},
        )
        await asyncio.gather(*tasks)

        # 监听者被调用时消息与收件箱项都已提交；用户消息不带 run_id / inbox_id。
        assert observed == [(session_id, 1, 1)]
        assert message.seq == 0
        assert message.run_id is None and message.inbox_id is None
        assert item.ref_id == str(message.seq)
        assert item.kind == InboxKind.USER_MESSAGE
        assert item.payload["message_id"] == message.id
        assert item.status == "pending"

    asyncio.run(scenario())


def test_passive_items_are_not_active_work_but_are_claimed(v3_factory) -> None:
    async def scenario() -> None:
        session_id = await seed_session(v3_factory)
        service = InboxService(v3_factory)
        await service.put(
            session_id,
            InboxKind.SYSTEM,
            payload={"text": "只是备注", "passive": True},
        )
        assert await service.has_active_pending(session_id) is False

        # passive 项不单独起一轮，但下一轮 claim 会连同它一起领走。
        run_id = await seed_run(v3_factory, session_id=session_id)
        claimed = await service.claim(session_id, run_id=run_id, limit=50)
        assert [item.kind for item in claimed] == ["system"]

        # 非 system 的项即便 payload 里写了 passive 也算活跃工作。
        await service.put(
            session_id, InboxKind.THREAD_EVENT, payload={"summary": "x", "passive": True}
        )
        assert await service.has_active_pending(session_id) is True

        await service.drop_pending(session_id)
        assert await service.has_active_pending(session_id) is False
        assert await service.count_pending(session_id) == 0

    asyncio.run(scenario())


def test_deliver_never_falls_back_to_orchestrator(v3_factory) -> None:
    async def scenario() -> None:
        orchestrator_id = await seed_session(v3_factory, kind="orchestrator")
        worker_id = await seed_session(v3_factory, kind="worker")
        ticket_id = await _ticket(v3_factory, worker_session_id=worker_id)
        router = _router(v3_factory)
        await router.subscribe(ticket_id, _PIPELINE)

        delivered = await router.deliver(
            _PIPELINE,
            kind=InboxKind.THREAD_EVENT,
            payload={"summary": "流水线完成"},
            ref_id=_PIPELINE,
        )
        assert delivered == [worker_id]
        assert len(await _inbox(v3_factory, worker_id)) == 1
        assert await _inbox(v3_factory, orchestrator_id) == []

        # 没有 active 订阅时返回空，不往总控写任何东西（publish 会回落，deliver 不会）。
        await router.unsubscribe(ticket_id, _PIPELINE)
        assert await router.deliver(_PIPELINE, payload={"summary": "迟到"}) == []
        assert len(await _inbox(v3_factory, worker_id)) == 1
        assert await _inbox(v3_factory, orchestrator_id) == []

    asyncio.run(scenario())


def test_report_forwarding_follows_the_resolver(v3_factory, spec_registry) -> None:
    async def scenario() -> None:
        old_orchestrator = await seed_session(v3_factory, kind="orchestrator")
        new_orchestrator = await seed_session(v3_factory, kind="orchestrator")
        inbox = InboxService(v3_factory)
        service = TicketService(
            v3_factory,
            inbox_service=inbox,
            thread_router=ThreadRouter(v3_factory, inbox),
            spec_registry=spec_registry,
        )
        ticket, _mandate = await service.create(
            kind="daily_ops",
            title="转发解析",
            scope="做日常",
            origin=TicketOrigin.USER,
            source=EventSource.WEB,
            sender="web",
            orchestrator_session_id=old_orchestrator,
        )

        async def resolver() -> str:
            return new_orchestrator

        service.bind_orchestrator_resolver(resolver)
        outcome = await service.record_report(
            ticket.id,
            parse_report({"status": "ready", "summary": "做完了"}),
        )

        # 工单上记的是旧会话，转发跟解析函数走，进新会话。
        assert outcome.forwarded_inbox_id is not None
        assert await _inbox(v3_factory, old_orchestrator) == []
        items = await _inbox(v3_factory, new_orchestrator)
        assert len(items) == 1
        assert items[0].kind == InboxKind.REPORT
        assert items[0].payload["status"] == "ready"
        assert items[0].payload["ticket_id"] == ticket.id

        # 启动补转也走同一个解析函数：直接造一条 forwarded=false 的待补转汇报，
        # 模拟上次进程在写库与转发之间重启。
        second, _mandate = await service.create(
            kind="daily_ops",
            title="补转解析",
            scope="做日常",
            origin=TicketOrigin.USER,
            source=EventSource.WEB,
            sender="web",
            orchestrator_session_id=old_orchestrator,
        )
        async with v3_factory() as db:
            db.add(
                TicketReport(
                    ticket_id=second.id,
                    status="ready",
                    summary="待补转",
                    forwarded=False,
                )
            )
            await db.commit()
        assert await service.forward_pending_reports() == 1
        assert len(await _inbox(v3_factory, new_orchestrator)) == 2
        assert await _inbox(v3_factory, old_orchestrator) == []

    asyncio.run(scenario())
