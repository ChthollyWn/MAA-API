"""M6 app assembly of settings, DeviceManager and CoreClient callbacks."""

from __future__ import annotations

import asyncio
import threading
from typing import Any

from fastapi.testclient import TestClient

from maa_api.core.enums import Message
from maa_api.core.supervisor import CoreState
from maa_api.db import session as db_session
from maa_api.main import create_app


class _Supervisor:
    def __init__(self, _config, *, on_crash, on_state_change) -> None:
        self.state = CoreState.READY
        self.generation = 1
        self.pid = 71
        self._on_state_change = on_state_change

    async def start(self, **_kwargs) -> None:
        self._on_state_change(self.state)

    async def stop(self, **_kwargs) -> None:
        return None


class _Client:
    def __init__(self, _supervisor, **_kwargs) -> None:
        self.handlers: dict[str, list] = {}
        self.closed = False

    def on(self, event_type, handler) -> None:
        self.handlers.setdefault(event_type, []).append(handler)

    def start_consumer(self) -> None:
        return None

    def emit(self, event_type: str, payload: dict[str, Any]) -> None:
        for handler in self.handlers[event_type]:
            handler(payload)

    def close(self) -> None:
        self.closed = True


class _DeviceManager:
    def __init__(self, _settings, _client, *, broadcast, core_id) -> None:
        self.broadcast = broadcast
        self.core_id = core_id
        self.state = "disconnected"
        self.retry_started = threading.Event()
        self.retry_cancelled = threading.Event()
        self.events: list[tuple[str, dict]] = []
        self.reconfigure_calls: list[dict] = []
        self.closed = False

    async def connect_with_retry(self, **_kwargs) -> bool:
        self.retry_started.set()
        try:
            await asyncio.Event().wait()
        except asyncio.CancelledError:
            self.retry_cancelled.set()
            raise

    def on_core_connection_event(self, what, details) -> None:
        self.events.append((what, details))

    async def reconfigure(self, **kwargs) -> bool:
        self.reconfigure_calls.append(kwargs)
        if kwargs.get("address"):
            self.address = kwargs["address"]
        return True

    def snapshot(self) -> dict[str, Any]:
        return {
            "core_id": self.core_id,
            "state": self.state,
            "address": getattr(self, "address", "127.0.0.1:5555"),
        }

    async def close(self) -> None:
        self.closed = True


class _Runner:
    def __init__(self, *_args, **_kwargs) -> None:
        self.operation_lock = asyncio.Lock()
        self.stopped = False

    async def start(self) -> None:
        return None

    async def stop(self) -> None:
        self.stopped = True

    def wake(self) -> None:
        return None

    def notify_core_state(self, _state=None) -> None:
        return None

    def notify_core_crash(self, _record=None) -> None:
        return None

    def log_context(self, _msg, _details):
        return None


def _make_app():
    app = create_app()
    app.state.core_supervisor_factory = _Supervisor
    app.state.core_client_factory = _Client
    app.state.device_manager_factory = _DeviceManager
    app.state.pipeline_runner_factory = _Runner
    return app


def test_ready_starts_device_retry_asynchronously_and_lifespan_serves_http(
    tmp_settings, isolated_db, make_client
) -> None:
    app = _make_app()
    with TestClient(app, raise_server_exceptions=False) as client:
        manager = app.state.device_manager
        core_client = app.state.core_client
        runner = app.state.pipeline_runner
        assert manager.retry_started.wait(timeout=1)
        assert client.get("/api/system/health").status_code == 200
        assert client.get("/api/device/status").status_code == 200

    assert manager.retry_cancelled.is_set()
    assert manager.closed
    assert runner.stopped
    assert core_client.closed


def test_connection_callback_reaches_manager_and_log_translator(
    tmp_settings, isolated_db, make_client
) -> None:
    app = _make_app()
    make_client(app)
    client = app.state.core_client
    details = {"what": "Reconnected", "uuid": "fake-device"}

    client.emit(
        "CALLBACK",
        {"msg": int(Message.ConnectionInfo), "details": details},
    )

    assert app.state.device_manager.events == [("Reconnected", details)]
    assert any(
        "重连成功" in record.content
        for record in app.state.log_hub._ring
    )


def test_setting_route_hot_applies_address_but_keeps_core_path_readonly(
    tmp_settings, isolated_db, make_client
) -> None:
    app = _make_app()
    client = make_client(app)

    changed = client.put(
        "/api/settings",
        json={"items": {"adb.address": "192.0.2.33:5555"}},
    )
    assert changed.status_code == 200, changed.text
    assert app.state.device_manager.reconfigure_calls == [
        {"address": "192.0.2.33:5555"}
    ]

    readonly = client.put(
        "/api/settings",
        json={"items": {"app.maa_core_path": "/tmp/other-core"}},
    )
    assert readonly.status_code == 403
    assert readonly.json()["error"]["code"] == "SETTING_READONLY"
