"""目标事件与复盘规则（M82 规格 §4.2、§4.7、§6）。

覆盖 §6 的三条：

- FakeLLM 研判 accept → ``goal-review`` 建 inferred、上限 none、``origin=goal``、
  带 ``goal_id`` 的 ``planner`` 工单，说明末尾附 ``goal_review`` 汇报格式；
- 研判 ignore 只留事件记录，不建工单；
- 规划工单还开着（订阅 ``goal:<id>``）时 ``goal.review_due`` 直通它，不另建单。

临时库、工单、话题、事件、规则与研判代理都是真实实现；只有 Worker 建会话与
模型是替身（``FakeLLM``）。
"""

from __future__ import annotations

import asyncio
import json
from types import SimpleNamespace
from typing import Any

from sqlalchemy import func, select

from maa_api.agent.events.bus import TriggerService
from maa_api.agent.events.evaluator import TriggerEvaluator
from maa_api.agent.events.rules import TriggerRuleService
from maa_api.agent.llm.profiles import ResolvedProfile
from maa_api.agent.runs.service import RunService
from maa_api.agent.specs.registry import AgentSpecRegistry
from maa_api.agent.tickets.inbox import InboxService
from maa_api.agent.tickets.service import TicketService
from maa_api.agent.tickets.threads import ThreadRouter
from maa_api.agent.tools import build_registry
from maa_api.db.models import AgentInbox, AgentTrigger, Goal, Ticket, utcnow
from maa_api.db.repositories.ticket import (
    MandateRepository,
    ThreadSubscriptionRepository,
    TicketRepository,
)
from maa_api.domain.enums import (
    EventSource,
    InboxKind,
    MandateStrength,
    RiskLevel,
    TicketOrigin,
    TriggerEvalStatus,
    TriggerRoute,
)
from maa_api.settings import Settings
from tests.fakes.fake_llm import FakeLLM, text_response
from tests.fakes.v3_db import make_session_factory, seed_session

#: 目标 id 必须是 UUID：话题键 ``goal:<uuid>`` 由 03 §15 的格式校验。
_GOAL_ID = "0f8fad5b-d9cb-469f-a165-70867728950e"
_GOAL_THREAD = f"goal:{_GOAL_ID}"

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
    """只认 ``trigger_eval`` 角色的模型路由。"""

    def __init__(self, llm: FakeLLM) -> None:
        self.llm = llm

    async def resolve(self, _role: str) -> ResolvedProfile:
        return _PROFILE

    def client_for(self, _profile: Any) -> _Handle:
        return _Handle(self.llm)


class _Ledger:
    """不落成本账的账本替身。"""

    async def record(self, **_kwargs: Any) -> int:
        return 0

    async def run_spent_micros(self, _run_id: str) -> int:
        return 0

    async def spent_today_micros(self, *, now: Any = None) -> int:
        return 0

    def warn(self, **_kwargs: Any) -> None:
        return None


class _WorkerService:
    """替身 Worker 服务：建一个 ``kind=worker`` 的会话行，不跑模型。"""

    def __init__(self, factory: Any) -> None:
        self._factory = factory

    async def ensure_worker_session(self, ticket: Ticket) -> SimpleNamespace:
        session_id = await seed_session(
            self._factory, kind="worker", ticket_id=ticket.id
        )
        return SimpleNamespace(id=session_id)


