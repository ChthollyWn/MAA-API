"""模型档案、角色与成本 REST 契约（M67 规格 §4.3.1、§6）。

用例按生产装配的最小集搭 app：只装 ``model_profiles`` 路由、统一错误处理器与
``app.state.model_profile_service`` / ``model_router`` / ``cost_ledger``；档案直接写进
``isolated_db`` 的临时库，不碰真实 ``resource/maa_api.db``。探查用 ``FakeLLM`` 按
四次探查的请求顺序排好应答，不碰网络。
"""

from __future__ import annotations

import asyncio

from cryptography.fernet import Fernet
from fastapi import FastAPI
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker
from sqlmodel import SQLModel

import maa_api.db.models  # noqa: F401  # 注册全部表，create_all 才能建出 model_profile
from maa_api.agent.llm.cost import CostLedger
from maa_api.agent.llm.profiles import ModelProfileService
from maa_api.agent.llm.roles import ModelRouter
from maa_api.api.errors import register_exception_handlers
from maa_api.api.routers import model_profiles as model_profiles_router
from maa_api.domain.enums import ModelRole
from tests.fakes.fake_llm import FakeLLM, text_response, tool_call

MASTER_KEY = Fernet.generate_key().decode()
BASE_URL = "https://profile.test/v1"
API_KEY = "sk-router"


def _factory(isolated_db) -> async_sessionmaker[AsyncSession]:
    """建表并返回指向 ``isolated_db`` 的会话工厂；先回收种子连接的事件循环。"""

    async def create_tables() -> None:
        async with isolated_db.begin() as connection:
            await connection.run_sync(SQLModel.metadata.create_all)

    asyncio.run(create_tables())
    isolated_db.sync_engine.dispose()
    return async_sessionmaker(isolated_db, expire_on_commit=False, class_=AsyncSession)


def _service(
    factory: async_sessionmaker[AsyncSession], *, client: FakeLLM | None = None
) -> ModelProfileService:
    provider = (
        (lambda: (lambda _settings: client)) if client is not None else (lambda: None)
    )
    return ModelProfileService(
        factory,
        env={"MAA_LLM_ENCRYPTION_KEY": MASTER_KEY},
        client_factory_provider=provider,
    )


def _client(
    make_client,
    *,
    service: ModelProfileService | None = None,
    model_router: ModelRouter | None = None,
    ledger: CostLedger | None = None,
):
    """生产装配的最小集：路由 + 统一错误体 + 三个 ``app.state`` 服务。"""
    app = FastAPI()
    register_exception_handlers(app)
    app.include_router(model_profiles_router.router)
    if service is not None:
        app.state.model_profile_service = service
    if model_router is not None:
        app.state.model_router = model_router
    if ledger is not None:
        app.state.cost_ledger = ledger
    return make_client(app)


def _create(client, **overrides):
    payload = {
        "name": "worker-a",
        "base_url": BASE_URL,
        "api_key": API_KEY,
        "model": "worker-model",
    }
    payload.update(overrides)
    return client.post("/api/agent/model-profiles", json=payload)


def _error(response):
    return response.json()["error"]


def test_profile_crud_roundtrip(isolated_db, make_client, tmp_settings):
    factory = _factory(isolated_db)
    service = _service(factory)
    client = _client(make_client, service=service, model_router=ModelRouter(service))

    created = _create(client)
    assert created.status_code == 201, created.text
    assert created.headers["location"] == "/api/agent/model-profiles/worker-a"
    body = created.json()
    assert body["name"] == "worker-a"
    assert body["api_key"] == "***"
    assert body["readonly"] is False
    assert body["id"]
    assert body["created_at"].endswith("Z")
    assert body["updated_at"].endswith("Z")

    listed = client.get("/api/agent/model-profiles")
    assert listed.status_code == 200, listed.text
    items = listed.json()["items"]
    assert [item["name"] for item in items] == ["default", "worker-a"]
    assert items[0]["readonly"] is True
    assert items[0]["id"] is None
    assert items[0]["created_at"] is None

    fetched = client.get("/api/agent/model-profiles/worker-a")
    assert fetched.status_code == 200
    assert fetched.json()["model"] == "worker-model"

    updated = client.put(
        "/api/agent/model-profiles/worker-a", json={"model": "worker-model-v2"}
    )
    assert updated.status_code == 200, updated.text
    assert updated.json()["model"] == "worker-model-v2"
    assert updated.json()["id"] == body["id"]

    deleted = client.delete("/api/agent/model-profiles/worker-a")
    assert deleted.status_code == 204
    assert deleted.content == b""

    missing = client.get("/api/agent/model-profiles/worker-a")
    assert missing.status_code == 404
    assert _error(missing)["code"] == "MODEL_PROFILE_NOT_FOUND"


