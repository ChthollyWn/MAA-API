"""干员编辑约束与 wire 归一（M27 规格 §7.3、§7.5）：纯函数，不碰数据库。

这一层刻意与 ORM 解耦：:mod:`maa_api.services.account.box` 负责事务与合并，
这里只回答「这个值合不合法」「这行怎么渲染」。因此约束可以脱离数据库单独验证
（``tests/services/test_account_box_rules.py``），也方便 Agent 工具与前端契约测试
复用同一组常量。

精英化上限、专精前置、模组类型与等级都来自规格 §7.5 的编辑抽屉；违反时抛
``VALIDATION_ERROR``（422 ``details.field`` / ``reason`` / ``allowed``），口径与请求
模型里的 Literal 校验一致（docs/05 §4.17）。
"""

from __future__ import annotations

from collections.abc import Mapping
from datetime import UTC, datetime
from typing import Any

from maa_api.domain.errors import AppError, ErrorCode

__all__ = [
    "BOX_FIELDS",
    "KNOWN_SOURCES",
    "SKLAND_FIELDS",
    "SKLAND_SOURCE",
    "EDITABLE_FIELDS",
    "MAX_LEVEL",
    "MAX_MASTERY_LEVEL",
    "MAX_MODULE_LEVEL",
    "MAX_POTENTIAL",
    "MAX_SKILL_LEVEL",
    "MIN_MODULE_LEVEL",
    "MODULE_TYPES",
    "RECOGNITION_SOURCE",
    "RECOGNIZED_FIELDS",
    "SKILL_COUNT_BY_RARITY",
    "UNKNOWN_TRAINING_FIELDS",
    "WRITE_SOURCES",
    "box_row_wire",
    "diff_wire",
    "elite_cap",
    "iso_z",
    "json_mapping",
    "module_wire",
    "normalize_masteries",
    "normalize_modules",
    "optional_int",
    "require_bool",
    "require_int",
    "skill_count",
    "text_list_wire",
    "validation_error",
]

#: 一条干员行对外给出的全部字段（REST 单条与列表项同形）。
BOX_FIELDS: tuple[str, ...] = (
    "char_id",
    "name",
    "rarity",
    "profession",
    "own",
    "elite",
    "level",
    "potential",
    "skill_level",
    "masteries",
    "modules",
    "favor_percent",
    "skill_ids",
    "module_details",
    "field_sources",
    "recognized_at",
    "updated_at",
)

#: 识别能覆盖的字段（规格 §7.3）：识别结果只动这四个。
RECOGNIZED_FIELDS: tuple[str, ...] = ("own", "elite", "level", "potential")

#: 森空岛账号数据能覆盖的字段（M42 规格 §4.5）：练度六列 + 信赖。
#: **own 也在里面**：接口返回的就是「已拥有」全集，整轮完整时不在集合里的干员置
#: ``own=False``（护栏见同步服务的 ``SKLAND_MIN_CHARS``）。
SKLAND_FIELDS: tuple[str, ...] = (
    "own",
    "elite",
    "level",
    "potential",
    "skill_level",
    "masteries",
    "modules",
    "favor_percent",
    # M43：卡片要用的两列（技能 id 与模组明细）。它们**不可编辑**，进这张表只是为了
    # 跟着同步一起写与记来源；因为没有手动来源，差异逻辑对它们天然不成立。
    "skill_ids",
    "module_details",
)

#: 可为「未识别」的练度列（M35 规格 §4.1）：取值 ``None`` 表示既没有识别值、
#: 也没有 manual / agent 写入。前端、API 文档与规划层共用这一份字段名。
UNKNOWN_TRAINING_FIELDS: tuple[str, ...] = (
    "elite",
    "level",
    "potential",
    "skill_level",
    "masteries",
    "modules",
)

#: ``PATCH`` 允许改的字段（规格 §7.5）。``char_id`` / ``name`` / ``rarity`` /
#: ``profession`` 是识别与资源侧的事实，不可手改。
EDITABLE_FIELDS: tuple[str, ...] = (
    "own",
    "elite",
    "level",
    "potential",
    "skill_level",
    "masteries",
    "modules",
)

