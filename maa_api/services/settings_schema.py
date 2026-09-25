"""Code-owned metadata for the visual settings API (docs/04 §5.6)."""

from __future__ import annotations

import json
import platform
from typing import Any
from pathlib import Path

from maa_api.settings import REPO_ROOT, SETTING_KEYS

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
    "app.maa_core_path": dict(label="MaaCore 路径", group="应用", type="string", hot_action="restart_required"),
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
    "channel.server": dict(label="默认数据服务器", group="渠道", type="string", enum=["CN", "US", "JP", "KR"], hot_action="none"),
    "llm.base_url": dict(label="LLM API 地址", group="LLM", type="string", hot_action="none"),
    "llm.api_key": dict(label="LLM API 密钥", group="LLM", type="string", sensitive=True, hot_action="none"),
    "llm.model": dict(label="LLM 模型", group="LLM", type="string", hot_action="none"),
    "agent.confirmation_timeout_seconds": dict(label="Agent 确认等待时长（秒）", group="Agent", type="integer", minimum=1, hot_action="none"),
    "agent.grant_confirmation_timeout_seconds": dict(label="原子操作授权确认等待时长（秒）", group="Agent", type="integer", minimum=1, hot_action="none"),
    "agent.atomic_grant_minutes": dict(label="原子操作授权窗口（分钟）", group="Agent", type="integer", minimum=1, maximum=60, hot_action="none"),
}

SETTINGS_SCHEMA: tuple[dict[str, Any], ...] = tuple(
    {
        "key": key,
        **_FIELDS[key],
        "readonly": key in SETTING_READONLY_KEYS,
    }
    for key in SETTING_KEYS
)


def schema_for(key: str) -> dict[str, Any]:
    """Return the metadata record for a known key."""
    for item in SETTINGS_SCHEMA:
        if item["key"] == key:
            return item
    raise KeyError(key)
