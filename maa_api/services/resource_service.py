"""Copilot, Infrast-plan and custom-task application service.

The service owns validation, asset persistence and the custom incremental layer.
``reload_resources`` is injected by the lifespan owner and must reload the
configured MaaCore resource chain (normally with ``LOAD_RESOURCE``); this
module never imports or calls ``CoreClient`` directly.
"""

from __future__ import annotations

import asyncio
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
_INFRAST_ROOMS = {
    "trading",
    "manufacture",
    "power",
    "dormitory",
    "control",
    "meeting",
    "hire",
    "processing",
}
_COPILOT_ROOT_FIELDS = {
    "type", "stage_name", "minimum_required", "doc", "groups", "opers", "actions",
    "buff", "equipment", "strategy", "tool_men", "drops", "blacklist", "stages",
}
_TASK_STRING_FIELDS = {
    "Doc", "Doc2", "Doc3", "Doc_2", "Docs", "action", "algorithm",
    "binThresholdDoc", "colorScales_Doc", "detector", "doc", "doc2", "docs",
    "doc_preDelay", "exceededNext_Doc", "maxTimesDoc", "maxTimes_Doc", "method",
    "next_Doc", "ocrReplaceDoc", "postDelay_Doc", "postDelayDoc", "preDelayDoc",
    "preDelay_Doc", "rectMove_Doc", "rectMove_doc", "roi_Doc", "roi_doc",
    "specialParamsDoc", "specialParams_Doc", "specialParams_doc", "specificRect_Doc",
    "templThreshold_Doc", "template_Doc", "baseTask", "specificRect_Doc",
}
_TASK_BOOL_FIELDS = {
    "cache", "colorWithClose", "fullMatch", "highResolutionSwipeFix", "isAscii",
    "pureColor", "subErrorIgnored", "useRaw", "withoutDet",
}
_TASK_INT_FIELDS = {"count", "maxTimes", "nmsDistance", "postDelay", "preDelay"}
_TASK_NUMBER_FIELDS = {"templThreshold"}
_TASK_STRING_ARRAY_FIELDS = {"next", "onErrorNext", "exceededNext", "reduceOtherTimes", "sub", "text"}
_TASK_NUMBER_ARRAY_FIELDS = {
    "roi": 4,
    "specificRect": 4,
    "rectMove": 4,
    "maskRange": 2,
    "specialParams": None,
    "binThreshold": 2,
}
_TASK_ARRAY_FIELDS = {
    "colorScales", "ocrReplace", "template", "crop_doc", "recognize"
}
_TASK_ALLOWED_FIELDS = (
    _TASK_STRING_FIELDS
    | _TASK_BOOL_FIELDS
    | _TASK_INT_FIELDS
    | _TASK_NUMBER_FIELDS
    | _TASK_STRING_ARRAY_FIELDS
    | set(_TASK_NUMBER_ARRAY_FIELDS)
    | _TASK_ARRAY_FIELDS
)


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
        self._custom_task_lock = asyncio.Lock()

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
        async with self._custom_task_lock:
            return await self._register_custom_task(
                name=name,
                description=description,
                content=content,
            )

    async def _register_custom_task(
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
        async with self._custom_task_lock:
            return await self._remove_custom_task(asset_id)

    async def _remove_custom_task(self, asset_id: str) -> dict[str, Any]:
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
        if set(content) - _COPILOT_ROOT_FIELDS:
            raise _invalid_json(ErrorCode.COPILOT_JSON_INVALID, "Copilot 包含未知字段")
        stage = content.get("stage_name")
        actions = content.get("actions")
        opers = content.get("opers", [])
        groups = content.get("groups", [])
        if not isinstance(stage, str) or not stage.strip():
            raise _invalid_json(ErrorCode.COPILOT_JSON_INVALID, "Copilot 缺少有效的 stage_name")
        if not _nonempty_string(content.get("minimum_required")):
            raise _invalid_json(ErrorCode.COPILOT_JSON_INVALID, "Copilot 缺少有效的 minimum_required")
        doc = content.get("doc")
        if not isinstance(doc, Mapping) or not _nonempty_string(doc.get("title")):
            raise _invalid_json(ErrorCode.COPILOT_JSON_INVALID, "Copilot doc 必须包含 title")
        for field in ("details",):
            if field in doc and not isinstance(doc[field], str):
                raise _invalid_json(ErrorCode.COPILOT_JSON_INVALID, f"Copilot doc.{field} 必须是字符串")
        for field in ("title_color", "details_color"):
            if field in doc and not isinstance(doc[field], str):
                raise _invalid_json(ErrorCode.COPILOT_JSON_INVALID, f"Copilot doc.{field} 必须是字符串")
        if not _valid_operators(opers) or not isinstance(groups, list) or any(
            not _valid_copilot_group(group) for group in groups
        ):
            raise _invalid_json(ErrorCode.COPILOT_JSON_INVALID, "Copilot 干员与分组结构无效")
        if "actions" in content:
            if not isinstance(actions, list) or any(not _valid_copilot_action(action) for action in actions):
                raise _invalid_json(ErrorCode.COPILOT_JSON_INVALID, "Copilot actions 结构无效")
        elif content.get("type") != "SSS":
            raise _invalid_json(ErrorCode.COPILOT_JSON_INVALID, "常规 Copilot 作业必须包含 actions 数组")
        if "type" in content and content["type"] != "SSS":
            raise _invalid_json(ErrorCode.COPILOT_JSON_INVALID, "Copilot type 只支持 SSS 作业")
        if content.get("type") == "SSS":
            self._validate_sss_copilot(content)
        else:
            for field in ("groups",):
                if field in content and not isinstance(content[field], list):
                    raise _invalid_json(ErrorCode.COPILOT_JSON_INVALID, f"Copilot {field} 必须是数组")
        self._checked_json(dict(content), ErrorCode.COPILOT_JSON_INVALID)

    def _validate_sss_copilot(self, content: Mapping[str, Any]) -> None:
        for field in ("buff", "strategy"):
            if field in content and not isinstance(content[field], str):
                raise _invalid_json(ErrorCode.COPILOT_JSON_INVALID, f"SSS 作业 {field} 必须是字符串")
        for field in ("equipment", "drops", "blacklist"):
            if field in content and not _string_list(content[field]):
                raise _invalid_json(ErrorCode.COPILOT_JSON_INVALID, f"SSS 作业 {field} 必须是字符串数组")
        if "tool_men" in content and not _count_mapping(content["tool_men"]):
            raise _invalid_json(ErrorCode.COPILOT_JSON_INVALID, "SSS 作业 tool_men 必须是非负整数映射")
        stages = content.get("stages")
        if not isinstance(stages, list) or not stages:
            raise _invalid_json(ErrorCode.COPILOT_JSON_INVALID, "SSS 作业必须包含 stages 数组")
        for stage in stages:
            if not isinstance(stage, Mapping) or not _nonempty_string(stage.get("stage_name")):
                raise _invalid_json(ErrorCode.COPILOT_JSON_INVALID, "SSS stages 项缺少 stage_name")
            strategies = stage.get("strategies")
            if not isinstance(strategies, list) or any(
                not _valid_sss_strategy(strategy) for strategy in strategies
            ):
                raise _invalid_json(ErrorCode.COPILOT_JSON_INVALID, "SSS strategies 结构无效")
            if "actions" in stage and (
                not isinstance(stage["actions"], list)
                or any(not _valid_copilot_action(action) for action in stage["actions"])
            ):
                raise _invalid_json(ErrorCode.COPILOT_JSON_INVALID, "SSS stage actions 结构无效")
            retry_times = stage.get("retry_times")
            if retry_times is not None and not _nonnegative_int(retry_times):
                raise _invalid_json(ErrorCode.COPILOT_JSON_INVALID, "SSS retry_times 必须是非负整数")

    def _validate_infrast_plan(self, content: Mapping[str, Any]) -> int:
        plans = content.get("plans") if isinstance(content, Mapping) else None
        if not isinstance(plans, list) or not plans:
            raise _invalid_json(ErrorCode.INFRAST_PLAN_INVALID, "基建方案必须包含非空 plans 数组")
        allowed_top = {"author", "description", "id", "title", "planTimes", "plans", "scheduleType", "buildingType"}
        if set(content) - allowed_top:
            raise _invalid_json(ErrorCode.INFRAST_PLAN_INVALID, "基建方案包含未知顶层字段")
        for field in ("author", "description", "title", "planTimes"):
            if field in content and not isinstance(content[field], str):
                raise _invalid_json(ErrorCode.INFRAST_PLAN_INVALID, f"基建方案 {field} 必须是字符串")
        if "id" in content and not _nonnegative_int(content["id"]):
            raise _invalid_json(ErrorCode.INFRAST_PLAN_INVALID, "基建方案 id 必须是非负整数")
        if "buildingType" in content and not (
            _nonnegative_int(content["buildingType"])
            or (isinstance(content["buildingType"], str) and content["buildingType"].isdigit())
        ):
            raise _invalid_json(ErrorCode.INFRAST_PLAN_INVALID, "基建方案 buildingType 必须是非负整数")
        if "scheduleType" in content and (
            not isinstance(content["scheduleType"], Mapping)
            or any(not _nonnegative_int(value) for value in content["scheduleType"].values())
        ):
            raise _invalid_json(ErrorCode.INFRAST_PLAN_INVALID, "基建方案 scheduleType 结构无效")
        for plan in plans:
            if (
                not isinstance(plan, Mapping)
                or not isinstance(plan.get("name"), str)
                or not plan["name"].strip()
            ):
                raise _invalid_json(ErrorCode.INFRAST_PLAN_INVALID, "基建方案 plans 项结构无效")
            if set(plan) - {"name", "description", "description_post", "Fiammetta", "drones", "rooms", "period"}:
                raise _invalid_json(ErrorCode.INFRAST_PLAN_INVALID, "基建 plans 项包含未知字段")
            for field in ("description", "description_post"):
                if field in plan and not isinstance(plan[field], str):
                    raise _invalid_json(ErrorCode.INFRAST_PLAN_INVALID, f"基建 plans.{field} 必须是字符串")
            if "Fiammetta" in plan and not _valid_fiammetta(plan["Fiammetta"]):
                raise _invalid_json(ErrorCode.INFRAST_PLAN_INVALID, "基建 Fiammetta 结构无效")
            if "drones" in plan and not _valid_drones(plan["drones"]):
                raise _invalid_json(ErrorCode.INFRAST_PLAN_INVALID, "基建 drones 结构无效")
            if "rooms" in plan and not _valid_rooms(plan["rooms"]):
                raise _invalid_json(ErrorCode.INFRAST_PLAN_INVALID, "基建 rooms 结构无效")
            if "period" in plan and not _valid_period(plan["period"]):
                raise _invalid_json(ErrorCode.INFRAST_PLAN_INVALID, "基建 period 结构无效")
        self._checked_json(dict(content), ErrorCode.INFRAST_PLAN_INVALID)
        return len(plans)

    def _validate_custom_task(self, content: Mapping[str, Any]) -> dict[str, Any]:
        if not isinstance(content, Mapping):
            raise _invalid_json(ErrorCode.CUSTOM_TASK_INVALID, "task 定义必须是 JSON 对象")
        value = dict(content)
        if value and not set(value).intersection(_TASK_ALLOWED_FIELDS):
            raise _invalid_json(ErrorCode.CUSTOM_TASK_INVALID, "task 定义必须包含 action、baseTask 或 algorithm")
        for field, item in value.items():
            if field not in _TASK_ALLOWED_FIELDS and not field.endswith("_Doc") and not field.endswith("Doc"):
                raise _invalid_json(ErrorCode.CUSTOM_TASK_INVALID, f"task 包含未知字段 {field}")
            if not _valid_task_field(field, item):
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
        if not _valid_json_tree(value):
            raise _invalid_json(code, "资源内容不是有效 JSON")
        try:
            encoded = json.dumps(
                value, ensure_ascii=False, separators=(",", ":"), allow_nan=False
            )
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
            or (isinstance(operator["skill"], int) and not isinstance(operator["skill"], bool) and 1 <= operator["skill"] <= 3)
        )
        and (
            "skill_usage" not in operator
            or (isinstance(operator["skill_usage"], int) and not isinstance(operator["skill_usage"], bool) and 0 <= operator["skill_usage"] <= 2)
        )
        for operator in value
    )


