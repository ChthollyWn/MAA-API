"""M90 T4：生成式表单提交端点（spec §4.3.3、§4.5.2、§6）。

装配与 ``test_ticket_messages_router.py`` 同款：临时库 + 真 ``TicketService`` /
``WorkerService`` / ``InboxService``，再加真 ``ArtifactService`` 并挂到
``app.state.artifact_service``。视图产物直接用服务写库，提交走 HTTP；写消息与
``meta`` 的结果再回库核对。

覆盖：成功写一条【表单提交】用户消息并唤醒 Worker；非法值带字段错误；非表单、
损坏视图、无工单、超限、结束工单、无产物的拒绝路径；``meta`` 只留最近 5 条且
读改写后写覆盖先写；路由挂在 agent 组。
"""

from __future__ import annotations

import asyncio
import json
from types import SimpleNamespace

import pytest
from fastapi import FastAPI
from sqlalchemy import select
from sqlmodel import SQLModel

import maa_api.db.models  # noqa: F401  # 注册全部表
from maa_api.agent.artifacts.service import ArtifactService
from maa_api.agent.artifacts.views import VIEW_MIME
from maa_api.agent.orchestrator.service import OrchestratorService
from maa_api.agent.runs import RunService
from maa_api.agent.tickets.inbox import InboxService
from maa_api.agent.tickets.service import TicketService
from maa_api.agent.tickets.threads import ThreadRouter
from maa_api.agent.workers.service import WorkerService
from maa_api.api.errors import register_exception_handlers
from maa_api.api.routers import artifacts as artifacts_router
from maa_api.db import session as db_session
from maa_api.db.models import AgentInbox, AgentMessage, AgentSession, Ticket
from maa_api.db.repositories.agent import AgentSessionRepository
from maa_api.domain.enums import EventSource, TicketOrigin
from maa_api.domain.errors import AppError, ErrorCode
from maa_api.main import custom_operation_id

