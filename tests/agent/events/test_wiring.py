"""各事件源接缝与诊断迁移（M81 规格 §4.2、§4.5.4、§6 的 test_wiring.py）。

八个用例覆盖：

- 流水线终态钩子与诊断入口共用去重键（一行事件、一张诊断工单）；
- 迟到的 ``CORE_CRASHED`` 改判补发 ``pipeline.core_crash_linked`` 并直通诊断工单；
- 有订阅的流水线失败只投订阅工单，不建诊断工单；
- ``agent.auto_diagnose_failures=false`` 时只入表、不调模型；
- 空闲内核崩溃每个 boot/generation 只发一条，换 ``boot_id`` 是新的一行；
- 设备状态只在变化时发、首次只记状态；
- ``core.failed`` 进表并交总控；
- 更新广播按目标发事件、重复广播不重复入表。

临时库与真实服务同口径（``tests/fakes/v3_db.py``）：工单、话题、收件箱、规则、
事件服务与诊断适配器都是真实实现，只有 Worker 建会话与研判是替身。
"""

from __future__ import annotations

import asyncio
from types import SimpleNamespace
from typing import Any

from sqlalchemy import func, select

from maa_api.agent.events.bus import TriggerService
from maa_api.agent.events.evaluator import EvalOutcome
from maa_api.agent.events.rules import TriggerRuleService
from maa_api.agent.events.wiring import (
    DeviceStatePublisher,
    PipelineEventPublisher,
    on_update_broadcast,
    publish_core_failed,
)
from maa_api.agent.incidents import AgentIncidentService
from maa_api.agent.tickets.inbox import InboxService
from maa_api.agent.tickets.service import TicketService
from maa_api.agent.tickets.threads import ThreadRouter
from maa_api.db.models import (
    AgentInbox,
    AgentTrigger,
    Pipeline,
    Task,
    Ticket,
    new_uuid,
    utcnow,
)
from maa_api.db.repositories.ticket import (
    ThreadSubscriptionRepository,
    TicketRepository,
)
from maa_api.domain.enums import (
    InboxKind,
    TicketStatus,
    TriggerAdmission,
    TriggerEvalStatus,
)
from maa_api.domain.errors import AppError, ErrorCode
from maa_api.settings import AgentSettings, Settings
from tests.fakes.v3_db import make_session_factory, seed_session

_SPECS = {"diagnose": "none", "ops": "none", "daily_ops": "consume"}

#: 话题键里的 pipeline id 必须是 UUID（03 §15 / ThreadRouter.parse_thread_key）。
_P1 = "0f8fad5b-d9cb-469f-a165-708677289501"
_P2 = "0f8fad5b-d9cb-469f-a165-708677289502"
_P3 = "0f8fad5b-d9cb-469f-a165-708677289503"
_P4 = "0f8fad5b-d9cb-469f-a165-708677289504"


class _SpecRegistry:
    """最小代理定义注册表；未知名字与生产一样抛 ``AGENT_SPEC_NOT_FOUND``。"""

    def get(self, name: str) -> SimpleNamespace:
        if name not in _SPECS:
            raise AppError(
                ErrorCode.AGENT_SPEC_NOT_FOUND, f"没有名为 {name} 的代理定义"
            )
        return SimpleNamespace(kind="worker", risk_ceiling=_SPECS[name])


class _WorkerService:
    """替身 Worker 服务：只建 ``kind=worker`` 的会话行，不跑模型。"""

    def __init__(self, factory: Any) -> None:
        self._factory = factory

    async def ensure_worker_session(self, ticket: Ticket) -> SimpleNamespace:
        session_id = await seed_session(
            self._factory, kind="worker", ticket_id=ticket.id
        )
        return SimpleNamespace(id=session_id)


class _Evaluator:
    """诊断规则的 ``policy`` 为空，研判不应被调用；记录次数用于断言。"""

    def __init__(self) -> None:
        self.calls = 0

    async def evaluate(self, trigger: AgentTrigger, rule: Any) -> EvalOutcome:
        self.calls += 1
        return EvalOutcome(
            status=TriggerEvalStatus.ACCEPT,
            decision=None,
            reason="no_policy",
            run_id=None,
        )


class _ActiveRunner:
    def __init__(self) -> None:
        self.active_pipeline_id = None
        self.active_pipeline_epoch = None
        self.crashed_pipeline_ids: dict[int, set[str]] = {}

    def core_crashed_pipeline_ids(self, generation: int) -> tuple[str, ...]:
        return tuple(self.crashed_pipeline_ids.get(int(generation), ()))


