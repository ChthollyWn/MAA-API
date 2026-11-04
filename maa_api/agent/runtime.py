"""v2 会话门面：会话锁、中断、能力探查、标题与失败说明，以及具名 SSE 事件。

一轮里的步进循环交给 :class:`maa_api.agent.harness.loop.AgentLoop`：本模块只保留
v2 的前置步骤（配置与会话检查、用户消息落库、能力探查、``message_start``、状态摘要、
知识库段、建客户端）与收尾（失败说明、标题生成）。事件列表不变：
``message_start`` / ``text_delta`` / ``tool_start`` / ``tool_result`` /
``confirmation_required`` / ``question_required`` / ``session_title`` /
``message_end`` / ``error``。
"""

from __future__ import annotations

import asyncio
import base64
import inspect
import json
import logging
import time
from collections.abc import (
    Awaitable,
    Callable,
    Collection,
    Mapping,
    Sequence,
)
from datetime import datetime
from pathlib import Path
from typing import Any

from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from maa_api.agent.confirmation import ConfirmationService
from maa_api.agent.context.clearing import ToolResultClearingMiddleware
from maa_api.agent.context.durable import DurableContextMiddleware
from maa_api.agent.harness.builtin_middleware import (
    AGENT_SCREENSHOT_GRID,
    DEFAULT_MAX_CONTEXT_CHARS,
    HISTORICAL_REASONING_CHARS,
    IMAGE_CONTEXT_CHARS,
    QUESTION_EXPIRED_TEXT,
    QUESTION_SKIPPED_TEXT,
    SCREENSHOT_RESULT_TOOLS,
    STUCK_EXEMPT_TOOLS,
    STUCK_REPEAT_LIMIT,
    TOOL_RESULT_PRUNE_HEAD,
    TOOL_RESULT_PRUNE_TAIL,
    TOOL_RESULT_PRUNE_THRESHOLD,
    ClarificationMiddleware,
    ContextLayoutMiddleware,
    LoopGuardMiddleware,
    PolicyMiddleware,
    TraceMiddleware,
    _context_size,
    _message_dict,
    _screenshot_payload,
    build_model_context,
)
from maa_api.agent.harness.chunks import (
    collect_completion,
    merge_tool_call_deltas,
)
from maa_api.agent.harness.context import ModelRequest, ModelResponse, RunContext
from maa_api.agent.harness.events import RunEvent, RunEventBus, sse_event
from maa_api.agent.harness.loop import (
    BUDGET_EXCEEDED_FINISH_REASON,
    INTERRUPTED_FINISH_REASON,
    INTERRUPTED_NOTICE,
    INTERRUPTED_TOOL_STUB,
    AgentLoop,
)
from maa_api.agent.harness.middleware import Middleware, MiddlewareChain
from maa_api.agent.harness.toolexec import SCREEN_TOOL_NAMES
from maa_api.agent.lease import LeaseMiddleware
from maa_api.agent.llm import OpenAICompatibleClient
from maa_api.agent.llm.budget import BudgetMiddleware
from maa_api.agent.llm.profiles import (
    _VISION_PROBE_MARKER,
    _VISION_PROBE_PNG,
    _VISION_PROBE_PROMPT,
    probe_tool_calling,
    probe_vision,
)
from maa_api.agent.redaction import redact_remote_data
from maa_api.agent.recon.runner import (
    ReconDeps,
    budget_from_settings,
    run_recon,
)
from maa_api.agent.registry import ToolRegistry
from maa_api.agent.runs import RunService
from maa_api.agent.specs.registry import builtin_spec, builtin_specs
from maa_api.agent.workers.middleware import (
    MandateMiddleware,
    ReportMiddleware,
)
from maa_api.db.models import AgentMessage, AgentRun, new_uuid, utcnow
from maa_api.db.repositories.agent import (
    AgentMessageRepository,
    AgentRunRepository,
    AgentSessionRepository,
)
from maa_api.db.repositories.log import ScreenshotRepository
from maa_api.domain.enums import AgentRole, AgentRunKind, AgentRunStatus
from maa_api.domain.errors import AppError, ErrorCode
from maa_api.settings import Settings, get_settings

__all__ = [
    "AgentRuntime",
    "INCIDENT_ALLOWED_TOOLS",
    "KNOWLEDGE_UNAVAILABLE",
    "SYSTEM_PROMPT",
    "build_agent_chain",
    "build_model_context",
    "call_runtime_factory",
    "merge_tool_call_deltas",
    "redact_remote_data",
]

logger = logging.getLogger(__name__)


DEFAULT_MAX_ITERATIONS: int | None = None
DEFAULT_CONFIRMATION_POLL_SECONDS = 0.25
#: 会话标题的长度上限。数据库列是 64，这里取 24：一屏的会话行放得下，也不必依赖
#: 截断兜底：真被截断说明模型没听话，标题已经不好看了。
TITLE_MAX_CHARS = 24
#: 生成标题时喂给模型的用户消息上限。标题只需要「问的是什么」，整段日志摘要没有用，
#: 还会白花 token。
TITLE_SEED_MAX_CHARS = 300
#: 生成标题的固定指令。要求「只输出标题」是必须的：模型很爱补一句「好的，这是标题：」。
TITLE_PROMPT = (
    "用不超过 12 个字概括用户这次要解决的问题，作为会话标题。"
    "只输出标题本身：不要引号、不要句末标点、不要任何解释或前后缀。"
)
#: 标题调用的超时。它卡在收流前，必须比一次正常对话短得多：默认 120 秒的超时意味着
#: 上游卡住时这一轮的「完成」也会跟着卡两分钟，那不叫快速总结。
TITLE_TIMEOUT_SECONDS = 20.0
#: 知识库段生成失败时的降级文本：这一轮照常进行，但模型要知道索引不可用（规格 §4.1）。
KNOWLEDGE_UNAVAILABLE = "知识库暂不可用"
#: 侦察 subagent 的步数硬上限（M48 规格 D4）：超过这个数就从「侦察」变成「重写一遍」。
RECON_MAX_STEPS_CAP = 20
#: 侦察单步的超时。比正常对话短：侦察是只读且可重来的，宁可让它带着不确定项收尾，
#: 也不要父 agent 的一次工具调用把整轮拖住。
RECON_TIMEOUT_SECONDS = 60.0
INCIDENT_ALLOWED_TOOLS = frozenset(builtin_spec("legacy_incident").tools)
SSE_KEEPALIVE_SECONDS = 10.0
#: 用户主动中断一轮时的收尾（规格 §3.1）：``INTERRUPTED_FINISH_REASON``、
#: ``INTERRUPTED_NOTICE``、``INTERRUPTED_TOOL_STUB`` 已搬到 ``harness.loop``，
#: 这里从新位置再导出，名字与值都不变。
#: 等 producer 收尾的宽限时长：收尾要写库、发 SSE，得给够时间；但一次「停止」的
#: HTTP 请求不能被一个卡住的收尾永久挂住，所以仍要设上限。
INTERRUPT_GRACE_SECONDS = 5.0
#: 能力探查**失败**（请求被上游拒绝、超时、连接层失败）时结果的最长存活时间。
#: 探查成功的结果不设过期：模型能力不会因为一次抖动而改变，改 ``config.yaml`` 仍然
#: 要重启服务才会重新探查（docs/13 §8.13）。失败态必须过期，否则一次瞬时抖动会把
#: 进程钉死在「当前模型未通过 function calling 能力检查」上——之后每一轮都在几秒内
#: 失败，界面上看起来就是「Agent 又用不了了」，而唯一的解法是重启服务。
CAPABILITY_FAILURE_TTL_SECONDS = 60.0
#: 普通对话轮次失败后补写的助手消息的 ``finish_reason``（落库列宽 24，无枚举约束）。
TURN_FAILED_FINISH_REASON = "turn_error"
#: 这些错误码意味着「用户消息已经落库，但这一轮没能给出回答」：会话里会留下一个没有
#: 回复的尾巴，刷新后看不出发生过什么。补一条说明让会话自洽（规格 §8.2）。
TURN_FAILURE_NOTICE_CODES = frozenset(
    {
        ErrorCode.LLM_REQUEST_FAILED,
        ErrorCode.LLM_TIMEOUT,
        ErrorCode.LLM_RATE_LIMITED,
        ErrorCode.LLM_CONTEXT_OVERFLOW,
    }
)
#: 失败说明的结尾。上下文超长要单独说：让用户「直接重发」是错的，那一轮重发还是超。
TURN_FAILURE_NOTICE_TAIL = "这条消息已保留，可以直接重发。"
TURN_FAILURE_NOTICE_OVERFLOW_TAIL = "这条消息已保留；请开启新会话或删减内容后再试。"
#: 非 AppError 的意外异常对外只说这一句，细节只进日志：SSE 与落库的消息都会给用户看，
#: 不该把内部异常文本暴露出去。
TURN_FAILURE_GENERIC_MESSAGE = "Agent 对话处理失败"
#: 门面这一轮建出的运行用哪一个内置代理定义（docs/v3/specs/m66-durable-runs §4.2.4）。
LEGACY_CHAT_AGENT = "legacy_chat"
LEGACY_INCIDENT_AGENT = "legacy_incident"
#: ``_save_incident_failure`` 落库的 ``finish_reason``（v2 就是 ``incident_error``）。
INCIDENT_FAILED_FINISH_REASON = "incident_error"
_SSE_END = object()

SYSTEM_PROMPT = builtin_spec("legacy_chat").body
#: 只属于 v3 代理定义（orchestrator/worker/subagent/background）、不能进 v2 会话声明
#: 的工具（M70 §4.5.12）：交集之外的自定义工具（测试/嵌入的最小注册表）照旧可用。
V3_ONLY_TOOL_NAMES = frozenset(
    name
    for spec in builtin_specs().values()
    if not spec.name.startswith("legacy_")
    for name in spec.tools
) - frozenset(builtin_spec(LEGACY_CHAT_AGENT).tools)


