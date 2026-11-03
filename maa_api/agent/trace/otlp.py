"""可选的 OTLP 导出（M68 规格 §4.2.4、10 §5.1–§5.4）。

导出是 ``otlp`` extra 才有的能力：模块本身不 import OpenTelemetry，只有构造
:class:`OtlpExporter` 时才延迟导入，缺依赖时抛 :class:`OtlpUnavailable`。
:func:`build_exporter` 把它翻译成「记一条警告、返回 ``None``」，于是没装 extra
的服务照常启动、追踪照常写库，只是不导出。

导出映射按 10 §5.2：库里存的 span 原样构造为 ``ReadableSpan``，再补后端识别用的
``gen_ai.provider.name``、``langfuse.observation.type``、``openinference.span.kind``、
``session.id`` 与根运行的 ``langfuse.trace.name``。属性里没有正文，见 10 §2.3。
"""

from __future__ import annotations

import logging
import urllib.parse
import uuid
from collections.abc import Mapping, Sequence
from datetime import UTC, datetime
from typing import Any

from maa_api.agent.trace.spans import AttributeValue, SpanRecord
from maa_api.settings import Settings

__all__ = [
    "EXPORT_TIMEOUT_SECONDS",
    "MAX_EXPORT_PENDING",
    "OTLP_EXTRA",
    "OtlpExporter",
    "OtlpUnavailable",
    "backend_attributes",
    "build_exporter",
    "otlp_span_name",
    "otlp_trace_id",
    "parse_otlp_headers",
]

logger = logging.getLogger(__name__)

#: 依赖所在 extra 的名字（16 §12）。
OTLP_EXTRA = "otlp"
#: 单批导出的超时（秒）；由 ``OTLPSpanExporter`` 的 HTTP 客户端执行。
EXPORT_TIMEOUT_SECONDS = 10.0
#: 导出队列上限：写库成功的批次在这里排队，满了丢最旧的（10 §5.5）。
MAX_EXPORT_PENDING = 10_000

_SERVICE_NAME = "maa-api"
_INSTRUMENTATION_SCOPE = "maa_api.agent.trace"

#: 10 §5.2 的导出表：span 名 → (导出名模板, OTLP SpanKind, langfuse 类型, openinference 类型)。
#: 模板里的 ``{}`` 用对应的 GenAI 属性值填；属性缺失时只留前缀。
_EXPORT_TABLE: dict[str, tuple[str, str, str, str]] = {
    "agent.run": ("invoke_agent {}", "INTERNAL", "agent", "AGENT"),
    "llm.chat": ("chat {}", "CLIENT", "generation", "LLM"),
    "llm.embed": ("embeddings {}", "CLIENT", "embedding", "EMBEDDING"),
    "tool.execute": ("execute_tool {}", "INTERNAL", "tool", "TOOL"),
    "delegate.subagent": ("execute_tool {}", "INTERNAL", "tool", "TOOL"),
    "retrieval.search": ("retrieval.search", "INTERNAL", "retriever", "RETRIEVER"),
}
#: 导出名模板里 ``{}`` 取哪个属性。
_NAME_ATTRIBUTE = {
    "agent.run": "gen_ai.agent.name",
    "llm.chat": "gen_ai.request.model",
    "llm.embed": "gen_ai.request.model",
    "tool.execute": "gen_ai.tool.name",
    "delegate.subagent": "gen_ai.tool.name",
}


class OtlpUnavailable(RuntimeError):
    """OTLP 依赖不可用（没装 ``otlp`` extra）。"""


def parse_otlp_headers(raw: str) -> dict[str, str]:
    """把 ``k=v,k=v`` 解析成字典（10 §5.1）。

    每项按**第一个** ``=`` 拆键值：Basic 认证的 base64 末尾常带 ``=``，按全部
    ``=`` 拆会把值截断。键与值去两端空白，值再做一次 URL 解码（值里的逗号写成
    ``%2C``）。空串与空项跳过；缺 ``=`` 的项抛 ``ValueError``。
    """
    if not raw:
        return {}
    headers: dict[str, str] = {}
    for item in raw.split(","):
        item = item.strip()
        if not item:
            continue
        key, separator, value = item.partition("=")
        if not separator:
            raise ValueError(f"otlp_headers 项缺少 '='：{item!r}")
        key = key.strip()
        if not key:
            raise ValueError(f"otlp_headers 项缺少键：{item!r}")
        headers[key] = urllib.parse.unquote(value.strip())
    return headers


