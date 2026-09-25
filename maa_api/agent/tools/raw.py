"""Agent tools for screenshots and bounded game-interface actions."""

from __future__ import annotations

import base64
import io
from datetime import UTC, datetime
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator

from maa_api.agent.registry import ToolContext, ToolDefinition, ToolRegistry, ToolRisk
from maa_api.db.repositories.pipeline import PipelineRepository
from maa_api.domain.errors import AppError, ErrorCode


class EmptyParams(BaseModel):
    model_config = ConfigDict(extra="forbid")


class ClickParams(BaseModel):
    model_config = ConfigDict(extra="forbid")

    x: int = Field(ge=0)
    y: int = Field(ge=0)


class SwipeParams(BaseModel):
    model_config = ConfigDict(extra="forbid")

    x1: int = Field(ge=0)
    y1: int = Field(ge=0)
    x2: int = Field(ge=0)
    y2: int = Field(ge=0)
    duration_ms: int = Field(default=300, ge=1)


class LongPressParams(BaseModel):
    model_config = ConfigDict(extra="forbid")

    x: int = Field(ge=0)
    y: int = Field(ge=0)
    duration_ms: int = Field(ge=1)


class InputTextParams(BaseModel):
    model_config = ConfigDict(extra="forbid")

    text: str


class KeyEventParams(BaseModel):
    model_config = ConfigDict(extra="forbid")

    key: Literal["BACK", "HOME", "ENTER", "DEL", "APP_SWITCH"]

    @field_validator("key", mode="before")
    @classmethod
    def normalize_key(cls, value: Any) -> Any:
        return value.upper() if isinstance(value, str) else value


def _device_manager(context: ToolContext) -> Any:
    request = context.request
    manager = getattr(
        getattr(getattr(request, "app", None), "state", None),
        "device_manager",
        None,
    )
    if manager is None:
        raise AppError(ErrorCode.SERVICE_UNAVAILABLE, "设备管理服务尚未启动")
    return manager


def _pipeline_runner(context: ToolContext) -> Any:
    request = context.request
    runner = getattr(
        getattr(getattr(request, "app", None), "state", None),
        "pipeline_runner",
        None,
    )
    if runner is None:
        raise AppError(ErrorCode.SERVICE_UNAVAILABLE, "流水线执行服务尚未启动")
    if context.db_session is None:
        raise AppError(ErrorCode.SERVICE_UNAVAILABLE, "原子操作缺少流水线状态会话")
    return runner


async def _check_pipeline_idle(
    context: ToolContext, runner: Any, operation: str
) -> None:
    current = await PipelineRepository(context.db_session).current(
        getattr(runner, "core_id", "default")
    )
    if current is not None:
        raise AppError(
            ErrorCode.PIPELINE_ALREADY_RUNNING,
            "流水线运行期间不能执行原子操作",
            {"pipeline_id": current.id, "operation": operation},
        )


async def _run_atomic(context: ToolContext, operation: str, call: Any) -> Any:
    runner = _pipeline_runner(context)
    async with runner.operation_lock:
        await _check_pipeline_idle(context, runner, operation)
        return await call(_device_manager(context))


async def _trigger_screencap(
    _params: EmptyParams, context: ToolContext
) -> dict[str, Any]:
    manager = _device_manager(context)
    state = manager.snapshot().get("state")
    if state != "connected":
        raise AppError(
            ErrorCode.DEVICE_NOT_CONNECTED,
            "设备当前未连接，无法截图",
            {"state": state},
        )
    image_result = await manager.screenshot(backend="core")
    image = image_result.image
    output = io.BytesIO()
    image.save(output, format="PNG")
    return {
        "backend": image_result.backend,
        "mime_type": "image/png",
        "width": image.width,
        "height": image.height,
        "captured_at": datetime.now(UTC).isoformat(),
        "image_base64": base64.b64encode(output.getvalue()).decode("ascii"),
    }


async def _click(params: ClickParams, context: ToolContext) -> dict[str, Any]:
    await _run_atomic(
        context, "click", lambda manager: manager.click(params.x, params.y)
    )
    return {"ok": True, "backend": "core", "x": params.x, "y": params.y}


async def _swipe(params: SwipeParams, context: ToolContext) -> dict[str, Any]:
    await _run_atomic(
        context,
        "swipe",
        lambda manager: manager.swipe(
            params.x1, params.y1, params.x2, params.y2, params.duration_ms
        ),
    )
    return {
        "ok": True,
        "backend": "adb",
        "x1": params.x1,
        "y1": params.y1,
        "x2": params.x2,
        "y2": params.y2,
        "duration_ms": params.duration_ms,
    }


async def _long_press(params: LongPressParams, context: ToolContext) -> dict[str, Any]:
    await _run_atomic(
        context,
        "long_press",
        lambda manager: manager.long_press(params.x, params.y, params.duration_ms),
    )
    return {
        "ok": True,
        "backend": "adb",
        "x": params.x,
        "y": params.y,
        "duration_ms": params.duration_ms,
    }


async def _input_text(params: InputTextParams, context: ToolContext) -> dict[str, Any]:
    await _run_atomic(
        context, "input_text", lambda manager: manager.input_text(params.text)
    )
    return {"ok": True, "backend": "adb", "text": params.text}


async def _key_event(params: KeyEventParams, context: ToolContext) -> dict[str, Any]:
    await _run_atomic(
        context, "key_event", lambda manager: manager.key_event(params.key)
    )
    return {"ok": True, "backend": "adb", "key": params.key}


async def _back_to_home(_params: EmptyParams, context: ToolContext) -> dict[str, Any]:
    ok = await _run_atomic(
        context, "back_to_home", lambda manager: manager.back_to_home()
    )
    return {"ok": bool(ok), "backend": "core"}


def register_tools(registry: ToolRegistry) -> None:
    """Register safe screenshot/reset tools and authorized raw game actions."""
    tools = (
        (
            "trigger_screencap",
            ToolRisk.SAFE,
            "触发 MaaCore 截图并返回 PNG base64 和图像尺寸。",
            EmptyParams,
            _trigger_screencap,
        ),
        (
            "click",
            ToolRisk.DANGEROUS,
            "点击游戏画面坐标；优先提交 MAA 任务而非盲目点坐标。",
            ClickParams,
            _click,
        ),
        (
            "swipe",
            ToolRisk.DANGEROUS,
            "通过 ADB 在游戏画面执行一次定长滑动。",
            SwipeParams,
            _swipe,
        ),
        (
            "long_press",
            ToolRisk.DANGEROUS,
            "通过 ADB 在游戏画面执行一次长按。",
            LongPressParams,
            _long_press,
        ),
        (
            "input_text",
            ToolRisk.DANGEROUS,
            "通过 ADB 向游戏界面输入文本。",
            InputTextParams,
            _input_text,
        ),
        (
            "key_event",
            ToolRisk.DANGEROUS,
            "发送白名单按键事件：BACK、HOME、ENTER、DEL 或 APP_SWITCH。",
            KeyEventParams,
            _key_event,
        ),
        (
            "back_to_home",
            ToolRisk.SAFE,
            "使用 MaaCore 的幂等复位动作返回游戏主界面。",
            EmptyParams,
            _back_to_home,
        ),
    )
    for name, risk, description, params_model, handler in tools:
        registry.register(
            ToolDefinition(
                name=name,
                group="raw",
                risk=risk,
                description=description,
                params_model=params_model,
                handler=handler,
            )
        )
