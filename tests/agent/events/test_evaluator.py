"""研判代理与事件卡（M81 规格 §4.2、§4.7、08 §4.3、§6）。

覆盖 §6 的四条：accept 返回草稿且运行成功；两次非法输出记 UNAVAILABLE 且运行
failed；worker 与规则不一致时按规则并在 reason 里说明；事件卡至多 8 张进行中
工单与 5 条近期事件。模型用 ``FakeLLM``，运行服务与临时库是真实的。
"""

from __future__ import annotations

import asyncio
import json
from types import SimpleNamespace
from typing import Any

import pytest
from sqlalchemy import select

from maa_api.agent.events.builtin_rules import BUILTIN_RULES
from maa_api.agent.events.evaluator import TriggerEvaluator
from maa_api.agent.events.rules import merge_rules
from maa_api.agent.llm.profiles import ResolvedProfile
from maa_api.agent.runs.service import RunService
from maa_api.agent.specs.registry import AgentSpecRegistry, builtin_spec
from maa_api.agent.tools import build_registry
from maa_api.db.models import AgentRun, AgentTrigger, Ticket, utcnow
from maa_api.db.repositories.triggers import TriggerRepository
from maa_api.domain.enums import TriggerEvalStatus, TriggerMode
from tests.fakes.fake_llm import FakeLLM, text_response
from tests.fakes.v3_db import make_session_factory
from tests.db.conftest import (  # noqa: F401  # pytest 从模块命名空间发现 fixture
    db_engine,
    db_path,
    db_session,
    db_session_factory,
)

_PROFILE = ResolvedProfile(
    name="trigger_eval",
    kind="chat",
    provider="openai_compatible",
    base_url="https://llm.test/v1",
    api_key="sk-test",
    model="cheap-model",
    context_window=128_000,
    max_output_tokens=None,
    supports_vision=None,
    supports_tools=False,
    supports_json_schema=True,
    dynamic_context_position="tail_system",
    price_input=None,
    price_cached_input=None,
    price_output=None,
    currency="CNY",
    embedding_dim=None,
    local_model_path=None,
    readonly=False,
)


class _Handle:
    def __init__(self, client: Any) -> None:
        self.client = client

    async def aclose(self) -> None:
        return None


class _Router:
    def __init__(self, llm: FakeLLM) -> None:
        self.llm = llm
        self.roles: list[str] = []

    async def resolve(self, role: str) -> ResolvedProfile:
        self.roles.append(role)
        return _PROFILE

    def client_for(self, _profile: Any) -> _Handle:
        return _Handle(self.llm)


class _Ledger:
    def __init__(self) -> None:
        self.records: list[dict[str, Any]] = []

    async def record(self, **kwargs: Any) -> int:
        self.records.append(kwargs)
        return 0

    async def run_spent_micros(self, _run_id: str) -> int:
        return 0

    async def spent_today_micros(self, *, now: Any = None) -> int:
        return 0

    def warn(self, **_kwargs: Any) -> None:
        return None


@pytest.fixture
def factory(db_path):
    engine, session_factory = asyncio.run(make_session_factory(db_path))
    yield session_factory
    engine.sync_engine.dispose()


def _decision(**overrides: Any) -> dict[str, Any]:
    values = {
        "decision": "accept",
        "worker": "daily_ops",
        "title": "提交一条只用理智的刷图流水线",
        "brief": "理智估计已满，提交一条现有理智的刷图流水线。",
        "scope": "提交刷图流水线并盯到结束；不用理智药，不碎石。",
        "reason": "理智已满且没有排队中的流水线。",
    }
    values.update(overrides)
    return values


def _response(payload: dict[str, Any]):
    return text_response(json.dumps(payload, ensure_ascii=False))


def _rule():
    rule = merge_rules(
        BUILTIN_RULES, [], default_mode=TriggerMode.OBSERVE
    ).get("sanity-full")
    assert rule is not None
    return rule


def _evaluator(factory, llm: FakeLLM, *, ledger: _Ledger | None = None):
    registry = AgentSpecRegistry(build_registry())
    registry.load()
    run_service = RunService(factory, boot_id="m81-t4")
    ledger = ledger or _Ledger()
    app = SimpleNamespace(state=SimpleNamespace(cost_ledger=ledger))
    evaluator = TriggerEvaluator(
        app=app,
        session_factory=factory,
        spec_registry=registry,
        run_service=run_service,
        model_router=_Router(llm),
    )
    return evaluator, run_service


async def _seed_trigger(
    factory,
    *,
    kind: str = "system.sanity_full",
    thread_key: str = "system:sanity",
    payload: dict[str, Any] | None = None,
) -> AgentTrigger:
    async with factory() as session:
        row = await TriggerRepository(session).insert(
            AgentTrigger(
                source="system",
                kind=kind,
                thread_key=thread_key,
                payload=payload or {"estimated": 135, "max_sanity": 135},
                created_at=utcnow(),
            )
        )
        await session.commit()
        return row


