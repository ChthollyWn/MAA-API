"""Single-consumer, callback-driven pipeline execution (docs/02 §5, docs/12 M5)."""

from __future__ import annotations

import asyncio
import logging
import time
from collections.abc import Sequence
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Callable

from pydantic import ValidationError
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from maa_api.core.enums import Message
from maa_api.core.registry import CoreRegistry, DEFAULT_CORE_ID
from maa_api.db.models import Pipeline, Screenshot, Task, utcnow
from maa_api.db.repositories.log import ScreenshotRepository
from maa_api.db.repositories.pipeline import PipelineRepository, TaskRepository
from maa_api.domain.enums import (
    NotifyEvent,
    PipelineSource,
    PipelineStatus,
    ScreenshotTrigger,
    TaskStatus,
)
from maa_api.domain.errors import AppError, ErrorCode
from maa_api.domain.task import RUNTIME_IMMUTABLE, TASK_LABELS, TASK_MODELS
from maa_api.services.log_hub import LogRecord, current_pipeline_id
from maa_api.util.image import store_screenshot

__all__ = [
    "TASK_SNAPSHOT_TIMEOUT_SECONDS",
    "TASK_TIMEOUT_SECONDS",
    "WAIT_POLL_SECONDS",
    "PipelineRunner",
    "build_pipeline_notification",
]

logger = logging.getLogger(__name__)

# Long-running game modes are deliberately given larger budgets than short
# account-management tasks. These are fallback guards; MaaCore callbacks remain
# the completion source of truth.
TASK_TIMEOUT_SECONDS: dict[str, float] = {
    "StartUp": 30 * 60,
    "CloseDown": 10 * 60,
    "Fight": 6 * 60 * 60,
    "Recruit": 60 * 60,
    "Infrast": 60 * 60,
    "Mall": 30 * 60,
    "Award": 30 * 60,
    "Roguelike": 8 * 60 * 60,
    "Reclamation": 8 * 60 * 60,
}
DEFAULT_TASK_TIMEOUT_SECONDS = 60 * 60
CORE_POLL_SECONDS = 2.0
# ``wait_terminal`` 的轮询间隔：Agent 等长任务时够用，又不至于在 SQLite 上打转。
WAIT_POLL_SECONDS = 1.0
# 轮询间隔下限，防止调用方传 0 变成忙等。
MIN_WAIT_POLL_SECONDS = 0.01
# 任务终态留档截图的硬超时：截图只是留现场，绝不能拖慢任务收尾（docs/12 M23 §3.9）。
TASK_SNAPSHOT_TIMEOUT_SECONDS = 10.0
# 需要留档的任务终态。``cancelled`` 不在其中：用户主动取消或服务重启回收，
# 语义上不需要现场。
SNAPSHOT_OUTCOMES: frozenset[str] = frozenset(
    {"completed", "failed", "skipped", "crashed"}
)
# 需要把留档截图挂到日志上的终态 → (级别, 文案模板)。``completed`` 不挂：成功
# 任务的日志里没有可附着的失败语义（docs/12 M23 §3.10）。
SNAPSHOT_LOG_MESSAGES: dict[str, tuple[str, str]] = {
    "failed": ("ERROR", "任务失败 [{name}]，已保存现场截图"),
    "skipped": ("WARNING", "任务多次失败已跳过 [{name}]，已保存现场截图"),
    "crashed": ("ERROR", "任务因内核崩溃中断 [{name}]，已保存现场截图"),
}


def _task_display_name(task: Task) -> str:
    """日志文案里的任务名：优先用户起的 ``task_name``，否则回落任务类型。"""

    name = str(getattr(task, "task_name", "") or "").strip()
    if name:
        return name
    type_name = str(getattr(task, "type_name", "") or "")
    return TASK_LABELS.get(type_name, type_name)


@dataclass(slots=True)
class _Attempt:
    task_id: str
    type_name: str
    maa_task_id: int
    future: asyncio.Future[str]


class _PipelineCancelled(Exception):
    """Internal signal used to unwind an in-flight CoreClient command."""


@dataclass(slots=True)
class _ActivePipeline:
    pipeline_id: str
    core_epoch: int | None = None
    cancel_requested: bool = False
    cancel_event: asyncio.Event = field(default_factory=asyncio.Event)
    crash_event: asyncio.Event = field(default_factory=asyncio.Event)
    core_crashed: bool = False
    stop_complete: asyncio.Event = field(default_factory=asyncio.Event)
    attempt: _Attempt | None = None
    task: Task | None = None
    copilot_snapshots: list[Path] = field(default_factory=list)

    def __post_init__(self) -> None:
        self.stop_complete.set()


