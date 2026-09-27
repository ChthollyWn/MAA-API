"""Copilot participates in the shared task and schedule input schema."""

from __future__ import annotations

from pydantic import TypeAdapter

from maa_api.api.routers.tasks import TASK_MODELS, export_type_schema
from maa_api.domain.task import CopilotInput, PipelineCreate, TaskInput
from maa_api.services.schedule_service import ScheduleWrite


def test_copilot_is_accepted_by_pipeline_schedule_and_task_schema() -> None:
    copilot = {"name": "Copilot", "asset_id": "asset-1", "formation": False}

    pipeline = PipelineCreate(tasks=[copilot])
    schedule = ScheduleWrite(
        name="copilot",
        cron="0 9 * * *",
        template=pipeline.tasks,
    )

    assert isinstance(TypeAdapter(TaskInput).validate_python(copilot), CopilotInput)
    assert pipeline.tasks[0].name == "Copilot"
    assert pipeline.tasks[0].to_core_params() == {
        "asset_id": "asset-1",
        "formation": False,
    }
    assert schedule.template[0].name == "Copilot"
    assert TASK_MODELS["Copilot"].model_fields["asset_id"].is_required()
    assert set(TASK_MODELS["Copilot"].model_fields) == {
        "name",
        "asset_id",
        "formation",
    }
    assert CopilotInput.model_validate(
        {"name": "Copilot", "asset_id": "asset-1"}
    ).formation is False

    schema = export_type_schema(TASK_MODELS["Copilot"])
    assert schema["label"]
    assert schema["schema"]["properties"]["asset_id"]["x-label"]
    assert schema["schema"]["examples"] == [copilot]
