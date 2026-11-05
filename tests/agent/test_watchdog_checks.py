"""M83 T1：记录器、六项确定性检查与告警去重（spec §4.1/§4.2/§4.5/§6）。

测试纪律：

- 检查是纯函数，快照全部由测试构造；不建库、不碰设备、不调模型。
- 阈值来自 ``WatchdogThresholds``；08 §6.1 写死的 60/10 分钟与 12 轮用时间戳
  真正跨过窗口来验证，而不是只改数字。
- 去重与「条件消失解除」用临时 SQLite 库 + ``SQLModel.metadata.create_all``
  （测试路径，生产建表走 Alembic）。
"""

from __future__ import annotations

from dataclasses import replace
from datetime import datetime, timedelta

import pytest
from sqlmodel import Session, SQLModel, create_engine, select

from maa_api.agent.watchdog import checks
from maa_api.agent.watchdog.checks import (
    AlertDraft,
    CoreSample,
    PipelineSample,
    QueueSample,
    ReportSample,
    RunSample,
    TicketSample,
    WatchdogSnapshot,
    WorkerRunSample,
)
from maa_api.agent.watchdog.recorder import WatchdogRecorder
from maa_api.agent.watchdog.store import (
    condition_key,
    resolve_condition_gone,
    upsert_open_alert,
)
from maa_api.db.models import WatchdogAlert
from maa_api.domain.enums import (
    AgentRunStatus,
    AlertSeverity,
    AlertTargetKind,
    ReportStatus,
    TicketStatus,
)

NOW = datetime(2026, 3, 1, 12, 0, 0)


def _snapshot(**kwargs) -> WatchdogSnapshot:
    kwargs.setdefault("observed_at", NOW)
    return WatchdogSnapshot(**kwargs)


# ---------------------------------------------------------------------------
# run_heartbeat_stale
# ---------------------------------------------------------------------------
def test_run_heartbeat_stale():
    """用 ``heartbeat_at``，不用 ``updated_at`` / ``started_at`` 代替。"""
    snapshot = _snapshot(
        runs=[
            # 开始时间很新、心跳很旧：必须告警（若误用 started_at 就漏报）。
            RunSample(
                id="run-stale",
                status=AgentRunStatus.RUNNING,
                heartbeat_at=NOW - timedelta(seconds=120),
                started_at=NOW - timedelta(seconds=30),
            ),
            # 心跳很新、开始时间很旧：不告警。
            RunSample(
                id="run-live",
                status=AgentRunStatus.RUNNING,
                heartbeat_at=NOW - timedelta(seconds=5),
                started_at=NOW - timedelta(hours=2),
            ),
            # 非 running 的终态历史不碰。
            RunSample(
                id="run-failed",
                status=AgentRunStatus.FAILED,
                heartbeat_at=NOW - timedelta(hours=1),
            ),
        ]
    )
    drafts = checks.run_heartbeat_stale(snapshot)
    assert [draft.target_id for draft in drafts] == ["run-stale"]
    draft = drafts[0]
    assert draft.check == "run_heartbeat_stale"
    assert draft.severity is AlertSeverity.WARN
    assert draft.target_kind is AlertTargetKind.RUN
    assert draft.evidence["run_id"] == "run-stale"
    assert draft.evidence["heartbeat_at"] == (NOW - timedelta(seconds=120)).isoformat()
    assert draft.evidence["threshold_seconds"] == 90


def test_run_heartbeat_stale_respects_threshold():
    snapshot = _snapshot(
        runs=[
            RunSample(
                id="run-just-in-time",
                status=AgentRunStatus.RUNNING,
                heartbeat_at=NOW - timedelta(seconds=89),
            )
        ]
    )
    assert checks.run_heartbeat_stale(snapshot) == []


