"""Device connection management and ADB operations (docs/08)."""
from __future__ import annotations

import asyncio
import io
import json
import logging
import math
import os
import shlex
import subprocess
import threading
import time
import uuid
from dataclasses import dataclass
from enum import StrEnum
from pathlib import Path
from typing import Any, Callable, Literal, Mapping

from PIL import Image
import adbutils

from maa_api.domain.errors import AppError, ErrorCode
from maa_api.services.log_hub import create_task_without_request_id

__all__ = [
    "COMMON_DEVICE_PORTS",
    "DeviceCandidate",
    "DeviceManager",
    "DeviceState",
    "ScreenshotResult",
]

logger = logging.getLogger(__name__)
COMMON_DEVICE_PORTS = (5555, 5556, 7555, 16384, 21503, 62001)
STARTUP_RETRY_ATTEMPTS, STARTUP_RETRY_INTERVAL = 60, 5.0
PREFLIGHT_RETRY_ATTEMPTS, PREFLIGHT_RETRY_INTERVAL = 3, 2.0
RECONNECT_RETRY_ATTEMPTS = 5
RECONNECT_BACKOFF = (3.0, 6.0, 12.0, 24.0, 48.0)
RECONNECT_WATCHDOG_SECONDS = 90.0
UNAVAILABLE_PROBE_INTERVAL = 60.0
ADB_HEALTH_TIMEOUT, CORE_CONNECT_TIMEOUT = 3.0, 60.0
_ADBUTILS_PATH_KEY = "ADBUTILS_ADB_PATH"
_ADBUTILS_PATH_LOCK = threading.RLock()
_ADBUTILS_PATH_OWNERS: dict[object, str] = {}
_ADBUTILS_PATH_ORIGINAL: str | None = None
_ADBUTILS_PATH_ORIGINAL_SET = False
_ADBUTILS_PATH_LAST_SET: str | None = None


class DeviceState(StrEnum):
    DISCONNECTED = "disconnected"
    CONNECTING = "connecting"
    CONNECTED = "connected"
    RECONNECTING = "reconnecting"
    UNAVAILABLE = "unavailable"


@dataclass(frozen=True, slots=True)
class DeviceCandidate:
    serial: str
    state: str
    model: str | None
    is_current: bool
    label: str

    def as_dict(self) -> dict[str, Any]:
        return {
            "serial": self.serial, "state": self.state, "model": self.model,
            "is_current": self.is_current, "label": self.label,
        }


@dataclass(frozen=True, slots=True)
class ScreenshotResult:
    """Captured image and the backend that actually supplied it."""

    image: Image.Image
    backend: Literal["adb", "core"]


@dataclass(slots=True)
class _AdbFailure(Exception):
    stage: str
    command: str
    output: str

    def __str__(self) -> str:
        return self.output or f"ADB 命令失败：{self.command}"


