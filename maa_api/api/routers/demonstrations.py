"""示教四个 REST 端点（M89 spec §4.3、§5 T6）。

路由只解析 query / body、取 ``app.state.demonstration_service`` 并映射错误；状态机、
编译与注册都在 :class:`~maa_api.agent.demonstrations.service.DemonstrationService`。
服务未装配或 ``demonstration.enabled=false`` 时四个端点都返回 503
``SERVICE_UNAVAILABLE``（spec §4.4）。没有 ``GET /{id}``：列表返回完整 ``steps``。
"""

from __future__ import annotations

from datetime import datetime
from typing import Any, Literal

from fastapi import APIRouter, Depends, Path, Query, Request
from pydantic import BaseModel, ConfigDict, Field

from maa_api.agent.demonstrations.service import DemonstrationService
from maa_api.api.deps import require_auth
from maa_api.api.errors import error_responses
from maa_api.domain.enums import DemonstrationStatus
from maa_api.domain.errors import AppError, ErrorCode
from maa_api.settings import get_settings

__all__ = [
    "CompiledTaskOut",
    "DemonstrationConvert",
    "DemonstrationConvertOut",
    "DemonstrationCreate",
    "DemonstrationOut",
    "DemonstrationPage",
    "DemonstrationStartOut",
    "DemonstrationStepOut",
    "DemonstrationStop",
    "router",
]

router = APIRouter(prefix="/api/agent", tags=["agent"])


class DemonstrationCreate(BaseModel):
    """开始录制的 body；整个 body 可省略，``title`` 为空时用服务端本地时间。"""

    model_config = ConfigDict(extra="forbid")

    title: str | None = Field(default=None, max_length=64)


class DemonstrationStop(BaseModel):
    """停止录制的 body；``discard=true`` 直接丢弃步骤。"""

    model_config = ConfigDict(extra="forbid")

    discard: bool = False


class DemonstrationConvert(BaseModel):
    """转换的 body；``register=true`` 才写资源。"""

    model_config = ConfigDict(extra="forbid")

    register: bool = False


class DemonstrationStepOut(BaseModel):
    """一条步骤；``at`` 原样透传服务写入的结尾 ``Z`` 的 UTC 字符串。"""

    seq: int
    action: Literal["click", "swipe", "long_press"]
    x: int | None = None
    y: int | None = None
    x1: int | None = None
    y1: int | None = None
    x2: int | None = None
    y2: int | None = None
    duration_ms: int | None = None
    delay_ms: int
    screen: list[int]
    screenshot_id: str | None = None
    result_screenshot_id: str | None = None
    at: str


class DemonstrationOut(BaseModel):
    """一次示教的完整状态（列表项与停止响应共用）。"""

    id: str
    title: str
    status: DemonstrationStatus
    steps: list[DemonstrationStepOut]
    converted_to: dict[str, Any] | None = None
    created_at: datetime
    updated_at: datetime


class DemonstrationStartOut(DemonstrationOut):
    """开始录制时多返回当前设备像素分辨率。"""

    screen: list[int]


class DemonstrationPage(BaseModel):
    """分页列表；``items`` 带完整 ``steps``。"""

    items: list[DemonstrationOut]
    total: int
    page: int
    size: int


class CompiledTaskOut(BaseModel):
    """一次转换的编译结果与（可选的）注册结果。"""

    entry: str
    tasks: dict[str, dict[str, Any]]
    chain: list[str]
    valid: bool
    errors: list[dict[str, str]]
    chain_errors: list[dict[str, str]]
    register: bool = Field(description="本次请求是否要求注册")
    registered: list[dict[str, str]]


class DemonstrationConvertOut(DemonstrationOut):
    """转换响应：示教本体 + 编译结果。"""

    compile: CompiledTaskOut


