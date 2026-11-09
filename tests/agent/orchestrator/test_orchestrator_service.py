"""M70 T11：OrchestratorService（spec §4.5.2、§6 的五条）。

异步用例沿用 M69 的写法：同步测试函数 + ``asyncio.run(scenario())``；临时库用
``tests/fakes/v3_db.make_session_factory``。会话、收件箱、运行、提问用真实服务，
代理定义注册表、模型路由、工单服务用轻量替身；``run_agent`` 在需要真跑一轮的地方
用 ``monkeypatch`` 换成假实现，不碰模型与中间件链。
"""

from __future__ import annotations

import asyncio
import inspect
from datetime import timedelta
from types import SimpleNamespace

import pytest
from sqlalchemy import select

from maa_api.agent.harness.context import RunOutcome
from maa_api.agent.harness.events import RunEvent, RunEventBus
from maa_api.agent.orchestrator import service as orchestrator_service
from maa_api.agent.orchestrator.service import OrchestratorService
from maa_api.agent.questions import QuestionService
from maa_api.agent.runs.service import RunService
from maa_api.agent.tickets.inbox import InboxService
from maa_api.db.models import AgentInbox, AgentSession, utcnow
from maa_api.domain.enums import InboxKind, InboxStatus
from maa_api.domain.errors import AppError, ErrorCode
from tests.fakes.v3_db import make_session_factory

_ORCHESTRATOR = SimpleNamespace(
    name="orchestrator",
    kind="orchestrator",
    title="总控",
    description="常驻的总控",
    model_role="orchestrator",
    risk_ceiling="none",
    spec_version="orch12345678",
    body="你是 MAA-API 的总控",
    tools=(),
    delegates=(),
)
_GENERAL = SimpleNamespace(
    name="general",
    kind="worker",
    title="通用执行",
    description="在界面里干活",
)


class _Registry:
    """两定义的最小注册表；总控卡只需要 ``list``，跑一轮只需要 ``get``。"""

    def get(self, name: str) -> SimpleNamespace:
        if name == "orchestrator":
            return _ORCHESTRATOR
        raise AppError(ErrorCode.AGENT_SPEC_NOT_FOUND, f"没有名为 {name} 的代理定义")

    def list(self) -> list[SimpleNamespace]:
        return [_ORCHESTRATOR, _GENERAL]


class _Router:
    """总控角色的模型档案替身；改字段即可切换「配置好了 / 没配好」。"""

    def __init__(self) -> None:
        self.roles: list[str] = []
        self.profile = SimpleNamespace(
            model="test-model",
            base_url="https://llm.test/v1",
            api_key="sk-test",
        )

    async def resolve(self, role: str) -> SimpleNamespace:
        self.roles.append(str(role))
        return self.profile


class _Harness:
    def __init__(
        self,
        factory,
        *,
        router: _Router | None = None,
        question_service: QuestionService | None = None,
        confirmation_service: SimpleNamespace | None = None,
        event_bus: RunEventBus | None = None,
        run_service: RunService | None = None,
    ) -> None:
        self.factory = factory
        self.inbox = InboxService(factory)
        self.registry = _Registry()
        self.router = router or _Router()
        self.question_service = question_service
        self.confirmation_service = confirmation_service or SimpleNamespace(
            aborted=[]
        )
        self.run_service = run_service or RunService(factory, boot_id="boot-test")
        self.state = SimpleNamespace(
            model_router=self.router,
            setting_service=None,
            question_service=question_service,
            confirmation_service=self.confirmation_service,
        )
        self.app = SimpleNamespace(state=self.state)
        self.events: list[RunEvent] = []
        self._unsubscribe = (
            event_bus.subscribe(self._record) if event_bus is not None else None
        )
        self.service = OrchestratorService(
            app=self.app,
            session_factory=factory,
            spec_registry=self.registry,
            run_service=self.run_service,
            inbox_service=self.inbox,
            ticket_service=SimpleNamespace(),
            question_service=question_service,
            confirmation_service=self.confirmation_service,
            model_router=self.router,
            event_bus=event_bus,
        )

    async def _record(self, event: RunEvent) -> None:
        self.events.append(event)

    async def close(self) -> None:
        await self.service.close()
        if self._unsubscribe is not None:
            self._unsubscribe()
            self._unsubscribe = None
        await self.run_service.close()


