"""M65 T9：侦察子循环跑在 AgentLoop 上（规格 §4.2.12、§4.5.2）。

recon 的链只有 ``trace`` / ``recon_budget`` / ``recon_dispatch``：不挂上下文组装、
打转检测与策略审计。这里用假步进函数与记录型注册表钉住循环用的定义、链顺序、
预算收尾与工具串行派发，不碰数据库。
"""

from __future__ import annotations

import asyncio
import json
from pathlib import Path
from typing import Any

import pytest

from maa_api.agent.harness.context import RunOutcome
from maa_api.agent.harness.loop import AgentLoop
from maa_api.agent.recon import explore
from maa_api.agent.recon.runner import ReconDeps, ReconStep, run_recon
from maa_api.agent.registry import ToolDefinition, ToolRegistry, ToolRisk
from maa_api.agent.specs.registry import builtin_spec
from maa_api.settings import AgentSettings, Settings, WorkspaceSettings

#: 子循环能用的三个只读工具与它们的参数模型。
_RECON_TOOLS = (
    ("list_files", explore.ListFilesParams),
    ("read_file", explore.ReadFileParams),
    ("search_code", explore.SearchCodeParams),
)


def _settings(root: Path) -> Settings:
    return Settings(
        workspace=WorkspaceSettings(root=str(root)),
        agent=AgentSettings(),
    )


def _deps(
    settings: Settings,
    *,
    max_steps: int = 8,
    max_chars: int = 200_000,
    output_chars: int = 20_000,
) -> ReconDeps:
    return ReconDeps(
        settings=settings,
        client=None,
        model="recon-model",
        max_steps=max_steps,
        max_chars=max_chars,
        output_chars=output_chars,
    )


class _StepScript:
    """注入 ``run_recon`` 的假步进函数：按剧本返回步骤并记录每次看到的 messages。"""

    def __init__(self, steps: list[ReconStep] | None = None) -> None:
        self.steps = list(steps or [])
        self.seen: list[list[dict[str, Any]]] = []

    async def __call__(
        self,
        _deps: ReconDeps,
        messages: list[dict[str, Any]],
        _schemas: list[dict[str, Any]],
    ) -> ReconStep:
        self.seen.append([dict(message) for message in messages])
        if not self.steps:
            return ReconStep(text="没有更多步骤")
        return self.steps.pop(0)


def _call(
    call_id: str,
    name: str = "list_files",
    arguments: dict[str, Any] | None = None,
) -> dict[str, Any]:
    return {
        "id": call_id,
        "name": name,
        "arguments": json.dumps(arguments if arguments is not None else {"path": "."}),
    }


def _recorder(name: str, calls: list[str]):
    async def handler(_params: Any, _context: Any) -> dict[str, Any]:
        calls.append(name)
        return {"ok": True, "tool": name}

    return handler


def _registry(resolve_handler) -> ToolRegistry:
    """三个同名工具的表：参数模型是真的，处理器由调用方决定。"""

    registry = ToolRegistry()
    for name, params_model in _RECON_TOOLS:
        registry.register(
            ToolDefinition(
                name=name,
                group="recon",
                risk=ToolRisk.SAFE,
                description=f"假的 {name}",
                params_model=params_model,
                handler=resolve_handler(name),
                read_only=True,
            )
        )
    return registry


def _answered_report() -> ReconStep:
    return ReconStep(
        text=json.dumps(
            {"summary": "查清了", "findings": [], "uncertainties": []},
            ensure_ascii=False,
        )
    )


# --------------------------------------------------------------------------- #
# 循环与链：recon 用 AgentLoop，但只用 trace / recon_budget / recon_dispatch
# --------------------------------------------------------------------------- #


