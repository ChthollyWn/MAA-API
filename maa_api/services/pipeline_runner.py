"""Single-consumer, callback-driven pipeline execution (docs/02 §5, docs/12 M5)."""

from __future__ import annotations

import asyncio
import logging
import time
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from typing import Any, Callable

from pydantic import ValidationError
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from maa_api.core.enums import Message
from maa_api.core.registry import CoreRegistry, DEFAULT_CORE_ID
from maa_api.db.models import Pipeline, Task, utcnow
from maa_api.db.repositories.pipeline import PipelineRepository, TaskRepository
from maa_api.domain.enums import PipelineSource, PipelineStatus, TaskStatus
from maa_api.domain.errors import AppError, ErrorCode
from maa_api.domain.task import RUNTIME_IMMUTABLE, TASK_MODELS
from maa_api.services.log_hub import current_pipeline_id

__all__ = ["TASK_TIMEOUT_SECONDS", "PipelineRunner"]

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
    cancel_requested: bool = False
    cancel_event: asyncio.Event = field(default_factory=asyncio.Event)
    crash_event: asyncio.Event = field(default_factory=asyncio.Event)
    core_crashed: bool = False
    stop_complete: asyncio.Event = field(default_factory=asyncio.Event)
    attempt: _Attempt | None = None
    task: Task | None = None

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
        broadcast: Callable[[str, dict[str, Any]], None] | None = None,
        task_timeouts: dict[str, float] | None = None,
    ) -> None:
        self.registry = registry
        self.supervisor = supervisor
        self._session_factory = session_factory
        self.core_id = core_id
        self.queue_service = queue_service
        self.device_manager = device_manager
        self._broadcast = broadcast or (lambda _kind, _data: None)
        self.task_timeouts = dict(TASK_TIMEOUT_SECONDS)
        if task_timeouts:
            self.task_timeouts.update(task_timeouts)

        self.operation_lock = asyncio.Lock()
        self._wake_event = asyncio.Event()
        self._worker: asyncio.Task[None] | None = None
        self._running = False
        self._active: _ActivePipeline | None = None
        self._cancel_requested_ids: set[str] = set()
        self._loop: asyncio.AbstractEventLoop | None = None

        client = self.registry.get(self.core_id)
        client.on("CALLBACK", self._on_callback)
        client.on("FATAL", self._on_fatal)

    @property
    def running(self) -> bool:
        return self._running

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
                    model.model_validate(
                        {**normalized_existing, **normalized_patch, "name": task.type_name}
                    )
                    patch_model = model.model_validate(
                        {**normalized_patch, "name": task.type_name}
                    )
                except ValidationError as exc:
                    raise AppError(
                        ErrorCode.TASK_PARAM_INVALID,
                        "运行中任务参数不符合任务 schema",
                        {"issues": exc.errors(include_input=False, include_context=False)},
                    ) from exc

                normalized_patch = patch_model.model_dump(
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
            async with self._session_factory() as session:
                repo = PipelineRepository(session)
                pipeline = await repo.claim_next(
                    self.core_id,
                    core_epoch=getattr(self.supervisor, "generation", None),
                )
                if pipeline is None:
                    return None, []
                tasks = await TaskRepository(session).list_by_pipeline(pipeline.id)
                await session.commit()
                return pipeline, tasks

    async def _execute_pipeline(
        self, pipeline: Pipeline, tasks: list[Task]
    ) -> None:
        active = _ActivePipeline(pipeline_id=pipeline.id)
        self._active = active
        log_context = current_pipeline_id.set(pipeline.id)
        had_failures = False
        last_task_error: AppError | None = None
        fatal_error: AppError | None = None
        cancelled = (
            not self._running or pipeline.id in self._cancel_requested_ids
        )
        self._cancel_requested_ids.discard(pipeline.id)
        try:
            if cancelled:
                await self._cancel_pipeline_rows(pipeline.id)
                return
            if self.device_manager is not None:
                try:
                    available = await self.device_manager.ensure_available(timeout=10.0)
                except asyncio.CancelledError:
                    raise
                except Exception:
                    logger.exception(
                        "流水线设备预检异常：pipeline_id=%s", pipeline.id
                    )
                    available = False
                if not available:
                    handled = await self._handle_device_unavailable(pipeline)
                    if handled:
                        return
                    cancelled = True
            for task in tasks:
                if active.cancel_requested or pipeline.id in self._cancel_requested_ids:
                    cancelled = True
                    break
                outcome, error = await self._execute_task(active, task)
                if outcome == "cancelled":
                    cancelled = True
                    break
                if outcome == "crashed":
                    fatal_error = error or AppError(
                        ErrorCode.CORE_CRASHED,
                        "MaaCore 子进程中断，流水线已停止",
                    )
                    break
                if outcome == "skipped":
                    had_failures = True
                    last_task_error = error

            if cancelled or active.cancel_requested:
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
            self._active = None
            self._cancel_requested_ids.discard(pipeline.id)
            current_pipeline_id.reset(log_context)
            await self.publish_pipeline(pipeline.id)
            await self.publish_queue_changed()

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
            attempt: _Attempt | None = None
            try:
                maa_task_id = int(
                    await self._await_core_command(
                        active, client.append_task(task.type_name, task.params)
                    )
                )
                if maa_task_id <= 0:
                    raise AppError(
                        ErrorCode.CORE_COMMAND_FAILED,
                        f"MaaCore 未接受任务 {task.type_name}",
                        {"cmd": "APPEND_TASK", "task_id": task.id},
                    )
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
                await pipeline_repo.mark_terminal(
                    pipeline.id,
                    PipelineStatus.FAILED,
                    error_code=ErrorCode.DEVICE_NOT_CONNECTED,
                    error_message=error.message,
                )
                await session.commit()
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

    def _apply_core_crash(self, record: dict[str, Any] | None) -> None:
        active = self._active
        if active is not None:
            active.core_crashed = True
            active.crash_event.set()
            if active.attempt is not None:
                future = active.attempt.future
                if not future.done():
                    future.set_result("crashed")
        self.wake()

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
        async with self._session_factory() as session:
            repo = PipelineRepository(session)
            await repo.mark_terminal(
                pipeline_id,
                status,
                error_code=error_code,
                error_message=error_message,
            )
            await session.commit()

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
