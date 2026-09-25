"""Behavior contracts for device and raw agent tools."""

from __future__ import annotations

import asyncio
import base64
import json
from datetime import datetime
from types import SimpleNamespace
from typing import Any

import pytest
from PIL import Image
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker
from sqlalchemy.pool import NullPool
from sqlmodel import SQLModel

from maa_api.agent.registry import ToolContext, ToolRegistry
from maa_api.agent.policy import PolicyEngine
from maa_api.db.models import Pipeline, utcnow
from maa_api.db.session import make_engine
from maa_api.domain.enums import CallerType, PipelineSource, PipelineStatus
from maa_api.domain.errors import AppError, ErrorCode
from maa_api.services.device_service import ScreenshotResult


class FakeDeviceManager:
    address = "127.0.0.1:5555"
    common_ports = (5555, 7555)

    def __init__(self) -> None:
        self.calls: list[tuple[Any, ...]] = []
        self.current_state = "connected"
        self.connect_result = True
        self.last_error: dict[str, Any] | None = None
        self.list_error: AppError | None = None

    @property
    def state(self) -> str:
        return self.current_state

    def snapshot(self) -> dict[str, Any]:
        return {
            "state": self.current_state,
            "address": self.address,
            "resolution": {"width": 1920, "height": 1080},
            "retry": {"attempt": 1, "max": 1},
            "last_error": self.last_error,
        }

    async def list_devices(self, *, include_common_ports: bool = False):
        self.calls.append(("list_devices", include_common_ports))
        if self.list_error is not None:
            raise self.list_error
        return [{"serial": self.address, "state": "device", "is_current": True}]

    async def connect(self, *, reason: str = "manual") -> bool:
        self.calls.append(("connect", reason))
        return self.connect_result

    async def screenshot(self, *, backend: str = "adb") -> ScreenshotResult:
        self.calls.append(("screenshot", backend))
        return ScreenshotResult(Image.new("RGB", (2, 1), (10, 20, 30)), "core")

    async def click(self, x: int, y: int) -> None:
        self.calls.append(("click", x, y))

    async def swipe(self, x1: int, y1: int, x2: int, y2: int, duration_ms: int):
        self.calls.append(("swipe", x1, y1, x2, y2, duration_ms))

    async def long_press(self, x: int, y: int, duration_ms: int):
        self.calls.append(("long_press", x, y, duration_ms))

    async def input_text(self, text: str):
        self.calls.append(("input_text", text))

    async def key_event(self, key: str):
        self.calls.append(("key_event", key))

    async def back_to_home(self) -> bool:
        self.calls.append(("back_to_home",))
        return True


class FakePipelineRunner:
    core_id = "default"

    def __init__(self) -> None:
        self.operation_lock = asyncio.Lock()


def _context(
    device: FakeDeviceManager | None = None,
    runner: FakePipelineRunner | None = None,
    db_session: Any = None,
) -> ToolContext:
    state = SimpleNamespace(device_manager=device, pipeline_runner=runner)
    request = SimpleNamespace(app=SimpleNamespace(state=state))
    return ToolContext(
        caller=CallerType.INTERNAL,
        session_id="session-1",
        request_id="request-1",
        request=request,
        db_session=db_session,
    )


@pytest.fixture
def db_factory(tmp_path):
    engine = make_engine(
        f"sqlite+aiosqlite:///{tmp_path / 'device-raw-tools.db'}", poolclass=NullPool
    )

    async def initialize() -> None:
        async with engine.begin() as connection:
            await connection.run_sync(SQLModel.metadata.create_all)

    asyncio.run(initialize())
    factory = async_sessionmaker(engine, expire_on_commit=False, class_=AsyncSession)
    yield factory
    engine.sync_engine.dispose()


def _registry() -> ToolRegistry:
    from maa_api.agent.tools.device import register_tools as register_device_tools
    from maa_api.agent.tools.raw import register_tools as register_raw_tools

    registry = ToolRegistry()
    register_device_tools(registry)
    register_raw_tools(registry)
    return registry


def test_device_and_raw_tools_publish_the_complete_safe_surface() -> None:
    registry = _registry()

    definitions = {definition.name: definition for definition in registry.list()}

    assert set(definitions) == {
        "get_device_status",
        "list_devices",
        "reconnect_device",
        "trigger_screencap",
        "click",
        "swipe",
        "long_press",
        "input_text",
        "key_event",
        "back_to_home",
    }
    assert {
        name for name, definition in definitions.items() if definition.group == "device"
    } == {
        "get_device_status", "list_devices", "reconnect_device"
    }
    assert {
        name for name, definition in definitions.items() if definition.group == "raw"
    } == {
        "trigger_screencap", "click", "swipe", "long_press", "input_text",
        "key_event", "back_to_home"
    }
    assert all(
        definitions[name].risk.value == "SAFE"
        for name in (
            "get_device_status",
            "list_devices",
            "reconnect_device",
            "trigger_screencap",
            "back_to_home",
        )
    )
    assert all(
        definitions[name].risk.value == "DANGEROUS"
        for name in ("click", "swipe", "long_press", "input_text", "key_event")
    )


