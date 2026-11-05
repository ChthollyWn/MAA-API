"""通道三表的仓储：``channel_binding`` / ``channel_bind_code`` / ``outbox``（M85 §4.1）。

纪律照 :mod:`maa_api.db.repositories.base`：

- **不 commit**：绑定码消费、建绑定与写回复要在同一个事务里，由
  :class:`~maa_api.agent.channels.service.ChannelService` 决定边界。写入方法只
  ``flush``。
- **业务决策不在仓储**：绑定码是否过期、平台是否匹配由 SQL 条件保证原子性，但
  「回哪句、建还是复用绑定」在服务层。
- **读方法返回 ORM 实例**，分页统一返回 :class:`Page`。

绑定码消费用带条件的 ``UPDATE`` 做 CAS：``code`` 存在、未被使用、未过期、平台
为空或与调用方一致时才置 ``used_at``；``rowcount`` 为 0 就是无效码。
"""

from __future__ import annotations

from datetime import datetime

from sqlalchemy import func, or_, select, update

from maa_api.db.models import ChannelBindCode, ChannelBinding, Outbox, utcnow
from maa_api.db.repositories.base import BaseRepository, Page

__all__ = [
    "ChannelBindCodeRepository",
    "ChannelBindingRepository",
    "OutboxRepository",
]

#: 绑定只有两个状态（spec §4.1）；本仓储不造枚举，常量只给服务层引用。
BINDING_ACTIVE = "active"
BINDING_REVOKED = "revoked"


class ChannelBindingRepository(BaseRepository):
    """``channel_binding`` 的读写。"""

    async def get(self, binding_id: str) -> ChannelBinding | None:
        """按主键取绑定；不存在返回 ``None``。"""
        return await self.session.get(ChannelBinding, binding_id)

    async def get_active(
        self, platform: str, external_user_id: str, external_chat_id: str
    ) -> ChannelBinding | None:
        """取同一 ``(platform, user, chat)`` 的 active 绑定。"""
        result = await self.session.execute(
            select(ChannelBinding).where(
                ChannelBinding.platform == str(platform),
                ChannelBinding.external_user_id == external_user_id,
                ChannelBinding.external_chat_id == external_chat_id,
                ChannelBinding.status == BINDING_ACTIVE,
            )
        )
        return result.scalar_one_or_none()

    async def get_by_identity(
        self, platform: str, external_user_id: str, external_chat_id: str
    ) -> ChannelBinding | None:
        """按唯一键取绑定，不论状态；重绑时用于复用被撤销的行。"""
        result = await self.session.execute(
            select(ChannelBinding).where(
                ChannelBinding.platform == str(platform),
                ChannelBinding.external_user_id == external_user_id,
                ChannelBinding.external_chat_id == external_chat_id,
            )
        )
        return result.scalar_one_or_none()

    async def list_all(
        self, *, platform: str | None = None, status: str | None = None
    ) -> list[ChannelBinding]:
        """按平台 / 状态过滤，``bound_at DESC``；两个条件都可选。"""
        conditions = []
        if platform is not None:
            conditions.append(ChannelBinding.platform == str(platform))
        if status is not None:
            conditions.append(ChannelBinding.status == status)
        result = await self.session.execute(
            select(ChannelBinding)
            .where(*conditions)
            .order_by(ChannelBinding.bound_at.desc(), ChannelBinding.id)
        )
        return list(result.scalars().all())

    async def create(
        self,
        *,
        platform: str,
        external_user_id: str,
        external_chat_id: str,
        display_name: str | None = None,
        now: datetime | None = None,
    ) -> ChannelBinding:
        """新建一条 active 绑定，只 ``flush``。"""
        moment = now or utcnow()
        binding = ChannelBinding(
            platform=str(platform),
            external_user_id=external_user_id,
            external_chat_id=external_chat_id,
            display_name=display_name,
            status=BINDING_ACTIVE,
            bound_at=moment,
            last_seen_at=moment,
        )
        self.session.add(binding)
        await self.session.flush()
        return binding

    async def reactivate(
        self, binding: ChannelBinding, *, now: datetime | None = None
    ) -> ChannelBinding:
        """复用一条已存在（通常是 revoked）的绑定：重新置 active 并刷新时间。"""
        moment = now or utcnow()
        binding.status = BINDING_ACTIVE
        binding.bound_at = moment
        binding.revoked_at = None
        binding.last_seen_at = moment
        self.session.add(binding)
        await self.session.flush()
        return binding

    async def revoke(self, binding_id: str, *, now: datetime | None = None) -> bool:
        """把一条 active 绑定置 revoked；本来就不 active 或不存在返回 ``False``。"""
        moment = now or utcnow()
        result = await self.session.execute(
            update(ChannelBinding)
            .where(
                ChannelBinding.id == binding_id,
                ChannelBinding.status == BINDING_ACTIVE,
            )
            .values(status=BINDING_REVOKED, revoked_at=moment)
            .execution_options(synchronize_session=False)
        )
        return bool(result.rowcount)

    async def mark_seen(
        self, binding_id: str, *, now: datetime | None = None
    ) -> None:
        """刷新 ``last_seen_at``；不改变状态。"""
        await self.session.execute(
            update(ChannelBinding)
            .where(ChannelBinding.id == binding_id)
            .values(last_seen_at=now or utcnow())
            .execution_options(synchronize_session=False)
        )


