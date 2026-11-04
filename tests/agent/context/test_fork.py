"""M80 T7：工单分叉（规格 §4.5.8、§6 的五条用例）。

每个用例在临时库上建一张源工单（含委托与 Worker 会话）与一段历史，再调
``RewindService.fork_ticket``，从库里核对新工单、委托、会话两列、复制过来的消息、
检查点与末尾提示。分叉不写 Worker 收件箱、不复制订阅/租约/计划/产物，也不用
``TicketService.create``（那会把推断委托推翻成显式委托）。
"""

from __future__ import annotations

import asyncio
import json
from datetime import datetime, timedelta

from sqlalchemy import func, select

from maa_api.agent.context.rewind import RewindService
from maa_api.agent.tools import build_registry
from maa_api.api.routers.tickets import forked_from_of
from maa_api.db.models import (
    AgentInbox,
    AgentMessage,
    AgentSession,
    CompactionCheckpoint,
    Mandate,
    Ticket,
)
from maa_api.domain.enums import TicketStatus
from maa_api.domain.errors import AppError, ErrorCode
from tests.fakes.v3_db import make_session_factory

BASE = datetime(2026, 11, 5, 0, 0, 0)


class _Tickets:
    """只实现分叉用到的 ``get``；不存在时按生产口径抛 404。"""

    def __init__(self, factory) -> None:
        self._factory = factory

    async def get(self, ticket_id: str) -> Ticket:
        async with self._factory() as db:
            ticket = await db.get(Ticket, ticket_id)
        if ticket is None:
            raise AppError(
                ErrorCode.TICKET_NOT_FOUND, "没有这个工单", {"ticket_id": ticket_id}
            )
        return ticket


class _Broadcast:
    def __init__(self) -> None:
        self.events: list[tuple[str, dict]] = []

    def __call__(self, message_type: str, data: dict) -> None:
        self.events.append((message_type, data))


def _service(factory, *, broadcast=None) -> RewindService:
    return RewindService(
        factory,
        registry=build_registry(),
        ticket_service=_Tickets(factory),
        broadcast=broadcast,
    )


async def _seed(
    factory,
    *,
    status: str = "open",
    title: str = "日常",
    strength: str = "explicit",
    source: str = "web",
    sender: str | None = "web",
    risk_ceiling: str = "consume",
    policy_version: str = "abc123",
    orchestrator_session_id: str | None = None,
) -> tuple[str, str]:
    """源工单、显式/推断委托与 Worker 会话；返回 ``(ticket_id, session_id)``。"""
    async with factory() as db:
        ticket = Ticket(
            title=title,
            kind="daily_ops",
            status=status,
            origin="user" if strength == "explicit" else "system",
            priority=2,
            orchestrator_session_id=orchestrator_session_id,
        )
        db.add(ticket)
        await db.flush()
        session = AgentSession(
            title="工单会话",
            model="worker-model",
            base_url="https://llm.test/v1",
            kind="worker",
            status="active",
            ticket_id=ticket.id,
        )
        db.add(session)
        await db.flush()
        ticket.worker_session_id = session.id
        db.add(
            Mandate(
                ticket_id=ticket.id,
                scope="把日常做完",
                strength=strength,
                source=source,
                sender=sender,
                rule_id=None,
                risk_ceiling=risk_ceiling,
                policy_version=policy_version,
            )
        )
        await db.commit()
        return str(ticket.id), str(session.id)


async def _seed_messages(factory, session_id: str) -> None:
    async with factory() as db:
        rows = [
            {"seq": 0, "role": "user", "content": "把日常做了", "created_at": BASE},
            {
                "seq": 1,
                "role": "assistant",
                "content": None,
                "tool_calls": [
                    {"id": "c1", "type": "function", "function": {"name": "get_stage"}}
                ],
                "created_at": BASE + timedelta(seconds=1),
            },
            {
                "seq": 2,
                "role": "tool",
                "content": json.dumps({"result": {"stage": {"code": "1-7"}}}),
                "tool_call_id": "c1",
                "tool_name": "get_stage",
                "created_at": BASE + timedelta(seconds=2),
            },
            {
                "seq": 3,
                "role": "assistant",
                "content": "查好了",
                "created_at": BASE + timedelta(seconds=3),
            },
            {
                "seq": 4,
                "role": "user",
                "content": "之后说的话",
                "created_at": BASE + timedelta(seconds=4),
            },
            {
                "seq": 5,
                "role": "assistant",
                "content": "之后被取代的回复",
                "superseded_at": BASE + timedelta(seconds=9),
                "created_at": BASE + timedelta(seconds=5),
            },
        ]
        for row in rows:
            db.add(AgentMessage(session_id=session_id, **row))
        await db.commit()


