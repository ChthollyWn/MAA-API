"""PRTS 来源（M77 spec §4.5、§6）。

覆盖种子页 20 个一批、``missing`` 跳过、随读监听器入队后消费者写库与开关、
``prune`` 三条规则、刷新清单带最旧的随读页，以及配置校验。正文全部是手写夹具。
"""

from __future__ import annotations

from datetime import datetime, timedelta
from types import SimpleNamespace

import pytest

from maa_api.agent.knowledge.index.indexer import SourceDocument
from maa_api.agent.knowledge.sources.prts import (
    DEFAULT_SEED_TITLES,
    PrtsCorpusSource,
)
from maa_api.db.models import utcnow
from maa_api.domain.errors import AppError, ErrorCode
from maa_api.services.external.prts import WikiPage
from tests.agent.knowledge_sources.support import (
    SettingsStub,
    async_test,
    documents,
    make_service,
)


class FakePrts:
    """按请求的标题生成页面；``missing`` 里的页面标记为不存在。"""

    def __init__(self, missing: set[str] | None = None) -> None:
        self.missing = missing or set()
        self.batches: list[list[str]] = []

    async def wikitext(self, titles):
        requested = list(titles)
        assert len(requested) <= 20
        self.batches.append(requested)
        pages = []
        for index, title in enumerate(requested):
            missing = title in self.missing
            pages.append(
                WikiPage(
                    page_id=None if missing else index + 1,
                    title=title,
                    content="" if missing else f"== {title} ==\n{title}的正文。",
                    revision=None if missing else 100 + index,
                    timestamp=None,
                    missing=missing,
                )
            )
        return SimpleNamespace(pages=tuple(pages))


class FakePageService:
    """页面服务的监听器登记处；用例直接调用登记的监听器。"""

    def __init__(self) -> None:
        self.listeners = []

    def add_listener(self, listener) -> None:
        self.listeners.append(listener)


def _source(
    fake: FakePrts,
    factory,
    settings: SettingsStub,
    page_service: FakePageService | None = None,
) -> PrtsCorpusSource:
    return PrtsCorpusSource(
        fake,
        session_factory=factory,
        settings_provider=lambda: settings,
        page_service=page_service,
    )


@async_test
async def test_seeds_fetched_in_batches_of_twenty(db_session_factory) -> None:
    """25 个种子分两次请求；``missing`` 的跳过。"""
    titles = [f"种子{index:02}" for index in range(25)]
    fake = FakePrts(missing={"种子02"})
    source = _source(fake, db_session_factory, SettingsStub())
    config = {"seed_titles": titles}
    refs = await source.list_refs(config)
    docs = await source.fetch(refs, config)
    assert [len(batch) for batch in fake.batches] == [20, 5]
    assert len(docs) == 24
    assert "种子02" not in {doc.external_id for doc in docs}
    assert all(doc.meta["origin"] == "seed" for doc in docs)
    assert all(doc.version is not None for doc in docs)


@async_test
async def test_read_through_pushes_page(db_session_factory) -> None:
    """监听器入队后消费者写入，``meta.origin=="read"``、带 ``read_at``。"""
    fake = FakePrts()
    page_service = FakePageService()
    settings = SettingsStub()
    source = _source(fake, db_session_factory, settings, page_service)
    service = make_service(db_session_factory, source, settings)
    try:
        await source.start(service)
        assert len(page_service.listeners) == 1
        fetched_at = datetime(2026, 1, 2, 3, 4, 5)
        page = WikiPage(
            page_id=7,
            title="随读页面",
            content="== 随读页面 ==\n第一次读到的正文。",
            revision=42,
            timestamp="2026-01-02T03:04:05Z",
            missing=False,
        )
        await page_service.listeners[0](page, fetched_at)
        await source._queue.join()
        stored = await documents(db_session_factory, "prts")
        document = stored["随读页面"]
        assert document.meta["origin"] == "read"
        assert document.meta["read_at"] == fetched_at.isoformat()
        assert document.version == "42"
        assert document.title == "随读页面"
    finally:
        await service.close()


@async_test
async def test_read_through_respects_switches(db_session_factory) -> None:
    """``rag.prts_enabled=false`` 时不写入。"""
    fake = FakePrts()
    page_service = FakePageService()
    settings = SettingsStub(prts_enabled=False)
    source = _source(fake, db_session_factory, settings, page_service)
    service = make_service(db_session_factory, source, settings)
    try:
        await source.start(service)
        page = WikiPage(
            page_id=1,
            title="被开关挡住的页面",
            content="正文",
            revision=1,
            timestamp=None,
            missing=False,
        )
        await page_service.listeners[0](page, datetime(2026, 1, 2))
        await source._queue.join()
        assert await documents(db_session_factory, "prts") == {}
    finally:
        await service.close()


