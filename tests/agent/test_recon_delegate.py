"""M48 ``delegate_recon`` 与侦察子循环（规格 §4.3 / §4.4 / D4 / D5）的验收用例。

子 agent 的模型有两处注入口，两种都用上：

- 假客户端：``stream_chat_completion`` 是异步生成器，产出 OpenAI 形态的 chunk（含分片的
  ``tool_calls`` 与 ``usage``），用来验证默认步进函数与流式拼块；
- ``step_runner``：直接给 ``run_recon`` 注入按剧本返回的 :class:`ReconStep`，用来把步数、
  预算、收尾报告这些分支钉死。

``runtime`` 用假对象（只实现 ``recon``）断言 ``delegate_recon`` 的参数透传；工作区用
``tmp_path`` 现造。带 ``xfail`` 的用例断言的是**规格**的读法，当前实现不合规（见 reason
与 W3 报告），``strict=False``，修好后转为 XPASS。
"""

from __future__ import annotations

import asyncio
import copy
import json
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import pytest
from pydantic import ValidationError

from maa_api.agent.recon import delegate, explore
from maa_api.agent.recon.runner import (
    MAX_CALLS_PER_STEP,
    ReconDeps,
    ReconStep,
    budget_from_settings,
    dispatch_tool,
    recon_tool_schemas,
    run_recon,
)
from maa_api.agent.registry import ToolContext, ToolDefinition, ToolRegistry, ToolRisk
from maa_api.domain.enums import CallerType
from maa_api.domain.errors import AppError, ErrorCode
from maa_api.settings import AgentSettings, Settings, WorkspaceSettings

# --------------------------------------------------------------------------- #
# 夹具与假件
# --------------------------------------------------------------------------- #


def async_test(func):
    """本仓库不装 pytest-asyncio：异步用例统一用 ``asyncio.run`` 跑。"""

    def wrapper(*args, **kwargs):
        return asyncio.run(func(*args, **kwargs))

    wrapper.__name__ = func.__name__
    return wrapper


def settings_for(root: Path | str, **agent: Any) -> Settings:
    return Settings(
        workspace=WorkspaceSettings(root=str(root)),
        agent=AgentSettings(**agent),
    )


def build_registry(with_delegate: bool = True) -> ToolRegistry:
    """装配真的工具注册表（不走 ``build_registry()``，避免与并行任务抢文件）。"""

    item = ToolRegistry()
    explore.register_tools(item)
    if with_delegate:
        delegate.register_tools(item)
    return item


def context(**state: Any) -> ToolContext:
    return ToolContext(
        CallerType.INTERNAL,
        None,
        None,
        SimpleNamespace(app=SimpleNamespace(state=SimpleNamespace(**state))),
        None,
    )


def call(arguments: dict[str, Any], ctx: ToolContext) -> Any:
    item = build_registry().get("delegate_recon")
    return asyncio.run(item.handler(item.params_model.model_validate(arguments), ctx))


def error_of(arguments: dict[str, Any], ctx: ToolContext) -> AppError:
    with pytest.raises(AppError) as info:
        call(arguments, ctx)
    return info.value


@pytest.fixture
def ws(tmp_path: Path) -> Path:
    root = tmp_path / "ws"
    root.mkdir()
    return root


class FakeRuntime:
    """假的 ``AgentRuntime``：记录 ``recon`` 的参数并按给定报告回。"""

    def __init__(self, report: dict[str, Any] | None = None) -> None:
        self.calls: list[dict[str, Any]] = []
        self.report = report if report is not None else {"summary": "假报告", "findings": []}

    async def recon(
        self,
        question: str,
        *,
        hints: list[str] | None = None,
        max_steps: int | None = None,
        settings: Settings | None = None,
    ) -> dict[str, Any]:
        self.calls.append(
            {
                "question": question,
                "hints": list(hints or []),
                "max_steps": max_steps,
                "settings": settings,
            }
        )
        return self.report