# ---------------------------------------------------------------------------
# ticket_stale
# ---------------------------------------------------------------------------
def test_ticket_stale():
    stale = TicketSample(
        id="ticket-stale",
        status=TicketStatus.OPEN,
        last_activity_at=NOW - timedelta(minutes=31),
    )
    snapshot = _snapshot(
        tickets=[
            stale,
            TicketSample(
                id="ticket-recent",
                status=TicketStatus.OPEN,
                last_activity_at=NOW - timedelta(minutes=5),
            ),
            TicketSample(
                id="ticket-active-run",
                status=TicketStatus.OPEN,
                last_activity_at=NOW - timedelta(hours=2),
                has_active_worker_run=True,
            ),
            TicketSample(
                id="ticket-pending-inbox",
                status=TicketStatus.OPEN,
                last_activity_at=NOW - timedelta(hours=2),
                has_pending_inbox=True,
            ),
            TicketSample(
                id="ticket-busy-pipeline",
                status=TicketStatus.OPEN,
                last_activity_at=NOW - timedelta(hours=2),
                has_busy_pipeline=True,
            ),
            TicketSample(
                id="ticket-waiting",
                status=TicketStatus.WAITING,
                last_activity_at=NOW - timedelta(hours=2),
            ),
        ]
    )
    drafts = checks.ticket_stale(snapshot)
    assert [draft.target_id for draft in drafts] == ["ticket-stale"]
    assert drafts[0].severity is AlertSeverity.WARN
    assert drafts[0].target_kind is AlertTargetKind.TICKET
    assert drafts[0].evidence["idle_minutes"] == 31
    assert drafts[0].evidence["threshold_minutes"] == 30


def test_ticket_stale_threshold_comes_from_snapshot():
    snapshot = WatchdogSnapshot(
        observed_at=NOW,
        thresholds=checks.WatchdogThresholds(ticket_stale_minutes=5),
        tickets=[
            TicketSample(
                id="ticket-6m",
                status=TicketStatus.OPEN,
                last_activity_at=NOW - timedelta(minutes=6),
            )
        ],
    )
    assert [draft.target_id for draft in checks.ticket_stale(snapshot)] == ["ticket-6m"]


# ---------------------------------------------------------------------------
# ticket_churn
# ---------------------------------------------------------------------------
def test_ticket_churn():
    churn = TicketSample(
        id="ticket-churn",
        status=TicketStatus.OPEN,
        worker_runs=tuple(
            WorkerRunSample(started_at=NOW - timedelta(minutes=minute))
            for minute in range(12)
        ),
        reports=(
            ReportSample(
                status=ReportStatus.PROGRESS, created_at=NOW - timedelta(minutes=3)
            ),
        ),
    )
    quiet = TicketSample(
        id="ticket-quiet",
        status=TicketStatus.OPEN,
        worker_runs=tuple(
            WorkerRunSample(started_at=NOW - timedelta(minutes=minute))
            for minute in range(11)
        ),
    )
    # 12 轮里有 1 轮在 60 分钟窗口之外，不算。
    outside_window = TicketSample(
        id="ticket-outside-window",
        status=TicketStatus.OPEN,
        worker_runs=tuple(
            WorkerRunSample(started_at=NOW - timedelta(minutes=minute))
            for minute in range(11)
        )
        + (WorkerRunSample(started_at=NOW - timedelta(minutes=61)),),
    )
    with_blocked_report = replace(
        churn,
        id="ticket-blocked-report",
        reports=(
            ReportSample(
                status=ReportStatus.BLOCKED, created_at=NOW - timedelta(minutes=3)
            ),
        ),
    )
    snapshot = _snapshot(tickets=[churn, quiet, outside_window, with_blocked_report])
    drafts = checks.ticket_churn(snapshot)
    assert [draft.target_id for draft in drafts] == ["ticket-churn"]
    draft = drafts[0]
    assert draft.check == "ticket_churn"
    assert draft.severity is AlertSeverity.WARN
    assert draft.evidence["window_minutes"] == 60
    assert draft.evidence["worker_runs"] == 12
    assert draft.evidence["min_runs"] == 12
    assert draft.evidence["report_statuses"] == ["progress"]


