"""Read-only resource lookup endpoints (docs/05 §6.13)."""

from __future__ import annotations

import json
import platform
import re
import threading
from dataclasses import dataclass
from pathlib import Path
from typing import Any
from urllib.parse import quote

from fastapi import APIRouter, Depends, Query, Request
from fastapi.responses import FileResponse
from pydantic import BaseModel, ConfigDict, Field
from sqlalchemy.ext.asyncio import AsyncSession

from maa_api.api.deps import get_session, require_auth
from maa_api.api.errors import error_responses
from maa_api.db.models import ResourceAsset, utcnow
from maa_api.db.repositories.resource import ResourceAssetRepository
from maa_api.domain.enums import ResourceAssetKind
from maa_api.domain.errors import AppError, ErrorCode
from maa_api.services.resource_service import _asset_wire
from maa_api.settings import get_settings


router = APIRouter(
    prefix="/api/resources",
    tags=["resources"],
    dependencies=[Depends(require_auth)],
)

REPO_ROOT = Path(__file__).resolve().parents[3]
ITEMS_ENDPOINT = "/api/resources/items"
_ICON_ID_PATTERN = re.compile(r"^[A-Za-z0-9_.-]+$")


class ItemOut(BaseModel):
    """An item that can be selected for ``Fight.drops``."""

    item_id: str
    name: str
    icon_url: str | None


class CopilotAssetOut(BaseModel):
    """Saved, validated Copilot metadata for queue editors."""

    id: str
    kind: str
    name: str
    description: str | None
    meta: dict[str, Any] | None
    enabled: bool
    checksum: str
    created_at: str | None
    updated_at: str | None


class CopilotAssetPageOut(BaseModel):
    items: list[CopilotAssetOut]
    total: int
    page: int = Field(ge=1)
    size: int = Field(ge=1, le=200)


class RetainRequest(BaseModel):
    """「保留」开关（规格 §9.11）：被保留的作业不会被每日清理删掉。"""

    model_config = ConfigDict(extra="forbid")

    retain: bool


@dataclass(frozen=True, slots=True)
class _Item:
    item_id: str
    name: str
    icon_filename: str | None


@dataclass(frozen=True, slots=True)
class _IndexCache:
    path: Path
    signature: tuple[int, int, int, int]
    items: tuple[_Item, ...]


_CACHE_LOCK = threading.Lock()
_INDEX_CACHE: _IndexCache | None = None


def _resource_root() -> Path:
    """Return the configured MaaCore resource directory without loading MaaCore."""
    configured = get_settings().maa_core_path
    if configured:
        core_path = Path(configured).expanduser()
        if not core_path.is_absolute():
            core_path = REPO_ROOT / core_path
    else:
        folder = {"Darwin": "Darwin", "Linux": "Linux", "Windows": "Win32"}.get(
            platform.system(), platform.system()
        )
        core_path = REPO_ROOT / "resource" / "lib" / "maa" / folder
    return core_path / "resource"


def _resource_error() -> AppError:
    return AppError(
        ErrorCode.RESOURCE_LOAD_FAILED,
        "读不到物品索引，请确认资源目录完整后重试",
        {"resource": "item_index.json"},
    )


def _file_signature(path: Path) -> tuple[int, int, int, int]:
    stat = path.stat()
    if not path.is_file():
        raise OSError("item index is not a file")
    return stat.st_dev, stat.st_ino, stat.st_size, stat.st_mtime_ns


