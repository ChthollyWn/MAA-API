"""评测运行器的端到端用例（M68 spec §6 test_eval_runner.py）。

用 ``FakeWorldEnvironment`` 与临时目录跑通「场景 + 录制 + 判分 + 结果落盘」，
不联网、不依赖 T11 的 12 个场景。
"""

from __future__ import annotations

import asyncio
import json
from pathlib import Path

import pytest

from maa_api.agent.evals import cassette as cassette_module
from maa_api.agent.evals import runner as runner_module
from maa_api.agent.evals.cassette import Cassette
from maa_api.agent.evals.runner import (
    REPLAY_ENDPOINT,
    EvalOptions,
    EvalRunner,
    EvalVariant,
    write_result,
)
from maa_api.agent.evals.scenario import load_scenarios
from tests.evals.environment import FakeWorldEnvironment

REPO_ROOT = Path(__file__).resolve().parents[2]
HEADER = {
    "type": "header",
    "format": 1,
    "scenario": "demo",
    "source": "handwritten",
    "recorded_at": "2026-11-04T00:00:00Z",
    "model": "eval-model",
    "agent": "legacy_chat",
    "spec_version": None,
    "notes": "测试",
}


def _write_scenario(root: Path, *, scenario_id: str = "demo", **overrides) -> Path:
    """写一个最小场景并返回场景目录。"""
    scenarios = root / "scenarios"
    scenarios.mkdir(parents=True, exist_ok=True)
    payload = {
        "id": scenario_id,
        "title": "自检",
        "suite": "regression",
        "agent": "legacy_chat",
        "entry": {"kind": "legacy_session"},
        "turns": [{"user": "你好"}],
        "expect": [{"grader": "text_contains", "any_of": ["你好"]}],
        "limits": {"timeout_seconds": 30, "max_model_calls": 5},
    }
    payload.update(overrides)
    path = scenarios / f"{scenario_id}.yaml"
    import json as json_module

    path.write_text(json_module.dumps(payload, ensure_ascii=False), encoding="utf8")
    return scenarios


def _write_cassette(root: Path, *, scenario_id: str = "demo", text: str = "你好，我在。") -> Path:
    """写一个最小录制文件并返回目录。"""
    cassettes = root / "cassettes"
    cassettes.mkdir(parents=True, exist_ok=True)
    header = dict(HEADER)
    header["scenario"] = scenario_id
    body = {"type": "call", "channel": "agent", "reply": {"text": text}}
    path = cassettes / f"{scenario_id}.jsonl"
    path.write_text(
        json.dumps(header, ensure_ascii=False) + "\n" + json.dumps(body, ensure_ascii=False) + "\n",
        encoding="utf8",
    )
    return cassettes


def _environment(root: Path) -> FakeWorldEnvironment:
    fixtures = root / "fixtures"
    fixtures.mkdir(parents=True, exist_ok=True)
    return FakeWorldEnvironment(repo_root=REPO_ROOT, fixtures_dir=fixtures)


def _options(root: Path, **overrides) -> EvalOptions:
    payload = {
        "mode": "replay",
        "cassette_dir": root / "cassettes",
        "out_dir": root / "out",
    }
    payload.update(overrides)
    return EvalOptions(**payload)


def test_replay_case_passes_end_to_end(tmp_path: Path) -> None:
    scenarios = load_scenarios(_write_scenario(tmp_path))
    _write_cassette(tmp_path)
    result = EvalRunner(_environment(tmp_path), _options(tmp_path)).run(
        scenarios, suite_label="regression", filters={}
    )
    assert len(result.cases) == 1
    case = result.cases[0]
    assert case.status == "passed", case.error or case.graders
    assert case.model_calls == 1
    assert case.usage["prompt_tokens"] == 0
    assert result.summary()["passed"] == 1


def test_unsupported_entry_is_skipped(tmp_path: Path) -> None:
    scenarios = load_scenarios(
        _write_scenario(
            tmp_path,
            entry={"kind": "orchestrator", "worker": "legacy_chat"},
            turns=[{"user": "你好"}],
        )
    )
    _write_cassette(tmp_path)
    result = EvalRunner(_environment(tmp_path), _options(tmp_path)).run(
        scenarios, suite_label="regression", filters={}
    )
    case = result.cases[0]
    assert case.status == "skipped"
    assert "入口" in case.detail["reason"]