class _Harness:
    def __init__(self, engine: Any, factory: Any, settings: Settings) -> None:
        self.engine = engine
        self.factory = factory
        self.settings = settings
        self.inbox = InboxService(factory)
        self.router = ThreadRouter(factory, self.inbox)
        self.registry = _SpecRegistry()
        self.ticket_events: list[tuple[str, dict[str, Any]]] = []
        self.broadcasts: list[tuple[str, dict[str, Any]]] = []
        self.ticket_service = TicketService(
            factory,
            inbox_service=self.inbox,
            thread_router=self.router,
            spec_registry=self.registry,
            broadcast=lambda kind, data: self.ticket_events.append((kind, data)),
        )
        self.worker_service = _WorkerService(factory)
        self.evaluator = _Evaluator()
        state = SimpleNamespace(
            ticket_service=self.ticket_service,
            inbox_service=self.inbox,
            worker_service=self.worker_service,
        )
        self.app = SimpleNamespace(state=state)
        self.rules = TriggerRuleService(
            factory,
            spec_registry=self.registry,
            settings_provider=lambda: self.settings,
        )
        self.trigger_service = TriggerService(
            factory,
            app=self.app,
            rules=self.rules,
            evaluator=self.evaluator,
            ticket_service=self.ticket_service,
            thread_router=self.router,
            orchestrator_service=SimpleNamespace(),
            broadcast=lambda kind, data: self.broadcasts.append((kind, data)),
            settings_provider=lambda: self.settings,
        )
        self.publisher = PipelineEventPublisher(
            self.trigger_service, factory, thread_router=self.router
        )
        self.incident = AgentIncidentService(
            factory,
            None,
            settings_provider=lambda: self.settings,
            pipeline_runner=_ActiveRunner(),
            request=SimpleNamespace(app=self.app),
            boot_id="boot-test",
        )
        self.incident.bind_trigger_service(self.trigger_service, self.publisher)
        state.agent_incident_service = self.incident
        state.trigger_service = self.trigger_service
        state.pipeline_event_publisher = self.publisher

    async def close(self) -> None:
        await self.trigger_service.close()
        await self.engine.dispose()

    async def new_incident_service(self, boot_id: str) -> AgentIncidentService:
        service = AgentIncidentService(
            self.factory,
            None,
            settings_provider=lambda: self.settings,
            pipeline_runner=_ActiveRunner(),
            request=SimpleNamespace(app=self.app),
            boot_id=boot_id,
        )
        service.bind_trigger_service(self.trigger_service, self.publisher)
        return service


async def _harness(path: Any, *, settings: Settings | None = None) -> _Harness:
    engine, factory = await make_session_factory(path)
    return _Harness(engine, factory, settings or Settings())


async def _seed_pipeline(
    factory: Any,
    pipeline_id: str,
    *,
    core_epoch: int = 1,
    error_code: str = "TASK_FAILED",
    error_message: str = "task ended in error",
) -> None:
    now = utcnow()
    async with factory() as db:
        db.add(
            Pipeline(
                id=pipeline_id,
                core_id="default",
                core_epoch=core_epoch,
                source="manual",
                priority=0,
                status="failed",
                title="日常",
                task_count=2,
                error_code=error_code,
                error_message=error_message,
                started_at=now,
                finished_at=now,
            )
        )
        await db.flush()
        db.add(
            Task(
                id=f"task-{pipeline_id}-1",
                pipeline_id=pipeline_id,
                order_index=0,
                type_name="Fight",
                task_name="Fight",
                params={},
                status="failed",
                error_code="TASK_FAILED",
            )
        )
        db.add(
            Task(
                id=f"task-{pipeline_id}-2",
                pipeline_id=pipeline_id,
                order_index=1,
                type_name="StartUp",
                task_name="StartUp",
                params={},
                status="completed",
            )
        )
        await db.commit()


