from __future__ import annotations

import asyncio
import hashlib
import io
import json
import zipfile
from pathlib import Path
from types import SimpleNamespace

import httpx
import pytest

from maa_api.services.game_update import (
    BILIBILI_CHANNEL,
    BILIBILI_INFO_URL,
    BILIBILI_PACKAGE,
    GameUpdateError,
    GameUpdateService,
    OFFICIAL_CHANNEL,
    OFFICIAL_DOWNLOAD_URL,
    OFFICIAL_PACKAGE,
    load_package_names,
    map_install_error,
    official_update_heuristic,
    package_name_for_channel,
    parse_bilibili_manifest,
    parse_device_available_bytes,
    parse_dumpsys_package,
    parse_official_range_metadata,
    validate_apk,
)
from maa_api.domain.errors import AppError, ErrorCode


def synthetic_apk(*, manifest: bool = True) -> bytes:
    output = io.BytesIO()
    with zipfile.ZipFile(output, "w", zipfile.ZIP_DEFLATED) as archive:
        if manifest:
            archive.writestr("AndroidManifest.xml", b"synthetic manifest")
        archive.writestr("classes.dex", b"synthetic dex")
    return output.getvalue()


def bili_payload(
    apk: bytes,
    *,
    link: str = "https://pkg.biligame.com/games/mrfz_2.7.71_20260824_022945_16353.apk",
    sign: str | None = None,
) -> dict[str, object]:
    return {
        "code": 0,
        "data": {
            "android_pkg_name": BILIBILI_PACKAGE,
            "android_pkg_ver": "190",
            "android_pkg_size": len(apk),
            "android_sign": sign or hashlib.md5(apk).hexdigest(),
            "android_download_link": link,
            "android_download_link2": "https://pkgdl.biligame.net/backup.apk",
            "summary": "活动开启，不是版本号",
        },
    }


def test_package_names_load_from_config_and_fall_back(tmp_path: Path) -> None:
    config = tmp_path / "config.json"
    config.write_text(json.dumps({"packageName": {"Official": "custom.official"}}))
    assert load_package_names(config)[OFFICIAL_CHANNEL] == "custom.official"
    assert load_package_names(config)[BILIBILI_CHANNEL] == BILIBILI_PACKAGE
    assert package_name_for_channel(OFFICIAL_CHANNEL, {}) == OFFICIAL_PACKAGE


def test_bilibili_manifest_uses_filename_version_and_explicit_fallback() -> None:
    apk = synthetic_apk()
    manifest = parse_bilibili_manifest(bili_payload(apk))
    assert manifest.version_name == "2.7.71"
    assert manifest.can_compare_version is True
    assert manifest.package_version == "190"
    assert manifest.size == len(apk)
    assert manifest.md5 == hashlib.md5(apk).hexdigest()

    fallback = parse_bilibili_manifest(
        bili_payload(apk, link="https://pkg.biligame.com/games/latest.apk")
    )
    assert fallback.version_name == "190"
    assert fallback.can_compare_version is False
    assert "summary" not in fallback.__dict__


def test_bilibili_manifest_rejects_wrong_package_and_bad_response() -> None:
    payload = bili_payload(synthetic_apk())
    payload["data"]["android_pkg_name"] = "wrong.package"  # type: ignore[index]
    with pytest.raises(GameUpdateError, match="包名"):
        parse_bilibili_manifest(payload)
    with pytest.raises(GameUpdateError):
        parse_bilibili_manifest({"code": 1, "data": {}})


def test_official_range_metadata_and_update_heuristic() -> None:
    metadata = parse_official_range_metadata(
        {
            "Content-Range": "bytes 0-0/1882311496",
            "ETag": '"BE459408D7BBA73A9FDD08CE8BCCA119-400"',
            "Last-Modified": "Mon, 31 Aug 2026 04:36:15 GMT",
        }
    )
    assert metadata.size == 1_882_311_496
    assert metadata.etag and metadata.etag.endswith("-400\"")
    assert official_update_heuristic(
        "2026-08-25 10:33:12", metadata.last_modified
    ) is True
    assert official_update_heuristic("not a date", metadata.last_modified) is None
    assert parse_official_range_metadata({"Content-Range": "invalid"}).size is None


