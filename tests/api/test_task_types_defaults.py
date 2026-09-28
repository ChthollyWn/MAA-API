"""``GET /api/tasks/types`` 系列的渠道默认值 overlay（M17 · spec §3.4、§5.4）。

契约（spec §3.4）：``client_type`` / ``server`` 在 pydantic 模型里**保持**
``default=None``，服务端按 ``model_fields_set`` 注入设置表里的全局渠道默认值；
但这两个 types 端点在**响应**的 schema 里给它们注入「当前生效」的 ``default``，
让前端按生效值预填并随提交显式下发。

因此本文件的四组断言：

1. 默认设置下，含 ``client_type`` 的类型 ``default == "Bilibili"``、含 ``server``
   的类型 ``default == "CN"``（两个端点都覆盖）；
2. setting 表写 ``channel.client_type=Official`` / ``channel.server=JP`` 后重新
   请求，overlay 跟着变（同一 client 的第二次请求，钉住「没有进程内缓存」）；
3. overlay 只作用于**响应副本**：``Model.model_json_schema()`` 的结果（包括被
   调用方缓存/复用同一份 dict 的极端情况）不被改写；
4. ``export_type_schema(model)`` 的单参调用仍然可用（``scripts/
   generate_task_types_fixture.py`` 与 ``tests/domain`` 在用），且拿到的是内置
   渠道默认值 —— 这样 fixture 与端点返回的结构才一致。

app 现搭的约定同 ``tests/api/conftest.py``：不 import ``maa_api.main``；两个端点
在 M17 起依赖 ``get_session``，所以一律挂 ``prepared_db``（隔离库 + 建表），
绝不碰仓库真实 ``resource/maa_api.db``。
"""

from __future__ import annotations

import asyncio
import copy
from collections.abc import Callable, Iterator
from typing import Any

import pytest
from fastapi import FastAPI
from sqlalchemy.ext.asyncio import AsyncEngine
from sqlmodel import SQLModel

import maa_api.db.models  # noqa: F401  # 注册 13 张表，create_all 才有表可建
from maa_api.api import deps
from maa_api.api.errors import register_exception_handlers
from maa_api.api.routers import tasks
from maa_api.db import session as db_session
from maa_api.db.repositories.setting import SettingRepository
from maa_api.domain.task import TASK_MODELS, ChannelDefaults
from maa_api.settings import Settings

#: spec §3.4：含 ``client_type`` 的三个类型。
CLIENT_TYPE_TYPES = ("StartUp", "CloseDown", "Fight")
#: spec §3.4：含 ``server`` 的两个类型。
SERVER_TYPES = ("Fight", "Recruit")
#: 十个类型里既没有 ``client_type`` 也没有 ``server`` 的，overlay 不得凭空加键。
CHANNELLESS_TYPES = ("Infrast", "Mall", "Award", "Roguelike", "Reclamation", "Copilot")


# ----------------------------------------------------------------------
# 夹具与库准备
# ----------------------------------------------------------------------


@pytest.fixture(autouse=True)
def _clean_rate_limiter() -> Iterator[None]:
    """清空进程内鉴权失败计数，避免与同进程其它 tests/api 用例互相污染。"""
    deps.reset_rate_limiter()
    yield
    deps.reset_rate_limiter()


@pytest.fixture
def app(tmp_settings: Settings) -> FastAPI:
    """装了统一异常处理器与 tasks 路由的现搭 app（token 为空＝免鉴权）。"""
    application = FastAPI()
    register_exception_handlers(application)
    application.include_router(tasks.router)
    return application


def _prepare_db(engine: AsyncEngine, values: dict[str, Any] | None = None) -> None:
    """建表（可选写 setting），随后释放连接池（M2-13 的既有做法）。"""

    async def scenario() -> None:
        async with engine.begin() as conn:
            await conn.run_sync(SQLModel.metadata.create_all)
        if values:
            async with db_session.session_factory() as session:
                repo = SettingRepository(session)
                for key, value in values.items():
                    await repo.set(key, value, updated_by="manual")
                await session.commit()

    asyncio.run(scenario())
    engine.sync_engine.dispose()


