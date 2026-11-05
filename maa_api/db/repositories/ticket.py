"""工单领域五个仓储：ticket / mandate / ticket_report / thread_subscription / agent_inbox。

M69 规格 §4.2。四条纪律照 ``base.py`` 与 ``agent.py``：

- **不 commit**：事务边界归调用方（``TicketService`` 要把工单、委托、汇报、订阅
  与收件箱写在同一事务里）。``create`` 只 ``merge() + flush()``，入参保持
  transient，返回受会话管理的实例。
- **状态机不在这里**：``update_if_status`` 只执行条件 ``UPDATE``，是否「已结束」
  由调用方按返回的 bool 决定；``worker_turn_running`` 只回答「有没有活动运行」。
- **读方法返回 ORM 实例**，分页统一返回 :class:`Page`；Core ``UPDATE`` 之后的读
  带 ``populate_existing``，避免身份映射里留着旧值。
- **委托不可变**（09 §2.1）：:class:`MandateRepository` 只有 ``create`` 与
  ``get_by_ticket``，**不提供 update / delete / set 方法**。
"""

from __future__ import annotations

from collections.abc import Collection, Iterable, Mapping, Sequence
from datetime import datetime
from typing import Any

from sqlalchemy import and_, delete, func, or_, select, update

from maa_api.db.models import (
    AgentInbox,
    AgentRun,
    Mandate,
    ThreadSubscription,
    Ticket,
    TicketReport,
)
from maa_api.db.repositories.agent import ACTIVE_RUN_STATUSES
from maa_api.db.repositories.base import BaseRepository, Page

#: 会补转的汇报状态（M69 规格 §4.2、§4.5.2）：progress 不转发；dropped 只在
#: 工单来源为 user 时转发，调用处用 ``join ticket`` 判断。
UNFORWARDED_REPORT_STATUSES: frozenset[str] = frozenset(
    {"waiting", "ready", "blocked", "failed"}
)

#: 已进入终态、可被保留策略清理的收件箱项（M69 规格 §4.5.7）。
FINISHED_INBOX_STATUSES: frozenset[str] = frozenset({"consumed", "dropped"})


