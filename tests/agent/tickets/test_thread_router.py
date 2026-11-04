"""``ThreadRouter`` 的订阅、投递与降级（M69 规格 §4.5.4、§6）。"""

from __future__ import annotations

import asyncio

import pytest
from sqlalchemy import func, select

from maa_api.agent.tickets.inbox import InboxService
from maa_api.agent.tickets.threads import ThreadRouter
from maa_api.db.models import AgentInbox, Ticket, new_uuid
from maa_api.domain.enums import InboxKind
from maa_api.domain.errors import AppError, ErrorCode
from tests.fakes.v3_db import seed_session

_UUID = "0f8fad5b-d9cb-469f-a165-70867728950e"
_PIPELINE = f"pipeline:{_UUID}"


async def _worker(factory) -> str:
    return await seed_session(factory, kind="worker")


async def _ticket(
    factory, *, status: str = "open", worker_session_id: str | None = None
) -> str:
    ticket_id = new_uuid()
    async with factory() as db:
        db.add(
            Ticket(
                id=ticket_id,
                title="路由测试工单",
                kind="daily_ops",
                status=status,
                origin="user",
                worker_session_id=worker_session_id,
            )
        )
        await db.commit()
    return ticket_id


async def _inbox(factory, session_id: str) -> list[AgentInbox]:
    async with factory() as db:
        result = await db.execute(
            select(AgentInbox)
            .where(AgentInbox.session_id == session_id)
            .order_by(AgentInbox.id.asc())
        )
        return list(result.scalars().all())


async def _inbox_total(factory) -> int:
    async with factory() as db:
        result = await db.execute(select(func.count()).select_from(AgentInbox))
        return int(result.scalar_one())


def _router(factory) -> ThreadRouter:
    return ThreadRouter(factory, InboxService(factory))


def test_publish_routes_to_subscribed_workers(v3_factory) -> None:
    async def scenario() -> None:
        first_worker = await _worker(v3_factory)
        first_ticket = await _ticket(v3_factory, worker_session_id=first_worker)
        second_worker = await _worker(v3_factory)
        second_ticket = await _ticket(v3_factory, worker_session_id=second_worker)

        router = _router(v3_factory)
        await router.subscribe(first_ticket, _PIPELINE)
        await router.subscribe(second_ticket, _PIPELINE)

        result = await router.publish(
            _PIPELINE, ref_id="evt-1", payload={"event": "completed"}
        )
        assert result.routed_to == "ticket"
        # ticket_ids 与订阅顺序（订阅 id 升序）一致。
        assert result.ticket_ids == (first_ticket, second_ticket)
        assert len(result.inbox_ids) == 2

        for session_id in (first_worker, second_worker):
            items = await _inbox(v3_factory, session_id)
            assert len(items) == 1
            item = items[0]
            assert item.id in result.inbox_ids
            assert item.kind == "thread_event"
            assert item.thread_key == _PIPELINE
            assert item.ref_id == "evt-1"
            assert item.payload == {"event": "completed"}
            assert item.status == "pending"

    asyncio.run(scenario())


def test_publish_without_subscription_goes_to_orchestrator(v3_factory) -> None:
    async def scenario() -> None:
        router = _router(v3_factory)
        # 没有总控会话：routed_to=none 且不写库。
        result = await router.publish("core:default", payload={"event": "x"})
        assert result.routed_to == "none"
        assert result.inbox_ids == ()
        assert result.ticket_ids == ()
        assert await _inbox_total(v3_factory) == 0

        orchestrator_id = await seed_session(v3_factory, kind="orchestrator")
        result = await router.publish("core:default", payload={"event": "x"})
        assert result.routed_to == "orchestrator"
        assert result.ticket_ids == ()
        items = await _inbox(v3_factory, orchestrator_id)
        assert [item.id for item in items] == list(result.inbox_ids)
        assert items[0].kind == "thread_event"

    asyncio.run(scenario())


def test_chat_thread_goes_to_orchestrator(v3_factory) -> None:
    async def scenario() -> None:
        orchestrator_id = await seed_session(v3_factory, kind="orchestrator")
        router = _router(v3_factory)
        result = await router.publish("chat:orchestrator", payload={"text": "hi"})
        assert result.routed_to == "orchestrator"
        assert result.ticket_ids == ()
        items = await _inbox(v3_factory, orchestrator_id)
        assert len(items) == 1
        assert items[0].payload == {"text": "hi"}

    asyncio.run(scenario())


def test_ticket_thread_routes_to_its_worker_directly(v3_factory) -> None:
    async def scenario() -> None:
        worker_id = await _worker(v3_factory)
        ticket_id = await _ticket(v3_factory, worker_session_id=worker_id)
        router = _router(v3_factory)

        result = await router.publish(
            f"ticket:{ticket_id}", payload={"event": "manual"}
        )
        assert result.routed_to == "ticket"
        assert result.ticket_ids == (ticket_id,)
        assert len(result.inbox_ids) == 1
        items = await _inbox(v3_factory, worker_id)
        assert [item.id for item in items] == list(result.inbox_ids)

    asyncio.run(scenario())


