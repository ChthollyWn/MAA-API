"""``PipelineRepository`` / ``TaskRepository`` 的仓储行为测试（M2-07）。

覆盖 docs/04 §9 的硬约束：

- 仓储不 commit：``create`` 只 flush，回滚后 pipeline 与 task 一起消失；
- ``claim_next`` 是条件更新：候选被并发取走时 ``rowcount == 0`` 返回 ``None``；
- ``mark_terminal`` / ``set_priority`` 用 ``bool`` 表示状态机是否接受；
- ``purge_before`` 先按天删、再按条数上限删，且 task 不留孤儿；
- 分页统一 ``Page(items/total/page/size)``，过滤与排序照 docs/04 §6 的索引形态。

用例形态：同步测试函数 + ``asyncio.run(scenario())``（本仓无 pytest-asyncio），
临时库与会话来自 ``tests/db/conftest.py``。
"""

import asyncio
from datetime import timedelta

from sqlalchemy import text
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine
from sqlalchemy.pool import NullPool
from sqlmodel import SQLModel

import maa_api.db.models  # noqa: F401
from maa_api.db.models import Pipeline, Schedule, Task, utcnow
from maa_api.db.repositories.pipeline import PipelineRepository, TaskRepository
from maa_api.domain.enums import PipelineSource, PipelineStatus, Priority, TaskStatus


def _pipeline(**overrides) -> Pipeline:
    fields = {
        "source": PipelineSource.MANUAL,
        "priority": Priority.MANUAL,
        "task_count": 1,
        "title": "日常",
    }
    fields.update(overrides)
    return Pipeline(**fields)


def _task(pipeline_id: str, order_index: int = 0) -> Task:
    return Task(
        pipeline_id=pipeline_id,
        order_index=order_index,
        type_name="Award",
        task_name="领取奖励",
        params={},
    )


async def _count(session, table: str, where: str = "", params: dict | None = None) -> int:
    sql = f"select count(*) from {table}" + (f" where {where}" if where else "")
    return (await session.execute(text(sql), params or {})).scalar_one()


def test_create_get_and_idempotency_key(db_session_factory):
    """写入即可读；``get_by_idempotency_key`` 只按真实 key 命中，空 key 返回 None。"""

    async def scenario():
        async with db_session_factory() as session:
            repo = PipelineRepository(session)
            pipeline = _pipeline(idempotency_key="k-1")
            await repo.create(pipeline, [_task(pipeline.id)])
            await session.commit()

            assert pipeline.id and len(pipeline.id) == 36
            got = await repo.get(pipeline.id)
            assert got is not None and got.title == "日常"
            assert got.status == PipelineStatus.PENDING
            assert await repo.get("no-such-id") is None

            assert (await repo.get_by_idempotency_key("k-1")).id == pipeline.id
            assert await repo.get_by_idempotency_key("k-2") is None
            assert await repo.get_by_idempotency_key("") is None

    asyncio.run(scenario())


def test_create_without_commit_is_atomic(db_session_factory):
    """仓储不 commit：回滚后 pipeline 与全部 task 一起消失（同一事务）。"""

    async def scenario():
        async with db_session_factory() as session:
            repo = PipelineRepository(session)
            pipeline = _pipeline(task_count=2)
            await repo.create(pipeline, [_task(pipeline.id, 0), _task(pipeline.id, 1)])
            # 未 commit 前，本会话内已可见（flush 过），且 task 挂上了外键
            assert await _count(session, "task", "pipeline_id = :p", {"p": pipeline.id}) == 2
            await session.rollback()

        async with db_session_factory() as session:
            assert await _count(session, "pipeline") == 0
            assert await _count(session, "task") == 0

    asyncio.run(scenario())


