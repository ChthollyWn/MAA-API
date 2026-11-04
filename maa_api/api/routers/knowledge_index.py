"""检索端点（M76 规格 §4.3）：测试检索、来源列表、开关与重建索引。

四个端点由独立的路由文件实现并沿用 ``agent`` tag（``operationId`` 仍是
``agent_*``）。校验放在函数体内而不是 ``Query(min_length=...)``：后者的失败是
FastAPI 原生的 422，契约要的是 400 ``RETRIEVAL_QUERY_INVALID``，且 ``details``
要带 ``field`` / ``reason``。服务实例由 T9 装配到 ``app.state``，路由只取用。

``GET /sources`` 的计数现查仓储而不是读来源行上的缓存列：同步进行中时两者会短暂
不一致，列表要展示「现在库里有什么」。``POST reindex`` 先做一次 FTS5
``integrity-check``，坏了就 ``rebuild``，再排后台同步。
"""

from __future__ import annotations

import logging
from datetime import UTC, datetime
from typing import Any, Literal

from fastapi import APIRouter, Depends, Path, Query, Request, status
from pydantic import BaseModel, ConfigDict, Field, field_serializer
from sqlalchemy import text

from maa_api.agent.knowledge.index.embeddings import EmbeddingStatus
from maa_api.agent.knowledge.index.indexer import KnowledgeIndexService
from maa_api.agent.knowledge.index.retriever import (
    MAX_QUERY_CHARS,
    MAX_TOP_K,
    MIN_TOP_K,
    RetrievalResult,
    Retriever,
)
from maa_api.api.deps import require_auth
from maa_api.api.errors import error_responses
from maa_api.db.models import KnowledgeSource
from maa_api.db.repositories.knowledge_index import KnowledgeIndexRepository
from maa_api.domain.errors import AppError, ErrorCode
from maa_api.settings import get_settings

__all__ = [
    "EmbeddingStatusView",
    "ReindexAccepted",
    "RetrievalSourceList",
    "RetrievalSourceUpdate",
    "RetrievalSourceView",
    "router",
]

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/agent/retrieval", tags=["agent"])

#: FTS5 的完整性校验与重建命令，写法见 <https://sqlite.org/fts5.html#the_integrity_check_command>。
_FTS_INTEGRITY_CHECK = (
    "INSERT INTO knowledge_chunk_fts(knowledge_chunk_fts) VALUES('integrity-check')"
)
_FTS_REBUILD = "INSERT INTO knowledge_chunk_fts(knowledge_chunk_fts) VALUES('rebuild')"

SourceStatus = Literal["idle", "indexing", "error"]


# ----------------------------------------------------------------------
# 模型
# ----------------------------------------------------------------------
def _utc_z(value: datetime | None) -> str | None:
    """把库里的 naive UTC 时间渲染成 ``...Z``（与 ``RunSummary`` 同口径）。"""
    if value is None:
        return None
    if value.tzinfo is None:
        value = value.replace(tzinfo=UTC)
    return value.astimezone(UTC).isoformat().replace("+00:00", "Z")


class RetrievalSourceView(BaseModel):
    """一个检索来源的完整对外形状：元数据、开关、状态与三类计数。"""

    id: str
    kind: str
    title: str
    enabled: bool
    setting_enabled: bool | None
    searchable_by_default: bool
    trusted: bool
    license: str | None
    status: SourceStatus
    error: str | None
    doc_count: int
    chunk_count: int
    vector_count: int
    last_indexed_at: datetime | None = Field(
        json_schema_extra={"format": "date-time"}
    )
    config: dict[str, Any]

    @field_serializer("last_indexed_at")
    def _serialize_time(self, value: datetime | None) -> str | None:
        return _utc_z(value)


class EmbeddingStatusView(BaseModel):
    """向量服务的状态与全库补向量进度。"""

    provider: Literal["none", "remote", "local"]
    model: str | None
    status: Literal["disabled", "ready", "indexing", "unavailable"]
    error: str | None
    total_chunks: int
    embedded_chunks: int


class RetrievalSourceList(BaseModel):
    """来源列表与全局向量状态。"""

    items: list[RetrievalSourceView]
    embedding: EmbeddingStatusView


