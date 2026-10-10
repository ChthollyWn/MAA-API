"""基建技能解析（规格 §8.3）：Cargo ``building_skill2`` 整表 + 干员页 ``{{后勤技能}}``。

纯函数：HTML/wikitext 进，结构出；不联网、不读盘。

实测要点（2026-10-10，PRTS 与 2026-10-08 的 CN 资源包）：

- ``building_skill2``（Cargo）三列 ``name`` / ``room`` / ``description``，整表 720 行、
  720 个互不重复的技能名；``description`` 是 **HTML**（不是 wikitext），带
  ``<span style="color:…">`` 着色与 ``<span class="mc-tooltips">`` 术语浮层；浮层里
  ``<span style="display:none">`` 的隐藏文本（``术语: …``、包含哪些干员、叠加规则）必须整段
  丢掉，否则会把「45% 生产力」这种解释文字混进技能效果。
- 干员页 ``==后勤技能==`` 段落只有一个 ``{{后勤技能}}`` 块，参数键是
  ``后勤技能<槽位>-<序号>`` 与 ``后勤技能<槽位>-<序号>阶段``（实测 80 名干员全是这几
  种写法，``阶段`` 目前只出现 ``精英0`` / ``精英1`` / ``精英2``）。
- 同一槽位里序号大的在更晚的阶段替换序号小的（森蚺 ``1-1`` 自动化·α 精英0 →
  ``1-2`` 自动化·β 精英2；迷迭香 ``2-1`` 念力 精英0 → ``2-2`` 意识实体 精英2）。

实现裁决（规格没写死，由本模块定）：

1. 只有**第一个** ``{{后勤技能}}`` 块生效（与 `operator` 解析器对 ``{{CharinfoV2}}`` 的
   口径一致）；同名参数出现多次时取第一个。
2. ``阶段`` 只做两种归一：``精英N`` → 精英化 N 级、``等级N`` → 等级 N；其余原文带出，
   不猜含义（``elite_requirement`` / ``level_requirement`` 对认不出的写法返回 ``None``）。
3. 重复技能名（同一次查询里）只留第一条；空名丢弃。整表查询按房间分块，块间可能重叠，
   这里兜底去重。
4. 效率提示是从**清洗后的描述**里正则抽出来的数字（描述本身照原样保留），不做数值换算，
   也不合并同类项：描述里写了几档就列几档，宁可多列也不替模型判断哪档更强。
"""

from __future__ import annotations

import html
import re
from dataclasses import dataclass
from typing import Any, Iterable, Mapping

from maa_api.services.prts_parse.wikitext import find_templates

__all__ = [
    "OPERATOR_BUILDING_TEMPLATE",
    "BuildingSkill",
    "BuildingSkillEntry",
    "clean_building_description",
    "efficiency_hints",
    "parse_building_skill_table",
    "parse_operator_building_skills",
]

#: 干员页里的后勤技能模板名。
OPERATOR_BUILDING_TEMPLATE = "后勤技能"

#: ``后勤技能<槽位>-<序号>``：槽位是干员的第几个后勤技能，序号是同槽位的档位。
_SKILL_KEY = re.compile(r"^后勤技能(?P<group>\d+)-(?P<variant>\d+)$")

#: ``精英N`` / ``等级N``（实测只有前者；后者留作兜底）。
_ELITE = re.compile(r"^精英(?P<level>\d+)$")
_LEVEL = re.compile(r"^等级(?P<level>\d+)$")

#: 任意 HTML 标签；用于浮层剔除与去标签。
_TAG = re.compile(r"<[^>]*>")
_HIDDEN_SPAN = re.compile(r'<span\b[^>]*style="[^"]*display\s*:\s*none[^"]*"[^>]*>', re.I)
_BR = re.compile(r"<br\s*/?>", re.I)
#: 自闭合/空元素：不改变嵌套深度。
_VOID_TAGS = frozenset({"br", "hr", "img", "wbr", "input", "meta", "link", "col", "area", "base"})
_TAG_NAME = re.compile(r"^</?\s*(?P<name>[A-Za-z][A-Za-z0-9]*)")