def test_raw_schemas_reject_arbitrary_adb_and_invalid_key_values() -> None:
    registry = _registry()

    with pytest.raises(AppError) as command_error:
        registry.validate("key_event", {"key": "BACK", "command": "rm -rf /"})
    assert command_error.value.code == ErrorCode.TOOL_ARGS_INVALID
    with pytest.raises(AppError) as key_error:
        registry.validate("key_event", {"key": "REBOOT"})
    assert key_error.value.code == ErrorCode.TOOL_ARGS_INVALID
    with pytest.raises(AppError) as coordinate_error:
        registry.validate("click", {"x": -1, "y": 2})
    assert coordinate_error.value.code == ErrorCode.TOOL_ARGS_INVALID
    key_schema = next(
        item["inputSchema"]
        for item in registry.export_schema("raw")
        if item["name"] == "key_event"
    )
    assert key_schema["properties"]["key"]["enum"] == [
        "BACK", "HOME", "ENTER", "DEL", "APP_SWITCH"
    ]


def test_only_five_raw_actions_require_session_level_authorization() -> None:
    registry = _registry()
    context = _context()

    async def scenario() -> None:
        policy = PolicyEngine()
        decisions = {
            name: await policy.evaluate(registry.get(name), {}, context)
            for name in ("click", "swipe", "long_press", "input_text", "key_event")
        }
        safe_decisions = {
            name: await policy.evaluate(registry.get(name), {}, context)
            for name in (
                "trigger_screencap",
                "back_to_home",
                "get_device_status",
                "list_devices",
                "reconnect_device",
            )
        }

        assert all(item.requires_confirmation for item in decisions.values())
        assert all(
            item.confirmation_action == "grant_atomic_ops"
            for item in decisions.values()
        )
        assert all(not item.requires_confirmation for item in safe_decisions.values())

    asyncio.run(scenario())


def test_device_tools_use_device_manager_and_return_json_values() -> None:
    registry = _registry()
    device = FakeDeviceManager()
    runner = FakePipelineRunner()

    async def scenario() -> None:
        context = _context(device, runner)
        status = await registry.execute("get_device_status", {}, context)
        candidates = await registry.execute(
            "list_devices", {"include_common_ports": True}, context
        )
        reconnect = await registry.execute("reconnect_device", {}, context)
        screenshot = await registry.execute("trigger_screencap", {}, context)

        assert status == device.snapshot()
        assert candidates == {
            "current": device.address,
            "devices": [
                {"serial": device.address, "state": "device", "is_current": True}
            ],
            "common_ports": [5555, 7555],
        }
        assert reconnect["state"] == "connected"
        assert device.calls == [
            ("list_devices", True),
            ("connect", "reconnect"),
            ("screenshot", "core"),
        ]
        assert screenshot["backend"] == "core"
        assert screenshot["width"] == 2 and screenshot["height"] == 1
        assert base64.b64decode(screenshot["image_base64"])
        datetime.fromisoformat(screenshot["captured_at"])
        json.dumps([status, candidates, reconnect, screenshot])

    asyncio.run(scenario())


def test_device_scan_maps_missing_adb_binary_to_public_error_code() -> None:
    registry = _registry()
    device = FakeDeviceManager()
    device.list_error = AppError(
        ErrorCode.DEVICE_SCAN_FAILED,
        "adb executable missing",
        {"stage": "adb_binary"},
    )

    async def scenario() -> None:
        with pytest.raises(AppError) as exc_info:
            await registry.execute("list_devices", {}, _context(device))
        assert exc_info.value.code == ErrorCode.ADB_NOT_FOUND
        assert exc_info.value.details == {"stage": "adb_binary"}

    asyncio.run(scenario())


