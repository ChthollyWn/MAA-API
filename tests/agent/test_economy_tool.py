"""M88 T5：``optimize_farming`` 工具（规格 §4.3.1、§6）。

覆盖：注册元数据、参数恰好一个、server 与开关透传、REST 不写报告、
内置 Worker 把工单传给 optimizer、``app.state.farming_optimizer`` 评测 seam。
"""

from __future__ import annotations

import asyncio
from types import SimpleNamespace
from typing import Any

import pytest

from maa_api.agent.harness.context import CURRENT_RUN, RunContext
from maa_api.agent.registry import ToolContext
from maa_api.agent.specs.registry import builtin_spec
from maa_api.agent.tools import build_registry
from maa_api.agent.tools import economy
from maa_api.agent.tools.economy import OptimizeFarmingParams, optimize_farming
from maa_api.domain.enums import CallerType
from maa_api.domain.errors import AppError, ErrorCode


class _FakeOptimizer:
    """记录调用参数的服务替身；返回固定结果。"""

    def __init__(self, result: dict[str, Any] | None = None) -> None:
        self.calls: list[SimpleNamespace] = []
        self._result = {"status": "complete", "report_artifact_id": None}
        if result is not None:
            self._result = result

    async def optimize(
        self,
        request: Any,
        *,
        write_report: bool,
        session_id: str | None,
        ticket_id: str | None,
    ) -> dict[str, Any]:
        self.calls.append(
            SimpleNamespace(
                request=request,
                write_report=write_report,
                session_id=session_id,
                ticket_id=ticket_id,
            )
        )
        return dict(self._result)


def _context(
    caller: CallerType,
    *,
    state: Any = None,
    run: RunContext | None = None,
) -> ToolContext:
    """REST 用请求上的 ``app.state``；INTERNAL 用运行上的应用。"""
    if run is not None:
        request = SimpleNamespace(app=run.app)
    else:
        request = SimpleNamespace(app=SimpleNamespace(state=state))
    return ToolContext(caller, None, None, request, None)


class _current_run:
    """``CURRENT_RUN`` 的上下文管理器；``run=None`` 时清空。"""

    def __init__(self, run: RunContext | None) -> None:
        self._run = run
        self._token = None

    def __enter__(self) -> None:
        self._token = CURRENT_RUN.set(self._run)

    def __exit__(self, *exc_info: object) -> None:
        CURRENT_RUN.reset(self._token)


def _run(*, state: Any, session_id: str = "s-1", ticket_id: str | None = "t-1") -> RunContext:
    return RunContext(
        run_id="run-m88",
        root_run_id="run-m88",
        parent_run_id=None,
        session_id=session_id,
        ticket_id=ticket_id,
        spec=builtin_spec("planner"),
        app=SimpleNamespace(state=state),
    )


def test_tool_registered_as_safe_economy_tool() -> None:
    """规格 §4.3.1：economy/SAFE、device=none、只读、可重取、不可信、REST 可见。"""

    definition = build_registry().get("optimize_farming")

    assert definition.group == "economy"
    assert definition.risk.value == "SAFE"
    assert definition.device == "none"
    assert definition.rest_visible is True
    assert definition.untrusted_output is True
    assert definition.refetchable is True
    assert definition.model_visible is True
    assert "活动商店" in definition.description
    assert "不提交任务" in definition.description


def test_exactly_one_of_materials_or_plan() -> None:
    """materials 与 plan_id 恰好一个；空字典算没给；材料数至少 1。"""

    registry = build_registry()
    for arguments in (
        {"materials": {"异铁块": 20}, "plan_id": "plan-1"},
        {},
        {"materials": {}},
        {"materials": {"异铁块": 0}},
    ):
        with pytest.raises(AppError) as caught:
            registry.validate("optimize_farming", arguments)

        error = caught.value
        assert error.code is ErrorCode.TOOL_ARGS_INVALID
        assert error.http_status == 422
        assert "issues" in (error.details or {})


