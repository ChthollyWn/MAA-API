"""内置代理定义（AgentSpec）：加载、校验、版本哈希与提示词逐字一致（M65 T3）。"""

from __future__ import annotations

import hashlib
import json
from importlib import resources
from pathlib import Path
from typing import Any

import pytest

from maa_api.agent import runtime
from maa_api.agent.recon import runner as recon_runner
from maa_api.agent.registry import DEVICE_LEVELS as TOOL_DEVICE_LEVELS
from maa_api.agent.registry import ToolRegistry
from maa_api.agent.specs import schema
from maa_api.agent.specs.registry import (
    BUILTIN_DIRNAME,
    BUILTIN_PACKAGE,
    AgentSpecError,
    AgentSpecRegistry,
    builtin_spec,
    builtin_specs,
    load_builtin_specs,
    parse_spec_file,
)
from maa_api.agent.tools import build_registry
from maa_api.agent.tools.catalog import DEFERRED_TOOL_GROUPS
from maa_api.domain.errors import AppError, ErrorCode

CHAT_SHA = "664c320349ef13cf3fdbc1f1d6969b85eebf7c7ffe980cd656c1b03381b72536"
RECON_SHA = "c321d4483a9824cdd517d8844a37a558c4d3269a2f51151c45d960be214b9bda"
INCIDENT_TOOLS = [
    "get_system_status",
    "get_device_status",
    "get_logs",
    "get_screenshot",
    "get_pipeline",
    "get_queue",
    "list_pipelines",
    "get_drop_stats",
    "read_knowledge",
    "stop_pipeline",
    "reconnect_device",
    "back_to_home",
]
#: M70 的七个 Worker/工单工具：注册表里有，但不属于 v2 会话（§4.5.12）。
M70_TOOLS = frozenset(
    {
        "report",
        "delegate",
        "create_ticket",
        "send_to_ticket",
        "list_tickets",
        "get_ticket",
        "cancel_ticket",
    }
)
#: M71 的两个设备租约工具：注册表里有，但不属于 v2 会话（M71 §4.2）。
M71_TOOLS = frozenset({"hold_device", "release_device"})
#: M75 的三个工作记忆工具：注册表里有，但不属于 v2 会话（M75 §4.7）。
M75_TOOLS = frozenset({"read_artifact", "write_note", "update_plan"})
#: M76 的检索工具：注册表里有，但不属于 v2 会话（M76 §4.7）。
M76_TOOLS = frozenset({"search_knowledge"})
#: M78 的技能工具：注册表里有，v2 会话不加载（M78 §4.2）。
M78_TOOLS = frozenset({"load_skill", "read_skill_file"})
#: M79 的记忆工具：注册表里有，v2 会话不加载（M79 §4.5）。
M79_TOOLS = frozenset({"remember"})
#: M82 的目标工具：注册表里有，v2 会话不加载（M82 §4.2）。
M82_TOOLS = frozenset({"create_goal", "list_goals", "get_goal", "update_goal"})
#: M86 的三个导航工具：注册表里有，v2 会话不加载（M86 §4.3.1）。
M86_TOOLS = frozenset({"where_am_i", "remember_screen", "navigate_to"})
BUILTIN_SPEC_NAMES = [
    "analyst",
    "copilot",
    "daily_ops",
    "diagnose",
    "general",
    "legacy_chat",
    "legacy_incident",
    "memory_curator",
    "navigator",
    "ops",
    "orchestrator",
    "planner",
    "recon",
    "researcher",
    "summarizer",
    "titler",
    "trigger_evaluator",
    "verifier",
    "watchdog",
]

BUILTIN_DIR = resources.files(BUILTIN_PACKAGE).joinpath(BUILTIN_DIRNAME)


def test_builtin_specs_load_nineteen_definitions_sorted_by_name():
    specs = builtin_specs()

    assert list(specs) == BUILTIN_SPEC_NAMES
    assert {spec.kind for spec in specs.values()} == {
        "background",
        "orchestrator",
        "subagent",
        "worker",
    }


