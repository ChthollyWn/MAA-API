"""M84 T7：``BriefService`` 的到点、巡检、收尾与推送（spec §4.5.9、§4.5.12、§6）。

临时库与 ``tests/fakes/v3_db.py`` 同口径；工单、收件箱与产物走真实服务，只有
Worker 建会话是替身。固定时钟 ``NOW = 2026-11-05 12:00Z``（北京 20:00），
所以简报标题与 ``brief_date`` 都不随真实时间漂移。
"""

from __future__ import annotations

import asyncio
import logging
from datetime import datetime, timedelta
from types import SimpleNamespace
from typing import Any

import pytest
from apscheduler.schedulers.asyncio import AsyncIOScheduler
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from maa_api.agent.artifacts.service import ArtifactService
from maa_api.agent.brief.facts import collect_facts
from maa_api.agent.brief.render import (
    render_brief_markdown,
    render_facts_markdown,
)
from maa_api.agent.brief.service import (
    BRIEF_JOB_ID,
    BriefRunResult,
    BriefService,
    _compose_brief_body,
)
from maa_api.agent.tickets.inbox import InboxService
from maa_api.agent.tickets.service import TicketService
from maa_api.agent.tickets.threads import ThreadRouter
from maa_api.db.models import (
    AgentInbox,
    Artifact,
    Mandate,
    Ticket,
    TicketReport,
)
from maa_api.domain.errors import AppError, ErrorCode
from maa_api.settings import BriefSettings, Settings
from tests.fakes.v3_db import make_session_factory, seed_session

#: 北京 2026-11-05 20:00（naive UTC）。
NOW = datetime(2026, 11, 5, 12, 0)


@pytest.fixture
def factory(tmp_path) -> async_sessionmaker[AsyncSession]:
    engine, session_factory = asyncio.run(make_session_factory(tmp_path / "brief.db"))
    yield session_factory
    engine.sync_engine.dispose()


class _Clock:
    """可写的固定时钟。"""

    def __init__(self, value: datetime = NOW) -> None:
        self.value = value

    def __call__(self) -> datetime:
        return self.value


class _Visible:
    """``has_visible_client`` 的替身。"""

    def __init__(self, value: bool = False) -> None:
        self.value = value

    def __call__(self) -> bool:
        return self.value


class _WebPush:
    """发件箱替身：M85 起简报先写 outbox，``enqueue`` 记录 payload。"""

    def __init__(self) -> None:
        self.payloads: list[dict[str, Any]] = []
        self.calls: list[dict[str, Any]] = []
        self.error: Exception | None = None

    async def enqueue(self, payload: dict[str, Any], **kwargs: Any) -> None:
        if self.error is not None:
            raise self.error
        self.payloads.append(payload)
        self.calls.append({"payload": payload, **kwargs})

    async def broadcast(self, payload: dict[str, Any]) -> None:
        if self.error is not None:
            raise self.error
        self.payloads.append(payload)


class _SpecRegistry:
    """最小代理定义注册表，只有 analyst。"""

    def get(self, name: str) -> SimpleNamespace:
        if name != "analyst":
            raise AppError(
                ErrorCode.AGENT_SPEC_NOT_FOUND, f"没有名为 {name} 的代理定义"
            )
        return SimpleNamespace(kind="worker", risk_ceiling="none")


class _WorkerService:
    """替身 Worker 服务：建一个 ``kind=worker`` 的会话行，不跑模型。"""

    def __init__(self, factory: async_sessionmaker[AsyncSession]) -> None:
        self._factory = factory

    async def ensure_worker_session(self, ticket: Any) -> SimpleNamespace:
        session_id = await seed_session(
            self._factory, kind="worker", ticket_id=ticket.id
        )
        return SimpleNamespace(id=session_id)


class _Harness:
    """临时库上的真实工单/产物服务与简报服务。"""

    def __init__(
        self,
        factory: async_sessionmaker[AsyncSession],
        tmp_path: Any,
        *,
        settings: Settings | None = None,
        visible: bool = False,
    ) -> None:
        self.factory = factory
        self.settings = settings or Settings(brief=BriefSettings())
        self.visible = _Visible(visible)
        self.clock = _Clock()
        self.webpush = _WebPush()
        self.inbox = InboxService(factory)
        self.router = ThreadRouter(factory, self.inbox)
        self.ticket_service = TicketService(
            factory,
            inbox_service=self.inbox,
            thread_router=self.router,
            spec_registry=_SpecRegistry(),
        )
        self.worker_service = _WorkerService(factory)
        self.ticket_service.bind_worker_service(lambda: self.worker_service)
        self.artifacts = ArtifactService(factory, resource_root=tmp_path / "resource")
        self.app = SimpleNamespace(
            state=SimpleNamespace(
                session_factory=factory,
                artifact_service=self.artifacts,
                ticket_service=self.ticket_service,
                worker_service=self.worker_service,
                webpush_outbox=self.webpush,
            )
        )
        self.service = BriefService(
            self.app,
            session_factory=factory,
            has_visible_client=self.visible,
            settings_provider=lambda: self.settings,
            clock=self.clock,
        )


