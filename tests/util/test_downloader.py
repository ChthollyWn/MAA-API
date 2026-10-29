import asyncio
from collections.abc import AsyncIterator
from pathlib import Path

import httpx
import pytest

from maa_api.util.downloader import (
    DEFAULT_TIMEOUT,
    _new_default_client,
    describe_download_error,
    download,
)


class ChunkStream(httpx.AsyncByteStream):
    def __init__(self, chunks: list[bytes]) -> None:
        self.chunks = chunks

    async def __aiter__(self) -> AsyncIterator[bytes]:
        for chunk in self.chunks:
            yield chunk


class RaisingStream(httpx.AsyncByteStream):
    async def __aiter__(self) -> AsyncIterator[bytes]:
        yield b"x" * (1024 * 1024)
        raise RuntimeError("connection dropped")


class ResettingStream(httpx.AsyncByteStream):
    """Yields some bytes, then drops the connection like a reset GitHub transfer."""

    def __init__(self, chunks: list[bytes]) -> None:
        self.chunks = chunks

    async def __aiter__(self) -> AsyncIterator[bytes]:
        for chunk in self.chunks:
            yield chunk
        raise httpx.RemoteProtocolError(
            "peer closed connection without sending complete message body"
        )


class BlockingStream(httpx.AsyncByteStream):
    def __init__(self, waiting: asyncio.Event) -> None:
        self.waiting = waiting

    async def __aiter__(self) -> AsyncIterator[bytes]:
        yield b"x" * (1024 * 1024)
        self.waiting.set()
        await asyncio.Event().wait()


def run(coro):
    return asyncio.run(coro)


def test_streams_to_part_then_atomically_completes_and_reports_progress(
    tmp_path: Path,
) -> None:
    requests: list[httpx.Request] = []
    progress: list[tuple[int, int]] = []

    def handler(request: httpx.Request) -> httpx.Response:
        requests.append(request)
        return httpx.Response(
            200,
            headers={"Content-Length": "6"},
            stream=ChunkStream([b"ab", b"cd", b"ef"]),
            request=request,
        )

    destination = tmp_path / "nested" / "payload.bin"
    client = httpx.AsyncClient(transport=httpx.MockTransport(handler))
    try:
        result = run(
            download(
                "https://download.invalid/file",
                destination,
                expected_size=6,
                on_progress=lambda downloaded, total: progress.append(
                    (downloaded, total)
                ),
                client=client,
            )
        )
    finally:
        run(client.aclose())

    assert result == destination
    assert destination.read_bytes() == b"abcdef"
    assert not destination.with_suffix(".bin.part").exists()
    assert progress[0] == (0, 6)
    assert progress[-1] == (6, 6)
    assert len(progress) >= 3
    assert requests[0].headers["User-Agent"].startswith("Mozilla/")
    assert "Range" not in requests[0].headers


def test_resumes_only_after_206_and_reuses_completed_destination(tmp_path: Path) -> None:
    destination = tmp_path / "archive.zip"
    destination.with_suffix(".zip.part").write_bytes(b"abc")
    requests: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        requests.append(request)
        return httpx.Response(
            206,
            headers={"Content-Range": "bytes 3-5/6", "Content-Length": "3"},
            stream=ChunkStream([b"def"]),
            request=request,
        )

    client = httpx.AsyncClient(transport=httpx.MockTransport(handler))
    try:
        result = run(
            download(
                "https://download.invalid/archive.zip",
                destination,
                expected_size=6,
                client=client,
            )
        )
        reused = run(
            download(
                "https://download.invalid/archive.zip",
                destination,
                expected_size=6,
                client=client,
            )
        )
    finally:
        run(client.aclose())

    assert result == reused == destination
    assert destination.read_bytes() == b"abcdef"
    assert requests[0].headers["Range"] == "bytes=3-"
    assert len(requests) == 1


def test_ignored_range_discards_old_partial_and_restarts_from_200(
    tmp_path: Path,
) -> None:
    destination = tmp_path / "payload.bin"
    destination.with_suffix(".bin.part").write_bytes(b"old")
    requests: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        requests.append(request)
        return httpx.Response(
            200,
            headers={"Content-Length": "5"},
            stream=ChunkStream([b"fresh"]),
            request=request,
        )

    client = httpx.AsyncClient(transport=httpx.MockTransport(handler))
    try:
        run(
            download(
                "https://download.invalid/payload.bin",
                destination,
                expected_size=5,
                client=client,
            )
        )
    finally:
        run(client.aclose())

    assert requests[0].headers["Range"] == "bytes=3-"
    assert destination.read_bytes() == b"fresh"


