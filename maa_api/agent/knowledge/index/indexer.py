"""检索索引服务与来源协议（M76 spec §4.2、§4.5）。

一条同步流水线：列出清单 → 只取变化过的文档 → 每篇一个事务写文档与分块 →
``full_listing`` 来源删掉清单里没有的文档 → 写回计数与状态。失败时状态置
``error`` 但保留旧文档：索引坏了顶多检索不到新内容，不能让整个知识库消失。

并发纪律：同一来源同一时刻只有一个同步；同步中再次请求只记脏标记，结束后补跑
一次。补向量单独一把锁，``EMBEDDING_UNAVAILABLE`` 后 10 分钟内不再重试，避免
每轮后台任务都去撞一个坏掉的向量服务。
"""

from __future__ import annotations

import asyncio
import hashlib
import logging
import time
from collections.abc import Awaitable, Callable, Mapping, Sequence
from dataclasses import dataclass, field
from datetime import datetime
from typing import Any, Protocol

from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from maa_api.agent.knowledge.index.chunker import (
    ChunkDraft,
    chunk_markdown,
    chunker_signature,
)
from maa_api.agent.knowledge.index.embeddings import EmbeddingResolver
from maa_api.agent.knowledge.index.vectors import pack_vector
from maa_api.db.models import utcnow
from maa_api.db.repositories.knowledge_index import KnowledgeIndexRepository
from maa_api.db.session import run_with_busy_retry
from maa_api.domain.enums import KnowledgeSourceKind
from maa_api.domain.errors import AppError, ErrorCode

logger = logging.getLogger(__name__)

__all__ = [
    "CorpusSource",
    "DocumentRef",
    "KnowledgeIndexService",
    "SourceDocument",
    "SyncSummary",
]

#: 每批取多少篇文档：取一批、写一批，中途失败时已写的批次保留。
FETCH_BATCH_SIZE = 20

#: 每次补向量处理的分块数。
EMBEDDING_BATCH_SIZE = 64

#: ``EMBEDDING_UNAVAILABLE`` 后的冷却时间（秒）。
EMBEDDING_RETRY_SECONDS = 600.0

#: ``knowledge_source.error`` 的列宽是 TEXT，但消息只留摘要，免得前端一屏放不下。
MAX_ERROR_CHARS = 500

_ENTRY_SOURCE_IDS = ("builtin", "user")


@dataclass(frozen=True, slots=True)
class DocumentRef:
    """来源清单里的一项；只够判断是否需要取正文。"""

    external_id: str
    title: str
    version: str | None = None


@dataclass(frozen=True, slots=True)
class SourceDocument:
    """一篇已取回正文的文档。"""

    external_id: str
    title: str
    text: str
    url: str | None = None
    version: str | None = None
    fetched_at: datetime | None = None
    meta: dict[str, Any] = field(default_factory=dict)


class CorpusSource(Protocol):
    """一个可索引的来源（内置知识、自定义知识，M77 起还有外部语料）。

    下列成员对 M76 的两个条目来源是可选成员：服务一律用 ``getattr`` 取，
    缺省行为分别是「不参与定时刷新」「不调 ``start``/``close``/``prune``」。
    """

    source_id: str
    kind: KnowledgeSourceKind
    title: str
    trusted: bool
    license: str | None
    searchable_by_default: bool
    setting_key: str | None
    full_listing: bool

    #: 参与 ``refresh_all`` 定时刷新（缺省 False）。
    scheduled_refresh: bool

    def validate_config(self, config: Mapping[str, Any]) -> dict[str, Any]: ...

    #: 服务 ``start()`` 时调，用来挂监听器、起自己的后台任务（缺省不调）。
    async def start(self, index: "KnowledgeIndexService") -> None: ...

    #: 服务 ``close()`` 时调，收掉 ``start()`` 起的东西（缺省不调）。
    async def close(self) -> None: ...

    #: 同步成功后调，按配置删掉该来源的过期文档并返回删除数（缺省不调）。
    async def prune(self, config: Mapping[str, Any]) -> int: ...

    async def list_refs(
        self, config: Mapping[str, Any]
    ) -> list[DocumentRef]: ...

    async def fetch(
        self, refs: Sequence[DocumentRef], config: Mapping[str, Any]
    ) -> list[SourceDocument]: ...


