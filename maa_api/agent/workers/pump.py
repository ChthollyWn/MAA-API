"""会话泵、并发闸与 AgentLoop 的统一入口（M70 规格 §4.2.2、§4.5.1）。

每个总控 / Worker 会话一个 :class:`SessionPump`：收件箱写入提交后由
:class:`WakeDispatcher` 唤醒，泵把批处理窗口内的多次唤醒合并成一轮，Worker 再经
:class:`WorkerSlots` 的并发闸按优先级放行。一轮怎么跑由服务注入的 ``run_turn``
决定；泵只负责「什么时候跑、跑几轮、怎么停」。

:func:`run_agent` 是 v3 运行的统一入口：总控、Worker 与子代理都经它建
``RunContext``、解析模型档案、装配中间件链并调用 ``AgentLoop.run``。``RunContext``
会放进调用方传进来的 ``state["harness.ctx"]``，收尾逻辑（补汇报）靠它拿到本轮。
"""

from __future__ import annotations

import asyncio
import logging
from collections.abc import Awaitable, Callable
from dataclasses import dataclass, field
from types import SimpleNamespace
from typing import Any, Literal

from maa_api.agent.harness.builtin_middleware import _message_dict
from maa_api.agent.harness.chunks import collect_completion
from maa_api.agent.harness.context import (
    ModelRequest,
    ModelResponse,
    RunContext,
    RunOutcome,
)
from maa_api.agent.harness.events import RunEventBus
from maa_api.agent.harness.loop import AgentLoop
from maa_api.db.models import AgentMessage, utcnow
from maa_api.db.repositories.agent import (
    AgentMessageRepository,
    AgentSessionRepository,
)
from maa_api.domain.enums import AgentRunStatus
from maa_api.domain.errors import AppError, ErrorCode
from maa_api.settings import get_settings

__all__ = [
    "INTERRUPT_GRACE_SECONDS",
    "RETRY_DELAY_SECONDS",
    "SWEEP_INTERVAL_SECONDS",
    "SessionPump",
    "TurnHandle",
    "WakeDispatcher",
    "WorkerSlots",
    "run_agent",
]

logger = logging.getLogger(__name__)

#: 置位 cancel_event 后等这一轮自己收尾的宽限期；与 v2 runtime 同值。
INTERRUPT_GRACE_SECONDS = 5.0
#: 一轮没有真正启动（``run_turn`` 返回 False）时，隔多久再试。
RETRY_DELAY_SECONDS = 5.0
#: 两个服务兜底扫描的间隔。
SWEEP_INTERVAL_SECONDS = 30.0

#: 容量没有变化通知，等待者按这个间隔重新读一次：设置页调大上限后，排队中的
#: Worker 不必等下一次 ``release`` 才起来。
_CAPACITY_POLL_SECONDS = 0.05


@dataclass(slots=True)
class TurnHandle:
    """交给 ``run_turn`` 的这一轮句柄：泵与运行之间只有取消两件事要说。"""

    cancel_event: asyncio.Event = field(default_factory=asyncio.Event)
    cancel_reason: Literal["interrupted", "cancelled"] = "interrupted"
    run_id: str | None = None


@dataclass(slots=True)
class _Waiter:
    priority: int
    sequence: int
    future: asyncio.Future[None]
    granted: bool = False


