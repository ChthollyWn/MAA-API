"""四渠道 token 鉴权与会话依赖（M3-06；docs/05 §5、§4.1、§5.2、§5.3）。

被测 app 全部在本文件里现搭（``FastAPI(...)`` + M3-04 的
:func:`~maa_api.api.errors.register_exception_handlers`），不 import ``maa_api.main``
（M3-09 之前它还是旧装配）。配置走 ``tests/api`` 夹具的 ``tmp_settings``（默认空
token ＝ 免鉴权模式，间接参数化传 token），数据库隔离走 ``isolated_db``（见
``tests/api/conftest.py`` 与 docs/ENVIRONMENT.md 的 M2-13 实测）。

三个自建 app
============

``build_guarded_app()``
    一条受保护的 ``/api/ping``（GET/HEAD/POST/PUT/PATCH/DELETE 六个方法）、
    身份探针 ``/api/whoami``、受保护的 ``/api/system/health``，最后注册
    ``GET /{full_path:path}`` 当 SPA catch-all —— ``/docs``、``/static/*``、
    ``/daily``、``/`` 与 ``/mcp/*``、``/api/unknown`` 都会落到它身上，正好用来
    端到端验证豁免清单与命名空间边界。

``build_probe_app()``
    不挂鉴权的 ``/probe``，只回显 :func:`~maa_api.api.deps.extract_token` 的结果，
    用来钉提取契约（渠道、原始值、不回退、``None``）。

``build_session_app()``
    三条 ``Depends(get_session)`` 路由：回声会话绑定的 URL、在 ``session_probe``
    表里插一行、数行数。建表 / 插入 / 计数全部走同一个 ``TestClient``（同一个事件
    循环），避免把 aiosqlite 连接池里的连接跨事件循环复用。
"""

from __future__ import annotations

from collections.abc import Iterator
from typing import Annotated

import pytest
from fastapi import Depends, FastAPI, Request
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from maa_api.api import deps
from maa_api.api.deps import (
    COOKIE_NAME,
    EXEMPT_PATH_PREFIXES,
    TokenChannel,
    auth_enabled,
    extract_token,
    get_session,
    is_exempt_path,
    require_auth,
    token_matches,
)
from maa_api.api.errors import register_exception_handlers
from maa_api.db import session as db_session
from maa_api.domain.errors import AppError, ErrorCode

TOKEN = "s3cret-token"

#: 配置了 token 时仍必须 200 的路径（docs/05 §5.3 + SPA catch-all）。
EXEMPT_PATHS = (
    "/api/system/health",
    "/docs",
    "/redoc",
    "/openapi.json",
    "/static/app.js",
    "/static/deep/nested/x.js",
    "/manifest.webmanifest",
    "/sw.js",
    "/daily",           # SPA catch-all
    "/",                # SPA 入口
)

#: 配置了 token 时必须 401 的路径（/mcp 刻意不豁免）。
PROTECTED_PATHS = (
    "/mcp",
    "/mcp/ping",
    "/api",
    "/api/unknown",
    "/api/pipelines",
    "/api/system/healthz",
)


# ----------------------------------------------------------------------
# 夹具
# ----------------------------------------------------------------------


@pytest.fixture(autouse=True)
def _clean_rate_limiter() -> Iterator[None]:
    """每个用例前后清空进程内限流状态，避免用例互相污染。"""
    deps.reset_rate_limiter()
    yield
    deps.reset_rate_limiter()


@pytest.fixture
def clock(monkeypatch: pytest.MonkeyPatch) -> list[float]:
    """可手动推进的限流时钟：``clock[0] += 61`` 即跳过冷却/窗口。"""
    now = [1000.0]
    monkeypatch.setattr(deps, "_now", lambda: now[0])
    return now


@pytest.fixture
def strict_limiter(monkeypatch: pytest.MonkeyPatch) -> None:
    """把阈值压到 3 次、冷却与窗口固定 60 秒，便于低成本逼出 429。"""
    monkeypatch.setattr(deps, "FAILURE_THRESHOLD", 3)
    monkeypatch.setattr(deps, "COOLDOWN_SECONDS", 60.0)
    monkeypatch.setattr(deps, "FAILURE_WINDOW_SECONDS", 60.0)


