"""``notify_channel`` 仓储：多通道通知配置的存取（docs/04 §5.12、§6、§9）。

``config`` 是一个**按 ``type`` 分支解释**的 JSON 列（email / webhook / bark /
dingtalk / wecom 的字段集合完全不同）。五种通道的字段校验用按 ``type`` 分发的
Pydantic 模型在服务层做，**不落成数据库约束**——仓储按「JSON 原文存取」处理它，
不解析、不校验、不脱敏（脱敏是读接口的事，规则与 ``setting`` 表相同）。

``(type, name)`` 的唯一约束由数据库兜底（``uq_notify_channel_type_name``）：
同名通道可以在不同类型下各配一个（如一个 webhook 与一个 bark 都叫「主通道」），
同类型下重名直接 ``IntegrityError``，由服务层转 409。仓储不先查后写。

``record_send`` 只回写最近一次发送结果三列（``last_sent_at`` / ``last_status`` /
``last_error``），供设置页展示；发送失败**不**自动停用通道（``enabled`` 只由
``set_enabled`` 改），否则一次网络抖动会静默关掉通知。

事务纪律（docs/04 §9）：所有方法都不 ``commit()``，由服务层决定事务边界。
"""

from sqlalchemy import delete, select, update

from maa_api.db.models import NotifyChannel, utcnow
from maa_api.db.repositories.base import BaseRepository
from maa_api.domain.enums import NotifyChannelType


class NotifyChannelRepository(BaseRepository):
    """``notify_channel`` 表的读写：配置增删查、启停、最近发送结果回写。"""

    async def create(self, channel: NotifyChannel) -> NotifyChannel:
        """写入一条通知通道，返回受会话管理的副本；只 ``flush`` 不 ``commit``。

        ``flush`` 是必须的：``(type, name)`` 重名冲突要在 ``create()`` 内以
        ``IntegrityError`` 抛出，服务层才能在同一个 ``try`` 里转成 409。

        与 :meth:`PipelineRepository.create` 同样用 ``Session.merge()`` 接收入参
        （M2-07 实测）：入参保持 transient，提交后字段仍可直接读。
        """
        stored = await self.session.merge(channel)
        await self.session.flush()
        return stored

    async def get(self, channel_id: str) -> NotifyChannel | None:
        """按主键取通道，没有返回 ``None``。

        ``populate_existing=True``：``set_enabled`` / ``record_send`` 都走 Core
        ``UPDATE``，不强制刷新就会返回身份映射里的陈旧实例（M2-08 实测）。
        """
        return await self.session.get(
            NotifyChannel, channel_id, populate_existing=True
        )

    async def list(
        self,
        *,
        type: NotifyChannelType | str | None = None,
        enabled: bool | None = None,
    ) -> list[NotifyChannel]:
        """通道列表：可选 ``type`` / ``enabled`` 过滤，按 ``created_at`` 升序。

        不返回 ``Page``：这是配置表而不是业务记录表，设置页一次展示全部通道，
        docs/04 §6 也没有为它列分页查询形态。顺序按创建时间升序 + ``id`` 兜底，
        保证同一秒创建的多条也有稳定次序（前端不会因刷新而换位）。
        """
        conditions = []
        if type is not None:
            conditions.append(NotifyChannel.type == type)
        if enabled is not None:
            conditions.append(NotifyChannel.enabled == bool(enabled))

        result = await self.session.execute(
            select(NotifyChannel)
            .where(*conditions)
            .order_by(NotifyChannel.created_at.asc(), NotifyChannel.id.asc())
            # 设置页常在 set_enabled / record_send 之后立刻回读
            .execution_options(populate_existing=True)
        )
        return list(result.scalars().all())

    async def set_enabled(self, channel_id: str, enabled: bool) -> bool:
        """启用/停用通道，返回是否存在这条记录。

        条件更新 + ``rowcount``（docs/04 §9 的写法），顺带刷新 ``updated_at``。
        改成与当前值相同的值也算成功（``rowcount`` 是匹配行数），服务层据此做
        幂等处理；记录不存在才返回 ``False``。
        """
        result = await self.session.execute(
            update(NotifyChannel)
            .where(NotifyChannel.id == channel_id)
            .values(enabled=bool(enabled), updated_at=utcnow())
        )
        return result.rowcount > 0

    async def record_send(
        self,
        channel_id: str,
        *,
        status: str,
        error: str | None = None,
    ) -> bool:
        """回写最近一次发送结果三列，返回是否存在这条记录。

        ``last_sent_at`` 取 ``utcnow()``（「上一次尝试发送的时刻」）；``status``
        是 ``success`` / ``failed``（列宽 16），``error`` 是失败原因文本。
        成功时 ``error=None`` 会清空上一次的失败原因——设置页展示的是「最近一次」
        的结果，留着旧错误会让人误以为这次也失败了。仓储不校验 ``status`` 的取值
        （没有对应的枚举，且它只影响展示），也不因失败而停用通道。
        """
        result = await self.session.execute(
            update(NotifyChannel)
            .where(NotifyChannel.id == channel_id)
            .values(
                last_sent_at=utcnow(),
                last_status=str(status),
                last_error=error,
            )
        )
        return result.rowcount > 0

    async def delete(self, channel_id: str) -> bool:
        """删除通道，返回是否真的删掉了（``False`` = 记录不存在）。

        物理删除：``notify_channel`` 只是配置，没有历史引用（发送记录在日志里），
        不需要软删；重复删除幂等。
        """
        result = await self.session.execute(
            delete(NotifyChannel).where(NotifyChannel.id == channel_id)
        )
        return result.rowcount > 0
