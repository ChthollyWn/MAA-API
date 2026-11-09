"""M84 T6：简报事实（spec §4.5.10、§6）。

临时库与 ``tests/fakes/v3_db.make_session_factory`` 同口径；固定 naive UTC 时钟，
「昨日」按简报日（北京时间 04:00 分日）取上一窗口，不随机器时区或跑测日期漂移。
消耗护栏已随维护性补丁删除，简报不再有消耗小节。
"""

from __future__ import annotations

import asyncio
from datetime import datetime, timedelta
from pathlib import Path
from types import SimpleNamespace

from maa_api.agent.brief import BriefFacts, collect_facts
from maa_api.db.models import (
    AgentQuestion,
    AgentTrigger,
    Confirmation,
    CostLedgerEntry,
    Goal,
    GoalMilestone,
    MemoryItem,
    Pipeline,
    SanityObservation,
    Schedule,
    StageDrop,
    Ticket,
    TriggerObservation,
    UpgradePlan,
    WatchdogAlert,
)
from tests.fakes.v3_db import make_session_factory, seed_session

#: 北京 2026-11-05 20:00（naive UTC），简报日 2026-11-05。
NOW = datetime(2026, 11, 5, 12, 0)
#: 上一个简报日窗口（北京 11-04 04:00 到 11-05 04:00）。
YESTERDAY = NOW - timedelta(days=1, hours=2)
#: 昨天窗口外的「今天」。
TODAY = NOW - timedelta(hours=1)


def _app(factory, **state) -> SimpleNamespace:
    values = {"session_factory": factory}
    values.update(state)
    return SimpleNamespace(state=SimpleNamespace(**values))


class _BrokenIndex:
    def snapshot(self):
        raise RuntimeError("activity broken")


class _BrokenGacha:
    async def pools(self):
        raise RuntimeError("gacha broken")


def test_sanity_estimate_caps_at_max(tmp_path: Path) -> None:
    async def scenario() -> None:
        engine, factory = await make_session_factory(tmp_path / "facts-sanity.db")
        try:
            async with factory() as session:
                session.add(
                    SanityObservation(
                        current_sanity=130,
                        max_sanity=135,
                        created_at=NOW - timedelta(hours=2),
                    )
                )
                # 7 天以前的采样不参与。
                session.add(
                    SanityObservation(
                        current_sanity=1,
                        max_sanity=135,
                        created_at=NOW - timedelta(days=8),
                    )
                )
                await session.commit()

            facts = await collect_facts(_app(factory), NOW)

            assert isinstance(facts, BriefFacts)
            assert facts.date.isoformat() == "2026-11-05"
            assert facts.weekday == "周四"
            sanity = facts.sanity
            assert sanity is not None
            # 120 分钟 // 6 = 20，130 + 20 被上限 135 截住。
            assert sanity["estimated"] == 135
            assert sanity["max"] == 135
            # 回满时刻 = 采样时刻 + 5 × 6 分钟 = 北京时间 18:30。
            assert sanity["full_at_local"] == "18:30"
            assert sanity["sampled_at_local"] == "11-05 18:00"
        finally:
            engine.sync_engine.dispose()

    asyncio.run(scenario())


def test_missing_sources_leave_sections_empty(tmp_path: Path) -> None:
    async def scenario() -> None:
        engine, factory = await make_session_factory(tmp_path / "facts-broken.db")
        try:
            app = _app(
                factory,
                activity_index=_BrokenIndex(),
                gacha_service=_BrokenGacha(),
            )
            facts = await collect_facts(app, NOW)

            assert facts.activity_lines == []
            assert facts.gacha_lines == []
            # 其他块照常：没有数据时是空集合，不是整个失败。
            assert facts.schedules_today == []
            assert facts.drops_yesterday == []
        finally:
            engine.sync_engine.dispose()

    asyncio.run(scenario())


