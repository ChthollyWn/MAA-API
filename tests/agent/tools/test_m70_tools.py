"""M70 T6：七个新工具的注册、REST 隔离、v2 工具声明与 handler 语义（规格 §4.5.4、§4.5.11、§6）。

五条用例：七个工具不在 REST 目录与 invoke 里、v2 会话的工具声明排除它们、
``report`` 的参数与调用方检查、``create_ticket`` 的 kind 与委托强度、已结束工单的
``send_to_ticket`` / ``cancel_ticket`` 报 ``TICKET_ENDED``。WorkerService /
DelegationService 在 T9/T10 才落地，这里用 ``app.state`` 上的替身注入。
"""

from __future__ import annotations

import asyncio
from types import SimpleNamespace

from fastapi import FastAPI
from fastapi.testclient import TestClient
from pydantic import ValidationError

from maa_api.agent.harness.context import CURRENT_RUN, RunContext
from maa_api.agent.registry import ToolContext
from maa_api.agent.runtime import AgentRuntime
from maa_api.agent.specs.registry import builtin_spec
from maa_api.agent.tools import build_registry
from maa_api.agent.tools.delegate import DelegateParams, delegate
from maa_api.agent.tools.report import ReportParams, report
from maa_api.agent.tools.tickets import (
    CancelTicketParams,
    CreateTicketParams,
    GetTicketParams,
    ListTicketsParams,
    SendToTicketParams,
    cancel_ticket,
    create_ticket,
    get_ticket,
    list_tickets,
    send_to_ticket,
)
from maa_api.api.deps import get_session
from maa_api.api.errors import register_exception_handlers
from maa_api.api.routers import agent
from maa_api.domain.enums import CallerType, TicketStatus
from maa_api.domain.errors import AppError, ErrorCode
from maa_api.settings import LLMSettings, Settings, set_settings

V3_TOOLS = {
    "report",
    "delegate",
    "create_ticket",
    "send_to_ticket",
    "list_tickets",
    "get_ticket",
    "cancel_ticket",
}


def _context() -> ToolContext:
    return ToolContext(CallerType.INTERNAL, None, None, None, None)


def _run(
    name: str, *, app: object = None, inbox_items: list[object] | None = None
) -> RunContext:
    return RunContext(
        run_id="run-m70",
        root_run_id="run-m70",
        parent_run_id=None,
        session_id="session-orchestrator",
        ticket_id=None if name == "orchestrator" else "ticket-1",
        spec=builtin_spec(name),
        app=app,
        inbox_items=list(inbox_items or ()),
    )


def _state(**services: object) -> SimpleNamespace:
    return SimpleNamespace(state=SimpleNamespace(**services))


class _current_run:
    """``CURRENT_RUN`` 的上下文管理器；``run=None`` 时清空。"""

    def __init__(self, run: RunContext | None) -> None:
        self._run = run
        self._token = None

    def __enter__(self) -> None:
        self._token = CURRENT_RUN.set(self._run)

    def __exit__(self, *exc_info: object) -> None:
        CURRENT_RUN.reset(self._token)


class _expect_app_error:
    """断言抛出指定错误码的 ``AppError``。"""

    def __init__(self, code: ErrorCode, message: str | None = None) -> None:
        self._code = code
        self._message = message

    def __enter__(self) -> None:
        return None

    def __exit__(self, exc_type: object, exc: object, tb: object) -> bool:
        assert exc_type is AppError, exc
        assert exc.code == self._code, exc
        if self._message is not None:
            assert self._message in exc.message, exc.message
        return True


def test_v3_tools_are_hidden_from_rest_catalog_and_invoke():
    registry = build_registry()
    app = FastAPI()
    register_exception_handlers(app)
    app.include_router(agent.router)
    app.state.confirmation_service = SimpleNamespace(registry=registry)
    set_settings(
        Settings(
            llm=LLMSettings(base_url="https://llm.test/v1", api_key="k", model="m")
        )
    )

    async def _no_session():
        yield None

    app.dependency_overrides[get_session] = _no_session
    client = TestClient(app, raise_server_exceptions=False)
    with client:
        listed = client.get("/api/agent/tools")
        assert listed.status_code == 200, listed.text
        names = {item["name"] for item in listed.json()["items"]}
        assert names.isdisjoint(V3_TOOLS)

        for name in sorted(V3_TOOLS):
            invoked = client.post(
                f"/api/agent/tools/{name}/invoke", json={"arguments": {}}
            )
            assert invoked.status_code == 404, (name, invoked.text)
            error = invoked.json()["error"]
            assert error["code"] == ErrorCode.TOOL_NOT_FOUND.value
            assert set(error) == {"code", "message"}


