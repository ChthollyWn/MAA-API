"""Thread-safe convergence point for task, service, and MaaCore debug logs.

The synchronous ``offer`` path only assigns an id, appends to a bounded deque,
and schedules work on the owning event loop. Database writes and sink fan-out
always happen on that loop, never on a logging, IPC, or file-tailer thread.
"""

from __future__ import annotations

import asyncio
import contextvars
import functools
import logging
import re
import sys
import threading
import time
import uuid
from collections import deque
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Any, Coroutine, Protocol, TypeVar

from sqlalchemy import func, select

from maa_api.db.models import LogEntry
from maa_api.domain.enums import LogLevel, LogSource
from maa_api.settings import get_settings

__all__ = [
    "LEVEL_ORDER",
    "SOURCE_DB_TO_WIRE",
    "SOURCE_WIRE_TO_DB",
    "TARGET_LOGGERS",
    "LogHub",
    "LogHubHandler",
    "LogRecord",
    "attach_log_hub",
    "current_pipeline_id",
    "current_request_id",
    "create_task_without_request_id",
    "get_log_hub",
    "mask_token",
    "set_log_hub",
]


SOURCE_WIRE_TO_DB: dict[str, LogSource] = {
    "task": LogSource.MAA_TASK,
    "service": LogSource.SERVER,
    "core": LogSource.MAACORE_DEBUG,
}
SOURCE_DB_TO_WIRE: dict[LogSource, str] = {
    value: key for key, value in SOURCE_WIRE_TO_DB.items()
}
LEVEL_ORDER: dict[str, int] = {
    "DEBUG": logging.DEBUG,
    "INFO": logging.INFO,
    "WARNING": logging.WARNING,
    "ERROR": logging.ERROR,
    "CRITICAL": logging.CRITICAL,
}
TARGET_LOGGERS = ("maa_api", "uvicorn", "uvicorn.access", "uvicorn.error")

# A query parameter named ``token`` is a credential even when it appears in
# an access-log URL embedded in otherwise ordinary prose.
_TOKEN_QUERY_RE = re.compile(r"(?i)([?&]token=)[^&#\s]+")
_STOP = object()


@dataclass(slots=True, kw_only=True)
class LogRecord:
    """A normalized log event shared by the hub, WebSocket, and REST APIs."""

    id: int = 0
    stream_id: str | None = None
    ts: float
    source: str
    level: str
    content: str
    pipeline_id: str | None = None
    task_id: str | None = None
    request_id: str | None = None
    logger: str | None = None
    raw: dict[str, Any] | None = None
    attachment: dict[str, Any] | None = None


class LogSink(Protocol):
    """A non-blocking recipient, such as a per-WebSocket send queue."""

    def put_log(self, record: LogRecord) -> None: ...


def mask_token(text: str) -> str:
    """Replace values of ``token`` query parameters without altering other text."""

    return _TOKEN_QUERY_RE.sub(r"\1***", text)


current_pipeline_id: contextvars.ContextVar[str | None] = contextvars.ContextVar(
    "maa_api_current_pipeline_id", default=None
)
current_request_id: contextvars.ContextVar[str | None] = contextvars.ContextVar(
    "maa_api_current_request_id", default=None
)
_TaskResult = TypeVar("_TaskResult")


def create_task_without_request_id(
    coroutine: Coroutine[Any, Any, _TaskResult], *, name: str | None = None
) -> asyncio.Task[_TaskResult]:
    """Create a task with the current context except for request correlation."""
    context = contextvars.copy_context()
    context.run(current_request_id.set, None)
    return asyncio.create_task(coroutine, name=name, context=context)


