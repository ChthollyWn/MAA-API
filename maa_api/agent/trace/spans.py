"""运行追踪的 span 记录器（M68 规格 §4.2.1、§4.5.1）。

``SpanRecorder`` 是追踪写入的唯一入口：运行的关键路径上只把结束的 span 放进内存
队列，由后台任务按批写库。三条语义写死在这里：

- :meth:`SpanRecorder.record` 是**同步**方法、不 await 写库；队列满或追踪关闭时
  只丢弃并计数，绝不把调用方拖进数据库。
- 写库失败的批次按 ``MAX_WRITE_ATTEMPTS`` 重试，仍失败则丢弃并计数；失败只写日志，
  不抛给调用方。
- ``close()`` 先尽力冲刷队列（最多 ``CLOSE_TIMEOUT_SECONDS``）再停后台任务。

span 只存元数据：消息正文、推理内容、工具参数与结果都不进属性（10 §2.3）。属性
清洗在 :func:`clean_attributes` 里，执行顺序与文档一致：去 ``None``、转允许的类型、
截断字符串、按插入顺序最多留 ``MAX_ATTRIBUTES`` 个键。
"""

from __future__ import annotations

import asyncio
import contextvars
import logging
import secrets
import time
import uuid
from collections import deque
from collections.abc import Callable, Coroutine, Mapping, Sequence
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from typing import Any, Literal

from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from maa_api.db.models import TraceSpan, utcnow
from maa_api.db.repositories.trace import TraceSpanRepository
from maa_api.db.session import run_with_busy_retry
from maa_api.domain.enums import SpanKind
from maa_api.settings import Settings, get_settings

__all__ = [
    "CLOSE_TIMEOUT_SECONDS",
    "DELEGATE_TOOLS",
    "FLUSH_BATCH_SIZE",
    "FLUSH_INTERVAL_SECONDS",
    "MAX_ATTRIBUTES",
    "MAX_ATTRIBUTE_CHARS",
    "MAX_LIST_ITEMS",
    "MAX_PENDING_SPANS",
    "MAX_WRITE_ATTEMPTS",
    "AttributeValue",
    "SpanHandle",
    "SpanRecorder",
    "SpanRecord",
    "SpanStatus",
    "clean_attributes",
    "current_span",
    "new_span_id",
    "reset_span",
    "use_span",
]

logger = logging.getLogger(__name__)

#: 后台写库任务多久醒一次（秒）。攒满一批时由唤醒事件提前叫醒，不等这个间隔。
FLUSH_INTERVAL_SECONDS = 0.5
#: 一次事务最多写多少条 span。太大长时间持 SQLite 写锁，太小写库开销占比高。
FLUSH_BATCH_SIZE = 200
#: 内存队列上限。运行关键路径不因数据库慢而无限占内存：满了丢弃新来的并计数。
MAX_PENDING_SPANS = 10_000
#: 同一批 span 最多写几次（含第一次）。仍失败就丢弃，计入 ``dropped``。
MAX_WRITE_ATTEMPTS = 3
#: ``close()`` 冲刷队列的时间上限（秒）。
CLOSE_TIMEOUT_SECONDS = 5.0

#: 单个 span 最多带多少属性；超出的按写入顺序丢弃。
MAX_ATTRIBUTES = 48
#: 字符串属性的最大字符数；超出的截成 ``MAX_ATTRIBUTE_CHARS - 1`` 字加「…」。
MAX_ATTRIBUTE_CHARS = 256
#: 列表属性最多保留多少项。
MAX_LIST_ITEMS = 16

#: 记录成 ``delegate.subagent`` 的工具名（10 §2.2）。
DELEGATE_TOOLS = frozenset({"delegate", "delegate_recon"})

SpanStatus = Literal["ok", "error"]
AttributeValue = str | int | float | bool | list[str]


def new_span_id() -> str:
    """16 位小写十六进制 span id（10 §2.1）。"""
    return secrets.token_hex(8)


def _truncate(value: str) -> str:
    if len(value) <= MAX_ATTRIBUTE_CHARS:
        return value
    return value[: MAX_ATTRIBUTE_CHARS - 1] + "…"


