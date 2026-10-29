"""Agent operations tools backed by the shared update and maintenance services."""

from __future__ import annotations

import asyncio
import json
import time
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field

from maa_api.agent.registry import ToolContext, ToolDefinition, ToolRegistry, ToolRisk
from maa_api.domain.enums import ResourceChannel, UpdateTarget
from maa_api.domain.errors import AppError, ErrorCode
from maa_api.services.update_network import discover_proxy_channels

__all__ = ["register_tools"]

#: ``wait_update`` 的轮询间隔（秒）。更新进度以秒级变化，1 秒足够细。
WAIT_UPDATE_POLL_SECONDS = 1.0
#: 更新终态：``success`` / ``failed`` / ``cancelled`` / ``skipped``。
UPDATE_TERMINAL_STATUSES = frozenset({"success", "failed", "cancelled", "skipped"})


class CheckUpdatesParams(BaseModel):
    model_config = ConfigDict(extra="forbid")

    refresh: bool = False


_PROXY_DESCRIPTION = (
    "本次更新下载使用的代理，如 http://127.0.0.1:7897。不填=自动探测本机可用代理，"
    "探测不到就直连。只想用某个特定代理时先 scan_proxy_channels 看地址再填。"
)
_AUTO_PROXY_DESCRIPTION = (
    "没有显式 proxy 时是否自动探测本机代理。用户说「别用代理」时传 false，"
    "强制本次更新直连。"
)
_PREFIX_DESCRIPTION = (
    "本次更新的 GitHub 下载加速前缀，如 https://ghfast.top/ ；不填=用设置里的值。"
    "只有在确实需要下载加速、且用户同意用某个加速服务时才填。"
)


class UpdateCoreParams(BaseModel):
    model_config = ConfigDict(extra="forbid")

    channel: Literal["stable"] = "stable"
    force: bool = False
    version: str | None = Field(default=None, min_length=1, max_length=64)
    proxy: str | None = Field(default=None, max_length=256, description=_PROXY_DESCRIPTION)
    auto_proxy: bool = Field(default=True, description=_AUTO_PROXY_DESCRIPTION)
    download_prefix: str | None = Field(
        default=None, max_length=512, description=_PREFIX_DESCRIPTION
    )


class UpdateResourceParams(BaseModel):
    model_config = ConfigDict(extra="forbid")

    channel: Literal["ota", "repo", "all"] = "all"
    force: bool = False
    proxy: str | None = Field(default=None, max_length=256, description=_PROXY_DESCRIPTION)
    auto_proxy: bool = Field(default=True, description=_AUTO_PROXY_DESCRIPTION)
    download_prefix: str | None = Field(
        default=None, max_length=512, description=_PREFIX_DESCRIPTION
    )


class UpdateGameParams(BaseModel):
    model_config = ConfigDict(extra="forbid")

    channel: Literal["Official", "Bilibili"] | None = None
    force: bool = False
    proxy: str | None = Field(default=None, max_length=256, description=_PROXY_DESCRIPTION)
    auto_proxy: bool = Field(default=True, description=_AUTO_PROXY_DESCRIPTION)


class ScanProxyChannelsParams(BaseModel):
    model_config = ConfigDict(extra="forbid")

    refresh: bool = Field(
        default=False, description="跳过 30 秒缓存重新扫描与探测。"
    )
    prefixes: list[str] = Field(
        default_factory=list,
        max_length=3,
        description=(
            "额外要探测可达性的下载加速前缀（http(s)://…），最多 3 个；"
            "用户临时提到某家加速服务时用它验证，不会写进设置。"
        ),
    )


class WaitUpdateParams(BaseModel):
    model_config = ConfigDict(extra="forbid")

    update_id: str | None = Field(
        default=None,
        description="update_* 工具返回的 update_id；不填就等当前正在跑的更新。",
    )
    timeout_seconds: int = Field(default=180, ge=1, le=900)


class EmptyParams(BaseModel):
    model_config = ConfigDict(extra="forbid")


