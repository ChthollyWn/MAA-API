"""``TicketService`` 的状态机、汇报、转发与结束收尾（M69 规格 §4.5.1–§4.5.3、§6）。

异步用例一律「同步测试函数 + ``asyncio.run(scenario())``」；临时库用
``tests/agent/tickets/conftest.py`` 的 ``v3_factory``，代理定义用 ``spec_registry``。
广播用一个收集列表代替 ``ws_manager``，结束监听者分别用同步与异步函数验证。
"""

from __future__ import annotations

import asyncio

import pytest
from sqlalchemy import func, select

from maa_api.agent.tickets.inbox import InboxService
from maa_api.agent.tickets.mandates import policy_version
from maa_api.agent.tickets.reports import HumanAction, parse_report
from maa_api.agent.tickets.service import (
    BLOCKED_AFTER_RESTART_PROMPT,
    BLOCKED_AFTER_RESTART_SUMMARY,
    TicketEnded,
    TicketService,
    block_tickets_after_restart,
    ticket_status_payload,
)
from maa_api.agent.tickets.threads import ThreadRouter
from maa_api.db.models import (
    AgentInbox,
    Mandate,
    ThreadSubscription,
    Ticket,
    TicketReport,
    utcnow,
)
from maa_api.db.repositories.ticket import (
    MandateRepository,
    TicketReportRepository,
    TicketRepository,
)
from maa_api.domain.enums import (
    EventSource,
    ReportStatus,
    TicketOrigin,
    TicketStatus,
)
from maa_api.domain.errors import AppError, ErrorCode
from tests.fakes.v3_db import seed_run, seed_session

_PIPELINE = "pipeline:0f8fad5b-d9cb-469f-a165-70867728950e"
_PAYLOAD_KEYS = {
    "event",
    "ticket_id",
    "title",
    "kind",
    "status",
    "last_report_status",
    "summary",
    "human_action",
}


class _Harness:
    """一套临时库上的收件箱、路由与服务；``events`` 收集广播。"""

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
            broadcast=self._record,
        )

    def _record(self, message_type: str, data: dict) -> None:
        self.events.append((message_type, data))

    async def create(self, **overrides):
        values = {
            "kind": "daily_ops",
            "title": "日常",
            "scope": "把日常做了",
            "origin": TicketOrigin.USER,
            "source": EventSource.WEB,
            "sender": "web",
        }
        values.update(overrides)
        return await self.service.create(**values)


def _harness(factory, registry) -> _Harness:
    return _Harness(factory, registry)


def _action(ref_id: str = "a-1") -> dict:
    return {"type": "confirmation", "prompt": "确认要用理智药", "ref_id": ref_id}


def _report(
    status: ReportStatus,
    summary: str = "进展",
    *,
    action: dict | None = None,
    details: dict | None = None,
):
    data: dict = {"status": status, "summary": summary}
    if action is not None:
        data["human_action"] = action
    if details is not None:
        data["details"] = details
    return parse_report(data)


async def _ticket(factory, ticket_id: str) -> Ticket | None:
    async with factory() as db:
        return await TicketRepository(db).get(ticket_id)


async def _mandate(factory, ticket_id: str) -> Mandate | None:
    async with factory() as db:
        return await MandateRepository(db).get_by_ticket(ticket_id)


async def _reports(factory, ticket_id: str) -> list[TicketReport]:
    async with factory() as db:
        result = await db.execute(
            select(TicketReport)
            .where(TicketReport.ticket_id == ticket_id)
            .order_by(TicketReport.id.asc())
        )
        return list(result.scalars().all())


async def _inbox(factory, session_id: str) -> list[AgentInbox]:
    async with factory() as db:
        result = await db.execute(
            select(AgentInbox)
            .where(AgentInbox.session_id == session_id)
            .order_by(AgentInbox.id.asc())
        )
        return list(result.scalars().all())


async def _subscriptions(factory, ticket_id: str) -> list[ThreadSubscription]:
    async with factory() as db:
        result = await db.execute(
            select(ThreadSubscription)
            .where(ThreadSubscription.ticket_id == ticket_id)
            .order_by(ThreadSubscription.id.asc())
        )
        return list(result.scalars().all())


async def _count(factory, model: type) -> int:
    async with factory() as db:
        result = await db.execute(select(func.count()).select_from(model))
        return int(result.scalar_one())


