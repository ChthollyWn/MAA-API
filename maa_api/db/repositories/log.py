"""``log_entry`` 与 ``screenshot`` 两个仓储（docs/04 §5.3、§5.4、§6、§9、§10.4）。

日志侧的三条硬约束（写错任何一条都会在 M4/M5 才暴雷）：

- **``created_at`` 是事件时间。** ``LogHub`` 攒批刷盘，入库比事件晚几百毫秒，
  所以 ``bulk_insert`` 原样写入调用方构造时给出的时间戳（IPC 事件的 ``ts``），
  绝不在这里用 ``INSERT`` 时刻覆盖（docs/04 §5.3）。
- **``query(after_id=...)`` 是严格 ``id > after_id``。** ``log_entry.id`` 单调递增
  且兼作 WebSocket 续传游标，语义写成 ``>=`` 会让客户端反复收到同一条日志。
- **``content`` 只放已格式化的正文，绝不放 base64 图像。** 截图先落 ``screenshot``
  表，日志里只带 ``meta.screenshot_id``（docs/04 §5.3）；仓储不做内容校验，
  这是调用方的纪律。

事务纪律（与 ``pipeline.py`` 的差别，读代码前先看这里）：

- :meth:`LogRepository.bulk_insert` 与 :meth:`LogRepository.purge` **自成事务**：
  日志落盘与业务事务的成败无关（业务回滚了也不该丢掉解释失败原因的日志，
  docs/04 §9）；且 docs/04 §10.4 的清理循环 ``while True: purge(...)`` 要求
  每次调用都提交并让出写锁，DELETE 留在调用方事务里会一直握住 SQLite 的写锁，
  分批删除就失去意义。
- :class:`ScreenshotRepository` 的写入方法（``create`` / ``mark_deleted`` /
  ``purge_before``）与 ``PipelineRepository`` 一致**不 commit**，由清理服务
  （M2-12）决定事务边界；``bulk_insert`` / ``purge`` 会顺带提交同一会话里调用方
  尚未提交的其他写入，所以调用方要给日志与清理任务各自开独立会话
  （``db/session.py`` 的会话纪律：后台任务各自开新会话）。
- 读方法一律不 commit。
"""

from collections.abc import Sequence
from datetime import datetime
from typing import Any

from sqlalchemy import delete, func, select, update

from maa_api.db.models import LogEntry, Screenshot, utcnow
from maa_api.db.repositories.base import BaseRepository, Page
from maa_api.domain.enums import LogLevel, LogSource

# 单条 DELETE 的行数上限（docs/04 §10.4）：一条语句删十万行会长时间持写锁，
# 期间所有日志落盘都在 busy_timeout 上排队。调用方循环 purge 直到返回 0。
PURGE_BATCH_SIZE = 5000


