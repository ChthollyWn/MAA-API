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
        # M67：九个角色各指向的模型档案，以及成本预算与上下文窗口兜底值。
        "llm.role_orchestrator": "MAA_LLM_ROLE_ORCHESTRATOR",
        "llm.role_worker": "MAA_LLM_ROLE_WORKER",
        "llm.role_subagent": "MAA_LLM_ROLE_SUBAGENT",
        "llm.role_summarizer": "MAA_LLM_ROLE_SUMMARIZER",
        "llm.role_title": "MAA_LLM_ROLE_TITLE",
        "llm.role_trigger_eval": "MAA_LLM_ROLE_TRIGGER_EVAL",
        "llm.role_memory": "MAA_LLM_ROLE_MEMORY",
        "llm.role_vision": "MAA_LLM_ROLE_VISION",
        "llm.role_embedding": "MAA_LLM_ROLE_EMBEDDING",
        "budget.daily_cost_limit": "MAA_BUDGET_DAILY_COST_LIMIT",
        "budget.per_run_cost_limit": "MAA_BUDGET_PER_RUN_COST_LIMIT",
        "budget.warn_ratio": "MAA_BUDGET_WARN_RATIO",
        "context.fallback_context_tokens": "MAA_CONTEXT_FALLBACK_CONTEXT_TOKENS",
        # M68：运行追踪（导出两项是 config-only，但环境变量映射仍需登记）。
        "trace.enabled": "MAA_TRACE_ENABLED",
        "trace.retention_days": "MAA_TRACE_RETENTION_DAYS",
        "trace.otlp_endpoint": "MAA_TRACE_OTLP_ENDPOINT",
        "trace.otlp_headers": "MAA_TRACE_OTLP_HEADERS",
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
        # M48：只读探索与侦察（工作区根目录、步数上限与两层字符预算）。
        "workspace.root": "MAA_WORKSPACE_ROOT",
        "agent.recon_max_steps": "MAA_AGENT_RECON_MAX_STEPS",
        "agent.recon_output_chars": "MAA_AGENT_RECON_OUTPUT_CHARS",
        "agent.recon_max_chars": "MAA_AGENT_RECON_MAX_CHARS",
        # M66：运行心跳与失联判定（config-only 的运维阈值）。
        "agent.run_heartbeat_seconds": "MAA_AGENT_RUN_HEARTBEAT_SECONDS",
        "agent.run_stale_seconds": "MAA_AGENT_RUN_STALE_SECONDS",
        # M70：总控合并窗口、Worker 并发闸与并行委派上限（都不是 config-only）。
        "orchestrator.batch_window_ms": "MAA_ORCHESTRATOR_BATCH_WINDOW_MS",
        "worker.max_concurrent": "MAA_WORKER_MAX_CONCURRENT",
        "delegation.max_parallel": "MAA_DELEGATION_MAX_PARALLEL",
        # M71：设备租约的有效期与排队等待时长（都不是 config-only）。
        "lease.ttl_seconds": "MAA_LEASE_TTL_SECONDS",
        "lease.wait_seconds": "MAA_LEASE_WAIT_SECONDS",
        # M74：压缩阈值、保留的最近轮次与工具结果清理边界（都不是 config-only）。
        "context.compaction_ratio": "MAA_CONTEXT_COMPACTION_RATIO",
        "context.keep_recent_turns": "MAA_CONTEXT_KEEP_RECENT_TURNS",
        "context.clear_tool_results_after": "MAA_CONTEXT_CLEAR_TOOL_RESULTS_AFTER",
        # M75：外置阈值与计划批准策略（都不是 config-only）。
        "context.spill_threshold_chars": "MAA_CONTEXT_SPILL_THRESHOLD_CHARS",
        "plan.approval_mode": "MAA_PLAN_APPROVAL_MODE",
        "plan.complex_min_steps": "MAA_PLAN_COMPLEX_MIN_STEPS",
        # M76：检索层开关、向量服务与分块参数（都不是 config-only）。
        "rag.enabled": "MAA_RAG_ENABLED",
        "rag.embedding_provider": "MAA_RAG_EMBEDDING_PROVIDER",
        "rag.top_k": "MAA_RAG_TOP_K",
        "rag.chunk_chars": "MAA_RAG_CHUNK_CHARS",
        "rag.chunk_overlap": "MAA_RAG_CHUNK_OVERLAP",
        # M77：外部语料四个开关与刷新时间（都不是 config-only）。
        "rag.maa_docs_enabled": "MAA_RAG_MAA_DOCS_ENABLED",
        "rag.prts_enabled": "MAA_RAG_PRTS_ENABLED",
        "rag.gamedata_enabled": "MAA_RAG_GAMEDATA_ENABLED",
        "rag.copilot_site_enabled": "MAA_RAG_COPILOT_SITE_ENABLED",
        "rag.refresh_cron": "MAA_RAG_REFRESH_CRON",
        # M78：技能总开关（不是 config-only）。
        "skills.enabled": "MAA_SKILLS_ENABLED",
        # M79：长期记忆四项（都不是 config-only）。
        "memory.enabled": "MAA_MEMORY_ENABLED",
        "memory.auto_extract": "MAA_MEMORY_AUTO_EXTRACT",
        "memory.require_review": "MAA_MEMORY_REQUIRE_REVIEW",
        "memory.max_injection_tokens": "MAA_MEMORY_MAX_INJECTION_TOKENS",
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