class LogHub:
    """Bounded in-memory log stream with independent sinks and batched storage."""

    def __init__(
        self,
        ring_size: int | None = None,
        *,
        batch_size: int | None = None,
        flush_interval: float | None = None,
        db_queue_size: int = 10_000,
    ) -> None:
        configured = get_settings().log
        self.ring_size = max(int(ring_size or configured.ring_size), 1)
        self.batch_size = max(int(batch_size or configured.batch_size), 1)
        self.flush_interval = max(
            float(flush_interval or configured.flush_interval), 0.001
        )
        self._ring: deque[LogRecord] = deque(maxlen=self.ring_size)
        self.stream_id = uuid.uuid4().hex
        self._db_queue: asyncio.Queue[LogRecord | object] = asyncio.Queue(
            maxsize=max(int(db_queue_size), 1)
        )
        self._sequence_lock = threading.Lock()
        self._next_id = 1
        self._loop: asyncio.AbstractEventLoop | None = None
        self._loop_thread_id: int | None = None
        self._writer_task: asyncio.Task[None] | None = None
        self._sinks: list[LogSink] = []
        self._started = False
        self._closing = False
        self._dropped_since_notice = 0
        self._last_drop_notice = 0.0

    async def start(self) -> None:
        """Bind to the current loop, resume ids after persisted rows, and start writer."""

        if self._started:
            return
        self._loop = asyncio.get_running_loop()
        self._loop_thread_id = threading.get_ident()

        # Resolve the factory at call time: test fixtures and application setup
        # replace this module attribute after importing db.session.
        from maa_api.db import session as db_session

        async with db_session.session_factory() as session:
            last_id = await session.scalar(select(func.max(LogEntry.id)))
        self._next_id = int(last_id or 0) + 1
        self._writer_task = asyncio.create_task(
            self._db_writer(), name="maa-api-log-db-writer"
        )
        self._started = True

    async def aclose(self) -> None:
        """Stop accepting persistence work and flush all records already queued."""

        if not self._started or self._closing:
            return
        self._closing = True
        writer = self._writer_task
        if writer is not None:
            # A sentinel follows all queued records. If the bounded queue is
            # full, awaiting space is safe here because the writer keeps draining.
            await self._db_queue.put(_STOP)
            await writer
        self._writer_task = None
        self._started = False

    def offer(self, record: LogRecord) -> None:
        """Publish a record from any thread without waiting or performing I/O."""

        self._offer(record, persist=True)

    def emit_internal(self, content: str, level: str = "WARNING") -> None:
        """Publish a hub diagnostic to memory and sinks, bypassing database queue."""

        self._offer(
            LogRecord(
                ts=time.time(),
                source="service",
                level=_normalise_level(level),
                content=mask_token(content),
                logger="maa_api.services.log_hub",
            ),
            persist=False,
        )

    def snapshot_after(self, last_seen_id: int) -> tuple[list[LogRecord], bool]:
        """Return retained rows newer than the cursor and whether earlier rows fell out."""

        cursor = int(last_seen_id)
        with self._sequence_lock:
            records = list(self._ring)
        if not records:
            return [], False
        oldest_id = records[0].id
        if cursor > records[-1].id:
            # A cursor beyond this stream can only come from an older/reused id
            # space or a malformed client checkpoint. Replay what remains and
            # signal the gap instead of silently suppressing every current row.
            return records, True
        truncated = cursor < oldest_id - 1
        return [record for record in records if record.id > cursor], truncated

    def add_sink(self, sink: LogSink) -> None:
        """Add a fan-out sink once."""

        if sink not in self._sinks:
            self._sinks.append(sink)

    def remove_sink(self, sink: LogSink) -> None:
        """Remove a previously registered fan-out sink."""

        try:
            self._sinks.remove(sink)
        except ValueError:
            pass

    def _offer(self, record: LogRecord, *, persist: bool) -> None:
        loop = self._loop
        with self._sequence_lock:
            record.id = self._next_id
            record.stream_id = self.stream_id
            self._next_id += 1
            if record.ts <= 0:
                record.ts = time.time()
            record.level = _normalise_level(record.level)
            record.content = mask_token(record.content)
            if record.pipeline_id is None:
                record.pipeline_id = current_pipeline_id.get()
            if record.request_id is None:
                record.request_id = current_request_id.get()
            self._ring.append(record)

        if loop is None or loop.is_closed() or not self._started or self._closing:
            return
        if threading.get_ident() == self._loop_thread_id:
            self._dispatch(record, persist=persist)
            return
        try:
            loop.call_soon_threadsafe(
                functools.partial(self._dispatch, record, persist=persist)
            )
        except RuntimeError as exc:
            # The loop may close between is_closed() and scheduling during shutdown.
            print(f"[LogHub] event loop is unavailable: {exc}", file=sys.stderr)

    def _dispatch(self, record: LogRecord, *, persist: bool) -> None:
        """Run sink fan-out and queue admission on the event-loop thread."""

        for sink in tuple(self._sinks):
            try:
                sink.put_log(record)
            except Exception as exc:  # sink failure must not break other sinks
                print(f"[LogHub] sink failed: {exc!r}", file=sys.stderr)
        if persist and self._should_persist(record):
            self._enqueue_db(record)

    def _should_persist(self, record: LogRecord) -> bool:
        if record.source not in SOURCE_WIRE_TO_DB:
            return False
        level = LEVEL_ORDER.get(record.level, logging.INFO)
        if record.source == "core":
            minimum = _configured_level(get_settings().log.persist_maacore_debug_level)
            return level >= minimum
        if record.source == "service" and record.logger == "uvicorn.access":
            return level >= logging.WARNING
        return True

    def _enqueue_db(self, record: LogRecord) -> None:
        """Apply level-aware backpressure. Called only on the owning event loop."""

        capacity = self._db_queue.maxsize
        queued = self._db_queue.qsize()
        level = LEVEL_ORDER.get(record.level, logging.INFO)
        ratio = queued / capacity if capacity else 0.0

        if queued >= capacity:
            self._record_drop()
            return
        if ratio >= 0.90 and level < logging.WARNING:
            self._record_drop()
            return
        if ratio >= 0.70 and level < logging.INFO:
            self._record_drop()
            return
        try:
            self._db_queue.put_nowait(record)
        except asyncio.QueueFull:
            self._record_drop()

    def _record_drop(self) -> None:
        self._dropped_since_notice += 1
        if self._dropped_since_notice < 1000:
            return
        now = time.monotonic()
        if now - self._last_drop_notice < 1.0:
            return
        count = self._dropped_since_notice
        self._dropped_since_notice = 0
        self._last_drop_notice = now
        self.emit_internal(f"日志洪峰，已丢弃 {count} 条低级别日志")

    async def _db_writer(self) -> None:
        batch: list[LogRecord] = []
        while True:
            try:
                item = await asyncio.wait_for(
                    self._db_queue.get(),
                    timeout=self.flush_interval if batch else None,
                )
            except TimeoutError:
                await self._flush_safely(batch)
                batch = []
                continue

            if item is _STOP:
                self._db_queue.task_done()
                if batch:
                    await self._flush_safely(batch)
                return

            assert isinstance(item, LogRecord)
            batch.append(item)
            self._db_queue.task_done()
            if len(batch) >= self.batch_size:
                await self._flush_safely(batch)
                batch = []

    async def _flush_safely(self, batch: list[LogRecord]) -> None:
        if not batch:
            return
        try:
            await self._flush(batch)
        except Exception as exc:
            # Do not feed database errors back into logging; that would recurse.
            print(f"[LogHub] database flush failed: {exc!r}", file=sys.stderr)

    async def _flush(self, records: list[LogRecord]) -> None:
        from maa_api.db import session as db_session
        from maa_api.db.models import Task
        from maa_api.db.repositories.log import LogRepository
        from maa_api.services.callback_statistics import CallbackStatisticsService

        entries = [_to_entry(record) for record in records]
        statistic_service = CallbackStatisticsService()
        async with db_session.session_factory() as session:
            statistic_rows = []
            for record in records:
                callback = (record.raw or {}).get("details")
                if not isinstance(callback, dict):
                    continue
                callback_data = callback.get("details")
                callback_data = callback_data if isinstance(callback_data, dict) else {}
                stage_data = callback_data.get("stage")
                stage_data = stage_data if isinstance(stage_data, dict) else {}
                stage_code = stage_data.get("stageCode")
                if (
                    callback.get("what") == "SanityBeforeStage"
                    and not stage_code
                    and record.task_id is not None
                ):
                    task = await session.get(Task, record.task_id)
                    if task is not None:
                        stage_code = (task.params or {}).get("stage")
                event_time = datetime.fromtimestamp(record.ts, timezone.utc).replace(
                    tzinfo=None
                )
                drops, sanity = statistic_service.rows_for(
                    callback,
                    message=(record.raw or {}).get("msg"),
                    created_at=event_time,
                    pipeline_id=record.pipeline_id,
                    task_id=record.task_id,
                    stage_code_fallback=(str(stage_code) if stage_code else None),
                )
                statistic_rows.extend(drops)
                statistic_rows.extend(sanity)
            await LogRepository(session).bulk_insert(
                entries, statistic_rows=statistic_rows
            )
        # Keep the in-memory cursor and SQLite's AUTOINCREMENT cursor aligned.
        # The writer is the only producer of log_entry rows in this service.
        for record, entry in zip(records, entries, strict=True):
            if entry.id is not None:
                record.id = int(entry.id)


