"""``agent_session`` 与 ``agent_message`` 两个仓储（docs/04 §5.7、§5.8、§9）。

内置 agent 会话的持久化层：会话元信息 + 会话消息轨迹。字段结构刻意贴近 OpenAI
Chat Completions（docs/04 §5.8），重建上下文只需要 ``SELECT`` 后改键名，所以
:meth:`AgentMessageRepository.list_by_session` 只保证「按 ``seq`` 升序」这一条
顺序契约，不做任何语义转换。

事务纪律（docs/04 §9）：本模块所有方法都**不 commit**，由调用方决定事务边界
—— 一次 agent 回合往往要在同一事务里写 assistant 消息、tool 消息与审计记录。
``create`` 只 ``flush``，让外键目标（会话行）在事务内可见、并让自增 ``id`` 落到
返回值上。

两条容易写错的边界，实现里都按 docs 钉死：

- **``atomic_grant_*`` 只是持久化与查询，本卡不实现授权判定、窗口计算与撤销
  语义**（归 M11 的 ``PolicyEngine``，docs/04 §5.7）。:meth:`AgentSessionRepository.clear_grant`
  把两个字段**一起置空**，而不是把 ``expires_at`` 改成过去时刻：这样「从未授权」
  与「授权已撤销」在数据上不可区分是有意设计，这张表只需要回答「现在是否有效」。
- **``list_recent`` 按 ``last_message_at DESC`` 且 NULL 排最后**：刚创建、还没发过
  消息的会话不能靠 NULL 的排序行为抢占侧栏头部（SQLite 恰好把 NULL 当最小值、
  ``DESC`` 时排最后，但这里显式写 ``IS NULL`` 排序键，不依赖方言的默认行为）。
"""

from datetime import datetime

from sqlalchemy import delete, func, select, update

from maa_api.db.models import AgentIdempotency, AgentMessage, AgentSession, utcnow
from maa_api.db.repositories.base import BaseRepository, Page


class AgentSessionRepository(BaseRepository):
    """内置 agent 会话的读写；授权字段的语义判定不在这里（归 M11）。"""

    async def create(self, session: AgentSession) -> AgentSession:
        """写入一个新会话，返回受会话管理的实例（只 ``flush`` 不 ``commit``）。

        与 ``PipelineRepository.create`` 同一套理由（M2-07 实测）：用
        ``Session.merge()`` 而不是 ``Session.add()``，入参保持 transient、字段
        永远可读；``add()`` 会让入参变成会话管理的持久化实例，之后任何一次
        ``expire_all()`` / ``expire_on_commit=True`` 的提交都会清空它的实例字典，
        在 async 上下文外读 ``session.id`` 直接 ``MissingGreenlet``。
        代价：**入参只是数据载体**，对它的后续修改不会被提交，后续操作请用返回值
        或本仓储的方法。
        """
        stored = await self.session.merge(session)
        await self.session.flush()
        return stored

    async def get(self, session_id: str) -> AgentSession | None:
        """按主键取会话；不存在返回 ``None``。

        ``populate_existing=True`` 是必须的：本仓储的 :meth:`update_grant` /
        :meth:`clear_grant` 走 Core ``UPDATE``，调用方（或本会话）身份映射里
        可能还留着旧实例；会话通常是 ``expire_on_commit=False``，不刷新的话
        ``get`` 会把陈旧对象原样返回（M2-08 实测）。
        """
        return await self.session.get(
            AgentSession, session_id, populate_existing=True
        )

    async def list_recent(self, limit: int = 20) -> list[AgentSession]:
        """侧栏会话列表：``last_message_at DESC``，从未发过消息的（NULL）排最后。

        同秒并列时用 ``created_at DESC, id DESC`` 兜稳定次序，避免分页/动画里
        顺序抖动。``limit`` 至少为 1（负 ``LIMIT`` 在 SQLite 上不是报错而是全表）。
        """
        result = await self.session.execute(
            select(AgentSession)
            .order_by(
                AgentSession.last_message_at.is_(None).asc(),
                AgentSession.last_message_at.desc(),
                AgentSession.created_at.desc(),
                AgentSession.id.desc(),
            )
            .limit(max(int(limit), 1))
        )
        return list(result.scalars().all())

    async def list_page(self, *, page: int = 1, size: int = 20) -> Page[AgentSession]:
        stmt = select(AgentSession).order_by(
            AgentSession.last_message_at.is_(None).asc(),
            AgentSession.last_message_at.desc(),
            AgentSession.created_at.desc(),
            AgentSession.id.desc(),
        )
        count_stmt = select(func.count()).select_from(AgentSession)
        return await self.paginate(stmt, count_stmt, page=page, size=size)

    async def delete(self, session_id: str) -> bool:
        result = await self.session.execute(
            delete(AgentSession).where(AgentSession.id == session_id)
        )
        return result.rowcount > 0

    async def clear_expired_grants(self, now: datetime) -> list[tuple[str, str]]:
        rows = (
            await self.session.execute(
                select(AgentSession.id, AgentSession.atomic_grant_id).where(
                    AgentSession.atomic_grant_id.is_not(None),
                    AgentSession.atomic_grant_expires_at.is_not(None),
                    AgentSession.atomic_grant_expires_at <= now,
                )
            )
        ).all()
        if not rows:
            return []
        for session_id, grant_id in rows:
            await self.session.execute(
                update(AgentSession)
                .where(
                    AgentSession.id == session_id,
                    AgentSession.atomic_grant_id == grant_id,
                )
                .values(
                    atomic_grant_id=None,
                    atomic_grant_expires_at=None,
                    updated_at=now,
                )
            )
        return [(str(session_id), str(grant_id)) for session_id, grant_id in rows]

    async def update_grant(
        self, session_id: str, *, confirmation_id: str, expires_at: datetime
    ) -> bool:
        """Write both persisted fields that represent a session grant."""
        result = await self.session.execute(
            update(AgentSession)
            .where(AgentSession.id == session_id)
            .values(
                atomic_grant_id=confirmation_id,
                atomic_grant_expires_at=expires_at,
                updated_at=utcnow(),
            )
        )
        return result.rowcount > 0

    async def clear_grant(self, session_id: str) -> bool:
        """Revoke a session grant by clearing both persisted fields."""
        result = await self.session.execute(
            update(AgentSession)
            .where(AgentSession.id == session_id)
            .values(
                atomic_grant_id=None,
                atomic_grant_expires_at=None,
                updated_at=utcnow(),
            )
        )
        return result.rowcount > 0


