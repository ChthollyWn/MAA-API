"""作业站客户端（规格 §9.4、附录 D）：``prts.maa.plus`` 的两个只读端点。

端点（2026-10-10 实测）：

- ``GET /copilot/query?level_keyword=<stageId>&order_by=<hot|views|id>&page=1&limit=<n>``：
  按关卡筛作业，返回 ``{"status_code":200,"data":{"has_next","page","total","limit","data":[...]}}``。
  每行的 ``content`` 是**作业 JSON 的字符串**（不是对象），含 ``opers`` / ``groups`` /
  ``actions``；``location`` 是 MAA 坐标。
- ``GET /copilot/get/<数字 id>``：一份作业的完整内容，形状同上。

**HTTP 200 但 body 里 ``status_code=404`` 视为不存在**（实测口径）：不存在的 id 会返回
HTTP 200、body ``{"status_code":404,"message":"作业不存在"}``。这正是本客户端不能只用
状态码判断存在性的原因，:meth:`CopilotSiteClient.get` 见到它抛
``GAME_DATA_NOT_FOUND``。``query`` 见到它按来源不可用降级（规格 §3.5、§4.8：作业站
不可用时不抛错，标 ``unavailable``）。

缓存档位：``query`` 走 ``live``（好评率、浏览量、热度一直在变），``get`` 走 ``static``
（一份作业的内容不会变）。不可达（断网、限流、熔断、来源关闭）时由调用方按 §3.5
记 ``unavailable``，本模块只把 ``AppError`` 抛上去。
"""

from __future__ import annotations

import json
import re
from dataclasses import dataclass, field
from datetime import datetime
from typing import Any, Callable, Mapping

from maa_api.domain.errors import AppError, ErrorCode
from maa_api.services.external.envelope import SourceRef
from maa_api.services.external.http import ExternalClient, ExternalResult, default_external_client
from maa_api.services.external.sources import CACHE_LIVE, CACHE_STATIC
from maa_api.settings import Settings, get_settings

__all__ = [
    "COPILOT_SITE",
    "DEFAULT_LIMIT",
    "MAX_LIMIT",
    "ORDERS",
    "QUERY_ENDPOINT",
    "GET_ENDPOINT",
    "CopilotRecord",
    "CopilotRow",
    "CopilotSiteClient",
    "CopilotSiteData",
    "copilot_link",
    "parse_content",
]

QUERY_ENDPOINT = "query"
GET_ENDPOINT = "get"

#: `order_by` 的取值域（规格 §9.4）；站点对未知值会静默按 id 排序，所以这里先拒。
ORDERS: tuple[str, ...] = ("hot", "views", "id")

#: 单页条数的默认值与上限。上限 50：``limit=200`` 时响应约 165 KB，50 条足够
#: 筛选出 10 条以内的候选，也不会让一次工具调用把 24 KB 的预算撑满。
DEFAULT_LIMIT = 5
MAX_LIMIT = 50

#: 展示用站点首页（附录 D 的前端外链白名单里对应 ``prts.plus``）。
COPILOT_SITE = "https://prts.maa.plus/"

#: 作业站的作业编号与关卡关键字：都是纯数字 / 受限字符，不接受任何 URL 片段。
_SITE_ID = re.compile(r"^[0-9]{1,12}$")
_STAGE_KEYWORD = re.compile(r"^[A-Za-z0-9_#\-/]{1,64}$")

#: 站点自己给的「不存在」状态码：HTTP 200 但 body 里是 404（实测口径）。
_NOT_FOUND_STATUS = 404


