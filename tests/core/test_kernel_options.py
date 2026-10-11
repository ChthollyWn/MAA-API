"""内核资源层配置生成（M38，docs/13 §8.53）。

MaaCore 只在内核启动、加载资源时读 ``config.json``，所以识别间隔这样的进程级选项不能
直接改内核目录里的文件（内核更新会整目录替换），而是由这里在开机时复制一份到
``resource/maa-layers/custom/resource/config.json`` 覆盖 ``options``。用例全部在
``tmp_path`` 里造内核目录，不碰真仓库资源、不启动内核。
"""

import json
import os
from pathlib import Path

from maa_api.core.kernel_options import (
    KERNEL_CONFIG_NAME,
    LAYER_NAME,
    ensure_task_delay,
    layer_config_path,
)


def _kernel(tmp_path: Path, payload: object, *, name: str = KERNEL_CONFIG_NAME) -> Path:
    """造一个内核目录：``<dir>/resource/config.json``。"""
    root = tmp_path / "kernel"
    resource = root / "resource"
    resource.mkdir(parents=True, exist_ok=True)
    if isinstance(payload, str):
        (resource / name).write_text(payload, encoding="utf-8")
    else:
        (resource / name).write_text(json.dumps(payload), encoding="utf-8")
    return root


def _read(path: Path) -> dict:
    return json.loads(path.read_text(encoding="utf-8"))


def test_the_layer_config_overrides_only_the_delay(tmp_path):
    """目标写在 custom 层里，内核配置的其余内容原样带过去（连接 profile 等）。"""
    kernel = _kernel(
        tmp_path,
        {
            "version": "v5.13.0",
            "options": {"taskDelay": 500, "debug": False},
            "connection": [{"configName": "General"}],
        },
    )
    layers = tmp_path / "maa-layers"

    written = ensure_task_delay(maa_path=kernel, layers_root=layers, task_delay=100)

    assert written == layers / LAYER_NAME / "resource" / KERNEL_CONFIG_NAME
    assert written == layer_config_path(layers)
    payload = _read(written)
    assert payload["options"]["taskDelay"] == 100
    assert payload["options"]["debug"] is False
    assert payload["version"] == "v5.13.0"
    assert payload["connection"] == [{"configName": "General"}]


def test_an_unchanged_layer_config_is_not_rewritten(tmp_path):
    """内容一致就不落盘：每次开机都重写会让 mtime 一直跳。"""
    kernel = _kernel(tmp_path, {"options": {"taskDelay": 500}})
    layers = tmp_path / "maa-layers"
    written = ensure_task_delay(maa_path=kernel, layers_root=layers, task_delay=100)
    first = os.stat(written).st_mtime_ns

    again = ensure_task_delay(maa_path=kernel, layers_root=layers, task_delay=100)

    assert again == written
    assert os.stat(written).st_mtime_ns == first


def test_a_changed_setting_rewrites_the_layer_config(tmp_path):
    """改了设置重启就按新值重新生成。"""
    kernel = _kernel(tmp_path, {"options": {"taskDelay": 500}})
    layers = tmp_path / "maa-layers"
    written = ensure_task_delay(maa_path=kernel, layers_root=layers, task_delay=100)

    ensure_task_delay(maa_path=kernel, layers_root=layers, task_delay=0)

    assert _read(written)["options"]["taskDelay"] == 0


def test_missing_options_are_added(tmp_path):
    """内核配置里暂时没有 options 段也算正常：补一段出来。"""
    kernel = _kernel(tmp_path, {"version": "v5.13.0"})
    layers = tmp_path / "maa-layers"

    written = ensure_task_delay(maa_path=kernel, layers_root=layers, task_delay=100)

    assert _read(written)["options"] == {"taskDelay": 100}


def test_a_missing_kernel_config_keeps_booting(tmp_path):
    """内核目录里没有 config.json：不报错、不落盘，内核用自己的默认值。"""
    kernel = tmp_path / "kernel"
    (kernel / "resource").mkdir(parents=True)
    layers = tmp_path / "maa-layers"

    assert ensure_task_delay(maa_path=kernel, layers_root=layers, task_delay=100) is None
    assert not layer_config_path(layers).exists()


def test_a_broken_kernel_config_keeps_booting(tmp_path):
    """坏 JSON 不能把启动带下去，也不覆盖上一次生成的层文件。"""
    kernel = _kernel(tmp_path, "{not json", name=KERNEL_CONFIG_NAME)
    layers = tmp_path / "maa-layers"

    assert ensure_task_delay(maa_path=kernel, layers_root=layers, task_delay=100) is None
    assert not layer_config_path(layers).exists()


def test_a_non_object_kernel_config_keeps_booting(tmp_path):
    """顶层不是对象时同样放过：层文件不生成。"""
    kernel = _kernel(tmp_path, [1, 2, 3])
    layers = tmp_path / "maa-layers"

    assert ensure_task_delay(maa_path=kernel, layers_root=layers, task_delay=100) is None
    assert not layer_config_path(layers).exists()


def test_the_layer_directory_is_created_on_demand(tmp_path):
    """层目录不存在时自己建出来（首次开机就是这样）。"""
    kernel = _kernel(tmp_path, {"options": {"taskDelay": 500}})
    layers = tmp_path / "maa-layers"

    written = ensure_task_delay(maa_path=kernel, layers_root=layers, task_delay=100)

    assert written.parent.is_dir()
