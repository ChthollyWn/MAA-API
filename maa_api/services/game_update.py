"""Offline-testable game APK discovery, validation, download, and install flow."""

from __future__ import annotations

import asyncio
import hashlib
import inspect
import json
import os
import re
import shutil
import zipfile
from collections.abc import Callable, Mapping
from dataclasses import dataclass, field
from datetime import datetime, timezone
from email.utils import parsedate_to_datetime
from pathlib import Path
from typing import Any
from urllib.parse import unquote, urlparse

import httpx

from maa_api.util import downloader

OFFICIAL_CHANNEL = "Official"
BILIBILI_CHANNEL = "Bilibili"
OFFICIAL_PACKAGE = "com.hypergryph.arknights"
BILIBILI_PACKAGE = "com.hypergryph.arknights.bilibili"
DEFAULT_PACKAGE_NAMES = {
    OFFICIAL_CHANNEL: OFFICIAL_PACKAGE,
    BILIBILI_CHANNEL: BILIBILI_PACKAGE,
}
BILIBILI_INFO_URL = (
    "https://line1-h5-pc-api.biligame.com/game/detail/gameinfo"
    "?game_base_id=101772"
)
OFFICIAL_DOWNLOAD_URL = "https://ak.hypergryph.com/downloads/android_lastest"
INSTALL_TIMEOUT_SECONDS = 20 * 60

BILI_APK_VER_RE = re.compile(r"mrfz_(?P<ver>\d+(?:\.\d+)+)_(?P<date>\d{8})_")
CONTENT_RANGE_RE = re.compile(r"^bytes\s+(\d+)-(\d+)/(\d+|\*)$", re.IGNORECASE)
INSTALL_FAILURE_RE = re.compile(r"Failure\s*\[([^\]]+)\]", re.IGNORECASE)
INSTALLED_RE = re.compile(r"versionName=(.*)")
UPDATED_RE = re.compile(r"lastUpdateTime=(.*)")

INSTALL_ERROR_MESSAGES = {
    "INSTALL_FAILED_INSUFFICIENT_STORAGE": "设备存储空间不足",
    "INSTALL_FAILED_UPDATE_INCOMPATIBLE": "已安装应用的签名不一致，需要人工确认后卸载再安装",
    "INSTALL_FAILED_VERSION_DOWNGRADE": "目标版本低于已安装版本，未自动降级或卸载",
    "INSTALL_FAILED_INVALID_APK": "APK 文件损坏，请重新下载",
    "INSTALL_PARSE_FAILED_NO_CERTIFICATES": "APK 未签名或签名损坏",
}


class GameUpdateError(RuntimeError):
    """A game update failure with a stable machine-readable code."""

    def __init__(self, code: str, message: str, *, details: Mapping[str, Any] | None = None):
        super().__init__(message)
        self.code = code
        self.details = dict(details or {})


@dataclass(frozen=True)
class BilibiliManifest:
    package_name: str
    package_version: str
    version_name: str | None
    can_compare_version: bool
    size: int | None
    md5: str | None
    download_url: str
    backup_url: str | None


@dataclass(frozen=True)
class OfficialRangeMetadata:
    size: int | None
    etag: str | None
    last_modified: str | None


@dataclass(frozen=True)
class InstalledPackage:
    package_name: str
    installed: bool
    version_name: str | None = None
    last_update_time: str | None = None


@dataclass(frozen=True)
class ApkValidation:
    size: int
    md5_matches: bool | None = None
    warnings: tuple[str, ...] = ()


@dataclass(frozen=True)
class GameUpdateInfo:
    channel: str
    package_name: str
    installed: InstalledPackage
    latest_version: str | None
    latest_label: str
    can_compare_version: bool
    remote_size: int | None = None
    remote_updated_at: str | None = None
    update_may_be_available: bool | None = None
    comparison_note: str | None = None


def load_package_names(config_path: str | Path | None = None) -> dict[str, str]:
    """Load MaaCore's package map when present, falling back to the CN packages."""
    names = dict(DEFAULT_PACKAGE_NAMES)
    path = Path(config_path) if config_path is not None else (
        Path(__file__).resolve().parents[2] / "resource" / "config.json"
    )
    try:
        raw = json.loads(path.read_text(encoding="utf-8"))
        configured = raw.get("packageName", {}) if isinstance(raw, dict) else {}
        for channel in DEFAULT_PACKAGE_NAMES:
            value = configured.get(channel) if isinstance(configured, dict) else None
            if isinstance(value, str) and value.strip():
                names[channel] = value.strip()
    except (OSError, ValueError, TypeError):
        pass
    return names


