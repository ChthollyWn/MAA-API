"""委托的强度、上限、策略版本与建单校验（M69 规格 §4.2、§6）。"""

from __future__ import annotations

import re

import pytest

from maa_api.agent.tickets.mandates import (
    ORIGIN_SOURCES,
    RISK_ORDER,
    build_mandate,
    ceiling_for,
    ceiling_risk,
    exceeds_ceiling,
    lower_ceiling,
    policy_version,
    strength_for,
)
from maa_api.db.models import Mandate
from maa_api.domain.enums import (
    EventSource,
    MandateStrength,
    RiskLevel,
    TicketOrigin,
)
from maa_api.domain.errors import AppError, ErrorCode

_IM_SOURCES = {
    EventSource.IM_TELEGRAM,
    EventSource.IM_FEISHU,
    EventSource.IM_DINGTALK,
    EventSource.IM_QQ,
}

_VALID = {
    "ticket_id": "ticket-1",
    "scope": "刷 1-7",
    "origin": TicketOrigin.USER,
    "source": EventSource.WEB,
    "sender": "web",
    "rule_id": None,
    "spec_ceiling": RiskLevel.CONSUME,
}


def test_strength_and_ceiling_rules() -> None:
    assert set(RISK_ORDER) == set(RiskLevel)
    assert [RISK_ORDER[level] for level in RiskLevel] == [0, 1, 2]
    assert set(ORIGIN_SOURCES) == set(TicketOrigin)
    for allowed in ORIGIN_SOURCES.values():
        assert allowed <= set(EventSource)

    assert ORIGIN_SOURCES[TicketOrigin.USER] == {EventSource.WEB} | _IM_SOURCES
    assert ORIGIN_SOURCES[TicketOrigin.TRIGGER] == set(EventSource) - {
        EventSource.WEB
    }
    assert ORIGIN_SOURCES[TicketOrigin.GOAL] == {EventSource.GOAL}
    assert ORIGIN_SOURCES[TicketOrigin.WATCHDOG] == {EventSource.WATCHDOG}
    assert ORIGIN_SOURCES[TicketOrigin.SYSTEM] == {
        EventSource.PIPELINE,
        EventSource.CORE,
        EventSource.DEVICE,
        EventSource.SCHEDULE,
        EventSource.SYSTEM,
    }

    for origin in TicketOrigin:
        strength = strength_for(origin)
        if origin == TicketOrigin.USER:
            assert strength == MandateStrength.EXPLICIT
        else:
            assert strength == MandateStrength.INFERRED
        for spec_ceiling in RiskLevel:
            expected = spec_ceiling if origin == TicketOrigin.USER else RiskLevel.NONE
            assert ceiling_for(strength, spec_ceiling) == expected
            assert ceiling_for(strength, spec_ceiling.value) == expected


def test_lower_ceiling_and_exceeds() -> None:
    assert lower_ceiling(RiskLevel.DESTRUCTIVE) == RiskLevel.DESTRUCTIVE
    assert (
        lower_ceiling(RiskLevel.DESTRUCTIVE, RiskLevel.CONSUME)
        == RiskLevel.CONSUME
    )
    assert (
        lower_ceiling(RiskLevel.CONSUME, RiskLevel.NONE, RiskLevel.DESTRUCTIVE)
        == RiskLevel.NONE
    )
    assert lower_ceiling("destructive", "consume") == RiskLevel.CONSUME
    with pytest.raises(ValueError):
        lower_ceiling()

    for ceiling in RiskLevel:
        assert exceeds_ceiling(RiskLevel.NONE, ceiling) is False
    assert exceeds_ceiling(RiskLevel.CONSUME, RiskLevel.NONE) is True
    assert exceeds_ceiling(RiskLevel.CONSUME, RiskLevel.CONSUME) is False
    assert exceeds_ceiling(RiskLevel.CONSUME, RiskLevel.DESTRUCTIVE) is False
    assert exceeds_ceiling(RiskLevel.DESTRUCTIVE, RiskLevel.CONSUME) is True
    assert exceeds_ceiling(RiskLevel.DESTRUCTIVE, RiskLevel.DESTRUCTIVE) is False
    assert exceeds_ceiling("consume", "none") is True
    assert exceeds_ceiling("destructive", "consume") is True


def test_atomic_grant_counts_as_consume() -> None:
    assert ceiling_risk(RiskLevel.NONE, "grant_atomic_ops") == RiskLevel.CONSUME
    assert (
        ceiling_risk(RiskLevel.DESTRUCTIVE, "grant_atomic_ops")
        == RiskLevel.CONSUME
    )
    assert ceiling_risk("consume", "grant_copilot_attempts") == RiskLevel.CONSUME
    assert ceiling_risk("destructive", "start_upgrade_plan") == RiskLevel.DESTRUCTIVE
    assert ceiling_risk(RiskLevel.NONE, None) == RiskLevel.NONE
    assert (
        exceeds_ceiling(
            ceiling_risk(RiskLevel.NONE, "grant_atomic_ops"), RiskLevel.NONE
        )
        is True
    )


