"""配置加载：``config.yaml`` + 环境变量 + 进程内缓存（docs/02 §6、§7，docs/04 §5.6）。

本模块是旧 ``maa_api/config/config.py`` 的替代者。与旧实现的关键区别只有一条：
**没有任何 import 期副作用** —— 不建目录、不读文件、不 dlopen，``import
maa_api.settings`` 在任何 CWD 下都成功且不产生任何文件。旧实现在 import 期就
``mkdir static/ resource/{lib,log,temp}`` 并可能拷贝 ``daily_task_template.json``，
CWD 不是仓库根时直接 ``RuntimeError``（见 docs/ENVIRONMENT.md），这对
「启动第 1 步先加载配置、第 2 步才初始化数据库」的顺序（docs/02 §7）是硬伤。
旧模块暂不删除（``maa_api/main.py`` 与 ``model/core/asst.py`` 还在 import 它），
调用方迁完后由后续卡清理；新代码一律用本模块。

解析优先级（docs/04 §5.6）::

    环境变量 > setting 表 > config.yaml > 代码内默认值

本模块实现 YAML、setting 表覆盖、环境变量与进程内缓存的统一解析；设置元信息、
校验、写入与热生效动作由 ``settings_schema.py`` / ``SettingService`` 提供。把
``setting`` 表读成 ``{"adb.address": ...}`` 这样的点分映射传给
:func:`resolve_settings`，便会按固定优先级合并；热更新时用 :func:`set_settings`
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
``adb.connection_config``    ``MAA_ADB_CONNECTION_CONFIG``
``adb.common_ports``         ``MAA_ADB_COMMON_PORTS``（逗号分隔整数）
``channel.client_type``      ``MAA_CHANNEL_CLIENT_TYPE``
``channel.server``           ``MAA_CHANNEL_SERVER``
``llm.base_url``             ``MAA_LLM_BASE_URL``
``llm.api_key``              ``MAA_LLM_API_KEY``
``llm.model``                ``MAA_LLM_MODEL``
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

只识别 :data:`SETTING_KEYS` 里的键；未识别的段（如 ``smtp``）与键一律
忽略，后续里程碑往 ``config.yaml`` 加段时旧文件不会报错。已知段写成非映射
（如 ``adb: 25``）抛 :class:`SettingsError` 而不是静默回落默认值——段名写对、
形状写错时静默会让用户以为配置生效了。yaml 解析失败同样抛
:class:`SettingsError`，消息里带文件路径，不静默吞。

``app.access_token`` / ``app.maa_core_path`` / ``adb.path`` 在设置 API 中只读
（docs/04 §5.6、docs/05 §4.13）。配置解析校验字段类型；UI 范围和枚举由设置
schema 与服务层执行，避免启动加载路径和设置 API 的验证责任混淆。
"""

from __future__ import annotations

import os
from collections.abc import Mapping
from pathlib import Path
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field
from ruamel.yaml import YAML
from ruamel.yaml.error import YAMLError