def test_ticket_status_payload_shape() -> None:
    ticket = Ticket(
        id="t-1",
        title="日常",
        kind="daily_ops",
        status="waiting",
        origin="user",
        summary="等你确认",
        last_report_status="waiting",
        human_action=_action(),
    )
    payload = ticket_status_payload("report_created", ticket)
    assert set(payload) == _PAYLOAD_KEYS
    assert payload["event"] == "report_created"
    assert payload["ticket_id"] == "t-1"
    assert payload["title"] == "日常"
    assert payload["kind"] == "daily_ops"
    assert payload["status"] == "waiting"
    assert payload["last_report_status"] == "waiting"
    assert payload["summary"] == "等你确认"
    assert payload["human_action"] == _action()


def test_create_writes_ticket_and_mandate_in_one_transaction(
    v3_factory, spec_registry
) -> None:
    async def scenario() -> None:
        harness = _harness(v3_factory, spec_registry)
        ticket, mandate = await harness.service.create(
            kind="daily_ops",
            title="  日常  ",
            scope="  把日常做了  ",
            origin=TicketOrigin.USER,
            source=EventSource.WEB,
            sender="web",
        )
        assert ticket.title == "日常"
        assert ticket.status == TicketStatus.OPEN
        assert ticket.origin == TicketOrigin.USER
        assert ticket.priority == 1
        assert ticket.created_at == ticket.updated_at
        assert mandate.ticket_id == ticket.id
        assert mandate.scope == "把日常做了"
        assert mandate.strength == "explicit"
        assert mandate.source == "web"
        assert mandate.risk_ceiling == "consume"
        assert mandate.policy_version == policy_version()
        assert len(mandate.policy_version) == 12

        stored_ticket = await _ticket(v3_factory, ticket.id)
        stored_mandate = await _mandate(v3_factory, ticket.id)
        assert stored_ticket is not None and stored_mandate is not None
        assert stored_ticket.status == "open"
        assert stored_mandate.id == mandate.id

        assert len(harness.events) == 1
        message_type, payload = harness.events[0]
        assert message_type == "ticket_status"
        assert payload["event"] == "ticket_created"
        assert set(payload) == _PAYLOAD_KEYS
        assert payload["ticket_id"] == ticket.id

    asyncio.run(scenario())


def test_create_inferred_mandate_has_none_ceiling(v3_factory, spec_registry) -> None:
    async def scenario() -> None:
        harness = _harness(v3_factory, spec_registry)
        ticket, mandate = await harness.service.create(
            kind="general",
            title="定时任务",
            scope="跑一次日常",
            origin=TicketOrigin.TRIGGER,
            source=EventSource.SCHEDULE,
            rule_id="rule-1",
        )
        assert ticket.origin == TicketOrigin.TRIGGER
        assert mandate.strength == "inferred"
        assert mandate.risk_ceiling == "none"
        assert mandate.source == "schedule"
        assert mandate.rule_id == "rule-1"

    asyncio.run(scenario())


def test_create_rejects_non_worker_kind(v3_factory, spec_registry) -> None:
    async def scenario() -> None:
        harness = _harness(v3_factory, spec_registry)
        with pytest.raises(AppError) as invalid:
            await harness.create(kind="researcher")
        assert invalid.value.code == ErrorCode.INVALID_PARAMETER
        with pytest.raises(AppError) as missing:
            await harness.create(kind="nonexistent")
        assert missing.value.code == ErrorCode.AGENT_SPEC_NOT_FOUND
        assert await _count(v3_factory, Ticket) == 0
        assert await _count(v3_factory, Mandate) == 0

    asyncio.run(scenario())


def test_create_validation(v3_factory, spec_registry) -> None:
    async def scenario() -> None:
        harness = _harness(v3_factory, spec_registry)
        for overrides in (
            {"title": "   "},
            {"title": "x" * 65},
            {"priority": 3},
            {"priority": -1},
            {"source": EventSource.PIPELINE},
        ):
            with pytest.raises(AppError) as exc:
                await harness.create(**overrides)
            assert exc.value.code == ErrorCode.INVALID_PARAMETER
        assert await _count(v3_factory, Ticket) == 0

    asyncio.run(scenario())


