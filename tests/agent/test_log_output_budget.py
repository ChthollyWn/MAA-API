"""M53：``get_logs`` 的输出预算（正文截断 + compact 投影）。

背景：2026-10-14 的「运行yw-1不用代理」会话里 ``get_logs`` 被调 17 次、合计
203 661 字，单条最大 41 432 字——整段回灌会按轮次重复计费。默认改成：每条正文
截到 200 字、只回定位用的字段；要看全文传 ``detail="full"`` 与更大的
``content_chars``。
"""

from __future__ import annotations

import asyncio

from maa_api.agent.registry import ToolContext
from maa_api.agent.tools.status import (
    MAX_LOG_RESULT_CHARS,
    GetLogsParams,
    _log_wire,
    _trim_log_items,
)


class _Row:
    """最小日志行：只带 ``_log_wire`` 读得到的字段。"""

    def __init__(self, content: str) -> None:
        self.id = 404
        self.created_at = _utc()
        self.source = "server"  # LogSource.SERVER
        self.level = "info"  # LogLevel.INFO
        self.content = content
        self.pipeline_id = "p-1"
        self.task_id = "t-1"
        self.meta = {"logger": "maa_api.core.client", "attachment": "a.bin"}


def _utc():
    from datetime import UTC, datetime

    return datetime(2026, 10, 14, 9, 33, tzinfo=UTC)


def test_log_wire_truncates_content_and_drops_optional_fields_by_default() -> None:
    """默认 compact：正文截到 content_chars，且不带 pipeline_id / logger 等字段。"""

    row = _Row("很长的内核日志 " * 5_000)  # 约 4 万字，与实测单条最大量级一致
    wire = _log_wire(row, content_chars=200)

    assert len(wire["content"]) == 200
    assert wire["content_truncated"] is True
    # compact 投影不带定位之外的字段
    for key in ("pipeline_id", "task_id", "logger", "attachment"):
        assert key not in wire
    # 定位用的四个字段在
    assert {"id", "ts", "source", "level"} <= set(wire)


def test_log_wire_full_keeps_everything_and_marks_no_truncation() -> None:
    """``detail="full"`` 时保留完整投影；短正文不标截断。"""

    short = _Row("短日志")
    wire = _log_wire(short, content_chars=200, detail="full")
    assert wire["content"] == "短日志"
    assert "content_truncated" not in wire
    assert wire["pipeline_id"] == "p-1"
    assert wire["logger"] == "maa_api.core.client"

    long_row = _Row("x" * 5_000)
    full = _log_wire(long_row, content_chars=2_000, detail="full")
    assert len(full["content"]) == 2_000
    assert full["content_truncated"] is True


def test_log_items_are_trimmed_to_the_total_budget_from_the_oldest_end() -> None:
    """M57：参数层单条预算之外再加一层总预算（实测 40×600 字 = 6 140 字）。"""

    items = [
        {"id": index, "ts": 1.0, "source": "service", "level": "info", "content": "x" * 300}
        for index in range(40)
    ]
    result = _trim_log_items(items)

    assert result["dropped"] > 0
    assert 1 <= len(result["items"]) < len(items)
    assert sum(len(item["content"]) for item in result["items"]) <= MAX_LOG_RESULT_CHARS
    # order=desc：保最新（id 大的在前），丢最旧。
    assert result["items"][0]["id"] == 0
    assert result["items"][-1]["id"] == len(result["items"]) - 1
    assert result["truncated"]["dropped"] == result["dropped"]

    short = [{"id": 1, "content": "ok"}]
    assert _trim_log_items(short) == {"items": short, "dropped": 0}


def test_get_logs_params_defaults_are_bounded() -> None:
    """参数默认值就是预算：200 字、compact；上限也钉住，避免有人放开成无界。"""

    params = GetLogsParams()
    assert params.content_chars == 200
    assert params.detail == "compact"

    # 越界在参数层就被拒（pydantic ge/le），不用等业务层
    import pytest

    with pytest.raises(ValueError):
        GetLogsParams(content_chars=10_000)
    with pytest.raises(ValueError):
        GetLogsParams(content_chars=10)
    with pytest.raises(ValueError):
        GetLogsParams(detail="everything")


def test_tool_context_import_is_unused_guard() -> None:
    """占位：确保 ``ToolContext`` 仍可导入（用例文件与实现同源，改名时这里先红）。"""

    assert ToolContext is not None and asyncio is not None
