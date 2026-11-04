"""M71 T6：``hold_device`` 与 ``release_device``（规格 §4.2、§6）。

异步用例一律「同步测试函数 + ``asyncio.run(scenario())``」。租约服务用 ``lease_env``
提供的真实实现；``ToolContext`` 只填 handler 会读的 ``request`` 与 ``origin``。
"""

from __future__ import annotations

import asyncio
from types import SimpleNamespace
from typing import Any

import pytest

from maa_api.agent.registry import ToolContext, ToolOrigin
from maa_api.agent.tools import build_registry
from maa_api.agent.tools.lease import lease_tools
from maa_api.domain.enums import CallerType
from maa_api.domain.errors import AppError, ErrorCode


def _definition(name: str):
    return next(item for item in lease_tools() if item.name == name)


def _context(service: Any, *, origin: ToolOrigin | None) -> ToolContext:
    """只放 handler 需要的两样：``request.app.state`` 上的服务与调用来源。"""
    state = SimpleNamespace(device_lease_service=service)
    return ToolContext(
        caller=CallerType.INTERNAL,
        session_id=None,
        request_id=None,
        request=SimpleNamespace(app=SimpleNamespace(state=state)),
        db_session=None,
        origin=origin,
    )


def _origin(world) -> ToolOrigin:
    return ToolOrigin(
        run_id=world.run_ids["a"],
        ticket_id=world.ticket_ids["a"],
        agent="daily_ops",
    )


def test_hold_device_takes_explicit_lease(lease_env, lease_world) -> None:
    _factory, service, _events, _published = lease_env
    origin = _origin(lease_world)

    async def scenario() -> None:
        definition = _definition("hold_device")
        params = definition.params_model.model_validate({"purpose": "先导航再提交"})
        result = await definition.handler(params, _context(service, origin=origin))

        assert result["held"] is True
        assert result["explicit"] is True
        assert result["lease_id"]
        assert result["expires_at"].endswith("Z")

        row = await service.current()
        assert row is not None
        assert row.lease_id == result["lease_id"]
        assert row.ticket_id == origin.ticket_id
        assert row.run_id == origin.run_id
        assert row.holder_agent == origin.agent
        assert row.purpose == "先导航再提交"
        assert row.explicit is True

        # 同一工单的 release_device 把租约删掉。
        release = _definition("release_device")
        released = await release.handler(
            release.params_model.model_validate({}),
            _context(service, origin=origin),
        )
        assert released == {"released": True}
        assert await service.current() is None

    asyncio.run(scenario())


def test_release_device_without_lease_raises(lease_env, lease_world) -> None:
    _factory, service, _events, _published = lease_env
    origin = _origin(lease_world)

    async def scenario() -> None:
        definition = _definition("release_device")
        with pytest.raises(AppError) as excinfo:
            await definition.handler(
                definition.params_model.model_validate({}),
                _context(service, origin=origin),
            )
        assert excinfo.value.code is ErrorCode.DEVICE_LEASE_NOT_HELD
        assert excinfo.value.message == "当前没有占用设备"
        assert await service.current() is None

    asyncio.run(scenario())


def test_hold_device_requires_ticket_origin(lease_env) -> None:
    _factory, service, _events, _published = lease_env
    no_origin: ToolOrigin | None = None
    no_ticket = ToolOrigin(run_id="run-a", ticket_id=None, agent="daily_ops")

    async def scenario() -> None:
        definition = _definition("hold_device")
        params = definition.params_model.model_validate({"purpose": "先导航再提交"})
        for origin in (no_origin, no_ticket):
            with pytest.raises(AppError) as excinfo:
                await definition.handler(params, _context(service, origin=origin))
            assert excinfo.value.code is ErrorCode.INVALID_PARAMETER
            assert excinfo.value.message == "只有工单里的代理能占用设备"
        # 被拒绝的调用不留下租约。
        assert await service.current() is None

    asyncio.run(scenario())


def test_hold_device_fails_closed_when_lease_service_is_missing() -> None:
    definition = _definition("hold_device")
    params = definition.params_model.model_validate({"purpose": "先导航再提交"})
    origin = ToolOrigin(run_id="run-a", ticket_id="ticket-a", agent="daily_ops")

    async def scenario() -> None:
        with pytest.raises(AppError) as excinfo:
            await definition.handler(params, _context(None, origin=origin))
        assert excinfo.value.code is ErrorCode.DEVICE_LEASE_BUSY
        assert excinfo.value.details == {"reason": "lease_service_unavailable"}

    asyncio.run(scenario())


def test_lease_tools_are_not_rest_visible() -> None:
    """T6 完成判据：REST 目录看不到两条工具，调用入口按未知工具拒绝。"""
    from maa_api.api.routers.agent import _ensure_rest_visible

    registry = build_registry()
    by_name = {definition.name: definition for definition in registry.list()}
    names = ("hold_device", "release_device")
    for name in names:
        definition = by_name[name]
        assert definition.group == "lease"
        assert definition.rest_visible is False
        assert definition.device == "none"
    assert set(names).isdisjoint(
        definition.name for definition in registry.list() if definition.rest_visible
    )

    service = SimpleNamespace(registry=registry)
    for name in names:
        with pytest.raises(AppError) as excinfo:
            _ensure_rest_visible(service, name)
        assert excinfo.value.code is ErrorCode.TOOL_NOT_FOUND
