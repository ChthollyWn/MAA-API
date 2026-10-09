"""``agent_knowledge`` 仓储：Agent 知识库的覆盖层（规格 §4.1、docs/04 §9）。

这张表只存用户的改动（内置条目随代码发布），所以仓储只有四件事：读一行、读全表、
整行写入、删一行，**不做合并**：「哪些列是覆盖、覆盖之后生效值是什么」是业务
决策，属于 :class:`maa_api.agent.knowledge.KnowledgeService`。与 ``setting``
仓储同构：表里没有某一行就意味着「用下层的值」，因此不把内置内容灌进表里。

事务纪律（docs/04 §9）：所有方法都不 ``commit()``，由调用方决定事务边界。
"""

from sqlalchemy import delete, select
from sqlalchemy.dialects.sqlite import insert as sqlite_insert

from maa_api.db.models import AgentKnowledge
from maa_api.db.repositories.base import BaseRepository


class AgentKnowledgeRepository(BaseRepository):
    """知识库覆盖行的读写（规格 §4.1）。"""

    async def get(self, entry_id: str) -> AgentKnowledge | None:
        """返回 ``entry_id`` 的覆盖行；表里没有这一行时返回 ``None``。

        返回 ``None`` 的含义只有一个：**用户没改过这条**（内置条目）或**这条
        不存在**（自定义条目）；两者的区分由服务层按内置 id 集合判定。
        """
        return await self.session.get(AgentKnowledge, entry_id)

    async def all(self) -> list[AgentKnowledge]:
        """全表覆盖行，按 ``id`` 升序。

        上限判定与 ``render_context`` 都要看全表（≤ 几十行），排序只为让日志与
        测试可读；真正的展示顺序由服务层按 kind、id 排。
        """
        result = await self.session.execute(
            select(AgentKnowledge).order_by(AgentKnowledge.id.asc())
        )
        return list(result.scalars().all())

    async def upsert(self, entry: AgentKnowledge) -> None:
        """整行写入：存在则覆盖可写列，不存在则插入。

        用 SQLite 的 ``INSERT ... ON CONFLICT(id) DO UPDATE`` 一条语句完成，
        ``created_at`` 不在更新列里：首次写入的时间要留住，「这条覆盖是什么
        时候建的」不该被每次编辑刷新。``set_`` 里的 NULL 是有意义的：它表示
        「这一列回到继承内置值」，所以不能跳过 NULL 列。
        """
        statement = sqlite_insert(AgentKnowledge).values(
            id=entry.id,
            kind=entry.kind,
            title=entry.title,
            when_to_use=entry.when_to_use,
            body=entry.body,
            enabled=entry.enabled,
            created_at=entry.created_at,
            updated_at=entry.updated_at,
        )
        statement = statement.on_conflict_do_update(
            index_elements=[AgentKnowledge.id],
            set_={
                "kind": statement.excluded.kind,
                "title": statement.excluded.title,
                "when_to_use": statement.excluded.when_to_use,
                "body": statement.excluded.body,
                "enabled": statement.excluded.enabled,
                "updated_at": statement.excluded.updated_at,
            },
        )
        await self.session.execute(statement)

    async def delete(self, entry_id: str) -> bool:
        """删除一行，返回是否真的删掉了（``False`` = 表里本来就没有）。

        对内置条目来说删掉即恢复默认，对自定义条目来说就是删除；服务层负责
        区分这两种语义，仓储不判断。
        """
        result = await self.session.execute(
            delete(AgentKnowledge).where(AgentKnowledge.id == entry_id)
        )
        return result.rowcount > 0
