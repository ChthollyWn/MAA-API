"""Visual settings endpoints backed by the lifespan-owned SettingService."""

from __future__ import annotations

from typing import Any

from fastapi import APIRouter, Depends, Query, Request
from pydantic import BaseModel, ConfigDict

from maa_api.api.deps import require_auth
from maa_api.api.errors import error_responses
from maa_api.domain.errors import AppError, ErrorCode
from maa_api.services.setting_service import SettingService
from maa_api.services.settings_schema import SETTINGS_SCHEMA

__all__ = ["router"]

router = APIRouter(prefix="/api", tags=["settings"])


class SettingUpdateRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    items: dict[str, Any]


class SettingResetRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    keys: list[str]


def _service(request: Request) -> SettingService:
    service = getattr(request.app.state, "setting_service", None)
    if service is None:
        raise AppError(ErrorCode.SERVICE_UNAVAILABLE, "设置服务尚未启动")
    return service


@router.get(
    "/settings",
    summary="读取生效配置",
    dependencies=[Depends(require_auth)],
)
async def get_settings(
    request: Request,
    group: str | None = Query(default=None),
) -> dict[str, Any]:
    return await _service(request).get(group=group)


@router.put(
    "/settings",
    summary="批量写入并热生效配置",
    responses=error_responses(
        "SETTING_KEY_UNKNOWN",
        "SETTING_VALUE_INVALID",
        "SETTING_READONLY",
        "SETTING_APPLY_FAILED",
    ),
    dependencies=[Depends(require_auth)],
)
async def update_settings(
    payload: SettingUpdateRequest,
    request: Request,
) -> dict[str, Any]:
    return await _service(request).update(payload.items)


@router.get(
    "/settings/schema",
    summary="读取设置项元信息",
    dependencies=[Depends(require_auth)],
)
async def get_settings_schema() -> dict[str, Any]:
    return {"items": [dict(item) for item in SETTINGS_SCHEMA], "total": len(SETTINGS_SCHEMA)}


@router.post(
    "/settings/reset",
    summary="清除设置项数据库覆盖",
    responses=error_responses("SETTING_KEY_UNKNOWN", "SETTING_READONLY"),
    dependencies=[Depends(require_auth)],
)
async def reset_settings(
    payload: SettingResetRequest,
    request: Request,
) -> dict[str, Any]:
    return await _service(request).reset(payload.keys)
