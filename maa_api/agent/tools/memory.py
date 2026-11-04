"""长期记忆工具：``remember``（M79 规格 §4.5、§4.7、05 §6.1）。

只提交候选，不直接生效：服务按 ``memory.require_review`` 写 ``proposed``（或直接
``active``），确认与改写由记忆页和审核端点负责。``memory.enabled=false`` 时按 M75
``note_too_long`` 的同一口径，在工具结果的 ``error`` 字段里返回
``memory_disabled``——它不是 API 契约的一部分，不新增 ``ErrorCode``。
"""

from __future__ import annotations

from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field

from maa_api.agent.registry import ToolContext, ToolDefinition, ToolRegistry, ToolRisk
from maa_api.domain.errors import AppError, ErrorCode

__all__ = [
    "MEMORY_DISABLED",
    "REMEMBER_DESCRIPTION",
    "RememberParams",
    "register_tools",
    "remember",
]

#: ``memory.enabled=false`` 时工具结果 ``error.code`` 的字符串（不是 ``ErrorCode``）。
MEMORY_DISABLED = "memory_disabled"

#: 规格 §4.7 的工具说明，逐字。
REMEMBER_DESCRIPTION = (
    "把一条你认为该长期记住的事实提交给用户确认。确认前不会生效。\n"
    "只提交用户明确表达过的偏好或环境事实。不要用它记流水线结果。"
)


class RememberParams(BaseModel):
    """``remember`` 的入参：一条候选记忆的种类、分类键与正文。"""

    model_config = ConfigDict(extra="forbid")

    kind: Literal["semantic", "episodic", "procedural"] = Field(
        description="semantic 是稳定事实与偏好，episodic 是一次经历，procedural 是验证过的做法。"
    )
    subject: str = Field(
        min_length=1,
        max_length=64,
        description="分类键，如 preference、account、stage:7-18、operator:阿米娅、infrast。",
    )
    content: str = Field(
        min_length=1,
        max_length=500,
        description="一条记忆只写一件事，用陈述句，不超过 500 字。",
    )


def register_tools(registry: ToolRegistry) -> None:
    """注册记忆工具；不进 REST 目录，只有定义里列出它的代理能看见。"""
    registry.register(
        ToolDefinition(
            name="remember",
            group="memory",
            risk=ToolRisk.SAFE,
            description=REMEMBER_DESCRIPTION,
            params_model=RememberParams,
            handler=remember,
            rest_visible=False,
        )
    )


async def remember(params: RememberParams, ctx: ToolContext) -> dict[str, Any]:
    """提交一条候选记忆；只提议，返回 ``{id, status}``。"""
    service = _memory_service(ctx)
    if service.memory_disabled():
        return {
            "error": {
                "code": MEMORY_DISABLED,
                "message": "长期记忆已停用，这条没有提交",
            }
        }
    origin = getattr(ctx, "origin", None)
    memory = await service.remember_proposal(
        kind=params.kind,
        subject=params.subject,
        content=params.content,
        source_ticket_id=getattr(origin, "ticket_id", None),
        source_session_id=getattr(ctx, "session_id", None),
        source_run_id=getattr(origin, "run_id", None),
    )
    if memory is None:
        # 去重或正文超限；对模型明确说没写进去，但不当作执行错误。
        return {"id": None, "status": "dropped"}
    return {"id": memory.id, "status": str(memory.status)}


def _memory_service(ctx: ToolContext) -> Any:
    state = getattr(getattr(ctx.request, "app", None), "state", None)
    service = getattr(state, "memory_service", None)
    if service is None:
        raise AppError(ErrorCode.AGENT_DISABLED, "记忆服务还没有启动，稍后再试")
    return service
