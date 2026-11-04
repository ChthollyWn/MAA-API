"""一轮代理运行的数据形状（规格 §4.2.4）。

``RunContext`` 是循环与中间件共享的唯一可变状态；其余 dataclass 是在模型调用、
工具执行与收尾之间传递的值对象。本模块不导入 ``runtime`` 门面：依赖只能从门面
指向 ``harness`` 单向，T8 的用例会把这一条钉死。
"""

from __future__ import annotations

import asyncio
from collections.abc import Awaitable, Callable, Mapping
from contextvars import ContextVar
from dataclasses import dataclass, field
from typing import Any, Literal

from maa_api.agent.llm.usage import Usage
from maa_api.agent.specs.schema import AgentSpec
from maa_api.domain.enums import AgentRunKind, CallerType
from maa_api.domain.errors import AppError, ErrorCode

__all__ = [
    "CURRENT_RUN",
    "EmitFn",
    "ModelRequest",
    "ModelResponse",
    "RunContext",
    "RunOutcome",
    "StopRun",
    "ToolCall",
    "ToolOutcome",
    "ToolStatus",
    "TurnDraft",
    "Usage",
    "require_run",
]

EmitFn = Callable[[str, Any], Awaitable[None]]
ToolStatus = Literal["ok", "error", "awaiting", "rejected", "expired", "skipped"]

#: 当前正在执行的工具调用所属的运行（M70 §4.2.2）。``toolexec`` 在调 handler 前
#: set、之后 reset；并发分支各自在自己的任务上下文里 set，互不串台。
CURRENT_RUN: ContextVar[RunContext | None] = ContextVar("CURRENT_RUN", default=None)


def require_run(
    *, kinds: frozenset[str] | set[str], message: str | None = None
) -> RunContext:
    """取当前工具调用所属的运行；不在运行里或代理种类不符时抛 ``INVALID_PARAMETER``。

    ``kinds`` 比较的是 ``ctx.spec.kind``（``orchestrator`` / ``worker`` /
    ``subagent`` / ``background``），不是 ``agent_run.kind``。
    """
    ctx = CURRENT_RUN.get()
    if ctx is None:
        raise AppError(
            ErrorCode.INVALID_PARAMETER,
            message or "这个工具只能在 Agent 运行里调用",
            {"kinds": sorted(kinds)},
        )
    if ctx.spec.kind not in kinds:
        raise AppError(
            ErrorCode.INVALID_PARAMETER,
            message or "当前代理不能调用这个工具",
            {"kinds": sorted(kinds), "agent_kind": str(ctx.spec.kind)},
        )
    return ctx


@dataclass(slots=True)
class TurnDraft:
    """当前这次模型调用已经流出、还没落库的内容；中断收尾靠它补历史。"""

    text: list[str] = field(default_factory=list)
    reasoning: list[str] = field(default_factory=list)
    pending_tool_calls: list[ToolCall] = field(default_factory=list)

    def reset(self) -> None:
        """清空三个列表；不换对象，拿过引用的地方仍然看到同一份草稿。"""
        self.text.clear()
        self.reasoning.clear()
        self.pending_tool_calls.clear()


@dataclass(frozen=True, slots=True)
class ToolCall:
    id: str
    name: str
    raw_arguments: Any
    #: ``execute_batch`` 解析后用 ``dataclasses.replace`` 填入；解析前是 ``None``
    #: （v2 先落 assistant 消息再解析参数）。
    arguments: dict[str, Any] | None = None


@dataclass(slots=True)
class ModelRequest:
    messages: list[dict[str, Any]]
    tools: list[dict[str, Any]]
    profile: Any = None  # M67
    response_format: dict[str, Any] | None = None
    estimated_tokens: int = 0
    meta: dict[str, Any] = field(default_factory=dict)


@dataclass(slots=True)
class ModelResponse:
    text: str
    reasoning: str
    usage: Usage
    upstream_finish_reason: str | None
    latency_ms: int
    #: 合并后的 OpenAI 形态，原样落库。
    raw_tool_calls: list[dict[str, Any]] = field(default_factory=list)
    #: loop 从 ``raw_tool_calls`` 解析出来的调用。
    tool_calls: list[ToolCall] = field(default_factory=list)
    #: 流式分片里的 ``model``（M68 §4.2.3）：写进 ``gen_ai.response.model``。
    model: str | None = None
    #: 流式分片里的 ``id``（M68 §4.2.3）：写进 ``gen_ai.response.id``。
    response_id: str | None = None


