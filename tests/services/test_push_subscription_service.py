"""Web Push 订阅服务的形状校验、幂等登记与失效标记（M14 Task 2）。"""

from __future__ import annotations

import asyncio
import base64
from types import SimpleNamespace

import pytest
from cryptography.hazmat.primitives.asymmetric import ec
from cryptography.hazmat.primitives import serialization
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker
from sqlalchemy.pool import NullPool
from sqlmodel import SQLModel

import maa_api.db.models  # noqa: F401
from maa_api.db.session import make_engine
from maa_api.domain.errors import AppError, ErrorCode
from maa_api.services import webpush_crypto as crypto
from maa_api.services import webpush_vapid as vapid
from maa_api.services.push_subscription_service import (
    PushSubscriptionService,
    subscription_wire,
)


def _b64(raw: bytes) -> str:
    return base64.urlsafe_b64encode(raw).rstrip(b"=").decode()


def _valid_keys() -> tuple[str, str]:
    """一对合法的 p256dh / auth（模拟浏览器生成的订阅密钥）。"""
    private_key = ec.generate_private_key(ec.SECP256R1())
    p256dh = _b64(
        private_key.public_key().public_bytes(
            serialization.Encoding.X962, serialization.PublicFormat.UncompressedPoint
        )
    )
    auth = _b64(b"\x01" * 16)
    return p256dh, auth


def _configured(monkeypatch, *, private_pem: str | None = None, subject: str = "mailto:t@example.com"):
    """把 VAPID 配置注入 ``get_settings()``（服务每次调用时解析）。"""
    if private_pem is None:
        private_pem, _ = vapid.generate_vapid_key_pair()
    settings = SimpleNamespace(
        notify=SimpleNamespace(vapid_private_key=private_pem, vapid_subject=subject)
    )
    monkeypatch.setattr("maa_api.settings.get_settings", lambda: settings)
    return settings


@pytest.fixture
def factory(tmp_path):
    """隔离到 tmp_path 的会话工厂（服务自行开事务，因此用文件库而非共享内存库）。"""
    engine = make_engine(
        f"sqlite+aiosqlite:///{tmp_path / 'push.db'}", poolclass=NullPool
    )

    async def create_schema():
        async with engine.begin() as connection:
            await connection.run_sync(SQLModel.metadata.create_all)

    asyncio.run(create_schema())
    yield async_sessionmaker(engine, expire_on_commit=False, class_=AsyncSession)
    engine.sync_engine.dispose()


def _service(factory) -> PushSubscriptionService:
    return PushSubscriptionService(factory)


ENDPOINT = "https://fcm.googleapis.com/fcm/send/abc-123"


def test_public_key_is_derived_from_the_private_key(factory, monkeypatch) -> None:
    private_pem, expected_public = vapid.generate_vapid_key_pair()
    _configured(monkeypatch, private_pem=private_pem)
    service = _service(factory)

    payload = asyncio.run(service.public_key())
    assert payload["public_key"] == expected_public
    assert payload["subject"] == "mailto:t@example.com"


def test_missing_vapid_key_reports_not_configured(factory, monkeypatch) -> None:
    _configured(monkeypatch, private_pem="")
    service = _service(factory)

    with pytest.raises(AppError) as excinfo:
        asyncio.run(service.public_key())
    assert excinfo.value.code is ErrorCode.PUSH_NOT_CONFIGURED
    assert excinfo.value.http_status == 503


def test_malformed_vapid_key_reports_not_configured(factory, monkeypatch) -> None:
    """配了一个坏私钥同样是「没配好」，不能变成 500。"""
    _configured(monkeypatch, private_pem="-----BEGIN PRIVATE KEY-----\nbroken")
    service = _service(factory)

    with pytest.raises(AppError) as excinfo:
        asyncio.run(service.public_key())
    assert excinfo.value.code is ErrorCode.PUSH_NOT_CONFIGURED


