"""Device state, connection, screenshot, and immediate ADB/Core actions."""

from __future__ import annotations

import io
import logging
import time
from typing import Any

from fastapi import APIRouter, Depends, Query, Request, Response
from PIL import Image
from pydantic import BaseModel, ConfigDict, Field
from sqlalchemy.ext.asyncio import AsyncSession

from maa_api.api.deps import get_pipeline_runner, get_session, require_auth
from maa_api.api.errors import error_responses
from maa_api.db.models import Screenshot
from maa_api.db.repositories.pipeline import PipelineRepository
from maa_api.db.repositories.log import ScreenshotRepository
from maa_api.domain.enums import ScreenshotBackend, ScreenshotTrigger
from maa_api.domain.errors import AppError, ErrorCode
from maa_api.settings import get_settings
from maa_api.util.image import store_screenshot

__all__ = ["router"]

logger = logging.getLogger(__name__)
router = APIRouter(prefix="/api", tags=["device"])

_ALLOWED_KEYS = frozenset({"BACK", "HOME", "ENTER", "DEL", "APP_SWITCH"})
_RECONNECT_HINTS = {
    "adb_binary": "请在设置页检查 ADB 路径，或将 adb 加入 PATH",
    "adb_connect": "请确认模拟器已启动，且 ADB 调试端口正确",
    "adb_shell": "设备已连接但无响应，请尝试重启模拟器",
    "core_connect": "内核无法连接设备，可能是分辨率不受支持或触控方案不兼容",
}


class ReconnectRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    address: str | None = None
    adb_path: str | None = None


class SwipeRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    x1: int = Field(ge=0)
    y1: int = Field(ge=0)
    x2: int = Field(ge=0)
    y2: int = Field(ge=0)
    duration_ms: int = Field(default=300, ge=1)
    force: bool = False


class LongPressRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    x: int = Field(ge=0)
    y: int = Field(ge=0)
    duration_ms: int = Field(ge=1)
    force: bool = False


class InputTextRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    text: str
    force: bool = False


class KeyEventRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    key: str
    force: bool = False


def _device_manager(request: Request) -> Any:
    manager = getattr(request.app.state, "device_manager", None)
    if manager is None:
        raise AppError(ErrorCode.SERVICE_UNAVAILABLE, "设备管理服务尚未启动")
    return manager


def _is_reconnecting(manager: Any) -> bool:
    """Recognize an active connect attempt, including the ADB preflight phase."""
    state = getattr(manager, "state", None)
    state = getattr(state, "value", state)
    return state in {"connecting", "reconnecting"}


def _reconnect_error(manager: Any) -> AppError:
    snapshot = manager.snapshot()
    if _is_reconnecting(manager):
        retry = snapshot.get("retry") or {}
        attempt, maximum = retry.get("attempt", 0), retry.get("max", 0)
        progress = (
            f"正在自动重连（第 {attempt} / {maximum} 次）"
            if maximum
            else "设备正在重连，请稍后再试"
        )
        return AppError(
            ErrorCode.PIPELINE_ALREADY_RUNNING,
            "设备正在自动重连，请稍后再试",
            {
                "operation": "reconnect",
                "state": snapshot.get("state"),
                "retry": retry,
                "message": progress,
            },
        )

    last_error = snapshot.get("last_error") or {}
    details = dict(last_error)
    stage = str(details.get("stage") or "adb_connect")
    address = str(snapshot.get("address") or "")
    details.setdefault("stage", stage)
    details.setdefault("command", f"adb connect {address}".strip())
    details.setdefault("output", "设备连接失败")
    details.setdefault("hint", _RECONNECT_HINTS.get(stage, _RECONNECT_HINTS["core_connect"]))
    details["address"] = address
    retry = snapshot.get("retry") or {}
    details["attempts"] = retry.get("attempt", 0)
    code = ErrorCode.ADB_NOT_FOUND if stage == "adb_binary" else ErrorCode.ADB_CONNECT_FAILED
    message = (
        "找不到 ADB 可执行文件"
        if stage == "adb_binary"
        else f"无法连接到 {address}" if address else "设备连接失败"
    )
    return AppError(code, message, details)


async def _ensure_atomic_allowed(
    *, operation: str, force: bool, session: AsyncSession
) -> None:
    current = await PipelineRepository(session).current()
    if current is not None and not force:
        raise AppError(
            ErrorCode.PIPELINE_ALREADY_RUNNING,
            "流水线运行期间不能执行原子操作；如确需介入，请显式设置 force=true",
            {"pipeline_id": current.id, "operation": operation},
        )
    if current is not None:
        logger.warning(
            "强制原子操作介入运行中的流水线：pipeline_id=%s operation=%s",
            current.id,
            operation,
        )


@router.get(
    "/device/status",
    summary="读取设备连接状态",
    responses=error_responses("SERVICE_UNAVAILABLE", "UNAUTHORIZED"),
    dependencies=[Depends(require_auth)],
)
async def device_status(request: Request) -> dict[str, Any]:
    return _device_manager(request).snapshot()


