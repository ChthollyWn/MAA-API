"""M42 端到端集成：**真实** `SklandClient` + 真实服务 + 真实 SQLite，只把 socket 换成 mock。

与另外两个 skland 测试文件的分工：

- `tests/services/test_skland_client.py` 用 `MockTransport` 单独钉住客户端的签名与解析；
- `tests/api/test_skland_router.py` 用假客户端钉住六个端点的契约与错误码；
- **本文件把两者接起来**：路由 → 服务 → 真客户端（签名 + 解析）→ 归一 → 写库 → 识别记录，
  只有出站 socket 是假的。这样 W0 尚未取到的真实响应形状一旦被客户端误解析，这里会先红，
  而不是等真机上用户扫码才发现。
"""

from __future__ import annotations

import asyncio
import json
from dataclasses import dataclass, field
from datetime import datetime
from typing import Any
from urllib.parse import parse_qs, urlparse

import httpx
import pytest
from cryptography.fernet import Fernet
from fastapi import FastAPI
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncEngine
from sqlmodel import SQLModel

import maa_api.db.models  # noqa: F401  # 注册全部表
from maa_api.api.errors import register_exception_handlers
from maa_api.api.routers import skland as skland_router
from maa_api.db import session as db_session
from maa_api.db.models import AccountOperator, AccountRecognition, SklandRole
from maa_api.services.account.box import OperatorBoxService
from maa_api.services.skland.client import SklandClient
from maa_api.services.skland.login import SklandLoginService
from maa_api.services.skland.store import SklandStore
from maa_api.services.skland.sync import SklandSyncService
from maa_api.settings import SKLAND_MIN_CHARS, get_settings

KEY = Fernet.generate_key().decode("ascii")
NOW = 1700000000.0  # 固定时钟：签名里的 timestamp 才可复算
SCAN_ID = "c" * 32


@dataclass
class FakeUpstream:
    """一个内存里的森空岛：按路径回罐装响应，并记录收到的请求。"""

    char_count: int = SKLAND_MIN_CHARS
    scan_status_calls: int = 0
    cred_rejected: bool = False
    requests: list[httpx.Request] = field(default_factory=list)

    # -- 造数据 -----------------------------------------------------------
    def chars(self) -> list[dict[str, Any]]:
        rows = []
        for index in range(self.char_count):
            rows.append(
                {
                    "charId": f"char_{index:04d}",
                    "level": 90,
                    "evolvePhase": 2,
                    "potentialRank": 5,
                    "mainSkillLvl": 7,
                    "favorPercent": 200,
                    "skills": [
                        {"id": "sk1", "specializeLevel": 3},
                        {"id": "sk2", "specializeLevel": 1},
                        {"id": "sk3", "specializeLevel": 0},
                    ],
                    "equip": [{"id": "uniequip_002_x", "level": 2, "locked": False}],
                }
            )
        return rows

    # -- mock 处理 --------------------------------------------------------
    def handler(self, request: httpx.Request) -> httpx.Response:
        self.requests.append(request)
        path = urlparse(str(request.url)).path
        if path == "/general/v1/gen_scan/login":
            return _json(
                {
                    "data": {
                        "scanId": SCAN_ID,
                        "scanUrl": f"hypergryph://scan_login?scanId={SCAN_ID}",
                    },
                    "msg": "OK",
                    "status": 0,
                    "type": "A",
                }
            )
        if path == "/general/v1/scan_status":
            scan_id = parse_qs(urlparse(str(request.url)).query).get("scanId", [""])[0]
            assert scan_id == SCAN_ID
            self.scan_status_calls += 1
            if self.scan_status_calls == 1:
                return _json({"msg": "未扫码", "status": 100, "type": "A"})
            if self.scan_status_calls == 2:
                # 真机实测的中间态：用户扫了码、还没在 App 里确认。
                return _json({"msg": "已扫码待确认", "status": 101, "type": "A"})
            return _json(
                {"data": {"scanCode": "scan-code-1"}, "msg": "OK", "status": 0, "type": "A"}
            )
        if path == "/user/auth/v1/token_by_scan_code":
            return _json({"data": {"token": "hypergryph-token"}, "msg": "OK", "status": 0})
        if path == "/user/oauth2/v2/grant":
            return _json({"data": {"code": "grant-code"}, "msg": "OK", "status": 0})
        if path == "/api/v1/user/auth/generate_cred_by_code":
            return _json({"code": 0, "data": {"cred": "cred-value", "token": "sign-key-1"}})
        if path == "/api/v1/auth/refresh":
            return _json({"code": 0, "data": {"token": "sign-key-2"}})
        if path == "/api/v1/game/player/binding":
            if "cred" not in request.headers:
                return httpx.Response(401, json={"code": 10002, "message": "用户未登录"})
            if self.cred_rejected:
                return httpx.Response(401, json={"code": 10002, "message": "用户未登录"})
            # 真机形状：data.list[] 按 appCode 分组，组内 bindingList[]，昵称键 nickName，
            # 等级在 roles[].level。
            return _json(
                {
                    "code": 0,
                    "data": {
                        "list": [
                            {
                                "appCode": "endfield",
                                "bindingList": [{"uid": "9999", "nickName": "终末地角色"}],
                            },
                            {
                                "appCode": "arknights",
                                "defaultUid": "1234567890",
                                "bindingList": [
                                    {
                                        "uid": "1234567890",
                                        "channelMasterId": "1",
                                        "nickName": "测试博士",
                                        "isDefault": True,
                                        "roles": [{"serverId": "1", "level": 120}],
                                    }
                                ],
                            },
                        ]
                    },
                }
            )
        if path == "/api/v1/game/player/info":
            return _json(
                {
                    "code": 0,
                    "data": {
                        "status": {"name": "测试博士", "level": 120},
                        "chars": self.chars(),
                        "charInfoMap": {"char_0000": {"name": "阿米娅", "rarity": 5}},
                        "equipmentInfoMap": {"uniequip_002_x": {"type": "MOD_X"}},
                    },
                }
            )
        raise AssertionError(f"未预期的上游请求：{path}")

    @property
    def transport(self) -> httpx.MockTransport:
        return httpx.MockTransport(self.handler)

    def signed_paths(self) -> list[str]:
        return [
            urlparse(str(request.url)).path
            for request in self.requests
            if "sign" in request.headers
        ]


