"""检索端点契约（M76 规格 §4.3、§6 的六条用例）。

用例只装 ``knowledge_index`` 路由：来源、文档与分块写进 ``tests/api/conftest.py``
的 ``isolated_db`` 临时库，不碰真实 ``resource/maa_api.db``。索引服务、检索器与
EmbeddingResolver 都用真实实现，只把来源替换成不联网的桩；``POST reindex`` 的
「忙」用一个阻塞在 ``list_refs`` 的来源制造，确保两次请求之间后台同步不会结束。

T9 之前 ``app.state`` 的装配由本文件自己完成（与生产同款：``knowledge_index_service``、
``retriever``、``session_factory``、``settings_provider``）。
"""

from __future__ import annotations

import asyncio
import threading
import time
from datetime import datetime
from types import SimpleNamespace
from typing import Any

import pytest
from fastapi import FastAPI
from sqlmodel import SQLModel

import maa_api.db.models  # noqa: F401  # 注册全部表（含 FTS 触发器）
from maa_api.agent.knowledge.index.embeddings import EmbeddingResolver
from maa_api.agent.knowledge.index.indexer import KnowledgeIndexService
from maa_api.agent.knowledge.index.retriever import Retriever
from maa_api.api import deps
from maa_api.api.errors import register_exception_handlers
from maa_api.api.routers import knowledge_index as knowledge_index_router
from maa_api.db import session as db_session
from maa_api.db.models import KnowledgeChunk, KnowledgeDocument
from maa_api.db.repositories.knowledge_index import KnowledgeIndexRepository
from maa_api.domain.enums import KnowledgeSourceKind
from maa_api.domain.errors import AppError, ErrorCode
from maa_api.settings import get_settings

#: 库里存的是 naive UTC；端点输出要补成 ``...Z``。
FETCHED_AT = datetime(2026, 11, 2, 8, 0, 0)
CHUNK_TEXT = "以 1-7 刷固源岩为推荐做法"
CHUNK_HEADING = "常驻最优图的判断"
HIT_FIELDS = {
    "chunk_id",
    "document_id",
    "source_id",
    "source_title",
    "trusted",
    "title",
    "heading",
    "url",
    "version",
    "fetched_at",
    "license",
    "snippet",
    "text",
    "score",
    "fts_rank",
    "vector_rank",
}


class _Source:
    """已注册来源的元数据；正文由用例直接写库，同步时返回空清单。"""

    def __init__(
        self,
        source_id: str,
        title: str,
        *,
        kind: KnowledgeSourceKind = KnowledgeSourceKind.BUILTIN,
        searchable_by_default: bool = True,
        setting_key: str | None = None,
    ) -> None:
        self.source_id = source_id
        self.kind = kind
        self.title = title
        self.trusted = True
        self.license = None
        self.searchable_by_default = searchable_by_default
        self.setting_key = setting_key
        self.full_listing = True
        #: 非 None 时 ``list_refs`` 会阻塞到这个事件被置位（reindex 的忙态用）。
        self.gate: threading.Event | None = None

    def validate_config(self, config: Any) -> dict[str, Any]:
        """桩来源和内置来源一样不接受配置。"""
        if config:
            raise AppError(
                ErrorCode.VALIDATION_ERROR,
                "来源不接受配置",
                {"reason": "config_not_supported"},
            )
        return {}

    async def list_refs(self, config: Any) -> list[Any]:
        del config
        if self.gate is not None:
            await asyncio.to_thread(self.gate.wait)
        return []

    async def fetch(self, refs: Any, config: Any) -> list[Any]:  # pragma: no cover
        raise AssertionError("空清单不会触发起正文")


def _error(response):
    return response.json()["error"]


async def _prepare_db(engine, factory) -> KnowledgeIndexService:
    """建表、注册两个来源并列出一篇文档；返回真实的索引服务。"""
    async with engine.begin() as connection:
        await connection.run_sync(SQLModel.metadata.create_all)
    service = KnowledgeIndexService(
        factory,
        settings_provider=get_settings,
        embeddings=EmbeddingResolver(
            factory, settings_provider=get_settings, model_router=None
        ),
    )
    builtin = _Source("builtin", "内置知识")
    user = _Source("user", "自定义知识", kind=KnowledgeSourceKind.USER)
    service.register(builtin)
    service.register(user)
    async with factory() as session:
        repository = KnowledgeIndexRepository(session)
        await repository.ensure_source(builtin)
        await repository.ensure_source(user)
        builtin_row = await repository.get_source("builtin")
        assert builtin_row is not None
        builtin_row.last_indexed_at = FETCHED_AT
        session.add(
            KnowledgeDocument(
                id="doc-builtin",
                source_id="builtin",
                external_id="drop-efficiency",
                title="掉落与刷图效率",
                content_hash="hash-1",
                version="v1",
                fetched_at=FETCHED_AT,
            )
        )
        await session.flush()
        session.add(
            KnowledgeChunk(
                document_id="doc-builtin",
                ord=0,
                heading=CHUNK_HEADING,
                text=CHUNK_TEXT,
                char_count=len(CHUNK_TEXT),
            )
        )
        await session.commit()
    return service


