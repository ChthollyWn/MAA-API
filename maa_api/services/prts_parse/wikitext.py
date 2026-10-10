"""MediaWiki wikitext 通用解析（规格 §5.5）：纯函数，不联网、不读盘、无全局可变状态。

四个入口：

``parse_templates(text)``
    按出现顺序取出模板。嵌套模板（``{{a|{{b|c}}}}``）切出来的是**外层一个模板**，
    参数值里保留内层原文；内层同时作为独立条目返回，所以「取内层」有三条等价的路：
    扁平列表里的下一个元素、``Template.nested()``、或者把参数值再喂给 ``parse_templates``。
    只要最外层时用 ``include_nested=False``。
``find_templates(text, name)``
    模板名精确匹配（两侧空白归一，大小写敏感），不认前缀也不认子串。
``strip_markup(text)``
    链接、展示类模板、``<br>``、``<ref>``、HTML 注释与其余标签 → 纯文本。
``split_sections(text)``
    按 ``==标题==`` 切分，``level = "=" 的个数 - 1``；第一个 section 是前言
    （``level=0``、``title=""``，空页也返回这一个）。

实现裁决（规格没写死、由本模块定，Task 4 报告里有完整清单）：

1. ``{{{...}}}`` 三花括号当普通文本：三个 ``{`` 原样输出，扫描继续往里走，所以默认值
   里真正的模板照常解析（``{{{1|{{color|#fff|x}}}}}`` → 模板 ``color``），不会当成模板。
   未闭合的 ``{{`` 同样不炸：按普通文本处理，宁可少取也不误配。
2. HTML 注释里的模板**不**解析（与 MediaWiki 一致：注释里的内容不展开），注释本身在
   :func:`strip_markup` 里被删掉。
3. 参数：键与值两侧空白都去掉（MediaWiki 只 trim 命名参数，这里为下游方便统一处理）；
   ``{{a|=x}}`` 按 MediaWiki 的旧行为当位置参数 ``=x``（空键不算命名参数）。
4. 展示类模板取**最后一个位置参数**（``{{color|#xxx|文字}}``、``{{异常效果|文字}}``、
   ``{{术语|…|文字}}``、``{{变动数值|…}}``、``{{mdi|…}}``、``{{#var:…}}`` 都是这条规则），
   没有位置参数就输出空串。``#vardefine``/``#vardefineecho``/``#widget``/``#invoke``/
   ``#set``/``#subobject`` 这类解析器函数不产出正文，直接输出空串（``{{#var:name}}`` 的
   变量名不是展示文本，只有带位置参数时才取最后一个）。唯一例外是 ``{{关卡链接|代号|stageId}}``：
   页面上显示的是**第一个**位置参数（代号），第二个是给查询用的 stageId，取最后一个会得到
   ``act3break 01`` 这种机器名，所以在 ``_FIRST_POSITIONAL_TEMPLATES`` 里显式列出。
5. 链接：``[[页面|显示名]]`` 取显示名（多段取最后一段）、``[[页面]]`` 取页面名、
   ``[[页面|]]`` 回落页面名；文件/分类/特殊命名空间（``文件:``/``File:``/``Image:``/
   ``分类:``/``Category:``/``特殊:``/``Special:``）整条丢掉——它们不产出正文。
   单中括号外链（``[https://… 文字]``）不在规格范围内，原样保留。
6. 标签：``<br>``/``<br/>``/``<br />`` → 换行；``<ref>``/``<references>`` 先删；其余 HTML
   标签删掉但保留其中文字。标签名只认 ASCII 字母开头，所以 PRTS 正文里的
   ``<拟生态制衡奖章>`` 这种装饰尖括号原样保留（HTML 解析器也是这么做的）。
7. 实体（``&nbsp;``/``&amp;`` 等）解码，``&nbsp;`` 归一成普通空格；``'''``/``''`` 强调
   标记删掉；行尾空白去掉、连续空行压成一个、整体 strip。
"""

from __future__ import annotations

import html
import re
from collections.abc import Iterator
from dataclasses import dataclass

