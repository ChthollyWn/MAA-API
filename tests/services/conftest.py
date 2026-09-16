"""``tests/services`` 共享 fixture：临时 resource 根 + 临时库 + 会话工厂（M2-12 建）。

与 ``tests/db/conftest.py`` 的差别：那边面向仓储，只需隔离库文件；保留策略还要
碰 ``<resource>/image/screenshot/`` 与 ``<resource>/temp/screencap/``，所以这里额外
把 ``maa_api.db.session`` 的 ``DB_PATH`` 指到 ``tmp_path/resource``，让服务推导出的
资源根也落在临时目录里（绝不碰仓库的 ``resource/``）。

本仓**没有 pytest-asyncio**（不要为异步用例新增插件依赖）：fixture 全是同步函数，
异步用例统一写成「同步测试函数 + ``asyncio.run(scenario())``」。

可用的 fixture
-------------
``resource_root``
    ``tmp_path / "resource"``，并把 ``session.DB_PATH`` / ``ASYNC_URL`` / ``SYNC_URL``
    一起改指到它下面的 ``maa_api.db``（monkeypatch，用例结束自动还原）。
``retention_engine``
    ``poolclass=NullPool`` 的 :class:`AsyncEngine`，已 ``create_all`` 建好全部表，
    复用 ``session.make_engine`` 所以六个 PRAGMA（含 ``foreign_keys=ON``）与生产一致。
    NullPool 是硬要求：同一个 engine 会被用例里多个 ``asyncio.run`` 事件循环使用。
``retention_session_factory``
    ``async_sessionmaker(retention_engine, expire_on_commit=False)``，直接传给
    ``run_retention(session_factory=...)``。
"""

import asyncio
from pathlib import Path

import pytest
from sqlalchemy.ext.asyncio import (
    AsyncEngine,
    AsyncSession,
    async_sessionmaker,
)
from sqlalchemy.pool import NullPool
from sqlmodel import SQLModel

import maa_api.db.models  # noqa: F401  # 注册 13 张表到 SQLModel.metadata
from maa_api.db import session as db_session
from maa_api.db.session import make_engine


async def _create_all(engine: AsyncEngine) -> None:
    async with engine.begin() as conn:
        await conn.run_sync(SQLModel.metadata.create_all)


@pytest.fixture
def resource_root(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    """临时 ``<resource>/`` 根，并把 session 模块的三个路径常量指过去。"""
    root = tmp_path / "resource"
    root.mkdir()
    db_path = root / "maa_api.db"
    monkeypatch.setattr(db_session, "DB_PATH", db_path)
    monkeypatch.setattr(db_session, "ASYNC_URL", f"sqlite+aiosqlite:///{db_path}")
    monkeypatch.setattr(db_session, "SYNC_URL", f"sqlite:///{db_path}")
    return root


@pytest.fixture
def retention_engine(resource_root: Path) -> AsyncEngine:
    """已建表的 NullPool 异步引擎（挂生产同款 PRAGMA）。"""
    engine = make_engine(
        f"sqlite+aiosqlite:///{resource_root / 'maa_api.db'}", poolclass=NullPool
    )
    asyncio.run(_create_all(engine))
    yield engine
    # 用同步接口 dispose：不依赖事件循环，NullPool 下也不会误关别的循环里的连接
    engine.sync_engine.dispose()


@pytest.fixture
def retention_session_factory(
    retention_engine: AsyncEngine,
) -> async_sessionmaker[AsyncSession]:
    """绑定 ``retention_engine`` 的会话工厂，``expire_on_commit=False``。"""
    return async_sessionmaker(
        retention_engine, expire_on_commit=False, class_=AsyncSession
    )
