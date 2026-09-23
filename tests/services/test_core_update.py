from __future__ import annotations

import asyncio
import io
import json
import os
import zipfile
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import httpx
import pytest

from maa_api.domain.errors import AppError, ErrorCode
from maa_api.services import core_update as core_update_module
from maa_api.services.core_update import (
    CORE_ARCHIVE_CACHE_TTL_SECONDS,
    CoreUpdateError,
    CoreUpdateWorkflow,
    apply_core_update,
    check_disk_space,
    core_platform_key,
    extract_core_archive,
    match_core_asset,
    normalize_core_package,
    rollback_core_update,
    validate_core_directory,
)


def _valid_archive(*, version: str = "v7.0.0", wrapped: bool = True) -> bytes:
    output = io.BytesIO()
    prefix = "MAA-v7.0.0-macos-runtime-universal/" if wrapped else ""
    with zipfile.ZipFile(output, "w") as bundle:
        bundle.writestr(prefix + "libMaaCore.dylib", b"fake native library")
        bundle.writestr(prefix + "resource/tasks/.keep", b"")
        bundle.writestr(prefix + "resource/template/.keep", b"")
        bundle.writestr(prefix + "resource/config.json", "{}")
        bundle.writestr(
            prefix + "resource/version.json",
            json.dumps({"version": version}),
        )
    return output.getvalue()


def _client_for_release(
    archive_bytes: bytes,
    *,
    size: int | None = None,
    mirrors: tuple[str, ...] = (),
) -> httpx.AsyncClient:
    asset_size = len(archive_bytes) if size is None else size

    def respond(request: httpx.Request) -> httpx.Response:
        if request.url.path.endswith("summary.json"):
            return httpx.Response(
                200,
                json={
                    "alpha": {"version": "v99", "detail": "https://example.test/alpha"},
                    "stable": {
                        "version": "v7.0.0",
                        "detail": "https://example.test/stable.json",
                    },
                },
            )
        return httpx.Response(
            200,
            json={
                "version": "v7.0.0",
                "details": {
                    "assets": [
                        {
                            "name": "MAA-v7.0.0-macos-runtime-universal.zip",
                            "size": asset_size,
                            "browser_download_url": "https://example.test/core.zip",
                            "mirrors": list(mirrors),
                        },
                        {
                            "name": "MAA-v7.0.0-macos-universal.dmg",
                            "size": asset_size,
                            "browser_download_url": "https://example.test/core.dmg",
                        },
                    ]
                },
            },
        )

    return httpx.AsyncClient(transport=httpx.MockTransport(respond))


class FakeSupervisor:
    def __init__(self, *, fail_first_start: bool = False) -> None:
        self.stops = 0
        self.starts = 0
        self.fail_first_start = fail_first_start
        self.maintenance_entries = 0

    class _Maintenance:
        def __init__(self, owner: "FakeSupervisor") -> None:
            self.owner = owner

        async def __aenter__(self) -> "FakeSupervisor":
            self.owner.maintenance_entries += 1
            return self.owner

        async def __aexit__(self, exc_type: Any, exc: Any, tb: Any) -> None:
            return None

    def acquire_maintenance(self) -> "FakeSupervisor._Maintenance":
        return self._Maintenance(self)

    async def stop(self) -> None:
        self.stops += 1

    async def start(self) -> None:
        self.starts += 1
        if self.fail_first_start and self.starts == 1:
            raise RuntimeError("injected new core start failure")


def _write_old_target(target: Path) -> None:
    target.mkdir(parents=True)
    (target / "old-core.marker").write_text("old", encoding="utf-8")


def _write_valid_core_directory(target: Path, version: str) -> None:
    resource = target / "resource"
    (resource / "tasks").mkdir(parents=True)
    (resource / "template").mkdir(parents=True)
    (target / "libMaaCore.dylib").write_bytes(b"fake core")
    (resource / "config.json").write_text("{}", encoding="utf-8")
    (resource / "version.json").write_text(
        json.dumps({"version": version}), encoding="utf-8"
    )
    (target / "restored-core.marker").write_text(version, encoding="utf-8")