class LogRepository(BaseRepository):
    """三路日志的落盘、分页/游标查询与两维清理。"""

    async def bulk_insert(self, entries: Sequence[LogEntry]) -> int:
        """攒批写入日志并**立即提交**，返回写入条数。

        ``LogHub`` 攒够 200 条或超过 500 ms 调一次（docs/04 §10.4），因此这里
        用 ``add_all()`` 而不是逐条 ``add()``。提交是必须的：日志与业务事务无关，
        业务回滚不该带走日志；同时提交后 ``entries`` 的 ``id`` 已由 flush 填好，
        可直接作为 WebSocket 广播的游标（会话若配了 ``expire_on_commit=True``，
        提交后读 ``id`` 会触发过期刷新，生产会话是 ``expire_on_commit=False``）。
        ``created_at`` 原样使用入参（事件时间），缺省才由模型 ``default_factory``
        补 ``utcnow()``。

        空序列直接返回 0，不开启也不提交事务 —— 刷盘循环最后一个不满批的队列
        不该顺带提交调用方会话里的其他写入。写入失败时回滚并原样抛出
        （外键失效等），会话保持可用，由 ``LogHub`` 决定丢弃还是重试。
        """
        rows = list(entries)
        if not rows:
            return 0
        try:
            self.session.add_all(rows)
            await self.session.commit()
        except Exception:
            await self.session.rollback()
            raise
        return len(rows)

    async def query(
        self,
        *,
        sources: Sequence[LogSource] | None = None,
        levels: Sequence[LogLevel] | None = None,
        pipeline_id: str | None = None,
        after_id: int | None = None,
        since: datetime | None = None,
        until: datetime | None = None,
        page: int = 1,
        size: int = 100,
        order: str = "desc",
    ) -> Page[LogEntry]:
        """按 id 分页查询日志，默认 ``DESC``，返回 :class:`Page`。

        ``sources`` / ``levels`` 传 ``None`` 表示不过滤；传**空序列**表示
        「没有任何来源/级别匹配」，返回空页（``IN ()`` 恒假），订阅了空集合的
        调用方不会意外拿到全量日志。``since`` / ``until`` 都是含端点的闭区间。
        ``after_id`` 是 WebSocket 断线续传游标：严格 ``id > after_id``
        （docs/04 §3.1/§5.3），补发时把它和 ``page`` 组合使用；``total`` 是
        过滤后的总数，客户端据此翻页。

        查询形态贴着 docs/04 §6 的索引写：``source`` 等值（复用
        ``ix_log_entry_source_created_at`` 首列）、``pipeline_id`` 等值
        （``ix_log_entry_pipeline_id_id``）、``id > ?`` 走主键，
        ``content`` 不做全文索引。
        """
        if order not in {"asc", "desc"}:
            raise ValueError("order must be 'asc' or 'desc'")
        conditions: list[Any] = []
        if sources is not None:
            conditions.append(LogEntry.source.in_([LogSource(s) for s in sources]))
        if levels is not None:
            conditions.append(LogEntry.level.in_([LogLevel(level) for level in levels]))
        if pipeline_id is not None:
            conditions.append(LogEntry.pipeline_id == pipeline_id)
        if after_id is not None:
            # 严格大于：续传游标是「最后一条已收到的日志」
            conditions.append(LogEntry.id > int(after_id))
        if since is not None:
            conditions.append(LogEntry.created_at >= since)
        if until is not None:
            conditions.append(LogEntry.created_at <= until)

        sort = LogEntry.id.asc() if order == "asc" else LogEntry.id.desc()
        items_stmt = select(LogEntry).where(*conditions).order_by(sort)
        count_stmt = select(func.count()).select_from(LogEntry).where(*conditions)
        return await self.paginate(items_stmt, count_stmt, page=page, size=size)

    async def query_cursor(
        self,
        *,
        sources: Sequence[LogSource] | None = None,
        minimum_level: LogLevel | None = None,
        pipeline_id: str | None = None,
        task_id: str | None = None,
        logger_prefix: str | None = None,
        query: str | None = None,
        since: datetime | None = None,
        until: datetime | None = None,
        after_id: int | None = None,
        before_id: int | None = None,
        order: str = "desc",
        size: int = 100,
        offset: int = 0,
    ) -> tuple[list[LogEntry], bool]:
        """Fetch one cursor page plus a look-ahead row, without a table count.

        ``after_id`` is an exclusive lower bound and ``before_id`` an exclusive
        upper bound. The order is explicit because clients use the same bounds
        for live catch-up (ascending) and older-history paging (descending).
        """
        if order not in {"asc", "desc"}:
            raise ValueError("order must be asc or desc")
        conditions: list[Any] = []
        if sources is not None:
            normalized_sources = [LogSource(source) for source in sources]
            if not normalized_sources:
                return [], False
            conditions.append(LogEntry.source.in_(normalized_sources))
        if minimum_level is not None:
            ranks = {
                LogLevel.DEBUG: 10,
                LogLevel.INFO: 20,
                LogLevel.WARNING: 30,
                LogLevel.ERROR: 40,
                LogLevel.CRITICAL: 50,
            }
            minimum_rank = ranks[LogLevel(minimum_level)]
            conditions.append(
                LogEntry.level.in_(
                    [level for level, rank in ranks.items() if rank >= minimum_rank]
                )
            )
        if pipeline_id is not None:
            conditions.append(LogEntry.pipeline_id == pipeline_id)
        if task_id is not None:
            conditions.append(LogEntry.task_id == task_id)
        if logger_prefix is not None:
            conditions.append(LogEntry.meta["logger"].as_string().startswith(logger_prefix))
        if query:
            conditions.append(LogEntry.content.contains(query, autoescape=True))
        if since is not None:
            conditions.append(LogEntry.created_at >= since)
        if until is not None:
            conditions.append(LogEntry.created_at <= until)
        if after_id is not None:
            conditions.append(LogEntry.id > int(after_id))
        if before_id is not None:
            conditions.append(LogEntry.id < int(before_id))

        sort = LogEntry.id.desc() if order == "desc" else LogEntry.id.asc()
        statement = (
            select(LogEntry)
            .where(*conditions)
            .order_by(sort)
            .offset(max(int(offset), 0))
            .limit(max(int(size), 1) + 1)
        )
        result = await self.session.execute(statement)
        rows = list(result.scalars().all())
        has_more = len(rows) > max(int(size), 1)
        return rows[: max(int(size), 1)], has_more

    async def delete_matching(
        self,
        *,
        sources: Sequence[LogSource] | None = None,
        before: datetime | None = None,
    ) -> int:
        """Delete matching history in its own transaction (M4 history endpoint)."""
        if sources is None and before is None:
            raise ValueError("at least one deletion condition is required")
        conditions: list[Any] = []
        if sources is not None:
            normalized_sources = [LogSource(source) for source in sources]
            if not normalized_sources:
                return 0
            conditions.append(LogEntry.source.in_(normalized_sources))
        if before is not None:
            conditions.append(LogEntry.created_at < before)
        try:
            result = await self.session.execute(
                delete(LogEntry).where(*conditions),
                execution_options={"synchronize_session": False},
            )
            await self.session.commit()
        except Exception:
            await self.session.rollback()
            raise
        return int(result.rowcount or 0)

    async def purge(self, source: LogSource, *, before: datetime, keep_max: int) -> int:
        """两维清理单一来源的日志并**立即提交**，返回删除条数（docs/04 §10.2/§10.4）。

        两个维度取更严格的一方：先删 ``created_at < before`` 的（第一维），
        再对剩余记录按 ``id`` 升序删到 ``keep_max`` 条以内（第二维，防「一次异常
        刷了几十万行」的突发）。每批最多 :data:`PURGE_BATCH_SIZE` 行，批内用
        子查询 ``ORDER BY ... LIMIT`` 表达 —— SQLite 默认没开
        ``SQLITE_ENABLE_UPDATE_DELETE_LIMIT``（本机实测编译选项里没有），
        ``DELETE ... LIMIT`` 不可用。

        调用方（M2-12 的清理服务）按 docs/04 §10.4 循环调用直到返回 0，中间
        ``await asyncio.sleep()`` 让出写锁；本方法每次调用都提交，正是为了让
        写锁在批与批之间真正释放。
        """
        target = LogSource(source)
        keep = max(int(keep_max), 0)
        deleted = 0
        try:
            # 第一维：超过保留天数
            while True:
                batch = await self._delete_old_batch(target, before)
                deleted += batch
                if batch < PURGE_BATCH_SIZE:
                    break
            # 第二维：条数上限，按 id 升序删最旧的，保留最新 keep 条
            while True:
                batch = await self._delete_over_cap_batch(target, keep)
                deleted += batch
                if batch == 0:
                    break
            await self.session.commit()
        except Exception:
            await self.session.rollback()
            raise
        return deleted

    async def _delete_old_batch(self, source: LogSource, before: datetime) -> int:
        """删一批 ``source`` 下 ``created_at < before`` 的日志，返回条数。"""
        batch_ids = (
            select(LogEntry.id)
            .where(LogEntry.source == source, LogEntry.created_at < before)
            .order_by(LogEntry.id.asc())
            .limit(PURGE_BATCH_SIZE)
        )
        result = await self.session.execute(
            delete(LogEntry).where(LogEntry.id.in_(batch_ids)),
            execution_options={"synchronize_session": False},
        )
        return result.rowcount

    async def _delete_over_cap_batch(self, source: LogSource, keep_max: int) -> int:
        """删一批超出条数上限的旧日志，返回条数；已在上限内返回 0。

        先数当前剩余条数再算超出量，因此每批都按最新状态判断；``LIMIT`` 取
        「超出量」与批上限的较小值。
        """
        total = await self.count(
            select(func.count()).select_from(LogEntry).where(LogEntry.source == source)
        )
        excess = total - keep_max
        if excess <= 0:
            return 0
        batch_ids = (
            select(LogEntry.id)
            .where(LogEntry.source == source)
            .order_by(LogEntry.id.asc())
            .limit(min(excess, PURGE_BATCH_SIZE))
        )
        result = await self.session.execute(
            delete(LogEntry).where(LogEntry.id.in_(batch_ids)),
            execution_options={"synchronize_session": False},
        )
        return result.rowcount


