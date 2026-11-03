"""``RunService`` 的用例（M66，docs/v3/specs/m66-durable-runs §4.2.2、§6）。

内存库 + ``StaticPool``：一个用例一个库，同步函数里 ``asyncio.run``。时钟与
``sleep`` 都从构造参数注入——``FakeClock`` 拨表，``_SteppedSleep`` 让心跳循环
一步一停，避免用例里出现真实等待，也避免心跳写库和用例的写库在同一时刻抢
同一条 SQLite 连接。
"""

from __future__ import annotations

import asyncio
from collections.abc import Awaitable, Callable
from datetime import datetime, timedelta
from typing import Any

import pytest
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncEngine, AsyncSession, async_sessionmaker
from sqlalchemy.pool import StaticPool
from sqlmodel import SQLModel

import maa_api.db.models  # noqa: F401 - 建表需要全部模型进 metadata
from maa_api.agent.runs import RunNode, RunService
from maa_api.agent.runs.recovery import (
    RECOVERY_FINISH_REASON,
    RECOVERY_NOTICE,
)
from maa_api.db.models import AgentMessage, AgentRun, AgentSession, new_uuid, utcnow
from maa_api.db.repositories.agent import AgentMessageRepository, AgentRunRepository
from maa_api.db.session import make_engine
from maa_api.domain.enums import AgentRunKind, AgentRunStatus
from maa_api.domain.errors import AppError, ErrorCode
from maa_api.settings import AgentSettings, Settings

BOOT_ID = "boot-current"
PREVIOUS_BOOT = "boot-previous"


class FakeClock:
    """可拨的时钟；``RunService`` 与它内部的 ``RunRecovery`` 共用同一个。"""

    def __init__(self, now: datetime | None = None) -> None:
        self._now = now or datetime(2026, 11, 3, 12, 0, 0)

    def __call__(self) -> datetime:
        return self._now

    def advance(self, seconds: float) -> None:
        self._now += timedelta(seconds=seconds)


class _SteppedSleep:
    """假 ``sleep``：每次进入先计数，再等测试放行一步。"""

    def __init__(self) -> None:
        self.calls = 0
        self._gate = asyncio.Event()

    async def __call__(self, _seconds: float) -> None:
        self.calls += 1
        await self._gate.wait()
        self._gate.clear()

    def release(self) -> None:
        self._gate.set()


class _BlockingSleep:
    """假 ``sleep``：一直等到被取消（``close`` 用它验证取消路径）。"""

    def __init__(self) -> None:
        self.calls = 0

    async def __call__(self, _seconds: float) -> None:
        self.calls += 1
        await asyncio.Event().wait()


def _settings() -> Settings:
    # 与生产默认值同量级，但固定下来：用例不读 config.yaml。
    return Settings(
        agent=AgentSettings(run_heartbeat_seconds=5, run_stale_seconds=30)
    )


async def _prepare() -> tuple[AsyncEngine, async_sessionmaker[AsyncSession]]:
    engine = make_engine("sqlite+aiosqlite:///:memory:", poolclass=StaticPool)
    factory = async_sessionmaker(engine, expire_on_commit=False, class_=AsyncSession)
    async with engine.begin() as connection:
        await connection.run_sync(SQLModel.metadata.create_all)
    return engine, factory


def _service(
    factory: async_sessionmaker[AsyncSession],
    clock: FakeClock,
    *,
    boot_id: str = BOOT_ID,
    sleep: Callable[[float], Awaitable[Any]] = asyncio.sleep,
) -> RunService:
    return RunService(
        factory,
        boot_id=boot_id,
        settings_provider=_settings,
        clock=clock,
        sleep=sleep,
    )


async def _seed_session(
    factory: async_sessionmaker[AsyncSession], session_id: str
) -> None:
    async with factory() as session:
        session.add(AgentSession(id=session_id, model="test-model"))
        await session.commit()


