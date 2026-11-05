"""``GoalService`` 的建立、规划/复盘落库、状态机与复盘 job（M82 规格 §6）。

临时库与真实服务同口径（``tests/fakes/v3_db.py``）：工单、话题、收件箱、事件、
定时任务与养成计划都是真实实现，只有 Worker 建会话与研判是替身；调度器用真实
:class:`AsyncIOScheduler` 但**不** ``start()``（job 落在 ``_pending_jobs`` 里，
``get_job`` 照常可读，与 M81 的 ``test_jobs.py`` 同口径）。

M82 T5 之前目标事件种类还没登记进 ``KNOWN_KINDS``，所以本文件用 autouse fixture
把三种目标事件临时登记上，让 ``TriggerService`` 的去重、订阅直通与「交总控」
分支照常工作；T5 落地后这层 fixture 不会改变断言。
"""

from __future__ import annotations

import asyncio
from datetime import UTC, datetime, timedelta, timezone
from types import SimpleNamespace
from typing import Any

import pytest
from apscheduler.schedulers.asyncio import AsyncIOScheduler
from apscheduler.triggers.cron import CronTrigger
from sqlalchemy import func, select

from maa_api.agent.events.bus import TriggerService
from maa_api.agent.events.evaluator import EvalOutcome
from maa_api.agent.events.kinds import KNOWN_KINDS, EventKind
from maa_api.agent.events.rules import TriggerRuleService
from maa_api.agent.goals import (
    GOAL_REVIEW_JOB_PREFIX,
    GoalCreate,
    GoalService,
    GoalTarget,
    GoalUpdate,
    planning_brief,
    planning_scope,
)
from maa_api.agent.registry import ToolContext
from maa_api.agent.tickets.inbox import InboxService
from maa_api.agent.tickets.service import TicketService
from maa_api.agent.tickets.threads import ThreadRouter
from maa_api.db.models import (
    AgentInbox,
    AgentTrigger,
    Goal,
    GoalMilestone,
    UpgradePlan,
    utcnow,
)
from maa_api.db.repositories.goals import GoalMilestoneRepository, GoalRepository
from maa_api.db.repositories.ticket import (
    MandateRepository,
    ThreadSubscriptionRepository,
    TicketRepository,
)
from maa_api.domain.enums import (
    CallerType,
    EventSource,
    GoalStatus,
    MandateStrength,
    MilestoneStatus,
    RiskLevel,
    TicketOrigin,
    TicketStatus,
    TriggerEvalStatus,
)
from maa_api.domain.errors import AppError, ErrorCode
from maa_api.services.account.plans import UpgradePlanService
from maa_api.services.queue_service import QueueService
from maa_api.services.schedule_service import ScheduleService, ScheduleWrite
from maa_api.settings import GoalsSettings, Settings
from tests.fakes.v3_db import make_session_factory, seed_session

#: 固定时钟：2026-11-05 12:00 UTC（naive），复盘与截止判断不随真实时间漂移。
NOW = datetime(2026, 11, 5, 12, 0)
#: 一个在 ``NOW`` 之前、已经逾期的里程碑时间。
OVERDUE = datetime(2026, 11, 4, 12, 0)
#: 一个在 ``NOW`` 之后的截止时间（北京时间 2026-11-30 22:00）。
FUTURE_DEADLINE = datetime(2026, 11, 30, 22, 0, tzinfo=timezone(timedelta(hours=8)))


@pytest.fixture(autouse=True)
def _register_goal_event_kinds(monkeypatch: pytest.MonkeyPatch) -> None:
    """T5 之前临时登记三种目标事件，让真实事件总线能走完整管线。"""
    kinds = {
        "goal.review_due": ("目标「{title}」到复盘时间", None),
        "goal.completed": ("目标「{title}」已完成", lambda _payload: True),
        "goal.deadline_passed": ("目标「{title}」过了截止时间", lambda _payload: True),
    }
    for kind, (summary, attention) in kinds.items():
        monkeypatch.setitem(
            KNOWN_KINDS,
            kind,
            EventKind(
                kind=kind,
                sources=frozenset({EventSource.GOAL.value}),
                thread_prefix="goal:",
                summary=summary,
                attention=attention,
            ),
        )


class _SpecRegistry:
    """最小代理定义注册表；未知名字与生产一样抛 ``AGENT_SPEC_NOT_FOUND``。"""

    def __init__(self, specs: dict[str, str] | None = None) -> None:
        self._specs = (
            dict(specs)
            if specs is not None
            else {"planner": "consume"}
        )

    def get(self, name: str) -> SimpleNamespace:
        if name not in self._specs:
            raise AppError(
                ErrorCode.AGENT_SPEC_NOT_FOUND, f"没有名为 {name} 的代理定义"
            )
        return SimpleNamespace(kind="worker", risk_ceiling=self._specs[name])


