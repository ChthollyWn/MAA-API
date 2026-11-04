"""``InboxService`` 的写入、领取、丢弃与监听者语义（M69 规格 §4.2、§4.5.5、§6）。

异步用例一律「同步测试函数 + ``asyncio.run(scenario())``」；临时库用
``tests/agent/tickets/conftest.py`` 的 ``v3_factory``。领取会写
``consumed_run_id``（外键指向 ``agent_run``），所以用例先用 ``seed_run`` 造运行。
"""

from __future__ import annotations

import asyncio

import pytest
from sqlalchemy import func, select

from maa_api.agent.tickets.inbox import MAX_PAYLOAD_BYTES, InboxService
from maa_api.db.models import AgentInbox
from maa_api.domain.enums import InboxKind
from maa_api.domain.errors import AppError, ErrorCode
from tests.fakes.v3_db import seed_run, seed_session


async def _rows(factory, session_id: str) -> list[AgentInbox]:
    async with factory() as db:
        result = await db.execute(
            select(AgentInbox)
            .where(AgentInbox.session_id == session_id)
            .order_by(AgentInbox.id.asc())
        )
        return list(result.scalars().all())


async def _count(factory) -> int:
    async with factory() as db:
        result = await db.execute(select(func.count()).select_from(AgentInbox))
        return int(result.scalar_one())


def test_claim_returns_pending_in_id_order_with_limit(v3_factory) -> None:
    async def scenario() -> None:
        session_id = await seed_session(v3_factory)
        service = InboxService(v3_factory)
        written = [
            await service.put(
                session_id, InboxKind.USER_MESSAGE, payload={"index": index}
            )
            for index in range(5)
        ]
        run1 = await seed_run(v3_factory, session_id=session_id)
        first = await service.claim(session_id, run_id=run1, limit=3)
        assert [item.id for item in first] == [item.id for item in written[:3]]

        run2 = await seed_run(v3_factory, session_id=session_id, status="finished")
        second = await service.claim(session_id, run_id=run2, limit=3)
        assert [item.id for item in second] == [item.id for item in written[3:]]

        run3 = await seed_run(v3_factory, session_id=session_id, status="finished")
        assert await service.claim(session_id, run_id=run3, limit=3) == []
        assert await service.pending_count(session_id) == 0

    asyncio.run(scenario())


def test_claim_marks_consumed_with_run_id(v3_factory) -> None:
    async def scenario() -> None:
        session_id = await seed_session(v3_factory)
        service = InboxService(v3_factory)
        written = [
            await service.put(session_id, InboxKind.THREAD_EVENT, payload={})
            for _ in range(2)
        ]
        run_id = await seed_run(v3_factory, session_id=session_id)
        claimed = await service.claim(session_id, run_id=run_id, limit=50)
        assert [item.id for item in claimed] == [item.id for item in written]

        rows = await _rows(v3_factory, session_id)
        for row in rows:
            assert row.status == "consumed"
            assert row.consumed_run_id == run_id
            assert row.consumed_at is not None

    asyncio.run(scenario())


def test_concurrent_claims_never_share_items(v3_factory) -> None:
    """两个服务实例（各自一把锁）并发领取 20 次，数据库条件更新保证不重叠。"""

    async def scenario() -> None:
        writer = InboxService(v3_factory)
        first_service = InboxService(v3_factory)
        second_service = InboxService(v3_factory)
        for round_index in range(20):
            session_id = await seed_session(v3_factory)
            first_run = await seed_run(v3_factory, session_id=session_id)
            second_run = await seed_run(
                v3_factory, session_id=session_id, status="finished"
            )
            written = [
                await writer.put(
                    session_id, InboxKind.USER_MESSAGE, payload={"index": index}
                )
                for index in range(6)
            ]
            expected = {item.id for item in written}

            first, second = await asyncio.gather(
                first_service.claim(session_id, run_id=first_run, limit=50),
                second_service.claim(session_id, run_id=second_run, limit=50),
            )
            first_ids = {item.id for item in first}
            second_ids = {item.id for item in second}
            assert first_ids.isdisjoint(second_ids), round_index
            assert first_ids | second_ids == expected, round_index
            assert [item.id for item in first] == sorted(first_ids), round_index
            assert [item.id for item in second] == sorted(second_ids), round_index

    asyncio.run(scenario())


