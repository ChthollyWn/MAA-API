"""剧情（规格 §8.5、§4.3）：`get_story` 的检索与正文分页。

分两层：

``search(query, limit=10)``
    先 ``list=search`` 检索（``srnamespace=0``，一次最多 10 条），再用**一次**批量
    ``prop=categories`` 过滤出属于剧情的页面：分类里有 ``分类:剧情``，或标题带
    ``/BEG``、``/END``、``/NBT`` 这类章节后缀（实测 `分类:剧情` 的 1713 个成员全都带
    后缀）的才算。没有命中的时候返回空列表，不报错；分类取不到时按标题后缀兜底，并把
    ``categories`` 写进 ``unavailable``（§3.5，降级不冒充）。

``get(title, offset, max_chars)``
    取页面 wikitext，交给 :func:`maa_api.services.prts_parse.story.parse_story_page`
    解析剧本，按 ``max_chars``（2000–8000，默认 6000）分页；超出时给
    ``truncated: {field, total_chars, next_offset}``，模型带 ``offset`` 接着读（§4.4）。

约定（§3.4、§4.3）：

- 标题只走 ``PrtsClient.wikitext``，命名空间与危险字符的校验在那边（``用户:张三`` 这类
  在发请求之前就被拒），这里不重复；
- 来源信封用 ``build_envelope`` 拼；外服加 ``region_note``（剧情是稳定数据，口径取 §4.3
  的「国服数据，外服可能尚未实装或数值不同」）；
- 页面不存在、或者页面不是剧情页（没有 ``{{剧情模拟器|文本数据=…}}``）都抛
  ``GAME_DATA_NOT_FOUND``：干员档案、活动简介这类设定文本要走 ``get_prts_page``。

规格没写死、由本模块裁决的实现细节（报告里会列全）：

1. ``search`` 的 ``results_total`` 是**过滤后**的条数，与 ``results`` 长度一致；
2. 分类匹配要求分类名**正好**是 ``剧情``（``分类:含有内置剧情的关卡`` 这种子串命中不算：
   实测关卡页 ``1-1 孤岛`` 就带这个分类，它是关卡页不是剧情页）；
3. 章节后缀的形状是 ``/`` + 全大写字母数字（``BEG``、``END``、``NBT``、``ENTRY``、
   ``AVG``、``SP1``）或 ``剧情``/``剧情1``（实测 1713 个成员的全部后缀）；
4. ``characters`` 是整页的说话人表（去重保序），不随 ``offset`` 窗口缩水；
5. 正文窗口除了 ``max_chars`` 还受字节预算约束（``TEXT_BUDGET_BYTES``，见 ``_window``）：
   实测 8000 个汉字加上说话人表与信封会到 24658 B，超过规格 §4.4 的 24 KB，所以按字节
   提前截断——``max_chars`` 是上限，「最多」不承诺给满，``next_offset`` 照常可用。
"""

from __future__ import annotations

import re
from typing import Any, Callable, Iterable, Sequence

from maa_api.domain.errors import AppError, ErrorCode
from maa_api.services.external.envelope import build_envelope
from maa_api.services.external.prts import MAX_SEARCH_LIMIT, PrtsClient, PrtsResult
from maa_api.services.game.activities import is_cn_client
from maa_api.services.game.pages import clean_snippet, make_page_url
from maa_api.services.prts_parse.story import parse_story_page
from maa_api.settings import Settings, get_settings

__all__ = [
    "DEFAULT_MAX_CHARS",
    "MAX_RESULT_BYTES",
    "TEXT_BUDGET_BYTES",
    "MAX_SEARCH_LIMIT",
    "STABLE_REGION_NOTE",
    "STORY_CATEGORY",
    "TEXT_MAX_CHARS",
    "TEXT_MIN_CHARS",
    "StoryService",
    "default_story_service",
    "is_story_page",
]

#: `max_chars` 的取值范围（规格 §8.5）。
TEXT_MIN_CHARS = 2000
TEXT_MAX_CHARS = 8000

#: `max_chars` 的默认值（规格 §8.5）。
DEFAULT_MAX_CHARS = 6000

