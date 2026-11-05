"""M84 T5：``SpendMiddleware``（spec §4.2、§4.5.6、§6）。

两条主用例：

- 超额：``call_next`` 不被调用，返回 ``SPEND_CAP_EXCEEDED``；``SpendGuard`` 自己
  写的那一条 ``rejected`` 审计存在且带 ``ticket_id``（中间件不写第二条）。
- 非消耗工具、未超额、没装配守卫、守卫抛别的异常：都直通内层。

异步用例一律「同步测试函数 + ``asyncio.run(scenario())``」。
"""

from __future__ import annotations

import asyncio
from datetime import datetime
from pathlib import Path
from types import SimpleNamespace

from sqlalchemy import func, select

from maa_api.agent.harness.context import RunContext, ToolCall, ToolOutcome
from maa_api.agent.specs.registry import builtin_spec
from maa_api.agent.spend import SpendGuard
from maa_api.agent.workers.middleware import SpendMiddleware
from maa_api.db.models import AgentAudit, Confirmation, Ticket
from maa_api.domain.enums import AuditStatus
from maa_api.domain.errors import AppError, ErrorCode
from maa_api.settings import Settings, SpendSettings
from tests.fakes.v3_db import make_session_factory, seed_session

#: 北京 2026-11-05 20:00（naive UTC），消耗日 2026-11-05。
NOW = datetime(2026, 11, 5, 12, 0)


class _Limits:
    """可变的设置提供者，与 ``tests/agent/test_spend.py`` 同口径。"""

    def __init__(self, *, medicine: int = -1, stone: int = -1, expedite: int = -1) -> None:
        self.medicine = medicine
        self.stone = stone
        self.expedite = expedite

    def __call__(self) -> Settings:
        return Settings(
            spend=SpendSettings(
                daily_medicine_limit=self.medicine,
                daily_stone_limit=self.stone,
                daily_expedite_limit=self.expedite,
            )
        )


def _context(*, app: object, session_id: str | None, ticket_id: str | None) -> RunContext:
    return RunContext(
        run_id="run-m84",
        root_run_id="run-m84",
        parent_run_id=None,
        session_id=session_id,
        ticket_id=ticket_id,
        spec=builtin_spec("general"),
        app=app,
    )


def _call(name: str, arguments: dict | None = None) -> ToolCall:
    return ToolCall(
        id=f"call-{name}",
        name=name,
        raw_arguments="{}",
        arguments=arguments or {},
    )


async def _count(factory, model: type) -> int:
    async with factory() as session:
        return int(
            (await session.execute(select(func.count()).select_from(model))).scalar_one()
        )


def test_blocks_before_policy_and_audits(tmp_path: Path) -> None:
    async def scenario() -> None:
        engine, factory = await make_session_factory(tmp_path / "spend-mw.db")
        try:
            async with factory() as session:
                ticket = Ticket(title="日常", kind="general", origin="user")
                session.add(ticket)
                await session.commit()
                ticket_id = str(ticket.id)
            session_id = await seed_session(
                factory, kind="worker", ticket_id=ticket_id
            )
            guard = SpendGuard(
                factory,
                settings_provider=_Limits(stone=0),
                clock=lambda: NOW,
            )
            app = SimpleNamespace(
                state=SimpleNamespace(spend_guard=guard, session_factory=factory)
            )
            ctx = _context(app=app, session_id=session_id, ticket_id=ticket_id)
            called = False

            async def call_next(_call: ToolCall) -> ToolOutcome:  # pragma: no cover
                nonlocal called
                called = True
                return ToolOutcome(status="ok", result={"should": "not run"})

            outcome = await SpendMiddleware().wrap_tool(
                ctx,
                _call(
                    "submit_pipeline",
                    {"tasks": [{"name": "Fight", "stone": 1}]},
                ),
                call_next,
            )

            assert called is False
            assert outcome.status == "error"
            assert outcome.error is not None
            assert outcome.error["code"] == ErrorCode.SPEND_CAP_EXCEEDED.value
            assert outcome.error["details"]["kind"] == "stone"
            assert outcome.error["details"]["limit"] == 0
            assert await _count(factory, Confirmation) == 0

            # 审计只有 SpendGuard.precheck 写的那一条，中间件不重复写。
            async with factory() as session:
                audits = list(
                    (
                        await session.execute(
                            select(AgentAudit).where(
                                AgentAudit.tool_name == "submit_pipeline"
                            )
                        )
                    )
                    .scalars()
                    .all()
                )
            assert len(audits) == 1
            audit = audits[0]
            assert audit.status == AuditStatus.REJECTED
            assert audit.error_code == ErrorCode.SPEND_CAP_EXCEEDED.value
            assert audit.ticket_id == ticket_id
            assert audit.session_id == session_id
        finally:
            engine.sync_engine.dispose()

    asyncio.run(scenario())