def test_dumpsys_parser_distinguishes_missing_package() -> None:
    output = """Packages:
  Package [com.other.game] (aaaa):
    versionName=0.1
    lastUpdateTime=2020-01-01 00:00:00
  Package [com.hypergryph.arknights] (abcdef):
    versionName=2.7.71
    lastUpdateTime=2026-08-25 10:33:12
  Package [com.last.game] (bbbb):
    versionName=9.9
    lastUpdateTime=2030-01-01 00:00:00
"""
    parsed = parse_dumpsys_package(output, OFFICIAL_PACKAGE)
    assert parsed.installed is True
    assert parsed.version_name == "2.7.71"
    assert parsed.last_update_time == "2026-08-25 10:33:12"
    assert parse_dumpsys_package(output, BILIBILI_PACKAGE).installed is False


def test_device_df_parser_reads_available_space_in_bytes() -> None:
    output = """Filesystem 1K-blocks Used Available Use% Mounted on
/dev/block/vda 100000 50000 50000 50% /data
"""
    assert parse_device_available_bytes(output) == 50_000 * 1024
    assert parse_device_available_bytes("Filesystem Size Used Avail Use% Mounted on\n/dev/x 1G 1M 900M 1% /data") == 900 * 1024**2
    assert parse_device_available_bytes("not df output") is None


def test_apk_validation_checks_size_zip_manifest_and_warning_only_md5(tmp_path: Path) -> None:
    apk = synthetic_apk()
    path = tmp_path / "sample.apk"
    path.write_bytes(apk)
    good = validate_apk(path, expected_size=len(apk), expected_md5=hashlib.md5(apk).hexdigest())
    assert good.md5_matches is True
    mismatch = validate_apk(path, expected_md5="0" * 32)
    assert mismatch.md5_matches is False
    assert mismatch.warnings

    with pytest.raises(GameUpdateError, match="大小"):
        validate_apk(path, expected_size=len(apk) + 1)
    path.write_bytes(synthetic_apk(manifest=False))
    with pytest.raises(GameUpdateError, match="AndroidManifest.xml"):
        validate_apk(path)
    path.write_bytes(b"not a zip")
    with pytest.raises(GameUpdateError, match="ZIP"):
        validate_apk(path)


def test_install_error_mapping_retains_unknown_code_and_output() -> None:
    mapped = map_install_error("Failure [INSTALL_FAILED_INSUFFICIENT_STORAGE: no space]")
    assert mapped == {
        "code": "INSTALL_FAILED_INSUFFICIENT_STORAGE",
        "message": "设备存储空间不足",
        "output": "Failure [INSTALL_FAILED_INSUFFICIENT_STORAGE: no space]",
    }
    unknown = map_install_error("Failure [INSTALL_FAILED_NEW_THING]")
    assert unknown is not None and unknown["message"].endswith("INSTALL_FAILED_NEW_THING")
    assert map_install_error("Success") is None


class FakeDeviceManager:
    def __init__(self, outputs: list[str] | None = None) -> None:
        self.outputs = list(outputs or [])
        self.calls: list[tuple[object, ...]] = []
        self.address = "127.0.0.1:5555"

    async def ensure_available(self, *, timeout: float) -> bool:
        self.calls.append(("ensure", timeout))
        return True

    async def shell(self, command: str) -> str:
        self.calls.append(("shell", command))
        if command == "df /data":
            return "Filesystem 1K-blocks Used Available Use% Mounted on\n/dev/block/vda 100000 50000 50000 50% /data"
        return self.outputs.pop(0) if self.outputs else ""

    async def force_stop(self, package: str) -> None:
        self.calls.append(("force_stop", package))

    async def install_apk(self, path: Path, **options: object) -> str:
        self.calls.append(("install", path, options))
        return "Success"


def safe_installer_for(device: FakeDeviceManager):
    async def install(
        path: Path,
        *,
        timeout: float,
        flags: list[str],
    ) -> str:
        device.calls.append(
            ("install", path, {"timeout": timeout, "flags": flags})
        )
        return "Success"

    return install


