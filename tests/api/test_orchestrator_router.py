"""总控 REST 契约（M70 规格 §4.3.1、§6）。

用例按生产装配的最小集搭 app：装总控路由、统一错误处理器与 ``app.state`` 上的
真实 :class:`OrchestratorService`；会话与消息写进 ``tests/api/conftest.py`` 的
``isolated_db`` 临时库，不碰真实 ``resource/maa_api.db``。

模型路由用替身（本文件只验证路由契约，不跑模型）；SSE 端点用替身
``RunEventStream``，避免用例等一条永远不会到来的 ``run_end``。
"""

from __future__ import annotations

import asyncio
from datetime import datetime
from types import SimpleNamespace

import pytest
from fastapi import FastAPI
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlmodel import SQLModel

import maa_api.db.models  # noqa: F401  # 注册全部表
from maa_api.agent.harness.events import RunEventBus
from maa_api.agent.orchestrator.service import OrchestratorService
from maa_api.agent.runs import RunService
from maa_api.agent.tickets.inbox import InboxService
from maa_api.api.errors import register_exception_handlers
from maa_api.api.routers import orchestrator as orchestrator_router
from maa_api.db import session as db_session
from maa_api.db.models import AgentInbox, AgentMessage, AgentRun
from maa_api.db.repositories.agent import (
    AgentMessageRepository,
    AgentRunRepository,
)

#: ``OrchestratorStateModel`` 的 5 个字段（规格 §4.3.1）。
STATE_FIELDS = {
    "session_id",
    "status",
    "turn_running",
    "pending_inbox",
    "archived_count",
}
#: ``AgentMessageItem`` 的 19 个字段（v2 message_wire + run_id、inbox_id、
#: M72 的 inbox_kind、inbox_thread_key）。
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
}


class _ModelRouter:
    """只返回一个档案；``profile`` 的字段为空时 ``ensure_ready`` 报未配置。"""

    def __init__(self) -> None:
        self.profile = SimpleNamespace(base_url="", api_key="", model="")
        self.error: Exception | None = None

    async def resolve(self, _role):
        if self.error is not None:
            raise self.error
        return self.profile


class _FakeStream:
    """替身 SSE 流：路由只要 ``close`` / ``track`` / ``frames`` 三件事。"""

    def __init__(self, app, *, session_id: str) -> None:
        self.app = app
        self.session_id = session_id
        self.tracked: int | None = None
        self.closed = False

    def track(self, inbox_id: int) -> None:
        self.tracked = int(inbox_id)

    def close(self) -> None:
        self.closed = True

    async def frames(self):
        if False:  # pragma: no cover - 只为让类型成为异步生成器
            yield ""


@pytest.fixture
def orchestrator_env(isolated_db, tmp_settings):
    """临时库上的总控服务与只装总控路由的 app。"""

    async def prepare() -> None:
        async with isolated_db.begin() as connection:
            await connection.run_sync(SQLModel.metadata.create_all)

    asyncio.run(prepare())
    # 种子连接属于 asyncio.run 的事件循环，交给 TestClient 的循环之前先回收。
    isolated_db.sync_engine.dispose()
    factory = db_session.session_factory

    app = FastAPI()
    register_exception_handlers(app)
    app.include_router(orchestrator_router.router)

    router = _ModelRouter()
    run_service = RunService(factory, boot_id="test-boot")
    service = OrchestratorService(
        app=app,
        session_factory=factory,
        spec_registry=SimpleNamespace(
            get=lambda _name: SimpleNamespace(spec_version="spec-1")
        ),
        run_service=run_service,
        inbox_service=InboxService(factory),
        ticket_service=None,
        question_service=None,
        confirmation_service=None,
        model_router=router,
        event_bus=RunEventBus(),
    )
    app.state.orchestrator_service = service
    return SimpleNamespace(
        app=app,
        factory=factory,
        service=service,
        router=router,
        run_service=run_service,
    )


@pytest.fixture
def fake_streams(monkeypatch):
    """把路由模块里的 ``RunEventStream`` 换成替身，返回创建过的实例列表。"""
    created: list[_FakeStream] = []

    class _Recorder(_FakeStream):
        def __init__(self, app, *, session_id: str) -> None:
            super().__init__(app, session_id=session_id)
            created.append(self)

    monkeypatch.setattr(orchestrator_router, "RunEventStream", _Recorder)
    return created


