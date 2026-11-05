"""事件总线与两级过滤管线（M81 规格 §4.5.1–§4.5.3、§4.5.7、§6）。

覆盖 §6 ``test_bus.py`` 的十二组用例与 §4.5.7 的边界：

- 管线：去重、订阅直通（总开关关掉也一样）、auto 建单、observe 观察与广播、
  「交总控」、stop、冷却、每日上限（含跨重启与 0 上限）、恢复 24 小时窗与幂等、
  同话题按到达顺序；
- 边界：未登记种类与超限 payload 记 rejected、建单失败回落 fallback、收件箱
  渲染拆分。

临时库用 ``tests/fakes/v3_db.py`` 的真实 SQLite；工单、话题路由、收件箱、规则
服务都是真实实现，只有研判代理与 Worker 服务是替身（不连模型、不建真会话）。
"""

from __future__ import annotations

import asyncio
from datetime import timedelta
from types import SimpleNamespace
from typing import Any

from sqlalchemy import func, select

from maa_api.agent.events.bus import TriggerService
from maa_api.agent.events.evaluator import EvalOutcome
from maa_api.agent.events.rules import TriggerRuleService
from maa_api.agent.llm.schemas.trigger_decision import TriggerDecision
from maa_api.agent.tickets.inbox import InboxService
from maa_api.agent.tickets.service import TicketService
from maa_api.agent.tickets.threads import ThreadRouter
from maa_api.db.models import (
    AgentInbox,
    AgentTrigger,
    Ticket,
    TriggerObservation,
    TriggerRule,
    new_uuid,
    utcnow,
)
from maa_api.db.repositories.ticket import (
    MandateRepository,
    ThreadSubscriptionRepository,
    TicketRepository,
)
from maa_api.db.repositories.triggers import (
    TriggerObservationRepository,
    TriggerRepository,
    TriggerRuleRepository,
)
from maa_api.domain.enums import (
    EventSource,
    InboxKind,
    MandateStrength,
    RiskLevel,
    TicketOrigin,
    TicketStatus,
    TriggerAdmission,
    TriggerEvalStatus,
    TriggerMode,
    TriggerRoute,
)
from maa_api.domain.errors import AppError, ErrorCode
from maa_api.settings import AgentSettings, Settings, TriggersSettings
from tests.fakes.v3_db import make_session_factory, seed_run, seed_session

_PIPELINE = "pipeline:0f8fad5b-d9cb-469f-a165-70867728950e"
_DEVICE = "device:emulator-1"
_DEVICE_TWO = "device:emulator-2"
_SANITY = "system:sanity"

_SPECS = {"diagnose": "none", "daily_ops": "consume", "ops": "none"}


class _SpecRegistry:
    """最小代理定义注册表；未知名字与生产一样抛 ``AGENT_SPEC_NOT_FOUND``。"""

    def get(self, name: str) -> SimpleNamespace:
        if name not in _SPECS:
            raise AppError(
                ErrorCode.AGENT_SPEC_NOT_FOUND, f"没有名为 {name} 的代理定义"
            )
        return SimpleNamespace(kind="worker", risk_ceiling=_SPECS[name])


class _WorkerService:
    """替身 Worker 服务：建一个 ``kind=worker`` 的会话行，不跑模型。"""

    def __init__(self, factory: Any) -> None:
        self._factory = factory

    async def ensure_worker_session(self, ticket: Ticket) -> SimpleNamespace:
        session_id = await seed_session(
            self._factory, kind="worker", ticket_id=ticket.id
        )
        return SimpleNamespace(id=session_id)


class _Evaluator:
    """替身研判：返回固定结论并计数，便于断言「一次都没调」。"""

    def __init__(self, outcome: EvalOutcome | None = None) -> None:
        self.outcome = outcome or EvalOutcome(
            status=TriggerEvalStatus.ACCEPT,
            decision=TriggerDecision(
                decision="accept",
                title="自动建单",
                brief="按事件处理，只做不需要确认的操作。",
                scope="处理这条事件",
                reason="值得处理",
            ),
            reason="值得处理",
            run_id=None,
        )
        self.calls = 0

    async def evaluate(self, trigger: AgentTrigger, rule: Any) -> EvalOutcome:
        self.calls += 1
        return self.outcome


