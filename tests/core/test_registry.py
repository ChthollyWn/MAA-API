"""CoreRegistry 契约（M1-12，docs/02 §5.4 / docs/13 ADR-10）。

用例只使用 ``object()`` 极简替身：注册表是纯内存映射，不启动 MaaCore 子进程、
不依赖 FakeAsst，也不验证多实例进程编排（那不在本卡范围内）。
"""

import subprocess
import sys
from pathlib import Path

import pytest

from maa_api.core.registry import DEFAULT_CORE_ID, CoreRegistry
from maa_api.domain.errors import AppError, ErrorCode

REPO_ROOT = Path(__file__).resolve().parents[2]


def test_default_core_id_is_default():
    """首版所有内核调用传的 core_id 恒为 "default"（docs/02 §5.4）。"""
    assert DEFAULT_CORE_ID == "default"


def test_get_resolves_default_when_core_id_omitted():
    registry = CoreRegistry()
    client = object()
    registry.register(DEFAULT_CORE_ID, client)

    assert registry.get() is client
    assert registry.get(DEFAULT_CORE_ID) is client
    assert registry.get("default") is client
    assert registry.default is client
    assert registry.default_id == DEFAULT_CORE_ID
    assert registry.ids() == [DEFAULT_CORE_ID]


def test_get_explicit_core_id():
    """显式 core_id 取回对应实例；省略时仍取 default，二者不互相影响。"""
    registry = CoreRegistry()
    default_client, second_client = object(), object()
    registry.register(DEFAULT_CORE_ID, default_client)
    registry.register("emu-2", second_client)

    assert registry.get("emu-2") is second_client
    assert registry.get(None) is default_client
    assert registry.get() is default_client
    assert registry.ids() == [DEFAULT_CORE_ID, "emu-2"]


def test_custom_default_id_only_changes_omitted_lookup():
    """default_id 可注入：扩展口不把 "default" 写死成全局变量。"""
    registry = CoreRegistry(default_id="alpha")
    alpha_client, fallback_client = object(), object()
    registry.register("alpha", alpha_client)
    registry.register(DEFAULT_CORE_ID, fallback_client)

    assert registry.default_id == "alpha"
    assert registry.get() is alpha_client
    assert registry.default is alpha_client
    assert registry.get(DEFAULT_CORE_ID) is fallback_client


def test_get_unregistered_raises_app_error_with_core_id_details():
    registry = CoreRegistry()
    registry.register(DEFAULT_CORE_ID, object())

    with pytest.raises(AppError) as explicit:
        registry.get("emu-2")
    assert explicit.value.code is ErrorCode.CORE_NOT_READY
    assert explicit.value.details == {"core_id": "emu-2"}
    assert "emu-2" in explicit.value.message

    with pytest.raises(AppError) as omitted:
        CoreRegistry().get()
    assert omitted.value.code is ErrorCode.CORE_NOT_READY
    assert omitted.value.details == {"core_id": DEFAULT_CORE_ID}

    with pytest.raises(AppError) as default_property:
        CoreRegistry().default
    assert default_property.value.code is ErrorCode.CORE_NOT_READY


def test_duplicate_register_raises_and_keeps_first_client():
    registry = CoreRegistry()
    first_client, second_client = object(), object()
    registry.register("emu-1", first_client)

    with pytest.raises(AppError) as conflict:
        registry.register("emu-1", second_client)

    assert conflict.value.code is ErrorCode.CORE_NOT_READY
    assert conflict.value.details == {"core_id": "emu-1"}
    assert "emu-1" in conflict.value.message
    assert registry.get("emu-1") is first_client
    assert registry.ids() == ["emu-1"]


def test_unregister_is_idempotent():
    registry = CoreRegistry()
    registry.register(DEFAULT_CORE_ID, object())

    assert registry.unregister(DEFAULT_CORE_ID) is True
    assert registry.ids() == []
    assert registry.unregister(DEFAULT_CORE_ID) is False
    with pytest.raises(AppError):
        registry.get()


def test_multiple_clients_do_not_crosstalk():
    """多个（假）client 并存时互不串，注销一个不影响其他。"""
    registry = CoreRegistry()
    clients = {core_id: object() for core_id in ("default", "emu-2", "emu-3")}
    for core_id, client in clients.items():
        registry.register(core_id, client)

    assert registry.ids() == ["default", "emu-2", "emu-3"]
    for core_id, client in clients.items():
        assert registry.get(core_id) is client

    assert registry.unregister("emu-2") is True
    assert registry.get("default") is clients["default"]
    assert registry.get("emu-3") is clients["emu-3"]
    with pytest.raises(AppError) as removed:
        registry.get("emu-2")
    assert removed.value.details == {"core_id": "emu-2"}


def test_registry_import_does_not_pull_client_or_supervisor():
    """轻量 import 契约：运行期不得把 client/supervisor 带进 sys.modules。

    必须在干净解释器里验证 —— 同一个 pytest 会话中其他测试模块已 import 过
    ``maa_api.core.client``，直接查 ``sys.modules`` 只会得到假阳性。
    """
    code = (
        "import sys; import maa_api.core.registry; "
        "leaked = [m for m in ('maa_api.core.client', 'maa_api.core.supervisor') "
        "if m in sys.modules]; "
        "print(leaked); raise SystemExit(1 if leaked else 0)"
    )
    proc = subprocess.run(
        [sys.executable, "-c", code],
        capture_output=True,
        text=True,
        cwd=REPO_ROOT,
        check=False,
    )
    assert proc.returncode == 0, proc.stdout + proc.stderr
