"""``delegate`` 工具：把独立子任务交给子代理（M70 规格 §4.5.4）。

工具壳只做参数校验、调用方检查与转发；深度与名单校验、并行信号量、子代理运行的
生命周期都在 :class:`DelegationService`（T10）。本工具是只读的，同一批里的只读
委派会被 toolexec 并发执行。
"""

from __future__ import annotations

from typing import Any

from pydantic import BaseModel, ConfigDict, Field

from maa_api.agent.harness.context import require_run
from maa_api.agent.registry import ToolContext, ToolDefinition, ToolRegistry, ToolRisk
from maa_api.agent.specs.schema import SPEC_NAME_PATTERN
from maa_api.agent.tools._services import v3_service

__all__ = ["DelegateParams", "register_tools"]

DELEGATE_DESCRIPTION = (
    "把一段独立的子任务交给子代理，拿回结构化结论。"
    "查多页资料、在界面里走几步、核对结果、查服务代码时用。"
)


class DelegateParams(BaseModel):
    """``delegate`` 的参数（规格 §4.5.4）。"""

    model_config = ConfigDict(extra="forbid")

    agent: str = Field(min_length=2, max_length=32, pattern=SPEC_NAME_PATTERN)
    task: str = Field(min_length=1, max_length=2000)
    context: str | None = Field(default=None, max_length=4000)


def register_tools(registry: ToolRegistry) -> None:
    """注册 ``delegate``（``worker`` 组，只读，不进 REST 目录）。"""

    registry.register(
        ToolDefinition(
            name="delegate",
            group="worker",
            risk=ToolRisk.SAFE,
            description=DELEGATE_DESCRIPTION,
            params_model=DelegateParams,
            handler=delegate,
            read_only=True,
            rest_visible=False,
        )
    )


async def delegate(params: DelegateParams, ctx: ToolContext) -> dict[str, Any]:
    """把子任务交给委派服务；深度与名单由服务校验。"""
    run = require_run(kinds={"worker", "subagent"})
    delegation_service = v3_service(run, ctx, "delegation_service", "子代理委派服务")
    return await delegation_service.delegate(
        run, agent=params.agent, task=params.task, context=params.context
    )
