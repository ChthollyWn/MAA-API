"""敌人页解析（规格 §6.4，M26 Task 3）：`{{敌人信息/common2}}` + 每级一个 `{{敌人信息/levelcontent}}`。

只做纯函数：wikitext 进，结构出，不联网、不读盘。

实测（2026-10-10，源石虫 / 爱国者）：

- 基础字段在 ``{{敌人信息/common2}}``：``id``、``名称``、``index``、``地位级别``、``描述``、
  ``伤害类型``、``攻击方式``、``行动方式``、``种类``、``能力``、``登场活动``；
  源石虫没有 ``能力`` 键。
- 每级一个 ``{{敌人信息/levelcontent}}``：``index`` + 一整套数值键（``最大生命值``、
  ``攻击力``、``防御力``、``法术抗性``、``移动速度``、``攻击速度``、``攻击间隔``、
  ``重量等级``、``损伤抵抗``、``元素抗性``、``基础嘲讽等级``、十二种 ``XX抗性``）、
  可选的 ``技能N`` / ``技能N效果`` / ``技能N初始`` / ``技能N消耗`` 与 ``天赋``。
- **级别 1 以上只写与级别 0 不同的字段**（源石虫只写生命值与攻击力；爱国者只写
  ``技能2效果``），所以输出前要先用级别 0 补齐。实测两个夹具都只有级别 0 与级别 1
  两级（规格 §6.8 的「三级数值」按「多级数值」理解）。

实现裁决（规格没写死、由本模块定）：

1. ``EnemyLevel.fields`` 是 ``levelcontent`` 的原始键值（``index`` 除外，**空值保留**、
   **不做任何清洗**）；领域层映射用的就是这些中文键名。
2. ``EnemySkill`` 的字段是 ``slot`` / ``name`` / ``effect`` / ``sp_initial`` / ``sp_cost``：
   槽位按数字升序（``技能10`` 排在 ``技能2`` 之后），数字字段保持**原文字符串**
   （页面上是 ``15`` / ``9999`` 这种整数，但也可能是别的写法，清洗交给领域层）。
   ``技能N`` 是槽位名（实测 ``投枪<br>[毁灭姿态]``），``sp_initial`` 对应 ``技能N初始``、
   ``sp_cost`` 对应 ``技能N消耗``。技能字段只做**轻度**归一：``<br>`` → 换行，正文仍是原文
   （``{{变动数值|5%|-}}`` 保留），与 ``fields`` 一样不清洗。
3. 只要某个槽有 ``技能N`` / ``技能N效果`` / ``技能N初始`` / ``技能N消耗`` 任一个非空键就
   算一条技能；缺的字段留 ``None``。所以爱国者级别 1 会产出一条只有 ``effect`` 的槽 2，
   名字等由 :func:`merge_levels` 从级别 0 补。
4. ``EnemyLevel.talent`` 是**清洗后**的 ``天赋``（链接、``{{color}}``、``{{异常效果}}`` 等
   展示模板展开，``<br>`` 保留成换行）；技能与 ``fields`` 一样保留原文，便于领域层自己决定
   怎么洗。注意 ``{{异常效果|眩晕|免疫}}`` 按展示规则只取最后一个位置参数，清洗后会变成
   ``免疫``，异常名丢失——这是 ``strip_markup`` 的既有口径（规格 §5.5 裁决 4）。
5. :func:`merge_levels` 返回每级一个 ``dict[str, Any]``：级别级的键是 ``fields`` 的**原始中文键**
   加 ``index``（int）与 ``天赋``（str 或 ``None``），另有 ``技能``：``list[dict]``，技能 dict 的键
   与 :class:`EnemySkill` 的字段同名（``slot`` / ``name`` / ``effect`` / ``sp_initial`` /
   ``sp_cost``，缺的字段是 ``None``），方便领域层直接映射成英文输出。级别 1 以上先用
   **上一级合并后的结果**补齐（``levelcontent`` 本身只写差异），再被本级覆盖；缺失的 ``天赋``
   同样沿用级别 0。技能按槽位合并：本级覆盖的字段生效，其余字段取级别 0 的同槽技能。计划文档里
   写的 ``merge_levels(page) -> tuple[EnemyLevel, ...]`` 是早期草稿，以本口径为准（见 Task 3 报告）。
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Any, Mapping

from maa_api.services.prts_parse.activity import (
    clean_text,
    template_param,
)
from maa_api.services.prts_parse.wikitext import (
    Template,
    find_templates,
)

__all__ = [
    "COMMON_TEMPLATE",
    "LEVEL_TEMPLATE",
    "EnemyLevel",
    "EnemyPage",
    "EnemySkill",
    "merge_levels",
    "parse_enemy_page",
]

#: 基础信息模板（实测）。
COMMON_TEMPLATE = "敌人信息/common2"

#: 每级数值模板（实测）。
LEVEL_TEMPLATE = "敌人信息/levelcontent"

#: ``技能0`` / ``技能0效果`` / ``技能0初始`` / ``技能0消耗`` 的键。
_SKILL_KEY = re.compile(r"^技能(?P<slot>\d+)(?P<field>.*)$")

#: 技能字段里的换行标记（只归一这一种标记，见 :func:`_skill_text`）。
_BR_RE = re.compile(r"<br\s*/?>", re.I)

#: 槽位字段 → :class:`EnemySkill` 属性；空后缀是技能名。
_SKILL_FIELDS = {
    "": "name",
    "效果": "effect",
    "初始": "sp_initial",
    "消耗": "sp_cost",
}

#: ``merge_levels`` 输出里技能 dict 的键：与 :class:`EnemySkill` 的字段同名（英文），
#: 供领域层直接映射；级别级的中文键（``最大生命值`` 等）来自 ``fields`` 原文。
_SKILL_DICT_KEYS = ("slot", "name", "effect", "sp_initial", "sp_cost")


@dataclass(frozen=True, slots=True)
class EnemySkill:
    """一级里的一条技能：``slot`` 是 ``技能N`` 的 ``N``（``技能0`` → 0）。"""

    slot: int
    name: str | None = None
    effect: str | None = None
    sp_initial: str | None = None
    sp_cost: str | None = None


@dataclass(frozen=True, slots=True)
class EnemyLevel:
    """一级的原始数据：``fields`` 是 ``levelcontent`` 的原始键值（不含 ``index``、未清洗）。"""

    index: int
    fields: Mapping[str, str]
    skills: tuple[EnemySkill, ...]
    talent: str | None = None


@dataclass(frozen=True, slots=True)
class EnemyPage:
    """敌人页解析结果：基础字段 + 按 ``index`` 升序的各级数据。"""

    title: str
    enemy_id: str | None = None
    name: str | None = None
    index: str | None = None
    rank: str | None = None
    category: str | None = None
    damage_type: str | None = None
    attack_type: str | None = None
    move_type: str | None = None
    description: str | None = None
    abilities: str | None = None
    debut: str | None = None
    levels: tuple[EnemyLevel, ...] = ()


def parse_enemy_page(content: str, *, title: str) -> EnemyPage:
    """解析敌人页：``{{敌人信息/common2}}`` 的基础字段 + 每个 ``levelcontent`` 一级。

    页面缺模板、缺字段、只有导航模板时一律返回带 ``None`` / 空元组的对象，不抛异常。
    """
    common = _first_template(content, COMMON_TEMPLATE)
    return EnemyPage(
        title=str(title),
        enemy_id=_first_text(common, "id"),
        name=_first_text(common, "名称"),
        index=_first_text(common, "index"),
        rank=_first_text(common, "地位级别"),
        category=_first_text(common, "种类"),
        damage_type=_first_text(common, "伤害类型"),
        attack_type=_first_text(common, "攻击方式"),
        move_type=_first_text(common, "行动方式"),
        description=clean_text(template_param(common, "描述")),
        abilities=clean_text(template_param(common, "能力")),
        debut=_first_text(common, "登场活动"),
        levels=_parse_levels(content),
    )


def merge_levels(page: EnemyPage) -> tuple[dict[str, Any], ...]:
    """把各等级的差异合并成完整数值：返回每级一个 dict（级别 1 以上先用级别 0 补齐）。

    输出形如 ``{"index": 1, "最大生命值": "2050", ..., "技能": [...], "天赋": "..."}``：
    原始中文键名保留给领域层映射，``index`` 是 int，``技能`` 是 ``list[dict]``。
    """
    merged: list[dict[str, Any]] = []
    carried: dict[str, str] = {}
    carried_skills: dict[int, EnemySkill] = {}
    carried_talent: str | None = None

    for level in page.levels:
        current = dict(carried)
        current.update(level.fields)

        skills: dict[int, EnemySkill] = {}
        for slot, skill in carried_skills.items():
            skills[slot] = skill
        for skill in level.skills:
            base = skills.get(skill.slot)
            if base is None:
                skills[skill.slot] = skill
                continue
            skills[skill.slot] = EnemySkill(
                slot=skill.slot,
                name=skill.name if skill.name is not None else base.name,
                effect=skill.effect if skill.effect is not None else base.effect,
                sp_initial=skill.sp_initial if skill.sp_initial is not None else base.sp_initial,
                sp_cost=skill.sp_cost if skill.sp_cost is not None else base.sp_cost,
            )

        talent = level.talent if level.talent is not None else carried_talent
        row: dict[str, Any] = dict(current)
        row["index"] = level.index
        row["技能"] = [_skill_dict(skills[slot]) for slot in sorted(skills)]
        row["天赋"] = talent
        merged.append(row)

        carried = current
        carried_skills = skills
        carried_talent = talent

    return tuple(merged)


# -- 内部 -----------------------------------------------------------------------


def _parse_levels(content: str) -> tuple[EnemyLevel, ...]:
    """取出全部 ``levelcontent``；``index`` 缺失或非数字时按出现顺序补，最后按 index 升序。

    排序是稳定排序：``index`` 重复时保持页面出现顺序。
    """
    levels: list[EnemyLevel] = []
    for position, template in enumerate(find_templates(content, LEVEL_TEMPLATE, include_nested=False)):
        fields = _params(template)
        index = _to_int(_first_text(template, "index"))
        fields.pop("index", None)
        levels.append(
            EnemyLevel(
                index=index if index is not None else position,
                fields=fields,
                skills=_parse_skills(template),
                talent=clean_text(template_param(template, "天赋")),
            )
        )
    levels.sort(key=lambda level: level.index)
    return tuple(levels)


def _parse_skills(template: Template) -> tuple[EnemySkill, ...]:
    """把 ``技能N*`` 键按槽位聚成 :class:`EnemySkill`，按槽位数字升序。"""
    slots: dict[int, dict[str, str]] = {}
    for arg in template.args:
        if not arg.key:
            continue
        match = _SKILL_KEY.match(arg.key)
        if match is None:
            continue
        field = _SKILL_FIELDS.get(match.group("field"))
        if field is None:
            continue
        slot = int(match.group("slot"))
        value = _skill_text(arg.value)
        if value:
            slots.setdefault(slot, {})[field] = value

    skills = []
    for slot in sorted(slots):
        values = slots[slot]
        skills.append(
            EnemySkill(
                slot=slot,
                name=values.get("name"),
                effect=values.get("effect"),
                sp_initial=values.get("sp_initial"),
                sp_cost=values.get("sp_cost"),
            )
        )
    return tuple(skills)


def _skill_text(value: str | None) -> str | None:
    """技能字段的**轻度**归一：只把 ``<br>`` 换成换行，其余 wikitext 原样保留。

    技能名与技能效果里有实测的 ``<br>``（``投枪<br>[毁灭姿态]``），它对下游是纯噪声；
    ``{{变动数值|5%|-}}`` / ``{{color}}`` 这类模板与 ``※``、``【】`` 全部保留。
    """
    if value is None:
        return None
    text = _BR_RE.sub("\n", value).strip()
    return text or None


def _skill_dict(skill: EnemySkill) -> dict[str, Any]:
    """一只技能 → 领域层用的 dict：键是 :class:`EnemySkill` 的字段名，缺的字段是 ``None``。"""
    return {key: getattr(skill, key) for key in _SKILL_DICT_KEYS}


def _params(template: Template) -> dict[str, str]:
    """模板的命名参数表（同名键保留**第一个**，与 :meth:`Template.param` 一致）。"""
    result: dict[str, str] = {}
    for arg in template.args:
        if arg.key and arg.key not in result:
            result[arg.key] = arg.value
    return result


def _first_template(content: str, name: str) -> Template | None:
    templates = find_templates(content, name, include_nested=False)
    return templates[0] if templates else None


def _first_text(template: Template | None, key: str) -> str | None:
    raw = template_param(template, key)
    if raw is None:
        return None
    return raw.strip() or None


def _to_int(value: str | None) -> int | None:
    if value is None:
        return None
    try:
        return int(value.strip())
    except (TypeError, ValueError):
        return None