def test_inspect_bilibili_fallback_is_labeled_and_never_uses_summary() -> None:
    apk = synthetic_apk()

    async def run() -> None:
        def handler(request: httpx.Request) -> httpx.Response:
            assert request.url == httpx.URL(BILIBILI_INFO_URL)
            return httpx.Response(200, json=bili_payload(apk, link="https://pkg.biligame.com/latest.apk"))

        client = httpx.AsyncClient(transport=httpx.MockTransport(handler))
        device = FakeDeviceManager([f"Package [{BILIBILI_PACKAGE}]\nversionName=2.7.70"])
        service = GameUpdateService(device, http_client=client)
        try:
            info = await service.inspect(BILIBILI_CHANNEL)
            assert info.latest_version == "190"
            assert info.can_compare_version is False
            assert "无法与已安装版本直接比较" in info.latest_label
            assert "活动开启" not in info.latest_label
        finally:
            await client.aclose()

    asyncio.run(run())


def test_download_latest_uses_mock_http_and_disk_preflight(tmp_path: Path) -> None:
    apk = synthetic_apk()
    payload = bili_payload(apk)

    async def run() -> None:
        requested: list[str] = []

        def handler(request: httpx.Request) -> httpx.Response:
            requested.append(str(request.url))
            if request.url == httpx.URL(BILIBILI_INFO_URL):
                return httpx.Response(200, json=payload)
            return httpx.Response(200, content=apk)

        client = httpx.AsyncClient(transport=httpx.MockTransport(handler))
        service = GameUpdateService(
            FakeDeviceManager(),
            http_client=client,
            disk_usage=lambda _path: SimpleNamespace(free=len(apk) * 2),
        )
        try:
            result = await service.download_latest(BILIBILI_CHANNEL, tmp_path / "game.apk")
            assert result.md5_matches is True
            assert requested == [BILIBILI_INFO_URL, payload["data"]["android_download_link"]]  # type: ignore[index]
        finally:
            await client.aclose()

    asyncio.run(run())


def test_bilibili_download_falls_back_to_backup_only_on_transfer_failure(
    tmp_path: Path,
) -> None:
    apk = synthetic_apk()
    payload = bili_payload(apk)
    primary = payload["data"]["android_download_link"]  # type: ignore[index]
    backup = payload["data"]["android_download_link2"]  # type: ignore[index]

    async def run() -> None:
        attempted: list[str] = []

        def handler(request: httpx.Request) -> httpx.Response:
            if request.url == httpx.URL(BILIBILI_INFO_URL):
                return httpx.Response(200, json=payload)
            raise AssertionError("the injected downloader owns APK transfer")

        async def fake_download(
            url: str,
            dest: Path,
            **kwargs: object,
        ) -> Path:
            attempted.append(url)
            if url == primary:
                raise OSError("primary CDN unavailable")
            assert url == backup
            dest.write_bytes(apk)
            return dest

        client = httpx.AsyncClient(transport=httpx.MockTransport(handler))
        service = GameUpdateService(
            FakeDeviceManager(),
            http_client=client,
            download_file=fake_download,
            disk_usage=lambda _path: SimpleNamespace(free=len(apk) * 2),
        )
        try:
            target = tmp_path / "game.apk"
            result = await service.download_latest(BILIBILI_CHANNEL, target)
            assert result.md5_matches is True
            assert attempted == [primary, backup]
            assert target.read_bytes() == apk
        finally:
            await client.aclose()

    asyncio.run(run())


def test_bilibili_download_reports_both_transfer_failures(tmp_path: Path) -> None:
    apk = synthetic_apk()
    payload = bili_payload(apk)
    primary = payload["data"]["android_download_link"]  # type: ignore[index]
    backup = payload["data"]["android_download_link2"]  # type: ignore[index]

    async def run() -> None:
        attempted: list[str] = []

        def handler(request: httpx.Request) -> httpx.Response:
            return httpx.Response(200, json=payload)

        async def fake_download(url: str, dest: Path, **kwargs: object) -> Path:
            attempted.append(url)
            raise OSError(f"{url} unavailable")

        client = httpx.AsyncClient(transport=httpx.MockTransport(handler))
        service = GameUpdateService(
            FakeDeviceManager(),
            http_client=client,
            download_file=fake_download,
            disk_usage=lambda _path: SimpleNamespace(free=len(apk) * 2),
        )
        try:
            with pytest.raises(GameUpdateError) as error:
                await service.download_latest(BILIBILI_CHANNEL, tmp_path / "game.apk")
            assert error.value.code == "GAME_DOWNLOAD_FAILED"
            assert attempted == [primary, backup]
            assert len(error.value.details["errors"]) == 2
        finally:
            await client.aclose()

    asyncio.run(run())


