"""Read-only screenshot archive and content-addressed image routes."""

from __future__ import annotations

import base64
import re
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from fastapi import APIRouter, Depends, Query, Response
from sqlalchemy.ext.asyncio import AsyncSession

from maa_api.api.deps import get_session, require_auth
from maa_api.api.errors import error_responses
from maa_api.db import session as db_session
from maa_api.db.models import Screenshot
from maa_api.db.repositories.log import ScreenshotRepository
from maa_api.domain.enums import ScreenshotTrigger
from maa_api.domain.errors import AppError, ErrorCode

__all__ = ["router"]

router = APIRouter(prefix="/api", tags=["screenshots"])
_SHA256_RE = re.compile(r"^[0-9a-f]{64}$")
_CACHE_CONTROL = "public, max-age=31536000, immutable"
_MIME_TYPES = {"jpeg": "image/jpeg", "jpg": "image/jpeg", "png": "image/png"}


def _root() -> Path:
    return Path(db_session.DB_PATH).parent.resolve()


def _record_path(screenshot: Screenshot) -> Path | None:
    root = _root()
    candidate = (root / screenshot.path).resolve()
    if not candidate.is_relative_to(root):
        return None
    return candidate


def _image_path(sha256: str, *, thumb: bool) -> Path | None:
    if not _SHA256_RE.fullmatch(sha256):
        return None
    root = _root()
    suffix = ".thumb.jpg" if thumb else ".jpg"
    candidate = (root / "image" / "screenshot" / sha256[:2] / f"{sha256}{suffix}").resolve()
    if not candidate.is_relative_to(root):
        return None
    return candidate


def _not_found(message: str = "截图不存在") -> AppError:
    return AppError(ErrorCode.SCREENSHOT_NOT_FOUND, message)


def _wire_list_item(item: Screenshot) -> dict[str, Any]:
    created_at = item.created_at
    if created_at.tzinfo is None:
        created_at = created_at.replace(tzinfo=timezone.utc)
    return {
        "id": item.id,
        "pipeline_id": item.pipeline_id,
        "task_id": item.task_id,
        "trigger": str(item.trigger),
        "backend": str(item.backend),
        "format": item.format,
        "width": item.width,
        "height": item.height,
        "size_bytes": item.size_bytes,
        "captured_at": created_at.timestamp(),
        "deleted_at": item.deleted_at.isoformat() if item.deleted_at else None,
    }


@router.get(
    "/screenshots",
    summary="查询截图列表",
    description="按流水线、触发原因和时间筛选截图，并以 page/size 分页。",
    responses=error_responses("INVALID_PAGINATION", "INVALID_PARAMETER"),
    dependencies=[Depends(require_auth)],
)
async def list_screenshots(
    pipeline_id: str | None = None,
    trigger: str | None = None,
    since: float | None = None,
    page: int = 1,
    size: int = 100,
    session: AsyncSession = Depends(get_session),
) -> dict[str, Any]:
    if page < 1 or size < 1 or size > 200:
        raise AppError(ErrorCode.INVALID_PAGINATION, "page 必须至少为 1，size 范围为 1 到 200")
    if trigger is not None and trigger not in {item.value for item in ScreenshotTrigger}:
        raise AppError(ErrorCode.INVALID_PARAMETER, f"未知截图 trigger: {trigger}")
    since_at = (
        datetime.fromtimestamp(since, timezone.utc).replace(tzinfo=None)
        if since is not None
        else None
    )
    result = await ScreenshotRepository(session).list_page(
        pipeline_id=pipeline_id,
        trigger=trigger,
        since=since_at,
        page=page,
        size=size,
    )
    return {
        "items": [_wire_list_item(item) for item in result.items],
        "total": result.total,
        "page": result.page,
        "size": result.size,
    }


@router.get(
    "/screenshots/{id}",
    summary="读取截图内容",
    description="返回截图图像字节；as=base64 时返回面向 agent 的 JSON 包装。",
    responses=error_responses(
        "SCREENSHOT_NOT_FOUND", "SCREENSHOT_EXPIRED", "INVALID_PARAMETER"
    ),
    dependencies=[Depends(require_auth)],
    response_model=None,
)
async def get_screenshot(
    id: str,
    as_: str | None = Query(default=None, alias="as"),
    session: AsyncSession = Depends(get_session),
) -> Response | dict[str, Any]:
    if as_ not in {None, "base64"}:
        raise AppError(ErrorCode.INVALID_PARAMETER, "as 仅支持 base64")
    item = await ScreenshotRepository(session).get(id)
    if item is None:
        raise _not_found()
    if item.deleted_at is not None:
        raise AppError(ErrorCode.SCREENSHOT_EXPIRED, "截图文件已过期清理")
    path = _record_path(item)
    if path is None or not path.is_file():
        raise _not_found("截图文件不存在")
    content = path.read_bytes()
    if as_ == "base64":
        captured_at = item.created_at
        if captured_at.tzinfo is None:
            captured_at = captured_at.replace(tzinfo=timezone.utc)
        return {
            "id": item.id,
            "format": item.format,
            "width": item.width,
            "height": item.height,
            "size_bytes": len(content),
            "captured_at": captured_at.timestamp(),
            "data": base64.b64encode(content).decode("ascii"),
        }
    return Response(
        content=content,
        media_type=_MIME_TYPES.get(item.format.lower(), "application/octet-stream"),
        headers={"Content-Length": str(len(content))},
    )


async def _get_addressed_image(sha256: str, *, thumb: bool) -> Response:
    path = _image_path(sha256, thumb=thumb)
    if path is None or not path.is_file():
        raise _not_found("图片不存在")
    try:
        content = path.read_bytes()
    except OSError:
        raise _not_found("图片不存在") from None
    return Response(
        content=content,
        media_type="image/jpeg",
        headers={"Cache-Control": _CACHE_CONTROL, "Content-Length": str(len(content))},
    )


@router.get(
    "/images/{sha256}/thumb",
    summary="读取截图缩略图",
    description="按内容哈希读取长边 320px 的 JPEG 缩略图，响应可长期缓存。",
    responses=error_responses("SCREENSHOT_NOT_FOUND"),
    dependencies=[Depends(require_auth)],
)
async def get_screenshot_thumb(sha256: str) -> Response:
    return await _get_addressed_image(sha256, thumb=True)


@router.get(
    "/images/{sha256}/full",
    summary="读取截图原图",
    description="按内容哈希读取 JPEG 原图，响应可长期缓存。",
    responses=error_responses("SCREENSHOT_NOT_FOUND"),
    dependencies=[Depends(require_auth)],
)
async def get_screenshot_full(sha256: str) -> Response:
    return await _get_addressed_image(sha256, thumb=False)