# ----------------------------------------------------------------------
# 自建 app
# ----------------------------------------------------------------------


def build_guarded_app() -> FastAPI:
    """受保护的最小 app：``/api/ping`` + 身份探针 + 受保护的 health + SPA catch-all。"""
    app = FastAPI(docs_url=None, redoc_url=None, openapi_url=None)
    register_exception_handlers(app)
    guarded = [Depends(require_auth)]

    @app.api_route(
        "/api/ping",
        methods=["GET", "HEAD", "POST", "PUT", "PATCH", "DELETE"],
        dependencies=guarded,
    )
    async def ping() -> dict[str, bool]:
        return {"ok": True}

    @app.get("/api/whoami", dependencies=guarded)
    async def whoami(request: Request) -> dict[str, object]:
        hit = extract_token(request)
        return {
            "channel": None if hit is None else hit.channel.value,
            "value": None if hit is None else hit.value,
        }

    @app.get("/api/system/health", dependencies=guarded)
    async def health() -> dict[str, bool]:
        return {"auth_enabled": auth_enabled()}

    # 必须最后注册（docs/05 §6.17）：/docs、/static/*、/daily、/ 都落到这里，
    # /api/unknown 与 /mcp/* 也落到这里 —— 后者用来验证命名空间不被 SPA 规则豁免。
    @app.get("/{full_path:path}", dependencies=guarded)
    async def spa(full_path: str) -> dict[str, str]:
        return {"spa": full_path}

    return app


def build_probe_app() -> FastAPI:
    """只回显 :func:`extract_token` 结果的免鉴权 app（不读 settings）。"""
    app = FastAPI(docs_url=None, redoc_url=None, openapi_url=None)

    @app.get("/probe")
    async def probe(request: Request) -> dict[str, object]:
        hit = extract_token(request)
        return {
            "channel": None if hit is None else hit.channel.value,
            "value": None if hit is None else hit.value,
        }

    return app


def build_session_app() -> FastAPI:
    """``Depends(get_session)`` 的三条路由：绑定 URL / 建表 / 插一行 / 数行数。"""
    app = FastAPI(docs_url=None, redoc_url=None, openapi_url=None)
    register_exception_handlers(app)

    @app.get("/session/url")
    async def session_url(
        session: Annotated[AsyncSession, Depends(get_session)],
    ) -> dict[str, str]:
        return {"url": str(session.bind.url)}

    @app.post("/session/table")
    async def session_table(
        session: Annotated[AsyncSession, Depends(get_session)],
    ) -> dict[str, bool]:
        await session.execute(
            text("CREATE TABLE IF NOT EXISTS session_probe (id INTEGER PRIMARY KEY, note TEXT)")
        )
        await session.commit()
        return {"created": True}

    @app.post("/session/insert")
    async def session_insert(
        session: Annotated[AsyncSession, Depends(get_session)],
        fail: bool = False,
    ) -> dict[str, bool]:
        await session.execute(
            text("INSERT INTO session_probe (id, note) VALUES (1, 'x')")
        )
        if fail:
            raise AppError(ErrorCode.INVALID_PARAMETER, "故意失败以验证回滚")
        return {"inserted": True}

    @app.get("/session/count")
    async def session_count(
        session: Annotated[AsyncSession, Depends(get_session)],
    ) -> dict[str, int]:
        result = await session.execute(text("SELECT COUNT(*) FROM session_probe"))
        return {"count": int(result.scalar_one())}

    return app


# ----------------------------------------------------------------------
# 提取契约：四渠道、优先级、原始值
# ----------------------------------------------------------------------


