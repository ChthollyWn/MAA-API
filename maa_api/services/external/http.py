"""出站 HTTP 客户端（规格 §3.2）：白名单、限流、重试、熔断、缓存与状态。

**唯一出口**：所有对外请求都走 :class:`ExternalClient`。工具与领域层拿不到原始 URL，也
不能传入 URL——URL 只能由 :mod:`maa_api.services.external.sources` 的登记表与参数字典拼装。

日志只记来源、端点、状态码、耗时、字节数与是否命中缓存，**不记响应正文**（外部内容既不
进业务表也不进日志，见规格 §3.6 第 5 条）。
"""

from __future__ import annotations

import asyncio
import json
import logging
import random
import time
from dataclasses import dataclass
from datetime import datetime
from typing import Any, Awaitable, Callable, Mapping

import httpx

from maa_api.domain.errors import AppError, ErrorCode
from maa_api.services.external.cache import CacheStore, cache_key
from maa_api.services.external.sources import (
    CACHE_LEVELS,
    ResolvedSource,
    resolve_source,
    validate_host,
)
from maa_api.services.external.status import SourceStatusRegistry
from maa_api.settings import Settings, get_settings

__all__ = [
    "ExternalClient",
    "ExternalResult",
    "USER_AGENT",
    "default_external_client",
    "reset_default_external_client",
]

logger = logging.getLogger(__name__)

#: 读不到分发元数据时的回退版本（与 `pyproject.toml`、`system.health` 的取法一致）。
_FALLBACK_VERSION = "0.1.0"


def _app_version() -> str:
    """应用版本；只读 `importlib.metadata`，不 import 主模块（避免循环依赖）。"""
    try:
        from importlib.metadata import PackageNotFoundError
        from importlib.metadata import version as distribution_version

        try:
            return distribution_version("MAA-API")
        except PackageNotFoundError:
            return _FALLBACK_VERSION
    except Exception:  # noqa: BLE001 - 版本只影响 UA，取不到不能拖垮出站
        return _FALLBACK_VERSION


#: robots.txt 要求标明身份（S1）。
USER_AGENT = f"MAA-API/{_app_version()} (+https://github.com/ChthollyWn/MAA-API)"

#: 单次请求的总时限 = 读取超时 + 这个余量（规格 §3.2）。
TOTAL_TIMEOUT_MARGIN = 15.0

#: 429 冷却：缺省 60 秒，封顶 300 秒。
RETRY_AFTER_DEFAULT = 60.0
RETRY_AFTER_MAX = 300.0

#: 重试间隔（秒）与抖动上限。
RETRY_DELAY = 1.0
RETRY_JITTER = 0.5

#: 等令牌最多等多久（秒），超过就报限流（规格 §3.2）。
TOKEN_WAIT_SECONDS = 5.0

#: 只对连接错误与这三个状态码重试一次。
RETRY_STATUSES = frozenset({502, 503, 504})


@dataclass(frozen=True, slots=True)
class ExternalResult:
    """一次取数的结果与元信息。``cached``/``stale`` 进来源信封（§3.4）。"""

    kind: str
    endpoint: str
    url: str
    data: Any
    fetched_at: datetime
    cached: bool
    stale: bool


class _TokenBucket:
    """每来源一个令牌桶：容量与每分钟上限一致，按时间线性回填。"""

    def __init__(self, rate_per_minute: int, *, clock: Callable[[], float]) -> None:
        self.rate = max(int(rate_per_minute), 1)
        self._clock = clock
        self._tokens = float(self.rate)
        self._updated = clock()

    def _refill(self) -> None:
        now = self._clock()
        elapsed = max(now - self._updated, 0.0)
        self._tokens = min(float(self.rate), self._tokens + elapsed * self.rate / 60.0)
        self._updated = now

    def try_take(self) -> float:
        """取一个令牌；取到返回 0，取不到返回需要等待的秒数。"""
        self._refill()
        if self._tokens >= 1.0:
            self._tokens -= 1.0
            return 0.0
        return (1.0 - self._tokens) * 60.0 / self.rate


