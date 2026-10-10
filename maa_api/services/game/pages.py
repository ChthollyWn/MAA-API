"""PRTS 通用检索领域层（规格 §5.8）：`search_prts` 与 `get_prts_page` 的回答形状。

分两层：

``search(query, limit)``
    `list=search` 取命中，再用**一次**批量分类查询推断每个结果的 `kind`（活动、关卡、干员、
    敌人、道具、卡池、集成战略、剧情或其他）。分类查询失败时 `kind` 一律给「其他」——
    检索本身能用就不该因为分类推断失败而报错。

``page(title, section, offset, max_chars)``
    取页面 wikitext（`redirects=1`，顺带拿到重定向来源与修订号）、分类与章节表；`fields`
    在命中已知模板时给结构化字段（本里程碑：活动页、关卡页），`text` 是清洗后的正文或
    指定章节的正文，超出 `max_chars` 时给 `truncated`。

约定（规格 §3.4、§3.6、§4.4）：

- 标题只走 `PrtsClient.wikitext` / `sections`，命名空间与危险字符的校验在那边，这里不重复；
- 来源信封用 ``build_envelope`` 拼；成功时至少带 wiki 取数那一条，`url` 也是
  ``https://prts.wiki/w/<标题空格换下划线>`` 的原始形状（百分号编码由前端做）；
- PRTS 自己的 `AppError` 原样往上抛，降级与信封装配由工具层负责（`GameDataUnavailable` 之类
  的兜底不在这里做）。

规格没写死、由本模块裁决的实现细节（报告里会列全）：

1. `text` 先 `strip_markup`，再把章节标题行归一成 ``== 标题 ==``：`strip_markup` 不认
   ``==``，原样留着会得到 ``==剧情==`` 这种粘连的标记；
2. `sections` 是 `PrtsClient.sections` 的层级表在前、wikitext 里能切出来的标题补在后——
   保证「列出来的标题都能用 `section` 取到正文」；
3. `search` 的 `results_total` 是**本次返回的条数**（不是 wiki 的 `totalhits`），与返回
   `results` 长度一致；
4. `truncated` 的窗口以清洗后的文本为准：`next_offset = offset + len(text窗口)`，
   `total_chars` 是清洗后全文（或该章节）的字数。
"""

from __future__ import annotations

import html
import re
from dataclasses import dataclass, fields, is_dataclass, replace
from datetime import datetime
from typing import Any, Iterable, Mapping, Sequence
from urllib.parse import quote_plus

from maa_api.domain.errors import AppError, ErrorCode
from maa_api.services.external.envelope import SourceRef, build_envelope
from maa_api.services.external.prts import PrtsClient, PrtsResult, WikiPage, WikiTextBatch
from maa_api.services.prts_parse.activity import ActivityPage, parse_activity_page
from maa_api.services.prts_parse.stage import StagePage, parse_stage_page
from maa_api.services.prts_parse.wikitext import split_sections, strip_markup

__all__ = [
    "KIND_ACTIVITY",
    "KIND_ENEMY",
    "KIND_GACHA",
    "KIND_ITEM",
    "KIND_OPERATOR",
    "KIND_OTHER",
    "KIND_ROGUELIKE",
    "KIND_STAGE",
    "KIND_STORY",
    "MAX_ACTIVITY_STAGE_REFS",
    "MAX_SNIPPET_CHARS",
    "OFFSET_MAX",
    "PAGE_URL_PREFIX",
    "SNIPPET_ELLIPSIS",
    "TEXT_MAX_CHARS",
    "TEXT_MIN_CHARS",
    "PageService",
    "classify_kind",
    "clean_section_title",
    "dataclass_to_dict",
    "default_page_service",
    "make_page_url",
    "make_search_url",
]

#: 页面链接前缀（规格 §5.8：给原始形式，URL 编码由前端做）。
PAGE_URL_PREFIX = "https://prts.wiki/w/"

#: 检索摘要的字数上限（规格 §5.8）。
MAX_SNIPPET_CHARS = 120

#: 摘要截断后的省略号。
SNIPPET_ELLIPSIS = "…"

#: `max_chars` 的取值范围（规格 §5.8）。
TEXT_MIN_CHARS = 500
TEXT_MAX_CHARS = 8000

#: `offset` 的上限：只是把拼错的超大值挡在外面（超出正文长度时返回空串）。
OFFSET_MAX = 1_000_000

