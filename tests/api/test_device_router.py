"""M6-03 device HTTP route behavior and direct service dispatch."""

from __future__ import annotations

import asyncio
import io
import sqlite3
from dataclasses import dataclass
from types import SimpleNamespace
from typing import Any

import pytest
from fastapi import FastAPI
from fastapi.routing import APIRoute
from PIL import Image
from sqlmodel import SQLModel

from maa_api.api.deps import get_session
from maa_api.api.errors import register_exception_handlers
from maa_api.api.routers import atomic, device
from maa_api.db import session as db_session
from maa_api.domain.errors import AppError, ErrorCode
from maa_api.domain.enums import ScreenshotBackend, ScreenshotTrigger
from maa_api.services.device_service import ScreenshotResult
from maa_api.settings import Settings


class FakeManager:
    def __init__(self) -> None:
        self.state = "connected"
        self.address = "127.0.0.1:5555"
        self.common_ports = (5555, 7555)
        self.calls: list[tuple[Any, ...]] = []
        self.retry = {"attempt": 0, "max": 0, "next_at": None}
        self.last_error: dict[str, Any] | None = None
        self.scan_error: Exception | None = None
        self.connect_result = True
        self.reconfigure_result = True
        self.screenshot_result = ScreenshotResult(Image.new("RGB", (4, 3), "red"), "adb")

    def snapshot(self) -> dict[str, Any]:
        return {
            "core_id": "default",
            "state": self.state,
            "address": self.address,
            "uuid": "test-device",
            "resolution": {"width": 4, "height": 3},
            "last_connected_at": 123.0,
            "retry": dict(self.retry),
            "last_error": self.last_error,
        }

    async def list_devices(self, *, include_common_ports: bool = False):
        self.calls.append(("list_devices", include_common_ports))
        if self.scan_error is not None:
            raise self.scan_error
        return [{
            "serial": self.address,
            "state": "device",
            "model": "TestPhone",
            "is_current": True,
            "label": f"TestPhone ({self.address})",
        }]

    async def connect(self, *, reason: str = "manual") -> bool:
        self.calls.append(("connect", reason))
        if self.connect_result:
            self.state = "connected"
        else:
            self.retry["attempt"] = 1
            self.retry["max"] = 1
        return self.connect_result

    async def reconfigure(self, *, address: str | None = None, adb_path: str | None = None) -> bool:
        self.calls.append(("reconfigure", address, adb_path))
        if address is not None:
            self.address = address
        if self.reconfigure_result:
            self.state = "connected"
        else:
            self.retry["attempt"] = 1
            self.retry["max"] = 1
        return self.reconfigure_result

    async def screenshot(self, *, backend: str = "adb") -> ScreenshotResult:
        self.calls.append(("screenshot", backend))
        return self.screenshot_result

    async def click(self, x: int, y: int) -> None:
        self.calls.append(("click", x, y))

    async def swipe(self, x1: int, y1: int, x2: int, y2: int, duration_ms: int) -> None:
        self.calls.append(("swipe", x1, y1, x2, y2, duration_ms))

    async def long_press(self, x: int, y: int, duration_ms: int) -> None:
        self.calls.append(("long_press", x, y, duration_ms))

    async def input_text(self, text: str) -> None:
        self.calls.append(("input_text", text))

    async def key_event(self, key: str) -> None:
        self.calls.append(("key_event", key))


@dataclass
class FakeSession:
    current: Any = None


class FakeRunner:
    def __init__(self) -> None:
        self.operation_lock = asyncio.Lock()


class FakeCoreRegistry:
    def __init__(self, manager: FakeManager) -> None:
        self.manager = manager

    def get(self):
        return self

    async def click(self, x: int, y: int, *, block: bool) -> None:
        assert block is True
        await self.manager.click(x, y)


@pytest.fixture
def device_app(tmp_settings: Settings, monkeypatch: pytest.MonkeyPatch):
    app = FastAPI()
    register_exception_handlers(app)
    app.include_router(device.router)
    app.include_router(atomic.router)
    app.state.device_manager = FakeManager()
    app.state.pipeline_runner = FakeRunner()
    app.state.core_registry = FakeCoreRegistry(app.state.device_manager)
    session = FakeSession()
    app.dependency_overrides[get_session] = lambda: session

    async def current_pipeline(repo):
        return repo.session.current

    monkeypatch.setattr(device.PipelineRepository, "current", current_pipeline)
    return app, app.state.device_manager, session