class ScreenshotRepository(BaseRepository):
    """截图归档记录的读写；文件本体归 M2-12 的保留策略管理。

    ``path`` 是相对 ``resource/`` 的相对路径（按日期分目录），仓储只存字符串、
    不拼绝对路径、不碰文件系统；``deleted_at`` 非空表示文件已被清理但**记录保留**
    （docs/04 §5.4），接口据此返回 ``410 SCREENSHOT_EXPIRED``（docs/05 §6.2）。
    本类所有方法都不 commit。
    """

    async def create(self, screenshot: Screenshot) -> Screenshot:
        """写入一条截图记录并 flush，返回受会话管理的副本（不 commit）。

        与 ``PipelineRepository.create`` 同款：``merge()`` 让入参保持 transient、
        字段永远可读（M2-07 实测：``add()`` 会让入参变成会话管理的持久化实例，
        提交/过期后在 async 上下文外读 ``id`` 会抛 ``MissingGreenlet``）。
        调用方拿到返回值或自己保留的 id 都行。
        """
        stored = await self.session.merge(screenshot)
        await self.session.flush()
        return stored

    async def get(self, screenshot_id: str) -> Screenshot | None:
        """按主键取一条截图记录，不存在返回 ``None``。

        ``populate_existing=True`` 是必需的：``mark_deleted`` 用 Core UPDATE 改的
        是库里的行，若身份映射里已有同 id 的旧实例，``session.get`` 默认会直接
        返回那个过期对象（会话是 ``expire_on_commit=False``，提交也不会刷新它），
        调用方就会看到 ``deleted_at is None``。
        """
        return await self.session.get(
            Screenshot, screenshot_id, populate_existing=True
        )

    async def list_page(
        self,
        *,
        pipeline_id: str | None = None,
        trigger: str | None = None,
        since: datetime | None = None,
        page: int = 1,
        size: int = 100,
    ) -> Page[Screenshot]:
        """List screenshots newest-first with the API's three supported filters."""
        conditions: list[Any] = []
        if pipeline_id is not None:
            conditions.append(Screenshot.pipeline_id == pipeline_id)
        if trigger is not None:
            conditions.append(Screenshot.trigger == trigger)
        if since is not None:
            conditions.append(Screenshot.created_at >= since)
        items_stmt = (
            select(Screenshot)
            .where(*conditions)
            .order_by(Screenshot.created_at.desc(), Screenshot.id.desc())
        )
        count_stmt = (
            select(func.count()).select_from(Screenshot).where(*conditions)
        )
        return await self.paginate(items_stmt, count_stmt, page=page, size=size)

    async def list_by_pipeline(self, pipeline_id: str) -> list[Screenshot]:
        """流水线的全部截图，按 ``created_at`` 升序（按任务发生顺序看现场）。

        **不过滤 ``deleted_at``**：流水线详情页要显示「此处有一张已过期的截图」
        （docs/05 §6.7），由调用方根据 ``deleted_at`` 决定返回 410 还是图像。
        走 ``ix_screenshot_pipeline_id_created_at``。
        """
        result = await self.session.execute(
            select(Screenshot)
            .where(Screenshot.pipeline_id == pipeline_id)
            .order_by(Screenshot.created_at.asc(), Screenshot.id.asc())
        )
        return list(result.scalars().all())

    async def list_older_than(self, cutoff: datetime) -> list[Screenshot]:
        """``created_at < cutoff`` 且**文件还在**（``deleted_at IS NULL``）的记录，
        按 ``created_at`` 升序 —— 给保留策略按「最旧优先」删文件用（docs/04 §10.3）。

        已打 ``deleted_at`` 的不再返回：文件已经删过一次，重复删是无意义 IO，
        也会让「超过大小上限」的循环反复处理同一批记录。
        """
        result = await self.session.execute(
            select(Screenshot)
            .where(Screenshot.created_at < cutoff, Screenshot.deleted_at.is_(None))
            .order_by(Screenshot.created_at.asc(), Screenshot.id.asc())
        )
        return list(result.scalars().all())

    async def total_size_active(self) -> int:
        """``deleted_at IS NULL`` 的记录 ``size_bytes`` 之和，空表返回 0。

        保留策略用它判断总大小上限（默认 512 MB）；已清理的记录不再计体积。
        """
        result = await self.session.execute(
            select(func.coalesce(func.sum(Screenshot.size_bytes), 0)).where(
                Screenshot.deleted_at.is_(None)
            )
        )
        return int(result.scalar_one())

    async def mark_deleted(self, ids: Sequence[str]) -> int:
        """给文件已被清理的记录打 ``deleted_at``，返回**本次新标记**的条数。

        条件里带 ``deleted_at IS NULL``：重复标记同一 id 返回 0 且不刷新时间戳
        （``deleted_at`` 是「文件何时被清理」的事实，重写会把它变成「最后一次
        调用清理任务的时间」）。空 id 列表直接返回 0。**不 commit** —— 文件删除
        与打标记由清理服务决定提交时机（M2-12）。
        """
        targets = [screenshot_id for screenshot_id in ids if screenshot_id]
        if not targets:
            return 0
        result = await self.session.execute(
            update(Screenshot)
            .where(Screenshot.id.in_(targets), Screenshot.deleted_at.is_(None))
            .values(deleted_at=utcnow())
        )
        return result.rowcount

    async def purge_before(self, cutoff: datetime) -> int:
        """删除 ``created_at < cutoff`` 的截图**记录**，返回条数（不 commit）。

        记录本身随流水线的保留策略一起清理（docs/04 §10.3）：文件保留 7 天、
        记录跟着 90 天的流水线历史走。调用方必须先用
        :meth:`list_older_than` + :meth:`mark_deleted` 处理完文件再调本方法，
        否则记录先没了、文件就成了永远找不到的孤儿；这里不替调用方做顺序决策，
        只按给定的 cutoff 删记录。
        """
        result = await self.session.execute(
            delete(Screenshot).where(Screenshot.created_at < cutoff),
            execution_options={"synchronize_session": False},
        )
        return result.rowcount