def _valid_copilot_group(value: Any) -> bool:
    return (
        isinstance(value, Mapping)
        and _nonempty_string(value.get("name"))
        and _valid_operators(value.get("opers"))
    )


def _valid_copilot_action(value: Any) -> bool:
    if not isinstance(value, Mapping) or not _nonempty_string(value.get("type")):
        return False
    action_type = value["type"]
    if action_type == "Deploy":
        if not _nonempty_string(value.get("name")) or not _valid_coordinates(value.get("location")):
            return False
        if not _valid_direction(value.get("direction")):
            return False
    if "name" in value and not _nonempty_string(value["name"]):
        return False
    if "location" in value and not _valid_coordinates(value["location"]):
        return False
    if "direction" in value and not _valid_direction(value["direction"]):
        return False
    for field in ("kills", "cost"):
        if field in value and not _nonnegative_int(value[field]):
            return False
    return True


def _valid_sss_strategy(value: Any) -> bool:
    if not isinstance(value, Mapping):
        return False
    if "tool_men" in value and not _count_mapping(value["tool_men"]):
        return False
    if "core" in value and not _nonempty_string(value["core"]):
        return False
    if "location" in value and not _valid_coordinates(value["location"]):
        return False
    if "direction" in value and not _valid_direction(value["direction"]):
        return False
    if "kills" in value and not _nonnegative_int(value["kills"]):
        return False
    return True


