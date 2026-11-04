"""M79 T3：历史索引与两个推送式检索来源（spec §4.5、§6、07 §6）。

覆盖 spec §6 的 ``test_finished_ticket_is_searchable``（``external_id`` 为
``ticket:<id>``，FTS 能按关卡代号命中），并补三种文档、redact、重试、启动扫描、
来源适配器与记忆索引清理。

异步用例沿用仓库约定：同步测试函数 + ``asyncio.run(scenario())``。
"""

from __future__ import annotations

import asyncio
from datetime import datetime
from types import SimpleNamespace
from typing import Any
from uuid import uuid4

from maa_api.agent.knowledge.index.embeddings import NullEmbeddingProvider
from maa_api.agent.knowledge.index.indexer import KnowledgeIndexService
from maa_api.agent.knowledge.index.retriever import Retriever
from maa_api.agent.memory.history_index import (
    INDEX_ATTEMPTS,
    MAX_INCIDENT_CHARS,
    HistoryIndexer,
)
from maa_api.agent.memory.service import MemoryService
from maa_api.agent.memory.sources import build_memory_sources
from maa_api.db.models import (
    AgentMessage,
    AgentSession,
    CompactionCheckpoint,
    Mandate,
    Ticket,
    TicketReport,
)
from maa_api.db.repositories.knowledge_index import KnowledgeIndexRepository
from maa_api.db.repositories.memory import MemoryRepository
from maa_api.domain.enums import KnowledgeSourceKind
from tests.db.conftest import (  # noqa: F401  # pytest 从模块命名空间发现 fixture
    db_engine,
    db_path,
    db_session,
    db_session_factory,
)

NOW = datetime(2026, 11, 5, 4, 0, 0)


class _Settings:
    """``rag.*`` 与 ``memory.*`` 的最小设置桩。"""

    def __init__(self, *, require_review: bool = False) -> None:
        self.memory = SimpleNamespace(
            enabled=True,
            require_review=require_review,
            max_injection_tokens=1500,
        )
        self.rag = SimpleNamespace(
            enabled=True,
            top_k=8,
            embedding_provider="none",
            chunk_chars=200,
            chunk_overlap=20,
        )


class _NullResolver:
    """EmbeddingResolver 桩：``none`` 提供者，不补向量。"""

    def __init__(self) -> None:
        self.provider = NullEmbeddingProvider()

    async def current(self) -> Any:
        return self.provider

    def status(self) -> Any:
        return SimpleNamespace(
            provider="none", model="none", status="disabled", error=None
        )


class _FakeIndex:
    """记录 push 调用；可配置前 N 次失败。"""

    def __init__(self, *, failures: int = 0) -> None:
        self.failures = failures
        self.calls: list[tuple[str, list[Any]]] = []
        self.documents: dict[str, dict[str, Any]] = {}

    async def push_documents(self, source_id: str, documents: list[Any]) -> Any:
        self.calls.append((source_id, list(documents)))
        if len(self.calls) <= self.failures:
            raise RuntimeError("索引写入失败")
        bucket = self.documents.setdefault(source_id, {})
        for document in documents:
            bucket[document.external_id] = document
        return SimpleNamespace(source_id=source_id, fetched=len(documents))


def _make_index(factory: Any, settings: _Settings) -> KnowledgeIndexService:
    index = KnowledgeIndexService(
        factory,
        settings_provider=lambda: settings,
        embeddings=_NullResolver(),
    )
    for source in build_memory_sources():
        index.register(source)
    return index


def _make_memory_service(
    factory: Any, settings: _Settings, index: KnowledgeIndexService
) -> MemoryService:
    return MemoryService(
        factory,
        index_writer=lambda docs: index.push_documents("memory", docs),
        index_remover=lambda ids: index.remove_documents("memory", ids),
        settings_provider=lambda: settings,
        clock=lambda: NOW,
    )


async def _seed_ticket(
    factory: Any,
    *,
    title: str = "1-7 刷固源岩",
    result: dict[str, Any] | None = None,
) -> str:
    ticket_id = str(uuid4())
    async with factory() as session:
        session.add(
            Ticket(
                id=ticket_id,
                title=title,
                kind="daily_ops",
                status="done",
                origin="user",
                result=result,
                created_at=NOW,
                updated_at=NOW,
            )
        )
        await session.flush()
        session.add(
            Mandate(
                ticket_id=ticket_id,
                scope="只刷 1-7，别用源石",
                strength="explicit",
                source="user",
                risk_ceiling="none",
                created_at=NOW,
            )
        )
        session.add(
            TicketReport(
                ticket_id=ticket_id,
                status="success",
                summary="1-7 三次都成功",
                created_at=NOW,
            )
        )
        await session.commit()
    return ticket_id


