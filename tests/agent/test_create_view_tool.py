"""M90 T2：``create_view`` 工具与注册（规格 §4.3.2、§4.5.1、§6）。"""

from __future__ import annotations

import asyncio
import json
from types import SimpleNamespace

import pytest
from sqlalchemy import func, select

from maa_api.agent.artifacts.service import ArtifactService
from maa_api.agent.artifacts.views import VIEW_MIME, validate_view, view_fields, view_kind
from maa_api.agent.harness.context import CURRENT_RUN, RunContext
from maa_api.agent.registry import ToolContext, ToolRisk
from maa_api.agent.specs.registry import builtin_spec
from maa_api.agent.tools import build_registry
from maa_api.agent.tools.artifacts import CreateViewParams, create_view
from maa_api.db.models import Artifact, Ticket
from maa_api.domain.enums import CallerType, TicketStatus
from maa_api.domain.errors import AppError, ErrorCode
from maa_api.settings import Settings, set_settings
from tests.fakes.v3_db import make_session_factory, seed_run, seed_session

#: 规格 §4.3.1 的例子（三个表单字段）。
EXAMPLE_VIEW = {
    "version": 1,
    "title": "这周刷哪一关",
    "components": [
        {"type": "text", "text": "按企鹅物流近 30 天的掉落率算出期望理智，越低越划算。"},
        {
            "type": "field",
            "field": "select",
            "name": "stage",
            "label": "关卡",
            "options": [
                {"value": "1-7", "label": "1-7"},
                {"value": "S4-1", "label": "S4-1"},
            ],
        },
        {
            "type": "field",
            "field": "number",
            "name": "times",
            "label": "次数",
            "min": 1,
            "max": 99,
            "default": 10,
        },
        {
            "type": "field",
            "field": "toggle",
            "name": "use_medicine",
            "label": "可以用理智药",
        },
        {"type": "submit", "label": "就这么刷"},
    ],
}


class _current_run:
    def __init__(self, run: RunContext) -> None:
        self._run = run
        self._token = None

    def __enter__(self) -> None:
        self._token = CURRENT_RUN.set(self._run)

    def __exit__(self, *exc_info: object) -> None:
        CURRENT_RUN.reset(self._token)


async def _seed_ticket(factory, title: str) -> str:
    ticket = Ticket(
        title=title,
        kind="general",
        status=TicketStatus.OPEN.value,
        origin="user",
    )
    async with factory() as db:
        db.add(ticket)
        await db.commit()
    return str(ticket.id)


def _spec(*tools: str):
    base = builtin_spec("general")
    return base.model_copy(update={"tools": (*base.tools, *tools)})


def _run(
    *, session_id: str, ticket_id: str | None, app: object
) -> RunContext:
    return RunContext(
        run_id="run-create-view",
        root_run_id="run-create-view",
        parent_run_id=None,
        session_id=session_id,
        ticket_id=ticket_id,
        spec=_spec("create_view"),
        app=app,
    )


def _tool_context(run: RunContext) -> ToolContext:
    return ToolContext(CallerType.INTERNAL, run.session_id, None, None, None)


def test_create_view_stores_form_artifact_for_ticket(tmp_path) -> None:
    async def scenario() -> None:
        engine, factory = await make_session_factory(tmp_path / "create-view.db")
        try:
            ticket_id = await _seed_ticket(factory, "生成视图")
            session_id = await seed_session(factory, kind="worker", ticket_id=ticket_id)
            run_id = await seed_run(factory, session_id=session_id, ticket_id=ticket_id)
            service = ArtifactService(factory, resource_root=tmp_path / "resource")
            app = SimpleNamespace(state=SimpleNamespace(artifact_service=service))
            run = _run(session_id=session_id, ticket_id=ticket_id, app=app)
            run.run_id = run_id
            ctx = _tool_context(run)

            with _current_run(run):
                created = await create_view(CreateViewParams(view=EXAMPLE_VIEW), ctx)

            assert set(created) == {"artifact_id", "kind", "title", "field_count"}
            assert created["kind"] == "form"
            assert created["title"] == "这周刷哪一关"
            assert created["field_count"] == 3

            detail = await service.get(created["artifact_id"])
            assert detail.artifact.kind == "form"
            assert detail.artifact.mime == VIEW_MIME
            assert detail.artifact.ticket_id == ticket_id
            assert detail.artifact.session_id == session_id
            assert detail.artifact.run_id == run_id
            assert detail.artifact.meta["view_version"] == 1
            assert detail.artifact.meta["submissions"] == []
            assert detail.artifact.meta["last_submitted_at"] is None

            spec = validate_view(json.loads(detail.content))
            assert view_kind(spec) == "form"
            assert [field.name for field in view_fields(spec)] == [
                "stage",
                "times",
                "use_medicine",
            ]
        finally:
            engine.sync_engine.dispose()

    asyncio.run(scenario())


