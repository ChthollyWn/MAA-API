"""Agent status tools reuse application services and return JSON values."""

from __future__ import annotations

import asyncio
import base64
import importlib
import importlib.util
from datetime import UTC, datetime
from types import SimpleNamespace

from PIL import Image

from maa_api.agent.registry import ToolContext, ToolRegistry, ToolRisk
from maa_api.core.enums import Message
from maa_api.domain.enums import CallerType, LogLevel, LogSource
from maa_api.db.models import LogEntry, utcnow
from maa_api.services.device_service import ScreenshotResult


def _register_status() -> ToolRegistry:
    module_name = "maa_api.agent.tools.status"
    assert importlib.util.find_spec(module_name) is not None, (
        "status tool module must be implemented"
    )
    module = importlib.import_module(module_name)
    registry = ToolRegistry()
    module.register_tools(registry)
    return registry


def _context(*, state: object | None = None, db_session: object | None = None) -> ToolContext:
    app = SimpleNamespace(state=state or SimpleNamespace())
    return ToolContext(
        caller=CallerType.INTERNAL,
        session_id=None,
        request_id="status-test",
        request=SimpleNamespace(app=app),
        db_session=db_session,
    )


def test_status_group_registers_only_the_documented_read_tools() -> None:
    registry = _register_status()

    definitions = registry.list("status")
    assert [definition.name for definition in definitions] == [
        "get_system_status",
        "get_screenshot",
        "get_versions",
        "get_logs",
        "resolve_stage",
        "get_drop_stats",
    ]
    assert all(definition.risk is ToolRisk.SAFE for definition in definitions)


def test_get_system_status_returns_service_core_device_and_queue_snapshots(monkeypatch) -> None:
    from maa_api.settings import Settings

    monkeypatch.setattr("maa_api.agent.tools.status.get_settings", lambda: Settings())

    class Queue:
        async def snapshot(self):
            return {
                "running": None,
                "pending": [],
                "counts": {"pending": 0, "running": 0},
                "paused": False,
            }

    state = SimpleNamespace(
        started_at=datetime(2026, 9, 25, 1, 2, 3, tzinfo=UTC),
        core_supervisor=SimpleNamespace(state="ready", pid=17, generation=4),
        device_manager=SimpleNamespace(
            snapshot=lambda: {
                "state": "connected",
                "address": "127.0.0.1:5555",
                "retry": {"attempt": 0, "max": 3, "next_at": None},
            }
        ),
        queue_service=Queue(),
    )
    registry = _register_status()

    result = asyncio.run(
        registry.execute("get_system_status", {}, _context(state=state))
    )

    assert result["status"] == "ok"
    assert result["core"] == {"state": "ready", "pid": 17, "generation": 4}
    assert result["device"]["state"] == "connected"
    assert result["queue"] == {"pending": 0, "running": 0, "paused": False}
    assert result["started_at"] == "2026-09-25T01:02:03+00:00"


def test_get_screenshot_returns_decodable_image_with_dimensions_and_capture_time() -> None:
    class Device:
        async def screenshot(self, backend: str = "adb") -> ScreenshotResult:
            assert backend == "adb"
            return ScreenshotResult(Image.new("RGB", (12, 8), (20, 40, 60)), "adb")

    registry = _register_status()
    result = asyncio.run(
        registry.execute(
            "get_screenshot",
            {},
            _context(state=SimpleNamespace(device_manager=Device())),
        )
    )

    import io

    image = Image.open(io.BytesIO(base64.b64decode(result["data"])))
    assert image.size == (12, 8)
    assert result["format"] == "jpeg"
    assert result["backend"] == "adb"
    assert isinstance(datetime.fromisoformat(result["captured_at"]), datetime)


