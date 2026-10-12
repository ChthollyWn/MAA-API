"""M44：把仓库识别从「养成材料」页签改到「全部」页签（内核资源覆盖层）。

**为什么需要覆盖层**：内核内置链是 ``Depot → DepotMaterialTab → DepotMaterialTabClicked``，
只点「养成材料」页签。真机实测（2026-10-12，模拟器 2560×1440）同一次 ``Depot`` 识别：
养成材料页签读到 26 件，改成「全部」页签读到 **77 件**。仓库页签栏实际是
``全部 / 消耗物品 / 基础物品 / 养成材料 / 特别纪念``，而内核的 ``DepotAllTab``
（roi ``[450, 0, 300, 138]``，1280×720 坐标）正好覆盖「全部」。

**为什么是启动时生成**：``resource/`` 不进版本库（AGENTS.md），而内核只在启动时加载一次
资源。这里照 M38 ``ensure_task_delay`` 的同一套做法，把任务覆盖与模板写进
``resource/maa-layers/custom/resource/``；写不进去只记日志，内核退回自带行为（少读几个
页签），不影响启动。

**模板从哪来**：内核 ``template/Depot/`` 下只有 ``DepotAllTab.png``（未选中）与
``DepotMaterialTabClicked.png``（养成材料选中），缺「全部选中」这张。我们自带一张
（``maa_api/assets/depot/DepotAllTabClicked.png``，取自真机截图按 roi 裁切）。
"""

from __future__ import annotations

import json
import logging
import shutil
from pathlib import Path
from typing import Any

logger = logging.getLogger(__name__)

__all__ = [
    "DEPOT_OVERLAY_TASKS",
    "DEPOT_OVERLAY_TASKS_FILE",
    "DEPOT_TAB_TEMPLATE_NAME",
    "ensure_depot_all_tabs",
]

#: 覆盖层任务文件名（放在 custom 层的 ``resource/tasks/`` 下）。
DEPOT_OVERLAY_TASKS_FILE = "MaaApi@DepotAllTabs.json"

#: 「全部」页签选中态模板名（内核按任务名推模板名：``DepotAllTabClicked`` → ``Depot/DepotAllTabClicked.png``）。
DEPOT_TAB_TEMPLATE_NAME = "DepotAllTabClicked.png"

#: 模板来源（仓库内自带，见模块文档）。
_TEMPLATE_SOURCE = (
    Path(__file__).resolve().parents[1] / "assets" / "depot" / DEPOT_TAB_TEMPLATE_NAME
)

#: 三个节点：``Depot`` 与 ``DepotAllTab`` 是**整条覆盖**（内核按任务名替换整个定义，
#: 只给 ``next`` 会把 ``template`` / ``roi`` / ``sub`` 丢掉），``DepotAllTabClicked`` 是新增。
DEPOT_OVERLAY_TASKS: dict[str, Any] = {
    "Depot": {
        "action": "DoNothing",
        "template": "SwitchTheme@ToggleSettingsMenu.png",
        "roi": [227, 327, 159, 152],
        "sub": ["Depot-Entry"],
        "next": ["DepotAllTab"],
        "doc": "M44 覆盖：内置链 next 是 DepotMaterialTab（只扫养成材料），改成 DepotAllTab（全部）",
    },
    "DepotAllTab": {
        "action": "ClickSelf",
        "roi": [450, 0, 300, 138],
        "next": ["DepotAllTabClicked"],
        "doc": "M44 覆盖：点「全部」页签；内置的 next 会切回养成材料",
    },
    "DepotAllTabClicked": {
        "roi": [450, 0, 300, 138],
        "doc": "M44 新增：等「全部」页签进入选中态（模板 DepotAllTabClicked.png）",
    },
}


def ensure_depot_all_tabs(*, layers_root: Path) -> Path | None:
    """把「点全部页签」的任务覆盖与选中态模板写进 custom 层。

    :param layers_root: 覆盖层根目录（``resource/maa-layers``）。
    :returns: 写出的任务文件路径；模板缺失或写不进去时返回 ``None``——调用方照常启动，
        内核沿用自带行为（只扫养成材料页签），只是少读几个页签。
    """
    custom = layers_root / "custom" / "resource"
    tasks_path = custom / "tasks" / DEPOT_OVERLAY_TASKS_FILE
    template_path = custom / "template" / "Depot" / DEPOT_TAB_TEMPLATE_NAME
    try:
        if not _TEMPLATE_SOURCE.is_file():
            logger.warning("仓库页签覆盖层模板缺失：%s", _TEMPLATE_SOURCE)
            return None
        tasks_path.parent.mkdir(parents=True, exist_ok=True)
        tasks_path.write_text(
            json.dumps(DEPOT_OVERLAY_TASKS, ensure_ascii=False, indent=2) + "\n",
            encoding="utf8",
        )
        template_path.parent.mkdir(parents=True, exist_ok=True)
        shutil.copyfile(_TEMPLATE_SOURCE, template_path)
    except OSError as exc:  # pragma: no cover - 磁盘异常只影响页签覆盖
        logger.warning("生成仓库页签覆盖层失败：%s", exc)
        return None
    return tasks_path
