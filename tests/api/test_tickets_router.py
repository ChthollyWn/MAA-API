"""工单只读 REST 契约（M69 规格 §4.3、§6）。

用例按生产装配的最小集搭 app：只装 tickets 路由、统一错误处理器和
``app.state.ticket_service``；工单用 T6 的 :meth:`TicketService.create` 写进
``tests/fakes/v3_db.py`` 造的临时库（真实 SQLite，不 mock 仓储）。时间由自增的
假时钟给出，列表的 ``updated_at DESC`` 与汇报的 ``id DESC`` 才有确定顺序。

三条端点都只读；服务缺失、分页越界与工单不存在分别对应 503 / 400 / 404。
"""

from __future__ import annotations

import asyncio
import re
from datetime import datetime, timedelta
from types import SimpleNamespace

import pytest
from fastapi import FastAPI

from maa_api.agent.lease import DeviceLeaseService
from maa_api.agent.tickets.inbox import InboxService
from maa_api.agent.tickets.reports import parse_report
from maa_api.agent.tickets.service import TicketService
from maa_api.agent.tickets.threads import ThreadRouter
from maa_api.api import deps
from maa_api.api.errors import register_exception_handlers
from maa_api.api.routers import tickets as tickets_router
from maa_api.db.models import DeviceLease, utcnow
from maa_api.domain.enums import EventSource, ReportStatus, TicketOrigin
from maa_api.domain.errors import AppError, ErrorCode
from maa_api.main import create_app
from tests.fakes.v3_db import make_session_factory

#: 与 ``TicketSummary`` 一致的 12 个字段（规格 §4.3）。
SUMMARY_FIELDS = {
    "id",
    "title",
    "kind",
    "status",
    "origin",
    "priority",
    "summary",
    "last_report_status",
    "human_action",
    "created_at",
    "updated_at",
    "ended_at",
}
#: ``TicketDetail`` 的 9 个字段（M71 §4.3 加 ``lease``，M80 加 ``forked_from``）。
DETAIL_FIELDS = {
    "ticket",
    "result",
    "mandate",
    "subscriptions",
    "recent_reports",
    "worker_session_id",
    "turn_running",
    "lease",
    "forked_from",
}
#: ``MandateOut`` 的 9 个字段。
MANDATE_FIELDS = {
    "id",
    "scope",
    "strength",
    "source",
    "sender",
    "rule_id",
    "risk_ceiling",
    "policy_version",
    "created_at",
}
#: ``TicketReportOut`` 的 9 个字段。
REPORT_FIELDS = {
    "id",
    "ticket_id",
    "run_id",
    "status",
    "summary",
    "human_action",
    "details",
    "forwarded",
    "created_at",
}

_SPECS: dict[str, tuple[str, str]] = {
    "daily_ops": ("worker", "consume"),
    "general": ("worker", "destructive"),
    "researcher": ("subagent", "none"),
}


def _registry() -> SimpleNamespace:
    """三个代理定义的最小注册表；未知名字与生产一样抛 ``AGENT_SPEC_NOT_FOUND``。"""

    def get(name: str) -> SimpleNamespace:
        try:
            kind, risk_ceiling = _SPECS[name]
        except KeyError:
            raise AppError(
                ErrorCode.AGENT_SPEC_NOT_FOUND, f"没有名为 {name} 的代理定义"
            ) from None
        return SimpleNamespace(kind=kind, risk_ceiling=risk_ceiling)

    return SimpleNamespace(get=get)


class _Clock:
    """每次调用前进一秒的假时钟，让 ``updated_at`` 严格递增。"""

    def __init__(self, start: datetime = datetime(2026, 11, 5, 8, 0, 0)) -> None:
        self._now = start

    def __call__(self) -> datetime:
        self._now += timedelta(seconds=1)
        return self._now


@pytest.fixture
def tickets_env(tmp_path):
    """临时库上的收件箱、话题路由与工单服务；用例结束销毁引擎。"""
    engine, factory = asyncio.run(make_session_factory(tmp_path / "tickets.db"))
    inbox = InboxService(factory)
    service = TicketService(
        factory,
        inbox_service=inbox,
        thread_router=ThreadRouter(factory, inbox),
        spec_registry=_registry(),
        clock=_Clock(),
    )
    yield SimpleNamespace(engine=engine, factory=factory, service=service)
    engine.sync_engine.dispose()