@router.post(
    "/device/reconnect",
    summary="手动重连设备",
    responses=error_responses(
        "ADB_CONNECT_FAILED", "ADB_NOT_FOUND", "PIPELINE_ALREADY_RUNNING", "UNAUTHORIZED"
    ),
    dependencies=[Depends(require_auth)],
    status_code=202,
)
async def reconnect_device(
    request: Request, payload: ReconnectRequest | None = None
) -> dict[str, Any]:
    manager = _device_manager(request)
    if _is_reconnecting(manager):
        raise _reconnect_error(manager)

    payload = payload or ReconnectRequest()
    started = time.monotonic()
    if payload.address is not None or payload.adb_path is not None:
        connected = await manager.reconfigure(
            address=payload.address, adb_path=payload.adb_path
        )
    else:
        connected = await manager.connect(reason="reconnect")
    if not connected:
        raise _reconnect_error(manager)
    result = manager.snapshot()
    result["elapsed_ms"] = round((time.monotonic() - started) * 1000)
    return result


async def _device_candidates(request: Request, include_common_ports: bool) -> dict[str, Any]:
    manager = _device_manager(request)
    try:
        devices = await manager.list_devices(include_common_ports=include_common_ports)
    except AppError as exc:
        if (
            exc.code == ErrorCode.DEVICE_SCAN_FAILED
            and isinstance(exc.details, dict)
            and exc.details.get("stage") == "adb_binary"
        ):
            raise AppError(ErrorCode.ADB_NOT_FOUND, exc.message, exc.details) from exc
        raise
    return {
        "current": manager.address,
        "devices": devices,
        "common_ports": list(manager.common_ports),
    }


@router.get(
    "/device/list",
    summary="扫描已连接设备",
    responses=error_responses("DEVICE_SCAN_FAILED", "ADB_NOT_FOUND", "UNAUTHORIZED"),
    dependencies=[Depends(require_auth)],
)
async def list_devices(
    request: Request,
    include_common_ports: bool = Query(default=False),
) -> dict[str, Any]:
    return await _device_candidates(request, include_common_ports)


@router.get(
    "/device/candidates",
    summary="扫描设备候选项",
    responses=error_responses("DEVICE_SCAN_FAILED", "ADB_NOT_FOUND", "UNAUTHORIZED"),
    dependencies=[Depends(require_auth)],
)
async def list_device_candidates(
    request: Request,
    include_common_ports: bool = Query(default=False),
) -> dict[str, Any]:
    return await _device_candidates(request, include_common_ports)


@router.get(
    "/device/screenshot",
    summary="获取实时设备截图",
    responses=error_responses(
        "SCREENSHOT_FAILED", "DEVICE_NOT_CONNECTED", "INVALID_PARAMETER", "UNAUTHORIZED"
    ),
    dependencies=[Depends(require_auth)],
    response_model=None,
)
async def device_screenshot(
    request: Request,
    session: AsyncSession = Depends(get_session),
    backend: str = Query(default="adb"),
    format: str = Query(default="jpeg"),
    size: str = Query(default="full"),
    quality: int | None = Query(default=None, ge=1, le=95),
    archive: bool = Query(default=False),
) -> Response | dict[str, Any]:
    if backend not in {"adb", "core"}:
        raise AppError(
            ErrorCode.INVALID_PARAMETER,
            "截图 backend 必须是 adb 或 core",
            {"backend": backend},
        )
    normalized_format = format.lower()
    if normalized_format not in {"jpeg", "jpg", "png"}:
        raise AppError(
            ErrorCode.INVALID_PARAMETER,
            "截图 format 仅支持 jpeg、jpg 或 png",
            {"format": format},
        )
    if size not in {"thumb", "mobile", "full"}:
        raise AppError(
            ErrorCode.INVALID_PARAMETER,
            "截图 size 仅支持 thumb、mobile 或 full",
            {"size": size},
        )

    manager = _device_manager(request)
    state = manager.snapshot().get("state")
    if state != "connected":
        raise AppError(
            ErrorCode.DEVICE_NOT_CONNECTED,
            "设备当前未连接，无法截图",
            {"state": state},
        )
    result = await manager.screenshot(backend=backend)
    configured_quality = int(get_settings().adb.screenshot_quality)
    output_quality = quality if quality is not None else configured_quality
    original_image = result.image.copy()
    image = original_image.copy()
    if size == "thumb":
        image.thumbnail((320, 320), Image.Resampling.LANCZOS)
        output_quality = 60
        normalized_format = "jpeg"
    elif size == "mobile":
        image.thumbnail((1280, 1280), Image.Resampling.LANCZOS)
    if archive:
        stored = store_screenshot(
            original_image,
            quality=quality if quality is not None else configured_quality,
        )
        relative_path = (
            f"image/screenshot/{stored['sha256'][:2]}/{stored['sha256']}.jpg"
        )
        screenshot = await ScreenshotRepository(session).create(
            Screenshot(
                trigger=ScreenshotTrigger.MANUAL,
                backend=ScreenshotBackend(result.backend),
                path=relative_path,
                format="jpg",
                width=stored["width"],
                height=stored["height"],
                size_bytes=stored["bytes"],
            )
        )
        screenshot_id = screenshot.id
        await session.commit()
        return {
            **stored,
            "id": screenshot_id,
            "backend": result.backend,
            "format": "jpg",
        }

    output = io.BytesIO()
    if normalized_format in {"jpeg", "jpg"}:
        image.convert("RGB").save(output, format="JPEG", quality=output_quality)
        media_type = "image/jpeg"
    else:
        image.save(output, format="PNG")
        media_type = "image/png"
    content = output.getvalue()
    return Response(
        content=content,
        media_type=media_type,
        headers={
            "Content-Length": str(len(content)),
            "X-Image-Backend": result.backend,
        },
    )


