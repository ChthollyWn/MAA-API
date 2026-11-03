"""OTLP 导出与 extra 的降级（M68 规格 §4.2.4、§6、10 §5.1–§5.4）。

只有最后一条用例真的用到 OpenTelemetry SDK：开头 ``pytest.importorskip`` 让没装
``otlp`` extra 的环境跳过它。其余用例只碰纯函数与「缺依赖只警告」的降级路径，
所以基础安装下整个文件照常通过（16 §12）。
"""

from __future__ import annotations

import builtins
import logging
import uuid
from datetime import UTC, datetime, timedelta
from typing import Any

import pytest

from maa_api.agent.trace.otlp import (
    EXPORT_TIMEOUT_SECONDS,
    OtlpExporter,
    OtlpUnavailable,
    backend_attributes,
    build_exporter,
    otlp_span_name,
    otlp_trace_id,
    parse_otlp_headers,
)
from maa_api.agent.trace.spans import SpanRecord
from maa_api.domain.enums import SpanKind
from maa_api.settings import Settings, TraceSettings

_TRACE_ID = "11111111-2222-4333-8444-555555555555"
_START = datetime(2026, 1, 2, 3, 4, 5, 123456)


def _record(
    *,
    name: str = "agent.run",
    span_id: str = "0011223344556677",
    parent_span_id: str | None = None,
    attributes: dict[str, Any] | None = None,
    status: str = "ok",
    trace_id: str = _TRACE_ID,
    started_at: datetime = _START,
    ended_at: datetime | None = None,
) -> SpanRecord:
    end = ended_at or (started_at + timedelta(milliseconds=12))
    return SpanRecord(
        trace_id=trace_id,
        span_id=span_id,
        parent_span_id=parent_span_id,
        run_id=None,
        name=name,
        kind=SpanKind.OTHER,
        status=status,  # type: ignore[arg-type]
        attributes=attributes or {},
        started_at=started_at,
        ended_at=end,
        duration_ms=int((end - started_at).total_seconds() * 1000),
    )


# --------------------------------------------------------------------------- #
# 头解析
# --------------------------------------------------------------------------- #
def test_parse_headers_splits_on_first_equal_and_unquotes() -> None:
    raw = (
        "Authorization=Basic%20YWJj=, x-scope=dev,"
        "x-langfuse-ingestion-version=4,x-empty=,"
    )

    headers = parse_otlp_headers(raw)

    # 值里的「=」属于 base64，按第一个「=」拆键值后要保留；逗号编码成 %2C。
    assert headers["Authorization"] == "Basic YWJj="
    assert headers["x-scope"] == "dev"
    assert headers["x-langfuse-ingestion-version"] == "4"
    assert headers["x-empty"] == ""
    assert parse_otlp_headers("") == {}
    assert parse_otlp_headers("  ") == {}
    # 值里的 %2C 解码成逗号，不会被当成两项。
    assert parse_otlp_headers("x-note=a%2Cb") == {"x-note": "a,b"}


def test_parse_headers_rejects_items_without_equal() -> None:
    with pytest.raises(ValueError, match="缺少"):
        parse_otlp_headers("x-scope")
    with pytest.raises(ValueError, match="缺少"):
        parse_otlp_headers("Authorization=Bearer token,x-scope")
    with pytest.raises(ValueError, match="缺少"):
        parse_otlp_headers("=  ")


# --------------------------------------------------------------------------- #
# 导出名与后端属性
# --------------------------------------------------------------------------- #
def test_export_names_follow_genai_conventions() -> None:
    cases = [
        (
            _record(name="agent.run", attributes={"gen_ai.agent.name": "legacy_chat"}),
            "invoke_agent legacy_chat",
        ),
        (
            _record(name="llm.chat", attributes={"gen_ai.request.model": "deepseek-chat"}),
            "chat deepseek-chat",
        ),
        (
            _record(
                name="llm.embed",
                attributes={"gen_ai.request.model": "text-embedding-3-small"},
            ),
            "embeddings text-embedding-3-small",
        ),
        (
            _record(name="tool.execute", attributes={"gen_ai.tool.name": "read_file"}),
            "execute_tool read_file",
        ),
        (
            _record(
                name="delegate.subagent",
                attributes={"gen_ai.tool.name": "delegate"},
            ),
            "execute_tool delegate",
        ),
        (_record(name="retrieval.search"), "retrieval.search"),
        (_record(name="policy.mandate"), "policy.mandate"),
        (_record(name="memory.inject"), "memory.inject"),
    ]

    assert [otlp_span_name(record) for record, _ in cases] == [
        expected for _, expected in cases
    ]
    # 缺属性时只留前缀，不留尾随空格。
    assert otlp_span_name(_record(name="agent.run")) == "invoke_agent"
    assert otlp_trace_id(_TRACE_ID) == uuid.UUID(_TRACE_ID).int