def _client(tickets_env, make_client, *, with_service: bool = True):
    """只装工单路由的 app；``with_service=False`` 模拟服务尚未装配。"""
    app = FastAPI()
    register_exception_handlers(app)
    app.include_router(tickets_router.router)
    if with_service:
        app.state.ticket_service = tickets_env.service
    return make_client(app)


def _create(tickets_env, **overrides):
    """用 T6 的 ``create`` 造一张工单（连带委托），返回 ``(ticket, mandate)``。"""
    values = {
        "kind": "daily_ops",
        "title": "日常",
        "scope": "把日常做了",
        "origin": TicketOrigin.USER,
        "source": EventSource.WEB,
        "sender": "web",
    }
    values.update(overrides)
    return asyncio.run(tickets_env.service.create(**values))


def _report(tickets_env, ticket_id: str, status: ReportStatus, **overrides):
    """记一条汇报，返回落库后的汇报（``id`` 已分配）。"""
    data = {"status": status, "summary": "进展"}
    data.update(overrides)
    outcome = asyncio.run(
        tickets_env.service.record_report(ticket_id, parse_report(data))
    )
    return outcome.report


def _error(response):
    return response.json()["error"]


# ---------------------------------------------------------------------------
# 列表：过滤、并集与分页
# ---------------------------------------------------------------------------
def test_list_tickets_filters_and_paginates(
    tickets_env, make_client, tmp_settings
):
    ticket_a, _ = _create(tickets_env, title="日常 A")
    ticket_b, _ = _create(tickets_env, kind="general", title="日常 B")
    _report(
        tickets_env,
        ticket_b.id,
        ReportStatus.WAITING,
        human_action={"type": "confirmation", "prompt": "确认用理智药", "ref_id": "c-1"},
    )
    ticket_c, _ = _create(tickets_env, title="日常 C")
    asyncio.run(tickets_env.service.cancel(ticket_c.id))
    ticket_d, _ = _create(
        tickets_env,
        title="日常 D",
        origin=TicketOrigin.TRIGGER,
        source=EventSource.PIPELINE,
        sender=None,
        rule_id="rule-1",
    )
    client = _client(tickets_env, make_client)

    first = client.get("/api/agent/tickets?size=2")
    assert first.status_code == 200, first.text
    payload = first.json()
    assert (payload["total"], payload["page"], payload["size"]) == (4, 1, 2)
    assert [item["id"] for item in payload["items"]] == [ticket_d.id, ticket_c.id]
    for item in payload["items"]:
        assert set(item) == SUMMARY_FIELDS
        assert item["status"] in {"open", "waiting", "cancelled"}
        assert item["created_at"].endswith("Z")
        assert item["updated_at"].endswith("Z")

    second = client.get("/api/agent/tickets?page=2&size=2")
    assert second.status_code == 200, second.text
    assert [item["id"] for item in second.json()["items"]] == [ticket_b.id, ticket_a.id]
    assert second.json()["total"] == 4

    def ids(query: str) -> list[str]:
        response = client.get(f"/api/agent/tickets{query}")
        assert response.status_code == 200, response.text
        return [item["id"] for item in response.json()["items"]]

    # 重复的 status 取并集；三个过滤条件各自生效。
    assert ids("?status=open") == [ticket_d.id, ticket_a.id]
    assert set(ids("?status=open&status=waiting")) == {
        ticket_a.id,
        ticket_b.id,
        ticket_d.id,
    }
    assert ids("?status=cancelled") == [ticket_c.id]
    assert ids("?kind=general") == [ticket_b.id]
    assert ids("?origin=trigger") == [ticket_d.id]

    bad_page = client.get("/api/agent/tickets?size=0")
    assert bad_page.status_code == 400, bad_page.text
    assert _error(bad_page)["code"] == "INVALID_PAGINATION"

    unknown = client.get("/api/agent/tickets?status=bogus")
    assert unknown.status_code == 422, unknown.text


