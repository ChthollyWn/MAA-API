"""值守端点（M83 规格 §4.3）。

三个操作与 §4.3 的表一一对应：概况、告警分页与解除。概况的 ``mode`` 每请求现读
设置（与 ``WatchdogService`` 同一份 ``watchdog.mode``），``last_run_at`` 取服务的
进程内属性（重启后为空），``open_count`` 现查 ``watchdog_alert`` 里未解除的行数。
告警列表直接经 ``app.state.session_factory`` 读表，``resolved`` 默认只看未解除，
排序按 ``created_at`` 倒序；解除不存在的 id 报 404 ``WATCHDOG_ALERT_NOT_FOUND``。

``AlertOut`` 写在本文件里（04 §1 允许放在路由文件内，形状会进 OpenAPI 与前端
生成类型）；库里的时间是不带时区的 UTC，出参在这里补成 ``...Z``。服务未装配
（lifespan 还在启动）时统一回 503 ``SERVICE_UNAVAILABLE``，与其它路由同款。
"""

from __future__ import annotations

from datetime import UTC, datetime
from typing import Any, Literal

from fastapi import APIRouter, Depends, Path, Query, Request
from pydantic import BaseModel
from sqlalchemy import func, select

from maa_api.api.deps import require_auth
from maa_api.api.errors import error_responses
from maa_api.db.models import WatchdogAlert, utcnow
from maa_api.domain.enums import AlertActionStatus, AlertSeverity, AlertTargetKind
from maa_api.domain.errors import AppError, ErrorCode
from maa_api.settings import get_settings

__all__ = ["AlertOut", "AlertPage", "WatchdogStatusOut", "router"]

router = APIRouter(prefix="/api/agent", tags=["agent"])

#: 列表分页上限；与 ``GET /api/agent/goals`` 等同口径（04 §1）。
MAX_PAGE_SIZE = 200


class WatchdogStatusOut(BaseModel):
    """值守概况（§4.3）：模式、进程内上次巡检时间与未解除告警数。"""

    mode: Literal["record", "auto"]
    last_run_at: datetime | None
    open_count: int


class AlertOut(BaseModel):
    """一条值守告警（§4.3）；``evidence`` 保留检查写入的判定证据。"""

    id: int
    check: str
    severity: AlertSeverity
    target_kind: AlertTargetKind
    target_id: str | None
    message: str
    evidence: dict[str, Any]
    action: str | None
    action_status: AlertActionStatus | None
    created_at: datetime
    resolved_at: datetime | None


class AlertPage(BaseModel):
    """告警的分页信封（§4.3 / 04 §1）。"""

    items: list[AlertOut]
    total: int
    page: int
    size: int


def _service(request: Request) -> Any:
    service = getattr(request.app.state, "watchdog_service", None)
    if service is None:
        raise AppError(
            ErrorCode.SERVICE_UNAVAILABLE, "值守服务还在启动中，请稍后重试"
        )
    return service


def _session_factory(request: Request) -> Any:
    factory = getattr(request.app.state, "session_factory", None)
    if factory is None:
        raise AppError(
            ErrorCode.SERVICE_UNAVAILABLE, "值守服务还在启动中，请稍后重试"
        )
    return factory


def _utc(value: datetime | None) -> datetime | None:
    """库里的 naive UTC → 带时区的 UTC，Pydantic 会把它渲染成 ``...Z``。"""
    if value is None:
        return None
    if value.tzinfo is None:
        return value.replace(tzinfo=UTC)
    return value.astimezone(UTC)


def _validate_pagination(page: int, size: int) -> None:
    if page < 1 or size < 1 or size > MAX_PAGE_SIZE:
        raise AppError(
            ErrorCode.INVALID_PAGINATION,
            f"page 不能小于 1，size 需在 1–{MAX_PAGE_SIZE} 之间",
        )