class FakeRunnerRuntime:
    """假的 runtime：照 ``AgentRuntime.recon`` 的口径把参数转给 ``run_recon``。"""

    def __init__(self, client: "FakeLLMClient") -> None:
        self.client = client
        self.calls = 0

    async def recon(
        self,
        question: str,
        *,
        hints: list[str] | None = None,
        max_steps: int | None = None,
        settings: Settings | None = None,
    ) -> dict[str, Any]:
        self.calls += 1
        resolved = settings if settings is not None else Settings()
        budget = budget_from_settings(resolved)
        steps = max_steps if isinstance(max_steps, int) and max_steps > 0 else budget.max_steps
        return await run_recon(
            ReconDeps(
                settings=resolved,
                client=self.client,
                model=resolved.llm.model,
                max_steps=steps,
                max_chars=budget.max_chars,
                output_chars=budget.output_chars,
            ),
            question=question,
            hints=hints or [],
        )


class FakeLLMClient:
    """假 LLM：每轮弹出一组 chunk，用完后重复 ``fallback``；记录每次请求。"""

    def __init__(
        self,
        turns: list[list[dict[str, Any]]] | None = None,
        *,
        fallback: list[dict[str, Any]] | None = None,
    ) -> None:
        self._turns = list(turns or [])
        self._fallback = list(fallback or [])
        self.calls: list[dict[str, Any]] = []

    async def stream_chat_completion(
        self, model: str, messages: list[dict[str, Any]], tools: list[dict[str, Any]]
    ):
        self.calls.append(
            {
                "model": model,
                "messages": copy.deepcopy(messages),
                "tools": copy.deepcopy(tools),
            }
        )
        chunks = self._turns.pop(0) if self._turns else list(self._fallback)
        for chunk in chunks:
            yield chunk


def text_chunk(text: str) -> dict[str, Any]:
    return {"choices": [{"delta": {"content": text}}]}


def tool_call_chunk(
    *,
    index: int = 0,
    call_id: str | None = None,
    name: str | None = None,
    arguments: str | None = None,
) -> dict[str, Any]:
    function: dict[str, Any] = {}
    if name is not None:
        function["name"] = name
    if arguments is not None:
        function["arguments"] = arguments
    entry: dict[str, Any] = {"index": index, "type": "function", "function": function}
    if call_id is not None:
        entry["id"] = call_id
    return {"choices": [{"delta": {"tool_calls": [entry]}}]}


def usage_chunk(prompt: int = 10, completion: int = 5) -> dict[str, Any]:
    return {"choices": [], "usage": {"prompt_tokens": prompt, "completion_tokens": completion}}


class StepScript:
    """注入 ``run_recon`` 的假步进函数：按剧本回 :class:`ReconStep`，记录每次看到的 messages。"""

    def __init__(self, steps: list[ReconStep]) -> None:
        self.steps = list(steps)
        self.seen: list[list[dict[str, Any]]] = []

    async def __call__(
        self, deps: ReconDeps, messages: list[dict[str, Any]], schemas: list[dict[str, Any]]
    ) -> ReconStep:
        self.seen.append(copy.deepcopy(messages))
        if not self.steps:
            return ReconStep(text="没有更多步骤")
        return self.steps.pop(0)


def recording_registry(calls: list[dict[str, Any]]) -> ToolRegistry:
    """三个同名工具的表：handler 记录调用并回一个短结果，不用真工作区。"""

    registry = ToolRegistry()
    for name, model in (
        ("list_files", explore.ListFilesParams),
        ("read_file", explore.ReadFileParams),
        ("search_code", explore.SearchCodeParams),
    ):
        registry.register(
            ToolDefinition(
                name=name,
                group="recon",
                risk=ToolRisk.SAFE,
                description=f"假的 {name}",
                params_model=model,
                handler=_recorder(name, calls),
                read_only=True,
            )
        )
    return registry


def _recorder(name: str, calls: list[dict[str, Any]]):
    async def handler(params: Any, ctx: ToolContext) -> dict[str, Any]:
        calls.append({"name": name, "arguments": params.model_dump()})
        return {"ok": True, "tool": name}

    return handler


