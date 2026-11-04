"""M70 T10：DelegationService（spec §4.5.4、§6 的六条）。

用例沿用 M69/M70 的写法：同步测试函数 + ``asyncio.run(scenario())``，临时库用
``tests/fakes/v3_db``；代理定义、模型档案、``run_agent`` 与结构化调用都用替身，
不经过 lifespan，也不真的调模型。运行行用真实的 ``RunService``，这样会话与运行树
的字段是数据库里的真实值。
"""

from __future__ import annotations

import asyncio
import json
from types import SimpleNamespace

import pytest
from sqlalchemy import select

from maa_api.agent.harness.context import RunContext, RunOutcome
from maa_api.agent.harness.events import RunEventBus
from maa_api.agent.llm.schemas.subagent_report import SubagentReport
from maa_api.agent.runs.service import RunService
from maa_api.agent.workers.delegation import DelegationService
from maa_api.db.models import AgentMessage, AgentRun, AgentSession, Ticket
from maa_api.domain.enums import (
    AgentRunStatus,
    AgentSessionKind,
    AgentSessionStatus,
    SpanKind,
)
from maa_api.domain.errors import AppError, ErrorCode
from tests.fakes.v3_db import make_session_factory, seed_run, seed_session

_SPEC_VERSION = "abcdef123456"


def _spec(
    name: str,
    *,
    kind: str = "subagent",
    title: str = "情报员",
    delegates: tuple[str, ...] = (),
    device: str = "none",
    output_schema: str | None = "subagent_report",
    risk_ceiling: str = "none",
    model_role: str = "subagent",
) -> SimpleNamespace:
    return SimpleNamespace(
        name=name,
        title=title,
        kind=kind,
        description=f"{title}的说明",
        model_role=model_role,
        tools=(),
        delegates=tuple(delegates),
        device=device,
        risk_ceiling=risk_ceiling,
        output_schema=output_schema,
        spec_version=_SPEC_VERSION,
        max_steps=8,
        body=f"你是{title}",
    )


def _worker_spec(*, delegates: tuple[str, ...]) -> SimpleNamespace:
    return _spec(
        "general",
        kind="worker",
        title="通用执行",
        delegates=delegates,
        output_schema=None,
        risk_ceiling="consume",
        model_role="worker",
    )


class _Registry:
    def __init__(self, specs: list[SimpleNamespace]) -> None:
        self._specs = {spec.name: spec for spec in specs}

    def get(self, name: str) -> SimpleNamespace:
        if name not in self._specs:
            raise AppError(
                ErrorCode.AGENT_SPEC_NOT_FOUND, f"没有名为 {name} 的代理定义"
            )
        return self._specs[name]

    def list(self) -> list[SimpleNamespace]:
        return list(self._specs.values())


class _Router:
    """subagent 角色的档案替身；``RunService`` 的父运行行另由临时库提供。"""

    def __init__(self) -> None:
        self.roles: list[str] = []

    async def resolve(self, role: str) -> SimpleNamespace:
        self.roles.append(str(role))
        return SimpleNamespace(
            model="test-model",
            base_url="https://llm.test/v1",
            api_key="sk-test",
            currency="CNY",
            supports_json_schema=False,
        )


class _Span:
    def __init__(self, name: str, kind: str, attributes: dict) -> None:
        self.name = name
        self.kind = kind
        self.attributes = dict(attributes)
        self.status = "ok"
        self.span_id = "span-test"
        self.trace_id = "trace-test"

    def end(self, *, status: str = "ok", attributes: dict | None = None) -> None:
        self.status = status
        if attributes:
            self.attributes.update(attributes)


class _Recorder:
    """``SpanRecorder`` 的替身：只记委派 span。"""

    def __init__(self) -> None:
        self.spans: list[_Span] = []

    def start_span(
        self,
        name: str,
        kind: str,
        *,
        trace_id: str,
        run_id: str | None,
        parent=None,
        attributes: dict | None = None,
    ) -> _Span:
        span = _Span(name, kind, attributes or {})
        self.spans.append(span)
        return span