async def _seed_subscribed_ticket(factory: Any, thread_key: str) -> tuple[str, str]:
    """建一张订阅 ``thread_key`` 的 open 工单（含 Worker 会话）。"""
    ticket_id = new_uuid()
    now = utcnow()
    async with factory() as db:
        db.add(
            Ticket(
                id=ticket_id,
                title="提交它的工单",
                kind="ops",
                status=TicketStatus.OPEN.value,
                origin="user",
                priority=1,
                created_at=now,
                updated_at=now,
            )
        )
        await db.commit()
    worker_id = await seed_session(factory, kind="worker", ticket_id=ticket_id)
    async with factory() as db:
        stored = await TicketRepository(db).get(ticket_id)
        stored.worker_session_id = worker_id
        await db.commit()
        await ThreadSubscriptionRepository(db).upsert_active(
            ticket_id, thread_key, now=utcnow()
        )
        await db.commit()
    return ticket_id, worker_id


async def _count(factory: Any, model: Any) -> int:
    async with factory() as db:
        return int(
            (await db.execute(select(func.count()).select_from(model))).scalar_one()
        )


async def _rows(factory: Any) -> list[AgentTrigger]:
    async with factory() as db:
        result = await db.execute(select(AgentTrigger).order_by(AgentTrigger.id.asc()))
        return list(result.scalars().all())


async def _tickets(factory: Any) -> list[Ticket]:
    async with factory() as db:
        result = await db.execute(select(Ticket).order_by(Ticket.created_at.asc()))
        return list(result.scalars().all())


async def _inbox(factory: Any, session_id: str) -> list[AgentInbox]:
    async with factory() as db:
        result = await db.execute(
            select(AgentInbox)
            .where(AgentInbox.session_id == session_id)
            .order_by(AgentInbox.id.asc())
        )
        return list(result.scalars().all())


async def _wait_until(predicate: Any, *, timeout: float = 10.0) -> None:
    loop = asyncio.get_running_loop()
    deadline = loop.time() + timeout
    while loop.time() < deadline:
        if await predicate():
            return
        await asyncio.sleep(0.01)
    raise AssertionError("条件在期限内没有成立")


# ---------------------------------------------------------------------------
# §6 八个接线用例
# ---------------------------------------------------------------------------
def test_pipeline_failure_from_hook_and_incident_is_one_trigger(tmp_path) -> None:
    async def scenario() -> None:
        harness = await _harness(tmp_path / "wiring.db")
        try:
            await _seed_pipeline(harness.factory, _P1)
            await harness.publisher.on_pipeline_finished(_P1, "failed")
            result = await harness.incident.pipeline_failed(_P1)

            rows = await _rows(harness.factory)
            assert len(rows) == 1
            row = rows[0]
            assert row.kind == "pipeline.failed"
            assert row.dedupe_key == f"pipeline:{_P1}:failed"
            assert row.routed_to == "ticket"
            assert row.ticket_id is not None
            tickets = await _tickets(harness.factory)
            assert len(tickets) == 1
            assert tickets[0].kind == "diagnose"
            assert tickets[0].source_trigger_id == row.id
            assert result.status == "existing"
            assert result.created is False
            assert result.incident_key == f"pipeline:{_P1}"
            assert result.session_id == tickets[0].id
        finally:
            await harness.close()

    asyncio.run(scenario())


def test_core_crash_promotion_publishes_linked_event(tmp_path) -> None:
    async def scenario() -> None:
        harness = await _harness(tmp_path / "wiring.db")
        try:
            await _seed_pipeline(
                harness.factory,
                _P2,
                core_epoch=4,
                error_code=ErrorCode.INTERNAL_ERROR.value,
            )
            await harness.publisher.on_pipeline_finished(_P2, "failed")
            async with harness.factory() as db:
                stored = await db.get(Pipeline, _P2)
                stored.error_code = ErrorCode.CORE_CRASHED.value
                stored.error_message = "MaaCore 子进程崩溃"
                await db.commit()

            result = await harness.incident.pipeline_failed(_P2)
            assert result.status == "existing"
            assert result.created is False

            rows = await _rows(harness.factory)
            assert [row.kind for row in rows] == [
                "pipeline.failed",
                "pipeline.core_crash_linked",
            ]
            linked = rows[1]
            assert linked.dedupe_key == f"pipeline:{_P2}:core:4"
            assert linked.payload["pipeline_id"] == _P2
            assert linked.payload["core_generation"] == 4
            assert linked.routed_to == "ticket"

            tickets = await _tickets(harness.factory)
            assert len(tickets) == 1
            assert linked.ticket_id == tickets[0].id
            items = await _inbox(harness.factory, tickets[0].worker_session_id)
            delivered = [
                item
                for item in items
                if str(item.kind) == str(InboxKind.THREAD_EVENT)
                and item.ref_id == str(linked.id)
            ]
            assert len(delivered) == 1
            assert "第 4 代" in delivered[0].payload["summary"]
        finally:
            await harness.close()

    asyncio.run(scenario())