def deps_for(
    settings: Settings, client: FakeLLMClient | None = None, **overrides: Any
) -> ReconDeps:
    budget = budget_from_settings(settings)
    values: dict[str, Any] = {
        "settings": settings,
        "client": client if client is not None else FakeLLMClient(),
        "model": settings.llm.model,
        "max_steps": budget.max_steps,
        "max_chars": budget.max_chars,
        "output_chars": budget.output_chars,
    }
    values.update(overrides)
    return ReconDeps(**values)


def tool_messages(messages: list[dict[str, Any]]) -> list[dict[str, Any]]:
    return [message for message in messages if message.get("role") == "tool"]


# --------------------------------------------------------------------------- #
# 注册元数据与参数
# --------------------------------------------------------------------------- #


def test_delegate_recon_metadata_is_safe_read_only_and_in_the_recon_group() -> None:
    registry = build_registry()
    item = registry.get("delegate_recon")
    assert item.name == "delegate_recon"
    assert item.group == "recon"
    assert item.risk is ToolRisk.SAFE
    assert item.read_only is True
    assert item.handler is delegate.delegate_recon
    assert item.params_model is delegate.DelegateReconParams
    assert [item.name for item in registry.list("recon")] == [
        "list_files",
        "read_file",
        "search_code",
        "delegate_recon",
    ]
    assert registry.list("recon")[-1] is item


def test_delegate_recon_params_require_question_and_bound_hints_and_steps() -> None:
    model = delegate.DelegateReconParams
    assert model(question="Q").hints == []
    assert model(question="Q").max_steps is None
    assert model(question="Q", hints=["a"], max_steps=20).max_steps == 20
    assert model(question="Q" * 500).question == "Q" * 500

    with pytest.raises(ValidationError):
        model()
    with pytest.raises(ValidationError):
        model(question="")
    with pytest.raises(ValidationError):
        model(question="Q" * 501)
    with pytest.raises(ValidationError):
        model(question="Q", hints=["a"] * (delegate.MAX_HINTS + 1))
    with pytest.raises(ValidationError):
        model(question="Q", max_steps=0)
    with pytest.raises(ValidationError):
        model(question="Q", max_steps=21)
    with pytest.raises(ValidationError):
        model(question="Q", unknown=True)


def test_delegate_recon_description_tells_the_parent_to_trust_only_evidence() -> None:
    """§5 对策：父 agent 侧的描述必须写明「没有证据的结论不要当事实」。"""

    description = build_registry().get("delegate_recon").description
    assert "只读" in description
    assert "证据" in description
    assert "不要当成事实" in description


# --------------------------------------------------------------------------- #
# handler：取依赖与参数透传
# --------------------------------------------------------------------------- #


def test_handler_passes_question_hints_and_steps_to_the_runtime(ws: Path) -> None:
    runtime = FakeRuntime()
    settings = settings_for(ws)

    payload = call(
        {"question": "这个报错从哪来", "hints": ["maa_api/agent", "errors.py"], "max_steps": 3},
        context(agent_runtime=runtime, settings_provider=lambda: settings),
    )

    assert runtime.calls == [
        {
            "question": "这个报错从哪来",
            "hints": ["maa_api/agent", "errors.py"],
            "max_steps": 3,
            "settings": settings,
        }
    ]
    assert payload == runtime.report


def test_handler_awaits_an_async_settings_provider(ws: Path) -> None:
    """M56 热修：装配层接的 ``SettingService.refresh`` 返回协程，必须 await。"""

    runtime = FakeRuntime()
    settings = settings_for(ws)

    async def provider():
        return settings

    payload = call(
        {"question": "这个报错从哪来"},
        context(agent_runtime=runtime, settings_provider=provider),
    )

    assert runtime.calls[0]["settings"] is settings
    assert payload == runtime.report


def test_handler_leaves_max_steps_unset_for_the_server_default(ws: Path) -> None:
    runtime = FakeRuntime()

    call(
        {"question": "只看一眼"},
        context(agent_runtime=runtime, settings_provider=lambda: settings_for(ws)),
    )

    assert runtime.calls[0]["max_steps"] is None
    assert runtime.calls[0]["hints"] == []