def test_subscribe_then_repeat_is_idempotent(factory, monkeypatch) -> None:
    _configured(monkeypatch)
    service = _service(factory)
    p256dh, auth = _valid_keys()

    first, created = asyncio.run(
        service.subscribe(endpoint=ENDPOINT, p256dh=p256dh, auth=auth, user_agent="UA/1")
    )
    assert created is True

    second, created_again = asyncio.run(
        service.subscribe(endpoint=ENDPOINT, p256dh=p256dh, auth=auth, user_agent="UA/2")
    )
    assert created_again is False
    assert second.id == first.id
    assert second.user_agent == "UA/2"

    rows = asyncio.run(service.list_subscriptions())
    assert len(rows) == 1
    assert rows[0].endpoint_host == "fcm.googleapis.com"


def test_resubscribing_clears_disabled_flag(factory, monkeypatch) -> None:
    """浏览器重新订阅是用户显式恢复推送；继续标记失效会让推送静默不达。"""
    _configured(monkeypatch)
    service = _service(factory)
    p256dh, auth = _valid_keys()
    stored, _ = asyncio.run(
        service.subscribe(endpoint=ENDPOINT, p256dh=p256dh, auth=auth)
    )
    asyncio.run(service.mark_disabled(stored.id))
    assert asyncio.run(service.list_subscriptions()) == []

    asyncio.run(service.subscribe(endpoint=ENDPOINT, p256dh=p256dh, auth=auth))
    assert len(asyncio.run(service.list_subscriptions())) == 1


@pytest.mark.parametrize(
    "endpoint",
    [
        "http://fcm.googleapis.com/fcm/send/abc",  # 非 https
        "fcm.googleapis.com/fcm/send/abc",  # 相对
        "",  # 空
    ],
)
def test_rejects_bad_endpoint(factory, monkeypatch, endpoint: str) -> None:
    _configured(monkeypatch)
    service = _service(factory)
    p256dh, auth = _valid_keys()

    with pytest.raises(AppError) as excinfo:
        asyncio.run(service.subscribe(endpoint=endpoint, p256dh=p256dh, auth=auth))
    assert excinfo.value.code is ErrorCode.PUSH_SUBSCRIPTION_INVALID
    assert excinfo.value.http_status == 422


def test_rejects_p256dh_that_is_not_on_the_curve(factory, monkeypatch) -> None:
    """长度对但不在曲线上：RFC 8291 §7 要求必须拒绝。"""
    _configured(monkeypatch)
    service = _service(factory)
    _, auth = _valid_keys()
    off_curve = _b64(b"\x04" + b"\x00" * 64)

    with pytest.raises(AppError) as excinfo:
        asyncio.run(service.subscribe(endpoint=ENDPOINT, p256dh=off_curve, auth=auth))
    assert excinfo.value.code is ErrorCode.PUSH_SUBSCRIPTION_INVALID


@pytest.mark.parametrize("bad", ["", "not-base64!!", _b64(b"\x05" * 64), _b64(b"\x04")])
def test_rejects_malformed_p256dh(factory, monkeypatch, bad: str) -> None:
    _configured(monkeypatch)
    service = _service(factory)
    _, auth = _valid_keys()

    with pytest.raises(AppError) as excinfo:
        asyncio.run(service.subscribe(endpoint=ENDPOINT, p256dh=bad, auth=auth))
    assert excinfo.value.code is ErrorCode.PUSH_SUBSCRIPTION_INVALID


@pytest.mark.parametrize("length", [15, 17, 0])
def test_rejects_auth_of_wrong_length(factory, monkeypatch, length: int) -> None:
    _configured(monkeypatch)
    service = _service(factory)
    p256dh, _ = _valid_keys()

    with pytest.raises(AppError) as excinfo:
        asyncio.run(service.subscribe(endpoint=ENDPOINT, p256dh=p256dh, auth=_b64(b"\x02" * length)))
    assert excinfo.value.code is ErrorCode.PUSH_SUBSCRIPTION_INVALID


