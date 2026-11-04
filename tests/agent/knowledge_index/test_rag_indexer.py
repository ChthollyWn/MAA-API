"""M76 T5：索引服务与内置来源（spec §4.2、§4.5、§6）。

用例全部走临时 SQLite 库（``tests/db/conftest.py`` 的 ``db_session_factory``）；
后台任务由服务自己持有，测试用 :func:`_drain` 等它们结束，最后 ``close()``。
"""

from __future__ import annotations

import asyncio
import time
from collections.abc import Mapping, Sequence
from datetime import datetime
from types import SimpleNamespace
from typing import Any

import numpy as np
from sqlalchemy import select, update

from maa_api.agent.knowledge import KnowledgeService, KnowledgeWrite
from maa_api.agent.knowledge.index.entries import KnowledgeEntrySource
from maa_api.agent.knowledge.index.indexer import (
    DocumentRef,
    KnowledgeIndexService,
    SourceDocument,
)
from maa_api.db.models import KnowledgeChunk, KnowledgeDocument, KnowledgeSource
from maa_api.domain.enums import KnowledgeSourceKind
from maa_api.domain.errors import AppError, ErrorCode

FETCHED_AT = datetime(2026, 1, 1, 12, 0, 0)


class _Settings:
    """可改 chunk 参数的 settings 桩（``settings_provider()`` 的返回值）。"""

    def __init__(self, *, chunk_chars: int = 200, chunk_overlap: int = 20) -> None:
        self.rag = SimpleNamespace(
            chunk_chars=chunk_chars, chunk_overlap=chunk_overlap
        )


class _NullProvider:
    name = "none"
    dim = 0

    async def embed(
        self, texts: Sequence[str], *, purpose: str
    ) -> np.ndarray:  # pragma: no cover - 服务在调用前就按 name 短路
        raise AppError(ErrorCode.EMBEDDING_UNAVAILABLE, "向量服务未启用")


class _FakeProvider:
    name = "fake:model"
    dim = 4

    def __init__(self) -> None:
        self.calls = 0
        self.fail = False
        self.on_embed: Any = None

    async def embed(self, texts: Sequence[str], *, purpose: str) -> np.ndarray:
        self.calls += 1
        if self.fail:
            if self.on_embed is not None:
                self.on_embed(False, "向量服务暂时不可用")
            raise AppError(ErrorCode.EMBEDDING_UNAVAILABLE, "向量服务暂时不可用")
        if self.on_embed is not None:
            self.on_embed(True, None)
        return np.tile(
            np.asarray([0.5, 0.5, 0.5, 0.5], dtype=np.float32), (len(texts), 1)
        )


class _FakeResolver:
    """EmbeddingResolver 的桩：``status()`` 跟着 provider 的成败走。"""

    def __init__(self, provider: Any | None = None) -> None:
        self.provider = provider or _NullProvider()
        self._status = SimpleNamespace(
            provider="remote",
            model="fake",
            status="ready",
            error=None,
        )
        self.provider.on_embed = self._note

    async def current(self) -> Any:
        return self.provider

    def status(self) -> Any:
        return self._status

    def _note(self, ok: bool, message: str | None) -> None:
        self._status = SimpleNamespace(
            provider="remote",
            model="fake",
            status="ready" if ok else "unavailable",
            error=None if ok else message,
        )


class _FakeSource:
    """可控的语料来源：清单、正文、失败与闸门都在测试里设置。"""

    source_id = "fake"
    kind = KnowledgeSourceKind.GAMEDATA
    title = "测试来源"
    trusted = False
    license = None
    searchable_by_default = True
    setting_key = None
    full_listing = True

    def __init__(self) -> None:
        self.documents: list[SourceDocument] = []
        self.fail = False
        self.refs_calls = 0
        self.gate: asyncio.Event | None = None
        self.started: asyncio.Event | None = None

    def validate_config(self, config: Mapping[str, Any]) -> dict[str, Any]:
        return dict(config)

    async def list_refs(
        self, config: Mapping[str, Any]
    ) -> list[DocumentRef]:
        self.refs_calls += 1
        if self.gate is not None:
            if self.started is not None:
                self.started.set()
            await self.gate.wait()
        if self.fail:
            raise RuntimeError("来源读取失败")
        return [
            DocumentRef(
                external_id=doc.external_id, title=doc.title, version=doc.version
            )
            for doc in self.documents
        ]

    async def fetch(
        self, refs: Sequence[DocumentRef], config: Mapping[str, Any]
    ) -> list[SourceDocument]:
        wanted = {ref.external_id for ref in refs}
        return [doc for doc in self.documents if doc.external_id in wanted]