def _json(payload: dict[str, Any]) -> httpx.Response:
    return httpx.Response(200, content=json.dumps(payload).encode(), headers={"content-type": "application/json"})


async def _create_schema(engine: AsyncEngine) -> None:
    async with engine.begin() as connection:
        await connection.run_sync(SQLModel.metadata.create_all)


def _build_app(engine: AsyncEngine, upstream: FakeUpstream) -> FastAPI:
    asyncio.run(_create_schema(engine))
    settings = get_settings()
    settings.skland.credential_key = KEY
    app = FastAPI()
    register_exception_handlers(app)
    app.include_router(skland_router.router)

    def client_factory(**kwargs: Any) -> SklandClient:
        kwargs.setdefault("timeout", 15.0)
        return SklandClient(transport=upstream.transport, now=lambda: NOW, **kwargs)

    store = SklandStore(db_session.session_factory, settings_provider=lambda: settings)
    box = OperatorBoxService(db_session.session_factory, settings_provider=lambda: settings)
    app.state.skland_sync_service = SklandSyncService(
        store=store,
        box=box,
        client_factory=client_factory,
        catalog_loader=lambda: {},
        settings_provider=lambda: settings,
        now=lambda: datetime(2026, 10, 12, 6, 0, 0),
    )
    app.state.skland_login_service = SklandLoginService(
        store=store,
        client_factory=client_factory,
        settings_provider=lambda: settings,
        now=lambda: datetime(2026, 10, 12, 6, 0, 0),
    )
    return app


@pytest.fixture(autouse=True)
def _isolated_settings(tmp_settings):
    """临时 config.yaml（默认无 access_token ⇒ 端点免鉴权），并清掉环境变量漂移。"""
    return tmp_settings


