"""设备租约服务（M71 规格 §4.2、§4.5.1–§4.5.3）。

一台设备同一时刻只归一个工单（或一个没有工单的根运行）使用。状态只存在
``device_lease`` 表的那一行里：进程崩溃后重启由恢复流程清掉上一进程的行，
服务本身不维护「谁持有」的内存副本。

三条并发纪律：

- **全部数据库写法都在同一把 :class:`asyncio.Condition` 里**，各自开会话、各自
  提交。锁只保护「读—判断—写」这一小段，排队等待用 ``Condition.wait`` 让出锁。
- **锁外才回调与广播**：``on_released``（唤醒队列消费者）、WS ``lease_changed``、
  工单收件箱投递都在释放锁之后做，慢回调不阻塞其他排队者；异常只记日志。
- **先到先得**：每个 ``acquire`` 进锁时往 ``deque`` 尾部放一个记号，只有队首的
  记号能在「没有行」时插入；其余的人重读或按 ``poll_seconds`` 定时醒来，因此
  租约过期、外部删除都能在有限时间内被发现。

本模块包含数据类、:class:`DeviceLeaseService` 与 :class:`LeaseMiddleware`；中间件
经 ``ctx.state["policy.before_execute"]`` 登记执行前钩子，由 ``PolicyMiddleware``
在确认通过之后、执行之前调用（M71 §4.5.5）。
"""

from __future__ import annotations

import asyncio
import dataclasses
import logging
from collections import deque
from collections.abc import Callable, Mapping
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from typing import TYPE_CHECKING, Any, Literal

from sqlalchemy.exc import IntegrityError

from maa_api.agent.harness.context import (
    RunContext,
    RunOutcome,
    ToolCall,
    ToolOutcome,
)
from maa_api.agent.harness.middleware import CallNext, Middleware
from maa_api.agent.registry import ToolRegistry
from maa_api.db.models import DeviceLease, new_uuid, utcnow
from maa_api.db.repositories.lease import DeviceLeaseRepository
from maa_api.db.repositories.ticket import TicketRepository
from maa_api.domain.enums import InboxKind
from maa_api.domain.errors import AppError, ErrorCode
from maa_api.settings import get_settings

if TYPE_CHECKING:  # 只为类型标注：避免服务模块反向依赖工单服务的导入链
    from maa_api.agent.tickets.service import TicketEnded
    from maa_api.settings import Settings

logger = logging.getLogger(__name__)

#: 单设备部署下恒为 ``default``（规格 §2：不做多设备）。
LEASE_DEVICE_ID = "default"

#: 人工接管设备的五种原因；每种对应 05 §2.3 收件箱 system 项的一句提示。
PreemptReason = Literal[
    "manual_pipeline",
    "rest_atomic",
    "rest_tool",
    "stop_pipeline",
    "manual_release",
]

#: 租约消失的原因；``released`` 是持有者自己放的，其余由服务判定。
ReleaseReason = Literal[
    "run_ended",
    "waiting_human",
    "released",
    "ticket_ended",
    "expired",
    "orphaned",
]

PREEMPT_TEXT: Mapping[str, str] = {
    "manual_pipeline": "用户手动提交了流水线",
    "rest_atomic": "用户直接操作了设备",
    "rest_tool": "用户通过开放 API 调用了操作设备的工具",
    "stop_pipeline": "用户停止了正在运行的流水线",
    "manual_release": "用户在网页上释放了设备",
}


@dataclass(frozen=True, slots=True)
class LeaseHolder:
    """一次取租约请求的主体；工单是身份，运行只用来记录释放责任。

    有工单时「同一工单（含子代理）重入」，所以 :meth:`owns` 只比 ``ticket_id``；
    没有工单的运行（例如总控或后台运行）才退化到比 ``run_id``。
    """

    ticket_id: str | None
    #: Worker 的根运行（子代理用父 Worker 的根运行）。
    run_id: str
    agent: str

    @property
    def key(self) -> str:
        return f"ticket:{self.ticket_id}" if self.ticket_id else f"run:{self.run_id}"

    def owns(self, row: DeviceLease) -> bool:
        if self.ticket_id:
            return row.ticket_id == self.ticket_id
        return self.run_id is not None and row.run_id == self.run_id


