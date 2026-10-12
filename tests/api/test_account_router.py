"""账号数据 REST 契约（M27 规格 §7.5、附录 C）：鉴权、分页、筛选、PATCH 校验与 404。

用临时 SQLite 起真实服务（``isolated_db`` + ``create_all``），只有 app 是现搭的
（``tests/api`` 的纪律：不 import ``maa_api.main``）。路由注册由 main.py 负责，
这里显式 ``include_router``，因此本文件在路由还没接进 main 时也能独立跑。
"""

from __future__ import annotations

import asyncio
from datetime import datetime

import pytest
from fastapi import FastAPI
from sqlalchemy.ext.asyncio import AsyncEngine
from sqlmodel import SQLModel

import maa_api.db.models  # noqa: F401  # 注册全部表
from maa_api.api.errors import register_exception_handlers
from maa_api.api.routers import account as account_router
from maa_api.db import session as db_session
from maa_api.services.account.box import OperatorBoxService
from maa_api.services.account.depot import DepotService
from maa_api.settings import get_settings

import pytest

#: 依赖并行任务的落地物：仓储模块与五张表。还没落地时整模块 skip，落地后自动变绿
#: （见本里程碑的交接说明）；不把 ImportError 伪装成用例失败。
pytest.importorskip(
    "maa_api.db.repositories.account",
    reason="M27 的表与仓储（db/models.py、db/repositories/account.py）尚未落地",
)


#: 端点（方法, 路径），鉴权用例逐个打一遍。
ENDPOINTS = (
    ("GET", "/api/account/summary"),
    ("GET", "/api/account/operators"),
    ("GET", "/api/account/operators/char_002_amiya"),
    ("PATCH", "/api/account/operators/char_002_amiya"),
    ("GET", "/api/account/operator-diffs"),
    ("DELETE", "/api/account/operator-diffs"),
    ("GET", "/api/account/depot"),
)


async def _create_schema(engine: AsyncEngine) -> None:
    async with engine.begin() as connection:
        await connection.run_sync(SQLModel.metadata.create_all)


def _build_app(isolated_db: AsyncEngine) -> FastAPI:
    """现搭 app：注册处理器 + 账号路由 + 两个服务（拿临时库的会话工厂）。"""
    asyncio.run(_create_schema(isolated_db))
    app = FastAPI()
    register_exception_handlers(app)
    app.include_router(account_router.router)
    # 生产装配同款：服务自己拿 session_factory（已被 isolated_db 换成临时库）。
    app.state.account_box_service = OperatorBoxService(db_session.session_factory)
    app.state.account_depot_service = DepotService(db_session.session_factory)
    return app


def _client(isolated_db: AsyncEngine, make_client):
    """``(app, client)``：用例两边都要用（app 拿服务，client 发请求）。"""
    app = _build_app(isolated_db)
    return app, make_client(app)


def _seed_box(app: FastAPI, rows: dict[str, dict], *, recognized_at=None) -> None:
    """经服务层的识别入口写几行，避免用例直接依赖仓储的建行形状。

    ``recognized_at`` 显式传时这次识别会被记成那个时刻（差异用例要构造「先识别 →
    手动改 → 再识别」的顺序，靠它把两次识别分开）。
    """
    service = app.state.account_box_service

    async def scenario():
        await service.apply_recognition(rows, recognized_at=recognized_at)

    asyncio.run(scenario())


def _error(response):
    return response.json()["error"]


# ---------------------------------------------------------------------------
# 鉴权
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("tmp_settings", ["s3cret"], indirect=True)
@pytest.mark.parametrize(("method", "path"), ENDPOINTS)
def test_account_endpoints_require_auth(
    isolated_db, make_client, tmp_settings, method, path
):
    app, client = _client(isolated_db, make_client)

    response = client.request(method, path, json={})
    assert response.status_code == 401
    assert _error(response)["code"] == "UNAUTHORIZED"

    # 带上 token 就能过鉴权那一关（这里只要求不是 401）。
    ok = client.request(
        method, path, json={}, headers={"Authorization": "Bearer s3cret"}
    )
    assert ok.status_code != 401


