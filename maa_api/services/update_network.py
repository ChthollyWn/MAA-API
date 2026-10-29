"""宿主机代理通道发现与更新下载网络决策（M61，docs/13 §8.84）。

更新链路的版本检查走的是国内可达的接口（api.maa.plus / Mirror酱 / 游戏服），
真正可能被网络卡住的只有「从 GitHub 下载资产」。用户的本机代理不一定常开，
所以这里把两件事放在一起：

1. **发现**：只读地列出宿主机上可能的代理通道——显式配置、环境变量、macOS
   系统代理、以及本机常见代理端口上的监听服务；对每个通道做一次到 GitHub 与
   更新接口的探测，给 Agent 与人工判断用。
2. **决策**：每次更新任务开始时选一条下载通道。显式指定的代理优先；没指定时
   只自动采用**本机端口扫描发现且探测可用**的代理（不隐式采用系统代理/环境
   变量，避免 M60 那种「重启前后走不同链路」）；都不可用则直连。

下载以外的检查请求仍用服务启动时创建的共享客户端，见 ``update_service`` 的
模块文档。代理 URL 里的账号密码在对外文案与更新记录中一律脱敏。
"""

from __future__ import annotations

import asyncio
import logging
import os
import platform
import re
import time
from dataclasses import dataclass
from typing import Any, Awaitable, Callable, Mapping
from urllib.parse import urlsplit, urlunsplit

import httpx

logger = logging.getLogger(__name__)

__all__ = [
    "DEFAULT_PROXY_PORTS",
    "PROXY_DISCOVERY_TTL_SECONDS",
    "UPDATE_HTTP_TIMEOUT_SECONDS",
    "ProxyChannel",
    "UpdateNetworkPlan",
    "create_update_http_client",
    "discover_proxy_channels",
    "mask_proxy_url",
    "normalize_prefix",
    "normalize_proxy",
    "parse_env_proxies",
    "parse_scutil_proxy",
    "probe_download_prefix",
    "resolve_update_network",
]

#: 更新时间链路共享 HTTP 客户端的超时（秒）。按**单次网络操作**计，不是整包
#: 总时长：下载的流式响应只要还有分片在流动就不会触发读超时。
UPDATE_HTTP_TIMEOUT_SECONDS = 30.0

#: 探测本机代理时只打这些回环端口：Clash / Clash Verge / Mihomo / V2Ray /
#: ShadowsocksX-NG / Surge / 常见 HTTP 代理与 PAC 端口的交集。不做全端口扫描。
DEFAULT_PROXY_PORTS: tuple[int, ...] = (
    7890, 7891, 7897, 1080, 1087, 8080, 8081, 8888, 6152, 6153, 20171,
)
#: 本机代理通道发现的进程内缓存时长（秒）。更新任务与工具调用共用。
PROXY_DISCOVERY_TTL_SECONDS = 30.0
#: 单个代理通道的探测超时（连接 / 读取，秒）。
PROXY_PROBE_TIMEOUT = httpx.Timeout(connect=4.0, read=6.0, write=6.0, pool=4.0)
#: 本机端口 TCP 探测超时（秒）。
PORT_PROBE_TIMEOUT = 0.35
#: 判定代理可用性的两个探测目标：资产宿主与更新接口各一个。
GITHUB_PROBE_URL = "https://github.com/robots.txt"
UPDATE_API_PROBE_URL = (
    "https://api.maa.plus/MaaAssistantArknights/api/version/summary.json"
)

_SCUTIL_KEY_RE = re.compile(r"([A-Za-z]+)\s*:\s*(\S+)")
_ENV_PROXY_KEYS = (
    "HTTPS_PROXY", "https_proxy", "HTTP_PROXY", "http_proxy", "ALL_PROXY", "all_proxy",
)
_SOURCE_ORDER = {"settings": 0, "job": 0, "system": 1, "env": 2, "local": 3}