def _workflow(
    tmp_path: Path,
    archive_bytes: bytes,
    *,
    supervisor: FakeSupervisor | None = None,
    download: Any = None,
    ready_version: Any = None,
    current_version: Any = None,
    before_start: Any = None,
    progress: Any = None,
    download_prefix: str | None = None,
    mirrors: tuple[str, ...] = (),
    disk_usage: Any = None,
) -> tuple[CoreUpdateWorkflow, Path, Path, dict[str, int]]:
    target = tmp_path / "lib" / "maa" / "Darwin"
    target.parent.mkdir(parents=True)
    _write_old_target(target)
    temp_root = tmp_path / "resource" / "temp" / "core-update"
    http_client = _client_for_release(archive_bytes, mirrors=mirrors)
    current_supervisor = supervisor or FakeSupervisor()
    download_count = {"count": 0}

    async def fake_download(url: str, dest: str | Path, **kwargs: Any) -> Path:
        download_count["count"] += 1
        path = Path(dest)
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(archive_bytes)
        return path

    async def tracked_download(url: str, dest: str | Path, **kwargs: Any) -> Path:
        download_count["count"] += 1
        assert download is not None
        return await download(url, dest, **kwargs)

    workflow = CoreUpdateWorkflow(
        http_client=http_client,
        target_path=target,
        temp_root=temp_root,
        supervisor=current_supervisor,
        core_client=object(),
        reconnect=lambda client: None,
        ready_version=ready_version or (lambda client: "v7.0.0"),
        current_version=current_version,
        before_start=before_start,
        progress=progress,
        download_prefix=download_prefix,
        downloader=tracked_download if download is not None else fake_download,
        disk_usage=disk_usage or (lambda path: SimpleNamespace(free=10**12)),
        system="Darwin",
        machine="arm64",
    )
    return workflow, target, target.with_name("Darwin.backup"), download_count


def test_platform_mapping_and_supported_asset_whitelist() -> None:
    assert core_platform_key("Darwin", "arm64") == "macos-runtime-universal"
    assert core_platform_key("Linux", "aarch64") == "linux-aarch64"
    assert core_platform_key("Linux", "arm64") == "linux-x86_64"
    assert core_platform_key("Windows", "AMD64") == "win-x64"
    assert core_platform_key("Windows", "ARM64") == "win-arm64"
    assets = [
        {
            "name": "MAA-v1-linux-x86_64.AppImage",
            "size": 1,
            "browser_download_url": "https://example.test/appimage",
        },
        {
            "name": "MAA-v1-linux-x86_64.tar.gz",
            "size": 2,
            "browser_download_url": "https://example.test/core.tar.gz",
        },
    ]
    assert match_core_asset(assets, system="Linux", machine="x86_64")["size"] == 2
    with pytest.raises(CoreUpdateError, match="no supported"):
        match_core_asset(assets[:1], system="Linux", machine="x86_64")


def test_duplicate_asset_size_conflict_warns(caplog: pytest.LogCaptureFixture) -> None:
    items = [
        {
            "name": "MAA-v1-macos-runtime-universal.zip",
            "size": size,
            "browser_download_url": f"https://example.test/{size}",
        }
        for size in (10, 11)
    ]
    match_core_asset(items, system="Darwin", machine="arm64")
    assert "conflicting sizes" in caplog.text


def test_extract_normalize_and_validate_wrapped_package(tmp_path: Path) -> None:
    archive = tmp_path / "release.zip"
    archive.write_bytes(_valid_archive())
    extracted = extract_core_archive(archive, tmp_path / "extracted")
    package = normalize_core_package(extracted)
    assert package.name == "MAA-v7.0.0-macos-runtime-universal"
    assert validate_core_directory(package, system="Darwin", machine="arm64") == {
        "version": "v7.0.0"
    }


