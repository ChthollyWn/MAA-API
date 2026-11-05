"""IM 通道适配器协议与公共工具（v3 specs/m85 §4.2、§4.5「适配器」）。

:class:`ChannelAdapter` 是 G1 最小协议（只有 ``send``）的完整版：加上
``platform`` 属性与 ``start`` / ``stop`` 生命周期。四个平台适配器与测试的
:class:`~maa_api.agent.channels.fake.FakeAdapter` 都实现它；``OutboxWorker``
仍然只调用 ``send``。

公共工具在这里集中三件事：

- 从 ``config`` 读设置：G3 之前适配器不依赖 ``Settings`` 类，构造时传
  ``Mapping``。键名同时接受 ``channels.<name>`` 与裸 ``<name>``，方便 G3 直接
  传全量设置或每平台一个小字典。
- 发件箱失败分类：网络错误、429、5xx → ``OutboxRetry``，其余 4xx →
  ``OutboxFatal``（spec §4.5）。
- 延迟导入平台 SDK：缺 extra 时只记日志、返回 ``None``，不在导入期让整个应用
  起不来（spec §4.2）。

``Inbound`` 是平台回调到 :class:`~maa_api.agent.channels.service.ChannelService`
（G3）的统一入站形状；适配器只负责把平台字段映射成它。
"""

from __future__ import annotations

import importlib
import logging
from collections.abc import Awaitable, Callable, Mapping
from dataclasses import dataclass
from typing import Any, Protocol, runtime_checkable

import httpx

from maa_api.agent.channels.errors import OutboxError, OutboxFatal, OutboxRetry
from maa_api.domain.enums import ChannelPlatform

__all__ = [
    "HTTP_TIMEOUT_SECONDS",
    "PLATFORM_EXTRAS",
    "BaseAdapter",
    "ChannelAdapter",
    "Inbound",
    "InboundHandler",
    "build_adapter",
    "build_adapters",
    "config_bool",
    "config_value",
    "error_for_status",
    "field_value",
    "import_sdk",
    "request_json",
    "response_detail",
]

logger = logging.getLogger(__name__)

#: 单次 HTTP 请求的超时；Telegram 长轮询单独放宽。
HTTP_TIMEOUT_SECONDS = 15.0

#: 平台 → (extra 名, PyPI 包名)。Telegram 用已有的 httpx，不在表里。
PLATFORM_EXTRAS: dict[ChannelPlatform, tuple[str, str]] = {
    ChannelPlatform.FEISHU: ("im-feishu", "lark-oapi"),
    ChannelPlatform.DINGTALK: ("im-dingtalk", "dingtalk-stream"),
    ChannelPlatform.QQ: ("im-qq", "qq-botpy"),
}


@dataclass(frozen=True, slots=True)
class Inbound:
    """一条平台入站消息（spec §4.5「适配器」逐字形状）。"""

    platform: ChannelPlatform
    external_user_id: str
    external_chat_id: str
    text: str
    message_id: str


InboundHandler = Callable[[Inbound], Awaitable[None]]


@runtime_checkable
class ChannelAdapter(Protocol):
    """完整适配器协议（spec §4.5）：平台标识 + 启停 + 发送。"""

    platform: ChannelPlatform

    async def start(self) -> None:
        """启动接收；未启用、未配置或没装 extra 时记日志并返回，不抛异常。"""
        ...

    async def stop(self) -> None:
        """停止接收；可重复调用，不抛异常。"""
        ...

    async def send(self, chat_id: str, text: str) -> dict[str, Any]:
        """发送一条文本，成功返回平台回执；失败抛 ``OutboxRetry`` / ``OutboxFatal``。"""
        ...


def config_value(config: Mapping[str, Any], name: str, default: Any = None) -> Any:
    """读 ``channels.<name>``，没有时退回裸 ``name``；``None`` 视作未设置。"""
    for key in (f"channels.{name}", name):
        if key in config and config[key] is not None:
            return config[key]
    return default


def config_bool(value: Any) -> bool:
    """把 YAML/设置里常见的布尔写法收敛成 ``bool``。"""
    if isinstance(value, str):
        return value.strip().lower() in {"1", "true", "yes", "on"}
    return bool(value)


def field_value(obj: Any, name: str, default: Any = None) -> Any:
    """同时支持 dict 与 SDK 对象（lark/botpy 的回调对象不是 Mapping）。"""
    if isinstance(obj, Mapping):
        return obj.get(name, default)
    return getattr(obj, name, default)


def response_detail(response: httpx.Response) -> str:
    """从错误响应里取一段可入库的说明，避免把整个 body 塞进 ``last_error``。"""
    try:
        body = response.json()
    except ValueError:
        body = None
    if isinstance(body, Mapping):
        for key in ("description", "message", "errmsg", "msg", "error"):
            value = body.get(key)
            if value:
                return str(value)[:300]
    return response.text.strip()[:300]


def error_for_status(platform: str, status: int, detail: str = "") -> OutboxError:
    """按 §4.5 分类 HTTP 失败：429 与 5xx 可重试，其余 4xx 致命。"""
    suffix = f"：{detail.strip()}" if detail.strip() else ""
    message = f"{platform} 接口返回 HTTP {status}{suffix}"
    if status == 429 or status >= 500:
        return OutboxRetry(message)
    return OutboxFatal(message)


