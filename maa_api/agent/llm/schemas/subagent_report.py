"""``subagent_report`` schema：子代理交回的结构化报告（M70 规格 §4.5.4、§4.7.8）。

字段与约束照规格：全部必填、不许多余字段；``findings`` 每条带自己的证据清单。
"""

from __future__ import annotations

from typing import Annotated

from pydantic import BaseModel, ConfigDict, Field

from maa_api.agent.llm.structured import register_schema

__all__ = ["SubagentFinding", "SubagentReport"]


class SubagentFinding(BaseModel):
    """报告里的一条结论；``evidence`` 是「来源·页面或关卡」形式的出处。"""

    model_config = ConfigDict(extra="forbid")

    title: str = Field(min_length=1, max_length=120)
    detail: str = Field(max_length=1500)
    evidence: list[Annotated[str, Field(max_length=300)]] = Field(max_length=10)


class SubagentReport(BaseModel):
    """子代理的最终输出：结论、要点、未查清的点与附录。"""

    model_config = ConfigDict(extra="forbid")

    summary: str = Field(min_length=1, max_length=1500)
    findings: list[SubagentFinding] = Field(max_length=20)
    uncertainties: list[Annotated[str, Field(max_length=300)]] = Field(max_length=20)
    artifacts: list[Annotated[str, Field(max_length=36)]] = Field(max_length=20)


register_schema("subagent_report", SubagentReport)
