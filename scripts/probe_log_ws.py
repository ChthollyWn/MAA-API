#!/usr/bin/env python3
"""M4-01 前置实测：WS 路由与关闭码、跨线程入队、IPC 事件载荷、asst.log tail 语义。

本脚本是**丢弃式探针**，不是生产代码。它把 ``docs/06-实时日志与WebSocket``、
``docs/05-API规范与路由清单 §5.4``、``docs/13-决策记录 §5`` 里 M4 依赖的、但只能靠
运行才能确证的前提逐条跑一遍，结论供 M4 后续卡（``services/log_hub.py``、
``api/ws.py``、``services/callback_translator.py``、``util/image.py``）直接引用。

做十一组实验（编号与 ``required_checks`` 的键一一对应）：

1. ``ws_route_hidden_from_openapi_and_route_contexts`` —— WS 路由的可发现性：
   ``app.openapi()["paths"]`` 不含 WS 路由、``fastapi.routing.iter_route_contexts``
   对被 include 的 WS 路由给不出路径，只有模块级 ``router.routes[*].path`` 可靠。
2. ``ws_close_4401_surfaces_as_disconnect_code`` —— ``accept()`` 后 ``close(4401)``
   在 ``TestClient`` 侧是 ``WebSocketDisconnect.code == 4401``。
3. ``missing_ws_path_disconnect_code_1000`` —— 连不存在的 WS 路径**同样**抛
   ``WebSocketDisconnect``，但 code 是 1000：不能用「连不上」断言路由缺失。
4. ``testclient_lifespan_needs_context_manager`` —— 只有 ``with TestClient(...)``
   才跑 lifespan。
5. ``extract_token_ws_query_precedes_cookie`` —— ``deps.extract_token(websocket)``
   能取 query 与 cookie，且 query 优先。
6. ``cross_thread_put_nowait_does_not_wake_loop`` —— 裸线程 ``asyncio.Queue.put_nowait``
   **不能**唤醒阻塞在 ``select`` 的事件循环消费者（记录毫秒级延迟与 call_soon_threadsafe
   对照），决定 ``LogHub.offer`` 是否需要 ``loop.call_soon_threadsafe``。
7. ``deque_maxlen_thread_appends_keep_length`` —— ``deque(maxlen=N)`` 多线程 append
   后长度恒为 N、无异常。
8. ``handler_payload_lacks_envelope_ts`` —— ``CoreClient`` 分派给 LOG / CALLBACK
   handler 的 payload 键集（**不含** envelope ``ts``）；事件用生产代码
   （``worker._QueueLogHandler`` / ``worker._callback_bridge``）构造。
9. ``asst_log_tail_rotation_truncation_half_line_replace`` —— ``asst.log`` tail 的五条
   语义：rename 轮转后旧句柄可读到 EOF、新文件 ``st_ino`` 变化、截断时 ``st_size``
   小于上次 offset、半行不消费、``errors="replace"`` 读非 UTF-8 不抛。
10. ``bulk_insert_backfills_ids_after_commit`` —— ``LogRepository.bulk_insert`` 提交后
    ``LogEntry.id`` 已回填且单调（决定环形缓冲与补发游标的 id 来源）。
11. ``ws_client_tooling_inventory_local`` —— 本机可用的 WS 客户端/服务端手段盘点
   （``websockets`` / ``wsproto`` / ``websocat`` 全缺；uvicorn 无法升级 WS）。

纪律（与卡面硬约束一致）：不联网、不 dlopen MaaCore、不触碰真实
``resource/maa_api.db`` 与 ``config.yaml``、不依赖真机；只 import M1–M3 已交付的模块
（``core.client`` / ``core.worker`` / ``api.deps`` / ``db.repositories.log``），
不 import ``maa_api.main``、不 import 尚不存在的 ``services.log_hub`` / ``api.ws`` 等。

产物（默认写入 ``tests/fixtures/``）：

- ``log_ws_probe_result.json``：机器可读的实测结果（含 11 条 ``required_checks``）；
- ``log_ws_probe_findings.md``：人读结论，同名小节给出「结论 + 原始数据 + 对 M4 实现的影响」。

用法::

    .venv/bin/python scripts/probe_log_ws.py
    .venv/bin/python scripts/probe_log_ws.py --out-json /tmp/r.json --out-md /tmp/r.md
"""

from __future__ import annotations

import argparse
import asyncio
import base64
import ctypes
import importlib
import json
import logging
import os
import queue
import shutil
import socket
import subprocess
import sys
import tempfile
import threading
import time
import traceback
import warnings
from collections import deque
from contextlib import asynccontextmanager
from pathlib import Path
from typing import Any, Callable

# FastAPI 的注解解析走「模块全局命名空间」：本脚本开了
# ``from __future__ import annotations``，若把 ``WebSocket`` 只在函数内 import，
# ``websocket: WebSocket`` 会解析不出类型、被当成必填 query 参数（实测 1008）。
# 所以这些名字必须在模块级可见。
from fastapi import APIRouter, FastAPI, WebSocket
from fastapi.routing import APIWebSocketRoute, iter_route_contexts
from starlette.websockets import WebSocketDisconnect

REPO_ROOT = Path(__file__).resolve().parents[1]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

DEFAULT_OUT_JSON = REPO_ROOT / "tests" / "fixtures" / "log_ws_probe_result.json"
DEFAULT_OUT_MD = REPO_ROOT / "tests" / "fixtures" / "log_ws_probe_findings.md"
ASYNC_SAMPLE_PATH = REPO_ROOT / "tests" / "fixtures" / "async_call_info_sample.json"
REAL_DB_PATH = REPO_ROOT / "resource" / "maa_api.db"
REAL_CONFIG_PATH = REPO_ROOT / "config.yaml"

#: 跨线程唤醒的判定窗口（秒）：裸线程 put 之后等这么久仍未送达即判「未唤醒」。
WAKEUP_WINDOW = 0.5


def _import_testclient():
    """延迟 import ``TestClient`` 并吞掉 starlette/httpx 的弃用警告（M3-01 已记录）。"""
    with warnings.catch_warnings():
        warnings.simplefilter("ignore")
        from fastapi.testclient import TestClient

    return TestClient


def _versions() -> dict[str, str]:
    versions: dict[str, str] = {"python": sys.version.split()[0], "platform": sys.platform}
    for name in ("fastapi", "starlette", "httpx", "pydantic", "sqlmodel", "sqlalchemy", "uvicorn"):
        try:
            module = importlib.import_module(name)
        except Exception as exc:  # noqa: BLE001 - 版本记录失败不影响探针
            versions[name] = f"<missing: {type(exc).__name__}>"
        else:
            versions[name] = str(getattr(module, "__version__", "?"))
    return versions


# ======================================================================================
# ① WS 路由可发现性
# ======================================================================================


def probe_ws_routing() -> dict[str, Any]:
    """WS 路由在 openapi / iter_route_contexts / 模块级 router 三种视角下的可见性。"""
    router = APIRouter()

    @router.websocket("/api/ws")
    async def ws_echo(websocket: WebSocket) -> None:  # pragma: no cover - 探针路由
        await websocket.accept()
        await websocket.close()

    @router.websocket("/api/ws-auth-fail")
    async def ws_auth_fail(websocket: WebSocket) -> None:  # pragma: no cover - 探针路由
        await websocket.accept()
        await websocket.close(code=4401)

    @router.get("/api/probe-http")
    async def probe_http() -> dict[str, bool]:
        return {"ok": True}

    app = FastAPI()
    app.include_router(router)

    # 注意：docs/06 的 asst.log 路由；这里只关心「怎么才能看到 WS 路径」
    openapi_paths = sorted(app.openapi()["paths"])
    context_paths: list[dict[str, Any]] = []
    for context in iter_route_contexts(app.routes):
        context_paths.append(
            {
                "type": type(context).__name__,
                "path": getattr(context, "path", None),
                "name": getattr(context, "name", None),
                "has_tags": hasattr(context, "tags"),
            }
        )
    module_router_paths = [route.path for route in router.routes]
    ws_route_types = sorted({type(route).__name__ for route in router.routes})

    # app.routes 直查（M3 已实测会抛 AttributeError）——这里复现一条，供 WS 卡 verify 避坑。
    app_route_path_errors: list[str] = []
    for route in app.routes:
        try:
            route.path  # noqa: B018 - 故意取属性，验证是否会抛
        except AttributeError as exc:
            app_route_path_errors.append(f"{type(route).__name__}: {exc}")

    included_router_private_paths: list[str] | None = None
    for route in app.routes:
        original = getattr(route, "original_router", None)
        if original is not None:
            included_router_private_paths = [r.path for r in original.routes]
            break

    ws_paths = {"/api/ws", "/api/ws-auth-fail"}
    # 推荐给 WS 卡 verify 的表达式，这里真跑一遍：
    recommended_paths = sorted(
        route.path for route in router.routes if isinstance(route, APIWebSocketRoute)
    )
    hidden_from_openapi = not (ws_paths & set(openapi_paths))
    hidden_from_contexts = not (ws_paths & {str(item["path"]) for item in context_paths})
    module_router_ok = ws_paths <= set(recommended_paths)
    placeholders = [item for item in context_paths if not item["path"]]
    check_ok = bool(hidden_from_openapi and hidden_from_contexts and module_router_ok)

    return {
        "check_name": "ws_route_hidden_from_openapi_and_route_contexts",
        "check_ok": check_ok,
        "openapi_paths": openapi_paths,
        "ws_paths_hidden_from_openapi": hidden_from_openapi,
        "iter_route_contexts": context_paths,
        "ws_paths_hidden_from_iter_route_contexts": hidden_from_contexts,
        "ws_context_placeholder_count": len(placeholders),
        "ws_context_placeholder_type": placeholders[0]["type"] if placeholders else None,
        "module_router_paths": module_router_paths,
        "recommended_verify_result": recommended_paths,
        "ws_paths_visible_on_module_router": module_router_ok,
        "module_router_route_types": ws_route_types,
        "recommended_verify_expression": (
            "sorted(r.path for r in maa_api.api.ws.router.routes "
            "if isinstance(r, fastapi.routing.APIWebSocketRoute))"
        ),
        "app_routes_path_attribute_errors": app_route_path_errors,
        "included_router_private_original_router_paths": included_router_private_paths,
        "app_route_types": [type(r).__name__ for r in app.routes],
    }


