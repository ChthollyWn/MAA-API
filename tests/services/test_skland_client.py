"""森空岛客户端测试（M42 规格 §2.2 / §4.3）：签名逐字复算、登录链路、错误归一与日志口径。

全部走 ``httpx.MockTransport``：**不发任何真实网络请求**；重试退避常量 monkeypatch 成 0，
用例不会真的等 0.5 秒（本仓没有 pytest-asyncio，异步用例统一 ``asyncio.run``）。
"""

from __future__ import annotations

import asyncio
import hashlib
import hmac
import json
import logging
from typing import Any, Callable, Mapping

import httpx
import pytest

from maa_api.domain.errors import AppError, ErrorCode
from maa_api.services.skland import client as skland_client
from maa_api.services.skland.client import (
    AS_BASE_URL,
    DEFAULT_EXTRA_HEADERS,
    SKLAND_APP_CODE,
    ZONAI_BASE_URL,
    BindingRole,
    ScanLogin,
    SklandClient,
    SklandCredential,
)

# ---------------------------------------------------------------------------
# 固定时钟与金标
# ---------------------------------------------------------------------------
FIXED_NOW = 1_700_000_000.0
#: `int(FIXED_NOW) - 1`（规格 §2.2 的那一秒偏移）。
TIMESTAMP = "1699999999"

CRED = "cred-9f2c-secret"
TOKEN = "token-7ab1-secret"
RENEWED_TOKEN = "token-renewed-secret"
SCAN_ID = "e2264fb214643b5df50c9a820c4c1b9f"
SCAN_URL = f"hypergryph://scan_login?scanId={SCAN_ID}"
SCAN_CODE = "scan-code-abc"
HG_TOKEN = "hg-token-abc"
AUTH_CODE = "auth-code-abc"

CLIENT_LOGGER = "maa_api.services.skland.client"

#: 金标签名（十六进制）：用「实现之外的独立算式」算好后钉死，防止实现与测试助手一起漂移。
GOLDEN_REFRESH_SIGN = "1d8e226a8fd930311e7ef9a828200600"
GOLDEN_PLAYER_INFO_SIGN = "e501f9d3c7607899ec5a5cf0f7996dfb"
GOLDEN_POST_SIGN = "78c0d10386f3e0b01709bb817f382894"
GOLDEN_EXTRA_HEADERS_SIGN = "8084c7688445b3368eea74c58d66614c"

#: POST 分支用的 body（含引号，验证紧凑 JSON 的转义与待签串一致）。
POST_BODY: dict[str, Any] = {"code": "a'b", "kind": 1, "nested": {"x": [1, 2]}}
POST_PATH = "/api/v1/example"


def expected_sign(
    *,
    path: str,
    query_params: str,
    token: str = TOKEN,
    extra_headers: Mapping[str, str] | None = None,
) -> str:
    """在测试里独立复算一次签名（只照规格 §2.2 的字面算式，不调用被测实现的任何函数）。"""
    header_for_sign = dict(DEFAULT_EXTRA_HEADERS if extra_headers is None else extra_headers)
    header_for_sign["timestamp"] = TIMESTAMP
    secret = (
        path + query_params + TIMESTAMP + json.dumps(header_for_sign, separators=(",", ":"))
    )
    digest = hmac.new(token.encode("utf-8"), secret.encode("utf-8"), hashlib.sha256).hexdigest()
    return hashlib.md5(digest.encode("utf-8")).hexdigest()


def async_test(func: Callable[..., Any]) -> Callable[..., Any]:
    """本仓库不装 pytest-asyncio：异步用例统一用 `asyncio.run` 跑。"""

    def wrapper(*args: Any, **kwargs: Any) -> Any:
        return asyncio.run(func(*args, **kwargs))

    wrapper.__name__ = func.__name__
    wrapper.__doc__ = func.__doc__
    return wrapper


