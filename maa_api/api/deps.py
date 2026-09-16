"""依赖注入：四渠道 token 鉴权与会话依赖（docs/05 §5、docs/02 §6）。

鉴权契约（docs/05 §5.1）
========================

单一 ``access_token``，四个渠道按固定优先级取**第一个非空值**即停止，
**不做回退尝试**：

1. ``Authorization: Bearer <token>``（渠道 :attr:`TokenChannel.BEARER`）
2. ``X-Token: <token>``（:attr:`TokenChannel.X_TOKEN`）
3. ``?token=<token>``（:attr:`TokenChannel.QUERY`）
4. cookie ``maa_token=<token>``（:attr:`TokenChannel.COOKIE`）

``Authorization`` 头存在但值不是 Bearer 形式（或 Bearer 的凭据为空）时**同样视为
命中 bearer 渠道**（返回整条原始头作为值，必然不匹配），不会回退去看 query 参数
—— 否则攻击者可以用正确的 query 掩盖错误的头，日志会记混。只有 ``Authorization:``
这样的**空头**按「没有值」处理、继续往下取，与「取第一个非空值」的字面规则一致。

cookie 渠道收窄（docs/05 §5.1、docs/13 §3）：仅凭 cookie 的请求只允许 ``GET`` /
``HEAD`` / WebSocket 升级，写方法（``POST`` / ``PUT`` / ``PATCH`` / ``DELETE``）
返回 403 ``FORBIDDEN``。判定顺序是**先校验 token、再判渠道**：cookie 里的 token
无效时返回 401 —— RFC 9110 的 401 是「缺少有效凭据」、403 是「凭据有效但被策略
拒绝」，docs/05 §4.1 也把 ``FORBIDDEN`` 定义为「身份有效但动作被策略拒绝」。

免鉴权模式（docs/05 §5.2）：``access_token`` 为空时全部端点免鉴权，
``auth_enabled()`` 为 False 时**不因为带了错误 token 而拒绝**；``extract_token()``
仍然照常执行，保证「先提取、后判定」的语义在两种模式下一致。

豁免清单（docs/05 §5.3）：见 :data:`EXEMPT_PATH_PREFIXES` 与 :func:`is_exempt_path`。
``/mcp`` 不豁免。

失败限流（docs/05 §4.1）
========================

同一来源 IP 的鉴权失败（401）计数达到 :data:`FAILURE_THRESHOLD`（默认 10）次后进入
:data:`COOLDOWN_SECONDS`（默认 60 秒）冷却期，期间该 IP 对非豁免端点的请求一律
429 ``RATE_LIMITED`` 并带 ``Retry-After``（剩余冷却秒数，整数）。冷却结束后计数
清零、恢复判定。

- 计数窗口 :data:`FAILURE_WINDOW_SECONDS`（默认 60 秒）：更早的失败会被裁掉，
  即「10 次/分钟」而不是「累计 10 次」。
- **成功鉴权清零**该 IP 的失败计数（「连续失败」语义）。
- cookie-only 写操作的 403 是渠道策略拒绝、不是鉴权失败，**不计入**。
- 计数器是进程内的 ``dict[ip, deque[timestamp]]``，用模块级锁保护（FastAPI 的
  同步依赖与线程池都可能并发进入）。每个 IP 的 deque 在记录时按窗口裁剪（长度
  不超过阈值），IP 总数超过 :data:`MAX_TRACKED_IP` 时做一次全表清理、丢弃最旧的
  条目，避免被大量伪造来源 IP 撑爆内存。
- 来源 IP 取 ``request.client.host``；部署在反向代理（docs/13 的 Tailscale Serve）
  后面时由部署层开 ``--proxy-headers`` 还原真实 IP。**不要**在这里信任
  ``X-Forwarded-For``：那等于给限流器一个客户端可随意伪造的键。
- 时钟走 :func:`_now`（``time.monotonic``），是给测试 monkeypatch 用的接缝。

429 用 ``AppError(ErrorCode.RATE_LIMITED, ..., headers={"Retry-After": ...})`` 抛出。
M3-11 起 ``AppError`` 能携带响应头、``api/errors.py`` 的处理器原样透传，所以这里不再
需要 M3-06 当时的绕行（``StarletteHTTPException(429, headers=...)``，缺陷见
``.refactor/DEFECTS.md`` 的 M3-04 条目，已随本卡修复）。取值一律是剩余冷却秒数
（整数），与 :data:`COOLDOWN_SECONDS` 同口径；响应体仍由处理器渲染成统一错误体，
``code`` 是 ``RATE_LIMITED``。
"""

