"""M71 设备租约测试（规格 T4、§6 的 ``test_device_lease_service.py``）。

异步用例一律「同步测试函数 + ``asyncio.run(scenario())``」；时间由 ``lease_world``
的可控时钟推进，不 sleep 真实租约时长。串行化用例内部循环 20 次，用来放大竞态。
"""

from __future__ import annotations

import asyncio
from datetime import timedelta

import pytest
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from maa_api.agent.lease import LEASE_DEVICE_ID, LeaseHolder
from maa_api.agent.tickets.service import TicketEnded
from maa_api.db.models import AgentRun, AgentSession, DeviceLease, Ticket
from maa_api.domain.enums import InboxKind, TicketStatus
from maa_api.domain.errors import AppError, ErrorCode


Factory = async_sessionmaker[AsyncSession]


async def _wait_until(predicate, *, timeout: float = 2.0) -> None:
    """轮询等待一个内存条件成立；失败时给出明确的超时信息。"""
    loop = asyncio.get_running_loop()
    deadline = loop.time() + timeout
    while not predicate():
        if loop.time() >= deadline:
            raise AssertionError("等待条件超时")
        await asyncio.sleep(0.005)


async def _all_rows(factory: Factory) -> list[DeviceLease]:
    async with factory() as db:
        result = await db.execute(select(DeviceLease))
        return list(result.scalars().all())


async def _row(factory: Factory) -> DeviceLease | None:
    rows = await _all_rows(factory)
    assert len(rows) <= 1
    return rows[0] if rows else None


async def _set_run_status(factory: Factory, run_id: str, status: str) -> None:
    async with factory() as db:
        run = await db.get(AgentRun, run_id)
        assert run is not None
        run.status = status
        await db.commit()


def _payload(events: list[tuple[str, dict]]) -> dict:
    """最后一次 ``agent_event`` 的 payload。"""
    assert events
    message_type, data = events[-1]
    assert message_type == "agent_event"
    assert data["event"] == "lease_changed"
    return data["payload"]


async def _extra_holder(factory: Factory) -> LeaseHolder:
    """先到先得用例的第三个工单（fixture 只预置两个）。

    工单 ↔ 会话是循环外键，必须分步插入：先工单、再会话、回填
    ``ticket.worker_session_id``、最后根运行。
    """
    ticket_id, session_id, run_id = "ticket-c", "session-c", "run-c"
    async with factory() as db:
        db.add(Ticket(id=ticket_id, title="工单丙", kind="daily_ops", origin="user"))
        await db.commit()
        db.add(
            AgentSession(
                id=session_id,
                model="test-model",
                kind="worker",
                status="active",
                ticket_id=ticket_id,
            )
        )
        await db.commit()
        ticket = await db.get(Ticket, ticket_id)
        assert ticket is not None
        ticket.worker_session_id = session_id
        await db.commit()
        db.add(
            AgentRun(
                id=run_id,
                root_run_id=run_id,
                session_id=session_id,
                ticket_id=ticket_id,
                agent_name="daily_ops",
                kind="worker_turn",
                status="running",
                boot_id="boot-test",
            )
        )
        await db.commit()
    return LeaseHolder(ticket_id=ticket_id, run_id=run_id, agent="daily_ops")


def test_acquire_inserts_single_row_and_broadcasts(lease_env, lease_world) -> None:
    factory, service, events, _published = lease_env
    world = lease_world

    async def scenario() -> None:
        holder = world.holders["a"]
        now = world.clock.now
        row = await service.acquire(holder, purpose="click")

        assert row.device_id == LEASE_DEVICE_ID
        assert row.ticket_id == world.ticket_ids["a"]
        assert row.run_id == world.run_ids["a"]
        assert row.holder_agent == "daily_ops"
        assert row.purpose == "click"
        assert row.explicit is False
        assert row.acquired_at == now
        assert row.heartbeat_at == now
        assert row.expires_at == now + timedelta(seconds=world.settings.ttl_seconds)
        assert row.boot_id == "boot-test"

        rows = await _all_rows(factory)
        assert len(rows) == 1
        assert rows[0].lease_id == row.lease_id

        assert len(events) == 1
        message_type, data = events[0]
        assert message_type == "agent_event"
        assert data["event"] == "lease_changed"
        payload = data["payload"]
        assert payload["change"] == "acquired"
        assert payload["reason"] is None
        assert payload["held"] is True
        lease = payload["lease"]
        assert lease["device_id"] == LEASE_DEVICE_ID
        assert lease["lease_id"] == row.lease_id
        assert lease["ticket_id"] == world.ticket_ids["a"]
        assert lease["ticket_title"] == "工单甲"
        assert lease["run_id"] == world.run_ids["a"]
        assert lease["holder_agent"] == "daily_ops"
        assert lease["purpose"] == "click"
        assert lease["explicit"] is False
        assert lease["acquired_at"].endswith("Z")
        assert lease["expires_at"].endswith("Z")
        assert lease["heartbeat_at"].endswith("Z")

    asyncio.run(scenario())


