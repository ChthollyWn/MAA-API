"""记忆 REST 契约（M79 规格 §4.3、§4.6、§6、§7）。

覆盖 spec §6 的 ``test_review_missing_is_404``，并补列表过滤与分页、审核的状态机
（批准 / 改后批准 / 拒绝 / 第二次 409）、取代关系、更新与删除、401、时间 ``Z``，
以及 spec §7 的脚本化验收中「审核通过 → 下一次总控运行的 system 第 4 段含该句」。

脚本化验收的上半段（工单结束 → 候选进 ``proposed``）已由
``tests/agent/test_memory_extract.py::test_ticket_done_proposes`` 用真实
``TicketService`` + FakeLLM 覆盖；这里搭不起同一套工单装配，所以只把下半段
（审核 → 注入）做成联动断言，两点合起来即 spec §7 的链路。

应用按生产装配的最小集现搭：只装记忆路由、统一错误处理器与
``app.state.memory_service``；数据写进 ``isolated_db`` 的临时库。所有数据库操作都
经 ``TestClient`` 的 portal 跑在同一个事件循环里（避免 aiosqlite 连接跨循环）。
"""

from __future__ import annotations

import asyncio
from types import SimpleNamespace
from typing import Any

import pytest
from fastapi import FastAPI
from sqlmodel import SQLModel

import maa_api.db.models  # noqa: F401  # 注册全部表，create_all 才能建出 memory_item
from maa_api.agent.context.layout import ContextLayout, ContextParts, Segment
from maa_api.agent.context.tokens import TokenEstimator
from maa_api.agent.harness.context import ModelRequest, RunContext
from maa_api.agent.memory.inject import MEMORY_HEADER, MemoryInjectionMiddleware
from maa_api.agent.memory.service import MemoryService
from maa_api.agent.specs.registry import builtin_spec
from maa_api.api import deps
from maa_api.api.errors import register_exception_handlers
from maa_api.api.routers import memories as memories_router
from maa_api.db import session as db_session
from maa_api.services.webpush_service import WebPushService

#: ``MemoryOut`` 的全部列（规格 §4.3、03 §10.2）；没有 ``pinned``。
MEMORY_FIELDS = {
    "id",
    "kind",
    "subject",
    "content",
    "status",
    "confidence",
    "source_ticket_id",
    "source_session_id",
    "source_run_id",
    "supersedes_id",
    "expires_at",
    "last_used_at",
    "use_count",
    "reviewed_by",
    "reviewed_at",
    "created_at",
    "updated_at",
}
TIME_FIELDS = ("expires_at", "last_used_at", "reviewed_at", "created_at", "updated_at")


def _memories_app(isolated_db, make_client, settings):
    """建表、装路由与服务；返回 ``(app, client)``。所有 DB 工作经 ``client.portal``。"""
    app = FastAPI()
    register_exception_handlers(app)
    app.include_router(memories_router.router)
    app.state.memory_service = MemoryService(
        db_session.session_factory, settings_provider=lambda: settings
    )
    app.state.context_layout = ContextLayout(TokenEstimator())
    client = make_client(app)
    client.portal.call(_create_schema, isolated_db)
    return app, client


async def _create_schema(isolated_db: Any) -> None:
    async with isolated_db.begin() as connection:
        await connection.run_sync(SQLModel.metadata.create_all)


def _create(client, **kwargs: Any):
    """在 ``TestClient`` 的事件循环里写一条候选（不经过 HTTP，前端没有创建端点）。"""
    service = client.app.state.memory_service
    return client.portal.call(lambda: service.create_candidate(**kwargs))


def _review(client, memory_id: str, approved: bool, content: str | None = None):
    service = client.app.state.memory_service
    return client.portal.call(
        lambda: service.review(memory_id, approved, content)
    )


def _error(response):
    return response.json()["error"]


def _assert_z_fields(payload: dict) -> None:
    """时间字段要么为空，要么以 ``Z`` 结尾（库里是 naive UTC）。"""
    for name in TIME_FIELDS:
        value = payload[name]
        assert value is None or value.endswith("Z"), (name, value)


