"""混合检索：全文与向量两路检索，用 RRF 融合（M76 spec §4.2、§4.5、v3 docs/07 §2.3）。

检索分五步：检查总开关 → 校验检索词与来源 → 全文路 → 向量路（四态）→ RRF
融合后按「每文档至多 3 块」拼结果。向量路的任何失败都只体现在
``RetrievalResult.vector`` 上，不影响已经拿到的全文结果：向量是锦上添花，
检索本身不能因为一个没配好的 embedding 档案而整个报错。
"""

from __future__ import annotations

import logging
import time
from collections.abc import Callable, Sequence
from datetime import datetime
from typing import Any, Literal

from pydantic import BaseModel
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from maa_api.agent.knowledge.index.embeddings import EmbeddingProvider
from maa_api.agent.knowledge.index.fts import FtsPlan, build_fts_plan, search_lexical
from maa_api.agent.knowledge.index.indexer import KnowledgeIndexService
from maa_api.agent.knowledge.index.vectors import VectorCache
from maa_api.db.repositories.knowledge_index import (
    ChunkRow,
    KnowledgeIndexRepository,
)
from maa_api.domain.errors import AppError, ErrorCode

__all__ = [
    "MAX_CHUNKS_PER_DOCUMENT",
    "RRF_K",
    "RetrievalHit",
    "RetrievalResult",
    "Retriever",
    "rrf_merge",
]

logger = logging.getLogger(__name__)

#: RRF 的平滑常数：名次 ``rank`` 的分值是 ``1 / (k + rank)``。
RRF_K = 60

#: 同一篇文档在一次检索里最多保留几块，避免一篇长文档霸屏。
MAX_CHUNKS_PER_DOCUMENT = 3

#: 检索词长度上限。
MAX_QUERY_CHARS = 500

#: ``top_k`` 的取值区间；``rag.top_k`` 的设置校验用同一组边界。
MIN_TOP_K = 1
MAX_TOP_K = 30

#: 全文与向量两路各取多少个候选再融合。
CANDIDATE_MIN = 40
CANDIDATE_FACTOR = 4

#: 向量路的四种状态。
VectorState = Literal["used", "disabled", "unavailable", "empty"]


class RetrievalHit(BaseModel):
    """一条检索结果：分块内容 + 出处 + 两路名次与融合分数。"""

    chunk_id: int
    document_id: str
    source_id: str
    source_title: str
    trusted: bool
    title: str
    heading: str | None
    url: str | None
    version: str | None
    fetched_at: datetime | None
    license: str | None
    snippet: str
    text: str
    score: float
    fts_rank: int | None
    vector_rank: int | None


class RetrievalResult(BaseModel):
    """一次检索的结果；``vector`` 说明向量路走到了哪一步。"""

    query: str
    top_k: int
    enabled: bool
    vector: VectorState
    vector_model: str | None
    sources: list[str]
    skipped_sources: list[str]
    hits: list[RetrievalHit]
    took_ms: int


def rrf_merge(
    *ranked: Sequence[int], k: int = RRF_K
) -> list[tuple[int, float, list[int | None]]]:
    """倒数名次融合；同分先比最好名次，再按分块 id 升序。

    返回 ``(chunk_id, score, ranks)``，``ranks`` 与输入的各路一一对应，某一路没
    命中时是 ``None``。输入里重复的分块只按第一次出现计分。
    """
    scores: dict[int, float] = {}
    best_rank: dict[int, int] = {}
    ranks: dict[int, list[int | None]] = {}
    for path, chunk_ids in enumerate(ranked):
        seen: set[int] = set()
        for position, raw_chunk_id in enumerate(chunk_ids, start=1):
            chunk_id = int(raw_chunk_id)
            if chunk_id in seen:
                continue
            seen.add(chunk_id)
            if chunk_id not in ranks:
                ranks[chunk_id] = [None] * len(ranked)
                scores[chunk_id] = 0.0
                best_rank[chunk_id] = position
            ranks[chunk_id][path] = position
            scores[chunk_id] += 1.0 / (k + position)
            best_rank[chunk_id] = min(best_rank[chunk_id], position)
    ordered = sorted(
        scores, key=lambda chunk_id: (-scores[chunk_id], best_rank[chunk_id], chunk_id)
    )
    return [(chunk_id, scores[chunk_id], ranks[chunk_id]) for chunk_id in ordered]