def create_update_http_client(
    proxy: str = "",
    *,
    timeout: httpx.Timeout | float = UPDATE_HTTP_TIMEOUT_SECONDS,
) -> httpx.AsyncClient:
    """构建更新时间链路用的 HTTP 客户端。

    - **有限超时**：卡死的连接必须在超时内失败，才能进入下载器的传输层重试与
      多源回退。自建 ``timeout=None`` 客户端时，一次卡住的 GitHub 请求会永远等
      下去（M60 实机事故：内核更新卡在 0% 一个半小时）。
    - **代理只能显式传入**（``app.proxy`` / 本工具的参数）：``trust_env=False``
      不读环境变量与 macOS 系统代理，避免同一个更新在服务重启前后走不同链路。
    """
    return httpx.AsyncClient(
        follow_redirects=True,
        timeout=timeout,
        proxy=proxy or None,
        trust_env=False,
    )


def mask_proxy_url(value: str) -> str:
    """去掉代理 URL 里的账号密码；解析失败时原样截断返回。"""

    text = str(value or "").strip()
    if not text:
        return ""
    try:
        parts = urlsplit(text)
    except ValueError:
        return text[:128]
    if "@" not in parts.netloc:
        return text
    host = parts.netloc.rsplit("@", 1)[-1]
    masked = urlunsplit((parts.scheme, f"***@{host}", parts.path, parts.query, parts.fragment))
    return masked


def _normalize_url(value: str, *, field: str) -> str:
    text = str(value or "").strip()
    if not text:
        raise ValueError(f"{field} 不能为空")
    if any(char.isspace() for char in text):
        raise ValueError(f"{field} 不能包含空白字符")
    try:
        parts = urlsplit(text)
    except ValueError as exc:
        raise ValueError(f"{field} 不是合法 URL") from exc
    if parts.scheme not in {"http", "https"}:
        raise ValueError(f"{field} 必须是 http:// 或 https:// 地址")
    if not parts.hostname:
        raise ValueError(f"{field} 缺少主机名")
    return text


def normalize_proxy(value: str) -> str:
    """校验并归一化代理地址（http/https，带主机名）。"""

    return _normalize_url(value, field="代理地址")


def normalize_prefix(value: str) -> str:
    """校验并归一化下载加速前缀（http/https，去掉尾部斜杠）。"""

    return _normalize_url(value, field="下载加速前缀").rstrip("/")


def parse_env_proxies(env: Mapping[str, str] | None = None) -> list[str]:
    """从环境变量收集代理地址（只用于**展示**，更新链路不会自动采用）。"""

    source = os.environ if env is None else env
    found: list[str] = []
    for key in _ENV_PROXY_KEYS:
        raw = str(source.get(key) or "").strip()
        if not raw:
            continue
        # ALL_PROXY 可能只写 host:port；补一个 http:// 方便展示与探测。
        url = raw if "://" in raw else f"http://{raw}"
        if url not in found:
            found.append(url)
    return found


def parse_scutil_proxy(text: str) -> list[str]:
    """解析 macOS ``scutil --proxy`` 的输出，返回启用中的代理 URL。"""

    values = {
        key: value for key, value in _SCUTIL_KEY_RE.findall(text or "")
    }

    def enabled(name: str) -> bool:
        return str(values.get(name, "0")).strip() == "1"

    found: list[str] = []
    if enabled("HTTPEnable"):
        host, port = values.get("HTTPProxy"), values.get("HTTPPort")
        if host and port:
            found.append(f"http://{host}:{port}")
    if enabled("HTTPSEnable"):
        host, port = values.get("HTTPSProxy"), values.get("HTTPSPort")
        if host and port:
            found.append(f"http://{host}:{port}")
    if enabled("SOCKSEnable"):
        host, port = values.get("SOCKSProxy"), values.get("SOCKSPort")
        if host and port:
            found.append(f"socks5://{host}:{port}")
    return list(dict.fromkeys(found))


