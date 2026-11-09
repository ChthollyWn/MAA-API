"""导航图 REST 端点（M86 spec §4.3.2、§6）：图、节点、跳转与 REST 抢占口径。

被测 app 按生产装配的最小集现搭：导航路由 + 统一错误处理器 + ``app.state.nav_service``
+ 假 ``device_manager``；表在 ``isolated_db`` 的临时库里用 ``create_all`` 建出来，
种子由 ``ensure_seed()`` 写入。REST 的租约口径单独用
``maa_api.api.routers.agent._rest_preempt_hook`` 钉住：``navigate_to`` 只抢占、
不获取也不持有租约。
"""

from __future__ import annotations

import asyncio
from datetime import timedelta
from pathlib import Path
from types import SimpleNamespace
from typing import Any

from fastapi import FastAPI, Request
from sqlmodel import SQLModel

import maa_api.db.models  # noqa: F401  # 注册全部表，create_all 才能建出导航两表
from maa_api.agent.nav import NavGraphService
from maa_api.agent.tools import build_registry as build_tool_registry
from maa_api.api.errors import register_exception_handlers
from maa_api.api.routers import agent as agent_router
from maa_api.api.routers import nav as nav_router
from maa_api.db import session as db_session
from maa_api.db.models import Screenshot, utcnow
from maa_api.domain.errors import AppError, ErrorCode
from maa_api.services.device_service import ScreenshotResult
from tests.fakes.nav import make_screen

UUID_A = "3f8fad5b-d9cb-469f-a165-708677289501"


class FakeDeviceManager:
    """只支持 ``screenshot`` 的假设备；``error`` 让下一次截图抛指定 ``AppError``。"""

    def __init__(self) -> None:
        self.image = make_screen("current")
        self.error: Exception | None = None
        self.calls = 0

    async def screenshot(self, *, backend: str = "adb") -> ScreenshotResult:
        self.calls += 1
        if self.error is not None:
            raise self.error
        return ScreenshotResult(self.image, "adb")


async def _create_schema(engine: Any) -> None:
    async with engine.begin() as connection:
        await connection.run_sync(SQLModel.metadata.create_all)


def _build_app(make_client, isolated_db):
    app = FastAPI()
    register_exception_handlers(app)
    app.include_router(nav_router.router)
    app.state.nav_service = NavGraphService(db_session.session_factory)
    app.state.device_manager = FakeDeviceManager()
    client = make_client(app)
    client.portal.call(_create_schema, isolated_db)
    client.portal.call(app.state.nav_service.ensure_seed)
    return app, client


def _error(response):
    return response.json()["error"]


def _node(client, node_id: str) -> dict[str, Any]:
    graph = client.get("/api/agent/nav/graph").json()
    return next(item for item in graph["nodes"] if item["id"] == node_id)


def _seed_screenshot(client, *, deleted: bool = False, path: str = "image/screenshot/aa/aa.jpg") -> str:
    async def create() -> str:
        async with db_session.session_factory() as db:
            row = Screenshot(
                trigger="agent",
                backend="adb",
                path=path,
                format="jpg",
                width=1280,
                height=720,
                size_bytes=10,
                deleted_at=utcnow() - timedelta(hours=1) if deleted else None,
            )
            db.add(row)
            await db.commit()
            return str(row.id)

    return client.portal.call(create)


# ----------------------------------------------------------------------
# GET /api/agent/nav/graph
# ----------------------------------------------------------------------


def test_get_graph_returns_seed(make_client, isolated_db, tmp_settings) -> None:
    _app, client = _build_app(make_client, isolated_db)

    response = client.get("/api/agent/nav/graph")

    assert response.status_code == 200, response.text
    payload = response.json()
    assert len(payload["nodes"]) == 15
    assert len(payload["edges"]) == 36
    assert payload["enabled"] is True
    assert payload["learn"] is True
    assert payload["vision_available"] is False
    home = next(item for item in payload["nodes"] if item["id"] == "home")
    assert home["title"] == "主界面"
    assert home["kind"] == "screen"
    assert home["origin"] == "seed"
    assert len(home["recognizer"]["rois"]) == 2
    assert home["exemplars"] == []
    assert home["created_at"].endswith("Z")


