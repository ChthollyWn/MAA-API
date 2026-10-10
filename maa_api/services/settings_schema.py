"""Code-owned metadata for the visual settings API (docs/04 §5.6)."""

from __future__ import annotations

import json
import platform
from typing import Any
from pathlib import Path

from maa_api.settings import CONFIG_ONLY_SETTING_KEYS, REPO_ROOT, SETTING_KEYS

__all__ = ["SETTINGS_SCHEMA", "SETTING_READONLY_KEYS"]

SETTING_READONLY_KEYS = frozenset(
    {"app.access_token", "app.maa_core_path", "adb.path"}
)


def _connection_config_names() -> list[str]:
    """Load MaaCore's supported ADB connection names in resource order."""
    platform_name = "Darwin" if platform.system() == "Darwin" else "Linux"
    platforms = ["Darwin", platform_name, "Linux"]
    paths = [
        REPO_ROOT / "resource" / "lib" / "maa" / name / "resource" / "config.json"
        for name in dict.fromkeys(platforms)
    ]
    for path in paths:
        try:
            document = json.loads(path.read_text(encoding="utf8"))
            entries = document["connection"]
            if not isinstance(entries, list):
                continue
            names = [
                entry["configName"]
                for entry in entries
                if isinstance(entry, dict)
                and isinstance(entry.get("configName"), str)
                and entry["configName"].strip()
            ]
            if names:
                return list(dict.fromkeys(names))
        except (OSError, ValueError, KeyError, TypeError):
            continue
    return ["General"]