def test_two_holders_are_serialized(lease_env, lease_world) -> None:
    factory, service, _events, _published = lease_env
    world = lease_world
    holder_a = world.holders["a"]
    holder_b = world.holders["b"]

    async def scenario() -> None:
        for _round in range(20):
            await service.acquire(holder_a, purpose="loop", wait_seconds=5)
            waiter = asyncio.create_task(
                service.acquire(holder_b, purpose="loop", wait_seconds=5)
            )
            try:
                await _wait_until(lambda: service.waiters == 1)
                assert not waiter.done()
                # 任何时刻表里最多一行，且当前是 A。
                rows = await _all_rows(factory)
                assert len(rows) == 1
                assert rows[0].ticket_id == world.ticket_ids["a"]

                await service.release(holder_a)
                row_b = await asyncio.wait_for(waiter, timeout=5)
                assert row_b.ticket_id == world.ticket_ids["b"]

                rows = await _all_rows(factory)
                assert len(rows) == 1
                assert rows[0].ticket_id == world.ticket_ids["b"]
                await service.release(holder_b)
                assert await _all_rows(factory) == []
            finally:
                if not waiter.done():
                    waiter.cancel()
                    await asyncio.gather(waiter, return_exceptions=True)

    asyncio.run(scenario())


def test_waiters_are_served_first_come_first_served(lease_env, lease_world) -> None:
    factory, service, _events, _published = lease_env
    world = lease_world

    async def scenario() -> None:
        holder_a = world.holders["a"]
        holder_b = world.holders["b"]
        holder_c = await _extra_holder(factory)

        await service.acquire(holder_a, purpose="hold", wait_seconds=0)
        task_b = asyncio.create_task(
            service.acquire(holder_b, purpose="b", wait_seconds=5)
        )
        await _wait_until(lambda: service.waiters == 1)
        task_c = asyncio.create_task(
            service.acquire(holder_c, purpose="c", wait_seconds=5)
        )
        await _wait_until(lambda: service.waiters == 2)
        assert not task_b.done()
        assert not task_c.done()

        await service.release(holder_a)
        row_b = await asyncio.wait_for(task_b, timeout=5)
        assert row_b.ticket_id == world.ticket_ids["b"]
        # B 拿到后 C 还在排队（先到先得的中间态）。
        await _wait_until(lambda: service.waiters == 1)
        assert not task_c.done()

        await service.release(holder_b)
        row_c = await asyncio.wait_for(task_c, timeout=5)
        assert row_c.ticket_id == "ticket-c"
        await _wait_until(lambda: service.waiters == 0)

        await service.release(holder_c)
        assert await _all_rows(factory) == []

    asyncio.run(scenario())


def test_busy_after_wait_seconds(lease_env, lease_world) -> None:
    _factory, service, _events, _published = lease_env
    world = lease_world

    async def scenario() -> None:
        row_a = await service.acquire(world.holders["a"], purpose="click")
        with pytest.raises(AppError) as error:
            await service.acquire(world.holders["b"], purpose="click", wait_seconds=0)
        assert error.value.code == ErrorCode.DEVICE_LEASE_BUSY
        assert error.value.message == "设备正被其他工单使用，稍后再试"
        details = error.value.details or {}
        assert details["holder_ticket_id"] == world.ticket_ids["a"]
        assert details["holder_agent"] == "daily_ops"
        assert details["expires_at"] is not None
        assert details["waited_seconds"] == 0
        # 失败不改变持有者。
        current = await service.current()
        assert current is not None
        assert current.lease_id == row_a.lease_id

    asyncio.run(scenario())