class _Emitter:
    def __init__(self) -> None:
        self.events: list[tuple[str, dict]] = []

    async def __call__(self, name: str, data: dict) -> None:
        self.events.append((name, data))


class _Harness:
    def __init__(
        self,
        factory,
        *,
        specs: list[SimpleNamespace],
        recorder: _Recorder | None = None,
    ) -> None:
        self.factory = factory
        self.registry = _Registry(specs)
        self.router = _Router()
        self.run_service = (
            RunService(factory, boot_id="boot-test") if factory is not None else None
        )
        self.service = DelegationService(
            app=SimpleNamespace(state=SimpleNamespace()),
            session_factory=factory,
            spec_registry=self.registry,
            run_service=self.run_service,
            model_router=self.router,
            span_recorder=recorder,
            event_bus=RunEventBus(run_id=None),
        )


def _use_max_parallel(monkeypatch, value: int) -> None:
    monkeypatch.setattr(
        "maa_api.agent.workers.delegation.get_settings",
        lambda: SimpleNamespace(delegation=SimpleNamespace(max_parallel=value)),
    )


def _report_json(
    summary: str = "1-7 的掉率稳定",
) -> str:
    return json.dumps(
        {
            "summary": summary,
            "findings": [
                {
                    "title": "固源岩掉率稳定",
                    "detail": "样本数超过 100，掉率在正常区间。",
                    "evidence": ["企鹅物流·1-7"],
                }
            ],
            "uncertainties": [],
            "artifacts": [],
        },
        ensure_ascii=False,
    )


async def _make_ticket(factory, *, title: str = "日常") -> str:
    ticket = Ticket(title=title, kind="general", origin="user")
    async with factory() as db:
        db.add(ticket)
        await db.commit()
    return str(ticket.id)


async def _parent(
    factory,
    *,
    spec: SimpleNamespace,
    ticket_id: str,
    state: dict | None = None,
) -> tuple[RunContext, _Emitter]:
    session_id = await seed_session(factory, kind="worker", ticket_id=ticket_id)
    run_id = await seed_run(factory, session_id=session_id, ticket_id=ticket_id)
    emit = _Emitter()
    ctx = RunContext(
        run_id=run_id,
        root_run_id=run_id,
        parent_run_id=None,
        session_id=session_id,
        ticket_id=ticket_id,
        spec=spec,
        app=SimpleNamespace(state=SimpleNamespace()),
        emit=emit,
    )
    ctx.state.update({"ticket.title": "日常", "mandate.ceiling": "consume"})
    if state:
        ctx.state.update(state)
    return ctx, emit


async def _wait_for(predicate, *, timeout: float = 3.0) -> None:
    loop = asyncio.get_running_loop()
    deadline = loop.time() + timeout
    while loop.time() < deadline:
        if predicate():
            return
        await asyncio.sleep(0.01)
    raise AssertionError("等待的条件没有在期限内成立")


async def _subagent_session(factory) -> AgentSession:
    async with factory() as db:
        result = await db.execute(
            select(AgentSession).where(
                AgentSession.kind == AgentSessionKind.SUBAGENT
            )
        )
        return result.scalar_one()


async def _session_messages(factory, session_id: str) -> list[AgentMessage]:
    async with factory() as db:
        result = await db.execute(
            select(AgentMessage)
            .where(AgentMessage.session_id == session_id)
            .order_by(AgentMessage.seq.asc())
        )
        return list(result.scalars().all())


async def _run(factory, run_id: str) -> AgentRun | None:
    async with factory() as db:
        return await db.get(AgentRun, run_id)