def _document(
    external_id: str,
    text: str,
    *,
    title: str = "文档",
    version: str = "v1",
) -> SourceDocument:
    return SourceDocument(
        external_id=external_id,
        title=title,
        text=text,
        version=version,
        fetched_at=FETCHED_AT,
    )


def _make_service(
    factory,
    *,
    sources: Sequence[Any] = (),
    embeddings: Any | None = None,
    settings: _Settings | None = None,
) -> tuple[KnowledgeIndexService, _Settings]:
    holder = settings or _Settings()
    service = KnowledgeIndexService(
        factory,
        settings_provider=lambda: holder,
        embeddings=embeddings or _FakeResolver(),
    )
    for source in sources:
        service.register(source)
    return service, holder


async def _drain(service: KnowledgeIndexService) -> None:
    """等所有后台任务结束（含任务里再排的任务）。"""
    for _ in range(500):
        tasks = [task for task in service._tasks if not task.done()]
        if not tasks:
            return
        await asyncio.wait_for(
            asyncio.gather(*tasks, return_exceptions=True), timeout=10
        )
    raise AssertionError("后台任务没有结束")


async def _wait_until(predicate, *, timeout: float = 20.0) -> None:
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if await predicate():
            return
        await asyncio.sleep(0.01)
    raise AssertionError("等待的条件没有在超时前成立")


async def _source_row(factory, source_id: str) -> KnowledgeSource:
    async with factory() as session:
        row = await session.get(KnowledgeSource, source_id)
        assert row is not None
        return row


async def _documents(factory, source_id: str = "fake") -> dict[str, Any]:
    async with factory() as session:
        result = await session.execute(
            select(KnowledgeDocument).where(KnowledgeDocument.source_id == source_id)
        )
        return {row.external_id: row for row in result.scalars().all()}


async def _chunks(factory) -> list[tuple[int, str, str, bytes | None]]:
    async with factory() as session:
        result = await session.execute(
            select(KnowledgeChunk).order_by(KnowledgeChunk.id.asc())
        )
        return [
            (
                int(row.id or 0),
                row.document_id,
                row.text,
                bytes(row.embedding) if row.embedding is not None else None,
            )
            for row in result.scalars().all()
        ]


async def _clear_vectors(factory) -> None:
    async with factory() as session:
        await session.execute(
            update(KnowledgeChunk)
            .values(embedding=None, embedding_model=None, embedding_dim=None)
            .execution_options(synchronize_session=False)
        )
        await session.commit()


# ---------------------------------------------------------------------------
# 用例
# ---------------------------------------------------------------------------
def test_start_ensures_rows_and_resets_indexing(db_session_factory) -> None:
    async def scenario() -> None:
        fake = _FakeSource()
        other = _FakeSource()
        other.source_id = "other"
        other.title = "其他来源"
        service, _ = _make_service(db_session_factory, sources=[fake, other])

        # 上一进程在同步中途退出留下的行：旧标题、用户改过的开关与配置、indexing。
        async with db_session_factory() as session:
            session.add(
                KnowledgeSource(
                    id="fake",
                    kind="gamedata",
                    title="旧标题",
                    enabled=False,
                    config={"page": 2},
                    status="indexing",
                )
            )
            await session.commit()

        try:
            await service.start()
            await _drain(service)
        finally:
            await service.close()

        row = await _source_row(db_session_factory, "fake")
        assert row.title == "测试来源"
        assert row.kind == "gamedata"
        assert row.status == "idle"
        assert row.enabled is False  # 用户设置不被同步覆盖
        assert row.config == {"page": 2}
        assert row.error is None

        other_row = await _source_row(db_session_factory, "other")
        assert other_row.title == "其他来源"
        assert other_row.status == "idle"
        assert other_row.enabled is True
        assert other_row.config == {}

        sources = [
            item.id
            for item in (
                await _source_rows(db_session_factory)
            )
        ]
        assert sources == ["fake", "other"]

    asyncio.run(scenario())


async def _source_rows(factory) -> list[KnowledgeSource]:
    async with factory() as session:
        result = await session.execute(
            select(KnowledgeSource).order_by(KnowledgeSource.id.asc())
        )
        return list(result.scalars().all())


