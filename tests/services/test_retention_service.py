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

from maa_api.db import session as db_session_module
from maa_api.db.models import LogEntry, Pipeline, Screenshot, utcnow
from maa_api.db.repositories import log as log_repo_module
from maa_api.domain.enums import (
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
