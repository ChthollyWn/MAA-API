"""Validate, persist and apply visual settings (docs/04 §5.6, docs/05 §6.11)."""

from __future__ import annotations

import os
from collections.abc import Mapping
from pathlib import Path
from typing import Any

from pydantic import ValidationError
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from maa_api.db.repositories.setting import SettingRepository
from maa_api.domain.errors import AppError, ErrorCode
from maa_api.services.settings_schema import (
    SETTINGS_SCHEMA,
    SETTING_READONLY_KEYS,
    schema_for,
)
import maa_api.settings as settings_module
from maa_api.settings import SETTING_KEYS, Settings, resolve_settings_with_sources, set_settings

__all__ = ["SettingService"]

_SCHEMA_BY_KEY = {entry["key"]: entry for entry in SETTINGS_SCHEMA}


class SettingService:
    """Settings facade with an injected DB session factory and optional device manager."""

    def __init__(
        self,
        session_factory: async_sessionmaker[AsyncSession],
        *,
        device_manager: Any = None,
        settings_path: str | Path | None = None,
        env: Mapping[str, str] | None = None,
    ) -> None:
        self._session_factory = session_factory
        self._device_manager = device_manager
        self._settings_path = (
            Path(settings_path)
            if settings_path is not None
            else settings_module.DEFAULT_CONFIG_PATH
        )
        self._env = env
        self._settings: Settings | None = None
        self._sources: dict[str, str] = {}
        self._db_overrides: dict[str, Any] = {}

    def bind_device_manager(self, device_manager: Any) -> None:
        """Bind the lifespan-created device manager for hot-apply actions."""
        self._device_manager = device_manager

    async def refresh(self) -> Settings:
        """Resolve YAML + setting table + environment and replace the process cache."""
        async with self._session_factory() as session:
            self._db_overrides = await SettingRepository(session).all()
        settings, sources = resolve_settings_with_sources(
            settings_module._read_yaml(self._settings_path),
            db_overrides=self._db_overrides,
            env=os.environ if self._env is None else self._env,
        )
        self._settings = settings
        self._sources = sources
        set_settings(settings)
        return settings

    async def get(self, group: str | None = None) -> dict[str, Any]:
        """Return every effective value and its winning layer, with secrets masked."""
        settings = await self.refresh()
        items: list[dict[str, Any]] = []
        for key in SETTING_KEYS:
            metadata = _SCHEMA_BY_KEY[key]
            if group is not None and metadata["group"] != group:
                continue
            value: Any = settings
            for part in SETTING_KEYS[key]:
                value = getattr(value, part)
            if metadata.get("sensitive"):
                value = "***"
            items.append(
                {
                    "key": key,
                    "label": metadata["label"],
                    "group": metadata["group"],
                    "value": value,
                    "source": self._sources[key],
                }
            )
        return {"items": items, "total": len(items)}

    async def update(self, items: Mapping[str, Any]) -> dict[str, Any]:
        """Validate a batch, commit DB overrides, refresh, then run hot-apply actions."""
        if not isinstance(items, Mapping):
            raise self._invalid("items 必须是对象")
        unknown = [key for key in items if key not in SETTING_KEYS]
        if unknown:
            raise AppError(
                ErrorCode.SETTING_KEY_UNKNOWN,
                "设置项不存在",
                {"keys": unknown},
            )
        ignored_sensitive = {
            key
            for key, value in items.items()
            if _SCHEMA_BY_KEY[key].get("sensitive") and value in ("***", "")
        }
        readonly = [
            key
            for key in items
            if key in SETTING_READONLY_KEYS and key not in ignored_sensitive
        ]
        if readonly:
            raise AppError(
                ErrorCode.SETTING_READONLY,
                "设置项只能通过 config.yaml 或环境变量修改",
                {"keys": readonly},
            )

        changes: dict[str, Any] = {}
        for key, value in items.items():
            metadata = schema_for(key)
            if metadata.get("sensitive") and value in ("***", ""):
                continue
            changes[key] = self._validate(key, value)

        if changes:
            async with self._session_factory() as session:
                repository = SettingRepository(session)
                for key, value in changes.items():
                    await repository.set(key, value, updated_by="manual")
                await session.commit()

        current = await self.refresh()
        if "adb.address" in changes:
            await self._apply_device(address=current.adb.address)
        elif "adb.connection_config" in changes:
            await self._apply_device()
        return await self.get()

    async def reset(self, keys: list[str]) -> dict[str, Any]:
        """Delete selected DB overrides and fall back to the next configured layer."""
        unknown = [key for key in keys if key not in SETTING_KEYS]
        if unknown:
            raise AppError(
                ErrorCode.SETTING_KEY_UNKNOWN,
                "设置项不存在",
                {"keys": unknown},
            )
        readonly = [key for key in keys if key in SETTING_READONLY_KEYS]
        if readonly:
            raise AppError(
                ErrorCode.SETTING_READONLY,
                "只读设置项没有数据库覆盖可重置",
                {"keys": readonly},
            )

        before = await self.refresh()
        if keys:
            async with self._session_factory() as session:
                repository = SettingRepository(session)
                for key in dict.fromkeys(keys):
                    await repository.delete(key)
                await session.commit()
        current = await self.refresh()
        if "adb.address" in keys and current.adb.address != before.adb.address:
            await self._apply_device(address=current.adb.address)
        elif (
            "adb.connection_config" in keys
            and current.adb.connection_config != before.adb.connection_config
        ):
            await self._apply_device()
        return await self.get()

    @staticmethod
    def _invalid(message: str, *, key: str | None = None, value: Any = None) -> AppError:
        details: dict[str, Any] = {}
        if key is not None:
            details["key"] = key
        if value is not None:
            details["value"] = repr(value)
        return AppError(ErrorCode.SETTING_VALUE_INVALID, message, details or None)

    def _validate(self, key: str, value: Any) -> Any:
        """Apply metadata constraints, then validate against the runtime Settings model."""
        metadata = schema_for(key)
        kind = metadata["type"]
        is_valid_type = {
            "string": lambda v: isinstance(v, str),
            "integer": lambda v: isinstance(v, int) and not isinstance(v, bool),
            "number": lambda v: isinstance(v, (int, float)) and not isinstance(v, bool),
            "integer[]": lambda v: isinstance(v, list)
            and all(isinstance(n, int) and not isinstance(n, bool) for n in v),
        }[kind](value)
        if not is_valid_type:
            raise self._invalid("设置项类型不正确", key=key, value=value)
        if "enum" in metadata and value not in metadata["enum"]:
            raise self._invalid("设置项不在允许的枚举范围内", key=key, value=value)
        if kind in {"integer", "number"}:
            if "minimum" in metadata and value < metadata["minimum"]:
                raise self._invalid("设置值低于最小值", key=key, value=value)
            if "maximum" in metadata and value > metadata["maximum"]:
                raise self._invalid("设置值高于最大值", key=key, value=value)
        if kind == "integer[]":
            if any(not metadata["minimum"] <= port <= metadata["maximum"] for port in value):
                raise self._invalid("端口必须在 1 到 65535 之间", key=key, value=value)
            if len(set(value)) != len(value):
                raise self._invalid("端口列表不能重复", key=key, value=value)
        try:
            settings, _ = resolve_settings_with_sources(
                db_overrides={key: value}, env={}
            )
        except (ValidationError, ValueError, TypeError) as exc:
            raise self._invalid("设置值不符合配置模型", key=key, value=value) from exc
        result: Any = settings
        for part in SETTING_KEYS[key]:
            result = getattr(result, part)
        # Persist the model's canonical representation (e.g. int instead of a float).
        return result

    async def _apply_device(self, *, address: str | None = None) -> None:
        if self._device_manager is None:
            raise AppError(
                ErrorCode.SETTING_APPLY_FAILED,
                "配置已保存，但设备管理器不可用",
                {"applied": True},
            )
        try:
            if address is None:
                applied = await self._device_manager.reconfigure()
            else:
                applied = await self._device_manager.reconfigure(address=address)
        except Exception as exc:
            raise AppError(
                ErrorCode.SETTING_APPLY_FAILED,
                "配置已保存，但设备重连失败",
                {"applied": True, "error": str(exc)},
            ) from exc
        if applied is not True:
            raise AppError(
                ErrorCode.SETTING_APPLY_FAILED,
                "配置已保存，但设备重连失败",
                {"applied": True},
            )
