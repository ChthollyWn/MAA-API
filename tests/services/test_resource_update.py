from __future__ import annotations

import io
import asyncio
import json
import shutil
import zipfile
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import httpx
import pytest

from maa_api.domain.enums import ResourceChannel
from maa_api.services.resource_update import (
    MIRROR_VERSION_URL,
    OTA_URL,
    REPO_ARCHIVE_URL,
    REPO_VERSION_URL,
    AppliedResource,
    ResourceCheck,
    StagedResource,
    ResourceUpdateError,
    ResourceUpdateWorkflow,
    apply_ota,
    apply_repo_overlay,
    check_ota,
    check_repo_disk_space,
    check_repo,
    check_resources,
    merge_repo_overlay,
    reapply_overlay,
    rollback_ota,
    rollback_repo_overlay,
    stage_ota,
    stage_repo_overlay,
)


def _repo_archive(
    *, version: str = "2026-09-14 04:36:14.000", bad_path: str | None = None
) -> bytes:
    output = io.BytesIO()
    with zipfile.ZipFile(output, "w", compression=zipfile.ZIP_DEFLATED) as archive:
        archive.writestr("MaaResource-main/", "")
        archive.writestr("MaaResource-main/cache/.gitignore", "ignored")
        archive.writestr("MaaResource-main/resource/", "")
        archive.writestr(
            "MaaResource-main/resource/version.json",
            json.dumps({"last_updated": version}),
        )
        archive.writestr("MaaResource-main/resource/stages.json", '{"new":"stage"}')
        archive.writestr("MaaResource-main/resource/template/items/new.png", b"image")
        if bad_path:
            archive.writestr(f"MaaResource-main/resource/{bad_path}", b"unsafe")
    return output.getvalue()


def _mock_client(routes: dict[str, Any]) -> httpx.AsyncClient:
    def respond(request: httpx.Request) -> httpx.Response:
        result = routes.get(str(request.url))
        if callable(result):
            return result(request)
        if isinstance(result, httpx.Response):
            return result
        if result is None:
            return httpx.Response(404)
        if isinstance(result, bytes):
            return httpx.Response(200, content=result)
        return httpx.Response(200, json=result)

    return httpx.AsyncClient(transport=httpx.MockTransport(respond))


def _write_base_resource(path: Path) -> None:
    (path / "tasks").mkdir(parents=True)
    (path / "template" / "Award").mkdir(parents=True)
    (path / "tasks" / "base.json").write_text("{}", encoding="utf-8")
    (path / "template" / "Award" / "keep.png").write_bytes(b"base-template")
    (path / "stages.json").write_text('{"old":"stage"}', encoding="utf-8")
    (path / "config.json").write_text("{}", encoding="utf-8")
    (path / "version.json").write_text('{"last_updated":"core"}', encoding="utf-8")


def test_ota_conditional_request_sha_and_304() -> None:
    async def scenario() -> None:
        body = b'{"SR-test":{"type":" copilot"}}'
        sent: list[httpx.Request] = []

        def respond(request: httpx.Request) -> httpx.Response:
            sent.append(request)
            return httpx.Response(
                200,
                content=body,
                headers={"ETag": '"new"', "Last-Modified": "yesterday"},
            )

        async with httpx.AsyncClient(transport=httpx.MockTransport(respond)) as client:
            first = await check_ota(client, metadata={"etag": '"old"', "last_modified": "before"})
            second = await check_ota(client, metadata={"checksum": first.checksum})
        assert sent[0].headers["If-None-Match"] == '"old"'
        assert sent[0].headers["If-Modified-Since"] == "before"
        assert first.changed is True
        assert second.changed is False  # body equality wins over an ETag change
        assert first.etag == '"new"'

        async with httpx.AsyncClient(
            transport=httpx.MockTransport(lambda request: httpx.Response(304))
        ) as client:
            unchanged = await check_ota(client, metadata={"etag": '"same"'})
        assert unchanged.changed is False
        assert unchanged.etag == '"same"'

    asyncio.run(scenario())


