"""Worker 与子代理的两个中间件（M70 规格 §4.5.5、§4.5.11）。

链上的位置见 05 §5.2：14 ``MandateMiddleware``、18 ``ReportMiddleware``；第 16 位在
M71 改成了 ``maa_api.agent.lease.LeaseMiddleware``（设备租约，确认通过后才取）。
总控链不挂这些（``applies_to`` 已排除）。

- ``MandateMiddleware``：委托的风险上限。策略引擎给出的 ``PolicyDecision.risk``
  高于上限就拒绝，被拦的调用不进 ``ConfirmationService``，不建确认与审计行。
- ``ReportMiddleware``：``report`` 必须是一批调用的最后一个且一批只有一个；成功时
  以 ``report_submitted`` 收尾本轮。``ensure_report`` 是「一轮结束却没有汇报」的
  兜底：失败补 ``blocked``，其余补 ``progress``；幂等，WorkerService 也调。
"""

from __future__ import annotations

import logging
from typing import Any

from maa_api.agent.harness.context import (
    ModelResponse,
    RunContext,
    RunOutcome,
    ToolCall,
    ToolOutcome,
)
from maa_api.agent.harness.middleware import CallNext, Middleware
from maa_api.agent.registry import ToolContext, ToolRegistry
from maa_api.agent.tickets.mandates import ceiling_risk
from maa_api.agent.tickets.reports import HumanAction, ReportInput
from maa_api.agent.workers.cards import RISK_RANK
from maa_api.domain.enums import CallerType, ReportStatus, TicketStatus
from maa_api.domain.errors import AppError, ErrorCode

__all__ = [
    "CEILING_LABELS",
    "FAILURE_LABELS",
    "MISSING_MANDATE_MESSAGE",
    "REPORT_NOT_LAST_MESSAGE",
    "RISK_LABELS",
    "MandateMiddleware",
    "ReportMiddleware",
    "ensure_report",
]

logger = logging.getLogger(__name__)

#: 上限与风险的中文名（§4.5.5）。
CEILING_LABELS: dict[str, str] = {"none": "无需确认", "consume": "消耗类及以下"}
RISK_LABELS: dict[str, str] = {"consume": "消耗类确认", "destructive": "破坏类确认"}
#: 失败原因的短标签（§4.5.11 的 ``FAILURE_LABELS``）。
FAILURE_LABELS: dict[str, str] = {
    "error": "运行出错",
    "stuck_loop": "连续重复同样的调用，已停下",
    "budget_exceeded": "超出成本预算",
    "context_overflow": "上下文装不下",
}
MISSING_MANDATE_MESSAGE = "找不到这个工单的委托，已拒绝执行"
REPORT_NOT_LAST_MESSAGE = "report 要放在这一批调用的最后，一批只能有一个"
#: 状态键（§4.5.11）；只经 ``ctx.state`` 共享。
SUBMITTED_KEY = "report.submitted"
INVALID_IDS_KEY = "report.invalid_ids"
ENSURED_KEY = "report.ensured"
EMPTY_FINAL_TEXT = "本轮没有输出"
FAILED_REPORT_PROMPT = "要让 Worker 再试一次，还是取消这个工单？"


def _error(code: ErrorCode, message: str, details: dict[str, Any] | None = None) -> ToolOutcome:
    error: dict[str, Any] = {"code": code.value, "message": message}
    if details is not None:
        error["details"] = details
    return ToolOutcome(status="error", error=error)


class MandateMiddleware(Middleware):
    """委托的风险上限检查（05 §5.2 第 14 位，spec §4.5.5）。"""

    name = "mandate"
    applies_to = frozenset({"worker", "subagent"})

    def __init__(
        self,
        *,
        registry: ToolRegistry,
        confirmation_service: Any,
        session_factory: Any,
    ) -> None:
        self.registry = registry
        self.confirmation_service = confirmation_service
        self.session_factory = session_factory

    async def wrap_tool(
        self, ctx: RunContext, call: ToolCall, call_next: CallNext
    ) -> ToolOutcome:
        ceiling = ctx.state.get("mandate.ceiling")
        ceiling_rank = RISK_RANK.get(ceiling) if isinstance(ceiling, str) else None
        if ceiling_rank is None:
            # fail closed：没有委托就没有可用的上限，任何工具都不放行。
            return _error(ErrorCode.MANDATE_SCOPE_EXCEEDED, MISSING_MANDATE_MESSAGE)
        if ceiling_rank == RISK_RANK["destructive"]:
            # 上限就是最高一级：没有比它更高的风险，不必再评估一次。
            return await call_next(call)
        try:
            definition = self.registry.get(call.name)
            params = self.registry.validate(call.name, call.arguments or {})
        except AppError:
            # 未知工具与参数错误交给内层按 v2 口径报 TOOL_NOT_FOUND / TOOL_ARGS_INVALID。
            return await call_next(call)
        decision = await self._evaluate(ctx, definition, params)
        if decision is None:
            return await call_next(call)
        # `grant_atomic_ops` 确认的是消耗类操作（09 §2.3）：上限比较按 consume 计，
        # 不能拿 decision.risk 的 none 直接放行（M71 修 M70 的缺口，spec §0 #10）。
        risk = str(ceiling_risk(decision.risk, decision.confirmation_action))
        risk_rank = RISK_RANK.get(risk)
        if risk_rank is None or risk_rank <= ceiling_rank:
            return await call_next(call)
        return _error(
            ErrorCode.MANDATE_SCOPE_EXCEEDED,
            f"超出工单的风险上限：这个工单只能做{CEILING_LABELS[ceiling]}的操作，"
            f"需要{RISK_LABELS[risk]}时请另开工单",
            {
                "tool": call.name,
                "risk": risk,
                "ceiling": ceiling,
                "ticket_id": ctx.ticket_id,
            },
        )

    async def _evaluate(self, ctx: RunContext, definition: Any, params: Any) -> Any:
        """调策略引擎；策略层自己的 ``AppError`` 返回 ``None``，交给内层抛出。"""
        policy = self.confirmation_service.policy
        try:
            async with self.session_factory() as db:
                context = ToolContext(
                    caller=CallerType.INTERNAL,
                    session_id=ctx.session_id,
                    request_id=f"run:{ctx.run_id}",
                    request=ctx.request,
                    db_session=db,
                )
                return await policy.evaluate(definition, params, context)
        except AppError:
            # 与 v2 策略层直接抛错同口径：内层 PolicyMiddleware 会再评估并报同一个错。
            return None


