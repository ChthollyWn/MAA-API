"""Read-only status, history, stage-resolution, and drop-statistics tools."""

from __future__ import annotations

import base64
from importlib.metadata import version
import io
from datetime import UTC, datetime
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field
from sqlalchemy.ext.asyncio import AsyncSession

from maa_api.agent.registry import ToolContext, ToolDefinition, ToolRegistry, ToolRisk
from maa_api.db.repositories.log import LogRepository
from maa_api.db.repositories.stage_statistics import StageStatisticsRepository
from maa_api.domain.enums import LogLevel, LogSource
from maa_api.domain.errors import AppError, ErrorCode
from maa_api.services.log_hub import SOURCE_DB_TO_WIRE, SOURCE_WIRE_TO_DB
from maa_api.services.stage_resolver import StageResolver
from maa_api.settings import get_settings

__all__ = ["GetDropStatsParams", "GetLogsParams", "ResolveStageParams", "register_tools"]


class EmptyParams(BaseModel):
    model_config = ConfigDict(extra="forbid")


class GetLogsParams(BaseModel):
    model_config = ConfigDict(extra="forbid")

    source: list[Literal["task", "service", "core"]] | None = None
    level: Literal["DEBUG", "INFO", "WARNING", "ERROR", "CRITICAL"] | None = None
    since: datetime | None = None
    until: datetime | None = None
    pipeline_id: str | None = None
    limit: int = Field(default=100, ge=1, le=200)
    after_id: int | None = Field(default=None, ge=0)


class ResolveStageParams(BaseModel):
    model_config = ConfigDict(extra="forbid")

    key: str = Field(min_length=1, max_length=128)


class GetDropStatsParams(BaseModel):
    model_config = ConfigDict(extra="forbid")

    stage: str | None = Field(default=None, max_length=24)
    since: datetime | None = None
    until: datetime | None = None


def register_tools(registry: ToolRegistry) -> None:
    """Register all SAFE tools in the ``status`` group."""
    definitions = (
        ToolDefinition(
            name="get_system_status",
            group="status",
            risk=ToolRisk.SAFE,
            description="读取服务、MaaCore、设备和队列状态。",
            params_model=EmptyParams,
            handler=get_system_status,
        ),
        ToolDefinition(
            name="get_screenshot",
            group="status",
            risk=ToolRisk.SAFE,
            description="获取当前设备画面，返回 JPEG base64、分辨率与 UTC 时间。",
            params_model=EmptyParams,
            handler=get_screenshot,
        ),
        ToolDefinition(
            name="get_versions",
            group="status",
            risk=ToolRisk.SAFE,
            description="读取内核、资源和游戏版本状态及最近一次检查结果。",
            params_model=EmptyParams,
            handler=get_versions,
        ),
        ToolDefinition(
            name="get_logs",
            group="status",
            risk=ToolRisk.SAFE,
            description="按来源、最低级别、时间范围和流水线查询历史日志。",
            params_model=GetLogsParams,
            handler=get_logs,
        ),
        ToolDefinition(
            name="resolve_stage",
            group="status",
            risk=ToolRisk.SAFE,
            description="互查关卡 code 与 stageId，并说明使用内核或本地资源索引。",
            params_model=ResolveStageParams,
            handler=resolve_stage,
        ),
        ToolDefinition(
            name="get_drop_stats",
            group="status",
            risk=ToolRisk.SAFE,
            description="按关卡和 UTC 时间范围聚合历史掉落，并返回理智观测曲线。",
            params_model=GetDropStatsParams,
            handler=get_drop_stats,
        ),
    )
    for definition in definitions:
        registry.register(definition)


async def get_system_status(_params: EmptyParams, ctx: ToolContext) -> dict[str, Any]:
    state = _app_state(ctx)
    core_supervisor = getattr(state, "core_supervisor", None)
    core = None
    if core_supervisor is not None:
        core_state = getattr(core_supervisor, "state", None)
        core = {
            "state": str(getattr(core_state, "value", core_state)),
            "pid": getattr(core_supervisor, "pid", None),
            "generation": getattr(core_supervisor, "generation", None),
        }

    device = None
    device_manager = getattr(state, "device_manager", None)
    if device_manager is not None:
        device = device_manager.snapshot()
        device.setdefault("retry", {"attempt": 0, "max": 0, "next_at": None})

    queue = None
    queue_service = getattr(state, "queue_service", None)
    if queue_service is not None:
        snapshot = await queue_service.snapshot()
        counts = snapshot.get("counts", {})
        queue = {
            "pending": int(counts.get("pending", 0)),
            "running": int(counts.get("running", 0)),
            "paused": bool(snapshot.get("paused", False)),
        }

    started_at = getattr(state, "started_at", None)
    if started_at is not None and not isinstance(started_at, str):
        formatter = getattr(started_at, "isoformat", None)
        started_at = formatter() if callable(formatter) else str(started_at)
    return {
        "status": "ok",
        "auth_enabled": bool(get_settings().access_token),
        "version": _service_version(),
        "started_at": started_at,
        "core": core,
        "device": device,
        "queue": queue,
    }


