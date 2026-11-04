"""技能 REST 契约（M78 规格 §4.3）：四个端点、鉴权、错误码与装配。

前四条用例照 spec §6 的清单：列表 / 详情形状、PUT / DELETE 往返、400 / 404 / 409 /
422、四个路由 401。最后一条从 ``create_app()`` 的真实 lifespan 断言装配：
``app.state.skill_registry`` 建好、预加载校验通过、``GET`` 返回 9 个内置技能。
"""

from __future__ import annotations

import asyncio

import pytest
from fastapi import FastAPI
from sqlalchemy.ext.asyncio import AsyncEngine
from sqlmodel import SQLModel

import maa_api.db.models  # noqa: F401  # 注册 agent_skill 表，便于 create_all
from maa_api.agent.skills import SkillRegistry
from maa_api.agent.specs.registry import AgentSpecRegistry
from maa_api.agent.tools import build_registry
from maa_api.api import deps
from maa_api.api.errors import register_exception_handlers
from maa_api.api.routers import skills as skills_router
from maa_api.db import session as db_session

#: 9 个内置技能，按 id 排序（列表契约）。
BUILTIN_IDS = [
    "account-resources",
    "playbook-daily-weekly",
    "playbook-drop-lookup",
    "playbook-infrast-shift",
    "playbook-promotion-planning",
    "playbook-recruit",
    "playbook-sanity-planning",
    "playbook-stage-clear",
    "task-preconditions",
]
LIST_FIELDS = {
    "id",
    "title",
    "description",
    "tools",
    "agents",
    "source",
    "modified",
    "status",
    "enabled",
    "body_chars",
    "files",
    "version",
    "updated_at",
}
DETAIL_FIELDS = LIST_FIELDS | {"body", "builtin"}
CUSTOM_PAYLOAD = {
    "title": "我的打法",
    "description": "用户问起我的固定打法时",
    "body": "## 步骤\n1. 先看首页。\n",
    "tools": ["get_screenshot"],
    "agents": ["general"],
    "enabled": True,
}


def _skills_client(isolated_db: AsyncEngine, make_client):
    """现搭只含技能路由的 app，并把真实注册表挂到 ``app.state``。"""

    async def create_schema():
        async with isolated_db.begin() as connection:
            await connection.run_sync(SQLModel.metadata.create_all)

    asyncio.run(create_schema())
    isolated_db.sync_engine.dispose()
    app = FastAPI()
    register_exception_handlers(app)
    app.include_router(skills_router.router)
    tool_registry = build_registry()
    spec_registry = AgentSpecRegistry(tool_registry)
    spec_registry.load()
    app.state.skill_registry = SkillRegistry(
        db_session.session_factory,
        tool_names={definition.name for definition in tool_registry.list()},
        agent_kinds={spec.name: spec.kind for spec in spec_registry.list()},
    )
    return make_client(app)


def _error(response):
    return response.json()["error"]


def _body(client, skill_id: str) -> dict:
    """读一条内置技能的 PUT 请求体（完整生效内容）。"""
    detail = client.get(f"/api/agent/skills/{skill_id}").json()
    return {
        "title": detail["title"],
        "description": detail["description"],
        "body": detail["body"],
        "tools": detail["tools"],
        "agents": detail["agents"],
        "enabled": detail["enabled"],
    }


# ---------------------------------------------------------------------------
# 成功路径
# ---------------------------------------------------------------------------
def test_list_and_detail_shapes(isolated_db, make_client, tmp_settings):
    client = _skills_client(isolated_db, make_client)

    response = client.get("/api/agent/skills")
    assert response.status_code == 200
    payload = response.json()
    assert set(payload) == {"items"}
    assert [item["id"] for item in payload["items"]] == BUILTIN_IDS
    for item in payload["items"]:
        assert set(item) == LIST_FIELDS
        assert item["source"] == "builtin"
        assert item["modified"] is False
        assert item["status"] == "active"
        assert item["enabled"] is True
        assert item["body_chars"] > 0
        assert item["version"] == 1
        assert item["files"] == []
        assert item["updated_at"] is None

    detail = client.get("/api/agent/skills/playbook-recruit")
    assert detail.status_code == 200
    body = detail.json()
    assert set(body) == DETAIL_FIELDS
    assert body["id"] == "playbook-recruit"
    assert body["title"] == "公开招募实操"
    assert body["source"] == "builtin"
    assert body["modified"] is False
    assert body["body"].strip()
    # 内置技能另附原文，供界面对比与恢复；未被修改时与生效值一致。
    assert body["builtin"]["id"] == "playbook-recruit"
    assert body["builtin"]["body"] == body["body"]
    assert body["builtin"]["version"] == 1

    missing = client.get("/api/agent/skills/no-such-skill")
    assert missing.status_code == 404
    assert _error(missing)["code"] == "SKILL_NOT_FOUND"
    assert _error(missing)["details"]["skill_id"] == "no-such-skill"