def call_runtime_factory(
    factory: Callable[..., Any],
    confirmation_service: ConfirmationService,
    question_service: Any = None,
) -> Any:
    """用注入的工厂构造 runtime，并兼容只接一个位置参数的旧工厂。

    ``agent_runtime_factory`` 是测试与冒烟脚本的替换点（``app.state`` 上的钩子），
    它们大多写成 ``lambda confirmation_service: ...``。所以这里按**工厂实际支持的
    签名**决定要不要传第二个关键字参数，而不是 try/except ``TypeError``：后者会把
    工厂内部的真实类型错误当成「旧签名」吞掉，然后错误地在更少的参数下重试。
    """
    try:
        parameters = inspect.signature(factory).parameters
    except (TypeError, ValueError):  # 内建/不可 introspection 的可调用对象
        parameters = {}
    accepts_keyword = "question_service" in parameters or any(
        parameter.kind is inspect.Parameter.VAR_KEYWORD
        for parameter in parameters.values()
    )
    if accepts_keyword:
        return factory(confirmation_service, question_service=question_service)
    return factory(confirmation_service)


def build_agent_chain(
    kind: str,
    *,
    confirmation_service: ConfirmationService,
    session_factory: async_sessionmaker[AsyncSession] | None = None,
    question_service: Any = None,
    confirmation_poll_seconds: float = DEFAULT_CONFIRMATION_POLL_SECONDS,
    device_lease_service: Any = None,
    cost_ledger: Any = None,
    settings_provider: Callable[[], Any] | None = None,
) -> MiddlewareChain:
    """v3 运行（总控 / Worker / 子代理）的中间件链（05 §5.2）。

    在 legacy 五元链的基础位置上按序插入 M70 的三个：14 ``MandateMiddleware``、
    16 ``LeaseMiddleware``（M71 取代 M70 临时的设备门）、18 ``ReportMiddleware``。
    三个都声明了 ``applies_to``，``MiddlewareChain.for_kind`` 会把它们从总控链裁掉。
    ``cost_ledger`` 存在时 ``BudgetMiddleware`` 仍在第 2 位（与门面一致）。

    M74 把 durable 与工具结果清理放到第 6、8 位；第 7 位留给 M74 后续任务的
    ``CompactionMiddleware``，它必须落在「收集 durable」之后、「清理」之前
    （压缩阈值按清理后的请求估算，06 §5.2）。
    """
    registry = confirmation_service.registry
    middlewares: list[Middleware] = [TraceMiddleware()]
    if cost_ledger is not None:
        middlewares.append(
            BudgetMiddleware(ledger=cost_ledger, settings_provider=settings_provider)
        )
    middlewares.extend(
        [
            ContextLayoutMiddleware(),
            DurableContextMiddleware(),
            ToolResultClearingMiddleware(
                registry=registry, settings_provider=settings_provider
            ),
            LoopGuardMiddleware(),
            MandateMiddleware(
                registry=registry,
                confirmation_service=confirmation_service,
                session_factory=session_factory,
            ),
            LeaseMiddleware(
                lease_service=device_lease_service,
                registry=registry,
            ),
            ClarificationMiddleware(
                question_service=question_service,
                registry=registry,
            ),
            ReportMiddleware(),
            PolicyMiddleware(
                confirmation_service=confirmation_service,
                session_factory=session_factory,
                poll_seconds=confirmation_poll_seconds,
            ),
        ]
    )
    return MiddlewareChain.for_kind(kind, middlewares)


class _LegacyRunTracker:
    """v2 门面这一轮的事件出口：原样转发事件，顺带记下 finish_reason、在等人时切运行状态。

    事件名是 v2 SSE 的稳定契约，所以这里按事件名判断「等确认/等提问」与「恢复执行」，
    不依赖 M65 内部重构（规格 §4.2.5）。``set_status`` 失败只记 warning：状态是运行的
    观测信息，不能因为一次数据库抖动把已经跑起来的一轮打断。
    """

    #: 出现这些事件说明这一轮在等人：确认或提问已经发给前端。
    _WAITING_EVENTS = frozenset({"confirmation_required", "question_required"})
    #: 等待期间出现这些事件说明人已经答过、这一轮继续跑。
    _RESUMING_EVENTS = frozenset({"tool_result", "text_delta", "tool_start"})

    def __init__(
        self,
        run_service: RunService | None,
        run_id: str,
        emit: Callable[[str, Any], Awaitable[Any]],
    ) -> None:
        self._run_service = run_service
        self.run_id = run_id
        self._emit = emit
        self._waiting = False
        self._finish_reason: str | None = None

    async def __call__(self, event: str, data: Any) -> None:
        """先按事件名处理状态与 finish_reason，再原样转发事件。

        顺序与规格 §4.2.4 的措辞（先转发、后处理）相反，但 SSE 的内容、顺序与字段
        完全不变，只是把状态写放在消费者看到事件之前。原因：v2 的测试大量使用
        ``sqlite+aiosqlite:///:memory:`` + ``StaticPool``，所有 session 共用同一条
        SQLite 连接；消费者一收到事件就写库（回答提问、批准确认），若生产者在同一
        时刻也在写，两边的事务会互相破坏（实测：提问的回答提交丢失，回答接口返回
        ``pending``）。先写状态让消费者永远在写完状态之后才启动。
        """
        if event in self._WAITING_EVENTS:
            self._waiting = True
            await self._set_status(AgentRunStatus.WAITING_HUMAN)
        elif self._waiting and event in self._RESUMING_EVENTS:
            self._waiting = False
            await self._set_status(AgentRunStatus.RUNNING)
        elif event == "message_end":
            reason = data.get("finish_reason") if isinstance(data, Mapping) else None
            self._finish_reason = str(reason) if reason else "stop"
        await self._emit(event, data)

    def outcome(self) -> tuple[AgentRunStatus, str]:
        """这一轮的终态：``stuck_loop`` 记失败，其余按上游给的 finish_reason 记成功。"""
        reason = self._finish_reason or "stop"
        if reason == "stuck_loop":
            return AgentRunStatus.FAILED, reason
        return AgentRunStatus.SUCCEEDED, reason

    async def _set_status(self, status: AgentRunStatus) -> None:
        service = self._run_service
        if service is None:
            return
        try:
            await service.set_status(self.run_id, status)
        except Exception:  # noqa: BLE001 - 状态只是观测信息，不打断这一轮
            logger.warning(
                "Agent 运行状态切换失败：run=%s status=%s", self.run_id, status, exc_info=True
            )