#: `fields.activity.stages` 的条数上限（规格 §4.4 的结果体积纪律；总量见 `stage_count`）。
MAX_ACTIVITY_STAGE_REFS = 60

#: `kind` 的取值（规格 §5.8）。
KIND_ACTIVITY = "活动"
KIND_STAGE = "关卡"
KIND_OPERATOR = "干员"
KIND_ENEMY = "敌人"
KIND_ITEM = "道具"
KIND_GACHA = "卡池"
KIND_ROGUELIKE = "集成战略"
KIND_STORY = "剧情"
KIND_OTHER = "其他"

#: 分类里的关键词（子串匹配，大小写不敏感）。
_CATEGORY_ACTIVITY = "有活动信息的页面"
_CATEGORY_STAGE = "关卡"
_CATEGORY_OPERATOR = "干员"
_CATEGORY_ENEMY = "敌人"
_CATEGORY_ITEM = ("道具", "材料")
_CATEGORY_GACHA = "寻访"
_CATEGORY_ROGUELIKE = "集成战略"
_CATEGORY_STORY = "剧情"

#: 章节标题里的 HTML 标签与多余空白（实测 `sections` 会带 `<span>…</span>`）。
_TAG = re.compile(r"</?[A-Za-z][A-Za-z0-9]*(?:\s[^<>]*?)?/?>")
_WHITESPACE = re.compile(r"\s+")

#: 正文里的章节标题行（独占一行、首尾 `=` 数量一致）。
_HEADING = re.compile(r"^(={1,6})[ \t]*(.+?)[ \t]*\1[ \t]*$", re.M)


@dataclass(frozen=True, slots=True)
class _RawPage:
    """一次页面取数的两份原始数据：wikitext（含修订号与重定向）与尽力取到的分类、章节表。"""

    batch: WikiTextBatch
    page: WikiPage
    categories: tuple[str, ...]
    sections: tuple[str, ...]


