"""会话压缩与检查点 REST 契约（M74 规格 §4.3、§6 的四条用例）。

用例只装 ``agent_sessions`` 路由：会话、消息、运行与检查点写进
``tests/api/conftest.py`` 的 ``isolated_db`` 临时库，不碰真实 ``resource/maa_api.db``。
压缩语义不在这里重测 —— app 上装配的是真实的
:class:`~maa_api.agent.context.compaction.Compactor`，摘要模型由 ``complete_structured``
的替身替换（成功路径根本走不到它：短会话在选边界时就 skip 了）。
"""

from __future__ import annotations

import asyncio
from datetime import timedelta
from types import SimpleNamespace

import pytest
from fastapi import FastAPI
from sqlalchemy import select
from sqlmodel import SQLModel

import maa_api.agent.context.compaction as compaction_module
import maa_api.db.models  # noqa: F401  # 注册全部表
from maa_api.agent.context.compaction import Compactor
from maa_api.agent.llm.profiles import ResolvedProfile
from maa_api.api.errors import register_exception_handlers
from maa_api.api.routers import agent_sessions
from maa_api.db import session as db_session
from maa_api.db.models import (
    AgentMessage,
    AgentRun,
    AgentSession,
    CompactionCheckpoint,
    new_uuid,
    utcnow,
)
from maa_api.domain.enums import CheckpointStatus
from maa_api.domain.errors import AppError, ErrorCode