def test_status_and_route_registration(device_app, make_client):
    app, manager, _ = device_app
    paths = set(app.openapi()["paths"])
    assert {
        "/api/device/status",
        "/api/device/reconnect",
        "/api/device/list",
        "/api/device/candidates",
        "/api/device/screenshot",
        "/api/device/click",
        "/api/device/swipe",
        "/api/device/long_press",
        "/api/device/input_text",
        "/api/device/key_event",
    } <= paths
    assert "/api/device/click" in paths
    assert sum(
        isinstance(route, APIRoute)
        and route.path == "/api/device/click"
        and "POST" in route.methods
        for route in (*device.router.routes, *atomic.router.routes)
    ) == 1

    response = make_client(app).get("/api/device/status")
    assert response.status_code == 200
    assert response.json() == manager.snapshot()


def test_list_and_candidates_use_manager_candidates(device_app, make_client):
    app, manager, _ = device_app
    client = make_client(app)

    listed = client.get("/api/device/list?include_common_ports=true")
    candidate = client.get("/api/device/candidates")

    assert listed.status_code == candidate.status_code == 200
    assert listed.json()["current"] == manager.address
    assert listed.json()["common_ports"] == [5555, 7555]
    assert listed.json()["devices"][0]["is_current"] is True
    assert candidate.json()["devices"][0]["label"].startswith("TestPhone")
    assert manager.calls == [("list_devices", True), ("list_devices", False)]


def test_scan_binary_failure_maps_to_adb_not_found(device_app, make_client):
    app, manager, _ = device_app
    manager.scan_error = AppError(
        ErrorCode.DEVICE_SCAN_FAILED,
        "读取 ADB 设备列表失败",
        {"stage": "adb_binary", "command": "/bad/adb devices -l", "output": "missing"},
    )

    response = make_client(app).get("/api/device/list")

    assert response.status_code == 500
    assert response.json()["error"]["code"] == "ADB_NOT_FOUND"
    assert response.json()["error"]["details"]["stage"] == "adb_binary"


def test_manual_reconnect_returns_accepted_snapshot_and_applies_override(device_app, make_client):
    app, manager, _ = device_app
    manager.state = "disconnected"

    response = make_client(app).post(
        "/api/device/reconnect", json={"address": "10.0.0.2:5555", "adb_path": "/adb"}
    )

    assert response.status_code == 202
    assert response.json()["state"] == "connected"
    assert response.json()["address"] == "10.0.0.2:5555"
    assert response.json()["elapsed_ms"] >= 0
    assert manager.calls == [("reconfigure", "10.0.0.2:5555", "/adb")]


def test_manual_reconnect_reports_active_attempt_as_conflict(device_app, make_client):
    app, manager, _ = device_app
    manager.state = "reconnecting"

    response = make_client(app).post("/api/device/reconnect", json={})

    assert response.status_code == 409
    error = response.json()["error"]
    assert error["code"] == ErrorCode.PIPELINE_ALREADY_RUNNING
    assert error["details"]["operation"] == "reconnect"
    assert error["details"]["retry"]["attempt"] == 0
    assert manager.calls == []


def test_manual_reconnect_preserves_failure_diagnostics(device_app, make_client):
    app, manager, _ = device_app
    manager.state = "unavailable"
    manager.last_error = {
        "stage": "adb_shell",
        "command": "adb -s 127.0.0.1:5555 shell echo ok",
        "output": "device offline",
        "hint": "设备已连接但无响应，请尝试重启模拟器",
    }
    manager.connect_result = False

    response = make_client(app).post("/api/device/reconnect")

    assert response.status_code == 502
    error = response.json()["error"]
    assert error["code"] == "ADB_CONNECT_FAILED"
    assert error["details"]["stage"] == "adb_shell"
    assert error["details"]["command"] == manager.last_error["command"]
    assert error["details"]["output"] == "device offline"
    assert error["details"]["hint"] == manager.last_error["hint"]
    assert error["details"]["attempts"] == 1


def test_screenshot_uses_actual_backend_and_supports_size_tiers(device_app, make_client):
    app, manager, _ = device_app
    manager.screenshot_result = ScreenshotResult(Image.new("RGB", (1600, 900), "blue"), "core")
    client = make_client(app)

    image_response = client.get("/api/device/screenshot?backend=adb&format=png")
    assert image_response.status_code == 200
    assert image_response.headers["content-type"] == "image/png"
    assert image_response.headers["x-image-backend"] == "core"
    with Image.open(io.BytesIO(image_response.content)) as image:
        assert image.size == (1600, 900)
    assert manager.calls[-1] == ("screenshot", "adb")

    mobile = client.get("/api/device/screenshot?size=mobile&format=png")
    assert mobile.status_code == 200
    with Image.open(io.BytesIO(mobile.content)) as image:
        assert image.size == (1280, 720)

    thumb = client.get("/api/device/screenshot?size=thumb")
    assert thumb.status_code == 200
    assert thumb.headers["content-type"] == "image/jpeg"
    with Image.open(io.BytesIO(thumb.content)) as image:
        assert image.size == (320, 180)


