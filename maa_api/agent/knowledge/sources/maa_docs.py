"""MAA 官方文档来源（M77 spec §4.1、§4.5）。

``contents/docs`` 列出 ``docs/`` 下的目录，配置里的 ``path`` 末段（默认 ``zh-cn``）
决定取哪一棵目录树；树里的 ``.md`` blob 才进清单，小于 200 字节的目录页跳过
（实测 49 个文件里 6 个只有一行标题）。``version`` 用 blob sha，所以版本没变的
文件第二次同步不会下载。

标题按 frontmatter ``title`` → 第一个 ``# `` 标题 → 文件名。正文原样入库，日志
不记外部正文。
"""

from __future__ import annotations

import re
from collections.abc import Mapping, Sequence
from pathlib import PurePosixPath
from typing import Any

from maa_api.agent.knowledge.index.indexer import DocumentRef, SourceDocument
from maa_api.db.models import utcnow
from maa_api.domain.enums import KnowledgeSourceKind
from maa_api.domain.errors import AppError, ErrorCode
from maa_api.services.external.maa_docs import MAA_DOCS_REPO_URL, MaaDocsClient

__all__ = ["MaaDocsSource"]

#: 默认配置；``validate_config`` 用默认值补齐缺的键。
DEFAULT_CONFIG: dict[str, str] = {"ref": "dev-v2", "path": "docs/zh-cn"}

#: 与 spec §4.1 逐字一致的两条形状约束。
_REF_PATTERN = re.compile(r"^[A-Za-z0-9._/-]{1,64}$")
_PATH_PATTERN = re.compile(r"^docs/[a-z]{2}-[a-z]{2}$")

#: 小于这个字节数的 Markdown 基本是只有标题的目录页。
MIN_DOC_BYTES = 200

#: GitHub blob 的展示地址前缀（仓库地址另加 ``/blob/<ref>/<path>/<相对路径>``）。
_BLOB_PREFIX = f"{MAA_DOCS_REPO_URL}/blob"

#: 开头的 YAML frontmatter；只读 ``title`` 一行，不引 YAML 依赖。
_FRONTMATTER = re.compile(r"\A---[ \t]*\r?\n(.*?)\r?\n---[ \t]*(?:\r?\n|\Z)", re.S)
_FRONTMATTER_TITLE = re.compile(r"^title[ \t]*:[ \t]*(.*?)[ \t]*$", re.M)
_HEADING = re.compile(r"^#[ \t]+(.+?)[ \t]*$", re.M)


class MaaDocsSource:
    """MAA 文档来源；清单与正文都经 :class:`MaaDocsClient`。"""

    source_id = "maa-docs"
    kind = KnowledgeSourceKind.MAA_DOCS
    title = "MAA 官方文档"
    trusted = False
    license = "AGPL-3.0（MaaAssistantArknights）"
    searchable_by_default = True
    setting_key = "rag.maa_docs_enabled"
    full_listing = True
    scheduled_refresh = True

    def __init__(self, client: MaaDocsClient) -> None:
        self._client = client

    # -- 配置 -----------------------------------------------------------
    def validate_config(self, config: Mapping[str, Any]) -> dict[str, Any]:
        """校验并补齐 ``ref`` / ``path``；非法值报 ``VALIDATION_ERROR``。"""
        values = dict(config or {})
        ref = values.get("ref", DEFAULT_CONFIG["ref"])
        path = values.get("path", DEFAULT_CONFIG["path"])
        if not isinstance(ref, str) or not _REF_PATTERN.match(ref):
            raise _invalid("ref", "ref 只能由字母、数字、点、下划线、斜杠与连字符组成，且不超过 64 个字符")
        if not isinstance(path, str) or not _PATH_PATTERN.match(path):
            raise _invalid("path", "path 必须形如 docs/zh-cn")
        return {"ref": ref, "path": path}

    # -- 清单与正文 -----------------------------------------------------
    async def list_refs(self, config: Mapping[str, Any]) -> list[DocumentRef]:
        """``contents/docs`` → ``zh-cn`` 目录树 → 只留 ``.md`` 且 size≥200 的 blob。"""
        values = self.validate_config(config)
        directory = values["path"].rsplit("/", 1)[-1]
        entries = await self._client.docs_dir(values["ref"])
        selected = next(
            (
                entry
                for entry in entries
                if isinstance(entry, Mapping)
                and entry.get("name") == directory
                and entry.get("type") == "dir"
            ),
            None,
        )
        tree_sha = selected.get("sha") if selected is not None else None
        if not isinstance(tree_sha, str) or not tree_sha:
            raise AppError(
                ErrorCode.EXTERNAL_RESPONSE_INVALID,
                f"MAA 文档目录里没有 {directory} 目录",
                {"kind": "maa_docs", "endpoint": "docs_dir", "name": directory},
            )
        tree = await self._client.tree(tree_sha)
        refs: list[DocumentRef] = []
        for entry in tree.get("tree") or ():
            if not isinstance(entry, Mapping):
                continue
            path = entry.get("path")
            if entry.get("type") != "blob" or not isinstance(path, str):
                continue
            size = entry.get("size")
            if not path.endswith(".md") or not isinstance(size, int) or size < MIN_DOC_BYTES:
                continue
            external_id = _relative_path(path, directory)
            refs.append(
                DocumentRef(
                    external_id=external_id,
                    title=external_id,
                    version=str(entry.get("sha") or "") or None,
                )
            )
        return refs

    async def fetch(
        self, refs: Sequence[DocumentRef], config: Mapping[str, Any]
    ) -> list[SourceDocument]:
        """逐个取 blob；标题与 URL 按 §4.1 的优先顺序生成。"""
        values = self.validate_config(config)
        path = values["path"]
        fetched_at = utcnow()
        documents: list[SourceDocument] = []
        for ref in refs:
            if not ref.version:
                continue
            text = await self._client.blob(ref.version)
            documents.append(
                SourceDocument(
                    external_id=ref.external_id,
                    title=_title_of(text, ref.external_id),
                    text=text,
                    url=f"{_BLOB_PREFIX}/{values['ref']}/{path}/{ref.external_id}",
                    version=ref.version,
                    fetched_at=fetched_at,
                    meta={"path": ref.external_id, "ref": values["ref"]},
                )
            )
        return documents


def _relative_path(path: str, directory: str) -> str:
    """目录树可能给子树相对路径，也可能给带 ``zh-cn/`` 前缀的路径；统一成前者。"""
    prefix = f"{directory}/"
    return path[len(prefix) :] if path.startswith(prefix) else path


def _title_of(text: str, external_id: str) -> str:
    """frontmatter ``title`` → 第一个 ``# `` 标题 → 文件名。"""
    match = _FRONTMATTER.match(text)
    if match is not None:
        title = _FRONTMATTER_TITLE.search(match.group(1))
        if title is not None:
            value = _unquote(title.group(1))
            if value:
                return value
    heading = _HEADING.search(text)
    if heading is not None:
        value = heading.group(1).strip()
        if value:
            return value
    return PurePosixPath(external_id).name


def _unquote(value: str) -> str:
    """去掉 frontmatter 标量两端的引号（只处理配对的一层）。"""
    text = value.strip()
    if len(text) >= 2 and text[0] == text[-1] and text[0] in {"'", '"'}:
        return text[1:-1].strip()
    return text


def _invalid(field: str, reason: str) -> AppError:
    return AppError(
        ErrorCode.VALIDATION_ERROR,
        f"MAA 文档来源的 {field} 不合法",
        {"field": field, "reason": reason},
    )
