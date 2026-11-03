"""M67 T4：角色路由（规格 §4.2.4、§6）。

与 ``test_profiles.py`` 一样：同步测试 + ``asyncio.run`` + 自建内存库。注入的
client factory 只记录收到的 ``Settings``，不发送任何请求。
"""

from __future__ import annotations

import asyncio
from collections.abc import Callable
from typing import Any

import pytest
from cryptography.fernet import Fernet
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker
from sqlalchemy.pool import StaticPool
from sqlmodel import SQLModel

import maa_api.db.models  # noqa: F401  # 把表注册进 SQLModel.metadata
from maa_api.agent.llm.profiles import (
    DEFAULT_PROFILE_NAME,
    ModelProfileCreate,
    ModelProfileService,
    ProbeResult,
)
from maa_api.agent.llm.roles import ROLE_SETTING_PREFIX, ClientHandle, ModelRouter
from maa_api.agent.specs.registry import builtin_spec
from maa_api.db import session as db_session
from maa_api.domain.errors import AppError, ErrorCode
from maa_api.settings import LLMSettings, Settings

MASTER_KEY = Fernet.generate_key().decode()
PROFILE_BASE_URL = "https://profile.test/v1"
PROFILE_API_KEY = "sk-profile"
LEGACY_SPEC_NAME = "legacy_chat"


async def _make_factory() -> tuple[async_sessionmaker[AsyncSession], Any]:
    engine = db_session.make_engine(
        "sqlite+aiosqlite:///:memory:", poolclass=StaticPool
    )
    async with engine.begin() as connection:
        await connection.run_sync(SQLModel.metadata.create_all)
    factory = async_sessionmaker(engine, expire_on_commit=False, class_=AsyncSession)
    return factory, engine


def _settings(**llm_overrides: Any) -> Settings:
    values: dict[str, Any] = {
        "base_url": PROFILE_BASE_URL,
        "api_key": PROFILE_API_KEY,
        "model": "test-model",
    }
    values.update(llm_overrides)
    return Settings(llm=LLMSettings(**values))


def _service(
    factory: async_sessionmaker[AsyncSession],
    *,
    settings: Settings | None = None,
    client_factory_provider: Callable[[], Callable[[Settings], Any] | None] = lambda: None,
) -> ModelProfileService:
    return ModelProfileService(
        factory,
        env={"MAA_LLM_ENCRYPTION_KEY": MASTER_KEY},
        settings_provider=lambda: settings if settings is not None else _settings(),
        client_factory_provider=client_factory_provider,
    )


def _seed_default_probe(service: ModelProfileService, settings: Settings) -> None:
    """预置 default 的探查结果，让 ``ensure_probed`` 不再发请求。"""
    service._default_probe[(settings.llm.base_url, settings.llm.model)] = ProbeResult(
        supports_tools=True,
        supports_vision=False,
        supports_json_schema=True,
        dynamic_context_position="tail_system",
        latency_ms=1,
        failed=False,
    )


def _payload(name: str, **overrides: Any) -> ModelProfileCreate:
    values: dict[str, Any] = {
        "name": name,
        "base_url": PROFILE_BASE_URL,
        "api_key": "sk-a",
        "model": f"{name}-model",
    }
    values.update(overrides)
    return ModelProfileCreate(**values)


def test_empty_role_setting_falls_back_to_default() -> None:
    async def scenario() -> None:
        factory, engine = await _make_factory()
        try:
            settings = _settings()
            service = _service(factory, settings=settings)
            _seed_default_probe(service, settings)
            router = ModelRouter(service, settings_provider=lambda: settings)

            assert settings.llm.role_orchestrator == ""
            profile = await router.resolve("orchestrator")
            assert profile.name == DEFAULT_PROFILE_NAME
            assert profile.readonly is True
            assert profile.model == settings.llm.model
            assert profile.supports_vision is False

            bindings = await router.list_roles()
            assert [binding.role for binding in bindings] == [
                "orchestrator",
                "worker",
                "subagent",
                "summarizer",
                "title",
                "trigger_eval",
                "memory",
                "vision",
                "embedding",
            ]
            assert all(binding.source == "default" for binding in bindings)
            # embedding 空设置是「没有远端向量」，不是回落到 default。
            assert bindings[-1].profile is None
            assert bindings[-1].model is None
        finally:
            await engine.dispose()

    asyncio.run(scenario())