def test_handler_reports_agent_disabled_when_runtime_or_settings_are_missing(ws: Path) -> None:
    settings = settings_for(ws)
    runtime = FakeRuntime()

    assert (
        error_of({"question": "Q"}, context(settings_provider=lambda: settings)).code
        is ErrorCode.AGENT_DISABLED
    )
    assert (
        error_of({"question": "Q"}, context(agent_runtime=runtime)).code
        is ErrorCode.AGENT_DISABLED
    )
    assert error_of({"question": "Q"}, context()).code is ErrorCode.AGENT_DISABLED
    assert (
        error_of(
            {"question": "Q"}, context(agent_runtime=None, settings_provider=None)
        ).code
        is ErrorCode.AGENT_DISABLED
    )


def test_delegate_recon_reports_unconfigured_workspace() -> None:
    """第四个工具在 ``workspace.root`` 为空时同样报「未配置工作区」（规格 D1）。"""

    runtime = FakeRunnerRuntime(FakeLLMClient())
    err = error_of(
        {"question": "Q"},
        context(agent_runtime=runtime, settings_provider=lambda: Settings()),
    )
    assert err.code is ErrorCode.WORKSPACE_NOT_CONFIGURED
    assert runtime.calls == 1


# --------------------------------------------------------------------------- #
# run_recon：未配置工作区
# --------------------------------------------------------------------------- #


def test_run_recon_refuses_an_unconfigured_workspace_before_calling_the_model() -> None:
    client = FakeLLMClient(fallback=[text_chunk("不该被调用")])
    deps = deps_for(Settings(), client)

    with pytest.raises(AppError) as info:
        asyncio.run(run_recon(deps, question="Q"))

    assert info.value.code is ErrorCode.WORKSPACE_NOT_CONFIGURED
    assert client.calls == []


# --------------------------------------------------------------------------- #
# run_recon：正常出报告（默认步进函数 + 流式假客户端）
# --------------------------------------------------------------------------- #

REPORT = {
    "summary": "报错来自 runner.py 的 dispatch_tool。",
    "findings": [
        {
            "title": "dispatch_tool 把工具结果压成一句 JSON 文本",
            "detail": "它先造一个只带 settings_provider 的最小 request，再执行工具。",
            "evidence": ["maa_api/agent/recon/runner.py:144"],
        }
    ],
    "uncertainties": ["没有验证真实 LLM 的行为"],
}


def test_run_recon_produces_the_report_from_a_streaming_client(ws: Path) -> None:
    (ws / "needle.txt").write_text("NEEDLE_TOKEN 在这里\n", encoding="utf-8")
    settings = settings_for(
        ws, recon_max_steps=5, recon_output_chars=1000, recon_max_chars=100_000
    )
    client = FakeLLMClient(
        [
            [
                tool_call_chunk(
                    call_id="call_1",
                    name="search_code",
                    arguments='{"pattern": "NEEDLE_',
                ),
                usage_chunk(),
                tool_call_chunk(arguments='TOKEN", "mode": "content"}'),
            ],
            [text_chunk(json.dumps(REPORT, ensure_ascii=False)), usage_chunk()],
        ]
    )

    report = asyncio.run(
        run_recon(
            deps_for(settings, client),
            question="这个报错从哪来",
            hints=["先看 runner.py"],
            registry=build_registry(with_delegate=False),
        )
    )

    assert report["question"] == "这个报错从哪来"
    assert report["steps_used"] == 2
    assert report["stopped"] == "answered"
    assert report["summary"] == REPORT["summary"]
    assert report["findings"] == [
        {
            "title": REPORT["findings"][0]["title"],
            "detail": REPORT["findings"][0]["detail"],
            "evidence": ["maa_api/agent/recon/runner.py:144"],
        }
    ]
    assert report["uncertainties"] == ["没有验证真实 LLM 的行为"]
    assert report["budget"]["max_steps"] == 5
    assert report["budget"]["max_chars"] == 100_000
    assert report["budget"]["output_chars"] == 1000
    assert report["budget"]["exhausted"] is False
    assert report["budget"]["chars_used"] > 0

    # 第一次请求：系统提示 + 问题块，schema 只有三个只读工具
    assert client.calls[0]["model"] == settings.llm.model
    first = client.calls[0]["messages"]
    assert [message["role"] for message in first] == ["system", "user"]
    assert "只读侦察员" in first[0]["content"]
    assert "path:line" in first[0]["content"] or "路径:行号" in first[0]["content"]
    assert "这个报错从哪来" in first[1]["content"]
    assert "先看 runner.py" in first[1]["content"]
    assert {tool["function"]["name"] for tool in client.calls[0]["tools"]} == {
        "list_files",
        "read_file",
        "search_code",
    }

    # 第二次请求：分片的 tool_calls 被拼成一个完整调用，工具结果以 tool 消息回灌
    second = client.calls[1]["messages"]
    assert [message["role"] for message in second] == ["system", "user", "assistant", "tool"]
    assert second[2]["tool_calls"][0]["id"] == "call_1"
    assert second[2]["tool_calls"][0]["type"] == "function"
    assert second[2]["tool_calls"][0]["function"]["name"] == "search_code"
    assert json.loads(second[2]["tool_calls"][0]["function"]["arguments"]) == {
        "pattern": "NEEDLE_TOKEN",
        "mode": "content",
    }
    assert second[3]["tool_call_id"] == "call_1"
    payload = json.loads(second[3]["content"])
    assert [match["path"] for match in payload["matches"]] == ["needle.txt"]
    assert payload["matches"][0]["line"] == 1


