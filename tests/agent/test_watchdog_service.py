"""``WatchdogService`` 的巡检、动作、通知与去重（M83 spec §4.5/§6，T2）。

测试纪律：

- 临时库与生产同口径（``tests/fakes/v3_db.py``）：告警、运行、工单、流水线、
  收件箱都是真实表，只有设备、内核、模型与执行器是替身；
- 不连真实设备/模型：``WatchdogService`` 的依赖全部注入；
- 用例形态是同步函数 + ``asyncio.run``（本仓没有 pytest-asyncio）。

覆盖 spec §6 的五条：record 不动、auto 只取消执行中的流水线、auto 唤醒但不取消
队列头、默认跳过重启内核、interval 自闸；另补通知四条、动作失败、条件消失解除、
中断入口解析与 blocked 汇报。
"""

from __future__ import annotations

import asyncio
from dataclasses import dataclass, field
from datetime import datetime, timedelta
from types import SimpleNamespace
from typing import Any

from sqlalchemy import select

from maa_api.agent.tickets.inbox import InboxService
from maa_api.agent.tickets.threads import ThreadRouter
from maa_api.agent.watchdog.recorder import WatchdogRecorder
from maa_api.agent.watchdog.service import WatchdogService
from maa_api.db.models import (
    AgentInbox,
    AgentRun,
    AgentSession,
    Pipeline,
    Ticket,
    WatchdogAlert,
    new_uuid,
)
from maa_api.domain.enums import (
    AgentRunKind,
    AgentRunStatus,
    AgentSessionKind,
    AlertActionStatus,
    InboxKind,
    PipelineSource,
    PipelineStatus,
    Priority,
    ReportStatus,
    TicketStatus,
)
from maa_api.settings import Settings
from tests.fakes.v3_db import make_session_factory

NOW = datetime(2026, 11, 5, 12, 0, 0)
BOOT_ID = "boot-watchdog-test"


async def _noop_sleep(_seconds: float) -> None:
    return None


class _Runner:
    """执行器替身：只记录 ``wake`` / ``request_cancel``，不碰队列。"""

    def __init__(
        self,
        *,
        running: bool = True,
        active_pipeline_id: str | None = None,
    ) -> None:
        self.running = running
        self.active_pipeline_id = active_pipeline_id
        self.wake_calls = 0
        self.cancel_calls: list[str] = []
        self.cancel_error: Exception | None = None

    def wake(self) -> None:
        self.wake_calls += 1

    async def request_cancel(self, pipeline_id: str) -> None:
        self.cancel_calls.append(pipeline_id)
        if self.cancel_error is not None:
            raise self.cancel_error


class _WorkerService:
    def __init__(self) -> None:
        self.calls: list[str] = []

    async def interrupt(self, ticket_id: str) -> dict[str, Any]:
        self.calls.append(ticket_id)
        return {"ticket_id": ticket_id, "interrupted": True, "turn_running": False}


class _OrchestratorService:
    def __init__(self) -> None:
        self.calls = 0

    async def interrupt(self) -> dict[str, Any]:
        self.calls += 1
        return {"session_id": "orchestrator", "interrupted": True, "turn_running": False}


class _TicketService:
    def __init__(self) -> None:
        self.reports: list[tuple[str, Any]] = []

    async def record_report(self, ticket_id: str, report: Any, run_id: Any = None) -> Any:
        self.reports.append((ticket_id, report))
        return SimpleNamespace(ticket_id=ticket_id, report=report, run_id=run_id)


class _WebPush:
    def __init__(self) -> None:
        self.payloads: list[dict[str, Any]] = []

    async def broadcast(self, payload: dict[str, Any]) -> None:
        self.payloads.append(payload)


