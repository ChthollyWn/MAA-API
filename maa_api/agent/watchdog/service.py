"""M83 值守服务：周期巡检、告警落库、有限动作与通知（spec §4.5、§5 T2）。

一轮 ``run_once`` 的顺序与 spec §4.5 的伪代码一致：

1. ``enabled=false`` 直接返回；距上次成功不足 ``watchdog.interval_seconds`` 也返回
   （job 仍每 60 秒触发，见 :data:`WATCHDOG_JOB_INTERVAL_SECONDS`）；
2. 组装 :class:`WatchdogSnapshot`（只读数据库与进程内记录器，不调模型、不碰设备）；
3. 跑 08 §6.1 的六项确定性检查；
4. 对其中 ``ticket_stale`` / ``ticket_churn`` 挑中的工单跑语义检查（每轮至多 2 张、
   每张每小时至多 1 次）：建 ``kind=watchdog_check`` 运行，用 ``complete_structured``
   向 ``watchdog`` 定义要 ``watchdog_findings``；模型失败跳过该工单；
5. 逐条 ``upsert_open_alert``；重复命中只更新 ``evidence``（G1 的 store 语义）；
6. ``mode=auto`` 才执行 08 §6.2 的动作，``mode=record`` 只写 ``action_status=recorded``；
   语义检查的 ``mark_blocked`` 还要求该工单本轮被确定性检查挑中；
7. 条件消失的未解除告警自动写 ``resolved_at``；
8. ``last_run_at`` 只在整轮成功后更新，供 G4 的概况端点读取。

动作失败只写 ``action_status=failed`` 与 ``evidence.error``，不向外抛；critical
新插入时按 spec §4.5 的四条通知：WebSocket ``agent_event`` / ``watchdog_alert``、
总控收件箱 ``system``、待你处理（前端读 severity，后端不另写来源）、无可见页面时
``WebPushService.broadcast``。warn / info 只进告警列表。
"""

from __future__ import annotations

import asyncio
import inspect
import logging
from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta
from typing import Any, Awaitable, Callable

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from maa_api.agent.llm.schemas.watchdog_findings import WatchdogFindings
from maa_api.agent.llm.structured import complete_structured
from maa_api.agent.specs.registry import builtin_spec
from maa_api.agent.tickets.reports import (
    MAX_SUMMARY_CHARS,
    HumanAction,
    ReportInput,
)
from maa_api.agent.watchdog.checks import (
    CHECK_FUNCTIONS,
    TICKET_CHURN_WINDOW_MINUTES,
    AlertDraft,
    CoreSample,
    PipelineSample,
    QueueSample,
    ReportSample,
    RunSample,
    TicketSample,
    WatchdogSnapshot,
    WatchdogThresholds,
    WorkerRunSample,
    deterministic_checks,
)
from maa_api.agent.watchdog.recorder import WatchdogRecorder
from maa_api.agent.watchdog.store import (
    draft_key,
    resolve_condition_gone_async,
    upsert_open_alert_async,
)
from maa_api.db.models import (
    AgentInbox,
    AgentRun,
    AgentSession,
    DeviceLease,
    Pipeline,
    ThreadSubscription,
    Ticket,
    TicketReport,
    WatchdogAlert,
    utcnow,
)
from maa_api.db.repositories.agent import (
    AgentRunRepository,
    AgentSessionRepository,
)
from maa_api.db.repositories.pipeline import PipelineRepository
from maa_api.db.repositories.ticket import (
    MandateRepository,
    ThreadSubscriptionRepository,
    TicketReportRepository,
    TicketRepository,
)
from maa_api.domain.enums import (
    AgentRunKind,
    AgentRunStatus,
    AgentSessionKind,
    AlertActionStatus,
    AlertSeverity,
    AlertTargetKind,
    InboxKind,
    InboxStatus,
    ModelRole,
    PipelineStatus,
    ReportStatus,
    TicketStatus,
)
from maa_api.domain.errors import AppError
from maa_api.settings import get_settings

logger = logging.getLogger(__name__)

__all__ = [
    "ACTION_BY_CHECK",
    "INTERRUPT_GRACE_SECONDS",
    "SEMANTIC_MAX_TICKETS_PER_ROUND",
    "SEMANTIC_MIN_INTERVAL_SECONDS",
    "WATCHDOG_AGENT",
    "WATCHDOG_FINDINGS_SCHEMA",
    "WATCHDOG_JOB_ID",
    "WATCHDOG_JOB_INTERVAL_SECONDS",
    "ActionResult",
    "WatchdogService",
]

#: lifespan 注册的 job id 与触发间隔（spec §4.2/§4.5）。
WATCHDOG_JOB_ID = "maa-watchdog"
WATCHDOG_JOB_INTERVAL_SECONDS = 60

#: ``interrupt_run`` 走完 M70 的中断入口后等这么久；仍 running 就写 interrupted。
INTERRUPT_GRACE_SECONDS = 30.0

#: 单设备部署下的设备 id（与 ``agent.lease.LEASE_DEVICE_ID`` 一致）。
DEFAULT_DEVICE_ID = "default"

#: 动作失败时 ``evidence.error.message`` 的截断长度。
MAX_ACTION_ERROR_CHARS = 500

#: 检查函数名 → ``auto`` 档的动作名；没有映射的检查只记录（``ticket_churn``）。
ACTION_BY_CHECK: dict[str, str] = {
    "run_heartbeat_stale": "interrupt_run",
    "ticket_stale": "mark_blocked",
    "pipeline_no_callback": "stop_pipeline",
    "core_not_ready": "restart_core",
    "queue_stuck": "wake_queue",
}

#: 语义检查的代理名与输出 schema（spec §4.5、§4.7）。
WATCHDOG_AGENT = "watchdog"
WATCHDOG_FINDINGS_SCHEMA = "watchdog_findings"

