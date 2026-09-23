"""实时日志 WebSocket：订阅、过滤、补发与慢客户端隔离。"""

from __future__ import annotations

import asyncio
import logging
import time
import uuid
from dataclasses import asdict, dataclass, field
from typing import Any

from fastapi import APIRouter, WebSocket, WebSocketDisconnect

from maa_api.api.deps import TokenChannel, auth_enabled, extract_token
from maa_api.services.log_hub import LEVEL_ORDER, LogHub, LogRecord, get_log_hub
from maa_api.settings import get_settings

__all__ = ["CHANNELS", "ClientSession", "ConnectionManager", "LogFilter", "manager", "router"]

router = APIRouter(prefix="/api", tags=["ws"])
logger = logging.getLogger(__name__)

CHANNELS = frozenset(
    {
        "log",
        "pipeline_status",
        "task_status",
        "queue_changed",
        "core_status",
        "device_status",
        "confirm_request",
        "confirm_resolved",
        "update_progress",
        "update_available",
        "agent_event",
        "server_shutdown",
    }
)
LOG_SOURCES = frozenset({"task", "service", "core"})


@dataclass(frozen=True, slots=True)
class LogFilter:
    """Server-side log subscription predicate."""

    sources: tuple[str, ...] | None = None
    min_level: str = "DEBUG"
    pipeline_id: str | None = None

    def accept(self, record: LogRecord) -> bool:
        if self.sources is not None and record.source not in self.sources:
            return False
        if LEVEL_ORDER.get(record.level.upper(), 0) < LEVEL_ORDER.get(
            self.min_level.upper(), 0
        ):
            return False
        return self.pipeline_id is None or record.pipeline_id == self.pipeline_id

    def cache_key(self) -> tuple[Any, ...]:
        return (self.sources, self.min_level.upper(), self.pipeline_id)


@dataclass(slots=True)
class ClientSession:
    """One socket and its bounded producer/consumer queue."""

    ws: WebSocket
    id: str = field(default_factory=lambda: uuid.uuid4().hex)
    send_queue: asyncio.Queue[dict[str, Any]] = field(
        default_factory=lambda: asyncio.Queue(maxsize=500)
    )
    channels: set[str] = field(default_factory=set)
    log_filter: LogFilter = field(default_factory=LogFilter)
    last_pong: float = field(default_factory=time.monotonic)
    sender_task: asyncio.Task[None] | None = None
    invalid_messages: int = 0
    closed: bool = False
    send_lock: asyncio.Lock = field(default_factory=asyncio.Lock)