def _client(env, make_client):
    return make_client(env.app)


def _error(response):
    return response.json()["error"]


def _configure(env) -> None:
    env.router.profile = SimpleNamespace(
        base_url="https://llm.test/v1", api_key="k", model="m"
    )


def test_get_orchestrator_creates_session_once(orchestrator_env, make_client):
    client = _client(orchestrator_env, make_client)

    first = client.get("/api/agent/orchestrator")
    second = client.get("/api/agent/orchestrator")
    assert first.status_code == 200, first.text
    assert second.status_code == 200, second.text
    payload = first.json()
    assert set(payload) == STATE_FIELDS
    assert payload["session_id"] == second.json()["session_id"]
    assert payload["status"] == "active"
    assert payload["turn_running"] is False
    assert payload["pending_inbox"] == 0
    assert payload["archived_count"] == 0


def test_send_message_validation_and_llm_not_configured(
    orchestrator_env, make_client, fake_streams
):
    client = _client(orchestrator_env, make_client)

    empty = client.post("/api/agent/orchestrator/messages", json={"content": ""})
    assert empty.status_code == 422, empty.text
    assert _error(empty)["code"] == "VALIDATION_ERROR"

    mentions = [
        {"type": "knowledge", "id": f"k-{index}", "label": "知识"}
        for index in range(21)
    ]
    too_many = client.post(
        "/api/agent/orchestrator/messages",
        json={"content": "你好", "mentions": mentions},
    )
    assert too_many.status_code == 422, too_many.text
    assert _error(too_many)["code"] == "VALIDATION_ERROR"

    # 模型档案为空：ensure_ready 在写任何东西之前失败。
    missing = client.post(
        "/api/agent/orchestrator/messages", json={"content": "你好"}
    )
    assert missing.status_code == 503, missing.text
    assert _error(missing)["code"] == "LLM_NOT_CONFIGURED"
    assert fake_streams == []


def test_mentions_are_stored_in_inbox_payload(
    orchestrator_env, make_client, fake_streams
):
    _configure(orchestrator_env)
    client = _client(orchestrator_env, make_client)
    mentions = [
        {"type": "knowledge", "id": "k-1", "label": "刷关知识"},
        {"type": "ticket", "id": "t-1", "label": "日常"},
    ]

    response = client.post(
        "/api/agent/orchestrator/messages",
        json={"content": "按这个做", "mentions": mentions},
    )
    assert response.status_code == 200, response.text
    assert response.headers["content-type"].startswith("text/event-stream")
    assert len(fake_streams) == 1
    stream = fake_streams[0]

    async def load():
        async with orchestrator_env.factory() as session:
            items = (
                await session.execute(
                    select(AgentInbox)
                    .where(AgentInbox.session_id == stream.session_id)
                    .order_by(AgentInbox.id.asc())
                )
            ).scalars().all()
            messages = (
                await session.execute(
                    select(AgentMessage)
                    .where(AgentMessage.session_id == stream.session_id)
                    .order_by(AgentMessage.seq.asc())
                )
            ).scalars().all()
        return items, messages

    items, messages = asyncio.run(load())
    assert [item.kind for item in items] == ["user_message"]
    assert items[0].payload["mentions"] == mentions
    assert items[0].payload["text"] == "按这个做"
    assert stream.tracked == items[0].id
    # 用户消息在写入时已落库：run_id / inbox_id 为空，转出的消息才有。
    assert len(messages) == 1
    assert messages[0].role == "user"
    assert messages[0].content == "按这个做"
    assert messages[0].run_id is None
    assert messages[0].inbox_id is None


