"""M89 T6：示教四个 REST 端点（spec §4.3、§5 T6、§6）。

应用按生产装配的最小集现搭：示教路由、统一错误处理器与 ``app.state`` 上的
``demonstration_service`` / ``device_manager``；服务、租约、资源服务与 SQLite 都是真的
（资源根在 ``tmp_path``）。数据库操作都经 ``TestClient`` 的 portal 跑在同一个事件
循环里，与 ``tests/api/test_goals_router.py`` 同一口径。

覆盖：开始返回 201 与 ``screen``、错误路径（重复开始 / 设备未连接 / 开关关闭）、
列表分页与步骤完整、停止 404/409、转换两阶段与失败回滚、OpenAPI 四个 operation。
"""

from __future__ import annotations

from datetime import datetime
from functools import partial
from types import SimpleNamespace
from typing import Any

from fastapi import FastAPI
from sqlalchemy import func, select
from sqlmodel import SQLModel

import maa_api.db.models  # noqa: F401  # 注册全部表，create_all 才能建出示教表
import maa_api.api.routers.demonstrations as demonstrations_router
from maa_api.agent.demonstrations.compiler import Step, compile_steps
from maa_api.agent.demonstrations.service import DemonstrationService
from maa_api.agent.lease import DeviceLeaseService
from maa_api.api.errors import register_exception_handlers
from maa_api.db import session as db_session
from maa_api.db.models import Demonstration, ResourceAsset
from maa_api.domain.enums import DemonstrationStatus, ResourceAssetKind
from maa_api.services.resource_service import ResourceService
from maa_api.settings import LeaseSettings


class FakeDeviceManager:
    """只实现 ``snapshot()``；状态与分辨率可控。"""

    def __init__(
        self,
        *,
        state: str = "connected",
        resolution: tuple[int, int] | None = (1280, 720),
    ) -> None:
        self.state = state
        self.resolution = resolution

    def snapshot(self) -> dict[str, Any]:
        return {
            "state": self.state,
            "resolution": (
                {"width": self.resolution[0], "height": self.resolution[1]}
                if self.resolution is not None
                else None
            ),
        }


async def _create_schema(engine: Any) -> None:
    async with engine.begin() as connection:
        await connection.run_sync(SQLModel.metadata.create_all)


def _build_app(isolated_db, make_client, tmp_path):
    """建表、装路由与真服务；返回环境命名空间。"""
    app = FastAPI()
    register_exception_handlers(app)
    app.include_router(demonstrations_router.router)

    device = FakeDeviceManager()
    lease = DeviceLeaseService(
        db_session.session_factory,
        boot_id="boot-test",
        settings_provider=lambda: SimpleNamespace(lease=LeaseSettings(wait_seconds=0)),
        poll_seconds=0.02,
    )
    resources = ResourceService(
        db_session.session_factory,
        resource_root=tmp_path / "resource",
        reload_resources=lambda: None,
    )
    service = DemonstrationService(
        db_session.session_factory,
        device_manager=device,
        lease=lease,
        resource_service=resources,
    )
    app.state.demonstration_service = service
    app.state.device_manager = device
    app.state.resource_service = resources
    client = make_client(app)
    client.portal.call(_create_schema, isolated_db)
    return SimpleNamespace(
        app=app,
        client=client,
        service=service,
        device=device,
        lease=lease,
        resources=resources,
    )


def _click_step(seq: int = 1, **overrides: Any) -> dict[str, Any]:
    step = {
        "seq": seq,
        "action": "click",
        "x": 640,
        "y": 360,
        "x1": None,
        "y1": None,
        "x2": None,
        "y2": None,
        "duration_ms": None,
        "delay_ms": 0,
        "screen": [1280, 720],
        "screenshot_id": None,
        "result_screenshot_id": None,
        "at": "2026-11-09T08:30:00.123Z",
    }
    step.update(overrides)
    return step


