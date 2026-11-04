"""M71 T7：确认与提问的冒泡（规格 §4.5.6、§6 的三条）。

异步用例一律「同步测试函数 + ``asyncio.run(scenario())``」。租约服务与两个预置工单
来自 ``lease_env``；冒泡另建一个 UUID 工单（话题键 ``ticket:<uuid>`` 只接受 UUID）
与一个活动总控会话，这样 ``bubble_human_request`` 能真的写总控收件箱。
"""

from __future__ import annotations

import asyncio
from types import SimpleNamespace

from sqlalchemy import select

from maa_api.agent.origin import bubble_human_request, settle_human_request
from maa_api.agent.registry import ToolOrigin
from maa_api.agent.tickets.inbox import InboxService
from maa_api.agent.tickets.service import TicketService
from maa_api.agent.tickets.threads import ThreadRouter
from maa_api.db.models import AgentInbox, AgentSession, Ticket
from maa_api.db.repositories.ticket import TicketRepository
from maa_api.domain.enums import InboxKind, TicketStatus
from tests.agent.lease.conftest import RUN_A


async def _state(factory, lease_service):
    """真实 TicketService / ThreadRouter / InboxService、一个活动总控与一个 UUID 工单。"""
    inbox = InboxService(factory)
    router = ThreadRouter(factory, inbox)
    service = TicketService(
        factory,
        inbox_service=inbox,
        thread_router=router,
        spec_registry=SimpleNamespace(
            get=lambda _name: SimpleNamespace(kind="worker", risk_ceiling="consume")
        ),
    )
    async with factory() as db:
        orchestrator = AgentSession(
            model="test-model", kind="orchestrator", status="active"
        )
        db.add(orchestrator)
        await db.commit()
        orchestrator_id = orchestrator.id
        ticket = Ticket(title="日常", kind="general", origin="user")
        db.add(ticket)
        await db.commit()
        ticket_id = str(ticket.id)
    state = SimpleNamespace(
        ticket_service=service,
        thread_router=router,
        device_lease_service=lease_service,
    )
    return state, ticket_id, orchestrator_id


async def _ticket(factory, ticket_id: str):
    async with factory() as db:
        return await TicketRepository(db).get(ticket_id)


async def _inbox(factory, session_id: str) -> list[AgentInbox]:
    async with factory() as db:
        result = await db.execute(
            select(AgentInbox)
            .where(AgentInbox.session_id == session_id)
            .order_by(AgentInbox.id)
        )
        return list(result.scalars().all())


def test_bubble_sets_ticket_waiting_and_notifies_orchestrator(lease_env) -> None:
    factory, lease_service, _events, _published = lease_env

    async def scenario() -> None:
        state, ticket_id, orchestrator_id = await _state(factory, lease_service)
        origin = ToolOrigin(run_id=RUN_A, ticket_id=ticket_id, agent="daily_ops")
        await bubble_human_request(
            state,
            origin,
            root_run_id=RUN_A,
            kind="confirmation",
            ref_id="conf-1",
            prompt="该操作需要人工确认",
        )

        ticket = await _ticket(factory, ticket_id)
        assert ticket is not None
        assert ticket.status == TicketStatus.WAITING.value
        assert ticket.human_action == {
            "type": "confirmation",
            "prompt": "该操作需要人工确认",
            "ref_id": "conf-1",
        }

        items = await _inbox(factory, orchestrator_id)
        assert len(items) == 1
        item = items[0]
        assert item.kind == InboxKind.SYSTEM.value
        assert item.thread_key == f"ticket:{ticket_id}"
        assert item.ref_id == "conf-1"
        assert item.payload == {
            "event": "human_request",
            "type": "confirmation",
            "ticket_id": ticket_id,
            "ticket_title": "日常",
            "agent": "daily_ops",
            "ref_id": "conf-1",
            "prompt": "该操作需要人工确认",
        }

    asyncio.run(scenario())


def test_settle_returns_ticket_to_open(lease_env) -> None:
    factory, lease_service, _events, _published = lease_env

    async def scenario() -> None:
        state, ticket_id, _orchestrator_id = await _state(factory, lease_service)
        origin = ToolOrigin(run_id=RUN_A, ticket_id=ticket_id, agent="daily_ops")
        await bubble_human_request(
            state,
            origin,
            root_run_id=RUN_A,
            kind="question",
            ref_id="question-1",
            prompt="用哪个客户端？",
        )
        ticket = await _ticket(factory, ticket_id)
        assert ticket is not None and ticket.status == TicketStatus.WAITING.value

        # ref_id 不同：不是这件事的终态，工单保持 waiting。
        await settle_human_request(state, origin, ref_id="question-other")
        ticket = await _ticket(factory, ticket_id)
        assert ticket is not None and ticket.status == TicketStatus.WAITING.value

        await settle_human_request(state, origin, ref_id="question-1")
        ticket = await _ticket(factory, ticket_id)
        assert ticket is not None
        assert ticket.status == TicketStatus.OPEN.value
        assert ticket.human_action is None

    asyncio.run(scenario())


def test_bubble_releases_implicit_lease(lease_env, lease_world) -> None:
    factory, lease_service, _events, _published = lease_env
    holder = lease_world.holders["a"]

    async def scenario() -> None:
        state, ticket_id, _orchestrator_id = await _state(factory, lease_service)
        origin = ToolOrigin(run_id=RUN_A, ticket_id=ticket_id, agent="daily_ops")
        # 隐式租约：等人时让出，否则整台设备会跟着空转（09 §3.2）。
        await lease_service.acquire(holder, purpose="click")
        assert await lease_service.current() is not None
        await bubble_human_request(
            state,
            origin,
            root_run_id=RUN_A,
            kind="confirmation",
            ref_id="conf-implicit",
            prompt="确认一下",
        )
        assert await lease_service.current() is None

        # 显式租约是代理主动占用的，等人时保留。
        await lease_service.acquire(holder, purpose="hold", explicit=True)
        await bubble_human_request(
            state,
            origin,
            root_run_id=RUN_A,
            kind="confirmation",
            ref_id="conf-explicit",
            prompt="确认一下",
        )
        row = await lease_service.current()
        assert row is not None
        assert row.explicit is True
        assert row.purpose == "hold"
        await lease_service.release(holder)

    asyncio.run(scenario())