def test_orchestrator_worker_delegation_settings_have_documented_defaults_and_bounds() -> None:
    """M70 总控与 Worker：合并窗口、并发闸与并行委派上限（规格 §4.4）。"""
    from maa_api.services.settings_schema import SETTINGS_SCHEMA, schema_for

    default = resolve_settings(env={})
    assert default.orchestrator.batch_window_ms == 800
    assert default.worker.max_concurrent == 4
    assert default.delegation.max_parallel == 3

    configured = resolve_settings(
        {
            "orchestrator": {"batch_window_ms": 1500},
            "worker": {"max_concurrent": 16},
            "delegation": {"max_parallel": 8},
        },
        env={},
    )
    assert configured.orchestrator.batch_window_ms == 1500
    assert configured.worker.max_concurrent == 16
    assert configured.delegation.max_parallel == 8

    env_layer = resolve_settings(
        env={
            "MAA_ORCHESTRATOR_BATCH_WINDOW_MS": "0",
            "MAA_WORKER_MAX_CONCURRENT": "6",
            "MAA_DELEGATION_MAX_PARALLEL": "2",
        }
    )
    assert env_layer.orchestrator.batch_window_ms == 0
    assert env_layer.worker.max_concurrent == 6
    assert env_layer.delegation.max_parallel == 2

    # 越界拒绝：每项各试下界 -1 与上界 +1（orchestrator 的下界是 0，所以用 -1）。
    for section, field, value in (
        ("orchestrator", "batch_window_ms", -1),
        ("orchestrator", "batch_window_ms", 5001),
        ("worker", "max_concurrent", 0),
        ("worker", "max_concurrent", 17),
        ("delegation", "max_parallel", 0),
        ("delegation", "max_parallel", 9),
    ):
        with pytest.raises(ValidationError):
            resolve_settings({section: {field: value}}, env={})

    # 设置 API：三项都登记、分组 Agent、无热更新动作，并带上界面上的上下限与说明。
    keys = {row["key"] for row in SETTINGS_SCHEMA}
    assert {
        "orchestrator.batch_window_ms",
        "worker.max_concurrent",
        "delegation.max_parallel",
    } <= keys
    orchestrator_row = schema_for("orchestrator.batch_window_ms")
    assert (
        orchestrator_row["type"],
        orchestrator_row["minimum"],
        orchestrator_row["maximum"],
    ) == ("integer", 0, 5000)
    assert orchestrator_row["label"] == "总控合并窗口（毫秒）"
    assert orchestrator_row["description"] == (
        "新消息到达后等这么久再处理，窗口内的消息合并成一轮"
    )
    worker_row = schema_for("worker.max_concurrent")
    assert (
        worker_row["type"],
        worker_row["minimum"],
        worker_row["maximum"],
    ) == ("integer", 1, 16)
    delegation_row = schema_for("delegation.max_parallel")
    assert (
        delegation_row["type"],
        delegation_row["minimum"],
        delegation_row["maximum"],
    ) == ("integer", 1, 8)
    assert delegation_row["description"] == "一批调用里同时执行的只读子代理个数"
    for key in (
        "orchestrator.batch_window_ms",
        "worker.max_concurrent",
        "delegation.max_parallel",
    ):
        assert schema_for(key)["group"] == "Agent"
        assert schema_for(key)["hot_action"] == "none"

    # 文档一致性：模板里的三个值就是代码默认值。
    template = YAML(typ="safe").load(TEMPLATE_PATH.read_text(encoding="utf8"))
    assert template["orchestrator"]["batch_window_ms"] == 800
    assert template["worker"]["max_concurrent"] == 4
    assert template["delegation"]["max_parallel"] == 3


def test_lease_settings_have_documented_defaults_and_bounds() -> None:
    """M71 设备租约：默认 600/30 秒，范围 60–3600 与 0–600（spec §4.4）。"""
    from maa_api.services.settings_schema import SETTINGS_SCHEMA, schema_for
    from maa_api.settings import CONFIG_ONLY_SETTING_KEYS, LeaseSettings

    default = resolve_settings(env={})
    assert default.lease == LeaseSettings()
    assert default.lease.ttl_seconds == 600
    assert default.lease.wait_seconds == 30

    configured = resolve_settings(
        {"lease": {"ttl_seconds": 1800, "wait_seconds": 120}}, env={}
    )
    assert configured.lease.ttl_seconds == 1800
    assert configured.lease.wait_seconds == 120

    env_layer = resolve_settings(
        env={"MAA_LEASE_TTL_SECONDS": "60", "MAA_LEASE_WAIT_SECONDS": "0"}
    )
    assert env_layer.lease.ttl_seconds == 60
    assert env_layer.lease.wait_seconds == 0

    for section, field, value in (
        ("lease", "ttl_seconds", 59),
        ("lease", "ttl_seconds", 3601),
        ("lease", "wait_seconds", -1),
        ("lease", "wait_seconds", 601),
    ):
        with pytest.raises(ValidationError):
            resolve_settings({section: {field: value}}, env={})

    # 设置 API：两项都登记、分组 Agent、无热更新动作，界面上下限与代码范围一致。
    keys = {row["key"] for row in SETTINGS_SCHEMA}
    assert {"lease.ttl_seconds", "lease.wait_seconds"} <= keys
    assert not ({"lease.ttl_seconds", "lease.wait_seconds"} & CONFIG_ONLY_SETTING_KEYS)
    ttl_row = schema_for("lease.ttl_seconds")
    assert (
        ttl_row["label"], ttl_row["group"], ttl_row["type"],
        ttl_row["minimum"], ttl_row["maximum"], ttl_row["hot_action"],
    ) == ("设备租约有效期（秒）", "Agent", "integer", 60, 3600, "none")
    wait_row = schema_for("lease.wait_seconds")
    assert (
        wait_row["label"], wait_row["group"], wait_row["type"],
        wait_row["minimum"], wait_row["maximum"], wait_row["hot_action"],
    ) == ("设备租约排队等待（秒）", "Agent", "integer", 0, 600, "none")

    # 文档一致性：模板里的两个值就是代码默认值。
    template = YAML(typ="safe").load(TEMPLATE_PATH.read_text(encoding="utf8"))
    assert template["lease"] == {"ttl_seconds": 600, "wait_seconds": 30}


