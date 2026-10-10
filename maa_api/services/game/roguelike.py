"""集成战略领域层（规格 §8.4）：`get_roguelike_info` 的回答形状。

主题页（傀影与猩红孤钻 / 水月与深蓝之树 / 探索者的银凇止境 / 萨卡兹的无终奇语 /
岁的界园志异 / 沉沦者的黑流树海）是 PRTS 上最大的页面之一（实测 170–261 KB），
所以正文**一律按章节取**（S27）：

1. `action=parse&prop=sections` 拿章节表（`PrtsClient.sections`）；
2. `action=parse&section=N&prop=wikitext|revid` 只取那一节（:meth:`RoguelikeService._section_wikitext`），
   永远不整页拉；节内再用 `split_sections` 找子块（`squads` 的分队表就是这么定位的）。

四个 ``topic``：

``overview``
    前言（`section=0`）的 `{{活动信息}}` 字段与清洗后的开场白。字段复用活动页解析器
    （`parse_activity_page`），与 `get_prts_page` 的 `fields.activity` 同形。
``squads``
    「开始探索」节里的分队表 → ``fields.squads``（名称、效果、解锁条件）；``text`` 是
    表前的说明文字，不重复罗列行内容。
``relic``
    给了 ``name`` 就走单页 `{{收藏品/common}}`（规格 §8.4「藏品优先取单页」），
    ``text`` 按「主题 → 售价 / 效果」渲染成一段可读摘要；没给就返回主题页的
    「收藏品一览」节，子页面标题放在 ``fields.index_page``，并标
    ``unavailable=["relic_list"]``——那一节的正文是整页嵌入的，本服务不展开。
``section``
    按 ``name``（章节标题或纯数字章节序号）取一节，正文带截断与 ``next_offset``。

约定（规格 §3.4、§4.1、§4.4）：

- 返回顶层带来源信封；正文是外部页面数据，``untrusted: true``；
- 单次正文窗口 ``TEXT_MAX_CHARS`` 字（口径同 `PageService.page`），超出给
  ``truncated: {field, total_chars, next_offset}``；要接着读用 `get_prts_page` 的
  ``offset``（同一标题、同一章节）；
- BlackFlow 的结果带 ``notes``：上游（MAA v6.18.0）的黑流树海策略还不完整，开局分队
  （如特勤分队）有精二机械师一类前置（规格 §2.5、§8.4）。

实现裁决（规格没写死、由本模块定，报告里列全）：

1. **章节 wikitext 由领域层直接经 `ExternalClient` 取**：`action=parse` 只取一节不在
   `PrtsClient` 的方法集里，而 `services/external/*` 本次不改；`page` / `section` /
   `prop` 三个参数本来就在 `sources._PRTS_PARAMS` 白名单里，标题仍走 `validate_title`。
2. 主题默认取常量表最后一个（BlackFlow）：`分类:集成战略` 的成员里它是最新的一页，
   而本仓库的 PRTS 客户端没有 `list=categorymembers`，不为「最新」单开取数路径。
3. ``theme`` 接受主题键（``JieGarden``）或页面标题（``岁的界园志异``），两侧空白归一。
4. **分队表解析**：在节内找第一个表头含「分队」的 wikitable，按 ``|-`` 切行、按行首
   ``|`` 与 ``||`` 切单元格（深度感知，模板/链接里的竖线不算分隔符），单元格先剥属性
   （``|style="…"|内容``）再 `strip_markup`。找不到表格时 ``squads = []`` 并标
   ``unavailable=["squads"]``，不报错——页面改版不该让整条链路失败。
5. ``section`` 的 ``name`` 先按清洗后的标题**精确**匹配，再按子串匹配，最后接受纯数字
   的章节序号；都对不上报 `GAME_DATA_NOT_FOUND`，报错里列出可用章节。
6. `{{收藏品/common}}` 的逐主题字段按 ``主题N`` / ``角标N`` / ``售价N`` / ``效果N`` /
   ``获取条件N`` / ``解锁条件N`` 归并成 ``themes[]``（``N`` 从 1 数起，跳空即停）。
7. **表格单元格里的单中括号外链先剥成显示名**：黑流树海等主题把职业名写成
   ``[https://prts.wiki/w/干员一览#?subProfession=… 本源术师]``，`strip_markup` 按 M25 的
   口径原样保留，一条分队效果里就是几百字符的 URL；只在单元格（与藏品字段）里额外
   清洗一次，正文 ``text`` 仍走 `strip_markup` 的老口径。
"""