@async_test
async def test_prune_rules(db_session_factory) -> None:
    """移出种子且没读过的删除；读过超过 90 天的删除；超过 max_pages 删最旧的随读页。"""
    now = utcnow()
    fake = FakePrts()
    settings = SettingsStub()
    source = _source(fake, db_session_factory, settings)
    service = make_service(db_session_factory, source, settings)

    def document(external_id: str, meta: dict, *, days_old: int = 0) -> SourceDocument:
        return SourceDocument(
            external_id=external_id,
            title=external_id,
            text=f"{external_id} 的正文",
            fetched_at=now - timedelta(days=days_old),
            meta=meta,
        )

    docs = [
        document(
            "理智",
            {"origin": "seed", "read_at": (now - timedelta(days=100)).isoformat()},
        ),
        document("旧种子", {"origin": "seed"}),
        document(
            "read_old",
            {
                "origin": "read",
                "read_at": (now - timedelta(days=100)).isoformat(),
            },
            days_old=120,
        ),
    ]
    for index in range(51):
        docs.append(
            document(
                f"read_{index:03}",
                {
                    "origin": "read",
                    "read_at": (now - timedelta(days=index + 1)).isoformat(),
                },
                days_old=51 - index,
            )
        )
    try:
        await service.push_documents("prts", docs)
        deleted = await source.prune(
            {"seed_titles": ["理智"], "max_pages": 50, "refresh_read_pages": 40}
        )
        assert deleted == 3
        stored = await documents(db_session_factory, "prts")
        # 当前种子页即使读过且 ``read_at`` 很旧也不能被删（否则同步后立刻消失）。
        assert set(stored) == {
            "理智",
            *(f"read_{index:03}" for index in range(1, 51)),
        }
    finally:
        await service.close()


@async_test
async def test_refresh_includes_oldest_read_pages(db_session_factory) -> None:
    """``list_refs`` 带上最旧的 ``refresh_read_pages`` 个随读页。"""
    now = utcnow()
    fake = FakePrts()
    settings = SettingsStub()
    source = _source(fake, db_session_factory, settings)
    service = make_service(db_session_factory, source, settings)
    docs = [
        SourceDocument(
            external_id=f"read_{name}",
            title=f"随读{name}",
            text=f"正文{name}",
            fetched_at=now - timedelta(days=days),
            meta={
                "origin": "read",
                "read_at": (now - timedelta(days=days)).isoformat(),
            },
        )
        for name, days in (("a", 3), ("b", 2), ("c", 1))
    ]
    try:
        await service.push_documents("prts", docs)
        refs = await source.list_refs({"refresh_read_pages": 2})
        assert len(refs) == len(DEFAULT_SEED_TITLES) + 2
        assert [ref.external_id for ref in refs[-2:]] == ["read_a", "read_b"]
    finally:
        await service.close()


def test_config_validation() -> None:
    """种子数量/标题、``max_pages``、``refresh_read_pages`` 的边界。"""
    source = PrtsCorpusSource(
        FakePrts(), session_factory=None, settings_provider=lambda: SettingsStub()  # type: ignore[arg-type]
    )
    defaults = source.validate_config({})
    assert defaults["seed_titles"] == list(DEFAULT_SEED_TITLES)
    assert defaults["max_pages"] == 500
    assert defaults["refresh_read_pages"] == 40
    for config in (
        {"seed_titles": "理智"},
        {"seed_titles": [123]},
        {"seed_titles": ["分类:角色"]},
        {"seed_titles": [f"种子{index}" for index in range(51)]},
    ):
        with pytest.raises(AppError) as info:
            source.validate_config(config)
        assert info.value.code is ErrorCode.VALIDATION_ERROR
        assert info.value.details["field"] == "seed_titles"
    for field, value in (
        ("max_pages", 49),
        ("max_pages", 2001),
        ("refresh_read_pages", -1),
        ("refresh_read_pages", 201),
    ):
        with pytest.raises(AppError) as info:
            source.validate_config({field: value})
        assert info.value.code is ErrorCode.VALIDATION_ERROR
        assert info.value.details["field"] == field