def test_compaction_settings_have_documented_defaults_and_bounds() -> None:
    """M74 压缩：0.75 / 2 / 3，范围 0.3–0.95、1–10、1–20（spec §4.4）。"""
    from maa_api.services.settings_schema import SETTINGS_SCHEMA, schema_for
    from maa_api.settings import CONFIG_ONLY_SETTING_KEYS, ContextSettings

    default = resolve_settings(env={})
    assert default.context == ContextSettings()
    assert default.context.fallback_context_tokens == 128000
    assert default.context.compaction_ratio == 0.75
    assert default.context.keep_recent_turns == 2
    assert default.context.clear_tool_results_after == 3

    configured = resolve_settings(
        {
            "context": {
                "compaction_ratio": 0.5,
                "keep_recent_turns": 6,
                "clear_tool_results_after": 12,
            }
        },
        env={},
    )
    assert configured.context.compaction_ratio == 0.5
    assert configured.context.keep_recent_turns == 6
    assert configured.context.clear_tool_results_after == 12

    env_layer = resolve_settings(
        env={
            "MAA_CONTEXT_COMPACTION_RATIO": "0.9",
            "MAA_CONTEXT_KEEP_RECENT_TURNS": "1",
            "MAA_CONTEXT_CLEAR_TOOL_RESULTS_AFTER": "20",
        }
    )
    assert env_layer.context.compaction_ratio == 0.9
    assert env_layer.context.keep_recent_turns == 1
    assert env_layer.context.clear_tool_results_after == 20

    # 越界拒绝：三项各试上下界外一格，并钉住边界值本身合法。
    for section, field, value in (
        ("context", "compaction_ratio", 0.29),
        ("context", "compaction_ratio", 0.96),
        ("context", "keep_recent_turns", 0),
        ("context", "keep_recent_turns", 11),
        ("context", "clear_tool_results_after", 0),
        ("context", "clear_tool_results_after", 21),
    ):
        with pytest.raises(ValidationError):
            resolve_settings({section: {field: value}}, env={})
    for value in (0.3, 0.95):
        assert (
            resolve_settings({"context": {"compaction_ratio": value}}, env={})
            .context.compaction_ratio
            == value
        )
    assert resolve_settings(
        {"context": {"keep_recent_turns": 10, "clear_tool_results_after": 1}}, env={}
    ).context.keep_recent_turns == 10

    # 设置 API：三项都登记、分组 Agent、无热更新动作，类型/范围与代码一致。
    keys = {
        "context.compaction_ratio",
        "context.keep_recent_turns",
        "context.clear_tool_results_after",
    }
    assert keys <= {row["key"] for row in SETTINGS_SCHEMA}
    assert not (keys & CONFIG_ONLY_SETTING_KEYS)
    ratio_row = schema_for("context.compaction_ratio")
    assert (
        ratio_row["label"], ratio_row["group"], ratio_row["type"],
        ratio_row["minimum"], ratio_row["maximum"], ratio_row["hot_action"],
    ) == ("自动压缩的窗口比例", "Agent", "number", 0.3, 0.95, "none")
    turns_row = schema_for("context.keep_recent_turns")
    assert (
        turns_row["label"], turns_row["group"], turns_row["type"],
        turns_row["minimum"], turns_row["maximum"], turns_row["hot_action"],
    ) == ("压缩时保留的最近轮次", "Agent", "integer", 1, 10, "none")
    clearing_row = schema_for("context.clear_tool_results_after")
    assert (
        clearing_row["label"], clearing_row["group"], clearing_row["type"],
        clearing_row["minimum"], clearing_row["maximum"],
        clearing_row["description"], clearing_row["hot_action"],
    ) == (
        "保留原文的最近工具往返数", "Agent", "integer", 1, 20,
        "更早的可重新获取的结果会成批换成占位", "none",
    )

    # 文档一致性：模板里的三个值与注释逐字照 spec §4.4。
    template_text = TEMPLATE_PATH.read_text(encoding="utf8")
    template = YAML(typ="safe").load(template_text)["context"]
    assert template["compaction_ratio"] == 0.75
    assert template["keep_recent_turns"] == 2
    assert template["clear_tool_results_after"] == 3
    assert "# 估算的上下文达到模型窗口的这个比例时自动压缩较早的消息" in template_text
    assert "# 压缩时原样保留最近几个用户轮次" in template_text
    assert "# 最近几个工具往返的结果保留原文，更早的可重新获取的结果成批换成占位" in template_text


