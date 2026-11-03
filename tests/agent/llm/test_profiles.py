"""M67 T4：模型档案服务（规格 §4.2.3、§6）。

用例形态与 ``tests/db`` 一致：同步测试函数 + ``asyncio.run(scenario())``（本仓没有
pytest-asyncio），每个场景自建内存库。能力探查用假 client 与假 estimator，不碰网络。
"""

from __future__ import annotations

import asyncio
from collections.abc import Iterable
from typing import Any

import pytest
from cryptography.fernet import Fernet
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker
from sqlalchemy.pool import StaticPool
from sqlmodel import SQLModel

import maa_api.db.models  # noqa: F401  # 把表注册进 SQLModel.metadata
from maa_api.agent.llm import profiles
from maa_api.agent.llm.profiles import (
    DEFAULT_PROFILE_NAME,
    PROBE_FAILURE_TTL_SECONDS,
    TAIL_PROBE_FILLER,
    ModelProfileCreate,
    ModelProfileService,
    ModelProfileUpdate,
    ProbeResult,
    probe_tail_position,
)
from maa_api.agent.secrets import decrypt_api_key
from maa_api.db import session as db_session
from maa_api.db.repositories.model_profile import ModelProfileRepository
from maa_api.domain.errors import AppError, ErrorCode
from maa_api.settings import LLMSettings, Settings

MASTER_KEY = Fernet.generate_key().decode()
PROFILE_BASE_URL = "https://profile.test/v1"
PROFILE_API_KEY = "sk-profile"


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
    env: dict[str, str] | None = None,
) -> ModelProfileService:
    return ModelProfileService(
        factory,
        env={"MAA_LLM_ENCRYPTION_KEY": MASTER_KEY} if env is None else env,
        settings_provider=lambda: settings if settings is not None else _settings(),
        client_factory_provider=lambda: (lambda _settings: object()),
    )


def _create_payload(name: str = "worker-a", **overrides: Any) -> ModelProfileCreate:
    values: dict[str, Any] = {
        "name": name,
        "base_url": PROFILE_BASE_URL,
        "api_key": "sk-a",
        "model": "worker-model",
    }
    values.update(overrides)
    return ModelProfileCreate(**values)


# ---------------------------------------------------------------------------
# default 合成与加密
# ---------------------------------------------------------------------------


def test_default_profile_is_synthesized_and_readonly() -> None:
    async def scenario() -> None:
        factory, engine = await _make_factory()
        try:
            service = _service(factory)
            views = await service.list_views()

            first = views[0]
            assert first.name == DEFAULT_PROFILE_NAME
            assert first.readonly is True
            assert first.api_key == "***"
            assert first.id is None
            assert first.created_at is None
            assert first.updated_at is None
            # default 不在表里：列表里只有这一项。
            assert len(views) == 1
        finally:
            await engine.dispose()

    asyncio.run(scenario())


def test_api_key_uses_the_settings_master_key() -> None:
    async def scenario() -> None:
        factory, engine = await _make_factory()
        try:
            service = _service(factory)
            await service.create(_create_payload(name="worker-a"))

            async with factory() as session:
                row = await ModelProfileRepository(session).get_by_name("worker-a")
                assert row is not None
                stored = row.api_key_encrypted
            assert stored is not None and stored.startswith("enc:v1:")
            assert decrypt_api_key(stored, MASTER_KEY) == "sk-a"

            # env 里没有主密钥时，带密钥的新建直接 503。
            no_key = ModelProfileService(
                factory,
                env={},
                settings_provider=lambda: _settings(),
                client_factory_provider=lambda: (lambda _settings: object()),
            )
            with pytest.raises(AppError) as excinfo:
                await no_key.create(_create_payload(name="worker-b", api_key="sk-b"))
            assert excinfo.value.code == ErrorCode.LLM_ENCRYPTION_KEY_INVALID
            assert excinfo.value.http_status == 503
            assert "MAA_LLM_ENCRYPTION_KEY" in (excinfo.value.message or "")
        finally:
            await engine.dispose()

    asyncio.run(scenario())


def test_update_masked_key_keeps_ciphertext_and_url_change_resets_probe() -> None:
    async def scenario() -> None:
        factory, engine = await _make_factory()
        try:
            service = _service(factory)
            await service.create(_create_payload(name="worker-a"))

            async with factory() as session:
                row = await ModelProfileRepository(session).get_by_name("worker-a")
                assert row is not None
                ciphertext = row.api_key_encrypted
                row.supports_vision = True
                row.supports_json_schema = True
                await session.commit()

            updated = await service.update(
                "worker-a", ModelProfileUpdate(api_key="***")
            )
            assert updated.api_key == "***"
            async with factory() as session:
                row = await ModelProfileRepository(session).get_by_name("worker-a")
                assert row is not None
                assert row.api_key_encrypted == ciphertext
                assert row.supports_vision is True
                assert row.supports_json_schema is True

            await service.update(
                "worker-a", ModelProfileUpdate(base_url="https://other.test/v1")
            )
            async with factory() as session:
                row = await ModelProfileRepository(session).get_by_name("worker-a")
                assert row is not None
                assert row.base_url == "https://other.test/v1"
                assert row.supports_vision is None
                assert row.supports_json_schema is None
        finally:
            await engine.dispose()

    asyncio.run(scenario())


