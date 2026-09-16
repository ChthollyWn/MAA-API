"""``schedule`` 仓储：定时任务的持久化（docs/04 §5.5、§6、§9）。

定时任务在本层只有三件事：**存模板、查装载集合、回写运行结果**。真正决定
「什么时候触发、要不要跳过、错过怎么办」的是 ``ScheduleService`` + APScheduler
（后续里程碑），本仓储刻意不做任何调度决策，也不 import APScheduler。

四条语义写错会在 M2-12 / M5 才暴雷：

- **``template`` 原样存 ``POST /api/pipelines`` 的 ``tasks`` 数组**，不做任何
  预处理、不注入默认值、不展开成 ``task`` 行（docs/04 §5.5）。定时触发时走的
  是和前端手动提交完全相同的校验与默认值注入路径，不会出现「手动能跑、定时报错」
  的分裂。
- **:meth:`ScheduleRepository.has_unfinished` 只回答「本 schedule 在队列里有没有
  未完成的实例」**，而不是「整个系统是否忙」（docs/04 §5.5）：条件固定为
  ``pipeline.schedule_id = ? AND status IN (pending, running)``，复用
  ``ix_pipeline_schedule_id_created_at``。别的 schedule 的实例、手动提交的流水线
  都不算数；系统忙时新流水线正常排队。是否调用它由调用方按
  ``schedule.skip_if_running`` 决定。
- **``next_run_at`` / ``last_run_at`` 由 APScheduler 回写**（:meth:`record_run`
  只是落值入口）：仓储不自己推算下一次触发时刻，也不在 ``create`` 时补默认值
  —— ``misfire_grace_seconds`` 与 ``catch_up`` 决定了补跑时这两个值可能都是
  过去的时刻，仓储无权替调度器决定。
- **``name`` 的唯一约束由数据库兜底**（``uq_schedule_name``），仓储不先查后写；
  冲突时 ``IntegrityError`` 原样抛出，由服务层转 409。

事务纪律（docs/04 §9）：所有方法都不 ``commit()``，由服务层决定事务边界。
"""

from datetime import datetime

from sqlalchemy import delete, exists, select, update

from maa_api.db.models import Pipeline, Schedule, utcnow
from maa_api.db.repositories.base import BaseRepository
from maa_api.db.repositories.pipeline import NON_TERMINAL_PIPELINE_STATUSES
from maa_api.domain.enums import PipelineStatus


