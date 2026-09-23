"""Stable MaaCore release discovery and transactional installation.

The module deliberately does not import or load MaaCore. Network access,
filesystem locations, downloading, process lifecycle, client reconnection and
progress reporting are all supplied by the caller so the update path can be
exercised without a running native core.
"""

from __future__ import annotations

import inspect
import json
import logging
import os
import platform as host_platform
import re
import shutil
import tarfile
import uuid
import zipfile
from dataclasses import dataclass
from pathlib import Path, PurePosixPath, PureWindowsPath
from typing import Any, Callable

import httpx

from maa_api.domain.errors import AppError, ErrorCode
from maa_api.util.downloader import download as default_downloader

__all__ = [
    "CORE_ARCHIVE_CACHE_TTL_SECONDS",
    "CoreRelease",
    "CoreUpdateError",
    "CoreUpdateWorkflow",
    "apply_core_update",
    "check_disk_space",
    "core_platform_key",
    "extract_core_archive",
    "match_core_asset",
    "normalize_core_package",
    "rollback_core_update",
    "validate_core_directory",
]

logger = logging.getLogger(__name__)

SUMMARY_URL = (
    "https://api.maa.plus/MaaAssistantArknights/api/version/summary.json"
)
CORE_ARCHIVE_CACHE_TTL_SECONDS = 7 * 24 * 60 * 60
_ASSET_PREFIX = "^MAA-.*-"
_VERSION_RE = re.compile(r"[A-Za-z0-9][A-Za-z0-9._+-]{0,127}\Z")


class CoreUpdateError(RuntimeError):
    """An invalid release, archive, or core update operation."""


@dataclass(frozen=True, slots=True)
class CoreRelease:
    """The selected stable release asset and its upstream metadata."""

    version: str
    channel: str
    detail_url: str
    asset_name: str
    asset_url: str
    asset_size: int
    mirrors: tuple[str, ...] = ()


def core_platform_key(system: str | None = None, machine: str | None = None) -> str:
    """Return the release platform identifier defined by docs/07 §2.2."""
    system = system if system is not None else host_platform.system()
    machine = machine if machine is not None else host_platform.machine()
    if system == "Darwin":
        return "macos-runtime-universal"
    if system == "Linux":
        return "linux-aarch64" if machine.lower() == "aarch64" else "linux-x86_64"
    if system == "Windows":
        return "win-x64" if machine.lower() in {"amd64", "x86_64"} else "win-arm64"
    raise CoreUpdateError(f"unsupported MaaCore platform: {system}/{machine}")


def match_core_asset(
    assets: list[dict[str, Any]] | tuple[dict[str, Any], ...],
    *,
    system: str | None = None,
    machine: str | None = None,
) -> dict[str, Any]:
    """Select the first supported full-runtime asset for the current platform.

    The extension whitelist intentionally excludes AppImage, dmg and delta
    packages. Repeated entries with the same name are accepted in their
    upstream order; size disagreement is surfaced as a warning.
    """
    key = core_platform_key(system, machine)
    pattern = re.compile(_ASSET_PREFIX + re.escape(key) + r"\.(?:zip|tar\.gz)\Z")
    matched: list[dict[str, Any]] = []
    for item in assets:
        if not isinstance(item, dict):
            continue
        name = item.get("name")
        if isinstance(name, str) and pattern.fullmatch(name):
            matched.append(item)
    if not matched:
        raise CoreUpdateError(f"no supported MaaCore asset for {key}")

    by_name: dict[str, list[dict[str, Any]]] = {}
    for item in matched:
        by_name.setdefault(item["name"], []).append(item)
    for name, duplicates in by_name.items():
        sizes = {item.get("size") for item in duplicates}
        if len(sizes) > 1:
            logger.warning(
                "MaaCore release contains duplicate asset %s with conflicting sizes: %s",
                name,
                sorted(map(str, sizes)),
            )

    selected = matched[0]
    name = selected["name"]
    if "\\" in name or PurePosixPath(name).name != name:
        raise CoreUpdateError(f"unsafe MaaCore asset filename: {name!r}")
    size = selected.get("size")
    url = selected.get("browser_download_url")
    if isinstance(size, bool) or not isinstance(size, int) or size < 0:
        raise CoreUpdateError(f"invalid size for MaaCore asset {selected['name']!r}")
    if not isinstance(url, str) or not url:
        raise CoreUpdateError(f"missing download URL for MaaCore asset {selected['name']!r}")
    return dict(selected)


