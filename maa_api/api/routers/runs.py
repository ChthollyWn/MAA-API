"""代理运行的只读查询（M66 规格 §4.3.2；M68 规格 §4.3 加 spans 端点）。

三条端点都从 ``app.state.run_service`` 取启动时装配好的 :class:`RunService`；服务
尚未装配（进程启动中、或测试只装了路由）返回 503 ``SERVICE_UNAVAILABLE``。分页参数
的边界在路由层判断（400 ``INVALID_PAGINATION``），查询本身交给服务层；运行 id 不存在
时由 ``RunService.get_tree`` 抛 404 ``RUN_NOT_FOUND``，路由不重复判断。

``GET /{run_id}/spans`` 按运行的 ``root_run_id`` 取**整条** trace：子运行 id 也返回
根运行那一整条（前端详情页不分叉）。span 只读 ``trace_span`` 表，追踪关闭或已按
保留策略清理时返回空 ``items``；超过 :data:`MAX_TRACE_SPANS` 时多出的丢掉并置
``truncated=true``。

``ticket_id`` 在工单功能上线前恒为空：传了就返回空页而不是忽略，免得前端误以为
过滤生效。
"""

from __future__ import annotations

from datetime import UTC, datetime
from typing import Any

from fastapi import APIRouter, Depends, Path, Query, Request
from pydantic import BaseModel

from maa_api.agent.runs import RunService
from maa_api.api.deps import require_auth
from maa_api.api.errors import error_responses
from maa_api.api.schemas.agent_v3 import RunDetail, RunPage, run_detail, run_summary
from maa_api.db.models import TraceSpan
from maa_api.db.repositories.trace import TraceSpanRepository
from maa_api.domain.errors import AppError, ErrorCode

__all__ = ["RunSpansResponse", "SpanOut", "router"]

#: 一次响应里最多的 span 条数：再多的 trace 前端也画不动，超出的截断并标注。
MAX_TRACE_SPANS = 5000


class SpanOut(BaseModel):
    """一个 span 的对外形状（M68 规格 §4.3）；时间与 ``RunSummary`` 一样带 ``Z``。"""

    span_id: str
    parent_span_id: str | None
    run_id: str | None
    name: str
    kind: str
    status: str
    attributes: dict[str, Any]
    started_at: str
    ended_at: str | None
    duration_ms: int | None


class RunSpansResponse(BaseModel):
    """运行所在的整条 trace：按 ``(started_at, id)`` 升序，最多 :data:`MAX_TRACE_SPANS`。"""

    trace_id: str
    items: list[SpanOut]
    truncated: bool


def _utc_z(value: datetime) -> str:
    """把库里的 naive UTC 时间渲染成 ``...Z``（与 ``RunSummary`` 同口径）。"""
    if value.tzinfo is None:
        value = value.replace(tzinfo=UTC)
    return value.astimezone(UTC).isoformat().replace("+00:00", "Z")


def _span_out(span: TraceSpan) -> SpanOut:
    return SpanOut(
        span_id=span.span_id,
        parent_span_id=span.parent_span_id,
        run_id=span.run_id,
        name=span.name,
        kind=span.kind,
        status=span.status,
        attributes=span.attributes or {},
        started_at=_utc_z(span.started_at),
        ended_at=None if span.ended_at is None else _utc_z(span.ended_at),
        duration_ms=span.duration_ms,
    )


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


@router.get(
    "/{run_id}/spans",
    response_model=RunSpansResponse,
    summary="读取运行所在的整条追踪",
    responses=error_responses("RUN_NOT_FOUND", "UNAUTHORIZED"),
    dependencies=[Depends(require_auth)],
)
async def list_run_spans(
    request: Request,
    run_id: str = Path(min_length=1, max_length=36, description="运行 id"),
) -> RunSpansResponse:
    service = _run_service(request)
    run = await service.get(run_id)
    if run is None:
        raise AppError(ErrorCode.RUN_NOT_FOUND, "没有这个运行", {"run_id": run_id})
    # trace_id 就是根运行的 id（SpanRecorder 写库时的口径），所以子运行 id 也取
    # 整条 trace；多取一条用来判断有没有被截断，返回前丢掉。
    async with service.session_factory() as session:
        spans = await TraceSpanRepository(session).list_by_trace(
            run.root_run_id, limit=MAX_TRACE_SPANS + 1
        )
    truncated = len(spans) > MAX_TRACE_SPANS
    return RunSpansResponse(
        trace_id=run.root_run_id,
        items=[_span_out(span) for span in spans[:MAX_TRACE_SPANS]],
        truncated=truncated,
    )
