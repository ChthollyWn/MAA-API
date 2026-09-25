"""``tests/db`` 共享 fixture：临时 SQLite 库 + 异步会话（M2-07 建，M2-08~M2-12 直接用）。

本仓**没有 pytest-asyncio**（不要为异步用例新增插件依赖），所以这里的 fixture
全部是同步函数；异步用例统一写成「同步测试函数 + ``asyncio.run(scenario())``」：

.. code-block:: python

    def test_something(db_session_factory):
        async def scenario():
            async with db_session_factory() as session:
                repo = XxxRepository(session)
                ...
                await session.commit()

        asyncio.run(scenario())

可用的 fixture
--------------

``db_path``
    ``tmp_path / "test.db"``。需要用同步 ``sqlite3`` 或 Alembic（``sqlite:///``）
    旁路检查同一个库文件时用它。
``db_engine``
    ``poolclass=NullPool`` 的 :class:`AsyncEngine`，已 ``create_all`` 建好全部表。
    NullPool 是硬要求：同一个 engine 会被用例里多个 ``asyncio.run`` 事件循环使用，
    带连接池时第二次 ``asyncio.run`` 会拿到绑定在已关闭循环上的连接，报
    「attached to a different loop」。引擎复用 ``maa_api.db.session.make_engine``，
    因此六个 PRAGMA（含 ``foreign_keys=ON``，CASCADE 才真实生效）与生产一致。
    ``create_all`` 只用于测试，生产建表永远走 Alembic（docs/04 §8.3，M2-04）。
``db_session_factory``
    ``async_sessionmaker(db_engine, expire_on_commit=False)``。推荐用法是每个
    ``asyncio.run`` 场景里 ``async with db_session_factory() as session`` 开新会话；
    会话不 commit 的改动在退出时回滚。
``db_session``
    已经开好的 :class:`AsyncSession`（同样 ``expire_on_commit=False``），适合不需要
    显式 ``async with`` 的单场景用例；fixture 结束时会关闭它。

注意：``tests/db`` 下的用例若需要**跨会话**观察写入（例如验证「提交后才可见」、
「IntegrityError 后换会话」），请在同一 engine 上自行 ``db_session_factory()``
开多个会话，不要复用 ``db_session``。
"""

import asyncio

import pytest
from sqlalchemy.ext.asyncio import (
    AsyncEngine,
    AsyncSession,
    async_sessionmaker,
)
from sqlalchemy.pool import NullPool
from sqlmodel import SQLModel

import maa_api.db.models  # noqa: F401  # 注册 16 张表到 SQLModel.metadata
from maa_api.db.session import make_engine


def _async_url(path) -> str:
    return f"sqlite+aiosqlite:///{path}"


async def _create_all(engine: AsyncEngine) -> None:
    async with engine.begin() as conn:
        await conn.run_sync(SQLModel.metadata.create_all)


@pytest.fixture
def db_path(tmp_path):
    """临时库文件路径（``tmp_path/test.db``），不碰仓库的 ``resource/``。"""
    return tmp_path / "test.db"


@pytest.fixture
def db_engine(db_path) -> AsyncEngine:
    """已建表的 NullPool 异步引擎（挂生产同款 PRAGMA）。"""
    engine = make_engine(_async_url(db_path), poolclass=NullPool)
    asyncio.run(_create_all(engine))
    yield engine
    # 用同步接口 dispose：不依赖事件循环，NullPool 下也不会误关别的循环里的连接
    engine.sync_engine.dispose()


@pytest.fixture
def db_session_factory(db_engine) -> async_sessionmaker[AsyncSession]:
    """绑定 ``db_engine`` 的会话工厂，``expire_on_commit=False``。"""
    return async_sessionmaker(db_engine, expire_on_commit=False, class_=AsyncSession)


@pytest.fixture
def db_session(db_session_factory) -> AsyncSession:
    """已开好的会话；用例结束由 fixture 关闭（重复 close 是幂等的）。"""
    session = db_session_factory()
    yield session
    asyncio.run(session.close())
