"""``maa_api/api/routers/tasks.py`` 的行为验收（M3-08）。

被测 app 现搭（``tests/api/conftest.py`` 的约定：不 import ``maa_api.main``），
异常处理器用 M3-04 的 :func:`~maa_api.api.errors.register_exception_handlers`，
这样 ``AppError`` 的状态码与请求体校验的 400/422 分界才与生产一致。

``POST /validate`` 依赖 ``get_session``，相关用例一律挂 ``isolated_db``：既不碰
仓库真实 ``resource/maa_api.db``，也让「setting 表写 Official → 注入 Official」
走真实仓储。:func:`_run_db_setup` 在 ``asyncio.run`` 里建表/写入后显式
``dispose()``：``isolated_db`` 的 engine 带连接池，不清掉的话 TestClient 的
事件循环会拿到绑在已关闭循环上的连接（M2-13 实测，``tests/db`` 用 NullPool 规避）。
"""

from __future__ import annotations

import asyncio
from collections.abc import Iterator
from typing import Any, Literal

import pytest
from fastapi import FastAPI
from pydantic import BaseModel
from sqlalchemy.ext.asyncio import AsyncEngine
from sqlmodel import SQLModel

import maa_api.db.models  # noqa: F401  # 注册 13 张表，create_all 才有表可建
from maa_api.api import deps
from maa_api.api.errors import register_exception_handlers
from maa_api.api.routers import tasks
from maa_api.db import session as db_session
from maa_api.db.repositories.setting import SettingRepository
from maa_api.settings import Settings

#: ``TaskInput`` 判别联合的定义顺序（docs/05 §7.4），也是 /types 的清单顺序。
EXPECTED_TYPES = [
    "StartUp",
    "CloseDown",
    "Fight",
    "Recruit",
    "Infrast",
    "Mall",
    "Award",
    "Roguelike",
    "Reclamation",
]


# ----------------------------------------------------------------------
# 夹具与库准备
# ----------------------------------------------------------------------


@pytest.fixture(autouse=True)
def _clean_rate_limiter() -> Iterator[None]:
    """清空进程内鉴权失败计数：401 用例与同进程的 tests/api 用例互不污染。"""
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


def _run_db_setup(
    engine: AsyncEngine, values: dict[str, Any] | None = None
) -> None:
    """在隔离库里建好 13 张表（可选写入若干 setting），随后释放连接池。"""

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
    """建好表、setting 表为空的隔离库；validate 用例的会话都连它。"""
    _run_db_setup(isolated_db)
    return isolated_db


# ----------------------------------------------------------------------
# export_type_schema / _collect_groups
# ----------------------------------------------------------------------


def test_task_models_cover_exactly_nine_types() -> None:
    """TASK_MODELS 由判别联合推导，键与顺序都是 docs/05 §7.4 的 9 类。"""
    assert len(tasks.TASK_MODELS) == 9
    assert list(tasks.TASK_MODELS) == EXPECTED_TYPES


def test_every_exported_schema_is_well_formed() -> None:
    """每个类型都要有说明、合法 JSON Schema、非空分组与中文标签。"""
    for name, model in tasks.TASK_MODELS.items():
        item = tasks.export_type_schema(model)
        assert set(item) == {
            "name",
            "label",
            "description",
            "schema",
            "groups",
            "runtime_immutable",
        }, name
        assert item["name"] == name
        assert item["label"]
        assert item["description"].strip(), name
        schema = item["schema"]
        assert schema["type"] == "object", name
        assert schema["additionalProperties"] is False, name
        assert schema["properties"], name
        assert item["groups"] and item["groups"][0] == tasks.BASE_GROUP, name
        assert len(item["groups"]) == len(set(item["groups"])), name
        for field_name, field in schema["properties"].items():
            assert field.get("x-label"), (name, field_name)
            assert field.get("x-group"), (name, field_name)


def test_fight_groups_are_ordered_and_deduplicated() -> None:
    """Fight 的分组顺序即渲染顺序（docs/05 §8.1 的示例）。"""
    item = tasks.export_type_schema(tasks.TASK_MODELS["Fight"])
    assert item["groups"] == ["基础", "资源消耗", "账号", "数据上报", "高级"]
    assert item["runtime_immutable"] == ["stage"]


def test_collect_groups_falls_back_to_base_group() -> None:
    """没有 x-group 的字段归入「基础」，且「基础」始终排在队首。"""
    schema = {
        "properties": {
            "a": {"x-group": "账号"},
            "b": {"type": "integer"},
            "c": {"x-group": "账号"},
        }
    }
    assert tasks._collect_groups(schema) == ["基础", "账号"]


