"""M89 设备端点采集接线（spec §4.5 采集、§5 T3、§6）。

真 ``TestClient`` + 假设备：路由、``DemonstrationService``、租约与数据库都是真的，
只有 ``DeviceManager`` 是替身。截图归档走真实 ``archive_screenshot``（库与文件都
落在 ``tmp_path``）；需要断言失败容忍时再 monkeypatch 归档函数。

异步准备与断言一律经 ``client.portal`` 在应用的同一事件循环里跑：``asyncio.Lock``
绑定首次使用的循环，混用 ``asyncio.run`` 会让服务锁在第二个循环里报错。
"""

from __future__ import annotations

import asyncio
from types import SimpleNamespace
from typing import Any

import pytest
from fastapi import FastAPI
from PIL import Image
from sqlalchemy import select
from sqlalchemy.ext.asyncio import async_sessionmaker, AsyncSession

import maa_api.agent.demonstrations.capture as capture_module
import maa_api.agent.demonstrations.service as demonstration_service_module
from maa_api.agent.demonstrations.service import DemonstrationService
from maa_api.agent.lease import DeviceLeaseService
from maa_api.api.deps import get_session
from maa_api.api.errors import register_exception_handlers
from maa_api.api.routers import atomic, device
from maa_api.db.models import Demonstration, Screenshot
from maa_api.domain.enums import DemonstrationStatus, ScreenshotBackend, ScreenshotTrigger
from maa_api.domain.errors import AppError, ErrorCode
from maa_api.services.device_service import ScreenshotResult
from maa_api.settings import LeaseSettings
from tests.fakes.v3_db import make_session_factory

Factory = async_sessionmaker[AsyncSession]


class FakeDevice:
    """只实现采集需要的接口；``click_hook`` 用来在动作执行期间插手。"""

    def __init__(self) -> None:
        self.state = "connected"
        self.resolution: tuple[int, int] | None = (1280, 720)
        self.calls: list[tuple[Any, ...]] = []
        self.screenshot_count = 0
        self.click_error: Exception | None = None
        self.click_hook: Any = None

    def snapshot(self) -> dict[str, Any]:
        return {
            "state": self.state,
            "resolution": (
                {"width": self.resolution[0], "height": self.resolution[1]}
                if self.resolution is not None
                else None
            ),
        }

    async def screenshot(self, *, backend: str = "adb") -> ScreenshotResult:
        self.calls.append(("screenshot", backend))
        self.screenshot_count += 1
        # 每次给不同颜色，避免内容寻址把两张图合并成同一份文件。
        image = Image.new("RGB", (8, 6), (self.screenshot_count * 7 % 256, 40, 90))
        return ScreenshotResult(image, "adb")

    async def click(self, x: int, y: int) -> None:
        self.calls.append(("click", x, y))
        self._ensure_connected("click")
        if self.click_error is not None:
            raise self.click_error
        if self.click_hook is not None:
            await self.click_hook()

    async def swipe(self, x1: int, y1: int, x2: int, y2: int, duration_ms: int) -> None:
        self.calls.append(("swipe", x1, y1, x2, y2, duration_ms))
        self._ensure_connected("swipe")

    async def long_press(self, x: int, y: int, duration_ms: int) -> None:
        self.calls.append(("long_press", x, y, duration_ms))
        self._ensure_connected("long_press")

    def _ensure_connected(self, operation: str) -> None:
        if self.state != "connected":
            raise AppError(
                ErrorCode.DEVICE_NOT_CONNECTED,
                f"设备当前未连接，无法{operation}",
                {"operation": operation, "state": self.state},
            )


class FakeRunner:
    def __init__(self) -> None:
        self.operation_lock = asyncio.Lock()


@pytest.fixture
def capture_env(tmp_path, tmp_settings, monkeypatch, make_client):
    engine_holder: dict[str, Any] = {}

    async def setup() -> Factory:
        engine, factory = await make_session_factory(tmp_path / "capture.db")
        engine_holder["engine"] = engine
        return factory

    factory = asyncio.run(setup())
    manager = FakeDevice()
    lease = DeviceLeaseService(
        factory,
        boot_id="boot-capture",
        settings_provider=lambda: SimpleNamespace(
            lease=LeaseSettings(wait_seconds=0)
        ),
        poll_seconds=0.02,
    )
    service = DemonstrationService(
        factory,
        device_manager=manager,
        lease=lease,
        resource_service=SimpleNamespace(),
    )

    app = FastAPI()
    register_exception_handlers(app)
    app.include_router(device.router)
    app.include_router(atomic.router)
    app.state.device_manager = manager
    app.state.pipeline_runner = FakeRunner()
    app.state.demonstration_service = service

    async def session_override():
        async with factory() as session:
            yield session

    app.dependency_overrides[get_session] = session_override
    # 采集后的固定停顿在测试里没有意义。
    monkeypatch.setattr(capture_module, "CLICK_SETTLE_SECONDS", 0)

    client = make_client(app)
    yield SimpleNamespace(
        client=client,
        service=service,
        manager=manager,
        lease=lease,
        factory=factory,
        app=app,
    )
    engine_holder["engine"].sync_engine.dispose()