async def _runs(factory) -> list[AgentRun]:
    async with factory() as session:
        result = await session.execute(select(AgentRun).order_by(AgentRun.started_at.asc()))
        return list(result.scalars().all())


def test_accept_decision_returns_draft_and_finishes_run(factory):
    async def scenario():
        llm = FakeLLM([_response(_decision())])
        evaluator, run_service = _evaluator(factory, llm)
        trigger = await _seed_trigger(factory)
        rule = _rule()

        outcome = await evaluator.evaluate(trigger, rule)

        assert outcome.status is TriggerEvalStatus.ACCEPT
        assert outcome.decision is not None
        assert outcome.decision.title == "提交一条只用理智的刷图流水线"
        assert outcome.decision.brief
        assert outcome.decision.scope
        assert outcome.reason == "理智已满且没有排队中的流水线。"
        runs = await _runs(factory)
        assert len(runs) == 1
        run = runs[0]
        assert run.id == outcome.run_id
        assert run.kind == "trigger_eval"
        assert run.agent_name == "trigger_evaluator"
        assert run.status == "succeeded"
        assert run.finish_reason == "stop"
        assert run.session_id is None
        assert run.spec_version == builtin_spec("trigger_evaluator").spec_version
        # 消息 = 定义正文 + 事件卡；角色是廉价研判角色，schema 是 trigger_decision。
        assert len(llm.turns) == 1
        _model, messages, _tools = llm.turns[0]
        assert messages[0] == {
            "role": "system",
            "content": builtin_spec("trigger_evaluator").body,
        }
        assert messages[1]["role"] == "user"
        assert "【事件】#" in messages[1]["content"]
        assert "理智估计已满（135/135）" in messages[1]["content"]
        assert "trigger_decision" in json.dumps(
            llm.request_options[0].get("response_format"), ensure_ascii=False
        )
        await run_service.close()

    asyncio.run(scenario())


def test_invalid_output_twice_is_unavailable(factory):
    async def scenario():
        llm = FakeLLM([text_response("不是 JSON"), text_response("仍然不是")])
        evaluator, run_service = _evaluator(factory, llm)
        trigger = await _seed_trigger(factory)

        outcome = await evaluator.evaluate(trigger, _rule())

        assert outcome.status is TriggerEvalStatus.UNAVAILABLE
        assert outcome.decision is None
        assert outcome.reason == "研判不可用：STRUCTURED_OUTPUT_INVALID"
        runs = await _runs(factory)
        assert len(runs) == 1
        assert runs[0].status == "failed"
        assert runs[0].error_code == "STRUCTURED_OUTPUT_INVALID"
        assert outcome.run_id == runs[0].id
        assert len(llm.turns) == 2
        await run_service.close()

    asyncio.run(scenario())


def test_worker_mismatch_falls_back_to_rule_worker(factory):
    async def scenario():
        llm = FakeLLM([_response(_decision(worker="ops"))])
        evaluator, run_service = _evaluator(factory, llm)
        trigger = await _seed_trigger(factory)

        outcome = await evaluator.evaluate(trigger, _rule())

        assert outcome.status is TriggerEvalStatus.ACCEPT
        assert outcome.decision is not None
        assert outcome.decision.worker == "daily_ops"
        assert outcome.reason.endswith("（已按规则改用 daily_ops）")
        await run_service.close()

    asyncio.run(scenario())


def test_card_lists_open_tickets_and_recent_thread_events(factory):
    async def scenario():
        llm = FakeLLM([])
        evaluator, run_service = _evaluator(factory, llm)

        # 同一话题先来 7 条事件，当前事件最后入表，事件卡只看它之前的 5 条。
        for index in range(7):
            await _seed_trigger(
                factory,
                payload={"estimated": index, "max_sanity": 135},
            )
        current = await _seed_trigger(factory)

        async with factory() as session:
            for index in range(10):
                session.add(
                    Ticket(
                        id=f"ticket-{index:04d}",
                        title=f"处理事件 {index}",
                        kind="daily_ops",
                        status="open" if index % 2 == 0 else "waiting",
                        origin="trigger",
                        summary=f"第 {index} 张工单的摘要",
                        updated_at=utcnow(),
                    )
                )
            session.add(
                Ticket(
                    id="ticket-done",
                    title="已经结束的工单",
                    kind="daily_ops",
                    status="done",
                    origin="trigger",
                )
            )
            await session.commit()

        card = await evaluator.render_card(current, _rule())

        header, recent_section = card.split("【这个话题最近的事件】")
        ticket_lines = [line for line in header.splitlines() if line.startswith("- ")]
        assert len(ticket_lines) == 8
        recent_lines = [
            line for line in recent_section.splitlines() if line.startswith("- ")
        ]
        assert len(recent_lines) == 5
        assert "ticket-done" not in card
        assert "sanity-full" in card
        assert builtin_spec("daily_ops").description in card
        assert "【事件】#" in card
        assert "【规则】sanity-full「理智已满」" in card
        await run_service.close()

    asyncio.run(scenario())
