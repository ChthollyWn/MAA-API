"""M11 exposes exactly the implemented docs/11 §3.2 tool catalog."""

from maa_api.agent.tools import build_registry


EXPECTED_TOOLS = {
    "get_system_status", "get_screenshot", "get_versions", "get_logs",
    "resolve_stage", "get_drop_stats", "list_task_types", "get_pipeline",
    "list_pipelines", "get_queue", "submit_pipeline", "stop_pipeline",
    "set_task_params", "cancel_queued", "click", "swipe", "long_press",
    "input_text", "key_event", "back_to_home", "trigger_screencap",
    "get_device_status", "reconnect_device", "list_devices", "list_copilots",
    "upload_copilot", "list_custom_tasks", "register_custom_task",
    "remove_custom_task", "set_infrast_plan", "check_updates", "update_core",
    "update_resource", "update_game", "restart_core", "list_schedules",
    "create_schedule", "update_schedule", "delete_schedule",
    "check_confirmation",
}


def test_registry_exposes_all_delivered_m11_tools_and_no_deferred_copilot_runner():
    registry = build_registry()

    actual = {definition.name for definition in registry.list()}
    assert actual == EXPECTED_TOOLS
    assert len(actual) == 40
    assert "run_copilot" not in actual


def test_registry_schema_marks_safe_raw_exceptions_and_consumption_rules():
    registry = build_registry()
    definitions = {item["name"]: item for item in registry.export_schema()}

    assert definitions["back_to_home"]["risk"] == "SAFE"
    assert definitions["trigger_screencap"]["risk"] == "SAFE"
    assert definitions["submit_pipeline"]["risk"] == "CONDITIONAL"
    assert definitions["register_custom_task"]["risk"] == "DANGEROUS"


def test_check_confirmation_is_safe_and_delegates_to_the_shared_confirmation_service():
    import asyncio
    from types import SimpleNamespace

    from maa_api.agent.registry import ToolContext
    from maa_api.domain.enums import CallerType

    class Service:
        async def check_confirmation(self, confirmation_id, *, context=None):
            assert context is not None
            return {"confirmation_id": confirmation_id, "status": "approved"}

    async def scenario():
        definition = build_registry().get("check_confirmation")
        params = definition.params_model.model_validate({"confirmation_id": "confirm-1"})
        context = ToolContext(
            CallerType.INTERNAL,
            None,
            None,
            SimpleNamespace(app=SimpleNamespace(state=SimpleNamespace(confirmation_service=Service()))),
            None,
        )
        assert await definition.handler(params, context) == {
            "confirmation_id": "confirm-1",
            "status": "approved",
        }

    asyncio.run(scenario())