#: 单个工具结果的体积上限（规格 §4.4，守 docs/04 §5.8 的 32 KB tool content 纪律）。
MAX_RESULT_BYTES = 24 * 1024

#: 正文窗口的字节预算：从 24 KB 里给信封、说话人表与 JSON 转义留 2 KB。
#: 实测（2026-10-10，60 个真实剧情页）8000 个汉字 ≈ 24000 B，加上说话人表与信封后
#: `NL-10 耀骑士/END` 会到 24658 B，所以这里按字节再收一次窗口。
TEXT_BUDGET_BYTES = MAX_RESULT_BYTES - 2 * 1024

#: `offset` 的上限：只是把拼错的超大值挡在外面（超出正文长度时返回空串）。
OFFSET_MAX = 1_000_000

#: 剧情分类（不带 `分类:` 前缀，实测）。
STORY_CATEGORY = "剧情"

#: 外服的剧情提示语（规格 §4.3 的稳定数据一条）。
STABLE_REGION_NOTE = "国服数据，外服可能尚未实装或数值不同"

#: 章节后缀：`/BEG`、`/END`、`/NBT`、`/ENTRY`、`/AVG`、`/SP1`、`/剧情1`（实测全部形状）。
_CHAPTER_SUFFIX = re.compile(r"^.+/(?:[A-Z][A-Z0-9]{0,7}|剧情\d?)$")


class StoryService:
    """剧情的领域服务；``prts`` 与 ``settings_provider`` 可注入（测试与多来源装配）。

    ``settings_provider`` 只在没给 ``client_type`` 时用来判定渠道（口径同
    ``StageService`` / ``ActivityService``）：剧情是稳定数据，外服照常查 PRTS，只加
    ``region_note``（§4.3）。
    """

    def __init__(
        self,
        *,
        prts: PrtsClient | None = None,
        settings_provider: Callable[[], Settings] = get_settings,
    ) -> None:
        self._prts = prts
        self._settings_provider = settings_provider

    @property
    def prts(self) -> PrtsClient:
        """PRTS 客户端；缺省现建一个（底层走进程内单例的连接）。"""
        return self._prts or PrtsClient()

    # -- 检索 -------------------------------------------------------------
    async def search(
        self,
        query: str,
        limit: int = MAX_SEARCH_LIMIT,
        *,
        client_type: str | None = None,
    ) -> dict[str, Any]:
        """检索剧情页（标题 + 片段 + 链接）；没有命中就返回空列表。"""
        result = await self.prts.search(query, limit=limit)
        rows = _search_rows(result)
        titles = [str(row.get("title") or "") for row in rows]
        titles = [title for title in titles if title]
        categories, unavailable = await self._categories(titles)

        results: list[dict[str, Any]] = []
        for row in rows:
            title = str(row.get("title") or "")
            if not title or not is_story_page(title, categories.get(title)):
                continue
            results.append(
                {
                    "title": title,
                    "snippet": clean_snippet(row.get("snippet")),
                    "url": make_page_url(title),
                }
            )
        payload: dict[str, Any] = {
            "query": query,
            "results": results,
            "results_total": len(results),
        }
        payload.update(
            build_envelope(
                [result.source_ref(title=query)],
                unavailable=unavailable,
                region_note=self._region_note(client_type),
            )
        )
        return payload

    async def _categories(self, titles: Sequence[str]) -> tuple[dict[str, list[str]], list[str]]:
        """一次批量取分类；取不到就给空表 + `unavailable`（检索本身照常返回）。"""
        if not titles:
            return {}, []
        try:
            mapping = await self.prts.categories(list(titles))
        except AppError:
            return {}, ["categories"]
        return mapping, []

    # -- 正文 -------------------------------------------------------------
    async def get(
        self,
        title: str,
        *,
        offset: int = 0,
        max_chars: int = DEFAULT_MAX_CHARS,
        client_type: str | None = None,
    ) -> dict[str, Any]:
        """取一个剧情页的剧本正文；按 ``max_chars`` 分页。"""
        self._validate_window(offset=offset, max_chars=max_chars)
        wanted = str(title).strip()
        batch = await self.prts.wikitext([title])
        page = batch.by_title(wanted) or (batch.pages[0] if batch.pages else None)
        if page is None or page.missing:
            raise AppError(
                ErrorCode.GAME_DATA_NOT_FOUND,
                f"PRTS 上没有找到剧情页「{wanted}」",
                {"title": wanted},
            )
        parsed = parse_story_page(page.content, title=page.title)
        if parsed is None:
            raise AppError(
                ErrorCode.GAME_DATA_NOT_FOUND,
                f"页面「{page.title}」不是剧情页（没有 {{{{剧情模拟器|文本数据=…}}}}）；"
                "干员档案、活动简介这类设定文本用 get_prts_page 按章节取",
                {"title": wanted, "resolved": page.title},
            )
        window, truncated = _window(parsed.text, offset=offset, max_chars=max_chars)
        payload: dict[str, Any] = {
            "title": page.title,
            "redirected_from": page.redirect_from,
            "characters": list(parsed.characters),
            "text": window,
            "truncated": truncated,
        }
        payload.update(
            build_envelope(
                batch.source_refs(),
                region_note=self._region_note(client_type),
            )
        )
        return payload

    def _region_note(self, client_type: str | None) -> str | None:
        """外服提示语（§4.3）；没给渠道时按设置判，读不到设置按国服处理。"""
        resolved = client_type
        if resolved is None:
            try:
                resolved = self._settings_provider().channel.client_type
            except Exception:  # noqa: BLE001 - 渠道只影响提示语
                return None
        return None if is_cn_client(resolved) else STABLE_REGION_NOTE

    @staticmethod
    def _validate_window(*, offset: int, max_chars: int) -> None:
        """`offset` / `max_chars` 的校验（与 `PageService.page` 同一口径）。"""
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
def is_story_page(title: str, categories: Iterable[str] | None = None) -> bool:
    """标题 + 分类是否属于剧情页（规格 §8.5 的索引口径）。"""
    for name in categories or ():
        if _category_name(name) == STORY_CATEGORY:
            return True
    return bool(_CHAPTER_SUFFIX.match(str(title or "")))