def test_ota_rejects_invalid_json_without_writing() -> None:
    async def scenario() -> None:
        async with httpx.AsyncClient(
            transport=httpx.MockTransport(lambda request: httpx.Response(200, content=b"not json"))
        ) as client:
            with pytest.raises(ResourceUpdateError, match="无效 JSON"):
                await check_ota(client)

    asyncio.run(scenario())


def test_repo_prefers_mirror_and_uses_db_version() -> None:
    async def scenario() -> None:
        routes = {
            MIRROR_VERSION_URL: {
                "code": 0,
                "data": {"version_name": "v2", "release_note": "event"},
            },
            REPO_VERSION_URL: {"last_updated": "raw"},
        }
        async with _mock_client(routes) as client:
            current = await check_repo(client, local_version="v2")
            newer = await check_repo(client, local_version="v1")
        assert current.changed is False
        assert newer.changed is True
        assert newer.version == "v2"
        assert newer.source == "mirrorchyan"
        assert newer.release_note == "event"

    asyncio.run(scenario())


def test_repo_falls_back_to_raw_and_all_keeps_optional_failure() -> None:
    async def scenario() -> None:
        routes = {
            MIRROR_VERSION_URL: httpx.Response(503),
            REPO_VERSION_URL: {"last_updated": "raw-version"},
            OTA_URL: {"tasks": []},
        }
        async with _mock_client(routes) as client:
            fallback = await check_repo(client, local_version=None)
        assert fallback.source == "raw"
        assert fallback.changed is True

        async with _mock_client({OTA_URL: {"tasks": []}}) as client:
            results = await check_resources(
                client, ResourceChannel.ALL, ota_metadata=None, repo_version=None
            )
        assert results[ResourceChannel.OTA].changed is True
        assert results[ResourceChannel.REPO].error
        with pytest.raises(ResourceUpdateError, match="UPDATE_MANIFEST_UNAVAILABLE"):
            async with _mock_client({}) as client:
                await check_resources(client, ResourceChannel.REPO, repo_version=None)

    asyncio.run(scenario())


def test_ota_stage_apply_and_rollback_are_atomic(tmp_path: Path) -> None:
    layers = tmp_path / "resource" / "maa-layers"
    target = layers / "cache" / "resource" / "tasks.json"
    target.parent.mkdir(parents=True)
    target.write_text('{"old":true}', encoding="utf-8")
    staged = stage_ota(
        ResourceCheck(
            ResourceChannel.OTA,
            changed=True,
            body=b'{"new":true}',
            checksum="checksum",
            etag="etag",
        ),
        tmp_path / "resource" / "temp",
    )
    applied = apply_ota(staged, layers)
    assert json.loads(target.read_text(encoding="utf-8")) == {"new": True}
    assert applied.backup.read_text(encoding="utf-8") == '{"old":true}'
    rollback_ota(applied)
    assert target.read_text(encoding="utf-8") == '{"old":true}'


def test_ota_snapshots_retain_only_the_last_five_cache_trees(tmp_path: Path) -> None:
    layers = tmp_path / "resource" / "maa-layers"
    target = layers / "cache" / "resource" / "tasks.json"
    target.parent.mkdir(parents=True)
    for index in range(7):
        target.write_text(json.dumps({"version": index}), encoding="utf-8")
        staged = stage_ota(
            ResourceCheck(
                ResourceChannel.OTA,
                changed=True,
                body=json.dumps({"version": index + 1}).encode(),
            ),
            tmp_path / "resource" / "temp",
        )
        apply_ota(staged, layers)

    backup_root = layers / "cache.backup"
    assert sorted(item.name for item in backup_root.iterdir()) == ["1", "2", "3", "4", "5"]
    assert json.loads((backup_root / "1/resource/tasks.json").read_text()) == {"version": 6}
    assert json.loads((backup_root / "5/resource/tasks.json").read_text()) == {"version": 2}


