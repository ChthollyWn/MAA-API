"""M84 T3：消耗护栏（spec §4.2、§4.5.1–§4.5.6、§6）。

异步用例一律「同步测试函数 + ``asyncio.run(scenario())``」（仓库没有
pytest-asyncio）；临时库照 ``tests/fakes/v3_db.make_session_factory`` 建真实表
结构，``foreign_keys=ON`` 与生产一致。所有时间用固定的 naive UTC 时钟，不随
机器时区或跑测日期漂移。
"""

from __future__ import annotations

import asyncio
from datetime import date, datetime, timedelta
from pathlib import Path

import pytest
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from maa_api.agent.spend import (
    BLOCK_HISTORY_LIMIT,
    CAPPED_SOURCES,
    SPEND_DAY_RESET_HOUR,
    SPEND_TIMEZONE,
    SPEND_TOOLS,
    SpendGuard,
    merged_task_params,
    planned_spend,
    spend_day_of,
    spend_day_window,
)
from maa_api.db.models import AgentAudit, AgentSession, Pipeline, SpendLedger, Task, Ticket
from maa_api.domain.enums import (
    AuditStatus,
    PipelineSource,
    PipelineStatus,
    Priority,
    RiskLevel,
    SpendKind,
    TaskStatus,
)
from maa_api.domain.errors import AppError, ErrorCode
from maa_api.settings import Settings, SpendSettings
from tests.fakes.v3_db import make_session_factory, seed_run

#: 北京 2026-11-05 20:00（naive UTC），消耗日 2026-11-05。
NOW = datetime(2026, 11, 5, 12, 0)
TODAY = date(2026, 11, 5)


class _Limits:
    """可变的设置提供者：测试中途改上限，下一次判定立即生效。"""

    def __init__(self, *, medicine: int = -1, stone: int = -1, expedite: int = -1) -> None:
        self.medicine = medicine
        self.stone = stone
        self.expedite = expedite

    def __call__(self) -> Settings:
        return Settings(
            spend=SpendSettings(
                daily_medicine_limit=self.medicine,
                daily_stone_limit=self.stone,
                daily_expedite_limit=self.expedite,
            )
        )


async def _open_env(
    tmp_path: Path,
    *,
    name: str = "spend.db",
    medicine: int = -1,
    stone: int = -1,
    expedite: int = -1,
):
    """临时库 + 固定时钟的守卫；返回 ``(engine, factory, guard, limits)``。"""
    engine, factory = await make_session_factory(tmp_path / name)
    limits = _Limits(medicine=medicine, stone=stone, expedite=expedite)
    guard = SpendGuard(
        factory, settings_provider=limits, clock=lambda: NOW
    )
    return engine, factory, guard, limits


async def _seed_pipeline(
    factory: async_sessionmaker[AsyncSession],
    tasks: list[dict],
    *,
    source: PipelineSource = PipelineSource.AGENT,
    status: PipelineStatus = PipelineStatus.RUNNING,
) -> tuple[Pipeline, list[Task]]:
    """写一条流水线与若干任务；``tasks[i] = {type, params, started}``。"""
    pipeline = Pipeline(
        source=source.value,
        priority=int(Priority.MANUAL),
        status=status.value,
        task_count=len(tasks),
    )
    rows = [
        Task(
            pipeline_id=pipeline.id,
            order_index=index,
            type_name=item["type"],
            task_name=item["type"],
            params=item.get("params", {}),
            status=TaskStatus.RUNNING.value,
            started_at=(
                NOW - timedelta(minutes=10) if item.get("started", True) else None
            ),
        )
        for index, item in enumerate(tasks)
    ]
    async with factory() as session:
        session.add(pipeline)
        session.add_all(rows)
        await session.commit()
    return pipeline, rows


async def _add_ledger(
    factory: async_sessionmaker[AsyncSession],
    *,
    kind: str,
    planned: int,
    actual: int | None = None,
    source: PipelineSource = PipelineSource.MANUAL,
    pipeline_id: str | None = None,
    task_id: str | None = None,
    created_at: datetime = NOW - timedelta(minutes=5),
) -> None:
    async with factory() as session:
        session.add(
            SpendLedger(
                kind=kind,
                planned=planned,
                actual=actual,
                source=source.value,
                pipeline_id=pipeline_id,
                task_id=task_id,
                created_at=created_at,
            )
        )
        await session.commit()


