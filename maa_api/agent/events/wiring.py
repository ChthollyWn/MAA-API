"""事件源接缝的适配器（M81 规格 §4.2、§4.5.4）。

本模块只做「外部状态 → ``TriggerService.publish``」的翻译，不含过滤与路由：

- :class:`PipelineEventPublisher`：``FinishHookFanout`` 的订阅者与诊断入口共用的
  流水线事件发布器；``publish_failure`` 还负责把迟到的 ``CORE_CRASHED`` 改判补成
  ``pipeline.core_crash_linked``。
- :class:`DeviceStatePublisher`：设备状态只在变化时发事件，第一次收到只记状态。
- :func:`publish_core_failed`：``CoreSupervisor`` 状态变为 ``failed`` 时发
  ``core.failed``（自动重启次数用完）。
- :func:`on_update_broadcast`：``UpdateService`` 的 ``update_available`` 广播拆成
  每个目标一条事件。
- :class:`SanityWatcher`：每 30 分钟按最近一条理智采样估算，满了发
  ``system.sanity_full``；job 注册在 M81 T7。

所有同步接缝都用 ``TriggerService.publish_nowait``：调度器、设备与内核回调都在
事件循环线程上，但不能为了写事件把调用方阻塞在研判/建单上。
"""

from __future__ import annotations

import logging
from collections.abc import Callable, Mapping
from datetime import UTC, datetime, timedelta
from typing import TYPE_CHECKING, Any

from sqlalchemy import select

from maa_api.db.models import (
    AgentSession,
    Pipeline,
    SanityObservation,
    Task,
    utcnow,
)
from maa_api.db.repositories.triggers import TriggerRepository
from maa_api.domain.enums import EventSource
from maa_api.domain.errors import AppError, ErrorCode

if TYPE_CHECKING:  # pragma: no cover - 只给类型检查用，避免运行时循环导入
    from maa_api.agent.events.bus import PublishResult, TriggerService

__all__ = [
    "DeviceStatePublisher",
    "PipelineEventPublisher",
    "SanityWatcher",
    "core_crash_payload",
    "on_update_broadcast",
    "publish_core_failed",
]

logger = logging.getLogger(__name__)

#: 08 §2.1：错误信息类字符串截到 500 字。
MAX_ERROR_CHARS = 500
#: 流水线公共 payload 的任务上限（08 §2.2）。
MAX_PAYLOAD_TASKS = 10
#: 理智估算的换算：每 6 分钟回 1 点（§4.5.5）。
SANITY_MINUTES_PER_POINT = 6
#: 理智采样的新鲜度窗口：超过 24 小时不再估算（§4.5.5）。
SANITY_FRESH_WINDOW = timedelta(hours=24)
#: 设备与内核事件的固定话题前缀（03 §15）。
DEFAULT_CORE_ID = "default"


def _iso_z(value: datetime | None) -> str | None:
    """库内 UTC naive 时间 → ``...Z`` 的 ISO 时间；``None`` 原样返回。"""
    if value is None:
        return None
    if value.tzinfo is None:
        value = value.replace(tzinfo=UTC)
    return value.astimezone(UTC).isoformat().replace("+00:00", "Z")


def _utc_naive(value: datetime) -> datetime:
    """把可能带时区的时间统一成库内的 UTC naive。"""
    if value.tzinfo is None:
        return value
    return value.astimezone(UTC).replace(tzinfo=None)


def _text(value: Any, limit: int = MAX_ERROR_CHARS) -> str | None:
    """错误信息类字段：``None`` 保留，其余转字符串并截断。"""
    if value is None:
        return None
    return str(value)[:limit]


def _status_text(value: Any) -> str | None:
    """``PipelineStatus`` / 字符串 / ``None`` 归一成小写状态文本。"""
    if value is None:
        return None
    return str(getattr(value, "value", value))