class _WorkerService:
    """替身 Worker 服务：建一个 ``kind=worker`` 的会话行，不跑模型。"""

    def __init__(self, factory: Any) -> None:
        self._factory = factory

    async def ensure_worker_session(self, ticket: Any) -> SimpleNamespace:
        session_id = await seed_session(
            self._factory, kind="worker", ticket_id=ticket.id
        )
        return SimpleNamespace(id=session_id)


class _Evaluator:
    """替身研判；目标事件没有规则匹配，正常不会被调用。"""

    async def evaluate(self, trigger: Any, rule: Any) -> EvalOutcome:
        return EvalOutcome(
            status=TriggerEvalStatus.SKIPPED,
            decision=None,
            reason="test",
            run_id=None,
        )


class _FakeRunner:
    """``ScheduleService`` 只需要这两个发布入口。"""

    async def publish_pipeline(self, pipeline_id: str) -> None:
        del pipeline_id

    async def publish_queue_changed(self) -> None:
        return None


class _Harness:
    """一套临时库上的真实服务 + 替身 Worker 会话 / 研判。"""

    def __init__(
        self,
        factory: Any,
        *,
        settings: Settings | None = None,
        specs: dict[str, str] | None = None,
    ) -> None:
        self.factory = factory
        self.settings = settings or Settings()
        self.settings_provider = lambda: self.settings
        self.inbox = InboxService(factory)
        self.router = ThreadRouter(factory, self.inbox)
        self.registry = _SpecRegistry(specs)
        self.broadcasts: list[dict[str, Any]] = []
        self.ticket_service = TicketService(
            factory,
            inbox_service=self.inbox,
            thread_router=self.router,
            spec_registry=self.registry,
            broadcast=self._record_ticket_event,
        )
        self.worker_service = _WorkerService(factory)
        self.ticket_service.bind_worker_service(lambda: self.worker_service)
        self.scheduler = AsyncIOScheduler()
        self.schedule_service = ScheduleService(
            factory, self.scheduler, QueueService(factory), _FakeRunner()
        )
        self.plan_service = UpgradePlanService(
            factory,
            schedule_service=self.schedule_service,
            settings_provider=self.settings_provider,
        )
        self.rules = TriggerRuleService(
            factory,
            spec_registry=self.registry,
            settings_provider=self.settings_provider,
        )
        self.trigger_service = TriggerService(
            factory,
            app=SimpleNamespace(
                state=SimpleNamespace(worker_service=self.worker_service)
            ),
            rules=self.rules,
            evaluator=_Evaluator(),
            ticket_service=self.ticket_service,
            thread_router=self.router,
            orchestrator_service=SimpleNamespace(),
            broadcast=self._record_broadcast,
            settings_provider=self.settings_provider,
            clock=lambda: NOW,
        )
        self.goals = GoalService(
            factory,
            app=SimpleNamespace(state=SimpleNamespace()),
            ticket_service=self.ticket_service,
            thread_router=self.router,
            trigger_service=self.trigger_service,
            upgrade_plan_service=self.plan_service,
            get_schedule_service=lambda: self.schedule_service,
            settings_provider=self.settings_provider,
            clock=lambda: NOW,
        )

    def _record_ticket_event(self, message_type: str, data: dict[str, Any]) -> None:
        self.broadcasts.append({"type": message_type, **data})

    def _record_broadcast(self, message_type: str, data: Any) -> None:
        self.broadcasts.append({"type": message_type, "data": data})

    async def start(self) -> None:
        self.goals.start_jobs(self.scheduler)
        await self.goals._jobs_ready

    async def close(self) -> None:
        await self.goals.close()

    async def create_goal(self, **overrides: Any) -> Any:
        values: dict[str, Any] = {
            "title": "银灰精二专三",
            "target": GoalTarget(
                text="月底前把银灰练到精二、三技能专三",
                operators=[{"operator": "银灰", "elite": 2}],
            ),
            "deadline": FUTURE_DEADLINE,
        }
        values.update(overrides)
        return await self.goals.create(GoalCreate(**values))

    async def create_schedule(self, name: str = "计划任务", **overrides: Any) -> Any:
        values: dict[str, Any] = {
            "name": name,
            "cron": "0 7 * * *",
            "timezone": "Asia/Shanghai",
            "template": [{"name": "StartUp"}],
            "priority": 2,
        }
        values.update(overrides)
        return await self.schedule_service.create(ScheduleWrite(**values))

    async def extra_ticket(self, goal_id: str, title: str = "复盘：银灰") -> Any:
        ticket, _mandate = await self.ticket_service.create(
            kind="planner",
            title=title,
            scope="复盘目标并调整它建立的计划与定时任务",
            origin=TicketOrigin.GOAL,
            source=EventSource.GOAL,
            sender="web",
            strength=MandateStrength.INFERRED,
            risk_ceiling=RiskLevel.NONE,
            goal_id=goal_id,
        )
        return ticket


