"""M21 提问 REST 契约（规格 §3.4）：四个端点、鉴权、错误码与请求模型约束。"""

from __future__ import annotations

import asyncio
import contextlib
import sqlite3
from collections.abc import Callable, Sequence
from contextlib import asynccontextmanager
from datetime import timedelta

import pytest
from fastapi import FastAPI
from sqlalchemy.ext.asyncio import AsyncEngine
from sqlmodel import SQLModel

import maa_api.db.models  # noqa: F401
from maa_api.agent.questions import QuestionService
from maa_api.api import deps
from maa_api.api.errors import register_exception_handlers
from maa_api.api.routers import questions as questions_router
from maa_api.db import session as db_session
from maa_api.db.models import AgentSession, utcnow
from maa_api.db.repositories.agent import AgentSessionRepository

QUESTION_FIELDS = {
    "question_id",
    "session_id",
    "tool_call_id",
    "question",
    "options",
    "multiple",
    "status",
    "selection",
    "created_at",
    "expires_at",
    "answered_at",
    "answered_by",
}
SESSION_ID = "session-1"
OPTIONS = [
    {"label": "官服（Official）", "description": "官方客户端"},
    {"label": "B 服", "description": None},
]


def _spec(**overrides) -> dict:
    """一条待建提问的参数；用例只在需要时覆盖个别字段。"""
    spec = {
        "session_id": SESSION_ID,
        "tool_call_id": "call-1",
        "question": "走哪个客户端渠道？",
        "options": OPTIONS,
        "multiple": False,
        "request_id": "req-1",
    }
    spec.update(overrides)
    return spec


def _setup(
    isolated_db: AsyncEngine,
    make_client,
    *,
    timeout_seconds: int = 600,
    clock: Callable[[], object] | None = None,
    seeds: Sequence[dict] = (),
):
    """临时库 + 只装提问路由的 app；返回 ``(client, service, 建好的提问 wire)``。

    提问在 lifespan 里建：它跑在 TestClient 自己的事件循环里，和随后的请求共用同一个
    引擎连接池。若改用 ``asyncio.run`` 建行，aiosqlite 的连接会跨事件循环复用，直接
    炸 ``MissingGreenlet``（``isolated_db`` 的引擎没有用 NullPool）。
    """

    async def create_schema():
        async with isolated_db.begin() as connection:
            await connection.run_sync(SQLModel.metadata.create_all)

    asyncio.run(create_schema())
    isolated_db.sync_engine.dispose()
    options = {"timeout_seconds": timeout_seconds}
    if clock is not None:
        options["clock"] = clock
    # 生产装配同款：服务自己拿 session_factory（这里已被 isolated_db 换成临时库）。
    service = QuestionService(db_session.session_factory, **options)
    created: list[dict] = []

    @asynccontextmanager
    async def lifespan(_app: FastAPI):
        # 临时库开着外键约束（apply_pragmas），提问的 session_id 必须先有会话行。
        async with db_session.session_factory() as session:
            repository = AgentSessionRepository(session)
            for session_id in dict.fromkeys(spec["session_id"] for spec in seeds):
                if await repository.get(session_id) is None:
                    await repository.create(
                        AgentSession(id=session_id, model="test-model")
                    )
            await session.commit()
        for spec in seeds:
            wire, _audit_id = await service.create(**spec)
            created.append(wire)
        yield

    app = FastAPI(lifespan=lifespan)
    register_exception_handlers(app)
    app.include_router(questions_router.router)
    app.state.question_service = service
    return make_client(app), service, created


def _error(response):
    return response.json()["error"]


def _confirmation_count() -> int:
    """直连临时库读计数：不碰异步引擎，也就没有跨事件循环的问题。"""
    with contextlib.closing(sqlite3.connect(db_session.DB_PATH)) as conn:
        return int(conn.execute("select count(*) from confirmation").fetchone()[0])


