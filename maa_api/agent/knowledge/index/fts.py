"""检索式生成、FTS5 全文查询与短查询的 LIKE 回退（M76 spec §4.2、v3 docs/07 §2.3）。

全文路按词长分流：

1. 去掉首尾空白后不足 3 个字符：整串走 ``LIKE``。
2. 否则按空白与中英文标点切词（不切连字符，保住 ``1-7`` 这类关卡代号）。
   长度不少于 3 的词进 FTS：连续中文按三字一组滑动，英文与数字整词保留；
   每组加双引号转义后用 ``OR`` 连接，最多 :data:`MAX_FTS_GROUPS` 组。
3. 一个能进 FTS 的词都没有时，每个短词各写一个 ``LIKE`` 条件，用 ``AND`` 连接。
4. ``LIKE`` 查询扫 ``knowledge_chunk`` 的 ``text`` 与 ``heading``，标题命中计
   3 次，最多扫 :data:`LIKE_CANDIDATE_SCAN` 行。

用户输入的 FTS5 语法字符（``"``、``AND``、``(`` 等）一律当普通字符。
"""

from __future__ import annotations

import re
from collections.abc import Sequence
from dataclasses import dataclass

from sqlalchemy import bindparam, text
from sqlalchemy.ext.asyncio import AsyncSession

from maa_api.domain.errors import AppError, ErrorCode

__all__ = [
    "LIKE_CANDIDATE_SCAN",
    "LIKE_MIN_CHARS",
    "MAX_FTS_GROUPS",
    "FtsPlan",
    "LexicalHit",
    "build_fts_plan",
    "search_lexical",
]

#: 少于这么多字符的词不进 FTS，改用 ``LIKE``。
LIKE_MIN_CHARS = 3
#: 一次检索最多生成的 FTS 词组数。
MAX_FTS_GROUPS = 64
#: ``LIKE`` 路径最多扫描的分块行数。
LIKE_CANDIDATE_SCAN = 2000
#: ``LIKE`` 片段在命中位置前后各取多少字。
LIKE_SNIPPET_RADIUS = 40

#: 空白与中英文标点都当分隔符；连字符与下划线保留在词里。
_WORD_SPLIT_RE = re.compile(r"[^\w-]+", re.UNICODE)
_CJK_RE = re.compile(r"[\u3400-\u4dbf\u4e00-\u9fff\uf900-\ufaff]")


@dataclass(frozen=True, slots=True)
class FtsPlan:
    """一条检索词的两条路：``match`` 为空表示只走 ``LIKE``。"""

    match: str | None
    like_terms: tuple[str, ...]


@dataclass(frozen=True, slots=True)
class LexicalHit:
    """全文路的一条命中：分块 id 与片段。"""

    chunk_id: int
    snippet: str


def build_fts_plan(query: str) -> FtsPlan:
    """按 07 §2.3 的四条规则生成检索式；没有任何可检索的字时抛 ``RETRIEVAL_QUERY_INVALID``。"""
    stripped = (query or "").strip()
    words = [word for word in _WORD_SPLIT_RE.split(stripped) if _is_searchable(word)]
    if not words:
        raise AppError(
            ErrorCode.RETRIEVAL_QUERY_INVALID,
            "检索词为空或只有标点",
            {"reason": "empty_query"},
        )

    if len(stripped) < LIKE_MIN_CHARS:
        return FtsPlan(match=None, like_terms=(stripped,))

    groups: list[str] = []
    short_words: list[str] = []
    for word in words:
        if len(word) < LIKE_MIN_CHARS:
            short_words.append(word)
            continue
        for group in _fts_groups(word):
            if group not in groups:
                groups.append(group)
            if len(groups) >= MAX_FTS_GROUPS:
                break
        if len(groups) >= MAX_FTS_GROUPS:
            break

    if not groups:
        return FtsPlan(match=None, like_terms=_dedupe(short_words))
    match = " OR ".join(f'"{group.replace(chr(34), chr(34) * 2)}"' for group in groups)
    return FtsPlan(match=match, like_terms=())


def _is_searchable(word: str) -> bool:
    """至少含一个字母或数字才算词；纯连字符、下划线不算。"""
    return any(char.isalnum() for char in word)


def _fts_groups(word: str) -> list[str]:
    """含中文的词生成重叠三字组；不含中文的词整词保留。"""
    if _CJK_RE.search(word):
        return [word[index : index + 3] for index in range(len(word) - 2)]
    return [word]