@dataclass(frozen=True, slots=True)
class SyncSummary:
    """一次来源同步的结果；``fetched`` 是取回正文的篇数。"""

    source_id: str
    fetched: int
    rebuilt: int
    unchanged: int
    deleted: int
    chunks: int


def _zero_summary(source_id: str) -> SyncSummary:
    return SyncSummary(
        source_id=source_id,
        fetched=0,
        rebuilt=0,
        unchanged=0,
        deleted=0,
        chunks=0,
    )


def _source_not_found(source_id: str) -> AppError:
    """未注册来源的 404。

    专用错误码 ``KNOWLEDGE_SOURCE_NOT_FOUND`` 由 T7 与其余三个码一起登记；在那之前
    退回 ``KNOWLEDGE_NOT_FOUND``（同为 404），登记后 ``getattr`` 自动用上新码。
    """
    code = getattr(
        ErrorCode, "KNOWLEDGE_SOURCE_NOT_FOUND", ErrorCode.KNOWLEDGE_NOT_FOUND
    )
    return AppError(code, "检索来源不存在", {"source_id": source_id})


def _error_message(exc: BaseException) -> str:
    if isinstance(exc, AppError):
        message = f"{exc.code}: {exc.message}" if exc.message else str(exc.code)
    else:
        message = str(exc) or type(exc).__name__
    return message[:MAX_ERROR_CHARS]