def test_sub_agent_only_sees_the_three_read_only_schemas() -> None:
    schemas = recon_tool_schemas(build_registry(with_delegate=True))

    assert {item["function"]["name"] for item in schemas} == {
        "list_files",
        "read_file",
        "search_code",
    }
    assert all(item["type"] == "function" for item in schemas)
    assert all(
        item["function"]["parameters"]["additionalProperties"] is False for item in schemas
    )


def test_assistant_tool_calls_sent_back_follow_the_openai_shape(ws: Path) -> None:
    (ws / "a.txt").write_text("x\n", encoding="utf-8")
    settings = settings_for(ws, recon_max_steps=2)
    script = StepScript(
        [
            ReconStep(text="", calls=[{"id": "call_1", "name": "list_files", "arguments": "{}"}]),
            ReconStep(text=json.dumps({"summary": "s", "findings": [], "uncertainties": []})),
        ]
    )

    asyncio.run(run_recon(deps_for(settings), question="Q", registry=build_registry(), step_runner=script))

    assistant = [
        message for message in script.seen[1] if message.get("role") == "assistant"
    ][0]
    call = assistant["tool_calls"][0]
    assert call["type"] == "function"
    assert call["function"] == {"name": "list_files", "arguments": "{}"}


# --------------------------------------------------------------------------- #
# run_recon：三层预算与收尾
# --------------------------------------------------------------------------- #


def test_run_recon_stops_at_max_steps_and_says_so(ws: Path) -> None:
    settings = settings_for(ws)
    dispatched: list[dict[str, Any]] = []
    script = StepScript(
        [
            ReconStep(text="", calls=[{"id": "c", "name": "list_files", "arguments": "{}"}])
            for _ in range(3)
        ]
    )

    report = asyncio.run(
        run_recon(
            deps_for(settings, max_steps=3, max_chars=100_000, output_chars=1000),
            question="Q",
            registry=recording_registry(dispatched),
            step_runner=script,
        )
    )

    assert len(script.seen) == 3
    assert len(dispatched) == 3
    assert dispatched[0] == {"name": "list_files", "arguments": {"path": ".", "depth": 1, "pattern": None}}
    assert report["steps_used"] == 3
    assert report["stopped"] == "max_steps"
    assert report["budget"]["max_steps"] == 3
    assert report["budget"]["exhausted"] is False
    assert any("步数用尽" in item for item in report["uncertainties"])
    # 最后一步不是 JSON 报告：findings 空、摘要退化，但**不抛错**
    assert report["findings"] == []
    assert any("没有按约定输出 JSON" in item for item in report["uncertainties"])
    # 每一步都把上一步的工具结果带回去
    assert len(tool_messages(script.seen[1])) == 1
    assert len(tool_messages(script.seen[2])) == 2


