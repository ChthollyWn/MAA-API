"""M70 T12：``RunEventStream`` 与 ``AgentRunBroadcaster``（spec §4.5.8、§4.3.3、§6 五条）。

用假事件总线与假运行服务直接驱动：总线把 ``RunEvent`` 交给订阅者，帧从
``stream.frames()`` 读；临时库只放收件箱项、会话与运行行，不起 lifespan、不碰模型。
"""

from __future__ import annotations

import asyncio
import json
from types import SimpleNamespace

from maa_api.agent.harness.events import RunEvent
from maa_api.agent.orchestrator import stream as stream_module
from maa_api.agent.orchestrator.stream import AgentRunBroadcaster, RunEventStream
from maa_api.db.models import AgentInbox
from maa_api.domain.enums import InboxStatus
from tests.fakes.v3_db import make_session_factory, seed_run, seed_session


class _FakeBus:
    """最小的总线替身：订阅/退订与异步分发；``run_id`` 由调用方按真实总线给。"""

    def __init__(self) -> None:
        self.sinks: list = []

    def subscribe(self, sink):
        self.sinks.append(sink)

        def unsubscribe() -> None:
            if sink in self.sinks:
                self.sinks.remove(sink)

        return unsubscribe

    async def emit(self, name: str, data, *, run_id: str | None = None) -> None:
        event = RunEvent(name=name, data=data, run_id=run_id)
        for sink in tuple(self.sinks):
            await sink(event)


class _FakeRunService:
    """只提供 ``RunEventStream`` 用到的两个查询；收件箱与会话仍走真实库。"""

    def __init__(self, factory, *, active=None, runs=None) -> None:
        self.session_factory = factory
        self._active = dict(active or {})
        self._runs = dict(runs or {})

    async def active_run(self, session_id):
        return self._active.get(session_id)

    async def get(self, run_id):
        return self._runs.get(run_id)


def _app(run_service, bus):
    return SimpleNamespace(
        state=SimpleNamespace(run_service=run_service, run_event_bus=bus)
    )


def _decode(frame: str) -> tuple[str, dict]:
    name = ""
    data: dict = {}
    for line in frame.splitlines():
        if line.startswith("event: "):
            name = line[len("event: ") :]
        elif line.startswith("data: "):
            data = json.loads(line[len("data: ") :])
    return name, data


async def _collect(frames) -> list[str]:
    collected: list[str] = []
    async for frame in frames:
        collected.append(frame)
    return collected


async def _put_inbox(
    factory,
    session_id: str,
    *,
    status: InboxStatus = InboxStatus.PENDING,
    consumed_run_id: str | None = None,
) -> int:
    async with factory() as db:
        item = AgentInbox(
            session_id=session_id,
            kind="user_message",
            payload={"text": "你好"},
            status=str(status),
            consumed_run_id=consumed_run_id,
        )
        db.add(item)
        await db.commit()
        return int(item.id)


def test_stream_forwards_current_run_then_consuming_run() -> None:
    """消息到达时已在跑的那一轮先转发，消费本条消息的那一轮 ``run_end`` 后关流。"""

    async def scenario() -> None:
        bus = _FakeBus()
        service = _FakeRunService(None, active={"s1": SimpleNamespace(id="r-old")})
        stream = RunEventStream(_app(service, bus), session_id="s1")
        try:
            await stream._wait_ready()
            stream.track(12)
            await bus.emit(
                "text_delta", {"run_id": "r-old", "text": "旧轮的尾巴"}, run_id="r-old"
            )
            await bus.emit(
                "run_end", {"run_id": "r-old", "finish_reason": "stop"}, run_id="r-old"
            )
            await bus.emit(
                "run_start",
                {
                    "run_id": "r-new",
                    "parent_run_id": None,
                    "session_id": "s1",
                    "ticket_id": None,
                    "agent": "orchestrator",
                    "kind": "orchestrator_turn",
                    "consumed_inbox_ids": [12],
                },
                run_id="r-new",
            )
            await bus.emit(
                "text_delta", {"run_id": "r-new", "text": "新轮"}, run_id="r-new"
            )
            await bus.emit(
                "run_end", {"run_id": "r-new", "finish_reason": "stop"}, run_id="r-new"
            )

            frames = await asyncio.wait_for(_collect(stream.frames()), timeout=2.0)
        finally:
            stream.close()

        assert [_decode(frame)[0] for frame in frames] == [
            "text_delta",
            "run_end",
            "run_start",
            "text_delta",
            "run_end",
        ]
        assert _decode(frames[0])[1]["run_id"] == "r-old"
        assert _decode(frames[-1])[1]["run_id"] == "r-new"
        # 关流即退订：总线不再持有这个流的 sink。
        assert bus.sinks == []

    asyncio.run(scenario())