class Upstream:
    """按顺序回放的假上游：记录每个请求；队列里的异常对象直接抛给 httpx。"""

    def __init__(self, *items: Any) -> None:
        self._items = list(items)
        self.requests: list[httpx.Request] = []

    def __call__(self, request: httpx.Request) -> httpx.Response:
        self.requests.append(request)
        item = self._items.pop(0)
        if isinstance(item, Exception):
            raise item
        return item

    @property
    def paths(self) -> list[str]:
        return [request.url.path for request in self.requests]

    @property
    def count(self) -> int:
        return len(self.requests)


def as_json(payload: Any, status_code: int = 200) -> httpx.Response:
    return httpx.Response(status_code, json=payload)


def hypergryph_ok(data: Mapping[str, Any] | None = None, msg: str = "OK") -> httpx.Response:
    body: dict[str, Any] = {"status": 0, "msg": msg, "type": "A"}
    if data is not None:
        body["data"] = dict(data)
    return as_json(body)


def zonai_ok(data: Mapping[str, Any] | None = None) -> httpx.Response:
    return as_json({"code": 0, "message": "OK", "data": dict(data or {})})


def zonai_error(code: int, message: str, *, status_code: int = 200) -> httpx.Response:
    return as_json({"code": code, "message": message}, status_code=status_code)


def make_client(upstream: Callable[[httpx.Request], httpx.Response], **kwargs: Any) -> SklandClient:
    kwargs.setdefault("now", lambda: FIXED_NOW)
    return SklandClient(transport=httpx.MockTransport(upstream), **kwargs)


@pytest.fixture(autouse=True)
def _no_backoff(monkeypatch: pytest.MonkeyPatch) -> None:
    """退避常量置 0：重试用例不必真的等 0.5 秒（仍然走 `asyncio.sleep` 这条路径）。"""
    monkeypatch.setattr(skland_client, "RETRY_BACKOFF_SECONDS", 0.0)


# ---------------------------------------------------------------------------
# ① 固定 now 下的签名逐字复算
# ---------------------------------------------------------------------------
@async_test
async def test_get_query_string_is_signed_literally() -> None:
    """GET：待签串的 query_params 是 URL 查询串，sign 与金标逐字节一致。"""
    upstream = Upstream(zonai_ok({"status": {"level": 120}}))
    client = make_client(upstream)

    data = await client.player_info(SklandCredential(cred=CRED, token=TOKEN), "123456789")

    assert data == {"status": {"level": 120}}
    assert upstream.count == 1
    request = upstream.requests[0]
    assert request.method == "GET"
    assert str(request.url) == f"{ZONAI_BASE_URL}/api/v1/game/player/info?uid=123456789"
    assert request.headers["cred"] == CRED
    assert request.headers["timestamp"] == TIMESTAMP
    assert request.headers["platform"] == "1"
    assert request.headers["vName"] == "1.0.0"
    assert request.headers["sign"] == expected_sign(
        path="/api/v1/game/player/info", query_params="uid=123456789"
    )
    assert request.headers["sign"] == GOLDEN_PLAYER_INFO_SIGN


@async_test
async def test_post_body_is_signed_as_compact_json() -> None:
    """POST：待签串是 body 的紧凑 JSON，且实际发送的字节与签的是同一份。

    本里程碑没有「带 cred 的 POST」公开方法（`refresh` 是 GET），所以直接打 `_send` 的
    POST 分支——这条分支是规格 §2.2 明文要求的（签到类接口以后会用到）。
    """
    upstream = Upstream(zonai_ok())
    client = make_client(upstream)
    query_params = json.dumps(POST_BODY, separators=(",", ":"))

    await client._send(
        base_url=ZONAI_BASE_URL,
        path=POST_PATH,
        method="POST",
        body=POST_BODY,
        credential=SklandCredential(cred=CRED, token=TOKEN),
    )

    request = upstream.requests[0]
    assert request.headers["Content-Type"] == "application/json"
    assert request.content == query_params.encode("utf-8")
    assert request.headers["sign"] == expected_sign(path=POST_PATH, query_params=query_params)
    assert request.headers["sign"] == GOLDEN_POST_SIGN
    # 没有查询串时 sign 只用 path + body：换个查询串必须换出不同的 sign。
    assert request.headers["sign"] != expected_sign(path=POST_PATH, query_params="")