def _valid_fiammetta(value: Any) -> bool:
    if not isinstance(value, Mapping) or set(value) - {"enable", "target", "order"}:
        return False
    return (
        ("enable" not in value or isinstance(value["enable"], bool))
        and ("target" not in value or isinstance(value["target"], str))
        and ("order" not in value or value["order"] in {"pre", "post"})
    )


def _valid_drones(value: Any) -> bool:
    if not isinstance(value, Mapping) or set(value) - {"room", "index", "enable", "order"}:
        return False
    index = value.get("index")
    return (
        ("room" not in value or value["room"] in {"trading", "manufacture"})
        and (
            "index" not in value
            or _nonnegative_int(index)
            or (isinstance(index, str) and index.isdigit())
        )
        and ("enable" not in value or isinstance(value["enable"], bool))
        and ("order" not in value or value["order"] in {"pre", "post"})
    )


def _valid_rooms(value: Any) -> bool:
    if not isinstance(value, Mapping) or set(value) - _INFRAST_ROOMS:
        return False
    for assignments in value.values():
        if not isinstance(assignments, list):
            return False
        for assignment in assignments:
            if not isinstance(assignment, Mapping):
                return False
            if set(assignment) - {"skip", "product", "operators", "sort", "autofill"}:
                return False
            if "skip" in assignment and not isinstance(assignment["skip"], bool):
                return False
            if "product" in assignment and not isinstance(assignment["product"], str):
                return False
            if "operators" in assignment and not _string_list(assignment["operators"]):
                return False
            for field in ("sort", "autofill"):
                if field in assignment and not isinstance(assignment[field], bool):
                    return False
    return True