def test_get_with_tasks_and_task_order(db_session_factory):
    """``with_tasks=True`` 挂出按 ``order_index`` 升序的任务列表。"""

    async def scenario():
        async with db_session_factory() as session:
            repo = PipelineRepository(session)
            pipeline = _pipeline(task_count=3)
            await repo.create(
                pipeline,
                [_task(pipeline.id, 2), _task(pipeline.id, 0), _task(pipeline.id, 1)],
            )
            await session.commit()

            trepo = TaskRepository(session)
            assert [t.order_index for t in await trepo.list_by_pipeline(pipeline.id)] == [0, 1, 2]

            plain = await repo.get(pipeline.id)
            assert not hasattr(plain, "tasks")

            detail = await repo.get(pipeline.id, with_tasks=True)
            assert [t.order_index for t in detail.tasks] == [0, 1, 2]
            # 非映射属性不污染模型字段
            assert "tasks" not in type(detail).model_fields

    asyncio.run(scenario())


def test_list_filters_and_pagination(db_session_factory):
    """列表默认按 ``created_at DESC``，四个过滤条件与分页边界都生效。"""

    async def scenario():
        async with db_session_factory() as session:
            repo = PipelineRepository(session)
            schedule = Schedule(name="每日常规", cron="0 7 * * *", template=[])
            session.add(schedule)
            await session.flush()

            now = utcnow()
            manual = _pipeline(title="manual", created_at=now - timedelta(hours=3))
            scheduled = _pipeline(
                title="scheduled",
                source=PipelineSource.SCHEDULED,
                priority=Priority.SCHEDULED,
                schedule_id=schedule.id,
                created_at=now - timedelta(hours=2),
            )
            agent = _pipeline(
                title="agent",
                source=PipelineSource.AGENT,
                priority=Priority.AGENT,
                status=PipelineStatus.COMPLETED,
                created_at=now - timedelta(hours=1),
            )
            for item in (manual, scheduled, agent):
                await repo.create(item, [_task(item.id)])
            await session.commit()

            all_page = await repo.list(page=1, size=10)
            assert (all_page.total, len(all_page.items), all_page.page, all_page.size) == (3, 3, 1, 10)
            assert [p.title for p in all_page.items] == ["agent", "scheduled", "manual"]

            page2 = await repo.list(page=2, size=2)
            assert (page2.total, len(page2.items), page2.page, page2.size) == (3, 1, 2, 2)
            assert [p.title for p in page2.items] == ["manual"]

            by_status = await repo.list(status=PipelineStatus.COMPLETED)
            assert [p.title for p in by_status.items] == ["agent"]
            by_source = await repo.list(source=PipelineSource.SCHEDULED)
            assert [p.title for p in by_source.items] == ["scheduled"]
            by_schedule = await repo.list(schedule_id=schedule.id)
            assert [p.title for p in by_schedule.items] == ["scheduled"]
            since = await repo.list(since=now - timedelta(hours=2, minutes=30))
            assert [p.title for p in since.items] == ["agent", "scheduled"]
            assert (await repo.list(status=PipelineStatus.FAILED)).total == 0

            # 越界分页参数被夹回合法区间，不生成负 offset
            clamped = await repo.list(page=0, size=0)
            assert (clamped.page, clamped.size, len(clamped.items)) == (1, 1, 1)

    asyncio.run(scenario())


def test_claim_next_priority_then_fifo(db_session_factory):
    """``claim_next`` 按 ``priority ASC, created_at ASC`` 领取并原子置 RUNNING。"""

    async def scenario():
        async with db_session_factory() as session:
            repo = PipelineRepository(session)
            now = utcnow()
            low = _pipeline(
                title="low",
                source=PipelineSource.SCHEDULED,
                priority=Priority.SCHEDULED,
                created_at=now - timedelta(minutes=10),
            )
            high = _pipeline(title="high", created_at=now - timedelta(minutes=5))
            await repo.create(low, [_task(low.id)])
            await repo.create(high, [_task(high.id)])
            await session.commit()
            assert await repo.count_pending() == 2

            first = await repo.claim_next(core_epoch=7)
            assert first is not None and first.id == high.id
            assert first.status == PipelineStatus.RUNNING
            assert first.started_at is not None
            assert first.core_epoch == 7
            assert await repo.count_pending() == 1
            assert (await repo.current()).id == high.id

            assert await repo.mark_terminal(high.id, PipelineStatus.COMPLETED) is True
            second = await repo.claim_next()
            assert second is not None and second.id == low.id

            assert await repo.claim_next() is None
            assert await repo.count_pending() == 0
            assert (await repo.current()).id == low.id

    asyncio.run(scenario())