class _Harness:
    """临时库上的真实服务 + FakeLLM 研判。"""

    def __init__(self, factory: Any, llm: FakeLLM) -> None:
        self.factory = factory
        self.settings = Settings()
        self.inbox = InboxService(factory)
        self.router = ThreadRouter(factory, self.inbox)
        self.registry = AgentSpecRegistry(build_registry())
        self.registry.load()
        self.ticket_service = TicketService(
            factory,
            inbox_service=self.inbox,
            thread_router=self.router,
            spec_registry=self.registry,
            broadcast=lambda *_args: None,
        )
        self.rules = TriggerRuleService(
            factory,
            spec_registry=self.registry,
            settings_provider=lambda: self.settings,
        )
        app = SimpleNamespace(
            state=SimpleNamespace(
                worker_service=_WorkerService(factory), cost_ledger=_Ledger()
            )
        )
        self.evaluator = TriggerEvaluator(
            app=app,
            session_factory=factory,
            spec_registry=self.registry,
            run_service=RunService(factory, boot_id="m82-t4"),
            model_router=_Router(llm),
        )
        self.service = TriggerService(
            factory,
            app=app,
            rules=self.rules,
            evaluator=self.evaluator,
            ticket_service=self.ticket_service,
            thread_router=self.router,
            orchestrator_service=SimpleNamespace(),
            broadcast=lambda *_args: None,
            settings_provider=lambda: self.settings,
        )


def _decision(**overrides: Any) -> dict[str, Any]:
    values = {
        "decision": "accept",
        "worker": "planner",
        "title": "调整银灰养成计划",
        "brief": "里程碑 1 逾期，养成计划的材料进度落后。",
        "scope": "复盘目标「银灰精二」并调整计划与定时任务，只做不需要确认的操作。",
        "reason": "有里程碑逾期",
    }
    values.update(overrides)
    return values


def _accept_llm(**overrides: Any) -> FakeLLM:
    payload = json.dumps(_decision(**overrides), ensure_ascii=False)
    return FakeLLM([text_response(payload)])


def _sample_payload(goal_id: str) -> dict[str, Any]:
    return {
        "goal_id": goal_id,
        "title": "银灰精二",
        "snapshot": {
            "now": "2026-11-05T12:00:00Z",
            "days_left": 2,
            "newly_missed": [{"ord": 1, "title": "精二", "due_at": "2026-11-04T22:00:00Z"}],
            "plan": {"id": "plan-1", "status": "active"},
            "schedules": {"ok": [], "disabled": ["s-1"], "missing": []},
        },
    }


async def _seed_goal(factory: Any, goal_id: str = _GOAL_ID) -> None:
    async with factory() as db:
        db.add(
            Goal(
                id=goal_id,
                title="银灰精二",
                target={"text": "月底前把银灰练到精二"},
                progress={"planning": "pending"},
                created_at=utcnow(),
                updated_at=utcnow(),
            )
        )
        await db.commit()


async def _count(factory: Any, model: Any) -> int:
    async with factory() as db:
        return int(
            (await db.execute(select(func.count()).select_from(model))).scalar_one()
        )


async def _inbox(factory: Any, session_id: str) -> list[AgentInbox]:
    async with factory() as db:
        result = await db.execute(
            select(AgentInbox).where(AgentInbox.session_id == session_id)
        )
        return list(result.scalars().all())


def test_goal_review_rule_creates_inferred_planner_ticket_with_goal_id(
    tmp_path,
) -> None:
    async def scenario() -> None:
        engine, factory = await make_session_factory(tmp_path / "goal-events.db")
        try:
            await _seed_goal(factory)
            llm = _accept_llm()
            harness = _Harness(factory, llm)

            result = await harness.service.publish(
                source=EventSource.GOAL,
                kind="goal.review_due",
                thread_key=_GOAL_THREAD,
                payload=_sample_payload(_GOAL_ID),
            )

            assert result.routed_to is TriggerRoute.TICKET
            assert result.ticket_id is not None
            assert result.eval_status is TriggerEvalStatus.ACCEPT
            async with factory() as db:
                ticket = await TicketRepository(db).get(result.ticket_id)
                mandate = await MandateRepository(db).get_by_ticket(result.ticket_id)
                subscriptions = await ThreadSubscriptionRepository(
                    db
                ).list_for_ticket(result.ticket_id)
            assert ticket is not None
            assert ticket.kind == "planner"
            assert ticket.goal_id == _GOAL_ID
            assert ticket.origin == TicketOrigin.GOAL.value
            assert ticket.source_trigger_id == result.trigger_id
            assert ticket.worker_session_id is not None
            assert ticket.title == "调整银灰养成计划"
            assert mandate is not None
            assert mandate.strength == MandateStrength.INFERRED.value
            assert mandate.risk_ceiling == RiskLevel.NONE.value
            assert mandate.rule_id == "goal-review"
            assert mandate.sender == "rule:goal-review"
            assert mandate.source == EventSource.GOAL.value
            assert mandate.scope == _decision()["scope"]
            assert any(item.active for item in subscriptions)

            # 说明末尾是固定的 goal_review 汇报格式，且带上目标 id。
            items = await _inbox(factory, ticket.worker_session_id)
            texts = [
                item.payload.get("text")
                for item in items
                if item.payload.get("text")
            ]
            assert len(texts) == 1
            text = texts[0]
            assert "这是推断委托的复盘工单" in text
            assert f"目标 id：{_GOAL_ID}" in text
            assert '"goal_review"' in text
            assert '"milestones": [{"ord": 1, "status": "done"' in text
            assert text.rstrip().endswith(
                "没有需要告诉用户的变化时汇报 dropped。"
            )
            # 研判提示来自内置规则，事件卡里能看到 §4.7 的口径。
            assert llm.turns
            assert "这是一个目标的定时复盘" in llm.turns[0][1][1]["content"]
        finally:
            await engine.dispose()

    asyncio.run(scenario())


