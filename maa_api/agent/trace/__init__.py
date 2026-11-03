"""运行追踪的 span 记录器（M68 规格 §4.2.1）。

公开名字从这里导出，调用方不直接依赖 ``spans`` 模块的内部布局：T7 的 OTLP 导出
与评测收集都只 import 本包。
"""

from __future__ import annotations

from maa_api.agent.trace.spans import (
    CLOSE_TIMEOUT_SECONDS,
    DELEGATE_TOOLS,
    FLUSH_BATCH_SIZE,
    FLUSH_INTERVAL_SECONDS,
    MAX_ATTRIBUTES,
    MAX_ATTRIBUTE_CHARS,
    MAX_LIST_ITEMS,
    MAX_PENDING_SPANS,
    MAX_WRITE_ATTEMPTS,
    AttributeValue,
    SpanHandle,
    SpanRecorder,
    SpanRecord,
    SpanStatus,
    clean_attributes,
    current_span,
    new_span_id,
    reset_span,
    use_span,
)

__all__ = [
    "CLOSE_TIMEOUT_SECONDS",
    "DELEGATE_TOOLS",
    "FLUSH_BATCH_SIZE",
    "FLUSH_INTERVAL_SECONDS",
    "MAX_ATTRIBUTES",
    "MAX_ATTRIBUTE_CHARS",
    "MAX_LIST_ITEMS",
    "MAX_PENDING_SPANS",
    "MAX_WRITE_ATTEMPTS",
    "AttributeValue",
    "SpanHandle",
    "SpanRecorder",
    "SpanRecord",
    "SpanStatus",
    "clean_attributes",
    "current_span",
    "new_span_id",
    "reset_span",
    "use_span",
]