# ---------------------------------------------------------------------------
# pipeline_no_callback
# ---------------------------------------------------------------------------
def test_pipeline_no_callback_without_sample():
    """本次任务开始后没有 CALLBACK：不告警，证据写明 ``no_sample_yet``。"""
    recorder = WatchdogRecorder(clock=lambda: NOW)
    recorder.note_pipeline_started(
        "pipeline-1", device_id="device-1", at=NOW - timedelta(minutes=30)
    )
    snapshot = _snapshot(pipelines=recorder.snapshot().pipelines)
    assert checks.pipeline_no_callback(snapshot) == []

    sample = recorder.pipeline_sample("pipeline-1")
    assert sample is not None and sample.no_sample_yet
    evidence = checks.pipeline_no_callback_evidence(sample, observed_at=NOW)
    assert evidence["reason"] == checks.NO_SAMPLE_YET
    assert evidence["last_callback_at"] is None

    # 任务开始前收到的内核回调也不算本次任务的采样。
    recorder.note_core_callback(at=NOW - timedelta(minutes=45))
    assert recorder.pipeline_sample("pipeline-1").no_sample_yet is True
    assert checks.pipeline_no_callback(
        _snapshot(pipelines=recorder.snapshot().pipelines)
    ) == []


def test_pipeline_no_callback_alerts_when_frozen():
    recorder = WatchdogRecorder(clock=lambda: NOW)
    recorder.note_pipeline_started(
        "pipeline-1", device_id="device-1", at=NOW - timedelta(minutes=20)
    )
    recorder.note_pipeline_progress("pipeline-1", at=NOW - timedelta(minutes=11))
    snapshot = _snapshot(pipelines=recorder.snapshot().pipelines)

    drafts = checks.pipeline_no_callback(snapshot)
    assert [draft.target_id for draft in drafts] == ["device-1"]
    draft = drafts[0]
    assert draft.check == "pipeline_no_callback"
    assert draft.severity is AlertSeverity.CRITICAL
    assert draft.target_kind is AlertTargetKind.DEVICE
    assert draft.evidence["minutes_since_callback"] == 11
    assert draft.evidence["threshold_minutes"] == 10
    assert "reason" not in draft.evidence

    # 有新回调就不告警。
    recorder.note_pipeline_progress("pipeline-1", at=NOW - timedelta(minutes=1))
    assert checks.pipeline_no_callback(
        _snapshot(pipelines=recorder.snapshot().pipelines)
    ) == []


def test_pipeline_no_callback_uses_core_callback_after_start():
    recorder = WatchdogRecorder(clock=lambda: NOW)
    recorder.note_pipeline_started(
        "pipeline-1", device_id="device-1", at=NOW - timedelta(minutes=20)
    )
    recorder.note_core_callback(at=NOW - timedelta(minutes=2))
    sample = recorder.pipeline_sample("pipeline-1")
    assert sample is not None
    assert sample.no_sample_yet is False
    assert sample.last_callback_at == NOW - timedelta(minutes=2)
    assert checks.pipeline_no_callback(
        _snapshot(pipelines=recorder.snapshot().pipelines)
    ) == []


def test_recorder_forgets_finished_pipeline():
    recorder = WatchdogRecorder(clock=lambda: NOW)
    recorder.note_pipeline("pipeline-1", at=NOW - timedelta(minutes=1))
    assert recorder.snapshot().pipeline("pipeline-1") is not None
    recorder.note_pipeline_finished("pipeline-1", at=NOW)
    assert recorder.snapshot().pipelines == ()
    assert recorder.pipeline_sample("pipeline-1") is None
    with pytest.raises(ValueError):
        recorder.note_pipeline("pipeline-1", "unknown")


def test_recorder_progress_before_start_counts_as_sample():
    """回调线程先到 progress 时不能被误判成「还没采样」。"""
    recorder = WatchdogRecorder(clock=lambda: NOW)
    recorder.note_pipeline_progress(
        "pipeline-1", device_id="device-1", at=NOW - timedelta(minutes=5)
    )
    sample = recorder.pipeline_sample("pipeline-1")
    assert sample is not None
    assert sample.no_sample_yet is False
    assert sample.last_callback_at == NOW - timedelta(minutes=5)


# ---------------------------------------------------------------------------
# core_not_ready
# ---------------------------------------------------------------------------
def test_core_not_ready():
    assert checks.core_not_ready(_snapshot(core=None)) == []

    starting = checks.core_not_ready(
        _snapshot(core=CoreSample("starting", NOW - timedelta(minutes=10)))
    )
    assert len(starting) == 1
    assert starting[0].check == "core_not_ready"
    assert starting[0].severity is AlertSeverity.CRITICAL
    assert starting[0].target_kind is AlertTargetKind.CORE
    assert starting[0].evidence["status"] == "starting"
    assert starting[0].evidence["duration_seconds"] == 600

    # 同样状态但不满 10 分钟：不告警。
    assert (
        checks.core_not_ready(
            _snapshot(core=CoreSample("crashed", NOW - timedelta(minutes=9)))
        )
        == []
    )
    # failed 即时告警，不看持续时长。
    failed = checks.core_not_ready(_snapshot(core=CoreSample("failed", None)))
    assert len(failed) == 1
    assert failed[0].severity is AlertSeverity.CRITICAL
    # 就绪状态再久也不告警。
    assert (
        checks.core_not_ready(
            _snapshot(core=CoreSample("ready", NOW - timedelta(days=1)))
        )
        == []
    )


