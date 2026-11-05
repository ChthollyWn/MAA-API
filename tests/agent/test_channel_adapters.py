"""M85 T3：适配器协议、Fake 与四平台（spec §4.2、§4.5、§5 T3、§6）。

覆盖：

- ``ChannelAdapter`` 完整协议、``Inbound`` 形状与 ``FakeAdapter`` 的记录/注入；
- §6 的 ``test_missing_extra_does_not_break_start``：三个 SDK 平台缺 extra 时
  ``start`` 只记「未安装」且不抛，Telegram（httpx，无 extra）照常启动；
- 未启用/未配置不启动、``stop`` 可重复调用；
- 四平台的发送 URL、回执与失败分类（网络/429/5xx → retry，其余 4xx → fatal）；
- 飞书 EncryptKey AES 解密 + SHA-256 签名、钉钉 HMAC 签名、QQ Ed25519 签名
  的固定样例（密钥都是假值，不是真实凭据）。

测试纪律与 ``tests/agent/test_outbox.py`` 一致：同步函数 + ``asyncio.run``，
HTTP 用 ``httpx.MockTransport``，SDK 用 ``sys.modules`` 里的假模块，不访问外网。
"""

from __future__ import annotations

import asyncio
import base64
import hashlib
import hmac
import json
import logging
import sys
import types
from typing import Any

import httpx
import pytest

from maa_api.agent.channels import adapter as adapter_module
from maa_api.agent.channels.adapter import (
    ChannelAdapter,
    Inbound,
    build_adapter,
    build_adapters,
    config_value,
)
from maa_api.agent.channels.dingtalk import (
    DINGTALK_ACCESS_TOKEN_HEADER,
    DINGTALK_ACCESS_TOKEN_PATH,
    DINGTALK_SEND_GROUP_PATH,
    DINGTALK_SEND_USER_PATH,
    DingTalkAdapter,
    inbound_from_dingtalk_message,
    verify_dingtalk_signature,
)
from maa_api.agent.channels.errors import OutboxFatal, OutboxRetry
from maa_api.agent.channels.fake import FakeAdapter
from maa_api.agent.channels.feishu import (
    FeishuAdapter,
    FeishuDecryptError,
    decrypt_feishu_event,
    inbound_from_feishu_event,
    verify_feishu_signature,
    verify_feishu_token,
)
from maa_api.agent.channels.qq import (
    QQ_SANDBOX_API_BASE,
    QQ_TOKEN_URL,
    QQAdapter,
    inbound_from_qq_message,
    qq_ed25519_public_key,
    verify_qq_signature,
)
from maa_api.agent.channels.telegram import (
    TelegramAdapter,
    inbound_from_update,
)
from maa_api.domain.enums import ChannelPlatform

# ---------------------------------------------------------------------------
# 固定假样例：密钥/密文/签名都由下面的算法生成，不来自真实应用。
# ---------------------------------------------------------------------------

FEISHU_TEST_KEY = "test-feishu-encrypt-key"
FEISHU_TEST_TIMESTAMP = "1725442341"
FEISHU_TEST_NONCE = "test-nonce"
FEISHU_TEST_CIPHER = (
    "MDEyMzQ1Njc4OWFiY2RlZsMnkL2CooYf4fwBtf+BPFYqDcqHUTkso94zVnE0p3vCG8Q879B7p0awc/"
    "bq5tHWaoy4DrR1Ty3bI4+kb30on8b9tvZm4XNR86SPdRKr6RkBEdmhZcRPnd9Fcvt6CuW9QPqDv/"
    "XKkCnojLXJpnMqP2LlLBzmF4XcJQ7wbf3S2e0Yiriid+jrZzHB5hvCAoCzAqFUxTt/+oV/7HQ7bI"
    "JIgB+2K3S9u7AhN0+kJpXSRGBhxv3XYlPGF2DZakUj44aA0oLrenJEDqYYvEn5szxyOgLoZO8Ad2A"
    "FyacGWNTB1JAaJWrKCNTAxhMs2a5g+3/c1Q9htJ3Tg/0cGVMfMFVrxBNrVfmio9C6djkZhHaaAP0"
    "Ko4627DTnZoZF5RcJlnM7UW7lpEDGbJod9piatF1I9cdbgp6+WlMkFB6U56q1RpFAHJJo71IdPle"
    "ToN8oCcYuzA=="
)
FEISHU_TEST_BODY = json.dumps({"encrypt": FEISHU_TEST_CIPHER}, separators=(",", ":"))
FEISHU_TEST_SIGNATURE = (
    "a9f82bf450c09ee9a6a71804d5e245e9fbeda20fdb475c83b9233db4e9e6adea"
)

