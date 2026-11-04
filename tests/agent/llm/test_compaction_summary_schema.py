"""``compaction_summary`` schema 与 06 §4.3 的字段约束一致（M74 规格 §6）。"""

from __future__ import annotations

import pytest
from pydantic import ValidationError

from maa_api.agent.llm.schemas.compaction_summary import CompactionSummary
from maa_api.agent.llm.structured import get_schema


def _fact(index: int) -> dict[str, object]:
    return {"text": f"事实 {index}", "source": "get_stage", "seq": index}


def test_schema_matches_06_section_4_3():
    schema = get_schema("compaction_summary")
    assert schema is CompactionSummary
    assert schema.model_json_schema()["required"] == ["summary"]

    summary = schema.model_validate({"summary": "刷完了 1-7，正在等基建换班。"})
    assert summary.summary == "刷完了 1-7，正在等基建换班。"
    assert summary.facts == []
    assert summary.next_step == ""

    with pytest.raises(ValidationError, match="facts"):
        schema.model_validate(
            {"summary": "事实太多", "facts": [_fact(index) for index in range(51)]}
        )

    with pytest.raises(ValidationError, match="kind"):
        schema.model_validate(
            {"summary": "引用坏了", "refs": [{"kind": "bogus", "id": "x", "label": ""}]}
        )

    with pytest.raises(ValidationError, match="extra"):
        schema.model_validate({"summary": "多余字段", "extra": 1})
