"""ResourceService validates and rolls back custom resource mutations."""

from __future__ import annotations

import asyncio
import hashlib
import importlib
import importlib.util
import json
from pathlib import Path
import stat

import pytest

from maa_api.domain.errors import AppError
from maa_api.domain.enums import ResourceAssetKind
from maa_api.db.models import ResourceAsset


def _service_type():
    module_name = "maa_api.services.resource_service"
    assert importlib.util.find_spec(module_name) is not None
    module = importlib.import_module(module_name)
    assert hasattr(module, "ResourceService"), "ResourceService must be implemented"
    return module.ResourceService


def _copilot():
    return {
        "minimum_required": "v4.0.0",
        "stage_name": "1-7",
        "doc": {"title": "test operation", "details": "validated sample"},
        "opers": [{"name": "阿米娅", "skill": 1}],
        "actions": [
            {"type": "Deploy", "name": "阿米娅", "location": [3, 4], "direction": "Left"}
        ],
    }


def _insert_asset(factory, asset: ResourceAsset) -> None:
    async def scenario() -> None:
        async with factory() as session:
            session.add(asset)
            await session.commit()

    asyncio.run(scenario())


def test_upload_copilot_validates_structure_and_persists_search_metadata(
    resource_root, retention_session_factory
):
    Service = _service_type()
    service = Service(
        retention_session_factory,
        resource_root=resource_root,
    )

    saved = asyncio.run(
        service.upload_copilot(
            name="1-7 safe",
            description="stable",
            content=_copilot(),
        )
    )

    assert saved["kind"] == "copilot"
    assert saved["name"] == "1-7 safe"
    assert saved["meta"]["stage_name"] == "1-7"
    listed = asyncio.run(service.list_copilots(page=1, size=10, q="1-7"))
    assert listed["total"] == 1
    assert listed["items"][0]["id"] == saved["id"]

    with pytest.raises(AppError) as exc_info:
        asyncio.run(
            service.upload_copilot(
                name="invalid",
                description=None,
                content={"stage_name": "1-7", "opers": [], "actions": "bad"},
            )
        )
    assert exc_info.value.code == "COPILOT_JSON_INVALID"


def test_resolve_copilot_path_materializes_unique_private_inline_snapshots(
    resource_root, retention_session_factory
):
    service = _service_type()(
        retention_session_factory,
        resource_root=resource_root,
    )
    saved = asyncio.run(
        service.upload_copilot(name="inline", description=None, content=_copilot())
    )

    first_path = asyncio.run(service.resolve_copilot_path(saved["id"]))
    second_path = asyncio.run(service.resolve_copilot_path(saved["id"]))

    assert first_path.is_file()
    assert first_path.resolve().is_relative_to(Path(resource_root).resolve())
    assert first_path.relative_to(resource_root).parts[:3] == (
        "agent-assets",
        "copilot",
        "runs",
    )
    assert first_path.name == "operation.json"
    assert first_path.parent.name != second_path.parent.name
    assert stat.S_IMODE(first_path.parent.stat().st_mode) == 0o700
    assert json.loads(first_path.read_text(encoding="utf-8")) == _copilot()
    assert json.loads(second_path.read_text(encoding="utf-8")) == _copilot()

    asyncio.run(service.release_copilot_path(first_path))
    asyncio.run(service.release_copilot_path(second_path))

    assert not first_path.exists()
    assert not second_path.exists()


def test_resolve_copilot_path_returns_existing_external_asset(
    resource_root, retention_session_factory
):
    service = _service_type()(
        retention_session_factory,
        resource_root=resource_root,
    )
    content = _copilot() | {
        "doc": {"title": "large", "details": "x" * (70 * 1024)}
    }
    saved = asyncio.run(
        service.upload_copilot(name="external", description=None, content=content)
    )

    source_path = Path(resource_root) / "agent-assets" / "copilot" / f"{saved['id']}.json"
    path = asyncio.run(service.resolve_copilot_path(saved["id"]))

    assert path.is_file()
    assert path != source_path
    assert path.relative_to(resource_root).parts[:3] == (
        "agent-assets",
        "copilot",
        "runs",
    )
    assert stat.S_IMODE(path.parent.stat().st_mode) == 0o700
    assert json.loads(path.read_text(encoding="utf-8")) == content

    source_path.unlink()

    assert json.loads(path.read_text(encoding="utf-8")) == content
    asyncio.run(service.release_copilot_path(path))
    assert not path.exists()