def package_name_for_channel(
    channel: str, package_names: Mapping[str, str] | None = None
) -> str:
    names = dict(DEFAULT_PACKAGE_NAMES)
    if package_names is None:
        names.update(load_package_names())
    else:
        names.update(package_names)
    try:
        return names[channel]
    except KeyError as exc:
        raise ValueError(f"游戏更新不支持渠道：{channel}") from exc


def _valid_http_url(value: Any) -> str | None:
    if not isinstance(value, str) or not value.strip():
        return None
    url = value.strip()
    return url if urlparse(url).scheme in {"http", "https"} else None


def parse_bilibili_manifest(payload: Mapping[str, Any]) -> BilibiliManifest:
    """Parse game detail JSON; summary is deliberately never used as a version."""
    try:
        code = int(payload.get("code", -1))
    except (TypeError, ValueError):
        code = -1
    data = payload.get("data")
    if code != 0 or not isinstance(data, Mapping):
        raise GameUpdateError("GAME_MANIFEST_INVALID", "B 服版本接口返回无效数据")

    package_name = str(data.get("android_pkg_name") or BILIBILI_PACKAGE)
    if package_name != BILIBILI_PACKAGE:
        raise GameUpdateError(
            "GAME_MANIFEST_INVALID",
            "B 服接口返回的包名与预期不符",
            details={"package_name": package_name},
        )
    download_url = _valid_http_url(data.get("android_download_link"))
    if download_url is None:
        raise GameUpdateError("GAME_MANIFEST_INVALID", "B 服接口缺少有效 APK 下载地址")
    backup_url = _valid_http_url(data.get("android_download_link2"))

    raw_size = data.get("android_pkg_size")
    try:
        size = int(raw_size) if raw_size is not None else None
        if size is not None and size <= 0:
            size = None
    except (TypeError, ValueError):
        size = None

    raw_md5 = data.get("android_sign")
    md5 = str(raw_md5).strip().lower() if raw_md5 is not None else None
    if not md5 or not re.fullmatch(r"[0-9a-f]{32}", md5):
        md5 = None

    match = BILI_APK_VER_RE.search(unquote(urlparse(download_url).path))
    if match:
        version_name = match.group("ver")
        can_compare = True
    else:
        # android_pkg_ver is Bilibili's package sequence, not versionName.
        version_name = str(data.get("android_pkg_ver")) if data.get("android_pkg_ver") is not None else None
        can_compare = False
    return BilibiliManifest(
        package_name=package_name,
        package_version=str(data.get("android_pkg_ver", "")),
        version_name=version_name,
        can_compare_version=can_compare,
        size=size,
        md5=md5,
        download_url=download_url,
        backup_url=backup_url,
    )


def parse_official_range_metadata(headers: Mapping[str, Any]) -> OfficialRangeMetadata:
    """Read package length and cache hints from a Range probe response."""
    normalized = {str(k).lower(): str(v).strip() for k, v in headers.items()}
    size: int | None = None
    content_range = normalized.get("content-range", "")
    match = CONTENT_RANGE_RE.fullmatch(content_range)
    if match and match.group(3) != "*":
        start, end, total = int(match.group(1)), int(match.group(2)), int(match.group(3))
        if start == 0 and end >= start and end < total:
            size = total
    return OfficialRangeMetadata(
        size=size,
        etag=normalized.get("etag"),
        last_modified=normalized.get("last-modified"),
    )


def parse_dumpsys_package(output: str, package_name: str) -> InstalledPackage:
    """Parse the documented dumpsys package fields without guessing install state."""
    package_line = re.compile(r"^\s*Package\s+\[([^\]]+)\](?:\s|$)", re.MULTILINE)
    matches = list(package_line.finditer(output))
    target = next((i for i, match in enumerate(matches) if match.group(1) == package_name), None)
    if target is None:
        return InstalledPackage(package_name=package_name, installed=False)
    start = matches[target].start()
    end = matches[target + 1].start() if target + 1 < len(matches) else len(output)
    package_output = output[start:end]
    version_match = INSTALLED_RE.search(package_output)
    update_match = UPDATED_RE.search(package_output)
    return InstalledPackage(
        package_name=package_name,
        installed=True,
        version_name=version_match.group(1).strip() if version_match else None,
        last_update_time=update_match.group(1).strip() if update_match else None,
    )


