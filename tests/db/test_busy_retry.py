"""``db/session.py`` 的写锁重试与错误描述（docs/13 §8.32）。

两条线索：

- 策略单测用假 session，确定「只重试 BUSY、次数有上限、退避不为零」；
- 集成用例用真实 SQLite：另一个会话持写锁不放，验证第一次 INSERT 撞锁失败、
  锁释放后重试真的成功——这正是全量测试里那条偶发失败的机制。
"""

from __future__ import annotations

import asyncio
import sqlite3

import pytest
from sqlalchemy import text
from sqlalchemy.exc import OperationalError

from maa_api.db.models import Screenshot
from maa_api.db.repositories.log import ScreenshotRepository
from maa_api.db.session import (
    describe_database_error,
    is_sqlite_busy_error,
    run_with_busy_retry,
)
from maa_api.domain.enums import ScreenshotBackend, ScreenshotTrigger


def _busy_error() -> OperationalError:
    return OperationalError(
        "INSERT INTO busy_noise VALUES (1)",
        {},
        sqlite3.OperationalError("database is locked"),
    )


def _missing_table_error() -> OperationalError:
    return OperationalError(
        "SELECT 1 FROM nope", {}, sqlite3.OperationalError("no such table: nope")
    )


class _FakeSavepoint:
    def __init__(self, session: "_FakeSession") -> None:
        self.session = session

    async def __aenter__(self) -> None:
        return None

    async def __aexit__(self, *_exc: object) -> bool:
        self.session.open_savepoints -= 1
        return False


class _FakeSession:
    """只实现 ``begin_nested()`` 的替身，用来观察重试时是否重开 savepoint。"""

    def __init__(self) -> None:
        self.savepoints = 0
        self.open_savepoints = 0

    def begin_nested(self) -> _FakeSavepoint:
        self.savepoints += 1
        self.open_savepoints += 1
        return _FakeSavepoint(self)


async def _no_sleep(_delay: float) -> None:
    return None


def test_is_sqlite_busy_error_matches_lock_contention_only() -> None:
    assert is_sqlite_busy_error(_busy_error()) is True
    assert is_sqlite_busy_error(_missing_table_error()) is False
    # 只有 SQLAlchemy 的 OperationalError 才有 orig；别的异常即使文案相同也不重试
    assert is_sqlite_busy_error(RuntimeError("database is locked")) is False


def test_describe_database_error_prefers_the_dbapi_message() -> None:
    assert describe_database_error(_busy_error()) == "OperationalError: database is locked"
    assert describe_database_error(RuntimeError("boom")) == "RuntimeError: boom"
    assert describe_database_error(sqlite3.OperationalError("")) == "OperationalError"


def test_run_with_busy_retry_retries_busy_failures_until_it_succeeds() -> None:
    session = _FakeSession()
    attempts = 0
    delays: list[float] = []

    async def operation() -> str:
        nonlocal attempts
        attempts += 1
        if attempts < 3:
            raise _busy_error()
        return "archived"

    async def sleep(delay: float) -> None:
        delays.append(delay)

    result = asyncio.run(
        run_with_busy_retry(session, operation, attempts=3, base_delay=0.1, sleep=sleep)
    )

    assert result == "archived"
    assert attempts == 3
    assert session.savepoints == 3
    assert session.open_savepoints == 0
    assert len(delays) == 2
    assert all(delay > 0 for delay in delays)


def test_run_with_busy_retry_gives_up_after_the_budget() -> None:
    session = _FakeSession()
    attempts = 0

    async def operation() -> None:
        nonlocal attempts
        attempts += 1
        raise _busy_error()

    with pytest.raises(OperationalError, match="database is locked"):
        asyncio.run(
            run_with_busy_retry(session, operation, attempts=2, sleep=_no_sleep)
        )

    assert attempts == 2
    assert session.open_savepoints == 0