def test_legacy_session_tool_schema_excludes_v3_tools():
    registry = build_registry()
    runtime = AgentRuntime(None, SimpleNamespace(registry=registry))  # type: ignore[arg-type]

    allowed = runtime.session_allowed_tools("legacy-session")

    assert allowed.isdisjoint(V3_TOOLS)
    assert "get_screenshot" in allowed
    assert "check_confirmation" in allowed
    catalog = runtime.tool_group_catalog("legacy-session")
    assert "worker" not in catalog["core_groups"]
    assert "tickets" not in catalog["core_groups"]
    assert "worker" not in catalog["deferred_groups"]
    # 扩展组开关行为不变：开启后仍只有 v2 工具。
    runtime.enable_tool_groups("legacy-session", {"account"})
    assert "get_operator_box" in runtime.session_allowed_tools("legacy-session")
    assert runtime.session_allowed_tools("legacy-session").isdisjoint(V3_TOOLS)


def test_report_params_and_caller_checks():
    async def scenario() -> None:
        for status in ("waiting", "blocked"):
            with _expect_validation_error("human_action"):
                ReportParams.model_validate({"status": status, "summary": "等你"})
        assert (
            ReportParams.model_validate(
                {
                    "status": "waiting",
                    "summary": "等你选关卡",
                    "human_action": {"type": "question", "prompt": "刷哪一关？"},
                }
            ).human_action.prompt
            == "刷哪一关？"
        )
        with _expect_validation_error("4000"):
            ReportParams.model_validate(
                {"status": "progress", "summary": "推进", "details": {"x": "字" * 4000}}
            )

        valid = ReportParams.model_validate({"status": "ready", "summary": "做完了"})
        # 不在运行里 / 在总控运行里：都只能由工单里的 Worker 调用。
        for run in (None, _run("orchestrator")):
            with _current_run(run):
                with _expect_app_error(
                    ErrorCode.INVALID_PARAMETER, "report 只能由工单里的 Worker 调用"
                ):
                    await report(valid, _context())

        class WorkerService:
            def __init__(self) -> None:
                self.calls: list[object] = []

            async def submit_report(self, run: RunContext, params: object) -> dict[str, object]:
                self.calls.append(params)
                return {"report_id": 41, "ticket_status": "done", "forwarded": True}

        service = WorkerService()
        with _current_run(_run("general", app=_state(worker_service=service))):
            result = await report(valid, _context())
        assert result == {"report_id": 41, "ticket_status": "done", "forwarded": True}
        assert len(service.calls) == 1

    asyncio.run(scenario())


class _expect_validation_error:
    def __init__(self, text: str) -> None:
        self._text = text

    def __enter__(self) -> None:
        return None

    def __exit__(self, exc_type: object, exc: object, tb: object) -> bool:
        assert exc_type is ValidationError, exc
        assert self._text in str(exc), exc
        return True


def test_create_ticket_kind_and_strength_rules():
    async def scenario() -> None:
        class WorkerService:
            def __init__(self) -> None:
                self.calls: list[dict[str, object]] = []

            async def open_ticket(self, **kwargs: object) -> SimpleNamespace:
                self.calls.append(kwargs)
                return SimpleNamespace(
                    ticket_id="ticket-9",
                    worker_session_id="worker-session",
                    title=kwargs["title"],
                    kind=kwargs["kind"],
                    status="open",
                )

        service = WorkerService()
        events: list[tuple[str, object]] = []

        async def emit(name: str, data: object) -> None:
            events.append((name, data))

        run = _run("orchestrator", app=_state(worker_service=service))
        run.emit = emit  # type: ignore[method-assign]
        arguments = {
            "kind": "general",
            "title": "日常",
            "brief": "把日常做了",
            "scope": "日常",
        }

        # legacy 与不存在的 kind 都在进服务之前被拒。
        for kind in ("legacy_chat", "no_such_worker"):
            with _current_run(run):
                with _expect_app_error(ErrorCode.INVALID_PARAMETER, "没有叫"):
                    await create_ticket(
                        CreateTicketParams.model_validate({**arguments, "kind": kind}),
                        _context(),
                    )
        assert service.calls == []

        # 有用户消息：显式委托，source / sender 取最后一条 payload。
        run.inbox_items[:] = [
            SimpleNamespace(kind="report", payload={"status": "progress"}),
            SimpleNamespace(
                kind="user_message",
                payload={"text": "把日常做了", "source": "web", "sender": "web"},
            ),
        ]
        params = CreateTicketParams.model_validate(arguments)
        with _current_run(run):
            opened = await create_ticket(params, _context())
        assert opened == {
            "ticket_id": "ticket-9",
            "title": "日常",
            "kind": "general",
            "status": "open",
            "worker_session_id": "worker-session",
            "strength": "explicit",
        }
        call = service.calls[-1]
        assert call["strength"] == "explicit"
        assert call["origin"] == "user"
        assert call["source"] == "web"
        assert call["sender"] == "web"
        assert call["orchestrator_session_id"] == "session-orchestrator"
        assert events[-1] == (
            "ticket_created",
            {
                "ticket_id": "ticket-9",
                "title": "日常",
                "kind": "general",
                "status": "open",
            },
        )

        # 只有汇报（没有用户消息）：推断委托，来源 system。
        run.inbox_items[:] = [SimpleNamespace(kind="report", payload={})]
        with _current_run(run):
            await create_ticket(params, _context())
        call = service.calls[-1]
        assert call["strength"] == "inferred"
        assert call["origin"] == "system"
        assert call["source"] == "system"
        assert call["sender"] == "orchestrator"

    asyncio.run(scenario())