def test_list_messages_interrupt_and_reset(
    orchestrator_env, make_client, monkeypatch
):
    client = _client(orchestrator_env, make_client)
    session_id = client.get("/api/agent/orchestrator").json()["session_id"]

    async def seed() -> None:
        inbox = InboxService(orchestrator_env.factory)
        item = await inbox.put(session_id, "system", payload={"text": "外部事件"})
        async with orchestrator_env.factory() as session:
            await AgentRunRepository(session).create(
                AgentRun(
                    id="run-1",
                    root_run_id="run-1",
                    session_id=session_id,
                    agent_name="orchestrator",
                    kind="orchestrator_turn",
                    status="succeeded",
                    boot_id="test-boot",
                    started_at=datetime(2026, 11, 5, 8, 0, 0),
                    finished_at=datetime(2026, 11, 5, 8, 0, 1),
                )
            )
            repository = AgentMessageRepository(session)
            await repository.create(
                AgentMessage(
                    session_id=session_id,
                    seq=0,
                    role="user",
                    content="第一条",
                    created_at=datetime(2026, 11, 5, 8, 0, 0),
                )
            )
            await repository.create(
                AgentMessage(
                    session_id=session_id,
                    seq=1,
                    role="assistant",
                    content="回答",
                    run_id="run-1",
                    inbox_id=int(item.id),
                    created_at=datetime(2026, 11, 5, 8, 0, 1),
                )
            )
            await session.commit()

    asyncio.run(seed())

    page = client.get("/api/agent/orchestrator/messages")
    assert page.status_code == 200, page.text
    payload = page.json()
    assert (payload["total"], payload["page"], payload["size"]) == (2, 1, 20)
    assert [item["seq"] for item in payload["items"]] == [0, 1]
    for item in payload["items"]:
        assert set(item) == MESSAGE_FIELDS
        assert item["created_at"].endswith("Z")
    assert payload["items"][1]["run_id"] == "run-1"
    assert payload["items"][1]["inbox_id"] == 1
    assert payload["items"][0]["run_id"] is None

    after = client.get("/api/agent/orchestrator/messages?after_seq=0")
    assert after.status_code == 200, after.text
    assert [item["seq"] for item in after.json()["items"]] == [1]

    bad_page = client.get("/api/agent/orchestrator/messages?size=0")
    assert bad_page.status_code == 400, bad_page.text
    assert _error(bad_page)["code"] == "INVALID_PAGINATION"

    stopped = client.post("/api/agent/orchestrator/interrupt")
    assert stopped.status_code == 200, stopped.text
    assert stopped.json() == {
        "session_id": session_id,
        "interrupted": False,
        "turn_running": False,
    }
    again = client.post("/api/agent/orchestrator/interrupt")
    assert again.status_code == 200, again.text
    assert again.json()["interrupted"] is False

    monkeypatch.setattr(orchestrator_env.service, "turn_running", lambda: True)
    refused = client.post("/api/agent/orchestrator/reset")
    assert refused.status_code == 409, refused.text
    assert _error(refused)["code"] == "RUN_ALREADY_ACTIVE"


def test_orchestrator_messages_carry_inbox_kind_and_thread_key(
    orchestrator_env, make_client
):
    """M72 §4.1：v3 列表带收件箱归属；普通用户消息两个字段为 null。"""
    client = _client(orchestrator_env, make_client)
    session_id = client.get("/api/agent/orchestrator").json()["session_id"]

    async def seed() -> int:
        async with orchestrator_env.factory() as session:
            inbox_item = AgentInbox(
                session_id=session_id,
                kind="report",
                thread_key="ticket:t1",
                payload={"text": "工单汇报"},
            )
            session.add(inbox_item)
            await session.commit()
            repository = AgentMessageRepository(session)
            await repository.create(
                AgentMessage(
                    session_id=session_id,
                    seq=0,
                    role="user",
                    content="普通用户消息",
                )
            )
            await repository.create(
                AgentMessage(
                    session_id=session_id,
                    seq=1,
                    role="user",
                    content="【工单汇报】做完了",
                    inbox_id=int(inbox_item.id),
                )
            )
            await session.commit()
        return int(inbox_item.id)

    inbox_id = asyncio.run(seed())

    list_response = client.get("/api/agent/orchestrator/messages")
    assert list_response.status_code == 200, list_response.text
    items = list_response.json()["items"]
    assert [item["seq"] for item in items] == [0, 1]
    assert items[0]["content"] == "普通用户消息"
    assert items[0]["inbox_id"] is None
    assert items[0]["inbox_kind"] is None
    assert items[0]["inbox_thread_key"] is None
    assert items[1]["inbox_id"] == inbox_id
    assert items[1]["inbox_kind"] == "report"
    assert items[1]["inbox_thread_key"] == "ticket:t1"