from __future__ import annotations

import re
from collections.abc import Iterator, Mapping, Sequence
from dataclasses import dataclass
from typing import Any

from maa_api.domain.errors import AppError, ErrorCode
from maa_api.services.external.envelope import SourceRef, build_envelope
from maa_api.services.external.http import ExternalResult
from maa_api.services.external.prts import PrtsClient, PrtsResult, validate_title
from maa_api.services.prts_parse.activity import (
    ACTIVITY_TEMPLATE,
    parse_activity_page,
    template_param,
)
from maa_api.services.prts_parse.wikitext import find_templates, strip_markup

__all__ = [
    "BLACKFLOW_NOTE",
    "DEFAULT_THEME",
    "RELIC_TEMPLATES",
    "ROGUELIKE_THEMES",
    "THEME_LABELS",
    "TEXT_MAX_CHARS",
    "TOPICS",
    "RoguelikeService",
    "default_roguelike_service",
]

#: 主题常量表（按上线顺序）；默认主题取最后一个（规格 §8.4「默认最新主题」）。
ROGUELIKE_THEMES: tuple[str, ...] = (
    "Phantom",
    "Mizuki",
    "Sami",
    "Sarkaz",
    "JieGarden",
    "BlackFlow",
)

#: 主题键 → PRTS 页面标题（实测 2026-10-10 的 `分类:集成战略` 成员）。
THEME_LABELS: Mapping[str, str] = {
    "Phantom": "傀影与猩红孤钻",
    "Mizuki": "水月与深蓝之树",
    "Sami": "探索者的银凇止境",
    "Sarkaz": "萨卡兹的无终奇语",
    "JieGarden": "岁的界园志异",
    "BlackFlow": "沉沦者的黑流树海",
}

#: 默认主题：最新的一期（BlackFlow）。
DEFAULT_THEME: str = ROGUELIKE_THEMES[-1]

#: `topic` 的取值（规格 §8.4）。
TOPICS: tuple[str, ...] = ("overview", "squads", "relic", "section")

#: 正文窗口的字数上限（与 `PageService.page` 的工具层默认值一致）。
TEXT_MAX_CHARS = 4000

#: BlackFlow 的说明（规格 §2.5、§8.4：上游策略尚不完整、有前置条件）。
BLACKFLOW_NOTE = (
    "BlackFlow（沉沦者的黑流树海）在上游 MAA v6.18.0 里的策略尚不完整："
    "开局分队有前置条件（如特勤分队需要精二机械师），参数与路线仅供参考。"
)

#: 藏品页用的模板（按优先级；`{{收藏品/common}}` 是实测的单页写法）。
RELIC_TEMPLATES: tuple[str, ...] = ("收藏品/common", "收藏品")

#: 分队表前的说明文字锚点（六个主题页都有这一行）。
_SQUAD_ANCHOR = "选择分队"

#: 收藏品页最多读几个主题段（够六期主题用）。
_MAX_RELIC_THEMES = 8

#: 正文里的章节标题行；`strip_markup` 不认 ``==``，先归一成 ``== 标题 ==``。
_HEADING_LINE = re.compile(r"^(={1,6})[ \t]*(.+?)[ \t]*\1[ \t]*$", re.M)

#: 章节序号（`action=parse&section=N` 只接受非负整数）。
_SECTION_INDEX = re.compile(r"^\d{1,4}$")

#: 单元格属性：``|style="…"|内容``。
_CELL_ATTRIBUTE = re.compile(
    r"^\s*(?:style|class|colspan|rowspan|align|valign|width|height|id|title|data-[A-Za-z0-9_-]+)\s*=",
    re.I,
)

#: 表头里的「分队」列。
_SQUAD_HEADER = re.compile(r"!\s*分队")

