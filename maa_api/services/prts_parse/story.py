"""剧情页解析（规格 §8.5）：``{{剧情模拟器|文本数据=…}}`` → 可读剧本。

只做纯函数：wikitext 进，剧本出，不联网、不读盘。

实测（2026-10-10，``1-1 孤岛/BEG`` 与 ``分类:剧情`` 的 1713 个成员）：

- 正文在 ``{{剧情模拟器}}`` 的 ``文本数据=`` 命名参数里，一行一条；
- ``[name="杜宾"]  台词`` 是说话人。属性名大小写、引号与后续属性都有变体
  （``[name=杜宾]``、``[name = '杜宾']``、``[name="杜宾", focus=1]``、``[name=""]``）；
- 其余以 ``[`` 开头的行都是演出指令，一律丢掉：``Character`` / ``Blocker`` /
  ``Background`` / ``PlayMusic`` / ``PlaySound`` / ``StopMusic`` / ``Delay`` / ``Image`` /
  ``ImageTween`` / ``CameraShake`` / ``HEADER`` / ``Dialog`` / ``Decision`` / ``Predicate``
  （实测大小写混用，所以按「以 ``[`` 开头且不是 ``name=``」判定，不列白名单）；
- 不以 ``[`` 开头的行是旁白（``7-18 爱国者之死/END`` 有大量旁白），照原样保留；
- 行内的 ``<color=#888888>`` / ``</color>`` / ``</>`` / ``<p=1>`` / ``<i>`` 是演出标记，
  删掉但保留其中的文字。标签名只认 ASCII 字母开头（或 ``</>``），所以正文里的
  ``<拟生态制衡奖章>`` 这类装饰尖括号原样保留（口径同 ``wikitext.strip_markup``）；
- ``文本数据`` 里没有裸 ``|``（实测 20 页），模板参数切分不会串行。

``parse_story_page`` 找不到 ``{{剧情模拟器}}``、没有 ``文本数据``、或者正文一行台词都留不下
时返回 ``None``：调用方据此抛 ``GAME_DATA_NOT_FOUND``，把模型引回 ``get_prts_page``。
"""

from __future__ import annotations

import re
from collections.abc import Iterator
from dataclasses import dataclass

from maa_api.services.prts_parse.wikitext import find_templates

__all__ = [
    "STORY_TEMPLATE",
    "STORY_TEXT_PARAM",
    "StoryLine",
    "StoryPage",
    "clean_story_text",
    "parse_story_page",
]

#: 剧情页的模板名（实测）。
STORY_TEMPLATE = "剧情模拟器"

#: 剧本正文所在的命名参数（实测）。
STORY_TEXT_PARAM = "文本数据"

#: ``[name="杜宾"] 台词``：属性名大小写不敏感，值允许双引号、单引号或裸写，后面允许别的属性。
_NAME_PREFIX = re.compile(
    r"^\s*\[\s*name\s*=\s*"
    r"(?:\"(?P<double>[^\"]*)\"|'(?P<single>[^']*)'|(?P<bare>[^,\]\s]*))"
    r"[^\]]*\]\s*(?P<text>.*)$",
    re.IGNORECASE,
)

#: 行内演出标记（``<color=…>``、``</color>``、``</>``、``<p=1>``、``<i>``）。
#: 标签名只认 ASCII 字母开头，``</>`` 单独兜住；装饰尖括号（``<奖章>``）保持原文。
_INLINE_COMMAND = re.compile(r"</?(?:[A-Za-z][^<>\n]{0,80})?>")


@dataclass(frozen=True, slots=True)
class StoryLine:
    """剧本的一行：``speaker is None`` 表示旁白。"""

    speaker: str | None
    text: str

    def render(self) -> str:
        """``杜宾：台词``；旁白只给文字。"""
        return f"{self.speaker}：{self.text}" if self.speaker else self.text


@dataclass(frozen=True, slots=True)
class StoryPage:
    """一个剧情页的解析结果。"""

    page_title: str
    lines: tuple[StoryLine, ...]
    characters: tuple[str, ...]
    raw_chars: int

    @property
    def text(self) -> str:
        """剧本正文：每行一段，去掉空行（规格 §8.5「保留段落换行」）。"""
        return "\n".join(line.render() for line in self.lines)


def clean_story_text(raw: str) -> str:
    """``文本数据`` 原文 → 剧本正文（指令与空行都去掉）。"""
    return "\n".join(line.render() for line in _iter_lines(raw))


def parse_story_page(content: str, *, title: str = "") -> StoryPage | None:
    """解析一个页面的 wikitext；不是剧情页（或没有正文）时返回 ``None``。"""
    for template in find_templates(str(content or ""), STORY_TEMPLATE):
        raw = template.param(STORY_TEXT_PARAM)
        if raw is None:
            continue
        lines = tuple(_iter_lines(raw))
        if not lines:
            return None
        characters = tuple(
            dict.fromkeys(line.speaker for line in lines if line.speaker)
        )
        return StoryPage(
            page_title=str(title or ""),
            lines=lines,
            characters=characters,
            raw_chars=len(str(content or "")),
        )
    return None


def _iter_lines(raw: str) -> Iterator[StoryLine]:
    """逐行分类：说话人、旁白、演出指令（丢掉）、空行（丢掉）。"""
    for line in str(raw or "").splitlines():
        stripped = line.strip()
        if not stripped:
            continue
        match = _NAME_PREFIX.match(stripped)
        if match is not None:
            speaker = _clean_inline(
                match.group("double") or match.group("single") or match.group("bare") or ""
            )
            text = _clean_inline(match.group("text"))
            if text:
                yield StoryLine(speaker=speaker or None, text=text)
            continue
        if stripped.startswith("["):
            continue
        text = _clean_inline(stripped)
        if text:
            yield StoryLine(speaker=None, text=text)


def _clean_inline(text: str) -> str:
    """去掉行内演出标记并去掉两侧空白。"""
    return _INLINE_COMMAND.sub("", str(text or "")).strip()
