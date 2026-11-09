"""初始场景集的回归闸门（M68 spec §6 test_regression_replay.py）。

默认跑法是 ``scripts/agent_eval.py --suite regression --mode replay``；这里把同一件事
放进 pytest，保证以后改 harness 时 regression 套件不会悄悄退化。
"""

from __future__ import annotations

from pathlib import Path

import pytest

from maa_api.agent.evals.runner import EvalOptions, EvalRunner
from maa_api.agent.evals.scenario import load_scenarios
from tests.evals.environment import FakeWorldEnvironment

REPO_ROOT = Path(__file__).resolve().parents[2]
SCENARIOS_DIR = REPO_ROOT / "tests" / "evals" / "scenarios"
CASSETTES_DIR = REPO_ROOT / "tests" / "evals" / "cassettes"
FIXTURES_DIR = REPO_ROOT / "tests" / "evals" / "fixtures"


def _environment() -> FakeWorldEnvironment:
    return FakeWorldEnvironment(repo_root=REPO_ROOT, fixtures_dir=FIXTURES_DIR)


def _options(tmp_path: Path) -> EvalOptions:
    return EvalOptions(mode="replay", cassette_dir=CASSETTES_DIR, out_dir=tmp_path)


def test_every_scenario_loads_and_has_a_cassette() -> None:
    scenarios = load_scenarios(SCENARIOS_DIR)
    assert len(scenarios) == 23
    ids = {scenario.id for scenario in scenarios}
    assert len(ids) == 23
    for scenario in scenarios:
        path = CASSETTES_DIR / scenario.cassette_name()
        assert path.is_file(), f"{scenario.id} 缺少录制文件 {path}"


def test_every_scenario_has_a_driver() -> None:
    from maa_api.agent.evals.runner import DRIVERS

    for scenario in load_scenarios(SCENARIOS_DIR):
        assert scenario.entry.kind in DRIVERS
    assert "ticket" in DRIVERS


@pytest.mark.parametrize(
    "scenario",
    load_scenarios(SCENARIOS_DIR, suites=["regression"]),
    ids=lambda scenario: scenario.id,
)
def test_regression_scenario_passes(scenario, tmp_path: Path) -> None:
    result = EvalRunner(_environment(), _options(tmp_path)).run(
        [scenario], suite_label="regression", filters={"scenario": [scenario.id]}
    )
    assert len(result.cases) == 1
    case = result.cases[0]
    assert case.status == "passed", case.error or case.graders
