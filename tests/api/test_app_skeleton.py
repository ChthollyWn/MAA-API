"""M3-09 应用装配验收：lifespan、CORS、OpenAPI 元数据与尾斜杠（docs/02 §7、docs/05 §11/§5.1/§2.5）。

被测对象是模块级的 :data:`maa_api.main.app`（本卡起 ``tests/api`` **允许** import
``maa_api.main``；此前几张卡的「不 import main」约定随 M3-09 失效）。八个验收点：

1. lifespan 真的跑了迁移：临时库里 ``alembic_version`` 恰好一行，且等于迁移目录的
   head（**不写死序号**：M2-05 实测教训，head 会随新迁移前移）。
2. ``/docs`` 与 ``/openapi.json`` 可用；``tags`` 顺序与 :data:`~maa_api.main.TAGS`
   一致；每条 API 路由的 operationId 是 ``{tag}_{函数名}`` 且唯一。
3. 带尾斜杠的路径 404 而不是 307（``redirect_slashes=False``，docs/05 §2.5）。
4. CORS 预检矩阵：本机白名单与局域网 regex 回显 origin + credentials；外部 origin
   不回显；regex 不越界（``172.32`` 与 ``192.168.1.1.evil.com``）。
5. 未知 ``/api`` 路径是**统一错误体**的 404，不是 FastAPI 默认 ``{"detail": ...}``。
6. 配了 token 时业务端点 401 / 带 ``X-Token`` 200；``/api/system/health`` 免鉴权
   且 ``auth_enabled=true``、``started_at`` 已由 lifespan 写入。
7. 免鉴权模式的启动 warning 文案（直接调 :func:`~maa_api.main.warn_if_auth_disabled`
   + 断言 lifespan 确实调用它；TestClient 线程里的 caplog 捕获不稳）。
8. ``import maa_api.main`` 不拉内核 / 旧栈且耗时 < 5s（子进程复跑 verify #1）。

夹具顺序：每个用例都同时要 ``tmp_settings``（lifespan 第 1 步会 ``load_settings()``，
必须是临时配置）与 ``isolated_db``（lifespan 第 2 步会读 ``session.SYNC_URL`` 跑迁移），
两者都排在 ``make_client`` 之前 —— 夹具按参数顺序初始化，``make_client`` 一进
``TestClient`` 上下文 lifespan 就跑完了。
"""

from __future__ import annotations

import logging
import subprocess
import sys
from concurrent.futures import ThreadPoolExecutor
from collections.abc import Iterator
from datetime import datetime
from pathlib import Path

import pytest
from fastapi import FastAPI
from alembic.config import Config
from alembic.script import ScriptDirectory
from fastapi.routing import iter_route_contexts
from fastapi.testclient import TestClient
from sqlalchemy import create_engine, text

import maa_api.main as main_module
from maa_api.core.supervisor import CoreState
from maa_api.api import deps
from maa_api.api.errors import register_exception_handlers
from maa_api.db import session as db_session
from maa_api.main import (
    AUTH_DISABLED_WARNING,
    CORS_HEADERS,
    CORS_METHODS,
    TAGS,
    app,
    warn_if_auth_disabled,
)
from maa_api.services.log_hub import LogHub, LogHubHandler

#: 仓库根：tests/api/test_app_skeleton.py → parents[2]。不依赖 CWD。
REPO_ROOT = Path(__file__).resolve().parents[2]
TOKEN = "s3cret-token"

#: M6 路由注册的代表路径；旧 router/adb、router/maa、router/template 仍不许出现。
EXPECTED_API_PATHS = {
    "/api/system/health",
    "/api/system/auth/cookie",
    "/api/tasks/types",
    "/api/tasks/types/{type_name}",
    "/api/tasks/validate",
    "/api/device/status",
    "/api/settings",
    "/api/settings/schema",
}
#: 旧装配的端点前缀（docs/02 §9：这些路由不迁移，直接废弃）。
RETIRED_PATH_PREFIXES = ("/api/adb", "/api/maa")
RETIRED_EXACT_PATHS = {"/", "/daily"}


