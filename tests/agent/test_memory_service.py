"""M79 T1：长期记忆服务（spec §4.1、§4.3、§4.5、§6）。

本文件覆盖 spec §6 列出的四条 ``tests/agent/test_memory_service.py`` 用例，并补
状态机边界、去重/白名单、过期扫描、更新删除与事件/索引注入点。异步用例统一写成
「同步测试函数 + ``asyncio.run(scenario())``」；临时库复用 ``tests/db/conftest.py``
的 ``create_all`` 夹具。

``memory`` 来源的索引写入在 T1 是可选回调：本文件用真实的
:class:`KnowledgeIndexService` + 一个假的 ``memory`` 来源验证「审核通过后能在
``memory`` 来源里查到」，该来源由 M79 G3 换成正式适配器。
"""

from __future__ import annotations

import asyncio
from datetime import datetime, timedelta
from types import SimpleNamespace
from typing import Any, Sequence

import pytest

from maa_api.agent.knowledge.index.embeddings import NullEmbeddingProvider
from maa_api.agent.knowledge.index.indexer import KnowledgeIndexService
from maa_api.agent.knowledge.index.retriever import Retriever
from maa_api.agent.memory import (
    EPISODIC_TTL,
    MAX_CONTENT_CHARS,
    MemoryService,
)
from maa_api.db.models import MemoryItem
from maa_api.db.repositories.knowledge_index import KnowledgeIndexRepository
from maa_api.db.repositories.memory import MemoryRepository
from maa_api.domain.enums import KnowledgeSourceKind, MemoryKind, MemoryStatus
from maa_api.domain.errors import AppError, ErrorCode
from tests.db.conftest import (  # noqa: F401  # pytest 从模块命名空间发现 fixture
    db_engine,
    db_path,
    db_session,
    db_session_factory,
)

NOW = datetime(2026, 11, 5, 4, 0, 0)


# ---------------------------------------------------------------------------
# 桩：设置、来源、向量解析器、记录器
# ---------------------------------------------------------------------------
class _Settings:
    """``memory.*`` 与检索层的最小设置桩；T1 还没有真实的 Settings 段。"""

    def __init__(self, *, require_review: bool = True, enabled: bool = True) -> None:
        self.memory = SimpleNamespace(
            enabled=enabled, require_review=require_review
        )
        self.rag = SimpleNamespace(
            enabled=True,
            top_k=8,
            embedding_provider="none",
            chunk_chars=200,
            chunk_overlap=20,
        )


class _MemorySource:
    """假的 ``memory`` 来源：只为让真实索引服务接受 ``push_documents``。

    ``list_refs`` / ``fetch`` 不会被推送路径调用；G3 会换成读表的正式适配器。
    """

    source_id = "memory"
    kind = KnowledgeSourceKind.MEMORY
    title = "长期记忆"
    trusted = False
    license = None
    searchable_by_default = False
    setting_key = None
    full_listing = False

    def validate_config(self, config: Any) -> dict[str, Any]:
        return dict(config)

    async def list_refs(self, config: Any) -> list[Any]:  # pragma: no cover - 不触发
        raise AssertionError("推送写入不应调用来源的 list_refs")

    async def fetch(self, refs: Any, config: Any) -> list[Any]:  # pragma: no cover
        raise AssertionError("推送写入不应调用来源的 fetch")


class _NullResolver:
    """EmbeddingResolver 桩：provider 为 none，索引服务不会去补向量。"""

    def __init__(self) -> None:
        self.provider = NullEmbeddingProvider()

    async def current(self) -> Any:
        return self.provider

    def status(self) -> Any:
        return SimpleNamespace(
            provider="none", model="none", status="disabled", error=None
        )


class _Recorder:
    """记录广播与索引写入；``index_writer`` 是异步回调。"""

    def __init__(self) -> None:
        self.events: list[tuple[str, dict[str, Any]]] = []
        self.documents: list[Any] = []

    def broadcast(self, message_type: str, data: dict[str, Any]) -> None:
        self.events.append((message_type, data))

    async def write_index(self, documents: Sequence[Any]) -> None:
        self.documents.extend(documents)


