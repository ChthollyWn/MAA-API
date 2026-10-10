"""作业内容的纯规则层（规格 §9.4、§9.5）：协议校验、练度要求比对与静态检查。

这一层不读库、不读文件、不发请求：账号干员表、关卡地块、干员基础数据都由调用方
以普通映射传进来。三个使用方：

- ``services/copilot/draft.py``：存草稿前跑协议校验与静态检查；
- ``services/game/copilots.py``：搜作业时用 :func:`evaluate_operator` 比对账号数据；
- ``agent/tools/copilot_site.py``：把校验失败翻译成给模型看的字段列表。

**为什么另起一份协议校验**（规格 §2.7 的 D2、§9.5）：既有的
``ResourceService._validate_copilot`` 比官方协议严（拒收顶层 ``version`` / ``difficulty``、
强制 ``doc.title``、``skill`` 只许 1–3、``skill_usage`` 只许 0–2、不认
``potentiality``），作业站上的作业原样导入会被它拒掉。这里按 MAA 官方协议
（docs.maa.plus「战斗流程协议」，2026-10-10 抓取）放行这些字段，同时保证交给内核
的作业仍是合法 JSON。``domain/task.py`` 的 ``CopilotInput`` 由并行任务修（D2）；
两者口径一致：放行 ``skill=0``、``skill_usage=3``、``costs``。
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from typing import Any, Mapping, Sequence

from maa_api.domain.errors import AppError, ErrorCode

__all__ = [
    "ALLOWED_TOP_FIELDS",
    "ACTION_TYPES",
    "DEFAULT_DEPLOY_LIMIT",
    "PROTOCOL_NOTE",
    "SKILL_COUNT_BY_RARITY",
    "STATIC_VIOLATION_CODES",
    "SUPPORT_NOTE",
    "OperatorCheck",
    "check_requirements",
    "content_bytes",
    "evaluate_operator",
    "missing_operator_names",
    "module_types_of",
    "normalize_content",
    "static_check",
    "validate_or_raise",
]

#: 官方协议里作业 JSON 的顶层字段白名单（MAA v6.18.0 的
#: ``docs/zh-cn/protocol/copilot-schema.md``＋作业站实际发出来的字段）。
#: 多出来的未知顶层字段拒收：它们只会是拼错的键，静默忽略会让模型以为自己写对了。
ALLOWED_TOP_FIELDS = frozenset(
    {
        "version",
        "stage_name",
        "difficulty",
        "minimum_required",
        "doc",
        "opers",
        "groups",
        "actions",
        "type",
        # SSS（保全派驻）专用；本服务不执行它，但导入的副本要能存下来。
        "buff",
        "equipment",
        "strategy",
        "tool_men",
        "drops",
        "blacklist",
        "stages",
    }
)

#: 官方协议的 action 类型（中英文都收，与内核一致）。只做白名单提示，不做限制：
#: ``Click`` / ``Swipe`` 等动作一律放行（Q38）。
ACTION_TYPES = frozenset(
    {
        "Deploy",
        "Skill",
        "Retreat",
        "SpeedUp",
        "BulletTime",
        "SkillUsage",
        "Output",
        "SkillDaemon",
        "MoveCamera",
        "ResetStopwatch",
        "Click",
        "Swipe",
        "SetUnitLocation",
        "部署",
        "技能",
        "撤退",
        "二倍速",
        "子弹时间",
        "技能用法",
        "打印",
        "摆完挂机",
        "移动镜头",
        "重置全局计时器",
        "点击",
        "滑动",
        "设置单位坐标",
    }
)

#: 关卡文件缺失或部署上限取不到时用的默认值（规格 §9.5 的「部署数 ≤ 部署上限」）。
DEFAULT_DEPLOY_LIMIT = 8

#: 传给模型的一句话：这份校验的口径来自官方协议。
PROTOCOL_NOTE = (
    "按 MAA 官方战斗流程协议校验：放行 skill=0、skill_usage=3、costs、version、"
    "difficulty，以及作业站的 potentiality 键"
)

#: 缺一名干员时借助战的说明（规格 §9.4 的 `missing_one`、Q42）。
SUPPORT_NOTE = "缺一名干员，可借助战补位（每份作业只有一名）"

#: 拿不到干员技能数时的稀有度默认值（与 ``services/account/rules.py`` 同口径，但那里
#: 是账号数据的口径；这里要能在不 import 账号层的情况下工作）。
SKILL_COUNT_BY_RARITY: dict[int, int] = {1: 1, 2: 1, 3: 2, 4: 2, 5: 3, 6: 3}
_DEFAULT_SKILL_COUNT = 1

#: ``buildableType``（关卡文件）→ 可以站的位置类型：1 地面、2 高台、3 都可以。
_MELEE_TYPES = frozenset({1, 3})
_RANGED_TYPES = frozenset({2, 3})

#: 静态检查里会出现在 ``violations`` 上的 code；调用方与测试据此断言。
STATIC_VIOLATION_CODES = (
    "map_unavailable",
    "location_invalid",
    "location_out_of_bounds",
    "tile_not_deployable",
    "tile_position_mismatch",
    "operator_unknown",
    "support_slot_exceeded",
    "deploy_limit_exceeded",
    "skill_index_exceeded",
)


def _number(value: Any) -> int | None:
    """宽松取整数：浮点、数字字符串都收，布尔与其余类型返回 ``None``。"""
    if isinstance(value, bool):
        return None
    if isinstance(value, int):
        return value
    if isinstance(value, float):
        return int(value)
    if isinstance(value, str):
        text = value.strip()
        try:
            return int(float(text))
        except ValueError:
            return None
    return None


def _text(value: Any) -> str | None:
    if isinstance(value, str) and value.strip():
        return value.strip()
    return None


# ---------------------------------------------------------------------------
# 作业里的干员与分组
# ---------------------------------------------------------------------------
def group_members(content: Mapping[str, Any], name: str) -> tuple[Mapping[str, Any], ...]:
    """按分组名取成员；找不到返回空元组。"""
    for group in content.get("groups") or ():
        if isinstance(group, Mapping) and group.get("name") == name:
            members = group.get("opers")
            if isinstance(members, list):
                return tuple(item for item in members if isinstance(item, Mapping))
    return ()


def declared_operators(content: Mapping[str, Any]) -> tuple[tuple[str, Mapping[str, Any]], ...]:
    """作业声明的全部待部署项：``(显示名, 条目)``。

    ``opers`` 原样列出；``groups`` 每个分组占一条，条目是分组本身，显示名是分组名。
    执行时一个分组只会选中一名成员，所以「缺口」按分组算一条，不是按成员算。
    """
    result: list[tuple[str, Mapping[str, Any]]] = []
    for item in content.get("opers") or ():
        if isinstance(item, Mapping):
            name = _text(item.get("name"))
            result.append((name or "?", item))
    for group in content.get("groups") or ():
        if isinstance(group, Mapping):
            name = _text(group.get("name"))
            result.append((name or "?", group))
    return tuple(result)


def candidates_of(entry: Mapping[str, Any]) -> tuple[Mapping[str, Any], ...]:
    """一条待部署项的候选成员：``opers`` 项是自己，分组是 ``opers`` 列表。"""
    if isinstance(entry.get("opers"), list):
        return tuple(item for item in entry["opers"] if isinstance(item, Mapping))
    return (entry,)


# ---------------------------------------------------------------------------
# 练度要求
# ---------------------------------------------------------------------------
def module_types_of(box_row: Mapping[str, Any]) -> dict[str, int]:
    """账号行的模组列 → ``{类型: 等级}``；未知类型或非整数等级丢弃。"""
    modules = box_row.get("modules")
    if not isinstance(modules, Mapping):
        return {}
    cleaned: dict[str, int] = {}
    for key, raw in modules.items():
        level = _number(raw)
        if level is None:
            continue
        cleaned[str(key)] = level
    return cleaned


def _skill_level_of(row: Mapping[str, Any], skill_index: int | None) -> tuple[int, bool]:
    """账号行的「技能等级」与「是否只给了下界」。

    返回的第一项按官方协议的 1–10 刻度：技能等级 1–7 是本体等级，8/9/10 是专精一/
    二/三（作业站的 ``requirements.skill_level`` 用的就是这个刻度，实测有 10）。
    第二项为真表示只算出了下界（缺 ``masteries`` 数据时不能断言专精没满）。
    """
    base = _number(row.get("skill_level")) or 7
    masteries = row.get("masteries")
    known = isinstance(masteries, Mapping) and any(
        _number(masteries.get(f"{index}")) not in (None, 0) for index in (1, 2, 3)
    )
    if skill_index is not None:
        mastery = 0
        if isinstance(masteries, Mapping):
            mastery = _number(masteries.get(str(skill_index))) or 0
        return base + max(mastery, 0), False
    if known:
        best = max(_number(masteries.get(str(index))) or 0 for index in (1, 2, 3))
        return base + best, False
    # 没有任何专精数据：本体等级够就是「至少」够（下界），不够就是确定不够。
    return base, True


@dataclass(frozen=True, slots=True)
class RequirementCheck:
    """一条 ``requirements`` 的比对结果。"""

    field: str
    required: int
    actual: int | None
    met: bool
    #: 为真表示账号数据不足以断言（如没填模组）：既不算不满足，也算不出满足。
    skipped: bool = False

    def as_dict(self) -> dict[str, Any]:
        return {
            "field": self.field,
            "required": self.required,
            "actual": self.actual,
            "met": self.met,
            "skipped": self.skipped,
        }


#: 账号数据里没有对应字段的要求（作业站的 ``potentiality`` 是展示用的兼容键，
#: 官方协议自己也标注「暂不支持」）。
_UNSUPPORTED_FIELDS = frozenset({"potentiality", "potential", "module_level"})


def check_requirements(
    entry: Mapping[str, Any], row: Mapping[str, Any] | None
) -> tuple[RequirementCheck, ...]:
    """把一条作业干员条目的 ``requirements`` 与账号行逐项比对（规格 §9.4）。

    :param row: 账号行（``box_row_wire`` 的形状）；``None`` 表示账号里没有这名干员，
        此时所有要求都算不满足——调用方会把它计成一个缺口。
    """
    requirements = entry.get("requirements")
    if not isinstance(requirements, Mapping):
        return ()
    checks: list[RequirementCheck] = []
    for field, raw in requirements.items():
        name = str(field)
        required = _number(raw)
        if name in _UNSUPPORTED_FIELDS or required is None or required < 0:
            # 未知键与 -1（官方协议的「不作要求」）都不参与判定。
            continue
        if row is None:
            checks.append(RequirementCheck(name, required, None, False))
            continue
        if name == "elite":
            actual = _number(row.get("elite")) or 0
            checks.append(RequirementCheck(name, required, actual, actual >= required))
        elif name == "level":
            actual = _number(row.get("level")) or 0
            checks.append(RequirementCheck(name, required, actual, actual >= required))
        elif name == "skill_level":
            actual, bound_only = _skill_level_of(row, _number(entry.get("skill")))
            met = actual >= required
            # 只有「下界也够」才算跳过（不确定但没失败）；下界不够就是确定不满足。
            checks.append(
                RequirementCheck(name, required, actual, met, skipped=bound_only and met)
            )
        elif name == "module":
            actual, met, skipped = _module_check(required, row)
            checks.append(RequirementCheck(name, required, actual, met, skipped=skipped))
    return tuple(checks)


def _module_check(required: int, row: Mapping[str, Any]) -> tuple[int | None, bool, bool]:
    """模组要求：``-1`` 不作要求、``0`` 不要模组、``1–5`` 是模组编号。

    账号数据只有「按类型记的等级」（``{"X": 3}``），拿不到模组编号 → 类型的映射，
    所以只做能站得住的两件事：要模组而账号一个都没有＝不满足；有模组就按最大等级比
    ``module_level``（本服务的账号数据里没有这一列，因此只要能判「有」就算跳过）。
    """
    if required == 0:
        return 0, True, False
    modules = module_types_of(row)
    if not modules:
        return 0, False, False
    return max(modules.values()), True, True


@dataclass(frozen=True, slots=True)
class OperatorCheck:
    """一个待部署项的比对结果（``opers`` 一项或 ``groups`` 一个分组）。"""

    name: str
    kind: str  # "oper" | "group"
    owned: bool | None
    known: bool
    #: 实际比对的成员：``opers`` 是自己，分组是全部成员（任一满足即可）。
    members: tuple[str, ...] = ()
    satisfied: bool = False
    checks: tuple[RequirementCheck, ...] = ()
    #: 这条要占一个缺口（缺一名干员／分组里没一个满足）。
    missing: bool = False
    #: 作业站没有声明的键；账号数据缺口用这个说明（规格 §3.5 的「字段组」口径）。
    note: str | None = None

    def as_dict(self) -> dict[str, Any]:
        return {
            "name": self.name,
            "kind": self.kind,
            "owned": self.owned,
            "known": self.known,
            "satisfied": self.satisfied,
            "missing": self.missing,
            "members": list(self.members),
            "requirements": [check.as_dict() for check in self.checks],
            "note": self.note,
        }


def _row_owner(box: Mapping[str, Mapping[str, Any]] | None, name: str) -> tuple[Mapping[str, Any] | None, str | None]:
    """按名字或 char_id 找账号行，返回 ``(行, 账号里登记的名字)``。"""
    if not box:
        return None, None
    row = box.get(name)
    if row is None:
        row = box.get(name.casefold())
    if row is None:
        return None, None
    registered = row.get("name")
    return row, registered if isinstance(registered, str) and registered else None


def _unknown(
    box: Mapping[str, Mapping[str, Any]] | None,
    name: str,
    kind: str,
    members: Sequence[str],
    *,
    note: str,
) -> OperatorCheck:
    owned = False if box else None
    return OperatorCheck(
        name=name,
        kind=kind,
        owned=owned,
        known=False,
        members=tuple(members),
        satisfied=False,
        missing=True,
        note=note,
    )


def evaluate_operator(
    name: str,
    entry: Mapping[str, Any],
    *,
    box: Mapping[str, Mapping[str, Any]] | None,
    kind: str = "oper",
    known_names: Mapping[str, str] | None = None,
    candidates: Sequence[Mapping[str, Any]] = (),
) -> OperatorCheck:
    """判定一个待部署项能否被这份账号满足（规格 §9.4 的筛选口径）。

    - ``opers``：账号里有、且 ``requirements`` 全满足才算满足；
    - ``groups``：任一成员满足即可（规格 §9.4 原话）；
    - 名字不在账号里但在游戏数据里（``known_names``）＝「未拥有」，仍是缺口；
    - 名字两边都没有＝拼错了，``known=False``（搜索时算缺口，存草稿时拒收）。
    """
    if kind == "group":
        member_names = [
            _text(item.get("name")) or "?" for item in candidates if isinstance(item, Mapping)
        ]
        best: OperatorCheck | None = None
        for item in candidates:
            if not isinstance(item, Mapping):
                continue
            member = _text(item.get("name")) or "?"
            check = _evaluate_single(
                member, item, box=box, known_names=known_names, kind="group-member"
            )
            if check.satisfied:
                return OperatorCheck(
                    name=name,
                    kind="group",
                    owned=check.owned,
                    known=check.known,
                    members=tuple(member_names),
                    satisfied=True,
                    checks=check.checks,
                    note=f"分组里「{member}」满足要求",
                )
            best = best or check
        if best is None:
            return _unknown(
                box,
                name,
                "group",
                member_names,
                note="分组里没有成员",
            )
        return OperatorCheck(
            name=name,
            kind="group",
            owned=best.owned,
            known=best.known,
            members=tuple(member_names),
            satisfied=False,
            checks=best.checks,
            missing=True,
            note=f"分组里没有满足要求的成员（示例：{best.name}）",
        )
    return _evaluate_single(name, entry, box=box, known_names=known_names, kind="oper")


def _evaluate_single(
    name: str,
    entry: Mapping[str, Any],
    *,
    box: Mapping[str, Mapping[str, Any]] | None,
    known_names: Mapping[str, str] | None,
    kind: str,
) -> OperatorCheck:
    row, registered = _row_owner(box, name)
    display = registered or name
    if row is not None and bool(row.get("own", True)):
        checks = check_requirements(entry, row)
        failed = [check for check in checks if not check.met and not check.skipped]
        return OperatorCheck(
            name=display,
            kind=kind,
            owned=True,
            known=True,
            satisfied=not failed,
            checks=checks,
            missing=bool(failed),
            note=None if not failed else "练度不满足：" + "、".join(
                f"{check.field} {check.actual} < {check.required}" for check in failed
            ),
        )
    if row is not None:
        # 账号里有这条记录但 own=false：识别说未拥有。
        return OperatorCheck(
            name=display,
            kind=kind,
            owned=False,
            known=True,
            satisfied=False,
            checks=check_requirements(entry, row),
            missing=True,
            note="账号数据里标记为未拥有",
        )
    if known_names is not None and name in known_names:
        return OperatorCheck(
            name=name,
            kind=kind,
            owned=False,
            known=True,
            satisfied=False,
            missing=True,
            note="干员存在，但账号数据里没有（可能需要借助战）",
        )
    if known_names is None:
        # 账号数据整体取不到：不下「不存在」的结论，只标未拥有并说明。
        return OperatorCheck(
            name=name,
            kind=kind,
            owned=False,
            known=True,
            satisfied=False,
            missing=True,
            note="没有账号干员数据，无法判断是否拥有",
        )
    return OperatorCheck(
        name=name,
        kind=kind,
        owned=False,
        known=False,
        satisfied=False,
        missing=True,
        note="游戏数据里找不到这个名字，检查是否拼错",
    )


def evaluate_content(
    content: Mapping[str, Any],
    *,
    box: Mapping[str, Mapping[str, Any]] | None,
    known_names: Mapping[str, str] | None = None,
) -> tuple[OperatorCheck, ...]:
    """整份作业的逐项判定；顺序与作业里声明的一致。"""
    results: list[OperatorCheck] = []
    for name, entry in declared_operators(content):
        candidates = candidates_of(entry)
        kind = "group" if isinstance(entry.get("opers"), list) else "oper"
        results.append(
            evaluate_operator(
                name,
                entry,
                box=box,
                kind=kind,
                known_names=known_names,
                candidates=candidates,
            )
        )
    return tuple(results)


def missing_operator_names(checks: Sequence[OperatorCheck]) -> list[str]:
    """缺口列表：缺的干员名或分组名。"""
    return [check.name for check in checks if check.missing]


def tier_of(missing: int) -> str:
    """缺口数 → 三档（规格 §9.4）：``ok`` / ``missing_one`` / ``infeasible``。"""
    if missing <= 0:
        return "ok"
    if missing == 1:
        return "missing_one"
    return "infeasible"


# ---------------------------------------------------------------------------
# 协议校验
# ---------------------------------------------------------------------------
def _invalid(message: str, **details: Any) -> AppError:
    return AppError(ErrorCode.COPILOT_JSON_INVALID, message, details or None)


def normalize_content(content: Any) -> dict[str, Any]:
    """协议校验（D2 修复后的口径）；返回浅拷贝，失败抛 ``COPILOT_JSON_INVALID``。

    放行清单（与 ``CopilotInput`` 的 D2 修复一致）：顶层 ``version`` 与 ``difficulty``、
    ``doc`` 缺 ``title``、``skill=0``、``skill_usage=3``、``action`` 上的 ``costs``、
    以及作业站的 ``requirements.potentiality``。``actions`` 里的 ``Click`` / ``Swipe``
    等动作不做限制（Q38）。
    """
    if not isinstance(content, Mapping):
        raise _invalid("Copilot 内容必须是 JSON 对象")
    unknown = sorted(set(map(str, content)) - ALLOWED_TOP_FIELDS)
    if unknown:
        raise _invalid("Copilot 包含未知顶层字段", fields=unknown)
    stage = _text(content.get("stage_name"))
    if not stage:
        raise _invalid("Copilot 缺少有效的 stage_name", field="stage_name")
    version = content.get("version")
    if version is not None and _number(version) is None:
        raise _invalid("Copilot version 必须是数字", field="version")
    difficulty = content.get("difficulty")
    if difficulty is not None and _number(difficulty) is None:
        raise _invalid("Copilot difficulty 必须是数字", field="difficulty")
    if not _text(content.get("minimum_required")):
        raise _invalid("Copilot 缺少有效的 minimum_required", field="minimum_required")
    job_type = content.get("type")
    if job_type is not None and str(job_type) not in {"SSS", "PRTS"}:
        raise _invalid("Copilot type 只支持 SSS", field="type")
    if content.get("doc") is not None:
        _check_doc(content["doc"])
    opers = content.get("opers")
    if opers is not None:
        _check_operator_list(opers, field="opers")
    groups = content.get("groups")
    if groups is not None:
        if not isinstance(groups, list):
            raise _invalid("Copilot groups 必须是数组", field="groups")
        for index, group in enumerate(groups):
            if not isinstance(group, Mapping):
                raise _invalid("Copilot groups 项必须是对象", field=f"groups[{index}]")
            if not _text(group.get("name")):
                raise _invalid("Copilot 分组缺少 name", field=f"groups[{index}].name")
            if not isinstance(group.get("opers"), list) or not group["opers"]:
                raise _invalid(
                    "Copilot 分组必须包含非空 opers 数组", field=f"groups[{index}].opers"
                )
            _check_operator_list(group["opers"], field=f"groups[{index}].opers")
    actions = content.get("actions")
    if actions is not None:
        if not isinstance(actions, list):
            raise _invalid("Copilot actions 必须是数组", field="actions")
        for index, action in enumerate(actions):
            if not isinstance(action, Mapping):
                raise _invalid("Copilot actions 项必须是对象", field=f"actions[{index}]")
            if "type" in action and not isinstance(action["type"], str):
                raise _invalid("Copilot action 的 type 必须是字符串", field=f"actions[{index}].type")
            if "location" in action and not _is_location(action["location"]):
                raise _invalid(
                    "Copilot action 的 location 必须是两个数字", field=f"actions[{index}].location"
                )
    if content.get("type") == "SSS":
        stages = content.get("stages")
        if not isinstance(stages, list) or not stages:
            raise _invalid("SSS 作业必须包含 stages 数组", field="stages")
    elif actions is None:
        raise _invalid("常规 Copilot 作业必须包含 actions 数组", field="actions")
    return dict(content)


def _check_doc(doc: Any) -> None:
    """``doc`` 可以是对象或字符串；对象里只校验能判的类型，``title`` 不再强制（D2）。"""
    if isinstance(doc, str):
        return
    if not isinstance(doc, Mapping):
        raise _invalid("Copilot doc 必须是对象或字符串", field="doc")
    for key, value in doc.items():
        if key in {"title", "details", "title_color", "details_color"} and not isinstance(
            value, str
        ):
            raise _invalid(f"Copilot doc.{key} 必须是字符串", field=f"doc.{key}")


def _check_operator_list(items: Any, *, field: str) -> None:
    if not isinstance(items, list):
        raise _invalid(f"Copilot {field} 必须是数组", field=field)
    for index, item in enumerate(items):
        if not isinstance(item, Mapping):
            raise _invalid(f"Copilot {field}[{index}] 必须是对象", field=f"{field}[{index}]")
        if not _text(item.get("name")):
            raise _invalid(f"Copilot {field}[{index}] 缺少 name", field=f"{field}[{index}].name")
        skill = item.get("skill")
        if skill is not None:
            value = _number(skill)
            if value is None or not 0 <= value <= 3:
                raise _invalid(
                    "Copilot 的 skill 只能是 0–3（0 = 不指定技能）",
                    field=f"{field}[{index}].skill",
                )
        usage = item.get("skill_usage")
        if usage is not None:
            value = _number(usage)
            # 0 不自动使用 / 1 好了就用 / 2 用 X 次 / 3 自动判断时机（官方协议 0–3）。
            if value is None or not 0 <= value <= 3:
                raise _invalid(
                    "Copilot 的 skill_usage 只能是 0–3",
                    field=f"{field}[{index}].skill_usage",
                )
        requirements = item.get("requirements")
        if requirements is not None and not isinstance(requirements, Mapping):
            raise _invalid(
                f"Copilot {field}[{index}].requirements 必须是对象",
                field=f"{field}[{index}].requirements",
            )


def _is_location(value: Any) -> bool:
    return (
        isinstance(value, (list, tuple))
        and len(value) == 2
        and all(_number(item) is not None for item in value)
    )


# ---------------------------------------------------------------------------
# 静态检查
# ---------------------------------------------------------------------------
@dataclass(slots=True)
class _Checker:
    """静态检查的上下文：地块、干员基础数据、账号干员表。"""

    layout: Mapping[str, Any] | None
    operators: Mapping[str, Mapping[str, Any]] | None
    box: Mapping[str, Mapping[str, Any]] | None
    deploy_limit: int | None
    allow_support: bool
    violations: list[dict[str, Any]] = field(default_factory=list)
    skipped: list[str] = field(default_factory=list)
    deploy_count: int = 0
    support_used: int = 0

    # -- 地块 -------------------------------------------------------------
    def tile(self, x: int, y: int) -> str | None:
        """归一化后的 3 字符地块码（地形 可部署 高度）；地图取不到时返回 ``None``。"""
        if self.layout is None:
            return None
        tiles = self.layout.get("tiles")
        if not isinstance(tiles, list) or not tiles:
            return None
        height = len(tiles)
        row = tiles[y] if 0 <= y < height else None
        if not isinstance(row, list) or not 0 <= x < len(row):
            return None
        cell = row[x]
        if isinstance(cell, str) and len(cell) >= 3:
            return cell
        return None

    def violation(self, code: str, message: str, **details: Any) -> None:
        self.violations.append({"code": code, "message": message, **details})

    def note_skip(self, reason: str) -> None:
        if reason not in self.skipped:
            self.skipped.append(reason)

    # -- 地块判定 ---------------------------------------------------------
    def check_location(self, name: str, location: Any, index: int) -> None:
        if not _is_location(location):
            self.violation(
                "location_invalid",
                f"{name} 的 location 必须是两个数字",
                operator=name,
                action=index,
            )
            return
        x, y = (_number(location[0]), _number(location[1]))
        assert x is not None and y is not None
        if self.layout is None:
            self.note_skip("map")
            return
        tile = self.tile(x, y)
        if tile is None:
            self.violation(
                "location_out_of_bounds",
                f"{name} 的部署点 [{x}, {y}] 超出地图范围",
                operator=name,
                action=index,
                location=[x, y],
            )
            return
        buildable = tile[1]
        if buildable not in {"1", "2", "3"}:
            self.violation(
                "tile_not_deployable",
                f"{name} 的部署点 [{x}, {y}] 不是可部署格子",
                operator=name,
                action=index,
                location=[x, y],
                tile=tile,
            )
            return
        position = self._position_of(name)
        if position is None:
            self.note_skip("operator_position")
            return
        allowed = _MELEE_TYPES if position == "MELEE" else _RANGED_TYPES
        if buildable not in {str(item) for item in allowed}:
            want = "地面" if position == "MELEE" else "高台"
            self.violation(
                "tile_position_mismatch",
                f"{name} 是{'近战' if position == 'MELEE' else '远程'}位，"
                f"部署点 [{x}, {y}] 只能放{want}位",
                operator=name,
                action=index,
                location=[x, y],
                tile=tile,
                position=position,
            )

    def _position_of(self, name: str) -> str | None:
        """干员的近战/远程：按中文名或 char_id 查游戏数据。"""
        if not self.operators:
            return None
        record = self.operators.get(name)
        if record is None:
            record = self.operators.get(name.casefold())
        if record is None:
            return None
        position = record.get("position")
        if isinstance(position, str) and position in {"MELEE", "RANGED"}:
            return position
        return None

    # -- 干员 -------------------------------------------------------------
    def check_operator(self, check: OperatorCheck) -> None:
        if not check.known:
            self.violation(
                "operator_unknown",
                f"游戏数据里找不到干员「{check.name}」，检查是否拼错",
                operator=check.name,
            )
            return
        if check.owned is None or check.owned:
            return
        if not self.allow_support:
            self.violation(
                "support_slot_exceeded",
                f"账号里没有干员「{check.name}」，且本次不允许助战补位",
                operator=check.name,
            )
            return
        self.support_used += 1
        if self.support_used > 1:
            self.violation(
                "support_slot_exceeded",
                f"账号里没有干员「{check.name}」：助战每份作业只能补一名",
                operator=check.name,
                support_used=self.support_used,
            )

    # -- 技能 -------------------------------------------------------------
    def check_skill_index(self, name: str, entry: Mapping[str, Any]) -> None:
        skill = entry.get("skill")
        if skill is None:
            return
        index = _number(skill)
        if index is None or index <= 0:
            # ``skill=0`` 是官方协议的「不指定技能」（D2）；负数留给协议校验。
            return
        slots = self._skill_slots(name)
        if slots is None:
            self.note_skip("operator_skills")
            return
        if index > slots:
            self.violation(
                "skill_index_exceeded",
                f"{name} 只有 {slots} 个技能，作业里指定了第 {index} 个",
                operator=name,
                skill=index,
                skill_count=slots,
            )

    def _skill_slots(self, name: str) -> int | None:
        if not self.operators:
            return None
        record = self.operators.get(name) or self.operators.get(name.casefold())
        if record is None:
            # 分组里的名字只是分组名，成员各自查。
            return None
        count = _number(record.get("skill_count"))
        if count is not None and count > 0:
            return count
        rarity = _number(record.get("rarity"))
        if rarity is None:
            return None
        return SKILL_COUNT_BY_RARITY.get(rarity, _DEFAULT_SKILL_COUNT)

    # -- 部署上限 ---------------------------------------------------------
    def check_deploy_count(self) -> None:
        if self.deploy_count <= 0:
            return
        if self.deploy_limit is None:
            self.note_skip("deploy_limit")
            return
        if self.deploy_count > self.deploy_limit:
            self.violation(
                "deploy_limit_exceeded",
                f"作业部署了 {self.deploy_count} 名干员，本关部署上限是 {self.deploy_limit}",
                deploy_count=self.deploy_count,
                deploy_limit=self.deploy_limit,
            )


def _iter_deploys(content: Mapping[str, Any]) -> list[tuple[int, Mapping[str, Any]]]:
    actions = content.get("actions")
    if not isinstance(actions, list):
        return []
    return [
        (index, action)
        for index, action in enumerate(actions)
        if isinstance(action, Mapping)
        and str(action.get("type", "Deploy")) in {"Deploy", "部署"}
    ]


def static_check(
    content: Mapping[str, Any],
    *,
    layout: Mapping[str, Any] | None = None,
    operators: Mapping[str, Mapping[str, Any]] | None = None,
    box: Mapping[str, Mapping[str, Any]] | None = None,
    deploy_limit: int | None = None,
    allow_support: bool = True,
) -> dict[str, Any]:
    """静态检查（规格 §9.5）：坐标、地块、干员、部署数与技能序号。

    ``Click`` / ``Swipe`` 等动作不做限制（Q38）。``layout`` 与 ``operators`` 取不到
    时对应的检查记进 ``skipped``（**不是**通过，也不是拒绝）；调用方要把 ``skipped``
    原样带给模型，避免它以为已经查过地图。
    """
    checker = _Checker(
        layout=layout,
        operators=operators,
        box=box,
        deploy_limit=deploy_limit,
        allow_support=allow_support,
    )
    if layout is None:
        checker.note_skip("map")
    if not operators:
        checker.note_skip("operator_position")
        checker.note_skip("operator_skills")

    deploys = _iter_deploys(content)
    for index, action in deploys:
        raw = action.get("name")
        name = _text(raw) or "?"
        checker.check_location(name, action.get("location"), index)
    checker.deploy_count = len(deploys)

    for check in evaluate_content(content, box=box, known_names=operators):
        checker.check_operator(check)
    for name, entry in declared_operators(content):
        if isinstance(entry.get("opers"), list):
            for member in candidates_of(entry):
                member_name = _text(member.get("name")) or "?"
                checker.check_skill_index(member_name, member)
        else:
            checker.check_skill_index(name, entry)
    checker.check_deploy_count()

    return {
        "ok": not checker.violations,
        "violations": checker.violations,
        "skipped": checker.skipped,
        "deploy_count": checker.deploy_count,
        "deploy_limit": deploy_limit,
        "support_used": checker.support_used,
    }


def validate_or_raise(
    content: Any,
    *,
    layout: Mapping[str, Any] | None = None,
    operators: Mapping[str, Mapping[str, Any]] | None = None,
    box: Mapping[str, Mapping[str, Any]] | None = None,
    deploy_limit: int | None = None,
    allow_support: bool = True,
) -> tuple[dict[str, Any], dict[str, Any]]:
    """协议校验 + 静态检查；任一条不过就抛 ``COPILOT_JSON_INVALID``。

    :returns: ``(内容, 静态检查报告)``。
    """
    normalized = normalize_content(content)
    report = static_check(
        normalized,
        layout=layout,
        operators=operators,
        box=box,
        deploy_limit=deploy_limit,
        allow_support=allow_support,
    )
    if not report["ok"]:
        raise AppError(
            ErrorCode.COPILOT_JSON_INVALID,
            "作业校验未通过：" + "；".join(item["message"] for item in report["violations"]),
            {
                "violations": report["violations"],
                "skipped": report["skipped"],
                "protocol": PROTOCOL_NOTE,
            },
        )
    return normalized, report


def content_bytes(content: Any) -> int:
    """内容序列化后的字节数（体积口径与 `resource_service` 一致）。"""
    return len(json.dumps(content, ensure_ascii=False).encode("utf-8"))
