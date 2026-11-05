"""工单服务：状态机、汇报收尾、转发与结束监听（M69 规格 §4.2、§4.5.1–§4.5.3）。

所有状态变化都经 ``TicketRepository.update_if_status`` 做条件更新，``allowed`` 是
非终态集合：两个终态汇报并发时只有一个能改到行，输家回滚并按 ``TICKET_ENDED``
处理。终态的收尾（``ended_at``、停用订阅、丢弃 Worker 收件箱的 pending 项）与
汇报写入在同一事务里；提交之后才转发、广播、通知结束监听者。

服务自己不读写全局状态：仓储用构造时注入的 ``session_factory`` 建会话，
``inbox_service`` / ``thread_router`` / ``spec_registry`` / ``broadcast`` 都由装配
方（T9 的 lifespan）传入，因此测试可以只给一个临时库。
"""

from __future__ import annotations

import asyncio
import inspect
import logging
from collections.abc import Awaitable, Callable, Collection, Iterable, Mapping
from dataclasses import dataclass
from datetime import datetime
from typing import Any

from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from maa_api.agent.tickets.inbox import InboxService
from maa_api.agent.tickets.mandates import build_mandate
from maa_api.agent.tickets.reports import (
    MAX_SUMMARY_CHARS,
    HumanAction,
    ReportInput,
    report_inbox_payload,
    should_forward,
    ticket_status_for,
)
from maa_api.agent.tickets.threads import ThreadRouter
from maa_api.db.models import (
    Mandate,
    ThreadSubscription,
    Ticket,
    TicketReport,
    new_uuid,
    utcnow,
)
from maa_api.db.repositories.base import Page
from maa_api.db.repositories.ticket import (
    AgentInboxRepository,
    MandateRepository,
    ThreadSubscriptionRepository,
    TicketReportRepository,
    TicketRepository,
)
from maa_api.domain.enums import (
    EventSource,
    InboxKind,
    ReportStatus,
    TicketOrigin,
    TicketStatus,
)
from maa_api.domain.errors import AppError, ErrorCode

logger = logging.getLogger(__name__)

__all__ = [
    "BLOCKED_AFTER_RESTART_PROMPT",
    "BLOCKED_AFTER_RESTART_SUMMARY",
    "OrchestratorResolver",
    "ReportOutcome",
    "MemoryExtractor",
    "TicketDetailData",
    "TicketEndListener",
    "TicketEnded",
    "TicketService",
    "block_tickets_after_restart",
    "ticket_status_payload",
]

#: 服务重启打断工单时写进 ``summary`` 的说明（M69 规格 §4.5.6）。
BLOCKED_AFTER_RESTART_SUMMARY = (
    "服务重启时这个工单的运行被中断了。继续之前先查看现场，不要直接重复刚才的设备操作。"
)
#: 服务重启打断工单时写进 ``human_action.prompt`` 的问句。
BLOCKED_AFTER_RESTART_PROMPT = "服务重启打断了这个工单，要继续还是取消？"

#: 状态机的非终态集合：所有条件更新的 ``allowed``。
_ACTIVE_STATUSES = frozenset(
    {TicketStatus.OPEN, TicketStatus.WAITING, TicketStatus.BLOCKED}
)
#: 汇报里带 ``human_action`` 的两个状态（§4.5.2 第 3 步）。
_HUMAN_ACTION_STATUSES = frozenset({ReportStatus.WAITING, ReportStatus.BLOCKED})
#: 把 ``details`` 写进工单 ``result`` 的两个终态。
_RESULT_STATUSES = frozenset({TicketStatus.DONE, TicketStatus.FAILED})
#: ``attach_worker_session`` 的 brief 长度上限。
_MAX_BRIEF_CHARS = 4000
#: 建单标题长度上限。
_MAX_TITLE_CHARS = 64
#: 合法的建单优先级。
_PRIORITIES = (0, 1, 2)


TicketEndListener = Callable[["TicketEnded"], Awaitable[None] | None]
#: 记忆抽取回调：M79 装配方注入 ``extract_candidates`` 的偏函数，终态提交后后台执行。
MemoryExtractor = Callable[["TicketEnded"], Awaitable[Any] | None]
#: 汇报转发目标的解析函数：返回「当前活动的总控会话」id（M70 §3.3）。
OrchestratorResolver = Callable[[], Awaitable[str | None]]