async def resolve_core_release(
    client: httpx.AsyncClient,
    *,
    system: str | None = None,
    machine: str | None = None,
) -> CoreRelease:
    """Resolve only the stable channel from the upstream summary and detail."""
    summary_response = await client.get(SUMMARY_URL)
    summary_response.raise_for_status()
    summary = summary_response.json()
    if not isinstance(summary, dict):
        raise CoreUpdateError("MaaCore version summary must be a JSON object")
    stable = summary.get("stable")
    if not isinstance(stable, dict):
        raise CoreUpdateError("MaaCore version summary has no stable release")
    version = stable.get("version")
    detail_url = stable.get("detail")
    if (
        not isinstance(version, str)
        or not _VERSION_RE.fullmatch(version)
        or not isinstance(detail_url, str)
        or not detail_url
    ):
        raise CoreUpdateError("MaaCore stable summary entry is malformed")

    detail_response = await client.get(detail_url)
    detail_response.raise_for_status()
    detail = detail_response.json()
    try:
        assets = detail["details"]["assets"]
    except (TypeError, KeyError) as exc:
        raise CoreUpdateError("MaaCore stable detail has no asset list") from exc
    if not isinstance(assets, list):
        raise CoreUpdateError("MaaCore stable asset list must be an array")
    asset = match_core_asset(assets, system=system, machine=machine)
    mirrors = asset.get("mirrors", ())
    if not isinstance(mirrors, (list, tuple)):
        mirrors = ()
    valid_mirrors = tuple(url for url in mirrors if isinstance(url, str) and url)
    return CoreRelease(
        version=version,
        channel="stable",
        detail_url=detail_url,
        asset_name=asset["name"],
        asset_url=asset["browser_download_url"],
        asset_size=asset["size"],
        mirrors=valid_mirrors,
    )


def _safe_archive_destination(root: Path, name: str) -> Path:
    # Archive paths are POSIX paths even when created on Windows. Reject both
    # POSIX and Windows absolute paths and normalize backslashes before checks.
    normalized = name.replace("\\", "/")
    member_path = PurePosixPath(normalized)
    windows_path = PureWindowsPath(name)
    if (
        member_path.is_absolute()
        or windows_path.is_absolute()
        or windows_path.drive
        or any(part in {"..", ""} for part in member_path.parts)
    ):
        raise CoreUpdateError(f"unsafe path in MaaCore archive: {name!r}")
    destination = root.joinpath(*member_path.parts)
    if not destination.resolve().is_relative_to(root.resolve()):
        raise CoreUpdateError(f"unsafe path in MaaCore archive: {name!r}")
    return destination


def extract_core_archive(archive: str | Path, destination: str | Path) -> Path:
    """Extract a zip or tar.gz archive while rejecting links and path traversal."""
    archive_path = Path(archive)
    output = Path(destination)
    if output.exists() and any(output.iterdir()):
        raise CoreUpdateError(f"extraction destination is not empty: {output}")
    output.mkdir(parents=True, exist_ok=True)
    try:
        if archive_path.name.lower().endswith(".zip"):
            with zipfile.ZipFile(archive_path) as bundle:
                for item in bundle.infolist():
                    target = _safe_archive_destination(output, item.filename)
                    mode = item.external_attr >> 16
                    if mode and (mode & 0o170000) == 0o120000:
                        raise CoreUpdateError(f"symlink in MaaCore archive: {item.filename!r}")
                    if item.is_dir():
                        target.mkdir(parents=True, exist_ok=True)
                        continue
                    target.parent.mkdir(parents=True, exist_ok=True)
                    with bundle.open(item) as source, target.open("wb") as sink:
                        shutil.copyfileobj(source, sink)
        elif archive_path.name.lower().endswith(".tar.gz"):
            with tarfile.open(archive_path, mode="r:gz") as bundle:
                for item in bundle.getmembers():
                    target = _safe_archive_destination(output, item.name)
                    if not (item.isdir() or item.isfile()):
                        raise CoreUpdateError(f"link or special file in MaaCore archive: {item.name!r}")
                    if item.isdir():
                        target.mkdir(parents=True, exist_ok=True)
                        continue
                    target.parent.mkdir(parents=True, exist_ok=True)
                    source = bundle.extractfile(item)
                    if source is None:
                        raise CoreUpdateError(f"cannot read MaaCore archive member: {item.name!r}")
                    with source, target.open("wb") as sink:
                        shutil.copyfileobj(source, sink)
        else:
            raise CoreUpdateError(f"unsupported MaaCore archive format: {archive_path.name}")
    except (OSError, tarfile.TarError, zipfile.BadZipFile) as exc:
        raise CoreUpdateError(f"cannot extract MaaCore archive: {exc}") from exc
    return output


