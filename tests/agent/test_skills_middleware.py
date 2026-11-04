"""M78 T4：技能中间件（规格 §6 的九条用例）。

中间件三个钩子各自单独驱动：``on_run_start`` 继承激活项并算好第 4 段，
``before_model`` 把第 4 段写进稳定段，``wrap_tool`` 记录激活与工具组，
``on_run_end`` 把激活项写回 ``agent_run.meta["skills"]``。最后一条用 ``FakeLLM``
驱动 ``AgentLoop``，走一遍真实循环的「加载 → 下一次模型请求 → 收尾写 meta」。

异步用例沿用本仓的「同步测试函数 + ``asyncio.run(scenario())``」写法，临时库用
``tests/fakes/v3_db.make_session_factory``，不碰真实资源。
"""

from __future__ import annotations

import asyncio
from datetime import timedelta
from types import SimpleNamespace
from typing import Any

from sqlalchemy import select

from maa_api.agent.context.durable import DurableContextMiddleware
from maa_api.agent.context.layout import ContextParts, Segment, parts_of
from maa_api.agent.harness.context import (
    ModelRequest,
    RunContext,
    ToolCall,
    ToolOutcome,
)
from maa_api.agent.harness.loop import AgentLoop, MemoryTranscript
from maa_api.agent.harness.middleware import Middleware, MiddlewareChain
from maa_api.agent.registry import ToolContext, ToolRegistry
from maa_api.agent.skills.middleware import (
    ALREADY_LOADED_NOTE,
    DEFAULT_LOADED_SEQ,
    PRELOADED_HEADER,
    RESTORED_HEADER,
    SkillsMiddleware,
)
from maa_api.agent.skills.registry import SkillRegistry, builtin_skills
from maa_api.agent.specs.registry import builtin_spec, builtin_specs
from maa_api.agent.tools import build_registry
from maa_api.db.models import AgentMessage, AgentRun, CompactionCheckpoint
from maa_api.domain.enums import CallerType
from tests.fakes.fake_llm import FakeLLM, text_response, tool_call, tool_response
from tests.fakes.v3_db import make_session_factory, seed_run, seed_session


def _profile() -> SimpleNamespace:
    return SimpleNamespace(key="test", dynamic_context_position="tail_system")


def _spec(name: str, **updates: Any) -> Any:
    return builtin_spec(name).model_copy(update=updates)


def _tool_definitions() -> Any:
    return build_registry()


def _agent_kinds() -> dict[str, str]:
    return {spec.name: spec.kind for spec in builtin_specs().values()}


def _registry(factory: Any, *, enabled: bool = True) -> SkillRegistry:
    settings = SimpleNamespace(skills=SimpleNamespace(enabled=enabled))
    return SkillRegistry(
        factory,
        tool_names={definition.name for definition in _tool_definitions().list()},
        agent_kinds=_agent_kinds(),
        settings_provider=lambda: settings,
    )


def _app(
    factory: Any,
    *,
    runtime: Any = None,
    context_layout: Any = None,
) -> SimpleNamespace:
    state = SimpleNamespace(
        session_factory=factory,
        confirmation_service=SimpleNamespace(registry=_tool_definitions()),
    )
    if runtime is not None:
        state.agent_runtime = runtime
    if context_layout is not None:
        state.context_layout = context_layout
    return SimpleNamespace(state=state)


def _context(
    spec: Any,
    *,
    app: Any,
    run_id: str,
    session_id: str,
) -> RunContext:
    return RunContext(
        run_id=run_id,
        root_run_id=run_id,
        parent_run_id=None,
        session_id=session_id,
        ticket_id=None,
        spec=spec,
        app=app,
        request=SimpleNamespace(app=app),
        state={},
    )


def _request(spec: Any) -> ModelRequest:
    return ModelRequest(
        messages=[],
        tools=[],
        profile=_profile(),
        meta={
            "layout_parts": ContextParts(
                system=[Segment("spec", spec.body)],
                tools=[],
                position="tail_system",
            )
        },
    )


def _system_text(request: ModelRequest) -> str:
    assert request.messages
    content = request.messages[0]["content"]
    assert isinstance(content, str)
    return content


async def _seed_message(factory: Any, session_id: str, *, seq: int) -> int:
    """一条已落库的 tool 消息，返回它的主键（``outcome.message_id`` 用）。"""
    async with factory() as db:
        message = AgentMessage(
            session_id=session_id,
            seq=seq,
            role="tool",
            content="{}",
            tool_name="load_skill",
        )
        db.add(message)
        await db.commit()
        return int(message.id)


