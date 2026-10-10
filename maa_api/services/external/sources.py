"""外部来源登记表（规格 §3.2、附录 D）。

每个来源固定一组主机、路径、允许的查询参数与限额；请求拼装与白名单复核都在这里，
工具层与领域层拿不到原始 URL，也不能传入 URL。

M25 只登记 PRTS；M26 起登记企鹅物流，M29 起登记作业站（``prts.maa.plus``）。后续里程碑往
:data:`SOURCES` 里加拆包数据，访问层与缓存层不用改。
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Mapping
from urllib.parse import urlencode

from maa_api.domain.errors import AppError, ErrorCode
from maa_api.settings import SourcesSettings, Settings, get_settings

__all__ = [
    "CACHE_LEVELS",
    "CACHE_LIVE",
    "CACHE_STATIC",
    "GAMEDATA_API_HOSTS",
    "GAMEDATA_DEFAULT_MIRRORS",
    "GAMEDATA_FILES",
    "MAX_PARAM_CHARS",
    "SOURCES",
    "ResolvedSource",
    "SourceSpec",
    "build_url",
    "cache_level_seconds",
    "gamedata_github_fallback_enabled",
    "gamedata_mirror_origins",
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

#: 路径里的 ``{name}`` 占位符（作业站的 ``/copilot/get/{site_id}``、拆包兜底的
#: ``/repos/.../contents/{path}``）。只有登记在这份路径里的参数名才会被替换。
_PLACEHOLDER = re.compile(r"\{([a-z_][a-z0-9_]*)\}")
#: 占位符的取值形状：数字、字母、``. _ @ - /``，长度封顶。比纯数字宽（拆包兜底要传仓库
#: 相对路径），但仍然不含 ``..``、反斜杠、查询串与片段——路径穿越与注入都拼不出来。
_PATH_SEGMENT = re.compile(r"^[A-Za-z0-9._@\-/]{1,400}$")


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

    def url(
        self,
        endpoint: str,
        params: Mapping[str, str],
        *,
        host: str | None = None,
        origin: str | None = None,
        suffix: str = "",
    ) -> str:
        """拼 URL 并复核白名单；端点键、参数名、参数值或主机不合法时抛错。

        :param host: 缺省用登记表的第一个主机；显式给主机时必须在该来源的 :attr:`hosts` 里
            （企鹅物流的 `.io` / `.cn` 靠这个参数切换）。
        :param origin: ``https://<主机><仓库前缀>`` 形式的**镜像前缀**（拆包数据用它：同一份
            内容有多个前缀）。主机仍须在白名单里，调用方仍然传不进任意 URL。
        :param suffix: 接在登记路径后面的相对路径（拆包数据的 ``zh_CN/gamedata/...``）。
            形状在 :data:`_PATH_SEGMENT` 里限定，不含 ``..``、反斜杠、查询串与片段。
        """
        path = self.paths.get(endpoint)
        if path is None:
            raise AppError(
                ErrorCode.EXTERNAL_RESPONSE_INVALID,
                f"来源 {self.kind} 没有端点 {endpoint}",
                {"kind": self.kind, "endpoint": endpoint},
            )
        cleaned = _validate_params(self, params)
        # 路径参数（`{site_id}`）：只有登记过的参数名能替换，替换值走同一套校验。
        placeholders = _PLACEHOLDER.findall(path)
        if placeholders:
            missing = [name for name in placeholders if name not in cleaned]
            if missing:
                raise AppError(
                    ErrorCode.EXTERNAL_SOURCE_UNAVAILABLE,
                    f"来源 {self.label} 的端点 {endpoint} 缺少路径参数",
                    {"kind": self.kind, "endpoint": endpoint, "missing": missing},
                )
            for name in placeholders:
                if not _PATH_SEGMENT.match(cleaned[name]):
                    raise AppError(
                        ErrorCode.EXTERNAL_SOURCE_UNAVAILABLE,
                        f"路径参数 {name} 形状不合法",
                        {"kind": self.kind, "endpoint": endpoint, "param": name},
                    )
            path = _PLACEHOLDER.sub(
                lambda match: cleaned[match.group(1)].strip(), path
            )
            # 路径参数只进路径，不进查询串。
            for name in placeholders:
                cleaned.pop(name, None)
        query = urlencode(cleaned)
        if origin is not None:
            base = _validate_origin(self, origin)
        else:
            base = f"https://{validate_host(self, host)}"
        if suffix:
            tail = str(suffix).strip("/")
            if not _PATH_SEGMENT.match(tail) or ".." in tail:
                raise AppError(
                    ErrorCode.INVALID_PARAMETER,
                    f"来源 {self.label} 的路径后缀形状不合法",
                    {"kind": self.kind, "suffix": tail[:120]},
                )
            path = f"{path}/{tail}"
        return f"{base}{path}?{query}" if query else f"{base}{path}"

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


#: 前缀（origin）允许的形状：``https://<主机>[/<路径段>...]``，路径段只允许仓库镜像常见的字符。
_ORIGIN = re.compile(r"^https://(?P<host>[a-z0-9.\-]+)(?P<path>(?:/[A-Za-z0-9._@\-]+)*)/?$")


def _validate_origin(spec: SourceSpec, origin: str) -> str:
    """镜像前缀校验：形状必须合法，主机必须在 ``spec.hosts`` 里（否则 ``INVALID_PARAMETER``）。"""
    cleaned = str(origin).strip().rstrip("/")
    match = _ORIGIN.match(cleaned)
    if match is None:
        raise AppError(
            ErrorCode.INVALID_PARAMETER,
            f"来源 {spec.label} 的镜像前缀形状不合法",
            {"kind": spec.kind, "origin": cleaned[:120]},
        )
    validate_host(spec, match.group("host"))
    return cleaned


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

#: 作业站（M29）的固定参数：`level_keyword` / `order_by` / `page` / `limit` 与
#: `get` 的路径参数。附录 D 还列了 `desc`，客户端没用它（默认排序即热度降序）。
_COPILOT_SITE_PARAMS = frozenset({"level_keyword", "order_by", "page", "limit", "site_id"})

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
    "copilot_site": SourceSpec(
        kind="copilot_site",
        label="作业站",
        # 作业站只此一个主机（附录 D）。``prts.plus`` 只是它的前端域名，接口在
        # ``prts.maa.plus`` 上（2026-10-10 实测）。
        hosts=("prts.maa.plus",),
        paths={
            "query": "/copilot/query",
            "get": "/copilot/get/{site_id}",
        },
        params=_COPILOT_SITE_PARAMS,
        # 实测 ``limit=50`` 的 query 响应约 40 KB、``limit=200`` 约 165 KB；get 一份
        # 完整作业最大几十 KB。1 MB 是宽裕上限，也挡住把整站倒出来的用法。
        max_bytes=1024 * 1024,
        connect_timeout=5.0,
        read_timeout=15.0,
        rate_per_minute=20,
        concurrency=2,
        cache_level=CACHE_LIVE,
        license="作业内容版权属于各自的作业作者，来源：PRTS 作业站（prts.maa.plus）",
    ),
    "gamedata": SourceSpec(
        kind="gamedata",
        label="拆包数据",
        hosts=("fastly.jsdelivr.net", "cdn.jsdelivr.net"),
        # 登记表在这里只当**前缀表**用：`/gh/仓库@分支` 是前缀，后面的
        # `zh_CN/gamedata/levels/<levelId>.json` 由客户端校验后拼上；这两个端点键只用来
        # 区分缓存档与日志（客户端不走 `url()`，因为路径要拼在前缀后面）。
        paths={
            # 只作为端点键（缓存键与日志）用；真正的镜像 URL 由客户端拼在镜像前缀后面——
            # 实测 `https://fastly.jsdelivr.net/gh/仓库@分支?path=…` 会被拒（HTTP 400），
            # 路径只能是路径。
            "level": "/zh_CN/gamedata/levels",
            "excel": "/zh_CN/gamedata/excel",
        },
        params=frozenset(),
        # 实测 6★ 干员表 character_table.json 是 15 MB、skill_table.json 是 11.5 MB；
        # 我们只取单个关卡文件（实测 1-7 是 87 KB）与两张常量表（< 2 MB），
        # 2 MB 挡的是「别把整棵 gamedata 目录倒出来」。
        max_bytes=2 * 1024 * 1024,
        connect_timeout=5.0,
        read_timeout=20.0,
        rate_per_minute=10,
        concurrency=2,
        cache_level=CACHE_LEVEL,
        license="拆包数据来自 Kengxxiao/ArknightsGameData，版权属于鹰角网络",
    ),
    "gamedata_github": SourceSpec(
        kind="gamedata_github",
        label="拆包数据（GitHub API）",
        hosts=("api.github.com",),
        # 路径模板；`{path}` 由客户端用白名单校验过的相对路径填充，`ref` 进查询串。
        paths={"contents": "/repos/Kengxxiao/ArknightsGameData/contents/{path}"},
        params=frozenset({"path", "ref"}),
        max_bytes=2 * 1024 * 1024,
        connect_timeout=5.0,
        read_timeout=20.0,
        rate_per_minute=10,
        concurrency=2,
        cache_level=CACHE_LEVEL,
        license="拆包数据来自 Kengxxiao/ArknightsGameData，版权属于鹰角网络",
    ),
}

#: 拆包数据的默认镜像前缀（附录 B）：主机 + 仓库路径，客户端在后面拼白名单路径。
GAMEDATA_DEFAULT_MIRRORS: tuple[str, ...] = (
    "https://fastly.jsdelivr.net/gh/Kengxxiao/ArknightsGameData@master",
    "https://cdn.jsdelivr.net/gh/Kengxxiao/ArknightsGameData@master",
)

#: 允许出现在镜像前缀里的主机：两个默认镜像 + GitHub 的原始内容域名（附录 D）。
#: 配了表外主机的镜像在取数时被跳过（不是把 URL 交出去），因此不会变成任意出站通道。
GAMEDATA_API_HOSTS: frozenset[str] = frozenset(
    {"fastly.jsdelivr.net", "cdn.jsdelivr.net", "raw.githubusercontent.com"}
)

#: `excel(name)` 允许取的文件（附录 D 的固定路径 + 文件名白名单）。
#: 只列升级经验与龙门币要用的两张：`gamedata_const.json`（经验/龙门币表）与
#: `item_table.json`（作战记录的 `expItems` 折算值）。不取 15 MB 的 `character_table.json`
#: 与 `enemy_database.json`。
GAMEDATA_FILES: frozenset[str] = frozenset({"gamedata_const.json", "item_table.json"})


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
    if kind in {"gamedata", "gamedata_github"} and not sources.gamedata_enabled:
        raise AppError(
            ErrorCode.EXTERNAL_SOURCE_DISABLED,
            "拆包数据源已关闭",
            {"kind": kind},
        )
    if kind == "prts":
        rate = sources.prts_rate_per_minute
    elif kind == "penguin":
        rate = sources.penguin_rate_per_minute
    elif kind in {"gamedata", "gamedata_github"}:
        rate = sources.gamedata_rate_per_minute
    else:
        # 作业站（M29）暂时没有自己的开关与限流键：用登记表的默认值，只受总开关约束。
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
    """三档缓存时长；`level` 档专给拆包关卡文件（M29），默认 24 小时。"""
    settings = settings or get_settings()
    return {
        CACHE_LIVE: settings.sources.cache_live_seconds,
        CACHE_STATIC: settings.sources.cache_static_seconds,
        CACHE_LEVEL: settings.sources.cache_level_seconds,
    }


#: 镜像前缀必须满足的形状：``https://<主机>[/<路径段>...]``，路径段只允许仓库镜像常见的
#: 字符（jsDelivr 的 ``/gh/仓库@分支`` 与 ``raw.githubusercontent.com`` 的 ``/仓库/分支``）。
#: 前缀里出现查询串、片段、``..`` 或编码转义一律拒绝——它们只可能来自拼接错误或注入尝试。
_MIRROR_PREFIX = re.compile(
    r"^https://(?P<host>[a-z0-9.\-]+)(?P<path>(?:/[A-Za-z0-9._@\-]+)*)/?$"
)


def gamedata_mirror_origins(sources: SourcesSettings | None = None) -> tuple[str, ...]:
    """当前配置下可用的镜像前缀（有序、去重）；表外主机与不合形状的前缀被跳过。

    配置为空或全被跳过时回落到 :data:`GAMEDATA_DEFAULT_MIRRORS`——「配错了」不该等于
    「拆包数据不可用」。前缀只作为**字符串前缀**使用，后面拼的相对路径由客户端校验。
    """
    settings = sources if sources is not None else get_settings().sources
    cleaned: list[str] = []
    for raw in settings.gamedata_mirrors or ():
        prefix = str(raw).strip().rstrip("/")
        match = _MIRROR_PREFIX.match(prefix)
        if match is None or match.group("host") not in GAMEDATA_API_HOSTS:
            continue
        if prefix not in cleaned:
            cleaned.append(prefix)
    return tuple(cleaned) or GAMEDATA_DEFAULT_MIRRORS


def gamedata_github_fallback_enabled(sources: SourcesSettings | None = None) -> bool:
    """镜像全失败后是否允许走 GitHub API（``sources.gamedata_github_api_fallback``）。"""
    settings = sources if sources is not None else get_settings().sources
    return bool(settings.gamedata_github_api_fallback)


def build_url(kind: str, endpoint: str, params: Mapping[str, str]) -> str:
    """便捷函数：只拼 URL 不发请求（单测断言请求形状用）。"""
    return get_spec(kind).url(endpoint, params)
