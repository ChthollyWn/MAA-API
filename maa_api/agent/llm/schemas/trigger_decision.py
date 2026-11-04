"""``trigger_decision`` schema：事件研判的结论（M81 规格 §4.7、08 §4.3）。

``decision`` 是 accept / stop / ignore；accept 时带工单草稿（``worker`` /
``title`` / ``brief`` / ``scope``），``reason`` 一句中文判断依据。约束与 §4.7 的
JSON Schema 一致：``worker`` 只能是代理定义名，``title`` ≤ 64、``brief`` ≤ 4000、
``scope`` ≤ 2000、``reason`` ≤ 300。
"""

from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, ConfigDict, Field

from maa_api.agent.llm.structured import register_schema

__all__ = ["TriggerDecision"]


class TriggerDecision(BaseModel):
    """一条事件该不该主动处理。"""

    model_config = ConfigDict(extra="forbid")

    decision: Literal["accept", "stop", "ignore"]
    worker: str | None = Field(
        default=None, pattern=r"^[a-z][a-z0-9_]{1,31}$"
    )
    title: str | None = Field(default=None, min_length=1, max_length=64)
    brief: str | None = Field(default=None, min_length=1, max_length=4000)
    scope: str | None = Field(default=None, min_length=1, max_length=2000)
    reason: str = Field(min_length=1, max_length=300)


register_schema("trigger_decision", TriggerDecision)