def test_spec_version_is_the_first_12_hex_of_the_file_sha256():
    for name, spec in builtin_specs().items():
        data = BUILTIN_DIR.joinpath(f"{name}.md").read_bytes()

        assert spec.spec_version == hashlib.sha256(data).hexdigest()[:12]
        assert spec.source_file == f"{name}.md"
        assert spec.name == name


def test_legacy_chat_body_is_byte_identical_to_the_v2_system_prompt():
    spec = builtin_spec("legacy_chat")

    assert hashlib.sha256(spec.body.encode("utf-8")).hexdigest() == CHAT_SHA
    assert runtime.SYSTEM_PROMPT is spec.body


def test_recon_body_is_byte_identical_to_the_v2_recon_prompt():
    spec = builtin_spec("recon")

    assert hashlib.sha256(spec.body.encode("utf-8")).hexdigest() == RECON_SHA
    assert recon_runner.SYSTEM_PROMPT is spec.body


def test_legacy_incident_shares_the_prompt_and_lists_the_twelve_recovery_tools():
    incident = builtin_spec("legacy_incident")
    chat = builtin_spec("legacy_chat")

    assert incident.body == chat.body
    assert incident.tools == tuple(INCIDENT_TOOLS)
    assert runtime.INCIDENT_ALLOWED_TOOLS == frozenset(INCIDENT_TOOLS)


def test_legacy_chat_lists_every_v2_registered_tool_in_registration_order():
    spec = builtin_spec("legacy_chat")
    definitions = build_registry().list()

    assert len(definitions) == 107
    assert spec.tools == tuple(
        definition.name
        for definition in definitions
        if definition.name
        not in M70_TOOLS
        | M71_TOOLS
        | M75_TOOLS
        | M76_TOOLS
        | M78_TOOLS
        | M79_TOOLS
        | M82_TOOLS
        | M86_TOOLS
    )
    assert spec.deferred_groups == tuple(sorted(DEFERRED_TOOL_GROUPS))
    assert set(DEFERRED_TOOL_GROUPS) <= set(spec.deferred_groups)


def test_registry_load_validates_tools_against_the_tool_registry():
    full_registry = build_registry()
    registry = AgentSpecRegistry(full_registry)

    registry.load()

    assert [spec.name for spec in registry.list()] == BUILTIN_SPEC_NAMES
    assert [spec.name for spec in registry.list()] == sorted(spec.name for spec in registry.list())

    missing = ToolRegistry()
    for definition in full_registry.list():
        if definition.name != "read_knowledge":
            missing.register(definition)
    with pytest.raises(AgentSpecError) as excinfo:
        AgentSpecRegistry(missing).load()
    assert "read_knowledge" in str(excinfo.value)


def test_registry_get_unknown_name_raises_agent_spec_not_found():
    registry = AgentSpecRegistry(build_registry())
    registry.load()

    with pytest.raises(AppError) as excinfo:
        registry.get("nope")

    assert excinfo.value.code is ErrorCode.AGENT_SPEC_NOT_FOUND
    assert excinfo.value.http_status == 404
    assert "没有名为 nope 的代理定义" in str(excinfo.value)


# ----------------------------------------------------------------------
# 坏定义参数化：逐条覆盖规格 §4.2.15 的规则 1–17
# ----------------------------------------------------------------------

_BODY = "你是用于校验规则的测试代理。\n"


def _definition(
    name: str = "spec_a", *, _drop: tuple[str, ...] = (), **overrides: Any
) -> str:
    """构造一份合法定义；``overrides`` 覆盖字段，``_drop`` 删掉必填字段。"""
    fields: dict[str, Any] = {
        "name": name,
        "title": "测试代理",
        "kind": "worker",
        "description": "用于校验规则的测试代理定义。",
        "model_role": "worker",
        "tools": ["get_system_status"],
        "deferred_groups": [],
        "device": "none",
        "risk_ceiling": "none",
        "max_steps": 0,
        "delegates": [],
        "skills": [],
        "output_schema": None,
        "budget": {"max_cost_per_run": 0},
        "version": 1,
    }
    fields.update(overrides)
    for field in _drop:
        fields.pop(field, None)
    return "---\n" + json.dumps(fields, ensure_ascii=False) + "\n---\n" + _BODY


