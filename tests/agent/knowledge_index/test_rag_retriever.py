"""M76 T6：混合检索器（spec §4.2、§4.5、§6）。

用例走 ``tests/db/conftest.py`` 的临时库：直接建来源、文档与分块行（``create_all``
装了 FTS 触发器），向量由按关键词给固定向量的假 provider 在
``ensure_embeddings()`` 里写入。检索器每次现读 settings 桩，因此测试可以改开关。
"""

from __future__ import annotations

import asyncio
from datetime import datetime
from types import SimpleNamespace
from typing import Any, Sequence

import numpy as np
import pytest

from maa_api.agent.knowledge.index.embeddings import NullEmbeddingProvider
from maa_api.agent.knowledge.index.indexer import KnowledgeIndexService
from maa_api.agent.knowledge.index.retriever import (
    MAX_CHUNKS_PER_DOCUMENT,
    RRF_K,
    Retriever,
    rrf_merge,
)
from maa_api.db.models import KnowledgeChunk, KnowledgeDocument, KnowledgeSource
from maa_api.domain.enums import KnowledgeSourceKind
from maa_api.domain.errors import AppError, ErrorCode

FETCHED_AT = datetime(2026, 1, 1, 12, 0, 0)


# ---------------------------------------------------------------------------
# 桩：settings、来源、向量 provider 与 resolver
# ---------------------------------------------------------------------------
class _Settings:
    def __init__(self, *, enabled: bool = True, top_k: int = 8, **flags: Any) -> None:
        self.rag = SimpleNamespace(
            enabled=enabled,
            top_k=top_k,
            embedding_provider="none",
            chunk_chars=200,
            chunk_overlap=20,
        )
        for key, value in flags.items():
            setattr(self.rag, key, value)


class _Source:
    """已注册来源的元数据；索引与正文由测试直接写库，不走 list_refs/fetch。"""

    def __init__(
        self,
        source_id: str,
        *,
        title: str | None = None,
        searchable_by_default: bool = True,
        setting_key: str | None = None,
    ) -> None:
        self.source_id = source_id
        self.kind = KnowledgeSourceKind.BUILTIN
        self.title = title or source_id
        self.trusted = True
        self.license = None
        self.searchable_by_default = searchable_by_default
        self.setting_key = setting_key
        self.full_listing = True

    def validate_config(self, config: Any) -> dict[str, Any]:
        return dict(config)

    async def list_refs(self, config: Any) -> list[Any]:  # pragma: no cover - 用例不触发
        raise AssertionError("检索器不应调用来源的 list_refs")

    async def fetch(self, refs: Any, config: Any) -> list[Any]:  # pragma: no cover
        raise AssertionError("检索器不应调用来源的 fetch")


#: 关键词 → 四维 one-hot 向量；查询词 ``vectoronly`` 与正文 ``合成`` 同一向量。
_VECTORS: tuple[tuple[str, tuple[float, float, float, float]], ...] = (
    ("vectoronly", (0.0, 1.0, 0.0, 0.0)),
    ("合成", (0.0, 1.0, 0.0, 0.0)),
    ("固源岩", (1.0, 0.0, 0.0, 0.0)),
    ("材料", (0.0, 0.0, 1.0, 0.0)),
)


class _KeywordProvider:
    """按关键词给固定向量的假 provider（dim=4），与真实实现一样返回归一化矩阵。"""

    name = "fake:model"
    dim = 4

    def __init__(self) -> None:
        self.fail = False
        self.calls = 0

    async def embed(
        self, texts: Sequence[str], *, purpose: str
    ) -> np.ndarray:
        del purpose  # 假件不区分文档与查询
        self.calls += 1
        if self.fail:
            raise AppError(ErrorCode.EMBEDDING_UNAVAILABLE, "向量服务暂时不可用")
        rows = []
        for text in texts:
            vector = (0.0, 0.0, 0.0, 1.0)
            for keyword, candidate in _VECTORS:
                if keyword in text:
                    vector = candidate
                    break
            rows.append(vector)
        return np.asarray(rows, dtype=np.float32)


class _Resolver:
    """EmbeddingResolver 的桩；``provider`` 可在用例中途替换。"""

    def __init__(self, provider: Any = None) -> None:
        self.provider = provider or NullEmbeddingProvider()

    async def current(self) -> Any:
        return self.provider

    def status(self) -> Any:
        return SimpleNamespace(
            provider="remote", model=self.provider.name, status="ready", error=None
        )


# ---------------------------------------------------------------------------
# 造数与构造
# ---------------------------------------------------------------------------
async def _seed_source(
    factory: Any,
    source_id: str,
    *,
    enabled: bool = True,
    title: str | None = None,
    trusted: bool = True,
) -> None:
    async with factory() as session:
        session.add(
            KnowledgeSource(
                id=source_id,
                kind="builtin",
                title=title or source_id,
                trusted=trusted,
                enabled=enabled,
            )
        )
        await session.commit()