class Retriever:
    """把全文与向量两路结果融合成带出处的检索结果。"""

    def __init__(
        self,
        session_factory: async_sessionmaker[AsyncSession],
        *,
        index: KnowledgeIndexService,
        settings_provider: Callable[[], Any],
    ) -> None:
        self._session_factory = session_factory
        self._index = index
        self._settings_provider = settings_provider
        self._vectors = VectorCache()
        # G3 的索引服务持有 EmbeddingResolver（构造参数 ``embeddings``），但没有
        # 暴露公开访问器；检索器与它在同一个包内，按内部属性取用，避免重复解析。
        self._embeddings = getattr(index, "_embeddings", None)
        if self._embeddings is None:
            raise TypeError("Retriever 需要一个持有 EmbeddingResolver 的索引服务")

    async def search(
        self,
        query: str,
        *,
        sources: Sequence[str] | None = None,
        top_k: int | None = None,
        caller: str = "retrieval_api",
    ) -> RetrievalResult:
        """检索 ``query``；``sources`` 省略时用默认可检索来源，``caller`` 只作调用方标识。"""
        del caller  # 只保留调用方标识；M76 不做向量成本记账
        started = time.perf_counter()
        settings = self._settings_provider()
        rag = settings.rag

        if not rag.enabled:
            return self._empty(
                query=str(query or "").strip(),
                top_k=self._disabled_top_k(top_k, rag),
                enabled=False,
                started=started,
            )

        text = self._validate_query(query)
        limit = self._validate_top_k(top_k, rag)

        async with self._session_factory() as session:
            rows = await KnowledgeIndexRepository(session).list_sources()
        enabled_rows = {row.id: bool(row.enabled) for row in rows}
        allowed, skipped = self._resolve_sources(
            sources,
            rows=[row.id for row in rows],
            enabled=enabled_rows,
            settings=settings,
        )
        if not allowed:
            return self._empty(
                query=text,
                top_k=limit,
                enabled=True,
                started=started,
                sources=[],
                skipped_sources=skipped,
            )

        candidate = max(limit * CANDIDATE_FACTOR, CANDIDATE_MIN)
        plan = self._build_plan(text)
        async with self._session_factory() as session:
            lexical = await search_lexical(
                session, plan, source_ids=allowed, limit=candidate
            )
        vector_state, vector_model, vector_ids = await self._search_vectors(
            text, allowed, candidate
        )

        fused = rrf_merge(
            [hit.chunk_id for hit in lexical],
            vector_ids,
        )
        snippets = {hit.chunk_id: hit.snippet for hit in lexical}
        by_id = await self._chunk_rows([chunk_id for chunk_id, _, _ in fused])

        hits: list[RetrievalHit] = []
        per_document: dict[str, int] = {}
        for chunk_id, score, ranks in fused:
            row = by_id.get(chunk_id)
            if row is None:  # 两次查询之间被删掉的分块
                continue
            count = per_document.get(row.document_id, 0)
            if count >= MAX_CHUNKS_PER_DOCUMENT:
                continue
            fts_rank = ranks[0]
            vector_rank = ranks[1] if len(ranks) > 1 else None
            snippet = snippets.get(chunk_id) if fts_rank is not None else None
            hits.append(
                RetrievalHit(
                    chunk_id=row.chunk_id,
                    document_id=row.document_id,
                    source_id=row.source_id,
                    source_title=row.source_title,
                    trusted=row.trusted,
                    title=row.title,
                    heading=row.heading,
                    url=row.url,
                    version=row.version,
                    fetched_at=row.fetched_at,
                    license=row.license,
                    snippet=(snippet or row.text[:200]),
                    text=row.text,
                    score=score,
                    fts_rank=fts_rank,
                    vector_rank=vector_rank,
                )
            )
            per_document[row.document_id] = count + 1
            if len(hits) >= limit:
                break

        return RetrievalResult(
            query=text,
            top_k=limit,
            enabled=True,
            vector=vector_state,
            vector_model=vector_model,
            sources=allowed,
            skipped_sources=skipped,
            hits=hits,
            took_ms=_elapsed_ms(started),
        )

    # -- 校验与来源 -----------------------------------------------------
    @staticmethod
    def _validate_query(query: str) -> str:
        text = (query or "").strip() if isinstance(query, str) else ""
        if not text:
            raise _invalid("q", "empty", "检索词不能为空")
        if len(text) > MAX_QUERY_CHARS:
            raise _invalid(
                "q", "too_long", f"检索词最多 {MAX_QUERY_CHARS} 字"
            )
        return text

    @staticmethod
    def _validate_top_k(top_k: int | None, rag: Any) -> int:
        if top_k is None:
            return int(rag.top_k)
        if isinstance(top_k, bool) or not isinstance(top_k, int):
            raise _invalid("top_k", "not_integer", "top_k 必须是整数")
        if not MIN_TOP_K <= top_k <= MAX_TOP_K:
            raise _invalid(
                "top_k", "out_of_range", f"top_k 必须在 {MIN_TOP_K}–{MAX_TOP_K} 之间"
            )
        return top_k

    @staticmethod
    def _disabled_top_k(top_k: int | None, rag: Any) -> int:
        """总开关关闭时不做校验，尽量原样回显调用方给的值。"""
        if isinstance(top_k, int) and not isinstance(top_k, bool) and top_k > 0:
            return top_k
        return int(rag.top_k)

    def _resolve_sources(
        self,
        requested: Sequence[str] | None,
        *,
        rows: Sequence[str],
        enabled: dict[str, bool],
        settings: Any,
    ) -> tuple[list[str], list[str]]:
        """返回 ``(实际检索的来源, 点名但被停用的来源)``。"""
        if requested is None:
            allowed: list[str] = []
            for source_id in rows:
                source = self._source_or_none(source_id)
                if source is None or not enabled.get(source_id, True):
                    continue
                if not source.searchable_by_default:
                    continue
                if source.setting_key and not _setting_enabled(
                    settings, source.setting_key
                ):
                    continue
                allowed.append(source_id)
            return allowed, []

        wanted = [str(source_id) for source_id in requested if source_id]
        unique = list(dict.fromkeys(wanted))
        unknown = [
            source_id
            for source_id in unique
            if self._source_or_none(source_id) is None
        ]
        if unknown:
            raise _invalid(
                "sources",
                "unknown_source",
                "有检索来源没有注册",
                unknown=unknown,
            )
        allowed = []
        skipped = []
        for source_id in unique:
            if enabled.get(source_id, True):
                allowed.append(source_id)
            else:
                skipped.append(source_id)
        return allowed, skipped

    def _source_or_none(self, source_id: str) -> Any:
        """已注册的来源对象；未注册返回 ``None``。"""
        try:
            return self._index.source(source_id)
        except AppError:
            return None

    # -- 两路检索 -------------------------------------------------------
    @staticmethod
    def _build_plan(text: str) -> FtsPlan:
        try:
            return build_fts_plan(text)
        except AppError as exc:
            if exc.code is not ErrorCode.RETRIEVAL_QUERY_INVALID:
                raise
            details = dict(exc.details or {})
            details.setdefault("field", "q")
            raise AppError(exc.code, exc.message, details) from None

    async def _search_vectors(
        self, text: str, source_ids: Sequence[str], candidate: int
    ) -> tuple[VectorState, str | None, list[int]]:
        """向量路的四态；任何失败都只记状态，不向上抛。"""
        provider: EmbeddingProvider = await self._embeddings.current()
        if provider.name == "none":
            return "disabled", None, []
        matrix = await self._vectors.get(
            self._session_factory,
            model=provider.name,
            index_version=self._index.index_version,
        )
        if matrix is None or matrix.matrix.size == 0:
            return "empty", provider.name, []
        try:
            query_matrix = await provider.embed([text], purpose="query")
        except Exception as exc:  # noqa: BLE001 - 向量服务失败只降级，不影响全文
            logger.warning("查询向量失败，本次只走全文检索：%s", exc)
            return "unavailable", provider.name, []
        if query_matrix.size == 0:
            return "unavailable", provider.name, []
        ids = self._vectors.top_k(
            matrix,
            query_matrix[0],
            source_ids=source_ids,
            k=candidate,
        )
        return "used", provider.name, ids

    async def _chunk_rows(self, ids: Sequence[int]) -> dict[int, ChunkRow]:
        if not ids:
            return {}
        async with self._session_factory() as session:
            rows = await KnowledgeIndexRepository(session).chunk_rows(ids)
        return {row.chunk_id: row for row in rows}

    # -- 空结果 ---------------------------------------------------------
    @staticmethod
    def _empty(
        *,
        query: str,
        top_k: int,
        enabled: bool,
        started: float,
        sources: Sequence[str] = (),
        skipped_sources: Sequence[str] = (),
    ) -> RetrievalResult:
        return RetrievalResult(
            query=query,
            top_k=top_k,
            enabled=enabled,
            vector="disabled",
            vector_model=None,
            sources=list(sources),
            skipped_sources=list(skipped_sources),
            hits=[],
            took_ms=_elapsed_ms(started),
        )


def _invalid(field: str, reason: str, message: str, **extra: Any) -> AppError:
    """构造带 ``details.field`` 的 ``RETRIEVAL_QUERY_INVALID``。"""
    details: dict[str, Any] = {"field": field, "reason": reason}
    details.update(extra)
    return AppError(ErrorCode.RETRIEVAL_QUERY_INVALID, message, details)


def _setting_enabled(settings: Any, key: str) -> bool:
    """按点分路径取开关设置项；路径不存在按关闭处理。"""
    value: Any = settings
    for part in key.split("."):
        value = getattr(value, part, None)
        if value is None:
            return False
    return bool(value)


def _elapsed_ms(started: float) -> int:
    return max(0, int((time.perf_counter() - started) * 1000))