def register_tools(registry: ToolRegistry) -> None:
    """Register status and destructive update/restart operations."""
    definitions = (
        ToolDefinition(
            name="check_updates",
            group="ops",
            risk=ToolRisk.SAFE,
            description="查内核、活动资源和游戏本体有没有新版本。用户问要不要更新时先查一次。",
            params_model=CheckUpdatesParams,
            handler=check_updates,
            read_only=True,
        ),
        ToolDefinition(
            name="update_core",
            group="ops",
            risk=ToolRisk.DANGEROUS,
            description=(
                "更新 MaaCore，过程会重启内核，正在跑的流水线会被中断。"
                "更新是异步的，调用后用 wait_update 等它结束。"
            ),
            params_model=UpdateCoreParams,
            handler=update_core,
        ),
        ToolDefinition(
            name="update_resource",
            group="ops",
            risk=ToolRisk.DANGEROUS,
            description=(
                "更新活动资源（活动关卡与图片等本地资源）。用在：用户明确要求；"
                "或内核因为不认识这个关卡而拒绝执行（作业/Fight 都会报 Not support stage："
                "Copilot 任务会读作业里的 stage_name 并在内核资源里校验）；"
                "或 Fight 需要新关卡的地块数据。注意：搜索作业站本身不需要更新资源。"
                "更新是异步的、可能要几分钟，调用后用 wait_update 等它结束，不要反复 get_versions。"
            ),
            params_model=UpdateResourceParams,
            handler=update_resource,
        ),
        ToolDefinition(
            name="update_game",
            group="ops",
            risk=ToolRisk.DANGEROUS,
            description="下载并安装游戏本体 APK。用户要升级游戏客户端时用。",
            params_model=UpdateGameParams,
            handler=update_game,
        ),
        ToolDefinition(
            name="scan_proxy_channels",
            group="ops",
            risk=ToolRisk.SAFE,
            description=(
                "只读地扫描宿主机上可能的代理通道：设置里的 app.proxy、环境变量、"
                "macOS 系统代理、本机常见代理端口（Clash/V2Ray 等）上的监听服务，"
                "并逐个探测经它访问 GitHub 与更新接口是否可用；同时探测设置里的"
                "下载加速前缀。更新下载卡住/失败时先用它看本机有没有可用代理，"
                "再把看中的地址作为 proxy 传给 update_core/update_resource。"
            ),
            params_model=ScanProxyChannelsParams,
            handler=scan_proxy_channels,
            read_only=True,
        ),
        ToolDefinition(
            name="wait_update",
            group="ops",
            risk=ToolRisk.SAFE,
            description=(
                "等一次更新（update_resource/update_core/update_game 返回的 update_id）结束或超时，"
                "返回终态与进度。更新是异步的：提交后用它等结果，不要反复 get_versions。"
                "超时返回 finished=false，更新仍在跑，可以再调一次。"
            ),
            params_model=WaitUpdateParams,
            handler=wait_update,
            read_only=True,
        ),
        ToolDefinition(
            name="restart_core",
            group="ops",
            risk=ToolRisk.DANGEROUS,
            description="重启 MaaCore，会先停掉队列与运行中的流水线。",
            params_model=EmptyParams,
            handler=restart_core,
        ),
    )
    for definition in definitions:
        registry.register(definition)


async def check_updates(
    params: CheckUpdatesParams, context: ToolContext
) -> dict[str, Any]:
    return await _service(context, "update_service").status(refresh=params.refresh)


def _network_options(
    *, proxy: str | None, auto_proxy: bool, download_prefix: str | None = None
) -> dict[str, Any]:
    """把工具参数翻译成 UpdateService 的任务级网络选项（缺省不加键）。"""
    options: dict[str, Any] = {}
    if proxy:
        options["proxy"] = proxy
    if not auto_proxy:
        options["auto_proxy"] = False
    if download_prefix:
        options["download_prefix"] = download_prefix
    return options


async def scan_proxy_channels(
    params: ScanProxyChannelsParams, context: ToolContext
) -> dict[str, Any]:
    return await discover_proxy_channels(
        settings=_settings(context),
        force=params.refresh,
        extra_prefixes=params.prefixes,
    )


async def update_core(params: UpdateCoreParams, context: ToolContext) -> dict[str, Any]:
    options = {
        "channel": params.channel,
        "force": params.force,
        "version": params.version,
    }
    options.update(
        _network_options(
            proxy=params.proxy,
            auto_proxy=params.auto_proxy,
            download_prefix=params.download_prefix,
        )
    )
    return await _start_update(context, UpdateTarget.CORE, options)


async def update_resource(
    params: UpdateResourceParams, context: ToolContext
) -> dict[str, Any]:
    options = {"channel": ResourceChannel(params.channel), "force": params.force}
    options.update(
        _network_options(
            proxy=params.proxy,
            auto_proxy=params.auto_proxy,
            download_prefix=params.download_prefix,
        )
    )
    return await _start_update(context, UpdateTarget.RESOURCE, options)