def test_account_service_unavailable_before_lifespan(isolated_db, make_client, tmp_settings):
    """服务还没装配（lifespan 未到位）时统一 503，不是 500。"""
    app = FastAPI()
    register_exception_handlers(app)
    app.include_router(account_router.router)
    client = make_client(app)

    for method, path in ENDPOINTS:
        response = client.request(method, path, json={})
        assert response.status_code == 503
        assert _error(response)["code"] == "SERVICE_UNAVAILABLE"


# ---------------------------------------------------------------------------
# 概要
# ---------------------------------------------------------------------------


def test_account_summary_reports_box_and_depot(isolated_db, make_client, tmp_settings):
    app, client = _client(isolated_db, make_client)
    _seed_box(
        app,
        {
            "char_002_amiya": {"name": "阿米娅", "rarity": 5, "own": True, "elite": 2, "level": 80},
            "char_103_angel": {"name": "能天使", "rarity": 6, "own": True, "elite": 1, "level": 60},
            "char_010_chen": {"name": "陈", "rarity": 6, "own": False, "elite": 0, "level": 0},
        },
    )

    response = client.get("/api/account/summary")
    assert response.status_code == 200
    payload = response.json()
    assert set(payload) == {"box", "depot"}
    assert payload["box"]["total"] == 2
    assert payload["box"]["elite_two"] == 1
    assert payload["box"]["pending_diffs"] == 0
    assert payload["box"]["stale_days"] == get_settings().agent.box_stale_days == 14
    assert isinstance(payload["box"]["stale"], bool)
    assert payload["depot"] == {
        "total": 0,
        "recognized_at": None,
        "stale": True,
        "stale_days": 3,
    }


def test_account_summary_stale_uses_settings_env_override(
    isolated_db, make_client, tmp_settings, monkeypatch
):
    """阈值现读配置：``MAA_AGENT_BOX_STALE_DAYS=0`` 不可用（下限 1），改成 1 再断言。"""
    monkeypatch.setenv("MAA_AGENT_BOX_STALE_DAYS", "1")
    monkeypatch.setenv("MAA_AGENT_DEPOT_STALE_DAYS", "1")
    # 让 get_settings() 重新加载，带上刚设的环境变量。
    from maa_api.settings import load_settings, set_settings

    set_settings(load_settings())
    app, client = _client(isolated_db, make_client)

    payload = client.get("/api/account/summary").json()
    assert payload["box"]["stale_days"] == 1
    assert payload["depot"]["stale_days"] == 1


# ---------------------------------------------------------------------------
# 干员列表：筛选与分页
# ---------------------------------------------------------------------------


def _seeded_client(isolated_db, make_client):
    app, client = _client(isolated_db, make_client)
    _seed_box(
        app,
        {
            "char_002_amiya": {
                "name": "阿米娅",
                "rarity": 5,
                "profession": "术师",
                "own": True,
                "elite": 2,
                "level": 60,
                "potential": 6,
            },
            "char_103_angel": {
                "name": "能天使",
                "rarity": 6,
                "profession": "狙击",
                "own": True,
                "elite": 1,
                "level": 60,
                "potential": 1,
            },
            "char_010_chen": {
                "name": "陈",
                "rarity": 6,
                "profession": "近卫",
                "own": False,
                "elite": 0,
                "level": 0,
                "potential": 0,
            },
        },
    )
    return app, client


def test_list_operators_hides_unowned_by_default_and_filters(
    isolated_db, make_client, tmp_settings
):
    app, client = _seeded_client(isolated_db, make_client)

    default = client.get("/api/account/operators").json()
    assert default["total"] == 2
    assert default["limit"] == 50 and default["offset"] == 0
    assert {item["char_id"] for item in default["items"]} == {
        "char_002_amiya",
        "char_103_angel",
    }

    everyone = client.get("/api/account/operators?owned_only=false").json()
    assert everyone["total"] == 3

    assert client.get("/api/account/operators?rarity_min=6").json()["total"] == 1
    assert (
        client.get("/api/account/operators?profession=狙击").json()["total"] == 1
    )
    assert client.get("/api/account/operators?elite_min=2").json()["total"] == 1
    assert client.get("/api/account/operators?query=阿米").json()["total"] == 1
    assert (
        client.get(
            "/api/account/operators?owned_only=false&profession=近卫&rarity_min=6"
        ).json()["total"]
        == 1
    )