def test_same_ticket_is_reentrant_and_renews(lease_env, lease_world) -> None:
    factory, service, events, _published = lease_env
    world = lease_world

    async def scenario() -> None:
        first = await service.acquire(world.holders["a"], purpose="click")
        assert len(events) == 1
        first_expiry = first.expires_at

        world.clock.advance(10)
        again = await service.acquire(
            world.holders["subagent_a"], purpose="navigate", wait_seconds=0
        )

        assert again.lease_id == first.lease_id
        assert again.expires_at == world.clock.now + timedelta(seconds=world.settings.ttl_seconds)
        assert again.expires_at > first_expiry
        # 隐式重入不换持有代理、不覆盖用途，也不广播。
        assert again.holder_agent == "daily_ops"
        assert again.purpose == "click"
        assert len(events) == 1
        rows = await _all_rows(factory)
        assert len(rows) == 1
        assert rows[0].lease_id == first.lease_id

    asyncio.run(scenario())


def test_explicit_survives_run_end_implicit_does_not(lease_env, lease_world) -> None:
    factory, service, events, _published = lease_env
    world = lease_world

    async def scenario() -> None:
        # 显式租约跨运行：根运行结束不释放。
        await service.acquire(world.holders["a"], purpose="hold", explicit=True)
        assert await service.release_run(world.run_ids["a"]) is False
        assert await _row(factory) is not None
        assert _payload(events)["change"] == "acquired"

        # 工单结束释放显式租约。
        assert await service.release_ticket(world.ticket_ids["a"]) is True
        assert await _row(factory) is None
        payload = _payload(events)
        assert payload["change"] == "released"
        assert payload["reason"] == "ticket_ended"

        # 隐式租约：根运行结束即释放。
        await service.acquire(world.holders["b"], purpose="click")
        assert await service.release_run(world.run_ids["b"], reason="run_ended") is True
        assert await _row(factory) is None
        payload = _payload(events)
        assert payload["change"] == "released"
        assert payload["reason"] == "run_ended"

    asyncio.run(scenario())


def test_release_requires_holder(lease_env, lease_world) -> None:
    factory, service, _events, _published = lease_env
    world = lease_world

    async def scenario() -> None:
        await service.acquire(world.holders["a"], purpose="click")
        assert await service.release(world.holders["b"]) is False
        assert await _row(factory) is not None
        assert await service.release(world.holders["a"]) is True
        assert await _all_rows(factory) == []

    asyncio.run(scenario())


def test_lease_expires_and_waiter_takes_over(lease_env, lease_world) -> None:
    factory, service, events, _published = lease_env
    world = lease_world

    async def scenario() -> None:
        row_a = await service.acquire(world.holders["a"], purpose="click")
        events.clear()

        waiter = asyncio.create_task(
            service.acquire(world.holders["b"], purpose="takeover", wait_seconds=5)
        )
        await _wait_until(lambda: service.waiters == 1)
        world.clock.advance(world.settings.ttl_seconds + 1)

        row_b = await asyncio.wait_for(waiter, timeout=5)
        assert row_b.ticket_id == world.ticket_ids["b"]
        changes = [entry[1]["payload"] for entry in events]
        assert [change["change"] for change in changes] == ["expired", "acquired"]
        assert changes[0]["reason"] == "expired"
        assert changes[0]["held"] is False
        assert changes[0]["lease"]["lease_id"] == row_a.lease_id
        assert changes[1]["lease"]["lease_id"] == row_b.lease_id

        # expire_due 删除到期行并广播 expired。
        world.clock.advance(world.settings.ttl_seconds + 1)
        events.clear()
        assert await service.expire_due() == 1
        assert await _all_rows(factory) == []
        payload = _payload(events)
        assert payload["change"] == "expired"
        assert payload["reason"] == "expired"
        assert payload["lease"]["lease_id"] == row_b.lease_id

    asyncio.run(scenario())


def test_orphaned_implicit_lease_released_by_scan(lease_env, lease_world) -> None:
    factory, service, events, _published = lease_env
    world = lease_world

    async def scenario() -> None:
        await service.acquire(world.holders["a"], purpose="click", explicit=False)
        await _set_run_status(factory, world.run_ids["a"], "succeeded")
        events.clear()

        assert await service.expire_due() == 1
        assert await _all_rows(factory) == []
        payload = _payload(events)
        assert payload["change"] == "released"
        assert payload["reason"] == "orphaned"

        # 显式租约跨运行，不按孤儿回收。
        await service.acquire(world.holders["b"], purpose="hold", explicit=True)
        await _set_run_status(factory, world.run_ids["b"], "succeeded")
        events.clear()
        assert await service.expire_due() == 0
        assert await _row(factory) is not None

    asyncio.run(scenario())