@pytest.fixture
def prepared_db(isolated_db: AsyncEngine, tmp_settings: Settings) -> AsyncEngine:
    """建好表、setting 表为空的隔离库。"""
    _prepare_db(isolated_db)
    return isolated_db


# ----------------------------------------------------------------------
# 断言辅助
# ----------------------------------------------------------------------


def _properties(body: dict[str, Any]) -> dict[str, dict[str, Any]]:
    """``{"Fight": {<字段名>: <字段 schema>}, ...}``，忽略分页信封。"""
    return {
        item["name"]: item["schema"]["properties"] for item in body["items"]
    }


def _get_types(client: Any) -> dict[str, dict[str, Any]]:
    response = client.get("/api/tasks/types")
    assert response.status_code == 200, response.text
    return _properties(response.json())


def _get_type(client: Any, type_name: str) -> dict[str, Any]:
    response = client.get(f"/api/tasks/types/{type_name}")
    assert response.status_code == 200, response.text
    return response.json()["schema"]["properties"]


# ----------------------------------------------------------------------
# 1. 默认设置下的 overlay
# ----------------------------------------------------------------------


def test_list_types_overlays_builtin_channel_defaults(
    app: FastAPI, make_client: Callable[..., Any], prepared_db: AsyncEngine
) -> None:
    """每个含 client_type / server 的类型都拿到内置渠道默认值。"""
    properties = _get_types(make_client(app))

    assert {
        name for name, props in properties.items() if "client_type" in props
    } == set(CLIENT_TYPE_TYPES)
    assert {
        name for name, props in properties.items() if "server" in props
    } == set(SERVER_TYPES)

    for name in CLIENT_TYPE_TYPES:
        assert properties[name]["client_type"]["default"] == "Bilibili", name
    for name in SERVER_TYPES:
        assert properties[name]["server"]["default"] == "CN", name

    # 三态语义不变：null 分支仍在（前端据此表达「显式留空」）
    assert {"type": "null"} in properties["Fight"]["client_type"]["anyOf"]
    assert {"type": "null"} in properties["Fight"]["server"]["anyOf"]

    # 没有这两个字段的类型不得凭空多出键
    for name in CHANNELLESS_TYPES:
        assert "client_type" not in properties[name], name
        assert "server" not in properties[name], name


def test_single_type_endpoint_overlays_builtin_channel_defaults(
    app: FastAPI, make_client: Callable[..., Any], prepared_db: AsyncEngine
) -> None:
    """``/types/{type_name}`` 与清单端点返回同一套 overlay。"""
    client = make_client(app)

    fight = _get_type(client, "Fight")
    assert fight["client_type"]["default"] == "Bilibili"
    assert fight["server"]["default"] == "CN"

    # Recruit 只有 server；StartUp 只有 client_type
    assert _get_type(client, "Recruit")["server"]["default"] == "CN"
    assert _get_type(client, "StartUp")["client_type"]["default"] == "Bilibili"

    infrast = _get_type(client, "Infrast")
    assert "client_type" not in infrast and "server" not in infrast


# ----------------------------------------------------------------------
# 2. overlay 跟随 setting 表
# ----------------------------------------------------------------------


def test_overlay_follows_setting_table_changes(
    app: FastAPI,
    make_client: Callable[..., Any],
    isolated_db: AsyncEngine,
    prepared_db: AsyncEngine,
) -> None:
    """改 setting 后重新请求（同一个 client）即拿到新生效值。"""
    client = make_client(app)
    assert _get_types(client)["Fight"]["client_type"]["default"] == "Bilibili"

    _prepare_db(isolated_db, {"channel.client_type": "Official", "channel.server": "JP"})

    properties = _get_types(client)
    assert properties["StartUp"]["client_type"]["default"] == "Official"
    assert properties["CloseDown"]["client_type"]["default"] == "Official"
    assert properties["Fight"]["client_type"]["default"] == "Official"
    assert properties["Fight"]["server"]["default"] == "JP"
    assert properties["Recruit"]["server"]["default"] == "JP"

    single = _get_type(client, "Fight")
    assert single["client_type"]["default"] == "Official"
    assert single["server"]["default"] == "JP"