class _Harness:
    """一套临时库上的真实服务 + 替身研判 / Worker。"""

    def __init__(self, factory: Any) -> None:
        self.factory = factory
        self.settings = Settings()
        self.inbox = InboxService(factory)
        self.router = ThreadRouter(factory, self.inbox)
        self.registry = _SpecRegistry()
        self.ticket_events: list[tuple[str, dict]] = []
        self.ticket_service = TicketService(
            factory,
            inbox_service=self.inbox,
            thread_router=self.router,
            spec_registry=self.registry,
            broadcast=lambda kind, data: self.ticket_events.append((kind, data)),
        )
        self.rules = TriggerRuleService(
            factory,
            spec_registry=self.registry,
            settings_provider=lambda: self.settings,
        )
        self.evaluator = _Evaluator()
        self.broadcasts: list[dict[str, Any]] = []
        app = SimpleNamespace(
            state=SimpleNamespace(worker_service=_WorkerService(factory))
        )
        self.service = TriggerService(
            factory,
            app=app,
            rules=self.rules,
            evaluator=self.evaluator,
            ticket_service=self.ticket_service,
            thread_router=self.router,
            orchestrator_service=SimpleNamespace(),
            broadcast=self._broadcast,
            settings_provider=lambda: self.settings,
        )

    def _broadcast(self, message_type: str, data: dict[str, Any]) -> None:
        self.broadcasts.append({"type": message_type, **data})


async def _factory(path: Any) -> Any:
    engine, factory = await make_session_factory(path)
    return engine, factory


async def _add_user_rule(
    factory: Any,
    rule_id: str,
    *,
    source: str,
    kind_pattern: str,
    mode: TriggerMode,
    worker: str = "ops",
    policy: str = "",
    conditions: dict[str, Any] | None = None,
    cooldown_seconds: int = 0,
) -> None:
    now = utcnow()
    async with factory() as db:
        await TriggerRuleRepository(db).upsert(
            TriggerRule(
                id=rule_id,
                title=rule_id,
                source=source,
                kind_pattern=kind_pattern,
                conditions=conditions or {},
                mode=str(mode),
                worker=worker,
                policy=policy,
                cooldown_seconds=cooldown_seconds,
                enabled=True,
                origin="user",
                created_at=now,
                updated_at=now,
            )
        )
        await db.commit()


async def _seed_ticket(
    factory: Any,
    *,
    thread_key: str | None = None,
    worker_session: bool = True,
    source_trigger_id: int | None = None,
    kind: str = "daily_ops",
) -> tuple[str, str | None]:
    """造一张 open 工单（可选 Worker 会话与订阅）；返回 ``(工单 id, 会话 id)``。"""
    ticket_id = new_uuid()
    now = utcnow()
    async with factory() as db:
        db.add(
            Ticket(
                id=ticket_id,
                title="订阅工单",
                kind=kind,
                status=TicketStatus.OPEN.value,
                origin=TicketOrigin.USER.value,
                priority=1,
                source_trigger_id=source_trigger_id,
                created_at=now,
                updated_at=now,
            )
        )
        await db.commit()
    worker_id: str | None = None
    if worker_session:
        worker_id = await seed_session(factory, kind="worker", ticket_id=ticket_id)
        async with factory() as db:
            stored = await TicketRepository(db).get(ticket_id)
            stored.worker_session_id = worker_id
            await db.commit()
    if thread_key is not None:
        async with factory() as db:
            await ThreadSubscriptionRepository(db).upsert_active(
                ticket_id, thread_key, now=utcnow()
            )
            await db.commit()
    return ticket_id, worker_id


async def _seed_trigger(
    factory: Any,
    *,
    source: str,
    kind: str,
    thread_key: str,
    payload: dict[str, Any] | None = None,
    created_at: Any = None,
) -> int:
    async with factory() as db:
        row = AgentTrigger(
            source=source,
            kind=kind,
            thread_key=thread_key,
            payload=payload or {},
            admission=str(TriggerAdmission.ACCEPTED),
            eval_status=str(TriggerEvalStatus.PENDING),
            created_at=created_at or utcnow(),
        )
        db.add(row)
        await db.commit()
        return int(row.id)


async def _count(factory: Any, model: Any) -> int:
    async with factory() as db:
        return int(
            (await db.execute(select(func.count()).select_from(model))).scalar_one()
        )


