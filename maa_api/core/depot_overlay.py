"""启动期清理仓库识别在 custom 层留下的陈旧任务链文件。

**这里原来做什么**：M44 用一份资源覆盖层把内核 ``Depot.next`` 从 ``DepotMaterialTab``
改成 ``DepotAllTab``（「全部」页签），并在启动期写进 ``resource/maa-layers/custom/``
（``resource/`` 不进版本库，内核又只在启动时加载一次资源，只能运行时生成）；M45 W2 又
在这里写了一条自识别的任务链（``MaaApi@DepotSelfScan@*``）。

**为什么要清理**（M45 W3b 真机 A/B，台账 §W3b-D）：覆盖层确实逐 key 生效，但
``DepotAllTab.png`` 只是**未选中态**模板（自算 masked NCC：未选中 0.8369、已选中
0.4710），而正常路径进仓库时「全部」页签已经选中 ⇒ ``Depot`` 连试 20 次匹配不上 →
``SubTaskError`` → ``analyze_basic_items`` 返回 ``{"data":"{}","done":true}`` +
``TaskChainError``，整轮仓库识别**空结果**（14:13 与 14:17 两轮复现）。这份覆盖层已按
2026-10-13 的裁决整块删除（``docs/13`` §8.67 一）；自识别本身也在 2026-10-13 关停下线、
2026-10-14 连实现一起删除（``docs/13`` §8.68）。

**所以本模块只做一件事：删掉磁盘上已经写出的那几份任务文件。** 只删代码不行 ——
它们都是启动期生成的、不进版本库，老检出上会残留，内核照样加载：覆盖层会把整轮识别打挂，
自识别链则是没人再引用的死文件（还留着 W0/W1 实验的探针文件）。每次启动都清一次，
内核就稳定回到自带的 ``Depot.next = ["DepotMaterialTab"]``，custom 层里只剩还在用的链。
删不掉（权限、只读挂载）只记日志：内核退回自带行为，不影响启动。
"""

from __future__ import annotations

import logging

from pathlib import Path

logger = logging.getLogger(__name__)

__all__ = [
    "STALE_DEPOT_TASKS_FILES",
    "remove_stale_depot_tasks",
]

#: 启动期要清掉的陈旧任务文件名（在 custom 层的 ``resource/tasks/`` 下）。
#: 全部是历史版本在启动期写出的、现在已经没有任何代码引用的文件：
#:
#: - ``MaaApi@DepotAllTabs.json``：M44 的仓库页签覆盖层（M45 W4 删实现，见 docs/13 §8.67）。
#: - ``MaaApi@DepotSelfScan.json``：M45 的自识别任务链（2026-10-14 删实现，见 §8.68）。
#: - ``MaaApi@DepotScanProbe.json``：M45 W0/W1 的探针链（实验后没入版本库，留在本机）。
STALE_DEPOT_TASKS_FILES: tuple[str, ...] = (
    "MaaApi@DepotAllTabs.json",
    "MaaApi@DepotSelfScan.json",
    "MaaApi@DepotScanProbe.json",
)


def remove_stale_depot_tasks(*, layers_root: Path) -> list[Path]:
    """删掉上面那几份任务文件（每次启动都清一次，幂等）。

    :param layers_root: 覆盖层根目录（``resource/maa-layers``）。
    :returns: 真正删掉的路径（可能为空）—— 文件本来就不存在（新检出 / 已经清过）或
        删不掉时都不出现在里面，调用方照常启动，内核沿用自带行为
        （``Depot.next = ["DepotMaterialTab"]``）。
    """
    tasks_dir = layers_root / "custom" / "resource" / "tasks"
    if not tasks_dir.is_dir():
        # 这一层还没建起来（新检出、或者从没生成过任何链）：不顺手创建目录。
        return []
    removed: list[Path] = []
    for name in STALE_DEPOT_TASKS_FILES:
        path = tasks_dir / name
        try:
            path.unlink()
        except FileNotFoundError:
            continue
        except OSError as exc:  # pragma: no cover - 磁盘异常只影响这次清理
            logger.warning("清理陈旧的仓库任务链失败，内核仍会加载它：%s（%s）", path, exc)
            continue
        logger.info("已删除陈旧的仓库任务链：%s", path)
        removed.append(path)
    return removed