class RetrievalSourceUpdate(BaseModel):
    """``PUT`` 的入参：两个字段都可选，但至少要改一项。"""

    model_config = ConfigDict(extra="forbid")

    enabled: bool | None = None
    config: dict[str, Any] | None = None


class ReindexAccepted(BaseModel):
    """``POST reindex`` 已排队；真正的重建在后台进行。"""

    source_id: str
    status: Literal["indexing"]


# ----------------------------------------------------------------------
# 依赖与服务
# ----------------------------------------------------------------------
def _index_service(request: Request) -> KnowledgeIndexService:
    service = getattr(request.app.state, "knowledge_index_service", None)
    if service is None:
        raise AppError(ErrorCode.SERVICE_UNAVAILABLE, "检索索引服务还在启动中，请稍后重试")
    return service


def _retriever(request: Request) -> Retriever:
    retriever = getattr(request.app.state, "retriever", None)
    if retriever is None:
        raise AppError(ErrorCode.SERVICE_UNAVAILABLE, "检索服务还在启动中，请稍后重试")
    return retriever


def _settings(request: Request) -> Any:
    provider = getattr(getattr(request.app, "state", None), "settings_provider", None)
    return provider() if callable(provider) else get_settings()


def _session_factory(request: Request, service: Any = None) -> Any:
    factory = getattr(getattr(request.app, "state", None), "session_factory", None)
    if factory is None and service is not None:
        factory = getattr(service, "_session_factory", None)
    if factory is None:
        raise AppError(ErrorCode.SERVICE_UNAVAILABLE, "检索索引服务还没启动")
    return factory


def _resolver(service: Any) -> Any:
    return getattr(service, "_embeddings", None)


def _embedding_state(service: Any) -> EmbeddingStatus | None:
    resolver = _resolver(service)
    status_method = getattr(resolver, "status", None)
    return status_method() if callable(status_method) else None


def _embedding_model_name(embedding: EmbeddingStatus | None) -> str:
    """仓储里的 ``embedding_model`` 是 provider 的 ``name``（含实现前缀）。"""
    if embedding is None or embedding.provider == "none" or not embedding.model:
        return "none"
    prefix = "openai_compatible" if embedding.provider == "remote" else "local_onnx"
    return f"{prefix}:{embedding.model}"


def _source_or_none(service: Any, source_id: str) -> Any:
    try:
        return service.source(source_id)
    except AppError as exc:
        if exc.code is ErrorCode.KNOWLEDGE_SOURCE_NOT_FOUND:
            return None
        raise


def _setting_enabled(settings: Any, source: Any) -> bool | None:
    key = getattr(source, "setting_key", None)
    if not key:
        return None
    value: Any = settings
    for part in key.split("."):
        value = getattr(value, part, None)
        if value is None:
            break
    return bool(value) if value is not None else False


def _row_status(raw: str | None) -> SourceStatus:
    value = str(raw or "idle")
    if value == "indexing" or value == "error":
        return value
    return "idle"


def _source_view(
    row: KnowledgeSource,
    source: Any,
    counts: tuple[int, int, int],
    settings: Any,
) -> RetrievalSourceView:
    doc_count, chunk_count, vector_count = counts
    return RetrievalSourceView(
        id=row.id,
        kind=str(source.kind),
        title=str(source.title),
        enabled=bool(row.enabled),
        setting_enabled=_setting_enabled(settings, source),
        searchable_by_default=bool(source.searchable_by_default),
        trusted=bool(source.trusted),
        license=source.license,
        status=_row_status(row.status),
        error=row.error,
        doc_count=doc_count,
        chunk_count=chunk_count,
        vector_count=vector_count,
        last_indexed_at=row.last_indexed_at,
        config=dict(row.config or {}),
    )


def _embedding_view(
    embedding: EmbeddingStatus | None, total_chunks: int, embedded_chunks: int
) -> EmbeddingStatusView:
    provider: Literal["none", "remote", "local"] = "none"
    model: str | None = None
    raw_status = "disabled"
    error: str | None = None
    if embedding is not None:
        provider = embedding.provider
        model = embedding.model
        raw_status = embedding.status
        error = embedding.error
    state: Literal["disabled", "ready", "indexing", "unavailable"]
    if raw_status == "ready" and embedded_chunks < total_chunks:
        # 向量服务可用但还有分块没补上：列表据此让前端轮询进度。
        state = "indexing"
    elif raw_status in ("disabled", "ready", "unavailable"):
        state = raw_status
    else:  # pragma: no cover - EmbeddingStatus 的类型已限定取值
        state = "unavailable"
    return EmbeddingStatusView(
        provider=provider,
        model=model,
        status=state,
        error=error,
        total_chunks=total_chunks,
        embedded_chunks=embedded_chunks,
    )


