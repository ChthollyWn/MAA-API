"""内置知识来源：把 :class:`KnowledgeService` 的生效条目接进索引（M76 spec §4.2）。

``builtin`` 来源收 ``source=builtin`` 的生效条目（含被用户改过的内置条目），
``user`` 来源收 ``source=custom`` 的自定义条目。两者都只收启用且 kind 不在
``FULLTEXT_KINDS`` 里的条目：术语表与用户档案每轮全文注入，再进检索索引只是重复。

分块器消费 Markdown 正文；这里把 ``when_to_use`` 与 ``body`` 拼成一篇文档，
``version`` 用三者（title/when_to_use/body）的哈希前 16 位，正文哈希由索引服务
自己算。``full_listing=True``：条目被删或停用后，下次同步从索引里删掉。
"""

from __future__ import annotations

import hashlib
from collections.abc import Mapping, Sequence
from typing import Any, Literal

from maa_api.agent.knowledge import (
    FULLTEXT_KINDS,
    KnowledgeEntry,
    KnowledgeService,
)
from maa_api.agent.knowledge.index.indexer import (
    CorpusSource,
    DocumentRef,
    SourceDocument,
)
from maa_api.db.models import utcnow
from maa_api.domain.enums import KnowledgeSourceKind
from maa_api.domain.errors import AppError, ErrorCode

__all__ = [
    "CorpusSource",
    "DocumentRef",
    "KnowledgeEntrySource",
    "SourceDocument",
]

_TITLES = {"builtin": "内置知识", "user": "自定义知识"}


class KnowledgeEntrySource:
    """``builtin`` 与 ``user`` 两个来源；内容来自 ``KnowledgeService`` 的生效条目。"""

    def __init__(
        self,
        knowledge: KnowledgeService,
        source_id: Literal["builtin", "user"],
    ) -> None:
        if source_id not in _TITLES:
            raise ValueError(f"未知的知识条目来源：{source_id!r}")
        self._knowledge = knowledge
        self.source_id = source_id
        self.kind = (
            KnowledgeSourceKind.BUILTIN
            if source_id == "builtin"
            else KnowledgeSourceKind.USER
        )
        self.title = _TITLES[source_id]
        self.trusted = True
        self.license = None
        self.searchable_by_default = True
        self.setting_key = None
        self.full_listing = True

    # -- 配置 -----------------------------------------------------------
    def validate_config(self, config: Mapping[str, Any]) -> dict[str, Any]:
        """内置来源没有可配置项；非空对象一律拒绝。"""
        if config:
            raise AppError(
                ErrorCode.VALIDATION_ERROR,
                "内置知识来源不接受配置",
                {"source_id": self.source_id, "reason": "config_not_supported"},
            )
        return {}

    # -- 清单与正文 -----------------------------------------------------
    async def list_refs(self, config: Mapping[str, Any]) -> list[DocumentRef]:
        del config  # 内置来源没有配置
        return [
            DocumentRef(
                external_id=entry.id,
                title=entry.title,
                version=_entry_version(entry),
            )
            for entry in await self._entries()
        ]

    async def fetch(
        self, refs: Sequence[DocumentRef], config: Mapping[str, Any]
    ) -> list[SourceDocument]:
        del config
        entries = {entry.id: entry for entry in await self._entries()}
        documents: list[SourceDocument] = []
        now = utcnow()
        for ref in refs:
            entry = entries.get(ref.external_id)
            if entry is None:
                # 清单与取正文之间条目被删掉：跳过，下一次同步会从清单里消失。
                continue
            documents.append(
                SourceDocument(
                    external_id=entry.id,
                    title=entry.title,
                    text=f"{entry.when_to_use}\n\n{entry.body}",
                    url=None,
                    version=ref.version or _entry_version(entry),
                    fetched_at=now,
                    meta={"kind": entry.kind, "modified": entry.modified},
                )
            )
        return documents

    async def _entries(self) -> list[KnowledgeEntry]:
        wanted = "builtin" if self.source_id == "builtin" else "custom"
        return [
            entry
            for entry in await self._knowledge.list_effective()
            if entry.enabled
            and entry.kind not in FULLTEXT_KINDS
            and entry.source == wanted
        ]


def _entry_version(entry: KnowledgeEntry) -> str:
    material = f"{entry.title}\n{entry.when_to_use}\n{entry.body}"
    return hashlib.sha256(material.encode("utf-8")).hexdigest()[:16]