async def _trigger_row(factory: Any, trigger_id: int) -> AgentTrigger:
    async with factory() as db:
        row = await TriggerRepository(db).get(trigger_id)
    assert row is not None
    return row


async def _inbox(factory: Any, session_id: str, kind: str | None = None) -> list[AgentInbox]:
    async with factory() as db:
        statement = select(AgentInbox).where(AgentInbox.session_id == session_id)
        if kind is not None:
            statement = statement.where(AgentInbox.kind == kind)
        result = await db.execute(statement.order_by(AgentInbox.id.asc()))
        return list(result.scalars().all())


async def _close(engine: Any) -> None:
    await engine.dispose()


# ---------------------------------------------------------------------------
# §6 十二条
# ---------------------------------------------------------------------------
def test_duplicate_dedupe_key_returns_existing_without_processing(tmp_path) -> None:
    async def scenario() -> None:
        engine, factory = await _factory(tmp_path / "bus.db")
        try:
            harness = _Harness(factory)
            first = await harness.service.publish(
                source=EventSource.PIPELINE,
                kind="pipeline.completed",
                thread_key=_PIPELINE,
                payload={"title": "日常", "pipeline_id": "p-1"},
                dedupe_key="pipeline:p-1:completed",
            )
            assert first.duplicate is False
            assert first.admission == TriggerAdmission.IGNORED
            assert first.routed_to == TriggerRoute.NONE

            second = await harness.service.publish(
                source=EventSource.PIPELINE,
                kind="pipeline.completed",
                thread_key=_PIPELINE,
                payload={"title": "日常", "pipeline_id": "p-1"},
                dedupe_key="pipeline:p-1:completed",
            )
            assert second.duplicate is True
            assert second.trigger_id == first.trigger_id
            assert await _count(factory, AgentTrigger) == 1
        finally:
            await _close(engine)

    asyncio.run(scenario())


def test_subscribed_thread_delivers_to_worker_even_when_triggers_disabled(
    tmp_path,
) -> None:
    async def scenario() -> None:
        engine, factory = await _factory(tmp_path / "bus.db")
        try:
            harness = _Harness(factory)
            harness.settings = Settings(triggers=TriggersSettings(enabled=False))
            ticket_id, worker_id = await _seed_ticket(
                factory, thread_key=_DEVICE
            )
            async with factory() as db:
                await ThreadSubscriptionRepository(db).upsert_active(
                    ticket_id, _PIPELINE, now=utcnow()
                )
                await db.commit()

            result = await harness.service.publish(
                source=EventSource.DEVICE,
                kind="device.connected",
                thread_key=_DEVICE,
                payload={"address": "127.0.0.1:5555"},
            )

            assert result.admission == TriggerAdmission.ACCEPTED
            assert result.routed_to == TriggerRoute.TICKET
            assert result.ticket_id == ticket_id
            assert result.eval_status == TriggerEvalStatus.SKIPPED
            items = await _inbox(factory, worker_id, str(InboxKind.THREAD_EVENT))
            assert len(items) == 1
            assert items[0].ref_id == str(result.trigger_id)
            assert items[0].payload["trigger_id"] == result.trigger_id
            assert items[0].payload["kind"] == "device.connected"
            assert "设备已连接" in items[0].payload["summary"]
            assert await _trigger_row(factory, result.trigger_id) is not None
            row = await _trigger_row(factory, result.trigger_id)
            assert row.processed_at is not None
            assert row.eval_reason == "subscribed"

            pipeline = await harness.service.publish(
                source=EventSource.PIPELINE,
                kind="pipeline.completed",
                thread_key=_PIPELINE,
                payload={"title": "日常", "pipeline_id": "p-1"},
            )
            assert pipeline.routed_to == TriggerRoute.TICKET
            pipeline_items = await _inbox(
                factory, worker_id, str(InboxKind.THREAD_EVENT)
            )
            # 流水线种类把 payload 本身的键并到收件箱项顶层（§4.5.2 第 1 步）。
            assert pipeline_items[-1].payload["pipeline_id"] == "p-1"
            assert pipeline_items[-1].payload["title"] == "日常"
        finally:
            await _close(engine)

    asyncio.run(scenario())


