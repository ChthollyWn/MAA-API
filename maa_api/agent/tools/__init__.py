"""Agent 工具实现，按域分文件。"""

from typing import Any

from pydantic import BaseModel, ConfigDict, Field

from maa_api.agent.registry import ToolContext, ToolDefinition, ToolRegistry, ToolRisk
from maa_api.domain.errors import AppError, ErrorCode

__all__ = ["build_registry"]


def build_registry() -> ToolRegistry:
    """Build the one shared registry for every delivered tool group."""
    # Imports stay inside the factory so each domain module may import its own
    # parameter/context helpers without creating a package initialization cycle.
    from maa_api.agent.tools.account import register_tools as register_account_tools
    from maa_api.agent.tools.activity import register_tools as register_activity_tools
    from maa_api.agent.tools.catalog import register_tools as register_catalog_tools
    from maa_api.agent.tools.copilot_attempt import register_tools as register_copilot_attempt_tools
    from maa_api.agent.tools.copilot_site import register_tools as register_copilot_site_tools
    from maa_api.agent.tools.device import register_tools as register_device_tools
    from maa_api.agent.tools.estate import register_tools as register_estate_tools
    from maa_api.agent.tools.game import register_tools as register_game_tools
    from maa_api.agent.tools.interaction import register_tools as register_interaction_tools
    from maa_api.agent.tools.knowledge import register_tools as register_knowledge_tools
    from maa_api.agent.tools.lease import lease_tools as build_lease_tools
    from maa_api.agent.tools.media import register_tools as register_media_tools
    from maa_api.agent.tools.memory import register_tools as register_memory_tools
    from maa_api.agent.tools.ops import register_tools as register_ops_tools
    from maa_api.agent.tools.delegate import register_tools as register_delegate_tools
    from maa_api.agent.tools.pipeline import register_tools as register_pipeline_tools
    from maa_api.agent.tools.raw import register_tools as register_raw_tools
    from maa_api.agent.tools.recon import register_tools as register_recon_tools
    from maa_api.agent.tools.report import register_tools as register_report_tools
    from maa_api.agent.tools.resource import register_tools as register_resource_tools
    from maa_api.agent.tools.roguelike import register_tools as register_roguelike_tools
    from maa_api.agent.tools.schedule import register_tools as register_schedule_tools
    from maa_api.agent.tools.skills import register_tools as register_skills_tools
    from maa_api.agent.tools.stage_level import register_tools as register_stage_level_tools
    from maa_api.agent.tools.status import register_tools as register_status_tools
    from maa_api.agent.tools.tickets import register_tools as register_ticket_tools
    from maa_api.agent.tools.workspace import register_tools as register_workspace_tools

    registry = ToolRegistry()
    for register in (
        register_status_tools,
        register_stage_level_tools,
        register_activity_tools,
        register_game_tools,
        register_account_tools,
        register_estate_tools,
        register_copilot_site_tools,
        register_copilot_attempt_tools,
        register_roguelike_tools,
        register_media_tools,
        register_pipeline_tools,
        register_device_tools,
        register_raw_tools,
        register_resource_tools,
        register_ops_tools,
        register_schedule_tools,
        register_knowledge_tools,
        # M78：技能工具接在知识工具之后（规格 §4.2）。
        register_skills_tools,
        # M79：记忆工具接在技能工具之后（规格 §4.5）；可见性由代理定义的 tools 决定。
        register_memory_tools,
        register_interaction_tools,
        # M48：只读探索三件套 + 侦察委派（规格 §4.2/§4.3）。
        register_recon_tools,
        # M57：工具目录 + 按需开启扩展组（docs/13 §8.80）。
        register_catalog_tools,
        # M70：Worker 与工单工具（规格 §4.2.2、§4.5.11），不进 REST 目录。
        register_report_tools,
        register_delegate_tools,
        register_ticket_tools,
    ):
        register(registry)
    # M71：设备租约工具（规格 §4.2），接在 M70 新工具之后，不进 REST 目录。
    for definition in build_lease_tools():
        registry.register(definition)
    # M75：工作记忆工具（续读与笔记），接在租约工具之后，不进 REST 目录。
    register_workspace_tools(registry)
    registry.register(
        ToolDefinition(
            name="check_confirmation",
            group="confirmation",
            risk=ToolRisk.SAFE,
            description="查人工确认的状态和已经执行完的工具结果。确认卡还没回来、或要确认某个操作到底过没过时用。",
            params_model=CheckConfirmationParams,
            handler=check_confirmation,
            read_only=True,
            refetchable=True,
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