def otlp_trace_id(trace_id: str) -> int:
    """``trace_id`` 是 36 位 UUID 字符串，OTLP 要 128 位整数。"""
    return uuid.UUID(trace_id).int


def _string_attribute(record: SpanRecord, key: str) -> str | None:
    value = record.attributes.get(key)
    if isinstance(value, str) and value:
        return value
    return None


def _span_name_info(record: SpanRecord) -> tuple[str, str, str, str]:
    """返回 (导出名, OTLP SpanKind 名, langfuse 类型, openinference 类型)。"""
    entry = _EXPORT_TABLE.get(record.name)
    if entry is not None:
        template, span_kind, langfuse_type, inference_kind = entry
    elif record.name.startswith("policy."):
        template, span_kind, langfuse_type, inference_kind = (
            record.name,
            "INTERNAL",
            "guardrail",
            "GUARDRAIL",
        )
    else:
        template, span_kind, langfuse_type, inference_kind = (
            record.name,
            "INTERNAL",
            "span",
            "CHAIN",
        )
    if "{}" not in template:
        return template, span_kind, langfuse_type, inference_kind
    attribute = _NAME_ATTRIBUTE.get(record.name)
    value = _string_attribute(record, attribute) if attribute else None
    return (
        template.format(value) if value else template.replace(" {}", ""),
        span_kind,
        langfuse_type,
        inference_kind,
    )


def otlp_span_name(record: SpanRecord) -> str:
    """10 §5.2 的导出名：``invoke_agent {agent}``、``chat {model}`` 等。"""
    return _span_name_info(record)[0]


def backend_attributes(record: SpanRecord) -> dict[str, AttributeValue]:
    """导出时补的后端属性（10 §5.2）。

    - ``gen_ai.provider.name``：取库里的 ``gen_ai.system``（语义约定 1.37 的新名字）；
    - ``langfuse.observation.type`` / ``openinference.span.kind``：按 span 名查表；
    - ``agent.run`` 的 ``session.id``：有工单取 ``maa.ticket.id``，否则取会话 id；
    - 根 ``agent.run`` 的 ``langfuse.trace.name``：代理定义名。
    """
    _, _, langfuse_type, inference_kind = _span_name_info(record)
    attributes: dict[str, AttributeValue] = {
        "langfuse.observation.type": langfuse_type,
        "openinference.span.kind": inference_kind,
    }
    provider = _string_attribute(record, "gen_ai.system")
    if provider is not None:
        attributes["gen_ai.provider.name"] = provider
    if record.name == "agent.run":
        session_id = _string_attribute(record, "maa.ticket.id") or _string_attribute(
            record, "gen_ai.conversation.id"
        )
        if session_id is not None:
            attributes["session.id"] = session_id
        if record.parent_span_id is None:
            trace_name = _string_attribute(
                record, "gen_ai.agent.name"
            ) or _string_attribute(record, "maa.agent.name")
            if trace_name is not None:
                attributes["langfuse.trace.name"] = trace_name
    return attributes


def _nanoseconds(moment: datetime) -> int:
    """naive UTC / 带时区时间 → Unix 纳秒（整数运算，不经过浮点数）。"""
    if moment.tzinfo is None:
        moment = moment.replace(tzinfo=UTC)
    else:
        moment = moment.astimezone(UTC)
    delta = moment - datetime(1970, 1, 1, tzinfo=UTC)
    return (delta.days * 86_400 + delta.seconds) * 1_000_000_000 + (
        delta.microseconds * 1_000
    )


