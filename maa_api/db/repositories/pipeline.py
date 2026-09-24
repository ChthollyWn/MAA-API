"""``pipeline`` 与 ``task`` 两个仓储（docs/04 §5.1、§5.2、§6、§9）。

`pipeline` 是队列的唯一事实来源：``PipelineRunner``（M5）按
``ORDER BY priority ASC, created_at ASC`` 取 ``PENDING`` 记录，因此本模块的
查询条件与排序都刻意贴着 docs/04 §6 的 ``ix_pipeline_dequeue`` /
``ix_pipeline_status_created_at`` / ``ix_pipeline_source_created_at`` /
``ix_pipeline_schedule_id_created_at`` 与 ``uq_task_pipeline_order`` 来写，
不额外发明查询形态。

事务纪律：本模块所有方法都**不 commit**。一次流水线提交要在同一事务里写
``pipeline`` 与全部 ``task``（:meth:`PipelineRepository.create` 只 ``flush``），
由服务层决定何时提交。
"""

from __future__ import annotations

from collections.abc import Sequence
from datetime import datetime
from typing import Any

from sqlalchemy import Select, delete, func, select, update

from maa_api.db.models import Pipeline, Task, utcnow
from maa_api.db.repositories.base import BaseRepository, Page
from maa_api.domain.enums import PipelineSource, PipelineStatus, Priority, TaskStatus

# 允许 mark_terminal 流转的起始状态：终态不可再变（docs/04 §9）。
NON_TERMINAL_PIPELINE_STATUSES = (PipelineStatus.PENDING, PipelineStatus.RUNNING)

# task 的终态：进入这些状态时补 finished_at（TaskStatus 没有 is_terminal 属性）。
TERMINAL_TASK_STATUSES = (
    TaskStatus.COMPLETED,
    TaskStatus.FAILED,
    TaskStatus.CANCELLED,
    TaskStatus.SKIPPED,
)