@pytest.mark.parametrize(
    ("state", "connect_result", "last_error", "expected_code"),
    [
        ("disconnected", False, {"stage": "adb_connect"}, ErrorCode.ADB_CONNECT_FAILED),
        ("disconnected", False, {"stage": "adb_binary"}, ErrorCode.ADB_NOT_FOUND),
        ("reconnecting", False, None, ErrorCode.PIPELINE_ALREADY_RUNNING),
    ],
)
def test_reconnect_maps_failure_and_rejects_duplicate_attempt(
    state, connect_result, last_error, expected_code
) -> None:
    registry = _registry()
    device = FakeDeviceManager()
    device.current_state = state
    device.connect_result = connect_result
    device.last_error = last_error

    async def scenario() -> None:
        with pytest.raises(AppError) as exc_info:
            await registry.execute("reconnect_device", {}, _context(device))
        assert exc_info.value.code == expected_code
        if expected_code == ErrorCode.ADB_CONNECT_FAILED:
            assert exc_info.value.details == {
                "stage": "adb_connect",
                "command": "adb connect 127.0.0.1:5555",
                "output": "设备连接失败",
                "hint": "请确认模拟器已启动，且 ADB 调试端口正确",
                "address": "127.0.0.1:5555",
                "attempts": 1,
            }
        if state == "reconnecting":
            assert device.calls == []
        else:
            assert device.calls == [("connect", "reconnect")]

    asyncio.run(scenario())


@pytest.mark.parametrize(
    ("name", "arguments", "expected_call"),
    [
        ("click", {"x": 8, "y": 9}, ("click", 8, 9)),
        (
            "swipe",
            {"x1": 1, "y1": 2, "x2": 3, "y2": 4, "duration_ms": 250},
            ("swipe", 1, 2, 3, 4, 250),
        ),
        (
            "long_press",
            {"x": 6, "y": 7, "duration_ms": 800},
            ("long_press", 6, 7, 800),
        ),
        ("input_text", {"text": "hello"}, ("input_text", "hello")),
        ("key_event", {"key": "back"}, ("key_event", "BACK")),
        ("back_to_home", {}, ("back_to_home",)),
    ],
)
def test_raw_tools_dispatch_only_typed_device_actions(
    name, arguments, expected_call, db_factory
) -> None:
    registry = _registry()
    device = FakeDeviceManager()
    runner = FakePipelineRunner()

    async def scenario() -> None:
        async with db_factory() as session:
            result = await registry.execute(
                name, arguments, _context(device, runner, session)
            )
            assert result["ok"] is True
            assert device.calls == [expected_call]

    asyncio.run(scenario())


@pytest.mark.parametrize(
    ("name", "arguments"),
    [
        ("click", {"x": 10, "y": 20}),
        (
            "swipe",
            {"x1": 1, "y1": 2, "x2": 3, "y2": 4, "duration_ms": 250},
        ),
        ("long_press", {"x": 4, "y": 5, "duration_ms": 700}),
        ("input_text", {"text": "hello"}),
        ("key_event", {"key": "BACK"}),
        ("back_to_home", {}),
    ],
)
def test_raw_tools_reject_pipeline_conflict_while_holding_operation_lock(
    name, arguments, db_factory
) -> None:
    registry = _registry()
    device = FakeDeviceManager()
    runner = FakePipelineRunner()

    async def scenario() -> None:
        async with db_factory() as session:
            session.add(
                Pipeline(
                    id="pipeline-running",
                    source=PipelineSource.AGENT,
                    priority=1,
                    status=PipelineStatus.RUNNING,
                    task_count=1,
                    started_at=utcnow(),
                )
            )
            await session.commit()

        async with db_factory() as session:
            context = _context(device, runner, session)
            with pytest.raises(AppError) as exc_info:
                await registry.execute(name, arguments, context)
        assert exc_info.value.code == ErrorCode.PIPELINE_ALREADY_RUNNING
        assert exc_info.value.details == {
            "pipeline_id": "pipeline-running",
            "operation": name,
        }
        assert device.calls == []

    asyncio.run(scenario())


def test_raw_action_holds_pipeline_operation_lock_until_device_call_finishes(
    db_factory,
) -> None:
    registry = _registry()
    device = FakeDeviceManager()
    runner = FakePipelineRunner()

    async def verify_lock(x: int, y: int) -> None:
        assert runner.operation_lock.locked()
        await asyncio.sleep(0)
        device.calls.append(("click", x, y))

    device.click = verify_lock

    async def scenario() -> None:
        async with db_factory() as session:
            result = await registry.execute(
                "click", {"x": 2, "y": 3}, _context(device, runner, session)
            )
        assert result["ok"] is True
        assert device.calls == [("click", 2, 3)]

    asyncio.run(scenario())


def test_trigger_screencap_requires_a_connected_device() -> None:
    registry = _registry()
    device = FakeDeviceManager()
    device.current_state = "disconnected"

    async def scenario() -> None:
        with pytest.raises(AppError) as exc_info:
            await registry.execute("trigger_screencap", {}, _context(device))
        assert exc_info.value.code == ErrorCode.DEVICE_NOT_CONNECTED
        assert device.calls == []

    asyncio.run(scenario())