async def _get_session(factory, session_id: str) -> AgentSession | None:
    async with factory() as db:
        return await db.get(AgentSession, session_id)


async def _all_orchestrators(factory) -> list[AgentSession]:
    async with factory() as db:
        result = await db.execute(
            select(AgentSession)
            .where(AgentSession.kind == "orchestrator")
            .order_by(AgentSession.created_at.asc(), AgentSession.id.asc())
        )
        return list(result.scalars().all())


async def _inbox_items(factory, session_id: str) -> list[AgentInbox]:
    async with factory() as db:
        result = await db.execute(
            select(AgentInbox)
            .where(AgentInbox.session_id == session_id)
            .order_by(AgentInbox.id.asc())
        )
        return list(result.scalars().all())


async def _wait_for(predicate, *, timeout: float = 3.0) -> None:
    loop = asyncio.get_running_loop()
    deadline = loop.time() + timeout
    while loop.time() < deadline:
        result = predicate()
        if inspect.isawaitable(result):
            result = await result
        if result:
            return
        await asyncio.sleep(0.01)
    raise AssertionError("等待的条件没有在期限内成立")


def test_single_active_session_under_concurrency_and_repair(tmp_path) -> None:
    engine, factory = asyncio.run(make_session_factory(tmp_path / "single.db"))
    try:

        async def scenario() -> None:
            harness = _Harness(factory)
            sessions = await asyncio.gather(
                *(harness.service.ensure_session() for _ in range(10))
            )
            ids = {str(session.id) for session in sessions}
            assert len(ids) == 1
            current_id = ids.pop()

            active = await _all_orchestrators(factory)
            assert [str(row.id) for row in active] == [current_id]
            assert active[0].title == "总控"
            assert str(active[0].kind) == "orchestrator"
            assert str(active[0].status) == "active"

            # 启动修复：再造两个更早的活动会话，新实例会把它们归档。
            older = utcnow() - timedelta(hours=1)
            abandoned_one = AgentSession(
                title="旧总控一",
                model="test-model",
                kind="orchestrator",
                status="active",
                created_at=older,
                updated_at=older,
            )
            abandoned_two = AgentSession(
                title="旧总控二",
                model="test-model",
                kind="orchestrator",
                status="active",
                created_at=older - timedelta(hours=1),
                updated_at=older,
            )
            async with factory() as db:
                db.add(abandoned_one)
                db.add(abandoned_two)
                await db.commit()
            abandoned_ids = {str(abandoned_one.id), str(abandoned_two.id)}

            restarted = _Harness(factory)
            kept = await restarted.service.ensure_session()
            assert str(kept.id) == current_id
            for session_id in abandoned_ids:
                row = await _get_session(factory, session_id)
                assert row is not None
                assert str(row.status) == "finished"
                assert row.archived_at is not None
            state = await restarted.service.state()
            assert state.session_id == current_id
            assert state.archived_count == 2
            assert state.pending_inbox == 0

            await restarted.close()
            await harness.close()

        asyncio.run(scenario())
    finally:
        engine.sync_engine.dispose()