DINGTALK_TEST_SECRET = "test-dingtalk-client-secret"
DINGTALK_TEST_TIMESTAMP = "1725442341"
DINGTALK_TEST_SIGNATURE = base64.b64encode(
    hmac.new(
        DINGTALK_TEST_SECRET.encode(),
        f"{DINGTALK_TEST_TIMESTAMP}\n{DINGTALK_TEST_SECRET}".encode(),
        hashlib.sha256,
    ).digest()
).decode()

#: QQ 官方签名文档里的示例密钥与示例 body（公开示例值）。
QQ_TEST_SECRET = "naOC0ocQE3shWLAfffVLB1rhYPG7"
QQ_TEST_TIMESTAMP = "1725442341"
QQ_TEST_BODY = '{ "op": 0,"d": {}, "t": "GATEWAY_EVENT_NAME"}'
QQ_TEST_SIGNATURE = (
    "2eb9983ebb8bb209e78fd095942f58e442656656e7975d01e64f9023a84b7c96"
    "4290fdd40e5500c33867ccfe9563b7e0b6bac0e1d42c13e787b304fd51f71102"
)
#: 同一示例密钥按官方规则推导出的公钥，用来确认 seed 规则没写错。
QQ_TEST_PUBLIC_KEY = (
    "d7c362fe78aef81ff23287b493628b5db02a3c4fe30b215e4d19609b5d76673a"
)


def _client(handler: Any) -> httpx.AsyncClient:
    return httpx.AsyncClient(transport=httpx.MockTransport(handler))


def _body(request: httpx.Request) -> dict[str, Any]:
    return json.loads(request.content or b"{}")


# ---------------------------------------------------------------------------
# 协议、构造与 FakeAdapter
# ---------------------------------------------------------------------------


def test_protocol_and_builder() -> None:
    fields = list(Inbound.__dataclass_fields__)
    assert fields == [
        "platform",
        "external_user_id",
        "external_chat_id",
        "text",
        "message_id",
    ]
    assert isinstance(FakeAdapter(), ChannelAdapter)
    assert list(build_adapters({})) == list(ChannelPlatform)
    assert isinstance(build_adapter("telegram", {}), TelegramAdapter)
    assert config_value({"channels.telegram_enabled": True}, "telegram_enabled") is True
    assert config_value({"telegram_enabled": True}, "telegram_enabled") is True
    assert config_value({}, "telegram_enabled", False) is False


