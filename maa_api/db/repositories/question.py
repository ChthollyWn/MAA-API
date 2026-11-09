"""``agent_question`` 仓储（docs/04 §5.17、§9）。

Agent 主动提问的写入、待答列表与终态流转。三条纪律与 ``ConfirmationRepository``
同源，理由也一样（docs/04 §5.17 的提问与确认是并列的一等交互，只是语义不同）：

- **状态流转只允许 ``PENDING → ANSWERED / SKIPPED / EXPIRED / CANCELLED``，终态不可再变。**
  :meth:`AgentQuestionRepository.resolve` 用 ``UPDATE ... WHERE id=? AND
  status='pending'`` 的 ``rowcount`` 判定「状态机是否接受了这次流转」，返回
  ``False`` 由服务层转 ``409``（已答再答、答一条历史过期的提问都在数据库层被拒）。
  这也是「问答只生效一次」的唯一保证：并发提交同一张卡片时只有一个能改到行。
  ``EXPIRED`` 自维护性补丁 `questions-no-expiry` 起只存在于历史数据；现在只有
  「跳过」与两条收尾（会话中断、工单结束）会产生终态。
- ``options`` / ``selection`` 原样存取，仓储不解析选项文本、也不判断「选中项是否
  合法」：那是服务层的业务校验（规格 §3.3），仓储只管状态机与查询。

事务纪律（docs/04 §9）：所有方法都不 ``commit()``，由调用方决定事务边界。
"""

from __future__ import annotations

from datetime import datetime
from typing import Any

from sqlalchemy import func, select, update

from maa_api.db.models import AgentQuestion, utcnow
from maa_api.db.repositories.base import BaseRepository, Page
from maa_api.domain.enums import QuestionStatus

#: resolve 接受的终态：PENDING 不是合法目标（没有「流转回待答」这回事）。
RESOLVABLE_STATUSES = (
    QuestionStatus.ANSWERED,
    QuestionStatus.SKIPPED,
    QuestionStatus.EXPIRED,
    QuestionStatus.CANCELLED,
)


class AgentQuestionRepository(BaseRepository):
    """Agent 提问的写入、待答列表与终态流转（docs/04 §5.17）。"""

    async def create(self, question: AgentQuestion) -> AgentQuestion:
        """写入一条提问，返回受会话管理的实例（只 ``flush`` 不 ``commit``）。

        用 ``merge()`` 而不是 ``add()``，理由同 ``AgentSessionRepository.create``：入参保持 transient、字段永远可读；代价是入参只是数据载体。
        """
        stored = await self.session.merge(question)
        await self.session.flush()
        return stored

    async def get(self, question_id: str) -> AgentQuestion | None:
        """按主键取一条提问；不存在返回 ``None``。

        ``populate_existing=True``：:meth:`resolve` / ``cancel_pending_*`` 都走
        Core ``UPDATE``（批量那条还显式关了 ``synchronize_session``），不刷新会
        返回身份映射里的陈旧实例。
        """
        return await self.session.get(
            AgentQuestion, question_id, populate_existing=True
        )

    async def list(
        self,
        *,
        session_id: str | None = None,
        status: QuestionStatus | str | None = QuestionStatus.PENDING,
        page: int = 1,
        size: int = 20,
    ) -> Page[AgentQuestion]:
        """提问分页：可选 ``session_id`` / ``status`` 过滤，按 ``created_at DESC``。

        排序与过滤刻意贴着 ``ix_agent_question_session_status_created_at``
        （``session_id`` 等值 → ``status`` 等值 → ``created_at`` 排序）来写；
        同秒并列用 ``id DESC`` 兜稳定次序，避免刷新时卡片顺序抖动。
        """
        conditions: list[Any] = []
        if session_id is not None:
            conditions.append(AgentQuestion.session_id == session_id)
        if status is not None:
            conditions.append(AgentQuestion.status == QuestionStatus(status))

        items_stmt = (
            select(AgentQuestion)
            .where(*conditions)
            .order_by(AgentQuestion.created_at.desc(), AgentQuestion.id.desc())
        )
        count_stmt = (
            select(func.count()).select_from(AgentQuestion).where(*conditions)
        )
        return await self.paginate(items_stmt, count_stmt, page=page, size=size)

    async def resolve(
        self,
        question_id: str,
        status: QuestionStatus,
        *,
        selection: list[Any] | None = None,
        answered_by: str,
        answered_at: datetime | None = None,
    ) -> bool:
        """把待答提问置入终态，返回状态机是否接受了这次流转（docs/04 §9）。

        ``WHERE status='pending'`` 在数据库层保证「终态不可再变」：重复回答、
        跳过一条已过期的提问都拿到 ``rowcount == 0`` → ``False``，由服务层转
        ``409``。``status`` 只接受三个终态，传 ``PENDING`` 直接 ``False``；
        传枚举外的值抛 ``ValueError``（与 ``ConfirmationRepository.resolve`` 一致）。
        """
        target = QuestionStatus(status)
        if target not in RESOLVABLE_STATUSES:
            return False

        result = await self.session.execute(
            update(AgentQuestion)
            .where(
                AgentQuestion.id == question_id,
                AgentQuestion.status == QuestionStatus.PENDING,
            )
            .values(
                status=target,
                selection=selection,
                answered_by=answered_by,
                answered_at=answered_at or utcnow(),
            )
        )
        return result.rowcount > 0

    async def cancel_pending_for_session(
        self, session_id: str, now: datetime
    ) -> list[str]:
        """把某个会话仍待答的提问批量置 ``CANCELLED``，返回其 id 列表。

        用 ``UPDATE ... RETURNING id`` 一条语句同时翻状态与取回 id：服务层要据此
        广播 ``question_resolved`` 并唤醒阻塞在
        :meth:`~maa_api.agent.questions.QuestionService.wait` 上的那一轮对话。这里按
        ``session_id`` 限定，因为「用户中断了某一轮」只该撤回那一轮挂起的提问，别的
        会话的卡片与它无关。``answered_by`` 记 ``system``：撤回不是任何人的裁决。
        """
        result = await self.session.execute(
            update(AgentQuestion)
            .where(
                AgentQuestion.session_id == session_id,
                AgentQuestion.status == QuestionStatus.PENDING,
            )
            .values(
                status=QuestionStatus.CANCELLED,
                answered_by="system",
                answered_at=now,
            )
            .returning(AgentQuestion.id),
            execution_options={"synchronize_session": False},
        )
        return list(result.scalars().all())

    async def cancel_pending_for_ticket(
        self, ticket_id: str, now: datetime
    ) -> list[str]:
        """把某个工单仍待答的提问批量置 ``CANCELLED``，返回其 id 列表。

        工单结束（完成/失败/取消）后这些提问已经永远不会被派上用场；与
        :meth:`cancel_pending_for_session` 同一条骨架，只是按 ``origin_ticket_id``
        过滤。``answered_by`` 记 ``system``：撤回不是任何人的裁决。
        """
        result = await self.session.execute(
            update(AgentQuestion)
            .where(
                AgentQuestion.origin_ticket_id == ticket_id,
                AgentQuestion.status == QuestionStatus.PENDING,
            )
            .values(
                status=QuestionStatus.CANCELLED,
                answered_by="system",
                answered_at=now,
            )
            .returning(AgentQuestion.id),
            execution_options={"synchronize_session": False},
        )
        return list(result.scalars().all())
