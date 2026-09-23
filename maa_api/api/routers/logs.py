"""Historical log query, cursor paging, export and explicit cleanup."""

from __future__ import annotations

import json
from datetime import datetime, timezone
from typing import Any

from fastapi import APIRouter, Depends, Query, Response
from sqlalchemy.ext.asyncio import AsyncSession

from maa_api.api.deps import get_session, require_auth
from maa_api.api.errors import error_responses
from maa_api.db.models import LogEntry
from maa_api.db.repositories.log import LogRepository
from maa_api.domain.enums import LogLevel, LogSource
from maa_api.domain.errors import AppError, ErrorCode
from maa_api.services.log_hub import SOURCE_DB_TO_WIRE, SOURCE_WIRE_TO_DB

__all__ = ["router"]

router = APIRouter(prefix="/api/system", tags=["system"])
_LEVEL_RANK = {"DEBUG": 10, "INFO": 20, "WARNING": 30, "ERROR": 40, "CRITICAL": 50}
_EXPORT_LIMIT = 100_000


def _bad_parameter(message: str) -> AppError:
    return AppError(ErrorCode.INVALID_PARAMETER, message)


def _bad_pagination(message: str) -> AppError:
    return AppError(ErrorCode.INVALID_PAGINATION, message)


def _wire_row(entry: LogEntry) -> dict[str, Any]:
    meta = entry.meta or {}
    created_at = entry.created_at
    if created_at.tzinfo is None:
        created_at = created_at.replace(tzinfo=timezone.utc)
    return {
        "id": entry.id,
        "ts": created_at.timestamp(),
        "source": SOURCE_DB_TO_WIRE[LogSource(entry.source)],
        "level": str(entry.level).upper(),
        "content": entry.content,
        "pipeline_id": entry.pipeline_id,
        "task_id": entry.task_id,
        "logger": meta.get("logger"),
        "attachment": meta.get("attachment"),
    }


def _filters(
    *,
    source: list[str] | None,
    level: list[str] | None,
    pipeline_id: str | None,
    task_id: str | None,
    logger_prefix: str | None,
    q: str | None,
) -> dict[str, Any]:
    sources: list[LogSource] | None = None
    if source is not None:
        try:
            sources = [SOURCE_WIRE_TO_DB[item] for item in source]
        except KeyError as exc:
            raise _bad_parameter(f"未知日志来源: {exc.args[0]}") from None
    minimum_level: LogLevel | None = None
    if level:
        normalized = [item.upper() for item in level]
        if any(item == "WARN" for item in normalized):
            normalized = ["WARNING" if item == "WARN" else item for item in normalized]
        if any(item not in _LEVEL_RANK for item in normalized):
            raise _bad_parameter("level 必须为 DEBUG/INFO/WARNING/ERROR/CRITICAL")
        # Repeated levels all constrain a minimum, so the strictest threshold wins.
        minimum_level = LogLevel(max(normalized, key=_LEVEL_RANK.__getitem__).lower())
    return {
        "sources": sources,
        "minimum_level": minimum_level,
        "pipeline_id": pipeline_id,
        "task_id": task_id,
        "logger_prefix": logger_prefix,
        "query": q,
    }


async def _cursor_rows(
    repo: LogRepository,
    filters: dict[str, Any],
    *,
    after_id: int | None,
    before_id: int | None,
    order: str,
    size: int,
) -> dict[str, Any]:
    rows, has_more = await repo.query_cursor(
        **filters,
        after_id=after_id,
        before_id=before_id,
        order=order,
        size=size,
    )
    items = [_wire_row(row) for row in rows]
    return {
        "items": items,
        "page": {
            "next_cursor": items[-1]["id"] if items and has_more else None,
            "has_more": has_more,
            "limit": size,
        },
    }


@router.get(
    "/logs",
    summary="查询历史日志",
    description="按来源、级别、流水线、任务、logger、内容与时间过滤；默认使用 id 游标分页。",
    responses=error_responses("INVALID_PARAMETER", "INVALID_PAGINATION"),
    dependencies=[Depends(require_auth)],
)
async def list_logs(
    source: list[str] | None = Query(default=None),
    level: list[str] | None = Query(default=None),
    pipeline_id: str | None = None,
    task_id: str | None = None,
    logger: str | None = None,
    q: str | None = None,
    since: float | None = None,
    until: float | None = None,
    after_id: int | None = None,
    before_id: int | None = None,
    order: str = "desc",
    page: int | None = Query(default=None, ge=1),
    size: int = Query(default=100, ge=1),
    session: AsyncSession = Depends(get_session),
) -> dict[str, Any]:
    if order not in {"asc", "desc"}:
        raise _bad_parameter("order 必须是 asc 或 desc")
    if size > 1000:
        size = 1000
    cursor_mode = after_id is not None or before_id is not None or page is None
    if cursor_mode and page is not None:
        raise _bad_pagination("游标模式与 page 模式不能同时使用")
    if not cursor_mode and (after_id is not None or before_id is not None):
        raise _bad_pagination("游标模式与 page 模式不能同时使用")
    filters = _filters(
        source=source,
        level=level,
        pipeline_id=pipeline_id,
        task_id=task_id,
        logger_prefix=logger,
        q=q,
    )
    if cursor_mode:
        if after_id is not None and before_id is not None and after_id >= before_id:
            raise _bad_pagination("after_id 必须小于 before_id")
        since_at = datetime.fromtimestamp(since, timezone.utc).replace(tzinfo=None) if since is not None else None
        until_at = datetime.fromtimestamp(until, timezone.utc).replace(tzinfo=None) if until is not None else None
        filters.update(since=since_at, until=until_at)
        return await _cursor_rows(
            LogRepository(session), filters, after_id=after_id, before_id=before_id,
            order=order, size=size,
        )

    # Legacy page mode remains available for callers that explicitly send page.
    sources = filters.pop("sources")
    minimum_level = filters.pop("minimum_level")
    pipeline_id_filter = filters.pop("pipeline_id")
    task_id_filter = filters.pop("task_id")
    logger_prefix = filters.pop("logger_prefix")
    query = filters.pop("query")
    since_at = datetime.fromtimestamp(since, timezone.utc).replace(tzinfo=None) if since is not None else None
    until_at = datetime.fromtimestamp(until, timezone.utc).replace(tzinfo=None) if until is not None else None
    # Use the no-count cursor query for the requested page window, then return the
    # established page wrapper without exposing a misleading total count.
    rows, has_more = await LogRepository(session).query_cursor(
        sources=sources, minimum_level=minimum_level, pipeline_id=pipeline_id_filter,
        task_id=task_id_filter, logger_prefix=logger_prefix, query=query,
        since=since_at, until=until_at, order=order, size=size,
        offset=(page - 1) * size,
    )
    items = [_wire_row(row) for row in rows]
    return {"items": items, "page": {
        "next_cursor": items[-1]["id"] if items and has_more else None,
        "has_more": has_more,
        "limit": size,
    }}


