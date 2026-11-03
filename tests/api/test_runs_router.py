"""运行查询 REST 契约（M66 规格 §4.3.2、§6）：分页、过滤、详情树、错误与 OpenAPI。

用例按生产装配的最小集搭 app：只装 runs 路由、统一错误处理器和
``app.state.run_service``；运行与服务直接写进 ``isolated_db`` 的临时库，不碰真实
``resource/maa_api.db``。分页边界由路由自己判断，因此坏参数用例不需要服务先连库。
"""

from __future__ import annotations

import asyncio
from datetime import datetime

import pytest
from fastapi import FastAPI
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker
from sqlmodel import SQLModel

import maa_api.db.models  # noqa: F401  # 注册全部表，create_all 才能建出 agent_run
from maa_api.agent.runs import RunService
from maa_api.api import deps
from maa_api.api.errors import register_exception_handlers
from maa_api.api.routers import runs as runs_router
from maa_api.db.models import AgentRun, AgentSession
from maa_api.domain.enums import AgentRunKind, AgentRunStatus
from maa_api.main import create_app

#: 测试进程的 boot_id；详情响应里的 ``boot_id`` 必须原样带出。
BOOT_ID = "test-boot"
MODEL = "deepseek-flash"
#: 倒序断言用的三个时间点（naive UTC，与库里的口径一致）。
T1 = datetime(2026, 11, 5, 8, 0, 0)
T2 = datetime(2026, 11, 5, 8, 1, 0)
T3 = datetime(2026, 11, 5, 8, 2, 0)

#: ``RunSummary`` 的 24 个字段（规格 §4.3.1）；``RunDetail`` 在此基础上多 4 个。
SUMMARY_FIELDS = {
    "run_id",
    "session_id",
    "ticket_id",
    "parent_run_id",
    "root_run_id",
    "agent_name",
    "spec_version",
    "kind",
    "status",
    "trigger_seq",
    "model",
    "model_profile",
    "prompt_tokens",
    "completion_tokens",
    "cached_tokens",
    "reasoning_tokens",
    "cost_micros",
    "currency",
    "steps",
    "finish_reason",
    "error_code",
    "heartbeat_at",
    "started_at",
    "finished_at",
}
DETAIL_FIELDS = SUMMARY_FIELDS | {"error_message", "boot_id", "meta", "children"}


def _session(session_id: str) -> AgentSession:
    return AgentSession(id=session_id, model=MODEL)


def _run(
    run_id: str,
    *,
    session_id: str | None = None,
    parent_run_id: str | None = None,
    root_run_id: str | None = None,
    started_at: datetime = T1,
    finished_at: datetime | None = None,
    status: AgentRunStatus = AgentRunStatus.SUCCEEDED,
    meta: dict | None = None,
    error_message: str | None = None,
    heartbeat_at: datetime | None = None,
    **fields,
) -> AgentRun:
    """一条最小可用的运行；``root_run_id`` 缺省就是自身 id（根运行）。"""
    return AgentRun(
        id=run_id,
        session_id=session_id,
        parent_run_id=parent_run_id,
        root_run_id=root_run_id or run_id,
        agent_name="legacy_chat",
        spec_version="a1b2c3d4e5f6",
        kind=AgentRunKind.LEGACY_TURN,
        status=status,
        boot_id=BOOT_ID,
        started_at=started_at,
        finished_at=finished_at if finished_at is not None else started_at,
        heartbeat_at=heartbeat_at if heartbeat_at is not None else started_at,
        meta=meta,
        error_message=error_message,
        **fields,
    )


def _runs_client(
    isolated_db,
    make_client,
    *,
    sessions: tuple[AgentSession, ...] = (),
    runs: tuple[AgentRun, ...] = (),
    with_service: bool = True,
):
    """建表、种子数据、装路由；``with_service=False`` 模拟服务尚未装配。"""

    async def seed() -> None:
        async with isolated_db.begin() as connection:
            await connection.run_sync(SQLModel.metadata.create_all)
        factory = async_sessionmaker(
            isolated_db, expire_on_commit=False, class_=AsyncSession
        )
        async with factory() as session:
            session.add_all(sessions)
            await session.flush()
            session.add_all(runs)
            await session.commit()

    asyncio.run(seed())
    # 种子连接属于 asyncio.run 的事件循环，交给 TestClient 的循环之前先回收。
    isolated_db.sync_engine.dispose()
    factory = async_sessionmaker(isolated_db, expire_on_commit=False, class_=AsyncSession)

    app = FastAPI()
    register_exception_handlers(app)
    app.include_router(runs_router.router)
    if with_service:
        app.state.run_service = RunService(factory, boot_id=BOOT_ID)
    return make_client(app)


