"""事件入表、两级过滤与路由（M81 规格 §4.5.1–§4.5.3）。

``TriggerService.publish`` 是事件进入系统的唯一入口，一次处理分三步：

1. **入表与准入**：先做程序准入（``kinds.validate_event``），不合法的行记
   ``rejected``；合法的行按 ``dedupe_key`` 去重后入表，``processed_at`` 为空表示
   还没处理完。同一话题的发布按到达顺序串行（每话题一把引用计数的锁，照
   ``incidents._key_lock``）。
2. **两级过滤**：先看话题有没有活跃订阅——有就直通 Worker 收件箱，不回落总控
   （M70 的流水线终态直通）；没有才按规则匹配，再按规则档位决定建工单、写观察
   记录或交总控。研判受每日次数上限与 ``asyncio.Semaphore`` 两道闸限制。
3. **收尾**：一次 UPDATE 写终值；``recover`` 也调 ``_process``，所以每一步都先查
   是否做过（收件箱项、工单、观察记录都有幂等入口）。

本模块只做编排：规则合并与匹配在 :mod:`maa_api.agent.events.rules`，研判在
:mod:`maa_api.agent.events.evaluator`，建单、订阅与收件箱分别归 M69/M70 的服务。

定时 job 归本模块（§4.5.5）：``start_jobs`` 注册理智估算 job（``maa-trigger-sanity``）
与当前启用的定时规则 job（``maa-trigger-rule-<rule_id>``），并订阅规则变化重排；
``close`` 只移除自己注册过的 job。
"""

from __future__ import annotations

import asyncio
import inspect
import json
import logging
from collections.abc import AsyncIterator, Callable, Mapping
from contextlib import asynccontextmanager
from dataclasses import dataclass
from datetime import UTC, date, datetime, time, timedelta
from typing import Any
from zoneinfo import ZoneInfo

from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from maa_api.agent.events.builtin_rules import (
    DraftContext,
    TicketDraft,
    default_draft,
)
from maa_api.agent.events.evaluator import EvalOutcome
from maa_api.agent.events.kinds import needs_attention, summarize, validate_event
from maa_api.agent.events.rules import (
    RULE_CRON_TIMEZONE,
    EffectiveRule,
    RuleSet,
)
from maa_api.db.models import AgentInbox, AgentTrigger, TriggerObservation, utcnow
from maa_api.db.repositories.agent import AgentSessionRepository
from maa_api.db.repositories.ticket import AgentInboxRepository, TicketRepository
from maa_api.db.repositories.triggers import (
    TriggerObservationRepository,
    TriggerRepository,
)
from maa_api.domain.enums import (
    EventSource,
    InboxKind,
    MandateStrength,
    RiskLevel,
    TicketOrigin,
    TriggerAdmission,
    TriggerEvalStatus,
    TriggerMode,
    TriggerRoute,
)
from maa_api.domain.errors import AppError, ErrorCode
from maa_api.services.schedule_service import cron_trigger

logger = logging.getLogger(__name__)

__all__ = ["PublishResult", "TriggerService"]

#: 全局最多同时研判两条事件（§4.5.2 第 4 步）。
EVAL_CONCURRENCY = 2
#: ``recover`` 补处理的时间窗：超过 24 小时的未处理事件直接过期（§4.5.3）。
RESTART_WINDOW = timedelta(hours=24)
#: 交总控时 payload 字段的 JSON 上限（§4.5.2 ``forward_to_orchestrator``）。
FORWARD_PAYLOAD_CHARS = 2000
#: 理智估算 job 的 id 与间隔（§4.5.5）。
SANITY_JOB_ID = "maa-trigger-sanity"
SANITY_INTERVAL_MINUTES = 30
#: 定时规则 job 的 id 前缀；完整 id 是 ``maa-trigger-rule-<rule_id>``（§4.5.5）。
RULE_JOB_PREFIX = "maa-trigger-rule-"
#: 定时规则 job 的 misfire 宽限（秒，§4.5.5）。
RULE_MISFIRE_GRACE_SECONDS = 300


def _rule_job_id(rule_id: str) -> str:
    """``maa-trigger-rule-<rule_id>``。"""
    return f"{RULE_JOB_PREFIX}{rule_id}"


@dataclass(frozen=True, slots=True)
class PublishResult:
    """一次 ``publish`` 的结果；``duplicate`` 为真时不重复处理（§4.2）。"""

    trigger_id: int | None
    duplicate: bool
    admission: TriggerAdmission
    routed_to: TriggerRoute
    ticket_id: str | None = None
    observation_id: int | None = None
    eval_status: TriggerEvalStatus = TriggerEvalStatus.SKIPPED


def _json(value: Any, limit: int | None = None) -> str:
    try:
        text = json.dumps(value, ensure_ascii=False, default=str)
    except (TypeError, ValueError):
        text = "{}"
    return text if limit is None else text[:limit]


