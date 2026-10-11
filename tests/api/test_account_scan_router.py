"""练度扫描三个端点的 REST 契约（M36 规格 §4.6、§4.9）。

``tests/api`` 的纪律：不 import ``maa_api.main``，app 现搭、路由显式 include。这里的
被测面是**路由层**——请求体校验、把参数原样交给服务、把服务返回的 dict 原样透出、
把服务抛的错误码翻成 HTTP 状态码。扫描循环本身在 ``tests/services`` 里用替身驱动
（``test_account_training_scan.py``），所以本文件给一个 duck-typed 的假服务就够：
真服务要设备、要内核、要截图，放进 REST 用例只会把契约测试拖成集成测试。

鉴权与「服务没装配时 503」两条横向用例在 ``test_account_router.py``（那文件的
``ENDPOINTS`` 里也列了这三个端点），本文件只补形状与错误映射。
"""

from __future__ import annotations

from typing import Any

import pytest
from fastapi import FastAPI

from maa_api.api.errors import register_exception_handlers
from maa_api.api.routers import account as account_router
from maa_api.domain.errors import AppError, ErrorCode
from maa_api.services.account.scan import TrainingScanMode, TrainingScanStatus

#: 状态响应的完整键集（规格 §4.6 的 GET 形状 + M36 自己补的 `total`/`targets`
#: + M39 自己补的 `mode`/`screens`）。
STATE_KEYS = {
    "status",
    "mode",
    "screens",
    "pipeline_id",
    "started_at",
    "finished_at",
    "scanned",
    "updated",
    "skipped",
    "unresolved",
    "mismatched",
    "message",
    "total",
    "targets",
    "deferred",
}

#: 受理响应的键集（规格 §4.6 的 POST 形状 + M39 自己补的 `mode`）。
SUBMIT_KEYS = {"pipeline_id", "mode", "total", "targets", "deferred", "unresolved"}


def _state(**overrides: Any) -> dict[str, Any]:
    snapshot = {
        "status": TrainingScanStatus.IDLE.value,
        "mode": TrainingScanMode.SINGLE.value,
        "screens": 0,
        "pipeline_id": None,
        "started_at": None,
        "finished_at": None,
        "scanned": 0,
        "updated": 0,
        "skipped": 0,
        "unresolved": 0,
        "mismatched": 0,
        "message": None,
        "total": None,
        "targets": [],
        "deferred": [],
    }
    snapshot.update(overrides)
    return snapshot


class FakeScanService:
    """只实现路由用到的那三个方法，并记下调用参数。"""

    def __init__(self, *, error: AppError | None = None, state: dict[str, Any] | None = None):
        self.error = error
        self.state = state or _state()
        self.calls: list[tuple[str, dict[str, Any]]] = []

    async def submit(self, **kwargs: Any) -> dict[str, Any]:
        self.calls.append(("submit", kwargs))
        if self.error is not None:
            raise self.error
        return {
            "pipeline_id": "pipe-1",
            "mode": "single",
            "total": 2,
            "targets": [
                {"char_id": "char_002_amiya", "name": "阿米娅"},
                {"char_id": "char_103_angel", "name": "能天使"},
            ],
            "deferred": ["陈"],
            "unresolved": ["某位查不到的干员"],
        }

    def snapshot(self) -> dict[str, Any]:
        self.calls.append(("snapshot", {}))
        return dict(self.state)

    async def cancel(self) -> dict[str, Any]:
        self.calls.append(("cancel", {}))
        if self.error is not None:
            raise self.error
        return dict(self.state)


@pytest.fixture
def scan_app(make_client, tmp_settings):
    """``(app, service, client)``：app 只装账号路由，扫描服务是上面那个替身。

    要 ``tmp_settings``：它把 ``access_token`` 置空＝免鉴权。少了它就会打到真实
    ``config.yaml`` 上、每个用例都吃一个 401——而 401 是**有速率限制**的，几十个
    401 之后同会话的其它鉴权用例会开始拿 429（M36 实测）。
    """

    def build(service: FakeScanService | None = None):
        app = FastAPI()
        register_exception_handlers(app)
        app.include_router(account_router.router)
        if service is not None:
            # 生产装配名：main.py 用 training_scan_service。
            app.state.training_scan_service = service
        return app, service, make_client(app)

    return build


# ---------------------------------------------------------------------------
# GET：状态形状
# ---------------------------------------------------------------------------


def test_get_returns_the_idle_state_shape(scan_app):
    app, service, client = scan_app(FakeScanService())

    response = client.get("/api/account/training-scan")

    assert response.status_code == 200
    assert set(response.json()) == STATE_KEYS
    assert response.json()["status"] == "idle"
    assert response.json()["scanned"] == 0
    assert service.calls == [("snapshot", {})]


def test_get_passes_the_running_state_through(scan_app):
    state = _state(
        status=TrainingScanStatus.RUNNING.value,
        pipeline_id="pipe-7",
        started_at="2026-10-11T03:00:00+00:00",
        scanned=3,
        updated=2,
        skipped=1,
        unresolved=1,
        mismatched=2,
        message="正在读详情",
        total=40,
        targets=[{"char_id": "char_002_amiya", "name": "阿米娅"}],
        deferred=["陈"],
    )
    app, _service, client = scan_app(FakeScanService(state=state))

    body = client.get("/api/account/training-scan").json()

    assert body["status"] == "running"
    assert body["pipeline_id"] == "pipe-7"
    assert body["scanned"] == 3 and body["updated"] == 2 and body["mismatched"] == 2
    assert body["total"] == 40
    assert body["targets"] == [{"char_id": "char_002_amiya", "name": "阿米娅"}]
    assert body["deferred"] == ["陈"]