def test_workspace_plan_settings_have_documented_defaults_and_bounds() -> None:
    """M75 工作记忆：8000 / complex / 4，范围 1000–100000、枚举、2–20（spec §4.4）。"""
    from maa_api.services.settings_schema import SETTINGS_SCHEMA, schema_for
    from maa_api.settings import CONFIG_ONLY_SETTING_KEYS, ContextSettings, PlanSettings

    default = resolve_settings(env={})
    assert default.context.spill_threshold_chars == 8000
    assert default.plan == PlanSettings()
    assert default.plan.approval_mode == "complex"
    assert default.plan.complex_min_steps == 4
    # 三项都不是 config-only：设置页可改，计划/外置每次现读。
    keys = {
        "context.spill_threshold_chars",
        "plan.approval_mode",
        "plan.complex_min_steps",
    }
    assert keys <= {row["key"] for row in SETTINGS_SCHEMA}
    assert not (keys & CONFIG_ONLY_SETTING_KEYS)

    configured = resolve_settings(
        {
            "context": {"spill_threshold_chars": 12000},
            "plan": {"approval_mode": "always", "complex_min_steps": 2},
        },
        env={},
    )
    assert configured.context.spill_threshold_chars == 12000
    assert configured.plan.approval_mode == "always"
    assert configured.plan.complex_min_steps == 2

    env_layer = resolve_settings(
        env={
            "MAA_CONTEXT_SPILL_THRESHOLD_CHARS": "100000",
            "MAA_PLAN_APPROVAL_MODE": "never",
            "MAA_PLAN_COMPLEX_MIN_STEPS": "20",
        }
    )
    assert env_layer.context.spill_threshold_chars == 100000
    assert env_layer.plan.approval_mode == "never"
    assert env_layer.plan.complex_min_steps == 20

    # 越界与非法枚举拒绝；边界值本身合法。
    for section, field, value in (
        ("context", "spill_threshold_chars", 999),
        ("context", "spill_threshold_chars", 100001),
        ("plan", "approval_mode", "sometimes"),
        ("plan", "complex_min_steps", 1),
        ("plan", "complex_min_steps", 21),
    ):
        with pytest.raises(ValidationError):
            resolve_settings({section: {field: value}}, env={})
    assert resolve_settings(
        {"context": {"spill_threshold_chars": 1000}}, env={}
    ).context.spill_threshold_chars == 1000
    assert resolve_settings(
        {"plan": {"complex_min_steps": 20}}, env={}
    ).plan.complex_min_steps == 20
    assert ContextSettings().spill_threshold_chars == 8000

    # 设置 API：三项都登记、分组 Agent、无热更新动作，类型/范围与代码一致。
    spill_row = schema_for("context.spill_threshold_chars")
    assert (
        spill_row["group"], spill_row["type"], spill_row["minimum"],
        spill_row["maximum"], spill_row["hot_action"],
    ) == ("Agent", "integer", 1000, 100000, "none")
    mode_row = schema_for("plan.approval_mode")
    assert (
        mode_row["group"], mode_row["type"], mode_row["enum"], mode_row["hot_action"],
    ) == ("Agent", "string", ["always", "complex", "never"], "none")
    steps_row = schema_for("plan.complex_min_steps")
    assert (
        steps_row["group"], steps_row["type"], steps_row["minimum"],
        steps_row["maximum"], steps_row["hot_action"],
    ) == ("Agent", "integer", 2, 20, "none")

    # 文档一致性：模板里的三项值与注释逐字照 spec §4.4。
    template_text = TEMPLATE_PATH.read_text(encoding="utf8")
    template = YAML(typ="safe").load(template_text)
    assert template["context"]["spill_threshold_chars"] == 8000
    assert template["plan"] == {"approval_mode": "complex", "complex_min_steps": 4}
    assert "# 工具输出超过这个字符数就外置成产物，上下文里只留指针" in template_text
    assert "# always 都要批准、complex 只对复杂计划、never 都不批准" in template_text
    assert "# 步骤数达到这个值算复杂计划" in template_text


