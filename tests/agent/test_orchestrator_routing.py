"""M73 T3：总控按用户原话选择 Worker 种类（spec §4.7.7、§6）。

真实临时库上装配总控 / Worker 服务与 Agent 运行时，模型换成
``tests/fakes/scripted_llm.ScriptedLLM``：它按系统提示里的身份标记把调用分给
总控队列（``route_by_marker``），脚本里的 ``create_ticket`` 决定落库的 ``kind``。
Worker 服务不 ``start``，工单建好后不会起 Worker 回合，所以脚本只需排总控队列。

脚本模型不是被测对象：用例验证装配把总控的 ``create_ticket(kind=...)`` 原样落到
工单表，并确认总控系统提示里真的带着 §4.7.7 的选种类段——路由规则失效时，
真实模型的种类选择也就没有依据。
"""

from __future__ import annotations

import asyncio
from types import SimpleNamespace
from typing import Any

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker
from sqlalchemy.pool import NullPool
from sqlmodel import SQLModel

import maa_api.db.models  # noqa: F401  # 注册全部表
from maa_api.agent.confirmation import ConfirmationService
from maa_api.agent.harness.events import RunEventBus
from maa_api.agent.orchestrator.service import OrchestratorService
from maa_api.agent.runtime import AgentRuntime
from maa_api.agent.runs.service import RunService
from maa_api.agent.specs.registry import AgentSpecRegistry
from maa_api.agent.tickets.inbox import InboxService
from maa_api.agent.tickets.service import TicketService
from maa_api.agent.tickets.threads import ThreadRouter
from maa_api.agent.tools import build_registry
from maa_api.agent.workers.service import WorkerService
from maa_api.db import session as db_session
from maa_api.db.models import Ticket
from tests.fakes.fake_llm import content_text, text_response, tool_call, tool_response
from tests.fakes.scripted_llm import ScriptedLLM

#: §4.7.7 的两处关键句：分别覆盖「按事选种类」与「一句话两件事」。
_DAILY_RULE = "日常、刷图、换班、公招用 daily_ops"
_TWO_INTENTS_RULE = "用户一句话里有两件事，就建两张工单，不要塞进一张。"

#: 等待脚本模型与泵收尾的期限；超时说明装配或脚本没按预期走。
TIMEOUT_SECONDS = 10.0


class _ModelHandle:
    """``run_agent`` 会对档案句柄调 ``aclose``；底层 ScriptedLLM 跨轮共用。"""

    def __init__(self, llm: ScriptedLLM) -> None:
        self.client = llm

    async def aclose(self) -> None:
        return None


class _ModelRouter:
    """把总控的模型调用交给 ScriptedLLM；档案只用于会话行与能力开关。"""

    profile = SimpleNamespace(
        name="default",
        kind="chat",
        provider="openai_compatible",
        model="test-model",
        base_url="https://llm.test/v1",
        api_key="sk-test",
        context_window=None,
        max_output_tokens=None,
        supports_vision=True,
        supports_tools=True,
        supports_json_schema=True,
        dynamic_context_position="tail_system",
        price_input=None,
        price_cached_input=None,
        price_output=None,
        currency="USD",
        embedding_dim=None,
        local_model_path=None,
        readonly=False,
        key=("default", "test-model"),
    )

    def __init__(self, llm: ScriptedLLM) -> None:
        self.llm = llm

    async def resolve(self, _role: Any) -> Any:
        return self.profile

    async def resolve_for_spec(self, _spec: Any, **_kwargs: Any) -> Any:
        return self.profile

    def client_for(self, _profile: Any) -> _ModelHandle:
        return _ModelHandle(self.llm)


class _Titler:
    async def title_for(self, _text: str) -> str:
        return "自动标题"


class _Harness:
    """临时库上的总控 + Worker 服务；Worker 不启动，不会有 Worker 回合。"""

    def __init__(self, *, engine, factory, llm, orchestrator, worker_service):
        self.engine = engine
        self.factory = factory
        self.llm = llm
        self.orchestrator = orchestrator
        self.worker_service = worker_service

    async def close(self) -> None:
        await self.orchestrator.close()
        await self.worker_service.close()
        await self.engine.dispose()


async def _routing_harness(
    tmp_path, scripts: dict[str, list[list[dict[str, Any]]]]
) -> _Harness:
    """建临时库、真实服务链与脚本化模型；脚本队列按调用方键排。"""
    engine = db_session.make_engine(
        f"sqlite+aiosqlite:///{tmp_path / 'routing.db'}", poolclass=NullPool
    )
    factory = async_sessionmaker(engine, expire_on_commit=False, class_=AsyncSession)
    async with engine.begin() as connection:
        await connection.run_sync(SQLModel.metadata.create_all)

    registry = AgentSpecRegistry(build_registry())
    registry.load()
    inbox_service = InboxService(factory)
    thread_router = ThreadRouter(factory, inbox_service)
    ticket_service = TicketService(
        factory,
        inbox_service=inbox_service,
        thread_router=thread_router,
        spec_registry=registry,
    )
    confirmations = ConfirmationService(factory, build_registry())
    runtime = AgentRuntime(factory, confirmations, question_service=None)
    llm = ScriptedLLM(scripts)
    model_router = _ModelRouter(llm)
    run_service = RunService(factory, boot_id="m73-t3-boot")
    event_bus = RunEventBus()
    state = SimpleNamespace(
        agent_runtime=runtime,
        model_router=model_router,
        run_event_bus=event_bus,
        run_service=run_service,
        ticket_service=ticket_service,
        inbox_service=inbox_service,
        agent_spec_registry=registry,
    )
    app = SimpleNamespace(state=state)
    worker_service = WorkerService(
        app=app,
        session_factory=factory,
        spec_registry=registry,
        run_service=run_service,
        inbox_service=inbox_service,
        ticket_service=ticket_service,
        thread_router=thread_router,
        delegation=SimpleNamespace(),
        titler=_Titler(),
        event_bus=event_bus,
    )
    state.worker_service = worker_service
    orchestrator = OrchestratorService(
        app=app,
        session_factory=factory,
        spec_registry=registry,
        run_service=run_service,
        inbox_service=inbox_service,
        ticket_service=ticket_service,
        question_service=None,
        confirmation_service=confirmations,
        model_router=model_router,
        event_bus=event_bus,
    )
    state.orchestrator_service = orchestrator
    return _Harness(
        engine=engine,
        factory=factory,
        llm=llm,
        orchestrator=orchestrator,
        worker_service=worker_service,
    )