def _utc_naive(value: datetime) -> datetime:
    """把可能带时区的时间统一成库内的 UTC naive。"""
    if value.tzinfo is None:
        return value
    return value.astimezone(UTC).replace(tzinfo=None)


def _iso_z(value: datetime) -> str:
    """库内 UTC naive → ``...Z`` 的 ISO 时间（WS 与 REST 同口径）。"""
    if value.tzinfo is None:
        value = value.replace(tzinfo=UTC)
    return value.astimezone(UTC).isoformat().replace("+00:00", "Z")


def _origin_for(source: str) -> TicketOrigin:
    """事件来源 → 工单来源：goal / watchdog 各自对应，其余都是 trigger（§4.5.2）。"""
    if source == EventSource.GOAL.value:
        return TicketOrigin.GOAL
    if source == EventSource.WATCHDOG.value:
        return TicketOrigin.WATCHDOG
    return TicketOrigin.TRIGGER


def _goal_id_for(row: AgentTrigger) -> str | None:
    """目标事件建出的工单要指回目标（M82 §4.2）；其他来源不带。"""
    if row.source != EventSource.GOAL.value:
        return None
    value = row.payload.get("goal_id")
    return str(value) if value else None


def _route(value: str | None) -> TriggerRoute:
    return TriggerRoute(value) if value else TriggerRoute.NONE


def _eval_status(value: str | None) -> TriggerEvalStatus:
    return (
        TriggerEvalStatus(value) if value else TriggerEvalStatus.SKIPPED
    )


