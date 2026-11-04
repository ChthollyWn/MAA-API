"""研判代理：便宜模型判断一条事件值不值得主动建工单（M81 规格 §4.2、§4.7、08 §4.3）。

每次研判建一条 ``kind=trigger_eval`` 运行，消息只有两段：``trigger_evaluator``
的定义正文与一张事件卡。模型经 M67 的结构化输出入口返回 ``trigger_decision``：

- ``accept``：给出工单草稿（执行代理只能是规则指定的那个）；
- ``stop``：建议交给总控处理正在进行的相关工作；
- ``ignore``：不处理。

``AppError``（结构化输出两次不合法、模型没配置、超预算、上游调用错误）一律记
运行 failed 并返回 ``UNAVAILABLE``，由 ``TriggerService`` 按 fallback 路由；其他
异常不在这里吞掉。事件卡里的 payload、工单摘要与历史事件都是外部数据，卡片本身
只做事实陈列。
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Any

from maa_api.agent.events.kinds import summarize
from maa_api.agent.events.rules import EffectiveRule
from maa_api.agent.llm.cost import CostLedger
from maa_api.agent.llm.schemas.trigger_decision import TriggerDecision
from maa_api.agent.llm.structured import complete_structured
from maa_api.db.models import AgentTrigger
from maa_api.db.repositories.ticket import TicketRepository
from maa_api.db.repositories.triggers import TriggerRepository
from maa_api.domain.enums import (
    AgentRunKind,
    AgentRunStatus,
    ModelRole,
    TicketStatus,
    TriggerEvalStatus,
)
from maa_api.domain.errors import AppError

__all__ = [
    "DECISION_SCHEMA",
    "EVALUATOR_AGENT",
    "EvalOutcome",
    "TriggerDecision",
    "TriggerEvaluator",
]

#: 后台代理名与输出 schema 名（§4.7）。
EVALUATOR_AGENT = "trigger_evaluator"
DECISION_SCHEMA = "trigger_decision"

#: 事件卡的上限（08 §4.3）。
MAX_CARD_PAYLOAD_CHARS = 4000
MAX_CARD_TICKETS = 8
MAX_CARD_EVENTS = 5
MAX_TICKET_SUMMARY_CHARS = 120
#: 进入「进行中的工单」的状态。
ACTIVE_TICKET_STATUSES = (
    TicketStatus.OPEN,
    TicketStatus.WAITING,
    TicketStatus.BLOCKED,
)

_DECISION_STATUSES = {
    "accept": TriggerEvalStatus.ACCEPT,
    "stop": TriggerEvalStatus.STOP,
    "ignore": TriggerEvalStatus.IGNORE,
}


@dataclass(frozen=True, slots=True)
class EvalOutcome:
    """一次研判的结果；``run_id`` 在失败时也保留，供事件表记 ``eval_run_id``。"""

    status: TriggerEvalStatus
    decision: TriggerDecision | None
    reason: str
    run_id: str | None


def _local_time(value: datetime | None) -> str:
    """数据库里的 UTC-naive 时间转本地时区的可读写法。"""
    if value is None:
        return "未知"
    if value.tzinfo is None:
        value = value.replace(tzinfo=UTC)
    return value.astimezone().strftime("%Y-%m-%d %H:%M:%S")


def _json(value: Any, limit: int | None = None) -> str:
    try:
        text = json.dumps(value, ensure_ascii=False, default=str)
    except (TypeError, ValueError):
        text = "{}"
    return text if limit is None else text[:limit]


class TriggerEvaluator:
    """按事件卡调用 ``trigger_evaluator`` 并记录一次运行。"""

    def __init__(
        self,
        *,
        app: Any,
        session_factory: Any,
        spec_registry: Any,
        run_service: Any,
        model_router: Any,
    ) -> None:
        self._app = app
        self._session_factory = session_factory
        self._spec_registry = spec_registry
        self._run_service = run_service
        self._router = model_router
        self._ledger: Any = None

    async def evaluate(
        self, trigger: AgentTrigger, rule: EffectiveRule
    ) -> EvalOutcome:
        """研判一条已命中规则的事件；只有 ``AppError`` 会被折算成不可用。"""
        spec = self._spec_registry.get(EVALUATOR_AGENT)
        run = await self._run_service.start(
            agent_name=EVALUATOR_AGENT,
            kind=AgentRunKind.TRIGGER_EVAL,
            session_id=None,
            spec_version=spec.spec_version,
        )
        messages = [
            {"role": "system", "content": spec.body},
            {"role": "user", "content": await self.render_card(trigger, rule)},
        ]
        try:
            result = await complete_structured(
                router=self._router,
                ledger=self._cost_ledger(),
                role=ModelRole.TRIGGER_EVAL,
                schema_name=DECISION_SCHEMA,
                messages=messages,
                agent_name=EVALUATOR_AGENT,
                run_id=run.id,
                session_id=None,
            )
        except AppError as exc:
            await self._run_service.finish(
                run.id,
                status=AgentRunStatus.FAILED,
                finish_reason="error",
                error_code=str(exc.code),
            )
            return EvalOutcome(
                status=TriggerEvalStatus.UNAVAILABLE,
                decision=None,
                reason=f"研判不可用：{exc.code}",
                run_id=run.id,
            )

        decision: TriggerDecision = result.value
        reason = decision.reason
        if (
            decision.worker is not None
            and rule.worker is not None
            and decision.worker != rule.worker
        ):
            reason = f"{reason}（已按规则改用 {rule.worker}）"
            decision = decision.model_copy(update={"worker": rule.worker})

        await self._run_service.finish(
            run.id,
            status=AgentRunStatus.SUCCEEDED,
            finish_reason="stop",
        )
        return EvalOutcome(
            status=_DECISION_STATUSES[decision.decision],
            decision=decision,
            reason=reason,
            run_id=run.id,
        )

    async def render_card(self, trigger: AgentTrigger, rule: EffectiveRule) -> str:
        """事件卡的逐行内容与顺序见 08 §4.3。"""
        async with self._session_factory() as session:
            tickets = await TicketRepository(session).list(
                statuses=ACTIVE_TICKET_STATUSES, size=MAX_CARD_TICKETS
            )
            recent = await TriggerRepository(session).recent_on_thread(
                trigger.thread_key,
                limit=MAX_CARD_EVENTS,
                before_id=trigger.id or 0,
            )
        description = self._worker_description(rule.worker)
        trigger_id = trigger.id if trigger.id is not None else "?"
        lines = [
            (
                f"【事件】#{trigger_id} {trigger.kind}（来源 {trigger.source}，"
                f"话题 {trigger.thread_key}，{_local_time(trigger.created_at)}）"
            ),
            f"摘要：{summarize(trigger.kind, trigger.payload)}",
            f"详情：{self._payload_detail(trigger.payload)}",
            (
                f"【规则】{rule.id}「{rule.title}」，执行代理 "
                f"{rule.worker or '（未指定）'}：{description}"
            ),
            f"研判提示：{rule.policy}",
            f"【进行中的工单】（至多 {MAX_CARD_TICKETS} 张，按 updated_at 倒序，"
            "状态 open、waiting、blocked）",
        ]
        if tickets.items:
            for ticket in tickets.items:
                summary = (ticket.summary or "（无摘要）")[:MAX_TICKET_SUMMARY_CHARS]
                lines.append(
                    f"- {ticket.id[:8]}「{ticket.title}」"
                    f"{ticket.kind} {ticket.status}：{summary}"
                )
        else:
            lines.append("（无）")
        lines.append(f"【这个话题最近的事件】（至多 {MAX_CARD_EVENTS} 条）")
        if recent:
            for item in recent:
                routed = item.routed_to or "none"
                lines.append(f"- {_local_time(item.created_at)} {item.kind} → {routed}")
        else:
            lines.append("（无）")
        return "\n".join(lines)

    # -- 内部 -----------------------------------------------------------
    def _cost_ledger(self) -> Any:
        if self._ledger is None:
            self._ledger = getattr(
                getattr(self._app, "state", None), "cost_ledger", None
            )
            if self._ledger is None:
                self._ledger = CostLedger(self._session_factory)
        return self._ledger

    def _worker_description(self, worker: str | None) -> str:
        if not worker:
            return "（未指定执行代理）"
        try:
            return self._spec_registry.get(worker).description
        except AppError:
            return "（代理定义不存在）"

    @staticmethod
    def _payload_detail(payload: Any) -> str:
        text = _json(payload)
        if len(text) > MAX_CARD_PAYLOAD_CHARS:
            return text[:MAX_CARD_PAYLOAD_CHARS] + "…（已截断）"
        return text