# ---------------------------------------------------------------------------
# queue_stuck
# ---------------------------------------------------------------------------
def test_queue_stuck():
    stuck = QueueSample(
        claimable_count=3,
        oldest_claimable_at=NOW - timedelta(minutes=11),
    )
    drafts = checks.queue_stuck(_snapshot(queue=stuck))
    assert len(drafts) == 1
    draft = drafts[0]
    assert draft.check == "queue_stuck"
    assert draft.severity is AlertSeverity.CRITICAL
    assert draft.target_kind is AlertTargetKind.QUEUE
    assert draft.evidence["claimable_count"] == 3
    assert draft.evidence["waited_minutes"] == 11
    assert draft.evidence["executor_alive"] is True


@pytest.mark.parametrize(
    "blocker",
    [
        {"paused": True},
        {"core_ready": False},
        {"device_connected": False},
        {"has_lease": True},
        {"executor_idle": False},
        {"claimable_count": 0},
        {"oldest_claimable_at": None},
    ],
)
def test_queue_stuck_requires_all_gates(blocker):
    queue = QueueSample(
        claimable_count=3,
        oldest_claimable_at=NOW - timedelta(minutes=11),
    )
    snapshot = _snapshot(queue=replace(queue, **blocker))
    assert checks.queue_stuck(snapshot) == []


def test_queue_stuck_respects_ten_minute_window():
    queue = QueueSample(
        claimable_count=1,
        oldest_claimable_at=NOW - timedelta(minutes=9),
    )
    assert checks.queue_stuck(_snapshot(queue=queue)) == []


# ---------------------------------------------------------------------------
# 六项检查的汇总
# ---------------------------------------------------------------------------
def test_deterministic_checks_covers_all_six_in_08_order():
    assert [check.__name__ for check in checks.CHECK_FUNCTIONS] == [
        "run_heartbeat_stale",
        "ticket_stale",
        "ticket_churn",
        "pipeline_no_callback",
        "core_not_ready",
        "queue_stuck",
    ]
    snapshot = _snapshot(
        runs=[
            RunSample(
                id="run-stale",
                status=AgentRunStatus.RUNNING,
                heartbeat_at=NOW - timedelta(minutes=5),
            )
        ],
        tickets=[
            TicketSample(
                id="ticket-stale",
                status=TicketStatus.OPEN,
                last_activity_at=NOW - timedelta(hours=1),
            ),
            TicketSample(
                id="ticket-churn",
                status=TicketStatus.OPEN,
                worker_runs=tuple(
                    WorkerRunSample(started_at=NOW - timedelta(minutes=minute))
                    for minute in range(12)
                ),
            ),
        ],
        pipelines=[
            PipelineSample(
                pipeline_id="pipeline-1",
                device_id="device-1",
                started_at=NOW - timedelta(minutes=30),
                last_callback_at=NOW - timedelta(minutes=20),
            )
        ],
        core=CoreSample("failed", NOW - timedelta(minutes=1)),
        queue=QueueSample(
            claimable_count=1, oldest_claimable_at=NOW - timedelta(minutes=11)
        ),
    )
    drafts = checks.deterministic_checks(snapshot)
    assert [draft.check for draft in drafts] == [
        "run_heartbeat_stale",
        "ticket_stale",
        "ticket_churn",
        "pipeline_no_callback",
        "core_not_ready",
        "queue_stuck",
    ]


# ---------------------------------------------------------------------------
# 落库语义：去重与条件消失解除
# ---------------------------------------------------------------------------
@pytest.fixture
def session(tmp_path):
    engine = create_engine(f"sqlite:///{tmp_path / 'watchdog.db'}")
    SQLModel.metadata.create_all(engine)
    with Session(engine) as session:
        yield session
    engine.dispose()


