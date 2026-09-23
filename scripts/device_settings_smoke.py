#!/usr/bin/env python3
"""Isolated M6 settings/device/lifespan integration smoke; no real MaaCore or ADB."""

from __future__ import annotations

import io
import json
import os
import shutil
import sqlite3
import tempfile
import time
from pathlib import Path
from typing import Any

from fastapi.testclient import TestClient
from PIL import Image
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker
from sqlalchemy.pool import NullPool

REPO_ROOT = Path(__file__).resolve().parents[1]


class _Supervisor:
    def __init__(self, _config, *, on_crash, on_state_change) -> None:
        from maa_api.core.supervisor import CoreState

        self.state = CoreState.READY
        self.generation = 1
        self.pid = 24680
        self._on_state_change = on_state_change

    async def start(self, **_kwargs) -> None:
        self._on_state_change(self.state)

    async def stop(self, **_kwargs) -> None:
        return None


class _CoreClient:
    def __init__(self, _supervisor, **_kwargs) -> None:
        self.handlers: dict[str, list] = {}
        self.connect_calls: list[tuple[str, str, dict[str, Any]]] = []

    def on(self, event_type, handler) -> None:
        self.handlers.setdefault(event_type, []).append(handler)

    def start_consumer(self) -> None:
        return None

    async def connect(self, adb_path, address, **kwargs) -> bool:
        self.connect_calls.append((adb_path, address, kwargs))
        return True

    def close(self) -> None:
        return None


class _ADBBackend:
    def __init__(self) -> None:
        self.connect_calls: list[str] = []
        self.disconnect_calls: list[str] = []
        self._screenshot = io.BytesIO()
        Image.new("RGB", (13, 9), "#243746").save(self._screenshot, format="PNG")
        self.screenshot_bytes = self._screenshot.getvalue()

    def start_server(self) -> str:
        return "started"

    def connect(self, address: str, timeout: float = 10.0) -> str:
        self.connect_calls.append(address)
        if address == "127.0.0.1:5555":
            raise OSError("simulated emulator is offline")
        return f"connected to {address}"

    def disconnect(self, address: str) -> str:
        self.disconnect_calls.append(address)
        return "disconnected"

    def shell(self, address: str, command: str, *, timeout: float = 10.0) -> str:
        return "ok"

    def screenshot(self, address: str) -> bytes:
        return self.screenshot_bytes

    def list_devices(self):
        return []


def _wait_until(predicate, timeout: float = 3.0) -> bool:
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if predicate():
            return True
        time.sleep(0.01)
    return bool(predicate())


