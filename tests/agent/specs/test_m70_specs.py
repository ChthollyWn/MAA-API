"""M70 T5：六份代理定义与三个结构化 schema（规格 §4.7、§6）。

四条用例覆盖：十六份定义加载与一致规则、``general`` 的工具集合、四个子代理的
输出 schema 与正数步数、正文身份标记唯一。T6 注册七个工具之后才能通过加载校验。
"""

from __future__ import annotations

import pytest
from pydantic import ValidationError

from maa_api.agent.llm import schemas  # noqa: F401 触发三个 schema 的注册
from maa_api.agent.llm.structured import get_schema
from maa_api.agent.specs.registry import (
    AgentSpecRegistry,
    builtin_spec,
    builtin_specs,
)
from maa_api.agent.tools import build_registry
from maa_api.agent.tools.catalog import DEFERRED_TOOL_GROUPS

#: 端到端测试按正文第一句区分调用方（``ScriptedLLM.MARKERS``，规格 §4.7）。
MARKERS = {
    "orchestrator": "你是 MAA-API 的总控",
    "general": "你是 MAA-API 的通用执行 Worker",
    "researcher": "你是情报员子代理",
    "navigator": "你是导航员子代理",
    "verifier": "你是验证员子代理",
    "titler": "你给工单起标题",
}
#: 注册表里 ``worker`` 组的九个工具：``general`` 只多四个，五个工单工具归总控。
WORKER_TOOLS = {"report", "delegate", "hold_device", "release_device"}
TICKET_TOOLS = {
    "create_ticket",
    "send_to_ticket",
    "list_tickets",
    "get_ticket",
    "cancel_ticket",
}
BUILTIN_SPEC_NAMES = [
    "analyst",
    "copilot",
    "daily_ops",
    "diagnose",
    "general",
    "legacy_chat",
    "legacy_incident",
    "navigator",
    "ops",
    "orchestrator",
    "planner",
    "recon",
    "researcher",
    "summarizer",
    "titler",
    "verifier",
]


def test_m70_specs_load_and_pass_consistency_rules():
    registry = AgentSpecRegistry(build_registry())

    registry.load()

    specs = registry.list()
    assert [spec.name for spec in specs] == BUILTIN_SPEC_NAMES
    orchestrators = [spec for spec in specs if spec.kind == "orchestrator"]
    assert [spec.name for spec in orchestrators] == ["orchestrator"]
    assert orchestrators[0].device == "none"
    # 总控只有一个，且不能委派（子代理名单只属于 Worker）。
    assert orchestrators[0].delegates == ()
    by_name = {spec.name: spec for spec in specs}
    assert by_name["general"].kind == "worker"
    assert by_name["titler"].kind == "background"
    assert {name for name, spec in by_name.items() if spec.kind == "subagent"} == {
        "navigator",
        "recon",
        "researcher",
        "verifier",
    }


def test_general_tools_equal_legacy_chat_minus_delegate_recon_plus_worker_tools():
    general = builtin_spec("general")
    legacy = builtin_spec("legacy_chat")

    expected = (set(legacy.tools) - {"delegate_recon"}) | WORKER_TOOLS
    assert set(general.tools) == expected
    assert "delegate_recon" not in general.tools
    assert TICKET_TOOLS.isdisjoint(general.tools)
    assert set(general.deferred_groups) == set(DEFERRED_TOOL_GROUPS)
    assert general.delegates == ("researcher", "navigator", "verifier", "recon")


def test_subagents_have_schema_and_positive_max_steps():
    specs = builtin_specs()
    subagents = [spec for spec in specs.values() if spec.kind == "subagent"]

    assert {spec.name for spec in subagents} == {
        "navigator",
        "recon",
        "researcher",
        "verifier",
    }
    for spec in subagents:
        assert spec.output_schema
        assert spec.max_steps > 0
        assert get_schema(spec.output_schema) is not None
    assert builtin_spec("recon").output_schema == "subagent_report"
    assert builtin_spec("verifier").output_schema == "verifier_verdict"
    assert builtin_spec("titler").output_schema == "title"

    # 三个 schema 与规格 §4.7.8 的字段和约束一致：全部必填、不许多余字段。
    title = get_schema("title")
    assert title.model_validate({"title": "日常"}).title == "日常"
    with pytest.raises(ValidationError):
        title.model_validate({"title": "字" * 17})
    with pytest.raises(ValidationError):
        title.model_validate({"title": "日常", "extra": 1})

    report = get_schema("subagent_report")
    assert report.model_validate(
        {"summary": "查到了", "findings": [], "uncertainties": [], "artifacts": []}
    ).summary == "查到了"
    finding = {
        "summary": "查到了",
        "findings": [{"title": "掉率", "detail": "样本 200", "evidence": ["企鹅物流·1-7"]}],
        "uncertainties": [],
        "artifacts": [],
    }
    assert report.model_validate(finding).findings[0].title == "掉率"
    with pytest.raises(ValidationError):
        report.model_validate(
            {
                "findings": [{"title": "x", "detail": "y", "evidence": [], "more": 1}],
                "uncertainties": [],
                "artifacts": [],
                "summary": "缺一个字段",
            }
        )

    verdict = get_schema("verifier_verdict")
    assert verdict.model_validate(
        {"passed": True, "confidence": 0.9, "evidence": ["get_pipeline"], "notes": ""}
    ).passed is True
    with pytest.raises(ValidationError):
        verdict.model_validate(
            {"passed": True, "confidence": 1.5, "evidence": [], "notes": ""}
        )


def test_prompt_markers_are_unique():
    specs = builtin_specs()

    for agent, marker in MARKERS.items():
        owners = [name for name, spec in specs.items() if marker in spec.body]
        assert owners == [agent], f"{marker!r} 应该只出现在 {agent} 的正文里"