async def _record_load(
    middleware: SkillsMiddleware,
    ctx: RunContext,
    factory: Any,
    *,
    skill_id: str,
    seq: int,
) -> ToolOutcome:
    """直接调用 ``wrap_tool``，模拟一次成功的 ``load_skill``。"""
    loaded = await middleware.registry.load(
        skill_id, agent_name=ctx.spec.name
    )
    message_id = await _seed_message(factory, ctx.session_id or "", seq=seq)

    async def call_next(_call: ToolCall) -> ToolOutcome:
        return ToolOutcome(
            status="ok", result=loaded.model_dump(), message_id=message_id
        )

    return await middleware.wrap_tool(
        ctx,
        ToolCall(
            id=f"call-{skill_id}",
            name="load_skill",
            raw_arguments={"skill_id": skill_id},
            arguments={"skill_id": skill_id},
        ),
        call_next,
    )


# ---------------------------------------------------------------------------
# 1. 索引只列当前代理可用的技能
# ---------------------------------------------------------------------------
def test_index_section_lists_available_skills(tmp_path) -> None:
    async def scenario() -> None:
        engine, factory = await make_session_factory(tmp_path / "skills-index.db")
        try:
            session_id = await seed_session(factory, kind="worker")
            spec = _spec("daily_ops")
            app = _app(factory)
            ctx = _context(spec, app=app, run_id="run-index", session_id=session_id)
            middleware = SkillsMiddleware(_registry(factory), session_factory=factory)

            await middleware.on_run_start(ctx)
            request = _request(spec)
            await middleware.before_model(ctx, request)
            text = _system_text(request)

            assert "- playbook-recruit｜公开招募实操：" in text
            assert "- task-preconditions｜任务执行前提与游戏状态：" in text
            # planner 专属的技能不出现在 daily_ops 的索引里。
            assert "- playbook-promotion-planning" not in text
            assert [segment.name for segment in parts_of(request).system] == [
                "spec",
                "skills",
            ]
        finally:
            engine.sync_engine.dispose()

    asyncio.run(scenario())


# ---------------------------------------------------------------------------
# 2. 预加载正文进第 4 段
# ---------------------------------------------------------------------------
def test_preloaded_body_is_injected_for_daily_ops(tmp_path) -> None:
    async def scenario() -> None:
        engine, factory = await make_session_factory(tmp_path / "skills-preload.db")
        try:
            session_id = await seed_session(factory, kind="worker")
            spec = _spec("daily_ops", skills=("task-preconditions",))
            app = _app(factory)
            ctx = _context(spec, app=app, run_id="run-preload", session_id=session_id)
            middleware = SkillsMiddleware(_registry(factory), session_factory=factory)

            await middleware.on_run_start(ctx)
            request = _request(spec)
            await middleware.before_model(ctx, request)
            text = _system_text(request)

            assert PRELOADED_HEADER in text
            assert "### 任务执行前提与游戏状态（task-preconditions）" in text
            assert "## 先看这一条" in text
            assert builtin_skills()["task-preconditions"].body[:40] in text
        finally:
            engine.sync_engine.dispose()

    asyncio.run(scenario())


# ---------------------------------------------------------------------------
# 3. 加载记录激活，下一轮继承
# ---------------------------------------------------------------------------
def test_load_skill_records_activation_and_carries_over(tmp_path) -> None:
    async def scenario() -> None:
        engine, factory = await make_session_factory(tmp_path / "skills-carry.db")
        try:
            session_id = await seed_session(factory, kind="worker")
            spec = _spec("daily_ops")
            app = _app(factory)
            first_id = await seed_run(factory, session_id=session_id, status="succeeded")
            first = _context(
                spec, app=app, run_id=first_id, session_id=session_id
            )
            middleware = SkillsMiddleware(_registry(factory), session_factory=factory)

            await middleware.on_run_start(first)
            outcome = await _record_load(
                middleware, first, factory, skill_id="playbook-recruit", seq=3
            )
            assert outcome.result["id"] == "playbook-recruit"
            assert first.state[SkillsMiddleware.STATE_KEY] == [
                {
                    "id": "playbook-recruit",
                    "version": outcome.result["version"],
                    "loaded_seq": 3,
                }
            ]
            await middleware.on_run_end(first, None)  # type: ignore[arg-type]

            async with factory() as db:
                stored = await db.get(AgentRun, first_id)
                assert stored is not None
                assert stored.meta is not None
                assert stored.meta["skills"][0]["id"] == "playbook-recruit"

            second_id = await seed_run(factory, session_id=session_id)
            async with factory() as db:
                second_row = await db.get(AgentRun, second_id)
                assert second_row is not None
                second_row.started_at = stored.started_at + timedelta(minutes=1)
                await db.commit()

            second = _context(
                spec, app=app, run_id=second_id, session_id=session_id
            )
            await middleware.on_run_start(second)
            active = second.state[SkillsMiddleware.STATE_KEY]
            assert [item["id"] for item in active] == ["playbook-recruit"]
            assert active[0]["loaded_seq"] == 3
        finally:
            engine.sync_engine.dispose()

    asyncio.run(scenario())