# ----------------------------------------------------------------------
# 检索校验
# ----------------------------------------------------------------------
def _invalid(field: str, reason: str, message: str, **extra: Any) -> AppError:
    """构造带 ``details.field`` 的 ``RETRIEVAL_QUERY_INVALID``（400）。"""
    details: dict[str, Any] = {"field": field, "reason": reason}
    details.update(extra)
    return AppError(ErrorCode.RETRIEVAL_QUERY_INVALID, message, details)


def _validate_query(q: str) -> str:
    value = q.strip()
    if not value:
        raise _invalid("q", "empty", "检索词不能为空")
    if len(value) > MAX_QUERY_CHARS:
        raise _invalid("q", "too_long", f"检索词最多 {MAX_QUERY_CHARS} 字")
    return value


def _validate_top_k(top_k: int | None) -> int | None:
    if top_k is None:
        return None
    if not MIN_TOP_K <= top_k <= MAX_TOP_K:
        raise _invalid(
            "top_k",
            "out_of_range",
            f"top_k 必须在 {MIN_TOP_K}–{MAX_TOP_K} 之间",
        )
    return top_k


def _validate_sources(request: Request, raw: str | None) -> list[str] | None:
    if raw is None:
        return None
    parts = list(dict.fromkeys(part.strip() for part in raw.split(",") if part.strip()))
    if not parts:
        return None
    service = _index_service(request)
    unknown = [
        source_id for source_id in parts if _source_or_none(service, source_id) is None
    ]
    if unknown:
        raise _invalid(
            "sources",
            "unknown_source",
            "有检索来源没有注册",
            unknown=unknown,
        )
    return parts


def _with_utc_times(result: RetrievalResult) -> RetrievalResult:
    """把命中里的 naive UTC 时间补上时区，序列化后才是 ``...Z``。"""
    if not result.hits:
        return result
    hits = []
    for hit in result.hits:
        fetched_at = hit.fetched_at
        if fetched_at is not None:
            if fetched_at.tzinfo is None:
                fetched_at = fetched_at.replace(tzinfo=UTC)
            else:
                fetched_at = fetched_at.astimezone(UTC)
        hits.append(hit.model_copy(update={"fetched_at": fetched_at}))
    return result.model_copy(update={"hits": hits})


# ----------------------------------------------------------------------
# 端点
# ----------------------------------------------------------------------
@router.get(
    "/search",
    response_model=RetrievalResult,
    summary="检索知识与已建索引的资料",
    responses=error_responses("RETRIEVAL_QUERY_INVALID", "UNAUTHORIZED"),
    dependencies=[Depends(require_auth)],
)
async def search_retrieval(
    request: Request,
    q: str = Query(description="检索词，去掉首尾空白后 1–500 字"),
    sources: str | None = Query(
        default=None, description="逗号分隔的来源 id；省略时检索默认可检索来源"
    ),
    top_k: int | None = Query(default=None, description="返回条数，1–30；省略时用设置值"),
) -> RetrievalResult:
    """按内容检索；向量服务失败只体现在 ``vector`` 上，不影响全文结果。"""
    query = _validate_query(q)
    limit = _validate_top_k(top_k)
    source_ids = _validate_sources(request, sources)
    result = await _retriever(request).search(
        query,
        sources=source_ids,
        top_k=limit,
        caller="retrieval_api",
    )
    return _with_utc_times(result)


