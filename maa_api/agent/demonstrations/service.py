"""示教录制与转换服务（M89 规格 §4.2、§4.5）。

一条 ``recording`` 行对应一次网页示教；进程内 ``_active_id`` 是「现在正在录」
的唯一标记，设备由 :class:`DeviceLeaseService` 的录制闸门占用（不写
``device_lease`` 表）。状态机见 spec §4.5：

- ``start`` 插入 ``recording``；同一时刻只允许一行；
- ``stop`` 有步骤置 ``recorded``，零步或显式丢弃置 ``discarded``；
- 进程启动清理与设备断开都走 ``discarded``；``converted`` / ``discarded`` 是终态。

``convert`` 依赖 M89 的编译器与批量注册，不属本模块的 Task 2 范围，由后续任务
在同一个类上补实现。
"""

from __future__ import annotations

import asyncio
import logging
from collections.abc import Mapping
from datetime import UTC, datetime
from typing import TYPE_CHECKING, Any

from PIL import Image
from sqlalchemy import func, select

from maa_api.agent.lease import DeviceLeaseService
from maa_api.db.models import Demonstration, utcnow
from maa_api.db.repositories.pipeline import PipelineRepository
from maa_api.domain.enums import (
    DemonstrationStatus,
    ScreenshotBackend,
    ScreenshotTrigger,
)
from maa_api.domain.errors import AppError, ErrorCode
from maa_api.services.screenshot_archive import archive_screenshot

if TYPE_CHECKING:  # 只为类型标注：避免服务层之间的循环导入
    from maa_api.services.resource_service import ResourceService

logger = logging.getLogger(__name__)

#: 一次示教最多保留的步骤数；第 100 步写入后自动停止并置 ``recorded``。
MAX_DEMONSTRATION_STEPS = 100

#: ``delay_ms`` 的上下界（spec §4.1）。
MAX_STEP_DELAY_MS = 10_000

#: 步骤里与动作无关的整数字段；不适用的写 ``None``。
_POINT_FIELDS = ("x", "y", "x1", "y1", "x2", "y2")

_STATUS_VALUES = frozenset(status.value for status in DemonstrationStatus)