@pytest.mark.parametrize(
    ("headers", "params", "cookies", "expected"),
    [
        ({}, None, None, None),
        ({"Authorization": f"Bearer {TOKEN}"}, None, None, ("bearer", TOKEN)),
        ({"X-Token": TOKEN}, None, None, ("x_token", TOKEN)),
        ({}, {"token": TOKEN}, None, ("query", TOKEN)),
        ({}, None, {COOKIE_NAME: TOKEN}, ("cookie", TOKEN)),
        # 优先级：高优先级渠道存在（哪怕是坏值）就停在那里，绝不回退。
        (
            {"Authorization": f"Bearer {TOKEN}", "X-Token": "wrong"},
            None,
            None,
            ("bearer", TOKEN),
        ),
        ({"X-Token": TOKEN}, {"token": "wrong"}, None, ("x_token", TOKEN)),
        ({}, {"token": TOKEN}, {COOKIE_NAME: "wrong"}, ("query", TOKEN)),
        # 非 Bearer 形式占住 bearer 渠道，值是整条原始头。
        ({"Authorization": f"Basic {TOKEN}"}, None, None, ("bearer", f"Basic {TOKEN}")),
        ({"Authorization": f"Basic {TOKEN}"}, {"token": TOKEN}, None, ("bearer", f"Basic {TOKEN}")),
        # Bearer 但凭据为空：算命中（值为空串），不回退。
        ({"Authorization": "Bearer"}, {"token": TOKEN}, None, ("bearer", "")),
        # 空 Authorization 头按「没有值」处理，继续往下取。
        ({"Authorization": "   ", "X-Token": TOKEN}, None, None, ("x_token", TOKEN)),
    ],
)
def test_extract_token_contract(
    headers: dict[str, str],
    params: dict[str, str] | None,
    cookies: dict[str, str] | None,
    expected: tuple[str, str] | None,
    make_client,
) -> None:
    client = make_client(build_probe_app())
    body = client.get("/probe", headers=headers, params=params, cookies=cookies).json()
    if expected is None:
        assert (body["channel"], body["value"]) == (None, None)
    else:
        assert (body["channel"], body["value"]) == expected


def test_token_hit_is_immutable_and_unpackable() -> None:
    """TokenHit 是 NamedTuple：M4 的 WS 握手要能按渠道取值、也能直接解包。"""
    hit = deps.TokenHit(TokenChannel.QUERY, TOKEN)
    channel, value = hit
    assert (channel, value) == (TokenChannel.QUERY, TOKEN)
    assert channel == "query"  # StrEnum：字符串常量与枚举是同一个值
    with pytest.raises(AttributeError):
        hit.value = "x"  # type: ignore[misc]


# ----------------------------------------------------------------------
# 四渠道各自成功
# ----------------------------------------------------------------------


@pytest.mark.parametrize("tmp_settings", [TOKEN], indirect=True)
def test_four_channels_each_succeed(tmp_settings, make_client) -> None:
    client = make_client(build_guarded_app())
    assert auth_enabled() is True
    cases = (
        {"headers": {"Authorization": f"Bearer {TOKEN}"}},
        {"headers": {"X-Token": TOKEN}},
        {"params": {"token": TOKEN}},
        {"cookies": {COOKIE_NAME: TOKEN}},
    )
    for kwargs in cases:
        resp = client.get("/api/ping", **kwargs)
        assert resp.status_code == 200, (kwargs, resp.text)
        assert resp.json() == {"ok": True}


@pytest.mark.parametrize("tmp_settings", ["秘密令牌"], indirect=True)
def test_non_ascii_token_matches_without_type_error(tmp_settings, make_client) -> None:
    """token 含非 ASCII 时不能把原始 str 喂给 hmac.compare_digest（会 TypeError）。"""
    assert token_matches("秘密令牌") is True
    assert token_matches("秘密令牌x") is False
    assert token_matches("wrong") is False
    client = make_client(build_guarded_app())
    # httpx 拒绝把非 ASCII 值放进请求头，所以端到端只走 query 渠道（URL 编码）。
    assert client.get("/api/ping", params={"token": "秘密令牌"}).status_code == 200
    assert client.get("/api/ping", params={"token": "wrong"}).status_code == 401


# ----------------------------------------------------------------------
# 优先级：取第一个存在的渠道，不回退
# ----------------------------------------------------------------------