class _AdbBackend:
    """ADBUtils client adapter; the executable is used only to start adb server."""

    def __init__(
        self,
        path_provider: Callable[[], str],
        client: Any = None,
    ) -> None:
        self._path_provider = path_provider
        self._client = adbutils.adb if client is None else client
        self._injected_client = client is not None
        self._path_owner = object()
        self._closed = False
        self.configure_path(self._path_provider())

    def configure_path(self, path: str) -> None:
        """Publish the selected binary for adbutils without clobbering it at close."""
        global _ADBUTILS_PATH_ORIGINAL, _ADBUTILS_PATH_ORIGINAL_SET
        global _ADBUTILS_PATH_LAST_SET
        resolved = str(path or "adb")
        with _ADBUTILS_PATH_LOCK:
            if not _ADBUTILS_PATH_OWNERS:
                _ADBUTILS_PATH_ORIGINAL = os.environ.get(_ADBUTILS_PATH_KEY)
                _ADBUTILS_PATH_ORIGINAL_SET = _ADBUTILS_PATH_KEY in os.environ
            else:
                _ADBUTILS_PATH_OWNERS.pop(self._path_owner, None)
            _ADBUTILS_PATH_OWNERS[self._path_owner] = resolved
            os.environ[_ADBUTILS_PATH_KEY] = resolved
            _ADBUTILS_PATH_LAST_SET = resolved

    def _sync_path(self) -> None:
        self.configure_path(self._path_provider())

    def _run(self, *args: str, timeout: float = 10.0) -> str:
        command = [self._path_provider(), *map(str, args)]
        environment = os.environ.copy()
        environment[_ADBUTILS_PATH_KEY] = command[0]
        try:
            result = subprocess.run(
                command,
                capture_output=True,
                text=True,
                timeout=timeout,
                check=False,
                env=environment,
            )
        except FileNotFoundError as exc:
            raise _AdbFailure("adb_binary", shlex.join(command), str(exc)) from exc
        except subprocess.TimeoutExpired as exc:
            output = exc.stderr or exc.stdout or f"命令超过 {timeout:g}s"
            raise _AdbFailure(_stage_for_command(args), shlex.join(command), str(output)) from exc
        output = "\n".join(
            part for part in (result.stdout.strip(), result.stderr.strip()) if part
        )
        if result.returncode:
            raise _AdbFailure(_stage_for_command(args), shlex.join(command), output)
        return result.stdout.strip()

    def start_server(self) -> str:
        self._sync_path()
        # Injected clients are isolated test/integration transports. They must
        # never cause a real adb executable to run as a side effect.
        if self._injected_client:
            start = getattr(self._client, "start_server", None)
            return str(start()) if callable(start) else ""
        return self._run("start-server")

    def connect(self, address: str, timeout: float = 10.0) -> str:
        self._sync_path()
        output = str(self._client.connect(address, timeout=timeout))
        lowered = output.lower()
        if any(s in lowered for s in ("unable to connect", "failed to connect", "cannot connect")):
            raise _AdbFailure(
                "adb_connect",
                f"{self._path_provider()} connect {address}",
                output,
            )
        return output

    def disconnect(self, address: str) -> str:
        self._sync_path()
        try:
            return str(self._client.disconnect(address, raise_error=False))
        except TypeError:
            # Test doubles and alternate supported adbutils clients may expose
            # the one-argument form.
            return str(self._client.disconnect(address))

    def shell(self, address: str, command: str, *, timeout: float = 10.0) -> str:
        self._sync_path()
        return str(
            self._client.device(address).shell(
                command, timeout=timeout, encoding="utf-8"
            )
        )

    def list_devices(self) -> list[Any]:
        self._sync_path()
        return list(self._client.list())

    def screenshot(self, address: str) -> Image.Image:
        self._sync_path()
        return self._client.device(address).screenshot(error_ok=False)

    def swipe(
        self, address: str, x1: int, y1: int, x2: int, y2: int, duration_ms: int
    ) -> str:
        self._sync_path()
        self._client.device(address).swipe(
            x1, y1, x2, y2, duration=max(int(duration_ms), 0) / 1000
        )
        return ""

    def long_press(self, address: str, x: int, y: int, duration_ms: int) -> str:
        return self.swipe(address, x, y, x, y, duration_ms)

    def input_text(self, address: str, text: str) -> str:
        self._sync_path()
        # Android's input tool uses %s to represent spaces. Supplying argv
        # through AdbDevice.shell avoids passing caller text to a host shell.
        encoded = text.replace("%", r"\%").replace(" ", "%s")
        return str(
            self._client.device(address).shell(["input", "text", encoded])
        )

    def key_event(self, address: str, key: str) -> str:
        codes = {
            "BACK": "KEYCODE_BACK",
            "HOME": "KEYCODE_HOME",
            "ENTER": "KEYCODE_ENTER",
            "DEL": "KEYCODE_DEL",
            "APP_SWITCH": "KEYCODE_APP_SWITCH",
        }
        self._sync_path()
        self._client.device(address).keyevent(codes[key])
        return ""

    def install_apk(self, address: str, path: Path, **options: Any) -> str:
        flags = []
        if options.pop("replace", True):
            flags.append("-r")
        if options.pop("allow_test", True):
            flags.append("-t")
        if options.pop("allow_downgrade", False):
            flags.append("-d")
        return self.install_apk_safe(
            address,
            Path(path),
            timeout=float(options.pop("timeout", 1200)),
            flags=tuple(flags),
        )

    def install_apk_safe(
        self,
        address: str,
        path: Path,
        *,
        timeout: float = 1200,
        flags: tuple[str, ...] = ("-r", "-d"),
    ) -> str:
        """Push and install an APK without adbutils' uninstall-and-retry path."""
        self._sync_path()
        timeout = float(timeout)
        if not math.isfinite(timeout) or timeout <= 0:
            raise ValueError("timeout must be a positive finite number")

        # Never derive a device path from caller-controlled input. A unique name
        # also keeps simultaneous installs from overwriting one another.
        remote_path = f"/data/local/tmp/maa-api-{uuid.uuid4().hex}.apk"
        command = ["pm", "install", *map(str, flags), remote_path]
        install_command = shlex.join(command)
        device = self._client.device(address)
        operation_error: BaseException | None = None
        try:
            device.sync.push(Path(path), remote_path, check=True)
            output = str(
                device.shell(install_command, timeout=timeout, encoding="utf-8")
            )
            if output.strip() != "Success":
                raise _AdbFailure("adb_install", install_command, output)
            return "Success"
        except BaseException as exc:
            operation_error = exc
            raise
        finally:
            cleanup_command = shlex.join(["rm", "-f", remote_path])
            try:
                device.shell(cleanup_command, timeout=10.0, encoding="utf-8")
            except Exception as cleanup_error:
                if operation_error is None:
                    raise _AdbFailure(
                        "adb_install_cleanup",
                        cleanup_command,
                        str(cleanup_error),
                    ) from cleanup_error
                logger.warning(
                    "清理远端临时 APK 失败 path=%s error=%s",
                    remote_path,
                    cleanup_error,
                )

    def force_stop(self, address: str, package: str) -> str:
        self._sync_path()
        self._client.device(address).app_stop(package)
        return ""

    def close(self) -> None:
        global _ADBUTILS_PATH_ORIGINAL, _ADBUTILS_PATH_ORIGINAL_SET
        global _ADBUTILS_PATH_LAST_SET
        if self._closed:
            return
        self._closed = True
        with _ADBUTILS_PATH_LOCK:
            owned_path = _ADBUTILS_PATH_OWNERS.pop(self._path_owner, None)
            if owned_path is None:
                return
            if _ADBUTILS_PATH_OWNERS:
                restore = next(reversed(_ADBUTILS_PATH_OWNERS.values()))
                os.environ[_ADBUTILS_PATH_KEY] = restore
                _ADBUTILS_PATH_LAST_SET = restore
            else:
                if os.environ.get(_ADBUTILS_PATH_KEY) == _ADBUTILS_PATH_LAST_SET:
                    if _ADBUTILS_PATH_ORIGINAL_SET:
                        os.environ[_ADBUTILS_PATH_KEY] = str(_ADBUTILS_PATH_ORIGINAL)
                    else:
                        os.environ.pop(_ADBUTILS_PATH_KEY, None)
                _ADBUTILS_PATH_ORIGINAL = None
                _ADBUTILS_PATH_ORIGINAL_SET = False
                _ADBUTILS_PATH_LAST_SET = None