__all__ = [
    "Section",
    "Template",
    "TemplateArg",
    "find_templates",
    "parse_templates",
    "split_sections",
    "strip_markup",
    "template_param",
]

#: 不产出正文的解析器函数（名字里 ``:`` 之前的部分，大小写不敏感）。
_SILENT_FUNCTIONS = frozenset(
    {"#vardefine", "#vardefineecho", "#widget", "#invoke", "#set", "#subobject"}
)

#: 不产出正文的链接命名空间（``[[文件:x.png]]``、``[[分类:x]]`` 之类）。
_NON_TEXT_NAMESPACES = frozenset(
    {"文件", "file", "image", "分类", "category", "特殊", "special"}
)

#: 展示文本在**第一个**位置参数的已知模板（其余模板一律取最后一个，见模块 docstring）。
#: ``{{关卡链接|代号|stageId?}}`` 在页面上显示的是代号，第二个参数是给 SMW/查询用的数据。
_FIRST_POSITIONAL_TEMPLATES = frozenset({"关卡链接"})

_COMMENT_RE = re.compile(r"<!--.*?(?:-->|\Z)", re.S)
_REF_PAIRED_RE = re.compile(r"<ref(?:erences)?\b[^>]*>.*?</ref(?:erences)?\s*>", re.S | re.I)
_REF_SELF_RE = re.compile(r"<ref(?:erences)?\b[^>]*/\s*>", re.I)
_REFERENCES_OPEN_RE = re.compile(r"<references\b[^>]*>", re.I)
_BR_RE = re.compile(r"<br\s*/?>", re.I)
#: 标签名只认 ASCII 字母开头，避免把 ``<拟生态制衡奖章>`` 这类装饰尖括号当标签删掉。
_TAG_RE = re.compile(r"</?[A-Za-z][A-Za-z0-9]*(?:\s[^<>]*?)?/?>")
_QUOTE_RE = re.compile(r"'{2,5}")
_TRAILING_SPACE_RE = re.compile(r"[ \t]+\n")
_BLANK_LINES_RE = re.compile(r"\n{3,}")
_HEADING_RE = re.compile(r"^(={1,6})[ \t]*(.+?)[ \t]*\1[ \t]*$", re.M)


@dataclass(frozen=True)
class TemplateArg:
    """模板的一个参数：``key is None`` 表示位置参数（保持出现顺序）。"""

    key: str | None
    value: str

    @property
    def is_positional(self) -> bool:
        """是否位置参数。"""
        return self.key is None


@dataclass(frozen=True)
class Template:
    """一个模板调用：``name`` 已去两侧空白，``args`` 保持出现顺序。"""

    name: str
    args: tuple[TemplateArg, ...] = ()

    def param(self, name: str, default: str | None = None) -> str | None:
        """按名字取第一个命名参数的值，没有就返回 ``default``。"""
        wanted = name.strip()
        for arg in self.args:
            if arg.key is not None and arg.key == wanted:
                return arg.value
        return default

    def params(self, name: str) -> tuple[str, ...]:
        """按名字取全部命名参数的值（同名参数可能出现多次）。"""
        wanted = name.strip()
        return tuple(arg.value for arg in self.args if arg.key == wanted)

    def positional(self) -> tuple[str, ...]:
        """全部位置参数的值，按出现顺序。"""
        return tuple(arg.value for arg in self.args if arg.key is None)

    def arg(self, index: int, default: str | None = None) -> str | None:
        """按序号取位置参数的值（支持负数），越界返回 ``default``。"""
        try:
            return self.positional()[index]
        except IndexError:
            return default

    def nested(self, name: str | None = None) -> list[Template]:
        """本模板各参数值里的模板（按出现顺序，含更深层；``name`` 为 None 表示全部）。"""
        found: list[Template] = []
        for arg in self.args:
            if name is None:
                found.extend(parse_templates(arg.value))
            else:
                found.extend(find_templates(arg.value, name))
        return found


