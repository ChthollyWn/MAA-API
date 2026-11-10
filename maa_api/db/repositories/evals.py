"""``eval_run`` / ``eval_case_result`` 两张表的仓储（M99 规格 §4.1、§4.2）。

三条纪律照 ``base.py`` 与 ``goals.py``：

- **不 commit**：事务边界归调用方（发布、查询都可能在同一个请求事务里）。
  写方法只 ``flush()``。
- **业务决策不在这里**：这次评测能不能发布、失败率怎么算、详情要截断到多少条，
  都由服务层与 ``agent/evals`` 决定；仓储只回答「怎么整行覆盖」「有哪些行」。
- **读方法返回 ORM 实例**，分页统一返回 :class:`Page`。

两个集合写法的口径：

- ``upsert_with_cases`` 是**按主键覆盖**：``eval_run`` 同 id 只有一行（``merge`` 先
  查后写），旧 ``eval_case_result`` 先按 ``eval_run_id`` 整批删掉再 ``add_all``
  新行，因此重复发布同一份结果不会撞唯一约束、也不会留旧用例。
- ``page_runs`` 按 ``finished_at DESC, id DESC`` 排序（列表页默认顺序），
  ``suite`` / ``mode`` 为 ``None`` 时不过滤；``total`` 是过滤后的总数。
"""

from __future__ import annotations

from collections.abc import Sequence

from sqlalchemy import delete, func, select

from maa_api.db.models import EvalCaseResult, EvalRun
from maa_api.db.repositories.base import BaseRepository, Page


class EvalRunRepository(BaseRepository):
    """``eval_run`` 的读写（M99 规格 §4.2）。"""

    async def upsert_with_cases(
        self, run: EvalRun, cases: Sequence[EvalCaseResult]
    ) -> None:
        """按主键覆盖一次评测：先删旧 cases 再写新行；只 ``flush``，不 commit。

        入参 ``run`` 可以是 transient 行（``merge`` 会按主键决定插入还是覆盖），
        ``cases`` 必须是 transient 的新行，且 ``eval_run_id`` 已指向 ``run.id``。
        """
        stored = await self.session.merge(run)
        await self.session.execute(
            delete(EvalCaseResult).where(EvalCaseResult.eval_run_id == stored.id)
        )
        self.session.add_all(list(cases))
        await self.session.flush()

    async def page_runs(
        self,
        *,
        page: int,
        size: int,
        suite: str | None,
        mode: str | None,
    ) -> Page[EvalRun]:
        """按 ``finished_at DESC, id DESC`` 分页；可选按套件 / 运行模式过滤。"""
        conditions = []
        if suite is not None:
            conditions.append(EvalRun.suite == suite)
        if mode is not None:
            conditions.append(EvalRun.mode == mode)
        statement = (
            select(EvalRun)
            .where(*conditions)
            .order_by(EvalRun.finished_at.desc(), EvalRun.id.desc())
        )
        count_statement = select(func.count()).select_from(EvalRun).where(*conditions)
        return await self.paginate(statement, count_statement, page=page, size=size)

    async def get_run(self, run_id: str) -> EvalRun | None:
        """按主键取一次评测；``populate_existing=True`` 保证看到刚覆盖的值。"""
        return await self.session.get(EvalRun, run_id, populate_existing=True)


class EvalCaseRepository(BaseRepository):
    """``eval_case_result`` 的只读查询（M99 规格 §4.2）。"""

    async def list_by_run(self, run_id: str) -> list[EvalCaseResult]:
        """某次评测的全部用例，按自增 ``id`` 升序（发布顺序，详情页按它展示）。"""
        result = await self.session.execute(
            select(EvalCaseResult)
            .where(EvalCaseResult.eval_run_id == run_id)
            .order_by(EvalCaseResult.id.asc())
        )
        return list(result.scalars().all())