def test_messages_within_window_are_claimed_together(tmp_path, monkeypatch) -> None:
    engine, factory = asyncio.run(make_session_factory(tmp_path / "batch.db"))
    try:

        async def scenario() -> None:
            monkeypatch.setattr(
                orchestrator_service,
                "get_settings",
                lambda: SimpleNamespace(
                    orchestrator=SimpleNamespace(batch_window_ms=30)
                ),
            )
            bus = RunEventBus()
            harness = _Harness(factory, event_bus=bus)
            session = await harness.service.ensure_session()
            for index in range(3):
                posted = await harness.service.post_user_message(
                    f"消息 {index}", [], source="web", sender="web"
                )
                assert posted.seq == index
                assert posted.session_id == session.id
                assert posted.inbox_id > 0

            seen: dict = {}

            async def fake_run_agent(
                app,
                spec,
                session,
                run,
                *,
                inbox_items,
                cancel_event,
                cancel_reason,
                state,
                parent=None,
            ) -> RunOutcome:
                seen["items"] = list(inbox_items)
                seen["run"] = run
                seen["state"] = dict(state)
                return RunOutcome(
                    status="succeeded", finish_reason="stop", final_text="好的"
                )

            monkeypatch.setattr(
                orchestrator_service, "run_agent", fake_run_agent
            )
            harness.service.wake()
            await _wait_for(lambda: "items" in seen)
            await _wait_for(lambda: not harness.service.turn_running())

            assert [str(item.kind) for item in seen["items"]] == [
                "user_message",
                "user_message",
                "user_message",
            ]
            assert seen["state"]["role_card"].startswith("## 可用的 Worker")
            assert "general" in seen["state"]["role_card"]
            items = await _inbox_items(factory, session.id)
            assert [str(item.status) for item in items] == [
                InboxStatus.CONSUMED.value
            ] * 3
            state = await harness.service.state()
            assert state.pending_inbox == 0
            assert state.turn_running is False

            # 路由写的用户消息也补发 message_persisted，run_id 为空。
            persisted = [
                event for event in harness.events if event.name == "message_persisted"
            ]
            assert len(persisted) == 3
            assert all(event.run_id is None for event in persisted)
            assert [event.data["message"]["seq"] for event in persisted] == [0, 1, 2]
            assert all(
                event.data["message"]["role"] == "user"
                and event.data["message"]["inbox_id"] is None
                for event in persisted
            )

            stored_run = await harness.run_service.get(seen["run"].id)
            assert stored_run is not None
            assert stored_run.session_id == session.id
            assert str(stored_run.kind) == "orchestrator_turn"
            assert str(stored_run.agent_name) == "orchestrator"
            # 假运行没有自己收尾，服务在 finally 里按失败收掉，不留 running 行。
            assert str(stored_run.status) == "failed"

            await harness.close()

        asyncio.run(scenario())
    finally:
        engine.sync_engine.dispose()


def test_new_message_skips_pending_question(tmp_path, monkeypatch) -> None:
    engine, factory = asyncio.run(make_session_factory(tmp_path / "question.db"))
    try:

        async def scenario() -> None:
            monkeypatch.setattr(
                orchestrator_service,
                "get_settings",
                lambda: SimpleNamespace(
                    orchestrator=SimpleNamespace(batch_window_ms=0)
                ),
            )
            questions = QuestionService(
                factory, broadcast=lambda _kind, _data: None
            )
            harness = _Harness(factory, question_service=questions)
            session = await harness.service.ensure_session()
            wire, _audit_id = await questions.create(
                session_id=session.id,
                tool_call_id="call-ask-1",
                question="刷哪一关？",
                options=[{"label": "1-7"}, {"label": "CE-6"}],
                multiple=False,
            )

            await harness.inbox.put(
                session.id,
                InboxKind.SYSTEM,
                payload={"text": "开始一轮", "passive": False},
            )
            first_started = asyncio.Event()
            observed: dict = {}
            turns: list[int] = []

            async def fake_turn(handle) -> bool:
                turns.append(len(turns))
                if len(turns) == 1:
                    await harness.inbox.drop_pending(session.id)
                    first_started.set()
                    resolved = await questions.wait(wire["question_id"])
                    observed["status"] = resolved["status"]
                    observed["answered_by"] = resolved["answered_by"]
                    observed["finish_reason"] = "question_unanswered"
                    return True
                await harness.inbox.drop_pending(session.id)
                observed["second_turn"] = True
                return True

            harness.service._run_turn = fake_turn  # type: ignore[method-assign]
            harness.service.wake()
            await asyncio.wait_for(first_started.wait(), timeout=3.0)

            posted = await harness.service.post_user_message("先刷 1-7", [])
            assert posted.session_id == session.id
            await _wait_for(lambda: observed.get("second_turn") is True)

            assert observed["status"] == "skipped"
            assert observed["answered_by"] == "superseded"
            assert observed["finish_reason"] == "question_unanswered"
            assert turns == [0, 1]

            await harness.close()

        asyncio.run(scenario())
    finally:
        engine.sync_engine.dispose()


