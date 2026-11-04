"""M71 五类人工操作对设备租约的抢占（规格 §4.5.3、§6 的 ``test_lease_preemption_routes``）。

每个用例只装被测路由 + 统一错误处理器，``app.state`` 放假的服务替身，租约服务
是真实实现（临时 SQLite 库）。四类入口各验一次「操作前删租约、持有者下一次取
租约得到 ``DEVICE_LEASE_PREEMPTED``」，并覆盖反例：取消 pending 流水线不抢占，
REST 调用独占设备工具要等确认通过、执行之前才抢占。

租约服务的 ``asyncio.Condition`` 必须在应用的事件循环里首次使用，所以准备租约
一律通过 ``TestClient`` 的 portal 调服务，不在测试线程里 ``asyncio.run``。
"""

from __future__ import annotations

import asyncio
import time
from datetime import UTC, datetime
from functools import partial
from types import SimpleNamespace
from typing import Any

import pytest
from fastapi import FastAPI
from pydantic import BaseModel
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from maa_api.agent.confirmation import ConfirmationService
from maa_api.agent.lease import DeviceLeaseService, LeaseHolder
from maa_api.agent.policy import PolicyEngine
from maa_api.agent.registry import ToolDefinition, ToolRegistry, ToolRisk
from maa_api.api import deps
from maa_api.api.errors import register_exception_handlers
from maa_api.api.routers import agent as agent_router
from maa_api.api.routers import atomic, confirmations, pipelines
from maa_api.db.models import AgentRun, AgentSession, Pipeline, Task, Ticket
from maa_api.domain.errors import AppError, ErrorCode
from maa_api.settings import LeaseSettings
from tests.fakes.v3_db import make_session_factory

TICKET = "ticket-a"
SESSION = "session-a"
RUN = "run-a"

#: 租约服务每次要用的设置；等待时长压到 0，抢占后的重取必须立刻报错。
LEASE_SETTINGS = LeaseSettings(ttl_seconds=60, wait_seconds=0)


def _holder() -> LeaseHolder:
    return LeaseHolder(ticket_id=TICKET, run_id=RUN, agent="daily_ops")


def _acquire(client, service: DeviceLeaseService) -> None:
    client.portal.call(partial(service.acquire, _holder(), purpose="click"))


def _current(client, service: DeviceLeaseService):
    return client.portal.call(service.current)


def _assert_preempted(client, service: DeviceLeaseService, reason: str) -> None:
    """持有者被抢占后的第一次取租约报 PREEMPTED，details 带原因。"""
    with pytest.raises(AppError) as excinfo:
        client.portal.call(partial(service.acquire, _holder(), purpose="click"))
    assert excinfo.value.code is ErrorCode.DEVICE_LEASE_PREEMPTED
    assert excinfo.value.details == {"reason": reason}


class FakeQueue:
    """``submit`` 只回一条新流水线；用例关心的是路由有没有先抢占租约。"""

    def __init__(self) -> None:
        self.calls: list[dict[str, Any]] = []

    async def submit(
        self,
        payload: Any,
        *,
        source: Any,
        idempotency_key: str | None = None,
        retry_of_id: str | None = None,
    ):
        self.calls.append(
            {
                "tasks": len(payload.tasks),
                "source": source,
                "idempotency_key": idempotency_key,
                "retry_of_id": retry_of_id,
            }
        )
        pipeline = SimpleNamespace(
            id=f"pipeline-{len(self.calls)}",
            status="pending",
            created_at=datetime.now(UTC),
            retry_of_id=retry_of_id,
            title=payload.title,
            priority=payload.priority,
            notify_on_finish=payload.notify_on_finish,
        )
        return pipeline, False


class FakeRunner:
    def __init__(self) -> None:
        self.operation_lock = asyncio.Lock()
        self.published: list[str] = []
        self.queue_changes = 0
        self.cancelled: list[str] = []

    async def publish_pipeline(self, pipeline_id: str) -> None:
        self.published.append(pipeline_id)

    async def publish_queue_changed(self) -> None:
        self.queue_changes += 1

    async def request_cancel(self, pipeline_id: str) -> None:
        self.cancelled.append(pipeline_id)