async def read_system_proxies(*, timeout: float = 2.0) -> list[str]:
    """读 macOS 系统代理；其它平台或读取失败返回空列表（只用于展示）。"""

    if platform.system() != "Darwin":
        return []
    try:
        process = await asyncio.create_subprocess_exec(
            "scutil",
            "--proxy",
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.DEVNULL,
        )
    except (OSError, NotImplementedError):
        return []
    try:
        stdout, _ = await asyncio.wait_for(process.communicate(), timeout=timeout)
    except asyncio.TimeoutError:
        process.kill()
        await process.wait()
        return []
    return parse_scutil_proxy(stdout.decode("utf-8", "replace"))


@dataclass(frozen=True, slots=True)
class ProxyChannel:
    """一个候选代理通道；``reachable`` 是 TCP 可达，两个 ``*_ok`` 是经它探测的结果。"""

    source: str
    url: str
    protocol: str = "http"
    reachable: bool | None = None
    github_ok: bool | None = None
    update_api_ok: bool | None = None
    latency_ms: int | None = None
    note: str | None = None

    def public(self) -> dict[str, Any]:
        return {
            "source": self.source,
            "url": mask_proxy_url(self.url),
            "protocol": self.protocol,
            "github_ok": self.github_ok,
            "update_api_ok": self.update_api_ok,
            "latency_ms": self.latency_ms,
            "note": self.note,
        }

    @property
    def usable_for_download(self) -> bool:
        return self.github_ok is True


def _probe_client(url: str) -> httpx.AsyncClient:
    return create_update_http_client(url, timeout=PROXY_PROBE_TIMEOUT)


async def _probe_channel(
    url: str,
    source: str,
    *,
    client_factory: Callable[[str], httpx.AsyncClient] = _probe_client,
) -> ProxyChannel:
    """对单个通道做一次 GitHub 与更新接口探测；失败只记录，不抛。"""

    protocol = "socks" if url.startswith("socks") else "http"
    note: str | None = None
    if protocol == "socks":
        note = "socks 代理未接入更新链路（仅支持 http 代理）"
    channel = ProxyChannel(source=source, url=url, protocol=protocol, note=note)
    if protocol == "socks":
        return channel
    try:
        client = client_factory(url)
    except Exception as exc:  # noqa: BLE001 - 代理 URL 非法时只降级
        logger.info("代理地址不可用（%s）：%s", mask_proxy_url(url), exc)
        return ProxyChannel(
            source=source, url=url, protocol=protocol, reachable=False,
            note=f"代理地址不可用：{exc.__class__.__name__}",
        )
    try:
        started = time.monotonic()
        github_ok = await _probe_url(client, GITHUB_PROBE_URL)
        latency_ms = int((time.monotonic() - started) * 1000)
        update_api_ok = await _probe_url(client, UPDATE_API_PROBE_URL)
        return ProxyChannel(
            source=source,
            url=url,
            protocol=protocol,
            reachable=github_ok or update_api_ok,
            github_ok=github_ok,
            update_api_ok=update_api_ok,
            latency_ms=latency_ms,
            note=note,
        )
    finally:
        try:
            await client.aclose()
        except Exception:  # noqa: BLE001 - 关闭失败不影响探测结论
            pass


async def _probe_url(client: httpx.AsyncClient, url: str) -> bool:
    try:
        response = await client.get(url)
    except Exception:  # noqa: BLE001 - 探测失败就是不可用
        return False
    return response.status_code < 400


async def _tcp_open(host: str, port: int, *, timeout: float = PORT_PROBE_TIMEOUT) -> bool:
    try:
        reader, writer = await asyncio.wait_for(
            asyncio.open_connection(host, port), timeout=timeout
        )
    except (OSError, asyncio.TimeoutError):
        return False
    writer.close()
    try:
        await writer.wait_closed()
    except Exception:  # noqa: BLE001 - 关闭失败不影响探测结论
        pass
    return True


