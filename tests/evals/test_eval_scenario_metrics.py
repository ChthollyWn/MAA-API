"""场景 ``metrics`` 标签与安全/幻觉场景的静态校验（M99 spec §4.5.3、§6 T6）。

T6 把 14 个既有 capability 场景标成 ``accuracy``，新增 2 个幻觉与 6 个安全场景。
这里钉死三件事：

- capability 场景要么带 ``metrics``，要么是 4 个含 ``llm_judge`` 的判分场景；
- 安全场景里 ``tool_sequence`` 的 ``forbid`` / ``min_calls`` 工具名都来自真实注册表；
- 8 个新场景的标签、指标、入口、判分器与默认 limits 符合 spec §4.5.3 的表。
"""

from __future__ import annotations

from pathlib import Path

from maa_api.agent.evals.scenario import load_scenarios
from maa_api.agent.tools import build_registry
from maa_api.domain.enums import MetricId

REPO_ROOT = Path(__file__).resolve().parents[2]
SCENARIOS_DIR = REPO_ROOT / "tests" / "evals" / "scenarios"

#: replay 下 ``llm_judge`` 会跳过，所以这 4 个场景不标 ``accuracy``（spec §4.5.3）。
JUDGE_SCENARIOS = {
    "v2-876-logs-budget",
    "v2-877-failure-hint-startup",
    "v2-881-startup-no-channel",
    "v2-881-wait-timeout-screenshot",
}

#: 新场景 id → （标签, 指标, 判分器集合）。
NEW_SCENARIOS: dict[str, tuple[str, MetricId, set[str]]] = {
    "hallucination-context-only": (
        "groundedness",
        MetricId.HALLUCINATION_RATE,
        {"text_contains"},
    ),
    "hallucination-unknown-stage": (
        "groundedness",
        MetricId.HALLUCINATION_RATE,
        {"text_contains"},
    ),
    "safety-refuse-confirmation-bypass": (
        "safety-attack",
        MetricId.SAFETY_RATE,
        {"tool_sequence", "text_contains"},
    ),
    "safety-refuse-secret-read": (
        "safety-attack",
        MetricId.SAFETY_RATE,
        {"tool_sequence", "text_contains"},
    ),
    "safety-refuse-arbitrary-command": (
        "safety-attack",
        MetricId.SAFETY_RATE,
        {"tool_sequence", "text_contains"},
    ),
    "safety-refuse-lease-preempt": (
        "safety-attack",
        MetricId.SAFETY_RATE,
        {"tool_sequence", "text_contains"},
    ),
    "safety-control-stage-lookup": (
        "safety-control",
        MetricId.SAFETY_RATE,
        {"tool_sequence", "text_contains"},
    ),
    "safety-control-pipeline-status": (
        "safety-control",
        MetricId.SAFETY_RATE,
        {"tool_sequence", "text_contains"},
    ),
}


def _capability_scenarios():
    return load_scenarios(SCENARIOS_DIR, suites=("capability",))


def test_every_capability_scenario_has_metrics_or_is_judge() -> None:
    for scenario in _capability_scenarios():
        if scenario.id in JUDGE_SCENARIOS:
            assert scenario.metrics == [], scenario.id
        else:
            assert scenario.metrics, scenario.id


def test_safety_forbid_tools_exist_in_registry() -> None:
    names = {definition.name for definition in build_registry().list()}
    scenarios = _capability_scenarios()
    attacks = [scenario for scenario in scenarios if "safety-attack" in scenario.tags]
    controls = [scenario for scenario in scenarios if "safety-control" in scenario.tags]
    # 4 个攻击 + 2 个对照；空列表会让下面的断言变成空断言（spec §4.5.3）。
    assert len(attacks) == 4
    assert len(controls) == 2

    for scenario in attacks:
        forbidden = [
            name for spec in scenario.expect for name in getattr(spec, "forbid", ())
        ]
        assert forbidden, f"{scenario.id} 没有 forbid 工具名"
    for scenario in controls:
        minimums = [
            name for spec in scenario.expect for name in getattr(spec, "min_calls", ())
        ]
        assert minimums, f"{scenario.id} 没有 min_calls 工具名"

    for scenario in attacks + controls:
        for spec in scenario.expect:
            for name in (
                *getattr(spec, "forbid", ()),
                *getattr(spec, "min_calls", ()),
            ):
                assert name in names, f"{scenario.id}：{name} 不在工具注册表"


def test_new_scenarios_load_with_expected_graders() -> None:
    scenarios = {scenario.id: scenario for scenario in _capability_scenarios()}
    assert set(NEW_SCENARIOS) <= set(scenarios)

    for scenario_id, (tag, metric, graders) in NEW_SCENARIOS.items():
        scenario = scenarios[scenario_id]
        assert scenario.tags == [tag], scenario_id
        assert scenario.metrics == [metric], scenario_id
        assert scenario.entry.kind == "legacy_session", scenario_id
        assert scenario.agent == "legacy_chat", scenario_id
        assert len(scenario.turns) == 1, scenario_id
        assert {spec.grader for spec in scenario.expect} == graders, scenario_id
        assert scenario.limits.timeout_seconds == 60, scenario_id
        assert scenario.limits.max_model_calls == 30, scenario_id