# ---------------------------------------------------------------------------
# 4. 压缩后补回正文
# ---------------------------------------------------------------------------
def test_compacted_load_is_restored_into_section(tmp_path) -> None:
    async def scenario() -> None:
        engine, factory = await make_session_factory(tmp_path / "skills-restore.db")
        try:
            session_id = await seed_session(factory, kind="worker")
            spec = _spec("daily_ops")
            app = _app(factory)
            first_id = await seed_run(factory, session_id=session_id, status="succeeded")
            first = _context(
                spec, app=app, run_id=first_id, session_id=session_id
            )
            middleware = SkillsMiddleware(_registry(factory), session_factory=factory)

            await middleware.on_run_start(first)
            await _record_load(
                middleware, first, factory, skill_id="playbook-recruit", seq=7
            )
            await middleware.on_run_end(first, None)  # type: ignore[arg-type]

            async with factory() as db:
                db.add(
                    CompactionCheckpoint(
                        session_id=session_id,
                        run_id=first_id,
                        upto_seq=7,
                        summary="摘要",
                        summary_json={},
                        durable={"skills": ["playbook-recruit"]},
                        tokens_before=100,
                        tokens_after=10,
                        trigger="auto",
                    )
                )
                await db.commit()
                first_row = await db.get(AgentRun, first_id)
                assert first_row is not None
                first_started = first_row.started_at

            second_id = await seed_run(factory, session_id=session_id)
            async with factory() as db:
                second_row = await db.get(AgentRun, second_id)
                assert second_row is not None
                second_row.started_at = first_started + timedelta(minutes=1)
                await db.commit()

            second = _context(
                spec, app=app, run_id=second_id, session_id=session_id
            )
            await middleware.on_run_start(second)
            request = _request(spec)
            await middleware.before_model(second, request)
            text = _system_text(request)

            assert RESTORED_HEADER in text
            assert "### 公开招募实操（playbook-recruit）" in text
            assert builtin_skills()["playbook-recruit"].body[:40] in text
        finally:
            engine.sync_engine.dispose()

    asyncio.run(scenario())


# ---------------------------------------------------------------------------
# 5. 同一轮第二次加载 already_loaded
# ---------------------------------------------------------------------------
def test_second_load_returns_already_loaded(tmp_path) -> None:
    async def scenario() -> None:
        engine, factory = await make_session_factory(tmp_path / "skills-second.db")
        try:
            session_id = await seed_session(factory, kind="worker")
            spec = _spec("daily_ops")
            app = _app(factory)
            run_id = await seed_run(factory, session_id=session_id)
            ctx = _context(spec, app=app, run_id=run_id, session_id=session_id)
            middleware = SkillsMiddleware(_registry(factory), session_factory=factory)
            await middleware.on_run_start(ctx)

            loaded = await middleware.registry.load(
                "playbook-recruit", agent_name="daily_ops"
            )
            calls = 0

            async def call_next(_call: ToolCall) -> ToolOutcome:
                nonlocal calls
                calls += 1
                # 每次都新建一份结果：第一次改完不影响第二次的输入。
                return ToolOutcome(status="ok", result=loaded.model_dump())

            call = ToolCall(
                id="call-1",
                name="load_skill",
                raw_arguments={"skill_id": "playbook-recruit"},
                arguments={"skill_id": "playbook-recruit"},
            )
            first = await middleware.wrap_tool(ctx, call, call_next)
            second = await middleware.wrap_tool(ctx, call, call_next)

            assert calls == 2  # 审计照常两条，只是第二次结果改成提示
            assert first.result["id"] == "playbook-recruit"
            assert "already_loaded" not in first.result
            assert second.result == {
                "id": "playbook-recruit",
                "already_loaded": True,
                "note": ALREADY_LOADED_NOTE,
            }
            active = ctx.state[SkillsMiddleware.STATE_KEY]
            assert [item["id"] for item in active] == ["playbook-recruit"]
            assert active[0]["loaded_seq"] == DEFAULT_LOADED_SEQ
        finally:
            engine.sync_engine.dispose()

    asyncio.run(scenario())


