"""Agent resource, maintenance and schedule tools keep application contracts."""

from __future__ import annotations

import asyncio
import importlib
import importlib.util
from datetime import UTC, datetime
from types import SimpleNamespace

import pytest

from maa_api.agent.policy import PolicyEngine
from maa_api.agent.registry import ToolContext, ToolRegistry, ToolRisk
from maa_api.domain.enums import CallerType, RiskLevel
from maa_api.domain.errors import AppError


def _module(name: str):
    module_name = f"maa_api.agent.tools.{name}"
    assert importlib.util.find_spec(module_name) is not None, (
        f"{name} tools must be implemented"
    )
    return importlib.import_module(module_name)


def _context(state: object, db_session: object | None = None) -> ToolContext:
    return ToolContext(
        caller=CallerType.INTERNAL,
        session_id="agent-session",
        request_id="agent-tools-test",
        request=SimpleNamespace(app=SimpleNamespace(state=state)),
        db_session=db_session,
    )


def test_resource_group_registers_upload_and_custom_task_but_no_copilot_runner() -> None:
    module = _module("resource")
    registry = ToolRegistry()

    module.register_tools(registry)

    definitions = {item.name: item for item in registry.list("resource")}
    assert set(definitions) == {
        "list_copilots",
        "upload_copilot",
        "set_infrast_plan",
        "list_custom_tasks",
        "register_custom_task",
        "remove_custom_task",
    }
    assert "run_copilot" not in {item.name for item in registry.list()}
    assert definitions["upload_copilot"].risk is ToolRisk.SAFE
    assert definitions["register_custom_task"].risk is ToolRisk.DANGEROUS


def test_resource_tool_uploads_only_validated_copilot_json() -> None:
    module = _module("resource")
    registry = ToolRegistry()
    module.register_tools(registry)

    class Resources:
        def __init__(self):
            self.uploads = []

        async def upload_copilot(self, **kwargs):
            self.uploads.append(kwargs)
            return {"id": "copilot-1", "name": kwargs["name"]}

    resources = Resources()
    context = _context(SimpleNamespace(resource_service=resources))
    valid = {
        "stage_name": "1-7",
        "opers": [{"name": "阿米娅", "skill": 1}],
        "actions": [{"type": "部署", "name": "阿米娅", "location": [3, 4]}],
    }

    result = asyncio.run(
        registry.execute(
            "upload_copilot",
            {"name": "1-7 稳定作业", "description": "fixture", "content": valid},
            context,
        )
    )

    assert result == {"id": "copilot-1", "name": "1-7 稳定作业"}
    assert resources.uploads == [
        {"name": "1-7 稳定作业", "description": "fixture", "content": valid}
    ]

    assert len(resources.uploads) == 1


def test_ops_tools_delegate_update_targets_and_expose_no_core_client() -> None:
    module = _module("ops")
    registry = ToolRegistry()
    module.register_tools(registry)

    class Updates:
        def __init__(self):
            self.started = []

        async def status(self, *, refresh=False):
            return {"updates": {"core": {"available": True}}, "cached": not refresh}

        async def start(self, target, options=None, caller="manual"):
            self.started.append((str(target), options, caller))
            return SimpleNamespace(id="update-1", target=target, status="running")

    updates = Updates()
    context = _context(SimpleNamespace(update_service=updates))
    status = asyncio.run(registry.execute("check_updates", {}, context))
    core = asyncio.run(
        registry.execute("update_core", {"channel": "stable"}, context)
    )
    resource = asyncio.run(
        registry.execute("update_resource", {"channel": "repo"}, context)
    )
    game = asyncio.run(
        registry.execute("update_game", {"channel": "Official"}, context)
    )

    assert status["updates"]["core"]["available"] is True
    assert [core["target"], resource["target"], game["target"]] == [
        "core",
        "resource",
        "game",
    ]
    assert updates.started == [
        ("core", {"channel": "stable", "force": False, "version": None}, "agent"),
        ("resource", {"channel": "repo", "force": False}, "agent"),
        ("game", {"force": False, "channel": "Official"}, "agent"),
    ]


def test_update_core_schema_exposes_only_the_confirmed_stable_channel() -> None:
    module = _module("ops")
    registry = ToolRegistry()
    module.register_tools(registry)

    schema = registry.export_schema("ops")
    core_schema = next(item["inputSchema"] for item in schema if item["name"] == "update_core")
    channel_schema = core_schema["properties"]["channel"]
    assert channel_schema.get("enum", [channel_schema.get("const")]) == ["stable"]
    with pytest.raises(AppError):
        registry.validate("update_core", {"channel": "beta"})


def test_restart_core_tool_uses_maintenance_service_and_returns_summary() -> None:
    module = _module("ops")
    registry = ToolRegistry()
    module.register_tools(registry)

    class AgentOps:
        async def restart_core(self, *, caller):
            assert caller == "agent"
            return {"status": "ready", "generation": 8}

    result = asyncio.run(
        registry.execute(
            "restart_core",
            {},
            _context(SimpleNamespace(agent_ops_service=AgentOps())),
        )
    )

    assert result == {"status": "ready", "generation": 8}


