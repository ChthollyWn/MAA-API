"""森空岛 ``player/info`` 的 ``chars[]`` → 账号干员练度更新（M42 规格 §2.3、§4.5，纯函数）。

这一层刻意不碰数据库、不碰网络：它的输入是上游响应里的一段 JSON，输出是一串
:class:`OperatorTrainingUpdate`，因此可以拿真机抓到的样例离线反复验证（W0 的取证报告
就是第一份输入）。

三条口径：

- **以本机目录为准**：``name`` / ``rarity`` / ``profession`` 取本机 ``battle_data.json``
  的 :class:`~maa_api.services.account.capture.OperatorMeta`（与识别、页面筛选同一份
  事实）；本机没有这名干员时才退到接口自带的 ``charInfoMap``。映射不到就留 ``None``，
  不猜。**注意口径差**：本机 ``rarity`` 是 1 基（阿米娅 5），接口 ``charInfoMap.rarity``
  是 0 基（阿米娅 4），兜底路径 +1 之后才落库（真机实测）。
- **越界值一律丢**（不夹取、不四舍五入）：``level`` 0–90、``evolvePhase`` 0–2、
  ``potential`` 1–6、``mainSkillLvl`` 1–7、专精 0–3、模组 1–3，越界的那一项留 ``None``
  并计入 ``dropped``——上游改形状时宁可少写一个字段，也不要往库里灌脏值。
- **「不知道」不许写成「没有」**：``skills`` / ``equip`` 缺字段或整体换形状时留 ``None``；
  非空 ``equip`` 里一个模组类型都认不出来时也留 ``None``，只有**空列表**才是「确认没有」。
- **``potentialRank`` 是 0 基**：本项目 ``account_operator.potential`` 是「潜能数」
  （真机库里 384 名已拥有干员中 184 名是 6 ＝ 满潜），所以 ``potential = potentialRank + 1``。
  这条换算由 W0 拿同账号的干员识别读数交叉验证（该通道 M47 已下线，仅作历史；规格 §2.5-1）。
"""

from __future__ import annotations

import logging
import re
from collections.abc import Iterable, Mapping, Sequence
from dataclasses import dataclass
from typing import Any

from maa_api.services.account.rules import (
    MAX_LEVEL,
    MAX_MASTERY_LEVEL,
    MAX_MODULE_LEVEL,
    MAX_POTENTIAL,
    MAX_SKILL_LEVEL,
    MIN_MODULE_LEVEL,
    MODULE_TYPES,
)

logger = logging.getLogger(__name__)

__all__ = [
    "MAX_FAVOR_PERCENT",
    "MODULE_INFO_TYPE_KEYS",
    "POTENTIAL_RANK_OFFSET",
    "OperatorTrainingUpdate",
    "module_type_of",
    "to_operator_updates",
]

#: ``potentialRank``（0 基）到 ``potential``（1 基）的偏移；W0 交叉验证后定稿。
POTENTIAL_RANK_OFFSET = 1
#: 信赖百分比上限（游戏里 0–200）。
MAX_FAVOR_PERCENT = 200
#: ``equipmentInfoMap`` 里承载模组类型的键名。**真机实测（2026-10-12）就是 ``typeIcon``**，
#: 值形如 ``ccr-y``（分支码小写 + ``-`` + 类型字母小写），证章是 ``original``；同一 id 的
#: 值与拆包 ``uniequip_table.json`` **逐字相同**（规格 §2.3）。其余键名只作为上游换写法的兜底。
MODULE_INFO_TYPE_KEYS: tuple[str, ...] = ("type", "moduleType", "equipType", "typeName")
#: ``typeIcon`` 的形状：1–4 位分支码 + ``-`` + 一个类型字母（大小写都可能出现，如 ``dec-X``）。
_MODULE_TYPE_ICON_RE = re.compile(r"^([a-z0-9]{1,4})-([a-z])$", re.IGNORECASE)
#: 基础模组（证章）的 ``typeIcon``：没有分支码也没有类型字母，不进 ``modules``。
MODULE_ORIGINAL_ICON = "original"
#: 模组类型的别名前缀：上游可能给 ``MOD_X`` / ``UNIEQUIP_X`` 这类写法。
_MODULE_TYPE_PREFIXES: tuple[str, ...] = ("mod_", "uniequip_", "equip_")