def _normalise_level(level: str) -> str:
    value = str(level).upper()
    if value == "WARN":
        value = "WARNING"
    return value if value in LEVEL_ORDER else "INFO"


def _configured_level(level: str) -> int:
    value = _normalise_level(level)
    return LEVEL_ORDER[value]


def _to_entry(record: LogRecord) -> LogEntry:
    """Translate a wire record to its DB enums and source-specific metadata."""

    meta: dict[str, Any] = dict(record.raw or {})
    if record.logger:
        meta.setdefault("logger", record.logger)
    if record.attachment:
        meta["attachment"] = dict(record.attachment)
        screenshot_id = record.attachment.get("screenshot_id")
        if screenshot_id:
            meta.setdefault("screenshot_id", screenshot_id)
    created_at = datetime.fromtimestamp(record.ts, timezone.utc).replace(tzinfo=None)
    return LogEntry(
        id=record.id,
        source=SOURCE_WIRE_TO_DB[record.source],
        level=LogLevel(record.level.lower()),
        content=record.content,
        meta=meta or None,
        pipeline_id=record.pipeline_id,
        task_id=record.task_id,
        created_at=created_at,
    )


class LogHubHandler(logging.Handler):
    """Non-blocking stdlib logging bridge into a :class:`LogHub`."""

    def __init__(self, hub: LogHub) -> None:
        super().__init__(level=logging.DEBUG)
        self.hub = hub

    def emit(self, record: logging.LogRecord) -> None:
        try:
            self.hub.offer(
                LogRecord(
                    ts=record.created,
                    source="service",
                    level=record.levelname,
                    content=self.format(record),
                    pipeline_id=getattr(
                        record, "pipeline_id", current_pipeline_id.get()
                    ),
                    task_id=getattr(record, "task_id", None),
                    request_id=getattr(record, "request_id", current_request_id.get()),
                    logger=record.name,
                )
            )
        except Exception:
            # logging.Handler.handleError avoids propagating failures into the
            # business operation that happened to emit the record.
            self.handleError(record)