async def _ledger_rows(
    factory: async_sessionmaker[AsyncSession],
) -> list[SpendLedger]:
    async with factory() as session:
        return list(
            (
                await session.execute(
                    select(SpendLedger).order_by(SpendLedger.id.asc())
                )
            )
            .scalars()
            .all()
        )


async def _count(
    factory: async_sessionmaker[AsyncSession], model: type
) -> int:
    async with factory() as session:
        return int(
            (
                await session.execute(select(func.count()).select_from(model))
            ).scalar_one()
        )


# ---------------------------------------------------------------------------
# 计划量
# ---------------------------------------------------------------------------
def test_planned_spend_fight_counts_medicine_and_stone() -> None:
    assert planned_spend("Fight", {"medicine": 2, "stone": 1}) == {
        SpendKind.MEDICINE: 2,
        SpendKind.STONE: 1,
    }
    # 非正 int 与布尔都按 0；只剩大于 0 的项。
    assert planned_spend("Fight", {"medicine": True, "stone": -1}) == {}
    assert planned_spend("Fight", {"medicine": 0, "stone": 0}) == {}
    assert planned_spend("Fight", {}) == {}
    # 只认 Fight / Recruit，其他类型没有消耗字段。
    assert planned_spend("Copilot", {"medicine": 9, "stone": 9}) == {}


def test_planned_spend_ignores_expiring_medicine() -> None:
    # 养成计划每日模板：expiring_medicine=999、medicine=0（裁决 R2）。
    assert planned_spend("Fight", {"expiring_medicine": 999}) == {}
    assert planned_spend("Fight", {"expiring_medicine": 999, "medicine": 1}) == {
        SpendKind.MEDICINE: 1
    }


def test_planned_spend_recruit_uses_times_when_expedite() -> None:
    assert planned_spend("Recruit", {"expedite": True, "times": 4}) == {
        SpendKind.EXPEDITE: 4
    }
    assert planned_spend(
        "Recruit", {"expedite": False, "times": 4, "expedite_times": 9}
    ) == {}
    # expedite_times 不再被内核读取，计划量只看 times。
    assert planned_spend(
        "Recruit", {"expedite": True, "times": 0, "expedite_times": 9}
    ) == {}
    assert planned_spend("Recruit", {"expedite": True, "times": 0}) == {}


def test_merged_task_params_validates_and_falls_back() -> None:
    merged = merged_task_params("Fight", {"medicine": 2}, {"medicine": 5})
    assert merged["medicine"] == 5
    assert merged["stone"] == 0
    # 别名补丁也接受（populate_by_name / validation_alias）。
    assert merged_task_params("Fight", {}, {"dr_grandet": True})["DrGrandet"] is True
    # 校验失败退回字典合并。
    assert merged_task_params("Fight", {"medicine": 2}, {"medicine": -1}) == {
        "medicine": -1
    }
    assert merged_task_params("NoSuchTask", {"x": 1}, {"y": 2}) == {"x": 1, "y": 2}


# ---------------------------------------------------------------------------
# 日界
# ---------------------------------------------------------------------------
def test_day_window_resets_at_four_beijing() -> None:
    assert SPEND_TIMEZONE == "Asia/Shanghai"
    assert SPEND_DAY_RESET_HOUR == 4

    before_start, before_end, before_day = spend_day_window(
        datetime(2026, 1, 1, 19, 59)
    )
    after_start, after_end, after_day = spend_day_window(
        datetime(2026, 1, 1, 20, 0)
    )
    # 19:59Z = 北京 03:59 → 前一天；20:00Z = 北京 04:00 → 当天。
    assert before_day == date(2026, 1, 1)
    assert after_day == date(2026, 1, 2)
    assert before_day != after_day
    assert before_start == datetime(2025, 12, 31, 20, 0)
    assert before_end == datetime(2026, 1, 1, 20, 0)
    assert after_start == datetime(2026, 1, 1, 20, 0)
    assert after_end == datetime(2026, 1, 2, 20, 0)
    assert spend_day_of(datetime(2026, 1, 1, 19, 59)) == before_day
    assert spend_day_of(datetime(2026, 1, 1, 20, 0)) == after_day
    # 窗口宽度恒为一天。
    assert after_end - after_start == timedelta(days=1)