def test_resume_false_starts_over_without_a_range_header(tmp_path: Path) -> None:
    destination = tmp_path / "payload.bin"
    destination.with_suffix(".bin.part").write_bytes(b"stale")
    requests: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        requests.append(request)
        return httpx.Response(
            200,
            headers={"Content-Length": "5"},
            stream=ChunkStream([b"fresh"]),
            request=request,
        )

    run(
        download(
            "https://download.invalid/payload.bin",
            destination,
            expected_size=5,
            resume=False,
            transport=httpx.MockTransport(handler),
        )
    )

    assert "Range" not in requests[0].headers
    assert destination.read_bytes() == b"fresh"


def test_size_mismatch_keeps_partial_by_default_and_can_clean_it(
    tmp_path: Path,
) -> None:
    destination = tmp_path / "payload.bin"
    transport = httpx.MockTransport(
        lambda request: httpx.Response(
            200,
            headers={"Content-Length": "3"},
            stream=ChunkStream([b"bad"]),
            request=request,
        )
    )

    with pytest.raises(ValueError, match="size mismatch"):
        run(
            download(
                "https://download.invalid/file",
                destination,
                expected_size=4,
                transport=transport,
            )
        )
    assert destination.with_suffix(".bin.part").read_bytes() == b"bad"

    with pytest.raises(ValueError, match="size mismatch"):
        run(
            download(
                "https://download.invalid/file",
                destination,
                expected_size=4,
                resume=False,
                transport=transport,
                keep_partial_on_error=False,
            )
        )
    assert not destination.with_suffix(".bin.part").exists()


@pytest.mark.parametrize("keep_partial", [True, False])
def test_stream_exception_respects_partial_file_policy(
    tmp_path: Path, keep_partial: bool
) -> None:
    destination = tmp_path / "payload.bin"
    transport = httpx.MockTransport(
        lambda request: httpx.Response(200, stream=RaisingStream(), request=request)
    )

    with pytest.raises(RuntimeError, match="connection dropped"):
        run(
            download(
                "https://download.invalid/file",
                destination,
                transport=transport,
                keep_partial_on_error=keep_partial,
            )
        )

    partial = destination.with_suffix(".bin.part")
    assert partial.exists() is keep_partial
    if keep_partial:
        assert partial.read_bytes() == b"x" * (1024 * 1024)


@pytest.mark.parametrize("keep_partial", [True, False])
def test_cancellation_respects_partial_file_policy(
    tmp_path: Path, keep_partial: bool
) -> None:
    async def scenario() -> None:
        destination = tmp_path / "payload.bin"
        waiting = asyncio.Event()
        transport = httpx.MockTransport(
            lambda request: httpx.Response(
                200,
                stream=BlockingStream(waiting),
                request=request,
            )
        )
        task = asyncio.create_task(
            download(
                "https://download.invalid/file",
                destination,
                transport=transport,
                keep_partial_on_error=keep_partial,
            )
        )
        await asyncio.wait_for(waiting.wait(), timeout=1)
        task.cancel()
        with pytest.raises(asyncio.CancelledError):
            await task

        partial = destination.with_suffix(".bin.part")
        assert partial.exists() is keep_partial
        if keep_partial:
            assert partial.read_bytes() == b"x" * (1024 * 1024)

    run(scenario())


def test_describe_download_error_keeps_the_type_when_the_message_is_empty() -> None:
    """httpx 超时的 str() 是空串，报错必须带上类型名才有排查价值。"""
    assert str(httpx.ReadTimeout("")) == ""
    assert describe_download_error(httpx.ReadTimeout("")) == "ReadTimeout"
    assert (
        describe_download_error(httpx.ConnectError("connection refused"))
        == "ConnectError: connection refused"
    )