class ExternalClient:
    """独占一个 httpx 客户端的出站门面；与更新用的客户端分开。

    可注入点全部给测试用：``transport``（`httpx.MockTransport`）、``settings_provider``、
    ``clock``（限流与冷却用单调时间）、``wall_clock``（缓存与 `fetched_at` 用墙上时间）、
    ``sleep``。
    """

    def __init__(
        self,
        *,
        cache: CacheStore | None = None,
        status: SourceStatusRegistry | None = None,
        settings_provider: Callable[[], Settings] = get_settings,
        transport: httpx.AsyncBaseTransport | None = None,
        clock: Callable[[], float] = time.monotonic,
        wall_clock: Callable[[], float] = time.time,
        sleep: Callable[[float], Awaitable[None]] = asyncio.sleep,
        jitter: Callable[[], float] = random.random,
    ) -> None:
        self._settings_provider = settings_provider
        settings = settings_provider()
        self.cache = cache if cache is not None else CacheStore(
            max_bytes=settings.sources.cache_max_mb * 1024 * 1024, clock=wall_clock
        )
        self.status = status if status is not None else SourceStatusRegistry(clock=wall_clock)
        self._transport = transport
        self._clock = clock
        self._wall_clock = wall_clock
        self._sleep = sleep
        self._jitter = jitter
        self._client: httpx.AsyncClient | None = None
        self._client_proxy: str | None = None
        self._buckets: dict[str, _TokenBucket] = {}
        self._semaphores: dict[str, asyncio.Semaphore] = {}
        self._inflight: dict[str, asyncio.Future[ExternalResult]] = {}

    # -- 生命周期 ---------------------------------------------------------
    async def aclose(self) -> None:
        """关闭底层客户端；重复调用安全。"""
        client, self._client = self._client, None
        self._client_proxy = None
        if client is not None:
            await client.aclose()

    # -- 取数 -------------------------------------------------------------
    async def get_json(
        self,
        kind: str,
        endpoint: str,
        params: Mapping[str, str] | None = None,
        *,
        cache_level: str | None = None,
        host: str | None = None,
    ) -> ExternalResult:
        """取一个端点的 JSON；带缓存、限流、重试、熔断与旧值回退。

        :param host: 显式指定来源主机（必须是该来源 :attr:`SourceSpec.hosts` 里的一员，
            如企鹅物流的 `penguin-stats.cn`）；缺省用登记表的第一个主机。

        :raises AppError: `EXTERNAL_SOURCE_DISABLED`（来源被关闭）、
            `EXTERNAL_SOURCE_RATE_LIMITED`（等不到令牌或远端 429 冷却中）、
            `EXTERNAL_SOURCE_UNAVAILABLE`（网络错误、超时、5xx、熔断中）、
            `EXTERNAL_RESPONSE_INVALID`（超限、JSON 无效、结构不符）、
            `INVALID_PARAMETER`（主机不在该来源的登记表里）。
        """
        source = resolve_source(kind, self._settings_provider())
        # 主机白名单在发请求之前判：来源登记表里没有的主机一律拒绝。
        validate_host(source.spec, host)
        url = source.spec.url(endpoint, params or {}, host=host)
        key = cache_key(kind, endpoint, params or {})
        level = cache_level if cache_level in CACHE_LEVELS else source.spec.cache_level
        ttl = source.cache_level_seconds.get(level, source.cache_level_seconds["static"])

        hit = self.cache.get(key)
        if hit is not None and not hit.stale:
            logger.debug(
                "外部取数命中缓存 kind=%s endpoint=%s", kind, endpoint
            )
            return ExternalResult(
                kind=kind,
                endpoint=endpoint,
                url=url,
                data=hit.value,
                fetched_at=hit.entry.fetched_at(),
                cached=True,
                stale=False,
            )

        # 同键并发合并成一次请求（规格 §3.3）。
        pending = self._inflight.get(key)
        if pending is not None:
            return await asyncio.shield(pending)

        task: asyncio.Future[ExternalResult] = asyncio.get_running_loop().create_future()
        self._inflight[key] = task
        try:
            result = await self._fetch(source, key, endpoint, url, ttl)
        except BaseException as exc:  # noqa: BLE001 - 失败要发给等同一键的调用方
            if not task.done():
                task.set_exception(exc)
            if not task.cancelled():
                # 取一次异常，避免没有等待者时 asyncio 记「未取回异常」。
                task.exception()
            raise
        else:
            if not task.done():
                task.set_result(result)
            return result
        finally:
            self._inflight.pop(key, None)

    async def _fetch(
        self,
        source: ResolvedSource,
        key: str,
        endpoint: str,
        url: str,
        ttl: int,
    ) -> ExternalResult:
        kind = source.kind
        remaining = self.status.cooldown_remaining(kind)
        if remaining:
            # 冷却期内直接返回限流码，不打站点（规格 §3.2）。
            raise _rate_limited(kind, remaining)

        await self._wait_for_token(source)
        started = self._clock()
        retries = 0
        try:
            response, retries = await self._request_with_retry(source, url)
            data = _parse_json(response)
            bytes_read = len(response.content)
        except AppError as exc:
            self.status.record_failure(
                kind, code=str(exc.code), message=exc.message or "", retries=retries
            )
            self._log(source, endpoint, None, started, None, cached=False, error=str(exc.code))
            stale = self.cache.get(key, allow_stale=True)
            if stale is not None:
                # 刷新失败回退到 24 小时内的旧值，标 stale 并带原抓取时间（S26）。
                logger.warning(
                    "外部取数失败，回退旧值 kind=%s endpoint=%s code=%s",
                    kind,
                    endpoint,
                    exc.code,
                )
                return ExternalResult(
                    kind=kind,
                    endpoint=endpoint,
                    url=url,
                    data=stale.value,
                    fetched_at=stale.entry.fetched_at(),
                    cached=True,
                    stale=True,
                )
            raise
        except (httpx.HTTPError, OSError) as exc:  # pragma: no cover - 兜底
            self.status.record_failure(
                kind, code=str(ErrorCode.EXTERNAL_SOURCE_UNAVAILABLE), message=str(exc)
            )
            raise _unavailable(kind, str(exc)) from exc

        self.cache.put(key, kind, data, ttl=ttl)
        self.status.record_success(kind, bytes_read=bytes_read, retries=retries)
        self._log(source, endpoint, response.status_code, started, bytes_read, cached=False)
        return ExternalResult(
            kind=kind,
            endpoint=endpoint,
            url=url,
            data=data,
            fetched_at=datetime.fromtimestamp(self._wall_clock()).astimezone(),
            cached=False,
            stale=False,
        )

    # -- 请求 -------------------------------------------------------------
    async def _request_with_retry(
        self, source: ResolvedSource, url: str
    ) -> tuple[httpx.Response, int]:
        """发请求；连接错误与 502/503/504 各重试一次。"""
        attempts = 0
        while True:
            try:
                response = await self._request(source, url)
            except (httpx.TransportError, httpx.TimeoutException) as exc:
                if attempts < 1:
                    attempts += 1
                    await self._sleep(RETRY_DELAY + self._jitter() * RETRY_JITTER)
                    continue
                raise _unavailable(source.kind, f"连接失败：{exc}") from exc
            if response.status_code in RETRY_STATUSES and attempts < 1:
                attempts += 1
                await self._sleep(RETRY_DELAY + self._jitter() * RETRY_JITTER)
                continue
            if response.status_code >= 400:
                raise _unavailable(
                    source.kind,
                    f"HTTP {response.status_code}",
                    status=response.status_code,
                )
            return response, attempts

    async def _request(self, source: ResolvedSource, url: str) -> httpx.Response:
        """一次实际请求：并发闸门 + 超时 + 解压后字节上限 + 状态码映射。"""
        spec = source.spec
        semaphore = self._semaphores.setdefault(
            source.kind, asyncio.Semaphore(spec.concurrency)
        )
        timeout = _timeout(spec)
        async with semaphore:
            client = await self._ensure_client(source)
            try:
                async with asyncio.timeout(spec.read_timeout + TOTAL_TIMEOUT_MARGIN):
                    async with client.stream("GET", url, timeout=timeout) as response:
                        if response.status_code == 429:
                            await self._handle_rate_limited(source, response)
                        chunks: list[bytes] = []
                        total = 0
                        async for chunk in response.aiter_bytes():
                            total += len(chunk)
                            if total > spec.max_bytes:
                                raise AppError(
                                    ErrorCode.EXTERNAL_RESPONSE_INVALID,
                                    f"{source.label} 的响应超过 {spec.max_bytes // 1024} KB 上限",
                                    {
                                        "kind": source.kind,
                                        "max_bytes": spec.max_bytes,
                                        "bytes": total,
                                    },
                                )
                            chunks.append(chunk)
                        body = b"".join(chunks)
                        # 已经解压过的字节不能再带着 content-encoding 交给 httpx，
                        # 否则构造 Response 时会二次解码并报 DecodingError。
                        headers = [
                            (name, value)
                            for name, value in response.headers.multi_items()
                            if name.lower() not in {"content-encoding", "content-length"}
                        ]
                        return httpx.Response(
                            status_code=response.status_code,
                            headers=headers,
                            content=body,
                            request=response.request,
                        )
            except TimeoutError as exc:
                raise _unavailable(
                    source.kind,
                    f"请求超过 {spec.read_timeout + TOTAL_TIMEOUT_MARGIN:.0f} 秒未完成",
                ) from exc

    async def _handle_rate_limited(
        self, source: ResolvedSource, response: httpx.Response
    ) -> None:
        """远端 429：读 `Retry-After` 进冷却，返回限流码（规格 §3.2）。"""
        seconds = _retry_after(response.headers.get("Retry-After"))
        self.status.open_cooldown(source.kind, seconds)
        raise _rate_limited(source.kind, int(seconds))

    async def _ensure_client(self, source: ResolvedSource) -> httpx.AsyncClient:
        """按当前代理设置拿客户端；代理变化时重建（规格 §3.2）。"""
        proxy = source.proxy if source.use_proxy else ""
        if self._client is not None and self._client_proxy == proxy:
            return self._client
        if self._client is not None:
            await self._client.aclose()
        kwargs: dict[str, Any] = {
            "headers": {"User-Agent": USER_AGENT},
            "follow_redirects": False,
            "timeout": _timeout(source.spec),
        }
        if proxy:
            kwargs["proxy"] = proxy
        if self._transport is not None:
            kwargs["transport"] = self._transport
        self._client = httpx.AsyncClient(**kwargs)
        self._client_proxy = proxy
        return self._client

    async def _wait_for_token(self, source: ResolvedSource) -> None:
        """令牌桶限流：取不到就等到 5 秒，仍取不到报限流。"""
        bucket = self._buckets.get(source.kind)
        current = source.rate_per_minute
        if bucket is None or bucket.rate != current:
            bucket = _TokenBucket(current, clock=self._clock)
            self._buckets[source.kind] = bucket
        waited = 0.0
        while True:
            delay = bucket.try_take()
            if delay <= 0:
                return
            if waited + delay > TOKEN_WAIT_SECONDS:
                raise _rate_limited(source.kind, int(delay))
            await self._sleep(delay)
            waited += delay

    # -- 日志 -------------------------------------------------------------
    def _log(
        self,
        source: ResolvedSource,
        endpoint: str,
        status_code: int | None,
        started: float,
        bytes_read: int | None,
        *,
        cached: bool,
        error: str | None = None,
    ) -> None:
        logger.info(
            "外部取数 kind=%s endpoint=%s status=%s 耗时=%.3fs 字节=%s 缓存=%s 错误=%s",
            source.kind,
            endpoint,
            status_code if status_code is not None else "-",
            self._clock() - started,
            bytes_read if bytes_read is not None else "-",
            "hit" if cached else "miss",
            error or "-",
        )


