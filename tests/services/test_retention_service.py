"""``services/retention_service`` 的行为测试（M2-12）。

覆盖 docs/04 §10.2/§10.3/§10.4 的硬约束：

- 日志分级保留：三个来源各自的「按天删」，以及天数删完仍超上限时按 ``id`` 升序
  删到上限以内（两个维度取更严格的一方）；
- 分批：``LogRepository.purge`` 每批的行数上限由 ``PURGE_BATCH_SIZE`` 控制，
  服务层在非零轮之间 ``asyncio.sleep`` 让出写锁；
- 流水线：90 天或最近 500 条，先触发者生效；
- 截图：7 天删文件 + 打 ``deleted_at``、总大小超限按 ``created_at`` 升序淘汰、
  记录随 90 天流水线历史清理；未过期文件不受影响；
- ``temp/screencap`` 中转图按 mtime 1 小时阈值删，且不进 ``screenshot`` 表；
- ``session_factory=None`` 时取 ``maa_api.db.session`` 的模块属性，资源根从
  ``session.DB_PATH`` 推导（不硬编码 ``resource/``）；
- 异常不吞，原样抛给 APScheduler 侧记录。

用例形态：同步测试函数 + ``asyncio.run(scenario())``（本仓无 pytest-asyncio），
临时资源根/库/会话工厂来自 ``tests/services/conftest.py``。
"""

import asyncio
import os
import time
from datetime import datetime, timedelta
from pathlib import Path

import pytest
from sqlalchemy import select

from maa_api.agent.artifacts.service import DB_STORAGE_MAX_BYTES, ArtifactService
from maa_api.db import session as db_session_module
from maa_api.db.models import (
    AgentInbox,
    AgentRun,
    AgentSession,
    AgentTrigger,
    Artifact,
    CostLedgerEntry,
    LogEntry,
    Pipeline,
    Screenshot,
    Ticket,
    TraceSpan,
    TriggerObservation,
    WatchdogAlert,
    utcnow,
)
from maa_api.db.repositories import log as log_repo_module
from maa_api.domain.enums import (
    AgentRunKind,
    AgentRunStatus,
    InboxKind,
    InboxStatus,
    LogLevel,
    LogSource,
    PipelineSource,
    Priority,
    ScreenshotBackend,
    ScreenshotTrigger,
)
from maa_api.services import retention_service as r


# ---------------------------------------------------------------------------
# 构造helper
# ---------------------------------------------------------------------------
def _log(
    content: str,
    *,
    source: LogSource = LogSource.SERVER,
    level: LogLevel = LogLevel.INFO,
    created_at: datetime | None = None,
) -> LogEntry:
    fields = {"source": source, "level": level, "content": content}
    if created_at is not None:
        fields["created_at"] = created_at
    return LogEntry(**fields)


def _pipeline(created_at: datetime) -> Pipeline:
    return Pipeline(
        source=PipelineSource.MANUAL,
        priority=int(Priority.MANUAL),
        task_count=1,
        created_at=created_at,
    )


def _make_shot(
    resource_root: Path,
    name: str,
    *,
    created_at: datetime,
    size_bytes: int = 10,
) -> tuple[Screenshot, Path]:
    """建一条截图记录 + 对应文件，返回（记录，绝对路径）。"""
    relative = f"image/screenshot/20260101/{name}"
    path = resource_root / relative
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(b"x" * size_bytes)
    shot = Screenshot(
        trigger=ScreenshotTrigger.MANUAL,
        backend=ScreenshotBackend.CORE,
        path=relative,
        format="jpeg",
        width=1,
        height=1,
        size_bytes=size_bytes,
        created_at=created_at,
    )
    return shot, path


def _make_temp(resource_root: Path, name: str, *, age_seconds: float) -> Path:
    """在 ``<resource>/temp/screencap/`` 放一个 mtime 为 ``age_seconds`` 前的文件。"""
    directory = resource_root / r.TEMP_SCREENCAP_DIR
    directory.mkdir(parents=True, exist_ok=True)
    path = directory / name
    path.write_bytes(b"x")
    mtime = time.time() - age_seconds
    os.utime(path, (mtime, mtime))
    return path