# ----------------------------------------------------------------------
# PUT /api/agent/nav/nodes/{node_id}
# ----------------------------------------------------------------------


def test_put_node_creates_user_node(make_client, isolated_db, tmp_settings) -> None:
    _app, client = _build_app(make_client, isolated_db)

    response = client.put(
        "/api/agent/nav/nodes/demo_screen",
        json={"title": "演示界面", "kind": "screen"},
    )

    assert response.status_code == 200, response.text
    payload = response.json()
    assert payload["id"] == "demo_screen"
    assert payload["title"] == "演示界面"
    assert payload["origin"] == "user"
    assert payload["exemplars"] == []
    assert payload["created_at"].endswith("Z")
    # 再 GET 一次：新节点落库了，种子不变。
    graph = client.get("/api/agent/nav/graph").json()
    assert len(graph["nodes"]) == 16


def test_put_node_rejects_bad_id(make_client, isolated_db, tmp_settings) -> None:
    _app, client = _build_app(make_client, isolated_db)

    response = client.put(
        "/api/agent/nav/nodes/Bad",
        json={"title": "坏 id", "kind": "screen"},
    )

    assert response.status_code == 400, response.text
    error = _error(response)
    assert error["code"] == "INVALID_PARAMETER"
    assert error["details"]["field"] == "node_id"


def test_put_node_add_current_exemplar(make_client, isolated_db, tmp_settings) -> None:
    app, client = _build_app(make_client, isolated_db)

    response = client.put(
        "/api/agent/nav/nodes/home",
        json={"title": "主界面", "kind": "screen", "add_exemplar": "current"},
    )

    assert response.status_code == 200, response.text
    payload = response.json()
    assert len(payload["exemplars"]) == 1
    exemplar = payload["exemplars"][0]
    assert exemplar["source"] == "user"
    assert exemplar["screenshot_id"]
    assert exemplar["thumb_url"].startswith("/api/images/")
    assert exemplar["thumb_url"].endswith("/thumb")
    assert app.state.device_manager.calls == 1


def test_put_node_add_exemplar_from_existing_screenshot(
    make_client, isolated_db, tmp_settings
) -> None:
    _app, client = _build_app(make_client, isolated_db)
    first = client.put(
        "/api/agent/nav/nodes/home",
        json={"title": "主界面", "kind": "screen", "add_exemplar": "current"},
    )
    screenshot_id = first.json()["exemplars"][0]["screenshot_id"]

    response = client.put(
        "/api/agent/nav/nodes/second_screen",
        json={
            "title": "第二界面",
            "kind": "screen",
            "add_exemplar_screenshot_id": screenshot_id,
        },
    )

    assert response.status_code == 200, response.text
    exemplars = response.json()["exemplars"]
    assert len(exemplars) == 1
    assert exemplars[0]["source"] == "user"
    assert exemplars[0]["screenshot_id"] == screenshot_id
    assert exemplars[0]["thumb_url"].startswith("/api/images/")


def test_put_node_rejects_both_exemplar_sources(
    make_client, isolated_db, tmp_settings
) -> None:
    _app, client = _build_app(make_client, isolated_db)

    response = client.put(
        "/api/agent/nav/nodes/home",
        json={
            "title": "主界面",
            "kind": "screen",
            "add_exemplar": "current",
            "add_exemplar_screenshot_id": UUID_A,
        },
    )

    assert response.status_code == 400, response.text
    assert _error(response)["code"] == "INVALID_PARAMETER"


def test_put_node_recomputes_exemplars_when_rois_change(
    make_client, isolated_db, tmp_settings
) -> None:
    _app, client = _build_app(make_client, isolated_db)
    first = client.put(
        "/api/agent/nav/nodes/home",
        json={"title": "主界面", "kind": "screen", "add_exemplar": "current"},
    )
    assert len(first.json()["exemplars"][0]["phash"].split(":")) == 2

    response = client.put(
        "/api/agent/nav/nodes/home",
        json={
            "title": "主界面",
            "kind": "screen",
            "recognizer": {"rois": [[0, 0, 1, 1], [0.5, 0.5, 0.25, 0.25]]},
        },
    )

    assert response.status_code == 200, response.text
    exemplars = response.json()["exemplars"]
    assert len(exemplars) == 1
    assert len(exemplars[0]["phash"].split(":")) == 2
    assert exemplars[0]["screenshot_id"]