@dataclass(frozen=True, slots=True)
class CopilotRow:
    """``query`` / ``get`` 里的一行作业元信息（``content`` 仍是字符串）。"""

    site_id: int
    type: str | None = None
    upload_time: str | None = None
    uploader: str | None = None
    uploader_id: str | None = None
    views: int | None = None
    hot_score: float | None = None
    available: bool = True
    rating_level: int | None = None
    rating_ratio: float | None = None
    not_enough_rating: bool = False
    like: int | None = None
    dislike: int | None = None
    comments_count: int | None = None
    content: str = ""
    raw: Mapping[str, Any] = field(default_factory=dict)

    def as_dict(self) -> dict[str, Any]:
        """给领域层与工具的字段（``content`` 另走 :meth:`parse_content`）。"""
        return {
            "site_id": self.site_id,
            "uploader": self.uploader,
            "upload_time": self.upload_time,
            "views": self.views,
            "hot_score": self.hot_score,
            "rating_ratio": self.rating_ratio,
            "rating_level": self.rating_level,
            "not_enough_rating": self.not_enough_rating,
            "like": self.like,
            "dislike": self.dislike,
            "comments_count": self.comments_count,
            "available": self.available,
            "link": copilot_link(self.site_id),
        }

    def parse_content(self) -> dict[str, Any] | None:
        """``content`` 字符串 → 作业对象；不是合法 JSON 对象时返回 ``None``。"""
        return parse_content(self.content)


@dataclass(frozen=True, slots=True)
class CopilotSiteData:
    """一次作业站取数：解析后的行 + 来源元信息。"""

    endpoint: str
    url: str
    fetched_at: datetime
    cached: bool
    stale: bool
    rows: tuple[CopilotRow, ...] = ()
    page: int | None = None
    limit: int | None = None
    total: int | None = None
    has_next: bool = False
    status_code: int | None = None
    message: str | None = None

    @property
    def not_found(self) -> bool:
        return self.status_code == _NOT_FOUND_STATUS

    def source_ref(self) -> SourceRef:
        """来源信封里的一条出处。"""
        return SourceRef(
            kind="copilot_site",
            label="作业站",
            title="PRTS 作业站",
            url=COPILOT_SITE,
            fetched_at=self.fetched_at,
            cached=self.cached,
            stale=self.stale,
        )


#: ``get`` 的返回类型：一份作业（``rows`` 只有一条）。
CopilotRecord = CopilotSiteData


class CopilotSiteClient:
    """作业站的只读客户端；所有请求都经 :class:`ExternalClient`（白名单、限流、缓存）。"""

    def __init__(
        self,
        client: ExternalClient | None = None,
        *,
        settings_provider: Callable[[], Settings] = get_settings,
    ) -> None:
        self._client = client
        self._settings_provider = settings_provider

    @property
    def client(self) -> ExternalClient:
        """底层客户端；缺省用进程内单例（装配在 ``app.state.external_client``）。"""
        return self._client or default_external_client()

    # -- 端点 -------------------------------------------------------------
    async def query(
        self,
        *,
        stage: str,
        order: str = "hot",
        page: int = 1,
        limit: int = DEFAULT_LIMIT,
    ) -> CopilotSiteData:
        """按关卡查作业列表（规格 §9.4）。

        :param stage: 关卡关键字。站点收 stageId、code、levelId 与中文关卡名；本客户端
            只放行 ``[A-Za-z0-9_#\\-/]``（中文名由领域层先用本机关卡索引换成 stageId），
            避免把模型给的东西直接拼进 URL。
        :param order: ``hot`` / ``views`` / ``id``。
        :param limit: 1–:data:`MAX_LIMIT`。
        """
        keyword = _stage_keyword(stage)
        order_by = _order(order)
        page_number = _positive_int(page, "page", minimum=1)
        page_size = _positive_int(limit, "limit", minimum=1, maximum=MAX_LIMIT)
        result = await self.client.get_json(
            "copilot_site",
            QUERY_ENDPOINT,
            {
                "level_keyword": keyword,
                "order_by": order_by,
                "page": str(page_number),
                "limit": str(page_size),
            },
            cache_level=CACHE_LIVE,
        )
        data = _envelope(result, QUERY_ENDPOINT)
        if _site_status(data) == _NOT_FOUND_STATUS:
            # 实测：``query`` 只在站点端异常时给 404 体；按「查不到这份数据」处理，
            # 让上层按 §3.5 记 unavailable，而不是抛一个假的不存在。
            raise _site_error(data, "作业站查询失败")
        rows = _rows_of(data.get("data"))
        listing = data.get("data") if isinstance(data.get("data"), Mapping) else {}
        return CopilotSiteData(
            endpoint=QUERY_ENDPOINT,
            url=result.url,
            fetched_at=result.fetched_at,
            cached=result.cached,
            stale=result.stale,
            rows=rows,
            page=_optional_int(listing.get("page")) or page_number,
            limit=_optional_int(listing.get("limit")) or page_size,
            total=_optional_int(listing.get("total")),
            has_next=bool(listing.get("has_next")),
            status_code=data.get("status_code"),
            message=_optional_text(data.get("message")),
        )

    async def get(self, site_id: int | str) -> CopilotRecord:
        """按作业编号取一份完整作业；站点说「作业不存在」时抛 ``GAME_DATA_NOT_FOUND``。"""
        cleaned = _site_id(site_id)
        result = await self.client.get_json(
            "copilot_site",
            GET_ENDPOINT,
            {"site_id": cleaned},
            cache_level=CACHE_STATIC,
        )
        data = _envelope(result, GET_ENDPOINT)
        if _site_status(data) == _NOT_FOUND_STATUS:
            raise AppError(
                ErrorCode.GAME_DATA_NOT_FOUND,
                f"作业站上没有编号 {cleaned} 的作业",
                {"site_id": cleaned, "site_status": _NOT_FOUND_STATUS},
            )
        rows = _record(data.get("data"))
        return CopilotSiteData(
            endpoint=GET_ENDPOINT,
            url=result.url,
            fetched_at=result.fetched_at,
            cached=result.cached,
            stale=result.stale,
            rows=rows,
            status_code=data.get("status_code"),
            message=_optional_text(data.get("message")),
        )


