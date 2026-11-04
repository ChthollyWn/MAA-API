"""判分器（M68 规格 §4.2.6、§4.5.5、§4.5.6、10 §6.7）。

覆盖十四个运算符、缺键规则、工具序列的 subsequence/next/exact、禁用工具与调用
次数、outcome 视图与孤儿工具调用、文字包含、llm_judge 的跳过与重试、用例状态。
"""

from __future__ import annotations

import asyncio
import json
from typing import Any

import pytest

from maa_api.agent.evals import (
    JUDGE_SYSTEM_PROMPT,
    CaseObservation,
    CapturedRequest,
    GradeResult,
    JudgeContext,
    LlmJudgeSpec,
    ObservedToolCall,
    OutcomeStateSpec,
    RequestFingerprint,
    TextContainsSpec,
    ToolSequenceSpec,
    ToolStep,
    case_status,
    grade_llm_judge,
    grade_outcome_state,
    grade_text_contains,
    grade_tool_sequence,
    match_value,
    outcome_view,
)


def _obs(**overrides: Any) -> CaseObservation:
    values: dict[str, Any] = {
        "turns": [],
        "tool_calls": [],
        "final_text": "",
        "all_text": "",
        "messages": [],
        "runs": [],
        "pipelines": [],
        "confirmations": [],
        "questions": [],
        "spans": [],
        "requests": [],
        "events": [],
        "model_calls": 0,
    }
    values.update(overrides)
    return CaseObservation(**values)


def _tool(
    name: str,
    arguments: dict[str, Any] | None = None,
    *,
    call_id: str = "c1",
    status: str = "ok",
    error_code: str | None = None,
    result: Any = None,
) -> ObservedToolCall:
    return ObservedToolCall(
        id=call_id,
        name=name,
        arguments=arguments or {},
        status=status,  # type: ignore[arg-type]
        error_code=error_code,
        result=result,
    )


def _fingerprint() -> RequestFingerprint:
    return RequestFingerprint(
        model="eval-model",
        roles=["system", "user"],
        tool_call_ids=[],
        tools_sha="tools1234567",
        system_sha="system123456",
    )


@pytest.mark.parametrize(
    ("expected", "good", "bad"),
    [
        (3, 3, 4),
        ({"x": {"absent": True}}, {}, {"x": None}),
        ({"not": 3}, 4, 3),
        ({"re": "^ab"}, "abc", "zab"),
        ({"contains": "bc"}, "abcd", "zzz"),
        ({"gte": 2}, 3, 1),
        ({"lte": 3}, 3, 4),
        ({"gt": 2}, 3, 2),
        ({"lt": 3}, 2, 3),
        ({"in": ["a", "b"]}, "a", "c"),
        ({"len": 2}, [1, 2], [1]),
        ({"first": 1}, [1, 2], [2, 1]),
        ({"last": 2}, [1, 2], [1, 3]),
        ({"any": {"gt": 4}}, [1, 5], [1, 2]),
        ({"all": {"gt": 0}}, [1, 5], [1, 0]),
    ],
)
def test_match_value_operators(expected: Any, good: Any, bad: Any) -> None:
    assert match_value(expected, good) == []
    errors = match_value(expected, bad)
    assert errors
    assert errors[0].startswith("$")


def test_absent_and_not_on_missing_keys() -> None:
    assert match_value({"timeout_seconds": {"not": {"gt": 120}}}, {}) == []
    assert match_value({"timeout_seconds": {"absent": True}}, {}) == []
    assert match_value({"timeout_seconds": {"not": 5}}, {}) == []
    assert match_value({"timeout_seconds": 120}, {}) != []
    assert match_value({"timeout_seconds": {"gte": 1}}, {}) != []
    assert match_value({"x": {"absent": True}}, {"x": None}) != []
    assert match_value({"x": {"absent": False}}, {"x": None}) == []

    # 对象是子集匹配、列表是按顺序的子序列匹配。
    actual = {
        "tasks": [
            {"name": "Fight", "stage": "1-7"},
            {"name": "StartUp", "start_game_enabled": True},
        ],
        "extra": True,
    }
    assert match_value({"tasks": [{"name": "StartUp"}]}, actual) == []
    assert match_value({"tasks": [{"name": "StartUp"}, {"name": "Fight"}]}, actual)