@router.get(
    "/logs/export",
    summary="导出日志",
    description="以 text 或 JSON Lines 附件导出经过过滤的日志，最多导出 100000 条。",
    responses=error_responses("INVALID_PARAMETER", "INVALID_PAGINATION"),
    dependencies=[Depends(require_auth)],
)
async def export_logs(
    format: str = "txt",
    source: list[str] | None = Query(default=None),
    level: list[str] | None = Query(default=None),
    pipeline_id: str | None = None,
    task_id: str | None = None,
    logger: str | None = None,
    q: str | None = None,
    since: float | None = None,
    until: float | None = None,
    after_id: int | None = None,
    before_id: int | None = None,
    order: str = "desc",
    session: AsyncSession = Depends(get_session),
) -> Response:
    if format not in {"txt", "jsonl"}:
        raise _bad_parameter("format 必须是 txt 或 jsonl")
    if order not in {"asc", "desc"}:
        raise _bad_parameter("order 必须是 asc 或 desc")
    if after_id is not None and before_id is not None and after_id >= before_id:
        raise _bad_pagination("after_id 必须小于 before_id")
    filters = _filters(
        source=source, level=level, pipeline_id=pipeline_id, task_id=task_id,
        logger_prefix=logger, q=q,
    )
    since_at = datetime.fromtimestamp(since, timezone.utc).replace(tzinfo=None) if since is not None else None
    until_at = datetime.fromtimestamp(until, timezone.utc).replace(tzinfo=None) if until is not None else None
    filters.update(since=since_at, until=until_at)
    rows, has_more = await LogRepository(session).query_cursor(
        **filters, after_id=after_id, before_id=before_id, order=order,
        size=_EXPORT_LIMIT,
    )
    truncated = has_more
    records = [_wire_row(row) for row in rows[:_EXPORT_LIMIT]]
    if format == "jsonl":
        lines = [json.dumps(item, ensure_ascii=False, separators=(",", ":")) for item in records]
        if truncated:
            lines.append(json.dumps({"truncated": True, "limit": _EXPORT_LIMIT}))
        body = "\n".join(lines) + ("\n" if lines else "")
        media_type, filename = "application/x-ndjson; charset=utf-8", "logs.jsonl"
    else:
        lines = []
        for item in records:
            stamp = datetime.fromtimestamp(item["ts"], timezone.utc).astimezone().strftime("%Y-%m-%d %H:%M:%S.%f")[:-3]
            lines.append(f"{stamp} [{item['level']:<5}] [{item['source']:<7}] {item['content']}")
        if truncated:
            lines.append(f"[TRUNCATED] Export limited to {_EXPORT_LIMIT} rows")
        body = "\n".join(lines) + ("\n" if lines else "")
        media_type, filename = "text/plain; charset=utf-8", "logs.txt"
    return Response(
        content=body,
        media_type=media_type,
        headers={"Content-Disposition": f'attachment; filename="{filename}"'},
    )


@router.delete(
    "/logs",
    status_code=204,
    summary="清理历史日志",
    description="按来源和/或时间界限删除日志；至少提供一个条件。",
    responses=error_responses("INVALID_PARAMETER"),
    dependencies=[Depends(require_auth)],
)
async def delete_logs(
    source: list[str] | None = Query(default=None),
    before: float | None = None,
    session: AsyncSession = Depends(get_session),
) -> Response:
    if source is None and before is None:
        raise _bad_parameter("source 与 before 至少提供一个")
    sources: list[LogSource] | None = None
    if source is not None:
        try:
            sources = [SOURCE_WIRE_TO_DB[item] for item in source]
        except KeyError as exc:
            raise _bad_parameter(f"未知日志来源: {exc.args[0]}") from None
    before_at = datetime.fromtimestamp(before, timezone.utc).replace(tzinfo=None) if before is not None else None
    await LogRepository(session).delete_matching(sources=sources, before=before_at)
    return Response(status_code=204)
