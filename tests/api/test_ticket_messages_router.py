"""工单对话端点（M70 规格 §4.3.1、§6）。

用例按生产装配的最小集搭 app：装 M69 的工单路由、统一错误处理器，``app.state``
上放真实的 ``ticket_service`` 与 ``WorkerService``。工单数据用 M69 的
``TicketService.create`` / ``attach_worker_session`` 写进 ``isolated_db`` 临时库；
Worker 与子代理会话、消息用仓储直接种子，``after_seq`` 的 id 换算才有确定顺序。
"""

from __future__ import annotations

import asyncio
from datetime import datetime, timedelta
from types import SimpleNamespace

import pytest
from fastapi import FastAPI
from sqlalchemy import select
from sqlmodel import SQLModel

import maa_api.db.models  # noqa: F401  # 注册全部表
from maa_api.agent.orchestrator.service import OrchestratorService
from maa_api.agent.runs import RunService
from maa_api.agent.tickets.inbox import InboxService
from maa_api.agent.tickets.service import TicketService
from maa_api.agent.tickets.threads import ThreadRouter
from maa_api.agent.workers.service import WorkerService
from maa_api.api.errors import register_exception_handlers
from maa_api.api.routers import tickets as tickets_router
from maa_api.db import session as db_session
from maa_api.db.models import (
    AgentInbox,
    AgentMessage,
    AgentRun,
    AgentSession,
    Ticket,
)
from maa_api.db.repositories.agent import (
    AgentMessageRepository,
    AgentRunRepository,
    AgentSessionRepository,
)
from maa_api.domain.enums import EventSource, TicketOrigin
from maa_api.domain.errors import AppError, ErrorCode

#: ``TicketDetail`` 的 9 个字段（M71 加 ``lease``，M80 加 ``forked_from``）。
DETAIL_FIELDS = {
    "ticket",
    "result",
    "mandate",
    "subscriptions",
    "recent_reports",
    "worker_session_id",
    "turn_running",
    "lease",
    "forked_from",
}
#: ``AgentMessageItem`` 的 20 个字段（M80 加 ``superseded_at``）。
MESSAGE_FIELDS = {
    "id",
    "session_id",
    "seq",
    "role",
    "content",
    "reasoning_content",
    "tool_calls",
    "tool_call_id",
    "tool_name",
    "audit_id",
    "prompt_tokens",
    "completion_tokens",
    "finish_reason",
    "latency_ms",
    "created_at",
    "run_id",
    "inbox_id",
    "inbox_kind",
    "inbox_thread_key",
    "superseded_at",
}

_SPECS: dict[str, tuple[str, str]] = {
    "daily_ops": ("worker", "consume"),
    "general": ("worker", "destructive"),
    "researcher": ("subagent", "none"),
}


def _registry() -> SimpleNamespace:
    """最小代理定义注册表；未知名字与生产一样抛 ``AGENT_SPEC_NOT_FOUND``。"""

    def get(name: str) -> SimpleNamespace:
        try:
            kind, risk_ceiling = _SPECS[name]
        except KeyError:
            raise AppError(
                ErrorCode.AGENT_SPEC_NOT_FOUND, f"没有名为 {name} 的代理定义"
            ) from None
        return SimpleNamespace(kind=kind, risk_ceiling=risk_ceiling)

    return SimpleNamespace(get=get)


class _Clock:
    """每次调用前进一秒的假时钟，让列表与详情的顺序稳定。"""

    def __init__(self) -> None:
        self._now = datetime(2026, 11, 5, 8, 0, 0)

    def __call__(self) -> datetime:
        self._now += timedelta(seconds=1)
        return self._now


