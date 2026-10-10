"""知识库 REST 契约（M18 规格 §4.1）：四个端点、鉴权、错误码与请求模型约束。"""

from __future__ import annotations

import asyncio
import logging

import pytest
from fastapi import FastAPI
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncEngine
from sqlmodel import SQLModel

import maa_api.db.models  # noqa: F401
from maa_api.agent.knowledge import (
    ENTRY_ID_PATTERN,
    KNOWLEDGE_KINDS,
    MAX_BODY_CHARS,
    MAX_TITLE_CHARS,
    MAX_WHEN_TO_USE_CHARS,
    KnowledgeService,
)
from maa_api.api import deps
from maa_api.api.errors import register_exception_handlers
from maa_api.api.routers import knowledge as knowledge_router
from maa_api.db import session as db_session
from maa_api.db.models import AgentAudit

BUILTIN_IDS = {
    # M18 的 5 条
    "account-resources",
    "screen-home",
    "screen-depot",
    "gacha-conversion",
    "glossary",
    # M19 新增的 26 条
    "annihilation-weekly",
    "certificate-shops",
    "currency-sources",
    "drop-efficiency",
    "event-and-rerun",
    "glossary-terms",
    "headhunt-rules",
    "infrast-mechanics",
    "operator-classes",
    "operator-progression",
    "playbook-daily-weekly",
    "playbook-drop-lookup",
    "playbook-infrast-shift",
    "playbook-promotion-planning",
    "playbook-recruit",
    "playbook-sanity-planning",
    "recruit-tags",
    "sanity-system",
    "screen-headhunt",
    "screen-infrast",
    "screen-operator",
    "screen-recruit",
    "screen-store",
    "screen-terminal",
    "stage-rules",
    "trust-and-potential",
    # M24 新增的 1 条
    "activity-stage-codes",
    # M32 新增的 1 条
    "task-preconditions",
}
#: 33 条内置条目在列表里的顺序（M32 起；M24 起为 32 条）：先 kind（playbook/screen/rule/glossary/profile），
#: 同 kind 按 id。
BUILTIN_LIST_ORDER = [
    "account-resources",
    "playbook-daily-weekly",
    "playbook-drop-lookup",
    "playbook-infrast-shift",
    "playbook-promotion-planning",
    "playbook-recruit",
    "playbook-sanity-planning",
    "task-preconditions",
    "screen-depot",
    "screen-headhunt",
    "screen-home",
    "screen-infrast",
    "screen-operator",
    "screen-recruit",
    "screen-store",
    "screen-terminal",
    "activity-stage-codes",
    "annihilation-weekly",
    "certificate-shops",
    "currency-sources",
    "drop-efficiency",
    "event-and-rerun",
    "gacha-conversion",
    "headhunt-rules",
    "infrast-mechanics",
    "operator-classes",
    "operator-progression",
    "recruit-tags",
    "sanity-system",
    "stage-rules",
    "trust-and-potential",
    "glossary",
    "glossary-terms",
]
#: 注入段的 kind 分组（规格 §4.1）：索引三项在前，全文两项在后。
INDEX_KINDS = ("playbook", "screen", "rule")
FULLTEXT_KINDS = ("glossary", "profile")
LIST_FIELDS = {
    "id",
    "kind",
    "title",
    "when_to_use",
    "enabled",
    "source",
    "modified",
    "body_chars",
    "updated_at",
}
DETAIL_FIELDS = (LIST_FIELDS - {"body_chars"}) | {"body", "builtin"}
CUSTOM_PAYLOAD = {
    "kind": "profile",
    "title": "我的账号",
    "when_to_use": "用户提到默认账号或偏好时",
    "body": "默认账号：官服。抽卡资源默认报不含源石与含源石两档。",
    "enabled": True,
}


def _knowledge_client(isolated_db: AsyncEngine, make_client):
    async def create_schema():
        async with isolated_db.begin() as connection:
            await connection.run_sync(SQLModel.metadata.create_all)

    asyncio.run(create_schema())
    isolated_db.sync_engine.dispose()
    app = FastAPI()
    register_exception_handlers(app)
    app.include_router(knowledge_router.router)
    # 生产装配同款：服务自己拿 session_factory（这里已被 isolated_db 换成临时库）。
    app.state.knowledge_service = KnowledgeService(db_session.session_factory)
    return make_client(app)