def _looks_like_core_package(path: Path) -> bool:
    return path.is_dir() and (path / "resource").is_dir()


def normalize_core_package(extracted: str | Path) -> Path:
    """Return the package root, accepting direct and one-directory-wrapped zips."""
    root = Path(extracted)
    if _looks_like_core_package(root):
        return root
    try:
        children = [item for item in root.iterdir() if item.is_dir()]
    except OSError as exc:
        raise CoreUpdateError(f"cannot inspect extracted MaaCore package: {exc}") from exc
    if len(children) == 1 and _looks_like_core_package(children[0]):
        return children[0]
    raise CoreUpdateError("MaaCore archive has an unsupported directory layout")


def validate_core_directory(
    directory: str | Path,
    *,
    system: str | None = None,
    machine: str | None = None,
) -> dict[str, Any]:
    """Check every required MaaCore library/resource file before installation."""
    root = Path(directory)
    key = core_platform_key(system, machine)
    library_name = {
        "macos-runtime-universal": "libMaaCore.dylib",
        "linux-aarch64": "libMaaCore.so",
        "linux-x86_64": "libMaaCore.so",
        "win-x64": "MaaCore.dll",
        "win-arm64": "MaaCore.dll",
    }[key]
    library = root / library_name
    if not library.is_file() or library.stat().st_size <= 0:
        raise CoreUpdateError(f"MaaCore package is missing a non-empty {library_name}")
    resource = root / "resource"
    for relative in ("tasks", "template", "config.json"):
        candidate = resource / relative
        if not (candidate.is_dir() if relative in {"tasks", "template"} else candidate.is_file()):
            raise CoreUpdateError(f"MaaCore package is missing resource/{relative}")
    version_path = resource / "version.json"
    try:
        with version_path.open("r", encoding="utf-8") as stream:
            version_data = json.load(stream)
    except (OSError, json.JSONDecodeError) as exc:
        raise CoreUpdateError("MaaCore package has an invalid resource/version.json") from exc
    if not isinstance(version_data, dict):
        raise CoreUpdateError("MaaCore resource/version.json must contain a JSON object")
    return version_data


def _absolute(path: Path) -> Path:
    return Path(os.path.abspath(os.fspath(path)))


def _check_target_backup(target: Path, backup: Path) -> tuple[Path, Path]:
    target_abs, backup_abs = _absolute(target), _absolute(backup)
    if target_abs == backup_abs or target_abs.parent != backup_abs.parent:
        raise CoreUpdateError("MaaCore target and its unique backup must be sibling paths")
    if backup_abs.name != f"{target_abs.name}.backup":
        raise CoreUpdateError("MaaCore backup path must be the unique <target>.backup sibling")
    if target.is_symlink() or backup.is_symlink():
        raise CoreUpdateError("MaaCore target and backup must not be symlinks")
    return target_abs, backup_abs


def apply_core_update(
    staged: str | Path, target: str | Path, backup: str | Path
) -> None:
    """Atomically switch staged core into place and restore target on failure."""
    staged_path, target_path, backup_path = Path(staged), Path(target), Path(backup)
    target_abs, backup_abs = _check_target_backup(target_path, backup_path)
    if not staged_path.is_dir():
        raise CoreUpdateError(f"staged MaaCore directory does not exist: {staged_path}")
    if target_abs.parent.stat().st_dev != staged_path.parent.stat().st_dev:
        raise CoreUpdateError("staged MaaCore and target must be on the same filesystem")

    if backup_path.exists():
        if backup_path.is_dir():
            shutil.rmtree(backup_path)
        else:
            backup_path.unlink()
    moved_target = False
    if target_path.exists():
        os.replace(target_path, backup_path)
        moved_target = True
    try:
        os.replace(staged_path, target_path)
    except BaseException:
        if moved_target and backup_path.exists() and not target_path.exists():
            os.replace(backup_path, target_path)
        raise


