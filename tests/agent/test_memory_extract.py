"""M79 T2：记忆抽取、curator 定义与 remember 工具（spec §4.2、§4.5、§6、§7）。

覆盖 spec §6 的四条：

- ``test_ticket_done_proposes``：工单 ``done`` 后后台抽取，候选进 ``proposed``；
- ``test_cancelled_ticket_also_extracts``：``cancelled`` 也抽；
- ``test_duplicate_subject_skipped``：同 kind/subject/规范化内容只留一条，超 500 字
  与不在白名单里的 ``supersedes`` 按规则丢弃；
- ``test_bad_schema_does_not_fail_ticket``：两次非法 JSON 后零写入、工单仍是 ``done``。

补充：后台任务失败不改终态、压缩检查点后抽取被压缩段、``remember`` 只提议与
``memory.enabled=false`` 的工具错误。模型全部用 FakeLLM / 假路由替身，不连真实模型。
"""

from __future__ import annotations

import asyncio
import json
from datetime import datetime
from types import SimpleNamespace
from typing import Any

from sqlmodel import select

from maa_api.agent.context.compaction import Compactor
from maa_api.agent.context.durable import DurableState
from maa_api.agent.llm.profiles import ResolvedProfile
from maa_api.agent.memory import MemoryService
from maa_api.agent.memory.extract import extract_candidates
from maa_api.agent.registry import ToolContext, ToolOrigin
from maa_api.agent.runs.service import RunService
from maa_api.agent.specs.registry import AgentSpecRegistry, builtin_spec
from maa_api.agent.tickets.inbox import InboxService
from maa_api.agent.tickets.reports import ReportInput
from maa_api.agent.tickets.service import TicketService
from maa_api.agent.tickets.threads import ThreadRouter
from maa_api.agent.tools import build_registry
from maa_api.agent.tools.memory import REMEMBER_DESCRIPTION
from maa_api.db.models import AgentMessage, AgentRun, Ticket
from maa_api.db.repositories.agent import AgentMessageRepository
from maa_api.db.repositories.memory import MemoryRepository
from maa_api.db.repositories.ticket import TicketRepository
from maa_api.domain.enums import (
    AgentRunKind,
    CallerType,
    EventSource,
    MemoryStatus,
    ReportStatus,
    TicketOrigin,
    TicketStatus,
)
from maa_api.domain.errors import AppError, ErrorCode
from tests.db.conftest import (  # noqa: F401  # pytest 从模块命名空间发现 fixture
    db_engine,
    db_path,
    db_session,
    db_session_factory,
)
from tests.fakes.fake_llm import FakeLLM, text_response
from tests.fakes.v3_db import seed_run, seed_session

NOW = datetime(2026, 11, 5, 4, 0, 0)


# ---------------------------------------------------------------------------
# 桩：设置、模型路由、账本、记录器
# ---------------------------------------------------------------------------
class _Settings:
    """``memory.*`` 的最小设置桩；``auto_extract`` 只被抽取读取。"""

    def __init__(
        self,
        *,
        enabled: bool = True,
        auto_extract: bool = True,
        require_review: bool = True,
    ) -> None:
        self.memory = SimpleNamespace(
            enabled=enabled,
            auto_extract=auto_extract,
            require_review=require_review,
        )


class _Recorder:
    """记录广播与索引写入；本用例不连真实索引。"""

    def __init__(self) -> None:
        self.events: list[tuple[str, dict[str, Any]]] = []
        self.documents: list[Any] = []

    def broadcast(self, message_type: str, data: dict[str, Any]) -> None:
        self.events.append((message_type, data))

    def write_index(self, documents: list[Any]) -> None:
        self.documents.extend(documents)


