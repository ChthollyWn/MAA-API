"""目标端点（M82 规格 §4.3）。

四个操作与 §4.3 的表一一对应：目标分页、建立、详情与修改。建立目标会顺手建规划
工单与复盘定时任务，改状态会暂停 / 恢复 / 取消关联的计划与定时任务——这些判断都在
:class:`~maa_api.agent.goals.service.GoalService`；路由层只做分页范围、路径参数与
业务错误码的出口。请求与响应模型直接用 G2 的 ``models.py``（时间字段的 ``Z`` 后缀
在模型的序列化器里统一处理）。

服务未装配（lifespan 还在启动）时统一回 503 ``SERVICE_UNAVAILABLE``，与其它路由同款。
"""

from __future__ import annotations

from fastapi import APIRouter, Depends, Path, Query, Request, Response, status

from maa_api.agent.goals.models import (
    GoalCreate,
    GoalDetail,
    GoalPage,
    GoalUpdate,
)
from maa_api.agent.goals.service import GoalService
from maa_api.api.deps import require_auth
from maa_api.api.errors import error_responses
from maa_api.domain.enums import GoalStatus
from maa_api.domain.errors import AppError, ErrorCode

__all__ = ["router"]

router = APIRouter(prefix="/api/agent", tags=["agent"])

#: 列表分页上限；与 ``GET /api/agent/memories`` 同口径。
MAX_PAGE_SIZE = 200


def _service(request: Request) -> GoalService:
    service = getattr(request.app.state, "goal_service", None)
    if service is None:
        raise AppError(
            ErrorCode.SERVICE_UNAVAILABLE, "目标服务还在启动中，请稍后重试"
        )
    return service


@router.get(
    "/goals",
    response_model=GoalPage,
    summary="分页读取目标",
    responses=error_responses(
        "INVALID_PAGINATION", "SERVICE_UNAVAILABLE", "UNAUTHORIZED"
    ),
    dependencies=[Depends(require_auth)],
)
async def list_goals(
    request: Request,
    statuses: list[GoalStatus] | None = Query(
        None, alias="status", description="只看这些状态，可重复表示并集"
    ),
    page: int = Query(1, description="页码，从 1 开始"),
    size: int = Query(20, description="每页条数，1–200"),
) -> GoalPage:
    """按状态过滤分页，``updated_at`` 倒序；不传 ``status`` 时返回全部状态。"""
    if page < 1 or size < 1 or size > MAX_PAGE_SIZE:
        raise AppError(
            ErrorCode.INVALID_PAGINATION,
            f"page 不能小于 1，size 需在 1–{MAX_PAGE_SIZE} 之间",
        )
    return await _service(request).list_goals(
        statuses=statuses, page=page, size=size
    )


@router.post(
    "/goals",
    response_model=GoalDetail,
    status_code=status.HTTP_201_CREATED,
    summary="新建目标",
    responses={
        **error_responses(
            "VALIDATION_ERROR",
            "SCHEDULE_CRON_INVALID",
            "AGENT_DISABLED",
            "SERVICE_UNAVAILABLE",
            "UNAUTHORIZED",
        ),
        201: {
            "description": "Created",
            "headers": {
                "Location": {
                    "description": "新目标的 URI",
                    "schema": {"type": "string"},
                }
            },
        },
    },
    dependencies=[Depends(require_auth)],
)
async def create_goal(
    payload: GoalCreate, request: Request, response: Response
) -> GoalDetail:
    """建目标、规划工单与复盘 job；成功时带 ``Location`` 指向新目标。"""
    detail = await _service(request).create(payload)
    response.headers["Location"] = f"/api/agent/goals/{detail.id}"
    return detail


@router.get(
    "/goals/{goal_id}",
    response_model=GoalDetail,
    summary="读取目标详情",
    responses=error_responses(
        "GOAL_NOT_FOUND", "SERVICE_UNAVAILABLE", "UNAUTHORIZED"
    ),
    dependencies=[Depends(require_auth)],
)
async def get_goal(
    request: Request,
    goal_id: str = Path(min_length=1, max_length=36, description="目标 id"),
) -> GoalDetail:
    """目标本体、里程碑与最近 20 张相关工单；不存在报 404 ``GOAL_NOT_FOUND``。"""
    return await _service(request).get(goal_id)


@router.put(
    "/goals/{goal_id}",
    response_model=GoalDetail,
    summary="修改目标",
    responses=error_responses(
        "GOAL_NOT_FOUND",
        "GOAL_ENDED",
        "VALIDATION_ERROR",
        "SERVICE_UNAVAILABLE",
        "UNAUTHORIZED",
    ),
    dependencies=[Depends(require_auth)],
)
async def update_goal(
    payload: GoalUpdate,
    request: Request,
    goal_id: str = Path(min_length=1, max_length=36, description="目标 id"),
) -> GoalDetail:
    """改标题、截止或状态（暂停 / 恢复 / 取消）；已结束的目标报 409 ``GOAL_ENDED``。

    恢复不做确认：REST 是用户本人在操作（规格 §4.3）。
    """
    return await _service(request).update(goal_id, payload)