async def _run_atomic(
    request: Request,
    runner: Any,
    session: AsyncSession,
    *,
    operation: str,
    force: bool,
    call: Any,
) -> dict[str, Any]:
    manager = _device_manager(request)
    async with runner.operation_lock:
        await _ensure_atomic_allowed(
            operation=operation, force=force, session=session
        )
        await call(manager)
    return {"ok": True, "backend": "core" if operation == "click" else "adb"}


@router.post(
    "/device/swipe",
    summary="执行滑动操作",
    responses=error_responses(
        "ADB_COMMAND_FAILED", "PIPELINE_ALREADY_RUNNING", "SERVICE_UNAVAILABLE", "UNAUTHORIZED"
    ),
    dependencies=[Depends(require_auth)],
)
async def swipe_device(
    payload: SwipeRequest,
    request: Request,
    runner: Any = Depends(get_pipeline_runner),
    session: AsyncSession = Depends(get_session),
) -> dict[str, Any]:
    result = await _run_atomic(
        request,
        runner,
        session,
        operation="swipe",
        force=payload.force,
        call=lambda manager: manager.swipe(
            payload.x1, payload.y1, payload.x2, payload.y2, payload.duration_ms
        ),
    )
    return {
        **result,
        "x1": payload.x1,
        "y1": payload.y1,
        "x2": payload.x2,
        "y2": payload.y2,
        "duration_ms": payload.duration_ms,
    }


@router.post(
    "/device/long_press",
    summary="执行长按操作",
    responses=error_responses(
        "ADB_COMMAND_FAILED", "PIPELINE_ALREADY_RUNNING", "SERVICE_UNAVAILABLE", "UNAUTHORIZED"
    ),
    dependencies=[Depends(require_auth)],
)
async def long_press_device(
    payload: LongPressRequest,
    request: Request,
    runner: Any = Depends(get_pipeline_runner),
    session: AsyncSession = Depends(get_session),
) -> dict[str, Any]:
    result = await _run_atomic(
        request,
        runner,
        session,
        operation="long_press",
        force=payload.force,
        call=lambda manager: manager.long_press(
            payload.x, payload.y, payload.duration_ms
        ),
    )
    return {
        **result,
        "x": payload.x,
        "y": payload.y,
        "duration_ms": payload.duration_ms,
    }


@router.post(
    "/device/input_text",
    summary="输入设备文本",
    responses=error_responses(
        "ADB_COMMAND_FAILED", "PIPELINE_ALREADY_RUNNING", "SERVICE_UNAVAILABLE", "UNAUTHORIZED"
    ),
    dependencies=[Depends(require_auth)],
)
async def input_device_text(
    payload: InputTextRequest,
    request: Request,
    runner: Any = Depends(get_pipeline_runner),
    session: AsyncSession = Depends(get_session),
) -> dict[str, Any]:
    result = await _run_atomic(
        request,
        runner,
        session,
        operation="input_text",
        force=payload.force,
        call=lambda manager: manager.input_text(payload.text),
    )
    return {**result, "text": payload.text}


@router.post(
    "/device/key_event",
    summary="发送设备按键事件",
    responses=error_responses(
        "INVALID_PARAMETER", "ADB_COMMAND_FAILED", "PIPELINE_ALREADY_RUNNING",
        "SERVICE_UNAVAILABLE", "UNAUTHORIZED"
    ),
    dependencies=[Depends(require_auth)],
)
async def device_key_event(
    payload: KeyEventRequest,
    request: Request,
    runner: Any = Depends(get_pipeline_runner),
    session: AsyncSession = Depends(get_session),
) -> dict[str, Any]:
    normalized = payload.key.upper()
    if normalized not in _ALLOWED_KEYS:
        raise AppError(
            ErrorCode.INVALID_PARAMETER,
            "按键不在允许列表中",
            {"key": payload.key, "allowed": sorted(_ALLOWED_KEYS)},
        )
    result = await _run_atomic(
        request,
        runner,
        session,
        operation="key_event",
        force=payload.force,
        call=lambda manager: manager.key_event(normalized),
    )
    return {**result, "key": normalized}