def _timeout(spec: Any) -> httpx.Timeout:
    """httpx 的超时对象：四个字段都要给，否则 httpx 拒绝构造。"""
    return httpx.Timeout(
        spec.read_timeout,
        connect=spec.connect_timeout,
        read=spec.read_timeout,
        write=spec.read_timeout,
        pool=spec.connect_timeout,
    )


def _parse_json(response: httpx.Response) -> Any:
    """解析响应体；MediaWiki 的错误对象按结构不符处理。"""
    try:
        data = json.loads(response.content.decode("utf-8"))
    except (UnicodeDecodeError, ValueError) as exc:
        raise AppError(
            ErrorCode.EXTERNAL_RESPONSE_INVALID,
            "外部来源返回的不是合法 JSON",
            {"status": response.status_code},
        ) from exc
    if isinstance(data, dict) and "error" in data:
        raise AppError(
            ErrorCode.EXTERNAL_RESPONSE_INVALID,
            f"外部来源返回错误：{_error_message(data['error'])}",
            {"status": response.status_code},
        )
    return data


def _error_message(error: Any) -> str:
    """MediaWiki 的错误体是 `{"code": ..., "info": ...}` 或 `{"code": [info]}`。"""
    if isinstance(error, dict):
        for key in ("info", "message", "code"):
            value = error.get(key)
            if isinstance(value, str):
                return value[:200]
            if isinstance(value, list) and value:
                return str(value[0])[:200]
    return str(error)[:200]