def test_fake_adapter_records_injects_and_classifies() -> None:
    received: list[Inbound] = []

    async def on_inbound(inbound: Inbound) -> None:
        received.append(inbound)

    async def scenario() -> None:
        fake = FakeAdapter(
            ChannelPlatform.FEISHU,
            on_inbound=on_inbound,
            receipt={"message_id": "om_fake"},
        )
        await fake.start()
        await fake.start()
        assert fake.running
        assert await fake.send("chat-1", "你好") == {
            "platform": "feishu",
            "message_id": "om_fake",
        }
        assert fake.sent == [("chat-1", "你好")]

        inbound = Inbound(ChannelPlatform.FEISHU, "u1", "chat-1", "hi", "m1")
        await fake.inject(inbound)
        assert received == [inbound]
        assert fake.inbound == [inbound]

        fake.retry_times = 2
        with pytest.raises(OutboxRetry):
            await fake.send("chat-1", "retry-1")
        with pytest.raises(OutboxRetry):
            await fake.send("chat-1", "retry-2")
        fake.fatal = True
        with pytest.raises(OutboxFatal):
            await fake.send("chat-1", "dead")
        assert fake.sent == [("chat-1", "你好")]

        await fake.stop()
        await fake.stop()
        assert not fake.running

    asyncio.run(scenario())


# ---------------------------------------------------------------------------
# §6：缺 extra 不影响 start；未启用/未配置不启动
# ---------------------------------------------------------------------------


def test_missing_extra_does_not_break_start(
    monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture
) -> None:
    def missing(name: str) -> Any:
        raise ImportError(name)

    monkeypatch.setattr(adapter_module.importlib, "import_module", missing)

    async def scenario() -> None:
        sdk_adapters = [
            FeishuAdapter(
                {
                    "feishu_enabled": True,
                    "feishu_app_id": "cli_fake",
                    "feishu_app_secret": "fake-secret",
                }
            ),
            DingTalkAdapter(
                {
                    "dingtalk_enabled": True,
                    "dingtalk_client_id": "ding_fake",
                    "dingtalk_client_secret": "fake-secret",
                }
            ),
            QQAdapter(
                {
                    "qq_enabled": True,
                    "qq_app_id": "fake-app",
                    "qq_app_secret": "fake-secret",
                }
            ),
        ]
        for adapter in sdk_adapters:
            await adapter.start()  # 不抛
            assert not adapter.running
            await adapter.stop()
            await adapter.stop()

        def handler(request: httpx.Request) -> httpx.Response:
            assert request.url.path.endswith("/getUpdates")
            return httpx.Response(200, json={"ok": True, "result": []})

        async with _client(handler) as client:
            telegram = TelegramAdapter(
                {
                    "telegram_enabled": True,
                    "telegram_bot_token": "123:fake-token",
                },
                http_client=client,
            )
            await telegram.start()
            assert telegram.running
            await telegram.stop()
            assert not telegram.running
            await telegram.stop()

    with caplog.at_level(logging.WARNING):
        asyncio.run(scenario())

    assert "未安装" in caplog.text
    for extra in ("im-feishu", "im-dingtalk", "im-qq"):
        assert extra in caplog.text


def test_disabled_or_unconfigured_does_not_start() -> None:
    cases = [
        (TelegramAdapter, ChannelPlatform.TELEGRAM, {"telegram_bot_token": "t"}),
        (
            FeishuAdapter,
            ChannelPlatform.FEISHU,
            {"feishu_app_id": "cli_fake", "feishu_app_secret": "s"},
        ),
        (
            DingTalkAdapter,
            ChannelPlatform.DINGTALK,
            {"dingtalk_client_id": "ding_fake", "dingtalk_client_secret": "s"},
        ),
        (
            QQAdapter,
            ChannelPlatform.QQ,
            {"qq_app_id": "app", "qq_app_secret": "s"},
        ),
    ]

    async def scenario() -> None:
        for adapter_cls, platform, credentials in cases:
            disabled = adapter_cls(
                {f"{platform}_enabled": False, **credentials}
            )
            await disabled.start()
            assert not disabled.running
            await disabled.stop()

            missing = adapter_cls({f"{platform}_enabled": True})
            await missing.start()
            assert not missing.running
            await missing.stop()

    asyncio.run(scenario())


# ---------------------------------------------------------------------------
# Telegram
# ---------------------------------------------------------------------------


