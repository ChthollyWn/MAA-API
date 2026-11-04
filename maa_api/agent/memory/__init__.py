"""长期记忆服务（M79 规格 §4.1、§4.3、§4.5）。

对外的公开名只有 :class:`MemoryService`；装配方把它挂到
``app.state.memory_service``，``remember`` 工具、抽取任务与注入中间件都从那里取。
M79 G3 会在 ``main.py`` 里把 ``index_writer`` 接到
``KnowledgeIndexService.push_documents("memory", ...)``，T1 只留注入点。
"""

from maa_api.agent.memory.service import (
    ACTIVE_SCAN_SIZE,
    EPISODIC_TTL,
    MAX_CONTENT_CHARS,
    MAX_SUBJECT_CHARS,
    PREVIEW_CHARS,
    PROPOSED_TTL,
    MemoryService,
)

__all__ = [
    "ACTIVE_SCAN_SIZE",
    "EPISODIC_TTL",
    "MAX_CONTENT_CHARS",
    "MAX_SUBJECT_CHARS",
    "PREVIEW_CHARS",
    "PROPOSED_TTL",
    "MemoryService",
]
