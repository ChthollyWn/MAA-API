"""Offline-testable update primitives for MAA's two active resource channels.

OTA data is a single conditional JSON request. MaaResource is version-checked
separately, downloaded without Range/resume, and retained as a raw repository
overlay so a newly installed MaaCore can receive it before its first start.
This module never imports MaaCore; HTTP, paths and lifecycle callbacks are
provided by the caller.
"""

from __future__ import annotations

import hashlib
import inspect
import json
import logging
import math
import os
import shutil
import time
import uuid
import zipfile
from collections.abc import Sequence
from dataclasses import dataclass
from enum import StrEnum
from pathlib import Path, PurePosixPath, PureWindowsPath
from typing import Any, Callable, Mapping

import httpx

from maa_api.domain.enums import ResourceChannel
from maa_api.util.downloader import download as default_downloader

__all__ = [
    "OTA_URL",
    "MIRROR_VERSION_URL",
    "REPO_VERSION_URL",
    "REPO_ARCHIVE_URL",
    "ResourceChannel",
    "ResourceSource",
    "ResourceItem",
    "RESOURCE_MANIFEST",
    "ResourceUpdateError",
    "ResourceCheck",
    "StagedResource",
    "AppliedResource",
    "check_ota",
    "check_repo",
    "check_resources",
    "check_repo_disk_space",
    "stage_ota",
    "stage_repo_overlay",
    "apply_ota",
    "apply_repo_overlay",
    "merge_repo_overlay",
    "rollback_ota",
    "rollback_repo_overlay",
    "reapply_overlay",
    "reload",
    "ResourceUpdateWorkflow",
]

logger = logging.getLogger(__name__)

OTA_URL = "https://api.maa.plus/MaaAssistantArknights/api/resource/tasks.json"
MIRROR_VERSION_URL = "https://mirrorchyan.com/api/resources/MaaResource/latest"
REPO_VERSION_URL = (
    "https://raw.githubusercontent.com/MaaAssistantArknights/"
    "MaaResource/main/resource/version.json"
)
REPO_ARCHIVE_URL = (
    "https://github.com/MaaAssistantArknights/MaaResource/"
    "archive/refs/heads/main.zip"
)


class ResourceSource(StrEnum):
    OTA = "ota"
    GIT_ARCHIVE = "git_archive"


@dataclass(frozen=True, slots=True)
class ResourceItem:
    key: str
    source: ResourceSource
    url: str
    dest: str
    required: bool
    client_types: tuple[str, ...] | None
    subdir: str | None = None


RESOURCE_MANIFEST: tuple[ResourceItem, ...] = (
    ResourceItem(
        key="cn_tasks",
        source=ResourceSource.OTA,
        url=OTA_URL,
        dest="cache/resource/tasks.json",
        required=True,
        client_types=("Official", "Bilibili"),
    ),
    ResourceItem(
        key="maa_resource",
        source=ResourceSource.GIT_ARCHIVE,
        url=REPO_ARCHIVE_URL,
        dest="repo",
        required=False,
        client_types=None,
        subdir="MaaResource-main/resource",
    ),
)

_OTA_ITEM, _REPO_ITEM = RESOURCE_MANIFEST
_REPO_RESOURCE_FILES = frozenset(
    {
        "Arknights-Tile-Pos",
        "battle_data.json",
        "global",
        "infrast.json",
        "item_index.json",
        "recruitment.json",
        "stages.json",
        "template",
        "version.json",
    }
)
_REPO_DIRECTORY_ROOTS = frozenset({"Arknights-Tile-Pos", "global", "template"})
_REPO_ARCHIVE_ESTIMATE = 13_916_116
_REPO_EXPANDED_ESTIMATE = 107 * 1024 * 1024
RESOURCE_DISK_GROWTH_FACTOR = 1.5
OTA_BACKUP_LIMIT = 5
RESOURCE_ARCHIVE_CACHE_TTL_SECONDS = 7 * 24 * 60 * 60


class ResourceUpdateError(RuntimeError):
    """A resource check, extraction, installation or rollback failed."""


@dataclass(frozen=True, slots=True)
class ResourceCheck:
    channel: ResourceChannel
    changed: bool
    version: str | None = None
    body: bytes | None = None
    checksum: str | None = None
    etag: str | None = None
    last_modified: str | None = None
    source: str | None = None
    release_note: str | None = None
    error: str | None = None


@dataclass(frozen=True, slots=True)
class StagedResource:
    channel: ResourceChannel
    path: Path
    version: str | None = None
    checksum: str | None = None
    etag: str | None = None
    last_modified: str | None = None
    cleanup_path: Path | None = None
    archive_path: Path | None = None


@dataclass(frozen=True, slots=True)
class AppliedResource:
    channel: ResourceChannel
    target: Path
    backup: Path
    repo_layer_target: Path | None = None
    repo_layer_backup: Path | None = None


async def _call(callback: Callable[..., Any] | None, *args: Any, **kwargs: Any) -> Any:
    if callback is None:
        return None
    result = callback(*args, **kwargs)
    if inspect.isawaitable(result):
        return await result
    return result


def _metadata_value(metadata: Any, name: str) -> Any:
    if metadata is None:
        return None
    if isinstance(metadata, Mapping):
        return metadata.get(name)
    return getattr(metadata, name, None)


def _checked_version(payload: Any, *, field: str, label: str) -> str:
    if not isinstance(payload, Mapping):
        raise ResourceUpdateError(f"{label} version response must be a JSON object")
    version = payload.get(field)
    if not isinstance(version, str) or not version.strip() or len(version) > 64:
        raise ResourceUpdateError(f"{label} version response has an invalid {field}")
    return version.strip()