def test_full_flow_binds_scans_syncs_and_persists(isolated_db, make_client):
    upstream = FakeUpstream()
    app = _build_app(isolated_db, upstream)
    client = make_client(app)

    # 1) 生成二维码：真客户端调 gen_scan，真编码器出 SVG。
    started = client.post("/api/skland/login/qrcode")
    assert started.status_code == 200
    start_body = started.json()
    assert start_body["qr_svg"].startswith("<svg")
    assert SCAN_ID in start_body["payload"]

    # 2) 第一次轮询：上游说「未扫码」。
    session_id = start_body["session_id"]
    assert client.get(f"/api/skland/login/qrcode/{session_id}").json()["state"] == "waiting"

    # 3) 第二次轮询：用户扫了码但还没确认（101）——仍是 waiting，会话不被消费。
    scanned = client.get(f"/api/skland/login/qrcode/{session_id}").json()
    assert scanned["state"] == "waiting"
    assert scanned["message"] == "已扫码待确认"

    # 4) 第三次轮询：确认 → 走完后半段（token → grant → cred → binding）并落库。
    done = client.get(f"/api/skland/login/qrcode/{session_id}")
    assert done.status_code == 200
    body = done.json()
    assert body["state"] == "succeeded"
    assert body["account"]["uid"] == "1234567890"
    assert [role["uid"] for role in body["roles"]] == ["1234567890"]  # endfield 那条被过滤

    # 5) 同步：真客户端带签名取数，归一后写库。
    synced = client.post("/api/skland/sync")
    assert synced.status_code == 200, synced.text
    summary = synced.json()
    assert summary["status"] == "succeeded"
    assert summary["total"] == SKLAND_MIN_CHARS
    assert summary["created"] == SKLAND_MIN_CHARS

    async def _check_db() -> None:
        async with db_session.session_factory() as session:
            row = await session.get(AccountOperator, "char_0000")
            assert row is not None
            assert (row.own, row.elite, row.level, row.potential) == (True, 2, 90, 6)
            assert row.skill_level == 7
            # 专精槽位取响应里 skills 的个数（3 个），不依赖稀有度是否读得出来。
            assert row.masteries == {"1": 3, "2": 1, "3": 0}
            assert row.modules == {"X": 2}
            assert row.favor_percent == 200
            assert row.field_sources["level"] == "skland"
            kinds = (await session.execute(select(AccountRecognition.kind))).scalars().all()
            assert "skland" in kinds
            role = await session.get(SklandRole, "1234567890")
            assert role is not None and role.nickname == "测试博士" and role.synced_at is not None

    asyncio.run(_check_db())

    # 5) 签名与凭据确实进了请求头（真客户端在跑，不是假件）。
    signed = upstream.signed_paths()
    assert "/api/v1/auth/refresh" in signed
    assert "/api/v1/game/player/binding" in signed
    assert "/api/v1/game/player/info" in signed
    for request in upstream.requests:
        if "sign" in request.headers:
            assert request.headers["cred"] == "cred-value"
            assert request.headers["sign"]
            assert request.headers["timestamp"]

    # 6) 状态端点与解绑。
    status = client.get("/api/skland/status").json()
    assert status["bound"] is True and status["last_sync_at"] is not None
    assert status["account"]["nickname"] == "测试博士"

    assert client.delete("/api/skland/binding").json() == {"bound": False}
    assert client.get("/api/skland/status").json()["bound"] is False

    # 解绑不动练度。
    async def _check_rows_survive() -> None:
        async with db_session.session_factory() as session:
            row = await session.get(AccountOperator, "char_0000")
            assert row is not None and row.level == 90

    asyncio.run(_check_rows_survive())


def test_guard_blocks_a_short_round_through_the_real_client(isolated_db, make_client):
    """`chars` 数低于护栏：整轮不写库（这里走真客户端的解析路径）。"""
    upstream = FakeUpstream(char_count=SKLAND_MIN_CHARS - 1)
    app = _build_app(isolated_db, upstream)
    client = make_client(app)

    session_id = client.post("/api/skland/login/qrcode").json()["session_id"]
    client.get(f"/api/skland/login/qrcode/{session_id}")  # 100 未扫码
    client.get(f"/api/skland/login/qrcode/{session_id}")  # 101 已扫码待确认
    client.get(f"/api/skland/login/qrcode/{session_id}")  # succeeded → 绑定

    response = client.post("/api/skland/sync")
    assert response.status_code == 502
    assert response.json()["error"]["code"] == "SKLAND_UPSTREAM_FAILED"

    async def _check_empty() -> None:
        async with db_session.session_factory() as session:
            rows = (await session.execute(select(AccountOperator))).scalars().all()
            assert rows == []

    asyncio.run(_check_empty())


def test_manual_bind_uses_the_real_client_and_rejects_a_dead_cred(isolated_db, make_client):
    upstream = FakeUpstream(cred_rejected=True)
    app = _build_app(isolated_db, upstream)
    client = make_client(app)

    response = client.post(
        "/api/skland/login/manual", json={"cred": "dead-cred", "token": "dead-key"}
    )
    assert response.status_code == 401
    assert response.json()["error"]["code"] == "SKLAND_LOGIN_FAILED"


def test_manual_bind_then_sync_works_without_the_qr_flow(isolated_db, make_client):
    """手动兜底那条路也要能一路走到写库（扫码被风控或网络卡住时的备选入口）。"""
    upstream = FakeUpstream()
    app = _build_app(isolated_db, upstream)
    client = make_client(app)

    bound = client.post(
        "/api/skland/login/manual", json={"cred": "cred-value", "token": "sign-key-1"}
    )
    assert bound.status_code == 200
    assert bound.json()["bound"] is True

    synced = client.post("/api/skland/sync")
    assert synced.status_code == 200
    assert synced.json()["total"] == SKLAND_MIN_CHARS
