"""Contracts for the bounded, thread-safe log convergence service."""

from __future__ import annotations

import asyncio
import logging
import threading
import time

import pytest
from sqlalchemy import select

from maa_api.db import session as db_session
from maa_api.db.models import LogEntry
from maa_api.domain.enums import LogLevel, LogSource
from maa_api.services.log_hub import (
    LEVEL_ORDER,
    SOURCE_DB_TO_WIRE,
    SOURCE_WIRE_TO_DB,
    LogHub,
    LogHubHandler,
    LogRecord,
    attach_log_hub,
    current_pipeline_id,
    get_log_hub,
    mask_token,
    set_log_hub,
)
from maa_api.settings import LogSettings, Settings


def _record(
    content: str,
    *,
    level: str = "INFO",
    source: str = "service",
    logger: str | None = None,
) -> LogRecord:
    return LogRecord(
        ts=time.time(), source=source, level=level, content=content, logger=logger
    )


def test_wire_database_mappings_and_record_defaults() -> None:
    assert SOURCE_WIRE_TO_DB == {
        "task": LogSource.MAA_TASK,
        "service": LogSource.SERVER,
        "core": LogSource.MAACORE_DEBUG,
    }
    assert SOURCE_DB_TO_WIRE == {
        LogSource.MAA_TASK: "task",
        LogSource.SERVER: "service",
        LogSource.MAACORE_DEBUG: "core",
    }
    assert list(LEVEL_ORDER) == ["DEBUG", "INFO", "WARNING", "ERROR", "CRITICAL"]
    row = LogRecord(ts=1.5, source="task", level="INFO", content="started")
    assert row.id == 0
    assert row.pipeline_id is None
    assert row.task_id is None
    assert row.logger is None
    assert row.raw is None
    assert row.attachment is None


def test_ring_is_bounded_and_snapshot_reports_truncation() -> None:
    hub = LogHub(ring_size=3)
    for index in range(5):
        hub.offer(_record(str(index)))

    records, truncated = hub.snapshot_after(0)
    assert [record.id for record in records] == [3, 4, 5]
    assert truncated is True
    assert hub.snapshot_after(2)[1] is False
    assert [record.id for record in hub.snapshot_after(3)[0]] == [4, 5]


def test_concurrent_offer_assigns_unique_ordered_ids() -> None:
    hub = LogHub(ring_size=800)
    threads = [
        threading.Thread(
            target=lambda worker=i: [hub.offer(_record(f"{worker}:{n}")) for n in range(100)]
        )
        for i in range(8)
    ]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join()

    records, _ = hub.snapshot_after(0)
    ids = [record.id for record in records]
    assert ids == list(range(1, 801))
    assert len(set(ids)) == 800


def test_current_pipeline_context_is_attached_to_offered_record() -> None:
    hub = LogHub()
    token = current_pipeline_id.set("pipeline-1")
    try:
        record = _record("work")
        hub.offer(record)
    finally:
        current_pipeline_id.reset(token)

    assert record.pipeline_id == "pipeline-1"


def test_backpressure_drops_by_queue_usage_and_reports_without_requeueing() -> None:
    hub = LogHub(db_queue_size=10)
    # Below 70% all levels are accepted.
    hub._enqueue_db(_record("debug-0", level="DEBUG"))
    assert hub._db_queue.qsize() == 1

    while hub._db_queue.qsize() < 7:
        hub._db_queue.put_nowait(_record("filler"))
    hub._enqueue_db(_record("debug-70", level="DEBUG"))
    assert hub._db_queue.qsize() == 7
    hub._enqueue_db(_record("info-70", level="INFO"))
    assert hub._db_queue.qsize() == 8

    hub._db_queue.put_nowait(_record("filler"))
    hub._enqueue_db(_record("info-90", level="INFO"))
    assert hub._db_queue.qsize() == 9
    hub._enqueue_db(_record("warning-90", level="WARNING"))
    assert hub._db_queue.qsize() == 10
    hub._enqueue_db(_record("warning-full", level="WARNING"))
    assert hub._db_queue.qsize() == 10

    # The internal warning goes to the ring but never back into the full DB queue.
    for _ in range(999):
        hub._enqueue_db(_record("overflow", level="WARNING"))
    assert hub._db_queue.qsize() == 10
    ring, _ = hub.snapshot_after(0)
    assert any("日志洪峰，已丢弃 1000 条低级别日志" == row.content for row in ring)