def _parse_mirror(payload: Any) -> tuple[str, str | None]:
    if not isinstance(payload, Mapping):
        raise ResourceUpdateError("Mirror 酱版本响应不是 JSON 对象")
    if payload.get("code") != 0:
        raise ResourceUpdateError("Mirror 酱版本查询未成功")
    data = payload.get("data")
    if not isinstance(data, Mapping):
        raise ResourceUpdateError("Mirror 酱版本响应缺少 data")
    return (
        _checked_version(data, field="version_name", label="Mirror 酱"),
        data.get("release_note") if isinstance(data.get("release_note"), str) else None,
    )


def _json_bytes(body: bytes, *, label: str) -> Any:
    try:
        return json.loads(body)
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise ResourceUpdateError(f"{label} 返回了无效 JSON") from exc


async def check_ota(
    client: httpx.AsyncClient,
    *,
    metadata: Any = None,
    progress: Callable[[dict[str, Any]], Any] | None = None,
) -> ResourceCheck:
    """Conditionally fetch and validate the OTA manifest's tasks.json."""
    headers: dict[str, str] = {}
    etag = _metadata_value(metadata, "etag")
    modified = _metadata_value(metadata, "last_modified")
    if isinstance(etag, str) and etag:
        headers["If-None-Match"] = etag
    if isinstance(modified, str) and modified:
        headers["If-Modified-Since"] = modified
    await _call(progress, {"phase": "checking", "channel": "ota"})
    try:
        response = await client.get(_OTA_ITEM.url, headers=headers)
    except httpx.HTTPError as exc:
        raise ResourceUpdateError("UPDATE_MANIFEST_UNAVAILABLE: OTA 请求失败") from exc
    if response.status_code == httpx.codes.NOT_MODIFIED:
        return ResourceCheck(
            ResourceChannel.OTA,
            changed=False,
            etag=response.headers.get("ETag") or etag,
            last_modified=response.headers.get("Last-Modified") or modified,
            source="ota",
        )
    try:
        response.raise_for_status()
    except httpx.HTTPError as exc:
        raise ResourceUpdateError("UPDATE_MANIFEST_UNAVAILABLE: OTA 请求失败") from exc
    body = response.content
    _json_bytes(body, label="OTA tasks.json")
    checksum = hashlib.sha256(body).hexdigest()
    old_checksum = _metadata_value(metadata, "checksum")
    return ResourceCheck(
        ResourceChannel.OTA,
        changed=checksum != old_checksum,
        body=body,
        checksum=checksum,
        etag=response.headers.get("ETag"),
        last_modified=response.headers.get("Last-Modified"),
        source="ota",
    )


async def check_repo(
    client: httpx.AsyncClient,
    *,
    local_version: str | None,
    progress: Callable[[dict[str, Any]], Any] | None = None,
) -> ResourceCheck:
    """Check the database-owned repo version, preferring Mirror 酱 then raw."""
    await _call(progress, {"phase": "checking", "channel": "repo"})
    version: str | None = None
    release_note: str | None = None
    source: str | None = None
    try:
        response = await client.get(MIRROR_VERSION_URL)
        response.raise_for_status()
        version, release_note = _parse_mirror(response.json())
        source = "mirrorchyan"
    except (httpx.HTTPError, ValueError, ResourceUpdateError) as mirror_error:
        logger.info("Mirror 酱 version check failed; falling back to raw: %s", mirror_error)
        try:
            response = await client.get(REPO_VERSION_URL)
            response.raise_for_status()
            payload = response.json()
            version = _checked_version(payload, field="last_updated", label="MaaResource")
            source = "raw"
        except (httpx.HTTPError, ValueError, ResourceUpdateError) as raw_error:
            raise ResourceUpdateError(
                "UPDATE_MANIFEST_UNAVAILABLE: Mirror 酱与 MaaResource raw 均无法查询"
            ) from raw_error
    return ResourceCheck(
        ResourceChannel.REPO,
        changed=version != local_version,
        version=version,
        source=source,
        release_note=release_note,
    )


async def check_resources(
    client: httpx.AsyncClient,
    channel: ResourceChannel | str,
    *,
    ota_metadata: Any = None,
    repo_version: str | None = None,
    progress: Callable[[dict[str, Any]], Any] | None = None,
) -> dict[ResourceChannel, ResourceCheck]:
    """Check either selected channel or both, without coupling their results."""
    selected = ResourceChannel(channel)
    checks: dict[ResourceChannel, ResourceCheck] = {}
    if selected in (ResourceChannel.OTA, ResourceChannel.ALL):
        try:
            checks[ResourceChannel.OTA] = await check_ota(
                client, metadata=ota_metadata, progress=progress
            )
        except ResourceUpdateError as exc:
            if selected == ResourceChannel.OTA:
                raise
            checks[ResourceChannel.OTA] = ResourceCheck(
                ResourceChannel.OTA,
                changed=False,
                source="unavailable",
                error=str(exc),
            )
    if selected in (ResourceChannel.REPO, ResourceChannel.ALL):
        try:
            checks[ResourceChannel.REPO] = await check_repo(
                client, local_version=repo_version, progress=progress
            )
        except ResourceUpdateError as exc:
            if selected == ResourceChannel.REPO:
                raise
            # The optional repo result remains visible to UpdateService while
            # the required OTA check result is retained for channel=all.
            checks[ResourceChannel.REPO] = ResourceCheck(
                ResourceChannel.REPO,
                changed=False,
                source="unavailable",
                error=str(exc),
            )
    return checks


