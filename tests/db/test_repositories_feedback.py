"""``RunFeedbackRepository`` 的行为（M99 spec §4.1、§4.2、§4.5.4、§6）。

用例形态：同步测试函数 + ``asyncio.run(scenario())``（本仓无 pytest-asyncio）；
临时库与会话来自 ``tests/db/conftest.py`` 的 ``db_session``。

覆盖的硬约束：

- ``upsert`` 按 ``run_id`` 覆盖：同一运行第二次提交只改行、不新增，``created_at``
  保留、``updated_at`` 取入参；唯一约束不会炸；
- ``get_by_run`` 命中返回 ORM 实例，未知运行返回 ``None``；
- ``list_since`` 左闭、按 ``created_at DESC, id DESC`` 排序并尊重 ``limit``。
"""

import asyncio
from datetime import datetime, timedelta

from sqlalchemy import func, select

from maa_api.db.models import AgentRun, RunFeedback
from maa_api.db.repositories.feedback import RunFeedbackRepository


def _at(minutes: int) -> datetime:
    """固定的 naive UTC 时刻，避免用例依赖当前时间。"""
    return datetime(2026, 1, 1) + timedelta(minutes=minutes)


def _agent_run(run_id: str) -> AgentRun:
    """``run_feedback.run_id`` 有 CASCADE 外键，评价必须先有运行。"""
    return AgentRun(
        id=run_id,
        root_run_id=run_id,
        agent_name="legacy_incident",
        kind="legacy_turn",
        boot_id="boot-1",
    )


def _feedback(run_id: str, *, rating: int, minutes: int, **overrides) -> RunFeedback:
    fields = {
        "run_id": run_id,
        "rating": rating,
        "created_at": _at(minutes),
        "updated_at": _at(minutes),
    }
    fields.update(overrides)
    return RunFeedback(**fields)


def test_upsert_creates_then_updates_keeping_created_at(db_session) -> None:
    async def scenario() -> None:
        db_session.add(_agent_run("run-1"))
        await db_session.flush()
        repo = RunFeedbackRepository(db_session)

        created = await repo.upsert(
            _feedback("run-1", rating=5, minutes=0, reasons=["slow"], comment="还行")
        )
        assert created.id is not None
        assert created.created_at == _at(0)

        updated = await repo.upsert(
            _feedback(
                "run-1",
                rating=2,
                minutes=60,
                reasons=["inaccurate", "incomplete"],
                comment="不准",
            )
        )
        assert updated.id == created.id
        assert (updated.rating, updated.reasons, updated.comment) == (
            2,
            ["inaccurate", "incomplete"],
            "不准",
        )
        # 覆盖不换行：created_at 保留，updated_at 取入参。
        assert updated.created_at == _at(0)
        assert updated.updated_at == _at(60)

        rows = (
            await db_session.execute(select(func.count()).select_from(RunFeedback))
        ).scalar_one()
        assert rows == 1

    asyncio.run(scenario())


def test_get_by_run_and_list_since(db_session) -> None:
    async def scenario() -> None:
        db_session.add_all(
            [_agent_run(f"run-{index}") for index in (1, 2, 3, 4, 5)]
        )
        await db_session.flush()
        repo = RunFeedbackRepository(db_session)

        await repo.upsert(
            _feedback("run-1", rating=5, minutes=0, comment="最早")
        )
        await repo.upsert(_feedback("run-2", rating=1, minutes=10, comment="同刻 A"))
        await repo.upsert(_feedback("run-4", rating=3, minutes=10, comment="同刻 B"))
        await repo.upsert(_feedback("run-3", rating=4, minutes=20, comment="最新"))
        # run-5 没有评价。

        got = await repo.get_by_run("run-1")
        assert got is not None and got.rating == 5
        assert await repo.get_by_run("run-5") is None

        # 左闭：恰好落在 since 上的两条都算；同刻按 id DESC。
        listed = await repo.list_since(_at(10), limit=10)
        assert [row.run_id for row in listed] == ["run-3", "run-4", "run-2"]
        limited = await repo.list_since(_at(10), limit=2)
        assert [row.run_id for row in limited] == ["run-3", "run-4"]

        assert await repo.list_since(_at(21), limit=10) == []

    asyncio.run(scenario())