def test_ended_ticket_receives_nothing(v3_factory) -> None:
    async def scenario() -> None:
        worker_id = await _worker(v3_factory)
        ended_ticket = await _ticket(
            v3_factory, status="done", worker_session_id=worker_id
        )
        orchestrator_id = await seed_session(v3_factory, kind="orchestrator")
        router = _router(v3_factory)

        # 工单自己的话题：工单已结束就交总控，不再投给 Worker。
        result = await router.publish(f"ticket:{ended_ticket}", payload={"event": "x"})
        assert result.routed_to == "orchestrator"
        assert result.ticket_ids == ()
        assert await _inbox(v3_factory, worker_id) == []
        assert len(await _inbox(v3_factory, orchestrator_id)) == 1

        # 订阅仍在活动、但工单已经结束：publish 还要按工单状态再过滤一次。
        second_worker = await _worker(v3_factory)
        second_ticket = await _ticket(v3_factory, worker_session_id=second_worker)
        await router.subscribe(second_ticket, _PIPELINE)
        async with v3_factory() as db:
            ticket = await db.get(Ticket, second_ticket)
            assert ticket is not None
            ticket.status = "cancelled"
            await db.commit()
        result = await router.publish(_PIPELINE, payload={"event": "x"})
        assert result.routed_to == "orchestrator"
        assert result.ticket_ids == ()
        assert await _inbox(v3_factory, second_worker) == []
        assert len(await _inbox(v3_factory, orchestrator_id)) == 2

    asyncio.run(scenario())


def test_unsubscribe_stops_routing(v3_factory) -> None:
    async def scenario() -> None:
        first_worker = await _worker(v3_factory)
        first_ticket = await _ticket(v3_factory, worker_session_id=first_worker)
        second_worker = await _worker(v3_factory)
        second_ticket = await _ticket(v3_factory, worker_session_id=second_worker)
        router = _router(v3_factory)
        await router.subscribe(first_ticket, _PIPELINE)
        await router.subscribe(second_ticket, _PIPELINE)

        assert await router.unsubscribe(first_ticket, _PIPELINE) is True
        assert await router.unsubscribe(first_ticket, _PIPELINE) is False
        result = await router.publish(_PIPELINE)
        assert result.ticket_ids == (second_ticket,)
        assert await _inbox(v3_factory, first_worker) == []
        assert len(await _inbox(v3_factory, second_worker)) == 1

    asyncio.run(scenario())


def test_subscribe_validation(v3_factory) -> None:
    async def scenario() -> None:
        open_ticket = await _ticket(v3_factory)
        ended_ticket = await _ticket(v3_factory, status="done")
        missing_ticket = new_uuid()
        router = _router(v3_factory)

        for thread_key in ("foo:1", f"ticket:{open_ticket}", "chat:orchestrator"):
            with pytest.raises(AppError) as exc_info:
                await router.subscribe(open_ticket, thread_key)
            assert exc_info.value.code is ErrorCode.INVALID_PARAMETER, thread_key

        with pytest.raises(AppError) as exc_info:
            await router.subscribe(ended_ticket, "core:default")
        assert exc_info.value.code is ErrorCode.TICKET_ENDED

        with pytest.raises(AppError) as exc_info:
            await router.subscribe(missing_ticket, "core:default")
        assert exc_info.value.code is ErrorCode.TICKET_NOT_FOUND

    asyncio.run(scenario())


def test_to_orchestrator_falls_back_from_archived_preferred(v3_factory) -> None:
    async def scenario() -> None:
        router = _router(v3_factory)
        # 没有总控会话时返回 None，不写库。
        assert (
            await router.to_orchestrator(
                kind=InboxKind.REPORT,
                thread_key=f"ticket:{_UUID}",
                ref_id="1",
                payload={"status": "ready"},
            )
            is None
        )
        assert await _inbox_total(v3_factory) == 0

        active_id = await seed_session(v3_factory, kind="orchestrator")
        archived_id = await seed_session(
            v3_factory, kind="orchestrator", archived=True
        )
        item_id = await router.to_orchestrator(
            kind=InboxKind.REPORT,
            thread_key=f"ticket:{_UUID}",
            ref_id="1",
            payload={"status": "ready"},
            preferred_session_id=archived_id,
        )
        assert item_id is not None
        items = await _inbox(v3_factory, active_id)
        assert [item.id for item in items] == [item_id]
        assert items[0].kind == "report"
        assert items[0].ref_id == "1"
        assert items[0].thread_key == f"ticket:{_UUID}"

        # preferred 是活动总控时直接用它。
        preferred_id = await seed_session(v3_factory, kind="orchestrator")
        preferred_item_id = await router.to_orchestrator(
            kind=InboxKind.SYSTEM,
            thread_key=None,
            ref_id=None,
            payload={"event": "boot"},
            preferred_session_id=preferred_id,
        )
        assert preferred_item_id is not None
        preferred_items = await _inbox(v3_factory, preferred_id)
        assert [item.id for item in preferred_items] == [preferred_item_id]

    asyncio.run(scenario())
