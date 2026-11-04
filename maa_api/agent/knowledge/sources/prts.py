"""PRTS 页面来源（M77 spec §4.1、§4.5）。

两个入口：

- **定时同步**：种子页 + 库里最旧的 ``refresh_read_pages`` 个随读页；每 20 个标题
  一批调 ``wikitext``，``missing`` 的跳过，正文用 ``clean_page_text`` 清洗。
- **随读推送**：``start()`` 把 ``_on_page_loaded`` 挂到页面服务上，监听器只把
  ``(page, fetched_at)`` 放进 ``Queue(maxsize=100)``，后台消费者在三个开关都为真时
  调 ``push_documents`` 写库。监听器本身绝不碰数据库，慢写入不会拖慢页面查询。

``prune`` 负责三条清理规则：移出种子的旧种子页、超过 90 天的随读页、随读页总数
超过 ``max_pages`` 时删最旧的。正文不写日志。
"""

from __future__ import annotations

import asyncio
import logging
import re
from collections.abc import Callable, Mapping, Sequence
from datetime import UTC, datetime, timedelta
from typing import Any

from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from maa_api.agent.knowledge.index.indexer import DocumentRef, SourceDocument
from maa_api.db.models import KnowledgeDocument, KnowledgeSource, utcnow
from maa_api.db.repositories.knowledge_index import KnowledgeIndexRepository
from maa_api.domain.enums import KnowledgeSourceKind
from maa_api.domain.errors import AppError, ErrorCode
from maa_api.services.external.prts import PrtsClient, WikiPage, validate_title
from maa_api.services.game.pages import (
    clean_page_text,
    default_page_service,
    make_page_url,
)
from maa_api.settings import get_settings

__all__ = [
    "DEFAULT_SEED_TITLES",
    "READ_PAGE_TTL_DAYS",
    "PrtsCorpusSource",
]

logger = logging.getLogger(__name__)

#: 默认种子页（spec §4.5 逐字）；用户可以在来源配置里整份替换。
DEFAULT_SEED_TITLES: tuple[str, ...] = (
    "理智",
    "公开招募",
    "罗德岛基建",
    "信赖值",
    "游戏数据基础",
    "采购中心",
    "干员模组一览",
    "危机合约",
    "集成战略",
    "保全派驻",
)

#: 随读页的保留天数。
READ_PAGE_TTL_DAYS = 90

#: 一次 wikitext 请求最多 20 个标题（`PrtsClient` 同一上限）。
BATCH_SIZE = 20

#: 随读队列上限；满了丢弃并记 debug，页面查询照常返回。
QUEUE_MAXSIZE = 100

#: 配置边界。
MAX_SEED_TITLES = 50
MIN_MAX_PAGES = 50
MAX_MAX_PAGES = 2000
MAX_REFRESH_READ_PAGES = 200

_INT_PATTERN = re.compile(r"^-?[0-9]+$")


