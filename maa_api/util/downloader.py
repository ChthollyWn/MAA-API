"""异步流式文件下载，支持安全续传、网络层重试和进度回调。"""

from __future__ import annotations

import asyncio
import inspect
import logging
import os
import re
from collections.abc import Callable
from pathlib import Path
from typing import Any

import httpx

logger = logging.getLogger(__name__)

_CHUNK_SIZE = 1024 * 1024
#: 网络层失败的指数退避序列（秒）。第 N 次重试用 ``backoff[min(N-1, len-1)]``，
#: 默认 3 次重试：0.5s → 1s → 2s，依据 docs/07 热更新方案 §5.4
#: 「HTTP 请求与下载分片失败时由下载器自身重试（指数退避，最多 3 次）」。
RETRY_BACKOFF_SECONDS: tuple[float, ...] = (0.5, 1.0, 2.0)
#: 调用方没传 ``client`` 时下载器自建客户端的网络超时。**不能是 ``None``**：
#: 国内直连 GitHub 的典型故障是「TCP 连接已建立、响应一个字节都不来」（M60
#: 实机：内核更新在 0% 卡了一个半小时），没有读超时就不会抛异常，docs/07
#: §5.4 的传输层重试与 §2.3 的多源回退都触发不了。超时按**单次网络操作**计，
#: 不是整包总时长：只要还有分片在流动，大文件下载不会因总时长超限被打断。
DEFAULT_TIMEOUT = httpx.Timeout(connect=15.0, read=60.0, write=30.0, pool=15.0)
#: 对第三方站点发请求时统一使用的浏览器 UA。**不能省**：httpx 默认发
#: ``python-httpx/<版本>``，B 站 WAF 会以 412 直接拒绝该 UA（``game_update`` 的
#: 渠道检查曾因此失败），其它 CDN 也可能有同类规则。
BROWSER_USER_AGENT = (
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
    "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/131.0.0.0 Safari/537.36"
)
_CONTENT_RANGE_RE = re.compile(r"^bytes\s+(\d+)-(\d+)/(\d+|\*)$", re.IGNORECASE)
ProgressCallback = Callable[[int, int], Any]


def _content_length(headers: httpx.Headers) -> int | None:
    value = headers.get("Content-Length")
    if value is None:
        return None
    try:
        size = int(value)
    except ValueError:
        return None
    return size if size >= 0 else None


def _content_range(headers: httpx.Headers) -> tuple[int, int, int | None] | None:
    value = headers.get("Content-Range")
    if value is None:
        return None
    match = _CONTENT_RANGE_RE.fullmatch(value.strip())
    if match is None:
        return None
    start, end = int(match.group(1)), int(match.group(2))
    total = None if match.group(3) == "*" else int(match.group(3))
    if end < start or (total is not None and end >= total):
        return None
    return start, end, total


def describe_download_error(exc: BaseException) -> str:
    """把一次下载失败渲染成日志与用户可见文案。

    ``httpx.ReadTimeout`` / ``httpx.ConnectTimeout`` 的 ``str()`` 是空串，只写
    ``f"{url}: {exc}"`` 会得到 ``url: `` 这种没有任何原因的报错（曾因此
    让「活动资源」的失败记录只剩一个下载地址）。类型名是这类异常唯一携带的信息。
    """
    message = str(exc).strip()
    name = type(exc).__name__
    return f"{name}: {message}" if message else name


def _is_retryable_failure(exc: BaseException) -> bool:
    """只有传输层失败重试；校验类失败与服务端明确答复都不重试。

    大小不符、``Content-Range`` 非法、服务端忽略 ``Range`` 后又截断，都属于
    「重试同样的动作还是同样的结果」（docs/07 §5.4）。服务端明确返回的非 2xx
    同理：那是「这个源不行」，交给调用方的多源回退，不在下载器里空转。
    ``DecodingError`` 是响应体被截断（常见于 gzip 传输被掐断），属于传输层。
    """
    if isinstance(exc, httpx.UnsupportedProtocol):
        # 协议不受支持是配置问题，重试不会变好。
        return False
    return isinstance(exc, (httpx.TransportError, httpx.DecodingError))