# ---------------------------------------------------------------------------
# 6. general 加载后自动开启 account 组
# ---------------------------------------------------------------------------
class _FakeRuntime:
    """只实现技能中间件用到的两个开组方法。"""

    def __init__(self) -> None:
        self.enabled: set[str] = set()
        self.calls: list[tuple[str, set[str]]] = []

    def enabled_tool_groups(self, session_id: str) -> set[str]:
        del session_id
        return set(self.enabled)

    def enable_tool_groups(self, session_id: str, groups: Any) -> set[str]:
        del session_id
        self.calls.append(("enable", set(groups)))
        self.enabled |= {str(group) for group in groups}
        return set(self.enabled)


def test_general_enables_deferred_group(tmp_path) -> None:
    async def scenario() -> None:
        engine, factory = await make_session_factory(tmp_path / "skills-group.db")
        try:
            session_id = await seed_session(factory, kind="worker")
            spec = _spec("general")
            runtime = _FakeRuntime()
            app = _app(factory, runtime=runtime)
            run_id = await seed_run(factory, session_id=session_id)
            ctx = _context(spec, app=app, run_id=run_id, session_id=session_id)
            middleware = SkillsMiddleware(_registry(factory), session_factory=factory)
            await middleware.on_run_start(ctx)

            outcome = await _record_load(
                middleware,
                ctx,
                factory,
                skill_id="playbook-promotion-planning",
                seq=4,
            )
            result = outcome.result

            assert "get_depot" in result["tools_available"]
            assert result["enabled_groups"] == ["account"]
            assert runtime.enabled == {"account"}
            # 工具集固定的代理声明不变：技能不往 spec.tools 里加东西。
            assert set(spec.tools) == set(builtin_spec("general").tools)
        finally:
            engine.sync_engine.dispose()

    asyncio.run(scenario())


# ---------------------------------------------------------------------------
# 7. 固定工具集：缺的工具列 unavailable，不自动开组
# ---------------------------------------------------------------------------
def test_fixed_toolset_reports_unavailable_tools(tmp_path) -> None:
    async def scenario() -> None:
        engine, factory = await make_session_factory(tmp_path / "skills-fixed.db")
        try:
            session_id = await seed_session(factory, kind="worker")
            spec = _spec("planner")
            runtime = _FakeRuntime()
            app = _app(factory, runtime=runtime)
            run_id = await seed_run(factory, session_id=session_id)
            ctx = _context(spec, app=app, run_id=run_id, session_id=session_id)
            middleware = SkillsMiddleware(_registry(factory), session_factory=factory)
            await middleware.on_run_start(ctx)

            outcome = await _record_load(
                middleware,
                ctx,
                factory,
                skill_id="playbook-infrast-shift",
                seq=5,
            )
            result = outcome.result

            assert "submit_pipeline" in result["tools_unavailable"]
            assert "submit_pipeline" not in result["tools_available"]
            assert "set_infrast_plan" in result["tools_available"]
            assert result["enabled_groups"] == []
            assert runtime.enabled == set()
            assert set(spec.tools) == set(builtin_spec("planner").tools)
        finally:
            engine.sync_engine.dispose()

    asyncio.run(scenario())


# ---------------------------------------------------------------------------
# 8. 设置关闭：没有第 4 段
# ---------------------------------------------------------------------------
def test_skills_disabled_injects_nothing(tmp_path) -> None:
    async def scenario() -> None:
        engine, factory = await make_session_factory(tmp_path / "skills-off.db")
        try:
            session_id = await seed_session(factory, kind="worker")
            spec = _spec("daily_ops", skills=("task-preconditions",))
            app = _app(factory)
            ctx = _context(spec, app=app, run_id="run-off", session_id=session_id)
            middleware = SkillsMiddleware(
                _registry(factory, enabled=False), session_factory=factory
            )

            await middleware.on_run_start(ctx)
            assert ctx.state[SkillsMiddleware.STATE_KEY] == []
            request = _request(spec)
            await middleware.before_model(ctx, request)

            assert request.messages == []
            assert [segment.name for segment in parts_of(request).system] == ["spec"]
        finally:
            engine.sync_engine.dispose()

    asyncio.run(scenario())