def copilot_link(site_id: int | str) -> str | None:
    """作业详情链接（2026-10-10 实测格式）；编号不合法时返回 ``None``。

    前端只为白名单主机渲染外链；``prts.maa.plus`` 需要一并进
    ``services/external/envelope.py`` 的 ``LINK_HOSTS``（M29 的装配任务）。
    """
    try:
        cleaned = _site_id(site_id)
    except AppError:
        return None
    return f"https://prts.maa.plus/copilot/{cleaned}"


def parse_content(content: Any) -> dict[str, Any] | None:
    """作业行里的 ``content`` → 作业对象。

    站点把作业 JSON 序列化成字符串放在 ``content`` 里（实测），所以这里解一次；
    不是合法 JSON 对象时返回 ``None``，由上层按结构不符处理。
    """
    if isinstance(content, Mapping):
        return dict(content)
    if isinstance(content, str) and content.strip():
        try:
            loaded = json.loads(content)
        except ValueError:
            return None
        if isinstance(loaded, Mapping):
            return dict(loaded)
    return None


# ---------------------------------------------------------------------------
# 内部
# ---------------------------------------------------------------------------
def _envelope(result: ExternalResult, endpoint: str) -> Mapping[str, Any]:
    """站点信封：顶层必须是带 ``status_code`` 的对象。"""
    data = result.data
    if not isinstance(data, Mapping) or "status_code" not in data:
        raise AppError(
            ErrorCode.EXTERNAL_RESPONSE_INVALID,
            f"作业站 {endpoint} 的响应结构不符：顶层不是 {{status_code, data}}",
            {"kind": "copilot_site", "endpoint": endpoint},
        )
    return data


def _site_status(data: Mapping[str, Any]) -> int | None:
    """站点自己的 ``status_code``（HTTP 200 时为 200，作业不存在时为 404）。"""
    return _optional_int(data.get("status_code"))


def _rows_of(payload: Any) -> tuple[CopilotRow, ...]:
    """``query`` 的 ``data`` 形状：``{has_next, page, total, limit, data: [...]}``。"""
    if isinstance(payload, Mapping):
        inner = payload.get("data")
        if isinstance(inner, list):
            return _rows(inner)
    return ()


def _rows(items: Any) -> tuple[CopilotRow, ...]:
    """一批作业行；不是数组时返回空元组。"""
    if not isinstance(items, list):
        return ()
    return tuple(_row(item) for item in items if isinstance(item, Mapping))


