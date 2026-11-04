"""M70 T9：WorkerService（spec §4.5.3、§4.5.7、§6 的八条）。

异步用例沿用 M69 的写法：同步测试函数 + ``asyncio.run(scenario())``；临时库用
``tests/fakes/v3_db.make_session_factory``。工单、收件箱与路由用真实服务，
orchestrator / 运行服务 / 标题器用轻量替身；泵用假的 ``_run_turn`` 观察唤醒与中断。
"""

from __future__ import annotations

import asyncio
from datetime import timedelta
from types import SimpleNamespace

from sqlalchemy import select

from maa_api.agent.harness.context import RunContext, RunOutcome
from maa_api.agent.harness.events import RunEventBus
from maa_api.agent.tickets.inbox import InboxService
from maa_api.agent.tickets.mandates import build_mandate
from maa_api.agent.tickets.service import TicketService
from maa_api.agent.tickets.threads import ThreadRouter
from maa_api.agent.tools.report import ReportParams
from maa_api.agent.runs.service import RunService
from maa_api.agent.workers.pump import TurnHandle
from maa_api.agent.workers.service import WorkerService
from maa_api.db.models import (
    AgentInbox,
    AgentMessage,
    AgentSession,
    Pipeline,
    ThreadSubscription,
    TicketReport,
    Ticket,
    new_uuid,
    utcnow,
)
from maa_api.db.repositories.ticket import MandateRepository, TicketRepository
from maa_api.domain.enums import (
    AgentRunStatus,
    InboxKind,
    InboxStatus,
    PipelineStatus,
    ReportStatus,
    TicketStatus,
)
from maa_api.domain.errors import AppError, ErrorCode
from tests.fakes.v3_db import make_session_factory, seed_run, seed_session

_SPEC = SimpleNamespace(
    name="general",
    kind="worker",
    title="通用执行",
    description="在界面里干活",
    risk_ceiling="consume",
    model_role="worker",
    spec_version="abcdef123456",
    body="你是 MAA-API 的通用执行 Worker",
    tools=(),
    delegates=(),
)


class _Registry:
    """一个 ``general`` Worker 定义；未知名字与生产一样抛 ``AGENT_SPEC_NOT_FOUND``。"""

    def get(self, name: str) -> SimpleNamespace:
        if name == "general":
            return _SPEC
        raise AppError(
            ErrorCode.AGENT_SPEC_NOT_FOUND, f"没有名为 {name} 的代理定义"
        )

    def list(self) -> list[SimpleNamespace]:
        return [_SPEC]


class _ProfileRouter:
    """Worker 角色的模型档案替身；``open_ticket`` 只用 model 与 base_url。"""

    def __init__(self) -> None:
        self.roles: list[str] = []
        self.profile = SimpleNamespace(
            model="test-model",
            base_url="https://llm.test/v1",
            api_key="sk-test",
        )

    async def resolve(self, role: str) -> SimpleNamespace:
        self.roles.append(str(role))
        return self.profile


class _Titler:
    def __init__(self, title: str = "自动标题") -> None:
        self.title = title
        self.calls: list[str] = []

    async def title_for(self, text: str) -> str:
        self.calls.append(text)
        return self.title


class _Orchestrator:
    """总控替身：只记``current_session_id`` 与 passive 通知。"""

    def __init__(self, session_id: str | None = None) -> None:
        self.session_id = session_id
        self.notifications: list[dict] = []

    async def current_session_id(self) -> str | None:
        return self.session_id

    async def notify(
        self,
        text: str,
        *,
        passive: bool,
        ref_id: str | None = None,
        ticket_id: str | None = None,
    ) -> int:
        self.notifications.append(
            {
                "text": text,
                "passive": passive,
                "ref_id": ref_id,
                "ticket_id": ticket_id,
            }
        )
        return len(self.notifications)


