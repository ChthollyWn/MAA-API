"""``LogRepository`` / ``ScreenshotRepository`` 的仓储行为测试（M2-08）。

覆盖 docs/04 §5.3/§5.4/§9/§10.4 的硬约束：

- ``bulk_insert`` 自成事务：调用方不 commit，另一个会话也能读到；
- ``created_at`` 用事件时间，不被入库时刻覆盖；
- ``query(after_id=...)`` 是严格 ``id > after_id``（WebSocket 续传游标），
  分页按 ``id DESC`` 而不是 ``created_at``；
- ``purge`` 先按天删、再按 id 升序删到 ``keep_max`` 以内，内部每批 LIMIT 5000
  （用 monkeypatch 缩小批上限验证分批），且自成事务；
- ``screenshot`` 的 ``deleted_at`` 只打一次、``list_older_than`` 只返回未清理的、
  ``total_size_active`` 不计已清理记录、``purge_before`` 不 commit。

用例形态：同步测试函数 + ``asyncio.run(scenario())``（本仓无 pytest-asyncio），
临时库与会话来自 ``tests/db/conftest.py``。
"""

import asyncio
from datetime import datetime, timedelta

import pytest
from sqlalchemy import text
from sqlalchemy.exc import IntegrityError

from maa_api.db.models import LogEntry, Pipeline, Screenshot, utcnow
from maa_api.db.repositories import log as log_module
from maa_api.db.repositories.log import LogRepository, ScreenshotRepository
from maa_api.domain.enums import (
    LogLevel,
    LogSource,
    PipelineSource,
    Priority,
    ScreenshotBackend,
    ScreenshotTrigger,
)

DEFAULT_SHOT = {
    "trigger": ScreenshotTrigger.MANUAL,
    "backend": ScreenshotBackend.CORE,
    "path": "image/screenshot/20260916/a.jpg",
    "format": "jpeg",
    "width": 1280,
    "height": 720,
    "size_bytes": 100,
}


def _entry(content: str, **overrides) -> LogEntry:
    fields = {
        "source": LogSource.SERVER,
        "level": LogLevel.INFO,
        "content": content,
    }
    fields.update(overrides)
    return LogEntry(**fields)


def _shot(**overrides) -> Screenshot:
    fields = dict(DEFAULT_SHOT)
    fields.update(overrides)
    return Screenshot(**fields)


async def _add_pipeline(session, pipeline_id: str = "p-1") -> str:
    """建一条真实流水线：``log_entry.pipeline_id`` / ``screenshot.pipeline_id``
    有外键，测试引擎开了 ``PRAGMA foreign_keys=ON``，不能拿假 id 写日志。"""
    session.add(
        Pipeline(
            id=pipeline_id,
            source=PipelineSource.MANUAL,
            priority=Priority.MANUAL,
            task_count=1,
            title="测试流水线",
        )
    )
    await session.commit()
    return pipeline_id


async def _count(session, table: str) -> int:
    return (await session.execute(text(f"select count(*) from {table}"))).scalar_one()


async def _log_contents(session) -> list[str]:
    """按 id 升序读出全部日志正文（与仓储的 DESC 排序相互独立）。"""
    result = await session.execute(text("select content from log_entry order by id"))
    return [row[0] for row in result.all()]


def test_bulk_insert_is_self_transacting_and_uses_event_timestamps(db_session_factory):
    """批量落盘立即提交；``created_at`` 用事件时间，id 单调递增。"""
    now = utcnow()
    events = [now - timedelta(days=3), now - timedelta(days=2), now - timedelta(days=1)]

    async def scenario():
        async with db_session_factory() as session:
            repo = LogRepository(session)
            entries = [
                _entry(f"line{i}", created_at=ts) for i, ts in enumerate(events)
            ]
            assert await repo.bulk_insert(entries) == 3
            # flush 后 id 已填好，可直接当 WebSocket 广播游标
            assert all(entry.id is not None for entry in entries)
            assert [entry.id for entry in entries] == sorted(
                entry.id for entry in entries
            )
            # 调用方没有 commit，这里也不 commit

        async with db_session_factory() as session:
            result = await session.execute(
                text("select content, created_at from log_entry order by id")
            )
            rows = result.all()
            assert [row[0] for row in rows] == ["line0", "line1", "line2"]
            # 入库时刻是 now，若被 INSERT 时刻覆盖就不会等于 days 前的事件时间
            assert [datetime.fromisoformat(row[1]) for row in rows] == events

    asyncio.run(scenario())


