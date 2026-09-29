"""M21 提问服务：落库、等待、裁决、过期与审计（规格 §3.3）。

与 ``test_confirmation_service.py`` 同一套写法：内存库 + ``asyncio.run(scenario())``，
不依赖真实配置。提问不是确认，因此这里同时钉住「两者不共用一张表」：走完整条链路后
``confirmation`` 表必须仍是空的。
"""

from __future__ import annotations

import asyncio
from datetime import timedelta

import pytest
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker
from sqlalchemy.pool import StaticPool
from sqlmodel import SQLModel

import maa_api.db.models  # noqa: F401
from maa_api.agent.questions import QuestionService
from maa_api.db import session as db_session
from maa_api.db.models import AgentSession, Confirmation, utcnow
from maa_api.db.repositories.agent import AgentSessionRepository
from maa_api.db.repositories.audit import AuditRepository
from maa_api.db.repositories.question import AgentQuestionRepository
from maa_api.domain.enums import AuditStatus, CallerType, QuestionStatus, RiskLevel
from maa_api.domain.errors import AppError, ErrorCode

OPTIONS = [
    {"label": "官服（Official）", "description": "官方客户端"},
    {"label": "B 服", "description": None},
]
SESSION_ID = "session-1"


def _database():
    engine = db_session.make_engine(
        "sqlite+aiosqlite:///:memory:", poolclass=StaticPool
    )
    factory = async_sessionmaker(engine, expire_on_commit=False, class_=AsyncSession)
    return engine, factory


async def _prepare(factory, **service_options):
    """建库 + 一条会话行 + 一个记录了广播的服务实例。"""
    events: list[tuple[str, dict]] = []
    service = QuestionService(
        factory,
        broadcast=lambda kind, data: events.append((kind, data)),
        **service_options,
    )
    async with factory() as session:
        await AgentSessionRepository(session).create(
            AgentSession(id=SESSION_ID, model="test-model")
        )
        await session.commit()
    return service, events


async def _create(service, **overrides):
    kwargs = {
        "session_id": SESSION_ID,
        "tool_call_id": "call-1",
        "question": "走哪个客户端渠道？",
        "options": OPTIONS,
        "multiple": False,
        "request_id": "req-1",
    }
    kwargs.update(overrides)
    return await service.create(**kwargs)


async def _audit(factory, audit_id: int):
    async with factory() as session:
        return await AuditRepository(session).get(audit_id)


async def _confirmation_count(factory) -> int:
    async with factory() as session:
        return int(
            (await session.execute(select(func.count()).select_from(Confirmation))).scalar_one()
        )


def test_create_persists_pending_question_with_ask_user_audit_and_broadcast():
    async def scenario():
        engine, factory = _database()
        async with engine.begin() as connection:
            await connection.run_sync(SQLModel.metadata.create_all)
        service, events = await _prepare(factory)

        wire, audit_id = await _create(service)

        assert wire["question_id"]
        assert wire["session_id"] == SESSION_ID
        assert wire["tool_call_id"] == "call-1"
        assert wire["question"] == "走哪个客户端渠道？"
        assert wire["options"] == OPTIONS
        assert wire["multiple"] is False
        assert wire["status"] == "pending"
        assert wire["selection"] is None
        assert wire["answered_at"] is None and wire["answered_by"] is None
        assert wire["created_at"].endswith("Z") and wire["expires_at"].endswith("Z")
        assert (await service.get(wire["question_id"])) == wire

        audit = await _audit(factory, audit_id)
        assert audit.tool_name == "ask_user"
        assert audit.caller == CallerType.INTERNAL
        assert audit.risk_level == RiskLevel.NONE
        assert audit.status == AuditStatus.PENDING
        assert audit.session_id == SESSION_ID
        assert audit.request_id == "req-1"
        assert audit.arguments == {
            "question": "走哪个客户端渠道？",
            "options": OPTIONS,
            "multiple": False,
        }

        # 超时时刻落库：创建时刻 + 服务配置的等待时长。
        async with factory() as session:
            row = await AgentQuestionRepository(session).get(wire["question_id"])
        assert row.expires_at - row.created_at == timedelta(seconds=service.timeout_seconds)
        assert [kind for kind, _data in events] == ["question_request"]
        assert events[0][1]["question_id"] == wire["question_id"]
        assert await _confirmation_count(factory) == 0

        await engine.dispose()

    asyncio.run(scenario())


def test_get_missing_question_raises_not_found():
    async def scenario():
        engine, factory = _database()
        async with engine.begin() as connection:
            await connection.run_sync(SQLModel.metadata.create_all)
        service, _events = await _prepare(factory)

        with pytest.raises(AppError) as error:
            await service.get("missing")
        assert error.value.code == ErrorCode.QUESTION_NOT_FOUND
        assert error.value.http_status == 404

        await engine.dispose()

    asyncio.run(scenario())