def test_readonly_delegations_run_in_parallel(tmp_path, monkeypatch) -> None:
    """两个 researcher 在屏障处同时等待（§4.5.4 并行）。"""
    engine, factory = asyncio.run(make_session_factory(tmp_path / "parallel.db"))
    try:

        async def scenario() -> None:
            _use_max_parallel(monkeypatch, 2)
            harness = _Harness(factory, specs=[_spec("researcher")])
            ticket_id = await _make_ticket(factory)
            parent_ctx, emit = await _parent(
                factory,
                spec=_worker_spec(delegates=("researcher",)),
                ticket_id=ticket_id,
            )
            entered = 0
            both_entered = asyncio.Event()
            shared_cancel: list[bool] = []

            async def fake_run_agent(
                app,
                spec,
                session,
                run,
                *,
                inbox_items,
                cancel_event,
                cancel_reason,
                state,
                parent=None,
            ) -> RunOutcome:
                nonlocal entered
                assert parent is parent_ctx
                assert cancel_event is parent_ctx.cancel_event
                shared_cancel.append(True)
                assert state["role_card"].startswith("## 任务卡")
                assert state["mandate.ceiling"] == "none"
                entered += 1
                if entered == 2:
                    both_entered.set()
                # 只有两个子代理同时进场，这个等待才会结束。
                await asyncio.wait_for(both_entered.wait(), timeout=2.0)
                return RunOutcome(
                    "succeeded", "stop", final_text=_report_json()
                )

            monkeypatch.setattr(
                "maa_api.agent.workers.delegation.run_agent", fake_run_agent
            )
            results = await asyncio.gather(
                harness.service.delegate(
                    parent_ctx,
                    agent="researcher",
                    task="查 1-7 的掉落",
                    context="主线",
                ),
                harness.service.delegate(
                    parent_ctx,
                    agent="researcher",
                    task="查 CE-6 的掉落",
                    context=None,
                ),
            )

            assert entered == 2
            assert shared_cancel == [True, True]
            assert all(result["degraded"] is False for result in results)
            assert all(result["agent"] == "researcher" for result in results)
            assert len({result["run_id"] for result in results}) == 2
            names = [name for name, _ in emit.events]
            assert names.count("subagent_start") == 2
            assert names.count("subagent_end") == 2
            async with factory() as db:
                sessions = list(
                    (
                        await db.execute(
                            select(AgentSession).where(
                                AgentSession.kind == AgentSessionKind.SUBAGENT
                            )
                        )
                    ).scalars()
                )
            assert len(sessions) == 2
            await harness.run_service.close()

        asyncio.run(scenario())
    finally:
        engine.sync_engine.dispose()


def test_max_parallel_and_exclusive_serialization(
    tmp_path, monkeypatch
) -> None:
    """上限 2 时第三个等；两个导航员不重叠（§4.5.4）。"""
    engine, factory = asyncio.run(make_session_factory(tmp_path / "limits.db"))
    try:

        async def scenario() -> None:
            _use_max_parallel(monkeypatch, 2)
            harness = _Harness(
                factory, specs=[_spec("researcher"), _spec("navigator", title="导航员", device="exclusive")]
            )
            ticket_id = await _make_ticket(factory)
            worker_spec = _worker_spec(delegates=("researcher", "navigator"))

            # 只读委派：两个在跑，第三个被信号量挡住。
            parent_a, _ = await _parent(
                factory, spec=worker_spec, ticket_id=ticket_id
            )
            started: list[str] = []
            gate = asyncio.Event()

            async def fake_readonly(
                app, spec, session, run, *, cancel_event, state, parent=None, **_
            ) -> RunOutcome:
                started.append(run.id)
                await gate.wait()
                return RunOutcome(
                    "succeeded", "stop", final_text=_report_json()
                )

            monkeypatch.setattr(
                "maa_api.agent.workers.delegation.run_agent", fake_readonly
            )
            tasks = [
                asyncio.create_task(
                    harness.service.delegate(
                        parent_a,
                        agent="researcher",
                        task=f"查第 {index} 个关卡",
                        context=None,
                    )
                )
                for index in range(3)
            ]
            await _wait_for(lambda: len(started) == 2)
            await asyncio.sleep(0.05)
            assert len(started) == 2
            gate.set()
            results = await asyncio.gather(*tasks)
            assert len(started) == 3
            assert len({result["run_id"] for result in results}) == 3

            # 独占设备：两个导航员按工单锁串行，第二个等第一个退出。
            parent_b, _ = await _parent(
                factory, spec=worker_spec, ticket_id=ticket_id
            )
            entered: list[str] = []
            release = asyncio.Event()

            async def fake_exclusive(
                app, spec, session, run, *, cancel_event, state, parent=None, **_
            ) -> RunOutcome:
                entered.append(run.id)
                await release.wait()
                return RunOutcome(
                    "succeeded", "stop", final_text=_report_json()
                )

            monkeypatch.setattr(
                "maa_api.agent.workers.delegation.run_agent", fake_exclusive
            )
            navigation = [
                asyncio.create_task(
                    harness.service.delegate(
                        parent_b,
                        agent="navigator",
                        task="走到 1-7 关卡界面",
                        context=None,
                    )
                )
                for _ in range(2)
            ]
            await _wait_for(lambda: len(entered) == 1)
            await asyncio.sleep(0.05)
            assert len(entered) == 1
            release.set()
            navigation_results = await asyncio.gather(*navigation)
            assert len(entered) == 2
            assert all(
                result["degraded"] is False for result in navigation_results
            )
            await harness.run_service.close()

        asyncio.run(scenario())
    finally:
        engine.sync_engine.dispose()