@pytest.fixture
def retrieval_env(isolated_db, tmp_settings):
    """临时库 + 真实索引服务 / 检索器；路由从 ``app.state`` 取它们。"""
    factory = db_session.session_factory
    service = asyncio.run(_prepare_db(isolated_db, factory))
    # 种子连接属于 asyncio.run 的事件循环，交给 TestClient 的循环之前先回收。
    isolated_db.sync_engine.dispose()
    retriever = Retriever(factory, index=service, settings_provider=get_settings)

    app = FastAPI()
    register_exception_handlers(app)
    app.include_router(knowledge_index_router.router)
    app.state.knowledge_index_service = service
    app.state.retriever = retriever
    app.state.session_factory = factory
    app.state.settings_provider = get_settings
    env = SimpleNamespace(
        app=app,
        service=service,
        retriever=retriever,
        factory=factory,
        sources={"builtin": service.source("builtin"), "user": service.source("user")},
    )
    yield env


# ---------------------------------------------------------------------------
# §6 的六条用例
# ---------------------------------------------------------------------------
def test_search_validates_q_top_k_and_sources(retrieval_env, make_client):
    """空 q、top_k 越界、未知来源都是 400，且 ``details.field`` 对得上。"""
    client = make_client(retrieval_env.app)
    cases = [
        ({"q": ""}, "q"),
        ({"q": "   "}, "q"),
        ({"q": "字" * 501}, "q"),
        ({"q": "固源岩", "top_k": 0}, "top_k"),
        ({"q": "固源岩", "top_k": 31}, "top_k"),
        ({"q": "固源岩", "sources": "builtin,nope"}, "sources"),
    ]
    for params, field in cases:
        response = client.get("/api/agent/retrieval/search", params=params)
        assert response.status_code == 400, response.text
        error = _error(response)
        assert error["code"] == "RETRIEVAL_QUERY_INVALID"
        assert error["details"]["field"] == field

    unknown = client.get(
        "/api/agent/retrieval/search", params={"q": "固源岩", "sources": "builtin,nope"}
    )
    assert _error(unknown)["details"]["unknown"] == ["nope"]


def test_search_returns_hits_with_provenance(retrieval_env, make_client):
    """命中带完整出处，``fetched_at`` 是带 ``Z`` 的 UTC 时间。"""
    client = make_client(retrieval_env.app)
    response = client.get(
        "/api/agent/retrieval/search",
        params={"q": "  固源岩  ", "top_k": 2},
    )
    assert response.status_code == 200, response.text
    body = response.json()
    assert set(body) == {
        "query",
        "top_k",
        "enabled",
        "vector",
        "vector_model",
        "sources",
        "skipped_sources",
        "hits",
        "took_ms",
    }
    assert body["query"] == "固源岩"
    assert body["top_k"] == 2
    assert body["enabled"] is True
    assert body["vector"] == "disabled"
    assert body["vector_model"] is None
    assert body["sources"] == ["builtin", "user"]
    assert body["skipped_sources"] == []
    assert isinstance(body["took_ms"], int) and body["took_ms"] >= 0

    assert len(body["hits"]) == 1
    hit = body["hits"][0]
    assert set(hit) == HIT_FIELDS
    assert hit["document_id"] == "doc-builtin"
    assert hit["source_id"] == "builtin"
    assert hit["source_title"] == "内置知识"
    assert hit["trusted"] is True
    assert hit["title"] == "掉落与刷图效率"
    assert hit["heading"] == CHUNK_HEADING
    assert hit["url"] is None
    assert hit["version"] == "v1"
    assert hit["fetched_at"] == "2026-11-02T08:00:00Z"
    assert hit["license"] is None
    assert "固源岩" in hit["snippet"]
    assert hit["text"] == CHUNK_TEXT
    assert hit["score"] > 0
    assert hit["fts_rank"] == 1
    assert hit["vector_rank"] is None