# ======================================================================================
# ②③ WS 关闭码
# ======================================================================================


def _ws_connect_outcome(client: Any, path: str) -> dict[str, Any]:
    """连一次 WS 并把「抛了什么/close code 是多少」记成可判定的字典。"""
    record: dict[str, Any] = {"path": path}
    try:
        with client.websocket_connect(path) as ws:
            record["received"] = ws.receive_text()
        record["raised"] = None
    except WebSocketDisconnect as exc:
        record.update(raised="WebSocketDisconnect", code=exc.code, reason=exc.reason)
    except Exception as exc:  # noqa: BLE001 - 记录真实类型
        record.update(raised=type(exc).__name__, error=str(exc))
    return record


def probe_ws_close_code_4401() -> dict[str, Any]:
    """``accept()`` 后 ``close(4401)`` 在 TestClient 侧是什么异常/什么 code。"""
    TestClient = _import_testclient()
    router = APIRouter()

    @router.websocket("/api/ws-auth-fail")
    async def ws_auth_fail(websocket: WebSocket) -> None:  # pragma: no cover - 探针路由
        await websocket.accept()
        await websocket.close(code=4401)

    app = FastAPI()
    app.include_router(router)
    accepted_close = _ws_connect_outcome(TestClient(app), "/api/ws-auth-fail")

    check_ok = accepted_close.get("raised") == "WebSocketDisconnect" and accepted_close.get(
        "code"
    ) == 4401
    return {
        "check_name": "ws_close_4401_surfaces_as_disconnect_code",
        "check_ok": check_ok,
        "accept_then_close_4401": accepted_close,
    }


def probe_ws_missing_route() -> dict[str, Any]:
    """连不存在的 WS 路径时的异常与 close code（对照「不 accept 就 close」）。"""
    TestClient = _import_testclient()
    router = APIRouter()

    @router.websocket("/api/ws-reject-before-accept")
    async def ws_reject(websocket: WebSocket) -> None:  # pragma: no cover - 探针路由
        await websocket.close(code=1008)

    app = FastAPI()
    app.include_router(router)
    client = TestClient(app)
    missing_path = _ws_connect_outcome(client, "/api/ws-does-not-exist")
    reject_before_accept = _ws_connect_outcome(client, "/api/ws-reject-before-accept")
    # 对照组：已有的 HTTP 路径被当 WS 连（不是缺失路由，看看会不会也伪装成 1000）
    @app.get("/api/http-only")
    async def http_only() -> dict[str, bool]:
        return {"ok": True}

    http_only_as_ws = _ws_connect_outcome(client, "/api/http-only")

    check_ok = (
        missing_path.get("raised") == "WebSocketDisconnect" and missing_path.get("code") == 1000
    )
    return {
        "check_name": "missing_ws_path_disconnect_code_1000",
        "check_ok": check_ok,
        "missing_route": missing_path,
        "reject_before_accept_control": reject_before_accept,
        "http_route_used_as_ws_control": http_only_as_ws,
        "missing_code_equals_http_route_control": missing_path.get("code")
        == http_only_as_ws.get("code"),
        "reject_before_accept_code": reject_before_accept.get("code"),
    }


# ======================================================================================
# ④ lifespan 与 TestClient 上下文
# ======================================================================================


def probe_lifespan_context() -> dict[str, Any]:
    """``TestClient`` 是否必须用 ``with`` 才跑 lifespan。"""
    TestClient = _import_testclient()
    events: list[str] = []

    @asynccontextmanager
    async def lifespan(_app: FastAPI):
        events.append("enter")
        yield
        events.append("exit")

    app = FastAPI(lifespan=lifespan)

    @app.get("/probe")
    async def probe() -> dict[str, bool]:
        return {"ok": True}

    client = TestClient(app)
    status_no_context = client.get("/probe").status_code
    events_no_context = list(events)

    with TestClient(app) as managed:
        status_with_context = managed.get("/probe").status_code
        events_inside_context = list(events)
    events_after_context = list(events)

    check_ok = (
        status_no_context == 200
        and events_no_context == []
        and status_with_context == 200
        and events_inside_context == ["enter"]
        and events_after_context == ["enter", "exit"]
    )
    return {
        "check_name": "testclient_lifespan_needs_context_manager",
        "check_ok": check_ok,
        "without_context": {"status": status_no_context, "lifespan_events": events_no_context},
        "inside_context": {"status": status_with_context, "lifespan_events": events_inside_context},
        "after_context": {"lifespan_events": events_after_context},
    }


# ======================================================================================
# ⑤ extract_token(websocket)：query 与 cookie 的优先级
# ======================================================================================


def probe_extract_token() -> dict[str, Any]:
    """在真实 WS 握手上跑 ``deps.extract_token``，记录渠道与优先级。"""
    from maa_api.api.deps import COOKIE_NAME, extract_token

    TestClient = _import_testclient()
    router = APIRouter()

    @router.websocket("/api/ws-token")
    async def ws_token(websocket: WebSocket) -> None:  # pragma: no cover - 探针路由
        await websocket.accept()
        hit = extract_token(websocket)
        await websocket.send_text(
            json.dumps(
                {
                    "channel": None if hit is None else str(hit.channel),
                    "value": None if hit is None else hit.value,
                }
            )
        )
        await websocket.close()

    app = FastAPI()
    app.include_router(router)
    client = TestClient(app)
    cookie = "cookie-token-value"
    query = "query-token-value"

    def probe(
        url: str,
        headers: dict[str, str] | None = None,
        client_cookie: str | None = None,
        cookies_kwarg: dict[str, str] | None = None,
    ) -> dict[str, Any]:
        record: dict[str, Any] = {
            "url": url,
            "headers": headers,
            "client_cookie": client_cookie,
            "cookies_kwarg": cookies_kwarg,
        }
        try:
            if client_cookie is not None:
                client.cookies.set(COOKIE_NAME, client_cookie, domain="testserver")
            kwargs: dict[str, Any] = {"headers": headers or {}}
            if cookies_kwarg is not None:
                kwargs["cookies"] = cookies_kwarg
            with client.websocket_connect(url, **kwargs) as ws:
                record.update(json.loads(ws.receive_text()))
        except Exception as exc:  # noqa: BLE001
            record.update(error=f"{type(exc).__name__}: {exc}")
        finally:
            client.cookies.clear()
        return record

    cases = {
        "no_credential": probe("/api/ws-token"),
        "query_only": probe(f"/api/ws-token?token={query}"),
        "cookie_via_header": probe("/api/ws-token", headers={"cookie": f"{COOKIE_NAME}={cookie}"}),
        "cookie_via_cookies_kwarg": probe(
            "/api/ws-token", cookies_kwarg={COOKIE_NAME: cookie}
        ),
        "query_beats_cookie": probe(
            f"/api/ws-token?token={query}", headers={"cookie": f"{COOKIE_NAME}={cookie}"}
        ),
        "empty_query_falls_through_to_cookie": probe(
            "/api/ws-token?token=", headers={"cookie": f"{COOKIE_NAME}={cookie}"}
        ),
        "client_cookie_jar_only": probe("/api/ws-token", client_cookie=cookie),
    }

    check_ok = (
        cases["no_credential"].get("channel") is None
        and cases["query_only"].get("channel") == "query"
        and cases["query_only"].get("value") == query
        and cases["cookie_via_header"].get("channel") == "cookie"
        and cases["cookie_via_header"].get("value") == cookie
        and cases["query_beats_cookie"].get("channel") == "query"
        and cases["query_beats_cookie"].get("value") == query
        and cases["empty_query_falls_through_to_cookie"].get("channel") == "cookie"
    )
    return {
        "check_name": "extract_token_ws_query_precedes_cookie",
        "check_ok": check_ok,
        "cookie_name": COOKIE_NAME,
        "cases": cases,
        "client_cookie_jar_reaches_ws_handshake": cases["client_cookie_jar_only"].get("channel")
        == "cookie",
        "cookies_kwarg_reaches_ws_handshake": cases["cookie_via_cookies_kwarg"].get("channel")
        == "cookie",
    }


# ======================================================================================
# ⑥ 跨线程入队：put_nowait 能否唤醒事件循环消费者
# ======================================================================================


def _queue_wakeup_scenario(mode: str, debug: bool = False, window: float = WAKEUP_WINDOW) -> dict[str, Any]:
    """让事件循环在另一条线程里阻塞等队列，主线程投递并测「是否被唤醒」。

    ``mode="raw"``：直接 ``asyncio.Queue.put_nowait``（生产代码里最容易写出的形态）。
    ``mode="threadsafe"``：``loop.call_soon_threadsafe(queue.put_nowait, item)``。
    """
    loop = asyncio.new_event_loop()
    loop.set_debug(debug)
    loop_errors: list[str] = []
    # debug 模式下 getter future 的 callback 没被调度，任务会以 "Task was destroyed but
    # it is pending!" 的形式在 loop 关闭时上报 —— 这本身就是被测现象，收进 loop_errors
    # 而不是让它打到 stderr。
    loop.set_exception_handler(
        lambda _loop, context: loop_errors.append(str(context.get("message")))
    )
    q: asyncio.Queue = asyncio.Queue(maxsize=100)
    got = threading.Event()
    stamp: dict[str, Any] = {}
    put_error: dict[str, str] = {}
    consumer_task: list[asyncio.Task] = []

    async def consumer() -> None:
        while True:
            item = await q.get()
            stamp["t"] = time.perf_counter()
            stamp["item"] = item
            got.set()

    def run() -> None:
        asyncio.set_event_loop(loop)
        consumer_task.append(loop.create_task(consumer()))
        loop.run_forever()

    thread = threading.Thread(target=run, name="probe-wakeup-loop", daemon=True)
    thread.start()
    time.sleep(0.15)  # 让循环进入 select（此时没有任何定时器/IO）

    started = time.perf_counter()
    try:
        if mode == "raw":
            q.put_nowait("item")
        else:
            loop.call_soon_threadsafe(q.put_nowait, "item")
    except Exception as exc:  # noqa: BLE001 - debug 模式下会抛 RuntimeError
        put_error["put"] = f"{type(exc).__name__}: {exc}"

    delivered = got.wait(window)
    latency_ms = round((stamp["t"] - started) * 1000, 3) if delivered else None

    # 无条件再唤醒一次：验证条目本身没丢，只是没唤醒消费者。
    loop.call_soon_threadsafe(lambda: None)
    delivered_after_wakeup = got.wait(1.0)

    def _stop() -> None:
        for task in consumer_task:
            task.cancel()
        # 给被取消的任务一次跑完的机会，避免 loop.close() 打 "Task was destroyed" 噪声
        loop.call_later(0.05, loop.stop)

    loop.call_soon_threadsafe(_stop)
    thread.join(timeout=2.0)
    with warnings.catch_warnings():
        warnings.simplefilter("ignore")
        loop.close()

    return {
        "mode": mode,
        "loop_debug": debug,
        "window_s": window,
        "put_error": put_error.get("put"),
        "delivered_within_window": delivered,
        "latency_ms": latency_ms,
        "delivered_after_explicit_wakeup": delivered_after_wakeup,
        "item_lost": not delivered_after_wakeup,
        "loop_exception_contexts": loop_errors,
    }