def _swipe_step(seq: int = 2) -> dict[str, Any]:
    return {
        "seq": seq,
        "action": "swipe",
        "x": None,
        "y": None,
        "x1": 100,
        "y1": 200,
        "x2": 900,
        "y2": 500,
        "duration_ms": 300,
        "delay_ms": 800,
        "screen": [1280, 720],
        "screenshot_id": None,
        "result_screenshot_id": None,
        "at": "2026-11-09T08:30:01.000Z",
    }


async def _seed(
    *,
    demonstration_id: str,
    status: DemonstrationStatus = DemonstrationStatus.RECORDED,
    steps: list[dict[str, Any]] | None = None,
    title: str = "示教 11-09 08:30",
    created_at: datetime | None = None,
) -> None:
    async with db_session.session_factory() as db:
        db.add(
            Demonstration(
                id=demonstration_id,
                title=title,
                status=status,
                steps=list(steps or []),
                created_at=created_at or datetime(2026, 11, 9, 8, 0, 0),
                updated_at=created_at or datetime(2026, 11, 9, 8, 0, 0),
            )
        )
        await db.commit()


async def _asset_count() -> int:
    async with db_session.session_factory() as db:
        result = await db.execute(select(func.count()).select_from(ResourceAsset))
        return int(result.scalar_one())


async def _insert_asset(name: str, asset_id: str = "asset-existing") -> None:
    async with db_session.session_factory() as db:
        db.add(
            ResourceAsset(
                id=asset_id,
                kind=ResourceAssetKind.CUSTOM_TASK,
                name=name,
                content={"algorithm": "JustReturn", "action": "Stop"},
                meta={"task_name": name},
            )
        )
        await db.commit()


async def _converted_to_map() -> dict[str, Any]:
    async with db_session.session_factory() as db:
        result = await db.execute(select(Demonstration))
        return {row.id: (str(row.status), row.converted_to) for row in result.scalars()}


def _entry_for(demonstration_id: str) -> str:
    """与 convert 同一条 prefix 规则算出的入口名。"""
    return compile_steps(
        prefix=f"Custom_Demo_{demonstration_id[:8]}",
        steps=[Step(action="click", x=640, y=360, screen=(1280, 720))],
    ).entry


def _error(response) -> dict[str, Any]:
    return response.json()["error"]


# ---------------------------------------------------------------------------
# 开始
# ---------------------------------------------------------------------------


def test_start_returns_recording_and_screen(
    isolated_db, make_client, tmp_path, tmp_settings
) -> None:
    env = _build_app(isolated_db, make_client, tmp_path)

    # body 可省略：标题用服务端本地时间的「示教 MM-DD HH:MM」。
    first = env.client.post("/api/agent/demonstrations")
    assert first.status_code == 201
    body = first.json()
    assert body["status"] == "recording"
    assert body["screen"] == [1280, 720]
    assert body["title"].startswith("示教 ")
    assert body["steps"] == []
    env.client.post(f"/api/agent/demonstrations/{body['id']}/stop", json={"discard": True})

    response = env.client.post(
        "/api/agent/demonstrations", json={"title": "新活动示教"}
    )
    assert response.status_code == 201
    body = response.json()
    assert body["status"] == "recording"
    assert body["title"] == "新活动示教"
    assert body["screen"] == [1280, 720]
    assert body["converted_to"] is None
    assert env.service.is_recording() is True
    assert env.lease.recording() is True