async def _seed_plan(
    factory: Any,
    *,
    session_id: str | None,
    status: str = "active",
    schedule_id: str | None = None,
    created_at: datetime | None = None,
) -> str:
    async with factory() as session:
        plan = UpgradePlan(
            name="养成计划",
            status=status,
            targets=[],
            requirements={"龙门币": 100},
            progress={"stages": [{"stage": "1-7", "runs": 3}]},
            schedule_id=schedule_id,
            session_id=session_id,
            created_at=created_at or utcnow(),
            updated_at=created_at or utcnow(),
        )
        session.add(plan)
        await session.commit()
        return str(plan.id)


async def _attach_to_goal(
    factory: Any,
    goal_id: str,
    *,
    schedule_ids: list[str] | None = None,
    upgrade_plan_id: str | None = None,
) -> None:
    async with factory() as session:
        goal = await session.get(Goal, goal_id)
        assert goal is not None
        if schedule_ids is not None:
            goal.schedule_ids = list(schedule_ids)
        if upgrade_plan_id is not None:
            goal.upgrade_plan_id = upgrade_plan_id
        await session.commit()


async def _seed_milestone(
    factory: Any,
    goal_id: str,
    *,
    ord: int = 1,
    title: str = "练到精二",
    due_at: datetime | None = None,
    status: str = MilestoneStatus.PENDING.value,
) -> None:
    async with factory() as session:
        session.add(
            GoalMilestone(
                goal_id=goal_id,
                ord=ord,
                title=title,
                due_at=due_at,
                status=status,
            )
        )
        await session.commit()


async def _get_goal(factory: Any, goal_id: str) -> Goal | None:
    async with factory() as session:
        return await GoalRepository(session).get(goal_id)


async def _get_milestones(factory: Any, goal_id: str) -> list[GoalMilestone]:
    async with factory() as session:
        return await GoalMilestoneRepository(session).list_by_goal(goal_id)


async def _triggers(factory: Any, kind: str) -> list[AgentTrigger]:
    async with factory() as session:
        result = await session.execute(
            select(AgentTrigger)
            .where(AgentTrigger.kind == kind)
            .order_by(AgentTrigger.id.asc())
        )
        return list(result.scalars().all())


async def _trigger_count(factory: Any, kind: str) -> int:
    async with factory() as session:
        return int(
            (
                await session.execute(
                    select(func.count())
                    .select_from(AgentTrigger)
                    .where(AgentTrigger.kind == kind)
                )
            ).scalar_one()
        )


def _job(harness: _Harness, goal_id: str) -> Any:
    try:
        return harness.scheduler.get_job(f"{GOAL_REVIEW_JOB_PREFIX}{goal_id}")
    except Exception:  # noqa: BLE001 - JobLookupError
        return None


def _report(status: str = "ready", details: dict[str, Any] | None = None) -> SimpleNamespace:
    return SimpleNamespace(status=status, details=details or {})


# ---------------------------------------------------------------------------
# 建立与规划
# ---------------------------------------------------------------------------
def test_create_builds_explicit_planning_ticket_and_subscribes(tmp_path) -> None:
    """规划工单 explicit/consume、带 goal_id、订阅 goal:<id>、job 已注册。"""

    async def scenario() -> None:
        engine, factory = await make_session_factory(tmp_path / "create.db")
        harness = _Harness(factory)
        try:
            await harness.start()
            detail = await harness.goals.create(
                GoalCreate(
                    title="银灰精二专三",
                    target=GoalTarget(text="月底前把银灰练到精二、三技能专三"),
                    deadline=FUTURE_DEADLINE,
                )
            )
            assert detail.status == GoalStatus.ACTIVE.value
            assert detail.planning_ticket_id

            async with factory() as session:
                ticket = await TicketRepository(session).get(
                    detail.planning_ticket_id
                )
                mandate = await MandateRepository(session).get_by_ticket(
                    detail.planning_ticket_id
                )
                subscribed = await ThreadSubscriptionRepository(
                    session
                ).active_ticket_ids(f"goal:{detail.id}")
            assert ticket is not None and ticket.kind == "planner"
            assert ticket.origin == TicketOrigin.GOAL.value
            assert ticket.goal_id == detail.id
            assert mandate is not None
            assert mandate.strength == MandateStrength.EXPLICIT.value
            assert mandate.risk_ceiling == RiskLevel.CONSUME.value
            assert mandate.source == EventSource.GOAL.value
            assert detail.planning_ticket_id in subscribed

            async with factory() as session:
                inbox = list(
                    (
                        await session.execute(
                            select(AgentInbox).where(
                                AgentInbox.session_id == ticket.worker_session_id
                            )
                        )
                    ).scalars().all()
                )
            assert any(
                "目标「银灰精二专三」刚建立" in str(item.payload.get("text", ""))
                for item in inbox
            )

            job = _job(harness, detail.id)
            assert job is not None, [item.id for item in harness.scheduler.get_jobs()]
            assert isinstance(job.trigger, CronTrigger)
            assert job.coalesce is True
            assert job.max_instances == 1
            assert job.misfire_grace_time == 300
            # 规划说明写在 create 的 brief 里（不是模型自己拼的）。
            async with factory() as session:
                stored_goal = await GoalRepository(session).get(detail.id)
            assert stored_goal is not None
            assert "目标「银灰精二专三」刚建立" in planning_brief(stored_goal)
            assert planning_scope(stored_goal).startswith("为目标「银灰精二专三」做规划")
        finally:
            await harness.close()
            engine.sync_engine.dispose()

    asyncio.run(scenario())