def test_default_profile_is_readonly_and_reserved(isolated_db, make_client, tmp_settings):
    factory = _factory(isolated_db)
    service = _service(factory)
    client = _client(make_client, service=service, model_router=ModelRouter(service))

    created = _create(client, name="default")
    assert created.status_code == 409
    assert _error(created)["code"] == "MODEL_PROFILE_EXISTS"

    updated = client.put(
        "/api/agent/model-profiles/default", json={"model": "other-model"}
    )
    assert updated.status_code == 409
    assert _error(updated)["code"] == "MODEL_PROFILE_READONLY"

    deleted = client.delete("/api/agent/model-profiles/default")
    assert deleted.status_code == 409
    assert _error(deleted)["code"] == "MODEL_PROFILE_READONLY"

    fetched = client.get("/api/agent/model-profiles/default")
    assert fetched.status_code == 200
    assert fetched.json()["name"] == "default"
    assert fetched.json()["readonly"] is True


def test_model_profile_wire_includes_offpeak_prices(
    isolated_db, make_client, tmp_settings
):
    """offpeak 规格 §4.3：default 带 6 个价格字段；CRUD wire 有 3 个空闲价。"""
    factory = _factory(isolated_db)
    service = _service(factory)
    client = _client(make_client, service=service, model_router=ModelRouter(service))

    listed = client.get("/api/agent/model-profiles")
    assert listed.status_code == 200, listed.text
    default = listed.json()["items"][0]
    assert default["name"] == "default"
    # 峰值价与空闲价都来自 llm.price_*（tmp_settings 用默认 0），0 表示未配置 → null。
    assert default["price_input"] is None
    assert default["price_cached_input"] is None
    assert default["price_output"] is None
    assert default["price_input_offpeak"] is None
    assert default["price_cached_input_offpeak"] is None
    assert default["price_output_offpeak"] is None

    created = _create(
        client,
        price_input_offpeak=1.0,
        price_cached_input_offpeak=0.02,
        price_output_offpeak=4.0,
    )
    assert created.status_code == 201, created.text
    body = created.json()
    assert body["price_input_offpeak"] == 1.0
    assert body["price_cached_input_offpeak"] == 0.02
    assert body["price_output_offpeak"] == 4.0

    fetched = client.get("/api/agent/model-profiles/worker-a")
    assert fetched.status_code == 200, fetched.text
    assert fetched.json()["price_input_offpeak"] == 1.0
    assert fetched.json()["price_cached_input_offpeak"] == 0.02
    assert fetched.json()["price_output_offpeak"] == 4.0

    # 局部更新不动其它两项；显式 null 清空（回退高峰价）。
    updated = client.put(
        "/api/agent/model-profiles/worker-a", json={"price_output_offpeak": 5.5}
    )
    assert updated.status_code == 200, updated.text
    assert updated.json()["price_output_offpeak"] == 5.5
    assert updated.json()["price_input_offpeak"] == 1.0
    assert updated.json()["price_cached_input_offpeak"] == 0.02

    cleared = client.put(
        "/api/agent/model-profiles/worker-a", json={"price_input_offpeak": None}
    )
    assert cleared.status_code == 200, cleared.text
    assert cleared.json()["price_input_offpeak"] is None
    assert cleared.json()["price_cached_input_offpeak"] == 0.02
    assert cleared.json()["price_output_offpeak"] == 5.5


