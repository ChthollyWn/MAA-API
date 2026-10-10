"""干员页解析（规格 §6.3）：`{{CharinfoV2}}` 等模板 → :class:`OperatorPage`。

纯函数：wikitext 进，结构出，不联网、不读盘。字段白名单照计划里的 T1 实测表
（2026-10-10，银灰 / 史尔特尔 / 克洛丝）。

实测要点：

- ``{{CharinfoV2}}``：``稀有度`` 从 0 数（``5`` 是 6★）；``特性`` 带 ``{{color}}``
  之类展示模板，要清洗；
- ``{{属性}}``：键值原样带出（``部署费用=18→20``、``精英2_满级_生命上限=2560``、
  ``潜能=-1,-4,26,,-1``），换算交给领域层；
- ``{{干员攻击范围}}``：``精英0范围=2-3`` 是范围编号；
- ``{{天赋列表3}}``：每个条目 ``天赋N`` / ``天赋N条件`` / ``天赋N效果``；``天赋N条件``
  里写模组等级的条目（``精英2 X模组2级``）归模组，基础天赋只留非模组条目；
- 潜能天赋的键有两种实测写法：``潜能增强_天赋1``（银灰、克洛丝）与
  ``潜能增强_3天赋效果``（史尔特尔，数字在前、没有第二个下划线），两种都认；一个槽位
  会有 ``…`` / ``…条件`` / ``…效果`` 三条键，按槽位合成一条（名字与条件缺失时从同槽位
  ``天赋N`` 借，效果优先用 ``…效果`` 那条）；
- ``{{技能}}`` / ``{{技能2}}`` / ``{{技能3}}``：``技能N{初始,消耗,持续,描述}``
  （N = 1..7）与 ``技能专精N*``；``持续`` 为空串时按没有处理；
- ``{{精英化材料}}``（``精1`` / ``精2``）、``{{技能升级材料}}``（``2..7``、
  ``一8..一10``、``二8..``、``三8..``）、``{{模组}}`` 的 ``材料消耗N`` 都走
  :func:`~maa_api.services.prts_parse.costs.parse_material_costs`。

实现裁决（规格没写死、由本模块定）：

1. 只有整块模板的**第一个** ``{{CharinfoV2}}`` / ``{{干员获得方式}}`` / ``{{属性}}`` /
   ``{{干员攻击范围}}`` / ``{{精英化材料}}`` / ``{{技能升级材料}}`` / ``{{潜能提升}}``
   生效；``{{天赋列表3}}``、``{{技能}}`` 系列与 ``{{模组}}`` 按页面出现顺序**逐个**生效。
2. 技能模板名按 ``技能`` + 可选数字整体匹配（``技能``、``技能2``、``技能3``…），
   不是 ``技能`` 前缀匹配，免得把 ``技能升级材料`` 之类也算进来。
3. ``{{属性}}`` 与 ``range_codes`` 原样保留（未清洗），其余文本字段过
   :func:`clean_text`／:func:`~maa_api.services.prts_parse.wikitext.strip_markup`；
   长文本（``描述`` / ``效果`` / ``备注``）保留换行。
4. ``稀有度`` 越界时夹到 1–6，解析不出数字给 ``None``。
5. 空的 ``|键=`` 与空行按「页面没写」处理（``None``），不当空字符串。
6. ``{{天赋列表3}}`` 里 ``天赋N条件`` 带「模组」的条目归模组：基础天赋进 ``talents``，
   模组那几条进对应 ``{{模组}}`` 的 ``talents`` / ``potential_talents``（模组模板自己
   不写条件，条件从同名天赋条目借；同名有 X 模组 2 级 / 3 级两条时，按 ``天赋2`` →
   模组 2 级、``天赋3`` → 模组 3 级对应）。
7. 实测模板名里可能夹注释（``{{CharinfoV2`` + 换行 + ``<!--…-->``），本模块按「去掉注释
   与空白」后的名字匹配；``operator`` / ``enemy`` / ``item`` 三个解析器都该这么匹配，
   没有回头改 ``activity`` / ``stage``（它们只看没有夹注释的模板）。
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Any, Mapping

from maa_api.services.prts_parse.activity import clean_text, template_param
from maa_api.services.prts_parse.costs import (
    MaterialCost,
    parse_material_costs,
    parse_wikitext,
)
from maa_api.services.prts_parse.wikitext import Template, parse_templates, strip_markup

__all__ = [
    "CHAR_INFO_TEMPLATE",
    "MODULE_TEMPLATE",
    "OperatorModule",
    "OperatorPage",
    "OperatorSkill",
    "OperatorSkillLevel",
    "OperatorTalent",
    "parse_operator_page",
]

CHAR_INFO_TEMPLATE = "CharinfoV2"
OBTAIN_TEMPLATE = "干员获得方式"
STATS_TEMPLATE = "属性"
RANGE_TEMPLATE = "干员攻击范围"
TALENT_TEMPLATE = "天赋列表3"
POTENTIAL_TEMPLATE = "潜能提升"
ELITE_COST_TEMPLATE = "精英化材料"
SKILL_COST_TEMPLATE = "技能升级材料"
MODULE_TEMPLATE = "模组"

#: 技能模板：``技能``、``技能2``、``技能3``（整体匹配，见模块 docstring 裁决 2）。
SKILL_TEMPLATE = re.compile(r"^技能(?P<index>\d*)$")

#: 稀有度是从 0 数的星级。
_RARITY = re.compile(r"^(?P<value>\d+)$")

#: ``天赋N`` / ``天赋N条件`` / ``天赋N效果``。
_TALENT_ENTRY = re.compile(r"^天赋(?P<index>\d+)(?P<field>条件|效果)?$")

#: ``潜能增强_天赋1[条件|效果]``（银灰、克洛丝写法）。
_POTENTIAL_TALENT = re.compile(r"^潜能增强_天赋(?P<index>\d+)(?P<field>条件|效果)?$")

#: ``潜能增强_3天赋效果``（史尔特尔写法：数字在前、没有第二个下划线）。
_POTENTIAL_TALENT_REVERSED = re.compile(r"^潜能增强_(?P<index>\d+)天赋(?P<field>条件|效果)?$")

#: ``技能1`` / ``技能专精1`` + ``初始|消耗|持续|描述``。
_SKILL_LEVEL = re.compile(
    r"^技能(?P<mastery>专精)?(?P<index>[1-7])(?P<field>初始|消耗|持续|描述)$"
)

#: ``材料消耗`` / ``材料消耗2`` / ``材料消耗3``（模组按等级）。
_MODULE_COST = re.compile(r"^材料消耗(?P<level>[1-3]?)$")

#: 模组的属性加成：``生命`` / ``生命2`` / ``生命3``（无后缀 = 1 级）。
_MODULE_STAT = re.compile(r"^(?P<name>.+?)(?P<level>[1-3])?$")

#: 模组里不是「属性加成」的键（其余带数值的键都当属性）。
_MODULE_NON_STAT = frozenset(
    {
        "名称",
        "模组图标",
        "类型图标",
        "类型",
        "类型颜色",
        "分支",
        "基础证章",
        "基础信息",
        "特性追加",
        "特性",
        "解锁等级",
        "解锁信赖",
        "解锁信赖2",
        "解锁信赖3",
        "任务1",
        "任务2",
        "任务3",
        "天赋",
        "天赋2",
        "天赋3",
        "潜能",
        "潜能天赋2",
        "潜能天赋3",
        "材料消耗",
        "材料消耗2",
        "材料消耗3",
    }
)

#: 模组天赋条目的名字：``天赋【领袖】更新``。
_MODULE_TALENT_NAME = re.compile(r"^天赋【(?P<name>[^】]+)】")

#: 技能等级的取值：``初始`` / ``消耗`` / ``持续`` / ``描述``。
_SKILL_FIELD = {"初始": "sp_initial", "消耗": "sp_cost", "持续": "duration", "描述": "description"}


@dataclass(frozen=True, slots=True)
class OperatorSkillLevel:
    """一个技能等级（``1``..``7`` / ``专精1``..``专精3``）。"""

    level: str
    sp_initial: str | None
    sp_cost: str | None
    duration: str | None
    description: str | None


@dataclass(frozen=True, slots=True)
class OperatorSkill:
    """一个技能：名称、两种类型、各级数值与备注。"""

    name: str
    charge_type: str | None
    trigger_type: str | None
    levels: tuple[OperatorSkillLevel, ...]
    note: str | None


@dataclass(frozen=True, slots=True)
class OperatorTalent:
    """一条天赋（或模组带来的天赋更新）：名称、解锁条件、效果。"""

    name: str
    condition: str | None
    effect: str | None


@dataclass(frozen=True, slots=True)
class OperatorModule:
    """一个模组：类型代号、各级属性、特性追加、天赋更新与各级材料。"""

    name: str
    type_code: str | None
    trait_added: bool
    trait: str | None
    level_stats: tuple[tuple[int, Mapping[str, str]], ...]
    talents: tuple[OperatorTalent, ...]
    potential_talents: tuple[OperatorTalent, ...]
    costs: tuple[tuple[int, tuple[MaterialCost, ...]], ...]


@dataclass(frozen=True, slots=True)
class OperatorPage:
    """干员页解析结果；缺字段一律 ``None`` / 空元组 / 空映射，不抛异常。"""

    title: str
    name: str | None
    char_id: str | None
    rarity: int | None
    profession: str | None
    branch: str | None
    trait: str | None
    tags: tuple[str, ...]
    position: str | None
    info_number: str | None
    obtain: str | None
    release_time: str | None
    stats: Mapping[str, str]
    range_codes: Mapping[str, str]
    elite_costs: Mapping[str, tuple[MaterialCost, ...]]
    skill_costs: Mapping[str, tuple[MaterialCost, ...]]
    skills: tuple[OperatorSkill, ...]
    talents: tuple[OperatorTalent, ...]
    potential_talents: tuple[OperatorTalent, ...]
    potentials: Mapping[str, str]
    modules: tuple[OperatorModule, ...]


def parse_operator_page(content: str, *, title: str) -> OperatorPage:
    """解析一个干员页；``title`` 是页面标题（重定向解析后的）。"""
    templates = parse_templates(content)

    info = _first_template(templates, CHAR_INFO_TEMPLATE)
    obtain = _first_template(templates, OBTAIN_TEMPLATE)
    stats = _first_template(templates, STATS_TEMPLATE)
    ranges = _first_template(templates, RANGE_TEMPLATE)
    elite = _first_template(templates, ELITE_COST_TEMPLATE)
    skill_costs = _first_template(templates, SKILL_COST_TEMPLATE)
    potentials = _first_template(templates, POTENTIAL_TEMPLATE)

    talents: list[OperatorTalent] = []
    potential_talents: list[OperatorTalent] = []
    talent_conditions: dict[str, tuple[str, ...]] = {}
    for template in _templates_named(templates, TALENT_TEMPLATE):
        block_talents, block_potentials, conditions = _parse_talent_block(template)
        talents.extend(block_talents)
        potential_talents.extend(block_potentials)
        # 模组模板里没有写解锁条件，条件在 ``{{天赋列表3}}`` 的同名条目上。
        for name, items in conditions.items():
            talent_conditions[name] = talent_conditions.get(name, ()) + items

    return OperatorPage(
        title=title,
        name=_text(template_param(info, "干员名")),
        char_id=_text(template_param(info, "干员id")),
        rarity=_rarity(template_param(info, "稀有度")),
        profession=_text(template_param(info, "职业")),
        branch=_text(template_param(info, "分支")),
        trait=_text(template_param(info, "特性")),
        tags=_words(template_param(info, "标签")),
        position=_text(template_param(info, "位置")),
        info_number=_text(template_param(info, "情报编号")),
        obtain=_text(template_param(obtain, "获得方式")),
        release_time=_text(template_param(obtain, "上线时间")),
        stats=_param_map(_param_text(stats)),
        range_codes=_param_map(_param_text(ranges)),
        elite_costs=_costs_by_key(_param_map(_param_text(elite))),
        skill_costs=_costs_by_key(_param_map(_param_text(skill_costs))),
        skills=tuple(
            _parse_skill(template)
            for template in templates
            if SKILL_TEMPLATE.match(_template_name(template))
        ),
        talents=tuple(talents),
        potential_talents=tuple(potential_talents),
        potentials=_text_map(_param_map(_param_text(potentials))),
        modules=tuple(
            _parse_module(template, talent_conditions)
            for template in _templates_named(templates, MODULE_TEMPLATE)
        ),
    )


# -- 天赋 ---------------------------------------------------------------------


def _parse_talent_block(
    template: Template,
) -> tuple[list[OperatorTalent], list[OperatorTalent], dict[str, tuple[str, ...]]]:
    """一个 ``{{天赋列表3}}`` → (基础天赋, 潜能天赋, 模组条目用的条件表)。

    ``天赋N`` 是名字、``天赋N条件`` 解锁条件、``天赋N效果`` 效果；条件里写模组等级的
    条目归模组（见模块 docstring），不进 ``talents`` / ``potential_talents``，但它的
    条件按天赋名记下来给 ``{{模组}}`` 里的天赋更新条目用（模组模板自己不写条件）。
    同名天赋会有多条模组条件（X 模组 2 级 / 3 级），所以条件值是元组、按出现顺序。
    """
    parsed = _param_map(_param_text(template))
    base: dict[int, dict[str, str]] = {}
    for key, value in parsed.items():
        match = _TALENT_ENTRY.match(key)
        if match is None:
            continue
        base.setdefault(int(match.group("index")), {})[match.group("field") or "name"] = value

    talents: list[OperatorTalent] = []
    conditions: dict[str, list[str]] = {}

    def remember(name: str, condition: str) -> bool:
        """模组条件记进条件表并返回 True（这条不进基础天赋）。"""
        if not _is_module_condition(condition):
            return False
        conditions.setdefault(name, []).append(condition)
        return True

    for index in sorted(base):
        fields = base[index]
        name = _text(fields.get("name"))
        if not name:
            continue
        condition = _text(fields.get("条件"))
        if condition is not None and remember(name, condition):
            continue
        talents.append(
            OperatorTalent(name=name, condition=condition, effect=_multiline(fields.get("效果")))
        )

    # 一个潜能天赋槽位在页面上有三个键（``潜能增强_天赋1`` / ``…条件`` / ``…效果``），
    # 所以按槽位收，每个槽位只出一条。名字与条件取 ``…条件`` / ``…效果`` 解出的值，
    # 缺了回落到同槽位 ``天赋N`` 上的值；效果优先用 ``潜能增强_*`` 自己的（它才是增强后
    # 的数值），并且从 ``…效果`` 那条取，查不到才用 ``天赋N效果`` 兜底。
    slots: dict[int, dict[str, str]] = {}
    for key, value in parsed.items():
        match = _POTENTIAL_TALENT.match(key) or _POTENTIAL_TALENT_REVERSED.match(key)
        if match is None:
            continue
        index = int(match.group("index"))
        field = match.group("field") or "name"
        slots.setdefault(index, {})[field] = value

    potentials: list[OperatorTalent] = []
    for index in sorted(slots):
        fields = dict(base.get(index, {}))
        fields.update(slots[index])
        name = _text(fields.get("name"))
        effect = _multiline(fields.get("效果"))
        condition = _text(fields.get("条件"))
        if not name:
            continue
        if _is_module_condition(condition):
            # 条件里写模组等级的槽位归模组的 ``potential_talents``（见模块 docstring）；
            # 条件也要记下来给 ``{{模组}}`` 的同名天赋更新条目用。
            conditions.setdefault(name, []).append(condition)
            continue
        potentials.append(OperatorTalent(name=name, condition=condition, effect=effect))
    return talents, potentials, {name: tuple(items) for name, items in conditions.items()}


def _is_module_condition(condition: str | None) -> bool:
    """``精英2 X模组2级`` 这种解锁条件属于模组，不是基础天赋。"""
    return condition is not None and "模组" in condition


# -- 技能 ---------------------------------------------------------------------


def _parse_skill(template: Template) -> OperatorSkill:
    parsed = _param_map(_param_text(template))
    fields: dict[str, dict[str, str | None]] = {}
    for key, value in parsed.items():
        match = _SKILL_LEVEL.match(key)
        if match is None:
            continue
        level = f"专精{match.group('index')}" if match.group("mastery") else match.group("index")
        fields.setdefault(level, {})[_SKILL_FIELD[match.group("field")]] = _multiline(value)

    levels = tuple(
        OperatorSkillLevel(
            level=level,
            sp_initial=fields[level].get("sp_initial"),
            sp_cost=fields[level].get("sp_cost"),
            duration=fields[level].get("duration"),
            description=fields[level].get("description"),
        )
        for level in _ordered_levels(fields)
    )
    return OperatorSkill(
        name=_text(template_param(template, "技能名")) or "",
        charge_type=_text(template_param(template, "技能类型1")),
        trigger_type=_text(template_param(template, "技能类型2")),
        levels=levels,
        note=_multiline(template_param(template, "备注")),
    )


def _ordered_levels(fields: Mapping[str, Any]) -> list[str]:
    """技能等级顺序：``1``..``7`` 在前，``专精1``..``专精3`` 在后，缺的跳过。"""
    plain = [str(index) for index in range(1, 8) if str(index) in fields]
    mastery = [f"专精{index}" for index in range(1, 4) if f"专精{index}" in fields]
    return plain + mastery


# -- 模组 ---------------------------------------------------------------------


def _parse_module(
    template: Template, talent_conditions: Mapping[str, tuple[str, ...]]
) -> OperatorModule:
    parsed = _param_map(_param_text(template))
    raw_stats: dict[int, dict[str, str]] = {}
    costs: dict[int, tuple[MaterialCost, ...]] = {}
    for key, value in parsed.items():
        cost = _MODULE_COST.match(key)
        if cost is not None:
            costs[int(cost.group("level") or 1)] = parse_material_costs(value)
            continue
        stat = _MODULE_STAT.match(key)
        if stat is not None and key not in _MODULE_NON_STAT and not key.startswith("任务"):
            raw_stats.setdefault(int(stat.group("level") or 1), {})[stat.group("name")] = value

    return OperatorModule(
        name=_text(template_param(template, "名称")) or "",
        type_code=_text(template_param(template, "类型")),
        trait_added=_yes(template_param(template, "特性追加")),
        trait=_multiline(template_param(template, "特性")),
        level_stats=tuple(
            (level, dict(raw_stats[level])) for level in sorted(raw_stats) if raw_stats[level]
        ),
        talents=_module_talents(parsed, ("天赋2", "天赋3"), talent_conditions),
        potential_talents=_module_talents(
            parsed, ("潜能天赋2", "潜能天赋3"), talent_conditions
        ),
        costs=tuple((level, costs[level]) for level in sorted(costs)),
    )


def _module_talents(
    parsed: Mapping[str, str],
    keys: tuple[str, ...],
    talent_conditions: Mapping[str, tuple[str, ...]],
) -> tuple[OperatorTalent, ...]:
    """``天赋2`` / ``天赋3`` / ``潜能天赋2`` / ``潜能天赋3`` → 天赋更新条目。

    名字从条目文本里取（``天赋【领袖】更新``），解锁条件从页面上的
    ``{{天赋列表3}}`` 同名条目借（模组模板自己不写条件）。同名天赋可能有多条模组条件
    （``精英2 X模组2级`` / ``精英2 X模组3级``），按模组字母（X / Y / …）与等级下标挑：
    ``天赋2`` 是模组 2 级、``天赋3`` 是模组 3 级，再对不上就用第一条。
    """
    talents: list[OperatorTalent] = []
    for index, key in enumerate(keys, start=2):
        value = parsed.get(key)
        if not value:
            continue
        name = _module_talent_name(value)
        if not name:
            continue
        talents.append(
            OperatorTalent(
                name=name,
                condition=_match_module_condition(talent_conditions.get(name, ()), index),
                effect=_multiline(value),
            )
        )
    return tuple(talents)


def _match_module_condition(conditions: tuple[str, ...], level: int) -> str | None:
    """从同名天赋的模组条件里挑本条对应的那个（见 :func:`_module_talents`）。"""
    if not conditions:
        return None
    suffix = f"模组{level}级"
    for condition in conditions:
        if condition.endswith(suffix):
            return condition
    return conditions[0]


def _module_talent_name(value: str) -> str | None:
    """``天赋【领袖】更新`` → ``领袖``。"""
    match = _MODULE_TALENT_NAME.match(strip_markup(value).strip())
    if match is None:
        return None
    return clean_text(match.group("name"))


# -- 小工具 -------------------------------------------------------------------


def _first_template(templates: list[Template], name: str) -> Template | None:
    for template in templates:
        if _template_name(template) == name:
            return template
    return None


def _templates_named(templates: list[Template], name: str) -> list[Template]:
    return [template for template in templates if _template_name(template) == name]


#: 模板名里的 HTML 注释（实测 ``{{CharinfoV2\n<!--下方为自动更新部分…-->``）。
_NAME_COMMENT = re.compile(r"<!--.*?(?:-->|\Z)", re.S)


def _template_name(template: Template) -> str:
    """模板名的归一化形状：去掉注释与两侧空白。

    实测银灰页写 ``{{CharinfoV2`` + 换行 + ``<!--下方为自动更新部分，您的修改可能会被覆盖-->``，
    ``wikitext`` 把注释前的整段当模板名（注释在模板名内部，不属于参数），不做归一就
    精确匹配不上 ``CharinfoV2``。
    """
    raw = getattr(template, "name", "") or ""
    return _NAME_COMMENT.sub("", raw).strip()


def _text(value: str | None) -> str | None:
    """单行文本：清洗 + 压掉换行（``clean_text`` 已经 strip，这里把内部换行并成空格）。"""
    cleaned = clean_text(value)
    if cleaned is None:
        return None
    return re.sub(r"\s*\n\s*", " ", cleaned)


def _multiline(value: str | None) -> str | None:
    """长文本：清洗但保留换行（``<br>`` 已归一成 ``\\n``）。"""
    return clean_text(value)


def _words(value: str | None) -> tuple[str, ...]:
    """空格分隔的短列表（``标签=输出 支援``）。"""
    cleaned = _text(value)
    return tuple(cleaned.split()) if cleaned else ()


def _rarity(value: str | None) -> int | None:
    """``稀有度`` 从 0 数：``5`` → 6；越界夹到 1–6，解析不了给 ``None``。"""
    cleaned = _text(value)
    if cleaned is None:
        return None
    match = _RARITY.match(cleaned)
    if match is None:
        return None
    return min(max(int(match.group("value")) + 1, 1), 6)


def _yes(value: str | None) -> bool:
    """``yes`` / ``是`` 之类的开关参数。"""
    cleaned = _text(value)
    return cleaned is not None and cleaned.casefold() in {"yes", "y", "true", "1", "是"}


def _param_map(text: str) -> Mapping[str, str]:
    """``|键=值`` 文本的键值表（按出现顺序，同名后写的覆盖）。"""
    return parse_wikitext(text)


def _param_text(template: Template) -> str:
    """把模板的参数还原成 ``|键=值`` 文本，喂给 :func:`parse_wikitext`。"""
    return "".join(_arg_text(arg) for arg in getattr(template, "args", ()))


def _arg_text(arg: object) -> str:
    """把一个模板参数还原成 ``|键=值`` 片段，喂给 :func:`parse_wikitext`。"""
    key = getattr(arg, "key", None)
    value = getattr(arg, "value", "")
    if key is None:
        return f"|{value}"
    return f"|{key}={value}"


def _costs_by_key(params: Mapping[str, str]) -> Mapping[str, tuple[MaterialCost, ...]]:
    """``精1`` / ``2`` / ``一8`` / … → 材料元组（保持页面顺序）。"""
    return {key: parse_material_costs(value) for key, value in params.items() if value}


def _text_map(params: Mapping[str, str]) -> Mapping[str, str]:
    """清洗后的 ``键=值`` 表：``潜能2`` … ``潜能6``、``精英0范围`` …"""
    cleaned: dict[str, str] = {}
    for key, value in params.items():
        text = _text(value)
        if text:
            cleaned[key] = text
    return cleaned