def test_delegate_shell_forwards_to_the_delegation_service():
    async def scenario() -> None:
        class DelegationService:
            def __init__(self) -> None:
                self.calls: list[tuple[object, str, str, str | None]] = []

            async def delegate(self, parent, *, agent, task, context):
                self.calls.append((parent, agent, task, context))
                return {"summary": "掉率查到了", "degraded": False, "agent": agent}

        service = DelegationService()
        run = _run("general", app=_state(delegation_service=service))
        params = DelegateParams.model_validate(
            {"agent": "researcher", "task": "查 1-7 掉率", "context": "关卡 1-7"}
        )
        with _current_run(run):
            result = await delegate(params, _context())

        assert result == {
            "summary": "掉率查到了",
            "degraded": False,
            "agent": "researcher",
        }
        assert service.calls == [(run, "researcher", "查 1-7 掉率", "关卡 1-7")]

    asyncio.run(scenario())


def test_list_and_get_ticket_read_through_the_ticket_service():
    async def scenario() -> None:
        from datetime import datetime

        class TicketService:
            async def list(self, *, statuses, page, size):
                assert statuses == [TicketStatus.OPEN]
                assert (page, size) == (1, 5)
                return SimpleNamespace(
                    items=[
                        SimpleNamespace(
                            id="ticket-1",
                            title="日常",
                            kind="general",
                            status="open",
                            last_report_status="progress",
                            summary="正在跑",
                            human_action=None,
                            updated_at=datetime(2026, 1, 2, 3, 4, 5),
                        )
                    ],
                    total=1,
                )

            async def detail(self, ticket_id: str):
                stamp = datetime(2026, 1, 2, 3, 4, 5)
                return SimpleNamespace(
                    ticket=SimpleNamespace(
                        id=ticket_id,
                        title="日常",
                        kind="general",
                        status="open",
                        origin="user",
                        priority=1,
                        summary="正在跑",
                        last_report_status="progress",
                        human_action=None,
                        result=None,
                        worker_session_id="worker-session",
                        created_at=stamp,
                        updated_at=stamp,
                        ended_at=None,
                    ),
                    mandate=SimpleNamespace(
                        id="mandate-1",
                        scope="日常",
                        strength="explicit",
                        source="web",
                        sender="web",
                        rule_id=None,
                        risk_ceiling="destructive",
                        policy_version=None,
                        created_at=stamp,
                    ),
                    subscriptions=[],
                    recent_reports=[],
                    turn_running=False,
                )

        run = _run("orchestrator", app=_state(ticket_service=TicketService()))
        with _current_run(run):
            listed = await list_tickets(
                ListTicketsParams(status=[TicketStatus.OPEN], limit=5), _context()
            )
            detail = await get_ticket(
                GetTicketParams(ticket_id="ticket-1"), _context()
            )

        assert listed == {
            "items": [
                {
                    "ticket_id": "ticket-1",
                    "title": "日常",
                    "kind": "general",
                    "status": "open",
                    "last_report_status": "progress",
                    "summary": "正在跑",
                    "human_action": None,
                    "updated_at": "2026-01-02T03:04:05Z",
                }
            ],
            "total": 1,
        }
        assert detail["ticket"]["id"] == "ticket-1"
        assert detail["ticket"]["updated_at"] == "2026-01-02T03:04:05Z"
        assert detail["mandate"]["scope"] == "日常"
        assert detail["worker_session_id"] == "worker-session"
        assert detail["turn_running"] is False

    asyncio.run(scenario())


def test_send_and_cancel_on_ended_ticket_return_ticket_ended():
    async def scenario() -> None:
        class WorkerService:
            async def send_from_orchestrator(self, ticket_id: str, content: str) -> int:
                raise AppError(
                    ErrorCode.TICKET_ENDED, "工单已结束", {"ticket_id": ticket_id}
                )

            async def cancel(self, ticket_id: str, *, reason: str, actor: str):
                raise AppError(
                    ErrorCode.TICKET_ENDED, "工单已结束", {"ticket_id": ticket_id}
                )

        run = _run("orchestrator", app=_state(worker_service=WorkerService()))
        with _current_run(run):
            with _expect_app_error(ErrorCode.TICKET_ENDED):
                await send_to_ticket(
                    SendToTicketParams(ticket_id="ticket-1", content="再加一次"),
                    _context(),
                )
            with _expect_app_error(ErrorCode.TICKET_ENDED):
                await cancel_ticket(
                    CancelTicketParams(ticket_id="ticket-1", reason="不做了"),
                    _context(),
                )

    asyncio.run(scenario())