def stage_ota(
    check: ResourceCheck,
    staging_root: str | Path,
) -> StagedResource:
    """Validate and persist the tiny OTA JSON to a same-filesystem stage file."""
    if check.channel != ResourceChannel.OTA or not check.changed or check.body is None:
        raise ResourceUpdateError("OTA check has no changed payload to stage")
    _json_bytes(check.body, label="OTA tasks.json")
    root = Path(staging_root)
    root.mkdir(parents=True, exist_ok=True)
    destination = root / f"tasks-{uuid.uuid4().hex}.json"
    with destination.open("xb") as stream:
        stream.write(check.body)
        stream.flush()
        os.fsync(stream.fileno())
    return StagedResource(
        channel=ResourceChannel.OTA,
        path=destination,
        checksum=check.checksum or hashlib.sha256(check.body).hexdigest(),
        etag=check.etag,
        last_modified=check.last_modified,
    )


def _safe_zip_relative(name: str) -> PurePosixPath:
    normalized = name.replace("\\", "/")
    path = PurePosixPath(normalized)
    windows_path = PureWindowsPath(name)
    if (
        not normalized
        or path.is_absolute()
        or windows_path.is_absolute()
        or windows_path.drive
        or any(part in {"", ".", ".."} for part in path.parts)
        or any(":" in part for part in path.parts)
    ):
        raise ResourceUpdateError(f"unsafe path in MaaResource archive: {name!r}")
    return path


def _is_zip_symlink(info: zipfile.ZipInfo) -> bool:
    mode = info.external_attr >> 16
    return bool(mode and (mode & 0o170000) == 0o120000)


def _tree_bytes(root: Path) -> int:
    """Count regular-file bytes without following symlinks."""
    if not root.exists():
        return 0
    if root.is_symlink() or not root.is_dir():
        raise ResourceUpdateError(f"disk estimate root must be a real directory: {root}")
    total = 0
    for item in root.rglob("*"):
        if item.is_symlink():
            continue
        if item.is_file():
            total += item.stat().st_size
    return total


def _existing_path(path: Path) -> Path:
    current = path
    while not current.exists():
        if current.parent == current:
            raise ResourceUpdateError(f"no existing ancestor for path: {path}")
        current = current.parent
    return current


def check_repo_disk_space(
    base_resource: str | Path,
    staging_root: str | Path,
    *,
    current_repo_resource: str | Path | None = None,
    disk_usage: Callable[[str | Path], Any] = shutil.disk_usage,
    archive_estimate: int = _REPO_ARCHIVE_ESTIMATE,
    expanded_estimate: int = _REPO_EXPANDED_ESTIMATE,
    growth_factor: float = RESOURCE_DISK_GROWTH_FACTOR,
) -> int:
    """Preflight actual peak writes for repo fallback without creating files.

    The current MaaCore resource tree is copied once for the overlay staging
    directory. Its previous version and the previous raw repo tree only move by
    same-filesystem rename, so they are not charged as duplicate copies. The
    downloaded archive and extracted repo tree are charged alongside that copy.
    """
    base = Path(base_resource)
    staging = Path(staging_root)
    if growth_factor < 1 or archive_estimate < 0 or expanded_estimate < 0:
        raise ValueError("resource disk estimates must be non-negative and growth_factor >= 1")
    if not base.is_dir() or base.is_symlink():
        raise ResourceUpdateError(f"MaaCore resource directory does not exist: {base}")
    existing_stage = _existing_path(staging)
    if existing_stage.stat().st_dev != base.parent.stat().st_dev:
        raise ResourceUpdateError("repo staging and MaaCore resource must share a filesystem")
    core_bytes = _tree_bytes(base)
    repo_bytes = (
        _tree_bytes(Path(current_repo_resource))
        if current_repo_resource is not None and Path(current_repo_resource).exists()
        else 0
    )
    expanded_bytes = max(repo_bytes, expanded_estimate)
    required = math.ceil((core_bytes + archive_estimate + expanded_bytes) * growth_factor)
    free = disk_usage(existing_stage).free
    if free < required:
        raise ResourceUpdateError(
            "UPDATE_DISK_INSUFFICIENT: MaaResource fallback needs "
            f"{required} bytes for staged core resource, ZIP and extraction; {free} available"
        )
    return required