def test_rag_settings_have_documented_defaults_and_bounds() -> None:
    """M76/M77 检索层：默认值、范围、互斥、cron 与设置 API 元数据（spec §4.4）。"""
    from maa_api.services.settings_schema import SETTINGS_SCHEMA, schema_for
    from maa_api.settings import CONFIG_ONLY_SETTING_KEYS, RagSettings

    default = resolve_settings(env={})
    assert default.rag == RagSettings()
    assert default.rag.enabled is True
    assert default.rag.embedding_provider == "none"
    assert default.rag.top_k == 8
    assert default.rag.chunk_chars == 800
    assert default.rag.chunk_overlap == 100
    assert default.rag.maa_docs_enabled is True
    assert default.rag.prts_enabled is True
    assert default.rag.gamedata_enabled is True
    assert default.rag.copilot_site_enabled is False
    assert default.rag.refresh_cron == "0 4 * * *"

    configured = resolve_settings(
        {
            "rag": {
                "enabled": False,
                "embedding_provider": "local",
                "top_k": 30,
                "chunk_chars": 4000,
                "chunk_overlap": 1000,
            }
        },
        env={},
    )
    assert configured.rag.enabled is False
    assert configured.rag.embedding_provider == "local"
    assert configured.rag.top_k == 30
    assert configured.rag.chunk_chars == 4000
    assert configured.rag.chunk_overlap == 1000

    env_layer = resolve_settings(
        env={
            "MAA_RAG_ENABLED": "false",
            "MAA_RAG_EMBEDDING_PROVIDER": "remote",
            "MAA_RAG_TOP_K": "1",
            "MAA_RAG_CHUNK_CHARS": "200",
            "MAA_RAG_CHUNK_OVERLAP": "0",
        }
    )
    assert env_layer.rag.enabled is False
    assert env_layer.rag.embedding_provider == "remote"
    assert env_layer.rag.top_k == 1
    assert env_layer.rag.chunk_chars == 200
    assert env_layer.rag.chunk_overlap == 0

    # 越界与非法枚举拒绝；边界值本身合法。
    for section, field, value in (
        ("rag", "top_k", 0),
        ("rag", "top_k", 31),
        ("rag", "chunk_chars", 199),
        ("rag", "chunk_chars", 4001),
        ("rag", "chunk_overlap", -1),
        ("rag", "chunk_overlap", 1001),
        ("rag", "embedding_provider", "openai"),
    ):
        with pytest.raises(ValidationError):
            resolve_settings({section: {field: value}}, env={})
    assert resolve_settings(
        {"rag": {"chunk_chars": 200, "chunk_overlap": 199}}, env={}
    ).rag.chunk_overlap == 199
    assert RagSettings().enabled is True

    # 互斥：重叠不小于分块长度时拒绝（spec §4.4 的模型校验器）。
    with pytest.raises(ValidationError):
        RagSettings(chunk_chars=800, chunk_overlap=800)
    with pytest.raises(ValidationError):
        resolve_settings({"rag": {"chunk_chars": 200, "chunk_overlap": 200}}, env={})

    # 设置 API：五项都登记、分组 Agent、无热更新动作，enum/范围/说明逐字照 spec。
    keys = {
        "rag.enabled",
        "rag.embedding_provider",
        "rag.top_k",
        "rag.chunk_chars",
        "rag.chunk_overlap",
    }
    assert keys <= {row["key"] for row in SETTINGS_SCHEMA}
    assert not (keys & CONFIG_ONLY_SETTING_KEYS)
    enabled_row = schema_for("rag.enabled")
    assert (
        enabled_row["label"], enabled_row["group"], enabled_row["type"],
        enabled_row["hot_action"],
    ) == ("启用知识检索", "Agent", "boolean", "none")
    provider_row = schema_for("rag.embedding_provider")
    assert (
        provider_row["label"], provider_row["group"], provider_row["type"],
        provider_row["enum"], provider_row["description"], provider_row["hot_action"],
    ) == (
        "向量服务", "Agent", "string", ["none", "remote", "local"],
        "none 只用全文检索；remote 用 LLM 设置里 embedding 角色的档案；local 用本地 ONNX 档案",
        "none",
    )
    for key, label, minimum, maximum in (
        ("rag.top_k", "检索返回条数", 1, 30),
        ("rag.chunk_chars", "分块长度（字）", 200, 4000),
        ("rag.chunk_overlap", "分块重叠（字）", 0, 1000),
    ):
        row = schema_for(key)
        assert (
            row["label"], row["group"], row["type"],
            row["minimum"], row["maximum"], row["hot_action"],
        ) == (label, "Agent", "integer", minimum, maximum, "none")

    # M77 外部语料：四个开关无热更新动作，刷新 cron 要重启服务；标签/说明逐字照 spec。
    corpus_keys = {
        "rag.maa_docs_enabled",
        "rag.prts_enabled",
        "rag.gamedata_enabled",
        "rag.copilot_site_enabled",
        "rag.refresh_cron",
    }
    assert corpus_keys <= {row["key"] for row in SETTINGS_SCHEMA}
    assert not (corpus_keys & CONFIG_ONLY_SETTING_KEYS)
    for key, label in (
        ("rag.maa_docs_enabled", "检索 MAA 文档"),
        ("rag.prts_enabled", "检索 PRTS 页面"),
        ("rag.gamedata_enabled", "检索拆包实体卡"),
        ("rag.copilot_site_enabled", "检索作业站作业"),
    ):
        row = schema_for(key)
        assert (
            row["label"], row["group"], row["type"], row["hot_action"],
        ) == (label, "Agent", "boolean", "none")
    cron_row = schema_for("rag.refresh_cron")
    assert (
        cron_row["label"], cron_row["group"], cron_row["type"],
        cron_row["hot_action"], cron_row["description"],
    ) == (
        "语料刷新时间（cron）", "Agent", "string", "restart_required",
        "五段 cron，按本机时区；改完重启服务生效",
    )

    # 文档一致性：模板里的值与注释逐字照 spec §4.4（M77 追加五个外部语料键）。
    template_text = TEMPLATE_PATH.read_text(encoding="utf8")
    template = YAML(typ="safe").load(template_text)
    assert template["rag"] == {
        "enabled": True,
        "embedding_provider": "none",
        "top_k": 8,
        "chunk_chars": 800,
        "chunk_overlap": 100,
        "maa_docs_enabled": True,
        "prts_enabled": True,
        "gamedata_enabled": True,
        "copilot_site_enabled": False,
        "refresh_cron": "0 4 * * *",
    }
    assert "# 知识检索总开关" in template_text
    assert (
        "# 向量服务：none 只用全文检索；remote 用 llm.role_embedding 指向的档案；"
        "local 用本地 ONNX 档案"
    ) in template_text
    assert "# 每次检索返回的条数（1–30）" in template_text
    assert "# 分块长度与相邻分块的重叠字数；改了以后下次同步时重建分块" in template_text
    assert "# 外部语料开关：关闭后不抓取、不参与检索（出站请求另受 sources.* 管）" in template_text
    assert "# 语料刷新时间，五段 cron，本机时区；改完重启服务生效" in template_text