#: 单元格里的单中括号外链：``[https://… 显示名]`` → ``显示名``（见模块 docstring 第 7 条）。
_EXTERNAL_LINK = re.compile(r"\[(?:https?://|//)[^\s\]]+(?:\s+([^\]]*))?\]")

#: `{{参阅|<页面>|<说明>}}` 与 `{{#lsth:<页面>|<锚点>}}`：收藏品一览节指向的子页面。
_INDEX_PAGE_TEMPLATES = ("参阅", "#lsth")


@dataclass(frozen=True, slots=True)
class _SectionRef:
    """章节表的一行。``index`` 为空串表示这一节在 toc 里但取不到（整页嵌入的子标题）。"""

    index: str
    level: int
    title: str

    def as_dict(self) -> dict[str, Any]:
        return {"index": self.index, "level": self.level, "title": self.title}


@dataclass(frozen=True, slots=True)
class _SectionText:
    """取到的一节：wikitext + 修订号 + 页面标题。"""

    wikitext: str
    revision: int | None
    page_title: str
    result: ExternalResult


@dataclass(frozen=True, slots=True)
class _WikiText:
    """取到的一整页：wikitext + 修订号 + 页面标题。"""

    content: str
    revision: int | None
    page_title: str
    result: ExternalResult


class RoguelikeService:
    """集成战略主题页的按章节取数；``prts`` 可注入（测试与多来源装配）。"""

    def __init__(self, *, prts: PrtsClient | None = None) -> None:
        self._prts = prts

    @property
    def prts(self) -> PrtsClient:
        """PRTS 客户端；缺省现建一个（底层走进程内单例的连接）。"""
        return self._prts or PrtsClient()

    # -- 入口 -------------------------------------------------------------
    async def info(
        self,
        *,
        theme: str | None = None,
        topic: str = "overview",
        name: str | None = None,
    ) -> dict[str, Any]:
        """按主题与 ``topic`` 取集成战略资料（规格 §8.4）。"""
        self._validate_topic(topic)
        key, title = self._resolve_theme(theme)
        cleaned_name = _clean_name(name)
        if topic == "section" and cleaned_name is None:
            # 参数错在取数之前就报出来，别为一次注定失败的调用打网。
            raise AppError(
                ErrorCode.INVALID_PARAMETER,
                "topic=section 必须给 name（章节标题或章节序号）",
                {"field": "name", "topic": "section"},
            )

        if topic == "relic" and cleaned_name is not None:
            return await self._relic_page(key, cleaned_name)

        sections = await self._load_sections(title)

        if topic == "overview":
            return await self._overview(key, title, sections)
        if topic == "squads":
            return await self._squads(key, title, sections)
        if topic == "relic":
            return await self._relic_index(key, title, sections)
        return await self._section(key, title, sections, cleaned_name)

    # -- 主题与参数 -------------------------------------------------------
    @staticmethod
    def _validate_topic(topic: str) -> None:
        if topic not in TOPICS:
            raise AppError(
                ErrorCode.INVALID_PARAMETER,
                f"topic 只能是 {'、'.join(TOPICS)}，收到「{str(topic)[:40]}」",
                {"topic": str(topic)[:40], "allowed": list(TOPICS)},
            )

    @staticmethod
    def _resolve_theme(theme: str | None) -> tuple[str, str]:
        """主题键或页面标题 →（主题键, 页面标题）；空值取最新主题。"""
        if theme is None:
            return DEFAULT_THEME, THEME_LABELS[DEFAULT_THEME]
        wanted = str(theme).strip()
        if wanted in THEME_LABELS:
            return wanted, THEME_LABELS[wanted]
        for key, label in THEME_LABELS.items():
            if label == wanted:
                return key, label
        raise AppError(
            ErrorCode.INVALID_PARAMETER,
            "theme 只能是 %s 之一（也接受主题页标题，如「岁的界园志异」）"
            % "、".join(ROGUELIKE_THEMES),
            {"theme": wanted[:40], "allowed": list(ROGUELIKE_THEMES)},
        )

    # -- 取数 -------------------------------------------------------------
    async def _load_sections(self, title: str) -> tuple[_SectionRef, ...]:
        """`action=parse&prop=sections`：章节表；返回里没有 `sections` 段时给空表。

        取数本身失败（页面不存在、来源不可用）时 `PrtsClient.sections` 的 `AppError`
        原样往上抛——六个主题页都有章节表，读不到就没有可用的降级结果。
        """
        result = await self.prts.sections(title)
        parsed = result.data.get("parse")
        raw = parsed.get("sections") if isinstance(parsed, dict) else None
        sections: list[_SectionRef] = []
        if isinstance(raw, list):
            for item in raw:
                if not isinstance(item, dict) or not item.get("line"):
                    continue
                line = _clean_text(str(item["line"])).strip()
                if not line:
                    continue
                index = str(item.get("index") or "")
                level = item.get("level")
                sections.append(
                    _SectionRef(
                        index=index if _SECTION_INDEX.match(index) else "",
                        level=int(level) if isinstance(level, str) and level.isdigit() else 0,
                        title=line,
                    )
                )
        return tuple(sections)

    async def _section_wikitext(self, title: str, index: str) -> _SectionText:
        """`action=parse&section=N&prop=wikitext|revid`：只取一节（S27）。"""
        if not _SECTION_INDEX.match(index):
            raise AppError(
                ErrorCode.INVALID_PARAMETER,
                f"章节序号必须是非负整数，收到「{str(index)[:20]}」",
                {"section": str(index)[:20]},
            )
        params = {
            "action": "parse",
            "page": validate_title(title),
            "section": index,
            "prop": "wikitext|revid",
            "format": "json",
            "formatversion": "2",
        }
        result = await self.prts.client.get_json("prts", "api", params)
        parsed = PrtsResult(result).data.get("parse")
        wikitext = parsed.get("wikitext") if isinstance(parsed, dict) else None
        if not isinstance(wikitext, str):
            raise AppError(
                ErrorCode.GAME_DATA_NOT_FOUND,
                f"PRTS 页面「{title}」没有第 {index} 节",
                {"title": title, "section": index},
            )
        revision = parsed.get("revid") if isinstance(parsed, dict) else None
        page_title = parsed.get("title") if isinstance(parsed, dict) else None
        return _SectionText(
            wikitext=wikitext,
            revision=revision if isinstance(revision, int) else None,
            page_title=page_title if isinstance(page_title, str) and page_title else title,
            result=result,
        )

    async def _fetch_page(self, title: str) -> _WikiText:
        """整页 wikitext：只用于藏品单页（小页面），不用于主题页。"""
        batch = await self.prts.wikitext([title])
        page = batch.by_title(validate_title(title)) or (batch.pages[0] if batch.pages else None)
        if page is None or page.missing:
            raise AppError(
                ErrorCode.GAME_DATA_NOT_FOUND,
                f"PRTS 上没有找到页面「{title}」",
                {"title": title},
            )
        return _WikiText(
            content=page.content,
            revision=page.revision,
            page_title=page.title or title,
            result=batch.result,
        )

    # -- 各 topic ---------------------------------------------------------
    async def _overview(
        self,
        theme: str,
        title: str,
        sections: tuple[_SectionRef, ...],
    ) -> dict[str, Any]:
        """前言 + `{{活动信息}}`（章节序号 0 是 MediaWiki 的前言）。"""
        lead = await self._section_wikitext(title, "0")
        activity = _activity_fields(lead.wikitext, lead.page_title)
        unavailable = [] if activity is not None else ["activity"]
        return self._payload(
            theme=theme,
            title=lead.page_title,
            topic="overview",
            section="前言",
            section_index="0",
            sections=sections,
            fields={"type": "activity", "activity": activity} if activity else None,
            text=_clean_text(lead.wikitext),
            revision=lead.revision,
            refs=[_source_ref(lead)],
            unavailable=unavailable,
        )

    async def _squads(
        self,
        theme: str,
        title: str,
        sections: tuple[_SectionRef, ...],
    ) -> dict[str, Any]:
        """「开始探索」节里的分队表（六个主题页都是这一节）。"""
        ref = _pick_squad_section(sections)
        if ref is None:
            return self._payload(
                theme=theme,
                title=title,
                topic="squads",
                section=None,
                section_index=None,
                sections=sections,
                fields=None,
                text="",
                revision=None,
                refs=[],
                unavailable=["squads"],
                notes=["主题页的章节表里没有「开始探索」一节，分队资料读不到。"],
            )
        parsed = await self._section_wikitext(title, ref.index)
        squads, prose = _parse_squad_section(parsed.wikitext)
        notes = [] if squads else ["这一节里没有找到分队表，分队列表可能已改版。"]
        return self._payload(
            theme=theme,
            title=parsed.page_title,
            topic="squads",
            section=ref.title,
            section_index=ref.index,
            sections=sections,
            fields={"type": "squads", "squads": squads},
            text=prose,
            revision=parsed.revision,
            refs=[_source_ref(parsed)],
            unavailable=[] if squads else ["squads"],
            notes=notes,
        )

    async def _relic_index(
        self,
        theme: str,
        title: str,
        sections: tuple[_SectionRef, ...],
    ) -> dict[str, Any]:
        """藏品一览节：正文是被整页嵌入的，只给节文本与子页面标题。"""
        ref = _pick_relic_section(sections)
        if ref is None:
            return self._payload(
                theme=theme,
                title=title,
                topic="relic",
                section=None,
                section_index=None,
                sections=sections,
                fields=None,
                text="",
                revision=None,
                refs=[],
                unavailable=["relic"],
                notes=["主题页的章节表里没有收藏品一览，请改用 name 指定具体藏品。"],
            )
        parsed = await self._section_wikitext(title, ref.index)
        index_page = _index_page_of(parsed.wikitext)
        fields: dict[str, Any] = {"type": "relic_index", "section": ref.title}
        if index_page:
            fields["index_page"] = index_page
        return self._payload(
            theme=theme,
            title=parsed.page_title,
            topic="relic",
            section=ref.title,
            section_index=ref.index,
            sections=sections,
            fields=fields,
            text=_clean_text(parsed.wikitext),
            revision=parsed.revision,
            refs=[_source_ref(parsed)],
            unavailable=["relic_list"],
            notes=["藏品清单在子页面上，本工具不展开；用 name 指定具体藏品，或按 index_page 取页面。"],
        )

    async def _relic_page(self, theme: str, name: str) -> dict[str, Any]:
        """单页藏品：优先 `{{收藏品/common}}`（规格 §8.4）。"""
        page = await self._fetch_page(name)
        relic = _parse_collectible(page.content, page_title=page.page_title)
        if relic is None:
            raise AppError(
                ErrorCode.GAME_DATA_NOT_FOUND,
                f"页面「{page.page_title}」不是收藏品页（没有 {{{{收藏品/common}}}} 模板）；"
                "请确认名称，或改用 topic=relic 不带 name 看藏品一览",
                {"title": page.page_title, "topic": "relic"},
            )
        return self._payload(
            theme=theme,
            title=page.page_title,
            topic="relic",
            section=None,
            section_index=None,
            sections=(),
            fields={"type": "relic", "relic": relic},
            text=_render_relic(relic),
            revision=page.revision,
            refs=[_source_ref(page)],
        )

    async def _section(
        self,
        theme: str,
        title: str,
        sections: tuple[_SectionRef, ...],
        name: str | None,
    ) -> dict[str, Any]:
        """按标题或序号取一节。"""
        if name is None:
            raise AppError(
                ErrorCode.INVALID_PARAMETER,
                "topic=section 必须给 name（章节标题或章节序号）",
                {"field": "name", "topic": "section", "sections": [item.title for item in sections]},
            )
        ref = _select_section(sections, name)
        parsed = await self._section_wikitext(title, ref.index)
        return self._payload(
            theme=theme,
            title=parsed.page_title,
            topic="section",
            section=ref.title,
            section_index=ref.index,
            sections=sections,
            fields=None,
            text=_clean_text(parsed.wikitext),
            revision=parsed.revision,
            refs=[_source_ref(parsed)],
        )

    # -- 组装 -------------------------------------------------------------
    def _payload(
        self,
        *,
        theme: str,
        title: str,
        topic: str,
        section: str | None,
        section_index: str | None,
        sections: tuple[_SectionRef, ...],
        fields: dict[str, Any] | None,
        text: str,
        revision: int | None,
        refs: Sequence[SourceRef],
        unavailable: Sequence[str] = (),
        notes: Sequence[str] = (),
    ) -> dict[str, Any]:
        window, truncated = _window(text)
        payload: dict[str, Any] = {
            "theme": theme,
            "theme_label": title,
            "title": title,
            "topic": topic,
            "section": section,
            "section_index": section_index,
            "revision": revision,
            "sections": [item.as_dict() for item in sections],
            "fields": fields,
            "text": window,
            "truncated": truncated,
            "notes": list(notes) + _theme_notes(theme),
        }
        payload.update(build_envelope(refs, unavailable=unavailable))
        return payload