def _quantity_to_bytes(value: str, *, default_multiplier: int) -> int | None:
    match = re.fullmatch(r"\s*(\d+(?:\.\d+)?)\s*([KMGT]?i?B|[KMGT])?\s*", value, re.IGNORECASE)
    if match is None:
        return None
    amount = float(match.group(1))
    unit = (match.group(2) or "").upper()
    multiplier = {
        "": default_multiplier,
        "B": 1,
        "K": 1024,
        "M": 1024**2,
        "G": 1024**3,
        "T": 1024**4,
        "KB": 1000,
        "KIB": 1024,
        "MB": 1000**2,
        "MIB": 1024**2,
        "GB": 1000**3,
        "GIB": 1024**3,
        "TB": 1000**4,
        "TIB": 1024**4,
    }.get(unit)
    return int(amount * multiplier) if multiplier is not None else None


def parse_device_available_bytes(output: str) -> int | None:
    """Parse the Available/Avail column from ``adb shell df /data`` output."""
    lines = [line.split() for line in output.splitlines() if line.split()]
    for index, header in enumerate(lines[:-1]):
        lowered = [column.lower() for column in header]
        available_index = next(
            (i for i, column in enumerate(lowered) if column in {"available", "avail"}),
            None,
        )
        if available_index is None:
            continue
        for row in lines[index + 1 :]:
            if len(row) <= available_index:
                continue
            unit_multiplier = 1024 if any(
                "1k-block" in column or "1024-block" in column for column in lowered
            ) else 1
            return _quantity_to_bytes(row[available_index], default_multiplier=unit_multiplier)
    return None


def map_install_error(output: str) -> dict[str, str] | None:
    """Map adb install output to a localized message while retaining raw output."""
    match = INSTALL_FAILURE_RE.search(output)
    if match is None:
        return None
    code = match.group(1).strip().split(":", 1)[0]
    return {
        "code": code,
        "message": INSTALL_ERROR_MESSAGES.get(code, f"APK 安装失败：{code}"),
        "output": output,
    }


def validate_apk(
    path: str | Path,
    *,
    expected_size: int | None = None,
    expected_md5: str | None = None,
) -> ApkValidation:
    """Check size and ZIP/manifest integrity; Bilibili MD5 mismatch is warning-only."""
    apk_path = Path(path)
    if not apk_path.is_file():
        raise GameUpdateError("GAME_APK_INVALID", "APK 文件不存在")
    actual_size = apk_path.stat().st_size
    if expected_size is not None and actual_size != expected_size:
        raise GameUpdateError(
            "GAME_APK_SIZE_MISMATCH",
            "APK 文件大小与版本信息不一致",
            details={"expected_size": expected_size, "actual_size": actual_size},
        )
    try:
        with zipfile.ZipFile(apk_path) as archive:
            if "AndroidManifest.xml" not in archive.namelist():
                raise GameUpdateError(
                    "GAME_APK_INVALID", "APK 缺少 AndroidManifest.xml"
                )
            corrupt_member = archive.testzip()
            if corrupt_member is not None:
                raise GameUpdateError(
                    "GAME_APK_INVALID",
                    "APK ZIP 结构校验失败",
                    details={"member": corrupt_member},
                )
    except (OSError, zipfile.BadZipFile, zipfile.LargeZipFile) as exc:
        raise GameUpdateError("GAME_APK_INVALID", "APK 不是有效的 ZIP 文件") from exc

    md5_matches: bool | None = None
    warnings: list[str] = []
    if expected_md5:
        digest = hashlib.md5()
        with apk_path.open("rb") as source:
            for chunk in iter(lambda: source.read(1024 * 1024), b""):
                digest.update(chunk)
        md5_matches = digest.hexdigest().lower() == expected_md5.lower()
        if not md5_matches:
            warnings.append("B 服 MD5 与 android_sign 不匹配；按策略仅警告，不阻断安装")
    return ApkValidation(actual_size, md5_matches, tuple(warnings))


def _parse_time(value: str | None) -> datetime | None:
    if not value:
        return None
    try:
        parsed = datetime.fromisoformat(value.strip().replace("Z", "+00:00"))
    except ValueError:
        try:
            parsed = parsedate_to_datetime(value)
        except (TypeError, ValueError, OverflowError):
            return None
    if parsed.tzinfo is None:
        return parsed.replace(tzinfo=timezone.utc)
    return parsed.astimezone(timezone.utc)


def official_update_heuristic(
    installed_at: str | None, remote_last_modified: str | None
) -> bool | None:
    """Heuristic only: a newer remote APK timestamp may indicate an update."""
    local, remote = _parse_time(installed_at), _parse_time(remote_last_modified)
    if local is None or remote is None:
        return None
    return remote > local


