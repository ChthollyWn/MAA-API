"""向量 BLOB 编解码与内存矩阵缓存（M76 spec §4.2、v3 docs/07 §2.2）。

分块向量按小端 ``float32`` 存进 ``knowledge_chunk.embedding``；检索时把当前模型的
全部向量拼成一个 ``(n, dim)`` 矩阵缓存在进程里。任何分块写入都会让
``KnowledgeIndexService.index_version`` 加一，缓存按它判断是否重建；重建里的
numpy 拼接放进线程，不阻塞事件循环。
"""

from __future__ import annotations

import asyncio
from collections.abc import Sequence
from dataclasses import dataclass
from typing import Any

import numpy as np
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from maa_api.db.models import KnowledgeChunk, KnowledgeDocument

__all__ = [
    "VectorCache",
    "VectorMatrix",
    "pack_vector",
    "top_k",
    "unpack_vector",
]


def pack_vector(vector: np.ndarray) -> bytes:
    """向量 → 小端 ``float32`` 字节串。"""
    return np.asarray(vector).astype("<f4").tobytes()


def unpack_vector(blob: bytes, dim: int) -> np.ndarray:
    """小端 ``float32`` 字节串 → 向量；长度与 ``dim`` 不符时抛 ``ValueError``。"""
    vector = np.frombuffer(blob, dtype="<f4")
    if vector.size != dim:
        raise ValueError(f"向量长度 {vector.size} 与维度 {dim} 不符")
    return vector


@dataclass
class VectorMatrix:
    """一个模型的全部当前向量；``matrix`` 已做 L2 归一化。"""

    model: str
    version: int
    chunk_ids: np.ndarray
    source_ids: np.ndarray
    matrix: np.ndarray


class VectorCache:
    """按 ``embedding_model`` 缓存一份矩阵；``index_version`` 变了就重建。"""

    def __init__(self) -> None:
        self._cached: VectorMatrix | None = None
        self._cached_key: tuple[str, int] | None = None

    async def get(
        self,
        session_factory: async_sessionmaker[AsyncSession],
        *,
        model: str,
        index_version: int,
    ) -> VectorMatrix | None:
        """取当前模型的矩阵；没有向量时返回 ``None``。"""
        key = (model, index_version)
        if self._cached_key == key:
            return self._cached
        rows = await self._load_rows(session_factory, model)
        self._cached = await asyncio.to_thread(_build_matrix, rows, model, index_version)
        self._cached_key = key
        return self._cached

    async def _load_rows(
        self, session_factory: async_sessionmaker[AsyncSession], model: str
    ) -> list[tuple[int, str, bytes]]:
        statement = (
            select(
                KnowledgeChunk.id,
                KnowledgeDocument.source_id,
                KnowledgeChunk.embedding,
            )
            .join(KnowledgeDocument, KnowledgeDocument.id == KnowledgeChunk.document_id)
            .where(
                KnowledgeChunk.embedding_model == model,
                KnowledgeChunk.embedding.is_not(None),
            )
            .order_by(KnowledgeChunk.id.asc())
        )
        async with session_factory() as session:
            result = await session.execute(statement)
            return [
                (int(chunk_id), str(source_id), blob)
                for chunk_id, source_id, blob in result.all()
            ]

    def top_k(
        self,
        m: VectorMatrix,
        query: np.ndarray,
        *,
        source_ids: Sequence[str],
        k: int,
    ) -> list[int]:
        """来源掩码后按余弦相似度取前 ``k``；同分按分块 id 升序。"""
        if k <= 0 or m.matrix.size == 0 or not source_ids:
            return []
        mask = np.isin(m.source_ids, np.asarray(list(source_ids), dtype=object))
        if not bool(mask.any()):
            return []
        scores = m.matrix[mask] @ np.asarray(query, dtype=np.float32)
        chunk_ids = m.chunk_ids[mask]
        count = min(k, scores.shape[0])
        if count <= 0:
            return []
        if count < scores.shape[0]:
            selected = np.argpartition(-scores, count - 1)[:count]
        else:
            selected = np.arange(scores.shape[0])
        ordered = sorted(
            (int(index) for index in selected),
            key=lambda index: (-float(scores[index]), int(chunk_ids[index])),
        )
        return [int(chunk_ids[index]) for index in ordered]


def top_k(
    m: VectorMatrix,
    query: np.ndarray,
    *,
    source_ids: Sequence[str],
    k: int,
) -> list[int]:
    """模块级 ``top_k``：与 :meth:`VectorCache.top_k` 同一实现。"""
    return VectorCache().top_k(m, query, source_ids=source_ids, k=k)


def _build_matrix(
    rows: Sequence[tuple[int, str, Any]], model: str, version: int
) -> VectorMatrix | None:
    """把 ``(chunk_id, source_id, blob)`` 行拼成归一化矩阵；没有行时返回 ``None``。"""
    if not rows:
        return None
    dim = len(rows[0][2]) // 4
    chunk_ids = np.empty(len(rows), dtype=np.int64)
    source_ids = np.empty(len(rows), dtype=object)
    matrix = np.empty((len(rows), dim), dtype=np.float32)
    for index, (chunk_id, source_id, blob) in enumerate(rows):
        chunk_ids[index] = int(chunk_id)
        source_ids[index] = str(source_id)
        matrix[index] = unpack_vector(blob, dim)
    norms = np.linalg.norm(matrix, axis=1, keepdims=True)
    np.divide(matrix, norms, out=matrix, where=norms > 0)
    return VectorMatrix(
        model=model,
        version=version,
        chunk_ids=chunk_ids,
        source_ids=source_ids,
        matrix=matrix,
    )