def test_core_and_uvicorn_access_have_persistence_thresholds(monkeypatch: pytest.MonkeyPatch) -> None:
    from maa_api.services import log_hub as module

    monkeypatch.setattr(
        module,
        "get_settings",
        lambda: Settings(log=LogSettings(persist_maacore_debug_level="WARNING")),
    )
    hub = LogHub()

    assert not hub._should_persist(_record("native info", source="core", level="INFO"))
    assert hub._should_persist(_record("native warning", source="core", level="WARNING"))
    assert not hub._should_persist(
        _record("GET /", source="service", logger="uvicorn.access", level="INFO")
    )
    assert hub._should_persist(
        _record("GET / failed", source="service", logger="uvicorn.access", level="ERROR")
    )
    assert hub._should_persist(_record("app info", logger="maa_api"))


def test_offer_from_worker_thread_dispatches_on_event_loop(
    monkeypatch: pytest.MonkeyPatch, retention_session_factory
) -> None:
    monkeypatch.setattr(db_session, "session_factory", retention_session_factory)

    async def scenario() -> None:
        hub = LogHub(batch_size=20, flush_interval=0.05)
        await hub.start()
        delivered = asyncio.Event()
        seen: list[tuple[int, LogRecord]] = []

        class Sink:
            def put_log(self, record: LogRecord) -> None:
                seen.append((threading.get_ident(), record))
                delivered.set()

        hub.add_sink(Sink())
        loop_thread = threading.get_ident()
        worker = threading.Thread(target=hub.offer, args=(_record("from thread"),))
        worker.start()
        worker.join()
        await asyncio.wait_for(delivered.wait(), timeout=1)
        assert seen[0][0] == loop_thread
        assert seen[0][1].content == "from thread"
        await hub.aclose()

    asyncio.run(scenario())


def test_batch_flushes_on_size_interval_and_close(
    monkeypatch: pytest.MonkeyPatch, retention_session_factory
) -> None:
    monkeypatch.setattr(db_session, "session_factory", retention_session_factory)

    async def read_rows() -> list[LogEntry]:
        async with retention_session_factory() as session:
            return list((await session.scalars(select(LogEntry).order_by(LogEntry.id))).all())

    async def scenario() -> list[LogEntry]:
        hub = LogHub(batch_size=2, flush_interval=0.04)
        await hub.start()
        hub.offer(_record("batch one"))
        hub.offer(_record("batch two"))
        await asyncio.sleep(0.03)
        assert len(await read_rows()) == 2  # full batch path

        hub.offer(_record("interval"))
        await asyncio.sleep(0.08)
        assert len(await read_rows()) == 3  # low-volume timeout path

        hub.offer(_record("close flush"))
        await hub.aclose()
        return await read_rows()

    rows = asyncio.run(scenario())
    assert [row.content for row in rows] == [
        "batch one",
        "batch two",
        "interval",
        "close flush",
    ]
    assert [row.source for row in rows] == [LogSource.SERVER] * 4
    assert [row.level for row in rows] == [LogLevel.INFO] * 4
    assert [row.id for row in rows] == [1, 2, 3, 4]


def test_handler_masks_token_and_swallows_hub_failure(capsys: pytest.CaptureFixture[str]) -> None:
    hub = LogHub()
    handler = LogHubHandler(hub)
    event = logging.LogRecord(
        "maa_api", logging.INFO, __file__, 1, "GET /?token=secret&x=1", (), None
    )
    handler.emit(event)
    rows, _ = hub.snapshot_after(0)
    assert len(rows) == 1
    assert rows[0].content == "GET /?token=***&x=1"
    assert rows[0].logger == "maa_api"

    class BrokenHub:
        def offer(self, _record: LogRecord) -> None:
            raise RuntimeError("sink unavailable")

    LogHubHandler(BrokenHub()).emit(event)  # type: ignore[arg-type]
    assert "RuntimeError: sink unavailable" in capsys.readouterr().err


def test_attach_log_hub_is_idempotent_and_global_accessors() -> None:
    logger = logging.getLogger(f"maa_api.test_log_hub.{id(threading.current_thread())}")
    hub = LogHub()
    original_handlers = list(logger.handlers)
    try:
        first = attach_log_hub(hub, (logger.name,))
        second = attach_log_hub(hub, (logger.name,))
        assert first is second
        assert sum(isinstance(h, LogHubHandler) and h.hub is hub for h in logger.handlers) == 1
        set_log_hub(hub)
        assert get_log_hub() is hub
        set_log_hub(None)
        assert get_log_hub() is None
    finally:
        for handler in list(logger.handlers):
            if handler not in original_handlers:
                logger.removeHandler(handler)


@pytest.mark.parametrize(
    ("source", "expected"),
    [
        ("GET /api/ws?token=abc123&last_seen_id=5", "GET /api/ws?token=***&last_seen_id=5"),
        ("ordinary text with token=abc123", "ordinary text with token=abc123"),
        ("GET /?TOKEN=secret", "GET /?TOKEN=***"),
    ],
)
def test_mask_token_only_redacts_query_token(source: str, expected: str) -> None:
    assert mask_token(source) == expected
