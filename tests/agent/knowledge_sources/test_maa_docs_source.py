"""MAA 文档来源（M77 spec §4.5、§6）。

用例覆盖清单过滤（只 ``.md``、跳过 <200 字节）、标题优先级与 URL、版本未变不下载、
第二批 403 时已写批次保留，以及 ``ref`` / ``path`` 的配置校验。全部用假客户端；
异步用例统一 ``asyncio.run``。
"""

from __future__ import annotations

import pytest

from maa_api.agent.knowledge.index.indexer import SourceDocument
from maa_api.agent.knowledge.sources.maa_docs import MaaDocsSource
from maa_api.domain.errors import AppError, ErrorCode
from tests.agent.knowledge_sources.support import (
    SettingsStub,
    async_test,
    documents,
    fixture,
    make_service,
    source_row,
)

BLOBS = {
    "blob-faq": "---\ntitle: 常见问题\ndescription: 测试\n---\n\n正文一。\n",
    "blob-connect": "# 连接模拟器失败\n\n检查 ADB 与服务端口。\n",
    "blob-exact": "只有正文，没有标题。",
}

EXPECTED_IDS = ["manual/faq.md", "manual/connect.md", "manual/exact.md"]
EXPECTED_REF_SHAS = ["blob-faq", "blob-connect", "blob-exact"]


class FakeMaaDocs:
    """记录三个端点的调用；``fail_after`` 用来模拟第 N+1 次 blob 被限流。"""

    def __init__(
        self,
        *,
        directory: list[dict] | None = None,
        tree: dict | None = None,
        blobs: dict[str, str] | None = None,
        fail_after: int | None = None,
    ) -> None:
        self.directory = directory if directory is not None else fixture("maa_docs_dir.json")
        self.tree_data = tree if tree is not None else fixture("maa_docs_tree.json")
        self.blobs = blobs if blobs is not None else dict(BLOBS)
        self.fail_after = fail_after
        self.docs_dir_refs: list[str] = []
        self.tree_shas: list[str] = []
        self.blob_calls: list[str] = []

    async def docs_dir(self, ref: str) -> list[dict]:
        self.docs_dir_refs.append(ref)
        return self.directory

    async def tree(self, tree_sha: str) -> dict:
        self.tree_shas.append(tree_sha)
        return self.tree_data

    async def blob(self, blob_sha: str) -> str:
        self.blob_calls.append(blob_sha)
        if self.fail_after is not None and len(self.blob_calls) > self.fail_after:
            raise AppError(
                ErrorCode.EXTERNAL_SOURCE_UNAVAILABLE,
                "GitHub API 本小时的匿名额度已用完，下次刷新时继续",
                {"status": 403},
            )
        return self.blobs[blob_sha]


def _source() -> tuple[MaaDocsSource, FakeMaaDocs]:
    fake = FakeMaaDocs()
    return MaaDocsSource(fake), fake


@async_test
async def test_list_refs_filters_markdown_and_tiny_files() -> None:
    """只要 ``.md``、跳过小于 200 字节的；``version`` 是 blob sha。"""
    source, fake = _source()
    refs = await source.list_refs({})
    assert [ref.external_id for ref in refs] == EXPECTED_IDS
    assert [ref.version for ref in refs] == EXPECTED_REF_SHAS
    assert fake.docs_dir_refs == ["dev-v2"]
    assert fake.tree_shas == ["tree-zh-cn"]


@async_test
async def test_fetch_title_and_url() -> None:
    """frontmatter ``title`` 优先，其次 ``# `` 标题，再没有取文件名；URL 用配置的 ref。"""
    source, fake = _source()
    refs = await source.list_refs({"ref": "test-ref"})
    docs = await source.fetch(refs, {"ref": "test-ref"})
    assert [doc.title for doc in docs] == ["常见问题", "连接模拟器失败", "exact.md"]
    assert [doc.external_id for doc in docs] == EXPECTED_IDS
    assert docs[0].url == (
        "https://github.com/MaaAssistantArknights/MaaAssistantArknights/"
        "blob/test-ref/docs/zh-cn/manual/faq.md"
    )
    assert docs[2].url.endswith("/blob/test-ref/docs/zh-cn/manual/exact.md")
    assert docs[0].meta == {"path": "manual/faq.md", "ref": "test-ref"}
    assert fake.blob_calls == EXPECTED_REF_SHAS



@async_test
async def test_unchanged_blob_is_not_downloaded(db_session_factory) -> None:
    """第二次同步版本都没变，所以一次 blob 都不发。"""
    source, fake = _source()
    service = make_service(db_session_factory, source, SettingsStub())
    try:
        first = await service.sync_source("maa-docs")
        assert first.rebuilt == 3
        assert fake.blob_calls == EXPECTED_REF_SHAS
        second = await service.sync_source("maa-docs")
        assert second.rebuilt == 0
        assert second.fetched == 0
        assert fake.blob_calls == EXPECTED_REF_SHAS
        assert len(await documents(db_session_factory, "maa-docs")) == 3
    finally:
        await service.close()


@async_test
async def test_rate_limit_keeps_written_batches(db_session_factory) -> None:
    """第二批 403 时第一批已入库、来源 ``status='error'``、没有删除。"""
    count = 25
    fake = FakeMaaDocs(
        directory=[{"name": "zh-cn", "type": "dir", "sha": "tree-zh-cn"}],
        tree={
            "sha": "tree-zh-cn",
            "truncated": False,
            "tree": [
                {
                    "path": f"manual/doc{index:02}.md",
                    "type": "blob",
                    "sha": f"blob-{index}",
                    "size": 300,
                }
                for index in range(count)
            ],
        },
        blobs={
            f"blob-{index}": f"# 文档 {index}\n\n" + "正文" * 100
            for index in range(count)
        },
        fail_after=20,
    )
    source = MaaDocsSource(fake)
    service = make_service(db_session_factory, source, SettingsStub())
    try:
        await service.push_documents(
            "maa-docs",
            [
                SourceDocument(
                    external_id="old.md",
                    title="旧文档",
                    text="旧正文",
                    version="old-sha",
                )
            ],
        )
        summary = await service.sync_source("maa-docs")
        assert summary.rebuilt == 20
        assert len(fake.blob_calls) == 21
        stored = await documents(db_session_factory, "maa-docs")
        assert "old.md" in stored
        assert len(stored) == 21
        row = await source_row(db_session_factory, "maa-docs")
        assert row is not None
        assert row.status == "error"
        assert "额度" in (row.error or "")
    finally:
        await service.close()


def test_config_validation() -> None:
    """非法 ``ref`` / ``path`` 报 ``VALIDATION_ERROR``；缺省键补齐。"""
    source, _ = _source()
    assert source.validate_config({}) == {"ref": "dev-v2", "path": "docs/zh-cn"}
    assert source.validate_config({"ref": "release/1.0"})["ref"] == "release/1.0"
    for field, value in (
        ("ref", ""),
        ("ref", "a" * 65),
        ("ref", "has space"),
        ("ref", "中文"),
        ("path", ""),
        ("path", "docs/zh"),
        ("path", "docs/zh-cn/"),
        ("path", "src/zh-cn"),
        ("path", "docs/ZH-cn"),
    ):
        with pytest.raises(AppError) as info:
            source.validate_config({field: value})
        assert info.value.code is ErrorCode.VALIDATION_ERROR
        assert info.value.details["field"] == field