class WorkerSlots:
    """Worker 的并发闸：容量现读，等待者按 ``(priority, 入队序号)`` 出队。

    ``capacity`` 是 callable 而不是常量：``worker.max_concurrent`` 是热生效设置。
    ``acquire`` 被取消时把自己从等待堆里移除，不会留下一个永远等不到的闸位。
    """

    def __init__(self, capacity: Callable[[], int]) -> None:
        if not callable(capacity):
            raise TypeError("capacity 必须可调用")
        self._capacity = capacity
        self._in_use = 0
        self._waiters: list[_Waiter] = []
        self._sequence = 0

    @property
    def in_use(self) -> int:
        return self._in_use

    @property
    def waiting(self) -> int:
        return len(self._waiters)

    async def acquire(self, priority: int) -> None:
        loop = asyncio.get_running_loop()
        self._sequence += 1
        waiter = _Waiter(
            priority=int(priority),
            sequence=self._sequence,
            future=loop.create_future(),
        )
        self._waiters.append(waiter)
        # 入队序号在同一个事件循环里单调递增；排序让新来的高优先级也能排在前面。
        self._waiters.sort(key=lambda item: (item.priority, item.sequence))
        self._grant()
        try:
            while not waiter.future.done():
                try:
                    await asyncio.wait_for(
                        asyncio.shield(waiter.future),
                        timeout=_CAPACITY_POLL_SECONDS,
                    )
                except TimeoutError:
                    # 容量可能刚被调大，重新试一次。
                    self._grant()
            waiter.future.result()
        except asyncio.CancelledError:
            if waiter.granted:
                # 已经拿到闸位但还没来得及返回：把闸位还给下一个等待者。
                waiter.granted = False
                self._in_use = max(0, self._in_use - 1)
                self._grant()
            else:
                try:
                    self._waiters.remove(waiter)
                except ValueError:
                    pass
                self._grant()
            raise

    def release(self) -> None:
        if self._in_use <= 0:
            logger.warning("WorkerSlots.release 没有对应的 acquire，已忽略")
            return
        self._in_use -= 1
        self._grant()

    def _grant(self) -> None:
        capacity = max(0, int(self._capacity()))
        while self._waiters and self._in_use < capacity:
            waiter = self._waiters.pop(0)
            waiter.granted = True
            self._in_use += 1
            if not waiter.future.done():
                waiter.future.set_result(None)


class SessionPump:
    """一个会话的「收件箱 → 运行」驱动（规格 §4.5.1）。"""

    def __init__(
        self,
        session_id: str,
        *,
        run_turn: Callable[[TurnHandle], Awaitable[bool]],
        has_work: Callable[[], Awaitable[bool]],
        window_ms: Callable[[], int],
        slots: WorkerSlots | None = None,
        priority: Callable[[], int] = lambda: 1,
    ) -> None:
        self.session_id = session_id
        self._run_turn = run_turn
        self._has_work = has_work
        self._window_ms = window_ms
        self._slots = slots
        self._priority = priority
        #: 有新工作待处理；窗口内的多次 wake 只把它置真。
        self._dirty = False
        self._closed = False
        self._loop_task: asyncio.Task[None] | None = None
        self._turn_task: asyncio.Task[bool] | None = None
        self._handle: TurnHandle | None = None

    @property
    def handle(self) -> TurnHandle | None:
        return self._handle

    @property
    def busy(self) -> bool:
        """此刻是否真有一轮在跑；只有循环在等窗口 / 闸时不算忙。"""
        return self._turn_task is not None

    def wake(self) -> None:
        if self._closed:
            return
        self._dirty = True
        if self._loop_task is None or self._loop_task.done():
            try:
                self._loop_task = asyncio.create_task(
                    self._loop(), name=f"pump-{self.session_id}"
                )
            except RuntimeError:
                # 没有事件循环时只记住 dirty；下一次 wake 会补起循环。
                logger.warning("会话 %s 的泵没有事件循环可起", self.session_id)
                return
            self._loop_task.add_done_callback(self._log_loop_failure)

    def _log_loop_failure(self, task: asyncio.Task[None]) -> None:
        # 取出异常，避免「Task exception was never retrieved」；循环本身被打断不算失败。
        if task.cancelled():
            return
        error = task.exception()
        if error is not None:
            logger.error(
                "会话 %s 的泵循环异常退出", self.session_id, exc_info=error
            )

    async def _loop(self) -> None:
        while self._dirty and not self._closed:
            self._dirty = False
            window = max(0, int(self._window_ms()))
            if window:
                await asyncio.sleep(window / 1000)
            if not await self._has_work():
                continue
            acquired = False
            if self._slots is not None:
                await self._slots.acquire(self._priority())
                acquired = True
            self._handle = TurnHandle()
            self._turn_task = asyncio.create_task(
                self._run_turn(self._handle), name=f"turn-{self.session_id}"
            )
            started = False
            try:
                started = await self._turn_task
            except asyncio.CancelledError:
                if asyncio.current_task().cancelling():
                    # 循环自己（close）被取消：不再跑下一轮。
                    raise
                # 否则是 interrupt 在宽限期后取消了这一轮，继续循环。
                continue
            except Exception:  # noqa: BLE001 - 一轮失败不能让泵整个死掉
                logger.exception("会话 %s 的一轮运行异常", self.session_id)
            finally:
                self._handle = None
                self._turn_task = None
                if acquired:
                    self._slots.release()
            if not started:
                await asyncio.sleep(RETRY_DELAY_SECONDS)
            if await self._has_work():
                self._dirty = True

    async def interrupt(
        self,
        reason: Literal["interrupted", "cancelled"],
        *,
        abort: Callable[[], Awaitable[None]],
    ) -> bool:
        """按 v2 的顺序中断这一轮：置原因 → 撤挂起 → 宽限 → 取消 → 再撤一次。"""
        handle = self._handle
        if handle is None:
            await self._abort(abort)
            return False
        handle.cancel_reason = reason
        handle.cancel_event.set()
        await self._abort(abort)
        task = self._turn_task
        if task is not None:
            await asyncio.wait({task}, timeout=INTERRUPT_GRACE_SECONDS)
            if not task.done():
                task.cancel()
                await asyncio.wait({task}, timeout=INTERRUPT_GRACE_SECONDS)
        # 宽限期内新派发的确认 / 提问也撤掉。
        await self._abort(abort)
        return True

    async def close(self) -> None:
        """停掉循环与在跑的一轮；可重复调用。"""
        self._closed = True
        self._dirty = False
        tasks = [
            task
            for task in (self._turn_task, self._loop_task)
            if task is not None and not task.done()
        ]
        for task in tasks:
            task.cancel()
        if tasks:
            await asyncio.gather(*tasks, return_exceptions=True)
        self._handle = None
        self._turn_task = None
        self._loop_task = None

    async def _abort(self, abort: Callable[[], Awaitable[None]]) -> None:
        try:
            await abort()
        except Exception:  # noqa: BLE001 - 撤回失败不能挡住中断本身
            logger.exception("会话 %s 撤回挂起的确认 / 提问失败", self.session_id)