def test_passes_other_tools_and_under_cap(tmp_path: Path) -> None:
    class _RecorderGuard:
        def __init__(self) -> None:
            self.calls: list[dict] = []

        async def precheck_tool_call(self, name, arguments, *, agent_session_id):
            self.calls.append(
                {
                    "name": name,
                    "arguments": arguments,
                    "agent_session_id": agent_session_id,
                }
            )

    async def scenario() -> None:
        engine, factory = await make_session_factory(tmp_path / "spend-pass.db")
        try:
            guard = SpendGuard(
                factory,
                settings_provider=_Limits(medicine=5),
                clock=lambda: NOW,
            )
            app = SimpleNamespace(
                state=SimpleNamespace(spend_guard=guard, session_factory=factory)
            )
            ctx = _context(app=app, session_id=None, ticket_id=None)

            async def ok(_call: ToolCall) -> ToolOutcome:
                return ToolOutcome(status="ok", result={"ran": _call.name})

            # 未超额：预检通过，调用内层。
            outcome = await SpendMiddleware().wrap_tool(
                ctx,
                _call("submit_pipeline", {"tasks": [{"name": "Fight", "medicine": 1}]}),
                ok,
            )
            assert outcome.status == "ok"
            assert outcome.result == {"ran": "submit_pipeline"}

            # 非消耗工具：不碰守卫，直接内层。
            recorder = _RecorderGuard()
            non_spend_ctx = _context(
                app=SimpleNamespace(
                    state=SimpleNamespace(
                        spend_guard=recorder, session_factory=factory
                    )
                ),
                session_id=None,
                ticket_id=None,
            )
            outcome = await SpendMiddleware().wrap_tool(
                non_spend_ctx, _call("get_stage"), ok
            )
            assert outcome.status == "ok"
            assert recorder.calls == []

            # 没装配守卫：消耗工具也直通（测试与最小应用）。
            bare_ctx = _context(
                app=SimpleNamespace(state=SimpleNamespace()),
                session_id=None,
                ticket_id=None,
            )
            outcome = await SpendMiddleware().wrap_tool(
                bare_ctx,
                _call("submit_pipeline", {"tasks": [{"name": "Fight", "medicine": 99}]}),
                ok,
            )
            assert outcome.status == "ok"

            # 守卫抛别的 AppError：不拦，交给内层。
            class _OtherErrorGuard:
                async def precheck_tool_call(self, *_args, **_kwargs):
                    raise AppError(ErrorCode.INVALID_PARAMETER, "不是消耗上限")

            other_ctx = _context(
                app=SimpleNamespace(
                    state=SimpleNamespace(
                        spend_guard=_OtherErrorGuard(), session_factory=factory
                    )
                ),
                session_id=None,
                ticket_id=None,
            )
            outcome = await SpendMiddleware().wrap_tool(
                other_ctx, _call("set_task_params", {}), ok
            )
            assert outcome.status == "ok"
            assert outcome.result == {"ran": "set_task_params"}
        finally:
            engine.sync_engine.dispose()

    asyncio.run(scenario())