class TicketRepository(BaseRepository):
    """``ticket`` 的读写；状态机与收尾语义在 ``TicketService``。"""

    async def create(self, ticket: Ticket) -> Ticket:
        """写入一个工单，只 ``flush`` 不 ``commit``（与 ``AgentSessionRepository`` 同口径）。"""
        stored = await self.session.merge(ticket)
        await self.session.flush()
        return stored

    async def get(self, ticket_id: str) -> Ticket | None:
        """按主键取工单；``populate_existing=True`` 保证看到条件更新后的值。"""
        return await self.session.get(Ticket, ticket_id, populate_existing=True)

    async def list(
        self,
        *,
        statuses: Collection[str] | None = None,
        kind: str | None = None,
        origin: str | None = None,
        page: int = 1,
        size: int = 20,
    ) -> Page[Ticket]:
        """按 ``updated_at DESC, id DESC`` 分页；三个过滤条件都可选。"""
        conditions = []
        if statuses is not None:
            conditions.append(Ticket.status.in_([str(status) for status in statuses]))
        if kind is not None:
            conditions.append(Ticket.kind == kind)
        if origin is not None:
            conditions.append(Ticket.origin == origin)
        stmt = (
            select(Ticket)
            .where(*conditions)
            .order_by(Ticket.updated_at.desc(), Ticket.id.desc())
        )
        count_stmt = select(func.count()).select_from(Ticket).where(*conditions)
        return await self.paginate(stmt, count_stmt, page=page, size=size)

    async def update_if_status(
        self,
        ticket_id: str,
        *,
        allowed: Collection[str],
        values: Mapping[str, Any],
    ) -> bool:
        """``UPDATE ticket SET values WHERE id=? AND status IN allowed``；返回是否改到行。

        条件更新是工单状态机的并发闸门：两个终态汇报同时到达时只有一个能把
        ``status`` 从非终态改走，输家拿到 ``False`` 并按「已结束」处理。空
        ``allowed`` / 空 ``values`` 都不发语句，直接返回 ``False``。
        """
        allowed_statuses = [str(status) for status in allowed]
        if not allowed_statuses or not values:
            return False
        result = await self.session.execute(
            update(Ticket)
            .where(
                Ticket.id == ticket_id,
                Ticket.status.in_(allowed_statuses),
            )
            .values(dict(values))
        )
        return result.rowcount > 0

    async def get_by_source_trigger(self, trigger_id: int) -> Ticket | None:
        """取由某条事件建出的工单；恢复时用它避免重复建单（M81 §4.5.2）。

        一条事件至多建一张工单，但历史数据可能没有唯一索引兜底，这里按
        ``id ASC`` 取第一条，不因多行报错。
        """
        result = await self.session.execute(
            select(Ticket)
            .where(Ticket.source_trigger_id == int(trigger_id))
            .order_by(Ticket.id.asc())
            .limit(1)
        )
        return result.scalars().first()

    async def list_by_goal(self, goal_id: str, *, limit: int = 20) -> list[Ticket]:
        """某个目标下的工单，``created_at DESC, id DESC`` 最多 ``limit`` 张（M82 §4.1）。

        目标的详情页只列最近 20 张：目标工单会随复盘累积，翻旧账不是详情页的职责。
        """
        result = await self.session.execute(
            select(Ticket)
            .where(Ticket.goal_id == goal_id)
            .order_by(Ticket.created_at.desc(), Ticket.id.desc())
            .limit(max(int(limit), 1))
        )
        return list(result.scalars().all())

    async def titles(self, ticket_ids: Iterable[str]) -> dict[str, str]:
        """批量取 ``id → title``；不存在的 id 不出现，空入参返回空 dict。"""
        ids = [str(ticket_id) for ticket_id in ticket_ids]
        if not ids:
            return {}
        result = await self.session.execute(
            select(Ticket.id, Ticket.title).where(Ticket.id.in_(ids))
        )
        return {str(ticket_id): str(title) for ticket_id, title in result.all()}

    async def worker_turn_running(self, worker_session_id: str | None) -> bool:
        """该会话是否有活动运行（``running`` / ``waiting_human``）；会话为空返回 ``False``。"""
        if not worker_session_id:
            return False
        result = await self.session.execute(
            select(AgentRun.id)
            .where(
                AgentRun.session_id == worker_session_id,
                AgentRun.status.in_(ACTIVE_RUN_STATUSES),
            )
            .limit(1)
        )
        return result.first() is not None


class MandateRepository(BaseRepository):
    """``mandate`` 的只追加读写；委托不可变，故没有 update / delete / set 方法。"""

    async def create(self, mandate: Mandate) -> Mandate:
        """写入委托；同一工单的第二条由 ``uq_mandate_ticket_id`` 拒绝。"""
        stored = await self.session.merge(mandate)
        await self.session.flush()
        return stored

    async def get_by_ticket(self, ticket_id: str) -> Mandate | None:
        """按工单取唯一委托；不存在返回 ``None``。"""
        result = await self.session.execute(
            select(Mandate).where(Mandate.ticket_id == ticket_id)
        )
        return result.scalar_one_or_none()