class WakeDispatcher:
    """收件箱监听器：查会话种类，转给总控或 WorkerService（规格 §4.5.1）。

    ``InboxService`` 的监听器是同步回调，这里起一个任务去查库；查到的种类按会话
    缓存，同一会话后续的唤醒不再碰数据库。不是总控 / Worker 的会话（v2 聊天、
    子代理）直接忽略。
    """

    def __init__(self, session_factory, *, orchestrator, workers) -> None:
        self._session_factory = session_factory
        self._orchestrator = orchestrator
        self._workers = workers
        self._kinds: dict[str, str] = {}
        self._tasks: set[asyncio.Task[None]] = set()

    def __call__(self, session_id: str) -> None:
        try:
            task = asyncio.create_task(
                self._dispatch(session_id), name=f"wake-{session_id}"
            )
        except RuntimeError:
            logger.warning("收件箱监听器没有事件循环可起（session=%s）", session_id)
            return
        self._tasks.add(task)
        task.add_done_callback(self._tasks.discard)

    async def _dispatch(self, session_id: str) -> None:
        try:
            kind = self._kinds.get(session_id)
            if kind is None:
                async with self._session_factory() as session:
                    stored = await AgentSessionRepository(session).get(session_id)
                if stored is None:
                    return
                kind = str(stored.kind)
                self._kinds[session_id] = kind
            if kind == "orchestrator":
                self._orchestrator.wake()
            elif kind == "worker":
                self._workers.wake(session_id)
        except Exception:  # noqa: BLE001 - 后台任务不能把异常抛成未处理任务
            logger.exception("唤醒分派失败（session=%s）", session_id)


