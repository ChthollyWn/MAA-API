"""``knowledge_source`` / ``knowledge_document`` / ``knowledge_chunk`` 的读写（M76 §4.2）。

三张表是检索层的存储：来源行保存开关与索引状态，文档按 ``(source_id,
external_id)`` 唯一，分块是 FTS5 与向量检索的单元。本仓储只负责「怎么读写」，
同步策略（哪些文档要重建、状态机、``index_version``）属于
:class:`maa_api.agent.knowledge.index.indexer.KnowledgeIndexService`。

事务纪律（docs/04 §9）：所有方法都不 ``commit()``，由调用方决定事务边界；
需要主键的写入会 ``flush()``，但不会把事务落盘。
"""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass
from datetime import datetime
from typing import TYPE_CHECKING, Any

from sqlalchemy import delete, func, or_, select, update

from maa_api.db.models import (
    KnowledgeChunk,
    KnowledgeDocument,
    KnowledgeSource,
    utcnow,
)
from maa_api.db.repositories.base import BaseRepository

if TYPE_CHECKING:
    from maa_api.agent.knowledge.index.chunker import ChunkDraft

__all__ = ["ChunkRow", "KnowledgeIndexRepository"]


@dataclass(frozen=True, slots=True)
class ChunkRow:
    """拼检索结果需要的分块 + 文档 + 来源字段（一次 JOIN 取齐）。"""

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
    text: str