def test_list_filters_by_session_and_status_with_pagination():
    async def scenario():
        engine, factory = _database()
        async with engine.begin() as connection:
            await connection.run_sync(SQLModel.metadata.create_all)
        service, _events = await _prepare(factory)
        first, _audit_id = await _create(service, tool_call_id="call-1")
        second, _audit_id = await _create(service, tool_call_id="call-2")
        await service.answer(first["question_id"], selected=["B 服"])
        async with factory() as session:
            await AgentSessionRepository(session).create(
                AgentSession(id="session-2", model="test-model")
            )
            await session.commit()
        third, _audit_id = await _create(
            service, session_id="session-2", tool_call_id="call-3"
        )

        # 默认只列 pending：两条（跨会话都算），按 created_at DESC。
        page = await service.list()
        assert [item["question_id"] for item in page["items"]] == [
            third["question_id"],
            second["question_id"],
        ]
        assert page["total"] == 2 and page["page"] == 1 and page["size"] == 20

        assert (await service.list(status=None))["total"] == 3
        assert (await service.list(status=QuestionStatus.ANSWERED))["total"] == 1
        scoped = await service.list(session_id="session-2", status=None)
        assert [item["question_id"] for item in scoped["items"]] == [third["question_id"]]
        assert (await service.list(session_id="session-2", status="pending"))["total"] == 1
        # 分页参数透传（size 生效，total 仍是过滤后的总数）。
        assert (await service.list(status=None, page=2, size=1))["items"][0]["question_id"] == second["question_id"]

        await engine.dispose()

    asyncio.run(scenario())


def test_answer_dedupes_labels_and_completes_the_audit():
    async def scenario():
        engine, factory = _database()
        async with engine.begin() as connection:
            await connection.run_sync(SQLModel.metadata.create_all)
        service, events = await _prepare(factory)
        wire, audit_id = await _create(service, multiple=True)

        answered = await service.answer(
            wire["question_id"], selected=["官服（Official）", "B 服", "B 服"]
        )

        assert answered["status"] == "answered"
        assert answered["selection"] == ["官服（Official）", "B 服"]
        assert answered["answered_by"] == "web"
        assert answered["answered_at"].endswith("Z")
        audit = await _audit(factory, audit_id)
        assert audit.status == AuditStatus.SUCCESS
        assert audit.result_summary == "用户选择：官服（Official）、B 服"
        assert [kind for kind, _data in events] == ["question_request", "question_resolved"]
        assert events[-1][1]["reason"] == "answered"
        assert events[-1][1]["selection"] == ["官服（Official）", "B 服"]
        assert await _confirmation_count(factory) == 0

        await engine.dispose()

    asyncio.run(scenario())


@pytest.mark.parametrize(
    "selected",
    [
        [],
        ["不存在的选项"],
        ["官服（Official）", "B 服"],  # 单选提交多项
        [""],
    ],
)
def test_answer_rejects_invalid_selections(selected):
    async def scenario():
        engine, factory = _database()
        async with engine.begin() as connection:
            await connection.run_sync(SQLModel.metadata.create_all)
        service, _events = await _prepare(factory)
        wire, audit_id = await _create(service)

        with pytest.raises(AppError) as error:
            await service.answer(wire["question_id"], selected=selected)

        assert error.value.code == ErrorCode.QUESTION_SELECTION_INVALID
        assert error.value.http_status == 400
        assert (await service.get(wire["question_id"]))["status"] == "pending"
        assert (await _audit(factory, audit_id)).status == AuditStatus.PENDING
        # 选择不合法不影响这张卡继续被正常回答。
        assert (await service.answer(wire["question_id"], selected=["B 服"]))["status"] == "answered"

        await engine.dispose()

    asyncio.run(scenario())


def test_answer_twice_and_skip_after_answer_report_already_answered():
    async def scenario():
        engine, factory = _database()
        async with engine.begin() as connection:
            await connection.run_sync(SQLModel.metadata.create_all)
        service, _events = await _prepare(factory)
        wire, _audit_id = await _create(service)
        await service.answer(wire["question_id"], selected=["B 服"])

        with pytest.raises(AppError) as answered_error:
            await service.answer(wire["question_id"], selected=["官服（Official）"])
        assert answered_error.value.code == ErrorCode.QUESTION_ALREADY_ANSWERED
        assert answered_error.value.http_status == 409

        with pytest.raises(AppError) as skip_error:
            await service.skip(wire["question_id"])
        assert skip_error.value.code == ErrorCode.QUESTION_ALREADY_ANSWERED

        # 冲突不改动已有裁决。
        assert (await service.get(wire["question_id"]))["selection"] == ["B 服"]

        await engine.dispose()

    asyncio.run(scenario())