def _is_sequence(value: Any) -> bool:
    """列表类容器；``str`` / ``bytes`` / ``Mapping`` 不按序列展开。"""
    return isinstance(value, (list, tuple, set, frozenset))


def _clean_value(value: Any) -> AttributeValue | None:
    """把一个属性值转成允许的形状；``None`` 表示不写。"""
    if value is None:
        return None
    if isinstance(value, str):
        return _truncate(value)
    # ``bool`` 在 ``int`` 之前判断：``True`` 要留布尔，不能变成 1。
    if isinstance(value, bool):
        return value
    if isinstance(value, int):
        return value
    if isinstance(value, float):
        return value
    if _is_sequence(value):
        items: list[str] = []
        for item in value:
            if item is None:
                continue
            text = item if isinstance(item, str) else str(item)
            items.append(_truncate(text))
            if len(items) >= MAX_LIST_ITEMS:
                break
        return items
    # 其他标量（枚举、路径、UUID 等）统一转字符串；容器也兜底成字符串，
    # 真正的容器内容不该出现在 span 里，但出现了也不能让清洗抛错。
    return _truncate(str(value))


def clean_attributes(values: Mapping[str, Any]) -> dict[str, AttributeValue]:
    """按 10 §2.3 清洗一次写入的属性。

    - 去掉值为 ``None`` 的键；
    - ``bool`` / ``int`` / ``float`` / ``str`` 原样，其他标量转字符串；
    - 序列转 ``list[str]`` 并只取前 :data:`MAX_LIST_ITEMS` 项；
    - 字符串超过 :data:`MAX_ATTRIBUTE_CHARS` 时截成 255 字加「…」；
    - 超过 :data:`MAX_ATTRIBUTES` 个键时按插入顺序丢弃其余的。
    """
    cleaned: dict[str, AttributeValue] = {}
    for key, value in values.items():
        if value is None:
            continue
        if len(cleaned) >= MAX_ATTRIBUTES:
            break
        converted = _clean_value(value)
        if converted is None:
            continue
        cleaned[str(key)] = converted
    return cleaned


def _merge_attributes(
    target: dict[str, AttributeValue], values: Mapping[str, Any]
) -> None:
    """把清洗后的属性并进 ``target``，并保持「最多 48 个、按插入顺序」的约束。"""
    for key, value in clean_attributes(values).items():
        target[key] = value
    if len(target) > MAX_ATTRIBUTES:
        trimmed = list(target.items())[:MAX_ATTRIBUTES]
        target.clear()
        target.update(trimmed)


def _naive_utc(moment: datetime) -> datetime:
    """带时区的时间换成 naive UTC；naive 时间按约定已经是 UTC，原样返回。"""
    if moment.tzinfo is None:
        return moment
    return moment.astimezone(UTC).replace(tzinfo=None)


def _duration_ms(started_at: datetime, ended_at: datetime) -> int:
    return max(0, int((ended_at - started_at).total_seconds() * 1000))


@dataclass(slots=True)
class SpanRecord:
    """一个已经结束、准备写库的 span；时间都是 naive UTC。"""

    trace_id: str
    span_id: str
    parent_span_id: str | None
    run_id: str | None
    name: str
    kind: SpanKind
    status: SpanStatus
    attributes: dict[str, AttributeValue]
    started_at: datetime
    ended_at: datetime
    duration_ms: int


