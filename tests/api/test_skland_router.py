"""M42 §4.6：``/api/skland`` 六个端点的 REST 契约（临时库 + 假上游）。

纪律与 ``tests/api/test_account_router.py`` 一致：不 import ``maa_api.main``，现搭
``FastAPI`` 并显式 ``include_router``，服务自己拿 ``db_session.session_factory``
（已被 ``isolated_db`` 换成临时库）。上游用假客户端，**不发真实请求**。
"""

from __future__ import annotations

import asyncio
from dataclasses import dataclass, field
from datetime import datetime
from typing import Any

import pytest
from cryptography.fernet import Fernet
from fastapi import FastAPI
from sqlalchemy.ext.asyncio import AsyncEngine
from sqlmodel import SQLModel

import maa_api.db.models  # noqa: F401  # 注册全部表
from maa_api.api.errors import register_exception_handlers
from maa_api.api.routers import skland as skland_router
from maa_api.db import session as db_session
from maa_api.domain.errors import AppError, ErrorCode
from maa_api.services.account.box import OperatorBoxService
from maa_api.services.skland.client import BindingRole, SklandCredential
from maa_api.services.skland.login import SklandLoginService
from maa_api.services.skland.store import SklandStore
from maa_api.services.skland.sync import SklandSyncService
from maa_api.settings import SKLAND_MIN_CHARS, Settings, get_settings

KEY = Fernet.generate_key().decode("ascii")
ENDPOINTS = (
    ("POST", "/api/skland/login/qrcode"),
    ("GET", "/api/skland/login/qrcode/" + "a" * 32),
    ("POST", "/api/skland/login/manual"),
    ("GET", "/api/skland/status"),
    ("POST", "/api/skland/sync"),
    ("DELETE", "/api/skland/binding"),
)


@dataclass
class FakeClient:
    """假上游：整条扫码 + 同步链路的九个方法。"""

    scan_id: str = "b" * 32
    scan_url: str = "hypergryph://scan_login?scanId=" + "b" * 32
    scan_states: list[tuple[int, str | None, str]] = field(
        default_factory=lambda: [(100, None, "未扫码"), (0, "scan-code-1", "OK")]
    )
    roles: list[BindingRole] = field(default_factory=list)
    chars: list[dict[str, Any]] = field(default_factory=list)
    binding_error: AppError | None = None
    closed: bool = False

    async def generate_scan(self):
        from maa_api.services.skland.client import ScanLogin

        return ScanLogin(scan_id=self.scan_id, scan_url=self.scan_url)

    async def scan_status(self, scan_id: str) -> tuple[int, str | None, str]:
        if len(self.scan_states) > 1:
            return self.scan_states.pop(0)
        return self.scan_states[0]

    async def token_by_scan_code(self, scan_code: str) -> str:
        return "hypergryph-token"

    async def grant(self, hypergryph_token: str) -> str:
        return "grant-code"

    async def generate_cred(self, code: str) -> SklandCredential:
        return SklandCredential(cred="cred-value", token="token-value")

    async def refresh(self, credential: SklandCredential) -> SklandCredential:
        return SklandCredential(cred=credential.cred, token=credential.token + "-new")

    async def binding(self, credential: SklandCredential) -> list[BindingRole]:
        if self.binding_error is not None:
            raise self.binding_error
        return list(self.roles)

    async def player_info(self, credential: SklandCredential, uid: str) -> dict[str, Any]:
        return {"chars": self.chars}

    async def aclose(self) -> None:
        self.closed = True


def _role(uid: str = "1234567890") -> BindingRole:
    return BindingRole(
        uid=uid,
        app_code="arknights",
        channel_master_id="1",
        nickname="博士",
        level=120,
        is_default=True,
    )


def _chars(count: int) -> list[dict[str, Any]]:
    return [
        {
            "charId": f"char_{index:04d}",
            "level": 90,
            "evolvePhase": 2,
            "potentialRank": 5,
            "mainSkillLvl": 7,
            "favorPercent": 100,
            "skills": [{"id": "s1", "specializeLevel": 3}],
            "equip": [],
        }
        for index in range(count)
    ]


async def _create_schema(engine: AsyncEngine) -> None:
    async with engine.begin() as connection:
        await connection.run_sync(SQLModel.metadata.create_all)


