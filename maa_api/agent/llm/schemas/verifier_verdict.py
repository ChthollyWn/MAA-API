"""``verifier_verdict`` schema：验证员的核对结论（M70 规格 §4.5.4、§4.7.8）。

``passed`` 只有在每条标准都有证据时才为 true；``confidence`` 是 0–1 的把握程度。
"""

from __future__ import annotations

from typing import Annotated

from pydantic import BaseModel, ConfigDict, Field

from maa_api.agent.llm.structured import register_schema

__all__ = ["VerifierVerdict"]


class VerifierVerdict(BaseModel):
    """验证员的最终输出：是否通过、把握程度、证据与备注。"""

    model_config = ConfigDict(extra="forbid")

    passed: bool
    confidence: float = Field(ge=0, le=1)
    evidence: list[Annotated[str, Field(max_length=300)]] = Field(max_length=20)
    notes: str = Field(max_length=1000)


register_schema("verifier_verdict", VerifierVerdict)