def _sha256(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


class KnowledgeIndexService:
    """检索索引服务：注册来源、同步、补向量与条目变更回调。"""

    def __init__(
        self,
        session_factory: async_sessionmaker[AsyncSession],
        *,
        settings_provider: Callable[[], Any],
        embeddings: EmbeddingResolver,
    ) -> None:
        self._session_factory = session_factory
        self._settings_provider = settings_provider
        self._embeddings = embeddings
        self._sources: dict[str, CorpusSource] = {}
        self._index_version = 0
        self._busy: set[str] = set()
        self._dirty: dict[str, bool] = {}
        self._tasks: set[asyncio.Task[Any]] = set()
        self._source_locks: dict[str, asyncio.Lock] = {}
        self._source_rows: dict[str, tuple[bool, datetime | None]] = {}
        self._failed_sources: set[str] = set()
        self._refreshing = False
        self._embedding_lock = asyncio.Lock()
        self._embedding_task: asyncio.Task[Any] | None = None
        self._embedding_retry_at: float | None = None
        self._closed = False

    # -- 注册与状态 -----------------------------------------------------
    def register(self, source: CorpusSource) -> None:
        """注册一个来源；``start()`` 为每个已注册来源补齐表行。"""
        self._sources[source.source_id] = source

    def source(self, source_id: str) -> CorpusSource:
        """取已注册来源；未注册抛 ``KNOWLEDGE_SOURCE_NOT_FOUND``。"""
        source = self._sources.get(source_id)
        if source is None:
            raise _source_not_found(source_id)
        return source

    @property
    def index_version(self) -> int:
        """每次写分块或向量加一；向量缓存据此判断是否重建。"""
        return self._index_version

    def is_busy(self, source_id: str) -> bool:
        """来源是否正在同步（含已排期、尚未开始）。"""
        return source_id in self._busy

    # -- 生命周期 -------------------------------------------------------
    async def start(self) -> None:
        """补齐来源行、清理残留的 ``indexing``，启动来源，然后后台跑首次同步。"""
        async with self._session_factory() as session:
            repository = KnowledgeIndexRepository(session)
            for source in self._sources.values():
                await repository.ensure_source(source)
            await repository.reset_indexing()
            await session.commit()
        self._source_rows = await self._load_source_rows()
        for source in self._sources.values():
            start_source = getattr(source, "start", None)
            if start_source is None:
                continue
            try:
                await start_source(self)
            except Exception:  # noqa: BLE001 - 外部语料起不来时其余来源照常工作
                logger.warning("启动检索来源 %s 失败", source.source_id, exc_info=True)
        self._spawn(self._startup_index())

    async def close(self) -> None:
        """取消并等待后台任务，再收掉来源；之后不再排新任务。"""
        self._closed = True
        tasks = [task for task in self._tasks if not task.done()]
        for task in tasks:
            task.cancel()
        if tasks:
            await asyncio.gather(*tasks, return_exceptions=True)
        self._tasks.clear()
        self._embedding_task = None
        # 本服务的后台任务停掉后再关来源：来源的消费者可能正在等 push 的锁。
        for source in self._sources.values():
            close_source = getattr(source, "close", None)
            if close_source is None:
                continue
            try:
                await close_source()
            except Exception:  # noqa: BLE001 - 一个来源关不掉不影响其余收尾
                logger.warning("关闭检索来源 %s 失败", source.source_id, exc_info=True)

    async def _load_source_rows(self) -> dict[str, tuple[bool, datetime | None]]:
        """读一次来源行的 ``(enabled, last_indexed_at)`` 缓存，供 ``never_indexed`` 用。"""
        async with self._session_factory() as session:
            rows = await KnowledgeIndexRepository(session).list_sources()
            states = {row.id: (bool(row.enabled), row.last_indexed_at) for row in rows}
        self._source_rows = states
        return states

    async def _startup_index(self) -> None:
        try:
            for source_id in _ENTRY_SOURCE_IDS:
                if source_id in self._sources:
                    await self.sync_source(source_id)
            await self.ensure_embeddings()
        except asyncio.CancelledError:
            raise
        except Exception:  # noqa: BLE001 - 启动索引只是后台任务
            logger.warning("启动索引失败，等待下次触发", exc_info=True)

    # -- 同步 -----------------------------------------------------------
    def request_sync(self, source_id: str, *, force: bool = False) -> bool:
        """排一次后台同步；已在同步时只记脏标记并返回 ``False``。"""
        self.source(source_id)  # 未注册来源在调用点就失败
        if self._closed:
            return False
        if source_id in self._busy:
            self._mark_dirty(source_id, force)
            return False
        self._busy.add(source_id)
        self._spawn(self._run_sync(source_id, force))
        return True

    async def sync_source(
        self, source_id: str, *, force: bool = False
    ) -> SyncSummary:
        """同步一个来源；忙时记脏标记并返回空摘要。"""
        if source_id in self._busy:
            self._mark_dirty(source_id, force)
            return _zero_summary(source_id)
        self._busy.add(source_id)
        return await self._sync_body(source_id, force)

    async def _run_sync(self, source_id: str, force: bool) -> None:
        await self._sync_body(source_id, force)

    async def push_documents(
        self, source_id: str, docs: Sequence[SourceDocument]
    ) -> SyncSummary:
        """推送式写入：等该来源锁，按同步的同一套规则写文档，不删任何文档。

        与 ``sync_source`` 共用每来源锁与忙标记：同步进行中时等待；推送期间到达的
        同步请求看到忙只记脏标记。写完排一次 ``ensure_embeddings``。正文没变时只
        刷新抓取时间与版本（``touch_documents``）。
        """
        source = self.source(source_id)
        items = list(docs)
        async with self._source_lock(source_id):
            self._busy.add(source_id)
            try:
                summary = await self._push_body(source, items)
            finally:
                self._busy.discard(source_id)
                if not self._closed:
                    next_force = self._dirty.pop(source_id, None)
                    if next_force is not None:
                        self.request_sync(source_id, force=next_force)
                    self._schedule_embeddings()
        return summary

    async def _push_body(
        self, source: CorpusSource, docs: Sequence[SourceDocument]
    ) -> SyncSummary:
        source_id = source.source_id
        rag = self._settings_provider().rag
        signature = chunker_signature(int(rag.chunk_chars), int(rag.chunk_overlap))
        rebuilt = unchanged = chunks = 0
        async with self._session_factory() as session:
            repository = KnowledgeIndexRepository(session)
            await repository.ensure_source(source)
            existing = await repository.documents_of(source_id)
            await session.commit()
        for doc in docs:
            content_hash = _sha256(doc.text)
            old = existing.get(doc.external_id)
            if (
                old is not None
                and old.content_hash == content_hash
                and old.meta.get("chunker") == signature
            ):
                await self._touch_document(old.id, doc)
                unchanged += 1
                continue
            drafts = chunk_markdown(
                doc.text,
                max_chars=int(rag.chunk_chars),
                overlap=int(rag.chunk_overlap),
            )
            await self._write_document(
                source_id, doc, drafts, content_hash, signature
            )
            rebuilt += 1
            chunks += len(drafts)
        if rebuilt:
            self._index_version += 1
        return SyncSummary(
            source_id=source_id,
            fetched=len(docs),
            rebuilt=rebuilt,
            unchanged=unchanged,
            deleted=0,
            chunks=chunks,
        )

    async def refresh_all(self) -> dict[str, str]:
        """定时刷新所有语料来源，返回 ``{source_id: "ok" | "skipped" | "error"}``。

        ``rag.enabled`` 为假时直接返回空；依次同步 ``scheduled_refresh``、来源行
        启用、且开关设置为真的来源，一个来源失败不影响下一个；最后补一次向量。
        已有一次刷新在跑时返回 ``{}``（single-flight）。
        """
        if self._refreshing:
            return {}
        settings = self._settings_provider()
        rag = getattr(settings, "rag", None)
        if not bool(getattr(rag, "enabled", True)):
            return {}
        self._refreshing = True
        results: dict[str, str] = {}
        try:
            rows = await self._load_source_rows()
            for source in self._sources.values():
                if not bool(getattr(source, "scheduled_refresh", False)):
                    continue
                source_id = source.source_id
                enabled, _ = rows.get(source_id, (True, None))
                if not enabled or not _setting_enabled(
                    settings, getattr(source, "setting_key", None)
                ):
                    results[source_id] = "skipped"
                    continue
                if self.is_busy(source_id):
                    results[source_id] = "skipped"
                    continue
                self._failed_sources.discard(source_id)
                await self.sync_source(source_id)
                results[source_id] = (
                    "error" if source_id in self._failed_sources else "ok"
                )
            await self.ensure_embeddings()
            return results
        finally:
            self._refreshing = False

    def never_indexed(self) -> list[str]:
        """从未建过索引、来源行启用且开关为真的定时来源 id（读缓存的来源行）。"""
        settings = self._settings_provider()
        result: list[str] = []
        for source in self._sources.values():
            if not bool(getattr(source, "scheduled_refresh", False)):
                continue
            state = self._source_rows.get(source.source_id)
            if state is None or not state[0] or state[1] is not None:
                continue
            if _setting_enabled(settings, getattr(source, "setting_key", None)):
                result.append(source.source_id)
        return result

    async def _sync_body(self, source_id: str, force: bool) -> SyncSummary:
        """拿每来源锁再同步；推送写入期间同步请求会在这里等锁。"""
        lock = self._source_lock(source_id)
        try:
            await lock.acquire()
        except BaseException:  # noqa: BLE001 - 等锁被取消时要把忙标记还回去
            self._busy.discard(source_id)
            raise
        try:
            return await self._sync_locked(source_id, force)
        finally:
            lock.release()

    async def _sync_locked(self, source_id: str, force: bool) -> SyncSummary:
        source = self.source(source_id)
        fetched = rebuilt = unchanged = deleted = chunks = 0
        try:
            config, model = await self._begin_sync(source)
            rag = self._settings_provider().rag
            signature = chunker_signature(
                int(rag.chunk_chars), int(rag.chunk_overlap)
            )
            async with self._session_factory() as session:
                existing = await KnowledgeIndexRepository(session).documents_of(
                    source_id
                )
            refs = await source.list_refs(config)
            needed = [
                ref
                for ref in refs
                if force
                or ref.external_id not in existing
                or ref.version is None
                or existing[ref.external_id].version != ref.version
                or existing[ref.external_id].meta.get("chunker") != signature
            ]
            for batch in _batches(needed, FETCH_BATCH_SIZE):
                docs = await source.fetch(batch, config)
                fetched += len(docs)
                for doc in docs:
                    content_hash = _sha256(doc.text)
                    old = existing.get(doc.external_id)
                    if (
                        old is not None
                        and old.content_hash == content_hash
                        and old.meta.get("chunker") == signature
                    ):
                        await self._touch_document(old.id, doc)
                        unchanged += 1
                        continue
                    drafts = chunk_markdown(
                        doc.text,
                        max_chars=int(rag.chunk_chars),
                        overlap=int(rag.chunk_overlap),
                    )
                    await self._write_document(
                        source_id, doc, drafts, content_hash, signature
                    )
                    rebuilt += 1
                    chunks += len(drafts)
            if source.full_listing:
                ref_ids = {ref.external_id for ref in refs}
                stale = [
                    old.id
                    for external_id, old in existing.items()
                    if external_id not in ref_ids
                ]
                if stale:
                    async with self._session_factory() as session:
                        repository = KnowledgeIndexRepository(session)
                        deleted = await repository.delete_documents(stale)
                        await session.commit()
            prune = getattr(source, "prune", None)
            if prune is not None:
                deleted += int(await prune(config))
            await self._finish_sync(source_id, model)
            self._failed_sources.discard(source_id)
            return SyncSummary(
                source_id=source_id,
                fetched=fetched,
                rebuilt=rebuilt,
                unchanged=unchanged,
                deleted=deleted,
                chunks=chunks,
            )
        except asyncio.CancelledError:
            raise
        except Exception as exc:  # noqa: BLE001 - 失败落 error，旧文档保留
            self._failed_sources.add(source_id)
            logger.warning(
                "同步检索来源 %s 失败：%s", source_id, _error_message(exc), exc_info=True
            )
            await self._record_error(source_id, exc)
            return SyncSummary(
                source_id=source_id,
                fetched=fetched,
                rebuilt=rebuilt,
                unchanged=unchanged,
                deleted=deleted,
                chunks=chunks,
            )
        finally:
            if rebuilt or deleted:
                self._index_version += 1
            self._busy.discard(source_id)
            if not self._closed:
                next_force = self._dirty.pop(source_id, None)
                if next_force is not None:
                    self.request_sync(source_id, force=next_force)
                if rebuilt or deleted:
                    self._schedule_embeddings()

    async def _begin_sync(
        self, source: CorpusSource
    ) -> tuple[dict[str, Any], str]:
        """确保来源行、置 ``indexing``，返回配置与当前向量模型名。"""
        provider = await self._embeddings.current()
        async with self._session_factory() as session:
            repository = KnowledgeIndexRepository(session)
            row = await repository.ensure_source(source)
            config = dict(row.config or {})
            await repository.update_source_state(
                source.source_id, status="indexing", error=None
            )
            await session.commit()
        return config, provider.name

    async def _finish_sync(self, source_id: str, model: str) -> None:
        async with self._session_factory() as session:
            repository = KnowledgeIndexRepository(session)
            doc_count, chunk_count, _ = await repository.counts(source_id, model)
            await repository.update_source_state(
                source_id,
                status="idle",
                error=None,
                counts=(doc_count, chunk_count),
                last_indexed_at=utcnow(),
            )
            await session.commit()

    async def _record_error(self, source_id: str, exc: BaseException) -> None:
        try:
            async with self._session_factory() as session:
                repository = KnowledgeIndexRepository(session)
                await repository.update_source_state(
                    source_id, status="error", error=_error_message(exc)
                )
                await session.commit()
        except Exception:  # noqa: BLE001 - 连错误状态都写不进去时只能记日志
            logger.warning("写入检索来源 %s 的错误状态失败", source_id, exc_info=True)

    async def _touch_document(self, document_id: str, doc: SourceDocument) -> None:
        async with self._session_factory() as session:
            repository = KnowledgeIndexRepository(session)

            async def operation() -> None:
                await repository.touch_documents(
                    [document_id], doc.fetched_at, doc.version
                )

            await run_with_busy_retry(session, operation)
            await session.commit()

    async def _write_document(
        self,
        source_id: str,
        doc: SourceDocument,
        drafts: Sequence[ChunkDraft],
        content_hash: str,
        signature: str,
    ) -> None:
        async with self._session_factory() as session:
            repository = KnowledgeIndexRepository(session)

            async def operation() -> None:
                row = await repository.upsert_document(
                    source_id=source_id,
                    external_id=doc.external_id,
                    title=doc.title,
                    content_hash=content_hash,
                    url=doc.url,
                    version=doc.version,
                    fetched_at=doc.fetched_at,
                    meta={**doc.meta, "chunker": signature},
                )
                await repository.replace_chunks(row.id, drafts)

            await run_with_busy_retry(session, operation)
            await session.commit()

    def _mark_dirty(self, source_id: str, force: bool) -> None:
        self._dirty[source_id] = self._dirty.get(source_id, False) or force

    def _source_lock(self, source_id: str) -> asyncio.Lock:
        """每来源一把锁：同步与推送写入共用，保证同一来源同一时刻只有一方写。"""
        lock = self._source_locks.get(source_id)
        if lock is None:
            lock = asyncio.Lock()
            self._source_locks[source_id] = lock
        return lock

    # -- 向量 -----------------------------------------------------------
    async def ensure_embeddings(self) -> int:
        """补齐当前模型缺的向量，返回本次写入的分块数。"""
        if self._embedding_lock.locked():
            return 0
        async with self._embedding_lock:
            if (
                self._embedding_retry_at is not None
                and time.monotonic() < self._embedding_retry_at
            ):
                return 0
            provider = await self._embeddings.current()
            if provider.name == "none":
                return 0
            embedded = 0
            while True:
                async with self._session_factory() as session:
                    rows = await KnowledgeIndexRepository(
                        session
                    ).chunks_missing_vectors(provider.name, EMBEDDING_BATCH_SIZE)
                if not rows:
                    break
                texts = [
                    _embedding_text(title, heading, text)
                    for _, title, heading, text in rows
                ]
                try:
                    matrix = await provider.embed(texts, purpose="document")
                except AppError as exc:
                    if exc.code == ErrorCode.EMBEDDING_UNAVAILABLE:
                        self._embedding_retry_at = (
                            time.monotonic() + EMBEDDING_RETRY_SECONDS
                        )
                        logger.warning(
                            "补向量失败，%d 秒内不再重试：%s",
                            int(EMBEDDING_RETRY_SECONDS),
                            _error_message(exc),
                        )
                        return embedded
                    raise
                payload = [
                    (chunk_id, pack_vector(matrix[index]))
                    for index, (chunk_id, *_rest) in enumerate(rows)
                ]
                async with self._session_factory() as session:
                    repository = KnowledgeIndexRepository(session)
                    await repository.write_vectors(
                        payload, model=provider.name, dim=int(matrix.shape[1])
                    )
                    await session.commit()
                embedded += len(rows)
                self._index_version += 1
                await asyncio.sleep(0)
            return embedded

    def _schedule_embeddings(self) -> None:
        if self._closed:
            return
        if self._embedding_task is not None and not self._embedding_task.done():
            return
        self._embedding_task = self._spawn(self._run_embeddings())

    async def _run_embeddings(self) -> None:
        try:
            await self.ensure_embeddings()
        finally:
            self._embedding_task = None

    # -- 条目变更 -------------------------------------------------------
    async def on_knowledge_changed(self, entry_id: str) -> None:
        """知识条目变更回调：条目只来自 ``builtin`` / ``user`` 两个来源。

        条目被删除后无法再判断它原来属于哪个来源（内置覆盖删除后仍是内置条目），
        所以两个内置来源都排一次同步；哈希没变的文档第二次不会重建。
        """
        del entry_id  # 变更内容由来源自己的 list_refs 重新读，回调只需要知道「变了」
        for source_id in _ENTRY_SOURCE_IDS:
            if source_id not in self._sources:
                continue
            try:
                self.request_sync(source_id)
            except Exception:  # noqa: BLE001 - 监听器失败不能影响知识条目写入
                logger.warning("条目变更触发 %s 同步失败", source_id, exc_info=True)

    # -- 后台任务 -------------------------------------------------------
    def _spawn(self, coro: Awaitable[Any]) -> asyncio.Task[Any]:
        task = asyncio.create_task(coro)
        self._tasks.add(task)
        task.add_done_callback(self._task_done)
        return task

    def _task_done(self, task: asyncio.Task[Any]) -> None:
        self._tasks.discard(task)
        if task.cancelled():
            return
        error = task.exception()
        if error is not None:
            logger.warning("检索索引后台任务失败：%s", _error_message(error), exc_info=error)


def _batches(items: Sequence[DocumentRef], size: int) -> list[list[DocumentRef]]:
    return [list(items[start : start + size]) for start in range(0, len(items), size)]


def _embedding_text(title: str, heading: str | None, text: str) -> str:
    return f"{title}\n{heading or ''}\n{text}"


def _setting_enabled(settings: Any, key: str | None) -> bool:
    """按 ``rag.prts_enabled`` 这样的点分键读设置；没有这类开关的来源视为开启。"""
    if not key:
        return True
    value: Any = settings
    for part in key.split("."):
        value = getattr(value, part, None)
        if value is None:
            return False
    return bool(value)