def test_put_node_drops_exemplars_without_screenshot_file(
    make_client, isolated_db, tmp_settings
) -> None:
    _app, client = _build_app(make_client, isolated_db)
    first = client.put(
        "/api/agent/nav/nodes/home",
        json={"title": "主界面", "kind": "screen", "add_exemplar": "current"},
    )
    thumb_url = first.json()["exemplars"][0]["thumb_url"]
    sha = thumb_url.removeprefix("/api/images/").removesuffix("/thumb")
    archived = Path(db_session.DB_PATH).parent / "image" / "screenshot" / sha[:2] / f"{sha}.jpg"
    assert archived.is_file()
    archived.unlink()

    response = client.put(
        "/api/agent/nav/nodes/home",
        json={
            "title": "主界面",
            "kind": "screen",
            "recognizer": {"rois": [[0, 0, 1, 1], [0.5, 0.5, 0.25, 0.25]]},
        },
    )

    assert response.status_code == 200, response.text
    # 截图文件已被清理：那个样本删掉（spec §4.5.7）。
    assert response.json()["exemplars"] == []


def test_put_node_removes_exemplar_by_phash(
    make_client, isolated_db, tmp_settings
) -> None:
    _app, client = _build_app(make_client, isolated_db)
    first = client.put(
        "/api/agent/nav/nodes/home",
        json={"title": "主界面", "kind": "screen", "add_exemplar": "current"},
    )
    phash = first.json()["exemplars"][0]["phash"]

    response = client.put(
        "/api/agent/nav/nodes/home",
        json={"title": "主界面", "kind": "screen", "remove_exemplars": [phash]},
    )

    assert response.status_code == 200, response.text
    assert response.json()["exemplars"] == []


def test_put_node_add_exemplar_rejects_expired_screenshot(
    make_client, isolated_db, tmp_settings
) -> None:
    _app, client = _build_app(make_client, isolated_db)
    screenshot_id = _seed_screenshot(client, deleted=True)

    response = client.put(
        "/api/agent/nav/nodes/home",
        json={
            "title": "主界面",
            "kind": "screen",
            "add_exemplar_screenshot_id": screenshot_id,
        },
    )

    assert response.status_code == 410, response.text
    assert _error(response)["code"] == "SCREENSHOT_EXPIRED"


def test_put_node_add_exemplar_missing_screenshot_is_404(
    make_client, isolated_db, tmp_settings
) -> None:
    _app, client = _build_app(make_client, isolated_db)

    response = client.put(
        "/api/agent/nav/nodes/home",
        json={
            "title": "主界面",
            "kind": "screen",
            "add_exemplar_screenshot_id": UUID_A,
        },
    )

    assert response.status_code == 404, response.text
    assert _error(response)["code"] == "SCREENSHOT_NOT_FOUND"


def test_put_node_add_current_exemplar_device_not_connected(
    make_client, isolated_db, tmp_settings
) -> None:
    app, client = _build_app(make_client, isolated_db)
    app.state.device_manager.error = AppError(
        ErrorCode.DEVICE_NOT_CONNECTED, "设备当前未连接"
    )

    response = client.put(
        "/api/agent/nav/nodes/home",
        json={"title": "主界面", "kind": "screen", "add_exemplar": "current"},
    )

    assert response.status_code == 503, response.text
    assert _error(response)["code"] == "DEVICE_NOT_CONNECTED"


# ----------------------------------------------------------------------
# PUT / DELETE /api/agent/nav/edges/{edge_id}
# ----------------------------------------------------------------------


def _create_edge(client, edge_id: str = UUID_A) -> dict[str, Any]:
    response = client.put(
        f"/api/agent/nav/edges/{edge_id}",
        json={
            "from_node": "home",
            "to_node": "depot",
            "action": {"type": "tap", "x": 0.5, "y": 0.5},
        },
    )
    assert response.status_code == 200, response.text
    return response.json()


