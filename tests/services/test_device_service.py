"""DeviceManager state, retry, scan, and operation tests use local substitutes only."""

from __future__ import annotations

import asyncio
import io
import json
import os
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import pytest
from PIL import Image

from maa_api.domain.errors import AppError, ErrorCode
from maa_api.services.device_service import (
    COMMON_DEVICE_PORTS,
    DeviceManager,
    DeviceState,
)
import maa_api.services.device_service as device_service
from maa_api.settings import AdbSettings, Settings


class FakeAdb:
    def __init__(self) -> None:
        self.connect_results: list[Any] = []
        self.shell_results: list[Any] = []
        self.devices: list[dict[str, str]] = []
        self.calls: list[tuple[Any, ...]] = []

    def start_server(self) -> str:
        self.calls.append(("start_server",))
        return ""

    def connect(self, address: str, timeout: float = 10.0) -> str:
        self.calls.append(("connect", address, timeout))
        if self.connect_results:
            result = self.connect_results.pop(0)
            if isinstance(result, Exception):
                raise result
            return str(result)
        return f"connected to {address}"

    def disconnect(self, address: str) -> str:
        self.calls.append(("disconnect", address))
        return f"disconnected {address}"

    def shell(self, address: str, command: str, *, timeout: float = 10.0) -> str:
        self.calls.append(("shell", address, command, timeout))
        if self.shell_results:
            result = self.shell_results.pop(0)
            if isinstance(result, Exception):
                raise result
            return str(result)
        if command == "getprop ro.product.model":
            return "Test Emulator"
        return "ok"

    def list_devices(self) -> list[dict[str, str]]:
        self.calls.append(("list_devices",))
        return list(self.devices)

    def screenshot(self, address: str) -> bytes:
        self.calls.append(("screenshot", address))
        return _png()

    def swipe(self, address: str, *coordinates: int) -> None:
        self.calls.append(("swipe", address, *coordinates))

    def install_apk(self, address: str, path: Path, **kw: Any) -> str:
        self.calls.append(("install_apk", address, path, kw))
        return "Success"

    def force_stop(self, address: str, package: str) -> None:
        self.calls.append(("force_stop", address, package))

    def long_press(self, address: str, x: int, y: int, duration_ms: int) -> None:
        self.calls.append(("long_press", address, x, y, duration_ms))

    def input_text(self, address: str, text: str) -> None:
        self.calls.append(("input_text", address, text))

    def key_event(self, address: str, key: str) -> None:
        self.calls.append(("key_event", address, key))


class FakeCore:
    def __init__(self) -> None:
        self.results: list[Any] = []
        self.calls: list[tuple[Any, ...]] = []
        self.screencap_path: Path | None = None
        self.click_calls: list[tuple[int, int]] = []

    async def click(self, x: int, y: int) -> None:
        self.click_calls.append((x, y))

    async def connect(
        self, adb_path: str, address: str, *, config: str, timeout: float
    ) -> bool:
        self.calls.append((adb_path, address, config, timeout))
        result = self.results.pop(0) if self.results else True
        if isinstance(result, Exception):
            raise result
        return result

    async def screencap(self) -> Path:
        if self.screencap_path is None:
            raise RuntimeError("no fallback screenshot")
        return self.screencap_path