def test_incremental_rebuild_rules(db_session_factory) -> None:
    async def scenario() -> None:
        knowledge = KnowledgeService(db_session_factory, builtins={})
        provider = _FakeProvider()
        service, settings = _make_service(
            db_session_factory,
            sources=[
                KnowledgeEntrySource(knowledge, "builtin"),
                KnowledgeEntrySource(knowledge, "user"),
            ],
            embeddings=_FakeResolver(provider),
        )
        await knowledge.put_entry(
            "entry-a",
            KnowledgeWrite(
                kind="playbook", title="条目 A", when_to_use="场景 A", body="正文 A"
            ),
        )
        await knowledge.put_entry(
            "entry-b",
            KnowledgeWrite(
                kind="playbook", title="条目 B", when_to_use="场景 B", body="正文 B"
            ),
        )
        try:
            first = await service.sync_source("user")
            assert first.rebuilt == 2
            await _drain(service)  # 等向量补完
            chunks_before = await _chunks(db_session_factory)
            documents_before = await _documents(db_session_factory, "user")
            assert set(documents_before) == {"entry-a", "entry-b"}
            assert len(chunks_before) == 2
            assert all(chunk[3] is not None for chunk in chunks_before)

            second = await service.sync_source("user")
            assert second.rebuilt == 0
            assert second.fetched == 0  # 版本与分块签名都没变，整篇不取正文
            assert await _chunks(db_session_factory) == chunks_before

            # 只改一条：只有那篇重建，另一篇的分块 id 与向量原样不动。
            await knowledge.put_entry(
                "entry-b",
                KnowledgeWrite(
                    kind="playbook",
                    title="条目 B",
                    when_to_use="场景 B",
                    body="正文 B 改",
                ),
            )
            third = await service.sync_source("user")
            assert third.fetched == 1  # 只有版本变了的 entry-b 被取正文
            assert third.rebuilt == 1
            documents_after = await _documents(db_session_factory, "user")
            chunks_after = await _chunks(db_session_factory)
            unchanged_ids = {
                chunk[0]
                for chunk in chunks_after
                if chunk[1] == documents_before["entry-a"].id
            }
            before_ids = {
                chunk[0]
                for chunk in chunks_before
                if chunk[1] == documents_before["entry-a"].id
            }
            assert unchanged_ids == before_ids
            assert documents_after["entry-b"].id == documents_before["entry-b"].id

            # 分块参数变了：签名不同，全部重建。
            settings.rag.chunk_chars = 300
            fourth = await service.sync_source("user")
            assert fourth.rebuilt == 2
            assert fourth.unchanged == 0
        finally:
            await service.close()

    asyncio.run(scenario())


def test_disabled_and_fulltext_entries_are_not_indexed(db_session_factory) -> None:
    async def scenario() -> None:
        knowledge = KnowledgeService(db_session_factory, builtins={})
        user_source = KnowledgeEntrySource(knowledge, "user")
        service, _ = _make_service(db_session_factory, sources=[user_source])
        await knowledge.put_entry(
            "keep",
            KnowledgeWrite(
                kind="playbook", title="保留", when_to_use="场景", body="正文"
            ),
        )
        await knowledge.put_entry(
            "disabled",
            KnowledgeWrite(
                kind="playbook",
                title="停用",
                when_to_use="场景",
                body="正文",
                enabled=False,
            ),
        )
        await knowledge.put_entry(
            "glossary-entry",
            KnowledgeWrite(
                kind="glossary", title="术语", when_to_use="场景", body="正文"
            ),
        )
        await knowledge.put_entry(
            "profile-entry",
            KnowledgeWrite(
                kind="profile", title="档案", when_to_use="场景", body="正文"
            ),
        )
        try:
            refs = await user_source.list_refs({})
            assert [ref.external_id for ref in refs] == ["keep"]
            summary = await service.sync_source("user")
            assert summary.rebuilt == 1
            await _drain(service)
            documents = await _documents(db_session_factory, "user")
            assert set(documents) == {"keep"}
        finally:
            await service.close()

    asyncio.run(scenario())