class PipelineEventPublisher:
    """流水线终态与失败的路由适配器（§4.2、§4.5.4）。"""

    def __init__(
        self,
        trigger_service: "TriggerService",
        session_factory: Any,
        *,
        thread_router: Any = None,
    ) -> None:
        self._trigger_service = trigger_service
        self._session_factory = session_factory
        #: M70 的终态钩子在订阅建立之前可能先触发（提交即结束的流水线）；
        #: 装配层传入 ThreadRouter，发布前补一次订阅（幂等），避免事件没人接。
        self._thread_router = thread_router

    async def build_payload(self, pipeline_id: str) -> dict[str, Any] | None:
        """按 08 §2.2 的公共字段读流水线；行不存在返回 ``None``。"""
        async with self._session_factory() as session:
            pipeline = await session.get(Pipeline, pipeline_id, populate_existing=True)
            if pipeline is None:
                return None
            tasks = (
                (
                    await session.execute(
                        select(Task)
                        .where(Task.pipeline_id == pipeline_id)
                        .order_by(Task.order_index)
                    )
                )
                .scalars()
                .all()
            )
            ticket_id: str | None = None
            if pipeline.agent_session_id:
                agent_session = await session.get(
                    AgentSession, pipeline.agent_session_id
                )
                if agent_session is not None:
                    ticket_id = agent_session.ticket_id
            return {
                "pipeline_id": pipeline.id,
                "title": pipeline.title,
                "source": str(pipeline.source),
                "status": str(pipeline.status),
                "error_code": pipeline.error_code,
                "error_message": _text(pipeline.error_message),
                "schedule_id": pipeline.schedule_id,
                "ticket_id": ticket_id,
                "started_at": _iso_z(pipeline.started_at),
                "finished_at": _iso_z(pipeline.finished_at),
                "tasks": [
                    {
                        "name": task.task_name,
                        "type": task.type_name,
                        "status": str(task.status),
                        "error_code": task.error_code,
                    }
                    for task in tasks[:MAX_PAYLOAD_TASKS]
                ],
            }

    async def on_pipeline_finished(
        self, pipeline_id: str, status: Any = None
    ) -> None:
        """``FinishHookFanout`` 订阅者：终态写成 ``pipeline.<status>``。"""
        status_text = _status_text(status)
        if status_text is None:
            payload = await self.build_payload(pipeline_id)
            if payload is None:
                return
            status_text = str(payload.get("status"))
        if status_text == "failed":
            await self.publish_failure(pipeline_id)
            return
        if status_text not in ("completed", "cancelled"):
            return
        await self._ensure_owner_subscription(pipeline_id)
        payload = await self.build_payload(pipeline_id)
        if payload is None:
            return
        await self._trigger_service.publish(
            source=EventSource.PIPELINE,
            kind=f"pipeline.{status_text}",
            thread_key=f"pipeline:{pipeline_id}",
            payload=payload,
            dedupe_key=f"pipeline:{pipeline_id}:{status_text}",
        )

    async def publish_failure(self, pipeline_id: str) -> "PublishResult | None":
        """发 ``pipeline.failed``；重复且改判为内核崩溃时补一条关联事件。"""
        await self._ensure_owner_subscription(pipeline_id)
        payload = await self.build_payload(pipeline_id)
        if payload is None:
            return None
        result = await self._trigger_service.publish(
            source=EventSource.PIPELINE,
            kind="pipeline.failed",
            thread_key=f"pipeline:{pipeline_id}",
            payload=payload,
            dedupe_key=f"pipeline:{pipeline_id}:failed",
        )
        if not result.duplicate:
            return result
        if payload.get("error_code") != ErrorCode.CORE_CRASHED.value:
            return result
        if await self._existing_failure_is_core_crash(result.trigger_id):
            return result
        generation = await self._core_generation(pipeline_id)
        if generation is None:
            return result
        await self._trigger_service.publish(
            source=EventSource.PIPELINE,
            kind="pipeline.core_crash_linked",
            thread_key=f"pipeline:{pipeline_id}",
            payload={
                "pipeline_id": pipeline_id,
                "core_generation": int(generation),
                "error_message": payload.get("error_message"),
            },
            dedupe_key=f"pipeline:{pipeline_id}:core:{int(generation)}",
        )
        return result

    async def _ensure_owner_subscription(self, pipeline_id: str) -> None:
        """M70 终态钩子的竞态兜底：发布前把提交者工单订阅到 ``pipeline:<id>``。

        订阅通常由 Worker 的 ``pipeline_submitted`` 事件异步建立；提交即结束的
        流水线会先触发终态钩子。M81 用事件服务替换了 ``ThreadRouter`` 订阅者，
        这里补上同一份兜底（重复订阅幂等）。
        """
        if self._thread_router is None:
            return
        async with self._session_factory() as session:
            pipeline = await session.get(Pipeline, pipeline_id)
            if pipeline is None or not pipeline.agent_session_id:
                return
            owner = await session.get(AgentSession, pipeline.agent_session_id)
            ticket_id = owner.ticket_id if owner is not None else None
        if not ticket_id:
            return
        try:
            await self._thread_router.subscribe(
                str(ticket_id), f"pipeline:{pipeline_id}"
            )
        except AppError:
            # 工单已结束或话题不可订阅：终态事件照常按规则/记录处理。
            logger.debug("流水线 %s 的提交工单已不能订阅话题", pipeline_id)
        except Exception:  # noqa: BLE001 - 竞态兜底不能拖垮终态钩子
            logger.warning("流水线 %s 补订阅失败", pipeline_id, exc_info=True)

    async def _existing_failure_is_core_crash(self, trigger_id: int | None) -> bool:
        """已入表的那条失败事件是不是本来就按 ``CORE_CRASHED`` 记的。"""
        if trigger_id is None:
            return True
        async with self._session_factory() as session:
            row = await TriggerRepository(session).get(int(trigger_id))
        if row is None or not isinstance(row.payload, Mapping):
            return False
        return row.payload.get("error_code") == ErrorCode.CORE_CRASHED.value

    async def _core_generation(self, pipeline_id: str) -> int | None:
        async with self._session_factory() as session:
            pipeline = await session.get(Pipeline, pipeline_id)
        if pipeline is None:
            return None
        return pipeline.core_epoch