@dataclass
class _Harness:
    factory: Any
    settings: Settings
    runner: _Runner
    worker: _WorkerService
    orchestrator: _OrchestratorService
    tickets: _TicketService
    webpush: _WebPush
    recorder: WatchdogRecorder
    restart_calls: list[str] = field(default_factory=list)
    broadcasts: list[tuple[str, dict[str, Any]]] = field(default_factory=list)
    has_visible_client: bool = True
    thread_router: ThreadRouter | None = None
    service: WatchdogService | None = None

    def build(self) -> WatchdogService:
        async def restart_core() -> None:
            self.restart_calls.append("restart")

        self.service = WatchdogService(
            self.factory,
            recorder=self.recorder,
            pipeline_runner=self.runner,
            queue_service=SimpleNamespace(paused=False),
            core_supervisor=SimpleNamespace(state="ready"),
            device_manager=SimpleNamespace(state="connected"),
            run_service=SimpleNamespace(boot_id=BOOT_ID),
            ticket_service=self.tickets,
            worker_service=self.worker,
            orchestrator_service=self.orchestrator,
            thread_router=self.thread_router,
            webpush_service=self.webpush,
            has_visible_client=lambda: self.has_visible_client,
            restart_core=restart_core,
            broadcast=lambda kind, data: self.broadcasts.append((kind, data)),
            settings_provider=lambda: self.settings,
            clock=lambda: NOW,
            sleep=_noop_sleep,
            interrupt_grace_seconds=0.0,
        )
        return self.service

    async def alerts(self) -> list[WatchdogAlert]:
        async with self.factory() as session:
            return list(
                (
                    await session.execute(
                        select(WatchdogAlert).order_by(WatchdogAlert.id.asc())
                    )
                )
                .scalars()
                .all()
            )


async def _make_harness(tmp_path: Any, name: str, **kwargs: Any) -> tuple[_Harness, Any]:
    engine, factory = await make_session_factory(tmp_path / f"{name}.db")
    settings = Settings()
    for key, value in kwargs.items():
        setattr(settings.watchdog, key, value)
    harness = _Harness(
        factory=factory,
        settings=settings,
        runner=_Runner(),
        worker=_WorkerService(),
        orchestrator=_OrchestratorService(),
        tickets=_TicketService(),
        webpush=_WebPush(),
        recorder=WatchdogRecorder(clock=lambda: NOW),
    )
    return harness, engine


async def _seed_ticket(
    factory: Any,
    *,
    updated_at: datetime,
    status: str = TicketStatus.OPEN.value,
) -> str:
    ticket = Ticket(
        title="值守测试工单",
        kind="sweeper",
        status=status,
        origin="user",
        updated_at=updated_at,
        created_at=updated_at - timedelta(hours=1),
    )
    async with factory() as session:
        session.add(ticket)
        await session.commit()
    return str(ticket.id)


async def _seed_session(
    factory: Any,
    *,
    kind: str,
    ticket_id: str | None = None,
) -> str:
    session_row = AgentSession(
        model="test-model",
        kind=kind,
        status="active",
        ticket_id=ticket_id,
    )
    async with factory() as session:
        session.add(session_row)
        await session.commit()
    return str(session_row.id)


async def _attach_worker_session(factory: Any, ticket_id: str, session_id: str) -> None:
    async with factory() as session:
        ticket = await session.get(Ticket, ticket_id)
        assert ticket is not None
        ticket.worker_session_id = session_id
        session.add(ticket)
        await session.commit()


async def _seed_run(
    factory: Any,
    *,
    session_id: str | None,
    ticket_id: str | None = None,
    heartbeat_at: datetime,
    status: str = AgentRunStatus.RUNNING.value,
    kind: str = AgentRunKind.WORKER_TURN.value,
) -> str:
    run_id = new_uuid()
    run = AgentRun(
        id=run_id,
        root_run_id=run_id,
        session_id=session_id,
        ticket_id=ticket_id,
        agent_name="watchdog-test",
        kind=kind,
        status=status,
        boot_id=BOOT_ID,
        heartbeat_at=heartbeat_at,
        started_at=heartbeat_at - timedelta(minutes=2),
    )
    async with factory() as session:
        session.add(run)
        await session.commit()
    return run_id