async def _seed_session(
    factory: Any, *, kind: str = "incident", title: str = "崩溃诊断"
) -> str:
    session_id = str(uuid4())
    async with factory() as session:
        session.add(
            AgentSession(
                id=session_id,
                kind=kind,
                title=title,
                model="test-model",
                created_at=NOW,
                updated_at=NOW,
            )
        )
        await session.commit()
    return session_id


async def _seed_message(
    factory: Any, session_id: str, *, seq: int, role: str, content: str
) -> None:
    async with factory() as session:
        session.add(
            AgentMessage(
                session_id=session_id,
                seq=seq,
                role=role,
                content=content,
                created_at=NOW,
            )
        )
        await session.commit()


async def _seed_checkpoint(
    factory: Any, *, session_id: str, summary: str
) -> str:
    checkpoint_id = str(uuid4())
    async with factory() as session:
        session.add(
            CompactionCheckpoint(
                id=checkpoint_id,
                session_id=session_id,
                upto_seq=10,
                summary=summary,
                summary_json={"schema_version": 1},
                durable={},
                tokens_before=1000,
                tokens_after=100,
                trigger="auto",
                created_at=NOW,
            )
        )
        await session.commit()
    return checkpoint_id


# ---------------------------------------------------------------------------
# §6 一条
# ---------------------------------------------------------------------------
def test_finished_ticket_is_searchable(db_session_factory) -> None:
    """终态工单写 ``ticket:<id>``，FTS 能按关卡代号查到这篇文档。"""
    factory = db_session_factory

    async def scenario() -> None:
        settings = _Settings()
        index = _make_index(factory, settings)
        indexer = HistoryIndexer(factory, index_service=index)
        try:
            ticket_id = await _seed_ticket(factory)
            assert await indexer.on_ticket_ended(
                SimpleNamespace(ticket_id=ticket_id)
            )
            async with factory() as session:
                documents = await KnowledgeIndexRepository(session).documents_of(
                    "history"
                )
            assert f"ticket:{ticket_id}" in documents

            retriever = Retriever(
                factory, index=index, settings_provider=lambda: settings
            )
            result = await retriever.search("1-7", sources=["history"])
            assert result.hits
            assert any("1-7" in hit.text for hit in result.hits)
            assert all(hit.source_id == "history" for hit in result.hits)
        finally:
            await index.close()

    asyncio.run(scenario())


# ---------------------------------------------------------------------------
# 三种文档、redact、版本
# ---------------------------------------------------------------------------
def test_three_document_types_and_redaction(db_session_factory) -> None:
    """ticket/checkpoint/incident 三种正文与 external_id；写入前遮掉凭据。"""
    factory = db_session_factory

    async def scenario() -> None:
        index = _FakeIndex()
        indexer = HistoryIndexer(factory, index_service=index)
        ticket_id = await _seed_ticket(
            factory, result={"api_key": "sk-very-secret", "stage": "1-7"}
        )
        assert await indexer.index_ticket(ticket_id)
        ticket_doc = index.documents["history"][f"ticket:{ticket_id}"]
        assert "1-7 刷固源岩" in ticket_doc.text
        assert "终态：done" in ticket_doc.text
        assert "1-7 三次都成功" in ticket_doc.text
        assert "sk-very-secret" not in ticket_doc.text
        assert "[REDACTED]" in ticket_doc.text
        assert ticket_doc.version == NOW.isoformat()

        incident_session = await _seed_session(factory, title="MaaCore 崩溃")
        await _seed_message(
            factory,
            incident_session,
            seq=0,
            role="user",
            content="为什么崩溃",
        )
        # 最后一条助手回答超过 4000 字：正文仍按上限截断。
        await _seed_message(
            factory,
            incident_session,
            seq=1,
            role="assistant",
            content="诊断结论：" + "x" * 5000,
        )
        assert await indexer.index_incidents() == 1
        incident_doc = index.documents["history"][f"incident:{incident_session}"]
        assert len(incident_doc.text) <= MAX_INCIDENT_CHARS
        assert "MaaCore 崩溃" in incident_doc.text
        assert "诊断结论" in incident_doc.text

        checkpoint_session = await _seed_session(
            factory, kind="orchestrator", title="总控会话"
        )
        checkpoint_id = await _seed_checkpoint(
            factory, session_id=checkpoint_session, summary="检查点摘要正文"
        )
        assert await indexer.index_checkpoint(checkpoint_id)
        checkpoint_doc = index.documents["history"][
            f"checkpoint:{checkpoint_id}"
        ]
        assert checkpoint_doc.text == "检查点摘要正文"
        assert checkpoint_doc.version == NOW.isoformat()

        # 每次 push 都写 history 来源。
        assert {source_id for source_id, _ in index.calls} == {"history"}

    asyncio.run(scenario())


