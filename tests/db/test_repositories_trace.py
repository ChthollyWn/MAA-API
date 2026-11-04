"""``TraceSpanRepository`` 的仓储行为（M68 spec §4.2.5、§6）。

两条用例：

- :meth:`TraceSpanRepository.list_by_trace` 按 ``(started_at, id)`` 升序、只取
  指定 trace、``limit`` 生效；
- :meth:`TraceSpanRepository.purge_before` 每次删一批最旧的行、严格按
  ``started_at < cutoff`` 过滤，并且**自成事务**（调用方不 commit，另开会话也能
  看到删除）；用 ``monkeypatch`` 把批上限缩到 2 逼出多批路径。

``add_many`` 是造数入口，顺带覆盖「只 flush 不提交」：回滚后另一个会话查不到。
用例形态：同步测试函数 + ``asyncio.run(scenario())``（本仓无 pytest-asyncio），
临时库与会话来自 ``tests/db/conftest.py``。
"""

import asyncio
from datetime import datetime, timedelta

from sqlalchemy import text

from maa_api.db.models import TraceSpan, utcnow
from maa_api.db.repositories import trace as trace_module
from maa_api.db.repositories.trace import (
    TRACE_PURGE_BATCH_SIZE,
    TraceSpanRepository,
)


def _span(trace_id: str, span_id: str, *, started_at: datetime) -> TraceSpan:
    return TraceSpan(
        trace_id=trace_id,
        span_id=span_id,
        name="tool.execute",
        kind="tool",
        status="ok",
        started_at=started_at,
    )


async def _count(session) -> int:
    return (await session.execute(text("select count(*) from trace_span"))).scalar_one()


async def _span_ids(session) -> list[str]:
    """按 id 升序读出全部 span_id（与仓储的 started_at 排序相互独立）。"""
    result = await session.execute(text("select span_id from trace_span order by id"))
    return [row[0] for row in result.all()]


def test_list_by_trace_orders_by_start_and_respects_limit(db_session_factory):
    base = utcnow()

    def rows() -> list[TraceSpan]:
        # 插入顺序 ≠ 开始时间顺序；同刻的两行由 id 决定先后。
        return [
            _span("trace-a", "span-late", started_at=base + timedelta(seconds=2)),
            _span("trace-a", "span-early-first", started_at=base),
            _span("trace-a", "span-early-second", started_at=base),
            _span("trace-b", "span-other", started_at=base - timedelta(seconds=5)),
        ]

    async def scenario():
        async with db_session_factory() as session:
            batch = rows()
            await TraceSpanRepository(session).add_many(batch)
            # 只 flush：id 已由数据库填好，同刻行按插入顺序拿到递增 id。
            assert [row.id for row in batch] == sorted(row.id for row in batch)
            await session.rollback()

        async with db_session_factory() as session:
            # add_many 没有提交：回滚之后另一个会话看不到这批行。
            assert await _count(session) == 0
            await TraceSpanRepository(session).add_many(rows())
            await session.commit()

        async with db_session_factory() as session:
            repo = TraceSpanRepository(session)
            assert [span.span_id for span in await repo.list_by_trace("trace-a", limit=10)] == [
                "span-early-first",
                "span-early-second",
                "span-late",
            ]
            assert [
                span.span_id for span in await repo.list_by_trace("trace-a", limit=2)
            ] == ["span-early-first", "span-early-second"]
            assert await repo.list_by_trace("trace-missing", limit=10) == []

    asyncio.run(scenario())


def test_purge_before_deletes_in_batches_and_commits(db_session_factory, monkeypatch):
    assert TRACE_PURGE_BATCH_SIZE == 5000
    monkeypatch.setattr(trace_module, "TRACE_PURGE_BATCH_SIZE", 2)
    base = utcnow()
    cutoff = base
    old_started = [base - timedelta(days=30) + timedelta(seconds=i) for i in range(5)]
    fresh_started = [base + timedelta(seconds=i) for i in range(2)]

    async def scenario():
        async with db_session_factory() as session:
            repo = TraceSpanRepository(session)
            await repo.add_many(
                [
                    *[
                        _span("trace-old", f"old-{i}", started_at=ts)
                        for i, ts in enumerate(old_started)
                    ],
                    *[
                        _span("trace-fresh", f"fresh-{i}", started_at=ts)
                        for i, ts in enumerate(fresh_started)
                    ],
                ]
            )
            await session.commit()

        async def purge_once() -> int:
            async with db_session_factory() as session:
                return await TraceSpanRepository(session).purge_before(cutoff)

        async def remaining() -> list[str]:
            async with db_session_factory() as session:
                return await _span_ids(session)

        # 每批 2 条、按 id 升序删最旧的行；调用方不 commit，另开会话就能看到删除。
        assert await purge_once() == 2
        assert await remaining() == ["old-2", "old-3", "old-4", "fresh-0", "fresh-1"]

        assert await purge_once() == 2
        assert await purge_once() == 1
        # 小于批量说明这一维已经删完；截止时间之后的行一条都不动。
        assert await purge_once() == 0
        assert await remaining() == ["fresh-0", "fresh-1"]

    asyncio.run(scenario())