def test_overlay_falls_back_when_setting_value_is_invalid(
    app: FastAPI, make_client: Callable[..., Any], isolated_db: AsyncEngine
) -> None:
    """设置表里的脏值只回退该字段，不让端点 500（与 /validate 同口径）。"""
    _prepare_db(isolated_db, {"channel.client_type": "Nope"})
    properties = _get_types(make_client(app))
    assert properties["Fight"]["client_type"]["default"] == "Bilibili"
    assert properties["Fight"]["server"]["default"] == "CN"


# ----------------------------------------------------------------------
# 3. 无副作用：不污染模型
# ----------------------------------------------------------------------


def test_overlay_does_not_pollute_model_json_schema(
    app: FastAPI, make_client: Callable[..., Any], prepared_db: AsyncEngine
) -> None:
    """注入后再取 ``Model.model_json_schema()``：两个字段仍是 ``default=None``。"""
    client = make_client(app)
    assert _get_types(client)["Fight"]["client_type"]["default"] == "Bilibili"
    assert _get_type(client, "Fight")["server"]["default"] == "CN"

    for name in ("StartUp", "CloseDown", "Fight", "Recruit"):
        raw = TASK_MODELS[name].model_json_schema(ref_template="#/$defs/{model}")
        for field_name in ("client_type", "server"):
            field = raw["properties"].get(field_name)
            if field is not None:
                assert field.get("default") is None, (name, field_name)


def test_overlay_writes_to_a_copy_of_a_shared_schema(
    app: FastAPI,
    make_client: Callable[..., Any],
    prepared_db: AsyncEngine,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """即使 ``model_json_schema()`` 返回同一份被复用的 dict，也不被就地改写。

    pydantic 2.11 实测每次都返回新 dict，所以上一条用例挡不住「就地改返回值」；
    这里把返回值换成固定的一份（模拟带缓存的实现），直接钉住「注入写在副本上」。
    """
    model = TASK_MODELS["Fight"]
    cached = model.model_json_schema(ref_template="#/$defs/{model}")
    before = copy.deepcopy(cached)

    def _cached_json_schema(
        cls: type[Any], **kwargs: Any
    ) -> dict[str, Any]:  # pragma: no cover - 仅作为替身
        return cached

    monkeypatch.setattr(model, "model_json_schema", classmethod(_cached_json_schema))

    client = make_client(app)
    response = client.get("/api/tasks/types/Fight")
    assert response.status_code == 200, response.text
    props = response.json()["schema"]["properties"]
    assert props["client_type"]["default"] == "Bilibili"
    assert props["server"]["default"] == "CN"
    # 被复用的那份 dict 原样不动
    assert cached == before
    assert cached["properties"]["client_type"]["default"] is None


# ----------------------------------------------------------------------
# 4. 单参调用（fixture 生成器与 tests/domain 的接缝）
# ----------------------------------------------------------------------


def test_export_type_schema_single_arg_uses_builtin_channel_defaults() -> None:
    """``export_type_schema(model)`` 仍可单参调用，拿到内置渠道默认值。

    ``scripts/generate_task_types_fixture.py`` 直接调它生成前端 fixture，
    文件头要求「fixture 必须与 ``GET /api/tasks/types`` 返回的结构完全一致」。
    """
    props = tasks.export_type_schema(TASK_MODELS["Fight"])["schema"]["properties"]
    assert props["client_type"]["default"] == "Bilibili"
    assert props["server"]["default"] == "CN"


def test_export_type_schema_accepts_explicit_channel_defaults() -> None:
    """显式传入的 ChannelDefaults 按值生效（端点的传参路径）。"""
    item = tasks.export_type_schema(
        TASK_MODELS["Fight"],
        ChannelDefaults(client_type="Official", server="US"),
    )
    props = item["schema"]["properties"]
    assert props["client_type"]["default"] == "Official"
    assert props["server"]["default"] == "US"
    # groups / 其余字段不受 overlay 影响
    assert item["groups"] == ["基础", "资源消耗", "账号", "数据上报", "高级"]
    assert props["stage"].get("default") is None or "default" not in props["stage"]