def test_start_error_paths(isolated_db, make_client, tmp_path, tmp_settings) -> None:
    env = _build_app(isolated_db, make_client, tmp_path)

    started = env.client.post("/api/agent/demonstrations", json={"title": "第一次"})
    assert started.status_code == 201
    recording_id = started.json()["id"]

    # 同一时刻只允许一条录制：第二个 start 409，details 带 id/status/action。
    second = env.client.post("/api/agent/demonstrations", json={"title": "第二次"})
    assert second.status_code == 409
    error = _error(second)
    assert error["code"] == "DEMONSTRATION_STATE_INVALID"
    assert error["details"] == {
        "id": recording_id,
        "status": "recording",
        "action": "start",
    }

    stopped = env.client.post(
        f"/api/agent/demonstrations/{recording_id}/stop", json={"discard": True}
    )
    assert stopped.status_code == 200
    assert stopped.json()["status"] == "discarded"

    # 设备未连接：503 DEVICE_NOT_CONNECTED，不插入行。
    env.device.state = "disconnected"
    unavailable = env.client.post("/api/agent/demonstrations")
    assert unavailable.status_code == 503
    assert _error(unavailable)["code"] == "DEVICE_NOT_CONNECTED"


def test_disabled_switch_returns_503_on_all_endpoints(
    isolated_db, make_client, tmp_path, tmp_settings, monkeypatch
) -> None:
    env = _build_app(isolated_db, make_client, tmp_path)
    monkeypatch.setattr(
        demonstrations_router,
        "get_settings",
        lambda: SimpleNamespace(demonstration=SimpleNamespace(enabled=False)),
    )

    for method, path in (
        ("post", "/api/agent/demonstrations"),
        ("get", "/api/agent/demonstrations"),
        ("post", "/api/agent/demonstrations/any-id/stop"),
        ("post", "/api/agent/demonstrations/any-id/convert"),
    ):
        response = getattr(env.client, method)(path)
        assert response.status_code == 503, (method, path, response.text)
        error = _error(response)
        assert error["code"] == "SERVICE_UNAVAILABLE"
        assert "details" not in error


# ---------------------------------------------------------------------------
# 列表
# ---------------------------------------------------------------------------


def test_list_steps_and_pagination(isolated_db, make_client, tmp_path, tmp_settings) -> None:
    env = _build_app(isolated_db, make_client, tmp_path)

    env.client.portal.call(
        partial(
            _seed,
            demonstration_id="demo-older",
            status=DemonstrationStatus.RECORDED,
            steps=[_click_step()],
            created_at=datetime(2026, 11, 9, 8, 0, 0),
        )
    )
    env.client.portal.call(
        partial(
            _seed,
            demonstration_id="demo-newer",
            status=DemonstrationStatus.DISCARDED,
            steps=[_click_step(), _swipe_step()],
            created_at=datetime(2026, 11, 9, 9, 0, 0),
        )
    )

    response = env.client.get("/api/agent/demonstrations")
    assert response.status_code == 200
    page = response.json()
    assert page["total"] == 2
    assert page["page"] == 1
    assert page["size"] == 20
    assert [item["id"] for item in page["items"]] == ["demo-newer", "demo-older"]
    newer = page["items"][0]
    assert len(newer["steps"]) == 2
    assert [step["action"] for step in newer["steps"]] == ["click", "swipe"]
    for step in newer["steps"]:
        assert step["at"].endswith("Z")
        assert datetime.fromisoformat(step["at"].replace("Z", "+00:00")).tzinfo is not None
    assert newer["steps"][1]["x1"] == 100
    assert newer["steps"][1]["duration_ms"] == 300

    filtered = env.client.get("/api/agent/demonstrations", params={"status": "recorded"})
    assert filtered.status_code == 200
    assert filtered.json()["total"] == 1
    assert [item["id"] for item in filtered.json()["items"]] == ["demo-older"]

    second_page = env.client.get(
        "/api/agent/demonstrations", params={"page": 2, "size": 1}
    )
    assert second_page.status_code == 200
    assert [item["id"] for item in second_page.json()["items"]] == ["demo-older"]

    # size 上限 50：51 是 Pydantic query 越界，422 VALIDATION_ERROR。
    too_large = env.client.get("/api/agent/demonstrations", params={"size": 51})
    assert too_large.status_code == 422
    assert _error(too_large)["code"] == "VALIDATION_ERROR"

    # status 非法值由服务报 400 INVALID_PARAMETER。
    invalid_status = env.client.get(
        "/api/agent/demonstrations", params={"status": "unknown"}
    )
    assert invalid_status.status_code == 400
    assert _error(invalid_status)["code"] == "INVALID_PARAMETER"


