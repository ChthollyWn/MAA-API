"""M4-07 WebSocket message, filtering, replay and slow-client contract."""

from __future__ import annotations

import asyncio

import pytest
from fastapi import FastAPI
from fastapi.routing import APIWebSocketRoute
from starlette.websockets import WebSocketDisconnect

import maa_api.api.ws as ws_api
from maa_api.services.log_hub import LogHub, LogRecord, set_log_hub


@pytest.fixture
def ws_app(monkeypatch):
    app = FastAPI()
    app.include_router(ws_api.router)
    manager = ws_api.ConnectionManager()
    monkeypatch.setattr(ws_api, "manager", manager)
    set_log_hub(None)
    yield app, manager
    set_log_hub(None)


def _record(source: str, content: str, *, level: str = "INFO") -> LogRecord:
    return LogRecord(ts=1.0, source=source, level=level, content=content)


def test_websocket_route_is_registered(ws_app):
    app, _ = ws_app
    paths = {
        route.path
        for route in ws_api.router.routes
        if isinstance(route, APIWebSocketRoute)
    }
    assert paths == {"/api/ws"}
    assert app


def test_auth_close_code_query_cookie_ping_and_unknown_type(
    ws_app, tmp_settings, make_client
):
    app, _ = ws_app
    # An invalid or missing credential closes after accept with the actionable code.
    from maa_api.settings import Settings, set_settings

    set_settings(Settings(access_token="ws-secret"))
    client = make_client(app)
    with pytest.raises(WebSocketDisconnect) as missing:
        with client.websocket_connect("/api/ws") as socket:
            socket.receive_json()
    assert missing.value.code == 4401

    with client.websocket_connect("/api/ws?token=ws-secret") as socket:
        socket.send_json({"type": "ping", "req_id": "p-1", "data": {"t": 12.5}})
        pong = socket.receive_json()
        assert pong["type"] == "pong"
        assert pong["req_id"] == "p-1" and pong["data"] == {"t": 12.5}
        socket.send_json({"type": "subscibe", "req_id": "bad", "data": {}})
        error = socket.receive_json()
        assert error["type"] == "error"
        assert error["req_id"] == "bad"
        assert error["data"]["code"] == "WS_BAD_MESSAGE"
        # A protocol error is recoverable; the connection remains usable.
        socket.send_json({"type": "ping", "data": {"t": 9}})
        assert socket.receive_json()["type"] == "pong"

    with client.websocket_connect(
        "/api/ws",
        cookies={"maa_token": "ws-secret"},
        headers={"origin": "http://localhost:8002"},
    ) as socket:
        socket.send_json({"type": "ping", "data": {"t": 4}})
        assert socket.receive_json()["data"]["t"] == 4

    for headers in ({}, {"origin": "https://evil.example"}):
        with pytest.raises(WebSocketDisconnect) as rejected:
            with client.websocket_connect(
                "/api/ws", cookies={"maa_token": "ws-secret"}, headers=headers
            ) as socket:
                socket.receive_json()
        assert rejected.value.code == 4403


def test_subscribe_replaces_filter_and_backfills_only_matching_records(
    ws_app, tmp_settings, make_client
):
    app, _ = ws_app
    hub = LogHub(ring_size=5)
    hub.offer(_record("task", "task row"))
    hub.offer(_record("core", "core row"))
    set_log_hub(hub)

    with make_client(app).websocket_connect("/api/ws") as socket:
        socket.send_json({
            "type": "subscribe",
            "req_id": "s-1",
            "data": {
                "channels": ["log", "core_status"],
                "log_filter": {"sources": ["task"], "min_level": "INFO"},
                "last_seen_id": 0,
            },
        })
        batch = socket.receive_json()
        assert batch["type"] == "log_batch"
        assert [row["content"] for row in batch["data"]["records"]] == ["task row"]
        assert batch["data"]["truncated"] is False
        subscribed = socket.receive_json()
        assert subscribed["type"] == "subscribed"
        assert subscribed["req_id"] == "s-1"
        assert subscribed["data"]["backfilled"] == 1

        # A later subscribe replaces channels and filters as one state update.
        socket.send_json({
            "type": "subscribe",
            "req_id": "s-2",
            "data": {
                "channels": ["core_status"],
                "log_filter": {"sources": ["core"], "min_level": "ERROR"},
                "last_seen_id": 0,
            },
        })
        assert socket.receive_json()["type"] == "subscribed"
        assert ws_api.manager.sessions[0].channels == {"core_status"}
        assert ws_api.manager.sessions[0].log_filter.sources == ("core",)
        ws_api.manager.broadcast("core_status", {"state": "ready"})
        assert socket.receive_json()["type"] == "core_status"