class PipelineRunner:
    """Consume persisted pipelines one at a time and drive MaaCore via IPC.

    Callback handlers are invoked by ``CoreClient`` on the asyncio loop thread.
    All database work is therefore scheduled on that loop, never on MaaCore's C
    callback thread. The database remains authoritative across process restarts.
    """

    def __init__(
        self,
        registry: CoreRegistry,
        supervisor: Any,
        session_factory: async_sessionmaker[AsyncSession],
        *,
        core_id: str = DEFAULT_CORE_ID,
        queue_service: Any = None,
        device_manager: Any = None,
        resource_service: Any = None,
        broadcast: Callable[[str, dict[str, Any]], None] | None = None,
        task_timeouts: dict[str, float] | None = None,
    ) -> None:
        self.registry = registry
        self.supervisor = supervisor
        self._session_factory = session_factory
        self.core_id = core_id
        self.queue_service = queue_service
        self.device_manager = device_manager
        self.resource_service = resource_service
        self._broadcast = broadcast or (lambda _kind, _data: None)
        self.task_timeouts = dict(TASK_TIMEOUT_SECONDS)
        if task_timeouts:
            self.task_timeouts.update(task_timeouts)

        self.operation_lock = asyncio.Lock()
        self._wake_event = asyncio.Event()
        self._worker: asyncio.Task[None] | None = None
        self._running = False
        self._active: _ActivePipeline | None = None
        self._claimed_pipeline: Pipeline | None = None
        self._claimed_pipeline_id: str | None = None
        self._claimed_pipeline_epoch: int | None = None
        self._claimed_core_crashes: dict[str, dict[str, Any]] = {}
        self._claiming_core_epoch: int | None = None
        self._core_crashes_during_claim: dict[int, dict[str, Any]] = {}
        self._claim_failure_pipeline_by_generation: dict[int, str] = {}
        self._claim_failure_core_records: dict[int, dict[str, Any]] = {}
        self._core_crash_pipeline_ids: dict[int, set[str]] = {}
        self._cancel_requested_ids: set[str] = set()
        self._loop: asyncio.AbstractEventLoop | None = None
        self.incident_service: Any = None
        self.notify_service: Any = None
        # 留档截图的日志附件只能走 ``LogHub.offer``：stdlib 日志渠道不搬运
        # ``attachment``。绑定是后置且可选的（同 ``bind_notify_service``）。
        self._log_hub: Any = None
        self._recovered_pipeline_ids: list[str] = []
        self._incident_tasks: set[asyncio.Task[Any]] = set()
        self._notify_tasks: set[asyncio.Task[Any]] = set()

        client = self.registry.get(self.core_id)
        client.on("CALLBACK", self._on_callback)
        client.on("FATAL", self._on_fatal)

    @property
    def running(self) -> bool:
        return self._running

    @property
    def active_pipeline_id(self) -> str | None:
        """Current or just-claimed pipeline identity for crash coalescing."""
        if self._active is not None:
            return self._active.pipeline_id
        return self._claimed_pipeline_id

    @property
    def active_pipeline_epoch(self) -> int | None:
        """Core generation assigned to the active or just-claimed pipeline."""
        if self._active is not None:
            return self._active.core_epoch
        return self._claimed_pipeline_epoch

    def core_crashed_pipeline_ids(self, generation: int) -> tuple[str, ...]:
        """Return current-boot pipelines known to have crashed in this generation."""
        return tuple(sorted(self._core_crash_pipeline_ids.get(int(generation), ())))

    def is_claiming_core_generation(self, generation: int) -> bool:
        """Whether a queue claim is in flight for this core generation."""
        return self._claiming_core_epoch == int(generation)

    def claim_failure_pipeline_id(self, generation: int) -> str | None:
        """Return the pipeline key reserved for an unreleased failed claim."""
        return self._claim_failure_pipeline_by_generation.get(int(generation))

    @property
    def recovered_pipeline_ids(self) -> tuple[str, ...]:
        """Interrupted failures recovered by the most recent start call."""
        return tuple(self._recovered_pipeline_ids)

    def bind_incident_service(self, service: Any) -> None:
        """Bind failure diagnosis after confirmation/runtime services are ready."""
        self.incident_service = service

    def schedule_pipeline_incident(self, pipeline_id: str) -> None:
        """Queue a committed terminal failure diagnosis without blocking the runner."""
        service = self.incident_service
        if service is None:
            return
        task = asyncio.create_task(
            service.pipeline_failed(pipeline_id),
            name=f"agent-pipeline-incident-{pipeline_id}",
        )
        self._incident_tasks.add(task)
        task.add_done_callback(self._forget_incident_task)

    def _schedule_core_incident(
        self, generation: int, record: dict[str, Any]
    ) -> None:
        service = self.incident_service
        if service is None:
            return
        task = asyncio.create_task(
            service.core_crashed(generation, record),
            name=f"agent-core-incident-{self.core_id}-{generation}",
        )
        self._incident_tasks.add(task)
        task.add_done_callback(self._forget_incident_task)

    def _forget_incident_task(self, task: asyncio.Task[Any]) -> None:
        self._incident_tasks.discard(task)
        if task.cancelled():
            return
        # ``Task.exception()`` 是「返回」异常而不是抛出它，写成 try/except 会把
        # 后台任务的失败静默吞掉——这里必须判返回值。
        failure = task.exception()
        if failure is not None:
            logger.error("流水线自动诊断后台任务失败：%s", failure, exc_info=failure)

    def bind_notify_service(self, service: Any) -> None:
        """Bind multi-channel notifications after ``NotifyService`` is built.

        Binding is late (and optional) for the same reason as the incident
        service: the runner is constructed before the services that need the
        database session factory, and tests substitute runners without it.
        """
        self.notify_service = service

    def bind_log_hub(self, hub: Any) -> None:
        """Bind the log hub so task snapshots can carry log attachments.

        ``LogHubHandler`` only carries the pipeline/task/request context; the
        ``attachment`` field reaches the frontend exclusively through
        ``LogHub.offer``, so the runner needs the hub itself.
        """
        self._log_hub = hub

    def _schedule_pipeline_notification(
        self, pipeline_id: str, status: PipelineStatus
    ) -> None:
        """Queue the terminal notification without blocking the queue consumer.

        Delivery can take up to the per-channel timeout (10s) per channel; the
        consumer must never wait for a notification before claiming the next
        pipeline. Shutdown cancels pending sends exactly like incident tasks.
        """
        if self.notify_service is None:
            return
        task = asyncio.create_task(
            self._send_pipeline_notification(pipeline_id, status),
            name=f"pipeline-notify-{pipeline_id}",
        )
        self._notify_tasks.add(task)
        task.add_done_callback(self._forget_notify_task)

    def _forget_notify_task(self, task: asyncio.Task[Any]) -> None:
        self._notify_tasks.discard(task)
        if task.cancelled():
            return
        failure = task.exception()
        if failure is not None:
            logger.error("流水线终态通知后台任务失败：%s", failure, exc_info=failure)

    async def _send_pipeline_notification(
        self, pipeline_id: str, status: PipelineStatus
    ) -> None:
        """Deliver one pipeline's terminal status to the configured channels.

        Transport failures are the channels' business (``NotifyService``
        records them per channel and never disables a channel); this method only
        has to keep its own failure out of the executor's terminal path.
        """
        service = self.notify_service
        if service is None:
            return
        async with self._session_factory() as session:
            pipeline = await PipelineRepository(session).get(
                pipeline_id, with_tasks=True
            )
        if pipeline is None:
            return
        if not bool(getattr(pipeline, "notify_on_finish", False)):
            return
        event, payload = build_pipeline_notification(
            pipeline, getattr(pipeline, "tasks", []) or [], status
        )
        result = await service.send_event(event, payload)
        logger.info(
            "流水线终态通知：pipeline_id=%s event=%s matched=%s sent=%s failed=%s",
            pipeline_id,
            event,
            result.get("matched"),
            result.get("sent"),
            result.get("failed"),
        )

    def wake(self) -> None:
        """Wake the consumer after a queue or core state change."""
        self._wake_event.set()

    def notify_core_state(self, _state: Any = None) -> None:
        self.wake()

    def notify_core_crash(self, record: dict[str, Any] | None = None) -> None:
        """Wake the active task when called from the supervisor's monitor thread."""
        loop = self._loop
        if loop is None or loop.is_closed():
            return
        try:
            current_loop = asyncio.get_running_loop()
        except RuntimeError:
            current_loop = None
        if current_loop is loop:
            self._apply_core_crash(record)
        else:
            loop.call_soon_threadsafe(self._apply_core_crash, record)

    async def start(self) -> None:
        if self._running:
            return
        self._loop = asyncio.get_running_loop()
        await self._recover_interrupted()
        self._running = True
        self._worker = asyncio.create_task(
            self._run_loop(), name=f"maa-pipeline-runner-{self.core_id}"
        )
        self.wake()

    async def stop(self) -> None:
        """Stop taking new work and cleanly cancel the current pipeline."""
        active = self._active
        if active is not None:
            try:
                await self.request_cancel(active.pipeline_id)
            except AppError:
                logger.exception("关闭时取消流水线失败：%s", active.pipeline_id)
        self._running = False
        self.wake()
        worker = self._worker
        self._worker = None
        if worker is not None:
            try:
                await asyncio.wait_for(worker, timeout=35.0)
            except TimeoutError:
                worker.cancel()
                try:
                    await worker
                except asyncio.CancelledError:
                    pass
        pending_incidents = tuple(self._incident_tasks)
        for task in pending_incidents:
            task.cancel()
        if pending_incidents:
            await asyncio.gather(*pending_incidents, return_exceptions=True)
        pending_notifications = tuple(self._notify_tasks)
        for task in pending_notifications:
            task.cancel()
        if pending_notifications:
            await asyncio.gather(*pending_notifications, return_exceptions=True)

    def log_context(self, msg: Any, details: dict[str, Any]) -> tuple[str, str] | None:
        """Return pipeline/task ids for task callback logs while a task is active."""
        try:
            message = Message(int(msg))
        except (TypeError, ValueError):
            return None
        if message not in {
            Message.TaskChainStart,
            Message.TaskChainCompleted,
            Message.TaskChainError,
            Message.TaskChainExtraInfo,
            Message.TaskChainStopped,
            Message.SubTaskError,
            Message.SubTaskStart,
            Message.SubTaskCompleted,
            Message.SubTaskExtraInfo,
            Message.SubTaskStopped,
            Message.AllTasksCompleted,
        }:
            return None
        active = self._active
        if active is None or active.attempt is None:
            return None
        raw_id = details.get("taskid") if isinstance(details, dict) else None
        if raw_id is not None:
            try:
                if int(raw_id) != active.attempt.maa_task_id:
                    return None
            except (TypeError, ValueError):
                return None
        return active.pipeline_id, active.attempt.task_id

    async def request_cancel(self, pipeline_id: str) -> None:
        """Request cancellation; the consumer remains the sole status writer."""
        cancelled_task_ids: list[str] = []
        async with self.operation_lock:
            async with self._session_factory() as session:
                repo = PipelineRepository(session)
                pipeline = await repo.get(pipeline_id)
                if pipeline is None:
                    raise AppError(
                        ErrorCode.PIPELINE_NOT_FOUND,
                        "流水线不存在",
                        {"pipeline_id": pipeline_id},
                    )
                status = PipelineStatus(pipeline.status)
                if status.is_terminal:
                    raise AppError(
                        ErrorCode.PIPELINE_NOT_CANCELLABLE,
                        "流水线已结束，无法取消",
                        {"pipeline_id": pipeline_id, "status": status.value},
                    )
                if status is PipelineStatus.PENDING:
                    tasks = await TaskRepository(session).list_by_pipeline(pipeline_id)
                    await repo.mark_terminal(pipeline_id, PipelineStatus.CANCELLED)
                    task_repo = TaskRepository(session)
                    for task in tasks:
                        if TaskStatus(task.status) is TaskStatus.PENDING:
                            await task_repo.update_status(task.id, TaskStatus.CANCELLED)
                            cancelled_task_ids.append(task.id)
                    await session.commit()
                    cancelled = await repo.get(pipeline_id, with_tasks=True)
                else:
                    self._cancel_requested_ids.add(pipeline_id)
                    active = self._active
                    if active is not None and active.pipeline_id == pipeline_id:
                        active.cancel_requested = True
                        active.stop_complete.clear()
                        # Signal only after STOP has been put on the same IPC
                        # queue, so the next pipeline cannot race ahead of it.
                        should_stop_core = True
                    else:
                        should_stop_core = True
                    cancelled = None

        if status is PipelineStatus.PENDING:
            if cancelled is not None:
                for task_id in cancelled_task_ids:
                    await self._publish_task(task_id)
                await self.publish_pipeline(pipeline_id)
                await self.publish_queue_changed()
            self.wake()
            return

        if should_stop_core:
            try:
                await self.registry.get(self.core_id).stop()
            except AppError as exc:
                logger.warning(
                    "取消流水线时 STOP 未确认：pipeline_id=%s code=%s",
                    pipeline_id,
                    exc.code,
                )
            finally:
                active = self._active
                if active is not None and active.pipeline_id == pipeline_id:
                    active.cancel_requested = True
                    active.cancel_event.set()
                    active.stop_complete.set()
                self.wake()

    async def set_task_params(self, task_id: str, params: dict[str, Any]) -> Task:
        """Validate and apply a supported live task parameter patch.

        Persist only after MaaCore acknowledges the patch. The operation lock
        prevents a cancellation or new queue claim from changing the active
        attempt between validation and the native command.
        """
        if not params or "name" in params:
            raise AppError(
                ErrorCode.TASK_PARAM_INVALID,
                "参数更新必须包含字段且不能修改任务类型 name",
            )
        if any(value is None for value in params.values()):
            raise AppError(
                ErrorCode.TASK_PARAM_INVALID,
                "运行中参数更新不能使用 null 值",
            )
        async with self.operation_lock:
            active = self._active
            if active is None or active.attempt is None or active.attempt.task_id != task_id:
                raise AppError(
                    ErrorCode.INVALID_PARAMETER,
                    "任务当前没有运行中的 MaaCore 执行",
                    {"task_id": task_id},
                )

            async with self._session_factory() as session:
                repository = TaskRepository(session)
                task = await repository.get(task_id)
                if task is None:
                    raise AppError(
                        ErrorCode.TASK_NOT_FOUND,
                        "任务不存在",
                        {"task_id": task_id},
                    )
                if TaskStatus(task.status) is not TaskStatus.RUNNING:
                    raise AppError(
                        ErrorCode.INVALID_PARAMETER,
                        "只有运行中的任务可以修改参数",
                        {"task_id": task_id, "status": str(task.status)},
                    )
                model = TASK_MODELS.get(task.type_name)
                if model is None:
                    raise AppError(
                        ErrorCode.UNKNOWN_TASK_TYPE,
                        f"未知任务类型：{task.type_name}",
                    )

                supplied_fields = _task_patch_field_names(model, params)
                immutable = set(RUNTIME_IMMUTABLE.get(task.type_name, ()))
                changed_immutable = supplied_fields.intersection(immutable)
                if changed_immutable:
                    raise AppError(
                        ErrorCode.TASK_NOT_RUNTIME_MUTABLE,
                        "运行中任务包含不可修改参数",
                        {"fields": sorted(changed_immutable)},
                    )

                normalized_existing = _normalize_task_params(model, task.params or {})
                normalized_patch = _normalize_task_params(model, params)
                try:
                    validated = model.model_validate(
                        {**normalized_existing, **normalized_patch, "name": task.type_name}
                    )
                except ValidationError as exc:
                    raise AppError(
                        ErrorCode.TASK_PARAM_INVALID,
                        "运行中任务参数不符合任务 schema",
                        {"issues": exc.errors(include_input=False, include_context=False)},
                    ) from exc

                normalized_patch = validated.model_dump(
                    include=supplied_fields,
                    exclude={"name"}, exclude_unset=True, by_alias=True
                )
                accepted = await self.registry.get(self.core_id).set_task_params(
                    int(active.attempt.maa_task_id), normalized_patch
                )
                if not accepted:
                    raise AppError(
                        ErrorCode.CORE_COMMAND_FAILED,
                        "MaaCore 拒绝修改运行中任务参数",
                        {"task_id": task_id, "maa_task_id": active.attempt.maa_task_id},
                    )
                stored_params = {**dict(task.params or {}), **normalized_patch}
                stored_raw_params = {
                    **dict(task.raw_params or {}),
                    **normalized_patch,
                }
                if not await repository.update_params(
                    task_id,
                    params=stored_params,
                    raw_params=stored_raw_params,
                ):
                    raise AppError(
                        ErrorCode.INVALID_PARAMETER,
                        "任务已结束，不再接受参数更新",
                        {"task_id": task_id},
                    )
                await session.commit()
                updated = await repository.get(task_id)
                if active.task is not None:
                    active.task.params = dict(stored_params)

        if updated is None:
            raise AppError(ErrorCode.TASK_NOT_FOUND, "任务不存在", {"task_id": task_id})
        await self._publish_task(task_id)
        return updated

    async def cancel_pending(self, *, source: str | None = None) -> int:
        """Cancel queued rows only; a running pipeline is never preempted."""
        try:
            source_value = None if source is None else source
            if source_value is not None:
                source_value = PipelineSource(source_value)
        except ValueError:
            raise AppError(
                ErrorCode.INVALID_PARAMETER,
                f"未知的流水线来源：{source}",
            ) from None
        async with self.operation_lock:
            async with self._session_factory() as session:
                repo = PipelineRepository(session)
                pending = await repo.list_pending(self.core_id, source=source_value)
                task_repo = TaskRepository(session)
                changed: list[str] = []
                changed_tasks: list[str] = []
                for pipeline in pending:
                    if await repo.mark_terminal(
                        pipeline.id, PipelineStatus.CANCELLED
                    ):
                        changed.append(pipeline.id)
                        for task in await task_repo.list_by_pipeline(pipeline.id):
                            if TaskStatus(task.status) is TaskStatus.PENDING:
                                await task_repo.update_status(
                                    task.id, TaskStatus.CANCELLED
                                )
                                changed_tasks.append(task.id)
                await session.commit()
        for task_id in changed_tasks:
            await self._publish_task(task_id)
        for pipeline_id in changed:
            await self.publish_pipeline(pipeline_id)
        if changed:
            await self.publish_queue_changed()
        self.wake()
        return len(changed)

    async def wait_terminal(
        self,
        pipeline_id: str,
        timeout_seconds: float,
        *,
        poll_seconds: float | None = None,
    ) -> tuple[Pipeline, bool]:
        """等待流水线进入终态，返回 ``(pipeline, finished)``。

        - 每次轮询开一个**自己的短会话**（``_session_factory``）：只读一次流水线与
          任务状态就关闭。调用方（Agent 工具上下文）的会话全程不被占用，等待几十秒
          到十分钟也不会把审计事务挂在那里。
        - 已经处于终态（completed / failed / cancelled）时立即返回，不睡第一个轮询
          周期；未知 id 立即抛 ``PIPELINE_NOT_FOUND``，同样不等满超时。
        - 到终态返回 ``(pipeline, True)``；超时返回 ``(pipeline, False)`` 与最后一次
          读到的状态。两个分支返回的流水线都带 ``tasks``，与 ``get_pipeline`` 同构。
        - ``asyncio.sleep`` 可被取消：调用方被取消（服务关闭、SSE 断开）时
          ``CancelledError`` 照常向上传播，这里不吞。

        ``poll_seconds`` 是轮询间隔，默认 :data:`WAIT_POLL_SECONDS`（1 秒）；下限
        :data:`MIN_WAIT_POLL_SECONDS`，避免调用方传 0 变成忙等。
        """
        interval = max(
            WAIT_POLL_SECONDS if poll_seconds is None else float(poll_seconds),
            MIN_WAIT_POLL_SECONDS,
        )
        deadline = time.monotonic() + max(float(timeout_seconds), 0.0)
        while True:
            pipeline = await self._read_pipeline(pipeline_id)
            if pipeline is None:
                raise AppError(
                    ErrorCode.PIPELINE_NOT_FOUND,
                    "流水线不存在",
                    {"pipeline_id": pipeline_id},
                )
            if PipelineStatus(pipeline.status).is_terminal:
                return pipeline, True
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                return pipeline, False
            await asyncio.sleep(min(interval, remaining))

    async def _read_pipeline(self, pipeline_id: str) -> Pipeline | None:
        """一次性短会话读出流水线与其任务。

        会话在返回前关闭，但 ``get(with_tasks=True)`` 已把全部字段与任务列表加载
        完毕（``tasks`` 走非映射属性），``publish_pipeline`` 用的是同一模式。
        """
        async with self._session_factory() as session:
            return await PipelineRepository(session).get(
                pipeline_id, with_tasks=True
            )

    async def _recover_interrupted(self) -> None:
        """Mark RUNNING work from a previous API process as interrupted."""
        recovered: list[str] = []
        async with self._session_factory() as session:
            pipelines = await PipelineRepository(session).list_running(self.core_id)
            pipeline_repo = PipelineRepository(session)
            task_repo = TaskRepository(session)
            for pipeline in pipelines:
                tasks = await task_repo.list_by_pipeline(pipeline.id)
                for task in tasks:
                    task_status = TaskStatus(task.status)
                    if task_status is TaskStatus.RUNNING:
                        await task_repo.update_status(
                            task.id,
                            TaskStatus.FAILED,
                            error_code=ErrorCode.CORE_CRASHED,
                            error_message="服务重启时任务仍在运行，已标记为内核中断",
                        )
                    elif task_status is TaskStatus.PENDING:
                        await task_repo.update_status(
                            task.id,
                            TaskStatus.CANCELLED,
                            error_code=ErrorCode.CORE_CRASHED,
                            error_message="流水线因内核中断而结束",
                        )
                await pipeline_repo.mark_terminal(
                    pipeline.id,
                    PipelineStatus.FAILED,
                    error_code=ErrorCode.CORE_CRASHED,
                    error_message="服务重启时流水线仍在运行，已标记为内核中断",
                )
                recovered.append(pipeline.id)
            if recovered:
                await session.commit()
        for pipeline_id in recovered:
            await self.publish_pipeline(pipeline_id)
        self._recovered_pipeline_ids = recovered

    async def _run_loop(self) -> None:
        while self._running:
            self._wake_event.clear()
            pipeline: Pipeline | None = None
            tasks: list[Task] = []
            if not (self.queue_service is not None and self.queue_service.paused):
                try:
                    if await self._core_can_run_work():
                        pipeline, tasks = await self._claim_next()
                except asyncio.CancelledError:
                    raise
                except Exception:  # noqa: BLE001 - keep the sole consumer alive
                    logger.exception("读取流水线队列或内核状态失败")
            if pipeline is not None:
                await self.publish_pipeline(pipeline.id)
                await self.publish_queue_changed()
                await self._execute_pipeline(pipeline, tasks)
                continue
            try:
                await asyncio.wait_for(self._wake_event.wait(), timeout=CORE_POLL_SECONDS)
            except TimeoutError:
                pass

    async def _core_can_run_work(self) -> bool:
        """Only gate queue claims on CoreSupervisor readiness.

        Device connectivity is deliberately checked after a row is claimed. If
        CoreClient.connected() were checked here, an initially offline device
        could never reach DeviceManager.ensure_available() and auto-recover.
        """
        state = getattr(self.supervisor, "state", None)
        state_value = getattr(state, "value", state)
        return state_value == "ready"

    async def _claim_next(self) -> tuple[Pipeline | None, list[Task]]:
        async with self.operation_lock:
            if not self._running:
                return None, []
            if self.queue_service is not None and self.queue_service.paused:
                return None, []
            generation = getattr(self.supervisor, "generation", None)
            self._claiming_core_epoch = (
                int(generation) if generation is not None else None
            )
            if generation is not None:
                self._claim_failure_pipeline_by_generation.pop(
                    int(generation), None
                )
                self._claim_failure_core_records.pop(int(generation), None)
            pipeline: Pipeline | None = None
            pipeline_id: str | None = None
            claimed_epoch: int | None = None
            claim_completed = False
            release_succeeded = True
            tasks: list[Task] = []
            try:
                async with self._session_factory() as session:
                    repo = PipelineRepository(session)
                    pipeline = await repo.claim_next(
                        self.core_id,
                        core_epoch=generation,
                    )
                    if pipeline is None:
                        self._claimed_pipeline = None
                        self._claimed_pipeline_id = None
                        self._claimed_pipeline_epoch = None
                    else:
                        # Publish the claim before the next await. Core callbacks
                        # arriving during task loading or COMMIT must coalesce here.
                        self._claimed_pipeline = pipeline
                        pipeline_id = pipeline.id
                        raw_claimed_epoch = getattr(pipeline, "core_epoch", None)
                        claimed_epoch = (
                            int(raw_claimed_epoch)
                            if raw_claimed_epoch is not None
                            else None
                        )
                        self._claimed_pipeline_id = pipeline_id
                        self._claimed_pipeline_epoch = claimed_epoch
                        if claimed_epoch is not None:
                            staged_crash = self._core_crashes_during_claim.pop(
                                int(claimed_epoch), None
                            )
                            if staged_crash is not None:
                                self._attach_claimed_core_crash(
                                    pipeline_id, claimed_epoch, staged_crash
                                )
                        tasks = await TaskRepository(session).list_by_pipeline(
                            pipeline_id
                        )
                        await session.commit()
                # Count the claim as complete only after the session context
                # exits successfully; __aexit__ can itself fail.
                claim_completed = True
                return (pipeline, tasks) if pipeline is not None else (None, [])
            except BaseException:
                if pipeline is not None and self._claimed_pipeline is pipeline:
                    self._claimed_pipeline = None
                    self._claimed_pipeline_id = None
                    self._claimed_pipeline_epoch = None
                if pipeline_id is not None:
                    pending_crash = self._claimed_core_crashes.pop(
                        pipeline_id, None
                    )
                    if pending_crash is not None:
                        raw_generation = pending_crash.get(
                            "generation", generation or claimed_epoch
                        )
                        try:
                            pending_generation = int(raw_generation)
                        except (TypeError, ValueError):
                            pending_generation = None
                        if pending_generation is not None:
                            self._core_crashes_during_claim[
                                pending_generation
                            ] = pending_crash
                    release_succeeded = False
                    for cleanup_attempt in range(2):
                        try:
                            async with self._session_factory() as cleanup_session:
                                await PipelineRepository(
                                    cleanup_session
                                ).release_failed_claim(
                                    pipeline_id, core_epoch=claimed_epoch
                                )
                                await cleanup_session.commit()
                            release_succeeded = True
                            break
                        except Exception:  # noqa: BLE001 - startup recovery remains the final fallback
                            logger.exception(
                                "claim 失败后释放流水线失败：pipeline_id=%s attempt=%s",
                                pipeline_id,
                                cleanup_attempt + 1,
                            )
                    if not release_succeeded:
                        if generation is not None:
                            self._claim_failure_pipeline_by_generation[
                                int(generation)
                            ] = pipeline_id
                        try:
                            await self._record_failed_claim_error(pipeline_id)
                        except Exception:  # noqa: BLE001 - leave startup recovery as final fallback
                            logger.exception(
                                "claim 失败后无法终结流水线：pipeline_id=%s",
                                pipeline_id,
                            )
                raise
            finally:
                try:
                    if generation is not None and (
                        not claim_completed or pipeline is None
                    ):
                        while True:
                            staged_crash = self._core_crashes_during_claim.pop(
                                int(generation), None
                            )
                            if staged_crash is None:
                                break
                            raw_generation = staged_crash.get(
                                "generation", generation
                            )
                            try:
                                crash_generation = int(raw_generation)
                            except (TypeError, ValueError):
                                crash_generation = int(generation)
                            if pipeline_id is None or release_succeeded:
                                self._schedule_core_incident(
                                    crash_generation, staged_crash
                                )
                            else:
                                self._claim_failure_core_records[
                                    crash_generation
                                ] = staged_crash
                    if pipeline_id is not None and not release_succeeded:
                        self._schedule_failed_claim_pipeline_incident(
                            pipeline_id,
                            int(generation) if generation is not None else None,
                        )
                finally:
                    self._claiming_core_epoch = None

    async def _execute_pipeline(
        self, pipeline: Pipeline, tasks: list[Task]
    ) -> None:
        pending_crash = self._claimed_core_crashes.pop(pipeline.id, None)
        active = _ActivePipeline(
            pipeline_id=pipeline.id,
            core_epoch=pipeline.core_epoch,
            core_crashed=pending_crash is not None,
        )
        if pending_crash is not None:
            active.crash_event.set()
        self._active = active
        log_context = current_pipeline_id.set(pipeline.id)
        had_failures = False
        last_task_error: AppError | None = None
        fatal_error: AppError | None = None
        cancelled = not active.core_crashed and (
            not self._running or pipeline.id in self._cancel_requested_ids
        )
        self._cancel_requested_ids.discard(pipeline.id)
        try:
            if cancelled:
                await self._cancel_pipeline_rows(pipeline.id)
                if active.core_crashed:
                    await self._record_core_crash_terminal(pipeline.id)
                return
            if self.device_manager is not None and not active.core_crashed:
                try:
                    available = await self.device_manager.ensure_available(timeout=10.0)
                except asyncio.CancelledError:
                    raise
                except Exception:
                    logger.exception(
                        "流水线设备预检异常：pipeline_id=%s", pipeline.id
                    )
                    available = False
                if not available and not active.core_crashed:
                    handled = await self._handle_device_unavailable(pipeline)
                    if handled:
                        if active.core_crashed:
                            await self._record_core_crash_terminal(pipeline.id)
                        return
                    cancelled = True
            if active.core_crashed:
                crash_error = AppError(
                    ErrorCode.CORE_CRASHED,
                    "MaaCore 子进程在任务执行前中断",
                    {"pipeline_id": pipeline.id},
                )
                if tasks:
                    await self._set_task_status(
                        tasks[0].id,
                        TaskStatus.FAILED,
                        error_code=crash_error.code,
                        error_message=crash_error.message,
                    )
                await self._cancel_unstarted_tasks(pipeline.id, crash_error)
                await self._finish_pipeline(
                    pipeline.id,
                    PipelineStatus.FAILED,
                    error_code=crash_error.code,
                    error_message=crash_error.message,
                )
                return
            for task in tasks:
                if active.cancel_requested or pipeline.id in self._cancel_requested_ids:
                    cancelled = True
                    break
                outcome, error = await self._execute_task(active, task)
                # 留档是纯附加动作：自身吞掉所有异常（除取消），不参与下面这些
                # 分支的判定，也不改变任务终态语义。
                await self._capture_task_snapshot(active, task, outcome)
                if outcome == "cancelled":
                    cancelled = True
                    break
                if outcome == "crashed":
                    fatal_error = error or AppError(
                        ErrorCode.CORE_CRASHED,
                        "MaaCore 子进程中断，流水线已停止",
                    )
                    break
                if outcome == "failed":
                    fatal_error = error or AppError(
                        ErrorCode.INTERNAL_ERROR,
                        "任务执行失败，流水线已停止",
                    )
                    break
                if outcome == "skipped":
                    had_failures = True
                    last_task_error = error

            if active.core_crashed:
                # A recorded core crash owns the terminal result even if a
                # concurrent cancellation request arrived in the same window.
                fatal_error = AppError(
                    ErrorCode.CORE_CRASHED,
                    "MaaCore 子进程中断，流水线已停止",
                    {"pipeline_id": pipeline.id},
                )
                cancelled = False
            if cancelled or (active.cancel_requested and not active.core_crashed):
                await self._cancel_pipeline_rows(pipeline.id)
            elif fatal_error is not None:
                await self._cancel_unstarted_tasks(pipeline.id, fatal_error)
                await self._finish_pipeline(
                    pipeline.id,
                    PipelineStatus.FAILED,
                    error_code=fatal_error.code,
                    error_message=fatal_error.message,
                )
            else:
                terminal = PipelineStatus.FAILED if had_failures else PipelineStatus.COMPLETED
                await self._finish_pipeline(
                    pipeline.id,
                    terminal,
                    error_code=(last_task_error.code if last_task_error else None),
                    error_message=(last_task_error.message if last_task_error else None),
                )
        except asyncio.CancelledError:
            # Lifespan first requests graceful cancellation. This fallback is
            # for a hard application shutdown after that grace period expires.
            await self._cancel_pipeline_rows(pipeline.id)
            raise
        except Exception as exc:  # noqa: BLE001 - persist unexpected runner failures
            logger.exception("流水线执行器发生未处理错误：%s", pipeline.id)
            await self._cancel_unstarted_tasks(
                pipeline.id,
                AppError(ErrorCode.INTERNAL_ERROR, "流水线执行器发生内部错误"),
            )
            await self._finish_pipeline(
                pipeline.id,
                PipelineStatus.FAILED,
                error_code=ErrorCode.INTERNAL_ERROR,
                error_message=str(exc),
            )
        finally:
            if self.resource_service is not None:
                for snapshot_path in active.copilot_snapshots:
                    try:
                        await self.resource_service.release_copilot_path(snapshot_path)
                    except Exception:  # noqa: BLE001 - cleanup must not rewrite pipeline result
                        logger.exception(
                            "清理 Copilot snapshot 失败：pipeline_id=%s path=%s",
                            pipeline.id,
                            snapshot_path,
                        )
            active.copilot_snapshots.clear()
            if active.core_crashed:
                await self._record_core_crash_terminal(pipeline.id)
            self._active = None
            if (
                self._claimed_pipeline is not None
                and self._claimed_pipeline.id == pipeline.id
            ):
                self._claimed_pipeline = None
                self._claimed_pipeline_id = None
                self._claimed_pipeline_epoch = None
            self._claimed_core_crashes.pop(pipeline.id, None)
            self._cancel_requested_ids.discard(pipeline.id)
            current_pipeline_id.reset(log_context)
            await self.publish_pipeline(pipeline.id)
            await self.publish_queue_changed()

    async def _capture_task_snapshot(
        self, active: _ActivePipeline, task: Task, outcome: str
    ) -> None:
        """任务进入终态后留一张现场截图（docs/12 M23 §3.9/§3.10）。

        ``completed`` / ``failed`` / ``skipped`` / ``crashed`` 都留：用户要看的
        正是「跑成什么样 / 卡在哪」，内核崩在任务里更是最需要现场的情况；
        ``cancelled`` 不留。失败与跳过（以及崩溃）额外把这张图挂到一条日志上。

        整段保持「失败即降级」：设备缺失、抓图异常、超时、落盘/落库失败都只记
        一条 WARNING，**绝不改写任务终态、不重试、不拖慢收尾**。
        """
        if outcome not in SNAPSHOT_OUTCOMES:
            return
        manager = self.device_manager
        if manager is None:
            # 未装配设备管理器是精简装配与既有测试替身的正常形态。
            logger.debug(
                "任务留档截图跳过：未装配 device_manager（pipeline_id=%s）",
                active.pipeline_id,
            )
            return
        capture = getattr(manager, "screenshot", None)
        if not callable(capture):
            logger.debug(
                "任务留档截图跳过：device_manager 未提供 screenshot()（pipeline_id=%s）",
                active.pipeline_id,
            )
            return

        try:
            result = await asyncio.wait_for(
                capture(backend="adb"), timeout=TASK_SNAPSHOT_TIMEOUT_SECONDS
            )
            # 不传 quality：与手动存档共用 ``adb.screenshot_quality``（25）的默认值，
            # 避免留档把磁盘吃满。``store_screenshot`` 是同步阻塞的 PIL + 写盘。
            stored = await asyncio.to_thread(store_screenshot, result.image)
            sha256 = str(stored["sha256"])
            relative_path = f"image/screenshot/{sha256[:2]}/{sha256}.jpg"
            async with self._session_factory() as session:
                screenshot = await ScreenshotRepository(session).create(
                    Screenshot(
                        trigger=ScreenshotTrigger.TASK_SNAPSHOT,
                        pipeline_id=active.pipeline_id,
                        task_id=task.id,
                        backend=str(result.backend),
                        path=relative_path,
                        format="jpg",
                        width=int(stored["width"]),
                        height=int(stored["height"]),
                        size_bytes=int(stored["bytes"]),
                    )
                )
                # 提交前取 id：会话若为 expire_on_commit=True，提交后再读会触发
                # 异步上下文里的懒加载。
                screenshot_id = screenshot.id
                await session.commit()
            self._offer_snapshot_log(active, task, outcome, stored, screenshot_id)
        except asyncio.CancelledError:
            # 取消照常传播：``_execute_pipeline`` 的取消处理必须照旧收尾。
            raise
        except Exception as exc:  # noqa: BLE001 - 留档失败不得改写任务终态
            logger.warning(
                "任务留档截图失败：pipeline_id=%s task_id=%s outcome=%s：%s",
                active.pipeline_id,
                task.id,
                outcome,
                exc,
            )

    def _offer_snapshot_log(
        self,
        active: _ActivePipeline,
        task: Task,
        outcome: str,
        stored: dict[str, Any],
        screenshot_id: str | None,
    ) -> None:
        """把留档截图作为附件挂到一条 ``source=task`` 的日志上。

        ``completed`` 不挂；``self._log_hub is None`` 时只跳过日志，留档照做。
        """
        message = SNAPSHOT_LOG_MESSAGES.get(outcome)
        hub = self._log_hub
        if message is None or hub is None:
            return
        level, template = message
        hub.offer(
            LogRecord(
                ts=time.time(),
                source="task",
                level=level,
                content=template.format(name=_task_display_name(task)),
                pipeline_id=active.pipeline_id,
                task_id=task.id,
                # 附件形状与前端的 ``logImage()`` 对齐：它只认 ``sha256`` 与
                # ``/api/images/<sha>/(thumb|full)`` 两种地址。
                attachment={**stored, "screenshot_id": screenshot_id},
            )
        )

    async def _execute_task(
        self, active: _ActivePipeline, task: Task
    ) -> tuple[str, AppError | None]:
        active.task = task
        client = self.registry.get(self.core_id)
        max_retries = max(int(task.max_retries), 0)
        retry_count = int(task.retry_count)
        error: AppError | None = None

        while True:
            if active.cancel_requested:
                await active.stop_complete.wait()
                await self._set_task_status(task.id, TaskStatus.CANCELLED)
                return "cancelled", None
            if active.core_crashed:
                error = AppError(
                    ErrorCode.CORE_CRASHED,
                    "MaaCore 子进程中断，当前任务未完成",
                    {"task_id": task.id},
                )
                await self._set_task_status(
                    task.id,
                    TaskStatus.FAILED,
                    error_code=error.code,
                    error_message=error.message,
                )
                return "crashed", error
            await self._set_task_status(task.id, TaskStatus.RUNNING)
            core_params = dict(task.params or {})
            if task.type_name == "Copilot":
                try:
                    asset_id = core_params.get("asset_id")
                    if not isinstance(asset_id, str) or not asset_id:
                        raise AppError(
                            ErrorCode.RESOURCE_ASSET_NOT_FOUND,
                            "Copilot 作业不存在",
                            {"task_id": task.id},
                        )
                    if self.resource_service is None:
                        raise AppError(
                            ErrorCode.SERVICE_UNAVAILABLE,
                            "Copilot 资源解析服务尚未装配",
                            {"task_id": task.id},
                        )
                    filename = await self.resource_service.resolve_copilot_path(asset_id)
                    active.copilot_snapshots.append(filename)
                except AppError as exc:
                    await self._set_task_status(
                        task.id,
                        TaskStatus.FAILED,
                        error_code=exc.code,
                        error_message=exc.message,
                    )
                    return "failed", exc
                except Exception as exc:  # noqa: BLE001 - fail closed before append
                    error = AppError(
                        ErrorCode.RESOURCE_LOAD_FAILED,
                        "Copilot 作业文件解析失败",
                        {"task_id": task.id},
                    )
                    logger.warning("Copilot 作业文件解析失败：%s：%s", task.id, exc)
                    await self._set_task_status(
                        task.id,
                        TaskStatus.FAILED,
                        error_code=error.code,
                        error_message=error.message,
                    )
                    return "failed", error
                core_params = {
                    "filename": str(filename),
                    "formation": core_params.get("formation", False),
                }
            attempt: _Attempt | None = None
            try:
                try:
                    append_result = await self._await_core_command(
                        active, client.append_task(task.type_name, core_params)
                    )
                except AppError as exc:
                    if (
                        task.type_name == "Copilot"
                        and exc.code is ErrorCode.CORE_COMMAND_FAILED
                    ):
                        await self._set_task_status(
                            task.id,
                            TaskStatus.FAILED,
                            error_code=exc.code,
                            error_message=exc.message,
                        )
                        return "failed", exc
                    raise
                maa_task_id = int(append_result)
                if maa_task_id <= 0:
                    rejection = AppError(
                        ErrorCode.CORE_COMMAND_FAILED,
                        f"MaaCore 未接受任务 {task.type_name}",
                        {"cmd": "APPEND_TASK", "task_id": task.id},
                    )
                    if task.type_name == "Copilot":
                        await self._set_task_status(
                            task.id,
                            TaskStatus.FAILED,
                            error_code=rejection.code,
                            error_message=rejection.message,
                        )
                        return "failed", rejection
                    raise rejection
                await self._bind_maa_task_id(task.id, maa_task_id)
                future: asyncio.Future[str] = asyncio.get_running_loop().create_future()
                attempt = _Attempt(task.id, task.type_name, maa_task_id, future)
                active.attempt = attempt
                started = await self._await_core_command(active, client.start())
                if not started:
                    raise AppError(
                        ErrorCode.CORE_COMMAND_FAILED,
                        f"MaaCore 未启动任务 {task.type_name}",
                        {"cmd": "START", "task_id": task.id},
                    )
                if active.cancel_requested:
                    await active.stop_complete.wait()
                    await self._set_task_status(task.id, TaskStatus.CANCELLED)
                    return "cancelled", None
                if active.core_crashed:
                    error = AppError(
                        ErrorCode.CORE_CRASHED,
                        "MaaCore 子进程中断，当前任务未完成",
                        {"task_id": task.id, "maa_task_id": maa_task_id},
                    )
                    await self._set_task_status(
                        task.id,
                        TaskStatus.FAILED,
                        error_code=error.code,
                        error_message=error.message,
                    )
                    return "crashed", error
                outcome = await self._wait_attempt(active, attempt)
                if outcome == "completed":
                    await self._set_task_status(task.id, TaskStatus.COMPLETED)
                    return "completed", None
                if outcome == "stopped":
                    await active.stop_complete.wait()
                    await self._set_task_status(task.id, TaskStatus.CANCELLED)
                    return "cancelled", None
                if outcome == "crashed":
                    error = AppError(
                        ErrorCode.CORE_CRASHED,
                        "MaaCore 子进程中断，当前任务未完成",
                        {"task_id": task.id, "maa_task_id": maa_task_id},
                    )
                    await self._set_task_status(
                        task.id,
                        TaskStatus.FAILED,
                        error_code=error.code,
                        error_message=error.message,
                    )
                    return "crashed", error
                error = AppError(
                    ErrorCode.CORE_COMMAND_FAILED,
                    f"MaaCore 任务链报告失败：{task.task_name}",
                    {"task_id": task.id, "maa_task_id": maa_task_id},
                )
            except asyncio.CancelledError:
                raise
            except _PipelineCancelled:
                await active.stop_complete.wait()
                await self._set_task_status(task.id, TaskStatus.CANCELLED)
                return "cancelled", None
            except AppError as exc:
                error = exc
                if (
                    task.type_name == "Copilot"
                    and (exc.details or {}).get("cmd") == "APPEND_TASK"
                ):
                    await self._set_task_status(
                        task.id,
                        TaskStatus.FAILED,
                        error_code=exc.code,
                        error_message=exc.message,
                    )
                    return "failed", exc
                if exc.code is ErrorCode.CORE_COMMAND_TIMEOUT:
                    # A timed-out IPC command may still have reached MaaCore.
                    # Stop the chain before any internal retry to avoid running
                    # the same operation concurrently.
                    error = AppError(
                        exc.code,
                        exc.message,
                        {**(exc.details or {}), "timed_out": True},
                    )
                if exc.code in (ErrorCode.CORE_CRASHED, ErrorCode.CORE_START_FAILED):
                    await self._set_task_status(
                        task.id,
                        TaskStatus.FAILED,
                        error_code=exc.code,
                        error_message=exc.message,
                    )
                    return "crashed", exc
            except Exception as exc:  # noqa: BLE001
                error = AppError(
                    ErrorCode.CORE_COMMAND_FAILED,
                    f"执行任务 {task.task_name} 失败：{exc}",
                    {"task_id": task.id},
                )
                logger.exception("任务调用 MaaCore 失败：%s", task.id)
            finally:
                if active.attempt is attempt:
                    active.attempt = None

            if active.cancel_requested:
                await active.stop_complete.wait()
                await self._set_task_status(task.id, TaskStatus.CANCELLED)
                return "cancelled", None

            if error is not None:
                try:
                    # Clear a task that was appended but did not start, and
                    # discard any remaining work after a failed attempt before
                    # retrying its parameters.
                    await client.stop()
                except AppError:
                    logger.warning("失败任务后的 MaaCore STOP 未确认：%s", task.id)

            if active.cancel_requested:
                await active.stop_complete.wait()
                await self._set_task_status(task.id, TaskStatus.CANCELLED)
                return "cancelled", None
            if active.core_crashed:
                crash_error = AppError(
                    ErrorCode.CORE_CRASHED,
                    "MaaCore 子进程中断，当前任务未完成",
                    {"task_id": task.id},
                )
                await self._set_task_status(
                    task.id,
                    TaskStatus.FAILED,
                    error_code=crash_error.code,
                    error_message=crash_error.message,
                )
                return "crashed", crash_error

            if retry_count < max_retries:
                retry_count = await self._increment_retry(task.id)
                await self._set_task_status(task.id, TaskStatus.RUNNING)
                await self._publish_task(task.id)
                delay = max(int(task.retry_delay), 0)
                if delay:
                    cancel_waiter = asyncio.create_task(active.cancel_event.wait())
                    crash_waiter = asyncio.create_task(active.crash_event.wait())
                    try:
                        await asyncio.wait(
                            {cancel_waiter, crash_waiter},
                            timeout=delay,
                            return_when=asyncio.FIRST_COMPLETED,
                        )
                    finally:
                        cancel_waiter.cancel()
                        crash_waiter.cancel()
                        for waiter in (cancel_waiter, crash_waiter):
                            try:
                                await waiter
                            except asyncio.CancelledError:
                                pass
                    if active.cancel_requested:
                        await active.stop_complete.wait()
                        await self._set_task_status(task.id, TaskStatus.CANCELLED)
                        return "cancelled", None
                    if active.core_crashed:
                        error = AppError(
                            ErrorCode.CORE_CRASHED,
                            "MaaCore 子进程中断，当前任务未完成",
                            {"task_id": task.id},
                        )
                        await self._set_task_status(
                            task.id,
                            TaskStatus.FAILED,
                            error_code=error.code,
                            error_message=error.message,
                        )
                        return "crashed", error
                continue

            await self._set_task_status(
                task.id,
                TaskStatus.SKIPPED,
                error_code=error.code if error else ErrorCode.CORE_COMMAND_FAILED,
                error_message=error.message if error else "任务达到最大重试次数，已跳过",
            )
            return "skipped", error

    async def _handle_device_unavailable(self, pipeline: Pipeline) -> bool:
        """Defer scheduled work six times; fail manual/agent work immediately.

        Return ``False`` when cancellation won the operation-lock race so the
        normal cancellation path can persist its terminal state.
        """
        error = AppError(
            ErrorCode.DEVICE_NOT_CONNECTED,
            "设备当前不可用，流水线未执行",
            {
                "pipeline_id": pipeline.id,
                "source": str(pipeline.source),
                "device": self.device_manager.snapshot()
                if self.device_manager is not None
                else None,
                "agent_action": "可先重连设备，再重新提交流水线"
                if PipelineSource(pipeline.source) is PipelineSource.AGENT
                else None,
            },
        )
        if (
            PipelineSource(pipeline.source) is PipelineSource.SCHEDULED
            and int(getattr(pipeline, "defer_count", 0)) < 6
        ):
            async with self.operation_lock:
                if self._active is not None and self._active.cancel_requested:
                    return False
                async with self._session_factory() as session:
                    repo = PipelineRepository(session)
                    deferred = await repo.defer_running(
                        pipeline.id, utcnow() + timedelta(minutes=5)
                    )
                    if deferred:
                        await session.commit()
            if deferred:
                logger.warning(
                    "设备不可用，定时流水线延后 5 分钟：pipeline_id=%s defer_count=%s",
                    pipeline.id,
                    int(getattr(pipeline, "defer_count", 0)) + 1,
                )
                return True

        changed_task_ids: list[str] = []
        pipeline_failed = False
        async with self.operation_lock:
            if self._active is not None and self._active.cancel_requested:
                return False
            async with self._session_factory() as session:
                pipeline_repo = PipelineRepository(session)
                task_repo = TaskRepository(session)
                current = await pipeline_repo.get(pipeline.id)
                if current is None or PipelineStatus(current.status).is_terminal:
                    return True
                tasks = await task_repo.list_by_pipeline(pipeline.id)
                for task in tasks:
                    if TaskStatus(task.status) in (TaskStatus.PENDING, TaskStatus.RUNNING):
                        await task_repo.update_status(
                            task.id,
                            TaskStatus.FAILED,
                            error_code=ErrorCode.DEVICE_NOT_CONNECTED,
                            error_message=error.message,
                        )
                        changed_task_ids.append(task.id)
                pipeline_failed = await pipeline_repo.mark_terminal(
                    pipeline.id,
                    PipelineStatus.FAILED,
                    error_code=ErrorCode.DEVICE_NOT_CONNECTED,
                    error_message=error.message,
                )
                await session.commit()
        if pipeline_failed:
            # 手动 / agent 提交的流水线在设备预检就失败：与 _finish_pipeline 同源，
            # 用户提交了任务却什么都没跑起来，是必须让他知道的一种终态。
            self._schedule_pipeline_notification(
                pipeline.id, PipelineStatus.FAILED
            )
            self.schedule_pipeline_incident(pipeline.id)
        for task_id in changed_task_ids:
            await self._publish_task(task_id)
        snapshot = (
            self.device_manager.snapshot()
            if self.device_manager is not None
            else {"core_id": self.core_id, "state": "unavailable"}
        )
        self._broadcast("device_status", snapshot)
        logger.error(
            "设备预检失败，流水线已终止：pipeline_id=%s source=%s code=%s",
            pipeline.id,
            pipeline.source,
            ErrorCode.DEVICE_NOT_CONNECTED,
        )
        return True

    async def _await_core_command(self, active: _ActivePipeline, awaitable: Any) -> Any:
        """Race an IPC command against cancellation and child-process failure."""
        command_task = asyncio.create_task(awaitable)
        cancel_waiter = asyncio.create_task(active.cancel_event.wait())
        crash_waiter = asyncio.create_task(active.crash_event.wait())
        try:
            done, _pending = await asyncio.wait(
                {command_task, cancel_waiter, crash_waiter},
                return_when=asyncio.FIRST_COMPLETED,
            )
            if crash_waiter in done or active.core_crashed:
                raise AppError(
                    ErrorCode.CORE_CRASHED,
                    "MaaCore 子进程中断，当前任务未完成",
                    {"pipeline_id": active.pipeline_id},
                )
            if cancel_waiter in done or active.cancel_requested:
                await active.stop_complete.wait()
                raise _PipelineCancelled
            return await command_task
        finally:
            for waiter in (cancel_waiter, crash_waiter):
                waiter.cancel()
            if not command_task.done():
                command_task.cancel()
            for task in (command_task, cancel_waiter, crash_waiter):
                try:
                    await task
                except BaseException:
                    pass

    async def _wait_attempt(self, active: _ActivePipeline, attempt: _Attempt) -> str:
        timeout = max(
            float(
                self.task_timeouts.get(
                    attempt.type_name, DEFAULT_TASK_TIMEOUT_SECONDS
                )
            ),
            0.1,
        )
        cancel_waiter = asyncio.create_task(active.cancel_event.wait())
        crash_waiter = asyncio.create_task(active.crash_event.wait())
        started = time.monotonic()
        try:
            done, _pending = await asyncio.wait(
                {attempt.future, cancel_waiter, crash_waiter},
                timeout=timeout,
                return_when=asyncio.FIRST_COMPLETED,
            )
            if crash_waiter in done or active.core_crashed:
                return "crashed"
            if cancel_waiter in done:
                await active.stop_complete.wait()
                return "stopped"
            if attempt.future in done:
                return attempt.future.result()
            error = AppError(
                ErrorCode.CORE_COMMAND_TIMEOUT,
                f"任务 {attempt.type_name} 执行超过 {timeout:g} 秒",
                {
                    "task_id": attempt.task_id,
                    "maa_task_id": attempt.maa_task_id,
                    "timeout": timeout,
                    "elapsed": time.monotonic() - started,
                    "timed_out": True,
                },
            )
            raise error
        finally:
            cancel_waiter.cancel()
            crash_waiter.cancel()
            for waiter in (cancel_waiter, crash_waiter):
                try:
                    await waiter
                except asyncio.CancelledError:
                    pass

    def _on_callback(self, payload: dict[str, Any]) -> None:
        if not isinstance(payload, dict):
            return
        try:
            message = Message(int(payload.get("msg")))
        except (TypeError, ValueError):
            return
        if message not in {
            Message.TaskChainCompleted,
            Message.TaskChainError,
            Message.TaskChainStopped,
        }:
            if message is Message.AllTasksCompleted:
                active = self._active
                attempt = active.attempt if active is not None else None
                if attempt is not None and not attempt.future.done():
                    logger.error(
                        "收到 AllTasksCompleted，但 task %s 仍未收到 TaskChain 终态回调；强制收敛为完成",
                        attempt.maa_task_id,
                    )
                    attempt.future.set_result("completed")
            return
        details = payload.get("details")
        if not isinstance(details, dict):
            details = {}
        active = self._active
        attempt = active.attempt if active is not None else None
        if attempt is None or attempt.future.done():
            return
        raw_id = details.get("taskid")
        if raw_id is not None:
            try:
                if int(raw_id) != attempt.maa_task_id:
                    return
            except (TypeError, ValueError):
                return
        else:
            taskchain = details.get("taskchain")
            if taskchain is not None and str(taskchain) != attempt.type_name:
                return
        result = {
            Message.TaskChainCompleted: "completed",
            Message.TaskChainError: "failed",
            Message.TaskChainStopped: "stopped",
        }[message]
        attempt.future.set_result(result)

    def _on_fatal(self, payload: dict[str, Any]) -> None:
        self._apply_core_crash(payload if isinstance(payload, dict) else None)

    def _attach_claimed_core_crash(
        self, pipeline_id: str, generation: int, record: dict[str, Any]
    ) -> None:
        self._core_crash_pipeline_ids.setdefault(generation, set()).add(pipeline_id)
        self._claimed_core_crashes[pipeline_id] = dict(record)

    def _apply_core_crash(self, record: dict[str, Any] | None) -> None:
        active = self._active
        generation = record.get("generation") if isinstance(record, dict) else None
        if not isinstance(generation, int):
            generation = None
        if active is not None:
            if (
                generation is not None
                and active.core_epoch is not None
                and generation != active.core_epoch
            ):
                if generation is not None:
                    self._schedule_claim_failure_crash(int(generation), record)
                return
            crashed_generation = (
                generation if generation is not None else active.core_epoch
            )
            if crashed_generation is not None:
                self._core_crash_pipeline_ids.setdefault(
                    int(crashed_generation), set()
                ).add(active.pipeline_id)
            active.core_crashed = True
            active.crash_event.set()
            if active.attempt is not None:
                future = active.attempt.future
                if not future.done():
                    future.set_result("crashed")
        else:
            claimed_id = self._claimed_pipeline_id
            claimed_epoch = self._claimed_pipeline_epoch
            if claimed_id is not None:
                if (
                    generation is not None
                    and claimed_epoch is not None
                    and generation != claimed_epoch
                ):
                    return
                crashed_generation = (
                    generation if generation is not None else claimed_epoch
                )
                if crashed_generation is not None:
                    self._attach_claimed_core_crash(
                        claimed_id,
                        int(crashed_generation),
                        dict(record or {}, generation=int(crashed_generation)),
                    )
            else:
                claim_generation = self._claiming_core_epoch
                if claim_generation is not None and (
                    generation is None or generation == claim_generation
                ):
                    self._core_crashes_during_claim[claim_generation] = dict(
                        record or {}, generation=claim_generation
                    )
                elif generation is not None:
                    self._schedule_claim_failure_crash(generation, record)
        self.wake()

    def _schedule_claim_failure_crash(
        self, generation: int, record: dict[str, Any] | None
    ) -> bool:
        pipeline_id = self._claim_failure_pipeline_by_generation.get(generation)
        if pipeline_id is None:
            return False
        crash_record = dict(record or {}, generation=generation)
        self._claim_failure_core_records[generation] = crash_record
        task = asyncio.create_task(
            self._record_failed_claim_core_crash(
                pipeline_id,
                generation=generation,
            ),
            name=f"agent-claim-core-crash-{pipeline_id}",
        )
        self._incident_tasks.add(task)
        task.add_done_callback(self._forget_incident_task)
        return True

    async def _set_task_status(
        self,
        task_id: str,
        status: TaskStatus,
        *,
        error_code: ErrorCode | str | None = None,
        error_message: str | None = None,
    ) -> None:
        async with self._session_factory() as session:
            repo = TaskRepository(session)
            await repo.update_status(
                task_id,
                status,
                error_code=error_code,
                error_message=error_message,
            )
            await session.commit()
        await self._publish_task(task_id)

    async def _bind_maa_task_id(self, task_id: str, maa_task_id: int) -> None:
        async with self._session_factory() as session:
            await TaskRepository(session).bind_maa_task_id(task_id, maa_task_id)
            await session.commit()

    async def _increment_retry(self, task_id: str) -> int:
        async with self._session_factory() as session:
            repo = TaskRepository(session)
            count = await repo.increment_retry(task_id)
            await session.commit()
            return count

    async def _cancel_unstarted_tasks(
        self, pipeline_id: str, error: AppError
    ) -> None:
        async with self._session_factory() as session:
            repo = TaskRepository(session)
            tasks = await repo.list_by_pipeline(pipeline_id)
            changed: list[str] = []
            for task in tasks:
                status = TaskStatus(task.status)
                if status in (TaskStatus.PENDING, TaskStatus.RUNNING):
                    target = TaskStatus.FAILED if status is TaskStatus.RUNNING else TaskStatus.CANCELLED
                    await repo.update_status(
                        task.id,
                        target,
                        error_code=error.code,
                        error_message=error.message,
                    )
                    changed.append(task.id)
            await session.commit()
        for task_id in changed:
            await self._publish_task(task_id)

    async def _cancel_pipeline_rows(self, pipeline_id: str) -> None:
        async with self._session_factory() as session:
            pipeline_repo = PipelineRepository(session)
            task_repo = TaskRepository(session)
            pipeline = await pipeline_repo.get(pipeline_id)
            if pipeline is None or PipelineStatus(pipeline.status).is_terminal:
                return
            tasks = await task_repo.list_by_pipeline(pipeline_id)
            changed: list[str] = []
            for task in tasks:
                if TaskStatus(task.status) in (TaskStatus.PENDING, TaskStatus.RUNNING):
                    await task_repo.update_status(task.id, TaskStatus.CANCELLED)
                    changed.append(task.id)
            await pipeline_repo.mark_terminal(
                pipeline_id, PipelineStatus.CANCELLED
            )
            await session.commit()
        for task_id in changed:
            await self._publish_task(task_id)

    async def _finish_pipeline(
        self,
        pipeline_id: str,
        status: PipelineStatus,
        *,
        error_code: ErrorCode | str | None = None,
        error_message: str | None = None,
    ) -> None:
        changed = False
        active = self._active
        if (
            active is not None
            and active.pipeline_id == pipeline_id
            and active.core_crashed
        ):
            status = PipelineStatus.FAILED
            error_code = ErrorCode.CORE_CRASHED
            error_message = "MaaCore 子进程中断，流水线已停止"
        async with self._session_factory() as session:
            repo = PipelineRepository(session)
            changed = await repo.mark_terminal(
                pipeline_id,
                status,
                error_code=error_code,
                error_message=error_message,
            )
            await session.commit()
        active = self._active
        if (
            active is not None
            and active.pipeline_id == pipeline_id
            and active.core_crashed
            and (
                PipelineStatus(status) is not PipelineStatus.FAILED
                or str(error_code) != ErrorCode.CORE_CRASHED.value
            )
        ):
            # 终态被内核崩溃抢占。通知留给 _record_core_crash_terminal 在真正改写
            # 那一次发出，否则这里会先报一条「完成」再报一条「失败」。
            await self._record_core_crash_terminal(pipeline_id)
            return
        if changed:
            # mark_terminal 只在状态机真的接受了这次流转时为真，重放不会重发。
            self._schedule_pipeline_notification(pipeline_id, PipelineStatus(status))
        if changed and PipelineStatus(status) is PipelineStatus.FAILED:
            if str(error_code) == ErrorCode.CORE_CRASHED.value:
                active = self._active
                current_id = (
                    active.pipeline_id
                    if active is not None
                    else self._claimed_pipeline_id
                )
                if current_id == pipeline_id:
                    core_epoch = (
                        active.core_epoch
                        if active is not None
                        else self._claimed_pipeline_epoch
                    )
                    if core_epoch is not None:
                        self._core_crash_pipeline_ids.setdefault(
                            int(core_epoch), set()
                        ).add(pipeline_id)
            self.schedule_pipeline_incident(pipeline_id)

    async def _record_core_crash_terminal(self, pipeline_id: str) -> None:
        """Persist CORE_CRASHED after an in-flight terminal write raced FATAL."""
        active = self._active
        if active is None or active.pipeline_id != pipeline_id or not active.core_crashed:
            return
        error_message = "MaaCore 子进程中断，流水线已停止"
        changed_task_ids: set[str] = set()
        persisted = False
        last_error: Exception | None = None
        # 累积而不是每次重试都覆盖：重试自会看到行已是 CORE_CRASHED、返回 False，
        # 用 False 覆盖就把上一次真正改写成功的记录丢了，通知也不会发出。它同时
        # 保证只在真正改写成功的那一次通知，重复调用（例如 finally 里再补一次）
        # 不会命中 mark_core_crashed 的 WHERE 条件。
        ever_promoted = False
        for _attempt in range(2):
            try:
                async with self._session_factory() as session:
                    pipeline_repo = PipelineRepository(session)
                    task_repo = TaskRepository(session)
                    ever_promoted = (
                        await pipeline_repo.mark_core_crashed(
                            pipeline_id, error_message=error_message
                        )
                        or ever_promoted
                    )
                    tasks = await task_repo.list_by_pipeline(pipeline_id)
                    active_task_id = active.task.id if active.task is not None else None
                    candidate = next(
                        (
                            task
                            for task in tasks
                            if task.id == active_task_id
                            and TaskStatus(task.status) is not TaskStatus.COMPLETED
                        ),
                        None,
                    )
                    if candidate is None:
                        candidate = next(
                            (
                                task
                                for task in tasks
                                if TaskStatus(task.status) is not TaskStatus.COMPLETED
                            ),
                            None,
                        )
                    for task in tasks:
                        current_status = TaskStatus(task.status)
                        if current_status is TaskStatus.COMPLETED:
                            continue
                        if candidate is not None and task.id == candidate.id:
                            await task_repo.update_status(
                                task.id,
                                TaskStatus.FAILED,
                                error_code=ErrorCode.CORE_CRASHED,
                                error_message=error_message,
                            )
                            changed_task_ids.add(task.id)
                        elif current_status in (TaskStatus.PENDING, TaskStatus.RUNNING):
                            await task_repo.update_status(
                                task.id,
                                TaskStatus.CANCELLED,
                                error_code=ErrorCode.CORE_CRASHED,
                                error_message=error_message,
                            )
                            changed_task_ids.add(task.id)
                    await session.commit()
                persisted = True
                break
            except Exception as exc:  # noqa: BLE001 - retry context-exit/commit ambiguity once
                last_error = exc
                logger.warning(
                    "写入 core crash 流水线终态失败，将重试：pipeline_id=%s attempt=%s error=%s",
                    pipeline_id,
                    _attempt + 1,
                    type(exc).__name__,
                )
        if not persisted:
            assert last_error is not None
            raise last_error
        if ever_promoted:
            self._schedule_pipeline_notification(pipeline_id, PipelineStatus.FAILED)
        if active.core_epoch is not None:
            self._core_crash_pipeline_ids.setdefault(
                int(active.core_epoch), set()
            ).add(pipeline_id)
        # The key is unique in storage, so schedule even when a prior commit
        # already wrote CORE_CRASHED but failed before queuing its incident.
        self.schedule_pipeline_incident(pipeline_id)
        for task_id in sorted(changed_task_ids):
            await self._publish_task(task_id)

    async def _record_failed_claim_core_crash(
        self,
        pipeline_id: str,
        *,
        generation: int | None,
        schedule_incident: bool = True,
    ) -> None:
        """Persist a core crash on a committed claim whose transaction then failed."""
        error_message = "MaaCore 子进程在任务开始前中断，流水线已停止"
        async with self._session_factory() as session:
            pipeline_repo = PipelineRepository(session)
            task_repo = TaskRepository(session)
            await pipeline_repo.mark_core_crashed(
                pipeline_id, error_message=error_message
            )
            tasks = await task_repo.list_by_pipeline(pipeline_id)
            candidate = next(
                (
                    task
                    for task in tasks
                    if TaskStatus(task.status) is not TaskStatus.COMPLETED
                ),
                None,
            )
            for task in tasks:
                current_status = TaskStatus(task.status)
                if current_status is TaskStatus.COMPLETED:
                    continue
                if candidate is not None and task.id == candidate.id:
                    await task_repo.update_status(
                        task.id,
                        TaskStatus.FAILED,
                        error_code=ErrorCode.CORE_CRASHED,
                        error_message=error_message,
                    )
                elif current_status in (TaskStatus.PENDING, TaskStatus.RUNNING):
                    await task_repo.update_status(
                        task.id,
                        TaskStatus.CANCELLED,
                        error_code=ErrorCode.CORE_CRASHED,
                        error_message=error_message,
                    )
            await session.commit()
        if generation is not None:
            self._core_crash_pipeline_ids.setdefault(generation, set()).add(
                pipeline_id
            )
            self._claim_failure_core_records.pop(generation, None)
        if schedule_incident:
            self.schedule_pipeline_incident(pipeline_id)

    async def _record_failed_claim_error(self, pipeline_id: str) -> None:
        """Fail a claim that cannot be released, so it cannot stick RUNNING."""
        error_message = "流水线领取失败，服务未能开始执行"
        changed = False
        async with self._session_factory() as session:
            pipeline_repo = PipelineRepository(session)
            task_repo = TaskRepository(session)
            changed = await pipeline_repo.mark_terminal(
                pipeline_id,
                PipelineStatus.FAILED,
                error_code=ErrorCode.INTERNAL_ERROR,
                error_message=error_message,
            )
            tasks = await task_repo.list_by_pipeline(pipeline_id)
            candidate = next(
                (
                    task
                    for task in tasks
                    if TaskStatus(task.status) is not TaskStatus.COMPLETED
                ),
                None,
            )
            for task in tasks:
                current_status = TaskStatus(task.status)
                if current_status is TaskStatus.COMPLETED:
                    continue
                if candidate is not None and task.id == candidate.id:
                    await task_repo.update_status(
                        task.id,
                        TaskStatus.FAILED,
                        error_code=ErrorCode.INTERNAL_ERROR,
                        error_message=error_message,
                    )
                elif current_status in (TaskStatus.PENDING, TaskStatus.RUNNING):
                    await task_repo.update_status(
                        task.id,
                        TaskStatus.CANCELLED,
                        error_code=ErrorCode.INTERNAL_ERROR,
                        error_message=error_message,
                    )
            await session.commit()
        if changed:
            self._schedule_pipeline_notification(pipeline_id, PipelineStatus.FAILED)
        # The finalizer gives a core monitor callback already queued with
        # call_soon_threadsafe one event-loop turn to coalesce before snapshotting.

    def _schedule_failed_claim_pipeline_incident(
        self, pipeline_id: str, generation: int | None
    ) -> None:
        service = self.incident_service
        if service is None:
            return

        async def finalize() -> None:
            try:
                await asyncio.sleep(0.05)
                if generation is not None:
                    record = self._claim_failure_core_records.pop(generation, None)
                    if record is not None:
                        # 这次写入没有重试路径，失败必须只影响「崩溃现场」的保真度，
                        # 不能连带跳过下面的诊断会话创建 —— 否则一次 DB 抖动会让
                        # 开着自动诊断的失败流水线完全没有诊断记录。
                        try:
                            await self._record_failed_claim_core_crash(
                                pipeline_id,
                                generation=generation,
                                schedule_incident=False,
                            )
                        except Exception:
                            logger.exception(
                                "记录 claim 失败期间的内核崩溃现场失败，仍继续创建诊断会话 pipeline_id=%s generation=%s",
                                pipeline_id,
                                generation,
                            )
                await service.pipeline_failed(pipeline_id)
            except Exception:
                logger.exception(
                    "claim 失败流水线的诊断调度失败 pipeline_id=%s", pipeline_id
                )
            finally:
                # Keep the generation-to-pipeline association until a new claim
                # starts or the process exits. A delayed monitor-thread FATAL
                # must still reuse this pipeline incident key after diagnosis.
                if generation is not None:
                    self._claim_failure_core_records.pop(generation, None)

        task = asyncio.create_task(
            finalize(), name=f"agent-claim-failure-incident-{pipeline_id}"
        )
        self._incident_tasks.add(task)
        task.add_done_callback(self._forget_incident_task)

    async def _publish_task(self, task_id: str) -> None:
        async with self._session_factory() as session:
            task = await TaskRepository(session).get(task_id)
        if task is None:
            return
        error = None
        if task.error_code or task.error_message:
            error = {"code": task.error_code, "message": task.error_message}
        self._broadcast(
            "task_status",
            {
                "pipeline_id": task.pipeline_id,
                "task_id": task.id,
                "task_name": task.task_name,
                "type_name": task.type_name,
                "status": str(task.status),
                "retry_count": task.retry_count,
                "max_retries": task.max_retries,
                "error": error,
            },
        )
        await self.publish_pipeline(task.pipeline_id)

    async def publish_pipeline(self, pipeline_id: str) -> None:
        async with self._session_factory() as session:
            pipeline = await PipelineRepository(session).get(
                pipeline_id, with_tasks=True
            )
        if pipeline is None:
            return
        tasks = getattr(pipeline, "tasks", [])
        progress = {
            "total": pipeline.task_count,
            "completed": sum(
                TaskStatus(task.status) is TaskStatus.COMPLETED for task in tasks
            ),
            "failed": sum(
                TaskStatus(task.status) in (TaskStatus.FAILED, TaskStatus.SKIPPED)
                for task in tasks
            ),
        }
        error = None
        if pipeline.error_code or pipeline.error_message:
            error = {
                "code": pipeline.error_code,
                "message": pipeline.error_message,
            }
        self._broadcast(
            "pipeline_status",
            {
                "pipeline_id": pipeline.id,
                "status": str(pipeline.status),
                "source": str(pipeline.source),
                "priority": int(pipeline.priority),
                "progress": progress,
                "started_at": _epoch(pipeline.started_at),
                "finished_at": _epoch(pipeline.finished_at),
                "error": error,
            },
        )

    async def publish_queue_changed(self) -> None:
        if self.queue_service is None:
            return
        self._broadcast(
            "queue_changed", await self.queue_service.snapshot(self.core_id)
        )