async def _seed_pipeline(
    factory: Any,
    *,
    status: str,
    created_at: datetime,
    started_at: datetime | None = None,
) -> str:
    pipeline = Pipeline(
        source=PipelineSource.MANUAL.value,
        priority=int(Priority.MANUAL),
        status=status,
        task_count=1,
        created_at=created_at,
        started_at=started_at,
    )
    async with factory() as session:
        session.add(pipeline)
        await session.commit()
    return str(pipeline.id)


async def _close(harness: _Harness, engine: Any) -> None:
    if harness.service is not None:
        await harness.service.close()
    engine.sync_engine.dispose()


# ---------------------------------------------------------------------------
# spec §6：record 不动
# ---------------------------------------------------------------------------
def test_record_mode_does_not_act(tmp_path) -> None:
    """record 档：告警写 recorded，中断、取消、唤醒与重启都不被调用。"""

    async def scenario() -> None:
        harness, engine = await _make_harness(tmp_path, "record")
        try:
            ticket_id = await _seed_ticket(harness.factory, updated_at=NOW)
            session_id = await _seed_session(
                harness.factory, kind=AgentSessionKind.WORKER.value, ticket_id=ticket_id
            )
            await _attach_worker_session(harness.factory, ticket_id, session_id)
            await _seed_run(
                harness.factory,
                session_id=session_id,
                ticket_id=ticket_id,
                heartbeat_at=NOW - timedelta(seconds=120),
            )
            pipeline_id = await _seed_pipeline(
                harness.factory,
                status=PipelineStatus.RUNNING.value,
                created_at=NOW - timedelta(minutes=20),
                started_at=NOW - timedelta(minutes=20),
            )
            harness.runner.active_pipeline_id = pipeline_id
            harness.recorder.note_core_callback(at=NOW - timedelta(minutes=11))
            service = harness.build()

            processed = await service.run_once(NOW)

            checks = {_str(alert.check) for alert in processed}
            assert checks == {"run_heartbeat_stale", "pipeline_no_callback"}
            rows = await harness.alerts()
            assert {row.action_status for row in rows} == {
                AlertActionStatus.RECORDED.value
            }
            assert harness.runner.cancel_calls == []
            assert harness.runner.wake_calls == 0
            assert harness.worker.calls == []
            assert harness.orchestrator.calls == 0
            assert harness.restart_calls == []
            assert service.last_run_at == NOW
        finally:
            await _close(harness, engine)

    asyncio.run(scenario())


# ---------------------------------------------------------------------------
# spec §6：auto 只取消执行中的那条
# ---------------------------------------------------------------------------
def test_auto_stops_frozen_pipeline(tmp_path) -> None:
    """``pipeline_no_callback`` 成立时只对正在执行的那条调 ``request_cancel``。"""

    async def scenario() -> None:
        harness, engine = await _make_harness(tmp_path, "frozen", mode="auto")
        try:
            running_id = await _seed_pipeline(
                harness.factory,
                status=PipelineStatus.RUNNING.value,
                created_at=NOW - timedelta(minutes=20),
                started_at=NOW - timedelta(minutes=20),
            )
            pending_id = await _seed_pipeline(
                harness.factory,
                status=PipelineStatus.PENDING.value,
                created_at=NOW - timedelta(minutes=11),
            )
            harness.runner.active_pipeline_id = running_id
            harness.recorder.note_core_callback(at=NOW - timedelta(minutes=11))
            service = harness.build()

            await service.run_once(NOW)

            assert harness.runner.cancel_calls == [running_id]
            async with harness.factory() as session:
                pending = await session.get(Pipeline, pending_id)
            assert pending is not None
            assert _str(pending.status) == PipelineStatus.PENDING.value
            alert = next(
                row
                for row in await harness.alerts()
                if row.check == "pipeline_no_callback"
            )
            assert alert.action == "stop_pipeline"
            assert alert.action_status == AlertActionStatus.DONE.value
        finally:
            await _close(harness, engine)

    asyncio.run(scenario())


