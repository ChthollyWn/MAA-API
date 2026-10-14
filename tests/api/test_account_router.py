"""账号数据 REST 契约（M27 规格 §7.5、附录 C）：鉴权、分页、筛选、404。

用临时 SQLite 起真实服务（``isolated_db`` + ``create_all``），只有 app 是现搭的
（``tests/api`` 的纪律：不 import ``maa_api.main``）。路由注册由 main.py 负责，
这里显式 ``include_router``，因此本文件在路由还没接进 main 时也能独立跑。

**M47 起账号干员数据是只读的**（docs/13 §8.70）：唯一写入者是森空岛练度同步，
``PATCH /api/account/operators/{char_id}`` 与两条 ``/operator-diffs`` 一起下线。
"""

from __future__ import annotations

import asyncio
from datetime import datetime, timedelta

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
from maa_api.services.skland.mapping import OperatorTrainingUpdate
from maa_api.settings import Settings, get_settings

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
    ("GET", "/api/account/depot"),
)

#: M47 删掉的写端点：路径要么没了（404），要么只剩 GET（405）。
REMOVED_ENDPOINTS = (
    ("PATCH", "/api/account/operators/char_002_amiya", 405),
    ("GET", "/api/account/operator-diffs", 404),
    ("DELETE", "/api/account/operator-diffs", 404),
)


async def _create_schema(engine: AsyncEngine) -> None:
    async with engine.begin() as connection:
        await connection.run_sync(SQLModel.metadata.create_all)


def _depot_settings_without_guard() -> Settings:
    """当前设置去掉仓库下限护栏（深拷贝，避免污染别的用例）。"""
    settings = get_settings().model_copy(deep=True)
    settings.agent.depot_min_items = 0
    return settings


def _build_app(isolated_db: AsyncEngine) -> FastAPI:
    """现搭 app：注册处理器 + 账号路由 + 两个服务（拿临时库的会话工厂）。"""
    asyncio.run(_create_schema(isolated_db))
    app = FastAPI()
    register_exception_handlers(app)
    app.include_router(account_router.router)
    # 生产装配同款：服务自己拿 session_factory（已被 isolated_db 换成临时库）。
    app.state.account_box_service = OperatorBoxService(db_session.session_factory)
    # 仓库下限护栏（M44）默认 20，用例只塞 2 件：这里显式关掉；但仍要**现读**设置
    # （stale_days 那条用例靠 env override 改它），所以返回的是当前设置的深拷贝。
    app.state.account_depot_service = DepotService(
        db_session.session_factory, settings_provider=_depot_settings_without_guard
    )
    return app


def _client(isolated_db: AsyncEngine, make_client):
    """``(app, client)``：用例两边都要用（app 拿服务，client 发请求）。"""
    app = _build_app(isolated_db)
    return app, make_client(app)


def _seed_box(app: FastAPI, rows: dict[str, dict]) -> None:
    """经森空岛同步的写入口造几行——M47 起这是唯一的写通道。"""
    service = app.state.account_box_service
    updates = [
        OperatorTrainingUpdate(char_id=char_id, **fields)
        for char_id, fields in rows.items()
    ]

    async def scenario():
        await service.apply_skland_sync(updates)

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


@pytest.mark.parametrize(("method", "path", "status"), REMOVED_ENDPOINTS)
def test_removed_write_endpoints_are_gone(
    isolated_db, make_client, tmp_settings, method, path, status
):
    """M47：手动编辑与识别差异两条写通道下线，路由不再对外（docs/13 §8.70）。"""
    _app, client = _client(isolated_db, make_client)

    assert client.request(method, path, json={}).status_code == status


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
    # M47：差异条随写通道下线，概要里没有 pending_diffs。
    assert "pending_diffs" not in payload["box"]
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


def test_operator_list_rows_carry_the_new_columns(isolated_db, make_client, tmp_settings):
    """列表路由有 `response_model`，行模型漏字段就会**静默丢掉**那一列。

    M42 的 `favor_percent` 就是这么丢的：单条读取返回裸 dict（带上了），列表走
    `AccountOperatorList` 过滤（没声明就没了）。M43 补模型的同时补上这条断言，
    免得以后再漏。
    """
    app, client = _seeded_client(isolated_db, make_client)

    payload = client.get("/api/account/operators").json()
    assert payload["items"], "用例前提：至少一行"
    row = payload["items"][0]
    for field in ("favor_percent", "skill_ids", "module_details"):
        assert field in row, f"列表行缺少 {field}（response_model 又漏了吗）"


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
        # M43 §4.3：卡片用的技能 id 与模组明细，同样是「不知道就 None」。
        "skill_ids",
        "module_details",
        "field_sources",
        "recognized_at",
        "updated_at",
    }
    # 这一轮同步没给这三项：wire 照实回 null（页面渲染成「未同步」），不兜底成 0。
    assert payload["skill_level"] is None
    assert payload["masteries"] is None
    assert payload["modules"] is None
    # 没同步过森空岛就没有信赖。
    assert payload["favor_percent"] is None
    # M47：唯一的写来源是森空岛同步。
    assert payload["field_sources"]["elite"] == "skland"
    assert payload["field_sources"]["own"] == "skland"

    missing = client.get("/api/account/operators/char_none")
    assert missing.status_code == 404
    assert _error(missing)["code"] == "ACCOUNT_DATA_NOT_FOUND"
    assert _error(missing)["details"] == {"char_id": "char_none"}


# ---------------------------------------------------------------------------
# 仓库
# ---------------------------------------------------------------------------


def test_depot_list_is_read_only_and_filterable(isolated_db, make_client, tmp_settings):
    app, client = _client(isolated_db, make_client)
    depot = app.state.account_depot_service

    # 识别时刻取「两小时前的整点」而不是写死日期：`stale` 判的是真实时钟，
    # 写死的日期一旦跨过 `agent.depot_stale_days`（默认 3 天）这条用例就会假红
    # （同一类问题在 tests/services/test_account_depot_service.py 里已经踩过一次）。
    stamp = datetime.now().replace(minute=0, second=0, microsecond=0) - timedelta(hours=2)

    async def scenario():
        await depot.replace_all(
            [
                {"item_id": "30012", "name": "聚酸酯组", "count": 3, "category": "材料"},
                {"item_id": "3231", "name": "技巧概要·卷1", "count": 40, "category": "技巧概要"},
            ],
            recognized_at=stamp,
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
    assert payload["items"][0]["recognized_at"] == stamp.isoformat() + "Z"

    filtered = client.get("/api/account/depot?category=材料&limit=1").json()
    assert filtered["total"] == 1
    assert filtered["items"][0]["item_id"] == "30012"
    assert client.get("/api/account/depot?query=技巧").json()["total"] == 1
    assert client.get("/api/account/depot?limit=0").status_code == 422
    assert client.get("/api/account/depot?limit=501").status_code == 422

    summary = client.get("/api/account/summary").json()["depot"]
    assert summary["total"] == 2
    assert summary["recognized_at"] == stamp.isoformat() + "Z"
    assert summary["stale"] is False
