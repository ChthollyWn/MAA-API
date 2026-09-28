"""Web Push 订阅 REST 契约（M14 Task 2）。

覆盖：VAPID 公钥端点、订阅登记的成功码与 Location、同一 endpoint 的幂等更新、
形状校验的 422、列表不泄露密钥材料、删除 204/404。
"""

from __future__ import annotations

import asyncio
import base64
from types import SimpleNamespace
from typing import Any

import pytest
from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric import ec
from fastapi import FastAPI
from sqlalchemy.ext.asyncio import AsyncEngine
from sqlmodel import SQLModel

import maa_api.db.models  # noqa: F401
from maa_api.api.errors import register_exception_handlers
from maa_api.api.routers import notifications as notifications_router
from maa_api.db import session as db_session
from maa_api.services import webpush_vapid as vapid
from maa_api.services.push_subscription_service import PushSubscriptionService

ENDPOINT = "https://fcm.googleapis.com/fcm/send/abc-123"


def _b64(raw: bytes) -> str:
    return base64.urlsafe_b64encode(raw).rstrip(b"=").decode()


def _keys() -> dict[str, str]:
    private_key = ec.generate_private_key(ec.SECP256R1())
    return {
        "p256dh": _b64(
            private_key.public_key().public_bytes(
                serialization.Encoding.X962,
                serialization.PublicFormat.UncompressedPoint,
            )
        ),
        "auth": _b64(b"\x03" * 16),
    }


@pytest.fixture
def vapid_public() -> str:
    return vapid.generate_vapid_key_pair()[1]


def _client(
    isolated_db: AsyncEngine,
    make_client,
    monkeypatch: pytest.MonkeyPatch,
    *,
    private_pem: str | None = None,
):
    async def create_schema():
        async with isolated_db.begin() as connection:
            await connection.run_sync(SQLModel.metadata.create_all)

    asyncio.run(create_schema())
    isolated_db.sync_engine.dispose()

    if private_pem is None:
        private_pem, _ = vapid.generate_vapid_key_pair()
    settings = SimpleNamespace(
        # 空串＝免鉴权（docs/05 §5.2）：本文件只挂 notifications 路由，不测鉴权本身。
        access_token="",
        notify=SimpleNamespace(
            vapid_private_key=private_pem, vapid_subject="mailto:t@example.com"
        ),
    )
    # 鉴权依赖在 import 期就把 ``get_settings`` 绑进了 ``maa_api.api.deps``，只打
    # ``maa_api.settings`` 那个入口没用：本机 ``config.yaml`` 一旦配了 access_token，
    # 这些用例会以 401 全红（与代码无关的环境性失败，M17 复现并修复）。
    monkeypatch.setattr("maa_api.settings.get_settings", lambda: settings)
    monkeypatch.setattr("maa_api.api.deps.get_settings", lambda: settings)

    app = FastAPI()
    register_exception_handlers(app)
    app.include_router(notifications_router.router)
    app.state.push_subscription_service = PushSubscriptionService(
        db_session.session_factory
    )
    return make_client(app)


def test_vapid_public_key_endpoint_returns_derived_key(
    isolated_db, make_client, monkeypatch, vapid_public
) -> None:
    private_pem, expected = vapid.generate_vapid_key_pair()
    client = _client(isolated_db, make_client, monkeypatch, private_pem=private_pem)

    response = client.get("/api/notifications/vapid-public-key")
    assert response.status_code == 200, response.text
    body = response.json()
    assert body["public_key"] == expected
    assert body["subject"] == "mailto:t@example.com"


def test_vapid_public_key_without_config_returns_503(
    isolated_db, make_client, monkeypatch
) -> None:
    client = _client(isolated_db, make_client, monkeypatch, private_pem="")

    response = client.get("/api/notifications/vapid-public-key")
    assert response.status_code == 503, response.text
    assert response.json()["error"]["code"] == "PUSH_NOT_CONFIGURED"