def test_yesterday_blocks_use_previous_brief_day(tmp_path: Path) -> None:
    async def scenario() -> None:
        engine, factory = await make_session_factory(tmp_path / "facts-yesterday.db")
        try:
            reason = "内核崩溃" * 20
            async with factory() as session:
                session.add_all(
                    [
                        Pipeline(
                            source="manual",
                            priority=0,
                            status="completed",
                            title="昨日完成",
                            task_count=1,
                            created_at=YESTERDAY,
                        ),
                        Pipeline(
                            source="manual",
                            priority=0,
                            status="failed",
                            title="昨日失败",
                            error_message=reason,
                            task_count=1,
                            created_at=YESTERDAY + timedelta(minutes=30),
                        ),
                        Pipeline(
                            source="manual",
                            priority=0,
                            status="completed",
                            title="今日完成",
                            task_count=1,
                            created_at=TODAY,
                        ),
                        StageDrop(
                            callback_id="cb-1",
                            item_name="固源岩",
                            quantity=10,
                            add_quantity=42,
                            created_at=YESTERDAY,
                        ),
                        StageDrop(
                            callback_id="cb-2",
                            item_name="装置",
                            quantity=5,
                            add_quantity=5,
                            created_at=TODAY,
                        ),
                        Ticket(
                            title="昨日工单",
                            kind="general",
                            origin="user",
                            status="done",
                            ended_at=YESTERDAY,
                        ),
                        Ticket(
                            title="今日工单",
                            kind="general",
                            origin="user",
                            status="failed",
                            ended_at=TODAY,
                        ),
                        CostLedgerEntry(
                            agent_name="worker",
                            role="worker",
                            model_profile="default",
                            model="m",
                            cost_micros=1_320_000,
                            currency="CNY",
                            created_at=YESTERDAY,
                        ),
                        CostLedgerEntry(
                            agent_name="worker",
                            role="worker",
                            model_profile="default",
                            model="m",
                            cost_micros=9_000_000,
                            currency="CNY",
                            created_at=TODAY,
                        ),
                    ]
                )
                await session.commit()

            facts = await collect_facts(_app(factory), NOW)

            assert facts.pipelines_yesterday == {
                "completed": 1,
                "failed": 1,
                "cancelled": 0,
                "failed_items": [
                    {"title": "昨日失败", "reason": reason[:40]}
                ],
            }
            assert facts.drops_yesterday == [
                {"item_name": "固源岩", "quantity": 42}
            ]
            assert facts.tickets_yesterday == {
                "done": 1,
                "failed": 0,
                "cancelled": 0,
                "titles": ["昨日工单"],
            }
            assert facts.cost_yesterday == {"CNY": 1_320_000}
        finally:
            engine.sync_engine.dispose()

    asyncio.run(scenario())


