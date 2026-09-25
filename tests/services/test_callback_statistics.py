"""Structured callback data survives log flushing and supports scoped aggregates."""

from __future__ import annotations

from datetime import UTC, datetime
import importlib.util
import json
from pathlib import Path

from sqlmodel import SQLModel

from maa_api.core.enums import Message
from maa_api.db import session as db_session
from maa_api.db.models import Pipeline, Task
from maa_api.db.repositories.pipeline import PipelineRepository
from maa_api.domain.enums import PipelineSource, PipelineStatus, Priority
from maa_api.services.log_hub import LogHub, LogRecord


def _require_statistics_schema_and_service() -> None:
    assert "stage_drop" in SQLModel.metadata.tables, (
        "structured stage-drop model must be registered"
    )
    assert "sanity_observation" in SQLModel.metadata.tables, (
        "structured sanity-observation model must be registered"
    )
    assert importlib.util.find_spec("maa_api.services.callback_statistics") is not None, (
        "callback statistics service must be implemented"
    )
    assert importlib.util.find_spec("maa_api.db.repositories.stage_statistics") is not None, (
        "stage statistics repository must be implemented"
    )


def _record(
    *,
    ts: float,
    what: str,
    data: dict,
    pipeline_id: str,
    task_id: str,
) -> LogRecord:
    callback = {"what": what, "details": data}
    return LogRecord(
        ts=ts,
        source="task",
        level="INFO",
        content=f"display text for {what}",
        pipeline_id=pipeline_id,
        task_id=task_id,
        raw={"msg": int(Message.SubTaskExtraInfo), "details": callback},
    )


def _stamp(value: str) -> float:
    return datetime.fromisoformat(value).replace(tzinfo=UTC).timestamp()


def _callback_fixture(name: str) -> dict:
    path = Path(__file__).parents[1] / "fixtures" / "agent" / name
    return json.loads(path.read_text(encoding="utf-8"))


def test_stage_callback_tables_are_part_of_the_current_metadata() -> None:
    _require_statistics_schema_and_service()


def test_log_flush_persists_structured_callback_data_and_queries_by_stage_and_time(
    retention_session_factory, monkeypatch
) -> None:
    _require_statistics_schema_and_service()
    from maa_api.db.repositories.stage_statistics import StageStatisticsRepository

    async def scenario() -> None:
        async with retention_session_factory() as session:
            pipeline = Pipeline(
                id="pipeline-statistics",
                source=PipelineSource.MANUAL,
                priority=Priority.MANUAL,
                status=PipelineStatus.COMPLETED,
                task_count=1,
            )
            task = Task(
                id="task-statistics",
                pipeline_id=pipeline.id,
                order_index=0,
                type_name="Fight",
                task_name="刷理智",
                params={"stage": "1-7"},
            )
            await PipelineRepository(session).create(pipeline, [task])
            await session.commit()

        stage_drops = _callback_fixture("stage_drops_callback.json")
        first_drop = _record(
            ts=_stamp("2026-09-24T10:00:00+00:00"),
            what=stage_drops["what"],
            data=stage_drops["details"],
            pipeline_id=pipeline.id,
            task_id=task.id,
        )
        second_drop = _record(
            ts=_stamp("2026-09-25T09:00:00+00:00"),
            what="StageDrops",
            data={
                "stars": 3,
                "stage": {"stageCode": "1-7"},
                "stats": [
                    {"itemId": "4001", "itemName": "龙门币", "quantity": 5, "addQuantity": 1},
                ],
            },
            pipeline_id=pipeline.id,
            task_id=task.id,
        )
        outside_time = _record(
            ts=_stamp("2026-09-23T23:59:59+00:00"),
            what="StageDrops",
            data={
                "stars": 3,
                "stage": {"stageCode": "1-7"},
                "stats": [
                    {"itemId": "4001", "itemName": "龙门币", "quantity": 99, "addQuantity": 0},
                ],
            },
            pipeline_id=pipeline.id,
            task_id=task.id,
        )
        other_stage = _record(
            ts=_stamp("2026-09-25T09:30:00+00:00"),
            what="StageDrops",
            data={
                "stars": 3,
                "stage": {"stageCode": "CE-6"},
                "stats": [
                    {"itemId": "4001", "itemName": "龙门币", "quantity": 200, "addQuantity": 0},
                ],
            },
            pipeline_id=pipeline.id,
            task_id=task.id,
        )
        sanity_callback = _callback_fixture("sanity_before_stage_callback.json")
        first_sanity = _record(
            ts=_stamp("2026-09-24T10:01:00+00:00"),
            what=sanity_callback["what"],
            data=sanity_callback["details"],
            pipeline_id=pipeline.id,
            task_id=task.id,
        )
        second_sanity = _record(
            ts=_stamp("2026-09-25T09:01:00+00:00"),
            what="SanityBeforeStage",
            data={"current_sanity": 126, "max_sanity": 135},
            pipeline_id=pipeline.id,
            task_id=task.id,
        )
        malformed = _record(
            ts=_stamp("2026-09-25T09:40:00+00:00"),
            what="StageDrops",
            data={
                "stars": 3,
                "stage": {"stageCode": "1-7"},
                "stats": [
                    {"itemId": "4001", "itemName": "龙门币", "quantity": "many", "addQuantity": 1},
                ],
            },
            pipeline_id=pipeline.id,
            task_id=task.id,
        )
        unrelated = _record(
            ts=_stamp("2026-09-25T09:45:00+00:00"),
            what="RoguelikeEvent",
            data={"name": "other callback"},
            pipeline_id=pipeline.id,
            task_id=task.id,
        )
        wrong_message = _record(
            ts=_stamp("2026-09-25T09:50:00+00:00"),
            what="StageDrops",
            data={
                "stars": 3,
                "stage": {"stageCode": "1-7"},
                "stats": [
                    {"itemId": "4001", "itemName": "龙门币", "quantity": 500, "addQuantity": 0},
                ],
            },
            pipeline_id=pipeline.id,
            task_id=task.id,
        )
        wrong_message.raw["msg"] = int(Message.SubTaskCompleted)

        monkeypatch.setattr(db_session, "session_factory", retention_session_factory)
        records = [
            first_drop,
            second_drop,
            outside_time,
            other_stage,
            first_sanity,
            second_sanity,
            malformed,
            unrelated,
            wrong_message,
        ]
        for index, record in enumerate(records, start=1):
            record.id = index
        await LogHub()._flush(records)

        since = datetime(2026, 9, 24)
        until = datetime(2026, 9, 25, 12)
        async with retention_session_factory() as session:
            repo = StageStatisticsRepository(session)
            summary = await repo.drop_stats(
                stage_code="1-7", since=since, until=until
            )
            curve = await repo.sanity_curve(
                stage_code="1-7", since=since, until=until
            )

        assert summary == {
            "stage_code": "1-7",
            "runs": 2,
            "items": [
                {
                    "item_id": "30012",
                    "item_name": "作战记录",
                    "quantity": 3,
                    "add_quantity": 0,
                },
                {
                    "item_id": "4001",
                    "item_name": "龙门币",
                    "quantity": 15,
                    "add_quantity": 3,
                },
            ],
        }
        assert [
            (item["current_sanity"], item["max_sanity"])
            for item in curve
        ] == [(120, 135), (126, 135)]
        assert [item["stage_code"] for item in curve] == ["1-7", "1-7"]

    import asyncio

    asyncio.run(scenario())