async def _seed_document(
    factory: Any,
    source_id: str,
    document_id: str,
    chunks: Sequence[tuple[str | None, str]],
    *,
    title: str = "文档",
) -> None:
    async with factory() as session:
        session.add(
            KnowledgeDocument(
                id=document_id,
                source_id=source_id,
                external_id=document_id,
                title=title,
                content_hash="h",
                version="v1",
                fetched_at=FETCHED_AT,
            )
        )
        await session.flush()
        session.add_all(
            [
                KnowledgeChunk(
                    document_id=document_id,
                    ord=index,
                    heading=heading,
                    text=text,
                    char_count=len(text),
                )
                for index, (heading, text) in enumerate(chunks)
            ]
        )
        await session.commit()


def _make_retriever(
    factory: Any,
    *,
    sources: Sequence[_Source],
    resolver: _Resolver,
    settings: _Settings,
) -> tuple[Retriever, KnowledgeIndexService]:
    service = KnowledgeIndexService(
        factory,
        settings_provider=lambda: settings,
        embeddings=resolver,  # type: ignore[arg-type]
    )
    for source in sources:
        service.register(source)
    retriever = Retriever(
        factory,
        index=service,
        settings_provider=lambda: settings,
    )
    return retriever, service


# ---------------------------------------------------------------------------
# 用例
# ---------------------------------------------------------------------------
def test_rrf_merge_order_and_ties() -> None:
    merged = rrf_merge([3, 1], [2, 3])
    assert [chunk_id for chunk_id, _, _ in merged] == [3, 2, 1]
    assert merged[0][1] == pytest.approx(1 / (RRF_K + 1) + 1 / (RRF_K + 2))
    assert merged[1][1] == pytest.approx(1 / (RRF_K + 1))
    assert merged[2][1] == pytest.approx(1 / (RRF_K + 2))
    assert merged[0][2] == [1, 2]
    assert merged[1][2] == [None, 1]
    assert merged[2][2] == [2, None]

    # 两路名次对称：分数相同，按 id 升序；ranks 仍记录各自的名次。
    tied = rrf_merge([2, 1], [1, 2])
    assert [chunk_id for chunk_id, _, _ in tied] == [1, 2]
    assert tied[0][1] == pytest.approx(1 / (RRF_K + 1) + 1 / (RRF_K + 2))
    assert tied[0][2] == [2, 1]
    assert tied[1][2] == [1, 2]

    # k 可覆盖；只有一路命中时另一路是 None。
    smaller = rrf_merge([7], [8], k=10)
    assert [chunk_id for chunk_id, _, _ in smaller] == [7, 8]
    assert smaller[0][1] == pytest.approx(1 / 11)
    assert smaller[0][2] == [1, None]
    assert smaller[1][2] == [None, 1]


def test_vector_modes(db_session_factory) -> None:
    async def scenario() -> None:
        settings = _Settings()
        provider = _KeywordProvider()
        resolver = _Resolver(NullEmbeddingProvider())
        retriever, service = _make_retriever(
            db_session_factory,
            sources=[_Source("builtin", title="内置知识")],
            resolver=resolver,
            settings=settings,
        )
        await _seed_source(db_session_factory, "builtin", title="内置知识")
        await _seed_document(
            db_session_factory, "builtin", "doc-a", [(None, "固源岩 掉落与刷图效率")]
        )
        await _seed_document(
            db_session_factory, "builtin", "doc-b", [(None, "合成 材料")]
        )
        try:
            # 1. provider 是 Null：向量路 disabled，全文照常。
            disabled = await retriever.search("固源岩")
            assert disabled.vector == "disabled"
            assert disabled.vector_model is None
            assert disabled.hits
            assert disabled.hits[0].fts_rank == 1
            assert disabled.hits[0].vector_rank is None

            # 2. 配了 provider 但还没有向量：empty，全文照常。
            resolver.provider = provider
            empty = await retriever.search("固源岩")
            assert empty.vector == "empty"
            assert empty.vector_model == provider.name
            assert empty.hits and empty.hits[0].fts_rank == 1

            # 3. 补完向量：只被向量路命中的分块也进结果，fts_rank 为 None。
            assert await service.ensure_embeddings() == 2
            used = await retriever.search("vectoronly")
            assert used.vector == "used"
            assert used.vector_model == provider.name
            assert used.hits
            vector_only = [hit for hit in used.hits if hit.fts_rank is None]
            assert vector_only
            assert "合成" in vector_only[0].text
            assert vector_only[0].vector_rank == 1
            # 全文路没命中时 snippet 取正文前 200 字。
            assert vector_only[0].snippet == vector_only[0].text[:200]

            # 4. 查询向量失败：unavailable，已有全文结果不丢。
            provider.fail = True
            unavailable = await retriever.search("固源岩")
            assert unavailable.vector == "unavailable"
            assert unavailable.vector_model == provider.name
            assert unavailable.hits
            assert unavailable.hits[0].fts_rank == 1
        finally:
            await service.close()

    asyncio.run(scenario())