def test_put_edge_creates_and_updates(make_client, isolated_db, tmp_settings) -> None:
    _app, client = _build_app(make_client, isolated_db)

    created = _create_edge(client)
    assert created["id"] == UUID_A
    assert created["origin"] == "user"
    assert created["enabled"] is True
    assert created["action"] == {"type": "tap", "x": 0.5, "y": 0.5}
    assert created["created_at"].endswith("Z")

    disabled = client.put(
        f"/api/agent/nav/edges/{UUID_A}", json={"enabled": False}
    )
    assert disabled.status_code == 200, disabled.text
    assert disabled.json()["enabled"] is False

    # 同端点的写请求里带相同端点值只忽略，不报错。
    enabled = client.put(
        f"/api/agent/nav/edges/{UUID_A}",
        json={"enabled": True, "from_node": "home", "to_node": "depot"},
    )
    assert enabled.status_code == 200, enabled.text
    assert enabled.json()["enabled"] is True


def test_put_edge_rejects_endpoint_change(
    make_client, isolated_db, tmp_settings
) -> None:
    _app, client = _build_app(make_client, isolated_db)
    _create_edge(client)

    response = client.put(
        f"/api/agent/nav/edges/{UUID_A}", json={"from_node": "terminal"}
    )

    assert response.status_code == 400, response.text
    error = _error(response)
    assert error["code"] == "INVALID_PARAMETER"
    assert error["details"]["field"] == "from_node"


def test_put_edge_rejects_bad_uuid(make_client, isolated_db, tmp_settings) -> None:
    _app, client = _build_app(make_client, isolated_db)

    response = client.put(
        "/api/agent/nav/edges/not-a-uuid",
        json={
            "from_node": "home",
            "to_node": "depot",
            "action": {"type": "tap", "x": 0.5, "y": 0.5},
        },
    )

    assert response.status_code == 400, response.text
    assert _error(response)["details"]["field"] == "edge_id"


def test_put_edge_unknown_without_fields_is_404(
    make_client, isolated_db, tmp_settings
) -> None:
    _app, client = _build_app(make_client, isolated_db)

    response = client.put(f"/api/agent/nav/edges/{UUID_A}", json={})

    assert response.status_code == 404, response.text
    assert _error(response)["code"] == "NOT_FOUND"


def test_put_edge_unknown_node_is_404(
    make_client, isolated_db, tmp_settings
) -> None:
    _app, client = _build_app(make_client, isolated_db)

    response = client.put(
        f"/api/agent/nav/edges/{UUID_A}",
        json={
            "from_node": "home",
            "to_node": "ghost_node",
            "action": {"type": "tap", "x": 0.5, "y": 0.5},
        },
    )

    assert response.status_code == 404, response.text
    assert _error(response)["code"] == "NAV_NODE_NOT_FOUND"


def test_delete_edge(make_client, isolated_db, tmp_settings) -> None:
    _app, client = _build_app(make_client, isolated_db)
    _create_edge(client)

    deleted = client.delete(f"/api/agent/nav/edges/{UUID_A}")
    assert deleted.status_code == 204, deleted.text
    assert deleted.content == b""

    again = client.delete(f"/api/agent/nav/edges/{UUID_A}")
    assert again.status_code == 404, again.text
    assert _error(again)["code"] == "NOT_FOUND"


# ----------------------------------------------------------------------
# REST 抢占口径（§4.5.5）
# ----------------------------------------------------------------------


def test_rest_navigate_to_preempts_without_holding_lease(monkeypatch) -> None:
    calls: list[tuple[str, str | None]] = []

    async def fake_preempt(request: Request, reason: str, detail: str | None = None) -> None:
        calls.append((reason, detail))

    monkeypatch.setattr(agent_router, "preempt_device", fake_preempt)
    service = SimpleNamespace(registry=build_tool_registry())
    # 没有 device_lease_service：钩子只抢占，不获取、不持有租约。
    state = SimpleNamespace()
    request = Request({"type": "http", "app": SimpleNamespace(state=state)})

    hook = agent_router._rest_preempt_hook(service, "navigate_to", request)
    assert hook is not None
    assert agent_router._rest_preempt_hook(service, "where_am_i", request) is None
    assert agent_router._rest_preempt_hook(service, "remember_screen", request) is None

    asyncio.run(hook(1))
    assert calls == [("rest_tool", None)]
    assert not hasattr(state, "device_lease_service")