@dataclass(frozen=True, slots=True)
class TicketEnded:
    """工单进入终态的通知载荷；结束监听者收到它（M70 用来收掉 Worker）。"""

    ticket_id: str
    status: TicketStatus
    worker_session_id: str | None


@dataclass(frozen=True, slots=True)
class TicketDetailData:
    """工单详情：工单、委托、订阅、最近 5 条汇报与 Worker 是否在运行。"""

    ticket: Ticket
    mandate: Mandate
    subscriptions: list[ThreadSubscription]
    recent_reports: list[TicketReport]
    turn_running: bool


@dataclass(frozen=True, slots=True)
class ReportOutcome:
    """一次汇报的结果；``forwarded_inbox_id`` 为空表示还没进总控收件箱。"""

    report: TicketReport
    ticket: Ticket
    forwarded_inbox_id: int | None


def ticket_status_payload(event: str, ticket: Ticket) -> dict[str, Any]:
    """WS ``ticket_status`` 的 ``data``（M69 规格 §4.3）。"""
    return {
        "event": event,
        "ticket_id": ticket.id,
        "title": ticket.title,
        "kind": ticket.kind,
        "status": ticket.status,
        "last_report_status": ticket.last_report_status,
        "summary": ticket.summary,
        "human_action": ticket.human_action,
    }


def _not_found(ticket_id: str) -> AppError:
    return AppError(
        ErrorCode.TICKET_NOT_FOUND,
        "没有这个工单",
        {"ticket_id": ticket_id},
    )


def _ended(ticket_id: str) -> AppError:
    return AppError(
        ErrorCode.TICKET_ENDED,
        "工单已结束",
        {"ticket_id": ticket_id},
    )


def _invalid(message: str, details: dict[str, Any] | None = None) -> AppError:
    return AppError(ErrorCode.INVALID_PARAMETER, message, details)