def test_run_recon_executes_at_most_four_tool_calls_per_step(ws: Path) -> None:
    """一步里模型发一长串调用时，只执行前 ``MAX_CALLS_PER_STEP`` 个（预算护栏）。"""

    dispatched: list[dict[str, Any]] = []
    script = StepScript(
        [
            ReconStep(
                text="",
                calls=[
                    {"id": f"c{index}", "name": "list_files", "arguments": "{}"}
                    for index in range(MAX_CALLS_PER_STEP + 2)
                ],
            ),
            ReconStep(text=json.dumps({"summary": "s", "findings": [], "uncertainties": []})),
        ]
    )

    asyncio.run(
        run_recon(
            deps_for(settings_for(ws)),
            question="Q",
            registry=recording_registry(dispatched),
            step_runner=script,
        )
    )

    assert len(dispatched) == MAX_CALLS_PER_STEP
    assert len(tool_messages(script.seen[1])) == MAX_CALLS_PER_STEP


def test_run_recon_ignores_tool_calls_outside_the_read_only_three(ws: Path) -> None:
    """子 agent 只能碰三个只读工具：别的名字不派发、也不写进回灌的对话。"""

    dispatched: list[dict[str, Any]] = []
    script = StepScript(
        [
            ReconStep(
                text="",
                calls=[
                    {"id": "c1", "name": "raw_screenshot", "arguments": "{}"},
                    {"id": "c2", "name": "delegate_recon", "arguments": '{"question": "Q"}'},
                    {"id": "c3", "name": "run_pipeline", "arguments": "{}"},
                ],
            )
        ]
    )

    report = asyncio.run(
        run_recon(
            deps_for(settings_for(ws)),
            question="Q",
            registry=recording_registry(dispatched),
            step_runner=script,
        )
    )

    assert dispatched == []
    assert report["steps_used"] == 1
    assert report["stopped"] == "answered"
    assert len(script.seen) == 1
    assert tool_messages(script.seen[0]) == []


def test_run_recon_stops_when_the_character_budget_is_gone(ws: Path) -> None:
    settings = settings_for(ws, recon_max_steps=8, recon_output_chars=1000, recon_max_chars=10_000)
    budget = budget_from_settings(settings)
    assert (budget.max_steps, budget.max_chars, budget.output_chars) == (8, 10_000, 1000)

    dispatched: list[dict[str, Any]] = []
    script = StepScript(
        [
            ReconStep(
                text="甲" * 10_500,
                calls=[{"id": "c", "name": "list_files", "arguments": "{}"}],
            )
        ]
    )

    report = asyncio.run(
        run_recon(
            deps_for(settings),
            question="Q",
            registry=recording_registry(dispatched),
            step_runner=script,
        )
    )

    assert len(script.seen) == 1
    assert dispatched == []  # 预算在派发工具之前就用尽
    assert report["steps_used"] == 1
    assert report["stopped"] == "budget"
    assert report["budget"]["exhausted"] is True
    assert report["budget"]["chars_used"] >= 10_000
    assert any("预算用尽" in item for item in report["uncertainties"])
    assert report["summary"] == "甲" * 1500  # D5：summary 截到 1500


def test_run_recon_degrades_to_the_final_text_when_json_is_missing(ws: Path) -> None:
    script = StepScript([ReconStep(text="没查清：文件读不了，卡在权限上。")])

    report = asyncio.run(
        run_recon(
            deps_for(settings_for(ws)),
            question="Q",
            registry=build_registry(),
            step_runner=script,
        )
    )

    assert report["stopped"] == "answered"
    assert report["steps_used"] == 1
    assert report["summary"] == "没查清：文件读不了，卡在权限上。"
    assert report["findings"] == []
    assert any("没有按约定输出 JSON" in item for item in report["uncertainties"])


def test_run_recon_parses_a_fenced_json_report(ws: Path) -> None:
    body = json.dumps(
        {"summary": "围栏里的结论", "findings": [], "uncertainties": ["没看到迁移脚本"]},
        ensure_ascii=False,
    )
    script = StepScript([ReconStep(text=f"先给结论：\n```json\n{body}\n```\n以上。")])

    report = asyncio.run(
        run_recon(
            deps_for(settings_for(ws)),
            question="Q",
            registry=build_registry(),
            step_runner=script,
        )
    )

    assert report["summary"] == "围栏里的结论"
    assert report["uncertainties"] == ["没看到迁移脚本"]
    assert report["stopped"] == "answered"