class PageService:
    """PRTS 通用检索；``prts`` 可注入（测试与多来源装配）。"""

    def __init__(self, *, prts: PrtsClient | None = None) -> None:
        self._prts = prts

    @property
    def prts(self) -> PrtsClient:
        """PRTS 客户端；缺省现建一个（底层走进程内单例的连接）。"""
        return self._prts or PrtsClient()

    # -- 检索 -------------------------------------------------------------
    async def search(self, query: str, *, limit: int = 5) -> dict[str, Any]:
        """主命名空间检索；`limit` 的 1–10 校验在 `PrtsClient.search` 里（规格 §5.8）。"""
        result = await self.prts.search(query, limit=limit)
        entries = result.query.get("search")
        rows = [row for row in entries if isinstance(row, dict)] if isinstance(entries, list) else []

        titles = [str(row.get("title") or "") for row in rows]
        titles = [title for title in titles if title]
        kinds = await self._kinds(titles)

        results: list[dict[str, Any]] = []
        for row in rows:
            title = str(row.get("title") or "")
            if not title:
                continue
            results.append(
                {
                    "title": title,
                    "snippet": clean_snippet(row.get("snippet")),
                    "kind": kinds.get(title, KIND_OTHER),
                    "url": make_page_url(title),
                }
            )
        payload: dict[str, Any] = {
            "query": query,
            "results": results,
            "results_total": len(results),
        }
        payload.update(build_envelope([_search_source(result, query)]))
        return payload

    async def _kinds(self, titles: Sequence[str]) -> dict[str, str]:
        """一次批量分类查询推断全部命中的 `kind`；失败时全部给「其他」。"""
        if not titles:
            return {}
        try:
            mapping = await self.prts.categories(list(titles))
        except AppError:
            return {}
        kinds: dict[str, str] = {}
        for title in titles:
            kinds[title] = classify_kind(mapping.get(title) or ())
        return kinds

    # -- 页面 -------------------------------------------------------------
    async def page(
        self,
        title: str,
        *,
        section: str | None = None,
        offset: int = 0,
        max_chars: int = 4000,
    ) -> dict[str, Any]:
        """取一个页面的结构化字段、章节表与正文（规格 §5.8）。"""
        self._validate_window(offset=offset, max_chars=max_chars)
        raw = await self._load(title)
        body, sections = self._select(raw, section=section)
        text = _clean_text(body)
        window, truncated = _window(text, offset=offset, max_chars=max_chars)

        page_title = raw.page.title
        fields = _fields_for(page_title, raw.page.content)
        payload: dict[str, Any] = {
            "title": page_title,
            "redirected_from": raw.page.redirect_from,
            "revision": raw.page.revision,
            "fetched_at": raw.batch.result.fetched_at.isoformat(),
            "categories": list(raw.categories),
            "kind": classify_kind(raw.categories),
            "fields": fields,
            "sections": list(sections),
            "text": window,
            "truncated": truncated,
        }
        payload.update(build_envelope(raw.batch.source_refs()))
        return payload

    async def _load(self, title: str) -> _RawPage:
        """一次取 wikitext，再尽力取分类与章节表（取不到就退空表）。"""
        batch = await self.prts.wikitext([title])
        wanted = _validated_title(title)
        page = batch.by_title(wanted) or (batch.pages[0] if batch.pages else None)
        if page is None:
            raise AppError(
                ErrorCode.GAME_DATA_NOT_FOUND,
                f"PRTS 上没有找到页面「{wanted}」",
                {"title": wanted},
            )
        if page.missing:
            raise AppError(
                ErrorCode.GAME_DATA_NOT_FOUND,
                f"PRTS 上没有找到页面「{page.title or wanted}」",
                {"title": wanted, "resolved": page.title},
            )

        categories = await self._fetch_categories(page.title)
        sections = await self._fetch_sections(page.title)
        return _RawPage(batch=batch, page=page, categories=categories, sections=sections)

    async def _fetch_categories(self, title: str) -> tuple[str, ...]:
        """分类：取不到给空表（`kind` 落回「其他」）。"""
        try:
            mapping = await self.prts.categories([title])
        except AppError:
            return ()
        return tuple(mapping.get(title) or ())

    async def _fetch_sections(self, title: str) -> tuple[str, ...]:
        """章节表：取不到给 `[]`（规格 §5.8 明确不抛错）。"""
        try:
            result = await self.prts.sections(title)
        except AppError:
            return ()
        parsed = result.data.get("parse")
        raw = parsed.get("sections") if isinstance(parsed, dict) else None
        if not isinstance(raw, list):
            return ()
        titles: list[str] = []
        for item in raw:
            if not isinstance(item, dict) or not item.get("line"):
                continue
            cleaned = clean_section_title(str(item["line"]))
            if cleaned:
                titles.append(cleaned)
        return tuple(titles)

    def _select(self, raw: _RawPage, *, section: str | None) -> tuple[str, tuple[str, ...]]:
        """选正文：整页或一个章节；返回（wikitext 原文, 章节标题表）。

        章节标题表按页面顺序：`prts.sections` 的层级表在前（它是 toc，含子标题），wikitext
        里能切出来的标题补在后（保证「列出来的标题都能取到正文」）。
        """
        parsed = split_sections(raw.page.content)
        headings = [(item.title, item) for item in parsed if item.title]
        listed = list(raw.sections)
        for title, _item in headings:
            if title not in listed:
                listed.append(title)
        if section is None:
            return raw.page.content, tuple(listed)

        wanted = clean_section_title(section)
        for title, item in headings:
            if title == wanted or clean_section_title(title) == wanted:
                return item.body, tuple(listed)
        available = "、".join(listed) or "（没有章节）"
        raise AppError(
            ErrorCode.GAME_DATA_NOT_FOUND,
            f"页面「{raw.page.title}」没有章节「{section}」；可用章节：{available}",
            {"title": raw.page.title, "section": section, "sections": list(listed)},
        )

    @staticmethod
    def _validate_window(*, offset: int, max_chars: int) -> None:
        if isinstance(max_chars, bool) or not isinstance(max_chars, int):
            raise AppError(
                ErrorCode.INVALID_PARAMETER,
                f"max_chars 必须是 {TEXT_MIN_CHARS}–{TEXT_MAX_CHARS} 之间的整数",
                {"max_chars": str(max_chars)[:40]},
            )
        if not TEXT_MIN_CHARS <= max_chars <= TEXT_MAX_CHARS:
            raise AppError(
                ErrorCode.INVALID_PARAMETER,
                f"max_chars 必须在 {TEXT_MIN_CHARS}–{TEXT_MAX_CHARS} 之间，收到 {max_chars}",
                {"max_chars": max_chars},
            )
        if isinstance(offset, bool) or not isinstance(offset, int):
            raise AppError(
                ErrorCode.INVALID_PARAMETER,
                "offset 必须是非负整数",
                {"offset": str(offset)[:40]},
            )
        if offset < 0 or offset > OFFSET_MAX:
            raise AppError(
                ErrorCode.INVALID_PARAMETER,
                f"offset 必须在 0–{OFFSET_MAX} 之间，收到 {offset}",
                {"offset": offset},
            )


