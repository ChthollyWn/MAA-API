"""代理定义 REST 契约（M65 规格 §4.3.1）：两端点、鉴权、错误码与 OpenAPI 模型。"""

from __future__ import annotations

import re

import pytest
from fastapi import FastAPI

from maa_api.agent.runtime import SYSTEM_PROMPT
from maa_api.agent.specs.registry import AgentSpecRegistry
from maa_api.agent.tools import build_registry
from maa_api.api import deps
from maa_api.api.errors import register_exception_handlers
from maa_api.api.routers import agent_specs as agent_specs_router
from maa_api.main import create_app

#: ``SpecSummary`` 的 9 个字段（规格 §4.3.1）。
LIST_FIELDS = {
    "name",
    "title",
    "kind",
    "description",
    "model_role",
    "tools",
    "delegates",
    "version",
    "spec_version",
}
#: ``SpecDetail`` 的 17 个字段：列表字段 + 定义文件里其余的 8 个。
DETAIL_FIELDS = LIST_FIELDS | {
    "deferred_groups",
    "device",
    "risk_ceiling",
    "max_steps",
    "skills",
    "output_schema",
    "budget",
    "body",
}
SPEC_NAMES = [
    "analyst",
    "copilot",
    "daily_ops",
    "diagnose",
    "general",
    "legacy_chat",
    "legacy_incident",
    "memory_curator",
    "navigator",
    "ops",
    "orchestrator",
    "planner",
    "recon",
    "researcher",
    "summarizer",
    "titler",
    "trigger_evaluator",
    "verifier",
    "watchdog",
]
#: 各定义的版本号；M71 给 ``general`` 加了设备租约工具与通用 Worker 提示，升到 2。
#: M75 §4.7：七个 Worker 各加三个工作记忆工具，``general`` 升到 3、其余升到 2。
#: M78 §4.7：八个总控与 Worker 定义各加两个技能工具，版本再 +1。
#: M79 §4.2：总控与 general 加 ``remember``、diagnose 加 ``search_knowledge``，版本再 +1。
#: M82 §4.7：总控加四个目标工具与一段目标提示，``orchestrator`` 升到 6。
#: M85 §4.7：总控加一段 IM 来源与短回复提示，``orchestrator`` 升到 7。
#: M86 §4.7：导航员加三个导航工具与「界面导航图」提示，``navigator`` 升到 2。
#: M87 §4.7：``daily_ops``、``copilot`` 开启 ``verify_before_ready`` 并升到 4，
#: ``verifier`` 补核对完成证据的提示，升到 2；``general`` 不动，保持 6。
#: M88 §4.7：``general`` 加 ``optimize_farming`` 与「刷图方案」段，升到 7。
#: M89 §4.7：``general`` 加两个自定义任务工具与「新活动」段，升到 8。
#: M90 §4.7：四个 Worker 加 ``create_view`` 与视图提示，``general`` 升到 9，
#: ``analyst``、``planner``、``copilot`` 升到 5。
EXPECTED_VERSIONS = {name: 1 for name in SPEC_NAMES} | {
    "analyst": 5,
    "copilot": 5,
    "daily_ops": 4,
    "diagnose": 4,
    "general": 9,
    "navigator": 2,
    "ops": 3,
    "orchestrator": 7,
    "planner": 5,
    "researcher": 2,
    "verifier": 2,
}


def _specs_client(make_client, *, with_registry: bool = True):
    """生产装配同款：路由 + 统一错误体 + ``app.state.agent_spec_registry``。"""
    app = FastAPI()
    register_exception_handlers(app)
    app.include_router(agent_specs_router.router)
    if with_registry:
        registry = AgentSpecRegistry(build_registry())
        registry.load()
        app.state.agent_spec_registry = registry
    return make_client(app)


def _error(response):
    return response.json()["error"]


# ---------------------------------------------------------------------------
# 成功路径
# ---------------------------------------------------------------------------
def test_list_specs_returns_the_nineteen_builtin_definitions(make_client, tmp_settings):
    client = _specs_client(make_client)

    response = client.get("/api/agent/specs")
    assert response.status_code == 200, response.text
    items = response.json()["items"]
    assert [item["name"] for item in items] == SPEC_NAMES
    assert len(LIST_FIELDS) == 9
    for item in items:
        assert set(item) == LIST_FIELDS
        assert re.fullmatch(r"[0-9a-f]{12}", item["spec_version"])
        assert item["version"] == EXPECTED_VERSIONS[item["name"]]
    by_name = {item["name"]: item for item in items}
    assert by_name["legacy_chat"]["kind"] == "worker"
    assert by_name["recon"]["kind"] == "subagent"
    assert by_name["recon"]["tools"] == ["list_files", "read_file", "search_code"]