#: 语义检查的确定性来源：只有被这两项挑中的工单才调模型（spec §4.5）。
SEMANTIC_SOURCE_CHECKS = frozenset({"ticket_stale", "ticket_churn"})
#: 每轮至多 2 张、每张每小时至多 1 次（spec §4.5；不做成配置）。
SEMANTIC_MAX_TICKETS_PER_ROUND = 2
SEMANTIC_MIN_INTERVAL_SECONDS = 3600

#: 工单快照卡里最多带几条最近汇报、委托与汇报摘要的截断长度。
MAX_CARD_REPORTS = 5
MAX_CARD_MANDATE_CHARS = 2000
MAX_CARD_REPORT_CHARS = 300

#: 判定「Worker 还有活动运行」的状态；与 AgentRunRepository 的活动态一致。
ACTIVE_RUN_STATUSES = (
    AgentRunStatus.RUNNING.value,
    AgentRunStatus.WAITING_HUMAN.value,
)

#: 内核/设备状态用短字符串比较，避免把 supervisior 与设备服务拖进 import 链。
CORE_READY = "ready"
DEVICE_CONNECTED = "connected"

#: 队列检查用的环回 core id（单设备部署）。
DEFAULT_CORE_ID = "default"


def _str(value: Any) -> str:
    """枚举与字符串统一成库里的短字符串。"""
    return value.value if hasattr(value, "value") else str(value)


def _error_payload(exc: BaseException) -> dict[str, str]:
    """动作异常的 ``evidence.error``：异常类型 + 截断后的消息（spec §4.5）。"""
    return {
        "type": type(exc).__name__,
        "message": str(exc)[:MAX_ACTION_ERROR_CHARS],
    }


def _local_time(value: datetime | None) -> str:
    """数据库里的 UTC-naive 时间转本地时区的可读写法；卡片给模型看。"""
    if value is None:
        return "未知"
    if value.tzinfo is None:
        value = value.replace(tzinfo=UTC)
    return value.astimezone().strftime("%Y-%m-%d %H:%M:%S")


@dataclass(frozen=True)
class ActionResult:
    """一次动作的结果；``status`` 直接写 ``watchdog_alert.action_status``。"""

    action: str | None
    status: AlertActionStatus
    #: 动作的附加事实（跳过原因、命中的中断入口等），写进 ``evidence.action``。
    detail: dict[str, Any] = field(default_factory=dict)
    #: 失败时的 ``evidence.error``；成功/跳过为 ``None``。
    error: dict[str, str] | None = None