@dataclass(slots=True)
class ToolOutcome:
    status: ToolStatus
    result: Any = None
    error: dict[str, Any] | None = None
    audit_id: int | None = None
    #: loop 落库后回填。
    message_id: int | None = None
    stop_run: bool = False
    stop_reason: str | None = None
    #: 结束本轮时写给用户的那句话。
    stop_text: str | None = None
    #: tool 消息正文；``None`` 时 loop 用 ``json_text(body())``。
    content: str | None = None

    def body(self) -> dict[str, Any]:
        return {"error": self.error} if self.error is not None else {"result": self.result}


@dataclass(slots=True)
class RunOutcome:
    #: succeeded / failed / interrupted；M66 改用 ``AgentRunStatus``。
    status: str
    finish_reason: str
    final_text: str | None = None
    report_id: int | None = None
    usage: Usage = field(default_factory=Usage)


class StopRun(Exception):
    """before_model / after_model 里抛出，loop 立即结束本次运行，不落当前这次响应。"""

    def __init__(self, reason: str, *, text: str | None = None, status: str = "succeeded") -> None:
        super().__init__(reason)
        self.reason = reason
        self.text = text
        self.status = status


def _never() -> bool:
    return False


def _interrupted_reason() -> str:
    """默认取消原因：M65 只有用户主动中断，M70 的工单取消由调用方注入 ``cancelled``。"""
    return "interrupted"


async def _discard(_name: str, _data: Any) -> None:
    return None


@dataclass(slots=True)
class RunContext:
    run_id: str
    root_run_id: str
    parent_run_id: str | None
    session_id: str | None
    ticket_id: str | None
    spec: AgentSpec
    caller: CallerType = CallerType.INTERNAL
    app: Any = None
    request: Any = None
    #: 默认丢弃；v2 会话是 ``RunEventBus.emit``。
    emit: EmitFn = _discard
    cancel_event: asyncio.Event = field(default_factory=asyncio.Event)
    #: 键用中间件 name。
    state: dict[str, Any] = field(default_factory=dict)
    steps: int = 0
    inbox_items: list[Any] = field(default_factory=list)
    #: v2 会话接 ``AgentRuntime._interrupted_now(session_id)``。
    interrupted_now: Callable[[], bool] = _never
    #: 执行期白名单；``None`` 不限（v2 普通会话）。
    allowed_tools: frozenset[str] | None = None
    vision_enabled: bool = True
    #: ``False`` 时只读批也串行（recon）。
    parallel_tools: bool = True
    draft: TurnDraft = field(default_factory=TurnDraft)
    pending_stop: tuple[str, str | None] | None = None
    #: 调用方建好的运行行（M70）：有它才按 v3 事件名发 ``run_start`` / ``run_end`` /
    #: ``message_persisted``；v2 门面不传，事件名与行为逐字不变。
    run: Any = None
    #: 取消原因回调（M70 §4.5.1）：``interrupted`` 或 ``cancelled``。
    cancel_reason: Callable[[], str] = _interrupted_reason

    def stop_after_batch(self, reason: str, text: str | None = None) -> None:
        """当前这批工具执行、落库完之后结束本次运行。"""
        self.pending_stop = (reason, text)

    @property
    def run_kind(self) -> str | None:
        """调用方建好的运行行的种类；没有运行行（v2 门面、recon）时为 ``None``。"""
        run = self.run
        if run is None:
            return None
        kind = run.get("kind") if isinstance(run, Mapping) else getattr(run, "kind", None)
        return None if kind is None else str(kind)

    @property
    def emits_v3_events(self) -> bool:
        """是否按 M70 §4.3.2 的 v3 事件名发事件。

        ``legacy_turn`` 运行即便带了运行行也继续用 v2 事件名：v2 SSE 仍由
        ``AgentRuntime.stream_message`` 输出，不能混进 v3 的名字。
        """
        kind = self.run_kind
        return kind is not None and kind != AgentRunKind.LEGACY_TURN.value