def test_auto_rule_creates_inferred_ticket_and_subscribes_thread(tmp_path) -> None:
    async def scenario() -> None:
        engine, factory = await _factory(tmp_path / "bus.db")
        try:
            harness = _Harness(factory)
            await _add_user_rule(
                factory,
                "auto-device",
                source=EventSource.DEVICE.value,
                kind_pattern="device.connected",
                mode=TriggerMode.AUTO,
                worker="ops",
            )

            result = await harness.service.publish(
                source=EventSource.DEVICE,
                kind="device.connected",
                thread_key=_DEVICE,
                payload={"address": "127.0.0.1:5555"},
            )

            assert result.routed_to == TriggerRoute.TICKET
            assert result.ticket_id is not None
            assert result.eval_status == TriggerEvalStatus.SKIPPED
            async with factory() as db:
                ticket = await TicketRepository(db).get(result.ticket_id)
                mandate = await MandateRepository(db).get_by_ticket(result.ticket_id)
                subscriptions = await ThreadSubscriptionRepository(
                    db
                ).list_for_ticket(result.ticket_id)
            assert ticket is not None
            assert ticket.kind == "ops"
            assert ticket.origin == TicketOrigin.TRIGGER.value
            assert ticket.source_trigger_id == result.trigger_id
            assert ticket.worker_session_id is not None
            assert mandate is not None
            assert mandate.strength == MandateStrength.INFERRED.value
            assert mandate.risk_ceiling == RiskLevel.NONE.value
            assert mandate.rule_id == "auto-device"
            assert mandate.sender == "rule:auto-device"
            assert mandate.source == EventSource.DEVICE.value
            assert mandate.policy_version == (await harness.rules.rule_set()).version
            assert any(item.active for item in subscriptions)
            # 收件箱里有一条可渲染的任务说明（M69 的空 ticket_brief 已被丢弃）。
            items = await _inbox(factory, ticket.worker_session_id, str(InboxKind.SYSTEM))
            texts = [item.payload.get("text") for item in items if item.payload.get("text")]
            assert texts and "事件处理任务" in texts[0]
            assert any(item.status == "dropped" for item in items)
        finally:
            await _close(engine)

    asyncio.run(scenario())


def test_observe_rule_writes_observation_and_broadcasts(tmp_path) -> None:
    async def scenario() -> None:
        engine, factory = await _factory(tmp_path / "bus.db")
        try:
            harness = _Harness(factory)

            result = await harness.service.publish(
                source=EventSource.SYSTEM,
                kind="system.sanity_full",
                thread_key=_SANITY,
                payload={"estimated": 135, "max_sanity": 135},
            )

            assert result.routed_to == TriggerRoute.OBSERVATION
            assert result.eval_status == TriggerEvalStatus.ACCEPT
            assert result.observation_id is not None
            assert await _count(factory, Ticket) == 0
            assert harness.evaluator.calls == 1
            async with factory() as db:
                observation = await TriggerObservationRepository(db).get(
                    result.observation_id
                )
            assert observation is not None
            assert observation.rule_id == "sanity-full"
            assert observation.decision["decision"] == "accept"
            assert observation.decision["title"] == "自动建单"
            assert len(harness.broadcasts) == 1
            event = harness.broadcasts[0]
            assert event["type"] == "agent_event"
            assert event["session_id"] is None
            assert event["event"] == "observation_created"
            assert event["payload"]["observation_id"] == result.observation_id
            assert event["payload"]["trigger_id"] == result.trigger_id
            assert event["payload"]["summary"].startswith("理智估计已满")
            assert event["payload"]["created_at"].endswith("Z")
        finally:
            await _close(engine)

    asyncio.run(scenario())