def _make_service(
    factory,
    *,
    settings: _Settings | None = None,
    recorder: _Recorder | None = None,
    index_writer: Any = None,
) -> tuple[MemoryService, _Settings, _Recorder]:
    holder = recorder or _Recorder()
    settings = settings or _Settings()
    service = MemoryService(
        factory,
        broadcast=holder.broadcast,
        index_writer=index_writer if index_writer is not None else holder.write_index,
        settings_provider=lambda: settings,
        clock=lambda: NOW,
    )
    return service, settings, holder


def _make_index(factory, settings: _Settings) -> KnowledgeIndexService:
    index = KnowledgeIndexService(
        factory,
        settings_provider=lambda: settings,
        embeddings=_NullResolver(),
    )
    index.register(_MemorySource())
    return index


async def _get(factory, memory_id: str) -> MemoryItem | None:
    async with factory() as session:
        return await MemoryRepository(session).get(memory_id)


# ---------------------------------------------------------------------------
# §6 四条
# ---------------------------------------------------------------------------
def test_review_activates_and_indexes(db_session_factory) -> None:
    """批准后 status=active，并且 ``memory`` 来源能检索到正文。"""
    factory = db_session_factory

    async def scenario() -> None:
        settings = _Settings()
        index = _make_index(factory, settings)
        service = MemoryService(
            factory,
            index_writer=lambda docs: index.push_documents("memory", docs),
            settings_provider=lambda: settings,
            clock=lambda: NOW,
        )
        try:
            candidate = await service.create_candidate(
                kind=MemoryKind.SEMANTIC,
                subject="preference",
                content="不用源石恢复理智",
            )
            assert candidate is not None
            assert candidate.status == MemoryStatus.PROPOSED.value

            async with factory() as session:
                indexed = await KnowledgeIndexRepository(session).documents_of("memory")
            assert indexed == {}

            reviewed = await service.review(candidate.id, True)
            assert reviewed.status == MemoryStatus.ACTIVE.value
            assert reviewed.reviewed_by == "user"
            assert reviewed.reviewed_at == NOW

            retriever = Retriever(
                factory, index=index, settings_provider=lambda: settings
            )
            result = await retriever.search("源石恢复", sources=["memory"])
            assert result.hits
            assert any("不用源石恢复理智" in hit.text for hit in result.hits)
        finally:
            await index.close()

    asyncio.run(scenario())


def test_review_reject(db_session_factory) -> None:
    """拒绝后 status=rejected，索引里没有这条正文。"""
    factory = db_session_factory

    async def scenario() -> None:
        service, _, recorder = _make_service(factory)
        candidate = await service.create_candidate(
            kind="semantic", subject="preference", content="偏好单刷"
        )
        assert candidate is not None
        recorder.documents.clear()

        reviewed = await service.review(candidate.id, False)
        assert reviewed.status == MemoryStatus.REJECTED.value
        assert reviewed.reviewed_by == "user"
        assert reviewed.reviewed_at == NOW
        assert recorder.documents == []
        assert await _get(factory, candidate.id) is not None

    asyncio.run(scenario())


def test_review_twice_conflicts(db_session_factory) -> None:
    """第二次审核 409 ``MEMORY_NOT_PROPOSED``；不存在的 id 是 404。"""
    factory = db_session_factory

    async def scenario() -> None:
        service, _, _ = _make_service(factory)
        candidate = await service.create_candidate(
            kind="semantic", subject="preference", content="偏好单刷"
        )
        assert candidate is not None
        await service.review(candidate.id, True)

        with pytest.raises(AppError) as conflict:
            await service.review(candidate.id, False)
        assert conflict.value.code is ErrorCode.MEMORY_NOT_PROPOSED
        assert conflict.value.http_status == 409

        with pytest.raises(AppError) as missing:
            await service.review("no-such-memory", True)
        assert missing.value.code is ErrorCode.MEMORY_NOT_FOUND
        assert missing.value.http_status == 404

    asyncio.run(scenario())


def test_remember_stays_proposed(db_session_factory) -> None:
    """``remember_proposal`` 只提议、confidence=1、不进索引。"""
    factory = db_session_factory

    async def scenario() -> None:
        service, _, recorder = _make_service(factory)
        memory = await service.remember_proposal(
            kind="semantic",
            subject="account",
            content="账号是官服",
        )
        assert memory is not None
        assert memory.status == MemoryStatus.PROPOSED.value
        assert memory.confidence == 1.0
        assert memory.reviewed_by is None
        assert recorder.documents == []
        # 提议仍会广播，界面据此刷新「待你处理」。
        assert len(recorder.events) == 1

    asyncio.run(scenario())