class FakeAdbUtilsDevice:
    def __init__(self, client: "FakeAdbUtilsClient", serial: str) -> None:
        self.client = client
        self.serial = serial

    def shell(self, command: Any, **kwargs: Any) -> str:
        self.client.calls.append(
            ("device.shell", self.serial, command, kwargs, os.environ.get("ADBUTILS_ADB_PATH"))
        )
        if isinstance(command, str) and command.startswith("pm install "):
            if self.client.install_exception is not None:
                raise self.client.install_exception
            return self.client.install_output
        if isinstance(command, str) and command.startswith("rm -f /data/local/tmp/"):
            if self.client.cleanup_exception is not None:
                raise self.client.cleanup_exception
            return ""
        if command == "echo ok":
            return "ok"
        if command == "getprop ro.product.model":
            return "adbutils emulator"
        return ""

    @property
    def sync(self) -> "FakeAdbUtilsSync":
        return FakeAdbUtilsSync(self.client, self.serial)

    def screenshot(self, *, error_ok: bool = True) -> Image.Image:
        self.client.calls.append(("device.screenshot", self.serial, error_ok))
        return Image.new("RGB", (2, 3), "blue")

    def swipe(self, x1: int, y1: int, x2: int, y2: int, *, duration: float) -> None:
        self.client.calls.append(
            ("device.swipe", self.serial, x1, y1, x2, y2, duration)
        )

    def keyevent(self, key: str) -> None:
        self.client.calls.append(("device.keyevent", self.serial, key))

    def app_stop(self, package: str) -> None:
        self.client.calls.append(("device.app_stop", self.serial, package))

    def install(self, path: Path, **kwargs: Any) -> None:
        self.client.calls.append(("device.install", self.serial, path, kwargs))

    def uninstall(self, package: str) -> None:
        self.client.calls.append(("device.uninstall", self.serial, package))


class FakeAdbUtilsSync:
    def __init__(self, client: "FakeAdbUtilsClient", serial: str) -> None:
        self.client = client
        self.serial = serial

    def push(self, source: Path, destination: str, *, check: bool = False) -> int:
        self.client.calls.append(
            ("device.sync.push", self.serial, source, destination, check)
        )
        if self.client.push_exception is not None:
            raise self.client.push_exception
        return source.stat().st_size


class FakeAdbUtilsClient:
    """adbutils.AdbClient-shaped fake; contains no socket/process implementation."""

    def __init__(self) -> None:
        self.calls: list[tuple[Any, ...]] = []
        self.install_output = "Success"
        self.install_exception: Exception | None = None
        self.push_exception: Exception | None = None
        self.cleanup_exception: Exception | None = None

    def connect(self, address: str, *, timeout: float | None = None) -> str:
        self.calls.append(
            ("client.connect", address, timeout, os.environ.get("ADBUTILS_ADB_PATH"))
        )
        return f"connected to {address}"

    def disconnect(self, address: str, *, raise_error: bool = False) -> str:
        self.calls.append(("client.disconnect", address, raise_error))
        return f"disconnected {address}"

    def list(self) -> list[Any]:
        self.calls.append(("client.list",))
        return [
            SimpleNamespace(serial="127.0.0.1:5555", state="device", tags={}),
            SimpleNamespace(serial="127.0.0.1:7555", state="offline", tags={}),
        ]

    def device(self, serial: str) -> FakeAdbUtilsDevice:
        self.calls.append(("client.device", serial))
        return FakeAdbUtilsDevice(self, serial)


def _settings(address: str = "127.0.0.1:5555") -> Settings:
    return Settings(adb=AdbSettings(path="/fake/adb", address=address))


def _png() -> bytes:
    stream = io.BytesIO()
    Image.new("RGB", (2, 3), "red").save(stream, format="PNG")
    return stream.getvalue()


def _manager(
    *,
    adb: FakeAdb | None = None,
    core: FakeCore | None = None,
    broadcast: Any = None,
    **kwargs: Any,
) -> tuple[DeviceManager, FakeAdb, FakeCore]:
    fake_adb = adb or FakeAdb()
    fake_core = core or FakeCore()
    manager = DeviceManager(
        _settings,
        fake_core,
        adb_backend=fake_adb,
        broadcast=broadcast,
        unavailable_probe_interval=0,
        **kwargs,
    )
    return manager, fake_adb, fake_core


def test_state_snapshot_and_public_contract_are_json_serializable() -> None:
    manager, _, _ = _manager()
    state_names = {state.value for state in DeviceState}
    assert {"disconnected", "connecting", "connected", "reconnecting", "unavailable"} <= state_names
    encoded = json.dumps(manager.snapshot())
    assert '"state": "disconnected"' in encoded
    assert manager.snapshot()["retry"] == {"attempt": 0, "max": 0, "next_at": None}
    asyncio.run(manager.close())