def test_report_keeps_findings_without_evidence_but_marks_nothing(ws: Path) -> None:
    """§5 的「无 evidence 时保留」照做；实现没有额外的「标记」字段（W3 报告记了一笔）。"""

    body = json.dumps(
        {
            "summary": "s",
            "findings": [{"title": "无证据的结论", "detail": "凭印象"}],
            "uncertainties": [],
        },
        ensure_ascii=False,
    )
    script = StepScript([ReconStep(text=body)])

    report = asyncio.run(
        run_recon(
            deps_for(settings_for(ws)),
            question="Q",
            registry=build_registry(),
            step_runner=script,
        )
    )

    assert report["findings"] == [
        {"title": "无证据的结论", "detail": "凭印象", "evidence": []}
    ]


def test_report_caps_summary_detail_evidence_findings_and_uncertainties(ws: Path) -> None:
    """D5 的边界：summary ≤ 1500、每条 title ≤ 200 / detail ≤ 600 / evidence ≤ 4、findings ≤ 8。"""

    body = json.dumps(
        {
            "summary": "结" * 2000,
            "findings": [
                {
                    "title": f"标题{index}" + "长" * 300,
                    "detail": "细" * 1000,
                    "evidence": [f"path{index}.py:{index}" for index in range(6)],
                }
                for index in range(10)
            ],
            "uncertainties": [f"不确定{index}" for index in range(9)],
        },
        ensure_ascii=False,
    )
    script = StepScript([ReconStep(text=body)])

    report = asyncio.run(
        run_recon(
            deps_for(settings_for(ws)),
            question="Q",
            registry=build_registry(),
            step_runner=script,
        )
    )

    assert len(report["summary"]) == 1500
    assert len(report["findings"]) == 8
    first = report["findings"][0]
    assert first["title"].startswith("标题0")
    assert len(first["title"]) == 200
    assert len(first["detail"]) == 600
    assert first["evidence"] == ["path0.py:0", "path1.py:1", "path2.py:2", "path3.py:3"]
    assert len(report["uncertainties"]) == 6


# --------------------------------------------------------------------------- #
# 父上下文只拿到报告（D5 的正面断言）
# --------------------------------------------------------------------------- #


def test_parent_context_only_receives_the_report_not_file_bodies(ws: Path) -> None:
    """子 agent 读到的长文件正文**不进**父上下文，但报告里的证据路径要在。"""

    marker = "PARENT_MUST_NOT_SEE_ME_9F3"
    lines = [f"line {index:03d} " + "填" * 40 for index in range(120)]
    lines[60] = f"line 060 {marker} " + "填" * 40
    text = "\n".join(lines) + "\n"
    (ws / "long.txt").write_text(text, encoding="utf-8")
    settings = settings_for(ws)

    script = StepScript(
        [
            ReconStep(
                text="",
                calls=[
                    {
                        "id": "call_1",
                        "name": "read_file",
                        "arguments": json.dumps({"path": "long.txt"}),
                    }
                ],
            ),
            ReconStep(
                text=json.dumps(
                    {
                        "summary": "long.txt 里藏着关键逻辑。",
                        "findings": [
                            {
                                "title": "关键逻辑在 long.txt 中段",
                                "detail": "第 61 行是入口。",
                                "evidence": ["long.txt:61"],
                            }
                        ],
                        "uncertainties": [],
                    },
                    ensure_ascii=False,
                )
            ),
        ]
    )

    report = asyncio.run(
        run_recon(
            deps_for(settings),
            question="关键逻辑在哪",
            registry=build_registry(with_delegate=False),
            step_runner=script,
        )
    )

    # 正对照：文件正文**确实**进了子 agent 的对话（否则下面的断言就是空的）
    sub_agent_tool_message = tool_messages(script.seen[1])[-1]["content"]
    assert marker in sub_agent_tool_message
    assert f"line 060 {marker}" in sub_agent_tool_message
    assert report["budget"]["chars_used"] >= len(text)

    # 父上下文：只有报告，没有正文
    serialized = json.dumps(report, ensure_ascii=False)
    assert marker not in serialized
    assert "line 060" not in serialized
    assert "填" * 40 not in serialized
    assert set(report) == {
        "question",
        "summary",
        "findings",
        "uncertainties",
        "steps_used",
        "stopped",
        "budget",
    }
    assert report["findings"][0]["evidence"] == ["long.txt:61"]
    assert report["steps_used"] == 2