@dataclass(frozen=True)
class Section:
    """``==标题==`` 切出来的一个章节：``level = "=" 的个数 - 1``（前言为 0）。"""

    level: int
    title: str
    body: str


def parse_templates(text: str, *, include_nested: bool = True) -> list[Template]:
    """取出 ``text`` 里的模板，按出现顺序（外层在前）。

    ``include_nested=False`` 只要最外层模板；默认连嵌套模板一起返回，嵌套模板在父模板里
    是参数值中的原文。
    """
    return list(_iter_templates(text, include_nested=include_nested))


def find_templates(text: str, name: str, *, include_nested: bool = True) -> list[Template]:
    """按模板名精确匹配（两侧空白归一，大小写敏感），保持出现顺序。"""
    wanted = name.strip()
    return [
        template
        for template in parse_templates(text, include_nested=include_nested)
        if template.name == wanted
    ]


def template_param(template: Template, key: str | int, default: str | None = None) -> str | None:
    """取模板参数：``key`` 是 ``str`` 时按名字、是 ``int`` 时按位置序号（支持负数）。"""
    if isinstance(key, int) and not isinstance(key, bool):
        return template.arg(key, default)
    return template.param(str(key), default)


def strip_markup(text: str) -> str:
    """把 wikitext 洗成纯文本（见模块 docstring 的裁决清单）。"""
    out = _COMMENT_RE.sub("", text)
    out = _REF_PAIRED_RE.sub("", out)
    out = _REF_SELF_RE.sub("", out)
    out = _REFERENCES_OPEN_RE.sub("", out)
    out = _BR_RE.sub("\n", out)
    out = _TAG_RE.sub("", out)
    out = _render(out)
    out = html.unescape(out).replace("\xa0", " ")
    out = _QUOTE_RE.sub("", out)
    out = _TRAILING_SPACE_RE.sub("\n", out)
    out = _BLANK_LINES_RE.sub("\n\n", out)
    return out.strip()


def split_sections(text: str) -> list[Section]:
    """按 ``==标题==`` 切分；第一个 section 是前言（``level=0``、``title=""``）。

    标题行必须独占一行且首尾 ``=`` 数量一致；HTML 注释里的标题不算。
    ``body`` 是标题行之后、下一个标题之前的原文，去掉首尾空行（标题行本身不在正文里）。
    """
    comment_spans = [(match.start(), match.end()) for match in _COMMENT_RE.finditer(text)]
    headings = [
        match for match in _HEADING_RE.finditer(text) if not _inside(match.start(), comment_spans)
    ]

    sections: list[Section] = []
    cursor = 0
    level = 0
    title = ""
    for match in headings:
        sections.append(Section(level=level, title=title, body=_body(text[cursor : match.start()])))
        cursor = match.end()
        level = len(match.group(1)) - 1
        title = match.group(2).strip()
    sections.append(Section(level=level, title=title, body=_body(text[cursor:])))
    return sections


def _inside(position: int, spans: list[tuple[int, int]]) -> bool:
    return any(start <= position < end for start, end in spans)


def _body(text: str) -> str:
    return text.strip("\n").rstrip()


def _iter_templates(text: str, *, include_nested: bool) -> Iterator[Template]:
    """扫描 ``text``：注释与三花括号当普通文本跳过，模板按出现顺序产出。"""
    index = 0
    length = len(text)
    while index < length:
        if text.startswith("<!--", index):
            end = text.find("-->", index + 4)
            index = length if end == -1 else end + 3
            continue
        if text.startswith("{{{", index):
            index += 3
            continue
        if text.startswith("{{", index):
            parsed = _parse_template(text, index)
            if parsed is None:
                index += 2
                continue
            template, end = parsed
            yield template
            if include_nested:
                for arg in template.args:
                    yield from _iter_templates(arg.value, include_nested=True)
            index = end
            continue
        index += 1


