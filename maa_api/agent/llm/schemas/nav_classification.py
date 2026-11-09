"""``nav_classification`` schema：视觉模型给当前游戏界面的分类（M86 spec §4.5.2）。

``node_id`` 必须是候选里的节点 id，模型认为都不像时给 ``null``；``confidence`` 是
0–1 的把握；``reason`` 一句中文（最多 200 字）。``extra="forbid"`` 让导出的 JSON
Schema 带 ``additionalProperties: false``。
"""

from __future__ import annotations

from pydantic import BaseModel, ConfigDict, Field

from maa_api.agent.llm.structured import register_schema

__all__ = ["NavClassification"]


class NavClassification(BaseModel):
    """一次界面分类的输出。"""

    model_config = ConfigDict(extra="forbid")

    node_id: str | None = Field(default=None, max_length=48)
    confidence: float = Field(ge=0, le=1)
    reason: str = Field(default="", max_length=200)


register_schema("nav_classification", NavClassification)