def _queue_wait_for_scenario(mode: str, timeout: float = 1.0) -> dict[str, Any]:
    """对照测量：用 ``asyncio.wait_for`` 的定时器唤醒循环时，消息要等多久才被消费。"""
    result: dict[str, Any] = {"mode": mode, "timeout_s": timeout}
    q: asyncio.Queue = asyncio.Queue(maxsize=100)

    def producer(loop: asyncio.AbstractEventLoop) -> None:
        time.sleep(0.15)
        if mode == "raw":
            q.put_nowait("item")
        else:
            loop.call_soon_threadsafe(q.put_nowait, "item")

    async def main() -> None:
        loop = asyncio.get_running_loop()
        thread = threading.Thread(target=producer, args=(loop,), daemon=True)
        started = time.perf_counter()
        thread.start()
        try:
            result["item"] = await asyncio.wait_for(q.get(), timeout=timeout)
        except asyncio.TimeoutError:
            result["item"] = None
            result["timed_out"] = True
        result["wait_elapsed_ms"] = round((time.perf_counter() - started) * 1000, 1)
        result["producer_put_at_ms"] = 150.0
        result["woken_by_put"] = result["wait_elapsed_ms"] < timeout * 1000 * 0.5
        thread.join(timeout=1.0)

    asyncio.run(main())
    return result


def probe_cross_thread_queue() -> dict[str, Any]:
    """裸线程 ``put_nowait`` 与 ``call_soon_threadsafe`` 的唤醒行为对照。"""
    raw = _queue_wakeup_scenario("raw")
    threadsafe = _queue_wakeup_scenario("threadsafe")
    raw_debug = _queue_wakeup_scenario("raw", debug=True)
    raw_wait_for = _queue_wait_for_scenario("raw")
    threadsafe_wait_for = _queue_wait_for_scenario("threadsafe")

    check_ok = (
        raw["delivered_within_window"] is False
        and raw["delivered_after_explicit_wakeup"] is True
        and raw["put_error"] is None
        and threadsafe["delivered_within_window"] is True
        and threadsafe["latency_ms"] is not None
        and threadsafe["latency_ms"] < 100
        and raw_debug["put_error"] is not None
    )
    return {
        "check_name": "cross_thread_put_nowait_does_not_wake_loop",
        "check_ok": check_ok,
        "raw_put_nowait": raw,
        "call_soon_threadsafe": threadsafe,
        "raw_put_nowait_loop_debug": raw_debug,
        "wait_for_control": {"raw": raw_wait_for, "threadsafe": threadsafe_wait_for},
    }


# ======================================================================================
# ⑦ deque(maxlen=N) 多线程 append
# ======================================================================================


def probe_deque_maxlen() -> dict[str, Any]:
    """``deque(maxlen=N)`` 在 8 线程 × 5000 次 append 下的长度与异常。"""
    maxlen = 500
    threads = 8
    per_thread = 5000
    ring: deque[tuple[int, int]] = deque(maxlen=maxlen)
    errors: list[str] = []
    over_length: list[int] = []

    def worker(tid: int) -> None:
        try:
            for index in range(per_thread):
                ring.append((tid, index))
                if len(ring) > maxlen:
                    over_length.append(len(ring))
        except Exception as exc:  # noqa: BLE001
            errors.append(f"{type(exc).__name__}: {exc}")

    started = time.perf_counter()
    pool = [threading.Thread(target=worker, args=(tid,)) for tid in range(threads)]
    for thread in pool:
        thread.start()
    for thread in pool:
        thread.join()
    elapsed_ms = round((time.perf_counter() - started) * 1000, 1)

    # 对照（额外发现，不进 required_checks）：无锁 id 自增在本机是否真的会重复
    unsynchronized = _unsynchronized_counter_probe()

    check_ok = len(ring) == maxlen and not errors and not over_length
    return {
        "check_name": "deque_maxlen_thread_appends_keep_length",
        "check_ok": check_ok,
        "maxlen": maxlen,
        "threads": threads,
        "appends_per_thread": per_thread,
        "total_appends": threads * per_thread,
        "final_length": len(ring),
        "observed_over_length": over_length[:5],
        "errors": errors,
        "elapsed_ms": elapsed_ms,
        "unsynchronized_id_counter_control": unsynchronized,
    }


def _unsynchronized_counter_probe() -> dict[str, Any]:
    """无锁 ``n += 1`` 的对照：本机 CPython 3.13.3 上是否复现重复 id。"""
    total = 4
    per_thread = 200_000
    state = [0]
    ids: list[int] = []

    def worker() -> None:
        for _ in range(per_thread):
            value = state[0]
            value += 1
            state[0] = value
            ids.append(value)

    pool = [threading.Thread(target=worker) for _ in range(total)]
    for thread in pool:
        thread.start()
    for thread in pool:
        thread.join()
    return {
        "threads": total,
        "per_thread": per_thread,
        "total": len(ids),
        "unique": len(set(ids)),
        "duplicates": len(ids) - len(set(ids)),
        "final_value": state[0],
    }


# ======================================================================================
# ⑧ CoreClient 分派给 LOG / CALLBACK handler 的 payload 键集
# ======================================================================================


class _ProbeSupervisor:
    """只含 ``CoreClient`` 依赖成员的替身 supervisor（形态照 tests/core/test_client.py）。"""

    def __init__(self) -> None:
        self.cmd_queue: queue.Queue = queue.Queue()
        self.event_queue: queue.Queue = queue.Queue()
        from maa_api.core.supervisor import CoreState

        self.state = CoreState.READY
        self.noted_events: list[dict] = []
        self.tracked: list[dict] = []
        self.released: list[str] = []
        self.dispatchers: list[Callable[..., Any]] = []

    def set_dispatcher(self, fn: Callable[..., Any]) -> None:
        self.dispatchers.append(fn)

    def track_command(self, command: dict) -> None:
        self.tracked.append(command)

    def release_command(self, cmd_id: str) -> None:
        self.released.append(cmd_id)

    def note_event(self, event: dict) -> None:
        self.noted_events.append(event)


def _build_log_event() -> dict[str, Any]:
    """用生产代码 ``worker._QueueLogHandler`` 造一条真实形态的 LOG 事件。"""
    from maa_api.core.worker import _QueueLogHandler

    raw_queue: queue.Queue = queue.Queue()
    handler = _QueueLogHandler(raw_queue)
    logger = logging.getLogger("probe.log_ws.subprocess")
    logger.handlers = [handler]
    logger.setLevel(logging.INFO)
    logger.propagate = False
    logger.info("子进程日志桥接样本")
    return raw_queue.get_nowait()


def _build_callback_event() -> dict[str, Any]:
    """用生产代码 ``worker._callback_bridge`` 造一条真实形态的 CALLBACK 事件。"""
    from maa_api.core.worker import _callback_bridge

    sample = json.loads(ASYNC_SAMPLE_PATH.read_text(encoding="utf8"))
    details = sample["async_call_info"]["details"]
    raw_queue: queue.Queue = queue.Queue()
    _callback_bridge(4, json.dumps(details).encode("utf-8"), ctypes.c_void_p(id(raw_queue)))
    return raw_queue.get_nowait()


