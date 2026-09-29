"""MaaCore 资源根、物品索引与图标路径（M23 媒体工具用）。

**有意重复**：这里的资源根 / 物品索引 / 图标路径规则与
:mod:`maa_api.api.routers.resources` 里的私有实现（``_resource_root`` / ``_load_index``
/ ``_icon_path``）是同一套规则的第二份拷贝 —— ``tests/api/test_resources_router.py``
直接 monkeypatch 那边的私有名与 ``_INDEX_CACHE``，把那份实现搬过来会破它的夹具。
两处必须保持一致：改这里的解析规则，就要同步改 ``api/routers/resources.py``。

与路由层的差别只有一处：本模块额外提供 :func:`iter_resource_roots`，按 MaaCore 的
真实加载链（基础层 → ``maa-layers/cache`` → ``maa-layers/custom``）给出资源层顺序，
供关卡 tile 数据这类需要跟随增量层的读取使用。
"""

from __future__ import annotations

import json
import platform
import re
import threading
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from maa_api.domain.errors import AppError, ErrorCode
from maa_api.settings import REPO_ROOT, get_settings

__all__ = [
    "find_by_name",
    "iter_resource_roots",
    "load_item_index",
    "resolve_icon_path",
    "resource_root",
]

_ICON_ID_PATTERN = re.compile(r"^[A-Za-z0-9_.-]+$")
#: MaaCore 资源更新时写进 ``resource/maa-layers/`` 的增量层目录名（main.py 的 boot
#: config 用的就是这两个）。``repo/`` 不是运行时层：它在更新时已被合并进
#: ``<maa_path>/resource``，加进来会让旧快照盖住新资源。
_LAYER_DIRECTORIES = ("cache", "custom")


@dataclass(frozen=True, slots=True)
class _Item:
    item_id: str
    name: str
    icon_filename: str | None


@dataclass(frozen=True, slots=True)
class _IndexCache:
    path: Path
    signature: tuple[int, int, int, int]
    items: dict[str, _Item]


_CACHE_LOCK = threading.Lock()
_INDEX_CACHE: _IndexCache | None = None


def resource_root() -> Path:
    """返回 MaaCore 的资源目录（``<maa_path>/resource``），不加载内核。"""
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


def iter_resource_roots() -> list[Path]:
    """按 MaaCore 的真实加载链给出资源层，**基础层在前、增量层在后**。

    后写的层覆盖先写的层，所以「哪一份生效」要用**反向遍历**（后面的先看）。目录不
    存在是常态（增量层只在资源更新后才有），本函数不检查存在性，调用方自己容忍。
    """
    layers_root = REPO_ROOT / "resource" / "maa-layers"
    return [
        resource_root(),
        *[layers_root / name / "resource" for name in _LAYER_DIRECTORIES],
    ]


def load_item_index() -> dict[str, dict[str, Any]]:
    """读取 ``<资源根>/item_index.json``，返回 ``{item_id: {name, icon_filename}}``。

    按文件签名（dev/ino/size/mtime_ns）缓存；读取途中文件被替换时重读一次，避免
    「签名与数据不配对」的缓存。索引缺失、不可读或结构不符时抛
    ``RESOURCE_LOAD_FAILED``。
    """
    _root, items = _load_index()
    return {
        item_id: {"name": item.name, "icon_filename": item.icon_filename}
        for item_id, item in items.items()
    }


def find_by_name(query: str) -> str | None:
    """按物品名找 ``item_id``：中文名精确匹配优先，其次大小写不敏感的精确匹配。

    同名物品有多个时取 ``item_id`` 字典序第一个（``item_index.json`` 里 474 条中确有
    重名，例如「寻访数据契约」26 条）。查不到返回 ``None``。
    """
    name = query.strip()
    if not name:
        return None
    _root, items = _load_index()
    exact = sorted(item.item_id for item in items.values() if item.name == name)
    if exact:
        return exact[0]
    folded = name.casefold()
    insensitive = sorted(
        item.item_id for item in items.values() if item.name.casefold() == folded
    )
    return insensitive[0] if insensitive else None


def resolve_icon_path(item_id: str) -> Path:
    """把索引里登记的物品图标解析成 ``template/items`` 下的真实文件。

    只接受索引登记的 basename、只允许 ``.png``、必须落在 ``template/items/`` 内
    （``resolve()`` + ``relative_to`` 双重校验）。物品不存在、没有图标或图标文件不在
    磁盘上时抛 ``RESOURCE_ASSET_NOT_FOUND``。
    """
    root, items = _load_index()
    item = items.get(item_id)
    icon_path = _icon_path(root, item.icon_filename if item is not None else None)
    if icon_path is None:
        raise AppError(
            ErrorCode.RESOURCE_ASSET_NOT_FOUND,
            "物品不存在或没有可用图标",
        )
    return icon_path


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


def _icon_path(resource_root_path: Path, icon_filename: str | None) -> Path | None:
    """只认索引里的 basename，并把它限制在 ``template/items`` 之内。"""
    if icon_filename is None or not _ICON_ID_PATTERN.fullmatch(icon_filename):
        return None
    if icon_filename in {".", ".."}:
        return None
    suffix = Path(icon_filename).suffix
    if suffix and suffix.lower() != ".png":
        return None

    try:
        resource_resolved = resource_root_path.resolve()
        items_directory = resource_root_path / "template" / "items"
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


def _parse_index(payload: Any) -> dict[str, _Item]:
    if not isinstance(payload, dict):
        raise _resource_error()

    items: dict[str, _Item] = {}
    for item_id, record in payload.items():
        if not isinstance(item_id, str) or not item_id or not isinstance(record, dict):
            raise _resource_error()
        name = record.get("name")
        if not isinstance(name, str) or not name.strip():
            raise _resource_error()
        raw_icon_filename = record.get("icon")
        icon_filename = raw_icon_filename if isinstance(raw_icon_filename, str) else None
        items[item_id] = _Item(item_id, name, icon_filename)
    return items


def _load_index() -> tuple[Path, dict[str, _Item]]:
    global _INDEX_CACHE

    root = resource_root()
    path = root / "item_index.json"
    try:
        resolved_root = root.resolve()
        resolved_path = path.resolve()
        resolved_path.relative_to(resolved_root)
        signature = _file_signature(resolved_path)
    except (OSError, RuntimeError, ValueError) as exc:
        raise _resource_error() from exc

    with _CACHE_LOCK:
        cached = _INDEX_CACHE
        if (
            cached is not None
            and cached.path == resolved_path
            and cached.signature == signature
        ):
            return resolved_root, cached.items

        try:
            payload = json.loads(resolved_path.read_text(encoding="utf-8"))
            # 读取途中被替换的文件不能让签名与数据错配（与 resources.py 同款处理）。
            after_read = _file_signature(resolved_path)
            if after_read != signature:
                signature = after_read
                payload = json.loads(resolved_path.read_text(encoding="utf-8"))
                signature = _file_signature(resolved_path)
            items = _parse_index(payload)
        except (OSError, UnicodeDecodeError, json.JSONDecodeError, RuntimeError) as exc:
            raise _resource_error() from exc
        except AppError:
            raise

        _INDEX_CACHE = _IndexCache(resolved_path, signature, items)
        return resolved_root, items
