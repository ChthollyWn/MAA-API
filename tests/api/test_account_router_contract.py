"""账号数据 REST 契约（M27 规格 §7.5、附录 C）：鉴权、分页、筛选、PATCH 校验与 404。

与 ``test_account_router.py`` 的分工：本文件用**假服务**驱动真实路由代码，因此不依赖
``db/models.py`` 与 ``db/repositories/account.py`` 是否已经落地；那边用临时 SQLite 跑
同一个路由的端到端版本。两边都跑通，说明路由的契约与服务的实现是同一份语义。

app 是现搭的（``tests/api`` 的纪律：不 import ``maa_api.main``）；路由注册由 main.py
负责，这里显式 ``include_router``，所以本文件在路由还没接进 main 时也能独立跑。
"""

from __future__ import annotations

import asyncio

import pytest
from fastapi import FastAPI

from maa_api.api.errors import register_exception_handlers
from maa_api.api.routers import account as account_router
from maa_api.domain.errors import AppError, ErrorCode

#: 七个端点（方法, 路径）：六个在附录 C 里，另加编辑抽屉用的单条 GET。
ENDPOINTS = (
    ("GET", "/api/account/summary"),
    ("GET", "/api/account/operators"),
    ("GET", "/api/account/operators/char_002_amiya"),
    ("PATCH", "/api/account/operators/char_002_amiya"),
    ("GET", "/api/account/operator-diffs"),
    ("DELETE", "/api/account/operator-diffs"),
    ("GET", "/api/account/depot"),
)


class FakeBoxService:
    """按冻结接口实现的最小替身：记录调用参数，返回固定行。"""

    def __init__(self) -> None:
        self.calls: list[tuple[str, dict]] = []
        self.rows: dict[str, dict] = {}
        self.diffs: list[dict] = []

    async def summary(self) -> dict:
        self.calls.append(("summary", {}))
        return {
            "total": 2,
            "elite_two": 1,
            "recognized_at": "2026-10-10T03:00:00Z",
            "stale": False,
            "stale_days": 14,
            "unrecognized": 1,
            "pending_diffs": len(self.diffs),
        }

    async def list_operators(self, **kwargs) -> dict:
        self.calls.append(("list_operators", kwargs))
        return {
            "items": list(self.rows.values()),
            "total": len(self.rows),
            "limit": kwargs["limit"],
            "offset": kwargs["offset"],
        }

    async def get_operator(self, char_id: str) -> dict:
        self.calls.append(("get_operator", {"char_id": char_id}))
        if char_id not in self.rows:
            raise AppError(
                ErrorCode.ACCOUNT_DATA_NOT_FOUND,
                "账号数据里没有这名干员的记录，请先运行一次干员识别",
                {"char_id": char_id},
            )
        return self.rows[char_id]

    async def patch_operator(self, char_id: str, payload, *, source: str = "manual") -> dict:
        self.calls.append(
            ("patch_operator", {"char_id": char_id, "payload": dict(payload), "source": source})
        )
        if char_id not in self.rows:
            raise AppError(
                ErrorCode.ACCOUNT_DATA_NOT_FOUND,
                "账号数据里没有这名干员的记录，请先运行一次干员识别",
                {"char_id": char_id},
            )
        if payload.get("elite", 0) > 2:
            raise AppError(
                ErrorCode.VALIDATION_ERROR,
                "字段 elite 不满足约束：超出取值范围",
                {"field": "elite", "reason": "超出取值范围", "allowed": {"minimum": 0, "maximum": 2}},
            )
        return {**self.rows[char_id], **{k: v for k, v in payload.items()}}

    async def list_diffs(self, *, include_dismissed: bool = False, limit: int = 200) -> dict:
        self.calls.append(
            ("list_diffs", {"include_dismissed": include_dismissed, "limit": limit})
        )
        items = [row for row in self.diffs if include_dismissed or row["dismissed_at"] is None]
        return {"items": items, "total": len(self.diffs)}

    async def dismiss_diffs(self, ids=None) -> int:
        self.calls.append(("dismiss_diffs", {"ids": ids}))
        return 0 if not self.diffs else len(self.diffs)


