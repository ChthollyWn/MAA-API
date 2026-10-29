"""宿主机代理通道发现与更新下载网络决策（M61，docs/13 §8.84）。"""

from __future__ import annotations

import asyncio
from types import SimpleNamespace

import pytest

from maa_api.services import update_network as net


@pytest.fixture(autouse=True)
def _clear_discovery_cache():
    net._DISCOVERY_CACHE = None
    yield
    net._DISCOVERY_CACHE = None


def run(coro):
    return asyncio.run(coro)


class FakeClient:
    def __init__(self) -> None:
        self.closed = False

    async def aclose(self) -> None:
        self.closed = True


def _settings(proxy: str = "", prefix: str = "") -> SimpleNamespace:
    return SimpleNamespace(
        proxy=proxy, updates=SimpleNamespace(download_prefix=prefix)
    )


def test_mask_proxy_url_hides_credentials_and_keeps_plain_addresses() -> None:
    assert (
        net.mask_proxy_url("http://user:pass@127.0.0.1:7897")
        == "http://***@127.0.0.1:7897"
    )
    assert net.mask_proxy_url("http://127.0.0.1:7897") == "http://127.0.0.1:7897"
    assert net.mask_proxy_url("") == ""


def test_normalize_proxy_and_prefix_validate_scheme_and_shape() -> None:
    assert net.normalize_proxy(" http://127.0.0.1:7897 ") == "http://127.0.0.1:7897"
    assert net.normalize_prefix("https://ghfast.top/") == "https://ghfast.top"

    for invalid in ("127.0.0.1:7897", "socks5://127.0.0.1:7897", "http://", ""):
        with pytest.raises(ValueError):
            net.normalize_proxy(invalid)
    with pytest.raises(ValueError):
        net.normalize_proxy("http://127.0.0.1:7897/path with space")


def test_parse_env_and_scutil_proxies() -> None:
    env = {
        "HTTPS_PROXY": "http://127.0.0.1:7897",
        "http_proxy": "127.0.0.1:7890",
    }
    assert net.parse_env_proxies(env) == [
        "http://127.0.0.1:7897",
        "http://127.0.0.1:7890",
    ]
    system = """
<dictionary> {
  HTTPEnable : 1
  HTTPPort : 7897
  HTTPProxy : 127.0.0.1
  HTTPSEnable : 1
  HTTPSPort : 7897
  HTTPSProxy : 127.0.0.1
  SOCKSEnable : 0
}
"""
    assert net.parse_scutil_proxy(system) == ["http://127.0.0.1:7897"]


def test_discover_dedupes_sources_sorts_by_usability_and_caches() -> None:
    probes: list[tuple[str, str]] = []

    async def system_reader() -> list[str]:
        return ["http://127.0.0.1:7897"]

    async def port_scanner() -> list[str]:
        return ["http://127.0.0.1:7897", "http://127.0.0.1:1080"]

    async def prober(url: str, source: str) -> net.ProxyChannel:
        probes.append((url, source))
        return net.ProxyChannel(
            source=source,
            url=url,
            reachable=True,
            github_ok=not url.endswith(":1080"),
            update_api_ok=True,
            latency_ms=12,
        )

    async def scenario():
        first = await net.discover_proxy_channels(
            settings=_settings(),
            env={"HTTPS_PROXY": "http://127.0.0.1:7897"},
            system_reader=system_reader,
            port_scanner=port_scanner,
            prober=prober,
            prefix_prober=_no_prefix_probe,
        )
        assert [item["url"] for item in first["channels"]] == [
            "http://127.0.0.1:7897",
            "http://127.0.0.1:1080",
        ]
        assert first["channels"][0]["source"] == "system"
        assert first["recommended"]["url"] == "http://127.0.0.1:7897"
        assert first["listening_ports"] == [
            "http://127.0.0.1:7897",
            "http://127.0.0.1:1080",
        ]
        # 第二次走 30 秒缓存，不再探测。
        second = await net.discover_proxy_channels(
            settings=_settings(), prober=prober, prefix_prober=_no_prefix_probe
        )
        assert probes == [
            ("http://127.0.0.1:7897", "system"),
            ("http://127.0.0.1:1080", "local"),
        ]
        assert second["channels"] == first["channels"]
        # force 跳过缓存（只喂显式代理一个候选，避免打到真实本机端口）。
        await net.discover_proxy_channels(
            settings=_settings(proxy="http://127.0.0.1:9999"),
            force=True,
            env={},
            system_reader=_empty_list,
            port_scanner=_empty_list,
            prober=prober,
            prefix_prober=_no_prefix_probe,
        )
        assert probes[-1] == ("http://127.0.0.1:9999", "settings")
        assert len(probes) == 3

    run(scenario())


async def _no_prefix_probe(url: str) -> dict:
    return {"url": url, "reachable": True, "status": 200, "latency_ms": 1, "note": None}


def test_discover_probes_configured_and_extra_prefixes_without_writing_settings() -> None:
    probed: list[str] = []

    async def prober(url: str, source: str) -> net.ProxyChannel:
        return net.ProxyChannel(source=source, url=url, reachable=False)

    async def prefix_prober(url: str) -> dict:
        probed.append(url)
        return {"url": url, "reachable": True, "status": 200, "latency_ms": 2, "note": None}

    async def scenario():
        result = await net.discover_proxy_channels(
            settings=_settings(prefix="https://ghfast.top/"),
            force=True,
            env={},
            system_reader=_empty_list,
            port_scanner=_empty_list,
            prober=prober,
            prefix_prober=prefix_prober,
            extra_prefixes=[
                "https://gh-proxy.com",
                "https://ghfast.top",
                "not-a-url",
            ],
        )
        # 设置里的前缀 + 去重后的额外前缀（非法地址被忽略），最多 3 个。
        assert probed == ["https://ghfast.top", "https://gh-proxy.com"]
        assert result["download_prefix"]["configured"] == "https://ghfast.top"

    run(scenario())