class GameUpdateService:
    """Game updater whose HTTP, filesystem, and device boundaries are injectable."""

    def __init__(
        self,
        device_manager: Any,
        *,
        installer: Callable[..., Any] | None = None,
        http_client: httpx.AsyncClient | None = None,
        transport: httpx.AsyncBaseTransport | None = None,
        download_file: Callable[..., Any] | None = None,
        disk_usage: Callable[[str | Path], Any] = shutil.disk_usage,
        package_names: Mapping[str, str] | None = None,
        install_timeout: float = INSTALL_TIMEOUT_SECONDS,
        install_heartbeat_interval: float = 30.0,
    ) -> None:
        if http_client is not None and transport is not None:
            raise ValueError("pass either http_client or transport, not both")
        self.device_manager = device_manager
        # The caller must inject a safe install path; adbutils' convenience
        # install() may uninstall automatically after some failure codes.
        self._safe_installer = installer
        self._client = http_client or httpx.AsyncClient(
            transport=transport, follow_redirects=True, timeout=30.0
        )
        self._owns_client = http_client is None
        self._download_file = download_file or downloader.download
        self._disk_usage = disk_usage
        self.package_names = dict(DEFAULT_PACKAGE_NAMES)
        if package_names is None:
            self.package_names.update(load_package_names())
        else:
            self.package_names.update(package_names)
        self.install_timeout = float(install_timeout)
        self.install_heartbeat_interval = max(float(install_heartbeat_interval), 0.01)

    async def aclose(self) -> None:
        if self._owns_client:
            await self._client.aclose()

    def package_name(self, channel: str) -> str:
        return package_name_for_channel(channel, self.package_names)

    async def _installed(self, channel: str) -> InstalledPackage:
        package = self.package_name(channel)
        await self._ensure_available(timeout=30.0)
        output = await self._shell(f"dumpsys package {package}")
        return parse_dumpsys_package(output, package)

    async def inspect(self, channel: str) -> GameUpdateInfo:
        """Fetch channel metadata and combine it with the installed package state."""
        installed = await self._installed(channel)
        if channel == BILIBILI_CHANNEL:
            response = await self._client.get(BILIBILI_INFO_URL)
            response.raise_for_status()
            manifest = parse_bilibili_manifest(response.json())
            return GameUpdateInfo(
                channel=channel,
                package_name=manifest.package_name,
                installed=installed,
                latest_version=manifest.version_name,
                latest_label=(
                    manifest.version_name
                    if manifest.can_compare_version
                    else f"B 服包序号 {manifest.package_version}（无法与已安装版本直接比较）"
                ),
                can_compare_version=manifest.can_compare_version,
                remote_size=manifest.size,
                comparison_note=None if manifest.can_compare_version else "无法与已安装版本直接比较",
            )
        if channel == OFFICIAL_CHANNEL:
            metadata = await self._official_metadata()
            possible = official_update_heuristic(
                installed.last_update_time, metadata.last_modified
            )
            return GameUpdateInfo(
                channel=channel,
                package_name=installed.package_name,
                installed=installed,
                latest_version=None,
                latest_label="官服未提供版本接口",
                can_compare_version=False,
                remote_size=metadata.size,
                remote_updated_at=metadata.last_modified,
                update_may_be_available=possible,
                comparison_note="按远端包更新时间与本地安装时间启发式判断；并非精确版本比较",
            )
        raise ValueError(f"游戏更新不支持渠道：{channel}")

    async def _official_metadata(self) -> OfficialRangeMetadata:
        async with self._client.stream(
            "GET", OFFICIAL_DOWNLOAD_URL, headers={"Range": "bytes=0-0"}
        ) as response:
            if response.status_code != httpx.codes.PARTIAL_CONTENT:
                raise GameUpdateError(
                    "GAME_VERSION_PROBE_FAILED",
                    "官服 Range 元信息探测未返回 206",
                    details={"status_code": response.status_code},
                )
            return parse_official_range_metadata(response.headers)

    def _preflight_disk(self, destination: Path, expected_size: int | None) -> None:
        if expected_size is None:
            return
        usage = self._disk_usage(destination.parent)
        required = int(expected_size * 1.2)
        if usage.free < required:
            raise GameUpdateError(
                "UPDATE_DISK_INSUFFICIENT",
                "下载目标磁盘空间不足",
                details={"required_bytes": required, "free_bytes": usage.free},
            )

    async def download_latest(
        self,
        channel: str,
        destination: str | Path,
        *,
        on_progress: Callable[[int, int], Any] | None = None,
    ) -> ApkValidation:
        """Download and validate the selected channel's latest APK."""
        target = Path(destination)
        expected_size: int | None
        expected_md5: str | None = None
        if channel == BILIBILI_CHANNEL:
            response = await self._client.get(BILIBILI_INFO_URL)
            response.raise_for_status()
            manifest = parse_bilibili_manifest(response.json())
            url, expected_size, expected_md5 = (
                manifest.download_url,
                manifest.size,
                manifest.md5,
            )
        elif channel == OFFICIAL_CHANNEL:
            metadata = await self._official_metadata()
            url, expected_size = OFFICIAL_DOWNLOAD_URL, metadata.size
        else:
            raise ValueError(f"游戏更新不支持渠道：{channel}")

        self._preflight_disk(target, expected_size)
        urls = [url]
        if channel == BILIBILI_CHANNEL and manifest.backup_url and manifest.backup_url != url:
            urls.append(manifest.backup_url)

        transfer_errors: list[str] = []
        validation: ApkValidation | None = None
        for index, attempt_url in enumerate(urls):
            # Keep resume data separate per origin. Combining byte ranges from
            # different CDNs can produce a corrupt APK even when both serve the
            # same release.
            attempt_target = (
                target
                if index == 0
                else target.with_name(f"{target.name}.backup")
            )
            try:
                result = self._download_file(
                    attempt_url,
                    attempt_target,
                    expected_size=expected_size,
                    on_progress=on_progress,
                    client=self._client,
                )
                if inspect.isawaitable(result):
                    result = await result
            except Exception as exc:
                transfer_errors.append(f"{attempt_url}: {exc}")
                continue

            downloaded_path = Path(result) if result is not None else attempt_target
            try:
                validation = validate_apk(
                    downloaded_path,
                    expected_size=expected_size,
                    expected_md5=expected_md5,
                )
            except GameUpdateError:
                # A completed but invalid APK is a validation failure, not a
                # transfer failure; do not silently switch sources. Remove the
                # completed cache so a later explicit retry can fetch it again.
                downloaded_path.unlink(missing_ok=True)
                raise
            if downloaded_path != target:
                os.replace(downloaded_path, target)
            break
        else:
            raise GameUpdateError(
                "GAME_DOWNLOAD_FAILED",
                "APK 下载失败",
                details={"errors": transfer_errors, "urls": urls},
            )

        assert validation is not None
        if expected_size is None:
            validation = ApkValidation(
                validation.size,
                validation.md5_matches,
                (*validation.warnings, "未取得远端 APK 大小，已跳过下载前空间预检"),
            )
        return validation

    async def install(
        self,
        channel: str,
        apk_path: str | Path,
        *,
        expected_size: int | None = None,
        expected_md5: str | None = None,
        on_progress: Callable[[str], Any] | None = None,
    ) -> InstalledPackage:
        """Force-stop only the selected package, install without uninstall, then verify."""
        package = self.package_name(channel)
        if self._safe_installer is None:
            raise GameUpdateError(
                "SAFE_INSTALLER_UNAVAILABLE",
                "未注入禁止自动卸载的安全 APK 安装器",
            )
        validate_apk(apk_path, expected_size=expected_size, expected_md5=expected_md5)
        await self._ensure_available(timeout=30.0)
        apk_size = Path(apk_path).stat().st_size
        df_output = await self._shell("df /data")
        available = parse_device_available_bytes(df_output)
        if available is None:
            raise GameUpdateError(
                "DEVICE_SPACE_UNKNOWN",
                "无法读取设备 /data 可用空间，已停止安装",
                details={"output": df_output},
            )
        required = apk_size * 2
        if available < required:
            raise GameUpdateError(
                "INSTALL_DEVICE_SPACE_INSUFFICIENT",
                "设备存储空间不足",
                details={"required_bytes": required, "available_bytes": available},
            )
        await self._force_stop(package)
        heartbeat: asyncio.Task[None] | None = None
        if on_progress is not None:
            await self._call_install_progress(on_progress)
            heartbeat = asyncio.create_task(self._emit_install_progress(on_progress))
        try:
            await asyncio.wait_for(
                self._install_apk(Path(apk_path)), timeout=self.install_timeout
            )
        except asyncio.TimeoutError as exc:
            raise GameUpdateError(
                "GAME_INSTALL_TIMEOUT",
                f"APK 安装超过 {self.install_timeout:g} 秒",
                details={"timeout_seconds": self.install_timeout},
            ) from exc
        except Exception as exc:
            exception_details = getattr(exc, "details", None)
            raw_output = (
                exception_details.get("output")
                if isinstance(exception_details, Mapping)
                else None
            )
            output = str(raw_output or exc)
            mapped = map_install_error(output)
            if mapped:
                raise GameUpdateError(
                    mapped["code"], mapped["message"], details={"output": mapped["output"]}
                ) from exc
            raise GameUpdateError(
                "GAME_INSTALL_FAILED", "APK 安装失败", details={"output": output}
            ) from exc
        finally:
            if heartbeat is not None:
                heartbeat.cancel()
                try:
                    await heartbeat
                except asyncio.CancelledError:
                    pass

        await self._ensure_available(timeout=30.0)
        installed = await self._read_installed_without_reconnect(package)
        if not installed.installed:
            raise GameUpdateError(
                "GAME_INSTALL_VERIFY_FAILED",
                "安装命令成功，但重新检查时未发现目标应用",
                details={"package_name": package},
            )
        return installed

    async def _ensure_available(self, *, timeout: float) -> None:
        ensure = getattr(self.device_manager, "ensure_available", None)
        if ensure is None:
            return
        result = ensure(timeout=timeout)
        if inspect.isawaitable(result):
            result = await result
        if result is False:
            raise GameUpdateError("DEVICE_UNAVAILABLE", "ADB 设备未连接")

    async def _shell(self, command: str) -> str:
        shell = getattr(self.device_manager, "shell", None)
        if shell is not None:
            result = shell(command)
            return str(await result if inspect.isawaitable(result) else result)
        # Current DeviceManager exposes shell on its injected backend, but not as
        # a public wrapper. Keep compatibility while still using that manager.
        backend = getattr(self.device_manager, "_backend", None)
        address = getattr(self.device_manager, "address", None)
        backend_shell = getattr(backend, "shell", None)
        if backend_shell is not None and address:
            return str(await asyncio.to_thread(backend_shell, address, command))
        raise GameUpdateError("DEVICE_SHELL_UNAVAILABLE", "DeviceManager 未提供 shell 操作")

    async def _force_stop(self, package: str) -> None:
        stop = getattr(self.device_manager, "force_stop", None)
        if stop is None:
            raise GameUpdateError("DEVICE_OPERATION_UNAVAILABLE", "DeviceManager 未提供停止应用操作")
        result = stop(package)
        if inspect.isawaitable(result):
            await result

    async def _install_apk(self, path: Path) -> Any:
        install = self._safe_installer
        if install is None:
            raise GameUpdateError("SAFE_INSTALLER_UNAVAILABLE", "未注入安全 APK 安装器")
        result = install(path, timeout=self.install_timeout, flags=["-r", "-d"])
        return await result if inspect.isawaitable(result) else result

    async def _emit_install_progress(self, callback: Callable[[str], Any]) -> None:
        while True:
            await asyncio.sleep(self.install_heartbeat_interval)
            await self._call_install_progress(callback)

    @staticmethod
    async def _call_install_progress(callback: Callable[[str], Any]) -> None:
        try:
            result = callback("applying")
            if inspect.isawaitable(result):
                await result
        except Exception:
            # A progress sink must not interrupt the device installation.
            pass

    async def _read_installed_without_reconnect(self, package: str) -> InstalledPackage:
        output = await self._shell(f"dumpsys package {package}")
        return parse_dumpsys_package(output, package)


__all__ = [
    "BILIBILI_CHANNEL",
    "BILIBILI_INFO_URL",
    "BILIBILI_PACKAGE",
    "BILI_APK_VER_RE",
    "DEFAULT_PACKAGE_NAMES",
    "GameUpdateError",
    "GameUpdateInfo",
    "GameUpdateService",
    "InstalledPackage",
    "INSTALL_TIMEOUT_SECONDS",
    "OFFICIAL_CHANNEL",
    "OFFICIAL_DOWNLOAD_URL",
    "OFFICIAL_PACKAGE",
    "OfficialRangeMetadata",
    "ApkValidation",
    "BilibiliManifest",
    "load_package_names",
    "map_install_error",
    "official_update_heuristic",
    "package_name_for_channel",
    "parse_bilibili_manifest",
    "parse_device_available_bytes",
    "parse_dumpsys_package",
    "parse_official_range_metadata",
    "validate_apk",
]
