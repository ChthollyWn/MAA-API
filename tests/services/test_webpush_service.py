"""Web Push 投递：载荷形状、VAPID 头、加密、失效处理与触达协同（M14 Task 3）。"""

from __future__ import annotations

import asyncio
import base64
import json
from types import SimpleNamespace

import pytest
from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric import ec
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker
from sqlalchemy.pool import NullPool
from sqlmodel import SQLModel

import maa_api.db.models  # noqa: F401
from maa_api.db.session import make_engine
from maa_api.services import webpush_crypto as crypto
from maa_api.services import webpush_vapid as vapid
from maa_api.services.push_subscription_service import PushSubscriptionService
from maa_api.services.webpush_service import (
    WebPushService,
    build_confirmation_payload,
)

ENDPOINT = "https://fcm.googleapis.com/fcm/send/abc-123"


def _b64(raw: bytes) -> str:
    return base64.urlsafe_b64encode(raw).rstrip(b"=").decode()


def _browser_keys() -> tuple[str, str, ec.EllipticCurvePrivateKey]:
    private_key = ec.generate_private_key(ec.SECP256R1())
    raw = private_key.public_key().public_bytes(
        serialization.Encoding.X962, serialization.PublicFormat.UncompressedPoint
    )
    return _b64(raw), _b64(b"\x09" * 16), private_key


class _Response:
    def __init__(self, status_code: int, text: str = "") -> None:
        self.status_code = status_code
        self.text = text


class _Recorder:
    """记录出站请求的假 httpx 客户端。"""

    def __init__(self, responses: list[_Response] | None = None) -> None:
        self.responses = list(responses or [])
        self.calls: list[tuple[str, str, dict]] = []

    async def request(self, method: str, url: str, **kwargs):
        self.calls.append((method, url, kwargs))
        if self.responses:
            return self.responses.pop(0)
        return _Response(201)


@pytest.fixture
def factory(tmp_path):
    engine = make_engine(
        f"sqlite+aiosqlite:///{tmp_path / 'webpush.db'}", poolclass=NullPool
    )

    async def create_schema():
        async with engine.begin() as connection:
            await connection.run_sync(SQLModel.metadata.create_all)

    asyncio.run(create_schema())
    yield async_sessionmaker(engine, expire_on_commit=False, class_=AsyncSession)
    engine.sync_engine.dispose()


def _settings(private_pem: str, subject: str = "mailto:ops@example.com"):
    return SimpleNamespace(
        notify=SimpleNamespace(vapid_private_key=private_pem, vapid_subject=subject)
    )


def _build(factory, http: _Recorder, *, private_pem: str | None = None):
    if private_pem is None:
        private_pem, _ = vapid.generate_vapid_key_pair()
    subscriptions = PushSubscriptionService(factory)
    service = WebPushService(
        subscriptions,
        http_client=http,
        settings_provider=lambda: _settings(private_pem),
    )
    return service, subscriptions, private_pem


def _register(subscriptions: PushSubscriptionService, endpoint: str = ENDPOINT) -> str:
    p256dh, auth, _ = _browser_keys()
    stored, _created = asyncio.run(
        subscriptions.subscribe(endpoint=endpoint, p256dh=p256dh, auth=auth)
    )
    return stored.id


# ---------------------------------------------------------------------------
# 载荷形状（Apple 卡片优先）
# ---------------------------------------------------------------------------


def test_payload_shape_uses_apple_first_design() -> None:
    payload = build_confirmation_payload(
        confirmation_id="c-1",
        title="碎石 3 颗",
        summary="该操作会消耗 3 颗源石。",
        url="/?confirm=c-1",
    )
    assert payload["title"] == "需要确认：碎石 3 颗"
    assert payload["body"] == "该操作会消耗 3 颗源石。"
    assert payload["tag"] == "confirm:c-1"
    assert payload["data"] == {"confirmationId": "c-1", "url": "/?confirm=c-1"}
    assert [action["action"] for action in payload["actions"]] == ["approve", "reject"]


def test_payload_can_omit_actions_for_platforms_without_buttons() -> None:
    payload = build_confirmation_payload(
        confirmation_id="c-1", title="t", summary="s", url="/", actions=False
    )
    assert payload["actions"] == []


