"""一轮代理运行的主循环（规格 §4.2.10、§4.5.1）。

``AgentLoop`` 只做「模型 → 工具 → 模型」的步进：每步重读历史、现算工具声明、把
响应交给中间件链、落 assistant / tool 消息、按一批的结果决定是否收尾。消息的读写
通过 :class:`Transcript` 注入：v2 会话用门面的 ``_LegacyTranscript`` 落库，侦察用
:class:`MemoryTranscript` 只留在内存里。

取消与中断是两种语义：``ctx.cancel_event`` 被置位、或用户主动中断（
``ctx.interrupted_now()`` 为真）时补完历史再抛；别的取消（服务关闭、上层取消）
原样抛出，不写任何消息。
"""

from __future__ import annotations

import asyncio
import logging
from collections.abc import Awaitable, Callable, Mapping, Sequence
from typing import Any, Protocol

from maa_api.agent.harness.chunks import parse_tool_call
from maa_api.agent.harness.context import (
    ModelRequest,
    ModelResponse,
    RunContext,
    RunOutcome,
    StopRun,
    ToolOutcome,
    Usage,
)
from maa_api.agent.harness.middleware import MiddlewareChain
from maa_api.agent.harness.toolexec import app_error_wire, execute_batch, json_text
from maa_api.agent.registry import ToolRegistry
from maa_api.domain.errors import AppError, ErrorCode

__all__ = [
    "BUDGET_EXCEEDED_FINISH_REASON",
    "CANCELLED_FINISH_REASON",
    "CANCELLED_STATUS",
    "CONTEXT_OVERFLOW_FINISH_REASON",
    "INTERRUPTED_FINISH_REASON",
    "INTERRUPTED_NOTICE",
    "INTERRUPTED_TOOL_STUB",
    "INVALID_TOOL_CALL_TEXT",
    "MESSAGE_PERSISTED_EVENT",
    "AgentLoop",
    "MemoryTranscript",
    "ModelCall",
    "ToolSchemaProvider",
    "Transcript",
    "select_stop",
]

logger = logging.getLogger(__name__)

#: 用户主动中断一轮时的收尾（规格 §3.1）。
#: ``finish_reason`` 的新取值；落库列宽 24 够用，且该列没有枚举约束。
INTERRUPTED_FINISH_REASON = "interrupted"
#: 工单取消时的运行终态与收尾原因（M70 §4.5.1、05 §2.3）。
CANCELLED_STATUS = "cancelled"
CANCELLED_FINISH_REASON = "cancelled"
#: 总线内部事件：每条助手 / 工具消息落库后发出，只给 WS 广播器用（M70 §4.5.8）。
MESSAGE_PERSISTED_EVENT = "message_persisted"
#: 预算超限时的收尾（M67 规格 §4.2.5）：``agent_run.finish_reason`` 的取值。
BUDGET_EXCEEDED_FINISH_REASON = "budget_exceeded"
#: 清理后仍超过窗口时的收尾（M74 规格 §4.2.4、§4.5.5）：``finish_reason`` 的取值。
CONTEXT_OVERFLOW_FINISH_REASON = "context_overflow"
#: 中断时追加到部分文本之后的标记；没有部分文本时它就是全部内容。
INTERRUPTED_NOTICE = "本轮已被你中断。"
#: 中断在历史里补的 tool 桩消息。**硬要求**：assistant 的 ``tool_calls`` 缺对应
#: tool 消息会让下一轮请求结构不完整，上游直接 400。
INTERRUPTED_TOOL_STUB = {
    "error": {
        "code": str(ErrorCode.AGENT_TURN_INTERRUPTED),
        "message": "本轮已被中断，该调用未完成",
    }
}
#: 模型返回的工具调用缺 id / 名字不合法时，assistant 消息落成的正文。
INVALID_TOOL_CALL_TEXT = "LLM 返回了无效的工具调用。"

ModelCall = Callable[[RunContext, ModelRequest], Awaitable[ModelResponse]]
ToolSchemaProvider = Callable[[RunContext], list[dict[str, Any]]]


class Transcript(Protocol):
    """一轮运行的消息存取；读、写、查已落库调用都经过它。"""

    async def load(self, ctx: RunContext) -> list[dict[str, Any]]: ...

    async def append(self, ctx: RunContext, *, role: str, **fields: Any) -> Any: ...

    async def persisted_tool_call_ids(self, ctx: RunContext) -> set[str]: ...