def _task_patch_field_names(model: type, params: dict[str, Any]) -> set[str]:
    """Translate Pydantic field aliases in a partial patch to Python names."""
    return {
        name
        for name in model.model_fields
        if set(params).intersection(_task_field_aliases(model, name))
    }


def _normalize_task_params(model: type, params: dict[str, Any]) -> dict[str, Any]:
    """Map accepted validation aliases to the model's Python field names."""
    normalized: dict[str, Any] = {}
    for key, value in params.items():
        field_name = next(
            (
                name
                for name in model.model_fields
                if key in _task_field_aliases(model, name)
            ),
            key,
        )
        normalized[field_name] = value
    return normalized


def _task_field_aliases(model: type, name: str) -> set[str]:
    field = model.model_fields[name]
    aliases = {name}
    for alias in (field.alias, field.serialization_alias, field.validation_alias):
        if isinstance(alias, str):
            aliases.add(alias)
        for choice in getattr(alias, "choices", ()):
            if isinstance(choice, str):
                aliases.add(choice)
    return aliases


def _epoch(value: datetime | None) -> float | None:
    if value is None:
        return None
    if value.tzinfo is None:
        value = value.replace(tzinfo=timezone.utc)
    return value.timestamp()


#: 通知正文里最多列举几个任务名；超出只说总数（锁屏通知放不下整条流水线）。
_NOTIFY_TASK_LIMIT = 6


