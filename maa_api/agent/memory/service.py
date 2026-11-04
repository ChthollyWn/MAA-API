"""长期记忆服务：候选写入、审核状态机、过期扫描与索引/事件注入点（M79 规格 §4.3、§4.5）。

四条纪律：

- **状态机在本服务，路由只做校验与调用。** ``review`` 只接受 ``proposed``：批准
  改为 ``active``（可顺手改写 ``content``），拒绝改为 ``rejected``；其余状态抛
  ``MEMORY_NOT_PROPOSED``。``update_memory`` 只改 ``active`` 的正文、分类键与
  过期时间。
- **落库与副作用分开。** 先提交事务，再写 ``memory`` 来源索引与广播；索引或广播
  失败只记日志，不回滚已提交的记忆（与产物、工单服务同一口径）。
- **注入点由装配方传入。** ``index_writer`` 是可选回调，接收
  :class:`~maa_api.agent.knowledge.index.indexer.SourceDocument` 列表；M79 G3 在
  ``main.py`` 把它接到 ``KnowledgeIndexService.push_documents("memory", ...)``。
  ``broadcast`` 与 ``ws_manager.broadcast`` 同签名，测试可传记录器。
- **过期不另建后台任务。** :meth:`mark_expired` 在注入选择前扫一遍：到期的
  ``active`` 改 ``expired``，超过 30 天未审的 ``proposed`` 也改 ``expired``；
  ``episodic`` 新建时默认 90 天过期。
"""

from __future__ import annotations

import inspect
import logging
import unicodedata
from collections.abc import Awaitable, Callable, Collection, Iterable
from datetime import datetime, timedelta
from typing import Any

from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from maa_api.agent.knowledge.index.indexer import SourceDocument
from maa_api.db.models import MemoryItem, utcnow
from maa_api.db.repositories.base import Page
from maa_api.db.repositories.memory import MemoryRepository
from maa_api.domain.enums import MemoryKind, MemoryStatus
from maa_api.domain.errors import AppError, ErrorCode
from maa_api.settings import get_settings

logger = logging.getLogger(__name__)

__all__ = [
    "ACTIVE_SCAN_SIZE",
    "EPISODIC_TTL",
    "MAX_CONTENT_CHARS",
    "MAX_SUBJECT_CHARS",
    "PREVIEW_CHARS",
    "PROPOSED_TTL",
    "MemoryService",
]

#: ``content`` 上限（07 §5.1）：超过的候选直接丢弃，审核改写时按参数错误拒绝。
MAX_CONTENT_CHARS = 500
#: ``subject`` 是分类键（07 §5.1），列宽 64。
MAX_SUBJECT_CHARS = 64
#: ``memory_proposed`` 事件里的正文预览长度（07 §5.3：前 80 字）。
PREVIEW_CHARS = 80
#: ``episodic`` 的默认有效期（07 §5.1、§5.5）。
EPISODIC_TTL = timedelta(days=90)
#: ``proposed`` 超过这个时间未审就过期（07 §5.3）。
PROPOSED_TTL = timedelta(days=30)
#: 过期扫描与注入候选一次读多少行；记忆表是用户审核过的事实，量级很小。
ACTIVE_SCAN_SIZE = 10_000

#: ``update_memory`` 区分「没传这个参数」与「显式传 None 清空」。
_UNSET: Any = object()

#: ``index_writer``：接收一批 SourceDocument，返回任意值（可等待）。
IndexWriter = Callable[[list[SourceDocument]], Awaitable[Any] | Any]
Broadcast = Callable[[str, dict[str, Any]], Awaitable[Any] | Any]


def _invalid(message: str, details: dict[str, Any] | None = None) -> AppError:
    return AppError(ErrorCode.INVALID_PARAMETER, message, details)


def _not_found(memory_id: str) -> AppError:
    return AppError(
        ErrorCode.MEMORY_NOT_FOUND,
        "没有这条记忆",
        {"memory_id": memory_id},
    )


