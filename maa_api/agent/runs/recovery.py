"""启动时收尾上一进程留下的运行，并让被打断的会话能继续。

一条运行的生命周期归发起方（docs/v3/13 §8），进程退出时不会有人替它写终态：
还在 ``running`` / ``waiting_human`` 的行只能由**下一次启动**收尾。收尾分三步，
前两步顺序不能反（与 v2 ``_finish_interrupted_turn`` 同一条硬约束）：

1. 给最后一批 ``tool_calls`` 里没有 tool 回复的调用补桩：assistant 的
   ``tool_calls`` 缺对应 tool 消息，下一轮请求结构不完整，上游直接 400。
2. 追加一条 ``role='system'`` 的恢复提示，告诉模型「刚才被打断了，先看现场」。
   喂给模型时由 ``build_model_context`` 转成 ``【系统】…`` 的 user 行。
3. 运行如果属于某个工单（``agent_run.ticket_id``），在写运行终态的同一事务里把
   ``open`` / ``waiting`` 的工单改成 ``blocked``，并补一条 ``forwarded=false`` 的
   汇报（M69 规格 §4.5.6）。此时 ``ticket_service`` 还没装配，不广播、不转发；
   lifespan 装配完服务后调用 ``forward_pending_reports`` 补转。
"""

from __future__ import annotations

import json
import logging
from collections.abc import Callable, Sequence
from datetime import datetime
from typing import Any

from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from maa_api.agent.tickets.service import block_tickets_after_restart
from maa_api.db.models import AgentMessage, AgentRun, utcnow
from maa_api.db.repositories.agent import (
    ACTIVE_RUN_STATUSES,
    AgentMessageRepository,
    AgentRunRepository,
    AgentSessionRepository,
)
from maa_api.domain.enums import AgentRunStatus

logger = logging.getLogger(__name__)

#: 恢复写下的 ``finish_reason``；运行时状态机里「服务关闭时被取消」不写终态，
#: 留给这里补。
RECOVERY_FINISH_REASON = "recovered_after_restart"
RECOVERY_NOTICE = "服务重启时这一轮被中断。继续之前先查看现场（截图、流水线状态），不要直接重复刚才的设备操作。"
RECOVERY_TOOL_STUB = {
    "error": {
        "code": "AGENT_TURN_INTERRUPTED",
        "message": "服务重启时这一轮被中断，该调用未完成",
    }
}


def _unpaired_tool_calls(history: Sequence[AgentMessage]) -> list[dict[str, Any]]:
    """找出最后一批 ``tool_calls`` 里还没拿到 tool 回复的调用。

    规则（docs/v3/specs/m66-durable-runs §4.2.3）：

    - 只看**最后一条带 ``tool_calls`` 的助手消息**；
    - 它之后的消息必须全是 ``tool``，否则说明历史本来就不是一个未完成的批次
      （例如后面已经跟了助手文本），返回空；
    - 返回其中 ``id`` 不在这些 tool 消息 ``tool_call_id`` 里的调用，保持原顺序。

    补的桩消息与那批调用相邻，满足 OpenAI 的消息结构要求。
    """
    batch_index = None
    for index in range(len(history) - 1, -1, -1):
        message = history[index]
        if message.role == "assistant" and message.tool_calls:
            batch_index = index
            break
    if batch_index is None:
        return []
    batch = history[batch_index]
    trailing = history[batch_index + 1 :]
    if any(message.role != "tool" for message in trailing):
        return []
    answered = {
        message.tool_call_id for message in trailing if message.tool_call_id is not None
    }
    return [
        call
        for call in batch.tool_calls or []
        if isinstance(call, dict) and call.get("id") not in answered
    ]


class RunRecovery:
    """把上一进程（``boot_id`` 不同）留下的活动运行收尾成 ``interrupted``。"""

    def __init__(
        self,
        session_factory: async_sessionmaker[AsyncSession],
        *,
        boot_id: str,
        clock: Callable[[], datetime] = utcnow,
    ) -> None:
        self.session_factory = session_factory
        self.boot_id = boot_id
        self._clock = clock

    async def recover(self) -> list[str]:
        """收尾所有不是本进程留下的活动运行，返回收尾成功的 id（按 ``started_at``）。

        每条运行一个事务；单条失败只记日志、继续下一条——一条坏历史不该让整个
        服务起不来。已经收尾过的运行不是活动态，再跑一次不会重复补消息。
        """
        async with self.session_factory() as session:
            runs = await AgentRunRepository(session).list_active_from_other_boots(
                self.boot_id
            )
        recovered: list[str] = []
        for run in runs:
            try:
                if await self.recover_run(run.id):
                    recovered.append(run.id)
            except Exception:  # noqa: BLE001 - 单条失败不能挡住启动
                logger.warning(
                    "启动恢复：运行 %s 收尾失败，继续下一条", run.id, exc_info=True
                )
        return recovered

    async def recover_run(self, run_id: str) -> bool:
        """收尾一条运行；它不存在或已经不是活动态时返回 ``False``。"""
        async with self.session_factory() as session:
            runs = AgentRunRepository(session)
            run = await runs.get(run_id)
            if run is None or run.status not in ACTIVE_RUN_STATUSES:
                return False
            # 与 ``list_active_from_other_boots`` 同一筛选条件：这条运行如果属于
            # 某个工单，先记下 id；提交前在写运行终态的同一事务里改工单（§4.5.6）。
            ticket_ids = [run.ticket_id] if run.ticket_id else []
            prompt_tokens, completion_tokens, steps = await runs.usage_totals(run_id)
            now = self._clock()
            run.status = AgentRunStatus.INTERRUPTED
            run.finish_reason = RECOVERY_FINISH_REASON
            run.finished_at = now
            run.prompt_tokens = prompt_tokens
            run.completion_tokens = completion_tokens
            run.steps = steps
            if run.session_id:
                appended = await self._append_recovery_messages(session, run, now)
                session_row = await AgentSessionRepository(session).get(run.session_id)
                if session_row is not None:
                    session_row.message_count += appended
                    session_row.updated_at = now
                    session_row.last_message_at = now
            if ticket_ids:
                await block_tickets_after_restart(session, ticket_ids, now=now)
            await session.commit()
        logger.warning("服务重启：运行 %s 已标为中断", run_id)
        return True

    async def _append_recovery_messages(
        self, session: AsyncSession, run: AgentRun, now: datetime
    ) -> int:
        """先补 tool 桩、再写恢复提示，返回追加的消息条数。"""
        session_id = run.session_id
        assert session_id is not None  # 调用方按 run.session_id 为真进入
        messages = AgentMessageRepository(session)
        history = await messages.list_by_session(session_id)
        appended = 0
        for call in _unpaired_tool_calls(history):
            await messages.create(
                AgentMessage(
                    session_id=session_id,
                    seq=await messages.next_seq(session_id),
                    role="tool",
                    content=json.dumps(RECOVERY_TOOL_STUB, ensure_ascii=False),
                    tool_call_id=call["id"],
                    tool_name=call["function"]["name"][:64],
                    run_id=run.id,
                    created_at=now,
                )
            )
            appended += 1
        await messages.create(
            AgentMessage(
                session_id=session_id,
                seq=await messages.next_seq(session_id),
                role="system",
                content=RECOVERY_NOTICE,
                finish_reason=RECOVERY_FINISH_REASON,
                run_id=run.id,
                created_at=now,
            )
        )
        return appended + 1