async def _all(factory: async_sessionmaker[AsyncSession], model: Any) -> list[Any]:
    async with factory() as db:
        result = await db.execute(select(model))
        return list(result.scalars().all())


async def _get(factory: async_sessionmaker[AsyncSession], model: Any, key: str) -> Any:
    async with factory() as db:
        return await db.get(model, key)


async def _artifact(factory: async_sessionmaker[AsyncSession], artifact_id: Any) -> Artifact:
    artifact = await _get(factory, Artifact, str(artifact_id))
    assert artifact is not None
    return artifact


async def _mandate(factory: async_sessionmaker[AsyncSession], ticket_id: Any) -> Mandate:
    async with factory() as db:
        result = await db.execute(
            select(Mandate).where(Mandate.ticket_id == str(ticket_id))
        )
        mandate = result.scalars().first()
    assert mandate is not None
    return mandate


async def _set_created_at(
    factory: async_sessionmaker[AsyncSession], artifact_id: Any, moment: datetime
) -> None:
    async with factory() as db:
        artifact = await db.get(Artifact, str(artifact_id))
        assert artifact is not None
        artifact.created_at = moment
        await db.commit()


async def _add_report(
    factory: async_sessionmaker[AsyncSession],
    *,
    ticket_id: Any,
    artifact_id: Any,
    status: str = "ready",
    summary: str = "今天状态不错。",
    suggestions: list[Any] | None = None,
) -> None:
    details: dict[str, Any] = {"brief_artifact_id": str(artifact_id)}
    if suggestions is not None:
        details["suggestions"] = suggestions
    async with factory() as db:
        db.add(
            TicketReport(
                ticket_id=str(ticket_id),
                status=status,
                summary=summary,
                details=details,
            )
        )
        await db.commit()


# ---------------------------------------------------------------------------
# 到点建单
# ---------------------------------------------------------------------------


def test_on_cron_creates_one_system_ticket_per_day(factory, tmp_path) -> None:
    async def scenario() -> None:
        harness = _Harness(factory, tmp_path)
        await harness.service.on_cron()

        tickets = await _all(factory, Ticket)
        assert len(tickets) == 1
        ticket = tickets[0]
        assert ticket.kind == "analyst"
        assert ticket.origin == "system"
        assert ticket.title == "每日简报 11-05"

        mandate = await _mandate(factory, ticket.id)
        assert mandate.strength == "inferred"
        assert mandate.risk_ceiling == "none"
        assert mandate.source == "schedule"
        assert mandate.sender == "brief"

        artifacts = await _all(factory, Artifact)
        assert len(artifacts) == 1
        artifact = artifacts[0]
        assert artifact.kind == "report"
        assert artifact.title == "每日简报 · 11 月 5 日"
        assert artifact.mime == "text/markdown"
        meta = artifact.meta
        assert meta["brief"] is True
        assert meta["status"] == "pending"
        assert meta["origin"] == "schedule"
        assert meta["brief_date"] == "2026-11-05"
        assert meta["ticket_id"] == ticket.id
        assert "## 今日建议" in (artifact.content or "")
        assert "生成中" in (artifact.content or "")
        assert meta["facts_markdown"] in (artifact.content or "")

        # 工单说明带事实与产物 id（analyst 靠它写回 brief_artifact_id）。
        inbox = await _all(factory, AgentInbox)
        brief_text = "\n".join(
            str((item.payload or {}).get("text", "")) for item in inbox
        )
        assert str(artifact.id) in brief_text
        assert "每日简报" in brief_text

        # 同一天第二次到点：已有 origin=schedule 的简报，不再建。
        await harness.service.on_cron()
        assert len(await _all(factory, Ticket)) == 1
        assert len(await _all(factory, Artifact)) == 1

    asyncio.run(scenario())


def test_on_cron_respects_enabled(factory, tmp_path) -> None:
    async def scenario() -> None:
        harness = _Harness(
            factory,
            tmp_path,
            settings=Settings(brief=BriefSettings(enabled=False)),
        )
        await harness.service.on_cron()
        assert await _all(factory, Ticket) == []
        assert await _all(factory, Artifact) == []

    asyncio.run(scenario())


