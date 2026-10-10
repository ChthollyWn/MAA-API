"""外部来源登记表（规格 §3.2、附录 D）。

每个来源固定一组主机、路径、允许的查询参数与限额；请求拼装与白名单复核都在这里，
工具层与领域层拿不到原始 URL，也不能传入 URL。

M25 只登记 PRTS；M26 起登记企鹅物流。后续里程碑往 :data:`SOURCES` 里加作业站与拆包数据，
访问层与缓存层不用改。
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Mapping
from urllib.parse import urlencode

from maa_api.domain.errors import AppError, ErrorCode
from maa_api.settings import Settings, get_settings

__all__ = [
    "CACHE_LEVELS",
    "CACHE_LIVE",
    "CACHE_STATIC",
    "MAX_PARAM_CHARS",
    "SOURCES",
    "ResolvedSource",
    "SourceSpec",
    "build_url",
    "cache_level_seconds",
    "get_spec",
    "resolve_source",
    "validate_host",
]

#: 缓存三档（规格 §3.3、S3）：当期数据要新，稳定数据少打站点，拆包关卡文件最稳。
CACHE_LIVE = "live"
CACHE_STATIC = "static"
CACHE_LEVEL = "level"
CACHE_LEVELS = (CACHE_LIVE, CACHE_STATIC, CACHE_LEVEL)

#: 单个查询参数值的长度上限。PRTS 的 SMW 查询是一整条字符串，2000 字够长；
#: 再长就不是模型生成的查询，而是试图把别的东西塞进 URL。
MAX_PARAM_CHARS = 2000


@dataclass(frozen=True, slots=True)
class SourceSpec:
    """一个外部来源的固定形状与限额。"""

    kind: str
    label: str
    hosts: tuple[str, ...]
    #: 端点键 → 路径。端点键进日志与缓存键，路径进 URL。
    paths: Mapping[str, str]
    #: 允许出现的查询参数名（并集）；PRTS 的 `action` 语义另由客户端校验。
    params: frozenset[str]
    #: 解压后的响应体上限（字节）。
    max_bytes: int
    connect_timeout: float = 5.0
    read_timeout: float = 15.0
    #: 默认每分钟请求上限；实际值由配置覆盖。
    rate_per_minute: int = 30
    #: 同一来源的并发请求上限。
    concurrency: int = 2
    #: 未显式指定时的缓存档。
    cache_level: str = CACHE_STATIC
    #: 展示用的许可说明，进来源信封（规格 §12 的许可与版权）。
    license: str = ""

    def url(self, endpoint: str, params: Mapping[str, str], *, host: str | None = None) -> str:
        """拼 URL 并复核白名单；端点键、参数名、参数值或主机不合法时抛错。

        `host` 缺省用登记表的第一个主机；显式给主机时必须在该来源的 :attr:`hosts` 里
        （企鹅物流的 `.io` / `.cn` 靠这个参数切换）。
        """
        path = self.paths.get(endpoint)
        if path is None:
            raise AppError(
                ErrorCode.EXTERNAL_RESPONSE_INVALID,
                f"来源 {self.kind} 没有端点 {endpoint}",
                {"kind": self.kind, "endpoint": endpoint},
            )
        cleaned = _validate_params(self, params)
        query = urlencode(cleaned)
        chosen = validate_host(self, host)
        return f"https://{chosen}{path}?{query}" if query else f"https://{chosen}{path}"

    def endpoint_path(self, endpoint: str) -> str:
        """端点键 → 路径；未知端点抛错。"""
        try:
            return self.paths[endpoint]
        except KeyError:
            raise AppError(
                ErrorCode.EXTERNAL_RESPONSE_INVALID,
                f"来源 {self.kind} 没有端点 {endpoint}",
                {"kind": self.kind, "endpoint": endpoint},
            ) from None


def validate_host(spec: SourceSpec, host: str | None) -> str:
    """主机白名单校验：``None`` 取登记表的第一个主机，不在表里抛 ``INVALID_PARAMETER``。"""
    if host is None:
        return spec.hosts[0]
    if host not in spec.hosts:
        raise AppError(
            ErrorCode.INVALID_PARAMETER,
            f"来源 {spec.label} 不认识主机 {host}",
            {"kind": spec.kind, "host": str(host)[:80], "hosts": list(spec.hosts)},
        )
    return host


def _validate_params(spec: SourceSpec, params: Mapping[str, str]) -> dict[str, str]:
    """参数白名单与取值校验；任何一条不合法都直接拒绝，不发请求。"""
    cleaned: dict[str, str] = {}
    for key, value in params.items():
        if not isinstance(key, str) or key not in spec.params:
            raise AppError(
                ErrorCode.EXTERNAL_SOURCE_UNAVAILABLE,
                f"来源 {spec.label} 不接受参数 {key}",
                {"kind": spec.kind, "param": str(key)},
            )
        if not isinstance(value, str):
            raise AppError(
                ErrorCode.EXTERNAL_SOURCE_UNAVAILABLE,
                f"参数 {key} 必须是字符串",
                {"kind": spec.kind, "param": key},
            )
        if len(value) > MAX_PARAM_CHARS:
            raise AppError(
                ErrorCode.EXTERNAL_SOURCE_UNAVAILABLE,
                f"参数 {key} 超长（上限 {MAX_PARAM_CHARS} 字）",
                {"kind": spec.kind, "param": key, "chars": len(value)},
            )
        # 换行、回车、制表符与 NUL 只可能来自拼接错误或注入尝试。
        if any(character in value for character in "\r\n\t\x00"):
            raise AppError(
                ErrorCode.EXTERNAL_SOURCE_UNAVAILABLE,
                f"参数 {key} 含控制字符",
                {"kind": spec.kind, "param": key},
            )
        cleaned[key] = value
    return cleaned


#: PRTS 的固定参数：只读 JSON 接口，`format` 与 `formatversion` 由客户端写死。
_PRTS_PARAMS = frozenset(
    {
        "action",
        "format",
        "formatversion",
        "query",  # action=ask 的整条 SMW 查询
        "titles",
        "redirects",
        "prop",
        "rvprop",
        "rvslots",
        "clshow",
        "cllimit",
        "list",
        "srsearch",
        "srnamespace",
        "srlimit",
        "srprop",
        "tables",
        "fields",
        "where",
        "join_on",
        "limit",
        "offset",
        "order_by",
        "page",
        "section",
        "search",
        "namespace",
    }
)

#: 企鹅物流的固定参数：只读 JSON 接口，`server` 与 `show_closed_zones` 由客户端拼。
_PENGUIN_PARAMS = frozenset({"server", "stageFilter", "itemFilter", "show_closed_zones"})

SOURCES: dict[str, SourceSpec] = {
    "prts": SourceSpec(
        kind="prts",
        label="PRTS",
        hosts=("prts.wiki",),
        paths={"api": "/api.php"},
        params=_PRTS_PARAMS,
        # 规格 §3.2：PRTS 2 MB。实测银灰的 `action=parse` HTML 是 545 KB，
        # 我们只取 wikitext / SMW / Cargo，2 MB 是宽裕的上限。
        max_bytes=2 * 1024 * 1024,
        connect_timeout=5.0,
        read_timeout=15.0,
        rate_per_minute=30,
        concurrency=2,
        cache_level=CACHE_STATIC,
        license="PRTS 内容按 CC BY-NC-SA 4.0 提供，版权属于鹰角网络",
    ),
    "penguin": SourceSpec(
        kind="penguin",
        label="企鹅物流",
        # 两个主机内容一致；`sources.penguin_host` 选主用主机，失败一次后改试另一个。
        hosts=("penguin-stats.io", "penguin-stats.cn"),
        paths={
            # 四个路径都在 `/PenguinStats/api/v2` 前缀下（实测；少这个前缀会拿到
            # 前端的 HTML，解析 JSON 时报 EXTERNAL_RESPONSE_INVALID）。
            "matrix": "/PenguinStats/api/v2/result/matrix",
            "stages": "/PenguinStats/api/v2/stages",
            "items": "/PenguinStats/api/v2/items",
            "period": "/PenguinStats/api/v2/period",
        },
        params=_PENGUIN_PARAMS,
        # 实测 CN 的整表 matrix（含已关闭）是 12,522 行、约 2.3 MB，4 MB 留足余量。
        max_bytes=4 * 1024 * 1024,
        connect_timeout=5.0,
        read_timeout=20.0,
        rate_per_minute=10,
        concurrency=2,
        cache_level=CACHE_STATIC,
        license="企鹅物流数据按 CC BY-NC 4.0 提供（penguin-stats.io）",
    ),
}


def get_spec(kind: str) -> SourceSpec:
    """按 kind 取来源登记；未登记的来源直接拒绝。"""
    spec = SOURCES.get(kind)
    if spec is None:
        raise AppError(
            ErrorCode.EXTERNAL_SOURCE_UNAVAILABLE,
            f"未登记的外部来源：{kind}",
            {"kind": kind},
        )
    return spec


@dataclass(frozen=True, slots=True)
class ResolvedSource:
    """来源登记 + 当前配置的合成结果；每次请求现算，改设置立即生效。"""

    spec: SourceSpec
    enabled: bool
    rate_per_minute: int
    use_proxy: bool
    proxy: str
    cache_level_seconds: Mapping[str, int]

    @property
    def kind(self) -> str:
        return self.spec.kind

    @property
    def label(self) -> str:
        return self.spec.label


def resolve_source(kind: str, settings: Settings | None = None) -> ResolvedSource:
    """把登记表与配置合成一个可用来源；被关闭时抛 `EXTERNAL_SOURCE_DISABLED`。

    总开关 `sources.enabled` 关掉等于所有来源不可用；来源自己的开关（如
    `sources.prts_enabled`、`sources.penguin_enabled`）单独再判一次。
    """
    spec = get_spec(kind)
    settings = settings or get_settings()
    sources = settings.sources
    if not sources.enabled:
        raise AppError(
            ErrorCode.EXTERNAL_SOURCE_DISABLED,
            "外部数据源总开关已关闭",
            {"kind": kind},
        )
    if kind == "prts" and not sources.prts_enabled:
        raise AppError(
            ErrorCode.EXTERNAL_SOURCE_DISABLED,
            "PRTS 数据源已关闭",
            {"kind": kind},
        )
    if kind == "penguin" and not sources.penguin_enabled:
        raise AppError(
            ErrorCode.EXTERNAL_SOURCE_DISABLED,
            "企鹅物流数据源已关闭",
            {"kind": kind},
        )
    if kind == "prts":
        rate = sources.prts_rate_per_minute
    elif kind == "penguin":
        rate = sources.penguin_rate_per_minute
    else:
        rate = spec.rate_per_minute
    proxy = settings.proxy if sources.use_proxy else ""
    return ResolvedSource(
        spec=spec,
        enabled=True,
        rate_per_minute=rate,
        use_proxy=sources.use_proxy,
        proxy=proxy,
        cache_level_seconds=cache_level_seconds(settings),
    )


def cache_level_seconds(settings: Settings | None = None) -> dict[str, int]:
    """三档缓存时长；`level` 档的键要到 M29 才加，这里先用默认 24 小时。"""
    settings = settings or get_settings()
    return {
        CACHE_LIVE: settings.sources.cache_live_seconds,
        CACHE_STATIC: settings.sources.cache_static_seconds,
        CACHE_LEVEL: 24 * 60 * 60,
    }


def build_url(kind: str, endpoint: str, params: Mapping[str, str]) -> str:
    """便捷函数：只拼 URL 不发请求（单测断言请求形状用）。"""
    return get_spec(kind).url(endpoint, params)