@pytest.mark.parametrize("tmp_settings", [TOKEN], indirect=True)
def test_priority_bearer_over_x_token(tmp_settings, make_client) -> None:
    client = make_client(build_guarded_app())
    # 正确的 Bearer 压过错误的 X-Token。
    ok = client.get(
        "/api/ping", headers={"Authorization": f"Bearer {TOKEN}", "X-Token": "wrong"}
    )
    assert ok.status_code == 200
    # 错误的 Bearer 不会被正确的 X-Token 掩盖。
    bad = client.get(
        "/api/ping", headers={"Authorization": "Bearer wrong", "X-Token": TOKEN}
    )
    assert bad.status_code == 401
    assert bad.json()["error"]["code"] == "UNAUTHORIZED"


@pytest.mark.parametrize("tmp_settings", [TOKEN], indirect=True)
def test_priority_does_not_fall_back_to_query(tmp_settings, make_client) -> None:
    client = make_client(build_guarded_app())
    # 错误的 Authorization 头 + 正确的 ?token= → 401（核心安全断言）。
    resp = client.get(
        "/api/ping", params={"token": TOKEN}, headers={"Authorization": "Bearer wrong"}
    )
    assert resp.status_code == 401
    # 格式错误的 Authorization 头同样压过正确的 query。
    resp = client.get(
        "/api/ping", params={"token": TOKEN}, headers={"Authorization": f"Basic {TOKEN}"}
    )
    assert resp.status_code == 401
    # X-Token 错误 + query 正确 → 401。
    resp = client.get("/api/ping", params={"token": TOKEN}, headers={"X-Token": "wrong"})
    assert resp.status_code == 401
    # query 错误 + cookie 正确 → 401。
    resp = client.get(
        "/api/ping", params={"token": "wrong"}, cookies={COOKIE_NAME: TOKEN}
    )
    assert resp.status_code == 401


@pytest.mark.parametrize("tmp_settings", [TOKEN], indirect=True)
def test_priority_channel_is_reported_in_details(tmp_settings, make_client) -> None:
    client = make_client(build_guarded_app())
    resp = client.get("/api/ping", headers={"X-Token": "wrong"})
    assert resp.status_code == 401
    assert resp.json()["error"]["details"] == {"channel": "x_token"}


# ----------------------------------------------------------------------
# cookie 渠道收窄：只读放行，写方法 403
# ----------------------------------------------------------------------


@pytest.mark.parametrize("tmp_settings", [TOKEN], indirect=True)
def test_cookie_only_read_allowed(tmp_settings, make_client) -> None:
    client = make_client(build_guarded_app())
    assert client.get("/api/ping", cookies={COOKIE_NAME: TOKEN}).status_code == 200
    assert client.head("/api/ping", cookies={COOKIE_NAME: TOKEN}).status_code == 200


@pytest.mark.parametrize("method", ["POST", "PUT", "PATCH", "DELETE"])
@pytest.mark.parametrize("tmp_settings", [TOKEN], indirect=True)
def test_cookie_only_write_is_forbidden(method, tmp_settings, make_client) -> None:
    client = make_client(build_guarded_app())
    resp = client.request(method, "/api/ping", cookies={COOKIE_NAME: TOKEN})
    assert resp.status_code == 403
    body = resp.json()
    assert body["error"]["code"] == "FORBIDDEN"
    assert body["error"]["details"]["channel"] == "cookie"
    assert body["error"]["details"]["method"] == method


@pytest.mark.parametrize("tmp_settings", [TOKEN], indirect=True)
def test_cookie_write_with_wrong_token_is_401(tmp_settings, make_client) -> None:
    """先判 token 再判渠道：cookie 里的 token 无效时是 401（缺有效凭据），不是 403。"""
    client = make_client(build_guarded_app())
    resp = client.post("/api/ping", cookies={COOKIE_NAME: "wrong"})
    assert resp.status_code == 401
    assert resp.json()["error"]["code"] == "UNAUTHORIZED"


@pytest.mark.parametrize("tmp_settings", [TOKEN], indirect=True)
def test_explicit_header_allows_write(tmp_settings, make_client) -> None:
    """写操作显式带凭据（header / query）不受 cookie 收窄影响。"""
    client = make_client(build_guarded_app())
    assert client.post("/api/ping", headers={"X-Token": TOKEN}).status_code == 200
    assert client.delete("/api/ping", params={"token": TOKEN}).status_code == 200


