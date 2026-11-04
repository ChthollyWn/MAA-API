"""M75 T2：工具结果外置与续读（规格 §4.5、§6，v3 06 §6.1/§6.3）。

长输出用例走真实链（``SpillMiddleware`` 外层 + ``PolicyMiddleware`` 内层）：审计在
替换之前落全文，返回给模型与 SSE 的是指针。短输出、定义里没有 ``read_artifact`` 的
代理不外置；续读工具覆盖偏移、上限、末尾与工单/会话范围。
"""

from __future__ import annotations

import asyncio
import json
from types import SimpleNamespace

import pytest
from pydantic import BaseModel, ConfigDict
from sqlalchemy import select

from maa_api.agent.artifacts.service import ArtifactService
from maa_api.agent.confirmation import ConfirmationService
from maa_api.agent.harness.builtin_middleware import PolicyMiddleware
from maa_api.agent.harness.context import (
    CURRENT_RUN,
    RunContext,
    ToolCall,
    ToolOutcome,
)
from maa_api.agent.harness.middleware import MiddlewareChain
from maa_api.agent.harness.spill import (
    NEXT_OFFSET_AFTER_PREVIEW,
    PREVIEW_HEAD_CHARS,
    PREVIEW_TAIL_CHARS,
    SPILL_HINT,
    SpillMiddleware,
)
from maa_api.agent.registry import ToolContext, ToolDefinition, ToolRegistry, ToolRisk
from maa_api.agent.specs.registry import builtin_spec
from maa_api.agent.tools import build_registry
from maa_api.agent.tools.workspace import ReadArtifactParams, read_artifact
from maa_api.db.models import AgentAudit, Ticket
from maa_api.domain.enums import CallerType, TicketStatus
from maa_api.domain.errors import AppError, ErrorCode
from tests.fakes.v3_db import make_session_factory, seed_run, seed_session

LONG_TEXT = "0123456789abcdef" * 1250  # 20 000 字


class _LongParams(BaseModel):
    model_config = ConfigDict(extra="forbid")


def _spec(*tools: str):
    base = builtin_spec("general")
    return base.model_copy(update={"tools": (*base.tools, *tools)})


def _long_tool() -> ToolDefinition:
    async def handler(_params: _LongParams, _ctx: ToolContext) -> dict:
        return {"text": LONG_TEXT}

    return ToolDefinition(
        name="get_long_text",
        group="test",
        risk=ToolRisk.SAFE,
        description="测试用长输出工具",
        params_model=_LongParams,
        handler=handler,
        read_only=True,
    )


def _registry() -> ToolRegistry:
    registry = build_registry()
    registry.register(_long_tool())
    return registry


def _run(
    *,
    session_id: str,
    ticket_id: str | None,
    app: object,
    tools: tuple[str, ...] = ("read_artifact",),
) -> RunContext:
    return RunContext(
        run_id="run-spill",
        root_run_id="run-spill",
        parent_run_id=None,
        session_id=session_id,
        ticket_id=ticket_id,
        spec=_spec(*tools),
        app=app,
    )


async def _seed_ticket(factory, title: str) -> str:
    ticket = Ticket(title=title, kind="general", status=TicketStatus.OPEN.value, origin="user")
    async with factory() as db:
        db.add(ticket)
        await db.commit()
    return str(ticket.id)


class _current_run:
    def __init__(self, run: RunContext) -> None:
        self._run = run
        self._token = None

    def __enter__(self) -> None:
        self._token = CURRENT_RUN.set(self._run)

    def __exit__(self, *exc_info: object) -> None:
        CURRENT_RUN.reset(self._token)


def _tool_context(run: RunContext) -> ToolContext:
    return ToolContext(CallerType.INTERNAL, run.session_id, None, None, None)