class FakeDepotService:
    def __init__(self) -> None:
        self.calls: list[tuple[str, dict]] = []
        self.items: list[dict] = []

    async def summary(self) -> dict:
        self.calls.append(("summary", {}))
        return {
            "total": len(self.items),
            "recognized_at": "2026-10-10T03:00:00Z",
            "stale": False,
            "stale_days": 3,
        }

    async def list_items(self, **kwargs) -> dict:
        self.calls.append(("list_items", kwargs))
        return {
            "items": self.items,
            "total": len(self.items),
            "limit": kwargs["limit"],
            "offset": kwargs["offset"],
        }


def _operator_row(char_id: str = "char_002_amiya") -> dict:
    return {
        "char_id": char_id,
        "name": "阿米娅",
        "rarity": 5,
        "profession": "术师",
        "own": True,
        "elite": 2,
        "level": 80,
        "potential": 6,
        "skill_level": 7,
        "masteries": {"1": 3, "2": 0, "3": 0},
        "modules": {"X": 1},
        "field_sources": {"elite": "recognition"},
        "recognized_at": "2026-10-10T03:00:00Z",
        "updated_at": "2026-10-10T03:00:00Z",
    }


def _app(*, with_services: bool = True) -> tuple[FastAPI, FakeBoxService, FakeDepotService]:
    app = FastAPI()
    register_exception_handlers(app)
    app.include_router(account_router.router)
    box, depot = FakeBoxService(), FakeDepotService()
    if with_services:
        app.state.account_box_service = box
        app.state.account_depot_service = depot
    return app, box, depot


def _error(response):
    return response.json()["error"]


# ---------------------------------------------------------------------------
# 鉴权与服务可用性
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("tmp_settings", ["s3cret"], indirect=True)
@pytest.mark.parametrize(("method", "path"), ENDPOINTS)
def test_account_endpoints_require_auth(make_client, tmp_settings, method, path):
    app, _box, _depot = _app()
    client = make_client(app)

    response = client.request(method, path, json={})
    assert response.status_code == 401
    assert _error(response)["code"] == "UNAUTHORIZED"

    ok = client.request(method, path, json={}, headers={"Authorization": "Bearer s3cret"})
    assert ok.status_code != 401


def test_account_service_unavailable_before_lifespan(make_client, tmp_settings):
    """服务还没装配（lifespan 未到位）时统一 503，不是 500。"""
    app, _box, _depot = _app(with_services=False)
    client = make_client(app)

    for method, path in ENDPOINTS:
        response = client.request(method, path, json={})
        assert response.status_code == 503
        assert _error(response)["code"] == "SERVICE_UNAVAILABLE"


# ---------------------------------------------------------------------------
# 概要
# ---------------------------------------------------------------------------


def test_summary_combines_box_and_depot(make_client, tmp_settings):
    app, box, depot = _app()
    client = make_client(app)

    response = client.get("/api/account/summary")
    assert response.status_code == 200
    payload = response.json()
    assert payload["box"]["total"] == 2
    assert payload["box"]["elite_two"] == 1
    assert payload["box"]["pending_diffs"] == 0
    assert payload["depot"] == {
        "total": 0,
        "recognized_at": "2026-10-10T03:00:00Z",
        "stale": False,
        "stale_days": 3,
    }
    assert [name for name, _ in box.calls] == ["summary"]
    assert [name for name, _ in depot.calls] == ["summary"]


# ---------------------------------------------------------------------------
# 干员列表：参数透传、分页与筛选
# ---------------------------------------------------------------------------