#: 字段来源的取值域：识别、手动、Agent、森空岛账号数据（M42 规格 §4.5）。
RECOGNITION_SOURCE = "recognition"
WRITE_SOURCES: tuple[str, ...] = ("manual", "agent")
#: 森空岛同步写入的来源标记。它与 ``recognition`` 的区别是**来源不同而不是可信度不同**：
#: 两者都是「机器读出来的账号事实」，同步覆盖手动值时同样记差异（``account_operator_diff``）。
SKLAND_SOURCE = "skland"
#: 全部已知来源取值（读侧的白名单，见 :func:`field_sources_wire`）。
KNOWN_SOURCES: tuple[str, ...] = (RECOGNITION_SOURCE, *WRITE_SOURCES, SKLAND_SOURCE)

#: 专精等级上限（规格 §7.5：0–3）。
MAX_MASTERY_LEVEL = 3
#: 模组类型固定四个（规格 §7.5），等级 1–3。
MODULE_TYPES: tuple[str, ...] = ("X", "Y", "D", "A")
MAX_MODULE_LEVEL = 3
#: 模组至少是 1 级（0 级＝没装模组，用空映射表示）。
MIN_MODULE_LEVEL = 1

#: 技能等级上限；专精要求它正好满级（规格 §7.5 的「技能 7 级」）。
MAX_SKILL_LEVEL = 7

#: 等级上限：6★ 精二满级 90，比它更高的值在任何干员身上都不存在。
MAX_LEVEL = 90
#: 潜能上限（0 潜到 6 潜）。
MAX_POTENTIAL = 6

#: 专精与模组能写成非空的最低精英化等级。
MASTERY_ELITE_REQUIRED = 2

#: 精英化上限按稀有度（规格 §7.5）：1–2★ 精零、3★ 精一、4–6★ 精二。
_ELITE_CAP_BY_RARITY: dict[int, int] = {1: 0, 2: 0, 3: 1}
_DEFAULT_ELITE_CAP = 2

#: 技能数按稀有度（规格 §7.5）：3★ 1 个、4–5★ 2 个、6★ 3 个；其余按 1 个。
SKILL_COUNT_BY_RARITY: dict[int, int] = {6: 3, 5: 2, 4: 2, 3: 1}
_DEFAULT_SKILL_COUNT = 1


def elite_cap(rarity: int | None) -> int:
    """稀有度对应的精英化上限；未知稀有度按 4–6★ 的宽档。"""
    if rarity is None:
        return _DEFAULT_ELITE_CAP
    return _ELITE_CAP_BY_RARITY.get(int(rarity), _DEFAULT_ELITE_CAP)


def skill_count(rarity: int | None) -> int:
    """稀有度对应的技能数默认值（规格 §7.5）。"""
    if rarity is None:
        return _DEFAULT_SKILL_COUNT
    return SKILL_COUNT_BY_RARITY.get(int(rarity), _DEFAULT_SKILL_COUNT)


def iso_z(value: datetime | None) -> str | None:
    """UTC 时间 → ``...Z``；naive 按 UTC 解释（库内统一 naive UTC，docs/04 §3.2）。"""
    if value is None:
        return None
    if value.tzinfo is None:
        value = value.replace(tzinfo=UTC)
    return value.astimezone(UTC).isoformat().replace("+00:00", "Z")


def validation_error(field: str, reason: str, **allowed: Any) -> AppError:
    """字段级编辑约束不满足：422 ``VALIDATION_ERROR``，``details`` 指出字段与合法值。

    口径与请求模型里的 Literal 校验一致（docs/05 §4.2/§4.17）：账号字段的取值域
    是「参数校验」而不是「请求结构不成立」，所以不用 400 ``INVALID_PARAMETER``。
    """
    details: dict[str, Any] = {"field": field, "reason": reason}
    if allowed:
        details["allowed"] = allowed
    return AppError(ErrorCode.VALIDATION_ERROR, f"字段 {field} 不满足约束：{reason}", details)


def require_int(field: str, value: Any, *, minimum: int, maximum: int) -> int:
    """取整数并夹在闭区间内；布尔不算整数（``True`` 当 1 会让「等级=是」静默通过）。"""
    if isinstance(value, bool) or not isinstance(value, int):
        raise validation_error(field, "必须是整数", minimum=minimum, maximum=maximum)
    if value < minimum or value > maximum:
        raise validation_error(field, "超出取值范围", minimum=minimum, maximum=maximum)
    return int(value)


