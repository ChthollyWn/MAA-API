"""M74 压缩检查点仓储与 ``AgentMessageRepository.list_after_seq``（spec §4.1、§6）。

用例形态与 ``tests/db/test_repositories_ticket.py`` 一致：同步测试函数 +
``asyncio.run(scenario())``（本仓无 pytest-asyncio），临时库走 ``tests/db/conftest.py``
的 ``db_session_factory``（NullPool + ``create_all``，PRAGMA 与生产一致）。

覆盖的硬约束：

- ``supersede_active`` + ``add`` 在同一事务里写完两个检查点后只有最新的是
  ``active``；第二次 ``supersede_active`` 返回改到的行数。
- ``active_for_session`` 只取 active；``list_for_session`` 按 ``created_at`` 降序、
  含已被取代的检查点。
- ``list_after_seq`` 只返回 ``seq > after_seq`` 的消息且按 ``seq`` 升序。
- 外键语义：会话删除级联删检查点；运行删除把 ``run_id`` 置空、检查点保留。
"""

import asyncio
from datetime import datetime

from sqlalchemy import delete, func, select

from maa_api.db.models import AgentMessage, AgentRun, AgentSession, CompactionCheckpoint
from maa_api.db.repositories.agent import AgentMessageRepository
from maa_api.db.repositories.compaction import CompactionCheckpointRepository
from tests.fakes.v3_db import seed_run, seed_session


def _checkpoint(
    session_id: str,
    *,
    upto_seq: int,
    created_at: datetime,
    run_id: str | None = None,
    trigger: str = "auto",
) -> CompactionCheckpoint:
    return CompactionCheckpoint(
        session_id=session_id,
        run_id=run_id,
        upto_seq=upto_seq,
        summary=f"【压缩摘要】seq ≤ {upto_seq}",
        summary_json={"degraded": False},
        durable={"open_tickets": []},
        tokens_before=1000,
        tokens_after=400,
        model=None,
        trigger=trigger,
        created_at=created_at,
    )


def test_supersede_keeps_a_single_active_checkpoint(db_session_factory) -> None:
    async def scenario() -> None:
        session_id = await seed_session(db_session_factory)
        async with db_session_factory() as session:
            repo = CompactionCheckpointRepository(session)

            # 第一次压缩：没有旧的 active，supersede 改到 0 行。
            assert await repo.supersede_active(session_id) == 0
            await repo.add(
                _checkpoint(
                    session_id,
                    upto_seq=10,
                    created_at=datetime(2026, 11, 1, 0, 0, 0),
                )
            )
            await session.commit()

            # 第二次压缩：同一事务里先置旧为 superseded 再插新行。
            assert await repo.supersede_active(session_id) == 1
            await repo.add(
                _checkpoint(
                    session_id,
                    upto_seq=20,
                    created_at=datetime(2026, 11, 1, 0, 10, 0),
                )
            )
            await session.commit()

            active = await repo.active_for_session(session_id)
            assert active is not None
            assert active.upto_seq == 20
            active_count = (
                await session.execute(
                    select(func.count())
                    .select_from(CompactionCheckpoint)
                    .where(
                        CompactionCheckpoint.session_id == session_id,
                        CompactionCheckpoint.status == "active",
                    )
                )
            ).scalar_one()
            assert active_count == 1

            # 列表按 created_at 降序，被取代的检查点仍在（前端要按 upto_seq 插标记）。
            listed = await repo.list_for_session(session_id)
            assert [item.upto_seq for item in listed] == [20, 10]
            assert [item.status for item in listed] == ["active", "superseded"]
            # 再调一次 supersede 只改剩下那一条。
            assert await repo.supersede_active(session_id) == 1
            assert await repo.active_for_session(session_id) is None

    asyncio.run(scenario())


def test_list_after_seq_reads_only_messages_after_the_checkpoint(
    db_session_factory,
) -> None:
    async def scenario() -> None:
        session_id = await seed_session(db_session_factory)
        async with db_session_factory() as session:
            for seq in range(5):
                session.add(
                    AgentMessage(
                        session_id=session_id,
                        seq=seq,
                        role="user",
                        content=f"m{seq}",
                    )
                )
            await session.commit()

            repo = AgentMessageRepository(session)
            after = await repo.list_after_seq(session_id, 1)
            assert [message.seq for message in after] == [2, 3, 4]
            # after_seq 之前的消息一条不留；大于末尾时为空。
            assert [m.seq for m in await repo.list_after_seq(session_id, -1)] == [
                0,
                1,
                2,
                3,
                4,
            ]
            assert await repo.list_after_seq(session_id, 4) == []

    asyncio.run(scenario())


def test_checkpoints_follow_session_and_run_lifecycle(db_session_factory) -> None:
    async def scenario() -> None:
        session_id = await seed_session(db_session_factory)
        run_id = await seed_run(db_session_factory, session_id=session_id)

        async with db_session_factory() as session:
            repo = CompactionCheckpointRepository(session)
            await repo.add(
                _checkpoint(
                    session_id,
                    upto_seq=3,
                    created_at=datetime(2026, 11, 1, 0, 0, 0),
                    run_id=run_id,
                )
            )
            await session.commit()

            # 运行删除只解除关联：检查点还在，run_id 变 NULL。
            run = await session.get(AgentRun, run_id)
            assert run is not None
            await session.delete(run)
            await session.commit()
            checkpoint = await repo.active_for_session(session_id)
            assert checkpoint is not None
            assert checkpoint.run_id is None

            # 会话删除级联删检查点。
            await session.execute(
                delete(AgentSession).where(AgentSession.id == session_id)
            )
            await session.commit()
            remaining = (
                await session.execute(
                    select(func.count())
                    .select_from(CompactionCheckpoint)
                    .where(CompactionCheckpoint.session_id == session_id)
                )
            ).scalar_one()
            assert remaining == 0

    asyncio.run(scenario())