def test_probe_writes_back_capabilities(isolated_db, make_client, tmp_settings):
    fake = FakeLLM(
        [
            [
                {
                    "choices": [
                        {
                            "index": 0,
                            "delta": {
                                "tool_calls": [
                                    tool_call("probe-1", "capability_probe")
                                ]
                            },
                            "finish_reason": "tool_calls",
                        }
                    ]
                }
            ],
            text_response("blue"),
            text_response('{"ok": true}'),
            text_response("OK"),
            text_response("OK"),
        ]
    )
    factory = _factory(isolated_db)
    service = _service(factory, client=fake)
    client = _client(make_client, service=service, model_router=ModelRouter(service))
    assert _create(client).status_code == 201

    response = client.post("/api/agent/model-profiles/worker-a/probe")
    assert response.status_code == 200, response.text
    result = response.json()
    assert result["supports_tools"] is True
    assert result["supports_vision"] is True
    assert result["supports_json_schema"] is True
    assert isinstance(result["latency_ms"], int)

    fetched = client.get("/api/agent/model-profiles/worker-a")
    assert fetched.status_code == 200
    assert fetched.json()["supports_tools"] is True
    assert fetched.json()["supports_vision"] is True
    assert fetched.json()["supports_json_schema"] is True
    assert len(fake.requests) == 5


def test_embedding_profile_probe_is_rejected(isolated_db, make_client, tmp_settings):
    factory = _factory(isolated_db)
    service = _service(factory)
    client = _client(make_client, service=service, model_router=ModelRouter(service))
    created = _create(
        client,
        name="embed-a",
        kind="embedding",
        provider="local_onnx",
        base_url=None,
        api_key=None,
        model="bge-m3",
        embedding_dim=1024,
        local_model_path="/models/bge-m3",
    )
    assert created.status_code == 201, created.text

    response = client.post("/api/agent/model-profiles/embed-a/probe")
    assert response.status_code == 400
    assert _error(response)["code"] == "INVALID_PARAMETER"


def test_model_roles_list_every_role_in_enum_order(
    isolated_db, make_client, tmp_settings
):
    factory = _factory(isolated_db)
    service = _service(factory)
    client = _client(make_client, service=service, model_router=ModelRouter(service))

    response = client.get("/api/agent/model-roles")
    assert response.status_code == 200, response.text
    items = response.json()["items"]
    assert [item["role"] for item in items] == [str(role) for role in ModelRole]
    assert len(items) == 9
    assert all(item["source"] == "default" for item in items)
    assert items[0] == {
        "role": "orchestrator",
        "profile": "default",
        "model": "deepseek-flash",
        "source": "default",
    }
    assert items[-1] == {
        "role": "embedding",
        "profile": None,
        "model": None,
        "source": "default",
    }


def test_costs_rejects_inverted_or_too_long_ranges(
    isolated_db, make_client, tmp_settings
):
    factory = _factory(isolated_db)
    ledger = CostLedger(factory)
    client = _client(make_client, service=_service(factory), ledger=ledger)

    inverted = client.get(
        "/api/agent/costs",
        params={
            "since": "2026-01-02T00:00:00",
            "until": "2026-01-01T00:00:00",
        },
    )
    assert inverted.status_code == 400, inverted.text
    assert _error(inverted)["code"] == "INVALID_PARAMETER"

    too_long = client.get(
        "/api/agent/costs",
        params={
            "since": "2024-01-01T00:00:00",
            "until": "2025-02-01T00:00:00",
        },
    )
    assert too_long.status_code == 400, too_long.text
    assert _error(too_long)["code"] == "INVALID_PARAMETER"

    empty = client.get("/api/agent/costs")
    assert empty.status_code == 200, empty.text
    assert empty.json() == {"currency": "CNY", "total_micros": 0, "groups": []}