def test_resolve_copilot_path_rejects_missing_and_non_copilot_assets(
    resource_root, retention_session_factory
):
    service = _service_type()(
        retention_session_factory,
        resource_root=resource_root,
    )
    _insert_asset(
        retention_session_factory,
        ResourceAsset(
            id="b" * 32,
            kind=ResourceAssetKind.CUSTOM_TASK,
            name="Custom_example",
            content={"algorithm": "OcrDetect"},
        ),
    )

    for asset_id in ("c" * 32, "b" * 32):
        with pytest.raises(AppError) as exc_info:
            asyncio.run(service.resolve_copilot_path(asset_id))
        assert exc_info.value.code == "RESOURCE_ASSET_NOT_FOUND"


def test_resolve_copilot_path_rejects_path_traversal_and_escaping_symlinks(
    resource_root, retention_session_factory, tmp_path
):
    service = _service_type()(
        retention_session_factory,
        resource_root=resource_root,
    )
    outside = tmp_path / "outside.json"
    outside.write_text(json.dumps(_copilot()), encoding="utf-8")
    _insert_asset(
        retention_session_factory,
        ResourceAsset(
            id="c" * 32,
            kind=ResourceAssetKind.COPILOT,
            name="traversal",
            content=None,
            path="../outside.json",
        ),
    )
    link = Path(resource_root) / "agent-assets" / "copilot" / f"{'d' * 32}.json"
    link.parent.mkdir(parents=True, exist_ok=True)
    link.symlink_to(outside)
    _insert_asset(
        retention_session_factory,
        ResourceAsset(
            id="d" * 32,
            kind=ResourceAssetKind.COPILOT,
            name="symlink",
            content=None,
            path=f"agent-assets/copilot/{'d' * 32}.json",
        ),
    )

    for asset_id in ("c" * 32, "d" * 32):
        with pytest.raises(AppError) as exc_info:
            asyncio.run(service.resolve_copilot_path(asset_id))
        assert exc_info.value.code == "RESOURCE_LOAD_FAILED"


def test_resolve_copilot_path_rejects_asset_id_path_injection(
    resource_root, retention_session_factory
):
    service = _service_type()(
        retention_session_factory,
        resource_root=resource_root,
    )
    content = _copilot()
    checksum = hashlib.sha256(
        json.dumps(
            content,
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
        ).encode("utf-8")
    ).hexdigest()
    _insert_asset(
        retention_session_factory,
        ResourceAsset(
            id="../escape",
            kind=ResourceAssetKind.COPILOT,
            name="path injection",
            content=content,
            checksum=checksum,
        ),
    )

    with pytest.raises(AppError) as exc_info:
        asyncio.run(service.resolve_copilot_path("../escape"))

    assert exc_info.value.code == "RESOURCE_ASSET_NOT_FOUND"
    assert not (Path(resource_root) / "agent-assets" / "escape.json").exists()


@pytest.mark.parametrize("component", ["resource_root", "agent-assets", "copilot", "runs"])
def test_resolve_inline_copilot_rejects_in_root_symlink_ancestors(
    resource_root, retention_session_factory, component
):
    Service = _service_type()
    service = Service(retention_session_factory, resource_root=resource_root)
    saved = asyncio.run(
        service.upload_copilot(name="inline", description=None, content=_copilot())
    )
    root = Path(resource_root)
    if component == "resource_root":
        alias_root = root.with_name(f"{root.name}-alias")
        alias_root.symlink_to(root, target_is_directory=True)
        service = Service(retention_session_factory, resource_root=alias_root)
        elsewhere = root / "agent-assets" / "copilot" / "runs"
    elif component == "agent-assets":
        alias = root / "agent-assets-alias"
        alias.mkdir()
        (root / "agent-assets").symlink_to(alias, target_is_directory=True)
        elsewhere = alias / "copilot" / "runs"
    elif component == "copilot":
        parent = root / "agent-assets"
        parent.mkdir()
        alias = parent / "copilot-alias"
        alias.mkdir()
        (parent / "copilot").symlink_to(alias, target_is_directory=True)
        elsewhere = alias / "runs"
    else:
        parent = root / "agent-assets" / "copilot"
        parent.mkdir(parents=True)
        alias = parent / "runs-alias"
        alias.mkdir()
        (parent / "runs").symlink_to(alias, target_is_directory=True)
        elsewhere = alias

    with pytest.raises(AppError) as exc_info:
        asyncio.run(service.resolve_copilot_path(saved["id"]))

    assert exc_info.value.code == "RESOURCE_LOAD_FAILED"
    assert not elsewhere.exists() or not list(elsewhere.glob("*/operation.json"))


