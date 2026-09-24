from __future__ import annotations

import pytest

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


def test_missing_authoritative_error_description_fails_loudly():
    with pytest.raises(ValueError, match="缺少错误码说明.*MISSING"):
        render_error_table(
            codes=["MISSING"],
            statuses={"MISSING": 400},
            source_rows={},
        )


def test_replace_generated_section_changes_only_marked_content():
    source = "before\n<!-- GENERATED:BLOCK:START -->\nold\n<!-- GENERATED:BLOCK:END -->\nafter\n"

    updated = replace_generated_section(source, "BLOCK", "new\nvalue")

    assert updated == "before\n<!-- GENERATED:BLOCK:START -->\nnew\nvalue\n<!-- GENERATED:BLOCK:END -->\nafter\n"