class PipelineRepository(BaseRepository):
    """流水线队列的读写。状态机流转一律用条件更新 + ``rowcount`` 判定。"""

    async def create(
        self, pipeline: Pipeline, tasks: Sequence[Task]
    ) -> Pipeline:
        """在同一事务里写入流水线与其全部任务，返回受会话管理的 ``Pipeline``。

        只 ``flush`` 不 ``commit``：调用方提交时两者一起落库，回滚时一起消失。

        用 ``Session.merge()`` 而不是 ``Session.add()`` 接收入参，这是 M2-07
        实测后的决定：``add()`` 会让**入参对象**变成会话管理的持久化实例，
        之后任何一次 ``expire_all()``（或 ``expire_on_commit=True`` 的提交）都会
        清空它的实例字典，而 async 上下文外读取 ``pipeline.id`` 这类字段会抛
        ``MissingGreenlet`` —— 调用方只是想在提交后拿 id，却被迫 await 刷新。
        ``merge()`` 把状态复制进一个会话管理的副本并返回它，入参保持 transient、
        字段永远可读（id 等 Python 侧默认值在构造时就已生成，与副本一致）。

        代价与约定：**入参只是数据载体**，对入参的后续修改不会被提交（副本才是
        会话管理的对象）；后续状态变更走本仓储的状态方法或使用返回值。
        传入的 ``task`` 的 ``pipeline_id`` 会被指向 ``pipeline.id`` 后一并写入。
        """
        stored = await self.session.merge(pipeline)
        await self.session.flush()  # 先落 pipeline 行，task 的外键才有目标
        for task in tasks:
            task.pipeline_id = stored.id
            await self.session.merge(task)
        await self.session.flush()
        return stored

    async def get(
        self, pipeline_id: str, *, with_tasks: bool = False
    ) -> Pipeline | None:
        """按主键取流水线；``with_tasks=True`` 时把任务列表挂到 ``pipeline.tasks``。

        `Pipeline` 模型没有定义 relationship（表定义归 M2-03，仓储不改模型），
        所以任务列表用**非映射属性** ``tasks`` 传递。pydantic v2 禁止给模型实例
        赋未声明字段（``ValueError: object has no field "tasks"``），只能走
        ``object.__setattr__`` 绕过 —— 这是 M2-07 实测结论，不要改回 ``setattr``。

        ``populate_existing=True`` 是必需的：``session.get()`` 对已在身份映射里的
        对象**不会**重新加载，调用方 ``expire_all()`` 之后拿到的是过期实例，
        在 async 上下文外读字段会抛 ``MissingGreenlet``（M2-07 实测）。
        强制从库里刷新，``get`` 的返回值永远可直接读。
        """
        pipeline = await self.session.get(
            Pipeline, pipeline_id, populate_existing=True
        )
        if pipeline is None or not with_tasks:
            return pipeline
        tasks = await TaskRepository(self.session).list_by_pipeline(pipeline_id)
        object.__setattr__(pipeline, "tasks", tasks)
        return pipeline

    async def get_by_idempotency_key(self, key: str) -> Pipeline | None:
        """幂等提交去重（``uq_pipeline_idempotency_key``）；``key`` 为空返回 None。"""
        if not key:
            return None
        result = await self.session.execute(
            select(Pipeline).where(Pipeline.idempotency_key == key).limit(1)
        )
        return result.scalars().first()

    async def list(
        self,
        *,
        status: PipelineStatus | None = None,
        source: PipelineSource | None = None,
        schedule_id: str | None = None,
        since: datetime | None = None,
        page: int = 1,
        size: int = 20,
    ) -> Page[Pipeline]:
        """流水线列表：可选 ``status`` / ``source`` / ``schedule_id`` / ``since``
        过滤，按 ``created_at DESC`` 分页（docs/04 §6 的前端首页与历史页形态）。

        ``since`` 是入队时间的下界（含端点），用于「最近 N 天」这类查询。
        """
        conditions: list[Any] = []
        if status is not None:
            conditions.append(Pipeline.status == status)
        if source is not None:
            conditions.append(Pipeline.source == source)
        if schedule_id is not None:
            conditions.append(Pipeline.schedule_id == schedule_id)
        if since is not None:
            conditions.append(Pipeline.created_at >= since)

        items_stmt = (
            select(Pipeline)
            .where(*conditions)
            # id 只作同秒并列时的稳定次序，不改变 created_at DESC 的主序
            .order_by(Pipeline.created_at.desc(), Pipeline.id.desc())
        )
        count_stmt = select(func.count()).select_from(Pipeline).where(*conditions)
        return await self.paginate(items_stmt, count_stmt, page=page, size=size)

    async def _next_pending_id(self, core_id: str) -> str | None:
        """取下一个候选流水线 id：``core_id + status='pending'``，按
        ``priority ASC, created_at ASC``（复用 ``ix_pipeline_dequeue``）。

        只取 id、不加锁：并发安全由 :meth:`claim_next` 的条件更新保证。
        """
        result = await self.session.execute(
            select(Pipeline.id)
            .where(
                Pipeline.core_id == core_id,
                Pipeline.status == PipelineStatus.PENDING,
                (Pipeline.deferred_until.is_(None)) | (Pipeline.deferred_until <= utcnow()),
            )
            .order_by(Pipeline.priority.asc(), Pipeline.created_at.asc())
            .limit(1)
        )
        return result.scalar_one_or_none()

    async def claim_next(
        self, core_id: str = "default", *, core_epoch: int | None = None
    ) -> Pipeline | None:
        """原子地领取下一条待执行流水线；没有可领的返回 ``None``。

        「先查后改」会有窗口：用户可能在领取的瞬间点取消，或未来出现第二个
        消费者。所以选中候选后仍用 **条件更新** 收口（docs/04 §9）：

        ``UPDATE pipeline SET status='running' ... WHERE id=? AND status='pending'``

        ``rowcount == 0`` 表示候选已被并发取走或已取消，返回 ``None``（由调用方
        决定是否重试，而不是在这里循环重选 —— 仓储不做队列调度决策）。

        ``core_epoch`` 是执行时子进程的启动世代号（docs/04 §5.1），用于事后
        把 ``task.maa_task_id`` 归位到某一次内核运行；M5 的 ``PipelineRunner``
        领取时传入，缺省不写（NULL）。
        """
        candidate_id = await self._next_pending_id(core_id)
        if candidate_id is None:
            return None

        result = await self.session.execute(
            update(Pipeline)
            .where(
                Pipeline.id == candidate_id,
                Pipeline.status == PipelineStatus.PENDING,
                (Pipeline.deferred_until.is_(None)) | (Pipeline.deferred_until <= utcnow()),
            )
            .values(
                status=PipelineStatus.RUNNING,
                started_at=utcnow(),
                core_epoch=core_epoch,
                deferred_until=None,
            )
        )
        if result.rowcount == 0:
            return None  # 被并发取走或已取消

        # populate_existing：把条件更新写入的状态读回来，即使该实例已在身份映射里
        return await self.session.get(
            Pipeline, candidate_id, populate_existing=True
        )

    async def defer_running(
        self, pipeline_id: str, deferred_until: datetime
    ) -> bool:
        """Return a scheduled RUNNING pipeline to PENDING until ``deferred_until``.

        Device preflight runs after a claim. This conditional update makes the
        handoff back to the queue atomic and persists the deferral count without
        committing; only scheduled rows are eligible for this transition.
        """
        result = await self.session.execute(
            update(Pipeline)
            .where(
                Pipeline.id == pipeline_id,
                Pipeline.status == PipelineStatus.RUNNING,
                Pipeline.source == PipelineSource.SCHEDULED,
            )
            .values(
                status=PipelineStatus.PENDING,
                started_at=None,
                core_epoch=None,
                deferred_until=deferred_until,
                defer_count=Pipeline.defer_count + 1,
            )
        )
        return result.rowcount > 0

    async def current(self, core_id: str = "default") -> Pipeline | None:
        """当前 ``RUNNING`` 的流水线（状态轮询与冲突判定用），没有返回 ``None``。"""
        result = await self.session.execute(
            select(Pipeline)
            .where(
                Pipeline.core_id == core_id,
                Pipeline.status == PipelineStatus.RUNNING,
            )
            .order_by(Pipeline.started_at.desc(), Pipeline.created_at.desc())
            .limit(1)
        )
        return result.scalars().first()

    async def list_running(self, core_id: str = "default") -> list[Pipeline]:
        """Return every running pipeline for recovery after a process restart."""
        result = await self.session.execute(
            select(Pipeline)
            .where(
                Pipeline.core_id == core_id,
                Pipeline.status == PipelineStatus.RUNNING,
            )
            .order_by(Pipeline.started_at.asc(), Pipeline.created_at.asc())
        )
        return list(result.scalars().all())

    async def list_pending(
        self, core_id: str = "default", *, source: PipelineSource | None = None
    ) -> list[Pipeline]:
        """Return queued rows in dequeue order, optionally restricted by source."""
        conditions: list[Any] = [
            Pipeline.core_id == core_id,
            Pipeline.status == PipelineStatus.PENDING,
        ]
        if source is not None:
            conditions.append(Pipeline.source == source)
        result = await self.session.execute(
            select(Pipeline)
            .where(*conditions)
            .order_by(Pipeline.priority.asc(), Pipeline.created_at.asc(), Pipeline.id.asc())
        )
        return list(result.scalars().all())

    async def release_expired_idempotency_key(
        self, key: str, *, older_than: datetime
    ) -> bool:
        """Allow a key to be reused after its documented 24-hour window."""
        result = await self.session.execute(
            update(Pipeline)
            .where(
                Pipeline.idempotency_key == key,
                Pipeline.created_at < older_than,
            )
            .values(idempotency_key=None)
        )
        return result.rowcount > 0

    async def count_pending(self, core_id: str = "default") -> int:
        """待执行队列长度（复用 ``ix_pipeline_dequeue`` 前缀）。"""
        return await self.count(
            select(func.count())
            .select_from(Pipeline)
            .where(
                Pipeline.core_id == core_id,
                Pipeline.status == PipelineStatus.PENDING,
            )
        )

    async def mark_terminal(
        self,
        pipeline_id: str,
        status: PipelineStatus,
        *,
        error_code: str | None = None,
        error_message: str | None = None,
    ) -> bool:
        """把流水线置入终态；返回状态机是否接受了这次流转（docs/04 §9）。

        ``WHERE status IN (pending, running)`` 在数据库层保证「终态不可再变」，
        不接受时返回 ``False``，服务层据此转 409。``status`` 不是终态（例如
        ``RUNNING``）时直接拒绝，避免绕过状态机。``error_code`` /
        ``error_message`` 随本次流转一起写（``None`` 即清空，非失败终态本就没有）。
        """
        target = PipelineStatus(status)
        if not target.is_terminal:
            return False

        result = await self.session.execute(
            update(Pipeline)
            .where(
                Pipeline.id == pipeline_id,
                Pipeline.status.in_(NON_TERMINAL_PIPELINE_STATUSES),
            )
            .values(
                status=target,
                finished_at=utcnow(),
                error_code=error_code,
                error_message=error_message,
            )
        )
        return result.rowcount > 0

    async def set_priority(self, pipeline_id: str, priority: Priority) -> bool:
        """调整待执行流水线的优先级（队列接口用）；非 ``PENDING`` 返回 ``False``。

        正在执行或已终态的流水线不参与排序，改优先级没有意义，所以只放行
        ``PENDING``（「不抢占」语义见 docs/02 §5.1）。
        """
        result = await self.session.execute(
            update(Pipeline)
            .where(
                Pipeline.id == pipeline_id,
                Pipeline.status == PipelineStatus.PENDING,
            )
            .values(priority=int(priority))
        )
        return result.rowcount > 0

    async def purge_before(self, cutoff: datetime, keep_latest: int) -> int:
        """按保留策略删除历史流水线，返回删除条数（docs/04 §10.3）。

        两个维度同时生效、先触发者先删：先删 ``created_at < cutoff`` 的，再对
        剩余记录只保留最近 ``keep_latest`` 条（多出来的按 ``created_at`` 升序删）。
        与日志清理同构，「取更严格的一方」。

        删除 pipeline 时同步删 task。生产连接的 ``PRAGMA foreign_keys=ON``
        （``db/session.py``）会让外键 CASCADE 自动清掉 task，但仓储不能假设调用
        方的连接一定开了那个 PRAGMA —— M2-07 实测：裸 ``create_async_engine``
        建出的库 ``foreign_keys=0``，CASCADE 静默不生效，purge 会留下孤儿 task。
        因此这里显式删一遍 task；级联生效时它只是一次空操作。

        本方法不 commit（清理服务自行决定事务边界与分批节奏，M2-12）。
        """
        deleted = 0
        # 第一优先级：超过保留期
        deleted += await self._delete_pipelines_with_tasks(
            select(Pipeline.id).where(Pipeline.created_at < cutoff)
        )
        # 第二优先级：剩余记录里超出条数上限的（保留最近 keep_latest 条）
        deleted += await self._delete_pipelines_with_tasks(
            select(Pipeline.id)
            .order_by(Pipeline.created_at.desc(), Pipeline.id.desc())
            .offset(max(int(keep_latest), 0))
        )
        return deleted

    async def _delete_pipelines_with_tasks(self, id_select: Select[Any]) -> int:
        """先把 ``id_select`` 选中的流水线的 task 删掉，再删流水线，返回流水线条数。

        ``synchronize_session=False``：批量清理不需要把删除同步回会话里的 ORM
        对象，避免 SQLAlchemy 为 DELETE 先发一条 SELECT 把整批实体取回来。
        子查询的 ``ORDER BY ... LIMIT`` 让 SQLite 不必支持 ``DELETE ... LIMIT``
        （默认编译期未开启 ``SQLITE_ENABLE_UPDATE_DELETE_LIMIT``，docs/04 §10.4）。
        """
        await self.session.execute(
            delete(Task).where(Task.pipeline_id.in_(id_select)),
            execution_options={"synchronize_session": False},
        )
        result = await self.session.execute(
            delete(Pipeline).where(Pipeline.id.in_(id_select)),
            execution_options={"synchronize_session": False},
        )
        return result.rowcount


