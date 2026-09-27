"""``push_subscription`` 仓储：Web Push 订阅的存取（M14，docs/09 §13.1）。

``endpoint`` 的唯一约束由数据库兜底（``uq_push_subscription_endpoint``）：浏览器
对同一个 Service Worker 重复订阅会拿到相同的 endpoint，因此重放注册是**幂等更新**
而不是新增行。仓储提供 :meth:`upsert` 在同一个事务里完成「有则更新、无则插入」，
不先查后写地依赖两次往返。

发送路径只关心「未失效」的订阅，所以列表默认过滤 ``disabled_at IS NULL``；失效标记
用 :meth:`mark_disabled`，**不删除行** —— 设置页需要展示哪台设备的订阅已失效。

事务纪律（docs/04 §9）：所有方法都不 ``commit()``，由服务层决定事务边界。
"""

from __future__ import annotations

from collections.abc import Sequence

from sqlalchemy import select

from maa_api.db.models import PushSubscription, utcnow
from maa_api.db.repositories.base import BaseRepository


class PushSubscriptionRepository(BaseRepository):
    """``push_subscription`` 表的读写。"""

    async def list_all(
        self, *, include_disabled: bool = False
    ) -> Sequence[PushSubscription]:
        """按创建时间列出订阅，默认只返回未失效的。"""
        statement = select(PushSubscription).order_by(PushSubscription.created_at)
        if not include_disabled:
            statement = statement.where(PushSubscription.disabled_at.is_(None))
        result = await self.session.execute(statement)
        return result.scalars().all()

    async def get(self, subscription_id: str) -> PushSubscription | None:
        """按主键取订阅，没有返回 ``None``。"""
        return await self.session.get(
            PushSubscription, subscription_id, populate_existing=True
        )

    async def get_by_endpoint(self, endpoint: str) -> PushSubscription | None:
        """按 endpoint 取订阅（幂等注册的查找键）。"""
        result = await self.session.execute(
            select(PushSubscription).where(PushSubscription.endpoint == endpoint)
        )
        return result.scalars().first()

    async def upsert(
        self,
        *,
        endpoint: str,
        endpoint_host: str,
        p256dh: str,
        auth: str,
        user_agent: str | None,
    ) -> tuple[PushSubscription, bool]:
        """写入订阅，返回 ``(订阅, 是否新建)``。

        重复注册会刷新密钥、UA 与 ``updated_at``，并**清除** ``disabled_at``：
        浏览器重新订阅是用户显式恢复推送的信号，继续标记失效会让推送静默不达。
        """
        existing = await self.get_by_endpoint(endpoint)
        if existing is None:
            subscription = PushSubscription(
                endpoint=endpoint,
                endpoint_host=endpoint_host,
                p256dh=p256dh,
                auth=auth,
                user_agent=user_agent,
            )
            stored = await self.session.merge(subscription)
            await self.session.flush()
            return stored, True

        existing.p256dh = p256dh
        existing.auth = auth
        existing.user_agent = user_agent
        existing.endpoint_host = endpoint_host
        existing.disabled_at = None
        existing.updated_at = utcnow()
        await self.session.flush()
        return existing, False

    async def mark_sent(self, subscription_id: str) -> None:
        """记录一次成功投递。"""
        subscription = await self.get(subscription_id)
        if subscription is None:
            return
        subscription.last_success_at = utcnow()
        await self.session.flush()

    async def mark_disabled(self, subscription_id: str) -> None:
        """标记订阅失效（推送服务返回 404/410），保留行供设置页展示。"""
        subscription = await self.get(subscription_id)
        if subscription is None or subscription.disabled_at is not None:
            return
        subscription.disabled_at = utcnow()
        subscription.updated_at = utcnow()
        await self.session.flush()

    async def delete(self, subscription_id: str) -> bool:
        """删除订阅，返回是否确实删掉了一行。"""
        subscription = await self.get(subscription_id)
        if subscription is None:
            return False
        await self.session.delete(subscription)
        await self.session.flush()
        return True