class ConnectionManager:
    """Manage socket sessions and publish without blocking log producers."""

    def __init__(self, *, queue_size: int = 500, max_connections: int = 8) -> None:
        self.queue_size = max(int(queue_size), 1)
        self.max_connections = max(int(max_connections), 1)
        self._sessions: dict[str, ClientSession] = {}
        self._loop: asyncio.AbstractEventLoop | None = None
        self._hub: LogHub | None = None

    @property
    def sessions(self) -> tuple[ClientSession, ...]:
        return tuple(self._sessions.values())

    def bind_hub(self, hub: LogHub | None) -> None:
        if hub is self._hub:
            return
        if self._hub is not None:
            self._hub.remove_sink(self)
        self._hub = hub
        if hub is not None:
            hub.add_sink(self)

    async def connect(self, websocket: WebSocket) -> ClientSession | None:
        self._loop = asyncio.get_running_loop()
        await websocket.accept()
        if len(self._sessions) >= self.max_connections:
            await websocket.close(code=4429, reason="connection_limit")
            return None
        session = ClientSession(
            ws=websocket,
            send_queue=asyncio.Queue(maxsize=self.queue_size),
        )
        self._sessions[session.id] = session
        session.sender_task = asyncio.create_task(
            self._sender(session), name=f"maa-api-ws-sender-{session.id[:8]}"
        )
        return session

    async def disconnect(self, session: ClientSession) -> None:
        if session.closed:
            return
        session.closed = True
        self._sessions.pop(session.id, None)
        task = session.sender_task
        session.sender_task = None
        if task is not None and task is not asyncio.current_task():
            task.cancel()
            try:
                await task
            except asyncio.CancelledError:
                pass

    async def close_all(self, code: int = 1001, reason: str = "server_shutdown") -> None:
        """Notify each client and close it, tolerating already-dead sockets."""
        sessions = list(self._sessions.values())
        for session in sessions:
            self._enqueue(session, self._envelope("server_shutdown", {}, time.time()))
        if sessions:
            try:
                await asyncio.wait_for(
                    asyncio.gather(*(s.send_queue.join() for s in sessions)), timeout=1.0
                )
            except TimeoutError:
                pass
        for session in sessions:
            try:
                await session.ws.close(code=code, reason=reason)
            except Exception:
                pass
            await self.disconnect(session)

    def put_log(self, record: LogRecord) -> None:
        """LogHub sink callback; the hub invokes sinks on its owning loop."""
        self.broadcast_log(record)

    def broadcast_log(self, record: LogRecord) -> None:
        payload = self._envelope("log", self._record_data(record), record.ts)
        for session in tuple(self._sessions.values()):
            if "log" in session.channels and session.log_filter.accept(record):
                self._enqueue(session, payload)

    def broadcast(self, message_type: str, data: Any, *, ts: float | None = None) -> None:
        """Publish another protocol event; channel filtering remains per session."""
        envelope = self._envelope(message_type, data, time.time() if ts is None else ts)
        for session in tuple(self._sessions.values()):
            if message_type == "server_shutdown" or message_type in session.channels:
                self._enqueue(session, envelope)

    def _enqueue(self, session: ClientSession, envelope: dict[str, Any]) -> None:
        if session.closed:
            return
        try:
            session.send_queue.put_nowait(envelope)
        except asyncio.QueueFull:
            loop = self._loop
            if loop is not None and not loop.is_closed():
                loop.create_task(self._drop_slow(session))

    async def _drop_slow(self, session: ClientSession) -> None:
        if session.closed:
            return
        try:
            await session.ws.close(code=1011, reason="slow_client")
        except Exception:
            pass
        await self.disconnect(session)

    async def _sender(self, session: ClientSession) -> None:
        try:
            while True:
                message = await session.send_queue.get()
                try:
                    async with session.send_lock:
                        await session.ws.send_json(message)
                finally:
                    session.send_queue.task_done()
        except (WebSocketDisconnect, asyncio.CancelledError):
            raise
        except Exception:
            await self.disconnect(session)

    @staticmethod
    def _record_data(record: LogRecord) -> dict[str, Any]:
        return {
            "id": record.id,
            "source": record.source,
            "level": record.level,
            "content": record.content,
            "pipeline_id": record.pipeline_id,
            "task_id": record.task_id,
            "logger": record.logger,
            "attachment": record.attachment,
        }

    @staticmethod
    def _envelope(message_type: str, data: Any, ts: float) -> dict[str, Any]:
        return {"type": message_type, "ts": ts, "data": data}


manager = ConnectionManager()


def _error(code: str, message: str, req_id: Any = None) -> dict[str, Any]:
    result: dict[str, Any] = {
        "type": "error",
        "ts": time.time(),
        "data": {"code": code, "message": message},
    }
    if req_id is not None:
        result["req_id"] = req_id
    return result


async def _send_direct(session: ClientSession, message: dict[str, Any]) -> None:
    try:
        async with session.send_lock:
            await session.ws.send_json(message)
    except Exception:
        await manager.disconnect(session)


def _record_wire(record: LogRecord) -> dict[str, Any]:
    return {"id": record.id, **manager._record_data(record)}


async def _backfill(
    session: ClientSession, hub: LogHub | None, cursor: int | None
) -> tuple[int, bool]:
    if cursor is None or hub is None or "log" not in session.channels:
        return 0, False
    records, truncated = hub.snapshot_after(cursor)
    records = [record for record in records if session.log_filter.accept(record)]
    if records or truncated:
        await _send_direct(
            session,
            {"type": "log_batch", "ts": time.time(), "data": {
                "records": [_record_wire(record) for record in records],
                "truncated": truncated,
            }},
        )
    return len(records), truncated


def _parse_filter(data: dict[str, Any]) -> LogFilter:
    raw = data.get("log_filter") or {}
    if not isinstance(raw, dict):
        raise ValueError("log_filter 必须是对象")
    sources_value = raw.get("sources")
    if sources_value is None:
        sources = None
    elif isinstance(sources_value, list) and all(
        isinstance(item, str) and item in LOG_SOURCES for item in sources_value
    ):
        sources = tuple(dict.fromkeys(sources_value))
    else:
        raise ValueError("sources 必须是 task/service/core 数组")
    minimum = str(raw.get("min_level", "DEBUG")).upper()
    if minimum == "WARN":
        minimum = "WARNING"
    if minimum not in LEVEL_ORDER:
        raise ValueError("min_level 无效")
    pipeline_id = raw.get("pipeline_id")
    if pipeline_id is not None and not isinstance(pipeline_id, str):
        raise ValueError("pipeline_id 必须是字符串或 null")
    return LogFilter(sources=sources, min_level=minimum, pipeline_id=pipeline_id)