def test_create_rejects_bad_cron_and_disabled_goals(tmp_path) -> None:
    """非法复盘 cron → SCHEDULE_CRON_INVALID；功能关闭 → AGENT_DISABLED。"""

    async def scenario() -> None:
        engine, factory = await make_session_factory(tmp_path / "reject.db")
        harness = _Harness(factory)
        try:
            with pytest.raises(AppError) as invalid:
                await harness.goals.create(
                    GoalCreate(
                        title="目标",
                        target=GoalTarget(text="做一件事"),
                        review_cron="每天一次",
                    )
                )
            assert invalid.value.code == ErrorCode.SCHEDULE_CRON_INVALID

            harness.settings.goals.enabled = False
            with pytest.raises(AppError) as disabled:
                await harness.goals.create(
                    GoalCreate(title="目标", target=GoalTarget(text="做一件事"))
                )
            assert disabled.value.code == ErrorCode.AGENT_DISABLED
        finally:
            await harness.close()
            engine.sync_engine.dispose()

    asyncio.run(scenario())


def test_apply_plan_writes_milestones_and_links_plan_by_worker_session(
    tmp_path,
) -> None:
    """里程碑按序、第一条 active；计划按 Worker 会话关联，定时任务并入。"""

    async def scenario() -> None:
        engine, factory = await make_session_factory(tmp_path / "plan.db")
        harness = _Harness(factory)
        try:
            await harness.start()
            detail = await harness.create_goal()
            async with factory() as session:
                ticket = await TicketRepository(session).get(
                    detail.planning_ticket_id
                )
            assert ticket is not None and ticket.worker_session_id

            plan_schedule = await harness.create_schedule("计划里的任务")
            detail_schedule = await harness.create_schedule("说明里的任务")
            worker_session = await seed_session(factory, kind="worker")
            plan_id = await _seed_plan(
                factory,
                session_id=ticket.worker_session_id,
                schedule_id=plan_schedule.id,
                created_at=datetime(2026, 11, 1),
            )
            # 更晚创建、但会话不属于这张工单的计划不能被误关联。
            await _seed_plan(
                factory,
                session_id=worker_session,
                status="active",
                created_at=datetime(2026, 11, 3),
            )

            await harness.goals.on_ticket_report(
                ticket,
                _report(
                    details={
                        "goal_plan": {
                            "milestones": [
                                {"title": "精二", "due_at": "2026-11-20T22:00:00+08:00"},
                                {"title": "三技能专三", "metrics": {"how": "skill"}},
                            ],
                            "schedule_ids": [detail_schedule.id],
                            "summary": "先刷材料再专精",
                        }
                    }
                ),
            )

            goal = await _get_goal(factory, detail.id)
            assert goal is not None
            assert goal.upgrade_plan_id == plan_id
            assert goal.schedule_ids == [detail_schedule.id, plan_schedule.id]
            assert goal.progress["planning"] == "done"
            assert goal.progress["summary"] == "先刷材料再专精"

            milestones = await _get_milestones(factory, detail.id)
            assert [row.ord for row in milestones] == [1, 2]
            assert [row.title for row in milestones] == ["精二", "三技能专三"]
            assert milestones[0].status == MilestoneStatus.ACTIVE.value
            assert milestones[1].status == MilestoneStatus.PENDING.value
            assert milestones[0].due_at == datetime(2026, 11, 20, 14, 0)
        finally:
            await harness.close()
            engine.sync_engine.dispose()

    asyncio.run(scenario())