def _record(payload: Any) -> tuple[CopilotRow, ...]:
    """``get`` 的 ``data`` 是一份作业对象。"""
    return (_row(payload),) if isinstance(payload, Mapping) else ()


def _row(item: Mapping[str, Any]) -> CopilotRow:
    return CopilotRow(
        site_id=_optional_int(item.get("id")) or 0,
        type=_optional_text(item.get("type")),
        upload_time=_optional_text(item.get("upload_time")),
        uploader=_optional_text(item.get("uploader")),
        uploader_id=_optional_text(item.get("uploader_id")),
        views=_optional_int(item.get("views")),
        hot_score=_optional_float(item.get("hot_score")),
        available=bool(item.get("available", True)),
        rating_level=_optional_int(item.get("rating_level")),
        rating_ratio=_optional_float(item.get("rating_ratio")),
        not_enough_rating=bool(item.get("not_enough_rating", False)),
        like=_optional_int(item.get("like")),
        dislike=_optional_int(item.get("dislike")),
        comments_count=_optional_int(item.get("comments_count")),
        content=item.get("content") if isinstance(item.get("content"), str) else "",
        raw=dict(item),
    )


def _site_error(data: Mapping[str, Any], message: str) -> AppError:
    return AppError(
        ErrorCode.EXTERNAL_SOURCE_UNAVAILABLE,
        f"{message}：{_optional_text(data.get('message')) or data.get('status_code')}",
        {"kind": "copilot_site", "site_status": data.get("status_code")},
    )


def _site_id(value: int | str) -> str:
    text = str(value).strip()
    if not _SITE_ID.match(text):
        raise AppError(
            ErrorCode.INVALID_PARAMETER,
            f"作业站编号必须是数字，收到 {text[:16]!r}",
            {"site_id": text[:16]},
        )
    return text


def _stage_keyword(stage: str) -> str:
    text = str(stage).strip()
    if not _STAGE_KEYWORD.match(text):
        raise AppError(
            ErrorCode.INVALID_PARAMETER,
            "stage 只能是关卡 stageId / code / levelId（字母、数字、下划线、连字符）",
            {"stage": text[:64]},
        )
    return text


def _order(order: str) -> str:
    text = str(order).strip().lower()
    if text not in ORDERS:
        raise AppError(
            ErrorCode.INVALID_PARAMETER,
            f"order 只能是 {'、'.join(ORDERS)}，收到 {str(order)[:16]}",
            {"order": str(order)[:16]},
        )
    return text


def _positive_int(value: Any, field: str, *, minimum: int, maximum: int | None = None) -> int:
    parsed = _optional_int(value)
    if parsed is None or parsed < minimum or (maximum is not None and parsed > maximum):
        limit = f"{minimum}–{maximum}" if maximum is not None else f"≥ {minimum}"
        raise AppError(
            ErrorCode.INVALID_PARAMETER,
            f"{field} 必须是 {limit} 的整数，收到 {value!r}",
            {"field": field},
        )
    return parsed


def _optional_int(value: Any) -> int | None:
    if isinstance(value, bool) or value is None:
        return None
    if isinstance(value, int):
        return value
    if isinstance(value, float):
        return int(value)
    if isinstance(value, str):
        text = value.strip()
        if text.lstrip("-").isdigit():
            return int(text)
    return None


def _optional_float(value: Any) -> float | None:
    if isinstance(value, bool) or value is None:
        return None
    if isinstance(value, (int, float)):
        return float(value)
    return None


def _optional_text(value: Any) -> str | None:
    if isinstance(value, str) and value.strip():
        return value.strip()
    return None


#: 供工具层直接引用的端点顺序（诊断与测试断言用）。
ENDPOINTS: Sequence[str] = (QUERY_ENDPOINT, GET_ENDPOINT)

#: 进程内共享的默认门面。
_DEFAULT: CopilotSiteClient | None = None


def default_copilot_site_client() -> CopilotSiteClient:
    global _DEFAULT
    if _DEFAULT is None:
        _DEFAULT = CopilotSiteClient()
    return _DEFAULT
