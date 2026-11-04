"""M80 T7：会话回溯（规格 §4.5.7、§6 的十条用例）。

全部用真临时库（``tests/fakes/v3_db.py`` 的会话工厂）与真实工具注册表；服务只
注入一个只实现 ``get`` 的工单服务替身（回溯本身不建单）。每个用例覆盖 §6 的一行
断言要点：取代与上下文排除、目标表逐行、两种 409、TICKET_ENDED、检查点作废与
改回、计划放弃、副作用清单与提示正文、dry_run 不写库、最后一条空操作、写锁之后
才出现的运行。
"""

from __future__ import annotations

import asyncio
import json
from datetime import datetime, timedelta
from typing import Any

import pytest
from sqlalchemy import select

from maa_api.agent.context.rewind import RewindService, SideEffect
from maa_api.agent.tools import build_registry
from maa_api.db.models import (
    AgentAudit,
    AgentMessage,
    AgentQuestion,
    AgentSession,
    CompactionCheckpoint,
    Confirmation,
    RunPlan,
    Ticket,
    utcnow,
)
from maa_api.db.repositories.agent import AgentMessageRepository, AgentRunRepository
from maa_api.domain.enums import TicketStatus
from maa_api.domain.errors import AppError, ErrorCode
from tests.fakes.v3_db import make_session_factory, seed_run

BASE = datetime(2026, 11, 5, 0, 0, 0)


class _Tickets:
    """只实现回溯/分叉用到的 ``get``；不存在时按生产口径抛 404。"""

    def __init__(self, factory) -> None:
        self._factory = factory

    async def get(self, ticket_id: str) -> Ticket:
        async with self._factory() as db:
            ticket = await db.get(Ticket, ticket_id)
        if ticket is None:
            raise AppError(
                ErrorCode.TICKET_NOT_FOUND, "没有这个工单", {"ticket_id": ticket_id}
            )
        return ticket


class _Broadcast:
    def __init__(self) -> None:
        self.events: list[tuple[str, dict[str, Any]]] = []

    def __call__(self, message_type: str, data: dict[str, Any]) -> None:
        self.events.append((message_type, data))


def _service(factory, *, broadcast: Any = None) -> RewindService:
    return RewindService(
        factory,
        registry=build_registry(),
        ticket_service=_Tickets(factory),
        broadcast=broadcast,
    )


async def _session(
    factory,
    *,
    kind: str = "worker",
    status: str = "active",
    ticket_id: str | None = None,
    archived: bool = False,
) -> str:
    row = AgentSession(
        model="test-model",
        kind=kind,
        status=status,
        ticket_id=ticket_id,
        archived_at=utcnow() if archived else None,
    )
    async with factory() as db:
        db.add(row)
        await db.commit()
    return str(row.id)


async def _messages(factory, session_id: str, rows: list[dict[str, Any]]) -> None:
    async with factory() as db:
        for row in rows:
            db.add(AgentMessage(session_id=session_id, **row))
        await db.commit()


async def _ticket(factory, *, status: str = "open", title: str = "日常") -> str:
    row = Ticket(title=title, kind="daily_ops", status=status, origin="user")
    async with factory() as db:
        db.add(row)
        await db.commit()
    return str(row.id)


async def _reason(service: RewindService, session_id: str, to_seq: int) -> str:
    with pytest.raises(AppError) as info:
        await service.rewind(session_id, to_seq, dry_run=True)
    assert info.value.code is ErrorCode.REWIND_TARGET_INVALID
    return str(info.value.details["reason"])


def _simple_rows(count: int = 6) -> list[dict[str, Any]]:
    """0 user / 1 assistant / 2 user / 3 assistant / 4 user / 5 assistant。"""
    rows: list[dict[str, Any]] = []
    for seq in range(count):
        role = "user" if seq % 2 == 0 else "assistant"
        rows.append(
            {
                "seq": seq,
                "role": role,
                "content": f"消息 {seq}",
                "created_at": BASE + timedelta(seconds=seq),
            }
        )
    return rows


async def _snapshot(factory, session_id: str) -> dict[str, Any]:
    async with factory() as db:
        messages = (
            await db.execute(
                select(AgentMessage)
                .where(AgentMessage.session_id == session_id)
                .order_by(AgentMessage.seq)
            )
        ).scalars().all()
        checkpoints = (
            await db.execute(
                select(CompactionCheckpoint).where(
                    CompactionCheckpoint.session_id == session_id
                )
            )
        ).scalars().all()
        plans = (
            await db.execute(
                select(RunPlan).where(RunPlan.session_id == session_id)
            )
        ).scalars().all()
    return {
        "messages": [
            (row.seq, row.role, row.superseded_at, row.content) for row in messages
        ],
        "checkpoints": [(row.upto_seq, str(row.status)) for row in checkpoints],
        "plans": [(str(row.status),) for row in plans],
    }