def test_report_updates_ticket_per_mapping(v3_factory, spec_registry) -> None:
    async def scenario() -> None:
        harness = _harness(v3_factory, spec_registry)
        cases = (
            (ReportStatus.PROGRESS, TicketStatus.OPEN, None, False),
            (ReportStatus.WAITING, TicketStatus.WAITING, _action(), False),
            (ReportStatus.READY, TicketStatus.DONE, None, True),
            (ReportStatus.BLOCKED, TicketStatus.BLOCKED, _action("b-9"), False),
            (ReportStatus.FAILED, TicketStatus.FAILED, None, True),
            (ReportStatus.DROPPED, TicketStatus.CANCELLED, None, False),
        )
        for index, (status, expected, action, has_result) in enumerate(cases):
            ticket, _ = await harness.create(title=f"映射 {index}")
            details = {"out": status.value} if has_result else None
            # ready 带 human_action 应被 parse_report 抹掉，工单字段保持为空。
            ignored_action = _action("ignored") if status is ReportStatus.READY else None
            report = _report(
                status,
                summary=f"汇报 {index}",
                action=action or ignored_action,
                details=details,
            )
            outcome = await harness.service.record_report(ticket.id, report)
            assert outcome.report.status == status.value
            assert outcome.forwarded_inbox_id is None

            row = await _ticket(v3_factory, ticket.id)
            assert row is not None
            assert row.status == expected.value
            assert row.summary == f"汇报 {index}"
            assert row.last_report_status == status.value
            if status in (ReportStatus.WAITING, ReportStatus.BLOCKED):
                assert row.human_action == action
            else:
                assert row.human_action is None
            if has_result:
                assert row.result == details
            else:
                assert row.result is None
            if expected.is_terminal:
                assert row.ended_at is not None

            reports = await _reports(v3_factory, ticket.id)
            assert len(reports) == 1
            assert reports[0].status == status.value
            assert reports[0].human_action == (
                action if status in (ReportStatus.WAITING, ReportStatus.BLOCKED) else None
            )
            assert reports[0].forwarded is False

    asyncio.run(scenario())


def test_report_forwarding_writes_orchestrator_inbox(v3_factory, spec_registry) -> None:
    async def scenario() -> None:
        harness = _harness(v3_factory, spec_registry)
        orchestrator = await seed_session(v3_factory, kind="orchestrator")
        expected = 0
        for status in (
            ReportStatus.WAITING,
            ReportStatus.READY,
            ReportStatus.BLOCKED,
            ReportStatus.FAILED,
        ):
            ticket, _ = await harness.create(
                title=f"转发 {status.value}", orchestrator_session_id=orchestrator
            )
            action = _action(f"ref-{status.value}") if status in (
                ReportStatus.WAITING,
                ReportStatus.BLOCKED,
            ) else None
            outcome = await harness.service.record_report(
                ticket.id, _report(status, summary=f"汇报 {status.value}", action=action)
            )
            assert outcome.forwarded_inbox_id is not None
            expected += 1

            items = await _inbox(v3_factory, orchestrator)
            assert len(items) == expected
            item = items[-1]
            assert item.kind == "report"
            assert item.thread_key == f"ticket:{ticket.id}"
            assert item.ref_id == str(outcome.report.id)
            assert set(item.payload) == {
                "ticket_id",
                "ticket_title",
                "ticket_kind",
                "report_id",
                "status",
                "summary",
                "human_action",
            }
            assert item.payload["ticket_id"] == ticket.id
            assert item.payload["ticket_title"] == f"转发 {status.value}"
            assert item.payload["ticket_kind"] == "daily_ops"
            assert item.payload["report_id"] == outcome.report.id
            assert item.payload["status"] == status.value
            assert item.payload["summary"] == f"汇报 {status.value}"
            assert item.payload["human_action"] == action

            reports = await _reports(v3_factory, ticket.id)
            assert reports[0].forwarded is True

        ticket, _ = await harness.create(title="进展不转发")
        outcome = await harness.service.record_report(ticket.id, _report(ReportStatus.PROGRESS))
        assert outcome.forwarded_inbox_id is None
        assert len(await _inbox(v3_factory, orchestrator)) == expected
        assert (await _reports(v3_factory, ticket.id))[0].forwarded is False

    asyncio.run(scenario())