async def _insert_run(
    factory: async_sessionmaker[AsyncSession],
    *,
    boot_id: str,
    session_id: str | None = None,
    status: str = AgentRunStatus.RUNNING,
    started_at: datetime | None = None,
    heartbeat_at: datetime | None = None,
) -> AgentRun:
    """直接插一条运行（模拟别的进程留下的行），不经过 ``RunService``。"""
    run_id = new_uuid()
    stamp = started_at or utcnow()
    async with factory() as session:
        await AgentRunRepository(session).create(
            AgentRun(
                id=run_id,
                session_id=session_id,
                root_run_id=run_id,
                agent_name="legacy_chat",
                kind=AgentRunKind.LEGACY_TURN,
                status=status,
                boot_id=boot_id,
                started_at=stamp,
                heartbeat_at=stamp if heartbeat_at is None else heartbeat_at,
            )
        )
        await session.commit()
    return await _load_run(factory, run_id)


async def _load_run(
    factory: async_sessionmaker[AsyncSession], run_id: str
) -> AgentRun:
    async with factory() as session:
        stored = await session.get(AgentRun, run_id, populate_existing=True)
    assert stored is not None
    return stored


async def _load_session(
    factory: async_sessionmaker[AsyncSession], session_id: str
) -> AgentSession:
    async with factory() as session:
        stored = await session.get(
            AgentSession, session_id, populate_existing=True
        )
    assert stored is not None
    return stored


async def _messages(
    factory: async_sessionmaker[AsyncSession], session_id: str
) -> list[AgentMessage]:
    async with factory() as session:
        result = await session.execute(
            select(AgentMessage)
            .where(AgentMessage.session_id == session_id)
            .order_by(AgentMessage.seq.asc())
        )
        return list(result.scalars().all())


async def _count_runs(
    factory: async_sessionmaker[AsyncSession], session_id: str | None = None
) -> int:
    async with factory() as session:
        stmt = select(func.count()).select_from(AgentRun)
        if session_id is not None:
            stmt = stmt.where(AgentRun.session_id == session_id)
        return int((await session.execute(stmt)).scalar_one())


async def _wait_until(
    predicate: Callable[[], bool], *, attempts: int = 2000
) -> None:
    for _ in range(attempts):
        if predicate():
            return
        await asyncio.sleep(0)
    raise AssertionError("等待的条件没有成立")


def _flatten(node: RunNode) -> list[RunNode]:
    result = [node]
    for child in node.children:
        result.extend(_flatten(child))
    return result


def test_start_creates_running_root_run_with_heartbeat() -> None:
    async def scenario() -> None:
        engine, factory = await _prepare()
        clock = FakeClock()
        service = _service(factory, clock)
        try:
            run = await service.start(
                agent_name="legacy_chat", kind=AgentRunKind.LEGACY_TURN
            )
            assert run.status == AgentRunStatus.RUNNING
            assert run.root_run_id == run.id
            assert run.boot_id == BOOT_ID
            assert run.started_at == clock()
            assert run.heartbeat_at == clock()
            assert service.is_live(run.id) is True
            stored = await _load_run(factory, run.id)
            assert stored.status == "running"
            assert stored.heartbeat_at == clock()
        finally:
            await service.close()
            await engine.dispose()

    asyncio.run(scenario())


def test_start_child_run_inherits_root() -> None:
    async def scenario() -> None:
        engine, factory = await _prepare()
        clock = FakeClock()
        service = _service(factory, clock)
        try:
            root = await service.start(
                agent_name="orchestrator", kind=AgentRunKind.ORCHESTRATOR_TURN
            )
            child = await service.start(
                agent_name="worker",
                kind=AgentRunKind.WORKER_TURN,
                parent_run_id=root.id,
            )
            grandchild = await service.start(
                agent_name="sub", kind=AgentRunKind.SUBAGENT, parent_run_id=child.id
            )
            assert child.parent_run_id == root.id
            assert grandchild.parent_run_id == child.id
            assert child.root_run_id == root.id
            assert grandchild.root_run_id == root.id
        finally:
            await service.close()
            await engine.dispose()

    asyncio.run(scenario())


def test_start_with_unknown_parent_raises_run_not_found() -> None:
    async def scenario() -> None:
        engine, factory = await _prepare()
        service = _service(factory, FakeClock())
        try:
            with pytest.raises(AppError) as excinfo:
                await service.start(
                    agent_name="worker",
                    kind=AgentRunKind.WORKER_TURN,
                    parent_run_id="missing-parent",
                )
            assert excinfo.value.code == ErrorCode.RUN_NOT_FOUND
            assert excinfo.value.details == {"run_id": "missing-parent"}
            assert await _count_runs(factory) == 0
        finally:
            await service.close()
            await engine.dispose()

    asyncio.run(scenario())


