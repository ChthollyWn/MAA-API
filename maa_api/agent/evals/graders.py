"""评测判分器（M68 规格 §4.2.6、§4.5.5、§4.5.6、10 §6.7）。

四个判分器共用一套期望值匹配规则（``match_value``）：标量相等、对象子集、列表
按顺序做子序列，外加十四个运算符。``grade_case`` 按场景 ``expect`` 的顺序逐个
执行，``case_status`` 把多个判分结果合成这个用例的状态。
"""

from __future__ import annotations

import json
import re
from collections import Counter
from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass, field
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field, ValidationError

from maa_api.agent.evals.cassette import CapturedRequest
from maa_api.agent.evals.scenario import (
    LlmJudgeSpec,
    OutcomeStateSpec,
    Scenario,
    TextContainsSpec,
    ToolSequenceSpec,
    ToolStep,
)

__all__ = [
    "JUDGE_SYSTEM_PROMPT",
    "OPERATORS",
    "CaseObservation",
    "GradeResult",
    "GradeStatus",
    "JudgeContext",
    "ObservedToolCall",
    "case_status",
    "grade_case",
    "grade_llm_judge",
    "grade_outcome_state",
    "grade_text_contains",
    "grade_tool_sequence",
    "match_value",
    "outcome_view",
]

GradeStatus = Literal["passed", "failed", "skipped", "error"]
OPERATORS = frozenset(
    {
        "absent",
        "not",
        "re",
        "contains",
        "gte",
        "lte",
        "gt",
        "lt",
        "in",
        "len",
        "first",
        "last",
        "any",
        "all",
    }
)

#: 评审模型看到的系统提示；逐字照规格 §4.5.6。
JUDGE_SYSTEM_PROMPT = (
    "你是评测员。按评分标准判断这次代理运行是否达标。只输出一个 JSON 对象："
    '{"score": 0 到 1 的数, "passed": true 或 false, "reasons": [最多 5 条简短理由]}。'
)


@dataclass
class GradeResult:
    grader: str
    status: GradeStatus
    score: float | None = None
    details: list[str] = field(default_factory=list)


@dataclass
class ObservedToolCall:
    id: str
    name: str
    arguments: dict[str, Any]
    status: Literal["ok", "error"]
    error_code: str | None
    result: Any


@dataclass
class CaseObservation:
    turns: list[str]
    tool_calls: list[ObservedToolCall]
    final_text: str
    all_text: str
    messages: list[dict[str, Any]]
    runs: list[dict[str, Any]]
    pipelines: list[dict[str, Any]]
    confirmations: list[dict[str, Any]]
    questions: list[dict[str, Any]]
    spans: list[dict[str, Any]]
    requests: list[CapturedRequest]
    events: list[tuple[str, dict[str, Any]]]
    model_calls: int


@dataclass
class JudgeContext:
    client_factory: Callable[[], Any] | None
    model: str | None


# --------------------------------------------------------------------------- #
# 期望值匹配
# --------------------------------------------------------------------------- #


def _format(value: Any, *, limit: int = 120) -> str:
    if isinstance(value, str):
        text = value
    else:
        try:
            text = json.dumps(value, ensure_ascii=False, default=str)
        except (TypeError, ValueError):
            text = repr(value)
    if len(text) > limit:
        text = text[: limit - 1] + "…"
    return text


def _mismatch(path: str, expected: Any, actual: Any, *, note: str = "") -> list[str]:
    suffix = f"（{note}）" if note else ""
    return [f"{path}：期望 {_format(expected)}，实际 {_format(actual)}{suffix}"]


def _is_operator(value: Any) -> bool:
    return (
        isinstance(value, Mapping)
        and len(value) == 1
        and next(iter(value)) in OPERATORS
    )


def _match_missing(expected: Any, path: str) -> list[str]:
    """实际对象里没有这个键时，只有 ``{absent: true}`` 与 ``{not: ...}`` 算匹配。"""
    if isinstance(expected, Mapping) and len(expected) == 1:
        operator = next(iter(expected))
        if operator == "absent" and expected[operator] is True:
            return []
        if operator == "not":
            return []
    return [f"{path}：期望存在这个键，实际没有"]