def test_per_document_cap_is_three(db_session_factory) -> None:
    async def scenario() -> None:
        settings = _Settings()
        retriever, service = _make_retriever(
            db_session_factory,
            sources=[_Source("builtin")],
            resolver=_Resolver(NullEmbeddingProvider()),
            settings=settings,
        )
        await _seed_source(db_session_factory, "builtin")
        await _seed_document(
            db_session_factory,
            "builtin",
            "doc-long",
            [(f"第 {index} 节", f"固源岩 第 {index} 块") for index in range(5)],
        )
        try:
            result = await retriever.search("固源岩", top_k=5)
            assert len(result.hits) == MAX_CHUNKS_PER_DOCUMENT
            assert {hit.document_id for hit in result.hits} == {"doc-long"}
            # 融合顺序就是全文名次；被丢掉的是第 4、5 块。
            assert [hit.fts_rank for hit in result.hits] == [1, 2, 3]
            assert [hit.chunk_id for hit in result.hits] == sorted(
                hit.chunk_id for hit in result.hits
            )
        finally:
            await service.close()

    asyncio.run(scenario())


def test_disabled_source_is_skipped_and_reported(db_session_factory) -> None:
    async def scenario() -> None:
        settings = _Settings(gamma_enabled=False)
        sources = [
            _Source("alpha", title="甲"),
            _Source("beta", title="乙"),
            _Source("gamma", title="丙", setting_key="rag.gamma_enabled"),
        ]
        retriever, service = _make_retriever(
            db_session_factory,
            sources=sources,
            resolver=_Resolver(NullEmbeddingProvider()),
            settings=settings,
        )
        await _seed_source(db_session_factory, "alpha", title="甲")
        await _seed_source(db_session_factory, "beta", title="乙", enabled=False)
        await _seed_source(db_session_factory, "gamma", title="丙")
        await _seed_document(db_session_factory, "alpha", "doc-a", [(None, "固源岩 甲")])
        await _seed_document(db_session_factory, "beta", "doc-b", [(None, "固源岩 乙")])
        try:
            # 默认：停用的 beta 不查；setting_key 对应的开关为假时 gamma 也不查。
            default = await retriever.search("固源岩")
            assert default.sources == ["alpha"]
            assert default.skipped_sources == []
            assert {hit.source_id for hit in default.hits} == {"alpha"}

            settings.rag.gamma_enabled = True
            widened = await retriever.search("固源岩")
            assert widened.sources == ["alpha", "gamma"]

            # 点名停用来源：进 skipped_sources，重复 id 去重，仍查 alpha。
            named = await retriever.search("固源岩", sources=["beta", "alpha", "beta"])
            assert named.sources == ["alpha"]
            assert named.skipped_sources == ["beta"]
            assert {hit.source_id for hit in named.hits} == {"alpha"}

            # 只点停用来源：空结果，但仍要报告它被跳过。
            only_skipped = await retriever.search("固源岩", sources=["beta"])
            assert only_skipped.enabled is True
            assert only_skipped.sources == []
            assert only_skipped.skipped_sources == ["beta"]
            assert only_skipped.hits == []

            # 点名未注册来源：400，details.unknown 列出未知 id。
            with pytest.raises(AppError) as exc_info:
                await retriever.search("固源岩", sources=["alpha", "nope"])
            assert exc_info.value.code == ErrorCode.RETRIEVAL_QUERY_INVALID
            assert exc_info.value.details == {
                "field": "sources",
                "reason": "unknown_source",
                "unknown": ["nope"],
            }
        finally:
            await service.close()

    asyncio.run(scenario())


def test_rag_disabled_returns_empty(db_session_factory) -> None:
    async def scenario() -> None:
        settings = _Settings(enabled=False)
        retriever, service = _make_retriever(
            db_session_factory,
            sources=[_Source("builtin")],
            resolver=_Resolver(_KeywordProvider()),
            settings=settings,
        )
        await _seed_source(db_session_factory, "builtin")
        await _seed_document(
            db_session_factory, "builtin", "doc-a", [(None, "固源岩 掉落")]
        )
        try:
            result = await retriever.search("固源岩")
            assert result.enabled is False
            assert result.hits == []
            assert result.sources == []
            assert result.vector == "disabled"
            assert result.top_k == settings.rag.top_k
            # 总开关关闭时不做检索词校验，也不碰索引。
            unchecked = await retriever.search("  ")
            assert unchecked.enabled is False
            assert unchecked.hits == []
        finally:
            await service.close()

    asyncio.run(scenario())