def test_claim_next_isolated_by_core_id(db_session_factory):
    """``core_id`` 不同的流水线不会被本实例领走（多实例扩展口，docs/02 §5.4）。"""

    async def scenario():
        async with db_session_factory() as session:
            repo = PipelineRepository(session)
            other = _pipeline(title="other-core", core_id="core-b")
            await repo.create(other, [_task(other.id)])
            await session.commit()

            assert await repo.claim_next(core_id="default") is None
            assert await repo.count_pending(core_id="default") == 0
            assert await repo.count_pending(core_id="core-b") == 1
            claimed = await repo.claim_next(core_id="core-b")
            assert claimed is not None and claimed.id == other.id

    asyncio.run(scenario())


def test_claim_next_returns_none_when_candidate_was_taken(db_session_factory):
    """条件更新 ``rowcount == 0`` 时返回 ``None``（模拟「选完候选后被并发取走」）。

    ``claim_next`` 的 SELECT 与 UPDATE 之间没有锁；真实竞态下候选可能已被取消或
    被另一个消费者领走。用子类钉住「候选 id 已非 pending」这一分支必须返回 None，
    而不是把已取消的流水线当成本次领取结果。
    """

    async def scenario():
        async with db_session_factory() as session:
            repo = PipelineRepository(session)
            taken = _pipeline(title="taken")
            await repo.create(taken, [_task(taken.id)])
            await session.commit()
            assert (await repo.claim_next()).id == taken.id  # 已被别人领走，现在是 running

            class _StaleCandidateRepository(PipelineRepository):
                async def _next_pending_id(self, core_id: str) -> str | None:
                    return taken.id  # SELECT 看到的还是 pending 快照

            stale = _StaleCandidateRepository(session)
            assert await stale.claim_next() is None
            # 状态没有被这次失败领取改动
            assert (await repo.get(taken.id)).status == PipelineStatus.RUNNING

    asyncio.run(scenario())


def test_cancelled_pending_pipeline_is_not_claimed(db_session_factory):
    """已取消的 PENDING 流水线不会被领取（取消是另一条并发写路径）。"""

    async def scenario():
        async with db_session_factory() as session:
            repo = PipelineRepository(session)
            pipeline = _pipeline()
            await repo.create(pipeline, [_task(pipeline.id)])
            await session.commit()

            assert await repo.mark_terminal(pipeline.id, PipelineStatus.CANCELLED) is True
            assert await repo.claim_next() is None
            assert await repo.count_pending() == 0

    asyncio.run(scenario())


def test_mark_terminal_state_machine(db_session_factory):
    """终态不可再变；非终态入参被拒；错误字段随流转落库。"""

    async def scenario():
        async with db_session_factory() as session:
            repo = PipelineRepository(session)
            queued = _pipeline(title="queued")
            running = _pipeline(title="running")
            await repo.create(queued, [_task(queued.id)])
            await repo.create(running, [_task(running.id)])
            await session.commit()

            # 排队中的流水线可以被取消（用户点取消的路径）
            assert await repo.mark_terminal(queued.id, PipelineStatus.CANCELLED) is True
            assert await repo.mark_terminal(queued.id, PipelineStatus.COMPLETED) is False
            cancelled = await repo.get(queued.id)
            assert cancelled.status == PipelineStatus.CANCELLED
            assert cancelled.finished_at is not None

            await repo.claim_next()
            assert (
                await repo.mark_terminal(
                    running.id,
                    PipelineStatus.FAILED,
                    error_code="DEVICE_DISCONNECTED",
                    error_message="设备掉线",
                )
                is True
            )
            failed = await repo.get(running.id)
            assert failed.status == PipelineStatus.FAILED
            assert failed.error_code == "DEVICE_DISCONNECTED"
            assert failed.error_message == "设备掉线"
            assert failed.finished_at is not None

            # 重复流转与非法目标都被拒绝
            assert await repo.mark_terminal(running.id, PipelineStatus.COMPLETED) is False
            assert await repo.mark_terminal(running.id, PipelineStatus.RUNNING) is False
            assert await repo.mark_terminal("no-such-id", PipelineStatus.CANCELLED) is False

    asyncio.run(scenario())