__all__ = [
    "CONFIG_ONLY_SETTING_KEYS",
    "DEFAULT_CONFIG_PATH",
    "ENV_OVERRIDES",
    "ENV_PREFIX",
    "REPO_ROOT",
    "SETTING_KEYS",
    "AdbSettings",
    "DeploymentSettings",
    "NotifySettings",
    "Settings",
    "SettingsError",
    "core_user_dir",
    "get_settings",
    "load_settings",
    "resolve_settings",
    "resolve_settings_with_sources",
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
    "updates.download_prefix": ("updates", "download_prefix"),
    "updates.check_hour": ("updates", "check_hour"),
    "agent.confirmation_timeout_seconds": ("agent", "confirmation_timeout_seconds"),
    "agent.grant_confirmation_timeout_seconds": ("agent", "grant_confirmation_timeout_seconds"),
    "agent.question_timeout_seconds": ("agent", "question_timeout_seconds"),
    "agent.atomic_grant_minutes": ("agent", "atomic_grant_minutes"),
    "agent.auto_diagnose_failures": ("agent", "auto_diagnose_failures"),
    "adb.path": ("adb", "path"),
    "adb.address": ("adb", "address"),
    "adb.screenshot_quality": ("adb", "screenshot_quality"),
    "log.ring_size": ("log", "ring_size"),
    "log.batch_size": ("log", "batch_size"),
    "log.flush_interval": ("log", "flush_interval"),
    "log.core_min_level": ("log", "core_min_level"),
    "log.persist_maacore_debug_level": ("log", "persist_maacore_debug_level"),
    "adb.connection_config": ("adb", "connection_config"),
    "adb.common_ports": ("adb", "common_ports"),
    "channel.client_type": ("channel", "client_type"),
    "channel.server": ("channel", "server"),
    "llm.base_url": ("llm", "base_url"),
    "llm.api_key": ("llm", "api_key"),
    "llm.model": ("llm", "model"),
    # VAPID 密钥仍需登记在这张表里：`config.yaml` 与环境变量两层都靠它驱动解析，
    # 表里没有的段会被整个忽略（notify 段曾因此静默读不到值）。
    # 它们被 CONFIG_ONLY_SETTING_KEYS 排除在设置 API 之外。
    "notify.vapid_private_key": ("notify", "vapid_private_key"),
    "notify.vapid_subject": ("notify", "vapid_subject"),
    "deployment.forwarded_allow_ips": ("deployment", "forwarded_allow_ips"),
    "deployment.cors_origins": ("deployment", "cors_origins"),
    "deployment.cors_origin_regex": ("deployment", "cors_origin_regex"),
}

#: 只允许从 ``config.yaml`` / 环境变量提供的 key：**不进设置 API、不落库**。
#:
#: 当前只有 VAPID 密钥：多行 PEM 在设置页的单行输入框里回写会丢换行，而
#: ``sensitive`` 项读取只回 ``***``，用户保存任意其它字段时前端会把 ``***`` 当新值
#: 提交、静默覆盖真实私钥。它还必须与已签发的订阅保持一致，只能由部署者显式编辑。
CONFIG_ONLY_SETTING_KEYS: frozenset[str] = frozenset(
    {"notify.vapid_private_key", "notify.vapid_subject"}
)

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
    connection_config: str = "General"
    common_ports: list[int] = Field(
        default_factory=lambda: [5555, 5556, 7555, 16384, 21503, 62001]
    )


class ChannelSettings(BaseModel):
    """Task channel defaults, independently configurable from task submissions."""

    model_config = ConfigDict(extra="ignore")

    client_type: Literal["Official", "Bilibili", "txwy", "YoStarEN", "YoStarJP", "YoStarKR"] = "Bilibili"
    server: Literal["CN", "US", "JP", "KR"] = "CN"


class LLMSettings(BaseModel):
    """OpenAI-compatible endpoint settings (docs/05 §6.11)."""

    model_config = ConfigDict(extra="ignore")

    base_url: str = "https://api.deepseek.com"
    api_key: str = ""
    # 上游只接受 deepseek-flash 与 deepseek-v4-pro；deepseek-flash 同时支持
    # function calling 与图片输入，因此作为默认值。模型名写错会被上游 400 拒绝。
    model: str = "deepseek-flash"


class LogSettings(BaseModel):
    """日志采集与刷盘配置（docs/06 §5.4、§6）。"""

    model_config = ConfigDict(extra="ignore")

    ring_size: int = 2000
    batch_size: int = 200
    flush_interval: float = 1.0
    core_min_level: str = "INF"
    persist_maacore_debug_level: str = "WARNING"