def _alert_out(row: WatchdogAlert) -> AlertOut:
    return AlertOut(
        id=int(row.id),
        check=row.check,
        severity=AlertSeverity(row.severity),
        target_kind=AlertTargetKind(row.target_kind),
        target_id=row.target_id,
        message=row.message,
        evidence=dict(row.evidence or {}),
        action=row.action,
        action_status=(
            AlertActionStatus(row.action_status) if row.action_status else None
        ),
        created_at=_utc(row.created_at),
        resolved_at=_utc(row.resolved_at),
    )


@router.get(
    "/watchdog",
    response_model=WatchdogStatusOut,
    summary="读取值守概况",
    responses=error_responses("SERVICE_UNAVAILABLE", "UNAUTHORIZED"),
    dependencies=[Depends(require_auth)],
)
async def get_watchdog(request: Request) -> WatchdogStatusOut:
    """模式取设置、上次巡检取服务属性、未解除数现查（§4.3）。"""
    service = _service(request)
    mode = get_settings().watchdog.mode
    async with _session_factory(request)() as session:
        open_count = await session.scalar(
            select(func.count())
            .select_from(WatchdogAlert)
            .where(WatchdogAlert.resolved_at.is_(None))
        )
    return WatchdogStatusOut(
        mode=mode,
        last_run_at=_utc(service.last_run_at),
        open_count=int(open_count or 0),
    )


@router.get(
    "/watchdog/alerts",
    response_model=AlertPage,
    summary="分页读取值守告警",
    responses=error_responses(
        "INVALID_PAGINATION", "SERVICE_UNAVAILABLE", "UNAUTHORIZED"
    ),
    dependencies=[Depends(require_auth)],
)
async def list_watchdog_alerts(
    request: Request,
    resolved: bool = Query(
        default=False,
        description="false（默认）只看未解除；true 只看已解除",
    ),
    severity: AlertSeverity | None = Query(
        default=None, description="按级别过滤：info / warn / critical"
    ),
    page: int = Query(default=1, description="页码，从 1 开始"),
    size: int = Query(default=20, description="每页条数，1–200"),
) -> AlertPage:
    """按解除状态与级别过滤，``created_at`` 倒序分页（§4.3）。"""
    _validate_pagination(page, size)
    _service(request)
    conditions = [
        WatchdogAlert.resolved_at.is_not(None)
        if resolved
        else WatchdogAlert.resolved_at.is_(None)
    ]
    if severity is not None:
        conditions.append(WatchdogAlert.severity == severity.value)
    async with _session_factory(request)() as session:
        total = await session.scalar(
            select(func.count()).select_from(WatchdogAlert).where(*conditions)
        )
        result = await session.execute(
            select(WatchdogAlert)
            .where(*conditions)
            .order_by(WatchdogAlert.created_at.desc(), WatchdogAlert.id.desc())
            .offset((page - 1) * size)
            .limit(size)
        )
        rows = result.scalars().all()
    return AlertPage(
        items=[_alert_out(row) for row in rows],
        total=int(total or 0),
        page=page,
        size=size,
    )


@router.post(
    "/watchdog/alerts/{alert_id}/resolve",
    response_model=AlertOut,
    summary="解除一条值守告警",
    responses=error_responses(
        "WATCHDOG_ALERT_NOT_FOUND", "SERVICE_UNAVAILABLE", "UNAUTHORIZED"
    ),
    dependencies=[Depends(require_auth)],
)
async def resolve_watchdog_alert(
    request: Request,
    alert_id: int = Path(description="告警 id"),
) -> AlertOut:
    """写入 ``resolved_at``；重复解除保留首次时间，不存在的 id 报 404（§4.3）。"""
    _service(request)
    async with _session_factory(request)() as session:
        alert = await session.get(WatchdogAlert, alert_id)
        if alert is None:
            raise AppError(
                ErrorCode.WATCHDOG_ALERT_NOT_FOUND,
                "没有这条值守告警",
                {"alert_id": alert_id},
            )
        if alert.resolved_at is None:
            alert.resolved_at = utcnow()
            session.add(alert)
            await session.commit()
            await session.refresh(alert)
        return _alert_out(alert)