class OtlpExporter:
    """把 :class:`SpanRecord` 批量交给 ``OTLPSpanExporter``。

    构造时才 import OpenTelemetry：没装 ``otlp`` extra 时抛 :class:`OtlpUnavailable`，
    不会把导入错误带进模块导入期。``export`` 是同步方法，由 ``SpanRecorder`` 用
    ``asyncio.to_thread`` 调，不阻塞事件循环。
    """

    def __init__(
        self,
        endpoint: str,
        headers: Mapping[str, str],
        *,
        service_version: str,
        timeout_seconds: float = EXPORT_TIMEOUT_SECONDS,
    ) -> None:
        if timeout_seconds <= 0:
            raise ValueError("timeout_seconds 必须为正数")
        try:
            from opentelemetry.exporter.otlp.proto.http.trace_exporter import (
                OTLPSpanExporter,
            )
            from opentelemetry.sdk.resources import Resource
            from opentelemetry.sdk.trace import ReadableSpan
            from opentelemetry.sdk.trace.export import SpanExportResult
            from opentelemetry.sdk.util.instrumentation import InstrumentationScope
            from opentelemetry.trace import (
                SpanContext,
                SpanKind,
                Status,
                StatusCode,
                TraceFlags,
            )
        except ImportError as exc:  # pragma: no cover - 装了 extra 就走到不到
            raise OtlpUnavailable(
                f"缺少 extra {OTLP_EXTRA}：poetry install --no-root --extras {OTLP_EXTRA}"
            ) from exc

        self._endpoint = endpoint
        self._headers = dict(headers)
        self._timeout_seconds = timeout_seconds
        self._span_exporter = OTLPSpanExporter(
            endpoint=endpoint, headers=dict(headers), timeout=timeout_seconds
        )
        self._resource = Resource.create(
            {"service.name": _SERVICE_NAME, "service.version": service_version}
        )
        self._instrumentation_scope = InstrumentationScope(_INSTRUMENTATION_SCOPE)
        # 运行期使用的 OpenTelemetry 类型：放在 self 上，模块导入不受 extra 影响。
        self._readable_span = ReadableSpan
        self._span_context = SpanContext
        self._span_kind = SpanKind
        self._trace_flags = TraceFlags(TraceFlags.SAMPLED)
        self._status = Status
        self._status_code = StatusCode
        self._success = SpanExportResult.SUCCESS
        self._shutdown = False

    @property
    def endpoint(self) -> str:
        return self._endpoint

    @property
    def headers(self) -> dict[str, str]:
        """导出头（含凭据）；只读副本，避免调用方改到内部状态。"""
        return dict(self._headers)

    def _context(self, trace_id: str, span_id: str) -> Any:
        return self._span_context(
            trace_id=otlp_trace_id(trace_id),
            span_id=int(span_id, 16),
            is_remote=False,
            trace_flags=self._trace_flags,
        )

    def _to_readable_span(self, record: SpanRecord) -> Any:
        _, span_kind, _, _ = _span_name_info(record)
        status = self._status(self._status_code.UNSET)
        if record.status == "error":
            description = _string_attribute(record, "error.type")
            status = self._status(self._status_code.ERROR, description)
        attributes: dict[str, Any] = dict(record.attributes)
        attributes.update(backend_attributes(record))
        parent = (
            self._context(record.trace_id, record.parent_span_id)
            if record.parent_span_id
            else None
        )
        return self._readable_span(
            name=otlp_span_name(record),
            context=self._context(record.trace_id, record.span_id),
            parent=parent,
            resource=self._resource,
            attributes=attributes,
            kind=self._span_kind[span_kind],
            status=status,
            start_time=_nanoseconds(record.started_at),
            end_time=_nanoseconds(record.ended_at),
            instrumentation_scope=self._instrumentation_scope,
        )

    def export(self, records: Sequence[SpanRecord]) -> bool:
        """同步导出一批 span；返回 ``SpanExportResult.SUCCESS`` 表示成功。"""
        if not records:
            return True
        spans = [self._to_readable_span(record) for record in records]
        return self._span_exporter.export(spans) == self._success

    def shutdown(self) -> None:
        """关闭底层 exporter；幂等。"""
        if self._shutdown:
            return
        self._shutdown = True
        self._span_exporter.shutdown()


def build_exporter(
    settings: Settings, *, service_version: str
) -> OtlpExporter | None:
    """按设置构造 exporter；没配地址或缺 extra 时返回 ``None``。

    缺 extra 只记一条警告：可选导出不能阻断服务启动，追踪照常写库（10 §5.1）。
    """
    endpoint = settings.trace.otlp_endpoint.strip()
    if not endpoint:
        return None
    headers = parse_otlp_headers(settings.trace.otlp_headers)
    try:
        return OtlpExporter(endpoint, headers, service_version=service_version)
    except OtlpUnavailable as exc:
        logger.warning(
            "trace.otlp_endpoint 已配置，但 OTLP 依赖不可用（extra %s），导出关闭：%s",
            OTLP_EXTRA,
            exc,
        )
        return None
