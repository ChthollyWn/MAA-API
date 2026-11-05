"""四个目标工具：建目标、列表、详情与修改（M82 规格 §4.2）。

工具壳只做参数与调用方检查，业务在 :class:`~maa_api.agent.goals.service.GoalService`
里；服务由 T7 装配到 ``app.state.goal_service``，装配前测试通过替身注入。四个工具
都不进 REST 目录（规格 §4.2）。
"""

from __future__ import annotations

from typing import Any

from pydantic import BaseModel, ConfigDict, Field

from maa_api.agent.goals.models import GoalCreate, GoalUpdate
from maa_api.agent.registry import ToolContext, ToolDefinition, ToolRegistry, ToolRisk
from maa_api.domain.enums import GoalStatus
from maa_api.domain.errors import AppError, ErrorCode

__all__ = [
    "GetGoalParams",
    "ListGoalsParams",
    "UpdateGoalParams",
    "register_tools",
]

CREATE_GOAL_DESCRIPTION = "用户要在一段时间内达成一件事时建目标，例如月底前练到某个练度、攒够某种材料。目标会自己建规划工单并按时复盘，同一件事不要再建工单。"
LIST_GOALS_DESCRIPTION = "列出目标与进度。用户问有哪些目标、目标进展如何时用。"
GET_GOAL_DESCRIPTION = "看一个目标的里程碑、最近一次复盘与相关工单。"
UPDATE_GOAL_DESCRIPTION = "改目标的标题、截止，或暂停、恢复、取消目标。恢复时如果会重新启用用理智药或源石的定时任务，会请用户确认。"


class ListGoalsParams(BaseModel):
    """``list_goals`` 的参数（规格 §4.2）。"""

    model_config = ConfigDict(extra="forbid")

    status: list[GoalStatus] | None = None
    limit: int = Field(default=10, ge=1, le=20)


class GetGoalParams(BaseModel):
    """``get_goal`` 的参数（规格 §4.2）。"""

    model_config = ConfigDict(extra="forbid")

    goal_id: str = Field(min_length=1, max_length=36)


class UpdateGoalParams(GoalUpdate):
    """``update_goal`` 的参数：目标 id + :class:`GoalUpdate` 的字段。"""

    goal_id: str = Field(min_length=1, max_length=36)


def register_tools(registry: ToolRegistry) -> None:
    """注册 ``goals`` 组的四个工具；全部不进 REST 目录。"""

    for name, risk, description, params_model, handler, read_only in (
        (
            "create_goal",
            ToolRisk.CONDITIONAL,
            CREATE_GOAL_DESCRIPTION,
            GoalCreate,
            create_goal,
            False,
        ),
        (
            "list_goals",
            ToolRisk.SAFE,
            LIST_GOALS_DESCRIPTION,
            ListGoalsParams,
            list_goals,
            True,
        ),
        (
            "get_goal",
            ToolRisk.SAFE,
            GET_GOAL_DESCRIPTION,
            GetGoalParams,
            get_goal,
            True,
        ),
        (
            "update_goal",
            ToolRisk.CONDITIONAL,
            UPDATE_GOAL_DESCRIPTION,
            UpdateGoalParams,
            update_goal,
            False,
        ),
    ):
        registry.register(
            ToolDefinition(
                name=name,
                group="goals",
                risk=risk,
                description=description,
                params_model=params_model,
                handler=handler,
                read_only=read_only,
                rest_visible=False,
            )
        )


async def create_goal(params: GoalCreate, context: ToolContext) -> dict[str, Any]:
    """建目标并返回工具需要的摘要；业务在 ``GoalService.create``。"""
    detail = await _goal_service(context).create(params)
    payload = detail.model_dump(mode="json")
    return {
        "goal_id": payload["id"],
        "title": payload["title"],
        "status": payload["status"],
        "planning_ticket_id": payload["planning_ticket_id"],
        "next_review_at": payload["next_review_at"],
    }


async def list_goals(
    params: ListGoalsParams, context: ToolContext
) -> dict[str, Any]:
    """列出目标摘要；工具只取第一页。"""
    page = await _goal_service(context).list_goals(
        statuses=params.status, page=1, size=params.limit
    )
    return {"items": page.model_dump(mode="json")["items"]}


async def get_goal(params: GetGoalParams, context: ToolContext) -> dict[str, Any]:
    """目标详情：目标、里程碑、最近 5 张目标工单与 ``progress.last_snapshot``。"""
    detail = await _goal_service(context).get(params.goal_id)
    payload = detail.model_dump(mode="json")
    payload["tickets"] = payload["tickets"][:5]
    return payload


async def update_goal(
    params: UpdateGoalParams, context: ToolContext
) -> dict[str, Any]:
    """改目标并返回改过的字段名；恢复的确认由策略层负责。"""
    fields = params.model_fields_set - {"goal_id"}
    payload = GoalUpdate.model_validate(
        params.model_dump(exclude={"goal_id"}, exclude_unset=True)
    )
    detail = await _goal_service(context).update(params.goal_id, payload)
    changed = [name for name in ("title", "deadline", "status") if name in fields]
    return {
        "goal_id": detail.id,
        "status": detail.status,
        "changed": changed,
    }


def _goal_service(context: ToolContext) -> Any:
    """取装配在 ``app.state.goal_service`` 上的目标服务。"""
    service = getattr(
        getattr(getattr(context.request, "app", None), "state", None),
        "goal_service",
        None,
    )
    if service is None:
        raise AppError(ErrorCode.SERVICE_UNAVAILABLE, "目标服务尚未启动")
    return service