def _icon_path(resource_root: Path, icon_filename: str | None) -> Path | None:
    """Resolve only a basename from an index record, confined to template/items."""
    if icon_filename is None or not _ICON_ID_PATTERN.fullmatch(icon_filename):
        return None
    if icon_filename in {".", ".."}:
        return None
    suffix = Path(icon_filename).suffix
    if suffix and suffix.lower() != ".png":
        return None

    try:
        resource_resolved = resource_root.resolve()
        items_directory = resource_root / "template" / "items"
        items_resolved = items_directory.resolve()
        items_resolved.relative_to(resource_resolved)
    except (OSError, RuntimeError, ValueError):
        return None

    try:
        filename = icon_filename if suffix else f"{icon_filename}.png"
        candidate = (items_directory / filename).resolve()
        candidate.relative_to(items_resolved)
        if not candidate.is_file():
            return None
    except (OSError, RuntimeError, ValueError):
        return None
    return candidate


def _parse_index(payload: Any, resource_root: Path) -> tuple[_Item, ...]:
    if not isinstance(payload, dict):
        raise _resource_error()

    items: list[_Item] = []
    for item_id, record in payload.items():
        if not isinstance(item_id, str) or not item_id or not isinstance(record, dict):
            raise _resource_error()
        name = record.get("name")
        if not isinstance(name, str) or not name.strip():
            raise _resource_error()

        raw_icon_filename = record.get("icon")
        icon_filename = raw_icon_filename if isinstance(raw_icon_filename, str) else None
        items.append(_Item(item_id, name, icon_filename))

    # Sorting by display name then key makes the response stable regardless of JSON key order.
    return tuple(sorted(items, key=lambda item: (item.name.casefold(), item.item_id)))


def _load_index() -> tuple[Path, tuple[_Item, ...]]:
    global _INDEX_CACHE

    resource_root = _resource_root()
    path = resource_root / "item_index.json"
    try:
        resolved_root = resource_root.resolve()
        resolved_path = path.resolve()
        resolved_path.relative_to(resolved_root)
        signature = _file_signature(resolved_path)
    except (OSError, RuntimeError, ValueError) as exc:
        raise _resource_error() from exc

    with _CACHE_LOCK:
        cached = _INDEX_CACHE
        if cached is not None and cached.path == resolved_path and cached.signature == signature:
            return resolved_path.parent, cached.items

        try:
            payload = json.loads(resolved_path.read_text(encoding="utf-8"))
            # A replacement during the read must not leave a mismatched signature/data pair.
            after_read = _file_signature(resolved_path)
            if after_read != signature:
                signature = after_read
                payload = json.loads(resolved_path.read_text(encoding="utf-8"))
                signature = _file_signature(resolved_path)
            items = _parse_index(payload, resolved_root)
        except (OSError, UnicodeDecodeError, json.JSONDecodeError, RuntimeError) as exc:
            raise _resource_error() from exc
        except AppError:
            raise

        _INDEX_CACHE = _IndexCache(resolved_path, signature, items)
        return resolved_root, items


@router.get(
    "/items",
    response_model=list[ItemOut],
    summary="列出 Fight.drops 可用物品",
    description=(
        "列出当前内核物品索引里的物品，按名称与物品 ID 稳定排序。\n\n"
        "- `icon_url` 指向同源图片端点，对应图片不可用时为 `null`\n"
        "- 索引缺失或无效时返回 `RESOURCE_LOAD_FAILED`"
    ),
    responses=error_responses("RESOURCE_LOAD_FAILED"),
)
async def list_items() -> list[ItemOut]:
    resource_root, items = _load_index()
    return [
        ItemOut(
            item_id=item.item_id,
            name=item.name,
            icon_url=(
                f"{ITEMS_ENDPOINT}/icon?item_id={quote(item.item_id, safe='')}"
                if _icon_path(resource_root, item.icon_filename) is not None
                else None
            ),
        )
        for item in items
    ]