def test_list_operators_passes_filters_through(make_client, tmp_settings):
    app, box, _depot = _app()
    box.rows["char_002_amiya"] = _operator_row()
    client = make_client(app)

    response = client.get(
        "/api/account/operators"
        "?query=阿米&owned_only=false&rarity_min=4&profession=术师&elite_min=1"
        "&limit=10&offset=5"
    )
    assert response.status_code == 200
    payload = response.json()
    assert payload["total"] == 1
    assert payload["limit"] == 10 and payload["offset"] == 5
    assert payload["items"][0]["char_id"] == "char_002_amiya"

    _name, kwargs = box.calls[-1]
    assert kwargs == {
        "query": "阿米",
        "owned_only": False,
        "rarity_min": 4,
        "profession": "术师",
        "elite_min": 1,
        "limit": 10,
        "offset": 5,
    }


def test_list_operators_defaults(make_client, tmp_settings):
    app, box, _depot = _app()
    client = make_client(app)

    assert client.get("/api/account/operators").status_code == 200
    _name, kwargs = box.calls[-1]
    assert kwargs == {
        "query": None,
        "owned_only": True,
        "rarity_min": None,
        "profession": None,
        "elite_min": None,
        "limit": 50,
        "offset": 0,
    }


@pytest.mark.parametrize(
    "query",
    ["limit=0", "limit=201", "offset=-1", "rarity_min=0", "rarity_min=7", "elite_min=3"],
)
def test_list_operators_rejects_out_of_range_query(make_client, tmp_settings, query):
    app, _box, _depot = _app()
    client = make_client(app)

    response = client.get(f"/api/account/operators?{query}")
    assert response.status_code == 422
    assert _error(response)["code"] == "VALIDATION_ERROR"


def test_get_operator_404_and_detail(make_client, tmp_settings):
    app, box, _depot = _app()
    box.rows["char_002_amiya"] = _operator_row()
    client = make_client(app)

    assert client.get("/api/account/operators/char_002_amiya").json()["level"] == 80

    missing = client.get("/api/account/operators/char_none")
    assert missing.status_code == 404
    assert _error(missing)["code"] == "ACCOUNT_DATA_NOT_FOUND"
    assert _error(missing)["details"] == {"char_id": "char_none"}


# ---------------------------------------------------------------------------
# PATCH：请求体形状与校验
# ---------------------------------------------------------------------------


def test_patch_passes_only_given_fields_and_source(make_client, tmp_settings):
    app, box, _depot = _app()
    box.rows["char_002_amiya"] = _operator_row()
    client = make_client(app)

    response = client.patch(
        "/api/account/operators/char_002_amiya",
        json={"level": 42, "masteries": {"1": 3}, "source": "agent"},
    )
    assert response.status_code == 200
    _name, kwargs = box.calls[-1]
    assert kwargs == {
        "char_id": "char_002_amiya",
        "payload": {"level": 42, "masteries": {"1": 3}},
        "source": "agent",
    }
    # source 不进 payload（它是写入来源，不是字段）。
    assert "source" not in kwargs["payload"]


def test_patch_defaults_source_to_manual(make_client, tmp_settings):
    app, box, _depot = _app()
    box.rows["char_002_amiya"] = _operator_row()
    client = make_client(app)

    assert client.patch(
        "/api/account/operators/char_002_amiya", json={"own": False}
    ).status_code == 200
    assert box.calls[-1][1]["source"] == "manual"


@pytest.mark.parametrize(
    "body",
    [
        {"rarity": 6},          # 不可编辑字段（extra="forbid"）
        {"name": "别的名字"},     # 同上
        {"elite": 3},           # 字段范围
        {"level": 91},
        {"potential": 7},
        {"skill_level": 8},
        {"skill_level": 0},
        {"masteries": {"4": 1}},  # 专精键只能是 1..3
        {"modules": {"Z": 1}},    # 模组类型只能是 X/Y/D/A
        {"source": "recognition"},  # 只能 manual / agent
        {"own": "yes"},           # 布尔字段不收字符串
    ],
)
def test_patch_rejects_bad_request_bodies(make_client, tmp_settings, body):
    app, box, _depot = _app()
    box.rows["char_002_amiya"] = _operator_row()
    client = make_client(app)

    response = client.patch("/api/account/operators/char_002_amiya", json=body)
    assert response.status_code == 422, body
    assert _error(response)["code"] == "VALIDATION_ERROR"