async def _tickets(factory) -> list[Ticket]:
    async with factory() as session:
        result = await session.execute(
            select(Ticket).order_by(Ticket.created_at.asc(), Ticket.id.asc())
        )
        return list(result.scalars().all())


async def _wait_until(predicate, *, message: str, timeout: float = TIMEOUT_SECONDS):
    loop = asyncio.get_running_loop()
    deadline = loop.time() + timeout
    while loop.time() < deadline:
        if await predicate():
            return
        await asyncio.sleep(0.01)
    raise AssertionError(f"{message} 未在 {timeout:.0f}s 内成立")


async def _say(harness: _Harness, content: str, ticket_count: int) -> None:
    """启动总控、投一条用户消息并等这一轮建出 ``ticket_count`` 张工单后收尾。"""
    await harness.orchestrator.start()
    await harness.orchestrator.post_user_message(content, [])
    harness.orchestrator.wake()

    async def _done() -> bool:
        return (
            len(await _tickets(harness.factory)) == ticket_count
            and not harness.orchestrator.turn_running()
            and not any(harness.llm.remaining().values())
        )

    await _wait_until(
        _done, message=f"总控对 {content!r} 建出 {ticket_count} 张工单并收尾"
    )


def _first_system_text(llm: ScriptedLLM) -> str:
    """第一次已记录调用的系统提示正文（``route_by_marker`` 用的那条）。"""
    _key, messages, _tools = llm.requests[0]
    return next(
        content_text(message.get("content"))
        for message in messages
        if str(message.get("role")) == "system"
    )


def _ticket_call(
    call_id: str, *, kind: str, title: str, brief: str, scope: str
) -> dict[str, Any]:
    return tool_call(
        call_id,
        "create_ticket",
        {"kind": kind, "title": title, "brief": brief, "scope": scope},
    )


def test_daily_request_uses_daily_ops(tmp_path):
    """「把日常做了」→ create_ticket 的 kind 落成 daily_ops，而不是 general。"""

    async def scenario() -> None:
        harness = await _routing_harness(
            tmp_path,
            {
                "orchestrator": [
                    tool_response(
                        _ticket_call(
                            "call-daily",
                            kind="daily_ops",
                            title="日常",
                            brief="把今天的日常做完。用户原话：把日常做了。",
                            scope="完成今天的日常",
                        )
                    ),
                    text_response("日常的工单已经交给日常 Worker。"),
                ]
            },
        )
        try:
            await _say(harness, "把日常做了", ticket_count=1)

            tickets = await _tickets(harness.factory)
            assert [ticket.kind for ticket in tickets] == ["daily_ops"]
            assert tickets[0].title == "日常"
            assert _DAILY_RULE in _first_system_text(harness.llm)
            assert _first_system_text(harness.llm).startswith("你是 MAA-API 的总控")
            assert [key for key, _m, _t in harness.llm.requests] == [
                "orchestrator"
            ] * 2
        finally:
            await harness.close()

    asyncio.run(scenario())


def test_two_intents_two_tickets(tmp_path):
    """「做日常并更新资源」→ 总控先后建两张工单：daily_ops 与 ops。"""

    async def scenario() -> None:
        harness = await _routing_harness(
            tmp_path,
            {
                "orchestrator": [
                    tool_response(
                        _ticket_call(
                            "call-daily",
                            kind="daily_ops",
                            title="日常",
                            brief="把今天的日常做完。用户原话：做日常并更新资源。",
                            scope="完成今天的日常",
                        )
                    ),
                    tool_response(
                        _ticket_call(
                            "call-ops",
                            kind="ops",
                            title="更新资源",
                            brief="更新游戏资源。用户原话：做日常并更新资源。",
                            scope="更新游戏资源",
                        )
                    ),
                    text_response("已经分别建了日常和更新资源两张工单。"),
                ]
            },
        )
        try:
            await _say(harness, "做日常并更新资源", ticket_count=2)

            tickets = await _tickets(harness.factory)
            assert [ticket.kind for ticket in tickets] == ["daily_ops", "ops"]
            assert [key for key, _m, _t in harness.llm.requests] == (
                ["orchestrator"] * 3
            )
            assert _TWO_INTENTS_RULE in _first_system_text(harness.llm)
        finally:
            await harness.close()

    asyncio.run(scenario())
