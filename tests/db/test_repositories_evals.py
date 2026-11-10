"""``EvalRunRepository`` / ``EvalCaseRepository`` 的行为（M99 spec §4.1、§4.2、§6）。

用例形态：同步测试函数 + ``asyncio.run(scenario())``（本仓无 pytest-asyncio）；
临时库与会话来自 ``tests/db/conftest.py`` 的 ``db_session``。

覆盖的硬约束：

- ``upsert_with_cases`` 按主键整行覆盖：同 id 再发布不新增 ``eval_run`` 行，
  旧 cases 先删后插，不撞 ``uq_eval_case_result_run_scenario_attempt_variant``；
- ``page_runs`` 按 ``finished_at DESC, id DESC`` 排序，``suite`` / ``mode``
  过滤与分页总数都生效；
- ``get_run`` 命中返回 ORM 实例，未知 id 返回 ``None``；
- ``list_by_run`` 只取该次评测的 cases，按 ``id`` 升序。
"""

import asyncio
from datetime import datetime, timedelta

from sqlalchemy import func, select

from maa_api.db.models import EvalCaseResult, EvalRun
from maa_api.db.repositories.evals import EvalCaseRepository, EvalRunRepository

RUN_ID = "20261110-081530-capability-live-3f2a"


def _at(minutes: int) -> datetime:
    """固定的 naive UTC 时刻，避免用例依赖当前时间。"""
    return datetime(2026, 1, 1) + timedelta(minutes=minutes)


def _run(run_id: str, **overrides) -> EvalRun:
    fields = {
        "id": run_id,
        "suite": "capability",
        "mode": "live",
        "started_at": _at(0),
        "finished_at": _at(5),
    }
    fields.update(overrides)
    return EvalRun(**fields)


def _case(run_id: str, scenario: str, **overrides) -> EvalCaseResult:
    fields = {
        "eval_run_id": run_id,
        "scenario": scenario,
        "title": scenario,
        "suite": "capability",
        "agent": "legacy_chat",
        "metrics": ["accuracy"],
        "tags": ["groundedness"],
        "status": "passed",
    }
    fields.update(overrides)
    return EvalCaseResult(**fields)


def test_upsert_with_cases_replaces_cases(db_session) -> None:
    async def scenario() -> None:
        runs = EvalRunRepository(db_session)
        cases = EvalCaseRepository(db_session)

        await runs.upsert_with_cases(
            _run(RUN_ID, total=2, passed=1, failed=1, duration_ms=500),
            [_case(RUN_ID, "scenario-a"), _case(RUN_ID, "scenario-b")],
        )
        stored = await cases.list_by_run(RUN_ID)
        assert [case.scenario for case in stored] == ["scenario-a", "scenario-b"]
        assert all(case.id is not None for case in stored)
        first_case_id = stored[0].id

        # 同 id 再发布：汇总整行覆盖，旧 cases 先删后插（复用同一唯一键也不炸）。
        await runs.upsert_with_cases(
            _run(
                RUN_ID,
                total=1,
                passed=1,
                finished_at=_at(30),
                duration_ms=1234,
            ),
            [_case(RUN_ID, "scenario-a", status="failed")],
        )
        reloaded = await runs.get_run(RUN_ID)
        assert reloaded is not None
        assert (reloaded.total, reloaded.passed, reloaded.duration_ms) == (1, 1, 1234)
        assert reloaded.finished_at == _at(30)

        replaced = await cases.list_by_run(RUN_ID)
        assert [case.scenario for case in replaced] == ["scenario-a"]
        assert replaced[0].status == "failed"
        # 旧行是真删掉的：新行重新拿自增 id，而不是原地更新。
        assert replaced[0].id != first_case_id

        # 覆盖不是追加：两边都只有一行。
        run_count = (
            await db_session.execute(select(func.count()).select_from(EvalRun))
        ).scalar_one()
        case_count = (
            await db_session.execute(select(func.count()).select_from(EvalCaseResult))
        ).scalar_one()
        assert (run_count, case_count) == (1, 1)

    asyncio.run(scenario())


def test_page_runs_orders_and_filters(db_session) -> None:
    async def scenario() -> None:
        runs = EvalRunRepository(db_session)
        db_session.add_all(
            [
                _run("run-a", suite="capability", mode="record", finished_at=_at(10)),
                _run("run-b", suite="regression", mode="replay", finished_at=_at(20)),
                _run("run-c", suite="capability", mode="live", finished_at=_at(30)),
                _run("run-d", suite="capability", mode="live", finished_at=_at(20)),
            ]
        )
        await db_session.flush()

        first = await runs.page_runs(page=1, size=2, suite=None, mode=None)
        assert (first.total, first.page, first.size) == (4, 1, 2)
        # finished_at DESC；同刻按 id DESC。
        assert [run.id for run in first.items] == ["run-c", "run-d"]
        second = await runs.page_runs(page=2, size=2, suite=None, mode=None)
        assert [run.id for run in second.items] == ["run-b", "run-a"]

        capability = await runs.page_runs(page=1, size=10, suite="capability", mode=None)
        assert capability.total == 3
        assert [run.id for run in capability.items] == ["run-c", "run-d", "run-a"]

        live = await runs.page_runs(page=1, size=10, suite="capability", mode="live")
        assert live.total == 2
        assert [run.id for run in live.items] == ["run-c", "run-d"]

        replay = await runs.page_runs(page=1, size=10, suite=None, mode="replay")
        assert [run.id for run in replay.items] == ["run-b"]

        missing = await runs.page_runs(page=1, size=10, suite="capability", mode="replay")
        assert (missing.total, missing.items) == (0, [])

    asyncio.run(scenario())


def test_get_run(db_session) -> None:
    async def scenario() -> None:
        runs = EvalRunRepository(db_session)
        await runs.upsert_with_cases(
            _run(
                RUN_ID,
                git={"commit": "8aae539", "dirty": False},
                model={"name": "gpt-test"},
                filters={"suite": "capability"},
            ),
            [],
        )

        got = await runs.get_run(RUN_ID)
        assert got is not None
        assert got.id == RUN_ID
        assert got.suite == "capability"
        # 三个可空 JSON 列原样读回；没有 cases 的评测也正常。
        assert got.git == {"commit": "8aae539", "dirty": False}
        assert got.model == {"name": "gpt-test"}
        assert got.filters == {"suite": "capability"}
        assert await runs.get_run("20261110-000000-capability-live-0000") is None

    asyncio.run(scenario())