async def _insert(factory, rows) -> None:
    async with factory() as db:
        db.add_all(rows)
        await db.commit()


async def _all(factory, model):
    async with factory() as db:
        return list((await db.execute(select(model))).scalars().all())


# ---------------------------------------------------------------------------
# 日志：按天 + 按条数上限
# ---------------------------------------------------------------------------
def test_logs_purged_by_age_per_source(retention_session_factory) -> None:
    """每个来源各按自己的天数阈值删；窗口内的新日志不受影响。"""
    now = utcnow()

    async def scenario():
        await _insert(
            retention_session_factory,
            [
                # 超过各自天数阈值的三条，必须删
                _log("maa-old", source=LogSource.MAA_TASK, created_at=now - timedelta(days=40)),
                _log("server-old", source=LogSource.SERVER, created_at=now - timedelta(days=10)),
                _log(
                    "debug-old",
                    source=LogSource.MAACORE_DEBUG,
                    level=LogLevel.WARNING,
                    created_at=now - timedelta(days=5),
                ),
                # 阈值内：29 天 / 6 天 / 2 天，保留
                _log("maa-new", source=LogSource.MAA_TASK, created_at=now - timedelta(days=29)),
                _log("server-new", source=LogSource.SERVER, created_at=now - timedelta(days=6)),
                _log(
                    "debug-new",
                    source=LogSource.MAACORE_DEBUG,
                    level=LogLevel.WARNING,
                    created_at=now - timedelta(days=2),
                ),
            ],
        )
        out = await r.run_retention(session_factory=retention_session_factory)
        assert out["logs_deleted"] == 3, out
        assert isinstance(out["pipelines_deleted"], int)
        logs = await _all(retention_session_factory, LogEntry)
        assert {x.content for x in logs} == {"maa-new", "server-new", "debug-new"}

    asyncio.run(scenario())


def test_logs_purged_by_count_cap_keeps_newest(
    retention_session_factory, monkeypatch
) -> None:
    """天数维度删完仍超上限时，按 id 升序删到上限以内，保留最新几条。"""
    now = utcnow()
    policies = dict(r.LOG_RETENTION)
    policies[LogSource.SERVER] = r.LogRetention(days=7, max_entries=3)
    monkeypatch.setattr(r, "LOG_RETENTION", policies)

    async def scenario():
        await _insert(
            retention_session_factory,
            [
                _log(f"line-{i}", created_at=now - timedelta(hours=5 - i))
                for i in range(5)
            ],
        )
        out = await r.run_retention(session_factory=retention_session_factory)
        assert out["logs_deleted"] == 2, out
        logs = await _all(retention_session_factory, LogEntry)
        assert [x.content for x in logs] == ["line-2", "line-3", "line-4"]

    asyncio.run(scenario())


def test_log_purge_waits_between_batches(
    retention_session_factory, monkeypatch
) -> None:
    """分批：每批行数由 PURGE_BATCH_SIZE 控制，非零轮之间 sleep 让出写锁。"""
    now = utcnow()
    monkeypatch.setattr(log_repo_module, "PURGE_BATCH_SIZE", 1)
    sleep_calls: list[float] = []

    async def fake_sleep(seconds: float) -> None:
        sleep_calls.append(seconds)

    monkeypatch.setattr(asyncio, "sleep", fake_sleep)

    async def scenario():
        await _insert(
            retention_session_factory,
            [
                _log("old-1", created_at=now - timedelta(days=10)),
                _log("old-2", created_at=now - timedelta(days=9)),
            ],
        )
        out = await r.run_retention(session_factory=retention_session_factory)
        assert out["logs_deleted"] == 2, out

    asyncio.run(scenario())
    assert sleep_calls == [r.BATCH_PAUSE_SECONDS], sleep_calls