def test_payload_truncates_long_text_instead_of_failing() -> None:
    payload = build_confirmation_payload(
        confirmation_id="c-1", title="x" * 500, summary="y" * 5000, url="/"
    )
    assert len(payload["title"]) <= 120
    assert len(payload["body"]) <= 400
    # 载荷本身必须能放进 RFC 8291 的单记录明文上限
    encoded = json.dumps(payload, ensure_ascii=False).encode()
    assert len(encoded) <= crypto.MAX_PLAINTEXT_BYTES


# ---------------------------------------------------------------------------
# 投递
# ---------------------------------------------------------------------------


def test_send_builds_rfc_compliant_request(factory) -> None:
    http = _Recorder([_Response(201)])
    service, subscriptions, private_pem = _build(factory, http)
    _register(subscriptions)

    payload = build_confirmation_payload(
        confirmation_id="c-9", title="碎石", summary="消耗 3 颗", url="/?confirm=c-9"
    )
    result = asyncio.run(service.broadcast(payload))

    assert result.delivered == 1
    assert result.failed == 0
    method, url, kwargs = http.calls[0]
    assert method == "POST"
    assert url == ENDPOINT
    headers = kwargs["headers"]
    assert headers["Content-Encoding"] == "aes128gcm"
    assert headers["Content-Type"] == "application/octet-stream"
    assert headers["TTL"] == "600"
    assert headers["Authorization"].startswith("vapid t=")
    assert f", k={vapid.public_key_from_private(vapid.load_vapid_private_key(private_pem))}" in headers["Authorization"]
    # 请求体是 86 octet 头 + 密文 + 16 octet tag
    assert len(kwargs["content"]) > 86 + 16


def test_authorization_jwt_audience_is_endpoint_origin(factory) -> None:
    http = _Recorder([_Response(201)])
    service, subscriptions, private_pem = _build(factory, http)
    _register(subscriptions)
    asyncio.run(service.broadcast(build_confirmation_payload(
        confirmation_id="c", title="t", summary="s", url="/"
    )))

    header = http.calls[0][2]["headers"]["Authorization"]
    token = header.split("t=", 1)[1].split(",", 1)[0]
    claims = vapid.verify_vapid_token(
        token, vapid.public_key_from_private(vapid.load_vapid_private_key(private_pem))
    )
    assert claims["aud"] == "https://fcm.googleapis.com"
    assert claims["sub"] == "mailto:ops@example.com"


def test_delivered_payload_decrypts_back_to_the_same_json(factory) -> None:
    """端到端自检：收端按 RFC 8291 解密，必须还原出原始载荷。"""
    http = _Recorder([_Response(201)])
    service, subscriptions, _ = _build(factory, http)
    # 密钥由本次"浏览器"生成，私钥留在测试里用于解密
    p256dh, auth, browser_private = _browser_keys()
    asyncio.run(subscriptions.subscribe(endpoint=ENDPOINT, p256dh=p256dh, auth=auth))

    payload = build_confirmation_payload(
        confirmation_id="c-42", title="碎石 3 颗", summary="消耗源石", url="/?confirm=c-42"
    )
    asyncio.run(service.broadcast(payload))

    body = http.calls[0][2]["content"]
    plaintext = _decrypt(body, browser_private, crypto.b64url_decode(auth))
    assert json.loads(plaintext) == payload


def _decrypt(body: bytes, browser_private, auth_secret: bytes) -> bytes:
    """按 RFC 8188 / 8291 解密单条记录（只用于测试）。"""
    import hashlib
    import hmac

    from cryptography.hazmat.primitives.ciphers.aead import AESGCM

    salt, rest = body[:16], body[16:]
    record_size = int.from_bytes(rest[:4], "big")
    keyid_len = rest[4]
    as_public = rest[5 : 5 + keyid_len]
    ciphertext = rest[5 + keyid_len :]
    assert record_size == 4096

    def extract(s, ikm):
        return hmac.new(s, ikm, hashlib.sha256).digest()

    def expand(prk, info, length):
        out, block, counter = b"", b"", 1
        while len(out) < length:
            block = hmac.new(prk, block + info + bytes([counter]), hashlib.sha256).digest()
            out += block
            counter += 1
        return out[:length]

    ua_public = browser_private.public_key().public_bytes(
        serialization.Encoding.X962, serialization.PublicFormat.UncompressedPoint
    )
    peer = ec.EllipticCurvePublicKey.from_encoded_point(ec.SECP256R1(), as_public)
    ecdh = browser_private.exchange(ec.ECDH(), peer)
    ikm = expand(extract(auth_secret, ecdh), b"WebPush: info\x00" + ua_public + as_public, 32)
    prk = extract(salt, ikm)
    cek = expand(prk, b"Content-Encoding: aes128gcm\x00", 16)
    nonce = expand(prk, b"Content-Encoding: nonce\x00", 12)
    return AESGCM(cek).decrypt(nonce, ciphertext, None).rstrip(b"\x02")


