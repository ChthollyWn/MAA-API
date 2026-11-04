"""``memory_proposals`` schema：记忆整理代理的输出（M79 规格 §4.5、§4.7）。

输出只有一个 ``items`` 数组，每条候选带种类、分类键、正文、置信度与可选的
``supersedes``。``content`` 不在这里限制 500 字：超限的候选按规格丢弃而不是让
整次抽取失败（重试一次仍不合 schema 才零写入），所以长度规则在
:mod:`maa_api.agent.memory.extract` 里按条判断。
"""

from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, ConfigDict, Field

from maa_api.agent.llm.structured import register_schema

__all__ = ["MemoryProposal", "MemoryProposals"]

class MemoryProposal(BaseModel):
    """一条候选记忆；``supersedes`` 只能指向抽取输入里给出的已生效记忆。"""

    model_config = ConfigDict(extra="forbid")

    kind: Literal["semantic", "episodic", "procedural"]
    subject: str | None = Field(default=None, max_length=64)
    content: str = Field(min_length=1)
    confidence: float = Field(default=0.5, ge=0.0, le=1.0)
    #: 不在这里限长：不在输入白名单里的 id 由抽取层丢掉该字段，而不是让整次输出失效。
    supersedes: str | None = None


class MemoryProposals(BaseModel):
    """记忆整理代理的最终输出；没有值得记的就给空数组。"""

    model_config = ConfigDict(extra="forbid")

    items: list[MemoryProposal] = Field(default_factory=list, max_length=50)


register_schema("memory_proposals", MemoryProposals)