def test_delegation_permission_and_depth() -> None:
    """``DELEGATION_NOT_ALLOWED``、``DELEGATION_DEPTH_EXCEEDED``。"""
    worker = _worker_spec(delegates=("researcher",))
    researcher = _spec("researcher", delegates=("researcher",))
    service = DelegationService(
        app=SimpleNamespace(state=SimpleNamespace()),
        session_factory=None,
        spec_registry=_Registry([worker, researcher]),
        run_service=None,
        model_router=_Router(),
        span_recorder=None,
        event_bus=None,
    )

    def context(spec) -> RunContext:
        return RunContext(
            run_id="parent-run",
            root_run_id="parent-run",
            parent_run_id=None,
            session_id="parent-session",
            ticket_id="ticket-1",
            spec=spec,
        )

    async def scenario() -> None:
        with pytest.raises(AppError) as not_allowed:
            await service.delegate(
                context(worker), agent="verifier", task="核对结果", context=None
            )
        assert not_allowed.value.code == ErrorCode.DELEGATION_NOT_ALLOWED
        assert not_allowed.value.details["agent"] == "verifier"
        assert not_allowed.value.details["allowed"] == ["researcher"]

        with pytest.raises(AppError) as depth:
            await service.delegate(
                context(researcher),
                agent="researcher",
                task="再查一层",
                context=None,
            )
        assert depth.value.code == ErrorCode.DELEGATION_DEPTH_EXCEEDED

    asyncio.run(scenario())