def _error(response):
    return response.json()["error"]


# ---------------------------------------------------------------------------
# 列表
# ---------------------------------------------------------------------------
def test_list_runs_paginates_newest_first(isolated_db, make_client, tmp_settings):
    client = _runs_client(
        isolated_db,
        make_client,
        sessions=(_session("s1"),),
        runs=(
            _run("run-old", session_id="s1", started_at=T1),
            _run("run-mid", session_id="s1", started_at=T2),
            _run("run-new", session_id="s1", started_at=T3),
        ),
    )

    response = client.get("/api/agent/runs?size=2")
    assert response.status_code == 200, response.text
    payload = response.json()
    assert (payload["total"], payload["page"], payload["size"]) == (3, 1, 2)
    assert [item["run_id"] for item in payload["items"]] == ["run-new", "run-mid"]
    for item in payload["items"]:
        assert set(item) == SUMMARY_FIELDS
        assert item["ticket_id"] is None
        assert item["started_at"].endswith("Z")
        assert item["finished_at"].endswith("Z")
        assert item["heartbeat_at"].endswith("Z")
        assert item["kind"] == "legacy_turn"
        assert item["status"] == "succeeded"

    second = client.get("/api/agent/runs?size=2&page=2")
    assert second.status_code == 200, second.text
    assert [item["run_id"] for item in second.json()["items"]] == ["run-old"]
    assert second.json()["total"] == 3


def test_list_runs_root_only_and_session_filter(isolated_db, make_client, tmp_settings):
    client = _runs_client(
        isolated_db,
        make_client,
        sessions=(_session("s1"), _session("s2")),
        runs=(
            _run("root-a", session_id="s1", started_at=T1),
            _run(
                "child-b",
                session_id="s1",
                parent_run_id="root-a",
                root_run_id="root-a",
                started_at=T2,
            ),
            _run("root-c", session_id="s2", started_at=T3),
        ),
    )

    def ids(query: str) -> list[str]:
        response = client.get(f"/api/agent/runs{query}")
        assert response.status_code == 200, response.text
        return [item["run_id"] for item in response.json()["items"]]

    # 默认 root_only=true：子运行不进列表，按 started_at 倒序。
    assert ids("") == ["root-c", "root-a"]
    # root_only=false：子运行也列出来。
    assert ids("?root_only=false") == ["root-c", "child-b", "root-a"]
    # session_id 过滤只留这个会话的运行；默认仍只列根。
    assert ids("?session_id=s1") == ["root-a"]
    assert ids("?session_id=s1&root_only=false") == ["child-b", "root-a"]
    assert ids("?session_id=s2") == ["root-c"]


def test_list_runs_with_ticket_id_returns_empty_page(
    isolated_db, make_client, tmp_settings
):
    client = _runs_client(
        isolated_db,
        make_client,
        sessions=(_session("s1"),),
        runs=(_run("run-1", session_id="s1"),),
    )

    response = client.get("/api/agent/runs?ticket_id=ticket-1")
    assert response.status_code == 200, response.text
    assert response.json() == {"items": [], "total": 0, "page": 1, "size": 20}


@pytest.mark.parametrize("query", ["page=0", "size=0", "size=201"])
def test_list_runs_rejects_bad_pagination(
    isolated_db, make_client, tmp_settings, query
):
    client = _runs_client(isolated_db, make_client)

    response = client.get(f"/api/agent/runs?{query}")
    assert response.status_code == 400, response.text
    assert _error(response)["code"] == "INVALID_PAGINATION"


