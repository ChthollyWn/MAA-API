"""M77 T4：来源协议扩展与定时刷新（spec §4.2、§4.5、§6）。

覆盖四件事：``refresh_all`` 跳过关闭的来源、单错继续、最后补向量与 single-flight；
``push_documents`` 等同步锁且只写不删；``prune`` 只在同步成功后调用并计入
``SyncSummary.deleted``。用例走临时 SQLite 库（``tests/db/conftest.py`` 的
``db_session_factory``），异步场景统一包在 ``asyncio.run`` 里（本仓没有
pytest-asyncio）。
"""

from __future__ import annotations

import asyncio
from collections.abc import Mapping, Sequence
from datetime import datetime
from types import SimpleNamespace
from typing import Any

import numpy as np
from sqlalchemy import select

from maa_api.agent.knowledge.index.indexer import (
    DocumentRef,
    KnowledgeIndexService,
    SourceDocument,
)
from maa_api.db.models import KnowledgeChunk, KnowledgeDocument, KnowledgeSource
from maa_api.domain.enums import KnowledgeSourceKind

FETCHED_AT = datetime(2026, 1, 1, 12, 0, 0)
FETCHED_AT_LATER = datetime(2026, 1, 2, 12, 0, 0)


class _Settings:
    """可改语料开关的 settings 桩；``**flags`` 直接挂到 ``rag`` 上。"""

    def __init__(self, *, enabled: bool = True, **flags: bool) -> None:
        self.rag = SimpleNamespace(
            enabled=enabled,
            chunk_chars=200,
            chunk_overlap=20,
            **flags,
        )


class _NullProvider:
    name = "none"
    dim = 0

    async def embed(
        self, texts: Sequence[str], *, purpose: str
    ) -> np.ndarray:  # pragma: no cover - 服务在调用前就按 name 短路
        raise AssertionError("provider 为 none 时不应调用 embed")


class _FakeProvider:
    name = "fake:model"
    dim = 4

    async def embed(self, texts: Sequence[str], *, purpose: str) -> np.ndarray:
        return np.tile(
            np.asarray([0.5, 0.5, 0.5, 0.5], dtype=np.float32), (len(texts), 1)
        )


class _Resolver:
    def __init__(self, provider: Any | None = None) -> None:
        self.provider = provider or _NullProvider()

    async def current(self) -> Any:
        return self.provider

    def status(self) -> Any:
        return SimpleNamespace(
            provider="remote", model="fake", status="ready", error=None
        )


class _Source:
    """可控的定时来源：清单、正文、失败、闸门与可选成员都在测试里设置。"""

    kind = KnowledgeSourceKind.GAMEDATA
    title = "测试语料"
    trusted = False
    license = None
    searchable_by_default = True
    full_listing = True
    scheduled_refresh = True

    def __init__(self, source_id: str, *, setting_key: str | None = None) -> None:
        self.source_id = source_id
        self.setting_key = setting_key
        self.documents: list[SourceDocument] = []
        self.fail = False
        self.gate: asyncio.Event | None = None
        self.started: asyncio.Event | None = None
        self.refs_calls = 0
        self.prune_calls = 0
        self.prune_result = 0
        self.prune_configs: list[Mapping[str, Any]] = []
        self.start_calls = 0
        self.close_calls = 0

    def validate_config(self, config: Mapping[str, Any]) -> dict[str, Any]:
        return dict(config)

    async def start(self, index: KnowledgeIndexService) -> None:
        self.start_calls += 1

    async def close(self) -> None:
        self.close_calls += 1

    async def prune(self, config: Mapping[str, Any]) -> int:
        self.prune_calls += 1
        self.prune_configs.append(dict(config))
        return self.prune_result

    async def list_refs(self, config: Mapping[str, Any]) -> list[DocumentRef]:
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
    fetched_at: datetime = FETCHED_AT,
) -> SourceDocument:
    return SourceDocument(
        external_id=external_id,
        title=title,
        text=text,
        version=version,
        fetched_at=fetched_at,
    )