def probe_core_client_dispatch() -> dict[str, Any]:
    """LOG / CALLBACK 事件经真实分派后，handler 收到的 payload 键集。"""
    from maa_api.core.client import CoreClient

    log_event = _build_log_event()
    callback_event = _build_callback_event()

    supervisor = _ProbeSupervisor()
    client = CoreClient(supervisor)
    seen: dict[str, dict[str, Any]] = {}

    def make_handler(kind: str) -> Callable[[dict], None]:
        def handler(payload: dict) -> None:
            seen[kind] = {
                "keys": sorted(payload.keys()),
                "payload": payload,
                "has_ts_key": "ts" in payload,
                "envelope_ts": None,
            }

        return handler

    client.on("LOG", make_handler("LOG"))
    client.on("CALLBACK", make_handler("CALLBACK"))

    for event in (log_event, callback_event):
        client._handle_event(event)  # noqa: SLF001 - 卡面允许直接驱动私有入口
        kind = event["type"]
        seen[kind]["envelope_ts"] = event.get("ts")
        seen[kind]["envelope_keys"] = sorted(event.keys())
        seen[kind]["payload_is_envelope_payload"] = event.get("payload") is seen[kind]["payload"]
        if seen[kind]["payload_is_envelope_payload"]:
            # 同一个对象：payload 里没有 ts 就是没有
            seen[kind]["ts_in_envelope_payload"] = "ts" in event["payload"]

    handler_payload_has_no_ts = all(not item["has_ts_key"] for item in seen.values())
    log_keys = seen["LOG"]["keys"]
    callback_keys = seen["CALLBACK"]["keys"]
    noted_keys = sorted(supervisor.noted_events[0].keys()) if supervisor.noted_events else []

    check_ok = (
        handler_payload_has_no_ts
        and log_keys == ["content", "level"]
        and callback_keys == ["details", "msg"]
        and noted_keys == ["payload", "ts", "type"]
        and seen["LOG"]["envelope_keys"] == ["payload", "ts", "type"]
        and seen["CALLBACK"]["envelope_keys"] == ["payload", "ts", "type"]
    )
    return {
        "check_name": "handler_payload_lacks_envelope_ts",
        "check_ok": check_ok,
        "log_event_envelope": {
            "keys": seen["LOG"]["envelope_keys"],
            "ts": seen["LOG"]["envelope_ts"],
            "payload": log_event["payload"],
        },
        "callback_event_envelope": {
            "keys": seen["CALLBACK"]["envelope_keys"],
            "ts": seen["CALLBACK"]["envelope_ts"],
            "payload_keys": sorted(callback_event["payload"].keys()),
            "details_keys": sorted(callback_event["payload"]["details"].keys()),
        },
        "handler_received": {
            "LOG": {"keys": log_keys, "has_ts_key": seen["LOG"]["has_ts_key"]},
            "CALLBACK": {"keys": callback_keys, "has_ts_key": seen["CALLBACK"]["has_ts_key"]},
        },
        "handler_payload_is_envelope_payload": {
            kind: item["payload_is_envelope_payload"] for kind, item in seen.items()
        },
        "supervisor_note_event_keys": noted_keys,
        "supervisor_note_event_has_ts": bool(noted_keys) and "ts" in supervisor.noted_events[0],
        "implication": (
            "handler 只收 payload，envelope ts 到不了 LogHub；LogRecord.ts 只能用 handler 内的"
            "到达时刻，除非 M4 改 CoreClient 的 on()/分派把 envelope 一并交给 handler"
        ),
    }


# ======================================================================================
# ⑨ asst.log tail 语义
# ======================================================================================


class MiniTailer:
    """docs/06 §5.4 ``AsstLogTailer`` 的最小复刻（只保留轮询/轮转/截断/半行/续行语义）。

    ``seekback`` 两种取值用于对照：

    - ``"bytes_before_read"``：读之前先记 ``tell()``，半行时 seek 回该位置（探针实测的正确做法）；
    - ``"encode_len"``：docs/06 §5.4 原文的 ``seek(tell() - len(line.encode("utf-8")))``
      （``errors="replace"`` 下含非法字节的半行会算错字节数）。
    """

    def __init__(self, path: Path, *, seekback: str = "bytes_before_read") -> None:
        self.path = path
        self.seekback = seekback
        self._fp = None
        self._inode: int | None = None
        self.consumed: list[str] = []
        self.events: list[dict[str, Any]] = []

    def close(self) -> None:
        if self._fp is not None:
            self._fp.close()
            self._fp = None

    def _open(self, *, seek_to_end: bool) -> None:
        self.close()
        self._fp = open(self.path, "r", encoding="utf-8", errors="replace")
        self._inode = self.path.stat().st_ino
        if seek_to_end:
            self._fp.seek(0, os.SEEK_END)
        self.events.append(
            {"event": "open", "seek_to_end": seek_to_end, "inode": self._inode, "offset": self._fp.tell()}
        )

    def poll_once(self) -> list[str]:
        """一次轮询；返回本次新消费的完整行（含轮转时从旧句柄读出的尾巴）。"""
        started = len(self.consumed)
        if not self.path.exists():
            self.close()
            return []
        stat = self.path.stat()
        if self._fp is None:
            self._open(seek_to_end=True)  # 首次打开定位到末尾，不回放历史
        elif stat.st_ino != self._inode:
            self._drain()  # 轮转：先把旧句柄读干净
            self._open(seek_to_end=False)
        elif stat.st_size < self._fp.tell():
            self._open(seek_to_end=False)  # 截断
        self._drain()
        return self.consumed[started:]

    def _drain(self) -> None:
        if self._fp is None:
            return
        while True:
            position_before = self._fp.tell()
            line = self._fp.readline()
            if line == "":
                break
            if not line.endswith("\n"):
                encoded_length = len(line.encode("utf-8"))
                if self.seekback == "bytes_before_read":
                    target = position_before
                else:
                    # docs/06 §5.4 原文：seek(tell() - len(line.encode("utf-8")))
                    target = self._fp.tell() - encoded_length
                self.events.append(
                    {
                        "event": "half_line",
                        "tell_after_read": self._fp.tell(),
                        "position_before_read": position_before,
                        "encoded_length": encoded_length,
                        "seek_target": target,
                    }
                )
                try:
                    self._fp.seek(target)
                except ValueError as exc:
                    self.events[-1]["seek_error"] = f"{type(exc).__name__}: {exc}"
                    raise
                break
            self.consumed.append(line.rstrip("\n"))


def probe_asst_log_tail() -> dict[str, Any]:
    """rename 轮转、截断、半行、非 UTF-8 四种情形下的 tail 语义。"""
    result: dict[str, Any] = {}
    with tempfile.TemporaryDirectory(prefix="maa_log_ws_probe_") as tmp:
        base = Path(tmp)
        log_path = base / "asst.log"
        bak_path = base / "asst.bak.log"
        log_path.write_text("L1\nL2\n", encoding="utf-8")

        # --- 首次打开 seek 到末尾：不回放历史 -------------------------------------
        tailer = MiniTailer(log_path)
        first_poll = tailer.poll_once()
        first_offset = tailer._fp.tell() if tailer._fp else None  # noqa: SLF001
        result["first_open_skips_existing"] = {
            "existing_lines": ["L1", "L2"],
            "consumed": first_poll,
            "offset": first_offset,
        }

        # --- 追加可见 -------------------------------------------------------------
        with open(log_path, "a", encoding="utf-8") as fp:
            fp.write("L3\n")
        result["append_visible"] = {"consumed": tailer.poll_once(), "offset": tailer._fp.tell()}  # noqa: SLF001

        # --- rename 轮转：旧句柄仍能读到轮转前追加的尾部 ---------------------------
        old_inode = log_path.stat().st_ino
        with open(log_path, "a", encoding="utf-8") as fp:
            fp.write("L4\n")  # MaaCore 在 rename 之前写入的最后一行
        os.rename(log_path, bak_path)
        log_path.write_text("N1\nN2\n", encoding="utf-8")
        new_inode = log_path.stat().st_ino
        rotated = tailer.poll_once()
        result["rotation"] = {
            "old_inode": old_inode,
            "new_inode": new_inode,
            "inode_changed": old_inode != new_inode,
            "consumed": rotated,
            "tail_before_rotate_consumed": "L4" in rotated,
            "new_file_consumed": [line for line in rotated if line.startswith("N")],
        }
        tailer.close()

        # --- 旧句柄的裸语义：rename 之后仍可读到追加内容，然后到 EOF ---------------
        raw_path = base / "raw.log"
        raw_path.write_text("A\n", encoding="utf-8")
        raw_fp = open(raw_path, "r", encoding="utf-8", errors="replace")
        raw_fp.read()
        with open(raw_path, "a", encoding="utf-8") as fp:
            fp.write("B\n")
        os.rename(raw_path, base / "raw.bak.log")
        raw_path.write_text("C\n", encoding="utf-8")
        result["old_handle_after_rename"] = {
            "read_after_rename": raw_fp.read(),
            "second_read": raw_fp.read(),
        }
        raw_fp.close()

        # --- 截断：inode 不变，st_size < 上次 offset ------------------------------
        trunc_path = base / "trunc.log"
        trunc_path.write_text("".join(f"T{index}\n" for index in range(10)), encoding="utf-8")
        trunc_tailer = MiniTailer(trunc_path)
        trunc_tailer.poll_once()
        offset_before = trunc_tailer._fp.tell()  # noqa: SLF001
        inode_before = trunc_path.stat().st_ino
        with open(trunc_path, "w", encoding="utf-8") as fp:  # 外部工具清空日志
            fp.write("T-after\n")
        stat_after = trunc_path.stat()
        consumed_after_truncate = trunc_tailer.poll_once()
        result["truncation"] = {
            "offset_before": offset_before,
            "size_after": stat_after.st_size,
            "size_lt_offset": stat_after.st_size < offset_before,
            "inode_unchanged": stat_after.st_ino == inode_before,
            "consumed_after_truncate": consumed_after_truncate,
        }
        trunc_tailer.close()

        # --- 半行：没有换行符不消费，补上换行后才消费 ------------------------------
        half_path = base / "half.log"
        half_path.write_text("", encoding="utf-8")
        half_tailer = MiniTailer(half_path)
        half_tailer.poll_once()
        with open(half_path, "a", encoding="utf-8") as fp:
            fp.write("partial")
        result["half_line"] = {
            "consumed_without_newline": half_tailer.poll_once(),
            "offset_after_poll": half_tailer._fp.tell(),  # noqa: SLF001
            "file_size": half_path.stat().st_size,
        }
        with open(half_path, "a", encoding="utf-8") as fp:
            fp.write("\n")
        result["half_line"]["consumed_after_newline"] = half_tailer.poll_once()
        result["half_line"]["half_line_events"] = half_tailer.events
        half_tailer.close()

        # --- 非 UTF-8：errors="replace" 不抛；strict 对照抛 -------------------------
        bad_path = base / "bad.log"
        bad_path.write_text("", encoding="utf-8")
        bad_tailer = MiniTailer(bad_path)
        bad_tailer.poll_once()  # 先建立句柄（首次打开 seek 到末尾）
        with open(bad_path, "ab") as fp:
            fp.write(b"good\n\xff\xfe bad\n")
        bad_tailer.poll_once()
        strict_error = None
        try:
            with open(bad_path, "r", encoding="utf-8", errors="strict") as fp:
                fp.read()
        except UnicodeDecodeError as exc:
            strict_error = f"{type(exc).__name__}: {exc.reason} at byte {exc.start}"
        result["non_utf8"] = {
            "consumed_with_replace": bad_tailer.consumed,
            "contains_replacement_char": any("\ufffd" in line for line in bad_tailer.consumed),
            "strict_control_error": strict_error,
        }
        bad_tailer.close()

        # --- 额外发现：docs §5.4 的 seek-back 写法在非法字节上半行会算错 -------------
        result["doc_seekback_trap"] = _probe_doc_seekback(base)

        # 五条语义判定
        rotation = result["rotation"]
        truncation = result["truncation"]
        half = result["half_line"]
        non_utf8 = result["non_utf8"]
        old_handle = result["old_handle_after_rename"]
        check_ok = (
            rotation["inode_changed"]
            and rotation["tail_before_rotate_consumed"]
            and rotation["new_file_consumed"] == ["N1", "N2"]
            and old_handle["read_after_rename"] == "B\n"
            and old_handle["second_read"] == ""
            and truncation["size_lt_offset"]
            and truncation["inode_unchanged"]
            and truncation["consumed_after_truncate"] == ["T-after"]
            and half["consumed_without_newline"] == []
            and half["offset_after_poll"] == 0
            and half["consumed_after_newline"] == ["partial"]
            and len(non_utf8["consumed_with_replace"]) == 2
            and non_utf8["contains_replacement_char"]
            and non_utf8["strict_control_error"] is not None
            and result["first_open_skips_existing"]["consumed"] == []
        )

    return {
        "check_name": "asst_log_tail_rotation_truncation_half_line_replace",
        "check_ok": check_ok,
        **result,
    }