def test_knowledge_change_listener_triggers_sync(db_session_factory) -> None:
    async def scenario() -> None:
        knowledge = KnowledgeService(db_session_factory, builtins={})
        service, _ = _make_service(
            db_session_factory,
            sources=[
                KnowledgeEntrySource(knowledge, "builtin"),
                KnowledgeEntrySource(knowledge, "user"),
            ],
        )
        knowledge.add_change_listener(service.on_knowledge_changed)
        try:
            await service.start()
            await _drain(service)
            assert await _documents(db_session_factory, "user") == {}

            await knowledge.put_entry(
                "new-entry",
                KnowledgeWrite(
                    kind="rule",
                    title="新条目",
                    when_to_use="用户问起时",
                    body="新增的正文内容",
                ),
            )
            await _drain(service)

            async def indexed() -> bool:
                documents = await _documents(db_session_factory, "user")
                return "new-entry" in documents

            await _wait_until(indexed)
            documents = await _documents(db_session_factory, "user")
            assert documents["new-entry"].title == "新条目"
            chunks = await _chunks(db_session_factory)
            assert any("新增的正文内容" in chunk[2] for chunk in chunks)
        finally:
            await service.close()

    asyncio.run(scenario())


def test_busy_source_marks_dirty_and_resyncs(db_session_factory) -> None:
    async def scenario() -> None:
        source = _FakeSource()
        source.documents = [_document("doc-1", "第一版正文")]
        service, _ = _make_service(db_session_factory, sources=[source])
        source.gate = asyncio.Event()
        source.started = asyncio.Event()
        task = asyncio.create_task(service.sync_source("fake"))
        try:
            await asyncio.wait_for(source.started.wait(), timeout=5)
            assert service.is_busy("fake") is True
            assert service.request_sync("fake") is False
            source.gate.set()
            summary = await task
            assert summary.rebuilt == 1
            await _drain(service)
            # 第一次 + 脏标记补跑一次；最终文档只有一份，没有重复。
            assert source.refs_calls == 2
            assert service.is_busy("fake") is False
            assert set(await _documents(db_session_factory)) == {"doc-1"}
        finally:
            source.gate.set()
            await service.close()

    asyncio.run(scenario())


def test_ensure_embeddings_fills_missing_and_stops_on_unavailable(
    db_session_factory,
) -> None:
    async def scenario() -> None:
        source = _FakeSource()
        source.documents = [
            _document("doc-1", "第一篇正文"),
            _document("doc-2", "第二篇正文"),
        ]
        provider = _FakeProvider()
        resolver = _FakeResolver(provider)
        service, _ = _make_service(
            db_session_factory, sources=[source], embeddings=resolver
        )
        try:
            await service.sync_source("fake")
            await _drain(service)
            assert all(chunk[3] is not None for chunk in await _chunks(db_session_factory))

            await _clear_vectors(db_session_factory)
            filled = await service.ensure_embeddings()
            assert filled == 2
            chunks = await _chunks(db_session_factory)
            assert all(chunk[3] is not None for chunk in chunks)
            assert all(len(chunk[3] or b"") == 16 for chunk in chunks)

            # provider 报不可用：停下并进入冷却，第二次调用不再打 provider。
            await _drain(service)
            await _clear_vectors(db_session_factory)
            provider.fail = True
            assert await service.ensure_embeddings() == 0
            assert resolver.status().status == "unavailable"
            calls = provider.calls
            assert await service.ensure_embeddings() == 0
            assert provider.calls == calls
            assert all(chunk[3] is None for chunk in await _chunks(db_session_factory))
        finally:
            await service.close()

    asyncio.run(scenario())


def test_sync_failure_marks_error_and_keeps_old_documents(
    db_session_factory,
) -> None:
    async def scenario() -> None:
        source = _FakeSource()
        source.documents = [_document("doc-1", "第一版正文")]
        service, _ = _make_service(db_session_factory, sources=[source])
        try:
            first = await service.sync_source("fake")
            assert first.rebuilt == 1
            await _drain(service)
            documents = await _documents(db_session_factory)
            chunks = await _chunks(db_session_factory)
            assert set(documents) == {"doc-1"}

            source.fail = True
            failed = await service.sync_source("fake")
            assert failed.rebuilt == 0
            await _drain(service)
            row = await _source_row(db_session_factory, "fake")
            assert row.status == "error"
            assert row.error is not None and "来源读取失败" in row.error
            # 旧文档与分块都还在。
            assert set(await _documents(db_session_factory)) == {"doc-1"}
            assert await _chunks(db_session_factory) == chunks

            source.fail = False
            recovered = await service.sync_source("fake")
            assert recovered.rebuilt == 0
            assert recovered.fetched == 0
            row = await _source_row(db_session_factory, "fake")
            assert row.status == "idle"
            assert row.error is None
            assert row.last_indexed_at is not None
        finally:
            await service.close()

    asyncio.run(scenario())
