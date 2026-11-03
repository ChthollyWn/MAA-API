"""代理运行的只读查询（M66 规格 §4.3.2）。

两条端点都从 ``app.state.run_service`` 取启动时装配好的 :class:`RunService`；服务
尚未装配（进程启动中、或测试只装了路由）返回 503 ``SERVICE_UNAVAILABLE``。分页参数
的边界在路由层判断（400 ``INVALID_PAGINATION``），查询本身交给服务层；运行 id 不存在
时由 ``RunService.get_tree`` 抛 404 ``RUN_NOT_FOUND``，路由不重复判断。

``ticket_id`` 在工单功能上线前恒为空：传了就返回空页而不是忽略，免得前端误以为
过滤生效。
"""

from __future__ import annotations

from fastapi import APIRouter, Depends, Path, Query, Request

from maa_api.agent.runs import RunService
from maa_api.api.deps import require_auth
from maa_api.api.errors import error_responses
from maa_api.api.schemas.agent_v3 import RunDetail, RunPage, run_detail, run_summary
from maa_api.domain.errors import AppError, ErrorCode

__all__ = ["router"]

router = APIRouter(prefix="/api/agent/runs", tags=["agent"])


def _run_service(request: Request) -> RunService:
    """取运行服务；装配层还没设置时返回 503。"""
    service = getattr(request.app.state, "run_service", None)
    if service is None:
        raise AppError(ErrorCode.SERVICE_UNAVAILABLE, "运行记录还在启动中，请稍后重试")
    return service


@router.get(
    "",
    response_model=RunPage,
    summary="列出代理运行",
    responses=error_responses("INVALID_PAGINATION", "SERVICE_UNAVAILABLE", "UNAUTHORIZED"),
    dependencies=[Depends(require_auth)],
)
async def list_runs(
    request: Request,
    session_id: str | None = Query(None, description="只看这个会话的运行"),
    ticket_id: str | None = Query(
        None, description="只看这个工单的运行；工单功能上线前返回空列表"
    ),
    root_only: bool = Query(True, description="只列根运行（不含子运行）"),
    page: int = Query(1, description="页码，从 1 开始"),
    size: int = Query(20, description="每页条数，1–200"),
) -> RunPage:
    if page < 1 or size < 1 or size > 200:
        raise AppError(
            ErrorCode.INVALID_PAGINATION, "page 不能小于 1，size 需在 1–200 之间"
        )
    if ticket_id is not None:
        return RunPage(items=[], total=0, page=page, size=size)
    result = await _run_service(request).list_runs(
        session_id=session_id, root_only=root_only, page=page, size=size
    )
    return RunPage(
        items=[run_summary(run) for run in result.items],
        total=result.total,
        page=result.page,
        size=result.size,
    )


@router.get(
    "/{run_id}",
    response_model=RunDetail,
    summary="读取运行与子运行树",
    responses=error_responses("RUN_NOT_FOUND", "SERVICE_UNAVAILABLE", "UNAUTHORIZED"),
    dependencies=[Depends(require_auth)],
)
async def get_run(
    request: Request,
    run_id: str = Path(min_length=1, max_length=36, description="运行 id"),
) -> RunDetail:
    return run_detail(await _run_service(request).get_tree(run_id, max_depth=3))