def _validate_repo_tree(resource_root: Path) -> str:
    if not resource_root.is_dir() or resource_root.is_symlink():
        raise ResourceUpdateError("MaaResource archive is missing resource/")
    try:
        entries = list(resource_root.iterdir())
    except OSError as exc:
        raise ResourceUpdateError("cannot inspect MaaResource resource/") from exc
    unexpected = {item.name for item in entries} - _REPO_RESOURCE_FILES
    if unexpected:
        raise ResourceUpdateError(
            f"MaaResource contains paths outside the resource whitelist: {sorted(unexpected)}"
        )
    for item in entries:
        if item.is_symlink() or not (item.is_dir() or item.is_file()):
            raise ResourceUpdateError(f"unsafe MaaResource entry: {item.name!r}")
        if item.is_dir() != (item.name in _REPO_DIRECTORY_ROOTS):
            raise ResourceUpdateError(f"MaaResource entry has an invalid type: {item.name!r}")
    version_path = resource_root / "version.json"
    try:
        version_payload = json.loads(version_path.read_text(encoding="utf-8"))
    except (OSError, UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise ResourceUpdateError("MaaResource version.json is invalid") from exc
    return _checked_version(version_payload, field="last_updated", label="MaaResource")


async def stage_repo_overlay(
    client: httpx.AsyncClient,
    check: ResourceCheck,
    staging_root: str | Path,
    *,
    downloader: Callable[..., Any] = default_downloader,
    download_prefix: str | None = None,
    cache_only: bool = False,
    progress: Callable[[dict[str, Any]], Any] | None = None,
    base_resource: str | Path | None = None,
    current_repo_resource: str | Path | None = None,
    disk_usage: Callable[[str | Path], Any] = shutil.disk_usage,
    growth_factor: float = RESOURCE_DISK_GROWTH_FACTOR,
) -> StagedResource:
    """Download and safely extract only MaaResource-main/resource from ZIP.

    The codeload archive deliberately uses ``resume=False``: its Range endpoint
    returns the entire archive with HTTP 200, so a partial append would corrupt it.
    """
    if check.channel != ResourceChannel.REPO or not check.changed:
        raise ResourceUpdateError("repo check has no changed version to stage")
    root = Path(staging_root)
    if base_resource is not None:
        check_repo_disk_space(
            base_resource,
            root,
            current_repo_resource=current_repo_resource,
            disk_usage=disk_usage,
            growth_factor=growth_factor,
        )
    root.mkdir(parents=True, exist_ok=True)
    cache_archive = root / "repo-archive-cache.zip"
    cache_metadata = root / "repo-archive-cache.json"
    work = root / f"repo-{uuid.uuid4().hex}"
    work.mkdir()
    archive = work / "main.zip"
    extracted = work / "extracted"
    extracted.mkdir()
    await _call(progress, {"phase": "downloading", "channel": "repo", "downloaded": 0, "total": 0})

    async def report(downloaded: int, total: int) -> None:
        await _call(
            progress,
            {"phase": "downloading", "channel": "repo", "downloaded": downloaded, "total": total},
        )

    download_urls: list[str] = []
    if download_prefix and download_prefix.strip():
        download_urls.append(
            f"{download_prefix.strip().rstrip('/')}/{_REPO_ITEM.url}"
        )
    download_urls.append(_REPO_ITEM.url)
    download_urls = list(dict.fromkeys(download_urls))
    try:
        cached_archive = False
        try:
            cached = json.loads(cache_metadata.read_text(encoding="utf-8"))
            cached_archive = (
                check.version is not None
                and cached.get("version") == check.version
                and cache_archive.is_file()
                and not cache_archive.is_symlink()
                and time.time() - cache_archive.stat().st_mtime
                <= RESOURCE_ARCHIVE_CACHE_TTL_SECONDS
            )
        except (OSError, ValueError, TypeError):
            cached_archive = False
        if cached_archive:
            shutil.copy2(cache_archive, archive)
        elif cache_only:
            raise ResourceUpdateError(
                "UPDATE_DOWNLOAD_FAILED: validated MaaResource retry archive is unavailable"
            )
        else:
            failures: list[tuple[str, Exception]] = []
            downloaded = False
            for url in download_urls:
                try:
                    await _call(
                        downloader,
                        url,
                        archive,
                        resume=False,
                        client=client,
                        on_progress=report,
                    )
                    downloaded = True
                    break
                except Exception as exc:
                    failures.append((url, exc))
                    logger.warning("MaaResource archive source failed (%s): %s", url, exc)
                    archive.unlink(missing_ok=True)
                    archive.with_suffix(archive.suffix + ".part").unlink(missing_ok=True)
            if not downloaded:
                details = "; ".join(f"{url}: {error}" for url, error in failures)
                raise ResourceUpdateError(
                    f"UPDATE_DOWNLOAD_FAILED: all MaaResource archive sources failed: {details}"
                ) from (failures[-1][1] if failures else None)
        try:
            with zipfile.ZipFile(archive) as bundle:
                bad_member = bundle.testzip()
                if bad_member is not None:
                    raise ResourceUpdateError(f"corrupt MaaResource ZIP member: {bad_member}")
                names = [info.filename for info in bundle.infolist()]
                if "MaaResource-main/" not in names:
                    raise ResourceUpdateError("MaaResource ZIP has no MaaResource-main/ root")
                prefix = "MaaResource-main/resource/"
                if not any(name.startswith(prefix) for name in names):
                    raise ResourceUpdateError("MaaResource ZIP has no MaaResource-main/resource/")
                if base_resource is not None:
                    # The ZIP has already consumed space. Confirm the remaining
                    # peak before writing any extracted repo members.
                    expanded_bytes = sum(
                        info.file_size
                        for info in bundle.infolist()
                        if info.filename.startswith(prefix) and not info.is_dir()
                    )
                    required_remaining = math.ceil(
                        (
                            (_tree_bytes(Path(base_resource)) + expanded_bytes) * growth_factor
                            + archive.stat().st_size * (growth_factor - 1)
                        )
                    )
                    free_now = disk_usage(_existing_path(Path(base_resource))).free
                    if free_now < required_remaining:
                        raise ResourceUpdateError(
                            "UPDATE_DISK_INSUFFICIENT: MaaResource extraction and overlay staging "
                            f"need {required_remaining} additional bytes; {free_now} available"
                        )
                seen: set[str] = set()
                target_resource = extracted / "repo" / "resource"
                for info in bundle.infolist():
                    relative = _safe_zip_relative(info.filename)
                    if _is_zip_symlink(info):
                        raise ResourceUpdateError(f"symlink in MaaResource ZIP: {info.filename!r}")
                    if not (relative.parts[0] == "MaaResource-main"):
                        raise ResourceUpdateError(f"unexpected top-level MaaResource path: {info.filename!r}")
                    if len(relative.parts) < 2 or relative.parts[1] != "resource":
                        # The archive also contains cache/.gitignore. It is intentionally ignored.
                        continue
                    remainder = PurePosixPath(*relative.parts[2:])
                    if not remainder.parts:
                        target_resource.mkdir(parents=True, exist_ok=True)
                        continue
                    if remainder.parts[0] not in _REPO_RESOURCE_FILES:
                        raise ResourceUpdateError(
                            f"MaaResource path is outside the overlay whitelist: {info.filename!r}"
                        )
                    is_directory_root = remainder.parts[0] in _REPO_DIRECTORY_ROOTS
                    if len(remainder.parts) == 1 and info.is_dir() != is_directory_root:
                        raise ResourceUpdateError(
                            f"MaaResource path has an invalid type: {info.filename!r}"
                        )
                    if not is_directory_root and len(remainder.parts) != 1:
                        raise ResourceUpdateError(
                            f"MaaResource file path has unexpected children: {info.filename!r}"
                        )
                    rel_key = remainder.as_posix()
                    if rel_key in seen:
                        raise ResourceUpdateError(f"duplicate MaaResource ZIP path: {rel_key!r}")
                    seen.add(rel_key)
                    destination = target_resource.joinpath(*remainder.parts)
                    if not destination.resolve().is_relative_to(target_resource.resolve()):
                        raise ResourceUpdateError(f"unsafe MaaResource path: {info.filename!r}")
                    if info.is_dir():
                        destination.mkdir(parents=True, exist_ok=True)
                    else:
                        destination.parent.mkdir(parents=True, exist_ok=True)
                        with bundle.open(info) as source, destination.open("xb") as sink:
                            shutil.copyfileobj(source, sink)
        except (OSError, zipfile.BadZipFile, RuntimeError) as exc:
            if isinstance(exc, ResourceUpdateError):
                raise
            raise ResourceUpdateError("UPDATE_EXTRACT_FAILED: cannot extract MaaResource ZIP") from exc
        repo_resource = extracted / "repo" / "resource"
        actual_version = _validate_repo_tree(repo_resource)
        if cached_archive and check.version is not None and actual_version != check.version:
            cache_archive.unlink(missing_ok=True)
            cache_metadata.unlink(missing_ok=True)
            raise ResourceUpdateError(
                "UPDATE_EXTRACT_FAILED: cached MaaResource version no longer matches the checked version"
            )
        if not cached_archive:
            staged_archive = root / f".repo-archive-cache-{uuid.uuid4().hex}.zip"
            staged_metadata = root / f".repo-archive-cache-{uuid.uuid4().hex}.json"
            shutil.copy2(archive, staged_archive)
            staged_metadata.write_text(
                json.dumps({"version": actual_version}, ensure_ascii=False),
                encoding="utf-8",
            )
            os.replace(staged_archive, cache_archive)
            os.replace(staged_metadata, cache_metadata)
        return StagedResource(
            channel=ResourceChannel.REPO,
            path=extracted / "repo",
            version=actual_version,
            cleanup_path=work,
            archive_path=cache_archive,
        )
    except BaseException:
        shutil.rmtree(work, ignore_errors=True)
        raise


def _require_resource_roots(base_resource: Path, repo_resource: Path) -> tuple[Path, Path]:
    base = Path(os.path.abspath(os.fspath(base_resource)))
    repo = Path(os.path.abspath(os.fspath(repo_resource)))
    if base.name != "resource" or repo.name != "resource" or repo.parent.name != "repo":
        raise ResourceUpdateError(
            "overlay roots must be MaaCore maa_path/resource and maa-layers/repo/resource"
        )
    if base.is_symlink() or repo.is_symlink():
        raise ResourceUpdateError("resource overlay roots must not be symlinks")
    return base, repo


def _validate_overlay_source(repo_resource: Path) -> list[Path]:
    version = _validate_repo_tree(repo_resource)
    del version
    files: list[Path] = []
    for item in repo_resource.rglob("*"):
        relative = item.relative_to(repo_resource)
        if item.is_symlink() or not (item.is_file() or item.is_dir()):
            raise ResourceUpdateError(f"unsafe repo overlay path: {relative.as_posix()!r}")
        if not relative.parts or relative.parts[0] not in _REPO_RESOURCE_FILES:
            raise ResourceUpdateError(f"repo overlay path is not allowed: {relative.as_posix()!r}")
        if ".." in relative.parts or relative.is_absolute():
            raise ResourceUpdateError(f"unsafe repo overlay path: {relative.as_posix()!r}")
        if item.is_file():
            files.append(item)
    return files


def _overlay_copy(base: Path, repo: Path, staged_resource: Path) -> None:
    if not base.is_dir():
        raise ResourceUpdateError(f"MaaCore base resource directory does not exist: {base}")
    files = _validate_overlay_source(repo)
    shutil.copytree(base, staged_resource, symlinks=True)
    stage_root = staged_resource.resolve()
    for source in files:
        relative = source.relative_to(repo)
        destination = staged_resource.joinpath(*relative.parts)
        cursor = staged_resource
        for part in relative.parts[:-1]:
            cursor = cursor / part
            if cursor.is_symlink():
                raise ResourceUpdateError(f"MaaCore resource path contains symlink: {relative.as_posix()!r}")
        if destination.is_symlink():
            raise ResourceUpdateError(f"MaaCore resource target is symlink: {relative.as_posix()!r}")
        if not destination.resolve(strict=False).is_relative_to(stage_root):
            raise ResourceUpdateError(f"MaaCore resource path escapes staging: {relative.as_posix()!r}")
        destination.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(source, destination)


def _sibling_backup_path(target: Path, backup: Path) -> tuple[Path, Path]:
    target_abs = Path(os.path.abspath(os.fspath(target)))
    backup_abs = Path(os.path.abspath(os.fspath(backup)))
    if target_abs.parent != backup_abs.parent or target_abs == backup_abs:
        raise ResourceUpdateError("resource target and backup must be sibling directories")
    if backup_abs.name != f"{target_abs.name}.backup":
        raise ResourceUpdateError("resource backup must be the unique <target>.backup sibling")
    if target.is_symlink() or backup.is_symlink():
        raise ResourceUpdateError("resource target and backup must not be symlinks")
    return target_abs, backup_abs


def _replace_directory_with_backup(staged: Path, target: Path, backup: Path) -> None:
    target_abs, backup_abs = _sibling_backup_path(target, backup)
    if not staged.is_dir():
        raise ResourceUpdateError(f"staged resource does not exist: {staged}")
    if staged.parent.stat().st_dev != target_abs.parent.stat().st_dev:
        raise ResourceUpdateError("resource staging and target must share a filesystem")
    if backup_abs.exists():
        if backup_abs.is_dir():
            shutil.rmtree(backup_abs)
        else:
            backup_abs.unlink()
    moved = False
    if target_abs.exists():
        os.replace(target_abs, backup_abs)
        moved = True
    try:
        os.replace(staged, target_abs)
    except BaseException:
        if moved and backup_abs.exists() and not target_abs.exists():
            os.replace(backup_abs, target_abs)
        raise


def apply_ota(staged: StagedResource, layers_root: str | Path) -> AppliedResource:
    """Atomically install cache/resource/tasks.json and retain five snapshots."""
    if staged.channel != ResourceChannel.OTA or not staged.path.is_file():
        raise ResourceUpdateError("invalid staged OTA resource")
    root = Path(layers_root)
    target = root / "cache" / "resource" / "tasks.json"
    backup_root = root / "cache.backup"
    backup = backup_root / "1" / "resource" / "tasks.json"
    target.parent.mkdir(parents=True, exist_ok=True)
    if staged.path.parent.stat().st_dev != target.parent.stat().st_dev:
        raise ResourceUpdateError("OTA staging and target must share a filesystem")

    # The OTA file is tiny. Keep the last five cache trees as numbered
    # snapshots instead of mixing this backup with MaaCore's cache/avatars.
    oldest = backup_root / str(OTA_BACKUP_LIMIT)
    if oldest.exists():
        shutil.rmtree(oldest)
    for index in range(OTA_BACKUP_LIMIT - 1, 0, -1):
        source = backup_root / str(index)
        destination = backup_root / str(index + 1)
        if source.exists():
            if destination.exists():
                shutil.rmtree(destination)
            os.replace(source, destination)
    has_snapshot = target.is_file()
    snapshot_created = False
    if has_snapshot:
        snapshot = backup_root / "1" / "resource" / "tasks.json"
        snapshot.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(target, snapshot)
        snapshot_created = True
    replacement = target.with_name(f".{target.name}.install-{uuid.uuid4().hex}")
    try:
        shutil.copy2(staged.path, replacement)
        os.replace(replacement, target)
    except BaseException:
        replacement.unlink(missing_ok=True)
        if snapshot_created:
            snapshot.unlink(missing_ok=True)
        raise
    return AppliedResource(ResourceChannel.OTA, target, backup)


def rollback_ota(applied: AppliedResource) -> None:
    if applied.channel != ResourceChannel.OTA:
        raise ResourceUpdateError("cannot roll back a non-OTA resource as OTA")
    if applied.target.exists():
        applied.target.unlink()
    if applied.backup.is_file():
        temporary = applied.target.with_name(
            f".{applied.target.name}.rollback-{uuid.uuid4().hex}"
        )
        shutil.copy2(applied.backup, temporary)
        os.replace(temporary, applied.target)


def merge_repo_overlay(
    base_resource: str | Path,
    repo_resource: str | Path,
    backup_dir: str | Path,
) -> Path:
    """Merge the repo's whitelisted files into a full staged MaaCore resource.

    The entire current ``maa_path/resource`` is kept as one backup, then replaced
    by a same-filesystem staged copy with repository files overlaid. The ``repo``
    tree itself remains intact as the source for future core updates.
    """
    base, repo = _require_resource_roots(Path(base_resource), Path(repo_resource))
    backup = Path(backup_dir)
    if not base.is_dir():
        raise ResourceUpdateError(f"MaaCore resource directory does not exist: {base}")
    _sibling_backup_path(base, backup)
    staged = base.parent / f".{base.name}.overlay-{uuid.uuid4().hex}"
    if staged.parent.stat().st_dev != base.parent.stat().st_dev:
        raise ResourceUpdateError("resource staging and target must share a filesystem")
    try:
        _overlay_copy(base, repo, staged)
        _replace_directory_with_backup(staged, base, backup)
    except BaseException:
        shutil.rmtree(staged, ignore_errors=True)
        raise
    return backup


def apply_repo_overlay(
    staged: StagedResource,
    *,
    maa_path: str | Path,
    layers_root: str | Path,
) -> AppliedResource:
    """Install the raw repo layer, then transactionally merge it into MaaCore."""
    if staged.channel != ResourceChannel.REPO or not (staged.path / "resource").is_dir():
        raise ResourceUpdateError("invalid staged MaaResource overlay")
    target_resource = Path(maa_path) / "resource"
    repo_target = Path(layers_root) / "repo"
    repo_backup = repo_target.with_name("repo.backup")
    resource_backup = target_resource.with_name("resource.backup")
    repo_target.parent.mkdir(parents=True, exist_ok=True)
    if staged.path.parent.stat().st_dev != repo_target.parent.stat().st_dev:
        raise ResourceUpdateError("repo staging and layer target must share a filesystem")
    if repo_backup.exists():
        shutil.rmtree(repo_backup)
    moved_repo = False
    if repo_target.exists():
        os.replace(repo_target, repo_backup)
        moved_repo = True
    try:
        os.replace(staged.path, repo_target)
        merge_repo_overlay(target_resource, repo_target / "resource", resource_backup)
    except BaseException as update_error:
        try:
            if repo_target.exists():
                shutil.rmtree(repo_target)
            if moved_repo and repo_backup.exists():
                os.replace(repo_backup, repo_target)
        except BaseException as rollback_error:
            raise ResourceUpdateError("UPDATE_ROLLBACK_FAILED: repo layer rollback failed") from rollback_error
        raise update_error
    finally:
        if staged.cleanup_path is not None:
            shutil.rmtree(staged.cleanup_path, ignore_errors=True)
    return AppliedResource(
        ResourceChannel.REPO,
        target_resource,
        resource_backup,
        repo_target,
        repo_backup,
    )


def rollback_repo_overlay(applied: AppliedResource) -> None:
    """Restore both the previous complete MaaCore resource and raw repo layer."""
    if applied.channel != ResourceChannel.REPO or applied.repo_layer_target is None:
        raise ResourceUpdateError("cannot roll back a non-repo resource as repo")
    rollback_errors: list[BaseException] = []
    try:
        if not applied.backup.is_dir():
            raise ResourceUpdateError("previous complete MaaCore resource backup is missing")
        failed = applied.target.with_name(f".{applied.target.name}.failed-{uuid.uuid4().hex}")
        if applied.target.exists():
            os.replace(applied.target, failed)
        try:
            os.replace(applied.backup, applied.target)
        except BaseException:
            if failed.exists() and not applied.target.exists():
                os.replace(failed, applied.target)
            raise
        shutil.rmtree(failed, ignore_errors=True)
    except BaseException as exc:
        rollback_errors.append(exc)
    try:
        target = applied.repo_layer_target
        if applied.repo_layer_backup is not None and applied.repo_layer_backup.exists():
            if target.exists():
                shutil.rmtree(target)
            os.replace(applied.repo_layer_backup, target)
        elif target.exists():
            shutil.rmtree(target)
    except BaseException as exc:
        rollback_errors.append(exc)
    if rollback_errors:
        raise ResourceUpdateError("UPDATE_ROLLBACK_FAILED: repo rollback failed") from rollback_errors[0]


def reapply_overlay(
    maa_path: str | Path,
    repo_resource: str | Path,
) -> None:
    """Merge stored repo files into a newly installed core before it starts.

    Unlike a user initiated repo refresh, this uses a short-lived swap backup:
    the core updater already retains the previous complete MaaCore installation.
    """
    base, repo = _require_resource_roots(Path(maa_path) / "resource", Path(repo_resource))
    if not base.is_dir():
        raise ResourceUpdateError(f"new MaaCore resource directory does not exist: {base}")
    staged = base.parent / f".{base.name}.reapply-{uuid.uuid4().hex}"
    transient = base.with_name(f".{base.name}.reapply-backup-{uuid.uuid4().hex}")
    try:
        _overlay_copy(base, repo, staged)
        os.replace(base, transient)
        try:
            os.replace(staged, base)
        except BaseException:
            if transient.exists() and not base.exists():
                os.replace(transient, base)
            raise
        shutil.rmtree(transient)
    except BaseException:
        shutil.rmtree(staged, ignore_errors=True)
        raise


async def reload(callback: Callable[..., Any], *args: Any, **kwargs: Any) -> Any:
    """Invoke an injected resource reload or core restart lifecycle callback."""
    return await _call(callback, *args, **kwargs)


class ResourceUpdateWorkflow:
    """Paths and lifecycle adapters for UpdateService's resource update calls.

    ``maa_path`` is the configured MaaCore directory, not its resource child.
    ``restart_core`` is expected to run only while the pipeline is idle. The
    callback is called after a successful repo apply, and before returning an
    update result so the caller can persist the resulting versions.
    """

    def __init__(
        self,
        *,
        http_client: httpx.AsyncClient,
        maa_path: str | Path,
        layers_root: str | Path,
        temp_root: str | Path,
        restart_core: Callable[..., Any] | None = None,
        reload_resources: Callable[..., Any] | None = None,
        wait_for_idle: Callable[..., Any] | None = None,
        downloader: Callable[..., Any] = default_downloader,
        download_prefix: str | None = None,
        progress: Callable[[dict[str, Any]], Any] | None = None,
        disk_usage: Callable[[str | Path], Any] = shutil.disk_usage,
    ) -> None:
        self.http_client = http_client
        self.maa_path = Path(maa_path)
        self.layers_root = Path(layers_root)
        self.temp_root = Path(temp_root)
        self.restart_core = restart_core
        self.reload_resources = reload_resources
        self.wait_for_idle = wait_for_idle
        self.reload_pending = False
        self.downloader = downloader
        self.download_prefix = download_prefix
        self.progress = progress
        self.disk_usage = disk_usage

    async def check(
        self,
        channel: ResourceChannel | str,
        *,
        ota_metadata: Any = None,
        repo_version: str | None = None,
    ) -> dict[ResourceChannel, ResourceCheck]:
        return await check_resources(
            self.http_client,
            channel,
            ota_metadata=ota_metadata,
            repo_version=repo_version,
            progress=self.progress,
        )

    async def stage(self, check: ResourceCheck) -> StagedResource:
        if check.channel == ResourceChannel.OTA:
            return stage_ota(check, self.temp_root)
        if check.channel == ResourceChannel.REPO:
            return await stage_repo_overlay(
                self.http_client,
                check,
                self.temp_root,
                downloader=self.downloader,
                download_prefix=self.download_prefix,
                progress=self.progress,
                base_resource=self.maa_path / "resource",
                current_repo_resource=self.layers_root / "repo" / "resource",
                disk_usage=self.disk_usage,
            )
        raise ResourceUpdateError(f"unsupported resource channel: {check.channel}")

    async def restage_cached(self, previous: StagedResource) -> StagedResource:
        """Re-extract the already validated repo ZIP for manual retry."""
        if (
            previous.channel is not ResourceChannel.REPO
            or previous.archive_path is None
            or not previous.archive_path.is_file()
            or previous.version is None
        ):
            raise ResourceUpdateError("no validated MaaResource archive is available for retry")
        check = ResourceCheck(
            channel=ResourceChannel.REPO,
            changed=True,
            version=previous.version,
        )
        return await stage_repo_overlay(
            self.http_client,
            check,
            self.temp_root,
            downloader=self.downloader,
            download_prefix=self.download_prefix,
            cache_only=True,
            progress=self.progress,
            base_resource=self.maa_path / "resource",
            current_repo_resource=self.layers_root / "repo" / "resource",
            disk_usage=self.disk_usage,
        )

    def apply(self, staged: StagedResource) -> AppliedResource:
        if staged.channel == ResourceChannel.OTA:
            return apply_ota(staged, self.layers_root)
        if staged.channel == ResourceChannel.REPO:
            return apply_repo_overlay(
                staged,
                maa_path=self.maa_path,
                layers_root=self.layers_root,
            )
        raise ResourceUpdateError(f"unsupported resource channel: {staged.channel}")

    def rollback(self, applied: AppliedResource) -> None:
        if applied.channel == ResourceChannel.OTA:
            rollback_ota(applied)
        elif applied.channel == ResourceChannel.REPO:
            rollback_repo_overlay(applied)
        else:
            raise ResourceUpdateError(f"unsupported resource channel: {applied.channel}")

    def reapply_overlay(self, maa_path: str | Path | None = None) -> None:
        """CoreUpdateWorkflow ``before_start(target_path)`` callback adapter."""
        core_path = Path(maa_path) if maa_path is not None else self.maa_path
        reapply_overlay(core_path, self.layers_root / "repo" / "resource")

    async def reload(self, *, restart: bool = False) -> Any:
        callback = self.restart_core if restart else self.reload_resources
        if callback is None:
            raise ResourceUpdateError(
                "repo update requires restart_core" if restart else "OTA update requires reload_resources"
            )
        return await reload(callback)

    async def install_all(
        self,
        staged_items: Sequence[StagedResource],
        *,
        reload_mode: str = "wait",
    ) -> dict[ResourceChannel, AppliedResource]:
        """Apply OTA then repo with exactly one reload/restart while idle."""
        if reload_mode not in {"wait", "force", "defer"}:
            raise ResourceUpdateError(f"unsupported resource reload mode: {reload_mode}")
        if reload_mode != "defer" and self.wait_for_idle is None:
            raise ResourceUpdateError("resource installation requires a pipeline-idle callback")
        staged_by_channel: dict[ResourceChannel, StagedResource] = {}
        for item in staged_items:
            if item.channel in staged_by_channel:
                raise ResourceUpdateError(f"duplicate staged resource channel: {item.channel}")
            staged_by_channel[item.channel] = item
        if not staged_by_channel:
            return {}
        ordered = [
            staged_by_channel[channel]
            for channel in (ResourceChannel.OTA, ResourceChannel.REPO)
            if channel in staged_by_channel
        ]
        if reload_mode != "defer":
            await _call(self.wait_for_idle)
        applied: dict[ResourceChannel, AppliedResource] = {}
        try:
            for staged in ordered:
                applied[staged.channel] = self.apply(staged)
        except BaseException:
            for item in reversed(ordered):
                installed = applied.get(item.channel)
                if installed is not None:
                    self.rollback(installed)
            raise

        if reload_mode == "defer":
            self.reload_pending = True
            return applied

        restart = ResourceChannel.REPO in applied
        try:
            await self.reload(restart=restart)
        except BaseException as load_error:
            try:
                for item in reversed(ordered):
                    installed = applied.get(item.channel)
                    if installed is not None:
                        self.rollback(installed)
                # Re-run the same single lifecycle operation against restored
                # files so the core cannot retain a failed resource view.
                await self.reload(restart=restart)
            except BaseException as rollback_error:
                raise ResourceUpdateError(
                    "UPDATE_ROLLBACK_FAILED: resource disk/runtime restoration failed"
                ) from rollback_error
            self.reload_pending = False
            raise ResourceUpdateError("RESOURCE_LOAD_FAILED: resource reload failed") from load_error
        self.reload_pending = False
        return applied

    async def install(
        self, staged: StagedResource, *, reload_mode: str = "wait"
    ) -> AppliedResource:
        """Single-channel convenience wrapper over :meth:`install_all`."""
        applied = await self.install_all([staged], reload_mode=reload_mode)
        return applied[staged.channel]
