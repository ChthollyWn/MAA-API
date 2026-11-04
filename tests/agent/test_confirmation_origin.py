"""M71 T3：确认与提问的来源、审计归属（规格 §4.2 第 4–7 条、§6）。

三条用例分别钉住：

- 带 origin 的确认：三列落库、WS ``confirm_request`` 与 ``get`` / ``list`` 的
  ``origin.ticket_title`` 是工单标题、审计三列同值。
- 带 origin 的提问：同样的三列与 wire，外加拿 ``ask_user`` 审计行。
- REST 直接调用（没有代理运行）：origin 为 ``null``，审计三列为 ``NULL``。
"""

from __future__ import annotations

import asyncio
from types import SimpleNamespace

from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker
from sqlalchemy.pool import StaticPool
from sqlmodel import SQLModel

import maa_api.db.models  # noqa: F401
from maa_api.agent.confirmation import ConfirmationService
from maa_api.agent.policy import PolicyEngine
from maa_api.agent.questions import QuestionService
from maa_api.agent.registry import (
    ToolContext,
    ToolDefinition,
    ToolOrigin,
    ToolRegistry,
    ToolRisk,
)
from maa_api.db import session as db_session
from maa_api.db.models import Ticket, new_uuid
from maa_api.db.repositories.audit import AuditRepository, ConfirmationRepository
from maa_api.db.repositories.question import AgentQuestionRepository
from maa_api.domain.enums import AuditStatus, CallerType, QuestionStatus
from tests.fakes.v3_db import seed_run, seed_session

TITLE = "日常"
AGENT = "daily_ops"
OPTIONS = [
    {"label": "官服（Official）", "description": "官方客户端"},
    {"label": "B 服", "description": None},
]


class Params(SQLModel):
    value: int = 2


def _database():
    engine = db_session.make_engine(
        "sqlite+aiosqlite:///:memory:", poolclass=StaticPool
    )
    factory = async_sessionmaker(engine, expire_on_commit=False, class_=AsyncSession)
    return engine, factory


def _request():
    return SimpleNamespace(app=SimpleNamespace(state=SimpleNamespace()))


async def _seed_ticket_and_run(factory) -> tuple[str, str, str]:
    """工单、Worker 会话与根运行：origin 的三列都要能落到真外键上。"""
    ticket_id = new_uuid()
    async with factory() as session:
        session.add(Ticket(id=ticket_id, title=TITLE, kind=AGENT, origin="user"))
        await session.commit()
    session_id = await seed_session(factory, kind="worker", ticket_id=ticket_id)
    run_id = await seed_run(factory, session_id=session_id, ticket_id=ticket_id)
    return session_id, run_id, ticket_id


def _registry(*, dangerous: bool = True) -> ToolRegistry:
    async def handler(params, _context):
        return {"accepted": params.value}

    registry = ToolRegistry()
    registry.register(
        ToolDefinition(
            name="dangerous_action",
            group="ops",
            risk=ToolRisk.DANGEROUS,
            description="test action",
            params_model=Params,
            handler=handler,
        )
    )
    if not dangerous:
        registry.register(
            ToolDefinition(
                name="safe_action",
                group="status",
                risk=ToolRisk.SAFE,
                description="safe test action",
                params_model=Params,
                handler=handler,
            )
        )
    return registry


def test_confirmation_origin_persisted_and_on_wire():
    async def scenario():
        engine, factory = _database()
        async with engine.begin() as connection:
            await connection.run_sync(SQLModel.metadata.create_all)
        events = []
        calls = []

        async def handler(params, _context):
            calls.append(params.value)
            return {"accepted": params.value}

        registry = ToolRegistry()
        registry.register(
            ToolDefinition(
                name="dangerous_action",
                group="ops",
                risk=ToolRisk.DANGEROUS,
                description="test action",
                params_model=Params,
                handler=handler,
            )
        )
        service = ConfirmationService(
            factory,
            registry,
            policy=PolicyEngine(),
            broadcast=lambda kind, data: events.append((kind, data)),
        )
        session_id, run_id, ticket_id = await _seed_ticket_and_run(factory)
        origin = ToolOrigin(run_id=run_id, ticket_id=ticket_id, agent=AGENT)
        context = ToolContext(
            CallerType.INTERNAL, session_id, "request-origin", _request(), None, origin
        )

        accepted = await service.invoke(
            "dangerous_action", {"value": 7}, context, mode="async"
        )

        async with factory() as session:
            row = await ConfirmationRepository(session).get(
                accepted["confirmation_id"]
            )
            assert row is not None
            assert (row.origin_run_id, row.origin_ticket_id, row.origin_agent) == (
                run_id,
                ticket_id,
                AGENT,
            )
            audit = await AuditRepository(session).get(accepted["audit_id"])
            assert audit is not None
            assert (audit.run_id, audit.ticket_id, audit.agent_name) == (
                run_id,
                ticket_id,
                AGENT,
            )

        expected_origin = {
            "ticket_id": ticket_id,
            "ticket_title": TITLE,
            "agent": AGENT,
            "run_id": run_id,
        }
        confirm_events = [data for kind, data in events if kind == "confirm_request"]
        assert len(confirm_events) == 1
        assert confirm_events[0]["origin"] == expected_origin

        detail = await service.get(accepted["confirmation_id"])
        assert detail["origin"] == expected_origin
        listed = await service.list()
        assert listed["items"][0]["origin"] == expected_origin

        await service.resolve(
            accepted["confirmation_id"],
            approved=True,
            resolved_by="web",
            request=_request(),
        )
        await service._workers[accepted["confirmation_id"]]
        assert calls == [7]

        async with factory() as session:
            audit = await AuditRepository(session).get(accepted["audit_id"])
            assert audit.status == AuditStatus.SUCCESS
            assert (audit.run_id, audit.ticket_id, audit.agent_name) == (
                run_id,
                ticket_id,
                AGENT,
            )
        assert (await service.get(accepted["confirmation_id"]))["origin"] == expected_origin
        await engine.dispose()

    asyncio.run(scenario())


