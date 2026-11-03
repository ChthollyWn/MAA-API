"""运行追踪 spans 端点的 REST 契约（M68 规格 §4.3、§6 T5）。

四条用例：子运行 id 也返回整条 trace、未知运行 404 ``RUN_NOT_FOUND``、超过
``MAX_TRACE_SPANS`` 时截断并置 ``truncated=true``、鉴权。app 按生产装配的最小集搭：
只装 runs 路由、统一错误处理器和 ``app.state.run_service``；运行与 span 直接写进
``isolated_db`` 的临时库，不碰真实 ``resource/maa_api.db``。

span 走 Core ``INSERT`` 批量落库：截断用例要造 5001 行，ORM 的 ``add_all`` 太慢。
"""

from __future__ import annotations

import asyncio
from datetime import datetime, timedelta
from typing import Any

import pytest
from fastapi import FastAPI
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker
from sqlmodel import SQLModel

import maa_api.db.models  # noqa: F401  # 注册全部表，create_all 才能建出 agent_run / trace_span
from maa_api.agent.runs import RunService
from maa_api.api import deps
from maa_api.api.errors import register_exception_handlers
from maa_api.api.routers import runs as runs_router
from maa_api.db.models import AgentRun, TraceSpan
from maa_api.domain.enums import AgentRunKind, AgentRunStatus

#: 测试进程的 boot_id；装配 ``RunService`` 需要它。
BOOT_ID = "test-boot"

#: trace_id 就是根运行 id。三个 id 都是 36 位 UUID 形状（与 SpanRecorder 的校验同形）。
ROOT_RUN = "11111111-1111-1111-1111-111111111111"
CHILD_RUN = "22222222-2222-2222-2222-222222222222"
OTHER_RUN = "33333333-3333-3333-3333-333333333333"
#: 三个稳定的开始时刻（naive UTC，与库里的口径一致）。
T1 = datetime(2026, 11, 5, 8, 0, 0)
T2 = T1 + timedelta(seconds=1)
T3 = T1 + timedelta(seconds=2)

#: ``SpanOut`` 的 10 个字段（规格 §4.3）。
SPAN_FIELDS = {
    "span_id",
    "parent_span_id",
    "run_id",
    "name",
    "kind",
    "status",
    "attributes",
    "started_at",
    "ended_at",
    "duration_ms",
}


def _run(
    run_id: str,
    *,
    parent_run_id: str | None = None,
    root_run_id: str | None = None,
) -> AgentRun:
    """一条最小可用的运行；``root_run_id`` 缺省就是自身 id（根运行）。"""
    return AgentRun(
        id=run_id,
        parent_run_id=parent_run_id,
        root_run_id=root_run_id or run_id,
        agent_name="legacy_chat",
        spec_version="a1b2c3d4e5f6",
        kind=AgentRunKind.LEGACY_TURN,
        status=AgentRunStatus.SUCCEEDED,
        boot_id=BOOT_ID,
        started_at=T1,
        finished_at=T2,
        heartbeat_at=T2,
    )


def _span_row(
    trace_id: str,
    span_id: str,
    *,
    run_id: str | None,
    started_at: datetime,
    **fields: Any,
) -> dict[str, Any]:
    """一行 ``trace_span`` 的 Core 插入字典；默认值是 ``agent.run`` 根 span 的样子。"""
    row: dict[str, Any] = {
        "trace_id": trace_id,
        "span_id": span_id,
        "parent_span_id": None,
        "run_id": run_id,
        "name": "agent.run",
        "kind": "other",
        "status": "ok",
        "attributes": {},
        "started_at": started_at,
        "ended_at": None,
        "duration_ms": None,
    }
    row.update(fields)
    return row


def _spans_client(
    isolated_db,
    make_client,
    *,
    runs: tuple[AgentRun, ...] = (),
    spans: tuple[dict[str, Any], ...] = (),
):
    """建表、种子运行与 span、装路由；``spans`` 是 ``TraceSpan`` 的插入字典。"""

    async def seed() -> None:
        async with isolated_db.begin() as connection:
            await connection.run_sync(SQLModel.metadata.create_all)
        factory = async_sessionmaker(
            isolated_db, expire_on_commit=False, class_=AsyncSession
        )
        async with factory() as session:
            session.add_all(runs)
            await session.commit()
        if spans:
            async with isolated_db.begin() as connection:
                await connection.execute(TraceSpan.__table__.insert(), list(spans))

    asyncio.run(seed())
    # 种子连接属于 asyncio.run 的事件循环，交给 TestClient 的循环之前先回收。
    isolated_db.sync_engine.dispose()
    factory = async_sessionmaker(isolated_db, expire_on_commit=False, class_=AsyncSession)

    app = FastAPI()
    register_exception_handlers(app)
    app.include_router(runs_router.router)
    app.state.run_service = RunService(factory, boot_id=BOOT_ID)
    return make_client(app)


def _error(response):
    return response.json()["error"]


