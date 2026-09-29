"""Agent 知识库读取工具（M18，规格 §4.1）。

只有这一个工具：Agent 没有任何写知识库的通道 —— 内容由持有访问令牌的人在设置页
里维护（信任边界见 ``maa_api.agent.knowledge`` 的模块文档）。
"""

from __future__ import annotations

from datetime import UTC, datetime
from typing import Any

from pydantic import BaseModel, ConfigDict, Field

from maa_api.agent.knowledge import ENTRY_ID_PATTERN
from maa_api.agent.registry import ToolContext, ToolDefinition, ToolRegistry, ToolRisk
from maa_api.domain.errors import AppError, ErrorCode

__all__ = ["ReadKnowledgeParams", "register_tools"]


class ReadKnowledgeParams(BaseModel):
    model_config = ConfigDict(extra="forbid")

    #: 与 REST 的路径参数同一套约束：id 是索引行里给出的，本来就只含小写字母、
    #: 数字与中划线。非法 id 在这里就拦下，不必等业务层去猜。
    entry_id: str = Field(pattern=ENTRY_ID_PATTERN.pattern)


def register_tools(registry: ToolRegistry) -> None:
    """Register the read-only knowledge base tool."""

    registry.register(
        ToolDefinition(
            name="read_knowledge",
            group="knowledge",
            risk=ToolRisk.SAFE,
            description=(
                "读取一条知识条目的全文（操作剧本、界面知识、规则、术语表或用户档案）。"
                "系统消息里的知识库索引给出条目 id 与适用场景；用户的问题与其中一条相关时，"
                "先用本工具读取全文再行动。"
            ),
            params_model=ReadKnowledgeParams,
            handler=read_knowledge,
            read_only=True,
        )
    )


async def read_knowledge(
    params: ReadKnowledgeParams, ctx: ToolContext
) -> dict[str, Any]:
    """读取条目的生效内容；未启用与未知 id 都由服务层报 ``KNOWLEDGE_NOT_FOUND``。"""
    service = _required_service(_app_state(ctx), "knowledge_service")
    entry = await service.read_for_agent(params.entry_id)
    return {
        "id": entry.id,
        "kind": entry.kind,
        "title": entry.title,
        "body": entry.body,
        "source": entry.source,
        "modified": entry.modified,
        "updated_at": _iso(entry.updated_at),
    }


def _app_state(ctx: ToolContext) -> Any:
    request = ctx.request
    app = getattr(request, "app", None)
    state = getattr(app, "state", None)
    if state is None:
        raise AppError(ErrorCode.INTERNAL_ERROR, "工具上下文未提供应用运行时状态")
    return state


def _required_service(state: Any, name: str) -> Any:
    service = getattr(state, name, None)
    if service is None:
        raise AppError(ErrorCode.INTERNAL_ERROR, f"应用未装配 {name}")
    return service


def _iso(value: datetime | None) -> str | None:
    if value is None:
        return None
    if value.tzinfo is None:
        value = value.replace(tzinfo=UTC)
    return value.astimezone(UTC).isoformat().replace("+00:00", "Z")