# ---------------------------------------------------------------------------
# 列表
# ---------------------------------------------------------------------------
def test_list_all_states_filters_and_paginates(isolated_db, make_client, tmp_settings):
    _app, client = _memories_app(isolated_db, make_client, tmp_settings)

    proposed = _create(
        client,
        kind="semantic",
        subject="preference",
        content="刷图不要用源石恢复理智",
        confidence=0.9,
    )
    active = _create(
        client, kind="episodic", subject="stage:1-7", content="上周刷 1-7 刷完了"
    )
    rejected = _create(
        client, kind="procedural", subject="device", content="ADB 掉线先重连一次"
    )
    _review(client, active.id, True)
    _review(client, rejected.id, False)

    # 不传 status 返回全部状态；每项都是 MemoryOut 全列，时间带 Z。
    response = client.get("/api/agent/memories")
    assert response.status_code == 200, response.text
    payload = response.json()
    assert set(payload) == {"items", "total", "page", "size"}
    assert (payload["total"], payload["page"], payload["size"]) == (3, 1, 20)
    assert {item["status"] for item in payload["items"]} == {
        "proposed",
        "active",
        "rejected",
    }
    for item in payload["items"]:
        assert set(item) == MEMORY_FIELDS
        _assert_z_fields(item)
    episodic = next(item for item in payload["items"] if item["id"] == active.id)
    assert episodic["expires_at"].endswith("Z")
    semantic = next(item for item in payload["items"] if item["id"] == proposed.id)
    assert semantic["expires_at"] is None

    # status / kind / q 过滤。
    only_proposed = client.get("/api/agent/memories?status=proposed").json()
    assert [item["id"] for item in only_proposed["items"]] == [proposed.id]
    assert only_proposed["total"] == 1
    only_episodic = client.get("/api/agent/memories?kind=episodic").json()
    assert [item["id"] for item in only_episodic["items"]] == [active.id]
    searched = client.get("/api/agent/memories?q=源石").json()
    assert [item["id"] for item in searched["items"]] == [proposed.id]
    # 未知状态按过滤后为空处理，不报错。
    assert client.get("/api/agent/memories?status=nope").json()["total"] == 0

    # 分页：按 updated_at 倒序，size=1 的第二页是中间那条。
    first = client.get("/api/agent/memories?size=1&page=1").json()
    second = client.get("/api/agent/memories?size=1&page=2").json()
    assert first["total"] == 3 and first["size"] == 1
    assert len(first["items"]) == len(second["items"]) == 1
    assert first["items"][0]["id"] != second["items"][0]["id"]

    # 坏分页：与其它列表端点同口径，400 INVALID_PAGINATION。
    for query in ("page=0", "size=0", "size=201"):
        bad = client.get(f"/api/agent/memories?{query}")
        assert bad.status_code == 400, (query, bad.text)
        assert _error(bad)["code"] == "INVALID_PAGINATION"


# ---------------------------------------------------------------------------
# 审核
# ---------------------------------------------------------------------------
def test_review_missing_is_404(isolated_db, make_client, tmp_settings):
    """spec §6：不存在的记忆报 404 ``MEMORY_NOT_FOUND``（四个动作同口径）。"""
    _app, client = _memories_app(isolated_db, make_client, tmp_settings)

    review = client.post(
        "/api/agent/memories/no-such-memory/review", json={"approved": True}
    )
    assert review.status_code == 404
    assert _error(review)["code"] == "MEMORY_NOT_FOUND"
    assert _error(review)["details"]["memory_id"] == "no-such-memory"

    assert client.put(
        "/api/agent/memories/no-such-memory", json={"content": "改"}
    ).status_code == 404
    assert client.delete("/api/agent/memories/no-such-memory").status_code == 404