@pytest.mark.parametrize("component", ["resource_root", "agent-assets", "copilot", "source"])
def test_resolve_external_copilot_rejects_in_root_symlink_components(
    resource_root, retention_session_factory, component
):
    Service = _service_type()
    service = Service(retention_session_factory, resource_root=resource_root)
    content = _copilot() | {
        "doc": {"title": "large", "details": "x" * (70 * 1024)}
    }
    saved = asyncio.run(
        service.upload_copilot(name="external", description=None, content=content)
    )
    root = Path(resource_root)
    source = root / "agent-assets" / "copilot" / f"{saved['id']}.json"
    if component == "resource_root":
        alias_root = root.with_name(f"{root.name}-alias")
        alias_root.symlink_to(root, target_is_directory=True)
        service = Service(retention_session_factory, resource_root=alias_root)
    elif component == "agent-assets":
        alias = root / "agent-assets-alias"
        (root / "agent-assets").rename(alias)
        (root / "agent-assets").symlink_to(alias, target_is_directory=True)
    elif component == "copilot":
        alias = root / "agent-assets" / "copilot-alias"
        (root / "agent-assets" / "copilot").rename(alias)
        (root / "agent-assets" / "copilot").symlink_to(alias, target_is_directory=True)
    else:
        alias = source.with_name("source-alias.json")
        source.rename(alias)
        source.symlink_to(alias)

    with pytest.raises(AppError) as exc_info:
        asyncio.run(service.resolve_copilot_path(saved["id"]))

    assert exc_info.value.code == "RESOURCE_LOAD_FAILED"
    assert not (root / "agent-assets" / "copilot" / "runs").exists()


def test_upload_copilot_accepts_sss_stage_and_strategy_schema(
    resource_root, retention_session_factory
):
    Service = _service_type()
    service = Service(retention_session_factory, resource_root=resource_root)
    content = {
        "type": "SSS",
        "stage_name": "Test Protocol",
        "minimum_required": "v5.2.1",
        "doc": {"title": "Test SSS", "details": "Validated nested strategies"},
        "buff": "Field Support Drone",
        "equipment": ["A", "B"],
        "strategy": "Core",
        "opers": [{"name": "Amiya", "skill": 2, "skill_usage": 1}],
        "tool_men": {"caster": 2},
        "drops": ["Caster"],
        "blacklist": ["Operator"],
        "stages": [
            {
                "stage_name": "Stage A",
                "strategies": [
                    {
                        "tool_men": {"caster": 1},
                        "location": [3, 4],
                        "direction": "Down",
                    }
                ],
                "actions": [{"type": "SpeedUp"}],
                "retry_times": 3,
            }
        ],
    }

    saved = asyncio.run(
        service.upload_copilot(name="sss", description=None, content=content)
    )

    assert saved["content"]["stages"][0]["strategies"][0]["tool_men"] == {
        "caster": 1
    }


@pytest.mark.parametrize(
    "invalid",
    [
        _copilot() | {"actions": [{"type": "Deploy", "name": "阿米娅", "location": [3], "direction": "Left"}]},
        _copilot() | {"opers": [{"name": "阿米娅", "skill": 9}]},
        _copilot() | {"groups": [{"name": "team", "opers": "not-an-array"}]},
        _copilot() | {"minimum_required": 4},
    ],
)
def test_upload_copilot_rejects_invalid_nested_schema(
    resource_root, retention_session_factory, invalid
):
    Service = _service_type()
    service = Service(retention_session_factory, resource_root=resource_root)

    with pytest.raises(AppError) as exc_info:
        asyncio.run(
            service.upload_copilot(
                name="bad nested data",
                description=None,
                content=invalid,
            )
        )

    assert exc_info.value.code == "COPILOT_JSON_INVALID"