def test_start_rejects_second_active_run_on_same_session() -> None:
    async def scenario() -> None:
        engine, factory = await _prepare()
        await _seed_session(factory, "session-1")
        service = _service(factory, FakeClock())
        try:
            first = await service.start(
                agent_name="legacy_chat",
                kind=AgentRunKind.LEGACY_TURN,
                session_id="session-1",
            )
            with pytest.raises(AppError) as excinfo:
                await service.start(
                    agent_name="legacy_chat",
                    kind=AgentRunKind.LEGACY_TURN,
                    session_id="session-1",
                )
            assert excinfo.value.code == ErrorCode.RUN_ALREADY_ACTIVE
            assert excinfo.value.details == {
                "session_id": "session-1",
                "run_id": first.id,
            }
            assert await _count_runs(factory, "session-1") == 1
            assert service.is_live(first.id) is True
        finally:
            await service.close()
            await engine.dispose()

    asyncio.run(scenario())


def test_start_allows_new_run_after_finish() -> None:
    async def scenario() -> None:
        engine, factory = await _prepare()
        await _seed_session(factory, "session-1")
        service = _service(factory, FakeClock())
        try:
            first = await service.start(
                agent_name="legacy_chat",
                kind=AgentRunKind.LEGACY_TURN,
                session_id="session-1",
            )
            await service.finish(
                first.id, status=AgentRunStatus.SUCCEEDED, finish_reason="stop"
            )
            second = await service.start(
                agent_name="legacy_chat",
                kind=AgentRunKind.LEGACY_TURN,
                session_id="session-1",
            )
            assert second.id != first.id
            assert service.is_live(second.id) is True
            assert await _count_runs(factory, "session-1") == 2
        finally:
            await service.close()
            await engine.dispose()

    asyncio.run(scenario())


def test_start_takes_over_stale_run_from_previous_boot() -> None:
    async def scenario() -> None:
        engine, factory = await _prepare()
        await _seed_session(factory, "session-1")
        clock = FakeClock()
        previous = _service(factory, clock, boot_id=PREVIOUS_BOOT)
        try:
            old_run = await previous.start(
                agent_name="legacy_chat",
                kind=AgentRunKind.LEGACY_TURN,
                session_id="session-1",
            )
        finally:
            await previous.close()
        service = _service(factory, clock)
        try:
            clock.advance(service.stale_after_seconds() + 1)
            run = await service.start(
                agent_name="legacy_chat",
                kind=AgentRunKind.LEGACY_TURN,
                session_id="session-1",
            )
            assert run.id != old_run.id
            recovered = await _load_run(factory, old_run.id)
            assert recovered.status == AgentRunStatus.INTERRUPTED
            assert recovered.finish_reason == RECOVERY_FINISH_REASON
            assert recovered.finished_at is not None
            messages = await _messages(factory, "session-1")
            assert [message.role for message in messages] == ["system"]
            assert messages[0].content == RECOVERY_NOTICE
            assert messages[0].run_id == old_run.id
            assert (await _load_session(factory, "session-1")).message_count == 1
        finally:
            await service.close()
            await engine.dispose()

    asyncio.run(scenario())


def test_start_keeps_fresh_run_from_another_live_boot() -> None:
    async def scenario() -> None:
        engine, factory = await _prepare()
        await _seed_session(factory, "session-1")
        clock = FakeClock()
        service = _service(factory, clock)
        try:
            old_run = await _insert_run(
                factory,
                boot_id=PREVIOUS_BOOT,
                session_id="session-1",
                heartbeat_at=clock(),
                started_at=clock(),
            )
            clock.advance(service.stale_after_seconds() - 1)
            with pytest.raises(AppError) as excinfo:
                await service.start(
                    agent_name="legacy_chat",
                    kind=AgentRunKind.LEGACY_TURN,
                    session_id="session-1",
                )
            assert excinfo.value.code == ErrorCode.RUN_ALREADY_ACTIVE
            assert excinfo.value.details == {
                "session_id": "session-1",
                "run_id": old_run.id,
            }
            kept = await _load_run(factory, old_run.id)
            assert kept.status == AgentRunStatus.RUNNING
            assert kept.finish_reason is None
            assert await _messages(factory, "session-1") == []
        finally:
            await service.close()
            await engine.dispose()

    asyncio.run(scenario())


