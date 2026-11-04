"""SpanRecorder 的队列、批量写库、重试与关闭（M68 规格 §4.2.1、§4.5.1）。

这些用例不起 lifespan：临时库直接构造 ``SpanRecorder``。``record`` 必须是同步
调用；写库失败只计数、不抛给调用方。这些语义是「追踪不阻塞运行」的底线。
"""

from __future__ import annotations

import asyncio
import inspect
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from pathlib import Path
from typing import Any

import pytest
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from maa_api.agent.trace import (
    MAX_ATTRIBUTES,
    MAX_LIST_ITEMS,
    SpanRecorder,
    SpanRecord,
    clean_attributes,
    new_span_id,
)
from maa_api.db.models import TraceSpan, new_uuid, utcnow
from maa_api.db.repositories.trace import TraceSpanRepository
from maa_api.db.session import make_engine
from maa_api.domain.enums import SpanKind
from maa_api.settings import Settings, TraceSettings


def _settings(*, enabled: bool = True) -> Settings:
    return Settings(trace=TraceSettings(enabled=enabled))


@asynccontextmanager
async def _recorder(
    tmp_path: Path,
    *,
    enabled: bool = True,
    **options: Any,
) -> AsyncIterator[tuple[SpanRecorder, async_sessionmaker[AsyncSession], Any]]:
    """临时库 + recorder；退出时关闭 recorder 并释放引擎。"""
    from sqlmodel import SQLModel

    engine = make_engine(f"sqlite+aiosqlite:///{tmp_path / 'trace.db'}")
    async with engine.begin() as connection:
        await connection.run_sync(SQLModel.metadata.create_all)
    factory = async_sessionmaker(engine, expire_on_commit=False, class_=AsyncSession)
    recorder = SpanRecorder(
        factory, settings_provider=lambda: _settings(enabled=enabled), **options
    )
    try:
        yield recorder, factory, engine
    finally:
        await recorder.close()
        await engine.dispose()


def _record(trace_id: str | None = None, *, name: str = "agent.run") -> SpanRecord:
    now = utcnow()
    return SpanRecord(
        trace_id=trace_id or new_uuid(),
        span_id=new_span_id(),
        parent_span_id=None,
        run_id=None,
        name=name,
        kind=SpanKind.OTHER,
        status="ok",
        attributes={},
        started_at=now,
        ended_at=now,
        duration_ms=0,
    )


async def _rows(factory: async_sessionmaker[AsyncSession]) -> list[TraceSpan]:
    async with factory() as session:
        result = await session.execute(select(TraceSpan).order_by(TraceSpan.id))
        return list(result.scalars().all())


async def _count(factory: async_sessionmaker[AsyncSession]) -> int:
    return len(await _rows(factory))


# --------------------------------------------------------------------------- #
# 属性清洗
# --------------------------------------------------------------------------- #
def test_clean_attributes_truncates_and_caps() -> None:
    long_text = "x" * 300
    values: dict[str, Any] = {
        "missing": None,
        "text": long_text,
        "flag": True,
        "count": 7,
        "ratio": 1.5,
        "tags": [f"tag-{index}" for index in range(20)],
    }
    values.update({f"k{index}": index for index in range(60)})

    cleaned = clean_attributes(values)

    assert "missing" not in cleaned
    assert cleaned["text"] == "x" * 255 + "…"
    assert len(cleaned["text"]) == 256
    assert cleaned["flag"] is True
    assert cleaned["count"] == 7
    assert cleaned["ratio"] == 1.5
    assert cleaned["tags"] == [f"tag-{index}" for index in range(MAX_LIST_ITEMS)]
    # 去 None 之后按插入顺序保留前 48 个键：5 个具名键 + k0..k42。
    assert len(cleaned) == MAX_ATTRIBUTES
    assert "k42" in cleaned
    assert "k43" not in cleaned
    assert list(cleaned)[:7] == [
        "text",
        "flag",
        "count",
        "ratio",
        "tags",
        "k0",
        "k1",
    ]


# --------------------------------------------------------------------------- #
# 开关、入队与批量写
# --------------------------------------------------------------------------- #
def test_disabled_recorder_returns_noop_handle(tmp_path: Path) -> None:
    async def scenario() -> None:
        async with _recorder(tmp_path, enabled=False) as (recorder, factory, _engine):
            handle = recorder.start_span(
                "agent.run",
                SpanKind.OTHER,
                trace_id=new_uuid(),
                run_id=None,
                attributes={"gen_ai.operation.name": "invoke_agent"},
            )
            assert recorder.enabled is False
            handle.set_attribute("maa.run.steps", 1)
            handle.end()
            assert handle.ended is True
            assert recorder.stats()["pending"] == 0
            await recorder.flush()
            assert recorder.stats()["written"] == 0
            assert await _count(factory) == 0

    asyncio.run(scenario())