def test_attention_kind_goes_to_orchestrator_after_observe_or_ignore(
    tmp_path,
) -> None:
    async def scenario() -> None:
        engine, factory = await _factory(tmp_path / "bus.db")
        try:
            orchestrator_id = await seed_session(factory, kind="orchestrator")
            harness = _Harness(factory)

            observed = await harness.service.publish(
                source=EventSource.DEVICE,
                kind="device.unavailable",
                thread_key=_DEVICE,
                payload={"retry_attempt": 3, "address": "127.0.0.1:5555"},
            )
            assert observed.routed_to == TriggerRoute.ORCHESTRATOR
            assert observed.observation_id is not None
            items = await _inbox(factory, orchestrator_id, str(InboxKind.TRIGGER))
            assert len(items) == 1
            assert items[0].payload["reason"] == "值得处理"
            assert items[0].payload["trigger_id"] == observed.trigger_id

            harness.evaluator.outcome = EvalOutcome(
                status=TriggerEvalStatus.IGNORE,
                decision=None,
                reason="没有待执行的工作",
                run_id=None,
            )
            ignored = await harness.service.publish(
                source=EventSource.DEVICE,
                kind="device.unavailable",
                thread_key=_DEVICE_TWO,
                payload={"retry_attempt": 4, "address": "127.0.0.1:5555"},
            )
            assert ignored.routed_to == TriggerRoute.ORCHESTRATOR
            assert ignored.observation_id is None
            items = await _inbox(factory, orchestrator_id, str(InboxKind.TRIGGER))
            assert len(items) == 2
            assert items[1].payload["reason"] == "没有待执行的工作"
            assert await _count(factory, TriggerObservation) == 1
        finally:
            await _close(engine)

    asyncio.run(scenario())


def test_stop_decision_forwards_to_orchestrator_with_reason(tmp_path) -> None:
    async def scenario() -> None:
        engine, factory = await _factory(tmp_path / "bus.db")
        try:
            orchestrator_id = await seed_session(factory, kind="orchestrator")
            harness = _Harness(factory)
            await _add_user_rule(
                factory,
                "stop-device",
                source=EventSource.DEVICE.value,
                kind_pattern="device.disconnected",
                mode=TriggerMode.AUTO,
                worker="ops",
                policy="设备不可用时停止正在跑的工单。",
            )
            harness.evaluator.outcome = EvalOutcome(
                status=TriggerEvalStatus.STOP,
                decision=TriggerDecision(
                    decision="stop", reason="设备不可用，应停止刷图"
                ),
                reason="设备不可用，应停止刷图",
                run_id=None,
            )

            result = await harness.service.publish(
                source=EventSource.DEVICE,
                kind="device.disconnected",
                thread_key=_DEVICE,
                payload={"address": "127.0.0.1:5555"},
            )

            assert result.routed_to == TriggerRoute.ORCHESTRATOR
            assert result.eval_status == TriggerEvalStatus.STOP
            assert await _count(factory, Ticket) == 0
            items = await _inbox(factory, orchestrator_id, str(InboxKind.TRIGGER))
            assert len(items) == 1
            assert items[0].payload["reason"] == "设备不可用，应停止刷图"
            assert items[0].payload["rule_id"] == "stop-device"
            assert items[0].payload["payload"].startswith("{")
        finally:
            await _close(engine)

    asyncio.run(scenario())


def test_cooldown_skips_same_rule_same_thread(tmp_path) -> None:
    async def scenario() -> None:
        engine, factory = await _factory(tmp_path / "bus.db")
        try:
            harness = _Harness(factory)
            await _add_user_rule(
                factory,
                "cool-rule",
                source=EventSource.DEVICE.value,
                kind_pattern="device.disconnected",
                mode=TriggerMode.OBSERVE,
                worker="ops",
                policy="看一下。",
                cooldown_seconds=3600,
            )

            first = await harness.service.publish(
                source=EventSource.DEVICE,
                kind="device.disconnected",
                thread_key=_DEVICE,
                payload={"address": "127.0.0.1:5555"},
            )
            assert first.routed_to == TriggerRoute.OBSERVATION
            second = await harness.service.publish(
                source=EventSource.DEVICE,
                kind="device.disconnected",
                thread_key=_DEVICE,
                payload={"address": "127.0.0.1:5555"},
            )

            assert second.routed_to == TriggerRoute.NONE
            assert second.eval_status == TriggerEvalStatus.SKIPPED
            row = await _trigger_row(factory, second.trigger_id)
            assert row.eval_reason == "cooldown"
            assert row.rule_id == "cool-rule"
            assert harness.evaluator.calls == 1
            assert await _count(factory, TriggerObservation) == 1
        finally:
            await _close(engine)

    asyncio.run(scenario())