# ----------------------------------------------------------------------
# 缺失 / 错误 token
# ----------------------------------------------------------------------


@pytest.mark.parametrize("tmp_settings", [TOKEN], indirect=True)
def test_missing_token_is_401_with_www_authenticate(tmp_settings, make_client) -> None:
    client = make_client(build_guarded_app())
    resp = client.get("/api/ping")
    assert resp.status_code == 401
    body = resp.json()
    assert body["error"]["code"] == "UNAUTHORIZED"
    assert body["error"]["message"] == "缺少 access token"
    assert "details" not in body["error"]
    # WWW-Authenticate 由 M3-04 的处理器统一补（docs/05 §2）。
    assert resp.headers["WWW-Authenticate"] == "Bearer"


@pytest.mark.parametrize("tmp_settings", [TOKEN], indirect=True)
def test_wrong_token_is_401(tmp_settings, make_client) -> None:
    client = make_client(build_guarded_app())
    for kwargs in (
        {"headers": {"Authorization": "Bearer wrong"}},
        {"headers": {"X-Token": "wrong"}},
        {"params": {"token": "wrong"}},
        {"cookies": {COOKIE_NAME: "wrong"}},
    ):
        resp = client.get("/api/ping", **kwargs)
        assert resp.status_code == 401, (kwargs, resp.text)
        assert resp.json()["error"]["message"] == "access token 不匹配"


# ----------------------------------------------------------------------
# 免鉴权模式（docs/05 §5.2）
# ----------------------------------------------------------------------


def test_disabled_mode_allows_everything(tmp_settings, make_client) -> None:
    client = make_client(build_guarded_app())
    assert tmp_settings.access_token == ""
    assert auth_enabled() is False
    # 不带 token、带错误 token、cookie-only 的写操作，全部放行。
    assert client.get("/api/ping").status_code == 200
    assert client.get("/api/ping", headers={"Authorization": "Bearer wrong"}).status_code == 200
    assert client.get("/api/ping", headers={"X-Token": "wrong"}).status_code == 200
    assert client.post("/api/ping", cookies={COOKIE_NAME: "wrong"}).status_code == 200
    # 提取照常执行（免鉴权模式不改渠道语义，M4 的握手判定可复用同一条路径）。
    body = client.get("/api/whoami", headers={"Authorization": "Basic x"}).json()
    assert (body["channel"], body["value"]) == ("bearer", "Basic x")


def test_disabled_mode_does_not_count_failures(tmp_settings, make_client, strict_limiter, clock) -> None:
    """免鉴权模式下带错误 token 既不 401 也不进失败计数。"""
    client = make_client(build_guarded_app())
    for _ in range(10):
        assert client.get("/api/ping", headers={"X-Token": "wrong"}).status_code == 200
    assert client.get("/api/ping", headers={"X-Token": "wrong"}).status_code == 200


def test_token_matches_is_true_when_auth_disabled(tmp_settings) -> None:
    """未配置 token 时 token_matches 恒 True（§5.2），M4 复用时不需重复判 auth_enabled。"""
    assert token_matches("") is True
    assert token_matches("anything") is True


# ----------------------------------------------------------------------
# 豁免清单（docs/05 §5.3、§6.17）
# ----------------------------------------------------------------------


@pytest.mark.parametrize("path", EXEMPT_PATHS)
def test_is_exempt_path_true(path: str) -> None:
    assert is_exempt_path(path) is True


@pytest.mark.parametrize("path", PROTECTED_PATHS)
def test_is_exempt_path_false(path: str) -> None:
    assert is_exempt_path(path) is False