def _probe_doc_seekback(base: Path) -> dict[str, Any]:
    """对照 docs/06 §5.4 原文的 ``seek(tell() - len(line.encode()))`` 写法。"""
    findings: dict[str, Any] = {}

    # 情形 A：非法字节出现在半行里、半行前已有完整行 → 偏移多退，读到重复内容
    drift_path = base / "doc_drift.log"
    drift_path.write_text("L1\n", encoding="utf-8")
    drift_tailer = MiniTailer(drift_path, seekback="encode_len")
    drift_tailer.poll_once()
    with open(drift_path, "ab") as fp:
        fp.write(b"x" * 10 + b"\xff")  # 无换行的半行，含 1 个非法字节
    try:
        drift_tailer.poll_once()
        drift_findings = {"raised": None}
    except ValueError as exc:
        drift_findings = {"raised": f"{type(exc).__name__}: {exc}"}
    drift_findings["events"] = drift_tailer.events[-2:]
    if drift_tailer._fp is not None:  # noqa: SLF001
        drift_findings["position_after_poll"] = drift_tailer._fp.tell()  # noqa: SLF001
        drift_findings["reread_next"] = drift_tailer._fp.readline()  # noqa: SLF001
    drift_tailer.close()
    findings["drift_case"] = drift_findings

    # 情形 B：非法字节半行就在文件开头 → seek 目标为负，直接 ValueError
    negative_path = base / "doc_negative.log"
    negative_path.write_text("", encoding="utf-8")
    negative_tailer = MiniTailer(negative_path, seekback="encode_len")
    negative_tailer.poll_once()
    with open(negative_path, "ab") as fp:
        fp.write(b"abc\xff")  # 4 字节 → replace 后 6 字节
    try:
        negative_tailer.poll_once()
        negative_findings = {"raised": None, "events": negative_tailer.events[-1:]}
    except ValueError as exc:
        negative_findings = {
            "raised": f"{type(exc).__name__}: {exc}",
            "events": negative_tailer.events[-1:],
        }
    negative_tailer.close()
    findings["negative_seek_case"] = negative_findings

    # 对照：正确写法（读前记位置）在同一文件上不抛且不重复
    control_path = base / "doc_control.log"
    control_path.write_text("L1\n", encoding="utf-8")
    control_tailer = MiniTailer(control_path, seekback="bytes_before_read")
    control_tailer.poll_once()
    with open(control_path, "ab") as fp:
        fp.write(b"x" * 10 + b"\xff")
    control_tailer.poll_once()
    with open(control_path, "ab") as fp:
        fp.write(b"\n")
    control_tailer.poll_once()
    findings["control_correct_seekback"] = {
        "consumed": control_tailer.consumed,
        "raised": None,
    }
    control_tailer.close()
    return findings


# ======================================================================================
# ⑩ LogRepository.bulk_insert 的 id 回填与单调性
# ======================================================================================


def probe_bulk_insert() -> dict[str, Any]:
    """在临时 sqlite 上跑 ``LogRepository.bulk_insert``，检查 id 回填与单调性。"""
    from sqlalchemy import select
    from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker
    from sqlalchemy.pool import NullPool
    from sqlmodel import SQLModel

    import maa_api.db.models  # noqa: F401 - 注册全部表，create_all 需要
    import maa_api.db.session as db_session
    from maa_api.db.models import LogEntry
    from maa_api.db.repositories.log import LogRepository

    outcome: dict[str, Any] = {}

    async def main(url: str) -> None:
        engine = db_session.make_engine(url, poolclass=NullPool)
        try:
            async with engine.begin() as conn:
                await conn.run_sync(SQLModel.metadata.create_all)
            factory = async_sessionmaker(engine, expire_on_commit=False, class_=AsyncSession)
            async with factory() as session:
                repo = LogRepository(session)
                batch1 = [
                    LogEntry(source="service", level="INFO", content=f"a{index}")
                    for index in range(3)
                ]
                count1 = await repo.bulk_insert(batch1)
                outcome["batch1"] = {"count": count1, "ids": [entry.id for entry in batch1]}
                batch2 = [
                    LogEntry(source="task", level="WARNING", content=f"b{index}")
                    for index in range(2)
                ]
                count2 = await repo.bulk_insert(batch2)
                outcome["batch2"] = {"count": count2, "ids": [entry.id for entry in batch2]}
                outcome["empty_batch"] = await repo.bulk_insert([])
                rows = (
                    (await session.execute(select(LogEntry.id).order_by(LogEntry.id)))
                    .scalars()
                    .all()
                )
                outcome["db_ids"] = list(rows)
                outcome["readable_after_commit"] = batch1[0].content
        finally:
            await engine.dispose()

    with tempfile.TemporaryDirectory(prefix="maa_log_ws_probe_db_") as tmp:
        db_file = Path(tmp) / "probe.db"
        asyncio.run(main(f"sqlite+aiosqlite:///{db_file}"))
    outcome["temp_db_removed_with_tempdir"] = not db_file.exists()

    ids1 = outcome["batch1"]["ids"]
    ids2 = outcome["batch2"]["ids"]
    check_ok = (
        outcome["batch1"]["count"] == 3
        and outcome["batch2"]["count"] == 2
        and outcome["empty_batch"] == 0
        and all(isinstance(value, int) for value in ids1 + ids2)
        and ids1 == sorted(ids1)
        and ids2 == sorted(ids2)
        and min(ids2) > max(ids1)
        and outcome["db_ids"] == ids1 + ids2
        and outcome["readable_after_commit"] == "a0"
    )
    return {
        "check_name": "bulk_insert_backfills_ids_after_commit",
        "check_ok": check_ok,
        "expire_on_commit": False,
        **outcome,
    }


# ======================================================================================
# ⑪ WS 客户端/服务端手段盘点
# ======================================================================================


def probe_ws_client_inventory() -> dict[str, Any]:
    """本机可用的 WS 客户端与服务端实现盘点（含 uvicorn 的 WS 协议类解析）。"""
    packages: dict[str, Any] = {}
    for name in ("websockets", "wsproto", "websocket", "httpx_ws", "aiohttp", "uvicorn"):
        try:
            module = importlib.import_module(name)
        except Exception as exc:  # noqa: BLE001
            packages[name] = {"available": False, "error": f"{type(exc).__name__}: {exc}"}
        else:
            packages[name] = {
                "available": True,
                "version": str(getattr(module, "__version__", "?")),
            }

    executables = {
        name: shutil.which(name)
        for name in ("websocat", "wscat", "wsdump", "curl", "nc", "socat")
    }
    # wsdump 属于系统 Python 的 site-packages，本项目 .venv 里 import websocket 失败
    wsdump_usable_from_venv = bool(packages["websocket"]["available"])

    TestClient = _import_testclient()
    testclient_available = TestClient is not None
    stdlib_socket_available = hasattr(socket, "create_connection")

    uvicorn_ws: dict[str, Any] = {}
    try:
        from uvicorn.protocols.websockets import auto as uvicorn_ws_auto

        protocol = getattr(uvicorn_ws_auto, "AutoWebSocketsProtocol", "missing")
        uvicorn_ws = {
            "AutoWebSocketsProtocol": None if protocol is None else getattr(protocol, "__name__", str(protocol)),
            "resolved": protocol is not None,
        }
    except Exception as exc:  # noqa: BLE001
        uvicorn_ws = {"error": f"{type(exc).__name__}: {exc}"}

    loopback = _probe_uvicorn_ws_upgrade()
    uvicorn_ws["loopback_upgrade_probe"] = loopback

    no_ws_package = not packages["websockets"]["available"] and not packages["wsproto"]["available"]
    check_ok = (
        no_ws_package
        and executables["websocat"] is None
        and testclient_available
        and stdlib_socket_available
        and uvicorn_ws.get("resolved") is False
    )
    return {
        "check_name": "ws_client_tooling_inventory_local",
        "check_ok": check_ok,
        "packages": packages,
        "executables": executables,
        "testclient_available": testclient_available,
        "stdlib_socket_available": stdlib_socket_available,
        "uvicorn_websockets": uvicorn_ws,
        "wsdump_usable_from_venv": wsdump_usable_from_venv,
        "smoke_default_mode": "fastapi.testclient.TestClient（进程内 ASGI，无 socket）",
        "smoke_advisory_mode": (
            "标准库 socket 手写握手；本机 uvicorn 没有 WS 协议实现，真实 uvicorn 会拒绝升级，"
            "advisory 模式当前不可用（除非装 websockets 或 wsproto）"
        ),
    }