def test_terminal_report_ends_subscriptions_and_drops_worker_inbox(
    v3_factory, spec_registry
) -> None:
    async def scenario() -> None:
        harness = _harness(v3_factory, spec_registry)
        worker = await seed_session(v3_factory, kind="worker")
        ticket, _ = await harness.create()
        await harness.router.subscribe(ticket.id, _PIPELINE)
        await harness.service.attach_worker_session(
            ticket.id, worker, brief="做日常"
        )

        ended: list[TicketEnded] = []
        async_ended: list[TicketEnded] = []

        def raising(_: TicketEnded) -> None:
            raise RuntimeError("监听者坏了")

        def listener(event: TicketEnded) -> None:
            ended.append(event)

        async def async_listener(event: TicketEnded) -> None:
            async_ended.append(event)

        harness.service.add_end_listener(raising)
        harness.service.add_end_listener(listener)
        harness.service.add_end_listener(async_listener)

        outcome = await harness.service.record_report(
            ticket.id, _report(ReportStatus.READY, details={"ok": True})
        )
        assert outcome.ticket.status == TicketStatus.DONE
        assert outcome.ticket.ended_at is not None

        subscriptions = await _subscriptions(v3_factory, ticket.id)
        assert len(subscriptions) == 1
        assert subscriptions[0].active is False
        assert subscriptions[0].ended_at is not None

        items = await _inbox(v3_factory, worker)
        assert len(items) == 1
        assert items[0].status == "dropped"
        assert items[0].consumed_at is not None

        expected = [
            TicketEnded(
                ticket_id=ticket.id,
                status=TicketStatus.DONE,
                worker_session_id=worker,
            )
        ]
        assert ended == expected
        assert async_ended == expected

    asyncio.run(scenario())


def test_report_on_ended_ticket_raises(v3_factory, spec_registry) -> None:
    async def scenario() -> None:
        harness = _harness(v3_factory, spec_registry)
        ticket, _ = await harness.create()
        await harness.service.record_report(ticket.id, _report(ReportStatus.READY))
        before = len(await _reports(v3_factory, ticket.id))
        events_before = len(harness.events)

        with pytest.raises(AppError) as exc:
            await harness.service.record_report(
                ticket.id, _report(ReportStatus.BLOCKED, action=_action())
            )
        assert exc.value.code == ErrorCode.TICKET_ENDED
        assert len(await _reports(v3_factory, ticket.id)) == before
        assert len(harness.events) == events_before

        with pytest.raises(AppError) as missing:
            await harness.service.record_report(
                "missing-ticket", _report(ReportStatus.PROGRESS)
            )
        assert missing.value.code == ErrorCode.TICKET_NOT_FOUND

    asyncio.run(scenario())


def test_concurrent_terminal_reports_end_once(v3_factory, spec_registry) -> None:
    async def scenario() -> None:
        harness = _harness(v3_factory, spec_registry)
        ticket, _ = await harness.create()
        ended: list[TicketEnded] = []
        harness.service.add_end_listener(ended.append)
        report = parse_report(
            {"status": ReportStatus.READY, "summary": "做完了"}
        )

        results = await asyncio.gather(
            harness.service.record_report(ticket.id, report),
            harness.service.record_report(ticket.id, report),
            return_exceptions=True,
        )
        outcomes = [result for result in results if not isinstance(result, BaseException)]
        errors = [result for result in results if isinstance(result, AppError)]
        assert len(outcomes) == 1
        assert len(errors) == 1
        assert errors[0].code == ErrorCode.TICKET_ENDED

        assert len(await _reports(v3_factory, ticket.id)) == 1
        assert len(ended) == 1
        row = await _ticket(v3_factory, ticket.id)
        assert row is not None and row.status == "done"
        report_events = [
            payload for message_type, payload in harness.events
            if message_type == "ticket_status" and payload["event"] == "report_created"
        ]
        assert len(report_events) == 1
        assert report_events[0]["status"] == "done"

    asyncio.run(scenario())