from __future__ import annotations

import hmac
import math
import threading
import time
from collections import deque
from collections.abc import AsyncIterator
from enum import StrEnum
from typing import NamedTuple

from fastapi import Request, WebSocket
from sqlalchemy.ext.asyncio import AsyncSession

import maa_api.db.session as db_session
from maa_api.domain.errors import AppError, ErrorCode
from maa_api.settings import get_settings

__all__ = [
    "COOKIE_NAME",
    "COOLDOWN_SECONDS",
    "EXEMPT_PATH_PREFIXES",
    "FAILURE_THRESHOLD",
    "FAILURE_WINDOW_SECONDS",
    "MAX_TRACKED_IP",
    "SPA_EXCLUDED_PREFIXES",
    "WRITE_METHODS",
    "TokenChannel",
    "TokenHit",
    "auth_enabled",
    "extract_token",
    "get_session",
    "is_exempt_path",
    "require_auth",
    "reset_rate_limiter",
    "token_matches",
]

# ----------------------------------------------------------------------
# 渠道与常量
# ----------------------------------------------------------------------


class TokenChannel(StrEnum):
    """token 来源渠道（docs/05 §5.1 的固定优先级顺序即下面的定义顺序）。

    取值就是四个字符串常量 ``bearer`` / ``x_token`` / ``query`` / ``cookie``，
    同时是 ``str`` 子类，M4 的 WebSocket 握手可以直接比较字符串。
    """

    BEARER = "bearer"
    X_TOKEN = "x_token"
    QUERY = "query"
    COOKIE = "cookie"


class TokenHit(NamedTuple):
    """一次提取的结果：命中的渠道 + **原始**值（未做任何匹配判定）。"""

    channel: TokenChannel
    value: str


#: 头名与 query 参数名（docs/05 §5.1 的表格）；Starlette 的头查找大小写不敏感。
AUTHORIZATION_HEADER = "authorization"
X_TOKEN_HEADER = "x-token"
TOKEN_QUERY_PARAM = "token"
#: cookie 名固定（docs/05 §5.1），前端登录接口与 M4 的 WS 握手都按它读写。
COOKIE_NAME = "maa_token"
#: Bearer 方案名按 RFC 7235 大小写不敏感。
BEARER_SCHEME = "bearer"

#: 仅凭 cookie 时不允许的写方法（docs/05 §5.1）；其余方法（GET/HEAD/OPTIONS）放行。
WRITE_METHODS = frozenset({"POST", "PUT", "PATCH", "DELETE"})

#: 即使配置了 token 也不校验的路径前缀（docs/05 §5.3），``/mcp`` 刻意不在其中。
#:
#: - 不带尾斜杠的条目按**路径段**匹配：``/docs`` 命中 ``/docs`` 与
#:   ``/docs/oauth2-redirect``，但不命中 ``/docsx``。
#: - 带尾斜杠的条目是目录前缀（``/static/`` 命中 ``/static/`` 下的一切）。
#: - **SPA catch-all 不是这里的一条字面前缀**：它覆盖的路径是「不属于
#:   :data:`SPA_EXCLUDED_PREFIXES` 的全部路径」（docs/05 §6.17），把 ``"/"`` 放进
#:   本元组会豁免一切。判定统一走 :func:`is_exempt_path`，不要自己拿本元组做
#:   ``startswith`` 循环。
EXEMPT_PATH_PREFIXES: tuple[str, ...] = (
    "/api/system/health",
    "/docs",
    "/redoc",
    "/openapi.json",
    "/static/",
    "/manifest.webmanifest",
    "/sw.js",
)

#: SPA catch-all 明确排除的三个命名空间（docs/05 §6.17）：这些前缀下的路径由 API
#: 或 MCP 接管，**不**享受前端兜底的豁免；其余路径一律由 SPA catch-all 服务，豁免。
SPA_EXCLUDED_PREFIXES: tuple[str, ...] = ("/api", "/mcp", "/static")