def test_list_operators_paginates(isolated_db, make_client, tmp_settings):
    app, client = _seeded_client(isolated_db, make_client)

    first = client.get("/api/account/operators?limit=1&offset=0").json()
    second = client.get("/api/account/operators?limit=1&offset=1").json()
    assert first["total"] == second["total"] == 2
    assert len(first["items"]) == len(second["items"]) == 1
    assert first["items"][0]["char_id"] != second["items"][0]["char_id"]
    assert first["limit"] == 1 and second["offset"] == 1

    # 越界参数由 Query 约束挡成 422，不进 SQLite。
    for bad in ("limit=0", "limit=201", "offset=-1", "rarity_min=7", "elite_min=3"):
        assert client.get(f"/api/account/operators?{bad}").status_code == 422


def test_operator_wire_shape_and_single_get(isolated_db, make_client, tmp_settings):
    app, client = _seeded_client(isolated_db, make_client)

    payload = client.get("/api/account/operators/char_002_amiya").json()
    assert set(payload) == {
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
        # M42 §2.3：信赖是森空岛独有的一列，与六列同口径可空。
        "favor_percent",
        "field_sources",
        "recognized_at",
        "updated_at",
    }
    # M35：识别给不出技能等级、专精与模组，wire 照实回 null（页面渲染成「未识别」）。
    assert payload["skill_level"] is None
    assert payload["masteries"] is None
    assert payload["modules"] is None
    # M42：没同步过森空岛就没有信赖。
    assert payload["favor_percent"] is None
    assert payload["field_sources"]["elite"] == "recognition"

    missing = client.get("/api/account/operators/char_none")
    assert missing.status_code == 404
    assert _error(missing)["code"] == "ACCOUNT_DATA_NOT_FOUND"
    assert _error(missing)["details"] == {"char_id": "char_none"}


# ---------------------------------------------------------------------------
# PATCH：校验与差异
# ---------------------------------------------------------------------------


def test_patch_operator_writes_fields_and_marks_source(isolated_db, make_client, tmp_settings):
    app, client = _seeded_client(isolated_db, make_client)

    response = client.patch(
        "/api/account/operators/char_002_amiya",
        json={"level": 42, "potential": 2, "source": "agent"},
    )
    assert response.status_code == 200
    payload = response.json()
    assert payload["level"] == 42
    assert payload["potential"] == 2
    assert payload["field_sources"]["level"] == "agent"
    assert payload["field_sources"]["potential"] == "agent"
    assert payload["updated_at"] is not None

    # 省略 source 时默认 manual。
    manual = client.patch(
        "/api/account/operators/char_002_amiya", json={"level": 43}
    ).json()
    assert manual["field_sources"]["level"] == "manual"


def test_patch_operator_rejects_unknown_fields_and_bad_values(
    isolated_db, make_client, tmp_settings
):
    app, client = _seeded_client(isolated_db, make_client)
    url = "/api/account/operators/char_002_amiya"

    # extra="forbid"：未知字段 422。
    unknown = client.patch(url, json={"rarity": 6})
    assert unknown.status_code == 422
    assert _error(unknown)["code"] == "VALIDATION_ERROR"

    for body in (
        {"elite": 3},
        {"level": 91},
        {"potential": 7},
        {"skill_level": 8},
        {"masteries": {"4": 1}},
        {"modules": {"Z": 1}},
        {"modules": {"X": 4}},
        {"source": "recognition"},
    ):
        response = client.patch(url, json=body)
        assert response.status_code == 422, body
        assert _error(response)["code"] == "VALIDATION_ERROR", body


def test_patch_masteries_only_judges_prerequisites_when_known(
    isolated_db, make_client, tmp_settings
):
    """M35：专精的前置只在**已知**时判；未知时不拦补录，已知时必须拦住。"""
    app, client = _seeded_client(isolated_db, make_client)
    url = "/api/account/operators/char_002_amiya"

    # 技能等级还没识别过（NULL）：先补录专精——拦住就没法录了。
    allowed = client.patch(url, json={"masteries": {"1": 3}})
    assert allowed.status_code == 200
    assert allowed.json()["masteries"] == {"1": 3, "2": 0, "3": 0}

    # 反向也护着：已有专精时把技能等级改到 7 级以下 → 422。
    lowered = client.patch(url, json={"skill_level": 4})
    assert lowered.status_code == 422
    assert _error(lowered)["code"] == "VALIDATION_ERROR"

    # 清空专精 → 技能等级已知为 4 级 → 同样的专精就被前置拦住了。
    assert client.patch(url, json={"masteries": {"1": 0}}).status_code == 200
    assert client.patch(url, json={"skill_level": 4}).status_code == 200
    rejected = client.patch(url, json={"masteries": {"1": 3}})
    assert rejected.status_code == 422
    assert _error(rejected)["code"] == "VALIDATION_ERROR"

    # 技能 7 级之后放行。
    assert client.patch(url, json={"skill_level": 7}).status_code == 200
    assert client.patch(url, json={"masteries": {"1": 3}}).status_code == 200


