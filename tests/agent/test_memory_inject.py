"""M79 T3：记忆注入中间件（spec §4.5、§6、07 §5.4）。

覆盖 spec §6 的三条：``test_preference_first_within_token_budget``、
``test_same_ticket_reuses_selection``、``test_proposed_not_injected``；并补预算上限、
过期扫描、用量刷新、span/结构化日志、总控/Worker 作用域、压缩后失效与记忆增删改
重选。

异步用例沿用仓库约定：同步测试函数 + ``asyncio.run(scenario())``。
"""

from __future__ import annotations

import asyncio
import logging
from datetime import datetime, timedelta
from types import SimpleNamespace
from typing import Any
from uuid import uuid4

from maa_api.agent.context.layout import ContextLayout, ContextParts, Segment
from maa_api.agent.context.tokens import TokenEstimator, estimate_text
from maa_api.agent.harness.context import ModelRequest, RunContext
from maa_api.agent.memory.inject import (
    DEFAULT_MAX_INJECTION_TOKENS,
    MEMORY_HEADER,
    MemoryInjectionCache,
    MemoryInjectionMiddleware,
)
from maa_api.agent.memory.service import MemoryService
from maa_api.agent.specs.registry import builtin_spec
from maa_api.agent.trace import SpanHandle
from maa_api.db.models import (
    AgentMessage,
    AgentSession,
    CompactionCheckpoint,
    Mandate,
    MemoryItem,
    Ticket,
)
from maa_api.db.repositories.memory import MemoryRepository
from maa_api.domain.enums import MemoryKind, MemoryStatus, SpanKind
from tests.db.conftest import (  # noqa: F401  # pytest 从模块命名空间发现 fixture
    db_engine,
    db_path,
    db_session,
    db_session_factory,
)

NOW = datetime(2026, 11, 5, 4, 0, 0)


class _Settings:
    """``memory.*`` 与检索层的最小设置桩；G4 才注册 Settings 段。"""

    def __init__(
        self,
        *,
        budget: int | None = DEFAULT_MAX_INJECTION_TOKENS,
        enabled: bool = True,
        require_review: bool = False,
    ) -> None:
        memory_values: dict[str, Any] = {
            "enabled": enabled,
            "require_review": require_review,
        }
        if budget is not None:
            memory_values["max_injection_tokens"] = budget
        self.memory = SimpleNamespace(**memory_values)
        self.rag = SimpleNamespace(
            enabled=True,
            top_k=8,
            embedding_provider="none",
            chunk_chars=200,
            chunk_overlap=20,
        )


class _Retriever:
    """只返回给定记忆 id 的检索器桩；记录每次查询词与来源。"""

    def __init__(self, memory_ids: list[str] | None = None) -> None:
        self.memory_ids = list(memory_ids or [])
        self.calls: list[dict[str, Any]] = []

    async def search(
        self,
        query: str,
        *,
        sources: Any = None,
        top_k: int | None = None,
        caller: str = "retrieval_api",
    ) -> Any:
        del top_k, caller
        self.calls.append({"query": query, "sources": list(sources or ())})
        return SimpleNamespace(
            hits=[
                SimpleNamespace(document_id=f"memory:{memory_id}")
                for memory_id in self.memory_ids
            ]
        )


class _RecordingRecorder:
    """按 ``SpanRecorder`` 的形状记录 span；``start_span`` 返回真实 ``SpanHandle``。"""

    enabled = True

    def __init__(self) -> None:
        self.records: list[Any] = []

    def start_span(
        self,
        name: str,
        kind: Any,
        *,
        trace_id: str,
        run_id: str | None,
        parent: SpanHandle | None = None,
        attributes: dict[str, Any] | None = None,
    ) -> SpanHandle:
        return SpanHandle(
            self,
            trace_id=trace_id,
            span_id=uuid4().hex[:16],
            parent_span_id=parent.span_id if parent is not None else None,
            run_id=run_id,
            name=name,
            kind=kind,
            started_at=NOW,
            attributes=attributes,
        )

    def record(self, record: Any) -> None:
        self.records.append(record)


def _spec(name: str = "daily_ops") -> Any:
    return builtin_spec(name)


def _make_service(
    factory: Any,
    settings: _Settings,
    *,
    clock: Any = lambda: NOW,
) -> MemoryService:
    return MemoryService(
        factory,
        settings_provider=lambda: settings,
        clock=clock,
    )


