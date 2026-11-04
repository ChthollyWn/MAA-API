"""M74 T5：durable 状态的收集与渲染（spec §4.2.2、§6 的两条用例）。

异步用例沿用 M69 的写法：同步测试函数 + ``asyncio.run(scenario())``；临时库用
``tests/fakes/v3_db.make_session_factory``。这里钉的是「系统直接读出来的事实」：
Worker 的工单、委托、本工单待确认与本会话待提问；总控的进行中工单及其尾部段。
"""

from __future__ import annotations

import asyncio
from datetime import datetime
from types import SimpleNamespace

from sqlalchemy import select

from maa_api.agent.context.durable import (
    DurableContextMiddleware,
    DurableState,
    collect_durable_state,
    render_durable,
    render_open_tickets,
)
from maa_api.agent.context.layout import (
    TAIL_HEADER,
    ContextLayout,
    ContextParts,
    parts_of,
)
from maa_api.agent.context.tokens import TokenEstimator
from maa_api.agent.harness.context import ModelRequest, RunContext
from maa_api.agent.llm.profiles import ResolvedProfile
from maa_api.agent.specs.registry import builtin_spec
from maa_api.db.models import AgentQuestion, Confirmation, Mandate, Ticket
from tests.fakes.v3_db import make_session_factory, seed_session

# 固定 id 让渲染文案可以逐字断言（前 4 位就是渲染里的短 id）。
_TICKET_ID = "7c2e0000-0000-4000-8000-000000000001"
_WAITING_TICKET_ID = "91af0000-0000-4000-8000-000000000002"
_DONE_TICKET_ID = "dead0000-0000-4000-8000-000000000003"
_OTHER_TICKET_ID = "bad00000-0000-4000-8000-000000000004"
_CONFIRMATION_ID = "4d0b0000-0000-4000-8000-000000000005"
_OTHER_CONFIRMATION_ID = "dead0000-0000-4000-8000-000000000006"
_QUESTION_ID = "e7c10000-0000-4000-8000-000000000007"
_OTHER_QUESTION_ID = "f00d0000-0000-4000-8000-000000000008"

_FIXED_TIME = datetime(2026, 11, 4, 12, 0, 0)


def _profile() -> ResolvedProfile:
    return ResolvedProfile(
        name="durable",
        kind="chat",
        provider="openai_compatible",
        base_url="https://llm.test/v1",
        api_key="sk-test",
        model="durable-model",
        context_window=None,
        max_output_tokens=None,
        supports_vision=None,
        supports_tools=True,
        supports_json_schema=None,
        dynamic_context_position="tail_system",
        price_input=None,
        price_cached_input=None,
        price_output=None,
        currency="CNY",
        embedding_dim=None,
        local_model_path=None,
        readonly=False,
    )


def _context(
    spec_name: str,
    *,
    app: object,
    session_id: str,
    ticket_id: str | None = None,
    state: dict | None = None,
) -> RunContext:
    return RunContext(
        run_id="run-durable",
        root_run_id="run-durable",
        parent_run_id=None,
        session_id=session_id,
        ticket_id=ticket_id,
        spec=builtin_spec(spec_name),
        app=app,
        state=dict(state or {}),
    )