@dataclass(frozen=True, slots=True)
class OperatorTrainingUpdate:
    """一名干员的练度读数（``None`` ＝ 这一项上游没给或给了越界值）。"""

    char_id: str
    own: bool = True
    name: str | None = None
    rarity: int | None = None
    profession: str | None = None
    elite: int | None = None
    level: int | None = None
    potential: int | None = None
    skill_level: int | None = None
    masteries: dict[str, int] | None = None
    modules: dict[str, int] | None = None
    favor_percent: int | None = None
    #: 按槽位的技能 id（M43）：技能图标的 URL 要用它，内核识别与手动编辑都不给这一列
    #: （两者 M47 已下线）。
    skill_ids: list[str] | None = None
    #: 模组明细（M43）：``[{type, level, equip_id, branch, label, type_icon}]``，
    #: 供页面显示 ``CCR-X`` 这类标签；``modules`` 仍是计划层用的 ``{"Y": 3}``。
    module_details: list[dict[str, Any]] | None = None


def to_operator_updates(
    chars: Sequence[Any],
    *,
    catalog: Mapping[str, Any] | None = None,
    char_info_map: Mapping[str, Any] | None = None,
    equipment_info_map: Mapping[str, Any] | None = None,
) -> tuple[list[OperatorTrainingUpdate], int]:
    """把 ``chars[]`` 转成更新列表；返回 ``(updates, dropped)``。

    ``dropped`` 是**丢弃的字段项数**（不是人数）：它进同步摘要的 ``dropped_invalid``，
    让「上游改了形状」这件事在页面与日志里可见，而不是静默少写几列。
    """
    catalog = catalog or {}
    char_info_map = char_info_map or {}
    equipment_info_map = equipment_info_map or {}

    updates: list[OperatorTrainingUpdate] = []
    dropped = 0
    for raw in chars:
        if not isinstance(raw, Mapping):
            dropped += 1
            continue
        char_id = _text(raw.get("charId"))
        if char_id is None:
            dropped += 1
            continue

        meta = catalog.get(char_id)
        info = char_info_map.get(char_id)
        info = info if isinstance(info, Mapping) else {}

        rarity = _int_in_range(
            _first(meta, "rarity", None) if meta is not None else None,
            0,
            6,
        )
        if rarity is None:
            # 兜底走接口自带的 ``charInfoMap.rarity``，但它的口径与本机不同：**0 基**
            # （真机实测 2026-10-12：阿米娅在这张表里是 4，而本机 ``battle_data.json``
            # 是 5，本机库里存的也是 5）。直接落库会整体小 1，所以这里 +1 再按 1–6 校验。
            raw_rarity = _int_in_range(_pick(info, ("rarity",)), 0, 5)
            rarity = raw_rarity + 1 if raw_rarity is not None else None

        elite, drop_elite = _checked(_int_in_range(raw.get("evolvePhase"), 0, 2))
        level, drop_level = _checked(_int_in_range(raw.get("level"), 0, MAX_LEVEL))
        skill_level, drop_skill = _checked(
            _int_in_range(raw.get("mainSkillLvl"), 1, MAX_SKILL_LEVEL)
        )
        potential, drop_potential = _checked(_potential(raw.get("potentialRank")))
        favor, drop_favor = _checked(_int_in_range(raw.get("favorPercent"), 0, MAX_FAVOR_PERCENT))
        masteries, drop_mastery = _masteries(raw.get("skills"))
        skill_ids, drop_skill_ids = _skill_ids(raw.get("skills"))
        modules, module_details, drop_module = _modules(
            raw.get("equip"), equipment_info_map
        )
        dropped += sum(
            (
                drop_elite,
                drop_level,
                drop_skill,
                drop_potential,
                drop_favor,
                drop_mastery,
                drop_skill_ids,
                drop_module,
            )
        )

        updates.append(
            OperatorTrainingUpdate(
                char_id=char_id,
                own=True,
                name=_text(_first(meta, "name", None) if meta is not None else None)
                or _text(_pick(info, ("name",))),
                rarity=rarity,
                profession=_text(_first(meta, "profession", None) if meta is not None else None)
                or _text(_pick(info, ("profession", "professionName"))),
                elite=elite,
                level=level,
                potential=potential,
                skill_level=skill_level,
                masteries=masteries,
                modules=modules,
                favor_percent=favor,
                skill_ids=skill_ids,
                module_details=module_details,
            )
        )
    return updates, dropped