class TicketService:
    """工单的建、查、汇报、取消与人工介入；事务边界由本服务掌握。"""

    def __init__(
        self,
        session_factory: async_sessionmaker[AsyncSession],
        *,
        inbox_service: InboxService,
        thread_router: ThreadRouter,
        spec_registry: Any,
        broadcast: Callable[[str, dict[str, Any]], None] | None = None,
        clock: Callable[[], datetime] = utcnow,
    ) -> None:
        self._session_factory = session_factory
        self._inbox_service = inbox_service
        self._thread_router = thread_router
        self._spec_registry = spec_registry
        self._broadcast = broadcast
        self._clock = clock
        self._end_listeners: list[TicketEndListener] = []
        self._orchestrator_resolver: OrchestratorResolver | None = None
        self._memory_extractor: MemoryExtractor | None = None
        #: M81 T5：事件管线建单时按需解析 Worker 服务（建 Worker 会话 + 写任务说明）。
        self._worker_service_provider: Callable[[], Any] | None = None
        #: 终态后起的记忆抽取后台任务；持有引用避免任务被回收，测试可等它们收尾。
        self._memory_tasks: set[asyncio.Task[Any]] = set()

    def bind_worker_service(self, provider: Callable[[], Any]) -> None:
        """注入 Worker 服务的惰性解析函数（M81 事件管线用）。

        ``create(brief=...)`` 需要建 Worker 会话时用它；装配顺序上 Worker 服务晚于
        本服务，所以传进来的是无参函数而不是实例。没绑定时带 ``brief`` 建单报
        ``SERVICE_UNAVAILABLE``，不带 ``brief`` 的既有调用不受影响。
        """
        self._worker_service_provider = provider

    def add_end_listener(self, listener: TicketEndListener) -> None:
        """注册工单结束监听者；按注册顺序在广播之后调用。"""
        self._end_listeners.append(listener)

    def bind_memory_extractor(self, extractor: MemoryExtractor) -> None:
        """绑定终态记忆抽取回调；作为结束监听者注册一次，提交后放到后台任务里。

        抽取自己起 ``memory_extract`` 运行、失败只记日志（规格 §4.5、§8）。装配方
        传 ``extract_candidates`` 的偏函数；测试可传任意协程替身。重复绑定时只换
        回调，不会重复注册监听者。
        """
        if self._memory_extractor is None:
            self.add_end_listener(self._extract_memory_after_end)
        self._memory_extractor = extractor

    async def _extract_memory_after_end(self, ended: TicketEnded) -> None:
        """终态监听者：调度后台抽取后立刻返回，接口不等模型。"""
        extractor = self._memory_extractor
        if extractor is None:
            return
        try:
            task = asyncio.create_task(
                self._run_memory_extraction(extractor, ended),
                name=f"memory-extract-{ended.ticket_id}",
            )
        except RuntimeError:  # pragma: no cover - 正常调用总在事件循环里
            logger.warning("工单结束记忆抽取没有事件循环：ticket=%s", ended.ticket_id)
            return
        self._memory_tasks.add(task)
        task.add_done_callback(self._memory_tasks.discard)

    @staticmethod
    async def _run_memory_extraction(
        extractor: MemoryExtractor, ended: TicketEnded
    ) -> None:
        """跑抽取回调；任何失败只记日志，不改变已提交的工单终态。"""
        try:
            result = extractor(ended)
            if inspect.isawaitable(result):
                await result
        except Exception:  # noqa: BLE001 - 后台任务失败不能扩散到调用方
            logger.warning(
                "工单终态记忆抽取失败：ticket=%s status=%s",
                ended.ticket_id,
                ended.status,
                exc_info=True,
            )

    def bind_orchestrator_resolver(self, resolver: OrchestratorResolver) -> None:
        """注入汇报转发的目标解析函数（M70 §3.3）：解析函数优先于工单上的会话 id。

        总控重置后旧会话会归档，汇报要进「当前活动的总控会话」；解析函数由装配
        方（``OrchestratorService.current_session_id``）提供，本服务不猜。
        """
        self._orchestrator_resolver = resolver

    async def _report_session_id(self, ticket: Ticket) -> str | None:
        """汇报的目标会话：绑定了解析函数就用它，否则沿用 M69 的工单会话 id。"""
        if self._orchestrator_resolver is not None:
            return await self._orchestrator_resolver()
        return ticket.orchestrator_session_id

    def _publish_ticket(self, event: str, ticket: Ticket) -> None:
        """广播 ``ticket_status``；没有广播函数或广播失败都不影响服务。"""
        if self._broadcast is None:
            return
        try:
            self._broadcast("ticket_status", ticket_status_payload(event, ticket))
        except Exception:
            logger.exception("工单状态广播失败（工单 %s，事件 %s）", ticket.id, event)

    async def _notify_end(self, ended: TicketEnded) -> None:
        """依次调用结束监听者；单个监听者失败只记日志。"""
        for listener in tuple(self._end_listeners):
            try:
                result = listener(ended)
                if inspect.isawaitable(result):
                    await result
            except Exception:
                logger.exception("工单结束监听者失败（工单 %s）", ended.ticket_id)

    async def create(
        self,
        *,
        kind: str,
        title: str,
        scope: str,
        origin: TicketOrigin,
        source: EventSource,
        sender: str | None = None,
        rule_id: str | None = None,
        priority: int = 1,
        orchestrator_session_id: str | None = None,
        write_brief: bool = True,
        brief: str | None = None,
        strength: str | None = None,
        risk_ceiling: str | None = None,
        policy_version: str | None = None,
        source_trigger_id: int | None = None,
    ) -> tuple[Ticket, Mandate]:
        """校验代理定义与委托后，把工单和委托写进同一事务（§4.5.3）。

        ``write_brief`` 是 M80 §4.5.8 给分叉留的开关：分叉把历史整段复制过来，
        传 ``False`` 表示不要给新 Worker 写任务说明。本入口自身不写收件箱——任务
        说明由 :meth:`attach_worker_session` 在 Worker 会话建好之后写入，所以这里
        只保留参数，不改变任何既有行为。

        M81 T5 的五个可选参数（默认 ``None``，不带时与 M69 完全一致）：
        ``source_trigger_id`` 写进 ``ticket.source_trigger_id``；``strength`` /
        ``risk_ceiling`` / ``policy_version`` 交给 :func:`build_mandate`
        （强度必须与 origin 一致、上限只能收紧）；``brief`` 非空时在建单之后用
        ``bind_worker_service`` 注入的 Worker 服务建会话并把说明写进它的收件箱。
        """
        del write_brief  # 见 docstring：收件箱写入在 attach_worker_session。
        title_text = title.strip() if isinstance(title, str) else ""
        if not 1 <= len(title_text) <= _MAX_TITLE_CHARS:
            raise _invalid(f"title 去首尾空白后必须是 1–{_MAX_TITLE_CHARS} 字")
        if priority not in _PRIORITIES:
            raise _invalid("priority 只能是 0、1、2")
        brief_text: str | None = None
        if brief is not None:
            brief_text = brief.strip() if isinstance(brief, str) else ""
            if not 1 <= len(brief_text) <= _MAX_BRIEF_CHARS:
                raise _invalid(
                    f"brief 去首尾空白后必须是 1–{_MAX_BRIEF_CHARS} 字"
                )

        spec = self._spec_registry.get(kind)
        if spec.kind != "worker":
            raise _invalid("这个代理定义不能负责工单", {"kind": kind})

        now = self._clock()
        ticket_id = new_uuid()
        mandate = build_mandate(
            ticket_id=ticket_id,
            scope=scope,
            origin=origin,
            source=source,
            sender=sender,
            rule_id=rule_id,
            spec_ceiling=spec.risk_ceiling,
            strength=strength,
            risk_ceiling=risk_ceiling,
            policy_version_value=policy_version,
        )
        ticket = Ticket(
            id=ticket_id,
            title=title_text,
            kind=kind,
            status=TicketStatus.OPEN.value,
            origin=str(TicketOrigin(origin)),
            priority=priority,
            orchestrator_session_id=orchestrator_session_id,
            source_trigger_id=source_trigger_id,
            created_at=now,
            updated_at=now,
        )
        async with self._session_factory() as session:
            stored_ticket = await TicketRepository(session).create(ticket)
            stored_mandate = await MandateRepository(session).create(mandate)
            await session.commit()

        self._publish_ticket("ticket_created", stored_ticket)
        if brief_text is not None:
            stored_ticket = await self._start_worker(stored_ticket, brief_text)
        return stored_ticket, stored_mandate

    async def _start_worker(self, ticket: Ticket, brief: str) -> Ticket:
        """建 Worker 会话、记到工单上，并写一条渲染认得的 ``system`` 任务说明。

        与 ``WorkerService.open_ticket`` 的收尾同口径：``attach_worker_session`` 写的
        M69 ``ticket_brief``（只有 ``event/title/brief``，没有 ``text``）渲染出来是
        空的，所以先丢掉它，再按 M70 §4.5.9 的 ``system`` 形状写一条带 ``text`` 的。
        """
        provider = self._worker_service_provider
        worker_service = provider() if provider is not None else None
        if worker_service is None:
            raise AppError(
                ErrorCode.SERVICE_UNAVAILABLE,
                "Worker 服务还没就绪，暂时不能创建工单",
            )
        session = await worker_service.ensure_worker_session(ticket)
        session_id = str(session.id)
        updated = await self.attach_worker_session(ticket.id, session_id, brief=brief)
        await self._inbox_service.drop_pending(session_id)
        await self._inbox_service.put(
            session_id,
            InboxKind.SYSTEM,
            thread_key=f"ticket:{ticket.id}",
            ref_id=f"brief:{ticket.id}",
            payload={
                "text": f"事件处理任务：\n{brief}",
                "passive": False,
                "origin": "trigger",
                "ticket_id": ticket.id,
            },
        )
        return updated

    async def attach_worker_session(
        self, ticket_id: str, worker_session_id: str, *, brief: str
    ) -> Ticket:
        """把 Worker 会话记到工单上，并往它的收件箱放一条 ``ticket_brief``。"""
        brief_text = brief.strip() if isinstance(brief, str) else ""
        if not 1 <= len(brief_text) <= _MAX_BRIEF_CHARS:
            raise _invalid(f"brief 去首尾空白后必须是 1–{_MAX_BRIEF_CHARS} 字")

        now = self._clock()
        async with self._session_factory() as session:
            repo = TicketRepository(session)
            ticket = await repo.get(ticket_id)
            if ticket is None:
                raise _not_found(ticket_id)
            if TicketStatus(ticket.status).is_terminal:
                raise _ended(ticket_id)
            changed = await repo.update_if_status(
                ticket_id,
                allowed=_ACTIVE_STATUSES,
                values={"worker_session_id": worker_session_id, "updated_at": now},
            )
            if not changed:
                await session.rollback()
                raise _ended(ticket_id)
            ticket = await repo.get(ticket_id)
            await session.commit()

        await self._inbox_service.put(
            worker_session_id,
            InboxKind.SYSTEM,
            thread_key=f"ticket:{ticket_id}",
            ref_id=ticket_id,
            payload={
                "event": "ticket_brief",
                "ticket_id": ticket_id,
                "title": ticket.title,
                "brief": brief_text,
            },
        )
        return ticket

    async def get(self, ticket_id: str) -> Ticket:
        """按 id 取工单；不存在抛 ``TICKET_NOT_FOUND``。"""
        async with self._session_factory() as session:
            ticket = await TicketRepository(session).get(ticket_id)
        if ticket is None:
            raise _not_found(ticket_id)
        return ticket

    async def update_result(
        self, ticket_id: str, patch: Mapping[str, Any]
    ) -> Ticket | None:
        """把 ``patch`` 合并进 ``ticket.result`` 并保存；不触发汇报逻辑。

        只合并顶层键：已有键保留，``patch`` 的同名键覆盖。工单不存在返回
        ``None``。诊断工单用它写 ``incident_key``（M73 规格 §4.1）；终态汇报写
        ``result`` 时也走合并，保证 ``incident_key`` 不会被删掉。
        """
        if not isinstance(patch, Mapping):
            raise _invalid("patch 必须是 JSON 对象")
        now = self._clock()
        async with self._session_factory() as session:
            tickets = TicketRepository(session)
            ticket = await tickets.get(ticket_id)
            if ticket is None:
                return None
            merged = dict(ticket.result or {})
            merged.update(dict(patch))
            ticket.result = merged
            ticket.updated_at = now
            await session.commit()
            return ticket

    async def detail(self, ticket_id: str) -> TicketDetailData:
        """工单详情：委托、订阅、最近 5 条汇报与 Worker 运行状态。"""
        async with self._session_factory() as session:
            tickets = TicketRepository(session)
            ticket = await tickets.get(ticket_id)
            if ticket is None:
                raise _not_found(ticket_id)
            mandate = await MandateRepository(session).get_by_ticket(ticket_id)
            if mandate is None:
                raise RuntimeError(f"工单 {ticket_id} 缺少委托记录")
            subscriptions = await ThreadSubscriptionRepository(
                session
            ).list_for_ticket(ticket_id)
            recent_reports = await TicketReportRepository(session).recent(
                ticket_id, limit=5
            )
            turn_running = await tickets.worker_turn_running(ticket.worker_session_id)
        return TicketDetailData(
            ticket=ticket,
            mandate=mandate,
            subscriptions=subscriptions,
            recent_reports=recent_reports,
            turn_running=turn_running,
        )

    async def list(
        self,
        *,
        statuses: Collection[TicketStatus] | None = None,
        kind: str | None = None,
        origin: TicketOrigin | None = None,
        page: int = 1,
        size: int = 20,
    ) -> Page[Ticket]:
        """分页读取工单；过滤条件都可选。"""
        async with self._session_factory() as session:
            return await TicketRepository(session).list(
                statuses=statuses,
                kind=kind,
                origin=origin,
                page=page,
                size=size,
            )

    async def list_reports(
        self, ticket_id: str, *, page: int = 1, size: int = 20
    ) -> Page[TicketReport]:
        """分页读取某工单的汇报；工单不存在抛 ``TICKET_NOT_FOUND``。"""
        async with self._session_factory() as session:
            if await TicketRepository(session).get(ticket_id) is None:
                raise _not_found(ticket_id)
            return await TicketReportRepository(session).list(
                ticket_id, page=page, size=size
            )

    async def record_report(
        self,
        ticket_id: str,
        report: ReportInput,
        *,
        run_id: str | None = None,
    ) -> ReportOutcome:
        """记录一次汇报，按状态机更新工单，必要时转发并通知（§4.5.2）。"""
        now = self._clock()
        dumped = report.model_dump(mode="json")
        human_action = dumped["human_action"]
        details = dumped["details"]
        target_status = ticket_status_for(report.status)
        terminal = target_status.is_terminal

        async with self._session_factory() as session:
            tickets = TicketRepository(session)
            ticket = await tickets.get(ticket_id)
            if ticket is None:
                raise _not_found(ticket_id)
            if TicketStatus(ticket.status).is_terminal:
                raise _ended(ticket_id)

            stored_report = await TicketReportRepository(session).create(
                TicketReport(
                    ticket_id=ticket_id,
                    run_id=run_id,
                    status=report.status.value,
                    summary=report.summary,
                    human_action=human_action,
                    details=details,
                    forwarded=False,
                    created_at=now,
                )
            )
            values: dict[str, Any] = {
                "status": target_status.value,
                "summary": report.summary,
                "last_report_status": report.status.value,
                "human_action": (
                    human_action
                    if report.status in _HUMAN_ACTION_STATUSES
                    else None
                ),
                "updated_at": now,
            }
            if target_status in _RESULT_STATUSES:
                # 合并而不是覆盖：诊断工单的 ``incident_key`` 写在建单时，终态
                # 汇报不得把它删掉（M73 规格 §4.1）。
                merged_result = dict(ticket.result or {})
                merged_result.update(details or {})
                values["result"] = merged_result or None
            if terminal:
                values["ended_at"] = now

            changed = await tickets.update_if_status(
                ticket_id, allowed=_ACTIVE_STATUSES, values=values
            )
            if not changed:
                # 并发终态汇报的输家：连刚插入的汇报一起回滚。
                await session.rollback()
                raise _ended(ticket_id)
            if terminal:
                await ThreadSubscriptionRepository(session).deactivate_for_ticket(
                    ticket_id, now=now
                )
                if ticket.worker_session_id:
                    await AgentInboxRepository(session).drop_pending(
                        ticket.worker_session_id, now=now
                    )
            ticket = await tickets.get(ticket_id)
            await session.commit()

        forwarded_inbox_id: int | None = None
        if should_forward(report.status, ticket.origin):
            forwarded_inbox_id = await self._thread_router.to_orchestrator(
                kind=InboxKind.REPORT,
                thread_key=f"ticket:{ticket_id}",
                ref_id=str(stored_report.id),
                payload=report_inbox_payload(ticket, stored_report),
                preferred_session_id=await self._report_session_id(ticket),
            )
            if forwarded_inbox_id is not None:
                async with self._session_factory() as session:
                    await TicketReportRepository(session).mark_forwarded(
                        stored_report.id
                    )
                    await session.commit()

        self._publish_ticket("report_created", ticket)
        if terminal:
            await self._notify_end(
                TicketEnded(
                    ticket_id=ticket.id,
                    status=TicketStatus(ticket.status),
                    worker_session_id=ticket.worker_session_id,
                )
            )
        return ReportOutcome(
            report=stored_report,
            ticket=ticket,
            forwarded_inbox_id=forwarded_inbox_id,
        )

    async def cancel(self, ticket_id: str, *, reason: str | None = None) -> Ticket:
        """取消工单：等价于记一条 ``dropped`` 汇报（§4.5.2 末尾）。"""
        summary = "已取消" if not reason else f"已取消：{reason}"
        outcome = await self.record_report(
            ticket_id,
            ReportInput(
                status=ReportStatus.DROPPED,
                summary=summary[:MAX_SUMMARY_CHARS],
            ),
        )
        return outcome.ticket

    async def await_human(
        self, ticket_id: str, action: HumanAction
    ) -> Ticket | None:
        """把工单改成 ``waiting`` 并记下待办；工单已结束时返回 ``None``。"""
        now = self._clock()
        async with self._session_factory() as session:
            repo = TicketRepository(session)
            ticket = await repo.get(ticket_id)
            if ticket is None:
                raise _not_found(ticket_id)
            if TicketStatus(ticket.status).is_terminal:
                return None
            changed = await repo.update_if_status(
                ticket_id,
                allowed=_ACTIVE_STATUSES,
                values={
                    "status": TicketStatus.WAITING.value,
                    "human_action": action.model_dump(mode="json"),
                    "updated_at": now,
                },
            )
            if not changed:
                await session.rollback()
                return None
            ticket = await repo.get(ticket_id)
            await session.commit()

        self._publish_ticket("ticket_updated", ticket)
        return ticket

    async def human_resolved(self, ticket_id: str, ref_id: str) -> Ticket | None:
        """人工待办解决：仅当 ``waiting`` 且 ``ref_id`` 相同才改回 ``open``。"""
        now = self._clock()
        async with self._session_factory() as session:
            repo = TicketRepository(session)
            ticket = await repo.get(ticket_id)
            if ticket is None:
                raise _not_found(ticket_id)
            if TicketStatus(ticket.status).is_terminal:
                return None
            human_action = ticket.human_action or {}
            if (
                ticket.status != TicketStatus.WAITING
                or human_action.get("ref_id") != ref_id
            ):
                return None
            changed = await repo.update_if_status(
                ticket_id,
                allowed=_ACTIVE_STATUSES,
                values={
                    "status": TicketStatus.OPEN.value,
                    "human_action": None,
                    "updated_at": now,
                },
            )
            if not changed:
                await session.rollback()
                return None
            ticket = await repo.get(ticket_id)
            await session.commit()

        self._publish_ticket("ticket_updated", ticket)
        return ticket

    async def forward_pending_reports(self, *, limit: int = 50) -> int:
        """把还没有进总控收件箱的汇报补转；遇到没有总控会话就停（§4.5.2）。"""
        async with self._session_factory() as session:
            reports = await TicketReportRepository(session).list_unforwarded(
                limit=limit
            )
            ticket_repo = TicketRepository(session)
            pending: list[tuple[TicketReport, Ticket]] = []
            for report in reports:
                ticket = await ticket_repo.get(report.ticket_id)
                if ticket is not None:
                    pending.append((report, ticket))

        forwarded = 0
        for report, ticket in pending:
            inbox_id = await self._thread_router.to_orchestrator(
                kind=InboxKind.REPORT,
                thread_key=f"ticket:{ticket.id}",
                ref_id=str(report.id),
                payload=report_inbox_payload(ticket, report),
                preferred_session_id=await self._report_session_id(ticket),
            )
            if inbox_id is None:
                break
            async with self._session_factory() as session:
                await TicketReportRepository(session).mark_forwarded(report.id)
                await session.commit()
            forwarded += 1
        return forwarded