def test_worker_state_has_ticket_mandate_and_pending(tmp_path) -> None:
    engine, factory = asyncio.run(make_session_factory(tmp_path / "durable-worker.db"))

    async def scenario() -> None:
        async with factory() as db:
            db.add(
                Ticket(
                    id=_TICKET_ID,
                    title="刷 1-7",
                    kind="general",
                    status="open",
                    origin="user",
                    priority=1,
                    last_report_status="progress",
                )
            )
            db.add(
                Ticket(
                    id=_OTHER_TICKET_ID,
                    title="另一个工单",
                    kind="general",
                    status="open",
                    origin="user",
                )
            )
            await db.commit()
        worker_session = await seed_session(
            factory, kind="worker", ticket_id=_TICKET_ID
        )
        other_session = await seed_session(factory, kind="worker")
        async with factory() as db:
            db.add(
                Mandate(
                    ticket_id=_TICKET_ID,
                    scope="刷 1-7 到 50 个固源岩",
                    strength="explicit",
                    source="user",
                    risk_ceiling="consume",
                )
            )
            db.add(
                Confirmation(
                    id=_CONFIRMATION_ID,
                    action="submit_pipeline",
                    risk_level="consume",
                    reason="使用 2 瓶理智药",
                    status="pending",
                    requested_by="agent",
                    expires_at=datetime(2026, 11, 4, 12, 10, 0),
                    origin_ticket_id=_TICKET_ID,
                    created_at=_FIXED_TIME,
                )
            )
            # 别的工单的待确认不在本工单的 durable 里。
            db.add(
                Confirmation(
                    id=_OTHER_CONFIRMATION_ID,
                    action="update_core",
                    risk_level="destructive",
                    reason="更新内核",
                    status="pending",
                    requested_by="agent",
                    expires_at=datetime(2026, 11, 4, 12, 10, 0),
                    origin_ticket_id=_OTHER_TICKET_ID,
                    created_at=_FIXED_TIME,
                )
            )
            db.add(
                AgentQuestion(
                    id=_QUESTION_ID,
                    session_id=worker_session,
                    tool_call_id="call-q",
                    question="要刷到多少个？",
                    status="pending",
                    expires_at=datetime(2026, 11, 4, 12, 10, 0),
                    created_at=_FIXED_TIME,
                )
            )
            # 别的会话的待回答提问不在本会话的 durable 里。
            db.add(
                AgentQuestion(
                    id=_OTHER_QUESTION_ID,
                    session_id=other_session,
                    tool_call_id="call-other-q",
                    question="别的问题",
                    status="pending",
                    expires_at=datetime(2026, 11, 4, 12, 10, 0),
                    created_at=_FIXED_TIME,
                )
            )
            await db.commit()

        app = SimpleNamespace(state=SimpleNamespace(session_factory=factory))
        ctx = _context(
            "general",
            app=app,
            session_id=worker_session,
            ticket_id=_TICKET_ID,
            state={"skills": {"loaded": ["skill-a"]}},
        )
        await DurableContextMiddleware().on_run_start(ctx)
        state = ctx.state["durable"]
        assert isinstance(state, DurableState)
        assert state.ticket == {
            "id": _TICKET_ID,
            "title": "刷 1-7",
            "status": "open",
            "last_report_status": "progress",
        }
        assert state.mandate == {
            "scope": "刷 1-7 到 50 个固源岩",
            "strength": "explicit",
            "risk_ceiling": "consume",
        }
        assert state.skills == ["skill-a"]
        assert state.pending == [
            {
                "kind": "confirmation",
                "id": _CONFIRMATION_ID,
                "tool": "submit_pipeline",
                "risk": "consume",
                "text": "使用 2 瓶理智药",
            },
            {"kind": "question", "id": _QUESTION_ID, "text": "要刷到多少个？"},
        ]
        assert render_durable(state) == (
            "工单：「刷 1-7」（7c2e…，open）\n"
            "委托：刷 1-7 到 50 个固源岩（explicit，风险上限 consume）\n"
            "待处理：\n"
            "- 确认 submit_pipeline（4d0b…，consume）：使用 2 瓶理智药\n"
            "- 提问（e7c1…）：要刷到多少个？"
        )

        # 总控口径（不传工单）只取进行中的工单与全部待处理；这里没有 open 工单。
        async with factory() as db:
            for ticket_id in (_TICKET_ID, _OTHER_TICKET_ID):
                ticket = await db.get(Ticket, ticket_id)
                assert ticket is not None
                ticket.status = "done"
            await db.commit()
        orchestrator = await collect_durable_state(
            app.state, session_id=worker_session, session_kind="orchestrator", ticket_id=None
        )
        assert orchestrator.ticket is None
        assert orchestrator.mandate is None
        assert orchestrator.open_tickets == []
        assert {item["id"] for item in orchestrator.pending} == {
            _CONFIRMATION_ID,
            _OTHER_CONFIRMATION_ID,
            _QUESTION_ID,
            _OTHER_QUESTION_ID,
        }

    try:
        asyncio.run(scenario())
    finally:
        engine.sync_engine.dispose()