def test_question_origin_persisted_and_on_wire():
    async def scenario():
        engine, factory = _database()
        async with engine.begin() as connection:
            await connection.run_sync(SQLModel.metadata.create_all)
        events: list[tuple[str, dict]] = []
        service = QuestionService(
            factory, broadcast=lambda kind, data: events.append((kind, data))
        )
        session_id, run_id, ticket_id = await _seed_ticket_and_run(factory)
        origin = ToolOrigin(run_id=run_id, ticket_id=ticket_id, agent=AGENT)
        expected_origin = {
            "ticket_id": ticket_id,
            "ticket_title": TITLE,
            "agent": AGENT,
            "run_id": run_id,
        }

        wire, audit_id = await service.create(
            session_id=session_id,
            tool_call_id="call-1",
            question="走哪个客户端渠道？",
            options=OPTIONS,
            multiple=False,
            request_id="req-1",
            origin=origin,
        )
        assert wire["origin"] == expected_origin
        assert events[0][0] == "question_request"
        assert events[0][1]["origin"] == expected_origin

        async with factory() as session:
            row = await AgentQuestionRepository(session).get(wire["question_id"])
            assert row is not None
            assert (row.origin_run_id, row.origin_ticket_id, row.origin_agent) == (
                run_id,
                ticket_id,
                AGENT,
            )
            audit = await AuditRepository(session).get(audit_id)
            assert audit is not None
            assert (audit.run_id, audit.ticket_id, audit.agent_name) == (
                run_id,
                ticket_id,
                AGENT,
            )

        assert (await service.get(wire["question_id"]))["origin"] == expected_origin
        listed = await service.list(session_id=session_id)
        assert listed["items"][0]["origin"] == expected_origin

        answered = await service.answer(
            wire["question_id"], selected=[OPTIONS[0]["label"]]
        )
        assert answered["status"] == QuestionStatus.ANSWERED
        assert answered["origin"] == expected_origin
        resolved = [data for kind, data in events if kind == "question_resolved"]
        assert resolved and resolved[-1]["origin"] == expected_origin
        await engine.dispose()

    asyncio.run(scenario())


def test_rest_calls_have_null_origin():
    async def scenario():
        engine, factory = _database()
        async with engine.begin() as connection:
            await connection.run_sync(SQLModel.metadata.create_all)
        events = []
        service = ConfirmationService(
            factory,
            _registry(dangerous=False),
            policy=PolicyEngine(),
            broadcast=lambda kind, data: events.append((kind, data)),
        )
        context = ToolContext(CallerType.REST, None, "request-rest", _request(), None)

        accepted = await service.invoke(
            "dangerous_action", {"value": 1}, context, mode="async"
        )
        assert (await service.get(accepted["confirmation_id"]))["origin"] is None
        confirm_events = [data for kind, data in events if kind == "confirm_request"]
        assert confirm_events[0]["origin"] is None
        async with factory() as session:
            audit = await AuditRepository(session).get(accepted["audit_id"])
            assert (audit.run_id, audit.ticket_id, audit.agent_name) == (
                None,
                None,
                None,
            )
        await service.resolve(
            accepted["confirmation_id"],
            approved=False,
            resolved_by="web",
            request=_request(),
        )
        await service._workers[accepted["confirmation_id"]]

        # 免确认路径走 ``_create_audit``，同样不写三列。
        result = await service.invoke(
            "safe_action", {"value": 3}, context, mode="sync"
        )
        async with factory() as session:
            audit = await AuditRepository(session).get(result["audit_id"])
            assert audit.status == AuditStatus.SUCCESS
            assert (audit.run_id, audit.ticket_id, audit.agent_name) == (
                None,
                None,
                None,
            )

        # 提问同理：没有 origin 时 wire 里是 null，列是 NULL。
        session_id = await seed_session(factory)
        questions = QuestionService(factory)
        wire, audit_id = await questions.create(
            session_id=session_id,
            tool_call_id="call-rest",
            question="选哪个客户端？",
            options=OPTIONS,
            multiple=False,
        )
        assert wire["origin"] is None
        async with factory() as session:
            row = await AgentQuestionRepository(session).get(wire["question_id"])
            assert (row.origin_run_id, row.origin_ticket_id, row.origin_agent) == (
                None,
                None,
                None,
            )
            audit = await AuditRepository(session).get(audit_id)
            assert (audit.run_id, audit.ticket_id, audit.agent_name) == (
                None,
                None,
                None,
            )
        assert (await questions.list(session_id=session_id))["items"][0]["origin"] is None
        await engine.dispose()

    asyncio.run(scenario())