def _draft(
    *,
    check: str = "ticket_stale",
    target_kind: AlertTargetKind = AlertTargetKind.TICKET,
    target_id: str | None = "ticket-1",
    message: str = "工单没有活动",
    evidence: dict | None = None,
) -> AlertDraft:
    return AlertDraft(
        check=check,
        severity=AlertSeverity.WARN,
        target_kind=target_kind,
        target_id=target_id,
        message=message,
        evidence=evidence or {"last_activity_at": "2026-03-01T11:00:00"},
    )


def test_open_alert_not_duplicated(session):
    first, created = upsert_open_alert(session, _draft(message="首次"), now=NOW)
    assert created is True

    second, created_again = upsert_open_alert(
        session,
        _draft(message="第二次", evidence={"last_activity_at": "later"}),
        now=NOW + timedelta(minutes=5),
    )
    assert created_again is False
    assert second.id == first.id

    rows = session.exec(select(WatchdogAlert)).all()
    assert len(rows) == 1
    row = rows[0]
    # 保留首次时间、正文与级别；只更新 evidence（spec §4.1）。
    assert row.created_at == NOW
    assert row.message == "首次"
    assert row.evidence["last_activity_at"] == "2026-03-01T11:00:00"
    assert row.evidence["latest"] == {"last_activity_at": "later"}
    assert row.evidence["last_seen_at"] == (NOW + timedelta(minutes=5)).isoformat()
    assert row.resolved_at is None

    # 解除之后同一目标再次命中是新的一行，不与历史行合并。
    resolve_condition_gone(
        session,
        checked_checks={"ticket_stale"},
        active_keys=set(),
        now=NOW + timedelta(minutes=10),
    )
    assert session.exec(select(WatchdogAlert)).one().resolved_at is not None
    third, created_third = upsert_open_alert(
        session, _draft(message="重新命中"), now=NOW + timedelta(minutes=11)
    )
    assert created_third is True
    assert third.id != first.id
    assert len(session.exec(select(WatchdogAlert)).all()) == 2


def test_different_targets_do_not_deduplicate(session):
    upsert_open_alert(session, _draft(target_id="ticket-1"), now=NOW)
    upsert_open_alert(session, _draft(target_id="ticket-2"), now=NOW)
    upsert_open_alert(
        session,
        _draft(check="run_heartbeat_stale", target_kind=AlertTargetKind.RUN),
        now=NOW,
    )
    assert len(session.exec(select(WatchdogAlert)).all()) == 3


def test_condition_cleared_resolves(session):
    active_run = _draft(
        check="run_heartbeat_stale",
        target_kind=AlertTargetKind.RUN,
        target_id="run-1",
    )
    gone_ticket = _draft(check="ticket_stale", target_id="ticket-1")
    semantic = _draft(check="ticket_no_progress", target_id="ticket-1")
    for draft in (active_run, gone_ticket, semantic):
        upsert_open_alert(session, draft, now=NOW)

    resolved = resolve_condition_gone(
        session,
        checked_checks={"run_heartbeat_stale", "ticket_stale"},
        active_keys={condition_key("run_heartbeat_stale", "run", "run-1")},
        now=NOW + timedelta(minutes=5),
    )
    assert [alert.check for alert in resolved] == ["ticket_stale"]

    by_check = {
        alert.check: alert for alert in session.exec(select(WatchdogAlert)).all()
    }
    assert by_check["run_heartbeat_stale"].resolved_at is None
    assert by_check["ticket_stale"].resolved_at == NOW + timedelta(minutes=5)
    # 语义检查不在本轮 checked_checks 里，不能被确定性检查误解除。
    assert by_check["ticket_no_progress"].resolved_at is None

    # 幂等：再跑一轮不会改已解除的、也不会碰还在活跃的。
    assert (
        resolve_condition_gone(
            session,
            checked_checks={"run_heartbeat_stale", "ticket_stale"},
            active_keys={condition_key("run_heartbeat_stale", "run", "run-1")},
            now=NOW + timedelta(minutes=10),
        )
        == []
    )
    assert (
        resolve_condition_gone(
            session, checked_checks=set(), active_keys=set(), now=NOW
        )
        == []
    )
