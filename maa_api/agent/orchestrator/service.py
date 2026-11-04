"""OrchestratorService：常驻总控会话的唯一性、一轮运行与重置（M70 规格 §4.5.2）。

总控没有索引保证「只有一个活动会话」：唯一性靠本服务的进程内
:class:`asyncio.Lock` 与启动时的修复归档。每次 :meth:`ensure_session` 都在锁内
先复查内存里的会话，再去库里找 ``kind='orchestrator' AND status='active' AND
archived_at IS NULL`` 的会话，多于一条时保留 ``created_at`` 最新的一条，其余写
``archived_at`` 并置 ``finished``。

一轮总控运行与 Worker 相同：由 :class:`~maa_api.agent.workers.pump.SessionPump`
在批处理窗口结束后调 :meth:`_run_turn`，领走收件箱、落消息，再交给
:func:`~maa_api.agent.workers.pump.run_agent` 跑 ``orchestrator`` 定义。区别是不带
并发闸、收尾不补汇报，且角色卡是 Worker 清单。

外部依赖全部由构造参数注入，测试只给临时库与替身；``app.state`` 上只读
``setting_service``（``ensure_ready`` 的加密错误判定）。
"""

from __future__ import annotations

import asyncio
import logging
from collections.abc import Mapping
from dataclasses import dataclass
from typing import Any

from sqlalchemy import func, select, update

from maa_api.agent.harness.loop import MESSAGE_PERSISTED_EVENT
from maa_api.agent.tickets.inbox import InboxService
from maa_api.agent.workers.cards import persist_inbox_messages, worker_catalog_card
from maa_api.agent.workers.pump import (
    SWEEP_INTERVAL_SECONDS,
    SessionPump,
    TurnHandle,
    run_agent,
)
from maa_api.agent.workers.service import PostedMessage
from maa_api.db.models import AgentInbox, AgentSession, utcnow
from maa_api.db.repositories.agent import AgentSessionRepository
from maa_api.domain.enums import (
    AgentRunKind,
    AgentRunStatus,
    AgentSessionKind,
    AgentSessionStatus,
    InboxKind,
    InboxStatus,
    ModelRole,
)
from maa_api.domain.errors import AppError, ErrorCode
from maa_api.settings import get_settings

__all__ = ["OrchestratorService", "OrchestratorState"]

logger = logging.getLogger(__name__)

#: 总控会话的固定标题与话题键（03 §15）。
_SESSION_TITLE = "总控"
_THREAD_KEY = "chat:orchestrator"
#: 一轮最多领取的收件箱项数（与 §4.5.5 的批大小一致）。
_CLAIM_LIMIT = 50
#: 活动运行的两种状态：收尾时才需要 ``finish``。
_ACTIVE_RUN_STATUSES = frozenset(
    {AgentRunStatus.RUNNING.value, AgentRunStatus.WAITING_HUMAN.value}
)


@dataclass(frozen=True, slots=True)
class OrchestratorState:
    """总控的对外状态（§4.2.2 的 ``OrchestratorStateModel``）。"""

    session_id: str
    status: str
    turn_running: bool
    pending_inbox: int
    archived_count: int