_SPECS: dict[str, tuple[str, str]] = {
    "daily_ops": ("worker", "consume"),
    "general": ("worker", "destructive"),
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


@pytest.fixture
def submit_env(isolated_db, tmp_path, tmp_settings):
    """临时库上的工单 / Worker / 产物服务与只装产物路由的 app。"""

    async def prepare() -> None:
        async with isolated_db.begin() as connection:
            await connection.run_sync(SQLModel.metadata.create_all)

    asyncio.run(prepare())
    isolated_db.sync_engine.dispose()
    factory = db_session.session_factory

    app = FastAPI(generate_unique_id_function=custom_operation_id)
    register_exception_handlers(app)
    app.include_router(artifacts_router.router)

    inbox = InboxService(factory)
    thread_router = ThreadRouter(factory, inbox)
    tickets = TicketService(
        factory,
        inbox_service=inbox,
        thread_router=thread_router,
        spec_registry=_registry(),
    )
    worker = WorkerService(
        app=app,
        session_factory=factory,
        spec_registry=_registry(),
        run_service=RunService(factory, boot_id="test-boot"),
        inbox_service=inbox,
        ticket_service=tickets,
        thread_router=thread_router,
        delegation=None,
        titler=None,
        event_bus=None,
    )
    artifacts = ArtifactService(factory, resource_root=tmp_path / "resource")
    app.state.ticket_service = tickets
    app.state.worker_service = worker
    app.state.artifact_service = artifacts
    return SimpleNamespace(
        app=app,
        factory=factory,
        engine=isolated_db,
        tickets=tickets,
        worker=worker,
        artifacts=artifacts,
    )


def _run(env, coro):
    """在独立事件循环里跑一段。"""
    return asyncio.run(coro)


def _view(*, title: str = "这周刷哪一关") -> dict:
    return {
        "version": 1,
        "title": title,
        "components": [
            {"type": "heading", "text": "参数"},
            {
                "type": "field",
                "field": "select",
                "name": "stage",
                "label": "关卡",
                "options": [
                    {"value": "1-7", "label": "1-7"},
                    {"value": "S4-1", "label": "S4-1"},
                ],
            },
            {"type": "field", "field": "number", "name": "times", "label": "次数"},
            {
                "type": "field",
                "field": "toggle",
                "name": "use_medicine",
                "label": "可以用理智药",
            },
            {"type": "submit"},
        ],
    }


_VALUES = {"stage": "1-7", "times": 10, "use_medicine": False}


def _put(env, **kwargs):
    return _run(env, env.artifacts.put(**kwargs))


def _put_form(env, *, ticket_id: str | None, **kwargs):
    title = kwargs.pop("title", "这周刷哪一关")
    view = kwargs.pop("view", None)
    body = kwargs.pop(
        "body",
        json.dumps(view or _view(title=title), ensure_ascii=False).encode("utf-8"),
    )
    return _put(
        env,
        kind="form",
        title=title,
        body=body,
        mime=VIEW_MIME,
        ticket_id=ticket_id,
        **kwargs,
    )


def _create_ticket(env, *, title: str = "日常", status: str | None = None) -> Ticket:
    ticket, _mandate = _run(
        env,
        env.tickets.create(
            kind="daily_ops",
            title=title,
            scope="把日常做了",
            origin=TicketOrigin.USER,
            source=EventSource.WEB,
            sender="web",
        ),
    )
    if status is not None:

        async def mark() -> None:
            async with env.factory() as session:
                row = await session.get(Ticket, ticket.id)
                assert row is not None
                row.status = status
                await session.commit()

        _run(env, mark())
    return ticket


def _attach_worker(env, ticket: Ticket) -> str:
    """先挂一个 Worker 会话，多次提交才会落进同一个会话。"""

    async def seed() -> str:
        async with env.factory() as session:
            stored = await AgentSessionRepository(session).create(
                AgentSession(
                    model="test-model", kind="worker", ticket_id=ticket.id
                )
            )
            await session.commit()
            return str(stored.id)

    session_id = _run(env, seed())
    _run(
        env,
        env.tickets.attach_worker_session(
            ticket.id, session_id, brief="把日常做了"
        ),
    )
    return session_id


def _bind_orchestrator(env) -> OrchestratorService:
    """真实总控服务：``post_user_message`` 的 passive 摘要落在它的收件箱。"""
    env.app.state.session_factory = env.factory
    orchestrator = OrchestratorService(
        app=env.app,
        session_factory=env.factory,
        spec_registry=_registry(),
        run_service=RunService(env.factory, boot_id="test-boot"),
        inbox_service=InboxService(env.factory),
        ticket_service=env.tickets,
        question_service=None,
        confirmation_service=None,
        model_router=None,
        event_bus=None,
    )
    env.worker.bind_orchestrator(orchestrator)
    return orchestrator


def _load_state(env, ticket_id: str):
    """读 Worker 会话的消息与新收件箱项；返回 ``(messages, items, orchestrator_items)``。"""

    async def load():
        async with env.factory() as session:
            worker_session = (
                await session.execute(
                    select(AgentSession).where(
                        AgentSession.ticket_id == ticket_id,
                        AgentSession.kind == "worker",
                    )
                )
            ).scalars().first()
            messages = []
            items = []
            if worker_session is not None:
                messages = list(
                    (
                        await session.execute(
                            select(AgentMessage)
                            .where(AgentMessage.session_id == str(worker_session.id))
                            .order_by(AgentMessage.seq.asc())
                        )
                    )
                    .scalars()
                    .all()
                )
                items = list(
                    (
                        await session.execute(
                            select(AgentInbox).where(
                                AgentInbox.session_id == str(worker_session.id)
                            )
                        )
                    )
                    .scalars()
                    .all()
                )
            orchestrator_session = (
                await session.execute(
                    select(AgentSession).where(AgentSession.kind == "orchestrator")
                )
            ).scalars().first()
            orchestrator_items = []
            if orchestrator_session is not None:
                orchestrator_items = list(
                    (
                        await session.execute(
                            select(AgentInbox).where(
                                AgentInbox.session_id == str(orchestrator_session.id)
                            )
                        )
                    )
                    .scalars()
                    .all()
                )
        return messages, items, orchestrator_items

    return _run(env, load())


def _worker_messages(env, ticket_id: str) -> list[AgentMessage]:
    return _load_state(env, ticket_id)[0]


def _error(response):
    return response.json()["error"]


def _submit(client, artifact_id: str, values: dict | None = None):
    return client.post(
        f"/api/agent/artifacts/{artifact_id}/submit",
        json={"values": _VALUES if values is None else values},
    )


# ---------------------------------------------------------------------------
# 成功路径
# ---------------------------------------------------------------------------
def test_submit_writes_user_message_into_worker_inbox(submit_env, make_client):
    ticket = _create_ticket(submit_env, title="日常")
    _attach_worker(submit_env, ticket)
    artifact = _put_form(submit_env, ticket_id=ticket.id, title="这周刷哪一关")
    _bind_orchestrator(submit_env)

    with make_client(submit_env.app) as client:
        response = _submit(client, str(artifact.id))

    assert response.status_code == 200, response.text
    payload = response.json()
    assert set(payload) == {"artifact_id", "ticket_id", "submitted_at"}
    assert payload["artifact_id"] == str(artifact.id)
    assert payload["ticket_id"] == ticket.id
    assert payload["submitted_at"].endswith("Z")

    messages, items, orchestrator_items = _load_state(submit_env, ticket.id)
    assert len(messages) == 1
    assert messages[0].role == "user"
    assert messages[0].content.startswith("【表单提交】")
    assert "产物 " + str(artifact.id)[:8] in messages[0].content

    pending = [
        item
        for item in items
        if item.thread_key == f"ticket:{ticket.id}"
        and str(item.kind) == "user_message"
        and str(item.status) == "pending"
    ]
    assert len(pending) == 1
    assert pending[0].payload["text"] == messages[0].content

    system_items = [item for item in orchestrator_items if str(item.kind) == "system"]
    assert len(system_items) == 1
    summary = system_items[0].payload["text"]
    assert summary.endswith("【表单提交】这周刷哪一关")
    assert system_items[0].payload["passive"] is True


# ---------------------------------------------------------------------------
# 校验失败：不写消息、不记 meta
# ---------------------------------------------------------------------------
def test_submit_missing_values_defaults_to_empty_dict(submit_env, make_client):
    """M90 §4.2：``values`` 缺省是空 dict，不是 422；按表单必填项返回 400 字段错误。"""
    ticket = _create_ticket(submit_env)
    artifact = _put_form(
        submit_env, ticket_id=ticket.id, meta={"view_version": 1, "submissions": []}
    )

    with make_client(submit_env.app) as client:
        response = client.post(
            f"/api/agent/artifacts/{artifact.id}/submit", json={}
        )

    assert response.status_code == 400, response.text
    error = _error(response)
    assert error["code"] == "INVALID_PARAMETER"
    assert "fields" in error["details"]
    assert _worker_messages(submit_env, ticket.id) == []


def test_submit_rejects_invalid_values_with_field_errors(submit_env, make_client):
    ticket = _create_ticket(submit_env)
    artifact = _put_form(
        submit_env, ticket_id=ticket.id, meta={"view_version": 1, "submissions": []}
    )

    with make_client(submit_env.app) as client:
        response = _submit(client, str(artifact.id), {"stage": "9-9", "times": "abc"})

    assert response.status_code == 400, response.text
    error = _error(response)
    assert error["code"] == "INVALID_PARAMETER"
    assert error["message"] == "表单有 2 处需要修改"
    assert set(error["details"]["fields"]) == {"stage", "times"}
    assert error["details"]["fields"]["stage"] == "不在可选项里"
    assert error["details"]["fields"]["times"] == "需要填数字"
    assert _worker_messages(submit_env, ticket.id) == []
    detail = _run(submit_env, submit_env.artifacts.get(str(artifact.id)))
    assert detail.artifact.meta.get("submissions", []) == []


def test_submit_rejects_non_form_artifact(submit_env, make_client):
    ticket = _create_ticket(submit_env)
    dashboard = _put(
        submit_env,
        kind="dashboard",
        title="看板",
        body=json.dumps(_view(), ensure_ascii=False).encode("utf-8"),
        mime=VIEW_MIME,
        ticket_id=ticket.id,
    )
    note = _put(
        submit_env,
        kind="note",
        title="笔记",
        body="# 笔记".encode("utf-8"),
        ticket_id=ticket.id,
    )

    with make_client(submit_env.app) as client:
        for artifact in (dashboard, note):
            response = _submit(client, str(artifact.id))
            assert response.status_code == 400, response.text
            assert _error(response)["message"] == "这个产物不是表单"

    assert _worker_messages(submit_env, ticket.id) == []


def test_submit_rejects_corrupt_view(submit_env, make_client):
    ticket = _create_ticket(submit_env)
    broken_json = _put_form(submit_env, ticket_id=ticket.id, body=b"not json")
    bad_view = _put_form(
        submit_env,
        ticket_id=ticket.id,
        body=json.dumps({"version": 2, "title": "旧视图"}, ensure_ascii=False).encode(),
    )

    with make_client(submit_env.app) as client:
        for artifact in (broken_json, bad_view):
            response = _submit(client, str(artifact.id))
            assert response.status_code == 400, response.text
            assert (
                _error(response)["message"] == "表单定义已损坏，请让 Worker 重新生成"
            )

    assert _worker_messages(submit_env, ticket.id) == []


def test_submit_rejects_artifact_without_ticket(submit_env, make_client):
    artifact = _put_form(submit_env, ticket_id=None)

    with make_client(submit_env.app) as client:
        response = _submit(client, str(artifact.id))

    assert response.status_code == 400, response.text
    assert _error(response)["message"] == "这个表单不属于任何工单"


def test_submit_rejects_oversized_values(submit_env, make_client):
    ticket = _create_ticket(submit_env)
    fields = [
        {
            "type": "field",
            "field": "text",
            "name": f"f{index}",
            "label": f"字段 {index}",
            "max_length": 500,
        }
        for index in range(20)
    ]
    view = {"version": 1, "title": "长表单", "components": [*fields, {"type": "submit"}]}
    artifact = _put_form(submit_env, ticket_id=ticket.id, title="长表单", view=view)
    values = {f"f{index}": "x" * 500 for index in range(20)}

    with make_client(submit_env.app) as client:
        response = _submit(client, str(artifact.id), values)

    assert response.status_code == 400, response.text
    error = _error(response)
    assert error["message"] == "提交内容太长"
    assert error["details"]["limit_bytes"] == 4096
    assert _worker_messages(submit_env, ticket.id) == []


def test_submit_on_ended_ticket_returns_ticket_ended(submit_env, make_client):
    ticket = _create_ticket(submit_env, status="done")
    artifact = _put_form(submit_env, ticket_id=ticket.id)

    with make_client(submit_env.app) as client:
        response = _submit(client, str(artifact.id))

    assert response.status_code == 409, response.text
    error = _error(response)
    assert error["code"] == "TICKET_ENDED"
    assert error["message"] == "工单已结束"
    assert _worker_messages(submit_env, ticket.id) == []


def test_submit_after_ticket_deleted_reports_no_ticket(submit_env, make_client):
    ticket = _create_ticket(submit_env)
    artifact = _put_form(submit_env, ticket_id=ticket.id)

    async def delete_ticket() -> None:
        async with submit_env.factory() as session:
            row = await session.get(Ticket, ticket.id)
            assert row is not None
            await session.delete(row)
            await session.commit()

    _run(submit_env, delete_ticket())
    detail = _run(submit_env, submit_env.artifacts.get(str(artifact.id)))
    assert detail.artifact.ticket_id is None  # 外键 SET NULL

    with make_client(submit_env.app) as client:
        response = _submit(client, str(artifact.id))

    assert response.status_code == 400, response.text
    error = _error(response)
    assert error["code"] == "INVALID_PARAMETER"
    assert error["message"] == "这个表单不属于任何工单"
    assert _worker_messages(submit_env, ticket.id) == []


def test_submit_unknown_artifact_returns_404(submit_env, make_client):
    with make_client(submit_env.app) as client:
        response = client.post(
            "/api/agent/artifacts/no-such-artifact/submit",
            json={"values": _VALUES},
        )

    assert response.status_code == 404, response.text
    error = _error(response)
    assert error["code"] == "ARTIFACT_NOT_FOUND"
    assert error["message"] == "产物不存在"


# ---------------------------------------------------------------------------
# meta 提交记录
# ---------------------------------------------------------------------------
def test_submit_keeps_last_five_submissions(submit_env, make_client):
    ticket = _create_ticket(submit_env)
    _attach_worker(submit_env, ticket)
    artifact = _put_form(
        submit_env,
        ticket_id=ticket.id,
        meta={"brief": "旧简报", "view_version": 1, "submissions": []},
    )

    responses = []
    with make_client(submit_env.app) as client:
        for index in range(6):
            response = _submit(
                client,
                str(artifact.id),
                {"stage": "1-7", "times": index + 1, "use_medicine": False},
            )
            assert response.status_code == 200, response.text
            responses.append(response.json())

    detail = _run(submit_env, submit_env.artifacts.get(str(artifact.id)))
    meta = detail.artifact.meta
    assert len(meta["submissions"]) == 5
    assert meta["last_submitted_at"] == responses[-1]["submitted_at"]
    assert [item["at"] for item in meta["submissions"]][-1] == responses[-1][
        "submitted_at"
    ]
    assert meta["submissions"][-1]["values"] == {
        "stage": "1-7",
        "times": 6,
        "use_medicine": False,
    }
    assert meta["brief"] == "旧简报"
    assert meta["view_version"] == 1
    # 六次提交写了六条用户消息；只留五条是 meta 的上限，不是消息的上限。
    messages = _worker_messages(submit_env, ticket.id)
    assert len(messages) == 6
    assert all(message.role == "user" for message in messages)


def test_submission_meta_last_write_wins(submit_env):
    """读改写：后写覆盖先写，钉住并发窗口里会丢一条记录。"""
    artifact = _put_form(submit_env, ticket_id=None)

    async def snapshot() -> dict:
        detail = await submit_env.artifacts.get(str(artifact.id))
        return dict(detail.artifact.meta or {})

    first = _run(submit_env, snapshot())
    second = _run(submit_env, snapshot())
    first["submissions"] = [
        {"at": "2026-12-01T08:00:00Z", "values": {"times": 1}}
    ]
    second["submissions"] = [
        {"at": "2026-12-01T08:00:01Z", "values": {"times": 2}}
    ]

    async def content() -> str:
        detail = await submit_env.artifacts.get(str(artifact.id))
        return detail.content

    body = _run(submit_env, content())
    _run(
        submit_env,
        submit_env.artifacts.update(str(artifact.id), content=body, meta=first),
    )
    _run(
        submit_env,
        submit_env.artifacts.update(str(artifact.id), content=body, meta=second),
    )

    detail = _run(submit_env, submit_env.artifacts.get(str(artifact.id)))
    submissions = detail.artifact.meta["submissions"]
    assert len(submissions) == 1
    assert submissions[0]["values"] == {"times": 2}


# ---------------------------------------------------------------------------
# 路由契约
# ---------------------------------------------------------------------------
def test_submit_route_is_in_agent_group(submit_env):
    schema = submit_env.app.openapi()
    operation = schema["paths"]["/api/agent/artifacts/{artifact_id}/submit"]["post"]

    assert operation["operationId"] == "agent_submit_artifact_form"
    assert operation["tags"] == ["agent"]