def test_wire_shape_hides_credential_material(factory, monkeypatch) -> None:
    """列表不得回显 endpoint 全文与两个密钥字段。"""
    _configured(monkeypatch)
    service = _service(factory)
    p256dh, auth = _valid_keys()
    stored, _ = asyncio.run(
        service.subscribe(endpoint=ENDPOINT, p256dh=p256dh, auth=auth, user_agent="UA")
    )

    body = subscription_wire(stored)
    assert set(body) == {
        "id",
        "endpoint_host",
        "user_agent",
        "created_at",
        "last_success_at",
        "disabled",
        "disabled_at",
    }
    serialized = repr(body)
    assert ENDPOINT not in serialized
    assert p256dh not in serialized
    assert auth not in serialized
    assert body["endpoint_host"] == "fcm.googleapis.com"
    assert body["disabled"] is False


def test_delete_missing_subscription_reports_not_found(factory, monkeypatch) -> None:
    _configured(monkeypatch)
    service = _service(factory)

    with pytest.raises(AppError) as excinfo:
        asyncio.run(service.delete("does-not-exist"))
    assert excinfo.value.code is ErrorCode.PUSH_SUBSCRIPTION_NOT_FOUND
    assert excinfo.value.http_status == 404


def test_mark_sent_records_delivery_time(factory, monkeypatch) -> None:
    _configured(monkeypatch)
    service = _service(factory)
    p256dh, auth = _valid_keys()
    stored, _ = asyncio.run(
        service.subscribe(endpoint=ENDPOINT, p256dh=p256dh, auth=auth)
    )
    assert stored.last_success_at is None

    asyncio.run(service.mark_sent(stored.id))
    refreshed = asyncio.run(service.get(stored.id))
    assert refreshed.last_success_at is not None


def test_user_agent_is_truncated_not_rejected(factory, monkeypatch) -> None:
    _configured(monkeypatch)
    service = _service(factory)
    p256dh, auth = _valid_keys()

    stored, _ = asyncio.run(
        service.subscribe(
            endpoint=ENDPOINT, p256dh=p256dh, auth=auth, user_agent="x" * 500
        )
    )
    assert len(stored.user_agent or "") == 255


def test_active_subscriptions_exclude_disabled(factory, monkeypatch) -> None:
    _configured(monkeypatch)
    service = _service(factory)
    p256dh, auth = _valid_keys()
    keep, _ = asyncio.run(
        service.subscribe(endpoint=f"{ENDPOINT}-keep", p256dh=p256dh, auth=auth)
    )
    drop, _ = asyncio.run(
        service.subscribe(endpoint=f"{ENDPOINT}-drop", p256dh=p256dh, auth=auth)
    )
    asyncio.run(service.mark_disabled(drop.id))

    active = asyncio.run(service.active_subscriptions())
    assert [row.id for row in active] == [keep.id]


def test_generated_p256dh_from_browser_style_json_is_accepted(
    factory, monkeypatch
) -> None:
    """形状贴近真实 ``PushSubscription.toJSON()``：带填充与否都应接受。"""
    _configured(monkeypatch)
    service = _service(factory)
    private_key = ec.generate_private_key(ec.SECP256R1())
    raw = private_key.public_key().public_bytes(
        serialization.Encoding.X962, serialization.PublicFormat.UncompressedPoint
    )
    padded = base64.urlsafe_b64encode(raw).decode()  # 带 '=' 填充
    assert padded.endswith("=")
    crypto.b64url_decode(padded)  # 解码器容忍填充

    stored, created = asyncio.run(
        service.subscribe(
            endpoint=ENDPOINT, p256dh=padded, auth=base64.urlsafe_b64encode(b"\x07" * 16).decode()
        )
    )
    assert created is True
    assert stored.p256dh == padded