def test_put_and_delete_round_trip(isolated_db, make_client, tmp_settings):
    client = _skills_client(isolated_db, make_client)
    original = _body(client, "playbook-recruit")

    # 覆盖内置技能：只改标题，响应带改后的生效值与内置原文。
    changed = client.put(
        "/api/agent/skills/playbook-recruit",
        json={**original, "title": "公开招募实操（改）"},
    )
    assert changed.status_code == 200
    payload = changed.json()
    assert payload["source"] == "builtin"
    assert payload["modified"] is True
    assert payload["title"] == "公开招募实操（改）"
    assert payload["builtin"]["title"] == "公开招募实操"
    assert payload["updated_at"].endswith("Z")
    listed = client.get("/api/agent/skills").json()["items"]
    assert listed[BUILTIN_IDS.index("playbook-recruit")]["modified"] is True

    # 恢复默认：DELETE 删掉覆盖行，回到内置内容。
    restored = client.delete("/api/agent/skills/playbook-recruit")
    assert restored.status_code == 204
    assert restored.content == b""
    back = client.get("/api/agent/skills/playbook-recruit").json()
    assert back["title"] == "公开招募实操"
    assert back["modified"] is False
    assert back["builtin"]["title"] == "公开招募实操"
    assert back["updated_at"] is None

    # 新建自定义技能：时间是带 Z 的 UTC，DELETE 后再读是 404。
    created = client.put("/api/agent/skills/my-playbook", json=CUSTOM_PAYLOAD)
    assert created.status_code == 200
    custom = created.json()
    assert custom["id"] == "my-playbook"
    assert custom["source"] == "user"
    assert custom["modified"] is False
    assert custom["builtin"] is None
    assert custom["version"] is None
    assert custom["body"] == CUSTOM_PAYLOAD["body"]
    assert custom["updated_at"].endswith("Z")
    assert len(client.get("/api/agent/skills").json()["items"]) == len(BUILTIN_IDS) + 1

    deleted = client.delete("/api/agent/skills/my-playbook")
    assert deleted.status_code == 204
    assert client.get("/api/agent/skills/my-playbook").status_code == 404
    assert client.delete("/api/agent/skills/my-playbook").status_code == 404


def test_errors(isolated_db, make_client, tmp_settings):
    client = _skills_client(isolated_db, make_client)
    original = _body(client, "playbook-recruit")

    # PUT 的 id 不合法（路径参数不加正则）→ 400 SKILL_INVALID，不是 422。
    invalid_id = client.put(
        "/api/agent/skills/Bad_ID", json={**original, "title": "临时标题"}
    )
    assert invalid_id.status_code == 400
    assert _error(invalid_id)["code"] == "SKILL_INVALID"
    assert _error(invalid_id)["details"]["field"] == "id"

    # GET / DELETE 的 id 不合法 → 404 SKILL_NOT_FOUND，也不是 422。
    assert client.get("/api/agent/skills/Bad_ID").status_code == 404
    assert client.delete("/api/agent/skills/Bad_ID").status_code == 404

    # 未知工具、未知代理：400 SKILL_INVALID，details 带 field 与 unknown。
    bad_tool = client.put(
        "/api/agent/skills/playbook-recruit",
        json={**original, "tools": [*original["tools"], "nope"]},
    )
    assert bad_tool.status_code == 400
    assert _error(bad_tool)["code"] == "SKILL_INVALID"
    assert _error(bad_tool)["details"]["field"] == "tools"
    assert _error(bad_tool)["details"]["unknown"] == ["nope"]
    bad_agent = client.put(
        "/api/agent/skills/playbook-recruit",
        json={**original, "agents": ["researcher"]},
    )
    assert bad_agent.status_code == 400
    assert _error(bad_agent)["details"]["unknown"] == ["researcher"]

    # 没有覆盖的内置技能不能删：409 SKILL_BUILTIN_READONLY。
    readonly = client.delete("/api/agent/skills/playbook-recruit")
    assert readonly.status_code == 409
    assert _error(readonly)["code"] == "SKILL_BUILTIN_READONLY"
    assert _error(readonly)["details"]["skill_id"] == "playbook-recruit"

    # 未知字段与缺必填：FastAPI 原生 422 VALIDATION_ERROR。
    unknown_field = client.put(
        "/api/agent/skills/my-playbook", json={**CUSTOM_PAYLOAD, "remark": "多余"}
    )
    assert unknown_field.status_code == 422
    assert _error(unknown_field)["code"] == "VALIDATION_ERROR"
    missing_field = client.put(
        "/api/agent/skills/my-playbook",
        json={key: value for key, value in CUSTOM_PAYLOAD.items() if key != "body"},
    )
    assert missing_field.status_code == 422


@pytest.mark.parametrize("tmp_settings", ["s3cret"], indirect=True)
def test_routes_require_auth(isolated_db, make_client, tmp_settings):
    deps.reset_rate_limiter()
    client = _skills_client(isolated_db, make_client)

    requests = (
        ("get", "/api/agent/skills", {}),
        ("get", "/api/agent/skills/playbook-recruit", {}),
        ("put", "/api/agent/skills/playbook-recruit", {"json": CUSTOM_PAYLOAD}),
        ("delete", "/api/agent/skills/playbook-recruit", {}),
    )
    for method, path, kwargs in requests:
        response = getattr(client, method)(path, **kwargs)
        assert response.status_code == 401, (method, path, response.text)
        assert _error(response)["code"] == "UNAUTHORIZED"
    deps.reset_rate_limiter()


def test_real_lifespan_serves_nine_skills(tmp_settings, isolated_db):
    """真实 lifespan 下注册表装配完成，列表返回 9 个内置技能。"""

    from fastapi.testclient import TestClient

    from maa_api.main import create_app

    app = create_app()
    with TestClient(app, raise_server_exceptions=False) as client:
        assert isinstance(getattr(app.state, "skill_registry", None), SkillRegistry)
        assert app.state.agent_runtime.skill_registry is app.state.skill_registry

        response = client.get("/api/agent/skills")
        assert response.status_code == 200
        payload = response.json()
        assert [item["id"] for item in payload["items"]] == BUILTIN_IDS