async def run_agent(
    app,
    spec,
    session,
    run,
    *,
    inbox_items,
    cancel_event,
    cancel_reason: Callable[[], str],
    state: dict[str, Any],
    parent: RunContext | None = None,
) -> RunOutcome:
    """跑一轮 v3 运行：建 ``RunContext``、装配服务链、调 ``AgentLoop.run``。

    子代理传 ``parent``：``root_run_id`` / ``parent_run_id`` 接在父运行上，事件也走
    父运行的出口，SSE 的流才能把子代理事件串起来。
    """
    state = state if state is not None else {}
    app_state = getattr(app, "state", None)
    runtime = getattr(app_state, "agent_runtime", None)
    if runtime is None:
        raise AppError(ErrorCode.INTERNAL_ERROR, "Agent 运行时尚未装配")
    session_factory = getattr(runtime, "session_factory", None)
    registry = getattr(
        getattr(runtime, "confirmation_service", None), "registry", None
    )
    router = getattr(app_state, "model_router", None)
    if session_factory is None or registry is None or router is None:
        raise AppError(ErrorCode.INTERNAL_ERROR, "Agent 运行时的依赖尚未装配")

    ticket_id = getattr(run, "ticket_id", None) or getattr(session, "ticket_id", None)
    if parent is not None:
        root_run_id = parent.root_run_id
        parent_run_id = parent.run_id
        ticket_id = parent.ticket_id
        emit = parent.emit
    else:
        root_run_id = getattr(run, "root_run_id", None) or run.id
        parent_run_id = getattr(run, "parent_run_id", None)
        # 一律用带 run_id 的 per-run 总线：`ticket_created` 等事件的 data 里没有
        # run_id，若直接复用装配层 `run_id=None` 的 hub，SSE 与 WS 就无法归属。
        # 事件由 per-run 总线原样转发给 hub（M70 §4.5.8）。
        hub = getattr(app_state, "run_event_bus", None)
        bus = RunEventBus(run_id=run.id, hub=hub)
        emit = bus.emit

    ctx = RunContext(
        run_id=run.id,
        root_run_id=root_run_id,
        parent_run_id=parent_run_id,
        session_id=getattr(run, "session_id", None) or session.id,
        ticket_id=ticket_id,
        spec=spec,
        app=app,
        request=SimpleNamespace(app=app),
        emit=emit,
        cancel_event=cancel_event,
        cancel_reason=cancel_reason,
        run=run,
        inbox_items=list(inbox_items),
    )
    ctx.state.update(
        {key: value for key, value in state.items() if key != "harness.ctx"}
    )
    # 收尾（WorkerService 的 ensure_report）要从这里拿本轮上下文；先放进去，
    # 即便运行中途抛异常也拿得到。
    state["harness.ctx"] = ctx

    profile = await router.resolve_for_spec(
        spec,
        session_model=getattr(session, "model", None),
        session_base_url=getattr(session, "base_url", None),
    )
    chain = runtime.build_chain(
        spec.kind,
        device_lease_service=getattr(app_state, "device_lease_service", None),
        cost_ledger=getattr(app_state, "cost_ledger", None),
        settings_provider=get_settings,
    )
    handle = router.client_for(profile)

    async def call_model(
        run_ctx: RunContext, request: ModelRequest
    ) -> ModelResponse:
        return await collect_completion(
            handle.client,
            profile.model,
            request.messages,
            request.tools,
            emit=run_ctx.emit,
            draft=run_ctx.draft,
            run_id=run_ctx.run_id,
        )

    def tool_schemas(run_ctx: RunContext) -> list[dict[str, Any]]:
        # 每步现算：本轮里用 enable_tool_group 打开的扩展组下一步生效。
        return runtime._tool_schemas(
            registry,
            vision_enabled=run_ctx.vision_enabled,
            allowed_tools=_allowed_tools(runtime, registry, spec, session.id),
        )

    loop = AgentLoop(
        chain=chain,
        transcript=_SessionTranscript(session_factory),
        call_model=call_model,
        tool_schemas=tool_schemas,
        registry=registry,
        cost_ledger=getattr(app_state, "cost_ledger", None),
    )
    run_service = getattr(app_state, "run_service", None)
    # 子代理运行由 DelegationService 在 finalize_output（可能含结构化重试）之后写
    # 终态；总控与 Worker 的运行没有别的所有者，由这里统一落库。
    owns_run = parent is None
    try:
        outcome = await loop.run(
            ctx,
            run=run,
            inbox_items=list(inbox_items),
            cancel_event=cancel_event,
        )
    except asyncio.CancelledError:
        # 中断 / 取消时 AgentLoop 已发 run_end 后原样抛出；运行终态由调用方写
        # （05 §2.3、M70 §4.5.7 的「运行终态由调用方按 cancelled 写」）。
        if owns_run and cancel_event is not None and cancel_event.is_set():
            reason = cancel_reason()
            await _finish_run(
                run_service,
                run.id,
                status=(
                    AgentRunStatus.CANCELLED
                    if reason == "cancelled"
                    else AgentRunStatus.INTERRUPTED
                ),
                finish_reason=reason,
            )
        raise
    else:
        # 成功路径此前没有任何调用方写终态，导致每条 v3 运行都停在 running，
        # 服务的 finally 只能按 failed 收尾。总控与 Worker 由统一入口在这里落库。
        if owns_run:
            await _finish_run(
                run_service,
                run.id,
                status=_outcome_status(outcome.status),
                finish_reason=outcome.finish_reason,
            )
        return outcome
    finally:
        await handle.aclose()


