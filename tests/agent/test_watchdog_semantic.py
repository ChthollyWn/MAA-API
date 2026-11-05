"""``WatchdogService`` 的语义检查与 ``watchdog`` 定义（M83 spec §4.5/§6，T3）。

覆盖 spec §6 的两条：只有被确定性检查挑中的工单才调模型；``mark_blocked`` 不新建
诊断工单。另补：每轮至多 2 张、每张每小时至多 1 次、模型失败跳过、record 档只记、
``mark_blocked`` 要求本轮确定性命中。

测试纪律与 G2 的 ``test_watchdog_service.py`` 相同：临时库与生产同口径，模型用
``FakeLLM``，运行服务真实、工单服务是只记录汇报的替身；用例形态是同步函数 +
``asyncio.run``（本仓没有 pytest-asyncio）。
"""

from __future__ import annotations

import asyncio
import json
import re
from dataclasses import dataclass, field
from datetime import datetime, timedelta
from types import SimpleNamespace
from typing import Any

from sqlalchemy import func, select

from maa_api.agent.llm.profiles import ResolvedProfile
from maa_api.agent.runs.service import RunService
from maa_api.agent.specs.registry import builtin_spec
from maa_api.agent.watchdog.checks import AlertDraft
from maa_api.agent.watchdog.recorder import WatchdogRecorder
from maa_api.agent.watchdog.service import (
    SEMANTIC_MAX_TICKETS_PER_ROUND,
    WATCHDOG_AGENT,
    WatchdogService,
)
from maa_api.db.models import AgentRun, Ticket, WatchdogAlert
from maa_api.domain.enums import (
    AgentRunKind,
    AgentRunStatus,
    AlertActionStatus,
    AlertSeverity,
    AlertTargetKind,
    ReportStatus,
    TicketStatus,
)
from maa_api.domain.errors import AppError, ErrorCode
from maa_api.settings import Settings
from tests.fakes.fake_llm import FakeLLM, text_response
from tests.fakes.v3_db import make_session_factory

NOW = datetime(2026, 11, 5, 12, 0, 0)
BOOT_ID = "boot-m83-semantic"