def _require_service(request: Request) -> DemonstrationService:
    """取示教服务；未装配或开关关闭都按 503 处理，且不带 ``details``。"""
    service = getattr(request.app.state, "demonstration_service", None)
    if service is None or not get_settings().demonstration.enabled:
        raise AppError(ErrorCode.SERVICE_UNAVAILABLE, "示教录制暂不可用")
    return service


@router.post(
    "/demonstrations",
    response_model=DemonstrationStartOut,
    status_code=201,
    summary="开始示教录制",
    responses=error_responses(
        "DEMONSTRATION_STATE_INVALID",
        "PIPELINE_ALREADY_RUNNING",
        "DEVICE_NOT_CONNECTED",
        "VALIDATION_ERROR",
        "SERVICE_UNAVAILABLE",
        "UNAUTHORIZED",
    ),
    dependencies=[Depends(require_auth)],
)
async def start_demonstration(
    request: Request,
    body: DemonstrationCreate | None = None,
) -> DemonstrationStartOut:
    """开始一次网页示教；同一时刻只允许一条 ``recording``，返回 ``screen``。"""
    service = _require_service(request)
    started = await service.start(title=body.title if body is not None else None)
    return DemonstrationStartOut.model_validate(started)


@router.get(
    "/demonstrations",
    response_model=DemonstrationPage,
    summary="读取示教列表",
    responses=error_responses(
        "INVALID_PARAMETER",
        "VALIDATION_ERROR",
        "SERVICE_UNAVAILABLE",
        "UNAUTHORIZED",
    ),
    dependencies=[Depends(require_auth)],
)
async def list_demonstrations(
    request: Request,
    status: str | None = Query(
        default=None, description="只看该状态；取 recording/recorded/converted/discarded"
    ),
    page: int = Query(default=1, ge=1, description="页码，从 1 开始"),
    size: int = Query(default=20, ge=1, le=50, description="每页条数，1–50"),
) -> DemonstrationPage:
    """按 ``created_at DESC, id DESC`` 分页；列表项带完整 ``steps``。"""
    service = _require_service(request)
    page_data = await service.list(status=status, page=page, size=size)
    return DemonstrationPage.model_validate(page_data)


@router.post(
    "/demonstrations/{id}/stop",
    response_model=DemonstrationOut,
    summary="停止示教录制",
    responses=error_responses(
        "DEMONSTRATION_NOT_FOUND",
        "DEMONSTRATION_STATE_INVALID",
        "VALIDATION_ERROR",
        "SERVICE_UNAVAILABLE",
        "UNAUTHORIZED",
    ),
    dependencies=[Depends(require_auth)],
)
async def stop_demonstration(
    request: Request,
    id: str = Path(min_length=1, max_length=36, description="示教 id"),
    body: DemonstrationStop | None = None,
) -> DemonstrationOut:
    """有步骤置 ``recorded``，零步或 ``discard=true`` 置 ``discarded``。"""
    service = _require_service(request)
    stopped = await service.stop(id, discard=body.discard if body is not None else False)
    return DemonstrationOut.model_validate(stopped)


@router.post(
    "/demonstrations/{id}/convert",
    response_model=DemonstrationConvertOut,
    summary="转换示教",
    responses=error_responses(
        "DEMONSTRATION_NOT_FOUND",
        "DEMONSTRATION_STATE_INVALID",
        "CUSTOM_TASK_INVALID",
        "RESOURCE_ASSET_CONFLICT",
        "RESOURCE_LOAD_FAILED",
        "VALIDATION_ERROR",
        "SERVICE_UNAVAILABLE",
        "UNAUTHORIZED",
    ),
    dependencies=[Depends(require_auth)],
)
async def convert_demonstration(
    request: Request,
    id: str = Path(min_length=1, max_length=36, description="示教 id"),
    body: DemonstrationConvert | None = None,
) -> DemonstrationConvertOut:
    """默认只编译并校验；``register=true`` 时一次性注册整条链并置 ``converted``。"""
    service = _require_service(request)
    converted = await service.convert(
        id, register=body.register if body is not None else False
    )
    return DemonstrationConvertOut.model_validate(converted)