# ---------------------------------------------------------------------------
# 用量
# ---------------------------------------------------------------------------
def test_usage_skips_tasks_that_never_started(tmp_path: Path) -> None:
    async def scenario() -> None:
        engine, factory, guard, _ = await _open_env(
            tmp_path, name="usage.db"
        )
        try:
            # 终态 + 从未开始 → 0。
            failed, failed_tasks = await _seed_pipeline(
                factory,
                [{"type": "Fight", "params": {"medicine": 3}, "started": False}],
                status=PipelineStatus.FAILED,
            )
            await _add_ledger(
                factory,
                kind="medicine",
                planned=3,
                pipeline_id=failed.id,
                task_id=failed_tasks[0].id,
            )
            # 终态但开始过 → planned。
            failed_started, started_tasks = await _seed_pipeline(
                factory,
                [{"type": "Fight", "params": {"medicine": 2}, "started": True}],
                status=PipelineStatus.FAILED,
            )
            await _add_ledger(
                factory,
                kind="medicine",
                planned=2,
                pipeline_id=failed_started.id,
                task_id=started_tasks[0].id,
            )
            # 运行中 + 从未开始 → planned（只有终态才归零）。
            running, running_tasks = await _seed_pipeline(
                factory,
                [{"type": "Fight", "params": {"medicine": 4}, "started": False}],
                status=PipelineStatus.RUNNING,
            )
            await _add_ledger(
                factory,
                kind="medicine",
                planned=4,
                pipeline_id=running.id,
                task_id=running_tasks[0].id,
            )
            # actual 优先于 planned。
            await _add_ledger(
                factory, kind="medicine", planned=99, actual=1, source=PipelineSource.MANUAL
            )
            # 窗口外的不计。
            await _add_ledger(
                factory,
                kind="medicine",
                planned=50,
                created_at=NOW - timedelta(days=2),
            )

            async with factory() as session:
                used = await guard.usage(
                    session,
                    start=spend_day_window(NOW)[0],
                    end=spend_day_window(NOW)[1],
                )
            assert used == {
                SpendKind.MEDICINE: 7,  # 2 + 4 + 1
                SpendKind.STONE: 0,
                SpendKind.EXPEDITE: 0,
            }
        finally:
            engine.sync_engine.dispose()

    asyncio.run(scenario())


# ---------------------------------------------------------------------------
# 判定与预留
# ---------------------------------------------------------------------------
def test_unlimited_and_zero_limits(tmp_path: Path) -> None:
    async def scenario() -> None:
        engine, factory, guard, limits = await _open_env(
            tmp_path, name="limits.db", medicine=-1
        )
        try:
            _, tasks = await _seed_pipeline(
                factory, [{"type": "Fight", "params": {"medicine": 999}}]
            )
            async with factory() as session:
                async with guard.reserve_submission(
                    session,
                    source=PipelineSource.AGENT,
                    tasks=tasks,
                    agent_session_id=None,
                ):
                    await session.commit()
            rows = await _ledger_rows(factory)
            assert [(row.kind, row.planned) for row in rows] == [
                (SpendKind.MEDICINE.value, 999)
            ]

            # 上限 0：任何正数都拒绝，账本不新增。
            limits.medicine = 0
            _, tasks = await _seed_pipeline(
                factory, [{"type": "Fight", "params": {"medicine": 1}}]
            )
            with pytest.raises(AppError) as caught:
                async with factory() as session:
                    async with guard.reserve_submission(
                        session,
                        source=PipelineSource.AGENT,
                        tasks=tasks,
                        agent_session_id=None,
                    ):
                        await session.commit()
            assert caught.value.code is ErrorCode.SPEND_CAP_EXCEEDED
            assert caught.value.details["limit"] == 0
            assert caught.value.message == "今日禁止使用理智药（上限为 0）。可以在设置里调整"
            assert len(await _ledger_rows(factory)) == 1
        finally:
            engine.sync_engine.dispose()

    asyncio.run(scenario())