class ReportMiddleware(Middleware):
    """``report`` 的位置校验与收尾（05 §5.2 第 18 位，spec §4.5.11）。"""

    name = "report"
    applies_to = frozenset({"worker"})

    async def on_run_start(self, ctx: RunContext) -> None:
        ctx.state[SUBMITTED_KEY] = None
        ctx.state[INVALID_IDS_KEY] = set()
        ctx.state[ENSURED_KEY] = False

    async def after_model(self, ctx: RunContext, resp: ModelResponse) -> ModelResponse:
        calls = resp.tool_calls
        report_ids = [call.id for call in calls if call.name == "report"]
        if not report_ids:
            return resp
        last_is_report = calls[-1].name == "report" if calls else False
        if len(report_ids) > 1 or not last_is_report:
            invalid = ctx.state.setdefault(INVALID_IDS_KEY, set())
            invalid.update(report_ids)
        return resp

    async def wrap_tool(
        self, ctx: RunContext, call: ToolCall, call_next: CallNext
    ) -> ToolOutcome:
        if call.name != "report":
            return await call_next(call)
        invalid = ctx.state.get(INVALID_IDS_KEY) or ()
        if call.id in invalid:
            return _error(ErrorCode.INVALID_PARAMETER, REPORT_NOT_LAST_MESSAGE)
        outcome = await call_next(call)
        if outcome.status == "ok" and outcome.error is None:
            ctx.state[SUBMITTED_KEY] = (
                outcome.result if outcome.result is not None else True
            )
            outcome.stop_run = True
            outcome.stop_reason = "report_submitted"
        return outcome

    async def on_run_end(self, ctx: RunContext, outcome: RunOutcome) -> None:
        await ensure_report(ctx, outcome)


def _app_state(ctx: RunContext) -> Any:
    return getattr(getattr(ctx, "app", None), "state", None)


async def ensure_report(ctx: RunContext, outcome: RunOutcome) -> None:
    """一轮结束却没有汇报时的兜底（spec §4.5.11）：幂等，WorkerService 也调。

    已提交、已补过、取消或工单已结束时什么都不做；``failed`` 补 ``blocked``（会转发
    给总控），其余补 ``progress``（不转发）。汇报经 ``TicketService.record_report``
    落库，并在 v3 运行上发 ``report`` 事件。
    """
    if ctx.state.get(SUBMITTED_KEY) is not None:
        return
    if ctx.state.get(ENSURED_KEY):
        return
    if str(outcome.status) == "cancelled":
        return
    ticket_id = ctx.ticket_id
    if not ticket_id:
        return
    service = getattr(_app_state(ctx), "ticket_service", None)
    if service is None:
        logger.warning("补汇报跳过：没有工单服务（ticket=%s）", ticket_id)
        return
    try:
        ticket = await service.get(ticket_id)
    except AppError:
        logger.warning("补汇报跳过：读不到工单 %s", ticket_id, exc_info=True)
        return
    except Exception:  # noqa: BLE001 - 兜底不能把一轮正常结束变成异常
        logger.exception("补汇报跳过：读工单 %s 失败", ticket_id)
        return
    if TicketStatus(ticket.status).is_terminal:
        ctx.state[ENSURED_KEY] = True
        return

    if str(outcome.status) == "failed":
        reason = FAILURE_LABELS.get(outcome.finish_reason, FAILURE_LABELS["error"])
        report = ReportInput(
            status=ReportStatus.BLOCKED,
            summary=f"这一轮运行没有完成：{reason}。",
            human_action=HumanAction(type="decision", prompt=FAILED_REPORT_PROMPT),
            details={"auto": True, "finish_reason": outcome.finish_reason},
        )
    else:
        summary = (outcome.final_text or "").strip()[:600] or EMPTY_FINAL_TEXT
        report = ReportInput(
            status=ReportStatus.PROGRESS,
            summary=summary,
            details={"auto": True, "finish_reason": outcome.finish_reason},
        )
    ctx.state[ENSURED_KEY] = True
    try:
        recorded = await service.record_report(ticket_id, report, run_id=ctx.run_id)
    except AppError:
        # 工单在补汇报前结束、或终态汇报竞态：保留已落库的汇报即可。
        logger.warning("补汇报写入失败（ticket=%s）", ticket_id, exc_info=True)
        return
    except Exception:  # noqa: BLE001 - 收尾兜底不能把一轮正常结束变成异常
        logger.exception("补汇报写入异常（ticket=%s）", ticket_id)
        return
    try:
        await ctx.emit(
            "report",
            {
                "run_id": ctx.run_id,
                "ticket_id": ticket_id,
                "report_id": recorded.report.id,
                "status": report.status.value,
                "summary": report.summary,
                "human_action": (
                    report.human_action.model_dump(mode="json")
                    if report.human_action is not None
                    else None
                ),
            },
        )
    except Exception:  # noqa: BLE001 - 事件发不出去不影响已经落库的汇报
        logger.exception("补汇报事件发送失败（ticket=%s）", ticket_id)