#: 同一 IP 在 :data:`FAILURE_WINDOW_SECONDS` 内的失败次数达到该值即进入冷却期。
FAILURE_THRESHOLD = 10
#: 失败计数窗口（秒）：更早的失败不计入「连续失败」。
FAILURE_WINDOW_SECONDS = 60.0
#: 冷却时长（秒）：即 ``Retry-After`` 的初值。
COOLDOWN_SECONDS = 60.0
#: 进程内最多跟踪多少个来源 IP，超出时丢最旧的条目（防内存膨胀）。
MAX_TRACKED_IP = 1024


# ----------------------------------------------------------------------
# 取值：四渠道与优先级
# ----------------------------------------------------------------------


def extract_token(request: Request | WebSocket) -> TokenHit | None:
    """按固定优先级提取 token（docs/05 §5.1），返回第一个非空值所在的渠道。

    :param request: HTTP 请求或 WebSocket 握手（M4 复用同一个函数；两者都有
        ``headers`` / ``query_params`` / ``cookies``）。
    :returns: :class:`TokenHit`；四个渠道都没有非空值时返回 ``None``。

    本函数**只做提取，不做任何匹配判定**：``Authorization`` 头存在但不是
    ``Bearer <token>`` 形式时返回整条原始头（渠道仍是 bearer），由
    :func:`require_auth` / M4 的握手判定层拒绝，绝不回退到低优先级渠道。
    值只做首尾空白裁剪（HTTP 头允许 OWS），不改动其它内容。
    """
    authorization = request.headers.get(AUTHORIZATION_HEADER)
    if authorization is not None and authorization.strip():
        raw = authorization.strip()
        scheme, _, credential = raw.partition(" ")
        # 非 Bearer 形式也占住 bearer 渠道：返回原始头，判定层必然判不匹配。
        value = credential.strip() if scheme.lower() == BEARER_SCHEME else raw
        return TokenHit(TokenChannel.BEARER, value)

    x_token = request.headers.get(X_TOKEN_HEADER)
    if x_token is not None and x_token.strip():
        return TokenHit(TokenChannel.X_TOKEN, x_token.strip())

    query_token = request.query_params.get(TOKEN_QUERY_PARAM)
    if query_token is not None and query_token.strip():
        return TokenHit(TokenChannel.QUERY, query_token.strip())

    cookie_token = request.cookies.get(COOKIE_NAME)
    if cookie_token is not None and cookie_token.strip():
        return TokenHit(TokenChannel.COOKIE, cookie_token.strip())

    return None


def auth_enabled() -> bool:
    """是否启用了鉴权：``settings.get_settings().access_token`` 非空（docs/05 §5.2）。

    每次调用都读 ``get_settings()``（进程内缓存由 :func:`~maa_api.settings.set_settings`
    替换），因此 M6 的热更新与测试注入都能立即生效。
    """
    return bool(get_settings().access_token)


def token_matches(candidate: str) -> bool:
    """候选 token 是否与配置的 ``access_token`` 匹配。

    用 :func:`hmac.compare_digest` 比较（时间侧信道免费避开），比较前统一编码成
    UTF-8 字节 —— ``compare_digest`` 对含非 ASCII 字符的 ``str`` 直接抛
    ``TypeError``，不能把客户端可控的字符串原样喂进去。

    未配置 ``access_token`` 时**恒为 True**（免鉴权模式，docs/05 §5.2），调用方
    不需要再判 :func:`auth_enabled`；M4 的 WebSocket 握手可直接复用。
    """
    expected = get_settings().access_token
    if not expected:
        return True
    return hmac.compare_digest(candidate.encode("utf-8"), expected.encode("utf-8"))


# ----------------------------------------------------------------------
# 豁免路径
# ----------------------------------------------------------------------


def _matches_prefix(path: str, prefix: str) -> bool:
    """路径是否命中前缀条目；不带尾斜杠的条目按路径段匹配（``/docs`` ≠ ``/docsx``）。"""
    if prefix.endswith("/"):
        return path.startswith(prefix)
    return path == prefix or path.startswith(prefix + "/")