@router.get(
    "/sources",
    response_model=RetrievalSourceList,
    summary="读取检索来源与向量状态",
    responses=error_responses("UNAUTHORIZED"),
    dependencies=[Depends(require_auth)],
)
async def list_retrieval_sources(request: Request) -> RetrievalSourceList:
    """列出已注册来源的开关、状态与计数，以及全库补向量进度。"""
    service = _index_service(request)
    settings = _settings(request)
    embedding = _embedding_state(service)
    model = _embedding_model_name(embedding)
    items: list[RetrievalSourceView] = []
    total_chunks = 0
    embedded_chunks = 0
    async with _session_factory(request, service)() as session:
        repository = KnowledgeIndexRepository(session)
        rows = await repository.list_sources()
        for row in rows:
            source = _source_or_none(service, row.id)
            if source is None:  # 行还在但代码里已移除该来源：不再展示
                continue
            counts = await repository.counts(row.id, model)
            items.append(_source_view(row, source, counts, settings))
            total_chunks += counts[1]
            embedded_chunks += counts[2]
    return RetrievalSourceList(
        items=items,
        embedding=_embedding_view(embedding, total_chunks, embedded_chunks),
    )


@router.put(
    "/sources/{source_id}",
    response_model=RetrievalSourceView,
    summary="更新检索来源的开关与配置",
    responses=error_responses(
        "KNOWLEDGE_SOURCE_NOT_FOUND", "VALIDATION_ERROR", "UNAUTHORIZED"
    ),
    dependencies=[Depends(require_auth)],
)
async def update_retrieval_source(
    payload: RetrievalSourceUpdate,
    request: Request,
    source_id: str = Path(description="检索来源 id"),
) -> RetrievalSourceView:
    """改 ``enabled`` 或整份 ``config``；``config`` 先交来源自己的校验。"""
    if payload.enabled is None and payload.config is None:
        raise AppError(
            ErrorCode.VALIDATION_ERROR,
            "至少要改一项",
            {"reason": "至少要改一项"},
        )
    service = _index_service(request)
    source = service.source(source_id)
    validated: dict[str, Any] | None = None
    if payload.config is not None:
        validated = source.validate_config(payload.config)
    settings = _settings(request)
    embedding = _embedding_state(service)
    model = _embedding_model_name(embedding)
    async with _session_factory(request, service)() as session:
        repository = KnowledgeIndexRepository(session)
        row = await repository.ensure_source(source)
        if validated is not None:
            row.config = validated
        if payload.enabled is not None:
            row.enabled = payload.enabled
        await session.commit()
        counts = await repository.counts(source_id, model)
        view = _source_view(row, source, counts, settings)
    logger.info(
        "检索来源更新：id=%s enabled=%s config=%s",
        source_id,
        view.enabled,
        bool(validated is not None),
    )
    return view


async def _ensure_fts_integrity(session_factory: Any) -> None:
    """校验 FTS5 索引；损坏时重建一次（只记 warning，不让请求失败）。"""
    try:
        async with session_factory() as session:
            await session.execute(text(_FTS_INTEGRITY_CHECK))
            await session.commit()
    except Exception:  # noqa: BLE001 - 校验/重建是尽力而为，坏索引由下一次同步补
        logger.warning("知识全文索引完整性校验失败，执行一次 rebuild", exc_info=True)
        async with session_factory() as session:
            await session.execute(text(_FTS_REBUILD))
            await session.commit()


@router.post(
    "/sources/{source_id}/reindex",
    status_code=status.HTTP_202_ACCEPTED,
    response_model=ReindexAccepted,
    summary="重建检索来源的索引",
    responses=error_responses(
        "KNOWLEDGE_SOURCE_NOT_FOUND", "KNOWLEDGE_SOURCE_BUSY", "UNAUTHORIZED"
    ),
    dependencies=[Depends(require_auth)],
)
async def reindex_retrieval_source(
    request: Request,
    source_id: str = Path(description="检索来源 id"),
) -> ReindexAccepted:
    """后台重建：先校验全文索引，再排一次强制同步；同步中返回 409。"""
    service = _index_service(request)
    service.source(source_id)
    if service.is_busy(source_id):
        raise AppError(
            ErrorCode.KNOWLEDGE_SOURCE_BUSY,
            "来源正在建立索引，请稍后再试",
            {"source_id": source_id},
        )
    await _ensure_fts_integrity(_session_factory(request, service))
    if not service.request_sync(source_id, force=True):
        raise AppError(
            ErrorCode.KNOWLEDGE_SOURCE_BUSY,
            "来源正在建立索引，请稍后再试",
            {"source_id": source_id},
        )
    logger.info("检索来源重建索引：id=%s", source_id)
    return ReindexAccepted(source_id=source_id, status="indexing")