def test_list_sources_includes_embedding_status(retrieval_env, make_client):
    """来源列表给出两个来源、计数与全局向量状态；时间补 Z。"""
    client = make_client(retrieval_env.app)
    response = client.get("/api/agent/retrieval/sources")
    assert response.status_code == 200, response.text
    body = response.json()
    assert [item["id"] for item in body["items"]] == ["builtin", "user"]

    builtin = body["items"][0]
    assert builtin["kind"] == "builtin"
    assert builtin["title"] == "内置知识"
    assert builtin["enabled"] is True
    assert builtin["setting_enabled"] is None
    assert builtin["searchable_by_default"] is True
    assert builtin["trusted"] is True
    assert builtin["license"] is None
    assert builtin["status"] == "idle"
    assert builtin["error"] is None
    assert builtin["doc_count"] == 1
    assert builtin["chunk_count"] == 1
    assert builtin["vector_count"] == 0
    assert builtin["last_indexed_at"] == "2026-11-02T08:00:00Z"
    assert builtin["config"] == {}

    embedding = body["embedding"]
    assert embedding["provider"] == "none"
    assert embedding["model"] is None
    assert embedding["status"] == "disabled"
    assert embedding["error"] is None
    assert embedding["total_chunks"] == 1
    assert embedding["embedded_chunks"] == 0


def test_update_source_enabled_and_config_validation(retrieval_env, make_client):
    """改 enabled 成功；内置来源不接受配置；未知来源 404；空请求体 422。"""
    client = make_client(retrieval_env.app)
    updated = client.put(
        "/api/agent/retrieval/sources/builtin", json={"enabled": False}
    )
    assert updated.status_code == 200, updated.text
    body = updated.json()
    assert body["id"] == "builtin"
    assert body["enabled"] is False
    assert body["config"] == {}

    unknown = client.put(
        "/api/agent/retrieval/sources/nope", json={"enabled": True}
    )
    assert unknown.status_code == 404, unknown.text
    assert _error(unknown)["code"] == "KNOWLEDGE_SOURCE_NOT_FOUND"

    rejected = client.put(
        "/api/agent/retrieval/sources/builtin", json={"config": {"x": 1}}
    )
    assert rejected.status_code == 422, rejected.text
    assert _error(rejected)["code"] == "VALIDATION_ERROR"

    empty_object = client.put("/api/agent/retrieval/sources/builtin", json={})
    assert empty_object.status_code == 422, empty_object.text
    assert _error(empty_object)["details"]["reason"] == "至少要改一项"

    empty_body = client.put("/api/agent/retrieval/sources/builtin")
    assert empty_body.status_code == 422, empty_body.text
    assert _error(empty_body)["code"] == "VALIDATION_ERROR"


def test_reindex_accepts_then_conflicts_while_busy(retrieval_env, make_client):
    """首次 202；同步没结束前再点一次是 409 ``KNOWLEDGE_SOURCE_BUSY``。"""
    client = make_client(retrieval_env.app)
    source = retrieval_env.sources["builtin"]
    gate = threading.Event()
    source.gate = gate

    calls: list[tuple[str, bool]] = []
    original = retrieval_env.service.request_sync

    def recording(source_id: str, *, force: bool = False) -> bool:
        calls.append((source_id, force))
        return original(source_id, force=force)

    retrieval_env.service.request_sync = recording  # type: ignore[method-assign]
    try:
        accepted = client.post("/api/agent/retrieval/sources/builtin/reindex")
        assert accepted.status_code == 202, accepted.text
        assert accepted.json() == {"source_id": "builtin", "status": "indexing"}
        assert calls == [("builtin", True)]

        busy = client.post("/api/agent/retrieval/sources/builtin/reindex")
        assert busy.status_code == 409, busy.text
        assert _error(busy)["code"] == "KNOWLEDGE_SOURCE_BUSY"

        unknown = client.post("/api/agent/retrieval/sources/nope/reindex")
        assert unknown.status_code == 404, unknown.text
        assert _error(unknown)["code"] == "KNOWLEDGE_SOURCE_NOT_FOUND"
    finally:
        gate.set()
        # 放行后台同步并等它结束，避免任务跨用例残留。
        for _ in range(200):
            if not retrieval_env.service.is_busy("builtin"):
                break
            time.sleep(0.01)


@pytest.mark.parametrize("tmp_settings", ["retrieval-secret"], indirect=True)
def test_routes_require_auth(retrieval_env, make_client, tmp_settings):
    """配了 token 后四条路由不带 token 都是 401。"""
    client = make_client(retrieval_env.app)
    deps.reset_rate_limiter()
    try:
        unauthenticated = [
            client.get("/api/agent/retrieval/search", params={"q": "固源岩"}),
            client.get("/api/agent/retrieval/sources"),
            client.put(
                "/api/agent/retrieval/sources/builtin", json={"enabled": True}
            ),
            client.post("/api/agent/retrieval/sources/builtin/reindex"),
        ]
        for response in unauthenticated:
            assert response.status_code == 401, response.text
            assert _error(response)["code"] == "UNAUTHORIZED"

        headers = {"X-Token": "retrieval-secret"}
        assert (
            client.get("/api/agent/retrieval/sources", headers=headers).status_code
            == 200
        )
    finally:
        deps.reset_rate_limiter()
