"""Copilot, Infrast-plan and custom-task application service.

The service owns validation, asset persistence and the custom incremental layer.
``reload_resources`` is injected by the lifespan owner and must reload the
configured MaaCore resource chain (normally with ``LOAD_RESOURCE``); this
module never imports or calls ``CoreClient`` directly.
"""

from __future__ import annotations

import hashlib
import inspect
import json
import logging
import os
import re
import tempfile
import uuid
from pathlib import Path
from typing import Any, Callable, Mapping

from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from maa_api.db.models import ResourceAsset, utcnow
from maa_api.db.repositories.resource import ResourceAssetRepository
from maa_api.domain.enums import ResourceAssetKind
from maa_api.domain.errors import AppError, ErrorCode

logger = logging.getLogger(__name__)

MAX_RESOURCE_BYTES = 2 * 1024 * 1024
INLINE_ASSET_BYTES = 64 * 1024
_CUSTOM_TASK_NAME = re.compile(r"^Custom_[A-Za-z0-9][A-Za-z0-9_.-]{0,54}$")


class ResourceService:
    """Manage validated user resources through ``ResourceAssetRepository``.

    ``resource_root`` is the repository's ``resource/`` directory. Callers that
    mutate custom tasks must inject ``reload_resources``; it is called after
    the custom layer has been written and again after rollback if the first
    load fails.
    """

    def __init__(
        self,
        session_factory: async_sessionmaker[AsyncSession],
        *,
        resource_root: str | Path,
        reload_resources: Callable[[], Any] | None = None,
    ) -> None:
        self.session_factory = session_factory
        self.resource_root = Path(resource_root).resolve()
        self.reload_resources = reload_resources

    async def upload_copilot(
        self,
        *,
        name: str,
        description: str | None,
        content: Mapping[str, Any],
    ) -> dict[str, Any]:
        self._validate_copilot(content)
        normalized_name = self._name(name)
        stored_content = self._checked_json(dict(content), ErrorCode.COPILOT_JSON_INVALID)
        meta = {
            "stage_name": content["stage_name"].strip(),
            "title": _nested_text(content.get("doc"), "title"),
        }
        asset_id = uuid.uuid4().hex
        inline_content, relative_path = self._storage_shape(
            ResourceAssetKind.COPILOT, asset_id, stored_content
        )
        asset = ResourceAsset(
            id=asset_id,
            kind=ResourceAssetKind.COPILOT,
            name=normalized_name,
            description=_description(description),
            content=inline_content,
            path=relative_path,
            meta=meta,
            checksum=_checksum(stored_content),
        )
        return await self._create_asset(asset, response_content=stored_content)

    async def list_copilots(
        self, *, page: int = 1, size: int = 20, q: str | None = None
    ) -> dict[str, Any]:
        return await self._list_assets(ResourceAssetKind.COPILOT, page=page, size=size, q=q)

    async def set_infrast_plan(
        self,
        *,
        name: str,
        description: str | None,
        content: Mapping[str, Any],
    ) -> dict[str, Any]:
        plan_count = self._validate_infrast_plan(content)
        normalized_name = self._name(name)
        stored_content = self._checked_json(dict(content), ErrorCode.INFRAST_PLAN_INVALID)
        filename = f"custom_infrast/{_safe_filename(normalized_name)}.json"
        target = self._infrast_plan_path(filename)
        previous_file = _read_file(target)
        meta = {"plan_count": plan_count, "filename": filename}
        existing_snapshot: ResourceAsset | None = None
        asset_id: str
        async with self.session_factory() as session:
            repo = ResourceAssetRepository(session)
            existing = next(
                (
                    row
                    for row in await repo.list_by_kind(ResourceAssetKind.INFRAST_PLAN)
                    if row.name == normalized_name
                ),
                None,
            )
            if existing is None:
                asset_id = uuid.uuid4().hex
                asset = ResourceAsset(
                    id=asset_id,
                    kind=ResourceAssetKind.INFRAST_PLAN,
                    name=normalized_name,
                    description=_description(description),
                    content=None,
                    path=str(target.relative_to(self.resource_root)),
                    meta=meta,
                    checksum=_checksum(stored_content),
                )
                try:
                    existing = await repo.create(asset)
                except IntegrityError as exc:
                    await session.rollback()
                    raise _asset_conflict(normalized_name) from exc
            else:
                existing_snapshot = _asset_copy(existing)
                asset_id = existing.id
                existing.description = _description(description)
                existing.content = None
                existing.path = str(target.relative_to(self.resource_root))
                existing.meta = meta
                existing.checksum = _checksum(stored_content)
                existing.updated_at = utcnow()
                existing = await session.merge(existing)
            await session.commit()
        try:
            _atomic_write_json(target, stored_content)
        except OSError as exc:
            await self._restore_infrast_asset(
                existing_snapshot,
                asset_id,
            )
            _restore_file(target, previous_file)
            raise AppError(ErrorCode.RESOURCE_LOAD_FAILED, "基建方案文件写入失败") from exc
        return {
            **_asset_wire(existing, include_content=True, content=stored_content),
            "filename": filename,
        }

    async def list_infrast_plans(
        self, *, page: int = 1, size: int = 20, q: str | None = None
    ) -> dict[str, Any]:
        return await self._list_assets(ResourceAssetKind.INFRAST_PLAN, page=page, size=size, q=q)

    async def register_custom_task(
        self,
        *,
        name: str,
        description: str | None,
        content: Mapping[str, Any],
    ) -> dict[str, Any]:
        self._require_reloader()
        normalized_name = self._custom_task_name(name)
        stored_content = self._validate_custom_task(content)
        asset_id = uuid.uuid4().hex
        inline_content, relative_path = self._storage_shape(
            ResourceAssetKind.CUSTOM_TASK, asset_id, stored_content
        )
        asset = ResourceAsset(
            id=asset_id,
            kind=ResourceAssetKind.CUSTOM_TASK,
            name=normalized_name,
            description=_description(description),
            content=inline_content,
            path=relative_path,
            meta={"task_name": normalized_name},
            checksum=_checksum(stored_content),
        )
        file_snapshot = self._snapshot_custom_file()
        try:
            created = await self._create_asset(asset, response_content=stored_content)
        except AppError:
            raise
        try:
            await self._write_enabled_custom_tasks()
            await self._reload()
        except Exception as exc:
            await self._rollback_created_custom_task(asset_id, file_snapshot, relative_path)
            raise AppError(
                ErrorCode.RESOURCE_LOAD_FAILED,
                "自定义任务已回滚，MaaCore 资源重载失败",
                {"task": normalized_name},
            ) from exc
        return created

    async def list_custom_tasks(
        self, *, page: int = 1, size: int = 20, q: str | None = None
    ) -> dict[str, Any]:
        return await self._list_assets(ResourceAssetKind.CUSTOM_TASK, page=page, size=size, q=q)

    async def remove_custom_task(self, asset_id: str) -> dict[str, Any]:
        self._require_reloader()
        file_snapshot = self._snapshot_custom_file()
        async with self.session_factory() as session:
            repo = ResourceAssetRepository(session)
            original = await repo.get(asset_id)
            if original is None or str(original.kind) != ResourceAssetKind.CUSTOM_TASK.value:
                raise AppError(ErrorCode.RESOURCE_ASSET_NOT_FOUND, "自定义任务不存在")
            snapshot = _asset_copy(original)
            await repo.delete(asset_id)
            await session.commit()

        try:
            await self._write_enabled_custom_tasks()
            await self._reload()
        except Exception as exc:
            await self._restore_custom_task(snapshot, file_snapshot)
            raise AppError(
                ErrorCode.RESOURCE_LOAD_FAILED,
                "自定义任务已恢复，MaaCore 资源重载失败",
                {"task": snapshot.name},
            ) from exc
        if snapshot.path:
            (self.resource_root / snapshot.path).unlink(missing_ok=True)
        return {"id": asset_id, "name": snapshot.name, "removed": True}

    async def _create_asset(
        self, asset: ResourceAsset, *, response_content: Any | None = None
    ) -> dict[str, Any]:
        content = asset.content if response_content is None else response_content
        path = self.resource_root / asset.path if asset.path else None
        previous_file = _read_file(path) if path is not None else None
        async with self.session_factory() as session:
            try:
                stored = await ResourceAssetRepository(session).create(asset)
                await session.commit()
            except IntegrityError as exc:
                await session.rollback()
                raise _asset_conflict(asset.name) from exc
        try:
            if path is not None:
                _atomic_write_json(path, content)
        except OSError as exc:
            async with self.session_factory() as session:
                await ResourceAssetRepository(session).delete(stored.id)
                await session.commit()
            _restore_file(path, previous_file)
            raise AppError(ErrorCode.RESOURCE_LOAD_FAILED, "资源文件写入失败") from exc
        return _asset_wire(stored, include_content=True, content=content)

    async def _list_assets(
        self,
        kind: ResourceAssetKind,
        *,
        page: int,
        size: int,
        q: str | None,
    ) -> dict[str, Any]:
        page, size = max(int(page), 1), min(max(int(size), 1), 200)
        query = q.strip().casefold() if q else ""
        async with self.session_factory() as session:
            rows = await ResourceAssetRepository(session).list_by_kind(kind)
        if query:
            rows = [
                row
                for row in rows
                if query in row.name.casefold()
                or query in (row.description or "").casefold()
                or query in json.dumps(row.meta or {}, ensure_ascii=False).casefold()
            ]
        start = (page - 1) * size
        return {
            "items": [_asset_wire(row, include_content=False) for row in rows[start : start + size]],
            "total": len(rows),
            "page": page,
            "size": size,
        }

    async def _write_enabled_custom_tasks(self) -> None:
        async with self.session_factory() as session:
            rows = await ResourceAssetRepository(session).list_by_kind(
                ResourceAssetKind.CUSTOM_TASK, enabled=True
            )
        tasks = {row.name: self._asset_content(row) for row in rows}
        target = self._custom_task_path()
        if not tasks:
            target.unlink(missing_ok=True)
            self._remove_empty_custom_directories(target.parent)
            return
        self._atomic_json_write(target, tasks)

    async def _rollback_created_custom_task(
        self, asset_id: str, file_snapshot: bytes | None, asset_path: str | None
    ) -> None:
        try:
            async with self.session_factory() as session:
                await ResourceAssetRepository(session).delete(asset_id)
                await session.commit()
            if asset_path:
                (self.resource_root / asset_path).unlink(missing_ok=True)
            self._restore_custom_file(file_snapshot)
            await self._reload()
        except Exception:
            logger.exception("回滚自定义 task %s 后的资源恢复重载失败", asset_id)

    async def _restore_custom_task(
        self, snapshot: ResourceAsset, file_snapshot: bytes | None
    ) -> None:
        async with self.session_factory() as session:
            await ResourceAssetRepository(session).create(snapshot)
            await session.commit()
        self._restore_custom_file(file_snapshot)
        await self._reload()

    def _validate_copilot(self, content: Mapping[str, Any]) -> None:
        if not isinstance(content, Mapping):
            raise _invalid_json(ErrorCode.COPILOT_JSON_INVALID, "Copilot 内容必须是 JSON 对象")
        stage = content.get("stage_name")
        actions = content.get("actions")
        opers = content.get("opers", [])
        groups = content.get("groups", [])
        if not isinstance(stage, str) or not stage.strip():
            raise _invalid_json(ErrorCode.COPILOT_JSON_INVALID, "Copilot 缺少有效的 stage_name")
        if not isinstance(actions, list) or not actions or any(
            not isinstance(action, Mapping)
            or not isinstance(action.get("type"), str)
            or not action["type"].strip()
            for action in actions
        ):
            raise _invalid_json(ErrorCode.COPILOT_JSON_INVALID, "Copilot actions 必须是含 type 的非空数组")
        if not _valid_operators(opers) or not isinstance(groups, list) or any(
            not isinstance(group, Mapping)
            or not isinstance(group.get("name"), str)
            or not _valid_operators(group.get("opers"))
            for group in groups
        ):
            raise _invalid_json(ErrorCode.COPILOT_JSON_INVALID, "Copilot 干员与分组结构无效")
        self._checked_json(dict(content), ErrorCode.COPILOT_JSON_INVALID)

    def _validate_infrast_plan(self, content: Mapping[str, Any]) -> int:
        plans = content.get("plans") if isinstance(content, Mapping) else None
        if not isinstance(plans, list) or not plans:
            raise _invalid_json(ErrorCode.INFRAST_PLAN_INVALID, "基建方案必须包含非空 plans 数组")
        for plan in plans:
            if (
                not isinstance(plan, Mapping)
                or not isinstance(plan.get("name"), str)
                or not plan["name"].strip()
                or ("rooms" in plan and not isinstance(plan["rooms"], Mapping))
            ):
                raise _invalid_json(ErrorCode.INFRAST_PLAN_INVALID, "基建方案 plans 项结构无效")
        self._checked_json(dict(content), ErrorCode.INFRAST_PLAN_INVALID)
        return len(plans)

    def _validate_custom_task(self, content: Mapping[str, Any]) -> dict[str, Any]:
        if not isinstance(content, Mapping) or not content:
            raise _invalid_json(ErrorCode.CUSTOM_TASK_INVALID, "task 定义必须是 JSON 对象")
        value = dict(content)
        if not set(value).intersection(
            {
                "action",
                "algorithm",
                "baseTask",
                "recognize",
                "template",
                "next",
                "onErrorNext",
                "specificRect",
                "roi",
                "text",
            }
        ):
            raise _invalid_json(ErrorCode.CUSTOM_TASK_INVALID, "task 定义没有可识别的任务字段")
        for field in ("action", "baseTask", "algorithm", "recognize"):
            if field in value and not isinstance(value[field], (str, Mapping, list)):
                raise _invalid_json(ErrorCode.CUSTOM_TASK_INVALID, f"task 的 {field} 结构无效")
        return self._checked_json(value, ErrorCode.CUSTOM_TASK_INVALID)

    @staticmethod
    def _name(value: str) -> str:
        normalized = value.strip()
        if not normalized or len(normalized) > 64:
            raise _invalid_json(ErrorCode.INVALID_PARAMETER, "资源名称长度必须为 1 至 64 个字符")
        return normalized

    def _custom_task_name(self, value: str) -> str:
        normalized = value.strip()
        if not _CUSTOM_TASK_NAME.fullmatch(normalized):
            raise _invalid_json(
                ErrorCode.CUSTOM_TASK_INVALID,
                "自定义 task 名必须以 Custom_ 开头，且只包含字母、数字、点、下划线或连字符",
            )
        return normalized

    @staticmethod
    def _checked_json(value: dict[str, Any], code: ErrorCode) -> dict[str, Any]:
        try:
            encoded = json.dumps(value, ensure_ascii=False, separators=(",", ":"))
        except (TypeError, ValueError) as exc:
            raise _invalid_json(code, "资源内容不是有效 JSON") from exc
        if len(encoded.encode("utf-8")) > MAX_RESOURCE_BYTES:
            raise AppError(ErrorCode.ASSET_TOO_LARGE, "单个资源不能超过 2 MB")
        return json.loads(encoded)

    def _require_reloader(self) -> None:
        if self.reload_resources is None:
            raise AppError(ErrorCode.SERVICE_UNAVAILABLE, "资源重载服务尚未装配")

    async def _reload(self) -> None:
        result = self.reload_resources()
        if inspect.isawaitable(result):
            await result

    def _custom_task_path(self) -> Path:
        target = self.resource_root / "maa-layers" / "custom" / "resource" / "tasks.json"
        target.parent.resolve().relative_to(self.resource_root)
        return target

    def _infrast_plan_path(self, filename: str) -> Path:
        target = self.resource_root / "maa-layers" / "custom" / "resource" / filename
        target.parent.resolve().relative_to(self.resource_root)
        return target

    def _storage_shape(
        self, kind: ResourceAssetKind, asset_id: str, content: dict[str, Any]
    ) -> tuple[dict[str, Any] | None, str | None]:
        encoded = json.dumps(content, ensure_ascii=False, separators=(",", ":"))
        if len(encoded.encode("utf-8")) <= INLINE_ASSET_BYTES:
            return content, None
        return None, f"agent-assets/{kind.value}/{asset_id}.json"

    def _asset_content(self, asset: ResourceAsset) -> Any:
        if asset.content is not None:
            return asset.content
        if not asset.path:
            return None
        try:
            path = (self.resource_root / asset.path).resolve()
            path.relative_to(self.resource_root)
            return json.loads(path.read_text(encoding="utf-8"))
        except (OSError, UnicodeDecodeError, json.JSONDecodeError, ValueError) as exc:
            raise AppError(
                ErrorCode.RESOURCE_LOAD_FAILED,
                "无法读取资源资产文件",
                {"asset_id": asset.id},
            ) from exc

    async def _restore_infrast_asset(
        self, snapshot: ResourceAsset | None, asset_id: str
    ) -> None:
        async with self.session_factory() as session:
            repo = ResourceAssetRepository(session)
            current = await repo.get(asset_id)
            if snapshot is None:
                if current is not None:
                    await repo.delete(asset_id)
            elif current is None:
                await repo.create(snapshot)
            else:
                current.description = snapshot.description
                current.content = snapshot.content
                current.path = snapshot.path
                current.meta = snapshot.meta
                current.checksum = snapshot.checksum
                current.updated_at = snapshot.updated_at
            await session.commit()

    def _snapshot_custom_file(self) -> bytes | None:
        target = self._custom_task_path()
        try:
            return target.read_bytes()
        except FileNotFoundError:
            return None

    def _restore_custom_file(self, snapshot: bytes | None) -> None:
        target = self._custom_task_path()
        if snapshot is None:
            target.unlink(missing_ok=True)
            self._remove_empty_custom_directories(target.parent)
            return
        target.parent.mkdir(parents=True, exist_ok=True)
        fd, temp_name = tempfile.mkstemp(prefix=".tasks-rollback-", dir=target.parent)
        temp = Path(temp_name)
        try:
            with os.fdopen(fd, "wb") as file:
                file.write(snapshot)
                file.flush()
                os.fsync(file.fileno())
            os.replace(temp, target)
        finally:
            temp.unlink(missing_ok=True)

    @staticmethod
    def _atomic_json_write(target: Path, value: Mapping[str, Any]) -> None:
        target.parent.mkdir(parents=True, exist_ok=True)
        fd, temp_name = tempfile.mkstemp(prefix=".tasks-", suffix=".json", dir=target.parent)
        temp = Path(temp_name)
        try:
            with os.fdopen(fd, "w", encoding="utf-8") as file:
                json.dump(value, file, ensure_ascii=False, indent=2)
                file.write("\n")
                file.flush()
                os.fsync(file.fileno())
            os.replace(temp, target)
        finally:
            temp.unlink(missing_ok=True)

    @staticmethod
    def _remove_empty_custom_directories(directory: Path) -> None:
        try:
            directory.rmdir()
            directory.parent.rmdir()
        except OSError:
            pass