def _error(response):
    return response.json()["error"]


def _entry_body(client, entry_id: str) -> dict:
    """读一条内置条目的 PUT 请求体（完整生效内容）。"""
    detail = client.get(f"/api/agent/knowledge/{entry_id}").json()
    return {
        "kind": detail["kind"],
        "title": detail["title"],
        "when_to_use": detail["when_to_use"],
        "body": detail["body"],
        "enabled": detail["enabled"],
    }


# ---------------------------------------------------------------------------
# 成功路径
# ---------------------------------------------------------------------------
def test_knowledge_list_returns_summaries_without_bodies(
    isolated_db, make_client, tmp_settings
):
    client = _knowledge_client(isolated_db, make_client)

    response = client.get("/api/agent/knowledge")
    assert response.status_code == 200
    payload = response.json()
    assert payload["total"] == len(BUILTIN_IDS) == len(payload["items"])
    assert [item["id"] for item in payload["items"]] == BUILTIN_LIST_ORDER
    for item in payload["items"]:
        assert set(item) == LIST_FIELDS
        assert item["source"] == "builtin"
        assert item["modified"] is False
        assert item["enabled"] is True
        assert item["body_chars"] > 0
        assert item["updated_at"] is None


def test_knowledge_detail_includes_the_body_and_the_builtin_snapshot(
    isolated_db, make_client, tmp_settings
):
    client = _knowledge_client(isolated_db, make_client)

    response = client.get("/api/agent/knowledge/account-resources")
    assert response.status_code == 200
    payload = response.json()
    assert set(payload) == DETAIL_FIELDS
    assert payload["kind"] == "playbook"
    assert payload["title"] == "查询账号资源"
    assert payload["body"].startswith("## 前置判断")
    # 内置条目另附原文，供界面对比与恢复；未被修改时与生效值一致。
    assert payload["builtin"]["id"] == "account-resources"
    assert payload["builtin"]["body"] == payload["body"]

    missing = client.get("/api/agent/knowledge/no-such-entry")
    assert missing.status_code == 404
    assert _error(missing)["code"] == "KNOWLEDGE_NOT_FOUND"
    assert _error(missing)["details"] == {"entry_id": "no-such-entry"}


def test_knowledge_custom_entry_round_trip(isolated_db, make_client, tmp_settings):
    client = _knowledge_client(isolated_db, make_client)

    created = client.put("/api/agent/knowledge/my-profile", json=CUSTOM_PAYLOAD)
    assert created.status_code == 200
    payload = created.json()
    assert payload["id"] == "my-profile"
    assert payload["source"] == "custom"
    assert payload["modified"] is False
    assert payload["builtin"] is None
    assert payload["body"] == CUSTOM_PAYLOAD["body"]
    assert payload["updated_at"] is not None

    listed = client.get("/api/agent/knowledge").json()
    assert listed["total"] == len(BUILTIN_IDS) + 1
    # profile 排在内置的全部 glossary 之后（kind 顺序，规格 §4.1）。
    glossary_ids = sorted(
        item["id"] for item in listed["items"] if item["kind"] == "glossary"
    )
    assert listed["items"][-1]["kind"] == "profile"
    assert listed["items"][-1]["id"] == "my-profile"
    assert listed["items"][-2]["id"] == glossary_ids[-1]
    assert listed["items"][-1]["body_chars"] == len(CUSTOM_PAYLOAD["body"])

    deleted = client.delete("/api/agent/knowledge/my-profile")
    assert deleted.status_code == 204
    assert deleted.content == b""
    assert client.get("/api/agent/knowledge/my-profile").status_code == 404
    assert client.delete("/api/agent/knowledge/my-profile").status_code == 404


def test_knowledge_put_without_enabled_defaults_to_enabled(
    isolated_db, make_client, tmp_settings
):
    client = _knowledge_client(isolated_db, make_client)

    created = client.put(
        "/api/agent/knowledge/my-profile",
        json={key: value for key, value in CUSTOM_PAYLOAD.items() if key != "enabled"},
    )
    assert created.status_code == 200
    assert created.json()["enabled"] is True