class PrtsCorpusSource:
    """PRTS 页面来源；定时同步与随读推送共用 ``push_documents`` 的写入口。"""

    source_id = "prts"
    kind = KnowledgeSourceKind.PRTS
    title = "PRTS 页面"
    trusted = False
    license = "CC BY-NC-SA 4.0，版权属于鹰角网络"
    searchable_by_default = True
    setting_key = "rag.prts_enabled"
    full_listing = False
    scheduled_refresh = True

    def __init__(
        self,
        client: PrtsClient,
        *,
        session_factory: async_sessionmaker[AsyncSession] | None = None,
        settings_provider: Callable[[], Any] | None = None,
        page_service: Any = None,
    ) -> None:
        self._client = client
        self._session_factory = session_factory
        self._settings_provider = settings_provider or get_settings
        self._page_service = page_service
        self._queue: asyncio.Queue[tuple[WikiPage, datetime]] = asyncio.Queue(
            maxsize=QUEUE_MAXSIZE
        )
        self._index: Any = None
        self._consumer: asyncio.Task[Any] | None = None
        self._started = False
        self._closed = False

    # -- 配置 -----------------------------------------------------------
    def validate_config(self, config: Mapping[str, Any]) -> dict[str, Any]:
        """校验 ``seed_titles`` / ``max_pages`` / ``refresh_read_pages`` 并补齐默认值。"""
        values = dict(config or {})
        seeds = values.get("seed_titles", list(DEFAULT_SEED_TITLES))
        if not isinstance(seeds, (list, tuple)) or not all(
            isinstance(item, str) for item in seeds
        ):
            raise _invalid("seed_titles", "种子标题必须是字符串列表")
        if len(seeds) > MAX_SEED_TITLES:
            raise _invalid("seed_titles", f"种子标题不能超过 {MAX_SEED_TITLES} 个")
        cleaned: list[str] = []
        for seed in seeds:
            try:
                cleaned.append(validate_title(seed))
            except AppError as exc:
                raise _invalid("seed_titles", f"种子标题不合法：{exc.message}") from exc
        max_pages = _bounded_int(
            values.get("max_pages", 500),
            "max_pages",
            minimum=MIN_MAX_PAGES,
            maximum=MAX_MAX_PAGES,
        )
        refresh_read_pages = _bounded_int(
            values.get("refresh_read_pages", 40),
            "refresh_read_pages",
            minimum=0,
            maximum=MAX_REFRESH_READ_PAGES,
        )
        return {
            "seed_titles": cleaned,
            "max_pages": max_pages,
            "refresh_read_pages": refresh_read_pages,
        }

    # -- 生命周期 -------------------------------------------------------
    async def start(self, index: Any) -> None:
        """挂页面监听器并起随读消费者；重复调用只生效一次。"""
        if self._started or self._closed:
            return
        self._started = True
        self._index = index
        self.page_service.add_listener(self._on_page_loaded)
        self._consumer = asyncio.create_task(
            self._consume(), name="prts-corpus-consumer"
        )

    async def close(self) -> None:
        """停掉随读消费者；监听器随后只做丢弃。"""
        self._closed = True
        task = self._consumer
        self._consumer = None
        if task is not None and not task.done():
            task.cancel()
            await asyncio.gather(task, return_exceptions=True)

    @property
    def page_service(self) -> Any:
        """页面服务；未注入时用进程内默认门面（spec §4.5）。"""
        return self._page_service or default_page_service()

    # -- 随读 -----------------------------------------------------------
    async def _on_page_loaded(self, page: WikiPage, fetched_at: datetime) -> None:
        """监听器：只入队，不做任何 I/O；队列满了丢弃并记 debug。"""
        if self._closed:
            return
        try:
            self._queue.put_nowait((page, fetched_at))
        except asyncio.QueueFull:
            logger.debug("PRTS 随读队列已满，丢弃页面 %s", page.title)

    async def _consume(self) -> None:
        """后台消费者：逐个页面检查开关后 push 入库。"""
        while True:
            page, fetched_at = await self._queue.get()
            try:
                await self._push_page(page, fetched_at)
            except asyncio.CancelledError:
                raise
            except Exception as exc:  # noqa: BLE001 - 推送失败不能拖垮消费者
                # 不记 exc_info：外部正文不能进日志，异常栈可能带库里的正文参数。
                logger.warning("PRTS 随读页写入索引失败：%s", type(exc).__name__)
            finally:
                self._queue.task_done()

    async def _push_page(self, page: WikiPage, fetched_at: datetime) -> None:
        if self._index is None or not await self._push_allowed():
            return
        seeds = await self._seed_titles()
        origin = "seed" if page.title in seeds else "read"
        meta: dict[str, Any] = {
            "origin": origin,
            "revision": page.revision,
            "redirect_from": page.redirect_from,
            "read_at": fetched_at.isoformat(),
        }
        document = SourceDocument(
            external_id=page.title,
            title=page.title,
            text=clean_page_text(page.content),
            url=make_page_url(page.title),
            version=_revision(page.revision),
            fetched_at=fetched_at,
            meta=meta,
        )
        await self._index.push_documents(self.source_id, [document])

    async def _push_allowed(self) -> bool:
        """``rag.enabled``、``rag.prts_enabled`` 与来源行 ``enabled`` 都为真。"""
        rag = getattr(self._settings_provider(), "rag", None)
        if not bool(getattr(rag, "enabled", True)):
            return False
        if not bool(getattr(rag, "prts_enabled", True)):
            return False
        return await self._source_row_enabled()

    async def _source_row_enabled(self) -> bool:
        if self._session_factory is None:
            return True
        async with self._session_factory() as session:
            row = await KnowledgeIndexRepository(session).get_source(self.source_id)
        return True if row is None else bool(row.enabled)

    async def _seed_titles(self) -> set[str]:
        """读来源行配置里的当前种子；读不到或配置坏了就退回默认种子。"""
        if self._session_factory is None:
            return set(DEFAULT_SEED_TITLES)
        try:
            async with self._session_factory() as session:
                row = await session.get(KnowledgeSource, self.source_id)
            config = dict(row.config or {}) if row is not None else {}
            return set(self.validate_config(config)["seed_titles"])
        except Exception:  # noqa: BLE001 - 只是决定 meta.origin，失败不该丢页面
            return set(DEFAULT_SEED_TITLES)

    # -- 清单与正文 -----------------------------------------------------
    async def list_refs(self, config: Mapping[str, Any]) -> list[DocumentRef]:
        """种子页各一条，再接库里最旧的 ``refresh_read_pages`` 个随读页。"""
        values = self.validate_config(config)
        refs = [
            DocumentRef(external_id=title, title=title, version=None)
            for title in values["seed_titles"]
        ]
        limit = values["refresh_read_pages"]
        if limit <= 0:
            return refs
        documents = await self._documents()
        reads = [
            document
            for document in documents.values()
            if document.meta.get("origin") == "read"
        ]
        reads.sort(key=_oldest_first)
        seen = {ref.external_id for ref in refs}
        added = 0
        for document in reads:
            if added >= limit:
                break
            if document.external_id in seen:
                continue
            seen.add(document.external_id)
            added += 1
            refs.append(
                DocumentRef(
                    external_id=document.external_id,
                    title=document.title,
                    version=None,
                )
            )
        return refs

    async def fetch(
        self, refs: Sequence[DocumentRef], config: Mapping[str, Any]
    ) -> list[SourceDocument]:
        """按 20 个标题一批取正文；缺失页跳过，种子/随读的 meta 分别处理。"""
        values = self.validate_config(config)
        seeds = set(values["seed_titles"])
        existing = await self._documents()
        documents: list[SourceDocument] = []
        for chunk in _batches(list(refs), BATCH_SIZE):
            batch = await self._client.wikitext([ref.external_id for ref in chunk])
            by_id = {ref.external_id: ref for ref in chunk}
            fetched_at = utcnow()
            for page in batch.pages:
                if page.missing:
                    continue
                ref = by_id.get(page.title) or by_id.get(page.redirect_from or "")
                if ref is None:
                    continue
                old = existing.get(ref.external_id) or existing.get(page.title)
                origin = "seed" if ref.external_id in seeds else "read"
                meta = {
                    key: value
                    for key, value in (dict(old.meta) if old is not None else {}).items()
                    if key != "chunker"
                }
                meta["origin"] = origin
                meta["revision"] = page.revision
                meta["redirect_from"] = page.redirect_from
                documents.append(
                    SourceDocument(
                        external_id=page.title or ref.external_id,
                        title=page.title or ref.external_id,
                        text=clean_page_text(page.content),
                        url=make_page_url(page.title or ref.external_id),
                        version=_revision(page.revision),
                        fetched_at=fetched_at,
                        meta=meta,
                    )
                )
        return documents

    # -- 清理 -----------------------------------------------------------
    async def prune(self, config: Mapping[str, Any]) -> int:
        """按 spec §4.5 的三条规则删过期文档，返回删除数。"""
        values = self.validate_config(config)
        if self._session_factory is None:
            return 0
        seeds = set(values["seed_titles"])
        now = utcnow()
        cutoff = now - timedelta(days=READ_PAGE_TTL_DAYS)
        async with self._session_factory() as session:
            repository = KnowledgeIndexRepository(session)
            documents = await repository.documents_of(self.source_id)
            doomed: set[str] = set()
            read_documents: list[KnowledgeDocument] = []
            for document in documents.values():
                meta = document.meta
                # 当前种子页永远是种子，不参与随读页的期限与数量清理；否则每次同步
                # 拉回来之后会立刻被旧 read_at 删掉。
                is_seed = document.title in seeds or document.external_id in seeds
                if (
                    meta.get("origin") == "seed"
                    and not is_seed
                    and not meta.get("read_at")
                ):
                    doomed.add(document.id)
                if is_seed:
                    continue
                read_at = _parse_time(meta.get("read_at"))
                if read_at is None:
                    continue
                if read_at < cutoff:
                    doomed.add(document.id)
                else:
                    read_documents.append(document)
            if len(read_documents) > values["max_pages"]:
                read_documents.sort(key=_oldest_first)
                excess = len(read_documents) - values["max_pages"]
                for document in read_documents[:excess]:
                    doomed.add(document.id)
            deleted = await repository.delete_documents(sorted(doomed))
            await session.commit()
        return deleted

    # -- 落库读取 -------------------------------------------------------
    async def _documents(self) -> dict[str, KnowledgeDocument]:
        if self._session_factory is None:
            return {}
        async with self._session_factory() as session:
            return await KnowledgeIndexRepository(session).documents_of(self.source_id)


