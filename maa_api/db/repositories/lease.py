"""``device_lease`` 仓储（M71 规格 §4.2、§4.5.1–§4.5.3）。

设备只有一台，表的主键 ``device_id`` 就是「容量 1」的硬约束：服务层的先到先得
排队、TTL 与抢占都靠对**这一行**的增删实现，没有第二个持有者能同时存在。

事务纪律与其余仓储一致：本类只 ``flush`` 不 ``commit``，事务边界由
``DeviceLeaseService`` 掌握（每个操作开一条会话、提交一次）。服务把全部写法都
放在同一把 ``asyncio.Condition`` 里串行化，因此这里不需要行锁或乐观版本号。
"""

from __future__ import annotations

from datetime import datetime

from sqlalchemy import delete, exists, or_, select, update

from maa_api.db.models import AgentRun, AgentSession, DeviceLease
from maa_api.db.repositories.agent import ACTIVE_RUN_STATUSES
from maa_api.db.repositories.base import BaseRepository


class DeviceLeaseRepository(BaseRepository):
    """租约行的读写与扫描查询；语义（何时删、删完广播什么）在服务层。"""

    async def get(self, device_id: str) -> DeviceLease | None:
        """按主键取租约；不存在返回 ``None``。

        ``populate_existing=True``：服务会在别的会话里 ``UPDATE`` 同一行（续期），
        当前会话若已加载过它，不刷新会读到陈旧的过期时间。
        """
        return await self.session.get(DeviceLease, device_id, populate_existing=True)

    async def insert(self, lease: DeviceLease) -> DeviceLease:
        """写入一行租约并 ``flush``；``device_id`` 已被占用时抛 ``IntegrityError``。

        不 ``commit``：服务用主键冲突判断「别人先拿到了」，随后回滚自己这条会话。
        """
        self.session.add(lease)
        await self.session.flush()
        return lease

    async def delete_lease(self, lease_id: str) -> bool:
        """按 ``lease_id`` 删行，返回是否真的删到。

        用 ``lease_id`` 而不是 ``device_id``：服务在锁内先读后删，带 lease_id 的
        条件删除能确保不会误删「读完之后又被别人换上的新租约」。
        """
        result = await self.session.execute(
            delete(DeviceLease).where(DeviceLease.lease_id == lease_id)
        )
        return bool(result.rowcount)

    async def update_expiry(
        self,
        lease_id: str,
        *,
        expires_at: datetime,
        heartbeat_at: datetime,
        explicit: bool,
        purpose: str,
    ) -> bool:
        """续期：改过期时间、心跳、显式标记与用途，返回是否找到了这一行。"""
        result = await self.session.execute(
            update(DeviceLease)
            .where(DeviceLease.lease_id == lease_id)
            .values(
                expires_at=expires_at,
                heartbeat_at=heartbeat_at,
                explicit=explicit,
                purpose=purpose,
            )
            .execution_options(synchronize_session=False)
        )
        return bool(result.rowcount)

    async def list_expired(self, now: datetime) -> list[DeviceLease]:
        """``expires_at <= now`` 的租约（扫描任务与 ``current`` 共用）。"""
        result = await self.session.execute(
            select(DeviceLease)
            .where(DeviceLease.expires_at <= now)
            .order_by(DeviceLease.acquired_at.asc())
        )
        return list(result.scalars().all())

    async def list_orphaned_implicit(self) -> list[DeviceLease]:
        """负责释放它们的根运行已经不活动的隐式租约（``explicit=false``）。

        两种都算孤儿：``run_id`` 为空（运行行已被删、外键 ``SET NULL``），或对应
        ``agent_run`` 的状态不在 ``running`` / ``waiting_human``。显式租约跨运行，
        不在此列。用 ``NOT EXISTS`` 表达，避免 ``LEFT JOIN`` 下 ``NULL NOT IN``
        三值逻辑把「运行行不存在」误判成活动。
        """
        active_run = exists().where(
            AgentRun.id == DeviceLease.run_id,
            AgentRun.status.in_(ACTIVE_RUN_STATUSES),
        )
        result = await self.session.execute(
            select(DeviceLease)
            .where(
                DeviceLease.explicit.is_(False),
                or_(DeviceLease.run_id.is_(None), ~active_run),
            )
            .order_by(DeviceLease.acquired_at.asc())
        )
        return list(result.scalars().all())

    async def delete_other_boots(self, boot_id: str) -> int:
        """删除上一进程遗留的租约，返回删除条数（恢复流程第 4 步用）。"""
        result = await self.session.execute(
            delete(DeviceLease).where(DeviceLease.boot_id != boot_id)
        )
        return int(result.rowcount or 0)

    async def session_ids_for_ticket(self, ticket_id: str) -> set[str]:
        """该工单名下的全部会话 id：Worker 与子代理都会带 ``ticket_id``。"""
        result = await self.session.execute(
            select(AgentSession.id).where(AgentSession.ticket_id == ticket_id)
        )
        return {str(session_id) for session_id in result.scalars().all()}

    async def session_id_for_run(self, run_id: str) -> str | None:
        """运行所属的会话 id；运行不存在返回 ``None``。"""
        result = await self.session.execute(
            select(AgentRun.session_id).where(AgentRun.id == run_id)
        )
        return result.scalar_one_or_none()
