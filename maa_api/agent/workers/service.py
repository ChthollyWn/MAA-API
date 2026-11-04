"""WorkerService：工单的建立、运行、汇报与结束（M70 规格 §4.5.3、§4.5.7）。

一个 Worker 会话一个 :class:`~maa_api.agent.workers.pump.SessionPump`，泵懒创建；
本服务负责三件事：

- 建单：查代理定义、补标题、把 ``worker_session_id`` 与会话里的任务说明写进库；
- 跑一轮：领收件箱、装配工单卡与委托上限、调 ``run_agent``，收尾时补汇报并释放设备门；
- 收口：汇报进终态、取消、工单结束后的收尾（丢收件箱、关泵、停订阅）。

外部依赖都从构造参数注入，测试可以只给临时库与几个替身；``app.state`` 上只读
``model_router`` / ``setting_service`` / ``question_service`` / ``confirmation_service``
与 ``ticket_service``（后者供 ``ensure_report`` 用）。
"""

from __future__ import annotations

import asyncio
import logging
from collections.abc import Mapping
from dataclasses import dataclass
from datetime import datetime
from typing import Any, Literal

from sqlalchemy import select, update

from maa_api.agent.harness.context import CURRENT_RUN, RunContext, RunOutcome
from maa_api.agent.harness.events import RunEvent, RunEventBus
from maa_api.agent.specs.registry import AgentSpecError
from maa_api.agent.tickets.inbox import InboxService
from maa_api.agent.tickets.reports import HumanAction as ReportHumanAction
from maa_api.agent.tickets.reports import ReportInput
from maa_api.agent.tickets.service import TicketService
from maa_api.agent.tickets.threads import ThreadRouter
from maa_api.agent.workers.cards import (
    effective_ceiling,
    persist_inbox_messages,
    ticket_card,
)
from maa_api.agent.workers.device_gate import DeviceGate
from maa_api.agent.workers.middleware import ensure_report
from maa_api.agent.workers.pump import (
    SWEEP_INTERVAL_SECONDS,
    SessionPump,
    TurnHandle,
    WorkerSlots,
    run_agent,
)
from maa_api.db.models import AgentSession, Pipeline, Ticket, utcnow
from maa_api.db.repositories.agent import AgentSessionRepository
from maa_api.db.repositories.pipeline import PipelineRepository
from maa_api.db.repositories.ticket import (
    MandateRepository,
    ThreadSubscriptionRepository,
    TicketRepository,
)
from maa_api.domain.enums import (
    AgentRunKind,
    AgentRunStatus,
    AgentSessionKind,
    AgentSessionStatus,
    InboxKind,
    ModelRole,
    PipelineStatus,
    ReportStatus,
    TicketStatus,
)
from maa_api.domain.errors import AppError, ErrorCode
from maa_api.settings import get_settings

__all__ = [
    "OpenedTicket",
    "PostedMessage",
    "WorkerService",
    "pipeline_event_summary",
]

logger = logging.getLogger(__name__)

#: 工单只有这三个终态；终态之后收件箱、订阅与泵都要收掉。
_TERMINAL_TICKET_STATUSES = frozenset(
    {TicketStatus.DONE, TicketStatus.FAILED, TicketStatus.CANCELLED}
)
#: 进入终态的汇报状态（映射后是 done / failed / cancelled）。
_TERMINAL_REPORT_STATUSES = frozenset(
    {ReportStatus.READY, ReportStatus.FAILED, ReportStatus.DROPPED}
)
#: 活动运行的两种状态：收尾时才需要 ``finish``。
_ACTIVE_RUN_STATUSES = frozenset(
    {AgentRunStatus.RUNNING.value, AgentRunStatus.WAITING_HUMAN.value}
)
#: 流水线终态的中文（§4.5.3 的 ``pipeline_event_summary``）。
_PIPELINE_STATUS_LABELS: dict[str, str] = {
    PipelineStatus.COMPLETED.value: "已完成",
    PipelineStatus.FAILED.value: "失败",
    PipelineStatus.CANCELLED.value: "已停止",
}
#: 没有标题的流水线在事件里的称呼。
_UNNAMED_PIPELINE = "未命名"
#: 工单里直接对话时，passive 提醒里引用的用户消息长度。
_NOTIFY_EXCERPT_CHARS = 200
#: 一轮最多领取的收件箱项数（与 §4.5.5 的批大小一致）。
_CLAIM_LIMIT = 50


@dataclass(frozen=True, slots=True)
class PostedMessage:
    """一条落进 Worker 收件箱的用户消息（§4.5.3 的返回值）。"""

    session_id: str
    inbox_id: int
    message_id: int
    seq: int


@dataclass(frozen=True, slots=True)
class OpenedTicket:
    """建单结果；``create_ticket`` 工具与之后的系统建单都只读这几个字段。"""

    ticket_id: str
    worker_session_id: str
    title: str
    kind: str
    status: str


def _terminal_ticket(ticket: Ticket | None) -> bool:
    return ticket is None or TicketStatus(ticket.status).is_terminal


def _ticket_status(ticket: Ticket) -> str:
    return str(ticket.status)