@dataclass(frozen=True, slots=True)
class ClaimGate:
    """队列领取闸门：租约有效期内只允许这些会话的流水线（及手动提交）出队。"""

    lease_id: str
    allowed_session_ids: frozenset[str]


def holder_for(ctx: RunContext) -> LeaseHolder:
    """从一轮运行取租约主体：工单取 ``ctx.ticket_id``，运行取根运行 id。"""
    return LeaseHolder(
        ticket_id=ctx.ticket_id,
        run_id=ctx.root_run_id,
        agent=ctx.spec.name,
    )


@dataclass(frozen=True, slots=True)
class _Change:
    """一次待广播的变化；在锁内产生，出锁后统一发。"""

    change: Literal["acquired", "released", "preempted", "expired"]
    reason: str | None
    row: DeviceLease


def _key_of(row: DeviceLease) -> str:
    """租约行的持有者身份，与 :attr:`LeaseHolder.key` 同构。"""
    return f"ticket:{row.ticket_id}" if row.ticket_id else f"run:{row.run_id}"


def _wire_time(value: datetime | None) -> str | None:
    """库内是 naive UTC；协议里带 ``Z``（与确认的 wire 同口径）。"""
    if value is None:
        return None
    return value.replace(tzinfo=UTC).isoformat().replace("+00:00", "Z")