def _match_number_operator(
    operator: str, expected: Any, actual: Any, path: str
) -> list[str]:
    if not isinstance(expected, (int, float)) or isinstance(expected, bool):
        return [f"{path}：运算符 {operator} 需要数字，实际期望写的是 {_format(expected)}"]
    if not isinstance(actual, (int, float)) or isinstance(actual, bool):
        return _mismatch(path, {operator: expected}, actual, note=f"{operator} 需要数字")
    comparisons = {
        "gte": actual >= expected,
        "lte": actual <= expected,
        "gt": actual > expected,
        "lt": actual < expected,
    }
    if not comparisons[operator]:
        return _mismatch(path, {operator: expected}, actual)
    return []


def _match_operator(operator: str, expected: Any, actual: Any, path: str) -> list[str]:
    if operator == "absent":
        if expected is True:
            return _mismatch(path, {"absent": True}, actual, note="键实际存在")
        return []
    if operator == "not":
        return [] if match_value(expected, actual, path=path) else _mismatch(
            path, {"not": expected}, actual
        )
    if operator == "re":
        if not isinstance(expected, str):
            return [f"{path}：re 运算符需要一个正则字符串"]
        try:
            matched = re.search(expected, str(actual)) is not None
        except re.error as exc:
            return [f"{path}：正则表达式不合法：{exc}"]
        return [] if matched else _mismatch(path, {"re": expected}, actual)
    if operator == "contains":
        try:
            if isinstance(actual, str):
                matched = str(expected) in actual
            elif isinstance(actual, Mapping):
                matched = expected in actual
            elif isinstance(actual, (list, tuple, set, frozenset)):
                matched = expected in actual
            else:
                matched = False
        except TypeError:
            matched = False
        return [] if matched else _mismatch(path, {"contains": expected}, actual)
    if operator in ("gte", "lte", "gt", "lt"):
        return _match_number_operator(operator, expected, actual, path)
    if operator == "in":
        try:
            matched = actual in expected
        except TypeError:
            matched = False
        if isinstance(expected, (str, bytes)) and not isinstance(actual, type(expected)):
            matched = False
        return [] if matched else _mismatch(path, {"in": expected}, actual)
    if operator == "len":
        if not hasattr(actual, "__len__"):
            return _mismatch(path, {"len": expected}, actual, note="实际值没有长度")
        length_errors = match_value(expected, len(actual), path=path)
        if length_errors:
            return [f"{path}：长度不匹配（实际 {len(actual)}）：{length_errors[0]}"]
        return []
    if operator in ("first", "last"):
        if not isinstance(actual, (list, tuple)) or not actual:
            return _mismatch(path, {operator: expected}, actual, note="实际值不是非空列表")
        index = 0 if operator == "first" else len(actual) - 1
        return match_value(expected, actual[index], path=f"{path}[{index}]")
    if operator == "any":
        if not isinstance(actual, (list, tuple, set, frozenset)):
            return _mismatch(path, {"any": expected}, actual, note="实际值不是列表")
        if any(match_value(expected, item, path=path) == [] for item in actual):
            return []
        return _mismatch(path, {"any": expected}, actual)
    if operator == "all":
        if not isinstance(actual, (list, tuple, set, frozenset)):
            return _mismatch(path, {"all": expected}, actual, note="实际值不是列表")
        errors: list[str] = []
        for index, item in enumerate(actual):
            errors.extend(match_value(expected, item, path=f"{path}[{index}]"))
        return errors
    return [f"{path}：未知运算符 {operator}"]


def _match_list(expected: Sequence[Any], actual: Sequence[Any], path: str) -> list[str]:
    """列表按顺序做子序列匹配：期望项依次出现，实际多出来的项允许跳过。"""
    if not isinstance(actual, (list, tuple)):
        return _mismatch(path, list(expected), actual, note="实际值不是列表")
    cursor = 0
    for position, item in enumerate(expected):
        found = None
        for index in range(cursor, len(actual)):
            if not match_value(item, actual[index], path=f"{path}[{index}]"):
                found = index
                break
        if found is None:
            return [
                f"{path}：列表里找不到按顺序匹配第 {position + 1} 项 "
                f"{_format(item)} 的元素（实际 {_format(list(actual))}）"
            ]
        cursor = found + 1
    return []