@pytest.fixture(autouse=True)
def _clean_rate_limiter() -> Iterator[None]:
    """每个用例前后清空鉴权失败计数，避免 401 用例互相污染（M3-06 限流）。"""
    deps.reset_rate_limiter()
    yield
    deps.reset_rate_limiter()


def _alembic_head() -> str:
    """当前迁移目录的 head（不写死序号，M2-05 实测教训）。"""
    cfg = Config(str(REPO_ROOT / "alembic.ini"))
    cfg.set_main_option("script_location", str(REPO_ROOT / "maa_api" / "db" / "migrations"))
    cfg.set_main_option("sqlalchemy.url", db_session.SYNC_URL)
    head = ScriptDirectory.from_config(cfg).get_current_head()
    assert head is not None, "迁移目录没有 head"
    return head


def _alembic_version_rows() -> list[str]:
    """读临时库 ``alembic_version`` 的全部行（同步引擎，与请求侧异步引擎互不干扰）。"""
    engine = create_engine(db_session.SYNC_URL)
    try:
        with engine.connect() as conn:
            return [
                row[0]
                for row in conn.execute(text("SELECT version_num FROM alembic_version"))
            ]
    finally:
        engine.dispose()


def _operation_ids(spec: dict) -> list[str]:
    """OpenAPI 文档里全部 operationId（保持 path/method 遍历顺序）。"""
    return [
        operation["operationId"]
        for path_item in spec["paths"].values()
        for operation in path_item.values()
        if isinstance(operation, dict) and "operationId" in operation
    ]


def _preflight(client: TestClient, origin: str):
    """发一次 CORS 预检（GET + X-Token/Content-Type 请求头）。"""
    return client.options(
        "/api/tasks/types",
        headers={
            "Origin": origin,
            "Access-Control-Request-Method": "GET",
            "Access-Control-Request-Headers": "X-Token, Content-Type",
        },
    )


class _LifecycleSupervisor:
    def __init__(self, _config, *, on_crash, on_state_change) -> None:
        self.state = CoreState.READY
        self.generation = 1
        self.pid = 123
        self._on_state_change = on_state_change

    async def start(self, **_kwargs) -> None:
        self._on_state_change(self.state)

    async def stop(self, **_kwargs) -> None:
        return None


class _LifecycleClient:
    def __init__(self, _supervisor, **_kwargs) -> None:
        self.handlers: dict[str, list] = {}

    def on(self, event_type, handler) -> None:
        self.handlers.setdefault(event_type, []).append(handler)

    def start_consumer(self) -> None:
        return None

    def close(self) -> None:
        return None


class _LifecycleDeviceManager:
    def __init__(self, _settings, _client, *, broadcast, core_id) -> None:
        self.broadcast = broadcast
        self.core_id = core_id
        self.state = "disconnected"
        self.start_calls: list[dict] = []
        self.closed = False
        self.connection_events: list[tuple[str, dict]] = []

    async def connect_with_retry(self, **kwargs) -> bool:
        self.start_calls.append(kwargs)
        return False

    def on_core_connection_event(self, what, details) -> None:
        self.connection_events.append((what, details))

    def snapshot(self) -> dict:
        return {"core_id": self.core_id, "state": self.state, "address": "offline:5555"}

    async def close(self) -> None:
        self.closed = True


class _LifecycleRunner:
    def __init__(self, *_args, **_kwargs) -> None:
        import asyncio

        self.operation_lock = asyncio.Lock()

    async def start(self) -> None:
        return None

    async def stop(self) -> None:
        return None

    def wake(self) -> None:
        return None

    def notify_core_state(self, _state=None) -> None:
        return None

    def notify_core_crash(self, _record=None) -> None:
        return None

    def log_context(self, _msg, _details):
        return None