def test_profile_in_use_cannot_be_deleted_or_disabled() -> None:
    holder = {"settings": _settings()}

    async def scenario() -> None:
        factory, engine = await _make_factory()
        try:
            service = ModelProfileService(
                factory,
                env={"MAA_LLM_ENCRYPTION_KEY": MASTER_KEY},
                settings_provider=lambda: holder["settings"],
                client_factory_provider=lambda: (lambda _settings: object()),
            )
            await service.create(_create_payload(name="worker-a"))
            holder["settings"] = _settings(role_worker="worker-a")

            with pytest.raises(AppError) as excinfo:
                await service.delete("worker-a")
            assert excinfo.value.code == ErrorCode.MODEL_PROFILE_IN_USE
            assert excinfo.value.details == {"roles": ["worker"]}

            with pytest.raises(AppError) as excinfo:
                await service.update(
                    "worker-a", ModelProfileUpdate(enabled=False)
                )
            assert excinfo.value.code == ErrorCode.MODEL_PROFILE_IN_USE
            assert excinfo.value.details == {"roles": ["worker"]}

            # 两条路都失败后档案仍在、仍然启用。
            view = await service.get_view("worker-a")
            assert view.enabled is True
        finally:
            await engine.dispose()

    asyncio.run(scenario())


# ---------------------------------------------------------------------------
# 尾部位置探查（docs/v3/06 §2.4）
# ---------------------------------------------------------------------------


def _text_chunks(text: str = "OK", usage: dict[str, Any] | None = None):
    chunks: list[dict[str, Any]] = [
        {
            "choices": [
                {"index": 0, "delta": {"content": text}, "finish_reason": None}
            ]
        },
        {"choices": [{"index": 0, "delta": {}, "finish_reason": "stop"}]},
    ]
    if usage is not None:
        chunks[-1]["usage"] = usage
    return chunks


class _FakeProbeClient:
    def __init__(self, responses: Iterable[list[dict[str, Any]] | AppError]) -> None:
        self.responses = list(responses)
        self.requests: list[tuple[str, list[dict[str, Any]], list[dict[str, Any]], dict[str, Any]]] = []

    async def stream_chat_completion(
        self, model, messages, tools, **options
    ):
        self.requests.append((model, list(messages), list(tools), dict(options)))
        response = self.responses.pop(0)
        if isinstance(response, AppError):
            raise response
        for chunk in response:
            yield chunk


class _FakeEstimator:
    def __init__(self, estimate: int = 1000) -> None:
        self.estimate = estimate

    def raw_estimate(self, messages, tools=()) -> int:
        return self.estimate


@pytest.mark.parametrize(
    ("responses", "expected"),
    [
        pytest.param(
            [_text_chunks(), _text_chunks(usage={"prompt_cache_hit_tokens": 0})],
            "tail_user",
            id="second-request-no-cache-hit",
        ),
        pytest.param(
            [_text_chunks(), _text_chunks(usage={"prompt_cache_hit_tokens": 800})],
            "tail_system",
            id="cache-hit-at-least-half-prefix",
        ),
        pytest.param(
            [
                AppError(
                    ErrorCode.LLM_REQUEST_FAILED,
                    "LLM 上游请求失败",
                    {"upstream_status": 400},
                )
            ],
            "tail_user",
            id="first-request-rejected",
        ),
    ],
)
def test_tail_probe_decisions(
    monkeypatch: pytest.MonkeyPatch,
    responses: list[list[dict[str, Any]] | AppError],
    expected: str,
) -> None:
    sleeps: list[float] = []

    async def fake_sleep(seconds: float) -> None:
        sleeps.append(seconds)

    monkeypatch.setattr(profiles.asyncio, "sleep", fake_sleep)
    client = _FakeProbeClient(responses)

    async def scenario() -> None:
        position, error = await probe_tail_position(
            client, "test-model", _FakeEstimator(estimate=1000)
        )
        assert error is None
        assert position == expected

    asyncio.run(scenario())

    assert client.requests[0][1][-1]["content"] == "probe tail A"
    assert client.requests[0][1][1] == {"role": "user", "content": TAIL_PROBE_FILLER}
    assert client.requests[0][3]["max_tokens"] == 8
    if expected == "tail_user" and len(client.requests) == 1:
        # 第一次就被拒：不再发第二次，也不 sleep。
        assert sleeps == []
    else:
        assert sleeps == [float(profiles.TAIL_PROBE_SLEEP_SECONDS)]
        assert client.requests[1][1][-1]["content"] == "probe tail B"
        assert client.requests[1][3]["max_tokens"] == 8


