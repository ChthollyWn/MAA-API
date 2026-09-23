"""M4-10 application lifespan and CoreClient log wiring contracts."""

from __future__ import annotations

import logging
import sqlite3
import asyncio
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

import maa_api.api.ws as ws_api
import maa_api.db.session as db_session
import maa_api.main as main_module
from maa_api.core.enums import Message
from maa_api.services.log_hub import LogHub, get_log_hub, set_log_hub
from maa_api.services.log_wiring import install_core_logging
from maa_api.settings import Settings, set_settings


def test_app_lifespan_starts_collectors_flushes_and_closes_ws(
    tmp_settings, isolated_db, monkeypatch
):
    tail_root = Path(isolated_db.url.database).parent / "missing-core"
    set_settings(
        Settings(
            maa_core_path=str(tail_root),
            adb=tmp_settings.adb,
            log=tmp_settings.log,
        )
    )
    monkeypatch.setattr(
        main_module,
        "load_settings",
        lambda: Settings(
            maa_core_path=str(tail_root),
            adb=tmp_settings.adb,
            log=tmp_settings.log,
        ),
    )
    application = main_module.create_app()
    client = TestClient(application, raise_server_exceptions=False)
    client.__enter__()

    hub = application.state.log_hub
    assert hub is get_log_hub()
    assert hub._started is True
    # Lifespan starts the tailer without creating a missing asst.log path.
    assert not (tail_root / "debug" / "asst.log").exists()

    logging.getLogger("maa_api.m4_lifespan_test").warning(
        "GET /api/ws?token=never-a-secret&x=1 HTTP/1.1 lifespan service record"
    )
    service_record = next(
        row for row in reversed(hub._ring)
        if row.source == "service" and row.logger == "maa_api.m4_lifespan_test"
    )
    assert "never-a-secret" not in service_record.content
    assert "token=***" in service_record.content

    client.__exit__(None, None, None)
    with sqlite3.connect(db_session.DB_PATH) as connection:
        row = connection.execute(
            "SELECT source, level, content, meta FROM log_entry "
            "WHERE content LIKE '%lifespan service record%'"
        ).fetchone()
    assert row is not None and row[0:2] == ("server", "warning")
    assert get_log_hub() is None
    assert not hasattr(application.state, "log_hub")
    assert not (tail_root / "debug").exists()


def test_manager_close_all_sends_shutdown_then_closes_1001():
    class FakeSocket:
        def __init__(self):
            self.messages = []
            self.closed_with = None

        async def send_json(self, message):
            self.messages.append(message)

        async def close(self, *, code, reason=""):
            self.closed_with = (code, reason)

    async def scenario():
        manager = ws_api.ConnectionManager()
        manager._loop = asyncio.get_running_loop()
        ws = FakeSocket()
        session = ws_api.ClientSession(ws=ws)
        session.sender_task = asyncio.create_task(manager._sender(session))
        manager._sessions[session.id] = session
        await manager.close_all(1001, "server_shutdown")
        assert [message["type"] for message in ws.messages] == ["server_shutdown"]
        assert ws.closed_with == (1001, "server_shutdown")
        assert not manager.sessions

    asyncio.run(scenario())


def test_install_core_logging_bridges_log_and_callbacks(tmp_settings):
    class FakeClient:
        def __init__(self):
            self.handlers = {}

        def on(self, event_type, handler):
            self.handlers[event_type] = handler

    hub = LogHub()
    client = FakeClient()
    translator = install_core_logging(client, hub)
    assert translator is not None
    client.handlers["LOG"]({
        "level": "WARNING",
        "content": "native python bridge",
        "logger": "maa_api.core.worker",
        "ts": 10.0,
    })
    client.handlers["CALLBACK"]({
        "msg": int(Message.SubTaskExtraInfo),
        "details": {
            "what": "SanityBeforeStage",
            "details": {"current_sanity": 120, "max_sanity": 135},
        },
    })
    records = {record.source: record for record in hub._ring}
    assert records["service"].content == "native python bridge"
    assert records["service"].logger == "core_worker.maa_api.core.worker"
    assert records["task"].content == "当前理智：120/135"

    with pytest.raises(TypeError, match=r"on\(event_type, handler\)"):
        install_core_logging(object(), hub)