def _start(client, service, title: str = "采集") -> dict[str, Any]:
    async def starter():
        return await service.start(title=title)

    return client.portal.call(starter)


def _demonstrations(factory: Factory) -> list[tuple[str, str, list[dict[str, Any]]]]:
    async def read():
        async with factory() as db:
            result = await db.execute(
                select(Demonstration).order_by(Demonstration.created_at, Demonstration.id)
            )
            return [
                (row.id, row.status, list(row.steps or []))
                for row in result.scalars().all()
            ]

    return asyncio.run(read())


def _screenshots(factory: Factory) -> list[Screenshot]:
    async def read():
        async with factory() as db:
            result = await db.execute(
                select(Screenshot).order_by(Screenshot.created_at, Screenshot.id)
            )
            return list(result.scalars().all())

    return asyncio.run(read())


def _spy_note_step(monkeypatch, service: DemonstrationService) -> list[dict[str, Any]]:
    calls: list[dict[str, Any]] = []
    original = service.note_step

    async def spy(demonstration_id, **kwargs):
        calls.append({"demonstration_id": demonstration_id, **kwargs})
        return await original(demonstration_id, **kwargs)

    monkeypatch.setattr(service, "note_step", spy)
    return calls


def test_click_appends_step_with_two_screenshots(capture_env):
    env = capture_env
    _start(env.client, env.service)

    response = env.client.post("/api/device/click", json={"x": 640, "y": 360})

    assert response.status_code == 200
    assert response.json() == {"ok": True, "backend": "core", "x": 640, "y": 360}
    (demonstration_id, status, steps), = _demonstrations(env.factory)
    assert status == DemonstrationStatus.RECORDING
    assert len(steps) == 1
    step = steps[0]
    assert step["seq"] == 1
    assert step["action"] == "click"
    assert (step["x"], step["y"]) == (640, 360)
    assert step["x1"] is None and step["y2"] is None
    assert step["duration_ms"] is None
    assert step["screen"] == [1280, 720]
    assert step["screenshot_id"]
    assert step["result_screenshot_id"]
    assert step["screenshot_id"] != step["result_screenshot_id"]
    assert step["at"].endswith("Z")

    screenshots = _screenshots(env.factory)
    assert len(screenshots) == 2
    assert {row.trigger for row in screenshots} == {ScreenshotTrigger.MANUAL}
    assert {row.backend for row in screenshots} == {ScreenshotBackend.ADB}
    assert [call[0] for call in env.manager.calls] == [
        "screenshot",
        "click",
        "screenshot",
    ]

    # 假设备下连点三次：三条步骤、六条截图记录（§5 T3 完成判据）。
    for _ in range(2):
        assert env.client.post("/api/device/click", json={"x": 1, "y": 2}).status_code == 200
    (_same_id, _status, steps), = _demonstrations(env.factory)
    assert [step["seq"] for step in steps] == [1, 2, 3]
    assert len(_screenshots(env.factory)) == 6


def test_no_capture_without_recording(capture_env):
    env = capture_env
    client = env.client

    click = client.post("/api/device/click", json={"x": 10, "y": 20})
    swipe = client.post(
        "/api/device/swipe", json={"x1": 1, "y1": 2, "x2": 3, "y2": 4}
    )
    press = client.post(
        "/api/device/long_press", json={"x": 5, "y": 6, "duration_ms": 1200}
    )

    assert click.status_code == swipe.status_code == press.status_code == 200
    # 响应与未接线时逐字一致。
    assert click.json() == {"ok": True, "backend": "core", "x": 10, "y": 20}
    assert swipe.json() == {
        "ok": True,
        "backend": "adb",
        "x1": 1,
        "y1": 2,
        "x2": 3,
        "y2": 4,
        "duration_ms": 300,
    }
    assert press.json() == {
        "ok": True,
        "backend": "adb",
        "x": 5,
        "y": 6,
        "duration_ms": 1200,
    }
    assert ("screenshot", "adb") not in env.manager.calls
    assert env.manager.screenshot_count == 0
    assert _demonstrations(env.factory) == []
    assert _screenshots(env.factory) == []