# ---------------------------------------------------------------------------
# 巡检收尾
# ---------------------------------------------------------------------------


def test_watch_finalizes_on_ready_report(factory, tmp_path) -> None:
    async def scenario() -> None:
        harness = _Harness(factory, tmp_path)
        await harness.service.on_cron()
        artifact = (await _all(factory, Artifact))[0]
        ticket = (await _all(factory, Ticket))[0]
        await _add_report(
            factory,
            ticket_id=ticket.id,
            artifact_id=artifact.id,
            summary="今天状态不错。",
            suggestions=[
                "先刷 1-7",
                " 补材料 ",
                42,
                "第三条",
                "第四条",
                "第五条",
                "第六条不应出现",
            ],
        )

        assert await harness.service.watch_once() == 1
        updated = await _artifact(factory, artifact.id)
        assert updated.meta["status"] == "ready"
        assert updated.meta["ticket_id"] == ticket.id
        assert updated.meta["brief"] is True
        assert updated.meta["brief_date"] == "2026-11-05"
        assert "## 理智" in updated.meta["facts_markdown"]
        body = updated.content or ""
        assert "今天状态不错。" in body
        assert "## 今日建议" in body
        assert "1. 先刷 1-7" in body
        assert "2. 补材料" in body
        assert "6. 第六条不应出现" not in body
        assert "生成中" not in body

        assert len(harness.webpush.payloads) == 1
        assert harness.webpush.payloads[0] == {
            "type": "notification",
            "title": "每日简报",
            "body": "今天状态不错。",
            "data": {
                "kind": "brief",
                "artifact_id": str(artifact.id),
                "ticket_id": str(ticket.id),
                "url": f"/tickets/{ticket.id}",
            },
        }
        assert harness.webpush.calls[0]["kind"].value == "brief"
        assert harness.webpush.calls[0]["dedupe_key"] == f"brief:{artifact.id}"

    asyncio.run(scenario())


def test_composed_body_matches_render_brief_markdown(factory, tmp_path) -> None:
    """收尾只能靠 meta.facts_markdown 重新拼全文，结果必须与 G4 的渲染一致。"""

    async def scenario() -> None:
        harness = _Harness(factory, tmp_path)
        facts = await collect_facts(harness.app, NOW)
        facts_markdown = render_facts_markdown(facts)
        for status, summary, suggestions in (
            ("pending", None, []),
            ("facts_only", "没写完", []),
            ("ready", "今天状态不错。", ["先刷 1-7", "再补材料"]),
        ):
            assert _compose_brief_body(
                facts_markdown, summary, suggestions, status
            ) == render_brief_markdown(facts, summary, suggestions, status)

    asyncio.run(scenario())


def test_watch_ready_without_suggestions_is_facts_only(factory, tmp_path) -> None:
    async def scenario() -> None:
        harness = _Harness(factory, tmp_path)
        await harness.service.on_cron()
        artifact = (await _all(factory, Artifact))[0]
        ticket = (await _all(factory, Ticket))[0]
        await _add_report(
            factory,
            ticket_id=ticket.id,
            artifact_id=artifact.id,
            summary="没写出建议。",
            suggestions=[],
        )

        assert await harness.service.watch_once() == 1
        updated = await _artifact(factory, artifact.id)
        assert updated.meta["status"] == "facts_only"
        assert "这次没有生成建议" in (updated.content or "")
        assert harness.webpush.payloads[0]["body"] == "今天的简报只生成了数据部分"

    asyncio.run(scenario())


def test_push_body_truncates_summary_to_120(factory, tmp_path) -> None:
    async def scenario() -> None:
        harness = _Harness(factory, tmp_path)
        await harness.service.on_cron()
        artifact = (await _all(factory, Artifact))[0]
        ticket = (await _all(factory, Ticket))[0]
        summary = "摘要" * 100
        await _add_report(
            factory,
            ticket_id=ticket.id,
            artifact_id=artifact.id,
            summary=summary,
            suggestions=["先刷 1-7"],
        )
        assert await harness.service.watch_once() == 1
        assert harness.webpush.payloads[0]["body"] == summary[:120]
        assert len(harness.webpush.payloads[0]["body"]) == 120

    asyncio.run(scenario())