def test_tool_sequence_subsequence_next_and_exact() -> None:
    calls = [
        _tool("submit_pipeline", {"tasks": [{"name": "Fight"}]}),
        _tool("get_system_status"),
        _tool(
            "wait_pipeline",
            {"pipeline_id": 1},
            status="error",
            error_code="AGENT_TURN_INTERRUPTED",
        ),
    ]
    obs = _obs(tool_calls=calls)

    subsequence = grade_tool_sequence(
        ToolSequenceSpec(
            grader="tool_sequence",
            steps=[
                ToolStep(tool="submit_pipeline", args={"tasks": [{"name": "Fight"}]}),
                ToolStep(tool="wait_pipeline", status="error"),
            ],
        ),
        obs,
    )
    assert subsequence.status == "passed"

    ordered = grade_tool_sequence(
        ToolSequenceSpec(
            grader="tool_sequence",
            steps=[ToolStep(tool="get_system_status"), ToolStep(tool="submit_pipeline")],
        ),
        obs,
    )
    assert ordered.status == "failed"

    next_fails = grade_tool_sequence(
        ToolSequenceSpec(
            grader="tool_sequence",
            steps=[
                ToolStep(tool="submit_pipeline"),
                ToolStep(tool="wait_pipeline", next=True),
            ],
        ),
        obs,
    )
    assert next_fails.status == "failed"
    assert "steps[1]" in next_fails.details[-2]

    exact_mismatch = grade_tool_sequence(
        ToolSequenceSpec(
            grader="tool_sequence",
            match="exact",
            steps=[ToolStep(tool="submit_pipeline")],
        ),
        obs,
    )
    assert exact_mismatch.status == "failed"
    assert "实际调用序列" in exact_mismatch.details[0]

    exact_ok = grade_tool_sequence(
        ToolSequenceSpec(
            grader="tool_sequence",
            match="exact",
            steps=[
                ToolStep(tool="submit_pipeline"),
                ToolStep(tool="get_system_status"),
                ToolStep(
                    tool="wait_pipeline",
                    status="error",
                    error_code="AGENT_TURN_INTERRUPTED",
                ),
            ],
        ),
        obs,
    )
    assert exact_ok.status == "passed"

    arguments_mismatch = grade_tool_sequence(
        ToolSequenceSpec(
            grader="tool_sequence",
            match="exact",
            steps=[ToolStep(tool="get_system_status", args={"detail": "full"})],
        ),
        _obs(tool_calls=[_tool("get_system_status", {"detail": "summary"})]),
    )
    assert arguments_mismatch.status == "failed"
    assert arguments_mismatch.details[0].startswith("steps[0].args")


def test_forbid_and_call_limits() -> None:
    forbidden = grade_tool_sequence(
        ToolSequenceSpec(grader="tool_sequence", forbid=["stop_pipeline"]),
        _obs(
            tool_calls=[
                _tool("submit_pipeline"),
                _tool("stop_pipeline", call_id="c2"),
            ]
        ),
    )
    assert forbidden.status == "failed"
    assert "stop_pipeline" in forbidden.details[0]

    over_max = grade_tool_sequence(
        ToolSequenceSpec(
            grader="tool_sequence", max_calls={"get_pipeline": 1}
        ),
        _obs(
            tool_calls=[
                _tool("get_pipeline", call_id="c1"),
                _tool("get_pipeline", call_id="c2"),
            ]
        ),
    )
    assert over_max.status == "failed"
    assert "超过上限" in over_max.details[0]

    under_min = grade_tool_sequence(
        ToolSequenceSpec(
            grader="tool_sequence", min_calls={"submit_pipeline": 2}
        ),
        _obs(tool_calls=[_tool("submit_pipeline")]),
    )
    assert under_min.status == "failed"
    assert "少于下限" in under_min.details[0]


def test_outcome_view_counts_orphans() -> None:
    messages = [
        {"role": "user", "content": "先看下状态"},
        {
            "role": "assistant",
            "content": "",
            "finish_reason": "tool_calls",
            "tool_calls": [
                {
                    "id": "a",
                    "type": "function",
                    "function": {"name": "get_system_status", "arguments": "{}"},
                },
                {
                    "id": "b",
                    "type": "function",
                    "function": {"name": "get_pipeline", "arguments": "{}"},
                },
            ],
        },
        {"role": "tool", "tool_call_id": "a", "content": '{"result": {}}'},
        {
            "role": "assistant",
            "content": "内核在线，设备已连接。",
            "finish_reason": "stop",
        },
    ]
    obs = _obs(
        messages=messages,
        runs=[
            {
                "agent_name": "legacy_chat",
                "kind": "legacy_turn",
                "status": "succeeded",
                "finish_reason": "stop",
                "steps": 3,
            }
        ],
        pipelines=[
            {
                "id": 1,
                "status": "completed",
                "title": "刷 1-7",
                "error_code": None,
                "tasks": [
                    {
                        "type_name": "Fight",
                        "status": "completed",
                        "params": {"stage": "1-7"},
                        "raw_params": {"stage": "1-7"},
                        "error_code": None,
                    }
                ],
            }
        ],
        confirmations=[{"status": "rejected"}],
        questions=[{"status": "expired"}, {"status": "answered"}],
        spans=[
            {"name": "agent.run"},
            {"name": "llm.chat"},
            {"name": "llm.chat"},
        ],
        requests=[
            CapturedRequest("agent", _fingerprint(), 2, 100, 5),
            CapturedRequest("agent", _fingerprint(), 1, 80, 4),
            CapturedRequest("aux", _fingerprint(), 0, 10, 0),
        ],
        model_calls=4,
    )
    view = outcome_view(obs)
    assert view["runs"]["count"] == 1
    assert view["runs"]["last"]["finish_reason"] == "stop"
    assert view["runs"]["all"][0]["agent_name"] == "legacy_chat"
    assert view["pipelines"]["count"] == 1
    assert view["pipelines"]["last"]["tasks"][0]["type_name"] == "Fight"
    assert view["confirmations"] == {"count": 1, "statuses": ["rejected"]}
    assert view["questions"] == {"count": 2, "statuses": ["expired", "answered"]}
    assert view["messages"]["orphan_tool_calls"] == 1
    assert view["messages"]["last_assistant"] == {
        "finish_reason": "stop",
        "content": "内核在线，设备已连接。",
    }
    assert view["model_calls"] == 4
    assert view["requests"] == {"max_reasoning_messages": 2, "max_chars": 100}
    assert view["spans"] == {
        "count": 3,
        "names": {"agent.run": 1, "llm.chat": 2},
    }

    graded = grade_outcome_state(
        OutcomeStateSpec(
            grader="outcome_state",
            messages={"orphan_tool_calls": 1},
            requests={"max_reasoning_messages": {"lte": 1}},
        ),
        obs,
    )
    assert graded.status == "failed"
    assert graded.details[0].startswith("$.requests")