def _not_proposed(memory_id: str, status: str) -> AppError:
    return AppError(
        ErrorCode.MEMORY_NOT_PROPOSED,
        "这条记忆不在待审核状态",
        {"memory_id": memory_id, "status": status},
    )


def _normalize_content(content: str) -> str:
    """去重用的规范化：NFKC、去首尾空白、把连续空白折叠成一个空格。"""
    return " ".join(unicodedata.normalize("NFKC", content).split())


def _preview(content: str) -> str:
    return content[:PREVIEW_CHARS]


def _kind_value(kind: MemoryKind | str) -> MemoryKind:
    try:
        return MemoryKind(str(kind))
    except ValueError:
        raise _invalid("未知的记忆种类", {"kind": str(kind)}) from None


def _subject_value(subject: str | None) -> str | None:
    if subject is None:
        return None
    if not isinstance(subject, str):
        raise _invalid("subject 必须是字符串或空", {"subject": repr(subject)})
    text = subject.strip()
    if not text:
        return None
    if len(text) > MAX_SUBJECT_CHARS:
        raise _invalid(
            f"subject 不能超过 {MAX_SUBJECT_CHARS} 字",
            {"length": len(text)},
        )
    return text


def _content_value(content: Any, *, drop_too_long: bool) -> str | None:
    """校验正文；``drop_too_long=True``（候选写入）时超限返回 None，否则报参数错误。"""
    if not isinstance(content, str):
        raise _invalid("content 必须是字符串")
    text = content.strip()
    if not text:
        return None
    if len(text) > MAX_CONTENT_CHARS:
        if drop_too_long:
            return None
        raise _invalid(
            f"content 不能超过 {MAX_CONTENT_CHARS} 字",
            {"length": len(text)},
        )
    return text