# ---------------------------------------------------------------------------
# 成功路径
# ---------------------------------------------------------------------------
def test_questions_list_filters_by_session_and_status(isolated_db, make_client, tmp_settings):
    client, _service, created = _setup(
        isolated_db,
        make_client,
        seeds=[
            _spec(tool_call_id="call-1"),
            _spec(tool_call_id="call-2"),
            _spec(session_id="session-2", tool_call_id="call-3"),
        ],
    )
    pending, second, other_session = created
    assert client.post(
        f"/api/agent/questions/{pending['question_id']}", json={"selected": ["B 服"]}
    ).status_code == 200

    listed = client.get("/api/agent/questions")
    assert listed.status_code == 200
    payload = listed.json()
    assert payload["total"] == 2 and payload["page"] == 1 and payload["size"] == 20
    assert [item["question_id"] for item in payload["items"]] == [
        other_session["question_id"],
        second["question_id"],
    ]
    for item in payload["items"]:
        assert set(item) == QUESTION_FIELDS
        assert item["status"] == "pending"
        assert item["answered_at"] is None and item["answered_by"] is None

    scoped = client.get(
        "/api/agent/questions", params={"session_id": "session-1", "status": "pending"}
    ).json()
    assert scoped["total"] == 1
    assert scoped["items"][0]["session_id"] == "session-1"

    answered = client.get(
        "/api/agent/questions", params={"status": "answered"}
    ).json()
    assert answered["total"] == 1
    assert answered["items"][0]["question_id"] == pending["question_id"]
    assert answered["items"][0]["selection"] == ["B 服"]

    assert client.get("/api/agent/questions", params={"status": "skipped"}).json()[
        "total"
    ] == 0
    assert client.get("/api/agent/questions", params={"status": "no-such"}).status_code == 422
    paginated = client.get(
        "/api/agent/questions", params={"page": 2, "size": 1, "status": "pending"}
    ).json()
    assert paginated["page"] == 2 and paginated["size"] == 1 and paginated["total"] == 2
    assert len(paginated["items"]) == 1


def test_questions_list_rejects_out_of_range_pagination(isolated_db, make_client, tmp_settings):
    client, _service, _created = _setup(isolated_db, make_client)

    for params in ({"page": 0}, {"size": 0}, {"size": 201}):
        response = client.get("/api/agent/questions", params=params)
        assert response.status_code == 400, response.text
        assert _error(response)["code"] == "INVALID_PAGINATION"


def test_question_detail_and_answer_round_trip(isolated_db, make_client, tmp_settings):
    client, _service, created = _setup(isolated_db, make_client, seeds=[_spec()])
    wire = created[0]

    detail = client.get(f"/api/agent/questions/{wire['question_id']}")
    assert detail.status_code == 200
    assert detail.json() == wire

    answered = client.post(
        f"/api/agent/questions/{wire['question_id']}",
        json={"selected": ["官服（Official）"]},
    )
    assert answered.status_code == 200, answered.text
    payload = answered.json()
    assert payload["status"] == "answered"
    assert payload["selection"] == ["官服（Official）"]
    assert payload["answered_by"] == "web"
    assert payload["answered_at"].endswith("Z")
    assert payload["question_id"] == wire["question_id"]

    # 已答的卡片不再出现在默认（pending）列表里；详情反映终态。
    assert client.get("/api/agent/questions").json()["total"] == 0
    assert client.get(
        f"/api/agent/questions/{wire['question_id']}"
    ).json()["selection"] == ["官服（Official）"]
    # 提问不是确认：整条链路一行 confirmation 都不该有。
    assert _confirmation_count() == 0


def test_question_skip_is_idempotent_by_state_machine(isolated_db, make_client, tmp_settings):
    client, _service, created = _setup(isolated_db, make_client, seeds=[_spec()])
    wire = created[0]

    skipped = client.post(f"/api/agent/questions/{wire['question_id']}/skip")
    assert skipped.status_code == 200, skipped.text
    assert skipped.json()["status"] == "skipped"
    assert skipped.json()["selection"] is None
    assert skipped.json()["answered_by"] == "web"

    repeated = client.post(f"/api/agent/questions/{wire['question_id']}/skip")
    assert repeated.status_code == 409
    assert _error(repeated)["code"] == "QUESTION_ALREADY_ANSWERED"
    assert _confirmation_count() == 0


# ---------------------------------------------------------------------------
# 错误路径
# ---------------------------------------------------------------------------
def test_question_endpoints_report_not_found_for_unknown_id(
    isolated_db, make_client, tmp_settings
):
    client, _service, _created = _setup(isolated_db, make_client)

    for response in (
        client.get("/api/agent/questions/nope"),
        client.post("/api/agent/questions/nope", json={"selected": ["B 服"]}),
        client.post("/api/agent/questions/nope/skip"),
    ):
        assert response.status_code == 404, response.text
        assert _error(response)["code"] == "QUESTION_NOT_FOUND"