@async_test
async def test_refresh_signs_with_empty_query_string() -> None:
    """GET 无查询串：query_params 是空串（不是省略），sign 与金标一致。"""
    upstream = Upstream(zonai_ok({"token": RENEWED_TOKEN}))
    client = make_client(upstream)

    renewed = await client.refresh(SklandCredential(cred=CRED, token=TOKEN))

    assert renewed == SklandCredential(cred=CRED, token=RENEWED_TOKEN)
    request = upstream.requests[0]
    assert str(request.url) == f"{ZONAI_BASE_URL}/api/v1/auth/refresh"
    assert request.headers["sign"] == expected_sign(path="/api/v1/auth/refresh", query_params="")
    assert request.headers["sign"] == GOLDEN_REFRESH_SIGN


@async_test
async def test_extra_headers_are_injectable_and_signed() -> None:
    """`platform` / `vName` 可注入且进签名（W0 真机要以实测值覆盖，规格 §2.5-10）。"""
    extra = {"platform": "2", "vName": "2.3.0", "dId": "abc123"}
    upstream = Upstream(zonai_ok({"token": RENEWED_TOKEN}))
    client = make_client(upstream, extra_headers=extra)

    await client.refresh(SklandCredential(cred=CRED, token=TOKEN))

    request = upstream.requests[0]
    assert request.headers["platform"] == "2"
    assert request.headers["vName"] == "2.3.0"
    assert request.headers["dId"] == "abc123"
    assert request.headers["sign"] == expected_sign(
        path="/api/v1/auth/refresh", query_params="", extra_headers=extra
    )
    assert request.headers["sign"] == GOLDEN_EXTRA_HEADERS_SIGN


@async_test
async def test_client_headers_use_project_user_agent() -> None:
    """`User-Agent` 与项目 UA 同一取法；`Accept-Encoding` / `Connection` 照社区实现。"""
    upstream = Upstream(hypergryph_ok({"scanId": SCAN_ID, "scanUrl": SCAN_URL}))
    client = make_client(upstream)

    await client.generate_scan()

    request = upstream.requests[0]
    assert request.headers["User-Agent"].startswith("MAA-API/")
    assert request.headers["Accept-Encoding"] == "gzip"
    assert request.headers["Connection"] == "close"


# ---------------------------------------------------------------------------
# ② 扫码会话
# ---------------------------------------------------------------------------
@async_test
async def test_generate_scan_parses_scan_id_and_url() -> None:
    """第 1 步：body 只带 appCode；scanUrl 原样取上游，不自己拼 `hypergryph://`。"""
    upstream = Upstream(hypergryph_ok({"scanId": SCAN_ID, "scanUrl": SCAN_URL}))
    client = make_client(upstream)

    login = await client.generate_scan()

    assert login == ScanLogin(scan_id=SCAN_ID, scan_url=SCAN_URL)
    request = upstream.requests[0]
    assert request.method == "POST"
    assert str(request.url) == f"{AS_BASE_URL}/general/v1/gen_scan/login"
    assert json.loads(request.content) == {"appCode": SKLAND_APP_CODE}
    # 鹰角侧不需要签名。
    assert "sign" not in request.headers
    assert "cred" not in request.headers


@async_test
async def test_generate_scan_rejects_missing_shape() -> None:
    """上游少了 `scanUrl` 也不许自己拼：报 `SKLAND_UPSTREAM_FAILED`。"""
    upstream = Upstream(hypergryph_ok({"scanId": SCAN_ID}))
    client = make_client(upstream)

    with pytest.raises(AppError) as info:
        await client.generate_scan()

    assert info.value.code is ErrorCode.SKLAND_UPSTREAM_FAILED
    assert "scanUrl" in (info.value.message or "")