class TriggerService:
    """事件入表、过滤与路由的唯一入口（M81 §4.2、§4.5.1–§4.5.3）。"""

    def __init__(
        self,
        session_factory: async_sessionmaker[AsyncSession],
        *,
        app: Any,
        rules: Any,
        evaluator: Any,
        ticket_service: Any,
        thread_router: Any,
        orchestrator_service: Any,
        broadcast: Callable[[str, dict[str, Any]], Any] | None,
        settings_provider: Callable[[], Any],
        clock: Callable[[], datetime] = utcnow,
    ) -> None:
        self._session_factory = session_factory
        self._app = app
        self._rules = rules
        self._evaluator = evaluator
        self._ticket_service = ticket_service
        self._thread_router = thread_router
        #: 转发总控走 ``thread_router.to_orchestrator``（M70 的写收件箱 + 唤醒入口），
        #: 服务本身只在需要解析当前会话时用它；保留注入以与规格签名一致。
        self._orchestrator_service = orchestrator_service
        self._broadcast = broadcast
        self._settings_provider = settings_provider
        self._clock = clock

        #: 每个话题一把锁，引用计数归零就删（照 ``incidents._key_lock``）。
        self._thread_locks: dict[str, tuple[asyncio.Lock, int]] = {}
        #: 研判并发上限（§4.5.2 第 4 步）。
        self._eval_slots = asyncio.Semaphore(EVAL_CONCURRENCY)
        #: 进程内每日研判计数：按本地日期分桶，当天第一次从库里接上（§4.5.2）。
        self._daily_date: date | None = None
        self._daily_count = 0
        #: ``publish_nowait`` 起任务，``close`` 时取消并等待。
        self._tasks: set[asyncio.Task[Any]] = set()
        #: G5 的定时 job：只移除自己注册过的 id（照 ``ScheduleService``）。
        self._scheduler: Any = None
        self._owned_job_ids: set[str] = set()
        #: 已注册定时规则 job 的 cron，用于规则改了 cron 时重排（§4.5.5）。
        self._job_crons: dict[str, str] = {}
        #: ``start_jobs`` 首次按库里的规则集注册 job 的任务；测试可 await 它以消除竞态。
        self._jobs_ready: asyncio.Task[Any] | None = None
        self._closed = False

        # 事件管线建单时要建 Worker 会话；Worker 服务装配在本服务之后，传惰性解析
        # 函数而不是实例（``TicketService.create`` 只在带 brief 时才会用到它）。
        bind = getattr(ticket_service, "bind_worker_service", None)
        if callable(bind):
            bind(lambda: getattr(getattr(app, "state", None), "worker_service", None))

    # ------------------------------------------------------------------
    # 发布
    # ------------------------------------------------------------------
    async def publish(
        self,
        *,
        source: EventSource | str,
        kind: str,
        thread_key: str,
        payload: Mapping[str, Any] | None = None,
        sender: str | None = None,
        dedupe_key: str | None = None,
    ) -> PublishResult:
        """把一条事件写进 ``agent_trigger`` 并完成准入、研判与路由（§4.5.1）。"""
        source_text = source.value if isinstance(source, EventSource) else str(source)
        data = dict(payload or {})
        async with self._thread_lock(thread_key):
            problem = validate_event(source_text, kind, thread_key, data)
            now = self._clock()
            if problem is not None:
                stored_payload = dict(data)
                if problem == "payload_too_large":
                    # 超限时只留原因与大小，不把 16 KiB 以上的正文再存一遍。
                    stored_payload = {
                        "rejected": problem,
                        "size": len(_json(data).encode("utf-8")),
                    }
                row = await self._insert(
                    source=source_text,
                    kind=kind,
                    thread_key=thread_key,
                    sender=sender,
                    payload=stored_payload,
                    dedupe_key=None,
                    admission=TriggerAdmission.REJECTED,
                    eval_status=TriggerEvalStatus.SKIPPED,
                    eval_reason=problem,
                    routed_to=TriggerRoute.NONE,
                    processed_at=now,
                )
                return PublishResult(
                    trigger_id=row.id,
                    duplicate=False,
                    admission=TriggerAdmission.REJECTED,
                    routed_to=TriggerRoute.NONE,
                )

            try:
                row = await self._insert(
                    source=source_text,
                    kind=kind,
                    thread_key=thread_key,
                    sender=sender,
                    payload=data,
                    dedupe_key=dedupe_key,
                    admission=TriggerAdmission.ACCEPTED,
                    eval_status=TriggerEvalStatus.PENDING,
                    eval_reason=None,
                    routed_to=None,
                    processed_at=None,
                )
            except IntegrityError:
                existing = (
                    await self._by_dedupe_key(dedupe_key)
                    if dedupe_key is not None
                    else None
                )
                if existing is None:
                    raise
                return PublishResult(
                    trigger_id=existing.id,
                    duplicate=True,
                    admission=TriggerAdmission(existing.admission),
                    routed_to=_route(existing.routed_to),
                    ticket_id=existing.ticket_id,
                    eval_status=_eval_status(existing.eval_status),
                )
            return await self._process(row)

    def publish_nowait(self, **kwargs: Any) -> None:
        """同步上下文用：在当前事件循环上起一个被跟踪的任务，异常只记日志。"""
        if self._closed:
            logger.warning("事件服务已关闭，丢弃一条异步发布：kind=%s", kwargs.get("kind"))
            return
        try:
            task = asyncio.create_task(
                self._publish_quiet(**kwargs), name="trigger-publish"
            )
        except RuntimeError:
            logger.warning(
                "事件服务没有事件循环，丢弃一条异步发布：kind=%s", kwargs.get("kind")
            )
            return
        self._tasks.add(task)
        task.add_done_callback(self._tasks.discard)

    async def _publish_quiet(self, **kwargs: Any) -> None:
        try:
            await self.publish(**kwargs)
        except Exception:  # noqa: BLE001 - 后台任务失败不能抛成未处理任务
            logger.exception("异步发布事件失败：kind=%s", kwargs.get("kind"))

    # ------------------------------------------------------------------
    # 六步处理
    # ------------------------------------------------------------------
    async def _process(self, row: AgentTrigger) -> PublishResult:
        """按 §4.5.2 的六步处理一行；``recover`` 也调它，每一步先查是否做过。"""
        if row.processed_at is not None:
            return self._result_from_row(row, duplicate=False)

        # 1. 订阅直通：有活跃订阅时只投 Worker 收件箱，不按规则、不回落总控。
        inbox_kind = (
            InboxKind.USER_MESSAGE
            if row.kind == "im.message"
            else InboxKind.THREAD_EVENT
        )
        ref_id = str(row.id)
        delivered = await self._find_delivery(inbox_kind, ref_id)
        if delivered is not None:
            ticket_id = await self._ticket_id_for_inbox(delivered)
        else:
            tickets = await self._thread_router.deliver_to_subscribers(
                row.thread_key,
                inbox_kind=inbox_kind,
                ref_id=ref_id,
                payload=self._delivery_payload(row),
            )
            ticket_id = tickets[0] if tickets else None
        if ticket_id is not None or delivered is not None:
            return await self._finish(
                row,
                admission=TriggerAdmission.ACCEPTED,
                eval_status=TriggerEvalStatus.SKIPPED,
                eval_reason="subscribed",
                routed_to=TriggerRoute.TICKET,
                ticket_id=ticket_id,
            )

        # 2. 规则匹配：总开关关掉时只入表，不匹配规则。
        settings = await self._settings()
        enabled = bool(getattr(settings.triggers, "enabled", True))
        rule: EffectiveRule | None = None
        version = ""
        if enabled:
            rule_set = await self._rules.rule_set()
            rule = rule_set.match(row, settings)
            version = rule_set.version
        if rule is None:
            return await self.fallback(
                row, reason="no_rule" if enabled else "triggers_disabled"
            )

        # 3. 冷却：同一规则、同一话题在冷却期内真正处理过就不再处理。
        if rule.cooldown_seconds > 0:
            since = self._clock() - timedelta(seconds=rule.cooldown_seconds)
            async with self._session_factory() as session:
                recent = await TriggerRepository(session).last_routed(
                    rule_id=rule.id, thread_key=row.thread_key, since=since
                )
            if recent is not None:
                return await self._finish(
                    row,
                    admission=TriggerAdmission.ACCEPTED,
                    eval_status=TriggerEvalStatus.SKIPPED,
                    eval_reason="cooldown",
                    routed_to=TriggerRoute.NONE,
                    rule_id=rule.id,
                )

        # 4. 研判：不研判的规则记 skipped，超出每日上限记 unavailable。
        mode = rule.effective_mode(settings)
        if not rule.policy:
            outcome = EvalOutcome(
                status=TriggerEvalStatus.ACCEPT,
                decision=None,
                reason="no_policy",
                run_id=None,
            )
            eval_status = TriggerEvalStatus.SKIPPED
        else:
            limit = int(getattr(settings.triggers, "eval_daily_limit", 0))
            async with self._eval_slots:
                if not await self._reserve_eval(limit):
                    outcome = EvalOutcome(
                        status=TriggerEvalStatus.UNAVAILABLE,
                        decision=None,
                        reason="daily_limit",
                        run_id=None,
                    )
                else:
                    outcome = await self._evaluator.evaluate(row, rule)
            eval_status = outcome.status

        # 5. 分支：档位只看规则（OFF 的规则不会被 match 返回）。
        if outcome.status == TriggerEvalStatus.ACCEPT:
            draft = await self._build_draft(row, rule, outcome.decision)
            if mode == TriggerMode.AUTO:
                try:
                    ticket = await self.create_ticket(row, rule, draft, version)
                except AppError as exc:
                    reason = f"ticket_failed:{exc.code}"
                    return await self.fallback(
                        row,
                        reason=reason,
                        rule_id=rule.id,
                        eval_status=eval_status,
                        eval_reason=reason,
                        eval_run_id=outcome.run_id,
                    )
                return await self._finish(
                    row,
                    admission=TriggerAdmission.ACCEPTED,
                    eval_status=eval_status,
                    eval_reason=outcome.reason,
                    routed_to=TriggerRoute.TICKET,
                    rule_id=rule.id,
                    eval_run_id=outcome.run_id,
                    ticket_id=ticket.id,
                )

            decision = self._decision_payload(rule, draft, outcome.reason)
            observation = await self.write_observation(row, rule, decision)
            route = (
                TriggerRoute.ORCHESTRATOR
                if needs_attention(row.kind, row.payload)
                else TriggerRoute.OBSERVATION
            )
            if route == TriggerRoute.ORCHESTRATOR:
                await self.forward_to_orchestrator(
                    row, reason=outcome.reason, rule_id=rule.id
                )
            return await self._finish(
                row,
                admission=TriggerAdmission.ACCEPTED,
                eval_status=eval_status,
                eval_reason=outcome.reason,
                routed_to=route,
                rule_id=rule.id,
                eval_run_id=outcome.run_id,
                observation_id=observation.id,
            )

        if outcome.status == TriggerEvalStatus.STOP:
            if mode == TriggerMode.AUTO:
                await self.forward_to_orchestrator(
                    row, reason=outcome.reason, rule_id=rule.id
                )
                return await self._finish(
                    row,
                    admission=TriggerAdmission.ACCEPTED,
                    eval_status=eval_status,
                    eval_reason=outcome.reason,
                    routed_to=TriggerRoute.ORCHESTRATOR,
                    rule_id=rule.id,
                    eval_run_id=outcome.run_id,
                )
            decision = self._decision_payload(rule, None, outcome.reason)
            observation = await self.write_observation(row, rule, decision)
            route = (
                TriggerRoute.ORCHESTRATOR
                if needs_attention(row.kind, row.payload)
                else TriggerRoute.OBSERVATION
            )
            if route == TriggerRoute.ORCHESTRATOR:
                await self.forward_to_orchestrator(
                    row, reason=outcome.reason, rule_id=rule.id
                )
            return await self._finish(
                row,
                admission=TriggerAdmission.ACCEPTED,
                eval_status=eval_status,
                eval_reason=outcome.reason,
                routed_to=route,
                rule_id=rule.id,
                eval_run_id=outcome.run_id,
                observation_id=observation.id,
            )

        # IGNORE / UNAVAILABLE：按 fallback 走（命中过规则，交总控那步仍适用）。
        return await self.fallback(
            row,
            reason=outcome.reason,
            rule_id=rule.id,
            eval_status=outcome.status,
            eval_reason=outcome.reason,
            eval_run_id=outcome.run_id,
        )

    async def fallback(
        self,
        row: AgentTrigger,
        *,
        reason: str,
        rule_id: str | None = None,
        eval_status: TriggerEvalStatus = TriggerEvalStatus.SKIPPED,
        eval_reason: str | None = None,
        eval_run_id: str | None = None,
    ) -> PublishResult:
        """没有建单/写观察时的去处：需要的话交总控，否则只记录（§4.5.2）。"""
        reason_text = eval_reason if eval_reason is not None else reason
        if needs_attention(row.kind, row.payload):
            await self.forward_to_orchestrator(row, reason=reason, rule_id=rule_id)
            return await self._finish(
                row,
                admission=TriggerAdmission.ACCEPTED,
                eval_status=eval_status,
                eval_reason=reason_text,
                routed_to=TriggerRoute.ORCHESTRATOR,
                rule_id=rule_id,
                eval_run_id=eval_run_id,
            )
        admission = (
            TriggerAdmission.ACCEPTED
            if rule_id is not None
            else TriggerAdmission.IGNORED
        )
        return await self._finish(
            row,
            admission=admission,
            eval_status=eval_status,
            eval_reason=reason_text,
            routed_to=TriggerRoute.NONE,
            rule_id=rule_id,
            eval_run_id=eval_run_id,
        )

    # ------------------------------------------------------------------
    # 建单 / 交总控 / 观察记录
    # ------------------------------------------------------------------
    async def create_ticket(
        self,
        row: AgentTrigger,
        rule: EffectiveRule,
        draft: TicketDraft,
        version: str,
    ) -> Any:
        """按规则与草稿建工单并订阅话题；恢复时复用同一事件已建的工单（§4.5.2）。"""
        if not rule.worker:
            raise AppError(
                ErrorCode.INVALID_PARAMETER,
                "事件规则没有执行代理",
                {"rule_id": rule.id},
            )
        async with self._session_factory() as session:
            ticket = await TicketRepository(session).get_by_source_trigger(int(row.id))
        if ticket is None:
            ticket, _mandate = await self._ticket_service.create(
                kind=rule.worker,
                title=draft.title,
                brief=draft.brief,
                scope=draft.scope,
                origin=_origin_for(row.source),
                strength=MandateStrength.INFERRED,
                source=EventSource(row.source),
                sender=f"rule:{rule.id}",
                rule_id=rule.id,
                risk_ceiling=RiskLevel.NONE,
                policy_version=version,
                source_trigger_id=int(row.id),
                goal_id=_goal_id_for(row),
            )
        try:
            await self._thread_router.subscribe(ticket.id, row.thread_key)
        except AppError:
            # 工单已结束（恢复场景）或话题不可订阅：工单本身仍有效，只记日志。
            logger.warning(
                "事件 %s 建出的工单 %s 没能订阅话题 %s",
                row.id,
                ticket.id,
                row.thread_key,
            )
        return ticket

    async def forward_to_orchestrator(
        self,
        row: AgentTrigger,
        *,
        reason: str,
        rule_id: str | None = None,
    ) -> int | None:
        """把事件写进总控收件箱并唤醒；同一事件只写一次（§4.5.2）。"""
        inbox_kind = (
            InboxKind.USER_MESSAGE
            if row.kind == "im.message"
            else InboxKind.TRIGGER
        )
        ref_id = str(row.id)
        async with self._session_factory() as session:
            if await AgentInboxRepository(session).exists(inbox_kind, ref_id):
                return None
        payload = {
            "trigger_id": row.id,
            "source": row.source,
            "kind": row.kind,
            "thread_key": row.thread_key,
            "summary": summarize(row.kind, row.payload),
            "payload": _json(row.payload, FORWARD_PAYLOAD_CHARS),
            "rule_id": rule_id,
            "reason": reason,
        }
        return await self._thread_router.to_orchestrator(
            kind=inbox_kind,
            thread_key=row.thread_key,
            ref_id=ref_id,
            payload=payload,
        )

    async def write_observation(
        self,
        row: AgentTrigger,
        rule: EffectiveRule,
        decision: Mapping[str, Any],
    ) -> TriggerObservation:
        """写一条观察记录并广播 ``observation_created``；同一条事件只写一条。"""
        async with self._session_factory() as session:
            repository = TriggerObservationRepository(session)
            existing = await repository.get_by_trigger(int(row.id))
            if existing is not None:
                return existing
            created = await repository.create(
                TriggerObservation(
                    trigger_id=int(row.id),
                    rule_id=rule.id,
                    decision=dict(decision),
                    created_at=self._clock(),
                )
            )
            await session.commit()
        await self._broadcast_observation(row, rule, created)
        return created

    async def _broadcast_observation(
        self,
        row: AgentTrigger,
        rule: EffectiveRule,
        observation: TriggerObservation,
    ) -> None:
        if self._broadcast is None:
            return
        data: dict[str, Any] = {
            "session_id": None,
            "event": "observation_created",
            "payload": {
                "observation_id": observation.id,
                "trigger_id": row.id,
                "rule_id": rule.id,
                "summary": summarize(row.kind, row.payload),
                "created_at": _iso_z(observation.created_at),
            },
        }
        try:
            result = self._broadcast("agent_event", data)
            if inspect.isawaitable(result):
                await result
        except Exception:  # noqa: BLE001 - 观察记录已落库，广播失败不能回滚
            logger.warning(
                "观察记录广播失败：observation=%s", observation.id, exc_info=True
            )

    # ------------------------------------------------------------------
    # 恢复与生命周期
    # ------------------------------------------------------------------
    async def recover(self) -> int:
        """启动补处理：24 小时内按 id 顺序重跑，更早的直接过期（§4.5.3）。"""
        async with self._session_factory() as session:
            rows = await TriggerRepository(session).unprocessed()
        cutoff = self._clock() - RESTART_WINDOW
        handled = 0
        for row in rows:
            if _utc_naive(row.created_at) >= cutoff:
                await self._process(row)
            else:
                await self._finish(
                    row,
                    admission=TriggerAdmission.ACCEPTED,
                    eval_status=TriggerEvalStatus.SKIPPED,
                    eval_reason="expired_after_restart",
                    routed_to=TriggerRoute.NONE,
                )
            handled += 1
        return handled

    def start_jobs(self, scheduler: Any) -> None:
        """注册理智估算 job 与定时规则 job，并订阅规则变化（§4.5.5）。

        ``maa-trigger-sanity`` 立即注册；定时规则要先读库里的规则集，所以首次注册
        放到一个被跟踪的任务里（``_jobs_ready``），不阻塞 lifespan 的启动流程。
        规则写入 / 删除后，``TriggerRuleService`` 会同步调用 :meth:`_sync_rule_jobs`
        增删 job。
        """
        if self._closed:
            return
        self._scheduler = scheduler
        # 局部导入：``wiring`` 只在类型检查时引用本模块，避免包内互相拖入。
        from maa_api.agent.events.wiring import SanityWatcher

        watcher = SanityWatcher(self, self._session_factory, clock=self._clock)
        self._add_sanity_job(watcher)
        self._rules.add_listener(self._sync_rule_jobs)
        self._jobs_ready = self._spawn_jobs_sync()

    def _add_sanity_job(self, watcher: Any) -> None:
        """注册 30 分钟一次的理智估算 job（``max_instances=1``、``coalesce=True``）。"""
        try:
            self._scheduler.add_job(
                watcher.tick,
                "interval",
                minutes=SANITY_INTERVAL_MINUTES,
                id=SANITY_JOB_ID,
                name="事件：理智估算",
                max_instances=1,
                coalesce=True,
                replace_existing=True,
            )
        except Exception:  # noqa: BLE001 - 调度器已关闭时不能拖垮启动
            logger.warning("注册理智估算 job 失败", exc_info=True)
            return
        self._owned_job_ids.add(SANITY_JOB_ID)

    def _spawn_jobs_sync(self) -> asyncio.Task[Any] | None:
        """起一个任务按当前规则集注册 job；没有事件循环时记日志跳过。"""
        try:
            task = asyncio.create_task(self._sync_jobs_quiet(), name="trigger-rule-jobs")
        except RuntimeError:
            logger.warning("事件服务没有事件循环，跳过首次定时规则注册")
            return None
        self._tasks.add(task)
        task.add_done_callback(self._tasks.discard)
        return task

    async def _sync_jobs_quiet(self) -> None:
        try:
            await self.sync_jobs()
        except Exception:  # noqa: BLE001 - 启动期规则读取失败只记日志
            logger.exception("首次注册定时规则 job 失败")

    async def sync_jobs(self) -> None:
        """按当前规则集重排定时规则 job（``start_jobs`` 首次调用）。"""
        if self._closed or self._scheduler is None:
            return
        rule_set = await self._rules.rule_set()
        self._sync_rule_jobs(rule_set)

    def _sync_rule_jobs(self, rule_set: RuleSet) -> None:
        """规则变化后增删定时规则 job；只动自己注册过的 id（§4.5.5）。"""
        if self._closed or self._scheduler is None:
            return
        settings = self._settings_provider()
        desired: dict[str, EffectiveRule] = {}
        for rule in rule_set.all():
            if rule.source != EventSource.SCHEDULE.value or not rule.cron:
                continue
            if rule.effective_mode(settings) == TriggerMode.OFF:
                continue
            desired[_rule_job_id(rule.id)] = rule

        for job_id in tuple(self._owned_job_ids):
            if job_id == SANITY_JOB_ID or job_id in desired:
                continue
            self._remove_job(job_id)
        for job_id, rule in desired.items():
            if (
                job_id in self._owned_job_ids
                and self._job_crons.get(job_id) == rule.cron
            ):
                continue
            self._add_rule_job(job_id, rule)

    def _add_rule_job(self, job_id: str, rule: EffectiveRule) -> None:
        """注册（或按新 cron 替换）一条定时规则 job。"""
        try:
            trigger = cron_trigger(rule.cron or "", RULE_CRON_TIMEZONE)
        except (ValueError, TypeError):
            logger.warning("定时规则 cron 解析失败，跳过注册：%s", rule.cron)
            return
        try:
            self._scheduler.add_job(
                self._fire_rule,
                trigger=trigger,
                args=[rule.id],
                id=job_id,
                name=f"事件规则：{rule.title}",
                misfire_grace_time=RULE_MISFIRE_GRACE_SECONDS,
                coalesce=True,
                max_instances=1,
                replace_existing=True,
            )
        except Exception:  # noqa: BLE001 - 调度器已关闭时不能打断规则写入
            logger.warning("注册定时规则 job 失败：%s", job_id, exc_info=True)
            return
        self._owned_job_ids.add(job_id)
        self._job_crons[job_id] = rule.cron or ""

    async def _fire_rule(self, rule_id: str) -> None:
        """定时规则到点：发一条 ``schedule.fired``（§4.5.5）。"""
        now = self._clock()
        if now.tzinfo is None:
            now = now.replace(tzinfo=UTC)
        scheduled = now.astimezone(ZoneInfo(RULE_CRON_TIMEZONE)).replace(
            second=0, microsecond=0
        )
        scheduled_for = scheduled.isoformat()
        await self.publish(
            source=EventSource.SCHEDULE,
            kind="schedule.fired",
            thread_key=f"schedule:{rule_id}",
            payload={"rule_id": rule_id, "scheduled_for": scheduled_for},
            dedupe_key=f"schedule:{rule_id}:{scheduled_for}",
        )

    def _remove_job(self, job_id: str) -> None:
        """移除一个自己注册的 job，并忘记它的 cron。"""
        try:
            self._scheduler.remove_job(job_id)
        except Exception:  # noqa: BLE001 - job 可能已被调度器清理
            logger.debug("移除事件 job 失败：%s", job_id)
        self._owned_job_ids.discard(job_id)
        self._job_crons.pop(job_id, None)

    async def close(self) -> None:
        """移除自己的 job，取消并等待 ``publish_nowait`` 起的任务。"""
        self._closed = True
        if self._scheduler is not None:
            for job_id in tuple(self._owned_job_ids):
                try:
                    self._scheduler.remove_job(job_id)
                except Exception:  # noqa: BLE001 - job 可能已被调度器清理
                    logger.debug("移除事件 job 失败：%s", job_id)
            self._owned_job_ids.clear()
            self._job_crons.clear()
        pending = [task for task in self._tasks if not task.done()]
        for task in pending:
            task.cancel()
        if pending:
            await asyncio.gather(*pending, return_exceptions=True)
        self._tasks.clear()
        self._jobs_ready = None

    # ------------------------------------------------------------------
    # 内部：入表、收尾、锁与计数
    # ------------------------------------------------------------------
    async def _insert(
        self,
        *,
        source: str,
        kind: str,
        thread_key: str,
        sender: str | None,
        payload: dict[str, Any],
        dedupe_key: str | None,
        admission: TriggerAdmission,
        eval_status: TriggerEvalStatus,
        eval_reason: str | None,
        routed_to: TriggerRoute | None,
        processed_at: datetime | None,
    ) -> AgentTrigger:
        """单独事务写入一行；``dedupe_key`` 冲突时让 ``IntegrityError`` 抛出。"""
        row = AgentTrigger(
            source=source,
            kind=kind,
            thread_key=thread_key,
            sender=sender,
            payload=payload,
            dedupe_key=dedupe_key,
            admission=str(admission),
            eval_status=str(eval_status),
            eval_reason=eval_reason,
            routed_to=str(routed_to) if routed_to is not None else None,
            created_at=self._clock(),
            processed_at=processed_at,
        )
        async with self._session_factory() as session:
            stored = await TriggerRepository(session).insert(row)
            await session.commit()
        return stored

    async def _by_dedupe_key(self, dedupe_key: str) -> AgentTrigger | None:
        async with self._session_factory() as session:
            return await TriggerRepository(session).get_by_dedupe_key(dedupe_key)

    async def _finish(
        self,
        row: AgentTrigger,
        *,
        admission: TriggerAdmission,
        eval_status: TriggerEvalStatus,
        eval_reason: str | None,
        routed_to: TriggerRoute,
        rule_id: str | None = None,
        eval_run_id: str | None = None,
        ticket_id: str | None = None,
        observation_id: int | None = None,
    ) -> PublishResult:
        """一次 UPDATE 写终值并返回结果；恢复重跑时重复写同样的值。"""
        async with self._session_factory() as session:
            stored = await TriggerRepository(session).get(int(row.id))
            if stored is None:
                raise RuntimeError(f"事件行不见了：{row.id}")
            stored.admission = str(admission)
            stored.rule_id = rule_id
            stored.eval_status = str(eval_status)
            stored.eval_reason = eval_reason
            stored.eval_run_id = eval_run_id
            stored.ticket_id = ticket_id
            stored.routed_to = str(routed_to)
            stored.processed_at = self._clock()
            await session.commit()
        return PublishResult(
            trigger_id=row.id,
            duplicate=False,
            admission=admission,
            routed_to=routed_to,
            ticket_id=ticket_id,
            observation_id=observation_id,
            eval_status=eval_status,
        )

    @staticmethod
    def _result_from_row(row: AgentTrigger, *, duplicate: bool) -> PublishResult:
        return PublishResult(
            trigger_id=row.id,
            duplicate=duplicate,
            admission=TriggerAdmission(row.admission),
            routed_to=_route(row.routed_to),
            ticket_id=row.ticket_id,
            eval_status=_eval_status(row.eval_status),
        )

    def _delivery_payload(self, row: AgentTrigger) -> dict[str, Any]:
        """订阅直通的收件箱 payload：四个公共键，流水线种类补 payload 本身的键。"""
        data: dict[str, Any] = {
            "trigger_id": row.id,
            "kind": row.kind,
            "summary": summarize(row.kind, row.payload),
            "payload": row.payload,
        }
        if row.kind.startswith("pipeline."):
            for key, value in row.payload.items():
                data.setdefault(str(key), value)
        return data

    async def _find_delivery(
        self, inbox_kind: InboxKind, ref_id: str
    ) -> AgentInbox | None:
        async with self._session_factory() as session:
            return await AgentInboxRepository(session).find_ref(inbox_kind, ref_id)

    async def _ticket_id_for_inbox(self, item: AgentInbox) -> str | None:
        """已投递过的收件箱项属于哪个工单的 Worker 会话（恢复时补 ``ticket_id``）。"""
        async with self._session_factory() as session:
            stored = await AgentSessionRepository(session).get(item.session_id)
        if stored is None or stored.ticket_id is None:
            return None
        return str(stored.ticket_id)

    async def _build_draft(
        self,
        row: AgentTrigger,
        rule: EffectiveRule,
        decision: Any,
    ) -> TicketDraft:
        builder = (
            rule.builtin.draft
            if rule.builtin is not None and rule.builtin.draft is not None
            else default_draft
        )
        return await builder(
            DraftContext(trigger=row, rule=rule, decision=decision, app=self._app)
        )

    @staticmethod
    def _decision_payload(
        rule: EffectiveRule, draft: TicketDraft | None, reason: str
    ) -> dict[str, Any]:
        """观察记录的 ``decision``：形状见 §4.1（stop 没有草稿三段）。"""
        payload: dict[str, Any] = {
            "decision": "stop" if draft is None else "accept",
            "worker": rule.worker,
            "title": None,
            "brief": None,
            "scope": None,
            "reason": reason,
        }
        if draft is not None:
            payload.update(
                title=draft.title, brief=draft.brief, scope=draft.scope
            )
        return payload

    async def _settings(self) -> Any:
        value = self._settings_provider()
        if inspect.isawaitable(value):
            value = await value
        return value

    async def _reserve_eval(self, limit: int) -> bool:
        """占用一次当天的研判额度；按本地日期分桶，跨重启从库里接上。"""
        local_now = _utc_naive(self._clock()).replace(tzinfo=UTC).astimezone()
        today = local_now.date()
        if self._daily_date != today:
            midnight_local = datetime.combine(
                today, time.min, tzinfo=local_now.tzinfo
            )
            since = midnight_local.astimezone(UTC).replace(tzinfo=None)
            async with self._session_factory() as session:
                count = await TriggerRepository(session).count_evaluated_since(since)
            self._daily_date = today
            self._daily_count = int(count)
        if self._daily_count >= limit:
            return False
        self._daily_count += 1
        return True

    @asynccontextmanager
    async def _thread_lock(self, key: str) -> AsyncIterator[None]:
        """每话题一把锁；引用计数归零就删（照 ``incidents._key_lock``）。"""
        lock, users = self._thread_locks.get(key, (asyncio.Lock(), 0))
        self._thread_locks[key] = (lock, users + 1)
        try:
            async with lock:
                yield
        finally:
            current_lock, users = self._thread_locks[key]
            if users <= 1:
                self._thread_locks.pop(key, None)
            else:
                self._thread_locks[key] = (current_lock, users - 1)