def _parse_template(text: str, start: int) -> tuple[Template, int] | None:
    """从 ``text[start:start + 2] == "{{"`` 解析一个模板，返回 (模板, 结束下标)。"""
    index = start + 2
    depth = 1
    length = len(text)
    while index < length:
        if text.startswith("{{", index):
            depth += 1
            index += 2
        elif text.startswith("}}", index):
            depth -= 1
            index += 2
            if depth == 0:
                return _build_template(text[start + 2 : index - 2]), index
        else:
            index += 1
    return None


def _build_template(body: str) -> Template:
    parts = _split_top_level(body)
    args: list[TemplateArg] = []
    for part in parts[1:]:
        key, value = _split_key_value(part)
        if key is None or not key.strip():
            # 空键按 MediaWiki 的旧行为当位置参数，整段（含 =）都是值
            args.append(TemplateArg(key=None, value=part.strip()))
        else:
            args.append(TemplateArg(key=key.strip(), value=value.strip()))
    return Template(name=parts[0].strip(), args=tuple(args))


def _split_top_level(body: str) -> list[str]:
    """按顶层 ``|`` 切分模板正文：``{{…}}`` 与 ``[[…]]`` 里的 ``|`` 不算分隔符。"""
    parts: list[str] = []
    buffer: list[str] = []
    braces = 0
    links = 0
    index = 0
    length = len(body)
    while index < length:
        pair = body[index : index + 2]
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
        if body[index] == "|" and braces == 0 and links == 0:
            parts.append("".join(buffer))
            buffer = []
            index += 1
            continue
        buffer.append(body[index])
        index += 1
    parts.append("".join(buffer))
    return parts


def _split_key_value(part: str) -> tuple[str | None, str]:
    """在顶层找第一个 ``=``（``{{…}}`` 与 ``[[…]]`` 里的不算），没有就返回 (None, part)。"""
    braces = 0
    links = 0
    index = 0
    length = len(part)
    while index < length:
        pair = part[index : index + 2]
        if pair == "{{":
            braces += 1
            index += 2
            continue
        if pair == "}}":
            braces = max(braces - 1, 0)
            index += 2
            continue
        if pair == "[[":
            links += 1
            index += 2
            continue
        if pair == "]]":
            links = max(links - 1, 0)
            index += 2
            continue
        if part[index] == "=" and braces == 0 and links == 0:
            return part[:index], part[index + 1 :]
        index += 1
    return None, part


def _render(text: str) -> str:
    """递归渲染：模板换成展示文本、链接换成显示名、三花括号与未闭合的括号保持原文。"""
    out: list[str] = []
    index = 0
    length = len(text)
    while index < length:
        if text.startswith("{{{", index):
            out.append("{{{")
            index += 3
            continue
        if text.startswith("{{", index):
            parsed = _parse_template(text, index)
            if parsed is None:
                out.append("{{")
                index += 2
                continue
            template, end = parsed
            out.append(_render(_template_text(template)))
            index = end
            continue
        if text.startswith("[[", index):
            end = text.find("]]", index + 2)
            if end != -1:
                out.append(_render(_link_text(text[index + 2 : end])))
                index = end + 2
                continue
        out.append(text[index])
        index += 1
    return "".join(out)


def _template_text(template: Template) -> str:
    """展示类模板取文字；不产出正文的解析器函数取空串。

    默认取**最后一个位置参数**；``_FIRST_POSITIONAL_TEMPLATES`` 里的模板取第一个。
    """
    if template.name.casefold().split(":", 1)[0] in _SILENT_FUNCTIONS:
        return ""
    positional = template.positional()
    if not positional:
        return ""
    if template.name in _FIRST_POSITIONAL_TEMPLATES:
        return positional[0]
    return positional[-1]


def _link_text(inner: str) -> str:
    """``[[目标|显示名]]`` 的显示名；文件/分类/特殊命名空间整条丢掉。"""
    parts = _split_top_level(inner)
    target = parts[0].strip()
    namespace = target.split(":", 1)[0].strip().casefold() if ":" in target else ""
    if namespace in _NON_TEXT_NAMESPACES:
        return ""
    if len(parts) > 1:
        label = parts[-1].strip()
        return label or target
    return target