class DeviceLeaseService:
    """容量 1 的设备租约：排队、TTL、续期、释放、过期、抢占与领取闸门。"""

    def __init__(
        self,
        session_factory: Any,
        *,
        boot_id: str,
        thread_router: Any = None,
        broadcast: Callable[[str, dict[str, Any]], None] | None = None,
        settings_provider: Callable[[], Settings] = get_settings,
        on_released: Callable[[], None] | None = None,
        clock: Callable[[], datetime] = utcnow,
        poll_seconds: float = 1.0,
    ) -> None:
        self._session_factory = session_factory
        self._boot_id = boot_id
        self._thread_router = thread_router
        self._broadcast = broadcast or (lambda _message_type, _data: None)
        self._settings_provider = settings_provider
        self._on_released = on_released
        self._clock = clock
        self._poll_seconds = max(float(poll_seconds), 0.01)
        self._cond = asyncio.Condition()
        #: 排队记号，先进先出；只有队首能在「没有行」时插入。
        self._queue: deque[object] = deque()
        #: 被抢占的持有者记号（一次性）：下一次 acquire 弹出来报 PREEMPTED。
        self._preempted: dict[str, PreemptReason] = {}
        self._scan_task: asyncio.Task[None] | None = None

    @property
    def waiters(self) -> int:
        """正在排队（含刚进锁判断中）的取租约请求数。"""
        return len(self._queue)

    # ---------------------------------------------------------------- 生命周期

    async def start(self, *, scan_interval_seconds: float = 10.0) -> None:
        """起过期/孤儿扫描任务；重复调用幂等（写法照确认服务的过期扫描）。"""
        if self._scan_task is not None and not self._scan_task.done():
            return
        self._scan_task = asyncio.create_task(
            self._scan_loop(max(float(scan_interval_seconds), 0.05)),
            name="agent-device-lease-expiry-scan",
        )

    async def close(self) -> None:
        """停扫描任务；不删行（重启清理由恢复流程负责）。"""
        if self._scan_task is None:
            return
        self._scan_task.cancel()
        await asyncio.gather(self._scan_task, return_exceptions=True)
        self._scan_task = None

    async def _scan_loop(self, interval: float) -> None:
        while True:
            await asyncio.sleep(interval)
            try:
                await self.expire_due()
            except asyncio.CancelledError:
                raise
            except Exception:  # noqa: BLE001 - 单轮扫描失败不能杀掉后台任务
                logger.exception("Agent 设备租约扫描失败")

    # ---------------------------------------------------------------- 取/续/放

    async def acquire(
        self,
        holder: LeaseHolder,
        *,
        purpose: str,
        explicit: bool = False,
        wait_seconds: float | None = None,
    ) -> DeviceLease:
        """取租约；同一工单重入即续期，队首才能插入，等待超时报 ``DEVICE_LEASE_BUSY``。"""
        if holder.key in self._preempted:
            # 被抢占后的第一次取租约：一次性记号，弹出来就恢复可再取。
            reason = self._preempted.pop(holder.key)
            raise AppError(
                ErrorCode.DEVICE_LEASE_PREEMPTED,
                "设备已被用户接管，先停下设备操作",
                {"reason": reason},
            )

        settings = self._settings_provider().lease
        wait = float(settings.wait_seconds if wait_seconds is None else wait_seconds)
        loop = asyncio.get_running_loop()
        deadline = loop.time() + max(wait, 0.0)
        token = object()
        changes: list[_Change] = []
        acquired: DeviceLease | None = None
        failure: AppError | None = None

        async with self._cond:
            self._queue.append(token)
            try:
                while True:
                    row, expired = await self._load_fresh(self._clock())
                    if expired is not None:
                        changes.append(_Change("expired", "expired", expired))
                    if row is not None and holder.owns(row):
                        # 同一工单（含子代理）重入：续期，不排队、不换行。
                        extended = await self._extend(
                            row,
                            settings.ttl_seconds,
                            explicit=explicit,
                            purpose=purpose,
                        )
                        if extended is not None:
                            acquired = extended
                            break
                        continue
                    if row is None and self._queue[0] is token:
                        inserted = await self._try_insert(
                            holder, purpose, explicit, settings.ttl_seconds
                        )
                        if inserted is not None:
                            acquired = inserted
                            changes.append(_Change("acquired", None, inserted))
                            break
                    remaining = deadline - loop.time()
                    if remaining <= 0:
                        failure = self._busy(holder, row, wait)
                        break
                    try:
                        await asyncio.wait_for(
                            self._cond.wait(), timeout=min(remaining, self._poll_seconds)
                        )
                    except TimeoutError:
                        # 定时重读：过期的行与外部删除都能被发现（§4.5.1）。
                        pass
            finally:
                self._queue.remove(token)
                self._cond.notify_all()

        await self._emit_changes(changes)
        if failure is not None:
            raise failure
        assert acquired is not None  # 循环只会在两种赋值后 break
        return acquired

    async def renew(self, holder: LeaseHolder) -> DeviceLease | None:
        """持有者续期；不是持有者（或已过期）返回 ``None``，不广播。"""
        settings = self._settings_provider().lease
        now = self._clock()
        async with self._cond:
            row = await self._get_row()
            if row is None or not holder.owns(row) or row.expires_at <= now:
                return None
            return await self._extend(
                row, settings.ttl_seconds, explicit=False, purpose=row.purpose
            )

    async def release(self, holder: LeaseHolder) -> bool:
        """持有者主动释放；非持有者返回 ``False`` 且不动那一行。"""
        changes: list[_Change] = []
        released: DeviceLease | None = None
        async with self._cond:
            row, expired = await self._load_fresh(self._clock())
            if expired is not None:
                changes.append(_Change("expired", "expired", expired))
            if row is not None and holder.owns(row):
                if await self._delete_rows([row]):
                    released = row
                    self._cond.notify_all()
        await self._emit_changes(changes)
        if released is None:
            return False
        self._notify_released()
        await self._emit_change("released", "released", released)
        return True

    async def release_run(
        self, run_id: str, *, reason: ReleaseReason = "run_ended"
    ) -> bool:
        """根运行结束时释放它的隐式租约；显式租约跨运行保留。"""
        removed = await self._release_where(
            lambda row: row.run_id == run_id and not row.explicit,
            reason=reason,
        )
        return removed is not None

    async def release_ticket(self, ticket_id: str) -> bool:
        """工单结束时释放该工单的租约，不论显式隐式。"""
        removed = await self._release_where(
            lambda row: row.ticket_id == ticket_id,
            reason="ticket_ended",
        )
        return removed is not None

    async def preempt(
        self, reason: PreemptReason, *, detail: str | None = None
    ) -> DeviceLease | None:
        """人工接管设备：删行、给持有者留一次性记号、广播并投递工单收件箱。

        没有租约时返回 ``None``，不广播、不写收件箱（§4.5.3）。
        """
        row: DeviceLease | None = None
        expired_row: DeviceLease | None = None
        async with self._cond:
            row, expired_row = await self._load_fresh(self._clock())
            if row is not None:
                if await self._delete_rows([row]):
                    self._preempted[_key_of(row)] = reason
                    self._cond.notify_all()
                else:
                    row = None

        if expired_row is not None:
            await self._emit_change("expired", "expired", expired_row)
        if row is None:
            return None

        self._notify_released()
        await self._emit_change("preempted", reason, row)
        if row.ticket_id and self._thread_router is not None:
            await self._thread_router.publish(
                f"ticket:{row.ticket_id}",
                kind=InboxKind.SYSTEM,
                ref_id=row.lease_id,
                payload={
                    "event": "lease_preempted",
                    "reason": reason,
                    "detail": detail,
                    "text": (
                        f"设备被用户接管：{PREEMPT_TEXT[reason]}。"
                        "不要再操作设备；需要继续时用 report 汇报 waiting，等用户回复。"
                    ),
                },
            )
        return row

    def clear_preempted(self, key: str) -> None:
        """清掉某个持有者的抢占记号（运行或工单结束时用）。"""
        self._preempted.pop(key, None)

    # ---------------------------------------------------------------- 清理与读取

    async def current(self) -> DeviceLease | None:
        """当前租约；顺手删掉已过期的行并广播 ``expired``。"""
        changes: list[_Change] = []
        async with self._cond:
            row, expired = await self._load_fresh(self._clock())
            if expired is not None:
                changes.append(_Change("expired", "expired", expired))
        await self._emit_changes(changes)
        return row

    async def claim_gate(self) -> ClaimGate | None:
        """没有（有效）租约返回 ``None``；有工单按工单会话，没工单按根运行会话。"""
        now = self._clock()
        async with self._session_factory() as session:
            repo = DeviceLeaseRepository(session)
            row = await repo.get(LEASE_DEVICE_ID)
            if row is None or row.expires_at <= now:
                return None
            if row.ticket_id:
                allowed = await repo.session_ids_for_ticket(row.ticket_id)
            else:
                allowed = set()
                if row.run_id:
                    session_id = await repo.session_id_for_run(row.run_id)
                    if session_id is not None:
                        allowed.add(session_id)
            return ClaimGate(
                lease_id=row.lease_id,
                allowed_session_ids=frozenset(allowed),
            )

    async def expire_due(self) -> int:
        """删到期的与孤儿隐式租约，返回删除数；每条删行各广播一次原因。"""
        now = self._clock()
        changes: list[_Change] = []
        async with self._cond:
            async with self._session_factory() as session:
                repo = DeviceLeaseRepository(session)
                candidates: dict[str, tuple[DeviceLease, str]] = {}
                for row in await repo.list_expired(now):
                    candidates[row.lease_id] = (row, "expired")
                for row in await repo.list_orphaned_implicit():
                    # 已过期的优先按 expired 报，不重复删两次。
                    candidates.setdefault(row.lease_id, (row, "orphaned"))
                for lease_id, (row, reason) in candidates.items():
                    if await repo.delete_lease(lease_id):
                        changes.append(
                            _Change("expired" if reason == "expired" else "released", reason, row)
                        )
                if changes:
                    await session.commit()
            if changes:
                self._cond.notify_all()

        await self._emit_changes(changes)
        if changes and not any(change.change == "expired" for change in changes):
            # 全是孤儿时也要唤醒；含 expired 时 ``_emit_changes`` 已经唤过一次。
            self._notify_released()
        return len(changes)

    async def on_ticket_ended(self, event: TicketEnded) -> None:
        """M69 的工单结束监听者：删该工单租约并清掉抢占记号。"""
        await self.release_ticket(event.ticket_id)
        self.clear_preempted(f"ticket:{event.ticket_id}")

    async def wire(self, row: DeviceLease | None) -> dict[str, Any] | None:
        """``DeviceLeaseOut`` 形状（§4.3）；工单标题现查，没有工单时为 ``None``。"""
        if row is None:
            return None
        title: str | None = None
        if row.ticket_id:
            async with self._session_factory() as session:
                titles = await TicketRepository(session).titles([row.ticket_id])
            title = titles.get(row.ticket_id)
        return {
            "device_id": row.device_id,
            "lease_id": row.lease_id,
            "ticket_id": row.ticket_id,
            "ticket_title": title,
            "run_id": row.run_id,
            "holder_agent": row.holder_agent,
            "purpose": row.purpose,
            "explicit": bool(row.explicit),
            "acquired_at": _wire_time(row.acquired_at),
            "expires_at": _wire_time(row.expires_at),
            "heartbeat_at": _wire_time(row.heartbeat_at),
        }

    # ---------------------------------------------------------------- 内部读写

    async def _get_row(self) -> DeviceLease | None:
        """只读地取当前行（不做过期清理）。"""
        async with self._session_factory() as session:
            return await DeviceLeaseRepository(session).get(LEASE_DEVICE_ID)

    async def _load_fresh(
        self, now: datetime
    ) -> tuple[DeviceLease | None, DeviceLease | None]:
        """读当前行；已过期的删掉并把旧行交回调用方（出锁后广播）。"""
        async with self._session_factory() as session:
            repo = DeviceLeaseRepository(session)
            row = await repo.get(LEASE_DEVICE_ID)
            if row is None:
                return None, None
            if row.expires_at <= now:
                deleted = await repo.delete_lease(row.lease_id)
                await session.commit()
                return None, (row if deleted else None)
            return row, None

    async def _try_insert(
        self, holder: LeaseHolder, purpose: str, explicit: bool, ttl_seconds: int
    ) -> DeviceLease | None:
        """插入新租约；主键冲突说明别人先拿到，返回 ``None`` 让调用方继续等。"""
        now = self._clock()
        lease = DeviceLease(
            device_id=LEASE_DEVICE_ID,
            lease_id=new_uuid(),
            ticket_id=holder.ticket_id,
            run_id=holder.run_id,
            holder_agent=holder.agent,
            purpose=purpose[:120],
            explicit=explicit,
            acquired_at=now,
            heartbeat_at=now,
            expires_at=now + timedelta(seconds=int(ttl_seconds)),
            boot_id=self._boot_id,
        )
        async with self._session_factory() as session:
            repo = DeviceLeaseRepository(session)
            try:
                await repo.insert(lease)
                await session.commit()
            except IntegrityError:
                await session.rollback()
                return None
        return lease

    async def _extend(
        self,
        row: DeviceLease,
        ttl_seconds: int,
        *,
        explicit: bool,
        purpose: str,
    ) -> DeviceLease | None:
        """把过期时间与心跳推到 ``now + ttl``；显式标记只升不降。"""
        now = self._clock()
        expires_at = now + timedelta(seconds=int(ttl_seconds))
        explicit_after = bool(row.explicit or explicit)
        # 重入的隐式调用不该覆盖原用途；显式 hold 时才用新 purpose（§4.5.1）。
        purpose_after = purpose[:120] if explicit else row.purpose
        async with self._session_factory() as session:
            repo = DeviceLeaseRepository(session)
            changed = await repo.update_expiry(
                row.lease_id,
                expires_at=expires_at,
                heartbeat_at=now,
                explicit=explicit_after,
                purpose=purpose_after,
            )
            if not changed:
                return None
            await session.commit()
        row.expires_at = expires_at
        row.heartbeat_at = now
        row.explicit = explicit_after
        row.purpose = purpose_after
        return row

    async def _delete_rows(self, rows: list[DeviceLease]) -> int:
        """在一个事务里删掉这些行，返回删除数。"""
        removed = 0
        async with self._session_factory() as session:
            repo = DeviceLeaseRepository(session)
            for row in rows:
                if await repo.delete_lease(row.lease_id):
                    removed += 1
            if removed:
                await session.commit()
        return removed

    async def _release_where(
        self, predicate: Callable[[DeviceLease], bool], *, reason: ReleaseReason
    ) -> DeviceLease | None:
        """``release_run`` / ``release_ticket`` 的共同骨架。"""
        changes: list[_Change] = []
        released: DeviceLease | None = None
        async with self._cond:
            row, expired = await self._load_fresh(self._clock())
            if expired is not None:
                changes.append(_Change("expired", "expired", expired))
            if row is not None and predicate(row):
                if await self._delete_rows([row]):
                    released = row
                    self._cond.notify_all()
        await self._emit_changes(changes)
        if released is not None:
            self._notify_released()
            await self._emit_change("released", reason, released)
        return released

    def _busy(
        self, holder: LeaseHolder, row: DeviceLease | None, wait: float
    ) -> AppError:
        """``DEVICE_LEASE_BUSY``；details 带当前持有者，供代理写明白在等谁。"""
        return AppError(
            ErrorCode.DEVICE_LEASE_BUSY,
            "设备正被其他工单使用，稍后再试",
            {
                "holder_ticket_id": row.ticket_id if row is not None else None,
                "holder_agent": row.holder_agent if row is not None else None,
                "expires_at": _wire_time(row.expires_at) if row is not None else None,
                "waited_seconds": round(max(wait, 0.0), 3),
            },
        )

    # ---------------------------------------------------------------- 回调与广播

    async def _emit_changes(self, changes: list[_Change]) -> None:
        for change in changes:
            await self._emit_change(change.change, change.reason, change.row)
        # 过期也是一次「设备空出来了」，队列消费者要知道（§4.5.4）。
        if any(change.change == "expired" for change in changes):
            self._notify_released()

    async def _emit_change(
        self, change: str, reason: str | None, row: DeviceLease
    ) -> None:
        """广播 WS ``agent_event`` 的 ``lease_changed``（§4.3）；失败只记日志。"""
        try:
            lease = await self.wire(row)
            self._broadcast(
                "agent_event",
                {
                    "session_id": None,
                    "event": "lease_changed",
                    "payload": {
                        "change": change,
                        "reason": reason,
                        "held": change == "acquired",
                        "lease": lease,
                    },
                },
            )
        except Exception:  # noqa: BLE001 - 广播失败不能回滚已经完成的租约操作
            logger.exception("设备租约状态广播失败：%s", change)

    def _notify_released(self) -> None:
        """唤醒队列消费者（``PipelineRunner.wake``）；异常只记日志。"""
        if self._on_released is None:
            return
        try:
            self._on_released()
        except Exception:  # noqa: BLE001 - 唤醒失败不应影响租约本身
            logger.exception("设备租约释放回调失败")