def test_apply_plan_drops_missing_schedules_and_records_note(tmp_path) -> None:
    """不存在的定时任务丢弃，并在 ``progress.notes`` 记一条。"""

    async def scenario() -> None:
        engine, factory = await make_session_factory(tmp_path / "missing.db")
        harness = _Harness(factory)
        try:
            await harness.start()
            detail = await harness.create_goal()
            async with factory() as session:
                ticket = await TicketRepository(session).get(
                    detail.planning_ticket_id
                )
            schedule = await harness.create_schedule()
            missing = "00000000-0000-4000-8000-000000000999"
            await harness.goals.on_ticket_report(
                ticket,
                _report(
                    details={
                        "goal_plan": {
                            "milestones": [{"title": "一"}],
                            "schedule_ids": [schedule.id, missing],
                        }
                    }
                ),
            )

            goal = await _get_goal(factory, detail.id)
            assert goal is not None
            assert goal.schedule_ids == [schedule.id]
            notes = goal.progress.get("notes") or []
            assert any("已不存在" in note for note in notes)
        finally:
            await harness.close()
            engine.sync_engine.dispose()

    asyncio.run(scenario())


def test_invalid_plan_details_marks_planning_invalid(tmp_path) -> None:
    """规划结果不合法 → ``planning=invalid_details``，里程碑不动。"""

    async def scenario() -> None:
        engine, factory = await make_session_factory(tmp_path / "invalid.db")
        harness = _Harness(factory)
        try:
            await harness.start()
            detail = await harness.create_goal()
            async with factory() as session:
                ticket = await TicketRepository(session).get(
                    detail.planning_ticket_id
                )
            await harness.goals.on_ticket_report(
                ticket,
                _report(
                    details={"goal_plan": {"milestones": [{"title": "保留"}]}}
                ),
            )
            await harness.goals.on_ticket_report(
                ticket, _report(details={"goal_plan": {"milestones": []}})
            )

            goal = await _get_goal(factory, detail.id)
            assert goal is not None
            assert goal.progress["planning"] == "invalid_details"
            assert any(
                "规划结果" in note for note in goal.progress.get("notes") or []
            )
            milestones = await _get_milestones(factory, detail.id)
            assert [row.title for row in milestones] == ["保留"]
        finally:
            await harness.close()
            engine.sync_engine.dispose()

    asyncio.run(scenario())


# ---------------------------------------------------------------------------
# 复盘
# ---------------------------------------------------------------------------
def test_review_marks_overdue_milestones_missed_and_publishes(tmp_path) -> None:
    """逾期里程碑标 missed、newly_missed 入快照、发一行 review_due。"""

    async def scenario() -> None:
        engine, factory = await make_session_factory(tmp_path / "review.db")
        harness = _Harness(factory)
        try:
            await harness.start()
            detail = await harness.create_goal()
            await _seed_milestone(factory, detail.id, due_at=OVERDUE)
            missing_schedule = "00000000-0000-4000-8000-000000000777"
            await _attach_to_goal(factory, detail.id, schedule_ids=[missing_schedule])

            await harness.goals.review(detail.id)

            milestones = await _get_milestones(factory, detail.id)
            assert [row.status for row in milestones] == [
                MilestoneStatus.MISSED.value
            ]
            goal = await _get_goal(factory, detail.id)
            assert goal is not None
            assert goal.last_review_at == NOW
            snapshot = goal.progress["last_snapshot"]
            assert [item["ord"] for item in snapshot["newly_missed"]] == [1]
            assert snapshot["schedules"]["missing"] == [missing_schedule]
            assert snapshot["days_left"] is not None

            rows = await _triggers(factory, "goal.review_due")
            assert len(rows) == 1
            assert rows[0].source == EventSource.GOAL.value
            assert rows[0].thread_key == f"goal:{detail.id}"
            assert rows[0].dedupe_key.startswith(f"goal:{detail.id}:review:")
            assert rows[0].payload["snapshot"]["newly_missed"][0]["ord"] == 1
        finally:
            await harness.close()
            engine.sync_engine.dispose()

    asyncio.run(scenario())


def test_review_completes_goal_when_plan_completed(tmp_path) -> None:
    """养成计划完成且里程碑全 done → 目标 done、发 completed、移除 job。"""

    async def scenario() -> None:
        engine, factory = await make_session_factory(tmp_path / "complete.db")
        harness = _Harness(factory)
        try:
            await harness.start()
            detail = await harness.create_goal()
            plan_id = await _seed_plan(factory, session_id=None, status="completed")
            await _attach_to_goal(factory, detail.id, upgrade_plan_id=plan_id)
            await _seed_milestone(
                factory,
                detail.id,
                ord=1,
                title="精二",
                status=MilestoneStatus.DONE.value,
            )
            await _seed_milestone(
                factory,
                detail.id,
                ord=2,
                title="专三",
                status=MilestoneStatus.SKIPPED.value,
            )

            await harness.goals.review(detail.id)

            goal = await _get_goal(factory, detail.id)
            assert goal is not None
            assert goal.status == GoalStatus.DONE.value
            assert goal.ended_at == NOW
            rows = await _triggers(factory, "goal.completed")
            assert len(rows) == 1
            assert rows[0].payload["via"] == "upgrade_plan"
            assert _job(harness, detail.id) is None
        finally:
            await harness.close()
            engine.sync_engine.dispose()

    asyncio.run(scenario())