def test_subagent_session_and_run_tree(tmp_path, monkeypatch) -> None:
    """``parent_session_id``、``parent_run_id``、``root_run_id``、``ticket_id``。"""
    engine, factory = asyncio.run(make_session_factory(tmp_path / "tree.db"))
    try:

        async def scenario() -> None:
            recorder = _Recorder()
            harness = _Harness(
                factory, specs=[_spec("researcher")], recorder=recorder
            )
            ticket_id = await _make_ticket(factory)
            parent_ctx, emit = await _parent(
                factory,
                spec=_worker_spec(delegates=("researcher",)),
                ticket_id=ticket_id,
            )
            seen: dict = {}

            async def fake_run_agent(
                app,
                spec,
                session,
                run,
                *,
                inbox_items,
                cancel_event,
                cancel_reason,
                state,
                parent=None,
            ) -> RunOutcome:
                seen["parent"] = parent
                seen["cancel_event"] = cancel_event
                seen["session_kind"] = str(session.kind)
                assert inbox_items == []
                return RunOutcome(
                    "succeeded", "stop", final_text=_report_json()
                )

            monkeypatch.setattr(
                "maa_api.agent.workers.delegation.run_agent", fake_run_agent
            )
            result = await harness.service.delegate(
                parent_ctx,
                agent="researcher",
                task="查 1-7 的掉落",
                context="主线 1-7",
            )

            assert result["agent"] == "researcher"
            assert result["degraded"] is False
            assert result["steps_used"] == 0
            assert seen["parent"] is parent_ctx
            assert seen["cancel_event"] is parent_ctx.cancel_event
            assert seen["session_kind"] == AgentSessionKind.SUBAGENT.value

            session = await _subagent_session(factory)
            stored_run = await _run(factory, result["run_id"])
            assert stored_run is not None
            messages = await _session_messages(factory, session.id)
            assert session.parent_session_id == parent_ctx.session_id
            assert session.ticket_id == ticket_id
            assert str(session.status) == AgentSessionStatus.FINISHED.value
            assert session.title == "情报员：查 1-7 的掉落"
            assert stored_run.parent_run_id == parent_ctx.run_id
            assert stored_run.root_run_id == parent_ctx.root_run_id
            assert stored_run.ticket_id == ticket_id
            assert str(stored_run.kind) == "subagent"
            assert str(stored_run.status) == AgentRunStatus.SUCCEEDED.value
            assert stored_run.finished_at is not None
            assert [(str(row.role), row.content) for row in messages] == [
                ("user", "按任务卡完成任务。")
            ]

            names = [name for name, _ in emit.events]
            assert names == ["subagent_start", "subagent_end"]
            assert emit.events[0][1] == {
                "run_id": result["run_id"],
                "parent_run_id": parent_ctx.run_id,
                "agent": "researcher",
                "task": "查 1-7 的掉落",
            }
            assert emit.events[1][1]["status"] == "succeeded"
            assert len(recorder.spans) == 1
            span = recorder.spans[0]
            assert span.name == "agent.delegate"
            assert span.kind == SpanKind.DELEGATE
            assert span.attributes["agent"] == "researcher"
            assert span.attributes["run_id"] == result["run_id"]
            await harness.run_service.close()

        asyncio.run(scenario())
    finally:
        engine.sync_engine.dispose()


def test_invalid_json_retries_once_then_degrades(
    tmp_path, monkeypatch
) -> None:
    """第二次合法则正常；两次都不合法则 ``degraded=true``。"""
    engine, factory = asyncio.run(make_session_factory(tmp_path / "degrade.db"))
    try:

        async def scenario() -> None:
            harness = _Harness(
                factory,
                specs=[
                    _spec("researcher"),
                    _spec(
                        "verifier",
                        title="验证员",
                        device="read",
                        output_schema="verifier_verdict",
                    ),
                ],
            )
            ticket_id = await _make_ticket(factory)
            worker_spec = _worker_spec(delegates=("researcher",))
            calls: list[dict] = []

            async def fake_run_agent(
                app, spec, session, run, *, cancel_event, state, parent=None, **_
            ) -> RunOutcome:
                return RunOutcome(
                    "succeeded", "stop", final_text="这不是 JSON"
                )

            async def retry_ok(**kwargs) -> SimpleNamespace:
                calls.append(kwargs)
                return SimpleNamespace(
                    value=SubagentReport(
                        summary="第二次合法",
                        findings=[],
                        uncertainties=[],
                        artifacts=[],
                    )
                )

            monkeypatch.setattr(
                "maa_api.agent.workers.delegation.run_agent", fake_run_agent
            )
            monkeypatch.setattr(
                "maa_api.agent.workers.delegation.complete_structured", retry_ok
            )

            first_parent, _ = await _parent(
                factory, spec=worker_spec, ticket_id=ticket_id
            )
            first = await harness.service.delegate(
                first_parent, agent="researcher", task="查掉率", context=None
            )
            assert first["degraded"] is False
            assert first["summary"] == "第二次合法"
            assert len(calls) == 1
            assert calls[0]["schema_name"] == "subagent_report"
            assert calls[0]["run_id"] == first["run_id"]
            assert calls[0]["agent_name"] == "researcher"
            first_session = await _subagent_session(factory)
            first_messages = await _session_messages(factory, first_session.id)
            correction = first_messages[-1]
            assert str(correction.role) == "user"
            assert correction.content.startswith("上一条回复不是合法的 JSON：")
            assert "只输出符合格式的 JSON 对象。" in correction.content

            async def retry_fails(**kwargs) -> SimpleNamespace:
                calls.append(kwargs)
                raise AppError(
                    ErrorCode.STRUCTURED_OUTPUT_INVALID,
                    "模型两次都没有给出符合要求的结构化结果",
                )

            monkeypatch.setattr(
                "maa_api.agent.workers.delegation.complete_structured",
                retry_fails,
            )
            second_parent, _ = await _parent(
                factory, spec=worker_spec, ticket_id=ticket_id
            )
            calls.clear()
            second = await harness.service.delegate(
                second_parent, agent="researcher", task="查掉率", context=None
            )
            assert len(calls) == 1
            assert second["degraded"] is True
            assert second["summary"] == "这不是 JSON"
            assert second["findings"] == []
            assert second["uncertainties"] == []
            assert second["artifacts"] == []

            # 验证员 schema 的降级字段不同：passed/confidence/notes。
            calls.clear()
            verifier_parent, _ = await _parent(
                factory,
                spec=_worker_spec(delegates=("verifier",)),
                ticket_id=ticket_id,
            )
            verdict = await harness.service.delegate(
                verifier_parent, agent="verifier", task="核对结果", context=None
            )
            assert len(calls) == 1
            assert verdict["degraded"] is True
            assert verdict["passed"] is False
            assert verdict["confidence"] == 0
            assert verdict["evidence"] == []
            assert verdict["notes"] == "这不是 JSON"
            await harness.run_service.close()

        asyncio.run(scenario())
    finally:
        engine.sync_engine.dispose()