def test_run_with_busy_retry_does_not_retry_other_operational_errors() -> None:
    session = _FakeSession()
    attempts = 0

    async def operation() -> None:
        nonlocal attempts
        attempts += 1
        raise _missing_table_error()

    with pytest.raises(OperationalError, match="no such table"):
        asyncio.run(run_with_busy_retry(session, operation, sleep=_no_sleep))

    assert attempts == 1


def test_savepoint_retry_recovers_when_a_concurrent_writer_releases(
    db_engine, db_session_factory
) -> None:
    """真实 SQLite：另一个会话持写锁 → 首次失败 → 释放后重试成功。"""

    async def scenario() -> None:
        async with db_engine.begin() as connection:
            await connection.exec_driver_sql(
                "CREATE TABLE IF NOT EXISTS busy_noise (x INTEGER)"
            )

        holder = db_session_factory()
        worker = db_session_factory()
        try:
            # 两个会话各自把忙等压到 50ms，用例不必等生产配置的 15 秒。
            await holder.execute(text("PRAGMA busy_timeout=50"))
            await worker.execute(text("PRAGMA busy_timeout=50"))
            await holder.execute(text("INSERT INTO busy_noise VALUES (1)"))
            # 先读一次：让 worker 有活动事务（并复现「读到写」的升级路径）
            await worker.execute(text("SELECT 1"))

            attempts = 0
            releases: list[float] = []

            async def operation() -> int:
                nonlocal attempts
                attempts += 1
                await worker.execute(text("INSERT INTO busy_noise VALUES (2)"))
                return attempts

            async def release(_delay: float) -> None:
                releases.append(_delay)
                await holder.rollback()   # 释放写锁，丢掉 holder 那行

            result = await run_with_busy_retry(
                worker, operation, attempts=3, base_delay=0.01, sleep=release
            )
        finally:
            await holder.close()
            await worker.close()

        assert result == 2, "第一次必须真的撞锁失败，第二次才成功"
        assert attempts == 2
        assert len(releases) == 1

    asyncio.run(scenario())


def test_savepoint_retry_of_the_archive_insert_keeps_exactly_one_row(
    db_engine, db_session_factory
) -> None:
    """生产路径的 ORM 版本：``create()`` 撞锁回退 savepoint 后，重试不留重复行。

    ``ScreenshotRepository.create()`` 每次都会 ``add()`` 一个新实例；如果 savepoint
    回滚没有把第一次的待插入对象清出去，重试就会写成两行（两个不同 UUID）。
    """
    from sqlalchemy import func, select

    async def scenario() -> None:
        async with db_engine.begin() as connection:
            await connection.exec_driver_sql(
                "CREATE TABLE IF NOT EXISTS busy_noise (x INTEGER)"
            )

        holder = db_session_factory()
        worker = db_session_factory()
        try:
            await holder.execute(text("PRAGMA busy_timeout=50"))
            await worker.execute(text("PRAGMA busy_timeout=50"))
            await holder.execute(text("INSERT INTO busy_noise VALUES (1)"))

            attempts = 0

            def row() -> Screenshot:
                return Screenshot(
                    trigger=ScreenshotTrigger.MANUAL,
                    backend=ScreenshotBackend.CORE,
                    path="image/screenshot/aa/busy.jpg",
                    format="jpg",
                    width=1,
                    height=1,
                    size_bytes=1,
                )

            async def operation() -> int:
                nonlocal attempts
                attempts += 1
                await ScreenshotRepository(worker).create(row())
                return attempts

            async def release(_delay: float) -> None:
                await holder.rollback()

            result = await run_with_busy_retry(
                worker, operation, attempts=3, base_delay=0.01, sleep=release
            )
            await worker.commit()

            async with db_session_factory() as checker:
                stored = await checker.scalar(select(func.count()).select_from(Screenshot))
        finally:
            await holder.close()
            await worker.close()

        assert result == 2
        assert attempts == 2
        assert stored == 1, "重试不得把第一次的待插入对象也写进去"

    asyncio.run(scenario())