@pytest.fixture(autouse=True)
def _fake_application_dependencies():
    """Run app lifespan with deterministic adapters; never load real MaaCore/ADB."""
    state = app.state
    names = (
        "core_supervisor_factory",
        "core_client_factory",
        "device_manager_factory",
        "pipeline_runner_factory",
    )
    old = {name: getattr(state, name, None) for name in names}
    state.core_supervisor_factory = _LifecycleSupervisor
    state.core_client_factory = _LifecycleClient
    state.device_manager_factory = _LifecycleDeviceManager
    state.pipeline_runner_factory = _LifecycleRunner
    try:
        yield
    finally:
        for name in names:
            if old[name] is None:
                if hasattr(state, name):
                    delattr(state, name)
            else:
                setattr(state, name, old[name])


# ----------------------------------------------------------------------
# 1. lifespan 真的跑了迁移
# ----------------------------------------------------------------------


def test_lifespan_migrates_the_temp_db_to_head(
    tmp_path: Path, tmp_settings, isolated_db, make_client
) -> None:
    """进入 TestClient 即执行 lifespan 第 1–2 步：配置加载 + 迁移到 head。

    断言版本行落在**临时库**里（``isolated_db`` 换掉了模块级 engine/session_factory
    与三条 URL，M2-13 实测），绝不碰仓库真实 ``resource/maa_api.db``。
    """
    client = make_client(app)
    assert client.get("/api/system/health").status_code == 200

    assert db_session.DB_PATH == tmp_path / "maa_api.db"
    assert db_session.DB_PATH.exists()
    rows = _alembic_version_rows()
    assert len(rows) == 1, rows
    assert rows[0] == _alembic_head()


def test_lifespan_loads_setting_service_and_device_manager(
    tmp_settings, isolated_db, make_client
) -> None:
    client = make_client(app)

    settings = client.get("/api/settings")
    device = client.get("/api/device/status")

    assert settings.status_code == 200, settings.text
    assert device.status_code == 200, device.text
    assert device.json() == app.state.device_manager.snapshot()
    assert app.state.setting_service._device_manager is app.state.device_manager
    assert app.state.device_manager.start_calls == [{"reason": "startup"}]


# ----------------------------------------------------------------------
# 2. /docs、/openapi.json、tags 顺序与 operationId
# ----------------------------------------------------------------------


def test_openapi_metadata_tags_and_operation_ids(
    tmp_settings, isolated_db, make_client
) -> None:
    """文档可用、tag 顺序与 TAGS 一致、operationId 形如 ``{tag}_{name}`` 且唯一。"""
    client = make_client(app)

    assert client.get("/docs").status_code == 200
    response = client.get("/openapi.json")
    assert response.status_code == 200
    spec = response.json()

    assert spec["info"]["title"] == "MAA-API"
    assert spec["info"]["version"] == main_module.service_version()

    tag_names = [entry["name"] for entry in spec["tags"]]
    assert tag_names == [entry["name"] for entry in TAGS]
    assert len(tag_names) == 15
    assert tag_names[0] == "system" and tag_names[-1] == "ws"

    ids = _operation_ids(spec)
    assert ids, "OpenAPI 文档里没有 operationId"
    assert len(set(ids)) == len(ids), ids

    # 每条已注册 API 路由的 operationId 必须等于 custom_operation_id 的结果；
    # 反向也要成立（文档里不能出现没注册过的 operationId）。
    # 注意：iter_route_contexts 对内置 /docs、/openapi.json 等给的是 RouteContext
    # （代理 Starlette Route，**没有** .tags 属性），所以要 getattr 取默认值，
    # 不能直接点属性。
    expected = {
        f"{tags[0]}_{context.name}"
        for context in iter_route_contexts(app.routes)
        if (tags := getattr(context, "tags", None))
        and str(getattr(context, "path", "")).startswith("/api")
    }
    assert set(ids) == expected, (ids, expected)
    assert all(operation_id.split("_")[0] in tag_names for operation_id in ids)


def test_current_routers_are_registered(
    tmp_settings, isolated_db, make_client
) -> None:
    """新 device/settings routes 已装配，旧端点与旧 static 挂载仍不在。"""
    client = make_client(app)
    paths = set(client.get("/openapi.json").json()["paths"])

    assert EXPECTED_API_PATHS <= paths
    assert not any(path.startswith(RETIRED_PATH_PREFIXES) for path in paths)
    assert not (paths & RETIRED_EXACT_PATHS)
    # 旧 static 是 mount，不会出现在 openapi paths 里，直接在 app.routes 上查。
    assert not any(getattr(route, "path", None) == "/static" for route in app.routes)