def module_detail_of(
    equip_id: str, equipment_info_map: Mapping[str, Any]
) -> dict[str, Any] | None:
    """模组 id → ``{"type", "branch", "label", "type_icon"}``；认不出返回 ``None``（不猜）。

    主路径是 ``typeIcon``（真机实测的字段）：``ccr-y`` ⇒ 类型 ``Y``、分支 ``ccr``、
    标签 ``CCR-Y``。``original``（证章）返回 ``{"type": None, ...}``——它没有类型字母、
    不属于 ``modules`` 的取值域，但是**正常状态**（调用方不该计 dropped），所以用
    ``type=None`` 与「认不出来的 ``None``」区分开。类型字母不在 ``MODULE_TYPES`` 里（如 ``so-b`` 的 ``B``）也返回
    ``None``：宁可丢掉这一条并计数，也不要往库里写一个前端与计划层都不认识的类型。
    """
    entry = equipment_info_map.get(equip_id)
    if not isinstance(entry, Mapping):
        return None
    icon = _text(entry.get("typeIcon"))
    if icon is not None:
        if icon.lower() == MODULE_ORIGINAL_ICON:
            # 证章（基础模组）：是**正常状态**，不是「认不出来」——用 ``type=None`` 的明细
            # 把它与「返回 None」区分开，调用方据此不计 dropped。
            return {"type": None, "branch": None, "label": None, "type_icon": icon}
        match = _MODULE_TYPE_ICON_RE.match(icon)
        if match is not None:
            branch = match.group(1).lower()
            letter = match.group(2).upper()
            if letter in MODULE_TYPES:
                return {
                    "type": letter,
                    "branch": branch,
                    "label": f"{branch.upper()}-{letter}",
                    "type_icon": icon,
                }
            logger.debug("模组 %s 的类型字母 %s 不在 %s 内，跳过", equip_id, letter, MODULE_TYPES)
            return None
    # 兜底：万一上游改回 ``type`` / ``moduleType`` 这类写法（``MOD_X``）。
    for key in MODULE_INFO_TYPE_KEYS:
        normalized = _normalize_module_type(entry.get(key))
        if normalized is not None:
            return {
                "type": normalized,
                "branch": None,
                "label": normalized,
                "type_icon": None,
            }
    return None


def module_type_of(equip_id: str, equipment_info_map: Mapping[str, Any]) -> str | None:
    """模组 id → 类型 ``X`` / ``Y`` / ``D`` / ``A``；认不出返回 ``None``（不猜）。"""
    detail = module_detail_of(equip_id, equipment_info_map)
    return str(detail["type"]) if detail is not None else None


# --------------------------------------------------------------------------- #
# 内部
# --------------------------------------------------------------------------- #
def _first(obj: Any, name: str, default: Any) -> Any:
    """从 ``OperatorMeta`` 这类对象上取属性；取不到用默认值。"""
    return getattr(obj, name, default)


def _pick(mapping: Mapping[str, Any], keys: Iterable[str]) -> Any:
    for key in keys:
        if key in mapping:
            return mapping[key]
    return None


def _text(value: Any) -> str | None:
    if not isinstance(value, str):
        return None
    text = value.strip()
    return text or None


def _int_in_range(value: Any, minimum: int, maximum: int) -> int | None:
    if isinstance(value, bool) or not isinstance(value, int):
        return None
    if value < minimum or value > maximum:
        return None
    return int(value)


def _checked(value: int | None) -> tuple[int | None, int]:
    """把 ``None`` 记成一次丢弃，其余原样返回。"""
    return (value, 0) if value is not None else (None, 1)


def _potential(raw: Any) -> int | None:
    """``potentialRank``（0 基）→ ``potential``（1 基）；越界返回 ``None``。"""
    rank = _int_in_range(raw, 0, MAX_POTENTIAL - 1)
    if rank is None:
        return None
    value = rank + POTENTIAL_RANK_OFFSET
    return value if 1 <= value <= MAX_POTENTIAL else None