def test_knowledge_override_then_restore_a_builtin_entry(
    isolated_db, make_client, tmp_settings
):
    client = _knowledge_client(isolated_db, make_client)
    original = _entry_body(client, "screen-home")

    changed = client.put(
        "/api/agent/knowledge/screen-home",
        json={**original, "title": "我的首页布局"},
    )
    assert changed.status_code == 200
    modified = changed.json()
    assert modified["source"] == "builtin"
    assert modified["modified"] is True
    assert modified["title"] == "我的首页布局"
    assert modified["when_to_use"] == original["when_to_use"]
    assert modified["body"] == original["body"]
    assert modified["updated_at"] is not None
    assert modified["builtin"]["title"] == original["title"]
    items = client.get("/api/agent/knowledge").json()["items"]
    assert items[BUILTIN_LIST_ORDER.index("screen-home")]["modified"] is True

    disabled = client.put(
        "/api/agent/knowledge/screen-home", json={**original, "enabled": False}
    )
    assert disabled.status_code == 200
    assert disabled.json()["enabled"] is False
    assert disabled.json()["modified"] is True
    assert disabled.json()["builtin"]["title"] == original["title"]

    restored = client.put(
        "/api/agent/knowledge/screen-home", json={**original, "enabled": True}
    )
    assert restored.status_code == 200
    back = restored.json()
    assert back["modified"] is False
    assert back["enabled"] is True
    assert back["title"] == original["title"]
    assert back["updated_at"] is None

    # 未被修改的内置条目不能删；改过之后 DELETE 就是「恢复默认」。
    assert client.delete("/api/agent/knowledge/screen-home").status_code == 409
    assert client.put(
        "/api/agent/knowledge/screen-home", json={**original, "title": "临时标题"}
    ).status_code == 200
    assert client.delete("/api/agent/knowledge/screen-home").status_code == 204
    assert client.get("/api/agent/knowledge/screen-home").json()["title"] == original["title"]


def test_knowledge_changing_a_builtin_kind_is_a_conflict(
    isolated_db, make_client, tmp_settings
):
    client = _knowledge_client(isolated_db, make_client)

    response = client.put(
        "/api/agent/knowledge/screen-home",
        json={**_entry_body(client, "screen-home"), "kind": "rule"},
    )
    assert response.status_code == 409
    assert _error(response)["code"] == "KNOWLEDGE_BUILTIN_READONLY"
    assert _error(response)["details"]["entry_id"] == "screen-home"

    untouched = client.get("/api/agent/knowledge/screen-home").json()
    assert untouched["kind"] == "screen" and untouched["modified"] is False


# ---------------------------------------------------------------------------
# 校验与上限
# ---------------------------------------------------------------------------
@pytest.mark.parametrize(
    "field, value",
    [
        ("kind", "unknown-kind"),
        ("kind", "PLAYBOOK"),
        ("title", ""),
        ("title", "题" * (MAX_TITLE_CHARS + 1)),
        ("when_to_use", ""),
        ("when_to_use", "用" * (MAX_WHEN_TO_USE_CHARS + 1)),
        ("body", ""),
        ("body", "文" * (MAX_BODY_CHARS + 1)),
        ("enabled", "maybe"),
    ],
)
def test_knowledge_field_violations_return_native_validation_errors(
    isolated_db, make_client, tmp_settings, field, value
):
    client = _knowledge_client(isolated_db, make_client)

    response = client.put(
        "/api/agent/knowledge/my-profile", json={**CUSTOM_PAYLOAD, field: value}
    )
    assert response.status_code == 422
    error = _error(response)
    assert error["code"] == "VALIDATION_ERROR"
    # FastAPI 原生形状：details.fields 是逐字段的错误列表，而不是服务层兜底的
    # {entry_id, field, reason}（Task 4 裁决 10）。
    assert isinstance(error["details"]["fields"], list) and error["details"]["fields"]
    assert "reason" not in error["details"]
    assert client.get("/api/agent/knowledge/my-profile").status_code == 404


def test_knowledge_rejects_unknown_body_fields(
    isolated_db, make_client, tmp_settings
):
    client = _knowledge_client(isolated_db, make_client)

    response = client.put(
        "/api/agent/knowledge/my-profile", json={**CUSTOM_PAYLOAD, "remark": "多余字段"}
    )
    assert response.status_code == 422
    assert _error(response)["code"] == "VALIDATION_ERROR"