def require_bool(field: str, value: Any) -> bool:
    """取布尔值；``0`` / ``"true"`` 这类近似值一律拒绝（页面传的是 JSON 布尔）。"""
    if not isinstance(value, bool):
        raise validation_error(field, "必须是布尔值")
    return value


def normalize_masteries(value: Any, *, slots: int) -> dict[str, int]:
    """校验专精映射：键 1–3、值 0–3；超过技能数的槽位归零。

    ``slots`` 是该稀有度的技能数（:func:`skill_count`）：4★ 只有两个技能，写第 3 槽
    不报错也不生效（归零），这样页面不必知道每个干员的技能数。
    """
    if not isinstance(value, Mapping):
        raise validation_error("masteries", "必须是技能序号到等级的映射")
    result = {"1": 0, "2": 0, "3": 0}
    for key, raw in value.items():
        try:
            slot = int(key)
        except (TypeError, ValueError):
            raise validation_error(
                "masteries", "技能序号只能是 1、2、3", keys=[1, 2, 3]
            ) from None
        if slot not in (1, 2, 3):
            raise validation_error("masteries", "技能序号只能是 1、2、3", keys=[1, 2, 3])
        level = require_int(
            f"masteries.{slot}", raw, minimum=0, maximum=MAX_MASTERY_LEVEL
        )
        result[str(slot)] = level if slot <= slots else 0
    return result


def normalize_modules(value: Any) -> dict[str, int]:
    """校验模组映射：类型固定 X/Y/D/A，等级 1–3。"""
    if not isinstance(value, Mapping):
        raise validation_error("modules", "必须是模组类型到等级的映射")
    result: dict[str, int] = {}
    for key, raw in value.items():
        module_type = str(key)
        if module_type not in MODULE_TYPES:
            raise validation_error(
                "modules", "模组类型只能是 X、Y、D、A", types=list(MODULE_TYPES)
            )
        result[module_type] = require_int(
            f"modules.{module_type}",
            raw,
            minimum=MIN_MODULE_LEVEL,
            maximum=MAX_MODULE_LEVEL,
        )
    return result


def json_mapping(value: Any) -> dict[str, Any]:
    """JSON 列归一成 dict；库里的 ``None`` / 非映射一律回空映射（读 JSON 列的唯一入口）。"""
    return dict(value) if isinstance(value, Mapping) else {}


def optional_int(value: Any) -> int | None:
    """练度列的读侧归一：``None`` / 非整数一律回 ``None``（＝未识别），**不兜底成 0**。

    布尔要单独挡掉：``True`` 也是 ``int``，但练度列里它只可能是脏数据。
    """
    if value is None or isinstance(value, bool):
        return None
    try:
        return int(value)
    except (TypeError, ValueError):
        return None


def mastery_wire(value: Any) -> dict[str, int] | None:
    """专精列 → ``{"1": 0..3, "2": ..., "3": ...}``；未知回 ``None``。

    三个槽位在**已知**时总是一起出现（前端不必区分「缺键」与「0 级」）；但只要这一列
    没被识别、也没被人工/Agent 写过（``None``），就原样返回 ``None``——M35 之前这里
    总补全三个 0，等于替「没识别过」编了一份「无专精」的答案。
    """
    if not isinstance(value, Mapping):
        return None
    result = {"1": 0, "2": 0, "3": 0}
    for key, raw in value.items():
        try:
            slot = int(key)
        except (TypeError, ValueError):
            continue
        if slot not in (1, 2, 3) or isinstance(raw, bool) or not isinstance(raw, int):
            continue
        result[str(slot)] = int(raw)
    return result


def module_wire(value: Any) -> dict[str, int] | None:
    """模组列 → ``{"X": 1..3}``；未知回 ``None``（口径同 :func:`mastery_wire`）。

    已装模组是 ``{"X": 2}`` 这种非空映射；``{}`` 与 ``None`` 的区别是「确认没装」与
    「不知道」，读侧不许把后者说成前者。
    """
    if not isinstance(value, Mapping):
        return None
    result: dict[str, int] = {}
    for key, raw in value.items():
        module_type = str(key)
        if module_type not in MODULE_TYPES:
            continue
        if isinstance(raw, bool) or not isinstance(raw, int):
            continue
        result[module_type] = int(raw)
    return result