def test_skip_transitions_to_skipped_with_rejected_audit_and_broadcast():
    async def scenario():
        engine, factory = _database()
        async with engine.begin() as connection:
            await connection.run_sync(SQLModel.metadata.create_all)
        service, events = await _prepare(factory)
        wire, audit_id = await _create(service)

        skipped = await service.skip(wire["question_id"])

        assert skipped["status"] == "skipped"
        assert skipped["selection"] is None
        assert skipped["answered_by"] == "web"
        audit = await _audit(factory, audit_id)
        assert audit.status == AuditStatus.REJECTED
        assert audit.result_summary == "用户跳过了提问"
        assert events[-1][0] == "question_resolved"
        assert events[-1][1]["reason"] == "skipped"

        with pytest.raises(AppError) as error:
            await service.skip(wire["question_id"])
        assert error.value.code == ErrorCode.QUESTION_ALREADY_ANSWERED
        assert await _confirmation_count(factory) == 0

        await engine.dispose()

    asyncio.run(scenario())


def test_expire_due_only_expires_overdue_questions_and_records_the_audit():
    async def scenario():
        engine, factory = _database()
        async with engine.begin() as connection:
            await connection.run_sync(SQLModel.metadata.create_all)
        current = [utcnow()]
        service, events = await _prepare(
            factory, timeout_seconds=60, clock=lambda: current[0]
        )
        overdue, overdue_audit = await _create(service, tool_call_id="call-1")
        # 两张卡的过期时刻必须错开，否则「只过期到期的那些」这条断言没有意义。
        current[0] = current[0] + timedelta(seconds=1)
        fresh, _fresh_audit = await _create(service, tool_call_id="call-2")

        expired_ids = await service.expire_due()
        assert expired_ids == []

        current[0] = current[0] + timedelta(seconds=59.5)
        expired_ids = await service.expire_due()
        assert expired_ids == [overdue["question_id"]]
        assert (await service.get(overdue["question_id"]))["status"] == "expired"
        assert (await service.get(fresh["question_id"]))["status"] == "pending"

        audit = await _audit(factory, overdue_audit)
        assert audit.status == AuditStatus.EXPIRED
        assert audit.error_code == str(ErrorCode.QUESTION_EXPIRED)
        assert audit.result_summary == "提问超时，本轮已结束"
        resolved = [data for kind, data in events if kind == "question_resolved"]
        assert [item["question_id"] for item in resolved] == [overdue["question_id"]]
        assert resolved[0]["reason"] == "expired"
        assert resolved[0]["answered_by"] == "system"

        # 过期后再回答/跳过都是 409 QUESTION_EXPIRED。
        with pytest.raises(AppError) as answer_error:
            await service.answer(overdue["question_id"], selected=["B 服"])
        assert answer_error.value.code == ErrorCode.QUESTION_EXPIRED
        assert answer_error.value.http_status == 409
        with pytest.raises(AppError) as skip_error:
            await service.skip(overdue["question_id"])
        assert skip_error.value.code == ErrorCode.QUESTION_EXPIRED

        await engine.dispose()

    asyncio.run(scenario())


def test_reset_after_restart_expires_every_leftover_pending_question():
    async def scenario():
        engine, factory = _database()
        async with engine.begin() as connection:
            await connection.run_sync(SQLModel.metadata.create_all)
        service, events = await _prepare(factory)
        first, first_audit = await _create(service, tool_call_id="call-1")
        second, second_audit = await _create(service, tool_call_id="call-2")

        await service.reset_after_restart()

        assert (await service.get(first["question_id"]))["status"] == "expired"
        assert (await service.get(second["question_id"]))["status"] == "expired"
        for audit_id in (first_audit, second_audit):
            audit = await _audit(factory, audit_id)
            assert audit.status == AuditStatus.EXPIRED
            assert audit.result_summary == "服务重启，未回答的提问已失效"
        assert [data["reason"] for kind, data in events if kind == "question_resolved"] == [
            "expired",
            "expired",
        ]
        # 再清一次是空操作。
        await service.reset_after_restart()
        assert len([kind for kind, _data in events if kind == "question_resolved"]) == 2

        await engine.dispose()

    asyncio.run(scenario())


def test_wait_returns_the_terminal_wire_on_timeout_without_raising():
    async def scenario():
        engine, factory = _database()
        async with engine.begin() as connection:
            await connection.run_sync(SQLModel.metadata.create_all)
        service, events = await _prepare(factory, timeout_seconds=1)
        wire, audit_id = await _create(service)

        result = await service.wait(wire["question_id"], poll_seconds=0.02)

        assert result["status"] == "expired"
        assert result["question_id"] == wire["question_id"]
        assert (await _audit(factory, audit_id)).status == AuditStatus.EXPIRED
        assert [data["reason"] for kind, data in events if kind == "question_resolved"] == [
            "expired"
        ]
        # 已经进入终态的提问，wait 立刻返回同一条终态 wire。
        assert await service.wait(wire["question_id"]) == result

        await engine.dispose()

    asyncio.run(scenario())


