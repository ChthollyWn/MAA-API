#!/usr/bin/env python3
"""M7 offline update API/service smoke using isolated storage and fake boundaries."""

from __future__ import annotations

import asyncio
import contextlib
import os
import shutil
import sys
import tempfile
import time
import traceback
import zipfile
from pathlib import Path
from typing import Any, Callable

REPO_ROOT = Path(__file__).resolve().parents[1]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))


class SmokeFailure(RuntimeError):
    pass


def require(condition: Any, message: str) -> None:
    if not condition:
        raise SmokeFailure(message)


class FakeHttpTransport:
    """A deny-by-default HTTP boundary: no request may leave this process."""

    def __init__(self) -> None:
        self.requests: list[str] = []

    async def get(self, url: str, *args: Any, **kwargs: Any) -> Any:
        del args, kwargs
        self.requests.append(url)
        raise SmokeFailure(f"unexpected HTTP request from offline smoke: {url}")

    async def request(self, method: str, url: str, *args: Any, **kwargs: Any) -> Any:
        del args, kwargs
        self.requests.append(f"{method} {url}")
        raise SmokeFailure(f"unexpected HTTP request from offline smoke: {method} {url}")


class FakeCore:
    def __init__(self, temp_root: Path, http: FakeHttpTransport) -> None:
        self.temp_root = temp_root
        self.http_client = http
        self.progress = None
        self.update_calls = 0
        self.check_calls = 0
        self.latest_version = "v2.0.0"
        self.failures_remaining = 0
        self.block_next = False
        self.entered: asyncio.Event | None = None
        self.release: asyncio.Event | None = None

    async def check(self) -> dict[str, Any]:
        self.check_calls += 1
        return {"current_version": "v1.0.0", "latest_version": self.latest_version, "available": True}

    async def update(self, *, channel: str, force: bool = False) -> str:
        require(channel == "stable", f"unexpected core channel: {channel}")
        del force
        self.update_calls += 1
        if self.progress is not None:
            await self.progress({"phase": "downloading", "downloaded": 10, "total": 100})
        if self.block_next:
            self.block_next = False
            self.entered = asyncio.Event()
            self.release = asyncio.Event()
            self.entered.set()
            await self.release.wait()
        if self.failures_remaining:
            self.failures_remaining -= 1
            raise RuntimeError("CORE_FAKE_FAILURE: simulated offline workflow failure")
        (self.temp_root / "core-result.txt").write_text(self.latest_version, encoding="utf-8")
        return self.latest_version


class FakeResource:
    def __init__(self, temp_root: Path, http: FakeHttpTransport) -> None:
        from maa_api.domain.enums import ResourceChannel

        self.temp_root = temp_root
        self.http_client = http
        self.progress = None
        self.install_calls = 0
        self.check_calls = 0
        self.failures_remaining = 0
        self.check_failures_remaining = 0
        self.version_suffix = "v2"
        self.channels = ResourceChannel

    async def check(self, channel: Any, **kwargs: Any) -> dict[Any, Any]:
        from maa_api.services.resource_update import ResourceCheck

        del kwargs
        self.check_calls += 1
        if self.check_failures_remaining:
            self.check_failures_remaining -= 1
            raise RuntimeError("RESOURCE_FAKE_FAILURE: simulated offline resource check failure")
        selected = self.channels(channel)
        result: dict[Any, Any] = {}
        for item in (self.channels.OTA, self.channels.REPO):
            if selected not in {self.channels.ALL, item}:
                continue
            result[item] = ResourceCheck(
                channel=item,
                changed=True,
                version=f"resource-{item.value}-{self.version_suffix}",
                body=b"fake metadata only",
                checksum=f"sha256-{item.value}-{self.version_suffix}",
                etag=f'"{item.value}-{self.version_suffix}"',
                source="offline-smoke",
            )
        return result

    async def stage(self, check: Any) -> Any:
        from maa_api.services.resource_update import StagedResource

        path = self.temp_root / f"staged-{check.channel.value}.bin"
        path.write_bytes(b"offline fake resource payload")
        return StagedResource(
            channel=check.channel,
            path=path,
            version=check.version,
            checksum=check.checksum,
        )

    async def install_all(self, staged: list[Any], *, reload_mode: str = "wait") -> None:
        self.install_calls += 1
        require(reload_mode in {"wait", "force", "defer"}, "unexpected resource reload mode")
        require(bool(staged), "resource install received no staged data")
        if self.progress is not None:
            await self.progress({"phase": "applying", "percent": 60})
        if self.failures_remaining:
            self.failures_remaining -= 1
            raise RuntimeError("RESOURCE_FAKE_FAILURE: simulated offline install failure")