def test_preempt_marks_holder_once(lease_env, lease_world) -> None:
    factory, service, events, published = lease_env
    world = lease_world

    async def scenario() -> None:
        row = await service.acquire(world.holders["a"], purpose="click")

        preempted = await service.preempt("manual_pipeline", detail="用户提交了流水线")
        assert preempted is not None
        assert preempted.lease_id == row.lease_id
        assert await _all_rows(factory) == []

        payload = _payload(events)
        assert payload["change"] == "preempted"
        assert payload["reason"] == "manual_pipeline"
        assert payload["held"] is False
        assert payload["lease"]["lease_id"] == row.lease_id

        assert len(published) == 1
        item = published[0]
        assert item["thread_key"] == f"ticket:{world.ticket_ids['a']}"
        assert item["kind"] == InboxKind.SYSTEM
        assert item["ref_id"] == row.lease_id
        item_payload = item["payload"]
        assert item_payload["event"] == "lease_preempted"
        assert item_payload["reason"] == "manual_pipeline"
        assert item_payload["detail"] == "用户提交了流水线"
        assert "设备被用户接管：用户手动提交了流水线" in item_payload["text"]

        # 记号一次性：第一次取租约报 PREEMPTED，第二次正常拿到。
        with pytest.raises(AppError) as error:
            await service.acquire(world.holders["a"], purpose="click")
        assert error.value.code == ErrorCode.DEVICE_LEASE_PREEMPTED
        assert error.value.details == {"reason": "manual_pipeline"}
        assert (await _row(factory)) is None

        again = await service.acquire(world.holders["a"], purpose="click")
        assert again.ticket_id == world.ticket_ids["a"]

    asyncio.run(scenario())


def test_preempt_mark_cleared_at_run_end(lease_env, lease_world) -> None:
    factory, service, _events, _published = lease_env
    world = lease_world
    holder_a = world.holders["a"]

    async def scenario() -> None:
        # clear_preempted：运行结束时清掉记号。
        await service.acquire(holder_a, purpose="click")
        await service.preempt("manual_release")
        service.clear_preempted(holder_a.key)
        row = await service.acquire(holder_a, purpose="click")
        assert row.ticket_id == world.ticket_ids["a"]
        await service.release(holder_a)

        # on_ticket_ended 也会清记号（即使已经没有行可删）。
        await service.acquire(holder_a, purpose="click")
        await service.preempt("manual_release")
        await service.on_ticket_ended(
            TicketEnded(
                ticket_id=world.ticket_ids["a"],
                status=TicketStatus.DONE,
                worker_session_id=world.worker_sessions["a"],
            )
        )
        assert await _row(factory) is None
        assert await service.acquire(holder_a, purpose="click") is not None

    asyncio.run(scenario())


def test_ticket_end_releases_lease(lease_env, lease_world) -> None:
    factory, service, events, _published = lease_env
    world = lease_world

    async def scenario() -> None:
        await service.acquire(world.holders["a"], purpose="click")
        await service.on_ticket_ended(
            TicketEnded(
                ticket_id=world.ticket_ids["a"],
                status=TicketStatus.CANCELLED,
                worker_session_id=world.worker_sessions["a"],
            )
        )
        assert await _all_rows(factory) == []
        payload = _payload(events)
        assert payload["change"] == "released"
        assert payload["reason"] == "ticket_ended"
        assert world.wake_calls

        # 显式租约同样随工单结束释放。
        await service.acquire(world.holders["b"], purpose="hold", explicit=True)
        await service.on_ticket_ended(
            TicketEnded(
                ticket_id=world.ticket_ids["b"],
                status=TicketStatus.DONE,
                worker_session_id=world.worker_sessions["b"],
            )
        )
        assert await _all_rows(factory) == []
        assert _payload(events)["reason"] == "ticket_ended"

    asyncio.run(scenario())


