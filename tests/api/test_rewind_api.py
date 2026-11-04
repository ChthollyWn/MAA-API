"""M80 T7：回溯与分叉的 REST 契约（规格 §4.3、§6 的两条用例）。

只装回溯与工单两个路由、统一错误处理器与 ``app.state.rewind_service``（分叉还
需要 ``app.state.ticket_service`` 的 ``detail``）。回溯用替身服务钉住状态码、响应
字段与错误体；分叉用临时库与真实 ``TicketDetail`` 转换，钉住 201、``Location``、
``forked_from`` 与 operationId。
"""

from __future__ import annotations

import asyncio
from dataclasses import replace

from fastapi import FastAPI
from sqlalchemy.ext.asyncio import AsyncEngine
from sqlmodel import SQLModel

import maa_api.db.models  # noqa: F401  # 注册全部表
from maa_api.agent.context.rewind import RewindResult, SideEffect
from maa_api.agent.tickets.service import TicketDetailData
from maa_api.api.errors import register_exception_handlers
from maa_api.api.routers import rewind as rewind_router
from maa_api.api.routers import tickets as tickets_router
from maa_api.db import session as db_session
from maa_api.db.models import AgentSession, Mandate, Ticket
from maa_api.db.repositories.ticket import MandateRepository, TicketRepository
from maa_api.domain.enums import EventSource, MandateStrength, RiskLevel, TicketOrigin
from maa_api.domain.errors import AppError, ErrorCode
from maa_api.main import create_app

#: ``RewindResultOut`` 的 10 个字段（规格 §4.3）。
REWIND_FIELDS = {
    "session_id",
    "to_seq",
    "dry_run",
    "superseded",
    "superseded_events",
    "checkpoints_invalidated",
    "checkpoint_reactivated",
    "plan_abandoned",
    "side_effects",
    "notice_message_id",
}
#: ``RewindSideEffectOut`` 的 4 个字段。
SIDE_EFFECT_FIELDS = {"seq", "tool_name", "audit_id", "detail"}
#: ``TicketDetail`` 的 9 个字段（M80 加 ``forked_from``）。
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
#: 一次正常的回溯结果：字段值与替身服务的返回对应。
REWIND_RESULT = RewindResult(
    session_id="session-1",
    to_seq=3,
    dry_run=False,
    superseded=2,
    superseded_events=1,
    checkpoints_invalidated=1,
    checkpoint_reactivated="cp-older",
    plan_abandoned=True,
    side_effects=(
        SideEffect(
            seq=4,
            tool_name="submit_pipeline",
            audit_id=17,
            detail="流水线 1f3e9a2c",
        ),
    ),
    notice_message_id=11,
)


class _RewindStub:
    """按 ``error`` 抛错；记录调用参数。"""

    def __init__(self, *, error: AppError | None = None) -> None:
        self.error = error
        self.calls: list[tuple[str, int, bool]] = []

    async def rewind(self, session_id: str, to_seq: int, *, dry_run: bool = False):
        self.calls.append((session_id, to_seq, dry_run))
        if self.error is not None:
            raise self.error
        return replace(
            REWIND_RESULT,
            session_id=session_id,
            to_seq=to_seq,
            dry_run=dry_run,
        )


class _ForkStub:
    def __init__(self, new_ticket_id: str) -> None:
        self.new_ticket_id = new_ticket_id
        self.error: AppError | None = None
        self.calls: list[tuple[str, int]] = []

    async def fork_ticket(self, ticket_id: str, at_seq: int) -> str:
        self.calls.append((ticket_id, at_seq))
        if self.error is not None:
            raise self.error
        return self.new_ticket_id


class _TicketServiceStub:
    """用真实仓储读工单与委托，返回 ``TicketDetailData``。"""

    async def detail(self, ticket_id: str) -> TicketDetailData:
        async with db_session.session_factory() as db:
            ticket = await TicketRepository(db).get(ticket_id)
            mandate = await MandateRepository(db).get_by_ticket(ticket_id)
        assert ticket is not None and mandate is not None
        return TicketDetailData(
            ticket=ticket,
            mandate=mandate,
            subscriptions=[],
            recent_reports=[],
            turn_running=False,
        )


def _app(rewind_service=None, ticket_service=None) -> FastAPI:
    app = FastAPI()
    register_exception_handlers(app)
    app.include_router(rewind_router.router)
    app.include_router(tickets_router.router)
    if rewind_service is not None:
        app.state.rewind_service = rewind_service
    if ticket_service is not None:
        app.state.ticket_service = ticket_service
    return app


