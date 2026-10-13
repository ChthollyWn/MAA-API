"""M44：把仓库识别从「养成材料」页签改到「全部」页签（内核资源覆盖层）。

**为什么需要覆盖层**：内核内置链是 ``Depot → DepotMaterialTab → DepotMaterialTabClicked``，
只点「养成材料」页签。仓库页签栏实际是 ``全部 / 消耗物品 / 基础物品 / 养成材料 / 特别纪念``，
而内核自带的 ``DepotAllTab``（roi ``[450, 0, 300, 138]``，1280×720 坐标）正好覆盖「全部」——
把 ``Depot.next`` 指过去，「全部」点完不再切回养成材料，内核那一趟就会在「全部」页上扫。

**覆盖是逐 key 合并，不是整条替换**（M45 W0 取证，内核 v6.18.0 ``Config/TaskData.cpp:78-105``）：
同名任务里我们给的 key 覆盖内核的、**没给的 key 保留内核值**（``m_json_all_tasks_info`` 从不
清空，见该文件 :315-323；加载顺序上 custom 层最后加载，后来的胜出，见 ``main.py`` 的
``incremental_paths``）。所以这里每个节点**只写要改的 key**，``template`` / ``roi`` /
``action`` 全部继承内核值。想整条替换必须显式声明 ``baseTask``，那样反而会把内核的
``sub`` / ``exceededNext`` 丢掉。M44 当初每个节点都写了全量 key，行为没问题，但注释写的
「内核按任务名替换整个定义」是错的。

**为什么是启动时生成**：``resource/`` 不进版本库（AGENTS.md），而内核只在启动时加载一次
资源。这里照 M38 ``ensure_task_delay`` 的同一套做法，把任务覆盖写进
``resource/maa-layers/custom/resource/``；写不进去只记日志，内核退回自带行为（少读几个
页签），不影响启动。

**M45 W0 的两条修正**（台账 ``docs/superpowers/ledgers/2026-10-13-m45-depot-selfscan.md``）：

1. 原来这里还有一个新增节点 ``DepotAllTabClicked``（等「全部」页签进入选中态）与配套模板
   ``maa_api/assets/depot/DepotAllTabClicked.png``，是**死代码**：模板按 2560×1440 截图裁的
   600×276，而 roi 是 1280 坐标的 300×138，内核每次都报 ``templ size is too large``
   （``asst.log:221804``）并直接拒绝，全日志 0 次命中。M45 删掉它，「全部」点完就地结束这一趟。
2. 「点全部页签 → 条目从 26 涨到 77」**证据链不成立**：``asst.log:150254`` 那一次（15:44）
   根本没有覆盖层就已经是 77，同进程 15:52 只有 26（``asst.log:150950``），差别是内核
   ``swipe_and_analyze()`` 提前 ``break``。覆盖机制本身有效（``asst.log:223855/223858``
   逐 key 合并实证），真机验收挪到 M45 W3 重做。
"""

from __future__ import annotations

import json
import logging

from pathlib import Path
from typing import Any

logger = logging.getLogger(__name__)

__all__ = [
    "DEPOT_OVERLAY_TASKS",
    "DEPOT_OVERLAY_TASKS_FILE",
    "ensure_depot_all_tabs",
]

#: 覆盖层任务文件名（放在 custom 层的 ``resource/tasks/`` 下）。
DEPOT_OVERLAY_TASKS_FILE = "MaaApi@DepotAllTabs.json"

#: 两个节点，都**只写要改的 key**（逐 key 合并，见模块文档）。
DEPOT_OVERLAY_TASKS: dict[str, Any] = {
    "Depot": {
        "next": ["DepotAllTab"],
        "doc": "M44 覆盖：内置 next 是 DepotMaterialTab（只扫养成材料），改成 DepotAllTab（全部）",
    },
    "DepotAllTab": {
        # 空 next ＝点完「全部」就地收尾，页签停在「全部」上（内核自带下一个节点
        # DepotMaterialTabClicked 的 roi 在养成材料页签上，永远等不到）。
        "next": [],
        "doc": "M44 覆盖：内置 next 是 DepotMaterialTab（会切回养成材料），改成点完就停",
    },
}


def ensure_depot_all_tabs(*, layers_root: Path) -> Path | None:
    """把「点全部页签」的任务覆盖写进 custom 层。

    :param layers_root: 覆盖层根目录（``resource/maa-layers``）。
    :returns: 写出的任务文件路径；写不进去时返回 ``None``——调用方照常启动，内核沿用
        自带行为（只扫养成材料页签），只是少读几个页签。
    """
    custom = layers_root / "custom" / "resource"
    tasks_path = custom / "tasks" / DEPOT_OVERLAY_TASKS_FILE
    try:
        tasks_path.parent.mkdir(parents=True, exist_ok=True)
        tasks_path.write_text(
            json.dumps(DEPOT_OVERLAY_TASKS, ensure_ascii=False, indent=2) + "\n",
            encoding="utf8",
        )
    except OSError as exc:  # pragma: no cover - 磁盘异常只影响页签覆盖
        logger.warning("生成仓库页签覆盖层失败：%s", exc)
        return None
    return tasks_path