def test_patch_surfaces_service_validation_error(make_client, tmp_settings):
    """服务层的跨字段约束（专精要精二且技能 7）以 422 + details.field 透出。

    这类约束请求模型表达不了（要看行里既有的精英化与技能等级），所以由服务层抛，
    路由层不做二次包装。
    """
    app, box, _depot = _app()
    box.rows["char_x"] = _operator_row("char_x")

    async def _reject(char_id, payload, *, source="manual"):
        raise AppError(
            ErrorCode.VALIDATION_ERROR,
            "字段 masteries 不满足约束：专精要求技能等级为 7",
            {
                "field": "masteries",
                "reason": "专精要求技能等级为 7",
                "allowed": {"skill_level_required": 7},
            },
        )

    box.patch_operator = _reject  # type: ignore[method-assign]
    client = make_client(app)

    response = client.patch("/api/account/operators/char_x", json={"masteries": {"1": 3}})
    assert response.status_code == 422
    error = _error(response)
    assert error["code"] == "VALIDATION_ERROR"
    assert error["details"]["field"] == "masteries"


# ---------------------------------------------------------------------------
# 差异
# ---------------------------------------------------------------------------


def test_list_diffs_defaults_and_flags(make_client, tmp_settings):
    app, box, _depot = _app()
    box.diffs.append(
        {
            "id": 1,
            "char_id": "char_002_amiya",
            "name": "阿米娅",
            "field": "level",
            "manual_value": 42,
            "recognized_value": 80,
            "created_at": "2026-10-10T03:00:00Z",
            "dismissed_at": None,
        }
    )
    client = make_client(app)

    default = client.get("/api/account/operator-diffs").json()
    assert default["total"] == 1
    assert default["items"][0]["field"] == "level"
    assert box.calls[-1][1] == {"include_dismissed": False, "limit": 200}

    flagged = client.get("/api/account/operator-diffs?include_dismissed=true&limit=5")
    assert flagged.status_code == 200
    assert box.calls[-1][1] == {"include_dismissed": True, "limit": 5}

    assert client.get("/api/account/operator-diffs?limit=501").status_code == 422


def test_dismiss_diffs_with_and_without_ids(make_client, tmp_settings):
    app, box, _depot = _app()
    box.diffs.append({"id": 1, "dismissed_at": None})
    client = make_client(app)

    selected = client.request(
        "DELETE", "/api/account/operator-diffs", json={"ids": [1, 2]}
    )
    assert selected.status_code == 200
    assert selected.json() == {"dismissed": 1}
    assert box.calls[-1][1] == {"ids": [1, 2]}

    # 不带请求体 = 全部（ids 透传 None）。
    everything = client.request("DELETE", "/api/account/operator-diffs")
    assert everything.status_code == 200
    assert box.calls[-1][1] == {"ids": None}

    # 未知字段仍然被挡。
    assert (
        client.request("DELETE", "/api/account/operator-diffs", json={"all": True}).status_code
        == 422
    )


# ---------------------------------------------------------------------------
# 仓库
# ---------------------------------------------------------------------------


def test_depot_list_passes_filters_and_reports_summary(make_client, tmp_settings):
    app, _box, depot = _app()
    depot.items.append(
        {
            "item_id": "30012",
            "name": "聚酸酯组",
            "count": 3,
            "category": "材料",
            "recognized_at": "2026-10-10T03:00:00Z",
        }
    )
    client = make_client(app)

    response = client.get("/api/account/depot?category=材料&query=聚酸&limit=20&offset=2")
    assert response.status_code == 200
    payload = response.json()
    assert payload["total"] == 1
    assert payload["limit"] == 20 and payload["offset"] == 2
    assert depot.calls[-1][1] == {
        "category": "材料",
        "query": "聚酸",
        "limit": 20,
        "offset": 2,
    }

    assert client.get("/api/account/depot").json()["total"] == 1
    assert depot.calls[-1][1] == {
        "category": None,
        "query": None,
        "limit": 100,
        "offset": 0,
    }
    assert client.get("/api/account/depot?limit=0").status_code == 422
    assert client.get("/api/account/depot?limit=501").status_code == 422