def _error(response):
    return response.json()["error"]


async def _create_tables(engine: AsyncEngine) -> None:
    async with engine.begin() as conn:
        await conn.run_sync(SQLModel.metadata.create_all)


def test_rewind_endpoint_contract(tmp_settings, make_client) -> None:
    """200/400/404/409/503、响应字段与 operationId。"""

    service = _RewindStub()
    client = make_client(_app(service))

    response = client.post(
        "/api/agent/sessions/session-1/rewind", json={"to_seq": 3}
    )
    assert response.status_code == 200, response.text
    payload = response.json()
    assert set(payload) == REWIND_FIELDS
    assert payload["session_id"] == "session-1"
    assert payload["to_seq"] == 3
    assert payload["dry_run"] is False
    assert payload["superseded"] == 2
    assert payload["superseded_events"] == 1
    assert payload["checkpoints_invalidated"] == 1
    assert payload["checkpoint_reactivated"] == "cp-older"
    assert payload["plan_abandoned"] is True
    assert payload["notice_message_id"] == 11
    assert set(payload["side_effects"][0]) == SIDE_EFFECT_FIELDS
    assert payload["side_effects"][0]["detail"] == "流水线 1f3e9a2c"
    assert service.calls == [("session-1", 3, False)]

    dry_run = client.post(
        "/api/agent/sessions/session-1/rewind", json={"to_seq": 3, "dry_run": True}
    )
    assert dry_run.status_code == 200, dry_run.text
    assert dry_run.json()["dry_run"] is True
    assert service.calls[-1] == ("session-1", 3, True)

    # 请求体校验：多一个键、to_seq 小于 -1 都是 422。
    for body in ({"to_seq": 3, "extra": 1}, {"to_seq": -2}):
        invalid = client.post(
            "/api/agent/sessions/session-1/rewind", json=body
        )
        assert invalid.status_code == 422, invalid.text

    # 400：目标不合法，details.reason 原样透出。
    service.error = AppError(
        ErrorCode.REWIND_TARGET_INVALID, "目标不合法", {"reason": "splits_tool_batch"}
    )
    bad_target = client.post("/api/agent/sessions/session-1/rewind", json={"to_seq": 2})
    assert bad_target.status_code == 400, bad_target.text
    assert _error(bad_target)["code"] == "REWIND_TARGET_INVALID"
    assert _error(bad_target)["details"]["reason"] == "splits_tool_batch"

    # 404：会话不存在。
    service.error = AppError(
        ErrorCode.AGENT_SESSION_NOT_FOUND, "没有这个会话", {"session_id": "nope"}
    )
    missing = client.post("/api/agent/sessions/nope/rewind", json={"to_seq": 1})
    assert missing.status_code == 404, missing.text
    assert _error(missing)["code"] == "AGENT_SESSION_NOT_FOUND"

    # 409：活动运行 / 待处理事项、工单已结束。
    service.error = AppError(
        ErrorCode.RUN_ALREADY_ACTIVE,
        "会话里还有运行",
        {"pending": {"confirmations": 1, "questions": 0}},
    )
    active = client.post("/api/agent/sessions/session-1/rewind", json={"to_seq": 1})
    assert active.status_code == 409, active.text
    assert _error(active)["details"]["pending"]["confirmations"] == 1
    service.error = AppError(ErrorCode.TICKET_ENDED, "工单已结束", {"ticket_id": "t1"})
    ended = client.post("/api/agent/sessions/session-1/rewind", json={"to_seq": 1})
    assert ended.status_code == 409, ended.text
    assert _error(ended)["code"] == "TICKET_ENDED"

    # 503：服务还没装配。
    missing_service = make_client(_app())
    disabled = missing_service.post(
        "/api/agent/sessions/session-1/rewind", json={"to_seq": 1}
    )
    assert disabled.status_code == 503, disabled.text
    assert _error(disabled)["code"] == "AGENT_DISABLED"

    schema = create_app().openapi()
    operation = schema["paths"]["/api/agent/sessions/{session_id}/rewind"]["post"]
    assert operation["operationId"] == "agent_rewind_session"
    assert operation["tags"] == ["agent"]
    body_schema = schema["components"]["schemas"]["RewindResultOut"]["properties"]
    assert set(body_schema) == REWIND_FIELDS


