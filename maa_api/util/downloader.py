"""异步流式文件下载，支持安全续传和进度回调。"""

from __future__ import annotations

import inspect
import os
import re
from collections.abc import Callable
from pathlib import Path
from typing import Any

import httpx

_CHUNK_SIZE = 1024 * 1024
_BROWSER_USER_AGENT = (
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


async def _notify_progress(
    callback: ProgressCallback | None, downloaded: int, total: int
) -> None:
    if callback is None:
        return
    result = callback(downloaded, total)
    if inspect.isawaitable(result):
        await result


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

    ``client`` and ``transport`` provide an HTTPX testing/integration seam. A
    supplied client remains owned by its caller. Progress callbacks receive
    ``(downloaded_bytes, total_bytes)``; ``total_bytes`` is zero when unknown.
    Synchronous and asynchronous callbacks are supported.
    """
    destination = Path(dest)
    partial = destination.with_suffix(destination.suffix + ".part")

    if expected_size is not None and expected_size < 0:
        raise ValueError("expected_size must be non-negative")
    if client is not None and transport is not None:
        raise ValueError("pass either client or transport, not both")

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
    offset = partial.stat().st_size if partial.is_file() else 0
    if expected_size is not None and offset > expected_size:
        partial.unlink(missing_ok=True)
        offset = 0
    if expected_size is not None and offset == expected_size and partial.is_file():
        os.replace(partial, destination)
        await _notify_progress(on_progress, offset, expected_size)
        return destination

    owns_client = client is None
    http_client = client or httpx.AsyncClient(
        transport=transport,
        follow_redirects=True,
        timeout=None,
    )
    headers = {"User-Agent": _BROWSER_USER_AGENT}
    if offset:
        headers["Range"] = f"bytes={offset}-"

    try:
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
    except BaseException:
        if not keep_partial_on_error:
            partial.unlink(missing_ok=True)
        raise
    finally:
        if owns_client:
            await http_client.aclose()
