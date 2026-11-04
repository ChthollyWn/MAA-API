"""五条内置事件规则与诊断草稿（M81 规格 §4.2、08 §3.2/§4.7）。

覆盖 §6 的两条：内置规则与 08 §3.2 逐列一致；流水线诊断草稿带 v2 诊断上下文
（``failure_object`` 与失败任务都在说明里）。
"""

from __future__ import annotations

import asyncio
import json
from types import SimpleNamespace

from maa_api.agent.events.builtin_rules import (
    BUILTIN_RULES,
    DEVICE_UNAVAILABLE_POLICY,
    DIAGNOSE_SCOPE,
    SANITY_FULL_POLICY,
    UPDATE_AVAILABLE_POLICY,
    diagnose_core_draft,
    diagnose_pipeline_draft,
)
from maa_api.agent.events.rules import merge_rules
from maa_api.db.models import AgentTrigger, utcnow
from maa_api.domain.enums import TriggerMode

#: 08 §3.2 中里程碑为 M81 的五条：id、来源、种类、条件、档位、Worker、冷却、总开关。
EXPECTED = [
    (
        "diagnose-pipeline-failure",
        "pipeline",
        "pipeline.failed",
        {},
        TriggerMode.AUTO,
        "diagnose",
        0,
        "agent.auto_diagnose_failures",
    ),
    (
        "diagnose-core-crash",
        "core",
        "core.crashed",
        {},
        TriggerMode.AUTO,
        "diagnose",
        0,
        "agent.auto_diagnose_failures",
    ),
    (
        "sanity-full",
        "system",
        "system.sanity_full",
        {},
        TriggerMode.OBSERVE,
        "daily_ops",
        21600,
        None,
    ),
    (
        "device-unavailable",
        "device",
        "device.unavailable",
        {},
        TriggerMode.OBSERVE,
        "ops",
        3600,
        None,
    ),
    (
        "update-available",
        "system",
        "system.update_available",
        {"target": ["core", "resource"]},
        TriggerMode.OBSERVE,
        "ops",
        86400,
        None,
    ),
]


def _rule(rule_id: str):
    return merge_rules(
        BUILTIN_RULES, [], default_mode=TriggerMode.OBSERVE
    ).get(rule_id)


def test_builtin_rules_match_doc_table():
    assert len(BUILTIN_RULES) == 5
    assert [rule.id for rule in BUILTIN_RULES] == [row[0] for row in EXPECTED]
    assert len({rule.title for rule in BUILTIN_RULES}) == 5

    for rule, expected in zip(BUILTIN_RULES, EXPECTED, strict=True):
        (
            rule_id,
            source,
            kind_pattern,
            conditions,
            mode,
            worker,
            cooldown,
            gate_setting,
        ) = expected
        assert rule.id == rule_id
        assert rule.source == source
        assert rule.kind_pattern == kind_pattern
        assert dict(rule.conditions) == conditions
        assert rule.mode == mode
        assert rule.worker == worker
        assert rule.cooldown_seconds == cooldown
        assert rule.gate_setting == gate_setting
        assert 1 <= len(rule.title) <= 64
        assert rule.draft is not None or gate_setting is None

    # 不研判的两条诊断规则：policy 为空串，草稿自己生成。
    diagnose = BUILTIN_RULES[:2]
    for rule in diagnose:
        assert rule.policy == ""
        assert rule.draft is not None
        assert rule.gate_setting == "agent.auto_diagnose_failures"
    # 三条研判规则的提示全文照 08 §4.7。
    by_id = {rule.id: rule for rule in BUILTIN_RULES}
    assert by_id["sanity-full"].policy == SANITY_FULL_POLICY
    assert by_id["device-unavailable"].policy == DEVICE_UNAVAILABLE_POLICY
    assert by_id["update-available"].policy == UPDATE_AVAILABLE_POLICY
    assert "ignore" in SANITY_FULL_POLICY
    assert "accept" in DEVICE_UNAVAILABLE_POLICY
    assert "ignore" in UPDATE_AVAILABLE_POLICY


class _IncidentService:
    """M73 诊断适配器的替身：记录调用并返回固定现场。"""

    def __init__(self, context: dict) -> None:
        self.context = context
        self.calls: list[tuple[str, tuple]] = []

    async def _pipeline_context(self, pipeline_id: str) -> dict:
        self.calls.append(("pipeline", (pipeline_id,)))
        return self.context

    async def _core_context(self, generation: int, record: dict) -> dict:
        self.calls.append(("core", (generation, record)))
        return self.context


def test_diagnose_pipeline_draft_contains_v2_incident_context():
    context = {
        "incident_type": "pipeline_failure",
        "failure_object": {
            "pipeline_id": "p1",
            "title": "日常",
            "status": "failed",
            "error_code": "TASK_ERROR",
        },
        "task_status": [
            {"task_id": 1, "name": "开始唤醒", "status": "succeeded"},
            {"task_id": 2, "name": "刷理智", "status": "failed"},
        ],
    }
    service = _IncidentService(context)
    trigger = AgentTrigger(
        id=7,
        source="pipeline",
        kind="pipeline.failed",
        thread_key="pipeline:p1",
        payload={"pipeline_id": "p1", "title": "日常", "status": "failed"},
        created_at=utcnow(),
    )
    rule = _rule("diagnose-pipeline-failure")
    assert rule is not None
    ctx = SimpleNamespace(
        trigger=trigger,
        rule=rule,
        decision=None,
        app=SimpleNamespace(state=SimpleNamespace(agent_incident_service=service)),
    )

    draft = asyncio.run(diagnose_pipeline_draft(ctx))

    assert draft.title == "诊断：流水线「日常」失败"
    assert draft.scope == DIAGNOSE_SCOPE
    assert len(draft.title) <= 64
    assert len(draft.brief) <= 4000
    assert draft.brief.startswith("【诊断上下文】")
    assert "failure_object" in draft.brief
    assert "刷理智" in draft.brief
    assert json.loads(draft.brief.split("\n", 1)[1]) == context
    assert service.calls == [("pipeline", ("p1",))]


def test_diagnose_core_draft_uses_generation_and_crash_record():
    context = {
        "incident_type": "idle_core_crash",
        "failure_object": {
            "core_id": "default",
            "generation": 3,
            "status": "crashed",
        },
    }
    service = _IncidentService(context)
    trigger = AgentTrigger(
        id=9,
        source="core",
        kind="core.crashed",
        thread_key="core:default",
        payload={"core_id": "default", "generation": 3, "reason": "crash"},
        created_at=utcnow(),
    )
    rule = _rule("diagnose-core-crash")
    assert rule is not None
    ctx = SimpleNamespace(
        trigger=trigger,
        rule=rule,
        decision=None,
        app=SimpleNamespace(state=SimpleNamespace(agent_incident_service=service)),
    )

    draft = asyncio.run(diagnose_core_draft(ctx))

    assert draft.title == "诊断：内核第 3 代崩溃"
    assert draft.scope == DIAGNOSE_SCOPE
    assert "failure_object" in draft.brief
    assert service.calls[0][0] == "core"
    assert service.calls[0][1][0] == 3
    assert service.calls[0][1][1]["reason"] == "crash"