def match_value(expected: Any, actual: Any, *, path: str = "$") -> list[str]:
    """按 10 §6.7 的规则匹配期望值；返回不匹配的描述，空列表表示匹配。"""
    if _is_operator(expected):
        operator, value = next(iter(expected.items()))
        return _match_operator(operator, value, actual, path)
    if isinstance(expected, Mapping):
        if not isinstance(actual, Mapping):
            return _mismatch(path, dict(expected), actual, note="实际值不是对象")
        errors: list[str] = []
        for key, item in expected.items():
            child = f"{path}.{key}"
            if key not in actual:
                errors.extend(_match_missing(item, child))
                continue
            errors.extend(match_value(item, actual[key], path=child))
        return errors
    if isinstance(expected, (list, tuple)):
        return _match_list(expected, actual, path)
    if isinstance(expected, bool) != isinstance(actual, bool):
        return _mismatch(path, expected, actual)
    if expected != actual:
        return _mismatch(path, expected, actual)
    return []


# --------------------------------------------------------------------------- #
# 观测视图
# --------------------------------------------------------------------------- #


def _run_view(run: Mapping[str, Any]) -> dict[str, Any]:
    return {
        "agent_name": run.get("agent_name"),
        "kind": run.get("kind"),
        "status": run.get("status"),
        "finish_reason": run.get("finish_reason"),
        "steps": run.get("steps"),
    }


def _pipeline_view(pipeline: Mapping[str, Any]) -> dict[str, Any]:
    tasks = pipeline.get("tasks")
    return {
        "status": pipeline.get("status"),
        "title": pipeline.get("title"),
        "error_code": pipeline.get("error_code"),
        "tasks": [
            {
                "type_name": task.get("type_name"),
                "status": task.get("status"),
                "params": task.get("params"),
                "raw_params": task.get("raw_params"),
                "error_code": task.get("error_code"),
            }
            for task in (tasks if isinstance(tasks, list) else [])
            if isinstance(task, Mapping)
        ],
    }


def _orphan_tool_calls(messages: Sequence[Mapping[str, Any]]) -> int:
    """助手消息里声明过、却没有对应 tool 消息回答的 tool_call id 数量。"""
    declared: set[str] = set()
    answered: set[str] = set()
    for message in messages:
        role = message.get("role")
        if role == "assistant":
            tool_calls = message.get("tool_calls")
            if isinstance(tool_calls, list):
                for call in tool_calls:
                    if isinstance(call, Mapping) and isinstance(call.get("id"), str):
                        declared.add(call["id"])
        elif role == "tool":
            call_id = message.get("tool_call_id")
            if isinstance(call_id, str):
                answered.add(call_id)
    return len(declared - answered)


def _last_assistant(messages: Sequence[Mapping[str, Any]]) -> dict[str, Any] | None:
    for message in reversed(messages):
        if message.get("role") == "assistant":
            return {
                "finish_reason": message.get("finish_reason"),
                "content": message.get("content") or "",
            }
    return None


def outcome_view(obs: CaseObservation) -> dict[str, Any]:
    """把观测整理成 ``outcome_state`` 判分器看到的系统状态。"""
    runs = [
        _run_view(run) for run in obs.runs if isinstance(run, Mapping)
    ]
    pipelines = [
        _pipeline_view(pipeline)
        for pipeline in obs.pipelines
        if isinstance(pipeline, Mapping)
    ]
    agent_requests = [
        request for request in obs.requests if request.channel == "agent"
    ]
    span_names = Counter(
        str(span.get("name"))
        for span in obs.spans
        if isinstance(span, Mapping) and span.get("name") is not None
    )
    return {
        "runs": {
            "count": len(runs),
            "last": runs[-1] if runs else None,
            "all": runs,
        },
        "pipelines": {
            "count": len(pipelines),
            "last": pipelines[-1] if pipelines else None,
        },
        "confirmations": {
            "count": len(obs.confirmations),
            "statuses": [
                confirmation.get("status")
                for confirmation in obs.confirmations
                if isinstance(confirmation, Mapping)
            ],
        },
        "questions": {
            "count": len(obs.questions),
            "statuses": [
                question.get("status")
                for question in obs.questions
                if isinstance(question, Mapping)
            ],
        },
        "messages": {
            "orphan_tool_calls": _orphan_tool_calls(obs.messages),
            "last_assistant": _last_assistant(obs.messages),
        },
        "model_calls": obs.model_calls,
        "requests": {
            "max_reasoning_messages": max(
                (request.reasoning_messages for request in agent_requests),
                default=0,
            ),
            "max_chars": max(
                (request.chars for request in agent_requests), default=0
            ),
        },
        "spans": {"count": len(obs.spans), "names": dict(span_names)},
    }