def test_extract_rejects_path_traversal(tmp_path: Path) -> None:
    archive = tmp_path / "malicious.zip"
    with zipfile.ZipFile(archive, "w") as bundle:
        bundle.writestr("../outside.txt", "bad")
    destination = tmp_path / "extract"
    with pytest.raises(CoreUpdateError, match="unsafe path"):
        extract_core_archive(archive, destination)
    assert not (tmp_path / "outside.txt").exists()


def test_corrupt_or_incomplete_package_is_rejected_before_touching_target(
    tmp_path: Path,
) -> None:
    async def run() -> None:
        corrupt = b"x" * len(_valid_archive())
        workflow, target, backup, downloads = _workflow(tmp_path, corrupt)
        with pytest.raises(CoreUpdateError):
            await workflow.update()
        assert (target / "old-core.marker").read_text(encoding="utf-8") == "old"
        assert not backup.exists()
        assert downloads["count"] == 1
        await workflow.http_client.aclose()

    asyncio.run(run())


def test_disk_preflight_prevents_download(tmp_path: Path) -> None:
    async def run() -> None:
        workflow, target, backup, downloads = _workflow(
            tmp_path,
            _valid_archive(),
            disk_usage=lambda path: SimpleNamespace(free=0),
        )
        with pytest.raises(CoreUpdateError, match="insufficient disk space"):
            await workflow.update()
        assert downloads["count"] == 0
        assert (target / "old-core.marker").is_file()
        assert not backup.exists()
        await workflow.http_client.aclose()

    asyncio.run(run())


def test_non_stable_channel_is_rejected_before_network_or_disk_work(tmp_path: Path) -> None:
    async def run() -> None:
        workflow, target, backup, downloads = _workflow(tmp_path, _valid_archive())
        with pytest.raises(CoreUpdateError, match="only the stable"):
            await workflow.update(channel="beta")
        assert downloads["count"] == 0
        assert (target / "old-core.marker").is_file()
        assert not backup.exists()
        await workflow.http_client.aclose()

    asyncio.run(run())