def test_manual_source_counted_not_capped(tmp_path: Path) -> None:
    async def scenario() -> None:
        engine, factory, guard, _ = await _open_env(
            tmp_path, name="manual.db", medicine=5
        )
        try:
            # manual 即使超额也照常写行（只计入、不拦）。
            _, manual_tasks = await _seed_pipeline(
                factory,
                [{"type": "Fight", "params": {"medicine": 6}}],
                source=PipelineSource.MANUAL,
            )
            async with factory() as session:
                async with guard.reserve_submission(
                    session,
                    source=PipelineSource.MANUAL,
                    tasks=manual_tasks,
                    agent_session_id=None,
                ):
                    await session.commit()
            rows = await _ledger_rows(factory)
            assert [(row.source, row.planned) for row in rows] == [
                (PipelineSource.MANUAL.value, 6)
            ]

            # 随后 agent 的提交看见 manual 的 6，超额被拒。
            _, agent_tasks = await _seed_pipeline(
                factory, [{"type": "Fight", "params": {"medicine": 1}}]
            )
            with pytest.raises(AppError) as caught:
                async with factory() as session:
                    async with guard.reserve_submission(
                        session,
                        source=PipelineSource.AGENT,
                        tasks=agent_tasks,
                        agent_session_id=None,
                    ):
                        await session.commit()
            assert caught.value.code is ErrorCode.SPEND_CAP_EXCEEDED
            assert caught.value.details["used"] == 6
            assert caught.value.details["source"] == "agent"
            assert caught.value.details["violations"] == [
                {"kind": "medicine", "limit": 5, "used": 6, "requested": 1}
            ]
            # 整条拒绝：只有 manual 那一行。
            assert len(await _ledger_rows(factory)) == 1
        finally:
            engine.sync_engine.dispose()

    asyncio.run(scenario())


def test_reserve_submission_skips_zero_spend(tmp_path: Path) -> None:
    async def scenario() -> None:
        engine, factory, guard, _ = await _open_env(
            tmp_path, name="zero.db", medicine=0
        )
        try:
            # 只有 Copilot 任务：计划量 0，不查上限也不写账本。
            _, tasks = await _seed_pipeline(
                factory,
                [{"type": "Copilot", "params": {"asset_id": "copilot-1"}}],
            )
            async with factory() as session:
                async with guard.reserve_submission(
                    session,
                    source=PipelineSource.AGENT,
                    tasks=tasks,
                    agent_session_id=None,
                ):
                    await session.commit()
            assert await _ledger_rows(factory) == []
            assert guard.blocked_today() == []
        finally:
            engine.sync_engine.dispose()

    asyncio.run(scenario())


def test_reserve_submission_rolls_back_on_violation(tmp_path: Path) -> None:
    async def scenario() -> None:
        engine, factory, guard, _ = await _open_env(
            tmp_path, name="rollback.db", medicine=2
        )
        try:
            async with factory() as session:
                pipeline = Pipeline(
                    source=PipelineSource.AGENT.value,
                    priority=int(Priority.AGENT),
                    status=PipelineStatus.PENDING.value,
                    task_count=1,
                )
                task = Task(
                    pipeline_id=pipeline.id,
                    order_index=0,
                    type_name="Fight",
                    task_name="Fight",
                    params={"medicine": 3},
                )
                session.add(pipeline)
                session.add(task)
                with pytest.raises(AppError) as caught:
                    async with guard.reserve_submission(
                        session,
                        source=PipelineSource.AGENT,
                        tasks=[task],
                        agent_session_id=None,
                    ):
                        await session.commit()
                assert caught.value.code is ErrorCode.SPEND_CAP_EXCEEDED
                # async with 结束时回滚：流水线、任务与账本都不落库。
            assert await _count(factory, Pipeline) == 0
            assert await _count(factory, Task) == 0
            assert await _count(factory, SpendLedger) == 0
        finally:
            engine.sync_engine.dispose()

    asyncio.run(scenario())


def test_reserve_submission_serializes_concurrent_calls(tmp_path: Path) -> None:
    async def scenario() -> None:
        engine, factory, guard, _ = await _open_env(
            tmp_path, name="concurrent.db", medicine=2
        )
        try:
            _, first_tasks = await _seed_pipeline(
                factory, [{"type": "Fight", "params": {"medicine": 2}}]
            )
            _, second_tasks = await _seed_pipeline(
                factory, [{"type": "Fight", "params": {"medicine": 2}}]
            )

            async def submit(tasks: list[Task]) -> str:
                async with factory() as session:
                    try:
                        async with guard.reserve_submission(
                            session,
                            source=PipelineSource.AGENT,
                            tasks=tasks,
                            agent_session_id=None,
                        ):
                            await session.commit()
                    except AppError as exc:
                        return exc.code.value
                return "ok"

            results = await asyncio.gather(
                submit(first_tasks), submit(second_tasks)
            )
            # 守卫锁串行化「查用量 + 写行」：只可能有一条通过。
            assert results.count("ok") == 1
            assert results.count(ErrorCode.SPEND_CAP_EXCEEDED.value) == 1
            rows = await _ledger_rows(factory)
            assert len(rows) == 1
            assert sum(row.planned for row in rows) == 2
        finally:
            engine.sync_engine.dispose()

    asyncio.run(scenario())


