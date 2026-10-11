"""内核资源层的 ``options`` 覆盖（M38，docs/13 §8.53）。

MaaCore 从 ``<内核目录>/resource/config.json`` 读进程级选项，其中 ``taskDelay`` 是识别
间隔（内核自带 500 ms）。这个文件会被内核更新整目录替换，所以不去改它；改成开机时把它
复制进 ``resource/maa-layers/custom/resource/config.json``——这一层在内核加载基础资源之后
加载，实测会覆盖 ``options``（练度扫描读一屏 13.3 → 4.6 秒）。

生成的文件跟着设置走：改完设置重启服务即重新生成；内核更新换掉基础文件之后，下次开机也会
按新的基础文件重新生成，不会残留旧值。
"""

from __future__ import annotations

import json
import logging
from pathlib import Path
from typing import Any

__all__ = ["KERNEL_CONFIG_NAME", "LAYER_NAME", "ensure_task_delay", "layer_config_path"]

logger = logging.getLogger(__name__)

#: 内核资源里的配置文件名与增量层名。
KERNEL_CONFIG_NAME = "config.json"
LAYER_NAME = "custom"


def layer_config_path(layers_root: Path) -> Path:
    """Return the path of the generated kernel config inside the custom layer."""
    return layers_root / LAYER_NAME / "resource" / KERNEL_CONFIG_NAME


def ensure_task_delay(*, maa_path: Path, layers_root: Path, task_delay: int) -> Path | None:
    """Write the kernel config into the custom layer with ``options.taskDelay`` overridden.

    :param maa_path: kernel directory holding ``resource/config.json``。
    :param layers_root: overlay root (``resource/maa-layers``)；增量层由内核按序加载。
    :param task_delay: 识别间隔毫秒数，写进 ``options.taskDelay``。
    :returns: 生成的文件路径；内核配置缺失、读不动或写不进去时返回 ``None``——
        调用方照常启动，内核沿用自带的默认值。
    """
    source = maa_path / "resource" / KERNEL_CONFIG_NAME
    try:
        payload: Any = json.loads(source.read_text(encoding="utf-8"))
    except (OSError, ValueError) as exc:
        logger.warning("内核配置读不动（%s）：识别间隔沿用内核默认（%s）", source, exc)
        return None
    if not isinstance(payload, dict):
        logger.warning("内核配置不是 JSON 对象：识别间隔沿用内核默认（%s）", source)
        return None
    options = payload.get("options")
    if not isinstance(options, dict):
        options = {}
    options["taskDelay"] = int(task_delay)
    payload["options"] = options
    text = json.dumps(payload, ensure_ascii=False, indent=2) + "\n"
    target = layer_config_path(layers_root)
    try:
        if target.is_file() and target.read_text(encoding="utf-8") == text:
            # 内容一致就不再写：避免每次开机都动 mtime。
            return target
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(text, encoding="utf-8")
    except OSError as exc:
        logger.warning("内核资源层配置写不动（%s）：识别间隔沿用内核默认（%s）", target, exc)
        return None
    logger.info("内核识别间隔 %d ms 已写进资源层：%s", task_delay, target)
    return target