# ---------------------------------------------------------------------------
# ③ scan_status 三态与 scanCode
# ---------------------------------------------------------------------------
@async_test
async def test_scan_status_confirmed_returns_scan_code() -> None:
    upstream = Upstream(hypergryph_ok({"scanCode": SCAN_CODE}))
    client = make_client(upstream)

    status, scan_code, msg = await client.scan_status(SCAN_ID)

    assert (status, scan_code, msg) == (0, SCAN_CODE, "OK")
    request = upstream.requests[0]
    assert request.method == "GET"
    assert str(request.url) == f"{AS_BASE_URL}/general/v1/scan_status?scanId={SCAN_ID}"


@async_test
async def test_scan_status_waiting_and_expired_are_returned_not_raised() -> None:
    """`100` 未扫码 / `102` 已失效都是正常回答，原样透出（规格 §2.1 第 1 条）。"""
    upstream = Upstream(
        as_json({"status": 100, "msg": "未扫码", "type": "A"}),
        as_json({"status": 102, "msg": "已失效", "type": "A"}),
        as_json({"status": 106, "msg": "别的", "type": "A"}),
    )
    client = make_client(upstream)

    assert await client.scan_status(SCAN_ID) == (100, None, "未扫码")
    assert await client.scan_status(SCAN_ID) == (102, None, "已失效")
    assert await client.scan_status(SCAN_ID) == (106, None, "别的")
    assert upstream.count == 3


@async_test
async def test_scan_status_http_error_is_upstream_failed() -> None:
    """只有参数长度校验会 400：HTTP 非 2xx 走上游失败，且不重试。"""
    upstream = Upstream(
        as_json(
            {"msg": "Key: 'ScanStatusReq.scanId' Error:Field validation for 'scanId' failed"},
            status_code=400,
        )
    )
    client = make_client(upstream)

    with pytest.raises(AppError) as info:
        await client.scan_status("zzz")

    assert info.value.code is ErrorCode.SKLAND_UPSTREAM_FAILED
    assert "scanId" in (info.value.message or "")
    assert upstream.count == 1


# ---------------------------------------------------------------------------
# ④ token_by_scan_code / grant / generate_cred
# ---------------------------------------------------------------------------
@async_test
async def test_token_by_scan_code_posts_scan_code() -> None:
    upstream = Upstream(hypergryph_ok({"token": HG_TOKEN}))
    client = make_client(upstream)

    assert await client.token_by_scan_code(SCAN_CODE) == HG_TOKEN
    request = upstream.requests[0]
    assert str(request.url) == f"{AS_BASE_URL}/user/auth/v1/token_by_scan_code"
    assert json.loads(request.content) == {"scanCode": SCAN_CODE}


@async_test
async def test_grant_posts_app_code_token_and_type() -> None:
    upstream = Upstream(hypergryph_ok({"code": AUTH_CODE}))
    client = make_client(upstream)

    assert await client.grant(HG_TOKEN) == AUTH_CODE
    request = upstream.requests[0]
    assert str(request.url) == f"{AS_BASE_URL}/user/oauth2/v2/grant"
    assert json.loads(request.content) == {
        "appCode": SKLAND_APP_CODE,
        "token": HG_TOKEN,
        "type": 0,
    }


@async_test
async def test_generate_cred_assembles_credential_without_signing() -> None:
    """第 5 步是链路起点：还没有 cred/token，请求不带签名头。"""
    upstream = Upstream(zonai_ok({"cred": CRED, "token": TOKEN}))
    client = make_client(upstream)

    credential = await client.generate_cred(AUTH_CODE)

    assert credential == SklandCredential(cred=CRED, token=TOKEN)
    request = upstream.requests[0]
    assert str(request.url) == f"{ZONAI_BASE_URL}/api/v1/user/auth/generate_cred_by_code"
    assert json.loads(request.content) == {"code": AUTH_CODE, "kind": 1}
    assert "sign" not in request.headers
    assert "cred" not in request.headers