def test_reset_refuses_while_running_and_moves_pending_items(
    tmp_path, monkeypatch
) -> None:
    engine, factory = asyncio.run(make_session_factory(tmp_path / "reset.db"))
    try:

        async def scenario() -> None:
            window = {"ms": 0}
            monkeypatch.setattr(
                orchestrator_service,
                "get_settings",
                lambda: SimpleNamespace(
                    orchestrator=SimpleNamespace(batch_window_ms=window["ms"])
                ),
            )
            harness = _Harness(factory)
            old = await harness.service.ensure_session()

            await harness.inbox.put(
                old.id,
                InboxKind.SYSTEM,
                payload={"text": "开始一轮", "passive": False},
            )
            started = asyncio.Event()
            release = asyncio.Event()

            async def blocking_turn(handle) -> bool:
                await harness.inbox.drop_pending(old.id)
                started.set()
                await release.wait()
                return True

            harness.service._run_turn = blocking_turn  # type: ignore[method-assign]
            harness.service.wake()
            await asyncio.wait_for(started.wait(), timeout=3.0)

            with pytest.raises(AppError) as running:
                await harness.service.reset()
            assert running.value.code is ErrorCode.RUN_ALREADY_ACTIVE
            assert running.value.message == "总控还在处理上一条消息，等它结束后再重新开始"

            release.set()
            await _wait_for(lambda: not harness.service.turn_running())

            # 有待处理的 user_message 时也拒绝，且不归档当前会话。
            item, _message = await harness.inbox.put_user_message(
                old.id,
                content="还没处理的那条",
                thread_key="chat:orchestrator",
                payload={"mentions": [], "source": "web", "sender": "web"},
            )
            assert item.id is not None
            with pytest.raises(AppError) as unprocessed:
                await harness.service.reset()
            assert unprocessed.value.code is ErrorCode.RUN_ALREADY_ACTIVE
            assert unprocessed.value.message == "总控还有没处理完的消息，稍后再重新开始"
            await harness.inbox.drop_pending(old.id)

            await harness.inbox.put(
                old.id,
                InboxKind.SYSTEM,
                payload={"text": "只是备注", "passive": True},
            )
            await harness.inbox.put(
                old.id,
                InboxKind.REPORT,
                payload={
                    "ticket_id": "t-1",
                    "title": "日常",
                    "kind": "general",
                    "status": "progress",
                    "summary": "还在跑",
                },
            )

            # 搬来的项里有非 passive 的会在 reset 末尾唤醒；把窗口拉长，
            # 断言「搬走」时新泵还没开始处理。
            window["ms"] = 5000
            state = await harness.service.reset()

            assert state.session_id != old.id
            assert state.status == "active"
            assert state.pending_inbox == 2
            assert state.archived_count == 1

            old_row = await _get_session(factory, old.id)
            assert old_row is not None
            assert old_row.archived_at is not None
            assert str(old_row.status) == "finished"
            new_row = await _get_session(factory, state.session_id)
            assert new_row is not None
            assert str(new_row.kind) == "orchestrator"
            assert str(new_row.status) == "active"
            assert new_row.archived_at is None

            moved = await _inbox_items(factory, state.session_id)
            assert len(moved) == 2
            assert {str(row.status) for row in moved} == {InboxStatus.PENDING.value}
            assert [str(row.kind) for row in moved] == ["system", "report"]
            left_behind = await _inbox_items(factory, old.id)
            assert all(str(row.status) != InboxStatus.PENDING.value for row in left_behind)
            assert old.id not in harness.service._pumps
            assert state.session_id in harness.service._pumps

            await harness.close()

        asyncio.run(scenario())
    finally:
        engine.sync_engine.dispose()