class TicketReportRepository(BaseRepository):
    """``ticket_report`` 的追加、读取与转发标记。"""

    async def create(self, report: TicketReport) -> TicketReport:
        """写入一条汇报，只 ``flush`` 不 ``commit``。"""
        stored = await self.session.merge(report)
        await self.session.flush()
        return stored

    async def recent(self, ticket_id: str, *, limit: int = 5) -> list[TicketReport]:
        """该工单最近 ``limit`` 条汇报，``id DESC``（详情页用）。"""
        result = await self.session.execute(
            select(TicketReport)
            .where(TicketReport.ticket_id == ticket_id)
            .order_by(TicketReport.id.desc())
            .limit(max(int(limit), 1))
            .execution_options(populate_existing=True)
        )
        return list(result.scalars().all())

    async def list(
        self, ticket_id: str, *, page: int = 1, size: int = 20
    ) -> Page[TicketReport]:
        """该工单的汇报分页，``created_at DESC, id DESC``。"""
        conditions = [TicketReport.ticket_id == ticket_id]
        stmt = (
            select(TicketReport)
            .where(*conditions)
            .order_by(TicketReport.created_at.desc(), TicketReport.id.desc())
            .execution_options(populate_existing=True)
        )
        count_stmt = (
            select(func.count()).select_from(TicketReport).where(*conditions)
        )
        return await self.paginate(stmt, count_stmt, page=page, size=size)

    async def mark_forwarded(self, report_id: int) -> bool:
        """把汇报标成已转发；返回是否改到行。"""
        result = await self.session.execute(
            update(TicketReport)
            .where(TicketReport.id == report_id)
            .values(forwarded=True)
        )
        return result.rowcount > 0

    async def list_unforwarded(self, *, limit: int = 50) -> list[TicketReport]:
        """待补转的汇报，``id ASC``，最多 ``limit`` 条。

        ``forwarded=false`` 且满足任一：状态是 waiting / ready / blocked / failed；
        或状态是 dropped 而所属工单 ``origin=user``。dropped 的工单来源要 join
        ``ticket`` 才能看到，trigger 等来源的取消汇报不补转。
        """
        result = await self.session.execute(
            select(TicketReport)
            .join(Ticket, Ticket.id == TicketReport.ticket_id)
            .where(
                TicketReport.forwarded.is_(False),
                or_(
                    TicketReport.status.in_(UNFORWARDED_REPORT_STATUSES),
                    and_(
                        TicketReport.status == "dropped",
                        Ticket.origin == "user",
                    ),
                ),
            )
            .order_by(TicketReport.id.asc())
            .limit(max(int(limit), 1))
            .execution_options(populate_existing=True)
        )
        return list(result.scalars().all())


class ThreadSubscriptionRepository(BaseRepository):
    """``thread_subscription`` 的订阅、停用与按话题键反查。"""

    async def upsert_active(
        self, ticket_id: str, thread_key: str, *, now: datetime
    ) -> ThreadSubscription:
        """没有就插入；已有（含已停用）就复用同一行并置 ``active=true``、``ended_at=NULL``。"""
        result = await self.session.execute(
            select(ThreadSubscription)
            .where(
                ThreadSubscription.ticket_id == ticket_id,
                ThreadSubscription.thread_key == thread_key,
            )
            .execution_options(populate_existing=True)
        )
        subscription = result.scalar_one_or_none()
        if subscription is not None:
            subscription.active = True
            subscription.ended_at = None
            await self.session.flush()
            return subscription
        stored = await self.session.merge(
            ThreadSubscription(
                ticket_id=ticket_id,
                thread_key=thread_key,
                active=True,
                created_at=now,
                ended_at=None,
            )
        )
        await self.session.flush()
        return stored

    async def deactivate(
        self, ticket_id: str, thread_key: str, *, now: datetime
    ) -> bool:
        """把一条**活动**订阅置为停用；本来就不活动或不存在时返回 ``False``。"""
        result = await self.session.execute(
            update(ThreadSubscription)
            .where(
                ThreadSubscription.ticket_id == ticket_id,
                ThreadSubscription.thread_key == thread_key,
                ThreadSubscription.active.is_(True),
            )
            .values(active=False, ended_at=now)
        )
        return result.rowcount > 0

    async def deactivate_for_ticket(self, ticket_id: str, *, now: datetime) -> int:
        """停用该工单的全部活动订阅（终态收尾），返回本次停用条数。"""
        result = await self.session.execute(
            update(ThreadSubscription)
            .where(
                ThreadSubscription.ticket_id == ticket_id,
                ThreadSubscription.active.is_(True),
            )
            .values(active=False, ended_at=now)
        )
        return max(int(result.rowcount or 0), 0)

    async def list_for_ticket(self, ticket_id: str) -> list[ThreadSubscription]:
        """该工单的全部订阅（含已停用），``id ASC``。"""
        result = await self.session.execute(
            select(ThreadSubscription)
            .where(ThreadSubscription.ticket_id == ticket_id)
            .order_by(ThreadSubscription.id.asc())
            .execution_options(populate_existing=True)
        )
        return list(result.scalars().all())

    async def active_ticket_ids(self, thread_key: str) -> list[str]:
        """订阅了该话题键的活动工单 id，``订阅 id ASC`` 且去重。"""
        result = await self.session.execute(
            select(ThreadSubscription.ticket_id)
            .where(
                ThreadSubscription.thread_key == thread_key,
                ThreadSubscription.active.is_(True),
            )
            .order_by(ThreadSubscription.id.asc())
        )
        return list(dict.fromkeys(str(ticket_id) for ticket_id in result.scalars()))


