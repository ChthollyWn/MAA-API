"""M11 exposes exactly the implemented docs/11 §3.2 tool catalog."""

import asyncio

from maa_api.agent.tools import build_registry


EXPECTED_TOOLS = {
    "get_system_status", "get_screenshot", "get_versions", "get_logs",
    "resolve_stage", "get_drop_stats", "list_task_types", "get_pipeline",
    "wait_pipeline", "list_pipelines", "get_queue", "submit_pipeline",
    "stop_pipeline", "set_task_params", "cancel_queued", "click", "swipe",
    "long_press", "input_text", "key_event", "back_to_home", "trigger_screencap",
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
    assert len(actual) == 41
    assert "run_copilot" not in actual


def test_wait_pipeline_is_registered_as_a_safe_read_only_pipeline_tool():
    registry = build_registry()
    definition = registry.get("wait_pipeline")

    assert definition.group == "pipeline"
    assert definition.risk.value == "SAFE"
    assert definition.read_only is True


def test_registry_schema_marks_safe_raw_exceptions_and_consumption_rules():
    registry = build_registry()
    definitions = {item["name"]: item for item in registry.export_schema()}

    assert definitions["back_to_home"]["risk"] == "SAFE"
    assert definitions["trigger_screencap"]["risk"] == "SAFE"
    assert definitions["submit_pipeline"]["risk"] == "CONDITIONAL"
    assert definitions["register_custom_task"]["risk"] == "DANGEROUS"


def test_trigger_screencap_is_registered_but_hidden_from_the_builtin_agent():
    """隐藏只影响模型：注册表、REST 目录和调用入口都保留（规格 §4.4）。"""

    registry = build_registry()
    definition = registry.get("trigger_screencap")

    assert definition.model_visible is False
    assert "trigger_screencap" in {item["name"] for item in registry.export_schema()}
    hidden = {item.name for item in registry.list() if not item.model_visible}
    assert hidden == {"trigger_screencap"}


def test_trigger_screencap_stays_in_the_rest_catalog_and_is_still_invocable():
    """REST 入口不变：`GET /api/agent/tools` 列出它，`invoke` 照旧执行。"""

    from fastapi import FastAPI
    from fastapi.testclient import TestClient
    from PIL import Image
    from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker
    from sqlalchemy.pool import StaticPool
    from sqlmodel import SQLModel

    import maa_api.db.models  # noqa: F401
    from maa_api.agent.confirmation import ConfirmationService
    from maa_api.agent.policy import PolicyEngine
    from maa_api.api.deps import get_session
    from maa_api.api.errors import register_exception_handlers
    from maa_api.api.routers import agent
    from maa_api.db import session as db_session
    from maa_api.services.device_service import ScreenshotResult
    from maa_api.settings import LLMSettings, Settings, set_settings

    class FakeDeviceManager:
        def snapshot(self):
            return {"state": "connected", "resolution": {"width": 1920, "height": 1080}}

        async def screenshot(self, *, backend: str = "adb") -> ScreenshotResult:
            return ScreenshotResult(Image.new("RGB", (2, 1), (10, 20, 30)), "core")

    async def scenario():
        engine = db_session.make_engine(
            "sqlite+aiosqlite:///:memory:", poolclass=StaticPool
        )
        factory = async_sessionmaker(engine, expire_on_commit=False, class_=AsyncSession)
        async with engine.begin() as connection:
            await connection.run_sync(SQLModel.metadata.create_all)
        service = ConfirmationService(factory, build_registry(), policy=PolicyEngine())
        app = FastAPI()
        register_exception_handlers(app)
        app.include_router(agent.router)
        app.state.confirmation_service = service
        app.state.device_manager = FakeDeviceManager()

        async def get_test_session():
            async with factory() as session:
                yield session

        app.dependency_overrides[get_session] = get_test_session
        set_settings(
            Settings(
                llm=LLMSettings(
                    base_url="https://llm.test/v1", api_key="k", model="m"
                )
            )
        )
        client = TestClient(app, raise_server_exceptions=False)
        client.__enter__()
        try:
            listed = client.get("/api/agent/tools")
            assert listed.status_code == 200
            assert "trigger_screencap" in {
                item["name"] for item in listed.json()["items"]
            }

            invoked = client.post(
                "/api/agent/tools/trigger_screencap/invoke", json={"arguments": {}}
            )
            assert invoked.status_code == 200
            payload = invoked.json()
            assert payload["status"] == "success"
            assert payload["audit_id"] is not None
            assert payload["result"]["mime_type"] == "image/png"
            assert (payload["result"]["width"], payload["result"]["height"]) == (2, 1)
            assert payload["result"]["image_base64"]
        finally:
            client.__exit__(None, None, None)
            await engine.dispose()

    asyncio.run(scenario())


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