def pipeline_event_summary(pipeline: Pipeline) -> str:
    """流水线终态的一句话摘要（§4.5.3）。

    形如 ``流水线「日常」已完成，用时 3 分钟``；失败时带错误码与错误信息前 120 字。
    没有起止时间的流水线不加用时。
    """
    title = str(getattr(pipeline, "title", None) or _UNNAMED_PIPELINE)
    status = str(getattr(pipeline, "status", None) or "")
    label = _PIPELINE_STATUS_LABELS.get(status, status)
    summary = f"流水线「{title}」{label}"
    error_code = getattr(pipeline, "error_code", None)
    if error_code:
        message = str(getattr(pipeline, "error_message", None) or "")[:120]
        summary += f"：{error_code}"
        if message:
            summary += f"，{message}"
    started_at: datetime | None = getattr(pipeline, "started_at", None)
    finished_at: datetime | None = getattr(pipeline, "finished_at", None)
    if started_at is not None and finished_at is not None:
        seconds = max(0.0, (finished_at - started_at).total_seconds())
        summary += f"，用时 {int(seconds // 60)} 分钟"
    return summary


class WorkerService:
    """工单的生命周期与 Worker 会话驱动（规格 §4.5.3、§4.5.7）。"""

    def __init__(
        self,
        *,
        app: Any,
        session_factory: Any,
        spec_registry: Any,
        run_service: Any,
        inbox_service: InboxService,
        ticket_service: TicketService,
        thread_router: ThreadRouter,
        delegation: Any,
        device_gate: DeviceGate,
        titler: Any,
        event_bus: RunEventBus | None,
    ) -> None:
        self._app = app
        self._session_factory = session_factory
        self._spec_registry = spec_registry
        self._run_service = run_service
        self._inbox = inbox_service
        self._ticket_service = ticket_service
        self._thread_router = thread_router
        #: M70 只保存，T10 的 DelegationService 才是它的使用者。
        self._delegation = delegation
        self._device_gate = device_gate
        self._titler = titler
        self._event_bus = event_bus
        self._orchestrator: Any = None
        self._pumps: dict[str, SessionPump] = {}
        self._priorities: dict[str, int] = {}
        self._slots = WorkerSlots(
            capacity=lambda: get_settings().worker.max_concurrent
        )
        self._unsubscribe: Any = None
        self._sweep_task: asyncio.Task[None] | None = None
        self._tasks: set[asyncio.Task[Any]] = set()
        self._started = False
        self._closed = False

    # ------------------------------------------------------------------
    # 装配与生命周期
    # ------------------------------------------------------------------
    def bind_orchestrator(self, orchestrator: Any) -> None:
        """注入总控服务：建单取当前总控会话、直接对话时发 passive 提醒。"""
        self._orchestrator = orchestrator

    async def start(self) -> None:
        """订阅事件总线、扫一遍待处理项，并起兜底扫描。"""
        if self._started:
            return
        self._started = True
        if self._event_bus is not None:
            self._unsubscribe = self._event_bus.subscribe(self._on_event)
        await self._sweep()
        self._sweep_task = asyncio.create_task(
            self._sweep_loop(), name="worker-service-sweep"
        )

    async def close(self) -> None:
        """退订、停扫描、关闭全部泵；可重复调用。"""
        self._closed = True
        if self._unsubscribe is not None:
            unsubscribe, self._unsubscribe = self._unsubscribe, None
            try:
                unsubscribe()
            except Exception:  # noqa: BLE001 - 退订失败不影响关闭
                logger.exception("WorkerService 退订事件总线失败")
        task = self._sweep_task
        self._sweep_task = None
        if task is not None and not task.done():
            task.cancel()
            await asyncio.gather(task, return_exceptions=True)
        pending = [item for item in self._tasks if not item.done()]
        for item in pending:
            item.cancel()
        if pending:
            await asyncio.gather(*pending, return_exceptions=True)
        self._tasks.clear()
        pumps, self._pumps = list(self._pumps.items()), {}
        for _, pump in pumps:
            await pump.close()

    async def _sweep_loop(self) -> None:
        while True:
            await asyncio.sleep(SWEEP_INTERVAL_SECONDS)
            try:
                await self._sweep()
            except Exception:  # noqa: BLE001 - 兜底扫描失败不能停掉循环
                logger.exception("WorkerService 兜底扫描失败")

    async def _sweep(self) -> None:
        """对「有非 passive 待处理项、工单未结束」的 Worker 会话各唤醒一次。"""
        terminal = [status.value for status in _TERMINAL_TICKET_STATUSES]
        statement = (
            select(AgentSession.id)
            .join(Ticket, Ticket.id == AgentSession.ticket_id)
            .where(
                AgentSession.kind == AgentSessionKind.WORKER,
                AgentSession.status == AgentSessionStatus.ACTIVE,
                AgentSession.archived_at.is_(None),
                Ticket.status.notin_(terminal),
            )
        )
        async with self._session_factory() as session:
            result = await session.execute(statement)
            session_ids = [str(row) for row in result.scalars()]
        for session_id in session_ids:
            try:
                if await self._inbox.has_active_pending(session_id):
                    self.wake(session_id)
            except Exception:  # noqa: BLE001 - 单个会话失败不挡其余
                logger.exception("WorkerService 扫描会话 %s 失败", session_id)

    # ------------------------------------------------------------------
    # 建单
    # ------------------------------------------------------------------
    async def open_ticket(
        self,
        *,
        kind: str,
        title: str | None,
        brief: str,
        scope: str,
        origin: str,
        strength: str,
        source: str,
        sender: str | None,
        rule_id: str | None = None,
        priority: int = 1,
        orchestrator_session_id: str | None = None,
        incident_key: str | None = None,
    ) -> OpenedTicket:
        """建工单、Worker 会话与任务说明（§4.5.3）。

        ``strength`` 只作形状校验：委托强度与上限由 ``TicketService.create`` 按
        ``origin`` 推导（M69 的 ``build_mandate``），这里不重复传。
        """
        spec = self._worker_spec(kind)
        expected_strength = "explicit" if str(origin) == "user" else "inferred"
        if strength != expected_strength:
            # M69 的 create 按 origin 推导委托强度，不再接受 strength；两者不一致
            # 会得到一个比调用方意图更强（或更弱）的委托，这里直接拒绝。
            raise AppError(
                ErrorCode.INVALID_PARAMETER,
                "委托强度与工单来源不一致",
                {"origin": str(origin), "strength": strength},
            )
        title_text = (title or "").strip()
        if not title_text:
            title_text = await self._titler.title_for(brief)
        title_text = str(title_text).strip()[:64] or "新工单"

        target_session_id = orchestrator_session_id
        if target_session_id is None:
            target_session_id = await self._current_orchestrator_session_id()
        ticket, _mandate = await self._ticket_service.create(
            kind=kind,
            title=title_text,
            scope=scope,
            origin=origin,
            source=source,
            sender=sender,
            rule_id=rule_id,
            priority=priority,
            orchestrator_session_id=target_session_id,
        )
        session = await self.ensure_worker_session(ticket, incident_key=incident_key)
        await self._ticket_service.attach_worker_session(
            ticket.id, session.id, brief=brief
        )
        # M69 的 attach_worker_session 顺带写一条 ticket_brief（形状是 M69 的
        # event/title/brief）。先把它丢掉，再按 M70 §4.5.9 的 system 形状写任务说明：
        # 收件箱渲染只认 payload.text，留着两条会让模型看到两条任务说明。
        await self._inbox.drop_pending(session.id)
        await self._inbox.put(
            session.id,
            InboxKind.SYSTEM,
            thread_key=f"ticket:{ticket.id}",
            ref_id=f"brief:{ticket.id}",
            payload={
                "text": f"总控交给你的任务：\n{brief}",
                "passive": False,
                "origin": "orchestrator",
                "ticket_id": ticket.id,
            },
        )
        self._priorities[session.id] = int(ticket.priority)
        return OpenedTicket(
            ticket_id=ticket.id,
            worker_session_id=session.id,
            title=ticket.title,
            kind=ticket.kind,
            status=_ticket_status(ticket),
        )

    async def ensure_worker_session(
        self, ticket: Ticket, *, incident_key: str | None = None
    ) -> AgentSession:
        """取或建工单的 Worker 会话（``kind='worker'``，带 ``ticket_id``）。

        已有活动会话就复用它；没有（或已结束）就新建。``incident_key`` 只给诊断
        工单用，撞唯一索引时 ``IntegrityError`` 原样抛给调用方。
        """
        if ticket.worker_session_id:
            existing = await self._get_session(ticket.worker_session_id)
            if (
                existing is not None
                and str(existing.kind) == AgentSessionKind.WORKER
                and str(existing.status) == AgentSessionStatus.ACTIVE
                and existing.archived_at is None
            ):
                return existing
        profile = await self._worker_profile(ticket)
        model = str(getattr(profile, "model", "") or "unconfigured")
        base_url = getattr(profile, "base_url", None)
        session = AgentSession(
            title=(ticket.title or "")[:64] or None,
            model=model[:64],
            base_url=base_url,
            kind=AgentSessionKind.WORKER,
            ticket_id=ticket.id,
            incident_key=incident_key,
        )
        async with self._session_factory() as db:
            stored = await AgentSessionRepository(db).create(session)
            await db.commit()
        return stored

    async def require_open_ticket(self, ticket_id: str) -> Ticket:
        """按 id 取未结束的工单；不存在 ``TICKET_NOT_FOUND``，终态 ``TICKET_ENDED``。"""
        ticket = await self._get_ticket(ticket_id)
        if ticket is None:
            raise AppError(
                ErrorCode.TICKET_NOT_FOUND, "没有这个工单", {"ticket_id": ticket_id}
            )
        if _terminal_ticket(ticket):
            raise AppError(
                ErrorCode.TICKET_ENDED, "工单已结束", {"ticket_id": ticket_id}
            )
        return ticket

    async def ensure_ready(self, ticket: Ticket) -> None:
        """Worker 角色的 LLM 配置检查（§4.5.2 的 Worker 版本）。"""
        try:
            profile = await self._worker_profile(ticket)
        except AppError as exc:
            encryption = getattr(self._state(), "setting_service", None)
            if getattr(encryption, "llm_encryption_error", None) is not None:
                raise AppError(
                    ErrorCode.LLM_ENCRYPTION_KEY_INVALID,
                    "已保存的 LLM API Key 无法解密，请到设置页重新填写 API Key",
                ) from exc
            raise
        if profile is None:
            return
        if not profile.base_url or not profile.api_key or not profile.model:
            service = getattr(self._state(), "setting_service", None)
            if getattr(service, "llm_encryption_error", None) is not None:
                raise AppError(
                    ErrorCode.LLM_ENCRYPTION_KEY_INVALID,
                    "已保存的 LLM API Key 无法解密，请到设置页重新填写 API Key",
                )
            raise AppError(
                ErrorCode.LLM_NOT_CONFIGURED,
                "还没有配置 LLM，请先在设置页填写 base_url、api_key 与 model",
            )

    # ------------------------------------------------------------------
    # 工单内对话
    # ------------------------------------------------------------------
    async def post_user_message(
        self, ticket_id: str, content: str, mentions: list[dict]
    ) -> PostedMessage:
        """用户在工单里直接说话：落消息、唤醒 Worker、给总控留 passive 摘要。"""
        ticket = await self.require_open_ticket(ticket_id)
        await self.ensure_ready(ticket)
        session = await self.ensure_worker_session(ticket)
        item, message = await self._inbox.put_user_message(
            session.id,
            content=content,
            thread_key=f"ticket:{ticket.id}",
            payload={
                "mentions": list(mentions or []),
                "source": "web",
                "sender": "web",
            },
        )
        seq = int(item.ref_id) if item.ref_id is not None else int(message.seq)
        self._priorities[session.id] = int(ticket.priority)
        await self._yield_pending_question(session.id)
        if self._orchestrator is not None:
            try:
                await self._orchestrator.notify(
                    f"用户在工单「{ticket.title}」里说：{str(content)[:_NOTIFY_EXCERPT_CHARS]}",
                    passive=True,
                    ref_id=f"ticket:{ticket.id}:msg:{seq}",
                    ticket_id=ticket.id,
                )
            except Exception:  # noqa: BLE001 - 通知总控失败不影响已落库的消息
                logger.exception("工单 %s 的用户消息通知总控失败", ticket.id)
        return PostedMessage(
            session_id=session.id,
            inbox_id=int(item.id or 0),
            message_id=int(message.id or 0),
            seq=seq,
        )

    async def send_from_orchestrator(self, ticket_id: str, content: str) -> int:
        """总控补充说明：写一条非 passive 的 system 项，返回收件箱 id。"""
        ticket = await self.require_open_ticket(ticket_id)
        session = await self.ensure_worker_session(ticket)
        item = await self._inbox.put(
            session.id,
            InboxKind.SYSTEM,
            thread_key=f"ticket:{ticket.id}",
            ref_id=f"orchestrator:{ticket.id}",
            payload={
                "text": f"总控补充：{content}",
                "passive": False,
                "origin": "orchestrator",
                "ticket_id": ticket.id,
            },
        )
        return int(item.id or 0)

    # ------------------------------------------------------------------
    # 汇报
    # ------------------------------------------------------------------
    async def submit_report(self, ctx: RunContext, params: Any) -> dict[str, Any]:
        """``report`` 工具的落库与收尾（§4.5.3）。"""
        ticket_id = ctx.ticket_id
        if not ticket_id:
            raise AppError(
                ErrorCode.INVALID_PARAMETER, "这一轮运行不属于任何工单"
            )
        ticket = await self._get_ticket(ticket_id)
        if ticket is None:
            raise AppError(
                ErrorCode.TICKET_NOT_FOUND, "没有这个工单", {"ticket_id": ticket_id}
            )
        if _terminal_ticket(ticket):
            raise AppError(
                ErrorCode.TICKET_ENDED,
                "工单已结束，不能再汇报",
                {"ticket_id": ticket_id},
            )

        status = ReportStatus(params.status)
        report = ReportInput(
            status=status,
            summary=params.summary,
            human_action=_human_action(params.human_action),
            details=getattr(params, "details", None),
        )
        outcome = await self._ticket_service.record_report(
            ticket_id, report, run_id=ctx.run_id
        )
        try:
            await ctx.emit(
                "report",
                {
                    "run_id": ctx.run_id,
                    "ticket_id": ticket_id,
                    "report_id": outcome.report.id,
                    "status": status.value,
                    "summary": report.summary,
                    "human_action": (
                        report.human_action.model_dump(mode="json")
                        if report.human_action is not None
                        else None
                    ),
                },
            )
        except Exception:  # noqa: BLE001 - 事件发不出去不影响已经落库的汇报
            logger.exception("工单 %s 的 report 事件发送失败", ticket_id)

        if status in _TERMINAL_REPORT_STATUSES:
            await self.on_ticket_ended(ticket_id, abort_calls=False)
        return {
            "report_id": outcome.report.id,
            "ticket_status": _ticket_status(outcome.ticket),
            "forwarded": outcome.forwarded_inbox_id is not None,
        }

    # ------------------------------------------------------------------
    # 唤醒与中断
    # ------------------------------------------------------------------
    def wake(self, session_id: str) -> None:
        """唤醒（或创建）该 Worker 会话的泵。"""
        if self._closed:
            return
        self._pump_for(session_id).wake()

    def turn_running(self, session_id: str) -> bool:
        """该会话此刻是否有一轮真在跑；只有泵在等窗口 / 闸时不算。"""
        pump = self._pumps.get(session_id)
        return bool(pump is not None and pump.busy)

    async def interrupt(self, ticket_id: str) -> dict[str, Any]:
        """中断当前一轮，工单状态不变（§4.5.7）。"""
        ticket = await self.require_open_ticket(ticket_id)
        session_id = ticket.worker_session_id
        if not session_id:
            return {"ticket_id": ticket_id, "interrupted": False, "turn_running": False}
        pump = self._pumps.get(session_id)
        if pump is None:
            await self._abort_calls(session_id)
            return {"ticket_id": ticket_id, "interrupted": False, "turn_running": False}
        interrupted = await pump.interrupt(
            "interrupted", abort=lambda: self._abort_calls(session_id)
        )
        return {
            "ticket_id": ticket_id,
            "interrupted": interrupted,
            "turn_running": self.turn_running(session_id),
        }

    async def cancel(
        self,
        ticket_id: str,
        *,
        reason: str | None,
        actor: Literal["user", "orchestrator"],
    ) -> Any:
        """取消工单：停代理、丢收件箱、释放设备门，已提交的流水线不停（§4.5.7）。"""
        ticket = await self.require_open_ticket(ticket_id)
        cancelled = await self._ticket_service.cancel(
            ticket_id, reason=reason or "用户取消"
        )
        if cancelled.result is None:
            await self._write_cancel_result(
                ticket_id, {"cancelled_by": actor, "reason": reason}
            )
        session_id = cancelled.worker_session_id
        if session_id:
            pump = self._pumps.get(session_id)
            if pump is not None:
                await pump.interrupt(
                    "cancelled", abort=lambda: self._abort_calls(session_id)
                )
            else:
                await self._abort_calls(session_id)
        await self.on_ticket_ended(ticket_id, abort_calls=False)
        # M69 的 cancel 记一条 dropped 汇报（广播 report_created）；spec 要求取消后
        # 还发一条 ticket_updated，这里补上。
        fresh = await self._get_ticket(ticket_id)
        if fresh is not None:
            self._publish_ticket("ticket_updated", fresh)
        if actor == "user" and self._orchestrator is not None:
            try:
                await self._orchestrator.notify(
                    f"用户取消了工单「{ticket.title}」：{reason or '没有说明原因'}",
                    passive=True,
                    ref_id=f"ticket:{ticket_id}:cancelled",
                    ticket_id=ticket_id,
                )
            except Exception:  # noqa: BLE001 - 通知总控失败不影响取消结果
                logger.exception("工单 %s 的取消通知总控失败", ticket_id)
        return await self._ticket_service.detail(ticket_id)

    async def on_ticket_ended(self, ticket_id: str, *, abort_calls: bool) -> None:
        """工单结束后的收尾（§4.5.7）：撤回调用、丢收件箱、释放门、停会话与泵。"""
        ticket = await self._get_ticket(ticket_id)
        if ticket is None:
            return
        session_id = ticket.worker_session_id
        if abort_calls and session_id:
            await self._abort_calls(session_id)
        if session_id:
            try:
                await self._inbox.drop_pending(session_id)
            except Exception:  # noqa: BLE001 - 收尾尽力而为
                logger.exception("工单 %s 丢弃 Worker 收件箱失败", ticket_id)
        try:
            await self._device_gate.release(ticket_id)
        except Exception:  # noqa: BLE001 - 释放设备门失败不能挡收尾
            logger.exception("工单 %s 释放设备门失败", ticket_id)
        if session_id:
            await self._finish_sessions(session_id)
            await self._close_pump(session_id)
        await self._deactivate_subscriptions(ticket_id)

    # ------------------------------------------------------------------
    # 流水线事件
    # ------------------------------------------------------------------
    async def on_pipeline_finished(self, pipeline_id: str, status: Any = None) -> None:
        """``FinishHookFanout`` 订阅者：把流水线终态投给订阅它的 Worker。"""
        key = f"pipeline:{pipeline_id}"
        async with self._session_factory() as session:
            pipeline = await PipelineRepository(session).get(pipeline_id)
        if pipeline is None:
            return
        payload = {
            "thread_key": key,
            "pipeline_id": pipeline_id,
            "status": str(status if status is not None else pipeline.status),
            "error_code": pipeline.error_code,
            "summary": pipeline_event_summary(pipeline),
        }
        delivered: list[str] = []
        try:
            delivered = await self._thread_router.deliver(
                key,
                kind=InboxKind.THREAD_EVENT,
                payload=payload,
                ref_id=key,
            )
        except Exception:  # noqa: BLE001 - 投递失败只记日志，不影响终态钩子其余订阅者
            logger.exception("流水线 %s 的终态投递失败", pipeline_id)
        if not delivered and pipeline.agent_session_id:
            # 竞态兜底：订阅在 tool_result 之后异步建立，提交即失败的流水线可能先
            # 触发终态钩子。按流水线的 agent_session_id 反查它的工单。
            owner = await self._get_session(pipeline.agent_session_id)
            if (
                owner is not None
                and str(owner.kind) in (AgentSessionKind.WORKER, AgentSessionKind.SUBAGENT)
                and owner.ticket_id
            ):
                ticket = await self._get_ticket(owner.ticket_id)
                if not _terminal_ticket(ticket):
                    try:
                        await self._thread_router.subscribe(owner.ticket_id, key)
                    except AppError:
                        logger.debug("流水线 %s 补订阅失败", pipeline_id, exc_info=True)
                    except Exception:  # noqa: BLE001 - 竞态兜底不能抛
                        logger.exception("流水线 %s 补订阅异常", pipeline_id)
                    try:
                        delivered = await self._thread_router.deliver(
                            key,
                            kind=InboxKind.THREAD_EVENT,
                            payload=payload,
                            ref_id=key,
                        )
                    except Exception:  # noqa: BLE001
                        logger.exception("流水线 %s 的补投递失败", pipeline_id)
        for session_id in delivered:
            self.wake(session_id)

    # ------------------------------------------------------------------
    # 一轮 Worker 运行
    # ------------------------------------------------------------------
    async def _run_turn(self, session_id: str, handle: TurnHandle) -> bool:
        """跑一轮 Worker（§4.5.3 的七步）；返回是否真正启动了一轮。"""
        session = await self._get_session(session_id)
        if session is None or not session.ticket_id:
            await self._inbox.drop_pending(session_id)
            return True
        ticket = await self._get_ticket(session.ticket_id)
        if _terminal_ticket(ticket):
            await self._inbox.drop_pending(session_id)
            return True

        try:
            spec = self._spec_registry.get(ticket.kind)
        except (AppError, AgentSpecError, KeyError):
            logger.warning("工单 %s 的代理定义 %s 不见了", ticket.id, ticket.kind)
            await self._inbox.drop_pending(session_id)
            return True
        mandate = await self._mandate_for(ticket.id)
        if mandate is None:
            logger.warning("工单 %s 缺少委托记录，跳过这一轮", ticket.id)
            await self._inbox.drop_pending(session_id)
            return True

        self._priorities[session_id] = int(ticket.priority)
        try:
            run = await self._run_service.start(
                session_id=session_id,
                agent_name=spec.name,
                kind=AgentRunKind.WORKER_TURN,
                spec_version=getattr(spec, "spec_version", None),
                ticket_id=ticket.id,
            )
        except AppError as exc:
            if exc.code == ErrorCode.RUN_ALREADY_ACTIVE:
                logger.warning("Worker 会话 %s 已有运行，稍后再试", session_id)
                return False
            raise
        handle.run_id = run.id

        items = await self._inbox.claim(session_id, run_id=run.id, limit=_CLAIM_LIMIT)
        if not items:
            await self._run_service.finish(
                run.id, status=AgentRunStatus.SUCCEEDED, finish_reason="stop"
            )
            return True
        await persist_inbox_messages(
            self._session_factory, self._event_bus, session_id, run.id, items
        )

        state: dict[str, Any] = {
            "role_card": ticket_card(ticket, mandate, spec, self._spec_registry),
            "mandate.ceiling": effective_ceiling(
                spec.risk_ceiling, mandate.risk_ceiling
            ),
            "ticket.title": ticket.title,
        }
        outcome = RunOutcome(status="failed", finish_reason="error")
        try:
            outcome = await run_agent(
                self._app,
                spec,
                session,
                run,
                inbox_items=items,
                cancel_event=handle.cancel_event,
                cancel_reason=lambda: handle.cancel_reason,
                state=state,
            )
        finally:
            ctx = state.get("harness.ctx")
            if not isinstance(ctx, RunContext):
                ctx = RunContext(
                    run_id=run.id,
                    root_run_id=run.id,
                    parent_run_id=None,
                    session_id=session_id,
                    ticket_id=ticket.id,
                    spec=spec,
                    app=self._app,
                    run=run,
                    state=state,
                )
            stored = await self._run_service.get(run.id)
            if stored is not None and str(stored.status) in _ACTIVE_RUN_STATUSES:
                await self._run_service.finish(
                    run.id,
                    status=AgentRunStatus.FAILED,
                    finish_reason="error",
                )
            try:
                await ensure_report(ctx, outcome)
            except Exception:  # noqa: BLE001 - 补汇报失败不能把收尾变成异常
                logger.exception("工单 %s 补汇报失败", ticket.id)
            try:
                await self._device_gate.release(ticket.id)
            except Exception:  # noqa: BLE001
                logger.exception("工单 %s 释放设备门失败", ticket.id)
        return True

    # ------------------------------------------------------------------
    # 事件总线
    # ------------------------------------------------------------------
    async def _on_event(self, event: RunEvent) -> None:
        """``submit_pipeline`` 的工具结果一到就订阅它的流水线话题。"""
        if event.name != "tool_result":
            return
        data = event.data if isinstance(event.data, Mapping) else {}
        if data.get("name") != "submit_pipeline":
            return
        pipeline_id = data.get("pipeline_id")
        if not pipeline_id:
            return
        run_id = event.run_id or data.get("run_id")
        try:
            task = asyncio.create_task(
                self._subscribe_pipeline(
                    ticket_id=data.get("ticket_id"),
                    run_id=str(run_id) if run_id else None,
                    pipeline_id=str(pipeline_id),
                ),
                name=f"subscribe-pipeline-{pipeline_id}",
            )
        except RuntimeError:
            logger.warning("没有事件循环，跳过流水线订阅（%s）", pipeline_id)
            return
        self._tasks.add(task)
        task.add_done_callback(self._tasks.discard)

    async def _subscribe_pipeline(
        self,
        *,
        ticket_id: str | None,
        run_id: str | None,
        pipeline_id: str,
    ) -> None:
        try:
            if not ticket_id and run_id:
                run = await self._run_service.get(run_id)
                ticket_id = getattr(run, "ticket_id", None)
            if not ticket_id:
                return
            await self._thread_router.subscribe(ticket_id, f"pipeline:{pipeline_id}")
        except AppError:
            logger.debug("流水线 %s 订阅失败", pipeline_id, exc_info=True)
        except Exception:  # noqa: BLE001 - 后台订阅任务不能抛成未处理异常
            logger.exception("流水线 %s 订阅异常", pipeline_id)

    # ------------------------------------------------------------------
    # 内部工具
    # ------------------------------------------------------------------
    def _state(self) -> Any:
        return getattr(self._app, "state", None)

    async def _get_session(self, session_id: str) -> AgentSession | None:
        async with self._session_factory() as session:
            return await AgentSessionRepository(session).get(session_id)

    async def _get_ticket(self, ticket_id: str) -> Ticket | None:
        async with self._session_factory() as session:
            return await TicketRepository(session).get(ticket_id)

    async def _mandate_for(self, ticket_id: str) -> Any:
        async with self._session_factory() as session:
            return await MandateRepository(session).get_by_ticket(ticket_id)

    def _worker_spec(self, kind: str) -> Any:
        try:
            spec = self._spec_registry.get(kind)
        except (AppError, AgentSpecError, KeyError):
            spec = None
        if (
            spec is None
            or getattr(spec, "kind", None) != "worker"
            or str(getattr(spec, "name", kind)).startswith("legacy_")
        ):
            raise AppError(
                ErrorCode.INVALID_PARAMETER,
                f"没有叫 {kind} 的 Worker",
                {"kind": kind},
            )
        return spec

    async def _worker_profile(self, ticket: Ticket) -> Any:
        router = getattr(self._state(), "model_router", None)
        if router is None:
            return None
        spec = self._spec_registry.get(ticket.kind)
        role = getattr(spec, "model_role", None) or ModelRole.WORKER
        return await router.resolve(role)

    async def _current_orchestrator_session_id(self) -> str | None:
        if self._orchestrator is None:
            return None
        try:
            return await self._orchestrator.current_session_id()
        except Exception:  # noqa: BLE001 - 取不到总控会话就建无归属工单
            logger.exception("读取当前总控会话失败")
            return None

    def _pump_for(self, session_id: str) -> SessionPump:
        pump = self._pumps.get(session_id)
        if pump is None:
            pump = SessionPump(
                session_id,
                run_turn=lambda handle: self._run_turn(session_id, handle),
                has_work=lambda: self._inbox.has_active_pending(session_id),
                window_ms=lambda: 0,
                slots=self._slots,
                priority=lambda: self._priorities.get(session_id, 1),
            )
            self._pumps[session_id] = pump
        return pump

    async def _close_pump(self, session_id: str) -> None:
        pump = self._pumps.get(session_id)
        if pump is None:
            return
        current = asyncio.current_task()
        turn_task = getattr(pump, "_turn_task", None)
        if turn_task is not None and turn_task is current:
            # 一轮 Worker 在 report 的 handler 里收尾时，当前任务就是泵的这一轮；
            # 在这里 cancel 会把自己也取消掉。移除引用即可：这一轮返回后泵循环
            # 发现没有工作会自己退出，服务关闭时也不需要再关一次。
            self._pumps.pop(session_id, None)
            return
        run = CURRENT_RUN.get()
        if (
            turn_task is not None
            and not turn_task.done()
            and run is not None
            and run.session_id == session_id
        ):
            # report 在 toolexec 的 gather 子任务里结束工单：它不是泵的这一轮，
            # 但这一轮正在等它。直接 cancel 会把正在收尾的运行打断（实测终态
            # 从 report_submitted 变成 failed/error），等这一轮结束再关泵。
            self._schedule_pump_close(session_id)
            return
        self._pumps.pop(session_id, None)
        await pump.close()

    def _schedule_pump_close(self, session_id: str) -> None:
        try:
            task = asyncio.create_task(
                self._close_pump_after_turn(session_id),
                name=f"close-pump-{session_id}",
            )
        except RuntimeError:
            logger.warning("没有事件循环，泵 %s 不能延后关闭", session_id)
            return
        self._tasks.add(task)
        task.add_done_callback(self._tasks.discard)

    async def _close_pump_after_turn(self, session_id: str) -> None:
        """等当前一轮真正结束后再关闭并移除泵（终态 report 的收尾路径）。"""
        pump = self._pumps.get(session_id)
        if pump is None:
            return
        turn_task = getattr(pump, "_turn_task", None)
        if turn_task is not None and not turn_task.done():
            await asyncio.wait({turn_task})
        pump = self._pumps.pop(session_id, None)
        if pump is not None:
            await pump.close()

    async def _abort_calls(self, session_id: str) -> None:
        """撤回该 Worker 会话与 active 子代理会话的挂起确认与提问（§4.5.1）。"""
        state = self._state()
        confirmation = getattr(state, "confirmation_service", None)
        question = getattr(state, "question_service", None)
        for current in [session_id, *await self._active_subagent_ids(session_id)]:
            if confirmation is not None:
                try:
                    await confirmation.abort_session_calls(current)
                except Exception:  # noqa: BLE001 - 撤回失败只记日志
                    logger.exception("撤回会话 %s 的挂起确认失败", current)
            if question is not None:
                try:
                    await question.cancel_pending_for_session(current)
                except Exception:  # noqa: BLE001
                    logger.exception("撤回会话 %s 的挂起提问失败", current)

    async def _active_subagent_ids(self, session_id: str) -> list[str]:
        statement = select(AgentSession.id).where(
            AgentSession.parent_session_id == session_id,
            AgentSession.status == AgentSessionStatus.ACTIVE,
        )
        async with self._session_factory() as session:
            result = await session.execute(statement)
            return [str(row) for row in result.scalars()]

    async def _finish_sessions(self, session_id: str) -> None:
        """Worker 会话与 active 子代理会话置 ``finished``（§4.5.7）。"""
        ids = [session_id, *await self._active_subagent_ids(session_id)]
        now = utcnow()
        async with self._session_factory() as session:
            await session.execute(
                update(AgentSession)
                .where(AgentSession.id.in_(ids))
                .values(status=AgentSessionStatus.FINISHED, updated_at=now)
            )
            await session.commit()

    async def _deactivate_subscriptions(self, ticket_id: str) -> None:
        async with self._session_factory() as session:
            await ThreadSubscriptionRepository(session).deactivate_for_ticket(
                ticket_id, now=utcnow()
            )
            await session.commit()

    async def _write_cancel_result(
        self, ticket_id: str, result: dict[str, Any]
    ) -> None:
        """M69 的 ``cancel`` 不写 ``ticket.result``，这里补上取消者与原因。"""
        async with self._session_factory() as session:
            changed = await TicketRepository(session).update_if_status(
                ticket_id,
                allowed={TicketStatus.CANCELLED},
                values={"result": result},
            )
            if changed:
                await session.commit()
            else:
                await session.rollback()

    def _publish_ticket(self, event: str, ticket: Ticket) -> None:
        """补发 ``ticket_status``：M69 的 ``TicketService`` 负责 create / report 两种，
        取消后的 ``ticket_updated`` 由这里补（§4.3.1 的说明）。"""
        publisher = getattr(self._ticket_service, "_publish_ticket", None)
        if not callable(publisher):
            return
        try:
            publisher(event, ticket)
        except Exception:  # noqa: BLE001 - 广播失败不影响取消
            logger.exception("工单 %s 的 %s 广播失败", ticket.id, event)

    async def _yield_pending_question(self, session_id: str) -> None:
        """新消息到达时让位：pump 忙就跳过该会话已有的 pending 提问（§4.5.2）。"""
        pump = self._pumps.get(session_id)
        if pump is None or not pump.busy:
            return
        service = getattr(self._state(), "question_service", None)
        if service is None:
            return
        try:
            page = await service.list(
                session_id=session_id, status="pending", page=1, size=50
            )
        except Exception:  # noqa: BLE001 - 让位失败不影响新消息
            logger.exception("列举会话 %s 的挂起提问失败", session_id)
            return
        for item in page.get("items") or ():
            question_id = item.get("id") if isinstance(item, Mapping) else None
            if not question_id:
                continue
            try:
                await service.skip(str(question_id), answered_by="superseded")
            except AppError:
                pass
            except Exception:  # noqa: BLE001
                logger.exception("跳过提问 %s 失败", question_id)


def _human_action(value: Any) -> ReportHumanAction | None:
    """把 ``report`` 工具的参数模型换成工单领域的 ``HumanAction``。"""
    if value is None:
        return None
    if isinstance(value, ReportHumanAction):
        return value
    if isinstance(value, Mapping):
        return ReportHumanAction.model_validate(dict(value))
    return ReportHumanAction(
        type=getattr(value, "type"),
        prompt=getattr(value, "prompt"),
        ref_id=getattr(value, "ref_id", None),
    )
