"""示教录制与转换服务（M89 规格 §4.2、§4.5）。

一条 ``recording`` 行对应一次网页示教；进程内 ``_active_id`` 是「现在正在录」
的唯一标记，设备由 :class:`DeviceLeaseService` 的录制闸门占用（不写
``device_lease`` 表）。状态机见 spec §4.5：

- ``start`` 插入 ``recording``；同一时刻只允许一行；
- ``stop`` 有步骤置 ``recorded``，零步或显式丢弃置 ``discarded``；
- 进程启动清理与设备断开都走 ``discarded``；``converted`` / ``discarded`` 是终态。

``convert`` 把 ``recorded`` 示教编译成 ``Custom_*`` 链（默认只预览）；``register``
为真时在 ``_convert_lock`` 内一次性注册整条链并置 ``converted``。
"""

from __future__ import annotations

import asyncio
import logging
from collections.abc import Mapping
from datetime import UTC, datetime
from typing import TYPE_CHECKING, Any

from PIL import Image
from sqlalchemy import func, select

from maa_api.agent.custom_task.rules import check_bundle
from maa_api.agent.demonstrations.compiler import (
    CompileError,
    Compiled,
    compile_steps,
    parse_step,
)
from maa_api.agent.lease import DeviceLeaseService
from maa_api.db.models import Demonstration, utcnow
from maa_api.db.repositories.pipeline import PipelineRepository
from maa_api.db.repositories.resource import ResourceAssetRepository
from maa_api.domain.enums import (
    DemonstrationStatus,
    ResourceAssetKind,
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

#: 设备连接状态的后台轮询间隔（spec §4.5 状态表：设备断开 → ``discarded``）。
#: 只在录制期间运行；测试把它压小以免等待两秒。
DEVICE_WATCH_INTERVAL_SECONDS = 2.0

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
        #: 串行转换：重读行、编译、预检与注册在锁内完成（spec §4.5「转换」）。
        self._convert_lock = asyncio.Lock()
        self._active_id: str | None = None
        #: 录制期间每 ``DEVICE_WATCH_INTERVAL_SECONDS`` 查一次设备；断开即 ``abort``。
        self._watch_task: asyncio.Task[None] | None = None

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
            self._cancel_watcher()
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
            self._start_watcher(row.id)
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
        """停止录制：有步骤置 ``recorded``，零步或 ``discard`` 置 ``discarded``。

        只释放仍属于这条示教的闸门：``stop(A)`` 与 ``start(B)`` 交错时不能清掉
        ``B`` 刚打开的闸门，此时按状态冲突返回 409（spec §4.5 边界）。
        """
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
            if not await self._release_gate(demonstration_id):
                raise self._stale_recording(
                    demonstration_id, data["status"], "stop"
                )
            return data

    async def abort(
        self, reason: str, *, demonstration_id: str | None = None
    ) -> bool:
        """丢弃当前活动录制（设备断开等），返回是否释放了录制闸门。

        ``demonstration_id`` 给定时只处理这一条：它与当前活动录制不一致时不动
        闸门、返回 ``False``（慢一步的 ``abort`` 不能清掉新录制刚打开的闸门）。
        没有活动录制时同样不动闸门；闸门的兜底释放由 ``stop``/``recover`` 负责。
        """
        async with self._append_lock:
            target = (
                demonstration_id
                if demonstration_id is not None
                else self._active_id
            )
            if target is None:
                return False
            if self._active_id != target:
                logger.info(
                    "示教录制已切换，忽略过期的 abort（%s） id=%s", reason, target
                )
                return False
            async with self._session_factory() as session:
                row = await session.get(Demonstration, target)
                if row is not None and row.status == DemonstrationStatus.RECORDING:
                    row.status = DemonstrationStatus.DISCARDED
                    row.updated_at = utcnow()
                    await session.commit()
                    logger.info("示教录制中止（%s），已丢弃 id=%s", reason, row.id)
            return await self._release_gate(target)

    # ---------------------------------------------------------------- 闸门与监视

    async def _release_gate(self, demonstration_id: str) -> bool:
        """只释放属于该示教的录制闸门，返回是否真的释放了。

        活动录制已经换人时不动闸门、也不取消对方的监视任务——这是
        ``stop(A)`` 与 ``start(B)`` 交错时的保护（spec §4.5 边界）。
        ``start`` 持 ``_start_lock`` 直到 ``_active_id`` 就位，这里也持同一把锁，
        否则 ``stop(A)`` 可能在 ``start(C)`` 刚打开闸门、还没置活动标记时把 C 的
        闸门关掉（复审复现的 D12 竞态）。
        """
        async with self._start_lock:
            if self._active_id != demonstration_id:
                return False
            self._active_id = None
            self._cancel_watcher()
            await self._lease.set_recording(False)
            return True

    def _start_watcher(self, demonstration_id: str) -> None:
        """起一个后台任务，在录制期间盯着设备连接状态（spec §4.5 状态表）。"""
        self._cancel_watcher()
        self._watch_task = asyncio.create_task(
            self._watch_device(demonstration_id),
            name="maa-demonstration-device-watch",
        )

    def _cancel_watcher(self) -> None:
        """取消设备监视任务；从监视任务自身调用时只清引用，避免取消自己。"""
        task = self._watch_task
        self._watch_task = None
        if task is not None and task is not asyncio.current_task():
            task.cancel()

    async def _watch_device(self, demonstration_id: str) -> None:
        """录制期间轮询设备连接；断开就 ``abort("device_lost")`` 置 ``discarded``。

        进程内闸门没有租约行的过期扫描兜底，设备拔掉后必须由这里主动收尾，
        否则行会一直停在 ``recording``（spec §4.5 状态表、§7 第 5 条）。
        """
        while True:
            await asyncio.sleep(DEVICE_WATCH_INTERVAL_SECONDS)
            if self._active_id != demonstration_id:
                return
            try:
                snapshot = self._device_manager.snapshot()
            except Exception:  # noqa: BLE001 - 读连接状态失败不能结束监视
                logger.warning("示教设备连接监视读取失败", exc_info=True)
                continue
            if snapshot.get("state") == "connected":
                continue
            logger.info("示教录制期间设备断开，丢弃 id=%s", demonstration_id)
            try:
                await self.abort(
                    "device_lost", demonstration_id=demonstration_id
                )
            except asyncio.CancelledError:
                raise
            except Exception:  # noqa: BLE001 - 后台任务不能把异常抛给事件循环
                logger.exception(
                    "设备断开时丢弃示教录制失败 id=%s", demonstration_id
                )
                # 下一轮再试；abort 成功或录制已换人时上面的 return 会收尾。
                continue
            return

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
                # 第 100 步自动停止；录制已切到别的示教时不能清掉对方的闸门。
                if not await self._release_gate(demonstration_id):
                    logger.info(
                        "第 %d 步落库时录制已切换到其它示教 id=%s",
                        MAX_DEMONSTRATION_STEPS,
                        demonstration_id,
                    )
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

    # ---------------------------------------------------------------- 转换

    async def convert(
        self, demonstration_id: str, *, register: bool
    ) -> dict[str, Any]:
        """把 ``recorded`` 示教编译成 ``Custom_*`` 链（spec §4.5「转换」）。

        默认只编译并返回（不改状态、不写资源）；``register=True`` 时一次性注册整条
        链，成功后置 ``converted`` 并写 ``converted_to``。编译、结构/链级校验或注册
        失败时行保持 ``recorded``，不写 ``converted_to``，也不留下部分资产。
        """
        async with self._convert_lock:
            async with self._session_factory() as session:
                row = await session.get(Demonstration, demonstration_id)
                if row is None:
                    raise self._not_found(demonstration_id)
                if row.status != DemonstrationStatus.RECORDED:
                    raise self._state_invalid(row, "convert")
                data = self._wire(row)
                title = row.title
                raw_steps = list(row.steps or [])

            try:
                compiled = compile_steps(
                    prefix=f"Custom_Demo_{demonstration_id[:8]}",
                    steps=[parse_step(raw) for raw in raw_steps],
                )
            except CompileError as exc:
                raise AppError(
                    ErrorCode.CUSTOM_TASK_INVALID,
                    "示教步骤无法编译成自定义任务，请检查步骤后重试",
                    {"errors": exc.errors, "chain_errors": []},
                ) from exc

            errors, chain_errors = self._chain_errors(compiled)
            if errors or chain_errors:
                raise AppError(
                    ErrorCode.CUSTOM_TASK_INVALID,
                    "自定义任务链校验失败，请按 errors 与 chain_errors 修正后重试",
                    {"errors": errors, "chain_errors": chain_errors},
                )

            async with self._session_factory() as session:
                conflicts = await self._conflicting_task_names(
                    session, compiled.tasks
                )
            if conflicts:
                raise AppError(
                    ErrorCode.CUSTOM_TASK_INVALID,
                    "已存在同名自定义任务，注册一定失败；先删掉旧任务或改用新名字",
                    {
                        "errors": [
                            {
                                "task": name,
                                "field": "name",
                                "reason": "已存在同名自定义任务",
                            }
                            for name in conflicts
                        ],
                        "chain_errors": [],
                    },
                )

            compiled_wire: dict[str, Any] = {
                "entry": compiled.entry,
                "tasks": compiled.tasks,
                "chain": list(compiled.chain),
                "valid": True,
                "errors": [],
                "chain_errors": [],
                "register": register,
                "registered": [],
            }
            if not register:
                return {**data, "compile": compiled_wire}

            result = await self._resource_service.register_custom_task(
                name=compiled.entry,
                description=f"示教 {title[:48]}",
                tasks=compiled.tasks,
            )
            items = list(result.get("items") or [])
            now = utcnow()
            converted_to = {
                "entry": str(result.get("entry") or compiled.entry),
                "task_names": [str(name) for name in result.get("task_names") or []],
                "asset_ids": [str(item.get("id")) for item in items],
                "registered_at": self._utc_iso(now),
            }
            compiled_wire["registered"] = [
                {"id": str(item.get("id")), "name": str(item.get("name"))}
                for item in items
            ]

            async with self._session_factory() as session:
                row = await session.get(Demonstration, demonstration_id)
                if row is None:
                    raise self._not_found(demonstration_id)
                if row.status != DemonstrationStatus.RECORDED:
                    raise self._state_invalid(row, "convert")
                row.status = DemonstrationStatus.CONVERTED
                row.converted_to = converted_to
                row.updated_at = now
                await session.commit()
                data = self._wire(row)
            return {**data, "compile": compiled_wire}

    def _chain_errors(
        self, compiled: Compiled
    ) -> tuple[list[dict[str, str]], list[dict[str, str]]]:
        """逐节点结构校验 + 链级校验；错误统一成 ``{task, field, reason}``。"""
        errors: list[dict[str, str]] = []
        for task_name, node in compiled.tasks.items():
            for issue in self._resource_service.validate_custom_task_content(node):
                errors.append(
                    {
                        "task": task_name,
                        "field": str(issue.get("field") or "content"),
                        "reason": str(issue.get("reason") or "结构校验失败"),
                    }
                )
        bundle_errors, chain_errors, _chain = check_bundle(
            compiled.tasks, compiled.entry
        )
        errors.extend(issue.as_dict() for issue in bundle_errors)
        return errors, [issue.as_dict() for issue in chain_errors]

    @staticmethod
    async def _conflicting_task_names(
        session: Any, tasks: Mapping[str, Any]
    ) -> list[str]:
        """注册前逐个比对现有 ``custom_task`` 资产名（写入竞态由 UNIQUE 约束兜住）。"""
        repository = ResourceAssetRepository(session)
        conflicts: list[str] = []
        for task_name in tasks:
            existing = await repository.get_by_kind_name(
                ResourceAssetKind.CUSTOM_TASK, task_name
            )
            if existing is not None:
                conflicts.append(str(task_name))
        return conflicts

    @staticmethod
    def _utc_iso(value: datetime) -> str:
        """``converted_to.registered_at`` 用结尾 ``Z`` 的 UTC ISO 时间。"""
        if value.tzinfo is None:
            return f"{value.isoformat()}Z"
        return value.astimezone(UTC).isoformat().replace("+00:00", "Z")

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

    @staticmethod
    def _stale_recording(
        demonstration_id: str, status: DemonstrationStatus | str, action: str
    ) -> AppError:
        """``stop(A)`` 与 ``start(B)`` 交错：A 已不是当前活动录制（spec §4.5 边界）。"""
        return AppError(
            ErrorCode.DEMONSTRATION_STATE_INVALID,
            f"示教 {demonstration_id} 已不是当前录制的示教，不能执行 {action}",
            {
                "id": demonstration_id,
                "status": status,
                "action": action,
            },
        )


__all__ = ["DemonstrationService", "MAX_DEMONSTRATION_STEPS"]