def _stage_for_command(args: tuple[str, ...]) -> str:
    if args and args[0] == "connect":
        return "adb_connect"
    if "shell" in args or "exec-out" in args:
        return "adb_shell"
    if args and args[0] == "devices":
        return "adb_scan"
    return "adb_command"


class DeviceManager:
    """Single source of truth for the default device connection."""

    def __init__(
        self,
        settings_provider: Any,
        core_client: Any,
        *,
        adb_backend: Any = None,
        adb_client: Any = None,
        broadcast: Callable[[str, dict[str, Any]], None] | None = None,
        core_id: str = "default",
        common_ports: tuple[int, ...] | None = None,
        reconnect_watchdog: float = RECONNECT_WATCHDOG_SECONDS,
        unavailable_probe_interval: float = UNAVAILABLE_PROBE_INTERVAL,
        startup_retry_attempts: int = STARTUP_RETRY_ATTEMPTS,
        startup_retry_interval: float = STARTUP_RETRY_INTERVAL,
        preflight_retry_attempts: int = PREFLIGHT_RETRY_ATTEMPTS,
        preflight_retry_interval: float = PREFLIGHT_RETRY_INTERVAL,
        reconnect_retry_attempts: int = RECONNECT_RETRY_ATTEMPTS,
        reconnect_backoff: tuple[float, ...] = RECONNECT_BACKOFF,
    ) -> None:
        self._settings_provider = settings_provider
        self._core_client = core_client
        self._address_override: str | None = None
        self._adb_path_override: str | None = None
        self._backend = adb_backend or _AdbBackend(
            lambda: self._adb_path, client=adb_client
        )
        self._broadcast = broadcast or (lambda _kind, _data: None)
        self.core_id = core_id
        self._common_ports_override = (
            tuple(int(port) for port in common_ports)
            if common_ports is not None
            else None
        )
        self._state = DeviceState.DISCONNECTED
        self._uuid: str | None = None
        self._resolution: dict[str, int] | None = None
        self._last_connected_at: float | None = None
        self._last_error: dict[str, Any] | None = None
        self._retry_attempt = 0
        self._retry_max = 0
        self._next_at: float | None = None
        self._active_reason = "startup"
        self._force_adb_disconnect = False
        self._screencap_failures = 0
        self._state_changed = asyncio.Event()
        self._attempt_lock = asyncio.Lock()
        self._tasks: set[asyncio.Task[Any]] = set()
        self._watchdog_task: asyncio.Task[Any] | None = None
        self._probe_task: asyncio.Task[Any] | None = None
        self._closed = False
        self.reconnect_watchdog = max(float(reconnect_watchdog), 0.0)
        self.unavailable_probe_interval = max(float(unavailable_probe_interval), 0.0)
        self.startup_retry_attempts = max(int(startup_retry_attempts), 1)
        self.startup_retry_interval = max(float(startup_retry_interval), 0.0)
        self.preflight_retry_attempts = max(int(preflight_retry_attempts), 1)
        self.preflight_retry_interval = max(float(preflight_retry_interval), 0.0)
        self.reconnect_retry_attempts = max(int(reconnect_retry_attempts), 1)
        self.reconnect_backoff = tuple(max(float(n), 0.0) for n in reconnect_backoff)

    @property
    def state(self) -> DeviceState:
        return self._state

    @property
    def address(self) -> str:
        return self._address_override or str(
            _nested_value(self._settings(), "adb", "address", default="")
        )

    @property
    def _adb_path(self) -> str:
        return self._adb_path_override or str(
            _nested_value(self._settings(), "adb", "path", default="adb")
        )

    @property
    def _connection_config(self) -> str:
        return str(
            _nested_value(
                self._settings(), "adb", "connection_config", default="General"
            )
            or "General"
        )

    @property
    def common_ports(self) -> tuple[int, ...]:
        configured = self._common_ports_override
        if configured is None:
            configured = _nested_value(
                self._settings(), "adb", "common_ports", default=COMMON_DEVICE_PORTS
            )
        return tuple(int(port) for port in configured)

    def snapshot(self) -> dict[str, Any]:
        """Return the REST/WS status payload using JSON-serializable values."""
        return {
            "core_id": self.core_id,
            "state": self.state.value,
            "address": self.address,
            "uuid": self._uuid,
            "resolution": dict(self._resolution) if self._resolution else None,
            "last_connected_at": self._last_connected_at,
            "retry": {
                "attempt": self._retry_attempt,
                "max": self._retry_max,
                "next_at": self._next_at,
            },
            "last_error": dict(self._last_error) if self._last_error else None,
        }

    async def connect(self, *, reason: str = "manual") -> bool:
        """Try exactly once, as required by the manual reconnect endpoint."""
        if self._closed or self._attempt_lock.locked():
            return False
        self._cancel_probe()
        async with self._attempt_lock:
            self._active_reason = reason
            self._retry_attempt = self._retry_max = 1
            self._next_at = None
            state = (
                DeviceState.RECONNECTING
                if reason in {"reconnect", "watchdog"}
                else DeviceState.CONNECTING
            )
            self._set_state(state, f"设备连接尝试（{reason}）")
            if await self._connect_once():
                self._connected()
                return True
            self._failed(reason)
            return False

    async def connect_with_retry(
        self,
        *,
        attempts: int | None = None,
        interval: float | None = None,
        reason: str = "startup",
    ) -> bool:
        """Run startup/preflight/automatic retries in the caller's async task."""
        count = max(int(attempts if attempts is not None else self.startup_retry_attempts), 1)
        delay = max(float(interval if interval is not None else self.startup_retry_interval), 0.0)
        recovering = reason in {
            "reconnect", "disconnect", "watchdog", "probe", "screencap_failed"
        }
        if self._closed or self._attempt_lock.locked():
            return False
        self._cancel_probe()
        async with self._attempt_lock:
            self._active_reason = reason
            recovering_state = recovering or (
                reason == "preflight" and self.state == DeviceState.RECONNECTING
            )
            self._retry_max = count
            for attempt in range(1, count + 1):
                if self._closed:
                    return False
                self._retry_attempt = attempt
                self._next_at = None
                self._set_state(
                    DeviceState.RECONNECTING
                    if recovering_state
                    else DeviceState.CONNECTING,
                    f"设备连接尝试 {attempt}/{count}（{reason}）",
                )
                if await self._connect_once():
                    self._connected()
                    return True
                if attempt < count:
                    wait = (
                        self.reconnect_backoff[
                            min(attempt - 1, len(self.reconnect_backoff) - 1)
                        ]
                        if recovering and self.reconnect_backoff
                        else delay
                    )
                    self._next_at = time.time() + wait
                    self._publish_status()
                    await asyncio.sleep(wait)
            self._failed(reason)
            return False

    async def disconnect(self) -> None:
        self._cancel_watchdog()
        self._cancel_probe()
        address = self.address
        try:
            if address:
                await asyncio.to_thread(self._backend.disconnect, address)
        except Exception as exc:
            logger.warning("断开 ADB 设备失败 address=%s error=%s", address, exc)
        self._last_error = None
        self._retry_attempt = self._retry_max = 0
        self._next_at = None
        self._set_state(DeviceState.DISCONNECTED, "设备已断开")

    async def ensure_available(self, timeout: float) -> bool:
        """Verify a live ADB shell, or perform three preflight connection attempts."""
        deadline = asyncio.get_running_loop().time() + max(float(timeout), 0.0)
        if self.state in {DeviceState.CONNECTING, DeviceState.RECONNECTING}:
            if not await self._wait_until_settled(deadline):
                return False
            if await self._healthy_connected():
                return True
        if self.state == DeviceState.CONNECTED and await self._healthy_connected():
            return True
        remaining = deadline - asyncio.get_running_loop().time()
        if remaining <= 0:
            return False
        try:
            return await asyncio.wait_for(
                self.connect_with_retry(
                    attempts=self.preflight_retry_attempts,
                    interval=self.preflight_retry_interval,
                    reason="preflight",
                ),
                timeout=remaining,
            )
        except asyncio.TimeoutError:
            if self.state in {DeviceState.CONNECTING, DeviceState.RECONNECTING}:
                self._last_error = {
                    "stage": "core_connect",
                    "output": f"任务前设备预检超过 {timeout:g}s",
                    "hint": _failure_hint("core_connect"),
                }
                self._failed("preflight_timeout")
            return False

    async def list_devices(self, *, include_common_ports: bool = False) -> list[dict[str, Any]]:
        """List known devices; only probe common emulator ports when requested."""
        try:
            if include_common_ports:
                await self._scan_common_ports()
            raw_devices = await asyncio.to_thread(self._backend.list_devices)
        except Exception as exc:
            raise AppError(
                ErrorCode.DEVICE_SCAN_FAILED,
                "读取 ADB 设备列表失败",
                self._error_details("adb_scan", exc),
            ) from exc
        result: list[dict[str, Any]] = []
        for raw in raw_devices:
            serial, state, model = _device_fields(raw)
            if not model and state == "device":
                try:
                    model = (
                        await asyncio.to_thread(
                            self._backend.shell,
                            serial,
                            "getprop ro.product.model",
                            timeout=ADB_HEALTH_TIMEOUT,
                        )
                    ).strip() or None
                except Exception:
                    model = None
            current = serial == self.address
            if model:
                label = f"{model} ({serial})"
            elif state == "device":
                label = serial
            else:
                label = f"{serial}（{_state_label(state)}）"
            result.append(DeviceCandidate(serial, state, model, current, label).as_dict())
        return result

    async def scan_candidates(
        self, *, include_common_ports: bool = False
    ) -> list[dict[str, Any]]:
        """Alias for the explicit candidate scan route."""
        return await self.list_devices(include_common_ports=include_common_ports)

    async def _scan_common_ports(self) -> None:
        async def probe(port: int) -> None:
            address = f"127.0.0.1:{port}"
            try:
                await asyncio.to_thread(self._backend.connect, address, timeout=2.0)
            except Exception:
                pass

        await asyncio.gather(*(probe(port) for port in self.common_ports))

    def on_core_connection_event(self, what: str, details: dict[str, Any]) -> None:
        """Drive state from MaaCore callbacks, respecting its internal reconnect."""
        details = details if isinstance(details, dict) else {}
        if what == "Reconnecting":
            self._cancel_watchdog()
            self._set_state(DeviceState.RECONNECTING, "MaaCore 正在尝试自动重连")
            if self.reconnect_watchdog:
                self._watchdog_task = self._spawn(
                    self._watchdog(), "maa-device-reconnect-watchdog"
                )
        elif what == "Reconnected":
            self._cancel_watchdog()
            self._cancel_auto_reconnect()
            self._capture_device_info(details)
            self._connected("MaaCore 已恢复设备连接")
        elif what == "Disconnect":
            self._cancel_watchdog()
            self._set_state(DeviceState.RECONNECTING, "MaaCore 放弃重连，设备管理器接管")
            self._start_reconnect("disconnect")
        elif what in {"ConnectFailed", "ConnectFaild"}:
            if self.state == DeviceState.CONNECTING:
                self._last_error = {
                    "stage": "core_connect",
                    "output": _format_details(details),
                    "hint": _failure_hint("core_connect"),
                }
                self._publish_status()
        elif what in {"ResolutionGot", "ResolutionInfo"}:
            self._capture_device_info(details)
            self._publish_status()
        elif what == "ScreencapFailed":
            self._screencap_failures += 1
            if self._screencap_failures >= 3:
                self._set_state(
                    DeviceState.RECONNECTING,
                    "连续三次截图失败，设备管理器接管重连",
                )
                self._start_reconnect("screencap_failed")
        elif what == "Connected":
            # Connected precedes AsyncCallInfo and is only an intermediate event.
            self._capture_device_info(details)

    async def screenshot(self, backend: str = "adb") -> ScreenshotResult:
        """Capture through ADB by default, or explicitly request MaaCore."""
        if backend == "core":
            try:
                return ScreenshotResult(
                    _as_image(Path(await self._core_client.screencap())), "core"
                )
            except Exception as exc:
                raise AppError(
                    ErrorCode.SCREENSHOT_FAILED,
                    "MaaCore 截图失败",
                    {"backend": "core", "error": str(exc)},
                ) from exc
        if backend != "adb":
            raise AppError(
                ErrorCode.INVALID_PARAMETER,
                "截图 backend 必须是 adb 或 core",
                {"backend": backend},
            )
        try:
            image = await asyncio.to_thread(self._backend.screenshot, self.address)
            result = ScreenshotResult(_as_image(image), "adb")
            self._screencap_failures = 0
            return result
        except Exception as adb_error:
            self.on_core_connection_event("ScreencapFailed", {"error": str(adb_error)})
            try:
                image = ScreenshotResult(
                    _as_image(Path(await self._core_client.screencap())), "core"
                )
                self._screencap_failures = 0
                return image
            except Exception as core_error:
                raise AppError(
                    ErrorCode.SCREENSHOT_FAILED,
                    "ADB 与 MaaCore 截图均失败",
                    {
                        "backend": "adb+maacore",
                        "adb_error": str(adb_error),
                        "core_error": str(core_error),
                    },
                ) from core_error

    async def swipe(
        self, x1: int, y1: int, x2: int, y2: int, duration_ms: int
    ) -> None:
        try:
            await asyncio.to_thread(
                self._backend.swipe,
                self.address,
                int(x1),
                int(y1),
                int(x2),
                int(y2),
                int(duration_ms),
            )
        except Exception as exc:
            raise AppError(
                ErrorCode.ADB_COMMAND_FAILED,
                "ADB 滑动操作失败",
                self._error_details("adb_command", exc),
            ) from exc

    async def click(self, x: int, y: int) -> None:
        """Click through MaaCore's native asynchronous click API."""
        await self._core_client.click(int(x), int(y))

    async def long_press(self, x: int, y: int, duration_ms: int) -> None:
        try:
            await asyncio.to_thread(
                self._backend.long_press,
                self.address,
                int(x),
                int(y),
                int(duration_ms),
            )
        except Exception as exc:
            raise AppError(
                ErrorCode.ADB_COMMAND_FAILED,
                "ADB 长按操作失败",
                self._error_details("adb_command", exc),
            ) from exc

    async def input_text(self, text: str) -> None:
        try:
            await asyncio.to_thread(self._backend.input_text, self.address, str(text))
        except Exception as exc:
            raise AppError(
                ErrorCode.ADB_COMMAND_FAILED,
                "ADB 输入文本失败",
                self._error_details("adb_command", exc),
            ) from exc

    async def key_event(self, key: str) -> None:
        allowed = {"BACK", "HOME", "ENTER", "DEL", "APP_SWITCH"}
        normalized = str(key).upper()
        if normalized not in allowed:
            raise AppError(
                ErrorCode.INVALID_PARAMETER,
                "按键不在允许列表中",
                {"key": key, "allowed": sorted(allowed)},
            )
        try:
            await asyncio.to_thread(self._backend.key_event, self.address, normalized)
        except Exception as exc:
            raise AppError(
                ErrorCode.ADB_COMMAND_FAILED,
                "ADB 按键操作失败",
                self._error_details("adb_command", exc),
            ) from exc

    async def install_apk(self, path: Path, **kw: Any) -> str:
        try:
            return await asyncio.to_thread(
                self._backend.install_apk, self.address, Path(path), **kw
            )
        except Exception as exc:
            raise AppError(
                ErrorCode.ADB_COMMAND_FAILED,
                "安装 APK 失败",
                self._error_details("adb_command", exc),
            ) from exc

    async def install_apk_safe(
        self,
        path: Path,
        *,
        timeout: float = 1200,
        flags: tuple[str, ...] = ("-r", "-d"),
    ) -> str:
        try:
            return await asyncio.to_thread(
                self._backend.install_apk_safe,
                self.address,
                Path(path),
                timeout=timeout,
                flags=flags,
            )
        except Exception as exc:
            raise AppError(
                ErrorCode.ADB_COMMAND_FAILED,
                "安全安装 APK 失败",
                self._error_details("adb_install", exc),
            ) from exc

    async def force_stop(self, package: str) -> None:
        try:
            await asyncio.to_thread(self._backend.force_stop, self.address, package)
        except Exception as exc:
            raise AppError(
                ErrorCode.ADB_COMMAND_FAILED,
                f"停止应用进程失败：{package}",
                self._error_details("adb_command", exc),
            ) from exc

    async def reconfigure(
        self, *, address: str | None = None, adb_path: str | None = None
    ) -> bool:
        """Apply ADB setting overrides, release a changed address, and try once."""
        old_address = self.address
        if address is not None:
            self._address_override = str(address)
        if adb_path is not None:
            self._adb_path_override = str(adb_path)
        if old_address and old_address != self.address:
            try:
                await asyncio.to_thread(self._backend.disconnect, old_address)
            except Exception as exc:
                logger.warning(
                    "切换 ADB 地址时断开旧设备失败 address=%s error=%s",
                    old_address,
                    exc,
                )
        configure_path = getattr(self._backend, "configure_path", None)
        if callable(configure_path):
            configure_path(self._adb_path)
        self._cancel_watchdog()
        self._cancel_probe()
        return await self.connect(reason="reconfigure")

    async def close(self) -> None:
        """Cancel owned watchdog, retry, and probe jobs."""
        if self._closed:
            return
        self._closed = True
        self._cancel_watchdog()
        for task in tuple(self._tasks):
            task.cancel()
        tasks = tuple(self._tasks)
        if tasks:
            await asyncio.gather(*tasks, return_exceptions=True)
        self._tasks.clear()
        self._probe_task = None
        close_backend = getattr(self._backend, "close", None)
        if callable(close_backend):
            close_backend()

    async def _connect_once(self) -> bool:
        address, adb_path = self.address, self._adb_path
        if not address:
            self._last_error = {
                "stage": "adb_connect",
                "command": "adb connect <empty address>",
                "output": "ADB 地址未配置",
                "hint": _failure_hint("adb_connect"),
            }
            return False
        try:
            if self._retry_attempt == 1:
                await asyncio.to_thread(self._backend.start_server)
            if self._force_adb_disconnect or self._active_reason in {
                "reconnect",
                "disconnect",
                "watchdog",
                "screencap_failed",
                "reconfigure",
            }:
                try:
                    await asyncio.to_thread(self._backend.disconnect, address)
                except Exception as exc:
                    # A disconnected transport may be reported as an adb error.
                    logger.debug(
                        "ADB 预清理连接失败 address=%s error=%s", address, exc
                    )
                self._force_adb_disconnect = False
            output = await asyncio.to_thread(self._backend.connect, address)
            health = await asyncio.to_thread(
                self._backend.shell,
                address,
                "echo ok",
                timeout=ADB_HEALTH_TIMEOUT,
            )
            if health.strip() != "ok":
                raise _AdbFailure(
                    "adb_shell",
                    f"{adb_path} -s {address} shell echo ok",
                    health,
                )
        except Exception as exc:
            self._last_error = self._error_details(
                getattr(exc, "stage", "adb_connect"), exc
            )
            logger.warning(
                "ADB 设备健康检查失败 address=%s stage=%s error=%s",
                address,
                self._last_error.get("stage"),
                exc,
            )
            return False
        try:
            connected = await self._core_client.connect(
                adb_path,
                address,
                config=self._connection_config,
                timeout=CORE_CONNECT_TIMEOUT,
            )
            if connected is not True:
                raise RuntimeError(
                    "CoreClient.connect 未确认 MaaCore 异步连接成功 "
                    f"（返回 {connected!r}；ADB 输出：{output}）"
                )
            return True
        except Exception as exc:
            self._last_error = {
                "stage": "core_connect",
                "command": f"CoreClient.connect({adb_path!r}, {address!r})",
                "output": str(exc),
                "hint": _failure_hint("core_connect"),
            }
            logger.warning("MaaCore 异步设备连接失败 address=%s error=%s", address, exc)
            return False

    async def _healthy_connected(self) -> bool:
        if self.state != DeviceState.CONNECTED:
            return False
        try:
            output = await asyncio.to_thread(
                self._backend.shell,
                self.address,
                "echo ok",
                timeout=ADB_HEALTH_TIMEOUT,
            )
            if output.strip() == "ok":
                return True
            raise _AdbFailure(
                "adb_shell",
                f"{self._adb_path} -s {self.address} shell echo ok",
                output,
            )
        except Exception as exc:
            self._last_error = self._error_details("adb_shell", exc)
            self._force_adb_disconnect = True
            self._set_state(DeviceState.RECONNECTING, "任务前设备健康检查失败")
            return False

    async def _wait_until_settled(self, deadline: float) -> bool:
        loop = asyncio.get_running_loop()
        while self.state in {DeviceState.CONNECTING, DeviceState.RECONNECTING}:
            remaining = deadline - loop.time()
            if remaining <= 0:
                return False
            changed = self._state_changed
            changed.clear()
            if self.state not in {DeviceState.CONNECTING, DeviceState.RECONNECTING}:
                break
            try:
                await asyncio.wait_for(changed.wait(), remaining)
            except asyncio.TimeoutError:
                return False
        return self.state == DeviceState.CONNECTED

    def _set_state(self, state: DeviceState, reason: str) -> None:
        changed = self._state != state
        self._state = state
        self._state_changed.set()
        self._publish_status()
        if changed:
            logger.info(
                "设备状态变更 core_id=%s state=%s address=%s reason=%s",
                self.core_id,
                state.value,
                self.address,
                reason,
            )

    def _publish_status(self) -> None:
        try:
            self._broadcast("device_status", self.snapshot())
        except Exception:
            logger.exception("广播 device_status 失败")

    def _connected(self, reason: str = "ADB 与 MaaCore 均已确认连接") -> None:
        self._last_connected_at = time.time()
        self._last_error = None
        self._retry_attempt = self._retry_max = 0
        self._next_at = None
        self._screencap_failures = 0
        self._cancel_watchdog()
        self._cancel_probe()
        self._set_state(DeviceState.CONNECTED, reason)

    def _failed(self, reason: str) -> None:
        if self._last_error is None:
            self._last_error = {
                "stage": "core_connect",
                "output": f"连接失败（{reason}）",
                "hint": _failure_hint("core_connect"),
            }
        self._next_at = None
        self._set_state(DeviceState.UNAVAILABLE, "设备重试次数耗尽")
        self._start_probe()

    def _capture_device_info(self, details: Mapping[str, Any]) -> None:
        nested = details.get("details")
        values = nested if isinstance(nested, Mapping) else {}
        uuid = details.get("uuid") or values.get("uuid")
        if uuid:
            self._uuid = str(uuid)
        width = details.get("width", values.get("width"))
        height = details.get("height", values.get("height"))
        try:
            if int(width) > 0 and int(height) > 0:
                self._resolution = {"width": int(width), "height": int(height)}
        except (TypeError, ValueError):
            pass

    async def _watchdog(self) -> None:
        try:
            await asyncio.sleep(self.reconnect_watchdog)
            if self.state == DeviceState.RECONNECTING and not self._closed:
                logger.warning(
                    "MaaCore 重连看门狗超时 address=%s timeout=%ss，设备管理器接管",
                    self.address,
                    self.reconnect_watchdog,
                )
                self._start_reconnect("watchdog")
        except asyncio.CancelledError:
            raise
        finally:
            if self._watchdog_task is asyncio.current_task():
                self._watchdog_task = None

    def _start_reconnect(self, reason: str) -> None:
        if any(
            task.get_name() == "maa-device-auto-reconnect" and not task.done()
            for task in self._tasks
        ):
            return
        self._spawn(
            self.connect_with_retry(
                attempts=self.reconnect_retry_attempts,
                interval=0,
                reason=reason,
            ),
            "maa-device-auto-reconnect",
        )

    def _start_probe(self) -> None:
        if self.unavailable_probe_interval <= 0 or self._closed:
            return
        if self._probe_task is None or self._probe_task.done():
            self._probe_task = self._spawn(
                self._probe_unavailable(), "maa-device-probe"
            )

    async def _probe_unavailable(self) -> None:
        try:
            while not self._closed and self.state == DeviceState.UNAVAILABLE:
                await asyncio.sleep(self.unavailable_probe_interval)
                if self.state != DeviceState.UNAVAILABLE:
                    return
                try:
                    output = await asyncio.to_thread(
                        self._backend.shell,
                        self.address,
                        "echo ok",
                        timeout=ADB_HEALTH_TIMEOUT,
                    )
                except Exception:
                    continue
                if output.strip() == "ok":
                    self._start_reconnect("probe")
                    return
        except asyncio.CancelledError:
            raise
        finally:
            if self._probe_task is asyncio.current_task():
                self._probe_task = None

    def _spawn(self, coroutine: Any, name: str) -> asyncio.Task[Any]:
        task = create_task_without_request_id(coroutine, name=name)
        self._tasks.add(task)
        task.add_done_callback(self._tasks.discard)
        return task

    def _cancel_watchdog(self) -> None:
        task, self._watchdog_task = self._watchdog_task, None
        if task is not None and task is not asyncio.current_task() and not task.done():
            task.cancel()

    def _cancel_probe(self) -> None:
        task, self._probe_task = self._probe_task, None
        if task is not None and task is not asyncio.current_task() and not task.done():
            task.cancel()

    def _cancel_auto_reconnect(self) -> None:
        current = asyncio.current_task()
        for task in tuple(self._tasks):
            if (
                task.get_name() == "maa-device-auto-reconnect"
                and task is not current
                and not task.done()
            ):
                task.cancel()

    def _error_details(self, stage: str, exc: Exception) -> dict[str, Any]:
        return {
            "stage": stage,
            "command": getattr(exc, "command", None),
            "output": getattr(exc, "output", None) or str(exc),
            "hint": _failure_hint(stage),
        }

    def _settings(self) -> Any:
        return (
            self._settings_provider()
            if callable(self._settings_provider)
            else self._settings_provider
        )


