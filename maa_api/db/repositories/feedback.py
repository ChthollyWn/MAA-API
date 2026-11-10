"""``run_feedback`` 表的仓储（M99 规格 §4.1、§4.2、§4.5.4）。

三条纪律照 ``base.py`` 与 ``goals.py``：

- **不 commit**：事务边界归调用方；写方法只 ``flush()``。
- **业务决策不在这里**：``reasons`` 去重与个数上限、``comment`` 空串归一、运行
  是否存在，都由路由层与服务层判断；仓储只负责「按 ``run_id`` 覆盖」。
- **读方法返回 ORM 实例**；``list_since`` 是列表查询，不套分页。

``upsert`` 的口径（spec §4.5.4）：

- 不存在则插入；已存在则只改 ``rating`` / ``reasons`` / ``comment`` / ``updated_at``，
  **``created_at`` 保留**，因此同一运行重复提交不会新增行。
- ``updated_at`` 取入参的值（调用方按请求时刻构造），仓储不自己生成时间。
- ``list_since`` 左闭（``created_at >= since``），按 ``created_at DESC, id DESC``
  排序，并尊重 ``limit``。
"""

from __future__ import annotations

from datetime import datetime

from sqlalchemy import select

from maa_api.db.models import RunFeedback
from maa_api.db.repositories.base import BaseRepository


class RunFeedbackRepository(BaseRepository):
    """``run_feedback`` 的读写：一次运行至多一条评价，重复提交覆盖。"""

    async def get_by_run(self, run_id: str) -> RunFeedback | None:
        """按 ``run_id`` 取评价；没有评价返回 ``None``（路由层转 ``{item: null}``）。"""
        result = await self.session.execute(
            select(RunFeedback).where(RunFeedback.run_id == run_id)
        )
        return result.scalar_one_or_none()

    async def upsert(self, feedback: RunFeedback) -> RunFeedback:
        """按 ``run_id`` 覆盖（不存在则插入）；只 ``flush``，不 commit。"""
        stored = await self.get_by_run(feedback.run_id)
        if stored is None:
            self.session.add(feedback)
            await self.session.flush()
            return feedback
        stored.rating = feedback.rating
        stored.reasons = list(feedback.reasons)
        stored.comment = feedback.comment
        stored.updated_at = feedback.updated_at
        await self.session.flush()
        return stored

    async def list_since(self, since: datetime, limit: int) -> list[RunFeedback]:
        """取 ``created_at >= since`` 的评价，最新在前，最多 ``limit`` 条。"""
        result = await self.session.execute(
            select(RunFeedback)
            .where(RunFeedback.created_at >= since)
            .order_by(RunFeedback.created_at.desc(), RunFeedback.id.desc())
            .limit(max(int(limit), 0))
        )
        return list(result.scalars().all())