_FIELDS: dict[str, dict[str, Any]] = {
    "app.access_token": dict(label="访问密钥", group="应用", type="string", sensitive=True, hot_action="restart_required"),
    "app.maa_core_path": dict(label="内核路径", group="应用", type="string", hot_action="restart_required"),
    "app.proxy": dict(label="系统代理", group="应用", type="string", hot_action="none"),
    "updates.download_prefix": dict(label="更新下载前缀", group="更新", type="string", hot_action="restart_required"),
    "updates.check_hour": dict(label="每日更新检查时间（小时）", group="更新", type="integer", minimum=0, maximum=23, hot_action="restart_required"),
    "adb.path": dict(label="ADB 路径", group="设备", type="string", hot_action="restart_required"),
    "adb.address": dict(label="设备地址", group="设备", type="string", hot_action="reconnect"),
    "adb.screenshot_quality": dict(label="截图质量", group="设备", type="integer", minimum=1, maximum=95, hot_action="none"),
    "adb.connection_config": dict(label="ADB 连接配置", group="设备", type="string", enum=_connection_config_names(), hot_action="reconnect"),
    "adb.common_ports": dict(label="模拟器常见端口", group="设备", type="integer[]", minimum=1, maximum=65535, hot_action="none"),
    "log.ring_size": dict(label="日志缓冲条数", group="日志", type="integer", minimum=1, maximum=100000, hot_action="none"),
    "log.batch_size": dict(label="日志刷盘批量", group="日志", type="integer", minimum=1, maximum=10000, hot_action="none"),
    "log.flush_interval": dict(label="日志刷盘间隔", group="日志", type="number", minimum=0.05, maximum=60, hot_action="none"),
    "log.core_min_level": dict(label="内核日志最低级别", group="日志", type="string", enum=["OFF", "FAT", "ERR", "WRN", "INF", "DBG", "TRC"], hot_action="none"),
    "log.persist_maacore_debug_level": dict(label="内核调试日志落盘级别", group="日志", type="string", enum=["OFF", "FATAL", "ERROR", "WARNING", "INFO", "DEBUG"], hot_action="none"),
    "channel.client_type": dict(label="默认客户端渠道", group="渠道", type="string", enum=["Official", "Bilibili"], hot_action="none"),
    # M25 的「外部数据源」分区。全部 hot_action="none"：访问层每次请求现读设置。
    "sources.enabled": dict(label="启用外部数据源", group="外部数据源", type="boolean", hot_action="none"),
    "sources.use_proxy": dict(label="外部请求走系统代理", group="外部数据源", type="boolean", hot_action="none"),
    "sources.cache_live_seconds": dict(label="当期数据缓存时长（秒）", group="外部数据源", type="integer", minimum=60, maximum=3600, hot_action="none"),
    "sources.cache_static_seconds": dict(label="稳定数据缓存时长（秒）", group="外部数据源", type="integer", minimum=600, maximum=86400, hot_action="none"),
    "sources.cache_max_mb": dict(label="缓存内存上限（MB）", group="外部数据源", type="integer", minimum=8, maximum=512, hot_action="none"),
    "sources.prts_enabled": dict(label="启用 PRTS", group="外部数据源", type="boolean", hot_action="none"),
    "sources.prts_rate_per_minute": dict(label="PRTS 每分钟请求上限", group="外部数据源", type="integer", minimum=1, maximum=120, hot_action="none"),
    # M26 的企鹅物流（掉率）。主机两个内容一致，失败时自动改试另一个。
    "sources.penguin_enabled": dict(label="启用企鹅物流", group="外部数据源", type="boolean", hot_action="none"),
    "sources.penguin_host": dict(label="企鹅物流主机", group="外部数据源", type="string", enum=["penguin-stats.io", "penguin-stats.cn"], hot_action="none"),
    "sources.penguin_rate_per_minute": dict(label="企鹅物流每分钟请求上限", group="外部数据源", type="integer", minimum=1, maximum=60, hot_action="none"),
    "channel.server": dict(label="默认数据服务器", group="渠道", type="string", enum=["CN", "US", "JP", "KR"], hot_action="none"),
    "llm.base_url": dict(label="LLM API 地址", group="LLM", type="string", hot_action="none"),
    "llm.api_key": dict(label="LLM API 密钥", group="LLM", type="string", sensitive=True, hot_action="none"),
    "llm.model": dict(label="LLM 模型", group="LLM", type="string", hot_action="none"),
    "agent.confirmation_timeout_seconds": dict(label="Agent 确认等待时长（秒）", group="Agent", type="integer", minimum=1, hot_action="none"),
    "agent.grant_confirmation_timeout_seconds": dict(label="屏幕操作授权确认等待时长（秒）", group="Agent", type="integer", minimum=1, hot_action="none"),
    "agent.question_timeout_seconds": dict(label="Agent 提问等待时长（秒）", group="Agent", type="integer", minimum=1, hot_action="none"),
    "agent.atomic_grant_minutes": dict(label="屏幕操作授权窗口（分钟）", group="Agent", type="integer", minimum=1, maximum=60, hot_action="none"),
    "agent.auto_diagnose_failures": dict(label="自动诊断流水线与内核故障", group="Agent", type="boolean", hot_action="none"),
    # M27 账号数据的过期阈值：概要接口与注入段现读设置，改完立即生效。
    "agent.box_stale_days": dict(label="干员识别过期天数", group="Agent", type="integer", minimum=1, maximum=365, hot_action="none"),
    "agent.depot_stale_days": dict(label="仓库识别过期天数", group="Agent", type="integer", minimum=1, maximum=60, hot_action="none"),
    # 下面两项只从 config.yaml 提供，不出现在设置 API 的响应里（见 CONFIG_ONLY_SETTING_KEYS）。
    # 这里保留元数据是为了让这张表覆盖全部 key，加 key 时不会漏掉归属与说明。
    "notify.vapid_private_key": dict(label="Web Push VAPID 私钥", group="通知", type="string", sensitive=True, hot_action="restart_required"),
    "notify.vapid_subject": dict(label="Web Push 联络邮箱", group="通知", type="string", hot_action="restart_required"),
    # 只在有反向代理（tailscale serve / Caddy）时才有意义；改完必须重启 uvicorn。
    "deployment.forwarded_allow_ips": dict(label="信任的代理来源", group="部署", type="string", hot_action="restart_required"),
    "deployment.cors_origins": dict(label="附加浏览器来源白名单", group="部署", type="string[]", hot_action="restart_required"),
    "deployment.cors_origin_regex": dict(label="附加来源正则", group="部署", type="string", hot_action="restart_required"),
}

#: 只从 ``config.yaml`` / 环境变量提供的 key 不出现在设置 API 里（见 settings.py 的说明）。
_SCHEMA_KEYS = tuple(key for key in SETTING_KEYS if key not in CONFIG_ONLY_SETTING_KEYS)

SETTINGS_SCHEMA: tuple[dict[str, Any], ...] = tuple(
    {
        "key": key,
        **_FIELDS[key],
        "readonly": key in SETTING_READONLY_KEYS,
    }
    for key in _SCHEMA_KEYS
)


def schema_for(key: str) -> dict[str, Any]:
    """Return the metadata record for a known key."""
    for item in SETTINGS_SCHEMA:
        if item["key"] == key:
            return item
    raise KeyError(key)