def test_telegram_send_url_and_receipt() -> None:
    requests: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        requests.append(request)
        return httpx.Response(
            200, json={"ok": True, "result": {"message_id": 17, "date": 1}}
        )

    async def scenario() -> None:
        async with _client(handler) as client:
            adapter = TelegramAdapter(
                {
                    "channels.telegram_enabled": True,
                    "channels.telegram_bot_token": "123:fake-token",
                    "channels.telegram_proxy": "",
                },
                http_client=client,
            )
            receipt = await adapter.send("42", "你好")
            assert receipt["message_id"] == 17
            assert receipt["platform"] == "telegram"

    asyncio.run(scenario())
    assert str(requests[0].url) == (
        "https://api.telegram.org/bot123:fake-token/sendMessage"
    )
    assert requests[0].method == "POST"
    assert _body(requests[0]) == {"chat_id": "42", "text": "你好"}


@pytest.mark.parametrize(
    ("status", "expected"),
    [(429, OutboxRetry), (500, OutboxRetry), (400, OutboxFatal), (404, OutboxFatal)],
)
def test_telegram_status_classification(
    status: int, expected: type[Exception]
) -> None:
    async def scenario() -> None:
        async with _client(
            lambda request: httpx.Response(
                status, json={"ok": False, "description": "boom"}
            )
        ) as client:
            adapter = TelegramAdapter(
                {"telegram_enabled": True, "telegram_bot_token": "t"},
                http_client=client,
            )
            with pytest.raises(expected):
                await adapter.send("42", "hi")

    asyncio.run(scenario())


def test_telegram_network_error_is_retry() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        raise httpx.ConnectError("boom")

    async def scenario() -> None:
        async with _client(handler) as client:
            adapter = TelegramAdapter(
                {"telegram_enabled": True, "telegram_bot_token": "t"},
                http_client=client,
            )
            with pytest.raises(OutboxRetry):
                await adapter.send("42", "hi")

    asyncio.run(scenario())


def test_telegram_inbound_and_poll_once() -> None:
    update = {
        "update_id": 5,
        "message": {
            "message_id": 9,
            "text": " /bind ABC23DEF ",
            "chat": {"id": -100},
            "from": {"id": 7},
        },
    }
    received: list[Inbound] = []

    async def on_inbound(inbound: Inbound) -> None:
        received.append(inbound)

    async def scenario() -> None:
        async with _client(
            lambda request: httpx.Response(200, json={"ok": True, "result": [update]})
        ) as client:
            adapter = TelegramAdapter(
                {"telegram_enabled": True, "telegram_bot_token": "t"},
                http_client=client,
                on_inbound=on_inbound,
            )
            updates, offset = await adapter.poll_once(0)
            assert updates == [update]
            assert offset == 6

    asyncio.run(scenario())
    assert received == [
        Inbound(ChannelPlatform.TELEGRAM, "7", "-100", "/bind ABC23DEF", "9")
    ]
    assert inbound_from_update({"update_id": 1}) is None
    assert inbound_from_update({"message": {"text": "", "chat": {}, "from": {}}}) is None


# ---------------------------------------------------------------------------
# 飞书
# ---------------------------------------------------------------------------