class ChannelBindCodeRepository(BaseRepository):
    """``channel_bind_code`` 的生成与一次性消费。"""

    async def get(self, code: str) -> ChannelBindCode | None:
        """按绑定码取行；不存在返回 ``None``。"""
        return await self.session.get(ChannelBindCode, code)

    async def create(
        self,
        *,
        code: str,
        platform: str | None,
        expires_at: datetime,
        now: datetime | None = None,
    ) -> ChannelBindCode:
        """写入一条绑定码，只 ``flush``；``code`` 撞主键由调用方重试。"""
        row = ChannelBindCode(
            code=code,
            platform=str(platform) if platform is not None else None,
            expires_at=expires_at,
            created_at=now or utcnow(),
        )
        self.session.add(row)
        await self.session.flush()
        return row

    async def consume(
        self, code: str, platform: str, *, now: datetime | None = None
    ) -> ChannelBindCode | None:
        """原子消费：存在、未用、未过期、平台为空或一致时置 ``used_at``。

        返回消费成功的行；任一条件不满足返回 ``None``，绑定码保持原状。
        """
        moment = now or utcnow()
        result = await self.session.execute(
            update(ChannelBindCode)
            .where(
                ChannelBindCode.code == code,
                ChannelBindCode.used_at.is_(None),
                ChannelBindCode.expires_at > moment,
                or_(
                    ChannelBindCode.platform.is_(None),
                    ChannelBindCode.platform == str(platform),
                ),
            )
            .values(used_at=moment)
            .execution_options(synchronize_session=False)
        )
        if not result.rowcount:
            return None
        return await self.get(code)


class OutboxRepository(BaseRepository):
    """``outbox`` 的列表与待发查询；发送循环自己领取（见 outbox.py）。"""

    async def page(
        self,
        *,
        status: str | None = None,
        channel: str | None = None,
        page: int = 1,
        size: int = 20,
    ) -> Page[Outbox]:
        """按状态 / 出口过滤，``created_at DESC, id DESC`` 分页。"""
        conditions = []
        if status is not None:
            conditions.append(Outbox.status == str(status))
        if channel is not None:
            conditions.append(Outbox.channel == str(channel))
        statement = (
            select(Outbox)
            .where(*conditions)
            .order_by(Outbox.created_at.desc(), Outbox.id.desc())
        )
        count_statement = select(func.count()).select_from(Outbox).where(*conditions)
        return await self.paginate(statement, count_statement, page=page, size=size)

    async def counts(self) -> dict[str, int]:
        """按状态计数；没有行的状态不出现在结果里。"""
        result = await self.session.execute(
            select(Outbox.status, func.count()).group_by(Outbox.status)
        )
        return {str(status): int(total) for status, total in result.all()}

    async def due(
        self, now: datetime, *, limit: int = 20
    ) -> list[Outbox]:
        """``status in (pending, failed)`` 且 ``next_attempt_at <= now`` 的行。

        只供诊断与测试；真正领取由 :class:`OutboxWorker` 用带状态条件的 UPDATE
        原子完成，避免两个 worker 重复发送。
        """
        result = await self.session.execute(
            select(Outbox)
            .where(
                Outbox.status.in_(("pending", "failed")),
                Outbox.next_attempt_at <= now,
            )
            .order_by(Outbox.next_attempt_at, Outbox.id)
            .limit(max(int(limit), 1))
        )
        return list(result.scalars().all())
