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
        "channel.client_type": "MAA_CHANNEL_CLIENT_TYPE",
        "channel.server": "MAA_CHANNEL_SERVER",
        "llm.base_url": "MAA_LLM_BASE_URL",
        "llm.api_key": "MAA_LLM_API_KEY",
        "llm.model": "MAA_LLM_MODEL",
    }


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