class MemoryTranscript:
    """不落库的消息列表（OpenAI 形态），recon 用。"""

    def __init__(self, initial: Sequence[dict[str, Any]] = ()) -> None:
        self.messages: list[dict[str, Any]] = list(initial)

    async def load(self, ctx: RunContext) -> list[dict[str, Any]]:
        return list(self.messages)

    async def append(self, ctx: RunContext, *, role: str, **fields: Any) -> dict[str, Any]:
        if role == "tool":
            message: dict[str, Any] = {
                "role": "tool",
                "tool_call_id": fields.get("tool_call_id"),
                "content": fields.get("content"),
            }
        elif role == "assistant":
            message = {"role": "assistant", "content": fields.get("content") or ""}
            if fields.get("tool_calls"):
                message["tool_calls"] = fields["tool_calls"]
        else:
            message = {"role": role, "content": fields.get("content")}
        self.messages.append(message)
        return message

    async def persisted_tool_call_ids(self, ctx: RunContext) -> set[str]:
        return {
            str(message["tool_call_id"])
            for message in self.messages
            if message.get("role") == "tool" and message.get("tool_call_id")
        }


def select_stop(
    outcomes: Sequence[ToolOutcome],
    pending: tuple[str, str | None] | None,
) -> tuple[str, str | None] | None:
    """决定这一批执行完之后本轮是否收尾，以及收尾原因与正文。

    优先级与 v2 一致：最后一个 ``question_unanswered`` → 第一个
    ``confirmation_resolved`` → 第一个其他 ``stop_run`` → 中间件预约的
    ``ctx.pending_stop``（例如 ``stuck_loop``）。提问取最后一个，是因为 v2 逐个
    覆盖 ``unanswered`` 变量；确认只留第一个。
    """
    unanswered: ToolOutcome | None = None
    confirmation: ToolOutcome | None = None
    other: ToolOutcome | None = None
    for outcome in outcomes:
        if outcome.stop_reason == "question_unanswered":
            unanswered = outcome
        elif outcome.stop_reason == "confirmation_resolved":
            if confirmation is None:
                confirmation = outcome
        elif outcome.stop_run and other is None:
            other = outcome
    chosen = unanswered or confirmation or other
    if chosen is not None:
        return chosen.stop_reason, chosen.stop_text
    return pending


def _reported_tokens(value: int) -> int | None:
    """归一口径下「上游没给 usage」与 0 都是 0；落库沿用 v2 的 ``None`` 语义。

    ``collect_completion`` 在没有 usage 块时产出全 0 的 ``Usage``（M67 §4.2.2），
    而 v2 的 ``agent_message.prompt_tokens`` / ``completion_tokens`` 在没有 usage 时
    是 ``NULL``。M65 的 parity golden 钉住了后者，所以写库前把 0 还原成 ``None``。
    """
    return value or None