def test_exempt_path_prefixes_cover_docs_05_section_5_3() -> None:
    """清单本身要含 5.3 的全部条目（SPA catch-all 由命名空间规则表达）。"""
    assert "/api/system/health" in EXEMPT_PATH_PREFIXES
    assert "/docs" in EXEMPT_PATH_PREFIXES
    assert "/redoc" in EXEMPT_PATH_PREFIXES
    assert "/openapi.json" in EXEMPT_PATH_PREFIXES
    assert "/static/" in EXEMPT_PATH_PREFIXES
    assert "/manifest.webmanifest" in EXEMPT_PATH_PREFIXES
    assert "/sw.js" in EXEMPT_PATH_PREFIXES
    # "/" 绝不能进清单：它会豁免一切，SPA catch-all 改由 SPA_EXCLUDED_PREFIXES 表达。
    assert "/" not in EXEMPT_PATH_PREFIXES
    assert deps.SPA_EXCLUDED_PREFIXES == ("/api", "/mcp", "/static")


@pytest.mark.parametrize("tmp_settings", [TOKEN], indirect=True)
@pytest.mark.parametrize("path", EXEMPT_PATHS)
def test_exempt_paths_pass_with_token_configured(path, tmp_settings, make_client) -> None:
    client = make_client(build_guarded_app())
    resp = client.get(path)
    assert resp.status_code == 200, (path, resp.text)


@pytest.mark.parametrize("tmp_settings", [TOKEN], indirect=True)
@pytest.mark.parametrize("path", PROTECTED_PATHS)
def test_protected_namespaces_are_not_exempt(path, tmp_settings, make_client) -> None:
    client = make_client(build_guarded_app())
    resp = client.get(path)
    assert resp.status_code == 401, (path, resp.text)


@pytest.mark.parametrize("tmp_settings", [TOKEN], indirect=True)
def test_health_reports_auth_enabled(tmp_settings, make_client) -> None:
    client = make_client(build_guarded_app())
    resp = client.get("/api/system/health")
    assert resp.status_code == 200
    assert resp.json() == {"auth_enabled": True}


# ----------------------------------------------------------------------
# 失败限流（docs/05 §4.1）
# ----------------------------------------------------------------------


def _fail(client, times: int) -> list[int]:
    """连续发 ``times`` 次错误 token 的请求，返回每次的状态码。"""
    return [
        client.get("/api/ping", headers={"X-Token": "wrong"}).status_code
        for _ in range(times)
    ]


@pytest.mark.parametrize("tmp_settings", [TOKEN], indirect=True)
def test_threshold_reached_returns_429_with_retry_after(
    tmp_settings, make_client, strict_limiter, clock
) -> None:
    client = make_client(build_guarded_app())
    assert _fail(client, 2) == [401, 401]
    tripped = client.get("/api/ping", headers={"X-Token": "wrong"})
    assert tripped.status_code == 429
    assert tripped.json()["error"]["code"] == "RATE_LIMITED"
    retry_after = tripped.headers["Retry-After"]
    assert retry_after.isdigit()
    assert 1 <= int(retry_after) <= 60

    # 冷却期内即使带正确 token 也拒绝，Retry-After 是剩余秒数（随时间递减）。
    clock[0] += 10
    blocked = client.get("/api/ping", headers={"X-Token": TOKEN})
    assert blocked.status_code == 429
    assert int(blocked.headers["Retry-After"]) == 50


@pytest.mark.parametrize("tmp_settings", [TOKEN], indirect=True)
def test_cooldown_expiry_restores_and_resets(
    tmp_settings, make_client, strict_limiter, clock
) -> None:
    client = make_client(build_guarded_app())
    assert _fail(client, 3)[-1] == 429
    clock[0] += 60.0  # 冷却结束
    assert client.get("/api/ping", headers={"X-Token": TOKEN}).status_code == 200
    # 计数已清零：再连续失败两次仍是 401，而不是立刻 429。
    assert _fail(client, 2) == [401, 401]


@pytest.mark.parametrize("tmp_settings", [TOKEN], indirect=True)
def test_success_clears_failure_counter(
    tmp_settings, make_client, strict_limiter, clock
) -> None:
    client = make_client(build_guarded_app())
    assert _fail(client, 2) == [401, 401]
    assert client.get("/api/ping", headers={"X-Token": TOKEN}).status_code == 200
    # 成功鉴权清零「连续失败」计数。
    assert _fail(client, 2) == [401, 401]