def test_cancel_writes_dropped_report(v3_factory, spec_registry) -> None:
    async def scenario() -> None:
        harness = _harness(v3_factory, spec_registry)
        orchestrator = await seed_session(v3_factory, kind="orchestrator")

        user_ticket, _ = await harness.create(title="用户取消")
        ticket = await harness.service.cancel(user_ticket.id, reason="不需要了")
        assert ticket.status == TicketStatus.CANCELLED
        reports = await _reports(v3_factory, user_ticket.id)
        assert len(reports) == 1
        assert reports[0].status == "dropped"
        assert reports[0].summary == "已取消：不需要了"
        assert reports[0].forwarded is True
        items = await _inbox(v3_factory, orchestrator)
        assert len(items) == 1
        assert items[0].payload["status"] == "dropped"

        plain_ticket, _ = await harness.create(title="无原因取消")
        await harness.service.cancel(plain_ticket.id)
        plain_reports = await _reports(v3_factory, plain_ticket.id)
        assert plain_reports[0].summary == "已取消"
        assert plain_reports[0].forwarded is True

        trigger_ticket, _ = await harness.create(
            kind="general",
            title="触发器取消",
            origin=TicketOrigin.TRIGGER,
            source=EventSource.SCHEDULE,
            sender=None,
            rule_id="rule-1",
        )
        before = len(await _inbox(v3_factory, orchestrator))
        await harness.service.cancel(trigger_ticket.id)
        assert len(await _inbox(v3_factory, orchestrator)) == before
        trigger_reports = await _reports(v3_factory, trigger_ticket.id)
        assert trigger_reports[0].forwarded is False
        async with v3_factory() as db:
            pending = await TicketReportRepository(db).list_unforwarded()
        assert all(report.ticket_id != trigger_ticket.id for report in pending)

    asyncio.run(scenario())


def test_await_human_and_resolve(v3_factory, spec_registry) -> None:
    async def scenario() -> None:
        harness = _harness(v3_factory, spec_registry)
        ticket, _ = await harness.create()
        action = HumanAction(type="confirmation", prompt="确认要用理智药", ref_id="a-1")
        waiting = await harness.service.await_human(ticket.id, action)
        assert waiting is not None
        assert waiting.status == TicketStatus.WAITING
        assert waiting.human_action == action.model_dump(mode="json")
        assert harness.events[-1][1]["event"] == "ticket_updated"

        assert await harness.service.human_resolved(ticket.id, "other") is None
        row = await _ticket(v3_factory, ticket.id)
        assert row is not None
        assert row.status == "waiting"
        assert row.human_action == action.model_dump(mode="json")

        resolved = await harness.service.human_resolved(ticket.id, "a-1")
        assert resolved is not None
        assert resolved.status == TicketStatus.OPEN
        assert resolved.human_action is None
        assert harness.events[-1][1]["event"] == "ticket_updated"

        blocked_ticket, _ = await harness.create(title="阻塞后再问")
        await harness.service.record_report(
            blocked_ticket.id,
            _report(ReportStatus.BLOCKED, action=_action("b-1")),
        )
        waiting_again = await harness.service.await_human(
            blocked_ticket.id,
            HumanAction(type="decision", prompt="继续吗", ref_id=None),
        )
        assert waiting_again is not None
        assert waiting_again.status == TicketStatus.WAITING

        with pytest.raises(AppError) as missing:
            await harness.service.await_human(
                "missing-ticket",
                HumanAction(type="decision", prompt="继续吗"),
            )
        assert missing.value.code == ErrorCode.TICKET_NOT_FOUND

    asyncio.run(scenario())


def test_terminal_ticket_rejects_changes_and_ignores_human_actions(
    v3_factory, spec_registry
) -> None:
    async def scenario() -> None:
        harness = _harness(v3_factory, spec_registry)
        worker = await seed_session(v3_factory, kind="worker")
        endings = (
            (ReportStatus.READY, TicketStatus.DONE),
            (ReportStatus.FAILED, TicketStatus.FAILED),
            (ReportStatus.DROPPED, TicketStatus.CANCELLED),
        )
        for index, (report_status, terminal) in enumerate(endings):
            ticket, _ = await harness.create(title=f"终态 {index}")
            await harness.service.record_report(
                ticket.id, _report(report_status, summary="结束")
            )
            row = await _ticket(v3_factory, ticket.id)
            assert row is not None and row.status == terminal.value

            with pytest.raises(AppError) as report_exc:
                await harness.service.record_report(
                    ticket.id, _report(ReportStatus.PROGRESS)
                )
            assert report_exc.value.code == ErrorCode.TICKET_ENDED
            with pytest.raises(AppError) as cancel_exc:
                await harness.service.cancel(ticket.id)
            assert cancel_exc.value.code == ErrorCode.TICKET_ENDED
            with pytest.raises(AppError) as attach_exc:
                await harness.service.attach_worker_session(
                    ticket.id, worker, brief="做日常"
                )
            assert attach_exc.value.code == ErrorCode.TICKET_ENDED

            events_before = len(harness.events)
            assert await harness.service.await_human(
                ticket.id, HumanAction(type="decision", prompt="继续吗")
            ) is None
            assert await harness.service.human_resolved(ticket.id, "a-1") is None
            assert len(harness.events) == events_before

    asyncio.run(scenario())