# ---------------------------------------------------------------------------
# spec §6：auto 唤醒队列但不取消队列头
# ---------------------------------------------------------------------------
def test_auto_wakes_queue_without_cancel(tmp_path) -> None:
    """``queue_stuck`` 成立时唤醒执行器，队列头仍在且没有被取消。"""

    async def scenario() -> None:
        harness, engine = await _make_harness(tmp_path, "queue", mode="auto")
        try:
            pending_id = await _seed_pipeline(
                harness.factory,
                status=PipelineStatus.PENDING.value,
                created_at=NOW - timedelta(minutes=11),
            )
            service = harness.build()

            await service.run_once(NOW)

            assert harness.runner.wake_calls == 1
            assert harness.runner.cancel_calls == []
            async with harness.factory() as session:
                pending = await session.get(Pipeline, pending_id)
            assert pending is not None
            assert _str(pending.status) == PipelineStatus.PENDING.value
            alert = next(
                row for row in await harness.alerts() if row.check == "queue_stuck"
            )
            assert alert.action == "wake_queue"
            assert alert.action_status == AlertActionStatus.DONE.value
        finally:
            await _close(harness, engine)

    asyncio.run(scenario())


# ---------------------------------------------------------------------------
# spec §6：默认跳过重启内核
# ---------------------------------------------------------------------------
def test_restart_skipped_by_default(tmp_path) -> None:
    """``allow_core_restart`` 默认 false：动作落 skipped，重启入口不被调用。"""

    async def scenario() -> None:
        harness, engine = await _make_harness(tmp_path, "restart-off", mode="auto")
        try:
            service = harness.build()
            # 内核 failed 不依赖持续时长；替身换成 failed。
            service._core_supervisor = SimpleNamespace(state="failed")

            await service.run_once(NOW)

            assert harness.restart_calls == []
            alert = next(
                row
                for row in await harness.alerts()
                if row.check == "core_not_ready"
            )
            assert alert.action == "restart_core"
            assert alert.action_status == AlertActionStatus.SKIPPED.value
            assert alert.evidence["action"]["reason"] == "allow_core_restart_disabled"
        finally:
            await _close(harness, engine)

    asyncio.run(scenario())


# ---------------------------------------------------------------------------
# spec §6：interval 自闸
# ---------------------------------------------------------------------------
def test_interval_gate(tmp_path) -> None:
    """默认 300 秒内第二次 ``run_once`` 不写新告警、也不刷新首次时间。"""

    async def scenario() -> None:
        harness, engine = await _make_harness(tmp_path, "interval")
        try:
            ticket_id = await _seed_ticket(harness.factory, updated_at=NOW)
            session_id = await _seed_session(
                harness.factory, kind=AgentSessionKind.WORKER.value, ticket_id=ticket_id
            )
            await _attach_worker_session(harness.factory, ticket_id, session_id)
            await _seed_run(
                harness.factory,
                session_id=session_id,
                ticket_id=ticket_id,
                heartbeat_at=NOW - timedelta(seconds=120),
            )
            service = harness.build()

            first = await service.run_once(NOW)
            assert len(first) == 1
            assert service.last_run_at == NOW
            async with harness.factory() as session:
                stored = (
                    await session.execute(select(WatchdogAlert))
                ).scalars().all()
            created_at = stored[0].created_at
            alert_id = int(stored[0].id)

            second = await service.run_once(NOW + timedelta(seconds=299))
            assert second == []
            assert service.last_run_at == NOW
            async with harness.factory() as session:
                rows = (await session.execute(select(WatchdogAlert))).scalars().all()
            assert len(rows) == 1
            assert int(rows[0].id) == alert_id
            assert rows[0].created_at == created_at

            # 到点后允许再跑一轮：仍是同一行，证据写入 latest。
            third = await service.run_once(NOW + timedelta(seconds=300))
            assert len(third) == 1
            assert service.last_run_at == NOW + timedelta(seconds=300)
            async with harness.factory() as session:
                rows = (await session.execute(select(WatchdogAlert))).scalars().all()
            assert len(rows) == 1
            assert rows[0].created_at == created_at
            assert "latest" in rows[0].evidence
        finally:
            await _close(harness, engine)

    asyncio.run(scenario())