def test_skills_settings_have_documented_default_and_metadata() -> None:
    """M78 技能总开关：默认值、分层与设置 API 元数据（spec §4.4）。"""
    from maa_api.services.settings_schema import SETTINGS_SCHEMA, schema_for
    from maa_api.settings import CONFIG_ONLY_SETTING_KEYS, SkillsSettings

    default = resolve_settings(env={})
    assert default.skills == SkillsSettings()
    assert default.skills.enabled is True

    configured = resolve_settings({"skills": {"enabled": False}}, env={})
    assert configured.skills.enabled is False

    env_layer = resolve_settings(env={"MAA_SKILLS_ENABLED": "false"})
    assert env_layer.skills.enabled is False

    # 设置 API：登记、分组 Agent、布尔、无热更新动作；不是 config-only。
    assert {"skills.enabled"} <= {row["key"] for row in SETTINGS_SCHEMA}
    assert "skills.enabled" not in CONFIG_ONLY_SETTING_KEYS
    row = schema_for("skills.enabled")
    assert (
        row["label"], row["group"], row["type"], row["hot_action"],
    ) == ("启用技能", "Agent", "boolean", "none")

    # 文档一致性：模板里的值与注释逐字照 spec §4.4。
    template_text = TEMPLATE_PATH.read_text(encoding="utf8")
    template = YAML(typ="safe").load(template_text)
    assert template["skills"] == {"enabled": True}
    assert "# 关闭后 Agent 看不到技能索引，也不能加载技能" in template_text


def test_memory_settings_have_documented_defaults_and_metadata() -> None:
    """M79 长期记忆四项：默认值、范围、环境层与设置 API 元数据（spec §4.4）。"""
    from pydantic import ValidationError

    from maa_api.services.settings_schema import SETTINGS_SCHEMA, schema_for
    from maa_api.settings import CONFIG_ONLY_SETTING_KEYS, MemorySettings

    default = resolve_settings(env={})
    assert default.memory == MemorySettings()
    assert default.memory.enabled is True
    assert default.memory.auto_extract is True
    assert default.memory.require_review is True
    assert default.memory.max_injection_tokens == 1500

    configured = resolve_settings(
        {
            "memory": {
                "enabled": False,
                "auto_extract": False,
                "require_review": False,
                "max_injection_tokens": 0,
            }
        },
        env={},
    )
    assert configured.memory.enabled is False
    assert configured.memory.auto_extract is False
    assert configured.memory.require_review is False
    assert configured.memory.max_injection_tokens == 0

    env_layer = resolve_settings(
        env={
            "MAA_MEMORY_ENABLED": "false",
            "MAA_MEMORY_AUTO_EXTRACT": "false",
            "MAA_MEMORY_REQUIRE_REVIEW": "false",
            "MAA_MEMORY_MAX_INJECTION_TOKENS": "8000",
        }
    )
    assert env_layer.memory.enabled is False
    assert env_layer.memory.auto_extract is False
    assert env_layer.memory.require_review is False
    assert env_layer.memory.max_injection_tokens == 8000

    # 0–8000 之外直接拒（注入预算）。
    for bad in (-1, 8001):
        with pytest.raises(ValidationError):
            resolve_settings({"memory": {"max_injection_tokens": bad}}, env={})
    with pytest.raises(ValidationError):
        MemorySettings(max_injection_tokens=8001)

    # 设置 API：四个键都登记、分组 Agent、不是 config-only。
    keys = {row["key"] for row in SETTINGS_SCHEMA}
    memory_keys = {
        "memory.enabled",
        "memory.auto_extract",
        "memory.require_review",
        "memory.max_injection_tokens",
    }
    assert memory_keys <= keys
    assert not (memory_keys & CONFIG_ONLY_SETTING_KEYS)
    assert (
        schema_for("memory.enabled")["label"],
        schema_for("memory.enabled")["group"],
        schema_for("memory.enabled")["type"],
        schema_for("memory.enabled")["hot_action"],
    ) == ("启用长期记忆", "Agent", "boolean", "none")
    budget = schema_for("memory.max_injection_tokens")
    assert (
        budget["label"], budget["group"], budget["type"],
        budget["minimum"], budget["maximum"], budget["hot_action"],
    ) == ("记忆注入上限（token）", "Agent", "integer", 0, 8000, "none")

    # 文档一致性：模板里的值与注释逐字照 spec §4.4。
    template_text = TEMPLATE_PATH.read_text(encoding="utf8")
    template = YAML(typ="safe").load(template_text)
    assert template["memory"] == {
        "enabled": True,
        "auto_extract": True,
        "require_review": True,
        "max_injection_tokens": 1500,
    }
    for comment in (
        "# 关闭后不抽取、不注入，remember 返回工具错误 memory_disabled",
        "# 关闭后只保留 remember 入口",
        "# 关闭后提议直接生效，审核人记 auto，仍发 memory_proposed",
        "# 注入预算，0–8000，用 M67 的 token 估算",
    ):
        assert comment in template_text


