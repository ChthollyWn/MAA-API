"""PRTS 客户端（规格 §5.4）：只读 JSON API，六种动作 + 固定的参数校验。

只用 SMW、Cargo、wikitext 与章节表，不取 `action=parse` 的 HTML（S5）。**不接受模型给的
URL**：标题与查询词按白名单字符校验，SMW 条件与 Cargo 的 `where` 只由固定模板生成。

返回的是解析前的 JSON 与元信息（修订号、抓取时间、是否缓存/旧值），结构化解析在
``maa_api/services/prts_parse/`` 里。
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from datetime import datetime
from typing import Any, Iterable, Mapping, Sequence

from maa_api.domain.errors import AppError, ErrorCode
from maa_api.services.external.envelope import SourceRef
from maa_api.services.external.http import ExternalClient, ExternalResult, default_external_client

__all__ = [
    "MAX_SEARCH_LIMIT",
    "MAX_TITLES_PER_REQUEST",
    "PrtsClient",
    "PrtsResult",
    "SmwPage",
    "WikiPage",
    "WikiTextBatch",
    "validate_title",
]

#: 一次 wikitext / categories 请求最多带的标题数（规格 §5.4）。
MAX_TITLES_PER_REQUEST = 20

#: `list=search` 的条数上限（规格 §5.4）。
MAX_SEARCH_LIMIT = 10

#: `action=cargoquery` 的条数上限（规格 §5.4）。
MAX_CARGO_LIMIT = 500

#: 标题里一律禁止的字符：它们只可能来自拼接错误或注入尝试。
#: `#` 也是禁止的——章节用 `section` 参数表达，不拼进标题。
_FORBIDDEN_TITLE_CHARS = frozenset("[]{}|#<>")

#: 禁止的命名空间前缀（大小写与中英文都认）。只允许主命名空间。
_NAMESPACES = (
    "用户",
    "用户讨论",
    "文件",
    "文件讨论",
    "模板",
    "模板讨论",
    "分类",
    "分类讨论",
    "特殊",
    "mediawiki",
    "help",
    "module",
    "user",
    "file",
    "template",
    "category",
    "special",
    "talk",
)

#: 标题长度上限（规格 §5.8 的 `title` 是 1–100 字）。
MAX_TITLE_CHARS = 100

#: SMW 条件：`[[属性::值]]`、`[[页面名]]`、`[[!分类:X]]`。
_CONDITION = re.compile(
    r"^\[\[(?P<neg>!)?(?P<prop>[^\[\]{}|<>#]{1,120}?)(?:::?(?P<value>[^\[\]{}|<>#;]{1,200}))?\]\]$"
)

#: Cargo 的标识符：表名、字段名、join_on 的左值。
_CARGO_IDENT = re.compile(r"^[A-Za-z_][A-Za-z0-9_.]{0,63}$")

#: 域名反查用的页面 id 形状（stageId、itemId、charId、enemyId）。
_ID_PATTERN = re.compile(r"^[A-Za-z0-9_\-/]{1,64}$")


@dataclass(frozen=True, slots=True)
class PrtsResult:
    """一次 PRTS 取数：解析前的 JSON + 来源元信息。"""

    result: ExternalResult

    @property
    def data(self) -> dict[str, Any]:
        value = self.result.data
        if not isinstance(value, dict):
            raise AppError(
                ErrorCode.EXTERNAL_RESPONSE_INVALID,
                "PRTS 返回的顶层不是对象",
                {"kind": self.result.kind},
            )
        return value

    @property
    def query(self) -> dict[str, Any]:
        """`action=query` / `action=ask` 的 `query` 段。"""
        value = self.data.get("query")
        return value if isinstance(value, dict) else {}

    def source_ref(self, *, title: str | None = None, revision: int | None = None) -> SourceRef:
        """构造来源信封里的一条出处。"""
        return SourceRef(
            kind="prts",
            label="PRTS",
            title=title,
            url=self.result.url,
            revision=revision,
            fetched_at=self.result.fetched_at,
            cached=self.result.cached,
            stale=self.result.stale,
        )


@dataclass(frozen=True, slots=True)
class SmwPage:
    """SMW 查询的一条结果。"""

    title: str
    fullurl: str | None
    printouts: Mapping[str, list[Any]]

    def first(self, key: str) -> Any:
        """某个属性的第一个值；没有就返回 ``None``。"""
        values = self.printouts.get(key) or []
        return values[0] if values else None

    def texts(self, key: str) -> list[str]:
        """某个属性的全部字符串值。"""
        return [str(value) for value in (self.printouts.get(key) or [])]


@dataclass(frozen=True, slots=True)
class WikiPage:
    """一个页面的 wikitext 与修订信息。"""

    page_id: int | None
    title: str
    content: str
    revision: int | None
    timestamp: str | None
    missing: bool
    redirect_from: str | None = None


@dataclass(frozen=True, slots=True)
class WikiTextBatch:
    """一次批量 wikitext 取数：页面表（按请求顺序）+ 来源元信息。"""

    pages: tuple[WikiPage, ...]
    result: ExternalResult

    def by_title(self, title: str) -> WikiPage | None:
        """按（重定向后的）标题取页面。"""
        for page in self.pages:
            if page.title == title:
                return page
        return None

    def source_refs(self) -> list[SourceRef]:
        """每个页面一条出处，供来源信封用。"""
        return [
            SourceRef(
                kind="prts",
                label="PRTS",
                title=page.title,
                url=f"https://prts.wiki/w/{page.title.replace(' ', '_')}",
                revision=page.revision,
                fetched_at=self.result.fetched_at,
                cached=self.result.cached,
                stale=self.result.stale,
            )
            for page in self.pages
        ]


def validate_title(title: str, *, max_chars: int = MAX_TITLE_CHARS) -> str:
    """标题白名单校验：非空、不超长、只允许主命名空间、无危险字符（规格 §3.6）。"""
    if not isinstance(title, str):
        raise AppError(ErrorCode.INVALID_PARAMETER, "标题必须是字符串", {"title": str(title)})
    cleaned = title.strip()
    if not cleaned or len(cleaned) > max_chars:
        raise AppError(
            ErrorCode.INVALID_PARAMETER,
            f"标题长度必须在 1–{max_chars} 字之间",
            {"title": cleaned[:120], "chars": len(cleaned)},
        )
    forbidden = sorted(set(cleaned) & _FORBIDDEN_TITLE_CHARS)
    if forbidden:
        raise AppError(
            ErrorCode.INVALID_PARAMETER,
            f"标题里不允许出现 {'、'.join(forbidden)}",
            {"title": cleaned[:120]},
        )
    if "\n" in cleaned or "\r" in cleaned or "\t" in cleaned:
        raise AppError(ErrorCode.INVALID_PARAMETER, "标题里不允许出现控制字符", {"title": cleaned[:120]})
    lowered = cleaned.casefold()
    for prefix in _NAMESPACES:
        marker = f"{prefix}:"
        if lowered.startswith(marker.casefold()):
            raise AppError(
                ErrorCode.INVALID_PARAMETER,
                f"只允许主命名空间的页面，不接受 {prefix} 命名空间",
                {"title": cleaned[:120], "namespace": prefix},
            )
    return cleaned


def _validate_titles(titles: Sequence[str]) -> list[str]:
    if not titles:
        raise AppError(ErrorCode.INVALID_PARAMETER, "至少要给一个标题", {})
    if len(titles) > MAX_TITLES_PER_REQUEST:
        raise AppError(
            ErrorCode.INVALID_PARAMETER,
            f"一次最多取 {MAX_TITLES_PER_REQUEST} 个标题",
            {"count": len(titles)},
        )
    return [validate_title(title) for title in titles]


def _validate_condition(condition: str) -> str:
    """SMW 条件校验：只允许固定模板生成的形状。"""
    if not isinstance(condition, str) or not _CONDITION.match(condition.strip()):
        raise AppError(
            ErrorCode.INVALID_PARAMETER,
            "SMW 条件形状不合法（只允许 [[页面]]、[[属性::值]]、[[!分类:X]]）",
            {"condition": str(condition)[:120]},
        )
    return condition.strip()


def _validate_printouts(printouts: Iterable[str]) -> list[str]:
    """SMW 打印属性名：字母、数字、下划线、连字符与中文。"""
    cleaned: list[str] = []
    for name in printouts:
        if not isinstance(name, str) or not re.fullmatch(r"[^\s|\[\]{}<>#;:]{1,40}", name):
            raise AppError(
                ErrorCode.INVALID_PARAMETER,
                f"SMW 属性名不合法：{str(name)[:40]}",
                {"printout": str(name)[:40]},
            )
        cleaned.append(name)
    return cleaned


class PrtsClient:
    """PRTS 的只读客户端；所有请求都经 `ExternalClient`。"""

    def __init__(self, client: ExternalClient | None = None) -> None:
        self._client = client

    @property
    def client(self) -> ExternalClient:
        """底层客户端；缺省用进程内单例（装配在 `app.state.external_client`）。"""
        return self._client or default_external_client()

    async def ask(
        self,
        conditions: Sequence[str],
        *,
        printouts: Sequence[str] = (),
        sort: str | None = None,
        order: str = "desc",
        limit: int = 50,
        offset: int | None = None,
        combine: str = "and",
    ) -> PrtsResult:
        """`action=ask`：SMW 查询（当期活动、卡池、关卡 id 反查都用它）。

        :param combine: ``and``（默认，条件间用空格）或 ``or``（条件间用 ``OR``）。
            SMW 的 OR 条件数量有上限（实测 8 可用、12 超限），调用方负责分块。
        """
        if not conditions:
            raise AppError(ErrorCode.INVALID_PARAMETER, "SMW 查询至少要有一个条件", {})
        if combine not in {"and", "or"}:
            raise AppError(
                ErrorCode.INVALID_PARAMETER,
                f"combine 只能是 and 或 or，收到 {combine}",
                {"combine": combine},
            )
        parts = [_validate_condition(condition) for condition in conditions]
        query = (" OR " if combine == "or" else " ").join(parts)
        for name in _validate_printouts(printouts):
            query += f"|?{name}"
        if sort is not None:
            if not re.fullmatch(r"[^\s|\[\]{}<>#;:]{1,40}", sort):
                raise AppError(
                    ErrorCode.INVALID_PARAMETER, f"排序字段不合法：{sort}", {"sort": sort}
                )
            query += f"|sort={sort}"
            if order not in {"asc", "desc"}:
                raise AppError(
                    ErrorCode.INVALID_PARAMETER, f"order 不合法：{order}", {"order": order}
                )
            query += f"|order={order}"
        if not 1 <= limit <= 500:
            raise AppError(
                ErrorCode.INVALID_PARAMETER, "limit 必须在 1–500 之间", {"limit": limit}
            )
        query += f"|limit={limit}"
        if offset is not None:
            if offset < 0:
                raise AppError(
                    ErrorCode.INVALID_PARAMETER, "offset 不能为负", {"offset": offset}
                )
            query += f"|offset={offset}"
        params = _with_format({"action": "ask", "query": query})
        result = await self.client.get_json("prts", "api", params)
        return PrtsResult(result)

    def smw_pages(self, result: PrtsResult) -> list[SmwPage]:
        """把 `action=ask` 的结果展开成 :class:`SmwPage` 列表。"""
        results = result.query.get("results")
        if not isinstance(results, dict):
            return []
        pages: list[SmwPage] = []
        for title, payload in results.items():
            if not isinstance(payload, dict):
                continue
            printouts = payload.get("printouts")
            pages.append(
                SmwPage(
                    title=str(payload.get("fulltext") or title),
                    fullurl=_fullurl(payload.get("fullurl")),
                    printouts=printouts if isinstance(printouts, dict) else {},
                )
            )
        return pages

    async def cargo(
        self,
        *,
        tables: Sequence[str],
        fields: Sequence[str],
        where: str | None = None,
        join_on: str | None = None,
        limit: int = 50,
    ) -> PrtsResult:
        """`action=cargoquery`：结构化表查询（M26 起用）。"""
        params = _with_format(
            {
                "action": "cargoquery",
                "tables": ",".join(_validate_cargo_idents(tables, "表名")),
                "fields": ",".join(_validate_cargo_idents(fields, "字段名")),
                "limit": str(limit),
            }
        )
        if not 1 <= limit <= MAX_CARGO_LIMIT:
            raise AppError(
                ErrorCode.INVALID_PARAMETER,
                f"limit 必须在 1–{MAX_CARGO_LIMIT} 之间",
                {"limit": limit},
            )
        if where:
            params["where"] = _validate_cargo_where(where)
        if join_on:
            params["join_on"] = _validate_join_on(join_on)
        result = await self.client.get_json("prts", "api", params)
        return PrtsResult(result)

    async def wikitext(self, titles: Sequence[str]) -> WikiTextBatch:
        """`action=query&prop=revisions`：页面正文（一次最多 20 个标题）。"""
        cleaned = _validate_titles(titles)
        params = _with_format(
            {
                "action": "query",
                "prop": "revisions",
                "rvprop": "content|ids|timestamp",
                "rvslots": "main",
                "redirects": "1",
                "titles": "|".join(cleaned),
            }
        )
        result = await self.client.get_json("prts", "api", params)
        return WikiTextBatch(pages=tuple(_wiki_pages(PrtsResult(result))), result=result)

    async def categories(self, titles: Sequence[str]) -> dict[str, list[str]]:
        """`prop=categories`：页面所属分类，用于推断 `kind`。"""
        cleaned = _validate_titles(titles)
        params = _with_format(
            {
                "action": "query",
                "prop": "categories",
                "clshow": "!hidden",
                "cllimit": "50",
                "titles": "|".join(cleaned),
            }
        )
        result = PrtsResult(await self.client.get_json("prts", "api", params))
        pages = result.query.get("pages")
        mapping: dict[str, list[str]] = {}
        if isinstance(pages, list):
            for page in pages:
                if not isinstance(page, dict) or not isinstance(page.get("title"), str):
                    continue
                entries = page.get("categories")
                names = [
                    str(entry.get("title", ""))
                    for entry in entries
                    if isinstance(entry, dict) and entry.get("title")
                ] if isinstance(entries, list) else []
                mapping[page["title"]] = names
        return mapping

    async def search(self, term: str, *, limit: int = 5) -> PrtsResult:
        """`list=search`：主命名空间检索。"""
        cleaned = term.strip()
        if not cleaned or len(cleaned) > MAX_TITLE_CHARS:
            raise AppError(
                ErrorCode.INVALID_PARAMETER,
                f"检索词长度必须在 1–{MAX_TITLE_CHARS} 字之间",
                {"query": cleaned[:120]},
            )
        if set(cleaned) & _FORBIDDEN_TITLE_CHARS:
            raise AppError(
                ErrorCode.INVALID_PARAMETER, "检索词里不允许出现 []{}|#<>", {"query": cleaned[:120]}
            )
        if not 1 <= limit <= MAX_SEARCH_LIMIT:
            raise AppError(
                ErrorCode.INVALID_PARAMETER,
                f"limit 必须在 1–{MAX_SEARCH_LIMIT} 之间",
                {"limit": limit},
            )
        params = _with_format(
            {
                "action": "query",
                "list": "search",
                "srsearch": cleaned,
                "srnamespace": "0",
                "srlimit": str(limit),
                "srprop": "snippet|titlesnippet|redirecttitle",
            }
        )
        result = await self.client.get_json("prts", "api", params)
        return PrtsResult(result)

    async def sections(self, title: str) -> PrtsResult:
        """`action=parse&prop=sections`：只取章节表，不取 HTML（S27）。"""
        cleaned = validate_title(title)
        params = _with_format(
            {"action": "parse", "prop": "sections", "page": cleaned}
        )
        result = await self.client.get_json("prts", "api", params)
        return PrtsResult(result)


def _with_format(params: Mapping[str, str]) -> dict[str, str]:
    """固定带 `format=json&formatversion=2`（规格 §5.4）。"""
    merged = dict(params)
    merged["format"] = "json"
    merged["formatversion"] = "2"
    return merged


def _fullurl(value: Any) -> str | None:
    """SMW 返回的 `//prts.wiki/w/xxx` 补上协议。"""
    if not isinstance(value, str) or not value:
        return None
    if value.startswith("//"):
        return f"https:{value}"
    return value


def _wiki_pages(result: PrtsResult) -> list[WikiPage]:
    """把 `prop=revisions` 的响应展开成 :class:`WikiPage`。"""
    raw_pages = result.query.get("pages")
    redirects = {
        item.get("from"): item.get("to")
        for item in (result.query.get("redirects") or [])
        if isinstance(item, dict)
    }
    pages: list[WikiPage] = []
    if not isinstance(raw_pages, list):
        return pages
    for page in raw_pages:
        if not isinstance(page, dict):
            continue
        title = str(page.get("title") or "")
        missing = bool(page.get("missing"))
        content = ""
        revision = None
        timestamp = None
        revisions = page.get("revisions")
        if isinstance(revisions, list) and revisions:
            first = revisions[0] if isinstance(revisions[0], dict) else {}
            revision = first.get("revid") if isinstance(first.get("revid"), int) else None
            timestamp = first.get("timestamp") if isinstance(first.get("timestamp"), str) else None
            slots = first.get("slots")
            main = slots.get("main") if isinstance(slots, dict) else None
            if isinstance(main, dict) and isinstance(main.get("content"), str):
                content = main["content"]
        pages.append(
            WikiPage(
                page_id=page.get("pageid") if isinstance(page.get("pageid"), int) else None,
                title=title,
                content=content,
                revision=revision,
                timestamp=timestamp,
                missing=missing,
                redirect_from=next(
                    (source for source, target in redirects.items() if target == title), None
                ),
            )
        )
    return pages


def _validate_cargo_idents(values: Sequence[str], label: str) -> list[str]:
    cleaned: list[str] = []
    for value in values:
        if not isinstance(value, str) or not _CARGO_IDENT.match(value):
            raise AppError(
                ErrorCode.INVALID_PARAMETER,
                f"Cargo {label}不合法：{str(value)[:40]}",
                {"value": str(value)[:40]},
            )
        cleaned.append(value)
    if not cleaned:
        raise AppError(ErrorCode.INVALID_PARAMETER, f"Cargo {label}不能为空", {})
    return cleaned


def _validate_cargo_where(where: str) -> str:
    """Cargo 的 `where`：只由固定模板生成，这里再挡一次注释与多语句。"""
    if not isinstance(where, str) or len(where) > 1000:
        raise AppError(ErrorCode.INVALID_PARAMETER, "Cargo where 不合法", {})
    if ";" in where or "--" in where or "/*" in where or "<" in where or ">" in where:
        raise AppError(
            ErrorCode.INVALID_PARAMETER,
            "Cargo where 里不允许出现 ; 、注释符号或尖括号",
            {"where": where[:120]},
        )
    return where


def _validate_join_on(join_on: str) -> str:
    """Cargo 的 `join_on`：逗号分隔的 `左 = 右` 对，两边都是标识符。"""
    if not isinstance(join_on, str) or not join_on.strip():
        raise AppError(ErrorCode.INVALID_PARAMETER, "Cargo join_on 不合法", {})
    for pair in join_on.split(","):
        if not re.fullmatch(r"\s*[A-Za-z_][A-Za-z0-9_.]*\s*=\s*[A-Za-z_][A-Za-z0-9_.]*\s*", pair):
            raise AppError(
                ErrorCode.INVALID_PARAMETER,
                f"Cargo join_on 不合法：{pair[:60]}",
                {"join_on": pair[:60]},
            )
    return join_on


#: 供领域层构造条件用的便捷函数：`id_condition("关卡id", "act3break_01")`。
def id_condition(prop: str, value: str) -> str:
    """按固定模板拼一条 `[[属性::值]]` 条件；值只允许 id 形状。"""
    if not _ID_PATTERN.match(value or ""):
        raise AppError(
            ErrorCode.INVALID_PARAMETER,
            f"id 形状不合法：{str(value)[:40]}",
            {"value": str(value)[:40]},
        )
    return _validate_condition(f"[[{prop}::{value}]]")


#: 供领域层构造条件用的便捷函数：`page_condition("7-18 爱国者之死")`。
def page_condition(title: str) -> str:
    """按固定模板拼一条 `[[页面名]]` 条件；标题走同一套白名单校验。"""
    return _validate_condition(f"[[{validate_title(title)}]]")


#: 时间戳：SMW 的 `timestamp` 是 Unix 秒字符串（规格 §2.1）。
def timestamp_seconds(value: Any) -> int | None:
    """SMW 时间属性 → Unix 秒；解析不了返回 ``None``。"""
    if isinstance(value, dict):
        raw = value.get("timestamp")
    else:
        raw = value
    if isinstance(raw, bool):
        return None
    if isinstance(raw, int):
        return raw
    if isinstance(raw, str) and re.fullmatch(r"-?\d{1,12}", raw.strip()):
        return int(raw)
    return None


#: 供解析层复用的时间字段顺序（SMW printout 是列表）。
def first_timestamp(values: Sequence[Any]) -> int | None:
    for value in values:
        parsed = timestamp_seconds(value)
        if parsed is not None:
            return parsed
    return None


def format_moment(seconds: int) -> datetime:
    """Unix 秒 → 本机时区的带时区 datetime（PRTS 的 `raw` 是 UTC，展示按本机时区）。"""
    return datetime.fromtimestamp(seconds).astimezone()