async def update_game(params: UpdateGameParams, context: ToolContext) -> dict[str, Any]:
    options: dict[str, Any] = {"force": params.force}
    if params.channel is not None:
        options["channel"] = params.channel
    options.update(_network_options(proxy=params.proxy, auto_proxy=params.auto_proxy))
    return await _start_update(context, UpdateTarget.GAME, options)


async def wait_update(
    params: WaitUpdateParams, context: ToolContext
) -> dict[str, Any]:
    """等一次更新到终态（M57，docs/13 §8.80）。

    更新是异步后台任务：``update_*`` 只返回 update_id，模型以前只能反复
    ``get_versions`` 轮询，拿不到变化时会被「原地打转」检测硬停（2026-10-17 实机会话
    seq99）。``wait_update`` 阻塞到终态或超时，返回进度与终态。
    """
    service = _service(context, "update_service")
    started = time.monotonic()
    while True:
        record = await _find_update(service, params.update_id)
        if record is None:
            return {
                "finished": True,
                "update": None,
                "waited_seconds": round(time.monotonic() - started, 1),
                "note": "当前没有进行中的更新",
            }
        summary = _update_summary(record)
        status = str(getattr(record.status, "value", record.status))
        waited = round(time.monotonic() - started, 1)
        if status in UPDATE_TERMINAL_STATUSES:
            return {"finished": True, "update": summary, "waited_seconds": waited}
        if time.monotonic() - started >= params.timeout_seconds:
            return {
                "finished": False,
                "update": summary,
                "waited_seconds": waited,
                "note": "还在跑；可以再调用一次 wait_update 继续等",
            }
        await asyncio.sleep(WAIT_UPDATE_POLL_SECONDS)


async def _find_update(service: Any, update_id: str | None) -> Any:
    """按 id 找更新记录；没给 id 就取当前正在跑的那条。"""
    if update_id:
        return await service.get(update_id)
    status = await service.status()
    running = status.get("running") if isinstance(status, dict) else None
    running_id = running.get("id") if isinstance(running, dict) else None
    if not running_id:
        return None
    return await service.get(str(running_id))


def _update_summary(record: Any) -> dict[str, Any]:
    finished_at = getattr(record, "finished_at", None)
    network: Any = None
    raw_log = getattr(record, "log", None)
    if isinstance(raw_log, str) and raw_log:
        try:
            parsed = json.loads(raw_log)
        except ValueError:
            parsed = None
        if isinstance(parsed, dict):
            candidate = parsed.get("network")
            if isinstance(candidate, dict):
                network = candidate
    return {
        "update_id": str(getattr(record, "id", "")),
        "target": str(getattr(record, "target", "")),
        "channel": getattr(record, "channel", None),
        "status": str(getattr(record, "status", "")),
        "phase": getattr(record, "phase", None),
        "progress": getattr(record, "progress", None),
        "bytes_total": getattr(record, "bytes_total", None),
        "bytes_done": getattr(record, "bytes_done", None),
        "from_version": getattr(record, "from_version", None),
        "to_version": getattr(record, "to_version", None),
        "error_code": getattr(record, "error_code", None),
        "error_message": getattr(record, "error_message", None),
        "finished_at": finished_at.isoformat() if finished_at is not None else None,
        "network": network,
    }


async def restart_core(_params: EmptyParams, context: ToolContext) -> dict[str, Any]:
    return await _service(context, "agent_ops_service").restart_core(caller="agent")


async def _start_update(
    context: ToolContext, target: UpdateTarget, options: dict[str, Any]
) -> dict[str, Any]:
    record = await _service(context, "update_service").start(
        target,
        options,
        caller="agent",
    )
    return {
        "update_id": str(record.id),
        "target": str(getattr(record.target, "value", record.target)),
        "status": str(getattr(record.status, "value", record.status)),
        "note": "更新在后台异步执行：用 wait_update(update_id=…) 等它结束，不要反复 get_versions。",
    }


def _settings(context: ToolContext) -> Any:
    """本次生效设置：优先 `app.state.settings_provider`，否则进程缓存。"""
    state = getattr(getattr(context.request, "app", None), "state", None)
    provider = getattr(state, "settings_provider", None)
    if callable(provider):
        try:
            return provider()
        except Exception:  # noqa: BLE001 - 设置读不到就退回默认配置
            pass
    from maa_api.settings import get_settings

    try:
        return get_settings()
    except Exception:  # noqa: BLE001 - 扫描工具可以无设置继续
        return None


def _service(context: ToolContext, name: str) -> Any:
    state = getattr(getattr(getattr(context.request, "app", None), "state", None), name, None)
    if state is None:
        raise AppError(ErrorCode.SERVICE_UNAVAILABLE, f"{name} 尚未启动")
    return state