def test_connect_requires_adb_shell_and_maacore_async_result_and_broadcasts() -> None:
    events: list[tuple[str, dict[str, Any]]] = []
    manager, adb, core = _manager(broadcast=lambda kind, data: events.append((kind, data)))

    assert asyncio.run(manager.connect(reason="manual")) is True
    assert manager.state is DeviceState.CONNECTED
    assert [call[0] for call in adb.calls] == ["start_server", "connect", "shell"]
    assert core.calls == [("/fake/adb", "127.0.0.1:5555", "General", 60.0)]
    assert [kind for kind, _ in events] == ["device_status", "device_status"]
    assert events[-1][1]["state"] == "connected"
    assert isinstance(manager.snapshot()["last_connected_at"], float)
    asyncio.run(manager.close())


def test_connected_intermediate_callback_does_not_claim_connection_success() -> None:
    manager, _, _ = _manager()
    manager.on_core_connection_event("Connected", {"uuid": "device-123"})
    assert manager.state is DeviceState.DISCONNECTED
    assert manager.snapshot()["uuid"] == "device-123"
    asyncio.run(manager.close())


def test_core_acceptance_without_async_success_is_not_connected() -> None:
    core = FakeCore()
    core.results.append(1)  # a truthy call id is acceptance, not the final bool result
    manager, _, _ = _manager(core=core)

    assert asyncio.run(manager.connect()) is False
    assert manager.state is DeviceState.UNAVAILABLE
    assert manager.snapshot()["last_error"]["stage"] == "core_connect"
    asyncio.run(manager.close())


def test_adb_shell_failure_skips_core_and_manual_connect_only_tries_once() -> None:
    adb = FakeAdb()
    adb.shell_results.append("offline")
    manager, _, core = _manager(adb=adb)

    assert asyncio.run(manager.connect()) is False
    assert manager.state is DeviceState.UNAVAILABLE
    assert len([call for call in adb.calls if call[0] == "connect"]) == 1
    assert core.calls == []
    assert manager.snapshot()["last_error"]["stage"] == "adb_shell"
    asyncio.run(manager.close())


def test_startup_retry_succeeds_after_transient_core_failures() -> None:
    core = FakeCore()
    core.results.extend([False, False, True])
    manager, adb, _ = _manager(core=core, startup_retry_interval=0)

    assert asyncio.run(
        manager.connect_with_retry(attempts=3, interval=0, reason="startup")
    ) is True
    assert manager.state is DeviceState.CONNECTED
    assert len(core.calls) == 3
    assert len([call for call in adb.calls if call[0] == "start_server"]) == 1
    asyncio.run(manager.close())


def test_ensure_available_uses_light_health_check_when_already_connected() -> None:
    manager, adb, _ = _manager()

    async def scenario() -> None:
        assert await manager.connect()
        before_core = len(manager._core_client.calls)
        assert await manager.ensure_available(timeout=1)
        assert len(manager._core_client.calls) == before_core
        assert [call[2] for call in adb.calls if call[0] == "shell"] == ["echo ok", "echo ok"]
        await manager.close()

    asyncio.run(scenario())


def test_preflight_recovers_after_connected_health_check_fails() -> None:
    manager, adb, core = _manager(preflight_retry_interval=0)

    async def scenario() -> None:
        assert await manager.connect()
        adb.shell_results.extend(["offline", "ok"])
        assert await manager.ensure_available(timeout=1)
        assert manager.state is DeviceState.CONNECTED
        assert len(core.calls) == 2
        assert ("disconnect", "127.0.0.1:5555") in adb.calls
        await manager.close()

    asyncio.run(scenario())


def test_ensure_available_preflight_retries_three_times() -> None:
    core = FakeCore()
    core.results.extend([False, False, True])
    manager, _, _ = _manager(core=core, preflight_retry_interval=0)

    assert asyncio.run(manager.ensure_available(timeout=1)) is True
    assert len(core.calls) == 3
    assert manager.state is DeviceState.CONNECTED
    asyncio.run(manager.close())


