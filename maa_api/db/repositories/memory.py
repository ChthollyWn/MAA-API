"""``memory_item`` 仓储：长期记忆的读写（M79 规格 §4.3、v3 docs/03 §10.2）。

三条纪律照 ``base.py`` 与既有仓储：

- **不 commit**：事务边界归调用方（``MemoryService`` 把状态变更、取代关系与
  读取写在同一次会话里）。``add`` 只 ``merge() + flush()``。
- **状态机不在这里**：仓储只做查询与整行写入；「只有 proposed 能审核」「只有
  active 能改」由 :class:`~maa_api.agent.memory.service.MemoryService` 判断。
- **读方法返回 ORM 实例**，分页统一返回 :class:`Page`。
"""

from __future__ import annotations

from sqlalchemy import delete, func, or_, select

from maa_api.db.models import MemoryItem
from maa_api.db.repositories.base import BaseRepository, Page


class MemoryRepository(BaseRepository):
    """``memory_item`` 表的读写（M79 规格 §4.1）。"""

    async def get(self, memory_id: str) -> MemoryItem | None:
        """按主键取一条记忆；没有时返回 ``None``。"""
        return await self.session.get(MemoryItem, memory_id, populate_existing=True)

    async def all(self) -> list[MemoryItem]:
        """全表行，按 ``created_at``、``id`` 升序。

        记忆表规模很小（用户审核过的事实），服务去重要看全表；排序只为让日志与
        测试可读。
        """
        result = await self.session.execute(
            select(MemoryItem).order_by(
                MemoryItem.created_at.asc(), MemoryItem.id.asc()
            )
        )
        return list(result.scalars().all())

    async def add(self, memory: MemoryItem) -> MemoryItem:
        """写入一条记忆，只 ``flush`` 不 ``commit``：调用方决定事务边界。"""
        stored = await self.session.merge(memory)
        await self.session.flush()
        return stored

    async def delete(self, memory_id: str) -> bool:
        """物理删除一行，返回是否真的删掉了。"""
        result = await self.session.execute(
            delete(MemoryItem).where(MemoryItem.id == memory_id)
        )
        return result.rowcount > 0

    async def list_filtered(
        self,
        *,
        status: str | None = None,
        kind: str | None = None,
        q: str | None = None,
        page: int = 1,
        size: int = 20,
    ) -> Page[MemoryItem]:
        """按 ``status`` / ``kind`` / ``q`` 过滤，按 ``updated_at DESC, id DESC`` 分页。

        ``q`` 在正文与分类键里做子串匹配；三个条件都可选，都不传时返回全部状态。
        """
        conditions = []
        if status is not None:
            conditions.append(MemoryItem.status == str(status))
        if kind is not None:
            conditions.append(MemoryItem.kind == str(kind))
        text = q.strip() if isinstance(q, str) else ""
        if text:
            conditions.append(
                or_(
                    MemoryItem.content.contains(text),
                    MemoryItem.subject.contains(text),
                )
            )
        statement = (
            select(MemoryItem)
            .where(*conditions)
            .order_by(MemoryItem.updated_at.desc(), MemoryItem.id.desc())
        )
        count_statement = (
            select(func.count()).select_from(MemoryItem).where(*conditions)
        )
        return await self.paginate(
            statement, count_statement, page=page, size=size
        )
