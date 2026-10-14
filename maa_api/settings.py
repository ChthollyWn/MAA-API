"""配置加载：``config.yaml`` + 环境变量 + 进程内缓存（docs/02 §6、§7，docs/04 §5.6）。

解析优先级：环境变量 > setting 表 > config.yaml > 代码内默认值。读侧只走
:func:`get_settings`，热更新用 :func:`set_settings` 替换缓存实例；设置元信息、校验与
写入在 ``settings_schema.py`` / ``SettingService``。

import 期无副作用：不建目录、不读文件、不 dlopen。配置文件固定为仓库根的
``config.yaml``（:data:`DEFAULT_CONFIG_PATH`），不依赖 CWD；放在别处时给
:func:`load_settings` 传路径。

环境变量名是 ``MAA_`` + 点分 key 的大写、点换下划线，映射表在 :data:`ENV_OVERRIDES`；
变量存在但为空串时按「未设置」处理。``access_token`` 用空串表示「未配置」：yaml 写
null、写空串或整个 ``app`` 段缺失都归一为 ``""``，鉴权据此关闭（docs/05 §5.2）。

只识别 :data:`SETTING_KEYS` 里的键，未识别的段与键忽略；已知段写成非映射（如
``adb: 25``）或 yaml 解析失败时抛 :class:`SettingsError`，不静默回落默认值。
``app.access_token`` / ``app.maa_core_path`` / ``adb.path`` 在设置 API 里只读。
"""

from __future__ import annotations

import os
from collections.abc import Mapping
from pathlib import Path
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator
from ruamel.yaml import YAML
from ruamel.yaml.error import YAMLError