# --------------------------------------------------------------------------- #
# 四个判分器
# --------------------------------------------------------------------------- #


def _describe_call(call: ObservedToolCall) -> str:
    arguments = _format(call.arguments, limit=80)
    suffix = f" {call.status}"
    if call.error_code:
        suffix += f"({call.error_code})"
    return f"{call.name}({arguments}){suffix}"


def _call_sequence(calls: Sequence[ObservedToolCall]) -> str:
    if not calls:
        return "（没有工具调用）"
    return " → ".join(_describe_call(call) for call in calls)


def _match_step(step: ToolStep, call: ObservedToolCall, path: str) -> list[str]:
    errors: list[str] = []
    if step.tool != call.name:
        errors.append(f"{path}.tool：期望 {step.tool}，实际 {call.name}")
    if step.args is not None:
        errors.extend(match_value(step.args, call.arguments, path=f"{path}.args"))
    if step.status is not None and step.status != call.status:
        errors.append(f"{path}.status：期望 {step.status}，实际 {call.status}")
    if step.error_code is not None and step.error_code != call.error_code:
        errors.append(
            f"{path}.error_code：期望 {step.error_code}，实际 {call.error_code}"
        )
    return errors


def grade_tool_sequence(
    spec: ToolSequenceSpec, obs: CaseObservation
) -> GradeResult:
    """按顺序/精确匹配工具调用，并检查禁用工具与调用次数。"""
    calls = obs.tool_calls
    counts = Counter(call.name for call in calls)
    errors: list[str] = []

    for tool in spec.forbid:
        if counts.get(tool):
            errors.append(f"出现了禁止的工具 {tool}（{counts[tool]} 次调用）")
    for tool, limit in spec.max_calls.items():
        if counts.get(tool, 0) > limit:
            errors.append(f"工具 {tool} 调用了 {counts[tool]} 次，超过上限 {limit}")
    for tool, limit in spec.min_calls.items():
        if counts.get(tool, 0) < limit:
            errors.append(f"工具 {tool} 调用了 {counts.get(tool, 0)} 次，少于下限 {limit}")

    if spec.match == "exact":
        if len(calls) != len(spec.steps):
            errors.append(
                f"期望 {len(spec.steps)} 次工具调用，实际 {len(calls)} 次；"
                f"实际调用序列：{_call_sequence(calls)}"
            )
        else:
            for index, step in enumerate(spec.steps):
                step_errors = _match_step(step, calls[index], f"steps[{index}]")
                if step_errors:
                    errors.extend(step_errors)
                    errors.append(f"实际调用序列：{_call_sequence(calls)}")
                    break
    else:
        cursor = 0
        last_index = -1
        for index, step in enumerate(spec.steps):
            if step.next and last_index >= 0:
                candidate = last_index + 1
                step_errors = (
                    _match_step(step, calls[candidate], f"steps[{index}]")
                    if candidate < len(calls)
                    else [f"steps[{index}] 要求紧跟在上一步之后，但实际没有下一次调用"]
                )
                if step_errors:
                    errors.extend(step_errors)
                    errors.append(f"实际调用序列：{_call_sequence(calls)}")
                    break
                last_index = candidate
                cursor = candidate + 1
                continue
            found = None
            for position in range(cursor, len(calls)):
                if not _match_step(step, calls[position], f"steps[{index}]"):
                    found = position
                    break
            if found is None:
                errors.append(
                    f"steps[{index}] 没有匹配到工具调用（期望 {step.tool}）；"
                    f"实际调用序列：{_call_sequence(calls)}"
                )
                break
            last_index = found
            cursor = found + 1

    if errors:
        return GradeResult("tool_sequence", "failed", details=errors)
    return GradeResult("tool_sequence", "passed")