async def _notify_progress(
    callback: ProgressCallback | None, downloaded: int, total: int
) -> None:
    if callback is None:
        return
    result = callback(downloaded, total)
    if inspect.isawaitable(result):
        await result


async def _download_once(
    http_client: httpx.AsyncClient,
    url: str,
    destination: Path,
    partial: Path,
    *,
    expected_size: int | None,
    on_progress: ProgressCallback | None,
    resume: bool,
) -> Path:
    """One streaming attempt; mutates ``partial`` and completes ``destination``."""
    if not resume:
        partial.unlink(missing_ok=True)
    offset = partial.stat().st_size if partial.is_file() else 0
    if expected_size is not None and offset > expected_size:
        partial.unlink(missing_ok=True)
        offset = 0
    if expected_size is not None and offset == expected_size and partial.is_file():
        os.replace(partial, destination)
        await _notify_progress(on_progress, offset, expected_size)
        return destination

    headers = {"User-Agent": BROWSER_USER_AGENT}
    if offset:
        headers["Range"] = f"bytes={offset}-"

    async with http_client.stream(
        "GET", url, headers=headers, follow_redirects=True
    ) as response:
        response.raise_for_status()

        range_info: tuple[int, int, int | None] | None = None
        if offset:
            if response.status_code == httpx.codes.PARTIAL_CONTENT:
                range_info = _content_range(response.headers)
                if range_info is None or range_info[0] != offset:
                    raise ValueError(
                        "server returned an invalid Content-Range for resume"
                    )
                if (
                    expected_size is not None
                    and range_info[2] is not None
                    and range_info[2] != expected_size
                ):
                    raise ValueError(
                        "Content-Range total does not match expected_size"
                    )
                if range_info[2] is not None and range_info[1] + 1 != range_info[2]:
                    raise ValueError("resumed response does not cover the full suffix")
            elif response.status_code == httpx.codes.OK:
                # The origin ignored Range. Start over; never append a 200 body.
                partial.unlink(missing_ok=True)
                offset = 0
            else:
                raise ValueError(
                    f"unexpected HTTP {response.status_code} for resumed download"
                )
        elif response.status_code != httpx.codes.OK:
            raise ValueError(
                f"expected HTTP 200 for a fresh download, got {response.status_code}"
            )

        content_length = _content_length(response.headers)
        if expected_size is not None:
            total = expected_size
        elif range_info is not None and range_info[2] is not None:
            total = range_info[2]
        elif content_length is not None:
            total = offset + content_length
        else:
            total = 0

        await _notify_progress(on_progress, offset, total)
        mode = "ab" if offset else "wb"
        received = 0
        with partial.open(mode) as output:
            async for chunk in response.aiter_bytes(_CHUNK_SIZE):
                if not chunk:
                    continue
                output.write(chunk)
                received += len(chunk)
                await _notify_progress(on_progress, offset + received, total)

        if range_info is not None:
            expected_range_bytes = range_info[1] - range_info[0] + 1
            if received != expected_range_bytes:
                raise ValueError(
                    "resumed response body length does not match Content-Range"
                )

    actual_size = partial.stat().st_size
    if expected_size is not None and actual_size != expected_size:
        raise ValueError(
            f"download size mismatch: expected {expected_size}, got {actual_size}"
        )
    if total and actual_size != total:
        raise ValueError(
            f"download size mismatch: response declared {total}, got {actual_size}"
        )

    os.replace(partial, destination)
    await _notify_progress(on_progress, actual_size, total or actual_size)
    return destination


def _new_default_client(
    transport: httpx.AsyncBaseTransport | None = None,
) -> httpx.AsyncClient:
    """调用方没传 ``client`` 时自建的下载客户端。

    - 必须有界超时（:data:`DEFAULT_TIMEOUT`）：卡死要变成超时，才能触发重试与多源回退；
    - ``trust_env=False``：代理只能显式配（docs/07 §2.3 的 ``app.proxy``），不读
      环境变量与 macOS 系统代理。否则同一个更新在服务重启前后可能走不同链路，
      M60 实机就是这样：检查请求走代理、下载却直连 GitHub 并卡死。
    """
    return httpx.AsyncClient(
        transport=transport,
        follow_redirects=True,
        timeout=DEFAULT_TIMEOUT,
        trust_env=False,
    )