def _retry_after(value: str | None) -> float:
    """`Retry-After` 的秒数；缺省 60 秒、封顶 300 秒（规格 §3.2）。"""
    if value is None:
        return RETRY_AFTER_DEFAULT
    try:
        seconds = float(value.strip())
    except (TypeError, ValueError):
        return RETRY_AFTER_DEFAULT
    if seconds <= 0:
        return RETRY_AFTER_DEFAULT
    return min(seconds, RETRY_AFTER_MAX)


def _rate_limited(kind: str, seconds: int) -> AppError:
    return AppError(
        ErrorCode.EXTERNAL_SOURCE_RATE_LIMITED,
        f"外部来源 {kind} 正在限流，约 {seconds} 秒后重试",
        {"kind": kind, "retry_after": seconds},
        headers={"Retry-After": str(max(seconds, 1))},
    )


def _unavailable(kind: str, message: str, *, status: int | None = None) -> AppError:
    details: dict[str, Any] = {"kind": kind}
    if status is not None:
        details["status"] = status
    return AppError(
        ErrorCode.EXTERNAL_SOURCE_UNAVAILABLE,
        f"外部来源 {kind} 不可用：{message}",
        details,
    )


#: 进程内共享的默认客户端；装配在 ``app.state.external_client``，模块级供工具缺省使用。
_DEFAULT: ExternalClient | None = None


def default_external_client() -> ExternalClient:
    """默认客户端（进程内单例）；测试请自行构造带 `MockTransport` 的实例。"""
    global _DEFAULT
    if _DEFAULT is None:
        _DEFAULT = ExternalClient()
    return _DEFAULT


def reset_default_external_client() -> None:
    """丢弃默认单例（测试与设置变更后的重建用）。"""
    global _DEFAULT
    _DEFAULT = None