def test_set_priority_only_for_pending(db_session_factory):
    """只有 PENDING 能改优先级；运行中/终态/不存在返回 False。"""

    async def scenario():
        async with db_session_factory() as session:
            repo = PipelineRepository(session)
            pending = _pipeline(title="pending")
            await repo.create(pending, [_task(pending.id)])
            await session.commit()

            assert await repo.set_priority(pending.id, Priority.SCHEDULED) is True
            assert (await repo.get(pending.id)).priority == Priority.SCHEDULED

            await repo.claim_next()
            assert await repo.set_priority(pending.id, Priority.AGENT) is False
            assert (await repo.get(pending.id)).priority == Priority.SCHEDULED
            assert await repo.set_priority("no-such-id", Priority.AGENT) is False

    asyncio.run(scenario())


def test_purge_before_by_age_deletes_tasks(db_session_factory):
    """超过保留期的流水线被删，且其 task 一并清掉（不留孤儿）。"""

    async def scenario():
        async with db_session_factory() as session:
            repo = PipelineRepository(session)
            old = _pipeline(
                title="old",
                status=PipelineStatus.COMPLETED,
                created_at=utcnow() - timedelta(days=200),
            )
            fresh = _pipeline(title="fresh")
            await repo.create(old, [_task(old.id)])
            await repo.create(fresh, [_task(fresh.id)])
            await session.commit()

            deleted = await repo.purge_before(utcnow() - timedelta(days=90), 500)
            await session.commit()
            assert deleted == 1
            assert await _count(session, "task", "pipeline_id = :p", {"p": old.id}) == 0
            assert await _count(session, "pipeline") == 1
            assert (await repo.list()).total == 1

    asyncio.run(scenario())


def test_purge_before_keep_latest_cap(db_session_factory):
    """天数维度没触发时，条数维度兜底：只保留最近 ``keep_latest`` 条。"""

    async def scenario():
        async with db_session_factory() as session:
            repo = PipelineRepository(session)
            now = utcnow()
            pipelines = [
                _pipeline(title=f"p{i}", created_at=now - timedelta(hours=10 - i))
                for i in range(3)
            ]
            for item in pipelines:
                await repo.create(item, [_task(item.id)])
            await session.commit()

            deleted = await repo.purge_before(now - timedelta(days=90), 1)
            await session.commit()
            assert deleted == 2
            left = await repo.list()
            assert [p.title for p in left.items] == ["p2"]
            assert await _count(session, "task") == 1

    asyncio.run(scenario())


def test_purge_before_deletes_tasks_without_foreign_keys_pragma(tmp_path):
    """``purge_before`` 不依赖调用方连接是否开了 ``PRAGMA foreign_keys``。

    裸 ``create_async_engine``（verify 脚本的形态）默认 ``foreign_keys=0``，SQLite
    的 ``ON DELETE CASCADE`` 静默失效；仓储显式删 task，所以孤儿不会出现。
    生产 ``db/session.py`` 开了 PRAGMA，同一段代码在那里仍正确。
    """

    async def scenario():
        engine = create_async_engine(
            f"sqlite+aiosqlite:///{tmp_path / 'no-fk.db'}", poolclass=NullPool
        )
        try:
            async with engine.begin() as conn:
                await conn.run_sync(SQLModel.metadata.create_all)
                assert (await conn.execute(text("PRAGMA foreign_keys"))).scalar() == 0
            factory = async_sessionmaker(engine, expire_on_commit=False)
            async with factory() as session:
                repo = PipelineRepository(session)
                old = _pipeline(
                    status=PipelineStatus.COMPLETED,
                    created_at=utcnow() - timedelta(days=200),
                )
                await repo.create(old, [_task(old.id)])
                await session.commit()

                assert await repo.purge_before(utcnow() - timedelta(days=90), 500) == 1
                await session.commit()
                assert await _count(session, "task") == 0
        finally:
            await engine.dispose()

    asyncio.run(scenario())