def test_get_versions_uses_the_cached_update_status_service() -> None:
    class Updates:
        async def status(self):
            return {
                "updates": {
                    "core": {"target": "core", "current": "6.17.5", "latest": "6.17.5"},
                    "resource": {"available": False},
                    "game": {"available": None},
                },
                "checked_at": "2026-09-25T00:00:00Z",
                "cached": True,
                "running": None,
            }

    registry = _register_status()
    result = asyncio.run(
        registry.execute(
            "get_versions",
            {},
            _context(state=SimpleNamespace(update_service=Updates())),
        )
    )

    assert result["updates"]["core"]["current"] == "6.17.5"
    assert result["cached"] is True


def test_get_logs_applies_source_level_time_and_pipeline_filters(monkeypatch) -> None:
    from maa_api.db.repositories.log import LogRepository

    seen: dict[str, object] = {}

    async def query_cursor(self, **kwargs):
        seen.update(kwargs)
        return [
            LogEntry(
                id=9,
                source=LogSource.MAA_TASK,
                level=LogLevel.INFO,
                content="通关 1-7",
                pipeline_id="pipeline-1",
                task_id="task-1",
                created_at=utcnow(),
                meta={"logger": None},
            )
        ], False

    monkeypatch.setattr(LogRepository, "query_cursor", query_cursor)
    registry = _register_status()
    since = datetime(2026, 9, 24, tzinfo=UTC)
    until = datetime(2026, 9, 25, tzinfo=UTC)
    result = asyncio.run(
        registry.execute(
            "get_logs",
            {
                "source": ["task"],
                "level": "INFO",
                "pipeline_id": "pipeline-1",
                "since": since.isoformat(),
                "until": until.isoformat(),
                "limit": 10,
            },
            _context(db_session=object()),
        )
    )

    assert seen["sources"] == [LogSource.MAA_TASK]
    assert seen["minimum_level"] is LogLevel.INFO
    assert seen["pipeline_id"] == "pipeline-1"
    assert seen["since"] == datetime(2026, 9, 24)
    assert seen["until"] == datetime(2026, 9, 25)
    assert result["items"][0]["content"] == "通关 1-7"
    assert result["items"][0]["source"] == "task"


def test_resolve_stage_uses_the_stage_resolver_adapter() -> None:
    class StageResolver:
        async def resolve(self, key: str):
            assert key == "1-7"
            return {
                "query": key,
                "source": "resource",
                "resolved": {"code": "1-7", "stage_id": "main_01-07", "level_id": None, "name": None},
            }

    registry = _register_status()
    result = asyncio.run(
        registry.execute(
            "resolve_stage",
            {"key": "1-7"},
            _context(state=SimpleNamespace(stage_resolver=StageResolver())),
        )
    )

    assert result["resolved"]["code"] == "1-7"
    assert result["resolved"]["stage_id"] == "main_01-07"
    assert result["source"] == "resource"


def test_get_drop_stats_uses_stage_and_time_bounds(monkeypatch) -> None:
    from maa_api.db.repositories.stage_statistics import StageStatisticsRepository

    seen: dict[str, object] = {}

    async def drop_stats(self, **kwargs):
        seen.update(kwargs)
        return {"stage_code": "1-7", "runs": 3, "items": []}

    async def sanity_curve(self, **kwargs):
        seen["sanity_curve"] = kwargs
        return []

    monkeypatch.setattr(StageStatisticsRepository, "drop_stats", drop_stats)
    monkeypatch.setattr(StageStatisticsRepository, "sanity_curve", sanity_curve)
    registry = _register_status()

    result = asyncio.run(
        registry.execute(
            "get_drop_stats",
            {
                "stage": "1-7",
                "since": "2026-09-01T00:00:00Z",
                "until": "2026-09-25T00:00:00Z",
            },
            _context(db_session=object()),
        )
    )

    assert result["runs"] == 3
    assert seen["stage_code"] == "1-7"
    assert seen["since"] == datetime(2026, 9, 1)
    assert seen["until"] == datetime(2026, 9, 25)
