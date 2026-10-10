"""拆包数据客户端（规格 §9.3、附录 B、附录 D）：关卡文件与两张常量表。

路径形状固定，只有**关卡 id 与文件名**是可变的，两者都在发请求之前按白名单校验——

- 关卡文件：``zh_CN/gamedata/levels/<levelId 小写>.json``，``levelId`` 必须匹配
  ``^(obt|activities)/[a-z0-9_/\\-]+$``（规格 §9.3 原文）。不匹配报 ``INVALID_PARAMETER``，
  **不拼 URL**（拿不到合法 id 就没有 URL 可发）。
- 常量表：``zh_CN/gamedata/excel/<name>.json``，``name`` 必须在
  :data:`~maa_api.services.external.sources.GAMEDATA_FILES` 里（升级经验与龙门币要用的两张）。

取数顺序（规格 §9.3）：

1. ``sources.gamedata_mirrors`` 列出的镜像前缀按顺序试（默认两个 jsDelivr 前缀，2026-10-10
   实测 ``fastly`` 通、``cdn`` 在本机直连超时）；
2. 都失败**且** ``sources.gamedata_github_api_fallback=true`` 时，再走 GitHub API 的
   ``contents`` 端点（``Accept: application/vnd.github.raw+json``，附录 D 的兜底行）；
3. 全部走完仍失败，把最后一个可降级错误抛出去（``EXTERNAL_SOURCE_UNAVAILABLE`` /
   ``EXTERNAL_RESPONSE_INVALID``），让领域层按 §4.8 决定降级。

HTTP 404 单独处理：镜像之间内容一致，**所有**候选都 404 才算「来源上没有这一关」，
报 ``GAME_DATA_NOT_FOUND``。只试了一部分（例如镜像 500、GitHub 关掉）时**不**报 404——
那会把「取不到」说成「不存在」（§3.5 禁止冒充）。

缓存用 ``level`` 档（附录 B，默认 24 小时）：拆包文件几个月才变一次。缓存键是
``(kind, endpoint, params)``，因此镜像轮换不影响命中——第一个镜像写进去的值，第二个镜像
请求同一个键时直接复用。
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from datetime import datetime
from typing import Any, Callable, Mapping
from urllib.parse import quote, urlsplit

from maa_api.domain.errors import AppError, ErrorCode
from maa_api.services.external.envelope import SourceRef
from maa_api.services.external.http import (
    ExternalClient,
    ExternalResult,
    default_external_client,
)
from maa_api.services.external.sources import (
    CACHE_LEVEL,
    GAMEDATA_API_HOSTS,
    GAMEDATA_FILES,
    gamedata_github_fallback_enabled,
    gamedata_mirror_origins,
    get_spec,
    resolve_source,
)
from maa_api.settings import Settings, get_settings

__all__ = [
    "EXCEL_PREFIX",
    "GAMEDATA_KIND",
    "GAMEDATA_LICENSE_TITLE",
    "LEVEL_ID_PATTERN",
    "LEVELS_PREFIX",
    "GamedataClient",
    "GamedataData",
    "validate_excel_name",
    "validate_level_id",
]

#: 关卡文件在仓库里的固定前缀（``zh_CN`` 是国服，外服数据不在这里——规格 §4.3）。
LEVELS_PREFIX = "zh_CN/gamedata/levels"

#: 常量表的固定前缀。
EXCEL_PREFIX = "zh_CN/gamedata/excel"

#: 来源 kind（登记在 ``sources.SOURCES``）。
GAMEDATA_KIND = "gamedata"

#: 兜底来源 kind（GitHub API）与它的主机（附录 D 的兜底行）。
GITHUB_KIND = "gamedata_github"
GITHUB_HOST = "api.github.com"

#: 来源信封里的许可与出处（拆包数据是鹰角网络的版权内容，仓库只做镜像）。
GAMEDATA_LICENSE_TITLE = "拆包数据 · Kengxxiao/ArknightsGameData"

#: 仓库的展示地址（``github.com`` 在附录 D 的外链白名单里）。
GAMEDATA_REPO_URL = "https://github.com/Kengxxiao/ArknightsGameData"

#: 关卡 id 的白名单形状（规格 §9.3 原文）。
LEVEL_ID_PATTERN = re.compile(r"^(obt|activities)/[a-z0-9_/\-]+$")

#: 可降级的错误：换下一个镜像或走兜底还有机会（规格 §3.5）。
_FALLBACK_CODES = frozenset(
    {ErrorCode.EXTERNAL_SOURCE_UNAVAILABLE, ErrorCode.EXTERNAL_RESPONSE_INVALID}
)

#: 允许当镜像主机的主机名（与 ``sources.GAMEDATA_API_HOSTS`` 同一份；这里再挡一次，
#: 保证 ``_host_of`` 交给访问层的值一定在 ``SourceSpec.hosts`` 里）。
GAMEDATA_MIRROR_HOSTS = GAMEDATA_API_HOSTS

#: 单段路径里禁止出现的字符：它们只可能来自拼接错误或注入尝试。
_FORBIDDEN_SEGMENT_CHARS = frozenset("\r\n\t\x00?#\\")


@dataclass(frozen=True, slots=True)
class GamedataData:
    """一次拆包取数：解析前的 JSON + 来源元信息。"""

    data: Any
    url: str
    fetched_at: datetime
    cached: bool
    stale: bool
    #: 实际用的镜像前缀或 ``github``（日志与来源信封用）。
    via: str

    def source_ref(self, *, title: str | None = None) -> SourceRef:
        """来源信封里的一条出处；``title`` 缺省写许可说明。"""
        return SourceRef(
            kind=GAMEDATA_KIND,
            label="拆包数据",
            title=title or GAMEDATA_LICENSE_TITLE,
            url=GAMEDATA_REPO_URL,
            fetched_at=self.fetched_at,
            cached=self.cached,
            stale=self.stale,
        )


def validate_level_id(level_id: str) -> str:
    """关卡 id 白名单校验：小写化后必须匹配 ``^(obt|activities)/[a-z0-9_/\\-]+$``。

    以下都在发请求之前拒绝：空、超长、大写字母、``..``、反斜杠、查询串与片段、
    连续或首尾的 ``/``（字符类允许 ``/``，这里补上形状约束）。返回小写后的 id。
    """
    if not isinstance(level_id, str):
        raise AppError(
            ErrorCode.INVALID_PARAMETER, "levelId 必须是字符串", {"level_id": str(level_id)[:80]}
        )
    cleaned = level_id.strip().lower()
    if not LEVEL_ID_PATTERN.match(cleaned):
        raise AppError(
            ErrorCode.INVALID_PARAMETER,
            "levelId 形状不合法（要形如 obt/main/level_main_01-07）",
            {"level_id": cleaned[:80]},
        )
    if (
        len(cleaned) > 160
        or ".." in cleaned
        or "//" in cleaned
        or cleaned.endswith("/")
        or set(cleaned) & _FORBIDDEN_SEGMENT_CHARS
    ):
        raise AppError(
            ErrorCode.INVALID_PARAMETER,
            "levelId 含不允许的路径片段",
            {"level_id": cleaned[:80]},
        )
    return cleaned


def validate_excel_name(name: str) -> str:
    """常量表文件名白名单校验：只允许 :data:`GAMEDATA_FILES` 里的名字。"""
    cleaned = str(name or "").strip()
    if cleaned not in GAMEDATA_FILES:
        raise AppError(
            ErrorCode.INVALID_PARAMETER,
            f"只允许取这些拆包常量表：{'、'.join(sorted(GAMEDATA_FILES))}",
            {"name": cleaned[:80]},
        )
    return cleaned


class GamedataClient:
    """拆包数据的只读客户端；所有请求都经 `ExternalClient`。"""

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
    async def level(self, level_id: str) -> GamedataData:
        """取一份关卡文件（``zh_CN/gamedata/levels/<levelId>.json``）。"""
        cleaned = validate_level_id(level_id)
        return await self._fetch(
            "level",
            f"{cleaned}.json",
            title=cleaned,
            github_path=f"{LEVELS_PREFIX}/{cleaned}.json",
        )

    async def excel(self, name: str) -> GamedataData:
        """取一张常量表（``zh_CN/gamedata/excel/<name>.json``）；升级经验与龙门币用它。"""
        cleaned = validate_excel_name(name)
        return await self._fetch("excel", cleaned, title=cleaned)

    # -- 取数 -------------------------------------------------------------
    async def _fetch(
        self, endpoint: str, relative_path: str, *, title: str, github_path: str = ""
    ) -> GamedataData:
        """按镜像顺序 + GitHub 兜底取一个文件；404 与「取不到」分开报。

        :param relative_path: 接在端点登记路径后面的部分（镜像 URL 用它，也进缓存键）。
        :param github_path: GitHub 兜底用的仓库相对路径（缺省由端点前缀与
            ``relative_path`` 拼，见 :meth:`_github`）。
        """
        sources = self._settings_provider().sources
        # 来源级校验（总开关、gamedata 开关）在发请求之前做，且只做一次：
        # `resolve_source` 抛的 EXTERNAL_SOURCE_DISABLED 不该降级成「文件不存在」。
        resolve_source(GAMEDATA_KIND, self._settings_provider())

        attempts: list[AppError] = []
        not_found = 0
        for origin in gamedata_mirror_origins(sources):
            try:
                result = await self.client.get_json(
                    GAMEDATA_KIND,
                    endpoint,
                    # 路径只能走路径：实测 jsDelivr 对 `…@master?path=…` 直接回 HTTP 400。
                    # 端点登记的路径是 `/zh_CN/gamedata/<levels|excel>`，拼在镜像前缀后面。
                    {},
                    cache_level=CACHE_LEVEL,
                    host=_host_of(origin),
                    origin=origin,
                    suffix=relative_path,
                )
            except AppError as exc:
                if exc.code not in _FALLBACK_CODES:
                    raise
                if _status_of(exc) == 404:
                    not_found += 1
                attempts.append(exc)
                continue
            return _wrap(result, via=origin)

        if gamedata_github_fallback_enabled(sources):
            try:
                result = await self._github(endpoint, github_path or relative_path)
            except AppError as exc:
                if exc.code not in _FALLBACK_CODES:
                    raise
                if _status_of(exc) == 404:
                    not_found += 1
                attempts.append(exc)
            else:
                return _wrap(result, via="github")

        if attempts and not_found == len(attempts):
            # 每个候选都 404：镜像内容一致，这才算来源上没有这一份。
            raise AppError(
                ErrorCode.GAME_DATA_NOT_FOUND,
                f"拆包数据里没有 {relative_path}",
                {"path": relative_path, "title": title},
            )
        if attempts:
            raise attempts[-1]
        # 走到这里只可能是「一个镜像都没配、也不许走 GitHub」。
        raise AppError(
            ErrorCode.EXTERNAL_SOURCE_UNAVAILABLE,
            "没有可用的拆包镜像（sources.gamedata_mirrors 为空且 GitHub 兜底已关闭）",
            {"path": relative_path},
        )

    async def _github(self, endpoint: str, relative_path: str) -> ExternalResult:
        """GitHub API 的 ``contents`` 兜底：请求原始内容（附录 D）。

        路径模板的占位符在登记表里（``/repos/Kengxxiao/ArknightsGameData/contents/{path}``），
        这里只填值；``{path}`` 进路径、``ref=master`` 进查询串，替换值都走登记表的路径参数
        校验（不含 ``..``、反斜杠、查询串与片段），访问层还会再挡一次主机白名单。
        """
        get_spec(GITHUB_KIND)
        # 路径按段编码：`/` 保留为分隔符，其余字面量原样（仓库路径里没有需要转义的字符，
        # 这一步只是给兜底路径再上一道保险）。
        encoded = quote(relative_path, safe="/")
        return await self.client.get_json(
            GITHUB_KIND,
            "contents",
            {"path": encoded, "ref": "master"},
            cache_level=CACHE_LEVEL,
            host=GITHUB_HOST,
        )


def _host_of(origin: str) -> str:
    """镜像前缀 → 主机名（``sources.gamedata_mirror_origins`` 只放行登记过的主机）。"""
    host = urlsplit(origin).hostname or ""
    if host not in GAMEDATA_MIRROR_HOSTS:
        raise AppError(
            ErrorCode.INVALID_PARAMETER,
            f"镜像前缀的主机不在白名单里：{host[:80]}",
            {"host": host[:80]},
        )
    return host


def _wrap(result: ExternalResult, *, via: str) -> GamedataData:
    return GamedataData(
        data=result.data,
        url=result.url,
        fetched_at=result.fetched_at,
        cached=result.cached,
        stale=result.stale,
        via=via,
    )


def _status_of(error: AppError) -> int | None:
    """从 :class:`AppError` 的 ``details`` 里取 HTTP 状态码（访问层写的）。"""
    details = error.details if isinstance(error.details, Mapping) else {}
    status = details.get("status")
    return status if isinstance(status, int) and not isinstance(status, bool) else None


def default_gamedata_client() -> GamedataClient:
    """模块级默认门面（与其余访问层同款；不缓存实例，客户端本身是单例）。"""
    return GamedataClient()