# ---------------------------------------------------------------------------
# POST：受理
# ---------------------------------------------------------------------------


def test_post_without_body_uses_the_service_defaults(scan_app):
    """整段 body 省略也能跑：三个字段都用服务端的默认（limit 40、含计划目标）。"""
    app, service, client = scan_app(FakeScanService())

    response = client.post("/api/account/training-scan")

    assert response.status_code == 200
    assert set(response.json()) == SUBMIT_KEYS
    assert response.json()["pipeline_id"] == "pipe-1"
    assert response.json()["targets"][0] == {"char_id": "char_002_amiya", "name": "阿米娅"}
    assert response.json()["deferred"] == ["陈"]
    assert service.calls == [
        (
            "submit",
            {
                "mode": TrainingScanMode.SINGLE,
                "limit": 40,
                "char_ids": None,
                "include_plan_targets": True,
            },
        )
    ]


def test_post_passes_the_body_through(scan_app):
    app, service, client = scan_app(FakeScanService())

    response = client.post(
        "/api/account/training-scan",
        json={"limit": 5, "char_ids": ["char_002_amiya"], "include_plan_targets": False},
    )

    assert response.status_code == 200
    assert service.calls == [
        (
            "submit",
            {
                "mode": TrainingScanMode.SINGLE,
                "limit": 5,
                "char_ids": ["char_002_amiya"],
                "include_plan_targets": False,
            },
        )
    ]


@pytest.mark.parametrize(
    "body",
    [
        {"limit": 0},
        {"limit": 201},
        # pydantic 的非严格模式会把 "40" 收成 40、"yes" 收成 True（与其它端点一致），
        # 所以这里只钉真正收不下来的输入。
        {"limit": "abc"},
        {"char_ids": "char_002_amiya"},
        {"include_plan_targets": "maybe"},
        {"unknown_field": 1},
    ],
)
def test_post_rejects_a_malformed_body(scan_app, body):
    app, service, client = scan_app(FakeScanService())

    response = client.post("/api/account/training-scan", json=body)

    assert response.status_code == 422
    assert response.json()["error"]["code"] == "VALIDATION_ERROR"
    assert service.calls == []  # 校验没过就不该碰服务


def test_post_maps_already_running_to_409(scan_app):
    """同一时刻只允许一轮（规格 §4.9）：服务抛 PIPELINE_ALREADY_RUNNING → 409。"""
    error = AppError(ErrorCode.PIPELINE_ALREADY_RUNNING, "已经有练度扫描在跑")
    app, _service, client = scan_app(FakeScanService(error=error))

    response = client.post("/api/account/training-scan")

    assert response.status_code == 409
    assert response.json()["error"]["code"] == "PIPELINE_ALREADY_RUNNING"


def test_post_maps_an_empty_roster_to_422(scan_app):
    error = AppError(ErrorCode.VALIDATION_ERROR, "这一轮没有可扫的干员")
    app, _service, client = scan_app(FakeScanService(error=error))

    response = client.post("/api/account/training-scan")

    assert response.status_code == 422
    assert response.json()["error"]["code"] == "VALIDATION_ERROR"


def test_post_maps_a_stalled_first_step_to_504(scan_app):
    """第一步迟迟不进队列 → 504，不是 500（规格 §4.6 的错误码表）。"""
    error = AppError(ErrorCode.CORE_COMMAND_TIMEOUT, "练度扫描没能及时开跑")
    app, _service, client = scan_app(FakeScanService(error=error))

    response = client.post("/api/account/training-scan")

    assert response.status_code == 504
    assert response.json()["error"]["code"] == "CORE_COMMAND_TIMEOUT"


def test_post_maps_a_failed_submit_to_502(scan_app):
    error = AppError(ErrorCode.CORE_COMMAND_FAILED, "练度扫描没能提交第一步")
    app, _service, client = scan_app(FakeScanService(error=error))

    response = client.post("/api/account/training-scan")

    assert response.status_code == 502
    assert response.json()["error"]["code"] == "CORE_COMMAND_FAILED"


# ---------------------------------------------------------------------------
# DELETE：取消
# ---------------------------------------------------------------------------


def test_delete_returns_the_cancelled_state(scan_app):
    state = _state(
        status=TrainingScanStatus.CANCELLED.value,
        pipeline_id="pipe-7",
        scanned=3,
        updated=2,
        message="已请求取消：这一步跑完就停，已经读到的练度保留",
        total=40,
    )
    app, service, client = scan_app(FakeScanService(state=state))

    response = client.delete("/api/account/training-scan")

    assert response.status_code == 200
    assert set(response.json()) == STATE_KEYS
    assert response.json()["status"] == "cancelled"
    assert response.json()["updated"] == 2
    assert service.calls == [("cancel", {})]


def test_delete_without_a_round_is_a_noop(scan_app):
    """没有在跑时取消是空操作：原样返回 idle 状态，不报错（规格 §4.9）。"""
    app, _service, client = scan_app(FakeScanService())

    response = client.delete("/api/account/training-scan")

    assert response.status_code == 200
    assert response.json()["status"] == "idle"


# ---------------------------------------------------------------------------
# 服务未装配
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("method", ["GET", "POST", "DELETE"])
def test_endpoints_report_503_without_the_service(scan_app, method):
    app, _service, client = scan_app(None)

    response = client.request(method, "/api/account/training-scan", json={})

    assert response.status_code == 503
    assert response.json()["error"]["code"] == "SERVICE_UNAVAILABLE"