class AgentLoop:
    """驱动一轮运行：每步一次模型调用，批与批之间只认 ``ctx`` 里的状态。"""

    def __init__(
        self,
        *,
        chain: MiddlewareChain,
        transcript: Transcript,
        call_model: ModelCall,
        tool_schemas: ToolSchemaProvider,
        registry: ToolRegistry,
        cost_ledger: Any = None,
    ) -> None:
        self._chain = chain
        self._transcript = transcript
        self._call_model = call_model
        self._tool_schemas = tool_schemas
        self._registry = registry
        # 成本账本：显式注入优先（测试），否则每次模型调用后从 ctx.app.state 取。
        self._cost_ledger = cost_ledger

    async def run(
        self,
        ctx: RunContext,
        *,
        run: Any = None,
        inbox_items: Sequence[Any] | None = None,
        state: Mapping[str, Any] | None = None,
        cancel_event: asyncio.Event | None = None,
    ) -> RunOutcome:
        """跑一轮运行。

        M70 的调用方可以把自己建好的运行行（``run``）、本轮领取的收件箱项与初始
        ``state``、``cancel_event`` 直接传进来；不传时全部用 ``ctx`` 上已有的值，
        M65 的 ``run(ctx)`` 调用方式不变。
        """
        if run is not None:
            ctx.run = run
        if inbox_items is not None:
            ctx.inbox_items = list(inbox_items)
        if state:
            ctx.state.update(state)
        if cancel_event is not None:
            ctx.cancel_event = cancel_event
        await self._chain.on_run_start(ctx)
        if ctx.emits_v3_events:
            await ctx.emit("run_start", self._run_start_data(ctx))
        try:
            outcome = await self._steps(ctx)
        except asyncio.CancelledError:
            # 只有用户主动中断或工单取消才补历史；服务关闭等取消原样抛出，不在关闭
            # 路径上写库。M70 的泵用 ``cancel_reason`` 区分两者（§4.5.1）。
            reason = self._cancel_reason(ctx)
            if reason is None:
                raise
            if reason == CANCELLED_FINISH_REASON:
                await self._finish_cancelled(ctx)
                await self._safe_run_end(
                    ctx, RunOutcome(CANCELLED_STATUS, CANCELLED_FINISH_REASON)
                )
                await self._emit_run_end(
                    ctx, RunOutcome(CANCELLED_STATUS, CANCELLED_FINISH_REASON)
                )
                raise
            await self._finish_interrupted(ctx)
            outcome = RunOutcome("interrupted", INTERRUPTED_FINISH_REASON)
            await self._safe_run_end(ctx, outcome)
            await self._emit_run_end(ctx, outcome)
            raise
        except AppError as exc:
            # 预算超限与上下文溢出要单独标记收尾原因（M67 §4.2.5、M74 §4.2.4）；
            # 门面照常按 AppError 发 error 事件并写运行终态，所以这里只映射
            # on_run_end 看到的原因后继续抛；``error_code`` 由调用方取 ``exc.code``。
            reason = (
                BUDGET_EXCEEDED_FINISH_REASON
                if exc.code == ErrorCode.BUDGET_EXCEEDED
                else (
                    CONTEXT_OVERFLOW_FINISH_REASON
                    if exc.code == ErrorCode.LLM_CONTEXT_OVERFLOW
                    else "error"
                )
            )
            await self._safe_run_end(ctx, RunOutcome("failed", reason))
            raise
        except Exception:
            await self._safe_run_end(ctx, RunOutcome("failed", "error"))
            raise
        await self._chain.on_run_end(ctx, outcome)
        await self._emit_run_end(ctx, outcome)
        return outcome

    def _cancel_reason(self, ctx: RunContext) -> str | None:
        """``cancel_event`` 已置位或有主动中断标记时给出原因，否则 ``None``。"""
        if ctx.cancel_event.is_set():
            return ctx.cancel_reason()
        if ctx.interrupted_now():
            return INTERRUPTED_FINISH_REASON
        return None

    @staticmethod
    def _run_start_data(ctx: RunContext) -> dict[str, Any]:
        return {
            "run_id": ctx.run_id,
            "parent_run_id": ctx.parent_run_id,
            "session_id": ctx.session_id,
            "ticket_id": ctx.ticket_id,
            "agent": ctx.spec.name,
            "kind": ctx.run_kind,
            "consumed_inbox_ids": [
                int(item.id)
                for item in ctx.inbox_items
                if getattr(item, "id", None) is not None
            ],
        }

    async def _emit_run_end(self, ctx: RunContext, outcome: RunOutcome) -> None:
        """v3 运行结束发 ``run_end``；v2 的 ``message_end`` 仍由各个收尾点发。"""
        if not ctx.emits_v3_events:
            return
        await ctx.emit(
            "run_end",
            {
                "run_id": ctx.run_id,
                "finish_reason": outcome.finish_reason,
                "usage": {
                    "prompt_tokens": outcome.usage.prompt_tokens,
                    "completion_tokens": outcome.usage.completion_tokens,
                    "cached_tokens": outcome.usage.cached_tokens,
                },
            },
        )

    async def _emit_message_end(self, ctx: RunContext, finish_reason: str) -> None:
        """v2 的收尾事件；v3 运行改由 ``run_end`` 收尾。"""
        if ctx.emits_v3_events:
            return
        await ctx.emit(
            "message_end",
            {"session_id": ctx.session_id, "finish_reason": finish_reason},
        )

    async def _steps(self, ctx: RunContext) -> RunOutcome:
        usage = Usage()
        while True:
            if ctx.cancel_event.is_set():
                if ctx.cancel_reason() == CANCELLED_FINISH_REASON:
                    await self._finish_cancelled(ctx)
                    return RunOutcome(
                        CANCELLED_STATUS, CANCELLED_FINISH_REASON, usage=usage
                    )
                await self._finish_interrupted(ctx)
                return RunOutcome("interrupted", INTERRUPTED_FINISH_REASON, usage=usage)
            # 每次模型调用都是一条独立的 assistant 消息：草稿必须从空白开始，否则第
            # N 步落库的内容会带上前 N-1 步已经落库的全文（v2 原语义）。
            ctx.draft.reset()
            ctx.pending_stop = None
            # 每步重读历史：上一批刚落的 tool 消息必须出现在下一次请求里。
            request = ModelRequest(
                messages=await self._transcript.load(ctx),
                tools=self._tool_schemas(ctx),
            )
            try:
                request = await self._chain.before_model(ctx, request)
                response = await self._call_model(ctx, request)
                # 每次模型调用返回后、after_model 链之前记账（M67 规格 §4.5.1）。
                await self._record_cost(ctx, request, response)
                if response.raw_tool_calls:
                    try:
                        response.tool_calls = [
                            parse_tool_call(raw) for raw in response.raw_tool_calls
                        ]
                    except AppError:
                        # 缺 id / 名字不合法的调用存不成 OpenAI 的 tool_calls：把伴随文本
                        # 落成普通 assistant 消息，再让门面发 error 事件。
                        await self._persist(
                            ctx,
                            role="assistant",
                            content=response.text or INVALID_TOOL_CALL_TEXT,
                            reasoning_content=response.reasoning or None,
                            prompt_tokens=_reported_tokens(
                                response.usage.prompt_tokens
                            ),
                            completion_tokens=_reported_tokens(
                                response.usage.completion_tokens
                            ),
                            finish_reason=response.upstream_finish_reason,
                            latency_ms=response.latency_ms,
                        )
                        raise
                response = await self._chain.after_model(ctx, response)
            except StopRun as stop:
                await self._finish_stop(ctx, stop.reason, stop.text)
                return RunOutcome(stop.status, stop.reason, stop.text, usage=usage)
            usage = usage + response.usage

            if not response.tool_calls:
                await self._persist(
                    ctx,
                    role="assistant",
                    content=response.text,
                    reasoning_content=response.reasoning or None,
                    prompt_tokens=_reported_tokens(response.usage.prompt_tokens),
                    completion_tokens=_reported_tokens(
                        response.usage.completion_tokens
                    ),
                    finish_reason=response.upstream_finish_reason,
                    latency_ms=response.latency_ms,
                )
                reason = response.upstream_finish_reason or "stop"
                await self._emit_message_end(ctx, reason)
                return RunOutcome("succeeded", reason, response.text, usage=usage)

            await self._persist(
                ctx,
                role="assistant",
                content=response.text or None,
                reasoning_content=response.reasoning or None,
                tool_calls=response.raw_tool_calls,
                prompt_tokens=_reported_tokens(response.usage.prompt_tokens),
                completion_tokens=_reported_tokens(response.usage.completion_tokens),
                finish_reason=response.upstream_finish_reason,
                latency_ms=response.latency_ms,
            )
            # 批次从这里开始「在飞」：tool 消息要等整批跑完才逐条落库，中断发生时
            # 草稿里剩下的就是需要补桩的调用。
            ctx.draft.pending_tool_calls = list(response.tool_calls)
            try:
                outcomes = await execute_batch(
                    ctx,
                    response.tool_calls,
                    chain=self._chain,
                    registry=self._registry,
                )
            except AppError as exc:
                # 某个调用的参数 JSON 坏了会中止整批。每个调用都补一条 error tool
                # 消息，保持 OpenAI 消息序列完整，也避免它被重试。
                for call in response.tool_calls:
                    stored = await self._persist(
                        ctx,
                        role="tool",
                        content=json_text({"error": app_error_wire(exc)}),
                        tool_call_id=call.id,
                        tool_name=call.name,
                    )
                    result = {
                        "tool_call_id": call.id,
                        "name": call.name,
                        "message_id": getattr(stored, "id", None),
                        "error": app_error_wire(exc),
                    }
                    if ctx.emits_v3_events:
                        result["run_id"] = ctx.run_id
                    await ctx.emit("tool_result", result)
                ctx.draft.pending_tool_calls = []
                raise
            for index, (call, outcome) in enumerate(
                zip(response.tool_calls, outcomes, strict=True)
            ):
                body = outcome.body()
                stored = await self._persist(
                    ctx,
                    role="tool",
                    content=(
                        outcome.content if outcome.content is not None else json_text(body)
                    ),
                    tool_call_id=call.id,
                    tool_name=call.name,
                    audit_id=outcome.audit_id,
                )
                outcome.message_id = getattr(stored, "id", None)
                # 这一条已经落库，从「待补桩」里去掉：中断正好落在这一条与下一条
                # 之间时，只补还没写的那几条。
                ctx.draft.pending_tool_calls = list(response.tool_calls[index + 1 :])
                result = {
                    "tool_call_id": call.id,
                    "name": call.name,
                    "message_id": outcome.message_id,
                    "audit_id": outcome.audit_id,
                    **body,
                }
                if ctx.emits_v3_events:
                    result["run_id"] = ctx.run_id
                await ctx.emit("tool_result", result)
            stop = select_stop(outcomes, ctx.pending_stop)
            if stop is not None:
                await self._finish_stop(ctx, stop[0], stop[1])
                return RunOutcome("succeeded", stop[0], stop[1], usage=usage)
            ctx.steps += 1
            if ctx.spec.max_steps and ctx.steps >= ctx.spec.max_steps:
                return RunOutcome("succeeded", "max_steps", usage=usage)

    async def _record_cost(
        self, ctx: RunContext, request: ModelRequest, response: ModelResponse
    ) -> None:
        """把这次调用记进成本账本；没有账本、没有档案或写库失败都不影响这一轮。

        legacy 定义在 T7 接上档案解析之前 ``request.profile`` 是 ``None``：没有档案
        就算不出价格，跳过记账（T7 之后 legacy 走 ``default`` 档案）。账本是观测
        与预算的数据源，写失败只记日志，不能把一次已经答完的模型调用变成失败。
        """
        ledger = self._cost_ledger
        if ledger is None:
            state = getattr(ctx.app, "state", None)
            ledger = getattr(state, "cost_ledger", None)
        profile = getattr(request, "profile", None)
        if ledger is None or profile is None:
            return
        try:
            await ledger.record(
                run_id=ctx.run_id,
                session_id=ctx.session_id,
                agent_name=ctx.spec.name,
                role=ctx.spec.model_role,
                profile=profile,
                usage=response.usage,
            )
        except Exception:  # noqa: BLE001 - 记账失败不能盖过模型回答
            logger.warning("成本账本写入失败：run=%s", ctx.run_id, exc_info=True)

    async def _finish_stop(
        self, ctx: RunContext, reason: str, text: str | None
    ) -> None:
        """以一句助手正文收尾：先落库、再推 ``text_delta``，最后 ``message_end``。"""
        if text is not None:
            await self._persist(
                ctx, role="assistant", content=text, finish_reason=reason
            )
            delta: dict[str, Any] = {"text": text}
            if ctx.emits_v3_events:
                delta["run_id"] = ctx.run_id
            await ctx.emit("text_delta", delta)
        await self._emit_message_end(ctx, reason)

    async def _persist(self, ctx: RunContext, *, role: str, **fields: Any) -> Any:
        """落一条消息；v3 运行在落库后补发总线内部事件 ``message_persisted``。"""
        stored = await self._transcript.append(ctx, role=role, **fields)
        if ctx.emits_v3_events:
            await ctx.emit(
                MESSAGE_PERSISTED_EVENT,
                {
                    "session_id": ctx.session_id,
                    "ticket_id": ctx.ticket_id,
                    "agent": ctx.spec.name,
                    "kind": ctx.run_kind,
                    "message": {
                        "id": self._field(stored, "id"),
                        "seq": self._field(stored, "seq"),
                        "role": role,
                        "inbox_id": self._field(stored, "inbox_id"),
                        "preview": self._preview(role, fields),
                    },
                },
            )
        return stored

    @staticmethod
    def _field(stored: Any, name: str) -> Any:
        if isinstance(stored, Mapping):
            return stored.get(name)
        return getattr(stored, name, None)

    @staticmethod
    def _preview(role: str, fields: Mapping[str, Any]) -> str:
        """WS ``message_created`` 的预览（M70 §4.3.3）：正文前 200 字或工具摘要。"""
        content = fields.get("content")
        if role == "tool":
            return f"{fields.get('tool_name') or '工具'} 的结果"
        if content:
            return str(content)[:200]
        calls = fields.get("tool_calls") or []
        names = [
            str((call.get("function") or {}).get("name") or "")
            for call in calls
            if isinstance(call, Mapping)
        ]
        names = [name for name in names if name]
        return "调用 " + "、".join(names) if names else ""

    async def _finish_interrupted(self, ctx: RunContext) -> None:
        """用户中断后的收尾：补历史 → 发 SSE（v2 ``_finish_interrupted_turn`` 原样）。

        1. 给这一批里还没落库的工具调用补 tool 桩消息。**顺序在标记消息之前**：
           assistant 的 ``tool_calls`` 与对应 tool 消息必须相邻。
        2. 把**已经流出去**的部分文本连同中断标记落成一条 assistant 消息。
           不重复 emit 那段文本：前端已经逐字收到过。
        3. 发 ``interrupted`` 再发 ``message_end``，让 SSE 正常收尾。

        每一步都吞异常只记日志：收尾失败也不能让取消变成一次「对话失败」。
        """
        persisted = await self._transcript.persisted_tool_call_ids(ctx)
        for call in ctx.draft.pending_tool_calls:
            if call.id in persisted:
                # 取消正好落在这一条的落库过程中：补桩前先回库确认，避免同一个
                # tool_call_id 出现两条 tool 消息。
                continue
            try:
                await self._persist(
                    ctx,
                    role="tool",
                    content=json_text(INTERRUPTED_TOOL_STUB),
                    tool_call_id=call.id,
                    tool_name=call.name,
                )
            except Exception:  # noqa: BLE001 - 见 docstring
                logger.warning(
                    "中断收尾：补 tool 桩消息失败 session=%s call=%s",
                    ctx.session_id,
                    call.id,
                    exc_info=True,
                )
        ctx.draft.pending_tool_calls.clear()
        partial = "".join(ctx.draft.text).strip()
        content = f"{partial}\n\n{INTERRUPTED_NOTICE}" if partial else INTERRUPTED_NOTICE
        try:
            await self._persist(
                ctx,
                role="assistant",
                content=content,
                reasoning_content="".join(ctx.draft.reasoning) or None,
                finish_reason=INTERRUPTED_FINISH_REASON,
            )
        except Exception:  # noqa: BLE001 - 见 docstring
            logger.warning(
                "中断收尾：写入中断消息失败 session=%s", ctx.session_id, exc_info=True
            )
        interrupted: dict[str, Any] = {
            "session_id": ctx.session_id,
            "finish_reason": INTERRUPTED_FINISH_REASON,
        }
        if ctx.emits_v3_events:
            interrupted["run_id"] = ctx.run_id
        await ctx.emit("interrupted", interrupted)
        await self._emit_message_end(ctx, INTERRUPTED_FINISH_REASON)

    async def _finish_cancelled(self, ctx: RunContext) -> None:
        """工单取消后的收尾：补未落库的 tool 桩消息，保住 OpenAI 消息序列。

        取消不是用户中断，不写「本轮已被你中断」那句话；已落库的汇报原样保留
        （M70 §4.5.7），运行终态由调用方按 ``cancelled`` 写。
        """
        persisted = await self._transcript.persisted_tool_call_ids(ctx)
        for call in ctx.draft.pending_tool_calls:
            if call.id in persisted:
                continue
            try:
                await self._persist(
                    ctx,
                    role="tool",
                    content=json_text(INTERRUPTED_TOOL_STUB),
                    tool_call_id=call.id,
                    tool_name=call.name,
                )
            except Exception:  # noqa: BLE001 - 与中断收尾同口径
                logger.warning(
                    "取消收尾：补 tool 桩消息失败 session=%s call=%s",
                    ctx.session_id,
                    call.id,
                    exc_info=True,
                )
        ctx.draft.pending_tool_calls.clear()

    async def _safe_run_end(self, ctx: RunContext, outcome: RunOutcome) -> None:
        """失败与中断路径上的 ``on_run_end``：自身异常只记日志，不盖过原因。"""
        try:
            await self._chain.on_run_end(ctx, outcome)
        except Exception:  # noqa: BLE001 - 收尾不能盖过真正的失败/取消
            logger.warning("代理运行收尾失败：run=%s", ctx.run_id, exc_info=True)