def test_enum_labels_keys_are_strings() -> None:
    """x-enum-labels 的键在 JSON 里必然是字符串（docs/09 §7.2）。"""
    props = tasks.export_type_schema(tasks.TASK_MODELS["Fight"])["schema"]["properties"]
    labels = props["series"]["x-enum-labels"]
    assert all(isinstance(key, str) for key in labels)
    assert set(labels) == {str(value) for value in range(-1, 7)}
    assert labels["-1"] == "禁用切换"
    assert labels["0"] == "自动选择最大可用次数"


def test_cross_field_dependencies_use_x_depends_on() -> None:
    """跨字段规则无法用标准 JSON Schema 表达，统一走 x-depends-on。"""
    recruit = tasks.export_type_schema(tasks.TASK_MODELS["Recruit"])["schema"][
        "properties"
    ]
    assert recruit["expedite_times"]["x-depends-on"] == {"expedite": True}
    assert recruit["set_time"]["x-depends-on"] == {"times": 0}
    fight = tasks.export_type_schema(tasks.TASK_MODELS["Fight"])["schema"]["properties"]
    assert fight["penguin_id"]["x-depends-on"] == {"report_to_penguin": True}


def _value_schema(field: dict[str, Any]) -> dict[str, Any]:
    """可选字段的常规形态是 ``anyOf: [{...}, {"type": "null"}]``（docs/09 §7.2），
    取非 null 分支；取值范围与类型都在那个分支里。"""
    branches = field.get("anyOf")
    if not branches:
        return field
    return next(branch for branch in branches if branch.get("type") != "null")


def test_export_keeps_x_keywords_and_native_constraints() -> None:
    """x-* 关键字原样保留；取值范围用原生关键字承载（docs/05 §8.2）。"""
    props = tasks.export_type_schema(tasks.TASK_MODELS["Fight"])["schema"]["properties"]
    assert props["stone"]["x-risk"] == "consume"
    assert _value_schema(props["stone"])["minimum"] == 0
    assert props["drops"]["x-widget"] == "item-count-map"
    assert props["series"]["x-widget"] == "select"
    series = _value_schema(props["series"])
    assert series["minimum"] == -1 and series["maximum"] == 6
    assert _value_schema(props["stage"])["type"] == "string"
    # 可选字段的 null 分支照旧导出，前端自己剔除（不是特例）
    assert {"type": "null"} in props["stage"]["anyOf"]