# ----------------------------------------------------------------------
# 3. 尾斜杠：404 而不是 307
# ----------------------------------------------------------------------


def test_trailing_slash_returns_404_not_307(
    tmp_settings, isolated_db, make_client
) -> None:
    """``redirect_slashes=False``：``/api/tasks/types/`` 不重定向（docs/05 §2.5）。"""
    client = make_client(app)
    response = client.get("/api/tasks/types/", follow_redirects=False)

    assert response.status_code == 404, response.text
    assert response.headers.get("location") is None
    assert response.json()["error"]["code"] == "NOT_FOUND"


# ----------------------------------------------------------------------
# 4. CORS 预检矩阵
# ----------------------------------------------------------------------


@pytest.mark.parametrize(
    "origin",
    ["http://localhost:8002", "http://192.168.1.10:5173", "https://10.0.0.7:8002"],
)
def test_cors_preflight_allows_whitelist_and_lan(
    tmp_settings, isolated_db, make_client, origin: str
) -> None:
    """白名单 origin 与 RFC 1918 局域网 origin 都回显 origin + credentials。"""
    client = make_client(app)
    response = _preflight(client, origin)

    assert response.status_code == 200, response.text
    assert response.headers["access-control-allow-origin"] == origin
    assert response.headers["access-control-allow-credentials"] == "true"

    methods = response.headers["access-control-allow-methods"]
    for method in CORS_METHODS:
        assert method in methods, methods
    allowed_headers = response.headers["access-control-allow-headers"].lower()
    for header in CORS_HEADERS:
        assert header.lower() in allowed_headers, allowed_headers


@pytest.mark.parametrize(
    "origin",
    [
        "http://evil.example",  # 完全外部
        "http://192.168.1.1.evil.com",  # 前缀像私网、实为外部域名（regex fullmatch 不匹配）
        "http://172.32.0.1:8002",  # 172.32 不在 172.16–31 私网段内
        "http://11.0.0.1:8002",  # 11.x 不是私网段（regex 只认 192.168. / 10. / 172.16–31.）
    ],
)
def test_cors_preflight_rejects_foreign_origins(
    tmp_settings, isolated_db, make_client, origin: str
) -> None:
    """非白名单 origin 的预检不带 ``access-control-allow-origin``（浏览器据此拒绝）。"""
    client = make_client(app)
    response = _preflight(client, origin)

    assert "access-control-allow-origin" not in response.headers
    assert response.status_code == 400, response.text


def test_cors_simple_response_echoes_allowed_origin(
    tmp_settings, isolated_db, make_client
) -> None:
    """非预检请求同样回显具体 origin + credentials（不能用通配符，否则浏览器拒绝）。"""
    client = make_client(app)
    response = client.get(
        "/api/system/health", headers={"Origin": "http://localhost:8002"}
    )

    assert response.status_code == 200
    assert response.headers["access-control-allow-origin"] == "http://localhost:8002"
    assert response.headers["access-control-allow-credentials"] == "true"


def test_request_trace_headers_are_echoed_generated_and_isolated(
    tmp_settings, isolated_db, make_client
) -> None:
    """Concurrent response headers and WebSocket log payloads retain each request id."""
    client = make_client(app)
    generated = client.get("/api/system/health")
    requests = [
        ("/api/system/health", "trace-a"),
        ("/api/tasks/types", "trace-b"),
    ]

    def get_with_id(item: tuple[str, str]):
        path, request_id = item
        return client.get(path, headers={"X-Request-Id": request_id})

    expected_by_content = {
        f"HTTP GET {path} completed": request_id
        for path, request_id in requests
    }
    with client.websocket_connect("/api/ws") as socket:
        socket.send_json({"type": "subscribe", "data": {"channels": ["log"]}})
        assert socket.receive_json()["type"] == "subscribed"

        with ThreadPoolExecutor(max_workers=2) as executor:
            responses = list(executor.map(get_with_id, requests))

        assert [response.status_code for response in responses] == [200, 200]
        assert [response.headers["x-request-id"] for response in responses] == [
            request_id for _, request_id in requests
        ]
        assert generated.headers["x-request-id"]
        assert generated.headers["x-request-id"] not in {
            request_id for _, request_id in requests
        }
        assert all(
            float(response.headers["x-response-time-ms"]) >= 0
            for response in responses
        )
        assert float(generated.headers["x-response-time-ms"]) >= 0

        events = [socket.receive_json(), socket.receive_json()]
        assert all(event["type"] == "log" for event in events)
        request_events = {
            event["data"]["content"]: event["data"]["request_id"]
            for event in events
        }
        assert request_events == expected_by_content