def test_policy_version_is_stable_hex() -> None:
    first = policy_version()
    second = policy_version()
    assert first == second
    assert re.fullmatch(r"[0-9a-f]{12}", first)


def test_build_mandate_validation() -> None:
    explicit = build_mandate(**_VALID)
    assert isinstance(explicit, Mandate)
    assert explicit.scope == "刷 1-7"
    assert explicit.strength == MandateStrength.EXPLICIT
    assert explicit.source == EventSource.WEB
    assert explicit.sender == "web"
    assert explicit.rule_id is None
    assert explicit.risk_ceiling == RiskLevel.CONSUME
    assert re.fullmatch(r"[0-9a-f]{12}", explicit.policy_version or "")

    stripped = build_mandate(**{**_VALID, "scope": "  刷 1-7  "})
    assert stripped.scope == "刷 1-7"

    im = build_mandate(
        **{
            **_VALID,
            "source": EventSource.IM_FEISHU,
            "sender": "im:feishu:oc_xxx",
        }
    )
    assert im.sender == "im:feishu:oc_xxx"
    assert im.risk_ceiling == RiskLevel.CONSUME

    inferred = build_mandate(
        ticket_id="ticket-2",
        scope="定时任务",
        origin=TicketOrigin.TRIGGER,
        source=EventSource.PIPELINE,
        sender=None,
        rule_id="rule-1",
        spec_ceiling=RiskLevel.DESTRUCTIVE,
    )
    assert inferred.strength == MandateStrength.INFERRED
    assert inferred.source == EventSource.PIPELINE
    assert inferred.rule_id == "rule-1"
    assert inferred.risk_ceiling == RiskLevel.NONE

    goal = build_mandate(
        ticket_id="ticket-3",
        scope="目标推进",
        origin=TicketOrigin.GOAL,
        source=EventSource.GOAL,
        sender=None,
        rule_id=None,
        spec_ceiling=RiskLevel.DESTRUCTIVE,
    )
    assert goal.risk_ceiling == RiskLevel.NONE

    # M82 §4.5.1：目标规划工单是用户这次明确请求的直接执行，允许显式委托；
    # 目标复盘工单（不传 strength/risk_ceiling）仍是推断委托、上限 none。
    planning = build_mandate(
        ticket_id="ticket-4",
        scope="为目标「银灰精二」做规划",
        origin=TicketOrigin.GOAL,
        source=EventSource.GOAL,
        sender="web",
        rule_id=None,
        spec_ceiling=RiskLevel.CONSUME,
        strength=MandateStrength.EXPLICIT,
        risk_ceiling=RiskLevel.CONSUME,
    )
    assert planning.strength == MandateStrength.EXPLICIT
    assert planning.risk_ceiling == RiskLevel.CONSUME

    invalid_cases = [
        ("空 scope", {"scope": "   "}),
        ("scope 超长", {"scope": "字" * 2001}),
        ("user 用 pipeline", {"source": EventSource.PIPELINE}),
        (
            "trigger 用 web",
            {
                "origin": TicketOrigin.TRIGGER,
                "source": EventSource.WEB,
                "sender": None,
                "rule_id": "rule-1",
            },
        ),
        (
            "trigger 缺 rule_id",
            {
                "origin": TicketOrigin.TRIGGER,
                "source": EventSource.PIPELINE,
                "sender": None,
            },
        ),
        (
            "trigger 的 rule_id 全是空白",
            {
                "origin": TicketOrigin.TRIGGER,
                "source": EventSource.PIPELINE,
                "sender": None,
                "rule_id": "  ",
            },
        ),
        ("user 缺 sender", {"sender": None}),
        ("user sender 不是 web 也不是 im:", {"sender": "rule:1"}),
        ("sender 含空白", {"sender": "im:feishu:oc x"}),
        ("sender 超过 128 字", {"sender": "im:" + "x" * 126}),
        (
            "rule_id 超过 48 字",
            {
                "origin": TicketOrigin.TRIGGER,
                "source": EventSource.PIPELINE,
                "sender": None,
                "rule_id": "r" * 49,
            },
        ),
        ("未知工单来源", {"origin": "bogus"}),
        ("未知事件来源", {"source": "bogus"}),
    ]
    for label, override in invalid_cases:
        with pytest.raises(AppError) as exc_info:
            build_mandate(**{**_VALID, **override})
        assert exc_info.value.code is ErrorCode.INVALID_PARAMETER, label