class LeaseMiddleware(Middleware):
    """写设备工具的执行前取租约（M71 §4.5.5，05 §5.2 第 15 位）。

    链上位于 ``ClarificationMiddleware`` 之前、``PolicyMiddleware`` 外侧：它不是
    执行器，只往 ``ctx.state["policy.before_execute"]`` 登记本调用的回调，由
    ``PolicyMiddleware`` 在确认通过之后、执行之前调用。因此等确认、等提问期间
    不占设备（与 M70 ``DeviceGateMiddleware`` 的「先取门再确认」相反）。
    """

    name = "lease"
    applies_to = frozenset({"worker", "subagent"})

    def __init__(
        self, lease_service: DeviceLeaseService | None, registry: ToolRegistry
    ) -> None:
        self.lease_service = lease_service
        self.registry = registry

    async def wrap_tool(
        self, ctx: RunContext, call: ToolCall, call_next: CallNext
    ) -> ToolOutcome:
        try:
            definition = self.registry.get(call.name)
        except AppError:
            # 未知工具交给内层按 v2 口径报 TOOL_NOT_FOUND。
            return await call_next(call)
        if definition.device != "exclusive":
            return await call_next(call)

        service = self.lease_service
        holder = holder_for(ctx)
        hooks = ctx.state.setdefault("policy.before_execute", {})
        #: 钩子取到租约后解析出 TTL；续期任务据此算间隔（不 sleep 真实 TTL）。
        ttl_seconds: asyncio.Future[float] = (
            asyncio.get_running_loop().create_future()
        )

        async def acquire_hook(audit_id: int) -> None:
            if service is None:
                raise AppError(
                    ErrorCode.DEVICE_LEASE_BUSY,
                    "设备租约服务尚未启动，请稍后重试",
                    {
                        "reason": "lease_service_unavailable",
                        "audit_id": audit_id,
                    },
                )
            lease = await service.acquire(holder, purpose=call.name)
            if not ttl_seconds.done():
                ttl_seconds.set_result(
                    (lease.expires_at - lease.heartbeat_at).total_seconds()
                )

        hooks.setdefault(call.id, []).append(acquire_hook)
        renewal = (
            asyncio.create_task(
                self._renew_loop(holder, ttl_seconds),
                name=f"agent-lease-renew-{call.id}",
            )
            if service is not None
            else None
        )
        try:
            outcome = await call_next(call)
        finally:
            if renewal is not None:
                renewal.cancel()
                await asyncio.gather(renewal, return_exceptions=True)
            current = ctx.state.get("policy.before_execute")
            if isinstance(current, dict):
                current.pop(call.id, None)
        if (
            outcome.error is not None
            and outcome.error.get("code") == ErrorCode.DEVICE_LEASE_PREEMPTED.value
        ):
            return dataclasses.replace(
                outcome, stop_run=True, stop_reason="lease_preempted"
            )
        return outcome

    async def _renew_loop(
        self, holder: LeaseHolder, ttl_seconds: asyncio.Future[float]
    ) -> None:
        """取到租约后每 ``max(ttl/3, 5)`` 秒续期；异常只记日志。"""
        ttl = await ttl_seconds
        interval = max(ttl / 3.0, 5.0)
        assert self.lease_service is not None  # 只有服务存在时才会起这个任务
        while True:
            await asyncio.sleep(interval)
            try:
                await self.lease_service.renew(holder)
            except asyncio.CancelledError:
                raise
            except Exception:  # noqa: BLE001 - 续期失败留待过期扫描兜底
                logger.exception("设备租约续期失败：%s", holder.key)

    async def on_run_end(self, ctx: RunContext, outcome: RunOutcome) -> None:
        """根运行结束时释放隐式租约并清掉抢占记号；子代理结束不释放。"""
        if ctx.run_id != ctx.root_run_id:
            return
        service = self.lease_service
        if service is None:
            return
        try:
            await service.release_run(ctx.root_run_id, reason="run_ended")
        except Exception:  # noqa: BLE001 - 收尾释放失败不能把一轮结束变成异常
            logger.exception("根运行结束时释放设备租约失败")
        service.clear_preempted(holder_for(ctx).key)