async def _forked(factory, service: RewindService, ticket_id: str, at_seq: int):
    new_ticket_id = await service.fork_ticket(ticket_id, at_seq)
    async with factory() as db:
        ticket = await db.get(Ticket, new_ticket_id)
        mandate = (
            await db.execute(select(Mandate).where(Mandate.ticket_id == new_ticket_id))
        ).scalar_one()
        session = (
            await db.execute(
                select(AgentSession).where(AgentSession.ticket_id == new_ticket_id)
            )
        ).scalars().one()
        messages = (
            await db.execute(
                select(AgentMessage)
                .where(AgentMessage.session_id == session.id)
                .order_by(AgentMessage.seq)
            )
        ).scalars().all()
        inbox = await db.scalar(
            select(func.count())
            .select_from(AgentInbox)
            .where(AgentInbox.session_id == session.id)
        )
    return ticket, mandate, session, list(messages), int(inbox or 0)


def test_fork_copies_mandate_and_history(tmp_path) -> None:
    """委托逐字段相等、seq 保持、被取代的不复制、run_id 为空。"""

    engine, factory = asyncio.run(make_session_factory(tmp_path / "fork.db"))

    async def scenario() -> None:
        ticket_id, session_id = await _seed(factory)
        await _seed_messages(factory, session_id)
        async with factory() as db:
            source = await db.get(Ticket, ticket_id)
            source_mandate = (
                await db.execute(select(Mandate).where(Mandate.ticket_id == ticket_id))
            ).scalar_one()

        ticket, mandate, session, messages, inbox = await _forked(
            factory, _service(factory), ticket_id, 3
        )

        assert (mandate.scope, mandate.strength, mandate.source) == (
            source_mandate.scope,
            source_mandate.strength,
            source_mandate.source,
        )
        assert (mandate.sender, mandate.rule_id, mandate.risk_ceiling) == (
            source_mandate.sender,
            source_mandate.rule_id,
            source_mandate.risk_ceiling,
        )
        assert mandate.policy_version == source_mandate.policy_version
        assert ticket.kind == source.kind
        assert ticket.priority == source.priority
        assert ticket.status == TicketStatus.OPEN.value
        assert ticket.origin == "user"
        assert ticket.title == "日常（分叉）"
        assert [message.seq for message in messages] == [0, 1, 2, 3, 4]
        # 复制过来的 4 条 + 末尾提示；seq 4 是提示（源会话的 seq 4 在目标之后）。
        assert [message.role for message in messages] == [
            "user",
            "assistant",
            "tool",
            "assistant",
            "system",
        ]
        assert all(message.run_id is None for message in messages)
        assert messages[2].tool_name == "get_stage"
        assert "之后被取代的回复" not in {
            message.content for message in messages
        }
        assert inbox == 0

    asyncio.run(scenario())
    engine.sync_engine.dispose()


def test_fork_sets_forked_from_and_notice(tmp_path) -> None:
    """新会话两列、末尾 system 提示逐字，``forked_from_of`` 反查得到来源。"""

    engine, factory = asyncio.run(make_session_factory(tmp_path / "forked_from.db"))

    async def scenario() -> None:
        ticket_id, session_id = await _seed(factory)
        await _seed_messages(factory, session_id)

        _ticket, _mandate, session, messages, _inbox = await _forked(
            factory, _service(factory), ticket_id, 3
        )

        assert session.forked_from_session_id == session_id
        assert session.forked_from_seq == 3
        assert session.kind == "worker"
        assert session.title == "工单会话"
        assert session.model == "worker-model"
        notice = messages[-1]
        assert notice.role == "system"
        assert notice.content == (
            "这个工单从工单「日常」的第 3 条消息处分叉。"
            "原工单在这之后做过的事不在这段记录里；"
            "设备、流水线与账号的当前状态以现场为准。"
        )
        async with factory() as db:
            forked = await forked_from_of(db, str(session.id))
        assert forked is not None
        assert (forked.ticket_id, forked.title, forked.at_seq) == (
            ticket_id,
            "日常",
            3,
        )

    asyncio.run(scenario())
    engine.sync_engine.dispose()