@async_test
async def test_full_login_chain_uses_each_step_output() -> None:
    """第 1–5 步串起来：每一步的产物就是下一步的入参。"""
    upstream = Upstream(
        hypergryph_ok({"scanId": SCAN_ID, "scanUrl": SCAN_URL}),
        hypergryph_ok({"scanCode": SCAN_CODE}),
        hypergryph_ok({"token": HG_TOKEN}),
        hypergryph_ok({"code": AUTH_CODE}),
        zonai_ok({"cred": CRED, "token": TOKEN}),
    )
    client = make_client(upstream)

    login = await client.generate_scan()
    assert await client.scan_status(login.scan_id) == (0, SCAN_CODE, "OK")
    assert await client.token_by_scan_code(SCAN_CODE) == HG_TOKEN
    assert await client.grant(HG_TOKEN) == AUTH_CODE
    credential = await client.generate_cred(AUTH_CODE)

    assert credential == SklandCredential(cred=CRED, token=TOKEN)
    assert upstream.paths == [
        "/general/v1/gen_scan/login",
        "/general/v1/scan_status",
        "/user/auth/v1/token_by_scan_code",
        "/user/oauth2/v2/grant",
        "/api/v1/user/auth/generate_cred_by_code",
    ]


# ---------------------------------------------------------------------------
# ⑤ refresh：带 cred 头 + 旧 token 签名
# ---------------------------------------------------------------------------
@async_test
async def test_refresh_uses_stored_cred_and_old_token_for_signing() -> None:
    upstream = Upstream(zonai_ok({"token": RENEWED_TOKEN}))
    client = make_client(upstream)

    renewed = await client.refresh(SklandCredential(cred=CRED, token=TOKEN))

    request = upstream.requests[0]
    assert request.headers["cred"] == CRED
    assert request.headers["timestamp"] == TIMESTAMP
    assert request.headers["sign"] == expected_sign(
        path="/api/v1/auth/refresh", query_params="", token=TOKEN
    )
    # 换了签名密钥必须换出新 sign：旧 token 与续期后的 token 不是同一个待签身份。
    assert request.headers["sign"] != expected_sign(
        path="/api/v1/auth/refresh", query_params="", token=RENEWED_TOKEN
    )
    assert renewed.cred == CRED
    assert renewed.token == RENEWED_TOKEN


@async_test
async def test_refresh_rejects_missing_token() -> None:
    upstream = Upstream(zonai_ok({"cred": CRED}))
    client = make_client(upstream)

    with pytest.raises(AppError) as info:
        await client.refresh(SklandCredential(cred=CRED, token=TOKEN))

    assert info.value.code is ErrorCode.SKLAND_UPSTREAM_FAILED
    assert "token" in (info.value.message or "")


# ---------------------------------------------------------------------------
# ⑥ binding：过滤与字段缺省
# ---------------------------------------------------------------------------
BINDING_PAYLOAD: dict[str, Any] = {
    "code": 0,
    "message": "OK",
    "data": {
        "bindingList": [
            {
                "uid": "123456789",
                "appCode": "arknights",
                "channelMasterId": "1",
                "nickname": "博士",
                "level": 120,
                "isDefault": True,
                "isDeleted": False,
            },
            {"uid": "987654321", "appCode": "arknights"},
            {"uid": "555555555", "appCode": "endfield", "nickname": "别的游戏"},
            {"appCode": "arknights", "nickname": "缺 uid"},
            "not-a-mapping",
        ]
    },
}