def test_unhandled_500_keeps_request_trace_in_response_and_error_log(
    tmp_settings, make_client
) -> None:
    """An exception handled outside middleware still retains its request correlation."""
    trace_app = FastAPI()
    register_exception_handlers(trace_app)
    trace_app.middleware("http")(main_module.request_trace_headers)

    @trace_app.get("/kaboom")
    async def kaboom() -> None:
        raise RuntimeError("expected trace test failure")

    hub = LogHub()
    handler = LogHubHandler(hub)
    error_logger = logging.getLogger("maa_api.api.errors")
    error_logger.addHandler(handler)
    try:
        response = make_client(trace_app).get(
            "/kaboom", headers={"X-Request-Id": "error-trace"}
        )
    finally:
        error_logger.removeHandler(handler)

    assert response.status_code == 500
    assert response.headers["x-request-id"] == "error-trace"
    assert float(response.headers["x-response-time-ms"]) >= 0
    records, _ = hub.snapshot_after(0)
    error_record = next(
        record for record in records if record.content.startswith("未捕获异常 trace_id=")
    )
    assert error_record.request_id == "error-trace"


def test_cors_allows_request_id_and_exposes_trace_response_headers(
    tmp_settings, isolated_db, make_client
) -> None:
    """Browser clients may send the correlation id and read both trace response headers."""
    client = make_client(app)
    response = client.options(
        "/api/system/health",
        headers={
            "Origin": "http://localhost:8002",
            "Access-Control-Request-Method": "GET",
            "Access-Control-Request-Headers": "X-Request-Id",
        },
    )

    assert response.status_code == 200, response.text
    assert "x-request-id" in response.headers["access-control-allow-headers"].lower()
    simple = client.get(
        "/api/system/health", headers={"Origin": "http://localhost:8002"}
    )
    exposed = simple.headers["access-control-expose-headers"].lower()
    assert "x-request-id" in exposed
    assert "x-response-time-ms" in exposed


def test_http_request_trace_is_attached_to_its_service_log(
    tmp_settings, isolated_db, make_client
) -> None:
    """The id returned by HTTP is stored on the corresponding live log record."""
    client = make_client(app)
    response = client.get(
        "/api/system/health", headers={"X-Request-Id": "http-ws-trace"}
    )
    records, _ = app.state.log_hub.snapshot_after(0)
    event = next(record for record in reversed(records) if record.content.startswith("HTTP GET"))

    assert response.headers["x-request-id"] == "http-ws-trace"
    assert event.request_id == "http-ws-trace"


# ----------------------------------------------------------------------
# 5. 未知 /api 路径：统一错误体的 404
# ----------------------------------------------------------------------


def test_unknown_api_path_uses_unified_error_body(
    tmp_settings, isolated_db, make_client
) -> None:
    """404 走 M3-04 的统一错误体，不是 FastAPI 默认的 ``{"detail": ...}``。"""
    client = make_client(app)
    response = client.get("/api/nope")

    assert response.status_code == 404
    body = response.json()
    assert "detail" not in body, body
    assert list(body) == ["error"]
    assert body["error"]["code"] == "NOT_FOUND"
    assert body["error"]["message"] == "资源不存在"


# ----------------------------------------------------------------------
# 6. 鉴权：401 / X-Token 200 / health 豁免
# ----------------------------------------------------------------------