def test_feishu_signature_and_decrypt_sample() -> None:
    assert verify_feishu_signature(
        timestamp=FEISHU_TEST_TIMESTAMP,
        nonce=FEISHU_TEST_NONCE,
        body=FEISHU_TEST_BODY,
        encrypt_key=FEISHU_TEST_KEY,
        signature=FEISHU_TEST_SIGNATURE,
    )
    assert not verify_feishu_signature(
        timestamp=FEISHU_TEST_TIMESTAMP,
        nonce=FEISHU_TEST_NONCE,
        body=FEISHU_TEST_BODY,
        encrypt_key=FEISHU_TEST_KEY,
        signature="0" * 64,
    )
    assert not verify_feishu_signature(
        timestamp=FEISHU_TEST_TIMESTAMP,
        nonce=FEISHU_TEST_NONCE,
        body=FEISHU_TEST_BODY + " ",
        encrypt_key=FEISHU_TEST_KEY,
        signature=FEISHU_TEST_SIGNATURE,
    )

    payload = decrypt_feishu_event(FEISHU_TEST_CIPHER, FEISHU_TEST_KEY)
    assert payload["header"]["event_type"] == "im.message.receive_v1"
    assert payload["event"]["message"]["message_id"] == "om_test_msg"
    assert verify_feishu_token(
        payload["header"]["token"], "test-verification-token"
    )
    assert not verify_feishu_token(payload["header"]["token"], "wrong")
    assert not verify_feishu_token(None, "test-verification-token")
    with pytest.raises(FeishuDecryptError):
        decrypt_feishu_event(FEISHU_TEST_CIPHER, "wrong-encrypt-key")

    inbound = inbound_from_feishu_event(payload)
    assert inbound == Inbound(
        ChannelPlatform.FEISHU,
        "ou_test_user",
        "oc_test_chat",
        "你好，绑定码是 ABC23DEF",
        "om_test_msg",
    )
    assert inbound_from_feishu_event({"event": {}}) is None


def _install_fake_lark(monkeypatch: pytest.MonkeyPatch) -> types.ModuleType:
    """在 sys.modules 里放一个只够发送用的假 lark 模块。"""
    fake_api = types.ModuleType("lark_oapi.api.im.v1")

    class _Request:
        def __init__(self, **kwargs: Any) -> None:
            self.__dict__.update(kwargs)

        @classmethod
        def builder(cls) -> "_RequestBuilder":
            return _RequestBuilder(cls)

    class _RequestBuilder:
        def __init__(self, cls: type[_Request]) -> None:
            self._cls = cls
            self._values: dict[str, Any] = {}

        def __getattr__(self, name: str) -> Any:
            def setter(value: Any) -> "_RequestBuilder":
                self._values[name] = value
                return self

            return setter

        def build(self) -> _Request:
            return self._cls(**self._values)

    fake_api.CreateMessageRequest = _Request
    fake_api.CreateMessageRequestBody = _Request
    fake_root = types.ModuleType("lark_oapi")
    fake_root.api = types.SimpleNamespace(
        im=types.SimpleNamespace(v1=fake_api)
    )
    monkeypatch.setitem(sys.modules, "lark_oapi", fake_root)
    monkeypatch.setitem(sys.modules, "lark_oapi.api.im.v1", fake_api)
    return fake_api


class _FakeFeishuResponse:
    def __init__(
        self,
        ok: bool,
        *,
        code: int = 0,
        msg: str = "",
        status: int | None = None,
        message_id: str | None = None,
    ) -> None:
        self.code = code
        self.msg = msg
        self.raw = (
            types.SimpleNamespace(status_code=status) if status is not None else None
        )
        self.data = (
            types.SimpleNamespace(message_id=message_id)
            if message_id is not None
            else None
        )
        self._ok = ok

    def success(self) -> bool:
        return self._ok


class _FakeFeishuClient:
    def __init__(self, response: _FakeFeishuResponse) -> None:
        self.response = response
        self.requests: list[Any] = []
        self.im = types.SimpleNamespace(
            v1=types.SimpleNamespace(
                message=types.SimpleNamespace(create=self._create)
            )
        )

    def _create(self, request: Any) -> _FakeFeishuResponse:
        self.requests.append(request)
        return self.response