def _in_namespace(path: str, prefixes: tuple[str, ...]) -> bool:
    """路径是否落在某个命名空间前缀下（按路径段，``/apix`` 不算 ``/api`` 下）。"""
    return any(_matches_prefix(path, prefix) for prefix in prefixes)


def is_exempt_path(path: str) -> bool:
    """路径是否豁免鉴权（docs/05 §5.3、§6.17）。

    两条规则任一成立即豁免：

    1. 命中 :data:`EXEMPT_PATH_PREFIXES` 的条目；
    2. **SPA catch-all**：不在 :data:`SPA_EXCLUDED_PREFIXES`（``/api``、``/mcp``、
       ``/static``）任一命名空间下 —— 这些路径由前端兜底返回 ``index.html``，
       用户得先看到页面才有地方填 token。
    """
    if not _in_namespace(path, SPA_EXCLUDED_PREFIXES):
        return True
    return any(_matches_prefix(path, prefix) for prefix in EXEMPT_PATH_PREFIXES)


# ----------------------------------------------------------------------
# 失败限流（进程内）
# ----------------------------------------------------------------------

#: key = 来源 IP，value = 窗口内的失败时刻（`_now()` 的读数）。
_failure_times: dict[str, deque[float]] = {}
#: key = 来源 IP，value = 冷却截止时刻；存在即处于冷却期。
_cooldown_until: dict[str, float] = {}
#: 保护上面两个字典。FastAPI 的依赖在线程池里并发执行，不能假设单线程。
_lock = threading.Lock()


def _now() -> float:
    """限流时钟（``time.monotonic``）。独立成函数便于测试替换。"""
    return time.monotonic()


def reset_rate_limiter() -> None:
    """清空全部失败计数与冷却状态（测试与进程内重启用）。"""
    with _lock:
        _failure_times.clear()
        _cooldown_until.clear()


def _client_ip(request: Request) -> str:
    """来源 IP；拿不到 client 时用 ``"unknown"`` 兜底（所有无来源请求共用一桶）。"""
    client = request.client
    return client.host if client is not None else "unknown"


def _rate_limited(retry_after: int) -> AppError:
    """429 ``RATE_LIMITED`` + ``Retry-After``（剩余冷却秒数，整数）。

    走 ``AppError`` 统一通道（M3-11）：处理器按 ``ERROR_HTTP_STATUS`` 取 429、渲染
    统一错误体，并把 ``headers`` 原样写进响应。
    """
    return AppError(
        ErrorCode.RATE_LIMITED,
        f"鉴权失败次数过多，请在 {retry_after} 秒后重试",
        headers={"Retry-After": str(retry_after)},
    )


def _raise_if_cooling_down(ip: str) -> None:
    """冷却期内一律 429（即使这次带的是正确 token）；冷却结束则顺手清空该 IP 计数。"""
    now = _now()
    with _lock:
        until = _cooldown_until.get(ip)
        if until is None:
            return
        if now >= until:
            _cooldown_until.pop(ip, None)
            _failure_times.pop(ip, None)
            return
        retry_after = max(1, math.ceil(until - now))
    raise _rate_limited(retry_after)


def _prune_locked(now: float) -> None:
    """IP 总数超上限时的清理；调用方必须已持 :data:`_lock`。

    先清掉冷却已结束的 IP（它们的计数已经无意义），仍然超限再按「最新一次失败」
    排序丢弃最旧的条目。每个 IP 的 deque 由 :func:`_record_failure` 按窗口裁剪，
    长度天然不超过阈值，所以内存上界是 ``MAX_TRACKED_IP * 阈值`` 个时间戳。
    """
    for ip in [key for key, until in _cooldown_until.items() if now >= until]:
        _cooldown_until.pop(ip, None)
        _failure_times.pop(ip, None)

    overflow = len(_failure_times) - MAX_TRACKED_IP
    if overflow <= 0:
        return
    ordered = sorted(
        _failure_times.items(), key=lambda item: item[1][-1] if item[1] else 0.0
    )
    for ip, _ in ordered[:overflow]:
        _failure_times.pop(ip, None)
        _cooldown_until.pop(ip, None)


