"""配置加载：``config.yaml`` + 环境变量 + 进程内缓存（docs/02 §6、§7，docs/04 §5.6）。

本模块是旧 ``maa_api/config/config.py`` 的替代者。与旧实现的关键区别只有一条：
**没有任何 import 期副作用** —— 不建目录、不读文件、不 dlopen，``import
maa_api.settings`` 在任何 CWD 下都成功且不产生任何文件。旧实现在 import 期就
``mkdir static/ resource/{lib,log,temp}`` 并可能拷贝 ``daily_task_template.json``，
CWD 不是仓库根时直接 ``RuntimeError``（见 .refactor/ENVIRONMENT.md），这对
「启动第 1 步先加载配置、第 2 步才初始化数据库」的顺序（docs/02 §7）是硬伤。
旧模块暂不删除（``maa_api/main.py`` 与 ``model/core/asst.py`` 还在 import 它），
调用方迁完后由后续卡清理；新代码一律用本模块。

解析优先级（docs/04 §5.6）::

    环境变量 > setting 表 > config.yaml > 代码内默认值

本卡只实现其中的 **config.yaml + 环境变量** 两层与进程内缓存；``setting`` 表的
覆盖层、写回与热生效动作是 M6 的工作。为 M6 留的接入点是 :func:`resolve_settings`
的 ``db_overrides`` 参数：把 ``setting`` 表读成 ``{"adb.address": ...}`` 这样的点分
映射传进去，它会按优先级插在 yaml 与 env 之间；热更新时用 :func:`set_settings`
替换缓存实例（读侧始终走 :func:`get_settings`，不自行解析文件）。

配置文件路径固定为仓库根的 ``config.yaml``（:data:`DEFAULT_CONFIG_PATH`，
``maa_api/settings.py`` → ``parents[1]``），**不依赖 CWD**；旧实现是 CWD 相对路径
``Path() / "config.yaml"``。部署时若把配置放在别处，显式给 :func:`load_settings`
传路径。

环境变量层
----------

命名统一为 ``MAA_`` + 点分 key 的大写、点换下划线（点分 key 与 docs/04 §5.6 的
``setting`` 表 key 一致）：

===========================  ===============================
点分 key                     环境变量
===========================  ===============================
``app.access_token``         ``MAA_APP_ACCESS_TOKEN``
``app.maa_core_path``        ``MAA_APP_MAA_CORE_PATH``
``app.proxy``                ``MAA_APP_PROXY``
``adb.path``                 ``MAA_ADB_PATH``
``adb.address``              ``MAA_ADB_ADDRESS``
``adb.screenshot_quality``   ``MAA_ADB_SCREENSHOT_QUALITY``
===========================  ===============================

映射表在代码里是 :data:`ENV_OVERRIDES`。变量**存在但取值为空串**时按「未设置」
处理：不会把一个合法配置清空，也不会让 int 字段拿到空串；这与设置写入接口
「空串视为不变更」的约定一致（docs/04 §5.6）。

未配置的 ``access_token``
-------------------------

``access_token`` 用**空字符串**表示「未配置」：yaml 里写 ``access_token:``（null）、
写空串、或整个 ``app`` 段缺失，都归一为 ``""``；代码里没有、将来也不会有一个
默认 token。读取方据此判定免鉴权模式（docs/05 §5.2：为空或未配置时全部端点免
鉴权），不区分「没配」与「配了空值」—— 两者对鉴权语义相同，都是关闭鉴权。
其余字段的 null 同样表示「未配置」，回落到默认值（``adb`` 三项的默认值与
``config.template.yaml`` 一致）。

yaml 段/键的取舍
----------------

只识别 :data:`SETTING_KEYS` 里的六个键；未识别的段（如 ``smtp``）与键一律
忽略，后续里程碑往 ``config.yaml`` 加段时旧文件不会报错。已知段写成非映射
（如 ``adb: 25``）抛 :class:`SettingsError` 而不是静默回落默认值——段名写对、
形状写错时静默会让用户以为配置生效了。yaml 解析失败同样抛
:class:`SettingsError`，消息里带文件路径，不静默吞。

本卡不做的事（归属 M6）：``setting`` 表覆盖与写回、字段元信息
（``settings_schema.py`` 的中文标签/取值范围/热生效动作）、敏感项的 ``"***"``
脱敏、``app.access_token`` / ``app.maa_core_path`` / ``adb.path`` 三项的只读
判定（docs/04 §5.6、docs/05 §4.13）。``screenshot_quality`` 这里只校验类型，
不校验 1–95：范围属于设置页的 schema 校验，放在启动路径上会把一个能跑的旧
配置变成起不来。
"""

from __future__ import annotations

import os
from collections.abc import Mapping
from pathlib import Path
from typing import Any