async def get_screenshot(_params: EmptyParams, ctx: ToolContext) -> dict[str, Any]:
    device_manager = _required_service(_app_state(ctx), "device_manager")
    result = await device_manager.screenshot(backend="adb")
    output = io.BytesIO()
    result.image.convert("RGB").save(output, format="JPEG", quality=85)
    captured_at = datetime.now(UTC)
    return {
        "format": "jpeg",
        "backend": result.backend,
        "width": result.image.width,
        "height": result.image.height,
        "captured_at": captured_at.isoformat().replace("+00:00", "Z"),
        "data": base64.b64encode(output.getvalue()).decode("ascii"),
    }


async def get_versions(_params: EmptyParams, ctx: ToolContext) -> dict[str, Any]:
    update_service = _required_service(_app_state(ctx), "update_service")
    return await update_service.status()


async def get_logs(params: GetLogsParams, ctx: ToolContext) -> dict[str, Any]:
    session = _required_session(ctx)
    sources = (
        [SOURCE_WIRE_TO_DB[source] for source in params.source]
        if params.source is not None
        else None
    )
    minimum_level = LogLevel(params.level.lower()) if params.level is not None else None
    since = _naive_utc(params.since)
    until = _naive_utc(params.until)
    if since is not None and until is not None and since > until:
        raise AppError(ErrorCode.INVALID_PARAMETER, "since 不能晚于 until")
    rows, has_more = await LogRepository(session).query_cursor(
        sources=sources,
        minimum_level=minimum_level,
        pipeline_id=params.pipeline_id,
        since=since,
        until=until,
        after_id=params.after_id,
        order="desc",
        size=params.limit,
    )
    return {
        "items": [_log_wire(row) for row in rows],
        "page": {
            "next_cursor": rows[-1].id if rows and has_more else None,
            "has_more": has_more,
            "limit": params.limit,
        },
    }


async def resolve_stage(params: ResolveStageParams, ctx: ToolContext) -> dict[str, Any]:
    state = _app_state(ctx)
    resolver = getattr(state, "stage_resolver", None)
    if resolver is None:
        resolver = StageResolver(getattr(state, "core_registry", None))
    return await resolver.resolve(params.key)


async def get_drop_stats(params: GetDropStatsParams, ctx: ToolContext) -> dict[str, Any]:
    since = _naive_utc(params.since)
    until = _naive_utc(params.until)
    if since is not None and until is not None and since > until:
        raise AppError(ErrorCode.INVALID_PARAMETER, "since 不能晚于 until")
    repository = StageStatisticsRepository(_required_session(ctx))
    drops = await repository.drop_stats(
        stage_code=params.stage,
        since=since,
        until=until,
    )
    sanity = await repository.sanity_curve(
        stage_code=params.stage,
        since=since,
        until=until,
    )
    return {**drops, "sanity_curve": sanity}


def _app_state(ctx: ToolContext) -> Any:
    request = ctx.request
    app = getattr(request, "app", None)
    state = getattr(app, "state", None)
    if state is None:
        raise AppError(ErrorCode.INTERNAL_ERROR, "工具上下文未提供应用运行时状态")
    return state


def _required_service(state: Any, name: str) -> Any:
    service = getattr(state, name, None)
    if service is None:
        raise AppError(ErrorCode.INTERNAL_ERROR, f"应用未装配 {name}")
    return service


def _required_session(ctx: ToolContext) -> AsyncSession:
    session = ctx.db_session
    if session is None:
        raise AppError(ErrorCode.INTERNAL_ERROR, "工具上下文未提供数据库会话")
    return session


def _naive_utc(value: datetime | None) -> datetime | None:
    if value is None:
        return None
    if value.tzinfo is None:
        return value
    return value.astimezone(UTC).replace(tzinfo=None)


def _log_wire(row: Any) -> dict[str, Any]:
    created_at = row.created_at
    if created_at.tzinfo is None:
        created_at = created_at.replace(tzinfo=UTC)
    meta = row.meta or {}
    return {
        "id": row.id,
        "ts": created_at.timestamp(),
        "source": SOURCE_DB_TO_WIRE[LogSource(row.source)],
        "level": str(row.level).upper(),
        "content": row.content,
        "pipeline_id": row.pipeline_id,
        "task_id": row.task_id,
        "logger": meta.get("logger"),
        "attachment": meta.get("attachment"),
    }


def _service_version() -> str:
    try:
        return version("maa-api")
    except Exception:
        return "0.1.0"
