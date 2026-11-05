"""发件箱：入队、领取、发送与状态迁移（v3 specs/m85 §4.5、docs/v3/03 §11.7）。

口径（spec §4.5「发件箱」）：

- 每 2 秒一批，最多 20 条：取 ``status in (pending, failed)`` 且
  ``next_attempt_at <= now`` 的行，领取时把状态改为 ``sending``。
- 成功 → ``sent`` / ``sent_at`` / ``receipt``；``OutboxRetry`` → ``attempts+1``、
  ``failed``、``next_attempt_at = now + min(3600, 2**attempts)`` 秒；
  ``attempts`` 达到 8 次 → ``dead``；``OutboxFatal`` → ``dead`` / ``last_error``。
- 进程启动时把遗留的 ``sending`` 改回 ``pending``（:meth:`OutboxWorker.reset_sending`，
  由 :meth:`OutboxWorker.run_forever` 在循环前调用一次）。
- ``dedupe_key`` 唯一：enqueue 冲突时返回旧行、不重复插入。enqueue **不提交**，
  与业务状态共用调用方的同一个事务（spec §8 风险与回滚）。

适配器协议在 G2 移到 :mod:`maa_api.agent.channels.adapter`（``platform`` +
``start`` / ``stop`` / ``send``）；这里仍从原位置再导出 ``ChannelAdapter``，
G1 的 ``adapter_for`` 注入点与测试不变。失败抛
:class:`~maa_api.agent.channels.errors.OutboxRetry` 或 ``OutboxFatal``。
"""

from __future__ import annotations

import asyncio
import dataclasses
import logging
from collections.abc import Awaitable, Callable, Mapping
from dataclasses import dataclass
from datetime import datetime, timedelta
from typing import Any

from sqlalchemy import select, update
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from maa_api.agent.channels.adapter import ChannelAdapter
from maa_api.agent.channels.errors import OutboxFatal, OutboxRetry
from maa_api.db.models import Outbox, utcnow
from maa_api.domain.enums import OutboxChannel, OutboxKind, OutboxStatus

__all__ = [
    "BATCH_SIZE",
    "BACKOFF_CAP_SECONDS",
    "MAX_ATTEMPTS",
    "POLL_INTERVAL_SECONDS",
    "ChannelAdapter",
    "OutboxWorker",
    "WebPushOutbox",
    "WebPushSender",
]

logger = logging.getLogger(__name__)

#: 轮询间隔与批大小写死在 worker，不进设置（spec §4.4）。
POLL_INTERVAL_SECONDS = 2.0
BATCH_SIZE = 20
#: ``attempts`` 达到这个数就进死信（spec §4.5）。
MAX_ATTEMPTS = 8
#: 退避上限：``min(3600, 2**attempts)`` 秒（spec §4.5）。
BACKOFF_CAP_SECONDS = 3600

#: 可领取的状态；``sending`` 不在内，避免两个 worker 重复领取同一行。
_DUE_STATUSES = (OutboxStatus.PENDING.value, OutboxStatus.FAILED.value)


#: 按 ``outbox.channel`` 取适配器；未知渠道返回 ``None`` 时按致命失败处理。
AdapterFactory = Callable[[str], ChannelAdapter | None]
#: 不经过平台适配器的出口（目前只有 Web Push）：收完整 payload，返回回执。
WebPushSender = Callable[[dict[str, Any]], Awaitable[Any]]


@dataclass(frozen=True, slots=True)
class _Claim:
    """领取到的一条待发行；发送期间不再持有 ORM 对象。"""

    id: int
    channel: str
    target: str
    payload: dict[str, Any]


def _error_text(exc: BaseException) -> str:
    """失败原因入库：异常消息为空时退回类名，便于 outbox 列表排查。"""
    detail = str(exc).strip()
    return detail or type(exc).__name__


def _receipt_dict(value: Any) -> dict[str, Any]:
    """把发送器返回的回执收敛成 JSON 列能存的字典。"""
    if isinstance(value, Mapping):
        return dict(value)
    if dataclasses.is_dataclass(value) and not isinstance(value, type):
        return dataclasses.asdict(value)
    if value is None:
        return {}
    return {"result": str(value)}


class WebPushOutbox:
    """Web Push 的发件箱入口（M85 §4.5「发件箱」）。

    只负责把 ``payload`` 写进 ``outbox``（``channel=webpush``、``target=all``）
    并提交；真正的 ``WebPushService.broadcast(payload)`` 由
    :class:`OutboxWorker` 注册的发送器执行。业务方不直接触达推送服务。
    """

    def __init__(
        self,
        worker: OutboxWorker,
        session_factory: async_sessionmaker[AsyncSession],
    ) -> None:
        self._worker = worker
        self._session_factory = session_factory

    async def enqueue(
        self,
        payload: dict[str, Any],
        *,
        kind: OutboxKind | str,
        dedupe_key: str,
        ref_ticket_id: str | None = None,
        ref_run_id: str | None = None,
        now: datetime | None = None,
    ) -> Outbox:
        """写一条待发 Web Push 并提交；``dedupe_key`` 已存在时返回旧行。"""
        async with self._session_factory() as session:
            row = await self._worker.enqueue(
                session,
                channel=OutboxChannel.WEBPUSH,
                target="all",
                kind=kind,
                payload=dict(payload),
                dedupe_key=str(dedupe_key),
                ref_ticket_id=ref_ticket_id,
                ref_run_id=ref_run_id,
                now=now,
            )
            await session.commit()
        return row