def _nested_value(value: Any, section: str, key: str, *, default: Any = None) -> Any:
    nested = value.get(section) if isinstance(value, Mapping) else getattr(value, section, None)
    if isinstance(nested, Mapping):
        return nested.get(key, default)
    return getattr(nested, key, default)


def _device_fields(raw: Any) -> tuple[str, str, str | None]:
    if isinstance(raw, Mapping):
        serial = str(raw.get("serial", raw.get("device", "")))
        state = str(raw.get("state", "unknown"))
        model = raw.get("model")
    else:
        serial = str(getattr(raw, "serial", ""))
        state = str(getattr(raw, "state", "unknown"))
        model = getattr(raw, "model", None)
        if not model:
            tags = getattr(raw, "tags", None)
            if isinstance(tags, Mapping):
                model = tags.get("model")
    return serial, state, str(model).strip() if model else None


def _as_image(value: Any) -> Image.Image:
    if isinstance(value, Image.Image):
        result = value.copy()
        result.load()
        return result
    if isinstance(value, Path):
        with Image.open(value) as image:
            result = image.copy()
            result.load()
            return result
    if isinstance(value, (bytes, bytearray, memoryview)):
        with Image.open(io.BytesIO(bytes(value))) as image:
            result = image.copy()
            result.load()
            return result
    raise TypeError(f"不支持的截图结果：{type(value).__name__}")


def _state_label(state: str) -> str:
    return {"offline": "离线", "unauthorized": "未授权"}.get(state, state)


def _failure_hint(stage: str) -> str:
    return {
        "adb_binary": "请在设置页检查 ADB 路径，或将 adb 加入 PATH",
        "adb_connect": "请确认模拟器已启动，且 ADB 调试端口正确",
        "adb_shell": "设备已连接但无响应，请尝试重启模拟器",
        "core_connect": "内核无法连接设备，可能是分辨率不受支持或触控方案不兼容",
    }.get(stage, "请检查设备连接与 ADB 命令输出")


def _format_details(details: Mapping[str, Any]) -> str:
    try:
        return json.dumps(details, ensure_ascii=False, default=str)
    except (TypeError, ValueError):
        return repr(details)