def test_claim_gate_lists_ticket_sessions(lease_env, lease_world) -> None:
    _factory, service, _events, _published = lease_env
    world = lease_world

    async def scenario() -> None:
        assert await service.claim_gate() is None

        row = await service.acquire(world.holders["a"], purpose="click")
        gate = await service.claim_gate()
        assert gate is not None
        assert gate.lease_id == row.lease_id
        # Worker 与子代理会话都在；别的工单的会话不在。
        assert gate.allowed_session_ids == frozenset(
            {world.worker_sessions["a"], world.subagent_sessions["a"]}
        )

        # 没有工单的租约：只允许根运行所属的那个会话。
        await service.release(world.holders["a"])
        run_holder = LeaseHolder(
            ticket_id=None, run_id=world.run_ids["b"], agent="daily_ops"
        )
        run_row = await service.acquire(run_holder, purpose="background")
        gate = await service.claim_gate()
        assert gate is not None
        assert gate.lease_id == run_row.lease_id
        assert gate.allowed_session_ids == frozenset({world.worker_sessions["b"]})

        # 过期的行不算有效租约。
        world.clock.advance(world.settings.ttl_seconds + 1)
        assert await service.claim_gate() is None

    asyncio.run(scenario())


def test_acquire_busy_while_recording(lease_env, lease_world) -> None:
    """M89 §4.5：录制期间取租约直接 409 ``DEVICE_LEASE_BUSY``，不插行。"""
    factory, service, _events, _published = lease_env
    world = lease_world

    async def scenario() -> None:
        assert service.recording() is False
        await service.set_recording(True)
        try:
            assert service.recording() is True
            with pytest.raises(AppError) as caught:
                await service.acquire(
                    world.holders["a"], purpose="click", wait_seconds=0
                )
            assert caught.value.code == ErrorCode.DEVICE_LEASE_BUSY
            assert caught.value.message == "用户正在网页上示教，设备暂不可用"
            assert await _all_rows(factory) == []
        finally:
            await service.set_recording(False)
        assert service.recording() is False

    asyncio.run(scenario())


def test_claim_gate_blocks_manual_while_recording(lease_env, lease_world) -> None:
    """M89 §4.5：录制期间领取闸门是空的、且不允许手动提交的流水线。"""
    _factory, service, _events, _published = lease_env
    world = lease_world

    async def scenario() -> None:
        await service.acquire(world.holders["a"], purpose="click")
        await service.set_recording(True)
        try:
            gate = await service.claim_gate()
            assert gate is not None
            assert gate.lease_id == "demonstration"
            assert gate.allowed_session_ids == frozenset()
            assert gate.allow_manual is False
        finally:
            await service.set_recording(False)

    asyncio.run(scenario())


def test_recording_gate_releases_manual_after_stop(lease_env, lease_world) -> None:
    """M89 §4.5：``set_recording(False)`` 后回到租约闸门，手动提交恢复可领。"""
    _factory, service, _events, _published = lease_env
    world = lease_world

    async def scenario() -> None:
        row = await service.acquire(world.holders["a"], purpose="click")
        await service.set_recording(True)
        assert (await service.claim_gate()).allow_manual is False

        await service.set_recording(False)
        gate = await service.claim_gate()
        assert gate is not None
        assert gate.lease_id == row.lease_id
        assert gate.allow_manual is True
        assert gate.allowed_session_ids == frozenset(
            {world.worker_sessions["a"], world.subagent_sessions["a"]}
        )

        # 没有租约时仍返回 None（旧行为不变）。
        await service.release(world.holders["a"])
        assert await service.claim_gate() is None

    asyncio.run(scenario())


def test_set_recording_false_notifies_released_once(lease_env, lease_world) -> None:
    """M89 §4.5/§7：录制闸门从 ``True`` 翻到 ``False`` 时唤醒队列消费者一次。

    录制期间入队的手动流水线靠这条通知出队；开闸（``False → True``）不是释放，
    状态没变化的重复调用也不重复通知。
    """
    _factory, service, _events, _published = lease_env
    world = lease_world

    async def scenario() -> None:
        await service.set_recording(True)
        # 开闸不是「设备空出来了」：不唤醒 runner，避免空转。
        assert world.wake_calls == []
        await service.set_recording(True)
        assert world.wake_calls == []

        await service.set_recording(False)
        assert world.wake_calls == ["released"]
        # 重复关闭不重复通知。
        await service.set_recording(False)
        assert world.wake_calls == ["released"]

    asyncio.run(scenario())