# ---------------------------------------------------------------------------
# 自动探查：并发去重与失败 TTL
# ---------------------------------------------------------------------------


def test_ensure_probed_runs_once_and_respects_the_failure_ttl(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    calls = {"tool": 0, "vision": 0}
    state: dict[str, Any] = {"vision_error": None}

    async def fake_tool(client, model):
        calls["tool"] += 1
        await asyncio.sleep(0)  # 让并发的第二个调用有机会进入等待
        return True, None

    async def fake_vision(client, model):
        calls["vision"] += 1
        return False, state["vision_error"]

    async def fake_json_schema(client, model):
        return True, None

    async def fake_tail(client, model, estimator):
        return "tail_system", None

    monkeypatch.setattr(profiles, "probe_tool_calling", fake_tool)
    monkeypatch.setattr(profiles, "probe_vision", fake_vision)
    monkeypatch.setattr(profiles, "probe_json_schema", fake_json_schema)
    monkeypatch.setattr(profiles, "probe_tail_position", fake_tail)
    monkeypatch.setattr(profiles, "_token_estimator", lambda: _FakeEstimator())

    clock = {"now": 100.0}
    monkeypatch.setattr(profiles, "_monotonic", lambda: clock["now"])

    async def scenario() -> None:
        factory, engine = await _make_factory()
        try:
            service = _service(factory)
            await service.create(_create_payload(name="worker-a"))
            first = await service.resolve("worker-a")
            second = await service.resolve("worker-a")
            assert first.supports_vision is None

            # 并发两次自动探查只发一组请求。
            probed = await asyncio.gather(
                service.ensure_probed(first),
                service.ensure_probed(second),
            )
            assert [profile.supports_vision for profile in probed] == [False, False]
            assert calls == {"tool": 1, "vision": 1}

            # 失败探查（视觉请求失败）只判定不了这一项，failed=True 记冷却。
            await service.create(_create_payload(name="worker-b"))
            failing = await service.resolve("worker-b")
            calls.update(tool=0, vision=0)
            state["vision_error"] = AppError(
                ErrorCode.LLM_TIMEOUT, "LLM 上游请求超时"
            )

            result = await service.ensure_probed(failing)
            assert result.supports_vision is None
            assert calls == {"tool": 1, "vision": 1}

            # TTL 内不再自动探查。
            again = await service.ensure_probed(failing)
            assert again.supports_vision is None
            assert calls == {"tool": 1, "vision": 1}

            # 过了 60 秒才允许重探。
            clock["now"] += PROBE_FAILURE_TTL_SECONDS + 1
            await service.ensure_probed(failing)
            assert calls == {"tool": 2, "vision": 2}
        finally:
            await engine.dispose()

    asyncio.run(scenario())


def test_probe_writes_capabilities_back_and_uses_tool_probe_latency(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """显式探查：四项都跑、None 项不覆盖、latency 取工具探查的耗时。"""

    async def fake_tool(client, model):
        return True, None

    async def fake_vision(client, model):
        return True, None

    async def fake_json_schema(client, model):
        return False, None

    async def fake_tail(client, model, estimator):
        return "tail_user", None

    monkeypatch.setattr(profiles, "probe_tool_calling", fake_tool)
    monkeypatch.setattr(profiles, "probe_vision", fake_vision)
    monkeypatch.setattr(profiles, "probe_json_schema", fake_json_schema)
    monkeypatch.setattr(profiles, "probe_tail_position", fake_tail)
    monkeypatch.setattr(profiles, "_token_estimator", lambda: _FakeEstimator())

    async def scenario() -> None:
        factory, engine = await _make_factory()
        try:
            service = _service(factory)
            await service.create(_create_payload(name="worker-a"))
            result = await service.probe("worker-a")
            assert isinstance(result, ProbeResult)
            assert result.supports_tools is True
            assert result.supports_vision is True
            assert result.supports_json_schema is False
            assert result.dynamic_context_position == "tail_user"
            assert result.failed is False
            assert result.latency_ms >= 0

            view = await service.get_view("worker-a")
            assert view.supports_vision is True
            assert view.supports_json_schema is False
            assert view.dynamic_context_position == "tail_user"

            # 向量档案不能探查。
            await service.create(
                _create_payload(
                    name="embedding-a",
                    kind="embedding",
                    model="embedding-model",
                    embedding_dim=1024,
                )
            )
            with pytest.raises(AppError) as excinfo:
                await service.probe("embedding-a")
            assert excinfo.value.code == ErrorCode.INVALID_PARAMETER
        finally:
            await engine.dispose()

    asyncio.run(scenario())

