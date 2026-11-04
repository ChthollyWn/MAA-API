"""v3 工单领域测试的临时库工具（M69 规格 §6 开头）。

三个函数与 ``tests/db/conftest.py`` 同口径：``NullPool`` 的异步引擎（同一 engine
会被多个 ``asyncio.run`` 使用）、``expire_on_commit=False`` 的会话工厂、
``SQLModel.metadata.create_all`` 建表。生产建表永远走 Alembic，这里只服务测试。

``seed_session`` / ``seed_run`` 各自开一条会话并提交，调用方拿到 id 后可以直接
用新会话读到行；需要更多字段时在测试里自行补写。
"""

from pathlib import Path

from sqlalchemy.ext.asyncio import (
    AsyncEngine,
    AsyncSession,
    async_sessionmaker,
)
from sqlalchemy.pool import NullPool
from sqlmodel import SQLModel

import maa_api.db.models  # noqa: F401  # 注册全部表到 SQLModel.metadata
from maa_api.db.models import AgentRun, AgentSession, new_uuid, utcnow
from maa_api.db.session import make_engine


async def make_session_factory(
    db_path: Path | str,
) -> tuple[AsyncEngine, async_sessionmaker[AsyncSession]]:
    """建一个 ``db_path`` 上的临时库，返回 ``(engine, session_factory)``。

    调用方负责在用例结束时 ``engine.sync_engine.dispose()``（与
    ``tests/db/conftest.py`` 的引擎 fixture 相同）。
    """
    engine = make_engine(f"sqlite+aiosqlite:///{db_path}", poolclass=NullPool)
    async with engine.begin() as conn:
        await conn.run_sync(SQLModel.metadata.create_all)
    factory = async_sessionmaker(engine, expire_on_commit=False, class_=AsyncSession)
    return engine, factory


async def seed_session(
    factory: async_sessionmaker[AsyncSession],
    *,
    kind: str = "worker",
    status: str = "active",
    ticket_id: str | None = None,
    archived: bool = False,
) -> str:
    """写一条 ``agent_session`` 并提交，返回会话 id。"""
    session = AgentSession(
        model="test-model",
        kind=kind,
        status=status,
        ticket_id=ticket_id,
        archived_at=utcnow() if archived else None,
    )
    async with factory() as db:
        db.add(session)
        await db.commit()
    return str(session.id)


async def seed_run(
    factory: async_sessionmaker[AsyncSession],
    *,
    session_id: str,
    ticket_id: str | None = None,
    status: str = "running",
    boot_id: str = "boot-test",
) -> str:
    """写一条 ``agent_run`` 并提交，返回运行 id（根运行的 ``root_run_id`` 指向自身）。"""
    run_id = new_uuid()
    run = AgentRun(
        id=run_id,
        root_run_id=run_id,
        session_id=session_id,
        ticket_id=ticket_id,
        agent_name="test",
        kind="worker_turn",
        status=status,
        boot_id=boot_id,
        started_at=utcnow(),
    )
    async with factory() as db:
        db.add(run)
        await db.commit()
    return run_id
