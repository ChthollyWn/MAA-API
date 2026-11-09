"""M87 T3：委派验证员后的结论落库（spec §4.5.4、§6）。

照 ``test_delegation_service.py`` 的替身写法：代理定义、模型档案、``run_agent`` 与
结构化调用都用替身，运行行用真实的 ``RunService``。这里只关心一件事：验证员子运行
收尾后 ``agent_run.meta["verdict"]`` 是否按 §4.5.4 写入，以及写失败时委派是否照常返回。
"""

from __future__ import annotations

import asyncio
import json
from types import SimpleNamespace

from sqlalchemy import select

from maa_api.agent.harness.context import RunContext, RunOutcome
from maa_api.agent.harness.events import RunEventBus
from maa_api.agent.llm.schemas.subagent_report import SubagentReport
from maa_api.agent.runs.service import RunService
from maa_api.agent.workers.delegation import DelegationService
from maa_api.agent.workers.verification import VerificationGate
from maa_api.db.models import AgentRun, Ticket
from maa_api.domain.errors import AppError, ErrorCode
from tests.fakes.v3_db import make_session_factory, seed_run, seed_session

_SPEC_VERSION = "abcdef123456"


def _spec(
    name: str,
    *,
    kind: str = "subagent",
    title: str = "情报员",
    delegates: tuple[str, ...] = (),
    output_schema: str | None = "subagent_report",
    risk_ceiling: str = "none",
) -> SimpleNamespace:
    return SimpleNamespace(
        name=name,
        title=title,
        kind=kind,
        description=f"{title}的说明",
        model_role="subagent",
        tools=(),
        delegates=tuple(delegates),
        device="none",
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
    )