def _app(
    *,
    service: MemoryService,
    retriever: Any = None,
    recorder: Any = None,
    cache: MemoryInjectionCache | None = None,
    factory: Any = None,
) -> Any:
    state = SimpleNamespace(
        memory_service=service,
        retriever=retriever,
        span_recorder=recorder,
        memory_injection_cache=cache,
        context_layout=ContextLayout(TokenEstimator()),
        session_factory=factory,
    )
    return SimpleNamespace(state=state)


def _context(
    *,
    app: Any,
    spec_name: str = "daily_ops",
    session_id: str | None = None,
    ticket_id: str | None = None,
) -> RunContext:
    run_id = str(uuid4())
    return RunContext(
        run_id=run_id,
        root_run_id=run_id,
        parent_run_id=None,
        session_id=session_id,
        ticket_id=ticket_id,
        spec=_spec(spec_name),
        app=app,
        request=SimpleNamespace(app=app),
        state={},
    )


def _request() -> ModelRequest:
    return ModelRequest(
        messages=[],
        tools=[],
        profile=SimpleNamespace(key="test", dynamic_context_position="tail_system"),
        meta={
            "layout_parts": ContextParts(
                system=[Segment("spec", "代理定义正文")],
                tools=[],
                position="tail_system",
            )
        },
    )


def _section(req: ModelRequest) -> str:
    for segment in req.meta["layout_parts"].system:
        if segment.name == "memory":
            return segment.text
    return ""


async def _create(
    service: MemoryService,
    *,
    content: str,
    kind: str = "semantic",
    subject: str = "preference",
    confidence: float = 0.8,
) -> MemoryItem:
    memory = await service.create_candidate(
        kind=kind,
        subject=subject,
        content=content,
        confidence=confidence,
    )
    assert memory is not None
    return memory


async def _seed_worker_ticket(factory: Any) -> str:
    """Worker 查询词要读工单标题与委托范围。"""
    ticket_id = str(uuid4())
    async with factory() as session:
        session.add(
            Ticket(
                id=ticket_id,
                title="1-7 刷固源岩",
                kind="daily_ops",
                status="open",
                origin="user",
                created_at=NOW,
                updated_at=NOW,
            )
        )
        await session.flush()
        session.add(
            Mandate(
                ticket_id=ticket_id,
                scope="只刷 1-7",
                strength="explicit",
                source="user",
                risk_ceiling="none",
                created_at=NOW,
            )
        )
        await session.commit()
    return ticket_id


async def _get_memory(factory: Any, memory_id: str) -> MemoryItem | None:
    async with factory() as session:
        return await MemoryRepository(session).get(memory_id)


# ---------------------------------------------------------------------------
# §6 三条
# ---------------------------------------------------------------------------
def test_preference_first_within_token_budget(db_session_factory) -> None:
    """token ≤ 1500，preference 在；必选组 ≤40%，相关组来自 ``memory`` 来源。"""
    factory = db_session_factory

    async def scenario() -> None:
        settings = _Settings(budget=1500)
        service = _make_service(factory, settings)
        preference = await _create(
            service,
            content="不用源石恢复理智",
            subject="preference",
            confidence=0.99,
        )
        related: list[MemoryItem] = []
        for index in range(20):
            related.append(
                await _create(
                    service,
                    content=f"以前第 {index} 次刷 1-7 的做法：" + "先回主界面再提交" * 20,
                    kind="procedural",
                    subject=f"stage:1-{index}",
                    confidence=0.5,
                )
            )
        ticket_id = await _seed_worker_ticket(factory)
        retriever = _Retriever([memory.id for memory in related])
        middleware = MemoryInjectionMiddleware(session_factory=factory, settings_provider=lambda: settings)
        app = _app(service=service, retriever=retriever, cache=MemoryInjectionCache())
        ctx = _context(
            app=app,
            spec_name="daily_ops",
            session_id=str(uuid4()),
            ticket_id=ticket_id,
        )

        req = await middleware.before_model(ctx, _request())
        section = _section(req)
        assert section.startswith(MEMORY_HEADER)
        assert "不用源石恢复理智" in section
        assert "事实｜2026-11-05｜" in section
        # 预算按 M67 估算：整段（含固定开头）不超过 1500。
        assert estimate_text(section) <= 1500
        # 必选组只有 preference，按 40% 预算（600）单独核算也不超。
        preference_line = next(
            line for line in section.splitlines() if "不用源石恢复理智" in line
        )
        assert estimate_text(preference_line) <= 600
        # 相关组只查 ``memory`` 来源，查询词含工单标题与委托。
        assert retriever.calls and retriever.calls[0]["sources"] == ["memory"]
        assert "1-7 刷固源岩" in retriever.calls[0]["query"]
        assert "只刷 1-7" in retriever.calls[0]["query"]
        # preference 排在相关组之前。
        first_related = next(
            (
                line
                for line in section.splitlines()
                if "以前第" in line
            ),
            None,
        )
        assert first_related is not None
        assert section.index("不用源石恢复理智") < section.index(first_related)
        assert section.splitlines()[0] == MEMORY_HEADER
        assert "不用源石恢复理智" in section.splitlines()[1]

    asyncio.run(scenario())