@async_test
async def test_binding_filters_arknights_and_tolerates_missing_fields() -> None:
    upstream = Upstream(as_json(BINDING_PAYLOAD))
    client = make_client(upstream)

    roles = await client.binding(SklandCredential(cred=CRED, token=TOKEN))

    assert roles == [
        BindingRole(
            uid="123456789",
            app_code="arknights",
            channel_master_id="1",
            nickname="博士",
            level=120,
            is_default=True,
        ),
        BindingRole(
            uid="987654321",
            app_code="arknights",
            channel_master_id=None,
            nickname=None,
            level=None,
            is_default=False,
        ),
    ]
    request = upstream.requests[0]
    assert str(request.url) == f"{ZONAI_BASE_URL}/api/v1/game/player/binding"
    assert request.headers["cred"] == CRED
    assert request.headers["sign"] == expected_sign(
        path="/api/v1/game/player/binding", query_params=""
    )


@async_test
async def test_binding_empty_list_is_not_an_error() -> None:
    upstream = Upstream(zonai_ok({"bindingList": []}))
    client = make_client(upstream)

    assert await client.binding(SklandCredential(cred=CRED, token=TOKEN)) == []


@async_test
async def test_binding_missing_list_is_upstream_failed() -> None:
    """键缺失是形状变了（空列表才是「没有绑定」），不静默当空。"""
    upstream = Upstream(zonai_ok({"other": 1}))
    client = make_client(upstream)

    with pytest.raises(AppError) as info:
        await client.binding(SklandCredential(cred=CRED, token=TOKEN))

    assert info.value.code is ErrorCode.SKLAND_UPSTREAM_FAILED
    assert "bindingList" in (info.value.message or "")


# ---------------------------------------------------------------------------
# ⑦ player_info：原样返回 data
# ---------------------------------------------------------------------------
@async_test
async def test_player_info_returns_raw_data() -> None:
    """归一化是 `mapping.py` 的事：客户端把 `data` 原样交出去（含本轮不落库的字段）。"""
    data = {
        "chars": [{"charId": "char_002_amiya", "level": 80, "potentialRank": 5}],
        "status": {"name": "博士", "level": 120},
        "charInfoMap": {"char_002_amiya": {"name": "阿米娅"}},
        "equipmentInfoMap": {},
        "gainTime": 1_700_000_000,
    }
    upstream = Upstream(zonai_ok(data))
    client = make_client(upstream)

    info = await client.player_info(SklandCredential(cred=CRED, token=TOKEN), "123456789")

    assert info == data
    assert info["chars"][0]["potentialRank"] == 5
    assert str(upstream.requests[0].url) == (
        f"{ZONAI_BASE_URL}/api/v1/game/player/info?uid=123456789"
    )


@async_test
async def test_player_info_missing_data_is_upstream_failed() -> None:
    upstream = Upstream(as_json({"code": 0, "message": "OK"}))
    client = make_client(upstream)

    with pytest.raises(AppError) as info:
        await client.player_info(SklandCredential(cred=CRED, token=TOKEN), "1")

    assert info.value.code is ErrorCode.SKLAND_UPSTREAM_FAILED
    assert "data" in (info.value.message or "")


# ---------------------------------------------------------------------------
# ⑧ 错误归一
# ---------------------------------------------------------------------------
@async_test
async def test_hypergryph_business_status_is_upstream_failed_without_retry() -> None:
    """鹰角侧 HTTP 200 + `status=100`：看 status 不看 HTTP 码，业务码不重试。"""
    upstream = Upstream(as_json({"status": 100, "msg": "登录失败", "type": "B"}))
    client = make_client(upstream)

    with pytest.raises(AppError) as info:
        await client.token_by_scan_code("deadbeef")

    assert info.value.code is ErrorCode.SKLAND_UPSTREAM_FAILED
    assert info.value.message == "登录失败"
    assert upstream.count == 1