@pytest.fixture
def messages_env(isolated_db, tmp_settings):
    """临时库上的工单 / Worker 服务与只装工单路由的 app。"""

    async def prepare() -> None:
        async with isolated_db.begin() as connection:
            await connection.run_sync(SQLModel.metadata.create_all)

    asyncio.run(prepare())
    # 种子连接属于 asyncio.run 的事件循环，交给 TestClient 的循环之前先回收。
    isolated_db.sync_engine.dispose()
    factory = db_session.session_factory

    app = FastAPI()
    register_exception_handlers(app)
    app.include_router(tickets_router.router)

    inbox = InboxService(factory)
    thread_router = ThreadRouter(factory, inbox)
    service = TicketService(
        factory,
        inbox_service=inbox,
        thread_router=thread_router,
        spec_registry=_registry(),
        clock=_Clock(),
    )
    worker = WorkerService(
        app=app,
        session_factory=factory,
        spec_registry=_registry(),
        run_service=RunService(factory, boot_id="test-boot"),
        inbox_service=inbox,
        ticket_service=service,
        thread_router=thread_router,
        delegation=None,
        titler=None,
        event_bus=None,
    )
    app.state.ticket_service = service
    app.state.worker_service = worker
    return SimpleNamespace(
        app=app,
        factory=factory,
        service=service,
        worker=worker,
    )


def _client(env, make_client):
    return make_client(env.app)


def _error(response):
    return response.json()["error"]


def _create(env, **overrides) -> Ticket:
    values = {
        "kind": "daily_ops",
        "title": "日常",
        "scope": "把日常做了",
        "origin": TicketOrigin.USER,
        "source": EventSource.WEB,
        "sender": "web",
    }
    values.update(overrides)
    ticket, _mandate = asyncio.run(env.service.create(**values))
    return ticket


def _seed_session(
    env,
    *,
    kind: str,
    ticket_id: str | None = None,
    parent_session_id: str | None = None,
) -> str:
    async def seed() -> str:
        async with env.factory() as session:
            stored = await AgentSessionRepository(session).create(
                AgentSession(
                    model="test-model",
                    kind=kind,
                    ticket_id=ticket_id,
                    parent_session_id=parent_session_id,
                )
            )
            await session.commit()
            return str(stored.id)

    return asyncio.run(seed())


def _attach_worker(env, ticket: Ticket) -> str:
    session_id = _seed_session(env, kind="worker", ticket_id=ticket.id)
    asyncio.run(
        env.service.attach_worker_session(
            ticket.id, session_id, brief="把日常做了"
        )
    )
    return session_id


def _seed_run(env, run_id: str, session_id: str) -> None:
    async def seed() -> None:
        async with env.factory() as session:
            await AgentRunRepository(session).create(
                AgentRun(
                    id=run_id,
                    root_run_id=run_id,
                    session_id=session_id,
                    agent_name="general",
                    kind="worker_turn",
                    status="succeeded",
                    boot_id="test-boot",
                    started_at=datetime(2026, 11, 5, 8, 0, 0),
                    finished_at=datetime(2026, 11, 5, 8, 0, 1),
                )
            )
            await session.commit()

    asyncio.run(seed())


def _seed_messages(env, rows: list[tuple[str, int, str, str, str | None]]) -> list[int]:
    """按给定顺序写消息；``rows`` 是 ``(session_id, seq, role, content, run_id)``。"""

    async def seed() -> list[int]:
        async with env.factory() as session:
            repository = AgentMessageRepository(session)
            ids = []
            for index, (session_id, seq, role, content, run_id) in enumerate(rows):
                stored = await repository.create(
                    AgentMessage(
                        session_id=session_id,
                        seq=seq,
                        role=role,
                        content=content,
                        run_id=run_id,
                        created_at=datetime(2026, 11, 5, 8, 1, index),
                    )
                )
                ids.append(int(stored.id))
            await session.commit()
            return ids

    return asyncio.run(seed())