# ---------------------------------------------------------------------------
# 纯函数
# ---------------------------------------------------------------------------
def _clean_text(body: str) -> str:
    """正文清洗：`strip_markup` 之前把章节标题行归一成 ``== 标题 ==``（口径同 `PageService`）。"""
    cleaned = _HEADING_LINE.sub(lambda match: f"== {match.group(2).strip()} ==", body)
    return strip_markup(cleaned)


def _clean_name(value: str | None) -> str | None:
    """``name`` 归一：空白或空串按「没给」处理。"""
    if value is None:
        return None
    cleaned = str(value).strip()
    return cleaned or None


def _theme_notes(theme: str) -> list[str]:
    return [BLACKFLOW_NOTE] if theme == "BlackFlow" else []


def _window(text: str) -> tuple[str, dict[str, Any] | None]:
    """按 `TEXT_MAX_CHARS` 截断；截断时给 `truncated`（口径同 `PageService.page`）。"""
    total = len(text)
    if total <= TEXT_MAX_CHARS:
        return text, None
    window = text[:TEXT_MAX_CHARS]
    return window, {"field": "text", "total_chars": total, "next_offset": len(window)}


def _pick_squad_section(sections: Sequence[_SectionRef]) -> _SectionRef | None:
    """分队表的宿主章节：标题含「分队」优先，否则「开始探索」（实测六个主题都是后者）。"""
    for ref in sections:
        if "分队" in ref.title and ref.index:
            return ref
    for ref in sections:
        if "开始探索" in ref.title and ref.index:
            return ref
    return None