@router.get(
    "/copilots",
    response_model=CopilotAssetPageOut,
    summary="列出已保存的 Copilot 作业",
    description=(
        "返回已校验作业的元数据，不含作业 JSON 内容；"
        "供流水线与定时任务编辑器选择 `asset_id`。"
    ),
    responses=error_responses("SERVICE_UNAVAILABLE", "UNAUTHORIZED"),
)
async def list_copilot_assets(
    request: Request,
    page: int = Query(default=1, ge=1),
    size: int = Query(default=20, ge=1, le=200),
    q: str | None = Query(default=None, max_length=128),
) -> dict[str, Any]:
    service = getattr(request.app.state, "resource_service", None)
    if service is None:
        raise AppError(ErrorCode.SERVICE_UNAVAILABLE, "资源功能还在启动中，请稍后重试")
    return await service.list_copilots(page=page, size=size, q=q)


@router.get(
    "/items/icon",
    response_class=FileResponse,
    summary="读取索引中物品的图标",
    description="返回物品索引中该物品的 PNG 图标；索引里没有该物品或图标文件缺失时返回 `RESOURCE_ASSET_NOT_FOUND`。",
    responses={
        **error_responses("RESOURCE_ASSET_NOT_FOUND", "RESOURCE_LOAD_FAILED"),
        200: {"description": "Successful Response", "content": {"image/png": {}}},
    },
)
async def get_item_icon(item_id: str = Query(..., min_length=1)) -> FileResponse:
    resource_root, items = _load_index()
    item = next((entry for entry in items if entry.item_id == item_id), None)
    icon_path = _icon_path(
        resource_root, item.icon_filename if item is not None else None
    )
    if icon_path is None:
        raise AppError(
            ErrorCode.RESOURCE_ASSET_NOT_FOUND,
            "物品不存在或没有可用图标，请核对物品 ID 或刷新物品列表",
        )
    return FileResponse(icon_path, media_type="image/png")


# ---------------------------------------------------------------------------
# 作业资源（M29，规格 §9.5 / §9.10）
#
# 读写直接落 ``resource_asset``：写入侧仍只走 Agent 工具（``upload_copilot`` 等，
# docs/05 §6.14），这三条只做「看 / 删 / 保留」。作业 JSON 的读法与
# ``ResourceService`` 一致：小作业在 ``content`` 列，大作业在 ``path`` 指向的文件。
# ---------------------------------------------------------------------------


def _copilot_service(request: Request) -> Any:
    service = getattr(request.app.state, "resource_service", None)
    if service is None:
        raise AppError(ErrorCode.SERVICE_UNAVAILABLE, "资源功能还在启动中，请稍后重试")
    return service


def _copilot_asset_root(request: Request) -> Path:
    """作业文件的根目录：先问资源服务，再退回 ``_resource_root()``。"""
    service = getattr(request.app.state, "resource_service", None)
    root = getattr(service, "resource_root", None)
    if isinstance(root, Path):
        return root
    return _resource_root()


def _copilot_not_found() -> AppError:
    return AppError(
        ErrorCode.RESOURCE_ASSET_NOT_FOUND, "Copilot 作业不存在，请刷新列表"
    )


def _copilot_file_error() -> AppError:
    return AppError(
        ErrorCode.RESOURCE_LOAD_FAILED,
        "Copilot 作业文件缺失或损坏，请重新上传",
    )


def _copilot_asset_file(root: Path, relative: str | None) -> Path | None:
    """``asset.path`` 解析到根目录之内才返回，越界或缺失一律 ``None``。"""
    if not relative:
        return None
    try:
        file_path = (root / relative).resolve()
        file_path.relative_to(root)
    except (OSError, RuntimeError, ValueError):
        return None
    return file_path


def _copilot_content(asset: ResourceAsset, root: Path) -> Any:
    if asset.content is not None:
        return asset.content
    file_path = _copilot_asset_file(root, asset.path)
    if file_path is None:
        raise _copilot_file_error()
    try:
        return json.loads(file_path.read_text(encoding="utf-8"))
    except (OSError, UnicodeDecodeError, json.JSONDecodeError, ValueError) as exc:
        raise _copilot_file_error() from exc