def field_sources_wire(value: Any) -> dict[str, str]:
    """字段来源列：只留已知字段与已知来源取值。"""
    known = set(BOX_FIELDS)
    result: dict[str, str] = {}
    for key, raw in json_mapping(value).items():
        field = str(key)
        source = str(raw)
        if field in known and source in KNOWN_SOURCES:
            result[field] = source
    return result


def box_row_wire(row: Any) -> dict[str, Any]:
    """干员表行 → 对外形状（规格 §7.5 的 wire、M35 规格 §4.3）。

    练度六列**原样透传**：有值给值，没识别也没人写过的给 ``None``（＝未识别）。
    M35 之前这里逐列兜底（``elite=0``、技能 1 级、专精三槽全 0、模组空映射），
    于是「不知道」在页面与会话里被渲染成「精零 1 级、无专精、无模组」。
    ``own`` 仍是布尔：``all_opers`` 每次都给出它，没有未知态。
    """
    return {
        "char_id": str(getattr(row, "char_id", "")),
        "name": getattr(row, "name", None),
        "rarity": getattr(row, "rarity", None),
        "profession": getattr(row, "profession", None),
        "own": bool(getattr(row, "own", False)),
        "elite": optional_int(getattr(row, "elite", None)),
        "level": optional_int(getattr(row, "level", None)),
        "potential": optional_int(getattr(row, "potential", None)),
        "skill_level": optional_int(getattr(row, "skill_level", None)),
        "masteries": mastery_wire(getattr(row, "masteries", None)),
        "modules": module_wire(getattr(row, "modules", None)),
        # 信赖（M42）：与六列同口径，``None`` ＝ 未知（没有同步过、或上游没给这一项）。
        "favor_percent": optional_int(getattr(row, "favor_percent", None)),
        # 卡片资源（M43）：技能 id 与模组明细，同样 ``None`` ＝ 不知道（不是空）。
        "skill_ids": text_list_wire(getattr(row, "skill_ids", None)),
        "module_details": module_details_wire(getattr(row, "module_details", None)),
        "field_sources": field_sources_wire(getattr(row, "field_sources", None)),
        "recognized_at": iso_z(getattr(row, "recognized_at", None)),
        "updated_at": iso_z(getattr(row, "updated_at", None)),
    }



def text_list_wire(value: Any) -> list[str] | None:
    """字符串列表透传：``None`` / 非列表 ⇒ ``None``（不知道），条目非字符串则跳过。"""
    if not isinstance(value, list):
        return None
    return [item for item in value if isinstance(item, str)]


def module_details_wire(value: Any) -> list[dict[str, Any]] | None:
    """模组明细透传：只保留认识的键，``type`` / ``level`` 缺一不可，否则整条丢掉。

    明细是 M43 卡片显示 ``CCR-X`` 的依据；读侧不重新解释、不补默认值——认不出的条目
    丢掉比编一个标签好。
    """
    if not isinstance(value, list):
        return None
    details: list[dict[str, Any]] = []
    for item in value:
        if not isinstance(item, Mapping):
            continue
        module_type = item.get("type")
        level = optional_int(item.get("level"))
        if not isinstance(module_type, str) or level is None:
            continue
        details.append(
            {
                "type": module_type,
                "level": level,
                "equip_id": item.get("equip_id") if isinstance(item.get("equip_id"), str) else None,
                "branch": item.get("branch") if isinstance(item.get("branch"), str) else None,
                "label": item.get("label") if isinstance(item.get("label"), str) else None,
                "type_icon": (
                    item.get("type_icon") if isinstance(item.get("type_icon"), str) else None
                ),
            }
        )
    return details


def diff_wire(row: Any) -> dict[str, Any]:
    """差异行 → 对外形状；``char_id`` 旁带上干员名，页面不必再查一次。

    仓储可以把干员名一起查出来挂在行上（``name`` 属性），查不到时为 ``None``。
    """
    return {
        "id": int(getattr(row, "id", 0) or 0),
        "char_id": str(getattr(row, "char_id", "")),
        "name": getattr(row, "name", None),
        "field": str(getattr(row, "field", "")),
        "manual_value": getattr(row, "manual_value", None),
        "recognized_value": getattr(row, "recognized_value", None),
        "created_at": iso_z(getattr(row, "created_at", None)),
        "dismissed_at": iso_z(getattr(row, "dismissed_at", None)),
    }