def test_fork_endpoint_contract(
    tmp_settings, isolated_db, make_client
) -> None:
    """201、``Location``、``TicketDetail`` 字段、``forked_from`` 与 operationId。"""

    asyncio.run(_create_tables(isolated_db))

    async def seed() -> tuple[str, str, str]:
        async with db_session.session_factory() as db:
            source = Ticket(
                title="日常",
                kind="daily_ops",
                status="done",
                origin="user",
            )
            db.add(source)
            await db.flush()
            source_session = AgentSession(
                title="源工单会话", model="worker-model", kind="worker", ticket_id=source.id
            )
            db.add(source_session)
            await db.flush()
            source.worker_session_id = source_session.id
            db.add(
                Mandate(
                    ticket_id=source.id,
                    scope="把日常做完",
                    strength=MandateStrength.EXPLICIT.value,
                    source=EventSource.WEB.value,
                    sender="web",
                    risk_ceiling=RiskLevel.CONSUME.value,
                    policy_version="abc123",
                )
            )
            new = Ticket(
                title="日常（分叉）",
                kind="daily_ops",
                status="open",
                origin="user",
            )
            db.add(new)
            await db.flush()
            new_session = AgentSession(
                title="源工单会话",
                model="worker-model",
                kind="worker",
                ticket_id=new.id,
                forked_from_session_id=source_session.id,
                forked_from_seq=3,
            )
            db.add(new_session)
            await db.flush()
            new.worker_session_id = new_session.id
            db.add(
                Mandate(
                    ticket_id=new.id,
                    scope="把日常做完",
                    strength=MandateStrength.EXPLICIT.value,
                    source=EventSource.WEB.value,
                    sender="web",
                    risk_ceiling=RiskLevel.CONSUME.value,
                    policy_version="abc123",
                )
            )
            await db.commit()
            return str(source.id), str(new.id), str(source_session.id)

    source_id, new_id, source_session_id = asyncio.run(seed())
    fork = _ForkStub(new_id)
    client = make_client(
        _app(fork, _TicketServiceStub())
    )

    response = client.post(
        f"/api/agent/tickets/{source_id}/fork", json={"at_seq": 3}
    )
    assert response.status_code == 201, response.text
    assert response.headers["Location"] == f"/api/agent/tickets/{new_id}"
    payload = response.json()
    assert set(payload) == DETAIL_FIELDS
    assert payload["ticket"]["id"] == new_id
    assert payload["ticket"]["status"] == "open"
    assert payload["worker_session_id"] is not None
    assert payload["lease"] is None
    assert payload["forked_from"] == {
        "ticket_id": source_id,
        "title": "日常",
        "at_seq": 3,
    }
    assert payload["mandate"]["strength"] == "explicit"
    assert payload["mandate"]["risk_ceiling"] == "consume"
    assert fork.calls == [(source_id, 3)]

    # 404：源工单不存在；400：目标落在工具批次中间。
    fork.error = AppError(
        ErrorCode.TICKET_NOT_FOUND, "没有这个工单", {"ticket_id": source_id}
    )
    missing = client.post(
        f"/api/agent/tickets/{source_id}/fork", json={"at_seq": 3}
    )
    assert missing.status_code == 404, missing.text
    assert _error(missing)["code"] == "TICKET_NOT_FOUND"
    fork.error = AppError(
        ErrorCode.REWIND_TARGET_INVALID, "目标不合法", {"reason": "not_found"}
    )
    bad_target = client.post(
        f"/api/agent/tickets/{source_id}/fork", json={"at_seq": 3}
    )
    assert bad_target.status_code == 400, bad_target.text
    assert _error(bad_target)["details"]["reason"] == "not_found"
    fork.error = None

    invalid = client.post(
        f"/api/agent/tickets/{source_id}/fork", json={"at_seq": -1}
    )
    assert invalid.status_code == 422, invalid.text
    invalid_extra = client.post(
        f"/api/agent/tickets/{source_id}/fork", json={"at_seq": 3, "extra": True}
    )
    assert invalid_extra.status_code == 422, invalid_extra.text

    schema = create_app().openapi()
    operation = schema["paths"]["/api/agent/tickets/{ticket_id}/fork"]["post"]
    assert operation["operationId"] == "agent_fork_ticket"
    assert operation["tags"] == ["agent"]
    assert operation["responses"]["201"]["headers"]["Location"]["schema"]["type"] == "string"
    detail_schema = schema["components"]["schemas"]["TicketDetail"]["properties"]
    assert set(detail_schema) == DETAIL_FIELDS
    assert detail_schema["forked_from"]["anyOf"][0]["$ref"].endswith("/ForkedFrom")