def _elapsed_seconds(
    started_at: datetime | None, finished_at: datetime | None
) -> int | None:
    """执行耗时；缺时间戳或时间倒流时返回 ``None``（不编造数字）。"""
    if started_at is None or finished_at is None:
        return None
    seconds = int((finished_at - started_at).total_seconds())
    return seconds if seconds >= 0 else None


def _duration_phrase(seconds: int) -> str:
    minutes, secs = divmod(seconds, 60)
    hours, minutes = divmod(minutes, 60)
    if hours:
        return f"{hours} 小时 {minutes} 分"
    if minutes:
        return f"{minutes} 分 {secs} 秒"
    return f"{secs} 秒"


def build_pipeline_notification(
    pipeline: Pipeline,
    tasks: Sequence[Task],
    status: PipelineStatus | None = None,
) -> tuple[NotifyEvent, dict[str, Any]]:
    """流水线终态通知的事件与载荷（docs/09 §13.1 的保底层）。

    标题写结果、正文写执行状态：任务清单、成功/失败计数、耗时与失败原因。全部
    用人话，不出现内部标识符（与 Web Push 的确认标题同一条原则）。``status`` 用于
    内核崩溃抢占终态的场景：那里数据库行已经是 FAILED，而调用点手上还是原始终态。
    """
    final = (
        PipelineStatus(status)
        if status is not None
        else PipelineStatus(pipeline.status)
    )
    labels = [
        TASK_LABELS.get(str(task.type_name), str(task.type_name)) for task in tasks
    ]
    if len(labels) > _NOTIFY_TASK_LIMIT:
        listed = "、".join(labels[:_NOTIFY_TASK_LIMIT]) + f" 等 {len(labels)} 项"
    else:
        listed = "、".join(labels)

    counts = {
        state: sum(TaskStatus(task.status) is state for task in tasks)
        for state in (
            TaskStatus.COMPLETED,
            TaskStatus.FAILED,
            TaskStatus.SKIPPED,
            TaskStatus.CANCELLED,
        )
    }
    total = len(tasks)
    lines: list[str] = []
    if listed:
        # 流水线名已经在通知标题里，正文不重复；各通道都会把标题与正文拼起来发。
        lines.append(f"任务：{listed}")
    if total:
        if counts[TaskStatus.COMPLETED] == total:
            lines.append(f"结果：{total} 项全部成功")
        else:
            summary = f"结果：{counts[TaskStatus.COMPLETED]}/{total} 成功"
            for state, phrase in (
                (TaskStatus.FAILED, "失败"),
                (TaskStatus.SKIPPED, "跳过"),
                (TaskStatus.CANCELLED, "未执行"),
            ):
                if counts[state]:
                    summary += f"，{counts[state]} 项{phrase}"
            lines.append(summary)
    elapsed = _elapsed_seconds(pipeline.started_at, pipeline.finished_at)
    if elapsed is not None:
        lines.append(f"用时：{_duration_phrase(elapsed)}")
    if final is PipelineStatus.FAILED:
        reason = str(pipeline.error_message or pipeline.error_code or "未知原因")
        if pipeline.error_message and pipeline.error_code:
            reason += f"（{pipeline.error_code}）"
        lines.append(f"失败原因：{reason}")

    label = "任务执行失败" if final is PipelineStatus.FAILED else "任务执行完成"
    payload: dict[str, Any] = {
        "title": f"{label}：{pipeline.title}" if pipeline.title else label,
        "message": "\n".join(lines) or label,
        "pipeline_id": str(pipeline.id),
        "pipeline_status": final.value,
        "pipeline_title": pipeline.title or "",
        "source": str(pipeline.source),
        "task_count": total or int(pipeline.task_count or 0),
        "completed_tasks": counts[TaskStatus.COMPLETED],
        "failed_tasks": counts[TaskStatus.FAILED],
        "skipped_tasks": counts[TaskStatus.SKIPPED],
        "cancelled_tasks": counts[TaskStatus.CANCELLED],
        "tasks": labels,
    }
    if elapsed is not None:
        payload["duration"] = _duration_phrase(elapsed)
        payload["duration_seconds"] = elapsed
    if final is PipelineStatus.FAILED:
        payload["error_code"] = str(pipeline.error_code or "")
        payload["error_message"] = str(pipeline.error_message or "")
    event = (
        NotifyEvent.PIPELINE_FAILED
        if final is PipelineStatus.FAILED
        else NotifyEvent.PIPELINE_COMPLETED
    )
    return event, payload