def test_apply_failure_restores_original_target(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    target = tmp_path / "maa" / "Darwin"
    target.parent.mkdir()
    _write_old_target(target)
    backup = target.with_name("Darwin.backup")
    staged = tmp_path / "staged"
    staged.mkdir()
    (staged / "new-core.marker").write_text("new", encoding="utf-8")
    original_replace = os.replace
    failed = {"value": False}

    def replace(source: Any, destination: Any) -> None:
        if Path(source) == staged and not failed["value"]:
            failed["value"] = True
            raise OSError("injected staged rename failure")
        original_replace(source, destination)

    monkeypatch.setattr(core_update_module.os, "replace", replace)
    with pytest.raises(OSError, match="injected"):
        apply_core_update(staged, target, backup)
    assert (target / "old-core.marker").read_text(encoding="utf-8") == "old"
    assert not backup.exists()
    assert (staged / "new-core.marker").is_file()


def test_apply_and_rollback_only_use_configured_target_and_unique_backup(
    tmp_path: Path,
) -> None:
    target = tmp_path / "maa" / "Darwin"
    target.parent.mkdir()
    _write_old_target(target)
    staged = tmp_path / "maa" / "staged"
    staged.mkdir()
    (staged / "new-core.marker").write_text("new", encoding="utf-8")
    backup = target.with_name("Darwin.backup")
    apply_core_update(staged, target, backup)
    assert (target / "new-core.marker").is_file()
    assert (backup / "old-core.marker").is_file()
    rollback_core_update(target, backup)
    assert (target / "old-core.marker").read_text(encoding="utf-8") == "old"
    assert not backup.exists()
    with pytest.raises(CoreUpdateError, match="unique"):
        rollback_core_update(target, target.with_name("some-other-backup"))


def test_unknown_running_version_does_not_prevent_stable_update(tmp_path: Path) -> None:
    async def run() -> None:
        archive = _valid_archive()
        supervisor = FakeSupervisor()

        def unknown_version(client: Any) -> None:
            raise RuntimeError("core is not ready")

        workflow, target, backup, _ = _workflow(
            tmp_path,
            archive,
            supervisor=supervisor,
            current_version=unknown_version,
            ready_version=lambda client: "v7.0.0",
        )
        release = await workflow.update()
        assert release.channel == "stable"
        assert (target / "libMaaCore.dylib").is_file()
        assert (backup / "old-core.marker").is_file()  # one previous version is retained
        assert supervisor.stops == 1
        assert supervisor.starts == 1
        await workflow.http_client.aclose()

    asyncio.run(run())


def test_matching_current_version_skips_without_downloading(tmp_path: Path) -> None:
    async def run() -> None:
        workflow, target, backup, downloads = _workflow(
            tmp_path,
            _valid_archive(),
            current_version=lambda client: "v7.0.0",
        )
        result = await workflow.update()
        assert result is None
        assert downloads["count"] == 0
        assert (target / "old-core.marker").is_file()
        assert not backup.exists()
        await workflow.http_client.aclose()

    asyncio.run(run())


def test_before_start_overlay_is_applied_after_switch(tmp_path: Path) -> None:
    async def run() -> None:
        archive = _valid_archive()
        events: list[str] = []
        supervisor = FakeSupervisor()

        async def before_start(target: Path) -> None:
            events.append("overlay")
            (target / "resource" / "overlay.marker").write_text("overlay", encoding="utf-8")

        async def ready_version(client: Any) -> str:
            events.append("ready-version")
            return "v7.0.0"

        workflow, target, backup, _ = _workflow(
            tmp_path,
            archive,
            supervisor=supervisor,
            before_start=before_start,
            ready_version=ready_version,
        )
        # Replace the fake supervisor's start method with one that records ordering.
        original_start = supervisor.start

        async def start() -> None:
            events.append("start")
            await original_start()

        supervisor.start = start  # type: ignore[method-assign]
        await workflow.update()
        assert events == ["overlay", "start", "ready-version"]
        assert (target / "resource" / "overlay.marker").read_text(encoding="utf-8") == "overlay"
        assert (backup / "old-core.marker").is_file()
        await workflow.http_client.aclose()

    asyncio.run(run())


def test_download_fallback_uses_prefix_mirrors_then_github_and_deduplicates(
    tmp_path: Path,
) -> None:
    async def run() -> None:
        archive = _valid_archive()
        calls: list[str] = []
        release_url = "https://example.test/core.zip"
        mirror = "https://mirror.test/core.zip"

        async def download(url: str, dest: str | Path, **kwargs: Any) -> Path:
            calls.append(url)
            if url != mirror:
                raise OSError(f"offline at {url}")
            path = Path(dest)
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_bytes(archive)
            return path

        workflow, target, backup, _ = _workflow(
            tmp_path,
            archive,
            download_prefix="https://proxy.test/",
            mirrors=(mirror, release_url),
            download=download,
        )

        await workflow.update()
        assert calls == [
            f"https://proxy.test/{release_url}",
            mirror,
        ]
        assert (target / "libMaaCore.dylib").is_file()
        assert (backup / "old-core.marker").is_file()
        await workflow.http_client.aclose()

    asyncio.run(run())


def test_valid_archive_is_cached_for_retry_after_install_failure(tmp_path: Path) -> None:
    async def run() -> None:
        archive = _valid_archive()
        supervisor = FakeSupervisor(fail_first_start=True)
        workflow, target, backup, downloads = _workflow(
            tmp_path, archive, supervisor=supervisor
        )
        cached = workflow.temp_root / "v7.0.0" / "MAA-v7.0.0-macos-runtime-universal.zip"
        with pytest.raises(RuntimeError, match="injected new core start failure"):
            await workflow.update()
        assert cached.read_bytes() == archive
        assert (target / "old-core.marker").is_file()
        assert not backup.exists()
        assert CORE_ARCHIVE_CACHE_TTL_SECONDS == 7 * 24 * 60 * 60

        result = await workflow.update()
        assert result is not None
        assert downloads["count"] == 1
        assert not cached.exists()
        assert (target / "libMaaCore.dylib").is_file()
        await workflow.http_client.aclose()

    asyncio.run(run())


def test_invalid_cached_archive_is_removed_and_redownloaded(tmp_path: Path) -> None:
    async def run() -> None:
        archive = _valid_archive()
        workflow, target, backup, downloads = _workflow(tmp_path, archive)
        cached = workflow.temp_root / "v7.0.0" / "MAA-v7.0.0-macos-runtime-universal.zip"
        cached.parent.mkdir(parents=True)
        cached.write_bytes(b"x" * len(archive))
        await workflow.update()
        assert downloads["count"] == 1
        assert not cached.exists()
        assert (target / "libMaaCore.dylib").is_file()
        assert (backup / "old-core.marker").is_file()
        await workflow.http_client.aclose()

    asyncio.run(run())


def test_all_download_failures_report_final_source_and_remove_partial_file(
    tmp_path: Path,
) -> None:
    async def run() -> None:
        archive = _valid_archive()
        release_url = "https://example.test/core.zip"
        mirror = "https://mirror.test/core.zip"
        calls: list[str] = []

        async def fail_download(url: str, dest: str | Path, **kwargs: Any) -> Path:
            calls.append(url)
            path = Path(dest)
            path.parent.mkdir(parents=True, exist_ok=True)
            path.with_suffix(path.suffix + ".part").write_bytes(b"partial")
            raise OSError(f"offline at {url}")

        workflow, target, backup, _ = _workflow(
            tmp_path,
            archive,
            download_prefix="https://proxy.test/",
            mirrors=(mirror,),
            download=fail_download,
        )
        with pytest.raises(CoreUpdateError, match="final source https://example.test/core.zip"):
            await workflow.update()
        assert calls == [
            f"https://proxy.test/{release_url}",
            mirror,
            release_url,
        ]
        archive_path = workflow.temp_root / "v7.0.0" / "MAA-v7.0.0-macos-runtime-universal.zip"
        assert not archive_path.exists()
        assert not archive_path.with_suffix(".zip.part").exists()
        assert (target / "old-core.marker").is_file()
        assert not backup.exists()
        await workflow.http_client.aclose()

    asyncio.run(run())


@pytest.mark.parametrize("failure", ["start", "version", "overlay"])
def test_restart_ready_or_overlay_failure_restores_original_target(
    tmp_path: Path, failure: str
) -> None:
    async def run() -> None:
        archive = _valid_archive()
        supervisor = FakeSupervisor(fail_first_start=failure == "start")
        version_calls = {"count": 0}

        def ready_version(client: Any) -> str:
            version_calls["count"] += 1
            return "unexpected-version" if failure == "version" else "v7.0.0"

        def before_start(path: Path) -> None:
            if failure == "overlay":
                raise RuntimeError("injected resource overlay failure")

        workflow, target, backup, _ = _workflow(
            tmp_path,
            archive,
            supervisor=supervisor,
            ready_version=ready_version,
            before_start=before_start if failure == "overlay" else None,
        )
        with pytest.raises((RuntimeError, CoreUpdateError)):
            await workflow.update()
        assert (target / "old-core.marker").read_text(encoding="utf-8") == "old"
        assert not backup.exists()
        assert supervisor.starts == (1 if failure == "overlay" else 2)
        assert supervisor.stops == (1 if failure == "overlay" or failure == "start" else 2)
        assert supervisor.maintenance_entries == 2
        await workflow.http_client.aclose()

    asyncio.run(run())


def test_check_disk_space_uses_three_times_archive_size(tmp_path: Path) -> None:
    usage = lambda path: SimpleNamespace(free=299)
    with pytest.raises(CoreUpdateError, match="need 300"):
        check_disk_space(tmp_path / "temp", 100, disk_usage=usage)
    check_disk_space(tmp_path / "temp", 100, disk_usage=lambda path: SimpleNamespace(free=300))


def test_validate_core_directory_requires_full_resource_layout(tmp_path: Path) -> None:
    root = tmp_path / "package"
    root.mkdir()
    (root / "libMaaCore.dylib").write_bytes(b"library")
    resource = root / "resource"
    resource.mkdir()
    (resource / "config.json").write_text("{}", encoding="utf-8")
    with pytest.raises(CoreUpdateError, match="resource/tasks"):
        validate_core_directory(root, system="Darwin", machine="arm64")


def test_workflow_rollback_swaps_backup_runs_overlay_and_reports_progress(
    tmp_path: Path,
) -> None:
    async def run() -> None:
        phases: list[str] = []
        overlay_paths: list[Path] = []
        workflow, target, backup, _ = _workflow(
            tmp_path,
            _valid_archive(version="v6.9.0"),
            ready_version=lambda client: "v6.9.0",
            before_start=lambda path: overlay_paths.append(path),
            progress=lambda event: phases.append(event["phase"]),
        )
        _write_valid_core_directory(backup, "v6.9.0")

        version = await workflow.rollback()

        assert version == "v6.9.0"
        assert overlay_paths == [target]
        assert (target / "restored-core.marker").read_text(encoding="utf-8") == "v6.9.0"
        assert (backup / "old-core.marker").read_text(encoding="utf-8") == "old"
        assert phases == ["restarting", "done"]
        assert workflow.supervisor.stops == 1
        assert workflow.supervisor.starts == 1
        await workflow.http_client.aclose()

    asyncio.run(run())


@pytest.mark.parametrize("failure", ["overlay", "start", "ready"])
def test_workflow_rollback_failure_restores_original_target_and_reports_error(
    tmp_path: Path, failure: str
) -> None:
    async def run() -> None:
        supervisor = FakeSupervisor(fail_first_start=failure == "start")

        def before_start(path: Path) -> None:
            if failure == "overlay":
                raise RuntimeError("injected rollback overlay failure")

        workflow, target, backup, _ = _workflow(
            tmp_path,
            _valid_archive(version="v6.9.0"),
            supervisor=supervisor,
            ready_version=lambda client: "" if failure == "ready" else "v6.9.0",
            before_start=before_start,
        )
        _write_valid_core_directory(backup, "v6.9.0")

        with pytest.raises(AppError) as captured:
            await workflow.rollback()

        assert captured.value.code is ErrorCode.UPDATE_ROLLBACK_FAILED
        assert (target / "old-core.marker").read_text(encoding="utf-8") == "old"
        assert (backup / "restored-core.marker").read_text(encoding="utf-8") == "v6.9.0"
        assert supervisor.maintenance_entries == 2
        expected_starts = 1 if failure == "overlay" else 2
        assert supervisor.starts == expected_starts
        expected_stops = 1 if failure == "overlay" else 2
        assert supervisor.stops == expected_stops
        await workflow.http_client.aclose()

    asyncio.run(run())


def test_workflow_rollback_requires_unique_valid_backup(tmp_path: Path) -> None:
    async def run() -> None:
        workflow, target, backup, _ = _workflow(tmp_path, _valid_archive())
        with pytest.raises(AppError) as captured:
            await workflow.rollback()
        assert captured.value.code is ErrorCode.UPDATE_ROLLBACK_FAILED
        assert (target / "old-core.marker").is_file()
        assert not backup.exists()
        assert workflow.supervisor.stops == 0
        await workflow.http_client.aclose()

    asyncio.run(run())