def test_install_all_applies_both_channels_then_restarts_once(tmp_path: Path) -> None:
    async def scenario() -> None:
        maa_path = tmp_path / "lib" / "maa" / "Darwin"
        base = maa_path / "resource"
        _write_base_resource(base)
        layers = tmp_path / "resource" / "maa-layers"
        calls: list[str] = []
        ota = stage_ota(
            ResourceCheck(ResourceChannel.OTA, changed=True, body=b'{"tasks":[]}'),
            tmp_path / "resource" / "temp",
        )
        repo_root = tmp_path / "resource" / "temp" / "staged-repo"
        repo_resource = repo_root / "resource"
        repo_resource.mkdir(parents=True)
        (repo_resource / "version.json").write_text(
            '{"last_updated":"repo-new"}', encoding="utf-8"
        )
        (repo_resource / "stages.json").write_text('{"new":true}', encoding="utf-8")
        repo = StagedResource(ResourceChannel.REPO, repo_root, version="repo-new")
        async with _mock_client({}) as client:
            workflow = ResourceUpdateWorkflow(
                http_client=client,
                maa_path=maa_path,
                layers_root=layers,
                temp_root=tmp_path / "temp",
                wait_for_idle=lambda: calls.append("idle"),
                reload_resources=lambda: calls.append("reload"),
                restart_core=lambda: calls.append("restart"),
            )
            applied = await workflow.install_all([ota, repo])
        assert set(applied) == {ResourceChannel.OTA, ResourceChannel.REPO}
        assert calls == ["idle", "restart"]
        assert json.loads((base / "stages.json").read_text()) == {"new": True}
        assert json.loads(
            (layers / "cache" / "resource" / "tasks.json").read_text()
        ) == {"tasks": []}

    asyncio.run(scenario())


def test_deferred_resource_install_changes_disk_without_runtime_reload(tmp_path: Path) -> None:
    async def scenario() -> None:
        maa_path = tmp_path / "lib" / "maa" / "Darwin"
        base = maa_path / "resource"
        _write_base_resource(base)
        layers = tmp_path / "resource" / "maa-layers"
        calls: list[str] = []
        ota = stage_ota(
            ResourceCheck(ResourceChannel.OTA, changed=True, body=b'{"tasks":[]}'),
            tmp_path / "resource" / "temp",
        )
        async with _mock_client({}) as client:
            workflow = ResourceUpdateWorkflow(
                http_client=client,
                maa_path=maa_path,
                layers_root=layers,
                temp_root=tmp_path / "temp",
                reload_resources=lambda: calls.append("reload"),
                restart_core=lambda: calls.append("restart"),
                wait_for_idle=lambda: calls.append("idle"),
            )
            result = await workflow.install_all([ota], reload_mode="defer")
        assert result[ResourceChannel.OTA].target.is_file()
        assert workflow.reload_pending is True
        assert calls == []

    asyncio.run(scenario())


def test_repo_disk_preflight_scales_with_full_base_and_repo_tree(tmp_path: Path) -> None:
    base = tmp_path / "core" / "Darwin" / "resource"
    _write_base_resource(base)
    repo = tmp_path / "resource" / "maa-layers" / "repo" / "resource"
    repo.mkdir(parents=True)
    (repo / "version.json").write_bytes(b"v" * 40)
    usage = lambda path: SimpleNamespace(free=10**12)
    small = check_repo_disk_space(
        base,
        tmp_path / "resource" / "temp",
        current_repo_resource=repo,
        disk_usage=usage,
        archive_estimate=100,
        expanded_estimate=0,
        growth_factor=1.25,
    )
    with (base / "template" / "Award" / "larger.png").open("wb") as stream:
        stream.write(b"x" * 1000)
    with (repo / "large-map.json").open("wb") as stream:
        stream.write(b"y" * 5000)
    larger = check_repo_disk_space(
        base,
        tmp_path / "resource" / "temp",
        current_repo_resource=repo,
        disk_usage=usage,
        archive_estimate=100,
        expanded_estimate=0,
        growth_factor=1.25,
    )
    assert larger - small == 7500  # 1000-byte core delta + 5000-byte repo estimate, ×1.25


