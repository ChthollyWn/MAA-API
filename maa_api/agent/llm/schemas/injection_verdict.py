"""``injection_verdict`` schema：rules_llm 复核的结论（M80 规格 §4.5.4）。

``verdict`` 是 injection / benign / uncertain；``confidence`` 是 0–1 的把握；
``categories`` 只用于补充规则没覆盖到的类型（最多 8 个），``reason`` 一句中文。
"""

from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, ConfigDict, Field

from maa_api.agent.llm.structured import register_schema

__all__ = ["InjectionVerdict"]

#: 复核模型可以给出的类别；``other`` 只用于确实无法归类的注入。
InjectionCategory = Literal[
    "override",
    "role",
    "fake_system",
    "fake_tool_call",
    "fake_confirmation",
    "exfiltration",
    "steer_url",
    "other",
]


class InjectionVerdict(BaseModel):
    """一段外部数据的注入判定。"""

    model_config = ConfigDict(extra="forbid")

    verdict: Literal["injection", "benign", "uncertain"]
    confidence: float = Field(ge=0, le=1)
    categories: list[InjectionCategory] = Field(max_length=8)
    reason: str = Field(max_length=120)


register_schema("injection_verdict", InjectionVerdict)