@pytest.mark.parametrize("tmp_settings", [TOKEN], indirect=True)
def test_failure_window_expiry_resets_count(
    tmp_settings, make_client, strict_limiter, clock
) -> None:
    client = make_client(build_guarded_app())
    assert _fail(client, 2) == [401, 401]
    clock[0] += 61.0  # 超出 60 秒窗口，旧失败不再计入
    assert _fail(client, 2) == [401, 401]
    assert _fail(client, 1) == [429]  # 新窗口内的第 3 次


@pytest.mark.parametrize("tmp_settings", [TOKEN], indirect=True)
def test_forbidden_cookie_write_not_counted_as_auth_failure(
    tmp_settings, make_client, strict_limiter, clock
) -> None:
    """403 是渠道策略拒绝，不是鉴权失败：连发 4 次也不该触发 429。"""
    client = make_client(build_guarded_app())
    for _ in range(4):
        assert client.post("/api/ping", cookies={COOKIE_NAME: TOKEN}).status_code == 403
    assert _fail(client, 1) == [401]


@pytest.mark.parametrize("tmp_settings", [TOKEN], indirect=True)
def test_reset_rate_limiter_clears_state(
    tmp_settings, make_client, strict_limiter, clock
) -> None:
    """进程内状态可整体清空（测试与进程内重启用）。"""
    client = make_client(build_guarded_app())
    assert _fail(client, 3)[-1] == 429
    deps.reset_rate_limiter()
    assert _fail(client, 1) == [401]  # 冷却与计数一并清空


# ----------------------------------------------------------------------
# get_session：绑定、回滚、不隐式提交（M2-13 教训）
# ----------------------------------------------------------------------


def test_get_session_binds_to_replaced_module_factory(
    isolated_db, tmp_path, make_client
) -> None:
    """替换模块属性后必须拿到 tmp 库会话；import 期缓存绑定会被这条用例抓住。"""
    client = make_client(build_session_app())
    resp = client.get("/session/url")
    assert resp.status_code == 200, resp.text
    url = resp.json()["url"]
    assert url == str(isolated_db.url)
    assert str(tmp_path) in url
    assert "resource/maa_api.db" not in url


def test_get_session_rolls_back_on_error(isolated_db, make_client, monkeypatch) -> None:
    client = make_client(build_session_app())
    assert client.post("/session/table").status_code == 200

    rollbacks: list[str] = []
    original = AsyncSession.rollback

    async def spy(self) -> None:
        rollbacks.append("rollback")
        await original(self)

    monkeypatch.setattr(AsyncSession, "rollback", spy)
    failed = client.post("/session/insert", params={"fail": "true"})
    assert failed.status_code == 400
    assert failed.json()["error"]["code"] == "INVALID_PARAMETER"
    assert rollbacks, "路由抛异常时 get_session 必须 rollback"
    assert client.get("/session/count").json() == {"count": 0}


def test_get_session_does_not_commit(isolated_db, make_client, monkeypatch) -> None:
    client = make_client(build_session_app())
    assert client.post("/session/table").status_code == 200

    commits: list[str] = []
    original = AsyncSession.commit

    async def spy(self) -> None:
        commits.append("commit")
        await original(self)

    monkeypatch.setattr(AsyncSession, "commit", spy)
    assert client.post("/session/insert").status_code == 200
    assert commits == [], "get_session 不得隐式 commit（事务边界归调用方）"
    # 未提交的写入在会话关闭时被丢弃。
    assert client.get("/session/count").json() == {"count": 0}


def test_session_factory_module_attribute_is_read_per_call(
    isolated_db, monkeypatch, make_client
) -> None:
    """每次调用都读模块属性：换掉 factory 后新会话立刻跟着换（不做 import 期绑定）。"""
    seen: list[object] = []
    original = db_session.session_factory

    def recording_factory(*args, **kwargs):
        seen.append(original)
        return original(*args, **kwargs)

    monkeypatch.setattr(db_session, "session_factory", recording_factory)
    client = make_client(build_session_app())
    assert client.get("/session/url").status_code == 200
    assert seen, "get_session 没有在调用时读取 db_session.session_factory"