def test_watch_finalizes_failed_report_as_facts_only(factory, tmp_path) -> None:
    async def scenario() -> None:
        harness = _Harness(factory, tmp_path)
        await harness.service.on_cron()
        artifact = (await _all(factory, Artifact))[0]
        ticket = (await _all(factory, Ticket))[0]
        await _add_report(
            factory,
            ticket_id=ticket.id,
            artifact_id=artifact.id,
            status="blocked",
            summary="数据不够，做不下去。",
        )

        assert await harness.service.watch_once() == 1
        updated = await _artifact(factory, artifact.id)
        assert updated.meta["status"] == "facts_only"
        assert "这次没有生成建议" in (updated.content or "")
        assert "数据不够，做不下去。" in (updated.content or "")
        assert harness.webpush.payloads[0]["body"] == "今天的简报只生成了数据部分"
        assert harness.webpush.payloads[0]["data"]["url"] == f"/tickets/{ticket.id}"

    asyncio.run(scenario())


def test_watch_times_out_to_facts_only(factory, tmp_path) -> None:
    async def scenario() -> None:
        harness = _Harness(factory, tmp_path)
        await harness.service.on_cron()
        artifact = (await _all(factory, Artifact))[0]
        ticket = (await _all(factory, Ticket))[0]
        await _set_created_at(
            factory, artifact.id, NOW - timedelta(minutes=31)
        )

        assert await harness.service.watch_once() == 1
        updated = await _artifact(factory, artifact.id)
        assert updated.meta["status"] == "facts_only"
        assert "这次没有生成建议" in (updated.content or "")
        assert harness.webpush.payloads[0]["body"] == "今天的简报只生成了数据部分"
        # 收尾后 meta.status 不再是 pending，下一轮不会再推一次。
        assert await harness.service.watch_once() == 0
        assert len(harness.webpush.payloads) == 1

    asyncio.run(scenario())


def test_watch_ignores_non_analyst_report(factory, tmp_path) -> None:
    async def scenario() -> None:
        harness = _Harness(factory, tmp_path)
        await harness.service.on_cron()
        artifact = (await _all(factory, Artifact))[0]
        analyst_ticket = (await _all(factory, Ticket))[0]
        other = Ticket(
            title="别的工单", kind="general", origin="user", status="open"
        )
        async with factory() as db:
            db.add(other)
            await db.commit()
        await _add_report(
            factory,
            ticket_id=other.id,
            artifact_id=artifact.id,
            suggestions=["不应采纳"],
        )

        # 汇报不是 analyst 的：未超时就不动。
        assert await harness.service.watch_once() == 0
        assert (await _artifact(factory, artifact.id)).meta["status"] == "pending"

        # 超时后照常 facts_only，ticket_id 保留建单时写回的值。
        await _set_created_at(
            factory, artifact.id, NOW - timedelta(minutes=31)
        )
        assert await harness.service.watch_once() == 1
        updated = await _artifact(factory, artifact.id)
        assert updated.meta["status"] == "facts_only"
        assert updated.meta["ticket_id"] == analyst_ticket.id
        assert "不应采纳" not in (updated.content or "")

    asyncio.run(scenario())


# ---------------------------------------------------------------------------
# 手动、重启与推送条件
# ---------------------------------------------------------------------------


def test_run_now_is_not_limited(factory, tmp_path) -> None:
    async def scenario() -> None:
        harness = _Harness(
            factory,
            tmp_path,
            settings=Settings(brief=BriefSettings(enabled=False)),
        )
        first = await harness.service.run_now()
        second = await harness.service.run_now()
        assert isinstance(first, BriefRunResult)
        assert first.ticket_id != second.ticket_id
        assert first.artifact_id != second.artifact_id

        tickets = await _all(factory, Ticket)
        assert len(tickets) == 2
        assert {ticket.origin for ticket in tickets} == {"user"}
        assert {ticket.kind for ticket in tickets} == {"analyst"}
        for ticket in tickets:
            mandate = await _mandate(factory, ticket.id)
            assert mandate.strength == "explicit"
            assert mandate.risk_ceiling == "none"
            assert mandate.source == "web"
            assert mandate.sender == "web"

        artifacts = await _all(factory, Artifact)
        assert len(artifacts) == 2
        assert {artifact.meta["origin"] for artifact in artifacts} == {"manual"}
        assert {artifact.meta["status"] for artifact in artifacts} == {"pending"}
        assert {str(item.id) for item in tickets} == {
            first.ticket_id,
            second.ticket_id,
        }

    asyncio.run(scenario())