def test_daily_limit_counts_across_restart(tmp_path) -> None:
    async def scenario() -> None:
        engine, factory = await _factory(tmp_path / "bus.db")
        try:
            worker_id = await seed_session(factory, kind="worker")
            run_id = await seed_run(factory, session_id=worker_id)
            harness = _Harness(factory)
            harness.settings = Settings(triggers=TriggersSettings(eval_daily_limit=1))
            harness.evaluator.outcome = EvalOutcome(
                status=TriggerEvalStatus.ACCEPT,
                decision=TriggerDecision(
                    decision="accept",
                    title="自动建单",
                    brief="处理事件",
                    scope="处理这条事件",
                    reason="值得处理",
                ),
                reason="值得处理",
                run_id=run_id,
            )
            await _add_user_rule(
                factory,
                "daily-rule",
                source=EventSource.DEVICE.value,
                kind_pattern="device.disconnected",
                mode=TriggerMode.OBSERVE,
                worker="ops",
                policy="看一下。",
            )
            first = await harness.service.publish(
                source=EventSource.DEVICE,
                kind="device.disconnected",
                thread_key=_DEVICE,
                payload={"address": "127.0.0.1:5555"},
            )
            assert first.eval_status == TriggerEvalStatus.ACCEPT
            assert harness.evaluator.calls == 1

            restarted = _Harness(factory)
            restarted.settings = Settings(
                triggers=TriggersSettings(eval_daily_limit=1)
            )
            second = await restarted.service.publish(
                source=EventSource.DEVICE,
                kind="device.disconnected",
                thread_key=_DEVICE_TWO,
                payload={"address": "127.0.0.1:5556"},
            )
            assert second.eval_status == TriggerEvalStatus.UNAVAILABLE
            assert second.routed_to == TriggerRoute.NONE
            row = await _trigger_row(factory, second.trigger_id)
            assert row.eval_reason == "daily_limit"
            assert restarted.evaluator.calls == 0
        finally:
            await _close(engine)

    asyncio.run(scenario())


def test_eval_daily_limit_zero_never_calls_model(tmp_path) -> None:
    async def scenario() -> None:
        engine, factory = await _factory(tmp_path / "bus.db")
        try:
            harness = _Harness(factory)
            harness.settings = Settings(triggers=TriggersSettings(eval_daily_limit=0))

            result = await harness.service.publish(
                source=EventSource.SYSTEM,
                kind="system.sanity_full",
                thread_key=_SANITY,
                payload={"estimated": 135, "max_sanity": 135},
            )

            assert harness.evaluator.calls == 0
            assert result.eval_status == TriggerEvalStatus.UNAVAILABLE
            assert result.routed_to == TriggerRoute.NONE
            assert result.observation_id is None
            row = await _trigger_row(factory, result.trigger_id)
            assert row.eval_reason == "daily_limit"
            assert await _count(factory, TriggerObservation) == 0
        finally:
            await _close(engine)

    asyncio.run(scenario())


def test_recover_processes_unfinished_without_duplicate_ticket(tmp_path) -> None:
    async def scenario() -> None:
        engine, factory = await _factory(tmp_path / "bus.db")
        try:
            harness = _Harness(factory)
            # A：上次在「入表之后、建单之前」退出，但工单其实已经建出来了。
            trigger_a = await _seed_trigger(
                factory,
                source=EventSource.PIPELINE.value,
                kind="pipeline.failed",
                thread_key=_PIPELINE,
                payload={"title": "日常", "error_message": "设备断开"},
            )
            ticket_a, _ = await _seed_ticket(
                factory, source_trigger_id=trigger_a
            )
            # B：上次在「写完订阅收件箱之后、写终值之前」退出。
            trigger_b = await _seed_trigger(
                factory,
                source=EventSource.DEVICE.value,
                kind="device.connected",
                thread_key=_DEVICE,
                payload={"address": "127.0.0.1:5555"},
            )
            ticket_b, worker_b = await _seed_ticket(factory)
            async with factory() as db:
                db.add(
                    AgentInbox(
                        session_id=worker_b,
                        kind=str(InboxKind.THREAD_EVENT),
                        thread_key=_DEVICE,
                        ref_id=str(trigger_b),
                        payload={"summary": "设备已连接"},
                        status="pending",
                        created_at=utcnow(),
                    )
                )
                await db.commit()

            handled = await harness.service.recover()

            assert handled == 2
            row_a = await _trigger_row(factory, trigger_a)
            assert row_a.routed_to == TriggerRoute.TICKET.value
            assert row_a.ticket_id == ticket_a
            assert row_a.processed_at is not None
            async with factory() as db:
                tickets = await TicketRepository(db).get_by_source_trigger(trigger_a)
            assert tickets is not None and tickets.id == ticket_a
            assert await _count(factory, Ticket) == 2
            row_b = await _trigger_row(factory, trigger_b)
            assert row_b.routed_to == TriggerRoute.TICKET.value
            assert row_b.ticket_id == ticket_b
            assert len(await _inbox(factory, worker_b)) == 1
        finally:
            await _close(engine)

    asyncio.run(scenario())