def test_get_spec_returns_the_prompt_body(make_client, tmp_settings):
    client = _specs_client(make_client)

    response = client.get("/api/agent/specs/legacy_chat")
    assert response.status_code == 200, response.text
    payload = response.json()
    assert len(DETAIL_FIELDS) == 17
    assert set(payload) == DETAIL_FIELDS
    assert payload["name"] == "legacy_chat"
    assert payload["body"] == SYSTEM_PROMPT
    assert payload["deferred_groups"] == [
        "account",
        "estate",
        "ops",
        "recon",
        "schedule",
    ]
    assert payload["device"] == "exclusive"
    assert payload["risk_ceiling"] == "destructive"
    assert payload["max_steps"] == 0
    assert payload["skills"] == []
    assert payload["output_schema"] is None
    assert payload["budget"] == {"max_cost_per_run": 0.0}


def test_worker_specs_expose_create_view_and_versions(make_client, tmp_settings):
    """M90 §4.7：四个 Worker 的详情都带 ``create_view``，版本是实测值 +1。"""
    client = _specs_client(make_client)

    for name in ("general", "analyst", "planner", "copilot"):
        response = client.get(f"/api/agent/specs/{name}")
        assert response.status_code == 200, response.text
        payload = response.json()
        assert "create_view" in payload["tools"]
        assert payload["version"] == EXPECTED_VERSIONS[name]


# ---------------------------------------------------------------------------
# 错误路径
# ---------------------------------------------------------------------------
def test_get_unknown_spec_is_404(make_client, tmp_settings):
    client = _specs_client(make_client)

    response = client.get("/api/agent/specs/no-such-spec")
    assert response.status_code == 404
    error = _error(response)
    assert error["code"] == "AGENT_SPEC_NOT_FOUND"
    assert (
        error["message"]
        == "没有名为 no-such-spec 的代理定义，可用的名字见代理定义列表"
    )


def test_specs_are_unavailable_without_a_registry(make_client, tmp_settings):
    client = _specs_client(make_client, with_registry=False)

    for response in (
        client.get("/api/agent/specs"),
        client.get("/api/agent/specs/recon"),
    ):
        assert response.status_code == 503
        error = _error(response)
        assert error["code"] == "SERVICE_UNAVAILABLE"
        assert error["message"] == "代理定义还在加载，请稍后重试"


# ---------------------------------------------------------------------------
# 鉴权与 OpenAPI
# ---------------------------------------------------------------------------
@pytest.mark.parametrize("tmp_settings", ["agent-spec-secret"], indirect=True)
def test_specs_require_the_token_when_one_is_configured(make_client, tmp_settings):
    client = _specs_client(make_client)
    deps.reset_rate_limiter()
    try:
        for response in (
            client.get("/api/agent/specs"),
            client.get("/api/agent/specs/recon"),
        ):
            assert response.status_code == 401, response.text
            assert _error(response)["code"] == "UNAUTHORIZED"

        headers = {"X-Token": "agent-spec-secret"}
        assert client.get("/api/agent/specs", headers=headers).status_code == 200
        assert client.get("/api/agent/specs/recon", headers=headers).status_code == 200
    finally:
        deps.reset_rate_limiter()


def test_spec_routes_publish_pydantic_models_in_openapi(tmp_settings):
    schema = create_app().openapi()
    paths = schema["paths"]

    list_operation = paths["/api/agent/specs"]["get"]
    detail_operation = paths["/api/agent/specs/{name}"]["get"]
    assert list_operation["operationId"] == "agent_list_specs"
    assert detail_operation["operationId"] == "agent_get_spec"
    assert list_operation["summary"] == "读取代理定义列表"
    assert detail_operation["summary"] == "读取代理定义详情"

    def resolved(operation) -> str:
        content = operation["responses"]["200"]["content"]["application/json"]
        return content["schema"]["$ref"].rsplit("/", 1)[-1]

    assert resolved(list_operation) == "SpecList"
    assert resolved(detail_operation) == "SpecDetail"

    components = schema["components"]["schemas"]
    assert set(components["SpecSummary"]["properties"]) == LIST_FIELDS
    assert set(components["SpecDetail"]["properties"]) == DETAIL_FIELDS
    assert "AGENT_SPEC_NOT_FOUND" in detail_operation["responses"]["404"]["description"]
    assert "SERVICE_UNAVAILABLE" in list_operation["responses"]["503"]["description"]