def _probe_uvicorn_ws_upgrade() -> dict[str, Any]:
    """起一个真实 uvicorn，用裸 socket 发 WS 握手，看服务端是否接受升级。

    只监听 127.0.0.1 的临时端口，不访问外网；失败一律记进返回值，不影响 required_checks。
    """
    result: dict[str, Any] = {}
    with tempfile.TemporaryDirectory(prefix="maa_log_ws_probe_uvicorn_") as tmp:
        app_path = Path(tmp) / "probe_ws_app.py"
        app_path.write_text(
            "from fastapi import FastAPI, WebSocket\n"
            "app = FastAPI()\n"
            "@app.websocket('/api/ws')\n"
            "async def ws(websocket: WebSocket):\n"
            "    await websocket.accept()\n"
            "    await websocket.close()\n",
            encoding="utf-8",
        )
        port = _free_port()
        result["port"] = port
        process = subprocess.Popen(
            [
                sys.executable,
                "-m",
                "uvicorn",
                "probe_ws_app:app",
                "--host",
                "127.0.0.1",
                "--port",
                str(port),
                "--log-level",
                "warning",
            ],
            cwd=tmp,
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            text=True,
        )
        try:
            if not _wait_for_port("127.0.0.1", port, timeout=10.0):
                result["error"] = "uvicorn 未在 10s 内监听端口"
                return result
            result.update(_raw_ws_handshake("127.0.0.1", port, "/api/ws"))
        except Exception as exc:  # noqa: BLE001
            result["error"] = f"{type(exc).__name__}: {exc}"
        finally:
            process.terminate()
            try:
                output, _ = process.communicate(timeout=5)
            except subprocess.TimeoutExpired:
                process.kill()
                output, _ = process.communicate(timeout=5)
            result["uvicorn_output"] = (output or "").strip().splitlines()[-3:]
    return result


def _free_port() -> int:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as sock:
        sock.bind(("127.0.0.1", 0))
        return int(sock.getsockname()[1])


def _wait_for_port(host: str, port: int, timeout: float) -> bool:
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        try:
            with socket.create_connection((host, port), timeout=0.25):
                return True
        except OSError:
            time.sleep(0.05)
    return False


def _raw_ws_handshake(host: str, port: int, path: str, timeout: float = 5.0) -> dict[str, Any]:
    """标准库裸 socket 的 WS 握手（advisory 模式的客户端手段）。"""
    key = base64.b64encode(os.urandom(16)).decode("ascii")
    request = (
        f"GET {path} HTTP/1.1\r\n"
        f"Host: {host}:{port}\r\n"
        "Upgrade: websocket\r\n"
        "Connection: Upgrade\r\n"
        f"Sec-WebSocket-Key: {key}\r\n"
        "Sec-WebSocket-Version: 13\r\n\r\n"
    )
    with socket.create_connection((host, port), timeout=timeout) as sock:
        sock.sendall(request.encode("ascii"))
        raw = sock.recv(4096)
    head = raw.split(b"\r\n\r\n", 1)[0].decode("latin-1")
    lines = head.split("\r\n")
    return {
        "request_path": path,
        "status_line": lines[0] if lines else "",
        "upgraded": bool(lines) and "101" in lines[0],
        "response_head": lines[:6],
        "sec_websocket_accept_present": any(
            line.lower().startswith("sec-websocket-accept:") for line in lines
        ),
    }


# ======================================================================================
# 环境守卫：确认真实库 / 配置 / 网络都没被碰
# ======================================================================================


def _stat_signature(path: Path) -> Any:
    try:
        stat = path.stat()
    except FileNotFoundError:
        return None
    return [stat.st_size, stat.st_mtime_ns, stat.st_ino]


def _env_guard() -> dict[str, Any]:
    import maa_api.settings as settings_module

    return {
        "real_db": _stat_signature(REAL_DB_PATH),
        "real_config": _stat_signature(REAL_CONFIG_PATH),
        "settings_cache_loaded": settings_module._CACHE is not None,  # noqa: SLF001
    }


# ======================================================================================
# 汇总
# ======================================================================================

PROBES: tuple[Callable[[], dict[str, Any]], ...] = (
    probe_ws_routing,
    probe_ws_close_code_4401,
    probe_ws_missing_route,
    probe_lifespan_context,
    probe_extract_token,
    probe_cross_thread_queue,
    probe_deque_maxlen,
    probe_core_client_dispatch,
    probe_asst_log_tail,
    probe_bulk_insert,
    probe_ws_client_inventory,
)


def _iso_now() -> str:
    return time.strftime("%Y-%m-%dT%H:%M:%S%z", time.localtime())


def run_all_probes() -> dict[str, Any]:
    result: dict[str, Any] = {
        "probe": "M4-01",
        "title": "M4 前置实测：WS 路由与关闭码、跨线程入队、IPC 事件载荷、asst.log tail 语义",
        "generated_at": _iso_now(),
        "environment": _versions(),
        "script": "scripts/probe_log_ws.py",
    }
    guard_before = _env_guard()
    errors: list[dict[str, Any]] = []
    checks: dict[str, bool] = {}
    sections: dict[str, Any] = {}

    for probe in PROBES:
        try:
            data = probe()
        except Exception:  # noqa: BLE001 - 探针自身异常不掩盖其它结论
            errors.append({"probe": probe.__name__, "traceback": traceback.format_exc()})
            continue
        name = str(data.pop("check_name"))
        checks[name] = bool(data.pop("check_ok"))
        sections[probe.__name__] = data

    guard_after = _env_guard()
    guard = {
        "before": guard_before,
        "after": guard_after,
        "real_db_untouched": guard_before["real_db"] == guard_after["real_db"],
        "real_config_untouched": guard_before["real_config"] == guard_after["real_config"],
        "settings_cache_loaded": guard_after["settings_cache_loaded"],
        "network_used": False,
        "maacore_dlopen": False,
    }
    if guard["settings_cache_loaded"]:
        errors.append(
            {
                "probe": "env_guard",
                "traceback": "settings 缓存被填充：探针链上有人调用了 get_settings()，可能读了真实 config.yaml",
            }
        )

    result["required_checks"] = checks
    result["sections"] = sections
    result["env_guard"] = guard
    result["errors"] = errors
    result["probe_ok"] = bool(checks) and all(checks.values()) and not errors
    return result


# ======================================================================================
# 人读结论
# ======================================================================================


def _json_block(value: Any) -> str:
    return json.dumps(value, ensure_ascii=False, indent=2, sort_keys=True)


def _section(
    md: list[str],
    index: int,
    key: str,
    conclusion: str,
    data: Any,
    impact: str,
) -> None:
    md.append(f"## {index}. {key}\n")
    md.append(f"**结论**：{conclusion}\n")
    md.append("**原始数据**：\n")
    md.append("```json")
    md.append(_json_block(data))
    md.append("```\n")
    md.append(f"**对 M4 实现的影响**：{impact}\n")