class FakeManager:
    def __init__(self) -> None:
        self.calls: list[tuple[Any, ...]] = []

    async def click(self, x: int, y: int) -> None:
        self.calls.append(("click", x, y))


async def _seed_lease_world(factory: async_sessionmaker[AsyncSession]) -> None:
    async with factory() as db:
        db.add(Ticket(id=TICKET, title="日常", kind="daily_ops", origin="user"))
        await db.commit()
        db.add(
            AgentSession(
                id=SESSION,
                model="test-model",
                kind="worker",
                status="active",
                ticket_id=TICKET,
            )
        )
        await db.commit()
        ticket = await db.get(Ticket, TICKET)
        assert ticket is not None
        ticket.worker_session_id = SESSION
        await db.commit()
        db.add(
            AgentRun(
                id=RUN,
                root_run_id=RUN,
                session_id=SESSION,
                ticket_id=TICKET,
                agent_name="daily_ops",
                kind="worker_turn",
                status="running",
                boot_id="boot-test",
            )
        )
        await db.commit()


@pytest.fixture
def lease_env(tmp_path):
    """真实租约服务与一个待抢占的工单世界。"""
    engine, factory = asyncio.run(make_session_factory(tmp_path / "lease-routes.db"))
    asyncio.run(_seed_lease_world(factory))
    service = DeviceLeaseService(
        factory,
        boot_id="boot-test",
        settings_provider=lambda: SimpleNamespace(lease=LEASE_SETTINGS),
    )
    yield SimpleNamespace(engine=engine, factory=factory, service=service)
    engine.sync_engine.dispose()


def _pipelines_app(env: SimpleNamespace, *, with_atomic: bool = False):
    app = FastAPI()
    register_exception_handlers(app)
    app.include_router(pipelines.router)
    queue = FakeQueue()
    runner = FakeRunner()
    manager = FakeManager()
    if with_atomic:
        app.include_router(atomic.router)
        app.state.device_manager = manager
    app.state.queue_service = queue
    app.state.pipeline_runner = runner
    app.state.device_lease_service = env.service

    async def get_test_session():
        async with env.factory() as session:
            yield session

    app.dependency_overrides[deps.get_session] = get_test_session
    return app, queue, runner, manager


def _seed_pipeline(
    env: SimpleNamespace,
    pipeline_id: str,
    status: str,
    *,
    with_task: bool = False,
) -> None:
    async def write() -> None:
        async with env.factory() as db:
            db.add(
                Pipeline(
                    id=pipeline_id,
                    source="manual",
                    priority=1,
                    status=status,
                    title="历史流水线",
                    task_count=1,
                )
            )
            await db.commit()
            if with_task:
                db.add(
                    Task(
                        pipeline_id=pipeline_id,
                        order_index=0,
                        type_name="StartUp",
                        task_name="开始唤醒",
                        raw_params={},
                    )
                )
                await db.commit()

    asyncio.run(write())


# ---------------------------------------------------------------------------
# 手动提交与重放
# ---------------------------------------------------------------------------
def test_manual_submit_preempts_lease(lease_env, make_client, tmp_settings):
    app, queue, runner, _ = _pipelines_app(lease_env)
    client = make_client(app)
    _acquire(client, lease_env.service)

    response = client.post("/api/pipelines", json={"tasks": [{"name": "StartUp"}]})

    assert response.status_code == 202, response.text
    assert response.json()["reused"] is False
    assert len(queue.calls) == 1
    assert _current(client, lease_env.service) is None
    assert runner.published == ["pipeline-1"]
    _assert_preempted(client, lease_env.service, "manual_pipeline")