__all__ = [
    "CONFIG_ONLY_SETTING_KEYS",
    "DEFAULT_CONFIG_PATH",
    "ENV_OVERRIDES",
    "ENV_PREFIX",
    "MIN_AUTO_SYNC_INTERVAL_MINUTES",
    "REPO_ROOT",
    "SETTING_KEYS",
    "AdbSettings",
    "CoreSettings",
    "DeploymentSettings",
    "NotifySettings",
    "Settings",
    "SklandSettings",
    "SourcesSettings",
    "SettingsError",
    "WorkspaceSettings",
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
#: 定时同步的最短间隔（分钟，M42 规格 §4.5）：森空岛是非官方接口，比每小时更密
#: 的轮询换不来信息量，只增加风控面。``0`` 表示关闭定时同步（默认）。
MIN_AUTO_SYNC_INTERVAL_MINUTES = 60
#: 手动同步的冷却（秒，M42 规格 §4.5）：冷却内的重复提交回 429 ``RATE_LIMITED``。
SKLAND_MIN_SYNC_INTERVAL_SECONDS = 60
#: 单次上游取数里 ``chars`` 数量的护栏下限（M42 规格 §4.5）：低于它整轮不写库，
#: 免得半截响应把整箱干员标成「未拥有」。W0 实测后定稿（当前 200）。
SKLAND_MIN_CHARS = 200

#: 点分 key → ``Settings`` 字段路径。yaml 的 ``app`` / ``adb`` 段、``setting``
#: 表的 key 与环境变量都按这张表解析；表里没有的段/键忽略。
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
    # 账号数据的过期阈值（M27，规格 §7.3、附录 B）：识别结果超过这些天数即提示重新识别。
    "sources.gamedata_mirrors": ("sources", "gamedata_mirrors"),
    "sources.gamedata_github_api_fallback": ("sources", "gamedata_github_api_fallback"),
    "agent.box_stale_days": ("agent", "box_stale_days"),
    "agent.copilot_grant_minutes": ("agent", "copilot_grant_minutes"),
    "agent.copilot_retention_days": ("agent", "copilot_retention_days"),
    "agent.copilot_attempt_timeout_minutes": ("agent", "copilot_attempt_timeout_minutes"),
    "agent.depot_stale_days": ("agent", "depot_stale_days"),
    # M45 W4 仓库写库闸门：上一轮条目数 ≥ depot_drop_min_prev 时，本轮低于
    # 上一轮 × depot_drop_ratio 就整轮不写（与 agent.depot_min_items 同一族）。
    "agent.depot_drop_min_prev": ("agent", "depot_drop_min_prev"),
    "agent.depot_drop_ratio": ("agent", "depot_drop_ratio"),
    # 作业研究（M29，规格 §9.6/§9.7/§9.11）：授权窗口、单次尝试的等待上限、
    # AI 草稿与作业站副本的保留天数。
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
    # M38 内核旋钮：识别间隔（毫秒）。开机时由 maa_api.core.kernel_options 生成一层
    # 资源覆盖内核自带的 500 ms，改完要重启服务才会生效。
    "core.task_delay": ("core", "task_delay"),
    # ---- 外部数据源（M25，附录 B）。M25 只登记 PRTS 相关键与公共键。----
    "sources.enabled": ("sources", "enabled"),
    "sources.use_proxy": ("sources", "use_proxy"),
    "sources.cache_live_seconds": ("sources", "cache_live_seconds"),
    "sources.cache_static_seconds": ("sources", "cache_static_seconds"),
    # M29 拆包关卡文件的缓存时长（附录 B）。
    "sources.cache_level_seconds": ("sources", "cache_level_seconds"),
    "sources.cache_max_mb": ("sources", "cache_max_mb"),
    "sources.prts_enabled": ("sources", "prts_enabled"),
    "sources.prts_rate_per_minute": ("sources", "prts_rate_per_minute"),
    "sources.penguin_enabled": ("sources", "penguin_enabled"),
    "sources.penguin_host": ("sources", "penguin_host"),
    "sources.penguin_rate_per_minute": ("sources", "penguin_rate_per_minute"),
    # M29 作业站（prts.maa.plus，规格 §9.5、附录 B）：开关与每分钟请求上限。
    "sources.copilot_site_enabled": ("sources", "copilot_site_enabled"),
    "sources.copilot_site_rate_per_minute": ("sources", "copilot_site_rate_per_minute"),
    # M29 拆包数据（规格 §9.3、附录 B）。
    "sources.gamedata_enabled": ("sources", "gamedata_enabled"),
    "sources.gamedata_rate_per_minute": ("sources", "gamedata_rate_per_minute"),
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
    # ---- 森空岛账号数据（M42 规格 §4.4、§4.5）。三项都只从 config.yaml / 环境变量来，
    # 同时列进 CONFIG_ONLY_SETTING_KEYS：凭据密钥是敏感项，定时间隔与超时不需要 UI
    # 开关（本里程碑明确不做前端定时开关，见规格 §3.2）。----
    "skland.credential_key": ("skland", "credential_key"),
    "skland.auto_sync_interval_minutes": ("skland", "auto_sync_interval_minutes"),
    "skland.request_timeout_seconds": ("skland", "request_timeout_seconds"),
    # ---- 只读探索与侦察（M48 规格 §4.1）。四项都只从 config.yaml / 环境变量来，
    # 同时列进 CONFIG_ONLY_SETTING_KEYS：``workspace.root`` 是宿主机绝对路径，放开给
    # 设置 API 等于让任何持有访问令牌的人把 agent 的只读窗口指向任意目录；三个预算是
    # 运维阈值，前端没有对应的产品语义（与 M42 的 skland 三项同款理由）。----
    "workspace.root": ("workspace", "root"),
    "agent.recon_max_steps": ("agent", "recon_max_steps"),
    "agent.recon_output_chars": ("agent", "recon_output_chars"),
    "agent.recon_max_chars": ("agent", "recon_max_chars"),
}