def _valid_task_field(field: str, value: Any) -> bool:
    if field in _TASK_STRING_FIELDS:
        return isinstance(value, str) or (
            "doc" in field.lower() and _string_list(value)
        )
    if field in _TASK_BOOL_FIELDS:
        return isinstance(value, bool)
    if field in _TASK_INT_FIELDS:
        return _nonnegative_int(value)
    if field in _TASK_NUMBER_FIELDS:
        return _number(value) or (isinstance(value, list) and _number_list(value))
    if field in _TASK_STRING_ARRAY_FIELDS:
        return _string_list(value)
    if field in _TASK_NUMBER_ARRAY_FIELDS:
        expected_length = _TASK_NUMBER_ARRAY_FIELDS[field]
        return (
            isinstance(value, list)
            and (expected_length is None or len(value) == expected_length)
            and _number_list(value)
        )
    if field == "template":
        return isinstance(value, str) or _string_list(value)
    if field == "crop_doc":
        return isinstance(value, Mapping) and _valid_json_tree(value)
    if field == "ocrReplace":
        return isinstance(value, list) and all(
            isinstance(pair, list) and len(pair) == 2 and all(isinstance(item, str) for item in pair)
            for pair in value
        )
    if field == "colorScales":
        return isinstance(value, list) and all(
            isinstance(scale, list)
            and (
                _number_list(scale)
                or all(isinstance(range_pair, list) and _number_list(range_pair) for range_pair in scale)
            )
            for scale in value
        )
    if field == "recognize":
        return isinstance(value, Mapping) or _string_list(value)
    return False


def _valid_json_tree(value: Any) -> bool:
    if value is None or isinstance(value, (str, bool, int)):
        return True
    if isinstance(value, float):
        return _number(value)
    if isinstance(value, list):
        return all(_valid_json_tree(item) for item in value)
    if isinstance(value, Mapping):
        return all(isinstance(key, str) and _valid_json_tree(item) for key, item in value.items())
    return False


def _string_list(value: Any) -> bool:
    return isinstance(value, list) and all(isinstance(item, str) for item in value)


def _number_list(value: list[Any]) -> bool:
    return all(_number(item) for item in value)


def _number(value: Any) -> bool:
    return isinstance(value, (int, float)) and not isinstance(value, bool)


def _nonnegative_int(value: Any) -> bool:
    return isinstance(value, int) and not isinstance(value, bool) and value >= 0


def _nonempty_string(value: Any) -> bool:
    return isinstance(value, str) and bool(value.strip())


def _valid_coordinates(value: Any) -> bool:
    return isinstance(value, list) and len(value) == 2 and all(_nonnegative_int(item) for item in value)


def _valid_direction(value: Any) -> bool:
    return isinstance(value, str) and value.casefold() in {
        "left", "right", "up", "down", "none"
    }


def _count_mapping(value: Any) -> bool:
    return isinstance(value, Mapping) and all(
        isinstance(key, str) and _nonnegative_int(count) for key, count in value.items()
    )


def _valid_period(value: Any) -> bool:
    return isinstance(value, list) and all(
        isinstance(period, list)
        and len(period) == 2
        and all(isinstance(time_value, str) and bool(time_value) for time_value in period)
        for period in value
    )