def test_pending_counts_from_contract_tables(tmp_path: Path) -> None:
    async def scenario() -> None:
        engine, factory = await make_session_factory(tmp_path / "facts-pending.db")
        try:
            session_id = await seed_session(factory, kind="worker")
            async with factory() as session:
                trigger = AgentTrigger(
                    source="system", kind="system.test", thread_key="system:test"
                )
                session.add(trigger)
                await session.flush()
                session.add_all(
                    [
                        Confirmation(
                            action="click",
                            risk_level="consume",
                            reason="测试",
                            requested_by="agent",
                            expires_at=NOW + timedelta(minutes=10),
                        ),
                        AgentQuestion(
                            session_id=session_id,
                            tool_call_id="call-q",
                            question="选哪个",
                            expires_at=NOW + timedelta(minutes=10),
                        ),
                        Ticket(
                            title="等待批准",
                            kind="general",
                            origin="user",
                            status="waiting",
                            updated_at=datetime(2026, 11, 1),
                        ),
                        Ticket(
                            title="被卡住",
                            kind="general",
                            origin="user",
                            status="blocked",
                            updated_at=datetime(2026, 11, 2),
                        ),
                        Ticket(
                            title="已完成",
                            kind="general",
                            origin="user",
                            status="done",
                            ended_at=YESTERDAY,
                        ),
                        WatchdogAlert(
                            check="ticket_stalled",
                            severity="critical",
                            target_kind="ticket",
                            message="严重告警",
                        ),
                        WatchdogAlert(
                            check="ticket_stalled",
                            severity="critical",
                            target_kind="ticket",
                            message="已解除",
                            resolved_at=NOW,
                        ),
                        WatchdogAlert(
                            check="ticket_stalled",
                            severity="warning",
                            target_kind="ticket",
                            message="警告不算",
                        ),
                        MemoryItem(
                            kind="fact", content="待审", status="proposed"
                        ),
                        MemoryItem(
                            kind="fact", content="生效", status="active"
                        ),
                        TriggerObservation(
                            trigger_id=trigger.id,
                            rule_id="rule-1",
                            decision={"decision": "stop"},
                        ),
                        TriggerObservation(
                            trigger_id=trigger.id,
                            rule_id="rule-2",
                            decision={"decision": "stop"},
                            verdict="accept",
                        ),
                    ]
                )
                await session.commit()

            facts = await collect_facts(_app(factory), NOW)

            assert facts.pending["confirmations"] == 1
            assert facts.pending["questions"] == 1
            assert facts.pending["tickets"] == ["被卡住", "等待批准"]
            assert facts.pending["tickets_total"] == 2
            assert facts.pending["alerts"] == 1
            assert facts.pending["memories"] == 1
            assert facts.pending["observations"] == 1
        finally:
            engine.sync_engine.dispose()

    asyncio.run(scenario())


def test_schedule_plans_and_goals_blocks(tmp_path: Path) -> None:
    async def scenario() -> None:
        engine, factory = await make_session_factory(tmp_path / "facts-more.db")
        try:
            async with factory() as session:
                goal = Goal(
                    title="打通主线",
                    deadline=datetime(2026, 11, 30, 14, 0),
                )
                session.add(goal)
                await session.flush()
                session.add_all(
                    [
                        Schedule(
                            name="日常",
                            cron="0 12 * * *",
                            enabled=True,
                            next_run_at=NOW + timedelta(hours=1),
                            template=[{"name": "StartUp"}, {"name": "Fight"}],
                        ),
                        Schedule(
                            name="停用",
                            cron="0 12 * * *",
                            enabled=False,
                            next_run_at=NOW + timedelta(hours=2),
                            template=[{"name": "Fight"}],
                        ),
                        Schedule(
                            name="后天",
                            cron="0 12 * * *",
                            enabled=True,
                            next_run_at=NOW + timedelta(days=2),
                            template=[{"name": "Fight"}],
                        ),
                        UpgradePlan(
                            name="精二 xx",
                            status="active",
                            progress={
                                "missing": {"材料甲": 2, "材料乙": 3},
                                "days_estimated": 6.0,
                            },
                        ),
                        UpgradePlan(
                            name="已完成",
                            status="completed",
                            progress={"missing": {"材料甲": 9}},
                        ),
                        GoalMilestone(
                            goal_id=goal.id, ord=1, title="第一步", status="done"
                        ),
                        GoalMilestone(
                            goal_id=goal.id, ord=2, title="第二步", status="done"
                        ),
                        GoalMilestone(
                            goal_id=goal.id, ord=3, title="第三步", status="pending"
                        ),
                    ]
                )
                await session.commit()

            facts = await collect_facts(_app(factory), NOW)

            assert facts.schedules_today == [
                {"name": "日常", "at_local": "21:00", "fight": True}
            ]
            assert facts.plans == [
                {
                    "name": "精二 xx",
                    "missing_kinds": 2,
                    "missing_total": 5,
                    "days_estimated": 6.0,
                }
            ]
            assert facts.goals == [
                {
                    "title": "打通主线",
                    "deadline_local": "11 月 30 日",
                    "done": 2,
                    "total": 3,
                }
            ]
        finally:
            engine.sync_engine.dispose()

    asyncio.run(scenario())