def core_user_dir() -> Path:
    """MaaCore 的用户数据目录：日志、调试图片与 ``crash.log`` 都落在这里。

    ``AsstSetUserDir`` 收到的就是这个路径（docs/06 §5.1），内核固定写
    ``<user_dir>/debug/asst.log``。启动装配与日志 tail 必须用**同一个**推导结果：
    这两处各自推导过一次不同答案，导致 tailer 长期盯着一份没人写的旧文件。
    """
    return REPO_ROOT / "resource" / "maa-api"


class UpdateSettings(BaseModel):
    """Update scheduling and optional archive mirror configuration."""

    model_config = ConfigDict(extra="ignore")

    download_prefix: str = ""
    check_hour: int = Field(default=9, ge=0, le=23)


class AgentSettings(BaseModel):
    """Confirmation/question timeouts and the bounded internal atomic-operation grant."""

    model_config = ConfigDict(extra="ignore")

    confirmation_timeout_seconds: int = Field(default=600, ge=1)
    grant_confirmation_timeout_seconds: int = Field(default=120, ge=1)
    #: 提问的等待时长（M21，规格 §1.3）：到点未回答就置 expired 并结束本轮。
    #: 与确认超时分开配：确认是「等你授权一次有风险的操作」，提问只是「等你选一个」。
    question_timeout_seconds: int = Field(default=600, ge=1)
    atomic_grant_minutes: int = Field(default=15, ge=1, le=60)
    auto_diagnose_failures: bool = True


class DeploymentSettings(BaseModel):
    """反向代理与浏览器来源白名单（M15，docs/09 §12.4）。

    首版 HTTPS 入口是 ``tailscale serve``：它是反向代理，请求到达 FastAPI 时
    ``X-Forwarded-Proto`` 为 ``https`` 而连接本身是到 ``127.0.0.1`` 的 HTTP。
    因此需要两件事，缺一不可：

    - 信任来自回环的代理头（``forwarded_allow_ips``，喂给 uvicorn 的
      ``--forwarded-allow-ips``）。**默认只信任 127.0.0.1**：信任 ``*`` 等于允许
      任何客户端伪造来源地址与协议，会同时绕过限流与 cookie 的 Secure 判定。
    - 把 ``*.ts.net`` 域名加进 origin 白名单。该域名取决于 tailnet 名与主机名，
      部署时才知道，所以必须是配置项而不是硬编码。
    """

    model_config = ConfigDict(extra="ignore")

    forwarded_allow_ips: str = "127.0.0.1"
    #: 追加的显式 origin（精确匹配），如 ``https://host.tailnet.ts.net``。
    cors_origins: list[str] = Field(default_factory=list)
    #: 追加的 origin 正则（整条匹配），用于 ``*.ts.net`` 这类通配域名。
    cors_origin_regex: str = ""


class NotifySettings(BaseModel):
    """Web Push 的 VAPID 密钥与联络地址（docs/09 §13.1）。

    私钥只从 ``config.yaml`` 取，**不在服务启动时自动生成**：每次启动换密钥会让所有
    既有订阅静默失效。生成入口是 ``scripts/generate_vapid_keys.py``。

    **刻意不登记在** :data:`SETTING_KEYS` **里**，因此设置 API 无法读写它：

    - 它是多行 PEM，设置页的单行输入框回写会把换行压掉，得到一个不可解析的密钥；
    - 它是 ``sensitive`` 项，读取时只回 ``***``；一旦经由设置 API 落库，用户在设置页
      保存任意**其它**字段时，前端会把 ``***`` 当新值提交，从而静默覆盖真实私钥；
    - 它必须与公钥严格对应。让它只来自部署者显式编辑的 ``config.yaml``，与
      "VAPID 密钥必须稳定" 这条要求是同一个约束的两面。
    """

    model_config = ConfigDict(extra="ignore")

    vapid_private_key: str = ""
    #: JWT 的 ``sub``，必须是 ``mailto:`` 或站点 URL。推送服务用它联系站点管理员。
    vapid_subject: str = "mailto:admin@example.com"


