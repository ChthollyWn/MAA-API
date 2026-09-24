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

from fastapi import APIRouter, Depends, Query
from fastapi.responses import FileResponse
from pydantic import BaseModel

from maa_api.api.deps import require_auth
from maa_api.api.errors import error_responses
from maa_api.domain.errors import AppError, ErrorCode
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
        "无法读取 MaaCore 物品索引",
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
        "读取当前 MaaCore `resource/item_index.json` 并按名称、物品 ID 稳定排序。"
        "`icon_url` 指向同源图片端点；对应图片不可用时为 `null`。索引缺失或无效时返回统一 API 错误。"
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
    "/items/icon",
    response_class=FileResponse,
    summary="读取索引中物品的图标",
    description="只返回 item_index.json 中登记且位于 `resource/template/items` 的 PNG 图标。",
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
            "物品不存在或没有可用图标",
        )
    return FileResponse(icon_path, media_type="image/png")