class OutboxWorker:
    """发件箱的发件循环；本身不创建引擎，会话工厂由调用方注入。"""

    def __init__(
        self,
        session_factory: async_sessionmaker[AsyncSession],
        adapter_for: AdapterFactory,
        *,
        batch_size: int = BATCH_SIZE,
        max_attempts: int = MAX_ATTEMPTS,
        interval: float = POLL_INTERVAL_SECONDS,
        backoff_cap: int = BACKOFF_CAP_SECONDS,
        senders: Mapping[str, WebPushSender] | None = None,
        clock: Callable[[], datetime] = utcnow,
    ) -> None:
        self._session_factory = session_factory
        self._adapter_for = adapter_for
        #: ``outbox.channel`` → 非适配器发送器；目前只有 ``webpush``。
        self._senders: dict[str, WebPushSender] = dict(senders or {})
        self.batch_size = batch_size
        self.max_attempts = max_attempts
        self.interval = interval
        self.backoff_cap = backoff_cap
        self._clock = clock

    def register_sender(
        self, channel: OutboxChannel | str, sender: WebPushSender
    ) -> None:
        """注册非适配器出口（G4 的 Web Push）；重复注册以后者为准。"""
        self._senders[str(channel)] = sender

    # ------------------------------------------------------------------
    # 入队
    # ------------------------------------------------------------------
    async def enqueue(
        self,
        session: AsyncSession,
        *,
        channel: OutboxChannel | str,
        target: str,
        kind: OutboxKind | str,
        payload: dict[str, Any],
        dedupe_key: str,
        ref_run_id: str | None = None,
        ref_ticket_id: str | None = None,
        next_attempt_at: datetime | None = None,
        now: datetime | None = None,
    ) -> Outbox:
        """在调用方的会话里入队；**不提交**，由业务事务一起提交。

        ``dedupe_key`` 已存在时直接返回旧行（不更新、不重复插入）。并发下第二个
        插入会撞唯一约束，捕获 ``IntegrityError``（保存点内）后同样返回旧行。
        """
        key = str(dedupe_key)
        existing = await session.scalar(
            select(Outbox).where(Outbox.dedupe_key == key)
        )
        if existing is not None:
            return existing

        moment = now or self._clock()
        row = Outbox(
            channel=str(channel),
            target=target,
            kind=str(kind),
            payload=dict(payload),
            dedupe_key=key,
            status=OutboxStatus.PENDING,
            attempts=0,
            next_attempt_at=next_attempt_at or moment,
            ref_run_id=ref_run_id,
            ref_ticket_id=ref_ticket_id,
            created_at=moment,
        )
        try:
            # 保存点：撞唯一约束只回滚这次插入，不毒化调用方的业务事务。
            async with session.begin_nested():
                session.add(row)
                await session.flush()
        except IntegrityError:
            existing = await session.scalar(
                select(Outbox).where(Outbox.dedupe_key == key)
            )
            if existing is None:  # pragma: no cover - 约束之外的完整性错误
                raise
            return existing
        return row

    # ------------------------------------------------------------------
    # 启动恢复
    # ------------------------------------------------------------------
    async def reset_sending(self) -> int:
        """把崩溃时遗留的 ``sending`` 改回 ``pending``，返回处理行数。"""
        async with self._session_factory() as session:
            result = await session.execute(
                update(Outbox)
                .where(Outbox.status == OutboxStatus.SENDING.value)
                .values(status=OutboxStatus.PENDING.value)
            )
            await session.commit()
        count = int(result.rowcount or 0)
        if count:
            logger.info("发件箱启动恢复：%s 条 sending 改回 pending", count)
        return count

    # ------------------------------------------------------------------
    # 发件循环
    # ------------------------------------------------------------------
    async def run_once(self, *, now: datetime | None = None) -> int:
        """领取并处理一批，返回本次处理的行数（最多 ``batch_size``）。"""
        moment = now or self._clock()
        async with self._session_factory() as session:
            claims = await self._claim(session, moment)
        for claim in claims:
            await self._deliver(claim, moment)
        return len(claims)

    async def run_forever(self, stop: asyncio.Event | None = None) -> None:
        """先恢复遗留 ``sending``，再按 ``interval`` 轮询；异常不终止循环。"""
        await self.reset_sending()
        while stop is None or not stop.is_set():
            try:
                await self.run_once()
            except asyncio.CancelledError:
                raise
            except Exception:  # pragma: no cover - 数据库层异常，保活后继续
                logger.exception("发件箱轮询失败，%s 秒后重试", self.interval)
            if stop is None:
                await asyncio.sleep(self.interval)
                continue
            try:
                await asyncio.wait_for(stop.wait(), timeout=self.interval)
            except TimeoutError:
                continue

    # ------------------------------------------------------------------
    # 内部：领取与发送
    # ------------------------------------------------------------------
    async def _claim(
        self, session: AsyncSession, now: datetime
    ) -> list[_Claim]:
        """原子领取：先选出到期行，再用带状态条件的 ``UPDATE ... RETURNING`` 抢占。"""
        candidate_ids = list(
            (
                await session.scalars(
                    select(Outbox.id)
                    .where(
                        Outbox.status.in_(_DUE_STATUSES),
                        Outbox.next_attempt_at <= now,
                    )
                    .order_by(Outbox.next_attempt_at, Outbox.id)
                    .limit(self.batch_size)
                )
            ).all()
        )
        if not candidate_ids:
            await session.rollback()
            return []

        claimed_ids = list(
            (
                await session.scalars(
                    update(Outbox)
                    .where(
                        Outbox.id.in_(candidate_ids),
                        Outbox.status.in_(_DUE_STATUSES),
                    )
                    .values(status=OutboxStatus.SENDING.value)
                    .returning(Outbox.id)
                )
            ).all()
        )
        if not claimed_ids:
            # 全被另一个 worker 先领走：这批不重复发送。
            await session.rollback()
            return []

        rows = (
            await session.scalars(select(Outbox).where(Outbox.id.in_(claimed_ids)))
        ).all()
        claims = [
            _Claim(
                id=int(row.id),
                channel=row.channel,
                target=row.target,
                payload=dict(row.payload or {}),
            )
            for row in rows
        ]
        await session.commit()
        return claims

    async def _deliver(self, claim: _Claim, now: datetime) -> None:
        """发送一条并落库结果；异常分类只由 ``OutboxRetry`` / ``OutboxFatal`` 决定。"""
        try:
            if claim.channel == OutboxChannel.WEBPUSH.value:
                # Web Push 的发送器收完整 payload，不经过平台适配器的 text 形状。
                sender = self._senders.get(claim.channel)
                if sender is None:
                    raise OutboxFatal("没有 webpush 的发送器")
                raw = await sender(dict(claim.payload))
                receipt = _receipt_dict(raw)
            else:
                adapter = self._adapter_for(claim.channel)
                if adapter is None:
                    raise OutboxFatal(f"没有 {claim.channel} 的适配器")
                text = claim.payload.get("text")
                if not isinstance(text, str) or not text:
                    raise OutboxFatal("outbox.payload.text 缺失或为空")
                receipt = await adapter.send(claim.target, text)
        except OutboxFatal as exc:
            await self._mark_dead(claim.id, _error_text(exc))
        except OutboxRetry as exc:
            await self._mark_retry(claim.id, _error_text(exc), now)
        except Exception as exc:  # noqa: BLE001 - 适配器漏包时按可重试处理，避免误死信
            logger.exception(
                "发件箱发送未分类异常，按可重试处理（id=%s channel=%s）",
                claim.id,
                claim.channel,
            )
            await self._mark_retry(claim.id, _error_text(exc), now)
        else:
            await self._mark_sent(claim.id, receipt, now)

    async def _mark_sent(
        self, outbox_id: int, receipt: dict[str, Any], now: datetime
    ) -> None:
        async with self._session_factory() as session:
            row = await session.get(Outbox, outbox_id)
            if row is None or row.status != OutboxStatus.SENDING.value:
                return
            row.status = OutboxStatus.SENT.value
            row.sent_at = now
            row.receipt = receipt
            row.last_error = None
            await session.commit()

    async def _mark_retry(self, outbox_id: int, error: str, now: datetime) -> None:
        async with self._session_factory() as session:
            row = await session.get(Outbox, outbox_id)
            if row is None or row.status != OutboxStatus.SENDING.value:
                return
            attempts = row.attempts + 1
            row.attempts = attempts
            row.last_error = error
            if attempts >= self.max_attempts:
                row.status = OutboxStatus.DEAD.value
            else:
                row.status = OutboxStatus.FAILED.value
                delay = min(self.backoff_cap, 2**attempts)
                row.next_attempt_at = now + timedelta(seconds=delay)
            await session.commit()

    async def _mark_dead(self, outbox_id: int, error: str) -> None:
        async with self._session_factory() as session:
            row = await session.get(Outbox, outbox_id)
            if row is None or row.status != OutboxStatus.SENDING.value:
                return
            row.status = OutboxStatus.DEAD.value
            row.last_error = error
            await session.commit()
