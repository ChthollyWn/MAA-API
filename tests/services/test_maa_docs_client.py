"""MAA 文档客户端（M77、规格 §4.2）：三个端点的 URL、base64 解码、截断与 403。

全部用 `httpx.MockTransport`，不打网。异步用例用 `asyncio.run` 跑（本仓库不装
pytest-asyncio）。
"""

from __future__ import annotations

import asyncio
import base64
from typing import Any

import httpx
import pytest

from maa_api.domain.errors import AppError, ErrorCode
from maa_api.services.external.http import ExternalClient
from maa_api.services.external.maa_docs import MaaDocsClient
from maa_api.settings import Settings, SourcesSettings

REPO_PATH = "/repos/MaaAssistantArknights/MaaAssistantArknights"
DOCS_PATH = f"{REPO_PATH}/contents/docs"
TREE_SHA = "abc123"
TREE_PATH = f"{REPO_PATH}/git/trees/{TREE_SHA}"
BLOB_SHA = "def456"
BLOB_PATH = f"{REPO_PATH}/git/blobs/{BLOB_SHA}"

TEXT = "# MAA 文档\n\n这是一份测试文档。\n"
_ENCODED = base64.b64encode(TEXT.encode("utf-8")).decode("ascii")
# GitHub API 的 base64 内容常带换行；客户端解码时要容忍。
BASE64_CONTENT = "\n".join(
    _ENCODED[index : index + 40] for index in range(0, len(_ENCODED), 40)
)


def async_test(func):
    """本仓库不装 pytest-asyncio：异步用例统一用 `asyncio.run` 跑。"""

    def wrapper(*args, **kwargs):
        return asyncio.run(func(*args, **kwargs))

    wrapper.__name__ = func.__name__
    return wrapper


class Routes:
    """按路径回响应，并记录每一次请求。"""

    def __init__(self, routes: dict[str, Any]) -> None:
        self.routes = routes
        self.requests: list[httpx.Request] = []

    def __call__(self, request: httpx.Request) -> httpx.Response:
        self.requests.append(request)
        route = self.routes.get(request.url.path)
        if route is None:
            return httpx.Response(404, json={"message": "Not Found"})
        if callable(route):
            route = route(request)
        if isinstance(route, httpx.Response):
            return route
        return httpx.Response(200, json=route)


def make_client(
    routes: Routes,
) -> tuple[MaaDocsClient, ExternalClient]:
    settings = Settings(
        sources=SourcesSettings(use_proxy=False, cache_max_mb=8)
    )
    external = ExternalClient(
        transport=httpx.MockTransport(routes),
        settings_provider=lambda: settings,
    )
    return MaaDocsClient(external), external


def ok_routes() -> Routes:
    return Routes(
        {
            DOCS_PATH: [
                {"name": "README.md", "type": "file", "sha": BLOB_SHA},
                {"name": "zh-cn", "type": "dir", "sha": "tree-sha"},
            ],
            TREE_PATH: {
                "sha": TREE_SHA,
                "truncated": False,
                "tree": [
                    {"path": "zh-cn/README.md", "type": "blob", "sha": BLOB_SHA}
                ],
            },
            BLOB_PATH: {
                "sha": BLOB_SHA,
                "encoding": "base64",
                "content": BASE64_CONTENT,
            },
        }
    )


@async_test
async def test_urls_and_params() -> None:
    """三个端点的 URL 形状；`tree` 带 recursive=1；`blob` 的 sha 进路径。"""
    routes = ok_routes()
    client, external = make_client(routes)
    try:
        docs = await client.docs_dir("dev-v2")
        tree = await client.tree(TREE_SHA)
        text = await client.blob(BLOB_SHA)
    finally:
        await external.aclose()

    assert docs[0]["name"] == "README.md"
    assert tree["sha"] == TREE_SHA
    assert text == TEXT
    assert len(routes.requests) == 3

    docs_request, tree_request, blob_request = routes.requests
    assert docs_request.url.host == "api.github.com"
    assert docs_request.url.path == DOCS_PATH
    assert dict(docs_request.url.params) == {"ref": "dev-v2"}

    assert tree_request.url.path == TREE_PATH
    assert dict(tree_request.url.params) == {"recursive": "1"}

    assert blob_request.url.path == BLOB_PATH
    assert dict(blob_request.url.params) == {}
    # 三个端点都不占进程内缓存。
    assert external.cache.stats("maa_docs")["entries"] == 0


@async_test
async def test_blob_decodes_base64() -> None:
    """返回 UTF-8 文本；`encoding` 不是 base64 时报 EXTERNAL_RESPONSE_INVALID。"""
    routes = ok_routes()
    client, external = make_client(routes)
    try:
        assert await client.blob(BLOB_SHA) == TEXT
    finally:
        await external.aclose()
    # GitHub 的 base64 内容带换行，解码要容忍；上面已经覆盖。

    bad_routes = Routes(
        {BLOB_PATH: {"sha": BLOB_SHA, "encoding": "utf-8", "content": TEXT}}
    )
    bad_client, bad_external = make_client(bad_routes)
    try:
        with pytest.raises(AppError) as info:
            await bad_client.blob(BLOB_SHA)
    finally:
        await bad_external.aclose()
    assert info.value.code is ErrorCode.EXTERNAL_RESPONSE_INVALID


@async_test
async def test_truncated_tree_is_invalid() -> None:
    """`truncated: true` 的目录树不完整，直接拒绝。"""
    routes = Routes(
        {
            TREE_PATH: {
                "sha": TREE_SHA,
                "truncated": True,
                "tree": [{"path": "a.md", "type": "blob", "sha": BLOB_SHA}],
            }
        }
    )
    client, external = make_client(routes)
    try:
        with pytest.raises(AppError) as info:
            await client.tree(TREE_SHA)
    finally:
        await external.aclose()
    assert info.value.code is ErrorCode.EXTERNAL_RESPONSE_INVALID
    assert "截断" in (info.value.message or "")
    assert len(routes.requests) == 1


@async_test
async def test_rate_limit_message() -> None:
    """403 改写成「匿名额度已用完」，错误码与 `details.status` 不变。"""
    routes = Routes(
        {
            DOCS_PATH: lambda request: httpx.Response(
                403, json={"message": "API rate limit exceeded"}
            )
        }
    )
    client, external = make_client(routes)
    try:
        with pytest.raises(AppError) as info:
            await client.docs_dir("dev-v2")
    finally:
        await external.aclose()
    assert info.value.code is ErrorCode.EXTERNAL_SOURCE_UNAVAILABLE
    assert info.value.message == "GitHub API 本小时的匿名额度已用完，下次刷新时继续"
    assert info.value.details["status"] == 403
    assert len(routes.requests) == 1