class DeviceStatePublisher:
    """设备状态变化 → ``device.*``；首次只记状态，重复状态不发（§4.5.4）。"""

    def __init__(self, get_service: Callable[[], "TriggerService | None"]) -> None:
        self._get_service = get_service
        self._last_state: str | None = None

    @property
    def last_state(self) -> str | None:
        return self._last_state

    def on_device_status(self, data: Mapping[str, Any]) -> None:
        """同步接缝：``DeviceManager`` 的 ``broadcast`` 回调。"""
        if not isinstance(data, Mapping):
            return
        state = _status_text(data.get("state"))
        if not state:
            return
        previous = self._last_state
        self._last_state = state
        if previous is None or state == previous:
            return

        if state == "connected":
            kind = "device.connected"
        elif state == "unavailable":
            kind = "device.unavailable"
        elif state in ("disconnected", "reconnecting") and previous == "connected":
            kind = "device.disconnected"
        else:
            return

        core_id = str(data.get("core_id") or DEFAULT_CORE_ID)
        payload: dict[str, Any] = {
            "core_id": core_id,
            "address": data.get("address"),
            "previous_state": previous,
        }
        if kind in ("device.disconnected", "device.unavailable"):
            payload["state"] = state
            last_error = data.get("last_error")
            payload["error_stage"] = (
                last_error.get("stage") if isinstance(last_error, Mapping) else None
            )
        if kind == "device.unavailable":
            retry = data.get("retry")
            payload["retry_attempt"] = (
                retry.get("attempt") if isinstance(retry, Mapping) else None
            )

        service = self._get_service()
        if service is None:
            return
        service.publish_nowait(
            source=EventSource.DEVICE,
            kind=kind,
            thread_key=f"device:{core_id}",
            payload=payload,
        )


def core_crash_payload(
    core_id: str, generation: int, record: Mapping[str, Any] | None
) -> dict[str, Any]:
    """``core.crashed`` 的 payload（08 §2.2 的字段，缺值留 ``None``）。"""
    data = record if isinstance(record, Mapping) else {}
    return {
        "core_id": core_id,
        "generation": int(generation),
        "reason": data.get("reason"),
        "exitcode": data.get("exitcode"),
        "attempt": data.get("attempt"),
        "at": data.get("at"),
        "detail": data.get("detail"),
    }