def test_reserve_patch_counts_only_increase(tmp_path: Path) -> None:
    async def scenario() -> None:
        engine, factory, guard, limits = await _open_env(
            tmp_path, name="patch.db", medicine=8
        )
        try:
            _, first = await _seed_pipeline(
                factory, [{"type": "Fight", "params": {"medicine": 2}}]
            )
            task = first[0]
            await _add_ledger(
                factory,
                kind="medicine",
                planned=2,
                pipeline_id=task.pipeline_id,
                task_id=task.id,
                source=PipelineSource.AGENT,
            )
            _, other = await _seed_pipeline(
                factory, [{"type": "Fight", "params": {"medicine": 3}}]
            )
            await _add_ledger(
                factory,
                kind="medicine",
                planned=3,
                pipeline_id=other[0].pipeline_id,
                task_id=other[0].id,
                source=PipelineSource.AGENT,
            )

            # 已计 3 + 自己的 2 = 5；2→5 的差额是 3，5 + 3 = 8 恰好到上限。
            async with factory() as session:
                async with guard.reserve_patch(
                    session,
                    task=task,
                    merged_params={"medicine": 5},
                    source=PipelineSource.AGENT,
                ):
                    await session.commit()
            rows = await _ledger_rows(factory)
            own = next(row for row in rows if row.task_id == task.id)
            assert own.planned == 5

            # 调低不减账：5 → 2 不查上限、不改行。
            async with factory() as session:
                async with guard.reserve_patch(
                    session,
                    task=task,
                    merged_params={"medicine": 2},
                    source=PipelineSource.AGENT,
                ):
                    await session.commit()
            rows = await _ledger_rows(factory)
            own = next(row for row in rows if row.task_id == task.id)
            assert own.planned == 5

            # 上限降到 7：5 → 6 的差额 1，已计 3 + 5 = 8，8 + 1 > 7 → 拒绝。
            limits.medicine = 7
            with pytest.raises(AppError) as caught:
                async with factory() as session:
                    async with guard.reserve_patch(
                        session,
                        task=task,
                        merged_params={"medicine": 6},
                        source=PipelineSource.AGENT,
                    ):
                        await session.commit()
            assert caught.value.code is ErrorCode.SPEND_CAP_EXCEEDED
            assert caught.value.details["requested"] == 1
            rows = await _ledger_rows(factory)
            own = next(row for row in rows if row.task_id == task.id)
            assert own.planned == 5

            # 没有旧行时补一行，pipeline_id 取 task.pipeline_id。
            _, untracked = await _seed_pipeline(
                factory, [{"type": "Fight", "params": {"medicine": 0}}]
            )
            new_task = untracked[0]
            limits.medicine = 999
            async with factory() as session:
                async with guard.reserve_patch(
                    session,
                    task=new_task,
                    merged_params={"medicine": 2},
                    source=PipelineSource.AGENT,
                ):
                    await session.commit()
            rows = await _ledger_rows(factory)
            added = next(row for row in rows if row.task_id == new_task.id)
            assert (added.planned, added.pipeline_id, added.source) == (
                2,
                new_task.pipeline_id,
                PipelineSource.AGENT.value,
            )
            assert added.ticket_id is None
        finally:
            engine.sync_engine.dispose()

    asyncio.run(scenario())


# ---------------------------------------------------------------------------
# 预检
# ---------------------------------------------------------------------------
async def _audit_rows(
    factory: async_sessionmaker[AsyncSession],
) -> list[AgentAudit]:
    async with factory() as session:
        return list(
            (
                await session.execute(
                    select(AgentAudit).order_by(AgentAudit.id.asc())
                )
            )
            .scalars()
            .all()
        )