# ---------------------------------------------------------------------------
# 详情
# ---------------------------------------------------------------------------
def test_get_ticket_detail_shape(tickets_env, make_client, tmp_settings):
    ticket, _ = _create(tickets_env)
    report_ids = [
        _report(tickets_env, ticket.id, ReportStatus.PROGRESS, summary=f"进展 {i}").id
        for i in range(1, 7)
    ]
    client = _client(tickets_env, make_client)

    response = client.get(f"/api/agent/tickets/{ticket.id}")
    assert response.status_code == 200, response.text
    payload = response.json()
    assert set(payload) == DETAIL_FIELDS
    assert set(payload["ticket"]) == SUMMARY_FIELDS
    assert payload["ticket"]["id"] == ticket.id
    assert payload["ticket"]["status"] == "open"
    assert payload["ticket"]["origin"] == "user"
    assert payload["ticket"]["priority"] == 1
    assert payload["ticket"]["summary"] == "进展 6"
    assert payload["ticket"]["last_report_status"] == "progress"
    assert payload["ticket"]["human_action"] is None
    assert payload["ticket"]["ended_at"] is None
    assert payload["ticket"]["created_at"].endswith("Z")
    assert payload["ticket"]["updated_at"].endswith("Z")

    assert set(payload["mandate"]) == MANDATE_FIELDS
    assert payload["mandate"]["scope"] == "把日常做了"
    assert payload["mandate"]["strength"] == "explicit"
    assert payload["mandate"]["source"] == "web"
    assert payload["mandate"]["risk_ceiling"] == "consume"
    assert re.fullmatch(r"[0-9a-f]{12}", payload["mandate"]["policy_version"])
    assert payload["mandate"]["created_at"].endswith("Z")

    # 只回最近 5 条，id 倒序；时间一律带 Z。
    reports = payload["recent_reports"]
    assert len(reports) == 5
    assert [item["id"] for item in reports] == list(reversed(report_ids[-5:]))
    for item in reports:
        assert set(item) == REPORT_FIELDS
        assert item["status"] == "progress"
        assert item["created_at"].endswith("Z")

    assert payload["subscriptions"] == []
    assert payload["result"] is None
    assert payload["worker_session_id"] is None
    assert payload["turn_running"] is False


def test_get_ticket_not_found(tickets_env, make_client, tmp_settings):
    client = _client(tickets_env, make_client)

    response = client.get("/api/agent/tickets/nope")
    assert response.status_code == 404, response.text
    error = _error(response)
    assert error["code"] == "TICKET_NOT_FOUND"
    assert error["details"]["ticket_id"] == "nope"


def test_ticket_detail_includes_current_lease(
    tickets_env, make_client, tmp_settings
):
    """M71 §4.3：当前租约属于本工单时填 ``lease``，别的工单持有时为 null。"""
    ticket, _ = _create(tickets_env)
    other, _ = _create(tickets_env, title="另一个工单")
    service = DeviceLeaseService(tickets_env.factory, boot_id="boot-test")

    def seed(ticket_id: str) -> None:
        """直接写租约行：``current()`` 的清理广播与这里要断言的形状无关。"""

        async def write() -> None:
            async with tickets_env.factory() as db:
                existing = await db.get(DeviceLease, "default")
                if existing is not None:
                    await db.delete(existing)
                    await db.commit()
                db.add(
                    DeviceLease(
                        device_id="default",
                        ticket_id=ticket_id,
                        holder_agent="daily_ops",
                        purpose="click",
                        expires_at=utcnow() + timedelta(minutes=5),
                        boot_id="boot-test",
                    )
                )
                await db.commit()

        asyncio.run(write())

    app = FastAPI()
    register_exception_handlers(app)
    app.include_router(tickets_router.router)
    app.state.ticket_service = tickets_env.service
    app.state.device_lease_service = service
    client = make_client(app)

    seed(other.id)
    response = client.get(f"/api/agent/tickets/{ticket.id}")
    assert response.status_code == 200, response.text
    assert response.json()["lease"] is None

    seed(ticket.id)
    response = client.get(f"/api/agent/tickets/{ticket.id}")
    assert response.status_code == 200, response.text
    lease = response.json()["lease"]
    assert lease is not None
    assert lease["ticket_id"] == ticket.id
    assert lease["ticket_title"] == "日常"
    assert lease["holder_agent"] == "daily_ops"
    assert lease["acquired_at"].endswith("Z")
    assert lease["expires_at"].endswith("Z")
    assert lease["heartbeat_at"].endswith("Z")