async def scan_local_proxy_ports(
    ports: tuple[int, ...] = DEFAULT_PROXY_PORTS,
    *,
    connector: Callable[[str, int], Awaitable[bool]] | None = None,
) -> list[str]:
    """探测 127.0.0.1 上常见代理端口，返回正在监听的候选 URL（按端口顺序）。"""

    connect = connector or _tcp_open
    results = await asyncio.gather(
        *(connect("127.0.0.1", port) for port in ports),
        return_exceptions=True,
    )
    urls: list[str] = []
    for port, opened in zip(ports, results):
        if opened is True:
            urls.append(f"http://127.0.0.1:{port}")
    return urls


_DISCOVERY_CACHE: tuple[float, list[ProxyChannel], dict[str, Any]] | None = None


def _settings_proxy(settings: Any) -> str:
    return str(getattr(settings, "proxy", "") or "").strip()


def _settings_prefix(settings: Any) -> str:
    updates = getattr(settings, "updates", None)
    return str(getattr(updates, "download_prefix", "") or "").strip()


def _sort_channels(channels: list[ProxyChannel]) -> list[ProxyChannel]:
    def key(channel: ProxyChannel) -> tuple[int, int, int, int]:
        return (
            0 if channel.github_ok is True else 1,
            0 if channel.update_api_ok is True else 1,
            _SOURCE_ORDER.get(channel.source, 9),
            channel.latency_ms if channel.latency_ms is not None else 10_000,
        )

    return sorted(channels, key=key)


async def _discover(
    *,
    settings: Any = None,
    force: bool = False,
    env: Mapping[str, str] | None = None,
    system_reader: Callable[[], Awaitable[list[str]]] | None = None,
    port_scanner: Callable[[], Awaitable[list[str]]] | None = None,
    prober: Callable[[str, str], Awaitable[ProxyChannel]] | None = None,
    clock: Callable[[], float] = time.monotonic,
) -> tuple[list[ProxyChannel], dict[str, Any]]:
    """返回（按可用性排序的通道列表，本机监听端口摘要）；带 30 秒进程内缓存。"""

    global _DISCOVERY_CACHE

    now = clock()
    if (
        not force
        and _DISCOVERY_CACHE is not None
        and now - _DISCOVERY_CACHE[0] < PROXY_DISCOVERY_TTL_SECONDS
    ):
        return list(_DISCOVERY_CACHE[1]), dict(_DISCOVERY_CACHE[2])

    candidates: list[tuple[str, str]] = []
    explicit = _settings_proxy(settings)
    if explicit:
        candidates.append(("settings", explicit))
    read_system = system_reader or read_system_proxies
    for url in await read_system():
        candidates.append(("system", url))
    for url in parse_env_proxies(env):
        candidates.append(("env", url))
    scanner = port_scanner or scan_local_proxy_ports
    listening = await scanner()
    for url in listening:
        candidates.append(("local", url))

    deduped: list[tuple[str, str]] = []
    index_by_url: dict[str, int] = {}
    for source, url in candidates:
        position = index_by_url.get(url)
        if position is not None:
            # 同一地址同时出现在系统代理/环境变量与本机监听端口里时，按本机代理
            # 记：自动决策只采用本机扫描到并探测可用的代理，不能因为先收集到
            # system/env 就丢掉了「它就是本机 127.0.0.1 上的代理」这个事实。
            if source == "local" and deduped[position][0] in {"system", "env"}:
                previous = deduped[position][0]
                deduped[position] = ("local", url)
                logger.debug(
                    "代理 %s 同时是 %s 与本机监听，按本机代理处理",
                    mask_proxy_url(url),
                    previous,
                )
            continue
        index_by_url[url] = len(deduped)
        deduped.append((source, url))

    probe = prober or _probe_channel
    probed = await asyncio.gather(
        *(probe(url, source) for source, url in deduped),
        return_exceptions=True,
    )
    channels: list[ProxyChannel] = []
    for item in probed:
        if isinstance(item, ProxyChannel):
            channels.append(item)
        elif isinstance(item, BaseException):
            logger.debug("代理通道探测异常：%s", item)
    channels = _sort_channels(channels)
    summary = {"listening_ports": listening}
    _DISCOVERY_CACHE = (now, list(channels), dict(summary))
    return channels, summary


