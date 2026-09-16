"""``maa_api/db/session.py`` 的引擎、PRAGMA 与会话配置测试。

本仓没有 pytest-asyncio：异步用例一律是**同步测试函数 + ``asyncio.run(...)``**，
事件循环内构造的引擎用 ``poolclass=NullPool``，避免连接跨事件循环复用。
所有库文件都落在 ``tmp_path``，不碰仓库的 ``resource/maa_api.db``。
"""

import asyncio
from pathlib import Path

import pytest
import sqlalchemy as sa
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.pool import NullPool

import maa_api.db.session as db_session

# docs/04 §2 的六个 PRAGMA 及其预期真实取值（NORMAL=1，MEMORY=2，-16000 KiB）。
EXPECTED_PRAGMAS = {
    "journal_mode": "wal",
    "foreign_keys": 1,
    "busy_timeout": 15000,
    "synchronous": 1,
    "temp_store": 2,
    "cache_size": -16000,
}


def _url(tmp_path: Path, name: str = "test.db") -> str:
    return f"sqlite+aiosqlite:///{tmp_path / name}"


async def _collect_pragmas(conn) -> dict:
    return {
        name: (await conn.exec_driver_sql(f"PRAGMA {name}")).scalar()
        for name in EXPECTED_PRAGMAS
    }


def test_db_path_and_urls_are_relative_to_resource_dir():
    """DB_PATH 必须是相对路径：目录创建归 db/migrate.py 的 ensure_schema。"""
    assert db_session.DB_PATH == Path("resource") / "maa_api.db"
    assert not db_session.DB_PATH.is_absolute()
    assert db_session.ASYNC_URL == "sqlite+aiosqlite:///resource/maa_api.db"
    assert db_session.SYNC_URL == "sqlite:///resource/maa_api.db"


def test_module_engine_has_pragma_listener_and_factory_config():
    """模块级 engine 与 session_factory 的装配：挂上监听器、提交后不过期。"""
    assert sa.event.contains(db_session.engine.sync_engine, "connect", db_session.apply_pragmas)
    assert db_session.engine.url.render_as_string(hide_password=False) == db_session.ASYNC_URL
    assert db_session.CONNECT_ARGS == {"timeout": 15, "check_same_thread": False}
    assert db_session.session_factory.class_ is AsyncSession
    assert db_session.session_factory.kw["expire_on_commit"] is False
    assert db_session.session_factory.kw["bind"] is db_session.engine


def test_six_pragmas_take_effect_on_real_connection(tmp_path):
    """用 tmp_path 的 URL 构造引擎，读回的是 SQLite 真实值而不是声明。"""

    async def main():
        engine = db_session.make_engine(_url(tmp_path), poolclass=NullPool)
        try:
            async with engine.connect() as conn:
                return await _collect_pragmas(conn)
        finally:
            await engine.dispose()

    values = asyncio.run(main())
    assert values == EXPECTED_PRAGMAS
    assert (tmp_path / "test.db").exists()


def test_pragmas_apply_to_every_pooled_connection(tmp_path):
    """PRAGMA 是连接级的：池里同时占用的两条连接都必须带 foreign_keys。"""

    async def main():
        engine = db_session.make_engine(_url(tmp_path, "pool.db"))  # 默认池
        try:
            async with engine.connect() as first, engine.connect() as second:
                raw_first = await first.get_raw_connection()
                raw_second = await second.get_raw_connection()
                assert raw_first.dbapi_connection is not raw_second.dbapi_connection
                assert await _collect_pragmas(first) == EXPECTED_PRAGMAS
                assert await _collect_pragmas(second) == EXPECTED_PRAGMAS
        finally:
            await engine.dispose()

    asyncio.run(main())


def test_foreign_keys_are_enforced_and_cascade(tmp_path):
    """foreign_keys=ON 是真的生效：孤儿插入被拒、删除父行级联删子行。"""

    async def main():
        engine = db_session.make_engine(_url(tmp_path, "fk.db"), poolclass=NullPool)
        try:
            async with engine.begin() as conn:
                await conn.exec_driver_sql("CREATE TABLE parent (id INTEGER PRIMARY KEY)")
                await conn.exec_driver_sql(
                    "CREATE TABLE child ("
                    "id INTEGER PRIMARY KEY, "
                    "parent_id INTEGER NOT NULL REFERENCES parent (id) ON DELETE CASCADE)"
                )
                await conn.exec_driver_sql("INSERT INTO parent (id) VALUES (1)")
                await conn.exec_driver_sql("INSERT INTO child (id, parent_id) VALUES (1, 1)")
            async with engine.connect() as conn:
                with pytest.raises(sa.exc.IntegrityError):
                    await conn.exec_driver_sql("INSERT INTO child (id, parent_id) VALUES (2, 999)")
            async with engine.begin() as conn:
                await conn.exec_driver_sql("DELETE FROM parent WHERE id = 1")
            async with engine.connect() as conn:
                assert (await conn.exec_driver_sql("SELECT COUNT(*) FROM child")).scalar() == 0
        finally:
            await engine.dispose()

    asyncio.run(main())


def test_session_factory_supports_async_context_manager(tmp_path):
    """session_factory 可换 bind 指向测试库，读写走异步会话。"""

    async def main():
        engine = db_session.make_engine(_url(tmp_path, "session.db"), poolclass=NullPool)
        try:
            async with db_session.session_factory(bind=engine) as session:
                assert isinstance(session, AsyncSession)
                assert (await session.execute(sa.text("PRAGMA foreign_keys"))).scalar() == 1
        finally:
            await engine.dispose()

    asyncio.run(main())


def test_committed_object_is_not_expired(tmp_path):
    """expire_on_commit=False：提交后仍能读字段（WebSocket 广播依赖）。

    后半段用 ``expire_on_commit=True`` 做对照，证明 ``inspect(...).expired``
    这个断言信号是有效的。
    """
    table = sa.Table(
        "probe_row",
        sa.MetaData(),
        sa.Column("id", sa.Integer, primary_key=True),
        sa.Column("label", sa.String(16), nullable=False),
    )
    registry = sa.orm.registry()

    class Probe:
        def __init__(self, label: str) -> None:
            self.label = label

    registry.map_imperatively(Probe, table)

    async def main():
        engine = db_session.make_engine(_url(tmp_path, "expire.db"), poolclass=NullPool)
        try:
            async with engine.begin() as conn:
                await conn.run_sync(table.metadata.create_all)

            async with db_session.session_factory(bind=engine) as session:
                row = Probe("hello")
                session.add(row)
                await session.commit()
                assert sa.inspect(row).expired is False
                assert row.id is not None
                assert row.label == "hello"

            async with db_session.session_factory(bind=engine, expire_on_commit=True) as session:
                control = await session.get(Probe, row.id)
                await session.commit()
                assert sa.inspect(control).expired is True
        finally:
            await engine.dispose()

    asyncio.run(main())
