"""``compaction_checkpoint`` 仓储（v3 M74 规格 §4.1）。

检查点是「seq ≤ upto_seq 的消息由 summary 替代」的持久化记录：读侧取 active 的
一条，列表侧按 ``created_at`` 降序，写侧在压缩事务里先 ``supersede_active`` 再
``add``。事务纪律与其余仓储一致：**不 commit**，由 ``Compactor`` 决定事务边界
（置旧 + 插新必须在同一事务里，否则会话可能短暂出现两个 active 或零个 active）。
"""

from sqlalchemy import select, update

from maa_api.db.models import CompactionCheckpoint
from maa_api.db.repositories.base import BaseRepository
from maa_api.domain.enums import CheckpointStatus


class CompactionCheckpointRepository(BaseRepository):
    """压缩检查点的读写；「一个会话最多一个 active」由调用方在同一事务里维护。"""

    async def active_for_session(self, session_id: str) -> CompactionCheckpoint | None:
        """取会话当前 active 的检查点；没有返回 ``None``（读历史时走全量）。"""
        result = await self.session.execute(
            select(CompactionCheckpoint)
            .where(
                CompactionCheckpoint.session_id == session_id,
                CompactionCheckpoint.status == CheckpointStatus.ACTIVE,
            )
            .order_by(CompactionCheckpoint.created_at.desc())
            .limit(1)
        )
        return result.scalar_one_or_none()

    async def list_for_session(self, session_id: str) -> list[CompactionCheckpoint]:
        """取会话的全部检查点，**按 ``created_at`` 降序**（列表接口用）。"""
        result = await self.session.execute(
            select(CompactionCheckpoint)
            .where(CompactionCheckpoint.session_id == session_id)
            .order_by(CompactionCheckpoint.created_at.desc())
        )
        return list(result.scalars().all())

    async def supersede_active(self, session_id: str) -> int:
        """把会话当前所有 active 检查点改成 ``superseded``，返回改到的行数。

        用 Core ``UPDATE`` 一条语句完成（不是先查后逐行改）：压缩事务里调用方随后
        会插入新行，两条语句在同一事务内提交，会话不会出现两个 active。
        """
        result = await self.session.execute(
            update(CompactionCheckpoint)
            .where(
                CompactionCheckpoint.session_id == session_id,
                CompactionCheckpoint.status == CheckpointStatus.ACTIVE,
            )
            .values(status=CheckpointStatus.SUPERSEDED)
        )
        return int(result.rowcount or 0)

    async def add(self, checkpoint: CompactionCheckpoint) -> CompactionCheckpoint:
        """追加一个检查点并 ``flush``（不 commit），返回受会话管理的实例。"""
        stored = await self.session.merge(checkpoint)
        await self.session.flush()
        return stored