def test_failed_action_appends_no_step(capture_env, monkeypatch):
    env = capture_env
    _start(env.client, env.service)
    note_calls = _spy_note_step(monkeypatch, env.service)
    env.manager.click_error = AppError(
        ErrorCode.DEVICE_NOT_CONNECTED, "点击失败：设备无响应", {"operation": "click"}
    )

    response = env.client.post("/api/device/click", json={"x": 10, "y": 20})

    assert response.status_code == 503
    assert response.json()["error"]["code"] == ErrorCode.DEVICE_NOT_CONNECTED
    assert note_calls == []
    (_demonstration_id, status, steps), = _demonstrations(env.factory)
    assert status == DemonstrationStatus.RECORDING
    assert steps == []
    # 操作前截图只在内存里；失败的动作不归档、不追加。
    assert env.manager.screenshot_count == 1
    assert _screenshots(env.factory) == []


def test_swipe_long_press_and_null_screenshots(capture_env, monkeypatch):
    env = capture_env
    _start(env.client, env.service)

    swipe = env.client.post(
        "/api/device/swipe",
        json={"x1": 100, "y1": 200, "x2": 300, "y2": 400, "duration_ms": 250},
    )
    press = env.client.post(
        "/api/device/long_press", json={"x": 50, "y": 60, "duration_ms": 900}
    )

    assert swipe.status_code == press.status_code == 200
    assert swipe.json() == {
        "ok": True,
        "backend": "adb",
        "x1": 100,
        "y1": 200,
        "x2": 300,
        "y2": 400,
        "duration_ms": 250,
    }
    assert press.json() == {
        "ok": True,
        "backend": "adb",
        "x": 50,
        "y": 60,
        "duration_ms": 900,
    }
    (_id, _status, steps), = _demonstrations(env.factory)
    assert [(step["action"], step["duration_ms"]) for step in steps] == [
        ("swipe", 250),
        ("long_press", 900),
    ]
    assert (steps[0]["x1"], steps[0]["y1"], steps[0]["x2"], steps[0]["y2"]) == (
        100,
        200,
        300,
        400,
    )
    assert (steps[1]["x"], steps[1]["y"]) == (50, 60)
    assert steps[0]["x"] is None and steps[1]["x1"] is None
    assert all(step["screenshot_id"] for step in steps)
    assert len(_screenshots(env.factory)) == 4

    async def broken_archive(*args, **kwargs):
        raise RuntimeError("归档磁盘写满")

    monkeypatch.setattr(
        demonstration_service_module, "archive_screenshot", broken_archive
    )
    click = env.client.post("/api/device/click", json={"x": 7, "y": 8})

    assert click.status_code == 200
    (_id, status, steps), = _demonstrations(env.factory)
    assert status == DemonstrationStatus.RECORDING
    assert len(steps) == 3
    assert steps[-1]["action"] == "click"
    assert steps[-1]["screenshot_id"] is None
    assert steps[-1]["result_screenshot_id"] is None


def test_unknown_resolution_skips_step(capture_env):
    env = capture_env
    _start(env.client, env.service)
    env.manager.resolution = None

    response = env.client.post("/api/device/click", json={"x": 10, "y": 20})

    assert response.status_code == 200
    (_id, status, steps), = _demonstrations(env.factory)
    assert status == DemonstrationStatus.RECORDING
    assert steps == []
    assert env.manager.screenshot_count == 0
    assert _screenshots(env.factory) == []


def test_device_lost_discards_recording(capture_env):
    env = capture_env
    _start(env.client, env.service)
    env.manager.state = "disconnected"

    response = env.client.post("/api/device/click", json={"x": 10, "y": 20})

    assert response.status_code == 503
    assert response.json()["error"]["code"] == ErrorCode.DEVICE_NOT_CONNECTED
    (_id, status, steps), = _demonstrations(env.factory)
    assert status == DemonstrationStatus.DISCARDED
    assert steps == []
    assert env.manager.screenshot_count == 0
    assert env.client.portal.call(env.lease.recording) is False


def test_stop_during_action_does_not_fail_the_response(capture_env):
    """动作成功期间用户停止录制：采集的 409 被吞掉，设备动作响应不变。"""
    env = capture_env
    _start(env.client, env.service)
    assert env.client.post("/api/device/click", json={"x": 1, "y": 2}).status_code == 200

    async def stop_during_click() -> None:
        await env.service.stop(env.service.active_id)

    env.manager.click_hook = stop_during_click

    response = env.client.post("/api/device/click", json={"x": 640, "y": 360})

    assert response.status_code == 200
    assert response.json() == {"ok": True, "backend": "core", "x": 640, "y": 360}
    (_id, status, steps), = _demonstrations(env.factory)
    assert status == DemonstrationStatus.RECORDED
    assert len(steps) == 1
    assert env.client.portal.call(env.lease.recording) is False