def test_task_repository_status_binding_and_retry(db_session_factory):
    """task 的绑定、状态时间戳、错误码与原子重试计数。"""

    async def scenario():
        async with db_session_factory() as session:
            prepo, trepo = PipelineRepository(session), TaskRepository(session)
            pipeline = _pipeline()
            task = _task(pipeline.id)
            await prepo.create(pipeline, [task])
            await session.commit()

            await trepo.bind_maa_task_id(task.id, 42)
            await trepo.update_status(task.id, TaskStatus.RUNNING)
            await session.commit()
            running = (await trepo.list_by_pipeline(pipeline.id))[0]
            assert running.maa_task_id == 42
            assert running.status == TaskStatus.RUNNING
            assert running.started_at is not None and running.finished_at is None
            assert running.error_code is None

            await trepo.update_status(
                task.id, TaskStatus.FAILED, error_code="TASK_TIMEOUT"
            )
            await session.commit()
            failed = (await trepo.list_by_pipeline(pipeline.id))[0]
            assert failed.status == TaskStatus.FAILED
            assert failed.error_code == "TASK_TIMEOUT"
            assert failed.finished_at is not None

            assert await trepo.increment_retry(task.id) == 1
            assert await trepo.increment_retry(task.id) == 2
            await session.commit()
            assert (await trepo.list_by_pipeline(pipeline.id))[0].retry_count == 2
            assert await trepo.increment_retry("no-such-id") == 0

            # 重试回到 RUNNING 时旧错误码被清掉
            await trepo.update_status(task.id, TaskStatus.RUNNING)
            await session.commit()
            retried = (await trepo.list_by_pipeline(pipeline.id))[0]
            assert retried.status == TaskStatus.RUNNING and retried.error_code is None

            await trepo.update_status(task.id, TaskStatus.SKIPPED)
            await session.commit()
            assert (await trepo.list_by_pipeline(pipeline.id))[0].finished_at is not None

    asyncio.run(scenario())


def test_create_keeps_inputs_readable_after_expire_all(db_session_factory):
    """``create`` 的入参保持 transient：``expire_all()`` 后仍可直接读 ``id``。

    这是 M2-07 卡面 verify 的用法，也是 ``create`` 用 ``merge`` 而非 ``add`` 的
    原因：入参若被会话收养，``expire_all()`` 会清空实例字典，随后在 async 上下文
    外读 ``pipeline.id`` 抛 ``MissingGreenlet``。返回的副本才是会话管理的实例。
    """

    async def scenario():
        async with db_session_factory() as session:
            repo = PipelineRepository(session)
            pipeline = _pipeline()
            stored = await repo.create(pipeline, [_task(pipeline.id)])
            await session.commit()

            assert stored is not pipeline
            assert stored in session and pipeline not in session

            expected_id = pipeline.id
            session.expire_all()
            # 入参字段可直接读（stored 是持久化实例，expire_all 后读它才需要 await）
            assert pipeline.id == expected_id
            assert pipeline.title == "日常"
            assert (await repo.get(pipeline.id)).status == PipelineStatus.PENDING

    asyncio.run(scenario())


def test_db_session_fixture_is_usable(db_session):
    """``db_session`` fixture 直接可用（已建表、提交后不过期）。"""

    async def scenario():
        repo = PipelineRepository(db_session)
        pipeline = _pipeline()
        await repo.create(pipeline, [_task(pipeline.id)])
        await db_session.commit()
        assert pipeline.id is not None  # expire_on_commit=False
        assert (await repo.get(pipeline.id)).title == "日常"

    asyncio.run(scenario())