def grade_outcome_state(
    spec: OutcomeStateSpec, obs: CaseObservation
) -> GradeResult:
    """把规格里每个非空字段与 ``outcome_view`` 的同名字段比较。"""
    view = outcome_view(obs)
    errors: list[str] = []
    for name in (
        "runs",
        "pipelines",
        "confirmations",
        "questions",
        "messages",
        "model_calls",
        "requests",
        "spans",
    ):
        expected = getattr(spec, name)
        if expected is None:
            continue
        errors.extend(match_value(expected, view[name], path=f"$.{name}"))
    if errors:
        return GradeResult("outcome_state", "failed", details=errors)
    return GradeResult("outcome_state", "passed")


def grade_text_contains(
    spec: TextContainsSpec, obs: CaseObservation
) -> GradeResult:
    """检查回复必须出现、至少出现一次与不得出现的文字。"""
    corpus = obs.final_text if spec.target == "final" else obs.all_text
    label = "最终回复" if spec.target == "final" else "全部回复"
    if spec.ignore_case:
        corpus = corpus.casefold()

    def prepare(text: str) -> str:
        return text.casefold() if spec.ignore_case else text

    errors: list[str] = []
    for text in spec.all_of:
        if prepare(text) not in corpus:
            errors.append(f"{label}缺少「{text}」")
    if spec.any_of:
        if not any(prepare(text) in corpus for text in spec.any_of):
            errors.append(
                f"{label}没有出现任何一项：{'、'.join(spec.any_of)}"
            )
    for text in spec.none_of:
        if prepare(text) in corpus:
            errors.append(f"{label}出现了不该出现的「{text}」")
    if errors:
        return GradeResult("text_contains", "failed", details=errors)
    return GradeResult("text_contains", "passed")


# --------------------------------------------------------------------------- #
# llm_judge
# --------------------------------------------------------------------------- #


class _JudgeVerdict(BaseModel):
    model_config = ConfigDict(extra="ignore")

    score: float = Field(ge=0, le=1)
    passed: bool
    reasons: list[str] = Field(default_factory=list)


def _judge_user_message(spec: LlmJudgeSpec, obs: CaseObservation) -> str:
    lines = ["评分标准：", spec.rubric, "", "用户每轮说的话："]
    if obs.turns:
        lines.extend(f"{index}. {turn}" for index, turn in enumerate(obs.turns, 1))
    else:
        lines.append("（没有记录）")
    lines.extend(["", "工具调用："])
    if obs.tool_calls:
        for index, call in enumerate(obs.tool_calls, 1):
            arguments = _format(call.arguments, limit=300)
            outcome = call.status
            if call.error_code:
                outcome += f"，错误码 {call.error_code}"
            lines.append(f"{index}. {call.name}({arguments}) → {outcome}")
    else:
        lines.append("（没有工具调用）")
    lines.extend(["", "最终回复：", obs.final_text or "（没有文字回复）"])
    return "\n".join(lines)


def _extract_verdict(text: str) -> _JudgeVerdict:
    stripped = text.strip()
    start = stripped.find("{")
    end = stripped.rfind("}")
    if start < 0 or end <= start:
        raise ValueError("输出里没有 JSON 对象")
    payload = json.loads(stripped[start : end + 1])
    if not isinstance(payload, dict):
        raise ValueError("输出不是 JSON 对象")
    return _JudgeVerdict.model_validate(payload)


async def _collect_judge_text(client: Any, model: str, messages: list[dict[str, Any]]) -> str:
    parts: list[str] = []
    async for chunk in client.stream_chat_completion(model, messages, []):
        if not isinstance(chunk, Mapping):
            continue
        choices = chunk.get("choices")
        if not isinstance(choices, list):
            continue
        for choice in choices:
            if not isinstance(choice, Mapping):
                continue
            delta = choice.get("delta")
            if isinstance(delta, Mapping) and isinstance(delta.get("content"), str):
                parts.append(delta["content"])
    return "".join(parts)


