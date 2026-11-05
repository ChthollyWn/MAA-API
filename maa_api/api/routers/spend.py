"""消耗与简报端点（M84 规格 §4.3）。

两条端点：``GET /api/agent/spend`` 读消耗汇总（今日三项、按日用量与今天拦下的
提交），``POST /api/agent/brief/run`` 立即生成一份每日简报。响应模型写在本文件里
（04 §1 允许，形状会进 OpenAPI 与前端生成类型）；时间字段由 :class:`SpendGuard`
的汇总结果统一渲染成 ISO 8601 的 ``...Z``。

`since` 缺省为今天（消耗日）往前 6 天，即含今天共 7 天；晚于今天或跨度超过 90 天
报 400 ``INVALID_PARAMETER``。服务未装配（lifespan 还在启动）时统一回 503
``SERVICE_UNAVAILABLE``，与其它路由同款。
"""

from __future__ import annotations

from datetime import date
from typing import Any, Literal

from fastapi import APIRouter, Depends, Query, Request, status
from pydantic import BaseModel

from maa_api.agent.spend import spend_day_window
from maa_api.api.deps import require_auth
from maa_api.api.errors import error_responses
from maa_api.domain.errors import AppError, ErrorCode

__all__ = [
    "BriefRunResult",
    "MAX_SPEND_RANGE_DAYS",
    "SpendBlock",
    "SpendDayRow",
    "SpendKindUsage",
    "SpendSummary",
    "router",
]

router = APIRouter(prefix="/api/agent", tags=["agent"])

#: ``days`` 的最长跨度，含首尾（spec §4.3）。
MAX_SPEND_RANGE_DAYS = 90


class SpendKindUsage(BaseModel):
    """今日一种资源的用量与上限（§4.3）；``limit=-1`` 时 ``remaining`` 为 null。"""

    kind: Literal["medicine", "stone", "expedite"]
    label: str
    used: int
    limit: int
    remaining: int | None


class SpendDayRow(BaseModel):
    """某一消耗日的三种资源用量（§4.3）；日期为 ``YYYY-MM-DD``。"""

    date: str
    medicine: int
    stone: int
    expedite: int


class SpendBlock(BaseModel):
    """今天被上限拦下的一次提交（§4.3）；``at`` 为 ISO 8601 带 ``Z``。"""

    at: str
    source: Literal["agent", "scheduled"]
    kind: Literal["medicine", "stone", "expedite"]
    requested: int
    used: int
    limit: int
    ref: str | None


class SpendSummary(BaseModel):
    """``GET /api/agent/spend`` 的响应体（§4.3）。"""

    date: str
    day_start: str
    day_end: str
    today: list[SpendKindUsage]
    days: list[SpendDayRow]
    blocked_today: list[SpendBlock]
    capped_sources: list[str]


class BriefRunResult(BaseModel):
    """``POST /api/agent/brief/run`` 的响应体（§4.3、§4.5.9）。"""

    ticket_id: str
    artifact_id: str


def _spend_guard(request: Request) -> Any:
    guard = getattr(request.app.state, "spend_guard", None)
    if guard is None:
        raise AppError(
            ErrorCode.SERVICE_UNAVAILABLE, "消耗护栏还在启动中，请稍后重试"
        )
    return guard


def _brief_service(request: Request) -> Any:
    service = getattr(request.app.state, "brief_service", None)
    if service is None:
        raise AppError(
            ErrorCode.SERVICE_UNAVAILABLE, "简报服务还在启动中，请稍后重试"
        )
    return service


def _require_ticket_service(request: Request) -> None:
    """``run_now`` 要建 analyst 工单；缺工单服务按服务未装配回 503。"""

    if getattr(request.app.state, "ticket_service", None) is None:
        raise AppError(
            ErrorCode.SERVICE_UNAVAILABLE, "工单服务还在启动中，请稍后重试"
        )


def _validate_since(since: date) -> None:
    """``since`` 不能晚于今天（消耗日），且与今天之间最多 90 天（含首尾）。"""

    today = spend_day_window()[2]
    if since > today:
        raise AppError(
            ErrorCode.INVALID_PARAMETER,
            f"since 不能晚于今天（{today.isoformat()}）",
            {"since": since.isoformat(), "today": today.isoformat()},
        )
    days = (today - since).days + 1
    if days > MAX_SPEND_RANGE_DAYS:
        raise AppError(
            ErrorCode.INVALID_PARAMETER,
            f"since 与今天之间最多 {MAX_SPEND_RANGE_DAYS} 天（含首尾）",
            {
                "since": since.isoformat(),
                "today": today.isoformat(),
                "days": days,
                "max_days": MAX_SPEND_RANGE_DAYS,
            },
        )


@router.get(
    "/spend",
    response_model=SpendSummary,
    summary="读取消耗汇总",
    responses=error_responses(
        "INVALID_PARAMETER", "SERVICE_UNAVAILABLE", "UNAUTHORIZED"
    ),
    dependencies=[Depends(require_auth)],
)
async def summarize_spend(
    request: Request,
    since: date | None = Query(
        None,
        description="起始消耗日（YYYY-MM-DD），缺省今天往前 6 天；含今天最多 90 天",
    ),
) -> SpendSummary:
    """今日三项用量、``since`` 起按日用量、今天拦下的提交与受限来源。"""

    guard = _spend_guard(request)
    if since is not None:
        _validate_since(since)
    return SpendSummary.model_validate(await guard.summary(since=since))


@router.post(
    "/brief/run",
    response_model=BriefRunResult,
    status_code=status.HTTP_202_ACCEPTED,
    summary="立即生成每日简报",
    responses=error_responses("SERVICE_UNAVAILABLE", "UNAUTHORIZED"),
    dependencies=[Depends(require_auth)],
)
async def run_brief(request: Request) -> BriefRunResult:
    """建一份简报产物与 analyst 工单并返回两个 id（202，不受一天一张限制）。"""

    service = _brief_service(request)
    _require_ticket_service(request)
    result = await service.run_now()
    return BriefRunResult(
        ticket_id=str(result.ticket_id), artifact_id=str(result.artifact_id)
    )
