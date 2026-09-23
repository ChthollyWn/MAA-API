import asyncio
from collections.abc import AsyncIterator
from pathlib import Path

import httpx
import pytest

from maa_api.util.downloader import download


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