def test_archive_creates_content_file_and_database_row(
    device_app, isolated_db, make_client
):
    app, manager, _ = device_app
    manager.screenshot_result = ScreenshotResult(
        Image.new("RGB", (1600, 900), "green"), "core"
    )

    async def create_schema() -> None:
        async with isolated_db.begin() as connection:
            await connection.run_sync(SQLModel.metadata.create_all)

    asyncio.run(create_schema())
    isolated_db.sync_engine.dispose()

    async def session_override():
        async with db_session.session_factory() as session:
            yield session

    app.dependency_overrides[get_session] = session_override
    archived = make_client(app).get("/api/device/screenshot?archive=true&quality=70&size=mobile")
    assert archived.status_code == 200
    body = archived.json()
    assert body["backend"] == "core"
    assert body["format"] == "jpg"
    assert body["width"] == 1600 and body["height"] == 900
    assert body["id"]
    relative_path = f"image/screenshot/{body['sha256'][:2]}/{body['sha256']}.jpg"
    assert (db_session.DB_PATH.parent / relative_path).is_file()
    with sqlite3.connect(db_session.DB_PATH) as connection:
        row = connection.execute(
            "SELECT id, trigger, backend, path, format, width, height, size_bytes "
            "FROM screenshot WHERE id = ?",
            (body["id"],),
        ).fetchone()
    assert row == (
        body["id"],
        ScreenshotTrigger.MANUAL,
        ScreenshotBackend.CORE,
        relative_path,
        "jpg",
        1600,
        900,
        body["bytes"],
    )


@pytest.mark.parametrize(
    ("path", "body", "expected"),
    [
        ("swipe", {"x1": 1, "y1": 2, "x2": 3, "y2": 4}, ("swipe", 1, 2, 3, 4, 300)),
        ("long_press", {"x": 5, "y": 6, "duration_ms": 1200}, ("long_press", 5, 6, 1200)),
        ("input_text", {"text": "A B"}, ("input_text", "A B")),
        ("key_event", {"key": "back"}, ("key_event", "BACK")),
    ],
)
def test_atomic_routes_dispatch_directly(device_app, make_client, path, body, expected):
    app, manager, _ = device_app
    response = make_client(app).post(f"/api/device/{path}", json=body)

    assert response.status_code == 200
    assert manager.calls == [expected]
    assert response.json()["backend"] == ("core" if path == "click" else "adb")


def test_click_route_uses_maacore_and_shared_conflict_contract(
    device_app, make_client
):
    app, manager, session = device_app
    session.current = SimpleNamespace(id="pipe-1")
    client = make_client(app)

    denied = client.post("/api/device/click", json={"x": 1, "y": 2})
    assert denied.status_code == 409
    assert manager.calls == []

    allowed = client.post("/api/device/click", json={"x": 1, "y": 2, "force": True})
    assert allowed.status_code == 200
    assert allowed.json()["backend"] == "core"
    assert manager.calls == [("click", 1, 2)]


def test_atomic_conflict_and_force_semantics(device_app, make_client):
    app, manager, session = device_app
    session.current = SimpleNamespace(id="pipe-1")
    client = make_client(app)

    denied = client.post("/api/device/click", json={"x": 1, "y": 2})
    assert denied.status_code == 409
    assert denied.json()["error"]["code"] == "PIPELINE_ALREADY_RUNNING"
    assert manager.calls == []

    allowed = client.post("/api/device/click", json={"x": 1, "y": 2, "force": True})
    assert allowed.status_code == 200
    assert manager.calls == [("click", 1, 2)]


def test_key_event_rejects_shell_like_values(device_app, make_client):
    app, manager, _ = device_app

    response = make_client(app).post(
        "/api/device/key_event", json={"key": "KEYCODE_BACK; rm -rf /"}
    )

    assert response.status_code == 400
    assert response.json()["error"]["code"] == "INVALID_PARAMETER"
    assert response.json()["error"]["details"]["allowed"] == [
        "APP_SWITCH", "BACK", "DEL", "ENTER", "HOME"
    ]
    assert manager.calls == []


def test_missing_device_manager_is_a_controlled_service_error(tmp_settings, make_client):
    app = FastAPI()
    register_exception_handlers(app)
    app.include_router(device.router)

    response = make_client(app).get("/api/device/status")

    assert response.status_code == 503
    assert response.json()["error"]["code"] == "SERVICE_UNAVAILABLE"
