"""``delegate_recon``：把一段长探索交给只读侦察 subagent（M48 规格 §4.3）。

父 agent 看到的只有参数与报告：子 agent 的中间步骤、读出的文件正文、原始工具输出
都不会写进父会话历史。这条边界就是 M48 存在的理由——长探索烧的是子 agent 自己的
上下文，不是父会话的。
"""

from __future__ import annotations

from typing import Any

from pydantic import BaseModel, ConfigDict, Field

from maa_api.agent.recon.runner import run_recon
from maa_api.agent.registry import ToolContext, ToolDefinition, ToolRegistry, ToolRisk
from maa_api.domain.errors import AppError, ErrorCode

__all__ = ["DelegateReconParams", "delegate_recon", "register_tools"]

#: ``hints`` 的条数上限：给多了等于让父 agent 把范围重写一遍。
MAX_HINTS = 5


class DelegateReconParams(BaseModel):
    model_config = ConfigDict(extra="forbid")

    question: str = Field(
        min_length=1,
        max_length=500,
        description="要查清的问题，一句话说清「想知道什么」，不要写操作步骤",
    )
    hints: list[str] = Field(
        default_factory=list,
        max_length=MAX_HINTS,
        description="可选的「去哪找」提示，例如某个目录或文件名",
    )
    max_steps: int | None = Field(
        default=None,
        ge=1,
        le=20,
        description="子 agent 最多跑几步；不填用服务端默认值",
    )


def register_tools(registry: ToolRegistry) -> None:
    registry.register(
        ToolDefinition(
            name="delegate_recon",
            group="recon",
            risk=ToolRisk.SAFE,
            description=(
                "把「查清楚某个问题」交给只读侦察 subagent：它会在工作区里搜代码、读文件、"
                "查 git，最后只回一份带证据（路径:行号）的报告。适合需要翻好几个文件才能"
                "回答的问题（实现在哪、这段报错从哪来、这个字段谁写的）。"
                "只有一两个文件的确定位置就直接 read_file，不要为省事外包。"
                "报告里没有证据的结论不要当成事实。"
            ),
            params_model=DelegateReconParams,
            handler=delegate_recon,
            read_only=True,
        )
    )


async def delegate_recon(
    params: DelegateReconParams, ctx: ToolContext
) -> dict[str, Any]:
    """取运行时与设置，跑一次侦察；工作区未配置时直接报错（规格 D1）。"""

    runtime = _required_attr(ctx, "agent_runtime", "Agent 运行时尚未装配")
    settings = _settings(ctx)
    return await runtime.recon(
        params.question,
        hints=list(params.hints),
        max_steps=params.max_steps,
        settings=settings,
    )


def _settings(ctx: ToolContext) -> Any:
    state = _state(ctx)
    provider = getattr(state, "settings_provider", None)
    if not callable(provider):
        raise AppError(ErrorCode.AGENT_DISABLED, "设置服务尚未启动")
    return provider()


def _required_attr(ctx: ToolContext, name: str, message: str) -> Any:
    value = getattr(_state(ctx), name, None)
    if value is None:
        raise AppError(ErrorCode.AGENT_DISABLED, message)
    return value


def _state(ctx: ToolContext) -> Any:
    state = getattr(getattr(ctx.request, "app", None), "state", None)
    if state is None:
        raise AppError(ErrorCode.AGENT_DISABLED, "工具上下文未提供应用运行时状态")
    return state


# 供 runtime 直接复用的入口（不经工具层，方便测试与内部调用）。
run = run_recon