def _write(root: Path, data: str | bytes, name: str = "spec_a") -> None:
    path = root / f"{name}.md"
    if isinstance(data, bytes):
        path.write_bytes(data)
    else:
        path.write_text(data, encoding="utf-8")


def _case_invalid_utf8(root: Path) -> None:
    _write(root, b"---\nname: spec_a\n---\n\xff")


def _case_missing_frontmatter(root: Path) -> None:
    _write(root, "name: spec_a\nkind: worker\n")


def _case_invalid_yaml(root: Path) -> None:
    _write(root, "---\nname: [spec_a\n---\n" + _BODY)


def _case_frontmatter_not_mapping(root: Path) -> None:
    _write(root, "---\n- spec_a\n- worker\n---\n" + _BODY)


def _case_unknown_field(root: Path) -> None:
    _write(root, _definition(bogus=1))


def _case_missing_required_field(root: Path) -> None:
    _write(root, _definition(_drop=("kind",)))


def _case_body_in_frontmatter(root: Path) -> None:
    _write(root, _definition(body="正文不能写进 frontmatter"))


def _case_bad_name_pattern(root: Path) -> None:
    _write(root, _definition(name="Bad"))


def _case_name_mismatch(root: Path) -> None:
    _write(root, _definition(name="spec_b"))


def _case_title_too_long(root: Path) -> None:
    _write(root, _definition(title="九个字的标题超长了"))


def _case_description_too_long(root: Path) -> None:
    _write(root, _definition(description="描" * 121))


def _case_bad_kind(root: Path) -> None:
    _write(root, _definition(kind="boss"))


def _case_bad_model_role(root: Path) -> None:
    _write(root, _definition(model_role="boss"))


def _case_bad_device(root: Path) -> None:
    _write(root, _definition(device="write"))


def _case_bad_risk_ceiling(root: Path) -> None:
    _write(root, _definition(risk_ceiling="all"))


def _case_negative_max_steps(root: Path) -> None:
    _write(root, _definition(max_steps=-1))


def _case_subagent_zero_steps(root: Path) -> None:
    _write(root, _definition(kind="subagent", max_steps=0, output_schema="report"))


def _case_bool_max_steps(root: Path) -> None:
    _write(root, _definition(max_steps=True))


def _case_version_zero(root: Path) -> None:
    _write(root, _definition(version=0))


def _case_negative_budget(root: Path) -> None:
    _write(root, _definition(budget={"max_cost_per_run": -1}))


def _case_duplicate_tools(root: Path) -> None:
    _write(root, _definition(tools=["get_system_status", "get_system_status"]))


def _case_duplicate_delegates(root: Path) -> None:
    _write(root, _definition(delegates=["spec_b", "spec_b"]))


def _case_unknown_deferred_group(root: Path) -> None:
    _write(root, _definition(deferred_groups=["nonexistent"]))


def _case_deferred_group_not_allowed(root: Path) -> None:
    _write(root, _definition(deferred_groups=["account"]))


def _case_delegates_on_subagent(root: Path) -> None:
    _write(
        root,
        _definition(kind="subagent", max_steps=1, output_schema="report", delegates=["spec_b"]),
    )


def _case_subagent_without_output_schema(root: Path) -> None:
    _write(root, _definition(kind="subagent", max_steps=1, output_schema=None))


def _case_orchestrator_with_device(root: Path) -> None:
    _write(root, _definition(kind="orchestrator", device="read"))


def _case_empty_body(root: Path) -> None:
    _write(root, _definition()[: -len(_BODY)] + "   \n")


def _case_two_orchestrators(root: Path) -> None:
    _write(root, _definition(name="spec_a", kind="orchestrator", device="none"), "spec_a")
    _write(root, _definition(name="spec_b", kind="orchestrator", device="none"), "spec_b")


def _case_delegate_missing(root: Path) -> None:
    _write(root, _definition(delegates=["ghost"]))