def _dedupe(items: list[str]) -> tuple[str, ...]:
    seen: list[str] = []
    for item in items:
        if item not in seen:
            seen.append(item)
    return tuple(seen)


async def search_lexical(
    session: AsyncSession,
    plan: FtsPlan,
    *,
    source_ids: Sequence[str],
    limit: int,
) -> list[LexicalHit]:
    """全文路：有 ``match`` 走 FTS5，否则走 ``LIKE`` 并在 Python 里按命中次数排序。"""
    sources = list(dict.fromkeys(source_ids))
    if not sources or limit <= 0:
        return []
    if plan.match is not None:
        return await _search_fts(session, plan.match, sources, limit)
    if not plan.like_terms:
        return []
    return await _search_like(session, plan.like_terms, sources, limit)


async def _search_fts(
    session: AsyncSession, match: str, sources: Sequence[str], limit: int
) -> list[LexicalHit]:
    statement = text(
        """
        SELECT kc.id,
               snippet(knowledge_chunk_fts, 0, '', '', '…', 24) AS snippet
        FROM knowledge_chunk_fts
        JOIN knowledge_chunk kc ON kc.id = knowledge_chunk_fts.rowid
        JOIN knowledge_document kd ON kd.id = kc.document_id
        WHERE knowledge_chunk_fts MATCH :match
          AND kd.source_id IN :source_ids
        ORDER BY bm25(knowledge_chunk_fts, 1.0, 2.0) ASC, kc.id ASC
        LIMIT :limit
        """
    ).bindparams(
        bindparam("source_ids", expanding=True),
        bindparam("match"),
        bindparam("limit"),
    )
    rows = (
        await session.execute(
            statement,
            {"match": match, "source_ids": list(sources), "limit": limit},
        )
    ).all()
    return [LexicalHit(chunk_id=int(row.id), snippet=str(row.snippet or "")) for row in rows]


async def _search_like(
    session: AsyncSession, terms: Sequence[str], sources: Sequence[str], limit: int
) -> list[LexicalHit]:
    conditions: list[str] = []
    parameters: dict[str, object] = {
        "source_ids": list(sources),
        "scan": LIKE_CANDIDATE_SCAN,
    }
    for index, term in enumerate(terms):
        key = f"term_{index}"
        parameters[key] = f"%{_escape_like(term)}%"
        conditions.append(
            f"(kc.text LIKE :{key} ESCAPE '\\' OR kc.heading LIKE :{key} ESCAPE '\\')"
        )
    statement = text(
        f"""
        SELECT kc.id, kc.text, kc.heading
        FROM knowledge_chunk kc
        JOIN knowledge_document kd ON kd.id = kc.document_id
        WHERE kd.source_id IN :source_ids
          AND {' AND '.join(conditions)}
        ORDER BY kc.id ASC
        LIMIT :scan
        """
    ).bindparams(
        bindparam("source_ids", expanding=True),
        bindparam("scan"),
        *[bindparam(f"term_{index}") for index in range(len(terms))],
    )
    rows = (await session.execute(statement, parameters)).all()

    scored: list[tuple[int, int, str]] = []
    for row in rows:
        body = str(row.text or "")
        heading = str(row.heading or "")
        score = 0
        for term in terms:
            score += _count_hits(body, term)
            score += 3 * _count_hits(heading, term)
        if score:
            scored.append((score, int(row.id), _like_snippet(body, terms)))
    scored.sort(key=lambda item: (-item[0], item[1]))
    return [LexicalHit(chunk_id=chunk_id, snippet=snippet) for _, chunk_id, snippet in scored[:limit]]


def _escape_like(term: str) -> str:
    """转义 ``LIKE`` 的通配符；反斜杠要先转义。"""
    return term.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_")


def _count_hits(text: str, term: str) -> int:
    if not text or not term:
        return 0
    return text.casefold().count(term.casefold())


def _like_snippet(body: str, terms: Sequence[str]) -> str:
    """取第一个命中位置前后各 40 字；标题命中的行退回正文前 200 字。"""
    folded = body.casefold()
    best: tuple[int, int] | None = None
    for term in terms:
        position = folded.find(term.casefold())
        if position < 0:
            continue
        length = len(term)
        if best is None or position < best[0]:
            best = (position, length)
    if best is None:
        return body[:200]
    position, length = best
    start = max(0, position - LIKE_SNIPPET_RADIUS)
    end = min(len(body), position + length + LIKE_SNIPPET_RADIUS)
    return body[start:end]
