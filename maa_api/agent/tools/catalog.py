"""工具目录与按需分组暴露（M57，docs/13 §8.80）。

80 个工具声明是每次请求的固定开销（约 4.9 万字符 / 1.9 万 token，占单次上下文约 1/3）。
把「账户、基建、运维、定时、侦察」五个扩展组默认隐藏，模型需要时用
``list_tool_groups`` 查看、``enable_tool_group`` 打开；核心任务（画面/操作/作业/
流水线/状态/活动/知识/提问/确认/设备）的工具始终可见。开启状态按会话保存在
:class:`~maa_api.agent.runtime.AgentRuntime` 进程内，跨轮有效。
"""

from __future__ import annotations

from typing import Any

from pydantic import BaseModel, ConfigDict, Field

from maa_api.agent.registry import ToolContext, ToolDefinition, ToolRegistry, ToolRisk
from maa_api.domain.errors import AppError, ErrorCode

__all__ = ["DEFERRED_TOOL_GROUPS", "register_tools"]

#: 默认不暴露给模型的工具分组。不在此集合里的分组全部常驻（新加的组默认安全），
#: 所以这里只需要列扩展组。``catalog`` 本身必须常驻，不能列进来。
DEFERRED_TOOL_GROUPS = frozenset({"account", "estate", "ops", "schedule", "recon"})


class EmptyParams(BaseModel):
    model_config = ConfigDict(extra="forbid")


class EnableToolGroupParams(BaseModel):
    model_config = ConfigDict(extra="forbid")

    group: str = Field(
        min_length=1,
        max_length=32,
        description="要开启的扩展组名（用 list_tool_groups 查看可选值）。",
    )


def register_tools(registry: ToolRegistry) -> None:
    """注册工具目录的两条元工具（M57）。"""
    registry.register(
        ToolDefinition(
            name="list_tool_groups",
            group="catalog",
            risk=ToolRisk.SAFE,
            description=(
                "列出默认未暴露的扩展工具组（账户/基建/媒体/运维/定时/侦察）、每组有哪些工具、"
                "以及本会话已开启哪些。需要其中某组的能力时先看一次。"
            ),
            params_model=EmptyParams,
            handler=list_tool_groups,
            read_only=True,
        )
    )
    registry.register(
        ToolDefinition(
            name="enable_tool_group",
            group="catalog",
            risk=ToolRisk.SAFE,
            description=(
                "开启一个扩展工具组，开启后本会话后续步骤就能看到并调用这组工具。"
                "只在确实需要时开：账户数据、基建方案、更新维护、定时任务、代码侦察。"
            ),
            params_model=EnableToolGroupParams,
            handler=enable_tool_group,
            read_only=True,
        )
    )


async def list_tool_groups(_params: EmptyParams, context: ToolContext) -> dict[str, Any]:
    runtime = _runtime(context)
    return runtime.tool_group_catalog(context.session_id or "")


async def enable_tool_group(
    params: EnableToolGroupParams, context: ToolContext
) -> dict[str, Any]:
    runtime = _runtime(context)
    if params.group not in DEFERRED_TOOL_GROUPS:
        raise AppError(
            ErrorCode.INVALID_PARAMETER,
            f"未知或无需开启的分组：{params.group}",
            {"可选": sorted(DEFERRED_TOOL_GROUPS)},
        )
    runtime.enable_tool_groups(context.session_id or "", {params.group})
    catalog = runtime.tool_group_catalog(context.session_id or "")
    catalog["note"] = "已开启；下一步起这组工具会出现在工具声明里"
    return catalog


def _runtime(context: ToolContext) -> Any:
    state = getattr(getattr(context.request, "app", None), "state", None)
    runtime = getattr(state, "agent_runtime", None)
    if runtime is None:
        raise AppError(
            ErrorCode.SERVICE_UNAVAILABLE,
            "Agent 运行时尚未启动，工具分组不可用",
        )
    return runtime