# ---------------------------------------------------------------------------
# 补充：候选规则、状态机、过期与注入辅助
# ---------------------------------------------------------------------------
def test_create_candidate_drops_invalid_and_duplicates(db_session_factory) -> None:
    """超 500 字丢弃；同 kind+subject+规范化内容去重；不同 subject 允许。"""
    factory = db_session_factory

    async def scenario() -> None:
        service, _, _ = _make_service(factory)
        assert (
            await service.create_candidate(
                kind="semantic", subject="preference", content="x" * (MAX_CONTENT_CHARS + 1)
            )
            is None
        )
        assert (
            await service.create_candidate(
                kind="semantic", subject="preference", content="   "
            )
            is None
        )

        first = await service.create_candidate(
            kind="semantic",
            subject="preference",
            content="主力队伍以近卫为核心",
            confidence=0.8,
        )
        assert first is not None
        assert first.confidence == 0.8
        # 规范化后相同（多余空白）的提议丢弃。
        assert (
            await service.create_candidate(
                kind="semantic",
                subject="preference",
                content="  主力队伍以近卫为核心  ",
            )
            is None
        )
        # 同内容但不同 subject / kind 是另一条记忆。
        assert (
            await service.create_candidate(
                kind="semantic", subject="account", content="主力队伍以近卫为核心"
            )
            is not None
        )
        assert (
            await service.create_candidate(
                kind="episodic", subject="preference", content="主力队伍以近卫为核心"
            )
            is not None
        )
        with pytest.raises(AppError) as invalid:
            await service.create_candidate(
                kind="fact", subject="preference", content="不存在的种类"
            )
        assert invalid.value.code is ErrorCode.INVALID_PARAMETER

    asyncio.run(scenario())


def test_supersedes_only_accepts_whitelist(db_session_factory) -> None:
    """``supersedes`` 只指向本次输入给出的 id，否则丢掉字段。"""
    factory = db_session_factory

    async def scenario() -> None:
        service, _, _ = _make_service(factory)
        old = await service.create_candidate(
            kind="semantic", subject="preference", content="偏好单刷"
        )
        assert old is not None

        dropped = await service.create_candidate(
            kind="semantic",
            subject="preference",
            content="偏好组队",
            supersedes_id=old.id,
            allowed_supersedes=set(),
        )
        assert dropped is not None
        assert dropped.supersedes_id is None

        kept = await service.create_candidate(
            kind="semantic",
            subject="preference",
            content="偏好挂机",
            supersedes_id=old.id,
            allowed_supersedes={old.id},
        )
        assert kept is not None
        assert kept.supersedes_id == old.id

    asyncio.run(scenario())


def test_review_applies_supersedes(db_session_factory) -> None:
    """批准时旧条是 active 就改 superseded；新条保留 supersedes_id。"""
    factory = db_session_factory

    async def scenario() -> None:
        service, _, _ = _make_service(factory)
        old = await service.create_candidate(
            kind="semantic", subject="preference", content="偏好单刷"
        )
        assert old is not None
        await service.review(old.id, True)

        new = await service.create_candidate(
            kind="semantic",
            subject="preference",
            content="偏好组队",
            supersedes_id=old.id,
            allowed_supersedes={old.id},
        )
        assert new is not None and new.supersedes_id == old.id
        reviewed = await service.review(new.id, True)

        stored_old = await _get(factory, old.id)
        assert stored_old is not None
        assert stored_old.status == MemoryStatus.SUPERSEDED.value
        assert reviewed.status == MemoryStatus.ACTIVE.value
        assert reviewed.supersedes_id == old.id

    asyncio.run(scenario())


def test_review_drops_supersedes_when_target_not_active(db_session_factory) -> None:
    """指向的记忆不是 active 时丢掉字段再批准，不新造错误码。"""
    factory = db_session_factory

    async def scenario() -> None:
        service, _, _ = _make_service(factory)
        old = await service.create_candidate(
            kind="semantic", subject="preference", content="偏好单刷"
        )
        assert old is not None  # 仍是 proposed

        new = await service.create_candidate(
            kind="semantic",
            subject="preference",
            content="偏好组队",
            supersedes_id=old.id,
            allowed_supersedes={old.id},
        )
        assert new is not None
        reviewed = await service.review(new.id, True)
        assert reviewed.supersedes_id is None
        assert reviewed.status == MemoryStatus.ACTIVE.value
        stored_old = await _get(factory, old.id)
        assert stored_old is not None
        assert stored_old.status == MemoryStatus.PROPOSED.value

    asyncio.run(scenario())