def _record_failure(ip: str) -> None:
    """记一次鉴权失败；本次即达阈值时进入冷却并抛 429（把调用方的 401 顶掉）。

    设计成「达阈值的那次请求直接得到 429」而不是「第 11 次才 429」：docs/05 §4.1
    的语义是失败超过阈值即进入冷却期，这一刻起该 IP 已经在冷却期内了。
    """
    now = _now()
    retry_after: int | None = None
    with _lock:
        times = _failure_times.setdefault(ip, deque())
        cutoff = now - FAILURE_WINDOW_SECONDS
        while times and times[0] <= cutoff:
            times.popleft()
        times.append(now)
        if len(times) >= FAILURE_THRESHOLD:
            _cooldown_until[ip] = now + COOLDOWN_SECONDS
            retry_after = max(1, math.ceil(COOLDOWN_SECONDS))
        _prune_locked(now)
    if retry_after is not None:
        raise _rate_limited(retry_after)


def _clear_failures(ip: str) -> None:
    """成功鉴权后清零该 IP 的失败计数（「连续失败」语义）。"""
    with _lock:
        _failure_times.pop(ip, None)
        _cooldown_until.pop(ip, None)


# ----------------------------------------------------------------------
# FastAPI 依赖
# ----------------------------------------------------------------------


async def require_auth(request: Request) -> None:
    """FastAPI 鉴权依赖：直接 ``Depends(require_auth)``。

    判定顺序（短路，先到先返回）：

    1. 豁免路径（:func:`is_exempt_path`）→ 放行；
    2. 免鉴权模式（:func:`auth_enabled` 为 False）→ 放行，且**不因为带了错误
       token 而拒绝**（docs/05 §5.2）；提取照常执行以保证语义一致；
    3. 处于失败冷却期 → 429 ``RATE_LIMITED`` + ``Retry-After``；
    4. 四个渠道都没有 token → 记一次失败并抛 401 ``UNAUTHORIZED``；
    5. token 不匹配 → 记一次失败并抛 401 ``UNAUTHORIZED``；
    6. 只命中 cookie 渠道且是写方法 → 403 ``FORBIDDEN``（**不计**失败）；
    7. 匹配且渠道允许 → 清零该 IP 的失败计数。

    401 的 ``WWW-Authenticate: Bearer`` 由 M3-04 的处理器统一补，本函数不管。
    """
    if is_exempt_path(request.url.path):
        return

    hit = extract_token(request)

    if not auth_enabled():
        return

    ip = _client_ip(request)
    _raise_if_cooling_down(ip)

    if hit is None:
        _record_failure(ip)
        raise AppError(ErrorCode.UNAUTHORIZED, "缺少 access token")

    if not token_matches(hit.value):
        _record_failure(ip)
        raise AppError(
            ErrorCode.UNAUTHORIZED,
            "access token 不匹配",
            {"channel": hit.channel.value},
        )

    if hit.channel is TokenChannel.COOKIE and request.method.upper() in WRITE_METHODS:
        raise AppError(
            ErrorCode.FORBIDDEN,
            "仅凭 cookie 的请求不允许写操作，请改用 Authorization 或 X-Token",
            {"channel": hit.channel.value, "method": request.method.upper()},
        )

    _clear_failures(ip)


async def get_session() -> AsyncIterator[AsyncSession]:
    """FastAPI 会话依赖：从 ``maa_api.db.session.session_factory`` 开一个会话。

    - ``session_factory`` 在**每次调用时**重新读模块属性，不做 import 期绑定：
      M2-13 实测，测试替换模块级 ``engine`` / ``session_factory`` 后，import 期
      缓存的对象仍指向真实 ``resource/maa_api.db``（SQLite 连库即建文件）。
    - 异常时 ``rollback()`` 后原样抛出；正常结束与异常路径都在 ``finally`` 里
      ``close()``。
    - **不隐式 commit**：事务边界由调用方（路由/服务）决定，仓储层纪律见 M2 系列
      实测。依赖退出时未提交的写入会被丢弃（SQLAlchemy 关闭时会回滚活动事务）。
    """
    session = db_session.session_factory()
    try:
        yield session
    except Exception:
        await session.rollback()
        raise
    finally:
        await session.close()
