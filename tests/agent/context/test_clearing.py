"""M74 T5：工具结果清理与 token 预算（spec §4.2.3、§4.2.4、§6 的六条用例）。

边界、候选与占位都是纯函数；最后一个用例把清理中间件接进真正的 ``AgentLoop``，
钉住溢出时的 ``finish_reason=context_overflow`` 映射。
"""

from __future__ import annotations

import asyncio
import json
from types import SimpleNamespace

import pytest

from maa_api.agent.context.clearing import (
    CLEARING_BATCH_ROUNDS,
    CLEARING_MIN_RESULT_CHARS,
    CLEARING_MIN_TOKENS,
    CONTEXT_OVERFLOW_MESSAGE,
    ToolResultClearingMiddleware,
    apply_clearing,
    clearing_placeholder,
    plan_clearing,
)
from maa_api.agent.context.layout import ContextLayout, ContextParts, parts_of
from maa_api.agent.context.tokens import (
    DEFAULT_OUTPUT_RESERVE,
    TokenEstimator,
    budget_for,
)
from maa_api.agent.harness.builtin_middleware import ContextLayoutMiddleware
from maa_api.agent.harness.context import (
    ModelRequest,
    ModelResponse,
    RunContext,
    RunOutcome,
)
from maa_api.agent.harness.loop import AgentLoop, MemoryTranscript
from maa_api.agent.harness.middleware import Middleware, MiddlewareChain
from maa_api.agent.llm.profiles import ResolvedProfile
from maa_api.agent.registry import ToolRegistry
from maa_api.agent.specs.registry import builtin_spec
from maa_api.domain.errors import AppError, ErrorCode


def _settings(*, clear_after: int = 3):
    return SimpleNamespace(
        context=SimpleNamespace(
            fallback_context_tokens=128000,
            compaction_ratio=0.75,
            keep_recent_turns=2,
            clear_tool_results_after=clear_after,
        )
    )


def _profile(
    *, window: int | None = None, max_output: int | None = None
) -> ResolvedProfile:
    return ResolvedProfile(
        name="clearing",
        kind="chat",
        provider="openai_compatible",
        base_url="https://llm.test/v1",
        api_key="sk-test",
        model="clearing-model",
        context_window=window,
        max_output_tokens=max_output,
        supports_vision=None,
        supports_tools=True,
        supports_json_schema=None,
        dynamic_context_position="tail_system",
        price_input=None,
        price_cached_input=None,
        price_output=None,
        currency="CNY",
        embedding_dim=None,
        local_model_path=None,
        readonly=False,
    )


class _Registry:
    """只关心 ``refetchable`` 的 registry 替身。"""

    def __init__(self, refetchable: set[str]) -> None:
        self._refetchable = set(refetchable)

    def get(self, name: str) -> SimpleNamespace:
        return SimpleNamespace(refetchable=name in self._refetchable)


def _round(round_index: int, tools: list[tuple[str, Any]]) -> list[dict]:
    """一个工具往返：带 ``tool_calls`` 的 assistant + 紧随的 tool 消息。"""
    calls = []
    messages: list[dict] = []
    replies: list[dict] = []
    for tool_index, (name, content) in enumerate(tools):
        call_id = f"call-{round_index}-{tool_index}"
        calls.append(
            {
                "id": call_id,
                "type": "function",
                "function": {"name": name, "arguments": "{}"},
            }
        )
        replies.append(
            {"role": "tool", "tool_call_id": call_id, "content": content}
        )
    messages.append({"role": "assistant", "content": None, "tool_calls": calls})
    messages.extend(replies)
    return messages


def _tool_history(
    rounds: int, *, content: Any = None, name: str = "get_logs"
) -> list[dict]:
    payload = content if content is not None else "x" * 3000
    history: list[dict] = []
    for index in range(rounds):
        history.extend(_round(index, [(name, payload)]))
    return history


def _refetchable(name: str) -> bool:
    return name == "get_logs"


def test_boundary_moves_in_batches_of_six() -> None:
    estimator = TokenEstimator()
    for rounds, expected in ((8, 0), (9, 6), (14, 6), (15, 12)):
        plan = plan_clearing(
            _tool_history(rounds),
            keep_rounds=3,
            is_refetchable=_refetchable,
            estimator=estimator,
        )
        assert plan.boundary == expected, rounds
    # 边界按 6 个往返成批推进（`CLEARING_BATCH_ROUNDS` 是唯一来源）。
    assert CLEARING_BATCH_ROUNDS == 6