from pydantic import BaseModel, ConfigDict, Field
from ruamel.yaml import YAML
from ruamel.yaml.error import YAMLError

__all__ = [
    "DEFAULT_CONFIG_PATH",
    "ENV_OVERRIDES",
    "ENV_PREFIX",
    "REPO_ROOT",
    "SETTING_KEYS",
    "AdbSettings",
    "Settings",
    "SettingsError",
    "get_settings",
    "load_settings",
    "resolve_settings",
    "set_settings",
]

#: 仓库根：``maa_api/settings.py`` → ``parents[1]``。不依赖 CWD。
REPO_ROOT = Path(__file__).resolve().parents[1]
#: 默认配置文件：仓库根的 ``config.yaml``（config.yaml 不入库，模板见 config.template.yaml）。
DEFAULT_CONFIG_PATH = REPO_ROOT / "config.yaml"
#: 环境变量前缀（docs/04 §5.6 的第三层）。
ENV_PREFIX = "MAA_"

#: 点分 key → ``Settings`` 字段路径。yaml 的 ``app`` / ``adb`` 段、``setting``
#: 表的 key 与环境变量都按这张表解析；表里没有的段/键一律忽略。
SETTING_KEYS: dict[str, tuple[str, ...]] = {
    "app.access_token": ("access_token",),
    "app.maa_core_path": ("maa_core_path",),
    "app.proxy": ("proxy",),
    "adb.path": ("adb", "path"),
    "adb.address": ("adb", "address"),
    "adb.screenshot_quality": ("adb", "screenshot_quality"),
    "log.ring_size": ("log", "ring_size"),
    "log.batch_size": ("log", "batch_size"),
    "log.flush_interval": ("log", "flush_interval"),
    "log.core_min_level": ("log", "core_min_level"),
    "log.persist_maacore_debug_level": ("log", "persist_maacore_debug_level"),
}

#: 点分 key → 环境变量名（``MAA_`` + 大写、点换下划线）。
ENV_OVERRIDES: dict[str, str] = {
    key: ENV_PREFIX + key.upper().replace(".", "_") for key in SETTING_KEYS
}

#: 已识别的 yaml 段：由 :data:`SETTING_KEYS` 推导，用于段形状校验。
_KNOWN_SECTIONS: tuple[str, ...] = tuple(
    dict.fromkeys(key.split(".", 1)[0] for key in SETTING_KEYS)
)


class SettingsError(RuntimeError):
    """配置文件无法解析。消息里一定带文件路径，启动日志可直接定位。"""


class AdbSettings(BaseModel):
    """``config.yaml`` 的 ``adb`` 段（默认值与 ``config.template.yaml`` 一致）。"""

    #: 未识别的键忽略而不是报错：后续里程碑会往段里加键，旧文件必须继续能读。
    model_config = ConfigDict(extra="ignore")

    path: str = "/opt/homebrew/bin/adb"
    address: str = "127.0.0.1:5555"
    screenshot_quality: int = 25


class LogSettings(BaseModel):
    """日志采集与刷盘配置（docs/06 §5.4、§6）。"""

    model_config = ConfigDict(extra="ignore")

    ring_size: int = 2000
    batch_size: int = 200
    flush_interval: float = 1.0
    core_min_level: str = "INF"
    persist_maacore_debug_level: str = "WARNING"


class Settings(BaseModel):
    """全量运行配置（本卡只有 ``config.yaml`` + 环境变量两层来源）。

    ``access_token`` 为 ``""`` 即「未配置」，此时全部端点免鉴权（docs/05 §5.2）。
    """

    #: 同上：未识别的段/键忽略（``smtp`` 段在本卡不建模，M6 的通知配置另建模型）。
    model_config = ConfigDict(extra="ignore")

    access_token: str = ""
    maa_core_path: str = ""
    proxy: str = ""
    adb: AdbSettings = Field(default_factory=AdbSettings)
    log: LogSettings = Field(default_factory=LogSettings)


def resolve_settings(
    file_data: Mapping[str, Any] | None = None,
    *,
    db_overrides: Mapping[str, Any] | None = None,
    env: Mapping[str, str] | None = None,
) -> Settings:
    """把各层配置合并成一个 :class:`Settings`（docs/04 §5.6 的优先级）。

    :param file_data: ``config.yaml`` 解析出的嵌套映射，如
        ``{"app": {"access_token": "x"}, "adb": {"address": "1.2.3.4:5555"}}``。
    :param db_overrides: ``setting`` 表的覆盖项，点分 key 映射，如
        ``{"adb.address": "1.2.3.4:5555"}``。**本卡只提供入口，M6 才接表。**
    :param env: 环境变量映射；``None`` 表示不叠加环境变量层（便于测试与 M6
        显式传入），:func:`load_settings` 传 ``os.environ``。
    :returns: 合并后的配置；某一层里值为 ``None`` 的键表示「这层没配」，继续
        往下层取，四层都没有就用模型默认值。
    """
    merged: dict[str, Any] = {}
    for layer in (_yaml_layer(file_data), _dotted_layer(db_overrides), _env_layer(env)):
        for key, value in layer.items():
            if value is not None:
                merged[key] = value

    payload: dict[str, Any] = {}
    for key, value in merged.items():
        node = payload
        parts = SETTING_KEYS[key]
        for part in parts[:-1]:
            node = node.setdefault(part, {})
        node[parts[-1]] = value
    return Settings.model_validate(payload)


