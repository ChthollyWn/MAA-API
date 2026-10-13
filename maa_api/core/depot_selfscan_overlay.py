"""M45 W2：仓库自识别的任务链（``MaaApi@DepotSelfScan@*``，custom 层覆盖）。

**为什么要一条自己的链**：M45 要在内核那一趟之外再拿一帧自己识别（规格 §4.1）。
链只负责「把设备带到仓库「全部」页并停住 / 翻页」，识别在 Python 侧做
（:class:`~maa_api.services.account.depot_scan_service.DepotScanService`），所以链里
全是导航与只读节点，不点任何道具、不改任何游戏数据。

**为什么用新任务名而不是覆盖内核节点**（规格 §4.2）：内核那一趟（95 个材料 + 数量
OCR）仍是最好的材料来源，M45 要与它并存；覆盖会改掉内核链的行为（M44 就是这么把
``Depot.next`` 改掉的）。本模块一个内核节点都不动，只在需要时**调用**它们。

**为什么启动时生成**：``resource/`` 不进版本库（AGENTS.md），内核又只在启动时加载一次
资源；照 M44 ``depot_overlay`` 的做法在启动期写进 custom 层，写不进去只记日志 ——
自识别整条链不可用，但不影响启动与内核那一趟。

## 链的形状

.. code-block:: text

    @Start ─┬─ @AllTab（已经在仓库页：点「全部」，停）
            └─ @EnterDepot → DepotBegin（不在仓库页：复用内核导航进仓库）
    @SwipeNextPage / @SwipePrevPage：横向翻一页（x 1150 → 110 @1280×720），翻完回 @Done
    @Done：链尾出口（Stop）

**「全部」页是横向翻页**（W2 真机实测，2026-10-13）：竖向滑动怎么划都不动（试过
``y 1320 → 180``、``y 950 → 300``、慢拖、``input draganddrop`` 四种），横向滑动一划
一页，几何与内核自带的 ``DepotSlowlySwipeToTheRight`` 同款。逐屏接受格数 7–24 不等，
**横向相位逐屏会漂（实测 78–114 px，先验是 86.6）**，所以 Python 侧每屏都要重新拟合
网格，且搜索半径要能兜住半列（台账 W2-B 有全部数字）。

- ``@AllTab`` 的基座是内核 ``DepotAllTab``（``ClickSelf``、roi ``[450, 0, 300, 138]``、
  自带 500 ms ``postDelay`` 覆盖切页动画），只把走向改成自己的链尾：内核那个节点的
  ``next`` 会切回「养成材料」，M45 必须停在「全部」。
- **没有「已选中」确认节点**：M44 的 ``DepotAllTabClicked`` 就是死在这里 —— 模板
  600×276 大于 roi 300×138，内核直接报 ``templ size is too large``（``asst.log:221804``），
  全日志 0 命中；W1 据此删掉了那个节点与自带资产（提交 ``e6ffaf3``）。不加回来：
  「这一页对不对」由识别器自己回答（``min_accepted`` 与逐格分数就是自检，规格 §4.4），
  比一个模板可靠，也省掉一个二进制资产。
- ``@EnterDepot`` 只写一个 ``next``，**不覆盖** ``DepotBegin`` 自己：它按 key 合并，
  这是 W0 真机实验证明过的姿势（``asst.log:223855/223858``）。
- **需要模板的节点必须显式写 ``template``**（哪怕用了 ``baseTask``）：内核在加载这一层时
  就给每个节点找模板，没写就按任务名推 ``<任务名>.png``；推出来的文件不存在会让**整层
  加载失败**，内核直接起不来。真机实测（2026-10-13 12:36）：
  ``Templ load failed, file not exists: MaaApi@DepotSelfScan@AllTab.png`` →
  ``TaskData load failed, path: .../custom/resource/tasks`` → ``CORE_START_FAILED``。
- 数量 OCR 的 24 个 ``NumberOcrReplace`` 节点不在本波（W2 只做身份，规格 §3.1-3），
  等 W3 的数量分批策略落地。
"""

from __future__ import annotations

import json
import logging

from pathlib import Path
from typing import Any

logger = logging.getLogger(__name__)

__all__ = [
    "DEPOT_SELF_SCAN_PREFIX",
    "DEPOT_SELF_SCAN_START",
    "DEPOT_SELF_SCAN_TASKS",
    "DEPOT_SELF_SCAN_TASKS_FILE",
    "ensure_depot_self_scan",
]

#: 节点前缀：M45 的链一律用它，内核节点一个都不碰（规格 §4.2）。
DEPOT_SELF_SCAN_PREFIX = "MaaApi@DepotSelfScan@"

#: 任务文件名（放在 custom 层的 ``resource/tasks/`` 下）。
DEPOT_SELF_SCAN_TASKS_FILE = "MaaApi@DepotSelfScan.json"

#: 提交 ``Custom`` 时用的链头。
DEPOT_SELF_SCAN_START = DEPOT_SELF_SCAN_PREFIX + "Start"

_DONE = DEPOT_SELF_SCAN_PREFIX + "Done"
_ALL_TAB = DEPOT_SELF_SCAN_PREFIX + "AllTab"
_ENTER_DEPOT = DEPOT_SELF_SCAN_PREFIX + "EnterDepot"
_SWIPE_NEXT = DEPOT_SELF_SCAN_PREFIX + "SwipeNextPage"
_SWIPE_PREV = DEPOT_SELF_SCAN_PREFIX + "SwipePrevPage"

