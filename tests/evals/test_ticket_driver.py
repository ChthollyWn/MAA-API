"""ticket 入口的场景解析、驱动与多会话回放（M86/M87 评测前置补丁）。

T1 是场景 schema 的解析用例：场景级 ``human``、``world.copilots``、
``world.copilot_results`` 与 seed 流水线的 ``agent_session_id``；T2 追加
``CassetteTransport`` 的多会话工具结果回放用例；T3 追加 ``drive_ticket`` 的
建单、人工策略、子代理会话与失败分类用例。
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
    load_scenarios,
)
from maa_api.agent.evals.runner import (
    DRIVERS,
    CaseResult,
    EvalOptions,
    EvalRunner,
    TicketNotOpened,
    TicketRunTimeout,
    drive_ticket,
)
from tests.evals.environment import FakeWorldEnvironment


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


# --------------------------------------------------------------------------- #
# ticket 驱动
# --------------------------------------------------------------------------- #

REPO_ROOT = Path(__file__).resolve().parents[2]

#: 动态上下文落点探查的应答；每条 ticket cassette 的前两条固定是它。
_PROBE_OK = {"text": "OK"}
#: 工单以 ``ready`` 结束会触发记忆抽取，需要在 cassette 末尾留一条应答。
_MEMORY_REPLY = {"text": json.dumps({"items": []}, ensure_ascii=False)}


def _tool(
    call_id: str, name: str, arguments: dict[str, Any] | None = None
) -> dict[str, Any]:
    """构造一条带工具调用的 agent 应答。"""
    return {
        "tool_calls": [{"id": call_id, "name": name, "arguments": arguments or {}}],
        "finish_reason": "tool_calls",
    }


def _ticket_case(stem: str, **overrides: Any) -> dict[str, Any]:
    """在最小 ticket 场景上覆盖字段；入口固定 general。"""
    data = _ticket(stem)
    data["agent"] = "general"
    data.update(overrides)
    return data


def _write_scenario(root: Path, data: dict[str, Any]) -> Path:
    """把场景写进 ``root/scenarios/<id>.yaml``。"""
    scenarios = root / "scenarios"
    scenarios.mkdir(parents=True, exist_ok=True)
    path = scenarios / f"{data['id']}.yaml"
    path.write_text(json.dumps(data, ensure_ascii=False), encoding="utf-8")
    return path


def _write_cassette(root: Path, scenario_id: str, calls: list[dict[str, Any]]) -> Path:
    """写 agent 通道录制；公共前缀是两条落点探查的 ``OK``。"""
    cassettes = root / "cassettes"
    cassettes.mkdir(parents=True, exist_ok=True)
    header = {
        "type": "header",
        "format": 1,
        "scenario": scenario_id,
        "source": "handwritten",
        "recorded_at": "2026-11-09T00:00:00Z",
        "model": "eval-model",
        "agent": "general",
        "spec_version": None,
        "notes": "ticket 驱动用例",
    }
    lines = [json.dumps(header, ensure_ascii=False)]
    for reply in (_PROBE_OK, _PROBE_OK, *calls):
        lines.append(
            json.dumps(
                {"type": "call", "channel": "agent", "reply": reply},
                ensure_ascii=False,
            )
        )
    path = cassettes / f"{scenario_id}.jsonl"
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")
    return path


def _run(
    scenario_data: dict[str, Any],
    calls: list[dict[str, Any]],
    tmp_path: Path,
    *,
    recorder_out: dict[str, list[str]] | None = None,
) -> CaseResult:
    """跑一个 ticket 场景；``recorder_out`` 收驱动写下的会话 id（契约断言用）。"""
    _write_scenario(tmp_path, scenario_data)
    _write_cassette(tmp_path, scenario_data["id"], calls)
    fixtures = tmp_path / "fixtures"
    fixtures.mkdir(parents=True, exist_ok=True)
    options = EvalOptions(
        mode="replay",
        cassette_dir=tmp_path / "cassettes",
        out_dir=tmp_path / "out",
    )
    real_driver = DRIVERS["ticket"]
    if recorder_out is not None:

        async def capture(eval_app, scenario, recorder):
            await real_driver(eval_app, scenario, recorder)
            recorder_out["session_ids"] = list(recorder.session_ids)

        DRIVERS["ticket"] = capture
    try:
        result = EvalRunner(
            FakeWorldEnvironment(repo_root=REPO_ROOT, fixtures_dir=fixtures), options
        ).run(
            load_scenarios(tmp_path / "scenarios"),
            suite_label="capability",
            filters={},
        )
    finally:
        DRIVERS["ticket"] = real_driver
    assert len(result.cases) == 1
    return result.cases[0]


def test_ticket_driver_is_registered() -> None:
    assert DRIVERS["ticket"] is drive_ticket
    assert issubclass(TicketNotOpened, RuntimeError)
    assert issubclass(TicketRunTimeout, RuntimeError)


def test_ticket_driver_runs_general_ticket_to_report(tmp_path: Path) -> None:
    data = _ticket_case(
        "ticket-general-report",
        expect=[
            {"grader": "tool_sequence", "steps": [{"tool": "report"}]},
            {
                "grader": "outcome_state",
                "runs": {
                    "count": {"gte": 1},
                    "all": [
                        {
                            "agent_name": "general",
                            "finish_reason": "report_submitted",
                        }
                    ],
                },
            },
        ],
    )
    recorder: dict[str, list[str]] = {}

    case = _run(
        data,
        [
            _tool("call-report-1", "report", {"status": "ready", "summary": "报告完成"}),
            _MEMORY_REPLY,
        ],
        tmp_path,
        recorder_out=recorder,
    )

    assert case.status == "passed", case.error or case.graders
    assert recorder["session_ids"], "驱动没有记下 Worker 会话"
    worker_session = recorder["session_ids"][0]
    report_messages = [
        message
        for message in case.detail["messages"]
        if message["session_id"] == worker_session
        and message["role"] == "assistant"
        and any(
            call["function"]["name"] == "report"
            for call in message.get("tool_calls") or []
        )
    ]
    assert report_messages, "Worker 会话里没有 report 调用"


def test_ticket_driver_approves_confirmation(tmp_path: Path) -> None:
    data = _ticket_case(
        "ticket-approve-confirmation",
        human={"confirmations": "approve"},
        expect=[
            {
                "grader": "tool_sequence",
                "steps": [
                    {"tool": "submit_pipeline", "status": "ok"},
                    {"tool": "report"},
                ],
            },
            {
                "grader": "outcome_state",
                "confirmations": {"count": 1, "statuses": ["approved"]},
                "pipelines": {"count": 1},
            },
        ],
    )

    case = _run(
        data,
        [
            _tool(
                "call-submit-1",
                "submit_pipeline",
                {
                    "title": "刷 1-7",
                    "tasks": [
                        {"name": "StartUp", "start_game_enabled": True},
                        {"name": "Fight", "stage": "1-7", "medicine": 1},
                    ],
                },
            ),
            _tool("call-report-1", "report", {"status": "ready", "summary": "刷完了"}),
            _MEMORY_REPLY,
        ],
        tmp_path,
    )

    assert case.status == "passed", case.error or case.graders
    submits = [
        call for call in case.detail["tool_calls"] if call["name"] == "submit_pipeline"
    ]
    assert submits and submits[0]["status"] == "ok"


def test_ticket_driver_answers_question(tmp_path: Path) -> None:
    data = _ticket_case(
        "ticket-answer-question",
        human={"questions": {"answer": ["仍按完成结束"]}},
        expect=[
            {
                "grader": "tool_sequence",
                "steps": [{"tool": "ask_user", "status": "ok"}, {"tool": "report"}],
            },
            {
                "grader": "outcome_state",
                "questions": {"count": 1, "statuses": ["answered"]},
                "runs": {
                    "count": {"gte": 1},
                    "all": [
                        {
                            "agent_name": "general",
                            "finish_reason": "report_submitted",
                        }
                    ],
                },
            },
        ],
    )

    case = _run(
        data,
        [
            _tool(
                "call-ask-1",
                "ask_user",
                {
                    "question": "验证没过，仍按完成结束吗？",
                    "options": [{"label": "仍按完成结束"}, {"label": "继续补救"}],
                },
            ),
            _tool("call-report-1", "report", {"status": "ready", "summary": "照旧结束"}),
            _MEMORY_REPLY,
        ],
        tmp_path,
    )

    assert case.status == "passed", case.error or case.graders
    answers = [
        json.loads(message["content"])
        for message in case.detail["messages"]
        if message["role"] == "tool" and message["tool_call_id"] == "call-ask-1"
    ]
    assert answers and answers[0]["result"]["selected"] == ["仍按完成结束"]


def test_ticket_driver_includes_subagent_session(tmp_path: Path) -> None:
    verdict = json.dumps(
        {
            "passed": True,
            "confidence": 0.9,
            "evidence": ["截图显示已在基建界面"],
            "notes": "",
        },
        ensure_ascii=False,
    )
    data = _ticket_case(
        "ticket-subagent-session",
        expect=[
            {
                "grader": "tool_sequence",
                "steps": [
                    {"tool": "delegate"},
                    {"tool": "get_screenshot"},
                    {"tool": "report"},
                ],
            },
            {
                "grader": "outcome_state",
                "runs": {
                    "count": {"gte": 1},
                    "all": [{"agent_name": "verifier", "status": "succeeded"}],
                },
            },
        ],
    )
    recorder: dict[str, list[str]] = {}

    case = _run(
        data,
        [
            _tool(
                "call-delegate-1",
                "delegate",
                {"agent": "verifier", "task": "核对是否已经到达基建"},
            ),
            _tool("call-shot-1", "get_screenshot"),
            {"text": verdict},
            _tool("call-report-1", "report", {"status": "ready", "summary": "到基建了"}),
            _MEMORY_REPLY,
        ],
        tmp_path,
        recorder_out=recorder,
    )

    assert case.status == "passed", case.error or case.graders
    assert len(recorder["session_ids"]) == 2
    subagent_session = recorder["session_ids"][1]
    subagent_calls = [
        message
        for message in case.detail["messages"]
        if message["session_id"] == subagent_session and message["role"] == "tool"
    ]
    assert subagent_calls, "子代理的工具结果没有进入观察"
    assert {message["tool_name"] for message in subagent_calls} & {"get_screenshot"}


def test_ticket_driver_reports_ticket_not_opened(tmp_path: Path) -> None:
    data = _ticket_case(
        "ticket-not-opened",
        entry={
            "kind": "ticket",
            "worker": "missing-worker",
            "title": "建单失败",
            "brief": "这个 Worker 不存在。",
            "scope": "验证建单失败",
        },
    )

    case = _run(data, [], tmp_path)

    assert case.status == "error"
    assert (case.error or "").startswith("ticket_not_opened："), case.error


def test_ticket_driver_reports_run_timeout(tmp_path: Path) -> None:
    data = _ticket_case(
        "ticket-run-timeout",
        limits={"timeout_seconds": 3, "max_model_calls": 5},
        human={"questions": "ignore"},
    )

    case = _run(
        data,
        [
            _tool(
                "call-ask-1",
                "ask_user",
                {
                    "question": "要继续吗？",
                    "options": [{"label": "继续"}, {"label": "停止"}],
                },
            )
        ],
        tmp_path,
    )

    assert case.status == "error"
    assert (case.error or "").startswith("run_timeout："), case.error
    assert "工单" in (case.error or "")


def test_missing_driver_is_skipped_with_reason(tmp_path: Path) -> None:
    data = {
        "id": "ticket-missing-driver",
        "title": "缺驱动",
        "suite": "regression",
        "agent": "orchestrator",
        "entry": {"kind": "orchestrator"},
        "turns": [{"user": "你好"}],
        "expect": [{"grader": "text_contains", "any_of": ["你好"]}],
    }

    case = _run(data, [], tmp_path)

    assert case.status == "skipped"
    assert case.detail["reason"].startswith("missing_driver：")