class FakeAdb:
    """No adb executable/device is called; this fake records the safe install request."""

    def __init__(self) -> None:
        self.install_calls: list[Path] = []

    async def install_apk_safe(self, apk: Path) -> None:
        self.install_calls.append(apk)


class FakeGame:
    def __init__(self, temp_root: Path, http: FakeHttpTransport, adb: FakeAdb) -> None:
        self.temp_root = temp_root
        self.http_client = http
        self.adb = adb
        self.progress = None
        self.download_calls = 0
        self.install_calls = 0
        self.check_calls = 0
        self.failures_remaining = 0
        self.latest_version = "2.0.0"

    async def inspect(self, channel: str) -> Any:
        from maa_api.services.game_update import GameUpdateInfo, InstalledPackage

        self.check_calls += 1
        package = "com.example.maa.offline"
        return GameUpdateInfo(
            channel=channel,
            package_name=package,
            installed=InstalledPackage(package, True, "1.0.0", "2026-01-01T00:00:00Z"),
            latest_version=self.latest_version,
            latest_label=f"{self.latest_version} (offline fake)",
            can_compare_version=True,
            remote_size=None,
        )

    async def download_latest(
        self,
        channel: str,
        destination: Path,
        *,
        on_progress: Callable[[int, int], Any] | None = None,
    ) -> Any:
        from maa_api.services.game_update import ApkValidation

        del channel
        self.download_calls += 1
        destination.parent.mkdir(parents=True, exist_ok=True)
        # validate_apk checks ZIP integrity and the Android manifest entry. This is
        # a tiny synthetic archive, never an APK downloaded from a remote source.
        with zipfile.ZipFile(destination, "w", compression=zipfile.ZIP_STORED) as archive:
            archive.writestr("AndroidManifest.xml", b"offline-test-manifest")
        size = destination.stat().st_size
        if on_progress is not None:
            result = on_progress(size, size)
            if asyncio.iscoroutine(result):
                await result
        return ApkValidation(size)

    async def install(
        self,
        channel: str,
        apk_path: Path,
        *,
        expected_size: int | None = None,
        expected_md5: str | None = None,
        on_progress: Callable[[str], Any] | None = None,
    ) -> None:
        del channel, expected_size, expected_md5
        self.install_calls += 1
        if on_progress is not None:
            result = on_progress("fake safe install")
            if asyncio.iscoroutine(result):
                await result
        if self.failures_remaining:
            self.failures_remaining -= 1
            raise RuntimeError("GAME_FAKE_FAILURE: simulated ADB install failure")
        await self.adb.install_apk_safe(apk_path)


