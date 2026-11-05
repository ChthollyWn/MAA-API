"""M85 T1：发件箱状态机（spec §4.5、§5 T1、§6）。

覆盖 spec §6 的四条（``test_retry_then_sent`` / ``test_fatal_is_dead`` /
``test_dedupe_key_unique`` / ``test_sending_reset_on_start``），另补退避到期才重试、
8 次进死信、批大小 20 与 enqueue 冲突返回旧行。

测试纪律与 ``tests/agent/test_memory_service.py`` 一致：同步测试函数 +
``asyncio.run(scenario())``，临时库用 ``tests/db/conftest.py`` 的
``create_all`` 夹具，适配器是文件内的 :class:`FakeAdapter`，不连外网、不碰
``resource/maa_api.db``。
"""

from __future__ import annotations

import asyncio
from datetime import datetime, timedelta
from typing import Any

from sqlalchemy import func, select

from maa_api.agent.channels.errors import OutboxFatal, OutboxRetry
from maa_api.agent.channels.outbox import BATCH_SIZE, MAX_ATTEMPTS, OutboxWorker
from maa_api.db.models import Outbox
from maa_api.domain.enums import OutboxChannel, OutboxKind, OutboxStatus
from tests.db.conftest import (  # noqa: F401  # pytest 从模块命名空间发现 fixture
    db_engine,
    db_path,
    db_session,
    db_session_factory,
)

NOW = datetime(2026, 11, 5, 4, 0, 0)
CHAT_ID = "chat-telegram-1"
TEXT = "刷完了，理智剩 42。"


class FakeAdapter:
    """按剧本抛异常或返回回执；剧本用完后退回成功回执。"""

    def __init__(self, outcomes: list[Any] | None = None) -> None:
        self.outcomes = list(outcomes or [])
        self.calls: list[tuple[str, str]] = []

    async def send(self, chat_id: str, text: str) -> dict[str, Any]:
        self.calls.append((chat_id, text))
        outcome = self.outcomes.pop(0) if self.outcomes else {"message_id": "auto"}
        if isinstance(outcome, BaseException):
            raise outcome
        return outcome


def _worker(factory, adapter: FakeAdapter, **kwargs) -> OutboxWorker:
    return OutboxWorker(factory, lambda _channel: adapter, **kwargs)


async def _enqueue(
    worker: OutboxWorker,
    factory,
    *,
    dedupe_key: str = "reply:run-1",
    payload: dict[str, Any] | None = None,
    now: datetime = NOW,
) -> int:
    """落一条待发行并提交，返回自增 id。"""
    async with factory() as session:
        row = await worker.enqueue(
            session,
            channel=OutboxChannel.IM_TELEGRAM,
            target=CHAT_ID,
            kind=OutboxKind.REPLY,
            payload=payload or {"text": TEXT},
            dedupe_key=dedupe_key,
            now=now,
        )
        await session.commit()
        return int(row.id)


async def _read(factory, outbox_id: int) -> dict[str, Any]:
    async with factory() as session:
        row = await session.get(Outbox, outbox_id)
        assert row is not None
        return {
            "status": row.status,
            "attempts": row.attempts,
            "next_attempt_at": row.next_attempt_at,
            "last_error": row.last_error,
            "sent_at": row.sent_at,
            "receipt": row.receipt,
            "payload": row.payload,
        }


async def _count(factory, status: OutboxStatus | None = None) -> int:
    async with factory() as session:
        stmt = select(func.count()).select_from(Outbox)
        if status is not None:
            stmt = stmt.where(Outbox.status == status.value)
        return int(await session.scalar(stmt) or 0)


# ---------------------------------------------------------------------------
# spec §6 四条
# ---------------------------------------------------------------------------
def test_retry_then_sent(db_session_factory):
    """先失败两次再成功：attempts 累加、退避到期才重试、成功后 sent。"""

    async def scenario() -> None:
        adapter = FakeAdapter(
            [
                OutboxRetry("network timeout"),
                OutboxRetry("429 too many requests"),
                {"message_id": "42"},
            ]
        )
        worker = _worker(db_session_factory, adapter)
        outbox_id = await _enqueue(worker, db_session_factory)

        # 第一次失败：attempts=1，退避 2 秒，状态 failed。
        assert await worker.run_once(now=NOW) == 1
        row = await _read(db_session_factory, outbox_id)
        assert row["status"] == OutboxStatus.FAILED.value
        assert row["attempts"] == 1
        assert row["next_attempt_at"] == NOW + timedelta(seconds=2)
        assert row["last_error"] == "network timeout"
        assert row["sent_at"] is None

        # 未到 next_attempt_at：不领取，也不调用适配器。
        assert await worker.run_once(now=NOW) == 0
        assert len(adapter.calls) == 1

        # 第二次失败：attempts=2，退避 2**2=4 秒。
        second_try = NOW + timedelta(seconds=2)
        assert await worker.run_once(now=second_try) == 1
        row = await _read(db_session_factory, outbox_id)
        assert row["status"] == OutboxStatus.FAILED.value
        assert row["attempts"] == 2
        assert row["next_attempt_at"] == second_try + timedelta(seconds=4)
        assert row["last_error"] == "429 too many requests"

        # 第三次成功：sent，attempts 保留 2，回执落库。
        third_try = second_try + timedelta(seconds=4)
        assert await worker.run_once(now=third_try) == 1
        row = await _read(db_session_factory, outbox_id)
        assert row["status"] == OutboxStatus.SENT.value
        assert row["attempts"] == 2
        assert row["sent_at"] == third_try
        assert row["receipt"] == {"message_id": "42"}
        assert row["last_error"] is None
        assert adapter.calls == [(CHAT_ID, TEXT)] * 3
        assert await _count(db_session_factory, OutboxStatus.SENT) == 1

    asyncio.run(scenario())