def test_start_reaps_orphan_run_of_this_boot() -> None:
    async def scenario() -> None:
        engine, factory = await _prepare()
        await _seed_session(factory, "session-1")
        service = _service(factory, FakeClock())
        try:
            orphan = await service.start(
                agent_name="legacy_chat",
                kind=AgentRunKind.LEGACY_TURN,
                session_id="session-1",
            )
            # 模拟 finish 写库失败后被移出跟踪：行还在活动态，但本进程不再跟踪它。
            del service._live[orphan.id]
            run = await service.start(
                agent_name="legacy_chat",
                kind=AgentRunKind.LEGACY_TURN,
                session_id="session-1",
            )
            assert run.id != orphan.id
            reaped = await _load_run(factory, orphan.id)
            assert reaped.status == AgentRunStatus.FAILED
            assert reaped.finish_reason == "error"
            assert reaped.error_code == str(ErrorCode.INTERNAL_ERROR)
        finally:
            await service.close()
            await engine.dispose()

    asyncio.run(scenario())


def test_finish_is_idempotent_and_aggregates_message_usage() -> None:
    async def scenario() -> None:
        engine, factory = await _prepare()
        await _seed_session(factory, "session-1")
        service = _service(factory, FakeClock())
        try:
            run = await service.start(
                agent_name="legacy_chat",
                kind=AgentRunKind.LEGACY_TURN,
                session_id="session-1",
            )
            async with factory() as session:
                messages = AgentMessageRepository(session)
                for seq, (latency, prompt, completion) in enumerate(
                    [(120, 7, 2), (80, 5, 1), (None, None, None)]
                ):
                    await messages.create(
                        AgentMessage(
                            session_id="session-1",
                            seq=seq,
                            role="assistant",
                            content="回复",
                            latency_ms=latency,
                            prompt_tokens=prompt,
                            completion_tokens=completion,
                            run_id=run.id,
                        )
                    )
                await session.commit()
            finished = await service.finish(
                run.id, status=AgentRunStatus.SUCCEEDED, finish_reason="stop"
            )
            assert finished.prompt_tokens == 12
            assert finished.completion_tokens == 3
            assert finished.steps == 2
            assert finished.model == "test-model"
            again = await service.finish(
                run.id,
                status=AgentRunStatus.FAILED,
                finish_reason="error",
                error_code="INTERNAL_ERROR",
            )
            assert again.status == AgentRunStatus.SUCCEEDED
            stored = await _load_run(factory, run.id)
            assert stored.status == AgentRunStatus.SUCCEEDED
            assert stored.finish_reason == "stop"
            assert stored.error_code is None
            assert (
                stored.prompt_tokens,
                stored.completion_tokens,
                stored.steps,
            ) == (12, 3, 2)
        finally:
            await service.close()
            await engine.dispose()

    asyncio.run(scenario())


def test_finish_rejects_non_terminal_status() -> None:
    async def scenario() -> None:
        engine, factory = await _prepare()
        service = _service(factory, FakeClock())
        try:
            run = await service.start(
                agent_name="legacy_chat", kind=AgentRunKind.LEGACY_TURN
            )
            with pytest.raises(ValueError):
                await service.finish(
                    run.id, status=AgentRunStatus.RUNNING, finish_reason="stop"
                )
            assert (await _load_run(factory, run.id)).status == "running"
        finally:
            await service.close()
            await engine.dispose()

    asyncio.run(scenario())


def test_finish_redacts_and_truncates_error_message() -> None:
    async def scenario() -> None:
        engine, factory = await _prepare()
        service = _service(factory, FakeClock())
        try:
            run = await service.start(
                agent_name="legacy_chat", kind=AgentRunKind.LEGACY_TURN
            )
            secret = "sk-live-abcdef1234567890"
            message = f"调用失败：{secret} " + "错误说明" * 1000
            finished = await service.finish(
                run.id,
                status=AgentRunStatus.FAILED,
                finish_reason="turn_error",
                error_code=str(ErrorCode.LLM_TIMEOUT),
                error_message=message,
            )
            assert secret not in finished.error_message
            assert "sk-" not in finished.error_message
            assert len(finished.error_message) <= 2000
            assert finished.error_message.startswith("调用失败：[REDACTED]")
            assert (await _load_run(factory, run.id)).error_message == (
                finished.error_message
            )
        finally:
            await service.close()
            await engine.dispose()

    asyncio.run(scenario())


