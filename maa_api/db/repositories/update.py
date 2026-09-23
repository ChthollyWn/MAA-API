"""``update_record`` 仓储：三种热更新的历史与并发互斥（docs/04 §5.11、§6、§9）。

四条硬性语义，写错会在 M7 才暴雷：

- **同一 ``target`` 同时只允许一条 ``running`` 记录**，由部分唯一索引
  ``uq_update_record_running_target`` 在数据库层强制，不是服务层「先查后插」。
  并发两个 ``POST /api/updates/core`` 时第二个 :meth:`UpdateRepository.create`
  直接撞 ``IntegrityError``，服务层捕获后转 ``409 UPDATE_ALREADY_RUNNING``
  （docs/04 §5.11）。仓储把 ``IntegrityError`` 原样抛出、不翻译成别的异常类型，
  否则服务层无法把「同 target 已在跑」与其他数据库错误区分开。
- **终态不可再变**：:meth:`UpdateRepository.mark_terminal` 用
  ``WHERE status IN (pending, running)`` 兜状态机，``bool`` 表示流转是否被接受。
  传来非终态（如 ``running``）直接拒绝，避免绕过状态机。
- **``progress`` / ``phase`` 的节流是服务层职责**（下载阶段每 500 ms 或每 1%
  才写一次库，其余时间只走 WebSocket 广播，docs/04 §5.11）。:meth:`update_progress`
  只负责「把给到的列写下去」，不做计时、不去重、不比较新旧值；不节流地高频调用
  会把 SQLite 的写锁打满。
- :meth:`UpdateRepository.update_progress` 的 ``None`` 表示**本次不改这一列**，
  而不是把它置 NULL：这四个字段只在一次运行内单调推进，没有清空需求；调用方
  每次只传发生变化的列即可，其余列保持原值。

当前状态由服务层在 :meth:`create` 时直接给（本仓储不提供 ``pending → running``
的单独流转方法），``mark_terminal`` 负责收口终态；``phase`` / ``progress`` /
``bytes_*`` 由 :meth:`update_progress` 推进。

事务纪律（docs/04 §9）：所有方法都不 ``commit()``，由服务层决定事务边界。
"""

from sqlalchemy import func, select, update

from maa_api.db.models import UpdateRecord, utcnow
from maa_api.db.repositories.base import BaseRepository, Page
from maa_api.domain.enums import UpdatePhase, UpdateStatus, UpdateTarget

# 可以继续推进运行状态的起始状态：终态不可再变（docs/04 §9）。
NON_TERMINAL_UPDATE_STATUSES = (UpdateStatus.PENDING, UpdateStatus.RUNNING)

# ``mark_terminal`` 接受的终态：``skipped``（已是最新，未实际执行）也是终态。
TERMINAL_UPDATE_STATUSES = (
    UpdateStatus.SUCCESS,
    UpdateStatus.FAILED,
    UpdateStatus.CANCELLED,
    UpdateStatus.SKIPPED,
)


