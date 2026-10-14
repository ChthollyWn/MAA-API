"""``maa_api/settings.py`` 契约（M3-03，docs/02 §6/§7、docs/04 §5.6、docs/05 §5.2）。

五组断言：

1. **读取**：临时 yaml 六个字段全读对；``str``/``Path``/``None`` 三种路径入参。
2. **默认值钉死**：缺文件、空文件、段为 null、字段为 null 都回落到
   ``config.template.yaml`` 那组默认值；``access_token`` 未配置就是空串
   （不是某个默认 token），读取方据此判免鉴权（docs/05 §5.2）。另有一条
   文档一致性门禁：``AdbSettings`` 的默认值必须与 ``config.template.yaml`` 一致。
3. **容错**：未识别的段/键忽略（后续里程碑加段时旧文件不报错）；yaml 解析失败
   抛带路径的 ``SettingsError``，不静默吞。
4. **分层与缓存**：``resolve_settings`` 的 yaml < DB < env 优先级（M6 的接入点）、
   环境变量命名映射、空串视为未设置、``set_settings`` 注入与清空。
5. **无 import 副作用**：子进程在临时 CWD 下 ``import maa_api.settings``，A) 不
   产生任何文件、B) 不 import ``maa_api.db`` 或 SQLAlchemy（settings 必须能在数据
   库可用之前独立加载，docs/02 §7）。子进程 CWD 是 ``tmp_path``，仓库目录不被
   污染；本文件从不读写仓库根的真实 ``config.yaml``（只读跟踪在库里的模板）。
"""

from __future__ import annotations

import os
import subprocess
import sys
from pathlib import Path

import pytest
from pydantic import ValidationError
from ruamel.yaml import YAML

from maa_api import settings as settings_module
from maa_api.settings import (
    DEFAULT_CONFIG_PATH,
    ENV_OVERRIDES,
    AdbSettings,
    CoreSettings,
    Settings,
    SettingsError,
    get_settings,
    load_settings,
    resolve_settings,
    set_settings,
)

#: 仓库根：tests/test_settings.py → parents[1]。不依赖 CWD。
REPO_ROOT_FROM_TEST = Path(__file__).resolve().parents[1]
TEMPLATE_PATH = REPO_ROOT_FROM_TEST / "config.template.yaml"

#: 多层 settings 解析用的临时 yaml。
FULL_YAML = """\
app:
  access_token: sekret
  maa_core_path: /tmp/maa
  proxy: http://127.0.0.1:7890
adb:
  path: /opt/homebrew/bin/adb
  address: 1.2.3.4:5555
  screenshot_quality: 30
  connection_config: General
  common_ports: [6000, 6001]
channel:
  client_type: Official
  server: JP
llm:
  base_url: https://example.test/v1
  api_key: secret-key
  model: example-model
"""


@pytest.fixture(autouse=True)
def _isolate(monkeypatch: pytest.MonkeyPatch) -> None:
    """清掉 ``MAA_*`` 变量与进程内缓存：用例互不污染，也不受真实 shell 环境影响。"""
    for name in ENV_OVERRIDES.values():
        monkeypatch.delenv(name, raising=False)
    set_settings(None)
    yield
    set_settings(None)


def _write(tmp_path: Path, text: str, name: str = "config.yaml") -> Path:
    path = tmp_path / name
    path.write_text(text, encoding="utf8")
    return path


# ----------------------------------------------------------------------
# 1. 读取
# ----------------------------------------------------------------------


def test_load_full_yaml(tmp_path: Path) -> None:
    path = _write(tmp_path, FULL_YAML)

    s = load_settings(path)

    assert isinstance(s, Settings)
    assert s.access_token == "sekret"
    assert s.maa_core_path == "/tmp/maa"
    assert s.proxy == "http://127.0.0.1:7890"
    assert isinstance(s.adb, AdbSettings)
    assert s.adb.path == "/opt/homebrew/bin/adb"
    assert s.adb.address == "1.2.3.4:5555"
    assert s.adb.screenshot_quality == 30
    assert s.adb.connection_config == "General"
    assert s.adb.common_ports == [6000, 6001]
    assert s.channel.client_type == "Official"
    assert s.channel.server == "JP"
    assert s.llm.api_key == "secret-key"
    assert s.llm.model == "example-model"


def test_legacy_mcp_settings_are_ignored(tmp_path: Path) -> None:
    path = _write(
        tmp_path,
        "mcp:\n  allowed_hosts: [192.168.1.10]\n",
    )

    settings = load_settings(path)

    assert not hasattr(settings, "mcp")


def test_load_accepts_str_path(tmp_path: Path) -> None:
    path = _write(tmp_path, FULL_YAML)

    assert load_settings(str(path)).access_token == "sekret"


