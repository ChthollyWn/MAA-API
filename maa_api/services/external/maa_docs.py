"""MAA 官方文档客户端（M77、规格 §4.2）：目录、递归目录树与文件内容。

三个端点都在 GitHub API 上，登记在 :data:`~maa_api.services.external.sources.SOURCES`
的 ``maa_docs`` 里。目录树与单个文件可能很大，也几乎只在同步时读一次，因此三个方法都
走 ``cache=False``：不占进程内缓存，也不在失败时回退旧值。

GitHub API 的匿名额度按小时计。访问层把 HTTP 403 映射成
``EXTERNAL_SOURCE_UNAVAILABLE``（``details.status=403``），这里把它改写成给用户看的
「本小时额度已用完」，错误码与 ``status`` 不变，下次定时刷新会继续。
"""

from __future__ import annotations

import base64
import binascii
from typing import Any, Mapping

from maa_api.domain.errors import AppError, ErrorCode
from maa_api.services.external.http import (
    ExternalClient,
    ExternalResult,
    default_external_client,
)
from maa_api.services.external.sources import CACHE_LIVE

__all__ = [
    "MAA_DOCS_KIND",
    "MAA_DOCS_REPO_URL",
    "MaaDocsClient",
]

#: 来源 kind（登记在 ``sources.SOURCES``）。
MAA_DOCS_KIND = "maa_docs"

#: 来源信封与日志里展示的仓库地址。
MAA_DOCS_REPO_URL = "https://github.com/MaaAssistantArknights/MaaAssistantArknights"

#: 403 的改写文案；GitHub 匿名额度按小时计，下一次刷新会自然继续。
_RATE_LIMIT_MESSAGE = "GitHub API 本小时的匿名额度已用完，下次刷新时继续"


class MaaDocsClient:
    """MAA 文档的只读客户端；所有请求都经 `ExternalClient`。"""

    def __init__(self, client: ExternalClient | None = None) -> None:
        self._client = client

    @property
    def client(self) -> ExternalClient:
        """底层客户端；缺省用进程内单例（装配在 `app.state.external_client`）。"""
        return self._client or default_external_client()

    # -- 端点 -------------------------------------------------------------
    async def docs_dir(self, ref: str) -> list[dict[str, Any]]:
        """取 ``docs`` 目录列表（``contents/docs?ref=<ref>``）；不是列表就报结构不符。"""
        result = await self._get(
            "docs_dir", {"ref": ref}, cache_level=CACHE_LIVE
        )
        if not isinstance(result.data, list):
            raise _invalid("docs_dir", "MAA 文档目录返回的不是列表", ref=ref)
        return result.data

    async def tree(self, tree_sha: str) -> dict[str, Any]:
        """取递归目录树（``git/trees/<sha>?recursive=1``）；被截断时拒绝使用。"""
        result = await self._get(
            "tree", {"tree_sha": tree_sha, "recursive": "1"}
        )
        data = result.data
        if not isinstance(data, dict):
            raise _invalid("tree", "MAA 文档目录树返回的不是对象", tree_sha=tree_sha)
        if data.get("truncated"):
            raise AppError(
                ErrorCode.EXTERNAL_RESPONSE_INVALID,
                "MAA 文档目录树被截断",
                {"kind": MAA_DOCS_KIND, "endpoint": "tree", "tree_sha": tree_sha},
            )
        return data

    async def blob(self, blob_sha: str) -> str:
        """取一个文件（``git/blobs/<sha>``）；base64 解码成 UTF-8 文本。"""
        result = await self._get("blob", {"blob_sha": blob_sha})
        data = result.data
        if not isinstance(data, dict):
            raise _invalid("blob", "MAA 文档文件返回的不是对象", blob_sha=blob_sha)
        encoding = data.get("encoding")
        content = data.get("content")
        if encoding != "base64" or not isinstance(content, str):
            raise _invalid(
                "blob", "MAA 文档文件不是 base64 编码", blob_sha=blob_sha
            )
        try:
            raw = base64.b64decode(content)
            return raw.decode("utf-8")
        except (binascii.Error, UnicodeDecodeError) as exc:
            raise AppError(
                ErrorCode.EXTERNAL_RESPONSE_INVALID,
                "MAA 文档文件解码失败",
                {"kind": MAA_DOCS_KIND, "endpoint": "blob", "blob_sha": blob_sha},
            ) from exc

    # -- 取数 -------------------------------------------------------------
    async def _get(
        self,
        endpoint: str,
        params: Mapping[str, str],
        *,
        cache_level: str | None = None,
    ) -> ExternalResult:
        """统一取数：不读不写缓存；403 改写成匿名额度用完的提示。"""
        try:
            return await self.client.get_json(
                MAA_DOCS_KIND,
                endpoint,
                params,
                cache_level=cache_level,
                cache=False,
            )
        except AppError as exc:
            if (
                exc.code is ErrorCode.EXTERNAL_SOURCE_UNAVAILABLE
                and _status_of(exc) == 403
            ):
                details = dict(exc.details or {})
                raise AppError(
                    ErrorCode.EXTERNAL_SOURCE_UNAVAILABLE,
                    _RATE_LIMIT_MESSAGE,
                    details,
                    headers=exc.headers,
                ) from exc
            raise


def _invalid(endpoint: str, message: str, **extra: str) -> AppError:
    """结构不符的固定错误形状；``details`` 带来源、端点与定位参数。"""
    return AppError(
        ErrorCode.EXTERNAL_RESPONSE_INVALID,
        message,
        {"kind": MAA_DOCS_KIND, "endpoint": endpoint, **extra},
    )


def _status_of(error: AppError) -> int | None:
    """从 :class:`AppError` 的 ``details`` 里取 HTTP 状态码（访问层写的）。"""
    details = error.details if isinstance(error.details, Mapping) else {}
    status = details.get("status")
    return status if isinstance(status, int) and not isinstance(status, bool) else None


def default_maa_docs_client() -> MaaDocsClient:
    """模块级默认门面（与其余访问层同款；不缓存实例，客户端本身是单例）。"""
    return MaaDocsClient()
