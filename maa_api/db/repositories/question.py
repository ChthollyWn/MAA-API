"""``agent_question`` 仓储（docs/04 §5.17、§9）。

Agent 主动提问的写入、待答列表与终态流转。三条纪律与 ``ConfirmationRepository``
同源，理由也一样（docs/04 §5.17 的提问与确认是并列的一等交互，只是语义不同）：

- **状态流转只允许 ``PENDING → ANSWERED / SKIPPED / EXPIRED / CANCELLED``，终态不可再变。**
  :meth:`AgentQuestionRepository.resolve` 用 ``UPDATE ... WHERE id=? AND
  status='pending'`` 的 ``rowcount`` 判定「状态机是否接受了这次流转」，返回
  ``False`` 由服务层转 ``409``（已答再答、答一条已过期的提问都在数据库层被拒）。
  这也是「问答只生效一次」的唯一保证：并发提交同一张卡片时只有一个能改到行。
- :meth:`expire_overdue` 与 :meth:`expire_all_pending` 都返回**被置为过期的 id
  列表**而不是条数：服务层要据此广播 ``question_resolved`` 并唤醒阻塞在
  :meth:`~maa_api.agent.questions.QuestionService.wait` 上的那一轮对话。
- ``options`` / ``selection`` 原样存取，仓储不解析选项文本、也不判断「选中项是否
  合法」——那是服务层的业务校验（规格 §3.3），仓储只管状态机与查询。

事务纪律（docs/04 §9）：本模块所有方法都**不 commit**，由调用方决定事务边界。
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

        用 ``merge()`` 而不是 ``add()``，理由同 ``AgentSessionRepository.create``
        （M2-07 实测）：入参保持 transient、字段永远可读；代价是入参只是数据载体。
        """
        stored = await self.session.merge(question)
        await self.session.flush()
        return stored

    async def get(self, question_id: str) -> AgentQuestion | None:
        """按主键取一条提问；不存在返回 ``None``。

        ``populate_existing=True``：:meth:`resolve` / :meth:`expire_overdue` 都走
        Core ``UPDATE``（批量那条还显式关了 ``synchronize_session``），不刷新会
        返回身份映射里的陈旧实例（M2-08 实测）。
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

    async def expire_overdue(self, now: datetime) -> list[str]:
        """把 ``expires_at < now`` 的待答提问批量置 ``EXPIRED``，返回其 id 列表。

        返回值是 **id 列表而不是条数**（docs/04 §9）：服务层要据此广播
        ``question_resolved`` 并唤醒等待中的那一轮。用 ``UPDATE ... RETURNING id``
        一条语句同时完成「置终态」与「取回 id」：先 SELECT 再 UPDATE 会有并发窗口，
        可能广播一个实际没被翻转的 id。``answered_by`` 记 ``system`` —— 超时不是
        任何人的裁决，与超时确认同一口径。
        """
        result = await self.session.execute(
            update(AgentQuestion)
            .where(
                AgentQuestion.status == QuestionStatus.PENDING,
                AgentQuestion.expires_at < now,
            )
            .values(
                status=QuestionStatus.EXPIRED,
                answered_by="system",
                answered_at=now,
            )
            .returning(AgentQuestion.id),
            execution_options={"synchronize_session": False},
        )
        return list(result.scalars().all())

    async def expire_all_pending(self, now: datetime) -> list[str]:
        """把所有遗留 pending 提问置 ``EXPIRED``，供进程重启清理。

        不做「按 ``expires_at`` 判定」：进程重启意味着等待那一轮的协程已经没了，
        没超时的提问同样不可能再被回答（规格 §3.3），一律置终态。
        """
        result = await self.session.execute(
            update(AgentQuestion)
            .where(AgentQuestion.status == QuestionStatus.PENDING)
            .values(
                status=QuestionStatus.EXPIRED,
                answered_by="system",
                answered_at=now,
            )
            .returning(AgentQuestion.id),
            execution_options={"synchronize_session": False},
        )
        return list(result.scalars().all())

    async def cancel_pending_for_session(
        self, session_id: str, now: datetime
    ) -> list[str]:
        """把某个会话仍待答的提问批量置 ``CANCELLED``，返回其 id 列表（M22）。

        与 :meth:`expire_all_pending` 同一条骨架（``UPDATE ... RETURNING id`` 一条
        语句同时翻状态与取回 id），区别只在过滤条件与目标状态：这里按 ``session_id``
        限定，因为「用户中断了某一轮」只该撤回那一轮挂起的提问，别的会话的卡片
        与它无关。``answered_by`` 记 ``system``——撤回不是任何人的裁决。
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