def _invalid_json(code: ErrorCode, message: str) -> AppError:
    return AppError(code, message)


def _asset_conflict(name: str) -> AppError:
    return AppError(ErrorCode.RESOURCE_ASSET_CONFLICT, "已存在同名资源", {"name": name})


def _asset_wire(
    asset: ResourceAsset, *, include_content: bool, content: Any = ...
) -> dict[str, Any]:
    value: dict[str, Any] = {
        "id": asset.id,
        "kind": str(asset.kind),
        "name": asset.name,
        "description": asset.description,
        "meta": asset.meta,
        "enabled": bool(asset.enabled),
        "checksum": asset.checksum,
        "created_at": asset.created_at.isoformat() if asset.created_at else None,
        "updated_at": asset.updated_at.isoformat() if asset.updated_at else None,
    }
    if include_content:
        value["content"] = asset.content if content is ... else content
    if asset.kind == ResourceAssetKind.INFRAST_PLAN and asset.meta:
        value["filename"] = asset.meta.get("filename")
    return value


def _safe_filename(value: str) -> str:
    cleaned = re.sub(r"[^A-Za-z0-9_.-]+", "_", value.strip())
    stem = cleaned.strip("._") or "plan"
    suffix = hashlib.sha256(value.encode("utf-8")).hexdigest()[:10]
    return f"{stem[:48]}-{suffix}"