class UpdateRepository(BaseRepository):
    """``update_record`` 表的读写：创建、当前运行中、历史分页、进度与终态。"""

    async def create(self, record: UpdateRecord) -> UpdateRecord:
        """写入一条更新记录，返回受会话管理的副本；只 ``flush`` 不 ``commit``。

        ``flush`` 不是可省的：部分唯一索引的冲突必须在 ``create()`` 内以
        ``IntegrityError`` 的形式抛出，服务层才能在同一个 ``try`` 里把它转成
        ``409``；拖到调用方 ``commit()`` 才发现冲突，错误归属就模糊了。

        与 :meth:`PipelineRepository.create` 同样用 ``Session.merge()`` 接收入参
        （M2-07 实测）：入参保持 transient，提交后字段仍可直接读，调用方不必为了
        拿 ``id`` 再 await 一次刷新；``merge()`` 的返回副本才是会话管理的对象。

        记录以 ``running`` 状态创建且 ``started_at`` 为空时补 ``utcnow()``：
        更新记录没有 ``pending → running`` 的流转（见模块文档），开始时刻就是
        创建时刻，显式传入的 ``started_at`` 不被覆盖。
        """
        if record.status == UpdateStatus.RUNNING and record.started_at is None:
            record.started_at = utcnow()
        stored = await self.session.merge(record)
        await self.session.flush()
        return stored

    async def get(self, record_id: str) -> UpdateRecord | None:
        """按主键取更新记录，没有返回 ``None``。

        ``populate_existing=True``：:meth:`update_progress` / :meth:`mark_terminal`
        都走 Core ``UPDATE``，不强制从库里刷新就会拿到身份映射里的陈旧实例
        （M2-08 实测），而调用方常在一次请求里先写进度再回读。
        """
        return await self.session.get(
            UpdateRecord, record_id, populate_existing=True
        )

    async def current(self, target: UpdateTarget | str) -> UpdateRecord | None:
        """该 ``target`` 当前 ``running`` 的记录；没有返回 ``None``。

        部分唯一索引保证每个 target 至多一条 ``running``，所以这里 ``LIMIT 1``
        不是「任选一条」而是唯一解（复用 ``uq_update_record_running_target``，
        docs/04 §6）。同时存在新近的成功记录时也只返回 ``running`` 的那条 ——
        冲突判定看的是「现在有没有在跑」，不是「最后一条是什么」。
        """
        result = await self.session.execute(
            select(UpdateRecord)
            .where(
                UpdateRecord.target == target,
                UpdateRecord.status == UpdateStatus.RUNNING,
            )
            .order_by(
                UpdateRecord.started_at.desc(),
                UpdateRecord.created_at.desc(),
                UpdateRecord.id.desc(),
            )
            .limit(1)
            .execution_options(populate_existing=True)
        )
        return result.scalars().first()

    async def list(
        self,
        *,
        target: UpdateTarget | str | None = None,
        status: UpdateStatus | str | None = None,
        page: int = 1,
        size: int = 20,
    ) -> Page[UpdateRecord]:
        """更新历史：可按 ``target`` / ``status`` 组合过滤后分页。

        ``total`` 与列表共享同一组过滤条件；目标过滤贴着
        ``ix_update_record_target_created_at(target, created_at)``（docs/04 §6），
        同秒并列用 ``id DESC`` 兜稳定次序。
        """
        conditions = []
        if target is not None:
            conditions.append(UpdateRecord.target == target)
        if status is not None:
            conditions.append(UpdateRecord.status == UpdateStatus(status))

        items_stmt = (
            select(UpdateRecord)
            .where(*conditions)
            .order_by(UpdateRecord.created_at.desc(), UpdateRecord.id.desc())
            # 历史页常在 update_progress / mark_terminal 之后立刻回读，强制取库里的值
            .execution_options(populate_existing=True)
        )
        count_stmt = (
            select(func.count()).select_from(UpdateRecord).where(*conditions)
        )
        return await self.paginate(items_stmt, count_stmt, page=page, size=size)

    async def update_progress(
        self,
        record_id: str,
        *,
        phase: UpdatePhase | str | None = None,
        progress: int | None = None,
        bytes_total: int | None = None,
        bytes_done: int | None = None,
    ) -> bool:
        """推进 ``phase`` / ``progress`` / ``bytes_*``，返回是否写到了记录。

        ``None`` = 本次不改这一列（见模块文档），只有实际给到的列进入 ``UPDATE``；
        四个参数全为 ``None`` 时不发语句、返回 ``False``。

        ``WHERE status IN (pending, running)``：节流后的进度写入可能与
        ``mark_terminal`` 并发，迟到的回调不得把 ``phase`` / ``progress`` 写回
        终态记录（否则失败的更新会显示「下载 87%」）。记录不存在或已终态都返回
        ``False``，服务层据此丢弃这次进度、不必报错。

        ``progress`` 是 0–100 的整数，取值范围由服务层校验，仓储不夹取；``phase``
        走 :class:`UpdatePhase` 校验，写错枚举值立刻 ``ValueError`` 而不是静默落库。
        """
        values: dict = {}
        if phase is not None:
            values["phase"] = UpdatePhase(phase)
        if progress is not None:
            values["progress"] = int(progress)
        if bytes_total is not None:
            values["bytes_total"] = int(bytes_total)
        if bytes_done is not None:
            values["bytes_done"] = int(bytes_done)
        if not values:
            return False

        result = await self.session.execute(
            update(UpdateRecord)
            .where(
                UpdateRecord.id == record_id,
                UpdateRecord.status.in_(NON_TERMINAL_UPDATE_STATUSES),
            )
            .values(**values)
        )
        return result.rowcount > 0

    async def mark_terminal(
        self,
        record_id: str,
        status: UpdateStatus | str,
        *,
        error_code: str | None = None,
        error_message: str | None = None,
    ) -> bool:
        """把更新记录置入终态，返回状态机是否接受了这次流转（docs/04 §9）。

        ``status`` 不是终态时直接拒绝（``False``）；``WHERE status IN (pending,
        running)`` 在数据库层保证终态不可再变，第二次调用返回 ``False``。
        ``error_code`` / ``error_message`` 随本次流转一起写（``None`` 即清空，
        成功终态本就没有错误信息）；``finished_at`` 由仓储补 ``utcnow()``。
        """
        target = UpdateStatus(status)
        if target not in TERMINAL_UPDATE_STATUSES:
            return False

        result = await self.session.execute(
            update(UpdateRecord)
            .where(
                UpdateRecord.id == record_id,
                UpdateRecord.status.in_(NON_TERMINAL_UPDATE_STATUSES),
            )
            .values(
                status=target,
                finished_at=utcnow(),
                error_code=error_code,
                error_message=error_message,
            )
        )
        return result.rowcount > 0