#: 只允许从 ``config.yaml`` / 环境变量提供的 key：**不进设置 API、不落库**。
#:
#: VAPID 密钥：多行 PEM 在设置页的单行输入框里回写会丢换行，而 ``sensitive`` 项读取
#: 只回 ``***``，用户保存任意其它字段时前端会把 ``***`` 当新值提交、静默覆盖真实私钥。
#: 它还必须与已签发的订阅保持一致，只能由部署者显式编辑。
#:
#: 森空岛三项（M42）：``credential_key`` 是凭据加密密钥（同「敏感项不走设置 API」的理由）；
#: ``auto_sync_interval_minutes`` 与 ``request_timeout_seconds`` 是运维阈值，本里程碑
#: 不做前端开关（规格 §3.2），放进设置 API 会凭空生成一组没有产品语义的表单控件。
#:
#: 只读探索四项（M48）：``workspace.root`` 是宿主机绝对路径，放开给设置 API 等于让任何
#: 持有访问令牌的人把 agent 的只读窗口指向任意目录（探索工具只拦越界读，拦不住
#: 「把根设到别处」）；三个 ``recon_*`` 预算是运维阈值，与 skland 两项同理没有前端语义。
CONFIG_ONLY_SETTING_KEYS: frozenset[str] = frozenset(
    {
        "notify.vapid_private_key",
        "notify.vapid_subject",
        "skland.credential_key",
        "skland.auto_sync_interval_minutes",
        "skland.request_timeout_seconds",
        "workspace.root",
        "agent.recon_max_steps",
        "agent.recon_output_chars",
        "agent.recon_max_chars",
    }
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


class CoreSettings(BaseModel):
    """``config.yaml`` 的 ``core`` 段：内核进程级旋钮（M38，docs/13 §8.53）。

    ``task_delay`` 是 MaaCore 的识别间隔：每个识别节点结束后等这么久再试下一个。
    内核自带 500 ms，实测它是练度扫描里最大的时间开销——读一屏 16 格 13.3 s；
    降到 100 ms 后 6.6 s，20 次读屏全部 16/16 命中。0 最快（4.7 s），但整链瞬时
    占满一个核，所以默认留在 100。
    """

    #: 未识别的键忽略：后续里程碑会往段里加键，旧文件必须继续能读。
    model_config = ConfigDict(extra="ignore")

    task_delay: int = Field(default=100, ge=0, le=2000)


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


class SourcesSettings(BaseModel):
    """外部数据源（M25，规格 §3.2、§4.6、附录 B）。

    访问层每次请求现读这个段（全部 ``hot_action="none"``），所以改开关或限流立即生效。
    M25 只登记 PRTS 相关键与公共键，企鹅物流（M26）、作业站与拆包数据（M29）的键
    在各自的里程碑加。
    """

    model_config = ConfigDict(extra="ignore")

    enabled: bool = True
    use_proxy: bool = True
    cache_live_seconds: int = Field(default=300, ge=60, le=3600)
    cache_static_seconds: int = Field(default=21600, ge=600, le=86400)
    #: M29 拆包关卡文件（几个月才变一次）的缓存时长，默认 24 小时（附录 B）。
    cache_level_seconds: int = Field(default=86400, ge=3600, le=604800)
    #: 拆包关卡数据的镜像顺序（规格 §9.3）；两个默认镜像都失败时才走 GitHub API。
    gamedata_mirrors: list[str] = Field(
        default_factory=lambda: [
            "https://fastly.jsdelivr.net/gh/Kengxxiao/ArknightsGameData@master",
            "https://cdn.jsdelivr.net/gh/Kengxxiao/ArknightsGameData@master",
        ]
    )
    gamedata_github_api_fallback: bool = True
    gamedata_enabled: bool = True
    gamedata_rate_per_minute: int = Field(default=10, ge=1, le=60)
    cache_max_mb: int = Field(default=64, ge=8, le=512)
    prts_enabled: bool = True
    prts_rate_per_minute: int = Field(default=30, ge=1, le=120)
    #: M26 企鹅物流。两个主机的内容一致，``.io`` 失败一次后自动改试 ``.cn``（规格 §6.6）。
    penguin_enabled: bool = True
    penguin_host: Literal["penguin-stats.io", "penguin-stats.cn"] = "penguin-stats.io"
    penguin_rate_per_minute: int = Field(default=10, ge=1, le=60)
    #: M29 作业站（``prts.maa.plus``，附录 B）。关掉后作业搜索与导入直接报来源已关闭；
    #: 改开关或限流立即生效（与其它来源一样每次请求现读）。
    copilot_site_enabled: bool = True
    copilot_site_rate_per_minute: int = Field(default=20, ge=1, le=60)


class AgentSettings(BaseModel):
    """Confirmation/question timeouts and the bounded internal atomic-operation grant."""

    model_config = ConfigDict(extra="ignore")

    confirmation_timeout_seconds: int = Field(default=600, ge=1)
    grant_confirmation_timeout_seconds: int = Field(default=120, ge=1)
    #: 提问的等待时长（规格 §1.3）：到点未回答就置 expired 并结束本轮。
    #: 与确认超时分开配：确认是「等你授权一次有风险的操作」，提问只是「等你选一个」。
    question_timeout_seconds: int = Field(default=600, ge=1)
    atomic_grant_minutes: int = Field(default=15, ge=1, le=60)
    auto_diagnose_failures: bool = True
    #: 账号数据（M27，规格 §7.3）：干员 / 仓库识别结果超过这些天数即「已过期」，
    #: 概要接口与注入段据此提示重新识别。取值范围见附录 B。
    box_stale_days: int = Field(default=14, ge=1, le=365)
    #: 作业授权有效期（规格 §9.6）；附录 B 写 10–120，实现放宽到 1–1440 并在 docs/13 §8.44 记偏离。
    copilot_grant_minutes: int = Field(default=60, ge=1, le=1440)
    #: AI 草稿与作业站副本的保留天数（§9.11）。
    copilot_retention_days: int = Field(default=7, ge=1, le=365)
    #: 一次作业尝试的最长等待（§9.7 第 4 步）。
    copilot_attempt_timeout_minutes: int = Field(default=20, ge=1, le=60)
    depot_stale_days: int = Field(default=3, ge=1, le=60)
    #: 仓库识别下限护栏（M44）：一次识别的条目数低于它时**整轮不写**，避免半截识别把
    #: 整表冲掉（真机实测：只读到 26 件的那次把 77 行的表覆盖了）。``0`` ＝ 关闭。
    depot_min_items: int = Field(default=20, ge=0, le=2000)
    #: 仓库识别的**下降护栏**（M45 W4）：``depot_min_items`` 只防「这一轮绝对太少」，
    #: 防不住「上一轮 96 → 本轮 69」这种相对断崖（真机 14:17 那次内核失败后的自动重试
    #: 就是 69 条把 96 行的整表冲掉，69 > 20 所以绝对下限放行）。规则：上一轮条目数
    #: ``≥ depot_drop_min_prev`` 且本轮 ``< 上一轮 × depot_drop_ratio`` ⇒ 整轮不写，
    #: 表保持原样，并把原因写进识别记录的 ``detail.skipped``。
    #: ``depot_drop_min_prev=0`` 或 ``depot_drop_ratio=0`` 都等于关掉这条规则。
    #: 默认比例是 **0.75**（不是 0.7）：0.7 的阈值是 96 × 0.7 = 67.2，拦不住实测的
    #: 96 → 69；0.75 的阈值是 72，同时仍放行「自识别没跑、只剩内核那趟」的 96 → 77。
    depot_drop_min_prev: int = Field(default=20, ge=0, le=5000)
    depot_drop_ratio: float = Field(default=0.75, ge=0.0, le=1.0)
    #: 作业研究（M29，规格 §9.6/§9.7/§9.11）：``copilot_grant_minutes`` 是作业授权的
    #: 有效期（``grant_copilot_attempts`` 的 ``window_minutes`` 默认值）；
    #: ``copilot_attempt_timeout_minutes`` 是 ``run_copilot_attempt`` 阻塞等待的上限；
    #: ``copilot_retention_days`` 是 AI 草稿 / 作业站副本的保留天数，被
    #: ``retention_service.cleanup_copilots`` 现读。范围见规格附录 B（授权窗口按
    #: 实施裁决放宽到 1–1440）。
    copilot_grant_minutes: int = Field(default=60, ge=1, le=1440)
    copilot_attempt_timeout_minutes: int = Field(default=20, ge=1, le=60)
    copilot_retention_days: int = Field(default=7, ge=1, le=365)
    #: 只读侦察 subagent（M48 规格 §4.1、§4.4）的三个预算上限，每次委派现读，改完立即生效。
    #: - ``recon_max_steps``：子 agent 的步数上限，默认 8。一次「查清楚某件事」实测多在
    #:   10 步内，上限 20 是护栏：再往上不会更准，只会在同一个问题上打转。步数用尽照常
    #:   出报告并在 ``budget`` 里标出来，不报错给父 agent。
    #: - ``recon_output_chars``：单次工具输出的截断长度，默认 20_000 字符。仓库里的文件
    #:   普遍比它长，所以截断不是丢弃——返回 ``truncated.next_offset`` 让模型续读。
    #:   上限 200_000 的理由：再大就等于把整个文件一次性塞进子 agent 的上下文，
    #:   正是这个里程碑要治的病。
    #: - ``recon_max_chars``：一次侦察的累计字符预算，默认 200_000，超出立即收尾并置
    #:   ``budget.exhausted=true``。它兜住「每一步都不大、步数一多总量失控」的情形，
    #:   因此必须显著大于单次截断值；下限 10_000 保证至少能读完几个文件，上限
    #:   2_000_000 是硬天花板（约 1M token 量级），不允许配出更大的单次侦察。
    recon_max_steps: int = Field(default=8, ge=1, le=20)
    recon_output_chars: int = Field(default=20000, ge=1000, le=200000)
    recon_max_chars: int = Field(default=200000, ge=10000, le=2000000)


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


class SklandSettings(BaseModel):
    """森空岛账号数据（M42 规格 §3.2、§4.4、§4.5）。

    三项都**只从 ``config.yaml`` / 环境变量来**（登记在 :data:`SETTING_KEYS` 里，
    段才会被解析；同时列进 :data:`CONFIG_ONLY_SETTING_KEYS`，不进设置 API）：

    - ``credential_key`` 是凭据的 Fernet 密钥。生成方式见 docs/15 与
      ``scripts/generate_skland_key.py``；为空即「没配」，此时绑定端点回
      ``SKLAND_ENCRYPTION_KEY_INVALID``，与 ``llm.api_key`` 缺失时同款行为。
    - ``auto_sync_interval_minutes`` 是定时同步间隔，``0`` ＝ 关闭（默认）。非 0 必须
      ≥ 60：这是**非官方接口**，本里程碑的取舍是「手动为主、定时兜底」，不允许配出
      比每小时更密的轮询。
    - ``request_timeout_seconds`` 是单次出站请求的超时。
    """

    model_config = ConfigDict(extra="ignore")

    credential_key: str = ""
    auto_sync_interval_minutes: int = Field(default=0, ge=0, le=1440)
    request_timeout_seconds: float = Field(default=15.0, ge=1.0, le=60.0)

    @field_validator("auto_sync_interval_minutes")
    @classmethod
    def _reject_sub_hourly_interval(cls, value: int) -> int:
        """``0`` 是关闭；其余取值不许低于 60 分钟（规格 §4.5 的频率护栏）。"""
        if value != 0 and value < MIN_AUTO_SYNC_INTERVAL_MINUTES:
            raise ValueError(
                f"定时同步间隔要么是 0（关闭），要么不低于 {MIN_AUTO_SYNC_INTERVAL_MINUTES} 分钟"
            )
        return value


class WorkspaceSettings(BaseModel):
    """只读探索工具的工作区根目录（M48 规格 §4.1）。

    ``root`` 是 ``list_files`` / ``read_file`` / ``search_code`` / ``delegate_recon``
    四个工具唯一的取数边界：相对路径按它解析，``resolve()`` 之后必须仍在它里面
    （软链接按解析后的真实路径判定），任何越界都在读之前拦下。

    **默认空串 ＝ 关闭探索能力**：四个工具一律回 ``WORKSPACE_NOT_CONFIGURED``，
    而不是退化成「按进程 CWD 解析」。服务被 launchd / systemd 拉起时 CWD 往往是
    ``/``，静默退化等于把整个文件系统交给模型；要开启就显式写一个目录。
    """

    model_config = ConfigDict(extra="ignore")

    root: str = ""


class Settings(BaseModel):
    """全量运行配置（env > DB > YAML > code defaults）。

    ``access_token`` 为 ``""`` 即「未配置」，此时全部端点免鉴权（docs/05 §5.2）。
    """

    #: 同上：未识别的段/键忽略（``smtp`` 段在本卡不建模，通知配置另建模型）。
    model_config = ConfigDict(extra="ignore")

    access_token: str = ""
    maa_core_path: str = ""
    proxy: str = ""
    adb: AdbSettings = Field(default_factory=AdbSettings)
    core: CoreSettings = Field(default_factory=CoreSettings)
    log: LogSettings = Field(default_factory=LogSettings)
    updates: UpdateSettings = Field(default_factory=UpdateSettings)
    agent: AgentSettings = Field(default_factory=AgentSettings)
    sources: SourcesSettings = Field(default_factory=SourcesSettings)
    channel: ChannelSettings = Field(default_factory=ChannelSettings)
    llm: LLMSettings = Field(default_factory=LLMSettings)
    notify: NotifySettings = Field(default_factory=NotifySettings)
    deployment: DeploymentSettings = Field(default_factory=DeploymentSettings)
    skland: SklandSettings = Field(default_factory=SklandSettings)
    #: M48 只读探索工具的根目录；``root`` 为空串即关闭探索能力（见 WorkspaceSettings）。
    workspace: WorkspaceSettings = Field(default_factory=WorkspaceSettings)


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
    :param env: 环境变量映射；``None`` 表示不叠加环境变量层（便于测试与后续
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

    供测试注入替身与热更新使用（改完 DB 覆盖层后重新解析并换上）。
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
    """只保留已知点分 key（``setting`` 表的覆盖项形态，）。"""
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