_PROFILE = ResolvedProfile(
    name="worker",
    kind="chat",
    provider="openai_compatible",
    base_url="https://llm.test/v1",
    api_key="sk-test",
    model="worker-model",
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
    """按角色返回固定档案、把请求转给 ``FakeLLM`` 的路由替身。"""

    def __init__(self, llm: FakeLLM) -> None:
        self.llm = llm
        self.roles: list[str] = []

    async def resolve(self, role: str) -> ResolvedProfile:
        self.roles.append(role)
        return _PROFILE

    def client_for(self, _profile: Any) -> _Handle:
        return _Handle(self.llm)


class _Ledger:
    """``complete_structured`` 只需要记账与预算查询的最小账本。"""

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


class _TicketService:
    def __init__(self) -> None:
        self.reports: list[tuple[str, Any]] = []

    async def record_report(
        self, ticket_id: str, report: Any, run_id: Any = None
    ) -> Any:
        self.reports.append((ticket_id, report))
        return SimpleNamespace(ticket_id=ticket_id, report=report, run_id=run_id)


@dataclass
class _Harness:
    factory: Any
    engine: Any
    settings: Settings
    llm: FakeLLM
    router: _Router
    tickets: _TicketService
    run_service: RunService
    service: WatchdogService
    alerts: list[WatchdogAlert] = field(default_factory=list)


async def _noop_sleep(_seconds: float) -> None:
    return None


async def _make_harness(
    tmp_path: Any,
    name: str,
    *,
    mode: str = "record",
    interval_seconds: int = 300,
) -> _Harness:
    engine, factory = await make_session_factory(tmp_path / f"{name}.db")
    settings = Settings()
    settings.watchdog.mode = mode  # type: ignore[assignment]
    settings.watchdog.interval_seconds = interval_seconds
    llm = FakeLLM([])
    router = _Router(llm)
    tickets = _TicketService()
    run_service = RunService(factory, boot_id=BOOT_ID, clock=lambda: NOW)
    service = WatchdogService(
        factory,
        recorder=WatchdogRecorder(clock=lambda: NOW),
        run_service=run_service,
        model_router=router,
        cost_ledger=_Ledger(),
        ticket_service=tickets,
        settings_provider=lambda: settings,
        clock=lambda: NOW,
        sleep=_noop_sleep,
        interrupt_grace_seconds=0.0,
    )
    return _Harness(
        factory=factory,
        engine=engine,
        settings=settings,
        llm=llm,
        router=router,
        tickets=tickets,
        run_service=run_service,
        service=service,
    )


async def _seed_ticket(
    factory: Any,
    *,
    title: str,
    updated_at: datetime,
    status: str = TicketStatus.OPEN.value,
) -> str:
    ticket = Ticket(
        title=title,
        kind="sweeper",
        status=status,
        origin="user",
        created_at=updated_at - timedelta(hours=2),
        updated_at=updated_at,
    )
    async with factory() as session:
        session.add(ticket)
        await session.commit()
    return str(ticket.id)


async def _close(harness: _Harness) -> None:
    await harness.service.close()
    await harness.run_service.close()
    harness.engine.sync_engine.dispose()


async def _alerts(harness: _Harness) -> list[WatchdogAlert]:
    async with harness.factory() as session:
        return list(
            (
                await session.execute(
                    select(WatchdogAlert).order_by(WatchdogAlert.id.asc())
                )
            )
            .scalars()
            .all()
        )


async def _runs(harness: _Harness) -> list[AgentRun]:
    async with harness.factory() as session:
        return list(
            (
                await session.execute(
                    select(AgentRun)
                    .where(AgentRun.kind == AgentRunKind.WATCHDOG_CHECK)
                    .order_by(AgentRun.started_at.asc())
                )
            )
            .scalars()
            .all()
        )


def _reply(payload: dict[str, Any]) -> list[dict[str, Any]]:
    return text_response(json.dumps(payload, ensure_ascii=False))


def _finding(
    ticket_id: str,
    *,
    check: str = "ticket_no_progress",
    severity: str = "warn",
    message: str = "这张工单最近多轮没有实质进展",
    suggested_action: str | None = None,
) -> dict[str, Any]:
    item: dict[str, Any] = {
        "check": check,
        "target_kind": "ticket",
        "target_id": ticket_id,
        "severity": severity,
        "message": message,
    }
    if suggested_action is not None:
        item["suggested_action"] = suggested_action
    return {"items": [item]}


def _card_response(messages: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """从本次卡片的工单标题行取 id，对这张工单作答（用于多张工单的轮次）。"""
    card = str(messages[-1].get("content") or "")
    match = re.search(r"【工单】([0-9a-f-]{36})", card)
    assert match is not None, card
    return _reply(_finding(match.group(1)))


# ---------------------------------------------------------------------------
# spec §6：只有被确定性检查挑中的工单才调模型
# ---------------------------------------------------------------------------
def test_semantic_only_on_flagged_tickets(tmp_path) -> None:
    async def scenario() -> None:
        harness = await _make_harness(tmp_path, "flagged")
        try:
            stale_id = await _seed_ticket(
                harness.factory,
                title="停滞工单",
                updated_at=NOW - timedelta(hours=2),
            )
            active_title = "活跃工单"
            await _seed_ticket(
                harness.factory,
                title=active_title,
                updated_at=NOW - timedelta(minutes=5),
            )
            harness.llm.responses.append(_reply(_finding(stale_id)))

            await harness.service.run_once(NOW)

            # 只对停滞工单调一次模型，而且用的是 watchdog 定义的正文。
            assert len(harness.llm.requests) == 1
            _model, messages, _tools = harness.llm.requests[0]
            assert messages[0]["content"] == builtin_spec("watchdog").body
            card = str(messages[-1]["content"])
            assert "停滞工单" in card and stale_id in card
            assert active_title not in card
            assert harness.router.roles == ["worker"]

            runs = await _runs(harness)
            assert len(runs) == 1
            assert runs[0].agent_name == WATCHDOG_AGENT
            assert runs[0].session_id is None
            assert runs[0].status == AgentRunStatus.SUCCEEDED.value
            assert runs[0].spec_version == builtin_spec("watchdog").spec_version

            checks = {alert.check for alert in await _alerts(harness)}
            assert checks == {"ticket_stale", "ticket_no_progress"}
            assert harness.tickets.reports == []
        finally:
            await _close(harness)

    asyncio.run(scenario())


# ---------------------------------------------------------------------------
# spec §6：mark_blocked 不新建诊断工单
# ---------------------------------------------------------------------------
def test_mark_blocked_does_not_create_diagnose(tmp_path) -> None:
    async def scenario() -> None:
        harness = await _make_harness(tmp_path, "blocked", mode="auto")
        try:
            ticket_id = await _seed_ticket(
                harness.factory,
                title="卡住的工单",
                updated_at=NOW - timedelta(hours=2),
            )
            harness.llm.responses.append(
                _reply(_finding(ticket_id, suggested_action="mark_blocked"))
            )

            await harness.service.run_once(NOW)

            # 确定性 ticket_stale 与语义检查各写一条 blocked；没有新建工单。
            assert len(harness.tickets.reports) == 2
            assert all(
                report.status == ReportStatus.BLOCKED
                for _ticket_id, report in harness.tickets.reports
            )
            async with harness.factory() as session:
                ticket_count = await session.scalar(
                    select(func.count()).select_from(Ticket)
                )
                diagnose_count = await session.scalar(
                    select(func.count())
                    .select_from(Ticket)
                    .where(Ticket.kind == "diagnose")
                )
            assert ticket_count == 1
            assert diagnose_count == 0

            semantic = next(
                alert
                for alert in await _alerts(harness)
                if alert.check == "ticket_no_progress"
            )
            assert semantic.action == "mark_blocked"
            assert semantic.action_status == AlertActionStatus.DONE.value
            assert semantic.evidence["deterministic_match"] is True
            assert semantic.evidence["suggested_action"] == "mark_blocked"
        finally:
            await _close(harness)

    asyncio.run(scenario())


# ---------------------------------------------------------------------------
# mark_blocked：record 档只记录建议
# ---------------------------------------------------------------------------
def test_semantic_mark_blocked_in_record_mode_only_records(tmp_path) -> None:
    async def scenario() -> None:
        harness = await _make_harness(tmp_path, "record")
        try:
            ticket_id = await _seed_ticket(
                harness.factory,
                title="卡住的工单",
                updated_at=NOW - timedelta(hours=2),
            )
            harness.llm.responses.append(
                _reply(_finding(ticket_id, suggested_action="mark_blocked"))
            )

            await harness.service.run_once(NOW)

            assert harness.tickets.reports == []
            semantic = next(
                alert
                for alert in await _alerts(harness)
                if alert.check == "ticket_no_progress"
            )
            assert semantic.action == "mark_blocked"
            assert semantic.action_status == AlertActionStatus.RECORDED.value
        finally:
            await _close(harness)

    asyncio.run(scenario())


# ---------------------------------------------------------------------------
# mark_blocked：没有本轮确定性命中时也只记录
# ---------------------------------------------------------------------------
def test_semantic_action_requires_deterministic_match(tmp_path) -> None:
    async def scenario() -> None:
        harness = await _make_harness(tmp_path, "no-match", mode="auto")
        try:
            ticket_id = await _seed_ticket(
                harness.factory,
                title="卡住的工单",
                updated_at=NOW - timedelta(hours=2),
            )
            draft = AlertDraft(
                check="ticket_no_progress",
                severity=AlertSeverity.WARN,
                target_kind=AlertTargetKind.TICKET,
                target_id=ticket_id,
                message="看起来卡住了",
                evidence={"ticket_id": ticket_id},
                suggested_action="mark_blocked",
            )

            result = await harness.service._apply_for_mode(
                draft, None, mode="auto"  # type: ignore[arg-type]
            )

            assert result.status == AlertActionStatus.RECORDED
            assert result.detail["reason"] == "ticket_not_flagged_deterministically"
            assert harness.tickets.reports == []
        finally:
            await _close(harness)

    asyncio.run(scenario())


# ---------------------------------------------------------------------------
# 每轮至多 2 张、每张每小时至多 1 次
# ---------------------------------------------------------------------------
def test_at_most_two_tickets_per_round(tmp_path) -> None:
    async def scenario() -> None:
        harness = await _make_harness(tmp_path, "two-per-round")
        try:
            for index in range(3):
                await _seed_ticket(
                    harness.factory,
                    title=f"停滞工单 {index}",
                    updated_at=NOW - timedelta(hours=2, minutes=index),
                )
            harness.llm.responses.extend([_card_response, _card_response])

            await harness.service.run_once(NOW)

            assert SEMANTIC_MAX_TICKETS_PER_ROUND == 2
            assert len(harness.llm.requests) == 2
            assert len(await _runs(harness)) == 2
            semantic_ids = {
                alert.target_id
                for alert in await _alerts(harness)
                if alert.check == "ticket_no_progress"
            }
            assert len(semantic_ids) == 2
        finally:
            await _close(harness)

    asyncio.run(scenario())


def test_same_ticket_at_most_once_per_hour(tmp_path) -> None:
    async def scenario() -> None:
        harness = await _make_harness(
            tmp_path, "hourly", interval_seconds=60
        )
        try:
            ticket_id = await _seed_ticket(
                harness.factory,
                title="停滞工单",
                updated_at=NOW - timedelta(hours=2),
            )
            harness.llm.responses.append(_reply(_finding(ticket_id)))

            await harness.service.run_once(NOW)
            await harness.service.run_once(NOW + timedelta(minutes=2))

            assert len(harness.llm.requests) == 1
            assert len(await _runs(harness)) == 1
        finally:
            await _close(harness)

    asyncio.run(scenario())


# ---------------------------------------------------------------------------
# 模型失败：跳过该工单，不写告警，不建诊断工单
# ---------------------------------------------------------------------------
def test_model_failure_skips_ticket_without_alert(tmp_path) -> None:
    async def scenario() -> None:
        harness = await _make_harness(tmp_path, "failure")
        try:
            await _seed_ticket(
                harness.factory,
                title="停滞工单",
                updated_at=NOW - timedelta(hours=2),
            )
            harness.llm.responses.append(
                AppError(ErrorCode.LLM_REQUEST_FAILED, "上游不可用")
            )

            await harness.service.run_once(NOW)

            checks = {alert.check for alert in await _alerts(harness)}
            assert checks == {"ticket_stale"}
            runs = await _runs(harness)
            assert len(runs) == 1
            assert runs[0].status == AgentRunStatus.FAILED.value
            assert runs[0].error_code == ErrorCode.LLM_REQUEST_FAILED.value
            assert harness.tickets.reports == []
            async with harness.factory() as session:
                ticket_count = await session.scalar(
                    select(func.count()).select_from(Ticket)
                )
            assert ticket_count == 1
        finally:
            await _close(harness)

    asyncio.run(scenario())
