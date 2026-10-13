"""M45 W4：清掉 M44 留在 custom 层的仓库页签覆盖（``MaaApi@DepotAllTabs.json``）。

**这里原来做什么**：M44 用一份资源覆盖层把内核 ``Depot.next`` 从 ``DepotMaterialTab``
改成 ``DepotAllTab``（「全部」页签），并在启动期写进 ``resource/maa-layers/custom/``
（``resource/`` 不进版本库，内核又只在启动时加载一次资源，只能运行时生成）。

**为什么删掉**（M45 W3b 真机 A/B，台账 §W3b-D）：

1. 覆盖本身确实逐 key 生效（有覆盖层时 ``asst.log:236732`` 的 ``to_be_recognized`` 是
   ``["DepotAllTab"]``，把文件写成写不进去后 ``asst.log:243716`` 变回内核的
   ``["DepotMaterialTab"]``），但**它在「从主界面进仓库」这条正常路径上是负面的**：
   ``DepotAllTab.png`` 只是**未选中态**模板（自算 masked NCC：未选中 0.8369、已选中
   0.4710），而正常路径进仓库时「全部」页签已经选中 ⇒ ``Depot`` 连试 20 次匹配不上 →
   ``SubTaskError`` → ``analyze_basic_items`` 返回 ``{"data":"{}","done":true}`` +
   ``TaskChainError``，整轮仓库识别**空结果**；采集层空结果护栏拦住不写库，
   连 M45 的自识别（它在空结果判断之后才跑）也一并没跑。14:13 与 14:17 两轮复现。
2. 覆盖层最初的理由不成立：M44 记的「点『全部』让条目 26 → 77」是内核
   ``swipe_and_analyze()`` 提前 ``break``（W0 已证），不是页签覆盖的功劳。
3. M45 的自识别链不依赖它：``maa_api/core/depot_selfscan_overlay.py`` 的
   ``MaaApi@DepotSelfScan@*`` 用 ``baseTask: DepotAllTab`` + 显式 ``template``
   自己点「全部」，只在需要时用、且是可选的幂等一步。

**所以本模块现在只做一件事：删掉磁盘上已经写出的那份覆盖文件。** 只删代码不行 ——
文件是启动期生成的、不进版本库，老检出上会残留，内核照样加载它，行为不一致；
每次启动都清一次，内核就稳定回到自带的 ``Depot.next = ["DepotMaterialTab"]``。
删不掉（权限、只读挂载）只记日志：内核退回自带行为，不影响启动。
"""

from __future__ import annotations

import logging

from pathlib import Path

logger = logging.getLogger(__name__)

__all__ = [
    "DEPOT_OVERLAY_TASKS_FILE",
    "remove_depot_all_tabs_override",
]

#: M44 覆盖层的任务文件名（在 custom 层的 ``resource/tasks/`` 下）。现在只用来删除。
DEPOT_OVERLAY_TASKS_FILE = "MaaApi@DepotAllTabs.json"


def remove_depot_all_tabs_override(*, layers_root: Path) -> Path | None:
    """删掉 M44 写下的仓库页签覆盖文件（每次启动都清一次）。

    :param layers_root: 覆盖层根目录（``resource/maa-layers``）。
    :returns: 删掉的路径；文件本来就不存在（新检出 / 已经清过）或删不掉时返回 ``None``
        —— 调用方照常启动，内核沿用自带行为（``Depot.next = ["DepotMaterialTab"]``）。
    """
    tasks_path = layers_root / "custom" / "resource" / "tasks" / DEPOT_OVERLAY_TASKS_FILE
    try:
        tasks_path.unlink()
    except FileNotFoundError:
        return None
    except OSError as exc:  # pragma: no cover - 磁盘异常只影响这次清理
        logger.warning(
            "清理 M44 仓库页签覆盖层失败，内核仍会加载它（仓库识别可能整轮空结果）：%s", exc
        )
        return None
    logger.info("已删除 M44 的仓库页签覆盖层，仓库识别回到内核自带的养成材料页签：%s", tasks_path)
    return tasks_path
