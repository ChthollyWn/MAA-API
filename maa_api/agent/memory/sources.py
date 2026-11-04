"""``history`` 与 ``memory`` 两个检索来源适配器（M79 规格 §4.5、07 §3、§6）。

这两个来源是**推送式**的：文档由 :class:`~maa_api.agent.memory.history_index.HistoryIndexer`
与 :class:`~maa_api.agent.memory.service.MemoryService` 通过
``KnowledgeIndexService.push_documents(...)`` 写入，不需要来源自己去枚举或抓取。
所以：

- ``list_refs`` 返回空清单、``fetch`` 返回空列表：定时同步看到「这次没有新文档」，
  不会删掉已推送的文档；
- ``full_listing=False``：即使同步被触发，也不会按空清单删除旧文档；
- ``scheduled_refresh=False``：不参加 ``rag.refresh_cron`` 的全量刷新；
- ``searchable_by_default=False``：默认检索不查历史与记忆，调用方必须显式
  ``sources=["history"]`` / ``sources=["memory"]``（07 §2.4、§6）；
- ``trusted``：历史正文混有工具输出与外部页面的转述，按不可信处理（``False``）；
  记忆是用户逐条审核过才生效的，按可信处理（``True``，07 §2.5）。

来源的启用开关（``knowledge_source.enabled``）仍由检索层判断。
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from typing import Any

from maa_api.agent.knowledge.index.indexer import DocumentRef, SourceDocument
from maa_api.domain.enums import KnowledgeSourceKind
from maa_api.domain.errors import AppError, ErrorCode

__all__ = [
    "HistoryCorpusSource",
    "MemoryCorpusSource",
    "build_memory_sources",
]

_HISTORY_TITLE = "历史记录"
_MEMORY_TITLE = "长期记忆"


class _PushOnlySource:
    """推送写入来源的公共形状；子类只改 id、种类与标题。"""

    source_id: str = ""
    kind: KnowledgeSourceKind
    title: str = ""
    trusted = False
    license = None
    searchable_by_default = False
    setting_key = None
    scheduled_refresh = False
    full_listing = False

    def validate_config(self, config: Mapping[str, Any]) -> dict[str, Any]:
        """推送式来源没有可配置项；非空对象一律拒绝（与内置条目来源同口径）。"""
        if config:
            raise AppError(
                ErrorCode.VALIDATION_ERROR,
                "推送式检索来源不接受配置",
                {"source_id": self.source_id, "reason": "config_not_supported"},
            )
        return {}

    async def list_refs(self, config: Mapping[str, Any]) -> list[DocumentRef]:
        """推送式来源没有清单；文档只经 ``push_documents`` 写入。"""
        del config
        return []

    async def fetch(
        self, refs: Sequence[DocumentRef], config: Mapping[str, Any]
    ) -> list[SourceDocument]:
        """没有清单就没有正文可取。"""
        del refs, config
        return []


class HistoryCorpusSource(_PushOnlySource):
    """已结束工单、压缩检查点与 v2 诊断会话（07 §6）。"""

    source_id = "history"
    kind = KnowledgeSourceKind.HISTORY
    title = _HISTORY_TITLE


class MemoryCorpusSource(_PushOnlySource):
    """用户审核过的长期记忆（07 §3、§5）。

    记忆只有审核通过才写进索引，内容来自用户确认，所以来源级 ``trusted=True``
    （07 §2.5）；历史仍按不可信处理。
    """

    source_id = "memory"
    kind = KnowledgeSourceKind.MEMORY
    title = _MEMORY_TITLE
    trusted = True


def build_memory_sources() -> tuple[HistoryCorpusSource, MemoryCorpusSource]:
    """构造 M79 的两个来源；``main.py`` 在 ``start()`` 之前逐个注册。"""
    return HistoryCorpusSource(), MemoryCorpusSource()