def test_orchestrator_tail_lists_open_tickets(tmp_path) -> None:
    engine, factory = asyncio.run(
        make_session_factory(tmp_path / "durable-orchestrator.db")
    )

    async def scenario() -> None:
        orchestrator_session = await seed_session(factory, kind="orchestrator")
        async with factory() as db:
            db.add_all(
                [
                    Ticket(
                        id=_TICKET_ID,
                        title="刷 1-7",
                        kind="general",
                        status="open",
                        origin="user",
                        priority=0,
                        updated_at=datetime(2026, 11, 4, 11, 0, 0),
                    ),
                    Ticket(
                        id=_WAITING_TICKET_ID,
                        title="诊断崩溃",
                        kind="diagnose",
                        status="waiting",
                        origin="user",
                        priority=1,
                        updated_at=datetime(2026, 11, 4, 10, 0, 0),
                        human_action={
                            "type": "decision",
                            "prompt": "批准计划「排查内核崩溃」？",
                        },
                    ),
                    Ticket(
                        id=_DONE_TICKET_ID,
                        title="已经做完的工单",
                        kind="general",
                        status="done",
                        origin="user",
                        priority=0,
                        updated_at=datetime(2026, 11, 4, 12, 0, 0),
                    ),
                ]
            )
            await db.commit()

        layout = ContextLayout(TokenEstimator())
        app = SimpleNamespace(
            state=SimpleNamespace(session_factory=factory, context_layout=layout)
        )
        ctx = _context(
            "orchestrator", app=app, session_id=orchestrator_session
        )
        middleware = DurableContextMiddleware()
        await middleware.on_run_start(ctx)
        durable = ctx.state["durable"]
        assert durable.ticket is None and durable.mandate is None
        assert [item["id"] for item in durable.open_tickets] == [
            _TICKET_ID,
            _WAITING_TICKET_ID,
        ]
        assert render_open_tickets(durable.open_tickets) == (
            "进行中的工单：\n"
            "- 「刷 1-7」open（7c2e…）\n"
            "- 「诊断崩溃」waiting（91af…），需要你：批准计划「排查内核崩溃」？"
        )

        profile = _profile()
        parts = ContextParts(
            history=[{"role": "user", "content": "现在有几件事？"}],
            tools=[],
            position="tail_system",
        )
        req = ModelRequest(messages=[], tools=[], profile=profile)
        req = layout.apply(ctx, req, parts, profile=profile)
        req = await middleware.before_model(ctx, req)
        tail_text = req.messages[-1]["content"]
        assert isinstance(tail_text, str)
        assert tail_text.startswith(TAIL_HEADER)
        assert "进行中的工单：" in tail_text
        assert "「刷 1-7」open（7c2e…）" in tail_text
        assert "「诊断崩溃」waiting（91af…）" in tail_text
        assert "已经做完的工单" not in tail_text
        assert [segment.name for segment in parts_of(req).tail] == ["tickets"]

        # 没有进行中的工单时不插 tickets 段（done 不算进行中）。
        async with factory() as db:
            result = await db.execute(
                select(Ticket).where(
                    Ticket.id.in_([_TICKET_ID, _WAITING_TICKET_ID])
                )
            )
            for ticket in result.scalars().all():
                ticket.status = "done"
            await db.commit()
        await middleware.on_run_start(ctx)
        parts2 = ContextParts(
            history=[{"role": "user", "content": "还有事吗？"}],
            tools=[],
            position="tail_system",
        )
        req2 = ModelRequest(messages=[], tools=[], profile=profile)
        req2 = layout.apply(ctx, req2, parts2, profile=profile)
        req2 = await middleware.before_model(ctx, req2)
        assert parts_of(req2).tail == []
        assert all(
            "进行中的工单" not in str(message.get("content"))
            for message in req2.messages
        )
        assert render_open_tickets([]) == ""

    try:
        asyncio.run(scenario())
    finally:
        engine.sync_engine.dispose()
