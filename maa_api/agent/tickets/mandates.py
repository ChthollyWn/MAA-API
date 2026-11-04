"""委托的强度、风险上限与策略版本（M69 规格 §4.2、v3 09 §2）。

委托在 ``TicketService.create`` 里由 :func:`build_mandate` 生成，与工单同一事务
落库，此后不可变。本模块只有纯函数：不读库、不写库，也不依赖服务装配。

- 强度只看工单来源：``user`` 是显式授权，其余来源都是推断授权。
- 有效上限：显式委托取 Worker 代理定义的 ``risk_ceiling``，推断委托固定 ``none``；
  多方上限用 :func:`lower_ceiling` 取最低。
- ``grant_atomic_ops`` 按 ``consume`` 计入上限比较，其余确认动作原样比较。
"""

from __future__ import annotations

import hashlib
import json
from collections.abc import Mapping

from maa_api.agent.policy import (
    ACCOUNT_WRITE_TOOLS,
    ATOMIC_TOOL_NAMES,
    SAFE_TOOL_NAMES,
)
from maa_api.db.models import Mandate
from maa_api.domain.enums import (
    EventSource,
    MandateStrength,
    RiskLevel,
    TicketOrigin,
)
from maa_api.domain.errors import AppError, ErrorCode
from maa_api.domain.task import RISK_FIELDS

__all__ = [
    "MAX_SCOPE_CHARS",
    "ORIGIN_SOURCES",
    "RISK_ORDER",
    "build_mandate",
    "ceiling_for",
    "ceiling_risk",
    "exceeds_ceiling",
    "lower_ceiling",
    "policy_version",
    "strength_for",
]

RISK_ORDER: Mapping[RiskLevel, int] = {
    RiskLevel.NONE: 0,
    RiskLevel.CONSUME: 1,
    RiskLevel.DESTRUCTIVE: 2,
}
#: 委托范围的字符上限（按去首尾空白后的长度算）。
MAX_SCOPE_CHARS = 2000
#: 发起人标识的字符上限；``sender`` 不允许含空白（web / ``im:<platform>:<user>``）。
_MAX_SENDER_CHARS = 128
#: 触发规则的字符上限。
_MAX_RULE_ID_CHARS = 48

_IM_SOURCES = frozenset(
    {
        EventSource.IM_TELEGRAM,
        EventSource.IM_FEISHU,
        EventSource.IM_DINGTALK,
        EventSource.IM_QQ,
    }
)

#: 工单来源允许的事件源（v3 03 §15）：
#: USER → web 与四个 im_*；TRIGGER → 除 web 外全部；GOAL → goal；
#: WATCHDOG → watchdog；SYSTEM → pipeline、core、device、schedule、system。
#: TRIGGER 还要求 ``rule_id``。
ORIGIN_SOURCES: Mapping[TicketOrigin, frozenset[EventSource]] = {
    TicketOrigin.USER: frozenset({EventSource.WEB}) | _IM_SOURCES,
    TicketOrigin.TRIGGER: frozenset(EventSource) - {EventSource.WEB},
    TicketOrigin.GOAL: frozenset({EventSource.GOAL}),
    TicketOrigin.WATCHDOG: frozenset({EventSource.WATCHDOG}),
    TicketOrigin.SYSTEM: frozenset(
        {
            EventSource.PIPELINE,
            EventSource.CORE,
            EventSource.DEVICE,
            EventSource.SCHEDULE,
            EventSource.SYSTEM,
        }
    ),
}


def _invalid(message: str) -> AppError:
    """校验失败统一走 ``INVALID_PARAMETER``（规格 §4.2）。"""
    return AppError(ErrorCode.INVALID_PARAMETER, message)


def strength_for(origin: TicketOrigin) -> MandateStrength:
    """user → explicit，其余 → inferred（09 §2.1）。"""
    return (
        MandateStrength.EXPLICIT
        if origin == TicketOrigin.USER
        else MandateStrength.INFERRED
    )


def ceiling_for(strength: MandateStrength, spec_ceiling: RiskLevel | str) -> RiskLevel:
    """explicit 取代理定义上限，inferred 固定 none。"""
    if strength == MandateStrength.INFERRED:
        return RiskLevel.NONE
    try:
        return RiskLevel(spec_ceiling)
    except ValueError:
        raise _invalid(f"未知的风险上限：{spec_ceiling}") from None


def lower_ceiling(*ceilings: RiskLevel | str) -> RiskLevel:
    """按 :data:`RISK_ORDER` 取最低；至少一个参数。"""
    if not ceilings:
        raise ValueError("lower_ceiling 至少需要一个风险上限")
    try:
        values = [RiskLevel(ceiling) for ceiling in ceilings]
    except ValueError:
        raise _invalid(f"未知的风险上限：{ceilings}") from None
    return min(values, key=lambda value: RISK_ORDER[value])