# ---------------------------------------------------------------------------
# 流水线：90 天或最近 500 条
# ---------------------------------------------------------------------------
def test_pipeline_purged_by_age_and_recent_kept(retention_session_factory) -> None:
    """超过 90 天的流水线删除，窗口内的保留（先触发者生效）。"""
    now = utcnow()

    async def scenario():
        await _insert(
            retention_session_factory,
            [
                _pipeline(now - timedelta(days=100)),
                _pipeline(now - timedelta(days=89)),
            ],
        )
        out = await r.run_retention(session_factory=retention_session_factory)
        assert out["pipelines_deleted"] == 1, out
        remaining = await _all(retention_session_factory, Pipeline)
        assert len(remaining) == 1
        assert remaining[0].created_at >= now - timedelta(days=90)

    asyncio.run(scenario())


def test_pipeline_purged_by_keep_latest(retention_session_factory, monkeypatch) -> None:
    """条数维度先触发时，只保留最近 N 条（默认 500，用例缩小到 2）。"""
    now = utcnow()
    monkeypatch.setattr(r, "PIPELINE_KEEP_LATEST", 2)

    async def scenario():
        await _insert(
            retention_session_factory,
            [
                _pipeline(now - timedelta(hours=4 - i))
                for i in range(4)
            ],
        )
        out = await r.run_retention(session_factory=retention_session_factory)
        assert out["pipelines_deleted"] == 2, out
        remaining = await _all(retention_session_factory, Pipeline)
        assert len(remaining) == 2

    asyncio.run(scenario())


# ---------------------------------------------------------------------------
# 截图：7 天文件 + 512 MB 上限 + 记录随 90 天历史
# ---------------------------------------------------------------------------
def test_expired_screenshot_file_deleted_and_marked(
    resource_root, retention_session_factory
) -> None:
    """过期截图删文件并打 deleted_at；未过期文件与记录都不受影响。"""
    now = utcnow()
    old_shot, old_file = _make_shot(resource_root, "old.jpg", created_at=now - timedelta(days=8))
    fresh_shot, fresh_file = _make_shot(resource_root, "fresh.jpg", created_at=now - timedelta(hours=1))

    async def scenario():
        await _insert(retention_session_factory, [old_shot, fresh_shot])
        out = await r.run_retention(session_factory=retention_session_factory)
        assert out["screenshots_deleted"] == 1, out
        shots = {x.path: x for x in await _all(retention_session_factory, Screenshot)}
        assert shots[old_shot.path].deleted_at is not None
        assert shots[fresh_shot.path].deleted_at is None

    asyncio.run(scenario())
    assert not old_file.exists()
    assert fresh_file.exists()


def test_screenshot_size_cap_evicts_oldest_first(
    resource_root, retention_session_factory, monkeypatch
) -> None:
    """总大小超上限时按 created_at 升序淘汰最旧的，直到降到上限以内。"""
    now = utcnow()
    monkeypatch.setattr(r, "SCREENSHOT_MAX_TOTAL_BYTES", 25)
    oldest, oldest_file = _make_shot(resource_root, "a.jpg", created_at=now - timedelta(days=2))
    middle, middle_file = _make_shot(resource_root, "b.jpg", created_at=now - timedelta(days=1))
    newest, newest_file = _make_shot(resource_root, "c.jpg", created_at=now - timedelta(hours=1))

    async def scenario():
        await _insert(retention_session_factory, [newest, middle, oldest])
        out = await r.run_retention(session_factory=retention_session_factory)
        assert out["screenshots_deleted"] == 1, out
        shots = {x.path: x for x in await _all(retention_session_factory, Screenshot)}
        assert shots[oldest.path].deleted_at is not None
        assert shots[middle.path].deleted_at is None
        assert shots[newest.path].deleted_at is None

    asyncio.run(scenario())
    assert not oldest_file.exists()
    assert middle_file.exists()
    assert newest_file.exists()