def test_question_answer_rejects_invalid_selections(isolated_db, make_client, tmp_settings):
    client, _service, created = _setup(isolated_db, make_client, seeds=[_spec()])
    endpoint = f"/api/agent/questions/{created[0]['question_id']}"

    for body in (
        {"selected": []},
        {"selected": ["不存在的选项"]},
        {"selected": ["官服（Official）", "B 服"]},
    ):
        response = client.post(endpoint, json=body)
        assert response.status_code == 400, response.text
        assert _error(response)["code"] == "QUESTION_SELECTION_INVALID"
        assert _error(response)["details"]["multiple"] is False
        assert client.get(endpoint).json()["status"] == "pending"

    # 请求模型自己拦下的形状问题仍是 422（缺字段、多余字段）。
    assert client.post(endpoint, json={}).status_code == 422
    assert client.post(endpoint, json={"selected": ["B 服"], "extra": 1}).status_code == 422
    # 被拒绝过三次的卡片仍能正常回答。
    assert client.post(endpoint, json={"selected": ["B 服"]}).status_code == 200
    assert client.post(endpoint, json={"selected": ["官服（Official）"]}).status_code == 409


def test_question_answer_and_skip_report_expired_after_the_deadline(
    isolated_db, make_client, tmp_settings
):
    current = [utcnow()]
    client, _service, created = _setup(
        isolated_db,
        make_client,
        timeout_seconds=60,
        clock=lambda: current[0],
        seeds=[_spec(tool_call_id="call-1"), _spec(tool_call_id="call-2")],
    )
    wire, skipped_candidate = created

    current[0] = current[0] + timedelta(seconds=61)

    for response in (
        client.post(
            f"/api/agent/questions/{wire['question_id']}", json={"selected": ["B 服"]}
        ),
        client.post(f"/api/agent/questions/{wire['question_id']}/skip"),
        client.post(f"/api/agent/questions/{skipped_candidate['question_id']}/skip"),
    ):
        assert response.status_code == 409, response.text
        assert _error(response)["code"] == "QUESTION_EXPIRED"

    # 回答超时把行推到 expired 终态（不是留在 pending）。
    assert client.get(f"/api/agent/questions/{wire['question_id']}").json()[
        "status"
    ] == "expired"
    assert client.get("/api/agent/questions").json()["total"] == 0


# ---------------------------------------------------------------------------
# 鉴权与 OpenAPI
# ---------------------------------------------------------------------------
@pytest.mark.parametrize("tmp_settings", ["questions-secret"], indirect=True)
def test_questions_endpoints_require_auth(isolated_db, make_client, tmp_settings):
    client, _service, created = _setup(isolated_db, make_client, seeds=[_spec()])
    wire = created[0]
    deps.reset_rate_limiter()
    try:
        unauthenticated = [
            client.get("/api/agent/questions"),
            client.get(f"/api/agent/questions/{wire['question_id']}"),
            client.post(
                f"/api/agent/questions/{wire['question_id']}", json={"selected": ["B 服"]}
            ),
            client.post(f"/api/agent/questions/{wire['question_id']}/skip"),
        ]
        for response in unauthenticated:
            assert response.status_code == 401, response.text
            assert _error(response)["code"] == "UNAUTHORIZED"

        headers = {"X-Token": "questions-secret"}
        assert client.get("/api/agent/questions", headers=headers).status_code == 200
    finally:
        deps.reset_rate_limiter()


def test_questions_openapi_declares_paths_and_error_codes(
    isolated_db, make_client, tmp_settings
):
    client, _service, _created = _setup(isolated_db, make_client)
    paths = client.get("/openapi.json").json()["paths"]

    assert set(paths["/api/agent/questions"]) == {"get"}
    detail = paths["/api/agent/questions/{question_id}"]
    assert set(detail) == {"get", "post"}
    assert set(paths["/api/agent/questions/{question_id}/skip"]) == {"post"}

    def declared(path: str, method: str, status_code: int) -> str:
        return paths[path][method]["responses"][str(status_code)]["description"]

    assert "UNAUTHORIZED" in declared("/api/agent/questions", "get", 401)
    assert "INVALID_PAGINATION" in declared("/api/agent/questions", "get", 400)
    assert "QUESTION_NOT_FOUND" in declared(
        "/api/agent/questions/{question_id}", "get", 404
    )
    answer_conflict = declared("/api/agent/questions/{question_id}", "post", 409)
    assert "QUESTION_ALREADY_ANSWERED" in answer_conflict
    assert "QUESTION_EXPIRED" in answer_conflict
    assert "QUESTION_SELECTION_INVALID" in declared(
        "/api/agent/questions/{question_id}", "post", 400
    )
    skip_conflict = declared("/api/agent/questions/{question_id}/skip", "post", 409)
    assert "QUESTION_ALREADY_ANSWERED" in skip_conflict
    assert "QUESTION_EXPIRED" in skip_conflict