def test_replay_marks_records_truncated_when_cursor_predates_ring(
    ws_app, tmp_settings, make_client
):
    app, _ = ws_app
    hub = LogHub(ring_size=2)
    for idx in range(3):
        hub.offer(_record("task", f"row-{idx}"))
    set_log_hub(hub)

    with make_client(app).websocket_connect("/api/ws?last_seen_id=0") as socket:
        batch = socket.receive_json()
        assert batch["type"] == "log_batch"
        assert batch["data"]["truncated"] is True
        assert [r["content"] for r in batch["data"]["records"]] == ["row-1", "row-2"]
        assert socket.receive_json()["type"] == "subscribed"


def test_slow_client_is_closed_without_affecting_other_sessions():
    class FakeSocket:
        def __init__(self):
            self.closed_with = None

        async def close(self, *, code, reason=""):
            self.closed_with = (code, reason)

    async def run():
        manager = ws_api.ConnectionManager(queue_size=1)
        manager._loop = asyncio.get_running_loop()
        slow_ws, healthy_ws = FakeSocket(), FakeSocket()
        slow = ws_api.ClientSession(ws=slow_ws, send_queue=asyncio.Queue(maxsize=1))
        healthy = ws_api.ClientSession(ws=healthy_ws, send_queue=asyncio.Queue(maxsize=1))
        slow.channels = {"core_status"}
        healthy.channels = {"core_status"}
        manager._sessions = {slow.id: slow, healthy.id: healthy}
        slow.send_queue.put_nowait({"occupied": True})
        manager.broadcast("core_status", {"state": "ready"})
        assert healthy.send_queue.qsize() == 1
        await asyncio.sleep(0)
        assert slow_ws.closed_with[0] == 1011
        assert slow.id not in {item.id for item in manager.sessions}
        assert healthy.id in {item.id for item in manager.sessions}

    asyncio.run(run())


def test_server_heartbeat_closes_client_after_two_unanswered_pings():
    class FakeSocket:
        def __init__(self):
            self.messages = []
            self.closed_with = None

        async def send_json(self, message):
            self.messages.append(message)

        async def close(self, *, code, reason=""):
            self.closed_with = (code, reason)

    async def run():
        manager = ws_api.ConnectionManager(heartbeat_interval=0.01, inactivity_timeout=1)
        manager._loop = asyncio.get_running_loop()
        ws = FakeSocket()
        session = ws_api.ClientSession(ws=ws)
        manager._sessions[session.id] = session
        sender = asyncio.create_task(manager._sender(session))
        session.sender_task = sender
        heartbeat = asyncio.create_task(manager._heartbeat(session))
        session.heartbeat_task = heartbeat
        await asyncio.wait_for(heartbeat, timeout=0.2)
        assert [item["type"] for item in ws.messages] == ["server_ping", "server_ping"]
        assert ws.closed_with == (1001, "heartbeat_timeout")
        assert not manager.sessions

    asyncio.run(run())


def test_client_inactivity_closes_session_even_without_log_traffic():
    class FakeSocket:
        def __init__(self):
            self.closed_with = None

        async def close(self, *, code, reason=""):
            self.closed_with = (code, reason)

    async def run():
        manager = ws_api.ConnectionManager(heartbeat_interval=0.01, inactivity_timeout=0.025)
        manager._loop = asyncio.get_running_loop()
        ws = FakeSocket()
        session = ws_api.ClientSession(ws=ws)
        manager._sessions[session.id] = session
        heartbeat = asyncio.create_task(manager._heartbeat(session))
        session.heartbeat_task = heartbeat
        await asyncio.wait_for(heartbeat, timeout=0.2)
        assert ws.closed_with == (1001, "client_inactive")
        assert not manager.sessions

    asyncio.run(run())


def test_connection_limit_uses_4429_and_broadcast_channel_filter():
    class FakeSocket:
        def __init__(self):
            self.closed_with = None
            self.messages = []

        async def accept(self):
            return None

        async def send_json(self, message):
            self.messages.append(message)

        async def close(self, *, code, reason=""):
            self.closed_with = (code, reason)

    async def run():
        manager = ws_api.ConnectionManager(max_connections=1)
        manager._loop = asyncio.get_running_loop()
        first_socket, rejected_socket = FakeSocket(), FakeSocket()
        session = await manager.connect(first_socket)
        assert session is not None
        assert await manager.connect(rejected_socket) is None
        assert rejected_socket.closed_with[0] == 4429

        session.channels = {"core_status"}
        manager.broadcast("core_status", {"state": "ready"}, channels={"core_status"})
        manager.broadcast("device_status", {"state": "connected"}, channels={"core_status"})
        await asyncio.wait_for(session.send_queue.join(), timeout=0.2)
        assert [message["type"] for message in first_socket.messages] == ["core_status"]
        await manager.disconnect(session)

    asyncio.run(run())
