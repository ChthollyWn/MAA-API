"""``trace_span`` 仓储：运行追踪的批量写入、按 trace 读取与按保留期清理（M68 §4.2.5）。

三条语义写死在这里，调用方不必猜：

- :meth:`TraceSpanRepository.add_many` 只 ``add_all()`` + ``flush()``，**不提交**：
  事务边界由 ``SpanRecorder`` 决定，一批 span 与别的写入是否同生共死由它选。
- :meth:`TraceSpanRepository.list_by_trace` 按 ``(started_at, id)`` 升序返回，
  ``id`` 是同一时刻多个 span 的稳定次序；``limit`` 由调用方给（API 侧 5000）。
- :meth:`TraceSpanRepository.purge_before` **自成事务**（唯一例外，照
  ``LogRepository.purge``）：删除是保留策略的后台工作，每批提交才能让出 SQLite
  的写锁；单次只删一批，调用方循环到返回值小于 :data:`TRACE_PURGE_BATCH_SIZE`。

其余读方法不 commit。
"""

from collections.abc import Sequence
from datetime import datetime

from sqlalchemy import delete, select

from maa_api.db.models import TraceSpan
from maa_api.db.repositories.base import BaseRepository

# 单条 DELETE 的行数上限（与 LogRepository 的 PURGE_BATCH_SIZE 同口径）：
# 一条语句删十万行会长时间持写锁，期间追踪写入都在 busy_timeout 上排队。
TRACE_PURGE_BATCH_SIZE = 5000


class TraceSpanRepository(BaseRepository):
    """``trace_span`` 的读写；属性清洗、span 树与导出都不在这里。"""

    async def add_many(self, rows: Sequence[TraceSpan]) -> None:
        """批量写入 span，只 ``flush`` 不 ``commit``。

        ``SpanRecorder`` 每次取一批（默认 200 条）调一次，所以用 ``add_all()``
        而不是逐条 ``add()``；``flush`` 之后自增 ``id`` 已落在入参实例上。
        空序列直接返回：不为空批次开启事务。``run_id`` 指向的运行已被删除时
        外键失败原样抛出，由调用方按写入失败重试或丢弃。
        """
        if not rows:
            return
        self.session.add_all(list(rows))
        await self.session.flush()

    async def list_by_trace(self, trace_id: str, *, limit: int) -> list[TraceSpan]:
        """取出 ``trace_id`` 下的 span，按 ``(started_at, id)`` 升序，最多 ``limit`` 条。

        同一时刻开始的 span（例如一次工具调用里并排的兄弟）按 id 升序，保证
        「父先于子、先发的先回」的稳定顺序，前端瀑布图不必自己再排。
        """
        result = await self.session.execute(
            select(TraceSpan)
            .where(TraceSpan.trace_id == trace_id)
            .order_by(TraceSpan.started_at.asc(), TraceSpan.id.asc())
            .limit(limit)
        )
        return list(result.scalars().all())

    async def purge_before(self, cutoff: datetime) -> int:
        """删一批 ``started_at < cutoff`` 的行并**提交**，返回删除条数。

        子查询按 ``id`` 升序取最旧的 :data:`TRACE_PURGE_BATCH_SIZE` 条（与
        ``LogRepository._delete_old_batch`` 同一个写法），再按 id 删掉它们。
        每次调用只删一批：保留策略循环调用，直到返回值小于批量。
        """
        batch_ids = (
            select(TraceSpan.id)
            .where(TraceSpan.started_at < cutoff)
            .order_by(TraceSpan.id.asc())
            .limit(TRACE_PURGE_BATCH_SIZE)
        )
        try:
            result = await self.session.execute(
                delete(TraceSpan).where(TraceSpan.id.in_(batch_ids)),
                execution_options={"synchronize_session": False},
            )
            await self.session.commit()
        except Exception:
            await self.session.rollback()
            raise
        return max(int(result.rowcount or 0), 0)
