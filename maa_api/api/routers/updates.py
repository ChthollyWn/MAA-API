"""Public update status, history and accepted update operations."""

from __future__ import annotations

import json
from typing import Any, Literal

from fastapi import APIRouter, Depends, Query, Request, Response, status
from pydantic import BaseModel, ConfigDict

from maa_api.api.deps import require_auth
from maa_api.api.errors import error_responses
from maa_api.db.models import UpdateRecord
from maa_api.domain.enums import ResourceChannel, UpdateStatus, UpdateTarget
from maa_api.domain.errors import AppError, ErrorCode
from maa_api.services.update_service import UpdateService
from maa_api.settings import get_settings

__all__ = ["router"]

router = APIRouter(prefix="/api", tags=["updates"])


class CoreUpdateRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    channel: str = "stable"
    force: bool = False
    version: str | None = None
    force_interrupt: bool = False


class ResourceUpdateRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    channel: str = "all"
    force: bool = False
    force_interrupt: bool = False
    reload_mode: Literal["wait", "force", "defer"] = "wait"


class GameUpdateRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    channel: str | None = None
    force: bool = False
    force_interrupt: bool = False


class CoreRollbackRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    force_interrupt: bool = False


def _service(request: Request) -> UpdateService:
    service = getattr(request.app.state, "update_service", None)
    if service is None:
        raise AppError(ErrorCode.SERVICE_UNAVAILABLE, "更新服务尚未启动")
    return service


def _record_wire(record: UpdateRecord, *, include_log: bool = False) -> dict[str, Any]:
    value = UpdateService._record_dict(record)
    if include_log:
        try:
            value["log"] = json.loads(record.log) if record.log else None
        except (TypeError, json.JSONDecodeError):
            value["log"] = record.log
    return value


def _validate_channel(value: str, allowed: set[str], label: str) -> str:
    if value not in allowed:
        raise AppError(
            ErrorCode.INVALID_PARAMETER,
            f"{label}取值无效",
            {"channel": value, "allowed": sorted(allowed)},
        )
    return value


def _invalid_pagination(message: str) -> AppError:
    return AppError(ErrorCode.INVALID_PAGINATION, message)


@router.get(
    "/updates/status",
    summary="检查内核、资源与游戏更新",
    responses=error_responses(
        "UPDATE_MANIFEST_UNAVAILABLE", "GAME_VERSION_UNKNOWN", "SERVICE_UNAVAILABLE", "UNAUTHORIZED"
    ),
    dependencies=[Depends(require_auth)],
)
async def update_status(
    request: Request,
    refresh: bool = Query(default=False),
) -> dict[str, Any]:
    return await _service(request).status(refresh=refresh)


@router.post(
    "/updates/core",
    status_code=status.HTTP_202_ACCEPTED,
    summary="受理 MaaCore 更新",
    responses=error_responses(
        "INVALID_PARAMETER",
        "UPDATE_ALREADY_RUNNING",
        "ALREADY_LATEST_VERSION",
        "UPDATE_BLOCKED_BY_PIPELINE",
        "UPDATE_QUEUE_BUSY_TIMEOUT",
        "SERVICE_UNAVAILABLE",
        "FORBIDDEN",
        "UNAUTHORIZED",
    ),
    dependencies=[Depends(require_auth)],
)
async def update_core(
    payload: CoreUpdateRequest,
    request: Request,
    response: Response,
) -> dict[str, Any]:
    _validate_channel(payload.channel, {"stable"}, "MaaCore channel")
    record = await _service(request).start(
        UpdateTarget.CORE,
        payload.model_dump(exclude_none=True),
        caller="manual",
    )
    response.headers["Location"] = f"/api/updates/{record.id}"
    return _record_wire(record)


@router.post(
    "/updates/resource",
    status_code=status.HTTP_202_ACCEPTED,
    summary="受理活动资源更新",
    responses=error_responses(
        "INVALID_PARAMETER",
        "UPDATE_ALREADY_RUNNING",
        "UPDATE_MANIFEST_UNAVAILABLE",
        "UPDATE_DISK_INSUFFICIENT",
        "UPDATE_BLOCKED_BY_PIPELINE",
        "UPDATE_QUEUE_BUSY_TIMEOUT",
        "SERVICE_UNAVAILABLE",
        "FORBIDDEN",
        "UNAUTHORIZED",
    ),
    dependencies=[Depends(require_auth)],
)
async def update_resource(
    payload: ResourceUpdateRequest,
    request: Request,
    response: Response,
) -> dict[str, Any]:
    _validate_channel(payload.channel, {item.value for item in ResourceChannel}, "resource channel")
    record = await _service(request).start(
        UpdateTarget.RESOURCE,
        payload.model_dump(),
        caller="manual",
    )
    response.headers["Location"] = f"/api/updates/{record.id}"
    return _record_wire(record)


