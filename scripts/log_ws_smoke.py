#!/usr/bin/env python3
"""M4-11 isolated end-to-end log, REST and WebSocket smoke.

The default run uses the real app/lifespan and database with an isolated
temporary root. Task events go through a spawned FakeAsst worker, CoreClient,
CallbackTranslator, LogHub and a TestClient WebSocket. A temporary native log
file exercises the real tailer; stdlib logging exercises the service source.
"""

from __future__ import annotations

import argparse
import asyncio
import base64
import contextlib
import errno
import hashlib
import json
import os
import platform
import shutil
import socket
import struct
import subprocess
import sys
import tempfile
import time
import traceback
import urllib.error
import urllib.request
from datetime import datetime
from pathlib import Path
from typing import Any

REPO_ROOT = Path(__file__).resolve().parents[1]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

TOKEN = "maa-log-ws-smoke-token"
DEFAULT_PORT = 8124
WORKER_NAME = "maa-core"


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="log_ws_smoke.py",
        description="M4 三路日志 → WebSocket + 历史查询/补发/截图附件全链路冒烟。",
        epilog=(
            "默认模式不联网、不加载 MaaCore、不依赖 websockets 包。\n"
            "--serve 追加真实 uvicorn/socket WebSocket 握手；--real-core 追加真机连接与任务。"
        ),
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    parser.add_argument("--serve", action="store_true", help="追加真实 uvicorn + 标准库 socket WS 握手")
    parser.add_argument("--port", type=int, default=DEFAULT_PORT, help=f"--serve 监听端口，默认 {DEFAULT_PORT}")
    parser.add_argument("--real-core", action="store_true", help="追加真实 MaaCore + 127.0.0.1:5555 冒烟")
    parser.add_argument("--maa-path", default=None, help="--real-core 内核目录；默认使用 app.maa_core_path")
    parser.add_argument("--address", default="127.0.0.1:5555", help="--real-core 设备地址")
    parser.add_argument("--timeout", type=float, default=90.0, help="--real-core 各阶段超时")
    parser.add_argument("--keep-temp", action="store_true", help="保留隔离目录用于排查")
    return parser


class SmokeFailure(RuntimeError):
    pass