def test_old_screenshot_record_purged_with_pipeline_retention(
    resource_root, retention_session_factory
) -> None:
    """记录本身随 90 天的流水线历史清理（文件早已在 7 天维度删掉）。"""
    now = utcnow()
    shot, path = _make_shot(resource_root, "ancient.jpg", created_at=now - timedelta(days=100))

    async def scenario():
        await _insert(retention_session_factory, [shot])
        out = await r.run_retention(session_factory=retention_session_factory)
        assert out["screenshots_deleted"] == 1, out
        assert await _all(retention_session_factory, Screenshot) == []

    asyncio.run(scenario())
    assert not path.exists()


# ---------------------------------------------------------------------------
# temp/screencap 中转图：1 小时阈值
# ---------------------------------------------------------------------------
def test_temp_screencap_one_hour_threshold(
    resource_root, retention_session_factory
) -> None:
    """mtime 超过 1 小时的中转图删除，新鲜的保留，且不进 screenshot 表。"""
    stale = _make_temp(resource_root, "stale.jpg", age_seconds=7200)
    fresh = _make_temp(resource_root, "fresh.jpg", age_seconds=60)

    async def scenario():
        out = await r.run_retention(session_factory=retention_session_factory)
        assert out["temp_files_deleted"] == 1, out
        assert await _all(retention_session_factory, Screenshot) == []

    asyncio.run(scenario())
    assert not stale.exists()
    assert fresh.exists()


def test_temp_screencap_missing_directory_is_noop(retention_session_factory) -> None:
    """从未截过图时目录不存在，清理任务不报错、计数为 0。"""

    async def scenario():
        out = await r.run_retention(session_factory=retention_session_factory)
        assert out["temp_files_deleted"] == 0, out

    asyncio.run(scenario())


# ---------------------------------------------------------------------------
# 接线契约：默认 session_factory、资源根推导、异常不吞
# ---------------------------------------------------------------------------
def test_default_session_factory_and_resource_root_from_module(
    resource_root, retention_session_factory, monkeypatch
) -> None:
    """``session_factory=None`` 时取 session 模块属性，资源根从 DB_PATH 推导。"""
    monkeypatch.setattr(db_session_module, "session_factory", retention_session_factory)
    # 若资源根仍指向仓库的 resource/，这个过期中转图不会被删
    stale = _make_temp(resource_root, "stale.jpg", age_seconds=7200)
    now = utcnow()

    async def scenario():
        await _insert(
            retention_session_factory,
            [_log("old", created_at=now - timedelta(days=10))],
        )
        return await r.run_retention()

    out = asyncio.run(scenario())
    assert out["logs_deleted"] == 1, out
    assert out["temp_files_deleted"] == 1, out
    assert not stale.exists()


def test_errors_are_not_swallowed(
    retention_session_factory, monkeypatch
) -> None:
    """清理异常原样抛出（APScheduler 侧记录），不吞成空 dict。"""

    async def boom(self, source, *, before, keep_max):
        raise RuntimeError("purge 失败")

    monkeypatch.setattr(log_repo_module.LogRepository, "purge", boom)

    async def scenario():
        await r.run_retention(session_factory=retention_session_factory)

    with pytest.raises(RuntimeError, match="purge 失败"):
        asyncio.run(scenario())


# ---------------------------------------------------------------------------
# 代理运行：不在保留策略范围内
# ---------------------------------------------------------------------------
def test_retention_keeps_agent_runs(retention_session_factory) -> None:
    """运行表没有保留规则：400 天前结束的运行在清理后仍在（03 §14）。"""
    now = utcnow()
    run_id = "retention-agent-run"
    finished_at = now - timedelta(days=400)

    async def scenario():
        # 运行与会话没有 ORM relationship，先落会话再落运行（外键已开启）。
        await _insert(
            retention_session_factory,
            [
                AgentSession(
                    id="retention-agent-session",
                    model="retention-model",
                    created_at=now - timedelta(days=400),
                )
            ],
        )
        await _insert(
            retention_session_factory,
            [
                AgentRun(
                    id=run_id,
                    session_id="retention-agent-session",
                    root_run_id=run_id,
                    agent_name="legacy_chat",
                    kind=AgentRunKind.LEGACY_TURN,
                    status=AgentRunStatus.SUCCEEDED,
                    boot_id="retention-boot",
                    started_at=now - timedelta(days=400, minutes=5),
                    heartbeat_at=finished_at,
                    finished_at=finished_at,
                )
            ],
        )
        await r.run_retention(session_factory=retention_session_factory)
        runs = await _all(retention_session_factory, AgentRun)
        assert [item.id for item in runs] == [run_id]
        assert runs[0].status == AgentRunStatus.SUCCEEDED

    asyncio.run(scenario())