def test_review_approve_reject_and_conflict(isolated_db, make_client, tmp_settings, monkeypatch):
    """批准 / 改后批准 / 拒绝；第二次审核 409；全程不调用 Web Push。"""
    _app, client = _memories_app(isolated_db, make_client, tmp_settings)

    pushes: list[Any] = []

    def record_push(self, *args: Any, **kwargs: Any) -> None:
        pushes.append((args, kwargs))

    monkeypatch.setattr(WebPushService, "notify_confirmation", record_push)

    approved = _create(
        client, kind="semantic", subject="preference", content="旧说法：用理智药"
    )
    response = client.post(
        f"/api/agent/memories/{approved.id}/review",
        json={"approved": True, "content": "改后：不用源石，用理智药"},
    )
    assert response.status_code == 200, response.text
    payload = response.json()
    assert payload["status"] == "active"
    assert payload["content"] == "改后：不用源石，用理智药"
    assert payload["reviewed_by"] == "user"
    assert payload["reviewed_at"].endswith("Z")
    _assert_z_fields(payload)

    conflict = client.post(
        f"/api/agent/memories/{approved.id}/review", json={"approved": False}
    )
    assert conflict.status_code == 409
    assert _error(conflict)["code"] == "MEMORY_NOT_PROPOSED"
    assert _error(conflict)["details"]["status"] == "active"

    rejected = _create(
        client, kind="procedural", subject="device", content="ADB 掉线先重连一次"
    )
    rejected_response = client.post(
        f"/api/agent/memories/{rejected.id}/review", json={"approved": False}
    )
    assert rejected_response.status_code == 200
    assert rejected_response.json()["status"] == "rejected"
    assert rejected_response.json()["reviewed_by"] == "user"

    assert pushes == [], "记忆审核不应调用 Web Push"

    # 未知字段与缺 approved：FastAPI 原生 422。
    assert client.post(
        f"/api/agent/memories/{rejected.id}/review",
        json={"approved": True, "remark": "多余"},
    ).status_code == 422
    assert client.post(
        f"/api/agent/memories/{rejected.id}/review", json={}
    ).status_code == 422


def test_review_approves_and_supersedes_old_active(isolated_db, make_client, tmp_settings):
    """批准时若 ``supersedes_id`` 指向生效记忆，旧条改为 superseded。"""
    _app, client = _memories_app(isolated_db, make_client, tmp_settings)

    old = _create(
        client, kind="semantic", subject="preference", content="旧偏好：手动换班"
    )
    _review(client, old.id, True)
    replacement = _create(
        client,
        kind="semantic",
        subject="preference",
        content="新偏好：自动换班",
        supersedes_id=old.id,
        allowed_supersedes=[old.id],
    )
    assert replacement.supersedes_id == old.id

    response = client.post(
        f"/api/agent/memories/{replacement.id}/review", json={"approved": True}
    )
    assert response.status_code == 200, response.text
    listed = {
        item["id"]: item
        for item in client.get("/api/agent/memories").json()["items"]
    }
    assert listed[replacement.id]["status"] == "active"
    assert listed[old.id]["status"] == "superseded"


# ---------------------------------------------------------------------------
# 更新与删除
# ---------------------------------------------------------------------------
def test_update_active_memory(isolated_db, make_client, tmp_settings):
    _app, client = _memories_app(isolated_db, make_client, tmp_settings)
    memory = _create(
        client, kind="semantic", subject="preference", content="原来的内容"
    )
    assert client.put(
        f"/api/agent/memories/{memory.id}", json={"content": "不该能改"}
    ).status_code == 400
    _review(client, memory.id, True)

    updated = client.put(
        f"/api/agent/memories/{memory.id}",
        json={
            "content": "改过的内容",
            "subject": "account",
            "expires_at": "2027-01-01T00:00:00Z",
        },
    )
    assert updated.status_code == 200, updated.text
    payload = updated.json()
    assert payload["content"] == "改过的内容"
    assert payload["subject"] == "account"
    assert payload["expires_at"].endswith("Z")
    _assert_z_fields(payload)

    # 显式传 null 清空过期时间；只改一个字段也合法。
    cleared = client.put(
        f"/api/agent/memories/{memory.id}", json={"expires_at": None}
    )
    assert cleared.status_code == 200
    assert cleared.json()["expires_at"] is None

    # 空请求体、未知字段、坏时间格式。
    assert client.put(
        f"/api/agent/memories/{memory.id}", json={}
    ).status_code == 400
    assert client.put(
        f"/api/agent/memories/{memory.id}", json={"pinned": True}
    ).status_code == 422
    assert client.put(
        f"/api/agent/memories/{memory.id}", json={"expires_at": "不是时间"}
    ).status_code == 422