def test_owned_pipeline_failure_goes_to_owner_not_diagnose(tmp_path) -> None:
    async def scenario() -> None:
        harness = await _harness(tmp_path / "wiring.db")
        try:
            owner_id, worker_id = await _seed_subscribed_ticket(
                harness.factory, f"pipeline:{_P3}"
            )
            await _seed_pipeline(harness.factory, _P3)

            await harness.publisher.on_pipeline_finished(_P3, "failed")

            rows = await _rows(harness.factory)
            assert len(rows) == 1
            assert rows[0].kind == "pipeline.failed"
            assert rows[0].routed_to == "ticket"
            assert rows[0].ticket_id == owner_id
            tickets = await _tickets(harness.factory)
            assert len(tickets) == 1
            assert tickets[0].kind == "ops"
            items = await _inbox(harness.factory, worker_id)
            assert len(items) == 1
            assert str(items[0].kind) == str(InboxKind.THREAD_EVENT)
            assert items[0].ref_id == str(rows[0].id)
        finally:
            await harness.close()

    asyncio.run(scenario())


def test_auto_diagnose_disabled_records_without_model_call(tmp_path) -> None:
    async def scenario() -> None:
        harness = await _harness(
            tmp_path / "wiring.db",
            settings=Settings(agent=AgentSettings(auto_diagnose_failures=False)),
        )
        try:
            await _seed_pipeline(harness.factory, _P4)
            await harness.publisher.on_pipeline_finished(_P4, "failed")

            rows = await _rows(harness.factory)
            assert len(rows) == 1
            assert rows[0].kind == "pipeline.failed"
            assert rows[0].admission == TriggerAdmission.IGNORED.value
            assert rows[0].eval_reason == "no_rule"
            assert rows[0].routed_to == "none"
            assert await _count(harness.factory, Ticket) == 0
            assert harness.evaluator.calls == 0
        finally:
            await harness.close()

    asyncio.run(scenario())


def test_idle_core_crash_publishes_once_per_boot_generation(tmp_path) -> None:
    async def scenario() -> None:
        harness = await _harness(tmp_path / "wiring.db")
        try:
            first = await harness.incident.core_crashed(
                7, {"reason": "process_exit", "exitcode": 1}
            )
            replay = await harness.incident.core_crashed(
                7, {"reason": "process_exit", "exitcode": 1}
            )

            assert first.status == "created"
            assert first.created is True
            assert replay.status == "existing"
            assert replay.created is False
            assert replay.session_id == first.session_id
            rows = await _rows(harness.factory)
            assert len(rows) == 1
            assert rows[0].kind == "core.crashed"
            assert rows[0].dedupe_key == "core:default:boot-test:7"
            assert rows[0].payload["generation"] == 7
            assert rows[0].payload["reason"] == "process_exit"
            assert rows[0].routed_to == "ticket"
            assert await _count(harness.factory, Ticket) == 1

            restarted = await harness.new_incident_service("boot-other")
            after_restart = await restarted.core_crashed(
                7, {"reason": "process_exit", "exitcode": 1}
            )
            assert after_restart.incident_key == "core:default:boot-other:7"
            rows = await _rows(harness.factory)
            assert [row.dedupe_key for row in rows] == [
                "core:default:boot-test:7",
                "core:default:boot-other:7",
            ]
            # 上一代的诊断工单还开着并订阅 core:default，新事件按 §4.5.2 第 1 步
            # 直通它，不再建一张；事件行仍按 boot_id 分开。
            assert after_restart.session_id == first.session_id
            assert await _count(harness.factory, Ticket) == 1
        finally:
            await harness.close()

    asyncio.run(scenario())