def test_recover_expires_rows_older_than_24h(tmp_path) -> None:
    async def scenario() -> None:
        engine, factory = await _factory(tmp_path / "bus.db")
        try:
            harness = _Harness(factory)
            old = utcnow() - timedelta(hours=25)
            trigger_id = await _seed_trigger(
                factory,
                source=EventSource.DEVICE.value,
                kind="device.connected",
                thread_key=_DEVICE,
                payload={"address": "127.0.0.1:5555"},
                created_at=old,
            )

            assert await harness.service.recover() == 1

            row = await _trigger_row(factory, trigger_id)
            assert row.admission == TriggerAdmission.ACCEPTED.value
            assert row.eval_status == TriggerEvalStatus.SKIPPED.value
            assert row.eval_reason == "expired_after_restart"
            assert row.routed_to == TriggerRoute.NONE.value
            assert row.processed_at is not None
            assert harness.evaluator.calls == 0
        finally:
            await _close(engine)

    asyncio.run(scenario())


def test_same_thread_events_processed_in_order(tmp_path) -> None:
    async def scenario() -> None:
        engine, factory = await _factory(tmp_path / "bus.db")
        try:
            harness = _Harness(factory)
            await _add_user_rule(
                factory,
                "auto-device",
                source=EventSource.DEVICE.value,
                kind_pattern="device.connected",
                mode=TriggerMode.AUTO,
                worker="ops",
            )

            first = await harness.service.publish(
                source=EventSource.DEVICE,
                kind="device.connected",
                thread_key=_DEVICE,
                payload={"address": "127.0.0.1:5555"},
            )
            second = await harness.service.publish(
                source=EventSource.DEVICE,
                kind="device.connected",
                thread_key=_DEVICE,
                payload={"address": "127.0.0.1:5555"},
            )

            assert first.routed_to == TriggerRoute.TICKET
            assert second.routed_to == TriggerRoute.TICKET
            assert second.ticket_id == first.ticket_id
            async with factory() as db:
                ticket = await TicketRepository(db).get(first.ticket_id)
            items = await _inbox(factory, ticket.worker_session_id)
            events = [
                item for item in items if item.kind == InboxKind.THREAD_EVENT
            ]
            assert len(events) == 1
            assert events[0].ref_id == str(second.trigger_id)
            row = await _trigger_row(factory, second.trigger_id)
            assert row.eval_reason == "subscribed"
        finally:
            await _close(engine)

    asyncio.run(scenario())


# ---------------------------------------------------------------------------
# §4.5.7 边界补充
# ---------------------------------------------------------------------------
def test_rejected_unknown_kind_and_oversize_payload_are_recorded_without_routing(
    tmp_path,
) -> None:
    async def scenario() -> None:
        engine, factory = await _factory(tmp_path / "bus.db")
        try:
            harness = _Harness(factory)

            unknown = await harness.service.publish(
                source=EventSource.DEVICE,
                kind="device.ghost",
                thread_key=_DEVICE,
                payload={"address": "x"},
            )
            assert unknown.admission == TriggerAdmission.REJECTED
            assert unknown.routed_to == TriggerRoute.NONE
            row = await _trigger_row(factory, unknown.trigger_id)
            assert row.eval_reason == "unknown_kind"
            assert row.processed_at is not None
            assert row.payload == {"address": "x"}

            oversize = await harness.service.publish(
                source=EventSource.DEVICE,
                kind="device.connected",
                thread_key=_DEVICE,
                payload={"address": "x" * 20_000},
            )
            assert oversize.admission == TriggerAdmission.REJECTED
            row = await _trigger_row(factory, oversize.trigger_id)
            assert row.eval_reason == "payload_too_large"
            assert set(row.payload) == {"rejected", "size"}
            assert row.payload["size"] > 16 * 1024
            assert harness.evaluator.calls == 0
            assert await _count(factory, Ticket) == 0
        finally:
            await _close(engine)

    asyncio.run(scenario())


