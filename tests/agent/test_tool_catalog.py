"""M11 exposes exactly the implemented docs/11 §3.2 tool catalog."""

import asyncio

import pytest

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
    "check_confirmation", "read_knowledge", "ask_user",
    # M23 媒体组（规格 §3.2）。
    "send_screenshot", "annotate_screenshot", "send_item_icons",
    "send_stage_map", "send_chart",
    # M24 活动组（规格 §4.2）。
    "get_activity_stages",
    "get_stage",
    "search_prts",
    "get_prts_page",
    "get_operator",
    "get_enemy",
    "get_item",
    "get_drop_rates",
    "get_operator_box",
    "get_depot",
    "update_operator_box",
    "plan_upgrade",
    "start_upgrade_plan",
    "list_upgrade_plans",
    "cancel_upgrade_plan",
}


def test_registry_exposes_all_delivered_m11_tools_and_no_deferred_copilot_runner():
    registry = build_registry()

    actual = {definition.name for definition in registry.list()}
    assert actual == EXPECTED_TOOLS
    assert len(actual) == 63
    assert "knowledge" in {definition.group for definition in registry.list()}
    assert "interaction" in {definition.group for definition in registry.list()}
    assert "media" in {definition.group for definition in registry.list()}
    assert "activity" in {definition.group for definition in registry.list()}
    assert "game" in {definition.group for definition in registry.list()}
    assert "account" in {definition.group for definition in registry.list()}
    assert "run_copilot" not in actual


def test_read_knowledge_is_registered_as_a_safe_read_only_knowledge_tool():
    """规格 §4.1：新工具组 knowledge，SAFE、只读，参数是条目 id。"""

    from maa_api.agent.knowledge import ENTRY_ID_PATTERN

    definition = build_registry().get("read_knowledge")

    assert definition.group == "knowledge"
    assert definition.risk.value == "SAFE"
    assert definition.read_only is True
    assert definition.model_visible is True
    assert (
        definition.params_model.model_json_schema(mode="validation")["properties"][
            "entry_id"
        ]["pattern"]
        == ENTRY_ID_PATTERN.pattern
    )


def test_read_knowledge_returns_the_entry_and_maps_missing_entries_to_the_service():
    from datetime import datetime
    from types import SimpleNamespace

    from pydantic import ValidationError

    from maa_api.agent.knowledge import KnowledgeEntry
    from maa_api.agent.registry import ToolContext
    from maa_api.domain.enums import CallerType
    from maa_api.domain.errors import AppError, ErrorCode

    entry = KnowledgeEntry(
        id="demo",
        kind="rule",
        title="演示规则",
        when_to_use="演示时",
        body="正文",
        enabled=True,
        source="builtin",
        modified=True,
        updated_at=datetime(2026, 9, 29, 3, 4, 5),
    )

    class Service:
        def __init__(self, *, enabled=True):
            self.enabled = enabled
            self.reads: list[str] = []

        async def read_for_agent(self, entry_id):
            self.reads.append(entry_id)
            if entry_id != "demo" or not self.enabled:
                raise AppError(
                    ErrorCode.KNOWLEDGE_NOT_FOUND,
                    "知识条目不存在或已停用",
                    {"entry_id": entry_id},
                )
            return entry

    def context_for(service):
        state = SimpleNamespace(knowledge_service=service)
        return ToolContext(
            CallerType.INTERNAL,
            None,
            None,
            SimpleNamespace(app=SimpleNamespace(state=state)),
            None,
        )

    async def scenario():
        definition = build_registry().get("read_knowledge")
        params = definition.params_model.model_validate({"entry_id": "demo"})

        service = Service()
        assert await definition.handler(params, context_for(service)) == {
            "id": "demo",
            "kind": "rule",
            "title": "演示规则",
            "body": "正文",
            "source": "builtin",
            "modified": True,
            # 工具结果里的时间统一带 UTC 偏移（与其它工具一致）。
            "updated_at": "2026-09-29T03:04:05Z",
        }
        assert service.reads == ["demo"]

        disabled = Service(enabled=False)
        with pytest.raises(AppError) as disabled_error:
            await definition.handler(params, context_for(disabled))
        assert disabled_error.value.code == ErrorCode.KNOWLEDGE_NOT_FOUND

        unknown = definition.params_model.model_validate({"entry_id": "other"})
        with pytest.raises(AppError) as unknown_error:
            await definition.handler(unknown, context_for(service))
        assert unknown_error.value.code == ErrorCode.KNOWLEDGE_NOT_FOUND
        assert unknown_error.value.details == {"entry_id": "other"}

        # 非法 id 在进服务之前就被参数模型拦下（与 REST 同一套约束）。
        with pytest.raises(ValidationError):
            definition.params_model.model_validate({"entry_id": "Bad_ID"})

    asyncio.run(scenario())


