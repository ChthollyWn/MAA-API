"""共享的 wikitext 小工具：材料消耗与 ``键=值`` 参数表（规格 §6.3）。

这个模块是 M26 三个解析器（干员 / 敌人 / 材料）的公共底座，只做纯函数：字符串进、
结构出，不联网、不读盘。放在单独的模块里是为了让 ``operator`` / ``enemy`` / ``item``
三边共用同一套材料数量口径，不各自实现一遍。

材料消耗的实测形状（2026-10-10，银灰 / 史尔特尔）：

- ``{{材料消耗|固源岩组|4}}``：第二、三个位置参数是名称与数量；
- ``{{材料消耗|龙门币|3w}}``：``w`` 后缀是「万」，``3w`` = 30000；
- ``{{材料消耗|龙门币|8万}}``：模组材料写中文「万」（精英化与技能升级写 ``w``），
  两种后缀都按万处理（规格 §6.3 只写了 ``w``，这里按实测放宽，见 M26 Task 2 报告）；
- 数量写不出整数时记 0（残页自保），同名材料相加合并、保持首次出现的顺序。
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Mapping

from maa_api.services.prts_parse.wikitext import parse_templates

__all__ = [
    "MATERIAL_COST_TEMPLATE",
    "MaterialCost",
    "parse_material_costs",
    "parse_wikitext",
]

#: 材料消耗模板名（实测）。
MATERIAL_COST_TEMPLATE = "材料消耗"

#: 数量：``4``、``3w``、``8万``（``w``/``万`` 都是「万」）。允许小数与内部空白。
_AMOUNT = re.compile(r"^(?P<number>\d+(?:\.\d+)?)\s*(?P<scale>[wW万]?)$")

#: 空的 ``键=值`` 行（``{{属性}}`` 里用来分隔字段组）。
_BLANK_ARG = re.compile(r"^[=\s]*$")


@dataclass(frozen=True, slots=True)
class MaterialCost:
    """一条材料消耗：名称 + 已归一成整数的数量（``3w`` → 30000）。"""

    name: str
    amount: int


def parse_material_costs(text: str) -> tuple[MaterialCost, ...]:
    """取出 ``text`` 里全部 ``{{材料消耗}}``，同名相加合并（保持首次出现顺序）。

    忽略其它模板与模板外的文本；``{{材料消耗}}`` 缺名称或数量时跳过那一条。
    """
    merged: dict[str, int] = {}
    for template in parse_templates(text):
        if template.name != MATERIAL_COST_TEMPLATE:
            continue
        args = [arg for arg in template.args if not _BLANK_ARG.match(arg.value)]
        positional = [arg.value for arg in args if arg.key is None]
        name = _param(args, "名称")
        if name is None and positional:
            name = positional[0]
        amount_text = _param(args, "数量")
        if amount_text is None and len(positional) > 1:
            amount_text = positional[1]
        cleaned = (name or "").strip()
        if not cleaned:
            continue
        if amount_text is None or not amount_text.strip():
            continue
        merged[cleaned] = merged.get(cleaned, 0) + _amount(amount_text)
    return tuple(MaterialCost(name=name, amount=amount) for name, amount in merged.items())


def parse_wikitext(text: str) -> Mapping[str, str]:
    """``|键=值`` 参数表（按出现顺序），跳过位置参数与空行。

    ``{{属性}}``（干员数值）与敌人 / 材料页的属性模板都是这个形状；同名键后写的覆盖
    先写的。模板外的文本、位置参数、值里带 ``=`` 的都不影响：``_split_key_value``
    只在顶层找第一个 ``=``。
    """
    params: dict[str, str] = {}
    for part in _top_level_parts(text):
        key, value = _split_key_value(part)
        if key is None:
            continue
        cleaned = key.strip()
        if not cleaned:
            continue
        params[cleaned] = value.strip()
    return params


def _param(args: list, name: str) -> str | None:
    for arg in args:
        if arg.key == name:
            return arg.value
    return None


def _amount(value: str | None) -> int:
    """``4`` / ``3w`` / ``8万`` → 整数；解析不了记 0。"""
    if not value:
        return 0
    match = _AMOUNT.match(value.strip())
    if match is None:
        return 0
    number = float(match.group("number"))
    if match.group("scale"):
        number *= 10000
    return int(number)


def _top_level_parts(text: str) -> list[str]:
    """按顶层 ``|`` 切分：``{{…}}`` 与 ``[[…]]`` 里的 ``|`` 不算分隔符。"""
    parts: list[str] = []
    buffer: list[str] = []
    braces = 0
    links = 0
    index = 0
    length = len(text)
    while index < length:
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
        if text[index] == "|" and braces == 0 and links == 0:
            parts.append("".join(buffer))
            buffer = []
            index += 1
            continue
        buffer.append(text[index])
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
