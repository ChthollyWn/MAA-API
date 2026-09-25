"""Agent 工具实现，按域分文件。"""

from typing import Any

from pydantic import BaseModel, ConfigDict, Field

from maa_api.agent.registry import ToolContext, ToolDefinition, ToolRegistry, ToolRisk
from maa_api.domain.errors import AppError, ErrorCode

__all__ = ["build_registry"]


def build_registry() -> ToolRegistry:
    """Build the one shared registry for every delivered M11 tool group."""
    # Imports stay inside the factory so each domain module may import its own
    # parameter/context helpers without creating a package initialization cycle.
    from maa_api.agent.tools.device import register_tools as register_device_tools
    from maa_api.agent.tools.ops import register_tools as register_ops_tools
    from maa_api.agent.tools.pipeline import register_tools as register_pipeline_tools
    from maa_api.agent.tools.raw import register_tools as register_raw_tools
    from maa_api.agent.tools.resource import register_tools as register_resource_tools
    from maa_api.agent.tools.schedule import register_tools as register_schedule_tools
    from maa_api.agent.tools.status import register_tools as register_status_tools

    registry = ToolRegistry()
    for register in (
        register_status_tools,
        register_pipeline_tools,
        register_device_tools,
        register_raw_tools,
        register_resource_tools,
        register_ops_tools,
        register_schedule_tools,
    ):
        register(registry)
    registry.register(
        ToolDefinition(
            name="check_confirmation",
            group="confirmation",
            risk=ToolRisk.SAFE,
            description="查询人工确认的状态和已完成工具调用的结果。",
            params_model=CheckConfirmationParams,
            handler=check_confirmation,
        )
    )
    return registry


class CheckConfirmationParams(BaseModel):
    model_config = ConfigDict(extra="forbid")

    confirmation_id: str = Field(min_length=1, max_length=36)


async def check_confirmation(
    params: CheckConfirmationParams, context: ToolContext
) -> dict[str, Any]:
    state = getattr(getattr(context.request, "app", None), "state", None)
    service = getattr(state, "confirmation_service", None)
    if service is None:
        raise AppError(ErrorCode.AGENT_DISABLED, "确认服务尚未启动")
    return await service.check_confirmation(params.confirmation_id, context=context)