def test_wait_pipeline_is_registered_as_a_safe_read_only_pipeline_tool():
    registry = build_registry()
    definition = registry.get("wait_pipeline")

    assert definition.group == "pipeline"
    assert definition.risk.value == "SAFE"
    assert definition.read_only is True


def test_ask_user_is_registered_as_a_safe_serial_interaction_tool():
    """规格 §3.1：新工具组 interaction，SAFE、非只读（必须与写操作串行）、对模型可见。"""

    definition = build_registry().get("ask_user")

    assert definition.group == "interaction"
    assert definition.risk.value == "SAFE"
    assert definition.read_only is False
    assert definition.model_visible is True
    # 选项数量与长度上限进 JSON Schema：模型看得到「2–6 个选项」这条硬约束。
    schema = definition.params_model.model_json_schema(mode="validation")
    assert schema["properties"]["options"]["minItems"] == 2
    assert schema["properties"]["options"]["maxItems"] == 6
    assert schema["properties"]["multiple"]["default"] is False


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


def test_ask_user_rest_invocation_fails_with_invalid_parameter():
    """规格 §2.2：REST 请求没法等 10 分钟，直接 400 并说明只能由内置 Agent 调用。"""

    from fastapi import FastAPI
    from fastapi.testclient import TestClient
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
    from maa_api.settings import LLMSettings, Settings, set_settings

    async def scenario():
        engine = db_session.make_engine(
            "sqlite+aiosqlite:///:memory:", poolclass=StaticPool
        )
        factory = async_sessionmaker(engine, expire_on_commit=False, class_=AsyncSession)
        async with engine.begin() as connection:
            await connection.run_sync(SQLModel.metadata.create_all)
        app = FastAPI()
        register_exception_handlers(app)
        app.include_router(agent.router)
        app.state.confirmation_service = ConfirmationService(
            factory, build_registry(), policy=PolicyEngine()
        )

        async def get_test_session():
            async with factory() as session:
                yield session

        app.dependency_overrides[get_session] = get_test_session
        set_settings(Settings(llm=LLMSettings(base_url="https://llm.test/v1", api_key="k", model="m")))
        client = TestClient(app, raise_server_exceptions=False)
        client.__enter__()
        try:
            listed = client.get("/api/agent/tools").json()["items"]
            assert "ask_user" in {item["name"] for item in listed}

            invoked = client.post(
                "/api/agent/tools/ask_user/invoke",
                json={
                    "arguments": {
                        "question": "走哪个渠道？",
                        "options": [{"label": "官服"}, {"label": "B 服"}],
                    }
                },
            )
            assert invoked.status_code == 400, invoked.text
            error = invoked.json()["error"]
            assert error["code"] == "INVALID_PARAMETER"
            assert "只能由内置 Agent 在会话内调用" in error["message"]
        finally:
            client.__exit__(None, None, None)
            await engine.dispose()

    asyncio.run(scenario())