def test_spans_are_written_in_batches_without_awaiting(tmp_path: Path) -> None:
    async def scenario() -> None:
        async with _recorder(
            tmp_path, batch_size=3, flush_interval=60.0
        ) as (recorder, factory, _engine):
            # record 是同步方法：不会 await 写库，也不返回协程。
            assert not inspect.iscoroutinefunction(recorder.record)
            await recorder.start()
            trace_id = new_uuid()

            recorder.record(_record(trace_id))
            recorder.record(_record(trace_id))
            assert recorder.stats()["pending"] == 2
            # 未攒满一批、也没到刷盘间隔：关键路径上没有写库发生。
            assert await _count(factory) == 0

            recorder.record(_record(trace_id))
            await recorder.flush()
            stats = recorder.stats()
            assert stats["pending"] == 0
            assert stats["written"] == 3
            rows = await _rows(factory)
            assert len(rows) == 3
            assert {row.trace_id for row in rows} == {trace_id}
            assert {row.name for row in rows} == {"agent.run"}

    asyncio.run(scenario())


def test_full_queue_drops_and_counts(tmp_path: Path) -> None:
    async def scenario() -> None:
        async with _recorder(
            tmp_path, max_pending=2, flush_interval=60.0
        ) as (recorder, factory, _engine):
            trace_id = new_uuid()
            for _ in range(3):
                recorder.record(_record(trace_id))

            stats = recorder.stats()
            assert stats["pending"] == 2
            assert stats["dropped"] == 1
            assert stats["written"] == 0

            await recorder.flush()
            assert recorder.stats()["written"] == 2
            assert await _count(factory) == 2

    asyncio.run(scenario())


def test_write_failure_is_retried_then_dropped(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    writes: list[int] = []

    async def failing_add_many(self: Any, rows: Any) -> None:
        writes.append(len(rows))
        raise RuntimeError("模拟写库失败")

    monkeypatch.setattr(TraceSpanRepository, "add_many", failing_add_many)

    async def scenario() -> None:
        async with _recorder(
            tmp_path, flush_interval=60.0
        ) as (recorder, factory, _engine):
            recorder.record(_record(new_uuid()))
            # flush 内部按尝试次数重试：3 次都失败后丢弃，不抛给调用方。
            await recorder.flush()
            stats = recorder.stats()
            assert stats["write_errors"] == 3
            assert stats["dropped"] == 1
            assert stats["written"] == 0
            assert stats["pending"] == 0
            assert writes == [1, 1, 1]
            assert await _count(factory) == 0

    asyncio.run(scenario())


def test_end_is_idempotent(tmp_path: Path) -> None:
    async def scenario() -> None:
        async with _recorder(tmp_path) as (recorder, factory, _engine):
            handle = recorder.start_span(
                "agent.run",
                SpanKind.OTHER,
                trace_id=new_uuid(),
                run_id=None,
            )
            handle.set_attribute("maa.run.status", "succeeded")
            handle.end()
            handle.end()
            handle.set_attribute("maa.run.finish_reason", "stop")
            assert handle.ended is True
            await recorder.flush()
            rows = await _rows(factory)
            assert len(rows) == 1
            assert rows[0].attributes == {"maa.run.status": "succeeded"}
            first_duration = rows[0].duration_ms
            handle.end()
            await recorder.flush()
            rows_again = await _rows(factory)
            assert len(rows_again) == 1
            assert rows_again[0].duration_ms == first_duration

    asyncio.run(scenario())


def test_close_flushes_pending_spans(tmp_path: Path) -> None:
    async def scenario() -> None:
        async with _recorder(
            tmp_path, flush_interval=60.0
        ) as (recorder, factory, _engine):
            await recorder.start()
            trace_id = new_uuid()
            recorder.record(_record(trace_id, name="llm.chat"))
            recorder.record(_record(trace_id, name="tool.execute"))
            await recorder.close()

            stats = recorder.stats()
            assert recorder.closed is True
            assert stats["pending"] == 0
            assert stats["written"] == 2
            rows = await _rows(factory)
            assert [row.name for row in rows] == ["llm.chat", "tool.execute"]
            # 关闭之后的 record 只丢弃并计数。
            recorder.record(_record(trace_id))
            assert recorder.stats()["dropped"] == 1

    asyncio.run(scenario())


def test_non_uuid_trace_id_is_rejected(tmp_path: Path) -> None:
    async def scenario() -> None:
        async with _recorder(tmp_path) as (recorder, _factory, _engine):
            for bad in ("not-a-uuid", "z" * 36, "0" * 36):
                with pytest.raises(ValueError):
                    recorder.start_span(
                        "agent.run",
                        SpanKind.OTHER,
                        trace_id=bad,
                        run_id=None,
                    )
            valid = recorder.start_span(
                "agent.run",
                SpanKind.OTHER,
                trace_id=new_uuid(),
                run_id=None,
            )
            assert len(valid.span_id) == 16
            valid.end()

    asyncio.run(scenario())