class WatchdogService:
    """值守的唯一入口；依赖都从构造注入，测试可全部换成熟替身。"""

    def __init__(
        self,
        session_factory: async_sessionmaker[AsyncSession],
        *,
        recorder: WatchdogRecorder | None = None,
        pipeline_runner: Any = None,
        queue_service: Any = None,
        core_supervisor: Any = None,
        device_manager: Any = None,
        run_service: Any = None,
        model_router: Any = None,
        cost_ledger: Any = None,
        ticket_service: Any = None,
        worker_service: Any = None,
        orchestrator_service: Any = None,
        thread_router: Any = None,
        webpush_service: Any = None,
        has_visible_client: Callable[[], bool] | None = None,
        restart_core: Callable[[], Awaitable[Any]] | None = None,
        broadcast: Callable[[str, dict[str, Any]], Any] | None = None,
        settings_provider: Callable[[], Any] = get_settings,
        clock: Callable[[], datetime] = utcnow,
        sleep: Callable[[float], Awaitable[Any]] = asyncio.sleep,
        interrupt_grace_seconds: float = INTERRUPT_GRACE_SECONDS,
        core_id: str = DEFAULT_CORE_ID,
    ) -> None:
        self._session_factory = session_factory
        self._recorder = recorder or WatchdogRecorder()
        self._pipeline_runner = pipeline_runner
        self._queue_service = queue_service
        self._core_supervisor = core_supervisor
        self._device_manager = device_manager
        self._run_service = run_service
        self._model_router = model_router
        self._cost_ledger = cost_ledger
        self._ticket_service = ticket_service
        self._worker_service = worker_service
        self._orchestrator_service = orchestrator_service
        self._thread_router = thread_router
        self._webpush_service = webpush_service
        self._has_visible_client = has_visible_client
        self._restart_core_callback = restart_core
        self._broadcast = broadcast
        self._settings_provider = settings_provider
        self._clock = clock
        self._sleep = sleep
        self._interrupt_grace_seconds = max(float(interrupt_grace_seconds), 0.0)
        self._core_id = core_id

        #: 上一轮成功结束的时刻；进程内状态，重启后清空（端点据此显示「上次巡检」）。
        self._last_run_at: datetime | None = None
        #: 内核状态与进入该状态的时刻；CoreSupervisor 本身不记持续时间。
        self._core_status: str | None = None
        self._core_state_since: datetime | None = None
        #: 每张工单上次语义检查的时刻；进程内字典，重启后清空可接受（spec §4.5）。
        self._semantic_last_at: dict[str, datetime] = {}
        self._closed = False

    # ------------------------------------------------------------------
    # 对外状态
    # ------------------------------------------------------------------
    @property
    def last_run_at(self) -> datetime | None:
        """上一轮整轮成功的 ``now``；G4 的 ``GET /api/agent/watchdog`` 读它。"""
        return self._last_run_at

    # ------------------------------------------------------------------
    # 一轮巡检
    # ------------------------------------------------------------------
    async def run_once(self, now: datetime | None = None) -> list[WatchdogAlert]:
        """执行一轮巡检，返回本轮命中的告警行（被闸门拦下时返回空列表）。"""
        if self._closed:
            return []
        settings = self._settings_provider()
        watchdog = settings.watchdog
        stamp = now or self._clock()
        if not watchdog.enabled:
            return []
        if self._last_run_at is not None and (
            stamp - self._last_run_at
            < timedelta(seconds=int(watchdog.interval_seconds))
        ):
            return []

        snapshot = await self.snapshot(stamp)
        deterministic = deterministic_checks(snapshot)
        semantic = await self._semantic_checks(deterministic, stamp)
        processed: list[WatchdogAlert] = []
        for draft in [*deterministic, *semantic]:
            alert, inserted = await self._upsert(draft, stamp)
            result = await self._apply_for_mode(draft, alert, mode=watchdog.mode)
            stored = await self._store_action(alert.id, result)
            if stored is not None:
                alert = stored
            if inserted and _str(alert.severity) == AlertSeverity.CRITICAL.value:
                await self._notify_critical(alert)
            processed.append(alert)

        await self._resolve_conditions(deterministic, stamp)
        self._last_run_at = stamp
        return processed

    async def snapshot(self, now: datetime | None = None) -> WatchdogSnapshot:
        """只读数据库与记录器，组装本轮检查用的快照。"""
        stamp = now or self._clock()
        settings = self._settings_provider()
        thresholds = WatchdogThresholds(
            run_stale_seconds=int(settings.agent.run_stale_seconds),
            ticket_stale_minutes=int(settings.watchdog.ticket_stale_minutes),
            device_frozen_minutes=int(settings.watchdog.device_frozen_minutes),
        )
        async with self._session_factory() as session:
            runs = await self._run_samples(session)
            tickets = await self._ticket_samples(session, stamp)
            pipelines = await self._pipeline_samples(session)
            queue = await self._queue_sample(session, stamp)
        return WatchdogSnapshot(
            observed_at=stamp,
            thresholds=thresholds,
            runs=runs,
            tickets=tickets,
            pipelines=pipelines,
            core=self._core_sample(stamp),
            queue=queue,
        )

    # ------------------------------------------------------------------
    # 语义检查
    # ------------------------------------------------------------------
    def _semantic_ready(self) -> bool:
        """语义检查依赖是否齐备；缺依赖只记 debug，不影响确定性巡检。"""
        if self._model_router is None or self._cost_ledger is None:
            return False
        runner = self._run_service
        return callable(getattr(runner, "start", None)) and callable(
            getattr(runner, "finish", None)
        )

    @staticmethod
    def _semantic_hits(drafts: list[AlertDraft]) -> list[AlertDraft]:
        """本轮被 ``ticket_stale`` / ``ticket_churn`` 挑中的工单，按确定性顺序去重。"""
        hits: list[AlertDraft] = []
        seen: set[str] = set()
        for draft in drafts:
            if draft.check not in SEMANTIC_SOURCE_CHECKS or not draft.target_id:
                continue
            if draft.target_id in seen:
                continue
            seen.add(draft.target_id)
            hits.append(draft)
        return hits

    async def _semantic_checks(
        self, drafts: list[AlertDraft], now: datetime
    ) -> list[AlertDraft]:
        """对确定性检查挑中的工单跑语义检查；每轮至多 2 张、每张每小时至多 1 次。"""
        if not self._semantic_ready():
            logger.debug(
                "跳过值守语义检查：未注入 model_router、run_service 或 cost_ledger"
            )
            return []
        selected: list[tuple[str, AlertDraft]] = []
        for hit in self._semantic_hits(drafts):
            ticket_id = str(hit.target_id)
            last = self._semantic_last_at.get(ticket_id)
            if last is not None and now - last < timedelta(
                seconds=SEMANTIC_MIN_INTERVAL_SECONDS
            ):
                continue
            selected.append((ticket_id, hit))
            if len(selected) >= SEMANTIC_MAX_TICKETS_PER_ROUND:
                break

        findings: list[AlertDraft] = []
        for ticket_id, hit in selected:
            # 先记时刻：模型失败也占用这张工单本小时的额度，避免失败后每轮重试。
            self._semantic_last_at[ticket_id] = now
            findings.extend(await self._run_semantic_check(ticket_id, hit, now))
        return findings

    async def _run_semantic_check(
        self, ticket_id: str, hit: AlertDraft, now: datetime
    ) -> list[AlertDraft]:
        """对一张工单调一次模型；失败只跳过该工单，不写告警、不建诊断工单。"""
        try:
            spec = builtin_spec(WATCHDOG_AGENT)
        except Exception:  # noqa: BLE001 - 定义缺失属于装配问题，跳过语义检查
            logger.debug("跳过值守语义检查：读不到 watchdog 定义", exc_info=True)
            return []
        card = await self._semantic_card(ticket_id, hit, now)
        if card is None:
            logger.debug("跳过值守语义检查：工单 %s 已不存在", ticket_id)
            return []
        try:
            run = await self._run_service.start(
                agent_name=WATCHDOG_AGENT,
                kind=AgentRunKind.WATCHDOG_CHECK,
                session_id=None,
                ticket_id=ticket_id,
                spec_version=spec.spec_version,
            )
        except Exception:  # noqa: BLE001 - 建不了运行就跳过这张工单
            logger.warning(
                "值守语义检查建运行失败：ticket=%s", ticket_id, exc_info=True
            )
            return []

        messages = [
            {"role": "system", "content": spec.body},
            {"role": "user", "content": card},
        ]
        try:
            result = await complete_structured(
                router=self._model_router,
                ledger=self._cost_ledger,
                role=ModelRole.WORKER,
                schema_name=WATCHDOG_FINDINGS_SCHEMA,
                messages=messages,
                agent_name=WATCHDOG_AGENT,
                run_id=run.id,
                session_id=None,
            )
        except AppError as exc:
            await self._finish_semantic_run(
                run.id,
                AgentRunStatus.FAILED,
                error_code=str(exc.code),
                error_message=str(exc),
            )
            logger.warning(
                "值守语义检查失败，跳过工单 %s：%s", ticket_id, exc.code
            )
            return []
        except Exception as exc:  # noqa: BLE001 - 语义检查失败不能打断整轮巡检
            await self._finish_semantic_run(
                run.id,
                AgentRunStatus.FAILED,
                error_code="SEMANTIC_CHECK_FAILED",
                error_message=str(exc),
            )
            logger.warning(
                "值守语义检查异常，跳过工单 %s", ticket_id, exc_info=True
            )
            return []

        await self._finish_semantic_run(run.id, AgentRunStatus.SUCCEEDED)
        return self._semantic_drafts(result.value, ticket_id, hit, run.id)

    async def _finish_semantic_run(
        self,
        run_id: str,
        status: AgentRunStatus,
        *,
        error_code: str | None = None,
        error_message: str | None = None,
    ) -> None:
        """收尾语义检查的运行；收尾失败只告警，不影响已得到的结论。"""
        finish_reason = "stop" if status == AgentRunStatus.SUCCEEDED else "error"
        try:
            await self._run_service.finish(
                run_id,
                status=status,
                finish_reason=finish_reason,
                error_code=error_code,
                error_message=error_message,
            )
        except Exception:  # noqa: BLE001 - 运行收尾失败不向外抛
            logger.warning(
                "值守语义检查收尾运行失败：run=%s", run_id, exc_info=True
            )

    def _semantic_drafts(
        self,
        value: WatchdogFindings,
        ticket_id: str,
        hit: AlertDraft,
        run_id: str,
    ) -> list[AlertDraft]:
        """把模型结论折算成告警草稿；只认本轮这张工单，同一 check 去重。"""
        drafts: list[AlertDraft] = []
        seen: set[str] = set()
        for item in value.items:
            check = str(item.check)
            target = str(item.target_id).strip()
            if target != ticket_id:
                logger.debug(
                    "语义检查返回了别的工单 %r（本轮 %s），忽略", target, ticket_id
                )
                continue
            if check in seen:
                continue
            seen.add(check)
            suggested = (
                item.suggested_action
                if item.suggested_action == "mark_blocked"
                else None
            )
            evidence: dict[str, Any] = {
                "ticket_id": ticket_id,
                "run_id": run_id,
                "source": "semantic",
                "deterministic_check": hit.check,
                # 供 ``_apply_for_mode`` 判定「本轮也被确定性检查挑中」。
                "deterministic_match": True,
            }
            if suggested is not None:
                evidence["suggested_action"] = suggested
            drafts.append(
                AlertDraft(
                    check=check,
                    severity=AlertSeverity(str(item.severity)),
                    target_kind=AlertTargetKind.TICKET,
                    target_id=ticket_id,
                    message=item.message,
                    evidence=evidence,
                    suggested_action=suggested,
                )
            )
        return drafts

    async def _semantic_card(
        self, ticket_id: str, hit: AlertDraft, now: datetime
    ) -> str | None:
        """工单快照卡：标题、委托、状态、最近活动与最近汇报（spec §4.5）。"""
        async with self._session_factory() as session:
            ticket = await TicketRepository(session).get(ticket_id)
            if ticket is None:
                return None
            mandate = await MandateRepository(session).get_by_ticket(ticket_id)
            reports = await TicketReportRepository(session).recent(
                ticket_id, limit=MAX_CARD_REPORTS
            )
        activity = self._card_last_activity(ticket, reports, hit)
        lines = [
            f"【工单】{ticket.id}「{ticket.title}」",
            f"状态：{_str(ticket.status)}　类型：{ticket.kind}　来源：{ticket.origin}",
            f"最近活动：{_local_time(activity)}（巡检时刻 {_local_time(now)}）",
        ]
        if mandate is not None:
            scope = (mandate.scope or "").strip()[:MAX_CARD_MANDATE_CHARS]
            lines.append(f"【委托】{scope or '（无）'}")
            lines.append(
                f"委托强度：{mandate.strength}　风险上限：{mandate.risk_ceiling}"
            )
        else:
            lines.append("【委托】（无）")
        lines.append(f"【本轮确定性检查】{hit.check}：{hit.message}")
        lines.append(f"【最近汇报】（最新在前，至多 {MAX_CARD_REPORTS} 条）")
        if reports:
            for report in reports:
                summary = (report.summary or "")[:MAX_CARD_REPORT_CHARS]
                lines.append(
                    f"- {_local_time(report.created_at)} "
                    f"{_str(report.status)}：{summary}"
                )
        else:
            lines.append("（无）")
        return "\n".join(lines)

    @staticmethod
    def _card_last_activity(
        ticket: Ticket, reports: list[TicketReport], hit: AlertDraft
    ) -> datetime | None:
        """卡片的「最近活动」：工单更新时间、确定性命中的活动时间与最新汇报取最大。"""
        candidates: list[datetime] = [ticket.updated_at]
        last_activity = hit.evidence.get("last_activity_at")
        if isinstance(last_activity, str):
            try:
                candidates.append(datetime.fromisoformat(last_activity))
            except ValueError:
                pass
        if reports:
            candidates.append(reports[0].created_at)
        return max((item for item in candidates if item is not None), default=None)

    # ------------------------------------------------------------------
    # 快照：运行 / 工单 / 流水线 / 内核 / 队列
    # ------------------------------------------------------------------
    async def _run_samples(self, session: AsyncSession) -> list[RunSample]:
        """只取本进程的 ``running`` 运行；别的进程留下的交给 M66 的启动恢复。"""
        boot_id = getattr(self._run_service, "boot_id", None)
        statement = select(AgentRun).where(
            AgentRun.status == AgentRunStatus.RUNNING
        )
        if boot_id:
            statement = statement.where(AgentRun.boot_id == str(boot_id))
        rows = (await session.execute(statement)).scalars().all()
        return [
            RunSample(
                id=row.id,
                status=row.status,
                heartbeat_at=row.heartbeat_at,
                started_at=row.started_at,
            )
            for row in rows
        ]

    async def _ticket_samples(
        self, session: AsyncSession, now: datetime
    ) -> list[TicketSample]:
        """``ticket_stale`` / ``ticket_churn`` 的判定事实；批量查询避免 N+1。"""
        tickets = list(
            (
                await session.execute(
                    select(Ticket).where(Ticket.status == TicketStatus.OPEN)
                )
            )
            .scalars()
            .all()
        )
        if not tickets:
            return []
        ticket_ids = [ticket.id for ticket in tickets]
        window_start = now - timedelta(minutes=TICKET_CHURN_WINDOW_MINUTES)

        active_counts = dict(
            (
                await session.execute(
                    select(AgentRun.ticket_id, func.count())
                    .where(
                        AgentRun.ticket_id.in_(ticket_ids),
                        AgentRun.status.in_(ACTIVE_RUN_STATUSES),
                    )
                    .group_by(AgentRun.ticket_id)
                )
            ).all()
        )

        worker_session_ids = [
            ticket.worker_session_id
            for ticket in tickets
            if ticket.worker_session_id
        ]
        pending_counts: dict[str, int] = {}
        if worker_session_ids:
            pending_counts = dict(
                (
                    await session.execute(
                        select(AgentInbox.session_id, func.count())
                        .where(
                            AgentInbox.session_id.in_(worker_session_ids),
                            AgentInbox.status == InboxStatus.PENDING,
                        )
                        .group_by(AgentInbox.session_id)
                    )
                ).all()
            )

        recent_runs = (
            (
                await session.execute(
                    select(AgentRun).where(
                        AgentRun.ticket_id.in_(ticket_ids),
                        AgentRun.kind == AgentRunKind.WORKER_TURN,
                        AgentRun.started_at >= window_start,
                    )
                )
            )
            .scalars()
            .all()
        )
        runs_by_ticket: dict[str, list[AgentRun]] = {}
        for run in recent_runs:
            if run.ticket_id:
                runs_by_ticket.setdefault(run.ticket_id, []).append(run)

        recent_reports = (
            (
                await session.execute(
                    select(TicketReport).where(
                        TicketReport.ticket_id.in_(ticket_ids),
                        TicketReport.created_at >= window_start,
                    )
                )
            )
            .scalars()
            .all()
        )
        reports_by_ticket: dict[str, list[TicketReport]] = {}
        for report in recent_reports:
            reports_by_ticket.setdefault(report.ticket_id, []).append(report)

        latest_reports = dict(
            (
                await session.execute(
                    select(
                        TicketReport.ticket_id, func.max(TicketReport.created_at)
                    )
                    .where(TicketReport.ticket_id.in_(ticket_ids))
                    .group_by(TicketReport.ticket_id)
                )
            ).all()
        )
        latest_runs = dict(
            (
                await session.execute(
                    select(AgentRun.ticket_id, func.max(AgentRun.started_at))
                    .where(AgentRun.ticket_id.in_(ticket_ids))
                    .group_by(AgentRun.ticket_id)
                )
            ).all()
        )

        subscriptions = (
            (
                await session.execute(
                    select(ThreadSubscription).where(
                        ThreadSubscription.ticket_id.in_(ticket_ids),
                        ThreadSubscription.active.is_(True),
                    )
                )
            )
            .scalars()
            .all()
        )
        pipeline_ids = {
            subscription.thread_key.split(":", 1)[1]
            for subscription in subscriptions
            if subscription.thread_key.startswith("pipeline:")
            and ":" in subscription.thread_key
        }
        pipelines: dict[str, Pipeline] = {}
        if pipeline_ids:
            rows = (
                (
                    await session.execute(
                        select(Pipeline).where(Pipeline.id.in_(pipeline_ids))
                    )
                )
                .scalars()
                .all()
            )
            pipelines = {row.id: row for row in rows}
        busy_tickets: set[str] = set()
        pipeline_activity: dict[str, datetime] = {}
        for subscription in subscriptions:
            if not subscription.thread_key.startswith("pipeline:"):
                continue
            pipeline = pipelines.get(subscription.thread_key.split(":", 1)[1])
            if pipeline is None:
                continue
            status = _str(pipeline.status)
            if status in (
                PipelineStatus.PENDING.value,
                PipelineStatus.RUNNING.value,
            ):
                busy_tickets.add(subscription.ticket_id)
            activity = (
                pipeline.finished_at
                or pipeline.started_at
                or pipeline.created_at
            )
            if activity is not None:
                pipeline_activity[subscription.ticket_id] = max(
                    pipeline_activity.get(subscription.ticket_id, activity),
                    activity,
                )

        samples: list[TicketSample] = []
        for ticket in tickets:
            activity = [
                ticket.updated_at,
                latest_reports.get(ticket.id),
                latest_runs.get(ticket.id),
                pipeline_activity.get(ticket.id),
            ]
            last_activity = max(
                (item for item in activity if item is not None),
                default=ticket.created_at,
            )
            samples.append(
                TicketSample(
                    id=ticket.id,
                    status=ticket.status,
                    last_activity_at=last_activity,
                    has_active_worker_run=bool(
                        active_counts.get(ticket.id, 0)
                    ),
                    has_pending_inbox=bool(
                        pending_counts.get(ticket.worker_session_id or "", 0)
                    ),
                    has_busy_pipeline=ticket.id in busy_tickets,
                    worker_runs=tuple(
                        WorkerRunSample(started_at=run.started_at)
                        for run in runs_by_ticket.get(ticket.id, ())
                        if run.started_at is not None
                    ),
                    reports=tuple(
                        ReportSample(
                            status=_str(report.status),
                            created_at=report.created_at,
                        )
                        for report in reports_by_ticket.get(ticket.id, ())
                    ),
                )
            )
        return samples

    async def _pipeline_samples(
        self, session: AsyncSession
    ) -> list[PipelineSample]:
        """正在执行的流水线 + 记录器里最后一个回调（没有记录就按未采样处理）。"""
        running = await PipelineRepository(session).list_running(self._core_id)
        recorder_snapshot = self._recorder.snapshot()
        samples: list[PipelineSample] = []
        for pipeline in running:
            sample = next(
                (
                    item
                    for item in recorder_snapshot.pipelines
                    if item.pipeline_id == pipeline.id
                ),
                None,
            )
            if sample is None:
                started_at = pipeline.started_at or pipeline.created_at
                sample = PipelineSample(
                    pipeline_id=pipeline.id,
                    started_at=started_at,
                    device_id=DEFAULT_DEVICE_ID,
                    last_callback_at=recorder_snapshot.last_core_callback_at,
                )
            elif sample.device_id is None:
                sample = PipelineSample(
                    pipeline_id=sample.pipeline_id,
                    started_at=sample.started_at,
                    device_id=DEFAULT_DEVICE_ID,
                    last_callback_at=sample.last_callback_at,
                )
            samples.append(sample)
        return samples

    def _core_sample(self, now: datetime) -> CoreSample | None:
        """内核状态与进入该状态的时刻；持续时长由服务进程内记录。"""
        supervisor = self._core_supervisor
        if supervisor is None:
            return None
        status = _str(getattr(supervisor, "state", "stopped"))
        if status != self._core_status:
            self._core_status = status
            self._core_state_since = now
        return CoreSample(status=status, state_since=self._core_state_since)

    async def _queue_sample(
        self, session: AsyncSession, now: datetime
    ) -> QueueSample | None:
        """队列、内核、设备、租约与执行器的可领取状态。"""
        if self._queue_service is None and self._pipeline_runner is None:
            return None
        pending = await PipelineRepository(session).list_pending(self._core_id)
        oldest = min(
            (pipeline.created_at for pipeline in pending), default=None
        )
        lease = await session.get(DeviceLease, DEFAULT_DEVICE_ID)
        has_lease = bool(
            lease is not None
            and (lease.expires_at is None or lease.expires_at > now)
        )
        runner = self._pipeline_runner
        executor_alive = bool(getattr(runner, "running", False)) if runner else False
        executor_idle = (
            getattr(runner, "active_pipeline_id", None) is None
            if runner is not None
            else False
        )
        return QueueSample(
            claimable_count=len(pending),
            oldest_claimable_at=oldest,
            paused=bool(getattr(self._queue_service, "paused", False)),
            core_ready=self._core_ready(),
            device_connected=self._device_connected(),
            has_lease=has_lease,
            executor_idle=executor_idle,
            executor_alive=executor_alive,
        )

    def _core_ready(self) -> bool:
        supervisor = self._core_supervisor
        return supervisor is not None and _str(
            getattr(supervisor, "state", "stopped")
        ) == CORE_READY

    def _device_connected(self) -> bool:
        manager = self._device_manager
        return manager is not None and _str(
            getattr(manager, "state", "disconnected")
        ) == DEVICE_CONNECTED

    # ------------------------------------------------------------------
    # 动作
    # ------------------------------------------------------------------
    async def _apply_for_mode(
        self, draft: AlertDraft, alert: WatchdogAlert, *, mode: str
    ) -> ActionResult:
        """record 档只记；auto 档才把草稿映射成动作。

        语义草稿的动作来自模型的 ``suggested_action``；``mark_blocked`` 仅在
        ``mode=auto`` 且这张工单本轮也被确定性检查挑中时执行（spec §4.5）。
        """
        action = ACTION_BY_CHECK.get(draft.check)
        if action is None:
            action = draft.suggested_action
        if mode != "auto" or action is None:
            return ActionResult(action, AlertActionStatus.RECORDED)
        if draft.suggested_action is not None and not draft.evidence.get(
            "deterministic_match"
        ):
            return ActionResult(
                action,
                AlertActionStatus.RECORDED,
                {"reason": "ticket_not_flagged_deterministically"},
            )
        return await self.apply_action(draft, alert=alert, action=action)

    async def apply_action(
        self,
        draft: AlertDraft,
        *,
        alert: WatchdogAlert | None = None,
        action: str | None = None,
    ) -> ActionResult:
        """执行一条草稿对应的动作；异常收敛成 ``failed``，绝不向外抛。

        ``action`` 为空时按确定性检查映射；语义草稿显式传入模型建议的动作。
        """
        action = action or ACTION_BY_CHECK.get(draft.check)
        if action is None:
            return ActionResult(None, AlertActionStatus.RECORDED)
        try:
            return await self._dispatch_action(action, draft, alert)
        except Exception as exc:  # noqa: BLE001 - 动作失败只落库，不打断本轮
            logger.warning(
                "值守动作 %s 失败（check=%s，target=%s）：%s",
                action,
                draft.check,
                draft.target_id,
                exc,
                exc_info=True,
            )
            return ActionResult(
                action,
                AlertActionStatus.FAILED,
                error=_error_payload(exc),
            )

    async def _dispatch_action(
        self,
        action: str,
        draft: AlertDraft,
        alert: WatchdogAlert | None,
    ) -> ActionResult:
        if action == "interrupt_run":
            return await self._interrupt_run(draft)
        if action == "mark_blocked":
            return await self._mark_blocked(draft, alert)
        if action == "stop_pipeline":
            return await self._stop_pipeline(draft)
        if action == "restart_core":
            return await self._restart_core()
        if action == "wake_queue":
            return await self._wake_queue(draft)
        return ActionResult(action, AlertActionStatus.SKIPPED, {"reason": "unknown"})

    async def _interrupt_run(self, draft: AlertDraft) -> ActionResult:
        """走 M70 的中断入口；30 秒后仍 running 就经运行仓储写 interrupted。"""
        action = "interrupt_run"
        run_id = draft.target_id or str(draft.evidence.get("run_id") or "")
        if not run_id:
            return ActionResult(action, AlertActionStatus.SKIPPED, {"reason": "run_id_missing"})
        async with self._session_factory() as session:
            run = await AgentRunRepository(session).get(run_id)
        if run is None:
            return ActionResult(action, AlertActionStatus.SKIPPED, {"reason": "run_not_found"})
        if _str(run.status) != AgentRunStatus.RUNNING.value:
            return ActionResult(action, AlertActionStatus.SKIPPED, {"reason": "not_running"})
        entry = await self._interrupt_target(run)
        if entry is None:
            return ActionResult(
                action, AlertActionStatus.SKIPPED, {"reason": "no_interrupt_entry"}
            )
        await self._sleep(self._interrupt_grace_seconds)
        async with self._session_factory() as session:
            repository = AgentRunRepository(session)
            fresh = await repository.get(run_id)
            if fresh is not None and _str(fresh.status) == AgentRunStatus.RUNNING.value:
                await repository.set_status_if_active(
                    run_id, AgentRunStatus.INTERRUPTED.value
                )
                await session.commit()
        return ActionResult(action, AlertActionStatus.DONE, entry)

    async def _interrupt_target(self, run: AgentRun) -> dict[str, Any] | None:
        """从 ``agent_run.session_id`` 反查会话类型与工单，返回命中的中断入口。"""
        session_row: AgentSession | None = None
        if run.session_id:
            async with self._session_factory() as session:
                session_row = await AgentSessionRepository(session).get(
                    run.session_id
                )
        kind = _str(session_row.kind) if session_row is not None else None
        ticket_id = (
            session_row.ticket_id if session_row is not None else None
        ) or run.ticket_id
        if kind == AgentSessionKind.ORCHESTRATOR.value:
            service = self._orchestrator_service
            if service is None:
                return None
            await service.interrupt()
            return {"entry": "orchestrator", "session_id": run.session_id}
        if (kind == AgentSessionKind.WORKER.value or kind is None) and ticket_id:
            service = self._worker_service
            if service is None:
                return None
            await service.interrupt(ticket_id)
            return {"entry": "worker", "ticket_id": ticket_id}
        return None

    async def _mark_blocked(
        self, draft: AlertDraft, alert: WatchdogAlert | None
    ) -> ActionResult:
        """写一条 blocked 汇报（带 decision），由 M69 照常转给总控。"""
        action = "mark_blocked"
        ticket_id = draft.target_id or str(draft.evidence.get("ticket_id") or "")
        if not ticket_id:
            return ActionResult(action, AlertActionStatus.SKIPPED, {"reason": "ticket_id_missing"})
        async with self._session_factory() as session:
            ticket = await TicketRepository(session).get(ticket_id)
        if ticket is None:
            return ActionResult(action, AlertActionStatus.SKIPPED, {"reason": "ticket_not_found"})
        if TicketStatus(ticket.status).is_terminal:
            return ActionResult(action, AlertActionStatus.SKIPPED, {"reason": "ticket_ended"})
        service = self._ticket_service
        if service is None:
            return ActionResult(action, AlertActionStatus.SKIPPED, {"reason": "ticket_service_unavailable"})
        summary = f"值守巡检发现工单停滞：{draft.message}"[:MAX_SUMMARY_CHARS]
        report = ReportInput(
            status=ReportStatus.BLOCKED,
            summary=summary,
            human_action=HumanAction(
                type="decision",
                prompt="要继续推进还是取消这张工单？",
                ref_id=(
                    f"watchdog:{alert.id}"
                    if alert is not None and alert.id is not None
                    else None
                ),
            ),
        )
        await service.record_report(ticket_id, report)
        return ActionResult(
            action,
            AlertActionStatus.DONE,
            {"ticket_id": ticket_id, "alert_id": alert.id if alert else None},
        )

    async def _stop_pipeline(self, draft: AlertDraft) -> ActionResult:
        """只取消正在执行的那条流水线（spec §6）。"""
        action = "stop_pipeline"
        pipeline_id = str(draft.evidence.get("pipeline_id") or "")
        async with self._session_factory() as session:
            repository = PipelineRepository(session)
            if pipeline_id:
                pipeline = await repository.get(pipeline_id)
                if pipeline is None or _str(pipeline.status) != PipelineStatus.RUNNING.value:
                    return ActionResult(
                        action,
                        AlertActionStatus.SKIPPED,
                        {"reason": "pipeline_not_running", "pipeline_id": pipeline_id},
                    )
            else:
                running = await repository.list_running(self._core_id)
                if not running:
                    return ActionResult(
                        action, AlertActionStatus.SKIPPED, {"reason": "pipeline_not_running"}
                    )
                pipeline_id = running[0].id
        runner = self._pipeline_runner
        if runner is None:
            return ActionResult(action, AlertActionStatus.SKIPPED, {"reason": "runner_unavailable"})
        await runner.request_cancel(pipeline_id)
        return ActionResult(
            action, AlertActionStatus.DONE, {"pipeline_id": pipeline_id}
        )

    async def _restart_core(self) -> ActionResult:
        """仅当 ``allow_core_restart`` 为真且没有流水线在执行时才重启内核。"""
        action = "restart_core"
        if not self._settings_provider().watchdog.allow_core_restart:
            return ActionResult(
                action,
                AlertActionStatus.SKIPPED,
                {"reason": "allow_core_restart_disabled"},
            )
        async with self._session_factory() as session:
            running = await PipelineRepository(session).list_running(self._core_id)
        if running:
            return ActionResult(
                action,
                AlertActionStatus.SKIPPED,
                {"reason": "pipeline_running", "pipeline_id": running[0].id},
            )
        if self._restart_core_callback is None:
            return ActionResult(
                action, AlertActionStatus.SKIPPED, {"reason": "restart_unavailable"}
            )
        await self._restart_core_callback()
        return ActionResult(action, AlertActionStatus.DONE)

    async def _wake_queue(self, draft: AlertDraft) -> ActionResult:
        """唤醒执行器；执行器任务已退出时只记告警，不取消队列头。"""
        action = "wake_queue"
        if not draft.evidence.get("executor_alive", True):
            return ActionResult(
                action,
                AlertActionStatus.SKIPPED,
                {
                    "reason": "executor_exited",
                    "hint": "执行器任务已退出，请重启服务",
                },
            )
        runner = self._pipeline_runner
        if runner is None:
            return ActionResult(
                action, AlertActionStatus.SKIPPED, {"reason": "runner_unavailable"}
            )
        runner.wake()
        return ActionResult(action, AlertActionStatus.DONE)

    # ------------------------------------------------------------------
    # 落库与通知
    # ------------------------------------------------------------------
    async def _upsert(
        self, draft: AlertDraft, now: datetime
    ) -> tuple[WatchdogAlert, bool]:
        async with self._session_factory() as session:
            return await upsert_open_alert_async(session, draft, now=now)

    async def _store_action(
        self, alert_id: int | None, result: ActionResult
    ) -> WatchdogAlert | None:
        """把动作名、状态、跳过原因与失败原因写回告警行。"""
        if alert_id is None:
            return None
        async with self._session_factory() as session:
            alert = await session.get(WatchdogAlert, alert_id)
            if alert is None:
                return None
            alert.action = result.action
            alert.action_status = _str(result.status)
            evidence = dict(alert.evidence or {})
            if result.detail:
                evidence["action"] = dict(result.detail)
            else:
                evidence.pop("action", None)
            if result.error is not None:
                evidence["error"] = dict(result.error)
            else:
                evidence.pop("error", None)
            alert.evidence = evidence
            session.add(alert)
            await session.commit()
            await session.refresh(alert)
            return alert

    async def _resolve_conditions(
        self, drafts: list[AlertDraft], now: datetime
    ) -> None:
        """六项确定性检查每轮都跑；本轮没命中的未解除告警写 ``resolved_at``。"""
        checked = tuple(check.__name__ for check in CHECK_FUNCTIONS)
        active = {draft_key(draft) for draft in drafts}
        async with self._session_factory() as session:
            await resolve_condition_gone_async(
                session,
                checked_checks=checked,
                active_keys=active,
                now=now,
            )

    async def _notify_critical(self, alert: WatchdogAlert) -> None:
        """critical 新插入时的三条对外通知（待你处理由前端按 severity 取）。"""
        await self._broadcast_alert(alert)
        await self._write_orchestrator(alert)
        await self._push_alert(alert)

    async def _broadcast_alert(self, alert: WatchdogAlert) -> None:
        if self._broadcast is None:
            return
        envelope = {
            "session_id": None,
            "event": "watchdog_alert",
            "payload": {
                "alert_id": alert.id,
                "check": alert.check,
                "severity": _str(alert.severity),
                "target_kind": _str(alert.target_kind),
                "target_id": alert.target_id,
                "message": alert.message,
                "action": alert.action,
                "action_status": alert.action_status,
                "created_at": alert.created_at.isoformat(),
            },
        }
        try:
            result = self._broadcast("agent_event", envelope)
            if inspect.isawaitable(result):
                await result
        except Exception:  # noqa: BLE001 - 告警已落库，广播失败不能回滚
            logger.warning("值守告警广播失败：alert=%s", alert.id, exc_info=True)

    async def _write_orchestrator(self, alert: WatchdogAlert) -> None:
        """写一条 ``system`` 项：正文是 message 加 alert id（spec §4.5）。"""
        router = self._thread_router
        if router is None:
            return
        text = f"{alert.message}（值守告警 #{alert.id}）"
        payload = {
            "event": "watchdog.alert",
            "text": text,
            "alert_id": alert.id,
            "check": alert.check,
            "severity": _str(alert.severity),
            "target_kind": _str(alert.target_kind),
            "target_id": alert.target_id,
            "passive": False,
            "origin": "service",
        }
        try:
            await router.to_orchestrator(
                kind=InboxKind.SYSTEM,
                thread_key=f"watchdog:{alert.check}",
                ref_id=f"watchdog:alert:{alert.id}",
                payload=payload,
            )
        except Exception:  # noqa: BLE001 - 收件箱写入失败不能还原告警
            logger.warning(
                "值守告警写总控收件箱失败：alert=%s", alert.id, exc_info=True
            )

    async def _push_alert(self, alert: WatchdogAlert) -> None:
        """没有可见页面时才推送；warn / info 不走这里。"""
        webpush = self._webpush_service
        visible = self._has_visible_client
        if webpush is None or visible is None or visible():
            return
        payload = {
            "title": f"值守告警：{alert.message}"[:40],
            "body": f"{alert.check}：{alert.message}"[:120],
            "tag": f"watchdog:{alert.id}",
            "data": {"url": "/more/watchdog", "alertId": alert.id},
        }
        try:
            await webpush.broadcast(payload)
        except Exception:  # noqa: BLE001 - 推送失败不能影响已落库的告警
            logger.warning("值守告警推送失败：alert=%s", alert.id, exc_info=True)

    # ------------------------------------------------------------------
    # 生命周期
    # ------------------------------------------------------------------
    async def close(self) -> None:
        """停止后续巡检；当前一轮由调度器收尾。"""
        self._closed = True