def _verifier_spec() -> SimpleNamespace:
    return _spec(
        "verifier",
        title="验证员",
        output_schema="verifier_verdict",
        risk_ceiling="none",
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
    async def resolve(self, role: str) -> SimpleNamespace:
        return SimpleNamespace(
            model="test-model",
            base_url="https://llm.test/v1",
            api_key="sk-test",
            currency="CNY",
            supports_json_schema=False,
        )


class _Harness:
    def __init__(self, factory, specs: list[SimpleNamespace]) -> None:
        self.factory = factory
        self.run_service = RunService(factory, boot_id="boot-test")
        self.service = DelegationService(
            app=SimpleNamespace(state=SimpleNamespace()),
            session_factory=factory,
            spec_registry=_Registry(specs),
            run_service=self.run_service,
            model_router=_Router(),
            span_recorder=None,
            event_bus=RunEventBus(run_id=None),
        )


async def _write_ticket(factory, *, title: str = "日常") -> str:
    ticket = Ticket(title=title, kind="daily_ops", origin="user")
    async with factory() as db:
        db.add(ticket)
        await db.commit()
    return str(ticket.id)


async def _parent(
    factory,
    *,
    spec: SimpleNamespace,
    ticket_id: str,
) -> RunContext:
    session_id = await seed_session(factory, kind="worker", ticket_id=ticket_id)
    run_id = await seed_run(factory, session_id=session_id, ticket_id=ticket_id)
    ctx = RunContext(
        run_id=run_id,
        root_run_id=run_id,
        parent_run_id=None,
        session_id=session_id,
        ticket_id=ticket_id,
        spec=spec,
        app=SimpleNamespace(state=SimpleNamespace()),
    )
    ctx.state.update({"ticket.title": "日常", "mandate.ceiling": "consume"})
    return ctx


async def _run(factory, run_id: str) -> AgentRun | None:
    async with factory() as db:
        return await db.get(AgentRun, run_id)


def _verdict_json(
    *,
    passed: bool = True,
    confidence: float = 0.9,
    notes: str = "",
) -> str:
    return json.dumps(
        {
            "passed": passed,
            "confidence": confidence,
            "evidence": ["截图显示 1-7 三星"],
            "notes": notes,
        },
        ensure_ascii=False,
    )


def test_verifier_output_is_recorded(tmp_path, monkeypatch) -> None:
    engine, factory = asyncio.run(make_session_factory(tmp_path / "verdict.db"))
    try:

        async def scenario() -> None:
            harness = _Harness(
                factory,
                specs=[
                    _verifier_spec(),
                    _spec("researcher"),
                ],
            )
            ticket_id = await _write_ticket(factory)
            parent_ctx = await _parent(
                factory,
                spec=_worker_spec(delegates=("verifier",)),
                ticket_id=ticket_id,
            )

            async def fake_run_agent(
                app, spec, session, run, *, cancel_event, state, parent=None, **_
            ) -> RunOutcome:
                return RunOutcome(
                    "succeeded", "stop", final_text=_verdict_json()
                )

            monkeypatch.setattr(
                "maa_api.agent.workers.delegation.run_agent", fake_run_agent
            )
            result = await harness.service.delegate(
                parent_ctx, agent="verifier", task="核对结果", context=None
            )

            assert result["passed"] is True
            stored = await _run(factory, result["run_id"])
            assert stored is not None
            verdict = (stored.meta or {})["verdict"]
            assert verdict["passed"] is True
            assert verdict["confidence"] == 0.9
            assert verdict["evidence"]
            assert verdict["degraded"] is False
            assert verdict["recorded_at"].endswith("Z")
            # 原有 meta 键要保留（示例：运行上已开启的工具组由别处写入）。
            assert isinstance(stored.meta, dict)
            await harness.run_service.close()

        asyncio.run(scenario())
    finally:
        engine.sync_engine.dispose()


def test_degraded_verifier_output_is_recorded(tmp_path, monkeypatch) -> None:
    engine, factory = asyncio.run(make_session_factory(tmp_path / "degraded.db"))
    try:

        async def scenario() -> None:
            harness = _Harness(factory, specs=[_verifier_spec()])
            ticket_id = await _write_ticket(factory)
            parent_ctx = await _parent(
                factory,
                spec=_worker_spec(delegates=("verifier",)),
                ticket_id=ticket_id,
            )

            async def fake_run_agent(
                app, spec, session, run, *, cancel_event, state, parent=None, **_
            ) -> RunOutcome:
                return RunOutcome("succeeded", "stop", final_text="这不是 JSON")

            async def retry_fails(**kwargs) -> SubagentReport:
                raise AppError(
                    ErrorCode.STRUCTURED_OUTPUT_INVALID,
                    "模型两次都没有给出符合要求的结构化结果",
                )

            monkeypatch.setattr(
                "maa_api.agent.workers.delegation.run_agent", fake_run_agent
            )
            monkeypatch.setattr(
                "maa_api.agent.workers.delegation.complete_structured", retry_fails
            )
            result = await harness.service.delegate(
                parent_ctx, agent="verifier", task="核对结果", context=None
            )

            assert result["degraded"] is True
            stored = await _run(factory, result["run_id"])
            assert stored is not None
            verdict = (stored.meta or {})["verdict"]
            assert verdict["degraded"] is True
            assert verdict["passed"] is False
            assert verdict["confidence"] == 0
            assert verdict["notes"] == "验证员没有给出合法结论"
            await harness.run_service.close()

        asyncio.run(scenario())
    finally:
        engine.sync_engine.dispose()


def test_other_subagents_are_not_recorded(tmp_path, monkeypatch) -> None:
    engine, factory = asyncio.run(make_session_factory(tmp_path / "researcher.db"))
    try:

        async def scenario() -> None:
            harness = _Harness(factory, specs=[_spec("researcher")])
            ticket_id = await _write_ticket(factory)
            parent_ctx = await _parent(
                factory,
                spec=_worker_spec(delegates=("researcher",)),
                ticket_id=ticket_id,
            )

            async def fake_run_agent(
                app, spec, session, run, *, cancel_event, state, parent=None, **_
            ) -> RunOutcome:
                return RunOutcome(
                    "succeeded",
                    "stop",
                    final_text=json.dumps(
                        {
                            "summary": "1-7 掉率稳定",
                            "findings": [],
                            "uncertainties": [],
                            "artifacts": [],
                        },
                        ensure_ascii=False,
                    ),
                )

            monkeypatch.setattr(
                "maa_api.agent.workers.delegation.run_agent", fake_run_agent
            )
            result = await harness.service.delegate(
                parent_ctx, agent="researcher", task="查掉率", context=None
            )

            stored = await _run(factory, result["run_id"])
            assert stored is not None
            assert "verdict" not in (stored.meta or {})
            await harness.run_service.close()

        asyncio.run(scenario())
    finally:
        engine.sync_engine.dispose()


def test_record_failure_does_not_break_delegation(tmp_path, monkeypatch) -> None:
    engine, factory = asyncio.run(make_session_factory(tmp_path / "meta-fail.db"))
    try:

        async def scenario() -> None:
            harness = _Harness(factory, specs=[_verifier_spec()])
            ticket_id = await _write_ticket(factory)
            parent_ctx = await _parent(
                factory,
                spec=_worker_spec(delegates=("verifier",)),
                ticket_id=ticket_id,
            )

            async def fake_run_agent(
                app, spec, session, run, *, cancel_event, state, parent=None, **_
            ) -> RunOutcome:
                return RunOutcome(
                    "succeeded", "stop", final_text=_verdict_json()
                )

            async def record_fails(self, **_kwargs) -> None:
                raise RuntimeError("数据库写失败")

            monkeypatch.setattr(
                "maa_api.agent.workers.delegation.run_agent", fake_run_agent
            )
            monkeypatch.setattr(
                VerificationGate, "record_verdict", record_fails
            )
            result = await harness.service.delegate(
                parent_ctx, agent="verifier", task="核对结果", context=None
            )

            assert result["passed"] is True
            assert result["agent"] == "verifier"
            stored = await _run(factory, result["run_id"])
            assert stored is not None
            assert "verdict" not in (stored.meta or {})
            await harness.run_service.close()

        asyncio.run(scenario())
    finally:
        engine.sync_engine.dispose()