def test_attach_worker_session_puts_brief(v3_factory, spec_registry) -> None:
    async def scenario() -> None:
        harness = _harness(v3_factory, spec_registry)
        worker = await seed_session(v3_factory, kind="worker")
        ticket, _ = await harness.create(title="会话工单")
        attached = await harness.service.attach_worker_session(
            ticket.id, worker, brief="  做日常  "
        )
        assert attached.worker_session_id == worker
        row = await _ticket(v3_factory, ticket.id)
        assert row is not None and row.worker_session_id == worker

        items = await _inbox(v3_factory, worker)
        assert len(items) == 1
        item = items[0]
        assert item.kind == "system"
        assert item.thread_key == f"ticket:{ticket.id}"
        assert item.ref_id == ticket.id
        assert item.payload == {
            "event": "ticket_brief",
            "ticket_id": ticket.id,
            "title": "会话工单",
            "brief": "做日常",
        }

        await harness.service.record_report(ticket.id, _report(ReportStatus.READY))
        with pytest.raises(AppError) as ended:
            await harness.service.attach_worker_session(
                ticket.id, worker, brief="再做一次"
            )
        assert ended.value.code == ErrorCode.TICKET_ENDED

        other, _ = await harness.create(title="另一位")
        for brief in ("   ", "x" * 4001):
            with pytest.raises(AppError) as invalid:
                await harness.service.attach_worker_session(
                    other.id, worker, brief=brief
                )
            assert invalid.value.code == ErrorCode.INVALID_PARAMETER
        other_row = await _ticket(v3_factory, other.id)
        assert other_row is not None and other_row.worker_session_id is None

        with pytest.raises(AppError) as missing:
            await harness.service.attach_worker_session(
                "missing-ticket", worker, brief="做日常"
            )
        assert missing.value.code == ErrorCode.TICKET_NOT_FOUND

    asyncio.run(scenario())


def test_forward_pending_reports_after_orchestrator_appears(
    v3_factory, spec_registry
) -> None:
    async def scenario() -> None:
        harness = _harness(v3_factory, spec_registry)
        ticket, _ = await harness.create()
        outcome = await harness.service.record_report(
            ticket.id, _report(ReportStatus.READY, summary="做完了")
        )
        assert outcome.forwarded_inbox_id is None
        reports = await _reports(v3_factory, ticket.id)
        assert reports[0].forwarded is False

        orchestrator = await seed_session(v3_factory, kind="orchestrator")
        assert await harness.service.forward_pending_reports() == 1
        items = await _inbox(v3_factory, orchestrator)
        assert len(items) == 1
        assert items[0].kind == "report"
        assert items[0].ref_id == str(reports[0].id)
        assert items[0].payload["report_id"] == reports[0].id
        assert items[0].payload["ticket_id"] == ticket.id
        assert (await _reports(v3_factory, ticket.id))[0].forwarded is True

        assert await harness.service.forward_pending_reports() == 0
        assert len(await _inbox(v3_factory, orchestrator)) == 1

    asyncio.run(scenario())


def test_forwarding_falls_back_from_archived_orchestrator(
    v3_factory, spec_registry
) -> None:
    async def scenario() -> None:
        harness = _harness(v3_factory, spec_registry)
        active = await seed_session(v3_factory, kind="orchestrator")
        archived = await seed_session(v3_factory, kind="orchestrator", archived=True)
        ticket, _ = await harness.create(
            title="归档回退", orchestrator_session_id=archived
        )
        outcome = await harness.service.record_report(
            ticket.id, _report(ReportStatus.READY)
        )
        assert outcome.forwarded_inbox_id is not None
        assert len(await _inbox(v3_factory, active)) == 1
        assert await _inbox(v3_factory, archived) == []

    asyncio.run(scenario())