async def _handle_message(
    session: ClientSession,
    message: Any,
    hub: LogHub | None,
    *,
    first_subscribe: list[bool],
) -> None:
    if not isinstance(message, dict) or not isinstance(message.get("type"), str):
        await _send_direct(session, _error("WS_BAD_MESSAGE", "消息必须包含 type 字符串"))
        session.invalid_messages += 1
        return
    message_type = message["type"]
    req_id = message.get("req_id")
    data = message.get("data") or {}
    if not isinstance(data, dict):
        await _send_direct(session, _error("WS_BAD_MESSAGE", "data 必须是对象", req_id))
        session.invalid_messages += 1
        return

    if message_type == "ping":
        session.last_pong = time.monotonic()
        await _send_direct(
            session,
            {"type": "pong", "req_id": req_id, "ts": time.time(), "data": {"t": data.get("t")}},
        )
        return

    if message_type == "subscribe":
        channels = data.get("channels", ["log"])
        if not isinstance(channels, list) or any(
            not isinstance(item, str) or item not in CHANNELS for item in channels
        ):
            await _send_direct(session, _error("WS_BAD_MESSAGE", "channels 含未知频道", req_id))
            session.invalid_messages += 1
            return
        try:
            log_filter = _parse_filter(data)
        except ValueError as exc:
            await _send_direct(session, _error("WS_BAD_MESSAGE", str(exc), req_id))
            session.invalid_messages += 1
            return
        session.channels = set(channels)
        session.log_filter = log_filter
        cursor: int | None = None
        if first_subscribe[0]:
            first_subscribe[0] = False
            raw_cursor = data.get("last_seen_id")
            if raw_cursor is not None:
                try:
                    cursor = max(int(raw_cursor), 0)
                except (TypeError, ValueError):
                    await _send_direct(session, _error("WS_BAD_MESSAGE", "last_seen_id 必须为整数", req_id))
                    session.invalid_messages += 1
                    return
        backfilled, truncated = await _backfill(session, hub, cursor)
        await _send_direct(
            session,
            {"type": "subscribed", "req_id": req_id, "ts": time.time(), "data": {
                "channels": sorted(session.channels),
                "backfilled": backfilled,
                "truncated": truncated,
            }},
        )
        return

    if message_type == "unsubscribe":
        channels = data.get("channels", [])
        if not isinstance(channels, list) or any(not isinstance(c, str) for c in channels):
            await _send_direct(session, _error("WS_BAD_MESSAGE", "channels 必须是字符串数组", req_id))
            session.invalid_messages += 1
            return
        session.channels.difference_update(channels)
        await _send_direct(
            session,
            {"type": "subscribed", "req_id": req_id, "ts": time.time(), "data": {
                "channels": sorted(session.channels), "backfilled": 0, "truncated": False,
            }},
        )
        return

    await _send_direct(session, _error("WS_BAD_MESSAGE", f"未知的消息类型: {message_type}", req_id))
    session.invalid_messages += 1


@router.websocket("/ws")
async def websocket_logs(websocket: WebSocket) -> None:
    """Authenticate, attach to the shared hub and serve protocol messages."""
    hit = extract_token(websocket)
    configured = auth_enabled()
    allowed_channel = hit is None or hit.channel in {TokenChannel.QUERY, TokenChannel.COOKIE}
    if configured and (
        hit is None or not allowed_channel or hit.value != get_settings().access_token
    ):
        await websocket.accept()
        await websocket.close(code=4401, reason="unauthorized")
        return

    current_manager = manager
    hub = get_log_hub()
    current_manager.bind_hub(hub)
    session = await current_manager.connect(websocket)
    if session is None:
        return
    first_subscribe = [True]
    query_cursor = websocket.query_params.get("last_seen_id")
    if query_cursor is not None:
        session.channels = {"log"}
        try:
            backfilled, truncated = await _backfill(session, hub, max(int(query_cursor), 0))
        except ValueError:
            await _send_direct(session, _error("WS_BAD_MESSAGE", "last_seen_id 必须为整数"))
        else:
            await _send_direct(session, {"type": "subscribed", "ts": time.time(), "data": {
                "channels": ["log"], "backfilled": backfilled, "truncated": truncated,
            }})
        first_subscribe[0] = False

    try:
        while True:
            try:
                message = await websocket.receive_json()
            except ValueError:
                await _send_direct(session, _error("WS_BAD_MESSAGE", "消息不是合法 JSON"))
                session.invalid_messages += 1
            else:
                await _handle_message(session, message, hub, first_subscribe=first_subscribe)
            if session.invalid_messages >= 10:
                await websocket.close(code=1008, reason="too_many_invalid_messages")
                break
    except WebSocketDisconnect:
        pass
    finally:
        await current_manager.disconnect(session)