def test_repo_disk_preflight_low_space_has_no_staging_mutation(tmp_path: Path) -> None:
    async def scenario() -> None:
        base = tmp_path / "core" / "Darwin" / "resource"
        _write_base_resource(base)
        staging = tmp_path / "resource" / "temp"
        called = False

        async def forbidden_download(*args: Any, **kwargs: Any) -> None:
            nonlocal called
            called = True
            raise AssertionError("download must not start after a failed disk preflight")

        async with _mock_client({}) as client:
            with pytest.raises(ResourceUpdateError, match="UPDATE_DISK_INSUFFICIENT"):
                await stage_repo_overlay(
                    client,
                    ResourceCheck(ResourceChannel.REPO, changed=True),
                    staging,
                    downloader=forbidden_download,
                    base_resource=base,
                    disk_usage=lambda path: SimpleNamespace(free=0),
                )
        assert not called
        assert not staging.exists()

    asyncio.run(scenario())


def test_repo_staging_disables_resume_and_extracts_only_resource(tmp_path: Path) -> None:
    async def scenario() -> None:
        payload = _repo_archive()
        urls: list[str] = []

        def respond(request: httpx.Request) -> httpx.Response:
            urls.append(str(request.url))
            return httpx.Response(200, content=payload)

        async with httpx.AsyncClient(transport=httpx.MockTransport(respond)) as client:
            staged = await stage_repo_overlay(
                client,
                ResourceCheck(ResourceChannel.REPO, changed=True, version="queried"),
                tmp_path / "resource" / "temp",
            )
        assert urls == [REPO_ARCHIVE_URL]
        assert staged.version == "2026-09-14 04:36:14.000"
        assert (staged.path / "resource" / "stages.json").is_file()
        assert not (staged.path / "cache").exists()

    asyncio.run(scenario())


def test_repo_archive_download_tries_user_prefix_then_original_url(tmp_path: Path) -> None:
    async def scenario() -> None:
        payload = _repo_archive()
        urls: list[str] = []

        def respond(request: httpx.Request) -> httpx.Response:
            urls.append(str(request.url))
            if str(request.url).startswith("https://proxy.example/"):
                return httpx.Response(503, text="proxy unavailable")
            return httpx.Response(200, content=payload)

        async with httpx.AsyncClient(transport=httpx.MockTransport(respond)) as client:
            staged = await stage_repo_overlay(
                client,
                ResourceCheck(ResourceChannel.REPO, changed=True),
                tmp_path / "resource" / "temp",
                download_prefix="https://proxy.example/",
            )
        assert urls == [f"https://proxy.example/{REPO_ARCHIVE_URL}", REPO_ARCHIVE_URL]
        assert staged.version == "2026-09-14 04:36:14.000"

    asyncio.run(scenario())


def test_repo_install_retry_reuses_validated_seven_day_archive(tmp_path: Path) -> None:
    async def scenario() -> None:
        payload = _repo_archive(version="repo-version")
        calls: list[str] = []

        def respond(request: httpx.Request) -> httpx.Response:
            calls.append(str(request.url))
            return httpx.Response(200, content=payload)

        root = tmp_path / "resource" / "temp"
        check = ResourceCheck(
            ResourceChannel.REPO,
            changed=True,
            version="repo-version",
        )
        async with httpx.AsyncClient(transport=httpx.MockTransport(respond)) as client:
            first = await stage_repo_overlay(client, check, root)
            assert first.archive_path and first.archive_path.is_file()
            shutil.rmtree(first.cleanup_path)

            async def forbidden_download(*args: Any, **kwargs: Any) -> None:
                raise AssertionError("retry should reuse the validated archive")

            retry = await stage_repo_overlay(
                client,
                check,
                root,
                downloader=forbidden_download,
                cache_only=True,
            )
        assert calls == [REPO_ARCHIVE_URL]
        assert retry.version == "repo-version"
        assert retry.archive_path == first.archive_path
        assert (retry.path / "resource" / "stages.json").is_file()

    asyncio.run(scenario())


