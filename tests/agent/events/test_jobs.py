"""定时 job：理智估算与定时规则（M81 规格 §4.5.5、§6 的 ``test_jobs.py``）。

两个用例：

- ``test_schedule_rule_registers_and_removes_job``：有 cron 的启用规则注册
  ``maa-trigger-rule-<id>``，回调 ``_fire_rule`` 发一条 ``schedule.fired``（带本地
  分钟时间与去重键）；规则删除后 job 消失，``close`` 只移除自己的 job。
- ``test_sanity_watcher_publishes_once_per_observation``：``maa-trigger-sanity`` 按
  30 分钟 interval 注册，回调 ``SanityWatcher.tick`` 在估计值满时发布一次
  ``system.sanity_full``，同一观测记录重复 tick 只有一行。

调度器用真实的 :class:`~apscheduler.schedulers.asyncio.AsyncIOScheduler` 但**不
``start()``**；job 落在 ``_pending_jobs`` 里，``get_job`` 照常可读。回调直接
``await``，与规格 T7 的验证方式一致。临时库与 ``test_bus.py`` 同口径：真实规则、
话题与工单服务，只有研判与 Worker 建会话是替身。
"""

from __future__ import annotations

import asyncio
from datetime import UTC, datetime, timedelta
from types import SimpleNamespace
from typing import Any

from apscheduler.schedulers.asyncio import AsyncIOScheduler
from apscheduler.triggers.cron import CronTrigger
from apscheduler.triggers.interval import IntervalTrigger
from sqlalchemy import delete, func, select

from maa_api.agent.events.bus import (
    RULE_JOB_PREFIX,
    SANITY_INTERVAL_MINUTES,
    SANITY_JOB_ID,
    TriggerService,
)
from maa_api.agent.events.evaluator import EvalOutcome
from maa_api.agent.events.rules import TriggerRuleService
from maa_api.agent.llm.schemas.trigger_decision import TriggerDecision
from maa_api.agent.tickets.inbox import InboxService
from maa_api.agent.tickets.service import TicketService
from maa_api.agent.tickets.threads import ThreadRouter
from maa_api.db.models import (
    AgentTrigger,
    SanityObservation,
    Ticket,
    TriggerRule,
    utcnow,
)
from maa_api.db.repositories.triggers import TriggerRuleRepository
from maa_api.domain.enums import TriggerEvalStatus, TriggerMode, TriggerRoute
from maa_api.domain.errors import AppError, ErrorCode
from maa_api.settings import Settings
from tests.fakes.v3_db import make_session_factory, seed_session

_SPECS = {"ops": "none", "daily_ops": "consume"}


class _SpecRegistry:
    """最小代理定义注册表；未知名字与生产一样抛 ``AGENT_SPEC_NOT_FOUND``。"""

    def get(self, name: str) -> SimpleNamespace:
        if name not in _SPECS:
            raise AppError(
                ErrorCode.AGENT_SPEC_NOT_FOUND, f"没有名为 {name} 的代理定义"
            )
        return SimpleNamespace(kind="worker", risk_ceiling=_SPECS[name])


class _WorkerService:
    def __init__(self, factory: Any) -> None:
        self._factory = factory

    async def ensure_worker_session(self, ticket: Ticket) -> SimpleNamespace:
        session_id = await seed_session(
            self._factory, kind="worker", ticket_id=ticket.id
        )
        return SimpleNamespace(id=session_id)


class _Evaluator:
    """替身研判：固定 accept，计数便于断言「一次都没调」。"""

    def __init__(self) -> None:
        self.calls = 0

    async def evaluate(self, trigger: AgentTrigger, rule: Any) -> EvalOutcome:
        self.calls += 1
        return EvalOutcome(
            status=TriggerEvalStatus.ACCEPT,
            decision=TriggerDecision(
                decision="accept",
                title="处理事件",
                brief="只做不需要确认的操作。",
                scope="处理这条事件",
                reason="值得处理",
            ),
            reason="值得处理",
            run_id=None,
        )


class _Harness:
    """临时库上的真实服务 + 替身研判 / Worker。"""

    def __init__(self, factory: Any, *, clock: Any = utcnow) -> None:
        self.factory = factory
        self.settings = Settings()
        self.inbox = InboxService(factory)
        self.router = ThreadRouter(factory, self.inbox)
        self.registry = _SpecRegistry()
        self.ticket_service = TicketService(
            factory,
            inbox_service=self.inbox,
            thread_router=self.router,
            spec_registry=self.registry,
            broadcast=lambda kind, data: None,
        )
        self.rules = TriggerRuleService(
            factory,
            spec_registry=self.registry,
            settings_provider=lambda: self.settings,
        )
        self.evaluator = _Evaluator()
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
            broadcast=lambda kind, data: None,
            settings_provider=lambda: self.settings,
            clock=clock,
        )


async def _add_schedule_rule(
    factory: Any,
    rule_id: str,
    *,
    cron: str = "*/5 * * * *",
    mode: TriggerMode = TriggerMode.OBSERVE,
) -> None:
    now = utcnow()
    async with factory() as db:
        await TriggerRuleRepository(db).upsert(
            TriggerRule(
                id=rule_id,
                title="定点清体力",
                source="schedule",
                kind_pattern="schedule.fired",
                conditions={},
                mode=str(mode),
                worker="ops",
                policy="",
                cooldown_seconds=0,
                cron=cron,
                enabled=True,
                origin="user",
                created_at=now,
                updated_at=now,
            )
        )
        await db.commit()