class AgentRuntime:
    """Persist conversations and route every model tool call through confirmation."""

    def __init__(
        self,
        session_factory: async_sessionmaker[AsyncSession],
        confirmation_service: ConfirmationService,
        *,
        settings_provider: Callable[[Any], Any] | None = None,
        client_factory: Callable[[Settings], Any] | None = None,
        context_provider: Callable[[Any], Any] | None = None,
        knowledge_service: Any = None,
        question_service: Any = None,
        max_iterations: int | None = DEFAULT_MAX_ITERATIONS,
        max_context_chars: int | None = DEFAULT_MAX_CONTEXT_CHARS,
        confirmation_poll_seconds: float = DEFAULT_CONFIRMATION_POLL_SECONDS,
        run_service: RunService | None = None,
    ) -> None:
        # None 表示不设步数上限（默认）；正整数只用于测试与需要受限的调用方。
        if max_iterations is not None and max_iterations < 1:
            raise ValueError("max_iterations must be positive or None")
        # None 表示不设上下文上限（交给模型自身的窗口）；正整数才启用本地渐近裁剪。
        if max_context_chars is not None and max_context_chars < 1:
            raise ValueError("max_context_chars must be positive or None")
        if confirmation_poll_seconds <= 0:
            raise ValueError("confirmation_poll_seconds must be positive")
        self.session_factory = session_factory
        self.confirmation_service = confirmation_service
        self.settings_provider = settings_provider
        self.client_factory = client_factory
        self.context_provider = context_provider
        # 与 context_provider 同一套接法：构造参数优先，缺省时从 app.state 取。
        self.knowledge_service = knowledge_service
        # 提问服务与确认服务并列注入，理由同 knowledge_service：构造参数优先。
        # 刻意**不**回落到 ``app.state``：``None`` 的语义是「这个部署没有提问能力」，
        # 要报 AGENT_DISABLED（规格 §3.5）；隐式回落会让「没装上」表现成「装上了」。
        self.question_service = question_service
        self.max_iterations = max_iterations
        self.max_context_chars = max_context_chars
        self.confirmation_poll_seconds = confirmation_poll_seconds
        # 装配层（``main.py`` 的 lifespan）先建 runtime、后建 ``RunService``，用
        # ``bind_run_service`` 换进来；直接构造的调用方（测试、冒烟）在这里自建一份。
        # ``session_factory`` 为 ``None`` 的替身（只测侦察）没有运行服务。
        self.run_service = (
            run_service
            if run_service is not None
            else (
                RunService(session_factory, boot_id=new_uuid())
                if session_factory is not None
                else None
            )
        )
        self._locked_sessions: set[str] = set()
        self._turn_tasks: set[asyncio.Task[None]] = set()
        self._queue_waiters: set[asyncio.Task[Any]] = set()
        #: 会话 → 本轮运行的 id；运行 id → 跑这一轮的 task。两者都在
        #: ``_begin_legacy_run`` 里登记，``_forget_run`` 里一起清掉。
        self._current_runs: dict[str, str] = {}
        self._run_tasks: dict[str, asyncio.Task[Any]] = {}
        #: 被用户**主动**中断的 run_id。只有在这里的运行，取消才会走「中断收尾」；
        #: 别的 CancelledError（服务关闭、上层取消）原样抛出，两种语义不能混。
        self._interrupted: set[str] = set()
        #: 已经进入中断收尾的 run_id。``interrupt_turn`` 靠它避免再 cancel 一次：
        #: 第二次 cancel 会打在收尾自己的 ``await`` 上（写库、发事件），把「补历史」
        #: 打断成半截。
        self._interrupt_settling: set[str] = set()
        self._capability_cache: dict[tuple[str, str], dict[str, Any]] = {}
        #: 会话 → 由 ``enable_tool_group`` 打开的扩展工具组（M57，docs/13 §8.80）。
        #: 只影响模型侧工具声明；进程重启后回到默认（核心组），会话可再次开启。
        self._enabled_tool_groups: dict[str, set[str]] = {}
        #: 失败态能力结果的过期时刻（``time.monotonic()``）。成功态不登记，永不过期。
        self._capability_expiry: dict[tuple[str, str], float] = {}

    def bind_run_service(self, run_service: RunService) -> None:
        """把装配层建好的 ``RunService`` 换进来（构造在先、服务在后，见 §4.2.6）。

        有运行在跑时不换：``_begin_legacy_run`` 已经用旧服务建了行，换掉之后终态会
        写到另一个服务的跟踪表里。
        """
        if self._current_runs:
            raise RuntimeError("有运行在跑，不能替换 RunService")
        self.run_service = run_service

    async def acquire_session(self, session_id: str) -> bool:
        """进程内快路径先占位；再看库里有没有别的活动运行，两者冲突时以库为准。"""
        if session_id in self._locked_sessions:
            return False
        self._locked_sessions.add(session_id)
        if self.run_service is None:
            return True
        try:
            active = await self.run_service.active_run(session_id)
        except BaseException:
            self._locked_sessions.discard(session_id)
            raise
        if active is not None:
            self._locked_sessions.discard(session_id)
            return False
        return True

    def release_session(self, session_id: str) -> None:
        """Release a turn slot; duplicate release is safe for response cleanup."""
        self._locked_sessions.discard(session_id)

    def turn_running(self, session_id: str) -> bool:
        """该会话此刻是否真有一轮在跑（规格 §3.1）。

        「锁还在」不等于「有人在跑」：REST 已经 ``acquire_session`` 而 producer 还没
        登记的那一小段里，锁在但没有任何执行。刷新后的前端要据此恢复停止入口，所以
        只认活动 task。
        """
        run_id = self._current_runs.get(session_id)
        task = self._run_tasks.get(run_id) if run_id is not None else None
        return task is not None and not task.done()

    async def interrupt_turn(self, session_id: str) -> bool:
        """中断该会话正在跑的一轮，返回是否真的中断了什么（规格 §3.1）。

        只停这一轮，**不碰流水线与队列**：已经提交进队列的任务继续跑，要停它仍走
        显式的 ``stop_pipeline``（用户裁决 §1.1）。被动断开（断网/关页面）不走这里，
        那条语义不变。

        顺序是刻意的：先撤确认，再取消本轮。``_wait_and_execute`` 醒来只认状态，
        用户批准就会执行，与本轮是否还在等无关：先取消本轮会让「取消期间被批准」
        变成一个真的副作用。取消之后再撤一次：这一小段窗口里刚创建的确认/审计
        （比如刚派发出去的工具）也要一起撤回。
        """
        run_id = self._current_runs.get(session_id)
        task = self._run_tasks.get(run_id) if run_id is not None else None
        if run_id is None or task is None or task.done():
            # 没有活动轮次：锁还在只可能是「REST 已拿锁、producer 还没登记」。释放它，
            # 否则这个会话会一直 409，而实际上没有任何东西在跑。仍然撤一次在飞调用：
            # 上一轮如果异常收场，挂着的确认/提问不该在这次「停止」之后还能复活。
            self.release_session(session_id)
            await self._abort_in_flight_calls(session_id)
            return False
        # 重复点「停止」时不重复 cancel：第二次 cancel 会打在收尾协程自己的 await 上，
        # 把「落库 + 发事件」的收尾打断，反而留下半截历史。
        already_interrupting = run_id in self._interrupted
        self._interrupted.add(run_id)
        try:
            await self._abort_in_flight_calls(session_id)
            if not already_interrupting and run_id not in self._interrupt_settling:
                task.cancel()
            await asyncio.wait({task}, timeout=INTERRUPT_GRACE_SECONDS)
            await self._abort_in_flight_calls(session_id)
        finally:
            # 收尾期间标记必须一直有效（收尾协程靠它判断这是主动中断）；task 结束后
            # 再清，避免把它留给下一个同会话的轮次。
            if task.done():
                self._interrupted.discard(run_id)
                self._interrupt_settling.discard(run_id)
        return True

    def _interrupted_now(self, session_id: str) -> bool:
        """本轮是否已被请求中断；为真时同时登记「收尾即将开始」。

        登记与返回在同一段同步代码里，所以 ``interrupt_turn`` 不可能在收尾已经启动
        之后再插进来 cancel 一次（它先查这个集合再决定要不要 cancel）。
        """
        run_id = self._current_runs.get(session_id)
        if run_id is None or run_id not in self._interrupted:
            return False
        self._interrupt_settling.add(run_id)
        return True

    def _clear_interrupt(self, session_id: str) -> None:
        """一轮结束后清掉中断标记：留着会让下一个同会话轮次把自己也当成被中断。

        新代码统一走 :meth:`_forget_run`；保留这个方法是为了兼容按会话清理的旧调用点。
        """
        run_id = self._current_runs.get(session_id)
        if run_id is not None:
            self._interrupted.discard(run_id)
            self._interrupt_settling.discard(run_id)

    async def _begin_legacy_run(
        self, session_id: str, *, agent_name: str, trigger_seq: int | None = None
    ) -> AgentRun:
        """建本轮运行（``kind=legacy_turn``），登记 ``_current_runs`` 与 ``_run_tasks``。

        ``model`` 取会话当前的 model；``spec_version`` 取内置代理定义的哈希。会话上
        已有活动运行（进程内锁漏掉、库里还有）时按 v2 的对外契约报
        ``AGENT_SESSION_BUSY``：v2 前端与测试只认这个码，``RUN_ALREADY_ACTIVE`` 是
        v3 运行端点用的。
        """
        service = self.run_service
        if service is None or self.session_factory is None:  # pragma: no cover - 装配缺陷
            raise RuntimeError("没有运行服务，无法建立运行")
        async with self.session_factory() as session:
            stored = await AgentSessionRepository(session).get(session_id)
        model = stored.model if stored is not None else None
        try:
            run = await service.start(
                agent_name=agent_name,
                kind=AgentRunKind.LEGACY_TURN,
                session_id=session_id,
                trigger_seq=trigger_seq,
                model=model,
                spec_version=builtin_spec(agent_name).spec_version,
            )
        except AppError as exc:
            if exc.code == ErrorCode.RUN_ALREADY_ACTIVE:
                raise AppError(
                    ErrorCode.AGENT_SESSION_BUSY,
                    "该会话还有一轮消息未结束，请等它结束后再发送",
                    exc.details,
                ) from exc
            raise
        self._current_runs[session_id] = run.id
        task = asyncio.current_task()
        if task is not None:
            self._run_tasks[run.id] = task
        return run

    async def _end_legacy_run(
        self,
        run: AgentRun | None,
        status: AgentRunStatus,
        finish_reason: str,
        *,
        error_code: str | None = None,
        error_message: str | None = None,
    ) -> None:
        """写本轮终态；``run`` 为 ``None`` 时什么都不做。

        写库失败只记 warning：终态是观测信息，不能阻塞下一轮。可以重复调用
        （``RunService.finish`` 幂等）。``finally`` 里一定 ``_forget_run``：终态先落库、
        再放进程内占位，紧接着的下一条消息才不会在 ``acquire_session`` 里看到上一轮
        还是活动态。
        """
        if run is None:
            return
        try:
            service = self.run_service
            if service is not None:
                await service.finish(
                    run.id,
                    status=status,
                    finish_reason=finish_reason,
                    error_code=error_code,
                    error_message=error_message,
                )
        except Exception:  # noqa: BLE001 - 终态写失败不阻塞下一轮
            logger.warning("Agent 运行终态写入失败：run=%s", run.id, exc_info=True)
        finally:
            self._forget_run(run.session_id, run.id)

    def _forget_run(self, session_id: str | None, run_id: str) -> None:
        """从三张进程内表里清掉这条运行；``_current_runs`` 只删指向它的那一项。"""
        self._interrupted.discard(run_id)
        self._interrupt_settling.discard(run_id)
        self._run_tasks.pop(run_id, None)
        if session_id is not None and self._current_runs.get(session_id) == run_id:
            self._current_runs.pop(session_id, None)

    async def _abort_in_flight_calls(self, session_id: str) -> None:
        """撤回该会话本轮在等/在跑的工具与挂起的提问。

        失败只记日志：撤回是尽力而为的收尾，不能因为一次数据库抖动就让「停止」本身
        失败（本轮该被取消的部分仍会被取消）。
        """
        try:
            await self.confirmation_service.abort_session_calls(session_id)
        except Exception:  # noqa: BLE001 - 见 docstring
            logger.exception("中断时撤回工具调用失败：%s", session_id)
        service = self.question_service
        if service is None:
            return
        try:
            await service.cancel_pending_for_session(session_id)
        except Exception:  # noqa: BLE001 - 见 docstring
            logger.exception("中断时撤回提问失败：%s", session_id)

    async def ensure_configured(self, request: Any, session_id: str | None = None) -> Settings:
        settings = await self._effective_settings(request)
        if not settings.llm.base_url or not settings.llm.api_key or not settings.llm.model:
            raise AppError(ErrorCode.LLM_NOT_CONFIGURED, "请先配置 LLM base_url、api_key 和 model")
        if session_id is not None:
            async with self.session_factory() as session:
                stored = await AgentSessionRepository(session).get(session_id)
                if stored is None:
                    raise AppError(ErrorCode.AGENT_SESSION_NOT_FOUND, "Agent 会话不存在")
                if stored.status != "active":
                    raise AppError(ErrorCode.AGENT_SESSION_NOT_FOUND, "Agent 会话已结束")
        return settings

    async def run_incident(
        self,
        session_id: str,
        request: Any,
        allowed_tools: Collection[str],
    ) -> dict[str, Any]:
        """Run the already-persisted incident user message in a restricted turn.

        The incident service commits the session and initial user message before
        entering here. This method therefore resumes that stored turn without
        appending a duplicate user row, and keeps the ordinary policy/audit path
        for every allowed tool.
        """
        # The caller can narrow recovery, but cannot expand this process-owned
        # safety boundary by passing additional tool names.
        allowed = frozenset(allowed_tools) & INCIDENT_ALLOWED_TOOLS
        if not await self.acquire_session(session_id):
            raise AppError(ErrorCode.AGENT_SESSION_BUSY, "自动诊断会话正在处理中")

        async def ignore_event(_event: str, _data: Any) -> None:
            return None

        run: AgentRun | None = None
        try:
            async with self.session_factory() as session:
                history = await AgentMessageRepository(session).list_by_session(session_id)
            last_user = next(
                (message for message in reversed(history) if message.role == "user"),
                None,
            )
            if last_user is None:
                raise AppError(
                    ErrorCode.AGENT_SESSION_NOT_FOUND,
                    "自动诊断会话没有可继续的用户消息",
                )
            run = await self._begin_legacy_run(
                session_id,
                agent_name=LEGACY_INCIDENT_AGENT,
                trigger_seq=last_user.seq,
            )
            tracker = _LegacyRunTracker(self.run_service, run.id, ignore_event)
            await self._run_turn(
                session_id,
                last_user.content or "",
                request,
                tracker,
                persist_user=False,
                allowed_tools=allowed,
                spec_name=LEGACY_INCIDENT_AGENT,
            )
            await self._end_legacy_run(run, *tracker.outcome())
            return {"status": "completed"}
        except AppError as exc:
            explanation = (
                f"自动诊断暂不可用（{exc.code}）：{exc.message}。"
                "故障记录已保存，修好 LLM 配置或模型能力后可以继续在本会话里问。"
            )
            await self._save_incident_failure(session_id, explanation)
            await self._end_legacy_run(
                run,
                AgentRunStatus.FAILED,
                (
                    BUDGET_EXCEEDED_FINISH_REASON
                    if exc.code == ErrorCode.BUDGET_EXCEEDED
                    else INCIDENT_FAILED_FINISH_REASON
                ),
                error_code=str(exc.code),
                error_message=explanation,
            )
            return {"status": "failed", "code": str(exc.code)}
        except asyncio.CancelledError:
            # 同一个中断端点也能停自动诊断，但它的话术不能沿用「服务关闭」那套：
            # 用户按的是停止，不是我们关机（规格 §2.2）。
            interrupted = run is not None and run.id in self._interrupted
            explanation = (
                "自动诊断已被你中断，故障记录已保存。可以继续在本会话里问；"
                "已经派发出去的操作不会自动重放。"
                if interrupted
                else "自动诊断在服务关闭时中断，故障记录已保存。可以继续在本会话里问；"
                "已经派发出去的操作不会自动重放。"
            )
            await self._save_incident_failure(session_id, explanation)
            if interrupted:
                await self._end_legacy_run(
                    run, AgentRunStatus.INTERRUPTED, INTERRUPTED_FINISH_REASON
                )
            raise
        except Exception as exc:  # noqa: BLE001 - preserve the durable incident conversation
            logger.warning("自动诊断执行失败：%s", type(exc).__name__)
            explanation = (
                "自动诊断暂不可用（LLM_REQUEST_FAILED）。故障记录已保存，"
                "检查 LLM 配置和服务日志后可以继续在本会话里问。"
            )
            await self._save_incident_failure(session_id, explanation)
            await self._end_legacy_run(
                run,
                AgentRunStatus.FAILED,
                INCIDENT_FAILED_FINISH_REASON,
                error_code=str(ErrorCode.LLM_REQUEST_FAILED),
                error_message=explanation,
            )
            return {"status": "failed", "code": str(ErrorCode.LLM_REQUEST_FAILED)}
        finally:
            if run is not None:
                self._forget_run(session_id, run.id)
            self.release_session(session_id)

    async def _save_incident_failure(self, session_id: str, explanation: str) -> None:
        try:
            await self._persist_message(
                session_id,
                role="assistant",
                content=explanation,
                finish_reason=INCIDENT_FAILED_FINISH_REASON,
            )
        except Exception as exc:  # noqa: BLE001 - don't replace the original diagnostic result
            logger.warning("无法保存自动诊断说明：%s", type(exc).__name__)

    def _forget_turn_task(self, task: asyncio.Task[None]) -> None:
        self._turn_tasks.discard(task)
        if not task.cancelled():
            task.exception()

    def _forget_queue_waiter(self, task: asyncio.Task[Any]) -> None:
        self._queue_waiters.discard(task)

    def _cached_capabilities(self, key: tuple[str, str]) -> dict[str, Any] | None:
        """读能力缓存；探查本身失败的结果过了保鲜期就作废，下一轮重新探。

        成功的探查（模型确实支持 / 确实不支持工具调用）永不过期：模型能力不会因为
        一次网络抖动而改变，改 ``config.yaml`` 仍需重启服务（docs/13 §8.13）。
        """
        result = self._capability_cache.get(key)
        if result is None:
            return None
        deadline = self._capability_expiry.get(key)
        if deadline is not None and time.monotonic() >= deadline:
            self._capability_cache.pop(key, None)
            self._capability_expiry.pop(key, None)
            return None
        return result

    def _remember_capabilities(
        self, key: tuple[str, str], result: dict[str, Any], *, probe_failed: bool
    ) -> None:
        """写入能力缓存。

        ``probe_failed`` 的语义是「这一次探查请求没跑通」（超时、连接层失败、上游
        4xx/5xx），而不是「模型明确不支持」。只有后者能长期缓存：把前者也钉死，
        一次抖动就会让之后每一轮都失败在能力检查上。
        """
        self._capability_cache[key] = result
        if probe_failed:
            self._capability_expiry[key] = (
                time.monotonic() + CAPABILITY_FAILURE_TTL_SECONDS
            )
        else:
            self._capability_expiry.pop(key, None)

    async def check_capabilities(self, request: Any) -> dict[str, Any]:
        """Probe function calling and image input without executing a tool."""
        model = get_settings().llm.model
        try:
            settings = await self._effective_settings(request)
        except AppError as exc:
            reason = (
                "数据库中的 LLM API Key 无法解密；请配置有效的 YAML/env API Key"
                if exc.code == ErrorCode.LLM_ENCRYPTION_KEY_INVALID
                else "当前 LLM 配置不可用，无法检查模型能力"
            )
            return {
                "model": model,
                "tool_calling": False,
                "vision": False,
                "ready": False,
                "status": _capability_status(exc.code),
                "degraded_reason": reason,
            }
        model = settings.llm.model
        if not settings.llm.base_url or not settings.llm.api_key or not model:
            result = {
                "model": model,
                "tool_calling": False,
                "vision": False,
                "ready": False,
                "status": "llm_not_configured",
                "degraded_reason": "请先配置 LLM base_url、api_key 和 model",
            }
            # LLM 配置在设置页可以随时补上（落库），所以「还没配」同样按失败态过期，
            # 不能等到重启才自愈。
            self._remember_capabilities(
                (model, settings.llm.base_url), result, probe_failed=True
            )
            return result

        return await self._probe_capabilities(settings, model=model, base_url=settings.llm.base_url)

    async def recon(
        self,
        question: str,
        *,
        hints: Sequence[str] = (),
        max_steps: int | None = None,
        settings: Settings | None = None,
    ) -> dict[str, Any]:
        """跑一次只读侦察 subagent，返回给父上下文看的报告（M48 规格 §4.3）。

        与 ``_run_turn_body`` 完全解耦：子 agent 不占会话锁、不写 ``agent_message``、
        不碰设备，只读工作区。父会话能看到的只有这份报告：子 agent 的中间步骤、
        被读出的文件正文与被截断的原始输出都留在它自己的消息列表里。

        :param question: 上层要查清的问题。
        :param hints: 「去哪找」的提示，最多 5 条。
        :param max_steps: 步数上限；``None`` 时取 ``agent.recon_max_steps``。
        :param settings: 已解析的设置；``None`` 时用 ``settings_provider``。
        :returns: ``{question, summary, findings, uncertainties, steps_used, stopped, budget}``。
        """

        resolved = settings if settings is not None else self._recon_settings()
        if inspect.isawaitable(resolved):
            # 装配层可能把 ``SettingService.refresh``（async）接成 settings_provider。
            resolved = await resolved
        budget = budget_from_settings(resolved)
        steps = max_steps if isinstance(max_steps, int) and max_steps > 0 else budget.max_steps
        client, owned = self._make_client(resolved, timeout_seconds=RECON_TIMEOUT_SECONDS)
        try:
            return await run_recon(
                ReconDeps(
                    settings=resolved,
                    client=client,
                    model=resolved.llm.model,
                    max_steps=min(steps, RECON_MAX_STEPS_CAP),
                    max_chars=budget.max_chars,
                    output_chars=budget.output_chars,
                ),
                question=question,
                hints=hints,
            )
        finally:
            if owned:
                await client.aclose()

    def _recon_settings(self) -> Settings:
        """侦察用的设置：与其它路径同一口径，走 ``settings_provider``。"""

        provider = self.settings_provider
        if callable(provider):
            return provider()
        return get_settings()

    async def _probe_capabilities(
        self, settings: Settings, *, model: str, base_url: str
    ) -> dict[str, Any]:
        """Probe one effective model/endpoint pair and cache both capabilities."""
        key = (model, base_url)
        probe_settings = settings.model_copy(
            update={
                "llm": settings.llm.model_copy(
                    update={"model": model, "base_url": base_url}
                )
            }
        )
        tool_calling, tool_error = False, None
        vision, vision_error = False, None
        client, owned = self._make_client(probe_settings)
        try:
            tool_calling, tool_error = await probe_tool_calling(client, model)
            vision, vision_error = await probe_vision(client, model)
        finally:
            if owned:
                await client.aclose()

        ready = tool_calling
        status = "ready" if tool_calling and vision else (
            "vision_degraded" if tool_calling else "tool_calling_unavailable"
        )
        if tool_error is not None and not tool_calling:
            status = _capability_status(tool_error.code)
        degraded_reason = None
        if not tool_calling:
            degraded_reason = _probe_degraded_reason(tool_error)
        elif not vision:
            degraded_reason = "当前模型不支持图片输入；截图和原子屏幕操作已禁用"
        result = {
            "model": model,
            "tool_calling": tool_calling,
            "vision": vision,
            "ready": ready,
            "status": status,
            "degraded_reason": degraded_reason,
        }
        if tool_error is not None or vision_error is not None:
            # 探查失败会让「工具调用：不可用」这类结论失真，日志里必须留下真实原因。
            # 这条路径此前一个字都不记（docs/13 §8.30 记的是同一类问题：能力自检失败
            # 却查不到原因）。失败态只缓存一小会儿，见 ``_remember_capabilities``。
            logger.warning(
                "Agent 能力探查失败：model=%s base_url=%s tool=%s vision=%s",
                model,
                base_url,
                _error_brief(tool_error),
                _error_brief(vision_error),
            )
        self._remember_capabilities(
            key, result, probe_failed=tool_error is not None or vision_error is not None
        )
        return result

    async def _produce_turn(
        self, queue: asyncio.Queue[Any], session_id: str, content: str, request: Any
    ) -> None:
        async def emit(event: str, data: Any) -> None:
            await queue.put(sse_event(event, data))

        run: AgentRun | None = None
        try:
            run = await self._begin_legacy_run(session_id, agent_name=LEGACY_CHAT_AGENT)
            tracker = _LegacyRunTracker(self.run_service, run.id, emit)
            await self._run_turn(session_id, content, request, tracker)
            # 先结束运行再生成标题：标题不是这一轮的一部分，运行的时长与终态只算对话本身。
            await self._end_legacy_run(run, *tracker.outcome())
            # 首轮回答完成后补标题，位置在 message_end 之后、收流之前：前端拿到
            # session_title 就能立刻回填，不用为标题再轮询一次。
            await self._generate_session_title(session_id, request, emit)
        except AppError as exc:
            # 这一行是这类故障唯一的服务端留痕：轮次失败此前只推一条 SSE error 事件，
            # 事件随页面一起消失，第二天再问「Agent 怎么又不好使了」就无从查起。
            logger.warning("Agent 轮次失败：session=%s %s", session_id, _error_brief(exc))
            await self._record_turn_failure(session_id, exc.code, exc.message)
            await self._end_legacy_run(
                run,
                AgentRunStatus.FAILED,
                (
                    BUDGET_EXCEEDED_FINISH_REASON
                    if exc.code == ErrorCode.BUDGET_EXCEEDED
                    else TURN_FAILED_FINISH_REASON
                ),
                error_code=str(exc.code),
                error_message=exc.message,
            )
            await emit(
                "error",
                {
                    "code": str(exc.code),
                    "message": exc.message,
                    **({"details": exc.details} if exc.details is not None else {}),
                },
            )
        except asyncio.CancelledError:
            # 主动中断的收尾已经在 ``_run_turn`` 里做完，这里只把取消继续抛出；
            # 被动断开（客户端断网/关页面）不会走到这里：producer 是 detached 的，
            # SSE 消费端的取消与它无关。
            if run is not None and run.id in self._interrupted:
                await self._end_legacy_run(
                    run, AgentRunStatus.INTERRUPTED, INTERRUPTED_FINISH_REASON
                )
            # 不是用户中断（服务关闭）时不写终态：下次启动由 RunRecovery 收尾并补恢复提示。
            raise
        except Exception as exc:  # noqa: BLE001 - keep an SSE stream from leaking details
            logger.exception(
                "Agent turn failed: session=%s error=%s", session_id, type(exc).__name__
            )
            await self._record_turn_failure(
                session_id, ErrorCode.LLM_REQUEST_FAILED, TURN_FAILURE_GENERIC_MESSAGE
            )
            await self._end_legacy_run(
                run,
                AgentRunStatus.FAILED,
                TURN_FAILED_FINISH_REASON,
                error_code=str(ErrorCode.LLM_REQUEST_FAILED),
                error_message=TURN_FAILURE_GENERIC_MESSAGE,
            )
            await emit(
                "error",
                {
                    "code": str(ErrorCode.LLM_REQUEST_FAILED),
                    "message": TURN_FAILURE_GENERIC_MESSAGE,
                },
            )
        finally:
            if run is not None:
                self._forget_run(session_id, run.id)
            self.release_session(session_id)
            await queue.put(_SSE_END)

    async def _record_turn_failure(
        self, session_id: str, code: ErrorCode | str, message: str | None
    ) -> None:
        """给「用户消息已落库、这一轮没答上」的会话补一条说明。

        没有它，这一轮在界面上就等于没发生过：刷新之后只剩用户那句孤零零的话，
        看起来像 Agent 把消息吃掉了。只在用户消息已经是最后一条时补——失败发生在
        落库之前（未配置 LLM、会话不存在）本来就没有尾巴，不该凭空多一条助手消息。
        写不进去也不能顶掉真正的错误，所以这里吞掉自己的异常。
        """
        if code not in TURN_FAILURE_NOTICE_CODES:
            return
        try:
            async with self.session_factory() as session:
                history = await AgentMessageRepository(session).list_by_session(session_id)
            if not history or str(history[-1].role) != "user":
                return
            tail = (
                TURN_FAILURE_NOTICE_OVERFLOW_TAIL
                if str(code) == ErrorCode.LLM_CONTEXT_OVERFLOW.value
                else TURN_FAILURE_NOTICE_TAIL
            )
            await self._persist_message(
                session_id,
                role="assistant",
                content=f"本轮没有完成（{code}）：{message or '原因未知'}。{tail}",
                finish_reason=TURN_FAILED_FINISH_REASON,
            )
        except Exception:  # noqa: BLE001 - 说明只是收尾，不能盖过真正的失败
            logger.warning("Agent 轮次失败说明写入失败：session=%s", session_id, exc_info=True)

    async def _run_turn(
        self,
        session_id: str,
        content: str,
        request: Any,
        emit: Callable[[str, Any], Any],
        *,
        persist_user: bool = True,
        allowed_tools: Collection[str] | None = None,
        spec_name: str = "legacy_chat",
    ) -> None:
        """把这一轮交给 :meth:`_run_turn_body`（规格 §3.1）。

        运行与 task 的登记已提前到 :meth:`_begin_legacy_run`：生命周期的发起方是
        ``_produce_turn`` / ``run_incident``，本方法只负责跑循环。自动诊断没有
        producer，同一个中断端点也能停它——靠的是 ``_begin_legacy_run`` 登记的那把
        task（就是调用方的 task）。
        """
        await self._run_turn_body(
            session_id,
            content,
            request,
            emit,
            persist_user=persist_user,
            allowed_tools=allowed_tools,
            spec_name=spec_name,
        )

    async def _run_turn_body(
        self,
        session_id: str,
        content: str,
        request: Any,
        emit: Callable[[str, Any], Any],
        *,
        persist_user: bool = True,
        allowed_tools: Collection[str] | None = None,
        spec_name: str = "legacy_chat",
    ) -> None:
        settings = await self.ensure_configured(request, session_id)
        async with self.session_factory() as session:
            stored_session = await AgentSessionRepository(session).get(session_id)
            assert stored_session is not None
            model = stored_session.model
            base_url = stored_session.base_url or settings.llm.base_url
            if stored_session.incident_key is not None and (
                not model
                or model.strip().casefold() == "unconfigured"
                or not stored_session.base_url
            ):
                model = settings.llm.model
                base_url = settings.llm.base_url
                stored_session.model = model
                stored_session.base_url = base_url or None
                stored_session.updated_at = utcnow()
                await session.commit()
        if persist_user:
            user_message = await self._persist_message(session_id, role="user", content=content)
        else:
            async with self.session_factory() as session:
                history = await AgentMessageRepository(session).list_by_session(session_id)
            user_message = next(
                (message for message in reversed(history) if message.role == "user"),
                None,
            )
            if user_message is None:
                raise AppError(
                    ErrorCode.AGENT_SESSION_NOT_FOUND,
                    "自动诊断会话没有可继续的用户消息",
                )
        capability_key = (model, base_url)
        capabilities = self._cached_capabilities(capability_key)
        if capabilities is None:
            capability_settings = settings.model_copy(
                update={
                    "llm": settings.llm.model_copy(
                        update={"model": model, "base_url": base_url}
                    )
                }
            )
            capabilities = await self._probe_capabilities(
                capability_settings, model=model, base_url=base_url
            )
        if not capabilities["tool_calling"]:
            raise AppError(
                ErrorCode.LLM_REQUEST_FAILED,
                capabilities["degraded_reason"]
                or "当前模型未通过 function calling 能力检查，无法进行 Agent 对话",
            )
        await emit(
            "message_start",
            {
                "session_id": session_id,
                "message_id": user_message.id,
                "seq": user_message.seq,
                "capability_status": capabilities["status"],
                "degraded_reason": capabilities["degraded_reason"],
            },
        )
        summary = await self._status_summary(request)
        knowledge_context = await self._knowledge_context(request)
        client_settings = settings.model_copy(
            update={
                "llm": settings.llm.model_copy(
                    update={"model": model, "base_url": base_url}
                )
            }
        )
        client, owned = self._make_client(client_settings)
        # 本轮的运行上下文与中间件链（规格 §4.2.11）：循环交给 AgentLoop，门面只做
        # v2 的前置步骤与收尾。每轮现取门面上的服务属性：测试在构造后替换的替身照样生效。
        registry = self.confirmation_service.registry
        vision_enabled = bool(capabilities["vision"])
        # 本轮的 emit 统一走事件总线：事件名、字段与顺序仍与 v2 逐字一致。
        bus = RunEventBus()

        async def forward(event: RunEvent) -> None:
            await emit(event.name, event.data)

        bus.subscribe(forward)
        # 本轮运行的真实 id（由 ``_begin_legacy_run`` 建出）：消息落库靠它带
        # ``run_id``，运行树与查询端点也靠它。门面这一轮的运行都是根节点，
        # ``root_run_id`` 就是自己、没有父运行（M70 的子代理才会不同）。
        run_id = self._current_runs.get(session_id)
        if run_id is None:  # pragma: no cover - 调用方必须先 _begin_legacy_run
            raise AppError(ErrorCode.INTERNAL_ERROR, "本轮运行尚未登记")
        ctx = RunContext(
            run_id=run_id,
            root_run_id=run_id,
            parent_run_id=None,
            session_id=session_id,
            ticket_id=None,
            spec=builtin_spec(spec_name),
            app=getattr(request, "app", None),
            request=request,
            emit=bus.emit,
            interrupted_now=lambda: self._interrupted_now(session_id),
            allowed_tools=(
                frozenset(allowed_tools) if allowed_tools is not None else None
            ),
            vision_enabled=vision_enabled,
        )
        # 上下文组装中间件的三个入参由门面每轮写一次（v2 直接调 build_model_context）。
        ctx.state[ContextLayoutMiddleware.name] = {
            "knowledge_context": knowledge_context,
            "status_summary": summary,
            "max_context_chars": self.max_context_chars,
        }

        async def call_model(run: RunContext, req: ModelRequest) -> ModelResponse:
            return await collect_completion(
                client,
                model,
                req.messages,
                req.tools,
                emit=run.emit,
                draft=run.draft,
            )

        def tool_schemas(run: RunContext) -> list[dict[str, Any]]:
            # 每步现算：本轮里开启的扩展组下一步生效。
            return self._tool_schemas(
                registry,
                vision_enabled=run.vision_enabled,
                allowed_tools=(
                    allowed_tools
                    if allowed_tools is not None
                    else self.session_allowed_tools(session_id)
                ),
            )

        # 成本账本由 lifespan 挂在 ``app.state``（T8）；没装配时预算中间件不进链。
        cost_ledger = getattr(
            getattr(getattr(request, "app", None), "state", None),
            "cost_ledger",
            None,
        )
        loop = AgentLoop(
            chain=self._legacy_chain(
                cost_ledger=cost_ledger, settings_provider=lambda: settings
            ),
            transcript=_LegacyTranscript(self, vision_enabled=vision_enabled),
            call_model=call_model,
            tool_schemas=tool_schemas,
            registry=registry,
        )
        try:
            await loop.run(ctx)
        finally:
            if owned:
                await client.aclose()

    def _legacy_chain(
        self,
        *,
        cost_ledger: Any = None,
        settings_provider: Callable[[], Any] | None = None,
    ) -> MiddlewareChain:
        """v2 会话的中间件链（05 §5.2 的子集：1、2、3、13、17、19）。

        ``BudgetMiddleware`` 只在装配了成本账本时插入第 2 位：没有账本就无从检查
        预算，v2 的链保持原样（默认 0 上限时同样不产生任何行为差异）。
        """
        middlewares: list[Middleware] = [TraceMiddleware()]
        if cost_ledger is not None:
            middlewares.append(
                BudgetMiddleware(
                    ledger=cost_ledger, settings_provider=settings_provider
                )
            )
        middlewares.extend(
            [
                ContextLayoutMiddleware(),
                LoopGuardMiddleware(),
                ClarificationMiddleware(
                    question_service=self.question_service,
                    registry=self.confirmation_service.registry,
                ),
                PolicyMiddleware(
                    confirmation_service=self.confirmation_service,
                    session_factory=self.session_factory,
                    poll_seconds=self.confirmation_poll_seconds,
                ),
            ]
        )
        return MiddlewareChain.for_kind("worker", middlewares)

    def build_chain(
        self,
        kind: str,
        *,
        device_lease_service: Any = None,
        cost_ledger: Any = None,
        settings_provider: Callable[[], Any] | None = None,
    ) -> MiddlewareChain:
        """v3 运行的链（总控 / Worker / 子代理）：总控链里没有 14/16/18。"""
        return build_agent_chain(
            kind,
            confirmation_service=self.confirmation_service,
            session_factory=self.session_factory,
            question_service=self.question_service,
            confirmation_poll_seconds=self.confirmation_poll_seconds,
            device_lease_service=device_lease_service,
            cost_ledger=cost_ledger,
            settings_provider=settings_provider,
        )

    async def _persist_message(
        self,
        session_id: str,
        *,
        role: str,
        content: str | None = None,
        reasoning_content: str | None = None,
        tool_calls: list[dict[str, Any]] | None = None,
        tool_call_id: str | None = None,
        tool_name: str | None = None,
        audit_id: int | None = None,
        prompt_tokens: int | None = None,
        completion_tokens: int | None = None,
        finish_reason: str | None = None,
        latency_ms: int | None = None,
    ) -> AgentMessage:
        # 这一轮运行里写出的每条非用户消息都带 ``run_id``；用户消息是触发这一轮的
        # 输入而不是产物，运行用 ``trigger_seq`` 指向它（规格 §4.2.4 第 11 步）。
        run_id = self._current_runs.get(session_id)
        now = utcnow()
        async with self.session_factory() as session:
            sessions = AgentSessionRepository(session)
            stored_session = await sessions.get(session_id)
            if stored_session is None:
                raise AppError(ErrorCode.AGENT_SESSION_NOT_FOUND, "Agent 会话不存在")
            messages = AgentMessageRepository(session)
            seq = await messages.next_seq(session_id)
            stored = await messages.create(
                AgentMessage(
                    session_id=session_id,
                    seq=seq,
                    role=role,
                    content=content,
                    reasoning_content=reasoning_content,
                    tool_calls=tool_calls,
                    tool_call_id=tool_call_id,
                    tool_name=tool_name,
                    audit_id=audit_id,
                    prompt_tokens=prompt_tokens,
                    completion_tokens=completion_tokens,
                    finish_reason=finish_reason,
                    latency_ms=latency_ms,
                    run_id=None if role == AgentRole.USER else run_id,
                    created_at=now,
                )
            )
            if run_id is not None and role == AgentRole.USER:
                await AgentRunRepository(session).set_trigger_seq_if_missing(run_id, seq)
            stored_session.message_count += 1
            stored_session.updated_at = now
            stored_session.last_message_at = now
            if prompt_tokens is not None:
                stored_session.prompt_tokens += prompt_tokens
            if completion_tokens is not None:
                stored_session.completion_tokens += completion_tokens
            await session.commit()
            return stored

    async def _generate_session_title(
        self,
        session_id: str,
        request: Any,
        emit: Callable[[str, Any], Any],
    ) -> str | None:
        """给还没有标题的会话补一个标题，按「首轮回答结束后、收流前」的时机。

        时机是刻意的：标题落库后紧跟着推 ``session_title`` 再结束 SSE，前端一次往返
        就能回填，不需要为标题额外轮询。代价是这一轮「完成」得等这次调用结束：所以
        它是独立的一次短调用（``TITLE_MAX_CHARS`` 个字的输出），失败吞掉、只记
        日志：标题只是导航用的装饰，不能因为上游抖动让一轮已经成功的回答变成报错。
        """
        try:
            async with self.session_factory() as session:
                stored = await AgentSessionRepository(session).get(session_id)
                if stored is None or (stored.title or "").strip():
                    return None
                model = stored.model
                base_url = stored.base_url
                messages = await AgentMessageRepository(session).list_by_session(session_id)
            seed = next(
                (message.content or "" for message in messages if message.role == "user"),
                "",
            ).strip()
            if not seed:
                return None
            # 标题跟着会话自己的模型走，而不是当前设置里的模型：会话的 model/base_url 是
            # 创建时的快照（docs/04 §5.7），换过设置的老会话仍要用它当初的那个端点。
            settings = await self._effective_settings(request)
            model = model or settings.llm.model
            client, owned = self._make_client(
                settings.model_copy(
                    update={
                        "llm": settings.llm.model_copy(
                            update={"model": model, "base_url": base_url or settings.llm.base_url}
                        )
                    }
                ),
                # 自定义 client_factory（测试替身）不认这个参数，只透给真实客户端。
                timeout_seconds=TITLE_TIMEOUT_SECONDS if self.client_factory is None else None,
                # 标题只是导航装饰，不重试：退避重试会把 20 秒的预算乘三，用户为了
                # 一个标题多等一轮收流不值得。
                max_attempts=1 if self.client_factory is None else None,
            )
            try:
                title = await self._read_title(client, model, seed[:TITLE_SEED_MAX_CHARS])
            finally:
                if owned:
                    await client.aclose()
            if not title:
                return None
            async with self.session_factory() as session:
                stored = await AgentSessionRepository(session).get(session_id)
                if stored is None or (stored.title or "").strip():
                    return None
                stored.title = title
                stored.updated_at = utcnow()
                await session.commit()
        except Exception:  # noqa: BLE001 - 标题失败不影响这一轮的结论
            logger.warning("Agent session %s title generation failed", session_id, exc_info=True)
            return None
        await emit("session_title", {"session_id": session_id, "title": title})
        return title

    async def _read_title(self, client: Any, model: str, seed: str) -> str | None:
        """跑一次「只取文本」的补全，返回清洗后的标题；空输出返回 None。"""
        parts: list[str] = []
        async for chunk in client.stream_chat_completion(
            model,
            [{"role": "user", "content": f"{TITLE_PROMPT}\n\n用户的问题：\n{seed}"}],
            [],
        ):
            if not isinstance(chunk, Mapping):
                continue
            for choice in chunk.get("choices") or []:
                if not isinstance(choice, Mapping):
                    continue
                delta = choice.get("delta") or {}
                if isinstance(delta, Mapping) and isinstance(delta.get("content"), str):
                    parts.append(delta["content"])
        return _clean_title("".join(parts))

    async def _effective_settings(self, request: Any) -> Settings:
        if self.settings_provider is not None:
            settings = self.settings_provider(request)
            if inspect.isawaitable(settings):
                settings = await settings
            error = None
        else:
            state = getattr(getattr(request, "app", None), "state", None)
            service = getattr(state, "setting_service", None)
            if service is not None:
                settings = await service.refresh()
                error = getattr(service, "llm_encryption_error", None)
            else:
                settings = get_settings()
                error = None
        if not isinstance(settings, Settings):
            raise TypeError("settings provider must return Settings")
        if not settings.llm.api_key and error is not None:
            raise AppError(
                ErrorCode.LLM_ENCRYPTION_KEY_INVALID,
                "数据库中的 LLM API Key 无法解密；请修复加密密钥或配置有效的 YAML/env API Key",
            )
        return settings

    def _make_client(
        self,
        settings: Settings,
        *,
        timeout_seconds: float | None = None,
        max_attempts: int | None = None,
    ) -> tuple[Any, bool]:
        if self.client_factory is not None:
            return self.client_factory(settings), False
        kwargs: dict[str, Any] = {}
        if timeout_seconds is not None:
            kwargs["timeout_seconds"] = timeout_seconds
        if max_attempts is not None:
            kwargs["max_attempts"] = max_attempts
        return (
            OpenAICompatibleClient(
                base_url=settings.llm.base_url,
                api_key=settings.llm.api_key,
                **kwargs,
            ),
            True,
        )

    def _tool_schemas(
        self,
        registry: ToolRegistry,
        *,
        vision_enabled: bool = True,
        allowed_tools: Collection[str] | None = None,
    ) -> list[dict[str, Any]]:
        """模型可见的工具声明；``model_schema`` 覆盖只改这里（M56，docs/13 §8.79）。

        完整 Pydantic schema 会把「12 种任务的完整字段图」重复塞进每个带任务参数
        的工具（实测三个工具各 36 KB、合计约 110 KB，占初始 prompt 六成）。模型侧
        换紧凑 schema 后仍由 ``registry.validate`` 用完整模型校验；REST 目录、
        OpenAPI 与运行期校验都不受影响。

        M57 再统一去掉 Pydantic 自动生成的 ``title`` 键（80 个工具声明里约 6.4 K 字，
        纯冗余标签），见 :func:`_compact_model_schema`。
        """
        return [
            {
                "type": "function",
                "function": {
                    "name": definition.name,
                    "description": definition.description,
                    "parameters": _compact_model_schema(
                        definition.model_schema
                        if definition.model_schema is not None
                        else definition.params_model.model_json_schema(
                            by_alias=True, mode="validation"
                        )
                    ),
                },
            }
            for definition in registry.list()
            if definition.model_visible
            if vision_enabled or definition.name not in SCREEN_TOOL_NAMES
            if allowed_tools is None or definition.name in allowed_tools
        ]

    def enabled_tool_groups(self, session_id: str) -> set[str]:
        """本会话已开启的扩展工具组（M57）。"""
        return set(self._enabled_tool_groups.get(session_id, ()))

    def enable_tool_groups(self, session_id: str, groups: Collection[str]) -> set[str]:
        """开启扩展组并返回本会话已开启集合；未知/核心组名被忽略。"""
        from maa_api.agent.tools.catalog import DEFERRED_TOOL_GROUPS

        valid = {str(group) for group in groups if str(group) in DEFERRED_TOOL_GROUPS}
        current = self._enabled_tool_groups.setdefault(session_id, set())
        current.update(valid)
        # 会话可能很多：上限 256 条，超出时丢最早插入的会话（重启后模型可再开）。
        while len(self._enabled_tool_groups) > 256:
            self._enabled_tool_groups.pop(next(iter(self._enabled_tool_groups)))
        return set(current)

    def session_allowed_tools(self, session_id: str) -> set[str]:
        """模型本轮可见的工具名：常驻组 + 已开启扩展组（M57，docs/13 §8.80）。"""
        from maa_api.agent.tools.catalog import DEFERRED_TOOL_GROUPS

        enabled = self.enabled_tool_groups(session_id)
        registry = self.confirmation_service.registry
        return {
            definition.name
            for definition in registry.list()
            if definition.model_visible
            and definition.name not in V3_ONLY_TOOL_NAMES
            and (
                definition.group not in DEFERRED_TOOL_GROUPS
                or definition.group in enabled
            )
        }

    def tool_group_catalog(self, session_id: str) -> dict[str, Any]:
        """``list_tool_groups`` 的数据源：扩展组、每组工具、本会话已开启哪些。"""
        from maa_api.agent.tools.catalog import DEFERRED_TOOL_GROUPS

        registry = self.confirmation_service.registry
        deferred: dict[str, list[str]] = {}
        core_groups: set[str] = set()
        for definition in registry.list():
            if not definition.model_visible:
                continue
            # v2 会话的目录不列 v3 专属工具（M70 §4.5.12）。
            if definition.name in V3_ONLY_TOOL_NAMES:
                continue
            if definition.group in DEFERRED_TOOL_GROUPS:
                deferred.setdefault(definition.group, []).append(definition.name)
            else:
                core_groups.add(definition.group)
        enabled = self.enabled_tool_groups(session_id)
        return {
            "deferred_groups": {
                group: {"tools": sorted(names), "enabled": group in enabled}
                for group, names in sorted(deferred.items())
            },
            "enabled_groups": sorted(enabled),
            "core_groups": sorted(core_groups),
            "note": "扩展组默认不暴露以省上下文；需要时用 enable_tool_group 打开，核心组工具始终可用。",
        }

    async def _knowledge_context(self, request: Any) -> str:
        """本轮的知识库段；读不出来就降级成 :data:`KNOWLEDGE_UNAVAILABLE`。

        知识库只是操作指引，读失败不该让整个诊断或对话失败：与 ``_status_summary``
        里设备状态不可用时的处理同一口径（规格 §4.1「知识库读取失败时注入
        『知识库暂不可用』，不阻塞本轮」）。
        """
        service = self.knowledge_service
        if service is None:
            state = getattr(getattr(request, "app", None), "state", None)
            service = getattr(state, "knowledge_service", None)
        if service is None:
            logger.warning("应用未装配 knowledge_service，知识库段降级为「知识库暂不可用」")
            return KNOWLEDGE_UNAVAILABLE
        try:
            return await service.render_context()
        except Exception:  # noqa: BLE001 - 知识库只是可选上下文
            logger.warning("知识库上下文生成失败，本轮降级为「知识库暂不可用」", exc_info=True)
            return KNOWLEDGE_UNAVAILABLE

    async def _status_summary(self, request: Any) -> Any:
        # 服务端本地时间，ISO 8601 带时区偏移：模型必须知道「现在」才能把截图时间和
        # 问题对齐（规格 §4.3）。无论走默认分支还是自定义分支，它都是第一项。
        now = datetime.now().astimezone().isoformat()
        provider = self.context_provider
        state = getattr(getattr(request, "app", None), "state", None)
        if provider is None:
            provider = getattr(state, "agent_context_provider", None)
        if provider is not None:
            result = provider(request)
            summary = await result if inspect.isawaitable(result) else result
            if isinstance(summary, Mapping):
                # 保留 provider 的键序与内容，只在最前面补 now；provider 自带的 now
                # 不作数，但位置仍留在第一项。
                merged = {"now": now}
                merged.update(summary)
                merged["now"] = now
                return merged
            # 非 Mapping 的 provider 结果没法就地补字段，包一层并原样保留。
            return {"now": now, "context": summary}

        summary: dict[str, Any] = {
            "now": now,
            "core": None,
            "device": None,
            "current_pipeline": getattr(state, "current_pipeline", None),
            "queue": None,
            "recent_error": getattr(state, "recent_error", None),
        }
        supervisor = getattr(state, "core_supervisor", None)
        if supervisor is not None:
            core_state = getattr(supervisor, "state", None)
            summary["core"] = getattr(core_state, "value", str(core_state))
        device_manager = getattr(state, "device_manager", None)
        if device_manager is not None:
            try:
                summary["device"] = device_manager.snapshot()
            except Exception:  # noqa: BLE001 - status is optional context only
                summary["device"] = "unavailable"
        # M58：当前生效的默认渠道。2026-10-20 的会话里模型看不到渠道，把 StartUp 的
        # client_type 猜成 Official，而本机只装了 B 服客户端，内核启动命令直接失败，
        # 整条流水线空转了 13 分钟（docs/13 §8.81）。
        summary["channel"] = await self._effective_channel(request)
        queue_service = getattr(state, "queue_service", None)
        if queue_service is not None:
            try:
                result = queue_service.snapshot()
                snapshot = await result if inspect.isawaitable(result) else result
                summary["queue"] = snapshot
                if isinstance(snapshot, Mapping):
                    running = snapshot.get("running")
                    summary["current_pipeline"] = running or summary["current_pipeline"]
                    running_id = running.get("pipeline_id") if isinstance(running, Mapping) else None
                    if running_id:
                        try:
                            from maa_api.db.repositories.pipeline import PipelineRepository

                            async with self.session_factory() as db_session:
                                pipeline = await PipelineRepository(db_session).get(
                                    str(running_id), with_tasks=True
                                )
                            if pipeline is not None:
                                tasks = list(getattr(pipeline, "tasks", []) or [])
                                summary["current_pipeline"] = {
                                    "pipeline_id": pipeline.id,
                                    "status": str(pipeline.status),
                                    "progress": {
                                        "total": pipeline.task_count,
                                        "completed": sum(
                                            str(task.status) == "completed" for task in tasks
                                        ),
                                        "failed": sum(
                                            str(task.status) in {"failed", "skipped"}
                                            for task in tasks
                                        ),
                                    },
                                    "error": pipeline.error_message,
                                }
                        except Exception:  # noqa: BLE001 - status is optional context only
                            pass
            except Exception:  # noqa: BLE001 - status is optional context only
                summary["queue"] = "unavailable"
        if summary["recent_error"] is None:
            try:
                from maa_api.db.repositories.log import LogRepository
                from maa_api.domain.enums import LogLevel

                async with self.session_factory() as db_session:
                    page = await LogRepository(db_session).query(
                        levels=[LogLevel.ERROR, LogLevel.CRITICAL], page=1, size=1
                    )
                if page.items:
                    entry = page.items[0]
                    summary["recent_error"] = {
                        "level": str(entry.level),
                        "message": entry.content,
                        "created_at": entry.created_at.isoformat(),
                    }
            except Exception:  # noqa: BLE001 - status is optional context only
                summary["recent_error"] = "unavailable"
        # M57：本机活动资源版本（只读本地 version.json，不做网络检查）。新活动/新关卡
        # 的作业与 Fight 需要内核资源认识关卡；版本明显落后时模型应先建议用户更新，
        # 避免先跑一次失败（2026-10-17 实机会话里因此多花了十几分钟）。
        summary["resource_version"] = _local_resource_version(state)
        return summary

    async def _effective_channel(self, request: Any) -> dict[str, Any] | None:
        """状态摘要里的生效默认渠道（M58，docs/13 §8.81）。

        渠道默认值与 ``_effective_settings`` 同一来源（设置页落库覆盖后的
        ``Settings.channel``），但**失败不抛**：状态摘要只是可选的观察数据，读不到
        设置不该让整轮对话失败。测试注入的 settings_provider 返回非 Settings 对象时
        也按「取不到」处理，由模型自己决定要不要问用户。
        """
        try:
            settings: Any
            if self.settings_provider is not None:
                settings = self.settings_provider(request)
                if inspect.isawaitable(settings):
                    settings = await settings
            else:
                state = (
                    getattr(getattr(request, "app", None), "state", None)
                    if request is not None
                    else None
                )
                provider = getattr(state, "settings_provider", None)
                if callable(provider):
                    settings = provider()
                    if inspect.isawaitable(settings):
                        settings = await settings
                else:
                    settings = get_settings()
            channel = getattr(settings, "channel", None)
            client_type = getattr(channel, "client_type", None)
            server = getattr(channel, "server", None)
            if client_type is None or server is None:
                return None
            return {"client_type": str(client_type), "server": str(server)}
        except Exception:  # noqa: BLE001 - status is optional context only
            return None

    async def _hydrate_latest_screenshot(
        self, history: Sequence[AgentMessage]
    ) -> list[dict[str, Any]]:
        """Load only the latest archived screenshot into an outbound model copy."""
        messages = [_message_dict(message) for message in history]
        latest = next(
            (
                index
                for index in range(len(messages) - 1, -1, -1)
                if _screenshot_payload(messages[index]) is not None
            ),
            None,
        )
        if latest is None:
            return messages
        payload = _screenshot_payload(messages[latest])
        if payload is None or not payload.get("screenshot_id") or any(
            payload.get(key) for key in ("data", "image", "base64")
        ):
            return messages

        screenshot_id = str(payload["screenshot_id"])
        async with self.session_factory() as session:
            record = await ScreenshotRepository(session).get(screenshot_id)
        if record is None or record.deleted_at is not None:
            payload["unavailable"] = True
        else:
            from maa_api.db import session as db_session_module

            root = Path(db_session_module.DB_PATH).parent.resolve()
            path = (root / record.path).resolve()
            if path.is_relative_to(root) and path.is_file():
                image_bytes = await asyncio.to_thread(path.read_bytes)
                payload["data"] = base64.b64encode(image_bytes).decode("ascii")
                payload["format"] = "jpeg"
            else:
                payload["unavailable"] = True
        content_value = json.loads(messages[latest]["content"])
        if isinstance(content_value, dict) and isinstance(content_value.get("result"), dict):
            content_value["result"] = payload
        else:
            content_value = payload
        messages[latest]["content"] = json.dumps(
            content_value, ensure_ascii=False, separators=(",", ":")
        )
        return messages