# ---------------------------------------------------------------------------
# 详情
# ---------------------------------------------------------------------------
def test_get_run_returns_tree(isolated_db, make_client, tmp_settings):
    client = _runs_client(
        isolated_db,
        make_client,
        sessions=(_session("s1"),),
        runs=(
            _run(
                "root-a",
                session_id="s1",
                started_at=T1,
                meta={"source": "test"},
                error_message="ignored by summary",
            ),
            _run(
                "child-b",
                session_id="s1",
                parent_run_id="root-a",
                root_run_id="root-a",
                started_at=T2,
            ),
        ),
    )

    response = client.get("/api/agent/runs/root-a")
    assert response.status_code == 200, response.text
    payload = response.json()
    assert set(payload) == DETAIL_FIELDS
    assert payload["run_id"] == "root-a"
    assert payload["boot_id"] == BOOT_ID
    assert payload["meta"] == {"source": "test"}
    assert payload["error_message"] == "ignored by summary"
    assert [child["run_id"] for child in payload["children"]] == ["child-b"]
    assert set(payload["children"][0]) == DETAIL_FIELDS
    assert payload["children"][0]["children"] == []
    assert payload["children"][0]["parent_run_id"] == "root-a"


def test_get_run_unknown_returns_404_run_not_found(
    isolated_db, make_client, tmp_settings
):
    client = _runs_client(isolated_db, make_client)

    response = client.get("/api/agent/runs/nope")
    assert response.status_code == 404, response.text
    error = _error(response)
    assert error["code"] == "RUN_NOT_FOUND"
    assert error["details"]["run_id"] == "nope"


def test_run_routes_report_503_without_a_service(
    isolated_db, make_client, tmp_settings
):
    """启动中（``app.state.run_service`` 还没设置）的固定文案。"""
    client = _runs_client(isolated_db, make_client, with_service=False)

    for response in (
        client.get("/api/agent/runs"),
        client.get("/api/agent/runs/run-1"),
    ):
        assert response.status_code == 503, response.text
        error = _error(response)
        assert error["code"] == "SERVICE_UNAVAILABLE"
        assert error["message"] == "运行记录还在启动中，请稍后重试"


# ---------------------------------------------------------------------------
# 鉴权与 OpenAPI
# ---------------------------------------------------------------------------
@pytest.mark.parametrize("tmp_settings", ["runs-secret"], indirect=True)
def test_run_routes_require_auth(isolated_db, make_client, tmp_settings):
    client = _runs_client(
        isolated_db,
        make_client,
        sessions=(_session("s1"),),
        runs=(_run("run-1", session_id="s1"),),
    )
    deps.reset_rate_limiter()
    try:
        for path in ("/api/agent/runs", "/api/agent/runs/run-1"):
            response = client.get(path)
            assert response.status_code == 401, response.text
            assert _error(response)["code"] == "UNAUTHORIZED"

        headers = {"X-Token": "runs-secret"}
        assert client.get("/api/agent/runs", headers=headers).status_code == 200
        assert (
            client.get("/api/agent/runs/run-1", headers=headers).status_code == 200
        )
    finally:
        deps.reset_rate_limiter()


def test_openapi_declares_run_operations(tmp_settings):
    schema = create_app().openapi()
    paths = schema["paths"]

    assert {"/api/agent/runs", "/api/agent/runs/{run_id}"} <= set(paths)
    list_operation = paths["/api/agent/runs"]["get"]
    detail_operation = paths["/api/agent/runs/{run_id}"]["get"]
    assert list_operation["operationId"] == "agent_list_runs"
    assert detail_operation["operationId"] == "agent_get_run"
    assert list_operation["summary"] == "列出代理运行"
    assert detail_operation["summary"] == "读取运行与子运行树"

    def response_model(operation) -> str:
        content = operation["responses"]["200"]["content"]["application/json"]
        return content["schema"]["$ref"].rsplit("/", 1)[-1]

    assert response_model(list_operation) == "RunPage"
    assert response_model(detail_operation) == "RunDetail"

    components = schema["components"]["schemas"]
    children = components["RunDetail"]["properties"]["children"]
    assert children["items"]["$ref"] == "#/components/schemas/RunDetail"
    # 错误码按 §4.3.2 出现在文档里（400 / 404 / 503 各一条）。
    assert "INVALID_PAGINATION" in list_operation["responses"]["400"]["description"]
    assert "RUN_NOT_FOUND" in detail_operation["responses"]["404"]["description"]
    assert "SERVICE_UNAVAILABLE" in list_operation["responses"]["503"]["description"]