def test_wait_wakes_up_immediately_when_the_user_answers():
    async def scenario():
        engine, factory = _database()
        async with engine.begin() as connection:
            await connection.run_sync(SQLModel.metadata.create_all)
        service, _events = await _prepare(factory, timeout_seconds=60)
        wire, _audit_id = await _create(service)

        waiting = asyncio.create_task(service.wait(wire["question_id"], poll_seconds=5))
        await asyncio.sleep(0)  # 让 wait 先挂到事件上
        await service.answer(wire["question_id"], selected=["官服（Official）"])

        result = await asyncio.wait_for(waiting, timeout=2)
        assert result["status"] == "answered"
        assert result["selection"] == ["官服（Official）"]
        assert service._resolution_events == {}

        await engine.dispose()

    asyncio.run(scenario())


def test_broadcast_failure_never_breaks_the_question_lifecycle():
    async def scenario():
        engine, factory = _database()
        async with engine.begin() as connection:
            await connection.run_sync(SQLModel.metadata.create_all)
        async with factory() as session:
            await AgentSessionRepository(session).create(
                AgentSession(id=SESSION_ID, model="test-model")
            )
            await session.commit()

        def explode(_kind, _data):
            raise RuntimeError("websocket 挂了")

        service = QuestionService(factory, broadcast=explode, timeout_seconds=60)
        wire, audit_id = await _create(service)

        assert (await service.answer(wire["question_id"], selected=["B 服"]))[
            "status"
        ] == "answered"
        assert (await _audit(factory, audit_id)).status == AuditStatus.SUCCESS

        await engine.dispose()

    asyncio.run(scenario())


def test_question_timeout_must_be_positive():
    with pytest.raises(ValueError):
        QuestionService(None, timeout_seconds=0)


def test_cancel_pending_for_session_cancels_cards_and_wakes_the_waiter(tmp_path):
    """M22 规格 §3.3：中断撤回本会话挂起的提问，别的会话不受影响。"""

    async def scenario():
        engine = db_session.make_engine(
            f"sqlite+aiosqlite:///{tmp_path / 'cancel-question.sqlite3'}"
        )
        factory = async_sessionmaker(engine, expire_on_commit=False, class_=AsyncSession)
        async with engine.begin() as connection:
            await connection.run_sync(SQLModel.metadata.create_all)
        service, events = await _prepare(factory)
        async with factory() as session:
            await AgentSessionRepository(session).create(
                AgentSession(id="session-2", model="test-model")
            )
            await session.commit()

        first, first_audit = await _create(service)
        second, _second_audit = await _create(
            service, tool_call_id="call-2", question="第二个问题？"
        )
        other, other_audit = await _create(service, session_id="session-2")

        waiter = asyncio.create_task(service.wait(first["question_id"]))
        ids = await service.cancel_pending_for_session(SESSION_ID)

        assert sorted(ids) == sorted(
            [first["question_id"], second["question_id"]]
        )
        # 等待方被唤醒到终态 cancelled，而不是继续挂到超时。
        woken = await asyncio.wait_for(waiter, timeout=5)
        assert woken["status"] == QuestionStatus.CANCELLED.value
        assert woken["question_id"] == first["question_id"]

        assert (await service.get(first["question_id"]))["status"] == "cancelled"
        assert (await service.get(second["question_id"]))["status"] == "cancelled"
        assert (await service.get(other["question_id"]))["status"] == "pending"
        assert (await service.list(status=QuestionStatus.PENDING))["total"] == 1

        audit = await _audit(factory, first_audit)
        assert audit.status == AuditStatus.REJECTED
        assert audit.result_summary == "本轮已被中断，提问已取消"
        assert (await _audit(factory, other_audit)).status == AuditStatus.PENDING

        resolved = [
            data for kind, data in events if kind == "question_resolved"
        ]
        assert {item["question_id"] for item in resolved} == set(ids)
        assert {item["reason"] for item in resolved} == {"cancelled"}
        # 幂等：再撤一次没有可处理的了。
        assert await service.cancel_pending_for_session(SESSION_ID) == []

        async with factory() as session:
            # 撤回不是超时：不能被记成 expired。
            rows = await AgentQuestionRepository(session).list(
                session_id=SESSION_ID, status=QuestionStatus.EXPIRED
            )
        assert rows.total == 0
        await engine.dispose()

    asyncio.run(scenario())