def test_only_large_refetchable_ok_results_are_cleared() -> None:
    history: list[dict] = []
    index_of: dict[str, int] = {}

    def add(round_index: int, tools: list[tuple[str, str, str]]) -> None:
        calls = []
        assistant_index = len(history)
        history.append({"role": "assistant", "content": None, "tool_calls": calls})
        for tool_index, (call_id, name, content) in enumerate(tools):
            calls.append(
                {
                    "id": call_id,
                    "type": "function",
                    "function": {"name": name, "arguments": "{}"},
                }
            )
            index_of[call_id] = len(history)
            history.append(
                {"role": "tool", "tool_call_id": call_id, "content": content}
            )
        assert history[assistant_index]["tool_calls"] is calls

    error_content = json.dumps(
        {"error": {"code": "X", "message": "e" * 400}}, ensure_ascii=False
    )
    add(
        0,
        [
            ("call-ok", "get_logs", "x" * 3000),
            ("call-side-effect", "submit_pipeline", "y" * 3000),
            ("call-small", "get_logs", "z" * 100),
            ("call-error", "get_logs", error_content),
        ],
    )
    for index in range(1, 6):
        add(index, [(f"call-ok-{index}", "get_logs", "x" * 3000)])
    for index in range(6, 9):
        add(index, [(f"call-recent-{index}", "get_logs", "x" * 3000)])

    plan = plan_clearing(
        history,
        keep_rounds=3,
        is_refetchable=_refetchable,
        estimator=TokenEstimator(),
    )
    assert plan.boundary == 6
    cleared_indexes = {
        index_of["call-ok"],
        *(index_of[f"call-ok-{index}"] for index in range(1, 6)),
    }
    assert set(plan.message_indexes) == cleared_indexes
    # 不可重取、≤ 200 字、错误结果三类都保留原文；最近 3 个往返也不动。
    for call_id in (
        "call-side-effect",
        "call-small",
        "call-error",
        "call-recent-6",
        "call-recent-7",
        "call-recent-8",
    ):
        assert index_of[call_id] not in plan.message_indexes

    cleared = apply_clearing(history, plan)
    assert cleared[index_of["call-ok"]]["content"].startswith(
        "[旧结果已清理：get_logs，原 3000 字"
    )
    assert cleared[index_of["call-side-effect"]]["content"] == "y" * 3000
    assert cleared[index_of["call-small"]]["content"] == "z" * 100
    assert cleared[index_of["call-error"]]["content"] == error_content
    # 不改库里的原历史。
    assert history[index_of["call-ok"]]["content"] == "x" * 3000
    # 保留 role / tool_call_id，name 只在原来有时保留。
    assert cleared[index_of["call-ok"]].keys() >= {"role", "tool_call_id", "content"}
    assert "name" not in cleared[index_of["call-ok"]]
    assert CLEARING_MIN_RESULT_CHARS == 200


def test_small_batches_are_left_alone() -> None:
    history = _tool_history(9, content="x" * 300)
    plan = plan_clearing(
        history,
        keep_rounds=3,
        is_refetchable=_refetchable,
        estimator=TokenEstimator(),
    )
    assert plan.boundary == 6
    # 6 × 300 字的估算远不到 4 000 token。
    assert plan.message_indexes == ()
    assert CLEARING_MIN_TOKENS == 4000


def _json_with_length(base: dict, length: int) -> str:
    empty = json.dumps(
        {**base, "padding": ""}, ensure_ascii=False, separators=(",", ":")
    )
    padding = length - len(empty)
    assert padding >= 0
    text = json.dumps(
        {**base, "padding": "x" * padding},
        ensure_ascii=False,
        separators=(",", ":"),
    )
    assert len(text) == length
    return text


def test_placeholder_formats() -> None:
    plain = {"role": "tool", "tool_call_id": "c-1", "content": "x" * 12345}
    assert clearing_placeholder(plain, "get_logs") == (
        "[旧结果已清理：get_logs，原 12345 字；需要时重新调用]"
    )
    assert clearing_placeholder(plain, "get_logs") == clearing_placeholder(
        plain, "get_logs"
    )

    screenshot = {
        "role": "tool",
        "tool_call_id": "c-2",
        "content": _json_with_length(
            {"captured_at": "2026-11-02T16:20:11+08:00"}, 2104
        ),
    }
    assert clearing_placeholder(screenshot, "get_screenshot") == (
        "[旧结果已清理：get_screenshot，原 2104 字，"
        "截图时间 2026-11-02T16:20:11+08:00；需要时重新调用]"
    )

    artifact = {
        "role": "tool",
        "tool_call_id": "c-3",
        "content": _json_with_length(
            {"artifact_id": "6f1c2a9e-0b7d-4c1e-9a52-3d8f0e6b1a24"}, 4630
        ),
    }
    assert clearing_placeholder(artifact, "get_prts_page") == (
        "[旧结果已清理：get_prts_page，原 4630 字，"
        "全文在产物 6f1c2a9e-…；用 read_artifact 读取]"
    )

    # captured_at 在 result 里同样识别；数组 content 的字数取 text 部分之和。
    nested = {
        "role": "tool",
        "tool_call_id": "c-4",
        "content": json.dumps(
            {"result": {"captured_at": "2026-11-02T16:20:11+08:00", "x": "y" * 100}},
            ensure_ascii=False,
        ),
    }
    assert "截图时间 2026-11-02T16:20:11+08:00" in clearing_placeholder(
        nested, "get_screenshot"
    )
    array = {
        "role": "tool",
        "tool_call_id": "c-5",
        "content": [
            {"type": "text", "text": "a" * 120},
            {"type": "text", "text": "b" * 80},
            {"type": "image_url", "image_url": {"url": "data:image/png;base64,AA"}},
        ],
    }
    assert clearing_placeholder(array, "get_screenshot") == (
        "[旧结果已清理：get_screenshot，原 200 字；需要时重新调用]"
    )