def test_subscribe_creates_and_repeat_updates(
    isolated_db, make_client, monkeypatch
) -> None:
    client = _client(isolated_db, make_client, monkeypatch)
    keys = _keys()

    created = client.post(
        "/api/notifications/push-subscriptions",
        json={"endpoint": ENDPOINT, "keys": keys},
        headers={"User-Agent": "Mozilla/5.0 (test)"},
    )
    assert created.status_code == 201, created.text
    body = created.json()
    assert body["endpoint_host"] == "fcm.googleapis.com"
    assert body["disabled"] is False
    assert created.headers["Location"] == (
        f"/api/notifications/push-subscriptions/{body['id']}"
    )

    repeated = client.post(
        "/api/notifications/push-subscriptions",
        json={"endpoint": ENDPOINT, "keys": keys},
        headers={"User-Agent": "Mozilla/5.0 (test 2)"},
    )
    assert repeated.status_code == 200, repeated.text
    assert repeated.json()["id"] == body["id"]
    assert repeated.json()["user_agent"] == "Mozilla/5.0 (test 2)"

    listing = client.get("/api/notifications/push-subscriptions")
    assert listing.status_code == 200
    assert listing.json()["total"] == 1


def test_listing_does_not_leak_credentials(
    isolated_db, make_client, monkeypatch
) -> None:
    client = _client(isolated_db, make_client, monkeypatch)
    keys = _keys()
    client.post(
        "/api/notifications/push-subscriptions",
        json={"endpoint": ENDPOINT, "keys": keys},
    )

    payload = client.get("/api/notifications/push-subscriptions").text
    assert ENDPOINT not in payload
    assert keys["p256dh"] not in payload
    assert keys["auth"] not in payload
    item = client.get("/api/notifications/push-subscriptions").json()["items"][0]
    assert set(item) == {
        "id",
        "endpoint_host",
        "user_agent",
        "created_at",
        "last_success_at",
        "disabled",
        "disabled_at",
    }


@pytest.mark.parametrize(
    "payload",
    [
        {"endpoint": "http://fcm.googleapis.com/x", "keys": _keys()},
        {"endpoint": ENDPOINT, "keys": {"p256dh": "bad", "auth": _b64(b"\x03" * 16)}},
        {"endpoint": ENDPOINT, "keys": {"p256dh": _keys()["p256dh"], "auth": "short"}},
        {"endpoint": ENDPOINT, "keys": {}},
    ],
)
def test_invalid_subscription_returns_422(
    isolated_db, make_client, monkeypatch, payload: dict[str, Any]
) -> None:
    client = _client(isolated_db, make_client, monkeypatch)

    response = client.post("/api/notifications/push-subscriptions", json=payload)
    assert response.status_code == 422, response.text
    assert response.json()["error"]["code"] == "PUSH_SUBSCRIPTION_INVALID"


def test_disabled_subscriptions_are_hidden_by_default(
    isolated_db, make_client, monkeypatch
) -> None:
    client = _client(isolated_db, make_client, monkeypatch)
    keys = _keys()
    created = client.post(
        "/api/notifications/push-subscriptions",
        json={"endpoint": ENDPOINT, "keys": keys},
    ).json()

    service = PushSubscriptionService(db_session.session_factory)
    asyncio.run(service.mark_disabled(created["id"]))

    assert client.get("/api/notifications/push-subscriptions").json()["total"] == 0
    included = client.get(
        "/api/notifications/push-subscriptions", params={"include_disabled": True}
    ).json()
    assert included["total"] == 1
    assert included["items"][0]["disabled"] is True


def test_delete_subscription_and_missing_404(
    isolated_db, make_client, monkeypatch
) -> None:
    client = _client(isolated_db, make_client, monkeypatch)
    created = client.post(
        "/api/notifications/push-subscriptions",
        json={"endpoint": ENDPOINT, "keys": _keys()},
    ).json()

    assert client.delete(
        f"/api/notifications/push-subscriptions/{created['id']}"
    ).status_code == 204
    assert client.get("/api/notifications/push-subscriptions").json()["total"] == 0

    missing = client.delete("/api/notifications/push-subscriptions/nope")
    assert missing.status_code == 404
    assert missing.json()["error"]["code"] == "PUSH_SUBSCRIPTION_NOT_FOUND"