class AgentInboxRepository(BaseRepository):
    """``agent_inbox`` 的写入、领取与保留期清理。"""

    async def create(self, item: AgentInbox) -> AgentInbox:
        """写入一条收件箱项，只 ``flush`` 不 ``commit``。"""
        stored = await self.session.merge(item)
        await self.session.flush()
        return stored

    async def find_ref(self, kind: str, ref_id: str) -> AgentInbox | None:
        """按 ``kind + ref_id`` 取最早的一条收件箱项；M81 的幂等检查用它。

        ``ref_id`` 在收件箱里不唯一（同一事件会投给多个订阅工单），调用方只关心
        「有没有写过、写给了哪个会话」，因此按 ``id ASC`` 取第一条。
        """
        result = await self.session.execute(
            select(AgentInbox)
            .where(
                AgentInbox.kind == str(kind),
                AgentInbox.ref_id == str(ref_id),
            )
            .order_by(AgentInbox.id.asc())
            .limit(1)
        )
        return result.scalars().first()

    async def exists(self, kind: str, ref_id: str) -> bool:
        """是否已有 ``kind + ref_id`` 的收件箱项（M81 的幂等闸门）。"""
        return await self.find_ref(kind, ref_id) is not None

    async def pending_ids(self, session_id: str, *, limit: int) -> list[int]:
        """该会话最早的 ``limit`` 条 pending 项 id，``id ASC``。"""
        result = await self.session.execute(
            select(AgentInbox.id)
            .where(
                AgentInbox.session_id == session_id,
                AgentInbox.status == "pending",
            )
            .order_by(AgentInbox.id.asc())
            .limit(max(int(limit), 1))
        )
        return [int(item_id) for item_id in result.scalars().all()]

    async def mark_consumed(
        self, ids: Sequence[int], *, run_id: str, now: datetime
    ) -> int:
        """把 pending 项标成 consumed 并记运行与时间；只算真正从 pending 改到的行。"""
        item_ids = [int(item_id) for item_id in ids]
        if not item_ids:
            return 0
        result = await self.session.execute(
            update(AgentInbox)
            .where(
                AgentInbox.id.in_(item_ids),
                AgentInbox.status == "pending",
            )
            .values(status="consumed", consumed_at=now, consumed_run_id=run_id)
        )
        return max(int(result.rowcount or 0), 0)

    async def list_consumed_by(
        self, ids: Sequence[int], *, run_id: str
    ) -> list[AgentInbox]:
        """本次运行真正领到的项：``id IN ids AND consumed_run_id=?``，``id ASC``。"""
        item_ids = [int(item_id) for item_id in ids]
        if not item_ids:
            return []
        result = await self.session.execute(
            select(AgentInbox)
            .where(
                AgentInbox.id.in_(item_ids),
                AgentInbox.consumed_run_id == run_id,
            )
            .order_by(AgentInbox.id.asc())
            .execution_options(populate_existing=True)
        )
        return list(result.scalars().all())

    async def drop_pending(self, session_id: str, *, now: datetime) -> int:
        """把该会话全部 pending 项标成 dropped（收尾），返回改到的行数。"""
        result = await self.session.execute(
            update(AgentInbox)
            .where(
                AgentInbox.session_id == session_id,
                AgentInbox.status == "pending",
            )
            .values(status="dropped", consumed_at=now)
        )
        return max(int(result.rowcount or 0), 0)

    async def count_pending(self, session_id: str) -> int:
        """该会话 pending 项条数。"""
        result = await self.session.execute(
            select(func.count())
            .select_from(AgentInbox)
            .where(
                AgentInbox.session_id == session_id,
                AgentInbox.status == "pending",
            )
        )
        return int(result.scalar_one())

    async def purge_finished_before(self, cutoff: datetime) -> int:
        """删掉终态（consumed / dropped）且 ``consumed_at < cutoff`` 的项，返回删除条数。"""
        result = await self.session.execute(
            delete(AgentInbox).where(
                AgentInbox.status.in_(FINISHED_INBOX_STATUSES),
                AgentInbox.consumed_at < cutoff,
            )
        )
        return max(int(result.rowcount or 0), 0)