def test_set_status_switches_between_running_and_waiting_human() -> None:
    async def scenario() -> None:
        engine, factory = await _prepare()
        await _seed_session(factory, "session-1")
        service = _service(factory, FakeClock())
        try:
            run = await service.start(
                agent_name="legacy_chat",
                kind=AgentRunKind.LEGACY_TURN,
                session_id="session-1",
            )
            assert (
                await service.set_status(run.id, AgentRunStatus.WAITING_HUMAN)
                is True
            )
            assert (await _load_run(factory, run.id)).status == "waiting_human"
            assert await service.set_status(run.id, AgentRunStatus.RUNNING) is True
            assert (await _load_run(factory, run.id)).status == "running"
            await service.finish(
                run.id, status=AgentRunStatus.SUCCEEDED, finish_reason="stop"
            )
            assert (
                await service.set_status(run.id, AgentRunStatus.WAITING_HUMAN)
                is False
            )
            assert (await _load_run(factory, run.id)).status == "succeeded"
        finally:
            await service.close()
            await engine.dispose()

    asyncio.run(scenario())


def test_heartbeat_refreshes_live_runs_only() -> None:
    async def scenario() -> None:
        engine, factory = await _prepare()
        await _seed_session(factory, "session-1")
        await _seed_session(factory, "session-2")
        clock = FakeClock()
        service = _service(factory, clock)
        try:
            live = await service.start(
                agent_name="legacy_chat",
                kind=AgentRunKind.LEGACY_TURN,
                session_id="session-1",
            )
            finished_run = await service.start(
                agent_name="legacy_chat",
                kind=AgentRunKind.LEGACY_TURN,
                session_id="session-2",
            )
            await service.finish(
                finished_run.id,
                status=AgentRunStatus.SUCCEEDED,
                finish_reason="stop",
            )
            finished_heartbeat = clock()
            clock.advance(7)
            assert await service.heartbeat() == 1
            assert (await _load_run(factory, live.id)).heartbeat_at == clock()
            assert (
                await _load_run(factory, finished_run.id)
            ).heartbeat_at == finished_heartbeat
        finally:
            await service.close()
            await engine.dispose()

    asyncio.run(scenario())


def test_heartbeat_loop_runs_while_live_and_stops_after_last_finish() -> None:
    async def scenario() -> None:
        engine, factory = await _prepare()
        clock = FakeClock()
        sleeper = _SteppedSleep()
        service = _service(factory, clock, sleep=sleeper)
        try:
            run = await service.start(
                agent_name="legacy_chat", kind=AgentRunKind.LEGACY_TURN
            )
            await _wait_until(lambda: sleeper.calls == 1)
            first_task = service._heartbeat_task
            assert first_task is not None
            assert first_task.done() is False

            sleeper.release()
            await _wait_until(lambda: sleeper.calls == 2)  # 第一次心跳已落库
            clock.advance(7)
            sleeper.release()
            await _wait_until(lambda: sleeper.calls == 3)  # 第二次心跳已落库
            assert (await _load_run(factory, run.id)).heartbeat_at == clock()

            await service.finish(
                run.id, status=AgentRunStatus.SUCCEEDED, finish_reason="stop"
            )
            sleeper.release()
            await _wait_until(first_task.done)
            assert service._heartbeat_task is None
            assert service.is_live(run.id) is False

            restarted = await service.start(
                agent_name="legacy_chat", kind=AgentRunKind.LEGACY_TURN
            )
            second_task = service._heartbeat_task
            assert second_task is not None
            assert second_task is not first_task
            assert second_task.done() is False
            await _wait_until(lambda: sleeper.calls == 4)
            await service.finish(
                restarted.id,
                status=AgentRunStatus.SUCCEEDED,
                finish_reason="stop",
            )
        finally:
            await service.close()
            await engine.dispose()

    asyncio.run(scenario())