def main() -> int:
    import maa_api.db.session as db_session
    import maa_api.main as main_module
    import maa_api.settings as settings_module
    from maa_api.services.device_service import DeviceManager

    temp_root = Path(tempfile.mkdtemp(prefix="maa-device-settings-smoke-")).resolve()
    resource_root = temp_root / "resource"
    resource_root.mkdir()
    core_root = temp_root / "fake-core"
    (core_root / "debug").mkdir(parents=True)
    config_path = temp_root / "config.yaml"
    config_path.write_text(
        "app:\n"
        "  access_token: ''\n"
        f"  maa_core_path: {json.dumps(str(core_root))}\n"
        "adb:\n"
        "  path: /fake/adb\n"
        "  address: 127.0.0.1:5555\n"
        "  screenshot_quality: 70\n"
        "log:\n"
        "  flush_interval: 0.02\n",
        encoding="utf-8",
    )

    saved_env = {key: value for key, value in os.environ.items() if key.startswith("MAA_")}
    saved = {
        "DB_PATH": db_session.DB_PATH,
        "ASYNC_URL": db_session.ASYNC_URL,
        "SYNC_URL": db_session.SYNC_URL,
        "engine": db_session.engine,
        "session_factory": db_session.session_factory,
        "DEFAULT_CONFIG_PATH": settings_module.DEFAULT_CONFIG_PATH,
        "settings_cache": settings_module.get_settings(),
    }
    factory_names = (
        "core_supervisor_factory",
        "core_client_factory",
        "device_manager_factory",
        "pipeline_runner_factory",
    )
    backend = _ADBBackend()
    app = None
    engine = None
    try:
        for name in tuple(os.environ):
            if name.startswith("MAA_"):
                os.environ.pop(name, None)

        db_path = resource_root / "maa_api.db"
        db_session.DB_PATH = db_path
        db_session.ASYNC_URL = f"sqlite+aiosqlite:///{db_path}"
        db_session.SYNC_URL = f"sqlite:///{db_path}"
        engine = db_session.make_engine(db_session.ASYNC_URL, poolclass=NullPool)
        db_session.engine = engine
        db_session.session_factory = async_sessionmaker(
            engine, expire_on_commit=False, class_=AsyncSession
        )
        settings_module.DEFAULT_CONFIG_PATH = config_path
        settings_module.set_settings(settings_module.load_settings(config_path))

        app = main_module.create_app()
        app.state.core_supervisor_factory = _Supervisor
        app.state.core_client_factory = _CoreClient

        def device_factory(settings_provider, core_client, **kwargs):
            return DeviceManager(
                settings_provider,
                core_client,
                adb_backend=backend,
                startup_retry_attempts=2,
                startup_retry_interval=0.01,
                preflight_retry_attempts=1,
                preflight_retry_interval=0,
                unavailable_probe_interval=0,
                **kwargs,
            )

        app.state.device_manager_factory = device_factory

        with TestClient(app, raise_server_exceptions=False) as client:
            health = client.get("/api/system/health")
            assert health.status_code == 200, health.text
            settings = client.get("/api/settings")
            assert settings.status_code == 200, settings.text
            status = client.get("/api/device/status")
            assert status.status_code == 200, status.text
            manager = app.state.device_manager

            # Startup work runs in the background while HTTP remains usable.
            assert _wait_until(lambda: manager.state.value == "unavailable")
            assert client.get("/api/system/health").status_code == 200
            assert client.get("/api/device/status").status_code == 200

            readonly = client.put(
                "/api/settings", json={"items": {"app.maa_core_path": "/tmp/other-core"}}
            )
            assert readonly.status_code == 403, readonly.text
            changed = client.put(
                "/api/settings",
                json={"items": {"adb.address": "192.0.2.45:5555"}},
            )
            assert changed.status_code == 200, changed.text
            assert manager.address == "192.0.2.45:5555"
            assert "192.0.2.45:5555" in backend.connect_calls
            assert manager.state.value == "connected"

            archived = client.get("/api/device/screenshot?archive=true")
            assert archived.status_code == 200, archived.text
            payload = archived.json()
            assert payload["id"] and payload["sha256"]
            with sqlite3.connect(db_path) as connection:
                row = connection.execute(
                    "SELECT path FROM screenshot WHERE id = ?", (payload["id"],)
                ).fetchone()
            assert row is not None
            archived_file = resource_root / row[0]
            assert archived_file.is_file()
            assert archived_file.parent.is_relative_to(resource_root)

            print("[device_settings_smoke] offline startup, settings hot-apply and isolated screenshot archive passed")

        if db_session.DB_PATH != db_path:
            raise AssertionError("smoke changed the isolated DB path unexpectedly")
        print("M6 SMOKE OK")
        return 0
    finally:
        # TestClient lifespan has closed manager, runner, core facade and DB engine.
        for name in factory_names:
            if app is not None and hasattr(app.state, name):
                delattr(app.state, name)
        settings_module.set_settings(saved["settings_cache"])
        settings_module.DEFAULT_CONFIG_PATH = saved["DEFAULT_CONFIG_PATH"]
        db_session.DB_PATH = saved["DB_PATH"]
        db_session.ASYNC_URL = saved["ASYNC_URL"]
        db_session.SYNC_URL = saved["SYNC_URL"]
        db_session.engine = saved["engine"]
        db_session.session_factory = saved["session_factory"]
        for key in tuple(os.environ):
            if key.startswith("MAA_"):
                os.environ.pop(key, None)
        os.environ.update(saved_env)
        if engine is not None:
            engine.sync_engine.dispose()
        shutil.rmtree(temp_root, ignore_errors=True)


if __name__ == "__main__":
    raise SystemExit(main())