@router.post(
    "/updates/game",
    status_code=status.HTTP_202_ACCEPTED,
    summary="受理游戏更新",
    responses=error_responses(
        "INVALID_PARAMETER",
        "UPDATE_ALREADY_RUNNING",
        "GAME_INSTALL_FAILED",
        "DEVICE_NOT_CONNECTED",
        "UPDATE_BLOCKED_BY_PIPELINE",
        "UPDATE_QUEUE_BUSY_TIMEOUT",
        "SERVICE_UNAVAILABLE",
        "FORBIDDEN",
        "UNAUTHORIZED",
    ),
    dependencies=[Depends(require_auth)],
)
async def update_game(
    payload: GameUpdateRequest,
    request: Request,
    response: Response,
) -> dict[str, Any]:
    channel = payload.channel or get_settings().channel.client_type
    _validate_channel(channel, {"Official", "Bilibili"}, "game channel")
    options = payload.model_dump(exclude={"channel"})
    options["channel"] = channel
    record = await _service(request).start(
        UpdateTarget.GAME,
        options,
        caller="manual",
    )
    response.headers["Location"] = f"/api/updates/{record.id}"
    return _record_wire(record)


@router.post(
    "/updates/core/rollback",
    status_code=status.HTTP_202_ACCEPTED,
    summary="受理 MaaCore 回滚",
    responses=error_responses(
        "UPDATE_ALREADY_RUNNING",
        "UPDATE_ROLLBACK_FAILED",
        "UPDATE_BLOCKED_BY_PIPELINE",
        "UPDATE_QUEUE_BUSY_TIMEOUT",
        "SERVICE_UNAVAILABLE",
        "FORBIDDEN",
        "UNAUTHORIZED",
    ),
    dependencies=[Depends(require_auth)],
)
async def rollback_core(
    request: Request,
    response: Response,
    payload: CoreRollbackRequest | None = None,
) -> dict[str, Any]:
    service = _service(request)
    # UpdateService owns the shared target lock and creates the pollable history row.
    record = await service.rollback_core(
        force_interrupt=bool(payload and payload.force_interrupt),
        caller="manual",
    )
    response.headers["Location"] = f"/api/updates/{record.id}"
    return _record_wire(record)


@router.get(
    "/updates",
    summary="分页查询更新历史",
    responses=error_responses("INVALID_PARAMETER", "INVALID_PAGINATION", "UNAUTHORIZED"),
    dependencies=[Depends(require_auth)],
)
async def list_updates(
    request: Request,
    target: str | None = Query(default=None),
    status_filter: str | None = Query(default=None, alias="status"),
    page: int = Query(default=1),
    size: int = Query(default=20),
) -> dict[str, Any]:
    if page < 1 or size < 1 or size > 200:
        raise _invalid_pagination("page 必须大于等于 1，size 必须在 1 到 200 之间")
    if target is not None and target not in {item.value for item in UpdateTarget}:
        raise AppError(ErrorCode.INVALID_PARAMETER, "target 取值无效", {"target": target})
    if status_filter is not None and status_filter not in {item.value for item in UpdateStatus}:
        raise AppError(
            ErrorCode.INVALID_PARAMETER,
            "status 取值无效",
            {"status": status_filter},
        )
    page_result = await _service(request).list(
        target=target,
        status=status_filter,
        page=page,
        size=size,
    )
    return {
        "items": [_record_wire(item) for item in page_result.items],
        "total": page_result.total,
        "page": page_result.page,
        "size": page_result.size,
    }


@router.get(
    "/updates/{update_id}",
    summary="读取更新进度与记录",
    responses=error_responses("UPDATE_NOT_FOUND", "UNAUTHORIZED"),
    dependencies=[Depends(require_auth)],
)
async def get_update(update_id: str, request: Request) -> dict[str, Any]:
    record = await _service(request).get(update_id)
    return _record_wire(record, include_log=True)


@router.post(
    "/updates/{update_id}/retry",
    status_code=status.HTTP_202_ACCEPTED,
    summary="受理失败更新重试",
    responses=error_responses(
        "UPDATE_NOT_FOUND", "UPDATE_NOT_CANCELLABLE", "UPDATE_ALREADY_RUNNING", "UNAUTHORIZED"
    ),
    dependencies=[Depends(require_auth)],
)
async def retry_update(
    update_id: str,
    request: Request,
    response: Response,
) -> dict[str, Any]:
    record = await _service(request).retry(update_id, caller="manual")
    response.headers["Location"] = f"/api/updates/{record.id}"
    return _record_wire(record)


@router.delete(
    "/updates/{update_id}",
    status_code=status.HTTP_202_ACCEPTED,
    summary="取消进行中的更新",
    responses=error_responses("UPDATE_NOT_FOUND", "UPDATE_NOT_CANCELLABLE", "UNAUTHORIZED"),
    dependencies=[Depends(require_auth)],
)
async def cancel_update(update_id: str, request: Request) -> dict[str, Any]:
    record = await _service(request).cancel(update_id)
    return _record_wire(record)