# ---------------------------------------------------------------------------
# 停止
# ---------------------------------------------------------------------------


def test_stop_error_paths(isolated_db, make_client, tmp_path, tmp_settings) -> None:
    env = _build_app(isolated_db, make_client, tmp_path)

    missing = env.client.post("/api/agent/demonstrations/missing-id/stop")
    assert missing.status_code == 404
    assert _error(missing)["code"] == "DEMONSTRATION_NOT_FOUND"
    assert _error(missing)["details"] == {"id": "missing-id"}

    env.client.portal.call(
        partial(
            _seed,
            demonstration_id="demo-recorded",
            status=DemonstrationStatus.RECORDED,
            steps=[_click_step()],
        )
    )
    conflict = env.client.post("/api/agent/demonstrations/demo-recorded/stop")
    assert conflict.status_code == 409
    error = _error(conflict)
    assert error["code"] == "DEMONSTRATION_STATE_INVALID"
    assert error["details"] == {
        "id": "demo-recorded",
        "status": "recorded",
        "action": "stop",
    }


# ---------------------------------------------------------------------------
# 转换
# ---------------------------------------------------------------------------


def test_convert_preview_does_not_write(
    isolated_db, make_client, tmp_path, tmp_settings
) -> None:
    env = _build_app(isolated_db, make_client, tmp_path)
    env.client.portal.call(
        partial(
            _seed,
            demonstration_id="demo-preview",
            status=DemonstrationStatus.RECORDED,
            steps=[_click_step(), _swipe_step()],
        )
    )
    before = env.client.portal.call(_asset_count)

    response = env.client.post("/api/agent/demonstrations/demo-preview/convert", json={})

    assert response.status_code == 200
    body = response.json()
    assert body["status"] == "recorded"
    assert body["converted_to"] is None
    compiled = body["compile"]
    assert compiled["valid"] is True
    assert compiled["register"] is False
    assert compiled["registered"] == []
    assert compiled["entry"] == _entry_for("demo-preview")
    assert compiled["chain"][-1].endswith("_Done")
    assert compiled["tasks"][compiled["chain"][-1]] == {
        "algorithm": "JustReturn",
        "action": "Stop",
    }
    assert env.client.portal.call(_asset_count) == before == 0


def test_convert_register_marks_converted(
    isolated_db, make_client, tmp_path, tmp_settings
) -> None:
    env = _build_app(isolated_db, make_client, tmp_path)
    env.client.portal.call(
        partial(
            _seed,
            demonstration_id="demo-register",
            status=DemonstrationStatus.RECORDED,
            steps=[_click_step(), _swipe_step()],
            title="新活动示教",
        )
    )

    response = env.client.post(
        "/api/agent/demonstrations/demo-register/convert",
        json={"register": True},
    )

    assert response.status_code == 200
    body = response.json()
    assert body["status"] == "converted"
    converted = body["converted_to"]
    assert converted["entry"] == body["compile"]["entry"]
    assert converted["entry"] == _entry_for("demo-register")
    assert len(converted["task_names"]) == len(converted["asset_ids"]) == 3
    assert converted["registered_at"].endswith("Z")
    assert body["compile"]["register"] is True
    assert [item["id"] for item in body["compile"]["registered"]] == converted["asset_ids"]
    assert env.client.portal.call(_asset_count) == 3

    # 已转换是终态：再转 409。
    again = env.client.post("/api/agent/demonstrations/demo-register/convert")
    assert again.status_code == 409
    assert _error(again)["code"] == "DEMONSTRATION_STATE_INVALID"