def test_start_marks_stale_pending_without_push(factory, tmp_path) -> None:
    async def scenario() -> None:
        harness = _Harness(factory, tmp_path)
        await harness.service.on_cron()
        artifact = (await _all(factory, Artifact))[0]
        await _set_created_at(
            factory, artifact.id, NOW - timedelta(minutes=31)
        )
        try:
            await harness.service.start()
            updated = await _artifact(factory, artifact.id)
            assert updated.meta["status"] == "facts_only"
            assert harness.webpush.payloads == []
        finally:
            await harness.service.stop()

    asyncio.run(scenario())


def test_push_skipped_when_page_visible(factory, tmp_path) -> None:
    async def scenario() -> None:
        harness = _Harness(factory, tmp_path, visible=True)
        await harness.service.on_cron()
        artifact = (await _all(factory, Artifact))[0]
        ticket = (await _all(factory, Ticket))[0]
        await _add_report(
            factory,
            ticket_id=ticket.id,
            artifact_id=artifact.id,
            suggestions=["先刷 1-7"],
        )
        assert await harness.service.watch_once() == 1
        assert (await _artifact(factory, artifact.id)).meta["status"] == "ready"
        assert harness.webpush.payloads == []

    asyncio.run(scenario())


def test_push_skipped_when_web_push_disabled(factory, tmp_path) -> None:
    async def scenario() -> None:
        harness = _Harness(
            factory,
            tmp_path,
            settings=Settings(brief=BriefSettings(push_web=False)),
        )
        await harness.service.on_cron()
        artifact = (await _all(factory, Artifact))[0]
        ticket = (await _all(factory, Ticket))[0]
        await _add_report(
            factory,
            ticket_id=ticket.id,
            artifact_id=artifact.id,
            suggestions=["先刷 1-7"],
        )
        assert await harness.service.watch_once() == 1
        assert (await _artifact(factory, artifact.id)).meta["status"] == "ready"
        assert harness.webpush.payloads == []

    asyncio.run(scenario())


def test_broadcast_failure_does_not_raise(factory, tmp_path) -> None:
    async def scenario() -> None:
        harness = _Harness(factory, tmp_path)
        harness.webpush.error = RuntimeError("push broken")
        await harness.service.on_cron()
        artifact = (await _all(factory, Artifact))[0]
        ticket = (await _all(factory, Ticket))[0]
        await _add_report(
            factory,
            ticket_id=ticket.id,
            artifact_id=artifact.id,
            suggestions=["先刷 1-7"],
        )
        assert await harness.service.watch_once() == 1
        assert (await _artifact(factory, artifact.id)).meta["status"] == "ready"

    asyncio.run(scenario())


def test_push_payload_truncates_summary_and_uses_root_without_ticket(
    factory, tmp_path
) -> None:
    async def scenario() -> None:
        harness = _Harness(factory, tmp_path)
        artifact = await harness.artifacts.put(
            kind="report",
            title="每日简报 · 11 月 5 日",
            body=b"# body",
            meta={
                "brief": True,
                "brief_date": "2026-11-05",
                "status": "pending",
                "origin": "schedule",
                "ticket_id": None,
                "facts_markdown": "# 每日简报 · 11 月 5 日（周四）\n\n## 理智\n没有理智记录",
            },
        )
        await _set_created_at(
            factory, artifact.id, NOW - timedelta(minutes=31)
        )
        assert await harness.service.watch_once() == 1
        payload = harness.webpush.payloads[0]
        assert payload["body"] == "今天的简报只生成了数据部分"
        assert payload["data"]["url"] == "/"
        assert payload["data"]["ticket_id"] is None

    asyncio.run(scenario())


# ---------------------------------------------------------------------------
# 调度器注册
# ---------------------------------------------------------------------------


def test_attach_scheduler_registers_daily_job(factory, tmp_path) -> None:
    harness = _Harness(factory, tmp_path)
    scheduler = AsyncIOScheduler()
    assert harness.service.attach_scheduler(scheduler) is None
    job = scheduler.get_job(BRIEF_JOB_ID)
    assert job is not None
    assert job.max_instances == 1
    assert job.coalesce is True


def test_attach_scheduler_logs_invalid_cron(
    factory, tmp_path, caplog: pytest.LogCaptureFixture
) -> None:
    harness = _Harness(
        factory,
        tmp_path,
        settings=Settings(brief=BriefSettings(cron="61 8 * * *")),
    )
    scheduler = AsyncIOScheduler()
    with caplog.at_level(logging.ERROR, logger="maa_api.agent.brief.service"):
        harness.service.attach_scheduler(scheduler)
    assert scheduler.get_job(BRIEF_JOB_ID) is None
    assert "brief.cron" in caplog.text