def rollback_core_update(target: str | Path, backup: str | Path) -> None:
    """Restore the configured unique backup to the MaaCore target directory."""
    target_path, backup_path = Path(target), Path(backup)
    _check_target_backup(target_path, backup_path)
    if not backup_path.is_dir():
        raise CoreUpdateError(f"MaaCore backup does not exist: {backup_path}")
    if target_path.exists():
        if not target_path.is_dir():
            raise CoreUpdateError(f"MaaCore target is not a directory: {target_path}")
        shutil.rmtree(target_path)
    os.replace(backup_path, target_path)


def _swap_core_update_dirs(target: str | Path, backup: str | Path) -> None:
    """Swap target and backup, retaining the former target as the new backup."""
    target_path, backup_path = Path(target), Path(backup)
    _check_target_backup(target_path, backup_path)
    if not target_path.is_dir() or not backup_path.is_dir():
        raise CoreUpdateError("MaaCore rollback swap requires target and backup directories")
    if target_path.parent.stat().st_dev != backup_path.parent.stat().st_dev:
        raise CoreUpdateError("MaaCore target and backup must be on the same filesystem")

    displaced = target_path.with_name(f".{target_path.name}.rollback-{uuid.uuid4().hex}")
    os.replace(target_path, displaced)
    try:
        os.replace(backup_path, target_path)
    except BaseException:
        if displaced.exists() and not target_path.exists():
            os.replace(displaced, target_path)
        raise
    try:
        os.replace(displaced, backup_path)
    except BaseException as swap_error:
        # Restore the pre-swap arrangement if the final rename fails.
        try:
            os.replace(target_path, backup_path)
            os.replace(displaced, target_path)
        except BaseException as recovery_error:
            swap_error.add_note(f"could not restore MaaCore swap: {recovery_error!r}")
        raise


def check_disk_space(
    temp_root: str | Path,
    asset_size: int,
    *,
    disk_usage: Callable[[str | Path], Any] = shutil.disk_usage,
    multiplier: int = 3,
) -> None:
    """Require package + extraction + backup space before downloading."""
    if asset_size < 0 or multiplier < 1:
        raise ValueError("asset_size must be non-negative and multiplier must be positive")
    root = Path(temp_root)
    root.mkdir(parents=True, exist_ok=True)
    free = disk_usage(root).free
    required = asset_size * multiplier
    if free < required:
        raise CoreUpdateError(
            f"insufficient disk space for MaaCore update: need {required}, have {free}"
        )


async def _call(callback: Callable[..., Any], *args: Any, **kwargs: Any) -> Any:
    result = callback(*args, **kwargs)
    if inspect.isawaitable(result):
        return await result
    return result