def test_network_failure_is_retried_and_resumes_from_the_surviving_partial(
    tmp_path: Path,
) -> None:
    """一次连接重置后重试应带 Range 续传，而不是整包重下（docs/07 §5.4）。"""
    destination = tmp_path / "archive.zip"
    requests: list[httpx.Request] = []
    total_size = 2 * 1024 * 1024
    first_half, second_half = b"a" * (1024 * 1024), b"b" * (1024 * 1024)

    def handler(request: httpx.Request) -> httpx.Response:
        requests.append(request)
        if request.headers.get("Range") == f"bytes={len(first_half)}-":
            return httpx.Response(
                206,
                headers={
                    "Content-Range": f"bytes {len(first_half)}-{total_size - 1}/{total_size}",
                    "Content-Length": str(len(second_half)),
                },
                stream=ChunkStream([second_half]),
                request=request,
            )
        return httpx.Response(
            200,
            headers={"Content-Length": str(total_size)},
            stream=ResettingStream([first_half]),
            request=request,
        )

    client = httpx.AsyncClient(transport=httpx.MockTransport(handler))
    try:
        result = run(
            download(
                "https://download.invalid/archive.zip",
                destination,
                expected_size=total_size,
                client=client,
                backoff=(0, 0, 0),
            )
        )
    finally:
        run(client.aclose())

    assert result == destination
    assert destination.read_bytes() == first_half + second_half
    assert not destination.with_suffix(".zip.part").exists()
    assert len(requests) == 2
    assert "Range" not in requests[0].headers
    assert requests[1].headers["Range"] == f"bytes={len(first_half)}-"


def test_http_status_failure_is_not_retried_so_source_fallback_stays_fast(
    tmp_path: Path,
) -> None:
    """服务端明确返回 503 说明这个源不行，重试交给调用方的多源回退。"""
    destination = tmp_path / "payload.bin"
    requests: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        requests.append(request)
        return httpx.Response(503, request=request)

    with pytest.raises(httpx.HTTPStatusError):
        run(
            download(
                "https://download.invalid/payload.bin",
                destination,
                transport=httpx.MockTransport(handler),
                backoff=(0, 0, 0),
            )
        )

    assert len(requests) == 1


def test_retry_budget_is_bounded_and_partial_policy_applies_at_the_end(
    tmp_path: Path,
) -> None:
    destination = tmp_path / "payload.bin"
    attempts = 0

    def handler(request: httpx.Request) -> httpx.Response:
        nonlocal attempts
        attempts += 1
        raise httpx.ConnectError("connection refused", request=request)

    with pytest.raises(httpx.ConnectError):
        run(
            download(
                "https://download.invalid/payload.bin",
                destination,
                transport=httpx.MockTransport(handler),
                max_retries=2,
                backoff=(),
                keep_partial_on_error=False,
            )
        )

    assert attempts == 3
    assert not destination.with_suffix(".bin.part").exists()


def test_non_retryable_status_is_not_retried(tmp_path: Path) -> None:
    destination = tmp_path / "payload.bin"
    requests: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        requests.append(request)
        return httpx.Response(404, request=request)

    with pytest.raises(httpx.HTTPStatusError):
        run(
            download(
                "https://download.invalid/missing.bin",
                destination,
                transport=httpx.MockTransport(handler),
                backoff=(0, 0, 0),
            )
        )

    assert len(requests) == 1


def test_validation_failure_is_not_retried(tmp_path: Path) -> None:
    destination = tmp_path / "payload.bin"
    requests: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        requests.append(request)
        return httpx.Response(
            200,
            headers={"Content-Length": "3"},
            stream=ChunkStream([b"bad"]),
            request=request,
        )

    with pytest.raises(ValueError, match="size mismatch"):
        run(
            download(
                "https://download.invalid/payload.bin",
                destination,
                expected_size=4,
                transport=httpx.MockTransport(handler),
                backoff=(0, 0, 0),
            )
        )

    assert len(requests) == 1


def test_default_client_is_bounded_and_ignores_system_proxy() -> None:
    """自建客户端必须有界超时且不读系统代理（M60）。

    卡死的判定完全依赖网络层超时抛异常：``timeout=None`` 时「TCP 已连上、响应
    不来」永远不会失败，docs/07 §5.4 的重试与 §2.3 的多源回退都触发不了。代理
    也只能显式配，否则同一个更新在重启前后会走不同链路。
    """

    async def scenario() -> None:
        client = _new_default_client()
        try:
            assert client.timeout.connect == DEFAULT_TIMEOUT.connect
            assert client.timeout.read == DEFAULT_TIMEOUT.read
            assert client.timeout.write == DEFAULT_TIMEOUT.write
            assert client.timeout.pool == DEFAULT_TIMEOUT.pool
            assert client.timeout.read is not None
            assert client.trust_env is False
        finally:
            await client.aclose()

    asyncio.run(scenario())