class OrchestratorService:
    """总控会话与总控运行的唯一入口（规格 §4.5.2）。"""

    def __init__(
        self,
        *,
        app: Any,
        session_factory: Any,
        spec_registry: Any,
        run_service: Any,
        inbox_service: InboxService,
        ticket_service: Any,
        question_service: Any,
        confirmation_service: Any,
        model_router: Any,
        event_bus: Any,
    ) -> None:
        self._app = app
        self._session_factory = session_factory
        self._spec_registry = spec_registry
        self._run_service = run_service
        self._inbox = inbox_service
        #: 总控暂时不用工单服务，保留注入以便 T13 的装配顺序与 §3.3 表一致。
        self._ticket_service = ticket_service
        self._question_service = question_service
        self._confirmation_service = confirmation_service
        self._model_router = model_router
        self._event_bus = event_bus
        self._session_id: str | None = None
        #: 进程内唯一的会话锁：并发 ``ensure_session`` 只会建一个会话。
        self._session_lock = asyncio.Lock()
        self._pumps: dict[str, SessionPump] = {}
        self._tasks: set[asyncio.Task[Any]] = set()
        self._sweep_task: asyncio.Task[None] | None = None
        self._started = False
        self._closed = False

    # ------------------------------------------------------------------
    # 装配与生命周期
    # ------------------------------------------------------------------
    async def start(self) -> None:
        """修复多余活动会话、扫描待处理项，并起兜底扫描。"""
        if self._started:
            return
        self._started = True
        await self.ensure_session()
        await self._sweep()
        self._sweep_task = asyncio.create_task(
            self._sweep_loop(), name="orchestrator-service-sweep"
        )

    async def close(self) -> None:
        """停扫描、关全部泵；可重复调用。"""
        self._closed = True
        task = self._sweep_task
        self._sweep_task = None
        if task is not None and not task.done():
            task.cancel()
            await asyncio.gather(task, return_exceptions=True)
        pending = [item for item in self._tasks if not item.done()]
        for item in pending:
            item.cancel()
        if pending:
            await asyncio.gather(*pending, return_exceptions=True)
        self._tasks.clear()
        pumps, self._pumps = list(self._pumps.values()), {}
        for pump in pumps:
            await pump.close()

    async def _sweep_loop(self) -> None:
        while True:
            await asyncio.sleep(SWEEP_INTERVAL_SECONDS)
            try:
                await self._sweep()
            except Exception:  # noqa: BLE001 - 兜底扫描失败不能停掉循环
                logger.exception("OrchestratorService 兜底扫描失败")

    async def _sweep(self) -> None:
        """有非 passive 待处理项且泵不忙时唤醒一次（§4.5.1）。"""
        session = await self.ensure_session()
        if self.turn_running():
            return
        if await self._inbox.has_active_pending(session.id):
            self.wake()

    # ------------------------------------------------------------------
    # 单一活动会话
    # ------------------------------------------------------------------
    async def ensure_session(self) -> AgentSession:
        """返回唯一的活动总控会话；多余的归档，没有就新建（§4.5.2）。"""
        async with self._session_lock:
            if self._session_id is not None:
                current = await self._get_session(self._session_id)
                if _is_live_orchestrator(current):
                    return current
            async with self._session_factory() as session:
                rows = await _active_orchestrator_sessions(session)
                if rows:
                    keep, extras = rows[0], rows[1:]
                    if extras:
                        now = utcnow()
                        for extra in extras:
                            extra.archived_at = now
                            extra.status = AgentSessionStatus.FINISHED
                            extra.updated_at = now
                        await session.commit()
                        logger.warning(
                            "归档了 %d 个多余的活动总控会话（保留 %s）",
                            len(extras),
                            keep.id,
                        )
                    self._session_id = str(keep.id)
                    return keep
            model, base_url = await self._profile_snapshot()
            now = utcnow()
            async with self._session_factory() as session:
                stored = await AgentSessionRepository(session).create(
                    AgentSession(
                        title=_SESSION_TITLE,
                        model=model,
                        base_url=base_url,
                        kind=AgentSessionKind.ORCHESTRATOR,
                        status=AgentSessionStatus.ACTIVE,
                        created_at=now,
                        updated_at=now,
                    )
                )
                await session.commit()
            self._session_id = str(stored.id)
            return stored

    async def current_session_id(self) -> str:
        """当前活动总控会话 id；没有就建一个。"""
        session = await self.ensure_session()
        return str(session.id)

    async def state(self) -> OrchestratorState:
        """会话 id、状态、是否在跑、待处理项数与归档数（§4.2.2）。"""
        session = await self.ensure_session()
        pending = await self._inbox.count_pending(session.id)
        async with self._session_factory() as db:
            archived = await db.scalar(
                select(func.count())
                .select_from(AgentSession)
                .where(
                    AgentSession.kind == AgentSessionKind.ORCHESTRATOR,
                    AgentSession.archived_at.is_not(None),
                )
            )
        return OrchestratorState(
            session_id=str(session.id),
            status=str(session.status),
            turn_running=self.turn_running(),
            pending_inbox=int(pending),
            archived_count=int(archived or 0),
        )

    # ------------------------------------------------------------------
    # 用户消息与通知
    # ------------------------------------------------------------------
    async def ensure_ready(self) -> None:
        """总控角色必须配好 base_url / api_key / model（§4.5.2 的错误文案）。"""
        try:
            profile = await self._model_router.resolve(ModelRole.ORCHESTRATOR)
        except AppError:
            if self._llm_encryption_error() is not None:
                raise AppError(
                    ErrorCode.LLM_ENCRYPTION_KEY_INVALID,
                    "已保存的 LLM API Key 无法解密，请到设置页重新填写 API Key",
                ) from None
            raise
        if (
            not getattr(profile, "base_url", None)
            or not getattr(profile, "api_key", None)
            or not getattr(profile, "model", None)
        ):
            if self._llm_encryption_error() is not None:
                raise AppError(
                    ErrorCode.LLM_ENCRYPTION_KEY_INVALID,
                    "已保存的 LLM API Key 无法解密，请到设置页重新填写 API Key",
                )
            raise AppError(
                ErrorCode.LLM_NOT_CONFIGURED,
                "还没有配置 LLM，请先在设置页填写 base_url、api_key 与 model",
            )

    async def post_user_message(
        self,
        content: str,
        mentions: list[dict],
        *,
        source: str = "web",
        sender: str = "web",
    ) -> PostedMessage:
        """用户对总控说话：落消息与收件箱项，再给挂起的提问让位（§4.5.2）。"""
        await self.ensure_ready()
        session = await self.ensure_session()
        item, message = await self._inbox.put_user_message(
            str(session.id),
            content=content,
            thread_key=_THREAD_KEY,
            payload={
                "mentions": list(mentions or []),
                "source": source,
                "sender": sender,
            },
        )
        seq = int(item.ref_id) if item.ref_id is not None else int(message.seq)
        await self._emit_message_persisted(
            session_id=str(session.id),
            message_id=int(message.id or 0),
            seq=seq,
            content=content,
        )
        await self._yield_pending_question(str(session.id))
        return PostedMessage(
            session_id=str(session.id),
            inbox_id=int(item.id or 0),
            message_id=int(message.id or 0),
            seq=seq,
        )

    async def notify(
        self,
        text: str,
        *,
        passive: bool,
        ref_id: str | None = None,
        ticket_id: str | None = None,
    ) -> int:
        """给当前总控会话写一条 ``system`` 项（passive 时不自起一轮）。"""
        session = await self.ensure_session()
        item = await self._inbox.put(
            str(session.id),
            InboxKind.SYSTEM,
            ref_id=ref_id,
            payload={
                "text": text,
                "passive": bool(passive),
                "origin": "service",
                "ticket_id": ticket_id,
            },
        )
        return int(item.id or 0)

    # ------------------------------------------------------------------
    # 一轮总控运行
    # ------------------------------------------------------------------
    async def _run_turn(self, handle: TurnHandle) -> bool:
        """跑一轮总控（§4.5.2）：返回是否真正启动了一轮。"""
        session = await self.ensure_session()
        spec = self._spec_registry.get("orchestrator")
        try:
            run = await self._run_service.start(
                session_id=str(session.id),
                agent_name="orchestrator",
                kind=AgentRunKind.ORCHESTRATOR_TURN,
                spec_version=getattr(spec, "spec_version", None),
            )
        except AppError as exc:
            if exc.code == ErrorCode.RUN_ALREADY_ACTIVE:
                logger.warning("总控会话 %s 已有运行，稍后再试", session.id)
                return False
            raise
        handle.run_id = run.id

        items = await self._inbox.claim(
            str(session.id), run_id=run.id, limit=_CLAIM_LIMIT
        )
        if not items:
            await self._run_service.finish(
                run.id, status=AgentRunStatus.SUCCEEDED, finish_reason="stop"
            )
            return True
        await persist_inbox_messages(
            self._session_factory,
            self._event_bus,
            str(session.id),
            run.id,
            items,
        )
        try:
            await run_agent(
                self._app,
                spec,
                session,
                run,
                inbox_items=items,
                cancel_event=handle.cancel_event,
                cancel_reason=lambda: handle.cancel_reason,
                state={"role_card": worker_catalog_card(self._spec_registry)},
            )
        finally:
            # 总控不补汇报，但同样不能留下一条永远 running 的运行行。
            stored = await self._run_service.get(run.id)
            if stored is not None and str(stored.status) in _ACTIVE_RUN_STATUSES:
                await self._run_service.finish(
                    run.id, status=AgentRunStatus.FAILED, finish_reason="error"
                )
        return True

    # ------------------------------------------------------------------
    # 唤醒、中断与重置
    # ------------------------------------------------------------------
    def wake(self) -> None:
        """唤醒当前总控会话的泵；还没有会话时先建再唤醒。"""
        if self._closed:
            return
        if self._session_id is None:
            self._spawn(self._ensure_and_wake(), name="orchestrator-ensure-wake")
            return
        self._pump_for(self._session_id).wake()

    def turn_running(self) -> bool:
        """当前总控会话此刻是否真有一轮在跑。"""
        if self._session_id is None:
            return False
        pump = self._pumps.get(self._session_id)
        return pump is not None and pump.busy

    async def interrupt(self) -> dict[str, Any]:
        """中断在跑的一轮：撤挂起确认与提问，返回 ``{session_id, interrupted, turn_running}``。"""
        session = await self.ensure_session()
        pump = self._pumps.get(str(session.id))
        if pump is None:
            return {
                "session_id": str(session.id),
                "interrupted": False,
                "turn_running": False,
            }
        interrupted = await pump.interrupt(
            "interrupted", abort=lambda: self._abort_calls(str(session.id))
        )
        return {
            "session_id": str(session.id),
            "interrupted": interrupted,
            "turn_running": pump.busy,
        }

    async def reset(self) -> OrchestratorState:
        """换一个新总控会话，把未处理项搬过去（§4.5.2）。"""
        await self.ensure_session()
        async with self._session_lock:
            assert self._session_id is not None
            old_id = self._session_id
            if self.turn_running() or await self._has_active_run(old_id):
                raise AppError(
                    ErrorCode.RUN_ALREADY_ACTIVE,
                    "总控还在处理上一条消息，等它结束后再重新开始",
                )
            if await self._has_pending_user_message(old_id):
                raise AppError(
                    ErrorCode.RUN_ALREADY_ACTIVE,
                    "总控还有没处理完的消息，稍后再重新开始",
                )
            model, base_url = await self._profile_snapshot()
            now = utcnow()
            async with self._session_factory() as db:
                new = await AgentSessionRepository(db).create(
                    AgentSession(
                        title=_SESSION_TITLE,
                        model=model,
                        base_url=base_url,
                        kind=AgentSessionKind.ORCHESTRATOR,
                        status=AgentSessionStatus.ACTIVE,
                        created_at=now,
                        updated_at=now,
                    )
                )
                await db.execute(
                    update(AgentInbox)
                    .where(
                        AgentInbox.session_id == old_id,
                        AgentInbox.status == InboxStatus.PENDING,
                    )
                    .values(session_id=new.id)
                )
                archived = await AgentSessionRepository(db).get(old_id)
                if archived is not None:
                    archived.archived_at = now
                    archived.status = AgentSessionStatus.FINISHED
                    archived.updated_at = now
                await db.commit()
                new_id = str(new.id)
            self._session_id = new_id
            await self._close_pump(old_id)
        # 搬来的项里有非 passive 的（或重置后又有新消息）就唤醒新会话。
        if self._session_id is not None and await self._inbox.has_active_pending(
            self._session_id
        ):
            self.wake()
        return await self.state()

    # ------------------------------------------------------------------
    # 内部工具
    # ------------------------------------------------------------------
    def _state(self) -> Any:
        return getattr(self._app, "state", None)

    def _llm_encryption_error(self) -> Any:
        setting_service = getattr(self._state(), "setting_service", None)
        return getattr(setting_service, "llm_encryption_error", None)

    async def _profile_snapshot(self) -> tuple[str, str | None]:
        """会话行快照：总控角色档案；解析不了时按未配置建。"""
        try:
            profile = await self._model_router.resolve(ModelRole.ORCHESTRATOR)
        except Exception:  # noqa: BLE001 - 没配好也要能建会话，GET 端点照常可用
            logger.warning("总控角色档案解析失败，会话先按未配置建", exc_info=True)
            return "unconfigured", None
        model = str(getattr(profile, "model", "") or "").strip() or "unconfigured"
        base_url = str(getattr(profile, "base_url", "") or "").strip() or None
        return model, base_url

    async def _get_session(self, session_id: str) -> AgentSession | None:
        async with self._session_factory() as session:
            return await AgentSessionRepository(session).get(session_id)

    async def _has_active_run(self, session_id: str) -> bool:
        return await self._run_service.active_run(session_id) is not None

    async def _has_pending_user_message(self, session_id: str) -> bool:
        async with self._session_factory() as db:
            count = await db.scalar(
                select(func.count())
                .select_from(AgentInbox)
                .where(
                    AgentInbox.session_id == session_id,
                    AgentInbox.status == InboxStatus.PENDING,
                    AgentInbox.kind == InboxKind.USER_MESSAGE,
                )
            )
        return bool(count)

    async def _emit_message_persisted(
        self, *, session_id: str, message_id: int, seq: int, content: str
    ) -> None:
        """用户消息落库后补发总线内部事件（WS 的 message_created 靠它）。"""
        if self._event_bus is None:
            return
        await self._event_bus.emit(
            MESSAGE_PERSISTED_EVENT,
            {
                "session_id": session_id,
                "ticket_id": None,
                "agent": "orchestrator",
                "kind": None,
                "message": {
                    "id": message_id,
                    "seq": seq,
                    "role": "user",
                    "inbox_id": None,
                    "preview": str(content)[:200],
                },
            },
        )

    async def _yield_pending_question(self, session_id: str) -> None:
        """新消息到达时让位：pump 忙就跳过该会话已有的 pending 提问（§4.5.2）。"""
        pump = self._pumps.get(session_id)
        if pump is None or not pump.busy:
            return
        service = self._question_service
        if service is None:
            return
        try:
            page = await service.list(
                session_id=session_id, status="pending", page=1, size=50
            )
        except Exception:  # noqa: BLE001 - 让位失败不影响新消息
            logger.exception("列举会话 %s 的挂起提问失败", session_id)
            return
        items = page.get("items") if isinstance(page, Mapping) else None
        for item in items or ():
            question_id = item.get("question_id") if isinstance(item, Mapping) else None
            if not question_id:
                continue
            try:
                await service.skip(str(question_id), answered_by="superseded")
            except AppError:
                pass
            except Exception:  # noqa: BLE001
                logger.exception("跳过提问 %s 失败", question_id)

    async def _abort_calls(self, session_id: str) -> None:
        """撤回该会话挂起的确认与提问；异常只记日志（§4.5.1）。"""
        confirmation = self._confirmation_service
        question = self._question_service
        if confirmation is not None:
            try:
                await confirmation.abort_session_calls(session_id)
            except Exception:  # noqa: BLE001 - 撤回失败只记日志
                logger.exception("撤回会话 %s 的挂起确认失败", session_id)
        if question is not None:
            try:
                await question.cancel_pending_for_session(session_id)
            except Exception:  # noqa: BLE001
                logger.exception("撤回会话 %s 的挂起提问失败", session_id)

    def _pump_for(self, session_id: str) -> SessionPump:
        pump = self._pumps.get(session_id)
        if pump is None:
            pump = SessionPump(
                session_id,
                run_turn=lambda handle: self._run_turn(handle),
                has_work=lambda: self._inbox.has_active_pending(session_id),
                window_ms=lambda: get_settings().orchestrator.batch_window_ms,
            )
            self._pumps[session_id] = pump
        return pump

    async def _close_pump(self, session_id: str) -> None:
        pump = self._pumps.pop(session_id, None)
        if pump is None:
            return
        current = asyncio.current_task()
        if getattr(pump, "_turn_task", None) is current:
            # 在泵自己的一轮里重置（API 层不会，但保持与 WorkerService 同口径）：
            # 取消当前任务会把自己也取消掉，移除引用即可。
            return
        await pump.close()

    async def _ensure_and_wake(self) -> None:
        try:
            session = await self.ensure_session()
        except Exception:  # noqa: BLE001 - 后台任务不能把异常抛成未处理任务
            logger.exception("总控会话建立失败，唤醒被丢弃")
            return
        self._pump_for(str(session.id)).wake()

    def _spawn(self, coro: Any, *, name: str) -> None:
        try:
            task = asyncio.create_task(coro, name=name)
        except RuntimeError:
            logger.warning("总控服务没有事件循环，任务 %s 未启动", name)
            return
        self._tasks.add(task)
        task.add_done_callback(self._tasks.discard)


def _is_live_orchestrator(session: AgentSession | None) -> bool:
    """该行是否仍是当前可用的活动总控会话。"""
    return (
        session is not None
        and str(session.kind) == AgentSessionKind.ORCHESTRATOR
        and str(session.status) == AgentSessionStatus.ACTIVE
        and session.archived_at is None
    )


async def _active_orchestrator_sessions(session: Any) -> list[AgentSession]:
    """全部活动总控会话，``created_at`` 降序（同刻用 id 兜稳定次序）。"""
    result = await session.execute(
        select(AgentSession)
        .where(
            AgentSession.kind == AgentSessionKind.ORCHESTRATOR,
            AgentSession.status == AgentSessionStatus.ACTIVE,
            AgentSession.archived_at.is_(None),
        )
        .order_by(AgentSession.created_at.desc(), AgentSession.id.desc())
    )
    return list(result.scalars().all())