# ---------------------------------------------------------------------------
# 成本账本（M67）
# ---------------------------------------------------------------------------
def test_cost_ledger_purged_after_365_days(retention_session_factory) -> None:
    """超过 365 天的成本账本行删除，窗口内的保留；既有五个键语义不变。"""
    now = utcnow()

    def ledger(created_at: datetime) -> CostLedgerEntry:
        return CostLedgerEntry(
            agent_name="legacy_chat",
            role="worker",
            model_profile="default",
            model="retention-model",
            prompt_tokens=10,
            completion_tokens=2,
            cost_micros=100,
            currency="CNY",
            created_at=created_at,
        )

    async def scenario():
        await _insert(
            retention_session_factory,
            [
                ledger(now - timedelta(days=366)),
                ledger(now - timedelta(days=364)),
            ],
        )
        out = await r.run_retention(session_factory=retention_session_factory)
        assert out["cost_ledger_deleted"] == 1, out
        # M2/M29 的五个键仍在，语义不变。
        for key in (
            "logs_deleted",
            "pipelines_deleted",
            "screenshots_deleted",
            "temp_files_deleted",
            "copilots_deleted",
        ):
            assert isinstance(out[key], int), out
        rows = await _all(retention_session_factory, CostLedgerEntry)
        assert len(rows) == 1
        assert rows[0].created_at > now - timedelta(days=365)

    asyncio.run(scenario())


# ---------------------------------------------------------------------------
# 运行追踪（M68）
# ---------------------------------------------------------------------------
def test_run_retention_purges_trace_spans_older_than_retention_days(
    retention_session_factory, monkeypatch
) -> None:
    """超过 ``trace.retention_days``（默认 14）的 span 删除，窗口内的保留。"""
    from maa_api.settings import Settings

    monkeypatch.setattr(r, "get_settings", lambda: Settings())
    now = utcnow()
    assert r.TRACE_RETENTION_DAYS == 14
    assert r._trace_retention_days() == 14

    def span(span_id: str, started_at: datetime) -> TraceSpan:
        return TraceSpan(
            trace_id="retention-trace",
            span_id=span_id,
            name="tool.execute",
            kind="tool",
            status="ok",
            started_at=started_at,
        )

    async def scenario():
        await _insert(
            retention_session_factory,
            [
                span("old-span", now - timedelta(days=15)),
                span("fresh-span", now - timedelta(days=2)),
            ],
        )
        out = await r.run_retention(session_factory=retention_session_factory)
        # 新键在；M67 的成本账本键也在（每一步都算过，值为 0 也是 int）。
        assert out["trace_spans_deleted"] == 1, out
        for key in (
            "logs_deleted",
            "pipelines_deleted",
            "screenshots_deleted",
            "temp_files_deleted",
            "copilots_deleted",
            "cost_ledger_deleted",
            "trace_spans_deleted",
        ):
            assert isinstance(out[key], int), out
        rows = await _all(retention_session_factory, TraceSpan)
        assert [row.span_id for row in rows] == ["fresh-span"]

    asyncio.run(scenario())