def _batches(items: Sequence[DocumentRef], size: int) -> list[list[DocumentRef]]:
    return [list(items[start : start + size]) for start in range(0, len(items), size)]


def _revision(revision: int | None) -> str | None:
    return str(revision) if revision is not None else None


def _oldest_first(document: KnowledgeDocument) -> tuple[bool, datetime, str]:
    return (
        document.fetched_at is None,
        document.fetched_at or datetime.min,
        document.external_id,
    )


def _parse_time(value: Any) -> datetime | None:
    """``meta.read_at`` 是 ISO 字符串；统一成 naive UTC 再比较。"""
    if not isinstance(value, str) or not value:
        return None
    try:
        parsed = datetime.fromisoformat(value)
    except ValueError:
        return None
    if parsed.tzinfo is not None:
        parsed = parsed.astimezone(UTC).replace(tzinfo=None)
    return parsed


def _bounded_int(value: Any, field: str, *, minimum: int, maximum: int) -> int:
    if isinstance(value, bool) or not isinstance(value, int):
        if isinstance(value, str) and _INT_PATTERN.match(value.strip()):
            value = int(value.strip())
        else:
            raise _invalid(field, f"{field} 必须是 {minimum}–{maximum} 的整数")
    if not minimum <= value <= maximum:
        raise _invalid(field, f"{field} 必须在 {minimum}–{maximum} 之间")
    return int(value)


def _invalid(field: str, reason: str) -> AppError:
    return AppError(
        ErrorCode.VALIDATION_ERROR,
        f"PRTS 来源的 {field} 不合法",
        {"field": field, "reason": reason},
    )