async def block_tickets_after_restart(
    session: AsyncSession,
    ticket_ids: Iterable[str],
    *,
    now: datetime,
) -> list[str]:
    """重启恢复：把 ``open`` / ``waiting`` 的工单改成 ``blocked`` 并各补一条汇报。

    供 ``RunRecovery`` 在把遗留运行改成 ``interrupted`` 的同一事务里调用；不提交、
    不广播（此时 ``ticket_service`` 还没装配），补转由 lifespan 的
    ``forward_pending_reports`` 负责（§4.5.6）。
    """
    human_action = {
        "type": "decision",
        "prompt": BLOCKED_AFTER_RESTART_PROMPT,
        "ref_id": None,
    }
    tickets = TicketRepository(session)
    reports = TicketReportRepository(session)
    blocked: list[str] = []
    for ticket_id in dict.fromkeys(ticket_ids):
        changed = await tickets.update_if_status(
            str(ticket_id),
            allowed={TicketStatus.OPEN, TicketStatus.WAITING},
            values={
                "status": TicketStatus.BLOCKED.value,
                "last_report_status": ReportStatus.BLOCKED.value,
                "summary": BLOCKED_AFTER_RESTART_SUMMARY,
                "human_action": human_action,
                "updated_at": now,
            },
        )
        if not changed:
            continue
        await reports.create(
            TicketReport(
                ticket_id=str(ticket_id),
                run_id=None,
                status=ReportStatus.BLOCKED.value,
                summary=BLOCKED_AFTER_RESTART_SUMMARY,
                human_action=human_action,
                details=None,
                forwarded=False,
                created_at=now,
            )
        )
        blocked.append(str(ticket_id))
    return blocked