def test_goal_review_ignore_records_only(tmp_path) -> None:
    async def scenario() -> None:
        engine, factory = await make_session_factory(tmp_path / "goal-ignore.db")
        try:
            await _seed_goal(factory)
            llm = FakeLLM(
                [
                    text_response(
                        json.dumps(
                            {"decision": "ignore", "reason": "进度正常"},
                            ensure_ascii=False,
                        )
                    )
                ]
            )
            harness = _Harness(factory, llm)

            result = await harness.service.publish(
                source=EventSource.GOAL,
                kind="goal.review_due",
                thread_key=_GOAL_THREAD,
                payload=_sample_payload(_GOAL_ID),
            )

            assert result.routed_to is TriggerRoute.NONE
            assert result.ticket_id is None
            assert result.eval_status is TriggerEvalStatus.IGNORE
            assert await _count(factory, Ticket) == 0
            # 事件本身留了记录，供事件列表与审计查看。
            async with factory() as db:
                row = await db.get(AgentTrigger, result.trigger_id)
            assert row is not None and row.processed_at is not None

        finally:
            await engine.dispose()

    asyncio.run(scenario())


def test_review_passes_through_open_planning_ticket(tmp_path) -> None:
    async def scenario() -> None:
        engine, factory = await make_session_factory(tmp_path / "goal-passthrough.db")
        try:
            await _seed_goal(factory)
            llm = FakeLLM([])
            harness = _Harness(factory, llm)
            planning, _ = await harness.ticket_service.create(
                kind="planner",
                title="规划：银灰精二",
                brief="把目标拆成里程碑。",
                scope="只改动为这个目标新建的计划与定时任务。",
                origin=TicketOrigin.GOAL,
                source=EventSource.GOAL,
                sender="rule:goal-review",
                strength=MandateStrength.EXPLICIT,
                goal_id=_GOAL_ID,
            )
            await harness.router.subscribe(planning.id, _GOAL_THREAD)

            result = await harness.service.publish(
                source=EventSource.GOAL,
                kind="goal.review_due",
                thread_key=_GOAL_THREAD,
                payload=_sample_payload(_GOAL_ID),
            )

            assert result.ticket_id == planning.id
            assert result.routed_to is TriggerRoute.TICKET
            assert result.eval_status is TriggerEvalStatus.SKIPPED
            assert await _count(factory, Ticket) == 1
            assert llm.requests == []
            items = await _inbox(factory, str(planning.worker_session_id))
            events = [
                item
                for item in items
                if item.kind == str(InboxKind.THREAD_EVENT)
            ]
            assert len(events) == 1
            assert events[0].ref_id == str(result.trigger_id)
            assert events[0].payload["kind"] == "goal.review_due"
            assert "到了复盘时间" in events[0].payload["summary"]
        finally:
            await engine.dispose()

    asyncio.run(scenario())