async def probe_download_prefix(
    prefix: str,
    *,
    client_factory: Callable[[], httpx.AsyncClient] = create_update_http_client,
    timeout: float = 6.0,
) -> dict[str, Any]:
    """探测一个下载加速前缀是否可达（只打前缀根地址，不验证能否代下 GitHub 资产）。"""

    try:
        normalized = normalize_prefix(prefix)
    except ValueError as exc:
        return {"url": str(prefix), "reachable": False, "note": str(exc)}
    client = client_factory()
    try:
        started = time.monotonic()
        try:
            response = await asyncio.wait_for(client.get(normalized + "/"), timeout=timeout)
            reachable = True
            status = response.status_code
            note = None
        except asyncio.TimeoutError:
            reachable, status, note = False, None, "探测超时"
        except Exception as exc:  # noqa: BLE001 - 探测失败就是不可达
            reachable, status, note = False, None, exc.__class__.__name__
        latency_ms = int((time.monotonic() - started) * 1000)
        return {
            "url": normalized,
            "reachable": reachable,
            "status": status,
            "latency_ms": latency_ms,
            "note": note,
        }
    finally:
        try:
            await client.aclose()
        except Exception:  # noqa: BLE001 - 关闭失败不影响探测结论
            pass


async def discover_proxy_channels(
    *,
    settings: Any = None,
    force: bool = False,
    env: Mapping[str, str] | None = None,
    system_reader: Callable[[], Awaitable[list[str]]] | None = None,
    port_scanner: Callable[[], Awaitable[list[str]]] | None = None,
    prober: Callable[[str, str], Awaitable[ProxyChannel]] | None = None,
    prefix_prober: Callable[[str], Awaitable[dict[str, Any]]] | None = None,
    extra_prefixes: list[str] | None = None,
) -> dict[str, Any]:
    """完整的宿主机代理通道快照（``scan_proxy_channels`` 工具的数据源）。

    ``extra_prefixes`` 是调用方临时给出的下载加速前缀候选（如用户提到某家加速
    服务），只做可达性探测，不写任何设置。
    """

    channels, summary = await _discover(
        settings=settings,
        force=force,
        env=env,
        system_reader=system_reader,
        port_scanner=port_scanner,
        prober=prober,
    )
    configured_prefix = _settings_prefix(settings)
    normalized_prefix: str | None = None
    if configured_prefix:
        try:
            normalized_prefix = normalize_prefix(configured_prefix)
        except ValueError:
            normalized_prefix = None
    prefix_candidates: list[str] = []
    if normalized_prefix:
        prefix_candidates.append(normalized_prefix)
    for item in extra_prefixes or []:
        try:
            candidate = normalize_prefix(item)
        except ValueError:
            continue
        if candidate not in prefix_candidates:
            prefix_candidates.append(candidate)
    probe_prefix = prefix_prober or probe_download_prefix
    prefixes = []
    for candidate in prefix_candidates[:3]:
        prefixes.append(await probe_prefix(candidate))

    recommended = next(
        (channel for channel in channels if channel.usable_for_download), None
    )
    return {
        "channels": [channel.public() for channel in channels],
        "recommended": recommended.public() if recommended is not None else None,
        "download_prefix": {
            "configured": normalized_prefix,
            "probes": prefixes,
        },
        "listening_ports": summary["listening_ports"],
        "notes": [
            "更新链路只在下载阶段使用代理；检查请求不受影响。",
            "系统代理与环境变量本身只作展示，不会自动采用；同一地址如果也在本机监听端口里，只算一条本机代理，自动模式会验证后采用。",
            "自动模式只会采用本机端口扫描发现且到 GitHub 探测可用的 http 代理。",
            "下载加速前缀只探测了根地址是否可达，不代表一定能代理 GitHub 资产。",
        ],
    }


