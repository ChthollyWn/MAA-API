"""DeviceManager state, retry, scan, and operation tests use local substitutes only."""

from __future__ import annotations

import asyncio
import io
import json
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