def test_bulk_insert_empty_is_noop_and_does_not_commit_pending_work(db_session_factory):
    """空批次直接返回 0，不顺带提交同一会话里调用方尚未提交的写入。"""

    async def scenario():
        async with db_session_factory() as session:
            repo = LogRepository(session)
            session.add(_entry("pending"))  # 调用方的未提交写入
            assert await repo.bulk_insert([]) == 0

        async with db_session_factory() as session:
            # 空批次若 commit 了，上面那条 pending 就会落库
            assert await _count(session, "log_entry") == 0

    asyncio.run(scenario())


def test_bulk_insert_rolls_back_on_foreign_key_violation(db_session_factory):
    """写入失败回滚并抛出，会话保持可用（外键指向不存在的流水线）。"""

    async def scenario():
        async with db_session_factory() as session:
            repo = LogRepository(session)
            with pytest.raises(IntegrityError):
                await repo.bulk_insert([_entry("bad", pipeline_id="no-such-pipeline")])
            # 回滚后会话还能继续用，坏行没有落库
            assert await repo.bulk_insert([_entry("good")]) == 1

        async with db_session_factory() as session:
            assert await _log_contents(session) == ["good"]

    asyncio.run(scenario())


def test_query_filters_and_cursor_semantics(db_session_factory):
    """过滤条件、时间闭区间、``id > after_id`` 续传语义、id DESC 排序。"""
    now = utcnow()

    async def scenario():
        async with db_session_factory() as session:
            await _add_pipeline(session)
            repo = LogRepository(session)
            await repo.bulk_insert(
                [
                    _entry("s-old", created_at=now - timedelta(days=3)),
                    _entry(
                        "s-new", level=LogLevel.WARNING, created_at=now - timedelta(hours=1)
                    ),
                    _entry(
                        "maa",
                        source=LogSource.MAA_TASK,
                        created_at=now - timedelta(hours=2),
                        pipeline_id="p-1",
                    ),
                    _entry(
                        "debug",
                        source=LogSource.MAACORE_DEBUG,
                        level=LogLevel.ERROR,
                        created_at=now,
                    ),
                    # 最后入库但事件时间最老：排序必须按 id 而不是 created_at
                    _entry("late-old", created_at=now - timedelta(days=30)),
                ]
            )

            all_page = await repo.query(page=1, size=10)
            assert (all_page.total, len(all_page.items), all_page.page, all_page.size) == (
                5,
                5,
                1,
                10,
            )
            assert [x.content for x in all_page.items] == [
                "late-old",
                "debug",
                "maa",
                "s-new",
                "s-old",
            ]
            assert [x.id for x in all_page.items] == sorted(
                (x.id for x in all_page.items), reverse=True
            )

            by_source = await repo.query(sources=[LogSource.SERVER])
            assert [x.content for x in by_source.items] == ["late-old", "s-new", "s-old"]
            assert (
                await repo.query(sources=[LogSource.SERVER, LogSource.MAA_TASK])
            ).total == 4
            assert (await repo.query(levels=[LogLevel.ERROR])).total == 1
            assert (await repo.query(pipeline_id="p-1")).total == 1
            assert (await repo.query(pipeline_id="no-such-pipeline")).total == 0

            # 空序列 = 没有任何来源/级别匹配，而不是「不过滤」
            assert (await repo.query(sources=[])).total == 0
            assert (await repo.query(levels=[])).total == 0

            # since / until 都是含端点的闭区间（now-2h 的 maa 两侧都命中）
            since = await repo.query(since=now - timedelta(hours=2))
            assert {x.content for x in since.items} == {"s-new", "maa", "debug"}
            until = await repo.query(until=now - timedelta(hours=2))
            assert {x.content for x in until.items} == {"maa", "s-old", "late-old"}

            # 续传游标：严格 id > after_id，只补发之后的记录
            cursor = all_page.items[2].id  # maa
            backfill = await repo.query(after_id=cursor)
            assert [x.content for x in backfill.items] == ["late-old", "debug"]
            assert (await repo.query(after_id=all_page.items[0].id)).total == 0

            # 游标 + 来源过滤组合
            assert (
                await repo.query(sources=[LogSource.SERVER], after_id=cursor)
            ).total == 1

    asyncio.run(scenario())


def test_query_pagination_and_clamping(db_session_factory):
    """分页边界与越界参数夹取（``page<=0`` / ``size<=0`` 不能生成负 offset）。"""

    async def scenario():
        async with db_session_factory() as session:
            repo = LogRepository(session)
            await repo.bulk_insert([_entry(f"l{i}") for i in range(5)])

            page2 = await repo.query(page=2, size=2)
            assert (page2.total, len(page2.items), page2.page, page2.size) == (5, 2, 2, 2)
            assert [x.content for x in page2.items] == ["l2", "l1"]

            clamped = await repo.query(page=0, size=0)
            assert (clamped.page, clamped.size, len(clamped.items)) == (1, 1, 1)
            assert clamped.items[0].content == "l4"

            beyond = await repo.query(page=99, size=10)
            assert (beyond.total, beyond.items) == (5, [])

    asyncio.run(scenario())