def test_bilibili_validation_failure_does_not_try_backup(tmp_path: Path) -> None:
    apk = synthetic_apk()
    payload = bili_payload(apk)
    payload["data"]["android_pkg_size"] = None  # type: ignore[index]
    primary = payload["data"]["android_download_link"]  # type: ignore[index]

    async def run() -> None:
        attempted: list[str] = []

        def handler(request: httpx.Request) -> httpx.Response:
            return httpx.Response(200, json=payload)

        async def fake_download(url: str, dest: Path, **kwargs: object) -> Path:
            attempted.append(url)
            dest.write_bytes(synthetic_apk(manifest=False))
            return dest

        client = httpx.AsyncClient(transport=httpx.MockTransport(handler))
        service = GameUpdateService(
            FakeDeviceManager(),
            http_client=client,
            download_file=fake_download,
            disk_usage=lambda _path: SimpleNamespace(free=10_000),
        )
        try:
            with pytest.raises(GameUpdateError, match="AndroidManifest.xml"):
                await service.download_latest(BILIBILI_CHANNEL, tmp_path / "game.apk")
            assert attempted == [primary]
            assert not (tmp_path / "game.apk").exists()
        finally:
            await client.aclose()

    asyncio.run(run())


def test_bilibili_download_reuses_completed_validated_destination(tmp_path: Path) -> None:
    apk = synthetic_apk()
    payload = bili_payload(apk)
    cdn_url = payload["data"]["android_download_link"]  # type: ignore[index]

    async def run() -> None:
        requests: list[str] = []

        def handler(request: httpx.Request) -> httpx.Response:
            requests.append(str(request.url))
            if request.url == httpx.URL(BILIBILI_INFO_URL):
                return httpx.Response(200, json=payload)
            assert request.url == httpx.URL(cdn_url)
            return httpx.Response(200, content=apk)

        client = httpx.AsyncClient(transport=httpx.MockTransport(handler))
        service = GameUpdateService(
            FakeDeviceManager(),
            http_client=client,
            disk_usage=lambda _path: SimpleNamespace(free=len(apk) * 2),
        )
        target = tmp_path / "reusable.apk"
        try:
            first = await service.download_latest(BILIBILI_CHANNEL, target)
            second = await service.download_latest(BILIBILI_CHANNEL, target)
            assert first.md5_matches is second.md5_matches is True
            assert requests.count(BILIBILI_INFO_URL) == 2
            assert requests.count(cdn_url) == 1
        finally:
            await client.aclose()

    asyncio.run(run())


def test_official_inspect_uses_range_probe_and_heuristic_label() -> None:
    async def run() -> None:
        calls: list[tuple[str, str | None]] = []

        def handler(request: httpx.Request) -> httpx.Response:
            calls.append((str(request.url), request.headers.get("range")))
            if request.url == httpx.URL(OFFICIAL_DOWNLOAD_URL):
                return httpx.Response(
                    206,
                    headers={
                        "Content-Range": "bytes 0-0/100",
                        "Last-Modified": "Mon, 31 Aug 2026 04:36:15 GMT",
                    },
                    content=b"x",
                )
            raise AssertionError(f"unexpected request: {request.url}")

        client = httpx.AsyncClient(transport=httpx.MockTransport(handler))
        device = FakeDeviceManager(
            [f"Package [{OFFICIAL_PACKAGE}]\nversionName=2.7.71\nlastUpdateTime=2026-08-25 10:33:12"]
        )
        service = GameUpdateService(device, http_client=client)
        try:
            info = await service.inspect(OFFICIAL_CHANNEL)
            assert info.latest_version is None
            assert "未提供版本接口" in info.latest_label
            assert info.remote_size == 100
            assert info.update_may_be_available is True
            assert "启发式" in (info.comparison_note or "")
            assert calls == [(OFFICIAL_DOWNLOAD_URL, "bytes=0-0")]
        finally:
            await client.aclose()

    asyncio.run(run())