def test_require_review_false_activates_and_broadcasts(db_session_factory) -> None:
    """``require_review=false`` 直接 active、reviewed_by=auto、仍发事件、写索引。"""
    factory = db_session_factory

    async def scenario() -> None:
        settings = _Settings(require_review=False)
        service, _, recorder = _make_service(factory, settings=settings)
        memory = await service.create_candidate(
            kind="episodic",
            subject="stage:7-18",
            content="2026-11-05 用作业站某份作业打 7-18 失败，原因是缺少某名干员",
        )
        assert memory is not None
        assert memory.status == MemoryStatus.ACTIVE.value
        assert memory.reviewed_by == "auto"
        assert memory.reviewed_at == NOW
        assert memory.expires_at == NOW + EPISODIC_TTL
        assert len(recorder.documents) == 1
        assert len(recorder.events) == 1

        message_type, envelope = recorder.events[0]
        assert message_type == "agent_event"
        assert set(envelope) == {"session_id", "event", "payload"}
        assert envelope["event"] == "memory_proposed"
        assert envelope["payload"]["id"] == memory.id
        assert envelope["payload"]["kind"] == "episodic"
        assert envelope["payload"]["preview"] == memory.content[:80]

        # 直接生效也落实取代关系，不留悬空的 supersedes_id。
        replacement = await service.create_candidate(
            kind="episodic",
            subject="stage:7-18",
            content="2026-11-06 同一份作业补上干员后打过了",
            supersedes_id=memory.id,
            allowed_supersedes={memory.id},
        )
        assert replacement is not None
        stored_old = await _get(factory, memory.id)
        assert stored_old is not None
        assert stored_old.status == MemoryStatus.SUPERSEDED.value

    asyncio.run(scenario())


def test_list_filters_and_pagination(db_session_factory) -> None:
    """不传 status 返回全部；status/kind/q/page/size 各自生效。"""
    factory = db_session_factory

    async def scenario() -> None:
        service, _, _ = _make_service(factory)
        for kind, subject, content in (
            ("semantic", "preference", "主力队伍以近卫为核心"),
            ("semantic", "account", "账号是官服"),
            ("episodic", "stage:1-7", "1-7 刷固源岩很稳"),
        ):
            assert (
                await service.create_candidate(
                    kind=kind, subject=subject, content=content
                )
                is not None
            )

        everything = await service.list_memories()
        assert everything.total == 3
        assert len(everything.items) == 3

        semantic = await service.list_memories(kind="semantic")
        assert semantic.total == 2

        active = await service.list_memories(status="active")
        assert active.total == 0

        searched = await service.list_memories(q="近卫")
        assert searched.total == 1
        assert searched.items[0].subject == "preference"

        first_page = await service.list_memories(page=1, size=2)
        second_page = await service.list_memories(page=2, size=2)
        assert first_page.total == 3 and len(first_page.items) == 2
        assert second_page.total == 3 and len(second_page.items) == 1

    asyncio.run(scenario())


def test_update_and_delete(db_session_factory) -> None:
    """active 可改 content/subject/expires_at；删除是物理删除，二次删除 404。"""
    factory = db_session_factory

    async def scenario() -> None:
        service, _, recorder = _make_service(
            factory, settings=_Settings(require_review=False)
        )
        memory = await service.create_candidate(
            kind="semantic", subject="preference", content="偏好单刷"
        )
        assert memory is not None
        recorder.documents.clear()

        updated = await service.update_memory(
            memory.id,
            content="偏好手动刷图",
            subject="account",
            expires_at=datetime(2027, 1, 1),
        )
        assert updated.content == "偏好手动刷图"
        assert updated.subject == "account"
        assert updated.expires_at == datetime(2027, 1, 1)
        assert len(recorder.documents) == 1  # 改正文后重新写索引

        proposed_service, _, _ = _make_service(factory)
        proposed = await proposed_service.create_candidate(
            kind="semantic", subject="device", content="设备是 M 系芯片"
        )
        assert proposed is not None
        with pytest.raises(AppError) as invalid:
            await proposed_service.update_memory(proposed.id, content="改不了")
        assert invalid.value.code is ErrorCode.INVALID_PARAMETER

        with pytest.raises(AppError) as missing:
            await proposed_service.update_memory("no-such-memory", content="改不了")
        assert missing.value.code is ErrorCode.MEMORY_NOT_FOUND

        await proposed_service.delete_memory(updated.id)
        assert await _get(factory, updated.id) is None
        with pytest.raises(AppError) as gone:
            await proposed_service.delete_memory(updated.id)
        assert gone.value.code is ErrorCode.MEMORY_NOT_FOUND

    asyncio.run(scenario())


