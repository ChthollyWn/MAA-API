"""M87 T1：``verify_before_ready`` 字段与三个定义的版本（规格 §4.7、§6）。

字段加在 ``AgentSpecFrontmatter``；``_check_field_rules`` 只允许 ``kind=worker``
设为 true。``daily_ops`` / ``copilot`` 声明闸门，其余 Worker 与总控保持默认 false。
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from maa_api.agent.specs.registry import AgentSpecError, AgentSpecRegistry
from maa_api.agent.specs.schema import AgentSpecFrontmatter
from maa_api.agent.tools import build_registry

_BODY = "你是用于校验 verify_before_ready 的测试代理。\n"


def _minimal_frontmatter() -> dict[str, object]:
    """一份不带 ``verify_before_ready`` 的合法文件头。"""
    return {
        "name": "spec_a",
        "title": "测试代理",
        "kind": "worker",
        "description": "用于校验 verify_before_ready 的测试代理定义。",
        "model_role": "worker",
        "tools": ["get_system_status"],
        "device": "none",
        "risk_ceiling": "none",
        "max_steps": 0,
        "version": 1,
    }


def _subagent_with_flag() -> str:
    """子代理定义写 ``verify_before_ready: true``：只有 worker 能开闸门。"""
    fields = {
        "name": "spec_a",
        "title": "测试代理",
        "kind": "subagent",
        "description": "用于校验 verify_before_ready 的测试子代理。",
        "model_role": "subagent",
        "tools": ["get_screenshot"],
        "device": "read",
        "risk_ceiling": "none",
        "max_steps": 4,
        "output_schema": "subagent_report",
        "verify_before_ready": True,
        "version": 1,
    }
    return "---\n" + json.dumps(fields, ensure_ascii=False) + "\n---\n" + _BODY


def _load_specs():
    registry = AgentSpecRegistry(build_registry())
    registry.load()
    return {spec.name: spec for spec in registry.list()}


def test_verify_flag_defaults_false():
    spec = AgentSpecFrontmatter.model_validate(_minimal_frontmatter())
    assert spec.verify_before_ready is False
    specs = _load_specs()
    assert specs["general"].verify_before_ready is False
    assert specs["planner"].verify_before_ready is False


def test_only_workers_may_verify(tmp_path: Path):
    (tmp_path / "spec_a.md").write_text(_subagent_with_flag(), encoding="utf-8")
    with pytest.raises(AgentSpecError, match="verify_before_ready"):
        AgentSpecRegistry(build_registry(), root=tmp_path).load()


def test_builtin_workers_that_verify():
    specs = _load_specs()
    assert specs["daily_ops"].verify_before_ready is True
    assert specs["copilot"].verify_before_ready is True
    for name in ("diagnose", "ops", "analyst", "general"):
        assert specs[name].verify_before_ready is False