def test_ticket_endpoints_404_409_and_cancel(messages_env, make_client):
    ticket = _create(messages_env, title="日常")
    _attach_worker(messages_env, ticket)
    ended = _create(messages_env, title="已结束")
    asyncio.run(messages_env.service.cancel(ended.id))
    client = _client(messages_env, make_client)

    missing_requests = (
        ("GET", "/api/agent/tickets/nope/messages", None),
        ("POST", "/api/agent/tickets/nope/messages", {"content": "你好"}),
        ("POST", "/api/agent/tickets/nope/interrupt", None),
        ("POST", "/api/agent/tickets/nope/cancel", {}),
    )
    for method, path, body in missing_requests:
        response = client.request(method, path, json=body)
        assert response.status_code == 404, (path, response.text)
        assert _error(response)["code"] == "TICKET_NOT_FOUND"

    ended_message = client.post(
        f"/api/agent/tickets/{ended.id}/messages", json={"content": "还能说话吗"}
    )
    assert ended_message.status_code == 409, ended_message.text
    assert _error(ended_message)["code"] == "TICKET_ENDED"

    ended_cancel = client.post(f"/api/agent/tickets/{ended.id}/cancel", json={})
    assert ended_cancel.status_code == 409, ended_cancel.text
    assert _error(ended_cancel)["code"] == "TICKET_ENDED"

    cancelled = client.post(
        f"/api/agent/tickets/{ticket.id}/cancel", json={"reason": "不要了"}
    )
    assert cancelled.status_code == 200, cancelled.text
    payload = cancelled.json()
    assert set(payload) == DETAIL_FIELDS
    assert payload["ticket"]["id"] == ticket.id
    assert payload["ticket"]["status"] == "cancelled"
    assert payload["result"] == {"cancelled_by": "user", "reason": "不要了"}
    assert payload["turn_running"] is False
    assert payload["recent_reports"][0]["status"] == "dropped"

    # 结束的工单仍可读消息；发消息则 409。
    after = client.get(f"/api/agent/tickets/{ticket.id}/messages")
    assert after.status_code == 200, after.text
    assert after.json()["total"] == 0
    rejected = client.post(
        f"/api/agent/tickets/{ticket.id}/messages", json={"content": "再补一句"}
    )
    assert rejected.status_code == 409, rejected.text
    assert _error(rejected)["code"] == "TICKET_ENDED"


def test_list_ticket_messages_with_subagents(messages_env, make_client):
    ticket = _create(messages_env, title="日常")
    worker_id = _attach_worker(messages_env, ticket)
    subagent_id = _seed_session(
        messages_env,
        kind="subagent",
        ticket_id=ticket.id,
        parent_session_id=worker_id,
    )
    _seed_run(messages_env, "run-1", worker_id)
    _seed_run(messages_env, "run-2", subagent_id)
    ids = _seed_messages(
        messages_env,
        [
            (worker_id, 0, "user", "w0", None),
            (worker_id, 1, "assistant", "w1", "run-1"),
            (subagent_id, 0, "assistant", "s0", "run-2"),
            (worker_id, 2, "user", "w2", None),
            (subagent_id, 1, "tool", "s1", None),
        ],
    )
    client = _client(messages_env, make_client)

    def ids_of(query: str) -> list[int]:
        response = client.get(f"/api/agent/tickets/{ticket.id}/messages{query}")
        assert response.status_code == 200, response.text
        payload = response.json()
        for item in payload["items"]:
            assert set(item) == MESSAGE_FIELDS
            assert item["created_at"].endswith("Z")
        return [item["id"] for item in payload["items"]]

    # 默认只列 Worker 会话；为 true 时按 agent_message.id 合并子代理消息。
    assert ids_of("?size=50") == [ids[0], ids[1], ids[3]]
    assert ids_of("?size=50&include_subagents=true") == ids
    # after_seq 指 Worker 会话的 seq，换算成那条消息的 id 后取 id 更大的行。
    assert ids_of("?size=50&include_subagents=true&after_seq=1") == ids[2:]
    assert ids_of("?size=50&after_seq=0") == [ids[1], ids[3]]
    assert ids_of("?size=50&include_subagents=true&after_seq=-1") == ids
    # 超过最大 seq 时，边界是最后一条 Worker 消息，子代理更晚的消息仍保留。
    assert ids_of("?size=50&include_subagents=true&after_seq=99") == [ids[4]]

    page = client.get(
        f"/api/agent/tickets/{ticket.id}/messages?include_subagents=true&page=2&size=2"
    )
    assert page.status_code == 200, page.text
    assert page.json()["page"] == 2
    assert [item["id"] for item in page.json()["items"]] == [ids[2], ids[3]]

    bad_page = client.get(f"/api/agent/tickets/{ticket.id}/messages?size=0")
    assert bad_page.status_code == 400, bad_page.text
    assert _error(bad_page)["code"] == "INVALID_PAGINATION"

    unknown = client.get("/api/agent/tickets/nope/messages")
    assert unknown.status_code == 404, unknown.text
    assert _error(unknown)["code"] == "TICKET_NOT_FOUND"

    # 还没建 Worker 会话的工单返回空页，而不是 404。
    empty_ticket = _create(messages_env, title="还没有 Worker")
    empty = client.get(f"/api/agent/tickets/{empty_ticket.id}/messages")
    assert empty.status_code == 200, empty.text
    assert empty.json() == {"items": [], "total": 0, "page": 1, "size": 20}