def test_retry_and_rest_atomic_preempt_lease(lease_env, make_client, tmp_settings):
    _seed_pipeline(lease_env, "pipeline-history", "completed", with_task=True)
    app, queue, _, manager = _pipelines_app(lease_env, with_atomic=True)
    client = make_client(app)

    _acquire(client, lease_env.service)
    retried = client.post("/api/pipelines/pipeline-history/retry", json={})
    assert retried.status_code == 202, retried.text
    assert retried.json()["retry_of_id"] == "pipeline-history"
    assert queue.calls[-1]["retry_of_id"] == "pipeline-history"
    assert _current(client, lease_env.service) is None
    _assert_preempted(client, lease_env.service, "manual_pipeline")

    _acquire(client, lease_env.service)
    clicked = client.post("/api/device/click", json={"x": 10, "y": 20})
    assert clicked.status_code == 200, clicked.text
    assert manager.calls == [("click", 10, 20)]
    assert _current(client, lease_env.service) is None
    _assert_preempted(client, lease_env.service, "rest_atomic")


# ---------------------------------------------------------------------------
# 停止运行中的流水线
# ---------------------------------------------------------------------------
def test_cancel_running_pipeline_preempts_but_pending_does_not(
    lease_env, make_client, tmp_settings
):
    _seed_pipeline(lease_env, "pipeline-running", "running")
    _seed_pipeline(lease_env, "pipeline-pending", "pending")
    app, _, runner, _ = _pipelines_app(lease_env)
    client = make_client(app)

    _acquire(client, lease_env.service)
    stopped = client.delete("/api/pipelines/pipeline-running")
    assert stopped.status_code == 202, stopped.text
    assert runner.cancelled == ["pipeline-running"]
    assert _current(client, lease_env.service) is None
    _assert_preempted(client, lease_env.service, "stop_pipeline")

    # 取消 pending 只是把它标成 cancelled，不构成「用户接管设备」。
    _acquire(client, lease_env.service)
    pending = client.delete("/api/pipelines/pipeline-pending")
    assert pending.status_code == 202, pending.text
    assert runner.cancelled == ["pipeline-running", "pipeline-pending"]
    assert _current(client, lease_env.service) is not None
    _acquire(client, lease_env.service)  # 同一工单重入，只续期，不报抢占


# ---------------------------------------------------------------------------
# REST 调用独占设备工具：确认通过之后、执行之前才抢占
# ---------------------------------------------------------------------------
class _Args(BaseModel):
    value: int = 0


def test_rest_tool_invoke_of_exclusive_tool_preempts_after_confirmation(
    lease_env, make_client, tmp_settings
):
    observed: list[Any] = []

    async def handler(_params, _context):
        # 执行前钩子已经跑过：handler 里看到的租约必须已经被删掉。
        observed.append(await lease_env.service.current())
        return {"ok": True}

    registry = ToolRegistry()
    registry.register(
        ToolDefinition(
            name="take_device",
            group="ops",
            risk=ToolRisk.DANGEROUS,
            description="独占设备",
            params_model=_Args,
            handler=handler,
            device="exclusive",
        )
    )
    confirmation_service = ConfirmationService(
        lease_env.factory, registry, policy=PolicyEngine()
    )

    app = FastAPI()
    register_exception_handlers(app)
    app.include_router(agent_router.router)
    app.include_router(confirmations.router)
    app.state.confirmation_service = confirmation_service
    app.state.session_factory = lease_env.factory
    app.state.device_lease_service = lease_env.service

    async def get_test_session():
        async with lease_env.factory() as session:
            yield session

    app.dependency_overrides[deps.get_session] = get_test_session
    client = make_client(app)
    _acquire(client, lease_env.service)

    invoked = client.post(
        "/api/agent/tools/take_device/invoke",
        json={"arguments": {}, "mode": "async"},
    )
    assert invoked.status_code == 202, invoked.text
    payload = invoked.json()
    assert payload["status"] == "awaiting_confirmation"
    confirmation_id = payload["confirmation_id"]
    # 待确认期间租约仍归持有者。
    assert _current(client, lease_env.service) is not None

    approved = client.post(
        f"/api/confirmations/{confirmation_id}", json={"approved": True}
    )
    assert approved.status_code == 200, approved.text
    for _ in range(200):
        if observed:
            break
        time.sleep(0.01)
    assert observed, "批准后工具没有执行"
    assert observed[0] is None
    assert _current(client, lease_env.service) is None
    _assert_preempted(client, lease_env.service, "rest_tool")