async def _empty_list() -> list[str]:
    return []


def test_resolve_uses_explicit_job_proxy_when_probe_ok() -> None:
    async def scenario():
        probed: list[tuple[str, str]] = []
        closures: list[FakeClient] = []

        async def prober(url: str, source: str) -> net.ProxyChannel:
            probed.append((url, source))
            return net.ProxyChannel(
                source=source, url=url, github_ok=True, latency_ms=88
            )

        def client_factory(proxy: str) -> FakeClient:
            closures.append(FakeClient())
            return closures[-1]

        plan = await net.resolve_update_network(
            {"proxy": "http://127.0.0.1:7897"},
            settings=_settings(),
            prober=prober,
            client_factory=client_factory,
            discoverer=_unexpected_discover,
        )
        assert probed == [("http://127.0.0.1:7897", "job")]
        assert plan.proxy == "http://127.0.0.1:7897"
        assert plan.proxy_source == "job"
        public = plan.public()
        assert public["proxy"] == "http://127.0.0.1:7897"
        assert public["proxy_source"] == "job"
        await plan.aclose()
        assert closures[0].closed is True

    run(scenario())


def test_resolve_falls_back_to_discovered_local_proxy_when_explicit_unreachable() -> None:
    async def scenario():
        async def prober(url: str, source: str) -> net.ProxyChannel:
            return net.ProxyChannel(source=source, url=url, github_ok=False, latency_ms=None)

        async def discoverer(settings=None) -> list[net.ProxyChannel]:
            return [
                net.ProxyChannel(
                    source="local",
                    url="http://127.0.0.1:7890",
                    github_ok=True,
                    update_api_ok=True,
                    latency_ms=30,
                )
            ]

        plan = await net.resolve_update_network(
            {"proxy": "http://127.0.0.1:7897"},
            settings=_settings(),
            prober=prober,
            discoverer=discoverer,
            client_factory=lambda proxy: FakeClient(),
        )
        assert plan.proxy == "http://127.0.0.1:7890"
        assert plan.proxy_source == "local"
        assert "显式代理探测不可用" in (plan.fallback_note or "")
        assert "改用本机探测到的可用代理" in (plan.fallback_note or "")
        await plan.aclose()

    run(scenario())


def test_resolve_falls_back_to_direct_and_explains_why() -> None:
    async def scenario():
        async def prober(url: str, source: str) -> net.ProxyChannel:
            return net.ProxyChannel(source=source, url=url, github_ok=False)

        async def discoverer(settings=None) -> list[net.ProxyChannel]:
            return []

        closures: list[str] = []

        plan = await net.resolve_update_network(
            {},
            settings=_settings(),
            prober=prober,
            discoverer=discoverer,
            client_factory=lambda proxy: (closures.append(proxy), FakeClient())[1],
        )
        assert plan.proxy is None
        assert closures == [""]
        assert "未发现可用的本机代理" in (plan.fallback_note or "")
        await plan.aclose()

    run(scenario())


def test_resolve_auto_proxy_false_never_discovers() -> None:
    async def scenario():
        called = {"discover": 0}

        async def discoverer(settings=None) -> list[net.ProxyChannel]:
            called["discover"] += 1
            return []

        async def ok_prober(url: str, source: str) -> net.ProxyChannel:
            return net.ProxyChannel(source=source, url=url, github_ok=True)

        # 显式指定了代理时 auto_proxy=false 只关掉「自动发现」，不让显式代理失效。
        plan = await net.resolve_update_network(
            {"auto_proxy": False, "proxy": "http://127.0.0.1:7897"},
            settings=_settings(),
            discoverer=discoverer,
            prober=ok_prober,
            client_factory=lambda proxy: FakeClient(),
        )
        assert plan.proxy == "http://127.0.0.1:7897"
        assert called["discover"] == 0
        await plan.aclose()

        # 没有显式代理时 auto_proxy=false 就是强制直连，连发现都不做。
        plan = await net.resolve_update_network(
            {"auto_proxy": False},
            settings=_settings(),
            discoverer=discoverer,
            client_factory=lambda proxy: FakeClient(),
        )
        assert called["discover"] == 0
        assert plan.proxy is None
        assert "禁用代理" in (plan.fallback_note or "")
        await plan.aclose()

    run(scenario())


def test_resolve_reads_settings_proxy_and_prefix_live() -> None:
    async def scenario():
        async def prober(url: str, source: str) -> net.ProxyChannel:
            return net.ProxyChannel(source=source, url=url, github_ok=True, latency_ms=5)

        plan = await net.resolve_update_network(
            {},
            settings=_settings(
                proxy="http://user:pw@127.0.0.1:7897",
                prefix="https://ghfast.top/",
            ),
            prober=prober,
            client_factory=lambda proxy: FakeClient(),
            discoverer=_unexpected_discover,
        )
        assert plan.proxy_source == "settings"
        assert plan.download_prefix == "https://ghfast.top"
        await plan.aclose()

    run(scenario())


async def _unexpected_discover(settings=None):
    raise AssertionError("不应触发自动发现")