def test_claim_limit_validation(v3_factory) -> None:
    async def scenario() -> None:
        service = InboxService(v3_factory)
        for limit in (0, -1, 201):
            with pytest.raises(ValueError):
                await service.claim("session", run_id="run", limit=limit)

    asyncio.run(scenario())


def test_listener_called_after_commit(v3_factory) -> None:
    async def scenario() -> None:
        session_id = await seed_session(v3_factory)
        service = InboxService(v3_factory)
        calls: list[str] = []
        observed: list[list[tuple[int, str]]] = []
        tasks: list[asyncio.Task[None]] = []

        async def read_back() -> None:
            async with v3_factory() as db:
                result = await db.execute(
                    select(AgentInbox).where(AgentInbox.session_id == session_id)
                )
                observed.append(
                    [(int(item.id), str(item.status)) for item in result.scalars()]
                )

        def listener(got_session_id: str) -> None:
            calls.append(got_session_id)
            tasks.append(asyncio.get_running_loop().create_task(read_back()))

        def failing_listener(_session_id: str) -> None:
            raise RuntimeError("监听者失败")

        later_calls: list[str] = []
        service.add_listener(listener)
        service.add_listener(failing_listener)
        service.add_listener(later_calls.append)

        item = await service.put(
            session_id, InboxKind.USER_MESSAGE, payload={"text": "你好"}
        )
        await asyncio.gather(*tasks)
        # 监听者被调用时，提交已经完成，新会话能读到 pending 项。
        assert calls == [session_id]
        assert observed == [[(int(item.id), "pending")]]
        # 抛异常的监听者只记日志，不影响 put 返回与后面的监听者。
        assert later_calls == [session_id]
        assert await service.pending_count(session_id) == 1

    asyncio.run(scenario())


def test_put_rejects_oversized_payload_and_bad_thread_key(v3_factory) -> None:
    async def scenario() -> None:
        session_id = await seed_session(v3_factory)
        service = InboxService(v3_factory)

        cases = [
            {
                "payload": {"text": "x" * MAX_PAYLOAD_BYTES},
            },
            {"payload": ["不是", "对象"]},
            {"payload": {"nested": object()}},
            {"thread_key": "foo:1", "payload": {"ok": True}},
            {"ref_id": "r" * 65, "payload": {"ok": True}},
        ]
        for case in cases:
            with pytest.raises(AppError) as exc_info:
                await service.put(session_id, InboxKind.USER_MESSAGE, **case)
            assert exc_info.value.code is ErrorCode.INVALID_PARAMETER
            assert await _count(v3_factory) == 0

    asyncio.run(scenario())


def test_drop_pending(v3_factory) -> None:
    async def scenario() -> None:
        session_id = await seed_session(v3_factory)
        service = InboxService(v3_factory)
        written = [
            await service.put(session_id, InboxKind.USER_MESSAGE, payload={})
            for _ in range(4)
        ]
        run_id = await seed_run(v3_factory, session_id=session_id)
        claimed = await service.claim(session_id, run_id=run_id, limit=1)
        assert [item.id for item in claimed] == [written[0].id]
        assert await service.pending_count(session_id) == 3

        assert await service.drop_pending(session_id) == 3
        assert await service.pending_count(session_id) == 0
        assert await service.drop_pending(session_id) == 0

        rows = await _rows(v3_factory, session_id)
        assert rows[0].status == "consumed"
        assert rows[0].consumed_run_id == run_id
        for row in rows[1:]:
            assert row.status == "dropped"
            assert row.consumed_at is not None
            assert row.consumed_run_id is None

    asyncio.run(scenario())
