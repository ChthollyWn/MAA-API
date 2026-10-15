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
    #: M53：单条日志正文的截断长度。内核的 DEBUG 行可以很长（实测单次查询最大
    #: 41 432 字），整段回灌会按轮次重复计费；要看全文传 detail="full"。
    content_chars: int = Field(default=200, ge=40, le=2000)
    #: M53：``compact``（默认）只给定位用的最小字段，``full`` 保留 logger 与附件
    #: 等完整投影。多数排查只需要「哪一行、什么级别、大致说了什么」。
    detail: Literal["compact", "full"] = "compact"


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
            description="读服务、内核、设备和队列的整体状态。用户问「现在什么情况」、或动手前要判断有没有流水线在跑时用。",
            params_model=EmptyParams,
            handler=get_system_status,
            read_only=True,
        ),
        ToolDefinition(
            name="get_screenshot",
            group="status",
            risk=ToolRisk.SAFE,
            description="截取当前设备画面。要读游戏界面上的数字、确认画面状态，或执行点击前确认位置时用。",
            params_model=EmptyParams,
            handler=get_screenshot,
            read_only=True,
        ),
        ToolDefinition(
            name="get_versions",
            group="status",
            risk=ToolRisk.SAFE,
            description="读内核、资源和游戏版本，以及最近一次检查结果。用户问「要不要更新」「现在什么版本」时用。",
            params_model=EmptyParams,
            handler=get_versions,
            read_only=True,
        ),
        ToolDefinition(
            name="get_logs",
            group="status",
            risk=ToolRisk.SAFE,
            description=(
                "查历史日志。排查失败原因、核对任务执行细节时用。"
                "默认每条正文截到 200 字、只回定位用的字段；要看全文就传 detail=\"full\" "
                "并调大 content_chars，翻页用 page.next_cursor。"
            ),
            params_model=GetLogsParams,
            handler=get_logs,
            read_only=True,
        ),
        ToolDefinition(
            name="resolve_stage",
            group="status",
            risk=ToolRisk.SAFE,
            description="把用户说的关卡名换成内核认的关卡代号。提交 Fight 或查掉落前先查一次。",
            params_model=ResolveStageParams,
            handler=resolve_stage,
            read_only=True,
        ),
        ToolDefinition(
            name="get_drop_stats",
            group="status",
            risk=ToolRisk.SAFE,
            description="查本机打过的关卡掉了什么材料、各刷了多少次。只统计本机记录，不是掉率。",
            params_model=GetDropStatsParams,
            handler=get_drop_stats,
            read_only=True,
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
        "items": [
            _log_wire(row, content_chars=params.content_chars, detail=params.detail)
            for row in rows
        ],
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


def _log_wire(row: Any, *, content_chars: int, detail: str = "compact") -> dict[str, Any]:
    """一条日志的对外形态；正文按 ``content_chars`` 截断（M53）。

    截断只影响回灌给模型的那一份：库里保留全文，需要时用 ``after_id`` 翻页或调大
    ``content_chars`` 重查。
    """

    created_at = row.created_at
    if created_at.tzinfo is None:
        created_at = created_at.replace(tzinfo=UTC)
    meta = row.meta or {}
    text = str(row.content or "")
    truncated = len(text) > content_chars
    wire: dict[str, Any] = {
        "id": row.id,
        "ts": created_at.timestamp(),
        "source": SOURCE_DB_TO_WIRE[LogSource(row.source)],
        "level": str(row.level).upper(),
        "content": text[:content_chars],
    }
    if truncated:
        wire["content_truncated"] = True
    if detail == "full":
        wire["pipeline_id"] = row.pipeline_id
        wire["task_id"] = row.task_id
        wire["logger"] = meta.get("logger")
        wire["attachment"] = meta.get("attachment")
    return wire


def _service_version() -> str:
    try:
        return version("maa-api")
    except Exception:
        return "0.1.0"