def test_purge_by_age_then_keep_max_is_self_transacting(db_session_factory):
    """两维清理：先删超过保留天数的，再按 id 升序删到 keep_max 以内。"""
    now = utcnow()

    async def scenario():
        async with db_session_factory() as session:
            repo = LogRepository(session)
            await repo.bulk_insert(
                [_entry(f"old{i}", created_at=now - timedelta(days=30 + i)) for i in range(3)]
                + [_entry(f"new{i}", created_at=now) for i in range(4)]
            )
            # 3 条超期 + 2 条超出条数上限（保留最新 2 条）
            assert await repo.purge(
                LogSource.SERVER, before=now - timedelta(days=7), keep_max=2
            ) == 5
            # 调用方不 commit：purge 自成事务，另一个会话也应看到结果

        async with db_session_factory() as session:
            assert await _log_contents(session) == ["new2", "new3"]

    asyncio.run(scenario())


def test_purge_respects_batch_limit(db_session_factory, monkeypatch):
    """每批最多 ``PURGE_BATCH_SIZE`` 行：两维都分批，单次调用内部循环删完。

    monkeypatch 把批上限缩到 2，用 6 行数据逼出多批路径（真按 5000 行造数据
    太慢，且批量边界才是这里要钉住的行为）。
    """
    monkeypatch.setattr(log_module, "PURGE_BATCH_SIZE", 2)
    now = utcnow()

    async def scenario():
        async with db_session_factory() as session:
            repo = LogRepository(session)
            # 第一维分批：5 条超期日志按 2+2+1 三批删
            await repo.bulk_insert(
                [_entry(f"old{i}", created_at=now - timedelta(days=30)) for i in range(5)]
                + [_entry("keep", created_at=now)]
            )
            assert await repo.purge(
                LogSource.SERVER, before=now - timedelta(days=7), keep_max=1
            ) == 5

            # 第二维分批：此时共 7 条（keep + fresh0..5），保留 1 条需删 6 条，
            # 按 2+2+2 三批删
            await repo.bulk_insert([_entry(f"fresh{i}") for i in range(6)])
            assert await repo.purge(
                LogSource.SERVER, before=now - timedelta(days=7), keep_max=1
            ) == 6

        async with db_session_factory() as session:
            assert await _log_contents(session) == ["fresh5"]

    asyncio.run(scenario())


def test_purge_returns_zero_when_nothing_to_do(db_session_factory):
    """没有可删记录时返回 0：M2-12 的清理循环靠这个值终止。"""
    now = utcnow()

    async def scenario():
        async with db_session_factory() as session:
            repo = LogRepository(session)
            assert await repo.purge(
                LogSource.SERVER, before=now - timedelta(days=7), keep_max=10
            ) == 0
            await repo.bulk_insert([_entry("fresh")])
            assert await repo.purge(
                LogSource.SERVER, before=now - timedelta(days=7), keep_max=10
            ) == 0

        async with db_session_factory() as session:
            assert await _log_contents(session) == ["fresh"]

    asyncio.run(scenario())


def test_purge_only_touches_requested_source(db_session_factory):
    """清理按来源分级：删 SERVER 时不能碰 MAA_TASK 与 MAACORE_DEBUG。"""
    now = utcnow()

    async def scenario():
        async with db_session_factory() as session:
            repo = LogRepository(session)
            await repo.bulk_insert(
                [
                    _entry("s1", created_at=now - timedelta(days=30)),
                    _entry("s2", created_at=now),
                    _entry(
                        "m1",
                        source=LogSource.MAA_TASK,
                        created_at=now - timedelta(days=300),
                    ),
                    _entry(
                        "d1",
                        source=LogSource.MAACORE_DEBUG,
                        created_at=now - timedelta(days=300),
                    ),
                ]
            )
            assert await repo.purge(
                LogSource.SERVER, before=now - timedelta(days=7), keep_max=0
            ) == 2

        async with db_session_factory() as session:
            assert await _log_contents(session) == ["m1", "d1"]

    asyncio.run(scenario())


