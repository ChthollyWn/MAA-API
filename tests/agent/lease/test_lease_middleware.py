"""M71 T5：``LeaseMiddleware``（规格 §4.5.5、§6 的 ``test_lease_middleware.py``）。

异步用例一律「同步测试函数 + ``asyncio.run(scenario())``」。租约服务用
``lease_env`` 提供的真实实现与两个预置工单；``call_next`` 用假函数代替
``PolicyMiddleware``，只观察 ``ctx.state["policy.before_execute"]`` 的登记与清理。
"""

from __future__ import annotations

import asyncio
from typing import Any

from pydantic import BaseModel, ConfigDict

from maa_api.agent.harness.context import ToolCall, ToolOutcome
from maa_api.agent.lease import LeaseMiddleware
from maa_api.agent.registry import ToolDefinition, ToolRegistry, ToolRisk
from maa_api.agent.specs.registry import builtin_spec
from maa_api.domain.errors import AppError, ErrorCode


class _Params(BaseModel):
    model_config = ConfigDict(extra="forbid")


def _registry() -> ToolRegistry:
    """一个独占设备的假工具与一个不占设备的假工具。"""
    registry = ToolRegistry()

    async def handler(_params, _ctx) -> dict[str, Any]:
        return {}

    registry.register(
        ToolDefinition(
            name="exclusive_tool",
            group="test",
            risk=ToolRisk.SAFE,
            description="独占设备",
            params_model=_Params,
            handler=handler,
            device="exclusive",
            rest_visible=False,
        )
    )
    registry.register(
        ToolDefinition(
            name="plain_tool",
            group="test",
            risk=ToolRisk.SAFE,
            description="不占设备",
            params_model=_Params,
            handler=handler,
            device="none",
            rest_visible=False,
        )
    )
    return registry


def _context(
    *,
    run_id: str,
    root_run_id: str,
    ticket_id: str,
    session_id: str,
    spec_name: str = "general",
):
    from maa_api.agent.harness.context import RunContext

    return RunContext(
        run_id=run_id,
        root_run_id=root_run_id,
        parent_run_id=None,
        session_id=session_id,
        ticket_id=ticket_id,
        spec=builtin_spec(spec_name),
    )


def _call(call_id: str, name: str) -> ToolCall:
    return ToolCall(id=call_id, name=name, raw_arguments="{}", arguments={})


def test_exclusive_tool_registers_before_execute_hook(lease_env, lease_world) -> None:
    factory, service, events, _published = lease_env
    world = lease_world
    middleware = LeaseMiddleware(service, _registry())

    async def scenario() -> None:
        ctx = _context(
            run_id=world.run_ids["a"],
            root_run_id=world.run_ids["a"],
            ticket_id=world.ticket_ids["a"],
            session_id=world.worker_sessions["a"],
        )

        async def call_next(call: ToolCall) -> ToolOutcome:
            hooks = ctx.state["policy.before_execute"][call.id]
            assert len(hooks) == 1
            await hooks[0](42)  # PolicyMiddleware 组合后就是这样调用
            return ToolOutcome(status="ok", result={})

        outcome = await middleware.wrap_tool(
            ctx, _call("call-1", "exclusive_tool"), call_next
        )

        assert outcome.status == "ok"
        # 调用结束后键被移除，下一轮不会带着上一轮的回调。
        assert "call-1" not in ctx.state["policy.before_execute"]
        row = await service.current()
        assert row is not None
        assert row.ticket_id == world.ticket_ids["a"]
        assert row.run_id == world.run_ids["a"]
        assert row.purpose == "exclusive_tool"
        assert row.holder_agent == "general"
        assert row.explicit is False
        assert [
            data["payload"]["change"] for kind, data in events if kind == "agent_event"
        ] == ["acquired"]

    asyncio.run(scenario())


def test_non_exclusive_tool_passes_through(lease_env, lease_world) -> None:
    _factory, service, _events, _published = lease_env
    world = lease_world
    middleware = LeaseMiddleware(service, _registry())

    async def scenario() -> None:
        ctx = _context(
            run_id=world.run_ids["a"],
            root_run_id=world.run_ids["a"],
            ticket_id=world.ticket_ids["a"],
            session_id=world.worker_sessions["a"],
        )
        seen: list[str] = []

        async def call_next(call: ToolCall) -> ToolOutcome:
            hooks = ctx.state.get("policy.before_execute") or {}
            assert call.id not in hooks
            seen.append(call.name)
            return ToolOutcome(status="ok", result={"ok": True})

        outcome = await middleware.wrap_tool(
            ctx, _call("call-2", "plain_tool"), call_next
        )

        assert outcome.result == {"ok": True}
        assert seen == ["plain_tool"]
        assert await service.current() is None
        assert "call-2" not in (ctx.state.get("policy.before_execute") or {})

    asyncio.run(scenario())


