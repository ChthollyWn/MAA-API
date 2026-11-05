"""``goal`` / ``goal_milestone`` 两张表的仓储（M82 规格 §4.1、docs/v3/03 §11.4）。

三条纪律照 ``base.py`` 与既有仓储：

- **不 commit**：事务边界归调用方（``GoalService`` 要把目标、规划工单、里程碑与
  复盘快照写在同一事务里）。所有写方法只 ``flush()``。
- **业务决策不在这里**：目标能不能改、里程碑该是什么状态、暂停时停用哪些定时任务
  都由 ``GoalService`` 判断；仓储只回答「有哪些行」与「整行/整组怎么写」。
- **读方法返回 ORM 实例**，分页统一返回 :class:`Page`。

两个集合写法的边界值得记住：

- ``replace_all`` 是**整体替换**：先删该目标的全部里程碑，再把入参逐个挂到
  ``goal_id`` 上。入参必须是 transient 的新行（服务层按规划结果重建），仓储不合并
  旧行，也不替调用方编号 ``ord``。
- ``set_status`` 按 ``(goal_id, ord)`` 条件更新，返回是否改到行；``ord`` 不存在时
  服务层按「跳过」处理（spec §4.5.3）。
"""

from __future__ import annotations

from collections.abc import Collection, Sequence

from sqlalchemy import delete, func, select, update

from maa_api.db.models import Goal, GoalMilestone, utcnow
from maa_api.db.repositories.base import BaseRepository, Page


class GoalRepository(BaseRepository):
    """``goal`` 的读写（M82 规格 §4.1）。"""

    async def create(self, goal: Goal) -> Goal:
        """写入一个目标，只 ``flush`` 不 ``commit``（与 ``ScheduleRepository`` 同口径）。"""
        stored = await self.session.merge(goal)
        await self.session.flush()
        return stored

    async def get(self, goal_id: str) -> Goal | None:
        """按主键取目标；``populate_existing=True`` 保证看到刚写入的值。"""
        return await self.session.get(Goal, goal_id, populate_existing=True)

    async def page(
        self,
        statuses: Collection[str] | None = None,
        page: int = 1,
        size: int = 20,
    ) -> Page[Goal]:
        """按 ``updated_at DESC, id DESC`` 分页；可选按状态集合过滤（目标列表）。"""
        conditions = []
        if statuses is not None:
            conditions.append(Goal.status.in_([str(status) for status in statuses]))
        statement = (
            select(Goal)
            .where(*conditions)
            .order_by(Goal.updated_at.desc(), Goal.id.desc())
        )
        count_statement = select(func.count()).select_from(Goal).where(*conditions)
        return await self.paginate(statement, count_statement, page=page, size=size)

    async def list_by_status(self, statuses: Collection[str]) -> list[Goal]:
        """取指定状态的全部目标，``updated_at DESC, id DESC``（复盘 job 装载用）。"""
        result = await self.session.execute(
            select(Goal)
            .where(Goal.status.in_([str(status) for status in statuses]))
            .order_by(Goal.updated_at.desc(), Goal.id.desc())
        )
        return list(result.scalars().all())

    async def update(self, goal: Goal) -> Goal:
        """全量替换一个目标（服务层先 ``get``、改完再交回来）；不提交事务。"""
        stored = await self.session.merge(goal)
        await self.session.flush()
        return stored


class GoalMilestoneRepository(BaseRepository):
    """``goal_milestone`` 的读写（M82 规格 §4.1）。"""

    async def list_by_goal(self, goal_id: str) -> list[GoalMilestone]:
        """某个目标的全部里程碑，按 ``ord``、``id`` 升序（详情页与复盘都按它）。"""
        result = await self.session.execute(
            select(GoalMilestone)
            .where(GoalMilestone.goal_id == goal_id)
            .order_by(GoalMilestone.ord.asc(), GoalMilestone.id.asc())
        )
        return list(result.scalars().all())

    async def replace_all(
        self, goal_id: str, rows: Sequence[GoalMilestone]
    ) -> list[GoalMilestone]:
        """整体替换目标的里程碑：先删旧行，再插入 ``rows``（入参须是 transient 新行）。

        ``goal_id`` 由参数统一写入，调用方不必逐行设置；``ord`` 由调用方决定。
        返回插入后受会话管理的行（``flush`` 后 id 已就位）。
        """
        await self.session.execute(
            delete(GoalMilestone).where(GoalMilestone.goal_id == goal_id)
        )
        stored: list[GoalMilestone] = []
        for row in rows:
            row.goal_id = goal_id
            self.session.add(row)
            stored.append(row)
        await self.session.flush()
        return stored

    async def set_status(self, goal_id: str, ord: int, status: str) -> bool:
        """按 ``(goal_id, ord)`` 更新里程碑状态，顺带刷新 ``updated_at``。

        返回是否改到行；``ord`` 不存在时返回 ``False``，由服务层决定是否记 ``notes``。
        """
        result = await self.session.execute(
            update(GoalMilestone)
            .where(
                GoalMilestone.goal_id == goal_id,
                GoalMilestone.ord == int(ord),
            )
            .values(status=str(status), updated_at=utcnow())
        )
        return result.rowcount > 0