# --------------------------------------------------------------------------- #
# dispatch_tool 的边界
# --------------------------------------------------------------------------- #


def test_dispatch_tool_turns_app_errors_into_text_for_the_sub_agent() -> None:
    """被拒绝也是侦察结论：``AppError`` 回成 JSON 文本，不把异常抛进子循环。"""

    registry = ToolRegistry()

    async def boom(params: Any, ctx: ToolContext) -> dict[str, Any]:
        raise AppError(ErrorCode.NOT_FOUND, "工作区里没有这个路径：nope.txt")

    registry.register(
        ToolDefinition(
            name="list_files",
            group="recon",
            risk=ToolRisk.SAFE,
            description="假的",
            params_model=explore.ListFilesParams,
            handler=boom,
            read_only=True,
        )
    )

    payload = asyncio.run(dispatch_tool(registry, Settings(), "list_files", {}))

    assert json.loads(payload) == {
        "error": "NOT_FOUND",
        "message": "工作区里没有这个路径：nope.txt",
    }


def test_dispatch_tool_gives_the_sub_agent_a_real_workspace_listing(ws: Path) -> None:
    """派发要真的把设置交给工具：子 agent 拿到的是列举结果，不是 ``AGENT_DISABLED``。"""

    (ws / "a.txt").write_text("x", encoding="utf-8")

    payload = asyncio.run(
        dispatch_tool(build_registry(with_delegate=False), settings_for(ws), "list_files", {})
    )

    decoded = json.loads(payload)
    assert "error" not in decoded
    assert [entry["path"] for entry in decoded["entries"]] == ["a.txt"]
    assert decoded["entries"][0]["bytes"] == 1


def test_dispatch_tool_does_not_expose_a_dead_workspace(ws: Path) -> None:
    """没有工作区时也走同一口径：回 ``WORKSPACE_NOT_CONFIGURED`` 的文本。"""

    payload = asyncio.run(
        dispatch_tool(build_registry(with_delegate=False), Settings(), "list_files", {})
    )

    decoded = json.loads(payload)
    assert decoded["error"] == "WORKSPACE_NOT_CONFIGURED"


# --------------------------------------------------------------------------- #
# AgentRuntime.recon 的接线
# --------------------------------------------------------------------------- #


def test_agent_runtime_recon_uses_the_configured_budget_and_clamps_steps(ws: Path) -> None:
    from maa_api.agent.runtime import RECON_MAX_STEPS_CAP, AgentRuntime

    settings = settings_for(ws, recon_max_steps=2)
    client = FakeLLMClient(
        fallback=[tool_call_chunk(call_id="c", name="list_files", arguments="{}")]
    )
    runtime = AgentRuntime(
        None,
        None,
        settings_provider=lambda: settings,
        client_factory=lambda resolved: client,
    )

    first = asyncio.run(runtime.recon("Q1"))

    assert first["steps_used"] == 2  # 没给 max_steps 时取 agent.recon_max_steps
    assert first["stopped"] == "max_steps"
    assert len(client.calls) == 2
    assert client.calls[0]["model"] == settings.llm.model
    assert {tool["function"]["name"] for tool in client.calls[0]["tools"]} == {
        "list_files",
        "read_file",
        "search_code",
    }

    second = asyncio.run(runtime.recon("Q2", max_steps=25))

    assert RECON_MAX_STEPS_CAP == 20
    assert second["budget"]["max_steps"] == 20
    assert second["steps_used"] == 20
    assert len(client.calls) == 22