@async_test
async def test_zonai_code_10002_is_login_failed() -> None:
    upstream = Upstream(zonai_error(10002, "用户未登录", status_code=200))
    client = make_client(upstream)

    with pytest.raises(AppError) as info:
        await client.binding(SklandCredential(cred=CRED, token=TOKEN))

    assert info.value.code is ErrorCode.SKLAND_LOGIN_FAILED
    assert info.value.message == "用户未登录"
    assert upstream.count == 1


@async_test
async def test_zonai_http_401_is_login_failed() -> None:
    """实测形状：未登录是 HTTP 401 + `code=10002`（规格 §2.1 第 4 条）。"""
    upstream = Upstream(
        as_json({"code": 10002, "message": "用户未登录", "timestamp": "1791772721"}, status_code=401)
    )
    client = make_client(upstream)

    with pytest.raises(AppError) as info:
        await client.binding(SklandCredential(cred=CRED, token=TOKEN))

    assert info.value.code is ErrorCode.SKLAND_LOGIN_FAILED
    assert info.value.message == "用户未登录"
    # 401 是业务结果，不重试。
    assert upstream.count == 1


@async_test
async def test_zonai_other_business_code_is_upstream_failed() -> None:
    upstream = Upstream(zonai_error(10003, "触发了风控"))
    client = make_client(upstream)

    with pytest.raises(AppError) as info:
        await client.player_info(SklandCredential(cred=CRED, token=TOKEN), "1")

    assert info.value.code is ErrorCode.SKLAND_UPSTREAM_FAILED
    assert info.value.message == "触发了风控"
    assert upstream.count == 1


@async_test
async def test_server_error_is_retried_once_then_succeeds() -> None:
    upstream = Upstream(as_json({"code": 1}, status_code=503), zonai_ok({"bindingList": []}))
    client = make_client(upstream)

    assert await client.binding(SklandCredential(cred=CRED, token=TOKEN)) == []
    assert upstream.count == 2


@async_test
async def test_hypergryph_server_error_is_retried_too() -> None:
    """重试在两侧共用的发送层：鹰角侧 5xx 同样是「重试一次再成功」。"""
    upstream = Upstream(
        httpx.Response(502, text="bad gateway"),
        hypergryph_ok({"scanId": SCAN_ID, "scanUrl": SCAN_URL}),
    )
    client = make_client(upstream)

    login = await client.generate_scan()

    assert login.scan_id == SCAN_ID
    assert upstream.count == 2


@async_test
async def test_hypergryph_server_error_after_retry_is_upstream_failed() -> None:
    upstream = Upstream(httpx.Response(503), httpx.Response(503))
    client = make_client(upstream)

    with pytest.raises(AppError) as info:
        await client.generate_scan()

    assert info.value.code is ErrorCode.SKLAND_UPSTREAM_FAILED
    assert info.value.message == "HTTP 503"
    assert upstream.count == 2


@async_test
async def test_server_error_after_retry_is_upstream_failed() -> None:
    upstream = Upstream(
        httpx.Response(500, text="<html>boom</html>"),
        httpx.Response(500, text="<html>boom</html>"),
    )
    client = make_client(upstream)

    with pytest.raises(AppError) as info:
        await client.binding(SklandCredential(cred=CRED, token=TOKEN))

    assert info.value.code is ErrorCode.SKLAND_UPSTREAM_FAILED
    assert info.value.message == "HTTP 500"
    assert upstream.count == 2


@async_test
async def test_network_error_is_retried_once_then_succeeds() -> None:
    upstream = Upstream(httpx.ConnectError("connection refused"), zonai_ok({"bindingList": []}))
    client = make_client(upstream)

    assert await client.binding(SklandCredential(cred=CRED, token=TOKEN)) == []
    assert upstream.count == 2