class Settings(BaseModel):
    """全量运行配置（env > DB > YAML > code defaults）。

    ``access_token`` 为 ``""`` 即「未配置」，此时全部端点免鉴权（docs/05 §5.2）。
    """

    #: 同上：未识别的段/键忽略（``smtp`` 段在本卡不建模，M6 的通知配置另建模型）。
    model_config = ConfigDict(extra="ignore")

    access_token: str = ""
    maa_core_path: str = ""
    proxy: str = ""
    adb: AdbSettings = Field(default_factory=AdbSettings)
    log: LogSettings = Field(default_factory=LogSettings)
    updates: UpdateSettings = Field(default_factory=UpdateSettings)
    agent: AgentSettings = Field(default_factory=AgentSettings)
    channel: ChannelSettings = Field(default_factory=ChannelSettings)
    llm: LLMSettings = Field(default_factory=LLMSettings)
    notify: NotifySettings = Field(default_factory=NotifySettings)
    deployment: DeploymentSettings = Field(default_factory=DeploymentSettings)


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
        ``{"adb.address": "1.2.3.4:5555"}``。
    :param env: 环境变量映射；``None`` 表示不叠加环境变量层（便于测试与 M6
        显式传入），:func:`load_settings` 传 ``os.environ``。
    :returns: 合并后的配置；某一层里值为 ``None`` 的键表示「这层没配」，继续
        往下层取，四层都没有就用模型默认值。
    """
    return resolve_settings_with_sources(
        file_data, db_overrides=db_overrides, env=env
    )[0]


def resolve_settings_with_sources(
    file_data: Mapping[str, Any] | None = None,
    *,
    db_overrides: Mapping[str, Any] | None = None,
    env: Mapping[str, str] | None = None,
) -> tuple[Settings, dict[str, str]]:
    """Resolve effective settings and identify the winning layer for each key."""
    layers = (
        ("yaml", _yaml_layer(file_data)),
        ("db", _dotted_layer(db_overrides)),
        ("env", _env_layer(env)),
    )
    merged: dict[str, Any] = {}
    sources: dict[str, str] = {}
    for source, layer in layers:
        for key, value in layer.items():
            if value is not None:
                merged[key] = value
                sources[key] = source

    payload: dict[str, Any] = {}
    for key, value in merged.items():
        node = payload
        parts = SETTING_KEYS[key]
        for part in parts[:-1]:
            node = node.setdefault(part, {})
        node[parts[-1]] = value
    settings = Settings.model_validate(payload)
    for key in SETTING_KEYS:
        sources.setdefault(key, "default")
    return settings, sources


def load_settings(
    path: str | Path | None = None,
    *,
    db_overrides: Mapping[str, Any] | None = None,
    env: Mapping[str, str] | None = None,
) -> Settings:
    """读 ``path`` 处的 yaml 并叠加环境变量；文件不存在时返回全默认值。

    ``path`` 为 ``None`` 时用 :data:`DEFAULT_CONFIG_PATH`（仓库根 ``config.yaml``）。
    文件不存在不抛异常，也不创建文件：调用方从 ``access_token == ""`` 等字段值
    看出「未配置」。yaml 解析失败或顶层不是映射时抛 :class:`SettingsError`，
    消息里带文件路径。
    """
    target = Path(path) if path is not None else DEFAULT_CONFIG_PATH
    return resolve_settings(
        _read_yaml(target), db_overrides=db_overrides,
        env=os.environ if env is None else env,
    )


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
    result: dict[str, Any] = {}
    for key, name in ENV_OVERRIDES.items():
        raw = env.get(name)
        if not raw:  # 空串（含变量存在但为空）＝未设置
            continue
        if key == "adb.common_ports":
            # Comma separated integer list; an empty list means no override.
            ports = [part.strip() for part in raw.split(",") if part.strip()]
            if ports:
                result[key] = ports
        else:
            result[key] = raw
    return result