class _LegacyTranscript:
    """v2 会话的消息存取：读写都经过门面，测试对门面方法的替换照样生效。"""

    def __init__(self, runtime: AgentRuntime, *, vision_enabled: bool) -> None:
        self._runtime = runtime
        self.vision_enabled = vision_enabled

    async def load(self, ctx: RunContext) -> list[dict[str, Any]]:
        async with self._runtime.session_factory() as session:
            history = await AgentMessageRepository(session).list_by_session(ctx.session_id)
        if self.vision_enabled:
            return await self._runtime._hydrate_latest_screenshot(history)
        # build_model_context 的第一步本来就把 ORM 行转成 dict。
        return [_message_dict(message) for message in history]

    async def append(self, ctx: RunContext, *, role: str, **fields: Any) -> AgentMessage:
        # 每次都现取 ``_persist_message``：中断补桩用例会在构造后替换它。
        return await self._runtime._persist_message(ctx.session_id, role=role, **fields)

    async def persisted_tool_call_ids(self, ctx: RunContext) -> set[str]:
        """已经落库的 tool 消息对应的 ``tool_call_id`` 集合（v2 原样）。"""
        try:
            async with self._runtime.session_factory() as session:
                history = await AgentMessageRepository(session).list_by_session(ctx.session_id)
        except Exception:  # noqa: BLE001 - 读不到就按「都没写进去」补桩，宁多勿缺
            logger.warning(
                "中断收尾：读取历史失败 session=%s", ctx.session_id, exc_info=True
            )
            return set()
        return {
            str(message.tool_call_id)
            for message in history
            if message.role == "tool" and message.tool_call_id
        }