def _make_service(
    factory,
    *,
    sources: Sequence[Any],
    embeddings: Any | None = None,
    settings: _Settings | None = None,
) -> tuple[KnowledgeIndexService, _Settings]:
    holder = settings or _Settings()
    service = KnowledgeIndexService(
        factory,
        settings_provider=lambda: holder,
        embeddings=embeddings or _Resolver(),
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


async def _documents(factory, source_id: str) -> dict[str, KnowledgeDocument]:
    async with factory() as session:
        result = await session.execute(
            select(KnowledgeDocument).where(KnowledgeDocument.source_id == source_id)
        )
        return {row.external_id: row for row in result.scalars().all()}


async def _source_row(factory, source_id: str) -> KnowledgeSource:
    async with factory() as session:
        row = await session.get(KnowledgeSource, source_id)
        assert row is not None
        return row


async def _chunks(factory) -> list[tuple[str, bytes | None]]:
    async with factory() as session:
        result = await session.execute(
            select(KnowledgeChunk).order_by(KnowledgeChunk.id.asc())
        )
        return [
            (
                row.text,
                bytes(row.embedding) if row.embedding is not None else None,
            )
            for row in result.scalars().all()
        ]


# ---------------------------------------------------------------------------
# 用例
# ---------------------------------------------------------------------------
def test_refresh_all_skips_disabled_and_continues_after_error(
    db_session_factory,
) -> None:
    async def scenario() -> None:
        settings = _Settings(
            skip_enabled=False, boom_enabled=True, ok_enabled=True
        )
        skipped = _Source("skip", setting_key="rag.skip_enabled")
        failing = _Source("boom", setting_key="rag.boom_enabled")
        failing.fail = True
        good = _Source("ok", setting_key="rag.ok_enabled")
        good.documents = [_document("doc-1", "可以检索的正文")]
        service, _ = _make_service(
            db_session_factory,
            sources=[skipped, failing, good],
            settings=settings,
        )
        try:
            await service.start()
            await _drain(service)
            # start() 缓存了来源行：开关关闭的来源不排首同步。
            assert service.never_indexed() == ["boom", "ok"]

            events: list[str] = []

            async def ensure() -> int:
                events.append("embeddings")
                return 0

            service.ensure_embeddings = ensure  # type: ignore[method-assign]
            result = await service.refresh_all()

            assert result == {"skip": "skipped", "boom": "error", "ok": "ok"}
            assert skipped.refs_calls == 0  # 设置关闭：一次都不抓
            assert failing.refs_calls == 1  # 出错后不中断
            assert good.refs_calls == 1
            assert set(await _documents(db_session_factory, "ok")) == {"doc-1"}
            assert (await _source_row(db_session_factory, "boom")).status == "error"
            assert (await _source_row(db_session_factory, "ok")).status == "idle"
            # 补向量排在所有来源之后（后台任务的调用不会晚于这一行）。
            assert events and events[-1] == "embeddings"
        finally:
            await service.close()

    asyncio.run(scenario())


def test_refresh_all_is_single_flight(db_session_factory) -> None:
    async def scenario() -> None:
        source = _Source("fake", setting_key="rag.fake_enabled")
        source.documents = [_document("doc-1", "第一篇正文")]
        service, _ = _make_service(
            db_session_factory,
            sources=[source],
            settings=_Settings(fake_enabled=True),
        )
        source.gate = asyncio.Event()
        source.started = asyncio.Event()
        try:
            await service.start()
            await _drain(service)
            task = asyncio.create_task(service.refresh_all())
            await asyncio.wait_for(source.started.wait(), timeout=5)
            # 第一次刷新还卡在来源上：第二次直接返回空，不排第二个同步。
            assert await service.refresh_all() == {}
            assert service.is_busy("fake") is True
            source.gate.set()
            assert await task == {"fake": "ok"}
            # 刷新结束后标志复位，可以再跑一轮。
            source.gate = None
            assert await service.refresh_all() == {"fake": "ok"}
        finally:
            if source.gate is not None:
                source.gate.set()
            await asyncio.wait_for(service.close(), timeout=10)

    asyncio.run(scenario())


def test_push_documents_waits_for_lock_and_never_deletes(
    db_session_factory,
) -> None:
    async def scenario() -> None:
        source = _Source("fake", setting_key="rag.fake_enabled")
        source.documents = [
            _document("doc-1", "第一篇正文"),
            _document("doc-2", "第二篇正文"),
        ]
        service, _ = _make_service(
            db_session_factory,
            sources=[source],
            embeddings=_Resolver(_FakeProvider()),
            settings=_Settings(fake_enabled=True),
        )
        try:
            await service.start()
            await _drain(service)
            first = await service.sync_source("fake")
            assert first.rebuilt == 2
            await _drain(service)

            # 同步进行中推送：推送必须等这把锁，不能插进同步事务。
            source.gate = asyncio.Event()
            source.started = asyncio.Event()
            sync_task = asyncio.create_task(service.sync_source("fake"))
            await asyncio.wait_for(source.started.wait(), timeout=5)
            push_task = asyncio.create_task(
                service.push_documents(
                    "fake", [_document("doc-3", "推送进来的正文")]
                )
            )
            await asyncio.sleep(0.05)
            assert push_task.done() is False
            source.gate.set()
            await sync_task
            pushed = await asyncio.wait_for(push_task, timeout=10)
            assert pushed.rebuilt == 1
            assert pushed.fetched == 1
            assert pushed.deleted == 0
            await _drain(service)

            # 只写不删：同步清单里没有的三份文档都在。
            documents = await _documents(db_session_factory, "fake")
            assert set(documents) == {"doc-1", "doc-2", "doc-3"}
            chunks = await _chunks(db_session_factory)
            assert any("推送进来的正文" in text for text, _ in chunks)
            assert all(embedding is not None for _, embedding in chunks)

            # 正文没变：只刷新抓取时间与版本，不重建分块。
            pushed_again = await service.push_documents(
                "fake",
                [
                    _document(
                        "doc-3",
                        "推送进来的正文",
                        version="v2",
                        fetched_at=FETCHED_AT_LATER,
                    )
                ],
            )
            assert pushed_again.unchanged == 1
            assert pushed_again.rebuilt == 0
            assert pushed_again.deleted == 0
            documents = await _documents(db_session_factory, "fake")
            assert set(documents) == {"doc-1", "doc-2", "doc-3"}
            assert documents["doc-3"].version == "v2"
            assert documents["doc-3"].fetched_at == FETCHED_AT_LATER
        finally:
            source.gate = None
            await asyncio.wait_for(service.close(), timeout=10)

    asyncio.run(scenario())


def test_prune_runs_after_successful_sync_only(db_session_factory) -> None:
    async def scenario() -> None:
        source = _Source("fake", setting_key="rag.fake_enabled")
        source.documents = [
            _document("doc-1", "第一篇正文"),
            _document("doc-2", "第二篇正文"),
        ]
        source.prune_result = 1
        service, _ = _make_service(
            db_session_factory,
            sources=[source],
            settings=_Settings(fake_enabled=True),
        )
        try:
            await service.start()
            assert source.start_calls == 1
            await _drain(service)

            first = await service.sync_source("fake")
            assert first.rebuilt == 2
            assert first.deleted == 1  # prune 的删除数并进摘要
            assert source.prune_calls == 1
            assert source.prune_configs == [{}]

            # 上游删了一篇：完整清单删除 1 篇 + prune 再报 1 篇。
            source.documents = source.documents[:1]
            second = await service.sync_source("fake")
            assert second.deleted == 2
            assert source.prune_calls == 2

            # 同步失败时绝不调 prune。
            source.fail = True
            failed = await service.sync_source("fake")
            assert failed.deleted == 0
            assert source.prune_calls == 2
            assert (await _source_row(db_session_factory, "fake")).status == "error"
        finally:
            await service.close()
        assert source.close_calls == 1

    asyncio.run(scenario())