#: 从描述里抽效率提示：``(模式, 标签, 单位)``，值在第一个捕获组。
_HINT_PATTERNS: tuple[tuple[re.Pattern[str], str, str], ...] = (
    (re.compile(r"订单获取效率[^，。；]{0,6}?([+-]\d+(?:\.\d+)?)%"), "订单获取效率", "%"),
    (re.compile(r"订单上限[^，。；]{0,6}?([+-]\d+)"), "订单上限", ""),
    (re.compile(r"生产力[^，。；]{0,10}?([+-]\d+(?:\.\d+)?)%"), "生产力", "%"),
    (re.compile(r"([+-]\d+(?:\.\d+)?)%的生产力"), "生产力", "%"),
    (re.compile(r"仓库容量上限[^，。；]{0,6}?([+-]\d+)"), "仓库容量上限", ""),
    (re.compile(r"心情每小时消耗[^，。；]{0,6}?([+-]\d+(?:\.\d+)?)"), "心情消耗", "/小时"),
    (re.compile(r"心情每小时恢复[^，。；]{0,6}?([+-]\d+(?:\.\d+)?)"), "心情恢复", "/小时"),
    (re.compile(r"配方全部([+-]\d+)心情消耗"), "配方心情消耗", ""),
    (re.compile(r"训练速度[^，。；]{0,6}?([+-]\d+(?:\.\d+)?)%"), "训练速度", "%"),
    (re.compile(r"搜集速度[^，。；]{0,6}?([+-]\d+(?:\.\d+)?)%"), "线索搜集速度", "%"),
    (re.compile(r"联络速度[^，。；]{0,6}?([+-]\d+(?:\.\d+)?)%"), "联络速度", "%"),
    (re.compile(r"充能速度[^，。；]{0,6}?([+-]\d+(?:\.\d+)?)%"), "充能速度", "%"),
    (re.compile(r"材料转化效率[^，。；]{0,6}?([+-]\d+(?:\.\d+)?)%"), "材料转化效率", "%"),
)

#: 阶梯型技能（实测 7 条，都是「首小时 X%，此后每小时 Y%，最终达到 Z%」的形状）：
#: 三个数一起给，避免只报首小时让人误判强度。
_RAMP = re.compile(
    r"(?P<label>生产力|无人机充能速度|线索搜集速度|订单获取效率|训练速度|材料转化效率)"
    r"(?P<first_word>首小时)?(?:提升)?(?P<first>[+-]?\d+(?:\.\d+)?)%"
    r"[，,]?\s*此后每小时(?:提升)?(?P<step>[+-]?\d+(?:\.\d+)?)%"
    r"[，,]?\s*最终达到(?P<final>[+-]?\d+(?:\.\d+)?)%"
)

#: 一条提示最多列几个数字（描述里的档位再多也不刷屏）。
MAX_HINTS = 6


@dataclass(frozen=True, slots=True)
class BuildingSkill:
    """Cargo ``building_skill2`` 的一行：技能名、房间、清洗后的描述。"""

    name: str
    room: str | None
    description: str

    def wire(self) -> dict[str, Any]:
        return {"name": self.name, "room": self.room, "description": self.description}


@dataclass(frozen=True, slots=True)
class BuildingSkillEntry:
    """干员页 ``{{后勤技能}}`` 的一条：技能名、解锁条件原文、槽位与档位。"""

    name: str
    unlock: str | None
    slot: str
    group: int | None
    variant: int | None

    @property
    def elite_requirement(self) -> int | None:
        """解锁需要的精英化等级；``阶段`` 不是 ``精英N`` 时返回 ``None``。"""
        match = _ELITE.match((self.unlock or "").strip())
        return int(match.group("level")) if match else None

    @property
    def level_requirement(self) -> int | None:
        """解锁需要的等级；``阶段`` 不是 ``等级N`` 时返回 ``None``。"""
        match = _LEVEL.match((self.unlock or "").strip())
        return int(match.group("level")) if match else None

    def wire(self) -> dict[str, Any]:
        return {
            "name": self.name,
            "unlock": self.unlock,
            "slot": self.slot,
        }


def clean_building_description(value: Any) -> str:
    """Cargo 的 HTML 描述 → 纯文本；隐藏浮层整段丢弃、``<br>`` 转换行、实体解码。"""
    if not isinstance(value, str) or not value:
        return ""
    text = _strip_hidden_spans(value)
    text = _BR.sub("\n", text)
    text = _TAG.sub("", text)
    text = html.unescape(text).replace("\xa0", " ")
    lines = [line.strip() for line in text.splitlines()]
    return "\n".join(line for line in lines if line)