def test_rewind_supersedes_and_excludes_from_context(tmp_path) -> None:
    """被取代消息有时间戳、``list_by_session`` 不返回、新消息 seq 接着最大值。"""

    engine, factory = asyncio.run(make_session_factory(tmp_path / "rewind.db"))

    async def scenario() -> None:
        session_id = await _session(factory, kind="orchestrator")
        await _messages(factory, session_id, _simple_rows())
        service = _service(factory)

        result = await service.rewind(session_id, 2)

        assert result.superseded == 3
        assert result.notice_message_id is None
        async with factory() as db:
            rows = (
                await db.execute(
                    select(AgentMessage)
                    .where(AgentMessage.session_id == session_id)
                    .order_by(AgentMessage.seq)
                )
            ).scalars().all()
            assert [row.superseded_at is not None for row in rows] == [
                False,
                False,
                False,
                True,
                True,
                True,
            ]
            assert all(row.superseded_at is not None for row in rows[3:])
            repo = AgentMessageRepository(db)
            live = await repo.list_by_session(session_id)
            assert [row.seq for row in live] == [0, 1, 2]
            assert await repo.next_seq(session_id) == 6

    asyncio.run(scenario())
    engine.sync_engine.dispose()


def test_rewind_target_rules(tmp_path) -> None:
    """§4.5.7 目标表逐行：合法目标、批次中间、带 tool_calls 的 assistant 等。"""

    engine, factory = asyncio.run(make_session_factory(tmp_path / "targets.db"))

    async def scenario() -> None:
        session_id = await _session(factory, kind="worker")
        await _messages(
            factory,
            session_id,
            [
                {"seq": 0, "role": "user", "content": "开始", "created_at": BASE},
                {
                    "seq": 1,
                    "role": "assistant",
                    "content": None,
                    "tool_calls": [
                        {"id": "c1", "type": "function", "function": {"name": "get_stage"}},
                        {"id": "c2", "type": "function", "function": {"name": "back_to_home"}},
                    ],
                    "created_at": BASE + timedelta(seconds=1),
                },
                {
                    "seq": 2,
                    "role": "tool",
                    "content": json.dumps({"result": {"stage": {"code": "1-7"}}}),
                    "tool_call_id": "c1",
                    "tool_name": "get_stage",
                    "created_at": BASE + timedelta(seconds=2),
                },
                {
                    "seq": 3,
                    "role": "tool",
                    "content": json.dumps({"result": {"ok": True}}),
                    "tool_call_id": "c2",
                    "tool_name": "back_to_home",
                    "created_at": BASE + timedelta(seconds=3),
                },
                {"seq": 4, "role": "assistant", "content": "完成", "created_at": BASE + timedelta(seconds=4)},
                {"seq": 5, "role": "system", "content": "提示", "created_at": BASE + timedelta(seconds=5)},
                {
                    "seq": 6,
                    "role": "assistant",
                    "content": "旧回复",
                    "superseded_at": BASE + timedelta(seconds=9),
                    "created_at": BASE + timedelta(seconds=6),
                },
            ],
        )
        service = _service(factory)

        # 合法：user、system、没有 tool_calls 的 assistant、批次末尾的 tool。
        for seq in (0, 4, 5, 3):
            result = await service.rewind(session_id, seq, dry_run=True)
            assert result.to_seq == seq
        # 不合法：带 tool_calls 的 assistant、批次中间的 tool。
        assert await _reason(service, session_id, 1) == "splits_tool_batch"
        assert await _reason(service, session_id, 2) == "splits_tool_batch"
        # 没有这条消息、已被取代、Worker 的 -1。
        assert await _reason(service, session_id, 99) == "not_found"
        assert await _reason(service, session_id, 6) == "superseded"
        assert await _reason(service, session_id, -1) == "session_start"

        chat_id = await _session(factory, kind="chat")
        await _messages(factory, chat_id, [{"seq": 0, "role": "user", "content": "hi"}])
        assert await _reason(service, chat_id, 0) == "session_kind"

        archived_id = await _session(factory, kind="orchestrator", archived=True)
        await _messages(factory, archived_id, [{"seq": 0, "role": "user", "content": "hi"}])
        assert await _reason(service, archived_id, 0) == "archived"

        # 总控的 -1 合法：取代全部消息。
        orchestrator_id = await _session(factory, kind="orchestrator")
        await _messages(factory, orchestrator_id, _simple_rows(3))
        result = await service.rewind(orchestrator_id, -1, dry_run=True)
        assert result.superseded == 3

    asyncio.run(scenario())
    engine.sync_engine.dispose()