def test_create_view_returns_errors_with_paths(tmp_path) -> None:
    async def scenario() -> None:
        engine, factory = await make_session_factory(tmp_path / "invalid.db")
        try:
            ticket_id = await _seed_ticket(factory, "坏视图")
            session_id = await seed_session(factory, kind="worker", ticket_id=ticket_id)
            service = ArtifactService(factory, resource_root=tmp_path / "resource")
            app = SimpleNamespace(state=SimpleNamespace(artifact_service=service))
            run = _run(session_id=session_id, ticket_id=ticket_id, app=app)
            ctx = _tool_context(run)
            invalid = {
                "version": 1,
                "title": "坏视图",
                "components": [{"type": "html"}],
            }

            with _current_run(run), pytest.raises(AppError) as caught:
                await create_view(CreateViewParams(view=invalid), ctx)

            assert caught.value.code == ErrorCode.TOOL_ARGS_INVALID
            assert caught.value.details["errors"][0]["path"] == "components.0"
            async with factory() as db:
                count = await db.scalar(select(func.count()).select_from(Artifact))
            assert count == 0
        finally:
            engine.sync_engine.dispose()

    asyncio.run(scenario())


def test_create_view_requires_a_ticket_session(tmp_path) -> None:
    async def scenario() -> None:
        engine, factory = await make_session_factory(tmp_path / "no-ticket.db")
        try:
            session_id = await seed_session(factory, kind="worker")
            service = ArtifactService(factory, resource_root=tmp_path / "resource")
            app = SimpleNamespace(state=SimpleNamespace(artifact_service=service))
            run = _run(session_id=session_id, ticket_id=None, app=app)
            ctx = _tool_context(run)

            with _current_run(run), pytest.raises(AppError) as caught:
                await create_view(CreateViewParams(view=EXAMPLE_VIEW), ctx)

            assert caught.value.code == ErrorCode.INVALID_PARAMETER
            assert caught.value.message == "只能在工单里生成视图"
            async with factory() as db:
                count = await db.scalar(select(func.count()).select_from(Artifact))
            assert count == 0
        finally:
            engine.sync_engine.dispose()

    asyncio.run(scenario())


def test_create_view_is_hidden_from_rest_catalog() -> None:
    from fastapi import FastAPI
    from fastapi.testclient import TestClient

    from maa_api.api.errors import register_exception_handlers
    from maa_api.api.routers import agent
    from maa_api.api.routers.agent import _ensure_rest_visible

    registry = build_registry()
    definition = registry.get("create_view")
    assert definition.group == "artifacts"
    assert definition.risk == ToolRisk.SAFE
    assert definition.read_only is False
    assert definition.refetchable is False
    assert definition.device == "none"
    assert definition.untrusted_output is False
    assert definition.rest_visible is False
    assert definition.model_visible is True
    assert "artifacts" in {item.group for item in registry.list()}

    set_settings(Settings())
    app = FastAPI()
    register_exception_handlers(app)
    app.include_router(agent.router)
    app.state.confirmation_service = SimpleNamespace(registry=registry)
    client = TestClient(app)
    try:
        response = client.get("/api/agent/tools")
        assert response.status_code == 200
        assert "create_view" not in {item["name"] for item in response.json()["items"]}
    finally:
        client.close()

    with pytest.raises(AppError) as caught:
        _ensure_rest_visible(SimpleNamespace(registry=registry), "create_view")
    assert caught.value.code == ErrorCode.TOOL_NOT_FOUND
