"""示教步骤采集：三个设备端点成功路径的公共接线（M89 spec §4.5 采集）。

采集是**尽力而为**的旁路：没有录制、设备断开、分辨率未知或截图失败都不改变设备
动作本身的结果。``begin`` 在动作之前取一次设备快照并在内存里截一张图；
``finish`` 在动作成功之后等画面稳定、再截一张图，把两张图交给
:meth:`DemonstrationService.note_step` 归档并追加步骤。

``note_step`` 的 409 在这里被吞掉：动作执行期间用户停止或丢弃录制是合法操作，
已成功的设备动作不能因为「采集晚了一步」而变成错误响应。
"""

from __future__ import annotations

import asyncio
import logging
from collections.abc import Mapping
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Any

from fastapi import Request
from PIL import Image

from maa_api.agent.tools.raw import CLICK_SETTLE_SECONDS
from maa_api.domain.errors import AppError
from maa_api.settings import get_settings

logger = logging.getLogger(__name__)


@dataclass(slots=True)
class PendingStep:
    """一次设备动作开始前的采集上下文（``before`` 只在内存里，不归档）。"""

    demonstration_id: str
    screen: tuple[int, int]
    backend: str
    started_at: datetime
    before: Image.Image | None


def _service(request: Request) -> Any | None:
    return getattr(request.app.state, "demonstration_service", None)


def _enabled() -> bool:
    """示教开关；配置键由 T6 加入，缺键时按启用处理（本任务先接线）。"""
    settings = get_settings()
    demonstration = getattr(settings, "demonstration", None)
    if demonstration is None:
        return True
    return bool(getattr(demonstration, "enabled", True))


def _resolution(snapshot: Mapping[str, Any]) -> tuple[int, int]:
    resolution = snapshot.get("resolution") or {}
    try:
        return int(resolution["width"]), int(resolution["height"])
    except (KeyError, TypeError, ValueError):
        return 0, 0


async def begin(request: Request, manager: Any) -> PendingStep | None:
    """动作前采集上下文；不录制时返回 ``None``，不截图、不加锁。"""
    service = _service(request)
    if service is None or not _enabled() or not service.is_recording():
        return None

    snapshot = manager.snapshot()
    if snapshot.get("state") != "connected":
        # 设备在录制中断开：丢弃这次录制并释放闸门，端点随后按原有错误返回。
        await service.abort("device_lost")
        return None

    width, height = _resolution(snapshot)
    if width <= 0 or height <= 0:
        logger.warning(
            "设备分辨率未知，跳过示教采集（state=%s）", snapshot.get("state")
        )
        return None

    demonstration_id = service.active_id
    if demonstration_id is None:
        return None

    started_at = datetime.now(UTC)
    before: Image.Image | None = None
    backend = "adb"
    try:
        captured = await manager.screenshot(backend="adb")
    except Exception:  # noqa: BLE001 - 截图失败不能拖垮设备动作
        logger.warning("示教步骤的操作前截图失败，before 记 null", exc_info=True)
    else:
        before = captured.image
        backend = str(captured.backend)

    return PendingStep(
        demonstration_id=demonstration_id,
        screen=(width, height),
        backend=backend,
        started_at=started_at,
        before=before,
    )


async def finish(
    request: Request,
    pending: PendingStep | None,
    manager: Any,
    *,
    action: str,
    points: Mapping[str, int | None],
    duration_ms: int | None,
) -> None:
    """动作成功后补截图并追加步骤；采集失败只记日志，不改设备动作的结果。"""
    if pending is None:
        return
    service = _service(request)
    if service is None:
        return

    await asyncio.sleep(CLICK_SETTLE_SECONDS)
    after: Image.Image | None = None
    try:
        captured = await manager.screenshot(backend="adb")
        after = captured.image
    except Exception:  # noqa: BLE001 - 动作已经成功，截图失败只让 id 记 null
        logger.warning("示教步骤的操作后截图失败，after 记 null", exc_info=True)

    try:
        await service.note_step(
            pending.demonstration_id,
            action=action,
            points=points,
            duration_ms=duration_ms,
            started_at=pending.started_at,
            before=pending.before,
            after=after,
            backend=pending.backend,
        )
    except AppError as exc:
        # 动作执行期间停止/丢弃录制：步骤不再追加，但动作本身已经成功。
        logger.info("示教步骤未追加（%s）：%s", exc.code, exc.message)
    except Exception:  # noqa: BLE001 - 采集不能把已成功的设备动作改写成错误
        logger.exception("示教步骤追加失败；设备动作已成功，响应保持不变")


async def cancel(pending: PendingStep | None) -> None:
    """设备动作失败：丢弃采集上下文（内存中的 before 图随对象释放）。"""
    if pending is None:
        return
    logger.debug(
        "设备动作失败，丢弃采集上下文 demonstration_id=%s",
        pending.demonstration_id,
    )


__all__ = ["PendingStep", "begin", "cancel", "finish"]