# ---------------------------------------------------------------------------
# 汇报分页
# ---------------------------------------------------------------------------
def test_list_ticket_reports_pages(tickets_env, make_client, tmp_settings):
    ticket, _ = _create(tickets_env)
    report_ids = [
        _report(tickets_env, ticket.id, ReportStatus.PROGRESS, summary=f"进展 {i}").id
        for i in range(1, 4)
    ]
    client = _client(tickets_env, make_client)

    first = client.get(f"/api/agent/tickets/{ticket.id}/reports?size=2")
    assert first.status_code == 200, first.text
    payload = first.json()
    assert (payload["total"], payload["page"], payload["size"]) == (3, 1, 2)
    assert [item["id"] for item in payload["items"]] == list(
        reversed(report_ids[-2:])
    )
    for item in payload["items"]:
        assert set(item) == REPORT_FIELDS

    second = client.get(f"/api/agent/tickets/{ticket.id}/reports?page=2&size=2")
    assert second.status_code == 200, second.text
    assert [item["id"] for item in second.json()["items"]] == [report_ids[0]]

    bad_page = client.get(f"/api/agent/tickets/{ticket.id}/reports?page=0")
    assert bad_page.status_code == 400, bad_page.text
    assert _error(bad_page)["code"] == "INVALID_PAGINATION"

    missing = client.get("/api/agent/tickets/nope/reports")
    assert missing.status_code == 404, missing.text
    assert _error(missing)["code"] == "TICKET_NOT_FOUND"


# ---------------------------------------------------------------------------
# 鉴权与 503
# ---------------------------------------------------------------------------
@pytest.mark.parametrize("tmp_settings", ["tickets-secret"], indirect=True)
def test_ticket_routes_require_auth(tickets_env, make_client, tmp_settings):
    ticket, _ = _create(tickets_env)
    _report(tickets_env, ticket.id, ReportStatus.PROGRESS)
    client = _client(tickets_env, make_client)
    paths = (
        "/api/agent/tickets",
        f"/api/agent/tickets/{ticket.id}",
        f"/api/agent/tickets/{ticket.id}/reports",
    )
    deps.reset_rate_limiter()
    try:
        for path in paths:
            response = client.get(path)
            assert response.status_code == 401, response.text
            assert _error(response)["code"] == "UNAUTHORIZED"

        headers = {"X-Token": "tickets-secret"}
        for path in paths:
            assert client.get(path, headers=headers).status_code == 200
    finally:
        deps.reset_rate_limiter()


def test_ticket_routes_report_503_without_a_service(
    tickets_env, make_client, tmp_settings
):
    """启动中（``app.state.ticket_service`` 还没设置）的固定文案。"""
    client = _client(tickets_env, make_client, with_service=False)

    for path in (
        "/api/agent/tickets",
        "/api/agent/tickets/nope",
        "/api/agent/tickets/nope/reports",
    ):
        response = client.get(path)
        assert response.status_code == 503, response.text
        error = _error(response)
        assert error["code"] == "AGENT_DISABLED"
        assert error["message"] == "工单服务还在启动中，请稍后重试"


# ---------------------------------------------------------------------------
# OpenAPI
# ---------------------------------------------------------------------------
def test_ticket_operation_ids(tickets_env, tmp_settings):
    schema = create_app().openapi()
    paths = schema["paths"]

    expected = {
        "/api/agent/tickets": ("agent_list_tickets", "分页读取工单"),
        "/api/agent/tickets/{ticket_id}": ("agent_get_ticket", "读取工单详情"),
        "/api/agent/tickets/{ticket_id}/reports": (
            "agent_list_ticket_reports",
            "分页读取工单汇报",
        ),
    }
    assert set(expected) <= set(paths)
    for path, (operation_id, summary) in expected.items():
        operation = paths[path]["get"]
        assert operation["operationId"] == operation_id
        assert operation["summary"] == summary
        assert operation["tags"] == ["agent"]

    components = schema["components"]["schemas"]
    for name in (
        "HumanActionOut",
        "TicketSummary",
        "MandateOut",
        "SubscriptionOut",
        "TicketReportOut",
        "TicketDetail",
        "TicketPage",
        "TicketReportPage",
    ):
        assert name in components