def load_settings(path: str | Path | None = None) -> Settings:
    """读 ``path`` 处的 yaml 并叠加环境变量；文件不存在时返回全默认值。

    ``path`` 为 ``None`` 时用 :data:`DEFAULT_CONFIG_PATH`（仓库根 ``config.yaml``）。
    文件不存在不抛异常，也不创建文件：调用方从 ``access_token == ""`` 等字段值
    看出「未配置」。yaml 解析失败或顶层不是映射时抛 :class:`SettingsError`，
    消息里带文件路径。
    """
    target = Path(path) if path is not None else DEFAULT_CONFIG_PATH
    return resolve_settings(_read_yaml(target), env=os.environ)


#: 进程内缓存；``None`` 表示下次 :func:`get_settings` 重新加载。
_CACHE: Settings | None = None


def get_settings() -> Settings:
    """返回进程内缓存的 :class:`Settings`，首次调用时用 :func:`load_settings` 加载。

    生产代码只读这一个入口，不自行解析 ``config.yaml``；文件改动不会自动生效
    （没有 mtime 轮询），显式调用 :func:`set_settings` 才换缓存。首次加载不是
    线程安全的，但重复加载同一文件是幂等的，因此不需要加锁。
    """
    global _CACHE
    if _CACHE is None:
        _CACHE = load_settings()
    return _CACHE


def set_settings(settings: Settings | None) -> None:
    """替换或清空进程内缓存：传 :class:`Settings` 注入，传 ``None`` 表示下次重新加载。

    供测试注入替身与 M6 的热更新使用（改完 DB 覆盖层后重新解析并换上）。
    """
    global _CACHE
    _CACHE = settings


def _read_yaml(path: Path) -> Mapping[str, Any]:
    """读 yaml 文件；不存在返回空映射。解析失败抛 :class:`SettingsError`。"""
    try:
        with path.open(encoding="utf8") as handle:
            data = YAML(typ="safe").load(handle)
    except FileNotFoundError:
        return {}
    except YAMLError as exc:
        raise SettingsError(
            f"配置文件解析失败：{path}（{type(exc).__name__}: {exc}）"
        ) from exc
    except UnicodeDecodeError as exc:
        raise SettingsError(f"配置文件不是合法的 UTF-8：{path}（{exc}）") from exc

    if data is None:
        return {}
    if not isinstance(data, Mapping):
        raise SettingsError(
            f"配置文件格式错误：{path} 的顶层必须是映射（app / adb 段），"
            f"实际是 {type(data).__name__}"
        )
    for section_name in _KNOWN_SECTIONS:
        section = data.get(section_name)
        if section is not None and not isinstance(section, Mapping):
            # 段名写对但形状写错（如 adb: 25）时静默回落默认值会让用户以为配置生效了，
            # 这里 fail loud；未识别的段（smtp…）仍然整个忽略。
            raise SettingsError(
                f"配置文件格式错误：{path} 的 {section_name} 段必须是映射，"
                f"实际是 {type(section).__name__}"
            )
    return data


def _yaml_layer(file_data: Mapping[str, Any] | None) -> dict[str, Any]:
    """把嵌套 yaml 映射拍平成点分 key；未识别的段/键直接丢掉。"""
    if not file_data:
        return {}
    flat: dict[str, Any] = {}
    for key in SETTING_KEYS:
        section_name, leaf = key.split(".", 1)
        section = file_data.get(section_name)
        if isinstance(section, Mapping) and leaf in section:
            flat[key] = section[leaf]
    return flat


def _dotted_layer(values: Mapping[str, Any] | None) -> dict[str, Any]:
    """只保留已知点分 key（``setting`` 表的覆盖项形态，M6 接入）。"""
    if not values:
        return {}
    return {key: value for key, value in values.items() if key in SETTING_KEYS}


def _env_layer(env: Mapping[str, str] | None) -> dict[str, Any]:
    """按 :data:`ENV_OVERRIDES` 取环境变量；空串视为未设置。"""
    if not env:
        return {}
    return {
        key: env[name]
        for key, name in ENV_OVERRIDES.items()
        if env.get(name)  # 空串（含变量存在但为空）＝未设置
    }
