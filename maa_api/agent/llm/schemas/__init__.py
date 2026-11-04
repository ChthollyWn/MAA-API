"""结构化输出的业务 schema（M67 规格 §4.2.7、M70 规格 §4.7.8）。

注册表与 ``register_schema`` 等入口在 :mod:`maa_api.agent.llm.structured`；本包每个
schema 一个 pydantic 模型文件，模块末尾调用 ``register_schema``，这里逐个导入以触发
注册。导入本包即完成注册，``complete_structured`` 按名字取到模型。
"""

from maa_api.agent.llm.schemas.memory_proposals import (  # noqa: F401
    MemoryProposal,
    MemoryProposals,
)
from maa_api.agent.llm.schemas.subagent_report import (  # noqa: F401
    SubagentFinding,
    SubagentReport,
)
from maa_api.agent.llm.schemas.title import Title  # noqa: F401
from maa_api.agent.llm.schemas.verifier_verdict import VerifierVerdict  # noqa: F401

from maa_api.agent.llm.schemas import compaction_summary  # noqa: F401

__all__ = [
    "MemoryProposal",
    "MemoryProposals",
    "SubagentFinding",
    "SubagentReport",
    "Title",
    "VerifierVerdict",
]