def _masteries(raw: Any) -> tuple[dict[str, int] | None, int]:
    """``skills[]`` → ``{"1": 0, "2": 0, "3": 0}`` 形状；认不出形状返回 ``None``。

    ``skills`` 不是列表（上游缺字段）时返回 ``None`` ＝ 不知道；是列表时**空列表也算
    确认没有**（与接口对精二以下干员的表达一致，规格 §2.3）。

    **槽位数取 ``len(skills)`` 而不是按稀有度查表**：响应里就是这名干员真的有的技能，
    比本机目录可靠——W0 尚未确认 ``charInfoMap`` 的字段名，若稀有度读不出来，按稀有度
    截槽会把 6★ 的第 2、3 个技能专精写成 0（＝「确认没有专精」），那是拿默认值冒充事实。
    超过 3 个的条目忽略并计数（技能序号只到 3）。
    """
    if not isinstance(raw, list):
        return None, 0
    result = {"1": 0, "2": 0, "3": 0}
    dropped = 0
    for index, entry in enumerate(raw):
        if index >= 3:
            dropped += 1
            continue
        if not isinstance(entry, Mapping):
            dropped += 1
            continue
        level = _int_in_range(entry.get("specializeLevel"), 0, MAX_MASTERY_LEVEL)
        if level is None:
            dropped += 1
            continue
        result[str(index + 1)] = level
    return result, dropped


def _skill_ids(raw: Any) -> tuple[list[str] | None, int]:
    """``skills[]`` → 按槽位的技能 id 列表；认不出形状返回 ``None``（不知道）。

    技能图标的 URL 就是 ``char_skill/{skill_id}.png``（规格 §2.2），所以顺序必须与
    ``masteries`` 的槽位一致：都是 ``skills[]`` 的数组顺序，超过 3 个的条目忽略并计数。
    """
    if not isinstance(raw, list):
        return None, 0
    ids: list[str] = []
    dropped = 0
    for index, entry in enumerate(raw):
        if index >= 3:
            dropped += 1
            continue
        if not isinstance(entry, Mapping):
            dropped += 1
            continue
        skill_id = _text(entry.get("id"))
        if skill_id is None:
            dropped += 1
            continue
        ids.append(skill_id)
    return ids, dropped


def _modules(
    raw: Any, equipment_info_map: Mapping[str, Any]
) -> tuple[dict[str, int] | None, list[dict[str, Any]] | None, int]:
    """``equip[]`` → ``({"X": 2}, [明细], dropped)``；认不出形状两处都返回 ``None``。

    - ``locked=true`` 与等级越界（<1 或 >3）都当「没装」：不计入 ``dropped``（那是正常的
      未解锁状态），也不进 ``modules`` / 明细；
    - **类型映射不出来才计一次** ``dropped``（说明 ``equipmentInfoMap`` 变了）；
    - 空列表是**正面读数**：``({}, [], 0)``＝这名干员确实没有已解锁的模组；
    - 非空列表却一个可解析项都没有 ⇒ ``(None, None, dropped)`` ＝ 不知道，而不是「
      确认没装」——把不知道写成「没有」是这套归一里最不能犯的错（M42 的护栏）。
    """
    if not isinstance(raw, list):
        return None, None, 0
    if not raw:
        return {}, [], 0
    result: dict[str, int] = {}
    details: list[dict[str, Any]] = []
    dropped = 0
    for entry in raw:
        if not isinstance(entry, Mapping):
            dropped += 1
            continue
        if entry.get("locked") is True:
            continue
        equip_id = _text(entry.get("id"))
        if equip_id is None:
            dropped += 1
            continue
        detail = module_detail_of(equip_id, equipment_info_map)
        if detail is None:
            dropped += 1
            continue
        if detail["type"] is None:
            # 证章（``original``）：没有类型字母，不进 ``modules``，也不算异常。
            continue
        level = _int_in_range(entry.get("level"), MIN_MODULE_LEVEL, MAX_MODULE_LEVEL)
        if level is None:
            continue
        module_type = str(detail["type"])
        result[module_type] = level
        details.append(
            {
                "type": module_type,
                "level": level,
                "equip_id": equip_id,
                "branch": detail["branch"],
                "label": detail["label"],
                "type_icon": detail["type_icon"],
            }
        )
    if not result:
        return None, None, dropped
    return result, details, dropped


def _normalize_module_type(value: Any) -> str | None:
    text = _text(value)
    if text is None:
        return None
    candidate = text.upper()
    for prefix in _MODULE_TYPE_PREFIXES:
        if candidate.startswith(prefix.upper()):
            candidate = candidate[len(prefix) :]
            break
    candidate = candidate.strip("_ ")
    if candidate in MODULE_TYPES:
        return candidate
    return None