def test_deadline_passed_published_once(tmp_path) -> None:
    """截止过了：两次复盘只发一条 deadline_passed。"""

    async def scenario() -> None:
        engine, factory = await make_session_factory(tmp_path / "deadline.db")
        harness = _Harness(factory)
        try:
            await harness.start()
            detail = await harness.create_goal(
                deadline=datetime(2026, 11, 1, 12, 0, tzinfo=UTC)
            )
            await _seed_milestone(factory, detail.id, due_at=None)

            await harness.goals.review(detail.id)
            await harness.goals.review(detail.id)

            assert await _trigger_count(factory, "goal.deadline_passed") == 1
            goal = await _get_goal(factory, detail.id)
            assert goal is not None
            assert goal.status == GoalStatus.ACTIVE.value
            assert goal.progress["deadline_notified"] is True
            rows = await _triggers(factory, "goal.deadline_passed")
            assert rows[0].payload["deadline"].endswith("Z")
        finally:
            await harness.close()
            engine.sync_engine.dispose()

    asyncio.run(scenario())


def test_review_report_updates_milestones_and_can_finish(tmp_path) -> None:
    """复盘汇报按 ord 改里程碑，goal_status=done 时完成目标。"""

    async def scenario() -> None:
        engine, factory = await make_session_factory(tmp_path / "report.db")
        harness = _Harness(factory)
        try:
            await harness.start()
            detail = await harness.create_goal()
            await _seed_milestone(factory, detail.id, ord=1, title="精二")
            await _seed_milestone(
                factory,
                detail.id,
                ord=2,
                title="专三",
                status=MilestoneStatus.DONE.value,
            )
            review_ticket = await harness.extra_ticket(detail.id)

            await harness.goals.on_ticket_report(
                review_ticket,
                _report(
                    details={
                        "goal_review": {
                            "milestones": [{"ord": 1, "status": "done"}],
                            "summary": "全部完成",
                            "goal_status": "done",
                        }
                    }
                ),
            )

            milestones = await _get_milestones(factory, detail.id)
            assert [row.status for row in milestones] == [
                MilestoneStatus.DONE.value,
                MilestoneStatus.DONE.value,
            ]
            goal = await _get_goal(factory, detail.id)
            assert goal is not None
            assert goal.status == GoalStatus.DONE.value
            assert _job(harness, detail.id) is None
        finally:
            await harness.close()
            engine.sync_engine.dispose()

    asyncio.run(scenario())


# ---------------------------------------------------------------------------
# 暂停、恢复、取消
# ---------------------------------------------------------------------------
def test_pause_and_resume_toggle_schedules_and_job(tmp_path) -> None:
    """暂停停用定时任务并 pause job；恢复反过来。"""

    async def scenario() -> None:
        engine, factory = await make_session_factory(tmp_path / "pause.db")
        harness = _Harness(factory)
        try:
            await harness.start()
            detail = await harness.create_goal()
            schedule = await harness.create_schedule()
            await _attach_to_goal(factory, detail.id, schedule_ids=[schedule.id])

            await harness.goals.update(
                detail.id, GoalUpdate(status="paused")
            )
            goal = await _get_goal(factory, detail.id)
            assert goal is not None and goal.status == GoalStatus.PAUSED.value
            assert goal.progress["paused_schedule_ids"] == [schedule.id]
            view = await harness.schedule_service.get(schedule.id)
            assert view.enabled is False
            job = _job(harness, detail.id)
            assert job is not None
            assert getattr(job, "next_run_time", "missing") is None

            await harness.goals.update(
                detail.id, GoalUpdate(status="active")
            )
            goal = await _get_goal(factory, detail.id)
            assert goal is not None and goal.status == GoalStatus.ACTIVE.value
            assert goal.progress["paused_schedule_ids"] == []
            view = await harness.schedule_service.get(schedule.id)
            assert view.enabled is True
            job = _job(harness, detail.id)
            assert job is not None
            assert getattr(job, "next_run_time", None) is not None
        finally:
            await harness.close()
            engine.sync_engine.dispose()

    asyncio.run(scenario())