# ---------------------------------------------------------------------------
# 补充：允许时重启、动作失败、通知、解除、中断、blocked
# ---------------------------------------------------------------------------
def test_restart_core_when_allowed_without_pipeline(tmp_path) -> None:
    """``allow_core_restart`` 为真且没有流水线时重启内核并落 done。"""

    async def scenario() -> None:
        harness, engine = await _make_harness(
            tmp_path, "restart-on", mode="auto", allow_core_restart=True
        )
        try:
            service = harness.build()
            service._core_supervisor = SimpleNamespace(state="failed")

            await service.run_once(NOW)

            assert harness.restart_calls == ["restart"]
            alert = next(
                row
                for row in await harness.alerts()
                if row.check == "core_not_ready"
            )
            assert alert.action_status == AlertActionStatus.DONE.value
        finally:
            await _close(harness, engine)

    asyncio.run(scenario())


def test_action_failure_records_error_without_raising(tmp_path) -> None:
    """动作抛异常：``action_status=failed``、``evidence.error`` 有类型与消息。"""

    async def scenario() -> None:
        harness, engine = await _make_harness(tmp_path, "action-fail", mode="auto")
        try:
            running_id = await _seed_pipeline(
                harness.factory,
                status=PipelineStatus.RUNNING.value,
                created_at=NOW - timedelta(minutes=20),
                started_at=NOW - timedelta(minutes=20),
            )
            harness.runner.active_pipeline_id = running_id
            harness.runner.cancel_error = RuntimeError("boom")
            harness.recorder.note_core_callback(at=NOW - timedelta(minutes=11))
            service = harness.build()

            processed = await service.run_once(NOW)

            assert processed
            alert = next(
                row
                for row in await harness.alerts()
                if row.check == "pipeline_no_callback"
            )
            assert alert.action_status == AlertActionStatus.FAILED.value
            assert alert.evidence["error"]["type"] == "RuntimeError"
            assert "boom" in alert.evidence["error"]["message"]
        finally:
            await _close(harness, engine)

    asyncio.run(scenario())


