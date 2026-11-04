"""M76 T4：EmbeddingProvider 三个实现与 EmbeddingResolver（spec §4.2、§6）。

本机不装 onnxruntime/tokenizers：本地路径用注入的假 session 与假分词器；
「没装依赖」用 ``monkeypatch.setitem(sys.modules, "onnxruntime", None)`` 制造
ImportError；远端全部走 ``httpx.MockTransport``，不发真实请求。
"""

from __future__ import annotations

import asyncio
import json
import sys
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import httpx
import numpy as np
import pytest

from sqlalchemy import select

from maa_api.agent.knowledge.index.embeddings import (
    EmbeddingResolver,
    LocalOnnxEmbeddingProvider,
    NullEmbeddingProvider,
    RemoteEmbeddingProvider,
)
from maa_api.db.models import ModelProfile
from maa_api.domain.errors import AppError, ErrorCode
from maa_api.settings import REPO_ROOT

API_KEY = "sk-top-secret"


def _settings(mode: str) -> Any:
    return lambda: SimpleNamespace(rag=SimpleNamespace(embedding_provider=mode))


class _Router:
    """假 ModelRouter：按需返回一个档案或抛 ``MODEL_PROFILE_NOT_FOUND``。"""

    def __init__(self, *, profile: Any = None, error: AppError | None = None) -> None:
        self._profile = profile
        self._error = error

    async def resolve(self, role: str) -> Any:
        assert role == "embedding"
        if self._error is not None:
            raise self._error
        return self._profile


def _missing_profile_error() -> AppError:
    return AppError(ErrorCode.MODEL_PROFILE_NOT_FOUND, "没有配置向量模型档案")


def _remote_profile(**overrides: Any) -> SimpleNamespace:
    values: dict[str, Any] = {
        "name": "emb",
        "kind": "embedding",
        "provider": "openai_compatible",
        "base_url": "https://llm.example/v1",
        "api_key": API_KEY,
        "model": "bge-small-zh-v1.5",
        "embedding_dim": 8,
        "local_model_path": None,
    }
    values.update(overrides)
    return SimpleNamespace(**values)


def test_remote_posts_batches_and_normalizes() -> None:
    requests: list[httpx.Request] = []
    usages: list[int] = []

    def handler(request: httpx.Request) -> httpx.Response:
        requests.append(request)
        body = json.loads(request.content)
        count = len(body["input"])
        data = [
            {"index": index, "embedding": [float(index + 1), 1.0, 0.0]}
            for index in range(count)
        ][::-1]
        return httpx.Response(
            200, json={"data": data, "usage": {"prompt_tokens": 10 * count}}
        )

    async def on_usage(tokens: int) -> None:
        usages.append(tokens)

    provider = RemoteEmbeddingProvider(
        base_url="https://llm.example/v1",
        api_key=API_KEY,
        model="bge",
        dim=3,
        on_usage=on_usage,
        transport=httpx.MockTransport(handler),
        batch_size=16,
    )
    texts = [f"文本 {index}" for index in range(20)]
    matrix = asyncio.run(provider.embed(texts, purpose="document"))

    assert matrix.shape == (20, 3)
    assert provider.dim == 3
    assert np.allclose(np.linalg.norm(matrix, axis=1), 1.0, atol=1e-6)
    # 每批 16 条；按 data[].index 排序后拼接（同批内第一列递增）。
    assert [len(json.loads(request.content)["input"]) for request in requests] == [16, 4]
    assert matrix[0, 0] < matrix[1, 0] < matrix[3, 0]
    assert usages == [160, 40]
    assert str(requests[0].url) == "https://llm.example/v1/embeddings"
    assert requests[0].headers["authorization"] == f"Bearer {API_KEY}"
    body = json.loads(requests[0].content)
    assert body["model"] == "bge"
    assert body["encoding_format"] == "float"