def _pick_relic_section(sections: Sequence[_SectionRef]) -> _SectionRef | None:
    """收藏品一览：标题含「收藏品」优先（实测写作「××（收藏品一览）」）。"""
    for ref in sections:
        if "收藏品" in ref.title and ref.index:
            return ref
    return None


def _select_section(sections: Sequence[_SectionRef], name: str) -> _SectionRef:
    """按标题精确 → 子串 → 纯数字序号的顺序选一节。"""
    usable = [ref for ref in sections if ref.index]
    for ref in usable:
        if ref.title == name:
            return ref
    for ref in usable:
        if name in ref.title:
            return ref
    if _SECTION_INDEX.match(name):
        for ref in usable:
            if ref.index == name:
                return ref
        return _SectionRef(index=name, level=0, title=name)
    available = "、".join(ref.title for ref in usable) or "（没有章节）"
    raise AppError(
        ErrorCode.GAME_DATA_NOT_FOUND,
        f"主题页没有章节「{name}」；可用章节：{available}",
        {"section": name, "sections": [ref.title for ref in usable]},
    )


def _activity_fields(content: str, title: str) -> dict[str, Any] | None:
    """`{{活动信息}}` → 与 `get_prts_page` 同形的活动字段；没有模板给 ``None``。"""
    if not find_templates(content, ACTIVITY_TEMPLATE):
        return None
    page = parse_activity_page(content, page_title=title)
    return {
        "page_title": page.page_title,
        "name": page.name,
        "type": page.type,
        "start_at": page.start_at.isoformat() if page.start_at else None,
        "end_at": page.end_at.isoformat() if page.end_at else None,
        "exchange_end_at": page.exchange_end_at.isoformat() if page.exchange_end_at else None,
    }