def _outcome_status(status: str) -> AgentRunStatus:
    """``RunOutcome.status`` 的字符串到运行行状态；未知值按失败处理。"""
    try:
        return AgentRunStatus(status)
    except ValueError:
        return AgentRunStatus.FAILED


async def _finish_run(
    run_service: Any,
    run_id: str,
    *,
    status: AgentRunStatus,
    finish_reason: str,
) -> None:
    """写运行终态；没装配运行服务（单元测试的轻量 app）时什么都不做。"""
    if run_service is None:
        return
    try:
        await run_service.finish(
            run_id, status=status, finish_reason=finish_reason
        )
    except Exception:  # noqa: BLE001 - 终态是观测信息，写失败不盖过本轮结果
        logger.warning("Agent 运行终态写入失败：run=%s", run_id, exc_info=True)


def _allowed_tools(runtime, registry, spec, session_id: str) -> set[str]:
    """这一轮模型能看到的工具：定义的清单，减去未开启的扩展组。"""
    from maa_api.agent.tools.catalog import DEFERRED_TOOL_GROUPS

    enabled = runtime.enabled_tool_groups(session_id)
    definitions = {definition.name: definition for definition in registry.list()}
    allowed: set[str] = set()
    for name in spec.tools:
        definition = definitions.get(name)
        if definition is None or not definition.model_visible:
            continue
        if (
            definition.group in DEFERRED_TOOL_GROUPS
            and definition.group not in enabled
        ):
            continue
        allowed.add(name)
    return allowed


class _SessionTranscript:
    """v3 运行的消息存取：直接读写 ``agent_message``，每条都带本轮的 ``run_id``。

    v2 门面的 ``_LegacyTranscript`` 走 ``AgentRuntime._persist_message``，那里用
    ``_current_runs`` 找运行 id；v3 的运行 id 由调用方建行时给出，所以这里自带一份
    落库逻辑，不碰 v2 的进程内登记表。
    """

    def __init__(self, session_factory) -> None:
        self._session_factory = session_factory

    async def load(self, ctx: RunContext) -> list[dict[str, Any]]:
        async with self._session_factory() as session:
            history = await AgentMessageRepository(session).list_by_session(
                ctx.session_id
            )
        return [_message_dict(message) for message in history]

    async def append(self, ctx: RunContext, *, role: str, **fields: Any) -> Any:
        now = utcnow()
        async with self._session_factory() as session:
            sessions = AgentSessionRepository(session)
            stored_session = await sessions.get(ctx.session_id)
            if stored_session is None:
                raise AppError(
                    ErrorCode.AGENT_SESSION_NOT_FOUND, "Agent 会话不存在"
                )
            messages = AgentMessageRepository(session)
            seq = await messages.next_seq(ctx.session_id)
            stored = await messages.create(
                AgentMessage(
                    session_id=ctx.session_id,
                    seq=seq,
                    role=role,
                    content=fields.get("content"),
                    reasoning_content=fields.get("reasoning_content"),
                    tool_calls=fields.get("tool_calls"),
                    tool_call_id=fields.get("tool_call_id"),
                    tool_name=fields.get("tool_name"),
                    audit_id=fields.get("audit_id"),
                    prompt_tokens=fields.get("prompt_tokens"),
                    completion_tokens=fields.get("completion_tokens"),
                    finish_reason=fields.get("finish_reason"),
                    latency_ms=fields.get("latency_ms"),
                    run_id=None if role == "user" else ctx.run_id,
                    created_at=now,
                )
            )
            stored_session.message_count += 1
            stored_session.updated_at = now
            stored_session.last_message_at = now
            if fields.get("prompt_tokens") is not None:
                stored_session.prompt_tokens += int(fields["prompt_tokens"])
            if fields.get("completion_tokens") is not None:
                stored_session.completion_tokens += int(fields["completion_tokens"])
            await session.commit()
            return stored

    async def persisted_tool_call_ids(self, ctx: RunContext) -> set[str]:
        try:
            async with self._session_factory() as session:
                history = await AgentMessageRepository(session).list_by_session(
                    ctx.session_id
                )
        except Exception:  # noqa: BLE001 - 读不到就按「都没写进去」补桩
            logger.warning(
                "中断收尾：读取历史失败 session=%s", ctx.session_id, exc_info=True
            )
            return set()
        return {
            str(message.tool_call_id)
            for message in history
            if message.role == "tool" and message.tool_call_id
        }