def test_parent_interrupt_stops_subagent(tmp_path, monkeypatch) -> None:
    """子运行随父运行结束；子代理会话收尾为 ``finished``。"""
    engine, factory = asyncio.run(make_session_factory(tmp_path / "interrupt.db"))
    try:

        async def scenario() -> None:
            harness = _Harness(factory, specs=[_spec("researcher")])
            ticket_id = await _make_ticket(factory)
            parent_ctx, emit = await _parent(
                factory,
                spec=_worker_spec(delegates=("researcher",)),
                ticket_id=ticket_id,
            )
            started = asyncio.Event()

            async def fake_run_agent(
                app, spec, session, run, *, cancel_event, state, parent=None, **_
            ) -> RunOutcome:
                assert cancel_event is parent_ctx.cancel_event
                started.set()
                await asyncio.wait_for(cancel_event.wait(), timeout=2.0)
                return RunOutcome("interrupted", "interrupted")

            async def retry_fails(**kwargs) -> SimpleNamespace:
                raise AppError(
                    ErrorCode.STRUCTURED_OUTPUT_INVALID, "取消后不再重试"
                )

            monkeypatch.setattr(
                "maa_api.agent.workers.delegation.run_agent", fake_run_agent
            )
            monkeypatch.setattr(
                "maa_api.agent.workers.delegation.complete_structured",
                retry_fails,
            )

            task = asyncio.create_task(
                harness.service.delegate(
                    parent_ctx, agent="researcher", task="查很久", context=None
                )
            )
            await asyncio.wait_for(started.wait(), timeout=2.0)
            parent_ctx.cancel_event.set()
            result = await asyncio.wait_for(task, timeout=2.0)

            assert result["degraded"] is True
            stored_run = await _run(factory, result["run_id"])
            assert stored_run is not None
            assert str(stored_run.status) == AgentRunStatus.INTERRUPTED.value
            session = await _subagent_session(factory)
            assert str(session.status) == AgentSessionStatus.FINISHED.value
            assert emit.events[-1][0] == "subagent_end"
            assert emit.events[-1][1]["status"] == "interrupted"
            await harness.run_service.close()

        asyncio.run(scenario())
    finally:
        engine.sync_engine.dispose()