async def download(
    url: str,
    dest: str | Path,
    *,
    expected_size: int | None = None,
    on_progress: ProgressCallback | None = None,
    resume: bool = True,
    client: httpx.AsyncClient | None = None,
    transport: httpx.AsyncBaseTransport | None = None,
    keep_partial_on_error: bool = True,
    max_retries: int = 3,
    backoff: tuple[float, ...] = RETRY_BACKOFF_SECONDS,
) -> Path:
    """Download ``url`` to ``dest`` without buffering the response in memory.

    Data is streamed to ``<dest>.part`` and atomically renamed when complete.
    Existing completed files are reused when their size matches ``expected_size``;
    if no size was supplied, an existing destination is treated as complete.

    A partial file is appended to only after a valid ``206 Partial Content``
    response whose ``Content-Range`` begins at the requested byte offset. If a
    server ignores Range and returns ``200``, the old partial is discarded before
    consuming the response. Stream errors and cancellation are propagated; the
    caller chooses whether the partial file is kept with ``keep_partial_on_error``.

    Transport-layer failures (timeouts, connection resets, truncated bodies) are
    retried inside this call up to ``max_retries`` times with exponential
    backoff, per docs/07 §5.4. With ``resume=True`` each retry continues from the
    surviving ``.part`` via ``Range``; with ``resume=False`` every retry starts
    from zero. Validation failures and explicit non-2xx responses are never
    retried here — the caller's source fallback owns those. ``keep_partial_on_error``
    applies to the final failure only, so an in-flight retry does not throw away
    bytes it could still resume.

    ``client`` and ``transport`` provide an HTTPX testing/integration seam. A
    supplied client remains owned by its caller. Progress callbacks receive
    ``(downloaded_bytes, total_bytes)``; ``total_bytes`` is zero when unknown.
    Synchronous and asynchronous callbacks are supported.

    When neither ``client`` nor ``transport`` is supplied the downloader builds
    its own client with :data:`DEFAULT_TIMEOUT` and ``trust_env=False`` (bounded
    timeouts, explicit proxy only). Callers that already own a configured client
    should pass it so the download shares their timeout and proxy policy.
    """
    destination = Path(dest)
    partial = destination.with_suffix(destination.suffix + ".part")

    if expected_size is not None and expected_size < 0:
        raise ValueError("expected_size must be non-negative")
    if client is not None and transport is not None:
        raise ValueError("pass either client or transport, not both")
    if max_retries < 0:
        raise ValueError("max_retries must be non-negative")
    if any(delay < 0 for delay in backoff):
        raise ValueError("backoff delays must be non-negative")

    if destination.is_file():
        current_size = destination.stat().st_size
        if expected_size is None or current_size == expected_size:
            await _notify_progress(
                on_progress,
                current_size,
                expected_size if expected_size is not None else current_size,
            )
            return destination

    destination.parent.mkdir(parents=True, exist_ok=True)

    if not resume:
        partial.unlink(missing_ok=True)

    owns_client = client is None
    http_client = client or _new_default_client(transport)

    try:
        attempt = 0
        while True:
            try:
                return await _download_once(
                    http_client,
                    url,
                    destination,
                    partial,
                    expected_size=expected_size,
                    on_progress=on_progress,
                    resume=resume,
                )
            except BaseException as exc:
                if attempt >= max_retries or not _is_retryable_failure(exc):
                    if not keep_partial_on_error:
                        partial.unlink(missing_ok=True)
                    raise
                attempt += 1
                delay = backoff[min(attempt - 1, len(backoff) - 1)] if backoff else 0.0
                logger.warning(
                    "download attempt %d/%d failed for %s (%s); retrying in %.1fs",
                    attempt,
                    max_retries + 1,
                    url,
                    describe_download_error(exc),
                    delay,
                )
                if delay > 0:
                    await asyncio.sleep(delay)
    finally:
        if owns_client:
            await http_client.aclose()