def test_fatal_is_dead(db_session_factory):
    """4xx 致命失败直接 dead，不再重试。"""

    async def scenario() -> None:
        adapter = FakeAdapter([OutboxFatal("400 bad request")])
        worker = _worker(db_session_factory, adapter)
        outbox_id = await _enqueue(worker, db_session_factory)

        assert await worker.run_once(now=NOW) == 1
        row = await _read(db_session_factory, outbox_id)
        assert row["status"] == OutboxStatus.DEAD.value
        assert row["last_error"] == "400 bad request"
        assert row["attempts"] == 0  # 致命失败不累计重试次数
        assert row["sent_at"] is None

        # 死信永远不再领取。
        assert await worker.run_once(now=NOW + timedelta(days=1)) == 0
        assert len(adapter.calls) == 1

    asyncio.run(scenario())


def test_dedupe_key_unique(db_session_factory):
    """第二次 enqueue 同一 dedupe_key 返回旧行，不插行也不改旧行。"""

    async def scenario() -> None:
        adapter = FakeAdapter([{"message_id": "1"}])
        worker = _worker(db_session_factory, adapter)
        first_id = await _enqueue(worker, db_session_factory, dedupe_key="reply:run-9")

        # 跨会话再入队：命中的是已提交的旧行。
        async with db_session_factory() as session:
            duplicate = await worker.enqueue(
                session,
                channel=OutboxChannel.IM_TELEGRAM,
                target="chat-other",
                kind=OutboxKind.REPLY,
                payload={"text": "不该覆盖旧行"},
                dedupe_key="reply:run-9",
                now=NOW + timedelta(seconds=30),
            )
            await session.commit()
        assert int(duplicate.id) == first_id

        assert await _count(db_session_factory) == 1
        row = await _read(db_session_factory, first_id)
        assert row["payload"] == {"text": TEXT}

        # 旧行照常发送，只发一次。
        assert await worker.run_once(now=NOW) == 1
        assert adapter.calls == [(CHAT_ID, TEXT)]
        assert await _count(db_session_factory, OutboxStatus.SENT) == 1

    asyncio.run(scenario())


def test_sending_reset_on_start(db_session_factory):
    """启动把遗留 sending 改回 pending，之后能正常续发；sent 不受影响。"""

    async def scenario() -> None:
        adapter = FakeAdapter([{"message_id": "resumed"}])
        worker = _worker(db_session_factory, adapter)
        stranded_id = await _enqueue(worker, db_session_factory, dedupe_key="reply:a")
        sent_id = await _enqueue(worker, db_session_factory, dedupe_key="reply:b")

        # 模拟进程在「领取后、发送前」崩溃，以及一条已经发完的行。
        async with db_session_factory() as session:
            stranded = await session.get(Outbox, stranded_id)
            assert stranded is not None
            stranded.status = OutboxStatus.SENDING.value
            finished = await session.get(Outbox, sent_id)
            assert finished is not None
            finished.status = OutboxStatus.SENT.value
            await session.commit()

        assert await worker.reset_sending() == 1
        row = await _read(db_session_factory, stranded_id)
        assert row["status"] == OutboxStatus.PENDING.value
        assert (await _read(db_session_factory, sent_id))["status"] == (
            OutboxStatus.SENT.value
        )

        assert await worker.run_once(now=NOW) == 1
        assert (await _read(db_session_factory, stranded_id))["status"] == (
            OutboxStatus.SENT.value
        )
        assert adapter.calls == [(CHAT_ID, TEXT)]

    asyncio.run(scenario())