# ---------------------------------------------------------------------------
# 失效与失败处理
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("gone", [404, 410])
def test_gone_subscription_is_disabled_and_skipped_later(factory, gone: int) -> None:
    http = _Recorder([_Response(gone), _Response(201)])
    service, subscriptions, _ = _build(factory, http)
    subscription_id = _register(subscriptions)

    result = asyncio.run(service.broadcast({"title": "t", "body": "b"}))
    assert result.delivered == 0
    assert result.disabled == [subscription_id]
    assert asyncio.run(subscriptions.get(subscription_id)).disabled_at is not None

    second = asyncio.run(service.broadcast({"title": "t", "body": "b"}))
    assert second.skipped_reason == "no_subscription"
    assert len(http.calls) == 1  # 第二次没有再打推送服务


def test_transient_failure_does_not_disable_subscription(factory) -> None:
    """一次 500 是服务端抖动；停用会让用户彻底收不到通知。"""
    http = _Recorder([_Response(500, "boom")])
    service, subscriptions, _ = _build(factory, http)
    subscription_id = _register(subscriptions)

    result = asyncio.run(service.broadcast({"title": "t", "body": "b"}))
    assert result.failed == 1
    assert result.disabled == []
    assert asyncio.run(subscriptions.get(subscription_id)).disabled_at is None


def test_missing_vapid_config_skips_without_error(factory) -> None:
    http = _Recorder()
    service, subscriptions, _ = _build(factory, http, private_pem="")
    _register(subscriptions)

    result = asyncio.run(service.broadcast({"title": "t", "body": "b"}))
    assert result.skipped_reason == "vapid_not_configured"
    assert http.calls == []


def test_no_subscription_skips_without_error(factory) -> None:
    http = _Recorder()
    service, _subscriptions, _ = _build(factory, http)

    result = asyncio.run(service.broadcast({"title": "t", "body": "b"}))
    assert result.skipped_reason == "no_subscription"
    assert http.calls == []


def test_transport_exception_is_contained(factory) -> None:
    class _Exploding:
        async def request(self, *_args, **_kwargs):
            raise OSError("network down")

    service, subscriptions, _ = _build(factory, _Exploding())
    _register(subscriptions)

    result = asyncio.run(service.broadcast({"title": "t", "body": "b"}))
    assert result.failed == 1
    assert result.delivered == 0


def test_corrupt_stored_key_disables_subscription(factory) -> None:
    """订阅里存了坏密钥（客户端 bug）时停用，而不是每次重试都失败。"""
    http = _Recorder()
    service, subscriptions, _ = _build(factory, http)
    p256dh, auth, _ = _browser_keys()
    stored, _ = asyncio.run(
        subscriptions.subscribe(endpoint=ENDPOINT, p256dh=p256dh, auth=auth)
    )
    # 绕过服务层校验直接写坏密钥，模拟历史上写入的脏数据
    async def corrupt():
        from maa_api.db.repositories.push import PushSubscriptionRepository

        async with factory() as session:
            await PushSubscriptionRepository(session).upsert(
                endpoint=ENDPOINT,
                endpoint_host="fcm.googleapis.com",
                p256dh=_b64(b"\x04" + b"\x00" * 64),
                auth=auth,
                user_agent=None,
            )
            await session.commit()

    asyncio.run(corrupt())

    result = asyncio.run(service.broadcast({"title": "t", "body": "b"}))
    assert result.disabled == [stored.id]
    assert len(http.calls) == 0


def test_oversized_payload_is_trimmed_then_sent(factory) -> None:
    http = _Recorder([_Response(201)])
    service, subscriptions, _ = _build(factory, http)
    _register(subscriptions)

    result = asyncio.run(service.broadcast({"title": "t", "body": "y" * 20000}))

    assert result.delivered == 1
    # 载荷被裁到 RFC 8291 的单记录上限内（86 octet 头 + 明文 + tag）
    assert len(http.calls[0][2]["content"]) <= crypto.MAX_PLAINTEXT_BYTES + 86 + 16


