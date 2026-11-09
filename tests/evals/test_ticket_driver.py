"""ticket 入口的场景解析、驱动与多会话回放（M86/M87 评测前置补丁）。

T1 是场景 schema 的解析用例：场景级 ``human``、``world.copilots``、
``world.copilot_results`` 与 seed 流水线的 ``agent_session_id``；T2 追加
``CassetteTransport`` 的多会话工具结果回放用例。后续任务在同一文件里追加
``drive_ticket`` 用例。
"""

from __future__ import annotations

import asyncio
import json
from pathlib import Path
from typing import Any

import httpx
import pytest

from maa_api.agent.evals import (
    Cassette,
    CassetteCall,
    CassetteHeader,
    CassetteTransport,
    ScenarioError,
    load_scenario,
)


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


# --------------------------------------------------------------------------- #
# 多会话回放
# --------------------------------------------------------------------------- #


def _cassette_header() -> CassetteHeader:
    return CassetteHeader(
        scenario="ticket-driver",
        source="handwritten",
        recorded_at="2026-11-09T00:00:00Z",
        model="eval-model",
        agent="legacy_chat",
        notes="多会话交错的回放",
    )


def _agent_messages() -> list[dict[str, Any]]:
    return [
        {"role": "system", "content": "你是代理。"},
        {"role": "user", "content": "先委派子代理，再汇报。"},
    ]


def _replay_request(messages: list[dict[str, Any]]) -> httpx.Request:
    return httpx.Request(
        "POST",
        "https://llm.invalid/v1/chat/completions",
        json={"model": "eval-model", "messages": messages, "tools": []},
    )


def _replay(
    messages: list[dict[str, Any]], transport: CassetteTransport
) -> httpx.Response:
    return asyncio.run(transport.handle_async_request(_replay_request(messages)))


def _multi_session_cassette() -> Cassette:
    """Worker 发出 call-a、另一个会话纯文本应答、Worker 再发出 call-b。"""
    cassette = Cassette.new(_cassette_header())
    cassette.append(
        CassetteCall(
            channel="agent",
            reply={
                "tool_calls": [
                    {"id": "call-a", "name": "delegate", "arguments": {}}
                ]
            },
        )
    )
    cassette.append(CassetteCall(channel="agent", reply={"text": "子代理先自己干活。"}))
    cassette.append(
        CassetteCall(
            channel="agent",
            reply={
                "tool_calls": [
                    {"id": "call-b", "name": "report", "arguments": {}}
                ]
            },
        )
    )
    return cassette


def test_cassette_tool_result_from_earlier_reply_passes() -> None:
    transport = CassetteTransport(_multi_session_cassette(), max_calls=5)

    assert _replay(_agent_messages(), transport).status_code == 200
    assert _replay(_agent_messages(), transport).status_code == 200
    with_tool_result = [
        *_agent_messages(),
        {"role": "tool", "tool_call_id": "call-a", "content": "{}"},
    ]
    assert _replay(with_tool_result, transport).status_code == 200
    assert transport.error is None


def test_cassette_unknown_tool_result_is_mismatch() -> None:
    transport = CassetteTransport(_multi_session_cassette(), max_calls=5)

    assert _replay(_agent_messages(), transport).status_code == 200
    assert _replay(_agent_messages(), transport).status_code == 200
    with_unknown_result = [
        *_agent_messages(),
        {"role": "tool", "tool_call_id": "call-x", "content": "{}"},
    ]
    assert _replay(with_unknown_result, transport).status_code == 400
    assert transport.error is not None
    assert transport.error.code == "mismatch"