def test_screenshot_create_get_and_list_by_pipeline(db_session_factory):
    """``create`` 只 flush；``list_by_pipeline`` 含过期条目、按 ``created_at`` 升序。"""
    now = utcnow()

    async def scenario():
        async with db_session_factory() as session:
            await _add_pipeline(session)
            repo = ScreenshotRepository(session)
            first = await repo.create(
                _shot(
                    pipeline_id="p-1",
                    created_at=now - timedelta(hours=2),
                    path="image/screenshot/20260916/a.jpg",
                )
            )
            second = await repo.create(
                _shot(
                    pipeline_id="p-1",
                    created_at=now - timedelta(hours=1),
                    path="image/screenshot/20260916/b.jpg",
                )
            )
            await repo.create(
                _shot(pipeline_id=None, path="image/screenshot/20260916/c.jpg")
            )
            await session.commit()

            got = await repo.get(first.id)
            assert got is not None and got.path.endswith("a.jpg")
            assert got.deleted_at is None
            assert await repo.get("no-such-id") is None

            assert await repo.mark_deleted([second.id]) == 1
            await session.commit()

            listed = await repo.list_by_pipeline("p-1")
            assert [s.id for s in listed] == [first.id, second.id]
            assert listed[0].deleted_at is None
            assert listed[1].deleted_at is not None  # 过期条目仍要出现在详情页
            assert await repo.list_by_pipeline("no-such-pipeline") == []

        # create 不 commit，提交之后跨会话可见
        async with db_session_factory() as session:
            assert len(await ScreenshotRepository(session).list_by_pipeline("p-1")) == 2

    asyncio.run(scenario())


def test_screenshot_list_older_than_and_total_size_active(db_session_factory):
    """``list_older_than`` 只返回文件还在的旧记录；``total_size_active`` 不计已清理。"""
    now = utcnow()

    async def scenario():
        async with db_session_factory() as session:
            repo = ScreenshotRepository(session)
            old = await repo.create(
                _shot(created_at=now - timedelta(days=10), size_bytes=10)
            )
            mid = await repo.create(
                _shot(created_at=now - timedelta(days=8), size_bytes=20)
            )
            fresh = await repo.create(
                _shot(created_at=now - timedelta(days=1), size_bytes=40)
            )
            expired = await repo.create(
                _shot(created_at=now - timedelta(days=9), size_bytes=80)
            )
            await session.commit()
            assert await repo.mark_deleted([expired.id]) == 1
            await session.commit()

            older = await repo.list_older_than(now - timedelta(days=7))
            assert [s.id for s in older] == [old.id, mid.id]  # created_at 升序
            assert fresh.id not in {s.id for s in older}
            assert expired.id not in {s.id for s in older}
            assert await repo.list_older_than(now - timedelta(days=30)) == []

            assert await repo.total_size_active() == 70  # 10 + 20 + 40

    asyncio.run(scenario())


def test_screenshot_mark_deleted_is_idempotent(db_session_factory):
    """``deleted_at`` 只打一次：重复标记返回 0 且不刷新时间戳。"""

    async def scenario():
        async with db_session_factory() as session:
            repo = ScreenshotRepository(session)
            shot = await repo.create(_shot())
            await session.commit()

            assert await repo.mark_deleted([shot.id]) == 1
            first_stamp = (await repo.get(shot.id)).deleted_at
            assert first_stamp is not None

            assert await repo.mark_deleted([shot.id]) == 0
            assert (await repo.get(shot.id)).deleted_at == first_stamp

            assert await repo.mark_deleted([]) == 0
            assert await repo.mark_deleted(["no-such-id"]) == 0

    asyncio.run(scenario())


def test_screenshot_purge_before_deletes_old_records_without_commit(db_session_factory):
    """``purge_before`` 删旧记录但不 commit（回滚即恢复），提交后跨会话可见。"""
    now = utcnow()
    cutoff = now - timedelta(days=7)

    async def scenario():
        async with db_session_factory() as session:
            repo = ScreenshotRepository(session)
            await repo.create(_shot(created_at=now - timedelta(days=10)))
            await repo.create(_shot(created_at=now - timedelta(days=8)))
            await repo.create(_shot(created_at=now - timedelta(days=1)))
            await session.commit()

        async with db_session_factory() as session:
            repo = ScreenshotRepository(session)
            # 两条旧记录（都没有 deleted_at）按 created_at < cutoff 删除
            assert await repo.purge_before(cutoff) == 2
            await session.rollback()  # 不 commit，回滚后记录应原样回来

        async with db_session_factory() as session:
            assert await _count(session, "screenshot") == 3

        async with db_session_factory() as session:
            assert await ScreenshotRepository(session).purge_before(cutoff) == 2
            await session.commit()

        async with db_session_factory() as session:
            assert await _count(session, "screenshot") == 1

    asyncio.run(scenario())
