"""``cost_ledger`` 仓储：模型调用的成本流水（v3 docs/03 §4.2、docs/04 §9）。

一次模型调用一行，只追加。这里只负责「怎么读写」，**不做任何成本计算与预算
判定**：``cost_micros`` 由 ``llm.cost.compute_cost_micros`` 算好再传进来，
「超没超预算」属于 ``BudgetMiddleware`` / ``check_budget``。

两条区间语义写死在这里，调用方不必猜：

- :meth:`CostLedgerRepository.sum_cost_since` 与 :meth:`CostLedgerRepository.rows_between`
  的 ``since`` 都是**闭区间**，``rows_between`` 的 ``until`` 是**开区间**
  （``since <= created_at < until``），因此按天汇总时 ``until`` 直接传下一天 0 点，
  相邻两天不会重复计同一行。
- :meth:`CostLedgerRepository.purge_before` 删 ``created_at < cutoff`` 的行，
  返回删除条数。

事务纪律（docs/04 §9）：所有方法都不 ``commit()``，由调用方决定事务边界。
"""

from datetime import datetime

from sqlalchemy import delete, func, select

from maa_api.db.models import CostLedgerEntry
from maa_api.db.repositories.base import BaseRepository


class CostLedgerRepository(BaseRepository):
    """成本账本的追加、区间汇总与按保留期清理。"""

    async def add(self, entry: CostLedgerEntry) -> CostLedgerEntry:
        """追加一行，返回受会话管理的副本；只 ``flush`` 不 ``commit``。

        ``merge()`` 与其它仓储的 ``create`` 同一套理由：入参保持 transient，
        ``flush`` 之后自增 ``id`` 落在返回值上。成本计算不在这里做。
        """
        stored = await self.session.merge(entry)
        await self.session.flush()
        return stored

    async def sum_cost_since(self, since: datetime, currency: str) -> int:
        """``created_at >= since`` 且币种相同的行的 ``cost_micros`` 之和。

        没有行时返回 ``0``。币种必须显式传：预算只统计一种币种，混币种相加
        没有意义（v3 docs/03 §4.2）。
        """
        result = await self.session.execute(
            select(func.coalesce(func.sum(CostLedgerEntry.cost_micros), 0)).where(
                CostLedgerEntry.created_at >= since,
                CostLedgerEntry.currency == currency,
            )
        )
        return int(result.scalar_one() or 0)

    async def rows_between(
        self, since: datetime, until: datetime
    ) -> list[CostLedgerEntry]:
        """``since <= created_at < until`` 的全部行，按时间升序（同刻按 id 升序）。

        服务层按天 / 代理 / 模型 / 角色分组汇总时用它一次取出区间数据，避免为
        每种分组各写一条 SQL。
        """
        result = await self.session.execute(
            select(CostLedgerEntry)
            .where(
                CostLedgerEntry.created_at >= since,
                CostLedgerEntry.created_at < until,
            )
            .order_by(CostLedgerEntry.created_at.asc(), CostLedgerEntry.id.asc())
        )
        return list(result.scalars().all())

    async def purge_before(self, cutoff: datetime) -> int:
        """删除 ``created_at < cutoff`` 的行，返回删除条数（不 ``commit``）。"""
        result = await self.session.execute(
            delete(CostLedgerEntry).where(CostLedgerEntry.created_at < cutoff),
            execution_options={"synchronize_session": False},
        )
        return max(int(result.rowcount or 0), 0)