def test_detail_list_and_get(v3_factory, spec_registry) -> None:
    async def scenario() -> None:
        harness = _harness(v3_factory, spec_registry)
        worker = await seed_session(v3_factory, kind="worker")
        ticket, _ = await harness.create(title="详情工单")
        await harness.service.attach_worker_session(ticket.id, worker, brief="做日常")
        await seed_run(v3_factory, session_id=worker)
        for index in range(6):
            await harness.service.record_report(
                ticket.id, _report(ReportStatus.PROGRESS, summary=f"进展 {index}")
            )

        data = await harness.service.detail(ticket.id)
        assert data.ticket.id == ticket.id
        assert data.mandate.ticket_id == ticket.id
        assert data.subscriptions == []
        assert len(data.recent_reports) == 5
        report_ids = [report.id for report in data.recent_reports]
        assert report_ids == sorted(report_ids, reverse=True)
        assert data.turn_running is True

        assert (await harness.service.get(ticket.id)).id == ticket.id
        with pytest.raises(AppError) as missing:
            await harness.service.get("missing-ticket")
        assert missing.value.code == ErrorCode.TICKET_NOT_FOUND

        done_ticket, _ = await harness.create(title="已结束")
        await harness.service.record_report(done_ticket.id, _report(ReportStatus.READY))
        done_page = await harness.service.list(statuses=[TicketStatus.DONE])
        assert done_page.total == 1
        assert done_page.items[0].id == done_ticket.id
        one_page = await harness.service.list(origin=TicketOrigin.USER, page=1, size=1)
        assert one_page.total == 2
        assert len(one_page.items) == 1

        reports_page = await harness.service.list_reports(ticket.id, page=1, size=3)
        assert reports_page.total == 6
        assert len(reports_page.items) == 3
        listed_ids = [report.id for report in reports_page.items]
        assert listed_ids == sorted(listed_ids, reverse=True)
        with pytest.raises(AppError) as missing_reports:
            await harness.service.list_reports("missing-ticket")
        assert missing_reports.value.code == ErrorCode.TICKET_NOT_FOUND

    asyncio.run(scenario())


def test_report_input_boundary_rows() -> None:
    """边界情况表里由 ``parse_report`` 把关的两行（工单服务的入参契约）。"""

    with pytest.raises(AppError) as missing_action:
        parse_report({"status": "waiting", "summary": "等你确认"})
    assert missing_action.value.code == ErrorCode.INVALID_PARAMETER
    issues = missing_action.value.details["issues"]
    assert any(issue["loc"][-1] == "human_action" for issue in issues)

    with pytest.raises(AppError) as too_large:
        parse_report(
            {
                "status": "progress",
                "summary": "进展",
                "details": {"blob": "x" * 9000},
            }
        )
    assert too_large.value.code == ErrorCode.INVALID_PARAMETER


def test_block_tickets_after_restart_marks_open_and_waiting(
    v3_factory, spec_registry
) -> None:
    async def scenario() -> None:
        harness = _harness(v3_factory, spec_registry)
        open_ticket, _ = await harness.create(title="开放")
        waiting_ticket, _ = await harness.create(title="等待")
        await harness.service.record_report(
            waiting_ticket.id, _report(ReportStatus.WAITING, action=_action("w-1"))
        )
        done_ticket, _ = await harness.create(title="已完成")
        await harness.service.record_report(done_ticket.id, _report(ReportStatus.READY))

        now = utcnow()
        async with v3_factory() as db:
            blocked = await block_tickets_after_restart(
                db, [open_ticket.id, waiting_ticket.id, done_ticket.id], now=now
            )
            await db.commit()
        assert blocked == [open_ticket.id, waiting_ticket.id]

        for ticket_id in blocked:
            row = await _ticket(v3_factory, ticket_id)
            assert row is not None
            assert row.status == "blocked"
            assert row.summary == BLOCKED_AFTER_RESTART_SUMMARY
            assert row.last_report_status == "blocked"
            assert row.human_action == {
                "type": "decision",
                "prompt": BLOCKED_AFTER_RESTART_PROMPT,
                "ref_id": None,
            }
            reports = await _reports(v3_factory, ticket_id)
            last = reports[-1]
            assert last.status == "blocked"
            assert last.summary == BLOCKED_AFTER_RESTART_SUMMARY
            assert last.forwarded is False
            assert last.run_id is None

        done_row = await _ticket(v3_factory, done_ticket.id)
        assert done_row is not None and done_row.status == "done"
        done_reports = await _reports(v3_factory, done_ticket.id)
        assert [report.status for report in done_reports] == ["ready"]

        async with v3_factory() as db:
            again = await block_tickets_after_restart(
                db, [open_ticket.id], now=utcnow()
            )
            await db.commit()
        assert again == []
        assert len(await _reports(v3_factory, open_ticket.id)) == 1

    asyncio.run(scenario())