class CoreUpdateWorkflow:
    """Injected stable core update workflow for the public UpdateService.

    ``reconnect`` and ``ready_version`` receive the supplied ``core_client``;
    they are adapters for the app's current connect/device settings and GET_VERSION
    contract. They may be sync or async. All archive work completes before the
    supervisor enters maintenance or the active core directory is touched.
    """

    def __init__(
        self,
        *,
        http_client: httpx.AsyncClient,
        target_path: str | Path,
        temp_root: str | Path,
        supervisor: Any,
        core_client: Any,
        reconnect: Callable[[Any], Any],
        ready_version: Callable[[Any], Any],
        current_version: Callable[[Any], Any] | None = None,
        before_start: Callable[[Path], Any] | None = None,
        download_prefix: str | None = None,
        downloader: Callable[..., Any] = default_downloader,
        progress: Callable[[dict[str, Any]], Any] | None = None,
        disk_usage: Callable[[str | Path], Any] = shutil.disk_usage,
        system: str | None = None,
        machine: str | None = None,
    ) -> None:
        self.http_client = http_client
        self.target_path = Path(target_path)
        self.backup_path = self.target_path.with_name(f"{self.target_path.name}.backup")
        self.temp_root = Path(temp_root)
        self.supervisor = supervisor
        self.core_client = core_client
        self.reconnect = reconnect
        self.ready_version = ready_version
        self.current_version = current_version
        self.before_start = before_start
        self.download_prefix = download_prefix
        self.downloader = downloader
        self.progress = progress
        self.disk_usage = disk_usage
        self.system = system
        self.machine = machine

    async def _notify(self, phase: str, **details: Any) -> None:
        if self.progress is not None:
            await _call(self.progress, {"phase": phase, **details})

    def _download_urls(self, release: CoreRelease) -> tuple[str, ...]:
        urls: list[str] = []
        prefix = self.download_prefix.strip() if self.download_prefix else ""
        if prefix:
            urls.append(f"{prefix.rstrip('/')}/{release.asset_url}")
        urls.extend(release.mirrors)
        urls.append(release.asset_url)
        return tuple(dict.fromkeys(url for url in urls if url))

    async def _download_and_validate_archive(
        self,
        release: CoreRelease,
        archive_path: Path,
        extracted_path: Path,
        download_progress: Callable[[int, int], Any],
    ) -> Path:
        """Reuse a valid stable cache or try configured sources in order.

        Invalid cached files and partial downloads are discarded. A successfully
        downloaded and validated archive stays at its stable cache path until the
        workflow succeeds or the caller's seven day cache cleanup removes it.
        """

        archive_path.parent.mkdir(parents=True, exist_ok=True)
        partial_path = archive_path.with_suffix(archive_path.suffix + ".part")

        def clear_extraction() -> None:
            shutil.rmtree(extracted_path, ignore_errors=True)

        async def validate_cached() -> Path:
            await self._notify("verifying", version=release.version)
            extract_core_archive(archive_path, extracted_path)
            package = normalize_core_package(extracted_path)
            validate_core_directory(package, system=self.system, machine=self.machine)
            return package

        if archive_path.is_file():
            if archive_path.stat().st_size == release.asset_size:
                try:
                    return await validate_cached()
                except Exception as exc:
                    logger.warning("discarding invalid cached MaaCore archive %s: %s", archive_path, exc)
            archive_path.unlink(missing_ok=True)
            clear_extraction()

        failures: list[tuple[str, Exception]] = []
        for url in self._download_urls(release):
            archive_path.unlink(missing_ok=True)
            partial_path.unlink(missing_ok=True)
            clear_extraction()
            try:
                await _call(
                    self.downloader,
                    url,
                    archive_path,
                    expected_size=release.asset_size,
                    on_progress=download_progress,
                    resume=True,
                )
                if not archive_path.is_file() or archive_path.stat().st_size != release.asset_size:
                    actual = archive_path.stat().st_size if archive_path.is_file() else 0
                    raise CoreUpdateError(
                        f"archive size mismatch: expected {release.asset_size}, got {actual}"
                    )
                return await validate_cached()
            except Exception as exc:
                failures.append((url, exc))
                logger.warning("MaaCore download source failed (%s): %s", url, exc)
                archive_path.unlink(missing_ok=True)
                partial_path.unlink(missing_ok=True)
                clear_extraction()

        if not failures:
            raise CoreUpdateError("MaaCore release has no usable download source")
        final_url, final_error = failures[-1]
        attempted = "; ".join(f"{url}: {error}" for url, error in failures)
        raise CoreUpdateError(
            f"all MaaCore download sources failed; final source {final_url}: "
            f"{final_error}; attempts: {attempted}"
        ) from final_error

    async def update(
        self, *, channel: str = "stable", force: bool = False
    ) -> CoreRelease | None:
        """Download, validate, install, restart and verify one stable release."""
        if channel != "stable":
            raise CoreUpdateError("MaaCore updates support only the stable channel")
        if not self.target_path.is_dir():
            raise CoreUpdateError(f"configured MaaCore target does not exist: {self.target_path}")

        release = await resolve_core_release(
            self.http_client, system=self.system, machine=self.machine
        )
        if self.current_version is not None and not force:
            try:
                installed_version = await _call(self.current_version, self.core_client)
            except Exception:
                installed_version = None
                logger.info("could not determine current MaaCore version; continuing update")
            if installed_version == release.version:
                await self._notify("skipped", version=release.version)
                return None

        self.temp_root.mkdir(parents=True, exist_ok=True)
        if self.temp_root.stat().st_dev != self.target_path.parent.stat().st_dev:
            raise CoreUpdateError(
                "MaaCore staging directory and configured target must share a filesystem"
            )
        check_disk_space(
            self.temp_root, release.asset_size, disk_usage=self.disk_usage
        )
        version_dir = self.temp_root / release.version
        version_dir.mkdir(parents=True, exist_ok=True)
        work_dir = version_dir / f".stage-{uuid.uuid4().hex}"
        archive_path = version_dir / release.asset_name
        extracted_path = work_dir / "extracted"
        work_dir.mkdir(parents=True, exist_ok=False)
        applied = False
        stopped = False
        started_new = False
        archive_validated = False
        installed = False
        try:
            await self._notify("downloading", version=release.version, downloaded=0, total=release.asset_size)

            async def download_progress(downloaded: int, total: int) -> None:
                await self._notify(
                    "downloading",
                    version=release.version,
                    downloaded=downloaded,
                    total=total,
                )

            staged = await self._download_and_validate_archive(
                release, archive_path, extracted_path, download_progress
            )
            archive_validated = True

            await self._notify("applying", version=release.version)
            async with self.supervisor.acquire_maintenance():
                stopped = True
                await self.supervisor.stop()
                apply_core_update(staged, self.target_path, self.backup_path)
                applied = True
                if self.before_start is not None:
                    await _call(self.before_start, self.target_path)
                await self._notify("restarting", version=release.version)
                await self.supervisor.start()
                started_new = True
                await _call(self.reconnect, self.core_client)
                running_version = await _call(self.ready_version, self.core_client)
                if running_version != release.version:
                    raise CoreUpdateError(
                        "MaaCore READY version mismatch: "
                        f"expected {release.version}, got {running_version!r}"
                    )
            await self._notify("done", version=release.version)
            installed = True
            return release
        except BaseException as update_error:
            recovery_error: BaseException | None = None
            if applied or stopped:
                try:
                    async with self.supervisor.acquire_maintenance():
                        if started_new:
                            await self.supervisor.stop()
                        if applied:
                            rollback_core_update(self.target_path, self.backup_path)
                        if stopped:
                            await self.supervisor.start()
                            await _call(self.reconnect, self.core_client)
                except BaseException as exc:
                    recovery_error = exc
            if recovery_error is not None:
                update_error.add_note(f"MaaCore rollback/restart also failed: {recovery_error!r}")
            raise
        finally:
            shutil.rmtree(work_dir, ignore_errors=True)
            if not archive_validated or installed:
                archive_path.unlink(missing_ok=True)
                archive_path.with_suffix(archive_path.suffix + ".part").unlink(missing_ok=True)
                try:
                    version_dir.rmdir()
                except OSError:
                    pass

    async def rollback(self) -> str:
        """Restore the configured previous core and verify it through the client.

        The active target and the sole ``<target>.backup`` directory are swapped
        inside the supervisor maintenance window. If overlay, startup, reconnect
        or READY verification fails, the swap is reversed and the original core
        is restarted before ``UPDATE_ROLLBACK_FAILED`` is raised.
        """
        try:
            if not self.target_path.is_dir():
                raise CoreUpdateError(f"configured MaaCore target is missing: {self.target_path}")
            if not self.backup_path.is_dir() or self.backup_path.is_symlink():
                raise CoreUpdateError(f"configured MaaCore backup is missing: {self.backup_path}")
            validate_core_directory(
                self.backup_path, system=self.system, machine=self.machine
            )
        except Exception as exc:
            raise AppError(
                ErrorCode.UPDATE_ROLLBACK_FAILED,
                "MaaCore backup is unavailable or invalid",
                {"reason": str(exc)},
            ) from exc

        stopped = False
        swapped = False
        start_attempted = False
        try:
            async with self.supervisor.acquire_maintenance():
                stopped = True
                await self.supervisor.stop()
                _swap_core_update_dirs(self.target_path, self.backup_path)
                swapped = True
                if self.before_start is not None:
                    await _call(self.before_start, self.target_path)
                await self._notify("restarting", action="rollback")
                start_attempted = True
                await self.supervisor.start()
                await _call(self.reconnect, self.core_client)
                version = await _call(self.ready_version, self.core_client)
                if not isinstance(version, str) or not version.strip():
                    raise CoreUpdateError("MaaCore rollback completed without a READY version")
        except BaseException as rollback_error:
            recovery_error: BaseException | None = None
            if stopped or swapped or start_attempted:
                try:
                    async with self.supervisor.acquire_maintenance():
                        if start_attempted:
                            await self.supervisor.stop()
                        if swapped:
                            _swap_core_update_dirs(self.target_path, self.backup_path)
                        if stopped:
                            await self.supervisor.start()
                            await _call(self.reconnect, self.core_client)
                except BaseException as exc:
                    recovery_error = exc
            details: dict[str, Any] = {"reason": str(rollback_error)}
            if recovery_error is not None:
                details["recovery_error"] = str(recovery_error)
            raise AppError(
                ErrorCode.UPDATE_ROLLBACK_FAILED,
                "MaaCore rollback failed",
                details,
            ) from rollback_error
        await self._notify("done", version=version, action="rollback")
        return version