def test_precheck_submit_blocks_and_records(tmp_path: Path) -> None:
    async def scenario() -> None:
        engine, factory, guard, limits = await _open_env(
            tmp_path, name="precheck.db", medicine=5
        )
        try:
            # 今天已经由 manual 用掉 4。
            await _add_ledger(
                factory, kind="medicine", planned=4, actual=4
            )
            with pytest.raises(AppError) as caught:
                await guard.precheck_tool_call(
                    "submit_pipeline",
                    {"tasks": [{"name": "Fight", "medicine": 2}]},
                    agent_session_id=None,
                )
            error = caught.value
            assert error.code is ErrorCode.SPEND_CAP_EXCEEDED
            assert error.message == (
                "超过今日消耗上限：理智药今天已计 4，上限 5，本次需要 2。"
                "可以在设置里调高上限，或改为手动提交"
            )
            assert error.details["kind"] == "medicine"
            assert error.details["limit"] == 5
            assert error.details["used"] == 4
            assert error.details["requested"] == 2
            assert error.details["source"] == "agent"
            assert error.details["day_start"] == "2026-11-04T20:00:00Z"
            blocked = guard.blocked_today()
            assert len(blocked) == 1
            assert blocked[0]["ref"] == "tool:submit_pipeline"
            assert blocked[0]["source"] == "agent"

            audits = await _audit_rows(factory)
            assert len(audits) == 1
            audit = audits[0]
            assert audit.status == AuditStatus.REJECTED
            assert audit.error_code == ErrorCode.SPEND_CAP_EXCEEDED.value
            assert audit.risk_level == RiskLevel.CONSUME
            assert audit.caller == "rest"
            assert audit.tool_name == "submit_pipeline"
            assert audit.ticket_id is None
            assert audit.arguments["tasks"] == [{"name": "Fight", "medicine": 2}]

            # 有工单的会话：ref 与审计的 ticket 都指向工单。
            ticket = Ticket(
                title="消耗测试",
                kind="general",
                status="open",
                origin="user",
            )
            async with factory() as session:
                session.add(ticket)
                await session.commit()
            session_id = await _seed_session_with_ticket(factory, ticket.id)
            run_id = await seed_run(
                factory, session_id=session_id, ticket_id=ticket.id
            )
            with pytest.raises(AppError):
                await guard.precheck_tool_call(
                    "submit_pipeline",
                    {"tasks": [{"name": "Fight", "medicine": 2}]},
                    agent_session_id=session_id,
                )
            blocked = guard.blocked_today()
            assert blocked[0]["ref"] == f"ticket:{ticket.id}"
            audits = await _audit_rows(factory)
            assert audits[-1].ticket_id == ticket.id
            assert audits[-1].run_id == run_id
            assert audits[-1].agent_name == "test"
            assert audits[-1].caller == "internal"

            # 多 violation：message 只写第一种，details.violations 全列。
            limits.medicine = 0
            limits.stone = 0
            with pytest.raises(AppError) as caught:
                await guard.precheck_tool_call(
                    "submit_pipeline",
                    {"tasks": [{"name": "Fight", "medicine": 1, "stone": 1}]},
                    agent_session_id=None,
                )
            assert caught.value.message == (
                "今日禁止使用理智药（上限为 0）。可以在设置里调整"
            )
            violations = caught.value.details["violations"]
            assert [item["kind"] for item in violations] == ["medicine", "stone"]
        finally:
            engine.sync_engine.dispose()

    asyncio.run(scenario())


async def _seed_session_with_ticket(
    factory: async_sessionmaker[AsyncSession], ticket_id: str
) -> str:
    session = AgentSession(model="test-model", kind="worker", ticket_id=ticket_id)
    async with factory() as session_db:
        session_db.add(session)
        await session_db.commit()
    return str(session.id)


def test_precheck_tolerates_invalid_arguments(tmp_path: Path) -> None:
    async def scenario() -> None:
        engine, factory, guard, _ = await _open_env(
            tmp_path, name="precheck-skip.db", medicine=0
        )
        try:
            # 参数不合法、未知工具、任务不存在、补丁类型不对：都直接返回。
            await guard.precheck_tool_call(
                "submit_pipeline", {"tasks": "nope"}, agent_session_id=None
            )
            await guard.precheck_tool_call(
                "submit_pipeline",
                {"tasks": [{"name": "NotATask"}]},
                agent_session_id=None,
            )
            await guard.precheck_tool_call(
                "set_task_params",
                {"task_id": "missing-task", "params": {"medicine": 2}},
                agent_session_id=None,
            )
            await guard.precheck_tool_call(
                "set_task_params",
                {"task_id": 5, "params": {"medicine": 2}},
                agent_session_id=None,
            )
            await guard.precheck_tool_call(
                "some_other_tool", {"tasks": []}, agent_session_id=None
            )
            assert guard.blocked_today() == []
            assert await _audit_rows(factory) == []
        finally:
            engine.sync_engine.dispose()

    asyncio.run(scenario())