# ---------------------------------------------------------------------------
# 整条 trace
# ---------------------------------------------------------------------------
def test_returns_whole_trace_for_child_run(isolated_db, make_client, tmp_settings):
    """子运行 id 也返回根运行那一整条 trace，按 ``(started_at, id)`` 升序。"""
    client = _spans_client(
        isolated_db,
        make_client,
        runs=(
            _run(ROOT_RUN),
            _run(CHILD_RUN, parent_run_id=ROOT_RUN, root_run_id=ROOT_RUN),
            # 第三个运行没有任何 span：追踪关闭或已清理时是空列表，不是 404。
            _run(OTHER_RUN),
        ),
        spans=(
            _span_row(
                ROOT_RUN,
                "span-root",
                run_id=ROOT_RUN,
                started_at=T1,
                ended_at=T3 + timedelta(seconds=1),
                duration_ms=3000,
            ),
            _span_row(
                ROOT_RUN,
                "span-llm",
                parent_span_id="span-root",
                run_id=ROOT_RUN,
                started_at=T2,
                name="llm.chat",
                kind="llm",
                attributes={"gen_ai.operation.name": "chat"},
                ended_at=T3,
                duration_ms=1000,
            ),
            _span_row(
                ROOT_RUN,
                "span-tool",
                parent_span_id="span-llm",
                run_id=CHILD_RUN,
                started_at=T3,
                name="tool.execute",
                kind="tool",
                status="error",
                attributes={"maa.tool.name": "delegate_to_worker"},
            ),
        ),
    )

    for run_id in (ROOT_RUN, CHILD_RUN):
        response = client.get(f"/api/agent/runs/{run_id}/spans")
        assert response.status_code == 200, response.text
        payload = response.json()
        assert payload["trace_id"] == ROOT_RUN
        assert payload["truncated"] is False
        assert [item["span_id"] for item in payload["items"]] == [
            "span-root",
            "span-llm",
            "span-tool",
        ]
        assert set(payload["items"][0]) == SPAN_FIELDS
        root = payload["items"][0]
        assert root["parent_span_id"] is None
        assert root["run_id"] == ROOT_RUN
        assert root["started_at"].endswith("Z")
        assert root["ended_at"].endswith("Z")
        assert root["duration_ms"] == 3000
        llm = payload["items"][1]
        assert llm["attributes"] == {"gen_ai.operation.name": "chat"}
        assert llm["kind"] == "llm"
        # 子运行的 span 也在整条 trace 里，``run_id`` 原样带出。
        tool = payload["items"][2]
        assert tool["run_id"] == CHILD_RUN
        assert tool["status"] == "error"

    empty = client.get(f"/api/agent/runs/{OTHER_RUN}/spans")
    assert empty.status_code == 200, empty.text
    assert empty.json() == {
        "trace_id": OTHER_RUN,
        "items": [],
        "truncated": False,
    }


def test_unknown_run_is_404(isolated_db, make_client, tmp_settings):
    """运行 id 不存在时报 404 ``RUN_NOT_FOUND``，``details`` 带原 id。"""
    client = _spans_client(isolated_db, make_client)

    response = client.get("/api/agent/runs/nope/spans")
    assert response.status_code == 404, response.text
    error = _error(response)
    assert error["code"] == "RUN_NOT_FOUND"
    assert error["details"]["run_id"] == "nope"


def test_truncates_after_limit(isolated_db, make_client, tmp_settings):
    """恰好多一条时丢掉排序最后的那条并置 ``truncated=true``。"""
    limit = runs_router.MAX_TRACE_SPANS
    assert limit == 5000
    spans = tuple(
        _span_row(
            ROOT_RUN,
            f"span-{index:04d}",
            run_id=ROOT_RUN,
            started_at=T1 + timedelta(milliseconds=index),
        )
        for index in range(limit + 1)
    )
    client = _spans_client(
        isolated_db, make_client, runs=(_run(ROOT_RUN),), spans=spans
    )

    response = client.get(f"/api/agent/runs/{ROOT_RUN}/spans")
    assert response.status_code == 200, response.text
    payload = response.json()
    assert payload["truncated"] is True
    assert len(payload["items"]) == limit
    # 丢掉的是排序后的最后一条（第 5001 条），不是随机一条。
    assert payload["items"][0]["span_id"] == "span-0000"
    assert payload["items"][-1]["span_id"] == f"span-{limit - 1:04d}"


# ---------------------------------------------------------------------------
# 鉴权
# ---------------------------------------------------------------------------
@pytest.mark.parametrize("tmp_settings", ["runs-spans-secret"], indirect=True)
def test_requires_auth(isolated_db, make_client, tmp_settings):
    """未带 token 是 401 ``UNAUTHORIZED``；带上就 200。"""
    client = _spans_client(isolated_db, make_client, runs=(_run(ROOT_RUN),))
    deps.reset_rate_limiter()
    try:
        path = f"/api/agent/runs/{ROOT_RUN}/spans"
        response = client.get(path)
        assert response.status_code == 401, response.text
        assert _error(response)["code"] == "UNAUTHORIZED"

        headers = {"X-Token": "runs-spans-secret"}
        assert client.get(path, headers=headers).status_code == 200
    finally:
        deps.reset_rate_limiter()