_attach_lock = threading.Lock()
_current_hub: LogHub | None = None
_current_hub_lock = threading.Lock()


def attach_log_hub(
    hub: LogHub, loggers: tuple[str, ...] = TARGET_LOGGERS
) -> LogHubHandler:
    """Attach one shared handler to configured logger names, idempotently."""

    with _attach_lock:
        handler = getattr(hub, "_logging_handler", None)
        if handler is None:
            handler = LogHubHandler(hub)
            handler.setFormatter(logging.Formatter("%(message)s"))
            hub._logging_handler = handler
        for logger_or_name in loggers:
            logger = (
                logger_or_name
                if isinstance(logger_or_name, logging.Logger)
                else logging.getLogger(logger_or_name)
            )
            existing = next(
                (
                    h
                    for h in logger.handlers
                    if isinstance(h, LogHubHandler) and h.hub is hub
                ),
                None,
            )
            if existing is None:
                logger.addHandler(handler)
            elif existing is not handler:
                logger.removeHandler(existing)
                logger.addHandler(handler)
        return handler


def get_log_hub() -> LogHub | None:
    with _current_hub_lock:
        return _current_hub


def set_log_hub(hub: LogHub | None) -> None:
    global _current_hub
    with _current_hub_lock:
        _current_hub = hub
