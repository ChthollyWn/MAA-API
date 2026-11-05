"""08 §6.1 的六项确定性检查（v3 specs/m83 §4.5）。

设计约束（spec §5 T1）：

- 全部是纯函数：输入 :class:`WatchdogSnapshot`，只读快照，不读数据库、不碰设备、
  不调用模型、不读取全局配置；阈值从快照带来的
  :class:`WatchdogThresholds` 取，08 写死的窗口与轮数是模块常量。
- 函数名就是 ``watchdog_alert.check`` 的字符串，与 08 §6.1 逐字一致；
  :func:`deterministic_checks` 按 08 的顺序调用六项并拼接草稿。
- 每项返回 ``list[AlertDraft]``；条件不成立、样本不足或无法判定时返回空列表。
  ``pipeline_no_callback`` 的「当前任务开始后还没有任何 CALLBACK」用
  :attr:`PipelineSample.no_sample_yet` 表达：不告警，证据 helper
  :func:`pipeline_no_callback_evidence` 会给出 ``reason=no_sample_yet``。

快照由服务层（G2 的 ``WatchdogService.snapshot``）从数据库与
:class:`~maa_api.agent.watchdog.recorder.WatchdogRecorder` 组装；测试直接构造。
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timedelta
from typing import Any, Sequence

from maa_api.domain.enums import (
    AgentRunStatus,
    AlertSeverity,
    AlertTargetKind,
    ReportStatus,
    TicketStatus,
)

#: 08 §6.1 写死的窗口与轮数；不做成配置（spec §4.4）。
TICKET_CHURN_WINDOW_MINUTES = 60
TICKET_CHURN_MIN_RUNS = 12
CORE_NOT_READY_MINUTES = 10
QUEUE_STUCK_MINUTES = 10

#: ``pipeline_no_callback`` 在样本不足时的证据原因（spec §4.2）。
NO_SAMPLE_YET = "no_sample_yet"

#: 内核未就绪的持续状态；``failed`` 单独即时告警（08 §6.1）。
CORE_UNREADY_STATUSES = frozenset({"starting", "restarting", "crashed"})


def _iso(value: datetime | None) -> str | None:
    """证据里的时间统一用 ISO 8601；``None`` 原样保留。"""
    return value.isoformat() if value is not None else None


def _str(value: Any) -> str:
    """枚举与字符串统一成 ``check`` / ``severity`` 列里的短字符串。"""
    return value.value if hasattr(value, "value") else str(value)


@dataclass(frozen=True)
class WatchdogThresholds:
    """检查用到的可配置阈值；默认值与 03/04 一致（spec §4.4）。"""

    run_stale_seconds: int = 90
    ticket_stale_minutes: int = 30
    device_frozen_minutes: int = 10


@dataclass(frozen=True)
class RunSample:
    """``agent_run`` 的心跳快照；``heartbeat_at`` 不是 ``updated_at``。"""

    id: str
    status: str
    heartbeat_at: datetime | None = None
    started_at: datetime | None = None


@dataclass(frozen=True)
class WorkerRunSample:
    """这张工单的一次 Worker 运行起始时刻（``ticket_churn`` 数窗口内的轮次）。"""

    started_at: datetime


@dataclass(frozen=True)
class ReportSample:
    """这张工单的一条汇报（``ticket_churn`` 判断有没有 progress 以外的汇报）。"""

    status: str
    created_at: datetime


@dataclass(frozen=True)
class TicketSample:
    """``ticket_stale`` / ``ticket_churn`` 的判定事实。"""

    id: str
    status: str
    last_activity_at: datetime | None = None
    has_active_worker_run: bool = False
    has_pending_inbox: bool = False
    has_busy_pipeline: bool = False
    #: ``ticket_churn`` 的原始样本：检查自己按 60 分钟窗口过滤。
    worker_runs: tuple[WorkerRunSample, ...] = ()
    reports: tuple[ReportSample, ...] = ()


@dataclass(frozen=True)
class PipelineSample:
    """正在执行的流水线与它在本次任务开始后的最后一个 CALLBACK。"""

    pipeline_id: str
    started_at: datetime
    device_id: str | None = None
    #: 本次任务开始后收到的最后一个回调（内核 CALLBACK 与流水线进度取较晚者）；
    #: ``None`` 或早于 ``started_at`` 表示还没采样。
    last_callback_at: datetime | None = None

    @property
    def no_sample_yet(self) -> bool:
        """当前任务开始后还没有任何 CALLBACK：不能据此判冻结（spec §4.2）。"""
        return (
            self.last_callback_at is None
            or self.last_callback_at < self.started_at
        )


@dataclass(frozen=True)
class CoreSample:
    """内核状态与进入该状态的时刻（``CoreSupervisor.state``）。"""

    status: str
    state_since: datetime | None = None


@dataclass(frozen=True)
class QueueSample:
    """``queue_stuck`` 判定事实；布尔项由服务层从各组件查得。"""

    claimable_count: int = 0
    oldest_claimable_at: datetime | None = None
    paused: bool = False
    core_ready: bool = True
    device_connected: bool = True
    has_lease: bool = False
    executor_idle: bool = True
    #: 执行器任务是否还活着；只进证据，动作侧据此决定「唤醒」还是「提示重启」。
    executor_alive: bool = True


@dataclass(frozen=True)
class WatchdogSnapshot:
    """一轮检查的只读快照；服务层用 ``observed_at`` 作为本轮时间基准。"""

    observed_at: datetime
    thresholds: WatchdogThresholds = field(default_factory=WatchdogThresholds)
    runs: Sequence[RunSample] = ()
    tickets: Sequence[TicketSample] = ()
    pipelines: Sequence[PipelineSample] = ()
    core: CoreSample | None = None
    queue: QueueSample | None = None


@dataclass(frozen=True)
class AlertDraft:
    """尚未落库的一条告警草稿；字段与 ``watchdog_alert`` 的前六列对应。"""

    check: str
    severity: AlertSeverity
    target_kind: AlertTargetKind
    target_id: str | None
    message: str
    evidence: dict[str, Any] = field(default_factory=dict)
    #: 语义检查（G3）给出的动作建议；确定性草稿恒为 ``None``。模型只给建议，
    #: 是否执行由 ``WatchdogService`` 按 mode 与确定性命中判定。
    suggested_action: str | None = None


def run_heartbeat_stale(snapshot: WatchdogSnapshot) -> list[AlertDraft]:
    """本进程的 ``running`` 运行心跳早于 ``agent.run_stale_seconds``。"""
    cutoff = snapshot.observed_at - timedelta(
        seconds=snapshot.thresholds.run_stale_seconds
    )
    drafts: list[AlertDraft] = []
    for run in snapshot.runs:
        if _str(run.status) != AgentRunStatus.RUNNING:
            continue
        last_beat = run.heartbeat_at or run.started_at
        if last_beat is None or last_beat >= cutoff:
            continue
        drafts.append(
            AlertDraft(
                check="run_heartbeat_stale",
                severity=AlertSeverity.WARN,
                target_kind=AlertTargetKind.RUN,
                target_id=run.id,
                message=f"运行 {run.id} 的心跳已超过 "
                f"{snapshot.thresholds.run_stale_seconds} 秒未更新",
                evidence={
                    "run_id": run.id,
                    "status": _str(run.status),
                    "heartbeat_at": _iso(run.heartbeat_at),
                    "started_at": _iso(run.started_at),
                    "stale_seconds": int(
                        (snapshot.observed_at - last_beat).total_seconds()
                    ),
                    "threshold_seconds": snapshot.thresholds.run_stale_seconds,
                },
            )
        )
    return drafts


def ticket_stale(snapshot: WatchdogSnapshot) -> list[AlertDraft]:
    """工单 ``open``、无活动 Worker / 待办收件箱 / 忙碌流水线且长期无活动。"""
    cutoff = snapshot.observed_at - timedelta(
        minutes=snapshot.thresholds.ticket_stale_minutes
    )
    drafts: list[AlertDraft] = []
    for ticket in snapshot.tickets:
        if _str(ticket.status) != TicketStatus.OPEN:
            continue
        if (
            ticket.has_active_worker_run
            or ticket.has_pending_inbox
            or ticket.has_busy_pipeline
        ):
            continue
        if ticket.last_activity_at is None or ticket.last_activity_at >= cutoff:
            continue
        idle_minutes = int(
            (snapshot.observed_at - ticket.last_activity_at).total_seconds() // 60
        )
        drafts.append(
            AlertDraft(
                check="ticket_stale",
                severity=AlertSeverity.WARN,
                target_kind=AlertTargetKind.TICKET,
                target_id=ticket.id,
                message=f"工单 {ticket.id} 已 {idle_minutes} 分钟没有活动",
                evidence={
                    "ticket_id": ticket.id,
                    "last_activity_at": _iso(ticket.last_activity_at),
                    "idle_minutes": idle_minutes,
                    "threshold_minutes": snapshot.thresholds.ticket_stale_minutes,
                    "active_worker_runs": 0,
                    "pending_inbox": 0,
                    "busy_pipelines": 0,
                },
            )
        )
    return drafts


def ticket_churn(snapshot: WatchdogSnapshot) -> list[AlertDraft]:
    """最近 60 分钟同一工单起了至少 12 轮 Worker，且期间没有 progress 以外汇报。"""
    window_start = snapshot.observed_at - timedelta(
        minutes=TICKET_CHURN_WINDOW_MINUTES
    )
    drafts: list[AlertDraft] = []
    for ticket in snapshot.tickets:
        runs = [
            run for run in ticket.worker_runs if run.started_at >= window_start
        ]
        if len(runs) < TICKET_CHURN_MIN_RUNS:
            continue
        reports = [
            report for report in ticket.reports if report.created_at >= window_start
        ]
        if any(_str(report.status) != ReportStatus.PROGRESS for report in reports):
            continue
        drafts.append(
            AlertDraft(
                check="ticket_churn",
                severity=AlertSeverity.WARN,
                target_kind=AlertTargetKind.TICKET,
                target_id=ticket.id,
                message=f"工单 {ticket.id} 最近 {TICKET_CHURN_WINDOW_MINUTES} 分钟"
                f"起了 {len(runs)} 轮 Worker 运行",
                evidence={
                    "ticket_id": ticket.id,
                    "window_minutes": TICKET_CHURN_WINDOW_MINUTES,
                    "worker_runs": len(runs),
                    "min_runs": TICKET_CHURN_MIN_RUNS,
                    "report_statuses": sorted(
                        {_str(report.status) for report in reports}
                    ),
                },
            )
        )
    return drafts


def pipeline_no_callback_evidence(
    pipeline: PipelineSample,
    *,
    observed_at: datetime,
    threshold_minutes: int | None = None,
) -> dict[str, Any]:
    """``pipeline_no_callback`` 的判定证据；未采样时带 ``reason``（spec §4.2）。"""
    evidence: dict[str, Any] = {
        "pipeline_id": pipeline.pipeline_id,
        "device_id": pipeline.device_id,
        "started_at": _iso(pipeline.started_at),
        "last_callback_at": _iso(pipeline.last_callback_at),
        "threshold_minutes": threshold_minutes,
    }
    if pipeline.no_sample_yet:
        evidence["reason"] = NO_SAMPLE_YET
    elif pipeline.last_callback_at is not None:
        evidence["minutes_since_callback"] = int(
            (observed_at - pipeline.last_callback_at).total_seconds() // 60
        )
    return evidence


def pipeline_no_callback(snapshot: WatchdogSnapshot) -> list[AlertDraft]:
    """正在执行的流水线超过 ``watchdog.device_frozen_minutes`` 没有内核回调。

    当前任务开始后还没有任何 CALLBACK 时（``no_sample_yet``）不告警：那不是冻结，
    是还没采样（spec §4.2）。
    """
    threshold = snapshot.thresholds.device_frozen_minutes
    cutoff = snapshot.observed_at - timedelta(minutes=threshold)
    drafts: list[AlertDraft] = []
    for pipeline in snapshot.pipelines:
        if pipeline.no_sample_yet or pipeline.last_callback_at is None:
            continue
        if pipeline.last_callback_at >= cutoff:
            continue
        evidence = pipeline_no_callback_evidence(
            pipeline,
            observed_at=snapshot.observed_at,
            threshold_minutes=threshold,
        )
        drafts.append(
            AlertDraft(
                check="pipeline_no_callback",
                severity=AlertSeverity.CRITICAL,
                target_kind=AlertTargetKind.DEVICE,
                target_id=pipeline.device_id,
                message=f"流水线 {pipeline.pipeline_id} 的当前任务已超过 "
                f"{threshold} 分钟没有内核回调",
                evidence=evidence,
            )
        )
    return drafts


def core_not_ready(snapshot: WatchdogSnapshot) -> list[AlertDraft]:
    """内核 ``starting`` / ``restarting`` / ``crashed`` 持续 10 分钟，或 ``failed``。"""
    core = snapshot.core
    if core is None:
        return []
    status = _str(core.status)
    if status == "failed":
        return [
            AlertDraft(
                check="core_not_ready",
                severity=AlertSeverity.CRITICAL,
                target_kind=AlertTargetKind.CORE,
                target_id="default",
                message="内核处于 failed 状态",
                evidence={
                    "status": status,
                    "state_since": _iso(core.state_since),
                    "duration_seconds": None,
                    "threshold_minutes": CORE_NOT_READY_MINUTES,
                },
            )
        ]
    if status not in CORE_UNREADY_STATUSES or core.state_since is None:
        return []
    duration = snapshot.observed_at - core.state_since
    if duration < timedelta(minutes=CORE_NOT_READY_MINUTES):
        return []
    return [
        AlertDraft(
            check="core_not_ready",
            severity=AlertSeverity.CRITICAL,
            target_kind=AlertTargetKind.CORE,
            target_id="default",
            message=f"内核状态 {status} 已持续 "
            f"{int(duration.total_seconds() // 60)} 分钟",
            evidence={
                "status": status,
                "state_since": _iso(core.state_since),
                "duration_seconds": int(duration.total_seconds()),
                "threshold_minutes": CORE_NOT_READY_MINUTES,
            },
        )
    ]


def queue_stuck(snapshot: WatchdogSnapshot) -> list[AlertDraft]:
    """可领取的排队流水线超过 10 分钟，且队列、内核、设备、租约、执行器都正常。"""
    queue = snapshot.queue
    if queue is None or queue.claimable_count <= 0:
        return []
    if queue.oldest_claimable_at is None:
        return []
    if (
        queue.paused
        or not queue.core_ready
        or not queue.device_connected
        or queue.has_lease
        or not queue.executor_idle
    ):
        return []
    waited = snapshot.observed_at - queue.oldest_claimable_at
    if waited < timedelta(minutes=QUEUE_STUCK_MINUTES):
        return []
    return [
        AlertDraft(
            check="queue_stuck",
            severity=AlertSeverity.CRITICAL,
            target_kind=AlertTargetKind.QUEUE,
            target_id="default",
            message=f"{queue.claimable_count} 条排队流水线已滞留超过 "
            f"{QUEUE_STUCK_MINUTES} 分钟",
            evidence={
                "claimable_count": queue.claimable_count,
                "oldest_claimable_at": _iso(queue.oldest_claimable_at),
                "waited_minutes": int(waited.total_seconds() // 60),
                "paused": queue.paused,
                "core_ready": queue.core_ready,
                "device_connected": queue.device_connected,
                "has_lease": queue.has_lease,
                "executor_idle": queue.executor_idle,
                "executor_alive": queue.executor_alive,
            },
        )
    ]


#: 08 §6.1 的顺序；``deterministic_checks`` 按它拼接，测试用函数名核对 check。
CHECK_FUNCTIONS = (
    run_heartbeat_stale,
    ticket_stale,
    ticket_churn,
    pipeline_no_callback,
    core_not_ready,
    queue_stuck,
)


def deterministic_checks(snapshot: WatchdogSnapshot) -> list[AlertDraft]:
    """跑完 08 §6.1 的六项确定性检查，按表内顺序返回告警草稿。"""
    drafts: list[AlertDraft] = []
    for check in CHECK_FUNCTIONS:
        drafts.extend(check(snapshot))
    return drafts
