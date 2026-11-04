"""M68 评测用的假内核、假设备与假流水线运行器（docs/v3/specs/m68-trace-evals.md §4.2.7）。

四个类照 ``scripts/agent_smoke.py`` 的同名私有类写，但参数可配置（脚本、设备状态、
当前画面），供评测场景的世界设置使用。PIL 只用来画一张 1280×720 的假截图。
"""

from __future__ import annotations

import asyncio
import functools
import json
from pathlib import Path
from typing import Any

from PIL import Image, ImageDraw

from maa_api.domain.errors import AppError, ErrorCode
from maa_api.services.device_service import ScreenshotResult
from maa_api.services.pipeline_runner import PipelineRunner
from tests.fakes.fake_asst import FakeAsst

# 每种画面一个底色，左上角写标签；评测断言只看得到图片存在与尺寸，不依赖颜色。
SCREEN_LABELS = {
    "home": "ARKNIGHTS HOME",
    "desktop": "ANDROID DESKTOP",
    "stage": "STAGE START PAGE",
    "battle": "IN BATTLE",
    "settlement": "SETTLEMENT",
}
SCREEN_COLORS = {
    "home": (24, 32, 48),
    "desktop": (36, 24, 40),
    "stage": (20, 40, 30),
    "battle": (48, 24, 24),
    "settlement": (44, 40, 20),
}
SCREEN_SIZE = (1280, 720)


def render_screen(screen: str) -> Image.Image:
    """渲染一张指定画面的假截图；未知画面按 home 处理。"""
    label = SCREEN_LABELS.get(screen, SCREEN_LABELS["home"])
    image = Image.new("RGB", SCREEN_SIZE, SCREEN_COLORS.get(screen, SCREEN_COLORS["home"]))
    draw = ImageDraw.Draw(image)
    draw.text((16, 16), label, fill=(240, 240, 240))
    return image


class FakeCoreSupervisor:
    """不加载 MaaCore 的 lifespan 适配器，状态直接 READY。"""

    def __init__(self, _config: Any, *, on_crash: Any, on_state_change: Any) -> None:
        del on_crash
        from maa_api.core.supervisor import CoreState

        self.state = CoreState.READY
        self.generation = 1
        self.pid = 11011
        self._on_state_change = on_state_change

    async def start(self, **_kwargs: Any) -> None:
        self._on_state_change(self.state)

    async def stop(self, **_kwargs: Any) -> None:
        return None

    class _Maintenance:
        async def __aenter__(self) -> None:
            return None

        async def __aexit__(self, *_exc: Any) -> None:
            return None

    def acquire_maintenance(self) -> _Maintenance:
        return self._Maintenance()

    async def restart(self) -> None:
        return None


class FakeCoreClient:
    """用 FakeAsst 走真实队列路径的假内核客户端；script 决定任务成败。"""

    def __init__(self, _supervisor: Any, *, script: str = "success", **_kwargs: Any) -> None:
        self.handlers: dict[str, list[Any]] = {}
        self.loop = asyncio.get_running_loop()
        self.asst = FakeAsst(script=script, delay_scale=0)
        self.asst.set_callback(self._on_asst_callback)

    def on(self, event: str, handler: Any) -> None:
        self.handlers.setdefault(event, []).append(handler)

    def _on_asst_callback(self, message: int, details: bytes, _arg: Any) -> None:
        payload = {"msg": message, "details": json.loads(details)}
        for handler in self.handlers.get("CALLBACK", []):
            self.loop.call_soon_threadsafe(handler, payload)

    def start_consumer(self) -> None:
        return None

    async def _send(self, *_args: Any, **_kwargs: Any) -> dict[str, Any]:
        return {"ret": 0}

    async def append_task(self, type_name: str, params: dict[str, Any]) -> int:
        task_id = self.asst.append_task(type_name, params)
        if task_id <= 0:
            raise AppError(
                ErrorCode.CORE_COMMAND_FAILED,
                "AsstAppendTask 返回 0：参数不被内核接受",
            )
        return task_id

    async def start(self) -> bool:
        return self.asst.start()

    async def stop(self) -> bool:
        return self.asst.stop()

    def close(self) -> None:
        self.asst.stop()


class FakeDeviceManager:
    """内存设备：截图来自渲染函数，连接状态由场景决定。"""

    def __init__(
        self,
        _settings: Any,
        _client: Any,
        *,
        broadcast: Any,
        core_id: str,
        connected: bool = True,
        screen: str = "home",
    ) -> None:
        self.broadcast = broadcast
        self.core_id = core_id
        self.connected = connected
        self.screen = screen
        self.address = "eval:5555"
        self.common_ports: list[int] = []
        self.reconnect_retry_attempts = 1
        self.clicks: list[tuple[int, int]] = []
        self.closed = False

    async def connect_with_retry(self, **_kwargs: Any) -> bool:
        return False

    async def ensure_available(self, *, timeout: float = 10.0) -> bool:
        del timeout
        return self.connected

    def on_core_connection_event(self, _what: str, _details: dict[str, Any]) -> None:
        return None

    def snapshot(self) -> dict[str, Any]:
        return {
            "core_id": self.core_id,
            "state": "connected" if self.connected else "disconnected",
            "address": self.address,
        }

    async def screenshot(self, backend: str = "adb") -> ScreenshotResult:
        del backend
        return ScreenshotResult(render_screen(self.screen), "adb")

    async def click(self, x: int, y: int) -> None:
        self.clicks.append((x, y))

    async def close(self) -> None:
        self.closed = True


class FakePipelineRunner(PipelineRunner):
    """生产队列/运行器路径，底层是 FakeAsst 而不是 MaaCore。"""

    async def publish_pipeline(self, _pipeline_id: str) -> None:
        return None

    async def publish_queue_changed(self) -> None:
        return None


def fake_core_factories(
    *, script: str = "success", device_connected: bool = True, screen: str = "home"
) -> dict[str, Any]:
    """返回 lifespan 需要的四个工厂，参数已经用 partial 绑定。"""
    return {
        "core_supervisor_factory": FakeCoreSupervisor,
        "core_client_factory": functools.partial(FakeCoreClient, script=script),
        "device_manager_factory": functools.partial(
            FakeDeviceManager, connected=device_connected, screen=screen
        ),
        "pipeline_runner_factory": FakePipelineRunner,
    }