def test_backend_attributes_for_langfuse_and_phoenix() -> None:
    llm = _record(
        name="llm.chat",
        attributes={"gen_ai.system": "deepseek", "gen_ai.request.model": "deepseek-chat"},
    )
    assert backend_attributes(llm) == {
        "langfuse.observation.type": "generation",
        "openinference.span.kind": "LLM",
        "gen_ai.provider.name": "deepseek",
    }

    # 根运行的 agent.run：session.id 取工单，再补 trace 名。
    root = _record(
        attributes={
            "gen_ai.agent.name": "legacy_chat",
            "maa.ticket.id": "ticket-1",
            "gen_ai.conversation.id": "session-1",
        }
    )
    root_attributes = backend_attributes(root)
    assert root_attributes["session.id"] == "ticket-1"
    assert root_attributes["langfuse.trace.name"] == "legacy_chat"
    assert root_attributes["langfuse.observation.type"] == "agent"
    assert root_attributes["openinference.span.kind"] == "AGENT"

    # 没有工单时退回会话 id；子运行的 agent.run 不写 trace 名。
    child = _record(
        parent_span_id="8899aabbccddeeff",
        attributes={"gen_ai.conversation.id": "session-2"},
    )
    child_attributes = backend_attributes(child)
    assert child_attributes["session.id"] == "session-2"
    assert "langfuse.trace.name" not in child_attributes

    # 工单与会话都没有时不写 session.id。
    assert "session.id" not in backend_attributes(_record())

    tool = _record(name="tool.execute", attributes={"gen_ai.tool.name": "read_file"})
    assert backend_attributes(tool)["langfuse.observation.type"] == "tool"
    assert backend_attributes(tool)["openinference.span.kind"] == "TOOL"

    retrieval = _record(name="retrieval.search")
    assert backend_attributes(retrieval)["langfuse.observation.type"] == "retriever"
    assert backend_attributes(retrieval)["openinference.span.kind"] == "RETRIEVER"

    policy = _record(name="policy.mandate")
    assert backend_attributes(policy)["langfuse.observation.type"] == "guardrail"
    assert backend_attributes(policy)["openinference.span.kind"] == "GUARDRAIL"

    assert backend_attributes(_record(name="memory.inject")) == {
        "langfuse.observation.type": "span",
        "openinference.span.kind": "CHAIN",
    }


# --------------------------------------------------------------------------- #
# 缺 extra 的降级
# --------------------------------------------------------------------------- #
def test_missing_extra_disables_export_with_warning(
    monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture
) -> None:
    real_import = builtins.__import__

    def blocked_import(
        name: str,
        globals: dict[str, Any] | None = None,
        locals: dict[str, Any] | None = None,
        fromlist: tuple[str, ...] = (),
        level: int = 0,
    ) -> Any:
        if name == "opentelemetry" or name.startswith("opentelemetry."):
            raise ImportError("no opentelemetry in this environment")
        return real_import(name, globals, locals, fromlist, level)

    monkeypatch.setattr(builtins, "__import__", blocked_import)
    settings = Settings(
        trace=TraceSettings(
            otlp_endpoint="http://127.0.0.1:6006/v1/traces",
            otlp_headers="Authorization=Bearer%20token",
        )
    )

    with caplog.at_level(logging.WARNING):
        assert build_exporter(settings, service_version="0.1.0") is None

    warnings = [record.getMessage() for record in caplog.records]
    assert any("otlp" in message.lower() for message in warnings)
    with pytest.raises(OtlpUnavailable):
        OtlpExporter(
            "http://127.0.0.1:6006/v1/traces",
            {"Authorization": "Bearer token"},
            service_version="0.1.0",
        )
    # 没配地址时根本不去导入依赖，也不记警告。
    caplog.clear()
    with caplog.at_level(logging.WARNING):
        assert build_exporter(Settings(), service_version="0.1.0") is None
    assert caplog.records == []


