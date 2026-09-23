"""M4-08 historical log filters, cursor pages, exports and cleanup."""

from __future__ import annotations

import asyncio
from datetime import datetime, timezone

from fastapi import FastAPI
from sqlmodel import SQLModel

import maa_api.db.models  # noqa: F401
from maa_api.api.errors import register_exception_handlers
from maa_api.api.routers.logs import router
from maa_api.db import session as db_session
from maa_api.db.models import LogEntry, Pipeline, Task
from maa_api.domain.enums import LogLevel, LogSource, PipelineSource, Priority


def _app() -> FastAPI:
    app = FastAPI()
    register_exception_handlers(app)
    app.include_router(router)
    return app


def test_log_query_filters_cursor_export_and_delete(
    tmp_settings, isolated_db, make_client
):
    async def seed() -> tuple[str, str]:
        async with isolated_db.begin() as connection:
            await connection.run_sync(SQLModel.metadata.create_all)
        pipeline = Pipeline(
            source=PipelineSource.MANUAL,
            priority=Priority.MANUAL,
            task_count=1,
            title="smoke pipeline",
        )
        task = Task(
            pipeline_id=pipeline.id,
            order_index=0,
            type_name="Award",
            task_name="领取奖励",
            params={},
        )
        async with db_session.session_factory() as session:
            session.add(pipeline)
            session.add(task)
            await session.commit()
            session.add_all(
                [
                LogEntry(
                    source=LogSource.SERVER,
                    level=LogLevel.INFO,
                    content="request accepted",
                    meta={"logger": "uvicorn.access"},
                    pipeline_id=pipeline.id,
                    task_id=task.id,
                    created_at=datetime(2026, 1, 1, tzinfo=timezone.utc).replace(tzinfo=None),
                ),
                LogEntry(
                    source=LogSource.SERVER,
                    level=LogLevel.WARNING,
                    content="request warning",
                    meta={"logger": "uvicorn.access"},
                    pipeline_id=pipeline.id,
                    task_id=task.id,
                    created_at=datetime(2026, 1, 2, tzinfo=timezone.utc).replace(tzinfo=None),
                ),
                LogEntry(
                    source=LogSource.MAA_TASK,
                    level=LogLevel.ERROR,
                    content="task failed: marker",
                    meta={"logger": "worker", "attachment": {"kind": "screenshot", "sha256": "a" * 64}},
                    pipeline_id=pipeline.id,
                    task_id=task.id,
                    created_at=datetime(2026, 1, 3, tzinfo=timezone.utc).replace(tzinfo=None),
                ),
                LogEntry(
                    source=LogSource.MAACORE_DEBUG,
                    level=LogLevel.DEBUG,
                    content="core trace",
                    meta={"logger": "core"},
                    created_at=datetime(2026, 1, 4, tzinfo=timezone.utc).replace(tzinfo=None),
                ),
                ]
            )
            await session.commit()
        return pipeline.id, task.id

    pipeline_id, task_id = asyncio.run(seed())
    isolated_db.sync_engine.dispose()
    client = make_client(_app())

    response = client.get(
        "/api/system/logs",
        params=[
            ("source", "service"),
            ("level", "INFO"),
            ("logger", "uvicorn"),
            ("q", "request"),
            ("pipeline_id", pipeline_id),
            ("task_id", task_id),
            ("order", "asc"),
            ("size", "1"),
        ],
    )
    assert response.status_code == 200, response.text
    body = response.json()
    assert [item["content"] for item in body["items"]] == ["request accepted"]
    assert body["items"][0]["source"] == "service"
    assert body["page"] == {"next_cursor": body["items"][0]["id"], "has_more": True, "limit": 1}

    # Inclusive query bounds and exclusive ID cursors remain stable in either order.
    all_rows = client.get("/api/system/logs", params={"order": "asc", "size": 10}).json()["items"]
    ids = [item["id"] for item in all_rows]
    bounded = client.get(
        "/api/system/logs",
        params={"after_id": ids[0], "before_id": ids[-1], "order": "asc"},
    )
    assert [item["id"] for item in bounded.json()["items"]] == ids[1:-1]

    limited = client.get("/api/system/logs?size=2001")
    assert limited.status_code == 200 and limited.json()["page"]["limit"] == 1000
    invalid = client.get("/api/system/logs?after_id=1&page=1")
    assert invalid.status_code == 400
    assert invalid.json()["error"]["code"] == "INVALID_PAGINATION"

    txt = client.get("/api/system/logs/export", params={"format": "txt", "q": "marker"})
    assert txt.status_code == 200
    assert "attachment; filename=\"logs.txt\"" in txt.headers["content-disposition"]
    assert "task failed: marker" in txt.text
    jsonl = client.get("/api/system/logs/export?format=jsonl&source=task")
    assert jsonl.status_code == 200
    assert "logs.jsonl" in jsonl.headers["content-disposition"]
    exported = jsonl.text.strip().splitlines()
    assert len(exported) == 1 and '"source":"task"' in exported[0]
    assert '"attachment":{"kind":"screenshot"' in exported[0]

    invalid_delete = client.delete("/api/system/logs")
    assert invalid_delete.status_code == 400
    assert invalid_delete.json()["error"]["code"] == "INVALID_PARAMETER"
    deleted = client.delete("/api/system/logs?source=service")
    assert deleted.status_code == 204 and deleted.content == b""
    remaining = client.get("/api/system/logs?order=asc&size=10").json()["items"]
    assert len(remaining) == 2
    assert {item["source"] for item in remaining} == {"task", "core"}