@pytest.mark.parametrize("tmp_settings", [TOKEN], indirect=True)
def test_auth_is_enforced_on_business_endpoints(
    tmp_settings, isolated_db, make_client
) -> None:
    """配 token 时业务端点无凭据 401、带 ``X-Token`` 200；health 免鉴权。"""
    client = make_client(app)

    anonymous = client.get("/api/tasks/types")
    assert anonymous.status_code == 401, anonymous.text
    assert anonymous.json()["error"]["code"] == "UNAUTHORIZED"
    assert anonymous.headers["www-authenticate"] == "Bearer"

    authorized = client.get("/api/tasks/types", headers={"X-Token": TOKEN})
    assert authorized.status_code == 200
    assert authorized.json()["total"] == 9

    health = client.get("/api/system/health")
    assert health.status_code == 200
    payload = health.json()
    assert payload["auth_enabled"] is True
    assert payload["started_at"] is not None
    assert datetime.fromisoformat(payload["started_at"]).utcoffset() is not None


def test_health_reports_auth_disabled_without_token(
    tmp_settings, isolated_db, make_client
) -> None:
    """免鉴权模式：health 免鉴权且 ``auth_enabled=false``，业务端点也不需要凭据。"""
    client = make_client(app)

    health = client.get("/api/system/health")
    assert health.status_code == 200
    assert health.json()["auth_enabled"] is False
    assert client.get("/api/tasks/types").status_code == 200


# ----------------------------------------------------------------------
# 7. 免鉴权模式的启动日志
# ----------------------------------------------------------------------


def test_auth_disabled_warning_is_logged(tmp_settings, caplog) -> None:
    """未配置 token 时打出固定文案（docs/05 §5.2，前端与运维按它判断）。"""
    with caplog.at_level(logging.WARNING, logger=main_module.__name__):
        warn_if_auth_disabled()

    assert AUTH_DISABLED_WARNING in caplog.text


@pytest.mark.parametrize("tmp_settings", [TOKEN], indirect=True)
def test_no_auth_disabled_warning_when_token_configured(tmp_settings, caplog) -> None:
    """配了 token 就不该出现那句 warning（否则运维会误判）。"""
    with caplog.at_level(logging.WARNING, logger=main_module.__name__):
        warn_if_auth_disabled()

    assert AUTH_DISABLED_WARNING not in caplog.text


def test_lifespan_logs_the_auth_disabled_warning(
    tmp_settings, isolated_db, make_client, caplog
) -> None:
    """免鉴权模式下，进入 TestClient（跑 lifespan）就打出那句 warning。

    M3-09 实测 caplog 能捕到 TestClient worker 线程里发出的记录；即使将来线程模型
    变化导致捕不到，上面的 :func:`~maa_api.main.warn_if_auth_disabled` 直调用例
    仍然钉住文案本身。
    """
    with caplog.at_level(logging.WARNING, logger=main_module.__name__):
        make_client(app)

    assert AUTH_DISABLED_WARNING in caplog.text


# ----------------------------------------------------------------------
# 8. import 隔离与耗时（verify #1 的回归版）
# ----------------------------------------------------------------------

#: 与卡面 verify #1 同形：import main 不得拉进内核 / 旧栈。
_IMPORT_PROBE = (
    "import sys, time; "
    "t = time.time(); import maa_api.main; dt = time.time() - t; "
    "bad = [m for m in sys.modules if m.startswith(('maa_api.core', 'maa_api.model', "
    "'maa_api.router', 'maa_api.scheduler', 'maa_api.dependence', 'maa_api.service.', "
    "'maa_api.config', 'maa_api.log', 'maa_api.exception'))]; "
    "assert not bad, bad; assert dt < 5, dt; print(dt)"
)


def test_import_maa_api_main_is_isolated_and_fast() -> None:
    """子进程复跑：import 不拉内核/旧栈，且 < 5s（本机实测约 0.3s）。"""
    process = subprocess.run(
        [sys.executable, "-c", _IMPORT_PROBE],
        cwd=REPO_ROOT,
        capture_output=True,
        text=True,
        timeout=60,
        check=False,
    )

    assert process.returncode == 0, process.stderr
    assert float(process.stdout.strip()) < 5.0