class SpanHandle:
    """进行中的 span。``recorder`` 为 ``None`` 时是空操作句柄（追踪关闭时返回它）。

    句柄本身不落库；:meth:`end` 把属性、状态与时长交给 recorder 入队。``end()``
    幂等：第二次调用不做任何事。
    """

    __slots__ = (
        "_attributes",
        "_ended",
        "_recorder",
        "_started_monotonic",
        "kind",
        "name",
        "parent_span_id",
        "run_id",
        "span_id",
        "started_at",
        "trace_id",
    )

    def __init__(
        self,
        recorder: SpanRecorder | None,
        *,
        trace_id: str,
        span_id: str,
        parent_span_id: str | None,
        run_id: str | None,
        name: str,
        kind: SpanKind,
        started_at: datetime,
        attributes: Mapping[str, Any] | None = None,
    ) -> None:
        self._recorder = recorder
        self.trace_id = trace_id
        self.span_id = span_id
        self.parent_span_id = parent_span_id
        self.run_id = run_id
        self.name = name
        self.kind = kind
        self.started_at = _naive_utc(started_at)
        self._attributes: dict[str, AttributeValue] = {}
        _merge_attributes(self._attributes, attributes or {})
        self._started_monotonic = time.monotonic()
        self._ended = False

    @property
    def ended(self) -> bool:
        return self._ended

    def set_attribute(self, key: str, value: Any) -> None:
        """追加/覆盖一个属性；已结束的 span 不再接受修改。"""
        self.set_attributes({key: value})

    def set_attributes(self, values: Mapping[str, Any]) -> None:
        if self._ended:
            return
        _merge_attributes(self._attributes, values)

    def end(
        self,
        *,
        status: SpanStatus = "ok",
        attributes: Mapping[str, Any] | None = None,
    ) -> None:
        """结束 span 并交给 recorder；重复调用无效。"""
        if self._ended:
            return
        self._ended = True
        if attributes:
            _merge_attributes(self._attributes, attributes)
        duration = max(0, int((time.monotonic() - self._started_monotonic) * 1000))
        record = SpanRecord(
            trace_id=self.trace_id,
            span_id=self.span_id,
            parent_span_id=self.parent_span_id,
            run_id=self.run_id,
            name=self.name,
            kind=self.kind,
            status=status,
            attributes=dict(self._attributes),
            started_at=self.started_at,
            ended_at=self.started_at + timedelta(milliseconds=duration),
            duration_ms=duration,
        )
        recorder = self._recorder
        if recorder is None:
            return
        try:
            recorder.record(record)
        except Exception:  # noqa: BLE001 - 记录 span 不能影响调用方
            logger.warning("span 入队失败：%s", self.name, exc_info=True)


@dataclass(slots=True)
class _Pending:
    """队列里的一条：重试次数由 recorder 维护，不写进 :class:`SpanRecord`。"""

    record: SpanRecord
    attempts: int = 0


_CURRENT_SPAN: contextvars.ContextVar[SpanHandle | None] = contextvars.ContextVar(
    "maa_current_span", default=None
)


def current_span() -> SpanHandle | None:
    """当前 asyncio 上下文里正在进行的 span；没有时为 ``None``。"""
    return _CURRENT_SPAN.get()


def use_span(handle: SpanHandle) -> contextvars.Token:
    """把 ``handle`` 设为当前 span，返回交给 :func:`reset_span` 的 token。"""
    return _CURRENT_SPAN.set(handle)


def reset_span(token: contextvars.Token) -> None:
    """复原 :func:`use_span` 之前的当前 span。"""
    _CURRENT_SPAN.reset(token)


def _validate_trace_id(trace_id: str) -> None:
    """``trace_id`` 必须是 36 位 UUID 字符串（与 ``agent_run.root_run_id`` 同形）。"""
    if not isinstance(trace_id, str) or len(trace_id) != 36:
        raise ValueError(f"trace_id 必须是 36 位 UUID 字符串：{trace_id!r}")
    try:
        uuid.UUID(trace_id)
    except (ValueError, AttributeError, TypeError) as exc:
        raise ValueError(f"trace_id 必须是 UUID 字符串：{trace_id!r}") from exc


def _to_row(record: SpanRecord) -> TraceSpan:
    return TraceSpan(
        trace_id=record.trace_id,
        span_id=record.span_id,
        parent_span_id=record.parent_span_id,
        run_id=record.run_id,
        name=record.name,
        kind=str(record.kind),
        status=record.status,
        attributes=dict(record.attributes),
        started_at=record.started_at,
        ended_at=record.ended_at,
        duration_ms=record.duration_ms,
    )