def test_remote_maps_errors_without_leaking_key() -> None:
    calls_401: list[httpx.Request] = []
    calls_500: list[httpx.Request] = []
    calls_timeout: list[httpx.Request] = []

    def handler_401(request: httpx.Request) -> httpx.Response:
        calls_401.append(request)
        return httpx.Response(
            401, json={"error": {"message": f"invalid key {API_KEY}"}}
        )

    def handler_500(request: httpx.Request) -> httpx.Response:
        calls_500.append(request)
        return httpx.Response(
            500, json={"error": {"message": f"upstream exploded with {API_KEY}"}}
        )

    def handler_timeout(request: httpx.Request) -> httpx.Response:
        calls_timeout.append(request)
        raise httpx.ReadTimeout("read timed out")

    cases = [
        ("401", handler_401, calls_401, 1),
        ("500", handler_500, calls_500, 2),
        ("timeout", handler_timeout, calls_timeout, 2),
    ]
    expected_messages = {
        "401": "拒绝",
        "500": "HTTP 500",
        "timeout": "超时",
    }
    for label, handler, calls, expected_calls in cases:
        provider = RemoteEmbeddingProvider(
            base_url="https://llm.example/v1",
            api_key=API_KEY,
            model="bge",
            dim=3,
            transport=httpx.MockTransport(handler),
        )
        with pytest.raises(AppError) as exc_info:
            asyncio.run(provider.embed(["问题"], purpose="query"))
        error = exc_info.value
        assert error.code == ErrorCode.EMBEDDING_UNAVAILABLE, label
        assert len(calls) == expected_calls, label
        assert expected_messages[label] in (error.message or ""), label
        assert API_KEY not in (error.message or ""), label
        assert API_KEY not in json.dumps(error.details or {}, ensure_ascii=False), label


def test_remote_url_appends_embeddings_once() -> None:
    def build(base_url: str) -> RemoteEmbeddingProvider:
        return RemoteEmbeddingProvider(
            base_url=base_url, api_key=API_KEY, model="m", dim=2
        )

    assert build("https://host/v1").url == "https://host/v1/embeddings"
    assert build("https://host/v1/").url == "https://host/v1/embeddings"
    assert build("https://host/v1/embeddings").url == "https://host/v1/embeddings"

    urls: list[str] = []

    def handler(request: httpx.Request) -> httpx.Response:
        urls.append(str(request.url))
        return httpx.Response(200, json={"data": [{"index": 0, "embedding": [1.0, 0.0]}]})

    provider = RemoteEmbeddingProvider(
        base_url="https://host/v1",
        api_key=API_KEY,
        model="m",
        dim=2,
        transport=httpx.MockTransport(handler),
    )
    asyncio.run(provider.embed(["a"], purpose="document"))
    assert urls == ["https://host/v1/embeddings"]


def _model_dir(tmp_path: Path, *, files: tuple[str, ...] = ("model.onnx", "tokenizer.json")) -> Path:
    directory = tmp_path / "model"
    directory.mkdir()
    for name in files:
        (directory / name).write_text("{}", encoding="utf-8")
    return directory