class AgentIdempotencyRepository(BaseRepository):
    """Persistent 24-hour REST invoke key lookup and retention."""

    async def get(self, caller: str, key: str) -> AgentIdempotency | None:
        result = await self.session.execute(
            select(AgentIdempotency).where(
                AgentIdempotency.caller == caller,
                AgentIdempotency.key == key,
            )
        )
        return result.scalar_one_or_none()

    async def delete_expired(self, cutoff: datetime) -> int:
        result = await self.session.execute(
            delete(AgentIdempotency).where(AgentIdempotency.created_at < cutoff)
        )
        return max(int(result.rowcount or 0), 0)

    async def delete_unlinked(self) -> int:
        """Remove abandoned reservations created before an audit was linked."""
        result = await self.session.execute(
            delete(AgentIdempotency).where(AgentIdempotency.audit_id.is_(None))
        )
        return max(int(result.rowcount or 0), 0)

    async def create(self, record: AgentIdempotency) -> AgentIdempotency:
        stored = await self.session.merge(record)
        await self.session.flush()
        return stored

    async def delete(self, caller: str, key: str) -> bool:
        result = await self.session.execute(
            delete(AgentIdempotency).where(
                AgentIdempotency.caller == caller,
                AgentIdempotency.key == key,
            )
        )
        return result.rowcount > 0

class AgentMessageRepository(BaseRepository):
    """会话消息的追加与按序读取（docs/04 §5.8）。"""

    async def create(self, message: AgentMessage) -> AgentMessage:
        """追加一条消息，返回受会话管理的实例（只 ``flush`` 不 ``commit``）。

        与 :meth:`AgentSessionRepository.create` 同样用 ``merge()`` 保护入参。
        ``seq`` 由调用方（agent 循环）分配，本仓储**不重排、不补号**：
        ``uq_agent_message_session_seq`` 唯一约束在数据库层兜底，撞号抛
        ``IntegrityError`` 是调用方的并发缺陷，仓储不替它做决策。

        ``content`` 超过 32 KB 的截断（docs/04 §5.8）与截图替换为 ``screenshot_id``
        是**调用方纪律**，不在仓储里做 —— 仓储只保证存进去的字节与给它的参数
        一致（``agent_audit`` 的裁剪是例外，docs/04 §5.9 明确要求入库前裁剪，
        所以放在 :class:`AuditRepository` 内）。
        """
        stored = await self.session.merge(message)
        await self.session.flush()
        return stored

    async def list_by_session(self, session_id: str) -> list[AgentMessage]:
        """取一个会话的全部消息，**按 ``seq`` 升序**（上下文重建的输入）。

        按 ``seq`` 而不是 ``id`` / ``created_at``：``seq`` 是会话内唯一的逻辑
        序号，同一条 assistant 消息里的多个 tool_call 结果是按序追加的；``id``
        单调但跨会话共享序列，两者顺序一致只是巧合。
        """
        result = await self.session.execute(
            select(AgentMessage)
            .where(AgentMessage.session_id == session_id)
            .order_by(AgentMessage.seq.asc())
        )
        return list(result.scalars().all())

    async def list_page(
        self,
        session_id: str,
        *,
        after_seq: int | None = None,
        page: int = 1,
        size: int = 20,
    ) -> Page[AgentMessage]:
        conditions = [AgentMessage.session_id == session_id]
        if after_seq is not None:
            conditions.append(AgentMessage.seq > after_seq)
        stmt = (
            select(AgentMessage)
            .where(*conditions)
            .order_by(AgentMessage.seq.asc())
        )
        count_stmt = (
            select(func.count())
            .select_from(AgentMessage)
            .where(*conditions)
        )
        return await self.paginate(stmt, count_stmt, page=page, size=size)
