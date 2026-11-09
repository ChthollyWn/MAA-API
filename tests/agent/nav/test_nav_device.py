"""``AppNavDevice``（M86 spec §4.2、§4.5.4）：把导航动作包进 ``raw.run_atomic``。

假 ``ToolContext`` 只带 ``request.app.state.device_manager``；``run_atomic`` 被替换成
「记录 operation + 直接回调假 manager」，这样本文件不依赖流水线库，也能看清每个写设备
动作确实走了 ``navigate_to`` 的原子操作。
"""

from __future__ import annotations

import asyncio
from types import SimpleNamespace
from typing import Any

import pytest
from PIL import Image

from maa_api.agent.nav.navigator import AppNavDevice
from maa_api.agent.registry import ToolContext
from maa_api.agent.tools import raw
from maa_api.domain.enums import CallerType
from maa_api.domain.errors import AppError, ErrorCode
from maa_api.services.device_service import ScreenshotResult


class RecordingManager:
    """记录设备调用；``error`` 用来模拟租约被接管等失败。"""

    def __init__(
        self,
        *,
        resolution: dict[str, Any] | None = None,
        image_size: tuple[int, int] = (1280, 720),
    ) -> None:
        self.resolution = resolution
        self.image_size = image_size
        self.calls: list[tuple[Any, ...]] = []
        self.error: AppError | None = None

    def snapshot(self) -> dict[str, Any]:
        self.calls.append(("snapshot",))
        return {"resolution": self.resolution}

    async def screenshot(self, *, backend: str = "adb") -> ScreenshotResult:
        self.calls.append(("screenshot", backend))
        if self.error is not None:
            raise self.error
        return ScreenshotResult(Image.new("RGB", self.image_size), backend)

    def _raise(self) -> None:
        if self.error is not None:
            raise self.error

    async def click(self, x: int, y: int) -> None:
        self.calls.append(("click", x, y))
        self._raise()

    async def swipe(self, x1: int, y1: int, x2: int, y2: int, duration_ms: int) -> None:
        self.calls.append(("swipe", x1, y1, x2, y2, duration_ms))
        self._raise()

    async def key_event(self, key: str) -> None:
        self.calls.append(("key_event", key))
        self._raise()

    async def back_to_home(self) -> bool:
        self.calls.append(("back_to_home",))
        self._raise()
        return True


def _context(manager: RecordingManager) -> ToolContext:
    state = SimpleNamespace(device_manager=manager, pipeline_runner=None)
    request = SimpleNamespace(app=SimpleNamespace(state=state))
    return ToolContext(
        caller=CallerType.INTERNAL,
        session_id="session-1",
        request_id="request-1",
        request=request,
        db_session=None,
    )


def test_each_write_action_runs_inside_run_atomic(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    manager = RecordingManager()
    operations: list[str] = []

    async def fake_run_atomic(context: ToolContext, operation: str, call: Any) -> Any:
        operations.append(operation)
        return await call(manager)

    monkeypatch.setattr(raw, "run_atomic", fake_run_atomic)
    device = AppNavDevice(_context(manager))

    async def scenario() -> None:
        await device.tap(10, 20)
        await device.swipe(1, 2, 3, 4, 250)
        await device.key("BACK")
        assert await device.home() is True
        await device.screenshot()

    asyncio.run(scenario())

    assert operations == ["navigate_to"] * 4
    assert manager.calls == [
        ("click", 10, 20),
        ("swipe", 1, 2, 3, 4, 250),
        ("key_event", "BACK"),
        ("back_to_home",),
        ("screenshot", "adb"),
    ]


def test_lease_preempted_propagates(monkeypatch: pytest.MonkeyPatch) -> None:
    manager = RecordingManager()
    manager.error = AppError(ErrorCode.DEVICE_LEASE_PREEMPTED, "租约被接管")

    async def fake_run_atomic(context: ToolContext, operation: str, call: Any) -> Any:
        return await call(manager)

    monkeypatch.setattr(raw, "run_atomic", fake_run_atomic)
    device = AppNavDevice(_context(manager))

    async def scenario() -> None:
        with pytest.raises(AppError) as error:
            await device.tap(1, 2)
        assert error.value.code is ErrorCode.DEVICE_LEASE_PREEMPTED
        with pytest.raises(AppError) as home_error:
            await device.home()
        assert home_error.value.code is ErrorCode.DEVICE_LEASE_PREEMPTED

    asyncio.run(scenario())


def test_resolution_converts_snapshot_dict_and_caches() -> None:
    manager = RecordingManager(resolution={"width": 1920, "height": 1080})
    device = AppNavDevice(_context(manager))

    async def scenario() -> None:
        first = await device.resolution()
        second = await device.resolution()
        assert first == (1920, 1080)
        assert isinstance(first[0], int) and isinstance(first[1], int)
        assert second == first

    asyncio.run(scenario())
    # 缓存后不再读 snapshot()。
    assert manager.calls == [("snapshot",)]


def test_resolution_falls_back_to_screenshot_size() -> None:
    manager = RecordingManager(resolution=None, image_size=(800, 600))
    device = AppNavDevice(_context(manager))

    async def scenario() -> None:
        assert await device.resolution() == (800, 600)
        assert await device.resolution() == (800, 600)

    asyncio.run(scenario())
    assert manager.calls == [
        ("snapshot",),
        ("screenshot", "adb"),
    ]


def test_screenshot_uses_adb_backend() -> None:
    manager = RecordingManager(image_size=(1280, 720))
    device = AppNavDevice(_context(manager))

    async def scenario() -> None:
        image = await device.screenshot()
        assert image.size == (1280, 720)

    asyncio.run(scenario())
    assert manager.calls == [("screenshot", "adb")]