def test_same_ticket_reuses_selection(db_session_factory) -> None:
    """同一工单第二次 wrap 字节相同，且不再检索、不再刷用量。"""
    factory = db_session_factory

    async def scenario() -> None:
        settings = _Settings()
        service = _make_service(factory, settings)
        memory = await _create(service, content="偏好手动换班")
        other = await _create(
            service, content="不相关的一条", kind="semantic", subject="device"
        )
        ticket_id = await _seed_worker_ticket(factory)
        retriever = _Retriever([memory.id])
        cache = MemoryInjectionCache()
        middleware = MemoryInjectionMiddleware(
            session_factory=factory, settings_provider=lambda: settings, cache=cache
        )
        app = _app(service=service, retriever=retriever, cache=cache)
        session_id = str(uuid4())
        ctx = _context(
            app=app,
            spec_name="daily_ops",
            session_id=session_id,
            ticket_id=ticket_id,
        )

        first = _section(await middleware.before_model(ctx, _request()))
        # 第二次的检索会给出一条新结果；缓存命中时不该被采用。
        retriever.memory_ids = [other.id, memory.id]
        second = _section(await middleware.before_model(ctx, _request()))
        assert first == second
        assert len(retriever.calls) == 1
        stored = await _get_memory(factory, memory.id)
        assert stored is not None and stored.use_count == 1

    asyncio.run(scenario())


def test_proposed_not_injected(db_session_factory) -> None:
    """正文不含候选（``proposed``），只注入审核生效的记忆。"""
    factory = db_session_factory

    async def scenario() -> None:
        settings = _Settings(require_review=True)
        service = _make_service(factory, settings)
        proposed = await _create(
            service, content="候选偏好：不要刷 1-7", confidence=0.9
        )
        approved = await _create(
            service, content="已审核偏好：先清体力", confidence=0.9
        )
        await service.review(approved.id, True)
        ticket_id = await _seed_worker_ticket(factory)
        middleware = MemoryInjectionMiddleware(
            session_factory=factory,
            settings_provider=lambda: settings,
            cache=MemoryInjectionCache(),
        )
        app = _app(
            service=service,
            retriever=_Retriever([proposed.id, approved.id]),
            cache=MemoryInjectionCache(),
        )
        ctx = _context(
            app=app,
            spec_name="daily_ops",
            session_id=str(uuid4()),
            ticket_id=ticket_id,
        )
        section = _section(await middleware.before_model(ctx, _request()))
        assert "已审核偏好：先清体力" in section
        assert "不要刷 1-7" not in section
        stored = await _get_memory(factory, proposed.id)
        assert stored is not None
        assert stored.status == MemoryStatus.PROPOSED.value

    asyncio.run(scenario())