@pytest.mark.parametrize("entry_id", ["Bad_ID", "x", "a" * 49, "-leading", "有中文"])
def test_knowledge_rejects_ids_that_are_not_url_segments(
    isolated_db, make_client, tmp_settings, entry_id
):
    client = _knowledge_client(isolated_db, make_client)

    for response in (
        client.get(f"/api/agent/knowledge/{entry_id}"),
        client.delete(f"/api/agent/knowledge/{entry_id}"),
        client.put(f"/api/agent/knowledge/{entry_id}", json=CUSTOM_PAYLOAD),
    ):
        assert response.status_code == 422, response.text
        assert _error(response)["code"] == "VALIDATION_ERROR"


#: 被删掉的旧启用条目数上限（M20）：写满它再多写一条，过去是 409，现在必须成功。
REMOVED_ENABLED_ENTRIES_CAP = 50
#: 被删掉的旧全文预算（字）。
REMOVED_FULLTEXT_CHARS_CAP = 3_000


def test_the_51st_enabled_entry_is_accepted_now(
    isolated_db, make_client, tmp_settings
):
    """第 51 条启用条目不再 409（旧实现抛 KNOWLEDGE_LIMIT_EXCEEDED）。"""
    client = _knowledge_client(isolated_db, make_client)
    payload = {
        "kind": "rule",
        "title": "自定义条目",
        "when_to_use": "测试条目数上限已去掉",
        "body": "正文",
        "enabled": True,
    }
    for index in range(REMOVED_ENABLED_ENTRIES_CAP - len(BUILTIN_IDS)):
        response = client.put(f"/api/agent/knowledge/custom-{index:02d}", json=payload)
        assert response.status_code == 200, response.text

    items = client.get("/api/agent/knowledge").json()["items"]
    assert sum(item["enabled"] for item in items) == REMOVED_ENABLED_ENTRIES_CAP

    written = client.put("/api/agent/knowledge/custom-overflow", json=payload)
    assert written.status_code == 200, written.text
    assert written.json()["enabled"] is True
    assert client.get("/api/agent/knowledge/custom-overflow").status_code == 200
    items = client.get("/api/agent/knowledge").json()["items"]
    assert sum(item["enabled"] for item in items) == REMOVED_ENABLED_ENTRIES_CAP + 1


def test_glossary_bodies_may_exceed_the_removed_fulltext_budget(
    isolated_db, make_client, tmp_settings
):
    """术语表正文超过旧预算（3,000 字）不再 409，整段写入并计入列表字数。"""
    client = _knowledge_client(isolated_db, make_client)
    # 内置的 glossary + glossary-terms 默认全部启用，用列表的 body_chars 现算。
    builtin_fulltext = sum(
        item["body_chars"]
        for item in client.get("/api/agent/knowledge").json()["items"]
        if item["kind"] in FULLTEXT_KINDS and item["enabled"]
    )
    assert builtin_fulltext > 0

    body = "字" * REMOVED_FULLTEXT_CHARS_CAP
    written = client.put(
        "/api/agent/knowledge/custom-glossary",
        json={
            "kind": "glossary",
            "title": "自建术语表",
            "when_to_use": "术语表",
            "body": body,
            "enabled": True,
        },
    )
    assert written.status_code == 200, written.text
    assert written.json()["body"] == body

    fulltext = sum(
        item["body_chars"]
        for item in client.get("/api/agent/knowledge").json()["items"]
        if item["kind"] in FULLTEXT_KINDS and item["enabled"]
    )
    assert fulltext == builtin_fulltext + len(body) > REMOVED_FULLTEXT_CHARS_CAP


# ---------------------------------------------------------------------------
# 鉴权、服务日志与 OpenAPI
# ---------------------------------------------------------------------------
@pytest.mark.parametrize("tmp_settings", ["knowledge-secret"], indirect=True)
def test_knowledge_endpoints_require_auth(isolated_db, make_client, tmp_settings):
    client = _knowledge_client(isolated_db, make_client)
    deps.reset_rate_limiter()
    try:
        unauthenticated = [
            client.get("/api/agent/knowledge"),
            client.get("/api/agent/knowledge/glossary"),
            client.put("/api/agent/knowledge/my-profile", json=CUSTOM_PAYLOAD),
            client.delete("/api/agent/knowledge/glossary"),
        ]
        for response in unauthenticated:
            assert response.status_code == 401, response.text
            assert _error(response)["code"] == "UNAUTHORIZED"

        headers = {"X-Token": "knowledge-secret"}
        assert client.get("/api/agent/knowledge", headers=headers).status_code == 200
    finally:
        deps.reset_rate_limiter()