def test_wait_update_blocks_until_terminal_state(monkeypatch: pytest.MonkeyPatch) -> None:
    """M57：更新是异步的，``wait_update`` 负责阻塞到终态，替代反复 get_versions。"""
    module = _module("ops")
    registry = ToolRegistry()
    module.register_tools(registry)
    monkeypatch.setattr(module, "WAIT_UPDATE_POLL_SECONDS", 0.0)

    class Updates:
        def __init__(self):
            self.calls = 0

        async def get(self, update_id):
            self.calls += 1
            terminal = self.calls > 1
            return SimpleNamespace(
                id=update_id,
                target="resource",
                channel="repo",
                status="success" if terminal else "running",
                phase="download" if not terminal else None,
                progress=100 if terminal else 40,
                bytes_total=10,
                bytes_done=10 if terminal else 4,
                from_version="2026-10-09",
                to_version="2026-10-16",
                error_code=None,
                error_message=None,
                finished_at=datetime(2026, 10, 17, tzinfo=UTC) if terminal else None,
            )

    updates = Updates()
    result = asyncio.run(
        registry.execute(
            "wait_update",
            {"update_id": "update-1", "timeout_seconds": 5},
            _context(SimpleNamespace(update_service=updates)),
        )
    )

    assert result["finished"] is True
    assert result["update"]["status"] == "success"
    assert result["update"]["progress"] == 100
    assert result["update"]["finished_at"]
    assert updates.calls >= 2


def test_wait_update_reports_when_nothing_is_running() -> None:
    module = _module("ops")
    registry = ToolRegistry()
    module.register_tools(registry)

    class Updates:
        async def status(self, *, refresh=False):
            return {"updates": {}, "running": None}

    result = asyncio.run(
        registry.execute(
            "wait_update", {"timeout_seconds": 5}, _context(SimpleNamespace(update_service=Updates()))
        )
    )

    assert result["finished"] is True
    assert result["update"] is None
    assert result["note"] == "当前没有进行中的更新"
    assert result["waited_seconds"] >= 0


def test_schedule_tools_delegate_crud_and_preserve_consumption_confirmation_policy() -> None:
    module = _module("schedule")
    registry = ToolRegistry()
    module.register_tools(registry)

    class Schedules:
        def __init__(self):
            self.created = None
            self.updated = None
            self.deleted = None

        async def list(self, *, enabled=None):
            return {"items": [], "total": 0}

        async def create(self, payload):
            self.created = payload
            return {"id": "schedule-1", "template": payload.template}

        async def update(self, schedule_id, payload):
            self.updated = (schedule_id, payload)
            return {"id": schedule_id, "template": payload.template}

        async def delete(self, schedule_id):
            self.deleted = schedule_id

    schedules = Schedules()
    context = _context(SimpleNamespace(schedule_service=schedules))
    template = [{"name": "Fight", "stone": 1, "stage": "1-7"}]
    payload = {
        "name": "daily",
        "cron": "0 8 * * *",
        "template": template,
    }
    created = asyncio.run(registry.execute("create_schedule", payload, context))
    updated = asyncio.run(
        registry.execute(
            "update_schedule", {**payload, "schedule_id": "schedule-1"}, context
        )
    )
    deleted = asyncio.run(
        registry.execute("delete_schedule", {"schedule_id": "schedule-1"}, context)
    )

    assert created["id"] == updated["id"] == "schedule-1"
    assert schedules.created.template[0].stone == 1
    assert schedules.updated[0] == "schedule-1"
    assert deleted == {"schedule_id": "schedule-1", "deleted": True}
    definitions = {item.name: item for item in registry.list("schedule")}
    assert definitions["create_schedule"].risk is ToolRisk.CONDITIONAL
    assert definitions["update_schedule"].risk is ToolRisk.CONDITIONAL
    assert definitions["delete_schedule"].risk is ToolRisk.SAFE

    policy = PolicyEngine()
    create_decision = asyncio.run(
        policy.evaluate(definitions["create_schedule"], payload, context)
    )
    update_decision = asyncio.run(
        policy.evaluate(
            definitions["update_schedule"],
            {**payload, "schedule_id": "schedule-1"},
            context,
        )
    )
    no_consumption = asyncio.run(
        policy.evaluate(
            definitions["create_schedule"],
            {**payload, "template": [{"name": "Fight", "stage": "1-7", "times": 3}]},
            context,
        )
    )
    delete_decision = asyncio.run(
        policy.evaluate(
            definitions["delete_schedule"], {"schedule_id": "schedule-1"}, context
        )
    )
    assert create_decision.requires_confirmation
    assert create_decision.risk is RiskLevel.CONSUME
    assert update_decision.requires_confirmation
    assert not no_consumption.requires_confirmation
    assert not delete_decision.requires_confirmation


def test_schedule_group_registration_uses_schedule_service_crud_contract() -> None:
    module = _module("schedule")
    registry = ToolRegistry()

    module.register_tools(registry)

    assert {item.name for item in registry.list("schedule")} == {
        "list_schedules",
        "create_schedule",
        "update_schedule",
        "delete_schedule",
    }
