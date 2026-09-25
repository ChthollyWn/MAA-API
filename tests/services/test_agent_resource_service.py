"""ResourceService validates and rolls back custom resource mutations."""

from __future__ import annotations

import asyncio
import importlib
import importlib.util
import json

import pytest

from maa_api.domain.errors import AppError


def _service_type():
    module_name = "maa_api.services.resource_service"
    assert importlib.util.find_spec(module_name) is not None
    module = importlib.import_module(module_name)
    assert hasattr(module, "ResourceService"), "ResourceService must be implemented"
    return module.ResourceService


def _copilot():
    return {
        "stage_name": "1-7",
        "opers": [{"name": "阿米娅", "skill": 1}],
        "actions": [
            {"type": "部署", "name": "阿米娅", "location": [3, 4], "direction": "左"}
        ],
    }


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


def test_large_copilot_is_stored_as_a_resource_relative_file(
    resource_root, retention_session_factory
):
    Service = _service_type()
    service = Service(retention_session_factory, resource_root=resource_root)
    content = _copilot() | {"doc": {"details": "x" * (65 * 1024)}}

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
    definition = {"action": "ClickSelf", "template": "Custom/Event.png"}

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


def test_infrast_plan_requires_a_plans_array_and_is_stored(
    resource_root, retention_session_factory
):
    Service = _service_type()
    service = Service(retention_session_factory, resource_root=resource_root)
    plan = {
        "author": "agent",
        "planTimes": "1班",
        "plans": [
            {"name": "白班", "rooms": {"trading": [], "manufacture": []}}
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