def test_knowledge_writes_emit_one_service_log_and_no_agent_audit(
    isolated_db, make_client, tmp_settings, caplog
):
    client = _knowledge_client(isolated_db, make_client)

    with caplog.at_level(logging.INFO, logger="maa_api.api.routers.knowledge"):
        assert (
            client.put("/api/agent/knowledge/my-profile", json=CUSTOM_PAYLOAD).status_code
            == 200
        )
        assert client.delete("/api/agent/knowledge/my-profile").status_code == 204

    messages = [
        record.getMessage()
        for record in caplog.records
        if record.name == "maa_api.api.routers.knowledge"
    ]
    assert sum("my-profile" in message for message in messages) == 2
    assert any("my-profile" in message and "写入" in message for message in messages)
    assert any("my-profile" in message and "删除" in message for message in messages)

    async def audit_count() -> int:
        async with db_session.session_factory() as session:
            return (
                await session.execute(select(func.count()).select_from(AgentAudit))
            ).scalar_one()

    assert asyncio.run(audit_count()) == 0


def test_knowledge_openapi_declares_paths_error_codes_and_constraints(
    isolated_db, make_client, tmp_settings
):
    client = _knowledge_client(isolated_db, make_client)
    spec = client.get("/openapi.json").json()
    paths = spec["paths"]

    assert set(paths["/api/agent/knowledge"]) == {"get"}
    entry_paths = paths["/api/agent/knowledge/{entry_id}"]
    assert set(entry_paths) == {"get", "put", "delete"}

    def declared(path: str, method: str, status_code: int) -> str:
        return paths[path][method]["responses"][str(status_code)]["description"]

    assert "UNAUTHORIZED" in declared("/api/agent/knowledge", "get", 401)

    assert "KNOWLEDGE_NOT_FOUND" in declared(
        "/api/agent/knowledge/{entry_id}", "get", 404
    )
    assert "VALIDATION_ERROR" in declared(
        "/api/agent/knowledge/{entry_id}", "get", 422
    )
    assert "UNAUTHORIZED" in declared("/api/agent/knowledge/{entry_id}", "get", 401)

    put_conflict = declared("/api/agent/knowledge/{entry_id}", "put", 409)
    assert "KNOWLEDGE_BUILTIN_READONLY" in put_conflict
    # M20：条目数与全文段字数不再有上限，这个错误码已从代码与 OpenAPI 中消失。
    assert "KNOWLEDGE_LIMIT_EXCEEDED" not in put_conflict
    assert "VALIDATION_ERROR" in declared(
        "/api/agent/knowledge/{entry_id}", "put", 422
    )

    assert "KNOWLEDGE_NOT_FOUND" in declared(
        "/api/agent/knowledge/{entry_id}", "delete", 404
    )
    assert "KNOWLEDGE_BUILTIN_READONLY" in declared(
        "/api/agent/knowledge/{entry_id}", "delete", 409
    )

    # 路径参数的正则：非法 id 在进业务层之前就被拦成 422。
    parameters = {
        item["name"]: item
        for item in paths["/api/agent/knowledge/{entry_id}"]["put"]["parameters"]
    }
    assert parameters["entry_id"]["schema"]["pattern"] == ENTRY_ID_PATTERN.pattern

    write = spec["components"]["schemas"]["KnowledgeWriteRequest"]
    assert write["additionalProperties"] is False
    assert write["required"] == ["kind", "title", "when_to_use", "body"]
    fields = write["properties"]
    assert fields["kind"]["enum"] == list(KNOWLEDGE_KINDS)
    assert fields["title"]["maxLength"] == MAX_TITLE_CHARS
    assert fields["when_to_use"]["maxLength"] == MAX_WHEN_TO_USE_CHARS
    assert fields["body"]["maxLength"] == MAX_BODY_CHARS
    assert fields["enabled"]["default"] is True

    summary = spec["components"]["schemas"]["KnowledgeSummary"]["properties"]
    assert set(summary) == LIST_FIELDS
    assert summary["updated_at"]["anyOf"][0]["format"] == "date-time"
    assert (
        spec["components"]["schemas"]["KnowledgePage"]["properties"]["total"][
            "type"
        ]
        == "integer"
    )