def test_start_sweeps_pending_items_and_ensure_ready(tmp_path, monkeypatch) -> None:
    engine, factory = asyncio.run(make_session_factory(tmp_path / "start.db"))
    try:

        async def scenario() -> None:
            monkeypatch.setattr(
                orchestrator_service,
                "get_settings",
                lambda: SimpleNamespace(
                    orchestrator=SimpleNamespace(batch_window_ms=0)
                ),
            )
            harness = _Harness(factory)
            session = await harness.service.ensure_session()
            await harness.inbox.put(
                session.id,
                InboxKind.SYSTEM,
                payload={"text": "继续处理", "passive": False},
            )

            started = asyncio.Event()
            runs: list = []

            async def fake_turn(handle) -> bool:
                runs.append(handle)
                await harness.inbox.drop_pending(session.id)
                started.set()
                return True

            harness.service._run_turn = fake_turn  # type: ignore[method-assign]
            await harness.service.start()
            await asyncio.wait_for(started.wait(), timeout=3.0)
            await _wait_for(lambda: not harness.service.turn_running())

            assert len(runs) == 1
            assert await harness.service.ensure_session() is not None
            kept = await harness.service.ensure_session()
            assert str(kept.id) == str(session.id)
            state = await harness.service.state()
            assert state.session_id == str(session.id)
            assert state.pending_inbox == 0
            all_rows = await _all_orchestrators(factory)
            assert len(all_rows) == 1

            # 配置齐全时通过；缺字段与解密失败各有自己的错误码与文案。
            await harness.service.ensure_ready()
            harness.router.profile.api_key = ""
            with pytest.raises(AppError) as unconfigured:
                await harness.service.ensure_ready()
            assert unconfigured.value.code is ErrorCode.LLM_NOT_CONFIGURED
            assert unconfigured.value.message == (
                "还没有配置 LLM，请先在设置页填写 base_url、api_key 与 model"
            )

            harness.router.profile.api_key = "sk-test"
            harness.router.profile.base_url = ""
            harness.state.setting_service = SimpleNamespace(
                llm_encryption_error=RuntimeError("解不开")
            )
            with pytest.raises(AppError) as invalid_key:
                await harness.service.ensure_ready()
            assert invalid_key.value.code is ErrorCode.LLM_ENCRYPTION_KEY_INVALID
            assert invalid_key.value.message == (
                "已保存的 LLM API Key 无法解密，请到设置页重新填写 API Key"
            )

            await harness.close()

        asyncio.run(scenario())
    finally:
        engine.sync_engine.dispose()



def test_im_source_reply_is_written_to_outbox(tmp_path) -> None:
    """M85 §4.5：本轮 user 消息 payload 有 platform 时写 ``reply:<run_id>``。"""

    async def scenario() -> None:
        engine, factory = await make_session_factory(tmp_path / "im-reply.db")
        calls: list[tuple] = []

        async def enqueue_reply_for_run(*args, **kwargs):
            calls.append((args, kwargs))

        try:
            harness = _Harness(factory)
            harness.state.channel_service = SimpleNamespace(
                enqueue_reply_for_run=enqueue_reply_for_run
            )
            item = SimpleNamespace(
                payload={"platform": "telegram", "external_chat_id": "chat-1"}
            )
            await harness.service._enqueue_im_reply(
                SimpleNamespace(id="run-1"), [item], final_text="已经处理好了"
            )
            assert len(calls) == 1
            args, kwargs = calls[0]
            assert args == ("run-1", "chat-1", "telegram", "已经处理好了")
            assert kwargs["ref_run_id"] == "run-1"

            # 网页来源没有 platform：不写发件箱。
            await harness.service._enqueue_im_reply(
                SimpleNamespace(id="run-2"),
                [SimpleNamespace(payload={"source": "web"})],
                final_text="网页回复",
            )
            assert len(calls) == 1
        finally:
            await engine.dispose()

    asyncio.run(scenario())