@pytest.mark.parametrize("bad_path", ["../../escape.json", "other.json"])
def test_repo_staging_rejects_traversal_and_non_whitelisted_paths(
    tmp_path: Path, bad_path: str
) -> None:
    async def scenario() -> None:
        payload = _repo_archive(bad_path=bad_path)
        async with httpx.AsyncClient(
            transport=httpx.MockTransport(lambda request: httpx.Response(200, content=payload))
        ) as client:
            with pytest.raises(ResourceUpdateError):
                await stage_repo_overlay(
                    client,
                    ResourceCheck(ResourceChannel.REPO, changed=True),
                    tmp_path / "temp",
                )
        assert not list((tmp_path / "temp").glob("repo-*/extracted/repo"))

    asyncio.run(scenario())


def test_repo_overlay_preserves_complete_resource_and_rollback_restores_it(
    tmp_path: Path,
) -> None:
    maa_path = tmp_path / "lib" / "maa" / "Darwin"
    base = maa_path / "resource"
    _write_base_resource(base)
    repo_root = tmp_path / "resource" / "maa-layers" / "repo" / "resource"
    (repo_root / "template" / "items").mkdir(parents=True)
    (repo_root / "version.json").write_text(
        '{"last_updated":"repo-version"}', encoding="utf-8"
    )
    (repo_root / "stages.json").write_text('{"repo":"stage"}', encoding="utf-8")
    (repo_root / "template" / "items" / "new.png").write_bytes(b"repo-template")
    backup = base.with_name("resource.backup")

    merge_repo_overlay(base, repo_root, backup)
    assert (base / "tasks" / "base.json").is_file()
    assert (base / "template" / "Award" / "keep.png").read_bytes() == b"base-template"
    assert json.loads((base / "stages.json").read_text(encoding="utf-8")) == {"repo": "stage"}
    assert (base / "template" / "items" / "new.png").read_bytes() == b"repo-template"
    assert (backup / "tasks" / "base.json").is_file()  # backup is a complete old resource


def test_repo_apply_stores_raw_layer_and_reapply_overlay_is_core_before_start_callback(
    tmp_path: Path,
) -> None:
    maa_path = tmp_path / "lib" / "maa" / "Darwin"
    base = maa_path / "resource"
    _write_base_resource(base)
    layers = tmp_path / "resource" / "maa-layers"
    stage_root = tmp_path / "resource" / "temp" / "stage" / "repo"
    (stage_root / "resource").mkdir(parents=True)
    (stage_root / "resource" / "version.json").write_text(
        '{"last_updated":"repo-v2"}', encoding="utf-8"
    )
    (stage_root / "resource" / "stages.json").write_text('{"new":2}', encoding="utf-8")
    staged = StagedResource(ResourceChannel.REPO, stage_root)

    applied = apply_repo_overlay(staged, maa_path=maa_path, layers_root=layers)
    assert (layers / "repo" / "resource" / "stages.json").is_file()
    assert json.loads((base / "stages.json").read_text(encoding="utf-8")) == {"new": 2}
    assert applied.backup.is_dir()

    # Simulate a fresh core directory with no repo files and the M7-03 callback.
    fresh_maa_path = tmp_path / "new-core" / "Darwin"
    _write_base_resource(fresh_maa_path / "resource")
    reapply_overlay(fresh_maa_path, layers / "repo" / "resource")
    assert json.loads(
        (fresh_maa_path / "resource" / "stages.json").read_text(encoding="utf-8")
    ) == {"new": 2}
    assert (fresh_maa_path / "resource" / "tasks" / "base.json").is_file()
    assert not list(fresh_maa_path.glob(".resource.reapply-backup-*"))

    rollback_repo_overlay(applied)
    assert json.loads((base / "stages.json").read_text(encoding="utf-8")) == {"old": "stage"}
    assert not (layers / "repo").exists()


