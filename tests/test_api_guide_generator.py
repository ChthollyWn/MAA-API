from __future__ import annotations

import pytest

from maa_api.domain.errors import ERROR_HTTP_STATUS, ErrorCode
import scripts.generate_api_guide as api_guide
from scripts.generate_api_guide import replace_generated_section, render_error_table


def test_error_table_is_generated_from_status_and_authoritative_descriptions():
    table = render_error_table(
        codes=["UNAUTHORIZED", "API_SNIPPET_NOT_FOUND"],
        statuses={"UNAUTHORIZED": 401, "API_SNIPPET_NOT_FOUND": 404},
        source_rows={
            "UNAUTHORIZED": "token 缺失或不匹配",
            "API_SNIPPET_NOT_FOUND": "收藏不存在",
        },
    )

    assert "| `UNAUTHORIZED` | 401 | token 缺失或不匹配 |" in table
    assert "| `API_SNIPPET_NOT_FOUND` | 404 | 收藏不存在 |" in table


def test_unreleased_error_families_have_neutral_descriptions():
    reserved_description = "预留错误码；对应功能未交付（M11/M13）"
    table = render_error_table(
        codes=[
            "CONFIRMATION_EXPIRED",
            "AGENT_SESSION_BUSY",
            "TOOL_EXECUTION_FAILED",
            "LLM_TIMEOUT",
            "PIPELINE_NOT_FOUND",
        ],
        statuses={
            "CONFIRMATION_EXPIRED": 409,
            "AGENT_SESSION_BUSY": 409,
            "TOOL_EXECUTION_FAILED": 500,
            "LLM_TIMEOUT": 504,
            "PIPELINE_NOT_FOUND": 404,
        },
        source_rows={
            "CONFIRMATION_EXPIRED": "超时未响应，已自动拒绝。默认超时 10 分钟",
            "AGENT_SESSION_BUSY": "该会话上一轮 tool-calling 循环尚未结束",
            "TOOL_EXECUTION_FAILED": "工具实现内部抛出未预期异常",
            "LLM_TIMEOUT": "上游在超时内未返回",
            "PIPELINE_NOT_FOUND": "id 不存在",
        },
    )

    for code, status in (
        ("CONFIRMATION_EXPIRED", 409),
        ("AGENT_SESSION_BUSY", 409),
        ("TOOL_EXECUTION_FAILED", 500),
        ("LLM_TIMEOUT", 504),
    ):
        assert f"| `{code}` | {status} | {reserved_description} |" in table
    assert "超时未响应" not in table
    assert "tool-calling" not in table
    assert "上游在超时内" not in table
    assert "| `PIPELINE_NOT_FOUND` | 404 | id 不存在 |" in table


def test_missing_authoritative_error_description_fails_loudly():
    with pytest.raises(ValueError, match="缺少错误码说明.*MISSING"):
        render_error_table(
            codes=["MISSING"],
            statuses={"MISSING": 400},
            source_rows={},
        )


def test_missing_http_status_mapping_fails_loudly(monkeypatch):
    status_map = dict(ERROR_HTTP_STATUS)
    del status_map[ErrorCode.API_SNIPPET_NOT_FOUND]
    monkeypatch.setattr(api_guide, "ERROR_HTTP_STATUS", status_map)
    source = "\n".join(
        [
            "<!-- GENERATED:ERROR-CODES:START -->",
            "<!-- GENERATED:ERROR-CODES:END -->",
            "<!-- GENERATED:OPENAPI-TAGS:START -->",
            "<!-- GENERATED:OPENAPI-TAGS:END -->",
        ]
    )

    with pytest.raises(ValueError, match="缺少 HTTP 状态码映射.*API_SNIPPET_NOT_FOUND"):
        api_guide.generated_guide(source, api_guide.ERROR_SPEC_PATH.read_text())


def test_replace_generated_section_changes_only_marked_content():
    source = "before\n<!-- GENERATED:BLOCK:START -->\nold\n<!-- GENERATED:BLOCK:END -->\nafter\n"

    updated = replace_generated_section(source, "BLOCK", "new\nvalue")

    assert updated == "before\n<!-- GENERATED:BLOCK:START -->\nnew\nvalue\n<!-- GENERATED:BLOCK:END -->\nafter\n"