def test_local_missing_dependency_or_files_is_unavailable(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    def noop_session(path: Path, threads: int) -> Any:
        raise AssertionError("缺文件时不应加载模型")

    def noop_tokenizer(path: Path) -> Any:
        raise AssertionError("缺文件时不应加载模型")

    # 没装 onnxruntime：import 失败。
    directory = _model_dir(tmp_path)
    provider = LocalOnnxEmbeddingProvider(directory, model="m", dim=4)
    monkeypatch.setitem(sys.modules, "onnxruntime", None)
    with pytest.raises(AppError) as exc_info:
        asyncio.run(provider.embed(["a"], purpose="document"))
    assert exc_info.value.code == ErrorCode.EMBEDDING_UNAVAILABLE
    assert "embed-local" in (exc_info.value.message or "")

    # 缺 tokenizer.json：消息写出文件名。
    (directory / "tokenizer.json").unlink()
    provider = LocalOnnxEmbeddingProvider(
        directory,
        model="m",
        dim=4,
        session_loader=noop_session,
        tokenizer_loader=noop_tokenizer,
    )
    with pytest.raises(AppError) as exc_info:
        asyncio.run(provider.embed(["a"], purpose="document"))
    assert "tokenizer.json" in (exc_info.value.message or "")

    # 缺 model.onnx 同理。
    (directory / "model.onnx").unlink()
    with pytest.raises(AppError) as exc_info:
        asyncio.run(provider.embed(["a"], purpose="document"))
    assert "model.onnx" in (exc_info.value.message or "")


class _Encoding:
    def __init__(self, ids: list[int]) -> None:
        self.ids = ids
        self.attention_mask = [1] * len(ids)
        self.type_ids = [0] * len(ids)


class _FakeTokenizer:
    def __init__(self) -> None:
        self.batches: list[list[str]] = []
        self.max_length: int | None = None
        self.padding = False

    def enable_truncation(self, max_length: int) -> None:
        self.max_length = max_length

    def enable_padding(self, **_: Any) -> None:
        self.padding = True

    def encode_batch(self, texts: list[str]) -> list[_Encoding]:
        self.batches.append(list(texts))
        return [_Encoding([101, 102]) for _ in texts]


class _FakeSession:
    def __init__(self, hidden: int, cls_vector: list[float]) -> None:
        self.hidden = hidden
        self.cls_vector = cls_vector

    def get_inputs(self) -> list[Any]:
        return [
            SimpleNamespace(name="input_ids"),
            SimpleNamespace(name="attention_mask"),
        ]

    def run(self, output_names: Any, feeds: dict[str, Any]) -> list[np.ndarray]:
        batch, sequence = feeds["input_ids"].shape
        output = np.zeros((batch, sequence, self.hidden), dtype=np.float32)
        output[:, 0, :] = self.cls_vector
        return [output]


def test_local_cls_pooling_and_query_prefix(tmp_path: Path) -> None:
    directory = _model_dir(tmp_path)
    (directory / "maa_embedding.json").write_text(
        json.dumps({"query_prefix": "查询：", "max_length": 7}), encoding="utf-8"
    )
    tokenizer = _FakeTokenizer()
    session = _FakeSession(hidden=4, cls_vector=[3.0, 4.0, 0.0, 0.0])
    loaded_threads: list[int] = []

    def session_loader(path: Path, threads: int) -> Any:
        assert path == directory
        loaded_threads.append(threads)
        return session

    provider = LocalOnnxEmbeddingProvider(
        directory,
        model="bge",
        dim=4,
        session_loader=session_loader,
        tokenizer_loader=lambda path: tokenizer,
        threads=3,
    )

    async def scenario() -> tuple[np.ndarray, np.ndarray]:
        document = await provider.embed(["文档内容"], purpose="document")
        query = await provider.embed(["问题"], purpose="query")
        return document, query

    document, query = asyncio.run(scenario())

    assert provider.name == "local_onnx:bge"
    assert provider.dim == 4
    assert tokenizer.batches == [["文档内容"], ["查询：问题"]]
    assert tokenizer.max_length == 7
    assert tokenizer.padding is True
    assert loaded_threads == [3]
    # CLS 池化后 L2 归一化：[3, 4, 0, 0] → [0.6, 0.8, 0, 0]。
    assert np.allclose(document[0], [0.6, 0.8, 0.0, 0.0], atol=1e-6)
    assert np.isclose(float(np.linalg.norm(query[0])), 1.0, atol=1e-6)


def test_dimension_mismatch_is_unavailable(tmp_path: Path) -> None:
    # 本地：档案 512，模型输出 384。
    directory = _model_dir(tmp_path)
    provider = LocalOnnxEmbeddingProvider(
        directory,
        model="bge",
        dim=512,
        session_loader=lambda path, threads: _FakeSession(
            hidden=384, cls_vector=[1.0] * 384
        ),
        tokenizer_loader=lambda path: _FakeTokenizer(),
    )
    with pytest.raises(AppError) as exc_info:
        asyncio.run(provider.embed(["a"], purpose="document"))
    assert exc_info.value.code == ErrorCode.EMBEDDING_UNAVAILABLE
    assert "向量维度与档案不一致" in (exc_info.value.message or "")

    # 远端：档案 512，返回 384。
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(
            200, json={"data": [{"index": 0, "embedding": [0.0] * 384}]}
        )

    remote = RemoteEmbeddingProvider(
        base_url="https://host/v1",
        api_key=API_KEY,
        model="bge",
        dim=512,
        transport=httpx.MockTransport(handler),
    )
    with pytest.raises(AppError) as exc_info:
        asyncio.run(remote.embed(["a"], purpose="document"))
    assert "向量维度与档案不一致" in (exc_info.value.message or "")


def test_resolver_selection_rules(db_session_factory) -> None:
    async def scenario() -> None:
        async with db_session_factory() as session:
            session.add_all(
                [
                    ModelProfile(
                        name="zzz-local",
                        kind="embedding",
                        provider="local_onnx",
                        model="zzz-model",
                        embedding_dim=4,
                        local_model_path="models/zzz",
                    ),
                    ModelProfile(
                        name="aaa-local",
                        kind="embedding",
                        provider="local_onnx",
                        model="aaa-model",
                        embedding_dim=4,
                        local_model_path="models/aaa",
                    ),
                ]
            )
            await session.commit()

        # none：Null + disabled。
        resolver = EmbeddingResolver(
            db_session_factory,
            settings_provider=_settings("none"),
            model_router=_Router(error=_missing_profile_error()),
        )
        provider = await resolver.current()
        assert isinstance(provider, NullEmbeddingProvider)
        assert resolver.status().status == "disabled"

        # remote 缺档案：Null + unavailable。
        resolver = EmbeddingResolver(
            db_session_factory,
            settings_provider=_settings("remote"),
            model_router=_Router(error=_missing_profile_error()),
        )
        provider = await resolver.current()
        assert isinstance(provider, NullEmbeddingProvider)
        assert resolver.status().status == "unavailable"
        assert resolver.status().provider == "remote"
        assert resolver.status().error

        # remote 档案类型不对：Null + unavailable，错误写明原因。
        resolver = EmbeddingResolver(
            db_session_factory,
            settings_provider=_settings("remote"),
            model_router=_Router(profile=_remote_profile(kind="chat")),
        )
        provider = await resolver.current()
        assert isinstance(provider, NullEmbeddingProvider)
        assert "类型" in (resolver.status().error or "")

        # remote 正常：返回远端 provider；同档案缓存同一个实例。
        resolver = EmbeddingResolver(
            db_session_factory,
            settings_provider=_settings("remote"),
            model_router=_Router(profile=_remote_profile()),
        )
        provider = await resolver.current()
        assert isinstance(provider, RemoteEmbeddingProvider)
        assert provider.name == "openai_compatible:bge-small-zh-v1.5"
        assert resolver.status().status == "ready"
        assert resolver.status().provider == "remote"
        assert await resolver.current() is provider

        # local：角色为空时按档案名取第一个启用的 local_onnx 档案。
        resolver = EmbeddingResolver(
            db_session_factory,
            settings_provider=_settings("local"),
            model_router=_Router(error=_missing_profile_error()),
        )
        provider = await resolver.current()
        assert isinstance(provider, LocalOnnxEmbeddingProvider)
        assert provider.name == "local_onnx:aaa-model"
        assert provider.model_dir == REPO_ROOT / "resource" / "models/aaa"
        assert resolver.status().status == "ready"
        assert await resolver.current() is provider

        # local：llm.role_embedding 指向 local_onnx 档案时优先用它。
        role_profile = SimpleNamespace(
            name="role-local",
            kind="embedding",
            provider="local_onnx",
            model="role-model",
            embedding_dim=4,
            local_model_path="models/role",
        )
        resolver = EmbeddingResolver(
            db_session_factory,
            settings_provider=_settings("local"),
            model_router=_Router(profile=role_profile),
        )
        provider = await resolver.current()
        assert provider.name == "local_onnx:role-model"
        assert provider.model_dir == REPO_ROOT / "resource" / "models/role"

        # local 找不到任何档案：Null + unavailable。
        resolver = EmbeddingResolver(
            db_session_factory,
            settings_provider=_settings("local"),
            model_router=_Router(error=_missing_profile_error()),
        )
        # 把两个本地档案停用后应当没有可选档案。
        async with db_session_factory() as session:
            rows = (await session.execute(select(ModelProfile))).scalars().all()
            for row in rows:
                row.enabled = False
            await session.commit()
        provider = await resolver.current()
        assert isinstance(provider, NullEmbeddingProvider)
        assert resolver.status().status == "unavailable"

    asyncio.run(scenario())
