"""Persistent, priority-ordered pipeline queue (docs/02 §5, docs/12 M5)."""

from __future__ import annotations

import asyncio
from collections.abc import Callable
from datetime import timedelta, timezone
from typing import Any

from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from maa_api.db.models import Pipeline, Task, utcnow
from maa_api.db.repositories.pipeline import PipelineRepository
from maa_api.domain.enums import PipelineSource, Priority
from maa_api.domain.errors import AppError, ErrorCode
from maa_api.domain.task import ChannelDefaults, PipelineCreate, normalize
from maa_api.services.task_defaults import load_channel_defaults

__all__ = [
    "IDEMPOTENCY_WINDOW",
    "MAX_PENDING_PIPELINES",
    "QueueService",
    "TaskQueue",
]

MAX_PENDING_PIPELINES = 50
IDEMPOTENCY_WINDOW = timedelta(hours=24)
QUEUE_FULL_RETRY_AFTER = "30"

_SOURCE_PRIORITY = {
    PipelineSource.MANUAL: Priority.MANUAL,
    PipelineSource.AGENT: Priority.AGENT,
    PipelineSource.SCHEDULED: Priority.SCHEDULED,
}


class QueueService:
    """Write and query the DB-backed queue.

    SQLite is the queue's source of truth. This service serializes admission
    checks in the single API process; claiming and status changes remain in
    :class:`PipelineRunner`.
    """

    def __init__(
        self,
        session_factory: async_sessionmaker[AsyncSession],
        *,
        wake_runner: Callable[[], None] | None = None,
    ) -> None:
        self._session_factory = session_factory
        self._wake_runner = wake_runner
        self._admission_lock = asyncio.Lock()
        self._operation_lock: asyncio.Lock | None = None
        self._paused = False

    @property
    def paused(self) -> bool:
        return self._paused

    def bind_runner(
        self,
        wake_runner: Callable[[], None],
        operation_lock: asyncio.Lock | None = None,
    ) -> None:
        self._wake_runner = wake_runner
        self._operation_lock = operation_lock

    def _wake(self) -> None:
        if self._wake_runner is not None:
            self._wake_runner()

    async def submit(
        self,
        payload: PipelineCreate,
        *,
        source: PipelineSource = PipelineSource.MANUAL,
        core_id: str = "default",
        idempotency_key: str | None = None,
        schedule_id: str | None = None,
        agent_session_id: str | None = None,
        retry_of_id: str | None = None,
    ) -> tuple[Pipeline, bool]:
        """Validate, normalize, and atomically add a pipeline and its tasks.

        The boolean result indicates that an unexpired idempotency key returned
        an existing row rather than creating a new one.
        """
        if not payload.tasks:
            raise AppError(ErrorCode.PIPELINE_EMPTY, "流水线至少要包含一个任务")
        if len(payload.tasks) > 32:
            raise AppError(
                ErrorCode.PIPELINE_TOO_MANY_TASKS,
                "单条流水线最多包含 32 个任务",
                {"task_count": len(payload.tasks), "maximum": 32},
            )
        if idempotency_key is not None and not 1 <= len(idempotency_key) <= 64:
            raise AppError(
                ErrorCode.INVALID_PARAMETER,
                "Idempotency-Key 长度必须为 1 到 64 个字符",
            )

        source = PipelineSource(source)
        source_priority = int(_SOURCE_PRIORITY[source])
        requested_priority = payload.priority
        # A caller may lower its own priority, but cannot promote itself above
        # the priority assigned to its entry point (docs/05 §7.4).
        priority = source_priority if requested_priority is None else max(
            source_priority, int(requested_priority)
        )

        async with self._admission_lock:
            async with self._session_factory() as session:
                repo = PipelineRepository(session)
                existing: Pipeline | None = None
                if idempotency_key:
                    existing = await repo.get_by_idempotency_key(idempotency_key)
                    if existing is not None:
                        expiry = utcnow() - IDEMPOTENCY_WINDOW
                        if existing.created_at < expiry:
                            await repo.release_expired_idempotency_key(
                                idempotency_key, older_than=expiry
                            )
                            existing = None
                        else:
                            existing = await repo.get(
                                existing.id, with_tasks=True
                            )
                            if existing is None:
                                existing = None
                            elif self._matches_request(
                                existing,
                                payload,
                                source=source,
                                priority=priority,
                            ):
                                await session.commit()
                                return existing, True
                            else:
                                raise AppError(
                                    ErrorCode.IDEMPOTENCY_KEY_CONFLICT,
                                    "Idempotency-Key 已用于不同的流水线请求",
                                    {"pipeline_id": existing.id},
                                )

                if self._paused:
                    raise AppError(
                        ErrorCode.QUEUE_PAUSED,
                        "任务队列已暂停，请恢复队列后再提交",
                    )

                pending_count = await repo.count_pending(core_id)
                if pending_count >= MAX_PENDING_PIPELINES:
                    raise AppError(
                        ErrorCode.QUEUE_FULL,
                        f"待执行流水线已达到上限 {MAX_PENDING_PIPELINES}",
                        {"pending": pending_count, "maximum": MAX_PENDING_PIPELINES},
                        headers={"Retry-After": QUEUE_FULL_RETRY_AFTER},
                    )

                defaults = await self._channel_defaults(session)
                normalized = [normalize(item, defaults) for item in payload.tasks]
                pipeline = Pipeline(
                    core_id=core_id,
                    source=source,
                    priority=priority,
                    status=PipelineStatus.PENDING,
                    title=payload.title,
                    task_count=len(normalized),
                    schedule_id=schedule_id,
                    agent_session_id=agent_session_id,
                    retry_of_id=retry_of_id,
                    idempotency_key=idempotency_key,
                    notify_on_finish=payload.notify_on_finish,
                )
                tasks = [
                    Task(
                        pipeline_id=pipeline.id,
                        order_index=index,
                        type_name=item.type_name,
                        task_name=item.task_name,
                        params=item.params,
                        raw_params=item.raw_params,
                    )
                    for index, item in enumerate(normalized)
                ]
                try:
                    stored = await repo.create(pipeline, tasks)
                    await session.commit()
                except IntegrityError:
                    await session.rollback()
                    # A second process is outside the supported deployment
                    # shape, but the DB unique index still makes key races safe.
                    if idempotency_key:
                        duplicate = await repo.get_by_idempotency_key(idempotency_key)
                        if duplicate is not None:
                            duplicate = await repo.get(
                                duplicate.id, with_tasks=True
                            )
                            if duplicate is not None and self._matches_request(
                                duplicate,
                                payload,
                                source=source,
                                priority=priority,
                            ):
                                await session.commit()
                                return duplicate, True
                            raise AppError(
                                ErrorCode.IDEMPOTENCY_KEY_CONFLICT,
                                "Idempotency-Key 已被另一请求占用",
                                {"pipeline_id": duplicate.id if duplicate else None},
                            ) from None
                    raise

            self._wake()
            return stored, False

    @staticmethod
    async def _channel_defaults(session: AsyncSession) -> ChannelDefaults:
        return await load_channel_defaults(session)

    @staticmethod
    def _matches_request(
        existing: Pipeline,
        payload: PipelineCreate,
        *,
        source: PipelineSource,
        priority: int,
    ) -> bool:
        old_tasks: list[Task] = getattr(existing, "tasks", [])
        if (
            existing.source != source
            or existing.priority != priority
            or existing.title != payload.title
            or existing.notify_on_finish != payload.notify_on_finish
            or len(old_tasks) != len(payload.tasks)
        ):
            return False
        return all(
            old.type_name == requested.name
            and old.raw_params == requested.model_dump(
                exclude={"name"}, exclude_unset=True, by_alias=True
            )
            for old, requested in zip(old_tasks, payload.tasks, strict=True)
        )

    async def snapshot(self, core_id: str = "default") -> dict[str, Any]:
        async with self._session_factory() as session:
            repo = PipelineRepository(session)
            running = await repo.current(core_id)
            pending = await repo.list_pending(core_id)
        return {
            "running": _queue_item(running) if running is not None else None,
            "pending": [_queue_item(item) for item in pending],
            "counts": {"pending": len(pending), "running": int(running is not None)},
            "paused": self._paused,
        }

    async def reprioritize(
        self, pipeline_id: str, priority: Priority
    ) -> Pipeline:
        async with self._session_factory() as session:
            repo = PipelineRepository(session)
            pipeline = await repo.get(pipeline_id)
            if pipeline is None:
                raise AppError(
                    ErrorCode.PIPELINE_NOT_FOUND,
                    "流水线不存在",
                    {"pipeline_id": pipeline_id},
                )
            if not await repo.set_priority(pipeline_id, priority):
                raise AppError(
                    ErrorCode.QUEUE_ITEM_NOT_PENDING,
                    "只有排队中的流水线可以调整优先级",
                    {"pipeline_id": pipeline_id, "status": pipeline.status},
                )
            await session.commit()
            updated = await repo.get(pipeline_id)
        self._wake()
        return updated or pipeline

    async def pause(self) -> bool:
        async with self._admission_lock:
            if self._operation_lock is None:
                changed = not self._paused
                self._paused = True
            else:
                async with self._operation_lock:
                    changed = not self._paused
                    self._paused = True
            self._wake()
            return changed

    async def resume(self) -> bool:
        async with self._admission_lock:
            if self._operation_lock is None:
                changed = self._paused
                self._paused = False
            else:
                async with self._operation_lock:
                    changed = self._paused
                    self._paused = False
            self._wake()
            return changed


def _queue_item(pipeline: Pipeline) -> dict[str, Any]:
    created_at = pipeline.created_at
    if created_at.tzinfo is None:
        created_at = created_at.replace(tzinfo=timezone.utc)
    return {
        "pipeline_id": pipeline.id,
        "name": pipeline.title or pipeline.id,
        "source": str(pipeline.source),
        "priority": int(pipeline.priority),
        "status": str(pipeline.status),
        "created_at": created_at.astimezone(timezone.utc)
        .isoformat()
        .replace("+00:00", "Z"),
    }


# Architecture and milestone docs call this component TaskQueue; retain the
# service-oriented name used by the API wiring as an alias.
TaskQueue = QueueService