def test_rewind_rejects_active_run_and_pending_confirmation(tmp_path) -> None:
    """活动运行与待处理确认/提问都返回 409；后者带 ``details.pending``。"""

    engine, factory = asyncio.run(make_session_factory(tmp_path / "pending.db"))

    async def scenario() -> None:
        service = _service(factory)

        running_id = await _session(factory, kind="worker")
        await _messages(factory, running_id, _simple_rows(4))
        await seed_run(factory, session_id=running_id, status="running")
        with pytest.raises(AppError) as info:
            await service.rewind(running_id, 1)
        assert info.value.code is ErrorCode.RUN_ALREADY_ACTIVE

        waiting_id = await _session(factory, kind="orchestrator")
        await _messages(factory, waiting_id, _simple_rows(4))
        await seed_run(factory, session_id=waiting_id, status="waiting_human")
        with pytest.raises(AppError) as info:
            await service.rewind(waiting_id, 1)
        assert info.value.code is ErrorCode.RUN_ALREADY_ACTIVE

        confirm_id = await _session(factory, kind="orchestrator")
        await _messages(factory, confirm_id, _simple_rows(4))
        async with factory() as db:
            db.add(
                Confirmation(
                    action="back_to_home",
                    risk_level="safe",
                    reason="读到了可疑外部内容",
                    payload={"session_id": confirm_id, "tool_name": "back_to_home"},
                    requested_by="internal",
                    expires_at=utcnow() + timedelta(minutes=5),
                )
            )
            await db.commit()
        with pytest.raises(AppError) as info:
            await service.rewind(confirm_id, 1)
        assert info.value.code is ErrorCode.RUN_ALREADY_ACTIVE
        assert info.value.details["pending"]["confirmations"] == 1
        assert info.value.details["pending"]["questions"] == 0

        question_id = await _session(factory, kind="orchestrator")
        await _messages(factory, question_id, _simple_rows(4))
        async with factory() as db:
            db.add(
                AgentQuestion(
                    session_id=question_id,
                    tool_call_id="call-1",
                    question="用哪个干员？",
                    options=[],
                    multiple=False,
                    expires_at=utcnow() + timedelta(minutes=5),
                )
            )
            await db.commit()
        with pytest.raises(AppError) as info:
            await service.rewind(question_id, 1)
        assert info.value.code is ErrorCode.RUN_ALREADY_ACTIVE
        assert info.value.details["pending"]["questions"] == 1

    asyncio.run(scenario())
    engine.sync_engine.dispose()


def test_rewind_rejects_ended_ticket(tmp_path) -> None:
    """Worker 会话所属工单已结束时返回 409 ``TICKET_ENDED``。"""

    engine, factory = asyncio.run(make_session_factory(tmp_path / "ended.db"))

    async def scenario() -> None:
        ended_ticket = await _ticket(factory, status=TicketStatus.DONE.value)
        ended_session = await _session(factory, kind="worker", ticket_id=ended_ticket)
        await _messages(factory, ended_session, _simple_rows(4))
        service = _service(factory)
        with pytest.raises(AppError) as info:
            await service.rewind(ended_session, 1)
        assert info.value.code is ErrorCode.TICKET_ENDED
        assert info.value.details["ticket_id"] == ended_ticket

        open_ticket = await _ticket(factory, status=TicketStatus.OPEN.value)
        open_session = await _session(factory, kind="worker", ticket_id=open_ticket)
        await _messages(factory, open_session, _simple_rows(4))
        result = await service.rewind(open_session, 1, dry_run=True)
        assert result.superseded == 2

    asyncio.run(scenario())
    engine.sync_engine.dispose()