def test_router_declares_the_documented_paths():
    """附录 C 的路径集合不许漂移（改了路由名 docs/05 §6.20/§6.21 也要改）。"""
    paths = {
        (method, route.path)
        for route in account_router.router.routes
        for method in route.methods
    }
    assert paths == {
        ("GET", "/api/account/summary"),
        ("GET", "/api/account/operators"),
        ("GET", "/api/account/operators/{char_id}"),
        ("PATCH", "/api/account/operators/{char_id}"),
        ("GET", "/api/account/operator-diffs"),
        ("DELETE", "/api/account/operator-diffs"),
        ("GET", "/api/account/depot"),
        ("GET", "/api/account/upgrade-plans"),
        ("GET", "/api/account/upgrade-plans/{plan_id}"),
        ("DELETE", "/api/account/upgrade-plans/{plan_id}"),
    }


#: docs/05 §6.20 冻结的干员行字段：响应模型必须与它逐字一致，不许漂移。
OPERATOR_ROW_FIELDS = {
    "char_id",
    "name",
    "rarity",
    "profession",
    "own",
    "elite",
    "level",
    "potential",
    "skill_level",
    "masteries",
    "modules",
    "field_sources",
    "recognized_at",
    "updated_at",
}


@pytest.mark.parametrize(
    ("path", "model", "keys"),
    (
        (
            "/api/account/operators",
            "AccountOperatorList",
            {"items", "total", "limit", "offset"},
        ),
        (
            "/api/account/depot",
            "AccountDepotList",
            {"items", "total", "limit", "offset", "categories"},
        ),
    ),
)
def test_list_endpoints_pin_their_response_shape(path, model, keys):
    """两个只读列表端点必须带 ``response_model``：形状要进 OpenAPI。

    没有响应模型时 OpenAPI 里是自由对象，``web/src/types/api.d.ts`` 生成不出
    ``items`` / ``total``，前端读错键也不会被 ``pnpm typecheck`` 发现（M34 的根因）。
    这里的断言就是那条护栏的守卫：去掉装饰器上的 ``response_model`` 会立刻红。
    """
    app, _box, _depot = _app()
    schema = app.openapi()
    response = schema["paths"][path]["get"]["responses"]["200"]["content"][
        "application/json"
    ]["schema"]
    assert response == {"$ref": f"#/components/schemas/{model}"}, (
        f"{path} 的 200 响应必须是 {model}，不是自由对象"
    )
    defined = schema["components"]["schemas"][model]
    assert set(defined["properties"]) == keys
    assert {"items", "total", "limit", "offset"} <= set(defined["required"])


def test_operator_row_schema_matches_the_frozen_field_list():
    """行模型的字段与 docs/05 §6.20 的清单一致（前端切片与注入段都按它写）。"""
    app, _box, _depot = _app()
    row = app.openapi()["components"]["schemas"]["AccountOperatorRow"]
    assert set(row["properties"]) == OPERATOR_ROW_FIELDS


def test_router_accepts_the_main_app_state_keys(make_client, tmp_settings):
    """main.py 装配的是 ``box_service`` / ``depot_service`` 两个键，路由必须认它们。"""
    app = FastAPI()
    register_exception_handlers(app)
    app.include_router(account_router.router)
    app.state.box_service = FakeBoxService()
    app.state.depot_service = FakeDepotService()
    client = make_client(app)

    assert client.get("/api/account/summary").status_code == 200
    assert client.get("/api/account/operators").status_code == 200
    assert client.get("/api/account/depot").status_code == 200
