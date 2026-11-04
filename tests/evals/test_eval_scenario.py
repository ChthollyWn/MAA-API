"""场景 schema 与加载（M68 规格 §4.2.6、10 §6.4）。

覆盖：全部字段往返、id 与文件名一致、设置键白名单、regression 禁用 llm_judge、
ticket 入口必填项、``json`` 与 ``body_file`` 二选一；错误消息都带文件路径。
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

import pytest
from ruamel.yaml import YAML

from maa_api.agent.evals import (
    AnswerSpec,
    HttpFixture,
    LlmJudgeSpec,
    OutcomeStateSpec,
    Scenario,
    ScenarioError,
    TextContainsSpec,
    ToolSequenceSpec,
    load_scenario,
)


def _write(path: Path, data: dict[str, Any]) -> Path:
    yaml = YAML()
    with path.open("w", encoding="utf-8") as handle:
        yaml.dump(data, handle)
    return path


def _minimal(stem: str = "basic-case") -> dict[str, Any]:
    return {
        "id": stem,
        "title": "基本场景",
        "suite": "regression",
        "agent": "legacy_chat",
        "entry": {"kind": "legacy_session"},
        "turns": [{"user": "把日常做了"}],
        "expect": [{"grader": "text_contains", "any_of": ["完成"]}],
    }


def _load(tmp_path: Path, stem: str, data: dict[str, Any]) -> Scenario:
    return load_scenario(_write(tmp_path / f"{stem}.yaml", data))


def test_all_fields_round_trip(tmp_path: Path) -> None:
    data = {
        "id": "capability-all-fields",
        "title": "所有字段",
        "suite": "capability",
        "agent": "legacy_chat",
        "source": "docs/v2/13 §8.81",
        "tags": ["legacy", "pipeline"],
        "entry": {
            "kind": "ticket",
            "worker": "worker",
            "title": "工单标题",
            "brief": "任务说明",
            "scope": "委托范围",
        },
        "settings": {
            "agent.question_timeout_seconds": 3,
            "agent.confirmation_timeout_seconds": 4,
            "llm.base_url": "https://llm.invalid/v1",
        },
        "world": {
            "core_script": "failure",
            "device_connected": False,
            "screen": "desktop",
            "http": [
                {"match": "https://example.invalid/api", "status": 201, "json": {"ok": True}}
            ],
            "seed": {
                "pipelines": [
                    {
                        "title": "刷 1-7",
                        "status": "failed",
                        "minutes_ago": 3,
                        "error_code": "CORE_COMMAND_FAILED",
                        "error_message": "识别失败",
                        "tasks": [
                            {
                                "type_name": "Fight",
                                "status": "skipped",
                                "params": {"stage": "1-7"},
                                "error_code": "CORE_COMMAND_FAILED",
                                "error_message": "没找到按钮",
                            }
                        ],
                        "logs": [
                            {"task": 0, "level": "warning", "content": "识别失败"}
                        ],
                    }
                ]
            },
        },
        "turns": [
            {
                "user": "先看下状态",
                "human": {
                    "confirmations": "approve",
                    "questions": {"answer": ["1-7", "CE-6"]},
                    "interrupt": {
                        "on": "tool_start",
                        "tool": "wait_pipeline",
                        "delay_seconds": 1.5,
                    },
                },
            }
        ],
        "expect": [
            {
                "grader": "tool_sequence",
                "match": "exact",
                "steps": [
                    {
                        "tool": "wait_pipeline",
                        "args": {"timeout_seconds": 60},
                        "status": "ok",
                        "next": True,
                    }
                ],
                "forbid": ["stop_pipeline"],
                "max_calls": {"wait_pipeline": 2},
                "min_calls": {"submit_pipeline": 1},
            },
            {
                "grader": "outcome_state",
                "pipelines": {"count": 1},
                "model_calls": 2,
            },
            {
                "grader": "text_contains",
                "target": "all",
                "all_of": ["完成"],
                "any_of": ["好"],
                "none_of": ["失败"],
                "ignore_case": False,
            },
            {
                "grader": "llm_judge",
                "rubric": "回答点出原因并给出下一步",
                "pass_score": 0.8,
            },
        ],
        "cassette": "custom.jsonl",
        "limits": {"timeout_seconds": 120, "max_model_calls": 5},
    }

    scenario = _load(tmp_path, "capability-all-fields", data)

    assert scenario.id == "capability-all-fields"
    assert scenario.title == "所有字段"
    assert scenario.suite == "capability"
    assert scenario.source == "docs/v2/13 §8.81"
    assert scenario.tags == ["legacy", "pipeline"]
    assert scenario.entry.kind == "ticket"
    assert scenario.entry.worker == "worker"
    assert scenario.entry.brief == "任务说明"
    assert scenario.entry.scope == "委托范围"
    assert scenario.settings["agent.question_timeout_seconds"] == 3
    assert scenario.world.core_script == "failure"
    assert scenario.world.device_connected is False
    assert scenario.world.screen == "desktop"
    assert scenario.world.http[0].status == 201
    assert scenario.world.http[0].json_body == {"ok": True}
    pipeline = scenario.world.seed.pipelines[0]
    assert pipeline.status == "failed"
    assert pipeline.tasks[0].params == {"stage": "1-7"}
    assert pipeline.logs[0].level == "warning"
    human = scenario.turns[0].human
    assert human.confirmations == "approve"
    assert isinstance(human.questions, AnswerSpec)
    assert human.questions.answer == ["1-7", "CE-6"]
    assert human.interrupt is not None
    assert human.interrupt.on == "tool_start"
    assert human.interrupt.delay_seconds == 1.5
    assert isinstance(scenario.expect[0], ToolSequenceSpec)
    assert scenario.expect[0].steps[0].next is True
    assert scenario.expect[0].max_calls == {"wait_pipeline": 2}
    assert isinstance(scenario.expect[1], OutcomeStateSpec)
    assert isinstance(scenario.expect[2], TextContainsSpec)
    assert scenario.expect[2].ignore_case is False
    assert isinstance(scenario.expect[3], LlmJudgeSpec)
    assert scenario.expect[3].pass_score == 0.8
    assert scenario.cassette_name() == "custom.jsonl"
    assert scenario.limits.timeout_seconds == 120
    assert scenario.limits.max_model_calls == 5


def test_id_must_equal_file_stem(tmp_path: Path) -> None:
    data = _minimal()
    data["id"] = "other-id"
    path = _write(tmp_path / "right-id.yaml", data)
    with pytest.raises(ScenarioError) as excinfo:
        load_scenario(path)
    message = str(excinfo.value)
    assert str(path) in message
    assert "right-id" in message
    assert "other-id" in message


def test_unknown_setting_key_is_rejected(tmp_path: Path) -> None:
    data = _minimal()
    data["settings"] = {"agent.unknown_knob": True}
    path = _write(tmp_path / "basic-case.yaml", data)
    with pytest.raises(ScenarioError) as excinfo:
        load_scenario(path)
    message = str(excinfo.value)
    assert str(path) in message
    assert "agent.unknown_knob" in message
    assert "settings" in message


def test_regression_cannot_use_llm_judge(tmp_path: Path) -> None:
    data = _minimal()
    data["expect"] = [{"grader": "llm_judge", "rubric": "回答正确"}]
    path = _write(tmp_path / "basic-case.yaml", data)
    with pytest.raises(ScenarioError) as excinfo:
        load_scenario(path)
    message = str(excinfo.value)
    assert str(path) in message
    assert "regression" in message
    assert "llm_judge" in message


def test_ticket_entry_requires_brief_and_scope(tmp_path: Path) -> None:
    data = _minimal()
    data.pop("turns")
    data["entry"] = {"kind": "ticket", "worker": "worker", "title": "工单"}
    path = _write(tmp_path / "basic-case.yaml", data)
    with pytest.raises(ScenarioError) as excinfo:
        load_scenario(path)
    message = str(excinfo.value)
    assert str(path) in message
    assert "brief" in message
    assert "scope" in message

    data["entry"]["brief"] = "任务说明"
    data["entry"]["scope"] = "委托范围"
    scenario = _load(tmp_path, "ticket-case", {**data, "id": "ticket-case"})
    assert scenario.entry.kind == "ticket"
    assert scenario.turns == []


def test_http_fixture_needs_exactly_one_body(tmp_path: Path) -> None:
    both = _minimal()
    both["world"] = {
        "http": [
            {
                "match": "https://example.invalid",
                "json": {"ok": True},
                "body_file": "body.json",
            }
        ]
    }
    path = _write(tmp_path / "both-bodies.yaml", {**both, "id": "both-bodies"})
    with pytest.raises(ScenarioError) as excinfo:
        load_scenario(path)
    assert str(path) in str(excinfo.value)
    assert "json 与 body_file 必须恰好提供一个" in str(excinfo.value)

    neither = _minimal()
    neither["world"] = {"http": [{"match": "https://example.invalid"}]}
    path = _write(tmp_path / "no-body.yaml", {**neither, "id": "no-body"})
    with pytest.raises(ScenarioError) as excinfo:
        load_scenario(path)
    assert str(path) in str(excinfo.value)

    fixture = HttpFixture.model_validate(
        {"match": "https://example.invalid", "body_file": "body.json"}
    )
    assert fixture.body_file == "body.json"
    assert fixture.json_body is None