def test_fork_copies_latest_valid_checkpoint(tmp_path) -> None:
    """复制 ``upto_seq ≤ at_seq``、未作废、``created_at`` 最新的那条为 active。"""

    engine, factory = asyncio.run(make_session_factory(tmp_path / "fork_cp.db"))

    async def scenario() -> None:
        ticket_id, session_id = await _seed(factory)
        await _seed_messages(factory, session_id)
        async with factory() as db:
            db.add_all(
                [
                    CompactionCheckpoint(
                        session_id=session_id,
                        upto_seq=1,
                        summary="早且生效",
                        summary_json={},
                        durable={},
                        tokens_before=1,
                        tokens_after=1,
                        trigger="auto",
                        status="active",
                        created_at=BASE + timedelta(seconds=10),
                    ),
                    CompactionCheckpoint(
                        session_id=session_id,
                        upto_seq=2,
                        summary="晚且被取代",
                        summary_json={"degraded": False},
                        durable={"tickets": []},
                        tokens_before=2,
                        tokens_after=2,
                        trigger="auto",
                        status="superseded",
                        created_at=BASE + timedelta(seconds=20),
                    ),
                    CompactionCheckpoint(
                        session_id=session_id,
                        upto_seq=2,
                        summary="已作废",
                        summary_json={},
                        durable={},
                        tokens_before=3,
                        tokens_after=3,
                        trigger="manual",
                        status="invalidated",
                        created_at=BASE + timedelta(seconds=30),
                    ),
                    CompactionCheckpoint(
                        session_id=session_id,
                        upto_seq=5,
                        summary="覆盖太远",
                        summary_json={},
                        durable={},
                        tokens_before=4,
                        tokens_after=4,
                        trigger="auto",
                        status="active",
                        created_at=BASE + timedelta(seconds=40),
                    ),
                ]
            )
            await db.commit()

        _ticket, _mandate, session, _messages, _inbox = await _forked(
            factory, _service(factory), ticket_id, 2
        )

        async with factory() as db:
            checkpoints = (
                await db.execute(
                    select(CompactionCheckpoint).where(
                        CompactionCheckpoint.session_id == session.id
                    )
                )
            ).scalars().all()
        assert len(checkpoints) == 1
        copied = checkpoints[0]
        assert copied.status == "active"
        assert copied.upto_seq == 2
        assert copied.summary == "晚且被取代"
        assert copied.run_id is None
        assert copied.summary_json == {"degraded": False}

    asyncio.run(scenario())
    engine.sync_engine.dispose()


def test_fork_keeps_inferred_mandate(tmp_path) -> None:
    """推断委托仍是 ``inferred``、风险上限仍是 ``none``。"""

    engine, factory = asyncio.run(make_session_factory(tmp_path / "fork_inferred.db"))

    async def scenario() -> None:
        ticket_id, session_id = await _seed(
            factory,
            strength="inferred",
            source="system",
            sender=None,
            risk_ceiling="none",
        )
        await _seed_messages(factory, session_id)

        _ticket, mandate, _session, _messages, _inbox = await _forked(
            factory, _service(factory), ticket_id, 3
        )

        assert mandate.strength == "inferred"
        assert mandate.risk_ceiling == "none"

    asyncio.run(scenario())
    engine.sync_engine.dispose()


def test_fork_does_not_write_inbox(tmp_path) -> None:
    """新 Worker 收件箱为空；借当前活动总控会话，广播 ``ticket_created``。"""

    engine, factory = asyncio.run(make_session_factory(tmp_path / "fork_inbox.db"))

    async def scenario() -> None:
        async with factory() as db:
            orchestrator = AgentSession(
                title="总控", model="orchestrator-model", kind="orchestrator"
            )
            db.add(orchestrator)
            await db.commit()
            orchestrator_id = str(orchestrator.id)
        ticket_id, session_id = await _seed(
            factory, orchestrator_session_id=orchestrator_id
        )
        await _seed_messages(factory, session_id)
        broadcast = _Broadcast()

        ticket, _mandate, _session, _messages, inbox = await _forked(
            factory, _service(factory, broadcast=broadcast), ticket_id, 3
        )

        assert inbox == 0
        assert ticket.orchestrator_session_id == orchestrator_id
        assert [name for name, _data in broadcast.events] == ["ticket_status"]
        _name, payload = broadcast.events[0]
        assert payload["event"] == "ticket_created"
        assert payload["ticket_id"] == str(ticket.id)
        assert payload["status"] == TicketStatus.OPEN.value

    asyncio.run(scenario())
    engine.sync_engine.dispose()