# ---------------------------------------------------------------------------
# Agent 收件箱（M69）
# ---------------------------------------------------------------------------
def test_agent_inbox_purged_after_30_days(retention_session_factory) -> None:
    """已处理超过 30 天的收件箱项删除；窗口内的与 pending 保留（规格 §4.5.7）。"""
    now = utcnow()
    assert r.INBOX_RETENTION_DAYS == 30
    session_id = "retention-inbox-session"

    def inbox(status: InboxStatus, *, consumed_days: float | None) -> AgentInbox:
        return AgentInbox(
            session_id=session_id,
            kind=InboxKind.REPORT,
            payload={"event": "report"},
            status=status,
            created_at=now - timedelta(days=40),
            consumed_at=(
                None
                if consumed_days is None
                else now - timedelta(days=consumed_days)
            ),
        )

    async def scenario():
        await _insert(
            retention_session_factory,
            [
                AgentSession(
                    id=session_id,
                    model="retention-model",
                    created_at=now - timedelta(days=40),
                )
            ],
        )
        await _insert(
            retention_session_factory,
            [
                # 31 天前消费 / 丢弃：超过 30 天，删除。
                inbox(InboxStatus.CONSUMED, consumed_days=31),
                inbox(InboxStatus.DROPPED, consumed_days=31),
                # 29 天前消费：窗口内，保留。
                inbox(InboxStatus.CONSUMED, consumed_days=29),
                # 40 天前创建但仍 pending：不按时间清理，保留。
                inbox(InboxStatus.PENDING, consumed_days=None),
            ],
        )
        out = await r.run_retention(session_factory=retention_session_factory)
        assert out["agent_inbox_deleted"] == 2, out
        assert isinstance(out["agent_inbox_deleted"], int)
        rows = await _all(retention_session_factory, AgentInbox)
        assert [row.status for row in rows] == [
            InboxStatus.CONSUMED.value,
            InboxStatus.PENDING.value,
        ]
        assert rows[0].consumed_at is not None
        assert rows[0].consumed_at > now - timedelta(days=30)
        assert rows[1].consumed_at is None

    asyncio.run(scenario())


# ---------------------------------------------------------------------------
# 产物（M75）
# ---------------------------------------------------------------------------
def test_spill_artifacts_purged_after_30_days(
    retention_session_factory, resource_root
) -> None:
    """只删 30 天前、未置顶的 spill（行 + 文件）；笔记与最近/置顶的保留。"""
    now = utcnow()

    async def scenario():
        service = ArtifactService(
            retention_session_factory, resource_root=resource_root
        )
        old = await service.put(
            kind="spill",
            title="旧的大输出",
            body=b"x" * (DB_STORAGE_MAX_BYTES + 1),
        )
        pinned = await service.put(kind="spill", title="置顶输出", body=b"keep")
        await service.pin(str(pinned.id), pinned=True)
        recent = await service.put(kind="spill", title="最近输出", body=b"keep")
        note = await service.put(kind="note", title="笔记", body=b"keep")

        async with retention_session_factory() as db:
            row = await db.get(Artifact, old.id)
            assert row is not None
            row.created_at = now - timedelta(days=31)
            await db.commit()

        assert old.path is not None
        old_file = resource_root / old.path
        assert old_file.is_file()

        out = await r.run_retention(session_factory=retention_session_factory)
        assert out["artifacts_deleted"] == 1, out
        assert isinstance(out["artifacts_deleted"], int)
        assert not old_file.exists()
        rows = await _all(retention_session_factory, Artifact)
        assert {row.id for row in rows} == {pinned.id, recent.id, note.id}

    asyncio.run(scenario())


