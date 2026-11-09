"""代理定义的数据形状：文件头字段，加上加载时补的正文与版本哈希。"""

from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, StrictInt

__all__ = [
    "AGENT_KINDS", "DEVICE_LEVELS", "LEGACY_SPEC_NAMES", "MODEL_ROLES", "RISK_CEILINGS",
    "SPEC_NAME_PATTERN", "AgentKind", "AgentSpec", "AgentSpecBudget", "AgentSpecFrontmatter",
    "DeviceLevel", "ModelRoleName", "RiskCeiling",
]

SPEC_NAME_PATTERN = r"^[a-z][a-z0-9_]{1,31}$"
#: v2 门面的两个代理定义：布局、能力探查与选模型方式保持 v2（M67 规格 §4.2.4）。
LEGACY_SPEC_NAMES = frozenset({"legacy_chat", "legacy_incident"})
AgentKind = Literal["orchestrator", "worker", "subagent", "background"]
AGENT_KINDS: tuple[str, ...] = ("orchestrator", "worker", "subagent", "background")
#: 与 ModelRole 枚举同值；枚举建好之前先用字面量。
ModelRoleName = Literal[
    "orchestrator", "worker", "subagent", "summarizer", "title",
    "trigger_eval", "memory", "vision", "embedding",
]
MODEL_ROLES: tuple[str, ...] = (
    "orchestrator", "worker", "subagent", "summarizer", "title",
    "trigger_eval", "memory", "vision", "embedding",
)
#: 由低到高。工具的 device 不能高于代理定义的 device。
DeviceLevel = Literal["none", "read", "exclusive"]
DEVICE_LEVELS: tuple[str, ...] = ("none", "read", "exclusive")
#: 与 RiskLevel 同值。
RiskCeiling = Literal["none", "consume", "destructive"]
RISK_CEILINGS: tuple[str, ...] = ("none", "consume", "destructive")


class AgentSpecBudget(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    max_cost_per_run: float = Field(default=0, ge=0)


class AgentSpecFrontmatter(BaseModel):
    """内置定义文件的 YAML 头。"""

    model_config = ConfigDict(extra="forbid", frozen=True)

    name: str = Field(pattern=SPEC_NAME_PATTERN)
    title: str = Field(min_length=1, max_length=8)
    kind: AgentKind
    description: str = Field(min_length=1, max_length=120)
    model_role: ModelRoleName
    tools: tuple[str, ...]
    deferred_groups: tuple[str, ...] = ()
    device: DeviceLevel
    risk_ceiling: RiskCeiling
    max_steps: StrictInt = Field(ge=0)
    delegates: tuple[str, ...] = ()
    skills: tuple[str, ...] = ()
    output_schema: str | None = None
    #: M87：``ready`` 汇报前要过完成闸门；只有 ``kind=worker`` 可以为 true。
    verify_before_ready: bool = False
    budget: AgentSpecBudget = Field(default_factory=AgentSpecBudget)
    version: StrictInt = Field(ge=1)


class AgentSpec(AgentSpecFrontmatter):
    """加载完成的代理定义。"""

    body: str = Field(min_length=1)
    spec_version: str = Field(pattern=r"^[0-9a-f]{12}$")
    source_file: str