def test_critical_notifies_once_and_warn_stays_in_list(tmp_path) -> None:
    """critical 新插入：WS + 总控 system + 无可见页面时的推送；warn 只进列表。"""

    async def scenario() -> None:
        harness, engine = await _make_harness(tmp_path, "notify")
        try:
            orchestrator_id = await _seed_session(
                harness.factory, kind=AgentSessionKind.ORCHESTRATOR.value
            )
            harness.thread_router = ThreadRouter(
                harness.factory, InboxService(harness.factory)
            )
            harness.has_visible_client = False
            ticket_id = await _seed_ticket(harness.factory, updated_at=NOW)
            session_id = await _seed_session(
                harness.factory, kind=AgentSessionKind.WORKER.value, ticket_id=ticket_id
            )
            await _attach_worker_session(harness.factory, ticket_id, session_id)
            await _seed_run(
                harness.factory,
                session_id=session_id,
                ticket_id=ticket_id,
                heartbeat_at=NOW - timedelta(seconds=120),
            )
            pipeline_id = await _seed_pipeline(
                harness.factory,
                status=PipelineStatus.RUNNING.value,
                created_at=NOW - timedelta(minutes=20),
                started_at=NOW - timedelta(minutes=20),
            )
            harness.runner.active_pipeline_id = pipeline_id
            harness.recorder.note_core_callback(at=NOW - timedelta(minutes=11))
            service = harness.build()

            await service.run_once(NOW)

            rows = await harness.alerts()
            assert {row.check for row in rows} == {
                "run_heartbeat_stale",
                "pipeline_no_callback",
            }
            critical = next(row for row in rows if row.check == "pipeline_no_callback")
            # 1) WS：house style 的 agent_event / watchdog_alert。
            assert len(harness.broadcasts) == 1
            kind, envelope = harness.broadcasts[0]
            assert kind == "agent_event"
            assert envelope["event"] == "watchdog_alert"
            assert envelope["session_id"] is None
            assert envelope["payload"]["alert_id"] == critical.id
            assert envelope["payload"]["check"] == "pipeline_no_callback"
            # 2) 总控收件箱：system 项，正文带 alert id。
            async with harness.factory() as session:
                inbox = (
                    (
                        await session.execute(
                            select(AgentInbox).where(
                                AgentInbox.session_id == orchestrator_id,
                                AgentInbox.kind == InboxKind.SYSTEM.value,
                            )
                        )
                    )
                    .scalars()
                    .all()
                )
            assert len(inbox) == 1
            assert inbox[0].payload["event"] == "watchdog.alert"
            assert inbox[0].payload["alert_id"] == critical.id
            assert f"#{critical.id}" in inbox[0].payload["text"]
            assert critical.message in inbox[0].payload["text"]
            # 3) 无可见页面 → Web Push。
            assert len(harness.webpush.payloads) == 1
            assert harness.webpush.payloads[0]["tag"] == f"watchdog:{critical.id}"
            # 4) 第二轮回合告警已存在：不重复通知。
            await service.run_once(NOW + timedelta(seconds=301))
            assert len(harness.broadcasts) == 1
            assert len(harness.webpush.payloads) == 1
        finally:
            await _close(harness, engine)

    asyncio.run(scenario())


def test_condition_cleared_resolves(tmp_path) -> None:
    """条件消失后下一轮把未解除告警写 ``resolved_at``。"""

    async def scenario() -> None:
        harness, engine = await _make_harness(tmp_path, "resolve")
        try:
            ticket_id = await _seed_ticket(harness.factory, updated_at=NOW)
            session_id = await _seed_session(
                harness.factory, kind=AgentSessionKind.WORKER.value, ticket_id=ticket_id
            )
            await _attach_worker_session(harness.factory, ticket_id, session_id)
            run_id = await _seed_run(
                harness.factory,
                session_id=session_id,
                ticket_id=ticket_id,
                heartbeat_at=NOW - timedelta(seconds=120),
            )
            service = harness.build()
            await service.run_once(NOW)

            second = NOW + timedelta(seconds=301)
            async with harness.factory() as session:
                run = await session.get(AgentRun, run_id)
                assert run is not None
                run.heartbeat_at = second
                session.add(run)
                await session.commit()

            await service.run_once(second)

            rows = await harness.alerts()
            assert len(rows) == 1
            assert rows[0].resolved_at is not None
        finally:
            await _close(harness, engine)

    asyncio.run(scenario())


def test_auto_interrupts_worker_run(tmp_path) -> None:
    """Worker 运行失联：调 ``WorkerService.interrupt(ticket_id)``，之后写 interrupted。"""

    async def scenario() -> None:
        harness, engine = await _make_harness(tmp_path, "interrupt-worker", mode="auto")
        try:
            ticket_id = await _seed_ticket(harness.factory, updated_at=NOW)
            session_id = await _seed_session(
                harness.factory, kind=AgentSessionKind.WORKER.value, ticket_id=ticket_id
            )
            await _attach_worker_session(harness.factory, ticket_id, session_id)
            run_id = await _seed_run(
                harness.factory,
                session_id=session_id,
                ticket_id=ticket_id,
                heartbeat_at=NOW - timedelta(seconds=120),
            )
            service = harness.build()

            await service.run_once(NOW)

            assert harness.worker.calls == [ticket_id]
            assert harness.orchestrator.calls == 0
            async with harness.factory() as session:
                run = await session.get(AgentRun, run_id)
            assert run is not None
            assert _str(run.status) == AgentRunStatus.INTERRUPTED.value
            alert = next(
                row
                for row in await harness.alerts()
                if row.check == "run_heartbeat_stale"
            )
            assert alert.action == "interrupt_run"
            assert alert.action_status == AlertActionStatus.DONE.value
            assert alert.evidence["action"]["entry"] == "worker"
        finally:
            await _close(harness, engine)

    asyncio.run(scenario())