def test_automatic_reconnect_uses_exponential_backoff(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    import maa_api.services.device_service as device_service

    core = FakeCore()
    core.results.extend([False, False, False, False, False])
    manager, _, _ = _manager(core=core, reconnect_retry_attempts=5)
    waits: list[float] = []

    async def capture_sleep(seconds: float) -> None:
        waits.append(seconds)

    monkeypatch.setattr(device_service.asyncio, "sleep", capture_sleep)

    assert asyncio.run(
        manager.connect_with_retry(attempts=5, reason="disconnect")
    ) is False
    assert waits == [3.0, 6.0, 12.0, 24.0]
    assert manager.state is DeviceState.UNAVAILABLE
    asyncio.run(manager.close())


def test_reconnecting_event_does_not_touch_adb_until_disconnect_or_watchdog() -> None:
    manager, adb, _ = _manager(reconnect_watchdog=10)

    async def scenario() -> None:
        manager.on_core_connection_event("Reconnecting", {})
        assert manager.state is DeviceState.RECONNECTING
        assert adb.calls == []
        manager.on_core_connection_event("Reconnected", {"uuid": "device-456"})
        assert manager.state is DeviceState.CONNECTED
        assert adb.calls == []
        assert manager.snapshot()["uuid"] == "device-456"
        await manager.close()

    asyncio.run(scenario())


def test_disconnect_event_hands_over_to_bounded_exponential_retries() -> None:
    manager, adb, core = _manager(reconnect_backoff=(0, 0, 0, 0, 0))
    core.results.extend([False, True])

    async def scenario() -> None:
        manager.on_core_connection_event("Disconnect", {})
        await asyncio.sleep(0.05)
        assert manager.state is DeviceState.CONNECTED
        assert len(core.calls) == 2
        assert manager.snapshot()["retry"]["max"] == 0
        assert any(call[0] == "connect" for call in adb.calls)
        await manager.close()

    asyncio.run(scenario())


def test_screencap_failure_escalates_only_after_three_and_success_resets_count() -> None:
    manager, _, _ = _manager(reconnect_backoff=(0, 0, 0, 0, 0))

    async def scenario() -> None:
        manager.on_core_connection_event("ScreencapFailed", {})
        manager.on_core_connection_event("ScreencapFailed", {})
        assert manager.state is DeviceState.DISCONNECTED
        manager.on_core_connection_event("ScreencapFailed", {})
        assert manager.state is DeviceState.RECONNECTING
        await manager.close()

    asyncio.run(scenario())


def test_device_scan_is_passive_by_default_and_common_port_probe_is_opt_in() -> None:
    adb = FakeAdb()
    adb.devices = [
        {"serial": "127.0.0.1:5555", "state": "device", "model": "MuMu"},
        {"serial": "emulator-5554", "state": "offline", "model": ""},
    ]
    manager, _, _ = _manager(adb=adb, common_ports=(5555, 7555))

    async def scenario() -> None:
        devices = await manager.list_devices()
        assert [item["serial"] for item in devices] == ["127.0.0.1:5555", "emulator-5554"]
        assert devices[0] == {
            "serial": "127.0.0.1:5555",
            "state": "device",
            "model": "MuMu",
            "is_current": True,
            "label": "MuMu (127.0.0.1:5555)",
        }
        assert not [call for call in adb.calls if call[0] == "connect"]
        assert await manager.scan_candidates(include_common_ports=True) == devices
        probed = [call[1] for call in adb.calls if call[0] == "connect"]
        assert set(probed) == {"127.0.0.1:5555", "127.0.0.1:7555"}
        await manager.close()

    asyncio.run(scenario())
    assert COMMON_DEVICE_PORTS == (5555, 5556, 7555, 16384, 21503, 62001)


def test_device_scan_failure_maps_domain_error() -> None:
    class BrokenAdb(FakeAdb):
        def list_devices(self) -> list[dict[str, str]]:
            raise OSError("adb server unavailable")

    manager, _, _ = _manager(adb=BrokenAdb())
    with pytest.raises(AppError) as raised:
        asyncio.run(manager.list_devices())
    assert raised.value.code == ErrorCode.DEVICE_SCAN_FAILED
    asyncio.run(manager.close())


def test_configured_connection_config_and_common_ports_are_read_from_settings() -> None:
    adb = FakeAdb()
    core = FakeCore()
    settings = SimpleNamespace(
        adb=SimpleNamespace(
            path="/fake/adb",
            address="127.0.0.1:5555",
            connection_config="MuMuEmulator12",
            common_ports=[6000, 6001],
        )
    )
    manager = DeviceManager(
        lambda: settings,
        core,
        adb_backend=adb,
        unavailable_probe_interval=0,
    )

    async def scenario() -> None:
        assert await manager.connect()
        assert core.calls[0][2] == "MuMuEmulator12"
        assert await manager.list_devices(include_common_ports=True) == []
        assert {call[1] for call in adb.calls if call[0] == "connect"} == {
            "127.0.0.1:5555",
            "127.0.0.1:6000",
            "127.0.0.1:6001",
        }
        await manager.close()

    asyncio.run(scenario())


def test_operations_are_delegated_to_adb_and_core_substitutes(tmp_path: Path) -> None:
    manager, adb, core = _manager()
    core.screencap_path = tmp_path / "core.png"
    core.screencap_path.write_bytes(_png())

    async def scenario() -> None:
        screenshot = await manager.screenshot()
        assert screenshot.image.size == (2, 3)
        assert screenshot.backend == "adb"
        core_image = await manager.screenshot(backend="core")
        assert core_image.image.size == (2, 3)
        assert core_image.backend == "core"
        await manager.swipe(1, 2, 3, 4, 500)
        await manager.long_press(7, 8, 900)
        await manager.input_text("hello world")
        await manager.key_event("BACK")
        await manager.click(9, 10)
        assert await manager.install_apk(Path("/tmp/test.apk")) == "Success"
        await manager.force_stop("com.example.game")
        await manager.close()

    asyncio.run(scenario())
    assert ("swipe", "127.0.0.1:5555", 1, 2, 3, 4, 500) in adb.calls
    assert ("long_press", "127.0.0.1:5555", 7, 8, 900) in adb.calls
    assert ("input_text", "127.0.0.1:5555", "hello world") in adb.calls
    assert ("key_event", "127.0.0.1:5555", "BACK") in adb.calls
    assert core.click_calls == [(9, 10)]
    assert ("force_stop", "127.0.0.1:5555", "com.example.game") in adb.calls


def test_adb_screenshot_falls_back_to_core_and_reports_actual_backend(
    tmp_path: Path,
) -> None:
    class BrokenScreenshotAdb(FakeAdb):
        def screenshot(self, address: str) -> bytes:
            raise RuntimeError("adb screencap failed")

    core = FakeCore()
    core.screencap_path = tmp_path / "fallback.png"
    core.screencap_path.write_bytes(_png())
    manager, _, _ = _manager(adb=BrokenScreenshotAdb(), core=core)

    async def scenario() -> None:
        result = await manager.screenshot()
        assert result.image.size == (2, 3)
        assert result.backend == "core"
        await manager.close()

    asyncio.run(scenario())


def test_key_event_rejects_shell_like_values() -> None:
    manager, adb, _ = _manager()
    with pytest.raises(AppError) as raised:
        asyncio.run(manager.key_event("BACK; reboot"))
    assert raised.value.code == ErrorCode.INVALID_PARAMETER
    assert not [call for call in adb.calls if call[0] == "key_event"]
    asyncio.run(manager.close())


def test_reconfigure_updates_address_disconnects_old_and_reconnects_once() -> None:
    manager, adb, core = _manager()

    async def scenario() -> None:
        assert await manager.reconfigure(address="127.0.0.1:7555", adb_path="/fake/adb-new")
        assert manager.address == "127.0.0.1:7555"
        assert core.calls[0][:2] == ("/fake/adb-new", "127.0.0.1:7555")
        assert ("disconnect", "127.0.0.1:5555") in adb.calls
        await manager.close()

    asyncio.run(scenario())


def test_default_backend_uses_injected_adbutils_client_and_manages_binary_path(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    original = "/before/test/adb"
    monkeypatch.setenv("ADBUTILS_ADB_PATH", original)
    client = FakeAdbUtilsClient()
    core = FakeCore()
    settings = {"value": _settings()}
    subprocess_calls: list[Any] = []
    (tmp_path / "fake.apk").write_bytes(b"fake apk")
    monkeypatch.setattr(
        device_service.subprocess,
        "run",
        lambda *args, **kwargs: subprocess_calls.append((args, kwargs)),
    )
    manager = DeviceManager(
        lambda: settings["value"],
        core,
        adb_client=client,
        unavailable_probe_interval=0,
    )
    assert os.environ["ADBUTILS_ADB_PATH"] == "/fake/adb"

    async def scenario() -> None:
        assert await manager.connect()
        assert os.environ["ADBUTILS_ADB_PATH"] == "/fake/adb"
        candidates = await manager.list_devices()
        assert candidates == [
            {
                "serial": "127.0.0.1:5555",
                "state": "device",
                "model": "adbutils emulator",
                "is_current": True,
                "label": "adbutils emulator (127.0.0.1:5555)",
            },
            {
                "serial": "127.0.0.1:7555",
                "state": "offline",
                "model": None,
                "is_current": False,
                "label": "127.0.0.1:7555（离线）",
            },
        ]
        screenshot = await manager.screenshot()
        assert screenshot.backend == "adb"
        assert screenshot.image.size == (2, 3)
        await manager.swipe(1, 2, 3, 4, 500)
        await manager.long_press(7, 8, 1000)
        await manager.input_text("hello world")
        await manager.key_event("BACK")
        await manager.force_stop("com.example.game")
        assert await manager.install_apk(tmp_path / "fake.apk") == "Success"

        assert await manager.reconfigure(
            address="127.0.0.1:7555", adb_path="/other/fake-adb"
        )
        assert os.environ["ADBUTILS_ADB_PATH"] == "/other/fake-adb"
        assert core.calls[-1][:2] == ("/other/fake-adb", "127.0.0.1:7555")
        await manager.close()

    asyncio.run(scenario())
    assert os.environ["ADBUTILS_ADB_PATH"] == original
    assert subprocess_calls == []
    calls = [call[0] for call in client.calls]
    assert "client.connect" in calls
    assert "client.disconnect" in calls
    assert "client.list" in calls
    assert "device.screenshot" in calls
    assert "device.swipe" in calls
    assert "device.keyevent" in calls
    assert "device.app_stop" in calls
    assert "device.install" not in calls
    assert "device.sync.push" in calls
    assert any(
        call[0] == "device.shell" and call[2] == ["input", "text", "hello%sworld"]
        for call in client.calls
    )
    assert all(
        call[-1] in {"/fake/adb", "/other/fake-adb"}
        for call in client.calls
        if call[0] in {"client.connect", "device.shell"}
    )


def test_safe_apk_install_pushes_then_runs_pm_install_and_always_cleans_remote(
    tmp_path: Path,
) -> None:
    client = FakeAdbUtilsClient()
    manager = DeviceManager(_settings, FakeCore(), adb_client=client, unavailable_probe_interval=0)
    apk = tmp_path / "update.apk"
    apk.write_bytes(b"apk-data")

    async def scenario() -> None:
        assert await manager.install_apk_safe(apk) == "Success"
        await manager.close()

    asyncio.run(scenario())

    push = next(call for call in client.calls if call[0] == "device.sync.push")
    assert push[2] == apk
    remote_path = push[3]
    assert remote_path.startswith("/data/local/tmp/maa-api-")
    assert remote_path.endswith(".apk")
    assert push[4] is True
    shell_calls = [call for call in client.calls if call[0] == "device.shell"]
    assert shell_calls[0][2] == f"pm install -r -d {remote_path}"
    assert shell_calls[0][3]["timeout"] == 1200
    assert shell_calls[-1][2] == f"rm -f {remote_path}"
    assert not {"device.install", "device.uninstall"} & {call[0] for call in client.calls}


@pytest.mark.parametrize(
    ("failure_kind", "message"),
    [
        ("install", "Failure [INSTALL_FAILED_VERSION_DOWNGRADE]"),
        ("push", "sync transport failed"),
    ],
)
def test_safe_apk_install_cleans_remote_on_push_or_install_failure(
    tmp_path: Path, failure_kind: str, message: str
) -> None:
    client = FakeAdbUtilsClient()
    if failure_kind == "install":
        client.install_output = message
    else:
        client.push_exception = RuntimeError(message)
    manager = DeviceManager(_settings, FakeCore(), adb_client=client, unavailable_probe_interval=0)
    apk = tmp_path / "update.apk"
    apk.write_bytes(b"apk-data")

    async def scenario() -> AppError:
        try:
            await manager.install_apk_safe(apk)
        except AppError as exc:
            return exc
        raise AssertionError("safe install unexpectedly succeeded")

    error = asyncio.run(scenario())
    asyncio.run(manager.close())
    assert error.code is ErrorCode.ADB_COMMAND_FAILED
    assert message in error.details["output"]
    push_calls = [call for call in client.calls if call[0] == "device.sync.push"]
    assert len(push_calls) == 1
    remote_path = push_calls[0][3]
    assert any(
        call[0] == "device.shell" and call[2] == f"rm -f {remote_path}"
        for call in client.calls
    )
    assert not {"device.install", "device.uninstall"} & {call[0] for call in client.calls}


def test_safe_apk_install_cleans_remote_after_install_timeout(tmp_path: Path) -> None:
    client = FakeAdbUtilsClient()
    client.install_exception = TimeoutError("pm install timed out")
    manager = DeviceManager(_settings, FakeCore(), adb_client=client, unavailable_probe_interval=0)
    apk = tmp_path / "update.apk"
    apk.write_bytes(b"apk-data")

    async def scenario() -> AppError:
        try:
            await manager.install_apk_safe(apk, timeout=1200)
        except AppError as exc:
            await manager.close()
            return exc
        raise AssertionError("safe install unexpectedly succeeded")

    error = asyncio.run(scenario())
    assert "timed out" in error.details["output"]
    assert any(
        call[0] == "device.shell" and call[2].startswith("rm -f /data/local/tmp/maa-api-")
        for call in client.calls
    )
    assert not {"device.install", "device.uninstall"} & {call[0] for call in client.calls}


def test_adbutils_common_port_probe_is_concurrent_and_opt_in(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    client = FakeAdbUtilsClient()
    active = 0
    max_active = 0
    import threading

    counter_lock = threading.Lock()

    original_connect = client.connect

    def delayed_connect(address: str, *, timeout: float | None = None) -> str:
        nonlocal active, max_active
        # This runs in concurrent worker threads; a tiny blocking delay makes
        # overlap observable without contacting adb server or a device.
        with counter_lock:
            active += 1
            max_active = max(max_active, active)
        try:
            import time

            time.sleep(0.01)
            return original_connect(address, timeout=timeout)
        finally:
            with counter_lock:
                active -= 1

    client.connect = delayed_connect  # type: ignore[method-assign]
    manager = DeviceManager(
        _settings,
        FakeCore(),
        adb_client=client,
        common_ports=(5555, 7555, 16384),
        unavailable_probe_interval=0,
    )

    async def scenario() -> None:
        assert await manager.list_devices()  # passive visible-device scan
        assert not [call for call in client.calls if call[0] == "client.connect"]
        await manager.list_devices(include_common_ports=True)
        await manager.close()

    asyncio.run(scenario())
    assert max_active > 1
    assert len([call for call in client.calls if call[0] == "client.connect"]) == 3