def test_delete_memory_removes_row(isolated_db, make_client, tmp_settings):
    _app, client = _memories_app(isolated_db, make_client, tmp_settings)
    memory = _create(
        client, kind="semantic", subject="preference", content="待删的记忆"
    )
    _review(client, memory.id, True)

    deleted = client.delete(f"/api/agent/memories/{memory.id}")
    assert deleted.status_code == 204
    assert deleted.content == b""
    assert client.get("/api/agent/memories").json()["total"] == 0
    assert client.delete(f"/api/agent/memories/{memory.id}").status_code == 404


# ---------------------------------------------------------------------------
# 鉴权与装配
# ---------------------------------------------------------------------------
@pytest.mark.parametrize("tmp_settings", ["s3cret"], indirect=True)
def test_routes_require_auth(isolated_db, make_client, tmp_settings):
    deps.reset_rate_limiter()
    _app, client = _memories_app(isolated_db, make_client, tmp_settings)

    requests = (
        ("get", "/api/agent/memories", {}),
        ("post", "/api/agent/memories/x/review", {"json": {"approved": True}}),
        ("put", "/api/agent/memories/x", {"json": {"content": "改"}}),
        ("delete", "/api/agent/memories/x", {}),
    )
    for method, path, kwargs in requests:
        response = getattr(client, method)(path, **kwargs)
        assert response.status_code == 401, (method, path, response.text)
        assert _error(response)["code"] == "UNAUTHORIZED"
    deps.reset_rate_limiter()


def test_real_lifespan_wires_memory_service(tmp_settings, isolated_db):
    """真实 lifespan：``app.state.memory_service`` 建好，四个路由都在。"""
    from fastapi.testclient import TestClient

    from maa_api.main import create_app

    app = create_app()
    paths = set(app.openapi()["paths"])
    for path in (
        "/api/agent/memories",
        "/api/agent/memories/{memory_id}",
        "/api/agent/memories/{memory_id}/review",
    ):
        assert path in paths, path

    with TestClient(app, raise_server_exceptions=False) as client:
        assert isinstance(getattr(app.state, "memory_service", None), MemoryService)
        response = client.get("/api/agent/memories")
        assert response.status_code == 200, response.text
        assert response.json() == {"items": [], "total": 0, "page": 1, "size": 20}
        assert client.delete("/api/agent/memories/nope").status_code == 404


# ---------------------------------------------------------------------------
# 脚本化验收的下半段：审核通过 → 下一次总控运行的 system 第 4 段
# ---------------------------------------------------------------------------
def test_approved_memory_enters_system_segment_4(isolated_db, make_client, tmp_settings):
    _app, client = _memories_app(isolated_db, make_client, tmp_settings)
    memory = _create(
        client,
        kind="semantic",
        subject="preference",
        content="以后刷图不要用源石恢复理智",
        confidence=0.9,
    )
    assert client.get("/api/agent/memories?status=proposed").json()["total"] == 1

    approved = client.post(
        f"/api/agent/memories/{memory.id}/review", json={"approved": True}
    )
    assert approved.status_code == 200
    assert approved.json()["status"] == "active"

    middleware = MemoryInjectionMiddleware(
        session_factory=db_session.session_factory,
        settings_provider=lambda: tmp_settings,
    )
    request = ModelRequest(
        messages=[],
        tools=[],
        profile=SimpleNamespace(key="test", dynamic_context_position="tail_system"),
        meta={
            "layout_parts": ContextParts(
                system=[Segment("spec", "代理定义正文")],
                tools=[],
                position="tail_system",
            )
        },
    )
    run_id = "run-m79-g4"
    context = RunContext(
        run_id=run_id,
        root_run_id=run_id,
        parent_run_id=None,
        session_id="main",
        ticket_id=None,
        spec=builtin_spec("orchestrator"),
        app=client.app,
        request=SimpleNamespace(app=client.app),
        state={},
    )
    result = client.portal.call(lambda: middleware.before_model(context, request))
    section = next(
        segment.text
        for segment in result.meta["layout_parts"].system
        if segment.name == "memory"
    )
    assert section.startswith(MEMORY_HEADER)
    assert "以后刷图不要用源石恢复理智" in section
    # 每条写种类、日期与内容（语义记忆显示为「事实」）。
    assert "事实" in section