async def _copilot_asset(
    session: AsyncSession, asset_id: str
) -> ResourceAsset | None:
    asset = await ResourceAssetRepository(session).get(asset_id)
    if asset is None or str(asset.kind) != ResourceAssetKind.COPILOT.value:
        return None
    return asset


@router.get(
    "/copilots/{asset_id}",
    response_model=None,
    summary="读取单个 Copilot 作业（含作业 JSON）",
    description=(
        "返回该作业的资源 wire 与 `content`（作业 JSON 原文），供「作业」页查看与下载。\n\n"
        "- 小作业的 `content` 在库里，大作业从 `agent-assets/copilot/` 下的文件读\n"
        "- 不存在、不是 Copilot 作业返回 `RESOURCE_ASSET_NOT_FOUND`\n"
        "- 文件缺失或损坏返回 `RESOURCE_LOAD_FAILED`"
    ),
    responses=error_responses(
        "RESOURCE_ASSET_NOT_FOUND",
        "RESOURCE_LOAD_FAILED",
        "SERVICE_UNAVAILABLE",
        "UNAUTHORIZED",
    ),
)
async def get_copilot_asset(
    asset_id: str,
    request: Request,
    session: AsyncSession = Depends(get_session),
) -> dict[str, Any]:
    _copilot_service(request)
    asset = await _copilot_asset(session, asset_id)
    if asset is None:
        raise _copilot_not_found()
    content = _copilot_content(asset, _copilot_asset_root(request))
    return _asset_wire(asset, include_content=True, content=content)


@router.delete(
    "/copilots/{asset_id}",
    summary="删除一个 Copilot 作业",
    description=(
        "删除作业资源记录；大作业落盘的文件一并清掉。"
        "不存在、不是 Copilot 作业返回 `RESOURCE_ASSET_NOT_FOUND`。"
    ),
    responses=error_responses(
        "RESOURCE_ASSET_NOT_FOUND", "SERVICE_UNAVAILABLE", "UNAUTHORIZED"
    ),
)
async def delete_copilot_asset(
    asset_id: str,
    request: Request,
    session: AsyncSession = Depends(get_session),
) -> dict[str, Any]:
    _copilot_service(request)
    repo = ResourceAssetRepository(session)
    asset = await _copilot_asset(session, asset_id)
    if asset is None:
        raise _copilot_not_found()
    file_path = _copilot_asset_file(_copilot_asset_root(request), asset.path)
    await repo.delete(asset_id)
    await session.commit()
    if file_path is not None:
        file_path.unlink(missing_ok=True)
    return {"id": asset_id, "removed": True}


@router.post(
    "/copilots/{asset_id}/retain",
    response_model=None,
    summary="设置 Copilot 作业是否保留",
    description=(
        "写入 `meta.retain`（规格 §9.11）：被保留的作业不会被每日清理删掉，可以再取消。"
        "返回更新后的资源 wire（不含 `content`）。"
    ),
    responses=error_responses(
        "RESOURCE_ASSET_NOT_FOUND", "SERVICE_UNAVAILABLE", "UNAUTHORIZED"
    ),
)
async def retain_copilot_asset(
    asset_id: str,
    payload: RetainRequest,
    request: Request,
    session: AsyncSession = Depends(get_session),
) -> dict[str, Any]:
    _copilot_service(request)
    repo = ResourceAssetRepository(session)
    asset = await _copilot_asset(session, asset_id)
    if asset is None:
        raise _copilot_not_found()
    # JSON 列的原地修改不被 ORM 追踪（docs/04 §7），整列赋值写回。
    meta = dict(asset.meta or {})
    meta["retain"] = bool(payload.retain)
    asset.meta = meta
    asset.updated_at = utcnow()
    await session.merge(asset)
    await session.commit()
    stored = await repo.get(asset_id)
    if stored is None:  # pragma: no cover - 刚提交的行不会凭空消失
        raise _copilot_not_found()
    return _asset_wire(stored, include_content=False)