class _RunService:
    """运行服务替身：``get`` 返回预置行；测试不允许真的起一轮。"""

    def __init__(self, runs: dict | None = None) -> None:
        self.runs = dict(runs or {})
        self.started: list[dict] = []
        self.finished: list[dict] = []

    async def get(self, run_id: str) -> SimpleNamespace | None:
        return self.runs.get(run_id)

    async def start(self, **kwargs) -> SimpleNamespace:
        self.started.append(kwargs)
        raise AssertionError("这个用例不应该真的起一轮")

    async def finish(self, run_id: str, *, status, finish_reason: str) -> None:
        self.finished.append(
            {
                "run_id": run_id,
                "status": status,
                "finish_reason": finish_reason,
            }
        )


class _Harness:
    def __init__(
        self,
        factory,
        *,
        run_service: _RunService | None = None,
        event_bus: RunEventBus | None = None,
    ) -> None:
        self.factory = factory
        self.inbox = InboxService(factory)
        self.router = ThreadRouter(factory, self.inbox)
        self.registry = _Registry()
        self.events: list[tuple[str, dict]] = []
        self.ticket_service = TicketService(
            factory,
            inbox_service=self.inbox,
            thread_router=self.router,
            spec_registry=self.registry,
            broadcast=self._record,
        )
        self.profile_router = _ProfileRouter()
        self.titler = _Titler()
        self.run_service = run_service or _RunService()
        self.bus = event_bus
        self.state = SimpleNamespace(
            model_router=self.profile_router,
            setting_service=None,
            question_service=None,
            confirmation_service=None,
            ticket_service=self.ticket_service,
        )
        self.app = SimpleNamespace(state=self.state)
        self.service = WorkerService(
            app=self.app,
            session_factory=factory,
            spec_registry=self.registry,
            run_service=self.run_service,
            inbox_service=self.inbox,
            ticket_service=self.ticket_service,
            thread_router=self.router,
            delegation=SimpleNamespace(),
            titler=self.titler,
            event_bus=self.bus,
        )
        self.orchestrator = _Orchestrator()
        self.service.bind_orchestrator(self.orchestrator)

    def _record(self, message_type: str, data: dict) -> None:
        self.events.append((message_type, data))

    async def close(self) -> None:
        await self.service.close()


async def _make_ticket(
    factory,
    *,
    status: TicketStatus = TicketStatus.OPEN,
    title: str = "日常",
    worker: bool = True,
    orchestrator_session_id: str | None = None,
) -> tuple[str, str | None]:
    """建工单并挂一个 Worker 会话；返回 ``(ticket_id, worker_session_id)``。"""
    ticket_id = new_uuid()
    async with factory() as db:
        db.add(
            Ticket(
                id=ticket_id,
                title=title,
                kind="general",
                status=status.value,
                origin="user",
                priority=1,
                orchestrator_session_id=orchestrator_session_id,
            )
        )
        await db.flush()
        db.add(
            build_mandate(
                ticket_id=ticket_id,
                scope="测试范围",
                origin="user",
                source="web",
                sender="web",
                rule_id=None,
                spec_ceiling=_SPEC.risk_ceiling,
            )
        )
        await db.commit()
    worker_session_id = None
    if worker:
        worker_session_id = await seed_session(
            factory, kind="worker", ticket_id=ticket_id
        )
        async with factory() as db:
            stored = await db.get(Ticket, ticket_id)
            assert stored is not None
            stored.worker_session_id = worker_session_id
            await db.commit()
    return ticket_id, worker_session_id


async def _add_pipeline(
    factory,
    *,
    status: PipelineStatus = PipelineStatus.COMPLETED,
    agent_session_id: str | None = None,
    title: str = "日常",
) -> str:
    pipeline_id = new_uuid()
    now = utcnow()
    async with factory() as db:
        db.add(
            Pipeline(
                id=pipeline_id,
                source="agent",
                priority=1,
                task_count=1,
                status=status.value,
                title=title,
                agent_session_id=agent_session_id,
                created_at=now,
                started_at=now - timedelta(minutes=3),
                finished_at=now,
            )
        )
        await db.commit()
    return pipeline_id