@async_test
async def test_network_error_after_retry_is_upstream_failed() -> None:
    upstream = Upstream(
        httpx.ConnectError("connection refused"), httpx.ReadTimeout("timed out")
    )
    client = make_client(upstream)

    with pytest.raises(AppError) as info:
        await client.binding(SklandCredential(cred=CRED, token=TOKEN))

    assert info.value.code is ErrorCode.SKLAND_UPSTREAM_FAILED
    assert "连接失败" in (info.value.message or "")
    assert upstream.count == 2


@async_test
async def test_aclose_is_idempotent() -> None:
    client = make_client(Upstream())
    await client.aclose()
    await client.aclose()


# ---------------------------------------------------------------------------
# ⑨ 日志：只有端点/状态码/耗时，绝无凭据与正文
# ---------------------------------------------------------------------------
def test_logs_never_contain_credentials_or_response_body(
    caplog: pytest.LogCaptureFixture,
) -> None:
    """整条链路跑一遍，DEBUG 全量捕获：cred / token / sign / 上游正文一个字都不许出现。"""
    upstream = Upstream(
        hypergryph_ok({"scanId": SCAN_ID, "scanUrl": SCAN_URL}),
        hypergryph_ok({"scanCode": SCAN_CODE}),
        hypergryph_ok({"token": HG_TOKEN}),
        hypergryph_ok({"code": AUTH_CODE}),
        zonai_ok({"cred": CRED, "token": TOKEN}),
        zonai_ok({"token": RENEWED_TOKEN}),
        as_json(BINDING_PAYLOAD),
        zonai_ok({"chars": [{"charId": "char_002_amiya"}]}),
    )
    client = make_client(upstream)

    async def scenario() -> tuple[int, str | None, dict[str, Any]]:
        login = await client.generate_scan()
        status, scan_code, _ = await client.scan_status(login.scan_id)
        hg_token = await client.token_by_scan_code(scan_code or "")
        auth_code = await client.grant(hg_token)
        credential = await client.generate_cred(auth_code)
        renewed = await client.refresh(credential)
        roles = await client.binding(renewed)
        info = await client.player_info(renewed, roles[0].uid)
        return status, scan_code, info

    caplog.set_level(logging.DEBUG)
    with caplog.at_level(logging.DEBUG, logger=CLIENT_LOGGER):
        status, scan_code, info = asyncio.run(scenario())

    assert (status, scan_code) == (0, SCAN_CODE)
    assert info == {"chars": [{"charId": "char_002_amiya"}]}
    # 日志确实记了（否则下面的断言是空过）。
    assert "森空岛取数" in caplog.text
    assert "endpoint=/api/v1/auth/refresh" in caplog.text
    assert "status=200" in caplog.text

    secrets = [
        CRED,
        TOKEN,
        RENEWED_TOKEN,
        HG_TOKEN,
        AUTH_CODE,
        SCAN_CODE,
        expected_sign(path="/api/v1/auth/refresh", query_params=""),
        expected_sign(path="/api/v1/game/player/info", query_params="uid=123456789"),
        "chars",  # 响应正文（`char_002_amiya` 只在 body 里）
        "char_002_amiya",
    ]
    for secret in secrets:
        assert secret not in caplog.text


def test_failure_logs_carry_no_body(caplog: pytest.LogCaptureFixture) -> None:
    """失败路径同样只记端点与状态码：上游文案只进异常 `message`，不进日志。"""
    upstream = Upstream(as_json({"code": 10002, "message": "用户未登录"}))
    client = make_client(upstream)

    async def scenario() -> AppError:
        with pytest.raises(AppError) as info:
            await client.binding(SklandCredential(cred=CRED, token=TOKEN))
        return info.value

    with caplog.at_level(logging.INFO, logger=CLIENT_LOGGER):
        error = asyncio.run(scenario())

    assert error.code is ErrorCode.SKLAND_LOGIN_FAILED
    assert "endpoint=/api/v1/game/player/binding" in caplog.text
    for secret in (CRED, TOKEN, "用户未登录"):
        assert secret not in caplog.text