@dataclass
class UpdateNetworkPlan:
    """一次更新任务的下载网络计划；``client`` 由调用方负责关闭。"""

    client: httpx.AsyncClient
    proxy: str | None = None
    proxy_source: str | None = None
    auto_proxy: bool = True
    fallback_note: str | None = None
    download_prefix: str | None = None
    probe: dict[str, Any] | None = None

    def public(self) -> dict[str, Any]:
        return {
            "proxy": mask_proxy_url(self.proxy) if self.proxy else None,
            "proxy_source": self.proxy_source,
            "auto_proxy": self.auto_proxy,
            "fallback_note": self.fallback_note,
            "download_prefix": self.download_prefix,
            "probe": self.probe,
        }

    async def aclose(self) -> None:
        try:
            await self.client.aclose()
        except Exception:  # noqa: BLE001 - 关闭失败不影响任务终态
            pass


def _channel_public(channel: ProxyChannel | None) -> dict[str, Any] | None:
    return channel.public() if channel is not None else None


async def _channels_for_plan(settings: Any = None) -> list[ProxyChannel]:
    """任务决策只需要通道列表：不探测加速前缀，并保留原始（未脱敏）URL。"""
    channels, _ = await _discover(settings=settings, force=False)
    return list(channels)


async def resolve_update_network(
    options: Mapping[str, Any] | None = None,
    *,
    settings: Any = None,
    discoverer: Callable[..., Awaitable[dict[str, Any]]] | None = None,
    client_factory: Callable[[str], httpx.AsyncClient] = create_update_http_client,
    prober: Callable[[str, str], Awaitable[ProxyChannel]] | None = None,
) -> UpdateNetworkPlan:
    """按任务参数与设置决定本次更新的下载通道。

    优先级：任务参数 ``proxy`` > 设置 ``app.proxy`` > 自动发现的可用本机代理 >
    直连。显式代理不可用时按 ``auto_proxy`` 决定是否再尝试自动发现，最后才直连
    ——无论走哪条，失败都会落在下载器的超时重试里，而不是无限等待。
    """

    values = dict(options or {})
    auto_proxy = values.get("auto_proxy", True)
    auto_proxy = True if auto_proxy is None else bool(auto_proxy)
    probe = prober or _probe_channel
    discover = discoverer or _channels_for_plan
    prefix_raw = values.get("download_prefix") or _settings_prefix(settings)
    download_prefix: str | None = None
    if prefix_raw:
        try:
            download_prefix = normalize_prefix(str(prefix_raw))
        except ValueError:
            download_prefix = None

    explicit = str(values.get("proxy") or _settings_proxy(settings) or "").strip()
    explicit_source = "job" if values.get("proxy") else "settings"
    fallback_note: str | None = None
    selected: ProxyChannel | None = None

    if explicit:
        try:
            explicit = normalize_proxy(explicit)
        except ValueError as exc:
            fallback_note = f"显式代理地址不可用（{exc}），本次改为直连"
            explicit = ""
        else:
            selected = await probe(explicit, explicit_source)
            if selected.github_ok is not True:
                fallback_note = (
                    "显式代理探测不可用"
                    f"（github_ok={selected.github_ok}，延迟={selected.latency_ms}ms）"
                )
                selected = None

    if selected is None and auto_proxy:
        candidates = await discover(settings=settings)
        local_candidates = [
            channel
            for channel in candidates
            if channel.source == "local" and channel.github_ok is True
        ]
        if local_candidates:
            selected = local_candidates[0]
            if fallback_note:
                fallback_note += "；已改用本机探测到的可用代理"
        elif not explicit and fallback_note is None:
            fallback_note = "未发现可用的本机代理，本次直连"
        elif explicit and fallback_note is None:
            fallback_note = "自动发现的代理也不可用，本次直连"
    elif selected is None and not explicit and not auto_proxy:
        fallback_note = "已按任务参数禁用代理，本次直连"

    proxy = selected.url if selected is not None else None
    client = client_factory(proxy or "")
    return UpdateNetworkPlan(
        client=client,
        proxy=proxy,
        proxy_source=selected.source if selected is not None else None,
        auto_proxy=auto_proxy,
        fallback_note=fallback_note,
        download_prefix=download_prefix,
        probe=_channel_public(selected),
    )