async def grade_llm_judge(
    spec: LlmJudgeSpec, obs: CaseObservation, judge: JudgeContext
) -> GradeResult:
    """让评审模型按评分标准打分；输出校验失败时重试一次。"""
    if judge.client_factory is None:
        return GradeResult(
            "llm_judge", "skipped", details=["回放没有录制评审应答"]
        )
    if not judge.model:
        return GradeResult("llm_judge", "error", details=["没有配置评审模型"])

    messages: list[dict[str, Any]] = [
        {"role": "system", "content": JUDGE_SYSTEM_PROMPT},
        {"role": "user", "content": _judge_user_message(spec, obs)},
    ]
    try:
        client = judge.client_factory()
    except Exception as exc:  # noqa: BLE001 - 评审端构造失败按 error 记录
        return GradeResult(
            "llm_judge", "error", details=[f"评审调用失败：{type(exc).__name__}: {exc}"]
        )
    if client is None:
        return GradeResult(
            "llm_judge", "skipped", details=["回放没有录制评审应答"]
        )
    try:
        first_error: str | None = None
        text = ""
        for attempt in range(2):
            text = await _collect_judge_text(client, judge.model, messages)
            try:
                verdict = _extract_verdict(text)
            except (ValueError, ValidationError) as exc:
                first_error = str(exc)
                if attempt == 0:
                    messages = [
                        *messages,
                        {"role": "assistant", "content": text},
                        {
                            "role": "user",
                            "content": (
                                "上一次输出无法解析为要求的 JSON："
                                f"{first_error}。请只输出一个 JSON 对象。"
                            ),
                        },
                    ]
                    continue
                return GradeResult(
                    "llm_judge",
                    "error",
                    details=[f"评审输出不是合法 JSON：{first_error}"],
                )
            details = [str(reason) for reason in verdict.reasons][:5]
            status: GradeStatus = (
                "passed"
                if verdict.passed and verdict.score >= spec.pass_score
                else "failed"
            )
            if not details:
                details = [f"评审得分 {verdict.score:.2f}（阈值 {spec.pass_score:.2f}）"]
            return GradeResult(
                "llm_judge", status, score=verdict.score, details=details
            )
    except Exception as exc:  # noqa: BLE001 - 评审调用失败按 error 记录
        return GradeResult(
            "llm_judge",
            "error",
            details=[f"评审调用失败：{type(exc).__name__}: {exc}"],
        )
    finally:
        aclose = getattr(client, "aclose", None)
        if callable(aclose):
            try:
                await aclose()
            except Exception:  # noqa: BLE001 - 关闭失败不影响判分
                pass
    return GradeResult("llm_judge", "error", details=["评审没有返回结果"])


# --------------------------------------------------------------------------- #
# 一个场景的判分
# --------------------------------------------------------------------------- #


async def grade_case(
    scenario: Scenario, obs: CaseObservation, judge: JudgeContext
) -> list[GradeResult]:
    """按场景 ``expect`` 的顺序执行并返回每个判分器的结果。"""
    results: list[GradeResult] = []
    for spec in scenario.expect:
        if isinstance(spec, ToolSequenceSpec):
            results.append(grade_tool_sequence(spec, obs))
        elif isinstance(spec, OutcomeStateSpec):
            results.append(grade_outcome_state(spec, obs))
        elif isinstance(spec, TextContainsSpec):
            results.append(grade_text_contains(spec, obs))
        elif isinstance(spec, LlmJudgeSpec):
            results.append(await grade_llm_judge(spec, obs, judge))
        else:  # pragma: no cover - 判别联合保证不会走到
            results.append(
                GradeResult(
                    str(getattr(spec, "grader", "unknown")),
                    "error",
                    details=["未知判分器"],
                )
            )
    return results


def case_status(
    results: Sequence[GradeResult],
) -> Literal["passed", "failed", "error", "skipped"]:
    """合成用例状态：有 error 记 error，否则有 failed 记 failed，再否则看 passed。"""
    statuses = {result.status for result in results}
    if "error" in statuses:
        return "error"
    if "failed" in statuses:
        return "failed"
    if "passed" in statuses:
        return "passed"
    return "skipped"