def test_device_publisher_only_on_transitions() -> None:
    class _Recorder:
        def __init__(self) -> None:
            self.calls: list[dict[str, Any]] = []

        def publish_nowait(self, **kwargs: Any) -> None:
            self.calls.append(kwargs)

    recorder = _Recorder()
    publisher = DeviceStatePublisher(lambda: recorder)  # type: ignore[arg-type]
    connected = {
        "core_id": "default",
        "state": "connected",
        "address": "127.0.0.1:5555",
        "last_error": None,
        "retry": {"attempt": 0, "max": 5},
    }

    publisher.on_device_status(connected)
    assert recorder.calls == []
    publisher.on_device_status(dict(connected))
    assert recorder.calls == []

    reached = dict(
        connected,
        state="disconnected",
        last_error={"stage": "adb_connect"},
    )
    publisher.on_device_status(reached)
    publisher.on_device_status(dict(connected))
    publisher.on_device_status(dict(connected, state="reconnecting"))
    publisher.on_device_status(
        dict(
            connected,
            state="unavailable",
            last_error={"stage": "connect"},
            retry={"attempt": 3, "max": 5},
        )
    )
    # unavailable 再回 reconnecting 不属于「从 connected 断开」，只记状态。
    publisher.on_device_status(dict(connected, state="reconnecting"))

    assert [call["kind"] for call in recorder.calls] == [
        "device.disconnected",
        "device.connected",
        "device.disconnected",
        "device.unavailable",
    ]
    disconnected = recorder.calls[0]
    assert disconnected["thread_key"] == "device:default"
    assert disconnected["payload"]["previous_state"] == "connected"
    assert disconnected["payload"]["state"] == "disconnected"
    assert disconnected["payload"]["error_stage"] == "adb_connect"
    unavailable = recorder.calls[3]
    assert unavailable["payload"]["retry_attempt"] == 3
    assert publisher.last_state == "reconnecting"


def test_core_failed_state_publishes_and_goes_to_orchestrator(tmp_path) -> None:
    async def scenario() -> None:
        harness = await _harness(tmp_path / "wiring.db")
        try:
            orchestrator_id = await seed_session(
                harness.factory, kind="orchestrator"
            )
            supervisor = SimpleNamespace(
                core_id="default",
                generation=5,
                last_crash={"reason": "process_exit", "exitcode": 1},
            )
            publish_core_failed(
                lambda: harness.trigger_service,
                supervisor=supervisor,
                boot_id="boot-test",
            )

            async def published() -> bool:
                rows = await _rows(harness.factory)
                return len(rows) == 1 and rows[0].processed_at is not None

            await _wait_until(published)
            rows = await _rows(harness.factory)
            row = rows[0]
            assert row.kind == "core.failed"
            assert row.dedupe_key == "core:default:boot-test:5:failed"
            assert row.thread_key == "core:default"
            assert row.payload["generation"] == 5
            assert row.payload["last_crash"]["reason"] == "process_exit"
            assert row.routed_to == "orchestrator"
            items = await _inbox(harness.factory, orchestrator_id)
            assert len(items) == 1
            assert str(items[0].kind) == str(InboxKind.TRIGGER)
            assert items[0].payload["kind"] == "core.failed"
        finally:
            await harness.close()

    asyncio.run(scenario())


def test_update_broadcast_publishes_one_event_per_target(tmp_path) -> None:
    async def scenario() -> None:
        harness = await _harness(tmp_path / "wiring.db")
        data = {
            "targets": [
                {"target": "core", "current": "1.0.0", "latest": "1.1.0"},
                {
                    "target": "resource",
                    "channel": "stable",
                    "current": "2.0.0",
                    "latest": "2.1.0",
                },
            ]
        }
        try:
            on_update_broadcast(
                lambda: harness.trigger_service, "update_available", data
            )

            async def published() -> bool:
                return await _count(harness.factory, AgentTrigger) == 2

            await _wait_until(published)
            rows = await _rows(harness.factory)
            assert {row.dedupe_key for row in rows} == {
                "update_available:core::1.1.0",
                "update_available:resource:stable:2.1.0",
            }
            assert all(row.kind == "system.update_available" for row in rows)
            assert all(row.thread_key == "system:update_available" for row in rows)
            payloads = {row.payload["target"]: row.payload for row in rows}
            assert payloads["core"]["latest"] == "1.1.0"
            assert payloads["resource"]["channel"] == "stable"

            on_update_broadcast(
                lambda: harness.trigger_service, "update_available", data
            )
            await asyncio.sleep(0.1)
            assert await _count(harness.factory, AgentTrigger) == 2
            # 其他广播（进度）不产生事件。
            on_update_broadcast(
                lambda: harness.trigger_service,
                "update_progress",
                {"target": "core", "percent": 50},
            )
            await asyncio.sleep(0.1)
            assert await _count(harness.factory, AgentTrigger) == 2
        finally:
            await harness.close()

    asyncio.run(scenario())