def build_findings_md(result: dict[str, Any]) -> str:
    checks = result.get("required_checks", {})
    sections = result.get("sections", {})
    env = result.get("environment", {})
    guard = result.get("env_guard", {})
    routing = sections.get("probe_ws_routing", {})
    close_4401 = sections.get("probe_ws_close_code_4401", {})
    missing_route = sections.get("probe_ws_missing_route", {})
    lifespan = sections.get("probe_lifespan_context", {})
    token = sections.get("probe_extract_token", {})
    wakeup = sections.get("probe_cross_thread_queue", {})
    ring = sections.get("probe_deque_maxlen", {})
    dispatch = sections.get("probe_core_client_dispatch", {})
    tail = sections.get("probe_asst_log_tail", {})
    bulk = sections.get("probe_bulk_insert", {})
    inventory = sections.get("probe_ws_client_inventory", {})

    md: list[str] = []
    md.append("# M4-01 前置实测结论：WS 路由与关闭码、跨线程入队、IPC 事件载荷、asst.log tail 语义\n")
    md.append(
        "本文件由 `scripts/probe_log_ws.py` 生成（重跑覆盖）。所有实验都在临时目录与进程内完成："
        "不联网、不 dlopen MaaCore、不触碰真实 `resource/maa_api.db` 与 `config.yaml`、不依赖真机。"
        "实测环境：" + ", ".join(f"`{name}={value}`" for name, value in sorted(env.items())) + "。\n"
    )

    md.append("## 必读结论（给 M4 实现卡）\n")
    md.append(
        "1. **WS 卡 verify 只能查模块级 `router.routes[*].path`**（"
        f"`{checks.get('ws_route_hidden_from_openapi_and_route_contexts')}`）："
        f"`app.openapi()['paths']` = {routing.get('openapi_paths')} 不含 WS 路径，"
        "`fastapi.routing.iter_route_contexts(app.routes)` 对被 include 的 WS 路由只给 `path=''` 的"
        f"上下文对象（实测 {routing.get('ws_context_placeholder_count')} 条，`type(...).__name__ == "
        f"'{routing.get('ws_context_placeholder_type')}'`、`has_tags=True`）；模块级 router 的 "
        f"`{routing.get('module_router_paths')}` 才可靠。推荐写成 "
        f"`{routing.get('recommended_verify_expression')}`。\n"
    )
    md.append(
        "2. **鉴权失败走 accept → close(4401)**，TestClient 侧 `WebSocketDisconnect.code == 4401`；"
        "而**连不存在的 WS 路径同样是 `WebSocketDisconnect`，code 是 1000**（把一个已注册的 HTTP 路径"
        "当 WS 连也得到 1000）—— 写测试时不能拿「抛了 WebSocketDisconnect」或 code=1000 当路由缺失的"
        "证据，路由存在性只能静态断言模块级 `router.routes`（见第 1 条）。\n"
    )
    md.append(
        "3. **`LogHub.offer` 必须用 `loop.call_soon_threadsafe` 投递**，"
        "不能直接 `asyncio.Queue.put_nowait`：裸线程 put 在无其它唤醒源时"
        f"（实测 {wakeup.get('raw_put_nowait', {}).get('window_s')}s 窗口内）"
        "完全不唤醒循环，消费者可能无限期挂着；`call_soon_threadsafe` 实测延迟 "
        f"{wakeup.get('call_soon_threadsafe', {}).get('latency_ms')} ms。\n"
    )
    md.append(
        "4. **`LogRecord.ts` 取 handler 内的到达时刻**：`CoreClient.on()` 的 handler 只收到 payload，"
        f"LOG 的键集是 {dispatch.get('handler_received', {}).get('LOG', {}).get('keys')}、"
        f"CALLBACK 是 {dispatch.get('handler_received', {}).get('CALLBACK', {}).get('keys')}，"
        "envelope `ts` 到不了 LogHub（只有 `supervisor.note_event` 拿得到）。若一定要用 IPC ts，"
        "得改 `CoreClient` 的分派把 envelope 一并交给 handler —— 那是 M1 模块的改动，M4 卡要有意识。\n"
    )
    md.append(
        "5. **asst.log tail 照 docs/06 §5.4 的判据写，但 `_drain` 的半行回退不能用 "
        "`len(line.encode('utf-8'))`**：`errors=\"replace\"` 下含非法字节的半行编码后更长，"
        "实测会多退到前一行的中间（读到重复内容）或抛 `ValueError: negative seek position`。"
        "正确做法是读之前先记 `tell()`，半行时 seek 回该位置。\n"
    )
    md.append(
        "6. **本机没有任何 WS 依赖**：`websockets` / `wsproto` 都装不上（缺包），`websocat` 不存在，"
        "uvicorn 因此解析不出 WS 协议类（`AutoWebSocketsProtocol is None`），真实 uvicorn 会拒绝升级"
        f"（裸 socket 实测 status line = `{inventory.get('uvicorn_websockets', {}).get('loopback_upgrade_probe', {}).get('status_line')}`）。"
        "冒烟默认模式只能用 `fastapi.testclient`；advisory 的裸 socket 模式在装上 `websockets` 之前不可用。\n"
    )

    _section(
        md,
        1,
        "ws_route_hidden_from_openapi_and_route_contexts",
        "WS 路由既不进 `app.openapi()['paths']`，也不出现在 `iter_route_contexts(app.routes)` 的 path 里；"
        "被 include 的 WS 路由只留下一个 `path=''` 的上下文对象。模块级 `router.routes[*].path` 才给得出 "
        "`/api/ws`。",
        {
            "openapi_paths": routing.get("openapi_paths"),
            "ws_paths_hidden_from_openapi": routing.get("ws_paths_hidden_from_openapi"),
            "iter_route_contexts": routing.get("iter_route_contexts"),
            "ws_paths_hidden_from_iter_route_contexts": routing.get(
                "ws_paths_hidden_from_iter_route_contexts"
            ),
            "module_router_paths": routing.get("module_router_paths"),
            "module_router_route_types": routing.get("module_router_route_types"),
            "recommended_verify_result": routing.get("recommended_verify_result"),
            "ws_context_placeholder_count": routing.get("ws_context_placeholder_count"),
            "ws_context_placeholder_type": routing.get("ws_context_placeholder_type"),
            "app_route_types": routing.get("app_route_types"),
            "app_routes_path_attribute_errors": routing.get("app_routes_path_attribute_errors"),
            "included_router_private_original_router_paths": routing.get(
                "included_router_private_original_router_paths"
            ),
        },
        "WS 卡的 verify 一律写成 `sorted(r.path for r in maa_api.api.ws.router.routes "
        "if isinstance(r, fastapi.routing.APIWebSocketRoute))`（或直接断言模块级 `router.routes` 的 path 集合），"
        "**不要**用 `{r.path for r in app.routes}`（`_IncludedRouter` 没有 `.path`，M3 已实测必炸），"
        "也不要用 `set(app.openapi()['paths'])`（WS 路由根本不在 spec 里）。"
        "`_IncludedRouter.original_router.routes` 虽然能挖出路径，但那是私有属性，不要写进门禁。",
    )

    _section(
        md,
        2,
        "ws_close_4401_surfaces_as_disconnect_code",
        "`accept()` 之后 `close(code=4401)`，TestClient 侧 `with client.websocket_connect(...)` 在 "
        "`receive_text()` 处抛 `WebSocketDisconnect`，`code == 4401`。",
        {"accept_then_close_4401": close_4401.get("accept_then_close_4401")},
        "鉴权失败用 accept → `close(code=4401)` 的写法可以在用例里断言 `exc.code == 4401`（docs/05 §5.4 的四个 "
        "close code 表就是可测契约），不必去读 HTTP 状态码。",
    )

    _section(
        md,
        3,
        "missing_ws_path_disconnect_code_1000",
        "连一个没有注册的 WS 路径，TestClient 抛出的同样是 `WebSocketDisconnect`，但 `code == 1000`。"
        "这个 1000 **不能**当作「路由存在」的证据：把一个**已注册的 HTTP 路径**当 WS 连，实测同样是 "
        f"`code == 1000`（`missing_code_equals_http_route_control="
        f"{missing_route.get('missing_code_equals_http_route_control')}`）。"
        "另一方面，服务端在 `accept()` 之前 `close(1008)` 时，TestClient 侧**确实**能读到 "
        f"`code == {missing_route.get('reject_before_accept_code')}`（不会像真机浏览器那样只剩 HTTP 403），"
        "所以 1000 只表示「这次握手没成功」，与路由是否存在无关。",
        {
            "missing_route": missing_route.get("missing_route"),
            "http_route_used_as_ws_control": missing_route.get("http_route_used_as_ws_control"),
            "reject_before_accept_control": missing_route.get("reject_before_accept_control"),
            "missing_code_equals_http_route_control": missing_route.get(
                "missing_code_equals_http_route_control"
            ),
        },
        "WS 用例里凡是要证明「鉴权失败」的地方，断言必须是 `exc.code == 4401` 而不是「抛了异常」；"
        "反过来，负向用例要证明路由缺失也不能靠 code（1000 与「HTTP 路径被当 WS 连」撞在一起），"
        "只能靠模块级 `router.routes[*].path` 的静态断言（见第 1 条）。"
        "另外：docs/06 §7.1 说「握手阶段返回 HTTP 403 时客户端拿不到原因」——那是**真实浏览器 + 真实服务端**"
        "的行为；TestClient 走 ASGI 直连，pre-accept 的 close code 反而可见（实测 1008）。"
        "但 M4 的鉴权失败仍应照 docs/05 §5.4 用 accept → `close(4401)`，这样真机与用例行为一致。",
    )

    _section(
        md,
        4,
        "testclient_lifespan_needs_context_manager",
        "`TestClient(app)` 直接 `.get()` 可以拿到 200，但 lifespan 的 enter/exit 都不执行；只有 "
        "`with TestClient(app) as client:` 才触发 enter（退出时触发 exit）。",
        lifespan,
        "WS 卡的用例必须用 with 上下文（否则 LogHub 后台任务、`session_factory` 等 lifespan 资源都不存在）。"
        "`tests/api/conftest.py` 的 `make_client(app)` 已经 `__enter__`，照它写即可。",
    )

    _section(
        md,
        5,
        "extract_token_ws_query_precedes_cookie",
        "`maa_api.api.deps.extract_token(websocket)` 在真实 WS 握手上能同时取到 query 与 cookie，"
        "优先级 query > cookie（两者都给时返回 query；query 为空串时落到 cookie）。",
        token.get("cases"),
        "WS 卡直接复用 `extract_token(websocket)`（它本来就是 `Request | WebSocket` 双签名），不要另写一套。"
        "写用例时 cookie 有两条可用路径：`headers={'cookie': f'{COOKIE_NAME}=...'}` 或 per-request 的 "
        "`client.websocket_connect(url, cookies={COOKIE_NAME: ...})`（实测两者都能落到 cookie 渠道）；"
        "**client 级 cookie jar 不行**（`client.cookies.set(..., domain='testserver')` 实测不进 WS 握手 scope）。",
    )

    _section(
        md,
        6,
        "cross_thread_put_nowait_does_not_wake_loop",
        "事件循环在另一条线程里 `await queue.get()` 且没有任何定时器/IO 时，主线程 `queue.put_nowait(item)` "
        "**不会**唤醒循环：实测 500ms 窗口内消费者收不到（`delivered_within_window=false`），之后显式 "
        "`call_soon_threadsafe(lambda: None)` 才把积压的条目送达（说明条目没丢，只是没人唤醒循环）。"
        "改用 `loop.call_soon_threadsafe(queue.put_nowait, item)` 时同一场景毫秒级送达。"
        "另外，循环开 debug 时裸 put 会直接在生产者线程抛 "
        "`RuntimeError: Non-thread-safe operation invoked on an event loop other than the current one`，"
        "且条目再也送不到消费者（getter future 的 callback 没被调度）。",
        wakeup,
        "`LogHub.offer` 的 WS 广播出口必须 `loop.call_soon_threadsafe(...)`（或让 `offer` 只在 loop 线程被调），"
        "绝不能把裸 `asyncio.Queue.put_nowait` 暴露给 logging handler / tailer 线程。"
        "顺带：`asyncio.Queue` 本身不是线程安全的，跨线程投递只走这一个入口。",
    )

    _section(
        md,
        7,
        "deque_maxlen_thread_appends_keep_length",
        f"8 线程 × 5000 次 append 到 `deque(maxlen=500)`：最终长度恒为 {ring.get('final_length')}，"
        "全程没有任何时刻超过 maxlen，也没有异常。",
        ring,
        "docs/06 §6.1 的环形缓冲可以放心用 `deque(maxlen=ring_size)` + 多线程 `append`，不需要额外加锁；"
        "`id` 自增与 `append` 分离（先加锁取 id，再 append），顺序不影响环形缓冲的正确性。"
        "附加记录：无锁 `n += 1` 的对照在本机 3.13.3 上没复现重复（见原始数据的 "
        "`unsynchronized_id_counter_control`），但这只是当前 GIL 的表现，不构成去掉 `_seq_lock` 的理由。",
    )

    _section(
        md,
        8,
        "handler_payload_lacks_envelope_ts",
        "用生产代码造出的 LOG / CALLBACK 事件信封都是 `{type, ts, payload}`；经 `CoreClient._handle_event` "
        "分派后，`on()` 注册的 handler 收到的是 **payload 本身**，键集分别是 "
        f"{dispatch.get('handler_received', {}).get('LOG', {}).get('keys')} 与 "
        f"{dispatch.get('handler_received', {}).get('CALLBACK', {}).get('keys')}，"
        "**没有** envelope `ts`。只有 `supervisor.note_event` 收得到完整信封。",
        dispatch,
        "M4 的 `LogHub` 从 handler 拿不到 IPC 事件的 `ts`：`LogRecord.ts` 只能取 handler 内的到达时刻"
        "（`time.time()`），这会引入「子进程写事件 → 主进程消费线程 → loop 分派」的排队延迟（正常 <1ms，"
        "洪峰时可到几十 ms，但不影响排序与补发，因为 id 由 LogHub 分配）。"
        "如果业务上要求 ts 等于子进程事件时间，必须单独开一张卡改 `CoreClient`（例如给 handler 传 "
        "`(payload, ts)` 或新增 `on_event`），M4 不应偷偷改 M1 的公开签名。",
    )

    _section(
        md,
        9,
        "asst_log_tail_rotation_truncation_half_line_replace",
        "五条 tail 语义全部按 docs/06 §5.4 的判据成立：①首次打开 seek 到末尾不回放历史；"
        "②`os.rename` 轮转后新 `asst.log` 的 `st_ino` 变化、旧句柄仍能把 rename 前写入的尾部读到 EOF；"
        "③截断时 `st_ino` 不变而 `st_size < 上次 offset`；④没有换行符的半行不消费（偏移停在行首，补上换行后才消费）；"
        "⑤`errors=\"replace\"` 读非法 UTF-8 字节不抛、替换成 U+FFFD（`errors=\"strict\"` 对照抛 UnicodeDecodeError）。",
        tail,
        "tail 实现照 docs/06 §5.4 的 `_poll_once` 判据写（inode 变化 + 尺寸回退两条都要）。"
        "**唯一必须改的一处**：`_drain` 的半行回退不能照抄 `seek(tell() - len(line.encode('utf-8')))` —— "
        "`errors=\"replace\"` 下非法字节会让重新编码的长度大于实际读入的字节数，实测多退到前一行中间"
        "（下一次读到重复的行片段）或在文件头部直接 `ValueError: negative seek position`。"
        "正确做法：读之前 `position_before = fp.tell()`，半行时 `fp.seek(position_before)`（探针对照已验证）。",
    )

    _section(
        md,
        10,
        "bulk_insert_backfills_ids_after_commit",
        "临时 sqlite 上 `LogRepository.bulk_insert` 两批写入后，入参 `LogEntry` 实例的 `id` 已回填为 "
        f"{bulk.get('batch1', {}).get('ids')} 与 {bulk.get('batch2', {}).get('ids')}，与库内行一致且严格单调；"
        "空序列返回 0；会话是 `expire_on_commit=False`，提交后字段仍可读。整库建在 `tempfile` 临时目录里，"
        f"出 `TemporaryDirectory` 即删（`temp_db_removed_with_tempdir={bulk.get('temp_db_removed_with_tempdir')}`）。",
        bulk,
        "广播不能等落库：`LogRecord.id` 按 docs/06 §2 由 `LogHub` 内存自增分配（WS 推送立刻带稳定 id，"
        "前端据此补发）。探针这条结论保证的是另一半：攒批落库时入参 `LogEntry.id` 在提交后已经回填、"
        "严格单调且与库内一致，所以「落库侧的 id 与内存 id 对齐/续号（服务重启后从库内 max+1 继续）」"
        "不会踩到「提交后 id 还是 None」的坑，不需要额外 flush + refresh。",
    )

    _section(
        md,
        11,
        "ws_client_tooling_inventory_local",
        "本机没有任何**能装进本项目 .venv** 的真实 WS 客户端/服务端实现：`websockets`、`wsproto`、"
        "`websocket`（websocket-client）、`httpx_ws`、`aiohttp` 全部 import 失败，`websocat`/`wscat` 不存在"
        "（`wsdump` 命令存在，但属于系统 Python 的 site-packages，`.venv` 里 import `websocket` 仍失败）；"
        "uvicorn 0.53.0 的 `AutoWebSocketsProtocol` 解析为 `None`，起真实 uvicorn 用裸 socket 发 WS 握手"
        f"被拒（实测 status line = `{inventory.get('uvicorn_websockets', {}).get('loopback_upgrade_probe', {}).get('status_line')}`，"
        f"日志 `{inventory.get('uvicorn_websockets', {}).get('loopback_upgrade_probe', {}).get('uvicorn_output')}`）。"
        "可用手段只剩 `fastapi.testclient.TestClient`（进程内 ASGI，无 socket）与标准库 `socket`（裸握手，"
        "当前会被 uvicorn 拒）。",
        {
            "packages": inventory.get("packages"),
            "executables": inventory.get("executables"),
            "testclient_available": inventory.get("testclient_available"),
            "stdlib_socket_available": inventory.get("stdlib_socket_available"),
            "uvicorn_websockets": inventory.get("uvicorn_websockets"),
            "smoke_default_mode": inventory.get("smoke_default_mode"),
            "smoke_advisory_mode": inventory.get("smoke_advisory_mode"),
        },
        "WS 卡的冒烟脚本默认模式（TestClient）可以照 M3 的 `scripts/api_smoke.py` 写；"
        "`--serve` 那一档如果真起 uvicorn，WS 升级在没有 `websockets`/`wsproto` 的情况下必然失败"
        "（实测 404 + `Unsupported upgrade request.` + `No supported WebSocket library detected.`），"
        "所以要么把 `websockets` 加进 `pyproject.toml`（M4 依赖卡决定），要么 advisory 档只做"
        "「端点存在性 + 裸 socket 被拒的现状记录」，不要把 WS 实时连通写进必过项。"
        "另外 `wsdump` 虽然存在于系统 Python 路径，但不能作为 .venv 内的冒烟手段。",
    )

    md.append("## 12. required_checks 一览\n")
    md.append("| check | 结果 |")
    md.append("|---|---|")
    for name, ok in checks.items():
        md.append(f"| `{name}` | {'✅ true' if ok else '❌ false'} |")
    md.append("")
    md.append(
        f"`probe_ok = {result.get('probe_ok')}`（11 条 required_checks 全 true 且探针自身无异常）。\n"
    )

    md.append("## 13. 环境守卫与探针自身错误\n")
    md.append(
        "真实库/配置未被触碰："
        f"`real_db_untouched={guard.get('real_db_untouched')}`、"
        f"`real_config_untouched={guard.get('real_config_untouched')}`、"
        f"`settings_cache_loaded={guard.get('settings_cache_loaded')}`（false = 没读过 config.yaml）、"
        "未联网、未 dlopen MaaCore。"
        f"注意 `real_db={(guard.get('after') or {}).get('real_db')}`：仓库根当前**没有** `resource/maa_api.db`，"
        "探针也没有创建它（第 10 条的库整建在临时目录里，用完即删）。\n"
    )
    md.append("```json")
    md.append(_json_block(guard))
    md.append("```\n")
    if result.get("errors"):
        md.append("探针自身错误：\n")
        for item in result["errors"]:
            md.append(f"```\n{item}\n```\n")
    else:
        md.append("探针自身错误：无。\n")

    md.append("## 14. 复现方式\n")
    md.append("```bash")
    md.append(".venv/bin/python scripts/probe_log_ws.py")
    md.append("```\n")
    md.append(
        "重跑覆盖 `tests/fixtures/log_ws_probe_result.json` 与 "
        "`tests/fixtures/log_ws_probe_findings.md`（后者含时间戳/端口等不稳定字段，不要直接 diff）。"
    )
    return "\n".join(md) + "\n"


