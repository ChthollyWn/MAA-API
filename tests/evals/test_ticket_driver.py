"""ticket 入口的场景解析、驱动与多会话回放（M86/M87 评测前置补丁）。

T1 只有场景 schema 的解析用例：场景级 ``human``、``world.copilots``、
``world.copilot_results`` 与 seed 流水线的 ``agent_session_id``。后续任务在同一
文件里追加 ``drive_ticket`` 与 cassette 回放用例。
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from maa_api.agent.evals import ScenarioError, load_scenario


def _write(tmp_path: Path, stem: str, data: dict) -> Path:
    """把场景写成 JSON-in-YAML；JSON 是 YAML 的子集，加载器无需特例。"""
    path = tmp_path / f"{stem}.yaml"
    path.write_text(json.dumps(data, ensure_ascii=False), encoding="utf-8")
    return path


def _ticket(stem: str = "ticket-driver") -> dict:
    """最小 ticket 场景；测试按需补 ``human`` 与 ``world``。"""
    return {
        "id": stem,
        "title": "工单驱动",
        "suite": "capability",
        "agent": "legacy_chat",
        "entry": {
            "kind": "ticket",
            "worker": "general",
            "title": "导航到基建",
            "brief": "导航到基建，到了告诉我。",
            "scope": "在游戏界面里导航到基建",
        },
        "expect": [{"grader": "text_contains", "any_of": ["完成"]}],
    }


def test_scenario_ticket_human_world_round_trip(tmp_path: Path) -> None:
    data = _ticket("ticket-human-world")
    data["human"] = {
        "confirmations": "approve",
        "questions": {"answer": ["仍按完成结束"]},
    }
    data["world"] = {
        "copilots": [
            {
                "id": "cp-eval-1-7",
                "title": "1-7 三星作业",
                "stage_id": "main_01-07",
                "stage_code": "1-7",
            }
        ],
        "copilot_results": ["failed", "three_star"],
        "seed": {
            "pipelines": [
                {
                    "title": "旧流水线",
                    "status": "completed",
                    "agent_session_id": "eval-seed-session",
                    "tasks": [{"type_name": "Fight"}],
                }
            ]
        },
    }

    scenario = load_scenario(_write(tmp_path, "ticket-human-world", data))

    assert scenario.human.confirmations == "approve"
    assert scenario.human.questions.answer == ["仍按完成结束"]
    assert scenario.world.copilots[0]["id"] == "cp-eval-1-7"
    assert scenario.world.copilot_results == ["failed", "three_star"]
    assert scenario.world.seed.pipelines[0].agent_session_id == "eval-seed-session"


def test_copilot_results_require_copilots(tmp_path: Path) -> None:
    data = _ticket("ticket-orphan-results")
    data["world"] = {"copilot_results": ["failed"]}

    path = _write(tmp_path, "ticket-orphan-results", data)
    with pytest.raises(ScenarioError) as excinfo:
        load_scenario(path)

    message = str(excinfo.value)
    assert str(path) in message
    assert "copilot_results" in message
    assert "copilots" in message


def test_copilot_unknown_field_is_rejected(tmp_path: Path) -> None:
    data = _ticket("ticket-copilot-unknown-field")
    data["world"] = {
        "copilots": [{"id": "cp-eval-1-7", "stage_id": "main_01-07", "stars": 3}]
    }

    path = _write(tmp_path, "ticket-copilot-unknown-field", data)
    with pytest.raises(ScenarioError) as excinfo:
        load_scenario(path)

    message = str(excinfo.value)
    assert str(path) in message
    assert "copilots[0]" in message
    assert "stars" in message


def test_copilot_result_value_is_validated(tmp_path: Path) -> None:
    data = _ticket("ticket-copilot-bad-result")
    data["world"] = {
        "copilots": [{"id": "cp-eval-1-7", "stage_id": "main_01-07"}],
        "copilot_results": ["victory"],
    }

    path = _write(tmp_path, "ticket-copilot-bad-result", data)
    with pytest.raises(ScenarioError) as excinfo:
        load_scenario(path)

    message = str(excinfo.value)
    assert str(path) in message
    assert "victory" in message
