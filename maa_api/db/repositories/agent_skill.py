"""``agent_skill`` 仓储：技能的覆盖层、用户技能与生成草稿（M78 规格 §4.1）。

内置技能随代码发布（``maa_api/agent/skills/builtin/<id>/SKILL.md``），表里只存：

- ``origin='override'``：对内置技能的覆盖行，只写与内置不同的列，其余为 NULL；
- ``origin='user'``：用户自建技能，各列必填；
- ``origin='generated'``：M95 经验沉淀生成的草稿（当前里程碑还没有写入方）。

与 :class:`~maa_api.db.repositories.agent_knowledge.AgentKnowledgeRepository`
同构：读一行、读全表、整行写入、删一行，**不做合并**——「哪些列是覆盖、覆盖之后
生效值是什么」是业务决策，属于
:class:`~maa_api.agent.skills.registry.SkillRegistry`。

事务纪律（docs/04 §9）：所有方法都不 ``commit()``，由调用方决定事务边界。
"""

from sqlalchemy import delete, select
from sqlalchemy.dialects.sqlite import insert as sqlite_insert

from maa_api.db.models import AgentSkill
from maa_api.db.repositories.base import BaseRepository


class AgentSkillRepository(BaseRepository):
    """``agent_skill`` 表的读写（M78 规格 §4.1）。"""

    async def get(self, skill_id: str) -> AgentSkill | None:
        """返回 ``skill_id`` 的行；表里没有这一行时返回 ``None``。

        ``None`` 的含义由服务层按内置 id 集合区分：内置技能表示「用内置原文」，
        自定义技能表示「不存在」。
        """
        return await self.session.get(AgentSkill, skill_id)

    async def all(self) -> list[AgentSkill]:
        """全表行，按 ``id`` 升序。

        表最多几十行、每轮全量读（技能索引与合并都要看全表）；排序只为让日志与
        测试可读。
        """
        result = await self.session.execute(
            select(AgentSkill).order_by(AgentSkill.id.asc())
        )
        return list(result.scalars().all())

    async def upsert(self, skill: AgentSkill) -> None:
        """整行写入：存在则覆盖可写列，不存在则插入。

        用 SQLite 的 ``INSERT ... ON CONFLICT(id) DO UPDATE`` 一条语句完成。
        ``created_at`` 不在更新列里：首次写入的时间要留住。``set_`` 里的 NULL
        是有意义的：它表示「这一列回到继承内置值」，所以不能跳过 NULL 列。
        """
        statement = sqlite_insert(AgentSkill).values(
            id=skill.id,
            title=skill.title,
            description=skill.description,
            body=skill.body,
            tools=skill.tools,
            agents=skill.agents,
            origin=skill.origin,
            status=skill.status,
            enabled=skill.enabled,
            created_at=skill.created_at,
            updated_at=skill.updated_at,
        )
        statement = statement.on_conflict_do_update(
            index_elements=[AgentSkill.id],
            set_={
                "title": statement.excluded.title,
                "description": statement.excluded.description,
                "body": statement.excluded.body,
                "tools": statement.excluded.tools,
                "agents": statement.excluded.agents,
                "origin": statement.excluded.origin,
                "status": statement.excluded.status,
                "enabled": statement.excluded.enabled,
                "updated_at": statement.excluded.updated_at,
            },
        )
        await self.session.execute(statement)

    async def delete(self, skill_id: str) -> bool:
        """删除一行，返回是否真的删掉了（``False`` = 表里本来就没有）。

        对内置技能来说删掉即恢复默认，对自定义技能来说就是删除；服务层负责区分
        这两种语义，仓储不判断。
        """
        result = await self.session.execute(
            delete(AgentSkill).where(AgentSkill.id == skill_id)
        )
        return result.rowcount > 0