def _build_app(engine: AsyncEngine, settings: Settings, client: FakeClient) -> FastAPI:
    asyncio.run(_create_schema(engine))
    app = FastAPI()
    register_exception_handlers(app)
    app.include_router(skland_router.router)
    store = SklandStore(db_session.session_factory, settings_provider=lambda: settings)
    box = OperatorBoxService(db_session.session_factory, settings_provider=lambda: settings)
    app.state.skland_sync_service = SklandSyncService(
        store=store,
        box=box,
        client_factory=lambda **_: client,
        catalog_loader=lambda: {},
        settings_provider=lambda: settings,
        now=lambda: datetime(2026, 10, 12, 5, 0, 0),
    )
    app.state.skland_login_service = SklandLoginService(
        store=store,
        client_factory=lambda **_: client,
        settings_provider=lambda: settings,
        now=lambda: datetime(2026, 10, 12, 5, 0, 0),
    )
    return app


def _client(engine: AsyncEngine, make_client, *, key: str = KEY, client: FakeClient | None = None):
    settings = get_settings()
    settings.skland.credential_key = key
    upstream = client or FakeClient()
    app = _build_app(engine, settings, upstream)
    return app, upstream, make_client(app)


def _error(response):
    return response.json()["error"]


@pytest.fixture(autouse=True)
def _isolated_settings(tmp_settings):
    """本文件所有用例都用临时 ``config.yaml``（否则会读到开发机真实的 config.yaml）。

    默认 access_token 为空 ⇒ 端点免鉴权，只有鉴权用例显式把 token 打开。
    """
    return tmp_settings


# --------------------------------------------------------------------------- #
# 鉴权与服务未装配
# --------------------------------------------------------------------------- #
@pytest.mark.parametrize("tmp_settings", ["s3cret"], indirect=True)
@pytest.mark.parametrize(("method", "path"), ENDPOINTS)
def test_skland_endpoints_require_auth(isolated_db, make_client, tmp_settings, method, path):
    settings = tmp_settings
    settings.skland.credential_key = KEY  # 免鉴权时也要求服务装配好
    app = _build_app(isolated_db, settings, FakeClient())
    client = make_client(app)
    response = client.request(method, path, json={})
    assert response.status_code == 401
    assert _error(response)["code"] == "UNAUTHORIZED"


def test_unnassembled_service_returns_503(isolated_db, make_client):
    app = FastAPI()
    register_exception_handlers(app)
    app.include_router(skland_router.router)
    client = make_client(app)
    response = client.get("/api/skland/status")
    assert response.status_code == 503
    assert _error(response)["code"] == "SERVICE_UNAVAILABLE"


# --------------------------------------------------------------------------- #
# 状态
# --------------------------------------------------------------------------- #
def test_status_reports_an_unbound_account(isolated_db, make_client):
    app, _upstream, client = _client(isolated_db, make_client, key="")
    response = client.get("/api/skland/status")
    assert response.status_code == 200
    body = response.json()
    assert body["bound"] is False
    assert body["key_configured"] is False
    assert body["account"] is None
    assert body["roles"] == []
    assert body["min_chars"] == SKLAND_MIN_CHARS


def test_sync_without_a_binding_returns_409(isolated_db, make_client):
    _app, _upstream, client = _client(isolated_db, make_client)
    response = client.post("/api/skland/sync")
    assert response.status_code == 409
    assert _error(response)["code"] == "SKLAND_NOT_BOUND"


# --------------------------------------------------------------------------- #
# 扫码链路
# --------------------------------------------------------------------------- #
def test_qr_login_happy_path_binds_and_then_syncs(isolated_db, make_client):
    upstream = FakeClient(roles=[_role()], chars=_chars(SKLAND_MIN_CHARS))
    _app, _upstream, client = _client(isolated_db, make_client, client=upstream)

    started = client.post("/api/skland/login/qrcode")
    assert started.status_code == 200
    start_body = started.json()
    assert start_body["qr_svg"].startswith("<svg")
    assert start_body["payload"].startswith("hypergryph://scan_login")
    assert start_body["poll_interval_ms"] == 2000
    session_id = start_body["session_id"]

    waiting = client.get(f"/api/skland/login/qrcode/{session_id}")
    assert waiting.status_code == 200
    assert waiting.json()["state"] == "waiting"

    # 第二次轮询返回确认（假上游的 scan_states 第二个元素）。
    upstream.scan_states = [(0, "scan-code-1", "OK")]
    done = client.get(f"/api/skland/login/qrcode/{session_id}")
    assert done.status_code == 200
    body = done.json()
    assert body["state"] == "succeeded"
    assert body["account"]["uid"] == "1234567890"
    assert body["roles"][0]["nickname"] == "博士"

    status = client.get("/api/skland/status").json()
    assert status["bound"] is True
    assert status["key_configured"] is True

    # 会话是一次性的：再轮询就是 404。
    again = client.get(f"/api/skland/login/qrcode/{session_id}")
    assert again.status_code == 404
    assert _error(again)["code"] == "SKLAND_QR_SESSION_NOT_FOUND"

    synced = client.post("/api/skland/sync")
    assert synced.status_code == 200
    summary = synced.json()
    assert summary["status"] == "succeeded"
    assert summary["total"] == SKLAND_MIN_CHARS
    assert summary["duration_ms"] >= 0

    # 冷却：紧接着再同步是 429，且带 Retry-After。
    cooldown = client.post("/api/skland/sync")
    assert cooldown.status_code == 429
    assert _error(cooldown)["code"] == "RATE_LIMITED"
    assert "Retry-After" in cooldown.headers

    unbound = client.delete("/api/skland/binding")
    assert unbound.status_code == 200
    assert unbound.json() == {"bound": False}
    assert client.get("/api/skland/status").json()["bound"] is False


