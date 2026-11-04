"""``title`` schema：工单标题（M70 规格 §4.7.7、§4.7.8）。

``TicketTitler`` 用它向模型要一个 1–16 字的标题；``extra="forbid"`` 让 pydantic 导出的
JSON Schema 带 ``additionalProperties: false``，与规格的 JSON Schema 等价。
"""

from __future__ import annotations

from pydantic import BaseModel, ConfigDict, Field

from maa_api.agent.llm.structured import register_schema

__all__ = ["Title"]


class Title(BaseModel):
    """标题生成的输出：只有一个不超过 16 字的 ``title``。"""

    model_config = ConfigDict(extra="forbid")

    title: str = Field(min_length=1, max_length=16)


register_schema("title", Title)