class Smoke:
    def __init__(self) -> None:
        self.temp_root = Path(tempfile.mkdtemp(prefix="maa-updates-smoke-")).resolve()
        self.failures: list[str] = []
        self.http = FakeHttpTransport()
        self.adb = FakeAdb()
        self.core: FakeCore | None = None
        self.resource: FakeResource | None = None
        self.game: FakeGame | None = None
        self.service: Any = None
        self.client: Any = None
        self.engine: Any = None
        self.saved_env: dict[str, str] = {}
        self.saved: dict[str, Any] = {}

    def ok(self, name: str, detail: str = "") -> None:
        print(f"[updates_smoke] [PASS] {name}{': ' + detail if detail else ''}", flush=True)

    def step(self, name: str, callback: Callable[[], Any]) -> None:
        try:
            detail = callback()
        except Exception as exc:  # report a useful failure while keeping cleanup reliable
            self.failures.append(f"{name}: {type(exc).__name__}: {exc}")
            print(f"[updates_smoke] [FAIL] {name}: {type(exc).__name__}: {exc}", flush=True)
            traceback.print_exc()
        else:
            self.ok(name, "" if detail is None else str(detail))

    def prepare(self) -> None:
        import maa_api.db.session as db_session
        import maa_api.settings as settings_module
        from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker
        from sqlalchemy.pool import NullPool
        from sqlmodel import SQLModel
        import maa_api.db.models  # register all application table models

        resource_root = self.temp_root / "resource"
        resource_root.mkdir(parents=True)
        db_path = resource_root / "maa_api-smoke.db"
        core_root = self.temp_root / "fake-core"
        core_root.mkdir()
        config_path = self.temp_root / "config.yaml"
        config_path.write_text(
            "app:\n"
            f"  maa_core_path: {core_root.as_posix()}\n"
            "adb:\n"
            "  path: /offline/fake/adb\n"
            "  address: 127.0.0.1:5555\n",
            encoding="utf-8",
        )

        self.saved_env = {key: value for key, value in os.environ.items() if key.startswith("MAA_")}
        for key in tuple(os.environ):
            if key.startswith("MAA_"):
                os.environ.pop(key, None)
        self.saved = {
            "db_path": db_session.DB_PATH,
            "async_url": db_session.ASYNC_URL,
            "sync_url": db_session.SYNC_URL,
            "engine": db_session.engine,
            "factory": db_session.session_factory,
            "config_path": settings_module.DEFAULT_CONFIG_PATH,
            "settings": settings_module.get_settings(),
        }
        db_session.DB_PATH = db_path
        db_session.ASYNC_URL = f"sqlite+aiosqlite:///{db_path}"
        db_session.SYNC_URL = f"sqlite:///{db_path}"
        self.engine = db_session.make_engine(db_session.ASYNC_URL, poolclass=NullPool)
        db_session.engine = self.engine
        db_session.session_factory = async_sessionmaker(
            self.engine, expire_on_commit=False, class_=AsyncSession
        )
        settings_module.DEFAULT_CONFIG_PATH = config_path
        settings_module.set_settings(settings_module.load_settings(config_path))

        async def create_schema() -> None:
            async with self.engine.begin() as connection:
                await connection.run_sync(SQLModel.metadata.create_all)

        asyncio.run(create_schema())

        from fastapi import FastAPI
        from fastapi.testclient import TestClient
        from maa_api.api.errors import register_exception_handlers
        from maa_api.api.routers import updates
        from maa_api.services.update_service import UpdateService

        self.core = FakeCore(self.temp_root, self.http)
        self.resource = FakeResource(self.temp_root, self.http)
        self.game = FakeGame(self.temp_root, self.http, self.adb)
        self.service = UpdateService(
            db_session.session_factory,
            core_workflow=self.core,
            resource_workflow=self.resource,
            game_workflow=self.game,
            broadcast=self._broadcast,
            temp_root=self.temp_root / "update-temp",
            cache_ttl=300,
        )
        app = FastAPI()
        register_exception_handlers(app)
        app.include_router(updates.router)

        @app.get("/api/system/health")
        async def health() -> dict[str, str]:
            return {"status": "ok"}

        app.state.update_service = self.service
        self.client = TestClient(app, raise_server_exceptions=False)
        self.client.__enter__()
        print(f"[updates_smoke] isolated root: {self.temp_root}", flush=True)

    def _broadcast(self, kind: str, data: dict[str, Any]) -> None:
        self.broadcasts.append((kind, data))

    @property
    def broadcasts(self) -> list[tuple[str, dict[str, Any]]]:
        if not hasattr(self, "_broadcasts"):
            self._broadcasts: list[tuple[str, dict[str, Any]]] = []
        return self._broadcasts

    def wait_terminal(self, update_id: str, timeout: float = 5.0) -> dict[str, Any]:
        deadline = time.monotonic() + timeout
        while time.monotonic() < deadline:
            response = self.client.get(f"/api/updates/{update_id}")
            require(response.status_code == 200, f"poll {update_id} returned {response.status_code}: {response.text}")
            record = response.json()
            if record.get("status") in {"success", "failed", "cancelled", "skipped"}:
                return record
            time.sleep(0.02)
        raise SmokeFailure(f"update {update_id} did not reach a terminal state")

    def post_update(self, path: str, payload: dict[str, Any] | None = None) -> dict[str, Any]:
        response = self.client.post(path, json=payload or {})
        require(response.status_code == 202, f"POST {path} expected 202, got {response.status_code}: {response.text}")
        record = response.json()
        require(bool(record.get("id")), f"POST {path} omitted update id")
        return record

    def status_cache(self) -> str:
        first = self.client.get("/api/updates/status")
        require(first.status_code == 200, f"initial status failed: {first.text}")
        require(first.json().get("cached") is False, "first status should be freshly inspected")
        counts = (self.core_checks, self.resource_checks, self.game_checks)
        cached = self.client.get("/api/updates/status")
        require(cached.status_code == 200 and cached.json().get("cached") is True, "second status did not use cache")
        require(counts == (self.core_checks, self.resource_checks, self.game_checks), "cached status re-inspected workflows")
        refreshed = self.client.get("/api/updates/status?refresh=true")
        require(refreshed.status_code == 200 and refreshed.json().get("cached") is False, "refresh did not force a fresh check")
        require(self.core_checks > counts[0] and self.resource_checks > counts[1] and self.game_checks > counts[2], "refresh did not inspect each update target")
        return "fresh, cache hit, and forced refresh inspected expected sources"

    @property
    def core_checks(self) -> int:
        return self.core.check_calls

    @property
    def resource_checks(self) -> int:
        return self.resource.check_calls

    @property
    def game_checks(self) -> int:
        return self.game.check_calls

    def core_mutex_cancel(self) -> str:
        self.core.block_next = True
        accepted = self.post_update("/api/updates/core", {"channel": "stable", "force": True})
        update_id = accepted["id"]
        async def wait_until_entered() -> None:
            deadline = asyncio.get_running_loop().time() + 3.0
            while self.core.entered is None and asyncio.get_running_loop().time() < deadline:
                await asyncio.sleep(0.01)
            require(self.core.entered is not None, "core fake did not start its blocked workflow")
            await asyncio.wait_for(self.core.entered.wait(), 3.0)

        self.client.portal.call(wait_until_entered)
        try:
            running = self.client.get(f"/api/updates/{update_id}")
            require(running.status_code == 200 and running.json()["status"] == "running", "progress endpoint did not expose running record")
            busy = self.client.post("/api/updates/resource", json={"channel": "ota"})
            require(busy.status_code == 409, f"concurrent update should be rejected with 409, got {busy.status_code}: {busy.text}")
        finally:
            cancelled = self.client.delete(f"/api/updates/{update_id}")
            require(cancelled.status_code == 202, f"cancel failed: {cancelled.status_code}: {cancelled.text}")
        terminal = self.wait_terminal(update_id)
        require(terminal["status"] == "cancelled", f"cancel terminal state was {terminal['status']}")
        return "running progress, global mutex, and cancellation terminal state passed"

    def core_failure_retry(self) -> str:
        self.core.failures_remaining = 1
        failed_id = self.post_update("/api/updates/core", {"channel": "stable", "force": True})["id"]
        failed = self.wait_terminal(failed_id)
        require(failed["status"] == "failed" and failed.get("error_code") == "CORE_FAKE_FAILURE", f"core failure was not persisted: {failed}")
        retry = self.client.post(f"/api/updates/{failed_id}/retry")
        require(retry.status_code == 202, f"core retry failed: {retry.status_code}: {retry.text}")
        retried = self.wait_terminal(retry.json()["id"])
        require(retried["status"] == "success", f"core retry did not complete: {retried}")
        require(retried["id"] != failed_id, "retry reused the failed history id")
        return "core failure recorded; retry created a new successful record"

    def resource_failure_retry(self) -> str:
        self.resource.check_failures_remaining = 1
        failed_id = self.post_update("/api/updates/resource", {"channel": "ota"})["id"]
        failed = self.wait_terminal(failed_id)
        require(failed["status"] == "failed" and failed.get("error_code") == "RESOURCE_FAKE_FAILURE", f"resource failure was not persisted: {failed}")
        retry = self.client.post(f"/api/updates/{failed_id}/retry")
        require(retry.status_code == 202, f"resource retry failed: {retry.status_code}: {retry.text}")
        retried = self.wait_terminal(retry.json()["id"])
        require(retried["status"] == "success", f"resource retry did not complete: {retried}")
        require(self.resource.install_calls >= 1, "resource fake install branch was not exercised")
        return "resource check failure, retry, staging and fake install completion passed"

    def game_failure_retry(self) -> str:
        self.game.failures_remaining = 1
        failed_id = self.post_update("/api/updates/game", {"channel": "Bilibili"})["id"]
        failed = self.wait_terminal(failed_id)
        require(failed["status"] == "failed" and failed.get("error_code") == "GAME_FAKE_FAILURE", f"game failure was not persisted: {failed}")
        retry = self.client.post(f"/api/updates/{failed_id}/retry")
        require(retry.status_code == 202, f"game retry failed: {retry.status_code}: {retry.text}")
        retried = self.wait_terminal(retry.json()["id"])
        require(retried["status"] == "success", f"game retry did not complete: {retried}")
        require(self.game.download_calls == 1, "game retry did not reuse its verified synthetic APK cache")
        require(len(self.adb.install_calls) == 1, "fake safe ADB installer was not called exactly once")
        return "game API, synthetic APK validation, failed install retry and fake safe install passed"

    def daily_dedup(self) -> str:
        self.core.latest_version = "v3.0.0"
        self.resource.version_suffix = "v3"
        self.game.latest_version = "3.0.0"
        before = len(self.broadcasts)
        first = self.client.portal.call(self.service.daily_check)
        after_first = len(self.broadcasts)
        second = self.client.portal.call(self.service.daily_check)
        after_second = len(self.broadcasts)
        require(first["checked_at"] and second["checked_at"], "daily check omitted check timestamps")
        require(after_first > before, "first daily check did not announce available updates")
        require(after_second == after_first, "same versions were announced more than once")
        require(first["notified"] and second["notified"] == [], "daily notification summary was not deduplicated")
        return "daily check aggregates availability and suppresses duplicate same-version notices"

    def service_survives(self) -> str:
        health = self.client.get("/api/system/health")
        require(health.status_code == 200 and health.json().get("status") == "ok", "health endpoint failed after update failures")
        require(not self.http.requests, f"smoke attempted HTTP traffic: {self.http.requests}")
        require(
            self.adb.install_calls
            and all(path.resolve().is_relative_to(self.temp_root.resolve()) for path in self.adb.install_calls),
            f"fake APK path escaped isolated temp root: {self.adb.install_calls!r}",
        )
        return "HTTP service remained usable; no external HTTP or real ADB operation occurred"

    def cleanup(self) -> None:
        import maa_api.db.session as db_session
        import maa_api.settings as settings_module

        if self.client is not None:
            with contextlib.suppress(Exception):
                self.client.__exit__(None, None, None)
            self.client = None
        if self.engine is not None:
            with contextlib.suppress(Exception):
                self.engine.sync_engine.dispose()
        if self.saved:
            settings_module.set_settings(self.saved["settings"])
            settings_module.DEFAULT_CONFIG_PATH = self.saved["config_path"]
            db_session.DB_PATH = self.saved["db_path"]
            db_session.ASYNC_URL = self.saved["async_url"]
            db_session.SYNC_URL = self.saved["sync_url"]
            db_session.engine = self.saved["engine"]
            db_session.session_factory = self.saved["factory"]
        for key in tuple(os.environ):
            if key.startswith("MAA_"):
                os.environ.pop(key, None)
        os.environ.update(self.saved_env)
        shutil.rmtree(self.temp_root, ignore_errors=True)

    def run(self) -> int:
        try:
            self.prepare()
            for name, callback in (
                ("status refresh/cache", self.status_cache),
                ("update progress/mutex/cancel", self.core_mutex_cancel),
                ("core failure/retry/completion", self.core_failure_retry),
                ("resource failure/retry/completion", self.resource_failure_retry),
                ("game failure/retry/completion", self.game_failure_retry),
                ("daily check summary/dedup", self.daily_dedup),
                ("offline boundaries/service availability", self.service_survives),
            ):
                self.step(name, callback)
        except Exception as exc:
            self.failures.append(f"setup: {type(exc).__name__}: {exc}")
            print(f"[updates_smoke] [FAIL] setup: {type(exc).__name__}: {exc}", flush=True)
            traceback.print_exc()
        finally:
            self.cleanup()
        if self.failures:
            print("[updates_smoke] failures:", flush=True)
            for failure in self.failures:
                print(f"  - {failure}", flush=True)
            return 1
        print("SMOKE OK", flush=True)
        return 0


def main() -> int:
    return Smoke().run()


if __name__ == "__main__":
    raise SystemExit(main())