def test_long_output_becomes_pointer_and_audit_keeps_full_text(tmp_path) -> None:
    async def scenario() -> None:
        engine, factory = await make_session_factory(tmp_path / "spill.db")
        try:
            session_id = await seed_session(factory, kind="worker")
            run_id = await seed_run(factory, session_id=session_id)
            registry = _registry()
            service = ArtifactService(factory, resource_root=tmp_path / "resource")
            app = SimpleNamespace(state=SimpleNamespace(artifact_service=service))
            confirmations = ConfirmationService(factory, registry)
            policy = PolicyMiddleware(
                confirmation_service=confirmations,
                session_factory=factory,
                poll_seconds=0.01,
            )
            chain = MiddlewareChain(
                [SpillMiddleware(threshold_chars=100), policy]
            )
            ctx = _run(session_id=session_id, ticket_id=None, app=app)
            ctx.run_id = run_id
            ctx.run = SimpleNamespace(kind="worker_turn")
            call = ToolCall(
                id="call-long", name="get_long_text", raw_arguments="{}", arguments={}
            )

            async def innermost(_call: ToolCall) -> ToolOutcome:  # pragma: no cover - policy 是执行器
                raise AssertionError("PolicyMiddleware 不应调用 call_next")

            outcome = await chain.wrap_tool(ctx, call, innermost)
            assert outcome.status == "ok"
            pointer = outcome.result
            full = json.dumps({"text": LONG_TEXT}, ensure_ascii=False)
            assert pointer["spilled"] is True
            assert pointer["tool"] == "get_long_text"
            assert pointer["total_chars"] == len(full)
            assert pointer["next_offset"] == NEXT_OFFSET_AFTER_PREVIEW == 3000
            assert pointer["hint"] == SPILL_HINT
            omitted = len(full) - PREVIEW_HEAD_CHARS - PREVIEW_TAIL_CHARS
            assert pointer["preview"] == (
                f"{full[:PREVIEW_HEAD_CHARS]}\n"
                f"[中间省略 {omitted} 字]\n"
                f"{full[-PREVIEW_TAIL_CHARS:]}"
            )
            # content 与 result 必须一起换：SSE 发的是 result。
            assert outcome.content == json.dumps(pointer, ensure_ascii=False)
            assert outcome.body()["result"] == pointer

            # 审计在替换之前落原文（顺序由本用例锁住）。审计列按既有契约截到
            # 2 KB（docs/04 §5.9），所以断言是「原文前缀」且不含指针。
            async with factory() as db:
                audits = list((await db.execute(select(AgentAudit))).scalars().all())
            assert len(audits) == 1
            assert audits[0].id == outcome.audit_id
            assert audits[0].status == "success"
            assert audits[0].result_summary == full[:2048]
            assert "spilled" not in (audits[0].result_summary or "")

            detail = await service.get(pointer["artifact_id"])
            assert detail.content == full
            assert detail.artifact.kind == "spill"
            assert detail.artifact.title == "get_long_text 的输出"
            assert detail.artifact.session_id == session_id
            assert detail.artifact.run_id == run_id
            assert detail.artifact.meta == {"tool": "get_long_text", "total_chars": len(full)}
        finally:
            engine.sync_engine.dispose()

    asyncio.run(scenario())


def test_short_output_unchanged(tmp_path) -> None:
    async def scenario() -> None:
        engine, factory = await make_session_factory(tmp_path / "short.db")
        try:
            service = ArtifactService(factory, resource_root=tmp_path / "resource")
            app = SimpleNamespace(state=SimpleNamespace(artifact_service=service))
            middleware = SpillMiddleware(threshold_chars=8000)
            ctx = _run(session_id="session-short", ticket_id=None, app=app)
            result = {"text": "短结果"}

            async def inner(_call: ToolCall) -> ToolOutcome:
                return ToolOutcome(status="ok", result=result)

            outcome = await middleware.wrap_tool(
                ctx, ToolCall(id="c", name="get_patch", raw_arguments="{}"), inner
            )
            assert outcome.result is result
            assert outcome.content is None
            page = await service.list()
            assert page.total == 0
        finally:
            engine.sync_engine.dispose()

    asyncio.run(scenario())


def test_agent_without_read_artifact_does_not_spill(tmp_path) -> None:
    async def scenario() -> None:
        engine, factory = await make_session_factory(tmp_path / "no-read.db")
        try:
            service = ArtifactService(factory, resource_root=tmp_path / "resource")
            app = SimpleNamespace(state=SimpleNamespace(artifact_service=service))
            middleware = SpillMiddleware(threshold_chars=10)
            # 总控 / 子代理的当前定义都不带 read_artifact。
            ctx = _run(session_id="session-no-read", ticket_id=None, app=app, tools=())

            async def inner(_call: ToolCall) -> ToolOutcome:
                return ToolOutcome(status="ok", result={"text": LONG_TEXT})

            outcome = await middleware.wrap_tool(
                ctx, ToolCall(id="c", name="get_long_text", raw_arguments="{}"), inner
            )
            assert outcome.result == {"text": LONG_TEXT}
            assert outcome.content is None
            page = await service.list()
            assert page.total == 0
        finally:
            engine.sync_engine.dispose()

    asyncio.run(scenario())