#: 压缩用例共用的档案：窗口足够大，短历史不会被长单轮保护挡下。
_PROFILE = ResolvedProfile(
    name="compaction",
    kind="chat",
    provider="openai_compatible",
    base_url="https://llm.test/v1",
    api_key="sk-test",
    model="compaction-model",
    context_window=100_000,
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


class _ProfileRouter:
    """模型路由替身：手动压缩只解析档案，不真的调模型。"""

    def __init__(self, profile: ResolvedProfile) -> None:
        self.profile = profile

    async def resolve(self, _role: str) -> ResolvedProfile:
        return self.profile

    async def resolve_for_spec(self, _spec) -> ResolvedProfile:
        return self.profile


@pytest.fixture
def agent_sessions_env(isolated_db, tmp_settings):
    """临时库 + 真压缩器的 app；只装 ``agent_sessions`` 路由。"""

    async def prepare() -> None:
        async with isolated_db.begin() as connection:
            await connection.run_sync(SQLModel.metadata.create_all)

    asyncio.run(prepare())
    # 种子连接属于 asyncio.run 的事件循环，交给 TestClient 的循环之前先回收。
    isolated_db.sync_engine.dispose()
    factory = db_session.session_factory

    app = FastAPI()
    register_exception_handlers(app)
    app.include_router(agent_sessions.router)
    app.state.session_factory = factory
    app.state.model_router = _ProfileRouter(_PROFILE)
    app.state.compactor = Compactor(app.state, factory)
    return SimpleNamespace(app=app, factory=factory)


async def _seed_session(
    factory,
    *,
    kind: str = "orchestrator",
    archived: bool = False,
    agent_name: str | None = "orchestrator",
    run_status: str = "succeeded",
    messages: tuple[tuple[int, str, str], ...] = (),
) -> str:
    """写一条会话（可选一条最近运行与若干消息）并提交，返回会话 id。"""
    session_id = new_uuid()
    async with factory() as db:
        db.add(
            AgentSession(
                id=session_id,
                model="test-model",
                kind=kind,
                archived_at=utcnow() if archived else None,
            )
        )
        # 没有 relationship 的模型不会被 UOW 按外键排序，父行先 flush 一次。
        await db.flush()
        if agent_name is not None:
            run_id = new_uuid()
            db.add(
                AgentRun(
                    id=run_id,
                    root_run_id=run_id,
                    session_id=session_id,
                    agent_name=agent_name,
                    kind="orchestrator_turn",
                    status=run_status,
                    boot_id="boot-test",
                )
            )
        for seq, role, content in messages:
            db.add(
                AgentMessage(
                    session_id=session_id, seq=seq, role=role, content=content
                )
            )
        await db.commit()
    return session_id


def test_compact_skips_short_sessions(agent_sessions_env, make_client):
    """短会话返回 200：``skipped=true``、没有检查点、前后估算相同。"""
    session_id = asyncio.run(
        _seed_session(
            agent_sessions_env.factory,
            messages=((0, "user", "你好"),),
        )
    )
    client = make_client(agent_sessions_env.app)

    response = client.post(f"/api/agent/sessions/{session_id}/compact")

    assert response.status_code == 200, response.text
    body = response.json()
    assert body["skipped"] is True
    assert body["checkpoint_id"] is None
    assert body["tokens_after"] == body["tokens_before"]


def test_compact_rejects_active_runs_and_wrong_kinds(agent_sessions_env, make_client):
    """活动运行 409、不可压缩种类 400、不存在 404。"""
    client = make_client(agent_sessions_env.app)

    async def seed() -> tuple[str, str, str]:
        busy = await _seed_session(
            agent_sessions_env.factory, kind="worker", run_status="running"
        )
        chat = await _seed_session(
            agent_sessions_env.factory, kind="chat", agent_name=None
        )
        archived = await _seed_session(
            agent_sessions_env.factory, kind="orchestrator", archived=True
        )
        return busy, chat, archived

    busy, chat, archived = asyncio.run(seed())

    response = client.post(f"/api/agent/sessions/{busy}/compact")
    assert response.status_code == 409
    assert response.json()["error"]["code"] == "RUN_ALREADY_ACTIVE"

    response = client.post(f"/api/agent/sessions/{chat}/compact")
    assert response.status_code == 400
    assert response.json()["error"] == {
        "code": "INVALID_PARAMETER",
        "message": "这个会话不支持压缩",
    }

    response = client.post(f"/api/agent/sessions/{archived}/compact")
    assert response.status_code == 400
    assert response.json()["error"]["code"] == "INVALID_PARAMETER"

    response = client.post("/api/agent/sessions/no-such-session/compact")
    assert response.status_code == 404
    assert response.json()["error"]["code"] == "AGENT_SESSION_NOT_FOUND"


def test_compact_failure_is_502(agent_sessions_env, make_client, monkeypatch):
    """摘要不可用：502 ``COMPACTION_FAILED``，且不写检查点。"""
    session_id = asyncio.run(
        _seed_session(
            agent_sessions_env.factory,
            messages=(
                (0, "user", "第一条"),
                (1, "assistant", "回复一"),
                (2, "user", "第二条"),
                (3, "assistant", "回复二"),
                (4, "user", "第三条"),
                (5, "assistant", "回复三"),
            ),
        )
    )

    async def _fail(**_kwargs):
        raise AppError(ErrorCode.STRUCTURED_OUTPUT_INVALID, "模型输出不合法")

    monkeypatch.setattr(compaction_module, "complete_structured", _fail)
    client = make_client(agent_sessions_env.app)

    response = client.post(f"/api/agent/sessions/{session_id}/compact")

    assert response.status_code == 502
    error = response.json()["error"]
    assert error["code"] == "COMPACTION_FAILED"
    assert error["details"]["cause"] == "STRUCTURED_OUTPUT_INVALID"

    async def count() -> int:
        async with agent_sessions_env.factory() as db:
            return len((await db.execute(select(CompactionCheckpoint))).scalars().all())

    assert asyncio.run(count()) == 0


def test_list_checkpoints_newest_first(agent_sessions_env, make_client):
    """列表按 ``created_at`` 降序，带 ``degraded`` 与 ``summary``；会话不存在 404。"""
    session_id = new_uuid()
    older, newer = new_uuid(), new_uuid()

    async def seed() -> None:
        async with agent_sessions_env.factory() as db:
            db.add(AgentSession(id=session_id, model="test-model", kind="orchestrator"))
            await db.flush()
            db.add(
                CompactionCheckpoint(
                    id=older,
                    session_id=session_id,
                    run_id=None,
                    upto_seq=8,
                    summary="较早的摘要",
                    summary_json={"degraded": False},
                    durable={},
                    tokens_before=900,
                    tokens_after=300,
                    model="summarizer-model",
                    trigger="auto",
                    status=CheckpointStatus.SUPERSEDED,
                    created_at=utcnow() - timedelta(minutes=2),
                )
            )
            db.add(
                CompactionCheckpoint(
                    id=newer,
                    session_id=session_id,
                    run_id=None,
                    upto_seq=16,
                    summary="按规则生成的摘要",
                    summary_json={"degraded": True},
                    durable={},
                    tokens_before=1200,
                    tokens_after=400,
                    model=None,
                    trigger="manual",
                    status=CheckpointStatus.ACTIVE,
                    created_at=utcnow() - timedelta(minutes=1),
                )
            )
            await db.commit()

    asyncio.run(seed())
    client = make_client(agent_sessions_env.app)

    response = client.get(f"/api/agent/sessions/{session_id}/checkpoints")

    assert response.status_code == 200, response.text
    items = response.json()["items"]
    assert [item["id"] for item in items] == [newer, older]
    assert items[0]["degraded"] is True
    assert items[0]["summary"] == "按规则生成的摘要"
    assert items[0]["model"] is None
    assert items[0]["created_at"].endswith("Z")
    assert items[1]["degraded"] is False
    assert items[1]["summary"] == "较早的摘要"
    assert items[1]["model"] == "summarizer-model"

    missing = client.get("/api/agent/sessions/no-such-session/checkpoints")
    assert missing.status_code == 404
    assert missing.json()["error"]["code"] == "AGENT_SESSION_NOT_FOUND"