# ---------------------------------------------------------------------------
# 9. durable 带上激活技能
# ---------------------------------------------------------------------------
def test_durable_context_includes_active_skills(tmp_path) -> None:
    async def scenario() -> None:
        engine, factory = await make_session_factory(tmp_path / "skills-durable.db")
        try:
            session_id = await seed_session(factory, kind="worker")
            spec = _spec("daily_ops")
            app = _app(factory)
            run_id = await seed_run(factory, session_id=session_id)
            ctx = _context(spec, app=app, run_id=run_id, session_id=session_id)
            middleware = SkillsMiddleware(_registry(factory), session_factory=factory)
            await middleware.on_run_start(ctx)
            await _record_load(
                middleware, ctx, factory, skill_id="playbook-recruit", seq=2
            )

            await DurableContextMiddleware().on_run_start(ctx)
            durable = ctx.state["durable"]
            assert durable.skills == ["playbook-recruit"]
        finally:
            engine.sync_engine.dispose()

    asyncio.run(scenario())


# ---------------------------------------------------------------------------
# 补充：FakeLLM 驱动的真实循环（加载 → 下一次请求 → 收尾写 meta）
# ---------------------------------------------------------------------------
class _ExecuteTools(Middleware):
    """链上的执行器：真的走工具注册表，其余中间件照常包在外层。"""

    name = "test_execute"

    def __init__(self, registry: ToolRegistry, app: Any) -> None:
        self._registry = registry
        self._app = app

    async def wrap_tool(self, ctx, call, call_next) -> ToolOutcome:
        del call_next
        result = await self._registry.execute(
            call.name,
            call.arguments or {},
            ToolContext(
                caller=CallerType.INTERNAL,
                session_id=ctx.session_id,
                request_id=None,
                request=SimpleNamespace(app=self._app),
                db_session=None,
            ),
        )
        return ToolOutcome(status="ok", result=result)


def test_loop_load_skill_feeds_the_next_request_and_persists_meta(
    tmp_path,
) -> None:
    async def scenario() -> None:
        engine, factory = await make_session_factory(tmp_path / "skills-loop.db")
        try:
            session_id = await seed_session(factory, kind="worker")
            spec = _spec("daily_ops")
            registry = _registry(factory)
            app = _app(factory)
            app.state.skill_registry = registry
            run_id = await seed_run(factory, session_id=session_id)
            ctx = _context(
                spec, app=app, run_id=run_id, session_id=session_id
            )
            ctx.state["model"] = {"profile": _profile()}
            ctx.request = SimpleNamespace(app=app)
            llm = FakeLLM(
                [
                    tool_response(
                        tool_call(
                            "call-skill",
                            "load_skill",
                            {"skill_id": "playbook-recruit"},
                        )
                    ),
                    text_response("按技能做完了"),
                ]
            )

            async def call_model(run_ctx: RunContext, req: ModelRequest):
                from maa_api.agent.harness.chunks import collect_completion

                return await collect_completion(
                    llm, "test", req.messages, req.tools, emit=run_ctx.emit
                )

            tool_registry = _tool_definitions()
            middleware = SkillsMiddleware(registry, session_factory=factory)
            from maa_api.agent.harness.builtin_middleware import (
                ContextLayoutMiddleware,
            )

            transcript = MemoryTranscript()
            loop = AgentLoop(
                chain=MiddlewareChain(
                    [
                        ContextLayoutMiddleware(),
                        middleware,
                        _ExecuteTools(tool_registry, app),
                    ]
                ),
                transcript=transcript,
                call_model=call_model,
                tool_schemas=lambda _ctx: [],
                registry=tool_registry,
            )
            outcome = await loop.run(ctx)
            assert outcome.final_text == "按技能做完了"

            async with factory() as db:
                run = await db.get(AgentRun, run_id)
                assert run is not None and run.meta is not None
                assert run.meta["skills"][0]["id"] == "playbook-recruit"

            tool_payloads = [
                message["content"]
                for message in transcript.messages
                if message.get("role") == "tool"
            ]
            assert tool_payloads
            assert '"tools_available"' in tool_payloads[0]
            assert "## 先问清需求" in tool_payloads[0]
        finally:
            engine.sync_engine.dispose()

    asyncio.run(scenario())