# ---------------------------------------------------------------------------
# 纯函数
# ---------------------------------------------------------------------------
def classify_kind(categories: Iterable[str]) -> str:
    """分类 → `kind`（规格 §5.8）；子串匹配、大小写不敏感，按下面的优先级取第一个命中。

    活动 → 关卡 → 集成战略 → 干员 → 敌人 → 道具 → 卡池 → 剧情 → 其他。

    两处顺序是有意的：集成战略主题页也带「分类:有活动信息的页面」，所以排在活动与关卡之后
    （活动与集成战略页之间的判定本来就是靠这条顺序）；「分类:含有内置剧情的关卡」同时含
    「关卡」与「剧情」，按关卡页处理。
    """
    folded = [str(name).casefold() for name in categories if name]
    if any(_CATEGORY_ACTIVITY in name for name in folded):
        return KIND_ACTIVITY
    if any(_CATEGORY_STAGE in name for name in folded):
        return KIND_STAGE
    if any(_CATEGORY_ROGUELIKE in name for name in folded):
        return KIND_ROGUELIKE
    if any(_CATEGORY_OPERATOR in name for name in folded):
        return KIND_OPERATOR
    if any(_CATEGORY_ENEMY in name for name in folded):
        return KIND_ENEMY
    if any(keyword in name for name in folded for keyword in _CATEGORY_ITEM):
        return KIND_ITEM
    if any(_CATEGORY_GACHA in name for name in folded):
        return KIND_GACHA
    if any(_CATEGORY_STORY in name for name in folded):
        return KIND_STORY
    return KIND_OTHER


def clean_snippet(value: Any) -> str:
    """检索摘要：去高亮标签、压成一个空格、≤ 120 字（超了截断加省略号）。"""
    text = strip_markup(str(value or ""))
    text = _WHITESPACE.sub(" ", text).strip()
    if len(text) <= MAX_SNIPPET_CHARS:
        return text
    return text[: MAX_SNIPPET_CHARS - 1] + SNIPPET_ELLIPSIS


def clean_section_title(value: str) -> str:
    """章节标题清洗：去掉 `<span>` 之类的标签、解码实体、压空白。"""
    text = _TAG.sub("", str(value or ""))
    text = _unescape(text)
    return _WHITESPACE.sub(" ", text).strip()


def _clean_text(body: str) -> str:
    """正文清洗：`strip_markup` 之后把章节标题行归一成 ``== 标题 ==``。

    `strip_markup` 不认 ``==``，原样留着会让模型看到 ``==剧情==`` 这种粘连的标记；
    这里把它压成一行可读的标题（章节标题是页面结构，删掉会让正文失去层次）。
    """
    cleaned = _HEADING.sub(lambda match: f"== {match.group(2).strip()} ==", body)
    return strip_markup(cleaned)


def make_page_url(title: str) -> str:
    """页面链接：空格换下划线，其余保持原始形式（编码由前端做）。"""
    return f"{PAGE_URL_PREFIX}{str(title).replace(' ', '_')}"


def dataclass_to_dict(value: Any) -> Any:
    """dataclass → dict 的小工具（规格 §5.8：`fields` 给可读的结构化字段）。

    - 只处理自己声明的字段，不用 `asdict` 一把梭（避免把内部字段与深层对象一起摊平）；
    - 嵌套的 dataclass 与它们的元组递归转换；
    - 不转换 datetime（`ActivityPage` 的时间字段单独在 `_activity_fields` 里 `isoformat`）。
    """
    if is_dataclass(value) and not isinstance(value, type):
        return {
            item.name: dataclass_to_dict(getattr(value, item.name)) for item in fields(value)
        }
    if isinstance(value, tuple):
        return [dataclass_to_dict(item) for item in value]
    if isinstance(value, list):
        return [dataclass_to_dict(item) for item in value]
    if isinstance(value, Mapping):
        return {str(key): dataclass_to_dict(item) for key, item in value.items()}
    return value