class ScheduleRepository(BaseRepository):
    """``schedule`` 表的读写：装载集合、启停、运行结果回写、未完成判定。"""

    async def create(self, schedule: Schedule) -> Schedule:
        """写入一条定时任务，返回受会话管理的副本；只 ``flush`` 不 ``commit``。

        与 :meth:`PipelineRepository.create` 同样用 ``Session.merge()`` 接收入参
        （M2-07 实测）：入参保持 transient，提交后字段仍可直接读，调用方不必为了
        拿 ``schedule.id`` 再 await 一次刷新；``merge()`` 的返回副本才是会话管理
        的对象，对入参的后续修改不会被提交。
        """
        stored = await self.session.merge(schedule)
        await self.session.flush()
        return stored

    async def get(self, schedule_id: str) -> Schedule | None:
        """按主键取定时任务，没有返回 ``None``。

        ``populate_existing=True``：``session.get()`` 对身份映射里已有的实例不会
        重新读库，配上 ``expire_on_commit=False`` 会返回陈旧对象（M2-07/M2-08
        实测），而调用方常在一次请求里先 ``set_enabled`` / ``record_run`` 再回读。
        """
        return await self.session.get(Schedule, schedule_id, populate_existing=True)

    async def list_enabled(self) -> list[Schedule]:
        """全部启用的定时任务，按 ``next_run_at`` 升序（APScheduler 装载用）。

        ``WHERE enabled = 1 ORDER BY next_run_at`` 正好复用
        ``ix_schedule_enabled_next_run_at(enabled, next_run_at)``（docs/04 §6）。
        ``next_run_at`` 为 NULL 的记录也会返回：装载后由 APScheduler 计算首次
        触发时刻，仓储不把它当成「未启用」。
        """
        result = await self.session.execute(
            select(Schedule)
            .where(Schedule.enabled.is_(True))
            .order_by(Schedule.next_run_at.asc(), Schedule.created_at.asc())
        )
        return list(result.scalars().all())

    async def set_enabled(self, schedule_id: str, enabled: bool) -> bool:
        """启用/停用定时任务，返回是否存在这条记录。

        条件更新 + ``rowcount``（docs/04 §9 的写法），顺带刷新 ``updated_at``。
        ``enabled`` 与当前值相同的重复调用也算成功（``rowcount`` 是匹配行数），
        服务层据此做幂等处理；记录不存在才返回 ``False``。
        """
        result = await self.session.execute(
            update(Schedule)
            .where(Schedule.id == schedule_id)
            .values(enabled=bool(enabled), updated_at=utcnow())
        )
        return result.rowcount > 0

    async def record_run(
        self,
        schedule_id: str,
        *,
        last_run_at: datetime,
        next_run_at: datetime,
        pipeline_id: str | None = None,
        result: PipelineStatus | str | None = None,
    ) -> bool:
        """回写一次触发的四列结果，返回是否存在这条记录。

        ``last_run_at`` / ``next_run_at`` 用调用方（APScheduler 的 job 回调）给的
        时刻，仓储不取 ``utcnow()``；``result`` 存 ``PipelineStatus`` 字符串
        （``schedule.last_result`` 列宽 16）。``None`` 是合法的清空值：例如本次
        触发只做了「跳过」判定，没有对应流水线。
        """
        result_value = PipelineStatus(result) if result is not None else None
        stmt = (
            update(Schedule)
            .where(Schedule.id == schedule_id)
            .values(
                last_run_at=last_run_at,
                next_run_at=next_run_at,
                last_pipeline_id=pipeline_id,
                last_result=result_value,
                updated_at=utcnow(),
            )
        )
        return (await self.session.execute(stmt)).rowcount > 0

    async def has_unfinished(self, schedule_id: str) -> bool:
        """本 schedule 是否有 ``pending`` / ``running`` 的流水线实例（docs/04 §5.5）。

        这就是 ``skip_if_running`` 的判据：只看**本 schedule 自己**的实例，
        不看系统整体忙不忙——手动提交的流水线、别的 schedule 的实例都不影响。
        用 ``EXISTS`` 而不是取回整行，复用 ``ix_pipeline_schedule_id_created_at``
        的前缀；判定口径与 :data:`NON_TERMINAL_PIPELINE_STATUSES` 共用一处，
        不会和 ``mark_terminal`` 的状态机漂移。
        """
        stmt = select(
            exists().where(
                Pipeline.schedule_id == schedule_id,
                Pipeline.status.in_(NON_TERMINAL_PIPELINE_STATUSES),
            )
        )
        return bool((await self.session.execute(stmt)).scalar())

    async def delete(self, schedule_id: str) -> bool:
        """删除定时任务，返回是否存在这条记录。

        先把 ``pipeline.schedule_id`` 指向本记录的实例置空，再删 schedule。
        外键声明的是 ``ON DELETE SET NULL``，但这依赖调用方连接开着
        ``PRAGMA foreign_keys``——裸 ``create_async_engine`` 建出的库该 pragma
        为 0，级联静默失效，删除会留下悬空引用（M2-07 实测，
        ``PipelineRepository.purge_before`` 同样显式清理子表）。PRAGMA 生效时
        这次 UPDATE 只是一次空操作。顺序不能反：schedule 删掉后就找不到这些
        流水线了。
        """
        await self.session.execute(
            update(Pipeline)
            .where(Pipeline.schedule_id == schedule_id)
            .values(schedule_id=None)
        )
        result = await self.session.execute(
            delete(Schedule).where(Schedule.id == schedule_id)
        )
        return result.rowcount > 0