# ---------------------------------------------------------------------------
# 触达协同
# ---------------------------------------------------------------------------


def test_no_push_when_a_client_is_connected(factory) -> None:
    http = _Recorder()
    service, subscriptions, _ = _build(factory, http)
    _register(subscriptions)

    result = asyncio.run(
        service.notify_confirmation(
            confirmation_id="c-1",
            title="t",
            summary="s",
            url="/",
            has_active_client=True,
        )
    )
    assert result.skipped_reason == "client_connected"
    assert http.calls == []


def test_push_when_no_client_is_connected(factory) -> None:
    http = _Recorder([_Response(201)])
    service, subscriptions, _ = _build(factory, http)
    _register(subscriptions)

    result = asyncio.run(
        service.notify_confirmation(
            confirmation_id="c-1",
            title="碎石 3 颗",
            summary="消耗源石",
            url="/?confirm=c-1",
            has_active_client=False,
        )
    )
    assert result.delivered == 1
    assert len(http.calls) == 1


# ---------------------------------------------------------------------------
# 通知文案：用户从锁屏点进来，标题里不能出现内部标识符
# ---------------------------------------------------------------------------


def test_confirmation_title_maps_tool_names_to_readable_actions() -> None:
    from maa_api.services.webpush_service import confirmation_title

    assert confirmation_title("click", {}) == "在游戏画面点击"
    assert confirmation_title("submit_pipeline", {}) == "提交任务流水线"
    # 认不出来的工具名直接退回原值，不编造动作
    assert confirmation_title("some_new_tool", {}) == "some_new_tool"


def test_confirmation_title_reports_consumption_in_plain_chinese() -> None:
    """``Fight.stone=3`` 这种内部形态对用户没有意义，必须换成资源名。"""
    from maa_api.services.webpush_service import confirmation_title

    title = confirmation_title(
        "run_task",
        {"task": {"name": "Fight", "params": {"stage": "1-7", "stone": 3}}},
    )
    assert "刷理智" in title
    assert "源石" in title
    assert "3" in title
    assert "stone" not in title and "Fight" not in title


def test_confirmation_title_handles_pipeline_task_lists() -> None:
    from maa_api.services.webpush_service import confirmation_title

    title = confirmation_title(
        "submit_pipeline",
        {"tasks": [{"name": "Fight", "params": {"medicine": 2}}]},
    )
    assert "理智药" in title and "×2" in title


def test_confirmation_title_omits_impact_when_nothing_is_consumed() -> None:
    from maa_api.services.webpush_service import confirmation_title

    # 没有消耗就不写影响范围：误报消耗比不报更糟
    assert confirmation_title("run_task", {"task": {"name": "Award", "params": {}}}) == (
        "执行任务"
    )


def test_confirmation_title_tolerates_malformed_arguments() -> None:
    from maa_api.services.webpush_service import confirmation_title

    for arguments in (None, "not-a-mapping", 42, {"tasks": "not-a-list"}, {"task": []}):
        assert confirmation_title("click", arguments) == "在游戏画面点击"


def test_notify_confirmation_sends_the_readable_title_to_the_browser(factory) -> None:
    """端到端确认标题：解密收到的载荷，里面必须是中文动作而不是内部工具名。"""
    http = _Recorder([_Response(201)])
    service, subscriptions, _ = _build(factory, http)
    p256dh, auth, browser_private = _browser_keys()
    asyncio.run(subscriptions.subscribe(endpoint=ENDPOINT, p256dh=p256dh, auth=auth))

    asyncio.run(
        service.notify_confirmation(
            confirmation_id="c-1",
            title="run_task",
            summary="该操作会消耗资源",
            url="/?confirm=c-1",
            has_active_client=False,
            arguments={"task": {"name": "Fight", "params": {"stone": 3}}},
        )
    )

    plaintext = _decrypt(
        http.calls[0][2]["content"], browser_private, crypto.b64url_decode(auth)
    )
    payload = json.loads(plaintext)
    assert "刷理智" in payload["title"] and "源石" in payload["title"]
    assert "run_task" not in payload["title"]
    assert payload["data"]["confirmationId"] == "c-1"