def test_stream_includes_subagent_events() -> None:
    """Worker / 总控流带上它委派的子代理事件（``parent_run_id`` 指向本流）。"""

    async def scenario() -> None:
        bus = _FakeBus()
        stream = RunEventStream(_app(_FakeRunService(None), bus), session_id="s1")
        try:
            stream.track(7)
            await bus.emit(
                "run_start",
                {
                    "run_id": "r-parent",
                    "parent_run_id": None,
                    "session_id": "s1",
                    "ticket_id": None,
                    "agent": "general",
                    "kind": "worker_turn",
                    "consumed_inbox_ids": [7],
                },
                run_id="r-parent",
            )
            await bus.emit(
                "subagent_start",
                {
                    "run_id": "r-sub",
                    "parent_run_id": "r-parent",
                    "agent": "researcher",
                    "task": "查 1-7 的掉落",
                },
                run_id="r-parent",
            )
            await bus.emit(
                "subagent_end",
                {"run_id": "r-sub", "status": "succeeded", "summary": "掉了装置"},
                run_id="r-parent",
            )
            await bus.emit(
                "run_end", {"run_id": "r-parent", "finish_reason": "stop"}, run_id="r-parent"
            )

            frames = await asyncio.wait_for(_collect(stream.frames()), timeout=2.0)
        finally:
            stream.close()

        names = [_decode(frame)[0] for frame in frames]
        assert names == ["run_start", "subagent_start", "subagent_end", "run_end"]
        subagent_start = _decode(frames[1])[1]
        assert subagent_start["run_id"] == "r-sub"
        assert subagent_start["parent_run_id"] == "r-parent"
        assert _decode(frames[2])[1]["summary"] == "掉了装置"

    asyncio.run(scenario())


def test_stream_db_fallback_for_missed_run_end_and_dropped_item(
    tmp_path, monkeypatch
) -> None:
    """没见到 ``run_end`` 时用运行行合成；项被丢弃时发 ``error`` 后关流。"""
    monkeypatch.setattr(stream_module, "SSE_KEEPALIVE_SECONDS", 0.01)

    async def scenario() -> None:
        engine, factory = await make_session_factory(tmp_path / "stream.db")
        try:
            # 总控：项已消费、运行已终态，但流里没见到 run_end。
            orchestrator_session = await seed_session(factory, kind="orchestrator")
            run_id = await seed_run(
                factory,
                session_id=orchestrator_session,
                status="succeeded",
                boot_id="other-boot",
            )
            inbox_id = await _put_inbox(
                factory,
                orchestrator_session,
                status=InboxStatus.CONSUMED,
                consumed_run_id=run_id,
            )
            run = SimpleNamespace(
                id=run_id,
                status="succeeded",
                finish_reason="stop",
                prompt_tokens=3,
                completion_tokens=4,
                cached_tokens=5,
                cost_micros=6,
                currency="CNY",
            )
            bus = _FakeBus()
            service = _FakeRunService(factory, runs={run_id: run})
            stream = RunEventStream(_app(service, bus), session_id=orchestrator_session)
            try:
                stream.track(inbox_id)
                frames = await asyncio.wait_for(_collect(stream.frames()), timeout=2.0)
            finally:
                stream.close()

            assert frames[0] == ": keep-alive\n\n"
            name, data = _decode(frames[1])
            assert name == "run_end"
            assert data["run_id"] == run_id
            assert data["finish_reason"] == "stop"
            assert data["usage"]["prompt_tokens"] == 3

            # 工单：项被丢弃，发 TICKET_ENDED 的错误帧并关流。
            worker_session = await seed_session(factory, kind="worker")
            dropped_id = await _put_inbox(
                factory, worker_session, status=InboxStatus.DROPPED
            )
            dropped_bus = _FakeBus()
            dropped_stream = RunEventStream(
                _app(_FakeRunService(factory), dropped_bus), session_id=worker_session
            )
            try:
                dropped_stream.track(dropped_id)
                dropped_frames = await asyncio.wait_for(
                    _collect(dropped_stream.frames()), timeout=2.0
                )
            finally:
                dropped_stream.close()

            assert dropped_frames[0] == ": keep-alive\n\n"
            name, data = _decode(dropped_frames[1])
            assert name == "error"
            assert data == {
                "code": "TICKET_ENDED",
                "message": "工单已结束，这条消息没有被处理",
            }
        finally:
            await engine.dispose()

    asyncio.run(scenario())