def _fields_for(title: str, content: str) -> dict[str, Any] | None:
    """命中已知模板时给结构化字段；都不是就返回 ``None``。

    关卡页判定：``{{普通关卡信息}}`` 系列或 ``{{突袭关卡信息}}``；活动页判定：
    ``{{活动信息}}``。本里程碑只支持这两类，后续里程碑新增解析器时在这里接。
    """
    stage = _parse_stage(title, content)
    if stage is not None:
        return {"type": "stage", "stage": _stage_fields(stage)}
    activity = _parse_activity(title, content)
    if activity is not None:
        return {"type": "activity", "activity": _activity_fields(activity)}
    return None


def _parse_stage(title: str, content: str) -> StagePage | None:
    """只在页面真的像关卡页时解析；`parse_stage_page` 对无模板页面也给空壳，这里挡掉。"""
    markers = ("{{普通关卡信息", "{{突袭关卡信息", "{{悖论模拟关卡信息")
    if not any(marker in content for marker in markers):
        return None
    parsed = parse_stage_page(content, title=title)
    if parsed.code is None and parsed.stage_id is None and parsed.stage_type is None:
        return None
    return parsed


def _parse_activity(title: str, content: str) -> ActivityPage | None:
    if "{{活动信息" not in content:
        return None
    return parse_activity_page(content, page_title=title)


def _stage_fields(stage: StagePage) -> dict[str, Any]:
    """关卡字段：`StagePage` 的同名字段 + 时间字段没有（关卡页不解析时间）。"""
    payload = dataclass_to_dict(stage)
    payload.pop("extra", None)  # 解析器的兜底袋，不给模型看
    return payload


def _activity_fields(activity: ActivityPage) -> dict[str, Any]:
    """活动字段：dataclass 同名字段，时间转 ISO 字符串，关卡引用单独列成 `stages`。

    关卡引用按 `MAX_ACTIVITY_STAGE_REFS` 截断，总量照实写在 `stage_count` 里（§4.4）。
    """
    payload: dict[str, Any] = {}
    for item in fields(activity):
        if item.name == "stages":
            continue
        value = getattr(activity, item.name)
        if isinstance(value, datetime):
            payload[item.name] = value.isoformat()
        else:
            payload[item.name] = value
    payload["stages"] = [
        {"code": ref.code, "stage_id": ref.stage_id, "page_title": ref.page_title}
        for ref in activity.stages[:MAX_ACTIVITY_STAGE_REFS]
    ]
    payload["stage_count"] = len(activity.stages)
    payload["codes"] = list(activity.codes[:MAX_ACTIVITY_STAGE_REFS])
    return payload


def _window(text: str, *, offset: int, max_chars: int) -> tuple[str, dict[str, Any] | None]:
    """按 `offset` 取最多 `max_chars` 字；截断时给 `truncated`。"""
    total = len(text)
    if offset >= total:
        return "", None
    window = text[offset : offset + max_chars]
    if offset + len(window) >= total:
        return window, None
    return window, {
        "field": "text",
        "total_chars": total,
        "next_offset": offset + len(window),
    }


def _validated_title(title: str) -> str:
    """去掉两侧空白（真正的白名单校验在 `PrtsClient.wikitext` 里）。"""
    return str(title).strip()


def _unescape(text: str) -> str:
    """HTML 实体解码；`&nbsp;` 归一成普通空格。"""
    return html.unescape(text).replace("\xa0", " ")


def _search_source(result: PrtsResult, query: str) -> SourceRef:
    """检索结果的出处：把底层 `api.php?list=search…` 换成人类可读的搜索页链接。

    `PrtsResult.source_ref()` 给的是请求 URL（`api.php`），前端会把它渲染成外链，指向
    一个 JSON 接口对用户没有意义；这里换成 PRTS 的搜索页，仍在白名单主机上。
    """
    ref = result.source_ref()
    return replace(ref, title=f"搜索：{query}", url=make_search_url(query))


def make_search_url(query: str) -> str:
    """搜索页链接：查询词按 URL 查询串编码（空格换 ``+``）。"""
    return f"{PAGE_URL_PREFIX}Special:搜索?search={quote_plus(str(query))}"


#: 进程内共享的默认门面；工具层用它（`tools/game.py` 的 `_page_service`）。
_DEFAULT: PageService | None = None


def default_page_service() -> PageService:
    """默认门面（进程内单例）。"""
    global _DEFAULT
    if _DEFAULT is None:
        _DEFAULT = PageService()
    return _DEFAULT