def publish_core_failed(
    get_service: Callable[[], "TriggerService | None"],
    *,
    supervisor: Any,
    boot_id: str,
) -> None:
    """内核状态变为 ``failed``：发 ``core.failed``（08 §2.2，交总控）。"""
    service = get_service()
    if service is None:
        return
    core_id = str(getattr(supervisor, "core_id", None) or DEFAULT_CORE_ID)
    generation = int(getattr(supervisor, "generation", 0) or 0)
    last_crash = getattr(supervisor, "last_crash", None)
    if isinstance(last_crash, Mapping):
        last_crash = {
            key: (_text(value) if isinstance(value, str) else value)
            for key, value in last_crash.items()
        }
    service.publish_nowait(
        source=EventSource.CORE,
        kind="core.failed",
        thread_key=f"core:{core_id}",
        payload={
            "core_id": core_id,
            "generation": generation,
            "last_crash": last_crash,
        },
        dedupe_key=f"core:{core_id}:{boot_id}:{generation}:failed",
    )


def on_update_broadcast(
    get_service: Callable[[], "TriggerService | None"],
    kind: str,
    data: Mapping[str, Any],
) -> None:
    """``UpdateService`` 的广播 → 每个可更新目标一条 ``system.update_available``。"""
    if kind != "update_available" or not isinstance(data, Mapping):
        return
    service = get_service()
    if service is None:
        return
    for item in data.get("targets") or []:
        if not isinstance(item, Mapping):
            continue
        target = item.get("target")
        latest = item.get("latest")
        if not target or latest is None:
            continue
        channel = item.get("channel")
        service.publish_nowait(
            source=EventSource.SYSTEM,
            kind="system.update_available",
            thread_key="system:update_available",
            payload={
                "target": str(target),
                "channel": channel,
                "current": item.get("current"),
                "latest": latest,
            },
            dedupe_key=f"update_available:{target}:{channel or ''}:{latest}",
        )


class SanityWatcher:
    """理智估算 job 的回调（M81 §4.5.5）；job 注册在 T7。"""

    def __init__(
        self,
        trigger_service: "TriggerService",
        session_factory: Any,
        clock: Callable[[], datetime] = utcnow,
    ) -> None:
        self._trigger_service = trigger_service
        self._session_factory = session_factory
        self._clock = clock

    async def tick(self) -> None:
        """取最近一条采样：24 小时内、估计已满时发一条 ``system.sanity_full``。"""
        async with self._session_factory() as session:
            observation = (
                (
                    await session.execute(
                        select(SanityObservation)
                        .order_by(
                            SanityObservation.created_at.desc(),
                            SanityObservation.id.desc(),
                        )
                        .limit(1)
                    )
                )
                .scalars()
                .first()
            )
        if observation is None:
            return
        observed_at = _utc_naive(observation.created_at)
        now = _utc_naive(self._clock())
        if now - observed_at > SANITY_FRESH_WINDOW:
            return
        elapsed_minutes = max(0, int((now - observed_at).total_seconds() // 60))
        estimated = min(
            observation.max_sanity,
            observation.current_sanity
            + elapsed_minutes // SANITY_MINUTES_PER_POINT,
        )
        if estimated < observation.max_sanity:
            return
        full_at = observed_at + timedelta(
            minutes=SANITY_MINUTES_PER_POINT
            * (observation.max_sanity - observation.current_sanity)
        )
        await self._trigger_service.publish(
            source=EventSource.SYSTEM,
            kind="system.sanity_full",
            thread_key="system:sanity",
            payload={
                "estimated": estimated,
                "max_sanity": observation.max_sanity,
                "observed": observation.current_sanity,
                "observed_at": _iso_z(observed_at),
                "stage_code": observation.stage_code,
                "full_at": _iso_z(full_at),
            },
            dedupe_key=f"sanity_full:{observation.id}",
        )