def test_text_contains_rules() -> None:
    obs = _obs(
        final_text="任务已完成。",
        all_text="任务已完成。\n中间有一次失败。",
    )
    assert grade_text_contains(
        TextContainsSpec(grader="text_contains", all_of=["完成"]), obs
    ).status == "passed"
    assert grade_text_contains(
        TextContainsSpec(grader="text_contains", any_of=["错误", "完成"]), obs
    ).status == "passed"
    assert grade_text_contains(
        TextContainsSpec(grader="text_contains", none_of=["失败"]), obs
    ).status == "passed"
    failed = grade_text_contains(
        TextContainsSpec(
            grader="text_contains", target="all", none_of=["失败"]
        ),
        obs,
    )
    assert failed.status == "failed"
    assert "失败" in failed.details[0]

    mixed = _obs(final_text="Done", all_text="Done\nFAILED")
    assert grade_text_contains(
        TextContainsSpec(grader="text_contains", all_of=["done"]), mixed
    ).status == "passed"
    assert grade_text_contains(
        TextContainsSpec(
            grader="text_contains", all_of=["done"], ignore_case=False
        ),
        mixed,
    ).status == "failed"


def test_llm_judge_skips_without_recorded_reply() -> None:
    result = asyncio.run(
        grade_llm_judge(
            LlmJudgeSpec(grader="llm_judge", rubric="回答正确"),
            _obs(),
            JudgeContext(client_factory=None, model=None),
        )
    )
    assert result.status == "skipped"
    assert result.details == ["回放没有录制评审应答"]


class _FakeJudgeClient:
    def __init__(self, replies: list[str]) -> None:
        self.replies = list(replies)
        self.calls: list[dict[str, Any]] = []
        self.closed = False

    async def stream_chat_completion(
        self,
        model: str,
        messages: list[dict[str, Any]],
        tools: list[dict[str, Any]],
        **options: Any,
    ):
        self.calls.append(
            {"model": model, "messages": list(messages), "tools": list(tools)}
        )
        reply = self.replies.pop(0)
        yield {"choices": [{"index": 0, "delta": {"content": reply}}]}
        yield {
            "choices": [
                {"index": 0, "delta": {}, "finish_reason": "stop"}
            ]
        }

    async def aclose(self) -> None:
        self.closed = True


def test_llm_judge_retries_once_on_invalid_json() -> None:
    client = _FakeJudgeClient(
        [
            "这不是 JSON",
            json.dumps(
                {"score": 0.9, "passed": True, "reasons": ["答到点子上"]},
                ensure_ascii=False,
            ),
        ]
    )
    result = asyncio.run(
        grade_llm_judge(
            LlmJudgeSpec(
                grader="llm_judge",
                rubric="回答点出失败任务与原因",
                pass_score=0.7,
            ),
            _obs(turns=["上一条流水线为什么失败了？"], final_text="失败在 Fight。"),
            JudgeContext(client_factory=lambda: client, model="judge-model"),
        )
    )
    assert result.status == "passed"
    assert result.score == 0.9
    assert result.details == ["答到点子上"]
    assert len(client.calls) == 2
    first = client.calls[0]
    assert first["model"] == "judge-model"
    assert first["messages"][0]["content"] == JUDGE_SYSTEM_PROMPT
    assert "回答点出失败任务与原因" in first["messages"][1]["content"]
    assert "失败在 Fight。" in first["messages"][1]["content"]
    retry_messages = client.calls[1]["messages"]
    assert "无法解析" in retry_messages[-1]["content"]
    assert client.closed is True


def test_case_status_rules() -> None:
    def result(status: str) -> GradeResult:
        return GradeResult("test", status)  # type: ignore[arg-type]

    assert case_status([result("passed")]) == "passed"
    assert case_status([result("passed"), result("skipped")]) == "passed"
    assert case_status([result("passed"), result("failed")]) == "failed"
    assert case_status([result("failed"), result("error")]) == "error"
    assert case_status([result("skipped")]) == "skipped"
    assert case_status([]) == "skipped"