def test_recon_runs_on_agent_loop_with_the_recon_definition(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    captured: dict[str, Any] = {}

    async def recording_run(loop_self: AgentLoop, ctx: Any) -> RunOutcome:
        captured["spec"] = ctx.spec
        captured["session_id"] = ctx.session_id
        captured["parallel_tools"] = ctx.parallel_tools
        return RunOutcome("succeeded", "stop")

    monkeypatch.setattr(AgentLoop, "run", recording_run)
    deps = _deps(_settings(tmp_path), max_steps=3)

    report = asyncio.run(
        run_recon(
            deps,
            question="入口在哪",
            registry=ToolRegistry(),
            step_runner=_StepScript(),
        )
    )

    spec = captured["spec"]
    assert spec.name == "recon"
    # 本次调用的预算覆盖了定义里的默认 8 步；内置定义本身不被改写。
    assert spec.max_steps == deps.max_steps == 3
    assert builtin_spec("recon").max_steps == 8
    assert spec.body == builtin_spec("recon").body
    assert captured["session_id"] is None
    assert captured["parallel_tools"] is False
    assert report["stopped"] == "answered"


def test_recon_chain_skips_context_layout_loop_guard_and_policy(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    captured: dict[str, Any] = {}

    async def recording_run(loop_self: AgentLoop, ctx: Any) -> RunOutcome:
        captured["names"] = loop_self._chain.names
        return RunOutcome("succeeded", "stop")

    monkeypatch.setattr(AgentLoop, "run", recording_run)

    asyncio.run(
        run_recon(
            _deps(_settings(tmp_path)),
            question="入口在哪",
            registry=ToolRegistry(),
            step_runner=_StepScript(),
        )
    )

    assert captured["names"] == ("trace", "recon_budget", "recon_dispatch")
    assert {"context_layout", "loop_guard", "policy"}.isdisjoint(captured["names"])


# --------------------------------------------------------------------------- #
# 边界：输出预算用尽、max_steps < 1、工具逐件派发
# --------------------------------------------------------------------------- #


def test_recon_output_budget_stops_the_rest_of_the_batch(tmp_path: Path) -> None:
    calls: list[str] = []

    report = asyncio.run(
        run_recon(
            # 一个字符的预算：第一条工具输出就把它用尽。
            _deps(_settings(tmp_path), max_steps=4, max_chars=1),
            question="Q",
            registry=_registry(lambda name: _recorder(name, calls)),
            step_runner=_StepScript(
                [ReconStep(text="", calls=[_call("c1"), _call("c2")])]
            ),
        )
    )

    assert calls == ["list_files"]  # 第二个调用没有派发
    assert report["stopped"] == "budget"
    assert report["steps_used"] == 1
    assert report["budget"]["exhausted"] is True
    assert any("预算用尽" in item for item in report["uncertainties"])


def test_recon_with_zero_max_steps_never_calls_the_model(tmp_path: Path) -> None:
    script = _StepScript([_answered_report()])

    report = asyncio.run(
        run_recon(
            _deps(_settings(tmp_path), max_steps=0),
            question="Q",
            registry=ToolRegistry(),
            step_runner=script,
        )
    )

    assert script.seen == []
    assert report["steps_used"] == 0
    assert report["stopped"] == "max_steps"
    assert report["budget"]["max_steps"] == 0
    assert any("步数用尽（0 步）" in item for item in report["uncertainties"])


def test_recon_dispatches_tools_one_by_one(tmp_path: Path) -> None:
    calls: list[str] = []
    active = 0
    peak = 0

    def resolve_handler(name: str):
        async def handler(_params: Any, _context: Any) -> dict[str, Any]:
            nonlocal active, peak
            calls.append(name)
            active += 1
            peak = max(peak, active)
            try:
                await asyncio.sleep(0.01)
            finally:
                active -= 1
            return {"ok": True, "tool": name}

        return handler

    script = _StepScript(
        [
            ReconStep(text="", calls=[_call(f"c{index}") for index in range(3)]),
            _answered_report(),
        ]
    )

    report = asyncio.run(
        run_recon(
            _deps(_settings(tmp_path)),
            question="Q",
            registry=_registry(resolve_handler),
            step_runner=script,
        )
    )

    assert calls == ["list_files", "list_files", "list_files"]
    assert peak == 1  # 只读批也不并发：parallel_tools=False
    assert report["stopped"] == "answered"
    assert report["steps_used"] == 2