# ---------------------------------------------------------------------------
# 补充：预算、过期、用量、作用域、日志与 span
# ---------------------------------------------------------------------------
def test_orchestrator_query_uses_checkpoint_and_recent_users(
    db_session_factory,
) -> None:
    """总控的检索词 = 检查点摘要 + 最近三条用户消息，旧消息不进。"""
    factory = db_session_factory

    async def scenario() -> None:
        settings = _Settings()
        service = _make_service(factory, settings)
        memory = await _create(service, content="偏好先跑剿灭")
        session_id = str(uuid4())
        async with factory() as session:
            session.add(
                AgentSession(
                    id=session_id,
                    kind="orchestrator",
                    title="总控会话",
                    model="test-model",
                    created_at=NOW,
                    updated_at=NOW,
                )
            )
            await session.flush()
            session.add(
                CompactionCheckpoint(
                    id=str(uuid4()),
                    session_id=session_id,
                    upto_seq=0,
                    summary="旧检查点摘要：刷过 1-7",
                    summary_json={"schema_version": 1},
                    durable={},
                    tokens_before=1000,
                    tokens_after=100,
                    trigger="auto",
                    created_at=NOW,
                )
            )
            for seq, text in enumerate(
                ["第一条旧消息", "第二条消息", "第三条消息", "第四条消息"]
            ):
                session.add(
                    AgentMessage(
                        session_id=session_id,
                        seq=seq,
                        role="user",
                        content=text,
                        created_at=NOW,
                    )
                )
            await session.commit()

        retriever = _Retriever([memory.id])
        middleware = MemoryInjectionMiddleware(
            session_factory=factory,
            settings_provider=lambda: settings,
            cache=MemoryInjectionCache(),
        )
        app = _app(service=service, retriever=retriever, cache=MemoryInjectionCache())
        ctx = _context(app=app, spec_name="orchestrator", session_id=session_id)
        await middleware.before_model(ctx, _request())
        query = retriever.calls[0]["query"]
        assert "旧检查点摘要" in query
        assert "第二条消息" in query
        assert "第三条消息" in query
        assert "第四条消息" in query
        assert "第一条旧消息" not in query

    asyncio.run(scenario())


def test_zero_budget_injects_nothing(db_session_factory) -> None:
    """``max_injection_tokens=0`` 时第 4 段不存在，也不检索。"""
    factory = db_session_factory

    async def scenario() -> None:
        settings = _Settings(budget=0)
        service = _make_service(factory, settings)
        await _create(service, content="有记忆但不注入")
        retriever = _Retriever()
        middleware = MemoryInjectionMiddleware(session_factory=factory, settings_provider=lambda: settings)
        app = _app(service=service, retriever=retriever, cache=MemoryInjectionCache())
        ctx = _context(app=app, session_id=str(uuid4()))
        req = await middleware.before_model(ctx, _request())
        assert _section(req) == ""
        assert retriever.calls == []

    asyncio.run(scenario())


def test_expired_memory_is_scanned_out(db_session_factory) -> None:
    """到期的 ``episodic`` 在选择前改 ``expired``，不出现在第 4 段。"""
    factory = db_session_factory

    async def scenario() -> None:
        settings = _Settings()
        service = _make_service(factory, settings)
        memory = await _create(
            service, content="2026-10-01 的一次失败", kind="episodic"
        )
        async with factory() as session:
            stored = await MemoryRepository(session).get(memory.id)
            assert stored is not None
            stored.expires_at = NOW - timedelta(days=1)
            await session.commit()

        middleware = MemoryInjectionMiddleware(
            session_factory=factory,
            settings_provider=lambda: settings,
            cache=MemoryInjectionCache(),
        )
        app = _app(
            service=service,
            retriever=_Retriever([memory.id]),
            cache=MemoryInjectionCache(),
        )
        ctx = _context(app=app, session_id=str(uuid4()))
        section = _section(await middleware.before_model(ctx, _request()))
        assert "一次失败" not in section
        stored = await _get_memory(factory, memory.id)
        assert stored is not None
        assert stored.status == MemoryStatus.EXPIRED.value

    asyncio.run(scenario())


def test_record_use_refreshes_usage_on_reselect(db_session_factory) -> None:
    """被注入（重新选择）的行刷 ``last_used_at``、``use_count`` 加一。"""
    factory = db_session_factory

    async def scenario() -> None:
        settings = _Settings()
        service = _make_service(factory, settings)
        memory = await _create(service, content="偏好先跑剿灭")
        middleware = MemoryInjectionMiddleware(
            session_factory=factory,
            settings_provider=lambda: settings,
            cache=MemoryInjectionCache(),
        )
        app = _app(
            service=service,
            retriever=_Retriever([memory.id]),
            cache=MemoryInjectionCache(),
        )
        ctx = _context(app=app, session_id=str(uuid4()))
        await middleware.before_model(ctx, _request())
        stored = await _get_memory(factory, memory.id)
        assert stored is not None
        assert stored.use_count == 1
        assert stored.last_used_at == NOW

    asyncio.run(scenario())