def test_preempted_outcome_stops_run(lease_env, lease_world) -> None:
    _factory, service, _events, _published = lease_env
    world = lease_world
    middleware = LeaseMiddleware(service, _registry())

    async def scenario() -> None:
        ctx = _context(
            run_id=world.run_ids["a"],
            root_run_id=world.run_ids["a"],
            ticket_id=world.ticket_ids["a"],
            session_id=world.worker_sessions["a"],
        )

        async def preempted(call: ToolCall) -> ToolOutcome:
            return ToolOutcome(
                status="error",
                audit_id=1,
                error={
                    "code": ErrorCode.DEVICE_LEASE_PREEMPTED.value,
                    "message": "设备已被用户接管，先停下设备操作",
                },
            )

        outcome = await middleware.wrap_tool(
            ctx, _call("call-3", "exclusive_tool"), preempted
        )
        assert outcome.stop_run is True
        assert outcome.stop_reason == "lease_preempted"
        assert outcome.status == "error"

        async def other_error(call: ToolCall) -> ToolOutcome:
            return ToolOutcome(
                status="error",
                error={"code": ErrorCode.TOOL_EXECUTION_FAILED.value, "message": "x"},
            )

        outcome = await middleware.wrap_tool(
            ctx, _call("call-4", "exclusive_tool"), other_error
        )
        assert outcome.stop_run is False
        assert outcome.stop_reason is None

    asyncio.run(scenario())


def test_root_run_end_releases_implicit_lease(lease_env, lease_world) -> None:
    _factory, service, _events, _published = lease_env
    world = lease_world
    middleware = LeaseMiddleware(service, _registry())

    async def scenario() -> None:
        from maa_api.agent.harness.context import RunOutcome

        result = RunOutcome("succeeded", "stop")
        row = await service.acquire(world.holders["a"], purpose="click")
        assert row.ticket_id == world.ticket_ids["a"]

        # 子代理运行结束不释放：根运行还活着。
        subagent = _context(
            run_id="run-sub",
            root_run_id=world.run_ids["a"],
            ticket_id=world.ticket_ids["a"],
            session_id=world.subagent_sessions["a"],
            spec_name="researcher",
        )
        await middleware.on_run_end(subagent, result)
        assert await service.current() is not None

        # 根运行结束：隐式租约释放。
        root = _context(
            run_id=world.run_ids["a"],
            root_run_id=world.run_ids["a"],
            ticket_id=world.ticket_ids["a"],
            session_id=world.worker_sessions["a"],
        )
        await middleware.on_run_end(root, result)
        assert await service.current() is None

        # 抢占记号也由根运行的 on_run_end 清掉；下一次取租约不再报 PREEMPTED。
        await service.acquire(world.holders["a"], purpose="click")
        await service.preempt("manual_pipeline")
        await middleware.on_run_end(root, result)
        again = await service.acquire(world.holders["a"], purpose="click")
        assert again.ticket_id == world.ticket_ids["a"]

    asyncio.run(scenario())


def test_policy_middleware_passes_composed_hook_and_pops(lease_env, lease_world) -> None:
    """``PolicyMiddleware`` 把登记的回调组合后传给 ``invoke``，结束后 pop 掉。"""
    _factory, service, _events, _published = lease_env
    world = lease_world
    from maa_api.agent.harness.builtin_middleware import PolicyMiddleware

    async def scenario() -> None:
        ctx = _context(
            run_id=world.run_ids["a"],
            root_run_id=world.run_ids["a"],
            ticket_id=world.ticket_ids["a"],
            session_id=world.worker_sessions["a"],
        )
        calls: list[tuple[str, int]] = []
        captured: dict[str, Any] = {}

        class _Session:
            async def __aenter__(self) -> None:
                return None

            async def __aexit__(self, *_exc: Any) -> bool:
                return False

        class _FakeConfirmationService:
            registry = _registry()

            async def invoke(self, name, arguments, context, **kwargs):
                captured["kwargs"] = kwargs
                await kwargs["on_audit_committed"](
                    1, 200, {"status": "success", "audit_id": 1, "result": {"ok": True}}
                )
                return {"status": "accepted", "audit_id": 1}

        def _session_factory() -> _Session:
            return _Session()

        middleware = PolicyMiddleware(
            confirmation_service=_FakeConfirmationService(),
            session_factory=_session_factory,
            poll_seconds=0.01,
        )

        async def first(audit_id: int) -> None:
            calls.append(("first", audit_id))

        async def second(audit_id: int) -> None:
            calls.append(("second", audit_id))

        ctx.state["policy.before_execute"] = {"call-5": [first, second]}
        outcome = await middleware.wrap_tool(
            ctx, _call("call-5", "exclusive_tool"), None  # type: ignore[arg-type]
        )
        assert outcome.status == "ok"
        on_before_execute = captured["kwargs"]["on_before_execute"]
        await on_before_execute(9)
        assert calls == [("first", 9), ("second", 9)]
        assert "call-5" not in ctx.state["policy.before_execute"]

        # 没有登记钩子时不传这个参数：v2 路径的调用形状逐字不变。
        captured.clear()
        await middleware.wrap_tool(
            ctx, _call("call-6", "plain_tool"), None  # type: ignore[arg-type]
        )
        assert "on_before_execute" not in captured["kwargs"]

        # 服务为 None 时钩子本身报 DEVICE_LEASE_BUSY（而不是静默放行）。
        missing = LeaseMiddleware(None, _registry())

        async def call_next(call: ToolCall) -> ToolOutcome:
            hooks = ctx.state["policy.before_execute"][call.id]
            await hooks[0](7)
            return ToolOutcome(status="ok")

        try:
            await missing.wrap_tool(ctx, _call("call-7", "exclusive_tool"), call_next)
        except AppError as exc:
            assert exc.code == ErrorCode.DEVICE_LEASE_BUSY
            assert (exc.details or {}).get("reason") == "lease_service_unavailable"
        else:  # pragma: no cover - 失败路径必须命中
            raise AssertionError("服务为 None 时应当抛 DEVICE_LEASE_BUSY")
        assert "call-7" not in ctx.state["policy.before_execute"]

    asyncio.run(scenario())