#: 模型侧工具声明里统一丢掉的 JSON Schema 键（M57）。``title`` 是 Pydantic 为每个
#: 字段自动生成的标签，模型拿它没用；80 个工具合计约 6.4 K 字。
MODEL_SCHEMA_DROPPED_KEYS = frozenset({"title"})


def _compact_model_schema(node: Any) -> Any:
    """递归去掉模型侧 schema 里的冗余键（M57，docs/13 §8.80）。

    只作用于发给模型的工具声明；``export_schema`` / REST 目录 / OpenAPI 与运行期
    校验都用原始 schema，契约不变。

    ``properties`` 的键是字段名，不是 schema 注解：``send_chart`` 之类真有名为
    ``title`` 的参数，不能误删。
    """
    if isinstance(node, dict):
        out: dict[str, Any] = {}
        for key, value in node.items():
            if key == "properties" and isinstance(value, dict):
                out[key] = {
                    name: _compact_model_schema(schema)
                    for name, schema in value.items()
                }
                continue
            if key in {"$defs", "definitions"} and isinstance(value, dict):
                out[key] = {
                    name: _compact_model_schema(schema)
                    for name, schema in value.items()
                }
                continue
            if key in MODEL_SCHEMA_DROPPED_KEYS:
                continue
            out[key] = _compact_model_schema(value)
        return out
    if isinstance(node, list):
        return [_compact_model_schema(item) for item in node]
    return node