def test_rag_refresh_cron_rejects_garbage() -> None:
    """M77：refresh_cron 交给 APScheduler 解析，解析不了的串在加载时就拒绝（spec §4.4）。"""
    from maa_api.settings import RagSettings

    assert (
        resolve_settings({"rag": {"refresh_cron": "30 4 * * *"}}, env={})
        .rag.refresh_cron
        == "30 4 * * *"
    )
    for bad in ("every day", "0 4 * *", "61 4 * * *", ""):
        with pytest.raises(ValidationError):
            resolve_settings({"rag": {"refresh_cron": bad}}, env={})
    with pytest.raises(ValidationError):
        RagSettings(refresh_cron="every day")
    with pytest.raises(ValidationError):
        resolve_settings(env={"MAA_RAG_REFRESH_CRON": "every day"})


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


def test_run_heartbeat_settings_have_documented_defaults_and_bounds() -> None:
    """M66 运行心跳：默认 15 / 90，yaml 可覆盖，越界拒绝，两个键都不进设置 API。"""
    from maa_api.services.settings_schema import SETTINGS_SCHEMA

    default = resolve_settings(env={})
    assert default.agent.run_heartbeat_seconds == 15
    assert default.agent.run_stale_seconds == 90

    configured = resolve_settings(
        {"agent": {"run_heartbeat_seconds": 30, "run_stale_seconds": 300}},
        env={},
    )
    assert configured.agent.run_heartbeat_seconds == 30
    assert configured.agent.run_stale_seconds == 300

    for bad in (4, 121):
        with pytest.raises(ValidationError):
            resolve_settings({"agent": {"run_heartbeat_seconds": bad}}, env={})
    for bad in (29, 601):
        with pytest.raises(ValidationError):
            resolve_settings({"agent": {"run_stale_seconds": bad}}, env={})

    schema_keys = {item["key"] for item in SETTINGS_SCHEMA}
    assert "agent.run_heartbeat_seconds" not in schema_keys
    assert "agent.run_stale_seconds" not in schema_keys