def test_auto_interrupts_orchestrator_run(tmp_path) -> None:
    """总控会话失联：调 ``OrchestratorService.interrupt()``，不走 Worker 入口。"""

    async def scenario() -> None:
        harness, engine = await _make_harness(
            tmp_path, "interrupt-orchestrator", mode="auto"
        )
        try:
            session_id = await _seed_session(
                harness.factory, kind=AgentSessionKind.ORCHESTRATOR.value
            )
            run_id = await _seed_run(
                harness.factory,
                session_id=session_id,
                heartbeat_at=NOW - timedelta(seconds=120),
                kind=AgentRunKind.ORCHESTRATOR_TURN.value,
            )
            service = harness.build()

            await service.run_once(NOW)

            assert harness.orchestrator.calls == 1
            assert harness.worker.calls == []
            async with harness.factory() as session:
                run = await session.get(AgentRun, run_id)
            assert run is not None
            assert _str(run.status) == AgentRunStatus.INTERRUPTED.value
        finally:
            await _close(harness, engine)

    asyncio.run(scenario())


def test_auto_marks_stale_ticket_blocked(tmp_path) -> None:
    """``ticket_stale`` 在 auto 档写 blocked 汇报，human_action 问继续还是取消。"""

    async def scenario() -> None:
        harness, engine = await _make_harness(tmp_path, "blocked", mode="auto")
        try:
            ticket_id = await _seed_ticket(
                harness.factory, updated_at=NOW - timedelta(minutes=31)
            )
            service = harness.build()

            await service.run_once(NOW)

            assert len(harness.tickets.reports) == 1
            reported_ticket, report = harness.tickets.reports[0]
            assert reported_ticket == ticket_id
            assert report.status is ReportStatus.BLOCKED
            assert report.human_action is not None
            assert report.human_action.type == "decision"
            assert report.human_action.ref_id is not None
            alert = next(
                row for row in await harness.alerts() if row.check == "ticket_stale"
            )
            assert alert.action == "mark_blocked"
            assert alert.action_status == AlertActionStatus.DONE.value
        finally:
            await _close(harness, engine)

    asyncio.run(scenario())


def test_disabled_returns_without_alerts(tmp_path) -> None:
    """``watchdog.enabled=false``：直接返回，不写告警、不更新 last_run_at。"""

    async def scenario() -> None:
        harness, engine = await _make_harness(tmp_path, "disabled", enabled=False)
        try:
            ticket_id = await _seed_ticket(harness.factory, updated_at=NOW)
            session_id = await _seed_session(
                harness.factory, kind=AgentSessionKind.WORKER.value, ticket_id=ticket_id
            )
            await _attach_worker_session(harness.factory, ticket_id, session_id)
            await _seed_run(
                harness.factory,
                session_id=session_id,
                ticket_id=ticket_id,
                heartbeat_at=NOW - timedelta(seconds=120),
            )
            service = harness.build()

            assert await service.run_once(NOW) == []
            assert await harness.alerts() == []
            assert service.last_run_at is None
        finally:
            await _close(harness, engine)

    asyncio.run(scenario())


def _str(value: Any) -> str:
    return value.value if hasattr(value, "value") else str(value)