def ceiling_risk(risk: RiskLevel | str, confirmation_action: str | None) -> RiskLevel:
    """参与上限比较的风险：grant_atomic_ops 按 consume 计，其余就是 risk（09 §2.3、§2.4）。"""
    if confirmation_action == "grant_atomic_ops":
        return RiskLevel.CONSUME
    try:
        return RiskLevel(risk)
    except ValueError:
        raise _invalid(f"未知的风险等级：{risk}") from None


def exceeds_ceiling(risk: RiskLevel | str, ceiling: RiskLevel | str) -> bool:
    """risk 为 consume/destructive 且序号高于 ceiling 时为 True；risk 为 none 永远 False。"""
    try:
        risk_value = RiskLevel(risk)
        ceiling_value = RiskLevel(ceiling)
    except ValueError:
        raise _invalid(f"未知的风险等级：risk={risk}、ceiling={ceiling}") from None
    if risk_value == RiskLevel.NONE:
        return False
    return RISK_ORDER[risk_value] > RISK_ORDER[ceiling_value]


def policy_version() -> str:
    """策略规则集的哈希（09 §2.1）。

    对 ``safe`` / ``atomic`` / ``account_write`` / ``risk_fields`` 四组规则（集合排序
    后）做 ``json.dumps(sort_keys=True)``，再取 sha256 的前 12 位十六进制。规则集
    变了这个值就变，事后复盘能知道当时按哪套规则判定。
    """
    rules = {
        "safe": sorted(SAFE_TOOL_NAMES),
        "atomic": sorted(ATOMIC_TOOL_NAMES),
        "account_write": sorted(ACCOUNT_WRITE_TOOLS),
        "risk_fields": {
            name: sorted(fields) for name, fields in RISK_FIELDS.items()
        },
    }
    encoded = json.dumps(rules, sort_keys=True).encode("utf-8")
    return hashlib.sha256(encoded).hexdigest()[:12]


def _normalize_origin(origin: TicketOrigin | str) -> TicketOrigin:
    try:
        return TicketOrigin(origin)
    except ValueError:
        raise _invalid(f"未知的工单来源：{origin}") from None


def _normalize_source(source: EventSource | str) -> EventSource:
    try:
        return EventSource(source)
    except ValueError:
        raise _invalid(f"未知的事件来源：{source}") from None


def build_mandate(
    *,
    ticket_id: str,
    scope: str,
    origin: TicketOrigin,
    source: EventSource,
    sender: str | None,
    rule_id: str | None,
    spec_ceiling: RiskLevel | str,
) -> Mandate:
    """校验并返回一个未落库的 Mandate；校验失败抛 ``AppError(INVALID_PARAMETER)``。

    scope 去首尾空白后 1–2000 字；source 必须属于 :data:`ORIGIN_SOURCES[origin]`；
    origin=user 时 sender 必填且为 ``web`` 或以 ``im:`` 开头；origin=trigger 时
    rule_id 必填；sender 不超过 128 字且不含空白；rule_id 不超过 48 字。
    """
    origin_value = _normalize_origin(origin)
    source_value = _normalize_source(source)

    scope_text = scope.strip() if isinstance(scope, str) else ""
    if not 1 <= len(scope_text) <= MAX_SCOPE_CHARS:
        raise _invalid(f"scope 去首尾空白后必须是 1–{MAX_SCOPE_CHARS} 字")

    if source_value not in ORIGIN_SOURCES[origin_value]:
        raise _invalid(f"来源 {source_value.value} 不能创建 {origin_value.value} 工单")

    if sender is not None:
        if not isinstance(sender, str):
            raise _invalid("sender 必须是字符串")
        if len(sender) > _MAX_SENDER_CHARS:
            raise _invalid(f"sender 不能超过 {_MAX_SENDER_CHARS} 字")
        if any(character.isspace() for character in sender):
            raise _invalid("sender 不能含空白")

    if origin_value == TicketOrigin.USER:
        if not sender:
            raise _invalid("origin=user 时 sender 必填")
        if sender != "web" and not sender.startswith("im:"):
            raise _invalid("origin=user 时 sender 必须是 web 或以 im: 开头")

    if rule_id is not None:
        if not isinstance(rule_id, str):
            raise _invalid("rule_id 必须是字符串")
        if len(rule_id) > _MAX_RULE_ID_CHARS:
            raise _invalid(f"rule_id 不能超过 {_MAX_RULE_ID_CHARS} 字")

    if origin_value == TicketOrigin.TRIGGER and not (rule_id and rule_id.strip()):
        raise _invalid("origin=trigger 时 rule_id 必填")

    strength = strength_for(origin_value)
    return Mandate(
        ticket_id=ticket_id,
        scope=scope_text,
        strength=strength.value,
        source=source_value.value,
        sender=sender,
        rule_id=rule_id,
        risk_ceiling=ceiling_for(strength, spec_ceiling).value,
        policy_version=policy_version(),
    )