def test_keepalive_comment(tmp_path, monkeypatch) -> None:
    """没有事件时按间隔发注释行；流关闭后从总线退订。"""
    monkeypatch.setattr(stream_module, "SSE_KEEPALIVE_SECONDS", 0.01)

    async def scenario() -> None:
        engine, factory = await make_session_factory(tmp_path / "keepalive.db")
        try:
            session_id = await seed_session(factory, kind="orchestrator")
            bus = _FakeBus()
            stream = RunEventStream(
                _app(_FakeRunService(factory), bus), session_id=session_id
            )
            frames = stream.frames()
            try:
                frame = await asyncio.wait_for(frames.__anext__(), timeout=2.0)
                assert frame == ": keep-alive\n\n"
            finally:
                await frames.aclose()
            assert bus.sinks == []
        finally:
            await engine.dispose()

    asyncio.run(scenario())


def test_agent_run_broadcaster_maps_three_events() -> None:
    """三种总线事件映射成 ``agent_run``；没有运行的消息运行字段为 null。"""

    async def scenario() -> None:
        bus = _FakeBus()
        calls: list[tuple[str, dict]] = []
        broadcaster = AgentRunBroadcaster(bus, lambda channel, data: calls.append((channel, data)))
        broadcaster.start()
        broadcaster.start()  # 幂等：不会重复订阅成两条消息
        try:
            # 路由写的用户消息：receive 时还没有消费它的运行。
            await bus.emit(
                "message_persisted",
                {
                    "session_id": "s1",
                    "ticket_id": None,
                    "agent": "orchestrator",
                    "kind": None,
                    "message": {
                        "id": 1,
                        "seq": 1,
                        "role": "user",
                        "inbox_id": None,
                        "preview": "建一个日常工单",
                    },
                },
            )
            await bus.emit(
                "run_start",
                {
                    "run_id": "r1",
                    "parent_run_id": None,
                    "session_id": "s1",
                    "ticket_id": None,
                    "agent": "orchestrator",
                    "kind": "orchestrator_turn",
                    "consumed_inbox_ids": [1],
                },
                run_id="r1",
            )
            # 本轮落库的助手消息：从 run_start 记下的上下文补 run_id。
            await bus.emit(
                "message_persisted",
                {
                    "session_id": "s1",
                    "ticket_id": None,
                    "agent": "orchestrator",
                    "kind": "orchestrator_turn",
                    "message": {
                        "id": 2,
                        "seq": 2,
                        "role": "assistant",
                        "inbox_id": None,
                        "preview": "工单已建",
                    },
                },
            )
            await bus.emit(
                "run_end", {"run_id": "r1", "finish_reason": "stop"}, run_id="r1"
            )
        finally:
            broadcaster.stop()

        # stop 之后的事件不再广播。
        before = len(calls)
        await bus.emit("run_end", {"run_id": "r9", "finish_reason": "stop"}, run_id="r9")
        assert len(calls) == before == 4
        assert {channel for channel, _ in calls} == {"agent_run"}

        user_message = calls[0][1]
        assert user_message["event"] == "message_created"
        assert user_message["run_id"] is None
        assert user_message["kind"] is None
        assert user_message["status"] is None
        assert user_message["agent"] == "orchestrator"

        started = calls[1][1]
        assert started == {
            "event": "run_started",
            "run_id": "r1",
            "session_id": "s1",
            "ticket_id": None,
            "agent": "orchestrator",
            "kind": "orchestrator_turn",
            "status": "running",
        }

        assistant = calls[2][1]
        assert assistant["event"] == "message_created"
        assert assistant["run_id"] == "r1"
        assert assistant["kind"] == "orchestrator_turn"
        assert assistant["status"] == "running"
        assert assistant["message"] == {
            "id": 2,
            "seq": 2,
            "role": "assistant",
            "inbox_id": None,
            "preview": "工单已建",
        }

        finished = calls[3][1]
        assert finished["event"] == "run_finished"
        assert finished["run_id"] == "r1"
        assert finished["status"] == "succeeded"
        assert finished["finish_reason"] == "stop"
        assert finished["agent"] == "orchestrator"
        assert finished["kind"] == "orchestrator_turn"

    asyncio.run(scenario())