# --------------------------------------------------------------------------- #
# 真实 SDK 的导出路径
# --------------------------------------------------------------------------- #
def test_exporter_sends_readable_spans_with_ids(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    pytest.importorskip("opentelemetry.sdk")
    from opentelemetry.sdk.trace.export.in_memory_span_exporter import (
        InMemorySpanExporter,
    )
    from opentelemetry.trace import SpanKind as OtlpSpanKind
    from opentelemetry.trace import StatusCode

    instances: list[InMemorySpanExporter] = []

    class CapturingInMemoryExporter(InMemorySpanExporter):
        """替换 ``OTLPSpanExporter``：收下构造参数，span 留在内存里供断言。"""

        def __init__(self, **kwargs: Any) -> None:
            super().__init__()
            self.kwargs = kwargs
            instances.append(self)

    monkeypatch.setattr(
        "opentelemetry.exporter.otlp.proto.http.trace_exporter.OTLPSpanExporter",
        CapturingInMemoryExporter,
    )
    exporter = OtlpExporter(
        "http://127.0.0.1:6006/v1/traces",
        {"Authorization": "Bearer token"},
        service_version="9.9.9",
    )

    root = _record(
        attributes={
            "gen_ai.agent.name": "legacy_chat",
            "gen_ai.conversation.id": "session-1",
        }
    )
    child = _record(
        name="tool.execute",
        span_id="8899aabbccddeeff",
        parent_span_id=root.span_id,
        attributes={"gen_ai.tool.name": "read_file", "error.type": "TOOL_ERROR"},
        status="error",
        started_at=_START + timedelta(milliseconds=2),
        ended_at=_START + timedelta(milliseconds=8),
    )

    assert exporter.export([]) is True
    assert exporter.export([root, child]) is True

    sent_exporter = instances[0]
    assert sent_exporter.kwargs["endpoint"] == "http://127.0.0.1:6006/v1/traces"
    assert sent_exporter.kwargs["headers"] == {"Authorization": "Bearer token"}
    assert sent_exporter.kwargs["timeout"] == EXPORT_TIMEOUT_SECONDS

    sent = sent_exporter.get_finished_spans()
    assert len(sent) == 2
    sent_root, sent_child = sent
    assert sent_root.name == "invoke_agent legacy_chat"
    assert sent_root.context.trace_id == uuid.UUID(_TRACE_ID).int
    assert sent_root.context.span_id == int(root.span_id, 16)
    assert sent_root.parent is None
    assert sent_root.kind == OtlpSpanKind.INTERNAL
    assert sent_root.attributes["session.id"] == "session-1"
    assert sent_root.attributes["langfuse.trace.name"] == "legacy_chat"
    assert sent_root.attributes["langfuse.observation.type"] == "agent"
    assert sent_root.resource.attributes["service.name"] == "maa-api"
    assert sent_root.resource.attributes["service.version"] == "9.9.9"
    assert sent_root.instrumentation_scope.name == "maa_api.agent.trace"

    assert sent_child.name == "execute_tool read_file"
    assert sent_child.context.trace_id == sent_root.context.trace_id
    assert sent_child.parent is not None
    assert sent_child.parent.span_id == sent_root.context.span_id
    assert sent_child.status.status_code == StatusCode.ERROR
    assert sent_child.status.description == "TOOL_ERROR"
    assert sent_child.kind == OtlpSpanKind.INTERNAL

    # 时间按 naive UTC 换成纳秒：差值与 duration_ms 一致。
    assert sent_root.start_time == 1767323045123456000
    assert sent_root.end_time - sent_root.start_time == 12_000_000
    assert sent_child.end_time - sent_child.start_time == 6_000_000

    exporter.shutdown()
    exporter.shutdown()  # 幂等，重复关闭不抛。
