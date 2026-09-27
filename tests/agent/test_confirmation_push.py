"""确认请求与 Web Push 的协同（M14 Task 3，docs/09 §13.1 第 2 步）。

这一层单独测「什么时候推」与「推送失败会不会影响确认」：加密与投递细节由
``tests/services/test_webpush_service.py`` 覆盖。
"""

from __future__ import annotations

import asyncio
from types import SimpleNamespace

from pydantic import BaseModel
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker
from sqlalchemy.pool import StaticPool
from sqlmodel import SQLModel

import maa_api.db.models  # noqa: F401
from maa_api.agent.confirmation import ConfirmationService
from maa_api.agent.registry import ToolContext, ToolDefinition, ToolRegistry, ToolRisk
from maa_api.db.repositories.audit import ConfirmationRepository
from maa_api.db.session import make_engine
from maa_api.domain.enums import CallerType


class Params(BaseModel):
    value: int = 1


class _Push:
    """记录推送调用；可配置成抛异常以验证「推送失败不影响确认」。"""

    def __init__(self, *, explode: bool = False) -> None:
        self.calls: list[dict] = []
        self.explode = explode

    async def notify_confirmation(self, **kwargs):
        self.calls.append(kwargs)
        if self.explode:
            raise OSError("push transport down")
        return SimpleNamespace(delivered=1)


def _database():
    engine = make_engine(
        "sqlite+aiosqlite:///:memory:", poolclass=StaticPool
    )
    factory = async_sessionmaker(engine, expire_on_commit=False, class_=AsyncSession)
    return engine, factory


def _request():
    return SimpleNamespace(app=SimpleNamespace(state=SimpleNamespace()))


def _registry() -> ToolRegistry:
    registry = ToolRegistry()

    async def handler(params, _context):
        return {"done": params.value}

    registry.register(
        ToolDefinition(
            name="dangerous_action",
            group="ops",
            risk=ToolRisk.DANGEROUS,
            description="test action",
            params_model=Params,
            handler=handler,
        )
    )
    return registry


async def _invoke_until_pending(service, factory):
    """发起同步调用并等到确认行落库（用广播事件同步，不轮询）。"""
    created = asyncio.Event()

    original_broadcast = service.broadcast

    def broadcast(kind, data):
        original_broadcast(kind, data)
        if kind == "confirm_request":
            created.set()

    service.broadcast = broadcast
    context = ToolContext(CallerType.REST, None, "sync-request", _request(), None)
    task = asyncio.create_task(
        service.invoke("dangerous_action", {"value": 5}, context, mode="sync")
    )
    await asyncio.wait_for(created.wait(), timeout=5)
    async with factory() as session:
        pending = await ConfirmationRepository(session).list_pending()
    assert pending
    return task, pending[0]


def test_no_client_connected_triggers_push_with_deep_link() -> None:
    async def scenario():
        engine, factory = _database()
        async with engine.begin() as connection:
            await connection.run_sync(SQLModel.metadata.create_all)
        push = _Push()
        service = ConfirmationService(
            factory,
            _registry(),
            push=push,
            has_active_client=lambda: False,
        )
        task, confirmation = await _invoke_until_pending(service, factory)
        await asyncio.sleep(0.02)

        assert len(push.calls) == 1
        call = push.calls[0]
        assert call["confirmation_id"] == confirmation.id
        assert call["has_active_client"] is False
        assert call["url"] == f"/?confirm={confirmation.id}"
        assert call["title"] == "dangerous_action"

        await service.resolve(confirmation.id, approved=True, resolved_by="web")
        await task
        await engine.dispose()

    asyncio.run(scenario())


def test_connected_client_suppresses_push() -> None:
    async def scenario():
        engine, factory = _database()
        async with engine.begin() as connection:
            await connection.run_sync(SQLModel.metadata.create_all)
        push = _Push()
        service = ConfirmationService(
            factory,
            _registry(),
            push=push,
            has_active_client=lambda: True,
        )
        task, confirmation = await _invoke_until_pending(service, factory)
        await asyncio.sleep(0.02)

        # 用户正看着页面：不推送，但确认本身照常进行
        assert push.calls[0]["has_active_client"] is True

        await service.resolve(confirmation.id, approved=True, resolved_by="web")
        await task
        await engine.dispose()

    asyncio.run(scenario())


def test_push_failure_does_not_break_confirmation() -> None:
    """推送发不出去只是少一条通知，确认照常等待与执行。"""

    async def scenario():
        engine, factory = _database()
        async with engine.begin() as connection:
            await connection.run_sync(SQLModel.metadata.create_all)
        push = _Push(explode=True)
        service = ConfirmationService(
            factory, _registry(), push=push, has_active_client=lambda: False
        )
        task, confirmation = await _invoke_until_pending(service, factory)
        await asyncio.sleep(0.02)
        assert len(push.calls) == 1
        assert not task.done(), "推送失败不应影响等待中的确认"

        await service.resolve(confirmation.id, approved=True, resolved_by="web")
        result = await task
        assert result["status"] == "success"
        await engine.dispose()

    asyncio.run(scenario())


def test_without_push_service_confirmation_still_works() -> None:
    async def scenario():
        engine, factory = _database()
        async with engine.begin() as connection:
            await connection.run_sync(SQLModel.metadata.create_all)
        service = ConfirmationService(factory, _registry())
        task, confirmation = await _invoke_until_pending(service, factory)

        await service.resolve(confirmation.id, approved=True, resolved_by="web")
        result = await task
        assert result["status"] == "success"
        await engine.dispose()

    asyncio.run(scenario())