# ---------------------------------------------------------------------------
# 事件与观察记录（M81）
# ---------------------------------------------------------------------------
def test_retention_keeps_triggers_with_ticket_or_recent_observation(
    retention_session_factory,
) -> None:
    """事件保留 30 天：有工单、或有 90 天内观察记录的不删；观察记录自己保留 90 天。"""
    now = utcnow()

    async def scenario():
        ticket = Ticket(
            title="诊断：流水线失败",
            kind="diagnose",
            origin="user",
            created_at=now,
        )
        await _insert(retention_session_factory, [ticket])
        await _insert(
            retention_session_factory,
            [
                # 有工单：即使很旧也留着（工单历史要能点回事件）。
                AgentTrigger(
                    source="pipeline",
                    kind="pipeline.failed",
                    thread_key="pipeline:ticket",
                    payload={"title": "日常"},
                    ticket_id=ticket.id,
                    created_at=now - timedelta(days=40),
                ),
                # 没有工单，但挂着 90 天内的观察记录：留着让用户审阅。
                AgentTrigger(
                    source="device",
                    kind="device.unavailable",
                    thread_key="device:observation",
                    payload={},
                    created_at=now - timedelta(days=40),
                ),
                # 没有工单、观察记录也过期：删。
                AgentTrigger(
                    source="device",
                    kind="device.unavailable",
                    thread_key="device:expired",
                    payload={},
                    created_at=now - timedelta(days=40),
                ),
                # 30 天内的事件：不删。
                AgentTrigger(
                    source="system",
                    kind="system.sanity_full",
                    thread_key="system:sanity",
                    payload={},
                    created_at=now - timedelta(days=10),
                ),
            ],
        )
        async with retention_session_factory() as db:
            rows = list(
                (
                    await db.execute(
                        select(AgentTrigger).order_by(AgentTrigger.id.asc())
                    )
                )
                .scalars()
                .all()
            )
            kept_by_ticket, kept_by_observation, expired, recent = rows
            db.add(
                TriggerObservation(
                    trigger_id=int(kept_by_observation.id),
                    rule_id="device-unavailable",
                    decision={"decision": "accept"},
                    created_at=now - timedelta(days=10),
                )
            )
            db.add(
                TriggerObservation(
                    trigger_id=int(expired.id),
                    rule_id="device-unavailable",
                    decision={"decision": "accept"},
                    created_at=now - timedelta(days=100),
                )
            )
            await db.commit()
            ids = {
                "kept_by_ticket": int(kept_by_ticket.id),
                "kept_by_observation": int(kept_by_observation.id),
                "expired": int(expired.id),
                "recent": int(recent.id),
            }

        out = await r.run_retention(session_factory=retention_session_factory)
        assert out["triggers_deleted"] == 1, out
        assert out["observations_deleted"] == 1, out
        triggers = await _all(retention_session_factory, AgentTrigger)
        assert {int(row.id) for row in triggers} == {
            ids["kept_by_ticket"],
            ids["kept_by_observation"],
            ids["recent"],
        }
        observations = await _all(retention_session_factory, TriggerObservation)
        assert [int(row.trigger_id) for row in observations] == [
            ids["kept_by_observation"]
        ]

    asyncio.run(scenario())


def test_watchdog_alerts_purged_after_90_days(retention_session_factory) -> None:
    """M83 / 03 §14：``watchdog_alert`` 超过 90 天删除，已解除的也一视同仁。"""

    async def scenario() -> None:
        now = utcnow()

        def alert(target_id: str, created_at: datetime, *, resolved: bool) -> WatchdogAlert:
            return WatchdogAlert(
                check="run_heartbeat_stale",
                severity="warn",
                target_kind="run",
                target_id=target_id,
                message="运行心跳超时",
                evidence={},
                action="interrupt_run",
                action_status="recorded",
                created_at=created_at,
                resolved_at=now if resolved else None,
            )

        async with retention_session_factory() as db:
            db.add(alert("old", now - timedelta(days=91), resolved=False))
            db.add(alert("old-resolved", now - timedelta(days=100), resolved=True))
            db.add(alert("recent", now - timedelta(days=10), resolved=False))
            await db.commit()

        out = await r.run_retention(session_factory=retention_session_factory)
        assert out["watchdog_alerts_deleted"] == 2, out
        rows = await _all(retention_session_factory, WatchdogAlert)
        assert [row.target_id for row in rows] == ["recent"]

    asyncio.run(scenario())