class DemonstrationService:
    """示教录制的状态机；API 层、设备端点采集与 T6 的装配共用。"""

    def __init__(
        self,
        session_factory: Any,
        *,
        device_manager: Any,
        lease: DeviceLeaseService,
        resource_service: ResourceService,
    ) -> None:
        self._session_factory = session_factory
        self._device_manager = device_manager
        self._lease = lease
        self._resource_service = resource_service
        #: 包住整个开始流程（查重、开闸门、查流水线、插行）。
        self._start_lock = asyncio.Lock()
        #: 串行步骤追加，并让 stop/abort/recover 与追加互斥。
        self._append_lock = asyncio.Lock()
        self._active_id: str | None = None

    # ---------------------------------------------------------------- 状态读取

    @property
    def active_id(self) -> str | None:
        """当前正在录的示教 id；没有录制时为 ``None``。"""
        return self._active_id

    def is_recording(self) -> bool:
        """是否有一次正在进行的录制（进程内标记，不查库）。"""
        return self._active_id is not None

    # ---------------------------------------------------------------- 生命周期

    async def recover(self) -> int:
        """进程启动清理：把遗留的 ``recording`` 行全部置 ``discarded``。

        只改 ``recording`` 行，``recorded`` / ``converted`` / ``discarded`` 不动；
        清内存活动标记并释放录制闸门。返回被清理的行数。
        """
        async with self._append_lock:
            changed = 0
            now = utcnow()
            async with self._session_factory() as session:
                result = await session.execute(
                    select(Demonstration).where(
                        Demonstration.status == DemonstrationStatus.RECORDING
                    )
                )
                rows = list(result.scalars().all())
                for row in rows:
                    row.status = DemonstrationStatus.DISCARDED
                    row.updated_at = now
                if rows:
                    await session.commit()
                changed = len(rows)
            self._active_id = None
            await self._lease.set_recording(False)
            if changed:
                logger.info("启动清理丢弃了 %d 条未结束的示教录制", changed)
            return changed

    async def start(self, *, title: str | None = None) -> dict[str, Any]:
        """开始一次录制；返回 ``DemonstrationStartOut`` 形状的 wire（含 ``screen``）。"""
        async with self._start_lock:
            async with self._session_factory() as session:
                existing = await self._find_recording(session)
            if existing is not None:
                raise self._state_invalid(existing, "start")

            snapshot = self._device_manager.snapshot()
            width, height = self._resolution(snapshot)
            if snapshot.get("state") != "connected" or width <= 0 or height <= 0:
                raise AppError(
                    ErrorCode.DEVICE_NOT_CONNECTED,
                    "设备未连接或分辨率未知，无法开始示教",
                    {"state": snapshot.get("state")},
                )

            await self._lease.set_recording(True)
            try:
                async with self._session_factory() as session:
                    running = await PipelineRepository(session).current()
                if running is not None:
                    await self._lease.set_recording(False)
                    raise AppError(
                        ErrorCode.PIPELINE_ALREADY_RUNNING,
                        "流水线运行期间不能开始示教录制",
                        {"pipeline_id": running.id},
                    )

                row = Demonstration(
                    title=self._default_title(title),
                    status=DemonstrationStatus.RECORDING,
                )
                try:
                    async with self._session_factory() as session:
                        session.add(row)
                        await session.commit()
                except BaseException:
                    await self._lease.set_recording(False)
                    raise
            except BaseException:
                # 上面单独回滚过的分支不会再走到这里；这里兜住其余异常。
                if self._lease.recording():
                    await self._lease.set_recording(False)
                raise

            self._active_id = row.id
            try:
                await self._lease.preempt(
                    "rest_atomic", detail="用户开始示教录制"
                )
            except Exception:  # noqa: BLE001 - 抢占失败不能回滚已经开始的录制
                logger.exception("开始示教时抢占设备租约失败")
            return self._wire(row, screen=[width, height])

    async def stop(
        self, demonstration_id: str, *, discard: bool = False
    ) -> dict[str, Any]:
        """停止录制：有步骤置 ``recorded``，零步或 ``discard`` 置 ``discarded``。"""
        async with self._append_lock:
            async with self._session_factory() as session:
                row = await session.get(Demonstration, demonstration_id)
                if row is None:
                    raise self._not_found(demonstration_id)
                if row.status != DemonstrationStatus.RECORDING:
                    raise self._state_invalid(row, "stop")
                row.status = (
                    DemonstrationStatus.DISCARDED
                    if discard or not row.steps
                    else DemonstrationStatus.RECORDED
                )
                row.updated_at = utcnow()
                await session.commit()
                data = self._wire(row)
            if self._active_id == demonstration_id:
                self._active_id = None
            await self._lease.set_recording(False)
            return data

    async def abort(self, reason: str) -> None:
        """丢弃当前活动录制（设备断开等）；没有活动录制时只释放闸门。"""
        async with self._append_lock:
            target = self._active_id
            async with self._session_factory() as session:
                row = (
                    await session.get(Demonstration, target)
                    if target is not None
                    else await self._find_recording(session)
                )
                if row is not None and row.status == DemonstrationStatus.RECORDING:
                    row.status = DemonstrationStatus.DISCARDED
                    row.updated_at = utcnow()
                    await session.commit()
                    if self._active_id == row.id:
                        self._active_id = None
                    logger.info("示教录制中止（%s），已丢弃 id=%s", reason, row.id)
            await self._lease.set_recording(False)

    # ---------------------------------------------------------------- 采集追加

    async def note_step(
        self,
        demonstration_id: str,
        *,
        action: str,
        points: Mapping[str, int | None],
        duration_ms: int | None,
        started_at: datetime,
        before: Image.Image | None,
        after: Image.Image | None,
        backend: str,
    ) -> dict[str, Any] | None:
        """追加一步；``at`` 取本步开始时间，``delay_ms`` 取与上一步的毫秒差。

        两张图在同一个会话里归档，单张失败对应 id 写 ``None``、步骤照常追加。
        第 100 步提交后自动停止并释放闸门。找不到 id 抛 404，状态不是
        ``recording`` 抛 409。
        """
        async with self._append_lock:
            async with self._session_factory() as session:
                row = await session.get(Demonstration, demonstration_id)
                if row is None:
                    raise self._not_found(demonstration_id)
                if row.status != DemonstrationStatus.RECORDING:
                    raise self._state_invalid(row, "note_step")

                steps = list(row.steps or [])
                started = self._as_utc(started_at)
                screenshot_id = await self._archive(session, before, backend)
                result_screenshot_id = await self._archive(session, after, backend)
                steps.append(
                    {
                        "seq": len(steps) + 1,
                        "action": action,
                        **{field: points.get(field) for field in _POINT_FIELDS},
                        "duration_ms": duration_ms,
                        "delay_ms": self._delay_ms(started, steps),
                        "screen": self._screen(),
                        "screenshot_id": screenshot_id,
                        "result_screenshot_id": result_screenshot_id,
                        "at": started.isoformat().replace("+00:00", "Z"),
                    }
                )
                row.steps = steps
                row.updated_at = utcnow()
                finished = len(steps) >= MAX_DEMONSTRATION_STEPS
                if finished:
                    row.status = DemonstrationStatus.RECORDED
                await session.commit()
                data = self._wire(row)

            if finished:
                self._active_id = None
                await self._lease.set_recording(False)
            return data

    # ---------------------------------------------------------------- 列表

    async def list(
        self, *, status: str | None, page: int, size: int
    ) -> dict[str, Any]:
        """按 ``created_at DESC, id DESC`` 分页；``status`` 过滤非法值报 400。"""
        if status is not None and status not in _STATUS_VALUES:
            raise AppError(
                ErrorCode.INVALID_PARAMETER,
                "示教状态取值无效",
                {"status": status},
            )
        page = max(int(page), 1)
        size = max(int(size), 1)
        conditions = (
            [Demonstration.status == status] if status is not None else []
        )
        async with self._session_factory() as session:
            total_result = await session.execute(
                select(func.count()).select_from(Demonstration).where(*conditions)
            )
            total = int(total_result.scalar_one())
            result = await session.execute(
                select(Demonstration)
                .where(*conditions)
                .order_by(Demonstration.created_at.desc(), Demonstration.id.desc())
                .offset((page - 1) * size)
                .limit(size)
            )
            items = [self._wire(row) for row in result.scalars().all()]
        return {"items": items, "total": total, "page": page, "size": size}

    # ---------------------------------------------------------------- 内部工具

    async def _find_recording(self, session: Any) -> Demonstration | None:
        result = await session.execute(
            select(Demonstration)
            .where(Demonstration.status == DemonstrationStatus.RECORDING)
            .order_by(Demonstration.created_at.desc(), Demonstration.id.desc())
            .limit(1)
        )
        return result.scalars().first()

    async def _archive(
        self, session: Any, image: Image.Image | None, backend: str
    ) -> str | None:
        """归档一张截图并返回 id；没有图或归档失败时返回 ``None``。"""
        if image is None:
            return None
        try:
            _row, meta = await archive_screenshot(
                session,
                image,
                trigger=ScreenshotTrigger.MANUAL,
                backend=ScreenshotBackend(backend),
            )
            return str(meta["screenshot_id"])
        except Exception:  # noqa: BLE001 - 截图失败不能拖垮已经完成的设备动作
            logger.warning("示教步骤截图归档失败，id 记 null", exc_info=True)
            return None

    def _screen(self) -> list[int]:
        """当前设备像素分辨率；未知时写 ``[0, 0]``（begin 已先挡掉未知）。"""
        width, height = self._resolution(self._device_manager.snapshot())
        return [width, height]

    @staticmethod
    def _resolution(snapshot: Mapping[str, Any]) -> tuple[int, int]:
        resolution = snapshot.get("resolution") or {}
        return int(resolution.get("width") or 0), int(resolution.get("height") or 0)

    @staticmethod
    def _as_utc(value: datetime) -> datetime:
        """库内与采集都用 naive UTC；外部若给 aware 值先归一。"""
        if value.tzinfo is None:
            return value.replace(tzinfo=UTC)
        return value.astimezone(UTC)

    @staticmethod
    def _delay_ms(started_at: datetime, steps: list[dict[str, Any]]) -> int:
        """与上一步开始时间的毫秒差，夹 0–10000；第一步为 0。"""
        if not steps:
            return 0
        previous = steps[-1].get("at")
        if not isinstance(previous, str):
            return 0
        try:
            previous_at = datetime.fromisoformat(previous.replace("Z", "+00:00"))
        except ValueError:
            return 0
        if previous_at.tzinfo is None:
            previous_at = previous_at.replace(tzinfo=UTC)
        delta = int((started_at - previous_at).total_seconds() * 1000)
        return max(0, min(delta, MAX_STEP_DELAY_MS))

    @staticmethod
    def _default_title(title: str | None) -> str:
        """未给或为空时用服务端本地时间的「示教 MM-DD HH:MM」。"""
        cleaned = (title or "").strip()
        if cleaned:
            return cleaned[:64]
        return f"示教 {datetime.now().strftime('%m-%d %H:%M')}"

    def _wire(
        self, row: Demonstration, *, screen: list[int] | None = None
    ) -> dict[str, Any]:
        data: dict[str, Any] = {
            "id": row.id,
            "title": row.title,
            "status": DemonstrationStatus(row.status),
            "steps": list(row.steps or []),
            "converted_to": row.converted_to,
            "created_at": row.created_at,
            "updated_at": row.updated_at,
        }
        if screen is not None:
            data["screen"] = list(screen)
        return data

    @staticmethod
    def _not_found(demonstration_id: str) -> AppError:
        return AppError(
            ErrorCode.DEMONSTRATION_NOT_FOUND,
            "示教不存在",
            {"id": demonstration_id},
        )

    @staticmethod
    def _state_invalid(row: Demonstration, action: str) -> AppError:
        return AppError(
            ErrorCode.DEMONSTRATION_STATE_INVALID,
            f"示教状态为 {row.status}，不能执行 {action}",
            {
                "id": row.id,
                "status": DemonstrationStatus(row.status),
                "action": action,
            },
        )


__all__ = ["DemonstrationService", "MAX_DEMONSTRATION_STEPS"]