def test_model_layer_settings_have_documented_defaults_and_bounds() -> None:
    """M67 模型层：13 个键都进设置 API，默认值、范围与模板照 spec §4.4。"""
    from maa_api.services.settings_schema import SETTINGS_SCHEMA, schema_for
    from maa_api.settings import (
        CONFIG_ONLY_SETTING_KEYS,
        BudgetSettings,
        ContextSettings,
    )

    roles = (
        "orchestrator",
        "worker",
        "subagent",
        "summarizer",
        "title",
        "trigger_eval",
        "memory",
        "vision",
        "embedding",
    )
    new_keys = {f"llm.role_{role}" for role in roles} | {
        "budget.daily_cost_limit",
        "budget.per_run_cost_limit",
        "budget.warn_ratio",
        "context.fallback_context_tokens",
    }

    default = resolve_settings(env={})
    assert [getattr(default.llm, f"role_{role}") for role in roles] == [""] * len(roles)
    assert default.budget == BudgetSettings()
    assert default.budget.daily_cost_limit == 0.0
    assert default.budget.per_run_cost_limit == 0.0
    assert default.budget.warn_ratio == 0.8
    assert default.context == ContextSettings()
    assert default.context.fallback_context_tokens == 128000

    configured = resolve_settings(
        {
            "llm": {"role_orchestrator": "planner"},
            "budget": {
                "daily_cost_limit": 12.5,
                "per_run_cost_limit": 2,
                "warn_ratio": 0.5,
            },
            "context": {"fallback_context_tokens": 64000},
        },
        env={},
    )
    assert configured.llm.role_orchestrator == "planner"
    assert configured.budget.daily_cost_limit == 12.5
    assert configured.budget.per_run_cost_limit == 2.0
    assert configured.budget.warn_ratio == 0.5
    assert configured.context.fallback_context_tokens == 64000

    for bad in (-1, 100001):
        with pytest.raises(ValidationError):
            resolve_settings({"budget": {"daily_cost_limit": bad}}, env={})
    for bad in (-1, 10001):
        with pytest.raises(ValidationError):
            resolve_settings({"budget": {"per_run_cost_limit": bad}}, env={})
    for bad in (0.09, 1.01):
        with pytest.raises(ValidationError):
            resolve_settings({"budget": {"warn_ratio": bad}}, env={})
    for bad in (7999, 2000001):
        with pytest.raises(ValidationError):
            resolve_settings({"context": {"fallback_context_tokens": bad}}, env={})

    schema = {item["key"]: item for item in SETTINGS_SCHEMA}
    assert not (new_keys & CONFIG_ONLY_SETTING_KEYS)
    assert new_keys <= set(schema)
    labels = {
        "orchestrator": "总控使用的模型档案",
        "worker": "Worker 使用的模型档案",
        "subagent": "子代理使用的模型档案",
        "summarizer": "摘要使用的模型档案",
        "title": "标题使用的模型档案",
        "trigger_eval": "事件研判使用的模型档案",
        "memory": "记忆整理使用的模型档案",
        "vision": "视觉使用的模型档案",
        "embedding": "向量使用的模型档案",
    }
    for role, label in labels.items():
        row = schema[f"llm.role_{role}"]
        assert row["label"] == label
        assert (row["group"], row["type"], row["hot_action"]) == ("LLM", "string", "none")
        description = "空表示不使用远端向量" if role == "embedding" else "空表示使用默认档案"
        assert row["description"] == description

    daily = schema["budget.daily_cost_limit"]
    assert (
        daily["group"], daily["type"], daily["minimum"], daily["maximum"],
        daily["description"], daily["hot_action"],
    ) == ("LLM", "number", 0, 100000, "0 为不限", "none")
    per_run = schema["budget.per_run_cost_limit"]
    assert (
        per_run["group"], per_run["type"], per_run["minimum"], per_run["maximum"],
        per_run["description"], per_run["hot_action"],
    ) == ("LLM", "number", 0, 10000, "0 为不限", "none")
    ratio = schema["budget.warn_ratio"]
    assert (
        ratio["group"], ratio["type"], ratio["minimum"], ratio["maximum"],
        ratio["hot_action"],
    ) == ("LLM", "number", 0.1, 1, "none")
    context_row = schema_for("context.fallback_context_tokens")
    assert (
        context_row["group"], context_row["type"], context_row["minimum"],
        context_row["maximum"], context_row["description"], context_row["hot_action"],
    ) == ("Agent", "integer", 8000, 2000000, "模型档案没写窗口时使用", "none")

    template_text = TEMPLATE_PATH.read_text(encoding="utf8")
    template = YAML(typ="safe").load(template_text)
    assert [template["llm"][f"role_{role}"] for role in roles] == [None] * len(roles)
    assert template["budget"] == {
        "daily_cost_limit": 0,
        "per_run_cost_limit": 0,
        "warn_ratio": 0.8,
    }
    assert template["context"] == {
        "fallback_context_tokens": 128000,
        "compaction_ratio": 0.75,
        "keep_recent_turns": 2,
        "clear_tool_results_after": 3,
        "spill_threshold_chars": 8000,
    }
    assert "# 各角色使用的模型档案名（设置页「模型档案」里建），空表示使用上面这组默认设置" in template_text
    assert "# 空表示不使用远端向量" in template_text


def test_trace_settings_have_documented_defaults_and_bounds() -> None:
    """M68 运行追踪：四个键（两条 config-only）、默认值、范围与模板照 spec §4.4。"""
    from maa_api.services.settings_schema import SETTINGS_SCHEMA, schema_for
    from maa_api.settings import CONFIG_ONLY_SETTING_KEYS, TraceSettings

    default = resolve_settings(env={})
    assert default.trace == TraceSettings()
    assert default.trace.enabled is True
    assert default.trace.retention_days == 14
    assert default.trace.otlp_endpoint == ""
    assert default.trace.otlp_headers == ""

    configured = resolve_settings(
        {
            "trace": {
                "enabled": False,
                "retention_days": 30,
                "otlp_endpoint": "http://127.0.0.1:6006/v1/traces",
                "otlp_headers": "Authorization=Bearer%20token,x-scope=dev",
            }
        },
        env={},
    )
    assert configured.trace.enabled is False
    assert configured.trace.retention_days == 30
    assert configured.trace.otlp_endpoint == "http://127.0.0.1:6006/v1/traces"
    assert configured.trace.otlp_headers == "Authorization=Bearer%20token,x-scope=dev"

    for bad in (0, 91):
        with pytest.raises(ValidationError):
            resolve_settings({"trace": {"retention_days": bad}}, env={})

    schema = {item["key"]: item for item in SETTINGS_SCHEMA}
    assert {"trace.enabled", "trace.retention_days"} <= set(schema)
    config_only = {"trace.otlp_endpoint", "trace.otlp_headers"}
    assert not (config_only & set(schema))
    assert config_only <= CONFIG_ONLY_SETTING_KEYS

    enabled = schema_for("trace.enabled")
    assert (
        enabled["label"], enabled["group"], enabled["type"], enabled["hot_action"],
    ) == ("记录运行追踪", "Agent", "boolean", "none")
    retention = schema_for("trace.retention_days")
    assert (
        retention["label"], retention["group"], retention["type"],
        retention["minimum"], retention["maximum"], retention["hot_action"],
    ) == ("运行追踪保留天数", "Agent", "integer", 1, 90, "none")

    template_text = TEMPLATE_PATH.read_text(encoding="utf8")
    template = YAML(typ="safe").load(template_text)
    assert template["trace"] == {
        "enabled": True,
        "retention_days": 14,
        "otlp_endpoint": None,
        "otlp_headers": None,
    }
    assert "# 超过这么多天的追踪记录由每天 04:00 的清理任务删除" in template_text


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