def _index_page_of(wikitext: str) -> str | None:
    """藏品一览节指向的子页面：`{{参阅|页面|说明}}` 或 `{{#lsth:页面|锚点}}`。"""
    for name in ("参阅", "#lsth"):
        for template in find_templates(wikitext, name):
            positional = template.positional()
            if not positional:
                continue
            target = strip_markup(positional[0]).strip()
            if target:
                return target
    return None


def _parse_squad_section(wikitext: str) -> tuple[list[dict[str, str]], str]:
    """分队表 →（行, 表前说明文字）。

    ``text`` 只给表前的说明（分队机制、升级效果口径），行内容在 ``fields.squads`` 里，
    两处重复没有意义（规格 §4.4）。
    """
    span = _find_squad_table(wikitext)
    if span is None:
        return [], ""
    start, _end = span
    head = wikitext.rfind(_SQUAD_ANCHOR, 0, start)
    if head != -1:
        start = wikitext.rfind("\n", 0, head) + 1
    prose = _clean_text(wikitext[start : span[0]])
    return _squad_rows(wikitext[span[0] : span[1]]), prose


def _find_squad_table(wikitext: str) -> tuple[int, int] | None:
    """第一个表头含「分队」的 wikitable 的 ``[start, end)``。"""
    for start, end in _iter_tables(wikitext):
        if _SQUAD_HEADER.search(wikitext[start : start + 600]):
            return start, end
    return None