def test_ticket_message_summary_excludes_reference_block(
    messages_env, make_client, monkeypatch
):
    """M72 §6：写库正文带【引用】段，给总控的 passive 摘要只引用原话。"""
    messages_env.app.state.session_factory = messages_env.factory

    class _KnowledgeService:
        async def read_for_agent(self, entry_id: str):
            return SimpleNamespace(
                id=entry_id,
                title="刷关知识",
                when_to_use="刷关前",
                body="先看关卡",
            )

    messages_env.app.state.knowledge_service = _KnowledgeService()

    orchestrator = OrchestratorService(
        app=messages_env.app,
        session_factory=messages_env.factory,
        spec_registry=_registry(),
        run_service=RunService(messages_env.factory, boot_id="test-boot"),
        inbox_service=InboxService(messages_env.factory),
        ticket_service=messages_env.service,
        question_service=None,
        confirmation_service=None,
        model_router=None,
        event_bus=None,
    )
    messages_env.worker.bind_orchestrator(orchestrator)

    class _FakeStream:
        def __init__(self, app, *, session_id: str) -> None:
            self.session_id = session_id
            self.tracked: int | None = None

        def track(self, inbox_id: int) -> None:
            self.tracked = int(inbox_id)

        def close(self) -> None:
            return None

        async def frames(self):
            if False:  # pragma: no cover - 只为让类型成为异步生成器
                yield ""

    monkeypatch.setattr(tickets_router, "RunEventStream", _FakeStream)
    ticket = _create(messages_env, title="日常")
    client = _client(messages_env, make_client)

    response = client.post(
        f"/api/agent/tickets/{ticket.id}/messages",
        json={
            "content": "先刷 1-7",
            "mentions": [
                {"type": "knowledge", "id": "k-1", "label": "刷关知识"}
            ],
        },
    )
    assert response.status_code == 200, response.text

    async def load():
        async with messages_env.factory() as session:
            orchestrator_session = (
                (
                    await session.execute(
                        select(AgentSession).where(
                            AgentSession.kind == "orchestrator"
                        )
                    )
                )
                .scalars()
                .first()
            )
            worker_messages = (
                (
                    await session.execute(
                        select(AgentMessage)
                        .join(
                            AgentSession,
                            AgentMessage.session_id == AgentSession.id,
                        )
                        .where(
                            AgentSession.ticket_id == ticket.id,
                            AgentSession.kind == "worker",
                        )
                        .order_by(AgentMessage.seq.asc())
                    )
                )
                .scalars()
                .all()
            )
            items = (
                (
                    await session.execute(
                        select(AgentInbox)
                        .where(
                            AgentInbox.session_id == str(orchestrator_session.id)
                        )
                        .order_by(AgentInbox.id.asc())
                    )
                )
                .scalars()
                .all()
            )
        return worker_messages, items

    worker_messages, items = asyncio.run(load())
    assert worker_messages[0].content == (
        "先刷 1-7\n\n【引用】\n"
        "- 知识「刷关知识」（k-1）：刷关前。正文节选：先看关卡"
    )
    system_items = [item for item in items if str(item.kind) == "system"]
    assert len(system_items) == 1
    summary = system_items[0].payload["text"]
    assert summary == "用户在工单「日常」里说：先刷 1-7"
    assert "【引用】" not in summary