def test_read_artifact_pages(tmp_path) -> None:
    async def scenario() -> None:
        engine, factory = await make_session_factory(tmp_path / "pages.db")
        try:
            ticket_id = await _seed_ticket(factory, "读产物")
            session_id = await seed_session(factory, kind="worker", ticket_id=ticket_id)
            run_id = await seed_run(factory, session_id=session_id, ticket_id=ticket_id)
            service = ArtifactService(factory, resource_root=tmp_path / "resource")
            text = "abcdefghij" * 1000  # 10 000 字
            artifact = await service.put(
                kind="spill",
                title="长输出",
                body=text.encode(),
                session_id=session_id,
                ticket_id=ticket_id,
                run_id=run_id,
            )
            app = SimpleNamespace(state=SimpleNamespace(artifact_service=service))
            run = _run(session_id=session_id, ticket_id=ticket_id, app=app)
            run.run_id = run_id
            ctx = _tool_context(run)
            params = ReadArtifactParams(artifact_id=str(artifact.id))

            with _current_run(run):
                first = await read_artifact(params, ctx)
                assert first["artifact_id"] == artifact.id
                assert first["total_chars"] == 10_000
                assert first["offset"] == 0
                assert len(first["content"]) == 8000  # 缺省上限 = 外置阈值
                assert first["next_offset"] == 8000

                tail = await read_artifact(
                    ReadArtifactParams(
                        artifact_id=str(artifact.id), offset=9000, limit=3000
                    ),
                    ctx,
                )
                assert tail["content"] == text[9000:]
                assert tail["next_offset"] is None

                clamped = await read_artifact(
                    ReadArtifactParams(
                        artifact_id=str(artifact.id), offset=-5, limit=10_000_000
                    ),
                    ctx,
                )
                assert clamped["offset"] == 0
                assert len(clamped["content"]) == 8000

                beyond = await read_artifact(
                    ReadArtifactParams(artifact_id=str(artifact.id), offset=20_000),
                    ctx,
                )
                assert beyond["content"] == ""
                assert beyond["next_offset"] is None
                assert beyond["offset"] == 20_000
        finally:
            engine.sync_engine.dispose()

    asyncio.run(scenario())


def test_other_ticket_artifact_is_404(tmp_path) -> None:
    async def scenario() -> None:
        engine, factory = await make_session_factory(tmp_path / "scoped.db")
        try:
            ticket_a = await _seed_ticket(factory, "工单 A")
            ticket_b = await _seed_ticket(factory, "工单 B")
            session_a = await seed_session(factory, kind="worker", ticket_id=ticket_a)
            session_b = await seed_session(factory, kind="worker", ticket_id=ticket_b)
            run_a = await seed_run(factory, session_id=session_a, ticket_id=ticket_a)
            run_b = await seed_run(factory, session_id=session_b, ticket_id=ticket_b)
            service = ArtifactService(factory, resource_root=tmp_path / "resource")
            artifact = await service.put(
                kind="spill",
                title="A 的产物",
                body=b"secret",
                session_id=session_a,
                ticket_id=ticket_a,
                run_id=run_a,
            )
            app = SimpleNamespace(state=SimpleNamespace(artifact_service=service))
            params = ReadArtifactParams(artifact_id=str(artifact.id))

            other_ticket = _run(session_id=session_a, ticket_id=ticket_b, app=app)
            other_ticket.run_id = run_b
            with _current_run(other_ticket), pytest.raises(AppError) as caught:
                await read_artifact(params, _tool_context(other_ticket))
            assert caught.value.code == ErrorCode.ARTIFACT_NOT_FOUND
            assert caught.value.http_status == 404

            other_session = _run(session_id=session_b, ticket_id=ticket_a, app=app)
            other_session.run_id = run_b
            with _current_run(other_session), pytest.raises(AppError) as caught:
                await read_artifact(params, _tool_context(other_session))
            assert caught.value.code == ErrorCode.ARTIFACT_NOT_FOUND

            missing = _run(session_id=session_a, ticket_id=ticket_a, app=app)
            missing.run_id = run_a
            with _current_run(missing), pytest.raises(AppError) as caught:
                await read_artifact(
                    ReadArtifactParams(artifact_id="does-not-exist"),
                    _tool_context(missing),
                )
            assert caught.value.code == ErrorCode.ARTIFACT_NOT_FOUND
        finally:
            engine.sync_engine.dispose()

    asyncio.run(scenario())