def test_role_kind_mismatch_is_rejected() -> None:
    async def scenario() -> None:
        factory, engine = await _make_factory()
        try:
            settings = _settings(role_worker="vectors")
            service = _service(factory, settings=settings)
            await service.create(
                _payload(
                    "vectors",
                    kind="embedding",
                    model="embedding-model",
                    embedding_dim=1024,
                )
            )
            router = ModelRouter(service, settings_provider=lambda: settings)

            with pytest.raises(AppError) as excinfo:
                await router.resolve("worker")
            assert excinfo.value.code == ErrorCode.MODEL_PROFILE_NOT_FOUND
            assert excinfo.value.details == {"reason": "kind_mismatch"}

            # embedding 角色指向 chat 档案同样拒绝。
            await service.create(_payload("chatty"))
            settings.llm.role_embedding = "chatty"
            with pytest.raises(AppError) as excinfo:
                await router.resolve("embedding")
            assert excinfo.value.code == ErrorCode.MODEL_PROFILE_NOT_FOUND
            assert excinfo.value.details == {"reason": "kind_mismatch"}
        finally:
            await engine.dispose()

    asyncio.run(scenario())


def test_legacy_specs_use_the_session_snapshot() -> None:
    async def scenario() -> None:
        factory, engine = await _make_factory()
        try:
            settings = _settings()
            service = _service(factory, settings=settings)
            router = ModelRouter(service, settings_provider=lambda: settings)
            spec = builtin_spec(LEGACY_SPEC_NAME)

            profile = await router.resolve_for_spec(
                spec,
                session_model="deepseek-v4-pro",
                session_base_url="https://session.test/v1",
            )
            assert profile.name == DEFAULT_PROFILE_NAME
            assert profile.model == "deepseek-v4-pro"
            assert profile.base_url == "https://session.test/v1"

            # 快照为空或 model 是 unconfigured：保持 llm.* 的默认值。
            for model in ("", " unconfigured "):
                unset = await router.resolve_for_spec(
                    spec,
                    session_model=model,
                    session_base_url="https://session.test/v1",
                )
                assert unset.model == settings.llm.model
                assert unset.base_url == settings.llm.base_url
        finally:
            await engine.dispose()

    asyncio.run(scenario())


def test_injected_client_factory_receives_profile_settings() -> None:
    async def scenario() -> None:
        factory, engine = await _make_factory()
        try:
            settings = _settings()
            captured: list[Settings] = []
            fake_client = object()

            def client_factory(overrides: Settings) -> Any:
                captured.append(overrides)
                return fake_client

            service = _service(
                factory,
                settings=settings,
                client_factory_provider=lambda: client_factory,
            )
            await service.create(
                _payload(
                    "vision-a",
                    base_url="https://vision.test/v1",
                    api_key="sk-vision",
                    model="vision-model",
                )
            )
            profile = await service.resolve("vision-a")
            router = ModelRouter(
                service,
                settings_provider=lambda: settings,
                client_factory_provider=lambda: client_factory,
            )

            handle = router.client_for(profile)
            assert isinstance(handle, ClientHandle)
            assert handle.owned is False
            assert handle.client is fake_client
            assert len(captured) == 1
            received = captured[0].llm
            assert received.base_url == profile.base_url == "https://vision.test/v1"
            assert received.api_key == profile.api_key == "sk-vision"
            assert received.model == profile.model == "vision-model"

            # 注入工厂时不需要关闭；没有工厂时新建的客户端由调用方持有。
            await handle.aclose()
            plain_router = ModelRouter(service, settings_provider=lambda: settings)
            owned = plain_router.client_for(profile, timeout_seconds=5.0)
            assert owned.owned is True
            assert owned.client.base_url == "https://vision.test/v1"
            assert owned.client.timeout_seconds == 5.0
            await owned.aclose()
        finally:
            await engine.dispose()

    asyncio.run(scenario())


def test_role_setting_prefix_is_the_documented_key_prefix() -> None:
    """角色设置 key 的前缀是 ``llm.role_``；``ModelRole`` 都能拼出真实字段。"""
    settings = _settings(role_worker="worker-a")
    assert ROLE_SETTING_PREFIX == "llm.role_"
    assert settings.llm.role_worker == "worker-a"
