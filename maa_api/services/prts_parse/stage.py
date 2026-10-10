"""关卡页解析（规格 §5.7）：`{{普通关卡信息}}` / `{{突袭关卡信息}}` / `{{敌方情报}}` / `{{关卡装置}}`。

字段白名单照 `get_stage` 的返回定；`{{关卡材料掉落}}` 这种前端组件模板在这里不展开
（掉落率在 M26 接企鹅物流）。

纯函数：wikitext 进，结构出，不联网、不读盘。
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import Any

from maa_api.services.prts_parse.activity import (
    clean_text,
    template_param,
)
from maa_api.services.prts_parse.wikitext import (
    Template,
    find_templates,
    strip_markup,
)

__all__ = [
    "CHALLENGE_TEMPLATE",
    "DEVICE_TEMPLATE",
    "ENEMY_TEMPLATE",
    "NORMAL_TEMPLATE",
    "StageDevice",
    "StageEnemy",
    "StagePage",
    "parse_stage_page",
]

NORMAL_TEMPLATE = "普通关卡信息"
CHALLENGE_TEMPLATE = "突袭关卡信息"
ENEMY_TEMPLATE = "敌方情报"
DEVICE_TEMPLATE = "关卡装置"
PAREN_TEMPLATE = "悖论模拟关卡信息"

#: `敌人1` / `敌人1数量` / `敌人1级别` / `敌人1生命值` 的键。
_ENEMY_KEY = re.compile(r"^敌人(?P<index>\d+)(?P<field>.*)$")

#: 掉落字段：``至纯源石:三星获得,阿米娅:首次掉落``。
_DROP_SPLIT = re.compile(r"[,，]")


@dataclass(frozen=True, slots=True)
class StageEnemy:
    """`{{敌方情报}}` 的一条。"""

    name: str
    count: int | None = None
    level: int | None = None
    rank: str | None = None
    hp: str | None = None


@dataclass(frozen=True, slots=True)
class StageDevice:
    """`{{关卡装置}}` 的一条。"""

    name: str
    deployable: int | None = None
    skill: str | None = None
    note: str | None = None


@dataclass(slots=True)
class StagePage:
    """关卡页解析结果；`unavailable` 列出页面上没有的字段组。"""

    title: str
    code: str | None = None
    name: str | None = None
    stage_id: str | None = None
    stage_type: str | None = None
    subtype: str | None = None
    difficulty: str | None = None
    unlock: str | None = None
    recommend_level: str | None = None
    deploy_limit: int | None = None
    initial_cost: int | None = None
    max_cost: int | None = None
    life_points: int | None = None
    enemy_count: int | None = None
    map_size: str | None = None
    sanity: int | None = None
    practice_sanity: int | None = None
    area: str | None = None
    shortest_time: str | None = None
    description: str | None = None
    first_drops: tuple[str, ...] = ()
    normal_drops: tuple[str, ...] = ()
    challenge_condition: str | None = None
    challenge_practice_sanity: int | None = None
    enemies: tuple[StageEnemy, ...] = ()
    devices: tuple[StageDevice, ...] = ()
    has_challenge: bool = False
    extra: dict[str, Any] = field(default_factory=dict)


def parse_stage_page(content: str, *, title: str) -> StagePage:
    """解析一个关卡页；普通难度优先，突袭信息单独给。"""
    normal = _first(content, NORMAL_TEMPLATE) or _first(content, PAREN_TEMPLATE)
    challenge = _first(content, CHALLENGE_TEMPLATE)
    page = StagePage(
        title=title,
        code=clean_text(template_param(normal, "关卡代号")),
        name=clean_text(template_param(normal, "关卡名")),
        stage_id=clean_text(template_param(normal, "关卡id")),
        stage_type=clean_text(template_param(normal, "关卡类型")),
        subtype=clean_text(template_param(normal, "子类型")),
        difficulty=clean_text(template_param(normal, "关卡难度")),
        unlock=clean_text(template_param(normal, "解锁条件")),
        recommend_level=clean_text(template_param(normal, "推荐等级")),
        deploy_limit=_int(template_param(normal, "部署上限")),
        initial_cost=_int(template_param(normal, "初始COST")),
        max_cost=_int(template_param(normal, "COST上限")),
        life_points=_int(template_param(normal, "目标点耐久")),
        enemy_count=_int(template_param(normal, "敌人数量")),
        map_size=clean_text(template_param(normal, "地图大小")),
        sanity=_int(template_param(normal, "作战消耗")),
        practice_sanity=_int(template_param(normal, "演习消耗")),
        area=clean_text(template_param(normal, "所属区域")),
        shortest_time=clean_text(template_param(normal, "最短用时")),
        description=clean_text(template_param(normal, "关卡描述")),
        first_drops=_drops(template_param(normal, "首次掉落")),
        normal_drops=_drops(template_param(normal, "常规掉落")),
        enemies=_enemies(content),
        devices=_devices(content),
        has_challenge=challenge is not None,
    )
    if challenge is not None:
        page.challenge_condition = clean_text(template_param(challenge, "关卡描述"))
        page.challenge_practice_sanity = _int(template_param(challenge, "演习消耗"))
        if page.recommend_level is None:
            page.recommend_level = clean_text(template_param(challenge, "推荐等级"))
    return page


def _first(content: str, name: str) -> Template | None:
    templates = find_templates(content, name)
    return templates[0] if templates else None


def _int(value: str | None) -> int | None:
    """模板里的数值：``9``、``精英2 LV.30``（后者不是数）都走这里。"""
    if value is None:
        return None
    cleaned = strip_markup(value).strip()
    match = re.fullmatch(r"-?\d+", cleaned)
    return int(match.group()) if match else None


def _drops(value: str | None) -> tuple[str, ...]:
    """掉落字段：逗号分隔的 ``物品:描述``；空项丢掉。"""
    if not value:
        return ()
    cleaned = strip_markup(value)
    items = []
    for part in _DROP_SPLIT.split(cleaned):
        item = part.strip()
        if item:
            items.append(item)
    return tuple(items)


def _enemies(content: str) -> tuple[StageEnemy, ...]:
    """`{{敌方情报}}`：按 ``敌人N`` 的 N 分组、按 N 升序。"""
    template = _first(content, ENEMY_TEMPLATE)
    if template is None:
        return ()
    grouped: dict[int, dict[str, str]] = {}
    for arg in getattr(template, "args", ()):
        key = getattr(arg, "key", None)
        value = getattr(arg, "value", None)
        if not key or value is None:
            continue
        match = _ENEMY_KEY.match(key)
        if match is None:
            continue
        index = int(match.group("index"))
        grouped.setdefault(index, {})[match.group("field")] = value

    enemies: list[StageEnemy] = []
    for index in sorted(grouped):
        fields = grouped[index]
        name = clean_text(fields.get(""))
        if not name:
            continue
        enemies.append(
            StageEnemy(
                name=name,
                count=_int(fields.get("数量")),
                level=_int(fields.get("级别")),
                rank=clean_text(fields.get("地位级别")),
                hp=clean_text(fields.get("生命值")),
            )
        )
    return tuple(enemies)


def _devices(content: str) -> tuple[StageDevice, ...]:
    """`{{关卡装置}}`：页面里可能出现多次，每次都算一条。"""
    devices: list[StageDevice] = []
    for template in find_templates(content, DEVICE_TEMPLATE):
        name = clean_text(template_param(template, "装置名称"))
        if not name:
            continue
        devices.append(
            StageDevice(
                name=name,
                deployable=_int(template_param(template, "装置可部署数量")),
                skill=clean_text(template_param(template, "装置技能")),
                note=clean_text(template_param(template, "备注")),
            )
        )
    return tuple(devices)


def raw_params(content: str, template_name: str) -> dict[str, str | None]:
    """某个模板的命名参数原文（调试与后续里程碑的字段扩展用）。"""
    template = _first(content, template_name)
    if template is None:
        return {}
    return {
        getattr(arg, "key", None): getattr(arg, "value", None)
        for arg in getattr(template, "args", ())
        if getattr(arg, "key", None)
    }