def _iter_tables(wikitext: str) -> Iterator[tuple[int, int]]:
    """按 ``{|`` / ``|}`` 扫描表格（含嵌套），按出现顺序产出。"""
    index = 0
    while True:
        start = wikitext.find("{|", index)
        if start == -1:
            return
        end = _table_end(wikitext, start)
        if end is None:
            return
        yield start, end
        index = end


def _table_end(wikitext: str, start: int) -> int | None:
    depth = 0
    index = start
    while index < len(wikitext):
        if wikitext.startswith("{|", index):
            depth += 1
            index += 2
            continue
        if wikitext.startswith("|}", index):
            depth -= 1
            index += 2
            if depth == 0:
                return index
            continue
        index += 1
    return None


def _squad_rows(table: str) -> list[dict[str, str]]:
    """表格正文 → 分队行（名称、效果、解锁条件）。"""
    rows: list[dict[str, str]] = []
    body = table[2:]
    if body.endswith("|}"):
        body = body[:-2]
    for chunk in re.split(r"\n\|-+", body):
        cells = _row_cells(chunk)
        if not cells:
            continue
        name = _joined(cells[0])
        if not name:
            continue
        rows.append(
            {
                "name": name,
                "effect": _joined(cells[1]) if len(cells) > 1 else "",
                "unlock": _joined(cells[2]) if len(cells) > 2 else "",
            }
        )
    return rows


def _row_cells(chunk: str) -> list[str]:
    """一行 → 单元格原文：行首 ``|`` 开新单元格，行内 ``||`` 也分隔（深度感知）。"""
    cells: list[str] = []
    for raw_line in chunk.split("\n"):
        line = raw_line.strip()
        if not line or line.startswith("|}") or line.startswith("|+") or line.startswith("!"):
            continue
        if not line.startswith("|"):
            if cells:
                cells[-1] = f"{cells[-1]}\n{line}"
            continue
        for part in _split_top(line[1:], "||"):
            cells.append(_strip_cell_attributes(part))
    return cells


def _strip_cell_attributes(cell: str) -> str:
    """``style="…"|内容`` → ``内容``（没有属性就原样返回）。"""
    head, sep, rest = cell.partition("|")
    if sep and _CELL_ATTRIBUTE.match(head):
        return rest
    return cell


def _split_top(text: str, separator: str) -> list[str]:
    """按 ``separator`` 切分，但 ``{{…}}`` 与 ``[[…]]`` 里的分隔符不算。"""
    parts: list[str] = []
    buffer: list[str] = []
    braces = 0
    links = 0
    index = 0
    while index < len(text):
        pair = text[index : index + 2]
        if pair in ("{{", "}}", "[[", "]]"):
            if pair == "{{":
                braces += 1
            elif pair == "}}":
                braces = max(braces - 1, 0)
            elif pair == "[[":
                links += 1
            else:
                links = max(links - 1, 0)
            buffer.append(pair)
            index += 2
            continue
        if braces == 0 and links == 0 and text.startswith(separator, index):
            parts.append("".join(buffer))
            buffer = []
            index += len(separator)
            continue
        buffer.append(text[index])
        index += 1
    parts.append("".join(buffer))
    return parts