def test_feishu_send_uses_chat_id(monkeypatch: pytest.MonkeyPatch) -> None:
    _install_fake_lark(monkeypatch)
    client = _FakeFeishuClient(_FakeFeishuResponse(True, message_id="om_sent"))

    async def scenario() -> None:
        adapter = FeishuAdapter(
            {
                "feishu_enabled": True,
                "feishu_app_id": "cli_fake",
                "feishu_app_secret": "fake-secret",
            },
            sdk_client=client,
        )
        receipt = await adapter.send("oc_chat", "你好")
        assert receipt == {
            "platform": "feishu",
            "message_id": "om_sent",
            "chat_id": "oc_chat",
        }

    asyncio.run(scenario())
    request = client.requests[0]
    assert request.receive_id_type == "chat_id"
    assert request.request_body.receive_id == "oc_chat"
    assert request.request_body.msg_type == "text"
    assert json.loads(request.request_body.content) == {"text": "你好"}


@pytest.mark.parametrize(
    ("response", "expected"),
    [
        (_FakeFeishuResponse(False, status=500, msg="boom"), OutboxRetry),
        (_FakeFeishuResponse(False, status=400, msg="bad"), OutboxFatal),
        (_FakeFeishuResponse(False, code=99991400), OutboxRetry),
        (_FakeFeishuResponse(False, code=10003), OutboxFatal),
    ],
)
def test_feishu_status_classification(
    monkeypatch: pytest.MonkeyPatch,
    response: _FakeFeishuResponse,
    expected: type[Exception],
) -> None:
    _install_fake_lark(monkeypatch)
    client = _FakeFeishuClient(response)

    async def scenario() -> None:
        adapter = FeishuAdapter(
            {"feishu_enabled": True, "feishu_app_id": "a", "feishu_app_secret": "s"},
            sdk_client=client,
        )
        with pytest.raises(expected):
            await adapter.send("oc_chat", "hi")

    asyncio.run(scenario())


# ---------------------------------------------------------------------------
# 钉钉
# ---------------------------------------------------------------------------


def test_dingtalk_signature_sample() -> None:
    assert verify_dingtalk_signature(
        DINGTALK_TEST_TIMESTAMP,
        DINGTALK_TEST_SIGNATURE,
        DINGTALK_TEST_SECRET,
    )
    assert not verify_dingtalk_signature(
        DINGTALK_TEST_TIMESTAMP, "bad-signature", DINGTALK_TEST_SECRET
    )
    assert not verify_dingtalk_signature("", DINGTALK_TEST_SIGNATURE, "secret")


def test_dingtalk_send_user_group_urls_and_receipt() -> None:
    requests: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        requests.append(request)
        if request.url.path == DINGTALK_ACCESS_TOKEN_PATH:
            return httpx.Response(
                200, json={"accessToken": "tok", "expireIn": 7200}
            )
        return httpx.Response(200, json={"processQueryKey": "pq-1"})

    async def scenario() -> None:
        async with _client(handler) as client:
            adapter = DingTalkAdapter(
                {
                    "channels.dingtalk_enabled": True,
                    "channels.dingtalk_client_id": "ding_client",
                    "channels.dingtalk_client_secret": "fake-secret",
                    "channels.dingtalk_robot_code": "ding_robot",
                },
                http_client=client,
            )
            user_receipt = await adapter.send("staff_1", "你好")
            group_receipt = await adapter.send("cid_group_1", "群里好")
            assert user_receipt["process_query_key"] == "pq-1"
            assert group_receipt["process_query_key"] == "pq-1"

    asyncio.run(scenario())
    # token 只取一次，两个发送复用缓存。
    assert len(requests) == 3
    token_request, user_request, group_request = requests
    assert str(token_request.url) == (
        "https://api.dingtalk.com/v1.0/oauth2/accessToken"
    )
    assert _body(token_request) == {
        "appKey": "ding_client",
        "appSecret": "fake-secret",
    }
    assert str(user_request.url) == (
        f"https://api.dingtalk.com{DINGTALK_SEND_USER_PATH}"
    )
    assert user_request.headers[DINGTALK_ACCESS_TOKEN_HEADER] == "tok"
    assert _body(user_request)["userIds"] == ["staff_1"]
    assert json.loads(_body(user_request)["msgParam"]) == {"content": "你好"}
    assert str(group_request.url) == (
        f"https://api.dingtalk.com{DINGTALK_SEND_GROUP_PATH}"
    )
    assert _body(group_request)["openConversationId"] == "cid_group_1"


