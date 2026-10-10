"""企鹅物流客户端（规格 §6.6、附录 B、附录 D）：四个只读端点 + 主机回退。

四个端点在登记表里都带 ``/PenguinStats/api/v2`` 前缀（``/result/matrix``、``/stages``、
``/items``、``/period``）：实测（2026-10-10）不带这个前缀的裸路径会返回前端 SPA 的 HTML，
状态码仍是 200，只会在解析 JSON 时报 ``EXTERNAL_RESPONSE_INVALID``。

主机按 ``sources.penguin_host`` 选（``penguin-stats.io`` / ``penguin-stats.cn``，两边内容
一致）；**请求失败一次后自动改试另一个**，站点级的限流与「来源被关闭」不重试。整表 matrix
（含已关闭的变体）走 ``static`` 档缓存，按单个材料 / 关卡查询时从整表里筛，不额外请求。

返回的是**解析前的 JSON**（`matrix` 是 ``{"matrix": [...]}``，其余是 list），与
:class:`PenguinData` 一起带出抓取时间、是否缓存 / 旧值与实际用的 URL，供掉率层拼来源信封。

数据许可 CC BY-NC 4.0（``developer.penguin-stats.io``）：来源出处里必须注明，
见 :data:`PENGUIN_LICENSE_TITLE`。
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from typing import Any, Callable, Mapping

from maa_api.domain.errors import AppError, ErrorCode
from maa_api.services.external.envelope import SourceRef
from maa_api.services.external.http import (
    ExternalClient,
    ExternalResult,
    default_external_client,
)
from maa_api.services.external.sources import CACHE_STATIC, SourceSpec, get_spec
from maa_api.settings import Settings, get_settings

__all__ = [
    "ITEMS_ENDPOINT",
    "MATRIX_ENDPOINT",
    "PENGUIN_LICENSE_TITLE",
    "PENGUIN_SITE",
    "PERIOD_ENDPOINT",
    "SERVERS",
    "STAGES_ENDPOINT",
    "PenguinClient",
    "PenguinData",
]

MATRIX_ENDPOINT = "matrix"
STAGES_ENDPOINT = "stages"
ITEMS_ENDPOINT = "items"
PERIOD_ENDPOINT = "period"

#: `server` 的取值域（规格 §4.3：跟随 `channel.server`）。
SERVERS: tuple[str, ...] = ("CN", "US", "JP", "KR")

#: 来源信封里的许可说明（CC BY-NC 4.0）；与 `services/game/items.py` 用同一句话。
PENGUIN_LICENSE_TITLE = "企鹅物流（数据许可 CC BY-NC 4.0）"

#: 许可与数据说明所在的站点首页；前端只为白名单主机渲染链接。
PENGUIN_SITE = "https://penguin-stats.io/"

#: 值得改试另一个主机的错误：站点级故障（DNS、证书、超时、5xx）与结构不符（那一边的
#: 缓存坏了）。限流是来源级的（本地令牌桶与远端冷却都按 kind 记），总开关与参数错误
#: 重试也没有意义。
_FALLBACK_CODES = frozenset(
    {ErrorCode.EXTERNAL_SOURCE_UNAVAILABLE, ErrorCode.EXTERNAL_RESPONSE_INVALID}
)


@dataclass(frozen=True, slots=True)
class PenguinData:
    """一次企鹅物流取数：解析前的 JSON + 来源元信息。"""

    data: Any
    url: str
    fetched_at: datetime
    cached: bool
    stale: bool

    def source_ref(self) -> SourceRef:
        """来源信封里的一条出处；许可说明写进 ``title``（规格 §6.5）。"""
        return SourceRef(
            kind="penguin",
            label="企鹅物流",
            title=PENGUIN_LICENSE_TITLE,
            url=PENGUIN_SITE,
            fetched_at=self.fetched_at,
            cached=self.cached,
            stale=self.stale,
        )


class PenguinClient:
    """企鹅物流的只读客户端；所有请求都经 `ExternalClient`。"""

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
        """底层客户端；缺省用进程内单例（装配在 `app.state.external_client`）。"""
        return self._client or default_external_client()

    # -- 端点 -------------------------------------------------------------
    async def matrix(
        self, *, server: str = "CN", include_closed: bool = False
    ) -> PenguinData:
        """整表掉率统计；``include_closed=True`` 时带 `show_closed_zones=true`（规格 §6.6）。

        默认只含当前开放的关卡（实测 8,087 行），带上已关闭的是 12,522 行。
        """
        params: dict[str, str] = {"server": _server(server)}
        if include_closed:
            params["show_closed_zones"] = "true"
        return await self._get(
            MATRIX_ENDPOINT, params, validate=_require_matrix
        )

    async def stages(self, *, server: str = "CN") -> PenguinData:
        """关卡表：`stageId`、`code`、`apCost`、`zoneId`、`stageType` 与各服 `existence`。"""
        return await self._get(
            STAGES_ENDPOINT,
            {"server": _server(server)},
            validate=_require_list(STAGES_ENDPOINT),
        )

    async def items(self) -> PenguinData:
        """道具表：`itemId`、`name`、`itemType`、`rarity`。"""
        return await self._get(
            ITEMS_ENDPOINT, {}, validate=_require_list(ITEMS_ENDPOINT)
        )

    async def period(self) -> PenguinData:
        """服务器时间线（开服与版本区间）；结构与其他列表端点一致。"""
        return await self._get(
            PERIOD_ENDPOINT, {}, validate=_require_list(PERIOD_ENDPOINT)
        )

    # -- 取数 -------------------------------------------------------------
    async def _get(
        self,
        endpoint: str,
        params: Mapping[str, str],
        *,
        validate: Callable[[Any], None],
    ) -> PenguinData:
        """按配置的主机取一个端点；失败一次后改试另一个主机。

        整表 matrix 与其余端点都走 ``static`` 档缓存（规格 §6.6）。
        """
        spec = get_spec("penguin")
        hosts = _host_order(self._settings_provider().sources.penguin_host, spec)
        last: AppError | None = None
        for host in hosts:
            try:
                result = await self.client.get_json(
                    "penguin",
                    endpoint,
                    params,
                    cache_level=CACHE_STATIC,
                    host=host,
                )
            except AppError as exc:
                if exc.code not in _FALLBACK_CODES:
                    raise
                last = exc
                continue
            try:
                validate(result.data)
            except AppError as exc:
                last = exc
                continue
            return _wrap(result)
        assert last is not None  # hosts 至少有一个，循环里必然记过一次失败
        raise last


def _wrap(result: ExternalResult) -> PenguinData:
    return PenguinData(
        data=result.data,
        url=result.url,
        fetched_at=result.fetched_at,
        cached=result.cached,
        stale=result.stale,
    )


def _host_order(primary: str, spec: SourceSpec) -> tuple[str, ...]:
    """主用主机在前，其余登记过的主机依次在后（去重）。"""
    ordered: list[str] = []
    for host in (primary, *spec.hosts):
        if host in spec.hosts and host not in ordered:
            ordered.append(host)
    return tuple(ordered)


def _server(server: str) -> str:
    """`server` 白名单校验：大小写不敏感，非法值在发请求之前就拒绝。"""
    cleaned = str(server).strip().upper()
    if cleaned not in SERVERS:
        raise AppError(
            ErrorCode.INVALID_PARAMETER,
            f"server 只能是 {'、'.join(SERVERS)}，收到 {str(server)[:16]}",
            {"server": str(server)[:16]},
        )
    return cleaned


def _invalid(endpoint: str, message: str) -> AppError:
    return AppError(
        ErrorCode.EXTERNAL_RESPONSE_INVALID,
        f"企鹅物流 {endpoint} 的响应结构不符：{message}",
        {"kind": "penguin", "endpoint": endpoint},
    )


def _require_matrix(data: Any) -> None:
    """整表 matrix 的顶层必须是 ``{"matrix": [...]}``。"""
    if not isinstance(data, dict) or not isinstance(data.get("matrix"), list):
        raise _invalid(MATRIX_ENDPOINT, "顶层不是 {\"matrix\": [...]}")


def _require_list(endpoint: str) -> Callable[[Any], None]:
    """`/stages`、`/items`、`/period` 的顶层必须是数组。"""

    def check(data: Any) -> None:
        if not isinstance(data, list):
            raise _invalid(endpoint, "顶层不是数组")

    return check