def test_export_uses_ref_template_for_nested_models(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """ref_template 指向 ``#/$defs/{model}``；嵌套模型的 $ref 因此可被前端解析。"""

    class Inner(BaseModel):
        value: int = 0

    class Outer(BaseModel):
        name: Literal["Probe"] = "Probe"
        inner: Inner = Inner()

    monkeypatch.setitem(tasks.TASK_LABELS, "Probe", "探针")
    monkeypatch.setitem(tasks.RUNTIME_IMMUTABLE, "Probe", [])

    schema = tasks.export_type_schema(Outer)["schema"]
    assert "Inner" in schema["$defs"]
    assert schema["properties"]["inner"]["$ref"] == "#/$defs/Inner"


def test_nine_task_schemas_have_no_nested_defs_today() -> None:
    """当前 9 个模型没有嵌套模型，$defs 为空（ref_template 是给将来留的契约）。"""
    for name, model in tasks.TASK_MODELS.items():
        schema = tasks.export_type_schema(model)["schema"]
        assert schema.get("$defs", {}) == {}, name


# ----------------------------------------------------------------------
# GET /api/tasks/types
# ----------------------------------------------------------------------


def test_list_types_returns_fixed_pagination_envelope(
    app: FastAPI, make_client: Any
) -> None:
    client = make_client(app)
    response = client.get("/api/tasks/types")
    assert response.status_code == 200, response.text
    body = response.json()
    assert set(body) == {"items", "total", "page", "size"}
    assert (body["total"], body["page"], body["size"]) == (9, 1, 9)
    assert [item["name"] for item in body["items"]] == EXPECTED_TYPES


def test_list_types_accepts_zh_and_rejects_other_languages(
    app: FastAPI, make_client: Any
) -> None:
    client = make_client(app)
    assert client.get("/api/tasks/types", params={"lang": "zh"}).status_code == 200
    response = client.get("/api/tasks/types", params={"lang": "en"})
    assert response.status_code == 400, response.text
    assert response.json()["error"]["code"] == "INVALID_PARAMETER"


# ----------------------------------------------------------------------
# GET /api/tasks/types/{type_name}
# ----------------------------------------------------------------------


def test_get_type_returns_single_item_without_envelope(
    app: FastAPI, make_client: Any
) -> None:
    client = make_client(app)
    response = client.get("/api/tasks/types/Fight")
    assert response.status_code == 200, response.text
    item = response.json()
    assert set(item) == {
        "name",
        "label",
        "description",
        "schema",
        "groups",
        "runtime_immutable",
    }
    assert item["name"] == "Fight"
    assert item["label"] == "刷理智"
    assert item["runtime_immutable"] == ["stage"]


def test_routes_are_registered_before_the_parametric_one(app: FastAPI) -> None:
    """四个端点都挂上；具体路径必须声明在 ``/{task_id}`` 通用详情路由之前。

    docs/05 §6.5：``GET /api/tasks/{task_id}`` 必须声明在类型 schema 路由之后，
    否则 ``types`` 会被当成 task_id 走进详情端点。本用例同时钉住声明顺序
    与「路由确实挂上」——fastapi 0.141 的 ``include_router`` 是惰性的，
    ``app.routes`` 里只有 ``_IncludedRouter`` 包装对象、没有拍平后的 ``.path``，
    所以路径集合取 OpenAPI，声明顺序取模块级 ``router.routes``。
    """
    paths = {
        "/api/tasks/types",
        "/api/tasks/types/{type_name}",
        "/api/tasks/validate",
        "/api/tasks/{task_id}",
    }
    assert paths <= set(app.openapi()["paths"])
    assert [route.path for route in tasks.router.routes] == [
        "/api/tasks/types",
        "/api/tasks/types/{type_name}",
        "/api/tasks/validate",
        "/api/tasks/{task_id}",
    ]


def test_get_unknown_type_is_400_unknown_task_type(
    app: FastAPI, make_client: Any
) -> None:
    client = make_client(app)
    response = client.get("/api/tasks/types/Nope")
    assert response.status_code == 400, response.text
    error = response.json()["error"]
    assert error["code"] == "UNKNOWN_TASK_TYPE"
    assert error["details"]["type_name"] == "Nope"


# ----------------------------------------------------------------------
# 鉴权
# ----------------------------------------------------------------------


@pytest.mark.parametrize("tmp_settings", ["s3cret"], indirect=True)
def test_business_endpoints_require_token_when_configured(
    app: FastAPI, make_client: Any
) -> None:
    client = make_client(app)
    assert client.get("/api/tasks/types").status_code == 401
    assert client.get("/api/tasks/types/Fight").status_code == 401
    assert (
        client.post("/api/tasks/validate", json={"tasks": [{"name": "Fight"}]}).status_code
        == 401
    )
    ok = client.get("/api/tasks/types", headers={"Authorization": "Bearer s3cret"})
    assert ok.status_code == 200, ok.text


# ----------------------------------------------------------------------
# POST /api/tasks/validate
# ----------------------------------------------------------------------


def test_validate_injects_channel_defaults(
    app: FastAPI, make_client: Any, prepared_db: AsyncEngine
) -> None:
    """没提 client_type/server 的任务注入 setting 默认值（Bilibili / CN）。"""
    client = make_client(app)
    response = client.post(
        "/api/tasks/validate", json={"tasks": [{"name": "Fight", "stage": "1-7"}]}
    )
    assert response.status_code == 200, response.text
    body = response.json()
    assert set(body) == {"items", "total", "page", "size"}
    assert (body["total"], body["page"], body["size"]) == (1, 1, 1)

    item = body["items"][0]
    assert set(item) == {"type_name", "task_name", "params", "raw_params"}
    assert item["type_name"] == "Fight"
    assert item["task_name"] == "刷理智"
    assert item["params"] == {
        "stage": "1-7",
        "client_type": "Bilibili",
        "server": "CN",
    }
    # raw_params 是原始提交快照：不含注入键
    assert item["raw_params"] == {"stage": "1-7"}


def test_validate_keeps_explicit_null_and_does_not_inject_it(
    app: FastAPI, make_client: Any, prepared_db: AsyncEngine
) -> None:
    """显式 null＝刻意留空：不注入、不下发，但 raw_params 保留 null。"""
    client = make_client(app)
    response = client.post(
        "/api/tasks/validate",
        json={"tasks": [{"name": "Fight", "stage": "1-7", "client_type": None}]},
    )
    item = response.json()["items"][0]
    assert "client_type" not in item["params"]
    assert item["params"]["server"] == "CN"  # server 没提 → 照常注入
    assert item["raw_params"] == {"stage": "1-7", "client_type": None}


def test_validate_keeps_explicit_values(
    app: FastAPI, make_client: Any, prepared_db: AsyncEngine
) -> None:
    client = make_client(app)
    response = client.post(
        "/api/tasks/validate",
        json={"tasks": [{"name": "Fight", "client_type": "Official", "server": "JP"}]},
    )
    item = response.json()["items"][0]
    assert item["params"]["client_type"] == "Official"
    assert item["params"]["server"] == "JP"
    assert item["raw_params"] == {"client_type": "Official", "server": "JP"}


def test_validate_does_not_inject_fields_the_model_lacks(
    app: FastAPI, make_client: Any, prepared_db: AsyncEngine
) -> None:
    """Infrast 没有 client_type/server 字段，注入逻辑不得凭空加键。"""
    client = make_client(app)
    response = client.post("/api/tasks/validate", json={"tasks": [{"name": "Infrast"}]})
    item = response.json()["items"][0]
    assert item["params"] == {}
    assert item["raw_params"] == {}


def test_validate_handles_mixed_task_types(
    app: FastAPI, make_client: Any, prepared_db: AsyncEngine
) -> None:
    client = make_client(app)
    response = client.post(
        "/api/tasks/validate",
        json={
            "tasks": [
                {"name": "StartUp", "start_game_enabled": True},
                {"name": "Fight", "stage": "1-7"},
                {"name": "Recruit", "times": 4},
            ]
        },
    )
    assert response.status_code == 200, response.text
    items = response.json()["items"]
    assert [item["type_name"] for item in items] == ["StartUp", "Fight", "Recruit"]
    assert items[0]["params"]["client_type"] == "Bilibili"
    assert items[1]["params"]["server"] == "CN"
    # Recruit 没有 client_type 字段，只有 server
    assert "client_type" not in items[2]["params"]
    assert items[2]["params"]["server"] == "CN"


def test_validate_unknown_type_is_400(
    app: FastAPI, make_client: Any, prepared_db: AsyncEngine
) -> None:
    client = make_client(app)
    response = client.post("/api/tasks/validate", json={"tasks": [{"name": "Nope"}]})
    assert response.status_code == 400, response.text
    assert response.json()["error"]["code"] == "UNKNOWN_TASK_TYPE"


def test_validate_infrast_custom_mode_without_filename_is_422(
    app: FastAPI, make_client: Any, prepared_db: AsyncEngine
) -> None:
    client = make_client(app)
    response = client.post(
        "/api/tasks/validate", json={"tasks": [{"name": "Infrast", "mode": 10000}]}
    )
    assert response.status_code == 422, response.text
    error = response.json()["error"]
    assert error["code"] == "TASK_PARAM_INVALID"


def test_validate_deprecated_roguelike_mode_is_400(
    app: FastAPI, make_client: Any, prepared_db: AsyncEngine
) -> None:
    client = make_client(app)
    response = client.post(
        "/api/tasks/validate", json={"tasks": [{"name": "Roguelike", "mode": 2}]}
    )
    assert response.status_code == 400, response.text
    assert response.json()["error"]["code"] == "TASK_PARAM_DEPRECATED"


def test_validate_empty_tasks_is_422(
    app: FastAPI, make_client: Any, prepared_db: AsyncEngine
) -> None:
    client = make_client(app)
    response = client.post("/api/tasks/validate", json={"tasks": []})
    assert response.status_code == 422, response.text
    assert response.json()["error"]["code"] == "VALIDATION_ERROR"


def test_validate_rejects_pipeline_only_fields(
    app: FastAPI, make_client: Any, prepared_db: AsyncEngine
) -> None:
    """validate 的请求体不收 title / priority（不复用 PipelineCreate）。"""
    client = make_client(app)
    response = client.post(
        "/api/tasks/validate",
        json={"tasks": [{"name": "CloseDown"}], "title": "日常"},
    )
    assert response.status_code == 422, response.text
    assert response.json()["error"]["code"] == "VALIDATION_ERROR"


def test_validate_reads_channel_defaults_from_setting_table(
    isolated_db: AsyncEngine, tmp_settings: Settings, app: FastAPI, make_client: Any
) -> None:
    """setting 表写 channel.client_type=Official / channel.server=JP 后按它注入。"""
    _run_db_setup(
        isolated_db, {"channel.client_type": "Official", "channel.server": "JP"}
    )
    client = make_client(app)
    response = client.post("/api/tasks/validate", json={"tasks": [{"name": "Fight"}]})
    assert response.status_code == 200, response.text
    item = response.json()["items"][0]
    assert item["params"] == {"client_type": "Official", "server": "JP"}
    assert item["raw_params"] == {}


def test_validate_falls_back_when_setting_value_is_invalid(
    isolated_db: AsyncEngine, tmp_settings: Settings, app: FastAPI, make_client: Any
) -> None:
    """设置表里的脏值只回退该字段，不让端点 500。"""
    _run_db_setup(isolated_db, {"channel.client_type": "Nope"})
    client = make_client(app)
    response = client.post("/api/tasks/validate", json={"tasks": [{"name": "Fight"}]})
    assert response.status_code == 200, response.text
    item = response.json()["items"][0]
    assert item["params"]["client_type"] == "Bilibili"
    assert item["params"]["server"] == "CN"