def _local_resource_version(state: Any) -> str | None:
    """本机活动资源版本（repo 层 ``version.json`` 的 ``last_updated``），失败返回 None。

    只读本地文件、不做网络检查：状态摘要每轮都拼，不能因为它去拉 manifest。
    用来让模型在涉及新活动/新关卡时先判断「本机资源是不是落后了」。
    """
    service = getattr(state, "resource_service", None)
    root = getattr(service, "resource_root", None)
    if not root:
        return None
    path = Path(root) / "maa-layers" / "repo" / "resource" / "version.json"
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeDecodeError, json.JSONDecodeError):
        return None
    if not isinstance(payload, dict):
        return None
    value = payload.get("last_updated") or payload.get("version")
    return str(value) if value else None


def _clean_title(raw: str) -> str | None:
    """把模型输出收成一个标题：去掉换行、包裹的引号与句末标点，超长按字符截断。

    模型被要求「只输出标题」，但仍然可能给成 ``「日常刷剿灭」`` 或 ``标题：日常刷剿灭。``。
    不做语义判断，只做保守清洗：只剥**成对**包裹的引号/书名号，只去句末标点，
    免得把标题里正常出现的引号也啃掉。
    """
    text = " ".join(raw.split())
    wrappers = {('"', '"'), ("'", "'"), ("“", "”"), ("‘", "’"), ("「", "」"), ("《", "》")}
    while len(text) >= 2 and (text[0], text[-1]) in wrappers:
        text = text[1:-1].strip()
    # 前缀「标题：」「会话标题 -」这类噪声：只在冒号/连字符后面还有内容时剥。
    for prefix in ("会话标题", "标题"):
        if text.startswith(prefix) and text[len(prefix) : len(prefix) + 1] in (":", "：", "-", "—"):
            text = text[len(prefix) + 1 :].strip()
            break
    text = text.rstrip("。.!！?？,，;；:：")
    return text[:TITLE_MAX_CHARS].strip() or None