@pytest.mark.parametrize(
    ("status", "expected"),
    [(429, OutboxRetry), (503, OutboxRetry), (400, OutboxFatal)],
)
def test_dingtalk_status_classification(
    status: int, expected: type[Exception]
) -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path == DINGTALK_ACCESS_TOKEN_PATH:
            return httpx.Response(200, json={"accessToken": "tok"})
        return httpx.Response(status, json={"message": "boom"})

    async def scenario() -> None:
        async with _client(handler) as client:
            adapter = DingTalkAdapter(
                {
                    "dingtalk_enabled": True,
                    "dingtalk_client_id": "ding_client",
                    "dingtalk_client_secret": "s",
                },
                http_client=client,
            )
            with pytest.raises(expected):
                await adapter.send("staff_1", "hi")

    asyncio.run(scenario())


def test_dingtalk_inbound_conversion() -> None:
    inbound = inbound_from_dingtalk_message(
        {
            "conversationId": "cid_chat",
            "conversationType": "2",
            "senderStaffId": "staff_1",
            "senderId": "sender_1",
            "msgId": "msg_1",
            "text": {"content": " 你好 "},
        }
    )
    assert inbound == Inbound(
        ChannelPlatform.DINGTALK, "staff_1", "group:cid_chat", "你好", "msg_1"
    )
    direct = inbound_from_dingtalk_message(
        {
            "conversationId": "cid_direct",
            "conversationType": "1",
            "senderStaffId": "staff_2",
            "msgId": "msg_2",
            "text": {"content": "hi"},
        }
    )
    assert direct == Inbound(
        ChannelPlatform.DINGTALK, "staff_2", "user:staff_2", "hi", "msg_2"
    )
    assert inbound_from_dingtalk_message("{not-json") is None
    assert inbound_from_dingtalk_message({"text": {"content": ""}}) is None


# ---------------------------------------------------------------------------
# QQ
# ---------------------------------------------------------------------------


def test_qq_signature_sample() -> None:
    assert qq_ed25519_public_key(QQ_TEST_SECRET).hex() == QQ_TEST_PUBLIC_KEY
    assert verify_qq_signature(
        QQ_TEST_SIGNATURE, QQ_TEST_TIMESTAMP, QQ_TEST_BODY, QQ_TEST_SECRET
    )
    assert not verify_qq_signature(
        QQ_TEST_SIGNATURE, "1725442342", QQ_TEST_BODY, QQ_TEST_SECRET
    )
    assert not verify_qq_signature("zz", QQ_TEST_TIMESTAMP, QQ_TEST_BODY, QQ_TEST_SECRET)
    assert not verify_qq_signature(
        QQ_TEST_SIGNATURE, QQ_TEST_TIMESTAMP, QQ_TEST_BODY + " ", QQ_TEST_SECRET
    )


def test_qq_send_user_url_and_receipt() -> None:
    requests: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        requests.append(request)
        if str(request.url) == QQ_TOKEN_URL:
            return httpx.Response(
                200, json={"access_token": "tok", "expires_in": "7200"}
            )
        return httpx.Response(
            200, json={"id": "msg_1", "timestamp": "1725442341"}
        )

    async def scenario() -> None:
        async with _client(handler) as client:
            adapter = QQAdapter(
                {
                    "channels.qq_enabled": True,
                    "channels.qq_app_id": "app-id",
                    "channels.qq_app_secret": "fake-secret",
                    "channels.qq_sandbox": False,
                },
                http_client=client,
            )
            receipt = await adapter.send("user:openid-1", "你好")
            assert receipt["message_id"] == "msg_1"
            assert receipt["platform"] == "qq"

    asyncio.run(scenario())
    assert len(requests) == 2
    token_request, send_request = requests
    assert str(token_request.url) == "https://api.bot.qq.com/app/getAppAccessToken"
    assert _body(token_request) == {
        "appId": "app-id",
        "clientSecret": "fake-secret",
    }
    assert str(send_request.url) == (
        "https://api.sgroup.qq.com/v2/users/openid-1/messages"
    )
    assert send_request.headers["Authorization"] == "QQBot tok"
    assert _body(send_request) == {"content": "你好", "msg_type": 0}