def test_incident_scan_only_touches_incident_sessions(db_session_factory) -> None:
    """启动扫描只取 ``kind=incident`` 的 v2 诊断会话。"""
    factory = db_session_factory

    async def scenario() -> None:
        index = _FakeIndex()
        indexer = HistoryIndexer(factory, index_service=index)
        incident = await _seed_session(factory, kind="incident")
        await _seed_message(
            factory, incident, seq=0, role="assistant", content="诊断回答"
        )
        chat = await _seed_session(factory, kind="chat", title="普通会话")
        await _seed_message(factory, chat, seq=0, role="assistant", content="闲聊")
        assert await indexer.index_incidents() == 1
        assert set(index.documents["history"]) == {f"incident:{incident}"}

    asyncio.run(scenario())


def test_index_failure_retries_three_times(db_session_factory, caplog) -> None:
    """失败重试三次；前两次失败第三次成功，全失败只记日志不抛。"""
    factory = db_session_factory

    async def scenario() -> None:
        ticket_id = await _seed_ticket(factory)

        flaky = _FakeIndex(failures=2)
        assert await HistoryIndexer(factory, index_service=flaky).index_ticket(
            ticket_id
        )
        assert len(flaky.calls) == INDEX_ATTEMPTS
        assert f"ticket:{ticket_id}" in flaky.documents["history"]

        broken = _FakeIndex(failures=99)
        assert not await HistoryIndexer(
            factory, index_service=broken
        ).index_ticket(ticket_id)
        assert len(broken.calls) == INDEX_ATTEMPTS

    asyncio.run(scenario())
    assert "历史索引写入失败" in caplog.text


# ---------------------------------------------------------------------------
# 来源适配器与记忆索引清理
# ---------------------------------------------------------------------------
def test_push_only_sources_and_memory_index_cleanup(db_session_factory) -> None:
    """history/memory 来源默认不可检索；记忆可信（07 §2.5）；写入/清理走 ``memory:<id>``。"""
    factory = db_session_factory

    async def scenario() -> None:
        history_source, memory_source = build_memory_sources()
        for source in (history_source, memory_source):
            assert source.searchable_by_default is False
            assert source.scheduled_refresh is False
            assert source.full_listing is False
            assert await source.list_refs({}) == []
            assert await source.fetch([], {}) == []
        # 历史混有工具输出与外部转述，不可信；记忆是用户审核过的，可信（07 §2.5）。
        assert history_source.trusted is False
        assert memory_source.trusted is True
        assert history_source.kind is KnowledgeSourceKind.HISTORY
        assert memory_source.kind is KnowledgeSourceKind.MEMORY
        assert history_source.source_id == "history"
        assert memory_source.source_id == "memory"

        settings = _Settings(require_review=True)
        index = _make_index(factory, settings)
        service = _make_memory_service(factory, settings, index)
        try:
            old = await service.create_candidate(
                kind="semantic", subject="preference", content="旧偏好：手动换班"
            )
            assert old is not None
            await service.review(old.id, True)
            async with factory() as session:
                documents = await KnowledgeIndexRepository(session).documents_of(
                    "memory"
                )
            assert f"memory:{old.id}" in documents

            retriever = Retriever(
                factory, index=index, settings_provider=lambda: settings
            )
            result = await retriever.search("手动换班", sources=["memory"])
            assert result.hits

            replacement = await service.create_candidate(
                kind="semantic",
                subject="preference",
                content="新偏好：自动换班",
                supersedes_id=old.id,
                allowed_supersedes={old.id},
            )
            assert replacement is not None
            await service.review(replacement.id, True)
            async with factory() as session:
                documents = await KnowledgeIndexRepository(session).documents_of(
                    "memory"
                )
            assert f"memory:{old.id}" not in documents
            assert f"memory:{replacement.id}" in documents
            async with factory() as session:
                stored = await MemoryRepository(session).get(old.id)
            assert stored is not None

            await service.delete_memory(replacement.id)
            async with factory() as session:
                documents = await KnowledgeIndexRepository(session).documents_of(
                    "memory"
                )
            assert documents == {}
        finally:
            await index.close()

    asyncio.run(scenario())
