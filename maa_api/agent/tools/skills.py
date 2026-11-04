"""技能工具：``load_skill`` 与 ``read_skill_file``（M78 规格 §4.2、§4.5）。

两个工具都只在代理运行里使用（``rest_visible=False``），由 ``SkillsMiddleware``
处理 ``load_skill`` 成功后的激活与工具组。``load_skill`` 不可重取：旧结果不是
「运行时观察数据」，不能被 M74 的清理换成占位；``read_skill_file`` 可重取。
"""

from __future__ import annotations

from typing import Any

from pydantic import BaseModel, ConfigDict, Field

from maa_api.agent.registry import ToolContext, ToolDefinition, ToolRegistry, ToolRisk
from maa_api.agent.skills.registry import SKILL_ID_PATTERN
from maa_api.domain.errors import AppError, ErrorCode

__all__ = ["LoadSkillParams", "ReadSkillFileParams", "register_tools"]


class LoadSkillParams(BaseModel):
    model_config = ConfigDict(extra="forbid")

    skill_id: str = Field(pattern=SKILL_ID_PATTERN.pattern)


class ReadSkillFileParams(BaseModel):
    model_config = ConfigDict(extra="forbid")

    skill_id: str = Field(pattern=SKILL_ID_PATTERN.pattern)
    path: str = Field(min_length=1, max_length=128)


def register_tools(registry: ToolRegistry) -> None:
    """注册技能工具（放在知识工具之后，组名 ``skills``）。"""
    registry.register(
        ToolDefinition(
            name="load_skill",
            group="skills",
            risk=ToolRisk.SAFE,
            description=(
                "加载一个技能的全文。系统提示的技能索引里有和当前请求对得上的技能时，"
                "先加载再动手，按技能的步骤做。"
            ),
            params_model=LoadSkillParams,
            handler=load_skill,
            read_only=True,
            refetchable=False,
            rest_visible=False,
        )
    )
    registry.register(
        ToolDefinition(
            name="read_skill_file",
            group="skills",
            risk=ToolRisk.SAFE,
            description=(
                "读技能目录里的附属文件。技能正文提到附属文件、需要更细的清单或示例时用。"
            ),
            params_model=ReadSkillFileParams,
            handler=read_skill_file,
            read_only=True,
            refetchable=True,
            rest_visible=False,
        )
    )


def _registry(ctx: ToolContext) -> Any:
    state = getattr(getattr(ctx.request, "app", None), "state", None)
    registry = getattr(state, "skill_registry", None)
    if registry is None:
        raise AppError(ErrorCode.INTERNAL_ERROR, "应用未装配 skill_registry")
    return registry


async def load_skill(params: LoadSkillParams, ctx: ToolContext) -> dict[str, Any]:
    """加载技能全文；不可用情况由注册表报 ``SKILL_NOT_FOUND``。"""
    loaded = await _registry(ctx).load(
        params.skill_id, agent_name=getattr(ctx, "agent_name", None)
    )
    return loaded.model_dump()


async def read_skill_file(
    params: ReadSkillFileParams, ctx: ToolContext
) -> dict[str, Any]:
    """读内置技能的附属文件；路径与大小守卫在注册表里。"""
    content = _registry(ctx).read_file(params.skill_id, params.path)
    return {
        "skill_id": params.skill_id,
        "path": params.path,
        "content": content,
        "chars": len(content),
    }