def test_timeout_interrupts_and_errors(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    async def hanging_driver(eval_app, scenario, recorder):
        del eval_app, scenario, recorder
        await asyncio.sleep(10)

    monkeypatch.setitem(runner_module.DRIVERS, "legacy_session", hanging_driver)
    scenarios = load_scenarios(
        _write_scenario(tmp_path, limits={"timeout_seconds": 1, "max_model_calls": 5})
    )
    _write_cassette(tmp_path)
    result = EvalRunner(_environment(tmp_path), _options(tmp_path)).run(
        scenarios, suite_label="regression", filters={}
    )
    case = result.cases[0]
    assert case.status == "error"
    assert "超过" in (case.error or "")


def test_write_result_appends_index(tmp_path: Path) -> None:
    scenarios = load_scenarios(_write_scenario(tmp_path))
    _write_cassette(tmp_path)
    runner = EvalRunner(_environment(tmp_path), _options(tmp_path))
    first = runner.run(scenarios, suite_label="regression", filters={})
    path = write_result(first, tmp_path / "out")
    assert path.name == "result.json"
    second = runner.run(scenarios, suite_label="regression", filters={})
    write_result(second, tmp_path / "out")
    lines = (tmp_path / "out" / "index.jsonl").read_text(encoding="utf8").strip().splitlines()
    assert len(lines) == 2
    payload = json.loads(lines[0])
    assert payload["suite"] == "regression"
    assert payload["summary"]["total"] == 1


def test_record_saves_only_passing_cassettes(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """record 模式：通过的用例写录制文件，失败的不写。"""
    import httpx

    class RecordingStub(httpx.AsyncBaseTransport):
        """把固定应答同时写进 cassette，充当一次成功的录制。"""

        def __init__(self, cassette, *, max_calls, inner=None, channel_override=None):
            del max_calls, inner, channel_override
            self.cassette = cassette

        async def handle_async_request(self, request):
            body = json.loads(request.content.decode("utf8"))
            channel = cassette_module.classify_request(body)
            reply: dict = {"text": "你好，我在。"}
            if channel == "probe":
                # 能力探查按真实协议回：工具探查回固定的 capability_probe 调用，
                # 视觉探查回颜色；否则运行时会把模型判成不支持工具。
                tool_names = [
                    (tool.get("function") or {}).get("name")
                    for tool in body.get("tools") or []
                ]
                reply = (
                    {
                        "tool_calls": [
                            {
                                "id": "call_probe",
                                "name": "capability_probe",
                                "arguments": {},
                            }
                        ],
                        "finish_reason": "tool_calls",
                    }
                    if "capability_probe" in tool_names
                    else {"text": "blue"}
                )
            elif channel == "aux":
                reply = {"text": "评测会话"}
            self.cassette.append(
                cassette_module.CassetteCall(channel=channel, reply=reply)
            )
            chunks = cassette_module.expand_reply(
                reply, model="eval-model", seq=len(self.cassette.calls)
            )
            return httpx.Response(
                200,
                content=cassette_module.sse_body(chunks),
                headers={"content-type": "text/event-stream"},
            )

    class FailingStub(RecordingStub):
        async def handle_async_request(self, request):
            del request
            return httpx.Response(500, json={"error": {"message": "upstream down"}})

    monkeypatch.setattr(runner_module, "RecordingTransport", RecordingStub)
    passing = load_scenarios(
        _write_scenario(tmp_path, scenario_id="demo-pass", cassette="demo-pass.jsonl")
    )
    failing = load_scenarios(
        _write_scenario(tmp_path, scenario_id="demo-fail", cassette="demo-fail.jsonl")
    )
    options = _options(
        tmp_path,
        mode="record",
        real_llm=REPLAY_ENDPOINT,
    )
    runner = EvalRunner(_environment(tmp_path), options)
    result = runner.run(passing, suite_label="regression", filters={})
    assert result.cases[0].status == "passed", result.cases[0].error
    assert (tmp_path / "cassettes" / "demo-pass.jsonl").is_file()

    monkeypatch.setattr(runner_module, "RecordingTransport", FailingStub)
    result = runner.run(failing, suite_label="regression", filters={})
    assert result.cases[0].status in ("failed", "error")
    assert not (tmp_path / "cassettes" / "demo-fail.jsonl").exists()


def test_variants_are_summarized_separately(tmp_path: Path) -> None:
    scenarios = load_scenarios(_write_scenario(tmp_path))
    _write_cassette(tmp_path)
    options = _options(
        tmp_path,
        variants=(EvalVariant("target"), EvalVariant("alt")),
    )
    result = EvalRunner(_environment(tmp_path), options).run(
        scenarios, suite_label="regression", filters={}
    )
    variants = result.variants()
    assert set(variants) == {"target", "alt"}
    assert variants["target"]["total"] == 1
    assert result.summary("alt")["total"] == 1
    assert len(result.cases) == 2