class MemoryService:
    """记忆表的读写、审核状态机与注入辅助；不持有全局状态。"""

    def __init__(
        self,
        session_factory: async_sessionmaker[AsyncSession],
        *,
        broadcast: Broadcast | None = None,
        index_writer: IndexWriter | None = None,
        settings_provider: Callable[[], Any] = get_settings,
        clock: Callable[[], datetime] = utcnow,
    ) -> None:
        self._session_factory = session_factory
        self._broadcast = broadcast
        self._index_writer = index_writer
        self._settings_provider = settings_provider
        self._clock = clock

    # -- 配置 -----------------------------------------------------------
    def _memory_option(self, name: str, default: Any) -> Any:
        """读 ``memory.<name>``；M79 G3 注册 Settings 前按默认值处理。"""
        settings = self._settings_provider()
        memory = getattr(settings, "memory", None)
        return getattr(memory, name, default)

    def memory_disabled(self) -> bool:
        """``memory.enabled=false`` 时不抽取、不注入（07 §5.2）。"""
        return not bool(self._memory_option("enabled", True))

    def _require_review(self) -> bool:
        """``memory.require_review`` 默认 true；false 时提议直接生效。"""
        return bool(self._memory_option("require_review", True))

    # -- 读取 -----------------------------------------------------------
    async def list_memories(
        self,
        *,
        status: str | None = None,
        kind: str | None = None,
        q: str | None = None,
        page: int = 1,
        size: int = 20,
    ) -> Page[MemoryItem]:
        """列表：不传 ``status`` 时返回全部状态（规格 §4.3）。"""
        async with self._session_factory() as session:
            return await MemoryRepository(session).list_filtered(
                status=status, kind=kind, q=q, page=page, size=size
            )

    async def get_memory(self, memory_id: str) -> MemoryItem | None:
        """按 id 取一条记忆；不存在返回 ``None``，路由据此报 404。"""
        async with self._session_factory() as session:
            return await MemoryRepository(session).get(memory_id)

    # -- 写入 -----------------------------------------------------------
    async def create_candidate(
        self,
        *,
        kind: MemoryKind | str,
        subject: str | None,
        content: str,
        confidence: float | None = None,
        source_ticket_id: str | None = None,
        source_session_id: str | None = None,
        source_run_id: str | None = None,
        supersedes_id: str | None = None,
        allowed_supersedes: Collection[str] | None = None,
    ) -> MemoryItem | None:
        """写入一条候选/提议；返回 ``None`` 表示按规则丢弃。

        丢弃规则（07 §5.2、§5.5）：正文为空或超过 500 字；同种类、同 ``subject``
        且规范化后内容相同的生效/待审记忆已存在。``supersedes_id`` 只认
        ``allowed_supersedes`` 里给出的 id（抽取输入里的已生效记忆），否则丢掉该字段。
        ``memory.require_review=true`` 写 ``proposed``；false 直接 ``active`` 并记
        ``reviewed_by=auto``。提交后广播 ``memory_proposed``，生效时写索引。
        """
        kind_value = _kind_value(kind)
        subject_text = _subject_value(subject)
        body = _content_value(content, drop_too_long=True)
        if body is None:
            return None
        if confidence is not None:
            try:
                confidence = float(confidence)
            except (TypeError, ValueError):
                raise _invalid("confidence 必须是数字", {"confidence": repr(confidence)})

        allowed = {str(item) for item in (allowed_supersedes or ())}
        supersedes = str(supersedes_id) if supersedes_id is not None else None
        if supersedes is not None and supersedes not in allowed:
            supersedes = None

        now = self._clock()
        require_review = self._require_review()
        status = MemoryStatus.PROPOSED if require_review else MemoryStatus.ACTIVE
        expires_at = now + EPISODIC_TTL if kind_value is MemoryKind.EPISODIC else None
        memory = MemoryItem(
            kind=kind_value.value,
            subject=subject_text,
            content=body,
            status=status.value,
            confidence=confidence,
            source_ticket_id=source_ticket_id,
            source_session_id=source_session_id,
            source_run_id=source_run_id,
            supersedes_id=supersedes,
            expires_at=expires_at,
            reviewed_by=None if require_review else "auto",
            reviewed_at=None if require_review else now,
            created_at=now,
            updated_at=now,
        )

        async with self._session_factory() as session:
            repository = MemoryRepository(session)
            if await self._is_duplicate(repository, memory):
                return None
            if memory.status == MemoryStatus.ACTIVE.value:
                await self._apply_supersede(repository, memory, now=now)
            stored = await repository.add(memory)
            await session.commit()

        if stored.status == MemoryStatus.ACTIVE.value:
            await self._write_index(stored)
        await self._publish(stored)
        return stored

    async def remember_proposal(
        self,
        *,
        kind: MemoryKind | str,
        subject: str | None,
        content: str,
        source_ticket_id: str | None = None,
        source_session_id: str | None = None,
        source_run_id: str | None = None,
    ) -> MemoryItem | None:
        """``remember`` 工具的落点：confidence 固定 1，其余规则同候选写入。"""
        return await self.create_candidate(
            kind=kind,
            subject=subject,
            content=content,
            confidence=1.0,
            source_ticket_id=source_ticket_id,
            source_session_id=source_session_id,
            source_run_id=source_run_id,
        )

    async def review(
        self,
        memory_id: str,
        approved: bool,
        content: str | None = None,
        reviewed_by: str = "user",
    ) -> MemoryItem:
        """审核一条 ``proposed`` 记忆；批准可改写正文，拒绝只改状态。

        只有 ``proposed`` 能审核，其余状态抛 ``MEMORY_NOT_PROPOSED``（409）。
        批准时若 ``supersedes_id`` 指向一条 ``active`` 记忆，把旧记忆改为
        ``superseded``；指向的不是 ``active`` 就丢掉这个字段再批准，不新造错误码。
        """
        now = self._clock()
        async with self._session_factory() as session:
            repository = MemoryRepository(session)
            memory = await repository.get(memory_id)
            if memory is None:
                raise _not_found(memory_id)
            if str(memory.status) != MemoryStatus.PROPOSED.value:
                raise _not_proposed(memory_id, str(memory.status))

            if approved:
                if content is not None:
                    body = _content_value(content, drop_too_long=False)
                    if body is None:
                        raise _invalid("content 不能为空")
                    memory.content = body
                memory.status = MemoryStatus.ACTIVE.value
                memory.reviewed_by = reviewed_by
                memory.reviewed_at = now
                await self._apply_supersede(repository, memory, now=now)
            else:
                memory.status = MemoryStatus.REJECTED.value
                memory.reviewed_by = reviewed_by
                memory.reviewed_at = now
            memory.updated_at = now
            await repository.add(memory)
            await session.commit()

        if approved:
            await self._write_index(memory)
        return memory

    async def update_memory(
        self,
        memory_id: str,
        *,
        content: Any = _UNSET,
        subject: Any = _UNSET,
        expires_at: Any = _UNSET,
    ) -> MemoryItem:
        """改一条 ``active`` 记忆的正文、分类键与过期时间。

        不存在抛 ``MEMORY_NOT_FOUND``；不是在生效中抛 ``INVALID_PARAMETER``。
        显式传 ``expires_at=None`` 表示清空过期时间。
        """
        now = self._clock()
        async with self._session_factory() as session:
            repository = MemoryRepository(session)
            memory = await repository.get(memory_id)
            if memory is None:
                raise _not_found(memory_id)
            if str(memory.status) != MemoryStatus.ACTIVE.value:
                raise _invalid(
                    "只有生效中的记忆可以修改",
                    {"memory_id": memory_id, "status": str(memory.status)},
                )

            changed = False
            if content is not _UNSET:
                body = _content_value(content, drop_too_long=False)
                if body is None:
                    raise _invalid("content 不能为空")
                memory.content = body
                changed = True
            if subject is not _UNSET:
                memory.subject = _subject_value(subject)
                changed = True
            if expires_at is not _UNSET:
                if expires_at is not None and not isinstance(expires_at, datetime):
                    raise _invalid("expires_at 必须是时间或空")
                memory.expires_at = expires_at
                changed = True
            if not changed:
                raise _invalid("至少提供一个要修改的字段")
            memory.updated_at = now
            await repository.add(memory)
            await session.commit()

        await self._write_index(memory)
        return memory

    async def delete_memory(self, memory_id: str) -> None:
        """物理删除一条记忆（03 §14 不自动删，用户在这里删）；不存在报 404。"""
        async with self._session_factory() as session:
            deleted = await MemoryRepository(session).delete(memory_id)
            await session.commit()
        if not deleted:
            raise _not_found(memory_id)

    # -- 注入辅助 -------------------------------------------------------
    async def active_for_injection(
        self, *, now: datetime | None = None
    ) -> list[MemoryItem]:
        """注入候选：先做一次过期扫描，再返回未过期的 ``active`` 记忆。"""
        moment = now or self._clock()
        await self.mark_expired(now=moment)
        async with self._session_factory() as session:
            page = await MemoryRepository(session).list_filtered(
                status=MemoryStatus.ACTIVE.value, page=1, size=ACTIVE_SCAN_SIZE
            )
        return [
            memory
            for memory in page.items
            if memory.expires_at is None or memory.expires_at > moment
        ]

    async def mark_expired(self, *, now: datetime | None = None) -> int:
        """把到期的 ``active`` 与超过 30 天未审的 ``proposed`` 改为 ``expired``。"""
        moment = now or self._clock()
        stale_before = moment - PROPOSED_TTL
        changed = 0
        async with self._session_factory() as session:
            repository = MemoryRepository(session)
            active = await repository.list_filtered(
                status=MemoryStatus.ACTIVE.value, page=1, size=ACTIVE_SCAN_SIZE
            )
            for memory in active.items:
                if memory.expires_at is not None and memory.expires_at <= moment:
                    memory.status = MemoryStatus.EXPIRED.value
                    memory.updated_at = moment
                    await repository.add(memory)
                    changed += 1
            proposed = await repository.list_filtered(
                status=MemoryStatus.PROPOSED.value, page=1, size=ACTIVE_SCAN_SIZE
            )
            for memory in proposed.items:
                if memory.created_at <= stale_before:
                    memory.status = MemoryStatus.EXPIRED.value
                    memory.updated_at = moment
                    await repository.add(memory)
                    changed += 1
            if changed:
                await session.commit()
        return changed

    async def expire_due(self, *, now: datetime | None = None) -> int:
        """:meth:`mark_expired` 的别名；注入路径与启动扫描用同一个入口。"""
        return await self.mark_expired(now=now)

    async def record_use(
        self,
        memory_ids: str | Iterable[str],
        *,
        now: datetime | None = None,
    ) -> int:
        """刷新被注入记忆的 ``last_used_at`` 并把 ``use_count`` 加一。"""
        if isinstance(memory_ids, str):
            ids = [memory_ids]
        else:
            ids = [str(memory_id) for memory_id in memory_ids]
        unique = list(dict.fromkeys(ids))
        if not unique:
            return 0
        moment = now or self._clock()
        updated = 0
        async with self._session_factory() as session:
            repository = MemoryRepository(session)
            for memory_id in unique:
                memory = await repository.get(memory_id)
                if memory is None:
                    continue
                memory.use_count = int(memory.use_count or 0) + 1
                memory.last_used_at = moment
                await repository.add(memory)
                updated += 1
            if updated:
                await session.commit()
        return updated

    # -- 内部 -----------------------------------------------------------
    async def _is_duplicate(
        self, repository: MemoryRepository, memory: MemoryItem
    ) -> bool:
        """同种类、同 ``subject``、规范化后内容相同的生效/待审记忆已存在。"""
        wanted = _normalize_content(memory.content)
        for existing in await repository.all():
            if str(existing.kind) != str(memory.kind):
                continue
            if existing.subject != memory.subject:
                continue
            if str(existing.status) not in (
                MemoryStatus.PROPOSED.value,
                MemoryStatus.ACTIVE.value,
            ):
                continue
            if _normalize_content(existing.content) == wanted:
                return True
        return False

    async def _apply_supersede(
        self,
        repository: MemoryRepository,
        memory: MemoryItem,
        *,
        now: datetime,
    ) -> None:
        """批准（或直接生效）时落实取代关系；目标不是 active 就丢掉字段。"""
        if memory.supersedes_id is None:
            return
        old = await repository.get(memory.supersedes_id)
        if old is None or str(old.status) != MemoryStatus.ACTIVE.value:
            memory.supersedes_id = None
            return
        old.status = MemoryStatus.SUPERSEDED.value
        old.updated_at = now
        await repository.add(old)

    async def _write_index(self, memory: MemoryItem) -> None:
        """把生效记忆写进 ``memory`` 来源索引；失败只记日志。"""
        if self._index_writer is None:
            return
        document = SourceDocument(
            external_id=memory.id,
            title=_preview(memory.content),
            text=memory.content,
            version=memory.updated_at.isoformat(),
            fetched_at=memory.updated_at,
            meta={
                "kind": str(memory.kind),
                "subject": memory.subject,
                "status": str(memory.status),
            },
        )
        try:
            result = self._index_writer([document])
            if inspect.isawaitable(result):
                await result
        except Exception:  # noqa: BLE001 - 记忆已落库，索引失败不能回滚写入
            logger.warning(
                "记忆写索引失败：memory=%s", memory.id, exc_info=True
            )

    async def _publish(self, memory: MemoryItem) -> None:
        """广播 ``agent_event`` / ``memory_proposed``；失败只记日志。"""
        if self._broadcast is None:
            return
        payload = {
            "session_id": memory.source_session_id,
            "event": "memory_proposed",
            "payload": {
                "id": memory.id,
                "kind": str(memory.kind),
                "preview": _preview(memory.content),
            },
        }
        try:
            result = self._broadcast("agent_event", payload)
            if inspect.isawaitable(result):
                await result
        except Exception:  # noqa: BLE001 - 记忆已落库，广播失败不能回滚写入
            logger.warning(
                "记忆提议广播失败：memory=%s", memory.id, exc_info=True
            )