# ---------------------------------------------------------------------------
# 补充：退避耗尽、批大小、领取状态
# ---------------------------------------------------------------------------
def test_retry_exhaustion_marks_dead(db_session_factory):
    """连续可重试失败到 attempts=8 转 dead，之后不再领取。"""

    async def scenario() -> None:
        adapter = FakeAdapter([OutboxRetry("503 unavailable")] * MAX_ATTEMPTS)
        worker = _worker(db_session_factory, adapter)
        outbox_id = await _enqueue(worker, db_session_factory)

        moment = NOW
        for attempt in range(1, MAX_ATTEMPTS + 1):
            assert await worker.run_once(now=moment) == 1
            row = await _read(db_session_factory, outbox_id)
            assert row["attempts"] == attempt
            assert row["last_error"] == "503 unavailable"
            if attempt < MAX_ATTEMPTS:
                assert row["status"] == OutboxStatus.FAILED.value
                # 退避公式 min(3600, 2**attempts)。
                assert row["next_attempt_at"] == moment + timedelta(
                    seconds=2**attempt
                )
                moment = row["next_attempt_at"]
            else:
                assert row["status"] == OutboxStatus.DEAD.value

        assert await worker.run_once(now=moment + timedelta(days=1)) == 0
        assert len(adapter.calls) == MAX_ATTEMPTS

    asyncio.run(scenario())


def test_batch_size_is_twenty_and_claim_marks_sending(db_session_factory):
    """一批最多 20 条；领取时先置 sending，发完才落 sent。"""

    async def scenario() -> None:
        pending_seen: list[int] = []

        class InspectingAdapter(FakeAdapter):
            async def send(self, chat_id: str, text: str) -> dict[str, Any]:
                # 发送期间本批行已经是 sending，不会被下一轮重复领取。
                async with db_session_factory() as check:
                    pending = await check.scalar(
                        select(func.count())
                        .select_from(Outbox)
                        .where(Outbox.status == OutboxStatus.PENDING.value)
                    )
                    pending_seen.append(int(pending or 0))
                return await super().send(chat_id, text)

        adapter = InspectingAdapter()
        worker = _worker(db_session_factory, adapter)
        async with db_session_factory() as session:
            for index in range(BATCH_SIZE + 5):
                await worker.enqueue(
                    session,
                    channel=OutboxChannel.IM_FEISHU,
                    target=CHAT_ID,
                    kind=OutboxKind.ALERT,
                    payload={"text": f"告警 {index}"},
                    dedupe_key=f"alert:{index}",
                    now=NOW,
                )
            await session.commit()

        assert await worker.run_once(now=NOW) == BATCH_SIZE
        assert await _count(db_session_factory, OutboxStatus.SENT) == BATCH_SIZE
        assert await _count(db_session_factory, OutboxStatus.PENDING) == 5
        assert len(adapter.calls) == BATCH_SIZE
        # 第一批发件时 20 条已领取为 sending，只剩 5 条 pending。
        assert pending_seen[:BATCH_SIZE] == [5] * BATCH_SIZE

        assert await worker.run_once(now=NOW) == 5
        assert await _count(db_session_factory, OutboxStatus.SENT) == BATCH_SIZE + 5
        assert pending_seen[BATCH_SIZE:] == [0] * 5

    asyncio.run(scenario())


def test_webpush_channel_uses_registered_sender(db_session_factory):
    """M85 G4：``webpush`` 行不经平台适配器，走注册的 Web Push 发送器。"""

    async def scenario() -> None:
        from maa_api.agent.channels.outbox import WebPushOutbox

        payloads: list[dict[str, Any]] = []

        async def sender(payload: dict[str, Any]) -> dict[str, Any]:
            payloads.append(payload)
            return {"attempted": 2, "delivered": 2, "failed": 0}

        worker = OutboxWorker(
            db_session_factory,
            lambda _channel: None,
            senders={OutboxChannel.WEBPUSH.value: sender},
            clock=lambda: NOW,
        )
        outbox = WebPushOutbox(worker, db_session_factory)
        row = await outbox.enqueue(
            {"title": "每日简报", "body": "今天状态不错"},
            kind=OutboxKind.BRIEF,
            dedupe_key="brief:artifact-1",
            ref_ticket_id=None,
        )
        duplicate = await outbox.enqueue(
            {"title": "每日简报", "body": "不该覆盖"},
            kind=OutboxKind.BRIEF,
            dedupe_key="brief:artifact-1",
        )
        assert int(duplicate.id) == int(row.id)

        assert await worker.run_once(now=NOW) == 1
        stored = await _read(db_session_factory, int(row.id))
        assert stored["status"] == OutboxStatus.SENT.value
        assert stored["receipt"] == {"attempted": 2, "delivered": 2, "failed": 0}
        assert payloads == [{"title": "每日简报", "body": "今天状态不错"}]
        assert stored["payload"]["title"] == "每日简报"

    asyncio.run(scenario())