def test_rewind_invalidates_and_reactivates_checkpoints(tmp_path) -> None:
    """晚的检查点作废、早的改回 active（§4.5.7 检查点两步）。"""

    engine, factory = asyncio.run(make_session_factory(tmp_path / "cp.db"))

    async def scenario() -> None:
        session_id = await _session(factory, kind="orchestrator")
        await _messages(factory, session_id, _simple_rows(5))
        async with factory() as db:
            older = CompactionCheckpoint(
                session_id=session_id,
                upto_seq=1,
                summary="早",
                summary_json={},
                durable={},
                tokens_before=100,
                tokens_after=10,
                trigger="auto",
                status="superseded",
                created_at=BASE + timedelta(seconds=10),
            )
            newer = CompactionCheckpoint(
                session_id=session_id,
                upto_seq=3,
                summary="晚",
                summary_json={},
                durable={},
                tokens_before=200,
                tokens_after=20,
                trigger="auto",
                status="active",
                created_at=BASE + timedelta(seconds=20),
            )
            db.add_all([older, newer])
            await db.commit()
            older_id, newer_id = str(older.id), str(newer.id)

        result = await _service(factory).rewind(session_id, 2)

        assert result.checkpoints_invalidated == 1
        assert result.checkpoint_reactivated == older_id
        async with factory() as db:
            rows = {
                str(row.id): str(row.status)
                for row in (
                    await db.execute(
                        select(CompactionCheckpoint).where(
                            CompactionCheckpoint.session_id == session_id
                        )
                    )
                ).scalars()
            }
        assert rows == {older_id: "active", newer_id: "invalidated"}

    asyncio.run(scenario())
    engine.sync_engine.dispose()


def test_rewind_abandons_newer_plan(tmp_path) -> None:
    """目标之后建的计划改 ``abandoned``；更早的与终态的计划不动。"""

    engine, factory = asyncio.run(make_session_factory(tmp_path / "plan.db"))

    async def scenario() -> None:
        session_id = await _session(factory, kind="worker")
        await _messages(factory, session_id, _simple_rows(5))
        async with factory() as db:
            live = RunPlan(
                session_id=session_id,
                title="目标之后的计划",
                items=[],
                status="active",
                created_at=BASE + timedelta(seconds=3),
                updated_at=BASE,
            )
            finished = RunPlan(
                session_id=session_id,
                title="已完成的计划",
                items=[],
                status="done",
                created_at=BASE + timedelta(seconds=4),
                updated_at=BASE,
            )
            db.add_all([live, finished])
            await db.commit()
            live_id = str(live.id)

        result = await _service(factory).rewind(session_id, 2)

        assert result.plan_abandoned is True
        async with factory() as db:
            plans = {
                str(row.id): str(row.status)
                for row in (
                    await db.execute(select(RunPlan).where(RunPlan.session_id == session_id))
                ).scalars()
            }
        assert plans[live_id] == "abandoned"
        assert "done" in plans.values()

    asyncio.run(scenario())
    engine.sync_engine.dispose()


def test_rewind_lists_side_effects_and_writes_notice(tmp_path) -> None:
    """``submit_pipeline`` 进清单、``detail`` 是「流水线 xxxxxxxx」，提示 role=system。"""

    engine, factory = asyncio.run(make_session_factory(tmp_path / "effects.db"))

    async def scenario() -> None:
        ticket_id = await _ticket(factory, title="日常")
        session_id = await _session(factory, kind="worker", ticket_id=ticket_id)
        async with factory() as db:
            db.add(
                AgentAudit(
                    id=1,
                    caller="internal",
                    tool_name="submit_pipeline",
                    arguments={},
                    status="success",
                    risk_level="none",
                )
            )
            await db.commit()
        await _messages(
            factory,
            session_id,
            [
                {"seq": 0, "role": "user", "content": "刷 1-7", "created_at": BASE},
                {
                    "seq": 1,
                    "role": "tool",
                    "content": json.dumps(
                        {"result": {"pipeline_id": "1f3e9a2c-aaaa-bbbb-cccc-dddddddddddd"}}
                    ),
                    "tool_name": "submit_pipeline",
                    "tool_call_id": "c1",
                    "audit_id": 1,
                    "created_at": BASE + timedelta(seconds=1),
                },
                {
                    "seq": 2,
                    "role": "tool",
                    "content": json.dumps({"result": {"stage": {"code": "1-7"}}}),
                    "tool_name": "get_stage",
                    "tool_call_id": "c2",
                    "created_at": BASE + timedelta(seconds=2),
                },
                {
                    "seq": 3,
                    "role": "tool",
                    "content": json.dumps({"result": {"ticket_id": ticket_id}}),
                    "tool_name": "create_ticket",
                    "tool_call_id": "c3",
                    "created_at": BASE + timedelta(seconds=3),
                },
                {
                    "seq": 4,
                    "role": "tool",
                    "content": json.dumps({"result": {"ok": True}}),
                    "tool_name": "ask_user",
                    "tool_call_id": "c4",
                    "created_at": BASE + timedelta(seconds=4),
                },
                {
                    "seq": 5,
                    "role": "tool",
                    "content": json.dumps(
                        {"error": {"code": "PROMPT_INJECTION_BLOCKED"}, "guard": {"action": "blocked"}}
                    ),
                    "tool_name": "update_game",
                    "tool_call_id": "c5",
                    "created_at": BASE + timedelta(seconds=5),
                },
                {"seq": 6, "role": "assistant", "content": "好了", "created_at": BASE + timedelta(seconds=6)},
            ],
        )
        result = await _service(factory).rewind(session_id, 0)

        assert result.side_effects == (
            SideEffect(
                seq=1,
                tool_name="submit_pipeline",
                audit_id=1,
                detail="流水线 1f3e9a2c",
            ),
            SideEffect(
                seq=3,
                tool_name="create_ticket",
                audit_id=None,
                detail="工单「日常」",
            ),
        )
        assert result.notice_message_id is not None
        async with factory() as db:
            notice = await db.get(AgentMessage, result.notice_message_id)
        assert notice is not None
        assert notice.role == "system"
        assert notice.content == (
            "用户回到了这里，之后的 6 条消息已作废。"
            "作废部分里已经执行的操作不会撤销：submit_pipeline（流水线 1f3e9a2c）、"
            "create_ticket（工单「日常」）。"
            "继续之前先查看现场，不要重复这些操作。"
        )

    asyncio.run(scenario())
    engine.sync_engine.dispose()