_PROFILE = ResolvedProfile(
    name="memory",
    kind="chat",
    provider="openai_compatible",
    base_url="https://llm.test/v1",
    api_key="sk-test",
    model="memory-model",
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
    """所有角色都用同一个 FakeLLM；``role`` 记录在这里供断言。"""

    def __init__(self, llm: FakeLLM) -> None:
        self.llm = llm
        self.roles: list[str] = []

    async def resolve(self, role: str) -> ResolvedProfile:
        self.roles.append(role)
        return _PROFILE

    def client_for(self, _profile: Any) -> _Handle:
        return _Handle(self.llm)


class _Ledger:
    """``complete_structured`` 只需要记账与两个预算查询。"""

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


class _Knowledge:
    """用户档案全文的替身：真实实现是 ``KnowledgeService.read_for_agent``。"""

    def __init__(self, body: str | None) -> None:
        self.body = body
        self.reads: list[str] = []

    async def read_for_agent(self, entry_id: str) -> Any:
        self.reads.append(entry_id)
        if self.body is None:
            raise AppError(ErrorCode.KNOWLEDGE_NOT_FOUND, "没有这条知识")
        return SimpleNamespace(body=self.body)


def _proposals(*items: dict[str, Any]) -> list[dict[str, Any]]:
    return text_response(json.dumps({"items": list(items)}, ensure_ascii=False))


def _make_memory(
    factory: Any,
    *,
    settings: _Settings | None = None,
    recorder: _Recorder | None = None,
) -> tuple[MemoryService, _Settings, _Recorder]:
    holder = recorder or _Recorder()
    settings = settings or _Settings()
    service = MemoryService(
        factory,
        broadcast=holder.broadcast,
        index_writer=holder.write_index,
        settings_provider=lambda: settings,
        clock=lambda: NOW,
    )
    return service, settings, holder


async def _seed_messages(
    factory: Any, session_id: str, rows: list[tuple[str, str]]
) -> None:
    async with factory() as session:
        repository = AgentMessageRepository(session)
        for seq, (role, content) in enumerate(rows):
            await repository.create(
                AgentMessage(session_id=session_id, seq=seq, role=role, content=content)
            )
        await session.commit()


async def _memory_runs(factory: Any) -> list[AgentRun]:
    async with factory() as session:
        result = await session.execute(
            select(AgentRun)
            .where(AgentRun.kind == AgentRunKind.MEMORY_EXTRACT.value)
            .order_by(AgentRun.started_at.asc())
        )
        return list(result.scalars().all())


async def _all_runs(factory: Any) -> list[AgentRun]:
    async with factory() as session:
        result = await session.execute(select(AgentRun).order_by(AgentRun.started_at.asc()))
        return list(result.scalars().all())


def _take_tasks(owner: Any) -> list[asyncio.Task[Any]]:
    """取走 ``owner._memory_tasks`` 里的后台任务（调用方已在事件循环里）。"""
    return list(owner._memory_tasks)


async def _wait(tasks: list[asyncio.Task[Any]]) -> None:
    if tasks:
        await asyncio.gather(*tasks, return_exceptions=True)


class _TicketRig:
    """临时库上的真实工单服务 + MemoryService + 假模型。"""

    def __init__(
        self,
        *,
        factory: Any,
        settings: _Settings,
        memory: MemoryService,
        tickets: TicketService,
        run_service: RunService,
        llm: FakeLLM,
        router: _Router,
        ledger: _Ledger,
        registry: AgentSpecRegistry,
        knowledge: _Knowledge | None,
    ) -> None:
        self.factory = factory
        self.settings = settings
        self.memory = memory
        self.tickets = tickets
        self.run_service = run_service
        self.llm = llm
        self.router = router
        self.ledger = ledger
        self.registry = registry
        self.knowledge = knowledge

    async def close(self) -> None:
        await self.run_service.close()


async def _build_ticket_rig(
    factory: Any,
    llm: FakeLLM,
    *,
    settings: _Settings | None = None,
    knowledge: _Knowledge | None = None,
    failing: bool = False,
) -> _TicketRig:
    settings = settings or _Settings()
    memory, settings, _recorder = _make_memory(factory, settings=settings)
    registry = AgentSpecRegistry(build_registry())
    registry.load()
    inbox = InboxService(factory)
    thread = ThreadRouter(factory, inbox)
    tickets = TicketService(
        factory,
        inbox_service=inbox,
        thread_router=thread,
        spec_registry=registry,
    )
    router = _Router(llm)
    ledger = _Ledger()
    run_service = RunService(factory, boot_id="m79-t2")

    if failing:

        async def extractor(_ended: Any) -> None:
            raise RuntimeError("模拟抽取任务失败")

    else:

        async def extractor(ended: Any) -> None:
            await extract_candidates(
                memory,
                session_factory=factory,
                router=router,
                ledger=ledger,
                run_service=run_service,
                ticket_id=ended.ticket_id,
                knowledge_service=knowledge,
                spec_registry=registry,
                settings_provider=lambda: settings,
            )

    tickets.bind_memory_extractor(extractor)
    return _TicketRig(
        factory=factory,
        settings=settings,
        memory=memory,
        tickets=tickets,
        run_service=run_service,
        llm=llm,
        router=router,
        ledger=ledger,
        registry=registry,
        knowledge=knowledge,
    )


async def _create_ticket(rig: _TicketRig, *, title: str, scope: str) -> Any:
    orchestrator_id = await seed_session(rig.factory, kind="orchestrator")
    ticket, _mandate = await rig.tickets.create(
        kind="daily_ops",
        title=title,
        scope=scope,
        origin=TicketOrigin.SYSTEM,
        source=EventSource.SYSTEM,
        orchestrator_session_id=orchestrator_id,
    )
    return ticket, orchestrator_id


# ---------------------------------------------------------------------------
# §6 四条
# ---------------------------------------------------------------------------
def test_ticket_done_proposes(db_session_factory) -> None:
    """终态触发：一轮 memory_extract 运行、输入齐全、两条候选进 proposed。"""
    factory = db_session_factory

    async def scenario() -> None:
        old_memory, settings, _recorder = _make_memory(factory)
        old = await old_memory.create_candidate(
            kind="semantic", subject="preference", content="不用源石恢复理智"
        )
        assert old is not None
        await old_memory.review(old.id, True)

        llm = FakeLLM(
            [
                _proposals(
                    {
                        "kind": "semantic",
                        "subject": "preference",
                        "content": "刷图不要用源石恢复理智",
                        "confidence": 0.9,
                        "supersedes": old.id,
                    },
                    {
                        "kind": "episodic",
                        "subject": "stage:1-7",
                        "content": "2026-11-05 刷 1-7 拿到 50 个固源岩",
                        "confidence": 0.7,
                    },
                )
            ]
        )
        knowledge = _Knowledge("基建布局：243\n班次：每天两班")
        rig = await _build_ticket_rig(
            factory, llm, settings=settings, knowledge=knowledge
        )
        try:
            ticket, orchestrator_id = await _create_ticket(
                rig,
                title="刷 1-7 到 50 个固源岩",
                scope="用户要求刷 1-7，不要用源石恢复理智",
            )
            await _seed_messages(
                factory,
                orchestrator_id,
                [
                    ("user", "以后刷图别用源石恢复理智"),
                    ("assistant", "记住了，会用理智药"),
                ],
            )
            await rig.tickets.record_report(
                ticket.id,
                ReportInput(
                    status=ReportStatus.READY,
                    summary="刷完了 50 个固源岩，没有用源石",
                ),
            )
            tasks = _take_tasks(rig.tickets)
            assert len(tasks) == 1
            await _wait(tasks)

            # 工单仍是 done，候选是 proposed。
            async with factory() as session:
                stored_ticket = await TicketRepository(session).get(ticket.id)
            assert stored_ticket.status == TicketStatus.DONE.value
            page = await old_memory.list_memories(status=MemoryStatus.PROPOSED.value)
            assert page.total == 2

            # 取代白名单里的 id 保留，来源字段落在行上。
            replacement = next(
                item for item in page.items if item.subject == "preference"
            )
            assert replacement.supersedes_id == old.id
            assert replacement.source_ticket_id == ticket.id
            assert replacement.source_session_id == orchestrator_id
            assert replacement.source_run_id is not None
            episodic = next(item for item in page.items if item.kind == "episodic")
            assert episodic.confidence == 0.7

            # 输入：工单标题、委托、最终汇报、用户原话、回答、已生效记忆 id、档案全文。
            assert len(llm.requests) == 1
            request_messages = llm.requests[0][1]
            assert request_messages[0]["role"] == "system"
            assert request_messages[0]["content"] == builtin_spec(
                "memory_curator"
            ).body
            prompt = request_messages[1]["content"]
            assert "刷 1-7 到 50 个固源岩" in prompt
            assert "用户要求刷 1-7，不要用源石恢复理智" in prompt
            assert "刷完了 50 个固源岩，没有用源石" in prompt
            assert "以后刷图别用源石恢复理智" in prompt
            assert "记住了，会用理智药" in prompt
            assert old.id in prompt
            assert "基建布局：243" in prompt
            assert knowledge.reads == ["agent-profile"]

            # 运行：kind/agent/model_role 与 meta 目标会话。
            runs = await _memory_runs(factory)
            assert len(runs) == 1
            run = runs[0]
            assert run.agent_name == "memory_curator"
            assert run.status == "succeeded"
            assert run.session_id is None
            assert run.meta == {
                "target_session_id": orchestrator_id,
                "trigger": "ticket",
            }
            assert rig.router.roles == ["memory"]
        finally:
            await rig.close()

    asyncio.run(scenario())


def test_cancelled_ticket_also_extracts(db_session_factory) -> None:
    """cancelled 也抽：工单终态为 cancelled 时仍写候选。"""
    factory = db_session_factory

    async def scenario() -> None:
        llm = FakeLLM(
            [
                _proposals(
                    {
                        "kind": "procedural",
                        "subject": "device",
                        "content": "这台设备的 ADB 连接超时后重连一次就好",
                        "confidence": 0.6,
                    }
                )
            ]
        )
        rig = await _build_ticket_rig(factory, llm)
        try:
            ticket, _orchestrator_id = await _create_ticket(
                rig, title="取消的刷图", scope="用户中途取消了"
            )
            await rig.tickets.record_report(
                ticket.id,
                ReportInput(status=ReportStatus.DROPPED, summary="用户取消"),
            )
            tasks = _take_tasks(rig.tickets)
            assert len(tasks) == 1
            await _wait(tasks)

            async with factory() as session:
                stored_ticket = await TicketRepository(session).get(ticket.id)
            assert stored_ticket.status == TicketStatus.CANCELLED.value
            page = await rig.memory.list_memories()
            assert page.total == 1
            assert page.items[0].status == MemoryStatus.PROPOSED.value
        finally:
            await rig.close()

    asyncio.run(scenario())


def test_failed_ticket_also_extracts(db_session_factory) -> None:
    """failed 也抽：失败工单的教训同样可能值得长期记住。"""
    factory = db_session_factory

    async def scenario() -> None:
        llm = FakeLLM(
            [
                _proposals(
                    {
                        "kind": "episodic",
                        "subject": "stage:7-18",
                        "content": "2026-11-05 打 7-18 因为缺干员失败",
                        "confidence": 0.5,
                    }
                )
            ]
        )
        rig = await _build_ticket_rig(factory, llm)
        try:
            ticket, _orchestrator_id = await _create_ticket(
                rig, title="失败的 7-18", scope="用户要求打 7-18"
            )
            await rig.tickets.record_report(
                ticket.id,
                ReportInput(status=ReportStatus.FAILED, summary="缺少干员，没打过"),
            )
            tasks = _take_tasks(rig.tickets)
            assert len(tasks) == 1
            await _wait(tasks)

            async with factory() as session:
                stored_ticket = await TicketRepository(session).get(ticket.id)
            assert stored_ticket.status == TicketStatus.FAILED.value
            assert (await rig.memory.list_memories()).total == 1
        finally:
            await rig.close()

    asyncio.run(scenario())


def test_duplicate_subject_skipped(db_session_factory) -> None:
    """同 kind+subject+规范化内容只留一条；超 500 字与白名单外的 supersedes 丢弃。"""
    factory = db_session_factory

    async def scenario() -> None:
        memory, settings, _recorder = _make_memory(factory)
        llm = FakeLLM(
            [
                _proposals(
                    {
                        "kind": "semantic",
                        "subject": "preference",
                        "content": "主力队伍以近卫为核心",
                        "confidence": 0.8,
                    },
                    {
                        "kind": "semantic",
                        "subject": "preference",
                        "content": "  主力队伍以近卫为核心  ",
                        "confidence": 0.5,
                    },
                    {
                        "kind": "semantic",
                        "subject": "preference",
                        "content": "x" * 501,
                    },
                    {
                        "kind": "semantic",
                        "subject": "account",
                        "content": "账号是官服",
                        "supersedes": "no-such-memory",
                    },
                )
            ]
        )
        stored = await extract_candidates(
            memory,
            session_factory=factory,
            router=_Router(llm),
            ledger=_Ledger(),
            settings_provider=lambda: settings,
        )
        assert [item.content for item in stored] == [
            "主力队伍以近卫为核心",
            "账号是官服",
        ]
        assert stored[1].supersedes_id is None
        assert (await memory.list_memories()).total == 2

    asyncio.run(scenario())


def test_bad_schema_does_not_fail_ticket(db_session_factory) -> None:
    """两次非法 JSON：模型被重试一次，零条写入，工单仍是 done，不抛到调用方。"""
    factory = db_session_factory

    async def scenario() -> None:
        llm = FakeLLM(
            [
                text_response("这不是 JSON"),
                text_response("仍然不是 JSON"),
            ]
        )
        rig = await _build_ticket_rig(factory, llm)
        try:
            ticket, _orchestrator_id = await _create_ticket(
                rig, title="坏输出的工单", scope="模型乱答"
            )
            await rig.tickets.record_report(
                ticket.id,
                ReportInput(status=ReportStatus.READY, summary="做完了"),
            )
            tasks = _take_tasks(rig.tickets)
            assert len(tasks) == 1
            await _wait(tasks)

            async with factory() as session:
                stored_ticket = await TicketRepository(session).get(ticket.id)
            assert stored_ticket.status == TicketStatus.DONE.value
            assert (await rig.memory.list_memories()).total == 0
            # complete_structured 的两次尝试各发一次请求。
            assert len(llm.requests) == 2
            runs = await _memory_runs(factory)
            assert len(runs) == 1
            assert runs[0].status == "failed"
        finally:
            await rig.close()

    asyncio.run(scenario())


# ---------------------------------------------------------------------------
# 补充：后台失败、压缩段、remember 工具
# ---------------------------------------------------------------------------
def test_background_failure_does_not_change_ticket(db_session_factory) -> None:
    """后台抽取抛错只记日志：``record_report`` 正常返回，终态已提交。"""
    factory = db_session_factory

    async def scenario() -> None:
        rig = await _build_ticket_rig(factory, FakeLLM([]), failing=True)
        try:
            ticket, _orchestrator_id = await _create_ticket(
                rig, title="抽取失败的工单", scope="模型挂了"
            )
            outcome = await rig.tickets.record_report(
                ticket.id,
                ReportInput(status=ReportStatus.READY, summary="做完了"),
            )
            assert outcome.ticket.status == TicketStatus.DONE.value
            tasks = _take_tasks(rig.tickets)
            assert len(tasks) == 1
            await _wait(tasks)
            async with factory() as session:
                stored_ticket = await TicketRepository(session).get(ticket.id)
            assert stored_ticket.status == TicketStatus.DONE.value
        finally:
            await rig.close()

    asyncio.run(scenario())


def test_compaction_extracts_compacted_segment(
    db_session_factory, monkeypatch
) -> None:
    """总控检查点后抽取被压缩段（用户消息与回答），并起 memory_extract 运行。"""
    factory = db_session_factory

    async def scenario() -> None:
        session_id = await seed_session(factory, kind="orchestrator")
        await _seed_messages(
            factory,
            session_id,
            [
                ("user", "用户原话甲：刷 1-7"),
                ("assistant", "总控回答甲：好的"),
                ("user", "用户原话乙"),
                ("assistant", "总控回答乙"),
                ("user", "用户原话丙"),
                ("assistant", "总控回答丙"),
                ("user", "用户原话丁"),
                ("assistant", "总控回答丁"),
            ],
        )
        llm = FakeLLM(
            [
                text_response(
                    json.dumps(
                        {"summary": "较早的消息已压缩到检查点", "next_step": ""},
                        ensure_ascii=False,
                    )
                ),
                _proposals(
                    {
                        "kind": "semantic",
                        "subject": "schedule",
                        "content": "用户习惯在晚上刷 1-7",
                        "confidence": 0.8,
                    }
                ),
            ]
        )
        router = _Router(llm)
        ledger = _Ledger()
        memory, settings, _recorder = _make_memory(factory)
        run_service = RunService(factory, boot_id="m79-t2")
        registry = AgentSpecRegistry(build_registry())
        registry.load()

        # 压缩模块读自己的设置：keep_recent_turns=2 让前两轮进压缩段。
        monkeypatch.setattr(
            "maa_api.agent.context.compaction.get_settings",
            lambda: SimpleNamespace(
                context=SimpleNamespace(
                    fallback_context_tokens=128_000,
                    compaction_ratio=0.75,
                    keep_recent_turns=2,
                    clear_tool_results_after=3,
                )
            ),
        )
        state = SimpleNamespace(
            memory_service=memory,
            model_router=router,
            cost_ledger=ledger,
            run_service=run_service,
            agent_spec_registry=registry,
            knowledge_service=_Knowledge("档案：用户偏好晚上玩"),
        )
        compactor = Compactor(state, factory)
        try:
            result = await compactor.compact(
                session_id=session_id,
                session_kind="orchestrator",
                ticket_id=None,
                spec=builtin_spec("orchestrator"),
                profile=_PROFILE,
                trigger="manual",
                durable=DurableState(captured_at="2026-11-05T04:00:00+08:00"),
            )
            assert result.checkpoint_id is not None

            tasks = _take_tasks(compactor)
            assert len(tasks) == 1
            await _wait(tasks)

            page = await memory.list_memories()
            assert page.total == 1
            assert page.items[0].source_session_id == session_id

            # 第二次模型请求是 curator：只带被压缩的用户消息与总控回答。
            assert len(llm.requests) == 2
            system_text = llm.requests[1][1][0]["content"]
            prompt = llm.requests[1][1][1]["content"]
            assert system_text == builtin_spec("memory_curator").body
            assert "用户原话甲：刷 1-7" in prompt
            assert "总控回答甲：好的" in prompt
            assert "用户原话乙" in prompt
            assert "总控回答乙" in prompt
            assert "用户原话丁" not in prompt
            assert "档案：用户偏好晚上玩" in prompt

            runs = await _all_runs(factory)
            memory_runs = [
                run
                for run in runs
                if run.kind == AgentRunKind.MEMORY_EXTRACT.value
            ]
            assert len(memory_runs) == 1
            assert memory_runs[0].parent_run_id is not None
            compaction_runs = [
                run for run in runs if run.kind == AgentRunKind.COMPACTION.value
            ]
            assert len(compaction_runs) == 1
            assert memory_runs[0].parent_run_id == compaction_runs[0].id
            assert memory_runs[0].meta == {
                "target_session_id": session_id,
                "trigger": "compaction",
            }

            # 只抽总控：Worker 的压缩段不再单独起抽取。
            compactor._schedule_memory_extraction(
                session_id=session_id,
                session_kind="worker",
                ticket_id=None,
                compacted=[],
                upto_seq=1,
                parent_run_id=None,
            )
            assert not [
                task for task in compactor._memory_tasks if not task.done()
            ]
        finally:
            await run_service.close()

    asyncio.run(scenario())


def test_remember_tool_proposes_and_honours_disabled(db_session_factory) -> None:
    """``remember`` 只提议、confidence=1；``memory.enabled=false`` 时工具错误。"""
    factory = db_session_factory

    async def scenario() -> None:
        registry = build_registry()
        definition = registry.get("remember")
        assert definition.group == "memory"
        assert definition.rest_visible is False
        assert definition.description == REMEMBER_DESCRIPTION

        memory, settings, _recorder = _make_memory(factory)
        source_session = await seed_session(factory, kind="orchestrator")
        source_run = await seed_run(factory, session_id=source_session)
        async with factory() as db:
            source_ticket = Ticket(
                title="来源工单",
                kind="daily_ops",
                status=TicketStatus.OPEN.value,
                origin=TicketOrigin.SYSTEM.value,
            )
            db.add(source_ticket)
            await db.commit()
        context = ToolContext(
            caller=CallerType.INTERNAL,
            session_id=source_session,
            request_id=None,
            request=SimpleNamespace(
                app=SimpleNamespace(
                    state=SimpleNamespace(memory_service=memory)
                )
            ),
            db_session=None,
            origin=ToolOrigin(
                run_id=source_run,
                ticket_id=str(source_ticket.id),
                agent="orchestrator",
            ),
        )
        result = await registry.execute(
            "remember",
            {
                "kind": "semantic",
                "subject": "preference",
                "content": "不用源石恢复理智",
            },
            context,
        )
        assert result["status"] == MemoryStatus.PROPOSED.value
        assert result["id"]
        async with factory() as session:
            row = await MemoryRepository(session).get(result["id"])
        assert row is not None
        assert row.confidence == 1.0
        assert row.source_ticket_id == str(source_ticket.id)
        assert row.source_session_id == source_session
        assert row.source_run_id == source_run

        disabled, _disabled_settings, _ = _make_memory(
            factory, settings=_Settings(enabled=False)
        )
        disabled_context = ToolContext(
            caller=CallerType.INTERNAL,
            session_id=None,
            request_id=None,
            request=SimpleNamespace(
                app=SimpleNamespace(
                    state=SimpleNamespace(memory_service=disabled)
                )
            ),
            db_session=None,
        )
        disabled_result = await registry.execute(
            "remember",
            {
                "kind": "semantic",
                "subject": "preference",
                "content": "这条不该写",
            },
            disabled_context,
        )
        assert disabled_result["error"]["code"] == "memory_disabled"
        page = await disabled.list_memories()
        assert page.total == 1  # 只有上一步写入的那条
        assert all(item.content != "这条不该写" for item in page.items)

    asyncio.run(scenario())