async def request_json(
    client: httpx.AsyncClient,
    method: str,
    url: str,
    *,
    platform: str,
    **kwargs: Any,
) -> tuple[httpx.Response, dict[str, Any]]:
    """发一次请求并解析 JSON；网络错误与状态码统一换成发件箱异常。"""
    try:
        response = await client.request(method, url, **kwargs)
    except httpx.HTTPError as exc:
        # 只记异常类名：httpx 的异常消息可能带完整 URL（Telegram 的 URL 里有 token）。
        raise OutboxRetry(f"{platform} 网络错误：{type(exc).__name__}") from exc
    if response.status_code >= 400:
        raise error_for_status(
            platform, response.status_code, response_detail(response)
        )
    try:
        body = response.json()
    except ValueError:
        return response, {}
    return response, body if isinstance(body, dict) else {}


def import_sdk(platform: ChannelPlatform, module: str) -> Any | None:
    """延迟导入平台 SDK；缺包时记「未安装」并返回 ``None``（spec §4.2）。"""
    try:
        return importlib.import_module(module)
    except ImportError:
        extra, package = PLATFORM_EXTRAS.get(
            platform, (f"im-{platform}", module)
        )
        logger.warning(
            "%s 适配器未安装：缺少 %s extra（%s），start/send 跳过",
            platform,
            extra,
            package,
        )
        return None


class BaseAdapter:
    """四平台共用的启停骨架：配置读取、客户端管理、回调分发。"""

    platform: ChannelPlatform

    def __init__(
        self,
        config: Mapping[str, Any],
        *,
        http_client: httpx.AsyncClient | None = None,
        on_inbound: InboundHandler | None = None,
    ) -> None:
        self._config = dict(config)
        self._http_client = http_client
        self._owns_client = http_client is None
        self._on_inbound = on_inbound
        self._running = False
        self._proxy: str | None = None

    @property
    def running(self) -> bool:
        return self._running

    def setting(self, name: str, default: Any = None) -> Any:
        """读设置；G3 装配时把 ``Settings.channels`` 的值传进来即可。"""
        return config_value(self._config, name, default)

    def _credential(self, name: str) -> str:
        return str(self.setting(name, "") or "").strip()

    def _start_allowed(self, *, required: tuple[str, ...] = ()) -> bool:
        """未启用或未配置时不启动（spec §4.2「未启用/未配置 → 不启动」）。"""
        if not config_bool(self.setting(f"{self.platform}_enabled", False)):
            logger.info("%s 通道未启用，不启动", self.platform)
            return False
        missing = [name for name in required if not self._credential(name)]
        if missing:
            logger.warning(
                "%s 通道缺少配置 %s，不启动", self.platform, "、".join(missing)
            )
            return False
        return True

    @property
    def _client(self) -> httpx.AsyncClient:
        if self._http_client is None:
            self._http_client = httpx.AsyncClient(
                timeout=HTTP_TIMEOUT_SECONDS, proxy=self._proxy
            )
            self._owns_client = True
        return self._http_client

    async def _close_client(self) -> None:
        if self._owns_client and self._http_client is not None:
            await self._http_client.aclose()
            self._http_client = None

    async def _notify(self, inbound: Inbound) -> None:
        if self._on_inbound is None:
            logger.debug(
                "%s 收到入站消息但未注册回调（message_id=%s）",
                self.platform,
                inbound.message_id,
            )
            return
        await self._on_inbound(inbound)

    async def start(self) -> None:
        raise NotImplementedError

    async def stop(self) -> None:
        self._running = False
        await self._close_client()

    async def send(self, chat_id: str, text: str) -> dict[str, Any]:
        raise NotImplementedError


def _adapter_class(platform: ChannelPlatform) -> type[BaseAdapter]:
    """延迟导入具体平台模块，避免 ``adapter`` 与平台模块循环导入。"""
    if platform is ChannelPlatform.TELEGRAM:
        from maa_api.agent.channels.telegram import TelegramAdapter

        return TelegramAdapter
    if platform is ChannelPlatform.FEISHU:
        from maa_api.agent.channels.feishu import FeishuAdapter

        return FeishuAdapter
    if platform is ChannelPlatform.DINGTALK:
        from maa_api.agent.channels.dingtalk import DingTalkAdapter

        return DingTalkAdapter
    if platform is ChannelPlatform.QQ:
        from maa_api.agent.channels.qq import QQAdapter

        return QQAdapter
    raise ValueError(f"未知的通道平台：{platform!r}")


def build_adapter(
    platform: ChannelPlatform | str,
    config: Mapping[str, Any],
    *,
    http_client: httpx.AsyncClient | None = None,
    on_inbound: InboundHandler | None = None,
) -> ChannelAdapter:
    """按平台构造适配器（G3 装配用）；不启动、不访问网络。"""
    resolved = ChannelPlatform(str(platform))
    return _adapter_class(resolved)(
        config, http_client=http_client, on_inbound=on_inbound
    )


def build_adapters(
    config: Mapping[str, Any],
    *,
    http_client: httpx.AsyncClient | None = None,
    on_inbound: InboundHandler | None = None,
) -> dict[ChannelPlatform, ChannelAdapter]:
    """一次构造四个平台适配器；是否启动由各适配器 ``start`` 自己判断。"""
    return {
        platform: build_adapter(
            platform, config, http_client=http_client, on_inbound=on_inbound
        )
        for platform in ChannelPlatform
    }