#: 一条失败日志的长度上限。``details`` 里可能有上游原文，日志不该被一段正文撑满。
ERROR_BRIEF_MAX_CHARS = 1000


def _error_brief(error: AppError | None) -> str:
    """把一次失败压成一行日志文本；``details`` 先脱敏（它可能带回上游原文）。"""
    if error is None:
        return "-"
    brief = f"{error.code}: {error.message or ''}".rstrip()
    if error.details:
        brief = f"{brief} details={redact_remote_data(error.details)}"
    return brief[:ERROR_BRIEF_MAX_CHARS]


def _capability_status(code: ErrorCode | str) -> str:
    if str(code) == ErrorCode.LLM_ENCRYPTION_KEY_INVALID.value:
        return "llm_encryption_key_invalid"
    if str(code) == ErrorCode.LLM_NOT_CONFIGURED.value:
        return "llm_not_configured"
    return "llm_unavailable"


def _probe_degraded_reason(tool_error: AppError | None) -> str:
    """能力探查没有拿到工具调用时，给用户一句能指向真实原因的话。

    模型名不存在、端点鉴权失败、中转站不转发 ``tools`` 都会落到这里。只报「未通过
    function calling 能力检查」会把这些原因混成一条，用户看不出该改配置还是换模型；
    因此上游给了可读原因（``llm.py`` 已脱敏并截断）就带出来。连接层失败（TLS 证书
    校验、域名解析、连接被拒）没有上游响应，原因在 ``details.upstream_error`` 里，
    同样带出来：否则这类故障只会显示一句「LLM 上游请求失败」。
    """
    if tool_error is None:
        return "当前模型未通过 function calling 能力检查，无法进行 Agent 对话"
    prefix = "当前模型无法进行 Agent 对话"
    details = tool_error.details if isinstance(tool_error.details, Mapping) else {}
    parts: list[str] = []
    upstream_status = details.get("upstream_status")
    upstream_code = details.get("upstream_code")
    if upstream_status is not None or upstream_code is not None:
        described = "，".join(
            item
            for item in (
                f"HTTP {upstream_status}" if upstream_status is not None else "",
                str(upstream_code) if upstream_code is not None else "",
            )
            if item
        )
        parts.append(f"能力探查请求被上游拒绝（{described}）")
    else:
        parts.append("能力探查请求失败")
    cause = next(
        (
            value
            for value in (
                details.get("upstream_message"),
                details.get("upstream_error"),
            )
            if isinstance(value, str) and value
        ),
        None,
    )
    if cause is not None:
        parts.append(str(redact_remote_data(cause)))
    else:
        fallback = str(tool_error.message) if tool_error.message else ""
        if fallback and fallback != prefix:
            parts.append(fallback)
    return f"{prefix}：{'；'.join(parts)}"