def test_cancel_cancels_plan_tickets_and_job(tmp_path) -> None:
    """取消：计划 cancelled、目标工单 cancelled、job 移除。"""

    async def scenario() -> None:
        engine, factory = await make_session_factory(tmp_path / "cancel.db")
        harness = _Harness(factory)
        try:
            await harness.start()
            detail = await harness.create_goal()
            async with factory() as session:
                planning = await TicketRepository(session).get(
                    detail.planning_ticket_id
                )
            assert planning is not None
            schedule = await harness.create_schedule()
            plan_id = await _seed_plan(
                factory,
                session_id=planning.worker_session_id,
                status="active",
                schedule_id=schedule.id,
            )
            await _attach_to_goal(
                factory,
                detail.id,
                schedule_ids=[schedule.id],
                upgrade_plan_id=plan_id,
            )
            extra = await harness.extra_ticket(detail.id, title="另开工单")

            await harness.goals.update(
                detail.id, GoalUpdate(status="cancelled")
            )

            goal = await _get_goal(factory, detail.id)
            assert goal is not None
            assert goal.status == GoalStatus.CANCELLED.value
            assert goal.ended_at == NOW
            async with factory() as session:
                plan = await session.get(UpgradePlan, plan_id)
                planning_after = await TicketRepository(session).get(planning.id)
                extra_after = await TicketRepository(session).get(extra.id)
            assert plan is not None and plan.status == "cancelled"
            assert planning_after is not None
            assert planning_after.status == TicketStatus.CANCELLED.value
            assert extra_after is not None
            assert extra_after.status == TicketStatus.CANCELLED.value
            assert _job(harness, detail.id) is None
        finally:
            await harness.close()
            engine.sync_engine.dispose()

    asyncio.run(scenario())


def test_update_ended_goal_raises_goal_ended(tmp_path) -> None:
    """已结束的目标再改 → GOAL_ENDED。"""

    async def scenario() -> None:
        engine, factory = await make_session_factory(tmp_path / "ended.db")
        harness = _Harness(factory)
        try:
            await harness.start()
            detail = await harness.create_goal()
            await harness.goals.update(
                detail.id, GoalUpdate(status="cancelled")
            )
            with pytest.raises(AppError) as ended:
                await harness.goals.update(
                    detail.id, GoalUpdate(title="新标题")
                )
            assert ended.value.code == ErrorCode.GOAL_ENDED
        finally:
            await harness.close()
            engine.sync_engine.dispose()

    asyncio.run(scenario())


# ---------------------------------------------------------------------------
# 必要边界（spec §4.5.8、§4.4、§4.5.5）
# ---------------------------------------------------------------------------
def test_same_minute_review_is_deduped(tmp_path) -> None:
    """同一分钟触发两次复盘：dedupe_key 相同，只入表一次。"""

    async def scenario() -> None:
        engine, factory = await make_session_factory(tmp_path / "dedupe.db")
        harness = _Harness(factory)
        try:
            await harness.start()
            detail = await harness.create_goal()
            await _seed_milestone(factory, detail.id, due_at=None)

            await harness.goals.review(detail.id)
            await harness.goals.review(detail.id)

            assert await _trigger_count(factory, "goal.review_due") == 1
        finally:
            await harness.close()
            engine.sync_engine.dispose()

    asyncio.run(scenario())


def test_review_is_skipped_when_goals_disabled(tmp_path) -> None:
    """`goals.enabled=false` 时复盘直接返回，不发事件。"""

    async def scenario() -> None:
        engine, factory = await make_session_factory(tmp_path / "disabled.db")
        harness = _Harness(factory)
        try:
            await harness.start()
            detail = await harness.create_goal()
            await _seed_milestone(factory, detail.id, due_at=OVERDUE)
            harness.settings.goals.enabled = False

            await harness.goals.review(detail.id)

            assert await _trigger_count(factory, "goal.review_due") == 0
            goal = await _get_goal(factory, detail.id)
            assert goal is not None and goal.last_review_at is None
            milestones = await _get_milestones(factory, detail.id)
            assert milestones[0].status == MilestoneStatus.PENDING.value
        finally:
            await harness.close()
            engine.sync_engine.dispose()

    asyncio.run(scenario())


def test_planning_ticket_failure_keeps_goal(tmp_path) -> None:
    """规划工单建不起来（planner 定义不在）时目标保留、planning=failed。"""

    async def scenario() -> None:
        engine, factory = await make_session_factory(tmp_path / "failed.db")
        harness = _Harness(factory, specs={})
        try:
            await harness.start()
            detail = await harness.create_goal()

            assert detail.status == GoalStatus.ACTIVE.value
            assert detail.planning_ticket_id is None
            assert detail.progress["planning"] == "failed"
            assert any(
                "规划没开始" in note
                for note in detail.progress.get("notes") or []
            )
        finally:
            await harness.close()
            engine.sync_engine.dispose()

    asyncio.run(scenario())


def test_update_deadline_rejects_naive_and_null_clears(tmp_path) -> None:
    """截止不带时区 → VALIDATION_ERROR；传 null 去掉截止。"""

    async def scenario() -> None:
        engine, factory = await make_session_factory(tmp_path / "deadline-update.db")
        harness = _Harness(factory)
        try:
            await harness.start()
            detail = await harness.create_goal()
            assert detail.deadline is not None

            with pytest.raises(AppError) as naive:
                await harness.goals.update(
                    detail.id,
                    GoalUpdate(deadline=datetime(2026, 12, 1, 10, 0)),
                )
            assert naive.value.code == ErrorCode.VALIDATION_ERROR

            updated = await harness.goals.update(
                detail.id, GoalUpdate(deadline=None)
            )
            assert updated.deadline is None
        finally:
            await harness.close()
            engine.sync_engine.dispose()

    asyncio.run(scenario())