async def _ticket(factory, ticket_id: str) -> Ticket | None:
    async with factory() as db:
        return await TicketRepository(db).get(ticket_id)


async def _mandate(factory, ticket_id: str):
    async with factory() as db:
        return await MandateRepository(db).get_by_ticket(ticket_id)


async def _session(factory, session_id: str) -> AgentSession | None:
    async with factory() as db:
        return await db.get(AgentSession, session_id)


async def _items(factory, session_id: str) -> list[AgentInbox]:
    async with factory() as db:
        result = await db.execute(
            select(AgentInbox)
            .where(AgentInbox.session_id == session_id)
            .order_by(AgentInbox.id.asc())
        )
        return list(result.scalars().all())


async def _pending_count(factory, session_id: str) -> int:
    rows = await _items(factory, session_id)
    return sum(1 for row in rows if str(row.status) == InboxStatus.PENDING)


async def _messages(factory, session_id: str) -> list[AgentMessage]:
    async with factory() as db:
        result = await db.execute(
            select(AgentMessage)
            .where(AgentMessage.session_id == session_id)
            .order_by(AgentMessage.seq.asc())
        )
        return list(result.scalars().all())


async def _reports(factory, ticket_id: str) -> list[TicketReport]:
    async with factory() as db:
        result = await db.execute(
            select(TicketReport)
            .where(TicketReport.ticket_id == ticket_id)
            .order_by(TicketReport.id.asc())
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


async def _wait_for(predicate, *, timeout: float = 3.0) -> None:
    loop = asyncio.get_running_loop()
    deadline = loop.time() + timeout
    while loop.time() < deadline:
        if await predicate():
            return
        await asyncio.sleep(0.01)
    raise AssertionError("等待的条件没有在期限内成立")


def test_open_ticket_creates_mandate_session_and_brief(tmp_path) -> None:
    engine, factory = asyncio.run(make_session_factory(tmp_path / "open.db"))
    try:

        async def scenario() -> None:
            harness = _Harness(factory)
            orchestrator_id = await seed_session(factory, kind="orchestrator")
            brief = "帮我刷 1-7 五次，顺便领一下日常奖励"
            opened = await harness.service.open_ticket(
                kind="general",
                title=None,
                brief=brief,
                scope="刷 1-7 五次",
                origin="user",
                strength="explicit",
                source="web",
                sender="web",
                priority=2,
                orchestrator_session_id=orchestrator_id,
            )

            ticket = await _ticket(factory, opened.ticket_id)
            assert ticket is not None
            assert ticket.worker_session_id == opened.worker_session_id
            assert opened.title == "自动标题"
            assert harness.titler.calls == [brief]

            mandate = await _mandate(factory, ticket.id)
            assert mandate is not None
            assert str(mandate.strength) == "explicit"
            assert str(mandate.risk_ceiling) == "consume"

            session = await _session(factory, opened.worker_session_id)
            assert session is not None
            assert str(session.kind) == "worker"
            assert session.ticket_id == ticket.id
            assert session.title == "自动标题"

            items = await _items(factory, opened.worker_session_id)
            pending = [
                row
                for row in items
                if str(row.status) == InboxStatus.PENDING
            ]
            assert len(pending) == 1
            item = pending[0]
            assert str(item.kind) == "system"
            assert item.thread_key == f"ticket:{ticket.id}"
            assert item.payload["text"] == f"总控交给你的任务：\n{brief}"
            assert item.payload["passive"] is False
            assert item.payload["origin"] == "orchestrator"
            # M69 的 create 已经广播 ticket_created，服务不重复发。
            created = [
                data
                for message_type, data in harness.events
                if message_type == "ticket_status"
                and data.get("event") == "ticket_created"
            ]
            assert len(created) == 1
            await harness.close()

        asyncio.run(scenario())
    finally:
        engine.sync_engine.dispose()


def test_submit_pipeline_result_subscribes_the_ticket(tmp_path) -> None:
    engine, factory = asyncio.run(make_session_factory(tmp_path / "subscribe.db"))
    try:

        async def scenario() -> None:
            ticket_id, worker_session_id = await _make_ticket(factory)
            assert worker_session_id is not None
            run_id = "run-subscribe"
            run = SimpleNamespace(
                id=run_id, ticket_id=ticket_id, status=AgentRunStatus.RUNNING.value
            )
            harness = _Harness(
                factory,
                run_service=_RunService({run_id: run}),
                event_bus=RunEventBus(run_id=run_id),
            )
            await harness.service.start()
            pipeline_id = new_uuid()
            assert harness.bus is not None

            await harness.bus.emit(
                "tool_result",
                {
                    "tool_call_id": "call-1",
                    "name": "submit_pipeline",
                    "pipeline_id": pipeline_id,
                },
            )

            async def subscribed() -> bool:
                return bool(await _subscriptions(factory, ticket_id))

            await _wait_for(subscribed)
            subscriptions = await _subscriptions(factory, ticket_id)
            assert [str(row.thread_key) for row in subscriptions] == [
                f"pipeline:{pipeline_id}"
            ]
            assert subscriptions[0].active is True
            await harness.close()

        asyncio.run(scenario())
    finally:
        engine.sync_engine.dispose()


def test_pipeline_finished_wakes_subscribed_worker(tmp_path) -> None:
    engine, factory = asyncio.run(make_session_factory(tmp_path / "finished.db"))
    try:

        async def scenario() -> None:
            ticket_id, worker_session_id = await _make_ticket(factory)
            assert worker_session_id is not None
            pipeline_id = await _add_pipeline(
                factory,
                status=PipelineStatus.COMPLETED,
                agent_session_id=worker_session_id,
            )
            harness = _Harness(factory)
            await harness.router.subscribe(ticket_id, f"pipeline:{pipeline_id}")

            started = asyncio.Event()

            async def fake_turn(session_id: str, handle) -> bool:
                started.set()
                return False

            harness.service._run_turn = fake_turn  # type: ignore[method-assign]
            await harness.service.on_pipeline_finished(
                pipeline_id, PipelineStatus.COMPLETED
            )

            items = await _items(factory, worker_session_id)
            thread_events = [
                item for item in items if str(item.kind) == InboxKind.THREAD_EVENT
            ]
            assert len(thread_events) == 1
            payload = thread_events[0].payload
            assert payload["thread_key"] == f"pipeline:{pipeline_id}"
            assert payload["pipeline_id"] == pipeline_id
            assert payload["status"] == "completed"
            assert "流水线「日常」已完成" in payload["summary"]
            assert "，用时 3 分钟" in payload["summary"]
            await asyncio.wait_for(started.wait(), timeout=3.0)
            await harness.close()

        asyncio.run(scenario())
    finally:
        engine.sync_engine.dispose()


def test_pipeline_finished_before_subscription_uses_owner_lookup(tmp_path) -> None:
    engine, factory = asyncio.run(make_session_factory(tmp_path / "race.db"))
    try:

        async def scenario() -> None:
            ticket_id, worker_session_id = await _make_ticket(factory)
            assert worker_session_id is not None
            pipeline_id = await _add_pipeline(
                factory,
                status=PipelineStatus.FAILED,
                agent_session_id=worker_session_id,
            )
            harness = _Harness(factory)
            started = asyncio.Event()

            async def fake_turn(session_id: str, handle) -> bool:
                started.set()
                return False

            harness.service._run_turn = fake_turn  # type: ignore[method-assign]
            await harness.service.on_pipeline_finished(pipeline_id, "failed")

            subscriptions = await _subscriptions(factory, ticket_id)
            assert [str(row.thread_key) for row in subscriptions] == [
                f"pipeline:{pipeline_id}"
            ]
            items = await _items(factory, worker_session_id)
            thread_events = [
                item for item in items if str(item.kind) == InboxKind.THREAD_EVENT
            ]
            assert len(thread_events) == 1
            assert "失败" in thread_events[0].payload["summary"]
            await asyncio.wait_for(started.wait(), timeout=3.0)
            await harness.close()

        asyncio.run(scenario())
    finally:
        engine.sync_engine.dispose()


def test_late_pipeline_event_for_ended_ticket_is_dropped(tmp_path) -> None:
    engine, factory = asyncio.run(make_session_factory(tmp_path / "late.db"))
    try:

        async def scenario() -> None:
            orchestrator_id = await seed_session(factory, kind="orchestrator")
            ticket_id, worker_session_id = await _make_ticket(
                factory,
                status=TicketStatus.DONE,
                orchestrator_session_id=orchestrator_id,
            )
            assert worker_session_id is not None
            pipeline_id = await _add_pipeline(
                factory,
                status=PipelineStatus.COMPLETED,
                agent_session_id=worker_session_id,
            )
            harness = _Harness(factory)
            await harness.service.on_pipeline_finished(pipeline_id, "completed")

            assert await _items(factory, worker_session_id) == []
            assert await _items(factory, orchestrator_id) == []
            assert await _subscriptions(factory, ticket_id) == []
            await harness.close()

        asyncio.run(scenario())
    finally:
        engine.sync_engine.dispose()


def test_ticket_message_reaches_worker_with_passive_note(tmp_path) -> None:
    engine, factory = asyncio.run(make_session_factory(tmp_path / "message.db"))
    try:

        async def scenario() -> None:
            ticket_id, worker_session_id = await _make_ticket(
                factory, title="刷 1-7"
            )
            assert worker_session_id is not None
            harness = _Harness(factory)
            content = "改成刷 1-7 十次，" + "再领一次日常奖励" * 20
            mentions = [{"type": "knowledge", "id": "k-1", "label": "日常"}]

            posted = await harness.service.post_user_message(
                ticket_id, content, mentions
            )

            items = await _items(factory, worker_session_id)
            assert len(items) == 1
            assert str(items[0].kind) == InboxKind.USER_MESSAGE
            assert items[0].payload["mentions"] == mentions
            assert items[0].payload["text"] == content
            messages = await _messages(factory, worker_session_id)
            assert [(message.role, message.content) for message in messages] == [
                ("user", content)
            ]
            assert posted.session_id == worker_session_id
            assert posted.seq == messages[0].seq
            assert posted.inbox_id == items[0].id

            assert len(harness.orchestrator.notifications) == 1
            note = harness.orchestrator.notifications[0]
            assert note["passive"] is True
            assert note["ticket_id"] == ticket_id
            assert note["ref_id"] == f"ticket:{ticket_id}:msg:{posted.seq}"
            assert note["text"] == f"用户在工单「刷 1-7」里说：{content[:200]}"
            await harness.close()

        asyncio.run(scenario())
    finally:
        engine.sync_engine.dispose()


def test_cancel_interrupts_drops_inbox_keeps_pipeline_and_notifies(tmp_path) -> None:
    engine, factory = asyncio.run(make_session_factory(tmp_path / "cancel.db"))
    try:

        async def scenario() -> None:
            ticket_id, worker_session_id = await _make_ticket(factory)
            assert worker_session_id is not None
            pipeline_id = await _add_pipeline(
                factory, status=PipelineStatus.RUNNING
            )
            harness = _Harness(factory)
            await harness.inbox.put(
                worker_session_id,
                InboxKind.SYSTEM,
                payload={"text": "补充一点", "passive": False},
            )
            started = asyncio.Event()
            reasons: list[str] = []

            async def blocking_turn(session_id: str, handle) -> bool:
                started.set()
                await handle.cancel_event.wait()
                reasons.append(handle.cancel_reason)
                return True

            harness.service._run_turn = blocking_turn  # type: ignore[method-assign]
            harness.service.wake(worker_session_id)
            await asyncio.wait_for(started.wait(), timeout=3.0)

            detail = await harness.service.cancel(
                ticket_id, reason="不做了", actor="user"
            )

            assert str(detail.ticket.status) == TicketStatus.CANCELLED.value
            assert reasons == ["cancelled"]
            assert await _pending_count(factory, worker_session_id) == 0
            stored = await _ticket(factory, ticket_id)
            assert stored is not None
            assert stored.result == {"cancelled_by": "user", "reason": "不做了"}
            async with factory() as db:
                pipeline = await db.get(Pipeline, pipeline_id)
            assert pipeline is not None
            assert str(pipeline.status) == PipelineStatus.RUNNING.value

            updated = [
                data
                for message_type, data in harness.events
                if message_type == "ticket_status"
                and data.get("event") == "ticket_updated"
            ]
            assert len(updated) == 1
            assert updated[0]["status"] == TicketStatus.CANCELLED.value

            assert len(harness.orchestrator.notifications) == 1
            note = harness.orchestrator.notifications[0]
            assert note["passive"] is True
            assert note["ticket_id"] == ticket_id
            assert note["ref_id"] == f"ticket:{ticket_id}:cancelled"
            assert note["text"] == "用户取消了工单「日常」：不做了"
            await harness.close()

        asyncio.run(scenario())
    finally:
        engine.sync_engine.dispose()


def test_ready_report_ends_ticket(tmp_path) -> None:
    engine, factory = asyncio.run(make_session_factory(tmp_path / "ready.db"))
    try:

        async def scenario() -> None:
            orchestrator_id = await seed_session(factory, kind="orchestrator")
            ticket_id, worker_session_id = await _make_ticket(
                factory, orchestrator_session_id=orchestrator_id
            )
            assert worker_session_id is not None
            await _add_pipeline(
                factory,
                status=PipelineStatus.COMPLETED,
                agent_session_id=worker_session_id,
            )
            harness = _Harness(factory)
            await harness.router.subscribe(ticket_id, f"pipeline:{new_uuid()}")
            run_id = await seed_run(
                factory, session_id=worker_session_id, ticket_id=ticket_id
            )
            emitted: list[tuple[str, dict]] = []

            async def emit(name: str, data: dict) -> None:
                emitted.append((name, data))

            ctx = RunContext(
                run_id=run_id,
                root_run_id="run-report",
                parent_run_id=None,
                session_id=worker_session_id,
                ticket_id=ticket_id,
                spec=_SPEC,
                app=harness.app,
                emit=emit,
            )
            result = await harness.service.submit_report(
                ctx,
                ReportParams(status=ReportStatus.READY, summary="做完了"),
            )

            assert isinstance(result["report_id"], int)
            assert result["ticket_status"] == TicketStatus.DONE.value
            assert result["forwarded"] is True

            stored = await _ticket(factory, ticket_id)
            assert stored is not None
            assert str(stored.status) == TicketStatus.DONE.value
            session = await _session(factory, worker_session_id)
            assert session is not None
            assert str(session.status) == "finished"
            subscriptions = await _subscriptions(factory, ticket_id)
            assert all(row.active is False for row in subscriptions)

            assert len(emitted) == 1
            assert emitted[0][0] == "report"
            assert emitted[0][1]["status"] == "ready"
            assert emitted[0][1]["ticket_id"] == ticket_id
            await harness.close()

        asyncio.run(scenario())
    finally:
        engine.sync_engine.dispose()


def test_terminal_report_inside_running_pump_does_not_cancel_the_turn(tmp_path) -> None:
    """回归：``submit_report`` 在泵的这一轮里收尾，不能把当前任务自己 cancel 掉。"""
    engine, factory = asyncio.run(make_session_factory(tmp_path / "self-close.db"))
    try:

        async def scenario() -> None:
            ticket_id, worker_session_id = await _make_ticket(factory)
            assert worker_session_id is not None
            run_id = await seed_run(
                factory, session_id=worker_session_id, ticket_id=ticket_id
            )
            harness = _Harness(factory)
            await harness.inbox.put(
                worker_session_id,
                InboxKind.SYSTEM,
                payload={"text": "开始干活", "passive": False},
            )
            finished = asyncio.Event()

            async def reporting_turn(session_id: str, handle) -> bool:
                ctx = RunContext(
                    run_id=run_id,
                    root_run_id=run_id,
                    parent_run_id=None,
                    session_id=session_id,
                    ticket_id=ticket_id,
                    spec=_SPEC,
                    app=harness.app,
                )
                await harness.service.submit_report(
                    ctx,
                    ReportParams(status=ReportStatus.READY, summary="做完了"),
                )
                finished.set()
                return True

            harness.service._run_turn = reporting_turn  # type: ignore[method-assign]
            harness.service.wake(worker_session_id)
            await asyncio.wait_for(finished.wait(), timeout=3.0)

            stored = await _ticket(factory, ticket_id)
            assert stored is not None
            assert str(stored.status) == TicketStatus.DONE.value
            await harness.close()

        asyncio.run(scenario())
    finally:
        engine.sync_engine.dispose()


def test_run_turn_claims_inbox_persists_messages_and_fills_report(
    tmp_path, monkeypatch
) -> None:
    """一轮真跑：领收件箱、落消息、收尾补汇报（§4.5.3 七步）。"""
    engine, factory = asyncio.run(make_session_factory(tmp_path / "turn.db"))
    try:

        async def scenario() -> None:
            ticket_id, worker_session_id = await _make_ticket(factory)
            assert worker_session_id is not None
            run_service = RunService(factory, boot_id="boot-test")
            harness = _Harness(factory, run_service=run_service)
            await harness.inbox.put(
                worker_session_id,
                InboxKind.SYSTEM,
                payload={"text": "开始干活", "passive": False},
            )
            seen: dict = {}

            async def fake_run_agent(
                app,
                spec,
                session,
                run,
                *,
                inbox_items,
                cancel_event,
                cancel_reason,
                state,
                parent=None,
            ) -> RunOutcome:
                seen["items"] = list(inbox_items)
                seen["role_card"] = state["role_card"]
                seen["ceiling"] = state["mandate.ceiling"]
                seen["ticket_title"] = state["ticket.title"]
                return RunOutcome(status="succeeded", finish_reason="stop", final_text="干完了")

            monkeypatch.setattr(
                "maa_api.agent.workers.service.run_agent", fake_run_agent
            )
            handle = TurnHandle()
            assert await harness.service._run_turn(worker_session_id, handle) is True
            assert handle.run_id is not None

            items = await _items(factory, worker_session_id)
            assert [str(item.status) for item in items] == [InboxStatus.CONSUMED.value]
            assert len(seen["items"]) == 1
            assert seen["ceiling"] == "consume"
            assert seen["ticket_title"] == "日常"
            assert "## 工单卡" in seen["role_card"]

            messages = await _messages(factory, worker_session_id)
            assert [(message.role, message.content) for message in messages] == [
                ("user", "【系统】开始干活")
            ]
            reports = await _reports(factory, ticket_id)
            assert [(str(row.status), row.summary) for row in reports] == [
                ("progress", "干完了")
            ]
            assert reports[0].details == {"auto": True, "finish_reason": "stop"}
            # 假运行没有自己收尾运行行：finally 按失败收掉，不留 running 行。
            stored_run = await run_service.get(handle.run_id)
            assert stored_run is not None
            assert str(stored_run.status) == AgentRunStatus.FAILED.value
            await run_service.close()
            await harness.close()

        asyncio.run(scenario())
    finally:
        engine.sync_engine.dispose()
