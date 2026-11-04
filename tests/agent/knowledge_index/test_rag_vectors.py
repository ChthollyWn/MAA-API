"""M76 T4：向量 BLOB 编解码、矩阵缓存与 top_k（spec §4.2、§6）。"""

from __future__ import annotations

import asyncio
import struct
from typing import Any

import numpy as np
import pytest

from maa_api.agent.knowledge.index.vectors import (
    VectorCache,
    VectorMatrix,
    pack_vector,
    unpack_vector,
)
from maa_api.db.models import KnowledgeChunk, KnowledgeDocument, KnowledgeSource


def test_pack_roundtrip_little_endian() -> None:
    vector = np.asarray([1.5, -2.25, 0.0], dtype=np.float32)
    blob = pack_vector(vector)

    assert isinstance(blob, bytes)
    assert len(blob) == 12
    # 小端：第一个 float32 的最低字节在前。
    assert blob[:4] == struct.pack("<f", 1.5)
    assert unpack_vector(blob, 3).tolist() == vector.tolist()

    with pytest.raises(ValueError):
        unpack_vector(blob, 2)


async def _seed_vectors(
    session_factory: Any, vectors: list[tuple[str, list[float]]]
) -> list[int]:
    async with session_factory() as session:
        session.add(KnowledgeSource(id="builtin", kind="builtin", title="内置"))
        await session.commit()
    async with session_factory() as session:
        session.add(
            KnowledgeDocument(
                id="doc",
                source_id="builtin",
                external_id="e",
                title="文档",
                content_hash="h",
            )
        )
        await session.commit()
    chunks = [
        KnowledgeChunk(
            document_id="doc",
            ord=index,
            heading=None,
            text=f"块 {index}",
            char_count=3,
            embedding=pack_vector(np.asarray(vector, dtype=np.float32)),
            embedding_model="m",
            embedding_dim=len(vector),
        )
        for index, (_source_id, vector) in enumerate(vectors)
    ]
    async with session_factory() as session:
        session.add_all(chunks)
        await session.commit()
    return [int(chunk.id or 0) for chunk in chunks]


def test_cache_rebuilds_when_index_version_changes(db_session_factory) -> None:
    async def scenario() -> None:
        ids = await _seed_vectors(
            db_session_factory,
            [("builtin", [1.0, 0.0]), ("builtin", [0.0, 1.0])],
        )
        cache = VectorCache()
        first = await cache.get(db_session_factory, model="m", index_version=1)
        assert first is not None
        assert first.chunk_ids.tolist() == ids
        again = await cache.get(db_session_factory, model="m", index_version=1)
        assert again is first

        # 版本号没变时不重建：即使库里多了行，也返回旧矩阵。
        async with db_session_factory() as session:
            session.add(
                KnowledgeChunk(
                    document_id="doc",
                    ord=9,
                    heading=None,
                    text="块 9",
                    char_count=3,
                    embedding=pack_vector(np.asarray([1.0, 1.0], dtype=np.float32)),
                    embedding_model="m",
                    embedding_dim=2,
                )
            )
            await session.commit()
        stale = await cache.get(db_session_factory, model="m", index_version=1)
        assert stale is first
        assert len(stale.chunk_ids) == 2

        rebuilt = await cache.get(db_session_factory, model="m", index_version=2)
        assert rebuilt is not None
        assert rebuilt is not first
        assert rebuilt.version == 2
        assert len(rebuilt.chunk_ids) == 3

    asyncio.run(scenario())


def test_top_k_filters_sources_and_breaks_ties_by_id() -> None:
    vectors = np.asarray(
        [
            [1.0, 0.0],
            [1.0, 0.0],
            [0.0, 1.0],
            [0.6, 0.8],
        ],
        dtype=np.float32,
    )
    matrix = VectorMatrix(
        model="m",
        version=1,
        chunk_ids=np.asarray([5, 3, 9, 1], dtype=np.int64),
        source_ids=np.asarray(["s1", "s1", "s2", "s1"], dtype=object),
        matrix=vectors,
    )
    cache = VectorCache()
    query = np.asarray([1.0, 0.0], dtype=np.float32)

    assert cache.top_k(matrix, query, source_ids=["s1"], k=2) == [3, 5]
    assert cache.top_k(matrix, query, source_ids=["s1"], k=10) == [3, 5, 1]
    assert cache.top_k(matrix, query, source_ids=["s2"], k=3) == [9]
    assert cache.top_k(matrix, query, source_ids=["nope"], k=3) == []
    assert cache.top_k(matrix, query, source_ids=["s1"], k=0) == []
