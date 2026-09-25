"""Agent-specific MaaCore maintenance actions.

``AgentOpsService`` shares ``UpdateService``'s admission lock, drains and pauses
the normal queue, then enters ``CoreSupervisor.acquire_maintenance()`` before a
manual restart. Its constructor dependencies are provided by the application
lifespan; no tool calls ``CoreClient`` or MaaCore IPC directly.
"""

from __future__ import annotations

import asyncio
import inspect
from typing import Any, Callable

from maa_api.db.repositories.pipeline import PipelineRepository
from maa_api.domain.errors import AppError, ErrorCode


class AgentOpsService:
    """Serialize explicit core restarts against updates and queued pipelines.

    ``session_factory`` opens sessions for queue-idle checks. Optional
    ``reconnect`` runs after the Supervisor reaches READY, typically using the
    lifespan's ``DeviceManager`` adapter.
    """

    def __init__(
        self,
        *,
        core_supervisor: Any,
        update_service: Any,
        queue_service: Any,
        pipeline_runner: Any,
        session_factory: Callable[[], Any],
        reconnect: Callable[[], Any] | None = None,
        idle_timeout_seconds: float = 30 * 60,
        poll_interval: float = 0.1,
    ) -> None:
        self.core_supervisor = core_supervisor
        self.update_service = update_service
        self.queue_service = queue_service
        self.pipeline_runner = pipeline_runner
        self.session_factory = session_factory
        self.reconnect = reconnect
        self.idle_timeout_seconds = max(float(idle_timeout_seconds), 0.0)
        self.poll_interval = max(float(poll_interval), 0.01)
        self._owns_queue_pause = False

    async def restart_core(self, *, caller: str = "agent") -> dict[str, Any]:
        """Restart the core after the queue drains, returning a stable summary."""
        del caller  # Reserved for audit attribution at the common tool boundary.
        update_lock = self.update_service._lock
        if update_lock.locked():
            raise AppError(ErrorCode.UPDATE_ALREADY_RUNNING, "已有更新或维护操作正在执行")

        # With asyncio.Lock, this acquisition does not yield while the lock is
        # free. The locked check therefore fails fast if an update owns it.
        await update_lock.acquire()
        try:
            running_update = await self.update_service._running_record()
            if running_update is not None:
                raise self.update_service._busy_error(running_update)
            await self._drain_and_pause_queue()
            async with self.core_supervisor.acquire_maintenance():
                await self.core_supervisor.restart()
            if self.reconnect is not None:
                result = self.reconnect()
                if inspect.isawaitable(result):
                    await result
            state = getattr(self.core_supervisor, "state", None)
            generation = getattr(self.core_supervisor, "generation", None)
            return {
                "status": str(getattr(state, "value", state or "ready")),
                "generation": generation,
            }
        finally:
            try:
                if self._owns_queue_pause:
                    await self.queue_service.resume()
                    self._owns_queue_pause = False
            finally:
                if update_lock.locked():
                    update_lock.release()

    async def _drain_and_pause_queue(self) -> bool:
        loop = asyncio.get_running_loop()
        deadline = loop.time() + self.idle_timeout_seconds
        changed_pause = False
        while True:
            active = getattr(self.pipeline_runner, "_active", None)
            operation_lock = getattr(self.pipeline_runner, "operation_lock", None)
            operation_busy = bool(operation_lock is not None and operation_lock.locked())
            running, pending = await self._pipeline_counts()
            idle = active is None and running is None and pending == 0 and not operation_busy
            if idle:
                if self.queue_service.paused:
                    return False
                # Record ownership before awaiting. QueueService.pause() can
                # mutate the paused flag and then raise or be cancelled before
                # returning its ``changed`` result.
                self._owns_queue_pause = True
                changed_pause = await self.queue_service.pause()
                self._owns_queue_pause = changed_pause
                active = getattr(self.pipeline_runner, "_active", None)
                operation_busy = bool(operation_lock is not None and operation_lock.locked())
                running, pending = await self._pipeline_counts()
                if active is None and running is None and pending == 0 and not operation_busy:
                    return changed_pause
                if changed_pause:
                    await self.queue_service.resume()
                    self._owns_queue_pause = False
                    changed_pause = False
            elif self.queue_service.paused and pending:
                raise AppError(
                    ErrorCode.UPDATE_BLOCKED_BY_PIPELINE,
                    "定时任务队列已暂停且仍有待执行流水线",
                    {"pending": pending},
                )

            if loop.time() >= deadline:
                raise AppError(
                    ErrorCode.UPDATE_BLOCKED_BY_PIPELINE,
                    "等待流水线队列空闲超时，未重启 MaaCore",
                    {"running_pipeline_id": getattr(running, "id", None), "pending": pending},
                )
            await asyncio.sleep(self.poll_interval)

    async def _pipeline_counts(self) -> tuple[Any, int]:
        core_id = getattr(self.pipeline_runner, "core_id", "default")
        async with self.session_factory() as session:
            repository = PipelineRepository(session)
            running = await repository.current(core_id)
            pending = await repository.count_pending(core_id)
        return running, pending