def _atomic_write_json(target: Path, value: Mapping[str, Any]) -> None:
    target.parent.mkdir(parents=True, exist_ok=True)
    fd, temp_name = tempfile.mkstemp(prefix=".asset-", suffix=".json", dir=target.parent)
    temp = Path(temp_name)
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as file:
            json.dump(value, file, ensure_ascii=False, indent=2)
            file.write("\n")
            file.flush()
            os.fsync(file.fileno())
        os.replace(temp, target)
    finally:
        temp.unlink(missing_ok=True)


def _read_file(path: Path | None) -> bytes | None:
    if path is None:
        return None
    try:
        return path.read_bytes()
    except FileNotFoundError:
        return None


def _restore_file(path: Path, content: bytes | None) -> None:
    if content is None:
        path.unlink(missing_ok=True)
        return
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, temp_name = tempfile.mkstemp(prefix=".asset-rollback-", dir=path.parent)
    temp = Path(temp_name)
    try:
        with os.fdopen(fd, "wb") as file:
            file.write(content)
            file.flush()
            os.fsync(file.fileno())
        os.replace(temp, path)
    finally:
        temp.unlink(missing_ok=True)


def _asset_copy(asset: ResourceAsset) -> ResourceAsset:
    return ResourceAsset(
        id=asset.id,
        kind=asset.kind,
        name=asset.name,
        description=asset.description,
        content=asset.content,
        path=asset.path,
        meta=asset.meta,
        checksum=asset.checksum,
        enabled=asset.enabled,
        remote_version=asset.remote_version,
        etag=asset.etag,
        last_modified=asset.last_modified,
        last_checked_at=asset.last_checked_at,
        created_at=asset.created_at,
        updated_at=asset.updated_at,
    )


def _description(value: str | None) -> str | None:
    if value is None:
        return None
    normalized = value.strip()
    return normalized or None


def _checksum(value: Mapping[str, Any]) -> str:
    payload = json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()


def _nested_text(value: Any, key: str) -> str | None:
    if not isinstance(value, Mapping):
        return None
    text = value.get(key)
    return text.strip() if isinstance(text, str) and text.strip() else None


def _valid_operators(value: Any) -> bool:
    if not isinstance(value, list):
        return False
    return all(
        isinstance(operator, Mapping)
        and isinstance(operator.get("name"), str)
        and bool(operator["name"].strip())
        and (
            "skill" not in operator
            or (isinstance(operator["skill"], int) and not isinstance(operator["skill"], bool))
        )
        for operator in value
    )