def test_install_stops_and_installs_selected_channel_then_rechecks(tmp_path: Path) -> None:
    apk_path = tmp_path / "game.apk"
    apk_path.write_bytes(synthetic_apk())

    async def run() -> None:
        device = FakeDeviceManager(
            [
                f"Package [{BILIBILI_PACKAGE}]\nversionName=2.7.71\nlastUpdateTime=2026-08-25 10:33:12",
            ]
        )
        service = GameUpdateService(
            device, installer=safe_installer_for(device), install_timeout=10
        )
        try:
            progress: list[str] = []
            installed = await service.install(
                BILIBILI_CHANNEL, apk_path, on_progress=progress.append
            )
            assert installed.installed is True
            assert installed.version_name == "2.7.71"
            assert progress == ["applying"]
            assert [call[0] for call in device.calls] == [
                "ensure",
                "shell",
                "force_stop",
                "install",
                "ensure",
                "shell",
            ]
            assert device.calls[2] == ("force_stop", BILIBILI_PACKAGE)
            install_call = device.calls[3]
            assert install_call[2] == {
                "timeout": 10,
                "flags": ["-r", "-d"],
            }
            assert install_call[1] == apk_path
            assert all(call[0] != "install_apk" for call in device.calls)
        finally:
            await service.aclose()

    asyncio.run(run())


def test_install_failure_is_mapped_without_uninstall(tmp_path: Path) -> None:
    apk_path = tmp_path / "game.apk"
    apk_path.write_bytes(synthetic_apk())

    async def run() -> None:
        device = FakeDeviceManager()

        async def fail_install(
            path: Path,
            *,
            timeout: float,
            flags: list[str],
        ) -> str:
            device.calls.append(("safe_install", path, timeout, flags))
            raise RuntimeError("Failure [INSTALL_FAILED_UPDATE_INCOMPATIBLE]")

        service = GameUpdateService(device, installer=fail_install)
        try:
            with pytest.raises(GameUpdateError) as error:
                await service.install(OFFICIAL_CHANNEL, apk_path)
            assert error.value.code == "INSTALL_FAILED_UPDATE_INCOMPATIBLE"
            assert "人工确认" in str(error.value)
            assert not any("uninstall" in str(call).lower() for call in device.calls)
        finally:
            await service.aclose()

    asyncio.run(run())


def test_install_failure_maps_underlying_device_output(tmp_path: Path) -> None:
    apk_path = tmp_path / "game.apk"
    apk_path.write_bytes(synthetic_apk())

    async def run() -> None:
        device = FakeDeviceManager()

        async def fail_install(path: Path, *, timeout: float, flags: list[str]) -> str:
            raise AppError(
                ErrorCode.ADB_COMMAND_FAILED,
                "安全安装 APK 失败",
                {"output": "Failure [INSTALL_FAILED_UPDATE_INCOMPATIBLE]"},
            )

        service = GameUpdateService(device, installer=fail_install)
        try:
            with pytest.raises(GameUpdateError) as error:
                await service.install(OFFICIAL_CHANNEL, apk_path)
            assert error.value.code == "INSTALL_FAILED_UPDATE_INCOMPATIBLE"
            assert "Failure [INSTALL_FAILED_UPDATE_INCOMPATIBLE]" in error.value.details["output"]
        finally:
            await service.aclose()

    asyncio.run(run())


def test_install_timeout_is_enforced(tmp_path: Path) -> None:
    apk_path = tmp_path / "game.apk"
    apk_path.write_bytes(synthetic_apk())

    async def run() -> None:
        device = FakeDeviceManager()

        async def slow_install(
            path: Path,
            *,
            timeout: float,
            flags: list[str],
        ) -> str:
            await asyncio.sleep(0.05)
            return "Success"

        service = GameUpdateService(
            device, installer=slow_install, install_timeout=0.001
        )
        try:
            with pytest.raises(GameUpdateError) as error:
                await service.install(OFFICIAL_CHANNEL, apk_path)
            assert error.value.code == "GAME_INSTALL_TIMEOUT"
        finally:
            await service.aclose()

    asyncio.run(run())


def test_install_refuses_to_run_without_explicit_safe_installer(tmp_path: Path) -> None:
    apk_path = tmp_path / "game.apk"
    apk_path.write_bytes(synthetic_apk())

    async def run() -> None:
        device = FakeDeviceManager()
        service = GameUpdateService(device)
        try:
            with pytest.raises(GameUpdateError) as error:
                await service.install(OFFICIAL_CHANNEL, apk_path)
            assert error.value.code == "SAFE_INSTALLER_UNAVAILABLE"
            assert device.calls == []
        finally:
            await service.aclose()

    asyncio.run(run())