def test_disabled_triggers_still_forwards_attention_kind(tmp_path) -> None:
    async def scenario() -> None:
        engine, factory = await _factory(tmp_path / "bus.db")
        try:
            orchestrator_id = await seed_session(factory, kind="orchestrator")
            harness = _Harness(factory)
            harness.settings = Settings(triggers=TriggersSettings(enabled=False))

            routed = await harness.service.publish(
                source=EventSource.CORE,
                kind="core.failed",
                thread_key="core:maa",
                payload={"core_id": "maa"},
            )
            assert routed.admission == TriggerAdmission.ACCEPTED
            assert routed.routed_to == TriggerRoute.ORCHESTRATOR
            items = await _inbox(factory, orchestrator_id, str(InboxKind.TRIGGER))
            assert len(items) == 1

            quiet = await harness.service.publish(
                source=EventSource.DEVICE,
                kind="device.connected",
                thread_key=_DEVICE,
                payload={"address": "127.0.0.1:5555"},
            )
            assert quiet.admission == TriggerAdmission.IGNORED
            assert quiet.routed_to == TriggerRoute.NONE
            row = await _trigger_row(factory, quiet.trigger_id)
            assert row.eval_reason == "triggers_disabled"
        finally:
            await _close(engine)

    asyncio.run(scenario())


def test_ticket_failure_falls_back_without_losing_the_event(tmp_path) -> None:
    async def scenario() -> None:
        engine, factory = await _factory(tmp_path / "bus.db")
        try:
            harness = _Harness(factory)
            await _add_user_rule(
                factory,
                "ghost-worker",
                source=EventSource.DEVICE.value,
                kind_pattern="device.connected",
                mode=TriggerMode.AUTO,
                worker="ghost",
            )

            result = await harness.service.publish(
                source=EventSource.DEVICE,
                kind="device.connected",
                thread_key=_DEVICE,
                payload={"address": "127.0.0.1:5555"},
            )

            assert result.routed_to == TriggerRoute.NONE
            assert result.admission == TriggerAdmission.ACCEPTED
            assert await _count(factory, Ticket) == 0
            row = await _trigger_row(factory, result.trigger_id)
            assert row.rule_id == "ghost-worker"
            assert row.eval_reason == (
                f"ticket_failed:{ErrorCode.AGENT_SPEC_NOT_FOUND.value}"
            )
            assert row.processed_at is not None
        finally:
            await _close(engine)

    asyncio.run(scenario())


def test_auto_diagnose_gate_off_records_without_ticket(tmp_path) -> None:
    async def scenario() -> None:
        engine, factory = await _factory(tmp_path / "bus.db")
        try:
            harness = _Harness(factory)
            harness.settings = Settings(
                agent=AgentSettings(auto_diagnose_failures=False)
            )

            result = await harness.service.publish(
                source=EventSource.PIPELINE,
                kind="pipeline.failed",
                thread_key=_PIPELINE,
                payload={"title": "日常", "error_message": "设备断开"},
            )

            assert result.admission == TriggerAdmission.IGNORED
            assert result.routed_to == TriggerRoute.NONE
            assert await _count(factory, Ticket) == 0
            row = await _trigger_row(factory, result.trigger_id)
            assert row.rule_id is None
            assert row.eval_reason == "no_rule"
            assert harness.evaluator.calls == 0
        finally:
            await _close(engine)

    asyncio.run(scenario())


def test_trigger_inbox_renders_three_lines_and_thread_event_unchanged() -> None:
    inbox = InboxService.__new__(InboxService)
    trigger_item = AgentInbox(
        session_id="s-1",
        kind=str(InboxKind.TRIGGER),
        thread_key=_DEVICE,
        ref_id="7",
        payload={"summary": "设备已连接", "trigger_id": 7, "reason": "值得处理"},
    )
    assert inbox.render(trigger_item) == (
        "【事件】设备已连接\n话题 device:emulator-1，事件 #7\n研判：值得处理"
    )

    no_reason = trigger_item.model_copy(update={"payload": {"summary": "设备已连接", "trigger_id": 7}})
    assert inbox.render(no_reason) == "【事件】设备已连接\n话题 device:emulator-1，事件 #7"

    thread_event = trigger_item.model_copy(
        update={"kind": str(InboxKind.THREAD_EVENT)}
    )
    assert inbox.render(thread_event) == "【事件】device:emulator-1：设备已连接"