def test_precheck_set_task_params_uses_increase(tmp_path: Path) -> None:
    async def scenario() -> None:
        engine, factory, guard, limits = await _open_env(
            tmp_path, name="precheck-patch.db", medicine=5
        )
        try:
            _, tasks = await _seed_pipeline(
                factory, [{"type": "Fight", "params": {"medicine": 2}}]
            )
            task = tasks[0]
            await _add_ledger(
                factory,
                kind="medicine",
                planned=2,
                pipeline_id=task.pipeline_id,
                task_id=task.id,
                source=PipelineSource.AGENT,
            )
            # 已计 2，改到 5 的差额 3，2 + 3 = 5 恰好到上限。
            await guard.precheck_tool_call(
                "set_task_params",
                {"task_id": task.id, "params": {"medicine": 5}},
                agent_session_id=None,
            )
            assert guard.blocked_today() == []

            # 上限降到 4：差额 3 仍使总量到 5，被拦。
            limits.medicine = 4
            with pytest.raises(AppError) as caught:
                await guard.precheck_tool_call(
                    "set_task_params",
                    {"task_id": task.id, "params": {"medicine": 5}},
                    agent_session_id=None,
                )
            assert caught.value.details["requested"] == 3
            assert caught.value.details["used"] == 2
            # 调低参数没有差额，直接放行。
            await guard.precheck_tool_call(
                "set_task_params",
                {"task_id": task.id, "params": {"medicine": 1}},
                agent_session_id=None,
            )
        finally:
            engine.sync_engine.dispose()

    asyncio.run(scenario())


# ---------------------------------------------------------------------------
# 汇总
# ---------------------------------------------------------------------------
def test_summary_shapes_days_and_today(tmp_path: Path) -> None:
    async def scenario() -> None:
        engine, factory, guard, _ = await _open_env(
            tmp_path, name="summary.db", medicine=5, stone=-1, expedite=0
        )
        try:
            await _add_ledger(
                factory, kind="medicine", planned=3, actual=3, created_at=NOW - timedelta(hours=1)
            )
            await _add_ledger(
                factory,
                kind="expedite",
                planned=2,
                actual=2,
                created_at=NOW - timedelta(days=1),
            )
            # since 之前的那天不计入 days。
            await _add_ledger(
                factory,
                kind="medicine",
                planned=99,
                actual=99,
                created_at=NOW - timedelta(days=3),
            )

            summary = await guard.summary(since=TODAY - timedelta(days=2))
            assert summary["date"] == "2026-11-05"
            assert summary["day_start"] == "2026-11-04T20:00:00Z"
            assert summary["day_end"] == "2026-11-05T20:00:00Z"
            assert summary["capped_sources"] == ["agent", "scheduled"]
            assert [item["kind"] for item in summary["today"]] == [
                "medicine",
                "stone",
                "expedite",
            ]
            assert summary["today"][0] == {
                "kind": "medicine",
                "label": "理智药",
                "used": 3,
                "limit": 5,
                "remaining": 2,
            }
            assert summary["today"][1] == {
                "kind": "stone",
                "label": "源石",
                "used": 0,
                "limit": -1,
                "remaining": None,
            }
            assert summary["days"] == [
                {
                    "date": "2026-11-03",
                    "medicine": 0,
                    "stone": 0,
                    "expedite": 0,
                },
                {
                    "date": "2026-11-04",
                    "medicine": 0,
                    "stone": 0,
                    "expedite": 2,
                },
                {
                    "date": "2026-11-05",
                    "medicine": 3,
                    "stone": 0,
                    "expedite": 0,
                },
            ]
            assert summary["blocked_today"] == []

            # 缺省 since：连续 7 天到今天。
            default = await guard.summary()
            assert len(default["days"]) == 7
            assert default["days"][0]["date"] == "2026-10-30"
            assert default["days"][-1]["date"] == "2026-11-05"
        finally:
            engine.sync_engine.dispose()

    asyncio.run(scenario())


def test_constants_match_spec() -> None:
    assert CAPPED_SOURCES == frozenset(
        {PipelineSource.AGENT, PipelineSource.SCHEDULED}
    )
    assert SPEND_TOOLS == frozenset({"submit_pipeline", "set_task_params"})
    assert BLOCK_HISTORY_LIMIT == 50