def test_large_copilot_is_stored_as_a_resource_relative_file(
    resource_root, retention_session_factory
):
    Service = _service_type()
    service = Service(retention_session_factory, resource_root=resource_root)
    content = _copilot() | {
        "doc": {"title": "large operation", "details": "x" * (65 * 1024)}
    }

    saved = asyncio.run(
        service.upload_copilot(
            name="large",
            description=None,
            content=content,
        )
    )

    from maa_api.db.repositories.resource import ResourceAssetRepository
    from maa_api.domain.enums import ResourceAssetKind

    async def read():
        async with retention_session_factory() as session:
            return await ResourceAssetRepository(session).get(saved["id"])

    row = asyncio.run(read())
    assert row.content is None
    assert row.path.startswith("agent-assets/copilot/")
    assert (resource_root / row.path).is_file()
    assert saved["content"] == content


def test_custom_task_is_written_in_custom_layer_and_reloaded(
    resource_root, retention_session_factory
):
    Service = _service_type()
    reloaded = []

    async def reload_resources():
        reloaded.append(True)

    service = Service(
        retention_session_factory,
        resource_root=resource_root,
        reload_resources=reload_resources,
    )
    definition = {
        "action": "ClickSelf",
        "template": "Custom/Event.png",
        "roi": [0, 0, 100, 100],
        "method": "HSVCount",
        "colorScales": [[[0, 0, 0], [255, 255, 255]]],
        "next": ["Custom_Next"],
    }

    saved = asyncio.run(
        service.register_custom_task(
            name="Custom_EventPanel",
            description="event panel",
            content=definition,
        )
    )

    path = resource_root / "maa-layers" / "custom" / "resource" / "tasks.json"
    assert saved["name"] == "Custom_EventPanel"
    assert path.is_file()
    assert json.loads(path.read_text(encoding="utf-8")) == {
        "Custom_EventPanel": definition
    }
    assert reloaded == [True]


@pytest.mark.parametrize(
    "definition",
    [
        {"action": 7},
        {"action": "ClickSelf", "roi": [1, 2, "bad", 4]},
        {"action": "ClickSelf", "template": ["ok.png", 4]},
        {"action": "ClickSelf", "next": "not-an-array"},
        {"unrecognized": True},
    ],
)
def test_custom_task_rejects_wrong_core_field_shapes(
    resource_root, retention_session_factory, definition
):
    Service = _service_type()
    service = Service(
        retention_session_factory,
        resource_root=resource_root,
        reload_resources=lambda: None,
    )

    with pytest.raises(AppError) as exc_info:
        asyncio.run(
            service.register_custom_task(
                name="Custom_Bad",
                description=None,
                content=definition,
            )
        )

    assert exc_info.value.code == "CUSTOM_TASK_INVALID"


def test_custom_task_reload_failure_restores_database_and_previous_file(
    resource_root, retention_session_factory
):
    Service = _service_type()
    custom_dir = resource_root / "maa-layers" / "custom" / "resource"
    custom_dir.mkdir(parents=True)
    task_file = custom_dir / "tasks.json"
    original = b'{"Custom_Existing":{"action":"DoNothing"}}\n'
    task_file.write_bytes(original)
    attempts = 0

    async def reload_resources():
        nonlocal attempts
        attempts += 1
        if attempts == 1:
            raise RuntimeError("core rejected task resource")

    service = Service(
        retention_session_factory,
        resource_root=resource_root,
        reload_resources=reload_resources,
    )

    with pytest.raises(AppError) as exc_info:
        asyncio.run(
            service.register_custom_task(
                name="Custom_Broken",
                description=None,
                content={"action": "ClickSelf"},
            )
        )

    assert exc_info.value.code == "RESOURCE_LOAD_FAILED"
    assert attempts == 2
    assert task_file.read_bytes() == original
    from maa_api.db.repositories.resource import ResourceAssetRepository
    from maa_api.domain.enums import ResourceAssetKind

    async def list_rows():
        async with retention_session_factory() as session:
            return await ResourceAssetRepository(session).list_by_kind(
                ResourceAssetKind.CUSTOM_TASK
            )

    assert asyncio.run(list_rows()) == []