def test_patch_operator_404_for_unknown_operator(isolated_db, make_client, tmp_settings):
    app, client = _seeded_client(isolated_db, make_client)

    response = client.patch("/api/account/operators/char_none", json={"level": 10})
    assert response.status_code == 404
    assert _error(response)["code"] == "ACCOUNT_DATA_NOT_FOUND"


def test_operator_diffs_list_and_dismiss(isolated_db, make_client, tmp_settings):
    """识别覆盖手动值 → 差异列表可见；DELETE 逐条或全部标记已知。"""
    app, client = _seeded_client(isolated_db, make_client)
    url = "/api/account/operators/char_002_amiya"

    # 手动把等级改成 42（来源 manual），再用一次识别把它盖回 80。
    assert client.patch(url, json={"level": 42}).status_code == 200
    _seed_box(
        app,
        {
            "char_002_amiya": {
                "name": "阿米娅",
                "rarity": 5,
                "own": True,
                "elite": 2,
                "level": 80,
                "potential": 6,
            }
        },
    )

    diffs = client.get("/api/account/operator-diffs").json()
    assert diffs["total"] == 1
    item = diffs["items"][0]
    assert item["char_id"] == "char_002_amiya"
    assert item["field"] == "level"
    assert item["manual_value"] == 42
    assert item["recognized_value"] == 80
    assert item["dismissed_at"] is None

    # 页面顶部的提示条数也同步。
    assert client.get("/api/account/summary").json()["box"]["pending_diffs"] == 1

    dismissed = client.request(
        "DELETE", "/api/account/operator-diffs", json={"ids": [item["id"]]}
    )
    assert dismissed.status_code == 200
    assert dismissed.json() == {"dismissed": 1}
    assert client.get("/api/account/operator-diffs").json()["total"] == 0

    # 不带请求体 = 全部标记（幂等：没有未处理差异时返回 0）。
    assert client.request("DELETE", "/api/account/operator-diffs").json() == {
        "dismissed": 0
    }
    listed = client.get("/api/account/operator-diffs?include_dismissed=true").json()
    assert len(listed["items"]) == 1
    assert listed["total"] == 0


# ---------------------------------------------------------------------------
# 仓库
# ---------------------------------------------------------------------------


def test_depot_list_is_read_only_and_filterable(isolated_db, make_client, tmp_settings):
    app, client = _client(isolated_db, make_client)
    depot = app.state.account_depot_service

    async def scenario():
        await depot.replace_all(
            [
                {"item_id": "30012", "name": "聚酸酯组", "count": 3, "category": "材料"},
                {"item_id": "3231", "name": "技巧概要·卷1", "count": 40, "category": "技巧概要"},
            ],
            recognized_at=datetime(2026, 10, 10, 3, 0, 0),
        )

    asyncio.run(scenario())

    payload = client.get("/api/account/depot").json()
    assert payload["total"] == 2
    assert payload["limit"] == 100 and payload["offset"] == 0
    assert set(payload["items"][0]) == {
        "item_id",
        "name",
        "count",
        "category",
        "recognized_at",
    }
    assert payload["items"][0]["recognized_at"] == "2026-10-10T03:00:00Z"

    filtered = client.get("/api/account/depot?category=材料&limit=1").json()
    assert filtered["total"] == 1
    assert filtered["items"][0]["item_id"] == "30012"
    assert client.get("/api/account/depot?query=技巧").json()["total"] == 1
    assert client.get("/api/account/depot?limit=0").status_code == 422
    assert client.get("/api/account/depot?limit=501").status_code == 422

    summary = client.get("/api/account/summary").json()["depot"]
    assert summary["total"] == 2
    assert summary["recognized_at"] == "2026-10-10T03:00:00Z"
    assert summary["stale"] is False