def test_memory_change_reselects(db_session_factory) -> None:
    """生效记忆有增删改时缓存失效、重新选择。"""
    factory = db_session_factory

    async def scenario() -> None:
        settings = _Settings()
        service = _make_service(factory, settings)
        first = await _create(service, content="偏好一")
        ticket_id = await _seed_worker_ticket(factory)
        retriever = _Retriever([first.id])
        cache = MemoryInjectionCache()
        middleware = MemoryInjectionMiddleware(
            session_factory=factory, settings_provider=lambda: settings, cache=cache
        )
        app = _app(service=service, retriever=retriever, cache=cache)
        ctx = _context(
            app=app,
            spec_name="daily_ops",
            session_id=str(uuid4()),
            ticket_id=ticket_id,
        )
        section = _section(await middleware.before_model(ctx, _request()))
        assert "偏好一" in section

        second = await _create(service, content="偏好二", subject="account")
        retriever.memory_ids = [first.id, second.id]
        section = _section(await middleware.before_model(ctx, _request()))
        assert "偏好二" in section
        assert len(retriever.calls) == 2

    asyncio.run(scenario())


def test_compaction_invalidation_reselects(db_session_factory) -> None:
    """压缩后 ``Compactor`` 让缓存失效，下一轮重新选择。"""
    factory = db_session_factory

    async def scenario() -> None:
        settings = _Settings()
        service = _make_service(factory, settings)
        memory = await _create(service, content="偏好一")
        ticket_id = await _seed_worker_ticket(factory)
        retriever = _Retriever([memory.id])
        cache = MemoryInjectionCache()
        middleware = MemoryInjectionMiddleware(
            session_factory=factory, settings_provider=lambda: settings, cache=cache
        )
        app = _app(service=service, retriever=retriever, cache=cache)
        session_id = str(uuid4())
        ctx = _context(
            app=app,
            spec_name="daily_ops",
            session_id=session_id,
            ticket_id=ticket_id,
        )
        await middleware.before_model(ctx, _request())
        cache.invalidate(session_id)
        retriever.memory_ids = []
        section = _section(await middleware.before_model(ctx, _request()))
        assert len(retriever.calls) == 2
        assert "偏好一" in section  # 必选组仍由 active 记忆给出

    asyncio.run(scenario())


def test_span_and_structured_log(db_session_factory, caplog) -> None:
    """记 ``memory.inject`` span（items/tokens）与结构化日志（暂无指标设施）。"""
    factory = db_session_factory

    async def scenario() -> None:
        settings = _Settings()
        service = _make_service(factory, settings)
        await _create(service, content="偏好先跑剿灭", confidence=0.9)
        recorder = _RecordingRecorder()
        middleware = MemoryInjectionMiddleware(
            session_factory=factory,
            settings_provider=lambda: settings,
            cache=MemoryInjectionCache(),
        )
        app = _app(
            service=service,
            retriever=_Retriever(),
            recorder=recorder,
            cache=MemoryInjectionCache(),
        )
        ctx = _context(app=app, session_id=str(uuid4()))
        with caplog.at_level(logging.INFO, logger="maa_api.agent.memory.inject"):
            await middleware.before_model(ctx, _request())
        assert recorder.records
        record = recorder.records[0]
        assert record.name == "memory.inject"
        assert record.kind == SpanKind.MEMORY
        assert record.attributes["maa.memory.items"] == 1
        assert record.attributes["maa.memory.tokens"] > 0
        assert "memory.inject items=1" in caplog.text
        event = next(
            record for record in caplog.records if getattr(record, "event", "") == "memory.inject"
        )
        assert event.__dict__["maa.memory.items"] == 1
        assert event.__dict__["maa.memory.tokens"] > 0

    asyncio.run(scenario())


def test_scope_is_orchestrator_and_worker_only() -> None:
    """只作用于总控与 Worker；名字固定为链序断言用。"""
    assert MemoryInjectionMiddleware.name == "memory_inject"
    assert MemoryInjectionMiddleware.applies_to == frozenset({"orchestrator", "worker"})