def test_qq_channel_url_uses_sandbox() -> None:
    requests: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        requests.append(request)
        if str(request.url) == QQ_TOKEN_URL:
            return httpx.Response(200, json={"access_token": "tok"})
        return httpx.Response(200, json={"id": "msg_2"})

    async def scenario() -> None:
        async with _client(handler) as client:
            adapter = QQAdapter(
                {
                    "qq_enabled": True,
                    "qq_app_id": "app-id",
                    "qq_app_secret": "fake-secret",
                    "qq_sandbox": True,
                },
                http_client=client,
            )
            await adapter.send("channel:6688", "hi")
            await adapter.send("group:group-1", "hi")

    asyncio.run(scenario())
    assert str(requests[1].url) == (
        f"{QQ_SANDBOX_API_BASE}/channels/6688/messages"
    )
    assert _body(requests[1]) == {"content": "hi"}
    assert str(requests[2].url) == (
        f"{QQ_SANDBOX_API_BASE}/v2/groups/group-1/messages"
    )
    assert _body(requests[2]) == {"content": "hi", "msg_type": 0}


@pytest.mark.parametrize(
    ("status", "expected"),
    [(429, OutboxRetry), (500, OutboxRetry), (400, OutboxFatal)],
)
def test_qq_status_classification(status: int, expected: type[Exception]) -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        if str(request.url) == QQ_TOKEN_URL:
            return httpx.Response(200, json={"access_token": "tok"})
        return httpx.Response(status, json={"message": "boom"})

    async def scenario() -> None:
        async with _client(handler) as client:
            adapter = QQAdapter(
                {
                    "qq_enabled": True,
                    "qq_app_id": "app-id",
                    "qq_app_secret": "s",
                },
                http_client=client,
            )
            with pytest.raises(expected):
                await adapter.send("user:openid-1", "hi")

    asyncio.run(scenario())


def test_qq_token_business_error_classification() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json={"code": 100007, "message": "appid invalid"})

    async def scenario() -> None:
        async with _client(handler) as client:
            adapter = QQAdapter(
                {"qq_enabled": True, "qq_app_id": "bad", "qq_app_secret": "bad"},
                http_client=client,
            )
            with pytest.raises(OutboxFatal):
                await adapter.send("user:openid-1", "hi")

    asyncio.run(scenario())


def test_qq_inbound_conversion() -> None:
    channel = inbound_from_qq_message(
        {
            "id": "msg_1",
            "content": " 你好 ",
            "channel_id": "6688",
            "author": {"id": "user_1"},
        }
    )
    assert channel == Inbound(
        ChannelPlatform.QQ, "user_1", "channel:6688", "你好", "msg_1"
    )

    group = inbound_from_qq_message(
        {
            "id": "msg_2",
            "content": "hi",
            "group_openid": "group_1",
            "author": {"member_openid": "member_1"},
        }
    )
    assert group == Inbound(
        ChannelPlatform.QQ, "member_1", "group:group_1", "hi", "msg_2"
    )

    direct = inbound_from_qq_message(
        {
            "id": "msg_3",
            "content": "hi",
            "author": {"user_openid": "openid_1"},
        }
    )
    assert direct == Inbound(
        ChannelPlatform.QQ, "openid_1", "user:openid_1", "hi", "msg_3"
    )
    assert inbound_from_qq_message({"content": ""}) is None