def test_server_is_forwarded() -> None:
    """``server`` 原样进 ``FarmingRequest``。"""

    fake = _FakeOptimizer()
    state = SimpleNamespace(farming_optimizer=fake)

    asyncio.run(
        optimize_farming(
            OptimizeFarmingParams(materials={"异铁块": 20}, server="JP"),
            _context(CallerType.REST, state=state),
        )
    )

    assert fake.calls[-1].request.server == "JP"


def test_include_flags_forwarded() -> None:
    """events/navigable/depot/owned_tokens 开关逐项透传。"""

    fake = _FakeOptimizer()
    state = SimpleNamespace(farming_optimizer=fake)

    asyncio.run(
        optimize_farming(
            OptimizeFarmingParams(
                materials={"异铁块": 20},
                include_events=False,
                include_unnavigable=True,
                use_depot=False,
                owned_tokens={"“未来瞻望镜”": 30},
            ),
            _context(CallerType.REST, state=state),
        )
    )

    request = fake.calls[-1].request
    assert request.include_events is False
    assert request.include_unnavigable is True
    assert request.use_depot is False
    assert request.owned_tokens == {"“未来瞻望镜”": 30}


def test_rest_call_does_not_request_report() -> None:
    """REST 调用即使 ``write_report=True`` 也不写报告，不传会话与工单。"""

    fake = _FakeOptimizer(result={"status": "complete", "report_artifact_id": None})
    state = SimpleNamespace(farming_optimizer=fake)

    result = asyncio.run(
        optimize_farming(
            OptimizeFarmingParams(materials={"异铁块": 20}, write_report=True),
            _context(CallerType.REST, state=state),
        )
    )

    call = fake.calls[-1]
    assert call.write_report is False
    assert call.session_id is None
    assert call.ticket_id is None
    assert result["report_artifact_id"] is None


def test_internal_call_passes_ticket_to_optimizer() -> None:
    """内置 Worker 且 ``write_report=true`` 时把会话与工单传给 optimizer。"""

    fake = _FakeOptimizer()
    run = _run(state=SimpleNamespace(farming_optimizer=fake))

    with _current_run(run):
        asyncio.run(
            optimize_farming(
                OptimizeFarmingParams(materials={"异铁块": 20}),
                _context(CallerType.INTERNAL, run=run),
            )
        )

    call = fake.calls[-1]
    assert call.write_report is True
    assert call.session_id == "s-1"
    assert call.ticket_id == "t-1"


def test_service_seam_is_used(monkeypatch: pytest.MonkeyPatch) -> None:
    """``app.state.farming_optimizer`` 优先于默认装配。"""

    def boom(_state: Any = None) -> Any:
        raise AssertionError("有评测 seam 时不该现造默认服务")

    monkeypatch.setattr(economy, "default_farming_optimizer", boom)
    fake = _FakeOptimizer()
    state = SimpleNamespace(farming_optimizer=fake)

    result = asyncio.run(
        optimize_farming(
            OptimizeFarmingParams(materials={"异铁块": 1}),
            _context(CallerType.REST, state=state),
        )
    )

    assert result["status"] == "complete"
    assert len(fake.calls) == 1


def test_default_optimizer_is_built_and_written_back(monkeypatch: pytest.MonkeyPatch) -> None:
    """没有 seam 时现造默认服务并写回 ``app.state``（生产与单测共用同一条路）。"""

    fake = _FakeOptimizer()
    created: list[Any] = []

    def build(state: Any = None) -> _FakeOptimizer:
        created.append(state)
        return fake

    monkeypatch.setattr(economy, "default_farming_optimizer", build)
    state = SimpleNamespace()

    asyncio.run(
        optimize_farming(
            OptimizeFarmingParams(materials={"异铁块": 1}),
            _context(CallerType.REST, state=state),
        )
    )

    assert created == [state]
    assert state.farming_optimizer is fake