def efficiency_hints(description: str, *, room: str | None = None) -> list[str]:
    """从描述里抽效率提示（按描述顺序，最多 :data:`MAX_HINTS` 条）。

    ``room`` 非空时给标签加房间前缀（``制造站生产力 +25%``），与规格 §8.3 的示例一致。
    阶梯型技能给「首小时 / 此后每小时 / 最终」三个数；普通技能给「标签 + 数值」。
    """
    text = description or ""
    prefix = room or ""
    found: list[tuple[int, str]] = []
    ramp_spans: list[tuple[int, int]] = []
    for match in _RAMP.finditer(text):
        first_word = "首小时" if match.group("first_word") else "起始"
        label = match.group("label")
        found.extend(
            (
                (match.start(), f"{prefix}{label}（{first_word}） {match.group('first')}%"),
                (
                    match.start("step"),
                    f"{prefix}{label}（此后每小时） {match.group('step')}%",
                ),
                (match.start("final"), f"{prefix}{label}（最终） {match.group('final')}%"),
            )
        )
        ramp_spans.append((match.start(), match.end()))
    for pattern, label, unit in _HINT_PATTERNS:
        for match in pattern.finditer(text):
            if any(start <= match.start() < end for start, end in ramp_spans):
                continue
            found.append((match.start(), f"{prefix}{label} {match.group(1)}{unit}"))
    found.sort(key=lambda item: item[0])
    hints: list[str] = []
    for _position, hint in found:
        if hint not in hints:
            hints.append(hint)
    return hints[:MAX_HINTS]


def parse_building_skill_table(rows: Iterable[Any]) -> tuple[BuildingSkill, ...]:
    """Cargo 行（``{"title": {...}}`` 或直接字段映射）→ :class:`BuildingSkill` 表。"""
    table: list[BuildingSkill] = []
    seen: set[str] = set()
    for row in rows:
        payload: Any = row
        if isinstance(row, Mapping) and isinstance(row.get("title"), Mapping):
            payload = row["title"]
        if not isinstance(payload, Mapping):
            continue
        raw_name = payload.get("name")
        if not isinstance(raw_name, str) or not raw_name.strip():
            continue
        name = raw_name.strip()
        if name in seen:
            continue
        seen.add(name)
        raw_room = payload.get("room")
        room = raw_room.strip() if isinstance(raw_room, str) and raw_room.strip() else None
        table.append(
            BuildingSkill(
                name=name,
                room=room,
                description=clean_building_description(payload.get("description")),
            )
        )
    return tuple(table)


def parse_operator_building_skills(
    wikitext: str, *, title: str | None = None
) -> tuple[BuildingSkillEntry, ...]:
    """干员页 ``{{后勤技能}}`` → 条目（按槽位、档位排序；只有第一个模板块生效）。"""
    blocks = find_templates(wikitext or "", OPERATOR_BUILDING_TEMPLATE)
    if not blocks:
        return ()
    values: dict[str, str] = {}
    for arg in blocks[0].args:
        if arg.key is None or arg.key in values:
            continue
        values[arg.key.strip()] = arg.value.strip()
    entries: list[BuildingSkillEntry] = []
    for key, value in values.items():
        match = _SKILL_KEY.match(key)
        if match is None or not value:
            continue
        entries.append(
            BuildingSkillEntry(
                name=value,
                unlock=values.get(f"{key}阶段") or None,
                slot=f"{match.group('group')}-{match.group('variant')}",
                group=int(match.group("group")),
                variant=int(match.group("variant")),
            )
        )
    entries.sort(key=lambda item: (item.group or 0, item.variant or 0))
    return tuple(entries)


def _strip_hidden_spans(text: str) -> str:
    """删掉 ``display:none`` 的 span 及其全部嵌套内容（工具浮层的隐藏文本）。"""
    kept: list[str] = []
    position = 0
    depth = 0
    hide_depth = 0
    for match in _TAG.finditer(text):
        if hide_depth == 0:
            kept.append(text[position : match.start()])
        position = match.end()
        tag = match.group(0)
        name_match = _TAG_NAME.match(tag)
        name = (name_match.group("name") if name_match else "").lower()
        if tag.startswith("</"):
            depth = max(depth - 1, 0)
            if hide_depth and depth < hide_depth:
                hide_depth = 0
            continue
        if tag.endswith("/>") or name in _VOID_TAGS:
            continue
        depth += 1
        if hide_depth == 0 and name == "span" and _HIDDEN_SPAN.match(tag):
            hide_depth = depth
    if hide_depth == 0:
        kept.append(text[position:])
    return "".join(kept)