async def _count(factory: Any, model: Any) -> int:
    async with factory() as db:
        return int(
            (await db.execute(select(func.count()).select_from(model))).scalar_one()
        )


async def _last_trigger(factory: Any) -> AgentTrigger:
    async with factory() as db:
        return (
            (await db.execute(select(AgentTrigger).order_by(AgentTrigger.id.desc())))
            .scalars()
            .first()
        )


def test_schedule_rule_registers_and_removes_job(tmp_path) -> None:
    """规则增删驱动 ``maa-trigger-rule-<id>`` 的注册 / 移除；回调发一条定时事件。"""
    clock = datetime(2026, 11, 5, 0, 3, 45, tzinfo=UTC)

    async def scenario() -> None:
        engine, factory = await make_session_factory(tmp_path / "jobs.db")
        try:
            harness = _Harness(factory, clock=lambda: clock)
            rule_id = "nightly-sanity"
            await _add_schedule_rule(factory, rule_id)
            scheduler = AsyncIOScheduler()
            scheduler.add_job(
                lambda: None, "interval", minutes=7, id="someone-else"
            )
            harness.service.start_jobs(scheduler)
            await harness.service._jobs_ready

            job_id = f"{RULE_JOB_PREFIX}{rule_id}"
            job = scheduler.get_job(job_id)
            assert job is not None, [item.id for item in scheduler.get_jobs()]
            assert isinstance(job.trigger, CronTrigger)
            fields = {field.name: str(field) for field in job.trigger.fields}
            assert fields["minute"] == "*/5"
            assert job.coalesce is True
            assert job.max_instances == 1

            sanity = scheduler.get_job(SANITY_JOB_ID)
            assert sanity is not None
            assert isinstance(sanity.trigger, IntervalTrigger)
            assert sanity.trigger.interval == timedelta(
                minutes=SANITY_INTERVAL_MINUTES
            )
            assert sanity.coalesce is True
            assert sanity.max_instances == 1

            # 回调直接 await（与调度器只存在 pending 列表无关）。
            await harness.service._fire_rule(rule_id)
            await harness.service._fire_rule(rule_id)  # 同一分钟：去重
            row = await _last_trigger(factory)
            assert row.kind == "schedule.fired"
            assert row.source == "schedule"
            assert row.thread_key == f"schedule:{rule_id}"
            assert row.rule_id == rule_id
            assert row.routed_to == TriggerRoute.OBSERVATION.value
            payload = dict(row.payload)
            assert payload["rule_id"] == rule_id
            # 本地时区是 Asia/Shanghai；分钟截断、秒为 0，且同一分钟第二次是重复。
            scheduled_local = datetime.fromisoformat(payload["scheduled_for"])
            assert scheduled_local.utcoffset() == timedelta(hours=8)
            assert (scheduled_local.hour, scheduled_local.minute) == (8, 3)
            assert scheduled_local.second == 0
            assert row.dedupe_key == f"schedule:{rule_id}:{payload['scheduled_for']}"
            assert await _count(factory, AgentTrigger) == 1

            # 删除规则 → 监听者移除 job；别人的 job 不动。
            await harness.rules.delete_rule(rule_id)
            assert scheduler.get_job(job_id) is None
            assert scheduler.get_job("someone-else") is not None

            await harness.service.close()
            assert scheduler.get_job(SANITY_JOB_ID) is None
            assert scheduler.get_job("someone-else") is not None
        finally:
            await engine.dispose()

    asyncio.run(scenario())


def test_sanity_watcher_publishes_once_per_observation(tmp_path) -> None:
    """估计值已满时发一条 ``system.sanity_full``；同一观测重复 tick 只有一行。"""
    now = datetime(2026, 11, 5, 9, 0, 0, tzinfo=UTC)

    async def scenario() -> None:
        engine, factory = await make_session_factory(tmp_path / "sanity.db")
        try:
            harness = _Harness(factory, clock=lambda: now)
            async with factory() as db:
                db.add(
                    SanityObservation(
                        pipeline_id=None,
                        task_id=None,
                        stage_code="Fight",
                        current_sanity=130,
                        max_sanity=135,
                        created_at=now.replace(tzinfo=None) - timedelta(minutes=60),
                    )
                )
                await db.commit()

            scheduler = AsyncIOScheduler()
            harness.service.start_jobs(scheduler)
            await harness.service._jobs_ready
            job = scheduler.get_job(SANITY_JOB_ID)
            assert job is not None
            await job.func()
            await job.func()

            async with factory() as db:
                rows = list(
                    (
                        await db.execute(
                            select(AgentTrigger).where(
                                AgentTrigger.kind == "system.sanity_full"
                            )
                        )
                    )
                    .scalars()
                    .all()
                )
            assert len(rows) == 1, rows
            row = rows[0]
            assert row.dedupe_key.startswith("sanity_full:")
            assert row.source == "system"
            assert row.thread_key == "system:sanity"
            assert row.payload["estimated"] == 135
            assert row.payload["max_sanity"] == 135
            assert row.payload["observed"] == 130
            assert row.payload["stage_code"] == "Fight"

            # 观测超过 24 小时：不再估算，tick 不发事件（即使去重表已清空）。
            async with factory() as db:
                observation = (
                    await db.execute(select(SanityObservation))
                ).scalars().first()
                observation.created_at = now.replace(tzinfo=None) - timedelta(hours=25)
                await db.execute(delete(AgentTrigger))
                await db.commit()
            await job.func()
            assert await _count(factory, AgentTrigger) == 0

            await harness.service.close()
        finally:
            await engine.dispose()

    asyncio.run(scenario())