class LogWsSmoke:
    def __init__(self, args: argparse.Namespace) -> None:
        self.args = args
        self.started = time.monotonic()
        self.temp_root: Path | None = None
        self.engine = None
        self.session_factory = None
        self.app = None
        self.client = None
        self.hub = None
        self.settings_module = None
        self.db_session = None
        self.saved_env: dict[str, str] = {}
        self.checks: list[tuple[str, str]] = []
        self.failures: list[str] = []
        self.repository_snapshot: dict[str, tuple[int, int, str]] = {}

    @staticmethod
    def step(message: str) -> None:
        print(f"[log_ws_smoke] {message}", flush=True)

    def ok(self, name: str, detail: str = "") -> None:
        self.checks.append((name, detail))
        print(f"[log_ws_smoke] [OK] {name}{': ' + detail if detail else ''}", flush=True)

    def fail(self, name: str, detail: str) -> None:
        self.failures.append(f"{name}: {detail}")
        print(f"[log_ws_smoke] [FAIL] {name}: {detail}", flush=True)

    def check(self, name: str, fn) -> None:
        try:
            detail = fn()
        except Exception as exc:  # noqa: BLE001 - report each actionable failure
            self.fail(name, f"{type(exc).__name__}: {exc}")
            traceback.print_exc()
        else:
            self.ok(name, "" if detail is None else str(detail))

    def _prepare(self) -> None:
        self.repository_snapshot = self._snapshot_repository_artifacts()
        self.temp_root = Path(tempfile.mkdtemp(prefix="maa-log-ws-smoke-")).resolve()
        resource = self.temp_root / "resource"
        resource.mkdir(parents=True)
        db_path = resource / "maa_api.db"
        core_root = self.temp_root / "core"
        (core_root / "debug").mkdir(parents=True)
        config_path = self.temp_root / "config.yaml"
        config_path.write_text(
            "app:\n"
            f"  access_token: {json.dumps(TOKEN)}\n"
            f"  maa_core_path: {json.dumps(str(core_root))}\n"
            "adb:\n"
            "  path: /usr/bin/adb\n"
            "  address: 127.0.0.1:5555\n"
            "  screenshot_quality: 72\n"
            "log:\n"
            "  ring_size: 64\n"
            "  batch_size: 8\n"
            "  flush_interval: 0.05\n"
            "  core_min_level: TRC\n"
            "  persist_maacore_debug_level: WARNING\n",
            encoding="utf-8",
        )

        import maa_api.db.session as db_session
        import maa_api.settings as settings_module
        from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker
        from sqlalchemy.pool import NullPool

        configured_core_path = settings_module.load_settings().maa_core_path
        self.real_core_path = Path(
            self.args.maa_path
            or configured_core_path
            or REPO_ROOT / "resource" / "lib" / "maa" / platform.system()
        ).expanduser().resolve()
        self.saved_env = {key: value for key, value in os.environ.items() if key.startswith("MAA_")}
        for key in list(os.environ):
            if key.startswith("MAA_"):
                os.environ.pop(key, None)

        self.db_session = db_session
        self.settings_module = settings_module
        db_session.DB_PATH = db_path
        db_session.SYNC_URL = f"sqlite:///{db_path}"
        db_session.ASYNC_URL = f"sqlite+aiosqlite:///{db_path}"
        self.engine = db_session.make_engine(db_session.ASYNC_URL, poolclass=NullPool)
        self.session_factory = async_sessionmaker(
            self.engine, expire_on_commit=False, class_=AsyncSession
        )
        db_session.engine = self.engine
        db_session.session_factory = self.session_factory
        settings_module.DEFAULT_CONFIG_PATH = config_path
        settings_module.set_settings(settings_module.load_settings(config_path))

        import maa_api.main as main_module

        main_module.load_settings = lambda: settings_module.load_settings(config_path)
        self.app = main_module.create_app()
        self.client = main_module  # retain module for routes and app references
        self.step(f"隔离目录：{self.temp_root}")

    @staticmethod
    def _snapshot_repository_artifacts() -> dict[str, tuple[int, int, str]]:
        """Fingerprint mutable runtime files the smoke must leave untouched."""
        candidates = [
            REPO_ROOT / "config.yaml",
            REPO_ROOT / "resource" / "maa_api.db",
            REPO_ROOT / "resource" / "maa_api.db-wal",
            REPO_ROOT / "resource" / "maa_api.db-shm",
        ]
        for directory in (
            REPO_ROOT / "resource" / "log",
            REPO_ROOT / "resource" / "image" / "screenshot",
        ):
            if directory.is_dir():
                candidates.extend(path for path in directory.rglob("*") if path.is_file())
        snapshot: dict[str, tuple[int, int, str]] = {}
        for path in candidates:
            if not path.is_file():
                continue
            stat = path.stat()
            digest = hashlib.sha256(path.read_bytes()).hexdigest()
            snapshot[path.relative_to(REPO_ROOT).as_posix()] = (
                stat.st_size,
                stat.st_mtime_ns,
                digest,
            )
        return snapshot

    def _receive_ws(self, websocket, *, timeout: float = 8.0) -> dict[str, Any]:
        """Bounded receive using the TestClient portal's ASGI stream."""
        import asyncio
        from starlette.websockets import WebSocketDisconnect

        async def receive():
            return await asyncio.wait_for(websocket._send_rx.receive(), timeout=timeout)

        message = self.client_portal.call(receive)
        if message.get("type") == "websocket.close":
            raise WebSocketDisconnect(
                code=message.get("code", 1000), reason=message.get("reason", "")
            )
        if message.get("type") != "websocket.send":
            raise SmokeFailure(f"unexpected WS ASGI message: {message!r}")
        return json.loads(message.get("text", "{}"))

    def _wait_hub(self, predicate, *, timeout: float = 8.0) -> Any:
        async def wait():
            deadline = asyncio.get_running_loop().time() + timeout
            while asyncio.get_running_loop().time() < deadline:
                for record in reversed(self.hub._ring):
                    if predicate(record):
                        return record
                await asyncio.sleep(0.025)
            raise TimeoutError("LogHub did not observe expected record")

        self.step("等待 LogHub 观察源记录")
        record = self.client_portal.call(wait)
        self.step(f"LogHub 已观察：source={record.source} content={record.content[:48]!r}")
        return record

    def _assert_auth_rejection(self) -> str:
        from starlette.websockets import WebSocketDisconnect

        try:
            with self.http.websocket_connect("/api/ws") as websocket:
                websocket.receive_json()
        except WebSocketDisconnect as exc:
            if exc.code != 4401:
                raise AssertionError(f"expected close 4401, got {exc.code}") from exc
            return "missing token closes with 4401"
        raise AssertionError("unauthenticated websocket unexpectedly connected")

    def _collect_core_task(self, *, real: bool = False) -> list[str]:
        from maa_api.core.client import CoreClient
        from maa_api.core.enums import Message
        from maa_api.core.supervisor import CoreSupervisor
        from maa_api.services.log_wiring import install_core_logging

        terminal_names = {"TaskChainCompleted", "TaskChainError", "TaskChainStopped"}
        callbacks: list[str] = []

        async def run() -> list[str]:
            loop = asyncio.get_running_loop()
            terminal = asyncio.Event()
            if real:
                core_path = self.real_core_path
                if not core_path.is_dir():
                    raise SmokeFailure(f"MaaCore 目录不存在：{core_path}")
                factory = "maa_api.core.asst:Asst"
                factory_kwargs = {}
            else:
                core_path = REPO_ROOT
                factory = "tests.fakes.fake_asst:FakeAsst"
                factory_kwargs = {"script": "success"}
            boot_config = {
                "maa_path": str(core_path),
                "user_dir": str(self.temp_root / ("real-core-user" if real else "fake-core-user")),
                "incremental_paths": [],
                "instance_options": {},
                "asst_factory": factory,
                "asst_factory_kwargs": factory_kwargs,
            }
            supervisor = CoreSupervisor(
                boot_config,
                backoff=(0.05,),
                heartbeat_interval=2.0,
                heartbeat_failures=3,
            )
            core_client = CoreClient(
                supervisor,
                connect_timeout=min(float(self.args.timeout), 30.0),
                accept_timeout=min(float(self.args.timeout), 15.0),
                screencap_dir=self.temp_root / "core-images",
            )
            install_core_logging(core_client, self.hub)

            def on_callback(payload: dict[str, Any]) -> None:
                try:
                    name = Message(int(payload.get("msg"))).name
                except (TypeError, ValueError):
                    return
                callbacks.append(name)
                if name in terminal_names:
                    terminal.set()

            core_client.on("CALLBACK", on_callback)
            core_client.start_consumer()
            try:
                await supervisor.start(wait_ready=True, timeout=min(float(self.args.timeout), 30.0))
                if real:
                    connected = await core_client.connect(
                        self.settings_module.get_settings().adb.path,
                        self.args.address,
                        "General",
                        timeout=min(float(self.args.timeout), 60.0),
                    )
                    if not connected:
                        raise SmokeFailure("真实 MaaCore connect 返回 False")
                task_id = await core_client.append_task("Award", {})
                if not task_id:
                    raise SmokeFailure("APPEND_TASK(Award) 返回 0")
                if not await core_client.start():
                    raise SmokeFailure("CoreClient.start() 返回 False")
                await asyncio.wait_for(
                    terminal.wait(), timeout=min(float(self.args.timeout), 90.0)
                )
                # Let the callback consumer dispatch the final queued events.
                await asyncio.sleep(0.05)
                return list(callbacks)
            finally:
                with contextlib.suppress(Exception):
                    await core_client.stop()
                with contextlib.suppress(Exception):
                    await supervisor.stop(graceful=True, timeout=5.0)
                with contextlib.suppress(Exception):
                    core_client.close()

        return self.client_portal.call(run)

    def _seed_screenshot(self) -> tuple[str, dict[str, Any]]:
        from PIL import Image
        from maa_api.db.models import Screenshot
        from maa_api.db.repositories.log import ScreenshotRepository
        from maa_api.domain.enums import ScreenshotBackend, ScreenshotTrigger
        from maa_api.services.log_hub import LogRecord
        from maa_api.util.image import store_screenshot

        attachment = store_screenshot(Image.new("RGB", (640, 360), (16, 72, 140)), quality=72)
        relative_path = (
            f"image/screenshot/{attachment['sha256'][:2]}/{attachment['sha256']}.jpg"
        )

        async def create():
            async with self.db_session.session_factory() as session:
                screenshot = await ScreenshotRepository(session).create(
                    Screenshot(
                        trigger=ScreenshotTrigger.AGENT,
                        backend=ScreenshotBackend.ADB,
                        path=relative_path,
                        format="jpeg",
                        width=attachment["width"],
                        height=attachment["height"],
                        size_bytes=attachment["bytes"],
                    )
                )
                await session.commit()
                return screenshot.id

        screenshot_id = self.client_portal.call(create)
        self.hub.offer(
            LogRecord(
                ts=time.time(),
                source="task",
                level="ERROR",
                content="M4 smoke attachment marker",
                attachment=attachment,
            )
        )
        return screenshot_id, attachment

    def _core_file_write(self) -> str:
        path = self.temp_root / "core" / "debug" / "asst.log"
        self.client_portal.call(asyncio.sleep, 0.3)  # allow tailer to open at EOF first
        stamp = datetime.now().strftime("%Y-%m-%d %H:%M:%S.%f")[:-3]
        lines = [
            f"[{stamp}][ERR][Px100][Tx200] M4 smoke core source marker one",
            f"[{stamp}][ERR][Px100][Tx201] M4 smoke core source marker two",
            f"[{stamp}][ERR][Px100][Tx202] M4 smoke core source marker three",
        ]
        with path.open("a", encoding="utf-8") as stream:
            stream.write("\n".join(lines) + "\n")
        return str(path)

    def _api(self, method: str, path: str, **kwargs):
        headers = dict(kwargs.pop("headers", {}))
        headers["X-Token"] = TOKEN
        return getattr(self.http, method)(path, headers=headers, **kwargs)

    def _live_pipeline(self) -> None:
        from maa_api.services.log_hub import LogRecord

        # Missing token must be distinguishable from a missing route.
        self.check("WebSocket missing token", self._assert_auth_rejection)
        socket_context = self.http.websocket_connect(f"/api/ws?token={TOKEN}")
        websocket = socket_context.__enter__()
        websocket.send_json({"type": "ping", "req_id": "smoke-ping", "data": {"t": 17.25}})
        pong = self._receive_ws(websocket)
        assert pong["type"] == "pong" and pong["req_id"] == "smoke-ping"
        assert pong["data"]["t"] == 17.25
        websocket.send_json({
            "type": "subscribe",
            "req_id": "smoke-subscribe",
            "data": {
                "channels": ["log"],
                "log_filter": {"sources": ["task", "service", "core"], "min_level": "DEBUG"},
            },
        })
        assert self._receive_ws(websocket)["type"] == "subscribed"
        self.ok("WS 鉴权 / ping / subscribe", "query token 通过；pong 回带 t；三源订阅成功")

        import logging

        logging.getLogger("maa_api.log_ws_smoke").warning("M4 smoke service source marker")
        service_record = self._wait_hub(
            lambda row: row.source == "service" and "M4 smoke service source marker" in row.content
        )
        service_frame = self._receive_ws(websocket)
        assert service_frame["type"] == "log"
        assert service_frame["data"]["id"] == service_record.id
        assert service_frame["data"]["source"] == "service"
        self.ok("Python logging → live WS", service_frame["data"]["content"])

        core_path = self._core_file_write()
        self.step("已向临时 asst.log 追加三行 native 日志")
        core_record = self._wait_hub(
            lambda row: row.source == "core" and "M4 smoke core source marker one" in row.content
        )
        self.step("等待 core 实时 WS 帧")
        core_frame = self._receive_ws(websocket)
        self.step(f"core WS 帧已收到：{core_frame.get('type')}")
        assert core_frame["type"] == "log"
        assert core_frame["data"]["id"] == core_record.id
        assert core_frame["data"]["source"] == "core"
        self.ok("临时 asst.log tail → live WS", f"{Path(core_path).name}; rotation-safe tail active")

        self.step("启动 FakeAsst spawn + CoreClient 任务链")
        callbacks = self._collect_core_task()
        self.step(f"FakeAsst 任务链终态已收到：{callbacks}")
        assert "TaskChainStart" in callbacks
        assert "TaskChainCompleted" in callbacks
        task_frame = self._receive_ws(websocket)
        while task_frame.get("type") == "log" and task_frame.get("data", {}).get("source") != "task":
            task_frame = self._receive_ws(websocket)
        assert task_frame["type"] == "log" and task_frame["data"]["source"] == "task"
        self.ok("FakeAsst spawn → CoreClient → CallbackTranslator → live WS", ", ".join(callbacks))

        socket_context.__exit__(None, None, None)

        screenshot_id, attachment = self._seed_screenshot()
        attachment_record = self._wait_hub(
            lambda row: row.content == "M4 smoke attachment marker"
        )
        assert attachment_record.attachment == attachment
        assert "base64" not in attachment_record.content.lower()
        self.ok("截图附件引用", f"{screenshot_id}; sha256={attachment['sha256'][:12]}…; no base64 in content")

        self._history_and_screenshots(screenshot_id, attachment)
        self._backfill_and_filter()

    def _history_and_screenshots(self, screenshot_id: str, attachment: dict[str, Any]) -> None:
        deadline = time.monotonic() + 8.0
        while time.monotonic() < deadline:
            response = self._api(
                "get", "/api/system/logs",
                params={"source": "core", "level": "ERROR", "q": "M4 smoke core source marker"},
            )
            if response.status_code == 200 and response.json()["items"]:
                break
            time.sleep(0.05)
        else:
            raise SmokeFailure("core source log did not reach historical storage")
        core_rows = response.json()["items"]
        assert all(row["source"] == "core" and row["level"] == "ERROR" for row in core_rows)
        assert all("M4 smoke core source marker" in row["content"] for row in core_rows)

        service = self._api(
            "get", "/api/system/logs",
            params={"source": "service", "level": "WARNING", "q": "service source marker", "order": "asc"},
        )
        assert service.status_code == 200 and service.json()["items"]
        service_rows = service.json()["items"]
        assert all(row["source"] == "service" and row["level"] == "WARNING" for row in service_rows)

        attachment_rows = []
        deadline = time.monotonic() + 8.0
        while time.monotonic() < deadline:
            attachment_response = self._api(
                "get", "/api/system/logs",
                params={"source": "task", "level": "ERROR", "q": "attachment marker"},
            )
            if attachment_response.status_code == 200 and attachment_response.json()["items"]:
                attachment_rows = attachment_response.json()["items"]
                break
            time.sleep(0.05)
        assert attachment_rows, "attachment log did not reach historical storage"
        assert attachment_rows[0]["attachment"]["sha256"] == attachment["sha256"]

        task_page = self._api(
            "get", "/api/system/logs",
            params={"source": "task", "order": "asc", "size": 1},
        )
        assert task_page.status_code == 200, task_page.text
        page = task_page.json()
        assert page["items"] and page["page"]["has_more"] is True
        cursor = page["page"]["next_cursor"]
        next_page = self._api(
            "get", "/api/system/logs",
            params={"source": "task", "after_id": cursor, "order": "asc", "size": 20},
        )
        assert next_page.status_code == 200
        assert all(item["id"] > cursor for item in next_page.json()["items"])
        assert any("M4 smoke attachment marker" in item["content"] for item in next_page.json()["items"])

        txt = self._api("get", "/api/system/logs/export", params={"format": "txt", "q": "service source marker"})
        jsonl = self._api("get", "/api/system/logs/export", params={"format": "jsonl", "source": "task", "q": "attachment marker"})
        assert txt.status_code == jsonl.status_code == 200
        assert "attachment; filename=\"logs.txt\"" in txt.headers.get("content-disposition", "")
        assert "M4 smoke service source marker" in txt.text
        assert "logs.jsonl" in jsonl.headers.get("content-disposition", "")
        exported = [json.loads(line) for line in jsonl.text.splitlines() if line.strip()]
        assert len(exported) == 1 and exported[0]["source"] == "task"
        assert exported[0]["attachment"]["sha256"] == attachment["sha256"]

        archive = self._api("get", "/api/screenshots", params={"trigger": "agent", "size": 20})
        assert archive.status_code == 200 and any(row["id"] == screenshot_id for row in archive.json()["items"])
        detail = self._api("get", f"/api/screenshots/{screenshot_id}?as=base64")
        assert detail.status_code == 200 and set(detail.json()) == {
            "id", "format", "width", "height", "size_bytes", "captured_at", "data"
        }
        assert base64.b64decode(detail.json()["data"])
        for variant in ("thumb", "full"):
            image = self._api("get", f"/api/images/{attachment['sha256']}/{variant}")
            assert image.status_code == 200
            assert image.headers.get("cache-control") == "public, max-age=31536000, immutable"
            assert image.content

        no_delete_condition = self._api("delete", "/api/system/logs")
        assert no_delete_condition.status_code == 400
        assert no_delete_condition.json()["error"]["code"] == "INVALID_PARAMETER"
        deleted = self._api("delete", "/api/system/logs", params={"source": "core"})
        assert deleted.status_code == 204 and not deleted.content
        empty_core = self._api("get", "/api/system/logs", params={"source": "core"})
        assert empty_core.status_code == 200 and empty_core.json()["items"] == []
        self.ok("历史查询 / 过滤 / 游标 / 导出 / 清理", "core/service/task filters; after_id; txt/jsonl; 204 cleanup")
        self.ok("截图归档 / base64 / immutable 图片", "list/detail + thumb/full cache-control validated")

    def _backfill_and_filter(self) -> None:
        from maa_api.services.log_hub import LogRecord

        # A cursor still in the bounded ring replays only newer ids.
        async def emit_ring():
            for idx in range(80):
                self.hub.emit_internal(f"M4 replay padding {idx}")
            await asyncio.sleep(0.02)

        self.client_portal.call(emit_ring)
        latest = self.hub._ring[-1].id
        recent_cursor = latest - 1
        with self.http.websocket_connect(f"/api/ws?token={TOKEN}") as websocket:
            websocket.send_json({
                "type": "subscribe",
                "req_id": "in-buffer",
                "data": {"channels": ["log"], "last_seen_id": recent_cursor},
            })
            batch = self._receive_ws(websocket)
            subscribed = self._receive_ws(websocket)
            assert batch["type"] == "log_batch"
            assert batch["data"]["truncated"] is False
            assert [row["id"] for row in batch["data"]["records"]] == [latest]
            assert subscribed["data"]["backfilled"] == 1

        # An older cursor is explicitly marked; history REST can fill the gap.
        with self.http.websocket_connect(f"/api/ws?token={TOKEN}") as websocket:
            websocket.send_json({
                "type": "subscribe",
                "req_id": "out-of-buffer",
                "data": {"channels": ["log"], "last_seen_id": 0},
            })
            batch = self._receive_ws(websocket)
            subscribed = self._receive_ws(websocket)
            assert batch["type"] == "log_batch"
            assert batch["data"]["truncated"] is True
            assert len(batch["data"]["records"]) == 64
            assert subscribed["data"]["truncated"] is True

        # Service-side filtering must drop core rows for a task-only client.
        with self.http.websocket_connect(f"/api/ws?token={TOKEN}") as websocket:
            websocket.send_json({
                "type": "subscribe",
                "data": {
                    "channels": ["log"],
                    "log_filter": {"sources": ["task"], "min_level": "INFO"},
                },
            })
            assert self._receive_ws(websocket)["type"] == "subscribed"
            self.client_portal.call(
                self.hub.offer,
                LogRecord(ts=time.time(), source="core", level="ERROR", content="filtered core sentinel"),
            )
            self.client_portal.call(
                self.hub.offer,
                LogRecord(ts=time.time(), source="task", level="INFO", content="task filter sentinel"),
            )
            frame = self._receive_ws(websocket)
            assert frame["type"] == "log" and frame["data"]["content"] == "task filter sentinel"
            assert frame["data"]["source"] == "task"
        self.ok("WebSocket 补发与订阅过滤", "in-buffer exact replay; old cursor truncated; task-only excludes core")

    def _default_run(self) -> None:
        from fastapi.testclient import TestClient

        self._prepare()
        with TestClient(self.app, raise_server_exceptions=False) as client:
            self.http = client
            self.client_portal = client.portal
            self.hub = self.app.state.log_hub
            assert self.hub._started
            self._live_pipeline()
            if self.args.real_core:
                callbacks = self._collect_core_task(real=True)
                assert "TaskChainStart" in callbacks
                self.ok("--real-core 设备任务", ", ".join(callbacks))

        self.ok("真实 app lifespan 收尾", "LogHub 已关闭并 flush；测试只写入隔离临时目录")

    def _serve_mode(self) -> None:
        if not 1 <= self.args.port <= 65535:
            raise SmokeFailure(f"非法端口：{self.args.port}")
        assert self.temp_root is not None
        serve_root = self.temp_root / "serve"
        serve_root.mkdir()
        (serve_root / "maa_api").symlink_to(REPO_ROOT / "maa_api", target_is_directory=True)
        (serve_root / "alembic.ini").symlink_to(REPO_ROOT / "alembic.ini")
        (serve_root / "resource").mkdir()
        with socket.socket() as probe:
            probe.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
            try:
                probe.bind(("127.0.0.1", self.args.port))
            except OSError as exc:
                if exc.errno == errno.EADDRINUSE:
                    raise SmokeFailure(f"--serve 端口 {self.args.port} 已占用：{exc}") from exc
                raise SmokeFailure(
                    f"--serve 无法绑定本机 loopback 端口 {self.args.port}：{exc}"
                ) from exc

        env = os.environ.copy()
        env["MAA_APP_ACCESS_TOKEN"] = TOKEN
        env["MAA_APP_MAA_CORE_PATH"] = str(self.temp_root / "serve-missing-core")
        env["PYTHONPATH"] = str(REPO_ROOT)
        process = subprocess.Popen(
            [sys.executable, "-m", "uvicorn", "maa_api.main:app", "--host", "127.0.0.1", "--port", str(self.args.port)],
            cwd=serve_root,
            env=env,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
        )
        started = time.monotonic()
        try:
            while time.monotonic() - started < 12:
                if process.poll() is not None:
                    raise SmokeFailure(f"uvicorn 提前退出：exit={process.returncode}")
                try:
                    with urllib.request.urlopen(
                        f"http://127.0.0.1:{self.args.port}/api/system/health", timeout=0.5
                    ) as response:
                        if response.status == 200:
                            break
                except (urllib.error.URLError, TimeoutError):
                    time.sleep(0.1)
            else:
                raise SmokeFailure("uvicorn 12 秒内未就绪")

            ws = socket.create_connection(("127.0.0.1", self.args.port), timeout=3)
            ws.settimeout(3)
            key = base64.b64encode(os.urandom(16)).decode("ascii")
            request = (
                f"GET /api/ws?token={TOKEN} HTTP/1.1\r\n"
                f"Host: 127.0.0.1:{self.args.port}\r\n"
                "Upgrade: websocket\r\nConnection: Upgrade\r\n"
                f"Sec-WebSocket-Key: {key}\r\nSec-WebSocket-Version: 13\r\n\r\n"
            )
            ws.sendall(request.encode("ascii"))
            response = bytearray()
            while b"\r\n\r\n" not in response:
                chunk = ws.recv(4096)
                if not chunk:
                    raise SmokeFailure("WS 握手期间连接关闭")
                response.extend(chunk)
            headers, remainder = bytes(response).split(b"\r\n\r\n", 1)
            if b" 101 " not in headers.split(b"\r\n", 1)[0]:
                raise SmokeFailure(f"WS 握手不是 101：{headers[:200]!r}")
            payload = json.dumps({"type": "subscribe", "data": {"channels": ["log"]}}, separators=(",", ":")).encode()
            mask = os.urandom(4)
            masked = bytes(byte ^ mask[index % 4] for index, byte in enumerate(payload))
            if len(payload) < 126:
                frame = bytes([0x81, 0x80 | len(payload)]) + mask + masked
            else:
                frame = bytes([0x81, 0x80 | 126]) + struct.pack("!H", len(payload)) + mask + masked
            ws.sendall(frame)
            server_frame = remainder or b""
            while len(server_frame) < 2:
                server_frame += ws.recv(4096)
            length = server_frame[1] & 0x7F
            offset = 2
            if length == 126:
                while len(server_frame) < 4:
                    server_frame += ws.recv(4096)
                length = struct.unpack("!H", server_frame[2:4])[0]
                offset = 4
            elif length == 127:
                raise SmokeFailure("unexpected oversized WS frame")
            while len(server_frame) < offset + length:
                server_frame += ws.recv(4096)
            message = json.loads(server_frame[offset : offset + length])
            assert message["type"] == "subscribed", message
            ws.close()
            self.ok("--serve 真实 uvicorn WebSocket 握手", f"101 + subscribed on 127.0.0.1:{self.args.port}")
        finally:
            if process.poll() is None:
                process.terminate()
            try:
                process.wait(timeout=5)
            except subprocess.TimeoutExpired:
                process.kill()
                process.wait(timeout=3)

    def _cleanup(self) -> None:
        if self.engine is not None:
            with contextlib.suppress(Exception):
                self.engine.sync_engine.dispose()
        if self.settings_module is not None:
            self.settings_module.set_settings(None)
        for key in list(os.environ):
            if key.startswith("MAA_"):
                os.environ.pop(key, None)
        os.environ.update(self.saved_env)
        if self.temp_root is not None:
            if self.args.keep_temp:
                self.step(f"--keep-temp：保留 {self.temp_root}")
            else:
                shutil.rmtree(self.temp_root, ignore_errors=True)

    def run(self) -> int:
        try:
            try:
                self._default_run()
                if self.args.serve:
                    self._serve_mode()
            except Exception as exc:  # noqa: BLE001 - CLI emits a complete checklist
                self.fail("端到端流程", f"{type(exc).__name__}: {exc}")
                traceback.print_exc()
        finally:
            self._cleanup()
        current_snapshot = self._snapshot_repository_artifacts()
        if current_snapshot != self.repository_snapshot:
            changed = sorted(
                key
                for key in self.repository_snapshot.keys() | current_snapshot.keys()
                if self.repository_snapshot.get(key) != current_snapshot.get(key)
            )
            self.fail("仓库零写入", ", ".join(changed))
        else:
            self.ok("仓库零写入", "DB/config/log/screenshot artifacts unchanged")
        total = len(self.checks) + len(self.failures)
        self.step(f"检查计数：{len(self.checks)}/{total} 通过，耗时 {time.monotonic() - self.started:.1f}s")
        if self.failures:
            print(f"[log_ws_smoke] 失败 {len(self.failures)} 项；未打印 SMOKE OK", flush=True)
            return 1
        self.step("全部检查通过")
        print("SMOKE OK", flush=True)
        return 0


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    try:
        return LogWsSmoke(args).run()
    except KeyboardInterrupt:
        print("[log_ws_smoke] 收到中断", file=sys.stderr, flush=True)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