def test_partial_yaml_keeps_other_defaults(tmp_path: Path) -> None:
    path = _write(tmp_path, "app:\n  access_token: only-token\n")

    s = load_settings(path)

    assert s.access_token == "only-token"
    assert s.maa_core_path == ""
    assert s.adb == AdbSettings()


# ----------------------------------------------------------------------
# 2. 默认值钉死
# ----------------------------------------------------------------------


def test_missing_file_returns_pinned_defaults(tmp_path: Path) -> None:
    missing = tmp_path / "missing.yaml"

    s = load_settings(missing)

    assert s.access_token == ""  # 未配置就是空串，不是默认 token
    assert s.maa_core_path == ""
    assert s.proxy == ""
    assert s.adb.path == "/opt/homebrew/bin/adb"
    assert s.adb.address == "127.0.0.1:5555"
    assert s.adb.screenshot_quality == 25
    assert s.adb.connection_config == "General"
    assert s.adb.common_ports == [5555, 5556, 7555, 16384, 21503, 62001]
    assert not missing.exists()  # 读一个不存在的路径不创建文件


def test_empty_file_returns_pinned_defaults(tmp_path: Path) -> None:
    path = _write(tmp_path, "")

    s = load_settings(path)

    assert s.access_token == ""
    assert s.adb.path == "/opt/homebrew/bin/adb"
    assert s.adb.address == "127.0.0.1:5555"
    assert s.adb.screenshot_quality == 25
    assert s.adb.connection_config == "General"
    assert s.adb.common_ports == [5555, 5556, 7555, 16384, 21503, 62001]


def test_null_sections_and_fields_fall_back(tmp_path: Path) -> None:
    """``access_token:``（null）、空段都是模板里的常见形态，一律按「未配置」处理。"""
    path = _write(
        tmp_path,
        "app:\n  access_token:\n  maa_core_path:\n  proxy:\nadb:\n",
    )

    s = load_settings(path)

    assert s.access_token == ""
    assert s.maa_core_path == ""
    assert s.proxy == ""
    assert s.adb.path == "/opt/homebrew/bin/adb"
    assert s.adb.address == "127.0.0.1:5555"
    assert s.adb.screenshot_quality == 25


def test_adb_defaults_match_config_template() -> None:
    """文档一致性门禁：默认值必须与 config.template.yaml 的 adb 段一致。"""
    template = YAML(typ="safe").load(TEMPLATE_PATH.read_text(encoding="utf8"))
    template_adb = template["adb"]

    defaults = AdbSettings()

    assert defaults.path == template_adb["path"]
    assert defaults.address == template_adb["address"]
    assert defaults.screenshot_quality == template_adb["screenshot_quality"]
    assert defaults.connection_config == template_adb["connection_config"]
    assert defaults.common_ports == template_adb["common_ports"]


def test_core_defaults_match_config_template() -> None:
    """文档一致性门禁：M38 的内核旋钮默认值必须与 config.template.yaml 的 core 段一致。"""
    template = YAML(typ="safe").load(TEMPLATE_PATH.read_text(encoding="utf8"))

    assert CoreSettings().task_delay == template["core"]["task_delay"]


# ----------------------------------------------------------------------
# 3. 容错
# ----------------------------------------------------------------------


def test_unknown_sections_and_keys_ignored(tmp_path: Path) -> None:
    """后续里程碑会往 yaml 加段（smtp / channel / llm…），旧解析器不能报错。"""
    path = _write(
        tmp_path,
        "app:\n"
        "  access_token: sekret\n"
        "  future_key: 1\n"
        "adb:\n"
        "  address: 1.2.3.4:5555\n"
        "  future_key: 2\n"
        "smtp:\n"
        "  server: smtp.qq.com\n"
        "  port: 587\n"
        "brand_new_section:\n"
        "  anything: true\n"
        "another_new_section: 25\n",
    )

    s = load_settings(path)

    assert s.access_token == "sekret"
    assert s.adb.address == "1.2.3.4:5555"
    assert not hasattr(s, "smtp")


def test_model_ignores_unknown_fields_directly() -> None:
    s = Settings.model_validate(
        {"access_token": "t", "nope": 1, "adb": {"path": "/x", "nope": 2}}
    )

    assert s.access_token == "t"
    assert s.adb.path == "/x"


def test_yaml_error_reports_file_path(tmp_path: Path) -> None:
    path = _write(tmp_path, "app: [unclosed\n")

    with pytest.raises(SettingsError) as excinfo:
        load_settings(path)

    assert str(path) in str(excinfo.value)


def test_non_mapping_top_level_reports_file_path(tmp_path: Path) -> None:
    path = _write(tmp_path, "- a\n- b\n")

    with pytest.raises(SettingsError) as excinfo:
        load_settings(path)

    assert str(path) in str(excinfo.value)
    assert "顶层" in str(excinfo.value)