#: 翻页的起止（1280×720 坐标）。**仓库「全部」页是横向翻页，不是纵向滚动**：
#: 内核自己的 ``DepotSlowlySwipeToTheRight``（``tasks.json``）就是横滑
#: ``x 1150 → 110``、``y 340``、200 ms —— 一页的宽度正是 8 列 × 129.92 ≈ 1039 px，
#: 所以这一划刚好走一页。W2 真机实测：连划 4 次拿到 4 个互不重叠的页（见台账）。
#: 竖向滑动（``y 1200 → 100`` 之类）实测**完全不动**，别再走回头路。
_SWIPE_FROM = [1150, 340, 20, 20]
_SWIPE_TO = [110, 340, 20, 20]

DEPOT_SELF_SCAN_TASKS: dict[str, Any] = {
    _DONE: {
        "algorithm": "JustReturn",
        "action": "Stop",
        "doc": "M45 链尾：必定命中，就地结束这一趟（取帧、翻页与识别都由 Python 侧接着做）",
    },
    _SWIPE_NEXT: {
        "algorithm": "JustReturn",
        "action": "Swipe",
        "specificRect": _SWIPE_FROM,
        "rectMove": _SWIPE_TO,
        # [滑动时长 ms, 1, 37, 1]：与内核 DepotSlowlySwipeToTheRight 同参数（200 ms 慢拖）。
        "specialParams": [200, 1, 37, 1],
        # 滑动有惯性，落定后再让调用方取帧（规格 §5 风险表：取帧太早会拿到过渡态）。
        "postDelay": 1500,
        "maxTimes": 1,
        "next": [_DONE],
        "exceededNext": [_DONE],
        "doc": "M45 翻页：仓库「全部」页横向翻到下一页（一页 = 8 列 ≈ 1039 px），供 Python 侧逐屏识别",
    },
    _SWIPE_PREV: {
        "algorithm": "JustReturn",
        "action": "Swipe",
        "specificRect": _SWIPE_TO,
        "rectMove": _SWIPE_FROM,
        "specialParams": [200, 1, 37, 1],
        "postDelay": 1500,
        "maxTimes": 1,
        "next": [_DONE],
        "exceededNext": [_DONE],
        "doc": "M45 翻页：横向翻回上一页（重扫与验收复位用）",
    },
    _ALL_TAB: {
        # baseTask 是「整条继承再改我要的 key」：roi/action/postDelay 全用内核 DepotAllTab 的，
        # 只把 next 换掉（内核的 next 会切回「养成材料」）。
        "baseTask": "DepotAllTab",
        # **template 必须自己再写一遍**：内核在**加载这一层时**就给每个需要模板的节点找模板，
        # 没写 template 就按任务名推 `<任务名>.png`。推出来的文件不存在会让**整层加载失败**
        # （真机实测：`Templ load failed, file not exists: MaaApi@DepotSelfScan@AllTab.png`
        # → `TaskData load failed, path: .../custom/resource/tasks` → 内核起不来）。
        # M36 的 `MaaApi@OperatorScan@Back` 也是 baseTask + 显式 template 的写法。
        "template": "DepotAllTab.png",
        "maxTimes": 1,
        "next": [_DONE],
        "exceededNext": [_DONE],
        "doc": "M45 点「全部」页签：基座是内核 DepotAllTab（roi [450,0,300,138]），只改走向",
    },
    _ENTER_DEPOT: {
        "algorithm": "JustReturn",
        "action": "DoNothing",
        # 只写 next 一项：DepotBegin 自己那 6 个候选与 algorithm 全部按 key 继承
        # （W0 真机实验，asst.log:223855/223858）。它会把设备带进仓库页。
        "next": ["DepotBegin"],
        "doc": "M45 进仓库页：调用内核 DepotBegin 链做导航（不覆盖它，只调用）",
    },
    DEPOT_SELF_SCAN_START: {
        "algorithm": "JustReturn",
        "action": "DoNothing",
        # 先试「已经在仓库页」：@AllTab 命中就地停住；没命中（不在仓库页）才走导航。
        "next": [_ALL_TAB, _ENTER_DEPOT],
        "doc": "M45 链头：已在仓库页就点「全部」，否则复用内核导航进仓库",
    },
}


def ensure_depot_self_scan(*, layers_root: Path) -> Path | None:
    """把自识别任务链写进 custom 层。

    :param layers_root: 覆盖层根目录（``resource/maa-layers``）。
    :returns: 写出的任务文件路径；写不进去时返回 ``None`` —— 调用方照常启动，
        自识别链不可用（内核那一趟不受影响）。
    """
    custom = layers_root / "custom" / "resource"
    tasks_path = custom / "tasks" / DEPOT_SELF_SCAN_TASKS_FILE
    try:
        tasks_path.parent.mkdir(parents=True, exist_ok=True)
        tasks_path.write_text(
            json.dumps(DEPOT_SELF_SCAN_TASKS, ensure_ascii=False, indent=2) + "\n",
            encoding="utf8",
        )
    except OSError as exc:  # pragma: no cover - 磁盘异常只影响自识别链
        logger.warning("生成仓库自识别任务链失败：%s", exc)
        return None
    return tasks_path