def _category_name(value: Any) -> str:
    """``分类:剧情`` → ``剧情``（`Category:剧情` 也认）。"""
    return str(value or "").rsplit(":", 1)[-1].strip()


def _search_rows(result: PrtsResult) -> list[dict[str, Any]]:
    entries = result.query.get("search")
    if not isinstance(entries, list):
        return []
    return [row for row in entries if isinstance(row, dict)]


def _window(text: str, *, offset: int, max_chars: int) -> tuple[str, dict[str, Any] | None]:
    """按 `offset` 取最多 `max_chars` 字；截断时给 `truncated`（与 `PageService` 同口径）。

    窗口再按字节收一次（``TEXT_BUDGET_BYTES``）：``max_chars`` 是**上限**，「最多」不承诺
    一定给满，超预算时提前截断并照常在 ``next_offset`` 里给出下一段的起点。
    """
    total = len(text)
    if offset >= total:
        return "", None
    window = _fit_bytes(text[offset : offset + max_chars], TEXT_BUDGET_BYTES)
    if offset + len(window) >= total:
        return window, None
    return window, {
        "field": "text",
        "total_chars": total,
        "next_offset": offset + len(window),
    }


def _fit_bytes(text: str, budget: int) -> str:
    """把 ``text`` 收到 ``budget`` 字节以内（UTF-8，二分找最大的字符数）。"""
    if len(text.encode("utf-8")) <= budget:
        return text
    low, high = 0, len(text)
    while low < high:
        middle = (low + high + 1) // 2
        if len(text[:middle].encode("utf-8")) <= budget:
            low = middle
        else:
            high = middle - 1
    return text[:low]


#: 进程内共享的默认门面；工具与注入段都用它。
_DEFAULT: StoryService | None = None


def default_story_service() -> StoryService:
    """默认门面（进程内单例）。"""
    global _DEFAULT
    if _DEFAULT is None:
        _DEFAULT = StoryService()
    return _DEFAULT