def test_convert_failure_paths(
    isolated_db, make_client, tmp_path, tmp_settings, monkeypatch
) -> None:
    env = _build_app(isolated_db, make_client, tmp_path)

    # 1. 编译失败：422 CUSTOM_TASK_INVALID，details 带两个错误数组；状态不变。
    env.client.portal.call(
        partial(
            _seed,
            demonstration_id="demo-bad",
            status=DemonstrationStatus.RECORDED,
            steps=[_click_step(action="input_text")],
        )
    )
    invalid = env.client.post(
        "/api/agent/demonstrations/demo-bad/convert", json={"register": True}
    )
    assert invalid.status_code == 422
    error = _error(invalid)
    assert error["code"] == "CUSTOM_TASK_INVALID"
    assert "errors" in error["details"]
    assert "chain_errors" in error["details"]
    assert env.client.portal.call(_asset_count) == 0

    # 2. 名字预检冲突：422，errors 里给冲突名；不写新资产。
    env.client.portal.call(
        partial(
            _seed,
            demonstration_id="demo-conflict",
            status=DemonstrationStatus.RECORDED,
            steps=[_click_step()],
        )
    )
    entry = _entry_for("demo-conflict")
    env.client.portal.call(partial(_insert_asset, entry, "asset-conflict"))
    conflicted = env.client.post(
        "/api/agent/demonstrations/demo-conflict/convert", json={"register": True}
    )
    assert conflicted.status_code == 422
    error = _error(conflicted)
    assert error["code"] == "CUSTOM_TASK_INVALID"
    assert error["details"]["errors"] == [
        {"task": entry, "field": "name", "reason": "已存在同名自定义任务"}
    ]
    assert env.client.portal.call(_asset_count) == 1

    # 3. 写入竞态：预检时不存在，注册时已被别人写入 → 409 RESOURCE_ASSET_CONFLICT。
    env.client.portal.call(
        partial(
            _seed,
            demonstration_id="demo-race",
            status=DemonstrationStatus.RECORDED,
            steps=[_click_step()],
        )
    )
    race_entry = _entry_for("demo-race")
    env.client.portal.call(partial(_insert_asset, race_entry, "asset-race"))

    async def _no_conflicts(self, _session, _tasks):
        return []

    monkeypatch.setattr(
        DemonstrationService, "_conflicting_task_names", _no_conflicts
    )
    raced = env.client.post(
        "/api/agent/demonstrations/demo-race/convert", json={"register": True}
    )
    assert raced.status_code == 409
    assert _error(raced)["code"] == "RESOURCE_ASSET_CONFLICT"
    assert env.client.portal.call(_asset_count) == 2

    # 三条失败路径都没有留下 converted_to，也没有部分资产。
    rows = env.client.portal.call(_converted_to_map)
    for demonstration_id in ("demo-bad", "demo-conflict", "demo-race"):
        assert rows[demonstration_id] == ("recorded", None)


# ---------------------------------------------------------------------------
# OpenAPI
# ---------------------------------------------------------------------------


def test_openapi_has_four_demonstration_operations(tmp_settings) -> None:
    from maa_api.main import create_app

    paths = create_app().openapi()["paths"]

    assert paths["/api/agent/demonstrations"]["post"]["operationId"] == (
        "agent_start_demonstration"
    )
    assert paths["/api/agent/demonstrations"]["get"]["operationId"] == (
        "agent_list_demonstrations"
    )
    assert paths["/api/agent/demonstrations/{id}/stop"]["post"]["operationId"] == (
        "agent_stop_demonstration"
    )
    assert paths["/api/agent/demonstrations/{id}/convert"]["post"]["operationId"] == (
        "agent_convert_demonstration"
    )
    start_schema = paths["/api/agent/demonstrations"]["post"]["responses"]["201"][
        "content"
    ]["application/json"]["schema"]
    assert start_schema["$ref"].rsplit("/", 1)[-1] == "DemonstrationStartOut"