def test_overlay_rejects_wrong_roots_and_symlinked_repo_files(tmp_path: Path) -> None:
    base = tmp_path / "maa" / "resource"
    _write_base_resource(base)
    wrong_repo = tmp_path / "repo-resource"
    (wrong_repo / "resource").mkdir(parents=True)
    (wrong_repo / "resource" / "version.json").write_text(
        '{"last_updated":"v"}', encoding="utf-8"
    )
    with pytest.raises(ResourceUpdateError, match="overlay roots"):
        merge_repo_overlay(base, wrong_repo / "resource", base.with_name("resource.backup"))

    repo = tmp_path / "resource" / "maa-layers" / "repo" / "resource"
    repo.mkdir(parents=True)
    (repo / "version.json").write_text('{"last_updated":"v"}', encoding="utf-8")
    outside = tmp_path / "outside.json"
    outside.write_text("unsafe", encoding="utf-8")
    try:
        (repo / "stages.json").symlink_to(outside)
    except OSError:
        pytest.skip("symlinks are unavailable in this environment")
    with pytest.raises(ResourceUpdateError, match="unsafe MaaResource entry"):
        merge_repo_overlay(base, repo, base.with_name("resource.backup"))
    assert outside.read_text(encoding="utf-8") == "unsafe"


def test_workflow_exposes_m7_03_before_start_callback(tmp_path: Path) -> None:
    async def scenario() -> None:
        core_root = tmp_path / "core" / "Darwin"
        _write_base_resource(core_root / "resource")
        layers = tmp_path / "resource" / "maa-layers"
        repo_resource = layers / "repo" / "resource"
        repo_resource.mkdir(parents=True)
        (repo_resource / "version.json").write_text(
            '{"last_updated":"v"}', encoding="utf-8"
        )
        (repo_resource / "stages.json").write_text('{"new":true}', encoding="utf-8")
        events: list[str] = []
        async with _mock_client({}) as client:
            workflow = ResourceUpdateWorkflow(
                http_client=client,
                maa_path=core_root,
                layers_root=layers,
                temp_root=tmp_path / "temp",
                restart_core=lambda: events.append("restart"),
                reload_resources=lambda: events.append("reload"),
            )
            workflow.reapply_overlay(core_root)
            await workflow.reload(restart=True)
        assert json.loads((core_root / "resource" / "stages.json").read_text()) == {"new": True}
        assert events == ["restart"]

    asyncio.run(scenario())


def test_failed_repo_restart_rolls_back_disk_and_restarts_old_resource_view(
    tmp_path: Path,
) -> None:
    async def scenario() -> None:
        maa_path = tmp_path / "lib" / "maa" / "Darwin"
        base = maa_path / "resource"
        _write_base_resource(base)
        layers = tmp_path / "resource" / "maa-layers"
        old_repo = layers / "repo" / "resource"
        old_repo.mkdir(parents=True)
        (old_repo / "version.json").write_text(
            '{"last_updated":"old-repo"}', encoding="utf-8"
        )
        (old_repo / "stages.json").write_text('{"version":"old"}', encoding="utf-8")
        staged_root = tmp_path / "resource" / "temp" / "staged" / "repo"
        (staged_root / "resource").mkdir(parents=True)
        (staged_root / "resource" / "version.json").write_text(
            '{"last_updated":"new-repo"}', encoding="utf-8"
        )
        (staged_root / "resource" / "stages.json").write_text(
            '{"version":"new"}', encoding="utf-8"
        )
        calls: list[str] = []
        restart_count = 0

        async def restart() -> None:
            nonlocal restart_count
            restart_count += 1
            if restart_count == 1:
                calls.append("failed-new-start")
                raise RuntimeError("new resource restart failed")
            calls.append("restored-old-start")
            assert json.loads((base / "stages.json").read_text()) == {"old": "stage"}

        async with _mock_client({}) as client:
            workflow = ResourceUpdateWorkflow(
                http_client=client,
                maa_path=maa_path,
                layers_root=layers,
                temp_root=tmp_path / "resource" / "temp",
                restart_core=restart,
                wait_for_idle=lambda: calls.append("idle"),
            )
            with pytest.raises(ResourceUpdateError, match="RESOURCE_LOAD_FAILED"):
                await workflow.install(StagedResource(ResourceChannel.REPO, staged_root))

        assert calls == ["idle", "failed-new-start", "restored-old-start"]
        assert json.loads((base / "stages.json").read_text()) == {"old": "stage"}
        assert json.loads(
            (layers / "repo" / "resource" / "stages.json").read_text()
        ) == {"version": "old"}

    asyncio.run(scenario())
