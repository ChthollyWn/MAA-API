"""M75 T4：动态尾部的 ``plan`` 与 ``notes`` 段（spec §4.5、§6，06 §2.3/§7.2）。

两类用例：

- ``plan`` 段由 ``PlanMiddleware`` 写入，文本与 06 §7.2 的示例一致（「进行中」
  与每步状态）；没有计划时不插段。
- ``notes`` 段由 ``DurableContextMiddleware`` 写入，只列 ``{id, title}``，id 用
  完整值（模型要拿它调用 ``read_artifact``）；没有笔记时不插段。

异步用例沿用本仓的「同步测试函数 + ``asyncio.run(scenario())``」写法，临时库用
``tests/fakes/v3_db.make_session_factory``，不碰真实 ``resource/maa_api.db``。
"""

from __future__ import annotations

import asyncio
from datetime import datetime
from types import SimpleNamespace

from maa_api.agent.context.durable import DurableContextMiddleware, DurableState
from maa_api.agent.context.layout import ContextParts, TAIL_HEADER, parts_of
from maa_api.agent.harness.context import ModelRequest, RunContext
from maa_api.agent.harness.plan import PlanMiddleware
from maa_api.agent.plans.service import PlanService
from maa_api.agent.specs.registry import builtin_spec
from tests.fakes.v3_db import make_session_factory, seed_session

TITLE = "刷 1-7 到 50 个固源岩"
NOTE_ID = "6f1c2a9e-0000-4000-8000-000000000001"
NOTE_TITLE = "掉落与理智的对照"


def _context(spec_name: str, *, app: object, session_id: str) -> RunContext:
    return RunContext(
        run_id="run-dynamic-tail",
        root_run_id="run-dynamic-tail",
        parent_run_id=None,
        session_id=session_id,
        ticket_id=None,
        spec=builtin_spec(spec_name),
        app=app,
        state={},
    )


def _profile() -> SimpleNamespace:
    return SimpleNamespace(key="test")


def _request() -> ModelRequest:
    return ModelRequest(
        messages=[],
        tools=[],
        profile=_profile(),
        meta={"layout_parts": ContextParts()},
    )


def test_plan_segment_tracks_item_status(tmp_path) -> None:
    """``plan`` 段带「进行中」与每一步的状态；没有计划时不插段。"""

    async def scenario() -> None:
        engine, factory = await make_session_factory(tmp_path / "tail-plan.db")
        try:
            session_id = await seed_session(factory, kind="worker")
            service = PlanService(factory, approval_mode="complex", complex_min_steps=4)
            await service.create_or_update(
                session_id=session_id,
                ticket_id=None,
                title=TITLE,
                items=[
                    {"title": "查当前库存与理智", "status": "done"},
                    {"title": "提交 1-7 刷图流水线", "status": "in_progress"},
                    {"title": "结束后核对掉落", "status": "pending"},
                ],
            )
            app = SimpleNamespace(state=SimpleNamespace(plan_service=service))
            middleware = PlanMiddleware()
            ctx = _context("general", app=app, session_id=session_id)

            req = _request()
            await middleware.before_model(ctx, req)
            tail_text = req.messages[-1]["content"]
            assert isinstance(tail_text, str)
            assert tail_text.startswith(TAIL_HEADER)
            assert f"当前计划「{TITLE}」（进行中）：" in tail_text
            assert "1. [完成] 查当前库存与理智" in tail_text
            assert "2. [进行中] 提交 1-7 刷图流水线" in tail_text
            assert "3. [待办] 结束后核对掉落" in tail_text
            assert [segment.name for segment in parts_of(req).tail] == ["plan"]

            # 没有计划的会话：不插 plan 段，请求原样返回。
            other_session = await seed_session(factory, kind="worker")
            other = _context("general", app=app, session_id=other_session)
            empty = _request()
            await middleware.before_model(other, empty)
            assert empty.messages == []
            assert parts_of(empty).tail == []
        finally:
            engine.sync_engine.dispose()

    asyncio.run(scenario())


def test_notes_segment_lists_id_and_title() -> None:
    """``notes`` 段只列 id 与标题；没有笔记时不插段。"""

    async def scenario() -> None:
        durable = DurableState(
            captured_at=datetime(2026, 11, 4, 12, 0, 0).astimezone().isoformat(),
            notes=[{"id": NOTE_ID, "title": NOTE_TITLE}],
        )
        app = SimpleNamespace(state=SimpleNamespace())
        ctx = _context("general", app=app, session_id="session-notes")
        ctx.state["durable"] = durable
        middleware = DurableContextMiddleware()

        req = _request()
        await middleware.before_model(ctx, req)
        tail_text = req.messages[-1]["content"]
        assert isinstance(tail_text, str)
        assert tail_text.startswith(TAIL_HEADER)
        assert "本工单的笔记（要看正文时用 read_artifact）：" in tail_text
        assert f"「{NOTE_TITLE}」（{NOTE_ID}）" in tail_text
        assert [segment.name for segment in parts_of(req).tail] == ["notes"]

        # 没有笔记时不插 notes 段（也不插 tickets，因为 Worker 不写该段）。
        empty_ctx = _context("general", app=app, session_id="session-empty")
        empty_ctx.state["durable"] = DurableState(captured_at="2026-11-04T12:00:00+08:00")
        empty_req = _request()
        await middleware.before_model(empty_ctx, empty_req)
        assert empty_req.messages == []
        assert parts_of(empty_req).tail == []

    asyncio.run(scenario())