def test_resume_consumption_reasons_names_consuming_schedule(tmp_path) -> None:
    """恢复目标时，暂停期间停用的理智药定时任务给出带名字的确认理由。"""

    async def scenario() -> None:
        engine, factory = await make_session_factory(tmp_path / "reasons.db")
        harness = _Harness(factory)
        try:
            await harness.start()
            detail = await harness.create_goal()
            schedule = await harness.create_schedule(
                name="每天刷 1-7",
                template=[
                    {
                        "name": "Fight",
                        "stage": "1-7",
                        "medicine": 1,
                        "stone": 0,
                    }
                ],
            )
            await _attach_to_goal(factory, detail.id, schedule_ids=[schedule.id])
            await harness.goals.update(detail.id, GoalUpdate(status="paused"))

            context = ToolContext(
                caller=CallerType.REST,
                session_id=None,
                request_id=None,
                request=SimpleNamespace(app=SimpleNamespace(state=SimpleNamespace())),
                db_session=None,
            )
            reasons = await harness.goals.resume_consumption_reasons(
                detail.id, context=context
            )
            assert reasons
            assert reasons[0].startswith("每天刷 1-7：")
            assert "medicine" in reasons[0]

            # 不是 paused 时返回空列表。
            await harness.goals.update(detail.id, GoalUpdate(status="active"))
            assert (
                await harness.goals.resume_consumption_reasons(
                    detail.id, context=context
                )
                == []
            )
        finally:
            await harness.close()
            engine.sync_engine.dispose()

    asyncio.run(scenario())


def test_invalid_default_cron_falls_back(tmp_path, caplog) -> None:
    """设置里的默认 cron 不合法时回落到 0 22 * * * 并记 warning。"""

    async def scenario() -> None:
        engine, factory = await make_session_factory(tmp_path / "fallback.db")
        settings = Settings(goals=GoalsSettings(default_review_cron="每晚"))
        harness = _Harness(factory, settings=settings)
        try:
            await harness.start()
            detail = await harness.create_goal(review_cron=None)
            assert detail.review_cron == "0 22 * * *"
        finally:
            await harness.close()
            engine.sync_engine.dispose()

    asyncio.run(scenario())
    assert any("回退" in record.message for record in caplog.records)


def test_list_goals_filters_status_and_counts_milestones(tmp_path) -> None:
    """列表按状态过滤，并给出里程碑总数/完成数与下次复盘。"""

    async def scenario() -> None:
        engine, factory = await make_session_factory(tmp_path / "list.db")
        harness = _Harness(factory)
        try:
            await harness.start()
            active = await harness.create_goal(title="进行中")
            paused = await harness.create_goal(title="要暂停")
            await _seed_milestone(
                factory,
                active.id,
                ord=1,
                status=MilestoneStatus.DONE.value,
            )
            await _seed_milestone(factory, active.id, ord=2)
            await harness.goals.update(paused.id, GoalUpdate(status="paused"))

            page = await harness.goals.list_goals(
                statuses=[GoalStatus.ACTIVE], page=1, size=20
            )
            assert page.total == 1
            assert page.items[0].title == "进行中"
            assert page.items[0].milestones_total == 2
            assert page.items[0].milestones_done == 1
            assert page.items[0].next_review_at is None  # 调度器未 start

            with pytest.raises(AppError) as missing:
                await harness.goals.get("00000000-0000-4000-8000-000000000000")
            assert missing.value.code == ErrorCode.GOAL_NOT_FOUND
        finally:
            await harness.close()
            engine.sync_engine.dispose()

    asyncio.run(scenario())


def test_paused_goal_job_is_registered_paused_on_start(tmp_path) -> None:
    """``start_jobs`` 给 paused 目标注册 job 后立即 pause。"""

    async def scenario() -> None:
        engine, factory = await make_session_factory(tmp_path / "start.db")
        harness = _Harness(factory)
        try:
            await harness.start()
            detail = await harness.create_goal()
            await harness.goals.update(detail.id, GoalUpdate(status="paused"))

            # 模拟服务重启：新实例从库里装载 paused 目标。
            await harness.goals.close()
            restarted = _Harness(factory, settings=harness.settings)
            try:
                await restarted.start()
                job = _job(restarted, detail.id)
                assert job is not None
                assert getattr(job, "next_run_time", "missing") is None
            finally:
                await restarted.close()
        finally:
            await harness.close()
            engine.sync_engine.dispose()

    asyncio.run(scenario())