def test_malformed_known_section_is_loud(tmp_path: Path) -> None:
    """``adb: 25`` 这类「段名对、形状错」不静默回落默认值。"""
    path = _write(tmp_path, "adb: 25\n")

    with pytest.raises(SettingsError) as excinfo:
        load_settings(path)

    assert str(path) in str(excinfo.value)
    assert "adb" in str(excinfo.value)


# ----------------------------------------------------------------------
# 4. 分层与缓存
# ----------------------------------------------------------------------


def test_invalid_field_value_is_loud(tmp_path: Path) -> None:
    """值类型错（如截图质量写成字符串）不静默回落，直接抛 pydantic 校验错。"""
    path = _write(tmp_path, "adb:\n  screenshot_quality: not-a-number\n")

    with pytest.raises(ValidationError):
        load_settings(path)


def test_env_vars_override_yaml(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    path = _write(tmp_path, FULL_YAML)
    monkeypatch.setenv("MAA_APP_ACCESS_TOKEN", "env-token")
    monkeypatch.setenv("MAA_ADB_PATH", "/env/adb")
    monkeypatch.setenv("MAA_ADB_SCREENSHOT_QUALITY", "42")
    monkeypatch.setenv("MAA_ADB_COMMON_PORTS", "7000, 7001,7555")
    monkeypatch.setenv("MAA_ADB_CONNECTION_CONFIG", "MuMuEmulator12")
    monkeypatch.setenv("MAA_CHANNEL_CLIENT_TYPE", "Bilibili")
    monkeypatch.setenv("MAA_LLM_API_KEY", "env-key")

    s = load_settings(path)

    assert s.access_token == "env-token"
    assert s.adb.path == "/env/adb"
    assert s.adb.screenshot_quality == 42
    assert s.adb.common_ports == [7000, 7001, 7555]
    assert s.adb.connection_config == "MuMuEmulator12"
    assert s.channel.client_type == "Bilibili"
    assert s.llm.api_key == "env-key"
    assert s.maa_core_path == "/tmp/maa"  # 没设环境变量的字段仍取 yaml
    assert s.adb.address == "1.2.3.4:5555"


def test_empty_env_var_means_unset(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    path = _write(tmp_path, FULL_YAML)
    monkeypatch.setenv("MAA_APP_PROXY", "")

    s = load_settings(path)

    assert s.proxy == "http://127.0.0.1:7890"
    assert s.adb.screenshot_quality == 30


def test_env_mapping_names_are_pinned() -> None:
    assert ENV_OVERRIDES == {
        "app.access_token": "MAA_APP_ACCESS_TOKEN",
        "app.maa_core_path": "MAA_APP_MAA_CORE_PATH",
        "app.proxy": "MAA_APP_PROXY",
        "adb.path": "MAA_ADB_PATH",
        "adb.address": "MAA_ADB_ADDRESS",
        "adb.screenshot_quality": "MAA_ADB_SCREENSHOT_QUALITY",
        "log.ring_size": "MAA_LOG_RING_SIZE",
        "log.batch_size": "MAA_LOG_BATCH_SIZE",
        "log.flush_interval": "MAA_LOG_FLUSH_INTERVAL",
        "log.core_min_level": "MAA_LOG_CORE_MIN_LEVEL",
        "log.persist_maacore_debug_level": "MAA_LOG_PERSIST_MAACORE_DEBUG_LEVEL",
        "adb.connection_config": "MAA_ADB_CONNECTION_CONFIG",
        "adb.common_ports": "MAA_ADB_COMMON_PORTS",
        # M38：内核识别间隔（开机写进资源层，重启生效）。
        "core.task_delay": "MAA_CORE_TASK_DELAY",
        # M25：外部数据源（全部 hot_action="none"，改完立即生效）。
        "sources.enabled": "MAA_SOURCES_ENABLED",
        "sources.use_proxy": "MAA_SOURCES_USE_PROXY",
        "sources.cache_live_seconds": "MAA_SOURCES_CACHE_LIVE_SECONDS",
        "sources.cache_static_seconds": "MAA_SOURCES_CACHE_STATIC_SECONDS",
        "sources.cache_max_mb": "MAA_SOURCES_CACHE_MAX_MB",
        "sources.prts_enabled": "MAA_SOURCES_PRTS_ENABLED",
        "sources.prts_rate_per_minute": "MAA_SOURCES_PRTS_RATE_PER_MINUTE",
        "sources.penguin_enabled": "MAA_SOURCES_PENGUIN_ENABLED",
        "sources.penguin_host": "MAA_SOURCES_PENGUIN_HOST",
        "sources.penguin_rate_per_minute": "MAA_SOURCES_PENGUIN_RATE_PER_MINUTE",
        # M29 作业站（附录 B）：独立开关与限流。
        "sources.copilot_site_enabled": "MAA_SOURCES_COPILOT_SITE_ENABLED",
        "sources.copilot_site_rate_per_minute": "MAA_SOURCES_COPILOT_SITE_RATE_PER_MINUTE",
        "channel.client_type": "MAA_CHANNEL_CLIENT_TYPE",
        "channel.server": "MAA_CHANNEL_SERVER",
        "llm.base_url": "MAA_LLM_BASE_URL",
        "llm.api_key": "MAA_LLM_API_KEY",
        "llm.model": "MAA_LLM_MODEL",
        "notify.vapid_private_key": "MAA_NOTIFY_VAPID_PRIVATE_KEY",
        "notify.vapid_subject": "MAA_NOTIFY_VAPID_SUBJECT",
        "deployment.forwarded_allow_ips": "MAA_DEPLOYMENT_FORWARDED_ALLOW_IPS",
        "deployment.cors_origins": "MAA_DEPLOYMENT_CORS_ORIGINS",
        "deployment.cors_origin_regex": "MAA_DEPLOYMENT_CORS_ORIGIN_REGEX",
        "updates.download_prefix": "MAA_UPDATES_DOWNLOAD_PREFIX",
        "updates.check_hour": "MAA_UPDATES_CHECK_HOUR",
        "agent.confirmation_timeout_seconds": "MAA_AGENT_CONFIRMATION_TIMEOUT_SECONDS",
        "agent.grant_confirmation_timeout_seconds": "MAA_AGENT_GRANT_CONFIRMATION_TIMEOUT_SECONDS",
        "agent.question_timeout_seconds": "MAA_AGENT_QUESTION_TIMEOUT_SECONDS",
        "agent.atomic_grant_minutes": "MAA_AGENT_ATOMIC_GRANT_MINUTES",
        "agent.auto_diagnose_failures": "MAA_AGENT_AUTO_DIAGNOSE_FAILURES",
        # M27：账号数据的过期阈值（附录 B）。
        "agent.box_stale_days": "MAA_AGENT_BOX_STALE_DAYS",
        "agent.depot_stale_days": "MAA_AGENT_DEPOT_STALE_DAYS",
        # M45 W4：仓库写库的两道闸——绝对下限（未登记进设置 API）与相对上一轮的下降护栏。
        "agent.depot_drop_min_prev": "MAA_AGENT_DEPOT_DROP_MIN_PREV",
        "agent.depot_drop_ratio": "MAA_AGENT_DEPOT_DROP_RATIO",
        # M29：作业研究的授权窗口、单次尝试超时与作业资源保留天数。
        "agent.copilot_grant_minutes": "MAA_AGENT_COPILOT_GRANT_MINUTES",
        "agent.copilot_attempt_timeout_minutes": "MAA_AGENT_COPILOT_ATTEMPT_TIMEOUT_MINUTES",
        "agent.copilot_retention_days": "MAA_AGENT_COPILOT_RETENTION_DAYS",
        "sources.cache_level_seconds": "MAA_SOURCES_CACHE_LEVEL_SECONDS",
        "sources.gamedata_enabled": "MAA_SOURCES_GAMEDATA_ENABLED",
        "sources.gamedata_rate_per_minute": "MAA_SOURCES_GAMEDATA_RATE_PER_MINUTE",
        "sources.gamedata_mirrors": "MAA_SOURCES_GAMEDATA_MIRRORS",
        "sources.gamedata_github_api_fallback": "MAA_SOURCES_GAMEDATA_GITHUB_API_FALLBACK",
        # M42：森空岛账号数据（凭据密钥、定时同步间隔、请求超时）。
        "skland.credential_key": "MAA_SKLAND_CREDENTIAL_KEY",
        "skland.auto_sync_interval_minutes": "MAA_SKLAND_AUTO_SYNC_INTERVAL_MINUTES",
        "skland.request_timeout_seconds": "MAA_SKLAND_REQUEST_TIMEOUT_SECONDS",
    }


def test_kernel_task_delay_has_a_documented_default_and_bounds():
    """M38 内核识别间隔：默认 100，取值 0–2000，超范围直接拒（docs/13 §8.53）。"""
    from pydantic import ValidationError

    from maa_api.services.settings_schema import schema_for

    assert resolve_settings(env={}).core.task_delay == 100
    assert resolve_settings({"core": {"task_delay": 0}}, env={}).core.task_delay == 0
    assert resolve_settings({"core": {"task_delay": 2000}}, env={}).core.task_delay == 2000

    for bad in (-1, 2001):
        with pytest.raises(ValidationError):
            resolve_settings({"core": {"task_delay": bad}}, env={})

    row = schema_for("core.task_delay")
    assert (row["type"], row["minimum"], row["maximum"]) == ("integer", 0, 2000)
    # 开机时才把值写进资源层，所以要重启服务才会生效。
    assert row["hot_action"] == "restart_required"


def test_agent_confirmation_and_atomic_grant_settings_have_documented_defaults_and_bounds():
    from maa_api.services.settings_schema import schema_for

    default = resolve_settings(env={})
    assert default.agent.confirmation_timeout_seconds == 600
    assert default.agent.grant_confirmation_timeout_seconds == 120
    assert default.agent.question_timeout_seconds == 600
    assert default.agent.atomic_grant_minutes == 15
    assert default.agent.auto_diagnose_failures is True

    configured = resolve_settings(
        {"agent": {
            "confirmation_timeout_seconds": 900,
            "grant_confirmation_timeout_seconds": 45,
            "question_timeout_seconds": 300,
            "atomic_grant_minutes": 60,
            "auto_diagnose_failures": False,
        }},
        env={},
    )
    assert configured.agent.confirmation_timeout_seconds == 900
    assert configured.agent.grant_confirmation_timeout_seconds == 45
    assert configured.agent.question_timeout_seconds == 300
    assert configured.agent.atomic_grant_minutes == 60
    assert configured.agent.auto_diagnose_failures is False

    with pytest.raises(ValidationError):
        resolve_settings({"agent": {"atomic_grant_minutes": 61}}, env={})
    with pytest.raises(ValidationError):
        resolve_settings({"agent": {"confirmation_timeout_seconds": 0}}, env={})
    with pytest.raises(ValidationError):
        resolve_settings({"agent": {"question_timeout_seconds": 0}}, env={})

    assert schema_for("agent.confirmation_timeout_seconds")["minimum"] == 1
    assert schema_for("agent.grant_confirmation_timeout_seconds")["minimum"] == 1
    assert schema_for("agent.question_timeout_seconds")["minimum"] == 1
    assert schema_for("agent.atomic_grant_minutes")["maximum"] == 60


def test_account_stale_days_have_documented_defaults_and_bounds():
    """M27 账号数据过期阈值：默认值与取值范围照附录 B。"""
    from maa_api.services.settings_schema import schema_for

    default = resolve_settings(env={})
    assert default.agent.box_stale_days == 14
    assert default.agent.depot_stale_days == 3

    configured = resolve_settings(
        {"agent": {"box_stale_days": 30, "depot_stale_days": 7}}, env={}
    )
    assert configured.agent.box_stale_days == 30
    assert configured.agent.depot_stale_days == 7

    env_layer = resolve_settings(env={"MAA_AGENT_BOX_STALE_DAYS": "21"})
    assert env_layer.agent.box_stale_days == 21

    with pytest.raises(ValidationError):
        resolve_settings({"agent": {"box_stale_days": 0}}, env={})
    with pytest.raises(ValidationError):
        resolve_settings({"agent": {"box_stale_days": 366}}, env={})
    with pytest.raises(ValidationError):
        resolve_settings({"agent": {"depot_stale_days": 0}}, env={})
    with pytest.raises(ValidationError):
        resolve_settings({"agent": {"depot_stale_days": 61}}, env={})

    assert schema_for("agent.box_stale_days")["minimum"] == 1
    assert schema_for("agent.box_stale_days")["maximum"] == 365
    assert schema_for("agent.box_stale_days")["group"] == "Agent"
    assert schema_for("agent.depot_stale_days")["minimum"] == 1
    assert schema_for("agent.depot_stale_days")["maximum"] == 60
    assert schema_for("agent.depot_stale_days")["group"] == "Agent"


def test_depot_drop_guard_has_documented_defaults_and_bounds():
    """M45 W4 的下降护栏：默认 ``20 / 0.75``，两项都为 0 时等于关掉。

    0.75 这个默认值有实测依据：真机那次断崖是 96 → 69，而 96 × 0.7 = 67.2 < 69，
    0.7 拦不住；0.75 的阈值是 72，同时仍放行 96 → 77 这种正常回退（当时那一轮的 77
    是「只剩内核那趟」的结果，M45 自识别删除后所有轮次都是这一路）。
    """
    from maa_api.services.settings_schema import schema_for

    default = resolve_settings(env={})
    assert default.agent.depot_drop_min_prev == 20
    assert default.agent.depot_drop_ratio == 0.75

    configured = resolve_settings(
        {"agent": {"depot_drop_min_prev": 0, "depot_drop_ratio": 0.0}}, env={}
    )
    assert configured.agent.depot_drop_min_prev == 0
    assert configured.agent.depot_drop_ratio == 0.0

    env_layer = resolve_settings(env={"MAA_AGENT_DEPOT_DROP_RATIO": "0.9"})
    assert env_layer.agent.depot_drop_ratio == 0.9

    with pytest.raises(ValidationError):
        resolve_settings({"agent": {"depot_drop_ratio": 1.1}}, env={})
    with pytest.raises(ValidationError):
        resolve_settings({"agent": {"depot_drop_min_prev": -1}}, env={})

    assert schema_for("agent.depot_drop_min_prev")["minimum"] == 0
    assert schema_for("agent.depot_drop_min_prev")["maximum"] == 5000
    assert schema_for("agent.depot_drop_min_prev")["group"] == "Agent"
    assert schema_for("agent.depot_drop_ratio")["minimum"] == 0
    assert schema_for("agent.depot_drop_ratio")["maximum"] == 1
    assert schema_for("agent.depot_drop_ratio")["type"] == "number"


def test_copilot_research_settings_have_documented_defaults_and_bounds() -> None:
    """M29 作业研究：授权窗口、单次尝试超时与作业资源保留天数（规格 §9.6/§9.7/§9.11）。"""
    from maa_api.services.settings_schema import schema_for

    default = resolve_settings(env={})
    assert default.agent.copilot_grant_minutes == 60
    assert default.agent.copilot_attempt_timeout_minutes == 20
    assert default.agent.copilot_retention_days == 7

    configured = resolve_settings(
        {
            "agent": {
                "copilot_grant_minutes": 120,
                "copilot_attempt_timeout_minutes": 45,
                "copilot_retention_days": 30,
            }
        },
        env={},
    )
    assert configured.agent.copilot_grant_minutes == 120
    assert configured.agent.copilot_attempt_timeout_minutes == 45
    assert configured.agent.copilot_retention_days == 30

    env_layer = resolve_settings(
        env={
            "MAA_AGENT_COPILOT_GRANT_MINUTES": "90",
            "MAA_AGENT_COPILOT_ATTEMPT_TIMEOUT_MINUTES": "30",
            "MAA_AGENT_COPILOT_RETENTION_DAYS": "21",
        }
    )
    assert env_layer.agent.copilot_grant_minutes == 90
    assert env_layer.agent.copilot_attempt_timeout_minutes == 30
    assert env_layer.agent.copilot_retention_days == 21

    with pytest.raises(ValidationError):
        resolve_settings({"agent": {"copilot_grant_minutes": 0}}, env={})
    with pytest.raises(ValidationError):
        resolve_settings({"agent": {"copilot_grant_minutes": 1441}}, env={})
    with pytest.raises(ValidationError):
        resolve_settings({"agent": {"copilot_attempt_timeout_minutes": 0}}, env={})
    with pytest.raises(ValidationError):
        resolve_settings({"agent": {"copilot_attempt_timeout_minutes": 61}}, env={})
    with pytest.raises(ValidationError):
        resolve_settings({"agent": {"copilot_retention_days": 0}}, env={})
    with pytest.raises(ValidationError):
        resolve_settings({"agent": {"copilot_retention_days": 366}}, env={})

    assert schema_for("agent.copilot_grant_minutes")["minimum"] == 1
    assert schema_for("agent.copilot_grant_minutes")["maximum"] == 1440
    assert schema_for("agent.copilot_attempt_timeout_minutes")["minimum"] == 1
    assert schema_for("agent.copilot_attempt_timeout_minutes")["maximum"] == 60
    assert schema_for("agent.copilot_retention_days")["minimum"] == 1
    assert schema_for("agent.copilot_retention_days")["maximum"] == 365
    assert schema_for("agent.copilot_retention_days")["group"] == "Agent"

    # 文档一致性：模板里的三个值就是代码默认值。
    template = YAML(typ="safe").load(TEMPLATE_PATH.read_text(encoding="utf8"))
    template_agent = template["agent"]
    assert template_agent["copilot_grant_minutes"] == 60
    assert template_agent["copilot_attempt_timeout_minutes"] == 20
    assert template_agent["copilot_retention_days"] == 7


def test_gamedata_settings_have_documented_defaults_and_bounds() -> None:
    """M29 拆包数据：镜像列表、GitHub 兜底、开关、限流与 level 档缓存（规格 §9.3、附录 B）。"""
    from maa_api.services.settings_schema import schema_for

    default = resolve_settings(env={})
    assert default.sources.cache_level_seconds == 86400
    assert default.sources.gamedata_enabled is True
    assert default.sources.gamedata_rate_per_minute == 10
    assert default.sources.gamedata_github_api_fallback is True
    assert default.sources.gamedata_mirrors == [
        "https://fastly.jsdelivr.net/gh/Kengxxiao/ArknightsGameData@master",
        "https://cdn.jsdelivr.net/gh/Kengxxiao/ArknightsGameData@master",
    ]

    configured = resolve_settings(
        {
            "sources": {
                "cache_level_seconds": 3600,
                "gamedata_enabled": False,
                "gamedata_rate_per_minute": 30,
                "gamedata_github_api_fallback": False,
                "gamedata_mirrors": ["https://cdn.jsdelivr.net/gh/Other/Repo@main"],
            }
        },
        env={},
    )
    assert configured.sources.cache_level_seconds == 3600
    assert configured.sources.gamedata_enabled is False
    assert configured.sources.gamedata_rate_per_minute == 30
    assert configured.sources.gamedata_github_api_fallback is False
    assert configured.sources.gamedata_mirrors == [
        "https://cdn.jsdelivr.net/gh/Other/Repo@main"
    ]

    env_layer = resolve_settings(
        env={
            "MAA_SOURCES_CACHE_LEVEL_SECONDS": "7200",
            "MAA_SOURCES_GAMEDATA_ENABLED": "false",
            "MAA_SOURCES_GAMEDATA_RATE_PER_MINUTE": "5",
            "MAA_SOURCES_GAMEDATA_GITHUB_API_FALLBACK": "false",
        }
    )
    assert env_layer.sources.cache_level_seconds == 7200
    assert env_layer.sources.gamedata_enabled is False
    assert env_layer.sources.gamedata_rate_per_minute == 5
    assert env_layer.sources.gamedata_github_api_fallback is False

    # 越界与设置页元数据。
    with pytest.raises(ValidationError):
        resolve_settings({"sources": {"cache_level_seconds": 60}}, env={})
    with pytest.raises(ValidationError):
        resolve_settings({"sources": {"gamedata_rate_per_minute": 0}}, env={})
    assert schema_for("sources.gamedata_mirrors")["group"] == "外部数据源"
    assert schema_for("sources.gamedata_mirrors")["type"] == "string_list"
    assert schema_for("sources.cache_level_seconds")["minimum"] == 3600

    # 文档一致性：模板里的值就是代码默认值。
    template = YAML(typ="safe").load(TEMPLATE_PATH.read_text(encoding="utf8"))
    template_sources = template["sources"]
    assert template_sources["cache_level_seconds"] == 86400
    assert template_sources["gamedata_enabled"] is True
    assert template_sources["gamedata_rate_per_minute"] == 10
    assert template_sources["gamedata_github_api_fallback"] is True
    assert template_sources["gamedata_mirrors"] == default.sources.gamedata_mirrors


def test_copilot_site_settings_have_documented_defaults_and_bounds() -> None:
    """M29 作业站：独立开关与限流（规格附录 B）。"""
    from maa_api.services.settings_schema import schema_for

    default = resolve_settings(env={})
    assert default.sources.copilot_site_enabled is True
    assert default.sources.copilot_site_rate_per_minute == 20

    configured = resolve_settings(
        {
            "sources": {
                "copilot_site_enabled": False,
                "copilot_site_rate_per_minute": 5,
            }
        },
        env={},
    )
    assert configured.sources.copilot_site_enabled is False
    assert configured.sources.copilot_site_rate_per_minute == 5

    env_layer = resolve_settings(
        env={
            "MAA_SOURCES_COPILOT_SITE_ENABLED": "false",
            "MAA_SOURCES_COPILOT_SITE_RATE_PER_MINUTE": "42",
        }
    )
    assert env_layer.sources.copilot_site_enabled is False
    assert env_layer.sources.copilot_site_rate_per_minute == 42

    # 越界：范围 1–60（附录 B）。
    with pytest.raises(ValidationError):
        resolve_settings({"sources": {"copilot_site_rate_per_minute": 0}}, env={})
    with pytest.raises(ValidationError):
        resolve_settings({"sources": {"copilot_site_rate_per_minute": 61}}, env={})

    # 设置页 schema：两个键都在「外部数据源」分组，类型、范围与生效方式正确。
    enabled_field = schema_for("sources.copilot_site_enabled")
    assert enabled_field["group"] == "外部数据源"
    assert enabled_field["label"] == "启用作业站"
    assert enabled_field["type"] == "boolean"
    assert enabled_field["hot_action"] == "none"

    rate_field = schema_for("sources.copilot_site_rate_per_minute")
    assert rate_field["group"] == "外部数据源"
    assert rate_field["label"] == "作业站每分钟请求上限"
    assert rate_field["type"] == "integer"
    assert rate_field["minimum"] == 1
    assert rate_field["maximum"] == 60
    assert rate_field["hot_action"] == "none"

    # 文档一致性：模板里的值就是代码默认值，且模板带中文注释。
    template_text = TEMPLATE_PATH.read_text(encoding="utf8")
    template_sources = YAML(typ="safe").load(template_text)["sources"]
    assert template_sources["copilot_site_enabled"] is True
    assert template_sources["copilot_site_rate_per_minute"] == 20
    assert "# 作业站（prts.maa.plus，M29）" in template_text


def test_log_settings_read_yaml_and_environment(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    path = _write(
        tmp_path,
        "log:\n"
        "  ring_size: 64\n"
        "  batch_size: 12\n"
        "  flush_interval: 0.25\n"
        "  core_min_level: TRC\n"
        "  persist_maacore_debug_level: INFO\n",
    )
    monkeypatch.setenv("MAA_LOG_RING_SIZE", "128")

    settings = load_settings(path)

    assert settings.log.ring_size == 128
    assert settings.log.batch_size == 12
    assert settings.log.flush_interval == 0.25
    assert settings.log.core_min_level == "TRC"
    assert settings.log.persist_maacore_debug_level == "INFO"


def test_log_settings_defaults_are_pinned() -> None:
    settings = Settings().log

    assert settings.ring_size == 2000
    assert settings.batch_size == 200
    assert settings.flush_interval == 1.0
    assert settings.core_min_level == "INF"
    assert settings.persist_maacore_debug_level == "WARNING"


def test_resolve_settings_layer_priority() -> None:
    """M6 接入点：yaml < setting 表 < 环境变量；未识别的 DB key 忽略。"""
    s = resolve_settings(
        {"app": {"proxy": "http://yaml"}, "adb": {"address": "yaml:5555"}},
        db_overrides={
            "app.proxy": "http://db",
            "adb.address": "db:5555",
            "adb.common_ports": [6000],
            "nope.key": "ignored",
        },
        env={"MAA_APP_PROXY": "http://env"},
    )

    assert s.proxy == "http://env"  # 环境变量 > DB
    assert s.adb.address == "db:5555"  # DB > yaml
    assert s.adb.common_ports == [6000]
    assert s.adb.path == "/opt/homebrew/bin/adb"  # 四层都没有 -> 模型默认值


def test_common_ports_empty_env_keeps_lower_layer(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    path = _write(tmp_path, "adb:\n  common_ports: [6000, 6001]\n")
    monkeypatch.setenv("MAA_ADB_COMMON_PORTS", ", ,")

    settings = load_settings(path)

    assert settings.adb.common_ports == [6000, 6001]


def test_resolve_settings_without_env_layer_is_pure() -> None:
    """``env=None`` 表示不叠加环境变量层，不会去读 ``os.environ``。"""
    s = resolve_settings({"app": {"access_token": "from-yaml"}})

    assert s.access_token == "from-yaml"


def test_get_settings_caches_and_set_settings_replaces(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    injected = Settings(access_token="t")
    set_settings(injected)

    assert get_settings() is injected  # 注入后不读文件

    config = _write(tmp_path, "app:\n  access_token: from-file\n")
    monkeypatch.setattr(settings_module, "DEFAULT_CONFIG_PATH", config)

    set_settings(None)
    reloaded = get_settings()

    assert reloaded is not injected
    assert reloaded.access_token == "from-file"
    assert get_settings() is reloaded  # 第二次调用命中缓存


def test_get_settings_uses_default_path_not_cwd(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """旧实现的致命伤是 CWD 相对路径；默认路径锚在仓库根，CWD 里的同名文件不参与。"""
    cwd = tmp_path / "elsewhere"
    cwd.mkdir()
    (cwd / "config.yaml").write_text("app:\n  access_token: cwd-token\n", encoding="utf8")
    config = tmp_path / "config.yaml"
    config.write_text("app:\n  access_token: default-path-token\n", encoding="utf8")
    monkeypatch.setattr(settings_module, "DEFAULT_CONFIG_PATH", config)
    monkeypatch.chdir(cwd)

    set_settings(None)
    s = get_settings()

    assert s.access_token == "default-path-token"
    assert DEFAULT_CONFIG_PATH == REPO_ROOT_FROM_TEST / "config.yaml"


# ----------------------------------------------------------------------
# 5. 无 import 副作用
# ----------------------------------------------------------------------


def test_import_has_no_side_effects(tmp_path: Path) -> None:
    """临时 CWD 下 import：不落任何文件、不 import 数据层。

    用子进程而不是 ``importlib.reload``：只有全新解释器才能证明「首次 import
    本身」没有副作用；子进程 CWD 是 ``tmp_path``，仓库目录不被本用例写脏
    （``PYTHONDONTWRITEBYTECODE`` 连 ``__pycache__`` 也不落）。
    """
    env = dict(os.environ)
    pythonpath = [str(REPO_ROOT_FROM_TEST)]
    if env.get("PYTHONPATH"):
        pythonpath.append(env["PYTHONPATH"])
    env["PYTHONPATH"] = os.pathsep.join(pythonpath)
    env["PYTHONDONTWRITEBYTECODE"] = "1"

    code = (
        "import sys, maa_api.settings;"
        "forbidden=[m for m in sys.modules "
        "if m.startswith('maa_api.db') or m.split('.')[0] in {'sqlalchemy', 'sqlmodel'}];"
        "assert not forbidden, forbidden"
    )
    proc = subprocess.run(
        [sys.executable, "-c", code],
        cwd=tmp_path,
        env=env,
        capture_output=True,
        text=True,
    )

    assert proc.returncode == 0, proc.stderr
    assert os.listdir(tmp_path) == []