# ======================================================================================
# main
# ======================================================================================


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        prog="probe_log_ws.py",
        description=(
            "M4-01 前置实测：WS 路由与关闭码、跨线程入队、IPC 事件载荷、asst.log tail 语义。"
            "全部实验在临时目录与进程内完成，不联网、不碰真实库/配置、不 dlopen MaaCore。"
        ),
        epilog="产物默认写入 tests/fixtures/log_ws_probe_result.json 与 tests/fixtures/log_ws_probe_findings.md。",
    )
    parser.add_argument("--out-json", default=str(DEFAULT_OUT_JSON), help="实测结果 JSON 路径")
    parser.add_argument("--out-md", default=str(DEFAULT_OUT_MD), help="人读结论 Markdown 路径")
    args = parser.parse_args(argv)

    out_json = Path(args.out_json)
    out_md = Path(args.out_md)
    if not out_json.is_absolute():
        out_json = REPO_ROOT / out_json
    if not out_md.is_absolute():
        out_md = REPO_ROOT / out_md

    result = run_all_probes()

    out_json.parent.mkdir(parents=True, exist_ok=True)
    out_md.parent.mkdir(parents=True, exist_ok=True)
    out_json.write_text(
        json.dumps(result, ensure_ascii=False, indent=2, sort_keys=True) + "\n", encoding="utf-8"
    )
    out_md.write_text(build_findings_md(result), encoding="utf-8")
    print(f"[probe] wrote {out_json}", flush=True)
    print(f"[probe] wrote {out_md}", flush=True)

    for item in result.get("errors", []):
        print(f"[probe] ERROR {item['probe']}: {item['traceback'].strip().splitlines()[-1]}", file=sys.stderr)

    checks = result.get("required_checks", {})
    failed = [name for name, ok in checks.items() if not ok]
    print(f"[probe] required_checks: {len(checks)} 条，通过 {len(checks) - len(failed)} 条", flush=True)
    if failed:
        print(f"[probe] FAILED required checks: {failed}", file=sys.stderr, flush=True)
        return 1
    if result.get("errors"):
        print("[probe] 探针自身有异常，见 JSON/md 的 errors", file=sys.stderr, flush=True)
        return 1
    print("PROBE OK", flush=True)
    return 0


if __name__ == "__main__":
    sys.exit(main())