def test_scanned_but_unconfirmed_keeps_waiting_with_the_upstream_message(isolated_db, make_client):
    """真机实测的 101「已扫码待确认」：既不是成功也不是失败，会话要留着继续轮询。"""
    upstream = FakeClient(
        roles=[_role()], scan_states=[(101, None, "已扫码待确认"), (0, "scan-code-1", "OK")]
    )
    _app, _upstream, client = _client(isolated_db, make_client, client=upstream)

    session_id = client.post("/api/skland/login/qrcode").json()["session_id"]
    body = client.get(f"/api/skland/login/qrcode/{session_id}").json()
    assert body["state"] == "waiting"
    assert body["message"] == "已扫码待确认"

    # 同一张码继续轮询：用户确认后就成功（会话没被 101 消费掉）。
    done = client.get(f"/api/skland/login/qrcode/{session_id}").json()
    assert done["state"] == "succeeded"


def test_expired_qr_reports_expired_state(isolated_db, make_client):
    upstream = FakeClient(scan_states=[(102, None, "已失效")])
    _app, _upstream, client = _client(isolated_db, make_client, client=upstream)

    session_id = client.post("/api/skland/login/qrcode").json()["session_id"]
    body = client.get(f"/api/skland/login/qrcode/{session_id}").json()
    assert body["state"] == "expired"
    assert "失效" in body["message"]


def test_unexpected_scan_status_reports_failure(isolated_db, make_client):
    upstream = FakeClient(scan_states=[(106, None, "怪状态")])
    _app, _upstream, client = _client(isolated_db, make_client, client=upstream)
    session_id = client.post("/api/skland/login/qrcode").json()["session_id"]
    body = client.get(f"/api/skland/login/qrcode/{session_id}").json()
    assert body["state"] == "failed"
    assert "106" in body["message"]


def test_unknown_session_shape_is_rejected(isolated_db, make_client):
    _app, _upstream, client = _client(isolated_db, make_client)
    response = client.get("/api/skland/login/qrcode/not-a-session")
    assert response.status_code == 422


# --------------------------------------------------------------------------- #
# 手动兜底
# --------------------------------------------------------------------------- #
def test_manual_bind_accepts_valid_credentials(isolated_db, make_client):
    upstream = FakeClient(roles=[_role()], chars=_chars(SKLAND_MIN_CHARS))
    _app, _upstream, client = _client(isolated_db, make_client, client=upstream)

    response = client.post(
        "/api/skland/login/manual", json={"cred": "cred-x", "token": "token-x"}
    )
    assert response.status_code == 200
    assert response.json()["bound"] is True


def test_manual_bind_rejects_credentials_the_upstream_refuses(isolated_db, make_client):
    upstream = FakeClient(
        binding_error=AppError(ErrorCode.SKLAND_LOGIN_FAILED, "用户未登录")
    )
    _app, _upstream, client = _client(isolated_db, make_client, client=upstream)

    response = client.post(
        "/api/skland/login/manual", json={"cred": "bad", "token": "bad"}
    )
    assert response.status_code == 401
    assert _error(response)["code"] == "SKLAND_LOGIN_FAILED"


def test_manual_bind_rejects_an_empty_body(isolated_db, make_client):
    _app, _upstream, client = _client(isolated_db, make_client)
    response = client.post("/api/skland/login/manual", json={"cred": "", "token": ""})
    assert response.status_code == 422
    assert _error(response)["code"] == "VALIDATION_ERROR"