class TaskRepository(BaseRepository):
    """流水线内任务的读写：顺序、内核 task id 绑定、状态与重试计数。"""

    async def list_by_pipeline(self, pipeline_id: str) -> list[Task]:
        """按 ``order_index`` 升序返回整条流水线的任务（详情页与执行循环共用）。"""
        result = await self.session.execute(
            select(Task)
            .where(Task.pipeline_id == pipeline_id)
            .order_by(Task.order_index.asc())
        )
        return list(result.scalars().all())

    async def get(self, task_id: str) -> Task | None:
        """Get one task by its public UUID."""
        return await self.session.get(Task, task_id, populate_existing=True)

    async def bind_maa_task_id(self, task_id: str, maa_task_id: int) -> None:
        """回写 ``AsstAppendTask`` 的返回值（内核 task id）。

        注意 ``maa_task_id == 0`` 是内核「参数校验失败、静默拒绝」的信号
        （见 docs/ENVIRONMENT.md），调用方应先判定失败再决定是否重试，
        仓储只负责落值。
        """
        await self.session.execute(
            update(Task).where(Task.id == task_id).values(maa_task_id=maa_task_id)
        )

    async def update_status(
        self,
        task_id: str,
        status: TaskStatus,
        *,
        error_code: str | None = None,
        error_message: str | None = None,
    ) -> None:
        """更新任务状态，并按目标状态补时间戳。

        ``RUNNING`` 补 ``started_at``；终态（completed / failed / cancelled /
        skipped）补 ``finished_at``。``error_code`` 随本次流转一起写（``None``
        即清空，重试回到 ``RUNNING`` 时旧错误码不该留着）。
        """
        target = TaskStatus(status)
        values: dict[str, Any] = {
            "status": target,
            "error_code": error_code,
            "error_message": error_message,
        }
        if target is TaskStatus.RUNNING:
            values["started_at"] = utcnow()
        elif target in TERMINAL_TASK_STATUSES:
            values["finished_at"] = utcnow()

        await self.session.execute(
            update(Task).where(Task.id == task_id).values(**values)
        )

    async def increment_retry(self, task_id: str) -> int:
        """把 ``retry_count`` 原子加一并返回新值；任务不存在时返回 ``0``。

        用 ``UPDATE ... RETURNING`` 而不是「先读后写」，重试计数不会因为两个
        回调同时到达而少加一次。返回值供服务层与 ``max_retries`` 比较。
        """
        result = await self.session.execute(
            update(Task)
            .where(Task.id == task_id)
            .values(retry_count=Task.retry_count + 1)
            .returning(Task.retry_count)
        )
        new_value = result.scalar_one_or_none()
        return int(new_value) if new_value is not None else 0
