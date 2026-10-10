"""外部数据源端点（规格 §5.11、§4.7、附录 C）。

只开两条，**不做 PRTS 的代理**（Q25）：

``GET /api/sources/status``
    各来源的开关、状态、冷却截止时间、缓存条数与字节数、命中率、最近一次成功与失败。

``DELETE /api/sources/cache?source=<kind>``
    清空全部或单个来源的缓存，返回清掉的条数。

两条都只读 ``app.state`` 上的访问层对象；访问层没装配时回落到模块级默认客户端。
"""

from __future__ import annotations

import logging
from typing import Any

from fastapi import APIRouter, Depends, Query, Request
from pydantic import BaseModel, ConfigDict, Field

from maa_api.api.deps import require_auth
from maa_api.api.errors import error_responses
from maa_api.services.external.http import ExternalClient, default_external_client
from maa_api.services.external.sources import SOURCES, resolve_source
from maa_api.settings import get_settings

__all__ = ["CacheClearResponse", "SourcesStatusResponse", "router"]

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/sources", tags=["sources"])


class SourceCacheStats(BaseModel):
    """一个来源的缓存统计。"""

    entries: int = Field(description="缓存条数")
    bytes: int = Field(description="估算占用字节数")
    hits: int = Field(description="命中次数")
    misses: int = Field(description="未命中次数")
    hit_rate: float | None = Field(default=None, description="命中率，没有读写时为 null")
    evictions: int = Field(description="因超出内存上限被淘汰的条数")


class SourceStatus(BaseModel):
    """一个来源的运行时状态。"""

    kind: str
    label: str
    enabled: bool = Field(description="该来源当前是否启用（总开关关闭时全为 false）")
    rate_per_minute: int = Field(description="当前每分钟请求上限")
    last_success_at: str | None = None
    last_error_at: str | None = None
    last_error_code: str | None = None
    last_error_message: str | None = None
    consecutive_failures: int = 0
    cooldown_until: str | None = Field(default=None, description="冷却截止时间，未在冷却时为 null")
    cooldown_seconds: int = Field(default=0, description="冷却剩余秒数")
    requests: int = 0
    bytes_read: int = 0
    retries: int = 0
    cache: SourceCacheStats


class SourcesStatusResponse(BaseModel):
    """各外部来源的状态快照。"""

    sources: list[SourceStatus]


class CacheClearResponse(BaseModel):
    """清空缓存的结果。"""

    model_config = ConfigDict(extra="forbid")

    source: str | None = Field(description="清空的来源；null 表示全部")
    cleared: int = Field(description="清掉的条数")


@router.get(
    "/status",
    response_model=SourcesStatusResponse,
    summary="查外部数据源状态",
    responses=error_responses("UNAUTHORIZED"),
    dependencies=[Depends(require_auth)],
)
async def get_sources_status(request: Request) -> SourcesStatusResponse:
    """各来源的开关、状态与缓存统计；供设置页显示与排查。"""
    client = _client(request)
    settings = get_settings()
    settings_provider = getattr(
        getattr(request.app, "state", None), "settings_provider", None
    )
    if callable(settings_provider):
        settings = settings_provider()
    entries: list[SourceStatus] = []
    for kind in SOURCES:
        enabled = True
        rate = SOURCES[kind].rate_per_minute
        try:
            resolved = resolve_source(kind, settings)
            rate = resolved.rate_per_minute
        except Exception:  # noqa: BLE001 - 被关闭不是错误，状态页要显示出来
            enabled = False
        snapshot = client.status.status(kind).as_dict(
            now=_now(client), cache_stats=client.cache.stats(kind)
        )
        entries.append(SourceStatus(**snapshot, enabled=enabled, rate_per_minute=rate))
    return SourcesStatusResponse(sources=entries)


@router.delete(
    "/cache",
    response_model=CacheClearResponse,
    summary="清空外部数据源缓存",
    responses=error_responses("INVALID_PARAMETER", "UNAUTHORIZED"),
    dependencies=[Depends(require_auth)],
)
async def clear_sources_cache(
    request: Request,
    source: str | None = Query(
        default=None, description="只清这个来源；不传则清空全部（如 prts）"
    ),
) -> CacheClearResponse:
    """清空全部或单个来源的内存缓存；返回清掉的条数。"""
    if source is not None and source not in SOURCES:
        from maa_api.domain.errors import AppError, ErrorCode

        raise AppError(
            ErrorCode.INVALID_PARAMETER,
            f"未知的来源：{source}",
            {"source": source, "known": sorted(SOURCES)},
        )
    client = _client(request)
    cleared = client.cache.clear(source)
    logger.info("清空外部数据源缓存：source=%s cleared=%s", source or "all", cleared)
    return CacheClearResponse(source=source, cleared=cleared)


def _client(request: Request) -> ExternalClient:
    """装配在 `app.state` 上的客户端优先；缺省用模块级单例。"""
    state = getattr(request.app, "state", None)
    client = getattr(state, "external_client", None)
    if isinstance(client, ExternalClient):
        return client
    return default_external_client()


def _now(client: ExternalClient) -> float:
    """客户端的时钟（测试注入可控时钟时状态快照才自洽）。"""
    clock: Any = getattr(client.status, "clock", None)
    if callable(clock):
        try:
            return float(clock())
        except Exception:  # noqa: BLE001 - 时钟只影响展示
            pass
    import time

    return time.time()