def test_concurrent_custom_task_success_survives_another_reload_rollback(
    resource_root, retention_session_factory
):
    Service = _service_type()
    first_reload = asyncio.Event()
    release_first_reload = asyncio.Event()
    calls = 0

    async def reload_resources():
        nonlocal calls
        calls += 1
        if calls == 1:
            first_reload.set()
            await release_first_reload.wait()
            raise RuntimeError("first task reload rejected")

    service = Service(
        retention_session_factory,
        resource_root=resource_root,
        reload_resources=reload_resources,
    )
    custom_file = resource_root / "maa-layers" / "custom" / "resource" / "tasks.json"

    async def scenario():
        first = asyncio.create_task(
            service.register_custom_task(
                name="Custom_First",
                description=None,
                content={"action": "ClickSelf"},
            )
        )
        await first_reload.wait()
        second = asyncio.create_task(
            service.register_custom_task(
                name="Custom_Second",
                description=None,
                content={"action": "DoNothing"},
            )
        )
        await asyncio.sleep(0)
        release_first_reload.set()
        first_result, second_result = await asyncio.gather(
            first, second, return_exceptions=True
        )
        return second_result, first_result

    second, first_error = asyncio.run(scenario())

    assert second["name"] == "Custom_Second"
    assert isinstance(first_error, AppError)
    assert first_error.code == "RESOURCE_LOAD_FAILED"
    assert json.loads(custom_file.read_text(encoding="utf-8")) == {
        "Custom_Second": {"action": "DoNothing"}
    }

    from maa_api.db.repositories.resource import ResourceAssetRepository
    from maa_api.domain.enums import ResourceAssetKind

    async def list_rows():
        async with retention_session_factory() as session:
            return await ResourceAssetRepository(session).list_by_kind(
                ResourceAssetKind.CUSTOM_TASK
            )

    rows = asyncio.run(list_rows())
    assert [row.name for row in rows] == ["Custom_Second"]


def test_infrast_plan_requires_a_plans_array_and_is_stored(
    resource_root, retention_session_factory
):
    Service = _service_type()
    service = Service(retention_session_factory, resource_root=resource_root)
    plan = {
        "author": "agent",
        "planTimes": "1班",
        "scheduleType": {"planTimes": 1, "trading": 1},
        "plans": [
            {
                "name": "白班",
                "Fiammetta": {"enable": True, "target": "", "order": "pre"},
                "drones": {"room": "trading", "index": 1, "enable": True, "order": "pre"},
                "period": [["08:00", "16:00"]],
                "rooms": {"trading": [], "manufacture": []},
            }
        ],
    }

    saved = asyncio.run(
        service.set_infrast_plan(name="custom", description=None, content=plan)
    )

    assert saved["kind"] == "infrast_plan"
    assert saved["meta"]["plan_count"] == 1
    assert saved["filename"].startswith("custom_infrast/")
    with pytest.raises(AppError) as exc_info:
        asyncio.run(
            service.set_infrast_plan(
                name="invalid", description=None, content={"plans": "bad"}
            )
        )
    assert exc_info.value.code == "INFRAST_PLAN_INVALID"


@pytest.mark.parametrize(
    "invalid",
    [
        {"plans": [{"name": "day", "rooms": {"unknown_room": []}}]},
        {"plans": [{"name": "day", "rooms": {"trading": [{"operators": "bad"}]}}]},
        {"plans": [{"name": "day", "Fiammetta": {"enable": "yes"}}]},
        {"plans": [{"name": "day", "drones": {"room": "unknown"}}]},
    ],
)
def test_infrast_plan_rejects_invalid_nested_room_and_operator_shapes(
    resource_root, retention_session_factory, invalid
):
    Service = _service_type()
    service = Service(retention_session_factory, resource_root=resource_root)

    with pytest.raises(AppError) as exc_info:
        asyncio.run(
            service.set_infrast_plan(
                name="invalid nested plan",
                description=None,
                content=invalid,
            )
        )

    assert exc_info.value.code == "INFRAST_PLAN_INVALID"