class SpanRecorder:
    """span 的内存队列与后台批量写入（§4.5.1）。

    ``enabled`` 每次现读 ``settings.trace.enabled``，所以热改设置立即生效；关闭时
    :meth:`start_span` 返回空操作句柄、:meth:`record` 直接丢弃。
    """

    def __init__(
        self,
        session_factory: async_sessionmaker[AsyncSession],
        *,
        settings_provider: Callable[[], Settings] = get_settings,
        exporter: Any | None = None,
        flush_interval: float = FLUSH_INTERVAL_SECONDS,
        batch_size: int = FLUSH_BATCH_SIZE,
        max_pending: int = MAX_PENDING_SPANS,
    ) -> None:
        if flush_interval <= 0:
            raise ValueError("flush_interval 必须为正数")
        if batch_size < 1:
            raise ValueError("batch_size 必须为正整数")
        if max_pending < 1:
            raise ValueError("max_pending 必须为正整数")
        self._session_factory = session_factory
        self._settings_provider = settings_provider
        self._exporter = exporter
        self._flush_interval = flush_interval
        self._batch_size = batch_size
        self._max_pending = max_pending
        self._pending: deque[_Pending] = deque()
        self._export_pending: deque[_Pending] = deque()
        self._written = 0
        self._dropped = 0
        self._write_errors = 0
        self._exported = 0
        self._export_dropped = 0
        self._wake = asyncio.Event()
        self._idle = asyncio.Event()
        self._idle.set()
        self._export_wake = asyncio.Event()
        self._writer_task: asyncio.Task[None] | None = None
        self._export_task: asyncio.Task[None] | None = None
        self._spawned: set[asyncio.Task[None]] = set()
        self._closed = False
        self._last_write_warning: float | None = None

    # ------------------------------------------------------------------
    # 状态与入队
    # ------------------------------------------------------------------
    @property
    def enabled(self) -> bool:
        """追踪是否开启；设置读取失败时按关闭处理（记录器不能拖垮运行）。"""
        try:
            return bool(self._settings_provider().trace.enabled)
        except Exception:  # noqa: BLE001 - 设置异常不能阻塞运行
            logger.warning("读取追踪设置失败，按关闭处理", exc_info=True)
            return False

    @property
    def closed(self) -> bool:
        return self._closed

    def start_span(
        self,
        name: str,
        kind: SpanKind,
        *,
        trace_id: str,
        run_id: str | None,
        parent: SpanHandle | None = None,
        attributes: Mapping[str, Any] | None = None,
    ) -> SpanHandle:
        """开始一个进行中的 span；追踪关闭时返回空操作句柄。

        ``trace_id`` 必须是 36 位 UUID 字符串：不合规属于调用方的编程错误，
        在源头就抛 ``ValueError``，不让它进数据库再变成重试与丢弃。
        """
        _validate_trace_id(trace_id)
        recorder = self if self.enabled and not self._closed else None
        return SpanHandle(
            recorder,
            trace_id=trace_id,
            span_id=new_span_id(),
            parent_span_id=parent.span_id if parent is not None else None,
            run_id=run_id,
            name=name,
            kind=kind,
            started_at=utcnow(),
            attributes=attributes,
        )

    def record_span(
        self,
        name: str,
        kind: SpanKind,
        *,
        trace_id: str,
        run_id: str | None,
        parent_span_id: str | None,
        started_at: datetime,
        ended_at: datetime,
        status: SpanStatus = "ok",
        attributes: Mapping[str, Any] | None = None,
    ) -> None:
        """直接入队一条已经结束的 span（工具调用后派生的 ``confirmation.wait``）。"""
        started = _naive_utc(started_at)
        ended = _naive_utc(ended_at)
        if ended < started:
            ended = started
        self.record(
            SpanRecord(
                trace_id=trace_id,
                span_id=new_span_id(),
                parent_span_id=parent_span_id,
                run_id=run_id,
                name=name,
                kind=kind,
                status=status,
                attributes=clean_attributes(attributes or {}),
                started_at=started,
                ended_at=ended,
                duration_ms=_duration_ms(started, ended),
            )
        )

    def record(self, record: SpanRecord) -> None:
        """同步入队；队列满、追踪关闭或已关闭时丢弃并计数，绝不 await 写库。"""
        if self._closed:
            self._dropped += 1
            return
        if not self.enabled:
            return
        if len(self._pending) >= self._max_pending:
            self._dropped += 1
            return
        self._pending.append(_Pending(record))
        self._idle.clear()
        if len(self._pending) >= self._batch_size:
            self._wake.set()

    def spawn(self, coro: Coroutine[Any, Any, None]) -> None:
        """派生一个后台小任务（例如按审计派生确认 span），异常只记日志。"""
        task = asyncio.create_task(coro)
        self._spawned.add(task)
        task.add_done_callback(self._forget_task)

    def _forget_task(self, task: asyncio.Task[None]) -> None:
        self._spawned.discard(task)
        if task.cancelled():
            return
        try:
            error = task.exception()
        except Exception:  # noqa: BLE001 - 取 exception 本身失败不该炸回调
            return
        if error is not None:
            logger.warning("追踪后台任务失败", exc_info=error)

    # ------------------------------------------------------------------
    # 后台任务
    # ------------------------------------------------------------------
    async def start(self) -> None:
        """起写库任务与导出任务；重复调用无副作用。"""
        if self._closed:
            return
        if self._writer_task is None or self._writer_task.done():
            self._writer_task = asyncio.create_task(
                self._writer_loop(), name="span-recorder-writer"
            )
        if self._exporter is not None and (
            self._export_task is None or self._export_task.done()
        ):
            self._export_task = asyncio.create_task(
                self._export_loop(), name="span-recorder-exporter"
            )

    async def flush(self) -> None:
        """立即写完队列（测试与评测收集结果前用）。"""
        if self._closed:
            return
        if self._writer_task is None or self._writer_task.done():
            await self._drain()
            return
        self._wake.set()
        await self._idle.wait()

    async def close(self) -> None:
        """冲刷队列（最多 :data:`CLOSE_TIMEOUT_SECONDS`）后停后台任务与 exporter。

        重复调用无副作用；关闭之后 :meth:`record` 只丢弃并计数。
        """
        if self._closed:
            return
        # 先给已经派生的后台任务一点时间把 span 入队（确认等待的派生查询）。
        if self._spawned:
            try:
                await asyncio.wait_for(
                    asyncio.gather(*tuple(self._spawned), return_exceptions=True),
                    timeout=CLOSE_TIMEOUT_SECONDS,
                )
            except TimeoutError:
                logger.warning("追踪收尾：派生任务未在时限内结束")
        try:
            await asyncio.wait_for(self.flush(), timeout=CLOSE_TIMEOUT_SECONDS)
        except TimeoutError:
            logger.warning(
                "追踪收尾：队列未在 %.1fs 内写完，剩余 %d 条 span 丢弃",
                CLOSE_TIMEOUT_SECONDS,
                len(self._pending),
            )
        except asyncio.CancelledError:
            raise
        except Exception:  # noqa: BLE001 - 关闭路径不能抛
            logger.warning("追踪收尾：冲刷队列失败", exc_info=True)
        self._closed = True
        self._wake.set()
        for task in (self._writer_task, self._export_task):
            if task is not None and not task.done():
                task.cancel()
                await asyncio.gather(task, return_exceptions=True)
        self._writer_task = None
        self._export_task = None
        self._dropped += len(self._pending)
        self._pending.clear()
        for task in tuple(self._spawned):
            if not task.done():
                task.cancel()
        if self._spawned:
            await asyncio.gather(*tuple(self._spawned), return_exceptions=True)
        if self._exporter is not None:
            try:
                await asyncio.wait_for(self._drain_exports(), timeout=1.0)
            except TimeoutError:
                logger.warning("追踪收尾：导出队列未在时限内写完")
            except asyncio.CancelledError:
                raise
            except Exception:  # noqa: BLE001 - 关闭路径不能抛
                logger.warning("追踪收尾：冲刷导出队列失败", exc_info=True)
            self._export_dropped += len(self._export_pending)
            self._export_pending.clear()
            try:
                await asyncio.to_thread(self._exporter.shutdown)
            except Exception:  # noqa: BLE001 - 关闭路径不能抛
                logger.warning("OTLP exporter 关闭失败", exc_info=True)

    def stats(self) -> dict[str, int]:
        """当前计数：``pending`` / ``written`` / ``dropped`` / ``write_errors`` /
        ``exported`` / ``export_dropped``。"""
        return {
            "pending": len(self._pending),
            "written": self._written,
            "dropped": self._dropped,
            "write_errors": self._write_errors,
            "exported": self._exported,
            "export_dropped": self._export_dropped,
        }

    async def _writer_loop(self) -> None:
        while True:
            try:
                await asyncio.wait_for(
                    self._wake.wait(), timeout=self._flush_interval
                )
            except TimeoutError:
                pass
            self._wake.clear()
            try:
                await self._drain()
            except asyncio.CancelledError:
                raise
            except Exception:  # noqa: BLE001 - 后台任务不能静默退出
                logger.warning("span 写库循环异常", exc_info=True)

    async def _drain(self) -> None:
        """尽量写完队列；失败的批次按尝试次数重试或丢弃。"""
        while self._pending:
            batch = [self._pending.popleft() for _ in range(
                min(self._batch_size, len(self._pending))
            )]
            await self._write_batch(batch)
        self._idle.set()

    async def _write_batch(self, batch: Sequence[_Pending]) -> bool:
        rows = [_to_row(item.record) for item in batch]
        try:
            async with self._session_factory() as session:

                async def operation() -> None:
                    await TraceSpanRepository(session).add_many(rows)

                await run_with_busy_retry(session, operation)
                await session.commit()
        except asyncio.CancelledError:
            self._requeue(batch)
            raise
        except Exception:  # noqa: BLE001 - 写库失败按重试/丢弃处理
            self._write_errors += 1
            retry = self._requeue(batch)
            self._dropped += len(batch) - len(retry)
            self._warn_write_failure()
            return False
        self._written += len(batch)
        if self._exporter is not None:
            self._enqueue_export([item.record for item in batch])
        return True

    def _requeue(self, batch: Sequence[_Pending]) -> list[_Pending]:
        """尝试次数 +1，未到上限的放回队首（保持原顺序）；返回仍要重试的条目。"""
        retry: list[_Pending] = []
        for item in batch:
            item.attempts += 1
            if item.attempts < MAX_WRITE_ATTEMPTS:
                retry.append(item)
        for item in reversed(retry):
            self._pending.appendleft(item)
        return retry

    def _warn_write_failure(self) -> None:
        """写库失败一分钟最多一条 warning（重试与丢弃各自已经计数）。"""
        now = time.monotonic()
        if (
            self._last_write_warning is not None
            and now - self._last_write_warning < 60
        ):
            return
        self._last_write_warning = now
        logger.warning(
            "span 写库失败（已尝试 %d 次），详见 trace 记录器的计数",
            MAX_WRITE_ATTEMPTS,
        )

    # ------------------------------------------------------------------
    # 导出队列（T7 接 OTLP；这里只留接口）
    # ------------------------------------------------------------------
    def _enqueue_export(self, records: Sequence[SpanRecord]) -> None:
        for record in records:
            if len(self._export_pending) >= self._max_pending:
                self._export_pending.popleft()
                self._export_dropped += 1
            self._export_pending.append(_Pending(record))
        if self._export_pending:
            self._export_wake.set()

    async def _export_loop(self) -> None:
        while True:
            try:
                await asyncio.wait_for(
                    self._export_wake.wait(), timeout=self._flush_interval
                )
            except TimeoutError:
                pass
            self._export_wake.clear()
            try:
                await self._drain_exports()
            except asyncio.CancelledError:
                raise
            except Exception:  # noqa: BLE001 - 导出任务不能静默退出
                logger.warning("span 导出循环异常", exc_info=True)

    async def _drain_exports(self) -> None:
        """导出队列：一批最多一次重试，第二次失败丢弃并计数。"""
        exporter = self._exporter
        if exporter is None:
            return
        while self._export_pending:
            batch = [
                self._export_pending.popleft()
                for _ in range(min(self._batch_size, len(self._export_pending)))
            ]
            try:
                ok = await asyncio.to_thread(
                    exporter.export, [item.record for item in batch]
                )
            except asyncio.CancelledError:
                for item in reversed(batch):
                    self._export_pending.appendleft(item)
                raise
            except Exception:  # noqa: BLE001 - 导出失败只计数
                logger.warning("span 导出失败", exc_info=True)
                ok = False
            if ok:
                self._exported += len(batch)
                continue
            retry = [item for item in batch if item.attempts < 1]
            for item in retry:
                item.attempts += 1
            for item in reversed(retry):
                self._export_pending.appendleft(item)
            self._export_dropped += len(batch) - len(retry)