def test_mark_expired_and_active_for_injection(db_session_factory) -> None:
    """到期的 active 与超过 30 天未审的 proposed 过期；注入只取有效 active。"""
    factory = db_session_factory

    async def scenario() -> None:
        service, _, _ = _make_service(factory)
        expired = await service.create_candidate(
            kind="episodic", subject="stage:1-7", content="旧经历"
        )
        stale = await service.create_candidate(
            kind="semantic", subject="preference", content="没人审核的偏好"
        )
        alive = await service.create_candidate(
            kind="semantic", subject="account", content="账号是官服"
        )
        assert expired is not None and stale is not None and alive is not None
        await service.review(expired.id, True)
        await service.review(alive.id, True)

        async with factory() as session:
            row = await MemoryRepository(session).get(expired.id)
            assert row is not None
            row.expires_at = NOW - timedelta(seconds=1)
            row = await MemoryRepository(session).get(stale.id)
            assert row is not None
            row.created_at = NOW - timedelta(days=31)
            await session.commit()

        assert await service.mark_expired(now=NOW) == 2
        assert (await _get(factory, expired.id)).status == MemoryStatus.EXPIRED.value
        assert (await _get(factory, stale.id)).status == MemoryStatus.EXPIRED.value
        assert (await _get(factory, alive.id)).status == MemoryStatus.ACTIVE.value
        # 第二次扫描没有可改的行，``expire_due`` 是同一入口的别名。
        assert await service.expire_due(now=NOW) == 0

        injectable = await service.active_for_injection(now=NOW)
        assert [row.id for row in injectable] == [alive.id]

    asyncio.run(scenario())


def test_record_use_refreshes_counters(db_session_factory) -> None:
    """注入后刷新 last_used_at、use_count 加一；不存在的 id 跳过。"""
    factory = db_session_factory

    async def scenario() -> None:
        service, _, _ = _make_service(
            factory, settings=_Settings(require_review=False)
        )
        memory = await service.create_candidate(
            kind="semantic", subject="preference", content="偏好单刷"
        )
        assert memory is not None
        assert memory.use_count == 0 and memory.last_used_at is None

        moment = datetime(2026, 11, 5, 5, 0, 0)
        assert await service.record_use(memory.id, now=moment) == 1
        stored = await _get(factory, memory.id)
        assert stored is not None
        assert stored.use_count == 1
        assert stored.last_used_at == moment

        assert await service.record_use([memory.id, "no-such-memory"], now=moment) == 1
        stored = await _get(factory, memory.id)
        assert stored is not None and stored.use_count == 2

    asyncio.run(scenario())


def test_memory_disabled_and_pre_g3_defaults(db_session_factory) -> None:
    """``memory.enabled`` 决定是否停用；Settings 还没有 memory 段时按默认值。"""
    factory = db_session_factory

    async def scenario() -> None:
        disabled, _, _ = _make_service(factory, settings=_Settings(enabled=False))
        assert disabled.memory_disabled() is True

        # T1 阶段 Settings 还没有 memory 段：按 enabled=true / require_review=true。
        legacy = MemoryService(
            factory,
            settings_provider=lambda: SimpleNamespace(),
            clock=lambda: NOW,
        )
        assert legacy.memory_disabled() is False
        memory = await legacy.create_candidate(
            kind="semantic", subject="preference", content="偏好单刷"
        )
        assert memory is not None
        assert memory.status == MemoryStatus.PROPOSED.value

    asyncio.run(scenario())
