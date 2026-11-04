"""M76 T3：检索式生成、FTS5 查询与 LIKE 回退（spec §4.2、§6、v03 docs/07 §2.3）。"""

from __future__ import annotations

import asyncio
from typing import Any

import pytest

from maa_api.agent.knowledge.index.fts import (
    FtsPlan,
    build_fts_plan,
    search_lexical,
)
from maa_api.db.models import KnowledgeChunk, KnowledgeDocument, KnowledgeSource
from maa_api.domain.errors import AppError, ErrorCode


def test_plan_short_queries_use_like() -> None:
    plan = build_fts_plan("理智")
    assert isinstance(plan, FtsPlan)
    assert plan.match is None
    assert plan.like_terms == ("理智",)

    spaced = build_fts_plan("精二 材料")
    assert spaced.match is None
    assert spaced.like_terms == ("精二", "材料")

    padded = build_fts_plan("  理智  ")
    assert padded.like_terms == ("理智",)


def test_plan_trigrams_and_ascii_words() -> None:
    chinese = build_fts_plan("怎么刷固源岩")
    assert chinese.match is not None
    for group in ("怎么刷", "么刷固", "刷固源", "固源岩"):
        assert f'"{group}"' in chinese.match
    assert chinese.match.count(" OR ") == 3
    assert chinese.like_terms == ()

    ascii_words = build_fts_plan("MaaCore 1-7")
    assert ascii_words.match == '"MaaCore" OR "1-7"'
    assert ascii_words.like_terms == ()

    mixed = build_fts_plan("固源岩 精二")
    assert mixed.match == '"固源岩"'
    assert mixed.like_terms == ()


def test_plan_escapes_quotes_and_operators(db_session_factory) -> None:
    plan = build_fts_plan('固源岩 " AND (')
    assert plan.match is not None
    assert '"固源岩"' in plan.match
    assert '"AND"' in plan.match
    # FTS5 的语法字符都成了普通词，不会出现在表达式里。
    assert "(" not in plan.match
    assert plan.match.count('"') % 2 == 0

    async def scenario() -> None:
        await _seed_chunks(db_session_factory, [("builtin", "标题", "固源岩 AND 内容")])
        async with db_session_factory() as session:
            hits = await search_lexical(
                session, plan, source_ids=["builtin"], limit=5
            )
        assert hits

    asyncio.run(scenario())


def test_plan_rejects_punctuation_only() -> None:
    for query in ("？？？", "  ", "", "，。！"):
        with pytest.raises(AppError) as exc_info:
            build_fts_plan(query)
        assert exc_info.value.code == ErrorCode.RETRIEVAL_QUERY_INVALID


async def _seed_chunks(
    session_factory: Any, specs: list[tuple[str, str | None, str]]
) -> list[int]:
    """specs 的每项是 ``(source_id, heading, text)``；返回按插入顺序的分块 id。"""
    source_ids = list(dict.fromkeys(source_id for source_id, _, _ in specs))
    async with session_factory() as session:
        for source_id in source_ids:
            session.add(
                KnowledgeSource(
                    id=source_id,
                    kind="builtin" if source_id == "builtin" else "user",
                    title=source_id,
                )
            )
        await session.commit()
    async with session_factory() as session:
        for source_id in source_ids:
            session.add(
                KnowledgeDocument(
                    id=f"doc-{source_id}",
                    source_id=source_id,
                    external_id="e",
                    title=f"{source_id} 文档",
                    content_hash="h",
                )
            )
        await session.commit()
    chunks = [
        KnowledgeChunk(
            document_id=f"doc-{source_id}",
            ord=index,
            heading=heading,
            text=text,
            char_count=len(text),
        )
        for index, (source_id, heading, text) in enumerate(specs)
    ]
    async with session_factory() as session:
        session.add_all(chunks)
        await session.commit()
    return [int(chunk.id or 0) for chunk in chunks]


def test_search_lexical_ranks_heading_matches_first(db_session_factory) -> None:
    specs = [
        ("builtin", "地图", "普通内容 固源岩 一次"),
        ("builtin", "固源岩", "普通内容 没有关键词"),
        ("builtin", "其他一", "无关文本一"),
        ("builtin", "其他二", "无关文本二"),
        ("builtin", "其他三", "无关文本三"),
        ("builtin", "其他四", "无关文本四"),
    ]

    async def scenario() -> list[int]:
        ids = await _seed_chunks(db_session_factory, specs)
        plan = build_fts_plan("固源岩")
        assert plan.match is not None
        async with db_session_factory() as session:
            hits = await search_lexical(
                session, plan, source_ids=["builtin"], limit=10
            )
        assert [hit.chunk_id for hit in hits] == [ids[1], ids[0]]
        assert hits[0].snippet
        return [hit.chunk_id for hit in hits]

    assert asyncio.run(scenario()) == [2, 1]


def test_search_lexical_like_counts_hits_and_filters_sources(
    db_session_factory,
) -> None:
    specs = [
        ("builtin", "无关", "理智理智"),
        ("builtin", "理智", "无关文本"),
        ("user", "无关", "理智"),
    ]

    async def scenario() -> None:
        ids = await _seed_chunks(db_session_factory, specs)
        plan = build_fts_plan("理智")
        assert plan.match is None
        async with db_session_factory() as session:
            hits = await search_lexical(
                session, plan, source_ids=["builtin"], limit=10
            )
        # 标题命中计 3 次，排在正文命中两次的前面；user 来源被过滤掉。
        assert [hit.chunk_id for hit in hits] == [ids[1], ids[0]]
        assert "理智" in hits[1].snippet
        assert all(hit.chunk_id != ids[2] for hit in hits)

    asyncio.run(scenario())


def test_search_lexical_escapes_like_wildcards(db_session_factory) -> None:
    specs = [
        ("builtin", None, "a_b理智"),
        ("builtin", None, "axb 理智"),
    ]

    async def scenario() -> None:
        ids = await _seed_chunks(db_session_factory, specs)
        plan = build_fts_plan("a_")
        assert plan.like_terms == ("a_",)
        async with db_session_factory() as session:
            hits = await search_lexical(
                session, plan, source_ids=["builtin"], limit=10
            )
        # ``_`` 被当成普通字符：只命中真的含 "a_" 的那一块。
        assert [hit.chunk_id for hit in hits] == [ids[0]]

    asyncio.run(scenario())