def test_rewind_dry_run_writes_nothing(tmp_path) -> None:
    """``dry_run`` 前后库内容相同，但结果里能算出全部影响。"""

    engine, factory = asyncio.run(make_session_factory(tmp_path / "dry.db"))

    async def scenario() -> None:
        session_id = await _session(factory, kind="worker")
        await _messages(factory, session_id, _simple_rows(5))
        async with factory() as db:
            db.add(
                CompactionCheckpoint(
                    session_id=session_id,
                    upto_seq=3,
                    summary="晚",
                    summary_json={},
                    durable={},
                    tokens_before=1,
                    tokens_after=1,
                    trigger="auto",
                    status="active",
                    created_at=BASE + timedelta(seconds=1),
                )
            )
            db.add(
                RunPlan(
                    session_id=session_id,
                    title="计划",
                    items=[],
                    status="draft",
                    created_at=BASE + timedelta(seconds=4),
                    updated_at=BASE,
                )
            )
            await db.commit()

        before = await _snapshot(factory, session_id)
        result = await _service(factory).rewind(session_id, 2, dry_run=True)
        after = await _snapshot(factory, session_id)

        assert before == after
        assert result.dry_run is True
        assert result.superseded == 2
        assert result.checkpoints_invalidated == 1
        assert result.plan_abandoned is True
        assert result.notice_message_id is None

    asyncio.run(scenario())
    engine.sync_engine.dispose()


def test_rewind_noop_on_last_message(tmp_path) -> None:
    """目标是最后一条消息：``superseded=0``、不写库、不广播。"""

    engine, factory = asyncio.run(make_session_factory(tmp_path / "noop.db"))

    async def scenario() -> None:
        session_id = await _session(factory, kind="orchestrator")
        await _messages(factory, session_id, _simple_rows(3))
        broadcast = _Broadcast()
        before = await _snapshot(factory, session_id)

        result = await _service(factory, broadcast=broadcast).rewind(session_id, 2)

        assert result.superseded == 0
        assert result.notice_message_id is None
        assert await _snapshot(factory, session_id) == before
        assert broadcast.events == []

    asyncio.run(scenario())
    engine.sync_engine.dispose()


def test_rewind_loses_race_to_new_run(tmp_path, monkeypatch) -> None:
    """第 1 步之后插入活动运行：409 且整个写事务回滚。"""

    engine, factory = asyncio.run(make_session_factory(tmp_path / "race.db"))

    async def scenario() -> None:
        session_id = await _session(factory, kind="orchestrator")
        await _messages(factory, session_id, _simple_rows(5))
        before = await _snapshot(factory, session_id)
        calls = {"count": 0}
        original = AgentRunRepository.get_active_for_session

        async def racing(repo, target_session_id):
            calls["count"] += 1
            if calls["count"] == 1:
                return None
            return await original(repo, target_session_id) or type(
                "Running", (), {"id": "run-race"}
            )()

        monkeypatch.setattr(
            AgentRunRepository, "get_active_for_session", racing
        )
        with pytest.raises(AppError) as info:
            await _service(factory).rewind(session_id, 2)
        assert info.value.code is ErrorCode.RUN_ALREADY_ACTIVE
        assert calls["count"] == 2
        assert await _snapshot(factory, session_id) == before

    asyncio.run(scenario())
    engine.sync_engine.dispose()