def test_clearing_is_stable_between_batches() -> None:
    settings = _settings()
    profile = _profile()
    layout = ContextLayout(TokenEstimator())
    app = SimpleNamespace(
        state=SimpleNamespace(context_layout=layout)
    )
    ctx = RunContext(
        run_id="run-stable",
        root_run_id="run-stable",
        parent_run_id=None,
        session_id="session-stable",
        ticket_id=None,
        spec=builtin_spec("general"),
        app=app,
    )
    middleware = ToolResultClearingMiddleware(
        registry=_Registry({"get_logs"}),
        settings_provider=lambda: settings,
    )

    plans = []
    for rounds in range(9, 15):
        history = _tool_history(rounds)
        parts = ContextParts(
            history=history, tools=[], position="tail_system"
        )
        req = ModelRequest(
            messages=[], tools=[], profile=profile, estimated_tokens=1
        )
        req.meta["layout_parts"] = parts
        req = asyncio.run(middleware.before_model(ctx, req))
        plans.append(
            plan_clearing(
                history,
                keep_rounds=3,
                is_refetchable=_refetchable,
                estimator=layout.estimator,
            )
        )
        # 每次请求的最终渲染都是「同样的前 6 个往返占位 + 更长的尾部」。
        assert parts_of(req).history[:8] == apply_clearing(
            history, plans[-1]
        )[:8]

    assert {plan.boundary for plan in plans} == {6}
    assert {plan.message_indexes for plan in plans} == {
        (1, 3, 5, 7, 9, 11)
    }
    # 六次请求之间前缀没有断点（§5.2 第 3、5 条）。
    assert ctx.state["context_layout"].get("breaks", 0) == 0


class _Recorder(Middleware):
    name = "recorder"

    def __init__(self) -> None:
        self.outcomes: list[tuple[str, str]] = []

    async def on_run_end(self, ctx: RunContext, outcome: RunOutcome) -> None:
        self.outcomes.append((outcome.status, outcome.finish_reason))


class _Router:
    def __init__(self, profile: ResolvedProfile) -> None:
        self.profile = profile

    async def resolve_for_spec(
        self, spec: object, *, session_model: str | None = None,
        session_base_url: str | None = None,
    ) -> ResolvedProfile:
        return self.profile


def test_overflow_after_clearing_fails_the_run() -> None:
    profile = _profile(window=8000)
    settings = _settings()
    budget = budget_for(profile, settings)
    assert budget.window == 8000
    assert budget.output_reserve == 2000
    assert budget.overflow_tokens == 6000

    recorder = _Recorder()
    layout = ContextLayout(TokenEstimator())
    app = SimpleNamespace(
        state=SimpleNamespace(
            context_layout=layout,
            model_router=_Router(profile),
        )
    )
    ctx = RunContext(
        run_id="run-overflow",
        root_run_id="run-overflow",
        parent_run_id=None,
        session_id="session-overflow",
        ticket_id=None,
        spec=builtin_spec("general"),
        app=app,
    )
    transcript = MemoryTranscript(
        [{"role": "user", "content": "刷" * 30000}]
    )

    async def call_model(run_ctx: RunContext, req: ModelRequest) -> ModelResponse:
        raise AssertionError("溢出时不应该发起模型调用")

    loop = AgentLoop(
        chain=MiddlewareChain(
            [
                ContextLayoutMiddleware(),
                ToolResultClearingMiddleware(
                    registry=_Registry({"get_logs"}),
                    settings_provider=lambda: settings,
                ),
                recorder,
            ]
        ),
        transcript=transcript,
        call_model=call_model,
        tool_schemas=lambda _ctx: [],
        registry=ToolRegistry(),
    )

    with pytest.raises(AppError) as caught:
        asyncio.run(loop.run(ctx))

    assert caught.value.code == ErrorCode.LLM_CONTEXT_OVERFLOW
    assert str(caught.value.code) == "LLM_CONTEXT_OVERFLOW"
    assert caught.value.message == CONTEXT_OVERFLOW_MESSAGE
    # AgentLoop 把 AppError 映射成 failed / context_overflow 后继续抛，调用方按
    # exc.code 写 error_code。
    assert recorder.outcomes == [("failed", "context_overflow")]
    assert ctx.state["context_layout"]["clear_boundary"] == 0
    assert DEFAULT_OUTPUT_RESERVE == 8192