def _case_delegate_is_not_subagent(root: Path) -> None:
    _write(root, _definition(name="spec_a", delegates=["spec_b"]), "spec_a")
    _write(root, _definition(name="spec_b"), "spec_b")


def _case_unknown_tool(root: Path) -> None:
    _write(root, _definition(tools=["not_a_tool"]))


def _case_tool_device_too_high(root: Path) -> None:
    _write(root, _definition(device="read", tools=["click"]))


@pytest.mark.parametrize(
    ("case", "use_tool_registry"),
    [
        pytest.param(_case_invalid_utf8, False, id="1-not-utf8"),
        pytest.param(_case_missing_frontmatter, False, id="1-no-frontmatter"),
        pytest.param(_case_invalid_yaml, False, id="1-invalid-yaml"),
        pytest.param(_case_frontmatter_not_mapping, False, id="1-not-a-mapping"),
        pytest.param(_case_unknown_field, False, id="2-unknown-field"),
        pytest.param(_case_missing_required_field, False, id="2-missing-required-field"),
        pytest.param(_case_body_in_frontmatter, False, id="2-body-in-frontmatter"),
        pytest.param(_case_bad_name_pattern, False, id="3-name-pattern"),
        pytest.param(_case_name_mismatch, False, id="3-name-vs-file"),
        pytest.param(_case_title_too_long, False, id="4-title-too-long"),
        pytest.param(_case_description_too_long, False, id="4-description-too-long"),
        pytest.param(_case_bad_kind, False, id="5-bad-kind"),
        pytest.param(_case_bad_model_role, False, id="5-bad-model-role"),
        pytest.param(_case_bad_device, False, id="5-bad-device"),
        pytest.param(_case_bad_risk_ceiling, False, id="5-bad-risk-ceiling"),
        pytest.param(_case_negative_max_steps, False, id="6-negative-max-steps"),
        pytest.param(_case_subagent_zero_steps, False, id="6-subagent-zero-steps"),
        pytest.param(_case_bool_max_steps, False, id="6-bool-max-steps"),
        pytest.param(_case_version_zero, False, id="7-version-zero"),
        pytest.param(_case_negative_budget, False, id="7-negative-budget"),
        pytest.param(_case_duplicate_tools, False, id="8-duplicate-tools"),
        pytest.param(_case_duplicate_delegates, False, id="8-duplicate-delegates"),
        pytest.param(_case_unknown_deferred_group, False, id="9-unknown-deferred-group"),
        pytest.param(_case_deferred_group_not_allowed, False, id="9-deferred-group-not-allowed"),
        pytest.param(_case_delegates_on_subagent, False, id="10-delegates-on-subagent"),
        pytest.param(_case_subagent_without_output_schema, False, id="11-subagent-output-schema"),
        pytest.param(_case_orchestrator_with_device, False, id="12-orchestrator-device"),
        pytest.param(_case_empty_body, False, id="13-empty-body"),
        pytest.param(_case_two_orchestrators, False, id="14-two-orchestrators"),
        pytest.param(_case_delegate_missing, False, id="15-delegate-missing"),
        pytest.param(_case_delegate_is_not_subagent, False, id="15-delegate-not-subagent"),
        pytest.param(_case_unknown_tool, True, id="16-unknown-tool"),
        pytest.param(_case_tool_device_too_high, True, id="17-tool-device-too-high"),
    ],
)
def test_invalid_definition_fails_loudly(
    tmp_path: Path, case: Any, use_tool_registry: bool
) -> None:
    # 先确认夹具本身合法，坏文件才是唯一的失败来源。
    parse_spec_file("spec_a.md", _definition().encode("utf-8"))

    case(tmp_path)

    with pytest.raises(AgentSpecError):
        if use_tool_registry:
            AgentSpecRegistry(build_registry(), root=tmp_path).load()
        else:
            load_builtin_specs(tmp_path)


def test_device_levels_are_the_same_in_both_modules():
    assert schema.DEVICE_LEVELS == TOOL_DEVICE_LEVELS