class KnowledgeIndexRepository(BaseRepository):
    """检索索引三张表的仓储；不 commit。"""

    # -- 来源 -----------------------------------------------------------
    async def get_source(self, source_id: str) -> KnowledgeSource | None:
        """按 id 取来源行；没建过索引时返回 ``None``。"""
        return await self.session.get(KnowledgeSource, source_id)

    async def list_sources(self) -> list[KnowledgeSource]:
        """全部来源行，按 id 升序。"""
        result = await self.session.execute(
            select(KnowledgeSource).order_by(KnowledgeSource.id.asc())
        )
        return list(result.scalars().all())

    async def ensure_source(self, source: Any) -> KnowledgeSource:
        """缺行就插入（启用、空配置）；有行只刷新代码侧元数据。

        ``enabled`` 与 ``config`` 是用户设置，不在同步时覆盖。
        """
        row = await self.get_source(source.source_id)
        if row is None:
            row = KnowledgeSource(
                id=source.source_id,
                kind=str(source.kind),
                title=source.title,
                enabled=True,
                trusted=bool(source.trusted),
                license=source.license,
                config={},
                status="idle",
            )
            self.session.add(row)
        else:
            row.kind = str(source.kind)
            row.title = source.title
            row.trusted = bool(source.trusted)
            row.license = source.license
            row.updated_at = utcnow()
        await self.session.flush()
        return row

    async def update_source_state(
        self,
        source_id: str,
        *,
        status: str,
        error: str | None,
        counts: tuple[int, int] | None = None,
        last_indexed_at: datetime | None = None,
    ) -> None:
        """更新索引状态；``counts`` 是 ``(文档数, 分块数)``，``None`` 表示不动计数。"""
        values: dict[str, Any] = {
            "status": status,
            "error": error,
            "updated_at": utcnow(),
        }
        if counts is not None:
            values["doc_count"], values["chunk_count"] = int(counts[0]), int(counts[1])
        if last_indexed_at is not None:
            values["last_indexed_at"] = last_indexed_at
        await self.session.execute(
            update(KnowledgeSource)
            .where(KnowledgeSource.id == source_id)
            .values(**values)
            .execution_options(synchronize_session=False)
        )

    # -- 文档 -----------------------------------------------------------
    async def documents_of(self, source_id: str) -> dict[str, KnowledgeDocument]:
        """来源下的全部文档，按 ``external_id`` 索引。"""
        result = await self.session.execute(
            select(KnowledgeDocument).where(KnowledgeDocument.source_id == source_id)
        )
        return {row.external_id: row for row in result.scalars().all()}

    async def upsert_document(
        self,
        *,
        source_id: str,
        external_id: str,
        title: str,
        content_hash: str,
        url: str | None = None,
        version: str | None = None,
        fetched_at: datetime | None = None,
        meta: dict[str, Any] | None = None,
    ) -> KnowledgeDocument:
        """按 ``(source_id, external_id)`` 写入或更新一篇文档，返回该行。"""
        result = await self.session.execute(
            select(KnowledgeDocument).where(
                KnowledgeDocument.source_id == source_id,
                KnowledgeDocument.external_id == external_id,
            )
        )
        row = result.scalar_one_or_none()
        if row is None:
            row = KnowledgeDocument(
                source_id=source_id,
                external_id=external_id,
                title=title,
                url=url,
                content_hash=content_hash,
                version=version,
                fetched_at=fetched_at,
                meta=dict(meta or {}),
            )
            self.session.add(row)
        else:
            row.title = title
            row.url = url
            row.content_hash = content_hash
            row.version = version
            row.fetched_at = fetched_at
            row.meta = dict(meta or {})
            row.updated_at = utcnow()
        await self.session.flush()
        return row

    async def replace_chunks(
        self, document_id: str, drafts: Sequence["ChunkDraft"]
    ) -> int:
        """整体替换一篇文档的分块（先删后插），返回写入块数。"""
        await self.session.execute(
            delete(KnowledgeChunk).where(KnowledgeChunk.document_id == document_id)
        )
        chunks = [
            KnowledgeChunk(
                document_id=document_id,
                ord=draft.ord,
                heading=draft.heading,
                text=draft.text,
                char_count=len(draft.text),
            )
            for draft in drafts
        ]
        self.session.add_all(chunks)
        await self.session.flush()
        return len(chunks)

    async def delete_documents(self, ids: Sequence[str]) -> int:
        """删除文档（分块与 FTS 行随外键级联），返回删除的文档数。"""
        if not ids:
            return 0
        result = await self.session.execute(
            delete(KnowledgeDocument)
            .where(KnowledgeDocument.id.in_(list(ids)))
            .execution_options(synchronize_session=False)
        )
        return int(result.rowcount or 0)

    async def touch_documents(
        self,
        ids: Sequence[str],
        fetched_at: datetime | None,
        version: str | None,
    ) -> None:
        """只刷新「取到了、内容没变」的元数据，不动分块。"""
        if not ids:
            return
        await self.session.execute(
            update(KnowledgeDocument)
            .where(KnowledgeDocument.id.in_(list(ids)))
            .values(fetched_at=fetched_at, version=version, updated_at=utcnow())
            .execution_options(synchronize_session=False)
        )

    # -- 分块与向量 -----------------------------------------------------
    async def chunks_missing_vectors(
        self, model: str, limit: int
    ) -> list[tuple[int, str, str | None, str]]:
        """当前模型还没有向量的分块：``(id, 文档标题, heading, text)``。"""
        statement = (
            select(
                KnowledgeChunk.id,
                KnowledgeDocument.title,
                KnowledgeChunk.heading,
                KnowledgeChunk.text,
            )
            .join(KnowledgeDocument, KnowledgeDocument.id == KnowledgeChunk.document_id)
            .where(
                or_(
                    KnowledgeChunk.embedding_model.is_(None),
                    KnowledgeChunk.embedding_model != model,
                )
            )
            .order_by(KnowledgeChunk.id.asc())
            .limit(int(limit))
        )
        result = await self.session.execute(statement)
        return [
            (int(chunk_id), str(title), heading, str(text))
            for chunk_id, title, heading, text in result.all()
        ]

    async def write_vectors(
        self,
        rows: Sequence[tuple[int, bytes]],
        *,
        model: str,
        dim: int,
    ) -> None:
        """批量写回向量（``rows`` 是 ``(chunk_id, blob)``）。"""
        if not rows:
            return
        # 逐行 UPDATE：SQLAlchemy 的 ORM 批量 UPDATE 走主键特殊路径，BLOB 用
        # executemany 时会要求主键参数名是 ``id`` 却又与列名冲突；分块是 64 行一批，
        # 逐行写更直白，也没有身份映射同步问题。
        for chunk_id, blob in rows:
            await self.session.execute(
                update(KnowledgeChunk)
                .where(KnowledgeChunk.id == int(chunk_id))
                .values(
                    embedding=blob,
                    embedding_model=model,
                    embedding_dim=int(dim),
                )
                .execution_options(synchronize_session=False)
            )

    async def vectors_for_model(
        self, model: str
    ) -> list[tuple[int, str, bytes]]:
        """当前模型的全部分块向量：``(chunk_id, source_id, blob)``，按 id 升序。"""
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
        result = await self.session.execute(statement)
        return [
            (int(chunk_id), str(source_id), bytes(blob))
            for chunk_id, source_id, blob in result.all()
            if blob is not None
        ]

    async def chunk_rows(self, ids: Sequence[int]) -> list[ChunkRow]:
        """按分块 id 取拼结果所需的字段（JOIN 文档与来源）。"""
        if not ids:
            return []
        statement = (
            select(
                KnowledgeChunk.id,
                KnowledgeChunk.document_id,
                KnowledgeDocument.source_id,
                KnowledgeSource.title,
                KnowledgeSource.trusted,
                KnowledgeDocument.title,
                KnowledgeChunk.heading,
                KnowledgeDocument.url,
                KnowledgeDocument.version,
                KnowledgeDocument.fetched_at,
                KnowledgeSource.license,
                KnowledgeChunk.text,
            )
            .join(KnowledgeDocument, KnowledgeDocument.id == KnowledgeChunk.document_id)
            .join(KnowledgeSource, KnowledgeSource.id == KnowledgeDocument.source_id)
            .where(KnowledgeChunk.id.in_(list(ids)))
        )
        result = await self.session.execute(statement)
        return [
            ChunkRow(
                chunk_id=int(chunk_id),
                document_id=str(document_id),
                source_id=str(source_id),
                source_title=str(source_title),
                trusted=bool(trusted),
                title=str(title),
                heading=heading,
                url=url,
                version=version,
                fetched_at=fetched_at,
                license=license,
                text=str(text),
            )
            for (
                chunk_id,
                document_id,
                source_id,
                source_title,
                trusted,
                title,
                heading,
                url,
                version,
                fetched_at,
                license,
                text,
            ) in result.all()
        ]

    async def counts(self, source_id: str, model: str) -> tuple[int, int, int]:
        """``(文档数, 分块数, 当前模型向量数)``。"""
        doc_count = await self.session.scalar(
            select(func.count())
            .select_from(KnowledgeDocument)
            .where(KnowledgeDocument.source_id == source_id)
        )
        chunk_count = await self.session.scalar(
            select(func.count())
            .select_from(KnowledgeChunk)
            .join(KnowledgeDocument, KnowledgeDocument.id == KnowledgeChunk.document_id)
            .where(KnowledgeDocument.source_id == source_id)
        )
        vector_count = await self.session.scalar(
            select(func.count())
            .select_from(KnowledgeChunk)
            .join(KnowledgeDocument, KnowledgeDocument.id == KnowledgeChunk.document_id)
            .where(
                KnowledgeDocument.source_id == source_id,
                KnowledgeChunk.embedding_model == model,
                KnowledgeChunk.embedding.is_not(None),
            )
        )
        return int(doc_count or 0), int(chunk_count or 0), int(vector_count or 0)

    async def reset_indexing(self) -> int:
        """把残留的 ``indexing`` 改回 ``idle``（进程上次同步中途退出）。"""
        result = await self.session.execute(
            update(KnowledgeSource)
            .where(KnowledgeSource.status == "indexing")
            .values(status="idle", updated_at=utcnow())
            .execution_options(synchronize_session=False)
        )
        return int(result.rowcount or 0)