def test_get_tree_returns_children_up_to_depth_three() -> None:
    async def scenario() -> None:
        engine, factory = await _prepare()
        clock = FakeClock()
        service = _service(factory, clock)
        try:
            a = await service.start(
                agent_name="orchestrator", kind=AgentRunKind.ORCHESTRATOR_TURN
            )
            clock.advance(1)
            b = await service.start(
                agent_name="worker",
                kind=AgentRunKind.WORKER_TURN,
                parent_run_id=a.id,
            )
            clock.advance(1)
            sibling = await service.start(
                agent_name="worker-2",
                kind=AgentRunKind.WORKER_TURN,
                parent_run_id=a.id,
            )
            clock.advance(1)
            c = await service.start(
                agent_name="sub-c", kind=AgentRunKind.SUBAGENT, parent_run_id=b.id
            )
            clock.advance(1)
            d = await service.start(
                agent_name="sub-d", kind=AgentRunKind.SUBAGENT, parent_run_id=c.id
            )
            clock.advance(1)
            e = await service.start(
                agent_name="sub-e", kind=AgentRunKind.SUBAGENT, parent_run_id=d.id
            )
            tree = await service.get_tree(a.id)
            assert tree.run.id == a.id
            assert [node.run.id for node in tree.children] == [b.id, sibling.id]
            assert [node.run.id for node in tree.children[0].children] == [c.id]
            d_node = tree.children[0].children[0].children[0]
            assert d_node.run.id == d.id
            assert d_node.children == []
            assert e.id not in {node.run.id for node in _flatten(tree)}
        finally:
            await service.close()
            await engine.dispose()

    asyncio.run(scenario())


def test_get_tree_unknown_run_raises_run_not_found() -> None:
    async def scenario() -> None:
        engine, factory = await _prepare()
        service = _service(factory, FakeClock())
        try:
            with pytest.raises(AppError) as excinfo:
                await service.get_tree("missing")
            assert excinfo.value.code == ErrorCode.RUN_NOT_FOUND
            assert excinfo.value.details == {"run_id": "missing"}
        finally:
            await service.close()
            await engine.dispose()

    asyncio.run(scenario())


def test_list_runs_filters_by_session_and_root_only() -> None:
    async def scenario() -> None:
        engine, factory = await _prepare()
        await _seed_session(factory, "session-1")
        await _seed_session(factory, "session-2")
        clock = FakeClock()
        service = _service(factory, clock)
        try:
            root_one = await service.start(
                agent_name="legacy_chat",
                kind=AgentRunKind.LEGACY_TURN,
                session_id="session-1",
            )
            clock.advance(1)
            root_two = await service.start(
                agent_name="legacy_chat",
                kind=AgentRunKind.LEGACY_TURN,
                session_id="session-2",
            )
            # 同一会话至多一条活动运行：子运行属于 session-1，先结束它的根。
            await service.finish(
                root_one.id,
                status=AgentRunStatus.SUCCEEDED,
                finish_reason="stop",
            )
            clock.advance(1)
            child = await service.start(
                agent_name="sub",
                kind=AgentRunKind.SUBAGENT,
                session_id="session-1",
                parent_run_id=root_one.id,
            )
            page = await service.list_runs(session_id="session-1")
            assert page.total == 1
            assert [run.id for run in page.items] == [root_one.id]
            assert (page.page, page.size) == (1, 20)

            page = await service.list_runs(
                session_id="session-1", root_only=False
            )
            assert page.total == 2
            assert [run.id for run in page.items] == [child.id, root_one.id]

            page = await service.list_runs(root_only=False)
            assert page.total == 3
            assert [run.id for run in page.items] == [
                child.id,
                root_two.id,
                root_one.id,
            ]

            page = await service.list_runs(session_id="session-2")
            assert page.total == 1
            assert [run.id for run in page.items] == [root_two.id]
        finally:
            await service.close()
            await engine.dispose()

    asyncio.run(scenario())


def test_close_cancels_heartbeat_loop() -> None:
    async def scenario() -> None:
        engine, factory = await _prepare()
        sleeper = _BlockingSleep()
        service = _service(factory, FakeClock(), sleep=sleeper)
        try:
            run = await service.start(
                agent_name="legacy_chat", kind=AgentRunKind.LEGACY_TURN
            )
            await _wait_until(lambda: sleeper.calls == 1)
            task = service._heartbeat_task
            assert task is not None
            assert task.done() is False
            await service.close()
            assert task.done() is True
            assert task.cancelled() is True
            assert service._heartbeat_task is None
            assert service.is_live(run.id) is False
            assert (await _load_run(factory, run.id)).status == "running"
        finally:
            await service.close()
            await engine.dispose()

    asyncio.run(scenario())