def _joined(cell: str) -> str:
    """单元格 → 清洗后的文字（保留单元格内的换行，压掉多余空行）。"""
    return _clean_field(cell)


def _clean_field(text: str) -> str:
    """表格单元格与藏品字段的清洗：先把单中括号外链剥成显示名，再走通用清洗。"""
    without_links = _EXTERNAL_LINK.sub(lambda match: (match.group(1) or "").strip(), text)
    return _clean_text(without_links).strip()


def _parse_collectible(content: str, *, page_title: str) -> dict[str, Any] | None:
    """`{{收藏品/common}}` → 藏品字段；页面不是藏品给 ``None``。"""
    template = None
    for name in RELIC_TEMPLATES:
        found = find_templates(content, name, include_nested=False)
        if found:
            template = found[0]
            break
    if template is None:
        return None
    name = _clean_field(template_param(template, "名称") or "") or page_title
    icon_id = strip_markup(template_param(template, "iconId") or "").strip()
    rarity = _as_int(template_param(template, "稀有度"))
    themes: list[dict[str, Any]] = []
    for index in range(1, _MAX_RELIC_THEMES + 1):
        theme = strip_markup(template_param(template, f"主题{index}") or "").strip()
        if not theme:
            break
        tags = strip_markup(template_param(template, f"角标{index}") or "").strip()
        themes.append(
            {
                "theme": theme,
                "tags": [tags] if tags else [],
                "price": _as_int(template_param(template, f"售价{index}")),
                "effect": _clean_field(template_param(template, f"效果{index}") or ""),
                "obtain": _clean_field(template_param(template, f"获取条件{index}") or ""),
                "unlock": _clean_field(template_param(template, f"解锁条件{index}") or ""),
            }
        )
    return {
        "name": name,
        "icon_id": icon_id,
        "rarity": rarity,
        "description": _clean_field(template_param(template, "描述") or ""),
        "themes": themes,
    }


def _render_relic(relic: Mapping[str, Any]) -> str:
    """藏品 → 一段可读正文（页面本身几乎只有模板，`strip_markup` 后会空掉）。"""
    lines = [str(relic.get("name") or "")]
    description = str(relic.get("description") or "")
    if description:
        lines.append(description)
    lines.append(f"稀有度：{relic.get('rarity')}")
    for entry in relic.get("themes") or []:
        if not isinstance(entry, Mapping):
            continue
        detail = str(entry.get("effect") or "")
        if entry.get("price") is not None:
            detail = f"{detail}（售价 {entry['price']}）" if detail else f"售价 {entry['price']}"
        lines.append(f"{entry.get('theme')}：{detail}")
    return "\n".join(line for line in lines if line != "")


def _as_int(value: Any) -> int | None:
    if isinstance(value, bool):
        return None
    if isinstance(value, int):
        return value
    if isinstance(value, str) and value.strip().lstrip("-").isdigit():
        return int(value.strip())
    return None


def _source_ref(item: _SectionText | _WikiText) -> SourceRef:
    """出处：页面标题 + `/w/<标题>` 链接 + 修订号与抓取时间。"""
    page_title = item.page_title
    return SourceRef(
        kind="prts",
        label="PRTS",
        title=page_title,
        url=f"https://prts.wiki/w/{page_title.replace(' ', '_')}",
        revision=item.revision,
        fetched_at=item.result.fetched_at,
        cached=item.result.cached,
        stale=item.result.stale,
    )


#: 进程内共享的默认门面；工具层用它（`tools/roguelike.py` 的默认装配）。
_DEFAULT: RoguelikeService | None = None


def default_roguelike_service() -> RoguelikeService:
    """默认门面（进程内单例）。"""
    global _DEFAULT
    if _DEFAULT is None:
        _DEFAULT = RoguelikeService()
    return _DEFAULT
